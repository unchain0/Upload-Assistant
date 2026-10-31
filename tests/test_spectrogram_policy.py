from __future__ import annotations

import asyncio

import pytest

from src.domain_models.release import Meta
from src.integrations.media.audio_spectrogram import supports_audio_spectrogram
from src.integrations.trackers.description_builder import DescriptionBuilder
from upload import _spectrogram_artifact_needed


def _meta(**values: object) -> Meta:
    return Meta(**values)


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (_meta(category="MUSIC"), True),
        (_meta(category="music"), True),
        (_meta(category="BOOK", audiobook=True), True),
        (_meta(category="BOOK"), False),
        (_meta(category="MOVIE"), False),
        (_meta(category="TV"), False),
    ],
)
def test_spectrogram_is_limited_to_music_and_audiobooks(
    meta: Meta, expected: bool
) -> None:
    assert supports_audio_spectrogram(meta) is expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (_meta(category="MUSIC"), True),
        (_meta(category="BOOK", audiobook=True), True),
        (_meta(category="MOVIE"), False),
        (_meta(category="TV"), False),
        (_meta(category="BOOK"), False),
    ],
)
def test_spectrogram_artifact_requires_an_allowed_category(
    meta: Meta, expected: bool
) -> None:
    assert _spectrogram_artifact_needed(
        meta, {"add_audio_spectrogram": True}
    ) is (expected)


def test_spectrogram_artifact_still_honours_the_request_flag() -> None:
    meta = _meta(category="MUSIC")
    assert _spectrogram_artifact_needed(
        meta, {"add_audio_spectrogram": False}
    ) is (False)


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (_meta(category="MUSIC"), True),
        (_meta(category="BOOK", audiobook=True), True),
        (_meta(category="MOVIE"), False),
        (_meta(category="TV"), False),
        (_meta(category="BOOK"), False),
    ],
)
def test_description_spectrogram_section_skips_other_categories(
    meta: Meta, expected: bool
) -> None:
    config = {
        "TRACKERS": {"GENERIC": {}},
        "DEFAULT": {"add_audio_spectrogram": True},
    }
    builder = DescriptionBuilder("GENERIC", config)
    assert builder._spectrogram_enabled(meta) is expected
    if not expected:
        assert asyncio.run(builder.get_audio_spectrogram_section(meta)) == ""


def test_audiobook_requests_a_spectrogram_without_the_flag() -> None:
    meta = _meta(category="BOOK", audiobook=True)
    assert (
        _spectrogram_artifact_needed(meta, {"add_audio_spectrogram": False})
        is True
    )
    assert supports_audio_spectrogram(meta) is True


def test_audiobook_renders_the_spectrogram_without_the_flag() -> None:
    meta = _meta(category="BOOK", audiobook=True)
    config = {
        "TRACKERS": {"GENERIC": {}},
        "DEFAULT": {"add_audio_spectrogram": False},
    }
    builder = DescriptionBuilder("GENERIC", config)
    assert builder._spectrogram_enabled(meta) is True
