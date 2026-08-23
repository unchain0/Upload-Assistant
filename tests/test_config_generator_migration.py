import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


class _CaptureConsole:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def print(
        self, message: object = "", *_args: object, **_kwargs: object
    ) -> None:
        self.messages.append(message)


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_migration", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_migrate_old_config_maps_defaults_and_tracker_sections(
    config_generator_module: Any,
) -> None:
    config = {
        "DEFAULT": {"default_trackers": " AR,UNKNOWN, AITHER "},
        "TRACKERS": {
            "ANT": {"api_key": "ant"},
            "CUSTOM": {"api_key": "custom"},
        },
    }

    result = config_generator_module.migrate_old_config(config)

    assert result is config
    assert config["DEFAULT"]["default_trackers"] == "ALPHARATIO,UNKNOWN,AITHER"
    assert config["TRACKERS"] == {
        "ANTHELION": {"api_key": "ant"},
        "CUSTOM": {"api_key": "custom"},
    }


def test_migrate_old_config_ignores_non_mapping_sections(
    config_generator_module: Any,
) -> None:
    config = {"DEFAULT": "invalid", "TRACKERS": []}

    assert config_generator_module.migrate_old_config(config) == config


def test_migrate_old_config_preserves_identity_mapping_notification(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = _CaptureConsole()
    monkeypatch.setattr(config_generator_module, "console", capture)
    config = {"DEFAULT": {"default_trackers": "AITHER"}}

    config_generator_module.migrate_old_config(config)

    assert config["DEFAULT"]["default_trackers"] == "AITHER"
    assert any(
        "Migrated old tracker names" in str(message)
        for message in capture.messages
    )
