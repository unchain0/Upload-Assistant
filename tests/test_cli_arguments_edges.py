from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Any

import pytest

from src.delivery.cli import arguments
from src.delivery.cli.arguments import Args, CustomArgumentParser, ShortHelpFormatter, partition_existing_paths, read_paths_from_stdin
from src.domain_models.release import Meta


def _parsed(monkeypatch: pytest.MonkeyPatch, values: dict[str, Any], meta: Meta | None = None, before: list[str] | None = None) -> Meta:
    namespace = argparse.Namespace(**values)
    monkeypatch.setattr(CustomArgumentParser, "parse_known_args", lambda _self, _input: (namespace, list(before or [])))
    parsed, _parser, _unused = Args({"DEFAULT": {"screens": 3}, "TRACKERS": {"AITHER": {"announce_url": "https://aither.invalid/announce"}}}).parse([], meta or Meta())
    return parsed


def test_partition_and_stdin_paths(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.write_text("x", encoding="utf-8")
    found, missing = partition_existing_paths([str(existing), str(tmp_path / "missing")])
    assert found == [str(existing.resolve())]
    assert missing == [str(tmp_path / "missing")]

    assert read_paths_from_stdin(["path"], io.StringIO("unused")) == (["path"], [])
    assert read_paths_from_stdin(["-h", "--paths-from-stdin"], io.StringIO("unused")) == (["-h", "--paths-from-stdin"], [])
    with pytest.raises(ValueError, match="only be specified once"):
        read_paths_from_stdin(["--paths-from-stdin", "--paths-from-stdin"], io.StringIO("path"))
    with pytest.raises(ValueError, match="did not receive any paths"):
        read_paths_from_stdin(["--paths-from-stdin"], io.StringIO("\n"))

    stream = io.StringIO("one\none\n\ntwo\n")
    stream.isatty = lambda: False  # type: ignore[method-assign]
    args, paths = read_paths_from_stdin(["--paths-from-stdin", "--debug"], stream)
    assert args == ["--debug"] and paths == ["one", "two"]

    interactive = io.StringIO("one\n\ntwo\n")
    interactive.isatty = lambda: True  # type: ignore[method-assign]
    assert read_paths_from_stdin(["--paths-from-stdin"], interactive)[1] == ["one"]


def test_help_formatter_and_parser_short_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    formatter = ShortHelpFormatter("upload.py")
    text = formatter.format_help()
    assert text.startswith("usage: upload.py") and "--tmdb" in text and "--debug" in text

    output = io.StringIO()
    parser = CustomArgumentParser(prog="upload.py")
    monkeypatch.setattr(sys, "argv", ["upload.py", "-h"])
    parser.print_help(output)
    assert "usage: upload.py" in output.getvalue()

    output = io.StringIO()
    parser.add_argument("--custom")
    monkeypatch.setattr(sys, "argv", ["upload.py", "--help"])
    parser.print_help(output)
    assert "--custom" in output.getvalue()


def test_parse_requires_path_and_site_upload_supplies_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[bool] = []
    monkeypatch.setattr(CustomArgumentParser, "print_help", lambda _self, _file=None: printed.append(True))
    with pytest.raises(SystemExit) as exc:
        _parsed(monkeypatch, {"path": [], "site_upload": None})
    assert exc.value.code == 1 and printed == [True]

    meta = _parsed(monkeypatch, {"path": [], "site_upload": ["aither"]})
    assert meta.path == "dummy_path_for_site_upload"
    assert meta.site_upload == "AITHER"


def test_parse_reassembles_paths_with_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    movie = tmp_path / "My Movie.mkv"
    movie.write_bytes(b"video")
    meta = _parsed(monkeypatch, {"path": [str(tmp_path / "My")], "site_upload": None}, before=["Movie.mkv"])
    assert meta.path == str(movie)

    folder = tmp_path / "My Folder"
    folder.mkdir()
    meta = _parsed(monkeypatch, {"path": [str(tmp_path / "My")], "site_upload": None}, before=["Folder"])
    assert meta.path == str(folder)


def test_parse_list_value_assignments_and_identifier_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    description = tmp_path / "description.txt"
    description.write_text("description", encoding="utf-8")
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    values: dict[str, Any] = {
        "path": [str(tmp_path)],
        "site_upload": ["aither"],
        "manual_type": ["web-dl"],
        "tag": ["GROUP"],
        "description_file": [str(description)],
        "comparison": [str(comparison)],
        "screens": ["5"],
        "imghost": ["imgbox"],
        "season": ["S01"],
        "episode": ["E02"],
        "manual_date": ["2026-01-02"],
        "tmdb_manual": ["movie/123"],
        "tracker_id": ["AITHER=456"],
        "manual_cast": ["Alice, Bob"],
        "openlibrary": ["https://openlibrary.org/works/OL123W"],
        "steam_manual": ["https://store.steampowered.com/app/789/game"],
        "manual_year": ["2024"],
        "manual_edition": ["Director", "Cut"],
        "manual_dvds": ["DVD1", "DVD2"],
        "dupe_size_difference_tolerance": ["5.5"],
        "freeleech": ["25"],
        "manual_episode_title": [],
        "tvmaze_manual": ["99"],
        "trackers": ["aither,bhd"],
        "manual_frames": "1, 2,3",
        "archive_password": ["random"],
    }
    meta = Meta(tmdb_manual="old", imdb_manual="old")
    parsed = _parsed(monkeypatch, values, meta)
    assert parsed.manual_type == "WEBDL"
    assert parsed.tag == "-GROUP"
    assert parsed.description_file == str(description.resolve())
    assert parsed.comparison == str(comparison.resolve())
    assert parsed.screens == 5 and parsed.imghost == "imgbox" and parsed.imghost_from_cli
    assert parsed.manual_season == "S01" and parsed.manual_episode == "E02" and parsed.manual_date == "2026-01-02"
    assert parsed.category == "MOVIE" and parsed.tmdb_manual == 123
    assert parsed.get_tracker_id("AITHER") == "456"
    assert parsed.manual_cast == ["Alice", "Bob"]
    assert parsed.openlibrary == "OL123W" and parsed.steam_manual == "789"
    assert parsed.manual_year == 2024
    assert parsed.manual_edition == ["Director", "Cut"]
    assert parsed.manual_dvds == ["DVD1", "DVD2"]
    assert parsed.dupe_size_difference_tolerance == 5.5 and parsed.freeleech == 25
    assert parsed.tvmaze_manual == "99"
    assert parsed.trackers == ["AITHER", "BHD"]
    assert parsed.manual_frames == [1, 2, 3]
    assert parsed.usenet_archive_password_is_random is True


def test_parse_scalar_empty_and_multi_value_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, Any] = {
        "path": [str(tmp_path)],
        "site_upload": "aither",
        "manual_year": 2025,
        "manual_edition": "Extended",
        "manual_dvds": "DVD9",
        "dupe_size_difference_tolerance": 10,
        "freeleech": 50,
        "manual_episode_title": [],
        "tvmaze_manual": 123,
        "trackers": ["aither", "bhd,ptp"],
    }
    parsed = _parsed(monkeypatch, values)
    assert parsed.site_upload == "AITHER"
    assert parsed.manual_year == 2025 and parsed.manual_edition == "Extended" and parsed.manual_dvds == "DVD9"
    assert parsed.dupe_size_difference_tolerance == 10.0 and parsed.freeleech == 50
    assert parsed.tvmaze_manual == 123 and parsed.trackers == ["AITHER", "BHD", "PTP"]

    empty_values = {
        "path": [str(tmp_path)],
        "site_upload": [],
        "manual_year": [],
        "manual_edition": [],
        "manual_dvds": [],
        "dupe_size_difference_tolerance": [],
        "freeleech": [],
        "tvmaze_manual": [],
        "trackers": [],
    }
    parsed = _parsed(monkeypatch, empty_values)
    assert parsed.site_upload is None
    assert parsed.manual_year == 0 and parsed.manual_dvds == ""
    assert parsed.dupe_size_difference_tolerance is None and parsed.freeleech == 0
    assert parsed.trackers == []


def test_parse_openlibrary_and_steam_raw_and_error_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = _parsed(
        monkeypatch,
        {"path": [str(tmp_path)], "site_upload": None, "openlibrary": ["OL456M"], "steam_manual": ["123"]},
    )
    assert parsed.openlibrary == "OL456M" and parsed.steam_manual == "123"

    parsed = _parsed(
        monkeypatch,
        {
            "path": [str(tmp_path)],
            "site_upload": None,
            "openlibrary": ["https://openlibrary.org/not/a/work"],
            "steam_manual": ["https://example.invalid/no-app"],
        },
    )
    assert parsed.openlibrary == "work"
    assert parsed.steam_manual == "https://example.invalid/no-app"

    class BrokenParsed:
        @property
        def path(self) -> str:
            raise RuntimeError("bad path")

    monkeypatch.setattr(arguments.urllib.parse, "urlparse", lambda _value: BrokenParsed())
    parsed = _parsed(
        monkeypatch,
        {
            "path": [str(tmp_path)],
            "site_upload": None,
            "openlibrary": ["https://broken.invalid/work"],
            "steam_manual": ["https://broken.invalid/app/1"],
        },
    )
    assert parsed.steam_manual == "https://broken.invalid/app/1"


def test_invalid_manual_frames_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exc:
        _parsed(monkeypatch, {"path": [str(tmp_path)], "site_upload": None, "manual_frames": "1,bad"})
    assert exc.value.code == 1


def test_book_overrides_language_primary_find_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arguments, "detect_newspaper", lambda _meta: None)
    monkeypatch.setattr(arguments, "sanitize_book_language", lambda _meta: None)
    monkeypatch.setattr(arguments, "sanitize_book_author", lambda _meta: None)

    meta = Meta(
        book_overview=["Custom", "overview"],
        book_author=" Author ",
        book_title=" Title ",
        book_isbn=" 9780000000000 ",
        book_asin=" B000000000 ",
        openlibrary=" OL1W ",
        book_publisher=" Publisher ",
        book_translator=" Translator ",
        book_language="pt",
        manual_year="2024",
    )
    Args._apply_book_meta_overrides(meta)
    assert meta.overview == "Custom overview" and meta.book_overview == "Custom overview"
    assert meta.author == "Author" and meta.title == "Title" and meta.isbn == "9780000000000"
    assert meta.asin == "B000000000" and meta.openlibrary == "OL1W"
    assert meta.publisher == "Publisher" and meta.book_translator == "Translator"
    assert meta.book_language == "Portuguese" and meta.book_language_iso == "por"
    assert meta.year == 2024 and meta.search_year == "2024"

    import langcodes

    class Same:
        def display_name(self, _language: str) -> str:
            return "english"

        def to_alpha3(self) -> str:
            return "eng"

    class Found:
        def display_name(self, _language: str) -> str:
            return "English"

        def to_alpha3(self) -> str:
            return "eng"

    monkeypatch.setattr(langcodes, "get", lambda _value: Same())
    monkeypatch.setattr(langcodes, "find", lambda _value: Found())
    meta = Meta(book_language="english")
    Args._apply_book_meta_overrides(meta)
    assert meta.book_language == "English" and meta.book_language_iso == "eng"

    monkeypatch.setattr(langcodes, "get", lambda _value: (_ for _ in ()).throw(LookupError()))
    monkeypatch.setattr(langcodes, "find", lambda _value: (_ for _ in ()).throw(LookupError()))
    meta = Meta(book_language="unknown language")
    Args._apply_book_meta_overrides(meta)
    assert meta.book_language == "Unknown Language" and meta.book_language_iso == ""

    meta = Meta(book_overview=[], book_language="")
    Args._apply_book_meta_overrides(meta)
    assert meta.overview == "" and meta.book_overview == ""


def test_game_overrides_all_values() -> None:
    meta = Meta(manual_platform=" ps5 ", steam_manual=" 123 ", game_version=" 1.2 ", game_subcategory=" DLC ", manual_year="2026")
    Args._apply_game_meta_overrides(meta)
    assert meta.manual_platform == "PS5" and meta.platform == "PS5"
    assert meta.steam_manual == "123" and meta.game_version == "1.2" and meta.game_subcategory == "dlc"
    assert meta.year == 2026 and meta.search_year == "2026"

    custom = Meta(manual_platform="custom")
    Args._apply_game_meta_overrides(custom)
    assert custom.platform == "CUSTOM"


def test_list_to_string_and_identifier_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = Args({"TRACKERS": {}})
    assert parser.list_to_string(["one"]) == "one"
    assert parser.list_to_string(["one", "two"]) == "one two"
    assert parser.list_to_string([object(), object()]) == "None"  # type: ignore[list-item]

    monkeypatch.setattr(arguments, "get_tracker_comment_hosts", lambda _config: {"AITHER": ("same.invalid",), "BHD": ("same.invalid",)})
    with pytest.raises(ValueError, match="unknown or ambiguous"):
        parser.parse_tracker_id("https://same.invalid/torrents/1")
    monkeypatch.setattr(arguments, "is_known_tracker", lambda _name: False)
    with pytest.raises(ValueError, match="supported tracker"):
        parser.parse_tracker_id("UNKNOWN=1")
    monkeypatch.setattr(arguments, "is_known_tracker", lambda _name: True)
    with pytest.raises(ValueError, match="numeric torrent ID"):
        parser.parse_tracker_id("AITHER=not-numeric")

    assert parser.parse_tmdb_id("movie/123", None) == ("MOVIE", 123)


def test_remaining_list_and_tracker_value_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    multi_site = _parsed(monkeypatch, {"path": [str(tmp_path)], "site_upload": ["aither", "bhd"]})
    assert multi_site.site_upload == "['AITHER', 'BHD']"

    one_dvd = _parsed(monkeypatch, {"path": [str(tmp_path)], "site_upload": None, "manual_dvds": ["DVD9"]})
    assert one_dvd.manual_dvds == "DVD9"

    scalar_tracker = _parsed(monkeypatch, {"path": [str(tmp_path)], "site_upload": None, "trackers": "aither"})
    assert scalar_tracker.trackers == ["AITHER"]

    numeric_tracker = _parsed(monkeypatch, {"path": [str(tmp_path)], "site_upload": None, "trackers": 123})
    assert numeric_tracker.trackers == ["123"]
