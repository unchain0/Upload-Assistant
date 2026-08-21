from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import release_naming_service as naming
from src.services.release_naming_service import NameManager


def _base_meta(
    category: str = "MOVIE", release_type: str = "ENCODE", **values: object
) -> Meta:
    state: dict[str, object] = {
        "category": category,
        "type": release_type,
        "title": "Example Title",
        "aka": "Alt Title",
        "year": 2025,
        "resolution": "1080p",
        "audio": "DDP 5.1",
        "service": "NF",
        "season": "S01",
        "episode": "E02",
        "part": "Part 1",
        "repack": "REPACK",
        "three_d": "3D",
        "tag": "-GROUP",
        "source": "BluRay",
        "uhd": "UHD",
        "hdr": "HDR10",
        "video_codec": "AVC",
        "video_encode": "x264",
        "region": "US",
        "dvd_size": "DVD9",
        "edition": "Director's Cut",
        "webdv": True,
        "trackers": [],
    }
    state.update(values)
    return Meta(state)


def _name(
    meta: Meta, monkeypatch: pytest.MonkeyPatch | None = None
) -> tuple[str, str, str, list[str]]:
    manager = NameManager({})
    if monkeypatch is not None:
        monkeypatch.setattr(
            manager,
            "missing_disc_info",
            AsyncMock(return_value=("US", "Criterion", [])),
        )
    return asyncio.run(manager.get_name(meta))


@pytest.mark.parametrize(
    ("release_type", "disc_type", "source", "marker"),
    [
        ("DISC", "BDMV", "BluRay", "UHD"),
        ("DISC", "DVD", "DVD", "DVD9"),
        ("DISC", "HDDVD", "HDDVD", "HDDVD"),
        ("REMUX", "", "BluRay", "REMUX"),
        ("REMUX", "", "PAL DVD", "REMUX"),
        ("ENCODE", "", "BluRay", "x264"),
        ("WEBDL", "", "Web", "WEB-DL"),
        ("WEBRIP", "", "Web", "WEBRip"),
        ("HDTV", "", "HDTV", "HDTV"),
        ("DVDRIP", "", "DVD", "DVDRip"),
    ],
)
def test_movie_name_matrix(
    release_type: str, disc_type: str, source: str, marker: str
) -> None:
    meta = _base_meta("MOVIE", release_type, is_disc=disc_type, source=source)
    name_notag, name, clean, missing = _name(meta)
    assert marker.casefold() in name_notag.casefold()
    assert name.endswith("-GROUP")
    assert clean
    assert isinstance(missing, list)


@pytest.mark.parametrize(
    ("release_type", "disc_type", "source", "marker"),
    [
        ("DISC", "BDMV", "BluRay", "S01E02"),
        ("DISC", "DVD", "DVD", "DVD9"),
        ("DISC", "HDDVD", "HDDVD", "HDDVD"),
        ("REMUX", "", "BluRay", "REMUX"),
        ("REMUX", "", "NTSC DVD", "REMUX"),
        ("ENCODE", "", "BluRay", "x264"),
        ("WEBDL", "", "Web", "WEB-DL"),
        ("WEBRIP", "", "Web", "WEBRip"),
        ("HDTV", "", "HDTV", "HDTV"),
        ("DVDRIP", "", "DVD", "DVDRip"),
    ],
)
def test_tv_name_matrix(
    release_type: str, disc_type: str, source: str, marker: str
) -> None:
    meta = _base_meta(
        "TV",
        release_type,
        is_disc=disc_type,
        source=source,
        search_year=2025,
        manual_episode_title="Episode Title",
    )
    name_notag, name, clean, missing = _name(meta)
    assert marker.casefold() in name_notag.casefold()
    assert name.endswith("-GROUP")
    assert clean
    assert isinstance(missing, list)


def test_get_name_manual_xxx_podcast_flags_and_invalid_name() -> None:
    manual = _base_meta(manual_name="  Manual Release  ")
    assert _name(manual)[0:2] == ("Manual Release", "Manual Release")

    xxx = _base_meta(
        "XXX", "WEBDL", scene_name="Studio.Scene-GROUP", tag="-GROUP"
    )
    name_notag, name, *_ = _name(xxx)
    assert name_notag == "Studio Scene-GROUP" and name == name_notag

    xxx = _base_meta("XXX", "WEBDL", scene_name="Studio.Scene", tag="-GROUP")
    assert _name(xxx)[1].endswith("-GROUP")

    podcast = _base_meta("PODCAST", "MP3", podcast_title="Podcast Episode")
    assert _name(podcast)[0] == "Podcast Episode"
    podcast = _base_meta(
        "PODCAST", "MP3", podcast_title="", name="Existing Name"
    )
    assert _name(podcast)[0] == "Existing Name"

    other_resolution = _base_meta(
        "MOVIE",
        "ENCODE",
        resolution="OTHER",
        edition="Hybrid Director's Cut",
        webdv=False,
    )
    generated = _name(other_resolution)[0]
    assert "Hybrid" not in generated and "Director's Cut" in generated

    game_meta = Meta(category="GAME", title="Game", platform="PS5", year=2025)
    assert _name(game_meta)[0] == "Game 2025 PS5"

    flags = _base_meta(
        "TV",
        "WEBDL",
        search_year="",
        manual_year=2030,
        manual_date=True,
        no_season=True,
        no_year=True,
        no_aka=True,
        manual_episode_title="",
        daily_episode_title="Daily Title",
        debug=True,
    )
    generated = _name(flags)[0]
    assert (
        "S01" not in generated
        and "2030" not in generated
        and "Alt Title" not in generated
    )
    assert "Daily Title" in generated

    class BadName:
        def split(self):
            raise RuntimeError("bad name")

    broken = _base_meta("PODCAST", "MP3", podcast_title=BadName())  # type: ignore[arg-type]
    with pytest.raises(OperationAbortedError):
        _name(broken)


def test_get_name_disc_requirements_remove_trackers_and_update_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NameManager({})
    monkeypatch.setattr(
        manager,
        "missing_disc_info",
        AsyncMock(return_value=("GB", "Criterion", ["ULCX"])),
    )
    meta = _base_meta(
        "MOVIE",
        "DISC",
        is_disc="BDMV",
        trackers=["ULCX", "SHAREISLAND"],
        unattended=True,
    )
    asyncio.run(manager.get_name(meta))
    assert meta.trackers == ["SHAREISLAND"]
    assert meta.region == "GB" and meta.distributor == "Criterion"

    monkeypatch.setattr(
        manager,
        "missing_disc_info",
        AsyncMock(return_value=("SKIPPED", "SKIPPED", [])),
    )
    original = _base_meta(
        "MOVIE",
        "DISC",
        is_disc="BDMV",
        trackers=["ULCX"],
        region="US",
        distributor="Original",
    )
    asyncio.run(manager.get_name(original))
    assert original.region == "US" and original.distributor == "Original"


def test_book_name_matrix_and_source_normalization() -> None:
    manager = NameManager({})
    base = {
        "author": "Author",
        "publisher": "Publisher",
        "title": "Author - Book",
        "year": 2025,
        "type": "EPUB",
        "book_language": "Portuguese",
        "season": "2",
        "episode": "3",
    }

    audiobook = Meta(**base, audiobook=True, manual_edition="Anniversary")
    value = manager.extract_book_name(audiobook)
    assert value == "Author - Book Anniversary 2025 PORTUGUESE AUDIOBOOK"

    no_author = Meta(title="Book", year=2025, type="EPUB", audiobook=True)
    assert manager.extract_book_name(no_author) == "Book 2025 AUDIOBOOK"

    generic_edition = Meta(**base, manual_edition="Anniversary")
    assert "Anniversary Edition" in manager.extract_book_name(generic_edition)

    comic = Meta(**base, comic=True, source="SCAN")
    assert "Vol 2 No 3" in manager.extract_book_name(
        comic
    ) and "COMiC eBOOK" in manager.extract_book_name(comic)
    manga = Meta(**base, manga=True, source="HYBRID")
    assert "Vol 2" in manager.extract_book_name(
        manga
    ) and "MANGA eBOOK" in manager.extract_book_name(manga)
    magazine = Meta(**base, magazine=True, source="RETAIL")
    assert "No 3" in manager.extract_book_name(
        magazine
    ) and "MAGAZiNE eBOOK" in manager.extract_book_name(magazine)
    newspaper = Meta(**base, newspaper=True, source="unknown")
    assert "eBOOK" in manager.extract_book_name(newspaper)

    generic = Meta(
        author="",
        publisher="Publisher",
        title="Publisher - Book",
        year=2025,
        type="PDF",
        uuid="retail-release",
        book_language_iso="eng",
    )
    assert (
        manager.extract_book_name(generic)
        == "Publisher - Book 2025 RETAiL eBOOK"
    )
    scanned = Meta(
        author="Author", title="Book", type="PDF", uuid="scan-release"
    )
    assert "SCAN" in manager.extract_book_name(scanned)
    hybrid = Meta(
        author="Author", title="Book", type="CBZ", uuid="hybrid-release"
    )
    assert "HYBRiD" in manager.extract_book_name(hybrid)
    manual_scan = Meta(
        author="Author", title="Book", type="CBZ", manual_source="SCAN"
    )
    assert "SCAN" in manager.extract_book_name(manual_scan)
    assert manager._strip_prefix_author_or_publisher(" Book ", "") == "Book"


def test_game_name_languages_versions_platforms_and_repack() -> None:
    manager = NameManager({})
    multi = Meta(
        title="Game",
        edition="GOTY",
        year=2025,
        platform="PS5",
        game_version="1.2",
        repack="REPACK",
        languages={"English": ["Audio"], "French": ["Audio"]},
        path="Game.MULTI.PS5",
    )
    value = manager.extract_game_name(multi)
    assert "GOTY v1.2 2025 MULTI2 PS5 REPACK" in value

    forced = Meta(title="Game", platform="PC", manual_multi=True, languages={})
    assert manager.extract_game_name(forced) == "Game MULTI"
    single_non_english = Meta(
        title="Game", platform="WINDOWS", languages={"French": []}
    )
    assert manager.extract_game_name(single_non_english) == "Game FRENCH"
    single_english = Meta(
        title="Game", platform="WIN", languages={"English": []}
    )
    assert manager.extract_game_name(single_english) == "Game"
    dotted = Meta(title="Game...Title", platform="LINUX")
    assert "Game Title" in manager.extract_game_name(dotted)


def test_music_helpers_and_naming_edges() -> None:
    manager = NameManager({})
    assert manager._music_release_field({}, "artist", "fallback") == "fallback"
    assert (
        manager._music_release_field({"fields": []}, "artist", "fallback")
        == "fallback"
    )
    assert (
        manager._music_release_field(
            {"fields": {"artist": "bad"}}, "artist", "fallback"
        )
        == "fallback"
    )
    assert (
        manager._music_release_field(
            {"fields": {"artist": {"value": "Artist"}}}, "artist"
        )
        == "Artist"
    )
    for raw, expected in (
        ("OGG VORBIS", "VORBIS"),
        ("OGG", "VORBIS"),
        ("MPEG AUDIO", "MP3"),
        ("MPEG-4 AAC", "AAC"),
        ("M4A", "AAC"),
        ("flac", "FLAC"),
    ):
        assert manager._music_codec(raw) == expected
    for raw, expected in (
        ("cd", "CD"),
        ("DTS CD", "DTS-CD"),
        ("8 track", "8-Track"),
        ("vinyl", "Vinyl"),
        ("web", "WEB"),
        ("cassette", "Cassette"),
        ("Other", "Other"),
    ):
        assert manager._music_source(raw) == expected

    release = {
        "fields": {
            "artist": {"value": "Artist"},
            "album": {"value": "Album"},
            "release_year": {"value": 2025},
            "media": {"value": "CD"},
        },
        "tracks": [{"codec": "FLAC", "bit_depth": 24, "sample_rate": 96000}],
    }
    assert (
        manager.extract_music_name(Meta(music_release=release))
        == "Artist - Album 2025 CD FLAC 24-bit 96 kHz"
    )
    fallback = Meta(
        artist="Artist",
        title="Album",
        year=2025,
        source="WEB",
        format="MP3",
        music_release={"tracks": "bad"},
    )
    assert (
        manager.extract_music_name(fallback) == "Artist - Album 2025 WEB MP3"
    )
    alac = {
        "fields": {
            "artist": {"value": "A"},
            "album": {"value": "B"},
            "nfo_bit_depth": {"value": 16},
            "nfo_sample_rate": {"value": 44100},
        },
        "tracks": [{"format": "ALAC"}],
    }
    assert "16-bit 44.1 kHz" in manager.extract_music_name(
        Meta(music_release=alac)
    )


def test_clean_filename_multi_replace_and_extract_title_year_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NameManager({})
    assert (
        asyncio.run(manager.clean_filename('<Bad>:"/\\|?*')) == "-Bad--------"
    )
    assert (
        asyncio.run(
            manager.multi_replace(
                "BluRay WEB", {"BluRay": "Disc", "WEB": "Stream"}
            )
        )
        == "Disc Stream"
    )

    cases = [
        (
            "Primary.2020 AKA Secondary.2021.1080p.mkv",
            ("Primary 2020", "Secondary 2021 1080p", "2020"),
        ),
        ("Primary AKA 12345 BluRay", ("Primary", "12345", None)),
        ("Primary AKA Secondary 2021 1080p", ("Primary", "Secondary", "2021")),
        ("2020.Title.2021.1080p.mkv", ("2020", None, "2021")),
        ("1917.2019.1080p.BluRay.mkv", ("1917", None, "2019")),
        ("1917.2019.S01E02.1080p.BluRay.mkv", ("1917", None, "2019")),
        ("1917.2019.S01.1080p.BluRay.mkv", ("1917", None, "2019")),
        ("Some.Movie.1982.2011.1080p.mkv", ("Some Movie", None, "2011")),
        (
            "Some.Movie.1982.2011.S01E02.1080p.BluRay.mkv",
            ("Some Movie", None, "2011"),
        ),
        (
            "Some.Movie.1982.2011.S01.1080p.BluRay.mkv",
            ("Some Movie", None, "2011"),
        ),
        ("Movie.(Alternate).2022.1080p.mkv", ("Movie", "Alternate", "2022")),
        ("Movie.2024.01.02.1080p.mkv", ("Movie", None, None)),
        ("Movie.S01E02.1080p.WEBDL.mkv", ("Movie", None, None)),
        ("Movie.S01.1080p.WEBDL.mkv", ("Movie", None, None)),
        ("Movie.(Alternate.2022.1080p.mkv", ("Movie", "Alternate", "2022")),
        ("Movie.Without.Markers.mkv", ("Movie Without Markers", None, None)),
    ]
    for filename, expected in cases:
        meta = Meta(uuid=filename)
        result = asyncio.run(manager.extract_title_and_year(meta, filename))
        assert result == expected

    monkeypatch.setattr(
        naming.guessit_module,
        "guessit",
        lambda *_args, **_kwargs: {"title": "Anime Title"},
    )
    monkeypatch.setattr(
        naming.anitopy, "parse", lambda _value: {"anime_title": "Parsed Anime"}
    )
    assert asyncio.run(
        manager.extract_title_and_year(
            Meta(uuid="[SubsPlease] Anime - 01"), "file.mkv"
        )
    ) == ("Parsed Anime", None, None)

    monkeypatch.setattr(naming.anitopy, "parse", lambda _value: {})
    assert (
        asyncio.run(
            manager.extract_title_and_year(
                Meta(uuid="[SubsPlease] Anime - 01"), "2022.mkv"
            )
        )[2]
        is None
    )
    assert asyncio.run(
        manager.extract_title_and_year(Meta(uuid=""), "2022.mkv")
    ) == (None, None, "2022")
    assert asyncio.run(
        manager.extract_title_and_year(Meta(uuid=""), "unknown.mkv")
    ) == (None, None, None)


def test_missing_disc_info_and_prompt_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NameManager({})
    manager.common.unit3d_distributor_ids = AsyncMock(return_value=0)
    manager.common.unit3d_region_ids = AsyncMock(return_value=0)

    unattended = Meta(
        is_disc="BDMV",
        unattended=True,
        unattended_confirm=False,
        region="",
        distributor="",
    )
    region, distributor, removed = asyncio.run(
        manager.missing_disc_info(unattended, ["ULCX", "SHAREISLAND"])
    )
    assert (region, distributor) == ("SKIPPED", "SKIPPED")
    assert removed == ["ULCX", "SHAREISLAND"]

    answers = iter(("us", "criterion"))
    monkeypatch.setattr(
        naming.cli_ui, "ask_string", lambda _prompt: next(answers)
    )
    interactive = Meta(
        is_disc="BDMV", unattended=False, region="", distributor=""
    )
    region, distributor, removed = asyncio.run(
        manager.missing_disc_info(interactive, ["ULCX"])
    )
    assert region == "US" and distributor == "CRITERION" and removed == []

    existing = Meta(is_disc="BDMV", region="US", distributor="Criterion")
    manager.common.unit3d_distributor_ids = AsyncMock(return_value=1)
    manager.common.unit3d_region_ids = AsyncMock(return_value=1)
    assert asyncio.run(manager.missing_disc_info(existing, ["ULCX"])) == (
        "US",
        "Criterion",
        [],
    )

    monkeypatch.setattr(naming.cli_ui, "ask_string", lambda _prompt: "")
    assert (
        asyncio.run(manager._prompt_for_field(Meta(), "Region", False))
        == "SKIPPED"
    )

    monkeypatch.setattr(
        naming.cli_ui,
        "ask_string",
        lambda _prompt: (_ for _ in ()).throw(EOFError()),
    )
    monkeypatch.setattr(naming.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(naming.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError):
        asyncio.run(manager._prompt_for_field(Meta(), "Region", True))
