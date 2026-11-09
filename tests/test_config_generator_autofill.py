import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_autofill", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_autofill_trackers_adds_new_templates_and_fills_existing_settings(
    config_generator_module: Any,
) -> None:
    example_new = {"api_key": "new", "tag_overrides": {"DEFAULT": "GROUP"}}
    config = {
        "TRACKERS": {
            "default_trackers": "AITHER",
            "AITHER": {"api_key": "kept"},
            "MANUAL": {},
        }
    }
    example = {
        "TRACKERS": {
            "default_trackers": "IGNORED",
            "AITHER": {"api_key": "default", "announce_url": "announce"},
            "MANUAL": {"manual_key": "manual"},
            "NEW": example_new,
        }
    }

    config_generator_module.autofill_missing_keys(config, example)

    trackers = config["TRACKERS"]
    assert trackers["default_trackers"] == "AITHER"
    assert trackers["AITHER"] == {
        "api_key": "kept",
        "announce_url": "announce",
    }
    assert trackers["MANUAL"] == {"manual_key": "manual"}
    assert trackers["NEW"] == example_new
    assert trackers["NEW"] is not example_new
    assert trackers["NEW"]["tag_overrides"] is not example_new["tag_overrides"]


def test_autofill_trackers_does_not_create_manual_tracker(
    config_generator_module: Any,
) -> None:
    config = {"TRACKERS": {"default_trackers": ""}}
    example = {"TRACKERS": {"MANUAL": {"api_key": "manual"}}}

    config_generator_module.autofill_missing_keys(config, example)

    assert config == {"TRACKERS": {"default_trackers": ""}}


def test_autofill_torrent_clients_matches_exact_name_then_client_type(
    config_generator_module: Any,
) -> None:
    config = {
        "TORRENT_CLIENTS": {
            "primary": {"torrent_client": "qbit", "host": "custom"},
            "deluge": {"torrent_client": "deluge"},
            "exact_non_dict": {"torrent_client": "qbit"},
            "unknown": {"torrent_client": "unknown"},
        }
    }
    example = {
        "TORRENT_CLIENTS": {
            "qbittorrent": {
                "torrent_client": "qbit",
                "host": "template",
                "port": 8080,
            },
            "deluge": {"torrent_client": "deluge", "port": 8112},
            "exact_non_dict": "invalid-template",
            "new_client": {"torrent_client": "new"},
        }
    }

    config_generator_module.autofill_missing_keys(config, example)

    clients = config["TORRENT_CLIENTS"]
    assert clients["primary"] == {
        "torrent_client": "qbit",
        "host": "custom",
        "port": 8080,
    }
    assert clients["deluge"] == {"torrent_client": "deluge", "port": 8112}
    assert clients["exact_non_dict"] == {"torrent_client": "qbit"}
    assert clients["unknown"] == {"torrent_client": "unknown"}
    assert "new_client" not in clients


def test_autofill_static_sections_adds_top_level_keys_and_uses_shallow_copy(
    config_generator_module: Any,
) -> None:
    nested = {"size": 100}
    images_template = {"nested": nested}
    config = {"DEFAULT": {"existing": "kept"}}
    example = {
        "DEFAULT": {"existing": "default", "missing": "added"},
        "IMAGES": images_template,
        "VERSION": 1,
    }

    config_generator_module.autofill_missing_keys(config, example)

    assert config["DEFAULT"] == {"existing": "kept", "missing": "added"}
    assert config["IMAGES"] == images_template
    assert config["IMAGES"] is not images_template
    assert config["IMAGES"]["nested"] is nested
    assert "VERSION" not in config
