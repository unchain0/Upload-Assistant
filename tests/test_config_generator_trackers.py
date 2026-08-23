import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_trackers", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch_input_sequence(
    monkeypatch: pytest.MonkeyPatch, responses: list[str]
) -> None:
    answers = iter(responses)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))


def test_configure_trackers_updates_selected_and_copies_skipped_tracker(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["n", "AITHER"])
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_user_input(prompt: str, **kwargs: Any) -> str:
        calls.append((prompt, kwargs))
        if prompt.startswith("\nEnter tracker acronyms"):
            return "AITHER, PTP"
        if "freeleech" in prompt:
            return "True"
        if "announce_url" in prompt:
            return "https://new.example/announce"
        return "new-api"

    monkeypatch.setattr(
        config_generator_module, "get_user_input", fake_user_input
    )
    existing_ptp = {"api_key": "ptp-existing"}
    existing = {
        "default_trackers": "AITHER, PTP",
        "AITHER": {
            "api_key": "old-api",
            "tag_overrides": {"AITHER": "INT"},
        },
        "PTP": existing_ptp,
    }
    example = {
        "default_trackers": "",
        "AITHER": {
            "api_key": "default-api",
            "freeleech": False,
            "announce_url": "https://default.example/announce",
            "tag_overrides": {"DEFAULT": "GROUP"},
        },
        "PTP": {"api_key": "ptp-default"},
    }

    configured = config_generator_module.configure_trackers(
        existing, example, {}
    )

    assert configured["default_trackers"] == "AITHER, PTP"
    assert configured["PTP"] is existing_ptp
    assert configured["AITHER"] == {
        "api_key": "new-api",
        "freeleech": "True",
        "announce_url": "https://new.example/announce",
        "tag_overrides": {"AITHER": "INT"},
    }
    api_call = next(
        call for call in calls if call[0] == "Tracker setting 'api_key'"
    )
    assert api_call[1]["is_password"] is True
    assert api_call[1]["existing_value"] == "old-api"
    announce_call = next(
        call for call in calls if call[0] == "Tracker setting 'announce_url'"
    )
    assert announce_call[1]["is_announce_url"] is True


def test_configure_trackers_adds_hd_torrents_without_prompting_announce_url(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["y"])
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_user_input(prompt: str, **kwargs: Any) -> str:
        calls.append((prompt, kwargs))
        if prompt.startswith("\nEnter tracker acronyms"):
            return "AITHER"
        if prompt.startswith("\nEnter any additional tracker acronyms"):
            return "HDTORRENTS"
        return "hd-api"

    monkeypatch.setattr(
        config_generator_module, "get_user_input", fake_user_input
    )
    example = {
        "default_trackers": "",
        "AITHER": {},
        "HDTORRENTS": {
            "announce_url": "https://hardcoded.example/announce",
            "api_key": "default-key",
        },
    }

    configured = config_generator_module.configure_trackers({}, example, {})

    assert configured["default_trackers"] == "AITHER"
    assert configured["AITHER"] == {}
    assert configured["HDTORRENTS"] == {
        "announce_url": "https://hardcoded.example/announce",
        "api_key": "hd-api",
    }
    assert not any(
        call[0] == "Tracker setting 'announce_url'" for call in calls
    )
    api_call = next(
        call for call in calls if call[0] == "Tracker setting 'api_key'"
    )
    assert api_call[1]["is_password"] is True
    assert "existing_value" not in api_call[1]


def test_configure_trackers_keeps_unknown_additional_tracker_as_empty_config(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["y"])

    def fake_user_input(prompt: str, **_kwargs: Any) -> str:
        if prompt.startswith("\nEnter tracker acronyms"):
            return "AITHER"
        if prompt.startswith("\nEnter any additional tracker acronyms"):
            return "UNKNOWN"
        return ""

    monkeypatch.setattr(
        config_generator_module, "get_user_input", fake_user_input
    )
    example = {
        "default_trackers": "",
        "AITHER": {},
        "PTP": {"api_key": "default"},
    }

    configured = config_generator_module.configure_trackers({}, example, {})

    assert configured == {
        "default_trackers": "AITHER",
        "AITHER": {},
        "UNKNOWN": {},
    }
