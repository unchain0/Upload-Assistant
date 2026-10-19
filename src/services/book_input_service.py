"""Normalize book-related CLI input without infrastructure dependencies."""

from __future__ import annotations

import base64
import contextlib
import re

from src.domain_models.book_language import (
    extract_first_author,
    is_valid_book_language,
    resolve_book_language,
)
from src.domain_models.release import Meta


def sanitize_book_language(meta: Meta) -> None:
    """Validate and sanitize book_language and book_language_iso in meta. Clear them if invalid."""
    lang = meta.book_language
    if not lang:
        meta.book_language = ""
        meta.book_language_iso = ""
        return

    full, iso = resolve_book_language(lang.strip())
    if is_valid_book_language(full, iso):
        meta.book_language = full
        meta.book_language_iso = iso
    else:
        meta.book_language = ""
        meta.book_language_iso = ""


def detect_newspaper(meta: Meta) -> None:
    np_names = [
        # Brazil
        "Zm9saGEgZGUgcy5wYXVsbw==",
        "Zm9saGEgZGUgcy4gcGF1bG8=",
        "Zm9saGEgZGUgc2FvIHBhdWxv",
        "Zm9saGEgZGUgc8OjbyBwYXVsbw==",
        "ZXN0YWRhbw==",
        "ZXN0YWTDo28=",
        "byBlc3RhZG8gZGUgcy4gcGF1bG8=",
        "byBlc3RhZG8gZGUgcy5wYXVsbw==",
        "byBlc3RhZG8gZGUgc8OjbyBwYXVsbw==",
        "byBnbG9ibw==",
        "dmFsb3IgZWNvbm9taWNv",
        "dmFsb3IgZWNvbsO0bWljbw==",
        "Y29ycmVpbyBicmF6aWxpZW5zZQ==",
        "Y29ycmVpbyBicmFzaWxpZW5zZQ==",
        "emVybyBob3Jh",
        "ZXN0YWRvIGRlIG1pbmFz",
        "ZGlhcmlvIGRvIG5vcmRlc3Rl",
        "ZGnDoXJpbyBkbyBub3JkZXN0ZQ==",
        "Z2F6ZXRhIGRvIHBvdm8=",
        "am9ybmFsIGRvIGJyYXNpbA==",
        "am9ybmFsIGRvIGNvbWVyY2lv",
        "am9ybmFsIGRvIGNvbW1lcmNpbw==",
        "YSB0cmlidW5hIGRhIGltcHJlbnNh",
        "Zm9saGEgZGlyaWdpZGE=",
        "YSB2b3ogZGEgc2VycmE=",
        "dHJpYnVuYSBkZSBwZXRyb3BvbGlz",
        "dHJpYnVuYSBkZSBwZXRyw7Nwb2xpcw==",
        "aW52ZXJ0YSAtIGpvcm5hbCBwcmEgdmVyZGFkZQ==",
        "am9ybmFsIGRlIGJyYXNpbGlh",
        "am9ybmFsIGRlIGJyYXPDrWxpYQ==",
        "YnJhc2lsIGVtIHRlbXBvIHJlYWw=",
        "Y29ycmVpbyBkbyBwb3Zv",
        "am9ybmFsIG5o",
        "am9ybmFsIHZz",
        "ZGlhcmlvIGRlIGNhbm9hcw==",
        "ZGnDoXJpbyBkZSBjYW5vYXM=",
        "am9ybmFsIGRvIHR1cmZl",
        "YnJhc2lsIGRlIGZhdG8=",
        "am9ybmFsIGdhemV0YSBkbyBvZXN0ZQ==",
        "cG9ydGFsIGRvIHRyaWFuZ3Vsbw==",
        "cG9ydGFsIGRvIHRyacOibmd1bG8=",
        "Z2F6ZXRhIG9ubGluZQ==",
        "ZGlhcmlvIGRlIGN1aWFiYQ==",
        "ZGnDoXJpbyBkZSBjdWlhYsOh",
        "YSBjcml0aWNhIGRlIGNhbXBvIGdyYW5kZQ==",
        "YSBjcsOtdGljYSBkZSBjYW1wbyBncmFuZGU=",
        "Y29ycmVpbyBkbyBlc3RhZG8=",
        "ZGlhcmlvIGRlIHBlcm5hbWJ1Y28=",
        "ZGnDoXJpbyBkZSBwZXJuYW1idWNv",
        "Zm9saGEgZGUgcGVybmFtYnVjbw==",
        "am9ybmFsIGltcHJlbnNhIGRvIGFncmVzdGU=",
        "ZGlhcmlvIGRhIGJvcmJvcmVtYQ==",
        "ZGnDoXJpbyBkYSBib3Jib3JlbWE=",
        "am9ybmFsIGRhIHBhcmFpYmE=",
        "am9ybmFsIGRhIHBhcmHDrWJh",
        "dmFsZSBwYXJhaWJhbm8=",
        "Y29ycmVpbyBkYSBwYXJhaWJh",
        "Y29ycmVpbyBkYSBwYXJhw61iYQ==",
        "dHJpYnVuYSBkbyBub3J0ZQ==",
        "Z2F6ZXRhIGRlIG1hY2F1",
        "ZGlhcmlvIGRlIG5hdGFs",
        "ZGnDoXJpbyBkZSBuYXRhbA==",
        "YXJhY2F0aSBvbmxpbmU=",
        "ZGlhcmlvIGRlIHNvcm9jYWJh",
        "ZGnDoXJpbyBkZSBzb3JvY2FiYQ==",
        "ZGlhcmlvIGRvIGdyYW5kZSBhYmM=",
        "ZGnDoXJpbyBkbyBncmFuZGUgYWJj",
        "bm90aWNpYXMgcG9wdWxhcmVz",
        "bm90w61jaWFzIHBvcHVsYXJlcw==",
        "Zm9saGEgdW5pdmVyc2Fs",
        "ZGlhcmlvIG9maWNpYWwgZG8gZXN0YWRvIGRlIHNhbyBwYXVsbw==",
        "ZGnDoXJpbyBvZmljaWFsIGRvIGVzdGFkbyBkZSBzw6NvIHBhdWxv",
        "Z2F6ZXRhIGRlIHByYWlhIGdyYW5kZQ==",
        "YWdvcmEgc2FvIHBhdWxv",
        "YWdvcmEgc8OjbyBwYXVsbw==",
        "am9ybmFsIGRlIHNhbnRhIGNhdGFyaW5h",
        "ZGlhcmlvIGNhdGFyaW5lbnNl",
        "ZGnDoXJpbyBjYXRhcmluZW5zZQ==",
        "dHJpYnVuYSBjYXRhcmluZW5zZQ==",
        "Zm9saGEgZGUgbG9uZHJpbmE=",
        "dHJpYnVuYSBkbyBwYXJhbmE=",
        "dHJpYnVuYSBkbyBwYXJhbsOh",
        "byBlc3RhZG8gZG8gcGFyYW5h",
        "byBlc3RhZG8gZG8gcGFyYW7DoQ==",
        "Z2F6ZXRhIGRvIHBhcmFuYQ==",
        "Z2F6ZXRhIGRvIHBhcmFuw6E=",
        "am9ybmFsIGRlIGxvbmRyaW5h",
        "Z2F6ZXRhIGRvIGlndWFjdQ==",
        "Z2F6ZXRhIGRvIGlndWHDp3U=",
        "Y29ycmVpbyBkYSBiYWhpYQ==",
        "dHJpYnVuYSBkYSBiYWhpYQ==",
        "am9ybmFsIGdyYXBpdW5h",
        "am9ybmFsIGdyYXBpw7puYQ==",
        "Z2F6ZXRhIGRlIHNlcmdpcGU=",
        "Z2F6ZXRhIGRlIGFsYWdvYXM=",
        "am9ybmFsIGRlIGFsYWdvYXM=",
        "dHJpYnVuYSBkZSBhbGFnb2Fz",
        "ZGlhcmlvIGRhIGFtYXpvbmlh",
        "ZGnDoXJpbyBkYSBhbWF6w7RuaWE=",
        "am9ybmFsIG1laW8gbm9ydGU=",
        "byBlc3RhZG8gZG8gbWFyYW5oYW8=",
        "byBlc3RhZG8gZG8gbWFyYW5ow6Nv",
    ]
    title_lower = meta.title.lower()
    for encoded in np_names:
        with contextlib.suppress(Exception):
            decoded = base64.b64decode(encoded).decode("utf-8")
            if decoded in title_lower:
                meta.newspaper = True
                break


def _normalized_underscored_name(value: str) -> tuple[str, bool]:
    has_underscores = "_" in value and " " not in value
    return (
        value.replace("_", " ") if has_underscores else value
    ), has_underscores


def _restore_name_underscores(value: str, had_underscores: bool) -> str:
    return value.replace(" ", "_") if had_underscores else value


def _clean_person_fragment(value: str) -> str:
    value = re.sub(r"\s*[,;/&]+\s*$", "", value)
    value = re.sub(r"^\s*[,;/&]+\s*", "", value)
    value = re.sub(r"\b(?:and|e)\b\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*\b(?:and|e)\b\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*-\s*$", "", value)
    value = re.sub(r"^\s*-\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"\(\s*\)|\[\s*\]", "", value).strip()


def _manual_translator_names(value: str) -> list[str]:
    return [
        name.replace("_", " ").strip()
        for name in value.split(",")
        if name.strip()
    ]


def _remove_manual_translators(author: str, translator: str) -> str:
    for name in _manual_translator_names(translator):
        pattern = r"\b" + re.escape(name) + r"\b"
        author = re.sub(pattern, "", author, flags=re.IGNORECASE)
    return _clean_person_fragment(author)


def _book_author_value(meta: Meta) -> str:
    return meta.author or meta.book_author or ""


def sanitize_book_author(meta: Meta) -> None:
    """Validate and sanitize author in meta by detecting and removing translators."""
    author = _book_author_value(meta)
    if not author:
        meta.author = ""
        return

    normalized_author, had_underscores = _normalized_underscored_name(author)
    manual_translator = meta.book_translator
    if manual_translator:
        normalized_author = _remove_manual_translators(
            normalized_author, manual_translator
        )
    normalized_author = _restore_name_underscores(
        normalized_author, had_underscores
    )

    cleaned_author, translator = clean_translator_from_author(
        normalized_author
    )
    meta.author = extract_first_author(cleaned_author)
    if translator and not meta.book_translator:
        meta.book_translator = translator


def _translator_keyword_pattern() -> str:
    keywords = [
        r"tradutor\w*",
        r"translator\w*",
        r"traduzido\b",
        r"trad\b\.?",
        r"trans\b\.?",
        r"tradu[cç]ao\b",
        r"translated\b",
    ]
    return "(?:" + "|".join(keywords) + ")"


def _translator_patterns(keyword_pattern: str) -> tuple[str, str]:
    pattern1 = (
        r"\b([A-Z][A-Za-zÀ-ÿ]+(?:\s+(?:de|da|do|dos|das|e))\s+[A-Z][A-Za-zÀ-ÿ]+|"
        r"[A-Z][A-Za-zÀ-ÿ]+(?:\s+[A-Z][A-Za-zÀ-ÿ]+)?)"
        r"\s*(?:\(|\[|-|\s_)*" + keyword_pattern + r"\)?\]?(?!\s+[A-ZÀ-ÿ])"
    )
    pattern2 = (
        r"\b(?:translated\s+by|traduzido\s+por|tradutor\w*|translator\w*|tradu[cç]ao)\s*:?\s*"
        r"([A-Z][A-Za-zÀ-ÿ]+(?:\s+[A-Z][A-Za-zÀ-ÿ]+)*)"
    )
    return pattern1, pattern2


def _translator_matches(normalized: str, pattern: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE)
    ]


def _strip_translator_patterns(
    normalized: str, pattern1: str, pattern2: str
) -> tuple[str, int]:
    normalized, count1 = re.subn(pattern1, "", normalized, flags=re.IGNORECASE)
    normalized, count2 = re.subn(pattern2, "", normalized, flags=re.IGNORECASE)
    return normalized, count1 + count2


def _fallback_bare_translator(
    normalized: str, keyword_pattern: str
) -> tuple[str, list[str]]:
    match = re.search(
        r"\b" + keyword_pattern + r"\b", normalized, re.IGNORECASE
    )
    if match is None:
        return normalized, []
    before_keyword = normalized[: match.start()].strip().rstrip(" _-,;([/")
    words = before_keyword.split()
    if len(words) >= 2:
        return " ".join(words[:-2]), [" ".join(words[-2:])]
    if len(words) == 1:
        return "", [words[0]]
    return "", []


def _unique_clean_translators(translators: list[str]) -> list[str]:
    result: list[str] = []
    for translator in translators:
        cleaned = _clean_person_fragment(translator.strip())
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def clean_translator_from_author(author: str) -> tuple[str, str]:
    """Detect translator names, remove them from author text, and return both."""
    if not author:
        return author, ""

    normalized, had_underscores = _normalized_underscored_name(author)
    keyword_pattern = _translator_keyword_pattern()
    pattern1, pattern2 = _translator_patterns(keyword_pattern)
    translators = _translator_matches(normalized, pattern1)
    translators.extend(_translator_matches(normalized, pattern2))
    normalized, match_count = _strip_translator_patterns(
        normalized, pattern1, pattern2
    )
    if match_count == 0:
        normalized, fallback_translators = _fallback_bare_translator(
            normalized, keyword_pattern
        )
        translators.extend(fallback_translators)

    normalized = _clean_person_fragment(normalized)
    normalized = _restore_name_underscores(normalized, had_underscores)
    translator_name = ", ".join(_unique_clean_translators(translators))
    return normalized, translator_name
