from __future__ import annotations

from typing import Any, ClassVar

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent_clients import deluge as deluge_module
from src.integrations.torrent_clients import (
    transmission as transmission_module,
)
from src.integrations.torrent_clients.deluge import DelugeClientMixin
from src.integrations.torrent_clients.path_utils import coerce_str_list
from src.integrations.torrent_clients.transmission import (
    TransmissionClientMixin,
)


def test_coerce_str_list_invalid_literal_falls_back_to_raw_string() -> None:
    assert coerce_str_list("[not valid]") == ["[not valid]"]
    assert coerce_str_list("['unterminated]") == ["['unterminated]"]
    assert coerce_str_list(7) == ["7"]
    assert coerce_str_list(None) == []

    from src.integrations.torrent_clients.path_utils import is_path_under

    assert not is_path_under("/short", "/short/longer")


class _Torrent:
    def dump(self) -> bytes:
        return b"torrent"


class _DelugeClient:
    connected_value: ClassVar[bool] = True
    instances: ClassVar[list[_DelugeClient]] = []

    def __init__(
        self, host: str, port: int, username: str, password: str
    ) -> None:
        self.args = (host, port, username, password)
        self.connected = self.connected_value
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.__class__.instances.append(self)

    def connect(self) -> None:
        return None

    def call(self, method: str, *args: object) -> None:
        self.calls.append((method, args))


def test_deluge_connected_and_disconnected_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deluge_module, "DelugeRPCClient", _DelugeClient)
    config: dict[str, Any] = {
        "deluge_url": "host",
        "deluge_port": "58846",
        "deluge_user": "user",
        "deluge_pass": "pass",
    }
    torrent = _Torrent()

    _DelugeClient.connected_value = True
    _DelugeClient.instances = []
    DelugeClientMixin().deluge(
        "/local/release/file.mkv",
        "release.torrent",
        torrent,
        "/local",
        "/remote",
        config,
    )  # type: ignore[arg-type]
    client = _DelugeClient.instances[-1]
    assert client.args == ("host", 58846, "user", "pass")
    method, args = client.calls[0]
    assert method == "core.add_torrent_file"
    assert args[0] == "release.torrent"
    assert args[2] == {
        "download_location": "/remote/release",
        "seed_mode": True,
    }

    _DelugeClient.connected_value = False
    _DelugeClient.instances = []
    DelugeClientMixin().deluge(
        "/local/release/file.mkv",
        "release.torrent",
        torrent,
        "/local",
        "/remote",
        config,
    )  # type: ignore[arg-type]
    assert _DelugeClient.instances[-1].calls == []


class _TransmissionClient:
    instances: ClassVar[list[_TransmissionClient]] = []
    fail = False

    def __init__(self, **kwargs: object) -> None:
        if self.fail:
            raise RuntimeError("connection failed")
        self.kwargs = kwargs
        self.calls: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def add_torrent(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _transmission_config() -> dict[str, Any]:
    return {
        "transmission_protocol": "https",
        "transmission_host": "host",
        "transmission_port": "9091",
        "transmission_username": "user",
        "transmission_password": "pass",
        "transmission_path": "/rpc",
    }


def test_transmission_connection_failure_and_label_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        transmission_module.transmission_rpc, "Client", _TransmissionClient
    )
    torrent = _Torrent()
    mixin = TransmissionClientMixin()

    _TransmissionClient.fail = True
    mixin.transmission(
        "/local/release/file.mkv",
        torrent,
        "/local",
        "/remote",
        _transmission_config(),
        Meta(),
    )  # type: ignore[arg-type]
    _TransmissionClient.fail = False

    config = _transmission_config()
    config["transmission_label"] = "client-label"
    _TransmissionClient.instances = []
    mixin.transmission(
        "/local/release/file.mkv",
        torrent,
        "/local",
        "/remote",
        config,
        Meta(transmission_label="meta-label"),
    )  # type: ignore[arg-type]
    client = _TransmissionClient.instances[-1]
    assert client.kwargs["path"] == "/rpc"
    assert client.calls[-1]["download_dir"] == "/remote/release"
    assert client.calls[-1]["labels"] == ["meta-label"]

    mixin.transmission(
        "/local/release/file.mkv",
        torrent,
        "/local",
        "/remote",
        config,
        Meta(transmission_label=None),
    )  # type: ignore[arg-type]
    assert _TransmissionClient.instances[-1].calls[-1]["labels"] == [
        "client-label"
    ]

    config.pop("transmission_label")
    config.pop("transmission_path")
    mixin.transmission(
        "/local/release/file.mkv",
        torrent,
        "/local",
        "/remote",
        config,
        Meta(transmission_label=None),
    )  # type: ignore[arg-type]
    client = _TransmissionClient.instances[-1]
    assert client.kwargs["path"] == "/transmission/rpc"
    assert client.calls[-1]["labels"] is None
