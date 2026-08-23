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


def get_epubmeta_output(epub_path: str) -> str | None:
    """Extract format EPUB metadata to match the output of epubmeta."""
    if not Path(epub_path).is_file() or not zipfile.is_zipfile(epub_path):
        return None

    try:
        with zipfile.ZipFile(epub_path, "r") as z:
            if len(z.infolist()) > 4096:
                return None
            rootfile_path = None
            try:
                container_data = _safe_zip_member_bytes(
                    z, "META-INF/container.xml"
                )
                if container_data is None:
                    raise ValueError(
                        "unsafe or missing EPUB container metadata"
                    )
                root = ET.fromstring(container_data)
                for elem in root.iter():
                    if elem.tag.split("}")[-1] == "rootfile":
                        rootfile_path = elem.attrib.get("full-path")
                        if rootfile_path:
                            break
            except Exception as e:
                logger.error(
                    f"[yellow]Warning: Error parsing EPUB metadata: {e}[/yellow]"
                )

            if not rootfile_path:
                for name in z.namelist():
                    if name.endswith(".opf"):
                        rootfile_path = name
                        break

            if not rootfile_path or rootfile_path not in z.namelist():
                return None

            opf_data = _safe_zip_member_bytes(z, rootfile_path)
            if opf_data is None:
                return None
            root = ET.fromstring(opf_data)

            # Get package tag attributes
            version = get_attr_ignore_ns(root, "version") or ""
            unique_id = get_attr_ignore_ns(root, "unique-identifier") or ""

            metadata_elem = None
            for elem in root.iter():
                if elem.tag.split("}")[-1] == "metadata":
                    metadata_elem = elem
                    break

            if metadata_elem is None:
                return None

            # Collect refinements
            refinements = []
            for child in metadata_elem:
                tag = child.tag.split("}")[-1]
                if tag == "meta":
                    refines = get_attr_ignore_ns(child, "refines")
                    if refines:
                        ref_id = refines.lstrip("#")
                        prop = get_attr_ignore_ns(child, "property") or ""
                        scheme = get_attr_ignore_ns(child, "scheme") or ""
                        text = (child.text or "").strip()
                        refinements.append(
                            {
                                "refId": ref_id,
                                "refProp": prop,
                                "refScheme": scheme,
                                "refText": text,
                            }
                        )

            def find_refinement(ref_id, prop):
                for r in refinements:
                    if r["refId"] == ref_id and r["refProp"] == prop:
                        return r
                return None

            identifiers = []
            titles = []
            languages = []
            contributors = []
            creators = []
            dates_map = {}
            sources = []
            m_type = None
            coverages = []
            descriptions = []
            formats = []
            publishers = []
            relations = []
            rights = []
            subjects = []

            for child in metadata_elem:
                tag = child.tag.split("}")[-1]
                text = (child.text or "").strip()

                if tag == "identifier":
                    id_val = get_attr_ignore_ns(child, "id")
                    scheme_val = get_attr_ignore_ns(child, "scheme")
                    id_type = None
                    if id_val:
                        ref = find_refinement(id_val, "identifier-type")
                        if ref:
                            id_type = ref["refText"]
                            if not scheme_val:
                                scheme_val = ref["refScheme"]
                    identifiers.append(
                        {
                            "id": id_val,
                            "identifier_type": id_type,
                            "scheme": scheme_val,
                            "text": text,
                        }
                    )

                elif tag == "title":
                    id_val = get_attr_ignore_ns(child, "id")
                    lang_val = get_attr_ignore_ns(child, "lang")
                    title_type = None
                    title_seq = None
                    if id_val:
                        ref_type = find_refinement(id_val, "title-type")
                        if ref_type:
                            title_type = ref_type["refText"]
                        ref_seq = find_refinement(id_val, "display-seq")
                        if ref_seq:
                            with contextlib.suppress(ValueError):
                                title_seq = int(ref_seq["refText"])
                    titles.append(
                        {
                            "lang": lang_val,
                            "title_type": title_type,
                            "title_seq": title_seq,
                            "text": text,
                        }
                    )

                elif tag == "language":
                    languages.append(text)

                elif tag in ("contributor", "creator"):
                    id_val = get_attr_ignore_ns(child, "id")
                    role_val = get_attr_ignore_ns(child, "role")
                    file_as_val = get_attr_ignore_ns(child, "file-as")
                    creator_seq = None

                    if id_val:
                        ref_role = find_refinement(id_val, "role")
                        if ref_role:
                            role_val = ref_role["refText"]
                        ref_file = find_refinement(id_val, "file-as")
                        if ref_file:
                            file_as_val = ref_file["refText"]
                        ref_seq = find_refinement(id_val, "display-seq")
                        if ref_seq:
                            with contextlib.suppress(ValueError):
                                creator_seq = int(ref_seq["refText"])

                    item = {
                        "role": role_val,
                        "file_as": file_as_val,
                        "creator_seq": creator_seq,
                        "text": text,
                    }
                    if tag == "creator":
                        creators.append(item)
                    else:
                        contributors.append(item)

                elif tag == "date":
                    event_val = get_attr_ignore_ns(child, "event")
                    event_name = date_event_from_str(event_val)
                    if event_name:
                        dates_map[event_name] = text

                elif tag == "meta":
                    refines = get_attr_ignore_ns(child, "refines")
                    if not refines:
                        prop = get_attr_ignore_ns(child, "property")
                        if prop and prop.startswith("dcterms:"):
                            event_name = date_event_from_str(prop)
                            if event_name:
                                dates_map[event_name] = text

                elif tag == "source":
                    id_val = get_attr_ignore_ns(child, "id")
                    id_type = None
                    scheme_val = None
                    source_of = None
                    if id_val:
                        ref_type = find_refinement(id_val, "identifier-type")
                        if ref_type:
                            id_type = ref_type["refText"]
                            scheme_val = ref_type["refScheme"] or None
                        ref_sof = find_refinement(id_val, "source-of")
                        if ref_sof:
                            source_of = ref_sof["refText"]
                    sources.append(
                        {
                            "id_type": id_type,
                            "scheme": scheme_val,
                            "source_of": source_of,
                            "text": text,
                        }
                    )

                elif tag == "type" and m_type is None:
                    m_type = text

                elif tag == "coverage":
                    coverages.append(text)

                elif tag == "description":
                    lang_val = get_attr_ignore_ns(child, "lang")
                    descriptions.append({"lang": lang_val, "text": text})

                elif tag == "format":
                    formats.append(text)

                elif tag == "publisher":
                    publishers.append(text)

                elif tag == "relation":
                    relations.append(text)

                elif tag == "rights":
                    rights.append(text)

                elif tag == "subject":
                    subjects.append(text)

            # Build formatted lines
            lines = []
            lines.append("package")
            lines.append(f"  version: {version}")
            lines.append(f"  unique-identifier: {unique_id}")

            def format_subline(key, val):
                if val is None or val == "":
                    return ""
                return f"  {key}: {val}"

            # 1. Identifiers
            for ident in identifiers:
                lines.append("identifier")
                if ident.get("id"):
                    lines.append(format_subline("id", ident["id"]))
                if ident.get("identifier_type"):
                    lines.append(
                        format_subline(
                            "identifier-type", ident["identifier_type"]
                        )
                    )
                if ident.get("scheme"):
                    lines.append(format_subline("scheme", ident["scheme"]))
                lines.append(format_subline("text", ident["text"]))

            # 2. Titles
            for title in titles:
                if (
                    title.get("lang") is None
                    and title.get("title_type") is None
                    and title.get("title_seq") is None
                ):
                    lines.append(f"title: {title['text']}")
                else:
                    lines.append("title")
                    lines.append(format_subline("text", title["text"]))
                    if title.get("lang"):
                        lines.append(format_subline("lang", title["lang"]))
                    if title.get("title_type"):
                        lines.append(
                            format_subline("title-type", title["title_type"])
                        )
                    if title.get("title_seq") is not None:
                        lines.append(
                            format_subline(
                                "display-seq", str(title["title_seq"])
                            )
                        )

            # 3. Languages
            lines.extend(f"language: {lang}" for lang in languages)

            # 4. Contributors
            for contributor in contributors:
                if (
                    contributor.get("role") is None
                    and contributor.get("file_as") is None
                    and contributor.get("creator_seq") is None
                ):
                    lines.append(f"contributor: {contributor['text']}")
                else:
                    lines.append("contributor")
                    lines.append(format_subline("text", contributor["text"]))
                    if contributor.get("file_as"):
                        lines.append(
                            format_subline("file-as", contributor["file_as"])
                        )
                    if contributor.get("role"):
                        lines.append(
                            format_subline("role", contributor["role"])
                        )
                    if contributor.get("creator_seq") is not None:
                        lines.append(
                            format_subline(
                                "display-seq", str(contributor["creator_seq"])
                            )
                        )

            # 5. Creators
            for creator in creators:
                if (
                    creator.get("role") is None
                    and creator.get("file_as") is None
                    and creator.get("creator_seq") is None
                ):
                    lines.append(f"creator: {creator['text']}")
                else:
                    lines.append("creator")
                    lines.append(format_subline("text", creator["text"]))
                    if creator.get("file_as"):
                        lines.append(
                            format_subline("file-as", creator["file_as"])
                        )
                    if creator.get("role"):
                        lines.append(format_subline("role", creator["role"]))
                    if creator.get("creator_seq") is not None:
                        lines.append(
                            format_subline(
                                "display-seq", str(creator["creator_seq"])
                            )
                        )

            # 6. Dates
            date_event_order = [
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
            ]
            date_event_strings = {
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
            for event_name in date_event_order:
                if event_name in dates_map:
                    lines.append("date")
                    lines.append(
                        format_subline("event", date_event_strings[event_name])
                    )
                    lines.append(format_subline("text", dates_map[event_name]))

            # 7. Sources
            for source in sources:
                if (
                    source.get("id_type") is None
                    and source.get("scheme") is None
                    and source.get("source_of") is None
                ):
                    lines.append(f"source: {source['text']}")
                else:
                    lines.append("source")
                    lines.append(format_subline("text", source["text"]))
                    if source.get("id_type"):
                        lines.append(
                            format_subline(
                                "identifier-type", source["id_type"]
                            )
                        )
                    if source.get("scheme"):
                        lines.append(
                            format_subline("scheme", source["scheme"])
                        )
                    if source.get("source_of"):
                        lines.append(
                            format_subline("source-of", source["source_of"])
                        )

            # 8. Type
            if m_type:
                lines.append(f"type: {m_type}")

            # 9. Coverage
            lines.extend(f"coverage: {coverage}" for coverage in coverages)

            # 10. Descriptions
            for desc in descriptions:
                if desc.get("lang") is None:
                    lines.append(f"description: {desc['text']}")
                else:
                    lines.append("description")
                    if desc.get("lang"):
                        lines.append(format_subline("lang", desc["lang"]))
                    lines.append(format_subline("text", desc["text"]))

            # 11. Formats
            lines.extend(f"format: {fmt}" for fmt in formats)

            # 12. Publishers
            lines.extend(f"publisher: {pub}" for pub in publishers)

            # 13. Relations
            lines.extend(f"relation: {rel}" for rel in relations)

            # 14. Rights
            lines.extend(f"rights: {right}" for right in rights)

            # 15. Subjects
            lines.extend(f"subject: {subject}" for subject in subjects)

            return "\n".join(lines) + "\n"

    except Exception as e:
        logger.debug(
            f"[yellow]Warning: Error parsing EPUB for epubmeta output: {e}[/yellow]"
        )
        return None
