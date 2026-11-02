from __future__ import annotations

from src.audio_spectrogram import spectrogram_requested
from src.meta import Meta


def test_audiobook_forces_the_spectrogram_image() -> None:
    assert spectrogram_requested(Meta(audiobook=True), {}) is True


def test_video_without_flags_does_not_request_a_spectrogram() -> None:
    assert spectrogram_requested(Meta(category="MOVIE"), {}) is False


def test_config_flag_still_requests_the_spectrogram() -> None:
    assert (
        spectrogram_requested(
            Meta(category="MOVIE"), {"DEFAULT": {"add_audio_spectrogram": True}}
        )
        is True
    )
