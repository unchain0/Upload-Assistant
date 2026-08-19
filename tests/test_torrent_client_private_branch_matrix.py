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
        if name in {"returncode", "progress", "ratio", "size", "num_files", "piece_size"}:
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

    def write(self, path: str | Path, *_args: object, **_kwargs: object) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"d4:infod4:name4:teste")

    def files(self) -> list[str]:
        return ["Example.mkv"]

    def validate(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def reuse(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def __getitem__(self, _key: str) -> Any:
        return {"name": "Example", "piece length": self.piece_size, "files": []}


class _Process:
    returncode = 0
    pid = 1
    stdout = None
    stderr = None

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
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
    def torrents_info(self, *_args: object, **_kwargs: object) -> list[_Universal]:
        return [_Universal(hash="abc123", name="Example", progress=1.0, state="seeding", content_path="/media/Example.mkv")]

    def torrents_add(self, *_args: object, **_kwargs: object) -> bool:
        return True

    def auth_log_in(self) -> None:
        return None

    def app_version(self) -> str:
        return "5.0"


def _modules() -> list[ModuleType]:
    return [importlib.import_module(info.name) for info in pkgutil.iter_modules(torrent_clients.__path__, f"{torrent_clients.__name__}.")]


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
    return {"root": root, "media": media, "torrent": torrent, "directory": directory}


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
            "deluge": {"torrent_client": "deluge", "local_path": [str(tmp_path)], "remote_path": [str(tmp_path)]},
            "transmission": {"torrent_client": "transmission", "local_path": [str(tmp_path)], "remote_path": [str(tmp_path)]},
            "watch": {"torrent_client": "watch", "watch_folder": str(tmp_path)},
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


def _value(name: str, annotation: object, meta: Meta, files: Mapping[str, Path], profile: int) -> object:
    key = name.casefold().lstrip("_")
    config = _config(Path(meta.base_dir))
    values: dict[str, object] = {
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
    if key in values:
        return values[key]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation in {inspect.Parameter.empty, Any}:
        return _Universal()
    if annotation is bool:
        return bool(profile % 2)
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "example"
    if annotation is Path:
        return files["media"]
    if origin in {list, Sequence}:
        return []
    if origin in {dict, Mapping}:
        return {}
    if origin is set:
        return set()
    if origin is tuple:
        return tuple(_value(key, item, meta, files, profile) for item in args if item is not Ellipsis)
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(key, concrete, meta, files, profile)
    return _Universal()


async def _invoke(function: Callable[..., object], meta: Meta, files: Mapping[str, Path], profile: int, overrides: Mapping[str, object] | None = None) -> object:
    overrides = overrides or {}
    target = function.__init__ if inspect.isclass(function) else function
    try:
        hints = get_type_hints(target)
    except NameError, TypeError:
        hints = {}
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.default is not inspect.Parameter.empty and parameter.name not in overrides:
            continue
        value = overrides.get(parameter.name, _value(parameter.name, hints.get(parameter.name, parameter.annotation), meta, files, profile))
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.5)
    return result


def test_torrent_client_private_helpers_execute_with_local_doubles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _files(tmp_path)
    modules = _modules()
    repository = Path.cwd()

    async def process(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    async def no_sleep(_delay: float = 0, *_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", process)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", process)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Completed())
    monkeypatch.setattr(subprocess, "check_output", lambda *_args, **_kwargs: b"ok")
    attempted: set[str] = set()
    terminations: list[str] = []
    rejections: list[str] = []
    blocked = {"cleanup", "cleanup_all", "kill_processes", "kill_all_threads"}

    async def run_callable(qualified: str, function: Callable[..., object]) -> None:
        attempted.add(qualified)
        scenarios = [({}, {})]
        scenarios.extend(literal_branch_scenarios(function, Meta.__dataclass_fields__, limit=96))
        for profile, (meta_updates, argument_updates) in enumerate(scenarios):
            meta = _meta(tmp_path, files, profile % 4)
            for key, value in meta_updates.items():
                if key in Meta.__dataclass_fields__:
                    setattr(meta, key, value)
            try:
                await _invoke(function, meta, files, profile % 4, argument_updates)
            except (KeyboardInterrupt, SystemExit) as error:
                terminations.append(f"{qualified}:{type(error).__name__}")
            except Exception as error:
                rejections.append(f"{qualified}:{type(error).__name__}")
            finally:
                os.chdir(repository)

    async def exercise() -> None:
        for module in modules:
            for attribute, replacement in (("Torrent", _Torrent), ("Client", _Client)):
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, replacement)
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ == module.__name__ and not name.startswith("__") and name not in blocked:
                    await run_callable(f"{module.__name__}.{name}", function)
            for class_name, class_type in inspect.getmembers(module, inspect.isclass):
                if class_type.__module__ != module.__name__:
                    continue
                try:
                    instance = await _invoke(class_type, _meta(tmp_path, files), files, 0)
                except Exception as error:
                    rejections.append(f"{module.__name__}.{class_name}.__init__:{type(error).__name__}")
                    continue
                for method_name, member in inspect.getmembers_static(instance):
                    if method_name.startswith("__") or method_name in blocked or not callable(member):
                        continue
                    try:
                        method = getattr(instance, method_name)
                    except Exception as error:
                        rejections.append(f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}")
                        continue
                    await run_callable(f"{module.__name__}.{class_name}.{method_name}", method)

    asyncio.run(exercise())
    assert len(attempted) >= 80
    assert terminations == []
    assert all(":" in rejection for rejection in rejections)
