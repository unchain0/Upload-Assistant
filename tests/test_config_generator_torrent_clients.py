import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_torrent_clients", path
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


def test_configure_torrent_clients_keeps_existing_default(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["y", "n"])
    configured_calls: list[str] = []

    def fake_configure(
        client_name: str,
        _existing: dict[str, Any],
        _example: dict[str, Any],
        configured: dict[str, Any],
        _comments: dict[str, list[str]],
    ) -> dict[str, Any]:
        configured_calls.append(client_name)
        configured[client_name] = {"configured": True}
        return configured

    monkeypatch.setattr(
        config_generator_module, "configure_single_client", fake_configure
    )

    configured, default_client = (
        config_generator_module.configure_torrent_clients(
            {"qbittorrent": {"host": "old"}},
            {"qbittorrent": {"host": "new"}, "deluge": {}},
            "qbittorrent",
            {},
        )
    )

    assert default_client == "qbittorrent"
    assert configured == {"qbittorrent": {"configured": True}}
    assert configured_calls == ["qbittorrent"]


def test_configure_torrent_clients_selects_default_when_missing(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["n"])
    user_inputs = iter(["qbittorrent"])
    monkeypatch.setattr(
        config_generator_module,
        "get_user_input",
        lambda *_args, **_kwargs: next(user_inputs),
    )
    monkeypatch.setattr(
        config_generator_module,
        "configure_single_client",
        lambda client, _existing, _example, configured, _comments: (
            configured.setdefault(client, {"configured": True}) or configured
        ),
    )

    configured, default_client = (
        config_generator_module.configure_torrent_clients(
            {}, {"qbittorrent": {}}, None, {}
        )
    )

    assert default_client == "qbittorrent"
    assert configured == {"qbittorrent": {"configured": True}}


def test_configure_torrent_clients_replaces_default_and_validates_additional(
    config_generator_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_input_sequence(monkeypatch, ["n", "y", "y", "y", "y", "n"])
    user_inputs = iter(["deluge", "", "deluge", "missing", "rtorrent"])
    monkeypatch.setattr(
        config_generator_module,
        "get_user_input",
        lambda *_args, **_kwargs: next(user_inputs),
    )
    configured_calls: list[str] = []

    def fake_configure(
        client_name: str,
        _existing: dict[str, Any],
        _example: dict[str, Any],
        configured: dict[str, Any],
        _comments: dict[str, list[str]],
    ) -> dict[str, Any]:
        configured_calls.append(client_name)
        configured[client_name] = {"configured": True}
        return configured

    monkeypatch.setattr(
        config_generator_module, "configure_single_client", fake_configure
    )
    examples = {"qbittorrent": {}, "deluge": {}, "rtorrent": {}}

    configured, default_client = (
        config_generator_module.configure_torrent_clients(
            {"qbittorrent": {"host": "old"}},
            examples,
            "qbittorrent",
            {},
        )
    )

    assert default_client == "deluge"
    assert configured == {
        "deluge": {"configured": True},
        "rtorrent": {"configured": True},
    }
    assert configured_calls == ["deluge", "rtorrent"]
