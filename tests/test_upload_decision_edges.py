from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import upload_decision_service as decisions
from src.services.upload_decision_service import UploadHelper


def _meta(tmp_path: Path, category: str = "MOVIE", **values: object) -> Meta:
    media = tmp_path / "Release.2026.1080p.WEB-DL.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(media),
        "name": "Release 2026 1080p WEB-DL-GROUP",
        "title": "Release",
        "year": 2026,
        "category": category,
        "overview": "An overview long enough to be displayed in the confirmation output.",
        "overview_meta": "Episode overview long enough to be displayed in the confirmation output.",
        "genres": ["Drama", "Action"],
        "demographic": "Adult",
        "tmdb_id": 123,
        "imdb_id": 456,
        "imdb": "0000456",
        "tvdb_id": 789,
        "tvmaze_id": 321,
        "mal_id": 654,
        "resolution": "1080p",
        "source": "WEB",
        "type": "WEBDL",
        "tag": "-GROUP",
        "edition": "Director's Cut",
        "region": "US",
        "distributor": "Criterion",
        "freeleech": 25,
        "trackers": [],
        "unattended": True,
        "unattended_confirm": False,
        "debug": False,
        "personalrelease": False,
        "is_disc": "",
        "keep_folder": False,
        "isdir": False,
        "original_imdb": 456,
        "original_tmdb": 123,
        "original_tvdb": 789,
        "original_tvmaze": 321,
        "original_mal": 654,
        "original_category": category,
        "matched_tracker": "TRACKER",
        "dupe": False,
        "ask_dupe": False,
        "were_trumping": False,
        "we_asked": False,
        "filename_match": "",
        "file_count_match": 0,
        "size_match": "",
        "season_pack_exists": False,
        "season_pack_contains_episode": False,
        "season_pack_name": "",
        "season_pack_link": "",
        "tv_pack": False,
        "auto_episode_title": "",
    }
    state.update(values)
    return Meta(state)


def _field(value: object, source: str = "file_tag") -> dict[str, object]:
    return {"value": value, "source": source, "confidence": 1.0}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (123, 123),
        (123.9, 123),
        ("", None),
        ("123", 123),
        ("1,024.50 MB", int(1024.50 * 1024**2)),
        ("1,024 MB", 1024 * 1024**2),
        ("1,544 TiB", int(1.544 * 1024**4)),
        ("389,61 MiB", int(389.61 * 1024**2)),
        ("1,2345 MB", int(1.2345 * 1024**2)),
        ("1.5 GB", int(1.5 * 1024**3)),
        ("1.5 XB", 1),
        ("1.5", 1),
        ("1.2.3 MB", None),
        ("invalid", None),
    ],
)
def test_parse_size_to_bytes_all_formats(raw: object, expected: int | None) -> None:
    assert decisions.parse_size_to_bytes(raw) == expected


def test_hsl_and_difference_color_cover_every_sector_and_clamp() -> None:
    for hue in (0, 59, 60, 119, 120, 179, 180, 239, 240, 299, 300, 360):
        rgb = decisions.hsl_to_rgb(float(hue), 0.9, 0.6)
        assert len(rgb) == 3
        assert all(0 <= component <= 255 for component in rgb)
    assert decisions.get_color_for_diff(-1.0) == decisions.get_color_for_diff(0.0)
    assert decisions.get_color_for_diff(2.0) == decisions.get_color_for_diff(0.5)
    assert len(decisions.get_color_for_diff(0.25)) == 6


def test_music_confirmation_lines_rich_metadata_conflicts_sidecars_and_artwork(tmp_path: Path) -> None:
    artwork = tmp_path / "cover.png"
    artwork.write_bytes(b"image")
    tracks = [
        {"format": "M4A", "codec": "AAC", "bit_depth": 16, "sample_rate": 44100, "channels": 2, "bitrate": 256000},
        {"format": "FLAC", "codec": "FLAC", "bit_depth": 24, "sample_rate": 96000, "channels": 1, "bitrate": 1000000},
        {"format": "MP3", "codec": "MP3", "bit_depth": 16, "sample_rate": 48000, "channels": 6, "bitrate": 320000},
    ]
    release = {
        "fields": {
            "artists": _field(["Artist", "Guest"], "external"),
            "album": _field("Album", "directory"),
            "year": _field(2020, "file_tag"),
            "media": _field("WEB", "inferred"),
            "release_type": _field("Album", "user"),
            "format": _field("M4A", "tracker"),
            "disc_count": _field(2),
            "track_count": _field(3),
            "edition": _field("Deluxe", "auxiliary"),
            "edition_year": _field(2024),
            "release_year": _field(2023),
            "retail_date": _field("2023-05-01"),
            "release_label": _field("Label"),
            "release_catalogue_number": _field("CAT-001"),
            "genres": _field(["Rock", "Pop"]),
        },
        "tracks": tracks,
        "auxiliary": {
            "logs": ["a.log"],
            "cues": ["a.cue", "b.cue"],
            "nfos": ["a.nfo"],
            "playlists": ["a.m3u"],
            "sfvs": ["a.sfv"],
            "artwork": ["cover.png"],
            "scans": ["one", "two"],
        },
        "conflicts": {f"field_{index}": ["a", "b"] for index in range(7)},
        "warnings": ["warning one", "warning two", "warning three", "warning four"],
    }
    meta = _meta(tmp_path, "MUSIC", music_release=release, artwork_path=str(artwork), debug=True)
    lines = decisions._music_confirmation_lines(meta, "MISSING")
    rendered = "\n".join(f"{item[0]}: {item[1]}" if isinstance(item, tuple) else item for item in lines)
    assert "Artist & Guest" in rendered
    assert "3 variants" in rendered
    assert "2 cues" in rendered
    assert "Metadata conflicts" in rendered and "(+2)" in rendered
    assert "warning one; warning two; warning three (+1 more)" in rendered
    assert "local/embedded artwork available; host upload skipped in debug" in rendered
    assert "This Release" in rendered and "Edition" in rendered and "Genre" in rendered


def test_music_confirmation_lines_public_missing_and_invalid_structures(tmp_path: Path) -> None:
    public = _meta(
        tmp_path,
        "MUSIC",
        artist="Fallback Artist",
        title="Fallback Album",
        year=2024,
        source="CD",
        format="FLAC",
        artwork_url="https://images.invalid/cover.jpg",
        music_release={"fields": [], "tracks": {}, "auxiliary": [], "warnings": {}, "conflicts": []},
    )
    rendered = str(decisions._music_confirmation_lines(public, "MISSING"))
    assert "public URL supplied" in rendered
    assert "Fallback Artist" in rendered

    missing = _meta(tmp_path, "MUSIC", artwork_url="ftp://invalid", artwork_path="", music_release="invalid")
    rendered = str(decisions._music_confirmation_lines(missing, "MISSING"))
    assert "not found (optional for Orpheus)" in rendered


def test_upload_helper_constructor_rejects_invalid_default() -> None:
    with pytest.raises(ValueError, match="DEFAULT"):
        UploadHelper({"DEFAULT": "invalid"})  # type: ignore[dict-item]
    helper = UploadHelper({})
    assert helper.default_config == {}


class _Tracker:
    prefers_repack: ClassVar[bool] = False
    torrent_url: ClassVar[str] = "https://tracker.invalid/torrents/"
    rename: ClassVar[object] = "Tracker Release"
    fail_name: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def get_name(self, _meta: Meta) -> object:
        if self.fail_name:
            raise RuntimeError("rename failed")
        return self.rename


def _helper(monkeypatch: pytest.MonkeyPatch, tracker: str = "TEST", tracker_class: type[_Tracker] = _Tracker) -> UploadHelper:
    helper = UploadHelper({"DEFAULT": {"show_dupe_size_diff": True, "embed_dupe_links": True}})
    mapping = dict(helper.tracker_class_map)
    mapping[tracker] = tracker_class
    monkeypatch.setattr(helper, "tracker_class_map", mapping)
    return helper


def _dupe(name: str = "Existing Release", **values: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name,
        "link": "https://tracker.invalid/torrents/1",
        "download": "https://tracker.invalid/download/1",
        "size": 100,
        "id": 1,
        "files": ["release.mkv"],
        "file_count": 1,
        "trumpable": False,
    }
    data.update(values)
    return data


def test_dupe_check_no_dupes_rename_failure_and_unattended_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    meta = _meta(tmp_path, source_size=100)
    assert asyncio.run(helper.dupe_check([], meta, "TEST")) == (False, meta)

    _Tracker.fail_name = True
    try:
        blocked = _meta(tmp_path, source_size=100, dupe=False, unattended=True)
        assert asyncio.run(helper.dupe_check([_dupe()], blocked, "TEST"))[0] is True
        allowed = _meta(tmp_path, source_size=100, dupe=True, unattended=True)
        assert asyncio.run(helper.dupe_check([_dupe()], allowed, "TEST"))[0] is False
    finally:
        _Tracker.fail_name = False


def test_dupe_rendering_size_links_deduplication_and_same_name_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    meta = _meta(tmp_path, source_size=200, unattended=False, dupe=True)
    candidates = [
        _dupe(meta.name, size="100 B"),
        _dupe("Duplicate Link", size="300 B"),
        _dupe("Duplicate Link Again", link="https://tracker.invalid/torrents/1", size=None),
        "plain entry",
    ]
    skip, result = asyncio.run(helper.dupe_check(candidates, meta, "TEST"))
    assert skip is False
    assert result.name.endswith("DUPE?")


def test_preferred_repack_skip_and_replacement_id_link_and_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class RepackTracker(_Tracker):
        prefers_repack = True

    helper = _helper(monkeypatch, "REPACK", RepackTracker)

    preferred = _dupe("Preferred REPACK", id=9)
    meta = _meta(tmp_path, REPACK_preferred_repack=preferred)
    assert asyncio.run(helper.dupe_check([preferred], meta, "REPACK"))[0] is True

    replaced = _dupe("Original", id=1)
    meta = _meta(tmp_path, REPACK_repack_replaces=replaced, unattended=True)
    assert asyncio.run(helper.dupe_check([replaced], meta, "REPACK"))[0] is False

    link_only = _dupe("Original", id=None, link="https://tracker.invalid/original")
    meta = _meta(tmp_path, REPACK_repack_replaces=link_only, unattended=True)
    assert asyncio.run(helper.dupe_check([link_only], meta, "REPACK"))[0] is False

    identity = {"name": "original identity"}
    meta = _meta(tmp_path, REPACK_repack_replaces=identity, unattended=True)
    assert asyncio.run(helper.dupe_check([identity], meta, "REPACK"))[0] is False

    remaining = _dupe("Another", id=2, link="https://tracker.invalid/2")
    meta = _meta(tmp_path, REPACK_repack_replaces=replaced, unattended=True, dupe=True)
    assert asyncio.run(helper.dupe_check([replaced, remaining], meta, "REPACK"))[0] is False


def test_repack_result_sanitizes_control_text_and_uses_trusted_numeric_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class RepackTracker(_Tracker):
        prefers_repack = True

    helper = _helper(monkeypatch, "REPACK", RepackTracker)
    unsafe = {"name": "Bad\x1b]8;;https://evil.invalid\x07Name\x00", "id": "not-a-number"}
    meta = _meta(tmp_path, REPACK_preferred_repack=unsafe)
    assert asyncio.run(helper.dupe_check([unsafe], meta, "REPACK"))[0] is True

    valid = {"name": "Good", "id": "123"}
    meta = _meta(tmp_path, REPACK_preferred_repack=valid)
    assert asyncio.run(helper.dupe_check([valid], meta, "REPACK"))[0] is True

    huge = {"name": "Huge", "id": "1" * 21}
    meta = _meta(tmp_path, REPACK_preferred_repack=huge)
    assert asyncio.run(helper.dupe_check([huge], meta, "REPACK"))[0] is True


def test_season_pack_direct_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    meta = _meta(tmp_path, "TV", dupe=False, season_pack_exists=True, season_pack_name="Season Pack")
    assert asyncio.run(helper.dupe_check([_dupe()], meta, "TEST"))[0] is True


def test_trumpable_prompt_accept_decline_filter_and_tag_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch, "AITHER")

    accepted = _meta(tmp_path, "TV", unattended=False, dupe=False, trumpable_id=1)
    candidate = _dupe("Show.S01E01-GROUP", trumpable=True)
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    skip, accepted = asyncio.run(helper.dupe_check([candidate], accepted, "AITHER"))
    assert skip is False
    assert accepted.we_asked and accepted.were_trumping
    assert accepted.trump_reason == "trumpable_release"

    matched = [
        _dupe("Show.S01E01-OTHER", id=1, tracker="AITHER"),
        _dupe("Show.S01E01-GROUP", id=2, link="https://tracker.invalid/2", tracker="AITHER"),
    ]
    declined = _meta(
        tmp_path,
        "TV",
        unattended=False,
        dupe=False,
        tv_pack=True,
        season_pack_contains_episode=True,
        tag="-GROUP",
        AITHER_matched_episode_ids=matched,
    )
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=False))
    skip, declined = asyncio.run(helper.dupe_check(matched, declined, "AITHER"))
    assert skip is False
    assert declined.AITHER_matched_episode_ids == []

    no_tag = _meta(
        tmp_path,
        "TV",
        unattended=False,
        dupe=True,
        season_pack_contains_episode=True,
        tag="-MISSING",
        AITHER_matched_episode_ids=matched,
    )
    assert asyncio.run(helper.dupe_check(matched, no_tag, "AITHER"))[0] is False


def test_trumpable_prompt_eof_translates_to_domain_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch, "AITHER")
    meta = _meta(tmp_path, "TV", unattended=False, dupe=False, trumpable_id=1)
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(side_effect=EOFError))
    cleanup = AsyncMock()
    monkeypatch.setattr(decisions.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(decisions.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(helper.dupe_check([_dupe(trumpable=True)], meta, "AITHER"))
    cleanup.assert_awaited_once()


def test_exact_match_prompt_aither_other_decline_and_eof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch, "AITHER")
    exact = _meta(
        tmp_path,
        unattended=False,
        dupe=False,
        filename_match="file = link",
        file_count_match=1,
        AITHER_matched_id=7,
    )
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    skip, exact = asyncio.run(helper.dupe_check([_dupe()], exact, "AITHER"))
    assert skip is False and exact.were_trumping and exact.trump_reason == "exact_match"
    assert exact.AITHER_trumpable_id == 7

    other = _helper(monkeypatch, "OTHER")
    meta = _meta(tmp_path, unattended=False, dupe=False, filename_match="file", file_count_match=1)
    monkeypatch.setattr(other, "prompt_yes_no", AsyncMock(return_value=False))
    assert asyncio.run(other.dupe_check([_dupe()], meta, "OTHER"))[0] is True
    assert meta.we_asked

    monkeypatch.setattr(other, "prompt_yes_no", AsyncMock(side_effect=EOFError))
    monkeypatch.setattr(decisions.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(decisions.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError):
        asyncio.run(other.dupe_check([_dupe()], _meta(tmp_path, unattended=False, dupe=False, filename_match="file", file_count_match=1), "OTHER"))


def test_general_dupes_bdinfo_prompt_season_pack_display_and_eof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    helper.ask_bdinfo_comparison = AsyncMock()
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=False))
    disc = _meta(tmp_path, unattended=False, dupe=False, is_disc="BDMV")
    assert asyncio.run(helper.dupe_check([_dupe()], disc, "TEST"))[0] is True
    helper.ask_bdinfo_comparison.assert_awaited_once()

    pack = _meta(
        tmp_path,
        "TV",
        unattended=False,
        dupe=False,
        season_pack_exists=True,
        season_pack_name="Pack",
        season_pack_link="https://tracker.invalid/pack",
    )
    # The early season-pack guard applies only when dupe is explicitly false.
    assert asyncio.run(helper.dupe_check([_dupe()], pack, "TEST"))[0] is True

    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(side_effect=EOFError))
    monkeypatch.setattr(decisions.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(decisions.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError):
        asyncio.run(helper.dupe_check([_dupe()], _meta(tmp_path, unattended=False, dupe=False), "TEST"))


def test_cross_seed_beyondhd_filename_count_and_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Beyond(_Tracker):
        rename: ClassVar[object] = {"name": "Release 2026 1080p WEB-DL-GROUP Director's Cut US"}

    helper = _helper(monkeypatch, "BEYONDHD", Beyond)
    beyond = _meta(
        tmp_path,
        edition="Director's Cut",
        region="US",
        size_match="size",
        BEYONDHD_matched_download="https://download.invalid/bhd",
        unattended=True,
        dupe=True,
    )
    candidate = _dupe("Release 2026 1080p WEB-DL-GROUP")
    assert asyncio.run(helper.dupe_check([candidate], beyond, "BEYONDHD"))[0] is False
    assert beyond.BEYONDHD_cross_seed == "https://download.invalid/bhd"

    helper = _helper(monkeypatch, "TEST")
    by_file = _meta(
        tmp_path,
        filename_match="file",
        file_count_match=1,
        TEST_matched_download="https://download.invalid/file",
        unattended=True,
        dupe=True,
    )
    asyncio.run(helper.dupe_check([_dupe()], by_file, "TEST"))
    assert by_file.TEST_cross_seed == "https://download.invalid/file"

    by_size = _meta(
        tmp_path,
        size_match="size",
        TEST_matched_download="https://download.invalid/size",
        unattended=True,
        dupe=True,
    )
    _Tracker.rename = by_size.name
    try:
        asyncio.run(helper.dupe_check([_dupe(by_size.name)], by_size, "TEST"))
        assert by_size.TEST_cross_seed == "https://download.invalid/size"
    finally:
        _Tracker.rename = "Tracker Release"


def test_remaining_dupe_formatting_repack_string_and_exact_trump_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    meta = _meta(tmp_path, unattended=False, dupe=True, source_size=100)
    assert asyncio.run(helper.dupe_check([_dupe("No Link", link=None, size=200)], meta, "TEST"))[0] is False

    class RepackTracker(_Tracker):
        prefers_repack = True

    repack_helper = _helper(monkeypatch, "REPACK_STRING", RepackTracker)
    meta = _meta(tmp_path, REPACK_STRING_preferred_repack="unsafe\x00 string")
    assert asyncio.run(repack_helper.dupe_check(["unsafe string"], meta, "REPACK_STRING"))[0] is True

    trump = _helper(monkeypatch, "AITHER")
    meta = _meta(
        tmp_path,
        unattended=False,
        dupe=False,
        trumpable_id=1,
        filename_match="file",
        file_count_match=1,
    )
    monkeypatch.setattr(trump, "prompt_yes_no", AsyncMock(return_value=True))
    _, meta = asyncio.run(trump.dupe_check([_dupe(trumpable=True)], meta, "AITHER"))
    assert meta.trump_reason == "exact_match"


def test_ask_bdinfo_comparison_no_content_decline_and_rendered_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _helper(monkeypatch)
    meta = _meta(tmp_path, is_disc="BDMV")
    monkeypatch.setattr(decisions, "has_bdinfo_content", lambda _entry: "")
    prompt = AsyncMock(return_value=True)
    monkeypatch.setattr(helper, "prompt_yes_no", prompt)
    asyncio.run(helper.ask_bdinfo_comparison(meta, ["plain", _dupe()], "TEST"))
    prompt.assert_not_awaited()

    monkeypatch.setattr(decisions, "has_bdinfo_content", lambda _entry: "BDINFO")
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=False))
    asyncio.run(helper.ask_bdinfo_comparison(meta, [_dupe()], "TEST"))

    responses = iter(
        (
            ("warning one", "result one"),
            ("", "result two"),
            ("warning three", ""),
        )
    )
    monkeypatch.setattr(decisions, "compare_bdinfo", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    asyncio.run(helper.ask_bdinfo_comparison(meta, ["plain", _dupe(id=1), _dupe(id=2), _dupe(id=3)], "TEST"))


def test_get_confirmation_unattended_book_and_music_views(tmp_path: Path) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    cover = tmp_path / "book-cover.jpg"
    cover.write_bytes(b"cover")
    book = _meta(
        tmp_path,
        "BOOK",
        debug=True,
        author="Author",
        book_translator="Translator",
        publisher="Publisher",
        book_language="English",
        isbn="9780000000000",
        asin="B000000000",
        narrator="Narrator",
        audiobook_duration_formatted="01h 02m",
        artwork_path=str(cover),
        comic=True,
        manga=False,
        magazine=True,
        newspaper=False,
        audiobook=True,
    )
    assert asyncio.run(helper.get_confirmation(book)) is True

    music_release = {
        "fields": {
            "artist": _field("Artist"),
            "album": _field("Album"),
            "year": _field(2026),
            "release_type": _field("Album"),
            "media": _field("WEB"),
            "format": _field("FLAC"),
        },
        "tracks": [{"format": "FLAC", "codec": "FLAC", "sample_rate": 44100, "channels": 2}],
    }
    music = _meta(tmp_path, "MUSIC", music_release=music_release, artwork_url="https://images.invalid/cover.jpg")
    assert asyncio.run(helper.get_confirmation(music)) is True


def test_get_confirmation_game_notes_ids_languages_and_cover_variants(tmp_path: Path) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    configured = _meta(
        tmp_path,
        "GAME",
        software_notes="Install these files",
        platform="PC",
        game_subcategory="full_game_dlc",
        game_version="1.2.3",
        developer="Developer",
        publisher="Publisher",
        artwork_url="https://images.invalid/game.jpg",
        igdb_id=123,
        steam_url="https://store.steampowered.com/app/123/game",
        languages={"English": ["audio", "text"], "French": ["text"]},
    )
    assert asyncio.run(helper.get_confirmation(configured)) is True

    linked = _meta(
        tmp_path,
        "GAME",
        software_notes="",
        description_link="https://example.invalid/very-long-installation-instructions",
        platform="PC",
        game_subcategory="unknown",
        game_version="",
        developer="",
        publisher="",
        artwork_url="",
        artwork_path="",
        igdb_id=0,
        steam_url="",
        languages=["English", "German"],
    )
    assert asyncio.run(helper.get_confirmation(linked)) is True

    file_notes = tmp_path / "INSTALL.txt"
    file_notes.write_text("instructions", encoding="utf-8")
    non_pc = _meta(
        tmp_path,
        "GAME",
        software_notes="",
        description_link="",
        description_file=str(file_notes),
        platform="PlayStation 5",
        game_subcategory="update",
        languages="English",
    )
    assert asyncio.run(helper.get_confirmation(non_pc)) is True

    missing_pc = _meta(
        tmp_path,
        "GAME",
        software_notes="",
        description_link="",
        description_file="",
        platform="PC",
        game_subcategory="dlc",
        languages=[],
    )
    assert asyncio.run(helper.get_confirmation(missing_pc)) is True


def test_get_confirmation_tv_disc_ids_personal_freeleech_and_episode_fields(tmp_path: Path) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    tv = _meta(
        tmp_path,
        "TV",
        tv_pack=False,
        auto_episode_title="Episode Title",
        overview_meta="Episode overview that should be truncated after sixty characters for display.",
        is_disc="BDMV",
        personalrelease=True,
        freeleech=100,
        tag="-GROUP",
    )
    assert asyncio.run(helper.get_confirmation(tv)) is True
    assert tv.keep_folder is False

    pack = _meta(tmp_path, "TV", tv_pack=True, auto_episode_title="Hidden", overview_meta="Hidden", demographic="")
    assert asyncio.run(helper.get_confirmation(pack)) is True


def test_get_confirmation_keep_folder_rejection_and_tracker_name_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class SingleValue(_Tracker):
        rename: ClassVar[object] = {"release": "Single Value Name"}

    class NamedDict(_Tracker):
        rename: ClassVar[object] = {"name": "Named Dict", "extra": "value"}

    class StringName(_Tracker):
        rename: ClassVar[object] = "String Name"

    class BrokenName(_Tracker):
        fail_name = True

    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": True}})
    mapping = dict(helper.tracker_class_map)
    mapping.update({"SINGLE": SingleValue, "NAMED": NamedDict, "STRING": StringName, "BROKEN": BrokenName})
    monkeypatch.setattr(helper, "tracker_class_map", mapping)

    rejected = _meta(tmp_path, unattended=False, keep_folder=True, isdir=True, trackers=[])
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=False))
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(helper.get_confirmation(rejected))

    accepted = _meta(
        tmp_path,
        unattended=False,
        keep_folder=True,
        isdir=True,
        trackers=["MANUAL", "USENET", "MISSING", "SINGLE", "NAMED", "STRING", "BROKEN"],
    )
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(side_effect=[True, False]))
    assert asyncio.run(helper.get_confirmation(accepted)) is False


def test_get_confirmation_unchanged_ids_returns_before_audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    meta = _meta(tmp_path, unattended=False, trackers=[])
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    assert asyncio.run(helper.get_confirmation(meta)) is True
    assert not (tmp_path / "data" / "db_check.json").exists()


def test_get_confirmation_changed_ids_writes_and_appends_audit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    prompt = AsyncMock(return_value=True)
    monkeypatch.setattr(helper, "prompt_yes_no", prompt)

    changed = _meta(
        tmp_path,
        unattended=False,
        trackers=[],
        original_imdb=None,
        original_tmdb=None,
        original_tvdb=None,
        original_tvmaze=None,
        original_mal=None,
        original_category=None,
        imdb_id=123,
        tmdb_id=456,
        tvdb_id=789,
        tvmaze_id=321,
        mal_id=654,
        category="TV",
    )
    assert asyncio.run(helper.get_confirmation(changed)) is True
    path = tmp_path / "data" / "db_check.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["original"]["imdb_id"] == "N/A"
    assert payload[0]["changed"]["imdb_url"].endswith("tt0000123")

    path.write_text("invalid json", encoding="utf-8")
    assert asyncio.run(helper.get_confirmation(changed)) is True
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1

    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert asyncio.run(helper.get_confirmation(changed)) is True
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1

    path.write_text(json.dumps([{"existing": True}]), encoding="utf-8")
    assert asyncio.run(helper.get_confirmation(changed)) is True
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 2


def test_get_confirmation_changed_ids_handles_empty_and_invalid_url_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    monkeypatch.setattr(helper, "prompt_yes_no", AsyncMock(return_value=True))
    path = tmp_path / "data" / "db_check.json"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    meta = _meta(
        tmp_path,
        unattended=False,
        trackers=[],
        original_imdb="not-numeric",
        original_tmdb=0,
        original_tvdb=0,
        original_tvmaze=0,
        original_mal=0,
        original_category="",
        imdb_id="not-numeric",
        tmdb_id=0,
        tvdb_id=0,
        tvmaze_id=0,
        mal_id=0,
        category="MOVIE",
    )
    assert asyncio.run(helper.get_confirmation(meta)) is True
    payload = json.loads(path.read_text(encoding="utf-8"))[0]
    assert payload["original"]["imdb_url"] is None
    assert payload["changed"]["tmdb_url"] is None


def test_get_confirmation_disc_clears_keep_folder_before_interactive_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = UploadHelper({"DEFAULT": {"sfx_on_prompt": False}})
    meta = _meta(tmp_path, unattended=False, is_disc="BDMV", keep_folder=True, isdir=True, trackers=[])
    prompt = AsyncMock(return_value=False)
    monkeypatch.setattr(helper, "prompt_yes_no", prompt)

    assert asyncio.run(helper.get_confirmation(meta)) is False
    assert meta.keep_folder is False
    prompt.assert_awaited_once_with("Is this correct?")
