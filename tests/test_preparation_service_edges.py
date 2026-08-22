from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import preparation_service
from src.services.preparation_service import Prep


def _prep(config: dict | None = None) -> Prep:
    prep = object.__new__(Prep)
    prep.config = config or {"DEFAULT": {"multiScreens": 2}}
    prep.takescreens_manager = SimpleNamespace()
    prep.rehost_images_manager = SimpleNamespace(
        takescreens_manager=SimpleNamespace()
    )
    return prep


def test_audiobook_cover_postcondition_is_fail_closed() -> None:
    prep = _prep()
    with pytest.raises(
        OperationAbortedError, match="Audiobook cover is required"
    ):
        prep._ensure_audiobook_cover(
            Meta(category="BOOK", audiobook=True, artwork_path="")
        )
    prep._ensure_audiobook_cover(
        Meta(category="BOOK", audiobook=False, artwork_path="")
    )


def test_check_adult_media_manual_tmdb_keywords_genres_and_false() -> None:
    prep = _prep()
    assert prep.check_adult_media(Meta(category="XXX"))
    assert prep.check_adult_media(
        Meta(category="MOVIE", tmdb_adult_media=True)
    )
    assert prep.check_adult_media(Meta(category="MOVIE", keywords=["adult"]))
    assert prep.check_adult_media(
        Meta(category="MOVIE", combined_genres=["Erotic"])
    )
    assert prep.check_adult_media(
        Meta(category="MOVIE", combined_genres="porn")
    )
    assert not prep.check_adult_media(
        Meta(category="MOVIE", keywords=["family"], combined_genres=["Drama"])
    )


def test_get_cat_manual_music_xxx_tv_anime_and_movie(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prep = _prep()

    async def exercise() -> None:
        assert await prep.get_cat("", Meta(manual_category="tv")) == "TV"
        assert await prep.get_cat("", Meta(manual_category=7)) is None  # type: ignore[arg-type]
        assert (
            await prep.get_cat("", Meta(path=str(tmp_path / "track.flac")))
            == "MUSIC"
        )

        monkeypatch.setattr(
            preparation_service.prep_helpers,
            "is_xxx_video_release",
            lambda _path: True,
        )
        assert (
            await prep.get_cat("", Meta(path=str(tmp_path / "release")))
            == "XXX"
        )
        monkeypatch.setattr(
            preparation_service.prep_helpers,
            "is_xxx_video_release",
            lambda _path: False,
        )

        assert (
            await prep.get_cat("", Meta(path="/media/tv/Show/episode.mkv"))
            == "TV"
        )
        assert (
            await prep.get_cat(
                "", Meta(path="/media/movie.mkv", uuid="Show.S01E01")
            )
            == "TV"
        )
        assert (
            await prep.get_cat(
                "", Meta(path="/media/[SubsPlease] Show - 07 (1080p).mkv")
            )
            == "TV"
        )
        assert (
            await prep.get_cat(
                "", Meta(path="/media/Movie.2026.1080p.mkv", uuid="movie")
            )
            == "MOVIE"
        )

    asyncio.run(exercise())


def test_stream_optimized_values() -> None:
    prep = _prep()
    assert asyncio.run(prep.stream_optimized(True)) == 1
    assert asyncio.run(prep.stream_optimized(False)) == 0


def test_parse_scene_nfo_missing_match_code_no_match_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prep = _prep()

    async def services(*_args, **_kwargs):
        return {"Amazon Prime": "AMZN", "Netflix": "NF"}

    monkeypatch.setattr(preparation_service, "get_service", services)

    async def exercise() -> None:
        missing = Meta(scene_nfo_file="")
        await prep.parse_scene_nfo(missing)
        assert not missing.service

        named = tmp_path / "named.nfo"
        named.write_text("Source : Amazon Prime\n", encoding="utf-8")
        meta = Meta(scene_nfo_file=str(named))
        await prep.parse_scene_nfo(meta)
        assert (
            meta.service == "AMZN" and meta.service_longname == "Amazon Prime"
        )

        coded = tmp_path / "coded.nfo"
        coded.write_text("Source: nf\n", encoding="utf-8")
        meta = Meta(scene_nfo_file=str(coded))
        await prep.parse_scene_nfo(meta)
        assert meta.service == "NF" and meta.service_longname == "Netflix"

        unmatched = tmp_path / "unmatched.nfo"
        unmatched.write_text("Source: Unknown\n", encoding="utf-8")
        meta = Meta(scene_nfo_file=str(unmatched))
        await prep.parse_scene_nfo(meta)
        assert not meta.service

        await prep.parse_scene_nfo(
            Meta(scene_nfo_file=str(tmp_path / "missing.nfo"))
        )

    asyncio.run(exercise())


def test_gather_prep_podcast_short_circuit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prep = _prep()
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="podcast",
        path=str(tmp_path / "podcast.mp3"),
        manual_category=" podcast ",
    )
    events: list[str] = []

    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "init_meta",
        lambda *_args: (False, False, object(), False, {}, {}),
    )

    async def gather(target: Meta) -> None:
        events.append("gather")
        target.title = "Podcast"

    async def trackers(*_args) -> None:
        events.append("trackers")

    monkeypatch.setattr(preparation_service, "_gather_podcast_prep_fn", gather)
    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "calculate_source_size",
        lambda *_args: events.append("size"),
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "process_trackers_and_torrent",
        trackers,
    )

    result = asyncio.run(prep.gather_prep(meta, "cli"))
    assert result.category == "PODCAST"
    assert result.title == "Podcast"
    assert events == ["gather", "size", "trackers"]


def test_gather_prep_xxx_contact_sheet_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prep = _prep()
    prep.takescreens_manager = SimpleNamespace(
        xxx_contact_sheet_settings=lambda: (2, 2, 3)
    )
    prep.rehost_images_manager = SimpleNamespace(
        takescreens_manager=SimpleNamespace(prepare_book_cover=AsyncMock())
    )
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="xxx",
        path=str(tmp_path / "video.mp4"),
        category="XXX",
        screens=10,
        filelist=["one.mp4", "two.mp4", "three.mp4", "four.mp4"],
        keep_images=True,
    )
    events: list[str] = []

    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "init_meta",
        lambda *_args: (False, False, object(), False, {}, {}),
    )

    async def detect(*_args):
        return str(tmp_path / "video.mp4"), {}

    async def process(*_args):
        return (
            "video.mp4",
            "video.mp4",
            str(tmp_path / "video.mp4"),
            "term",
            "folder",
            {},
            str(tmp_path / "video.mp4"),
        )

    async def no_op(*_args, **_kwargs):
        events.append("async")

    monkeypatch.setattr(
        preparation_service.prep_helpers, "detect_disc_and_category", detect
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "process_media_files", process
    )
    monkeypatch.setattr(
        preparation_service,
        "sync_single_episode_from_filename",
        lambda _meta: None,
    )
    monkeypatch.setattr(
        preparation_service, "populate_hdr_for_early_capture", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "calculate_source_size",
        lambda *_args: events.append("size"),
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "validate_media", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "process_trackers_and_torrent", no_op
    )
    monkeypatch.setattr(
        preparation_service, "restart_early_artifact_tasks", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "search_metadata", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "finalize_metadata", no_op
    )
    monkeypatch.setattr(preparation_service, "prepare_artwork", no_op)
    monkeypatch.setattr(
        preparation_service.languages_manager,
        "apply_confirmed_single_audio_language",
        no_op,
    )
    monkeypatch.setattr(
        preparation_service.languages_manager, "process_desc_language", no_op
    )
    monkeypatch.setattr(
        preparation_service,
        "manifest_files",
        lambda *_args: [Path("one.png"), Path("two.png")],
    )

    result = asyncio.run(prep.gather_prep(meta, "cli"))
    assert result.screens == 2
    assert "size" in events


def test_capture_early_screenshots_normal_video_and_failure(
    tmp_path: Path,
) -> None:
    prep = _prep()
    screenshots = AsyncMock()
    prep.takescreens_manager = SimpleNamespace(screenshots=screenshots)
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="video",
        category="MOVIE",
        screens=2,
        path=str(tmp_path / "video.mkv"),
    )
    asyncio.run(
        prep._capture_early_screenshots(
            meta, "video.mkv", str(tmp_path / "video.mkv"), {}
        )
    )
    screenshots.assert_awaited_once()

    screenshots.side_effect = RuntimeError("failed")
    asyncio.run(
        prep._capture_early_screenshots(
            meta, "video.mkv", str(tmp_path / "video.mkv"), {}
        )
    )


def test_constructor_and_delegate_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    def factory(name: str):
        class Adapter:
            def __init__(self, *_args, **_kwargs) -> None:
                created.append(name)

        return Adapter

    for name in (
        "TvdbData",
        "ApplyOverrides",
        "AudioManager",
        "DiscInfoManager",
        "NameManager",
        "TrackerDataManager",
        "SceneManager",
        "MetadataSearchingManager",
        "TmdbManager",
        "SeasonEpisodeManager",
        "RadarrManager",
        "SonarrManager",
        "RehostImagesManager",
        "TakeScreensManager",
    ):
        monkeypatch.setattr(preparation_service, name, factory(name))

    prep = Prep(4, "IMGBB", {"DEFAULT": {}})
    assert prep.screens == 4 and prep.img_host == "imgbb"
    assert len(created) == 14

    meta = Meta(path="release")
    monkeypatch.setattr(
        preparation_service,
        "_resolve_book_filelist_fn",
        lambda target, location: (location, [target.path], "book", "file"),
    )
    monkeypatch.setattr(
        preparation_service,
        "_resolve_game_filelist_fn",
        lambda target, location: (location, [target.path], "game", "file"),
    )
    assert Prep._resolve_book_filelist(meta, "book-path")[2] == "book"
    assert Prep._resolve_game_filelist(meta, "game-path")[2] == "game"

    book = AsyncMock()
    game = AsyncMock()
    music = AsyncMock()
    monkeypatch.setattr(preparation_service, "_gather_book_prep_fn", book)
    monkeypatch.setattr(preparation_service, "_gather_game_prep_fn", game)
    monkeypatch.setattr(preparation_service, "_gather_music_prep_fn", music)
    asyncio.run(prep._gather_book_prep(meta, "video", "base"))
    asyncio.run(prep._gather_game_prep(meta, "video", "base"))
    asyncio.run(prep._gather_music_prep(meta))
    book.assert_awaited_once_with(meta, "video", "base", prep.config)
    game.assert_awaited_once_with(meta, "video", "base", prep.config)
    music.assert_awaited_once_with(meta, prep.config)


def test_gather_prep_book_writes_metadata_and_awaits_early_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prep = _prep()
    cover = AsyncMock()
    prep.rehost_images_manager = SimpleNamespace(
        takescreens_manager=SimpleNamespace(prepare_book_cover=cover)
    )
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="book",
        path=str(tmp_path / "book.epub"),
        category="BOOK",
        keep_images=False,
        screens=1,
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "init_meta",
        lambda *_args: (False, False, object(), False, {}, {}),
    )

    async def detect(*_args):
        return str(tmp_path / "book.epub"), {}

    async def process(*_args):
        return (
            "book.epub",
            "book.epub",
            str(tmp_path / "book.epub"),
            "term",
            "folder",
            {},
            str(tmp_path / "book.epub"),
        )

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        preparation_service.prep_helpers, "detect_disc_and_category", detect
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "process_media_files", process
    )
    monkeypatch.setattr(
        preparation_service,
        "sync_single_episode_from_filename",
        lambda _meta: None,
    )
    monkeypatch.setattr(
        preparation_service, "populate_hdr_for_early_capture", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers,
        "calculate_source_size",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "validate_media", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "process_trackers_and_torrent", no_op
    )
    monkeypatch.setattr(
        preparation_service, "restart_early_artifact_tasks", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "search_metadata", no_op
    )
    monkeypatch.setattr(
        preparation_service.prep_helpers, "finalize_metadata", no_op
    )
    monkeypatch.setattr(preparation_service, "prepare_artwork", no_op)
    monkeypatch.setattr(
        preparation_service.languages_manager,
        "apply_confirmed_single_audio_language",
        no_op,
    )
    monkeypatch.setattr(
        preparation_service.languages_manager, "process_desc_language", no_op
    )

    result = asyncio.run(prep.gather_prep(meta, "cli"))
    cover.assert_awaited_once()
    saved = tmp_path / "tmp" / "book" / "meta.json"
    assert saved.is_file()
    assert result is meta


def test_capture_early_screenshots_dvd_xxx_and_cancellation(
    tmp_path: Path,
) -> None:
    prep = _prep()
    dvd = AsyncMock()
    xxx = AsyncMock()
    prep.takescreens_manager = SimpleNamespace(
        dvd_screenshots=dvd, xxx_contact_sheets=xxx
    )

    dvd_meta = Meta(
        base_dir=str(tmp_path),
        uuid="dvd",
        category="MOVIE",
        is_disc="DVD",
        screens=2,
    )
    asyncio.run(prep._capture_early_screenshots(dvd_meta, "dvd", "dvd", {}))
    dvd.assert_awaited_once()

    xxx_meta = Meta(
        base_dir=str(tmp_path),
        uuid="xxx",
        category="XXX",
        screens=2,
        filelist=["one.mp4"],
    )
    asyncio.run(prep._capture_early_screenshots(xxx_meta, "xxx", "xxx", {}))
    xxx.assert_awaited_once()

    dvd.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            prep._capture_early_screenshots(dvd_meta, "dvd", "dvd", {})
        )
