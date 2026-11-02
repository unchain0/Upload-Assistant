from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.znth import Zenith


def _tracker() -> Zenith:
    return Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "category": "BOOK",
        "type": "M4B",
        "audiobook": True,
        "narrator": "Narrator",
        "author": "Author",
        "title": "Title",
        "year": 2024,
    }
    defaults.update(values)
    return Meta(**defaults)


@pytest.mark.parametrize(
    "values",
    (
        {"category": "PODCAST"},
        {"source": "LibriVox"},
        {"description": "Public domain recording from LibriVox"},
    ),
)
def test_zenith_blocks_podcast_and_librivox_audiobooks(values: dict[str, object]) -> None:
    assert _tracker()._audiobook_metadata_policy(_meta(**values)) is False


def test_zenith_allows_a_commercial_audiobook() -> None:
    assert _tracker()._audiobook_metadata_policy(_meta(source="Audible")) is True


def test_zenith_multi_audiobook_rejects_m4b_and_bad_names(tmp_path: Path) -> None:
    m4b = [tmp_path / "Part 01. Chapter.m4b", tmp_path / "Part 02. Chapter.m4b"]
    assert "M4B audiobooks must contain one file" in (
        Zenith._multi_audiobook_layout_error(_meta(), m4b)
    )
    mp3 = [tmp_path / "track1.mp3", tmp_path / "track2.mp3"]
    meta = _meta(type="MP3", title="Title", year=2024)
    error = Zenith._multi_audiobook_layout_error(meta, mp3)
    assert "invalid name" in error
