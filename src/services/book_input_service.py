"""Normalize book-related CLI input without infrastructure dependencies."""

from __future__ import annotations

import base64
import contextlib
import re

from src.domain_models.book_language import extract_first_author, is_valid_book_language, resolve_book_language
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


def sanitize_book_author(meta: Meta) -> None:
    """Validate and sanitize author in meta by detecting and removing translators."""
    author = meta.author
    if not author:
        # Check if book_author is present and copy it if needed
        book_author = meta.book_author
        if book_author:
            author = book_author
        else:
            meta.author = ""
            return

    author = author
    has_underscores = "_" in author and " " not in author
    normalized_author = author.replace("_", " ") if has_underscores else author

    manual_translator = meta.book_translator
    if manual_translator:
        # Also replace underscores in manual translator names in case they entered underscores
        manual_translator_str = manual_translator
        names_to_remove = [n.replace("_", " ").strip() for n in manual_translator_str.split(",") if n.strip()]
        for name in names_to_remove:
            pattern = r"\b" + re.escape(name) + r"\b"
            normalized_author = re.sub(pattern, "", normalized_author, flags=re.IGNORECASE)

        # Clean up delimiters and extra whitespace left behind
        normalized_author = re.sub(r"\s*[,;/&]+\s*$", "", normalized_author)
        normalized_author = re.sub(r"^\s*[,;/&]+\s*", "", normalized_author)
        normalized_author = re.sub(r"\b(?:and|e)\b\s*$", "", normalized_author, flags=re.IGNORECASE)
        normalized_author = re.sub(r"^\s*\b(?:and|e)\b\s*", "", normalized_author, flags=re.IGNORECASE)
        normalized_author = re.sub(r"\s*-\s*$", "", normalized_author)
        normalized_author = re.sub(r"^\s*-\s*", "", normalized_author)
        normalized_author = re.sub(r"\s+", " ", normalized_author).strip()
        normalized_author = re.sub(r"\(\s*\)|\[\s*\]", "", normalized_author).strip()

    if has_underscores:
        normalized_author = normalized_author.replace(" ", "_")

    cleaned_author, translator = clean_translator_from_author(normalized_author)
    meta.author = extract_first_author(cleaned_author)
    if translator and not meta.book_translator:
        meta.book_translator = translator


def clean_translator_from_author(author: str) -> tuple[str, str]:
    """Detect if a name is a translator, remove it from the author field and return both."""
    if not author:
        return author, ""

    # If it contains underscores and no spaces, e.g. "Rosa_Montero_Mariana_Sanchez_tradutor"
    # we normalize underscores to spaces for processing.
    has_underscores = "_" in author and " " not in author
    normalized = author.replace("_", " ") if has_underscores else author

    # Translator keywords (case-insensitive)
    keywords = [
        r"tradutor\w*",  # tradutor, tradutora, tradutores, tradutoras
        r"translator\w*",  # translator, translators
        r"traduzido\b",  # traduzido, traduzida
        r"trad\b\.?",  # trad, trad.
        r"trans\b\.?",  # trans, trans.
        r"tradu[cç]ao\b",  # tradução, traducao
        r"translated\b",  # translated
    ]
    pattern_keywords = "(?:" + "|".join(keywords) + ")"

    # Pattern 1: [Name] followed by translator keyword (e.g. "Mariana Sanchez (tradutor)" or "Mariana Sanchez - tradutor")
    # Limit to matching at most 2 capitalized words to prevent greedily matching the author name if no delimiter is present.
    pattern1 = (
        r"\b([A-Z][A-Za-zÀ-ÿ]+(?:\s+(?:de|da|do|dos|das|e))\s+[A-Z][A-Za-zÀ-ÿ]+|"  # 2 words with particle
        r"[A-Z][A-Za-zÀ-ÿ]+(?:\s+[A-Z][A-Za-zÀ-ÿ]+)?)"  # 1 or 2 capitalized words
        r"\s*(?:\(|\[|-|\s_)*" + pattern_keywords + r"\)?\]?(?!\s+[A-ZÀ-ÿ])"
    )

    # Pattern 2: Translator keyword followed by [Name] (e.g. "translated by John Doe" or "traduzido por John Doe")
    pattern2 = (
        r"\b(?:translated\s+by|traduzido\s+por|tradutor\w*|translator\w*|tradu[cç]ao)\s*:?\s*"
        r"([A-Z][A-Za-zÀ-ÿ]+(?:\s+[A-Z][A-Za-zÀ-ÿ]+)*)"
    )

    translators = [match.group(1).strip() for match in re.finditer(pattern1, normalized, flags=re.IGNORECASE)]

    # Find all matches for pattern2 to extract translator name(s)
    translators.extend(match.group(1).strip() for match in re.finditer(pattern2, normalized, flags=re.IGNORECASE))

    # Apply pattern 1
    normalized, count1 = re.subn(pattern1, "", normalized, flags=re.IGNORECASE)

    # Apply pattern 2
    normalized, count2 = re.subn(pattern2, "", normalized, flags=re.IGNORECASE)

    # If neither pattern matched but a bare keyword is present, fallback to word-based stripping
    if count1 == 0 and count2 == 0:
        match = re.search(r"\b" + pattern_keywords + r"\b", normalized, re.IGNORECASE)
        if match:
            before_keyword = normalized[: match.start()].strip()
            before_keyword = before_keyword.rstrip(" _-,;([/")
            words = before_keyword.split()
            if len(words) >= 2:
                translators.append(" ".join(words[-2:]))
                normalized = " ".join(words[:-2])
            elif len(words) == 1:
                translators.append(words[0])
                normalized = ""
            else:
                normalized = ""

    # Clean up delimiters and extra whitespace left behind (anchored to start/end of the string)
    normalized = re.sub(r"\s*[,;/&]+\s*$", "", normalized)
    normalized = re.sub(r"^\s*[,;/&]+\s*", "", normalized)
    normalized = re.sub(r"\b(?:and|e)\b\s*$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^\s*\b(?:and|e)\b\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*-\s*$", "", normalized)
    normalized = re.sub(r"^\s*-\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Remove any empty brackets/parentheses left behind
    normalized = re.sub(r"\(\s*\)|\[\s*\]", "", normalized).strip()

    if has_underscores:
        normalized = normalized.replace(" ", "_")

    # Format the translator list as a comma-separated string
    unique_translators = []
    for t in translators:
        t_clean = t.strip()
        t_clean = re.sub(r"\s*[,;/&]+\s*$", "", t_clean)
        t_clean = re.sub(r"^\s*[,;/&]+\s*", "", t_clean)
        t_clean = re.sub(r"\b(?:and|e)\b\s*$", "", t_clean, flags=re.IGNORECASE)
        t_clean = re.sub(r"^\s*\b(?:and|e)\b\s*", "", t_clean, flags=re.IGNORECASE)
        t_clean = re.sub(r"\s*-\s*$", "", t_clean)
        t_clean = re.sub(r"^\s*-\s*", "", t_clean)
        t_clean = re.sub(r"\s+", " ", t_clean).strip()
        t_clean = re.sub(r"\(\s*\)|\[\s*\]", "", t_clean).strip()

        if t_clean and t_clean not in unique_translators:
            unique_translators.append(t_clean)

    translator_name = ", ".join(unique_translators)

    return normalized, translator_name
