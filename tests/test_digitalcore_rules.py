import asyncio
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.digitalcore import DigitalCore


def _tracker() -> DigitalCore:
    return DigitalCore({"TRACKERS": {"DIGITALCORE": {}}})


def _tracker_with_metadata() -> DigitalCore:
    return DigitalCore(
        {"TRACKERS": {"DIGITALCORE": {"use_metadata_name": True}}}
    )


def _make_meta(**overrides: Any) -> Meta:
    values: dict[str, Any] = {
        "category": "MOVIE",
        "source": "WEBDL",
        "type": "WEBDL",
        "name": "Example Movie 2024 1080p WEB-DL H.264",
        "clean_name": "Example Movie 2024 1080p WEB-DL H.264",
        "scene_name": "",
        "basename_no_ext": "Example.Movie.2024.1080p.WEB-DL.x264",
        "image_list": [],
    }
    values.update(overrides)
    return Meta(**values)


def test_digitalcore_rejects_cam_or_ts_source_uploads() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(_make_meta(source="CAM"))
    )
    assert not asyncio.run(
        _tracker().get_additional_checks(_make_meta(source="TS"))
    )
    assert not asyncio.run(
        _tracker().get_additional_checks(_make_meta(type="CAM"))
    )


def test_digitalcore_rejects_cam_or_ts_tokens_in_name_or_tag() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(
            _make_meta(name="Example Movie 2024 CAM")
        )
    )
    assert not asyncio.run(
        _tracker().get_additional_checks(
            _make_meta(name="Example.Movie.CAM.2024", tag="CAM")
        )
    )


def test_digitalcore_rejects_webp_screenshots() -> None:
    assert not asyncio.run(
        _tracker().get_additional_checks(
            _make_meta(
                image_list=[
                    {"raw_url": "https://example.com/shot-1.webp"},
                    {"raw_url": "https://example.com/shot-2.jpg"},
                    {"raw_url": "https://example.com/shot-3.png"},
                ]
            )
        )
    )


def test_digitalcore_allows_jpg_png_and_gif_screenshots() -> None:
    assert asyncio.run(
        _tracker().get_additional_checks(
            _make_meta(
                image_list=[
                    {"raw_url": "https://example.com/shot-1.jpg"},
                    {"raw_url": "https://example.com/shot-2.png"},
                    {"raw_url": "https://example.com/shot-3.gif"},
                ]
            )
        )
    )


def test_digitalcore_names_scene_release_with_unrar_when_metadata_names_enabled() -> (
    None
):
    assert (
        asyncio.run(
            _tracker_with_metadata().get_name(
                _make_meta(
                    clean_name="Example Movie 2024 1080p WEB-DL x264",
                    scene_name="Example.Movie.2024.1080p.WEB-DL.x264-SCENE",
                )
            )
        )
        == "Example.Movie.2024.1080p.WEB-DL.x264-SCENE [UNRAR]"
    )


def test_digitalcore_sanitizes_default_book_names_for_upload() -> None:
    cases = (
        (
            "Only with a Bargepole: A Novel 1974 SCAN eBOOK",
            "Only with a Bargepole A Novel 1974 SCAN eBOOK",
        ),
        (
            "Ellin Carsta - Die unbeugsame Händlerstochter 2020 GERMAN AUDIOBOOK",
            "Ellin Carsta - Die unbeugsame Handlerstochter 2020 GERMAN AUDIOBOOK",
        ),
        (
            "Doris Litz - Blutzeit - Das Böse wartet auf dich! 2021 GERMAN AUDIOBOOK",
            "Doris Litz - Blutzeit - Das Bose wartet auf dich 2021 GERMAN AUDIOBOOK",
        ),
    )
    for raw_name, expected in cases:
        meta = _make_meta(
            category="BOOK",
            scene_name="",
            basename_no_ext=raw_name,
        )
        assert asyncio.run(_tracker().get_name(meta)) == expected


def test_digitalcore_keeps_scene_name_and_unrar_when_not_using_metadata() -> (
    None
):
    assert (
        asyncio.run(
            _tracker().get_name(
                _make_meta(
                    scene_name="Example.Movie.2024.1080p.WEB-DL.x264-SCENE",
                    basename_no_ext="Fallback Name",
                )
            )
        )
        == "Example.Movie.2024.1080p.WEB-DL.x264-SCENE [UNRAR]"
    )
