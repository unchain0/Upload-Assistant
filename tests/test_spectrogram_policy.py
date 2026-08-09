# ruff: noqa: S101

import pytest

from src.meta import Meta
from src.spectrogram_policy import should_process_audio_spectrogram


@pytest.mark.parametrize(
    ("meta_values", "config"),
    [
        ({"audio_spectrogram": True}, {"DEFAULT": {"add_audio_spectrogram": False}}),
        ({"audio_spectrogram_tracks": "all"}, {"DEFAULT": {"add_audio_spectrogram": False}}),
        ({}, {"DEFAULT": {"add_audio_spectrogram": True}}),
    ],
)
def test_darkpeers_never_processes_non_music_spectrograms(meta_values, config) -> None:
    meta = Meta(category="TV", trackers=["DARKPEERS"], tracker_status={"DARKPEERS": {"upload": True}}, **meta_values)

    assert should_process_audio_spectrogram(meta, config) is False


def test_darkpeers_processes_music_spectrograms() -> None:
    meta = Meta(category="MUSIC", audio_spectrogram=True, trackers=["DARKPEERS"], tracker_status={"DARKPEERS": {"upload": True}})

    assert should_process_audio_spectrogram(meta, {"DEFAULT": {}}) is True


def test_other_eligible_tracker_can_still_request_spectrogram() -> None:
    meta = Meta(
        category="TV",
        audio_spectrogram=True,
        trackers=["DARKPEERS", "ZENITH"],
        tracker_status={"DARKPEERS": {"upload": True}, "ZENITH": {"upload": True}},
    )

    assert should_process_audio_spectrogram(meta, {"DEFAULT": {}}) is True


def test_ineligible_other_tracker_does_not_override_darkpeers_policy() -> None:
    meta = Meta(
        category="MOVIE",
        audio_spectrogram=True,
        trackers=["DARKPEERS", "ZENITH"],
        tracker_status={"DARKPEERS": {"upload": True}, "ZENITH": {"upload": False}},
    )

    assert should_process_audio_spectrogram(meta, {"DEFAULT": {}}) is False


def test_darkpeers_does_not_process_audiobook_spectrograms() -> None:
    meta = Meta(category="BOOK", audiobook=True, audio_spectrogram=True, trackers=["DARKPEERS"], tracker_status={"DARKPEERS": {"upload": True}})

    assert should_process_audio_spectrogram(meta, {"DEFAULT": {}}) is False
