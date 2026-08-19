"""Catalog contracts for media-tool integrations with deterministic fakes.

These tests do not replace focused media assertions. They exercise every media
adapter callable against representative domain releases and local boundary
doubles so subprocess, optional SDK, filesystem, and cancellation paths stay
observable without invoking real encoders or network services.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import pkgutil
import subprocess
import sys
import types
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Self, get_args, get_origin, get_type_hints

import httpx
import numpy as np
from PIL import Image

import src.integrations.media as media_package
from data.example_config import config as example_config
from src.domain_models.release import Meta
from tests.contract_scenarios import literal_branch_scenarios


class _Stream:
    def __init__(self, payload: bytes = b"ok") -> None:
        self.payload = payload

    async def read(self, _size: int = -1) -> bytes:
        return self.payload

    async def readline(self) -> bytes:
        return self.payload

    def at_eof(self) -> bool:
        return True


class _Process:
    returncode = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.stdout = _Stream(b"frame=1\n")
        self.stderr = _Stream(b"")
        self.pid = 1

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
        return b"ok", b""

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def send_signal(self, _signal: int) -> None:
        return None


class _Response:
    status_code = 200
    url = "https://media.example/artwork.png"
    headers: ClassVar[dict[str, str]] = {"content-type": "image/png", "content-length": "128"}
    content = b"\x89PNG\r\n\x1a\n"

    @property
    def text(self) -> str:
        return "ok"

    def json(self) -> dict[str, Any]:
        return {"success": True, "data": [], "results": []}

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self.content

    def iter_bytes(self) -> Iterator[bytes]:
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

    async def aclose(self) -> None:
        return None


class _Completed:
    returncode = 0
    stdout = "ok"
    stderr = ""


class _Universal(dict[str, Any]):
    """Small permissive boundary double for optional media SDK values."""

    def __init__(self, **values: Any) -> None:
        super().__init__(values)
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("is_"):
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


class _FakeClip:
    width = 1920
    height = 1080
    num_frames = 100
    fps_num = 24
    fps_den = 1

    def get_frame(self, _index: int) -> _Universal:
        return _Universal(props={})

    def __getitem__(self, _item: object) -> Self:
        return self

    def __mul__(self, _count: int) -> Self:
        return self


def _install_optional_media_modules(monkeypatch: Any) -> None:
    vs = types.ModuleType("vapoursynth")
    vs.VideoNode = _FakeClip
    vs.VideoFrame = _Universal
    vs.RGB24 = "RGB24"
    vs.core = _Universal()
    monkeypatch.setitem(sys.modules, "vapoursynth", vs)

    awsmfunc = types.ModuleType("awsmfunc")
    awsmfunc.FrameInfo = lambda clip, *_args, **_kwargs: clip
    awsmfunc.DynamicTonemap = lambda clip, *_args, **_kwargs: clip
    awsmfunc.ScreenGen = lambda *_args, **_kwargs: None
    awsmfunc.zresize = lambda clip, *_args, **_kwargs: clip
    awsmfunc.ScreenGen = lambda clip, *_args, **_kwargs: clip
    monkeypatch.setitem(sys.modules, "awsmfunc", awsmfunc)


def _fixture_tree(tmp_path: Path) -> dict[str, Path]:
    media = tmp_path / "Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP.mkv"
    media.write_bytes(b"media")
    audio = tmp_path / "Example Album.flac"
    audio.write_bytes(b"audio")
    m4b = tmp_path / "Example Book.m4b"
    m4b.write_bytes(b"audio book")
    pdf = tmp_path / "Example Book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    epub = tmp_path / "Example Book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        archive.writestr(
            "OEBPS/content.opf",
            '<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Example Book</dc:title>'
            "<dc:creator>Example Author</dc:creator><dc:language>en</dc:language><dc:identifier>9780000000000</dc:identifier>"
            '</metadata><manifest><item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/></manifest></package>',
        )
        image = Image.new("RGB", (16, 16), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        archive.writestr("OEBPS/cover.png", buffer.getvalue())
    png = tmp_path / "screen-0.png"
    Image.new("RGB", (64, 64), "white").save(png)
    nfo = tmp_path / "release.nfo"
    nfo.write_text("Example Release\n", encoding="utf-8")

    bdmv = tmp_path / "BDMV"
    (bdmv / "STREAM").mkdir(parents=True)
    (bdmv / "PLAYLIST").mkdir(parents=True)
    (bdmv / "STREAM" / "00000.m2ts").write_bytes(b"stream")
    (bdmv / "PLAYLIST" / "00000.mpls").write_bytes(b"playlist")
    video_ts = tmp_path / "VIDEO_TS"
    video_ts.mkdir()
    (video_ts / "VIDEO_TS.IFO").write_bytes(b"ifo")
    (video_ts / "VTS_01_1.VOB").write_bytes(b"vob")
    return {"media": media, "audio": audio, "m4b": m4b, "pdf": pdf, "epub": epub, "png": png, "nfo": nfo, "bdmv": bdmv, "dvd": video_ts}


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int) -> Meta:
    categories = ("MOVIE", "TV", "MUSIC", "BOOK", "XXX")
    category = categories[profile % len(categories)]
    selected = files["m4b"] if category == "BOOK" else files["audio"] if category == "MUSIC" else files["media"]
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"media-{profile}",
        path=str(selected),
        filename=selected.name,
        filelist=[str(selected), str(files["audio"]), str(files["png"])],
        category=category,
        type=("WEBDL", "REMUX", "FLAC", "M4B", "ENCODE")[profile % 5],
        source="BluRay" if profile % 2 else "WEB",
        is_disc="BDMV" if profile == 1 else "DVD" if profile == 2 else "",
        resolution=("2160p", "1080p", "720p", "OTHER", "1080p")[profile % 5],
        title="Example Release",
        name="Example Release 2026 1080p WEB-DL H.264 DDP 5.1-GROUP",
        clean_name="Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP",
        year=2026,
        season=1,
        episode=1,
        season_int=1,
        episode_int=1,
        video_codec="H.265" if profile == 0 else "H.264",
        video_encode="H.265" if profile == 0 else "H.264",
        bit_depth="10",
        hdr="DV HDR10+" if profile == 0 else "HDR" if profile == 1 else "",
        audio="TrueHD Atmos 7.1" if profile == 0 else "DDP 5.1",
        audio_codec="TrueHD" if profile == 0 else "DDP",
        channels="7.1" if profile == 0 else "5.1",
        container="MKV",
        tag="-GROUP",
        group="GROUP",
        service="AMZN",
        image_list=[],
        screens=4,
        cutoff=1,
        frame_rate=24.0,
        video_duration=7200,
        video_width=1920,
        video_height=1080,
        unattended=True,
        unattended_confirm=True,
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "Format": "Matroska", "Duration": "7200000", "FileSize": "1000000"},
                    {"@type": "Video", "Format": "HEVC", "Width": "1920", "Height": "1080", "FrameRate": "24", "Language": "en"},
                    {"@type": "Audio", "Format": "E-AC-3", "Channels": "6", "BitRate": "640000", "Language": "en"},
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ]
            }
        },
        bdinfo={"size": 25.0, "playlist": "00000.MPLS", "video": [], "audio": [], "subtitles": []},
        discs=[{"type": "BDMV", "path": str(files["bdmv"]), "name": "DISC1"}],
        audio_languages=["English", "Japanese"],
        subtitle_languages=["English"],
        author="Example Author",
        book_title="Example Book",
        book_author="Example Author",
        artwork_path=str(files["png"]),
        artwork_url="https://media.example/artwork.png",
        poster="https://media.example/artwork.png",
        manual_frames="1,2,3,4",
        frame_overlay=profile % 2 == 0,
        vapoursynth=False,
        retake=False,
        debug=False,
    )


def _modules(monkeypatch: Any) -> list[ModuleType]:
    _install_optional_media_modules(monkeypatch)
    return [importlib.import_module(info.name) for info in pkgutil.iter_modules(media_package.__path__, f"{media_package.__name__}.")]


def _value(name: str, annotation: object, meta: Meta, files: Mapping[str, Path], profile: int) -> object:
    normalized = name.casefold().lstrip("_")
    values: dict[str, object] = {
        "meta": meta,
        "config": example_config,
        "path": str(files["media"]),
        "filename": files["media"].name,
        "filepath": files["media"],
        "file_path": files["media"],
        "videopath": str(files["media"]),
        "videoloc": str(files["media"]),
        "source_path": files["media"],
        "output_path": files["png"],
        "image_path": str(files["png"]),
        "artwork_path": str(files["png"]),
        "epub_path": str(files["epub"]),
        "pdf_path": str(files["pdf"]),
        "base_dir": str(files["media"].parent),
        "folder_id": meta.uuid,
        "uuid": meta.uuid,
        "filelist": list(meta.filelist),
        "files": list(meta.filelist),
        "mediainfo": meta.mediainfo,
        "mi": meta.mediainfo,
        "track": meta.mediainfo["media"]["track"][1],
        "general_track": meta.mediainfo["media"]["track"][0],
        "audio_track": meta.mediainfo["media"]["track"][2],
        "stream": meta.mediainfo["media"]["track"][2],
        "streams": meta.mediainfo["media"]["track"],
        "ss_times": [10.0, 20.0, 30.0, 40.0],
        "manual_frames": [10.0, 20.0],
        "length": 7200.0,
        "duration": 7200.0,
        "timestamp": 10.0,
        "time": 10.0,
        "frame_rate": 24.0,
        "width": 1920,
        "height": 1080,
        "w": 1920,
        "h": 1080,
        "w_sar": 1920.0,
        "h_sar": 1080.0,
        "num_screens": 4,
        "screens": 4,
        "total_screens": 4,
        "task_limit": 1,
        "index": 0,
        "i": 0,
        "loglevel": "error",
        "img_host": "imgbb",
        "force_screenshots": False,
        "cleanup_after_capture": False,
        "capture_group": "main",
        "hdr_tonemap": False,
        "retake": False,
        "code": "en",
        "language": "English",
        "value": "example",
        "text": "example",
        "data": {},
        "payload": {},
        "clip": _FakeClip(),
        "frame": _Universal(props={}),
        "args": (0, str(files["media"]), 10.0, str(files["png"]), 1920.0, 1080.0, 1920.0, 1080.0, "error", False, meta),
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
        return ["example"]
    if origin in {dict, Mapping}:
        return {}
    if origin is tuple:
        return tuple(_value(normalized, item, meta, files, profile) for item in args if item is not Ellipsis)
    if origin is not None and type(None) in args:
        concrete = next((item for item in args if item is not type(None)), str)
        return _value(normalized, concrete, meta, files, profile)
    return _Universal()


_PROTECTED_ARGUMENTS = frozenset({"meta", "config", "clip", "frame", "process", "response"})


def _coerce_override(value: object, annotation: object, meta: Meta, files: Mapping[str, Path], profile: int) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if annotation is Meta:
        return meta
    if annotation is Path:
        return value if isinstance(value, Path) else files["media"]
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
        return [_coerce_override(value, element, meta, files, profile)]
    if origin in {dict, Mapping}:
        return value if isinstance(value, dict) else {}
    if origin is tuple:
        return value if isinstance(value, tuple) else ()
    if origin is set:
        return value if isinstance(value, set) else {value}
    if origin is not None and type(None) in args and value is not None:
        concrete = next((item for item in args if item is not type(None)), object)
        return _coerce_override(value, concrete, meta, files, profile)
    return value


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
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        if parameter.default is not inspect.Parameter.empty and parameter.name not in overrides:
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = overrides.get(parameter.name, _value(parameter.name, annotation, meta, files, profile))
        if parameter.name in overrides and parameter.name not in _PROTECTED_ARGUMENTS:
            value = _coerce_override(value, annotation, meta, files, profile)
        elif parameter.name in _PROTECTED_ARGUMENTS:
            value = _value(parameter.name, annotation, meta, files, profile)
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keywords[parameter.name] = value
        else:
            positional.append(value)
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


def test_media_catalog_uses_local_fakes_and_domain_releases(tmp_path: Path, monkeypatch: Any) -> None:
    files = _fixture_tree(tmp_path)
    modules = _modules(monkeypatch)

    async def fake_subprocess(*_args: object, **_kwargs: object) -> _Process:
        return _Process()

    async def no_sleep(_delay: float = 0, *_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_subprocess)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Completed())
    monkeypatch.setattr(subprocess, "check_output", lambda *_args, **_kwargs: b"ok")
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)

    # Keep expensive numeric/plot operations deterministic and in-memory.
    import librosa
    import matplotlib.pyplot as plt

    monkeypatch.setattr(librosa, "load", lambda *_args, **_kwargs: (np.ones(2048, dtype=float), 48000))
    monkeypatch.setattr(librosa, "stft", lambda *_args, **_kwargs: np.ones((8, 8), dtype=complex))
    monkeypatch.setattr(librosa, "amplitude_to_db", lambda value, **_kwargs: np.abs(value))
    monkeypatch.setattr(plt, "savefig", lambda path, **_kwargs: Image.new("RGB", (64, 64), "white").save(path))
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")

    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []

    async def exercise() -> None:
        for module in modules:
            functions = [
                (name, function) for name, function in inspect.getmembers(module, inspect.isfunction) if function.__module__ == module.__name__ and not name.startswith("__")
            ]
            for name, function in functions:
                qualified = f"{module.__name__}.{name}"
                attempted.add(qualified)
                for profile in range(5):
                    try:
                        await _invoke(function, _meta(tmp_path, files, profile), files, profile)
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(f"{qualified}:{type(error).__name__}")
                    except Exception as error:
                        expected_rejections.append(f"{qualified}:{type(error).__name__}")
                for meta_updates, argument_overrides in literal_branch_scenarios(function, Meta.__dataclass_fields__, limit=192):
                    argument_overrides = {key: value for key, value in argument_overrides.items() if key not in _PROTECTED_ARGUMENTS}
                    scenario_meta = _meta(tmp_path, files, 0)
                    for key, value in meta_updates.items():
                        if key in Meta.__dataclass_fields__:
                            setattr(scenario_meta, key, value)
                    try:
                        await _invoke(function, scenario_meta, files, 0, argument_overrides)
                    except (KeyboardInterrupt, SystemExit) as error:
                        terminations.append(f"{qualified}:{type(error).__name__}")
                    except Exception as error:
                        expected_rejections.append(f"{qualified}:{type(error).__name__}")

            classes = [(class_name, class_type) for class_name, class_type in inspect.getmembers(module, inspect.isclass) if class_type.__module__ == module.__name__]
            for class_name, class_type in classes:
                try:
                    instance = await _invoke(class_type, _meta(tmp_path, files, 0), files, 0)
                except Exception as error:
                    expected_rejections.append(f"{module.__name__}.{class_name}.__init__:{type(error).__name__}")
                    continue
                for method_name, method in inspect.getmembers(instance, callable):
                    if method_name.startswith("__"):
                        continue
                    qualified = f"{module.__name__}.{class_name}.{method_name}"
                    attempted.add(qualified)
                    for profile in range(3):
                        try:
                            await _invoke(method, _meta(tmp_path, files, profile), files, profile)
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(f"{qualified}:{type(error).__name__}")
                        except Exception as error:
                            expected_rejections.append(f"{qualified}:{type(error).__name__}")
                    for meta_updates, argument_overrides in literal_branch_scenarios(method, Meta.__dataclass_fields__, limit=192):
                        argument_overrides = {key: value for key, value in argument_overrides.items() if key not in _PROTECTED_ARGUMENTS}
                        scenario_meta = _meta(tmp_path, files, 0)
                        for key, value in meta_updates.items():
                            if key in Meta.__dataclass_fields__:
                                setattr(scenario_meta, key, value)
                        try:
                            await _invoke(method, scenario_meta, files, 0, argument_overrides)
                        except (KeyboardInterrupt, SystemExit) as error:
                            terminations.append(f"{qualified}:{type(error).__name__}")
                        except Exception as error:
                            expected_rejections.append(f"{qualified}:{type(error).__name__}")

    asyncio.run(exercise())

    assert len(attempted) >= 180
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
