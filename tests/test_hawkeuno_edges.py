from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.hawkeuno import HawkeUno


def _config() -> dict:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "HAWKEUNO": {
                "api_key": "test-key",
                "announce_url": "https://tracker.invalid/announce",
                "internal": True,
                "internal_groups": ["GROUP"],
            }
        },
    }


def _tracker() -> HawkeUno:
    return HawkeUno(_config())


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "type": "ENCODE",
        "language_checked": True,
        "audio_languages": ["English"],
        "valid_mi_settings": True,
        "is_disc": "",
        "video_encode": "x265",
        "video_codec": "HEVC",
        "mediainfo": {"media": {"track": []}},
        "genre": [],
        "unattended": True,
        "category": "MOVIE",
        "tmdb": 1,
        "anon": False,
        "imdb_id": 1,
        "edition": "",
        "tag": "-GROUP",
        "tracker_status": {},
        "debug": False,
        "repack": "",
        "region": "",
        "distributor": "",
        "season_int": 0,
        "episode_int": 0,
        "tvdb_id": 0,
        "mal_id": 0,
        "tv_pack": False,
    }
    state.update(values)
    return Meta(state)


def test_hawkeuno_hevc_crf_passes_policy() -> None:
    meta = _meta(
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Video",
                        "Encoded_Library_Settings": "rc=crf / crf=20",
                    }
                ]
            }
        }
    )
    assert _tracker()._codec_quality_policy_passes(meta)


def test_hawkeuno_media_tracks_reject_non_list_payload() -> None:
    assert HawkeUno._media_tracks(_meta(mediainfo={"media": {"track": "bad"}})) == []


def test_hawkeuno_animation_skips_bitrate_floor() -> None:
    tracker = _tracker()
    assert tracker._bitrate_policy_passes(_meta(genre=["Animation"]), "1000000")


def test_hawkeuno_low_bitrate_is_rejected() -> None:
    tracker = _tracker()
    assert not tracker._bitrate_policy_passes(_meta(genre=["Drama"]), "2000000")


@pytest.mark.asyncio
async def test_hawkeuno_internal_payload_marks_release() -> None:
    tracker = _tracker()
    tracker.get_type_id = AsyncMock(return_value={"type_id": "15"})  # type: ignore[method-assign]
    data = await tracker._base_upload_data(_meta(tag="-GROUP"))
    assert data["internal"] == 1


@pytest.mark.asyncio
async def test_hawkeuno_upload_http_status_error() -> None:
    tracker = _tracker()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    request = httpx.Request("POST", tracker.upload_url)
    response = httpx.Response(422, request=request, text="bad payload")
    tracker._submit_upload = AsyncMock(side_effect=httpx.HTTPStatusError("bad", request=request, response=response))  # type: ignore[method-assign]
    meta = _meta()
    assert not await tracker.upload(meta)
    assert "HTTP 422" in meta.tracker_status["HAWKEUNO"]["status_message"]


@pytest.mark.asyncio
async def test_hawkeuno_upload_request_error() -> None:
    tracker = _tracker()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    error = httpx.RequestError("offline", request=httpx.Request("POST", tracker.upload_url))
    tracker._submit_upload = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    meta = _meta()
    assert not await tracker.upload(meta)
    assert "offline" in meta.tracker_status["HAWKEUNO"]["status_message"]


@pytest.mark.asyncio
async def test_hawkeuno_upload_unexpected_error_propagates() -> None:
    tracker = _tracker()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    tracker._submit_upload = AsyncMock(side_effect=RuntimeError("unexpected"))  # type: ignore[method-assign]
    meta = _meta()
    with pytest.raises(RuntimeError, match="unexpected"):
        await tracker.upload(meta)
    assert "unexpected" in meta.tracker_status["HAWKEUNO"]["status_message"]
