# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
"""Book and Audiobook preparation helpers.

This module contains the logic that was previously inlined inside the
``Prep`` class in ``prep.py``.  It is intentionally kept free of any
``Prep``-specific imports so it can be tested and extended in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.domain_models.book_language import is_valid_book_language, resolve_book_language
from src.domain_models.errors import MediaInfoError
from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.integrations.media.book_extractors import (
    _safe_zip_member_bytes,
    validate_isbn_checksum,
)
from src.integrations.media.book_extractors import (
    extract_cbr_cbz_metadata as _extract_cbr_cbz_metadata,
)
from src.integrations.media.book_extractors import (
    extract_epub_metadata as _extract_epub_metadata,
)
from src.integrations.media.book_extractors import (
    extract_isbn_from_pdf as _extract_isbn_from_pdf,
)
from src.integrations.media.book_extractors import (
    extract_mobi_metadata as _extract_mobi_metadata,
)
from src.integrations.media.book_extractors import extract_pdf_page_count as _extract_pdf_page_count
from src.integrations.media.book_extractors import (
    extract_series_from_filename as _extract_series_from_filename,
)
from src.integrations.media.book_extractors import (
    get_epubmeta_output as _get_epubmeta_output,
)
from src.integrations.media.book_extractors import (
    normalize_series_index as _normalize_series_index,
)
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.media_info_export import export_info
from src.integrations.observability.runtime_support import logger
from src.services.book_input_service import (
    detect_newspaper,
    sanitize_book_author,
    sanitize_book_language,
)

# ---------------------------------------------------------------------------
# File-list resolution
# ---------------------------------------------------------------------------

BOOK_EXTENSIONS = frozenset(
    {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".fb2", ".html", ".htm", ".chm", ".djvu", ".doc", ".docx", ".kfx", ".lit", ".pdb", ".txt", ".rtf", ".cbz", ".cbr"}
)
AUDIOBOOK_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".m4b",
        ".flac",
        ".alac",
        ".aac",
        ".m4a",
        ".ogg",
        ".opus",
        ".wav",
        ".ac3",
        ".dts",
        ".aiff",
        ".ape",
        ".wv",
        ".wma",
        ".aax",
        ".aaxc",
    }
)


def _reject_plain_text_only(filelist: list[str]) -> None:
    if filelist and all(Path(file).suffix.lower() == ".txt" for file in filelist):
        raise ItemProcessingError("Plain-text TXT files are not supported as standalone book uploads.")


_TEXT_SIDECAR_STEMS = frozenset({"cover", "folder", "index", "info", "readme"})


def _normalized_book_stem(path: str) -> str:
    return re.sub(r"[\W_]+", " ", Path(path).stem.casefold()).strip()


def resolve_book_filelist(
    meta: Meta,
    videoloc: str,
) -> tuple[str, list[str], str, str]:
    """Scan *videoloc* for book/audiobook files and update *meta* in-place.

    Populates ``meta.filelist``, ``meta.scene``, ``meta.imdb_id``,
    and ``meta.audiobook``.

    Returns:
        A 4-tuple ``(videopath, filelist, search_term, search_file_folder)``
        where *videopath* is the primary/largest file used as the "video"
        reference for downstream processing.
    """
    allowed_extensions = BOOK_EXTENSIONS | AUDIOBOOK_EXTENSIONS

    filelist: list[str] = []
    if Path(videoloc).is_dir():
        for root, _, files in os.walk(videoloc):
            for file in files:
                ext = Path(file).suffix.lower()
                if ext in allowed_extensions:
                    filelist.append(str(Path(Path(root) / file).resolve()))
        filelist = sorted(filelist)
        if not filelist:
            raise ItemProcessingError("No book or audiobook files were found in the selected path.")
        _reject_plain_text_only(filelist)
        richer_book_files = [file for file in filelist if Path(file).suffix.lower() in BOOK_EXTENSIONS - {".txt", ".html", ".htm"}]
        if richer_book_files:
            richer_stems = {_normalized_book_stem(file) for file in richer_book_files}
            filelist = [
                file
                for file in filelist
                if not (
                    Path(file).suffix.lower() in {".txt", ".html", ".htm"}
                    and (
                        Path(file).stem.casefold() in _TEXT_SIDECAR_STEMS
                        or any(richer_stem == _normalized_book_stem(file) or richer_stem.startswith(f"{_normalized_book_stem(file)} ") for richer_stem in richer_stems)
                    )
                )
            ]
        ebook_files = [file for file in filelist if Path(file).suffix.lower() in BOOK_EXTENSIONS]
        audiobook_files = [file for file in filelist if Path(file).suffix.lower() in AUDIOBOOK_EXTENSIONS]
        if ebook_files and audiobook_files:
            raise ItemProcessingError("Ebook and audiobook files were found in the same path. Upload each media type separately.")
        if len(ebook_files) > 1:
            filenames = ", ".join(Path(file).name for file in ebook_files)
            raise ItemProcessingError(f"Multiple ebook files were found in one path ({filenames}). Upload each ebook file and format separately.")
        videopath = sorted(filelist, key=os.path.getsize, reverse=True)[0]
    else:
        videopath = videoloc
        filelist.append(videoloc)
        _reject_plain_text_only(filelist)

    meta.filelist = filelist
    meta.imdb_id = 0

    primary_ext = Path(videopath).suffix.lower()
    meta.audiobook = bool(meta.audiobook or (primary_ext in AUDIOBOOK_EXTENSIONS) or any(Path(f).suffix.lower() in AUDIOBOOK_EXTENSIONS for f in filelist))

    search_term = Path(filelist[0]).name if filelist else ""
    search_file_folder = "file"
    return videopath, filelist, search_term, search_file_folder


# ---------------------------------------------------------------------------
# Language resolution helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MediaInfo metadata extraction
# ---------------------------------------------------------------------------


def _mi_extra(general_track: dict[str, Any], name: str) -> str:
    """Case-insensitive lookup of a freeform tag in a MediaInfo General track's extra dict."""
    extra = general_track.get("extra")
    if not isinstance(extra, dict):
        return ""
    for key, val in extra.items():
        if key.lower() == name.lower() and val and not isinstance(val, dict):
            text = str(val).strip()
            if text:
                return text
    return ""


def _unescape_meta_val(val: Any) -> str | None:
    if val is None or isinstance(val, (dict, list)):
        return None
    import html

    return html.unescape(str(val)).strip()


_AUTHOR_PARTICLES = frozenset({"al", "da", "de", "del", "della", "di", "dos", "du", "la", "le", "of", "van", "von", "y"})
_BOOK_FORMAT_SUFFIX = re.compile(
    r"\s*(?:\.(?:azw3?|cb[rz]|djvu|epub|fb2|html?|kfx|lit|mobi|pdf|rtf|txt)|\((?:azw3?|cb[rz]|djvu|epub|fb2|kfx|mobi|pdf|retail|scan|hybrid)\))\s*$",
    re.IGNORECASE,
)
_BOOK_PART_ONLY = re.compile(r"^(?:vol(?:ume)?|book|part|tome)\s*[#.]?\s*\d+(?:\.\d+)?$", re.IGNORECASE)
_ASIN_VALUE = re.compile(r"\bASIN\s*[:#]?\s*([A-Z0-9]{10})(?![A-Z0-9])", re.IGNORECASE)


def _author_likelihood(value: str) -> int:
    words = re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", value, flags=re.UNICODE)
    if not 2 <= len(words) <= 6:
        return 0
    if words[0].casefold() in {"a", "an", "the"}:
        return 0
    substantive = [word for word in words if word.casefold() not in _AUTHOR_PARTICLES]
    if not substantive or any(not word[0].isupper() for word in substantive):
        return -1
    comma_groups = [re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", group, flags=re.UNICODE) for group in value.split(",")]
    if len(comma_groups) > 1 and all(2 <= len(group) <= 3 for group in comma_groups):
        return 4
    return 3 if len(substantive) <= 3 else 1


def _strip_book_format_suffix(value: str) -> str:
    return _BOOK_FORMAT_SUFFIX.sub("", value).strip()


def _extract_asin_identifier(value: Any) -> str:
    match = _ASIN_VALUE.search(str(value or "").strip())
    return match.group(1).upper() if match else ""


def _is_capitalized_mononym(value: str) -> bool:
    words = re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", value, flags=re.UNICODE)
    return len(words) == 1 and words[0][0].isupper()


def book_identity_from_path(path: str) -> tuple[str, str]:
    source = Path(path)
    name = source.name if source.is_dir() else source.stem
    name = re.sub(r"\s*\[AUDIOBOOK\]\s*$", "", name, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s+-\s+", name, maxsplit=1)
    if len(parts) != 2:
        return "", ""
    first, second = parts
    if _BOOK_PART_ONLY.fullmatch(second.strip()):
        return "", re.sub(r"_\s+", ": ", name).strip()
    first_score = _author_likelihood(first)
    second_score = _author_likelihood(second)
    if first_score >= 3 and second_score <= 1:
        author, title = first, second
    elif (second_score >= 3 and first_score <= 0) or (first_score < 0 and not re.search(r"\d", first) and _is_capitalized_mononym(second)):
        author, title = second, first
    elif first_score >= 3 and second_score >= 3:
        author, title = first, second
    else:
        return "", re.sub(r"_\s+", ": ", name).strip()
    title = _strip_book_format_suffix(re.sub(r"_\s+", ": ", title).strip())
    title_parts = re.split(r"\s+-\s+", title)
    if len(title_parts) > 1 and validate_isbn_checksum(title_parts[-1]):
        title = " - ".join(title_parts[:-1]).strip()
    return author.strip(), title


_IDENTITY_STOPWORDS = frozenset({"a", "an", "and", "as", "book", "for", "in", "life", "of", "the", "through", "to", "with", "you", "your"})
_GENERIC_PROVIDER_TITLES = frozenset({"anovel", "amemoir", "abiography", "ahistory", "aguide"})


def _identity_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2 and token not in _IDENTITY_STOPWORDS}


def _normalized_book_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _author_identity_tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _prefer_descriptive_source_title(current_title: str, author: str, source_title: str) -> str:
    current_identity = _normalized_book_identity(current_title)
    author_identity = _normalized_book_identity(author)
    source_tokens = _identity_tokens(source_title)
    current_tokens = _identity_tokens(current_title)
    if current_identity in _GENERIC_PROVIDER_TITLES and source_tokens:
        return source_title
    if current_identity and current_identity == author_identity and current_tokens < source_tokens:
        return source_title
    return current_title


def _prefer_descriptive_source_author(current_author: str, source_author: str) -> str:
    current_tokens = _author_identity_tokens(current_author)
    source_tokens = _author_identity_tokens(source_author)
    if current_tokens and len(source_tokens) > len(current_tokens) and current_tokens < source_tokens:
        return source_author
    return current_author


def _publisher_from_overview(value: str) -> str:
    import html

    plain = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain).replace("\u00a0", " ")
    plain = re.sub(r"[\u200b\u200e\u200f\ufeff]", "", plain)
    match = re.search(r"\bpublisher\b\s*(?::|\uFF1A)\s*([^\r\n]+)", plain, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _matching_isbn_metadata(meta: Meta, *providers: dict[str, Any] | None) -> dict[str, Any] | None:
    current_isbn = validate_isbn_checksum(str(meta.isbn or ""))
    if not current_isbn:
        return None
    return next(
        (provider for provider in providers if provider and validate_isbn_checksum(str(provider.get("isbn") or "")) == current_isbn),
        None,
    )


def book_identity_conflict(meta: Meta, path: str) -> str | None:
    path_author, path_title = book_identity_from_path(path)
    if not path_author or not path_title or not meta.author or not meta.title:
        return None

    path_author_tokens = _author_identity_tokens(path_author)
    metadata_author_tokens = _author_identity_tokens(str(meta.author))
    shorter_author, longer_author = sorted((path_author_tokens, metadata_author_tokens), key=len)
    distinctive_mononym = len(shorter_author) == 1 and len(next(iter(shorter_author), "")) >= 5
    authors_match = path_author_tokens == metadata_author_tokens or ((len(shorter_author) >= 2 or distinctive_mononym) and shorter_author < longer_author)
    if not authors_match:
        return f"Book metadata author '{meta.author}' conflicts with source author '{path_author}'"

    path_tokens = _identity_tokens(path_title)
    metadata_tokens = _identity_tokens(str(meta.title))
    if len(path_tokens) < 2 or len(metadata_tokens) < 2 or path_tokens & metadata_tokens:
        return None
    return f"Book metadata title '{meta.title}' conflicts with source title '{path_title}' for author '{meta.author}'"


def missing_book_fields(meta: Meta) -> list[str]:
    missing: list[str] = []
    for field in ("title", "author", "year", "book_language"):
        value = getattr(meta, field, None)
        invalid_value = not value or str(value).strip().lower() in {"", "none", "null"}
        invalid_language = field == "book_language" and not is_valid_book_language(str(value), str(meta.book_language_iso or ""))
        if invalid_value or invalid_language:
            missing.append(field)

    if meta.audiobook:
        for field in ("narrator", "publisher"):
            value = getattr(meta, field, None)
            if not value or str(value).strip().lower() in {"", "none", "null"}:
                missing.append(field)
        asin = str(meta.asin or "").strip().upper()
        if not validate_isbn_checksum(str(meta.isbn or "")) and not re.fullmatch(r"[A-Z0-9]{10}", asin):
            missing.append("isbn_or_asin")
    return missing


def _validated_isbns(value: str) -> set[str]:
    patterns = re.findall(r"(?<!\d)(?:97[89](?:[- ]?\d){10}|\d(?:[- ]?\d){8}[- ]?[\dXx])(?!\d)", value)
    return {validated for candidate in patterns if (validated := validate_isbn_checksum(candidate))}


def _epub_content_identifiers(path: str) -> tuple[set[str], set[str]]:
    isbns: set[str] = set()
    asins: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 4096:
                return set(), set()
            total_size = 0
            for member in members:
                if Path(member.filename).suffix.lower() not in {".opf", ".xhtml", ".html", ".htm", ".xml", ".ncx"}:
                    continue
                total_size += member.file_size
                if total_size > 16 * 1024 * 1024:
                    return set(), set()
                payload = _safe_zip_member_bytes(archive, member.filename)
                if payload is None:
                    return set(), set()
                text = payload.decode("utf-8", errors="ignore")
                isbns.update(_validated_isbns(text))
                asins.update(re.findall(r"\bB0[A-Z0-9]{8}\b", text.upper()))
    except OSError, zipfile.BadZipFile:
        return set(), set()
    return isbns, asins


def _reconcile_epub_identifiers(meta: Meta, epub_meta: dict[str, Any], path: str) -> None:
    primary_isbn = validate_isbn_checksum(str(epub_meta.get("isbn", "")))
    filename_isbns = _validated_isbns(Path(path).stem)
    content_isbns, content_asins = _epub_content_identifiers(path)

    if len(filename_isbns) == 1:
        epub_meta["isbn"] = next(iter(filename_isbns))
    elif len(filename_isbns) > 1:
        values = ", ".join(sorted(filename_isbns))
        raise ItemProcessingError(f"Conflicting EPUB ISBNs could not be resolved ({values}). Re-run with --isbn using the identifier for this exact edition.", path)
    elif primary_isbn:
        epub_meta["isbn"] = primary_isbn
    elif len(content_isbns) == 1:
        epub_meta["isbn"] = next(iter(content_isbns))
    elif len(content_isbns) > 1:
        values = ", ".join(sorted(content_isbns))
        raise ItemProcessingError(f"Conflicting EPUB ISBNs could not be resolved ({values}). Re-run with --isbn using the identifier for this exact edition.", path)

    if not meta.book_asin and not meta.asin and len(content_asins) == 1:
        epub_meta["asin"] = next(iter(content_asins))


async def gather_book_prep(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Set up BOOK/Audiobook category fields and extract embedded MediaInfo metadata.

    Sets ``meta.category``, ``meta.resolution``, ``meta.search_year``,
    ``meta.hfr``, ``meta.sd``, and ``meta.mediainfo``, then attempts
    to populate title, author, narrator, publisher, ISBN, overview, year,
    keywords, and language from the file's embedded tags.
    """
    meta.category = "BOOK"
    meta.search_year = ""
    meta.resolution = "Other"
    meta.hfr = False
    meta.sd = 0
    meta.valid_mi_settings = True

    # Warn if Google Books API key is missing
    api_key = ""
    if config and "DEFAULT" in config:
        api_key = config["DEFAULT"].get("google_books_api_key", "").strip()
    if not api_key:
        logger.warning("[bold red]Warning: Google Books API key is not configured. Book metadata searches will be limited and incomplete.[/bold red]")

    # Check if the file format is CBR or CBZ and automatically set comic to True
    file_ext = Path(videopath).suffix.lstrip(".").upper()
    explicit_comic = bool(meta.comic)
    explicit_manga = bool(meta.manga)
    if file_ext in ("CBR", "CBZ"):
        meta.comic = True

    # Identify CLI overrides at the very start
    cli_overrides = {
        "title": bool(meta.book_title),
        "author": bool(meta.book_author),
        "narrator": bool(meta.narrator),
        "publisher": bool(meta.book_publisher),
        "isbn": bool(meta.book_isbn),
        "asin": bool(meta.book_asin),
        "book_language": bool(meta.book_language),
        "year": "manual_year" in meta and (meta.manual_year or 0) > 0,
        "keywords": bool(meta.keywords),
        "overview": bool(meta.overview),
    }
    source_metadata_fields: set[str] = set()

    def apply_source_metadata(extracted: dict[str, Any]) -> None:
        override_keys = {
            "title": "title",
            "author": "author",
            "narrator": "narrator",
            "publisher": "publisher",
            "isbn": "isbn",
            "asin": "asin",
            "year": "year",
            "keywords": "keywords",
            "overview": "overview",
        }
        for key, val in extracted.items():
            if not val:
                continue
            if key == "book_language_raw":
                if cli_overrides["book_language"]:
                    continue
                full, iso3 = resolve_book_language(str(val))
                if is_valid_book_language(full, iso3):
                    meta.book_language = full
                    meta.book_language_iso = iso3
                    source_metadata_fields.update({"book_language", "book_language_iso"})
                continue
            override_key = override_keys.get(key)
            if override_key and cli_overrides[override_key]:
                continue
            if key == "isbn":
                val = validate_isbn_checksum(str(val))
                if not val:
                    continue
            if key == "year":
                meta[key] = int(val)
                meta.search_year = int(val)
                source_metadata_fields.add("search_year")
            else:
                meta[key] = val
            source_metadata_fields.add(key)

    def source_has(key: str) -> bool:
        return (
            key in source_metadata_fields
            or (key == "search_year" and "year" in source_metadata_fields)
            or (key == "book_language_iso" and "book_language" in source_metadata_fields)
        )

    # Extract EPUB metadata directly if the file is an EPUB
    if videopath.lower().endswith(".epub") and Path(videopath).is_file():
        meta.epubmeta_output = _get_epubmeta_output(videopath)
        epub_meta = _extract_epub_metadata(videopath)
        if epub_meta:
            if not cli_overrides["isbn"]:
                _reconcile_epub_identifiers(meta, epub_meta, videopath)
            logger.debug(f"[cyan]EPUB metadata extracted: {epub_meta}[/cyan]")
            apply_source_metadata(epub_meta)

    # Extract CBR/CBZ metadata directly if the file is a CBR/CBZ
    if videopath.lower().endswith((".cbr", ".cbz")) and Path(videopath).is_file():
        cbr_cbz_meta = _extract_cbr_cbz_metadata(videopath)
        if cbr_cbz_meta:
            logger.debug(f"[cyan]CBR/CBZ metadata extracted: {cbr_cbz_meta}[/cyan]")
            apply_source_metadata(cbr_cbz_meta)

    # AZW and AZW3 are Kindle variants of the MOBI family.  The extractor may
    # not support every DRM/KFX variant, but it safely returns no metadata then.
    if videopath.lower().endswith((".mobi", ".azw", ".azw3")) and Path(videopath).is_file():
        mobi_meta = _extract_mobi_metadata(videopath)
        if mobi_meta:
            logger.debug(f"[cyan]MOBI metadata extracted: {mobi_meta}[/cyan]")
            apply_source_metadata(mobi_meta)

    # Extract ISBN from PDF directly if the file is a PDF
    if videopath.lower().endswith(".pdf") and Path(videopath).is_file():
        page_count = _extract_pdf_page_count(videopath)
        if page_count:
            meta.page_count = page_count
        pdf_isbn = _extract_isbn_from_pdf(videopath)
        if pdf_isbn and not meta.isbn:
            meta.isbn = pdf_isbn
            logger.debug(f"[cyan]PDF ISBN extracted: {pdf_isbn}[/cyan]")

    if not meta.edit:
        try:
            mi = await export_info(
                videopath,
                meta.isdir,
                meta.uuid,
                base_dir,
                is_dvd=(meta.is_disc == "DVD"),
            )
            meta.mediainfo = mi
        except MediaInfoError as error:
            logger.warning(f"[yellow]MediaInfo could not inspect book/audiobook release: {error}[/yellow]")
            logger.debug(error.debug_details)
            meta.mediainfo = {}
        except Exception as error:
            logger.warning(f"[yellow]MediaInfo export failed for book/audiobook: {error}[/yellow]")
            meta.mediainfo = {}
    else:
        pass  # meta.mediainfo already populated from a previous run

    if meta.mediainfo:
        try:
            tracks = meta.mediainfo.get("media", {}).get("track", [])
            general_track = next((t for t in tracks if t.get("@type") == "General"), None)
            if general_track:
                # 1. Title/Album
                album = _unescape_meta_val(general_track.get("Album") or general_track.get("album"))
                track_name = _unescape_meta_val(general_track.get("Track_name") or general_track.get("track_name"))

                # Detect if the audiobook is Unabridged or Abridged from file metadata
                detected_edition = None
                for val in (album, track_name):
                    if val:
                        match = re.search(r"\b(unabridged|abridged)\b", val, re.IGNORECASE)
                        if match:
                            detected_edition = match.group(1).capitalize()
                            break
                if detected_edition and not meta.edition:
                    meta.edition = detected_edition

                if not meta.title:
                    if album:
                        meta.title = album
                    elif track_name:
                        meta.title = track_name

                # Clean the edition from the title if it's not a CLI override
                if not cli_overrides["title"] and meta.title:
                    original_title = meta.title
                    # Remove brackets like [...] and their content
                    cleaned_title = re.sub(r"\s*\[[^\]]*\]", "", original_title)
                    cleaned_title = re.sub(r"\s*[\(\[\{-]?\s*\b(unabridged|abridged)\b\s*[\)\]\}]?\s*", " ", cleaned_title, flags=re.IGNORECASE)
                    cleaned_title = re.sub(r"\s+", " ", cleaned_title).strip()
                    cleaned_title = cleaned_title.strip("-").strip()
                    meta.title = cleaned_title

                # 2. Author
                performer = _unescape_meta_val(general_track.get("Performer") or general_track.get("performer"))
                album_performer = _unescape_meta_val(general_track.get("Album_Performer") or general_track.get("album_performer"))
                if not meta.author:
                    if performer:
                        meta.author = performer
                    elif album_performer:
                        meta.author = album_performer

                # 3. Narrator
                composer = _unescape_meta_val(general_track.get("Composer") or general_track.get("composer"))
                if composer and not meta.narrator:
                    meta.narrator = composer

                # 4. Publisher
                publisher = _unescape_meta_val(general_track.get("Publisher") or general_track.get("publisher"))
                if publisher and not meta.publisher:
                    meta.publisher = publisher

                # 5. ISBN
                isbn_val = general_track.get("ISBN") or general_track.get("isbn")
                if not isbn_val and isinstance(general_track.get("extra"), dict):
                    isbn_val = general_track["extra"].get("ISBN") or general_track["extra"].get("isbn")
                isbn_val = _unescape_meta_val(isbn_val)
                asin_from_isbn = _extract_asin_identifier(isbn_val)
                if asin_from_isbn and not meta.asin:
                    meta.asin = asin_from_isbn
                elif isbn_val and not meta.isbn:
                    meta.isbn = isbn_val

                # 5b. ASIN
                asin_val = general_track.get("ASIN") or general_track.get("asin")
                if not asin_val and isinstance(general_track.get("extra"), dict):
                    asin_val = general_track["extra"].get("ASIN") or general_track["extra"].get("asin")
                asin_val = _unescape_meta_val(asin_val)
                normalized_asin = _extract_asin_identifier(asin_val) or str(asin_val or "").strip().upper()
                if normalized_asin and re.fullmatch(r"[A-Z0-9]{10}", normalized_asin) and not meta.asin:
                    meta.asin = normalized_asin

                # Series from extra.SERIES / extra.SERIESPART
                if not meta.book_series:
                    series_val = _mi_extra(general_track, "SERIES")
                    if series_val:
                        meta.book_series = series_val
                        part_val = _mi_extra(general_track, "SERIESPART")
                        if part_val and not meta.book_series_index:
                            meta.book_series_index = _normalize_series_index(part_val)

                # 6. Overview/Comment
                comment = _unescape_meta_val(general_track.get("Comment") or general_track.get("comment"))
                description = _unescape_meta_val(general_track.get("Description") or general_track.get("description"))
                if not meta.overview:
                    if comment:
                        meta.overview = comment
                    elif description:
                        meta.overview = description

                # 7. Year (extract 4-digit number)
                rec_date = _unescape_meta_val(general_track.get("Recorded_Date") or general_track.get("recorded_date"))
                if rec_date and not meta.year:
                    match = re.search(r"\b\d{4}\b", rec_date)
                    if match:
                        meta.year = int(match.group(0))
                        meta.search_year = int(match.group(0))

                # 8. Genre -> Keywords
                genre = _unescape_meta_val(general_track.get("Genre") or general_track.get("genre"))
                if genre:
                    words = re.split(r"[;,]", genre)
                    cleaned_words = [w.strip().lower() for w in words if w.strip()]
                    if cleaned_words:
                        existing_keywords = meta.keywords
                        existing_list: list[str] = []
                        if existing_keywords:
                            # existing_keywords is guaranteed to be a list of strings
                            existing_list.extend([x.strip().lower() for x in existing_keywords if x.strip()])
                        for cw in cleaned_words:
                            if cw not in existing_list:
                                existing_list.append(cw)
                        meta.keywords = existing_list

                # 9. Language
                if not meta.book_language:
                    lang_val = _unescape_meta_val(general_track.get("Language") or general_track.get("language"))
                    if lang_val:
                        full, iso3 = resolve_book_language(lang_val)
                        if is_valid_book_language(full, iso3):
                            meta.book_language = full
                            meta.book_language_iso = iso3

                if not meta.book_language:
                    # Fallback: Audio track language (audiobooks)
                    for t in tracks:
                        if t.get("@type") == "Audio":
                            lang_val = _unescape_meta_val(t.get("Language") or t.get("language"))
                            if lang_val:
                                full, iso3 = resolve_book_language(lang_val)
                                if is_valid_book_language(full, iso3):
                                    meta.book_language = full
                                    meta.book_language_iso = iso3
                                    break

                if not meta.book_language:
                    # Fallback: Text track language (ebooks like PDF/EPUB)
                    for t in tracks:
                        if t.get("@type") == "Text":
                            lang_val = _unescape_meta_val(t.get("Language") or t.get("language"))
                            if lang_val:
                                full, iso3 = resolve_book_language(lang_val)
                                if is_valid_book_language(full, iso3):
                                    meta.book_language = full
                                    meta.book_language_iso = iso3
                                    break
        except Exception as ex:
            logger.debug(f"[yellow]Warning: Error extracting embedded book metadata: {ex}[/yellow]")

    fallback_author, fallback_title = book_identity_from_path(str(meta.path or videopath))
    if fallback_author:
        meta.author = _prefer_descriptive_source_author(str(meta.author or ""), fallback_author)
    if fallback_title:
        meta.title = _prefer_descriptive_source_title(str(meta.title or ""), str(meta.author or ""), fallback_title)

    local_title = str(meta.title or "").strip()
    local_author = str(meta.author or "").strip()

    # Series fallback from filename (embedded Calibre/MediaInfo tags take precedence)
    if not meta.book_series:
        fname_series, fname_index = _extract_series_from_filename(Path(videopath).name)
        if fname_series:
            meta.book_series = fname_series
            if fname_index and not meta.book_series_index:
                meta.book_series_index = fname_index

    # MyAnonamouse API search using torrent client comments (online lookup takes precedence)
    if not meta.torrent_comments and not meta.skip_auto_torrent and not meta.edit and config:
        from src.integrations.torrent_clients.client_manager import Clients

        try:
            client = Clients(config=config)
            await client.get_pathed_torrents((meta.path if meta.path is not None else videopath), meta)
        except Exception as e:
            logger.debug(f"[yellow]Warning: Could not search client for book torrent comments: {e}[/yellow]")

    mam_id = None
    if meta.torrent_comments:
        for comment_data in meta.torrent_comments:
            trackers = str(comment_data.get("trackers", ""))
            comment = str(comment_data.get("comment", ""))

            def _is_mam_host(url_or_host: str) -> bool:
                value = (url_or_host or "").strip()
                if not value:
                    return False
                parsed = urlparse(value)
                host = parsed.hostname
                if not host and "://" not in value:
                    host = urlparse(f"//{value}").hostname
                if not host:
                    return False
                host = host.lower().rstrip(".")
                return host == "myanonamouse.net" or host.endswith(".myanonamouse.net")

            is_mam = _is_mam_host(trackers)
            if not is_mam and comment_data.get("tracker_urls"):
                for tu in comment_data["tracker_urls"]:
                    if (isinstance(tu, dict) and _is_mam_host(str(tu.get("url", "")))) or (isinstance(tu, str) and _is_mam_host(tu)):
                        is_mam = True
                        break

            if is_mam:
                match = re.search(r"\bMID=(\d+)", comment)
                if match:
                    mam_id = match.group(1)
                    logger.debug(f"[cyan]Found MyAnonamouse ID {mam_id} in torrent comment[/cyan]")
                    break

    mam_data = None
    if mam_id:
        try:
            api_key = ""
            if config and "DEFAULT" in config:
                api_key = config["DEFAULT"].get("mam_api_key", "").strip() or config["DEFAULT"].get("mam_id", "").strip()
            api_key = api_key or os.environ.get("MAM_API_KEY", "").strip() or os.environ.get("MAM_ID", "").strip()

            from src.integrations.external_apis.myanonamouse import myanonamouse_manager

            mam_data = await myanonamouse_manager.search_by_id(mam_id, base_dir=base_dir, api_key=api_key)
            if mam_data:
                for key, val in mam_data.items():
                    if val:
                        # Enforce priority: CLI override > MAM > local metadata
                        is_override = False
                        if (
                            (key == "title" and cli_overrides["title"])
                            or (key == "author" and cli_overrides["author"])
                            or (key == "narrator" and cli_overrides["narrator"])
                            or (key == "publisher" and cli_overrides["publisher"])
                            or (key == "isbn" and cli_overrides["isbn"])
                            or (key == "asin" and cli_overrides["asin"])
                            or (key in ("book_language", "book_language_iso") and cli_overrides["book_language"])
                            or (key in ("year", "search_year") and cli_overrides["year"])
                            or (key == "keywords" and cli_overrides["keywords"])
                            or (key == "overview" and cli_overrides["overview"])
                        ):
                            is_override = True
                        if source_has(key):
                            is_override = True

                        if not is_override:
                            if key == "year":
                                meta[key] = int(val)
                            else:
                                meta[key] = val
                            if key == "year" and "search_year" not in mam_data:
                                meta.search_year = int(val)
        except Exception as ex:
            logger.debug(f"[yellow]Warning: MyAnonamouse API lookup failed: {ex}[/yellow]")

    if meta.isbn:
        validated_isbn = validate_isbn_checksum(str(meta.isbn))
        if validated_isbn:
            meta.isbn = validated_isbn
        else:
            logger.warning(f"[yellow]Ignoring invalid ISBN metadata: {meta.isbn}[/yellow]")
            meta.isbn = ""
            meta.book_isbn = ""

    # Google Books API search using ISBN (online lookup takes precedence)
    google_books_data = None
    isbn = meta.isbn
    if isbn:
        try:
            api_key = ""
            if config and "DEFAULT" in config:
                api_key = config["DEFAULT"].get("google_books_api_key", "").strip()
            from src.integrations.external_apis.google_books import google_books_manager

            google_books_data = await google_books_manager.search_by_isbn(isbn, base_dir=base_dir, api_key=api_key)
            if google_books_data:
                for key, val in google_books_data.items():
                    if val:
                        # Enforce priority: CLI override > MAM > Google Books > local metadata
                        is_override = False
                        if (
                            (key == "title" and cli_overrides["title"])
                            or (key == "author" and cli_overrides["author"])
                            or (key == "publisher" and cli_overrides["publisher"])
                            or (key == "isbn" and cli_overrides["isbn"])
                            or (key == "asin" and cli_overrides["asin"])
                            or (key in ("book_language", "book_language_iso") and cli_overrides["book_language"])
                            or (key in ("year", "search_year") and cli_overrides["year"])
                            or (key == "keywords" and cli_overrides["keywords"])
                            or (key == "overview" and cli_overrides["overview"])
                        ):
                            is_override = True
                        if source_has(key):
                            is_override = True

                        # Do not overwrite fields already populated by MAM, except for artwork (prefer Google Books cover)
                        if (
                            key != "artwork_url"
                            and mam_data
                            and (key in mam_data or (key == "book_language_iso" and "book_language" in mam_data) or (key == "search_year" and "year" in mam_data))
                        ):
                            is_override = True

                        if not is_override:
                            if key == "year":
                                meta[key] = int(val)
                            else:
                                meta[key] = val
                            if key == "year" and "search_year" not in google_books_data:
                                meta.search_year = int(val)
        except Exception as ex:
            logger.debug(f"[yellow]Warning: Google Books API lookup failed: {ex}[/yellow]")

    # OpenLibrary API search (online lookup takes precedence)
    openlibrary_data = None
    openlibrary_id = meta.openlibrary
    if openlibrary_id:
        from src.integrations.external_apis.openlibrary import openlibrary_manager

        openlibrary_data = await openlibrary_manager.search_by_work_id(openlibrary_id, base_dir=base_dir)
    elif meta.isbn:
        from src.integrations.external_apis.openlibrary import openlibrary_manager

        openlibrary_data = await openlibrary_manager.search_by_isbn(meta.isbn, base_dir=base_dir)

    if openlibrary_data:
        for key, val in openlibrary_data.items():
            if val:
                # Enforce priority: CLI override > MAM > Google Books > OpenLibrary > local metadata
                is_override = False
                if (
                    (key == "title" and cli_overrides["title"])
                    or (key == "author" and cli_overrides["author"])
                    or (key == "publisher" and cli_overrides["publisher"])
                    or (key == "isbn" and cli_overrides["isbn"])
                    or (key == "asin" and cli_overrides["asin"])
                    or (key in ("book_language", "book_language_iso") and cli_overrides["book_language"])
                    or (key in ("year", "search_year") and cli_overrides["year"])
                    or (key == "keywords" and cli_overrides["keywords"])
                    or (key == "overview" and cli_overrides["overview"])
                ):
                    is_override = True
                if source_has(key):
                    is_override = True

                # Do not overwrite fields already populated by MAM
                if mam_data and (key in mam_data or (key == "book_language_iso" and "book_language" in mam_data) or (key == "search_year" and "year" in mam_data)):
                    is_override = True

                # Do not overwrite fields already populated by Google Books
                if google_books_data and (
                    key in google_books_data or (key == "book_language_iso" and "book_language" in google_books_data) or (key == "search_year" and "year" in google_books_data)
                ):
                    is_override = True

                if not is_override:
                    if key == "year":
                        meta[key] = int(val)
                    else:
                        meta[key] = val
                    if key == "year" and "search_year" not in openlibrary_data:
                        meta.search_year = int(val)

    if not meta.publisher and not cli_overrides["publisher"]:
        inferred_publisher = _publisher_from_overview(str(meta.overview or ""))
        if inferred_publisher:
            meta.publisher = inferred_publisher

    exact_edition = _matching_isbn_metadata(meta, mam_data, google_books_data, openlibrary_data)
    exact_title = _matching_isbn_metadata(meta, google_books_data, openlibrary_data, mam_data)
    exact_year = _matching_isbn_metadata(meta, google_books_data, openlibrary_data, mam_data)
    exact_publisher = _matching_isbn_metadata(meta, mam_data, openlibrary_data, google_books_data)
    if exact_edition:
        if not local_title:
            filename_title = _strip_book_format_suffix(Path(videopath).stem)
            overview_tokens = _identity_tokens(str(exact_edition.get("overview") or meta.overview or ""))
            if len(_identity_tokens(filename_title) & overview_tokens) >= 2:
                local_title = filename_title
        edition_fields = (
            "title",
            "author",
            "publisher",
            "year",
            "search_year",
            "book_language",
            "book_language_iso",
            "overview",
            "keywords",
            "genres",
        )
        for key in edition_fields:
            if key == "publisher" and source_has(key):
                continue
            if key == "title" and exact_title:
                provider = exact_title
            elif key in {"year", "search_year"} and exact_year:
                provider = exact_year
            elif key == "publisher" and exact_publisher:
                provider = exact_publisher
            else:
                provider = exact_edition
            val = provider.get(key)
            if key == "title" and local_title and val:
                local_author_tokens = _author_identity_tokens(local_author)
                provider_author_tokens = _author_identity_tokens(str(exact_edition.get("author") or ""))
                authors_match = bool(local_author_tokens and provider_author_tokens and local_author_tokens == provider_author_tokens)
                overview_tokens = _identity_tokens(str(exact_edition.get("overview") or meta.overview or ""))
                local_title_supported = len(_identity_tokens(local_title) & overview_tokens) >= 2
                title_similarity = SequenceMatcher(None, _normalized_book_identity(local_title), _normalized_book_identity(str(val))).ratio()
                if title_similarity < 0.72 and (authors_match or local_title_supported):
                    val = local_title
            override_key = "book_language" if key == "book_language_iso" else key
            if val and not cli_overrides.get(override_key, False):
                meta[key] = int(val) if key in {"year", "search_year"} else val

    repair_title_source = local_title or str(fallback_title or "")
    if not cli_overrides["title"] and repair_title_source:
        meta.title = _prefer_descriptive_source_title(str(meta.title or ""), str(meta.author or ""), repair_title_source)

    if file_ext not in {"CBR", "CBZ"}:
        meta.comic = explicit_comic
        meta.manga = explicit_manga

    meta.title = _strip_book_format_suffix(str(meta.title or ""))
    if (
        fallback_author
        and fallback_title
        and not cli_overrides["author"]
        and not cli_overrides["title"]
        and _normalized_book_identity(str(meta.author or "")) == _normalized_book_identity(fallback_title)
        and _normalized_book_identity(meta.title) == _normalized_book_identity(fallback_author)
    ):
        meta.author = fallback_author
        meta.title = fallback_title

    if meta.unattended and not cli_overrides["title"] and not meta.get("trusted_book_layout", False):
        conflict = book_identity_conflict(meta, str(meta.path or videopath))
        if conflict:
            raise ItemProcessingError(conflict, str(meta.path or videopath))

    if meta.audiobook:
        filelist = meta.filelist
        total_duration, duration_formatted = await get_audiobook_duration(filelist)
        meta.audiobook_duration = total_duration
        meta.audiobook_duration_formatted = duration_formatted

        avg_bitrate = await get_audiobook_bitrate(filelist)
        if avg_bitrate is not None:
            meta.audiobook_bitrate = avg_bitrate

    detect_newspaper(meta)
    sanitize_book_language(meta)
    sanitize_book_author(meta)


async def get_audiobook_duration(filelist: list[str]) -> tuple[float, str]:
    """Calculate the sum of durations of all audio files in the file list using MediaInfo."""
    audio_files = [f for f in filelist if Path(f).suffix.lower() in AUDIOBOOK_EXTENSIONS]

    if not audio_files:
        return 0.0, ""

    def _get_file_duration(file_path: str) -> float:
        with contextlib.suppress(Exception):
            if not Path(file_path).is_file():
                return 0.0
            media_info = MediaInfo.parse(file_path)
            for track in media_info.tracks:
                if track.track_type == "General":
                    duration_ms = track.duration
                    if duration_ms is not None:
                        return float(duration_ms) / 1000.0
        return 0.0

    tasks = [asyncio.to_thread(_get_file_duration, f) for f in audio_files]
    durations = await asyncio.gather(*tasks)
    total_seconds = float(sum(durations))

    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)

    # Format as HH:MM:SS if hours > 0, otherwise MM:SS
    duration_formatted = f"{hours:02d}h {minutes:02d}m {seconds:02d}s" if hours > 0 else f"{minutes:02d}m {seconds:02d}s"

    return total_seconds, duration_formatted


async def get_audiobook_bitrate(filelist: list[str]) -> int | None:
    """Calculate the average bitrate (in kbps) of a sample of audio files (max 5) in the file list using MediaInfo."""
    audio_files = [f for f in filelist if Path(f).suffix.lower() in AUDIOBOOK_EXTENSIONS]

    # Limit to a maximum of 5 files to optimize performance
    audio_files = audio_files[:5]

    if not audio_files:
        return None

    def _get_file_bitrate(file_path: str) -> int | None:
        with contextlib.suppress(Exception):
            if not Path(file_path).is_file():
                return None
            media_info = MediaInfo.parse(file_path)
            for track in media_info.tracks:
                if track.track_type == "Audio":
                    track_data = track.to_data()
                    br = track_data.get("bit_rate") or track_data.get("BitRate")
                    if br is not None:
                        match = re.search(r"\d+", str(br))
                        if match:
                            return int(match.group(0))
            # Fallback to General track
            for track in media_info.tracks:
                if track.track_type == "General":
                    track_data = track.to_data()
                    br = track_data.get("overall_bit_rate") or track_data.get("OverallBitRate")
                    if br is not None:
                        match = re.search(r"\d+", str(br))
                        if match:
                            return int(match.group(0))
        return None

    tasks = [asyncio.to_thread(_get_file_bitrate, f) for f in audio_files]
    bitrates = await asyncio.gather(*tasks)

    valid_bitrates = [br for br in bitrates if br is not None]
    if not valid_bitrates:
        return None

    avg_bps = sum(valid_bitrates) / len(valid_bitrates)
    return round(avg_bps / 1000) if avg_bps >= 1000 else round(avg_bps)
