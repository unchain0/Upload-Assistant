import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_user_input", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _capture_input(
    monkeypatch: pytest.MonkeyPatch, response: str
) -> list[str]:
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return response

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_get_user_input_masks_existing_password_and_reuses_it_on_enter(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _capture_input(monkeypatch, "")

    value = config_generator_module.get_user_input(
        "Password", is_password=True, existing_value="abcdefghijk"
    )

    assert value == "abcdefghijk"
    assert prompts == ["Password [existing: abcdef*****]: "]


def test_get_user_input_masks_long_announce_url(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _capture_input(monkeypatch, "")
    existing = "123456789012345678901234567890"

    value = config_generator_module.get_user_input(
        "Announce", is_announce_url=True, existing_value=existing
    )

    assert value == existing
    assert prompts == [
        "Announce [existing: 123456789012345...**************...567890]: "
    ]


def test_get_user_input_uses_default_when_no_existing_value(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _capture_input(monkeypatch, "")

    value = config_generator_module.get_user_input("Host", default="localhost")

    assert value == "localhost"
    assert prompts == ["Host [default: localhost]: "]


def test_get_user_input_prefers_typed_value_over_existing_value(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _capture_input(monkeypatch, "new-value")

    value = config_generator_module.get_user_input(
        "Host", default="default", existing_value="old-value"
    )

    assert value == "new-value"
    assert prompts == ["Host [existing: old-value]: "]
