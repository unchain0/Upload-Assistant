from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

_external_vs = ModuleType("vapoursynth")
_external_vs.core = SimpleNamespace()  # type: ignore[attr-defined]
_external_awsm = ModuleType("awsmfunc")
_external_awsm.DynamicTonemap = lambda clip, **_kwargs: clip  # type: ignore[attr-defined]
_external_awsm.ScreenGen = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
_external_awsm.zresize = lambda clip, **_kwargs: clip  # type: ignore[attr-defined]
sys.modules.setdefault("vapoursynth", _external_vs)
sys.modules.setdefault("awsmfunc", _external_awsm)

from src.integrations.media import vapoursynth  # noqa: E402


class Clip:
    def __init__(
        self,
        *,
        width: int = 1920,
        height: int = 1080,
        frames: int = 20000,
        primaries: int = 1,
    ) -> None:
        self.width = width
        self.height = height
        self.num_frames = frames
        self._primaries = primaries
        self.crop_calls: list[dict[str, float]] = []
        self.std = SimpleNamespace(Crop=self._crop)

    def __len__(self) -> int:
        return self.num_frames

    def get_frame(self, _index: int) -> Any:
        return SimpleNamespace(
            props={"_Primaries": self._primaries, "_PictType": "I"}
        )

    def _crop(self, **kwargs: float) -> Clip:
        self.crop_calls.append(dict(kwargs))
        return self


class FakeCore:
    def __init__(self, source: Clip, encode: Clip | None = None) -> None:
        self.source = source
        self.encode = encode or Clip()
        self.ffms_calls: list[tuple[str, dict[str, object]]] = []
        self.lsmas_calls: list[str] = []
        self.text_calls: list[str] = []
        self.frame_eval_calls = 0
        self.ffms2 = SimpleNamespace(Source=self._ffms_source)
        self.lsmas = SimpleNamespace(LWLibavSource=self._lsmas_source)
        self.text = SimpleNamespace(Text=self._text)
        self.std = SimpleNamespace(FrameEval=self._frame_eval)

    def _ffms_source(self, path: str, **kwargs: object) -> Clip:
        self.ffms_calls.append((path, dict(kwargs)))
        return self.encode if "encode" in Path(path).name else self.source

    def _lsmas_source(self, path: str) -> Clip:
        self.lsmas_calls.append(path)
        return self.source

    def _text(self, clip: Clip, info: str) -> Clip:
        self.text_calls.append(info)
        return clip

    def _frame_eval(self, clip: Clip, callback: Any, prop_src: Clip) -> Clip:
        self.frame_eval_calls += 1
        callback(3, prop_src.get_frame(0))
        return clip


def test_custom_frame_info_calls_frame_eval_and_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clip = Clip(frames=42)
    core = FakeCore(clip)
    monkeypatch.setattr(vapoursynth, "core", core)
    assert vapoursynth.custom_frame_info(clip, "ignored") is clip
    assert core.frame_eval_calls == 1
    assert core.text_calls == ["Frame 3 of 42\nPicture type: I"]


def test_optimize_images_disabled_missing_old_python_and_levels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "image.png"
    vapoursynth.optimize_images(image, {"optimize_images": False})
    vapoursynth.optimize_images(image, {"optimize_images": True})

    image.write_bytes(b"x")
    import platform

    monkeypatch.setattr(
        platform, "python_version_tuple", lambda: ("3", "6", "0")
    )
    vapoursynth.optimize_images(image, {"optimize_images": True})

    calls: list[tuple[Path, int]] = []
    fake_oxipng = SimpleNamespace(
        optimize=lambda path, level: calls.append((Path(path), level))
    )
    monkeypatch.setitem(sys.modules, "oxipng", fake_oxipng)
    monkeypatch.setattr(
        platform, "python_version_tuple", lambda: ("3", "14", "0")
    )
    vapoursynth.optimize_images(image, {"optimize_images": True})
    assert calls[-1] == (image, 3)

    real_stat = Path.stat

    def huge_stat(path: Path, *args: object, **kwargs: object):
        value = real_stat(path, *args, **kwargs)
        if path == image:
            return SimpleNamespace(st_size=16_000_000)
        return value

    monkeypatch.setattr(Path, "stat", huge_stat)
    vapoursynth.optimize_images(image, {"optimize_images": True})
    assert calls[-1] == (image, 6)

    fake_oxipng.optimize = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("optimize failed")
    )
    vapoursynth.optimize_images(image, {"optimize_images": True})


def test_vs_screengn_uses_existing_frames_and_m2ts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Clip(primaries=1)
    core = FakeCore(source)
    monkeypatch.setattr(vapoursynth, "core", core)
    screens = tmp_path / "screens.txt"
    screens.write_text("1000\n2000\n", encoding="utf-8")
    screen_calls: list[tuple[Clip, str, str]] = []
    monkeypatch.setattr(
        vapoursynth,
        "ScreenGen",
        lambda clip, directory, suffix: screen_calls.append(
            (clip, directory, suffix)
        ),
    )
    optimized: list[Path] = []
    monkeypatch.setattr(
        vapoursynth,
        "optimize_images",
        lambda image, _config: optimized.append(Path(image)),
    )

    vapoursynth.vs_screengn(
        "source.m2ts",
        num=2,
        dir=str(tmp_path),
        config={"optimize_images": True},
    )

    assert core.lsmas_calls == ["source.m2ts"]
    assert screen_calls == [(source, str(tmp_path), "a")]
    assert optimized == [tmp_path / "01a.png", tmp_path / "02a.png"]


def test_vs_screengn_generates_frames_cache_and_missing_encode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Clip(frames=20_000, primaries=1)
    core = FakeCore(source)
    monkeypatch.setattr(vapoursynth, "core", core)
    (tmp_path / "screens.txt").write_text("-1\n", encoding="utf-8")
    generated = iter((1500, 2500))
    monkeypatch.setattr(
        vapoursynth.random, "randint", lambda *_args: next(generated)
    )
    monkeypatch.setattr(vapoursynth, "ScreenGen", lambda *_args: None)
    monkeypatch.setattr(vapoursynth, "optimize_images", lambda *_args: None)

    vapoursynth.vs_screengn(
        "source.mkv",
        encode=str(tmp_path / "missing-encode.mkv"),
        num=2,
        dir=str(tmp_path),
    )

    assert core.ffms_calls[0][0] == "source.mkv"
    assert (tmp_path / "screens.txt").read_text(
        encoding="utf-8"
    ) == "1500\n2500\n"

    cache = tmp_path / "ffms2.ffms2"
    cache.write_text("cached", encoding="utf-8")
    (tmp_path / "screens.txt").write_text("1500\n2500\n", encoding="utf-8")
    vapoursynth.vs_screengn("source.mkv", num=2, dir=str(tmp_path))
    assert cache.exists()


def test_vs_screengn_encode_resize_tonemap_and_screen_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Clip(width=3840, height=2160, primaries=9)
    encode = Clip(width=1920, height=800, primaries=9)
    core = FakeCore(source, encode)
    monkeypatch.setattr(vapoursynth, "core", core)
    encode_path = tmp_path / "encode.mkv"
    encode_path.write_bytes(b"encode")
    (tmp_path / "screens.txt").write_text("1500\n", encoding="utf-8")

    resize_calls: list[dict[str, object]] = []

    def resize(clip: Clip, **kwargs: object) -> Clip:
        resize_calls.append(dict(kwargs))
        if "preset" in kwargs:
            return Clip(width=1728, height=2160, primaries=clip._primaries)
        return clip

    tonemap_calls: list[Clip] = []
    monkeypatch.setattr(vapoursynth, "zresize", resize)
    monkeypatch.setattr(
        vapoursynth,
        "DynamicTonemap",
        lambda clip, **_kwargs: tonemap_calls.append(clip) or clip,
    )
    info_calls: list[str] = []
    monkeypatch.setattr(
        vapoursynth,
        "custom_frame_info",
        lambda clip, text: info_calls.append(text) or clip,
    )
    screens: list[str] = []
    monkeypatch.setattr(
        vapoursynth,
        "ScreenGen",
        lambda _clip, _directory, suffix: screens.append(suffix),
    )
    monkeypatch.setattr(vapoursynth, "optimize_images", lambda *_args: None)

    vapoursynth.vs_screengn(
        "source.mkv", encode=str(encode_path), num=1, dir=str(tmp_path)
    )

    assert source.crop_calls
    assert resize_calls[-1] == {"width": 1920, "height": None}
    assert tonemap_calls == [source, encode]
    assert info_calls == ["Tonemapped", "Encode (Tonemapped)"]
    assert screens == ["a", "b"]


def test_vs_screengn_portrait_resize_and_index_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = Clip(width=1920, height=1080)
    encode = Clip(width=720, height=1280)
    core = FakeCore(source, encode)
    monkeypatch.setattr(vapoursynth, "core", core)
    encode_path = tmp_path / "encode.mkv"
    encode_path.write_bytes(b"encode")
    (tmp_path / "screens.txt").write_text("1500\n", encoding="utf-8")
    resize_calls: list[dict[str, object]] = []

    def resize(clip: Clip, **kwargs: object) -> Clip:
        resize_calls.append(dict(kwargs))
        return Clip(width=600, height=1080) if "preset" in kwargs else clip

    monkeypatch.setattr(vapoursynth, "zresize", resize)
    monkeypatch.setattr(vapoursynth, "ScreenGen", lambda *_args: None)
    monkeypatch.setattr(vapoursynth, "optimize_images", lambda *_args: None)
    vapoursynth.vs_screengn(
        "source.mkv", encode=str(encode_path), num=1, dir=str(tmp_path)
    )
    assert resize_calls[-1] == {"width": None, "height": 1280}

    core.ffms2.Source = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("index failed")
    )
    with pytest.raises(RuntimeError, match="index failed"):
        vapoursynth.vs_screengn(
            "broken.mkv", num=1, dir=str(tmp_path / "other")
        )
