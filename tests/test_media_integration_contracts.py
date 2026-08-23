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


class _Response:
    status_code = 200
    url = "https://media.example/artwork.png"
    headers: ClassVar[dict[str, str]] = {
        "content-type": "image/png",
        "content-length": "128",
    }
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
    media = (
        tmp_path / "Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP.mkv"
    )
    media.write_bytes(b"media")
    audio = tmp_path / "Example Album.flac"
    audio.write_bytes(b"audio")
    m4b = tmp_path / "Example Book.m4b"
    m4b.write_bytes(b"audio book")
    pdf = tmp_path / "Example Book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    epub = tmp_path / "Example Book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
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
    return {
        "media": media,
        "audio": audio,
        "m4b": m4b,
        "pdf": pdf,
        "epub": epub,
        "png": png,
        "nfo": nfo,
        "bdmv": bdmv,
        "dvd": video_ts,
    }


_MEDIA_CATEGORIES = ("MOVIE", "TV", "MUSIC", "BOOK", "XXX")
_MEDIA_TYPES = ("WEBDL", "REMUX", "FLAC", "M4B", "ENCODE")
_MEDIA_RESOLUTIONS = ("2160p", "1080p", "720p", "OTHER", "1080p")
_MEDIA_DISC_TYPES = ("", "BDMV", "DVD", "", "")
_MEDIA_VIDEO_CODECS = ("H.265", "H.264", "H.264", "H.264", "H.264")
_MEDIA_HDR = ("DV HDR10+", "HDR", "", "", "")
_MEDIA_AUDIO = (
    "TrueHD Atmos 7.1",
    "DDP 5.1",
    "DDP 5.1",
    "DDP 5.1",
    "DDP 5.1",
)
_MEDIA_AUDIO_CODECS = ("TrueHD", "DDP", "DDP", "DDP", "DDP")
_MEDIA_CHANNELS = ("7.1", "5.1", "5.1", "5.1", "5.1")
_CATEGORY_FILE_KEYS = {"BOOK": "m4b", "MUSIC": "audio"}


def _profile_value(values: tuple[Any, ...], profile: int) -> Any:
    return values[profile % len(values)]


def _profile_media_file(files: Mapping[str, Path], category: str) -> Path:
    return files[_CATEGORY_FILE_KEYS.get(category, "media")]


def _meta(tmp_path: Path, files: Mapping[str, Path], profile: int) -> Meta:
    category = _profile_value(_MEDIA_CATEGORIES, profile)
    selected = _profile_media_file(files, category)
    return Meta(
        base_dir=str(tmp_path),
        uuid=f"media-{profile}",
        path=str(selected),
        filename=selected.name,
        filelist=[str(selected), str(files["audio"]), str(files["png"])],
        category=category,
        type=_profile_value(_MEDIA_TYPES, profile),
        source=_profile_value(("WEB", "BluRay"), profile),
        is_disc=_profile_value(_MEDIA_DISC_TYPES, profile),
        resolution=_profile_value(_MEDIA_RESOLUTIONS, profile),
        title="Example Release",
        name="Example Release 2026 1080p WEB-DL H.264 DDP 5.1-GROUP",
        clean_name="Example.Release.2026.1080p.WEB-DL.H.264.DDP5.1-GROUP",
        year=2026,
        season=1,
        episode=1,
        season_int=1,
        episode_int=1,
        video_codec=_profile_value(_MEDIA_VIDEO_CODECS, profile),
        video_encode=_profile_value(_MEDIA_VIDEO_CODECS, profile),
        bit_depth="10",
        hdr=_profile_value(_MEDIA_HDR, profile),
        audio=_profile_value(_MEDIA_AUDIO, profile),
        audio_codec=_profile_value(_MEDIA_AUDIO_CODECS, profile),
        channels=_profile_value(_MEDIA_CHANNELS, profile),
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
                    {
                        "@type": "General",
                        "Format": "Matroska",
                        "Duration": "7200000",
                        "FileSize": "1000000",
                    },
                    {
                        "@type": "Video",
                        "Format": "HEVC",
                        "Width": "1920",
                        "Height": "1080",
                        "FrameRate": "24",
                        "Language": "en",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Channels": "6",
                        "BitRate": "640000",
                        "Language": "en",
                    },
                    {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                ]
            }
        },
        bdinfo={
            "size": 25.0,
            "playlist": "00000.MPLS",
            "video": [],
            "audio": [],
            "subtitles": [],
        },
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
    return [
        importlib.import_module(info.name)
        for info in pkgutil.iter_modules(
            media_package.__path__, f"{media_package.__name__}."
        )
    ]


_MISSING = object()


def _named_media_values(
    meta: Meta, files: Mapping[str, Path]
) -> dict[str, object]:
    return {
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
        "args": (
            0,
            str(files["media"]),
            10.0,
            str(files["png"]),
            1920.0,
            1080.0,
            1920.0,
            1080.0,
            "error",
            False,
            meta,
        ),
    }


def _scalar_media_value(
    annotation: object, files: Mapping[str, Path], profile: int
) -> object:
    if annotation in {inspect.Parameter.empty, Any}:
        return _Universal()
    if annotation is Path:
        return files["media"]
    values: dict[object, object] = {
        bool: bool(profile % 2),
        int: 1,
        float: 1.0,
        str: "example",
    }
    return values.get(annotation, _MISSING)


def _direct_media_composite(origin: object) -> object:
    values: dict[object, object] = {
        list: ["example"],
        Sequence: ["example"],
        dict: {},
        Mapping: {},
    }
    return values.get(origin, _MISSING)


def _tuple_media_value(
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


def _media_optional_type(args: tuple[object, ...]) -> object | None:
    if type(None) not in args:
        return None
    return next((item for item in args if item is not type(None)), str)


def _composite_media_value(
    normalized: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    origin = get_origin(annotation)
    args = get_args(annotation)
    direct = _direct_media_composite(origin)
    if direct is not _MISSING:
        return direct
    if origin is tuple:
        return _tuple_media_value(normalized, args, meta, files, profile)
    concrete = _media_optional_type(args)
    if concrete is None:
        return _Universal()
    return _value(normalized, concrete, meta, files, profile)


def _value(
    name: str,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    normalized = name.casefold().lstrip("_")
    values = _named_media_values(meta, files)
    if normalized in values:
        return values[normalized]
    scalar = _scalar_media_value(annotation, files, profile)
    if scalar is not _MISSING:
        return scalar
    return _composite_media_value(normalized, annotation, meta, files, profile)


_PROTECTED_ARGUMENTS = frozenset(
    {"meta", "config", "clip", "frame", "process", "response"}
)


def _coerce_media_number(value: object, annotation: object) -> object:
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


def _coerce_media_meta(
    _value: object, meta: Meta, _files: Mapping[str, Path]
) -> object:
    return meta


def _coerce_media_path(
    value: object, _meta: Meta, files: Mapping[str, Path]
) -> object:
    return value if isinstance(value, Path) else files["media"]


def _coerce_media_str(
    value: object, _meta: Meta, _files: Mapping[str, Path]
) -> object:
    return str(value)


def _coerce_media_bool(
    value: object, _meta: Meta, _files: Mapping[str, Path]
) -> object:
    return bool(value)


_MEDIA_SIMPLE_COERCERS: dict[
    object, Callable[[object, Meta, Mapping[str, Path]], object]
] = {
    Meta: _coerce_media_meta,
    Path: _coerce_media_path,
    str: _coerce_media_str,
    bool: _coerce_media_bool,
}


def _coerce_media_simple(
    value: object,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
) -> object:
    coercer = _MEDIA_SIMPLE_COERCERS.get(annotation)
    if coercer is None:
        return _MISSING
    return coercer(value, meta, files)


def _coerce_media_list(
    value: object,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> list[object]:
    if isinstance(value, list):
        return value
    element = args[0] if args else object
    return [_coerce_override(value, element, meta, files, profile)]


def _coerce_media_mapping(value: object) -> object:
    return value if isinstance(value, dict) else {}


def _coerce_media_tuple(value: object) -> object:
    return value if isinstance(value, tuple) else ()


def _coerce_media_set(value: object) -> object:
    return value if isinstance(value, set) else {value}


_MEDIA_COLLECTION_COERCERS: dict[object, Callable[[object], object]] = {
    dict: _coerce_media_mapping,
    Mapping: _coerce_media_mapping,
    tuple: _coerce_media_tuple,
    set: _coerce_media_set,
}


def _coerce_media_direct_collection(value: object, origin: object) -> object:
    coercer = _MEDIA_COLLECTION_COERCERS.get(origin)
    if coercer is None:
        return _MISSING
    return coercer(value)


def _coerce_media_collection(
    value: object,
    origin: object,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if origin in {list, Sequence}:
        return _coerce_media_list(value, args, meta, files, profile)
    return _coerce_media_direct_collection(value, origin)


def _coerce_media_optional(
    value: object,
    args: tuple[object, ...],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    if value is None:
        return value
    concrete = _media_optional_type(args)
    if concrete is None:
        return value
    return _coerce_override(value, concrete, meta, files, profile)


def _coerce_override(
    value: object,
    annotation: object,
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    simple = _coerce_media_simple(value, annotation, meta, files)
    if simple is not _MISSING:
        return simple
    number = _coerce_media_number(value, annotation)
    if number is not _MISSING:
        return number
    origin = get_origin(annotation)
    args = get_args(annotation)
    collection = _coerce_media_collection(
        value, origin, args, meta, files, profile
    )
    if collection is not _MISSING:
        return collection
    return _coerce_media_optional(value, args, meta, files, profile)


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


def _media_parameter_value(
    parameter: inspect.Parameter,
    annotation: object,
    overrides: Mapping[str, object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
) -> object:
    value = _value(parameter.name, annotation, meta, files, profile)
    if parameter.name not in overrides:
        return value
    if parameter.name in _PROTECTED_ARGUMENTS:
        return value
    return _coerce_override(
        overrides[parameter.name], annotation, meta, files, profile
    )


def _media_invocation_arguments(
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    overrides: Mapping[str, object],
) -> tuple[list[object], dict[str, object]]:
    hint_target = function.__init__ if inspect.isclass(function) else function
    hints = _safe_type_hints(hint_target)
    positional: list[object] = []
    keywords: dict[str, object] = {}
    for parameter in inspect.signature(function).parameters.values():
        if not _include_parameter(parameter, overrides):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        value = _media_parameter_value(
            parameter, annotation, overrides, meta, files, profile
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
    resolved_overrides = overrides or {}
    positional, keywords = _media_invocation_arguments(
        function, meta, files, profile, resolved_overrides
    )
    result = function(*positional, **keywords)
    if inspect.isawaitable(result):
        return await asyncio.wait_for(result, timeout=0.08)
    return result


_BLOCKED_MEDIA_CONTRACT_HELPERS = frozenset(
    {
        "_kill_process",
        "_terminate_posix_group",
        "_terminate_process",
        "_terminate_windows_process",
        "_terminate_process_tree",
        "_wait_taskkill",
        "_wait_terminated_process",
    }
)


async def _media_process_double(*_args: object, **_kwargs: object) -> _Process:
    return _Process()


async def _media_no_sleep(
    _delay: float = 0, *_args: object, **_kwargs: object
) -> None:
    return None


def _download_dvd_mediainfo_double(_base_dir: str) -> None:
    return None


def _patch_media_boundaries(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _media_process_double
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_shell", _media_process_double
    )
    monkeypatch.setattr(asyncio, "sleep", _media_no_sleep)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: _Completed()
    )
    monkeypatch.setattr(
        subprocess, "check_output", lambda *_args, **_kwargs: b"ok"
    )
    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)
    disc_info_module = importlib.import_module(
        "src.integrations.media.disc_info"
    )
    monkeypatch.setattr(
        disc_info_module,
        "download_dvd_mediainfo",
        _download_dvd_mediainfo_double,
    )


def _patch_numeric_boundaries(monkeypatch: Any) -> None:
    import librosa
    import matplotlib.pyplot as plt

    monkeypatch.setattr(
        librosa,
        "load",
        lambda *_args, **_kwargs: (np.ones(2048, dtype=float), 48000),
    )
    monkeypatch.setattr(
        librosa,
        "stft",
        lambda *_args, **_kwargs: np.ones((8, 8), dtype=complex),
    )
    monkeypatch.setattr(
        librosa, "amplitude_to_db", lambda value, **_kwargs: np.abs(value)
    )
    monkeypatch.setattr(
        plt,
        "savefig",
        lambda path, **_kwargs: Image.new("RGB", (64, 64), "white").save(path),
    )
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "1")


def _media_module_functions(
    module: ModuleType,
) -> list[tuple[str, Callable[..., object]]]:
    return [
        (name, function)
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if function.__module__ == module.__name__
        and not name.startswith("__")
        and name not in _BLOCKED_MEDIA_CONTRACT_HELPERS
    ]


def _media_module_classes(module: ModuleType) -> list[tuple[str, type[Any]]]:
    return [
        (class_name, class_type)
        for class_name, class_type in inspect.getmembers(
            module, inspect.isclass
        )
        if class_type.__module__ == module.__name__
    ]


def _filtered_media_overrides(
    overrides: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in overrides.items()
        if key not in _PROTECTED_ARGUMENTS
    }


def _apply_media_meta_updates(
    meta: Meta, updates: Mapping[str, object]
) -> None:
    for key, value in updates.items():
        if key in Meta.__dataclass_fields__:
            setattr(meta, key, value)


def _record_media_error(
    qualified: str,
    error: BaseException,
    terminations: list[str],
    rejections: list[str],
) -> None:
    if isinstance(error, KeyboardInterrupt | SystemExit):
        terminations.append(f"{qualified}:{type(error).__name__}")
        return
    rejections.append(f"{qualified}:{type(error).__name__}")


async def _run_media_call(
    qualified: str,
    function: Callable[..., object],
    meta: Meta,
    files: Mapping[str, Path],
    profile: int,
    terminations: list[str],
    rejections: list[str],
    overrides: Mapping[str, object] | None = None,
) -> object | None:
    try:
        return await _invoke(function, meta, files, profile, overrides)
    except BaseException as error:
        _record_media_error(qualified, error, terminations, rejections)
        return None


async def _run_media_profiles(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile_count: int,
    terminations: list[str],
    rejections: list[str],
) -> None:
    for profile in range(profile_count):
        await _run_media_call(
            qualified,
            function,
            _meta(tmp_path, files, profile),
            files,
            profile,
            terminations,
            rejections,
        )


async def _run_media_literal_scenarios(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for meta_updates, argument_overrides in literal_branch_scenarios(
        function, Meta.__dataclass_fields__, limit=192
    ):
        scenario_meta = _meta(tmp_path, files, 0)
        _apply_media_meta_updates(scenario_meta, meta_updates)
        await _run_media_call(
            qualified,
            function,
            scenario_meta,
            files,
            0,
            terminations,
            rejections,
            _filtered_media_overrides(argument_overrides),
        )


async def _exercise_media_callable(
    qualified: str,
    function: Callable[..., object],
    tmp_path: Path,
    files: Mapping[str, Path],
    profile_count: int,
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    attempted.add(qualified)
    await _run_media_profiles(
        qualified,
        function,
        tmp_path,
        files,
        profile_count,
        terminations,
        rejections,
    )
    await _run_media_literal_scenarios(
        qualified, function, tmp_path, files, terminations, rejections
    )


async def _instantiate_media_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    rejections: list[str],
) -> object | None:
    try:
        return await _invoke(class_type, _meta(tmp_path, files, 0), files, 0)
    except Exception as error:
        rejections.append(
            f"{module.__name__}.{class_name}.__init__:{type(error).__name__}"
        )
        return None


def _media_instance_method(
    instance: object, method_name: str, member: object
) -> Callable[..., object] | None:
    if method_name.startswith("__"):
        return None
    if method_name in _BLOCKED_MEDIA_CONTRACT_HELPERS or not callable(member):
        return None
    return getattr(instance, method_name)


async def _exercise_media_instance(
    module: ModuleType,
    class_name: str,
    instance: object,
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for method_name, member in inspect.getmembers_static(instance):
        method = _media_instance_method(instance, method_name, member)
        if method is None:
            continue
        await _exercise_media_callable(
            f"{module.__name__}.{class_name}.{method_name}",
            method,
            tmp_path,
            files,
            3,
            attempted,
            terminations,
            rejections,
        )


async def _exercise_media_class(
    module: ModuleType,
    class_name: str,
    class_type: type[Any],
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    instance = await _instantiate_media_class(
        module, class_name, class_type, tmp_path, files, rejections
    )
    if instance is None:
        return
    await _exercise_media_instance(
        module,
        class_name,
        instance,
        tmp_path,
        files,
        attempted,
        terminations,
        rejections,
    )


async def _exercise_media_module(
    module: ModuleType,
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for name, function in _media_module_functions(module):
        await _exercise_media_callable(
            f"{module.__name__}.{name}",
            function,
            tmp_path,
            files,
            5,
            attempted,
            terminations,
            rejections,
        )
    for class_name, class_type in _media_module_classes(module):
        await _exercise_media_class(
            module,
            class_name,
            class_type,
            tmp_path,
            files,
            attempted,
            terminations,
            rejections,
        )


async def _exercise_media_modules(
    modules: list[ModuleType],
    tmp_path: Path,
    files: Mapping[str, Path],
    attempted: set[str],
    terminations: list[str],
    rejections: list[str],
) -> None:
    for module in modules:
        await _exercise_media_module(
            module, tmp_path, files, attempted, terminations, rejections
        )


def test_media_catalog_uses_local_fakes_and_domain_releases(
    tmp_path: Path, monkeypatch: Any
) -> None:
    files = _fixture_tree(tmp_path)
    modules = _modules(monkeypatch)
    _patch_media_boundaries(monkeypatch)
    _patch_numeric_boundaries(monkeypatch)
    attempted: set[str] = set()
    terminations: list[str] = []
    expected_rejections: list[str] = []
    asyncio.run(
        _exercise_media_modules(
            modules,
            tmp_path,
            files,
            attempted,
            terminations,
            expected_rejections,
        )
    )
    assert len(attempted) >= 180
    assert terminations == []
    assert all(":" in item for item in expected_rejections)
