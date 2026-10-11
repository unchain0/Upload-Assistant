import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_client_config", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_configure_single_client_uses_existing_when_example_missing(
    config_generator_module: Any,
) -> None:
    existing = {"CUSTOM": {"torrent_client": "custom", "host": "old"}}
    configured: dict[str, Any] = {}

    result = config_generator_module.configure_single_client(
        "CUSTOM", existing, {}, configured, {}
    )

    assert result is configured
    assert configured["CUSTOM"] == existing["CUSTOM"]


def test_configure_single_client_preserves_prompt_semantics(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_input(prompt: str, **kwargs: Any) -> str:
        calls.append((prompt, kwargs))
        return f"value-{len(calls)}"

    monkeypatch.setattr(config_generator_module, "get_user_input", fake_input)
    existing = {
        "QBIT": {
            "verify_ssl": False,
            "password": "old-secret",
            "host": "127.0.0.1",
        }
    }
    example = {
        "QBIT": {
            "torrent_client": "qbit",
            "verify_ssl": True,
            "password": None,
            "host": "localhost",
        }
    }

    configured = config_generator_module.configure_single_client(
        "QBIT", existing, example, {}, {}
    )

    assert configured["QBIT"] == {
        "torrent_client": "qbit",
        "verify_ssl": "value-1",
        "password": "value-2",
        "host": "value-3",
    }
    assert calls[0] == (
        "Client setting 'verify_ssl'? (True/False)",
        {"default": "True", "existing_value": "False"},
    )
    assert calls[1] == (
        "Client setting 'password'",
        {
            "default": "",
            "is_password": True,
            "existing_value": "old-secret",
        },
    )
    assert calls[2] == (
        "Client setting 'host'",
        {
            "default": "localhost",
            "is_password": False,
            "existing_value": "127.0.0.1",
        },
    )


def test_client_setting_comments_prefers_client_specific_comments(
    config_generator_module: Any,
) -> None:
    comments = {
        "host": ["# generic"],
        "TORRENT_CLIENTS.QBIT.host": ["# specific"],
    }

    assert config_generator_module._client_setting_comments(
        comments, "QBIT", "host"
    ) == ["# specific"]
    assert config_generator_module._client_setting_comments(
        comments, "RTF", "host"
    ) == ["# generic"]
