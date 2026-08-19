from __future__ import annotations

from src.domain_models.release import Meta
from src.services import book_input_service


def test_sanitize_book_language_empty_valid_and_invalid(monkeypatch) -> None:
    empty = Meta(book_language="")
    book_input_service.sanitize_book_language(empty)
    assert empty.book_language == "" and empty.book_language_iso == ""

    valid = Meta(book_language=" english ")
    book_input_service.sanitize_book_language(valid)
    assert valid.book_language == "English"
    assert valid.book_language_iso == "eng"

    monkeypatch.setattr(book_input_service, "resolve_book_language", lambda _value: ("Unknown", ""))
    invalid = Meta(book_language="invalid")
    book_input_service.sanitize_book_language(invalid)
    assert invalid.book_language == "" and invalid.book_language_iso == ""


def test_detect_newspaper_known_and_unknown_titles() -> None:
    known = Meta(title="O Globo - Edição Nacional")
    book_input_service.detect_newspaper(known)
    assert known.newspaper is True

    unknown = Meta(title="A Novel")
    book_input_service.detect_newspaper(unknown)
    assert unknown.newspaper is False


def test_sanitize_book_author_empty_fallback_manual_and_detected_translator() -> None:
    empty = Meta(author="", book_author="")
    book_input_service.sanitize_book_author(empty)
    assert empty.author == ""

    fallback = Meta(author="", book_author="Jane_Doe")
    book_input_service.sanitize_book_author(fallback)
    assert fallback.author == "Jane_Doe"

    manual = Meta(author="Jane Doe, John Smith", book_translator="John_Smith")
    book_input_service.sanitize_book_author(manual)
    assert manual.author == "Jane Doe"
    assert manual.book_translator == "John_Smith"

    detected = Meta(author="Jane Doe translated by John Smith")
    book_input_service.sanitize_book_author(detected)
    assert detected.author == "Jane Doe"
    assert detected.book_translator == "John Smith"


def test_clean_translator_patterns_underscores_fallbacks_and_deduplication() -> None:
    author, translator = book_input_service.clean_translator_from_author("")
    assert author == "" and translator == ""

    author, translator = book_input_service.clean_translator_from_author("Jane_Doe_John_Smith_tradutor")
    assert author == "Jane_Doe"
    assert translator == "John Smith"

    author, translator = book_input_service.clean_translator_from_author("Jane Doe translated by John Smith")
    assert author == "Jane Doe"
    assert translator == "John Smith"

    author, translator = book_input_service.clean_translator_from_author("123 456 tradutor")
    assert author == ""
    assert translator == "123 456"

    author, translator = book_input_service.clean_translator_from_author("123 tradutor")
    assert author == ""
    assert translator == "123"

    author, translator = book_input_service.clean_translator_from_author("tradutor")
    assert author == ""
    assert translator == ""

    author, translator = book_input_service.clean_translator_from_author("Jane Doe - John Smith translator; John Smith translated by")
    assert author.startswith("Jane Doe")
    assert translator.count("John Smith") <= 1
