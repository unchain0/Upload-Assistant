from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.media import zentag


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "trackers": ["ZENITH"],
        "unattended": True,
    }
    defaults.update(values)
    return Meta(**defaults)


@pytest.mark.parametrize(
    ("values", "config", "expected"),
    (
        ({"site_check": True}, {"DEFAULT": {}}, "site_check is enabled"),
        ({"unattended": False}, {"DEFAULT": {}}, "the run is not unattended"),
        (
            {"trackers": ["OTHER"]},
            {"DEFAULT": {}},
            "ZENITH is not among the selected trackers",
        ),
        (
            {},
            {"DEFAULT": {"auto_zentag": False}},
            "auto_zentag is disabled in DEFAULT",
        ),
        ({"category": "MOVIE"}, {"DEFAULT": {}}, "category is 'MOVIE', not BOOK"),
        ({"audiobook": True}, {"DEFAULT": {}}, "the book is an audiobook"),
        ({"manga": True}, {"DEFAULT": {}}, "comic, manga"),
    ),
)
def test_skip_reason_is_logged(
    values: dict[str, object], config: dict, expected: str, caplog
) -> None:  # type: ignore[no-untyped-def]
    assert zentag.should_prepare_zenith_ebook(_meta(**values), config) is False
    assert expected in caplog.text


def test_missing_file_path_is_named(caplog) -> None:  # type: ignore[no-untyped-def]
    meta = _meta(path="/does/not/exist", filelist=["/does/not/exist"])
    assert zentag.should_prepare_zenith_ebook(meta, {"DEFAULT": {}}) is False
    assert "no ebook file could be resolved" in caplog.text
    assert "/does/not/exist" in caplog.text


def test_ready_ebook_has_no_skip_reason(tmp_path: Path, caplog) -> None:
    book = tmp_path / "book.epub"
    book.write_bytes(b"x")
    meta = _meta(path=str(book), filelist=[str(book)])
    assert zentag.should_prepare_zenith_ebook(meta, {"DEFAULT": {}}) is True
    assert "not preparing" not in caplog.text
