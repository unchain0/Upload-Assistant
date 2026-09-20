import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_default_section", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_configure_default_section_preserves_dynamic_maps_and_linked_skips(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_input(prompt: str, **kwargs: Any) -> str:
        calls.append((prompt, kwargs))
        if "tone_map" in prompt:
            return "False"
        if "tmdb_api" in prompt:
            return "secret"
        return "None"

    monkeypatch.setattr(config_generator_module, "get_user_input", fake_input)
    existing = {"tag_overrides": {"AITHER": "INT"}}
    example = {
        "default_torrent_client": "qbit",
        "tag_overrides": {"DEFAULT": "GROUP"},
        "tone_map": True,
        "algorithm": "hable",
        "tmdb_api": "",
        "optional_value": None,
    }

    configured = config_generator_module.configure_default_section(
        existing, example, {"tmdb_api": ["# TMDB key"]}
    )

    assert configured == {
        "tag_overrides": {"AITHER": "INT"},
        "tone_map": "False",
        "algorithm": "hable",
        "tmdb_api": "secret",
        "optional_value": None,
    }
    assert [prompt for prompt, _kwargs in calls] == [
        "Setting 'tone_map'? (True/False)",
        "Setting 'tmdb_api'",
        "Setting 'optional_value'",
    ]
    assert calls[1][1]["is_password"] is True


def test_configure_default_section_quick_setup_prompts_only_essential_setting(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    image_host_calls: list[dict[str, Any]] = []

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    def fake_input(prompt: str, **_kwargs: Any) -> str:
        prompts.append(prompt)
        return "tmdb-secret"

    def fake_img_host(
        config_defaults: dict[str, Any],
        _existing_defaults: dict[str, Any],
        _example_defaults: dict[str, Any],
        _config_comments: dict[str, list[str]],
    ) -> None:
        image_host_calls.append(config_defaults)
        config_defaults["img_host_1"] = "imgbox"

    monkeypatch.setattr(config_generator_module, "get_user_input", fake_input)
    monkeypatch.setattr(config_generator_module, "get_img_host", fake_img_host)
    example = {
        "tmdb_api": "",
        "tone_map": True,
        "algorithm": "hable",
        "tag_overrides": {"DEFAULT": "GROUP"},
    }

    configured = config_generator_module.configure_default_section(
        {}, example, {}, quick_setup=True
    )

    assert configured == {
        "tmdb_api": "tmdb-secret",
        "tone_map": True,
        "algorithm": "hable",
        "tag_overrides": {"DEFAULT": "GROUP"},
        "img_host_1": "imgbox",
    }
    assert prompts == ["Setting 'tmdb_api'"]
    assert image_host_calls == [configured]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("api_key", True),
        ("tracker_password", True),
        ("custom_url", True),
        ("regular_setting", False),
    ],
)
def test_sensitive_default_key_detection(
    config_generator_module: Any, key: str, expected: bool
) -> None:
    assert config_generator_module._is_sensitive_default_key(key) is expected
