from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.services import preparation_helpers as helpers


class _FinalizeManager:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tvmaze_tvdb = (0, 0, None, "")
        self.tv_data_updates: dict[str, object] = {}
        self.tvdb_result = ([], 0)
        self.localized_result: object = {"localized": True}

    async def get_season_episode(self, _video: str, meta: Meta) -> Meta:
        self.calls.append("season")
        return meta

    async def check_season_pack_completeness(self, _meta: Meta) -> None:
        self.calls.append("pack")

    async def get_tvmaze_tvdb(self, *_args, **_kwargs):
        self.calls.append("tvmaze_tvdb")
        return self.tvmaze_tvdb

    async def get_tv_data(self, meta: Meta) -> Meta:
        self.calls.append("tv_data")
        for key, value in self.tv_data_updates.items():
            setattr(meta, key, value)
        return meta

    async def search_tvdb_series(self, **_kwargs):
        self.calls.append("tvdb_search")
        return self.tvdb_result

    async def check_hosts(self, *_args, **_kwargs) -> None:
        self.calls.append("rehost")

    async def get_source_override(self, meta: Meta, **_kwargs) -> Meta:
        self.calls.append("override")
        return meta

    async def get_audio_v2(self, *_args, **_kwargs):
        self.calls.append("audio")
        return "DDP 5.1", "5.1", False

    async def is_scene(self, *_args, **_kwargs):
        self.calls.append("scene")
        return "Scene.Release.mkv", True, 123

    async def get_tmdb_localized_data(self, *_args, **_kwargs):
        self.calls.append("localized")
        return self.localized_result


def _prep(manager: _FinalizeManager, *, config: dict | None = None):
    return SimpleNamespace(
        config=config
        or {
            "DEFAULT": {
                "get_bluray_info": False,
                "bluray_score": 100,
                "bluray_single_score": 100,
                "use_bluray_images": False,
                "user_overrides": False,
                "personal_release_groups": [],
                "episode_overview": False,
            }
        },
        season_episode_manager=manager,
        metadata_searching_manager=manager,
        tvdb_handler=manager,
        rehost_images_manager=manager,
        overrides=manager,
        audio_manager=manager,
        scene_manager=manager,
        tmdb_manager=manager,
        stream_optimized=AsyncMock(
            side_effect=lambda value: 1 if value else 0
        ),
        parse_scene_nfo=AsyncMock(),
        check_adult_media=lambda _meta: True,
    )


def _meta(tmp_path: Path, **values: object) -> Meta:
    video = tmp_path / "release.mkv"
    video.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "Release.2024.1080p",
        "path": str(video),
        "filelist": [str(video)],
        "filename": video.name,
        "category": "MOVIE",
        "title": "Main Title",
        "year": 2024,
        "search_year": 2024,
        "imdb_id": 123,
        "tmdb_id": 456,
        "tvdb_id": 0,
        "tvmaze_id": 0,
        "mal_id": 0,
        "imdb_info": {},
        "manual_language": "",
        "manual_date": "",
        "tvmaze_manual": 0,
        "tv_pack": False,
        "not_anime": True,
        "scene": False,
        "site_check": False,
        "edit": False,
        "is_disc": "",
        "resolution": "1080p",
        "type": "ENCODE",
        "source": "WEB",
        "tag": "-GROUP",
        "trackers": [],
        "tracker_ids": {},
        "mediainfo": {"media": {"track": []}},
        "genres": [],
        "audio": "",
        "channels": "",
        "service": "",
        "no_edition": False,
        "manual_edition": "",
        "stream": False,
        "we_need_tag": False,
        "no_tag": False,
        "no_override": False,
        "region": "",
        "distributor": "",
        "bdinfo": {},
        "discs": [],
        "keep_folder": False,
        "pre_release": False,
    }
    state.update(values)
    return Meta(state)


def _patch_finalize_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        helpers.video_manager, "get_container", AsyncMock(return_value="MKV")
    )
    monkeypatch.setattr(
        helpers.video_manager, "is_3d", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        helpers.video_manager, "get_uhd", AsyncMock(return_value="UHD")
    )
    monkeypatch.setattr(
        helpers.video_manager, "get_hdr", AsyncMock(return_value="HDR10")
    )
    monkeypatch.setattr(
        helpers.video_manager,
        "get_video_codec",
        AsyncMock(return_value="H.264"),
    )
    monkeypatch.setattr(
        helpers.video_manager,
        "get_video_encode",
        AsyncMock(return_value=("x264", "H.264", True, "10")),
    )
    monkeypatch.setattr(
        helpers, "get_source", AsyncMock(return_value=("Web", "ENCODE"))
    )
    monkeypatch.setattr(
        helpers, "get_distributor", AsyncMock(side_effect=lambda value: value)
    )
    monkeypatch.setattr(
        helpers,
        "get_region",
        AsyncMock(side_effect=lambda _bdinfo, value: value or "US"),
    )

    async def service(*_args, get_services_only: bool = False, **_kwargs):
        return (
            {"Amazon": "AMZN", "Crunchyroll": "CR", "HIDIVE": "HIDI"}
            if get_services_only
            else ("AMZN", "Amazon")
        )

    monkeypatch.setattr(helpers, "get_service", service)
    monkeypatch.setattr(
        helpers,
        "get_edition",
        AsyncMock(return_value=("Director REPACK2 Cut", "", False)),
    )
    monkeypatch.setattr(helpers, "get_tag", AsyncMock(return_value="-GROUP"))
    monkeypatch.setattr(
        helpers, "tag_override", AsyncMock(side_effect=lambda meta: meta)
    )
    monkeypatch.setattr(
        helpers, "validate_mediainfo", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        helpers,
        "get_bluray_releases",
        AsyncMock(return_value=[{"name": "release"}]),
    )

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(helpers.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        helpers.tvmaze_manager, "search_tvmaze", AsyncMock(return_value=789)
    )
    monkeypatch.setattr(
        helpers.imdb_manager,
        "get_imdb_from_episode",
        AsyncMock(return_value={"series": {"series_id": "tt7654321"}}),
    )
    monkeypatch.setattr(
        helpers.imdb_manager,
        "get_imdb_info_api",
        AsyncMock(
            return_value={
                "title": "Series Title",
                "aka": "AKA Different Name (2024)",
                "year": 2024,
                "genres": "Drama",
            }
        ),
    )


def test_finalize_tv_comprehensive_metadata_ids_bitrates_tags_and_localization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    manager.tvmaze_tvdb = (
        789,
        654,
        [{"name": "result"}],
        "Series Name (2024)",
    )
    manager.tv_data_updates = {"tvdb_imdb_id": "tt9999999"}
    config = {
        "DEFAULT": {
            "get_bluray_info": False,
            "bluray_score": "90",
            "bluray_single_score": "80",
            "use_bluray_images": False,
            "user_overrides": True,
            "personal_release_groups": ["5.1GROUP"],
            "episode_overview": True,
        }
    }
    prep = _prep(manager, config=config)
    tracks = [
        {
            "@type": "General",
            "OverallBitRate": "9000000",
            "FrameRate": "23.976",
        },
        {
            "@type": "Video",
            "BitRate": "8000000",
            "FrameRate": "24",
            "Width": "1920",
            "Height": "1080",
        },
        {"@type": "Audio", "BitRate": "768000"},
    ]
    meta = _meta(
        tmp_path,
        category="TV",
        title="Main Title",
        imdb_info={
            "title": "AKA Completely Different (2024)",
            "aka": "AKA Other Name (2024)",
            "year": 2024,
            "type": "tv movie",
            "genres": "Drama, Action",
        },
        tv_pack=True,
        not_anime=False,
        tvdb_id=0,
        tvmaze_id=0,
        genres=["Action", "Comedy"],
        mediainfo={"media": {"track": tracks}},
        tag="-5.1GROUP",
        channels="5.1",
        stream=True,
        type="ENCODE",
        no_override=False,
        trackers=["FAKE_LOCALIZED", "MISSING"],
    )

    from src.integrations.trackers import registry

    class Localized:
        tmdb_localization_requirements: ClassVar[dict[str, dict[str, str]]] = {
            "pt-BR": {
                "main": "credits,images",
                "season": "episodes",
                "episode": "credits",
            }
        }

    monkeypatch.setitem(
        registry.tracker_class_map, "FAKE_LOCALIZED", Localized
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {"media": {"track": tracks}},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )

    assert {
        "season",
        "pack",
        "tvmaze_tvdb",
        "tv_data",
        "override",
        "audio",
        "localized",
    } <= set(manager.calls)
    assert meta.tv_movie is True
    assert meta.tvmaze_id == 789 and meta.tvdb_id == 654
    assert meta.imdb_id == 7654321
    assert meta.tvdb_search_results
    assert meta.container == "MKV"
    assert meta.audio == "DDP 5.1" and meta.channels == "5.1"
    assert (
        meta.video_bitrate == 8000
        and meta.audio_bitrate == 768
        and meta.frame_rate == 24.0
    )
    assert meta.video_width == 1920 and meta.video_height == 1080
    assert meta.repack == "REPACK2" and "REPACK" not in meta.edition
    assert meta.valid_mi_settings is False
    assert meta.service == "AMZN" and meta.service_longname == "Amazon"
    assert meta.combined_genres == "Action, Comedy, Drama"
    assert meta.adult_media is True
    assert meta.personalrelease is True
    assert meta.tag == "GROUP"
    assert meta.imdb == "7654321" and meta.imdb_tt == "tt7654321"
    assert meta.tmdb_localized_data["pt-BR"]["main"] == {"localized": True}
    assert meta.pre_release is False


def test_finalize_tv_individual_tvmaze_tvdb_and_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    prep = _prep(manager)
    meta = _meta(
        tmp_path,
        category="TV",
        tvmaze_id=0,
        tvdb_id=123,
        imdb_id=123,
        not_anime=True,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )
    assert meta.tvmaze_id == 789

    manager = _FinalizeManager()
    manager.tvdb_result = ([{"series": "result"}], 456)
    prep = _prep(manager)
    meta = _meta(
        tmp_path,
        category="TV",
        tvmaze_id=123,
        tvdb_id=0,
        imdb_id=123,
        not_anime=True,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )
    assert meta.tvdb_id == 456 and meta.tvdb_search_results

    async def fail(**_kwargs):
        raise RuntimeError("tvdb failed")

    manager.search_tvdb_series = fail  # type: ignore[method-assign]
    meta = _meta(
        tmp_path,
        category="TV",
        tvmaze_id=123,
        tvdb_id=0,
        imdb_id=123,
        not_anime=True,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )

    monkeypatch.setattr(
        helpers.tvmaze_manager,
        "search_tvmaze",
        AsyncMock(return_value=(321, "extra")),
    )
    manager = _FinalizeManager()
    prep = _prep(manager)
    meta = _meta(
        tmp_path,
        category="TV",
        tvmaze_id=0,
        tvdb_id=123,
        imdb_id=123,
        not_anime=True,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )
    assert meta.tvmaze_id == 321


def test_finalize_bluray_disc_bitrates_images_service_and_no_edition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    config = {
        "DEFAULT": {
            "get_bluray_info": True,
            "bluray_score": 100,
            "bluray_single_score": 100,
            "use_bluray_images": True,
            "user_overrides": False,
            "personal_release_groups": [],
            "episode_overview": False,
        }
    }
    prep = _prep(manager, config=config)
    bdinfo = {
        "video": [{"bitrate": "25,000 kbps", "fps": "23.976"}],
        "audio": [{"bitrate": "1,500 kbps"}],
    }
    meta = _meta(
        tmp_path,
        category="MOVIE",
        is_disc="BDMV",
        distributor=None,
        region=None,
        imdb_id=123,
        resolution="2160p",
        bdinfo=bdinfo,
        service="AMZN",
        tag="GROUP",
        no_edition=True,
        type="DISC",
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            bdinfo,
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )
    assert "rehost" in manager.calls
    assert (
        meta.video_bitrate == 25000
        and meta.audio_bitrate == 1500
        and meta.frame_rate == 23.976
    )
    assert (
        meta.video_width == round((16 / 9) * 2160)
        and meta.video_height == 2160
    )
    assert meta.region == "US" and meta.video_codec == "H.264"
    assert meta.tag == "-GROUP" and meta.edition == ""
    assert meta.service_longname == "Amazon"

    fallback = _meta(
        tmp_path,
        category="MOVIE",
        is_disc="BDMV",
        distributor="US",
        region="US",
        imdb_id=123,
        resolution="1080p",
        discs=[{"bdinfo": bdinfo}],
        bdinfo={},
        no_edition=True,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            fallback,
            fallback.path,
            {},
            {},
            fallback.filename,
            fallback.filename,
            fallback.path,
        )
    )
    assert fallback.video_bitrate == 25000


def test_finalize_subsplease_service_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    prep = _prep(_FinalizeManager())
    cases = [
        ("1080p", "9000000", "", "CR"),
        ("1080p", "7000000", "", "HIDI"),
        ("720p", "5000000", "", "CR"),
        ("720p", "3000000", "", "HIDI"),
        ("1080p", "", "9000000", "CR"),
    ]
    for resolution, bitrate, overall, expected in cases:
        tracks = [
            {"@type": "General", "OverallBitRate": overall},
            {"@type": "Video", "BitRate": bitrate},
        ]
        meta = _meta(
            tmp_path,
            category="TV",
            tag="-SubsPlease",
            resolution=resolution,
            service="",
            not_anime=True,
            tvmaze_id=1,
            tvdb_id=1,
            mediainfo={"media": {"track": tracks}},
        )
        asyncio.run(
            helpers.finalize_metadata(
                prep,
                meta,
                meta.path,
                {},
                {"media": {"track": tracks}},
                meta.filename,
                meta.filename,
                meta.path,
            )
        )
        assert meta.service == expected
        assert meta.episode_title == ""


def test_finalize_tag_scene_error_no_tag_and_book_game_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    prep = _prep(manager)
    monkeypatch.setattr(
        helpers,
        "get_tag",
        AsyncMock(side_effect=["-lower", RuntimeError("tag failed")]),
    )
    meta = _meta(
        tmp_path,
        category="MOVIE",
        tag=None,
        scene=False,
        service="",
        no_tag=True,
        imdb_id=0,
    )
    lower_video = str(tmp_path / "lowercase.release.mkv")
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            lower_video,
            {},
            {},
            meta.filename,
            meta.filename,
            lower_video,
        )
    )
    assert meta.tag == ""
    assert "scene" in manager.calls
    assert meta.imdb == "0" and meta.imdb_tt == ""

    book = _meta(
        tmp_path,
        category="BOOK",
        title="",
        year=0,
        overview="",
        genres=[],
        type="",
        edition="",
        manual_edition="First",
    )
    book_path = str(tmp_path / "book.cbz")
    asyncio.run(
        helpers.finalize_metadata(
            prep, book, book_path, {}, {}, "book.cbz", "book.cbz", book_path
        )
    )
    assert (
        book.container == "cbz" and book.type == "CBZ" and book.comic is True
    )
    assert (
        book.source == "WEB" and book.edition == "First" and book.year is None
    )

    game = _meta(
        tmp_path,
        category="GAME",
        title="",
        year=0,
        overview="",
        genres=[],
        type="",
        source="",
    )
    game_path = str(tmp_path / "game.iso")
    asyncio.run(
        helpers.finalize_metadata(
            prep, game, game_path, {}, {}, "game.iso", "game.iso", game_path
        )
    )
    assert (
        game.container == "iso" and game.type == "GAME" and game.year is None
    )


def test_finalize_localization_failure_and_skipped_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    prep = _prep(manager)

    from src.integrations.trackers import registry

    class Requirements:
        tmdb_localization_requirements: ClassVar[dict[str, dict[str, str]]] = {
            "pt-BR": {
                "season": "credits",
                "episode": "credits",
                "main": "images",
            }
        }

    monkeypatch.setitem(registry.tracker_class_map, "REQ", Requirements)
    meta = _meta(
        tmp_path,
        category="MOVIE",
        tmdb_id=123,
        trackers=["REQ"],
        tv_pack=False,
    )
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )
    assert "localized" in manager.calls

    async def fail(*_args, **_kwargs):
        raise RuntimeError("localized failed")

    manager.get_tmdb_localized_data = fail  # type: ignore[method-assign]
    meta = _meta(tmp_path, category="MOVIE", tmdb_id=123, trackers=["REQ"])
    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )


def test_finalize_prefers_distinct_imdb_aka_when_primary_title_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    prep = _prep(manager)
    meta = _meta(
        tmp_path,
        category="MOVIE",
        title="Main Title",
        imdb_info={
            "title": "Main Title",
            "aka": "Remote Alias (2024)",
            "year": 2024,
        },
        aka="",
    )

    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )

    assert meta.title == "Main Title"
    assert meta.aka == "AKA Remote Alias"


@pytest.mark.parametrize(
    ("remote_aka", "expected"),
    [
        ("AKA Main Title", ""),
        ("Distant Alias", "AKA Distant Alias"),
        ("", ""),
    ],
)
def test_finalize_episode_imdb_aka_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remote_aka: str,
    expected: str,
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    manager.tv_data_updates = {"tvdb_imdb_id": "tt111"}
    prep = _prep(manager)
    monkeypatch.setattr(
        helpers.imdb_manager,
        "get_imdb_from_episode",
        AsyncMock(return_value={"series": {"series_id": "tt7654321"}}),
    )
    monkeypatch.setattr(
        helpers.imdb_manager,
        "get_imdb_info_api",
        AsyncMock(
            return_value={
                "title": "Series Title",
                "aka": remote_aka,
                "year": 2024,
            }
        ),
    )
    meta = _meta(
        tmp_path,
        category="TV",
        title="Main Title",
        imdb_id=123,
        tvmaze_id=1,
        tvdb_id=1,
        not_anime=True,
        imdb_info={},
    )

    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )

    assert meta.imdb_id == 7654321
    assert meta.aka == expected


def test_finalize_uses_meta_bdinfo_region_fallback_scene_tag_and_no_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_finalize_globals(monkeypatch)
    manager = _FinalizeManager()
    prep = _prep(manager)
    prep.parse_scene_nfo = AsyncMock()

    async def no_service(*_args, get_services_only: bool = False, **_kwargs):
        return {} if get_services_only else ("", "")

    monkeypatch.setattr(helpers, "get_service", no_service)
    monkeypatch.setattr(helpers, "get_region", AsyncMock(return_value=None))
    monkeypatch.setattr(helpers, "get_tag", AsyncMock(return_value="-SCENE"))

    from src.integrations.trackers import registry

    class NoRequirements:
        pass

    monkeypatch.setitem(
        registry.tracker_class_map, "NO_REQUIREMENTS", NoRequirements
    )
    stored_bdinfo = {
        "video": [{"bitrate": "20,000 kbps", "fps": "24.000"}],
        "audio": [{"bitrate": "1,000 kbps"}],
    }
    meta = _meta(
        tmp_path,
        category="TV",
        is_disc="BDMV",
        bdinfo=stored_bdinfo,
        discs=[],
        region=None,
        scene=True,
        service="",
        tag=None,
        we_need_tag=True,
        scene_name="Scene.Name",
        tvmaze_id=1,
        tvdb_id=1,
        not_anime=True,
        trackers=["NO_REQUIREMENTS"],
    )

    asyncio.run(
        helpers.finalize_metadata(
            prep,
            meta,
            meta.path,
            {},
            {},
            meta.filename,
            meta.filename,
            meta.path,
        )
    )

    assert meta.video_bitrate == 20000
    assert meta.audio_bitrate == 1000
    assert meta.frame_rate == 24.0
    assert meta.region == ""
    assert meta.tag == "-SCENE"
    prep.parse_scene_nfo.assert_awaited_once_with(meta)
