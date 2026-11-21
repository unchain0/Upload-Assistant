from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from data.example_config import config as example_config
from src.screenshot_overlays import (
    OVERLAY_KEYS,
    format_timestamp,
    overlay_enabled,
    overlay_filters,
    overlay_lines,
    overlay_options,
    overlay_text_size,
    overlays_active,
)
from src.services.configuration_validation_service import (
    _validate_default_section,
)


def _validate(values: dict[str, Any]) -> tuple[list[str], list[Any]]:
    defaults = deepcopy(example_config["DEFAULT"])
    defaults["tmdb_api"] = "synthetic-test-key"
    defaults.update(values)
    return _validate_default_section(defaults)


@pytest.mark.parametrize("key", OVERLAY_KEYS)
def test_overlay_switch_types_are_validated(key: str) -> None:
    _, warnings = _validate({key: True})
    assert not any(warning.key == key for warning in warnings)
    _, warnings = _validate({key: "invalid"})
    assert any(warning.key == key for warning in warnings)


@pytest.mark.parametrize("value", [1, "1", 18, "18", 100, "100"])
def test_valid_overlay_text_sizes_are_preserved(value: Any) -> None:
    config = {"overlay_text_size": value}
    errors, warnings = _validate(config)
    assert not errors
    assert not any(warning.key == "overlay_text_size" for warning in warnings)
    assert config["overlay_text_size"] == value
    assert overlay_text_size(config) == int(value)


@pytest.mark.parametrize(
    "value",
    [
        0,
        "0",
        -1,
        101,
        "101",
        "abc",
        "18.5",
        18.5,
        "",
        True,
        None,
        [],
        float("inf"),
    ],
)
def test_invalid_overlay_text_sizes_warn_without_rewriting_config(
    value: Any,
) -> None:
    config = {"overlay_text_size": value}
    errors, warnings = _validate(config)
    assert not errors
    assert any(warning.key == "overlay_text_size" for warning in warnings)
    assert config["overlay_text_size"] is value
    assert 1 <= overlay_text_size(config) <= 100


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("overlay_position", ("left", "right")),
        ("overlay_layout", ("stacked", "single_line")),
    ],
)
def test_manual_overlay_choices_warn_on_unsupported_values(
    key: str, values: tuple[str, str]
) -> None:
    for value in (*values, "invalid", values[0].upper(), "", None, []):
        config = {key: value}
        errors, warnings = _validate(config)
        assert not errors
        assert any(warning.key == key for warning in warnings) == (
            value not in values
        )
        assert config[key] is value


@pytest.mark.parametrize("legacy", [None, False, True, "False", "True"])
def test_legacy_defaults(legacy: Any) -> None:
    defaults = {} if legacy is None else {"frame_overlay": legacy}
    options = overlay_options(defaults)
    expected = legacy in (True, "True")
    assert options == dict(
        zip(OVERLAY_KEYS, [expected, expected, False, expected], strict=True)
    )


def test_explicit_switches_override_legacy_independently() -> None:
    options = overlay_options(
        {
            "frame_overlay": True,
            "overlay_frame_number": False,
            "overlay_timestamp": True,
        }
    )
    assert options == dict(
        zip(OVERLAY_KEYS, [False, True, True, True], strict=True)
    )
    assert not any(
        overlay_options(
            {"frame_overlay": True, **dict.fromkeys(OVERLAY_KEYS, False)}
        ).values()
    )


def test_master_gates_output_without_clearing_label_preferences() -> None:
    config = {"frame_overlay": False, "overlay_timestamp": True}
    assert overlay_options(config)["overlay_timestamp"] is True
    assert not overlay_enabled(config)
    assert not overlays_active(config)
    meta = SimpleNamespace(frame_overlay=True)
    assert overlay_filters(config, meta, 20, True) == []
    config["frame_overlay"] = True
    assert overlays_active(config)
    assert not overlays_active(
        {"frame_overlay": True, **dict.fromkeys(OVERLAY_KEYS, False)}
    )
    assert overlay_enabled({"overlay_timestamp": True})


def test_single_line_format_orders_timestamp_before_picture_type() -> None:
    options = dict.fromkeys(OVERLAY_KEYS, True)
    assert overlay_lines(
        options, 58342, "B", "00:40:33.097", True, layout="single_line"
    ) == ["Frame 58342 • 00:40:33.097 • B-Frame • Tonemapped"]
    options["overlay_frame_number"] = False
    options["overlay_frame_type"] = False
    assert overlay_lines(
        options, 58342, "B", "00:40:33.097", False, layout="single_line"
    ) == ["00:40:33.097"]
    assert (
        overlay_lines({}, 0, "?", "00:00:00.000", True, layout="single_line")
        == []
    )


@pytest.mark.parametrize("layout,count", [("stacked", 4), ("single_line", 1)])
@pytest.mark.parametrize("position", ["left", "right"])
def test_layout_and_alignment_reach_ffmpeg_filters(
    layout: str, count: int, position: str
) -> None:
    config = {
        "frame_overlay": True,
        **dict.fromkeys(OVERLAY_KEYS, True),
        "overlay_position": position,
        "overlay_layout": layout,
    }
    meta = SimpleNamespace(
        frame_overlay=True,
        frame_info_map={},
        frame_rate=24,
        resolution="1080p",
    )
    filters = overlay_filters(config, meta, 20, True)
    assert len(filters) == count
    assert all(
        f":x={'w-tw-10' if position == 'right' else '10'}:" in entry
        for entry in filters
    )
    assert ":y=10:" in filters[0]


def test_conditional_label_is_last_and_disabled_labels_leave_no_gaps() -> None:
    options = overlay_options(
        {"overlay_timestamp": True, "overlay_tonemapped": True}
    )
    assert overlay_lines(options, 24, "P", "00:00:01.000", True) == [
        "Timestamp: 00:00:01.000",
        "Tonemapped",
    ]
    assert overlay_lines(options, 24, "P", "00:00:01.000", False) == [
        "Timestamp: 00:00:01.000"
    ]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00.000"),
        (2538.375, "00:42:18.375"),
        (3599.9999, "01:00:00.000"),
    ],
)
def test_timestamp_rounding(seconds: float, expected: str) -> None:
    assert format_timestamp(seconds) == expected


@pytest.mark.parametrize("dvd", [False, True])
def test_capture_filters_respect_selection_spacing_and_upload_suppression(
    dvd: bool,
) -> None:
    meta = SimpleNamespace(
        frame_overlay=True,
        frame_info_map={},
        frame_rate=24,
        resolution="576p" if dvd else "1080p",
    )
    defaults = {"overlay_timestamp": True, "overlay_tonemapped": True}
    filters = overlay_filters(defaults, meta, 12.345, True, dvd=dvd)
    assert len(filters) == 2
    assert "Timestamp" in filters[0] and "pts\\:hms\\:12.345000" in filters[0]
    assert ":y=10:" in filters[0] and ":y=30:" in filters[1]
    assert "Tonemapped" in filters[1]
    assert len(overlay_filters(defaults, meta, 12.345, False, dvd=dvd)) == 1
    meta.frame_overlay = False
    assert overlay_filters(defaults, meta, 12.345, True, dvd=dvd) == []


def test_all_off_is_clean_even_with_stale_runtime_flag() -> None:
    meta = SimpleNamespace(
        frame_overlay=True,
        frame_info_map={},
        frame_rate=24,
        resolution="1080p",
    )
    assert overlay_filters({}, meta, 0, True) == []
