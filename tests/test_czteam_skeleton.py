from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from src.domain_models.release import Meta
from src.domain_models.tracker_catalog import TRACKER_DEFINITIONS
from src.integrations.trackers.NEXUSPHP.czteam import CZTeam
from src.integrations.trackers.registry import tracker_class_map


def _tracker(**settings: object) -> CZTeam:
    return CZTeam({"DEFAULT": {"tmdb_api": "dummy"}, "TRACKERS": {"CZTEAM": dict(settings)}})


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "video_codec": "H.264",
        "name": "Movie.2024.1080p.WEB-DL.H.264-GROUP",
    }
    defaults.update(values)
    return Meta(**defaults)


def test_czteam_is_registered_in_the_registry_and_catalog() -> None:
    assert tracker_class_map["CZTEAM"] is CZTeam
    definition = TRACKER_DEFINITIONS["CZTEAM"]
    assert definition.auth_type == "cookies"
    assert definition.supported_categories == ("MOVIE", "TV", "GAME", "MUSIC", "XXX")
    assert definition.comment_hosts == ("czteam.me",)


def test_czteam_reads_ids_from_configuration() -> None:
    tracker = _tracker(
        categories={"MOVIE": 7},
        types={"WEBDL": 3},
        codecs={"H.264": 5},
        resolutions={"1080p": 2},
    )
    meta = _meta()
    assert tracker.get_category(meta) == 7
    assert tracker.get_type(meta) == 3
    assert tracker.get_codec(meta) == 5
    assert tracker.get_resolution(meta) == 2


def test_czteam_ignores_unknown_or_invalid_configured_ids() -> None:
    tracker = _tracker(categories={"MOVIE": "abc", "TV": 0})
    assert tracker.get_category(_meta()) == 29
    assert tracker.get_category(_meta(category="TV")) == 5
    assert tracker.get_category(_meta(category="BOOK")) == 0


def test_czteam_does_not_upload_an_unmapped_category() -> None:
    tracker = _tracker()
    tracker.cookie_auth_uploader.handle_upload = AsyncMock()  # type: ignore[method-assign]
    meta = _meta(category="BOOK")
    assert asyncio.run(tracker.upload(meta)) is False
    tracker.cookie_auth_uploader.handle_upload.assert_not_awaited()
    assert meta.skipping == "CZTEAM"


def test_czteam_uploads_a_mapped_category() -> None:
    tracker = _tracker(categories={"MOVIE": 7})
    tracker.cookie_validator.load_session_cookies = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    tracker.get_data = AsyncMock(return_value={"type": 7})  # type: ignore[method-assign]
    tracker.cookie_auth_uploader.handle_upload = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )
    assert asyncio.run(tracker.upload(_meta())) is True
    tracker.cookie_auth_uploader.handle_upload.assert_awaited_once()


def test_czteam_maps_the_real_site_categories() -> None:
    tracker = _tracker()
    assert tracker.get_category(_meta()) == 29
    assert tracker.get_category(_meta(type="XviD")) == 19
    assert tracker.get_category(_meta(is_disc="BDMV")) == 36
    assert tracker.get_category(_meta(category="TV", resolution="1080p")) == 5
    assert tracker.get_category(_meta(category="TV", resolution="480p")) == 7
    assert tracker.get_category(_meta(category="GAME", is_disc="ISO")) == 4
    assert tracker.get_category(
        _meta(category="GAME", platform="PlayStation 5")
    ) == 12
    assert tracker.get_category(_meta(category="MUSIC", type="FLAC")) == 35
    assert tracker.get_category(_meta(category="BOOK")) == 0


def test_czteam_validates_the_literal_enums() -> None:
    assert CZTeam._enum_value("1080p", CZTeam._RESOLUTIONS) == "1080p"
    assert CZTeam._enum_value("1440p", CZTeam._RESOLUTIONS) == ""
    assert CZTeam._enum_value("h.265", CZTeam._CODECS) == "H.265"
    assert CZTeam._enum_value("mkv", CZTeam._CONTAINERS) == "MKV"
    assert CZTeam._enum_value("web-dl", CZTeam._SOURCES) == "WEB-DL"
