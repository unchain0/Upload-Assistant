from __future__ import annotations

from pathlib import Path

from src.domain_models.release import Meta
from src.integrations.media.zentag import fallback_ebook_filename


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "author": "J.K. Rowling",
        "title": "Harry Potter and the Goblet of Fire",
        "year": 2000,
        "book_language_iso": "eng",
        "isbn": "9781781100103",
        "book_series": "Harry Potter",
        "book_series_index": "4",
        "path": "/books/Harry Potter.epub",
    }
    defaults.update(values)
    return Meta(**defaults)


def test_fallback_filename_matches_the_wiki_pattern() -> None:
    assert fallback_ebook_filename(_meta()) == (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire "
        "(2000) [ENG 9781781100103].epub"
    )


def test_fallback_filename_applies_the_character_substitutions() -> None:
    name = fallback_ebook_filename(
        _meta(title="PHP: MySQL | JavaScript? <All-in-One> *Guide*")
    )
    assert name is not None
    assert name.startswith(
        "J.K. Rowling - Harry Potter #4 - PHP- MySQL - JavaScript All-in-One Guide"
    )


def test_fallback_filename_needs_the_identifier() -> None:
    assert fallback_ebook_filename(_meta(isbn="")) is None
    assert fallback_ebook_filename(_meta(year=None)) is None
    assert fallback_ebook_filename(_meta(book_language_iso="")) is None


def test_fallback_copies_the_ebook_without_touching_the_original(
    tmp_path: Path,
) -> None:
    from src.integrations.media.zentag import _fallback_ebook_copy

    source = tmp_path / "original.epub"
    source.write_bytes(b"book")
    meta = _meta(base_dir=str(tmp_path), uuid="abc")
    target = _fallback_ebook_copy(meta, source, str(tmp_path))
    assert target is not None
    release_dir = Path(target)
    assert release_dir.is_dir()
    expected = release_dir / (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire "
        "(2000) [ENG 9781781100103].epub"
    )
    assert expected.read_bytes() == b"book"
    assert source.read_bytes() == b"book"


def test_missing_fields_are_logged(
    caplog,  # type: ignore[no-untyped-def]
) -> None:
    assert fallback_ebook_filename(_meta(isbn="", year=None)) is None
    assert "missing: year, isbn" in caplog.text


def test_ebook_meta_failure_uses_the_fallback(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from src.integrations.media import zentag

    source = tmp_path / "original.epub"
    source.write_bytes(b"book")
    meta = _meta(path=str(source), base_dir=str(tmp_path), uuid="abc")

    async def failing(_meta: object, _source: object, _base: str, _config: object) -> str:
        raise RuntimeError("zentag failed: ebook-meta not usable")

    monkeypatch.setattr(zentag, "_prepare_ebook_copy", failing)
    monkeypatch.setattr(zentag, "_ebook_source", lambda _meta: source)
    monkeypatch.setattr(
        zentag, "should_prepare_zenith_ebook", lambda _meta, _config: True
    )
    target = __import__("asyncio").run(
        zentag.prepare_zenith_ebook(meta, str(tmp_path), {})
    )
    assert target is not None
    release_dir = Path(target)
    assert release_dir.name == (
        "J.K. Rowling - Harry Potter #4 - Harry Potter and the Goblet of Fire "
        "(2000) [ENG 9781781100103]"
    )
    assert (release_dir / f"{release_dir.name}.epub").is_file()


def test_fallback_directories_are_unique(tmp_path: Path) -> None:
    from src.integrations.media.zentag import _fallback_ebook_copy

    source = tmp_path / "original.epub"
    source.write_bytes(b"book")
    meta = _meta(base_dir=str(tmp_path), uuid="abc")
    first = _fallback_ebook_copy(meta, source, str(tmp_path))
    second = _fallback_ebook_copy(meta, source, str(tmp_path))
    assert first is not None and second is not None
    assert first != second
    assert "(1)" not in Path(first).name and "(1)" not in Path(second).name


def test_fallback_filename_moves_the_trailing_article() -> None:
    assert fallback_ebook_filename(
        _meta(title="Origins of the Federal Reserve, The")
    ).startswith("J.K. Rowling - Harry Potter #4 - The Origins of the Federal Reserve")
    assert fallback_ebook_filename(
        _meta(title="Tale, A")
    ).startswith("J.K. Rowling - Harry Potter #4 - A Tale")
    assert fallback_ebook_filename(
        _meta(title="Apple, An")
    ).startswith("J.K. Rowling - Harry Potter #4 - An Apple")
    assert fallback_ebook_filename(_meta(title="Plain Title")).startswith(
        "J.K. Rowling - Harry Potter #4 - Plain Title"
    )
