from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.USENET import nzbgeek as nzbgeek_module
from src.integrations.trackers.USENET.nzbgeek import NZBGeek


def _config(*, limit: int = 10) -> dict[str, Any]:
    return {
        "TRACKERS": {
            "NZBGEEK": {"api_key": "test-key", "daily_api_hit_limit": limit}
        },
        "USENET": {},
    }


def _tracker(*, limit: int = 10) -> NZBGeek:
    return NZBGeek(_config(limit=limit))


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "scene_name": "",
        "basename_no_ext": "Release.Name",
        "category": "MOVIE",
        "resolution": "1080p",
        "title": "Release Name",
        "tvdb_id": 0,
        "season_int": 0,
        "episode_int": 0,
        "imdb_tt": "",
        "platform": "PC",
        "format": "MP3",
        "audiobook": False,
        "type": "EPUB",
        "scene": False,
        "is_disc": "",
        "nzb_path": "",
        "tracker_status": {},
        "debug": False,
    }
    state.update(values)
    return Meta(state)


def _response(status: int = 200, text: str = "") -> httpx.Response:
    return httpx.Response(
        status,
        text=text,
        request=httpx.Request("GET", "https://api.nzbgeek.info/api"),
    )


@pytest.mark.asyncio
async def test_nzbgeek_name_query_and_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path, scene_name="Scene.Release")
    monkeypatch.setattr(
        nzbgeek_module, "build_newznab_search_query", lambda _meta: "query"
    )
    monkeypatch.setattr(
        nzbgeek_module, "parse_newznab_dupes", lambda text: [{"name": text}]
    )

    assert await tracker.get_name(meta) == "Scene.Release"
    assert tracker.get_search_query(meta) == "query"
    assert tracker._parse_dupes_from_response("payload") == [
        {"name": "payload"}
    ]


@pytest.mark.asyncio
async def test_nzbgeek_local_upload_cache_short_circuits(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    cache = tmp_path / "tmp" / meta.uuid / "NZBGEEK_upload_ok"
    cache.parent.mkdir(parents=True)
    cache.write_text("ok", encoding="utf-8")

    assert await tracker.search_existing(meta) == ["Release.Name"]


@pytest.mark.asyncio
async def test_nzbgeek_search_disabled_by_limit(tmp_path: Path) -> None:
    assert await _tracker(limit=0).search_existing(_meta(tmp_path)) == []


@pytest.mark.asyncio
async def test_nzbgeek_search_hit_limit_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        nzbgeek_module,
        "reserve_daily_api_hit",
        AsyncMock(return_value=(False, 10)),
    )
    assert await _tracker().search_existing(_meta(tmp_path)) == []


@pytest.mark.asyncio
async def test_nzbgeek_search_success_and_empty_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        nzbgeek_module,
        "reserve_daily_api_hit",
        AsyncMock(return_value=(True, 1)),
    )
    monkeypatch.setattr(
        tracker, "_parse_dupes_from_response", lambda text: [{"name": text}]
    )
    monkeypatch.setattr(
        tracker,
        "_search_response",
        AsyncMock(return_value=_response(text="dupe")),
    )
    assert await tracker.search_existing(_meta(tmp_path)) == [{"name": "dupe"}]

    tracker._search_response = AsyncMock(return_value=_response(text="   "))  # type: ignore[method-assign]
    assert await tracker.search_existing(_meta(tmp_path, uuid="empty")) == []


def test_nzbgeek_search_parameter_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker, "get_search_query", lambda _meta: "fallback-query"
    )

    tv = _meta(
        tmp_path, category="TV", tvdb_id=123, season_int=2, episode_int=4
    )
    assert tracker._search_params(tv) == {
        "cat": "5040",
        "t": "tvsearch",
        "tvdbid": "123",
        "season": "2",
        "ep": "4",
    }

    tv_query = _meta(tmp_path, category="TV", tvdb_id="bad")
    assert tracker._tv_search_identity(tv_query) == {"q": "fallback-query"}

    movie = _meta(tmp_path, category="MOVIE", imdb_tt="tt123")
    assert tracker._movie_search_params(movie) == {
        "t": "movie",
        "imdbid": "tt123",
    }
    assert tracker._movie_search_params(
        _meta(tmp_path, category="MOVIE", imdb_tt="")
    ) == {"t": "movie", "q": "fallback-query"}

    generic = _meta(tmp_path, category="BOOK")
    assert tracker._search_params(generic)["t"] == "search"


class _Client:
    response: ClassVar[httpx.Response] = _response(text="ok")
    params: ClassVar[dict[str, str]] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(
        self, _url: str, *, params: dict[str, str]
    ) -> httpx.Response:
        type(self).params = params
        return type(self).response


@pytest.mark.asyncio
async def test_nzbgeek_search_response_builds_api_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nzbgeek_module.httpx, "AsyncClient", _Client)
    response = await _tracker()._search_response({"cat": "2040"})
    assert response.text == "ok"
    assert _Client.params["apikey"] == "test-key"
    assert _Client.params["extended"] == "1"


def test_nzbgeek_remaining_category_branches(tmp_path: Path) -> None:
    tracker = _tracker()
    cases = (
        (_meta(tmp_path, category="XXX", resolution="1080p"), "6040"),
        (_meta(tmp_path, category="XXX", resolution="480p"), "6070"),
        (_meta(tmp_path, category="GAME", platform="Nintendo Switch"), "1035"),
        (_meta(tmp_path, category="GAME", platform="PlayStation 5"), "1000"),
        (_meta(tmp_path, category="GAME", platform="PC"), "4050"),
        (_meta(tmp_path, category="MUSIC", format="Audiobook"), "3030"),
        (_meta(tmp_path, category="MUSIC", format="MP3"), "3010"),
        (_meta(tmp_path, category="UNKNOWN"), "8010"),
    )
    for meta, expected in cases:
        assert tracker.get_category_id(meta) == expected
    assert tracker._quality_band("480p") == "sd"
    assert tracker._is_hd_or_uhd(_meta(tmp_path, resolution="2160p"))


@pytest.mark.asyncio
async def test_nzbgeek_nfo_candidates_scene_and_missing(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    scene_root = tmp_path / "tmp" / "scene"
    scene_root.mkdir(parents=True)
    (scene_root / "release.nfo").write_bytes(b"nfo")
    scene = _meta(tmp_path, uuid="scene", scene=True)
    assert await tracker._get_nfo_file(scene) == (
        "release.nfo",
        b"nfo",
        "application/octet-stream",
    )

    missing = _meta(tmp_path, uuid="missing")
    assert await tracker._get_nfo_file(missing) is None


@pytest.mark.asyncio
async def test_nzbgeek_upload_missing_source_and_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    assert not await tracker.upload(_meta(tmp_path, nzb_path=""))

    nzb = tmp_path / "release.nzb"
    nzb.write_bytes(b"nzb")
    tracker.common.check_nzb_file = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_get_nfo_file", AsyncMock(return_value=None))
    debug = _meta(tmp_path, uuid="debug", nzb_path=str(nzb), debug=True)
    assert await tracker.upload(debug)
    assert debug.tracker_status["NZBGEEK"]["status_message"].startswith(
        "Debug mode"
    )


@pytest.mark.asyncio
async def test_nzbgeek_upload_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path, tracker_status={})
    files: dict[str, tuple[str, bytes, str]] = {}

    monkeypatch.setattr(
        tracker,
        "_submit",
        AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    )
    assert not await tracker._upload_with_error_handling(
        meta, files, None, meta.tracker_status.setdefault("NZBGEEK", {})
    )
    assert "timed out" in meta.tracker_status["NZBGEEK"]["status_message"]

    request_error = httpx.RequestError(
        "offline", request=httpx.Request("POST", tracker.submit_url)
    )
    tracker._submit = AsyncMock(side_effect=request_error)  # type: ignore[method-assign]
    assert not await tracker._upload_with_error_handling(
        meta, files, None, meta.tracker_status["NZBGEEK"]
    )
    assert "offline" in meta.tracker_status["NZBGEEK"]["status_message"]

    tracker._submit = AsyncMock(side_effect=RuntimeError("unexpected"))  # type: ignore[method-assign]
    assert not await tracker._upload_with_error_handling(
        meta, files, None, meta.tracker_status["NZBGEEK"]
    )
    assert (
        "Unexpected error" in meta.tracker_status["NZBGEEK"]["status_message"]
    )


@pytest.mark.asyncio
async def test_nzbgeek_process_upload_response_rejections(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    status: dict[str, Any] = {}
    assert not await tracker._process_upload_response(
        meta, _response(status=500, text="error"), None, status
    )
    assert "HTTP 500" in status["status_message"]

    status = {}
    bad = _response(
        text='{"response":{"@attributes":{"API":"NO","REGISTER":"OK"}}}'
    )
    assert not await tracker._process_upload_response(meta, bad, None, status)
    assert "did not confirm" in status["status_message"]
