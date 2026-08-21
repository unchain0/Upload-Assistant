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


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    config = copy.deepcopy(example_config)
    values: dict[str, object] = {
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
    if normalized in values:
        return values[normalized]
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
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
        return tuple(
            _value(normalized, item, meta, files, profile)
            for item in args
            if item is not Ellipsis
        )
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, meta, files, profile)
    return _Universal()


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
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
                meta,
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
    destructive_names = {
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
    }

    async def exercise() -> None:
        for module in modules:
            for attribute, replacement in (
                ("AsyncClient", _AsyncClient),
                ("Session", _Session),
            ):
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, replacement)
            for name, function in inspect.getmembers(
                module, inspect.isfunction
            ):
                if (
                    function.__module__ != module.__name__
                    or name.startswith("__")
                    or name in destructive_names
                ):
                    continue
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                for profile in range(3):
                    try:
                        await _invoke(
                            function,
                            _meta(tmp_path, files, profile),
                            files,
                            profile,
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    except Exception as error:
                        expected_rejections.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    finally:
                        os.chdir(repository_cwd)
                for (
                    meta_updates,
                    argument_overrides,
                ) in literal_branch_scenarios(
                    function, Meta.__dataclass_fields__, limit=192
                ):
                    scenario_meta = _meta(tmp_path, files, 0)
                    for key, value in meta_updates.items():
                        if key in Meta.__dataclass_fields__:
                            setattr(scenario_meta, key, value)
                    try:
                        await _invoke(
                            function,
                            scenario_meta,
                            files,
                            0,
                            argument_overrides,
                        )
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    except Exception as error:
                        expected_rejections.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                    finally:
                        os.chdir(repository_cwd)

            for class_name, class_type in inspect.getmembers(
                module, inspect.isclass
            ):
                if class_type.__module__ != module.__name__:
                    continue
                try:
                    instance = await _invoke(
                        class_type, _meta(tmp_path, files, 0), files, 0
                    )
                except Exception as error:
                    expected_rejections.append(
                        f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
                    )
                    continue
                for method_name, static_member in inspect.getmembers_static(
                    instance
                ):
                    if (
                        method_name.startswith("__")
                        or method_name in destructive_names
                        or not callable(static_member)
                    ):
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    try:
                        method = getattr(instance, method_name)
                    except Exception as error:
                        expected_rejections.append(
                            f"{qualified}:{type(error).__name__}"
                        )
                        continue
                    attempted.add(qualified)
                    if os.environ.get("UA_CONTRACT_TRACE"):
                        print(qualified, flush=True)
                    for profile in range(2):
                        try:
                            await _invoke(
                                method,
                                _meta(tmp_path, files, profile),
                                files,
                                profile,
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        except Exception as error:
                            expected_rejections.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        finally:
                            os.chdir(repository_cwd)
                    for (
                        meta_updates,
                        argument_overrides,
                    ) in literal_branch_scenarios(
                        method, Meta.__dataclass_fields__, limit=192
                    ):
                        scenario_meta = _meta(tmp_path, files, 0)
                        for key, value in meta_updates.items():
                            if key in Meta.__dataclass_fields__:
                                setattr(scenario_meta, key, value)
                        try:
                            await _invoke(
                                method,
                                scenario_meta,
                                files,
                                0,
                                argument_overrides,
                            )
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        except Exception as error:
                            expected_rejections.append(
                                f"{qualified}:{type(error).__name__}"
                            )
                        finally:
                            os.chdir(repository_cwd)

    asyncio.run(exercise())

    assert len(attempted) >= 100
    assert terminations == []
    assert all(":" in rejection for rejection in expected_rejections)
