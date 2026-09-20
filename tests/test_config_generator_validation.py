import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_validation", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validate_config_reviews_nested_unexpected_keys(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"DEFAULT": {"known": 1, "remove_me": "old", "keep_me": "custom"}}
    example = {"DEFAULT": {"known": 0}}
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = config_generator_module.validate_config(config, example)

    assert result is config
    assert config == {"DEFAULT": {"known": 1, "keep_me": "custom"}}


def test_validate_config_reviews_unexpected_root_section(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"DEFAULT": {}, "OLD_SECTION": {"value": 1}}
    example = {"DEFAULT": {}}
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    config_generator_module.validate_config(config, example)

    assert config == {"DEFAULT": {}}


def test_find_missing_keys_reports_nested_and_root_paths(
    config_generator_module: Any,
) -> None:
    existing = {"DEFAULT": {"nested": {"present": 1}}}
    example = {
        "DEFAULT": {
            "nested": {"present": 0, "missing": 2},
            "top_missing": 3,
        },
        "TRACKERS": {},
    }

    assert config_generator_module.find_missing_keys(existing, example) == [
        "DEFAULT.nested.missing",
        "DEFAULT.top_missing",
        "TRACKERS",
    ]
