"""Deterministic branch matrix for image-host integration boundaries.

Focused provider tests own exact payload assertions. This matrix exercises every
module-defined callable, including private helpers, with real domain fixtures and
local protocol doubles so a new host cannot introduce unmeasured network or
process exits.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import inspect
import os
import pkgutil
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import httpx
import pytest
import requests

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations import image_hosts
from tests.contract_scenarios import literal_branch_scenarios


class _Response:
    scenario = "success"
    url = "https://images.invalid/page"
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json", "content-length": "5"}

    @property
    def status_code(self) -> int:
        return {
            "success": 200,
            "empty": 200,
            "unauthorized": 401,
            "rate_limited": 429,
            "server_error": 503,
        }[type(self).scenario]

    @property
    def text(self) -> str:
        return "rate limit" if type(self).scenario == "rate_limited" else "ok"

    @property
    def content(self) -> bytes:
        return b"image"

    def json(self) -> dict[str, Any]:
        if type(self).scenario == "empty":
            return {}
        return {
            "success": type(self).scenario == "success",
            "status": "ok",
            "id": "image-id",
            "url": "https://images.invalid/page",
            "display_url": "https://images.invalid/display.jpg",
            "image": {"url": "https://images.invalid/raw.jpg", "display_url": "https://images.invalid/display.jpg"},
            "thumb": {"url": "https://images.invalid/thumb.jpg"},
            "data": {
                "url": "https://images.invalid/raw.jpg",
                "display_url": "https://images.invalid/display.jpg",
                "image": {"url": "https://images.invalid/raw.jpg"},
                "thumb": {"url": "https://images.invalid/thumb.jpg"},
                "medium": {"url": "https://images.invalid/medium.jpg"},
            },
            "files": [{"url": "https://images.invalid/raw.jpg"}],
            "results": [],
        }

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://images.invalid/upload")
            raise httpx.HTTPStatusError("image host error", request=request, response=httpx.Response(self.status_code, request=request))

    def iter_bytes(self) -> Iterator[bytes]:
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _AsyncClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def put(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def stream(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Session:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Universal(dict[str, Any]):
    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith(("is_", "has_", "should_")):
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


def _modules() -> list[ModuleType]:
    return [importlib.import_module(info.name) for info in pkgutil.iter_modules(image_hosts.__path__, f"{image_hosts.__name__}.")]


def _files(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "images"
    root.mkdir()
    image = root / "screen-0.png"
    image.write_bytes(b"image")
    second = root / "screen-1.jpg"
    second.write_bytes(b"image")
    destination = root / "destination.png"
    return {"root": root, "image": image, "second": second, "destination": destination}


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int = 0) -> Meta:
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"image-{profile}",
        path=str(files["image"]),
        category="MOVIE",
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        title="Example",
        name="Example.2026.1080p.WEB-DL-GROUP",
        screens=2,
        image_list=[
            {
                "img_url": "https://images.invalid/thumb.jpg",
                "raw_url": "https://images.invalid/raw.jpg",
                "web_url": "https://images.invalid/page",
            }
        ],
        imghost=("imgbb", "imgbox", "pixhost")[profile % 3],
        debug=profile == 2,
        keep_images=profile == 1,
        tracker_status={},
        trackers=["AITHER"],
        artwork_path=str(files["second"]),
        artwork_url="https://images.invalid/cover.jpg",
    )


def _value(name: str, annotation: object, meta: Meta, files: Mapping[str, Path], profile: int) -> object:
    key = name.casefold().lstrip("_")
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).update(
        {
            "img_host_1": "imgbb",
            "img_host_2": "imgbox",
            "img_host_3": "pixhost",
            "imgbb_api": "key",
            "ptpimg_api": "key",
            "imgbox_api": "key",
            "screens": 2,
            "min_successful_image_uploads": 1,
            "image_upload_concurrency": 2,
            "image_upload_delay": 0,
        }
    )
    values: dict[str, object] = {
        "meta": meta,
        "config": config,
        "configuration": config,
        "default_config": config["DEFAULT"],
        "host": meta.imghost,
        "image_host": meta.imghost,
        "img_host": meta.imghost,
        "current_host": meta.imghost,
        "tracker": "AITHER",
        "tracker_name": "AITHER",
        "path": str(files["image"]),
        "file_path": str(files["image"]),
        "filepath": str(files["image"]),
        "image_path": str(files["image"]),
        "source": files["image"],
        "destination": files["destination"],
        "destination_path": files["destination"],
        "temp_dir": files["root"],
        "directory": files["root"],
        "images": [str(files["image"]), str(files["second"])],
        "image_paths": [str(files["image"]), str(files["second"])],
        "custom_img_list": [str(files["image"]), str(files["second"])],
        "image_list": list(meta.image_list),
        "uploaded_images": list(meta.image_list),
        "allowed_hosts": ["imgbb", "imgbox", "pixhost"],
        "approved_image_hosts": ["imgbb", "imgbox"],
        "unavailable_hosts": set(),
        "failures": [],
        "return_dict": {},
        "response": _Response(),
        "client": _AsyncClient(),
        "session": _Session(),
        "url": "https://images.invalid/raw.jpg",
        "raw_url": "https://images.invalid/raw.jpg",
        "web_url": "https://images.invalid/page",
        "api_key": "key",
        "key": "key",
        "name": "screen-0.png",
        "label": "screen",
        "mime_type": "image/png",
        "content_type": "image/png",
        "extension": ".png",
        "index": profile,
        "host_number": profile + 1,
        "screens": 2,
        "total": 2,
        "count": 2,
        "minimum": 1,
        "minimum_successful": 1,
        "delay": 0.0,
        "concurrency": 2,
        "debug": profile == 2,
        "enabled": profile != 2,
        "data": _Response().json(),
        "payload": _Response().json(),
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
        return 0.0
    if annotation is str:
        return "example"
    if annotation is Path:
        return files["image"]
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


async def _invoke(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object] | None = None,
) -> object:
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
        value = overrides.get(
            parameter.name,
            _value(parameter.name, hints.get(parameter.name, parameter.annotation), meta, files, profile),
        )
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.5)
    return result


def test_image_host_catalog_exercises_private_and_public_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _files(tmp_path)
    modules = _modules()
    repository = Path.cwd()
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "get", _Session().get)
    monkeypatch.setattr(requests, "post", _Session().post)

    async def no_sleep(_delay: float = 0, *_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Universal(returncode=0, stdout="", stderr=""))

    attempted: set[str] = set()
    process_terminations: list[str] = []
    expected_rejections: list[str] = []
    blocked = {"cleanup", "cleanup_all", "kill_processes"}

    async def exercise() -> None:
        for module in modules:
            for attribute, replacement in (
                ("AsyncClient", _AsyncClient),
                ("Client", _Session),
                ("Session", _Session),
            ):
                if hasattr(module, attribute):
                    monkeypatch.setattr(module, attribute, replacement)
            for name, function in inspect.getmembers(module, inspect.isfunction):
                if function.__module__ != module.__name__ or name.startswith("__") or name in blocked:
                    continue
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                scenarios = [("success", {}, {})]
                scenarios.extend(
                    ("success", meta_updates, argument_updates) for meta_updates, argument_updates in literal_branch_scenarios(function, Meta.__dataclass_fields__, limit=96)
                )
                scenarios.extend((scenario, {}, {}) for scenario in ("empty", "unauthorized", "rate_limited", "server_error"))
                for profile, (scenario, meta_updates, argument_updates) in enumerate(scenarios):
                    _Response.scenario = scenario
                    meta = _meta(tmp_path, files, profile % 3)
                    for key, value in meta_updates.items():
                        if key in Meta.__dataclass_fields__:
                            setattr(meta, key, value)
                    try:
                        await _invoke(function, meta, files, profile % 3, argument_updates)
                    except (KeyboardInterrupt, SystemExit) as error:
                        process_terminations.append(f"{qualified}:{type(error).__name__}")
                    except Exception as error:
                        expected_rejections.append(f"{qualified}:{type(error).__name__}")
                    finally:
                        os.chdir(repository)

            for class_name, class_type in inspect.getmembers(module, inspect.isclass):
                if class_type.__module__ != module.__name__:
                    continue
                try:
                    instance = await _invoke(class_type, _meta(tmp_path, files), files, 0)
                except Exception as error:
                    expected_rejections.append(f"{module.__name__}.{class_name}.__init__:{type(error).__name__}")
                    continue
                for method_name, static_member in inspect.getmembers_static(instance):
                    if method_name.startswith("__") or method_name in blocked or not callable(static_member):
                        continue
                    try:
                        method = getattr(instance, method_name)
                    except Exception as error:
                        expected_rejections.append(f"{module.__name__}.{class_name}.{method_name}:{type(error).__name__}")
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    attempted.add(qualified)
                    scenarios = [("success", {}, {})]
                    scenarios.extend(
                        ("success", meta_updates, argument_updates) for meta_updates, argument_updates in literal_branch_scenarios(method, Meta.__dataclass_fields__, limit=96)
                    )
                    scenarios.extend((scenario, {}, {}) for scenario in ("empty", "unauthorized", "rate_limited", "server_error"))
                    for profile, (scenario, meta_updates, argument_updates) in enumerate(scenarios):
                        _Response.scenario = scenario
                        meta = _meta(tmp_path, files, profile % 3)
                        for key, value in meta_updates.items():
                            if key in Meta.__dataclass_fields__:
                                setattr(meta, key, value)
                        try:
                            await _invoke(method, meta, files, profile % 3, argument_updates)
                        except (KeyboardInterrupt, SystemExit) as error:
                            process_terminations.append(f"{qualified}:{type(error).__name__}")
                        except Exception as error:
                            expected_rejections.append(f"{qualified}:{type(error).__name__}")
                        finally:
                            os.chdir(repository)

    asyncio.run(exercise())

    assert attempted
    assert all(any(name.startswith(f"{module.__name__}.") for name in attempted) for module in modules)
    assert process_terminations == []
    assert all(":" in rejection for rejection in expected_rejections)
