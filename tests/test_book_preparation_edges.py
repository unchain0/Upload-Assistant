from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import MediaInfoError
from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.services import book_preparation


def _book_file(tmp_path: Path, name: str = "Alice Writer - Example Book.m4b") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"book")
    return path


def _meta(tmp_path: Path, path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "book-edge",
        "path": str(path),
        "filelist": [str(path)],
        "category": "BOOK",
        "edit": True,
        "unattended": False,
        "skip_auto_torrent": True,
    }
    state.update(values)
    return Meta(state)


def _patch_external_lookups(monkeypatch: pytest.MonkeyPatch, *, mam=None, google=None, openlibrary=None) -> None:
    monkeypatch.setattr(
        "src.integrations.external_apis.myanonamouse.myanonamouse_manager.search_by_id",
        AsyncMock(return_value=mam),
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.google_books.google_books_manager.search_by_isbn",
        AsyncMock(return_value=google),
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.openlibrary.openlibrary_manager.search_by_isbn",
        AsyncMock(return_value=openlibrary),
    )
    monkeypatch.setattr(
        "src.integrations.external_apis.openlibrary.openlibrary_manager.search_by_work_id",
        AsyncMock(return_value=openlibrary),
    )


def test_resolve_book_filelist_rejects_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ItemProcessingError, match="No book or audiobook"):
        book_preparation.resolve_book_filelist(Meta(), str(empty))


def test_media_info_helpers_cover_case_lists_and_unescaping() -> None:
    assert book_preparation._mi_extra({}, "SERIES") == ""
    assert book_preparation._mi_extra({"extra": []}, "SERIES") == ""
    assert book_preparation._mi_extra({"extra": {"series": "  Saga  "}}, "SERIES") == "Saga"
    assert book_preparation._mi_extra({"extra": {"series": {"bad": True}}}, "SERIES") == ""
    assert book_preparation._mi_extra({"extra": {"series": "  "}}, "SERIES") == ""
    assert book_preparation._unescape_meta_val(None) is None
    assert book_preparation._unescape_meta_val({"bad": True}) is None
    assert book_preparation._unescape_meta_val(["bad"]) is None
    assert book_preparation._unescape_meta_val(" A &amp; B ") == "A & B"


def test_epub_content_identifier_limits_errors_and_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not zip")
    assert book_preparation._epub_content_identifiers(str(bad)) == (set(), set())

    rich = tmp_path / "rich.epub"
    with zipfile.ZipFile(rich, "w") as archive:
        archive.writestr("content.opf", "ISBN 9780306406157 ASIN B012345678")
        archive.writestr("image.jpg", b"image")
    assert book_preparation._epub_content_identifiers(str(rich)) == ({"9780306406157"}, {"B012345678"})

    many = tmp_path / "many.epub"
    with zipfile.ZipFile(many, "w") as archive:
        for index in range(4097):
            archive.writestr(f"{index}.xml", "")
    assert book_preparation._epub_content_identifiers(str(many)) == (set(), set())

    huge = tmp_path / "huge.epub"
    with zipfile.ZipFile(huge, "w") as archive:
        archive.writestr("huge.xml", b"x" * (17 * 1024 * 1024))
    assert book_preparation._epub_content_identifiers(str(huge)) == (set(), set())

    safe = tmp_path / "safe.epub"
    with zipfile.ZipFile(safe, "w") as archive:
        archive.writestr("content.opf", "ISBN 9780306406157")
    monkeypatch.setattr(book_preparation, "_safe_zip_member_bytes", lambda *_args: None)
    assert book_preparation._epub_content_identifiers(str(safe)) == (set(), set())


def test_reconcile_epub_identifiers_priorities_and_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "Book.epub"
    path.write_bytes(b"book")
    meta = Meta()

    monkeypatch.setattr(book_preparation, "_validated_isbns", lambda _value: {"9780306406157"})
    monkeypatch.setattr(book_preparation, "_epub_content_identifiers", lambda _path: ({"9781861972712"}, {"B012345678"}))
    metadata: dict[str, object] = {}
    book_preparation._reconcile_epub_identifiers(meta, metadata, str(path))
    assert metadata["isbn"] == "9780306406157"
    assert metadata["asin"] == "B012345678"

    monkeypatch.setattr(book_preparation, "_validated_isbns", lambda _value: {"9780306406157", "9781861972712"})
    with pytest.raises(ItemProcessingError, match="Conflicting EPUB ISBNs"):
        book_preparation._reconcile_epub_identifiers(meta, {}, str(path))

    monkeypatch.setattr(book_preparation, "_validated_isbns", lambda _value: set())
    monkeypatch.setattr(book_preparation, "_epub_content_identifiers", lambda _path: ({"9780306406157", "9781861972712"}, set()))
    with pytest.raises(ItemProcessingError, match="Conflicting EPUB ISBNs"):
        book_preparation._reconcile_epub_identifiers(meta, {}, str(path))

    monkeypatch.setattr(book_preparation, "_epub_content_identifiers", lambda _path: ({"9780306406157"}, set()))
    metadata = {}
    book_preparation._reconcile_epub_identifiers(meta, metadata, str(path))
    assert metadata["isbn"] == "9780306406157"

    metadata = {"isbn": "9781861972712"}
    monkeypatch.setattr(book_preparation, "_epub_content_identifiers", lambda _path: (set(), set()))
    book_preparation._reconcile_epub_identifiers(meta, metadata, str(path))
    assert metadata["isbn"] == "9781861972712"


def test_rich_embedded_mediainfo_populates_audiobook_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path)
    meta = _meta(
        tmp_path,
        path,
        audiobook=False,
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Album": "Example Book [Unabridged]",
                        "Track_name": "Fallback Track",
                        "Performer": "Alice Writer",
                        "Album_Performer": "Fallback Author",
                        "Composer": "Nora Narrator",
                        "Publisher": "Example Press",
                        "ISBN": "9780306406157",
                        "ASIN": "B012345678",
                        "Comment": "A &amp; B overview",
                        "Description": "Fallback description",
                        "Recorded_Date": "Recorded 2024-01-01",
                        "Genre": "Fiction; Adventure, fiction",
                        "Language": "en",
                        "extra": {"SERIES": "Example Saga", "SERIESPART": "2.0"},
                    },
                    {"@type": "Audio", "Language": "fr"},
                    {"@type": "Text", "Language": "de"},
                ]
            }
        },
    )
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))

    assert meta.category == "BOOK"
    assert meta.edition == "Unabridged"
    assert meta.title == "Example Book"
    assert meta.author == "Alice Writer"
    assert meta.narrator == "Nora Narrator"
    assert meta.publisher == "Example Press"
    assert meta.isbn == "9780306406157"
    assert meta.asin == "B012345678"
    assert meta.book_series == "Example Saga"
    assert meta.book_series_index == "2"
    assert meta.overview == "A & B overview"
    assert meta.year == 2024 and meta.search_year == 2024
    assert meta.keywords == ["fiction", "adventure"]
    assert meta.book_language == "English" and meta.book_language_iso == "eng"


def test_embedded_metadata_uses_fallback_author_description_asin_and_audio_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Fallback Author - Fallback Title.m4b")
    meta = _meta(
        tmp_path,
        path,
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Track_name": "Fallback Title (Abridged)",
                        "Album_Performer": "Fallback Author",
                        "Description": "Description only",
                        "ISBN": "ASIN: B098765432",
                        "Recorded_Date": "no year",
                        "Genre": "  ",
                        "extra": {"ASIN": "B000000001"},
                    },
                    {"@type": "Audio", "Language": "fr"},
                ]
            }
        },
    )
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.edition == "Abridged"
    assert meta.title == "Fallback Title"
    assert meta.author == "Fallback Author"
    assert meta.overview == "Description only"
    assert meta.asin == "B098765432"
    assert meta.book_language == "French"


def test_embedded_metadata_text_language_and_error_are_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Unknown Book.pdf")
    meta = _meta(
        tmp_path,
        path,
        mediainfo={"media": {"track": [{"@type": "General", "Album": ["invalid"]}, {"@type": "Text", "Language": "de"}]}},
    )
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.book_language == "German"

    class Broken(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("broken metadata")

    meta = _meta(tmp_path, path, mediainfo=Broken())
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))


def test_source_extractors_cli_overrides_and_invalid_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for suffix, attribute, payload in (
        (".epub", "_extract_epub_metadata", {"title": "EPUB title", "book_language_raw": "en", "isbn": "invalid", "year": 2020}),
        (".cbz", "_extract_cbr_cbz_metadata", {"title": "Comic title", "author": "Comic Author"}),
        (".mobi", "_extract_mobi_metadata", {"title": "MOBI title", "author": "MOBI Author"}),
    ):
        path = _book_file(tmp_path, f"book{suffix}")
        monkeypatch.setattr(book_preparation, attribute, lambda _path, value=payload: value)
        if suffix == ".epub":
            monkeypatch.setattr(book_preparation, "_get_epubmeta_output", lambda _path: "epubmeta")
            monkeypatch.setattr(book_preparation, "_reconcile_epub_identifiers", lambda *_args: None)
        meta = _meta(tmp_path, path, mediainfo={}, book_title="CLI title", book_language="English")
        _patch_external_lookups(monkeypatch)
        asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
        assert meta.title != payload.get("title")

    pdf = _book_file(tmp_path, "book.pdf")
    monkeypatch.setattr(book_preparation, "_extract_pdf_page_count", lambda _path: 123)
    monkeypatch.setattr(book_preparation, "_extract_isbn_from_pdf", lambda _path: "9780306406157")
    meta = _meta(tmp_path, pdf, mediainfo={})
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(pdf), str(tmp_path), {"DEFAULT": {}}))
    assert meta.page_count == 123 and meta.isbn == "9780306406157"


def test_media_info_export_errors_are_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path)
    _patch_external_lookups(monkeypatch)
    monkeypatch.setattr(
        book_preparation,
        "export_info",
        AsyncMock(side_effect=MediaInfoError("bad", command=["mediainfo"], stderr="details")),
    )
    meta = _meta(tmp_path, path, edit=False)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.mediainfo == {}

    monkeypatch.setattr(book_preparation, "export_info", AsyncMock(side_effect=RuntimeError("bad")))
    meta = _meta(tmp_path, path, edit=False)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.mediainfo == {}


def test_mam_host_comment_google_openlibrary_and_lookup_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Alice Writer - Example Book.m4b")
    mam_data = {
        "title": "MAM Title",
        "author": "MAM Author",
        "publisher": "MAM Press",
        "year": 2021,
        "isbn": "9780306406157",
        "search_year": 2021,
        "book_language": "English",
        "book_language_iso": "eng",
    }
    google_data = {"title": "Google Title", "publisher": "Google Press", "artwork_url": "https://cover.invalid/google.jpg", "isbn": "9780306406157"}
    open_data = {"title": "Open Title", "overview": "Publisher: Open Press", "isbn": "9780306406157"}
    _patch_external_lookups(monkeypatch, mam=mam_data, google=google_data, openlibrary=open_data)

    meta = _meta(
        tmp_path,
        path,
        mediainfo={},
        torrent_comments=[
            {"trackers": "not-mam", "comment": "MID=1", "tracker_urls": [{"url": "https://foo.myanonamouse.net/announce"}]},
        ],
    )
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {"mam_api_key": "key", "google_books_api_key": "key"}}))
    assert meta.title == "Google Title"
    assert meta.publisher == "MAM Press"
    assert meta.artwork_url == "https://cover.invalid/google.jpg"

    async def fail(*_args, **_kwargs):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr("src.integrations.external_apis.myanonamouse.myanonamouse_manager.search_by_id", fail)
    monkeypatch.setattr("src.integrations.external_apis.google_books.google_books_manager.search_by_isbn", fail)
    meta = _meta(
        tmp_path,
        path,
        mediainfo={},
        isbn="9780306406157",
        torrent_comments=[{"trackers": "myanonamouse.net", "comment": "MID=2"}],
    )
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))


def test_openlibrary_work_lookup_publisher_inference_and_invalid_isbn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Alice Writer - Example Book.epub")
    _patch_external_lookups(
        monkeypatch,
        openlibrary={"title": "Open Title", "year": 2022, "search_year": 2022, "overview": "Publisher: Inferred Press"},
    )
    monkeypatch.setattr(book_preparation, "_get_epubmeta_output", lambda _path: "")
    monkeypatch.setattr(book_preparation, "_extract_epub_metadata", lambda _path: {})
    meta = _meta(tmp_path, path, mediainfo={}, openlibrary="OL123W")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.title == "Open Title" and meta.publisher == "Inferred Press"

    meta = _meta(tmp_path, path, mediainfo={}, isbn="invalid", book_isbn="invalid")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.isbn == "" and meta.book_isbn == ""


def test_audiobook_duration_and_bitrate_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = [_book_file(tmp_path, f"track-{index}.mp3") for index in range(6)]
    missing = tmp_path / "missing.mp3"

    class Track:
        def __init__(self, track_type: str, *, duration=None, data=None) -> None:
            self.track_type = track_type
            self.duration = duration
            self._data = data or {}

        def to_data(self):
            return self._data

    duration_values = {
        str(files[0]): [Track("General", duration=3_700_000)],
        str(files[1]): [Track("General", duration=125_000)],
        str(files[2]): [Track("General", duration=None)],
    }

    def parse_duration(path: str):
        if path == str(files[3]):
            raise RuntimeError("bad")
        return SimpleNamespace(tracks=duration_values.get(path, []))

    monkeypatch.setattr(book_preparation.MediaInfo, "parse", parse_duration)
    total, formatted = asyncio.run(book_preparation.get_audiobook_duration([*(str(path) for path in files[:4]), str(missing), "not-audio.txt"]))
    assert total == 3825.0
    assert formatted == "01h 03m 45s"
    assert asyncio.run(book_preparation.get_audiobook_duration([])) == (0.0, "")

    bitrate_values = {
        str(files[0]): [Track("Audio", data={"bit_rate": "128000"})],
        str(files[1]): [Track("Audio", data={"BitRate": "64000"})],
        str(files[2]): [Track("General", data={"overall_bit_rate": "96000"})],
        str(files[3]): [Track("General", data={"OverallBitRate": "32000"})],
        str(files[4]): [Track("Audio", data={"bit_rate": "invalid"})],
    }

    def parse_bitrate(path: str):
        return SimpleNamespace(tracks=bitrate_values.get(path, []))

    monkeypatch.setattr(book_preparation.MediaInfo, "parse", parse_bitrate)
    assert asyncio.run(book_preparation.get_audiobook_bitrate([str(path) for path in files])) == 80
    assert asyncio.run(book_preparation.get_audiobook_bitrate([])) is None
    monkeypatch.setattr(book_preparation.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[]))
    assert asyncio.run(book_preparation.get_audiobook_bitrate([str(files[0])])) is None

    monkeypatch.setattr(book_preparation.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[Track("Audio", data={"bit_rate": "999"})]))
    assert asyncio.run(book_preparation.get_audiobook_bitrate([str(files[0])])) == 999


def test_missing_fields_and_embedded_extra_isbn_existing_keywords_and_parser_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = book_preparation.missing_book_fields(Meta(audiobook=False))
    assert missing == ["title", "author", "year", "book_language"]

    path = _book_file(tmp_path, "Alice Writer - Example Book.m4b")
    meta = _meta(
        tmp_path,
        path,
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Album": "Example Book",
                        "Performer": "Alice Writer",
                        "extra": {"ISBN": "9780306406157"},
                        "Genre": "Fiction, Adventure",
                    },
                    {"@type": "Text", "Language": "en"},
                ]
            }
        },
        keywords=["fiction"],
    )
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.isbn == "9780306406157"
    assert meta.keywords == ["fiction", "adventure"]

    class BrokenTrack(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("broken track")

    meta = _meta(tmp_path, path, mediainfo={"media": {"track": [BrokenTrack()]}})
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))


def test_filename_series_and_client_lookup_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Alice Writer - Example Series #2 - Example Book.m4b")
    _patch_external_lookups(monkeypatch)
    monkeypatch.setattr(book_preparation, "export_info", AsyncMock(return_value={}))

    from src.integrations.torrent_clients import client_manager

    class Client:
        calls: ClassVar[list[str]] = []

        def __init__(self, config):
            self.config = config

        async def get_pathed_torrents(self, source: str, _meta: Meta) -> None:
            self.calls.append(source)

    monkeypatch.setattr(client_manager, "Clients", Client)
    meta = _meta(tmp_path, path, edit=False, skip_auto_torrent=False, torrent_comments=[])
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.book_series == "Example Series"
    assert meta.book_series_index == "2"
    assert Client.calls == [str(path)]

    class BrokenClient:
        def __init__(self, _config):
            raise RuntimeError("client failed")

    monkeypatch.setattr(client_manager, "Clients", BrokenClient)
    meta = _meta(tmp_path, path, edit=False, skip_auto_torrent=False, torrent_comments=[])
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))


def test_mam_host_edge_values_and_year_without_search_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path)
    _patch_external_lookups(monkeypatch, mam={"year": 2023, "title": "MAM Title"})
    comments = [
        {"trackers": "", "comment": "MID=0"},
        {"trackers": "http://", "comment": "MID=0"},
        {"trackers": "not-a-host", "comment": "MID=0", "tracker_urls": ["https://myanonamouse.net/announce"]},
    ]
    meta = _meta(tmp_path, path, mediainfo={}, torrent_comments=comments)
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {"mam_id": "key"}}))
    assert meta.year == 2023 and meta.search_year == 2023


def test_google_year_fallback_exception_openlibrary_override_and_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path)
    _patch_external_lookups(monkeypatch, google={"year": 2024, "title": "Google Title"}, openlibrary=None)
    meta = _meta(tmp_path, path, mediainfo={}, isbn="9780306406157")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {"google_books_api_key": "key"}}))
    assert meta.year == 2024 and meta.search_year == 2024

    async def google_failure(*_args, **_kwargs):
        raise RuntimeError("google failed")

    monkeypatch.setattr("src.integrations.external_apis.google_books.google_books_manager.search_by_isbn", google_failure)
    meta = _meta(tmp_path, path, mediainfo={}, isbn="9780306406157")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))

    _patch_external_lookups(monkeypatch, google={"title": "Google Title"}, openlibrary=None)
    meta = _meta(tmp_path, path, mediainfo={}, isbn="9780306406157", book_title="CLI Title")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.title != "Google Title"

    _patch_external_lookups(monkeypatch, google=None, openlibrary={"year": 2025, "title": "Open Title"})
    meta = _meta(tmp_path, path, mediainfo={}, isbn="9780306406157", book_title="CLI Title")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.title != "Open Title"
    assert meta.year == 2025 and meta.search_year == 2025


def test_exact_edition_filename_title_swap_and_unattended_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _book_file(tmp_path, "Alice Writer - Descriptive Local Title.m4b")
    exact = {
        "isbn": "9780306406157",
        "title": "Generic Provider Title",
        "author": "Alice Writer",
        "overview": "Descriptive Local Title is the definitive story",
        "year": 2024,
    }
    _patch_external_lookups(monkeypatch, google=exact, openlibrary=exact)
    meta = _meta(tmp_path, path, mediainfo={}, isbn="9780306406157", title="", author="")
    asyncio.run(book_preparation.gather_book_prep(meta, str(path), str(tmp_path), {"DEFAULT": {}}))
    assert meta.title.endswith("Descriptive Local Title")

    swapped = _book_file(tmp_path, "Alice Writer - Actual Title.m4b")
    meta = _meta(tmp_path, swapped, mediainfo={}, author="Actual Title", title="Alice Writer")
    _patch_external_lookups(monkeypatch)
    asyncio.run(book_preparation.gather_book_prep(meta, str(swapped), str(tmp_path), {"DEFAULT": {}}))
    assert meta.author == "Alice Writer" and meta.title == "Actual Title"

    conflict = _book_file(tmp_path, "Alice Writer - Source Story.m4b")
    meta = _meta(tmp_path, conflict, mediainfo={}, author="Different Author", title="Other Story", unattended=True)
    with pytest.raises(ItemProcessingError, match="conflicts"):
        asyncio.run(book_preparation.gather_book_prep(meta, str(conflict), str(tmp_path), {"DEFAULT": {}}))


def test_bitrate_missing_file_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.mp3"
    monkeypatch.setattr(book_preparation.MediaInfo, "parse", lambda _path: (_ for _ in ()).throw(AssertionError("should not parse missing file")))
    assert asyncio.run(book_preparation.get_audiobook_bitrate([str(missing)])) is None
