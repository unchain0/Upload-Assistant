"""Pure language and author normalization rules for book releases."""

from __future__ import annotations

import contextlib
import re

import langcodes


def resolve_book_language(raw: str) -> tuple[str, str]:
    """Return the English language name and ISO-639-3 code for an input."""

    normalized = raw.strip()
    with contextlib.suppress(Exception):
        language = langcodes.get(normalized.lower())
        full_name = language.display_name("en") or normalized.title()
        alpha3 = language.to_alpha3() or ""
        if full_name and full_name.lower() != normalized.lower():
            return full_name, alpha3
    try:
        language = langcodes.find(normalized)
        return language.display_name("en") or normalized.title(), language.to_alpha3() or ""
    except Exception:
        return normalized.title(), ""


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
    uses_underscores = "_" in author and " " not in author
    normalized = author.replace("_", " ") if uses_underscores else author
    split_pattern = r"\s*(?:,|;|&|/|\+|\band\b|\be\b|\by\b|\bwith\b|\s+-\s+)\s*"
    parts = re.split(split_pattern, normalized, flags=re.IGNORECASE)
    first_author = parts[0].strip() if parts else ""
    return first_author.replace(" ", "_") if uses_underscores else first_author
