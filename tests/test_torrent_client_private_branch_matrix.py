"""Private-helper branch matrix for torrent-client adapters."""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import os
import pkgutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Self, get_args, get_origin, get_type_hints

import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations import torrent_clients
from tests.contract_scenarios import literal_branch_scenarios


class _Universal(dict[str, Any]):
    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("is_", "has_", "should_")):
            return False
        if name in {
            "returncode",
            "progress",
            "ratio",
            "size",
            "num_files",
            "piece_size",
        }:
            return 0
        return _Universal()

    def __call__(self, *_args: object, **_kwargs: object) -> _Universal:
        return _Universal()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def __await__(self):
        async def resolved() -> _Universal:
            return self

        return resolved().__await__()

    def __bool__(self) -> bool:
        return False

    def __int__(self) -> int:
        return 1

    def __float__(self) -> float:
        return 1.0

    def __str__(self) -> str:
        return "example"

    def __iter__(self):
        return iter(())


class _Torrent:
    infohash = "abc123"
    piece_size = 16 * 1024
    private = True
    source = "UA"

    @classmethod
    def read(cls, *_args: object, **_kwargs: object) -> _Torrent:
        return cls()

    @classmethod
    def copy(cls, _torrent: object) -> _Torrent:
        return cls()

    def write(
        self, path: str | Path, *_args: object, **_kwargs: object
    ) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"d4:infod4:name4:teste")

    def files(self) -> list[str]:
        return ["Example.mkv"]

    def validate(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def reuse(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def __getitem__(self, _key: str) -> Any:
        return {
            "name": "Example",
            "piece length": self.piece_size,
            "files": [],
        }


class _Process:
    returncode = 0
    pid = 1
    stdout = None
    stderr = None

    async def communicate(
        self, _input: bytes | None = None
    ) -> tuple[bytes, bytes]:
        return b"ok", b""

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.returncode = -9


class _Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


class _Client(_Universal):
    def torrents_info(
        self, *_args: object, **_kwargs: object
    ) -> list[_Universal]:
        return [
            _Universal(
                hash="abc123",
                name="Example",
                progress=1.0,
                state="seeding",
                content_path="/media/Example.mkv",
            )
        ]

    def torrents_add(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def auth_log_in(self) -> None:
        return None

    def app_version(self) -> str:
        return "5.0"


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            torrent_clients.__path__, f"{torrent_clients.__name__}."
        )
    ]


def _files(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "torrent-client"
    root.mkdir()
    media = root / "Example.mkv"
    media.write_bytes(b"media")
    torrent = root / "BASE.torrent"
    torrent.write_bytes(b"d4:infod4:name4:teste")
    directory = root / "Release"
    directory.mkdir()
    (directory / "Example.mkv").write_bytes(b"media")
    return {
        "root": root,
        "media": media,
        "torrent": torrent,
        "directory": directory,
    }


def _config(tmp_path: Path) -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).update(
        {
            "default_torrent_client": "qbit",
            "injecting_client_list": ["qbit"],
            "searching_client_list": ["qbit"],
        }
    )
    config.setdefault("TORRENT_CLIENTS", {}).update(
        {
            "qbit": {
                "torrent_client": "qbit",
                "qbit_url": "https://qbit.invalid",
                "qbit_port": 443,
                "qbit_user": "user",
                "qbit_pass": "pass",
                "local_path": [str(tmp_path)],
                "remote_path": [str(tmp_path)],
                "torrent_storage_dir": str(tmp_path),
                "torrent_storage_dir_string": str(tmp_path),
                "enable_search": True,
                "automatic_management_paths": [],
            },
            "rtorrent": {
                "torrent_client": "rtorrent",
                "rtorrent_url": "https://rtorrent.invalid",
                "local_path": [str(tmp_path)],
                "remote_path": [str(tmp_path)],
            },
            "deluge": {
                "torrent_client": "deluge",
                "local_path": [str(tmp_path)],
                "remote_path": [str(tmp_path)],
            },
            "transmission": {
                "torrent_client": "transmission",
                "local_path": [str(tmp_path)],
                "remote_path": [str(tmp_path)],
            },
            "watch": {
                "torrent_client": "watch",
                "watch_folder": str(tmp_path),
            },
        }
    )
    return config


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int = 0) -> Meta:
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"client-{profile}",
        path=str(files["media"] if profile % 2 == 0 else files["directory"]),
        filelist=[str(files["media"])],
        filename=files["media"].name,
        category="MOVIE",
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        title="Example",
        name="Example.2026.1080p.WEB-DL-GROUP",
        year=2026,
        tag="-GROUP",
        group="GROUP",
        torrent_path=str(files["torrent"]),
        reuse_torrent_path=str(files["torrent"]),
        infohash="abc123",
        torrenthash="abc123",
        torrent_client="qbit",
        client="qbit",
        filelist_count=1,
        source_size=5,
        subtitle_files=[],
        debug=profile == 3,
        unattended=True,
        tracker_status={},
    )


_MISSING = object()
_BLOCKED_TORRENT_HELPERS = frozenset(
    {"cleanup", "cleanup_all", "kill_processes", "kill_all_threads"}
)


def _named_values(
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> dict[str, object]:
    config = _config(Path(meta.base_dir))
    return {
        "meta": meta,
        "config": config,
        "configuration": config,
        "client_config": config["TORRENT_CLIENTS"]["qbit"],
        "client": _Client(),
        "qbt_client": _Client(),
        "torrent_client": "qbit",
        "client_name": "qbit",
        "path": str(meta.path),
        "content_path": str(meta.path),
        "source_path": str(meta.path),
        "destination": str(files["root"] / "destination"),
        "destination_path": str(files["root"] / "destination"),
        "torrent_path": str(files["torrent"]),
        "torrent_file": str(files["torrent"]),
        "torrent_file_path": str(files["torrent"]),
        "torrent": _Torrent(),
        "metainfo": {"info": {"name": "Example", "piece length": 16384}},
        "torrent_info": {"name": "Example", "files": []},
        "torrent_hash": "abc123",
        "torrenthash": "abc123",
        "info_hash": "abc123",
        "infohash": "abc123",
        "hash": "abc123",
        "files": [str(files["media"])],
        "filelist": [str(files["media"])],
        "local_path": str(meta.path),
        "remote_path": str(meta.path),
        "save_path": str(files["root"]),
        "content_layout": "Original",
        "category": "UA",
        "tags": ["UA"],
        "tracker": "AITHER",
        "tracker_name": "AITHER",
        "url": "https://qbit.invalid",
        "host": "qbit.invalid",
        "port": 443,
        "username": "user",
        "password": "pass",
        "label": "UA",
        "name": "Example",
        "search_term": "Example",
        "data": {},
        "payload": {},
        "response": _Universal(status_code=200, json=lambda: {}),
        "process": _Process(),
        "timeout": 1.0,
        "ratio": 1.0,
        "seed_time": 1,
        "use_hardlink": profile % 2 == 0,
        "use_symlink": profile % 2 == 1,
        "cross": profile == 2,
        "debug": profile == 3,
        "enabled": True,
    }


def _primitive_value(
    annotation: object,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return _Universal()
    if annotation is Path:
        return files["media"]
    factories: dict[object, Callable[[], object]] = {
        bool: lambda: bool(profile % 2),
        int: lambda: 1,
        float: lambda: 1.0,
        str: lambda: "example",
    }
    factory = factories.get(annotation)
    if factory is None:
        return _MISSING
    return factory()


def _collection_value(origin: object) -> object:
    factories: dict[object, Callable[[], object]] = {
        list: list,
        Sequence: list,
        dict: dict,
        Mapping: dict,
        set: set,
    }
    factory = factories.get(origin)
    if factory is None:
        return _MISSING
    return factory()


def _tuple_value(
    key: str,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> tuple[object, ...]:
    return tuple(
        _value(key, item, meta, files, profile)
        for item in args
        if item is not Ellipsis
    )


def _optional_value(
    key: str,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    concrete = next((item for item in args if item is not type(None)), str)
    return _value(key, concrete, meta, files, profile)


def _composite_value(
    key: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _collection_value(origin)
    if collection is not _MISSING:
        return collection
    if origin is tuple:
        return _tuple_value(key, args, meta, files, profile)
    if origin is None or type(None) not in args:
        return _Universal()
    return _optional_value(key, args, meta, files, profile)


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    key = name.casefold().lstrip("_")
    named = _named_values(meta, files, profile)
    if key in named:
        return named[key]
    primitive = _primitive_value(annotation, files, profile)
    if primitive is not _MISSING:
        return primitive
    return _composite_value(key, annotation, meta, files, profile)


def _safe_type_hints(target: object) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except NameError, TypeError:
        return {}


def _include_parameter(
    parameter: inspect.Parameter,
    overrides: Mapping[str, object],
) -> bool:
    if parameter.kind in {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }:
        return False
    if parameter.default is inspect.Parameter.empty:
        return True
    return parameter.name in overrides


def _parameter_value(
    parameter: inspect.Parameter,
    hints: Mapping[str, Any],
    overrides: Mapping[str, object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if parameter.name in overrides:
        return overrides[parameter.name]
    return _value(
        parameter.name,
        hints.get(parameter.name, parameter.annotation),
        meta,
        files,
        profile,
    )


def _invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
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
        value = _parameter_value(
            parameter,
            hints,
            overrides,
            meta,
            files,
            profile,
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


async def _resolved_result(result: object) -> object:
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.5)
    return result


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    resolved_overrides = overrides or {}
    positional, keywords = _invocation_arguments(
        function, meta, files, profile, resolved_overrides
    )
    return await _resolved_result(function(*positional, **keywords))


def _scenario_rows(
    function: Callable[..., object],
) -> list[tuple[dict[str, object], dict[str, object]]]:
    scenarios: list[tuple[dict[str, object], dict[str, object]]] = [({}, {})]
    scenarios.extend(
        literal_branch_scenarios(function, Meta.__dataclass_fields__, limit=96)
    )
    return scenarios


def _apply_meta_updates(meta: Meta, updates: Mapping[str, object]) -> None:
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)


async def _run_scenario(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    profile: int,
    meta_updates: Mapping[str, object],
    argument_updates: Mapping[str, object],
    terminations: list[str],
    rejections: list[str],
) -> None:
    meta = _meta(tmp_path, files, profile % 4)
    _apply_meta_updates(meta, meta_updates)
    try:
        await _invoke(
            function,
            meta,
            files,
            profile % 4,
            argument_updates,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        rejections.append(f"{qualified}:{type(error).__name__}")
    finally:
        os.chdir(repository)


async def _run_callable(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    attempted.add(qualified)
    for profile, row in enumerate(_scenario_rows(function)):
        meta_updates, argument_updates = row
        await _run_scenario(
            qualified,
            function,
            tmp_path,
            files,
            repository,
            profile,
            meta_updates,
            argument_updates,
            terminations,
            rejections,
        )


async def _process_double(*_args: object, **_kwargs: object) -> _Process:
    return _Process()


async def _no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


def _patch_torrent_module(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attribute, replacement in (
        ("Torrent", _Torrent),
        ("Client", _Client),
    ):
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, replacement)


def _module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__
        and not name.startswith("__")
        and name not in _BLOCKED_TORRENT_HELPERS
    ]


def _module_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (name, class_type)
        for name, class_type in inspect.getmembers(module, inspect.isclass)
        if class_type.__module__ == module.__name__
    ]


async def _instantiate_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    rejections: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, _meta(tmp_path, files), files, 0)
    except Exception as error:
        rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None


def _instance_method(
    instance: object,
    module: ModuleType,
    class_name: str,
    method_name: str,
    member: object,
    rejections: list[str],
) -> Callable[..., object] | None:
    if method_name.startswith("__"):
        return None
    if method_name in _BLOCKED_TORRENT_HELPERS or not callable(member):
        return None
    try:
        return getattr(instance, method_name)
    except Exception as error:
        rejections.append(
            f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}"
        )
        return None


async def _exercise_instance(
    module: ModuleType,
    class_name: str,
    instance: object,
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for method_name, member in inspect.getmembers_static(instance):
        method = _instance_method(
            instance,
            module,
            class_name,
            method_name,
            member,
            rejections,
        )
        if method is None:
            continue
        await _run_callable(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            tmp_path,
            files,
            repository,
            attempted,
            terminations,
            rejections,
        )


async def _exercise_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    instance = await _instantiate_class(
        module, class_name, class_type, tmp_path, files, rejections
    )
    if instance is None:
        return
    await _exercise_instance(
        module,
        class_name,
        instance,
        tmp_path,
        files,
        repository,
        attempted,
        terminations,
        rejections,
    )


async def _exercise_module(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    _patch_torrent_module(module, monkeypatch)
    for name, function in _module_functions(module):
        await _run_callable(
            f"{module.__name__}.{name}",
            function,
            tmp_path,
            files,
            repository,
            attempted,
            terminations,
            rejections,
        )
    for class_name, class_type in _module_classes(module):
        await _exercise_class(
            module,
            class_name,
            class_type,
            tmp_path,
            files,
            repository,
            attempted,
            terminations,
            rejections,
        )


async def _exercise_modules(
    modules: list[ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: Mapping[str, Path],
    repository: Path,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for module in modules:
        await _exercise_module(
            module,
            monkeypatch,
            tmp_path,
            files,
            repository,
            attempted,
            terminations,
            rejections,
        )


def test_torrent_client_private_helpers_execute_with_local_doubles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _files(tmp_path)
    modules = _modules()
    repository = Path.cwd()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _process_double)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _process_double)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"ok"
    )
    attempted: set[str] = set()
    terminations: list[str] = []
    rejections: list[str] = []
    asyncio.run(
        _exercise_modules(
            modules,
            monkeypatch,
            tmp_path,
            files,
            repository,
            attempted,
            terminations,
            rejections,
        )
    )
    assert len(attempted) >= 80
    assert terminations == []
    assert all(":" in rejection for rejection in rejections)
