from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import ScreenshotCaptureError
from src.domain_models.release import Meta
from src.integrations.media import screenshot_capture as capture


@pytest.fixture(autouse=True)
def _reset_capture_config() -> None:
    capture.TakeScreensManager({"DEFAULT": {}})


def _disc_source(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "disc"
    stream = root / "STREAM"
    stream.mkdir(parents=True)
    source = stream / "00001.m2ts"
    source.write_bytes(b"video")
    return source, {
        "path": str(root),
        "files": [
            {"length": "00:00:10.500", "file": "short.m2ts"},
            {"length": "00:02:00.250", "file": source.name},
        ],
        "video": [{"fps": "24 fps", "codec": "AVC", "hdr_dv": ""}],
    }


def _disc_meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "disc-release",
        "category": "MOVIE",
        "screens": 1,
        "image_list": [],
        "imghost": "imgbb",
        "debug": True,
        "hdr": "",
        "frame_overlay": False,
        "frame_rate": 24.0,
        "frame_info_map": {},
        "retake": False,
        "is_disc": "BDMV",
        "discs": [],
        "tv_pack": False,
    }
    state.update(values)
    return Meta(state)


def _write_media_info(base_dir: Path, folder_id: str, *, general: object = "100", video: dict[str, object] | None = None) -> None:
    target = base_dir / "tmp" / folder_id
    target.mkdir(parents=True, exist_ok=True)
    video_data: dict[str, object] = {
        "Duration": "100",
        "Width": "1920",
        "Height": "1080",
        "PixelAspectRatio": "1",
        "DisplayAspectRatio": "1.777",
        "FrameRate": "24",
    }
    if video:
        video_data.update(video)
    (target / "MediaInfo.json").write_text(
        json.dumps({"media": {"track": [{"Duration": general}, video_data]}}),
        encoding="utf-8",
    )


def _write_large(path: str | Path, size: int = 80_000) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * size)
    return str(target)


def _write_epub(path: Path, files: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)


def test_capture_size_and_cleanup_policies() -> None:
    assert not capture._capture_size_is_valid(None, 100_000)
    assert not capture._capture_size_is_valid("imgbb", 75_000)
    assert capture._capture_size_is_valid("imgbox", 80_000)
    assert capture._capture_size_is_valid("lostimg", 80_000)
    assert capture._capture_size_is_valid("ptscreens", 80_000)
    assert not capture._capture_size_is_valid("unknown", 80_000)

    capture.default_config = {"multiScreens": 2}
    assert capture._should_cleanup_after_capture(Meta(tv_pack=False, discs=[]), True)
    assert not capture._should_cleanup_after_capture(Meta(tv_pack=True, discs=[]), True)
    assert not capture._should_cleanup_after_capture(Meta(tv_pack=False, discs=[{}, {}]), True)
    capture.default_config = {"multiScreens": 0}
    assert capture._should_cleanup_after_capture(Meta(tv_pack=True, discs=[{}, {}]), True)
    assert not capture._should_cleanup_after_capture(Meta(), False)


def test_disc_screenshots_early_exits_and_invalid_fps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, bdinfo = _disc_source(tmp_path)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))

    existing = _disc_meta(tmp_path, image_list=[{"img_url": "https://img.invalid/a.png"}])
    capture.cutoff = 1
    assert asyncio.run(capture.disc_screenshots(existing, "Disc", bdinfo, existing.uuid, existing.base_dir, False)) == []

    zero = _disc_meta(tmp_path, screens=0)
    assert asyncio.run(capture.disc_screenshots(zero, "Disc", bdinfo, zero.uuid, zero.base_dir, False)) == []

    enough = _disc_meta(tmp_path, screens=1)
    assert asyncio.run(capture.disc_screenshots(enough, "Disc", bdinfo, enough.uuid, enough.base_dir, False, image_list=[{"x": "y"}])) == []

    bad_fps = {**bdinfo, "video": [{"fps": "bad fps", "codec": "AVC", "hdr_dv": ""}]}
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    monkeypatch.setattr(capture, "capture_disc_task", AsyncMock(return_value=None))
    result = asyncio.run(capture.disc_screenshots(_disc_meta(tmp_path), "Disc", bad_fps, "bad-fps", str(tmp_path), False, cleanup_after_capture=False))
    assert result == []


def test_disc_screenshots_capture_overlay_smallest_registration_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, bdinfo = _disc_source(tmp_path)
    meta = _disc_meta(tmp_path, frame_overlay=True, hdr="HDR10", discs=[{"name": "disc"}])
    capture.tone_map = True
    capture.task_limit = 2
    capture.cutoff = 2
    capture.default_config = {"multiScreens": 2}
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["10", "20"]))
    monkeypatch.setattr(capture, "get_frame_info", AsyncMock(side_effect=[{"frame_type": "I"}, {"frame_type": "P"}]))
    cleanup = AsyncMock()
    monkeypatch.setattr(capture.cleanup_manager, "cleanup", cleanup)

    calls = 0

    async def capture_frame(index: int, file: str, _ss_time: str, image_path: str, *_args: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        assert file == str(source)
        _write_large(image_path, 80_000 + index * 10_000)
        return index, image_path

    monkeypatch.setattr(capture, "capture_disc_task", capture_frame)
    result = asyncio.run(capture.disc_screenshots(meta, "Disc:Title", bdinfo, meta.uuid, meta.base_dir, False))
    assert len(result) == 1
    assert calls == 2
    assert meta.tonemapped is True
    assert meta.frame_info_map["10"]["frame_type"] == "I"
    cleanup.assert_awaited_once()


def test_disc_screenshots_vapoursynth_lostimg_and_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, bdinfo = _disc_source(tmp_path)
    meta = _disc_meta(tmp_path, screens=2, imghost="lostimg", debug=False)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="lostimg"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2", "3"]))

    module = ModuleType("src.integrations.media.vapoursynth")

    def vs_screengn(*, source: str, encode: object, num: int, dir: str) -> None:
        del source, encode
        _write_large(Path(dir) / "valid.png", 80_000)
        _write_large(Path(dir) / "small.png", 10_000)
        assert num in {1, 2}

    module.vs_screengn = vs_screengn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.integrations.media.vapoursynth", module)
    result = asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, True, cleanup_after_capture=False))
    assert len(result) == 1

    second = asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, True, cleanup_after_capture=False))
    assert len(second) == 1
    reused = asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, True, cleanup_after_capture=False))
    assert reused == []


def test_disc_screenshots_retake_success_failure_and_remove_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, bdinfo = _disc_source(tmp_path)
    meta = _disc_meta(tmp_path, imghost="pixhost", screens=1, debug=False)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="pixhost"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    monkeypatch.setattr(capture, "random", SimpleNamespace(uniform=lambda *_args: 5.0))
    calls = 0

    async def retaking(index: int, _file: str, _time: str, image_path: str, *_args: object) -> tuple[int, str] | None:
        nonlocal calls
        calls += 1
        _write_large(image_path, 10_000 if calls <= 2 else 90_000)
        return index, image_path

    monkeypatch.setattr(capture, "capture_disc_task", retaking)
    result = asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, False, cleanup_after_capture=False))
    assert len(result) == 1 and calls >= 3

    failure = _disc_meta(tmp_path, imghost="unknown", screens=1, uuid="failure", debug=False)
    monkeypatch.setattr(capture, "capture_disc_task", AsyncMock(return_value=None))
    assert asyncio.run(capture.disc_screenshots(failure, "Disc", bdinfo, failure.uuid, failure.base_dir, False, cleanup_after_capture=False)) == []


def test_capture_disc_task_filters_verbose_failure_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.m2ts"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    meta = Meta(
        frame_overlay=True,
        frame_info_map={"10": {"pts_time": 9.5, "frame_type": "I"}},
        frame_rate=24.0,
        resolution="2160p",
        debug=True,
    )
    capture.default_config = {"overlay_text_size": 18}
    capture.algorithm = "mobius"
    capture.desat = 10
    commands: list[str] = []

    async def success(command: Any) -> tuple[int, bytes, bytes]:
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        output.write_bytes(b"image")
        return 0, b"stdout", b"stderr"

    monkeypatch.setattr(capture, "run_ffmpeg", success)
    result = asyncio.run(capture.capture_disc_task(0, str(source), "10", str(output), "none", "verbose", True, meta))
    assert result == (0, str(output))
    assert "Tonemapped HDR" in commands[0]

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(1, b"", b"failed")))
    assert asyncio.run(capture.capture_disc_task(0, str(source), "10", str(output), "none", "quiet", False, Meta())) is None
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=RuntimeError("boom")))
    assert asyncio.run(capture.capture_disc_task(0, str(source), "10", str(output), "none", "quiet", False, Meta())) is None


def _dvd_meta(tmp_path: Path, **values: object) -> Meta:
    disc_root = tmp_path / "dvd"
    disc_root.mkdir(exist_ok=True)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "dvd-release",
        "category": "MOVIE",
        "screens": 1,
        "image_list": [],
        "debug": True,
        "ffdebug": False,
        "retake": False,
        "frame_overlay": False,
        "frame_rate": 25.0,
        "frame_info_map": {},
        "resolution": "576p",
        "is_disc": "DVD",
        "discs": [
            {
                "name": "DISC:ONE",
                "path": str(disc_root),
                "main_set": ["01_0.IFO", "01_1.VOB", "01_2.VOB"],
            }
        ],
        "tv_pack": False,
    }
    state.update(values)
    return Meta(state)


def _ifo_tracks(*, duration: object = "10000 / 20000") -> SimpleNamespace:
    return SimpleNamespace(
        tracks=[
            SimpleNamespace(
                track_type="Video",
                duration=duration,
                pixel_aspect_ratio="1",
                display_aspect_ratio="1.777",
                width="720",
                height="576",
                frame_rate="25",
            )
        ]
    )


def test_dvd_screenshots_early_reuse_valid_capture_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture.cutoff = 1
    existing = _dvd_meta(tmp_path, image_list=[{"img_url": "https://img.invalid/a.png"}])
    asyncio.run(capture.dvd_screenshots(existing, 0))

    zero = _dvd_meta(tmp_path, screens=0)
    asyncio.run(capture.dvd_screenshots(zero, 0))

    enough = _dvd_meta(tmp_path, image_list=[{"img_url": "local"}], screens=1)
    asyncio.run(capture.dvd_screenshots(enough, 0))

    meta = _dvd_meta(tmp_path)
    existing_path = capture.screenshots_dir(meta.base_dir, meta.uuid) / "DISC_ONE-0.png"
    _write_large(existing_path)
    capture.register_screenshots(meta.base_dir, meta.uuid, [str(existing_path)], "DISC_ONE")
    asyncio.run(capture.dvd_screenshots(meta, 0, num_screens=1))

    capture.clear_screenshot_group(meta.base_dir, meta.uuid, "DISC_ONE")
    existing_path.unlink(missing_ok=True)
    parse_calls: list[str] = []

    def parse(path: str, **kwargs: object) -> object:
        parse_calls.append(path)
        if kwargs.get("output") == "JSON":
            if path.endswith("01_1.VOB"):
                return json.dumps({"media": {"track": [{"Duration": "0", "Width": None, "Height": None}]}})
            return json.dumps({"media": {"track": [{"Duration": "120", "Width": "720", "Height": "576"}]}})
        return _ifo_tracks()

    monkeypatch.setattr(capture.MediaInfo, "parse", parse)
    meta = _dvd_meta(tmp_path, uuid="dvd-existing", screens=2)
    directory = capture.screenshots_dir(meta.base_dir, meta.uuid)
    one = directory / "DISC_ONE-0.png"
    two = directory / "DISC_ONE-1.png"
    _write_large(one)
    _write_large(two)
    asyncio.run(capture.dvd_screenshots(meta, 0, num_screens=2, cleanup_after_capture=False))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    monkeypatch.setattr(capture, "get_frame_info", AsyncMock(side_effect=[{"frame_type": "I"}, {"frame_type": "P"}]))
    cleanup = AsyncMock()
    monkeypatch.setattr(capture.cleanup_manager, "cleanup", cleanup)
    capture_meta = _dvd_meta(tmp_path, uuid="dvd-capture", screens=1, frame_overlay=True)

    async def dvd_capture(task: tuple[int, str, str, str, Meta, float, float, float, float]) -> tuple[int, str]:
        index, _source, output, *_rest = task
        _write_large(output, 130_000 + index * 10_000)
        return index, output

    monkeypatch.setattr(capture, "capture_dvd_screenshot", dvd_capture)
    asyncio.run(capture.dvd_screenshots(capture_meta, 0, num_screens=1))
    assert any(path.endswith("01_2.VOB") for path in parse_calls)
    assert capture_meta.frame_info_map
    cleanup.assert_awaited_once()
    assert len(capture.manifest_files(capture_meta.base_dir, capture_meta.uuid, "DISC_ONE")) == 1


def test_dvd_screenshots_vob_fallback_retake_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _dvd_meta(tmp_path, debug=False)
    parse_count = 0

    def parse(_path: str, **kwargs: object) -> object:
        nonlocal parse_count
        if kwargs.get("output") == "JSON":
            parse_count += 1
            raise RuntimeError("invalid VOB")
        return _ifo_tracks(duration=10_000)

    monkeypatch.setattr(capture.MediaInfo, "parse", parse)
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    monkeypatch.setattr(capture.random, "uniform", lambda *_args: 3.0)
    calls = 0

    async def small_then_large(task: tuple[int, str, str, str, Meta, float, float, float, float]) -> tuple[int, str | None]:
        nonlocal calls
        calls += 1
        index, _source, output, *_rest = task
        if calls <= 2:
            _write_large(output, 10_000)
        elif calls == 3:
            return index, None
        else:
            _write_large(output, 90_000)
        return index, output

    monkeypatch.setattr(capture, "capture_dvd_screenshot", small_then_large)
    asyncio.run(capture.dvd_screenshots(meta, 0, num_screens=1, cleanup_after_capture=False))
    assert parse_count >= 6 and calls >= 4

    failure = _dvd_meta(tmp_path, uuid="dvd-failure", debug=False)
    monkeypatch.setattr(capture, "capture_dvd_screenshot", AsyncMock(side_effect=RuntimeError("capture failed")))
    asyncio.run(capture.dvd_screenshots(failure, 0, num_screens=1, cleanup_after_capture=False))


def test_capture_dvd_screenshot_scaling_overlay_duration_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.vob"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    tracks = [SimpleNamespace(track_type="Video", duration="5")]
    monkeypatch.setattr(capture.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=tracks))
    meta = Meta(
        ffdebug=True,
        debug=True,
        frame_overlay=True,
        frame_info_map={"4.0": {"pts_time": 4.5, "frame_type": "B"}},
        frame_rate=25.0,
        resolution="576p",
    )
    commands: list[str] = []

    async def success(command: Any) -> tuple[int, bytes, bytes]:
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        output.write_bytes(b"image")
        return 0, b"", b""

    monkeypatch.setattr(capture, "run_ffmpeg", success)
    result = asyncio.run(capture.capture_dvd_screenshot((1, str(source), str(output), "10", meta, 720, 576, 1.2, 0.8)))
    assert result == (1, str(output))
    assert "scale=" in commands[0] and "Frame Type" in commands[0]

    tracks[0].duration = "bad"
    output.unlink()
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(0, b"", b"")))
    assert asyncio.run(capture.capture_dvd_screenshot((1, str(source), str(output), "2", meta, 720, 576, 1, 1))) == (1, None)

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(1, b"", b"failed")))
    assert asyncio.run(capture.capture_dvd_screenshot((1, str(source), str(output), "2", meta, 720, 576, 1, 1))) == (1, None)
    monkeypatch.setattr(capture.MediaInfo, "parse", lambda _path: (_ for _ in ()).throw(RuntimeError("bad media")))
    assert asyncio.run(capture.capture_dvd_screenshot((1, str(source), str(output), "2", meta, 720, 576, 1, 1))) == (1, None)


def _screen_meta(tmp_path: Path, folder_id: str = "release", **values: object) -> Meta:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": folder_id,
        "path": str(source),
        "filelist": [str(source)],
        "category": "MOVIE",
        "audiobook": False,
        "screens": 1,
        "image_list": [],
        "imghost": "imgbb",
        "debug": True,
        "ffdebug": False,
        "retake": False,
        "frame_overlay": False,
        "frame_rate": 24.0,
        "frame_info_map": {},
        "resolution": "1080p",
        "hdr": "",
        "libplacebo": False,
        "libplacebo_warmed": False,
        "is_disc": "",
        "discs": [],
        "tv_pack": False,
    }
    state.update(values)
    return Meta(state)


def test_screenshots_category_dispatch_existing_and_mediainfo_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game = _screen_meta(tmp_path, category="GAME")
    assert asyncio.run(capture.screenshots(game.path, "Game", game.uuid, game.base_dir, game)) == []

    book = _screen_meta(tmp_path, folder_id="book", category="BOOK", audiobook=True)
    cover = AsyncMock(return_value="cover.png")
    monkeypatch.setattr(capture, "prepare_book_cover", cover)
    assert asyncio.run(capture.screenshots(book.path, "Book", book.uuid, book.base_dir, book)) == []
    cover.assert_awaited_once()

    ebook = _screen_meta(tmp_path, folder_id="ebook", category="BOOK", audiobook=False)
    monkeypatch.setattr(capture, "generate_ebook_screenshots", AsyncMock(return_value=["page.png"]))
    assert asyncio.run(capture.screenshots(ebook.path, "Book", ebook.uuid, ebook.base_dir, ebook)) == ["page.png"]

    capture.cutoff = 1
    existing = _screen_meta(tmp_path, folder_id="existing", image_list=[{"img_url": "https://img.invalid/a.png"}])
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    assert asyncio.run(capture.screenshots(existing.path, "Movie", existing.uuid, existing.base_dir, existing)) is None

    invalid = _screen_meta(tmp_path, folder_id="invalid")
    target = tmp_path / "tmp" / invalid.uuid
    target.mkdir(parents=True)
    (target / "MediaInfo.json").write_text("not json", encoding="utf-8")
    assert asyncio.run(capture.screenshots(invalid.path, "Movie", invalid.uuid, invalid.base_dir, invalid)) is None


def test_screenshots_safe_float_manual_frames_and_local_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _screen_meta(tmp_path)
    _write_media_info(
        tmp_path,
        meta.uuid,
        general={"value": "100"},
        video={
            "Duration": {"Duration": "100"},
            "Width": {"#value": "1920"},
            "Height": {"value": "1080"},
            "PixelAspectRatio": {"duration": "1"},
            "DisplayAspectRatio": {"bad": True},
            "FrameRate": ["bad"],
        },
    )
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))
    monkeypatch.setattr(capture, "capture_screenshot", AsyncMock(return_value=None))
    assert asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta, manual_frames=[24])) == []

    with pytest.raises(ScreenshotCaptureError, match="Invalid manual frame"):
        asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta, manual_frames="bad"))

    reuse = _screen_meta(tmp_path, folder_id="reuse")
    _write_media_info(tmp_path, reuse.uuid)
    screenshot_dir = capture.screenshots_dir(reuse.base_dir, reuse.uuid)
    local = screenshot_dir / "Movie-0.png"
    _write_large(local)
    assert asyncio.run(capture.screenshots(reuse.path, "Movie", reuse.uuid, reuse.base_dir, reuse, num_screens=1)) == [str(local)]


def test_screenshots_capture_overlay_success_gather_errors_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _screen_meta(tmp_path, frame_overlay=True, screens=2)
    _write_media_info(tmp_path, meta.uuid)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="ptscreens"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    monkeypatch.setattr(capture, "get_frame_info", AsyncMock(side_effect=[{"frame_type": "I"}, {"frame_type": "P"}]))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))
    cleanup = AsyncMock()
    monkeypatch.setattr(capture.cleanup_manager, "cleanup", cleanup)

    async def make(index_args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str]:
        index, _path, _time, output, *_rest = index_args
        _write_large(output, 90_000)
        return index, output

    monkeypatch.setattr(capture, "capture_screenshot", make)
    result = asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta))
    assert len(result or []) == 2 and meta.frame_info_map
    cleanup.assert_awaited_once()

    broken = _screen_meta(tmp_path, folder_id="broken")
    _write_media_info(tmp_path, broken.uuid)

    async def fail_gather(*awaitables: object, **_kwargs: object) -> list[object]:
        for awaitable in awaitables:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        raise RuntimeError("gather failed")

    monkeypatch.setattr(capture.asyncio, "gather", fail_gather)
    with pytest.raises(ScreenshotCaptureError, match="Screenshot capture failed"):
        asyncio.run(capture.screenshots(broken.path, "Movie", broken.uuid, broken.base_dir, broken))


def test_screenshots_retake_success_exhaustion_manual_lostimg_and_random_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbox"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))
    meta = _screen_meta(tmp_path, folder_id="retake")
    _write_media_info(tmp_path, meta.uuid)
    calls = 0

    async def small_then_large(args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str] | None:
        nonlocal calls
        calls += 1
        index, _path, _time, output, *_rest = args
        _write_large(output, 10_000 if calls == 1 else 90_000)
        return index, output

    monkeypatch.setattr(capture, "capture_screenshot", small_then_large)
    result = asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta, cleanup_after_capture=False))
    assert len(result or []) == 1 and calls == 2

    exhausted = _screen_meta(tmp_path, folder_id="exhausted", imghost="unknown")
    _write_media_info(tmp_path, exhausted.uuid)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(capture, "capture_screenshot", small_then_large)
    assert asyncio.run(capture.screenshots(exhausted.path, "Movie", exhausted.uuid, exhausted.base_dir, exhausted, cleanup_after_capture=False)) == []

    manual = _screen_meta(tmp_path, folder_id="manual-lost", imghost="lostimg")
    _write_media_info(tmp_path, manual.uuid)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="lostimg"))

    async def small_manual(args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str]:
        index, _path, _time, output, *_rest = args
        _write_large(output, 10_000)
        return index, output

    monkeypatch.setattr(capture, "capture_screenshot", small_manual)
    assert asyncio.run(capture.screenshots(manual.path, "Movie", manual.uuid, manual.base_dir, manual, manual_frames=[24], cleanup_after_capture=False)) == []


def test_capture_screenshot_invalid_paths_directory_and_basic_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "frame.png"
    meta = Meta(filelist=[], debug=False, frame_overlay=False)
    assert asyncio.run(capture.capture_screenshot((0, "missing", 1, str(output), 0, 1080, 1, 1, "quiet", False, meta))) is None
    assert asyncio.run(capture.capture_screenshot((0, "missing", -1, str(output), 1920, 1080, 1, 1, "quiet", False, meta))) is None
    assert asyncio.run(capture.capture_screenshot((0, str(tmp_path), 1, str(output), 1920, 1080, 1, 1, "quiet", False, meta))) is None
    assert asyncio.run(capture.capture_screenshot((0, str(tmp_path / "not-there.mkv"), 1, str(output), 1920, 1080, 1, 1, "quiet", False, meta))) is None

    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    meta.filelist = [str(source)]
    commands: list[str] = []

    async def success(command: Any) -> tuple[int, bytes, bytes]:
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        output.write_bytes(b"image")
        return 0, b"stdout", b"stderr"

    capture.ffmpeg_limit = True
    capture.use_libplacebo = False
    monkeypatch.setattr(capture, "run_ffmpeg", success)
    result = asyncio.run(capture.capture_screenshot((2, str(tmp_path), 1, str(output), 1920, 1080, 1.2, 0.8, "verbose", True, meta)))
    assert result == (2, str(output))
    assert "zscale=transfer=linear" in commands[0]
    assert "scale=" in commands[0] and "-threads 1" in commands[0]


def test_capture_screenshot_libplacebo_warmup_retry_fallback_and_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    capture.use_libplacebo = True
    capture.default_config = {"ffmpeg_warmup": True}
    meta = Meta(libplacebo=True, libplacebo_warmed=False, frame_overlay=False, debug=True)
    warmup = AsyncMock()
    monkeypatch.setattr(capture, "libplacebo_warmup", warmup)
    commands: list[str] = []
    calls = 0

    async def sequence(command: Any) -> tuple[int, bytes, bytes]:
        nonlocal calls
        calls += 1
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        if calls == 3:
            output.write_bytes(b"image")
            return 0, b"fallback stdout", b"fallback stderr"
        return 1, b"", b"libplacebo failed"

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(capture, "run_ffmpeg", sequence)
    monkeypatch.setattr(capture.asyncio, "sleep", no_sleep)
    result = asyncio.run(capture.capture_screenshot((0, str(source), 1, str(output), 1920, 1080, 1, 1, "verbose", True, meta)))
    assert result == (0, str(output)) and calls == 3
    assert meta.libplacebo is False
    assert "libplacebo=" in commands[0] and "zscale=" in commands[2]
    warmup.assert_awaited_once()

    output.unlink()

    async def timeout_wait(awaitable: object, **_kwargs: object) -> object:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(capture.asyncio, "wait_for", timeout_wait)
    meta = Meta(libplacebo=False, frame_overlay=False, debug=True)
    assert asyncio.run(capture.capture_screenshot((1, str(source), 1, str(output), 1920, 1080, 1, 1, "quiet", False, meta))) == (1, None)


def test_capture_screenshot_overlay_pts_compile_failure_complex_error_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "overlay.png"
    capture.default_config = {"overlay_text_size": 20}
    meta = Meta(
        frame_overlay=True,
        frame_info_map={"2.0": {"pts_time": 2.5, "frame_type": "B"}},
        frame_rate=25.0,
        resolution=None,
        debug=True,
        libplacebo=False,
    )
    commands: list[str] = []

    async def success(command: Any) -> tuple[int, bytes, bytes]:
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        output.write_bytes(b"image")
        return 0, b"overlay stdout", b"overlay stderr"

    monkeypatch.setattr(capture, "run_ffmpeg", success)
    result = asyncio.run(capture.capture_screenshot((0, str(source), 2.0, str(output), 1920, 1080, 1, 1, "verbose", True, meta)))
    assert result == (0, str(output))
    assert "Frame Number" in commands[0] and "Tonemapped HDR" in commands[0]

    output.unlink()
    original_compile = capture.compile_ffmpeg_command
    monkeypatch.setattr(capture, "compile_ffmpeg_command", lambda _command: (_ for _ in ()).throw(RuntimeError("compile failed")))
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(1, b"", b"Error initializing complex filters")))
    assert asyncio.run(capture.capture_screenshot((0, str(source), 2.0, str(output), 1920, 1080, 1, 1, "verbose", False, Meta(debug=True)))) == (0, None)
    monkeypatch.setattr(capture, "compile_ffmpeg_command", original_compile)

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(capture.capture_screenshot((0, str(source), 2.0, str(output), 1920, 1080, 1, 1, "quiet", False, Meta())))

    bad = Meta(frame_overlay=True, frame_info_map={}, resolution="invalid", debug=True)
    assert asyncio.run(capture.capture_screenshot((0, str(source), 2.0, str(output), 1920, 1080, 1, 1, "quiet", False, bad))) is None


def test_valid_ss_time_categories_disc_offset_and_single_screen() -> None:
    tv = Meta(category="TV", is_disc="", retake_call_count=None)
    times = asyncio.run(capture.valid_ss_time(["0"], 2, 100, 10, tv, retake=True))
    assert len(times) == 3 and tv.retake_call_count == 1

    movie = Meta(category="Movie", is_disc="", retake_call_count=39)
    times = asyncio.run(capture.valid_ss_time([], 1, 100, 10, movie, retake=True))
    assert len(times) == 1 and movie.retake_call_count == 40

    disc = Meta(category="MOVIE", is_disc="BDMV")
    assert len(asyncio.run(capture.valid_ss_time([], 2, 100, 10, disc))) == 3


def test_get_frame_info_patterns_none_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    meta = Meta(frame_rate=25.0)

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(0, b"", b"showinfo pict_type:I pts_time:2.500")))
    result = asyncio.run(capture.get_frame_info(str(source), "2", meta))
    assert result == {"frame_type": "I", "frame_number": 62, "pts_time": 2.5}

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(0, b"", b"showinfo type:P ")))
    assert asyncio.run(capture.get_frame_info(str(source), "2", meta))["frame_type"] == "P"

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(None, b"", b"")))
    fallback = asyncio.run(capture.get_frame_info(str(source), "2", meta))
    assert fallback == {"frame_type": "Unknown", "frame_number": 50}

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=RuntimeError("ffmpeg failed")))
    assert asyncio.run(capture.get_frame_info(str(source), "2", Meta(frame_rate=None))) == {"frame_type": "Unknown", "frame_number": 48}


def test_libplacebo_compatibility_all_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    meta = Meta(is_disc="", debug=True)
    calls = 0

    async def compatible(_command: Any) -> tuple[int, bytes, bytes]:
        nonlocal calls
        calls += 1
        test_path = Path(str(output).replace(".png", "_test.png"))
        test_path.write_bytes(b"image")
        return 0, b"", b""

    monkeypatch.setattr(capture, "run_ffmpeg", compatible)
    assert asyncio.run(capture.check_libplacebo_compatibility(1.2, 0.8, 1920, 1080, str(source), "1", str(output), "verbose", meta)) == (True, True)
    assert calls == 1 and not Path(str(output).replace(".png", "_test.png")).exists()

    outcomes = iter(((1, b"", b""), (0, b"", b"")))
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=lambda _command: next(outcomes)))
    assert asyncio.run(capture.check_libplacebo_compatibility(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", meta)) == (False, True)

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(1, b"", b"")))
    assert asyncio.run(capture.check_libplacebo_compatibility(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", meta)) == (False, False)
    assert asyncio.run(capture.check_libplacebo_compatibility(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", Meta(is_disc="BDMV"))) == (False, False)

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=RuntimeError("failed")))
    assert asyncio.run(capture.check_libplacebo_compatibility(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", meta)) == (False, False)


def test_determine_tonemapping_and_warmup_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"

    capture.tone_map = False
    assert not asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", Meta(hdr="HDR")))

    capture.tone_map = True
    capture.use_libplacebo = True
    capture.ffmpeg_is_good = True
    good = Meta(hdr="HDR", frame_overlay=False)
    assert asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", good))
    assert good.libplacebo and good.tonemapped

    capture.ffmpeg_is_good = False
    monkeypatch.setattr(capture, "check_libplacebo_compatibility", AsyncMock(return_value=(False, False)))
    incompatible = Meta(hdr="DV", frame_overlay=False)
    assert not asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", incompatible))

    overlay = Meta(hdr="HLG", frame_overlay=True)
    assert asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", overlay))
    assert overlay.tonemapped and not overlay.libplacebo

    assert not asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", Meta(hdr="SDR")))

    assert asyncio.run(capture.libplacebo_warmup(str(source), Meta(libplacebo=False), "quiet")) is None
    assert asyncio.run(capture.libplacebo_warmup(str(source), Meta(libplacebo=True, libplacebo_warmed=True), "quiet")) is None
    assert asyncio.run(capture.libplacebo_warmup(str(tmp_path / "missing"), Meta(libplacebo=True), "quiet")) is None

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(return_value=(0, b"", b"")))
    warmed = Meta(libplacebo=True, libplacebo_warmed=False, debug=True)
    asyncio.run(capture.libplacebo_warmup(str(source), warmed, "verbose"))
    assert warmed.libplacebo_warmed

    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=RuntimeError("warmup failed")))
    warmed = Meta(libplacebo=True, libplacebo_warmed=False, debug=True)
    asyncio.run(capture.libplacebo_warmup(str(source), warmed, "verbose"))
    assert warmed.libplacebo_warmed


def test_run_ffmpeg_arm_and_bundled_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "frame.png"
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    candidate = Path(capture.__file__).resolve().parent.parent / "bin" / "ffmpeg" / "arm" / "ffmpeg"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"binary")
    candidate.chmod(0o755)
    captured: list[tuple[object, ...]] = []

    async def create(*args: object, **_kwargs: object) -> SimpleNamespace:
        captured.append(args)

        async def communicate() -> tuple[bytes, bytes]:
            return b"", b""

        return SimpleNamespace(returncode=0, communicate=communicate)

    monkeypatch.setattr(capture, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capture.platform, "system", lambda: "Linux")
    monkeypatch.setattr(capture.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(capture.asyncio, "create_subprocess_exec", create)
    command = capture.ffmpeg.input(str(source)).output(str(output), vframes=1)
    try:
        assert asyncio.run(capture.run_ffmpeg(command))[0] == 0
        assert captured[0][0] == str(candidate)
    finally:
        candidate.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            candidate.parent.rmdir()
        with contextlib.suppress(OSError):
            candidate.parent.parent.rmdir()


def test_disc_screenshot_error_result_remove_failure_and_exhaustion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _source, bdinfo = _disc_source(tmp_path)
    meta = _disc_meta(tmp_path, uuid="disc-errors", screens=1, imghost="unknown", debug=False)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    calls = 0

    async def errors(index: int, _file: str, _time: str, image_path: str, *_args: object) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return index, "Error generated by capture"
        _write_large(image_path, 10_000)
        return index, image_path

    monkeypatch.setattr(capture, "capture_disc_task", errors)
    original_getsize = os.path.getsize
    monkeypatch.setattr(capture.os.path, "getsize", lambda _path: (_ for _ in ()).throw(OSError("size failed")))
    result = asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, False, cleanup_after_capture=False))
    assert result == []

    meta = _disc_meta(tmp_path, uuid="disc-exhaust", screens=1, imghost="imgbox", debug=False)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbox"))
    monkeypatch.setattr(capture.os.path, "getsize", original_getsize)
    attempts = 0

    async def fail_retakes(index: int, _file: str, _time: str, image_path: str, *_args: object) -> tuple[int, str]:
        nonlocal attempts
        attempts += 1
        if attempts > 2:
            raise RuntimeError("retake failed")
        _write_large(image_path, 10_000)
        return index, image_path

    monkeypatch.setattr(capture, "capture_disc_task", fail_retakes)
    assert asyncio.run(capture.disc_screenshots(meta, "Disc", bdinfo, meta.uuid, meta.base_dir, False, cleanup_after_capture=False)) == []
    assert attempts >= 3


def test_dvd_existing_files_error_strings_delete_failure_and_exhaustion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def parse(_path: str, **kwargs: object) -> object:
        if kwargs.get("output") == "JSON":
            return json.dumps({"media": {"track": [{"Duration": "120", "Width": "720", "Height": "576"}]}})
        return _ifo_tracks()

    monkeypatch.setattr(capture.MediaInfo, "parse", parse)
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    error_meta = _dvd_meta(tmp_path, uuid="dvd-error-string", screens=1, debug=False)
    monkeypatch.setattr(capture, "capture_dvd_screenshot", AsyncMock(return_value=(0, "Error from DVD capture")))
    asyncio.run(capture.dvd_screenshots(error_meta, 0, num_screens=1, cleanup_after_capture=False))

    failure = _dvd_meta(tmp_path, uuid="dvd-retake-failure", screens=1, debug=False)
    attempts = 0

    async def always_small(task: tuple[int, str, str, str, Meta, float, float, float, float]) -> tuple[int, str]:
        nonlocal attempts
        attempts += 1
        index, _source, output, *_rest = task
        _write_large(output, 10_000)
        return index, output

    monkeypatch.setattr(capture, "capture_dvd_screenshot", always_small)
    original_unlink = Path.unlink
    delete_calls = 0

    def fail_delete(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls > 1 and path.name.startswith("DISC_ONE"):
            raise OSError("delete failed")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_delete)
    asyncio.run(capture.dvd_screenshots(failure, 0, num_screens=1, cleanup_after_capture=False))
    assert attempts >= 1
    monkeypatch.setattr(Path, "unlink", original_unlink)

    exception = _dvd_meta(tmp_path, uuid="dvd-retake-exception", screens=1, debug=False)
    calls = 0

    async def initial_then_error(task: tuple[int, str, str, str, Meta, float, float, float, float]) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        index, _source, output, *_rest = task
        if calls == 1:
            _write_large(output, 10_000)
            return index, output
        raise RuntimeError("retake exception")

    monkeypatch.setattr(capture, "capture_dvd_screenshot", initial_then_error)
    asyncio.run(capture.dvd_screenshots(exception, 0, num_screens=1, cleanup_after_capture=False))
    assert calls >= 2


def test_capture_dvd_no_filters_format_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.vob"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    monkeypatch.setattr(capture.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[]))
    commands: list[str] = []

    async def success(command: Any) -> tuple[int, bytes, bytes]:
        commands.append(" ".join(capture.compile_ffmpeg_command(command)))
        output.write_bytes(b"image")
        return 0, b"", b""

    monkeypatch.setattr(capture, "run_ffmpeg", success)
    result = asyncio.run(capture.capture_dvd_screenshot((0, str(source), str(output), "1", Meta(frame_overlay=False), 720, 576, 1, 1)))
    assert result == (0, str(output)) and "format=rgb24" in commands[0]


def test_audiobook_fallback_no_cover_direct_path_skips_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mutagen
    import mutagen.mp4

    audio = tmp_path / "book.m4b"
    audio.write_bytes(b"audio")
    output = tmp_path / "cover.bin"
    monkeypatch.setattr(mutagen, "File", lambda _path: (_ for _ in ()).throw(ValueError("broken chapters")))
    monkeypatch.setattr(mutagen.mp4, "Atoms", lambda _fileobj: object())
    monkeypatch.setattr(mutagen.mp4, "MP4Tags", lambda _atoms, _fileobj: {})
    assert not asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(path=str(audio), filelist=[]), str(output)))

    monkeypatch.setattr(mutagen.mp4, "MP4Tags", lambda *_args: (_ for _ in ()).throw(RuntimeError("fallback failed")))
    assert not asyncio.run(capture.extract_embedded_cover_from_audiobook(Meta(filelist=[str(audio), str(tmp_path / "missing.mp3"), str(tmp_path / "skip.txt")]), str(output)))


def test_artwork_invalid_written_file_epub_parent_resolution_and_comic_jpg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artwork = tmp_path / "artwork.png"
    meta = Meta(artwork_url="https://host.invalid/image.png")
    monkeypatch.setattr(capture, "is_public_http_url", lambda _url: True)
    monkeypatch.setattr(capture, "is_valid_image_bytes", lambda _data: True)
    monkeypatch.setattr(capture, "is_valid_cover_image", lambda _path: False)

    class Response:
        status_code = 200
        is_redirect = False
        headers: ClassVar[dict[str, str]] = {}
        content = b"image"

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    assert not asyncio.run(capture.download_artwork_from_meta(meta, str(artwork), force=True))

    epub = tmp_path / "parent.epub"
    _write_epub(
        epub,
        {
            "OPS/sub/book.opf": '<package><manifest><item id="cover" href="../../../images/cover.jpg" media-type="image/jpeg"/></manifest></package>',
            "images/cover.jpg": b"cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(epub), str(artwork)))

    id_cover = tmp_path / "id-cover.epub"
    _write_epub(
        id_cover,
        {
            "book.opf": '<package><manifest><item id="cover" href="cover.jpg" media-type="application/octet-stream"/></manifest></package>',
            "cover.jpg": b"id-cover",
        },
    )
    assert asyncio.run(capture.extract_epub_cover(str(id_cover), str(artwork)))

    malformed = tmp_path / "malformed.epub"
    malformed.write_bytes(b"not zip")
    assert not asyncio.run(capture.extract_epub_cover(str(malformed), str(artwork)))

    jpg = tmp_path / "page.jpg"
    from PIL import Image

    Image.new("RGB", (4, 4), "red").save(jpg)
    cbr = tmp_path / "comic.cbr"
    with zipfile.ZipFile(cbr, "w") as archive:
        archive.write(jpg, "page1.jpg")
    assert asyncio.run(capture.extract_document_cover(str(cbr), str(artwork)))


def test_prepare_epub_downloaded_artwork_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"book")
    monkeypatch.setattr(capture, "is_valid_cover_image", lambda _path: False)
    monkeypatch.setattr(capture, "extract_epub_cover", AsyncMock(return_value=False))
    monkeypatch.setattr(capture, "download_artwork_from_meta", AsyncMock(return_value=True))
    meta = Meta(audiobook=False, artwork_path="")
    result = asyncio.run(capture.prepare_book_cover(str(epub), "downloaded", str(tmp_path), meta))
    assert result and meta.artwork_path == result


def test_generate_epub_cover_success_and_cached_banner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fitz

    epub = tmp_path / "book.epub"
    epub.write_bytes(b"book")
    meta = Meta(base_dir=str(tmp_path), uuid="epub-success", retake=False, artwork_path="")
    banner = capture.artwork_dir(meta.base_dir, meta.uuid) / "POSTER_BANNER.png"
    banner.write_bytes(b"banner")
    cover = capture.artwork_dir(meta.base_dir, meta.uuid) / "POSTER.png"

    class Page:
        def get_pixmap(self, *, matrix: object) -> object:
            del matrix
            return SimpleNamespace(save=lambda path: Path(path).write_bytes(b"page"))

    class Document:
        def __len__(self) -> int:
            return 1

        def __getitem__(self, _index: int) -> Page:
            return Page()

        def close(self) -> None:
            return None

    monkeypatch.setattr(fitz, "open", lambda *_args, **_kwargs: Document())

    async def extract(_path: str, output: str, **_kwargs: object) -> bool:
        Path(output).write_bytes(b"cover")
        return True

    monkeypatch.setattr(capture, "prepare_book_cover", AsyncMock(return_value=None))
    monkeypatch.setattr(capture, "extract_epub_cover", extract)
    pages = asyncio.run(capture.generate_ebook_screenshots(str(epub), "Book", meta.uuid, meta.base_dir, meta, num_screens=1))
    assert pages and cover.is_file() and meta.artwork_banner_path == str(banner)


def test_screenshots_remaining_result_exception_keyboard_random_and_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _screen_meta(tmp_path, folder_id="registered", screens=1)
    _write_media_info(tmp_path, meta.uuid)
    registered = capture.screenshots_dir(meta.base_dir, meta.uuid) / "registered.png"
    _write_large(registered)
    capture.register_screenshots(meta.base_dir, meta.uuid, [str(registered)], "main")
    assert asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta))

    values = _screen_meta(tmp_path, folder_id="safe-float", screens=1)
    _write_media_info(tmp_path, values.uuid, general="bad", video={"Duration": "bad", "Width": 1920, "Height": 1080})
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))
    monkeypatch.setattr(capture, "capture_screenshot", AsyncMock(return_value=None))
    assert asyncio.run(capture.screenshots(values.path, "Movie", values.uuid, values.base_dir, values, manual_frames=[1])) == []

    exception_meta = _screen_meta(tmp_path, folder_id="result-exception", screens=1)
    _write_media_info(tmp_path, exception_meta.uuid)
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    monkeypatch.setattr(capture, "capture_screenshot", AsyncMock(side_effect=RuntimeError("child failed")))
    assert asyncio.run(capture.screenshots(exception_meta.path, "Movie", exception_meta.uuid, exception_meta.base_dir, exception_meta)) == []

    cancel_meta = _screen_meta(tmp_path, folder_id="keyboard", screens=1)
    _write_media_info(tmp_path, cancel_meta.uuid)
    real_gather = capture.asyncio.gather

    async def keyboard(*awaitables: object, **_kwargs: object) -> list[object]:
        for awaitable in awaitables:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        raise KeyboardInterrupt

    monkeypatch.setattr(capture.asyncio, "gather", keyboard)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(capture.screenshots(cancel_meta.path, "Movie", cancel_meta.uuid, cancel_meta.base_dir, cancel_meta))
    monkeypatch.setattr(capture.asyncio, "gather", real_gather)

    random_meta = _screen_meta(tmp_path, folder_id="random-retake", screens=1, imghost="imgbox", retake=True)
    _write_media_info(tmp_path, random_meta.uuid)
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    calls = 0

    async def renamed(args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str] | object:
        nonlocal calls
        calls += 1
        index, _path, _time, output, *_rest = args
        target = Path(output).with_name("Movie-9.png")
        if calls == 1:
            _write_large(target, 10_000)
            return index, str(target)
        if calls == 2:
            return object()
        if calls == 3:
            return index, str(target.with_name("missing.png"))
        if calls == 4:
            raise RuntimeError("retake failed")
        _write_large(target, 90_000)
        return index, str(target)

    monkeypatch.setattr(capture, "capture_screenshot", renamed)
    monkeypatch.setattr(capture.random, "uniform", lambda *_args: 5.0)
    result = asyncio.run(
        capture.screenshots(
            random_meta.path,
            "Movie",
            random_meta.uuid,
            random_meta.base_dir,
            random_meta,
            force_screenshots=True,
            cleanup_after_capture=False,
        )
    )
    assert result and calls >= 5


def test_libplacebo_zscale_cleanup_and_determine_non_hardware(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    output = tmp_path / "frame.png"
    test_output = Path(str(output).replace(".png", "_test.png"))
    test_output.write_bytes(b"existing")
    outcomes = iter(((1, b"", b""), (0, b"", b"")))
    monkeypatch.setattr(capture, "run_ffmpeg", AsyncMock(side_effect=lambda _command: next(outcomes)))
    assert asyncio.run(capture.check_libplacebo_compatibility(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", Meta(is_disc=""))) == (False, True)
    assert not test_output.exists()

    capture.tone_map = True
    capture.use_libplacebo = False
    meta = Meta(hdr="HDR", frame_overlay=False)
    assert asyncio.run(capture.determine_tonemapping(1, 1, 1920, 1080, str(source), "1", str(output), "quiet", meta))


def test_dvd_retake_remains_small_until_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _dvd_meta(tmp_path, uuid="dvd-small-exhaust", screens=1, debug=False)

    def parse(_path: str, **kwargs: object) -> object:
        if kwargs.get("output") == "JSON":
            return json.dumps({"media": {"track": [{"Duration": "120", "Width": "720", "Height": "576"}]}})
        return _ifo_tracks()

    monkeypatch.setattr(capture.MediaInfo, "parse", parse)
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1", "2"]))
    calls = 0

    async def always_small(task: tuple[int, str, str, str, Meta, float, float, float, float]) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        index, _source, output, *_rest = task
        _write_large(output, 10_000)
        return index, output

    monkeypatch.setattr(capture, "capture_dvd_screenshot", always_small)
    asyncio.run(capture.dvd_screenshots(meta, 0, num_screens=1, cleanup_after_capture=False))
    assert calls >= 5


def test_screenshots_default_request_and_existing_remote_zero_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _screen_meta(tmp_path, folder_id="default-request", screens=1)
    _write_media_info(tmp_path, meta.uuid)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))

    async def make(args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str]:
        index, _path, _time, output, *_rest = args
        _write_large(output)
        return index, output

    monkeypatch.setattr(capture, "capture_screenshot", make)
    assert asyncio.run(capture.screenshots(meta.path, "Movie", meta.uuid, meta.base_dir, meta))

    remote = _screen_meta(
        tmp_path,
        folder_id="remote-zero",
        screens=1,
        image_list=[{"img_url": "https://img.invalid/existing.png"}],
    )
    capture.cutoff = 99
    _write_media_info(tmp_path, remote.uuid)
    assert asyncio.run(capture.screenshots(remote.path, "Movie", remote.uuid, remote.base_dir, remote)) is None


def test_screenshots_empty_manual_frame_list_uses_default_screen_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _screen_meta(tmp_path, folder_id="empty-manual-list", screens=1)
    _write_media_info(tmp_path, meta.uuid)
    monkeypatch.setattr(capture, "get_image_host", AsyncMock(return_value="imgbb"))
    monkeypatch.setattr(capture, "valid_ss_time", AsyncMock(return_value=["1"]))
    monkeypatch.setattr(capture, "determine_tonemapping", AsyncMock(return_value=False))

    async def make(args: tuple[int, str, float, str, float, float, float, float, str, bool, Meta]) -> tuple[int, str]:
        index, _path, _time, output, *_rest = args
        _write_large(output)
        return index, output

    monkeypatch.setattr(capture, "capture_screenshot", make)
    result = asyncio.run(
        capture.screenshots(
            meta.path,
            "Movie",
            meta.uuid,
            meta.base_dir,
            meta,
            manual_frames=[],
            cleanup_after_capture=False,
        )
    )
    assert len(result or []) == 1
