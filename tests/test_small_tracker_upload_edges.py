from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.trackers import bithdtv as bithdtv_module
from src.integrations.trackers import ptskit as ptskit_module
from src.integrations.trackers.bithdtv import BitHDTV
from src.integrations.trackers.ptskit import Ptskit
from src.integrations.trackers.UNIT3D import aither as aither_module
from src.integrations.trackers.UNIT3D.aither import Aither


class FakeCommon:
    ready: ClassVar[list[tuple[object, ...]]] = []
    created: ClassVar[list[tuple[object, ...]]] = []

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def create_torrent_for_upload(self, *args: object, **_kwargs: object) -> None:
        type(self).created.append(args)

    async def create_torrent_ready_to_seed(self, *args: object, **_kwargs: object) -> None:
        type(self).ready.append(args)

    @classmethod
    def reset(cls) -> None:
        cls.ready = []
        cls.created = []


class BithdResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __bool__(self) -> bool:
        return True

    def json(self) -> Any:
        return self.payload


class BithdClient:
    payload: ClassVar[object] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> BithdResponse:
        return BithdResponse(type(self).payload)


def _bithd_config() -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "BITHDTV": {
                "api_key": "test-key",
                "my_announce_url": "https://tracker.invalid/announce",
            }
        },
    }


def _bithd_meta(tmp_path: Path, **values: object) -> Meta:
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "bithdtv",
        "name": "Example 1080p WEB-DL H.264",
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "three_d": False,
        "bdinfo": {},
        "is_disc": "",
        "filelist": [str(video)],
        "path": str(video),
        "mediainfo": {"media": {"track": []}},
        "tvmaze_id": 123,
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt1"},
        "image_list": [],
        "debug": False,
        "tracker_status": {"BITHDTV": {}},
    }
    state.update(values)
    return Meta(state)


def _bithd_files(meta: Meta) -> None:
    root = release_temp_dir(meta.base_dir, meta.uuid)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("mediainfo", encoding="utf-8")
    (root / "[BITHDTV]DESCRIPTION.txt").write_text("description", encoding="utf-8")
    (root / "[BITHDTV].torrent").write_bytes(b"torrent")


def test_bithdtv_success_and_debug_upload_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeCommon.reset()
    monkeypatch.setattr(bithdtv_module, "Common", FakeCommon)
    monkeypatch.setattr(bithdtv_module.httpx, "AsyncClient", BithdClient)
    monkeypatch.setattr(BitHDTV, "edit_desc", AsyncMock())
    monkeypatch.setattr(bithdtv_module.DescriptionBuilder, "format_short_mediainfo_json", lambda *_args, **_kwargs: "short-mi")
    tracker = BitHDTV(_bithd_config())

    meta = _bithd_meta(tmp_path)
    _bithd_files(meta)
    BithdClient.payload = {"data": {"view": "https://tracker.invalid/1"}}
    assert asyncio.run(tracker.upload(meta))
    assert FakeCommon.ready

    debug = _bithd_meta(tmp_path, uuid="debug", debug=True)
    _bithd_files(debug)
    assert asyncio.run(tracker.upload(debug))
    assert debug.tracker_status["BITHDTV"]["status_message"].startswith("Debug mode")


def test_bithdtv_h265_remux_and_encode_type_ids() -> None:
    tracker = BitHDTV(_bithd_config())
    assert asyncio.run(tracker.get_type_movie_id(Meta(type="REMUX", name="Movie 265", three_d=False))) == "48"
    assert asyncio.run(tracker.get_type_movie_id(Meta(type="ENCODE", name="Movie 265", three_d=False))) == "43"


def _aither_config() -> dict[str, Any]:
    return {"DEFAULT": {}, "TRACKERS": {"AITHER": {}}}


def test_aither_invalid_mediainfo_and_hdr_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Aither(_aither_config())
    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.get_additional_checks(Meta(is_disc="", valid_mi=True)))
    assert not asyncio.run(tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=False)))
    monkeypatch.setattr(tracker, "get_flag", AsyncMock(return_value=0))
    assert asyncio.run(tracker.get_additional_data(Meta(hdr="HDR"))) == {"mod_queue_opt_in": 0, "hdr": 1}


def test_aither_foreign_dvd_and_nondisc_names(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Aither(_aither_config())
    monkeypatch.setattr(aither_module.languages_manager, "has_english_language", AsyncMock(return_value=False))

    dvd = Meta(
        name="Movie 2025 DVD REMUX",
        category="MOVIE",
        type="REMUX",
        source="DVD",
        resolution="480p",
        year=2025,
        manual_year=0,
        language_checked=True,
        audio_languages=["French"],
        no_aka=True,
        no_year=False,
    )
    assert "2025 FRENCH" in asyncio.run(tracker.get_name(dvd))["name"]

    web = Meta(
        name="Movie 1080p WEB-DL",
        category="MOVIE",
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        is_disc="",
        year=2025,
        manual_year=0,
        language_checked=True,
        audio_languages=["French"],
        no_aka=True,
        no_year=False,
    )
    assert "FRENCH 1080p" in asyncio.run(tracker.get_name(web))["name"]


def test_aither_alt_title_ordering() -> None:
    tracker = Aither(_aither_config())
    meta = Meta(
        name="Movie 2025 Alternate 1080p",
        category="MOVIE",
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        year=2025,
        manual_year=0,
        language_checked=True,
        audio_languages=["English"],
        aka="Alternate",
        no_aka=False,
        no_year=False,
    )
    assert "Alternate 2025" in asyncio.run(tracker.get_name(meta))["name"]


class PtsResponse:
    def __init__(self, text: str, url: str = "https://www.ptskit.org/torrents.php") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class PtsSession:
    def __init__(self, response: PtsResponse) -> None:
        self.response = response
        self.cookies: dict[str, str] = {}

    async def get(self, *_args: object, **_kwargs: object) -> PtsResponse:
        return self.response

    async def aclose(self) -> None:
        return None


def _ptskit_config() -> dict[str, Any]:
    return {"DEFAULT": {}, "TRACKERS": {"PTSKIT": {"announce_url": "https://tracker.invalid/announce"}}}


def test_ptskit_login_redirect_marks_tracker_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ptskit_module.httpx, "AsyncClient", lambda *_args, **_kwargs: PtsSession(PtsResponse("")))
    tracker = Ptskit(_ptskit_config())
    tracker.session = PtsSession(PtsResponse("login", "https://www.ptskit.org/login.php"))  # type: ignore[assignment]
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    meta = Meta(imdb_info={"imdbID": "tt1"})

    assert asyncio.run(tracker.search_existing(meta)) == []
    assert meta.skipping == "PTSKIT"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


def test_ptskit_extracts_torrent_names(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <table class='torrents'>
      <tr><td><table class='torrentname'><tr><td><b>Release One</b></td></tr></table></td></tr>
      <tr><td><table class='torrentname'><tr><td>No bold name</td></tr></table></td></tr>
      <tr><td><table class='torrentname'><tr><td><b>Release Two</b></td></tr></table></td></tr>
    </table>
    """
    monkeypatch.setattr(ptskit_module.httpx, "AsyncClient", lambda *_args, **_kwargs: PtsSession(PtsResponse(html)))
    tracker = Ptskit(_ptskit_config())
    tracker.session = PtsSession(PtsResponse(html))  # type: ignore[assignment]

    assert asyncio.run(tracker.search_existing(Meta(imdb_info={"imdbID": "tt1"}))) == ["Release One", "Release Two"]
