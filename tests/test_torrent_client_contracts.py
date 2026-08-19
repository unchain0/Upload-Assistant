"""Boundary contracts for torrent-client integrations.

The matrix drives qBittorrent, rTorrent, Deluge, Transmission, and path-mapping
code with local torrent fixtures and deterministic client doubles. Focused tests
assert exact reuse and proxy behavior; this catalog protects the remaining
adapter paths from hidden network access and process termination.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import ssl
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

import bencodepy
import httpx
import qbittorrentapi
import transmission_rpc

import src.integrations.torrent_clients as torrent_clients_package
from data.example_config import config as example_config
from src.domain_models.release import Meta
from tests.contract_scenarios import literal_branch_scenarios


class _TorrentFile:
    name = "Example.Release.2026.1080p.WEB-DL.H.264-GROUP.mkv"
    size = 1024
    progress = 1.0
    priority = 1


class _TorrentInfo(dict[str, Any]):
    def __init__(self, root: Path, profile: int = 0) -> None:
        hash_value = "a" * 40
        values = {
            "hash": hash_value,
            "name": "Example.Release.2026.1080p.WEB-DL.H.264-GROUP",
            "content_path": str(root),
            "save_path": str(root.parent),
            "size": 1024,
            "total_size": 1024,
            "amount_left": 0 if profile == 0 else 1024,
            "progress": 1.0 if profile == 0 else 0.0,
            "state": "uploading" if profile == 0 else "pausedDL",
            "tracker": "https://tracker.example/announce",
            "comment": "https://passthepopcorn.me/torrents.php?id=1&torrentid=2",
            "category": "Upload Assistant",
            "tags": "ua",
            "piece_size": 1048576,
            "completion_on": 1,
            "added_on": 1,
            "files": [_TorrentFile()],
        }
        super().__init__(values)
        self.__dict__.update(values)


class _QbitClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.root = Path.cwd()
        self.profile = 0
        self.auth = self
        self.torrents = self
        self.app = self
        self.transfer = self

    def auth_log_in(self) -> None:
        return None

    def auth_log_out(self) -> None:
        return None

    def torrents_info(self, *_args: object, **_kwargs: object) -> list[_TorrentInfo]:
        return [_TorrentInfo(self.root, self.profile)]

    def torrents_files(self, *_args: object, **_kwargs: object) -> list[_TorrentFile]:
        return [_TorrentFile()]

    def torrents_trackers(self, *_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        return [{"url": "https://tracker.example/announce", "status": 2}]

    def torrents_properties(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"comment": "https://passthepopcorn.me/torrents.php?id=1&torrentid=2", "save_path": str(self.root)}

    def torrents_export(self, *_args: object, **_kwargs: object) -> bytes:
        return _torrent_bytes()

    def torrents_add(self, *_args: object, **_kwargs: object) -> str:
        return "Ok."

    def app_version(self) -> str:
        return "5.0.0"

    def transfer_info(self) -> dict[str, int]:
        return {"dl_info_speed": 0, "up_info_speed": 0}

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name.startswith(("torrents_", "transfer_", "app_", "auth_")):
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)


class _Response:
    status_code = 200
    text = "Ok."
    content = b""
    headers: ClassVar[dict[str, str]] = {}

    def json(self) -> list[dict[str, Any]] | dict[str, Any]:
        return [dict(_TorrentInfo(Path.cwd()))]

    def raise_for_status(self) -> None:
        return None


class _AsyncClient:
    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _TransmissionClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def add_torrent(self, *_args: object, **_kwargs: object) -> dict[str, int]:
        return {"id": 1}

    def get_torrents(self, *_args: object, **_kwargs: object) -> list[_TorrentInfo]:
        return [_TorrentInfo(Path.cwd())]

    def close(self) -> None:
        return None


class _DelugeClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.connected = True

    def connect(self) -> bool:
        return True

    def call(self, method: str, *_args: object, **_kwargs: object) -> object:
        if "get_torrents_status" in method:
            return {b"a" * 40: {b"name": b"Example Release", b"save_path": str(Path.cwd()).encode()}}
        return b"a" * 40

    def disconnect(self) -> None:
        return None


class _XmlRpc:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.load = self
        self.d = self

    def __getattr__(self, _name: str) -> Callable[..., object]:
        return lambda *_args, **_kwargs: "a" * 40


def _torrent_bytes() -> bytes:
    return bencodepy.encode(
        {
            b"announce": b"https://tracker.example/announce",
            b"comment": b"https://passthepopcorn.me/torrents.php?id=1&torrentid=2",
            b"info": {
                b"name": b"Example.Release.2026.1080p.WEB-DL.H.264-GROUP",
                b"piece length": 262144,
                b"length": 5,
                b"pieces": b"0" * 20,
            },
        }
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    media = tmp_path / "Example.Release.2026.1080p.WEB-DL.H.264-GROUP.mkv"
    media.write_bytes(b"media")
    torrent = tmp_path / "BASE.torrent"
    torrent.write_bytes(_torrent_bytes())
    return media, torrent


def _config(tmp_path: Path) -> dict[str, Any]:
    config = {key: value.copy() if isinstance(value, dict) else value for key, value in example_config.items()}
    default = config.setdefault("DEFAULT", {})
    default.update(
        {
            "torrent_client": "qbit",
            "default_torrent_client": "qbit",
            "torrent_storage_dir": str(tmp_path),
            "client_sleep": 0,
        }
    )
    clients = config.setdefault("TORRENT_CLIENTS", {})
    for name in ("qbit", "qbit_proxy", "rtorrent", "deluge", "transmission"):
        clients[name] = {
            "torrent_client": "qbit" if name.startswith("qbit") else name,
            "qbit_url": "https://qbit.example",
            "qbit_port": 443,
            "qbit_user": "user",
            "qbit_pass": "pass",
            "qbit_category": "Upload Assistant",
            "qbit_tag": "ua",
            "qbit_local_path": str(tmp_path),
            "qbit_remote_path": str(tmp_path),
            "local_path": str(tmp_path),
            "remote_path": str(tmp_path),
            "rtorrent_url": "https://rtorrent.example/RPC2",
            "deluge_url": "deluge.example",
            "deluge_port": 58846,
            "deluge_user": "user",
            "deluge_pass": "pass",
            "transmission_url": "transmission.example",
            "transmission_port": 9091,
            "transmission_user": "user",
            "transmission_pass": "pass",
        }
    return config


def _meta(tmp_path: Path, media: Path, torrent: Path, profile: int) -> Meta:
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"torrent-client-{profile}",
        path=str(media),
        filename=media.name,
        filelist=[str(media)],
        isdir=False,
        category="MOVIE" if profile % 2 == 0 else "TV",
        type="WEBDL",
        resolution="1080p",
        title="Example Release",
        name="Example Release 2026 1080p WEB-DL H.264-GROUP",
        clean_name="Example.Release.2026.1080p.WEB-DL.H.264-GROUP",
        year=2026,
        tag="-GROUP",
        group="GROUP",
        trackers=["PASSTHEPOPCORN"],
        tracker="PASSTHEPOPCORN",
        torrent_path=str(torrent),
        client="qbit",
        unattended=True,
        debug=False,
        no_seed=profile == 2,
        keep_folder=profile == 1,
        is_disc="BDMV" if profile == 3 else "",
        bdinfo={"size": 1.0},
        mediainfo={"media": {"track": []}},
    )


def _modules() -> list[ModuleType]:
    return [importlib.import_module(info.name) for info in pkgutil.iter_modules(torrent_clients_package.__path__, f"{torrent_clients_package.__name__}.")]


def _value(name: str, annotation: object, meta: Meta, config: dict[str, Any], tmp_path: Path, media: Path, torrent: Path, profile: int) -> object:
    normalized = name.casefold().lstrip("_")
    client = _QbitClient()
    client.root = tmp_path
    client.profile = profile
    info = _TorrentInfo(tmp_path, profile)
    values: dict[str, object] = {
        "config": config,
        "meta": meta,
        "client": client,
        "qbt_client": client,
        "qbit_client": client,
        "torrent": info,
        "torrent_info": info,
        "torrent_path": str(torrent),
        "torrent_file": str(torrent),
        "torrent_bytes": _torrent_bytes(),
        "path": str(media),
        "content_path": str(media),
        "save_path": str(tmp_path),
        "local_path": str(tmp_path),
        "remote_path": str(tmp_path),
        "normalized_path": tmp_path / "normalized.torrent",
        "hash": "a" * 40,
        "torrent_hash": "a" * 40,
        "info_hash": "a" * 40,
        "comment": "https://passthepopcorn.me/torrents.php?id=1&torrentid=2",
        "url": "https://qbit.example/api/v2/torrents/info",
        "host": "passthepopcorn.me",
        "tracker_hosts": {"PASSTHEPOPCORN": ("passthepopcorn.me",)},
        "client_config": config["TORRENT_CLIENTS"]["qbit"],
        "torrents": [info],
        "torrent_list": [info],
        "candidates": [info],
        "files": [_TorrentFile()],
        "filelist": list(meta.filelist),
        "patterns": ["passthepopcorn.me"],
        "tracker_patterns": {"PASSTHEPOPCORN": ["passthepopcorn.me"]},
        "tracker": "PASSTHEPOPCORN",
        "client_name": "qbit",
        "directory": tmp_path,
        "root": tmp_path,
        "index": 0,
        "retry_count": profile,
        "attempts": 2,
        "delay": 0.0,
        "ssl_context": ssl.create_default_context(),
        "response": _Response(),
        "data": {},
        "payload": {},
    }
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return None
    if annotation is bool:
        return profile % 2 == 0
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "example"
    if annotation is Path:
        return media
    if origin in {list, Sequence}:
        return [info]
    if origin in {dict, Mapping}:
        return {}
    if origin is tuple:
        return ()
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, meta, config, tmp_path, media, torrent, profile)
    return None


_PROTECTED_ARGUMENTS = frozenset(
    {
        "meta",
        "config",
        "client",
        "qbt_client",
        "qbit_client",
        "torrent",
        "torrent_info",
        "response",
        "ssl_context",
    }
)


def _coerce_override(
    value: object,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Meta:
        return meta
    if annotation is Path:
        return value if isinstance(value, Path) else media
    if annotation is str:
        return str(value)
    if annotation is bool:
        return bool(value)
    if annotation is int:
        try:
            return int(value)
        except TypeError, ValueError:
            return 1
    if annotation is float:
        try:
            return float(value)
        except TypeError, ValueError:
            return 1.0
    if origin in {list, Sequence}:
        if isinstance(value, list):
            return value
        element = args[0] if args else object
        return [_coerce_override(value, element, meta, config, tmp_path, media, torrent, profile)]
    if origin in {dict, Mapping}:
        return value if isinstance(value, dict) else {}
    if origin is tuple:
        return value if isinstance(value, tuple) else ()
    if origin is set:
        return value if isinstance(value, set) else {value}
    if origin is not None and type(None) in args and value is not None:
        concrete = next((item for item in args if item is not type(None)), object)
        return _coerce_override(value, concrete, meta, config, tmp_path, media, torrent, profile)
    return value


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    positional: list[object] = []
    keywords: dict[str, object] = {}
    overrides = overrides or {}
    hint_target = function.__init__ if inspect.isclass(function) else function
    try:
        hints = get_type_hints(hint_target)
    except NameError, TypeError:
        hints = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.default is not inspect.Parameter.empty and parameter.name not in overrides:
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = _value(parameter.name, annotation, meta, config, tmp_path, media, torrent, profile)
        if parameter.name in overrides and parameter.name not in _PROTECTED_ARGUMENTS:
            value = _coerce_override(overrides[parameter.name], annotation, meta, config, tmp_path, media, torrent, profile)
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


def test_torrent_client_catalog_uses_local_fakes(tmp_path: Path, monkeypatch: Any) -> None:
    media, torrent = _fixture(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(qbittorrentapi, "Client", _QbitClient)
    monkeypatch.setattr(transmission_rpc, "Client", _TransmissionClient)
    monkeypatch.setattr("deluge_client.DelugeRPCClient", _DelugeClient)
    monkeypatch.setattr("xmlrpc.client.ServerProxy", _XmlRpc)
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    async def no_sleep(_delay: float = 0, *_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []

    async def exercise() -> None:
        for module in _modules():
            functions = [
                (name, function) for name, function in inspect.getmembers(module, inspect.isfunction) if function.__module__ == module.__name__ and not name.startswith("__")
            ]
            for name, function in functions:
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                for profile in range(4):
                    try:
                        await _invoke(function, _meta(tmp_path, media, torrent, profile), config, tmp_path, media, torrent, profile)
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(f"{qualified}:{type(error).__name__}")
                    except Exception as error:
                        expected_rejections.append(f"{qualified}:{type(error).__name__}")
                for meta_updates, argument_overrides in literal_branch_scenarios(function, Meta.__dataclass_fields__, limit=256):
                    argument_overrides = {key: value for key, value in argument_overrides.items() if key not in _PROTECTED_ARGUMENTS}
                    scenario_meta = _meta(tmp_path, media, torrent, 0)
                    for key, value in meta_updates.items():
                        if key in Meta.__dataclass_fields__:
                            setattr(scenario_meta, key, value)
                    try:
                        await _invoke(function, scenario_meta, config, tmp_path, media, torrent, 0, argument_overrides)
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(f"{qualified}:{type(error).__name__}")
                    except Exception as error:
                        expected_rejections.append(f"{qualified}:{type(error).__name__}")

            classes = [
                (class_name, class_type)
                for class_name, class_type in inspect.getmembers(module, inspect.isclass)
                if class_type.__module__ == module.__name__ and not class_name.startswith("_")
            ]
            for class_name, class_type in classes:
                try:
                    instance = await _invoke(class_type, _meta(tmp_path, media, torrent, 0), config, tmp_path, media, torrent, 0)
                except Exception as error:
                    expected_rejections.append(f"{module.__name__}.{class_name}.__init__:{type(error).__name__}")
                    continue
                if hasattr(instance, "config"):
                    instance.config = config
                for method_name, method in inspect.getmembers(instance, callable):
                    if method_name.startswith("__"):
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    attempted.add(qualified)
                    for profile in range(4):
                        try:
                            await _invoke(method, _meta(tmp_path, media, torrent, profile), config, tmp_path, media, torrent, profile)
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(f"{qualified}:{type(error).__name__}")
                        except Exception as error:
                            expected_rejections.append(f"{qualified}:{type(error).__name__}")
                    for meta_updates, argument_overrides in literal_branch_scenarios(method, Meta.__dataclass_fields__, limit=256):
                        argument_overrides = {key: value for key, value in argument_overrides.items() if key not in _PROTECTED_ARGUMENTS}
                        scenario_meta = _meta(tmp_path, media, torrent, 0)
                        for key, value in meta_updates.items():
                            if key in Meta.__dataclass_fields__:
                                setattr(scenario_meta, key, value)
                        try:
                            await _invoke(method, scenario_meta, config, tmp_path, media, torrent, 0, argument_overrides)
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(f"{qualified}:{type(error).__name__}")
                        except Exception as error:
                            expected_rejections.append(f"{qualified}:{type(error).__name__}")

    asyncio.run(exercise())

    assert len(attempted) >= 45
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
