"""Pure language and author normalization rules for book releases."""

from __future__ import annotations

import contextlib
import re

import langcodes


def resolve_book_language(raw: str) -> tuple[str, str]:
    """Return the English language name and ISO-639-3 code for an input."""

    normalized = raw.strip()
    direct = _language_from_code(normalized)
    return direct if direct is not None else _language_from_name(normalized)


def _language_from_code(normalized: str) -> tuple[str, str] | None:
    with contextlib.suppress(Exception):
        language = langcodes.get(normalized.lower())
        result = _language_result(language, normalized)
        if result[0] and result[0].lower() != normalized.lower():
            return result
    return None


def _language_from_name(normalized: str) -> tuple[str, str]:
    try:
        return _language_result(langcodes.find(normalized), normalized)
    except Exception:
        return normalized.title(), ""


def _language_result(language: langcodes.Language, normalized: str) -> tuple[str, str]:
    display_name = language.display_name
    to_alpha3 = language.to_alpha3
    return display_name("en") or normalized.title(), to_alpha3() or ""


def is_valid_book_language(full_name: str, iso_code: str) -> bool:
    """Return whether a resolved language is meaningful for a book release."""

    if not full_name or not iso_code:
        return False
    normalized_name = full_name.strip().lower()
    normalized_iso = iso_code.strip().lower()
    if normalized_name in {"", "unknown", "unknown language", "undetermined", "und", "none", "null"}:
        return False
    return normalized_iso not in {"", "und", "zxx"}


def extract_first_author(author: str) -> str:
    """Extract the first author from a potentially multi-author value."""

    if not author:
        return ""
    uses_underscores = _underscore_delimited_author(author)
    normalized = author.replace("_", " ") if uses_underscores else author
    first_author = _first_author_part(normalized)
    return first_author.replace(" ", "_") if uses_underscores else first_author


def _underscore_delimited_author(author: str) -> bool:
    return "_" in author and " " not in author


def _first_author_part(author: str) -> str:
    split_pattern = r"\s*(?:,|;|&|/|\+|\band\b|\be\b|\by\b|\bwith\b|\s+-\s+)\s*"
    parts = re.split(split_pattern, author, flags=re.IGNORECASE)
    return parts[0].strip() if parts else ""
