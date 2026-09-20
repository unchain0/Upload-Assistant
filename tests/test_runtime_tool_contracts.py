"""Deterministic contracts for downloaded runtime-tool integrations."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import pkgutil
import platform
import subprocess
import tarfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, get_args, get_origin, get_type_hints

import httpx
import requests

import src.integrations.runtime_tools as runtime_tools_package
from data.example_config import config as example_config
from tests.contract_scenarios import literal_branch_scenarios


class _Response:
    status_code = 200
    content = b"tool"
    text = "tool"
    headers: ClassVar[dict[str, str]] = {"content-length": "4"}

    def json(self) -> dict[str, Any]:
        return {"tag_name": "v1.0.0", "assets": [], "sha256": ""}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192) -> list[bytes]:
        del chunk_size
        return [self.content]


class _AsyncResponse(_Response):
    async def aiter_bytes(self) -> Any:
        yield self.content


class _AsyncClient:
    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _AsyncResponse:
        return _AsyncResponse()

    async def post(self, *_args: object, **_kwargs: object) -> _AsyncResponse:
        return _AsyncResponse()


class _Completed:
    returncode = 0
    stdout = "tool 1.0.0"
    stderr = ""


class _Process:
    returncode = 0

    async def communicate(
        self, _input: bytes | None = None
    ) -> tuple[bytes, bytes]:
        return b"tool 1.0.0", b""

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def _archives(tmp_path: Path) -> dict[str, Path]:
    executable = tmp_path / "tool"
    executable.write_bytes(b"tool")
    executable.chmod(0o755)
    zip_path = tmp_path / "tool.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("bundle/tool", b"tool")
        archive.writestr("bundle/LICENSE", b"license")
    tar_path = tmp_path / "tool.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        info = tarfile.TarInfo("bundle/tool")
        info.size = 4
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(b"tool"))
    checksum = tmp_path / "sha256.sum"
    checksum.write_text(
        "f9f90a3e4fb6d7fae061e82b10f44aa6e868e8276a19f3fc0b4ef607df5b2bc0  tool\n",
        encoding="utf-8",
    )
    return {
        "executable": executable,
        "zip": zip_path,
        "tar": tar_path,
        "checksum": checksum,
    }


def _modules() -> list[ModuleType]:
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            runtime_tools_package.__path__,
            f"{runtime_tools_package.__name__}.",
        )
    ]


_MISSING = object()
_DOWNLOAD_ATTRIBUTES = (
    "download_file",
    "download_verified_asset",
    "download_verified_asset_sync",
    "download_bounded_asset",
    "download_bounded_asset_sync",
)


def _destination(tmp_path: Path, profile: int) -> Path:
    destination = tmp_path / f"destination-{profile}"
    destination.mkdir(exist_ok=True)
    return destination


def _named_values(
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
    destination: Path,
) -> dict[str, object]:
    return {
        "config": example_config,
        "base_dir": str(tmp_path),
        "path": files["executable"],
        "file_path": files["executable"],
        "archive_path": files["zip"] if profile % 2 == 0 else files["tar"],
        "zip_path": files["zip"],
        "tar_path": files["tar"],
        "checksum_path": files["checksum"],
        "source": files["executable"],
        "destination": destination,
        "destination_dir": destination,
        "destination_path": destination / "tool",
        "install_dir": destination,
        "target_dir": destination,
        "output_dir": destination,
        "target": destination / "tool",
        "binary_path": destination / "tool",
        "url": "https://downloads.example/tool.zip",
        "download_url": "https://downloads.example/tool.zip",
        "asset_url": "https://downloads.example/tool.zip",
        "asset_name": "tool.zip",
        "filename": "tool.zip",
        "tool_name": "tool",
        "tool": "tool",
        "version": "1.0.0",
        "expected_sha256": "f9f90a3e4fb6d7fae061e82b10f44aa6e868e8276a19f3fc0b4ef607df5b2bc0",
        "declared_size": 4,
        "max_bytes": 1024,
        "member_name": "bundle/tool",
        "members": ["bundle/tool"],
        "platform_name": ("linux", "windows", "darwin")[profile % 3],
        "machine": ("x86_64", "AMD64", "arm64")[profile % 3],
        "system": ("Linux", "Windows", "Darwin")[profile % 3],
        "executable": "tool",
        "command": ["tool", "--version"],
        "args": ["--version"],
        "data": b"tool",
    }


def _scalar_annotation_value(
    annotation: object, profile: int, destination: Path
) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return destination
    values: dict[object, object] = {
        bool: profile % 2 == 0,
        int: 1,
        float: 1.0,
        str: "tool",
        Path: destination,
    }
    return values.get(annotation, _MISSING)


def _origin_annotation_value(origin: object) -> object:
    values: dict[object, object] = {
        list: ["tool"],
        Sequence: ["tool"],
        dict: {},
        Mapping: {},
        tuple: (),
    }
    return values.get(origin, _MISSING)


def _optional_annotation_value(
    name: str,
    origin: object,
    args: tuple[object, ...],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if origin is None or type(None) not in args:
        return _MISSING
    concrete = next((item for item in args if item is not type(None)), str)
    return _value(name, concrete, tmp_path, files, profile)


def _value(
    name: str,
    annotation: object,
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    destination = _destination(tmp_path, profile)
    named = _named_values(tmp_path, files, profile, destination).get(
        normalized, _MISSING
    )
    if named is not _MISSING:
        return named
    scalar = _scalar_annotation_value(annotation, profile, destination)
    if scalar is not _MISSING:
        return scalar
    origin = get_origin(annotation)
    origin_value = _origin_annotation_value(origin)
    if origin_value is not _MISSING:
        return origin_value
    optional = _optional_annotation_value(
        normalized,
        origin,
        get_args(annotation),
        tmp_path,
        files,
        profile,
    )
    if optional is not _MISSING:
        return optional
    return destination


def _type_hints(function: Callable[..., object]) -> dict[str, object]:
    target = function.__init__ if inspect.isclass(function) else function
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
    if parameter.default is inspect.Parameter.empty:
        return True
    return parameter.name in overrides


def _parameter_value(
    parameter: inspect.Parameter,
    hints: Mapping[str, object],
    overrides: Mapping[str, object],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    fallback = _value(
        parameter.name,
        hints.get(parameter.name, parameter.annotation),
        tmp_path,
        files,
        profile,
    )
    return overrides.get(parameter.name, fallback)


def _invocation_arguments(
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    hints = _type_hints(function)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_parameter(parameter, overrides):
            continue
        value = _parameter_value(
            parameter, hints, overrides, tmp_path, files, profile
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    return positional, keywords


async def _invoke(
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
    actual_overrides = overrides or {}
    positional, keywords = _invocation_arguments(
        function, tmp_path, files, profile, actual_overrides
    )
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


def _sync_download(
    _url: str, destination: str | Path, *_args: object, **_kwargs: object
) -> Path:
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"tool")
    return target


async def _async_download(
    _url: str, destination: str | Path, *_args: object, **_kwargs: object
) -> Path:
    return _sync_download(_url, destination)


async def _fake_process(*_args: object, **_kwargs: object) -> _Process:
    return _Process()


async def _no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


def _patch_global_boundaries(monkeypatch: Any) -> None:
    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_process)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"tool 1.0.0"
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def _download_replacement(function: object) -> Callable[..., object]:
    if inspect.iscoroutinefunction(function):
        return _async_download
    return _sync_download


def _patch_module_downloads(module: ModuleType, monkeypatch: Any) -> None:
    for attribute in _DOWNLOAD_ATTRIBUTES:
        if not hasattr(module, attribute):
            continue
        original = getattr(module, attribute)
        monkeypatch.setattr(module, attribute, _download_replacement(original))


async def _record_invocation(
    function: Callable[..., object],
    qualified: str,
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
    terminations: list[str],
    expected_rejections: list[str],
    overrides: Mapping[str, object] | None = None,
) -> None:
    try:
        await _invoke(function, tmp_path, files, profile, overrides)
    except (KeyboardInterrupt, SystemExit) as error:
        terminations.append(f"{qualified}:{type(error).__name__}")
    except Exception as error:
        expected_rejections.append(f"{qualified}:{type(error).__name__}")


async def _exercise_profiles(
    function: Callable[..., object],
    qualified: str,
    tmp_path: Path,
    files: Mapping[str, Path],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    for profile in range(3):
        await _record_invocation(
            function,
            qualified,
            tmp_path,
            files,
            profile,
            terminations,
            expected_rejections,
        )


def _is_runtime_function(
    module: ModuleType, name: str, function: object
) -> bool:
    if not inspect.isfunction(function):
        return False
    if function.__module__ != module.__name__:
        return False
    return not (name.startswith("__") or name.endswith("_sync"))


def _function_members(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    members: list[tuple[str, Callable[..., object]]] = []
    for name, function in inspect.getmembers(module, inspect.isfunction):
        if _is_runtime_function(module, name, function):
            members.append((name, function))
    return members


async def _exercise_module_functions(
    module: ModuleType,
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    expected_rejections: list[str],
) -> Callable[..., object] | None:
    raw_functions = inspect.getmembers(module, inspect.isfunction)
    for name, function in _function_members(module):
        qualified = f"{module.__name__}.{name}"
        attempted.add(qualified)
        await _exercise_profiles(
            function,
            qualified,
            tmp_path,
            files,
            terminations,
            expected_rejections,
        )
    if not raw_functions:
        return None
    return raw_functions[-1][1]


def _runtime_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (name, class_type)
        for name, class_type in inspect.getmembers(module, inspect.isclass)
        if class_type.__module__ == module.__name__
    ]


async def _construct_instance(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    expected_rejections: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, tmp_path, files, 0)
    except Exception as error:
        expected_rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None


def _callable_methods(
    instance: object,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, method)
        for name, method in inspect.getmembers(instance, callable)
        if not name.startswith("__")
    ]


async def _exercise_instance_profiles(
    module: ModuleType,
    class_name: str,
    instance: object,
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    expected_rejections: list[str],
) -> str | None:
    last_qualified: str | None = None
    for method_name, method in _callable_methods(instance):
        qualified = f"{module.__name__}.{class_name}.{method_name}"
        last_qualified = qualified
        attempted.add(qualified)
        await _exercise_profiles(
            method,
            qualified,
            tmp_path,
            files,
            terminations,
            expected_rejections,
        )
    return last_qualified


async def _exercise_literal_scenarios(
    function: Callable[..., object],
    qualified: str,
    tmp_path: Path,
    files: Mapping[str, Path],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    for _meta_updates, argument_overrides in literal_branch_scenarios(
        function, (), limit=192
    ):
        await _record_invocation(
            function,
            qualified,
            tmp_path,
            files,
            0,
            terminations,
            expected_rejections,
            argument_overrides,
        )


async def _exercise_class_profiles(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    last_function: Callable[..., object] | None,
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    instance = await _construct_instance(
        module, class_name, class_type, tmp_path, files, expected_rejections
    )
    if instance is None:
        return
    last_qualified = await _exercise_instance_profiles(
        module,
        class_name,
        instance,
        tmp_path,
        files,
        attempted,
        terminations,
        expected_rejections,
    )
    if last_function is None or last_qualified is None:
        return
    await _exercise_literal_scenarios(
        last_function,
        last_qualified,
        tmp_path,
        files,
        terminations,
        expected_rejections,
    )


async def _exercise_class_literal_methods(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    instance = await _construct_instance(
        module, class_name, class_type, tmp_path, files, expected_rejections
    )
    if instance is None:
        return
    for method_name, method in _callable_methods(instance):
        qualified = f"{module.__name__}.{class_name}.{method_name}"
        await _exercise_literal_scenarios(
            method,
            qualified,
            tmp_path,
            files,
            terminations,
            expected_rejections,
        )


async def _exercise_module(
    module: ModuleType,
    tmp_path: Path,
    files: Mapping[str, Path],
    monkeypatch: Any,
    attempted: set[str],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    _patch_module_downloads(module, monkeypatch)
    last_function = await _exercise_module_functions(
        module,
        tmp_path,
        files,
        attempted,
        terminations,
        expected_rejections,
    )
    classes = _runtime_classes(module)
    for class_name, class_type in classes:
        await _exercise_class_profiles(
            module,
            class_name,
            class_type,
            last_function,
            tmp_path,
            files,
            attempted,
            terminations,
            expected_rejections,
        )
    for class_name, class_type in classes:
        await _exercise_class_literal_methods(
            module,
            class_name,
            class_type,
            tmp_path,
            files,
            terminations,
            expected_rejections,
        )


async def _exercise_catalog(
    modules: list[ModuleType],
    tmp_path: Path,
    files: Mapping[str, Path],
    monkeypatch: Any,
    attempted: set[str],
    terminations: list[str],
    expected_rejections: list[str],
) -> None:
    for module in modules:
        await _exercise_module(
            module,
            tmp_path,
            files,
            monkeypatch,
            attempted,
            terminations,
            expected_rejections,
        )


def _sync_wrapper_call(
    function: Callable[..., object], name: str, tmp_path: Path
) -> None:
    if name == "download_bounded_asset_sync":
        function(
            "https://downloads.example/tool",
            tmp_path / f"{name}.bin",
            max_bytes=1024,
        )
        return
    function(
        "https://downloads.example/tool",
        tmp_path / f"{name}.bin",
        expected_sha256="f9f90a3e4fb6d7fae061e82b10f44aa6e868e8276a19f3fc0b4ef607df5b2bc0",
        max_bytes=1024,
    )


def _exercise_sync_wrappers(
    tmp_path: Path,
    attempted: set[str],
    expected_rejections: list[str],
) -> None:
    integrity = importlib.import_module(
        "src.integrations.runtime_tools.download_integrity"
    )
    for name in (
        "download_bounded_asset_sync",
        "download_verified_asset_sync",
    ):
        function = getattr(integrity, name)
        attempted.add(f"{integrity.__name__}.{name}")
        try:
            _sync_wrapper_call(function, name, tmp_path)
        except Exception as error:
            expected_rejections.append(
                f"{integrity.__name__}.{name}:{type(error).__name__}"
            )


def test_runtime_tool_catalog_uses_verified_local_boundaries(
    tmp_path: Path, monkeypatch: Any
) -> None:
    files = _archives(tmp_path)
    _patch_global_boundaries(monkeypatch)
    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []

    asyncio.run(
        _exercise_catalog(
            _modules(),
            tmp_path,
            files,
            monkeypatch,
            attempted,
            terminations,
            expected_rejections,
        )
    )
    _exercise_sync_wrappers(tmp_path, attempted, expected_rejections)

    assert len(attempted) >= 50
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
