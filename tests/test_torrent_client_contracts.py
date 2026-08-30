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
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, cast, get_args, get_origin, get_type_hints

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

    def torrents_info(
        self, *_args: object, **_kwargs: object
    ) -> list[_TorrentInfo]:
        return [_TorrentInfo(self.root, self.profile)]

    def torrents_files(
        self, *_args: object, **_kwargs: object
    ) -> list[_TorrentFile]:
        return [_TorrentFile()]

    def torrents_trackers(
        self, *_args: object, **_kwargs: object
    ) -> list[dict[str, Any]]:
        return [{"url": "https://tracker.example/announce", "status": 2}]

    def torrents_properties(
        self, *_args: object, **_kwargs: object
    ) -> dict[str, Any]:
        return {
            "comment": "https://passthepopcorn.me/torrents.php?id=1&torrentid=2",
            "save_path": str(self.root),
        }

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

    def get_torrents(
        self, *_args: object, **_kwargs: object
    ) -> list[_TorrentInfo]:
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
            return {
                b"a" * 40: {
                    b"name": b"Example Release",
                    b"save_path": str(Path.cwd()).encode(),
                }
            }
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
    config = {
        key: value.copy() if isinstance(value, dict) else value
        for key, value in example_config.items()
    }
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
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            torrent_clients_package.__path__,
            f"{torrent_clients_package.__name__}.",
        )
    ]


_MISSING = object()


def _client_values(
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> dict[str, object]:
    client = _QbitClient()
    client.root = tmp_path
    client.profile = profile
    info = _TorrentInfo(tmp_path, profile)
    return {
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


def _scalar_value(annotation: object, profile: int) -> object:
    values = {
        bool: profile % 2 == 0,
        int: 1,
        float: 1.0,
        str: "example",
    }
    return values.get(annotation, _MISSING)


def _direct_composite_value(
    origin: object, tmp_path: Path, profile: int
) -> object:
    if origin in {list, Sequence}:
        return [_TorrentInfo(tmp_path, profile)]
    if origin in {dict, Mapping}:
        return {}
    if origin is tuple:
        return ()
    return _MISSING


def _optional_type(args: tuple[object, ...]) -> object | None:
    if type(None) not in args:
        return None
    return next((item for item in args if item is not type(None)), str)


def _composite_value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    origin = get_origin(annotation)
    direct = _direct_composite_value(origin, tmp_path, profile)
    if direct is not _MISSING:
        return direct
    concrete = _optional_type(get_args(annotation))
    if concrete is None:
        return None
    return _value(
        name, concrete, meta, config, tmp_path, media, torrent, profile
    )


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    values = _client_values(meta, config, tmp_path, media, torrent, profile)
    if normalized in values:
        return values[normalized]
    if annotation in {inspect.Parameter.empty, Any}:
        return None
    if annotation is Path:
        return media
    scalar = _scalar_value(annotation, profile)
    if scalar is not _MISSING:
        return scalar
    return _composite_value(
        normalized, annotation, meta, config, tmp_path, media, torrent, profile
    )


_LITERAL_SCENARIO_LIMIT = 64


_PROTECTED_ARGUMENTS = frozenset(
    {
        "meta",
        "config",
        "client",
        "qbt_client",
        "qbit_client",
        "qbt_session",
        "proxy_session",
        "session",
        "torrent",
        "torrent_info",
        "response",
        "ssl_context",
    }
)


def _coerce_number(value: object, annotation: object) -> object:
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
    return _MISSING


def _coerce_list(
    value: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> list[object]:
    if isinstance(value, list):
        return value
    element = args[0] if args else object
    return [
        _coerce_override(
            value, element, meta, config, tmp_path, media, torrent, profile
        )
    ]


def _coerce_mapping(value: object) -> object:
    return value if isinstance(value, dict) else {}


def _coerce_tuple(value: object) -> object:
    return value if isinstance(value, tuple) else ()


def _coerce_set(value: object) -> object:
    return value if isinstance(value, set) else {value}


_COLLECTION_COERCERS: dict[object, Callable[[object], object]] = {
    dict: _coerce_mapping,
    Mapping: _coerce_mapping,
    tuple: _coerce_tuple,
    set: _coerce_set,
}


def _coerce_direct_collection(value: object, origin: object) -> object:
    coercer = _COLLECTION_COERCERS.get(origin)
    if coercer is None:
        return _MISSING
    return coercer(value)


def _coerce_collection(
    value: object,
    origin: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    if origin in {list, Sequence}:
        return _coerce_list(
            value, args, meta, config, tmp_path, media, torrent, profile
        )
    return _coerce_direct_collection(value, origin)


def _optional_concrete(args: tuple[object, ...]) -> object | None:
    if type(None) not in args:
        return None
    return next((item for item in args if item is not type(None)), object)


def _coerce_optional(
    value: object,
    args: tuple[object, ...],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    if value is None:
        return value
    concrete = _optional_concrete(args)
    if concrete is None:
        return value
    return _coerce_override(
        value, concrete, meta, config, tmp_path, media, torrent, profile
    )


def _coerce_meta(_value: object, meta: Meta, _media: Path) -> object:
    return meta


def _coerce_path(value: object, _meta: Meta, media: Path) -> object:
    return value if isinstance(value, Path) else media


def _coerce_str(value: object, _meta: Meta, _media: Path) -> object:
    return str(value)


def _coerce_bool(value: object, _meta: Meta, _media: Path) -> object:
    return bool(value)


_SIMPLE_COERCERS: dict[object, Callable[[object, Meta, Path], object]] = {
    Meta: _coerce_meta,
    Path: _coerce_path,
    str: _coerce_str,
    bool: _coerce_bool,
}


def _coerce_simple(
    value: object, annotation: object, meta: Meta, media: Path
) -> object:
    coercer = _SIMPLE_COERCERS.get(annotation)
    if coercer is None:
        return _MISSING
    return coercer(value, meta, media)


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
    simple = _coerce_simple(value, annotation, meta, media)
    if simple is not _MISSING:
        return simple
    number = _coerce_number(value, annotation)
    if number is not _MISSING:
        return number
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _coerce_collection(
        value, origin, args, meta, config, tmp_path, media, torrent, profile
    )
    if collection is not _MISSING:
        return collection
    return _coerce_optional(
        value, args, meta, config, tmp_path, media, torrent, profile
    )


def _safe_type_hints(target: object) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except NameError, TypeError:
        return {}


def _include_parameter(
    parameter: inspect.Parameter, overrides: Mapping[str, object]
) -> bool:
    if parameter.kind in {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }:
        return False
    return (
        parameter.default is inspect.Parameter.empty
        or parameter.name in overrides
    )


def _override_value(
    parameter: inspect.Parameter,
    annotation: object,
    value: object,
    overrides: Mapping[str, object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
) -> object:
    if (
        parameter.name not in overrides
        or parameter.name in _PROTECTED_ARGUMENTS
    ):
        return value
    return _coerce_override(
        overrides[parameter.name],
        annotation,
        meta,
        config,
        tmp_path,
        media,
        torrent,
        profile,
    )


def _invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    target = function.__init__ if inspect.isclass(function) else function
    hints = _safe_type_hints(target)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_parameter(parameter, overrides):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = _value(
            parameter.name,
            annotation,
            meta,
            config,
            tmp_path,
            media,
            torrent,
            profile,
        )
        value = _override_value(
            parameter,
            annotation,
            value,
            overrides,
            meta,
            config,
            tmp_path,
            media,
            torrent,
            profile,
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


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
    resolved_overrides = overrides or {}
    positional, keywords = _invocation_arguments(
        function,
        meta,
        config,
        tmp_path,
        media,
        torrent,
        profile,
        resolved_overrides,
    )
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


async def _no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


def _patch_clients(monkeypatch: Any) -> None:
    monkeypatch.setattr(qbittorrentapi, "Client", _QbitClient)
    monkeypatch.setattr(transmission_rpc, "Client", _TransmissionClient)
    monkeypatch.setattr("deluge_client.DelugeRPCClient", _DelugeClient)
    monkeypatch.setattr("xmlrpc.client.ServerProxy", _XmlRpc)
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__ and not name.startswith("__")
    ]


def _module_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (name, class_type)
        for name, class_type in inspect.getmembers(module, inspect.isclass)
        if class_type.__module__ == module.__name__
        and not name.startswith("_")
    ]


def _filtered_overrides(overrides: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in overrides.items()
        if key not in _PROTECTED_ARGUMENTS
    }


def _apply_meta_updates(meta: Meta, updates: Mapping[str, object]) -> None:
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)


async def _run_call(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    profile: int,
    terminations: list[str],
    rejections: list[str],
    overrides: Mapping[str, object] | None = None,
) -> None:
    try:
        await _invoke(
            function,
            meta,
            config,
            tmp_path,
            media,
            torrent,
            profile,
            overrides,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        rejections.append(f"{qualified}:{type(error).__name__}")


async def _run_profiles(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    terminations: list[str],
    rejections: list[str],
) -> None:
    for profile in range(4):
        await _run_call(
            qualified,
            function,
            _meta(tmp_path, media, torrent, profile),
            config,
            tmp_path,
            media,
            torrent,
            profile,
            terminations,
            rejections,
        )


async def _run_literal_scenarios(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    terminations: list[str],
    rejections: list[str],
) -> None:
    for meta_updates, overrides in literal_branch_scenarios(
        function,
        Meta.__dataclass_fields__,
        limit=_LITERAL_SCENARIO_LIMIT,
    ):
        scenario_meta = _meta(tmp_path, media, torrent, 0)
        _apply_meta_updates(scenario_meta, meta_updates)
        await _run_call(
            qualified,
            function,
            scenario_meta,
            config,
            tmp_path,
            media,
            torrent,
            0,
            terminations,
            rejections,
            _filtered_overrides(overrides),
        )


async def _exercise_callable(
    qualified: str,
    function: Callable[..., object],
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    attempted.add(qualified)
    await _run_profiles(
        qualified,
        function,
        config,
        tmp_path,
        media,
        torrent,
        terminations,
        rejections,
    )
    await _run_literal_scenarios(
        qualified,
        function,
        config,
        tmp_path,
        media,
        torrent,
        terminations,
        rejections,
    )


async def _instantiate_client_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    rejections: list[str],
) -> object | None:
    try:
        instance = await _invoke(
            class_type,
            _meta(tmp_path, media, torrent, 0),
            config,
            tmp_path,
            media,
            torrent,
            0,
        )
    except Exception as error:
        rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None
    with suppress(AttributeError, TypeError):
        cast(Any, instance).config = config
    return instance


async def _exercise_instance(
    module: ModuleType,
    class_name: str,
    instance: object,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for method_name, method in inspect.getmembers(instance, callable):
        if method_name.startswith("__"):
            continue
        implementation = getattr(method, "__func__", method)
        if getattr(implementation, "__module__", None) != module.__name__:
            continue
        await _exercise_callable(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            config,
            tmp_path,
            media,
            torrent,
            attempted,
            terminations,
            rejections,
        )


async def _exercise_module(
    module: ModuleType,
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for name, function in _module_functions(module):
        await _exercise_callable(
            f"{module.__name__}.{name}",
            function,
            config,
            tmp_path,
            media,
            torrent,
            attempted,
            terminations,
            rejections,
        )
    for class_name, class_type in _module_classes(module):
        instance = await _instantiate_client_class(
            module,
            class_name,
            class_type,
            config,
            tmp_path,
            media,
            torrent,
            rejections,
        )
        if instance is not None:
            await _exercise_instance(
                module,
                class_name,
                instance,
                config,
                tmp_path,
                media,
                torrent,
                attempted,
                terminations,
                rejections,
            )


async def _exercise_modules(
    modules: list[ModuleType],
    config: dict[str, Any],
    tmp_path: Path,
    media: Path,
    torrent: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for module in modules:
        await _exercise_module(
            module,
            config,
            tmp_path,
            media,
            torrent,
            attempted,
            terminations,
            rejections,
        )


def test_torrent_client_catalog_uses_local_fakes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    media, torrent = _fixture(tmp_path)
    config = _config(tmp_path)
    _patch_clients(monkeypatch)
    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []
    asyncio.run(
        _exercise_modules(
            _modules(),
            config,
            tmp_path,
            media,
            torrent,
            attempted,
            terminations,
            expected_rejections,
        )
    )
    assert len(attempted) >= 45
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
