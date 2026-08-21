"""Complete boundary coverage for ebook and comic metadata extraction."""

from __future__ import annotations

import builtins
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.integrations.media.book_extractors as extractors

_RICH_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns:opf="http://www.idpf.org/2007/opf"
         unique-identifier="book-id" version="3.0">
  <metadata>
    <dc:identifier id="book-id">urn:isbn:9780306406157</dc:identifier>
    <dc:identifier id="source-id">isbn:0306406152</dc:identifier>
    <dc:title id="title-main" xml:lang="en">Example Book</dc:title>
    <meta refines="#title-main" property="title-type">main</meta>
    <meta refines="#title-main" property="display-seq">1</meta>
    <dc:language>en</dc:language>
    <dc:creator id="author" opf:role="aut">Alice Writer</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <meta refines="#author" property="file-as">Writer, Alice</meta>
    <meta refines="#author" property="display-seq">1</meta>
    <dc:contributor id="editor">Eve Editor</dc:contributor>
    <meta refines="#editor" property="role">edt</meta>
    <meta refines="#editor" property="file-as">Editor, Eve</meta>
    <meta refines="#editor" property="display-seq">2</meta>
    <dc:date opf:event="publication">2024-05-01</dc:date>
    <meta property="dcterms:modified">2024-06-01</meta>
    <dc:source id="source-id">Source Work</dc:source>
    <meta refines="#source-id" property="identifier-type" scheme="onix:codelist5">15</meta>
    <meta refines="#source-id" property="source-of">pagination</meta>
    <dc:type>Text</dc:type>
    <dc:coverage>Worldwide</dc:coverage>
    <dc:description xml:lang="en">A &amp; B</dc:description>
    <dc:format>application/epub+zip</dc:format>
    <dc:publisher>Example Press</dc:publisher>
    <dc:relation>Related Work</dc:relation>
    <dc:rights>Copyright</dc:rights>
    <dc:subject>Fiction</dc:subject>
    <meta name="calibre:series" content="Example Series" />
    <meta name="calibre:series_index" content="5.0" />
  </metadata>
</package>
"""


def _epub(
    path: Path,
    opf: str = _RICH_OPF,
    *,
    include_container: bool = True,
    opf_name: str = "OEBPS/content.opf",
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if include_container:
            archive.writestr(
                "META-INF/container.xml",
                f'<container><rootfiles><rootfile full-path="{opf_name}"/></rootfiles></container>',
            )
        archive.writestr(opf_name, opf)
    return path


def test_zip_safety_and_series_helpers(tmp_path: Path) -> None:
    archive_path = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("small", b"ok")
    with zipfile.ZipFile(archive_path) as archive:
        assert extractors._safe_zip_member_bytes(archive, "small") == b"ok"
        assert extractors._safe_zip_member_bytes(archive, "missing") is None

    assert extractors.normalize_series_index("5.0") == "5"
    assert extractors.normalize_series_index("5.5") == "5.5"
    assert extractors.normalize_series_index("  unknown  ") == "unknown"
    assert extractors.extract_series_from_filename(
        "Author - Series Name #5.0 - Title.epub"
    ) == ("Series Name", "5")
    assert extractors.extract_series_from_filename("No series.epub") == (
        "",
        "",
    )


def test_epub_metadata_and_formatted_output_cover_all_metadata_shapes(
    tmp_path: Path,
) -> None:
    epub = _epub(tmp_path / "rich.epub")

    metadata = extractors.extract_epub_metadata(str(epub))
    assert metadata == {
        "title": "Example Book",
        "author": "Alice Writer",
        "book_language_raw": "en",
        "year": "2024",
        "isbn": "9780306406157",
        "overview": "A & B",
        "publisher": "Example Press",
        "book_series": "Example Series",
        "book_series_index": "5",
    }

    output = extractors.get_epubmeta_output(str(epub))
    assert output is not None
    for expected in (
        "version: 3.0",
        "unique-identifier: book-id",
        "identifier-type: 15",
        "title-type: main",
        "creator",
        "contributor",
        "event: created",
        "event: modified",
        "source-of: pagination",
        "type: Text",
        "coverage: Worldwide",
        "description",
        "format: application/epub+zip",
        "publisher: Example Press",
        "relation: Related Work",
        "rights: Copyright",
        "subject: Fiction",
    ):
        assert expected in output


def test_epub_fallbacks_and_malformed_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = _epub(
        tmp_path / "fallback.epub",
        include_container=False,
        opf_name="content.opf",
    )
    assert (
        extractors.extract_epub_metadata(str(fallback))["title"]
        == "Example Book"
    )
    assert "title" in (extractors.get_epubmeta_output(str(fallback)) or "")

    no_opf = tmp_path / "no-opf.epub"
    with zipfile.ZipFile(no_opf, "w") as archive:
        archive.writestr("META-INF/container.xml", "<broken")
    assert extractors.extract_epub_metadata(str(no_opf)) == {}
    assert extractors.get_epubmeta_output(str(no_opf)) is None

    malformed = _epub(tmp_path / "malformed.epub", "<not-xml")
    assert extractors.extract_epub_metadata(str(malformed)) == {}
    assert extractors.get_epubmeta_output(str(malformed)) is None
    assert (
        extractors.extract_epub_metadata(str(tmp_path / "missing.epub")) == {}
    )
    assert (
        extractors.get_epubmeta_output(str(tmp_path / "missing.epub")) is None
    )

    huge = tmp_path / "huge.epub"
    with zipfile.ZipFile(huge, "w") as archive:
        for index in range(4097):
            archive.writestr(f"{index}.txt", "x")
    assert extractors.extract_epub_metadata(str(huge)) == {}
    assert extractors.get_epubmeta_output(str(huge)) is None

    with zipfile.ZipFile(fallback) as archive:
        monkeypatch.setattr(
            archive,
            "read",
            lambda _name: (_ for _ in ()).throw(OSError("read failed")),
        )
        with pytest.raises(OSError):
            archive.read("content.opf")


def test_comic_metadata_for_cbz_cbr_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comic_xml = b"""<ComicInfo><Series>Series</Series><Title>Issue</Title><Writer>Writer</Writer>
    <Penciller>Penciller</Penciller><Publisher>Publisher</Publisher><Year>2025</Year>
    <LanguageISO>pt-BR</LanguageISO><Summary>Summary</Summary><Genre>Action, Adventure</Genre></ComicInfo>"""
    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.writestr("folder/ComicInfo.xml", comic_xml)
    assert extractors.extract_cbr_cbz_metadata(str(cbz)) == {
        "title": "Series",
        "author": "Writer",
        "publisher": "Publisher",
        "year": "2025",
        "book_language_raw": "pt-BR",
        "overview": "Summary",
        "keywords": ["Action", "Adventure"],
        "genres": ["Action", "Adventure"],
    }

    title_only = tmp_path / "title-only.cbz"
    with zipfile.ZipFile(title_only, "w") as archive:
        archive.writestr(
            "ComicInfo.xml",
            b"<ComicInfo><Title>Issue</Title><Penciller>Artist</Penciller></ComicInfo>",
        )
    assert extractors.extract_cbr_cbz_metadata(str(title_only)) == {
        "title": "Issue",
        "author": "Artist",
    }

    class FakeRar:
        def __init__(self, _path: str, _mode: str) -> None:
            pass

        def __enter__(self) -> FakeRar:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def namelist(self) -> list[str]:
            return ["ComicInfo.xml"]

        def read(self, _name: str) -> bytes:
            return comic_xml

    monkeypatch.setattr(
        extractors, "rarfile", SimpleNamespace(RarFile=FakeRar)
    )
    cbr = tmp_path / "comic.cbr"
    cbr.write_bytes(b"rar")
    assert extractors.extract_cbr_cbz_metadata(str(cbr))["title"] == "Series"
    monkeypatch.setattr(extractors, "rarfile", None)
    assert extractors.extract_cbr_cbz_metadata(str(cbr)) == {}

    broken = tmp_path / "broken.cbz"
    broken.write_bytes(b"not zip")
    assert extractors.extract_cbr_cbz_metadata(str(broken)) == {}
    assert (
        extractors.extract_cbr_cbz_metadata(str(tmp_path / "missing.cbz"))
        == {}
    )

    malformed = tmp_path / "malformed.cbz"
    with zipfile.ZipFile(malformed, "w") as archive:
        archive.writestr("ComicInfo.xml", b"<broken")
    assert extractors.extract_cbr_cbz_metadata(str(malformed)) == {}


def test_mobi_metadata_success_parse_failure_import_failure_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.mobi"
    source.write_bytes(b"mobi")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    opf = extracted / "content.opf"
    opf.write_text(
        """<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata>
        <dc:title>Book</dc:title><dc:creator>Author</dc:creator><dc:language>en</dc:language>
        <dc:date>2023-01-01</dc:date><dc:identifier>urn:isbn:9780306406157</dc:identifier>
        <dc:description>&lt;b&gt;Overview&lt;/b&gt; &amp;amp; detail</dc:description><dc:publisher>Press</dc:publisher>
        </metadata></package>""",
        encoding="utf-8",
    )
    fake_mobi = types.ModuleType("mobi")
    fake_mobi.extract = lambda _path: (str(extracted), None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mobi", fake_mobi)
    monkeypatch.setattr(extractors.shutil, "rmtree", lambda _path: None)
    assert extractors.extract_mobi_metadata(str(source)) == {
        "title": "Book",
        "author": "Author",
        "book_language_raw": "en",
        "year": "2023",
        "isbn": "9780306406157",
        "overview": "Overview & detail",
        "publisher": "Press",
    }

    opf.write_bytes(b"\xff<broken")
    assert extractors.extract_mobi_metadata(str(source)) == {}
    fake_mobi.extract = lambda _path: (_ for _ in ()).throw(
        OSError("extract failed")
    )  # type: ignore[attr-defined]
    assert extractors.extract_mobi_metadata(str(source)) == {}
    assert (
        extractors.extract_mobi_metadata(str(tmp_path / "missing.mobi")) == {}
    )

    real_import = builtins.__import__

    def missing_mobi(name: str, *args: object, **kwargs: object) -> Any:
        if name == "mobi":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "mobi", raising=False)
    monkeypatch.setattr(builtins, "__import__", missing_mobi)
    assert extractors.extract_mobi_metadata(str(source)) == {}


def test_isbn_pdf_and_date_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        extractors.validate_isbn_checksum("978-0-306-40615-7")
        == "9780306406157"
    )
    assert extractors.validate_isbn_checksum("0-306-40615-2") == "0306406152"
    assert extractors.validate_isbn_checksum("invalid") is None
    assert extractors.validate_isbn_checksum("9780306406158") is None
    assert extractors.validate_isbn_checksum("0306406153") is None

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")

    class Page:
        def __init__(self, text: object) -> None:
            self.text = text

        def get_text(self) -> object:
            return self.text

    class Document:
        def __init__(self, pages: list[Page]) -> None:
            self.pages = pages

        def __len__(self) -> int:
            return len(self.pages)

        def __getitem__(self, index: int) -> Page:
            return self.pages[index]

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    tools = SimpleNamespace(mupdf_display_errors=lambda _enabled: None)
    fake_fitz = types.ModuleType("fitz")
    fake_fitz.TOOLS = tools  # type: ignore[attr-defined]
    fake_fitz.open = lambda _path: Document(
        [Page("front"), Page("ISBN 978-0-306-40615-7"), Page(123)]
    )  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    assert extractors.extract_pdf_page_count(str(pdf)) == 3
    assert extractors.extract_isbn_from_pdf(str(pdf)) == "9780306406157"

    fake_fitz.open = lambda _path: Document([])  # type: ignore[attr-defined]
    assert extractors.extract_pdf_page_count(str(pdf)) is None
    assert extractors.extract_isbn_from_pdf(str(pdf)) is None
    fake_fitz.open = lambda _path: (_ for _ in ()).throw(OSError("bad pdf"))  # type: ignore[attr-defined]
    assert extractors.extract_pdf_page_count(str(pdf)) is None
    assert extractors.extract_isbn_from_pdf(str(pdf)) is None
    assert (
        extractors.extract_pdf_page_count(str(tmp_path / "missing.pdf"))
        is None
    )
    assert (
        extractors.extract_isbn_from_pdf(str(tmp_path / "missing.pdf")) is None
    )

    assert extractors.date_event_from_str(None) == "Epub"
    expected = {
        "dcterms:available": "Available",
        "dcterms:created": "Created",
        "publication": "Created",
        "dcterms:date": "Date",
        "dcterms:dateaccepted": "DateAccepted",
        "dcterms:datecopyrighted": "DateCopyrighted",
        "dcterms:datesubmitted": "DateSubmitted",
        "dcterms:issued": "Issued",
        "original-publication": "Issued",
        "dcterms:modified": "Modified",
        "dcterms:valid": "Valid",
    }
    for value, result in expected.items():
        assert extractors.date_event_from_str(value) == result
    assert extractors.date_event_from_str("unknown") is None

    element = extractors.ET.fromstring(
        '<node plain="value" xmlns:x="urn:x" x:namespaced="other"/>'
    )
    assert extractors.get_attr_ignore_ns(element, "plain") == "value"
    assert extractors.get_attr_ignore_ns(element, "namespaced") == "other"
    assert extractors.get_attr_ignore_ns(element, "missing") is None


def test_book_extractor_optional_import_and_archive_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy

    real_import = builtins.__import__

    def no_rarfile(name: str, *args: object, **kwargs: object) -> Any:
        if name == "rarfile":
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(builtins, "__import__", no_rarfile)
        namespace = runpy.run_path(str(Path(extractors.__file__)))
    assert namespace["rarfile"] is None

    oversized = tmp_path / "oversized.zip"
    with zipfile.ZipFile(
        oversized, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "payload", b"0" * (extractors._MAX_EPUB_MEMBER_SIZE + 1)
        )
    with zipfile.ZipFile(oversized) as archive:
        assert extractors._safe_zip_member_bytes(archive, "payload") is None

    class BrokenRar:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("broken archive")

    cbr = tmp_path / "broken.cbr"
    cbr.write_bytes(b"rar")
    monkeypatch.setattr(
        extractors, "rarfile", SimpleNamespace(RarFile=BrokenRar)
    )
    assert extractors.extract_cbr_cbz_metadata(str(cbr)) == {}


def test_epub_output_minimal_and_rejected_opf_members(tmp_path: Path) -> None:
    simple_opf = """<package version="2.0"><metadata>
    <identifier></identifier><title>Simple</title><creator>Author</creator>
    <contributor>Editor</contributor><source>Source</source><description>Description</description>
    </metadata></package>"""
    simple = _epub(tmp_path / "simple.epub", simple_opf)
    output = extractors.get_epubmeta_output(str(simple))
    assert output is not None
    for line in (
        "title: Simple",
        "creator: Author",
        "contributor: Editor",
        "source: Source",
        "description: Description",
    ):
        assert line in output

    no_metadata = _epub(
        tmp_path / "no-metadata.epub",
        '<package version="2.0"><manifest/></package>',
    )
    assert extractors.get_epubmeta_output(str(no_metadata)) is None

    oversized_opf = tmp_path / "oversized-opf.epub"
    with zipfile.ZipFile(
        oversized_opf, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfile full-path="content.opf"/></container>',
        )
        archive.writestr(
            "content.opf", "0" * (extractors._MAX_EPUB_MEMBER_SIZE + 1)
        )
    assert extractors.extract_epub_metadata(str(oversized_opf)) == {}
    assert extractors.get_epubmeta_output(str(oversized_opf)) is None


def test_mobi_isbn_prefix_and_pdf_import_and_pagination_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "alternate.mobi"
    source.write_bytes(b"mobi")
    extracted = tmp_path / "mobi-output"
    extracted.mkdir()
    (extracted / "alternate.opf").write_text(
        """<package><metadata><identifier>isbn:0306406152</identifier>
        <date>0101-01-01</date><description>Plain</description></metadata></package>""",
        encoding="utf-8",
    )
    fake_mobi = types.ModuleType("mobi")
    fake_mobi.extract = lambda _path: (str(extracted), None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mobi", fake_mobi)
    monkeypatch.setattr(extractors.shutil, "rmtree", lambda _path: None)
    assert (
        extractors.extract_mobi_metadata(str(source))["isbn"] == "0306406152"
    )

    pdf = tmp_path / "long.pdf"
    pdf.write_bytes(b"pdf")

    class Page:
        def __init__(self, text: object) -> None:
            self._text = text

        def get_text(self) -> object:
            return self._text

    class Document:
        def __init__(self) -> None:
            self.pages = [Page(123), *[Page("") for _ in range(99)]]
            self.pages[50] = Page("ISBN-13: 9780306406157")

        def __len__(self) -> int:
            return len(self.pages)

        def __getitem__(self, index: int) -> Page:
            return self.pages[index]

        def __enter__(self) -> Document:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    fake_fitz = types.ModuleType("fitz")
    fake_fitz.TOOLS = SimpleNamespace(
        mupdf_display_errors=lambda _enabled: (_ for _ in ()).throw(
            RuntimeError("ignored")
        )
    )  # type: ignore[attr-defined]
    fake_fitz.open = lambda _path: Document()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    assert extractors.extract_isbn_from_pdf(str(pdf)) == "9780306406157"

    real_import = builtins.__import__

    def no_fitz(name: str, *args: object, **kwargs: object) -> Any:
        if name == "fitz":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "fitz", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_fitz)
    assert extractors.extract_pdf_page_count(str(pdf)) is None
    assert extractors.extract_isbn_from_pdf(str(pdf)) is None
