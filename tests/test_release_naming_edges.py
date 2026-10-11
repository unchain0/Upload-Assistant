from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import release_naming_service
from src.services.release_naming_service import NameManager


def _manager(config: dict[str, Any] | None = None) -> NameManager:
    return NameManager(config or {"DEFAULT": {}, "TRACKERS": {}})


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "category": "MOVIE",
        "type": "ENCODE",
        "title": "Example Movie",
        "aka": "Alt Title",
        "year": 2025,
        "manual_year": 0,
        "resolution": "1080p",
        "audio": "DTS 5.1",
        "service": "NF",
        "season": "S01",
        "episode": "E01",
        "part": "",
        "repack": "",
        "three_d": "",
        "tag": "-GROUP",
        "source": "BluRay",
        "uhd": "",
        "hdr": "HDR10",
        "webdv": False,
        "video_codec": "AVC",
        "video_encode": "x264",
        "region": "A",
        "dvd_size": "DVD9",
        "edition": "",
        "is_disc": "",
        "trackers": [],
        "unattended": False,
        "unattended_confirm": False,
        "manual_episode_title": "",
        "daily_episode_title": "",
        "manual_date": None,
        "manual_name": None,
        "no_season": False,
        "no_year": False,
        "no_aka": False,
        "debug": False,
    }
    state.update(values)
    return Meta(state)


@pytest.mark.parametrize(
    ("category", "release_type", "disc", "source"),
    [
        ("MOVIE", "DISC", "BDMV", "BluRay"),
        ("MOVIE", "DISC", "DVD", "DVD"),
        ("MOVIE", "DISC", "HDDVD", "HDDVD"),
        ("MOVIE", "REMUX", "", "BluRay"),
        ("MOVIE", "REMUX", "", "DVD"),
        ("MOVIE", "ENCODE", "", "BluRay"),
        ("MOVIE", "WEBDL", "", "Web"),
        ("MOVIE", "WEBRIP", "", "Web"),
        ("MOVIE", "HDTV", "", "HDTV"),
        ("MOVIE", "DVDRIP", "", "DVD"),
        ("TV", "DISC", "BDMV", "BluRay"),
        ("TV", "DISC", "DVD", "DVD"),
        ("TV", "DISC", "HDDVD", "HDDVD"),
        ("TV", "REMUX", "", "BluRay"),
        ("TV", "REMUX", "", "DVD"),
        ("TV", "ENCODE", "", "BluRay"),
        ("TV", "WEBDL", "", "Web"),
        ("TV", "WEBRIP", "", "Web"),
        ("TV", "HDTV", "", "HDTV"),
        ("TV", "DVDRIP", "", "DVD"),
    ],
)
def test_get_name_covers_movie_and_tv_release_shapes(
    category: str, release_type: str, disc: str, source: str
) -> None:
    manager = _manager()
    meta = _meta(
        category=category, type=release_type, is_disc=disc, source=source
    )

    name_notag, name, clean, potential_missing = asyncio.run(
        manager.get_name(meta)
    )

    assert "Example Movie" in name_notag
    assert name.endswith("-GROUP")
    assert clean
    assert isinstance(potential_missing, list)


def test_get_name_manual_xxx_book_game_music_podcast_and_flags() -> None:
    manager = _manager()

    manual = _meta(manual_name="  My Manual Name  ", tag="-TAG")
    assert asyncio.run(manager.get_name(manual))[1] == "My Manual Name"

    xxx = _meta(category="XXX", scene_name="Scene.Release-GRP", tag="-GRP")
    assert asyncio.run(manager.get_name(xxx))[1] == "Scene Release-GRP"

    book = _meta(
        category="BOOK",
        title="Book",
        author="Author",
        type="EPUB",
        source="RETAIL",
        tag="",
    )
    assert "eBOOK" in asyncio.run(manager.get_name(book))[0]

    game = _meta(
        category="GAME",
        title="Game",
        platform="PS5",
        game_version="1.2",
        languages={"English": {}, "French": {}},
        manual_multi=True,
        tag="",
    )
    assert "MULTI2" in asyncio.run(manager.get_name(game))[0]

    music = _meta(
        category="MUSIC",
        artist="Artist",
        title="Album",
        source="WEB",
        format="FLAC",
        tag="",
        music_release={
            "fields": {
                "artist": {"value": "Artist"},
                "album": {"value": "Album"},
                "release_year": {"value": 2025},
                "media": {"value": "WEB"},
            },
            "tracks": [
                {"codec": "FLAC", "bit_depth": 24, "sample_rate": 96000}
            ],
        },
    )
    assert (
        asyncio.run(manager.get_name(music))[0]
        == "Artist - Album 2025 WEB FLAC 24-bit 96 kHz"
    )

    podcast = _meta(
        category="PODCAST", podcast_title="Podcast Episode", tag=""
    )
    assert asyncio.run(manager.get_name(podcast))[0] == "Podcast Episode"

    tv = _meta(
        category="TV",
        type="WEBDL",
        manual_date="2026-08-18",
        manual_episode_title="Manual Episode",
        daily_episode_title="Daily",
        no_year=True,
        no_season=True,
        no_aka=True,
        resolution="OTHER",
        edition="Hybrid Director's Cut",
        webdv=True,
        debug=True,
    )
    result = asyncio.run(manager.get_name(tv))[0]
    assert (
        "S01" not in result
        and "Alt Title" not in result
        and "2025" not in result
    )
    assert "Director's Cut" in result and result.count("Hybrid") == 1

    class BadName:
        def split(self):
            raise RuntimeError("bad name")

    broken = _meta(category="PODCAST", podcast_title="")
    broken.name = BadName()  # type: ignore[assignment]
    broken.title = BadName()  # type: ignore[assignment]
    with pytest.raises(OperationAbortedError):
        asyncio.run(manager.get_name(broken))


def test_disc_requirements_update_and_remove_trackers() -> None:
    manager = _manager()
    manager.missing_disc_info = AsyncMock(
        return_value=("B", "Criterion", ["ULCX"])
    )  # type: ignore[method-assign]
    meta = _meta(
        is_disc="BDMV",
        type="DISC",
        trackers=["ULCX", "SHAREISLAND"],
        unattended=True,
    )

    asyncio.run(manager.get_name(meta))

    assert meta.region == "B"
    assert meta.distributor == "Criterion"
    assert meta.trackers == ["SHAREISLAND"]

    manager.missing_disc_info = AsyncMock(
        return_value=("SKIPPED", "SKIPPED", [])
    )  # type: ignore[method-assign]
    meta = _meta(
        is_disc="BDMV",
        type="DISC",
        trackers=["ULCX"],
        region="A",
        distributor="Existing",
    )
    asyncio.run(manager.get_name(meta))
    assert meta.region == "A" and meta.distributor == "Existing"


def test_extract_book_name_all_subtypes_languages_sources_and_editions() -> (
    None
):
    manager = _manager()
    audiobook = _meta(
        category="BOOK",
        audiobook=True,
        author="Author",
        title="Author - Story",
        manual_edition="Deluxe",
        year=2020,
        book_language="French",
        type="MP3",
    )
    assert (
        manager.extract_book_name(audiobook)
        == "Author - Story Deluxe 2020 FRENCH AUDIOBOOK"
    )

    no_author = _meta(
        category="BOOK",
        audiobook=True,
        author="",
        publisher="",
        title="Story",
        year=2020,
        type="MP3",
    )
    assert manager.extract_book_name(no_author).startswith("Story")

    comic = _meta(
        category="BOOK",
        comic=True,
        title="Comic",
        manual_season="2",
        manual_episode="3",
        type="PDF",
        source="",
        uuid="scan",
    )
    comic_name = manager.extract_book_name(comic)
    assert (
        "Vol 2" in comic_name
        and "No 3" in comic_name
        and "COMiC" in comic_name
        and "SCAN" in comic_name
    )

    manga = _meta(
        category="BOOK",
        manga=True,
        title="Manga",
        season="4",
        type="EPUB",
        source="HYBRID",
    )
    assert "MANGA" in manager.extract_book_name(
        manga
    ) and "HYBRiD" in manager.extract_book_name(manga)

    magazine = _meta(
        category="BOOK",
        magazine=True,
        title="Magazine",
        episode="5",
        type="CBZ",
        manual_source="RETAIL",
    )
    assert "MAGAZiNE" in manager.extract_book_name(
        magazine
    ) and "RETAiL" in manager.extract_book_name(magazine)

    newspaper = _meta(
        category="BOOK",
        newspaper=True,
        title="News",
        type="PDF",
        source="invalid",
        uuid="retail",
    )
    assert manager.extract_book_name(newspaper).endswith("RETAiL eBOOK")

    default = _meta(
        category="BOOK",
        title="Publisher - Book",
        author="",
        publisher="Publisher",
        edition="Second Edition",
        type="MOBI",
        book_language_iso="por",
        source="SCAN",
    )
    assert (
        manager.extract_book_name(default)
        == "Publisher - Book Second Edition 2025 POR SCAN MOBI eBOOK"
    )

    assert manager._strip_prefix_author_or_publisher("", "Author") == ""
    assert manager._strip_prefix_author_or_publisher("Book", "") == "Book"
    assert (
        manager._strip_prefix_author_or_publisher("Author - Book", "Author")
        == "Book"
    )


def test_extract_game_name_language_platform_version_and_repack() -> None:
    manager = _manager()
    multi = _meta(
        category="GAME",
        title="Game..Name",
        edition="GOTY",
        year=2025,
        platform="PS5",
        game_version="1.2.3",
        repack="REPACK",
        languages={"English": {}, "French": {}, "German": {}},
        path="Game.MULTI.iso",
    )
    name = manager.extract_game_name(multi)
    assert (
        "Game Name" in name
        and "v1.2.3" in name
        and "MULTI3" in name
        and "PS5" in name
        and "REPACK" in name
    )

    forced = _meta(
        category="GAME",
        title="Game",
        platform="PC",
        manual_multi=True,
        languages={},
        game_version="v2",
    )
    assert manager.extract_game_name(forced) == "Game v2 2025 MULTI"

    non_english = _meta(
        category="GAME",
        title="Game",
        platform="WINDOWS",
        languages={"Spanish": {}},
    )
    assert "SPANISH" in manager.extract_game_name(non_english)

    english = _meta(
        category="GAME",
        title="Game",
        platform="WIN",
        languages={"English": {}},
    )
    assert "ENGLISH" not in manager.extract_game_name(english)


def test_music_helpers_and_lossy_name() -> None:
    manager = _manager()
    assert (
        manager._music_release_field({"fields": "bad"}, "album", "fallback")
        == "fallback"
    )
    assert (
        manager._music_release_field(
            {"fields": {"album": "bad"}}, "album", "fallback"
        )
        == "fallback"
    )
    assert manager._music_codec("OGG VORBIS") == "VORBIS"
    assert manager._music_codec("MPEG AUDIO") == "MP3"
    assert manager._music_codec("MPEG-4 AAC") == "AAC"
    assert manager._music_source("DTS CD") == "DTS-CD"
    assert manager._music_source("unknown") == "unknown"

    meta = _meta(
        category="MUSIC",
        artist="Artist",
        title="Album",
        year=2020,
        source="WEB",
        type="MP3",
        format="",
        music_release={"tracks": [{"format": "MPEG AUDIO"}]},
    )
    assert manager.extract_music_name(meta) == "Artist - Album 2020 WEB MP3"


def test_clean_filename_and_multi_replace() -> None:
    manager = _manager()
    assert asyncio.run(manager.clean_filename('a<>:"/\\|?*b')) == "a---------b"
    assert (
        asyncio.run(
            manager.multi_replace("BluRay.Movie", {"BluRay": "", ".": " "})
        ).strip()
        == "Movie"
    )


@pytest.mark.parametrize(
    ("uuid", "expected"),
    [
        (
            "Primary.2020.AKA.Secondary.2021.1080p",
            ("Primary 2020", "Secondary 2021", "2020"),
        ),
        ("Primary.AKA.12345.2020.1080p", ("Primary", "12345", None)),
        ("1917.2019.1080p.BluRay", ("1917", None, "2019")),
        ("Movie.1982.2011.1080p.BluRay", ("Movie", None, "2011")),
        ("Show.2026.08.18.1080p.WEB-DL", ("Show", None, None)),
        (
            "Movie (Secondary).2025.1080p.BluRay",
            ("Movie", "Secondary", "2025"),
        ),
        ("No.Pattern.Title", ("No Pattern Title", None, None)),
    ],
)
def test_extract_title_and_year_patterns(
    uuid: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    manager = _manager()
    assert (
        asyncio.run(manager.extract_title_and_year(Meta(uuid=uuid), uuid))
        == expected
    )


def test_extract_title_subsplease_year_start_and_year_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    monkeypatch.setattr(
        release_naming_service,
        "guessit_fn",
        lambda *_args, **_kwargs: {"title": "Anime Title"},
    )
    monkeypatch.setattr(
        release_naming_service.anitopy,
        "parse",
        lambda _value: {"anime_title": "Parsed Anime"},
    )
    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="[SubsPlease] Anime - 01 (1080p)"), "file.mkv"
        )
    ) == ("Parsed Anime", None, None)

    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="2020.Title.2021.1080p"), "2020.Title.2021.1080p.mkv"
        )
    ) == ("2020", None, "2021")

    # Force a cleaned empty title while leaving a year in the raw basename.
    meta = Meta(uuid="BluRay.2025")
    title, secondary, year = asyncio.run(
        manager.extract_title_and_year(meta, "BluRay.2025.mkv")
    )
    assert (title, secondary, year) == (None, None, "2025")


def test_missing_disc_info_prompting_and_tracker_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    manager.common.unit3d_region_ids = AsyncMock(side_effect=[0, 1])  # type: ignore[method-assign]
    manager.common.unit3d_distributor_ids = AsyncMock(side_effect=[0, 2])  # type: ignore[method-assign]
    answers = iter(("B", "Criterion"))
    monkeypatch.setattr(
        release_naming_service.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: next(answers),
    )
    meta = _meta(
        is_disc="BDMV",
        region="",
        distributor="",
        trackers=["ULCX", "SHAREISLAND"],
    )

    region, distributor, removed = asyncio.run(
        manager.missing_disc_info(meta, ["ULCX", "SHAREISLAND"])
    )

    assert region == "B" and distributor == "CRITERION" and removed == []

    manager.common.unit3d_region_ids = AsyncMock(return_value=0)  # type: ignore[method-assign]
    manager.common.unit3d_distributor_ids = AsyncMock(return_value=0)  # type: ignore[method-assign]
    unattended = _meta(
        is_disc="BDMV",
        region="",
        distributor="",
        unattended=True,
        unattended_confirm=False,
    )
    region, distributor, removed = asyncio.run(
        manager.missing_disc_info(unattended, ["ULCX", "SHAREISLAND"])
    )
    assert (
        region == "SKIPPED"
        and distributor == "SKIPPED"
        and removed == ["ULCX", "SHAREISLAND"]
    )

    non_disc = _meta(is_disc="", region="A", distributor="Criterion")
    assert asyncio.run(manager.missing_disc_info(non_disc, ["ULCX"])) == (
        "A",
        "Criterion",
        [],
    )


def test_prompt_optional_mandatory_empty_and_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    monkeypatch.setattr(
        release_naming_service.cli_ui,
        "ask_string",
        lambda prompt: "value" if "MANDATORY" in prompt else "",
    )
    assert (
        asyncio.run(manager._prompt_for_field(_meta(), "Region", True))
        == "VALUE"
    )
    assert (
        asyncio.run(manager._prompt_for_field(_meta(), "Region", False))
        == "SKIPPED"
    )
    assert (
        asyncio.run(
            manager._prompt_for_field(
                _meta(unattended=True, unattended_confirm=False),
                "Region",
                True,
            )
        )
        == "SKIPPED"
    )

    monkeypatch.setattr(
        release_naming_service.cli_ui,
        "ask_string",
        lambda _prompt: (_ for _ in ()).throw(EOFError()),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(
        release_naming_service.cleanup_manager, "cleanup", cleanup
    )
    monkeypatch.setattr(
        release_naming_service.cleanup_manager, "reset_terminal", lambda: None
    )
    with pytest.raises(OperationAbortedError):
        asyncio.run(manager._prompt_for_field(_meta(), "Region", True))
    cleanup.assert_awaited_once()


def test_remaining_name_and_title_year_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager()
    monkeypatch.setattr(
        release_naming_service.guessit_module,
        "guessit",
        lambda value, options=None: {"title": value, "options": options},
    )
    assert release_naming_service.guessit_fn("Example")["title"] == "Example"

    manual_year = _meta(manual_year=2030)
    assert "2030" in asyncio.run(manager.get_name(manual_year))[0]

    daily_title = _meta(
        category="TV",
        type="WEBDL",
        manual_episode_title="",
        daily_episode_title="Daily Episode",
    )
    assert "Daily Episode" in asyncio.run(manager.get_name(daily_title))[0]

    edition = _meta(
        category="BOOK",
        title="Book",
        author="Author",
        edition="Collector",
        type="EPUB",
        source="RETAIL",
    )
    assert "Collector Edition" in manager.extract_book_name(edition)

    hybrid = _meta(
        category="BOOK",
        title="Book",
        author="Author",
        type="EPUB",
        source="",
        uuid="Book.Hybrid",
    )
    assert "HYBRiD" in manager.extract_book_name(hybrid)

    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Primary.AKA.Secondary.2021.1080p"),
            "Primary.AKA.Secondary.2021.1080p",
        )
    ) == (
        "Primary",
        "Secondary",
        "2021",
    )
    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Primary AKA Secondary"), "Primary AKA Secondary"
        )
    ) == (
        "Primary",
        "Secondary",
        None,
    )

    title, _, year = asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Movie.1982.2011.S01E02.1080p.BluRay.mkv"),
            "Movie.1982.2011.S01E02.1080p.BluRay.mkv",
        )
    )
    assert title == "Movie" and year == "2011"

    title, _, _ = asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Show.S01E02.1080p.WEB-DL.mkv"),
            "Show.S01E02.1080p.WEB-DL.mkv",
        )
    )
    assert title == "Show"

    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Movie (Secondary.2025.1080p"),
            "Movie (Secondary.2025.1080p.mkv",
        )
    ) == ("Movie", "Secondary", "2025")

    assert asyncio.run(
        manager.extract_title_and_year(Meta(uuid="BluRay"), "BluRay.mkv")
    ) == (None, None, None)


def test_title_year_season_only_boundaries() -> None:
    manager = _manager()
    title, _, year = asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Movie.1982.2011.S01.1080p.BluRay.mkv"),
            "Movie.1982.2011.S01.1080p.BluRay.mkv",
        )
    )
    assert title == "Movie" and year == "2011"

    title, _, year = asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="Show.S01.1080p.WEB-DL.mkv"),
            "Show.S01.1080p.WEB-DL.mkv",
        )
    )
    assert title == "Show" and year is None
