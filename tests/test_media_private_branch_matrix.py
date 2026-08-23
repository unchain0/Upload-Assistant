"""Deterministic private-helper matrix for media integration modules."""

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
from types import ModuleType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import httpx
import pytest

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations import media
from tests.contract_scenarios import literal_branch_scenarios


class _Response:
    scenario = "success"
    status_code = 200
    text = "ok"
    content = b"media"
    headers: ClassVar[dict[str, str]] = {
        "content-type": "application/octet-stream",
        "content-length": "5",
    }

    def json(self) -> dict[str, Any]:
        return {"success": True, "data": {}, "results": [], "id": 1}

    def raise_for_status(self) -> None:
        return None

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

    async def request(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def aclose(self) -> None:
        return None


class _Stream:
    def __init__(self) -> None:
        self.read_done = False

    async def read(self, _size: int = -1) -> bytes:
        if self.read_done:
            return b""
        self.read_done = True
        return b"ok"

    async def readline(self) -> bytes:
        if self.read_done:
            return b""
        self.read_done = True
        return b"ok\n"

    def at_eof(self) -> bool:
        return self.read_done


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
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        self.returncode = -9

    def send_signal(self, _signal: int) -> None:
        return None


class _Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


class _Track:
    track_type = "General"
    format = "Matroska"
    duration = 120000
    width = 1920
    height = 1080
    frame_rate = "24.000"
    bit_rate = "8000000"
    language = "English"

    def to_data(self) -> dict[str, Any]:
        return {
            "@type": self.track_type,
            "Format": self.format,
            "Duration": self.duration,
            "Width": self.width,
            "Height": self.height,
            "FrameRate": self.frame_rate,
            "BitRate": self.bit_rate,
        }


class _MediaInfo:
    tracks: ClassVar[list[_Track]] = [_Track()]

    @classmethod
    def parse(
        cls, *_args: object, output: str | None = None, **_kwargs: object
    ) -> Any:
        if output:
            return "General\nFormat : Matroska"
        return cls()

    def to_data(self) -> dict[str, Any]:
        return {"media": {"track": [track.to_data() for track in self.tracks]}}


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
    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(media.__path__, f"{media.__name__}."):
        try:
            modules.append(importlib.import_module(info.name))
        except ModuleNotFoundError as error:
            if info.name.endswith(".vapoursynth") and error.name in {
                "awsmfunc",
                "vapoursynth",
            }:
                continue
            raise
    return modules


def _files(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "media"
    root.mkdir()
    video = root / "Example.2026.1080p.WEB-DL-GROUP.mkv"
    video.write_bytes(b"media")
    audio = root / "track.flac"
    audio.write_bytes(b"audio")
    image = root / "screen.png"
    image.write_bytes(b"image")
    nfo = root / "release.nfo"
    nfo.write_text("Example NFO", encoding="utf-8")
    bdmv = root / "BDMV"
    (bdmv / "STREAM").mkdir(parents=True)
    (bdmv / "PLAYLIST").mkdir()
    (bdmv / "STREAM" / "00001.m2ts").write_bytes(b"disc")
    (bdmv / "PLAYLIST" / "00001.mpls").write_bytes(b"playlist")
    video_ts = root / "VIDEO_TS"
    video_ts.mkdir()
    (video_ts / "VTS_01_1.VOB").write_bytes(b"dvd")
    return {
        "root": root,
        "video": video,
        "audio": audio,
        "image": image,
        "nfo": nfo,
        "bdmv": bdmv,
        "video_ts": video_ts,
    }


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int = 0) -> Meta:
    categories = ("MOVIE", "TV", "MUSIC", "BOOK", "GAME", "XXX")
    discs = ("", "BDMV", "DVD", "HDDVD", "", "")
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"media-{profile}",
        path=str(files["video"]),
        filename=files["video"].name,
        filelist=[str(files["video"])],
        category=categories[profile % len(categories)],
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        title="Example",
        name="Example.2026.1080p.WEB-DL-GROUP",
        year=2026,
        season=1,
        episode=2,
        season_int=1,
        episode_int=2,
        is_disc=discs[profile % len(discs)],
        bdinfo={
            "title": "Example",
            "playlist": "00001.MPLS",
            "video": [],
            "audio": [],
            "subtitles": [],
        },
        discs=[
            {
                "path": str(files["root"]),
                "main_set": ["01"],
                "largest_evo": str(files["video"]),
            }
        ],
        mediainfo={"media": {"track": [_Track().to_data()]}},
        screens=2,
        image_list=[],
        debug=profile == 5,
        edit=profile == 4,
        unattended=True,
        retake=False,
        manual_frames=[1, 2],
    )


def _config() -> dict[str, Any]:
    config = copy.deepcopy(example_config)
    config.setdefault("DEFAULT", {}).update(
        {
            "screens": 2,
            "multiScreens": 2,
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
            "vapoursynth": False,
            "image_upload_delay": 0,
            "get_bluray_info": False,
        }
    )
    return config


_MISSING = object()
_BLOCKED_MEDIA_HELPERS = frozenset(
    {
        "cleanup",
        "cleanup_all",
        "kill_processes",
        "_kill_process",
        "_terminate_posix_group",
        "_terminate_process",
        "_terminate_windows_process",
        "_terminate_process_tree",
        "_wait_taskkill",
        "_wait_terminated_process",
    }
)


def _named_values(
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> dict[str, object]:
    return {
        "meta": meta,
        "config": _config(),
        "configuration": _config(),
        "base_dir": str(meta.base_dir),
        "path": str(files["video"]),
        "file_path": str(files["video"]),
        "filepath": str(files["video"]),
        "video": str(files["video"]),
        "videopath": str(files["video"]),
        "video_path": str(files["video"]),
        "audio_path": str(files["audio"]),
        "image_path": str(files["image"]),
        "output": str(files["root"] / "output.txt"),
        "output_path": str(files["root"] / "output.txt"),
        "destination": files["root"] / "destination.bin",
        "directory": files["root"],
        "folder": files["root"],
        "folder_id": meta.uuid,
        "uuid": meta.uuid,
        "disc_path": str(files["root"]),
        "bdmv_path": str(files["bdmv"]),
        "dvd_path": str(files["video_ts"]),
        "hddvd_path": str(files["root"]),
        "playlist": "00001.MPLS",
        "playlist_file": files["bdmv"] / "PLAYLIST" / "00001.mpls",
        "stream_file": files["bdmv"] / "STREAM" / "00001.m2ts",
        "files": [str(files["video"])],
        "filelist": [str(files["video"])],
        "frames": [1, 2],
        "timestamps": [1, 2],
        "screens": 2,
        "num_screens": 2,
        "frame": 1,
        "frame_number": 1,
        "index": profile,
        "width": 1920,
        "height": 1080,
        "duration": 120.0,
        "fps": 24.0,
        "frame_rate": 24.0,
        "resolution": "1080p",
        "codec": "H.264",
        "command": ["ffmpeg", "-version"],
        "cmd": ["ffmpeg", "-version"],
        "args": ["ffmpeg", "-version"],
        "process": _Process(),
        "media_info": _MediaInfo(),
        "mediainfo": _MediaInfo().to_data(),
        "track": _Track(),
        "data": {"media": {"track": [_Track().to_data()]}},
        "bdinfo": dict(meta.bdinfo),
        "disc": {"path": str(files["root"]), "main_set": ["01"]},
        "discs": list(meta.discs),
        "text": "Example",
        "value": "Example",
        "name": "Example",
        "language": "English",
        "debug": profile == 5,
        "enabled": True,
        "client": _AsyncClient(),
    }


def _primitive_value(
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return _Universal()
    if annotation is Path:
        return files["video"]
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
    primitive = _primitive_value(annotation, meta, files, profile)
    if primitive is not _MISSING:
        return primitive
    return _composite_value(key, annotation, meta, files, profile)


def _safe_type_hints(target: object) -> dict[str, Any]:
    try:
        return get_type_hints(target)
    except (NameError, TypeError):
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
        literal_branch_scenarios(
            function, Meta.__dataclass_fields__, limit=96
        )
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
    meta = _meta(tmp_path, files, profile % 6)
    _apply_meta_updates(meta, meta_updates)
    try:
        await _invoke(
            function,
            meta,
            files,
            profile % 6,
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


def _patch_media_module(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attribute, replacement in (
        ("AsyncClient", _AsyncClient),
        ("MediaInfo", _MediaInfo),
    ):
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, replacement)
    if hasattr(module, "create_subprocess_exec"):
        monkeypatch.setattr(module, "create_subprocess_exec", _process_double)


def _module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__
        and not name.startswith("__")
        and name not in _BLOCKED_MEDIA_HELPERS
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
        return await _invoke(
            class_type, _meta(tmp_path, files), files, 0
        )
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
    if method_name in _BLOCKED_MEDIA_HELPERS or not callable(member):
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
    _patch_media_module(module, monkeypatch)
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


def test_media_private_helpers_execute_with_boundary_doubles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _files(tmp_path)
    modules = _modules()
    repository = Path.cwd()
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
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
    assert attempted
    assert all(
        any(name.startswith(f"{module.__name__}.") for name in attempted)
        for module in modules
    )
    assert terminations == []
    assert all(":" in rejection for rejection in rejections)
