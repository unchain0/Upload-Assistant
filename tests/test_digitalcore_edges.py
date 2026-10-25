from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest
from torf import MetainfoError, Torrent

from src.domain_models.release import Meta
from src.integrations.trackers import digitalcore
from src.integrations.trackers.digitalcore import DigitalCore
from tests.test_digitalcore_rules import _make_meta, _tracker


def _response(
    payload: Any, *, status: int = 200, text: str | None = None
) -> httpx.Response:
    request = httpx.Request("GET", "https://digitalcore.club/api/v1/torrents")
    if text is not None:
        return httpx.Response(status, request=request, text=text)
    return httpx.Response(status, request=request, json=payload)


def _valid_torrent(tmp_path: Path) -> str:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    torrent_path = tmp_path / "registered.torrent"
    torrent = Torrent(path=payload, piece_size=32768)
    torrent.generate(threads=1)
    torrent.write(torrent_path)
    return str(torrent_path)


def test_digitalcore_search_payload_empty_and_matching_entry() -> None:
    assert DigitalCore._search_payload(_response([], text="[]")) == []
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 123,
            "name": "Release",
            "size": 42,
            "numfiles": 2,
        },
        6,
    )
    assert entry is not None
    assert entry["id"] == 123
    assert entry["link"] == "https://digitalcore.club/torrent/123/"


def test_digitalcore_rejects_divx_and_rar() -> None:
    tracker = _tracker()
    assert not asyncio.run(
        tracker.get_additional_checks(_make_meta(video_codec="DivX"))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_make_meta(filelist=["release.rar"]))
    )


def test_digitalcore_non_video_category_skips_video_file_rules() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_additional_checks(
            _make_meta(category="BOOK", filelist=["release.rar"])
        )
    )


def test_digitalcore_firstpic_uses_safe_hosted_artwork() -> None:
    meta = Meta(
        category="BOOK",
        hosted_artwork=[{"raw_url": "https://img.digitalcore.club/cover.jpg"}],
    )
    assert (
        asyncio.run(_tracker().get_firstpic(meta))
        == "https://img.digitalcore.club/cover.jpg"
    )


def test_digitalcore_firstpic_rehosts_unsafe_artwork() -> None:
    tracker = _tracker()
    tracker.rehost_images_manager.check_policy = AsyncMock(
        return_value=(
            [{"raw_url": "https://img.digitalcore.club/rehosted.jpg"}],
            False,
            True,
        )
    )
    meta = Meta(
        category="BOOK",
        hosted_artwork=[{"raw_url": "https://i.ibb.co/unsafe.jpg"}],
        imghost="imgbb",
    )

    result = asyncio.run(tracker.get_firstpic(meta))

    assert result == "https://img.digitalcore.club/rehosted.jpg"
    assert meta.imghost == "imgbb"
    tracker.rehost_images_manager.check_policy.assert_awaited_once_with(
        meta, "covers", tracker.image_host_policy
    )


def test_digitalcore_firstpic_drops_unrehosted_unsafe_artwork() -> None:
    tracker = _tracker()
    tracker.rehost_images_manager.check_policy = AsyncMock(
        return_value=(
            [{"raw_url": "https://i.ibb.co/still-unsafe.jpg"}],
            False,
            True,
        )
    )
    meta = Meta(
        category="MUSIC",
        hosted_artwork=[{"raw_url": "https://i.ibb.co/unsafe.jpg"}],
    )

    assert asyncio.run(tracker.get_firstpic(meta)) == ""


def test_digitalcore_firstpic_normalizes_ptscreens_cdn() -> None:
    meta = Meta(
        category="BOOK",
        hosted_artwork=[{"raw_url": "https://img.ptscreens.com/cover.png"}],
    )

    assert (
        asyncio.run(_tracker().get_firstpic(meta))
        == "https://img2.ptscreens.com/cover.png"
    )


@pytest.mark.asyncio
async def test_digitalcore_description_normalizes_ptscreens_csp_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = (
        "[center][url=https://ptscreens.com/image/example]"
        "[img=350]https://img.ptscreens.com/example.png[/img][/url] "
        "[url=https://ptscreens.com/image/spectrogram]"
        "[img=350]https://img.ptscreens.com/spectrogram.png[/img][/url]"
        "[/center]"
    )
    monkeypatch.setattr(
        "src.integrations.trackers.digitalcore.DescriptionBuilder."
        "general_description_generator",
        AsyncMock(return_value=description),
    )

    result = await _tracker().generate_description(_make_meta())

    assert "img.ptscreens.com" not in result
    assert result.count("https://img2.ptscreens.com/") == 2
    assert "https://ptscreens.com/image/example" in result


@pytest.mark.asyncio
async def test_digitalcore_upload_release_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    response = _response({"id": 321, "message": "ok"})
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(return_value=response)
    )
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=_valid_torrent(tmp_path)
    )
    meta = _make_meta(tracker_status={})
    status: dict[str, Any] = {}

    assert await tracker._upload_release(meta, {}, "Release", status)
    assert status["torrent_id"] == "321/"
    tracker.common.download_tracker_torrent.assert_awaited_once()


@pytest.mark.asyncio
async def test_digitalcore_retries_until_registered_torrent_is_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        side_effect=[None, _valid_torrent(tmp_path)]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(digitalcore.asyncio, "sleep", sleep)
    status: dict[str, Any] = {}

    assert await tracker._handle_upload_response(
        _make_meta(), status, _response({"id": 321, "message": "ok"})
    )

    assert tracker.common.download_tracker_torrent.await_count == 2
    sleep.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_digitalcore_blocks_client_progress_when_torrent_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    sleep = AsyncMock()
    monkeypatch.setattr(digitalcore.asyncio, "sleep", sleep)
    status: dict[str, Any] = {}

    assert not await tracker._handle_upload_response(
        _make_meta(), status, _response({"id": 321, "message": "ok"})
    )

    assert tracker.common.download_tracker_torrent.await_count == 4
    assert status["status_message"] == (
        "data error: registered torrent unavailable for client injection"
    )
    assert sleep.await_args_list == [call(10), call(20), call(30)]


@pytest.mark.asyncio
async def test_digitalcore_rejects_invalid_registered_torrent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    invalid = tmp_path / "invalid.torrent"
    invalid.write_text("not a torrent", encoding="utf-8")
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=str(invalid)
    )
    sleep = AsyncMock()
    monkeypatch.setattr(digitalcore.asyncio, "sleep", sleep)
    status: dict[str, Any] = {}

    assert not await tracker._handle_upload_response(
        _make_meta(), status, _response({"id": 321, "message": "ok"})
    )

    assert tracker.common.download_tracker_torrent.await_count == 4
    assert sleep.await_args_list == [call(10), call(20), call(30)]


@pytest.mark.asyncio
async def test_digitalcore_upload_release_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    request = httpx.Request("POST", tracker.api_base_url)
    response = httpx.Response(422, request=request, text="invalid")
    error = httpx.HTTPStatusError(
        "invalid", request=request, response=response
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(side_effect=error)
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "HTTP 422" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_upload_release_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_submit_upload",
        AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "timed out" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_upload_release_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.api_base_url)
    )
    monkeypatch.setattr(
        tracker, "_submit_upload", AsyncMock(side_effect=error)
    )
    status: dict[str, Any] = {}

    assert not await tracker._upload_release(
        _make_meta(), {}, "Release", status
    )
    assert "offline" in status["status_message"]


@pytest.mark.asyncio
async def test_digitalcore_submit_upload_posts_torrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_torrent_bytes", AsyncMock(return_value=b"torrent")
    )
    response = _response({"id": 1})
    post = AsyncMock(return_value=response)
    tracker.session = SimpleNamespace(
        headers={"X-API-KEY": "key"},
        post=post,
        timeout=SimpleNamespace(write=30.0),
    )  # type: ignore[assignment]

    result = await tracker._submit_upload(
        _make_meta(), {"category": 6}, "Release"
    )

    assert result is response
    post.assert_awaited_once()
    files = post.await_args.kwargs["files"]
    assert files["file"] == (
        "Release.torrent",
        b"torrent",
        "application/x-bittorrent",
    )


def test_digitalcore_dupe_filter_rejects_webrip_for_webdl() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 201,
            "name": "Tatami.2023.Iranian.1080p.WEBRip.x265-DH",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_rejects_codec_mismatch() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 202,
            "name": "Tatami.2024.1080p.AMZN.WEB-DL.x265-OTHER",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_requires_video_semantics_missing() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 203,
            "name": "Tatami 2024 FLY",
            "size": 100,
            "numfiles": "",
        },
        6,
        meta,
    )
    assert entry is None


def test_digitalcore_dupe_filter_keeps_compatible_webdl() -> None:
    meta = _make_meta(
        type="WEBDL",
        source="WEBDL",
        resolution="1080p",
        video_encode="H.264",
        video_codec="AVC",
    )
    entry = DigitalCore._dupe_entry(
        {
            "category": 6,
            "id": 204,
            "name": "Tatami.2024.1080p.AMZN.WEB-DL.H.264-FLY",
            "size": 100,
            "numfiles": 1,
        },
        6,
        meta,
    )
    assert entry is not None
    assert entry["type"] == "WEBDL"
    assert entry["res"] == "1080p"


def test_digitalcore_normalizes_generic_web_source() -> None:
    assert DigitalCore._normalized_source_type("WEB") == "WEBDL"


@pytest.mark.asyncio
async def test_digitalcore_upload_payload_matches_current_public_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "generate_description", AsyncMock(return_value="description")
    )
    monkeypatch.setattr(
        tracker, "mediainfo", AsyncMock(return_value="mediainfo")
    )
    monkeypatch.setattr(tracker, "get_firstpic", AsyncMock(return_value=""))
    meta = _make_meta(resolution="1080p", imdb_tt="")

    data = await tracker.fetch_data(meta)

    assert data["category"] == 6
    assert data["imdbId"] == "0"
    assert data["reqid"] == "0"
    assert data["anonymousUpload"] == "0"
    assert data["p2p"] == "0"
    assert data["unrar"] == "1"
    assert data["othergenre"] == ""
    assert data["requestModQueue"] == "0"
    assert data["modQueueMessage"] == ""
    assert "section" not in data
    assert "frileech" not in data


def test_digitalcore_normalizes_language_like_current_upload_form() -> None:
    assert DigitalCore._normalized_language("English") == "english"
    assert (
        DigitalCore._normalized_language("English, German, English")
        == "english,german"
    )
    assert (
        DigitalCore._normalized_language("ALL, Brazilian Portuguese")
        == "brazilian-portuguese"
    )


@pytest.mark.asyncio
async def test_digitalcore_resolves_external_imdb_to_internal_id() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_response(
            {"id": 2468, "internalId": 2468, "imdbid": "tt35521200"}
        )
    )

    assert await tracker._resolve_imdb_id("tt35521200") == "2468"
    tracker.session.get.assert_awaited_once_with(
        "https://digitalcore.club/api/v1/moviedata/imdb/tt35521200"
    )


@pytest.mark.asyncio
async def test_digitalcore_imdb_resolution_failure_preserves_external_id() -> (
    None
):
    tracker = _tracker()
    tracker.session.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_response({"message": "backend unavailable"}, status=500)
    )

    assert await tracker._resolve_imdb_id("tt35521200") == "tt35521200"
    assert await tracker._resolve_imdb_id("") == "0"
    assert await tracker._resolve_imdb_id("1234") == "1234"


@pytest.mark.asyncio
async def test_digitalcore_upload_reports_unsupported_category_before_post() -> (
    None
):
    tracker = _tracker()
    meta = _make_meta(resolution="480p", tracker_status={})

    assert not await tracker.upload(meta)
    assert (
        "Unsupported category/resolution"
        in meta.tracker_status["DIGITALCORE"]["status_message"]
    )


@pytest.mark.asyncio
async def test_digitalcore_success_download_is_restricted_to_tracker_host(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    response = _response({"id": 654, "message": "ok"})
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=_valid_torrent(tmp_path)
    )
    status: dict[str, Any] = {}

    assert await tracker._handle_upload_response(
        _make_meta(), status, response
    )

    kwargs = tracker.common.download_tracker_torrent.await_args.kwargs
    assert kwargs["allowed_hosts"] == ("digitalcore.club",)
    assert kwargs["max_size"] == 8 * 1024 * 1024


def test_digitalcore_response_message_handles_current_error_shapes() -> None:
    duplicate = _response(
        {
            "error": "Duplicate",
            "torrent_name": "Example_Movie",
            "torrent_id": 777,
        }
    )
    nested = _response({"data": {"message": "Validation failed"}})
    plain = _response({}, text="Authentication Required")
    malformed = _response({}, text="<html>upstream error</html>")

    assert DigitalCore._response_message(duplicate) == (
        "Duplicate: Example Movie already exists (torrent ID 777)."
    )
    assert DigitalCore._response_message(nested) == "Validation failed"
    assert DigitalCore._response_message(plain) == "Authentication Required"
    assert (
        DigitalCore._response_message(malformed)
        == "<html>upstream error</html>"
    )


@pytest.mark.asyncio
async def test_digitalcore_retries_invalid_metainfo_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    candidate = tmp_path / "candidate.torrent"
    candidate.write_bytes(b"d4:infodee")
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=str(candidate)
    )
    sleep = AsyncMock()
    monkeypatch.setattr(
        digitalcore.Torrent,
        "read",
        lambda _path: (_ for _ in ()).throw(MetainfoError("invalid metainfo")),
    )
    monkeypatch.setattr(digitalcore.asyncio, "sleep", sleep)

    assert not await tracker._handle_upload_response(
        _make_meta(), {}, _response({"id": 321, "message": "ok"})
    )
    assert tracker.common.download_tracker_torrent.await_count == 4
    assert sleep.await_args_list == [call(10), call(20), call(30)]


@pytest.mark.asyncio
async def test_digitalcore_retry_deadline_skips_fourth_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    sleep = AsyncMock()
    clock = Mock(side_effect=[0.0, 0.0, 0.0, 10.0, 10.0, 30.0, 30.0])
    monkeypatch.setattr(
        digitalcore, "time", SimpleNamespace(monotonic=clock), raising=False
    )
    monkeypatch.setattr(digitalcore.asyncio, "sleep", sleep)

    assert not await tracker._handle_upload_response(
        _make_meta(), {}, _response({"id": 654, "message": "ok"})
    )
    assert tracker.common.download_tracker_torrent.await_count == 3
    assert sleep.await_args_list == [call(10), call(20)]


@pytest.mark.asyncio
async def test_digitalcore_retry_timeout_returns_false_without_client_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.download_tracker_torrent = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    client_injection = AsyncMock()
    tracker.common.add_to_client = client_injection  # type: ignore[method-assign]

    async def timed_out(awaitable: Any, **_kwargs: Any) -> None:
        awaitable.close()
        raise TimeoutError

    wait_for = AsyncMock(side_effect=timed_out)
    monkeypatch.setattr(digitalcore.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(digitalcore.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        digitalcore, "time", SimpleNamespace(monotonic=Mock(return_value=0.0)), raising=False
    )

    assert not await tracker._handle_upload_response(
        _make_meta(), {}, _response({"id": 655, "message": "ok"})
    )
    wait_for.assert_awaited_once()
    client_injection.assert_not_awaited()
