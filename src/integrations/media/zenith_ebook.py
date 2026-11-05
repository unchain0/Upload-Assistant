"""Zenith ebook naming rules (https://znth.cx/wikis/10)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

_FILENAME_TRANSLATION = str.maketrans(
    {"/": "-", ":": "-", "?": "", "<": "", ">": "", "|": "-", "*": "", '"': "'"}
)
_UNDEFINED = {"undefined", "none", "null", "nan"}
_YEAR_GROUP = re.compile(r"\(\s*\d{4}(?:\s*[,;-]\s*\d{4})*\s*\)")
_ISBN_VALUE = re.compile(r"\b(?:97[89])?\d{9}[\dXx]\b")
_NUM_EDITION = re.compile(
    r"(?:(?<=\s)|^|_)(\d+)(?:st|nd|rd|th)\s*(?:edition|ed\.?)?(?=\s|$|[),.])",
    re.IGNORECASE,
)
_SHORT_EDITION = re.compile(r"(?:(?<=\s)|^|_)(\d+)[eE]\b")
_WORD_EDITION = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
    r"\s+(?:edition|ed\.?)\b",
    re.IGNORECASE,
)
_NAMED_EDITION = re.compile(
    r"\b(scholar|definitive|annotated|revised)\b", re.IGNORECASE
)
_ORDINALS = {
    "first": "1st",
    "1st": "1st",
    "second": "2nd",
    "2nd": "2nd",
    "third": "3rd",
    "3rd": "3rd",
    "fourth": "4th",
    "4th": "4th",
    "fifth": "5th",
    "5th": "5th",
    "sixth": "6th",
    "6th": "6th",
    "seventh": "7th",
    "7th": "7th",
    "eighth": "8th",
    "8th": "8th",
    "ninth": "9th",
    "9th": "9th",
    "tenth": "10th",
    "10th": "10th",
}
_NAMED_EDITIONS = {"scholar", "definitive", "annotated", "revised"}
_FORBIDDEN = re.compile(r'[/:*?"<>|]')
_EDITION_PHRASE = re.compile(
    r"(?:^|[\s,;-]+)(?:first|second|third|fourth|fifth|sixth|seventh|"
    r"eighth|ninth|tenth|scholar|definitive|annotated|revised|"
    r"\d+(?:st|nd|rd|th))\s+(?:edition|ed\.?)\b",
    re.IGNORECASE,
)
_SHORT_EDITION_PHRASE = re.compile(r"(?:^|[\s_-]+)(\d+)[eE]\b")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tokens(value: Any) -> list[str]:
    folded = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return re.findall(r"[a-z0-9]+", folded)


_EDITION_WORDS = {
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
}


def _ordinal_from_digits(value: str) -> str:
    number = int(value)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _edition_token(match: re.Match[str]) -> str:
    raw = match.group(1).casefold()
    if raw in _NAMED_EDITIONS or raw in _EDITION_WORDS:
        return raw.capitalize()
    if raw.isdigit():
        return _ordinal_from_digits(raw)
    return raw


def _edition_from_text(value: str) -> str:
    for pattern in (_NUM_EDITION, _SHORT_EDITION, _WORD_EDITION, _NAMED_EDITION):
        for match in pattern.finditer(value):
            token = _edition_token(match)
            if token in {"1st", "First"}:
                continue
            return token
    return ""


def _source_texts(meta: Any) -> list[str]:
    names = [str(item) for item in (meta.filelist or [])]
    return [
        _text(getattr(meta, "manual_edition", "")),
        _text(getattr(meta, "edition", "")),
        _text(getattr(meta, "title", "")),
        _text(getattr(meta, "basename_no_ext", "")),
        *names,
    ]


def canonical_author(meta: Any) -> str:
    from src.domain_models.book_naming import canonical_book_author

    return canonical_book_author(meta)


def edition(meta: Any) -> str:
    """Short edition token: 2nd, Second, 5th, Scholar, Definitive."""
    for value in (meta.manual_edition, meta.edition):
        token = _edition_from_text(_text(value))
        if token:
            return token
    for value in _source_texts(meta):
        token = _edition_from_text(value)
        if token:
            return token
    return ""


def _without_edition(value: str, token: str) -> str:
    if not token:
        return value
    word = re.escape(token.casefold())
    value = re.sub(
        rf"\(\s*{word}(?:\s+ed(?:ition)?\.?)?\s*\)",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        rf"\b{word}\s+edition\b", " ", value, flags=re.IGNORECASE
    )
    value = re.sub(rf"\b{word}\s+ed\.?\b", " ", value, flags=re.IGNORECASE)
    return re.sub(
        rf"(?:,\s*|\s+-\s*)?\b{word}\b\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )


def _without_subtitle(value: str) -> str:
    head, separator, _tail = value.partition(":")
    return head.strip() if separator else value


def title(meta: Any) -> str:
    from src.domain_models.book_naming import (
        canonical_book_author,
        clean_book_title,
    )

    author = canonical_book_author(meta)
    value = clean_book_title(meta.title or "", author)
    token = edition(meta)
    value = _without_edition(value, token)
    value = _EDITION_PHRASE.sub(" ", value)
    value = _SHORT_EDITION_PHRASE.sub(" ", value)
    value = re.sub(r"\(\s*\)", "", value)
    value = _YEAR_GROUP.sub(" ", value)
    value = _ISBN_VALUE.sub(" ", value)
    value = _without_subtitle(value)
    value = re.sub(r"\s+", " ", value).strip(" -,.;")
    match = re.match(
        r"^(?P<body>.+),\s*(?P<article>The|A|An)$", value, re.IGNORECASE
    )
    if match:
        return f"{match.group('article').capitalize()} {match.group('body')}"
    return value


def year(meta: Any) -> str:
    value = str(meta.year if meta.year is not None else "").strip()
    return value if re.fullmatch(r"\d{4}", value) else ""


def language(meta: Any) -> str:
    return re.sub(r"[^A-Z]", "", str(meta.book_language_iso or "").upper())[:3]


_WHITESPACE = re.compile(r"\s+")
_TECHNICAL_MARKER = re.compile(r"[A-Za-z][#+]|[#+]{1,2}\d")
_SERIES_MARKER_ONLY = re.compile(
    r"^(?:[A-Za-z][#+]{1,2}\d*|[#+]{1,2}\d+|\d+)$"
)


def _is_real_series(name: str) -> bool:
    """Reject lookup values such as C #9, C#, C++ or a bare number."""
    compact = _WHITESPACE.sub("", unicodedata.normalize("NFKC", name))
    return bool(compact) and not _SERIES_MARKER_ONLY.fullmatch(compact)


def _series_repeats_title(name: str, meta: Any) -> bool:
    """A technical marker such as C#9 is title text, not a series name."""
    series_tokens = _tokens(name)
    title_tokens = _tokens(title(meta))
    if not series_tokens or not title_tokens:
        return False
    if series_tokens == title_tokens:
        return True
    if title_tokens[: len(series_tokens)] != series_tokens:
        return False
    marker = _WHITESPACE.sub("", unicodedata.normalize("NFKC", name))
    if _TECHNICAL_MARKER.search(marker):
        return True
    index_tokens = _tokens(_text(meta.book_series_index))
    series_with_index = series_tokens + index_tokens
    if (
        len(series_tokens) == 1
        and index_tokens
        and len(marker) == 1
        and title_tokens[: len(series_with_index)] == series_with_index
    ):
        return True
    return (
        len(series_tokens) == 2
        and len(series_tokens[0]) == 1
        and series_tokens[1].isdigit()
    )


def series(meta: Any) -> str:
    name = _text(meta.book_series)
    index = _text(meta.book_series_index)
    if not name or not _is_real_series(name) or _series_repeats_title(name, meta):
        return ""
    return f"{name} #{index}" if index else name


def missing_fields(meta: Any) -> list[str]:
    author, book_title, book_year, lang, isbn = _required(meta)
    return [
        name
        for name, value in (
            ("author", author),
            ("title", book_title),
            ("year", book_year),
            ("book_language_iso", lang),
            ("isbn", isbn),
        )
        if not value
    ]


def retail(meta: Any) -> bool:
    declared = f"{meta.manual_source or ''} {meta.source or ''}".casefold()
    if "retail" in declared:
        return True
    names = " ".join(
        [str(meta.basename_no_ext or ""), *[str(item) for item in meta.filelist or []]]
    ).casefold()
    return "retail" in names


def _required(meta: Any) -> tuple[str, str, str, str, str]:
    return (
        canonical_author(meta),
        title(meta),
        year(meta),
        language(meta),
        _text(meta.isbn),
    )


def filename(meta: Any) -> str | None:
    """Compliant file name, or None when a required field is missing."""
    author, book_title, book_year, lang, isbn = _required(meta)
    if not all((author, book_title, book_year, lang, isbn)):
        return None
    if _UNDEFINED & {author.casefold(), book_title.casefold()}:
        return None
    parts = [author]
    series_part = series(meta)
    if series_part:
        parts.extend(["-", series_part])
    parts.extend(["-", book_title, f"({book_year})"])
    bracket = [lang]
    token = edition(meta)
    if token:
        bracket.append(token)
    bracket.append(isbn)
    if retail(meta):
        bracket.append("Retail")
    parts.append(f"[{' '.join(bracket)}]")
    name = " ".join(parts)
    extension = Path(str(meta.path or meta.title or "")).suffix
    return f"{name.translate(_FILENAME_TRANSLATION)}{extension}"


def _contains_sequence(tokens: list[str], needle: list[str]) -> bool:
    if not needle:
        return True
    size = len(needle)
    return any(
        tokens[index : index + size] == needle
        for index in range(len(tokens) - size + 1)
    )


def _sequence_count(tokens: list[str], needle: list[str]) -> int:
    if not needle:
        return 0
    size = len(needle)
    return sum(
        tokens[index : index + size] == needle
        for index in range(len(tokens) - size + 1)
    )


def validate_filename(stem: str, meta: Any) -> str | None:
    """Return a human-readable reason when the prepared name breaks the guide."""
    if not stem:
        return None
    if _FORBIDDEN.search(stem):
        return "forbidden character in the ebook filename"
    if "_" in stem:
        return "underscore in the ebook filename"
    if "undefined" in stem.casefold():
        return "'undefined' in the ebook filename"
    author, _book_title, book_year, lang, isbn = _required(meta)
    if not isbn:
        return "ISBN is required in the ebook filename"
    bracket = re.search(r"\[([^\]]+)\]\s*$", stem)
    if " - " not in stem or not bracket:
        return "filename does not follow 'Author - [Series # - ]Title (Year) [LANG Edition ISBN Retail]'"
    stem_tokens = _tokens(stem)
    author_tokens = _tokens(author)
    if _sequence_count(stem_tokens, author_tokens) != 1:
        return "author is missing or repeated in the ebook filename"
    expected_title = title(meta)
    if not _contains_sequence(stem_tokens, _tokens(expected_title)):
        return f"title '{expected_title}' is missing from the ebook filename"
    if f"({book_year})" not in stem:
        return f"year ({book_year}) is missing from the ebook filename"
    if len(re.findall(r"\(\d{4}\)", stem)) != 1:
        return "the ebook filename must carry exactly one year"
    content = bracket.group(1)
    if lang not in content:
        return f"language {lang} is missing from the ebook filename"
    if isbn not in content:
        return "ISBN is missing from the ebook filename brackets"
    token = edition(meta)
    if token and token not in content:
        return f"edition {token} is missing from the ebook filename"
    if not token and re.search(r"\b(?:second|third|fourth|fifth|sixth|\d+(?:st|nd|rd|th)|scholar|definitive|annotated|revised)\b", content, re.IGNORECASE):
        return "first editions must not carry an edition token"
    return None
