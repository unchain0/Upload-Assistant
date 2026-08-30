"""Boundary-smoke contracts for the smaller integration packages.

Focused tests own exact behavior.  These contracts exercise every public
callable against temporary files and deterministic doubles so integration
surfaces cannot silently grow untested process, network, or filesystem effects.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import os
import pkgutil
import subprocess
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import httpx
import requests

from data.example_config import config as example_config
from src.domain_models.release import Meta
from tests.contract_scenarios import literal_branch_scenarios

_PACKAGE_NAMES = (
    "src.integrations.cache",
    "src.integrations.configuration",
    "src.integrations.filesystem",
    "src.integrations.mapping",
    "src.integrations.observability",
    "src.integrations.packaging",
    "src.integrations.security",
    "src.integrations.torrent",
    "src.integrations.usenet",
)


class _Response:
    status_code = 200
    text = "ok"
    content = b"ok"
    url = "https://example.invalid/resource"
    headers: ClassVar[dict[str, str]] = {
        "content-type": "application/json",
        "content-length": "2",
    }

    def json(self) -> dict[str, Any]:
        return {
            "success": True,
            "status": "ok",
            "data": [],
            "results": [],
            "items": [],
            "id": 1,
            "url": self.url,
        }

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def put(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def delete(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def stream(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Session:
    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _Stream:
    async def read(self, _size: int = -1) -> bytes:
        return b"ok"

    async def readline(self) -> bytes:
        return b"ok\n"

    def at_eof(self) -> bool:
        return True


class _Process:
    returncode = 0
    pid = 1

    def __init__(self) -> None:
        self.stdout = _Stream()
        self.stderr = _Stream()

    async def communicate(
        self, _input: bytes | None = None
    ) -> tuple[bytes, bytes]:
        return b"ok", b""

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def send_signal(self, _signal: int) -> None:
        return None


class _Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


class _Universal(dict[str, Any]):
    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("is_", "has_")):
            return False
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


def _fixture_tree(tmp_path: Path) -> dict[str, Path]:
    release = tmp_path / "release"
    release.mkdir()
    media = release / "Example.Release.2026.1080p.WEB-DL.H.264-GROUP.mkv"
    media.write_bytes(b"media")
    image = release / "screen-0.png"
    image.write_bytes(b"image")
    description = release / "DESCRIPTION.txt"
    description.write_text("Example description", encoding="utf-8")
    nzb = release / "Example.Release.nzb"
    nzb.write_text(
        '<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"><head/></nzb>',
        encoding="utf-8",
    )
    torrent = release / "BASE.torrent"
    torrent.write_bytes(b"d4:infod4:name4:teste")
    temp = tmp_path / "tmp" / "support"
    temp.mkdir(parents=True)
    for source in (image, description, nzb, torrent):
        (temp / source.name).write_bytes(source.read_bytes())
    return {
        "root": tmp_path,
        "release": release,
        "media": media,
        "image": image,
        "description": description,
        "nzb": nzb,
        "torrent": torrent,
        "temp": temp,
    }


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int) -> Meta:
    return Meta(
        base_dir=str(tmp_path),
        uuid="support",
        path=str(files["media"]),
        filename=files["media"].name,
        filelist=[str(files["media"])],
        category=("MOVIE", "TV", "BOOK")[profile % 3],
        type=("WEBDL", "REMUX", "M4B")[profile % 3],
        source="WEB",
        resolution="1080p",
        title="Example Release",
        name="Example Release 2026 1080p WEB-DL H.264-GROUP",
        clean_name="Example.Release.2026.1080p.WEB-DL.H.264-GROUP",
        year=2026,
        imdb_id="tt1234567",
        tmdb_id=123,
        tvdb_id=456,
        season=1,
        episode=1,
        season_int=1,
        episode_int=1,
        tag="-GROUP",
        group="GROUP",
        video_codec="H.264",
        audio="DDP 5.1",
        channels="5.1",
        image_list=[
            {
                "img_url": "https://img.invalid/a.png",
                "raw_url": "https://img.invalid/a.png",
                "web_url": "https://img.invalid/a",
            }
        ],
        screens=1,
        tracker_status={},
        trackers=["AITHER"],
        nzb_path=str(files["nzb"]),
        torrent_path=str(files["torrent"]),
        unattended=True,
        debug=profile == 2,
        mediainfo={
            "media": {"track": [{"@type": "General", "Format": "Matroska"}]}
        },
        bdinfo={},
        discs=[],
        manual=False,
    )


def _modules() -> list[ModuleType]:
    modules: list[ModuleType] = []
    for package_name in _PACKAGE_NAMES:
        package = importlib.import_module(package_name)
        modules.extend(
            importlib.import_module(info.name)
            for info in pkgutil.iter_modules(
                package.__path__, f"{package.__name__}."
            )
        )
    return modules


_NO_CONTAINER_VALUE = object()


def _named_values(
    meta: Meta, files: Mapping[str, Path], profile: int
) -> dict[str, object]:
    config = copy.deepcopy(example_config)
    return {
        "meta": meta,
        "configuration": config,
        "config": config,
        "base_dir": str(files["root"]),
        "root": files["root"],
        "path": files["media"],
        "file_path": files["media"],
        "filepath": files["media"],
        "source": files["image"],
        "source_path": files["image"],
        "destination": files["temp"] / "destination.bin",
        "destination_path": files["temp"] / "destination.bin",
        "temp_dir": files["temp"],
        "directory": files["temp"],
        "release_id": meta.uuid,
        "folder_id": meta.uuid,
        "uuid": meta.uuid,
        "tracker": "AITHER",
        "tracker_name": "AITHER",
        "collection": "screenshots",
        "group": "main",
        "host": "imgbb",
        "img_host": "imgbb",
        "url": "https://example.invalid/resource",
        "raw_url": "https://example.invalid/raw.png",
        "image_url": "https://example.invalid/image.png",
        "image_path": files["image"],
        "images": list(meta.image_list),
        "image_list": list(meta.image_list),
        "allowed_hosts": ["imgbb", "imgbox"],
        "unavailable_hosts": set(),
        "reviewed_uploads": [],
        "payload": {"data": [], "results": []},
        "data": {"data": [], "results": []},
        "mapping": {"value": "example"},
        "value": "example",
        "text": "example",
        "name": meta.name,
        "title": meta.title,
        "command": ["tool", "--version"],
        "cmd": ["tool", "--version"],
        "args": ["tool", "--version"],
        "nzb_path": files["nzb"],
        "torrent_path": files["torrent"],
        "filelist": list(meta.filelist),
        "files": list(meta.filelist),
        "callback": lambda *_args, **_kwargs: None,
        "index": profile,
        "count": 1,
        "size": 1024,
        "total": 1,
        "enabled": bool(profile % 2),
    }


def _tuple_annotation_value(
    normalized: str,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> tuple[object, ...]:
    return tuple(
        _value(normalized, item, meta, files, profile)
        for item in args
        if item is not Ellipsis
    )


def _container_annotation_value(
    origin: object,
    args: tuple[object, ...],
    normalized: str,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if origin in {list, Sequence}:
        return []
    if origin in {dict, Mapping}:
        return {}
    if origin is set:
        return set()
    if origin is tuple:
        return _tuple_annotation_value(normalized, args, meta, files, profile)
    return _NO_CONTAINER_VALUE


def _optional_annotation_value(
    origin: object,
    args: tuple[object, ...],
    normalized: str,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if origin is None or type(None) not in args:
        return _Universal()
    for item in args:
        if item is not type(None):
            return _value(normalized, item, meta, files, profile)
    return _Universal()


def _annotation_is_unspecified(annotation: object) -> bool:
    return annotation is inspect.Parameter.empty or annotation is Any


def _annotation_value(
    annotation: object,
    normalized: str,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if _annotation_is_unspecified(annotation):
        return _Universal()
    if annotation is bool:
        return bool(profile % 2)
    scalar_values: dict[object, object] = {
        int: 1,
        float: 1.0,
        str: "example",
        Path: files["media"],
    }
    if annotation in scalar_values:
        return scalar_values[annotation]
    origin = get_origin(annotation)
    args = get_args(annotation)
    container = _container_annotation_value(
        origin, args, normalized, meta, files, profile
    )
    if container is not _NO_CONTAINER_VALUE:
        return container
    return _optional_annotation_value(
        origin, args, normalized, meta, files, profile
    )


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    values = _named_values(meta, files, profile)
    if normalized in values:
        return values[normalized]
    return _annotation_value(annotation, normalized, meta, files, profile)


def _safe_type_hints(function: Callable[..., object]) -> dict[str, Any]:
    hint_target = function.__init__ if inspect.isclass(function) else function
    try:
        return get_type_hints(hint_target)
    except NameError, TypeError:
        return {}


def _skip_parameter(
    parameter: inspect.Parameter, overrides: Mapping[str, object]
) -> bool:
    if parameter.kind in {
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    }:
        return True
    if parameter.default is inspect.Parameter.empty:
        return False
    return parameter.name not in overrides


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


def _call_arguments(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    positional: list[object] = []
    keywords: dict[str, object] = {}
    hints = _safe_type_hints(function)
    for parameter in inspect.signature(function).parameters.values():
        if _skip_parameter(parameter, overrides):
            continue
        value = _parameter_value(
            parameter, hints, overrides, meta, files, profile
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    positional, keywords = _call_arguments(
        function, meta, files, profile, overrides or {}
    )
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


@dataclass
class _ContractState:
    tmp_path: Path
    files: Mapping[str, Path]
    repository_cwd: Path
    attempted: set[str]
    terminations: list[str]
    expected_rejections: list[str]
    destructive_names: set[str]


def _patch_module_boundaries(module: ModuleType, monkeypatch: Any) -> None:
    for attribute, replacement in (
        ("AsyncClient", _AsyncClient),
        ("Session", _Session),
    ):
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, replacement)


def _function_is_eligible(
    module: ModuleType,
    name: str,
    function: Callable[..., object],
    state: _ContractState,
) -> bool:
    if function.__module__ != module.__name__:
        return False
    if name.startswith("__"):
        return False
    return name not in state.destructive_names


async def _record_call(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    state: _ContractState,
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> None:
    try:
        await _invoke(function, meta, state.files, profile, overrides)
    except (KeyboardInterrupt, SystemExit) as error:
        state.terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        state.expected_rejections.append(f"{qualified}:{type(error).__name__}")
    finally:
        os.chdir(state.repository_cwd)


async def _exercise_profiles(
    qualified: str,
    function: Callable[..., object],
    state: _ContractState,
    profile_count: int,
) -> None:
    for profile in range(profile_count):
        await _record_call(
            qualified,
            function,
            _meta(state.tmp_path, state.files, profile),
            state,
            profile,
        )


def _scenario_meta(
    state: _ContractState, meta_updates: Mapping[str, object]
) -> Meta:
    scenario_meta = _meta(state.tmp_path, state.files, 0)
    for key, value in meta_updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(scenario_meta, key, value)
    return scenario_meta


async def _exercise_literals(
    qualified: str,
    function: Callable[..., object],
    state: _ContractState,
) -> None:
    for meta_updates, argument_overrides in literal_branch_scenarios(
        function, Meta.__dataclass_fields__, limit=192
    ):
        await _record_call(
            qualified,
            function,
            _scenario_meta(state, meta_updates),
            state,
            0,
            argument_overrides,
        )


async def _exercise_callable(
    qualified: str,
    function: Callable[..., object],
    state: _ContractState,
    profile_count: int,
) -> None:
    state.attempted.add(qualified)
    await _exercise_profiles(qualified, function, state, profile_count)
    await _exercise_literals(qualified, function, state)


async def _exercise_module_functions(
    module: ModuleType, state: _ContractState
) -> None:
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if not _function_is_eligible(module, name, function, state):
            continue
        await _exercise_callable(
            f"{module.__name__}.{name}", function, state, 3
        )


async def _construct_instance(
    module: ModuleType,
    class_name: str,
    class_type: type[object],
    state: _ContractState,
) -> object | None:
    try:
        return await _invoke(
            class_type,
            _meta(state.tmp_path, state.files, 0),
            state.files,
            0,
        )
    except Exception as error:
        state.expected_rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None


def _member_is_eligible(
    method_name: str, static_member: object, state: _ContractState
) -> bool:
    if method_name.startswith("__"):
        return False
    if method_name in state.destructive_names:
        return False
    return callable(static_member)


def _resolve_method(
    instance: object,
    qualified: str,
    method_name: str,
    state: _ContractState,
) -> Callable[..., object] | None:
    try:
        method = getattr(instance, method_name)
    except Exception as error:
        state.expected_rejections.append(f"{qualified}:{type(error).__name__}")
        return None
    return method if callable(method) else None


async def _exercise_instance_methods(
    module: ModuleType,
    class_name: str,
    instance: object,
    state: _ContractState,
) -> None:
    for method_name, static_member in inspect.getmembers_static(instance):
        if not _member_is_eligible(method_name, static_member, state):
            continue
        qualified = f"{module.__name__}.{class_name}.{method_name}"
        method = _resolve_method(instance, qualified, method_name, state)
        if method is None:
            continue
        if os.environ.get("UA_CONTRACT_TRACE"):
            print(qualified, flush=True)
        await _exercise_callable(qualified, method, state, 2)


async def _exercise_module_classes(
    module: ModuleType, state: _ContractState
) -> None:
    for class_name, class_type in inspect.getmembers(module, inspect.isclass):
        if class_type.__module__ != module.__name__:
            continue
        instance = await _construct_instance(
            module, class_name, class_type, state
        )
        if instance is None:
            continue
        await _exercise_instance_methods(module, class_name, instance, state)


async def _exercise_catalog(
    modules: Sequence[ModuleType], state: _ContractState, monkeypatch: Any
) -> None:
    for module in modules:
        _patch_module_boundaries(module, monkeypatch)
        await _exercise_module_functions(module, state)
        await _exercise_module_classes(module, state)


def test_support_integration_catalog_uses_local_boundary_doubles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    files = _fixture_tree(tmp_path)
    modules = _modules()
    repository_cwd = Path.cwd()

    async def fake_process(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "get", _Session().get)
    monkeypatch.setattr(requests, "post", _Session().post)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_process)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_process)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"ok"
    )
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")

    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []
    state = _ContractState(
        tmp_path=tmp_path,
        files=files,
        repository_cwd=repository_cwd,
        attempted=attempted,
        terminations=terminations,
        expected_rejections=expected_rejections,
        destructive_names={
            "cleanup",
            "cleanup_all",
            "kill_all_threads",
            "kill_processes",
            "remove_empty_directories",
            "reset_terminal",
            "prepare_and_upload_usenet",
            "run_7z_with_progress",
            "run_nyuu_with_progress",
            "run_par2_with_progress",
            "run_pesto_with_progress",
        },
    )

    asyncio.run(_exercise_catalog(modules, state, monkeypatch))

    assert len(attempted) >= 100
    assert terminations == []
    assert all(":" in rejection for rejection in expected_rejections)
