from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from src.domain_models.release import Meta
from src.integrations.media import audio_spectrogram


def _file(tmp_path: Path, name: str = "audio.flac") -> Path:
    path = tmp_path / name
    path.write_bytes(b"audio")
    return path


def _meta(tmp_path: Path, **values: object) -> Meta:
    file = _file(tmp_path)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "spectrogram",
        "category": "MUSIC",
        "filelist": [str(file)],
        "bdinfo": {},
        "spectrograms_images": [],
        "audio_spectrogram_tracks": None,
        "unattended": True,
        "skip_imghost_upload": False,
        "debug": False,
    }
    state.update(values)
    return Meta(state)


def test_env_cjk_and_selection_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UA_AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    monkeypatch.delenv("AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    assert audio_spectrogram._env_font_path() is None
    monkeypatch.setenv("AUDIO_SPECTROGRAM_FONT_PATH", " /font.ttf ")
    assert audio_spectrogram._env_font_path() == "/font.ttf"
    assert not audio_spectrogram._is_cjk_character("")
    assert audio_spectrogram._is_cjk_character("中")
    assert not audio_spectrogram._is_cjk_character("A")

    streams = [{"index": 1}, {"index": 2}]
    assert audio_spectrogram.select_audio_streams(streams, "all,0") == streams
    assert audio_spectrogram.select_audio_streams(streams, "bad,9") == []


def test_ffprobe_success_failures_and_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _file(tmp_path)

    def run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=json.dumps({"streams": [{"index": 1}, "bad"]}), stderr="")

    monkeypatch.setattr(audio_spectrogram.subprocess, "run", run)
    assert audio_spectrogram.get_audio_streams(file) == [{"index": 1}]

    monkeypatch.setattr(
        audio_spectrogram.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout="", stderr="probe failed"),
    )
    with pytest.raises(RuntimeError, match="probe failed"):
        audio_spectrogram.get_audio_streams(file)

    monkeypatch.setattr(
        audio_spectrogram.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout="not-json", stderr=""),
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        audio_spectrogram.get_audio_streams(file)

    monkeypatch.setattr(audio_spectrogram.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")))
    with pytest.raises(RuntimeError, match="Could not run ffprobe"):
        audio_spectrogram.get_audio_streams(file)


def test_config_source_stft_cache_and_font_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert audio_spectrogram._positive_config_int({"DEFAULT": {"x": "bad"}}, "x", 5) == 5
    assert audio_spectrogram._positive_config_int({"DEFAULT": {"x": 0}}, "x", 5) == 5
    assert audio_spectrogram._positive_config_int({"DEFAULT": {"x": 7}}, "x", 5) == 7

    disc = _file(tmp_path, "disc.m2ts")
    assert audio_spectrogram.get_spectrogram_sources("MOVIE", [], disc, 2) == [disc]
    one = _file(tmp_path, "one.mkv")
    two = _file(tmp_path, "two.mkv")
    assert audio_spectrogram.get_spectrogram_sources("MOVIE", [one, two], None, 5) == [one]
    assert audio_spectrogram.get_stft_parameters(1)[0] == 32

    cache = tmp_path / "cache.json"
    assert audio_spectrogram._load_cached_images(cache, "fp") == []
    cache.write_text("bad-json", encoding="utf-8")
    assert audio_spectrogram._load_cached_images(cache, "fp") == []
    cache.write_text(json.dumps({"fingerprint": "other", "spectrograms_images": []}), encoding="utf-8")
    assert audio_spectrogram._load_cached_images(cache, "fp") == []
    cache.write_text(json.dumps({"fingerprint": "fp", "spectrograms_images": [{"raw_url": "x"}]}), encoding="utf-8")
    assert audio_spectrogram._load_cached_images(cache, "fp") == [{"raw_url": "x"}]

    monkeypatch.setattr(audio_spectrogram.font_manager, "FontProperties", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("font")))
    assert audio_spectrogram._font_name_for_file("/fonts/MyFont.ttf") == "MyFont"
    assert audio_spectrogram._build_plot_font_properties("/fonts/MyFont.ttf") == (None, False)
    assert audio_spectrogram._build_plot_font_properties(None) == (None, False)

    monkeypatch.setattr(audio_spectrogram.ft2font, "FT2Font", lambda _path: (_ for _ in ()).throw(RuntimeError("bad")))
    assert not audio_spectrogram._font_is_loadable("bad.ttf")
    assert audio_spectrogram._font_path_supports_cjk("/fonts/NotoSansCJK.ttf")
    assert audio_spectrogram._font_path_supports_cjk("/fonts/plain.ttf", "Noto Sans SC")
    assert not audio_spectrogram._font_path_supports_cjk("/fonts/plain.ttf", "Plain")
    assert not audio_spectrogram._font_path_supports_cjk("/fonts/plain.ttf")


def test_resolve_font_cache_fallback_and_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audio_spectrogram._PLOT_FONT_CACHE = ("Cached", False, None)
    assert audio_spectrogram._resolve_plot_font() == ("Cached", False, None)
    audio_spectrogram._PLOT_FONT_CACHE = None

    monkeypatch.delenv("UA_AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    monkeypatch.delenv("AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    fallback = str(tmp_path / "Plain.ttf")
    Path(fallback).write_bytes(b"font")
    monkeypatch.setattr(audio_spectrogram.font_manager, "findfont", lambda name, **_kwargs: fallback if name == "Noto Sans" else (_ for _ in ()).throw(ValueError("missing")))
    monkeypatch.setattr(audio_spectrogram, "_font_name_for_file", lambda _path: "Plain")
    monkeypatch.setattr(audio_spectrogram, "_font_is_loadable", lambda _path: True)
    monkeypatch.setattr(audio_spectrogram.font_manager, "findSystemFonts", lambda: [])
    assert audio_spectrogram._resolve_plot_font() == ("Plain", False, fallback)

    audio_spectrogram._PLOT_FONT_CACHE = None
    monkeypatch.setattr(audio_spectrogram.font_manager, "findfont", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("missing")))
    assert audio_spectrogram._resolve_plot_font() == ("DejaVu Sans", False, None)


def test_generate_spectrogram_decode_and_audio_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _file(tmp_path)
    output = tmp_path / "out"
    output.mkdir()

    monkeypatch.setattr(
        audio_spectrogram.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"decode failed"),
    )
    with pytest.raises(RuntimeError, match="decode failed"):
        audio_spectrogram.generate_spectrogram(1, "Label", "en", file, output, 10, 48000, 1, "file")

    monkeypatch.setattr(audio_spectrogram.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 1)))
    with pytest.raises(RuntimeError, match="Could not decode"):
        audio_spectrogram.generate_spectrogram(1, "Label", "en", file, output, 10, 48000, 1, "file")

    monkeypatch.setattr(
        audio_spectrogram.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"wav", stderr=b""),
    )
    monkeypatch.setattr(audio_spectrogram.librosa, "load", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audio read")))
    with pytest.raises(RuntimeError, match="Could not read decoded"):
        audio_spectrogram.generate_spectrogram(1, "Label", "en", file, output, 10, 48000, 1, "file")

    monkeypatch.setattr(audio_spectrogram.librosa, "load", lambda *_args, **_kwargs: (np.array([]), 48000))
    with pytest.raises(RuntimeError, match="no decodable samples"):
        audio_spectrogram.generate_spectrogram(1, "Label", "en", file, output, 10, 48000, 1, "file")


def test_generate_spectrogram_success_with_plot_doubles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = _file(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    monkeypatch.setattr(
        audio_spectrogram.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"wav", stderr=b""),
    )
    monkeypatch.setattr(audio_spectrogram.librosa, "load", lambda *_args, **_kwargs: (np.ones(128), 48000))
    monkeypatch.setattr(audio_spectrogram.librosa, "stft", lambda *_args, **_kwargs: np.ones((4, 4)))
    monkeypatch.setattr(audio_spectrogram.librosa, "amplitude_to_db", lambda value, **_kwargs: value)
    monkeypatch.setattr(audio_spectrogram.librosa.display, "specshow", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(audio_spectrogram, "_resolve_plot_font", lambda: ("Plain", False, None))

    class Axis:
        def set_title(self, *_args: object, **_kwargs: object) -> None:
            return None

        def text(self, *_args: object, **_kwargs: object) -> None:
            return None

        def set_xlabel(self, *_args: object, **_kwargs: object) -> None:
            return None

        def set_ylabel(self, *_args: object, **_kwargs: object) -> None:
            return None

    Axis.transAxes = object()

    class Figure:
        def colorbar(self, *_args: object, **_kwargs: object) -> None:
            return None

        def tight_layout(self) -> None:
            return None

        def savefig(self, path: Path, **_kwargs: object) -> None:
            path.write_bytes(b"png")

    monkeypatch.setattr(audio_spectrogram.plt, "subplots", lambda **_kwargs: (Figure(), Axis()))
    monkeypatch.setattr(audio_spectrogram.plt, "close", lambda _figure: None)
    path = audio_spectrogram.generate_spectrogram(1, "Stream_1", "en", file, output, 10, 48000, 1, "source")
    assert path.is_file() and path.name == "spectrogram_source_01_stream_1.png"


def test_process_shortcuts_sources_streams_choice_cache_and_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"audio_spectrogram_max_files": 1, "process_all_audio_spectrogram": True}}
    existing = _meta(tmp_path, spectrograms_images=[{"raw_url": "existing"}])
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(existing, config)) == []

    missing = _meta(tmp_path, uuid="missing", filelist=[str(tmp_path / "missing.flac")])
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(missing, config)) == []

    meta = _meta(tmp_path)
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: [{"index": 1, "tags": {"language": "en", "title": "Main"}}])
    plot = tmp_path / "plot.png"
    plot.write_bytes(b"plot")
    monkeypatch.setattr(audio_spectrogram, "generate_spectrogram", lambda *_args, **_kwargs: plot)
    monkeypatch.setattr(audio_spectrogram, "_resolve_plot_font", lambda: ("Plain", False, None))
    uploader = SimpleNamespace(upload_screens=AsyncMock(return_value=([{"raw_url": "uploaded"}], 1)))
    result = asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, config, uploader))
    assert result == [str(plot)] and meta.spectrograms_images == [{"raw_url": "uploaded"}]
    cache = Path(tmp_path) / "tmp" / "spectrogram" / "audio_spectrograms_images.json"
    assert cache.is_file()

    cached = _meta(tmp_path, uuid="spectrogram")
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: [{"index": 1, "tags": {}}])
    fingerprint = audio_spectrogram._cache_fingerprint([Path(cached.filelist[0])], 600, 48000, [(Path(cached.filelist[0]), 1)])
    cache.write_text(json.dumps({"fingerprint": fingerprint, "spectrograms_images": [{"raw_url": "cached"}]}), encoding="utf-8")
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(cached, {"DEFAULT": {}})) == []
    assert cached.spectrograms_images == [{"raw_url": "cached"}]


def test_process_stream_errors_bdinfo_selection_generation_and_upload_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {}}
    source = _file(tmp_path, "00001.m2ts")
    bdroot = tmp_path / "disc"
    stream_dir = bdroot / "STREAM"
    stream_dir.mkdir(parents=True)
    target = stream_dir / source.name
    target.write_bytes(source.read_bytes())
    meta = _meta(
        tmp_path,
        uuid="disc",
        category="MOVIE",
        filelist=[str(source)],
        bdinfo={"path": str(bdroot), "files": [{"file": source.name}], "audio": [{"language": "French", "codec": "DTS"}]},
        audio_spectrogram_tracks="bad",
    )
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: [{"index": 2, "tags": {"language": "und"}}])
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, config)) == []

    meta.audio_spectrogram_tracks = "all"
    monkeypatch.setattr(audio_spectrogram, "generate_spectrogram", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("plot failed")))
    monkeypatch.setattr(audio_spectrogram, "_resolve_plot_font", lambda: ("Plain", False, None))
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, config)) == []

    meta = _meta(tmp_path, uuid="stream-error")
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: (_ for _ in ()).throw(RuntimeError("probe failed")))
    assert asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, config)) == []

    meta = _meta(tmp_path, uuid="upload-error")
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: [{"index": 1, "tags": {}}])
    plot = tmp_path / "upload-error.png"
    plot.write_bytes(b"plot")
    monkeypatch.setattr(audio_spectrogram, "generate_spectrogram", lambda *_args, **_kwargs: plot)
    uploader = SimpleNamespace(upload_screens=AsyncMock(side_effect=RuntimeError("upload failed")))
    result = asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, config, uploader))
    assert result == [str(plot)] and meta.spectrograms_images == []


def test_process_attended_prompt_and_multi_source_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    one = _file(tmp_path, "one.flac")
    two = _file(tmp_path, "two.flac")
    meta = _meta(tmp_path, filelist=[str(one), str(two)], unattended=False, category="MUSIC")
    monkeypatch.setattr(audio_spectrogram, "get_audio_streams", lambda _path: [{"index": 1, "tags": {"language": "en", "title": "Main"}}])
    monkeypatch.setattr(audio_spectrogram, "prompt_audio_stream_positions", lambda: "0")
    plot = tmp_path / "plot.png"
    plot.write_bytes(b"plot")
    monkeypatch.setattr(audio_spectrogram, "generate_spectrogram", lambda *_args, **_kwargs: plot)
    monkeypatch.setattr(audio_spectrogram, "_resolve_plot_font", lambda: ("Plain", False, None))
    result = asyncio.run(audio_spectrogram.process_audio_spectrograms(meta, {"DEFAULT": {"audio_spectrogram_max_files": 1}}))
    assert result == [str(plot)]


def test_resolve_plot_font_skips_empty_findfont(monkeypatch: pytest.MonkeyPatch) -> None:
    audio_spectrogram._PLOT_FONT_CACHE = None
    monkeypatch.delenv("UA_AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    monkeypatch.delenv("AUDIO_SPECTROGRAM_FONT_PATH", raising=False)
    monkeypatch.setattr(audio_spectrogram.font_manager, "findfont", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(audio_spectrogram.font_manager, "findSystemFonts", lambda: [])
    assert audio_spectrogram._resolve_plot_font() == ("DejaVu Sans", False, None)
