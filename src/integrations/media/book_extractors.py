# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Extractors for book and audiobook files metadata."""

from __future__ import annotations

import contextlib
import html
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

try:
    import rarfile
except ImportError:  # Optional CBR support.
    rarfile = None  # type: ignore[assignment]

from src.integrations.observability.runtime_support import logger

_MAX_EPUB_MEMBER_SIZE = 2 * 1024 * 1024
_MAX_EPUB_COMPRESSION_RATIO = 100
_EPUB_TEXT_FIELDS = {
    "title": "title",
    "language": "language",
    "description": "description",
    "publisher": "publisher",
}

_DATE_EVENT_NAMES = {
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


def _safe_zip_member_bytes(
    archive: zipfile.ZipFile, name: str
) -> bytes | None:
    try:
        member = archive.getinfo(name)
    except KeyError:
        return None
    if (
        member.file_size > _MAX_EPUB_MEMBER_SIZE
        or member.file_size
        > max(member.compress_size, 1) * _MAX_EPUB_COMPRESSION_RATIO
    ):
        return None
    return archive.read(member)


def normalize_series_index(value: str) -> str:
    """Drop a trailing .0 from a series index ("5.0" -> "5"), keeping "5.5"/"0.5"."""
    try:
        idx = float(value)
    except TypeError, ValueError:
        return (value).strip()
    return str(int(idx)) if idx.is_integer() else str(idx)


@dataclass(slots=True)
class _EpubMetadataState:
    title: str = ""
    creators: list[tuple[str, str, str]] = field(default_factory=list)
    creator_roles: dict[str, str] = field(default_factory=dict)
    language: str = ""
    date: str = ""
    identifiers: list[tuple[str, str]] = field(default_factory=list)
    description: str = ""
    publisher: str = ""
    series: str = ""
    series_index: str = ""


def _rootfile_path_from_container(container_data: bytes) -> str | None:
    root = ET.fromstring(container_data)
    for element in root.iter():
        if not element.tag.endswith("rootfile"):
            continue
        path = element.attrib.get("full-path")
        if path:
            return path
    return None


def _epub_container_rootfile(archive: zipfile.ZipFile) -> str | None:
    try:
        container_data = _safe_zip_member_bytes(
            archive, "META-INF/container.xml"
        )
        if container_data is None:
            raise ValueError("unsafe or missing EPUB container metadata")
        return _rootfile_path_from_container(container_data)
    except Exception as error:
        logger.debug(
            f"[yellow]Debug: META-INF/container.xml not found or unreadable: {error}[/yellow]"
        )
        return None


def _epub_fallback_rootfile(archive: zipfile.ZipFile) -> str | None:
    for name in archive.namelist():
        if name.endswith(".opf"):
            return name
    return None


def _epub_rootfile_path(archive: zipfile.ZipFile) -> str | None:
    return _epub_container_rootfile(archive) or _epub_fallback_rootfile(
        archive
    )


def _epub_element_text(element: ET.Element) -> str:
    return (element.text or "").strip()


def _epub_inline_role(element: ET.Element) -> str:
    for key, value in element.attrib.items():
        if key.split("}")[-1] == "role":
            return value.strip().lower()
    return ""


def _record_epub_creator(
    state: _EpubMetadataState, element: ET.Element
) -> None:
    creator = _epub_element_text(element)
    if not creator:
        return
    creator_id = element.attrib.get("id", "").strip()
    state.creators.append((creator_id, creator, _epub_inline_role(element)))


def _record_epub_date(state: _EpubMetadataState, element: ET.Element) -> None:
    event = str(get_attr_ignore_ns(element, "event") or "").strip().casefold()
    if event in {"modification", "modified", "dcterms:modified"}:
        return
    state.date = _epub_element_text(element)


def _record_epub_identifier(
    state: _EpubMetadataState, element: ET.Element
) -> None:
    state.identifiers.append(
        (element.attrib.get("id", "").strip(), _epub_element_text(element))
    )


def _epub_attribute_text(element: ET.Element, key: str) -> str:
    value = element.attrib.get(key)
    return "" if value is None else value


def _epub_role_meta_value(element: ET.Element) -> tuple[str, str] | None:
    property_name = _epub_attribute_text(element, "property").lower()
    if property_name != "role":
        return None
    refined_id = _epub_attribute_text(element, "refines").lstrip("#").strip()
    if not refined_id:
        return None
    role = (
        element.text
        if element.text is not None
        else _epub_attribute_text(element, "content")
    )
    return refined_id, role.strip().lower()


def _record_epub_role_meta(
    state: _EpubMetadataState, element: ET.Element
) -> bool:
    role_meta = _epub_role_meta_value(element)
    if role_meta is None:
        return False
    refined_id, role = role_meta
    state.creator_roles[refined_id] = role
    return True


def _record_epub_series_meta(
    state: _EpubMetadataState, element: ET.Element
) -> None:
    meta_name = (element.attrib.get("name") or "").lower()
    content = (element.attrib.get("content") or "").strip()
    if meta_name == "calibre:series":
        state.series = content
    elif meta_name == "calibre:series_index":
        state.series_index = content


def _record_epub_meta(state: _EpubMetadataState, element: ET.Element) -> None:
    if _record_epub_role_meta(state, element):
        return
    _record_epub_series_meta(state, element)


def _record_epub_text_field(
    state: _EpubMetadataState, tag_local: str, element: ET.Element
) -> bool:
    attribute = _EPUB_TEXT_FIELDS.get(tag_local)
    if attribute is None:
        return False
    setattr(state, attribute, _epub_element_text(element))
    return True


def _record_epub_structured_element(
    state: _EpubMetadataState, tag_local: str, element: ET.Element
) -> None:
    if tag_local == "creator":
        _record_epub_creator(state, element)
    elif tag_local == "date":
        _record_epub_date(state, element)
    elif tag_local == "identifier":
        _record_epub_identifier(state, element)
    elif tag_local == "meta":
        _record_epub_meta(state, element)


def _record_epub_element(
    state: _EpubMetadataState, element: ET.Element
) -> None:
    tag_local = element.tag.split("}")[-1]
    if _record_epub_text_field(state, tag_local, element):
        return
    _record_epub_structured_element(state, tag_local, element)


def _parse_epub_state(root: ET.Element) -> _EpubMetadataState:
    state = _EpubMetadataState()
    for element in root.iter():
        _record_epub_element(state, element)
    return state


def _epub_author(state: _EpubMetadataState) -> str:
    for creator_id, creator, inline_role in state.creators:
        if (
            inline_role == "aut"
            or state.creator_roles.get(creator_id) == "aut"
        ):
            return creator
    return state.creators[0][1] if state.creators else ""


def _epub_year(date: str) -> str:
    if not date or date.startswith("0101-01-01"):
        return ""
    match = re.search(r"\b\d{4}\b", date)
    return match.group(0) if match else ""


def _unique_epub_identifiers(
    identifiers: list[tuple[str, str]], unique_id: str
) -> list[str]:
    if not unique_id:
        return []
    return [
        value
        for identifier_id, value in identifiers
        if identifier_id == unique_id
    ]


def _prefixed_isbn_identifiers(
    identifiers: list[tuple[str, str]],
) -> list[str]:
    return [
        value
        for _identifier_id, value in identifiers
        if value.lower().startswith(("urn:isbn:", "isbn:"))
    ]


def _ordered_epub_identifiers(
    identifiers: list[tuple[str, str]], unique_id: str
) -> list[str]:
    ordered = _unique_epub_identifiers(identifiers, unique_id)
    ordered.extend(_prefixed_isbn_identifiers(identifiers))
    ordered.extend(value for _identifier_id, value in identifiers)
    return ordered


def _epub_isbn(state: _EpubMetadataState, unique_id: str) -> str:
    for identifier in _ordered_epub_identifiers(state.identifiers, unique_id):
        cleaned = re.sub(r"[^\dXx]", "", identifier).upper()
        if validate_isbn_checksum(cleaned):
            return cleaned
    return ""


def _set_book_metadata_value(
    metadata: dict[str, Any], key: str, value: str
) -> None:
    if value:
        metadata[key] = value


def _metadata_from_epub_state(
    state: _EpubMetadataState, unique_id: str
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    _set_book_metadata_value(metadata, "title", state.title)
    _set_book_metadata_value(metadata, "author", _epub_author(state))
    _set_book_metadata_value(metadata, "book_language_raw", state.language)
    _set_book_metadata_value(metadata, "year", _epub_year(state.date))
    _set_book_metadata_value(metadata, "isbn", _epub_isbn(state, unique_id))
    _set_book_metadata_value(metadata, "overview", state.description)
    _set_book_metadata_value(metadata, "publisher", state.publisher)
    _set_book_metadata_value(metadata, "book_series", state.series)
    series_index = (
        normalize_series_index(state.series_index)
        if state.series_index
        else ""
    )
    _set_book_metadata_value(metadata, "book_series_index", series_index)
    return metadata


def _extract_epub_archive_metadata(archive: zipfile.ZipFile) -> dict[str, Any]:
    if len(archive.infolist()) > 4096:
        return {}
    rootfile_path = _epub_rootfile_path(archive)
    if not rootfile_path:
        logger.debug(
            "[yellow]Debug: No OPF metadata file found in EPUB ZIP[/yellow]"
        )
        return {}
    opf_data = _safe_zip_member_bytes(archive, rootfile_path)
    if opf_data is None:
        return {}
    root = ET.fromstring(opf_data)
    unique_id = get_attr_ignore_ns(root, "unique-identifier") or ""
    return _metadata_from_epub_state(_parse_epub_state(root), unique_id)


def extract_epub_metadata(epub_path: str) -> dict[str, Any]:
    """Extract metadata from an EPUB zip container's OPF file."""
    if not Path(epub_path).is_file() or not zipfile.is_zipfile(epub_path):
        return {}
    try:
        with zipfile.ZipFile(epub_path, "r") as archive:
            return _extract_epub_archive_metadata(archive)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error parsing EPUB metadata: {error}[/yellow]"
        )
        return {}


def extract_series_from_filename(filename: str) -> tuple[str, str]:
    """Parse (series, index) from a filename like "Author - Series #5 - Title", or ("", "")."""
    name = Path(filename).stem
    match = re.search(r"[-–]\s*([^-–#\[\]]+?)\s*#\s*(\d+(?:\.\d+)?)", name)  # noqa: RUF001
    if not match:
        return "", ""
    return match.group(1).strip(), normalize_series_index(match.group(2))


_COMIC_INFO_FIELDS = {
    "Series": "series",
    "Title": "title",
    "Writer": "writer",
    "Penciller": "penciller",
    "Publisher": "publisher",
    "Year": "year",
    "LanguageISO": "language_iso",
    "Summary": "summary",
    "Genre": "genre",
}


def _comic_info_name(names: list[str]) -> str | None:
    return next(
        (name for name in names if name.lower().endswith("comicinfo.xml")),
        None,
    )


def _read_cbz_comic_info(filepath: str) -> bytes | None:
    try:
        with zipfile.ZipFile(filepath, "r") as archive:
            xml_name = _comic_info_name(archive.namelist())
            return archive.read(xml_name) if xml_name else None
    except Exception as error:
        logger.debug(
            f"[yellow]Debug: Error reading CBZ zip archive: {error}[/yellow]"
        )
        return None


def _read_cbr_comic_info(filepath: str) -> bytes | None:
    if rarfile is None:
        logger.debug(
            "[yellow]Debug: rarfile library not available for CBR metadata extraction.[/yellow]"
        )
        return None
    try:
        with rarfile.RarFile(filepath, "r") as archive:
            xml_name = _comic_info_name(archive.namelist())
            if not xml_name:
                return None
            archive_api = cast(Any, archive)
            return bytes(archive_api.read(xml_name))
    except Exception as error:
        logger.debug(
            f"[yellow]Debug: Error reading CBR rar archive: {error}[/yellow]"
        )
        return None


def _comic_info_bytes(filepath: str) -> bytes | None:
    extension = Path(filepath).suffix.lower()
    if extension == ".cbz" or zipfile.is_zipfile(filepath):
        return _read_cbz_comic_info(filepath)
    if extension == ".cbr":
        return _read_cbr_comic_info(filepath)
    return None


def _comic_info_values(root: ET.Element) -> dict[str, str]:
    values = dict.fromkeys(_COMIC_INFO_FIELDS.values(), "")
    for element in root.iter():
        field = _COMIC_INFO_FIELDS.get(element.tag.split("}")[-1])
        if field is not None:
            values[field] = _epub_element_text(element)
    return values


def _comic_year(value: str) -> str:
    match = re.search(r"\b\d{4}\b", value)
    return match.group(0) if match else ""


def _comic_genres(value: str) -> list[str]:
    return [genre.strip() for genre in value.split(",") if genre.strip()]


def _comic_info_metadata(values: dict[str, str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    _set_book_metadata_value(
        metadata, "title", values["series"] or values["title"]
    )
    _set_book_metadata_value(
        metadata, "author", values["writer"] or values["penciller"]
    )
    _set_book_metadata_value(metadata, "publisher", values["publisher"])
    _set_book_metadata_value(metadata, "year", _comic_year(values["year"]))
    _set_book_metadata_value(
        metadata, "book_language_raw", values["language_iso"]
    )
    _set_book_metadata_value(metadata, "overview", values["summary"])
    genres = _comic_genres(values["genre"])
    if genres:
        metadata["keywords"] = metadata["genres"] = genres
    return metadata


def _parse_comic_info_metadata(xml_data: bytes) -> dict[str, Any]:
    try:
        return _comic_info_metadata(
            _comic_info_values(ET.fromstring(xml_data))
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error parsing ComicInfo.xml metadata: {error}[/yellow]"
        )
        return {}


def extract_cbr_cbz_metadata(filepath: str) -> dict[str, Any]:
    """Extract metadata from a CBR (RAR) or CBZ (ZIP) ComicInfo.xml file."""
    if not Path(filepath).is_file():
        return {}
    xml_data = _comic_info_bytes(filepath)
    if not xml_data:
        return {}
    return _parse_comic_info_metadata(xml_data)


_MOBI_TEXT_FIELDS = {
    "title": "title",
    "creator": "author",
    "language": "language",
    "date": "date",
    "description": "description",
    "publisher": "publisher",
}


@dataclass(slots=True)
class _MobiMetadataState:
    title: str = ""
    author: str = ""
    language: str = ""
    date: str = ""
    identifier: str = ""
    description: str = ""
    publisher: str = ""


def _find_mobi_opf(tempdir: str | os.PathLike[str]) -> Path | None:
    for root, _directories, files in os.walk(tempdir):
        for filename in files:
            if filename.endswith(".opf"):
                return Path(root) / filename
    return None


def _parse_mobi_xml(opf_data: bytes) -> ET.Element | None:
    try:
        return ET.fromstring(opf_data)
    except Exception:
        try:
            decoded = opf_data.decode("utf-8", errors="replace")
            return ET.fromstring(decoded.encode("utf-8"))
        except Exception as error:
            logger.debug(
                f"[yellow]Debug: Error parsing MOBI XML data: {error}[/yellow]"
            )
            return None


def _mobi_identifier_value(value: str) -> str:
    lowered = value.lower()
    if lowered.startswith("urn:isbn:"):
        return value[9:]
    if lowered.startswith("isbn:"):
        return value[5:]
    return ""


def _record_mobi_element(
    state: _MobiMetadataState, element: ET.Element
) -> None:
    tag_local = element.tag.split("}")[-1]
    text = _epub_element_text(element)
    if tag_local == "identifier":
        identifier = _mobi_identifier_value(text)
        if identifier:
            state.identifier = identifier
        return
    attribute = _MOBI_TEXT_FIELDS.get(tag_local)
    if attribute is not None:
        setattr(state, attribute, text)


def _parse_mobi_state(root: ET.Element) -> _MobiMetadataState:
    state = _MobiMetadataState()
    for element in root.iter():
        _record_mobi_element(state, element)
    return state


def _mobi_year(value: str) -> str:
    if not value or value.startswith("0101-01-01"):
        return ""
    match = re.search(r"\b\d{4}\b", value)
    return match.group(0) if match else ""


def _mobi_isbn(value: str) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^\d]", "", value)
    return cleaned if len(cleaned) in (10, 13) else ""


def _mobi_description(value: str) -> str:
    if not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", "", value)
    return html.unescape(without_tags).strip()


def _metadata_from_mobi_state(state: _MobiMetadataState) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    _set_book_metadata_value(metadata, "title", state.title)
    _set_book_metadata_value(metadata, "author", state.author)
    _set_book_metadata_value(metadata, "book_language_raw", state.language)
    _set_book_metadata_value(metadata, "year", _mobi_year(state.date))
    _set_book_metadata_value(metadata, "isbn", _mobi_isbn(state.identifier))
    _set_book_metadata_value(
        metadata, "overview", _mobi_description(state.description)
    )
    _set_book_metadata_value(metadata, "publisher", state.publisher)
    return metadata


def _metadata_from_mobi_opf(opf_path: Path | None) -> dict[str, Any]:
    if opf_path is None or not opf_path.is_file():
        return {}
    opf_data = opf_path.read_bytes()
    root = _parse_mobi_xml(opf_data)
    if root is None:
        return {}
    return _metadata_from_mobi_state(_parse_mobi_state(root))


def _mobi_extract_tempdir(mobi_api: Any, mobi_path: str) -> str:
    tempdir, _output = mobi_api.extract(mobi_path)
    return str(tempdir)


def _cleanup_mobi_tempdir(tempdir: str | None) -> None:
    if not tempdir:
        return
    path = Path(tempdir)
    if not path.exists():
        return
    with contextlib.suppress(Exception):
        shutil.rmtree(path)


def extract_mobi_metadata(mobi_path: str) -> dict[str, Any]:
    """Extract metadata from a MOBI file using its extracted OPF."""
    if not Path(mobi_path).is_file():
        return {}
    try:
        import mobi
    except ImportError:
        logger.debug(
            "[yellow]Debug: mobi library is not installed. Skipping MOBI metadata extraction.[/yellow]"
        )
        return {}
    tempdir: str | None = None
    try:
        tempdir = _mobi_extract_tempdir(cast(Any, mobi), mobi_path)
        return _metadata_from_mobi_opf(_find_mobi_opf(tempdir))
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error parsing MOBI metadata: {error}[/yellow]"
        )
        return {}
    finally:
        _cleanup_mobi_tempdir(tempdir)


def _valid_isbn13(value: str) -> bool:
    if len(value) != 13 or not value.isdigit():
        return False
    total = sum(
        int(value[index]) * (1 if index % 2 == 0 else 3) for index in range(13)
    )
    return total % 10 == 0


def _valid_isbn10_shape(value: str) -> bool:
    if len(value) != 10 or not value[:9].isdigit():
        return False
    return value[9].isdigit() or value[9] == "X"


def _isbn10_digit(value: str, index: int) -> int:
    return 10 if value[index] == "X" else int(value[index])


def _valid_isbn10(value: str) -> bool:
    if not _valid_isbn10_shape(value):
        return False
    total = sum(
        _isbn10_digit(value, index) * (10 - index) for index in range(10)
    )
    return total % 11 == 0


def validate_isbn_checksum(candidate: str) -> str | None:
    """Validate and return cleaned ISBN-10 or ISBN-13 if valid, else None."""
    cleaned = re.sub(r"[- ]", "", candidate).upper()
    if _valid_isbn13(cleaned) or _valid_isbn10(cleaned):
        return cleaned
    return None


def extract_pdf_page_count(pdf_path: str) -> int | None:
    try:
        import fitz
    except ImportError:
        return None

    if not Path(pdf_path).is_file():
        return None

    try:
        with fitz.open(pdf_path) as doc:
            return len(doc) or None
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error reading PDF page count: {error}[/yellow]"
        )
        return None


def _pdf_page_order(num_pages: int) -> list[int]:
    front_limit = min(30, num_pages)
    back_limit = max(0, num_pages - 30)
    ordered = list(range(front_limit))
    seen = set(ordered)
    for page_num in [*range(back_limit, num_pages), *range(num_pages)]:
        if page_num not in seen:
            ordered.append(page_num)
            seen.add(page_num)
    return ordered


def _isbn_candidates_from_text(text: str) -> list[str]:
    labelled = re.findall(
        r"\bISBN(?:-1[03])?:?\s*((?:97[89][- ]?)?\d(?:[- ]?\d){8,11}[- ]?[\dX])\b",
        text,
        re.IGNORECASE,
    )
    bare_isbn13 = re.findall(r"\b(97[89](?:[- ]?\d){10})\b", text)
    return list(dict.fromkeys([*labelled, *bare_isbn13]))


def _validated_isbn_from_text(text: object) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    for candidate in _isbn_candidates_from_text(text):
        validated = validate_isbn_checksum(candidate)
        if validated is not None:
            return validated
    return None


def _isbn_from_pdf_document(document: Any) -> str | None:
    for page_num in _pdf_page_order(len(document)):
        validated = _validated_isbn_from_text(document[page_num].get_text())
        if validated is None:
            continue
        logger.info(
            f"[cyan]Found valid ISBN {validated} on PDF page {page_num}[/cyan]"
        )
        return validated
    return None


def _disable_mupdf_errors(fitz: Any) -> None:
    with contextlib.suppress(Exception):
        fitz.TOOLS.mupdf_display_errors(False)


def extract_isbn_from_pdf(pdf_path: str) -> str | None:
    """Search for and extract a valid ISBN from a PDF file using PyMuPDF (fitz)."""
    try:
        import fitz
    except ImportError:
        logger.debug(
            "[yellow]Debug: PyMuPDF (fitz) is not installed. Skipping PDF ISBN extraction.[/yellow]"
        )
        return None
    if not Path(pdf_path).is_file():
        return None
    try:
        _disable_mupdf_errors(fitz)
        with fitz.open(pdf_path) as document:
            if len(document) == 0:
                return None
            return _isbn_from_pdf_document(document)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error extracting ISBN from PDF: {error}[/yellow]"
        )
        return None


def date_event_from_str(event_str: str | None) -> str | None:
    if not event_str:
        return "Epub"
    return _DATE_EVENT_NAMES.get(event_str.strip().lower())


def get_attr_ignore_ns(elem: ET.Element, attr_name: str) -> str | None:
    if attr_name in elem.attrib:
        return elem.attrib[attr_name]
    for k, v in elem.attrib.items():
        if k.split("}")[-1] == attr_name:
            return v
    return None


@dataclass(slots=True, frozen=True)
class _EpubRefinement:
    ref_id: str
    ref_prop: str
    ref_scheme: str
    ref_text: str


@dataclass(slots=True, frozen=True)
class _EpubOutputIdentifier:
    identifier_id: str | None
    identifier_type: str | None
    scheme: str | None
    text: str


@dataclass(slots=True, frozen=True)
class _EpubOutputTitle:
    lang: str | None
    title_type: str | None
    title_seq: int | None
    text: str


@dataclass(slots=True, frozen=True)
class _EpubOutputAgent:
    role: str | None
    file_as: str | None
    creator_seq: int | None
    text: str


@dataclass(slots=True, frozen=True)
class _EpubOutputSource:
    identifier_type: str | None
    scheme: str | None
    source_of: str | None
    text: str


@dataclass(slots=True, frozen=True)
class _EpubOutputDescription:
    lang: str | None
    text: str


@dataclass(slots=True)
class _EpubMetaOutputState:
    version: str = ""
    unique_id: str = ""
    identifiers: list[_EpubOutputIdentifier] = field(default_factory=list)
    titles: list[_EpubOutputTitle] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    contributors: list[_EpubOutputAgent] = field(default_factory=list)
    creators: list[_EpubOutputAgent] = field(default_factory=list)
    dates: dict[str, str] = field(default_factory=dict)
    sources: list[_EpubOutputSource] = field(default_factory=list)
    media_type: str | None = None
    coverages: list[str] = field(default_factory=list)
    descriptions: list[_EpubOutputDescription] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    rights: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)


_DATE_EVENT_OUTPUT_ORDER = (
    "Available",
    "Created",
    "Date",
    "DateAccepted",
    "DateCopyrighted",
    "DateSubmitted",
    "Epub",
    "Issued",
    "Modified",
    "Valid",
)
_DATE_EVENT_OUTPUT_NAMES = {
    "Available": "available",
    "Created": "created",
    "Date": "date",
    "DateAccepted": "dateAccepted",
    "DateCopyrighted": "dateCopyrighted",
    "DateSubmitted": "dateSubmitted",
    "Epub": "EPUB created",
    "Issued": "issued",
    "Modified": "modified",
    "Valid": "valid",
}


def _epubmeta_container_rootfile(archive: zipfile.ZipFile) -> str | None:
    try:
        container_data = _safe_zip_member_bytes(
            archive, "META-INF/container.xml"
        )
        if container_data is None:
            raise ValueError("unsafe or missing EPUB container metadata")
        return _rootfile_path_from_container(container_data)
    except Exception as error:
        logger.error(
            f"[yellow]Warning: Error parsing EPUB metadata: {error}[/yellow]"
        )
        return None


def _epubmeta_rootfile_path(archive: zipfile.ZipFile) -> str | None:
    return _epubmeta_container_rootfile(archive) or _epub_fallback_rootfile(
        archive
    )


def _epubmeta_metadata_element(root: ET.Element) -> ET.Element | None:
    for element in root.iter():
        if element.tag.split("}")[-1] == "metadata":
            return element
    return None


def _epubmeta_valid_rootfile(archive: zipfile.ZipFile) -> str | None:
    rootfile_path = _epubmeta_rootfile_path(archive)
    if not rootfile_path:
        return None
    return rootfile_path if rootfile_path in archive.namelist() else None


def _epubmeta_archive_root(
    archive: zipfile.ZipFile,
) -> tuple[ET.Element, ET.Element] | None:
    if len(archive.infolist()) > 4096:
        return None
    rootfile_path = _epubmeta_valid_rootfile(archive)
    if rootfile_path is None:
        return None
    opf_data = _safe_zip_member_bytes(archive, rootfile_path)
    if opf_data is None:
        return None
    root = ET.fromstring(opf_data)
    metadata_element = _epubmeta_metadata_element(root)
    if metadata_element is None:
        return None
    return root, metadata_element


def _epub_refinement(element: ET.Element) -> _EpubRefinement | None:
    refines = get_attr_ignore_ns(element, "refines")
    if not refines:
        return None
    return _EpubRefinement(
        ref_id=refines.lstrip("#"),
        ref_prop=get_attr_ignore_ns(element, "property") or "",
        ref_scheme=get_attr_ignore_ns(element, "scheme") or "",
        ref_text=_epub_element_text(element),
    )


def _collect_epub_refinements(
    metadata_element: ET.Element,
) -> list[_EpubRefinement]:
    refinements: list[_EpubRefinement] = []
    for child in metadata_element:
        if child.tag.split("}")[-1] != "meta":
            continue
        refinement = _epub_refinement(child)
        if refinement is not None:
            refinements.append(refinement)
    return refinements


def _find_epub_refinement(
    refinements: list[_EpubRefinement], ref_id: str, prop: str
) -> _EpubRefinement | None:
    for refinement in refinements:
        if refinement.ref_id == ref_id and refinement.ref_prop == prop:
            return refinement
    return None


def _refinement_text(
    refinements: list[_EpubRefinement], ref_id: str | None, prop: str
) -> str | None:
    if not ref_id:
        return None
    refinement = _find_epub_refinement(refinements, ref_id, prop)
    return refinement.ref_text if refinement is not None else None


def _refinement_int(
    refinements: list[_EpubRefinement], ref_id: str | None, prop: str
) -> int | None:
    value = _refinement_text(refinements, ref_id, prop)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _identifier_refinement_values(
    refinements: list[_EpubRefinement],
    identifier_id: str | None,
    scheme: str | None,
) -> tuple[str | None, str | None]:
    if not identifier_id:
        return None, scheme
    refinement = _find_epub_refinement(
        refinements, identifier_id, "identifier-type"
    )
    if refinement is None:
        return None, scheme
    resolved_scheme = scheme if scheme else refinement.ref_scheme
    return refinement.ref_text, resolved_scheme


def _add_output_identifier(
    state: _EpubMetaOutputState,
    child: ET.Element,
    refinements: list[_EpubRefinement],
) -> None:
    identifier_id = get_attr_ignore_ns(child, "id")
    scheme = get_attr_ignore_ns(child, "scheme")
    identifier_type, scheme = _identifier_refinement_values(
        refinements, identifier_id, scheme
    )
    state.identifiers.append(
        _EpubOutputIdentifier(
            identifier_id=identifier_id,
            identifier_type=identifier_type,
            scheme=scheme,
            text=_epub_element_text(child),
        )
    )


def _add_output_title(
    state: _EpubMetaOutputState,
    child: ET.Element,
    refinements: list[_EpubRefinement],
) -> None:
    identifier = get_attr_ignore_ns(child, "id")
    state.titles.append(
        _EpubOutputTitle(
            lang=get_attr_ignore_ns(child, "lang"),
            title_type=_refinement_text(refinements, identifier, "title-type"),
            title_seq=_refinement_int(refinements, identifier, "display-seq"),
            text=_epub_element_text(child),
        )
    )


def _agent_refinement_values(
    refinements: list[_EpubRefinement],
    identifier: str | None,
    role: str | None,
    file_as: str | None,
) -> tuple[str | None, str | None, int | None]:
    refined_role = _refinement_text(refinements, identifier, "role")
    refined_file_as = _refinement_text(refinements, identifier, "file-as")
    creator_seq = _refinement_int(refinements, identifier, "display-seq")
    return refined_role or role, refined_file_as or file_as, creator_seq


def _output_agent(
    child: ET.Element, refinements: list[_EpubRefinement]
) -> _EpubOutputAgent:
    identifier = get_attr_ignore_ns(child, "id")
    role, file_as, creator_seq = _agent_refinement_values(
        refinements,
        identifier,
        get_attr_ignore_ns(child, "role"),
        get_attr_ignore_ns(child, "file-as"),
    )
    return _EpubOutputAgent(
        role=role,
        file_as=file_as,
        creator_seq=creator_seq,
        text=_epub_element_text(child),
    )


def _add_output_contributor(
    state: _EpubMetaOutputState,
    child: ET.Element,
    refinements: list[_EpubRefinement],
) -> None:
    state.contributors.append(_output_agent(child, refinements))


def _add_output_creator(
    state: _EpubMetaOutputState,
    child: ET.Element,
    refinements: list[_EpubRefinement],
) -> None:
    state.creators.append(_output_agent(child, refinements))


def _record_output_date(
    state: _EpubMetaOutputState, child: ET.Element
) -> None:
    event_name = date_event_from_str(get_attr_ignore_ns(child, "event"))
    if event_name:
        state.dates[event_name] = _epub_element_text(child)


def _record_output_meta_date(
    state: _EpubMetaOutputState, child: ET.Element
) -> None:
    if get_attr_ignore_ns(child, "refines"):
        return
    prop = get_attr_ignore_ns(child, "property")
    if not prop or not prop.startswith("dcterms:"):
        return
    event_name = date_event_from_str(prop)
    if event_name:
        state.dates[event_name] = _epub_element_text(child)


def _source_refinement_values(
    refinements: list[_EpubRefinement], identifier: str | None
) -> tuple[str | None, str | None, str | None]:
    identifier_ref = (
        _find_epub_refinement(refinements, identifier, "identifier-type")
        if identifier
        else None
    )
    source_of = _refinement_text(refinements, identifier, "source-of")
    if identifier_ref is None:
        return None, None, source_of
    scheme = identifier_ref.ref_scheme if identifier_ref.ref_scheme else None
    return identifier_ref.ref_text, scheme, source_of


def _add_output_source(
    state: _EpubMetaOutputState,
    child: ET.Element,
    refinements: list[_EpubRefinement],
) -> None:
    identifier_type, scheme, source_of = _source_refinement_values(
        refinements, get_attr_ignore_ns(child, "id")
    )
    state.sources.append(
        _EpubOutputSource(
            identifier_type=identifier_type,
            scheme=scheme,
            source_of=source_of,
            text=_epub_element_text(child),
        )
    )


def _add_output_language(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    state.languages.append(_epub_element_text(child))


def _add_output_date(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _record_output_date(state, child)


def _add_output_meta(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _record_output_meta_date(state, child)


def _add_output_type(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    if state.media_type is None:
        state.media_type = _epub_element_text(child)


def _append_output_text(values: list[str], child: ET.Element) -> None:
    values.append(_epub_element_text(child))


def _add_output_coverage(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.coverages, child)


def _add_output_description(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    state.descriptions.append(
        _EpubOutputDescription(
            lang=get_attr_ignore_ns(child, "lang"),
            text=_epub_element_text(child),
        )
    )


def _add_output_format(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.formats, child)


def _add_output_publisher(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.publishers, child)


def _add_output_relation(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.relations, child)


def _add_output_rights(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.rights, child)


def _add_output_subject(
    state: _EpubMetaOutputState,
    child: ET.Element,
    _refinements: list[_EpubRefinement],
) -> None:
    _append_output_text(state.subjects, child)


_EpubOutputHandler = Callable[
    [_EpubMetaOutputState, ET.Element, list[_EpubRefinement]], None
]

_EPUB_OUTPUT_HANDLERS: dict[str, _EpubOutputHandler] = {
    "identifier": _add_output_identifier,
    "title": _add_output_title,
    "language": _add_output_language,
    "contributor": _add_output_contributor,
    "creator": _add_output_creator,
    "date": _add_output_date,
    "meta": _add_output_meta,
    "source": _add_output_source,
    "type": _add_output_type,
    "coverage": _add_output_coverage,
    "description": _add_output_description,
    "format": _add_output_format,
    "publisher": _add_output_publisher,
    "relation": _add_output_relation,
    "rights": _add_output_rights,
    "subject": _add_output_subject,
}


def _collect_epubmeta_state(
    root: ET.Element, metadata_element: ET.Element
) -> _EpubMetaOutputState:
    state = _EpubMetaOutputState(
        version=get_attr_ignore_ns(root, "version") or "",
        unique_id=get_attr_ignore_ns(root, "unique-identifier") or "",
    )
    refinements = _collect_epub_refinements(metadata_element)
    for child in metadata_element:
        tag = child.tag.split("}")[-1]
        handler = _EPUB_OUTPUT_HANDLERS.get(tag)
        if handler is not None:
            handler(state, child, refinements)
    return state


def _format_subline(key: str, value: object) -> str:
    if value is None or value == "":
        return ""
    return f"  {key}: {value}"


def _append_optional_subline(
    lines: list[str], key: str, value: object
) -> None:
    line = _format_subline(key, value)
    if line:
        lines.append(line)


def _render_identifiers(
    identifiers: list[_EpubOutputIdentifier],
) -> list[str]:
    lines: list[str] = []
    for identifier in identifiers:
        lines.append("identifier")
        _append_optional_subline(lines, "id", identifier.identifier_id)
        _append_optional_subline(
            lines, "identifier-type", identifier.identifier_type
        )
        _append_optional_subline(lines, "scheme", identifier.scheme)
        lines.append(_format_subline("text", identifier.text))
    return lines


def _title_is_simple(title: _EpubOutputTitle) -> bool:
    return all(
        value is None
        for value in (title.lang, title.title_type, title.title_seq)
    )


def _render_titles(titles: list[_EpubOutputTitle]) -> list[str]:
    lines: list[str] = []
    for title in titles:
        if _title_is_simple(title):
            lines.append(f"title: {title.text}")
            continue
        lines.append("title")
        lines.append(_format_subline("text", title.text))
        _append_optional_subline(lines, "lang", title.lang)
        _append_optional_subline(lines, "title-type", title.title_type)
        _append_optional_subline(lines, "display-seq", title.title_seq)
    return lines


def _agent_is_simple(agent: _EpubOutputAgent) -> bool:
    return all(
        value is None
        for value in (agent.role, agent.file_as, agent.creator_seq)
    )


def _render_agents(label: str, agents: list[_EpubOutputAgent]) -> list[str]:
    lines: list[str] = []
    for agent in agents:
        if _agent_is_simple(agent):
            lines.append(f"{label}: {agent.text}")
            continue
        lines.append(label)
        lines.append(_format_subline("text", agent.text))
        _append_optional_subline(lines, "file-as", agent.file_as)
        _append_optional_subline(lines, "role", agent.role)
        _append_optional_subline(lines, "display-seq", agent.creator_seq)
    return lines


def _render_dates(dates: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for event_name in _DATE_EVENT_OUTPUT_ORDER:
        if event_name not in dates:
            continue
        lines.append("date")
        lines.append(
            _format_subline("event", _DATE_EVENT_OUTPUT_NAMES[event_name])
        )
        lines.append(_format_subline("text", dates[event_name]))
    return lines


def _source_is_simple(source: _EpubOutputSource) -> bool:
    return all(
        value is None
        for value in (source.identifier_type, source.scheme, source.source_of)
    )


def _render_sources(sources: list[_EpubOutputSource]) -> list[str]:
    lines: list[str] = []
    for source in sources:
        if _source_is_simple(source):
            lines.append(f"source: {source.text}")
            continue
        lines.append("source")
        lines.append(_format_subline("text", source.text))
        _append_optional_subline(
            lines, "identifier-type", source.identifier_type
        )
        _append_optional_subline(lines, "scheme", source.scheme)
        _append_optional_subline(lines, "source-of", source.source_of)
    return lines


def _render_descriptions(
    descriptions: list[_EpubOutputDescription],
) -> list[str]:
    lines: list[str] = []
    for description in descriptions:
        if description.lang is None:
            lines.append(f"description: {description.text}")
            continue
        lines.append("description")
        _append_optional_subline(lines, "lang", description.lang)
        lines.append(_format_subline("text", description.text))
    return lines


def _render_simple_values(label: str, values: list[str]) -> list[str]:
    return [f"{label}: {value}" for value in values]


def _render_epubmeta_output(state: _EpubMetaOutputState) -> str:
    lines = [
        "package",
        f"  version: {state.version}",
        f"  unique-identifier: {state.unique_id}",
    ]
    lines.extend(_render_identifiers(state.identifiers))
    lines.extend(_render_titles(state.titles))
    lines.extend(_render_simple_values("language", state.languages))
    lines.extend(_render_agents("contributor", state.contributors))
    lines.extend(_render_agents("creator", state.creators))
    lines.extend(_render_dates(state.dates))
    lines.extend(_render_sources(state.sources))
    if state.media_type:
        lines.append(f"type: {state.media_type}")
    lines.extend(_render_simple_values("coverage", state.coverages))
    lines.extend(_render_descriptions(state.descriptions))
    lines.extend(_render_simple_values("format", state.formats))
    lines.extend(_render_simple_values("publisher", state.publishers))
    lines.extend(_render_simple_values("relation", state.relations))
    lines.extend(_render_simple_values("rights", state.rights))
    lines.extend(_render_simple_values("subject", state.subjects))
    return "\n".join(lines) + "\n"


def _epubmeta_output_from_archive(archive: zipfile.ZipFile) -> str | None:
    parsed = _epubmeta_archive_root(archive)
    if parsed is None:
        return None
    root, metadata_element = parsed
    return _render_epubmeta_output(
        _collect_epubmeta_state(root, metadata_element)
    )


def get_epubmeta_output(epub_path: str) -> str | None:
    """Extract format EPUB metadata to match the output of epubmeta."""
    if not Path(epub_path).is_file() or not zipfile.is_zipfile(epub_path):
        return None
    try:
        with zipfile.ZipFile(epub_path, "r") as archive:
            return _epubmeta_output_from_archive(archive)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error parsing EPUB for epubmeta output: {error}[/yellow]"
        )
        return None
