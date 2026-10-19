import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_image_hosts", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch_inputs(
    monkeypatch: pytest.MonkeyPatch, responses: list[str]
) -> None:
    answers = iter(responses)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))


def test_get_img_host_reuses_existing_host_and_clears_unused_keys(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config_generator_module,
        "image_host_config_map",
        lambda: {"imgbox": None, "imgbb": "imgbb_api"},
    )
    _patch_inputs(monkeypatch, ["invalid-count", ""])
    configured: dict[str, Any] = {}

    config_generator_module.get_img_host(
        configured,
        {"img_host_1": "IMGBOX", "imgbb_api": "old"},
        {"imgbb_api": "default"},
        {},
    )

    assert configured == {"img_host_1": "imgbox", "imgbb_api": ""}


def test_get_img_host_retries_invalid_host_and_prompts_for_api_key(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config_generator_module,
        "image_host_config_map",
        lambda: {"imgbb": "imgbb_api", "imgbox": None},
    )
    _patch_inputs(monkeypatch, ["1", "unknown", "imgbb"])
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_input(prompt: str, **kwargs: Any) -> str:
        calls.append((prompt, kwargs))
        return "new-key"

    monkeypatch.setattr(config_generator_module, "get_user_input", fake_input)
    configured: dict[str, Any] = {}

    config_generator_module.get_img_host(
        configured,
        {"imgbb_api": "old-key"},
        {"imgbb_api": "default-key"},
        {"imgbb_api": ["# ImageBB key"]},
    )

    assert configured == {"img_host_1": "imgbb", "imgbb_api": "new-key"}
    assert calls == [
        (
            "Setting 'imgbb_api' for imgbb",
            {
                "default": "default-key",
                "is_password": True,
                "existing_value": "old-key",
            },
        )
    ]


@pytest.mark.parametrize(("response", "expected"), [("0", 1), ("99", 10)])
def test_image_host_count_clamps_to_supported_range(
    config_generator_module: Any,
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    expected: int,
) -> None:
    _patch_inputs(monkeypatch, [response])
    assert config_generator_module._image_host_count([]) == expected
