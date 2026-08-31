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
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from src.domain_models.book_language import (
    is_valid_book_language,
    resolve_book_language,
)
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
from src.integrations.media.book_extractors import (
    extract_pdf_page_count as _extract_pdf_page_count,
)
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
    {
        ".pdf",
        ".epub",
        ".mobi",
        ".azw",
        ".azw3",
        ".fb2",
        ".html",
        ".htm",
        ".chm",
        ".djvu",
        ".doc",
        ".docx",
        ".kfx",
        ".lit",
        ".pdb",
        ".txt",
        ".rtf",
        ".cbz",
        ".cbr",
    }
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
    if filelist and all(
        Path(file).suffix.lower() == ".txt" for file in filelist
    ):
        raise ItemProcessingError(
            "Plain-text TXT files are not supported as standalone book uploads."
        )


_TEXT_SIDECAR_STEMS = frozenset({"cover", "folder", "index", "info", "readme"})


def _normalized_book_stem(path: str) -> str:
    return re.sub(r"[\W_]+", " ", Path(path).stem.casefold()).strip()


def _book_files_in_directory(videoloc: str) -> list[str]:
    allowed = BOOK_EXTENSIONS | AUDIOBOOK_EXTENSIONS
    return sorted(
        str((Path(root) / filename).resolve())
        for root, _dirs, files in os.walk(videoloc)
        for filename in files
        if Path(filename).suffix.lower() in allowed
    )


def _is_text_sidecar(file: str, richer_stems: set[str]) -> bool:
    path = Path(file)
    if path.suffix.lower() not in {".txt", ".html", ".htm"}:
        return False
    stem = _normalized_book_stem(file)
    return path.stem.casefold() in _TEXT_SIDECAR_STEMS or any(
        richer == stem or richer.startswith(f"{stem} ")
        for richer in richer_stems
    )


def _richer_book_files(filelist: list[str]) -> list[str]:
    richer_extensions = BOOK_EXTENSIONS - {".txt", ".html", ".htm"}
    return [
        file
        for file in filelist
        if Path(file).suffix.lower() in richer_extensions
    ]


def _without_text_sidecars(filelist: list[str]) -> list[str]:
    richer = _richer_book_files(filelist)
    if not richer:
        return filelist
    richer_stems = {_normalized_book_stem(file) for file in richer}
    return [
        file for file in filelist if not _is_text_sidecar(file, richer_stems)
    ]


def _book_media_partitions(filelist: list[str]) -> tuple[list[str], list[str]]:
    ebooks = [
        file
        for file in filelist
        if Path(file).suffix.lower() in BOOK_EXTENSIONS
    ]
    audiobooks = [
        file
        for file in filelist
        if Path(file).suffix.lower() in AUDIOBOOK_EXTENSIONS
    ]
    return ebooks, audiobooks


def _validate_book_directory_media(filelist: list[str]) -> None:
    ebooks, audiobooks = _book_media_partitions(filelist)
    if ebooks and audiobooks:
        raise ItemProcessingError(
            "Ebook and audiobook files were found in the same path. Upload each media type separately."
        )
    if len(ebooks) <= 1:
        return
    filenames = ", ".join(Path(file).name for file in ebooks)
    raise ItemProcessingError(
        f"Multiple ebook files were found in one path ({filenames}). Upload each ebook file and format separately."
    )


def _directory_book_filelist(videoloc: str) -> list[str]:
    filelist = _book_files_in_directory(videoloc)
    if not filelist:
        raise ItemProcessingError(
            "No book or audiobook files were found in the selected path."
        )
    _reject_plain_text_only(filelist)
    filtered = _without_text_sidecars(filelist)
    _validate_book_directory_media(filtered)
    return filtered


def _primary_book_file(filelist: list[str]) -> str:
    return max(filelist, key=os.path.getsize)


def _apply_audiobook_flag(
    meta: Meta, videopath: str, filelist: list[str]
) -> None:
    primary_is_audio = Path(videopath).suffix.lower() in AUDIOBOOK_EXTENSIONS
    contains_audio = any(
        Path(file).suffix.lower() in AUDIOBOOK_EXTENSIONS for file in filelist
    )
    meta.audiobook = bool(meta.audiobook or primary_is_audio or contains_audio)


def resolve_book_filelist(
    meta: Meta,
    videoloc: str,
) -> tuple[str, list[str], str, str]:
    """Scan *videoloc* for book/audiobook files and update *meta* in-place."""
    if Path(videoloc).is_dir():
        filelist = _directory_book_filelist(videoloc)
        videopath = _primary_book_file(filelist)
    else:
        filelist = [videoloc]
        videopath = videoloc
        _reject_plain_text_only(filelist)
    meta.filelist = filelist
    meta.imdb_id = 0
    _apply_audiobook_flag(meta, videopath, filelist)
    search_term = Path(filelist[0]).name if filelist else ""
    return videopath, filelist, search_term, "file"


# ---------------------------------------------------------------------------
# Language resolution helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MediaInfo metadata extraction
# ---------------------------------------------------------------------------


def _metadata_scalar_text(value: Any) -> str:
    if not value or isinstance(value, dict):
        return ""
    return str(value).strip()


def _mi_extra(general_track: dict[str, Any], name: str) -> str:
    """Case-insensitive lookup of a freeform MediaInfo General extra tag."""
    extra = general_track.get("extra")
    if not isinstance(extra, dict):
        return ""
    values = cast(dict[str, Any], extra)
    target = name.casefold()
    for key, value in values.items():
        if key.casefold() == target:
            return _metadata_scalar_text(value)
    return ""


def _unescape_meta_val(val: Any) -> str | None:
    if val is None or isinstance(val, (dict, list)):
        return None
    import html

    return html.unescape(str(val)).strip()


_AUTHOR_PARTICLES = frozenset(
    {
        "al",
        "da",
        "de",
        "del",
        "della",
        "di",
        "dos",
        "du",
        "la",
        "le",
        "of",
        "van",
        "von",
        "y",
    }
)
_BOOK_FORMAT_SUFFIX = re.compile(
    r"\s*(?:\.(?:azw3?|cb[rz]|djvu|epub|fb2|html?|kfx|lit|mobi|pdf|rtf|txt)|\((?:azw3?|cb[rz]|djvu|epub|fb2|kfx|mobi|pdf|retail|scan|hybrid)\))\s*$",
    re.IGNORECASE,
)
_BOOK_PART_ONLY = re.compile(
    r"^(?:vol(?:ume)?|book|part|tome)\s*[#.]?\s*\d+(?:\.\d+)?$", re.IGNORECASE
)
_ASIN_VALUE = re.compile(
    r"\bASIN\s*[:#]?\s*([A-Z0-9]{10})(?![A-Z0-9])", re.IGNORECASE
)


def _author_words(value: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", value, flags=re.UNICODE)


def _substantive_author_words(words: list[str]) -> list[str]:
    return [word for word in words if word.casefold() not in _AUTHOR_PARTICLES]


def _comma_author_groups(value: str) -> list[list[str]]:
    return [_author_words(group) for group in value.split(",")]


def _author_word_shape_valid(words: list[str]) -> bool:
    return 2 <= len(words) <= 6 and words[0].casefold() not in {
        "a",
        "an",
        "the",
    }


def _capitalized_substantive_words(substantive: list[str]) -> bool:
    return bool(substantive) and all(word[0].isupper() for word in substantive)


def _comma_author_score(value: str) -> int | None:
    groups = _comma_author_groups(value)
    if len(groups) > 1 and all(2 <= len(group) <= 3 for group in groups):
        return 4
    return None


def _substantive_author_score(substantive: list[str]) -> int:
    return 3 if len(substantive) <= 3 else 1


def _author_likelihood(value: str) -> int:
    words = _author_words(value)
    if not _author_word_shape_valid(words):
        return 0
    substantive = _substantive_author_words(words)
    if not _capitalized_substantive_words(substantive):
        return -1
    comma_score = _comma_author_score(value)
    return (
        comma_score
        if comma_score is not None
        else _substantive_author_score(substantive)
    )


def _strip_book_format_suffix(value: str) -> str:
    return _BOOK_FORMAT_SUFFIX.sub("", value).strip()


def _extract_asin_identifier(value: Any) -> str:
    match = _ASIN_VALUE.search(str(value or "").strip())
    return match.group(1).upper() if match else ""


def _is_capitalized_mononym(value: str) -> bool:
    words = re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", value, flags=re.UNICODE)
    return len(words) == 1 and words[0][0].isupper()


def _book_path_name(path: str) -> str:
    source = Path(path)
    name = source.name if source.is_dir() else source.stem
    return re.sub(
        r"\s*\[AUDIOBOOK\]\s*$", "", name, flags=re.IGNORECASE
    ).strip()


def _fallback_book_title(name: str) -> tuple[str, str]:
    return "", re.sub(r"_\s+", ": ", name).strip()


def _first_part_is_author(first_score: int, second_score: int) -> bool:
    return first_score >= 3 and (second_score <= 1 or second_score >= 3)


def _second_part_is_author(
    first: str, second: str, first_score: int, second_score: int
) -> bool:
    if second_score >= 3 and first_score <= 0:
        return True
    return bool(
        first_score < 0
        and not re.search(r"\d", first)
        and _is_capitalized_mononym(second)
    )


def _book_identity_pair(first: str, second: str) -> tuple[str, str] | None:
    first_score = _author_likelihood(first)
    second_score = _author_likelihood(second)
    if _first_part_is_author(first_score, second_score):
        return first, second
    if _second_part_is_author(first, second, first_score, second_score):
        return second, first
    return None


def _clean_book_identity_title(title: str) -> str:
    cleaned = _strip_book_format_suffix(re.sub(r"_\s+", ": ", title).strip())
    parts = re.split(r"\s+-\s+", cleaned)
    if len(parts) > 1 and validate_isbn_checksum(parts[-1]):
        return " - ".join(parts[:-1]).strip()
    return cleaned


def book_identity_from_path(path: str) -> tuple[str, str]:
    name = _book_path_name(path)
    parts = re.split(r"\s+-\s+", name, maxsplit=1)
    if len(parts) != 2:
        return "", ""
    first, second = parts
    if _BOOK_PART_ONLY.fullmatch(second.strip()):
        return _fallback_book_title(name)
    pair = _book_identity_pair(first, second)
    if pair is None:
        return _fallback_book_title(name)
    author, title = pair
    return author.strip(), _clean_book_identity_title(title)


_IDENTITY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "book",
        "for",
        "in",
        "life",
        "of",
        "the",
        "through",
        "to",
        "with",
        "you",
        "your",
    }
)
_GENERIC_PROVIDER_TITLES = frozenset(
    {"anovel", "amemoir", "abiography", "ahistory", "aguide"}
)


def _identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in _IDENTITY_STOPWORDS
    }


def _normalized_book_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _author_identity_tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _source_title_is_more_descriptive(
    current_identity: str,
    author_identity: str,
    current_tokens: set[str],
    source_tokens: set[str],
) -> bool:
    if current_identity in _GENERIC_PROVIDER_TITLES and source_tokens:
        return True
    return bool(
        current_identity
        and current_identity == author_identity
        and current_tokens < source_tokens
    )


def _prefer_descriptive_source_title(
    current_title: str, author: str, source_title: str
) -> str:
    current_identity = _normalized_book_identity(current_title)
    author_identity = _normalized_book_identity(author)
    source_tokens = _identity_tokens(source_title)
    current_tokens = _identity_tokens(current_title)
    return (
        source_title
        if _source_title_is_more_descriptive(
            current_identity, author_identity, current_tokens, source_tokens
        )
        else current_title
    )


def _prefer_descriptive_source_author(
    current_author: str, source_author: str
) -> str:
    current_tokens = _author_identity_tokens(current_author)
    source_tokens = _author_identity_tokens(source_author)
    if (
        current_tokens
        and len(source_tokens) > len(current_tokens)
        and current_tokens < source_tokens
    ):
        return source_author
    return current_author


def _publisher_from_overview(value: str) -> str:
    import html

    plain = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    plain = re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain).replace("\u00a0", " ")
    plain = re.sub(r"[\u200b\u200e\u200f\ufeff]", "", plain)
    match = re.search(
        r"\bpublisher\b\s*(?::|\uFF1A)\s*([^\r\n]+)",
        plain,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def _provider_matches_isbn(
    provider: dict[str, Any] | None, current_isbn: str
) -> bool:
    if not provider:
        return False
    provider_isbn = validate_isbn_checksum(str(provider.get("isbn") or ""))
    return provider_isbn == current_isbn


def _matching_isbn_metadata(
    meta: Meta, *providers: dict[str, Any] | None
) -> dict[str, Any] | None:
    current_isbn = validate_isbn_checksum(str(meta.isbn or ""))
    if not current_isbn:
        return None
    return next(
        (
            provider
            for provider in providers
            if _provider_matches_isbn(provider, current_isbn)
        ),
        None,
    )


def _book_authors_match(path_author: str, metadata_author: str) -> bool:
    path_tokens = _author_identity_tokens(path_author)
    metadata_tokens = _author_identity_tokens(metadata_author)
    if path_tokens == metadata_tokens:
        return True
    shorter, longer = sorted((path_tokens, metadata_tokens), key=len)
    mononym = len(shorter) == 1 and len(next(iter(shorter), "")) >= 5
    return bool((len(shorter) >= 2 or mononym) and shorter < longer)


def _book_titles_conflict(path_title: str, metadata_title: str) -> bool:
    path_tokens = _identity_tokens(path_title)
    metadata_tokens = _identity_tokens(metadata_title)
    if len(path_tokens) < 2 or len(metadata_tokens) < 2:
        return False
    return not bool(path_tokens & metadata_tokens)


def _book_identity_complete(
    meta: Meta, path_author: str, path_title: str
) -> bool:
    return all(
        (
            bool(path_author),
            bool(path_title),
            bool(meta.author),
            bool(meta.title),
        )
    )


def book_identity_conflict(meta: Meta, path: str) -> str | None:
    path_author, path_title = book_identity_from_path(path)
    if not _book_identity_complete(meta, path_author, path_title):
        return None
    metadata_author = str(meta.author)
    if not _book_authors_match(path_author, metadata_author):
        return f"Book metadata author '{meta.author}' conflicts with source author '{path_author}'"
    if not _book_titles_conflict(path_title, str(meta.title)):
        return None
    return f"Book metadata title '{meta.title}' conflicts with source title '{path_title}' for author '{meta.author}'"


def _missing_book_value(value: Any) -> bool:
    return not value or str(value).strip().lower() in {"", "none", "null"}


def _required_book_field_missing(meta: Meta, field: str) -> bool:
    value = getattr(meta, field, None)
    if _missing_book_value(value):
        return True
    if field != "book_language":
        return False
    return not is_valid_book_language(
        str(value), str(meta.book_language_iso or "")
    )


def _audiobook_identifier_missing(meta: Meta) -> bool:
    isbn = validate_isbn_checksum(str(meta.isbn or ""))
    asin = str(meta.asin or "").strip().upper()
    return not isbn and not re.fullmatch(r"[A-Z0-9]{10}", asin)


def _audiobook_missing_fields(meta: Meta) -> list[str]:
    missing = [
        field
        for field in ("narrator", "publisher")
        if _missing_book_value(getattr(meta, field, None))
    ]
    if _audiobook_identifier_missing(meta):
        missing.append("isbn_or_asin")
    return missing


def missing_book_fields(meta: Meta) -> list[str]:
    missing = [
        field
        for field in ("title", "author", "year", "book_language")
        if _required_book_field_missing(meta, field)
    ]
    if meta.audiobook:
        missing.extend(_audiobook_missing_fields(meta))
    return missing


def _validated_isbns(value: str) -> set[str]:
    patterns = re.findall(
        r"(?<!\d)(?:97[89](?:[- ]?\d){10}|\d(?:[- ]?\d){8}[- ]?[\dXx])(?!\d)",
        value,
    )
    return {
        validated
        for candidate in patterns
        if (validated := validate_isbn_checksum(candidate))
    }


_EPUB_IDENTIFIER_EXTENSIONS = frozenset(
    {".opf", ".xhtml", ".html", ".htm", ".xml", ".ncx"}
)


def _is_epub_identifier_member(member: zipfile.ZipInfo) -> bool:
    return Path(member.filename).suffix.lower() in _EPUB_IDENTIFIER_EXTENSIONS


def _selected_epub_identifier_members(
    members: list[zipfile.ZipInfo],
) -> list[zipfile.ZipInfo]:
    return [member for member in members if _is_epub_identifier_member(member)]


def _epub_identifier_members(
    members: list[zipfile.ZipInfo],
) -> list[zipfile.ZipInfo] | None:
    if len(members) > 4096:
        return None
    selected = _selected_epub_identifier_members(members)
    total_size = sum(member.file_size for member in selected)
    return selected if total_size <= 16 * 1024 * 1024 else None


def _member_identifiers(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo
) -> tuple[set[str], set[str]] | None:
    payload = _safe_zip_member_bytes(archive, member.filename)
    if payload is None:
        return None
    text = payload.decode("utf-8", errors="ignore")
    return _validated_isbns(text), set(
        re.findall(r"\bB0[A-Z0-9]{8}\b", text.upper())
    )


def _epub_content_identifiers(path: str) -> tuple[set[str], set[str]]:
    isbns: set[str] = set()
    asins: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = _epub_identifier_members(archive.infolist())
            if members is None:
                return set(), set()
            for member in members:
                identifiers = _member_identifiers(archive, member)
                if identifiers is None:
                    return set(), set()
                member_isbns, member_asins = identifiers
                isbns.update(member_isbns)
                asins.update(member_asins)
    except OSError, zipfile.BadZipFile:
        return set(), set()
    return isbns, asins


def _single_or_conflicting_isbn(values: set[str], path: str) -> str | None:
    if len(values) == 1:
        return next(iter(values))
    if len(values) <= 1:
        return None
    rendered = ", ".join(sorted(values))
    raise ItemProcessingError(
        f"Conflicting EPUB ISBNs could not be resolved ({rendered}). Re-run with --isbn using the identifier for this exact edition.",
        path,
    )


def _resolved_epub_isbn(
    primary_isbn: str | None,
    filename_isbns: set[str],
    content_isbns: set[str],
    path: str,
) -> str | None:
    filename_isbn = _single_or_conflicting_isbn(filename_isbns, path)
    if filename_isbn:
        return filename_isbn
    if primary_isbn:
        return primary_isbn
    return _single_or_conflicting_isbn(content_isbns, path)


def _reconcile_epub_identifiers(
    meta: Meta, epub_meta: dict[str, Any], path: str
) -> None:
    primary_isbn = validate_isbn_checksum(str(epub_meta.get("isbn", "")))
    filename_isbns = _validated_isbns(Path(path).stem)
    content_isbns, content_asins = _epub_content_identifiers(path)
    resolved = _resolved_epub_isbn(
        primary_isbn, filename_isbns, content_isbns, path
    )
    if resolved:
        epub_meta["isbn"] = resolved
    if not meta.book_asin and not meta.asin and len(content_asins) == 1:
        epub_meta["asin"] = next(iter(content_asins))


@dataclass
class _BookPrepContext:
    meta: Meta
    videopath: str
    base_dir: str
    config: dict[str, Any] | None
    file_ext: str
    explicit_comic: bool
    explicit_manga: bool
    cli_overrides: dict[str, bool]
    source_metadata_fields: set[str]


def _book_cli_overrides(meta: Meta) -> dict[str, bool]:
    return {
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


def _book_default_config(config: dict[str, Any] | None) -> dict[str, Any]:
    if not config:
        return {}
    value = config.get("DEFAULT", {})
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _warn_missing_google_books_key(config: dict[str, Any] | None) -> None:
    api_key = str(
        _book_default_config(config).get("google_books_api_key", "")
    ).strip()
    if api_key:
        return
    logger.warning(
        "[bold red]Warning: Google Books API key is not configured. Book metadata searches will be limited and incomplete.[/bold red]"
    )


def _initialize_book_prep(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None,
) -> _BookPrepContext:
    meta.category = "BOOK"
    meta.search_year = ""
    meta.resolution = "Other"
    meta.hfr = False
    meta.sd = 0
    meta.valid_mi_settings = True
    _warn_missing_google_books_key(config)
    file_ext = Path(videopath).suffix.lstrip(".").upper()
    explicit_comic = bool(meta.comic)
    explicit_manga = bool(meta.manga)
    if file_ext in {"CBR", "CBZ"}:
        meta.comic = True
    return _BookPrepContext(
        meta=meta,
        videopath=videopath,
        base_dir=base_dir,
        config=config,
        file_ext=file_ext,
        explicit_comic=explicit_comic,
        explicit_manga=explicit_manga,
        cli_overrides=_book_cli_overrides(meta),
        source_metadata_fields=set(),
    )


_SOURCE_OVERRIDE_KEYS = {
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


def _source_field_blocked(ctx: _BookPrepContext, key: str) -> bool:
    override_key = _SOURCE_OVERRIDE_KEYS.get(key)
    return bool(override_key and ctx.cli_overrides[override_key])


def _apply_source_language(ctx: _BookPrepContext, value: Any) -> None:
    if ctx.cli_overrides["book_language"]:
        return
    full, iso3 = resolve_book_language(str(value))
    if not is_valid_book_language(full, iso3):
        return
    ctx.meta.book_language = full
    ctx.meta.book_language_iso = iso3
    ctx.source_metadata_fields.update({"book_language", "book_language_iso"})


def _apply_source_scalar(ctx: _BookPrepContext, key: str, value: Any) -> None:
    if _source_field_blocked(ctx, key):
        return
    if key == "isbn":
        value = validate_isbn_checksum(str(value))
        if not value:
            return
    if key == "year":
        ctx.meta[key] = int(value)
        ctx.meta.search_year = int(value)
        ctx.source_metadata_fields.add("search_year")
    else:
        ctx.meta[key] = value
    ctx.source_metadata_fields.add(key)


def _apply_source_metadata(
    ctx: _BookPrepContext, extracted: dict[str, Any]
) -> None:
    for key, value in extracted.items():
        if not value:
            continue
        if key == "book_language_raw":
            _apply_source_language(ctx, value)
            continue
        _apply_source_scalar(ctx, key, value)


def _source_has(ctx: _BookPrepContext, key: str) -> bool:
    if key in ctx.source_metadata_fields:
        return True
    if key == "search_year":
        return "year" in ctx.source_metadata_fields
    if key == "book_language_iso":
        return "book_language" in ctx.source_metadata_fields
    return False


def _is_local_book_file(ctx: _BookPrepContext, *suffixes: str) -> bool:
    return (
        ctx.videopath.lower().endswith(suffixes)
        and Path(ctx.videopath).is_file()
    )


def _extract_epub_source(ctx: _BookPrepContext) -> None:
    if not _is_local_book_file(ctx, ".epub"):
        return
    ctx.meta.epubmeta_output = _get_epubmeta_output(ctx.videopath)
    epub_meta = _extract_epub_metadata(ctx.videopath)
    if not epub_meta:
        return
    if not ctx.cli_overrides["isbn"]:
        _reconcile_epub_identifiers(ctx.meta, epub_meta, ctx.videopath)
    logger.debug(f"[cyan]EPUB metadata extracted: {epub_meta}[/cyan]")
    _apply_source_metadata(ctx, epub_meta)


def _extract_comic_source(ctx: _BookPrepContext) -> None:
    if not _is_local_book_file(ctx, ".cbr", ".cbz"):
        return
    metadata = _extract_cbr_cbz_metadata(ctx.videopath)
    if not metadata:
        return
    logger.debug(f"[cyan]CBR/CBZ metadata extracted: {metadata}[/cyan]")
    _apply_source_metadata(ctx, metadata)


def _extract_mobi_source(ctx: _BookPrepContext) -> None:
    if not _is_local_book_file(ctx, ".mobi", ".azw", ".azw3"):
        return
    metadata = _extract_mobi_metadata(ctx.videopath)
    if not metadata:
        return
    logger.debug(f"[cyan]MOBI metadata extracted: {metadata}[/cyan]")
    _apply_source_metadata(ctx, metadata)


def _extract_pdf_source(ctx: _BookPrepContext) -> None:
    if not _is_local_book_file(ctx, ".pdf"):
        return
    page_count = _extract_pdf_page_count(ctx.videopath)
    if page_count:
        ctx.meta.page_count = page_count
    pdf_isbn = _extract_isbn_from_pdf(ctx.videopath)
    if pdf_isbn and not ctx.meta.isbn:
        ctx.meta.isbn = pdf_isbn
        logger.debug(f"[cyan]PDF ISBN extracted: {pdf_isbn}[/cyan]")


def _extract_local_book_sources(ctx: _BookPrepContext) -> None:
    _extract_epub_source(ctx)
    _extract_comic_source(ctx)
    _extract_mobi_source(ctx)
    _extract_pdf_source(ctx)


async def _export_book_mediainfo(ctx: _BookPrepContext) -> None:
    if ctx.meta.edit:
        return
    try:
        ctx.meta.mediainfo = await export_info(
            ctx.videopath,
            ctx.meta.isdir,
            ctx.meta.uuid,
            ctx.base_dir,
            is_dvd=(ctx.meta.is_disc == "DVD"),
        )
    except MediaInfoError as error:
        logger.warning(
            f"[yellow]MediaInfo could not inspect book/audiobook release: {error}[/yellow]"
        )
        logger.debug(error.debug_details)
        ctx.meta.mediainfo = {}
    except Exception as error:
        logger.warning(
            f"[yellow]MediaInfo export failed for book/audiobook: {error}[/yellow]"
        )
        ctx.meta.mediainfo = {}


def _dict_value(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], value)
        if isinstance(item, dict)
    ]


def _book_mediainfo_tracks(meta: Meta) -> list[dict[str, Any]]:
    media = _dict_value(_dict_value(meta.mediainfo).get("media", {}))
    return _dict_items(media.get("track", []))


def _general_book_track(tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (track for track in tracks if track.get("@type") == "General"), None
    )


def _first_track_value(track: dict[str, Any], *keys: str) -> str | None:
    raw = next(
        (track.get(key) for key in keys if track.get(key) is not None), None
    )
    return _unescape_meta_val(raw)


def _detected_audiobook_edition(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        match = re.search(r"\b(unabridged|abridged)\b", value, re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
    return None


def _clean_embedded_book_title(value: str) -> str:
    cleaned = re.sub(r"\s*\[[^\]]*\]", "", value)
    cleaned = re.sub(
        r"\s*[\(\[\{-]?\s*\b(unabridged|abridged)\b\s*[\)\]\}]?\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip().strip("-").strip()


def _apply_detected_edition(meta: Meta, edition: str | None) -> None:
    if edition and not meta.edition:
        meta.edition = edition


def _apply_embedded_title_value(
    meta: Meta, album: str | None, track_name: str | None
) -> None:
    if not meta.title:
        meta.title = album or track_name or meta.title


def _clean_embedded_title_if_allowed(ctx: _BookPrepContext) -> None:
    if not ctx.cli_overrides["title"] and ctx.meta.title:
        ctx.meta.title = _clean_embedded_book_title(str(ctx.meta.title))


def _apply_embedded_title(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    album = _first_track_value(general, "Album", "album")
    track_name = _first_track_value(general, "Track_name", "track_name")
    _apply_detected_edition(
        ctx.meta, _detected_audiobook_edition(album, track_name)
    )
    _apply_embedded_title_value(ctx.meta, album, track_name)
    _clean_embedded_title_if_allowed(ctx)


def _set_book_field_if_empty(meta: Meta, field: str, value: Any) -> None:
    if value and not getattr(meta, field, None):
        setattr(meta, field, value)


def _apply_embedded_people(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    performer = _first_track_value(general, "Performer", "performer")
    album_performer = _first_track_value(
        general, "Album_Performer", "album_performer"
    )
    _set_book_field_if_empty(ctx.meta, "author", performer or album_performer)
    _set_book_field_if_empty(
        ctx.meta,
        "narrator",
        _first_track_value(general, "Composer", "composer"),
    )
    _set_book_field_if_empty(
        ctx.meta,
        "publisher",
        _first_track_value(general, "Publisher", "publisher"),
    )


def _extra_track_value(general: dict[str, Any], *keys: str) -> Any:
    extra = general.get("extra")
    if not isinstance(extra, dict):
        return None
    values = cast(dict[str, Any], extra)
    return next(
        (values.get(key) for key in keys if values.get(key) is not None), None
    )


def _identifier_track_value(general: dict[str, Any], *keys: str) -> str | None:
    value = next(
        (general.get(key) for key in keys if general.get(key) is not None),
        None,
    )
    if value is None:
        value = _extra_track_value(general, *keys)
    return _unescape_meta_val(value)


def _apply_embedded_isbn_or_asin(meta: Meta, isbn_value: str | None) -> None:
    asin = _extract_asin_identifier(isbn_value)
    if asin and not meta.asin:
        meta.asin = asin
        return
    if isbn_value and not meta.isbn:
        meta.isbn = isbn_value


def _normalized_asin_value(value: str | None) -> str:
    return _extract_asin_identifier(value) or str(value or "").strip().upper()


def _apply_embedded_asin(meta: Meta, asin_value: str | None) -> None:
    normalized = _normalized_asin_value(asin_value)
    if (
        normalized
        and re.fullmatch(r"[A-Z0-9]{10}", normalized)
        and not meta.asin
    ):
        meta.asin = normalized


def _apply_embedded_identifiers(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    _apply_embedded_isbn_or_asin(
        ctx.meta, _identifier_track_value(general, "ISBN", "isbn")
    )
    _apply_embedded_asin(
        ctx.meta, _identifier_track_value(general, "ASIN", "asin")
    )


def _apply_embedded_series(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    if ctx.meta.book_series:
        return
    series = _mi_extra(general, "SERIES")
    if not series:
        return
    ctx.meta.book_series = series
    part = _mi_extra(general, "SERIESPART")
    if part and not ctx.meta.book_series_index:
        ctx.meta.book_series_index = _normalize_series_index(part)


def _apply_embedded_overview(meta: Meta, general: dict[str, Any]) -> None:
    if meta.overview:
        return
    meta.overview = (
        _first_track_value(general, "Comment", "comment")
        or _first_track_value(general, "Description", "description")
        or meta.overview
    )


def _recorded_year(general: dict[str, Any]) -> int | None:
    recorded = _first_track_value(general, "Recorded_Date", "recorded_date")
    if not recorded:
        return None
    match = re.search(r"\b\d{4}\b", recorded)
    return int(match.group(0)) if match else None


def _apply_embedded_year(meta: Meta, general: dict[str, Any]) -> None:
    if meta.year:
        return
    year = _recorded_year(general)
    if year is not None:
        meta.year = year
        meta.search_year = year


def _apply_embedded_overview_year(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    _apply_embedded_overview(ctx.meta, general)
    _apply_embedded_year(ctx.meta, general)


def _normalized_keyword_values(existing: Any) -> list[str]:
    return [
        str(item).strip().lower()
        for item in cast(list[Any], existing or [])
        if str(item).strip()
    ]


def _genre_keyword_values(genre: str) -> list[str]:
    return [
        word.strip().lower()
        for word in re.split(r"[;,]", genre)
        if word.strip()
    ]


def _merged_keywords(existing: Any, genre: str) -> list[str]:
    values = _normalized_keyword_values(existing)
    for cleaned in _genre_keyword_values(genre):
        if cleaned not in values:
            values.append(cleaned)
    return values


def _apply_embedded_genre(
    ctx: _BookPrepContext, general: dict[str, Any]
) -> None:
    genre = _first_track_value(general, "Genre", "genre")
    if genre:
        ctx.meta.keywords = _merged_keywords(ctx.meta.keywords, genre)


def _resolved_track_language(track: dict[str, Any]) -> tuple[str, str] | None:
    language = _first_track_value(track, "Language", "language")
    if not language:
        return None
    full, iso3 = resolve_book_language(language)
    return (full, iso3) if is_valid_book_language(full, iso3) else None


def _first_valid_track_language(
    tracks: list[dict[str, Any]], track_type: str
) -> tuple[str, str] | None:
    for track in tracks:
        if track.get("@type") != track_type:
            continue
        resolved = _resolved_track_language(track)
        if resolved is not None:
            return resolved
    return None


def _apply_embedded_language(
    ctx: _BookPrepContext,
    general: dict[str, Any],
    tracks: list[dict[str, Any]],
) -> None:
    if ctx.meta.book_language:
        return
    resolved = _resolved_track_language(general)
    if resolved is None:
        resolved = _first_valid_track_language(tracks, "Audio")
    if resolved is None:
        resolved = _first_valid_track_language(tracks, "Text")
    if resolved is not None:
        ctx.meta.book_language, ctx.meta.book_language_iso = resolved


def _extract_embedded_book_metadata(ctx: _BookPrepContext) -> None:
    if not ctx.meta.mediainfo:
        return
    try:
        tracks = _book_mediainfo_tracks(ctx.meta)
        general = _general_book_track(tracks)
        if general is None:
            return
        _apply_embedded_title(ctx, general)
        _apply_embedded_people(ctx, general)
        _apply_embedded_identifiers(ctx, general)
        _apply_embedded_series(ctx, general)
        _apply_embedded_overview_year(ctx, general)
        _apply_embedded_genre(ctx, general)
        _apply_embedded_language(ctx, general, tracks)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Error extracting embedded book metadata: {error}[/yellow]"
        )


def _apply_path_author(meta: Meta, fallback_author: str) -> None:
    if fallback_author:
        meta.author = _prefer_descriptive_source_author(
            str(meta.author or ""), fallback_author
        )


def _apply_path_title(meta: Meta, fallback_title: str) -> None:
    if fallback_title:
        meta.title = _prefer_descriptive_source_title(
            str(meta.title or ""), str(meta.author or ""), fallback_title
        )


def _apply_path_identity(ctx: _BookPrepContext) -> tuple[str, str, str, str]:
    fallback_author, fallback_title = book_identity_from_path(
        str(ctx.meta.path or ctx.videopath)
    )
    _apply_path_author(ctx.meta, fallback_author)
    _apply_path_title(ctx.meta, fallback_title)
    return (
        fallback_author,
        fallback_title,
        str(ctx.meta.title or "").strip(),
        str(ctx.meta.author or "").strip(),
    )


def _apply_filename_series(ctx: _BookPrepContext) -> None:
    if ctx.meta.book_series:
        return
    series, index = _extract_series_from_filename(Path(ctx.videopath).name)
    if not series:
        return
    ctx.meta.book_series = series
    if index and not ctx.meta.book_series_index:
        ctx.meta.book_series_index = index


def _torrent_comment_lookup_allowed(ctx: _BookPrepContext) -> bool:
    meta = ctx.meta
    return all(
        (
            not meta.torrent_comments,
            not meta.skip_auto_torrent,
            not meta.edit,
            bool(ctx.config),
        )
    )


async def _maybe_fetch_torrent_comments(ctx: _BookPrepContext) -> None:
    if not _torrent_comment_lookup_allowed(ctx):
        return
    from src.integrations.torrent_clients.client_manager import Clients

    try:
        client = Clients(config=cast(dict[str, Any], ctx.config))
        source = ctx.meta.path if ctx.meta.path is not None else ctx.videopath
        await client.get_pathed_torrents(source, ctx.meta)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Could not search client for book torrent comments: {error}[/yellow]"
        )


def _candidate_hostname(candidate: str) -> str | None:
    host = urlparse(candidate).hostname
    if host is not None:
        return host
    return (
        urlparse(f"//{candidate}").hostname if "://" not in candidate else None
    )


def _parsed_hostname(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    return str(_candidate_hostname(candidate) or "").lower().rstrip(".")


def _mam_host(value: str) -> bool:
    host = _parsed_hostname(value)
    return bool(
        host
        and (host == "myanonamouse.net" or host.endswith(".myanonamouse.net"))
    )


def _tracker_url_text(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(cast(dict[str, Any], raw).get("url", ""))
    return raw if isinstance(raw, str) else ""


def _comment_tracker_urls(comment_data: dict[str, Any]) -> list[str]:
    urls = comment_data.get("tracker_urls", [])
    if not isinstance(urls, list):
        return []
    return [_tracker_url_text(raw) for raw in cast(list[Any], urls)]


def _comment_has_mam_tracker(comment_data: dict[str, Any]) -> bool:
    if _mam_host(str(comment_data.get("trackers", ""))):
        return True
    return any(
        _mam_host(value) for value in _comment_tracker_urls(comment_data)
    )


def _mam_id_from_comment(comment_data: dict[str, Any]) -> str | None:
    if not _comment_has_mam_tracker(comment_data):
        return None
    match = re.search(r"\bMID=(\d+)", str(comment_data.get("comment", "")))
    return match.group(1) if match else None


def _mam_id_from_comments(meta: Meta) -> str | None:
    comments = meta.torrent_comments
    if not isinstance(comments, list):
        return None
    for raw in cast(list[Any], comments):
        if not isinstance(raw, dict):
            continue
        mam_id = _mam_id_from_comment(cast(dict[str, Any], raw))
        if mam_id:
            logger.debug(
                f"[cyan]Found MyAnonamouse ID {mam_id} in torrent comment[/cyan]"
            )
            return mam_id
    return None


def _mam_api_key(config: dict[str, Any] | None) -> str:
    defaults = _book_default_config(config)
    configured = (
        str(defaults.get("mam_api_key", "")).strip()
        or str(defaults.get("mam_id", "")).strip()
    )
    return (
        configured
        or os.environ.get("MAM_API_KEY", "").strip()
        or os.environ.get("MAM_ID", "").strip()
    )


def _provider_override_key(key: str) -> str:
    if key == "book_language_iso":
        return "book_language"
    if key == "search_year":
        return "year"
    return key


def _provider_contains_field(
    provider: dict[str, Any] | None, key: str
) -> bool:
    if not provider:
        return False
    if key in provider:
        return True
    if key == "book_language_iso":
        return "book_language" in provider
    if key == "search_year":
        return "year" in provider
    return False


def _cli_or_source_blocks_provider(ctx: _BookPrepContext, key: str) -> bool:
    override_key = _provider_override_key(key)
    return bool(
        ctx.cli_overrides.get(override_key, False) or _source_has(ctx, key)
    )


def _prior_provider_has_field(
    prior: tuple[dict[str, Any] | None, ...], key: str
) -> bool:
    return any(_provider_contains_field(provider, key) for provider in prior)


def _provider_field_blocked(
    ctx: _BookPrepContext,
    key: str,
    prior: tuple[dict[str, Any] | None, ...],
    *,
    artwork_may_override: bool = False,
) -> bool:
    if _cli_or_source_blocks_provider(ctx, key):
        return True
    if artwork_may_override and key == "artwork_url":
        return False
    return _prior_provider_has_field(prior, key)


def _apply_provider_value(
    meta: Meta, key: str, value: Any, data: dict[str, Any]
) -> None:
    meta[key] = int(value) if key == "year" else value
    if key == "year" and "search_year" not in data:
        meta.search_year = int(value)


def _apply_provider_metadata(
    ctx: _BookPrepContext,
    data: dict[str, Any] | None,
    *,
    prior: tuple[dict[str, Any] | None, ...] = (),
    artwork_may_override: bool = False,
) -> None:
    if not data:
        return
    for key, value in data.items():
        if not value:
            continue
        if _provider_field_blocked(
            ctx, key, prior, artwork_may_override=artwork_may_override
        ):
            continue
        _apply_provider_value(ctx.meta, key, value, data)


async def _fetch_mam_metadata(
    ctx: _BookPrepContext, mam_id: str | None
) -> dict[str, Any] | None:
    if not mam_id:
        return None
    try:
        from src.integrations.external_apis.myanonamouse import (
            myanonamouse_manager,
        )

        result = await myanonamouse_manager.search_by_id(
            mam_id, base_dir=ctx.base_dir, api_key=_mam_api_key(ctx.config)
        )
        return (
            cast(dict[str, Any], result) if isinstance(result, dict) else None
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: MyAnonamouse API lookup failed: {error}[/yellow]"
        )
        return None


def _validate_meta_isbn(meta: Meta) -> str:
    if not meta.isbn:
        return ""
    validated = validate_isbn_checksum(str(meta.isbn))
    if validated:
        meta.isbn = validated
        return validated
    logger.warning(
        f"[yellow]Ignoring invalid ISBN metadata: {meta.isbn}[/yellow]"
    )
    meta.isbn = ""
    meta.book_isbn = ""
    return ""


async def _fetch_google_metadata(
    ctx: _BookPrepContext, isbn: str
) -> dict[str, Any] | None:
    if not isbn:
        return None
    try:
        from src.integrations.external_apis.google_books import (
            google_books_manager,
        )

        api_key = str(
            _book_default_config(ctx.config).get("google_books_api_key", "")
        ).strip()
        result = await google_books_manager.search_by_isbn(
            isbn, base_dir=ctx.base_dir, api_key=api_key
        )
        return (
            cast(dict[str, Any], result) if isinstance(result, dict) else None
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Google Books API lookup failed: {error}[/yellow]"
        )
        return None


async def _fetch_openlibrary_metadata(
    ctx: _BookPrepContext,
) -> dict[str, Any] | None:
    from src.integrations.external_apis.openlibrary import openlibrary_manager

    if ctx.meta.openlibrary:
        result = await openlibrary_manager.search_by_work_id(
            ctx.meta.openlibrary, base_dir=ctx.base_dir
        )
    elif ctx.meta.isbn:
        result = await openlibrary_manager.search_by_isbn(
            ctx.meta.isbn, base_dir=ctx.base_dir
        )
    else:
        return None
    return cast(dict[str, Any], result) if isinstance(result, dict) else None


def _infer_publisher_from_overview(ctx: _BookPrepContext) -> None:
    if ctx.meta.publisher or ctx.cli_overrides["publisher"]:
        return
    inferred = _publisher_from_overview(str(ctx.meta.overview or ""))
    if inferred:
        ctx.meta.publisher = inferred


def _exact_edition_providers(
    meta: Meta,
    mam_data: dict[str, Any] | None,
    google_data: dict[str, Any] | None,
    open_data: dict[str, Any] | None,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    return (
        _matching_isbn_metadata(meta, mam_data, google_data, open_data),
        _matching_isbn_metadata(meta, google_data, open_data, mam_data),
        _matching_isbn_metadata(meta, google_data, open_data, mam_data),
        _matching_isbn_metadata(meta, mam_data, open_data, google_data),
    )


def _local_title_supported(local_title: str, overview: str) -> bool:
    return len(_identity_tokens(local_title) & _identity_tokens(overview)) >= 2


def _maybe_filename_local_title(
    local_title: str,
    videopath: str,
    overview: str,
) -> str:
    if local_title:
        return local_title
    filename_title = _strip_book_format_suffix(Path(videopath).stem)
    return (
        filename_title
        if _local_title_supported(filename_title, overview)
        else ""
    )


def _edition_provider_for_field(
    key: str,
    exact_edition: dict[str, Any],
    exact_title: dict[str, Any] | None,
    exact_year: dict[str, Any] | None,
    exact_publisher: dict[str, Any] | None,
) -> dict[str, Any]:
    preferred = {
        "title": exact_title,
        "year": exact_year,
        "search_year": exact_year,
        "publisher": exact_publisher,
    }.get(key)
    return preferred if preferred else exact_edition


def _exact_authors_match(
    local_author: str, exact_edition: dict[str, Any]
) -> bool:
    local_tokens = _author_identity_tokens(local_author)
    provider_tokens = _author_identity_tokens(
        str(exact_edition.get("author") or "")
    )
    return bool(
        local_tokens and provider_tokens and local_tokens == provider_tokens
    )


def _provider_title_similarity(local_title: str, value: Any) -> float:
    return SequenceMatcher(
        None,
        _normalized_book_identity(local_title),
        _normalized_book_identity(str(value)),
    ).ratio()


def _prefer_exact_local_title(
    local_title: str,
    local_author: str,
    exact_edition: dict[str, Any],
    value: Any,
    overview: str,
) -> bool:
    if not local_title or not value:
        return False
    supported = _exact_authors_match(
        local_author, exact_edition
    ) or _local_title_supported(local_title, overview)
    return supported and _provider_title_similarity(local_title, value) < 0.72


def _exact_title_value(
    local_title: str,
    local_author: str,
    exact_edition: dict[str, Any],
    value: Any,
    overview: str,
) -> Any:
    return (
        local_title
        if _prefer_exact_local_title(
            local_title, local_author, exact_edition, value, overview
        )
        else value
    )


_EXACT_EDITION_FIELDS = (
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


def _exact_edition_field_blocked(ctx: _BookPrepContext, key: str) -> bool:
    if key == "publisher" and _source_has(ctx, key):
        return True
    override_key = "book_language" if key == "book_language_iso" else key
    return bool(ctx.cli_overrides.get(override_key, False))


def _exact_edition_field_value(
    key: str,
    local_title: str,
    local_author: str,
    exact_edition: dict[str, Any],
    exact_title: dict[str, Any] | None,
    exact_year: dict[str, Any] | None,
    exact_publisher: dict[str, Any] | None,
    overview: str,
) -> Any:
    provider = _edition_provider_for_field(
        key, exact_edition, exact_title, exact_year, exact_publisher
    )
    value = provider.get(key)
    if key != "title":
        return value
    return _exact_title_value(
        local_title, local_author, exact_edition, value, overview
    )


def _apply_exact_edition_field(
    ctx: _BookPrepContext,
    key: str,
    value: Any,
) -> None:
    if not value or _exact_edition_field_blocked(ctx, key):
        return
    ctx.meta[key] = int(value) if key in {"year", "search_year"} else value


def _apply_exact_edition(
    ctx: _BookPrepContext,
    local_title: str,
    local_author: str,
    mam_data: dict[str, Any] | None,
    google_data: dict[str, Any] | None,
    open_data: dict[str, Any] | None,
) -> str:
    exact_edition, exact_title, exact_year, exact_publisher = (
        _exact_edition_providers(ctx.meta, mam_data, google_data, open_data)
    )
    if not exact_edition:
        return local_title
    overview = str(exact_edition.get("overview") or ctx.meta.overview or "")
    local_title = _maybe_filename_local_title(
        local_title, ctx.videopath, overview
    )
    for key in _EXACT_EDITION_FIELDS:
        value = _exact_edition_field_value(
            key,
            local_title,
            local_author,
            exact_edition,
            exact_title,
            exact_year,
            exact_publisher,
            overview,
        )
        _apply_exact_edition_field(ctx, key, value)
    return local_title


def _restore_comic_flags(ctx: _BookPrepContext) -> None:
    if ctx.file_ext in {"CBR", "CBZ"}:
        return
    ctx.meta.comic = ctx.explicit_comic
    ctx.meta.manga = ctx.explicit_manga


def _path_identity_is_swapped(
    ctx: _BookPrepContext, fallback_author: str, fallback_title: str
) -> bool:
    author_matches_title = _normalized_book_identity(
        str(ctx.meta.author or "")
    ) == _normalized_book_identity(fallback_title)
    title_matches_author = _normalized_book_identity(
        str(ctx.meta.title or "")
    ) == _normalized_book_identity(fallback_author)
    return author_matches_title and title_matches_author


def _swapped_path_repair_allowed(
    ctx: _BookPrepContext, fallback_author: str, fallback_title: str
) -> bool:
    return all(
        (
            bool(fallback_author),
            bool(fallback_title),
            not ctx.cli_overrides["author"],
            not ctx.cli_overrides["title"],
        )
    )


def _repair_swapped_path_identity(
    ctx: _BookPrepContext, fallback_author: str, fallback_title: str
) -> None:
    if not _swapped_path_repair_allowed(ctx, fallback_author, fallback_title):
        return
    if _path_identity_is_swapped(ctx, fallback_author, fallback_title):
        ctx.meta.author = fallback_author
        ctx.meta.title = fallback_title


def _book_identity_validation_needed(ctx: _BookPrepContext) -> bool:
    return bool(
        ctx.meta.unattended
        and not ctx.cli_overrides["title"]
        and not ctx.meta.get("trusted_book_layout", False)
    )


def _validate_unattended_book_identity(ctx: _BookPrepContext) -> None:
    if not _book_identity_validation_needed(ctx):
        return
    source = str(ctx.meta.path or ctx.videopath)
    conflict = book_identity_conflict(ctx.meta, source)
    if conflict:
        raise ItemProcessingError(conflict, source)


async def _apply_audiobook_stats(meta: Meta) -> None:
    if not meta.audiobook:
        return
    total_duration, formatted = await get_audiobook_duration(meta.filelist)
    meta.audiobook_duration = total_duration
    meta.audiobook_duration_formatted = formatted
    average = await get_audiobook_bitrate(meta.filelist)
    if average is not None:
        meta.audiobook_bitrate = average


def _finalize_book_prep_sanitization(meta: Meta) -> None:
    detect_newspaper(meta)
    sanitize_book_language(meta)
    sanitize_book_author(meta)


async def _safe_openlibrary_metadata(
    ctx: _BookPrepContext,
) -> dict[str, Any] | None:
    try:
        return await _fetch_openlibrary_metadata(ctx)
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: OpenLibrary API lookup failed: {error}[/yellow]"
        )
        return None


async def _online_book_metadata(
    ctx: _BookPrepContext,
) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None
]:
    mam_data = await _fetch_mam_metadata(ctx, _mam_id_from_comments(ctx.meta))
    _apply_provider_metadata(ctx, mam_data)
    google_data = await _fetch_google_metadata(
        ctx, _validate_meta_isbn(ctx.meta)
    )
    _apply_provider_metadata(
        ctx,
        google_data,
        prior=(mam_data,),
        artwork_may_override=True,
    )
    open_data = await _safe_openlibrary_metadata(ctx)
    _apply_provider_metadata(ctx, open_data, prior=(mam_data, google_data))
    return mam_data, google_data, open_data


def _final_book_title_source(local_title: str, fallback_title: str) -> str:
    return local_title or fallback_title


def _should_repair_book_title(ctx: _BookPrepContext, source: str) -> bool:
    return bool(source and not ctx.cli_overrides["title"])


def _repair_final_book_title(
    ctx: _BookPrepContext, local_title: str, fallback_title: str
) -> None:
    source = _final_book_title_source(local_title, fallback_title)
    if _should_repair_book_title(ctx, source):
        ctx.meta.title = _prefer_descriptive_source_title(
            str(ctx.meta.title or ""), str(ctx.meta.author or ""), source
        )
    ctx.meta.title = _strip_book_format_suffix(str(ctx.meta.title or ""))


async def gather_book_prep(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Prepare BOOK/Audiobook metadata from local and external sources."""
    ctx = _initialize_book_prep(meta, videopath, base_dir, config)
    _extract_local_book_sources(ctx)
    await _export_book_mediainfo(ctx)
    _extract_embedded_book_metadata(ctx)
    fallback_author, fallback_title, local_title, local_author = (
        _apply_path_identity(ctx)
    )
    _apply_filename_series(ctx)
    await _maybe_fetch_torrent_comments(ctx)
    mam_data, google_data, open_data = await _online_book_metadata(ctx)
    _infer_publisher_from_overview(ctx)
    local_title = _apply_exact_edition(
        ctx, local_title, local_author, mam_data, google_data, open_data
    )
    _repair_final_book_title(ctx, local_title, fallback_title)
    _restore_comic_flags(ctx)
    _repair_swapped_path_identity(ctx, fallback_author, fallback_title)
    _validate_unattended_book_identity(ctx)
    await _apply_audiobook_stats(meta)
    _finalize_book_prep_sanitization(meta)


def _audiobook_files(
    filelist: list[str], *, limit: int | None = None
) -> list[str]:
    files = [
        file
        for file in filelist
        if Path(file).suffix.lower() in AUDIOBOOK_EXTENSIONS
    ]
    return files[:limit] if limit is not None else files


def _first_mi_track(media_info: Any, track_type: str) -> Any | None:
    return next(
        (
            track
            for track in media_info.tracks
            if track.track_type == track_type
        ),
        None,
    )


def _general_duration_seconds(media_info: Any) -> float:
    general = _first_mi_track(media_info, "General")
    duration = general.duration if general is not None else None
    return float(duration) / 1000.0 if duration is not None else 0.0


def _file_duration_seconds(file_path: str) -> float:
    if not Path(file_path).is_file():
        return 0.0
    with contextlib.suppress(Exception):
        return _general_duration_seconds(MediaInfo.parse(file_path))
    return 0.0


def _formatted_audiobook_duration(total_seconds: float) -> str:
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:02d}m {seconds:02d}s"


async def get_audiobook_duration(filelist: list[str]) -> tuple[float, str]:
    """Calculate total duration of audiobook files."""
    audio_files = _audiobook_files(filelist)
    if not audio_files:
        return 0.0, ""
    durations = await asyncio.gather(
        *(
            asyncio.to_thread(_file_duration_seconds, file)
            for file in audio_files
        )
    )
    total_seconds = float(sum(durations))
    return total_seconds, _formatted_audiobook_duration(total_seconds)


def _track_bitrate_value(track: Any, *keys: str) -> int | None:
    data = cast(dict[str, Any], track.to_data())
    raw = next(
        (data.get(key) for key in keys if data.get(key) is not None), None
    )
    if raw is None:
        return None
    match = re.search(r"\d+", str(raw))
    return int(match.group(0)) if match else None


def _media_audio_bitrate(media_info: Any) -> int | None:
    audio = _first_mi_track(media_info, "Audio")
    if audio is not None:
        bitrate = _track_bitrate_value(audio, "bit_rate", "BitRate")
        if bitrate is not None:
            return bitrate
    general = _first_mi_track(media_info, "General")
    if general is None:
        return None
    return _track_bitrate_value(general, "overall_bit_rate", "OverallBitRate")


def _file_audio_bitrate(file_path: str) -> int | None:
    if not Path(file_path).is_file():
        return None
    with contextlib.suppress(Exception):
        return _media_audio_bitrate(MediaInfo.parse(file_path))
    return None


def _average_kbps(values: list[int]) -> int | None:
    if not values:
        return None
    average = sum(values) / len(values)
    return round(average / 1000) if average >= 1000 else round(average)


async def get_audiobook_bitrate(filelist: list[str]) -> int | None:
    """Calculate average bitrate (kbps) from up to five audiobook files."""
    audio_files = _audiobook_files(filelist, limit=5)
    if not audio_files:
        return None
    bitrates = await asyncio.gather(
        *(asyncio.to_thread(_file_audio_bitrate, file) for file in audio_files)
    )
    valid = [bitrate for bitrate in bitrates if bitrate is not None]
    return _average_kbps(cast(list[int], valid))
