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


def _value(
    name: str,
    annotation: object,
    tmp_path: Path,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    destination = tmp_path / f"destination-{profile}"
    destination.mkdir(exist_ok=True)
    values: dict[str, object] = {
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
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return destination
    if annotation is bool:
        return profile % 2 == 0
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "tool"
    if annotation is Path:
        return destination
    if origin in {list, Sequence}:
        return ["tool"]
    if origin in {dict, Mapping}:
        return {}
    if origin is tuple:
        return ()
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, tmp_path, files, profile)
    return destination


async def _invoke(
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
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
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if (
            parameter.default is not inspect.Parameter.empty
            and parameter.name not in overrides
        ):
            continue
        value = overrides.get(
            parameter.name,
            _value(
                parameter.name,
                hints.get(parameter.name, parameter.annotation),
                tmp_path,
                files,
                profile,
            ),
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


def test_runtime_tool_catalog_uses_verified_local_boundaries(
    tmp_path: Path, monkeypatch: Any
) -> None:
    files = _archives(tmp_path)

    def sync_download(
        _url: str, destination: str | Path, *_args: object, **_kwargs: object
    ) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"tool")
        return target

    async def async_download(
        _url: str, destination: str | Path, *_args: object, **_kwargs: object
    ) -> Path:
        return sync_download(_url, destination)

    async def fake_process(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    async def no_sleep(
        _delay: float = 0, *_args: object, **_kwargs: object
    ) -> None:
        return None

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_process)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"tool 1.0.0"
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")

    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []

    async def exercise() -> None:
        for module in _modules():
            for attribute in (
                "download_file",
                "download_verified_asset",
                "download_verified_asset_sync",
                "download_bounded_asset",
                "download_bounded_asset_sync",
            ):
                if hasattr(module, attribute):
                    replacement = (
                        async_download
                        if inspect.iscoroutinefunction(
                            getattr(module, attribute)
                        )
                        else sync_download
                    )
                    monkeypatch.setattr(module, attribute, replacement)
            for name, function in inspect.getmembers(
                module, inspect.isfunction
            ):
                if (
                    function.__module__ != module.__name__
                    or name.startswith("__")
                    or name.endswith("_sync")
                ):
                    continue
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                for profile in range(3):
                    try:
                        await _invoke(function, tmp_path, files, profile)
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    except Exception as error:
                        expected_rejections.append(
                            f"{qualified}:{type(error).__name__}"
                        )

            for class_name, class_type in inspect.getmembers(
                module, inspect.isclass
            ):
                if class_type.__module__ != module.__name__:
                    continue
                try:
                    instance = await _invoke(class_type, tmp_path, files, 0)
                except Exception as error:
                    expected_rejections.append(
                        f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
                    )
                    continue
                for method_name, method in inspect.getmembers(
                    instance, callable
                ):
                    if method_name.startswith("__"):
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    attempted.add(qualified)
                    for profile in range(3):
                        try:
                            await _invoke(method, tmp_path, files, profile)
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        except Exception as error:
                            expected_rejections.append(
                                f"{qualified}:{type(error).__name__}"
                            )

                for (
                    _meta_updates,
                    argument_overrides,
                ) in literal_branch_scenarios(function, (), limit=192):
                    try:
                        await _invoke(
                            function, tmp_path, files, 0, argument_overrides
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    except Exception as error:
                        expected_rejections.append(
                            f"{qualified}:{type(error).__name__}"
                        )

            for class_name, class_type in inspect.getmembers(
                module, inspect.isclass
            ):
                if class_type.__module__ != module.__name__:
                    continue
                try:
                    instance = await _invoke(class_type, tmp_path, files, 0)
                except Exception as error:
                    expected_rejections.append(
                        f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
                    )
                    continue
                for method_name, method in inspect.getmembers(
                    instance, callable
                ):
                    if method_name.startswith("__"):
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    for (
                        _meta_updates,
                        argument_overrides,
                    ) in literal_branch_scenarios(method, (), limit=192):
                        try:
                            await _invoke(
                                method, tmp_path, files, 0, argument_overrides
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        except Exception as error:
                            expected_rejections.append(
                                f"{qualified}:{type(error).__name__}"
                            )

    asyncio.run(exercise())

    # Sync wrappers intentionally own an event loop and must be exercised from
    # normal synchronous test context, not from inside ``exercise``.
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
            if name == "download_bounded_asset_sync":
                function(
                    "https://downloads.example/tool",
                    tmp_path / f"{name}.bin",
                    max_bytes=1024,
                )
            else:
                function(
                    "https://downloads.example/tool",
                    tmp_path / f"{name}.bin",
                    expected_sha256="f9f90a3e4fb6d7fae061e82b10f44aa6e868e8276a19f3fc0b4ef607df5b2bc0",
                    max_bytes=1024,
                )
        except Exception as error:
            expected_rejections.append(
                f"{integrity.__name__}.{name}:{type(error).__name__}"
            )

    assert len(attempted) >= 50
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
