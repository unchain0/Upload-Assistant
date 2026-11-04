"""Clean a BOOK release name shared by the tracker name builders."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_UNDEFINED = {"undefined", "none", "null", "nan"}

_LIBRARY_NOTE = re.compile(
    r"\s*\([^)]*(?:library|biblioteca)[^)]*\)", re.IGNORECASE
)
_EDITION_SUFFIX = re.compile(
    r"(?:(?<=\s)|^)(\d+)(?:e|st|nd|rd|th)\b\.?(?:\s*ed(?:ition)?\.?)?",
    re.IGNORECASE,
)
_DUPLICATE_YEAR = re.compile(r"\((\d{4})\)\s+\1\b")
_SPACED_HASH = re.compile(r"\b([A-Za-z])\s+#(?=\d)")
_SEPARATOR = re.compile(r"\s+[-\u2013\u2014]\s+")
_WHITESPACE = re.compile(r"\s+")


def _folded(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_value.casefold()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _folded(value))


def _safe_text(value: Any) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    return "" if _folded(text).strip(" ._-") in _UNDEFINED else text


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def canonical_book_author(meta: Any) -> str:
    """Return the full author list, never a surname fragment."""
    for value in (meta.book_author, meta.author):
        text = _safe_text(value)
        if text:
            text = _WHITESPACE.sub(" ", text.replace("_", " ")).strip()
            if any(character.isalpha() for character in text) and text.isupper():
                text = text.title()
            return text
    return ""


def _author_variants(author: str) -> list[list[str]]:
    variants: list[list[str]] = []
    whole = _tokens(author)
    if whole:
        variants.append(whole)
    if len(whole) >= 2:
        variants.append(whole[-1:] + whole[:-1])
    parts = re.split(r"\s*[,;&]\s*|\s+(?:and|e)\s+", author, flags=re.IGNORECASE)
    if len(parts) > 1:
        variants.extend(tokens for part in parts if (tokens := _tokens(part)))
        if "," in author:
            surname, _, given = author.partition(",")
            inverted = _tokens(f"{given} {surname}")
            if inverted:
                variants.append(inverted)
    return variants


def _is_author_segment(segment: str, variants: list[list[str]]) -> bool:
    tokens = _tokens(segment)
    if not tokens:
        return False
    for variant in variants:
        if tokens == variant:
            return True
        if len(tokens) <= len(variant) and tokens == variant[-len(tokens) :]:
            return True
        if len(tokens) <= len(variant) and tokens == variant[: len(tokens)]:
            return True
    return False


def _strip_leading_author_words(title: str, variants: list[list[str]]) -> str:
    words = title.split()
    folded = [_tokens(word) for word in words]
    changed = True
    while changed:
        changed = False
        for variant in variants:
            size = len(variant)
            if size == 0 or len(folded) < size:
                continue
            if [token for tokens in folded[:size] for token in tokens] == variant:
                words = words[size:]
                folded = folded[size:]
                changed = True
                break
    return " ".join(words).lstrip(" -\u2013\u2014:")


def _strip_author_segments(title: str, author: str) -> str:
    variants = _author_variants(author)
    if not variants:
        return title
    title = _strip_leading_author_words(title, variants)
    parts = [part.strip() for part in _SEPARATOR.split(title) if part.strip()]
    kept = [part for part in parts if not _is_author_segment(part, variants)]
    if not kept:
        return title
    deduped: list[str] = []
    for part in kept:
        if deduped and _folded(deduped[-1]) == _folded(part):
            continue
        if deduped:
            previous = _tokens(deduped[-1])
            current = _tokens(part)
            if previous and current[: len(previous)] == previous:
                deduped[-1] = part
                continue
        deduped.append(part)
    return " - ".join(deduped)


def _normalize_edition(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f"{_ordinal(int(match.group(1)))} Edition"

    return _EDITION_SUFFIX.sub(replace, value)


def clean_book_title(title: Any, author: str = "") -> str:
    text = _safe_text(title)
    if not text:
        return ""
    text = text.replace("_", " ")
    text = _LIBRARY_NOTE.sub("", text)
    text = _SPACED_HASH.sub(r"\1#", text)
    text = _WHITESPACE.sub(" ", text).strip(" -")
    text = _strip_author_segments(text, author)
    text = _normalize_edition(text)
    return _WHITESPACE.sub(" ", text).strip(" -")


def clean_book_name(name: Any) -> str:
    text = _safe_text(name)
    if not text:
        return ""
    text = text.replace("_", " ")
    text = _LIBRARY_NOTE.sub("", text)
    text = _SPACED_HASH.sub(r"\1#", text)
    text = _DUPLICATE_YEAR.sub(r"(\1)", text)
    return _WHITESPACE.sub(" ", text).strip(" -")
