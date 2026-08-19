from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.media import disc_menus
from src.integrations.media.disc_menus import DiscMenus


class _Uploader:
    calls: ClassVar[list[list[str | Path]]] = []

    def __init__(self, _config: dict[str, Any] | None = None) -> None:
        pass

    async def upload_screens(self, _meta: Meta, **kwargs: Any) -> tuple[list[dict[str, str]], int]:
        files = list(kwargs.get("custom_img_list", []))
        type(self).calls.append(files)
        uploaded = [
            {
                "img_url": f"thumb:{Path(path).name}",
                "raw_url": f"raw:{Path(path).name}",
                "web_url": f"web:{Path(path).name}",
            }
            for path in files
        ]
        return uploaded, len(uploaded)

    @classmethod
    def reset(cls) -> None:
        cls.calls = []


class _Track:
    def __init__(
        self,
        *,
        track_type: str = "Video",
        width: object = 720,
        height: object = 480,
        par: object = 1.0,
        dar: object = 1.3333,
        duration: object = 1000,
    ) -> None:
        self.track_type = track_type
        self.width = width
        self.height = height
        self.pixel_aspect_ratio = par
        self.display_aspect_ratio = dar
        self.duration = duration


class _Process:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stderr: bytes = b"",
        callback: Any = None,
    ) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.callback = callback
        self.calls = 0
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.calls += 1
        if self.callback is not None:
            self.callback(self.calls)
        return b"stdout", self.stderr

    def kill(self) -> None:
        self.killed = True


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "disc-menu",
        "path_to_menu_screenshots": "",
        "discs": [],
        "menu_images": [],
    }
    state.update(values)
    return Meta(state)


def _large_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 50001)
    return path


def _png(path: Path, value: int = 255) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (value, value, value)).save(path)


def test_select_evenly_spaced_and_discard_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert disc_menus.select_evenly_spaced([1, 2], 3) == [1, 2]
    assert disc_menus.select_evenly_spaced([1, 2, 3], 0) == []
    assert disc_menus.select_evenly_spaced([1, 2, 3], 1) == [1]
    assert disc_menus.select_evenly_spaced(list(range(10)), 4) == [0, 3, 6, 9]

    first = tmp_path / "DVD-VTS_01_0-001.png"
    second = tmp_path / "DVD-VTS_01_0-002.png"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    original_unlink = Path.unlink

    def flaky_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.endswith("002.png"):
            raise OSError("read only")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    disc_menus.discard_previous_menu_capture_files(tmp_path / "DVD-VTS_01_0-%03d.png")
    assert not first.exists() and second.exists()


def test_get_disc_menu_images_dispatches_all_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    meta = _meta(tmp_path)
    manager = DiscMenus(meta, {"DEFAULT": {}})
    manager.auto_capture_dvd_menus = AsyncMock()
    manager.get_local_images = AsyncMock()
    asyncio.run(manager.get_disc_menu_images(meta))
    manager.auto_capture_dvd_menus.assert_not_awaited()

    manager = DiscMenus(meta, {"DEFAULT": {"auto_dvd_menus": True}})
    manager.auto_capture_dvd_menus = AsyncMock()
    asyncio.run(manager.get_disc_menu_images(meta))
    manager.auto_capture_dvd_menus.assert_awaited_once_with(meta)

    local = tmp_path / "menus"
    local.mkdir()
    meta.path_to_menu_screenshots = str(local)
    manager = DiscMenus(meta, {"DEFAULT": {}})
    manager.get_local_images = AsyncMock()
    asyncio.run(manager.get_disc_menu_images(meta))
    manager.get_local_images.assert_awaited_once_with(meta)

    meta.path_to_menu_screenshots = str(tmp_path / "missing")
    asyncio.run(DiscMenus(meta, {"DEFAULT": {}}).get_disc_menu_images(meta))


def test_local_images_and_json_persistence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    _Uploader.reset()
    local = tmp_path / "menus"
    local.mkdir()
    meta = _meta(tmp_path, path_to_menu_screenshots=str(local))
    manager = DiscMenus(meta, {"DEFAULT": {}})
    asyncio.run(manager.get_local_images(meta))
    assert meta.menu_images == []

    for name in ("one.png", "two.JPG", "three.webp", "skip.txt"):
        (local / name).write_bytes(b"x")
    asyncio.run(manager.get_local_images(meta))
    assert len(meta.menu_images) == 3
    saved = tmp_path / "tmp" / "disc-menu" / "menu_images.json"
    assert saved.is_file() and '"menu_images"' in saved.read_text()
    asyncio.run(manager.save_images_to_json(meta, []))


def test_auto_capture_unsupported_invalid_paths_and_scan_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    manager = DiscMenus(_meta(tmp_path), {"DEFAULT": {}})
    asyncio.run(manager.auto_capture_dvd_menus(_meta(tmp_path, discs=[])))

    missing = tmp_path / "missing"
    meta = _meta(
        tmp_path,
        discs=[
            {"type": "HDDVD", "name": "HD"},
            {"type": "BDMV", "name": "BD"},
            {"type": "DVD", "name": "Missing", "path": str(missing)},
        ],
    )
    asyncio.run(manager.auto_capture_dvd_menus(meta))

    dvd = tmp_path / "dvd"
    dvd.mkdir()
    meta = _meta(tmp_path, discs=[{"type": "DVD", "name": "Broken", "path": str(dvd)}])
    original_iterdir = Path.iterdir

    def fail_iterdir(path: Path):
        if path == dvd:
            raise OSError("cannot scan")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    asyncio.run(manager.auto_capture_dvd_menus(meta))


def test_auto_capture_static_motion_fallback_retry_filter_limit_and_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    _Uploader.reset()
    dvd = tmp_path / "VIDEO_TS"
    static = _large_file(dvd / "VIDEO_TS.VOB")
    motion = _large_file(dvd / "VTS_01_0.VOB")
    no_video = _large_file(dvd / "VTS_02_0.VOB")
    bad_info = _large_file(dvd / "VTS_03_0.VOB")
    failed = _large_file(dvd / "VTS_04_0.VOB")
    exploding = _large_file(dvd / "VTS_05_0.VOB")
    _large_file(dvd / "VTS_06_1.VOB")
    (dvd / "VTS_07_0.VOB").write_bytes(b"small")

    def parse(path: Path):
        if path == no_video:
            return SimpleNamespace(tracks=[_Track(track_type="Audio")])
        if path == bad_info:
            raise RuntimeError("mediainfo failed")
        if path == motion:
            return SimpleNamespace(tracks=[_Track(width="720", height="480", par="1.2", dar="1.8", duration="5000")])
        if path == failed:
            return SimpleNamespace(tracks=[_Track(duration="1000")])
        if path == exploding:
            return SimpleNamespace(tracks=[_Track(duration="1000")])
        return SimpleNamespace(tracks=[_Track(width=None, height=None, par=None, dar=None, duration="1000")])

    monkeypatch.setattr(disc_menus.MediaInfo, "parse", parse)
    monkeypatch.setattr(disc_menus, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(disc_menus.platform, "system", lambda: "Linux")
    monkeypatch.setattr(disc_menus.platform, "machine", lambda: "x86_64")
    ffmpeg = tmp_path / "bin" / "ffmpeg" / "amd" / "ffmpeg"
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.write_bytes(b"binary")
    monkeypatch.setattr(disc_menus, "should_scale_screenshots_for_par", lambda _config: True)
    monkeypatch.setattr(disc_menus, "screenshot_par_scale_factors", lambda *_args: (1.201, 0.801))

    output = tmp_path / "tmp" / "disc-menu" / "menu_screenshots"

    def create_static(_calls: int) -> None:
        _png(output / "DVD_Name-VIDEO_TS-001.png", 0)
        _png(output / "DVD_Name-VIDEO_TS-002.png", 255)
        (output / "DVD_Name-VIDEO_TS-003.png").write_bytes(b"corrupt")

    def create_bad_info(_calls: int) -> None:
        _png(output / "DVD_Name-VTS_03_0-001.png", 255)

    def create_retry(_calls: int) -> None:
        _png(output / "DVD_Name-VTS_01_0-001.png", 0)
        (output / "DVD_Name-VTS_01_0-002.png").write_bytes(b"corrupt")

    processes = iter(
        [
            _Process(callback=create_static),
            _Process(),
            _Process(returncode=1, stderr=b"fallback failed"),
            _Process(callback=create_retry),
            _Process(callback=create_bad_info),
            _Process(returncode=1, stderr=b"ffmpeg failed"),
        ]
    )

    async def subprocess_factory(*args: object, **_kwargs: object) -> _Process:
        if str(args[-1]).endswith("VTS_05_0-%03d.png"):
            raise RuntimeError("spawn failed")
        return next(processes)

    monkeypatch.setattr(disc_menus.asyncio, "create_subprocess_exec", subprocess_factory)
    meta = _meta(tmp_path, discs=[{"type": "DVD", "name": "DVD:Name", "path": str(dvd)}])
    manager = DiscMenus(meta, {"DEFAULT": {"max_menu_screens": "2", "scale_screenshots_for_par": True}})
    asyncio.run(manager.auto_capture_dvd_menus(meta))
    assert len(meta.menu_images) == 2
    assert _Uploader.calls and len(_Uploader.calls[-1]) == 2
    assert (tmp_path / "tmp" / "disc-menu" / "menu_images.json").is_file()
    assert static.exists() and motion.exists()


def test_auto_capture_timeouts_kill_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    dvd = tmp_path / "timeout-dvd"
    _large_file(dvd / "VIDEO_TS.VOB")
    monkeypatch.setattr(disc_menus.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[_Track(duration=1000)]))
    process = _Process(returncode=1, stderr=b"timed out")
    monkeypatch.setattr(disc_menus.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))

    async def timeout(awaitable: Any, **_kwargs: object) -> Any:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(disc_menus.asyncio, "wait_for", timeout)
    meta = _meta(tmp_path, discs=[{"type": "DVD", "name": "Timeout", "path": str(dvd)}])
    asyncio.run(DiscMenus(meta, {"DEFAULT": {"max_menu_screens": "bad"}}).auto_capture_dvd_menus(meta))
    assert process.killed


def test_process_disc_menus_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    get = AsyncMock()

    class FakeMenus:
        def __init__(self, _meta: Meta, _config: dict[str, Any]) -> None:
            pass

        get_disc_menu_images = get

    monkeypatch.setattr(disc_menus, "DiscMenus", FakeMenus)
    meta = _meta(tmp_path)
    asyncio.run(disc_menus.process_disc_menus(meta, {"DEFAULT": {}}))
    get.assert_awaited_once_with(meta)


def test_auto_capture_motion_fallback_filters_black_and_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    _Uploader.reset()
    dvd = tmp_path / "fallback-dvd"
    motion = _large_file(dvd / "VIDEO_TS.VOB")
    monkeypatch.setattr(disc_menus.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[_Track(duration=5000)]))
    output = tmp_path / "tmp" / "disc-menu" / "menu_screenshots"

    def fallback_frames(_calls: int) -> None:
        _png(output / "Fallback-VIDEO_TS-001.png", 0)
        (output / "Fallback-VIDEO_TS-002.png").write_bytes(b"corrupt")
        _png(output / "Fallback-VIDEO_TS-003.png", 255)

    processes = iter([_Process(), _Process(callback=fallback_frames)])
    monkeypatch.setattr(disc_menus.asyncio, "create_subprocess_exec", AsyncMock(side_effect=lambda *_args, **_kwargs: next(processes)))
    meta = _meta(tmp_path, discs=[{"type": "DVD", "name": "Fallback", "path": str(dvd)}])
    asyncio.run(DiscMenus(meta, {"DEFAULT": {"max_menu_screens": 3}}).auto_capture_dvd_menus(meta))
    assert len(meta.menu_images) == 2
    assert any(image["raw_url"].endswith("Fallback-VIDEO_TS-003.png") for image in meta.menu_images)
    assert motion.exists()


def test_auto_capture_motion_timeouts_cover_fallback_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disc_menus, "UploadScreensManager", _Uploader)
    _Uploader.reset()
    dvd = tmp_path / "timeout-motion"
    _large_file(dvd / "VIDEO_TS.VOB")
    monkeypatch.setattr(disc_menus.MediaInfo, "parse", lambda _path: SimpleNamespace(tracks=[_Track(duration=5000)]))
    output = tmp_path / "tmp" / "disc-menu" / "menu_screenshots"

    def retry_frames(_calls: int) -> None:
        _png(output / "Timeout-VIDEO_TS-001.png", 0)
        (output / "Timeout-VIDEO_TS-002.png").write_bytes(b"corrupt")
        _png(output / "Timeout-VIDEO_TS-003.png", 255)

    processes = [_Process(), _Process(), _Process(callback=retry_frames)]
    monkeypatch.setattr(disc_menus.asyncio, "create_subprocess_exec", AsyncMock(side_effect=processes))

    async def timeout(awaitable: Any, **_kwargs: object) -> Any:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(disc_menus.asyncio, "wait_for", timeout)
    meta = _meta(tmp_path, discs=[{"type": "DVD", "name": "Timeout", "path": str(dvd)}])
    asyncio.run(DiscMenus(meta, {"DEFAULT": {"max_menu_screens": 3}}).auto_capture_dvd_menus(meta))
    assert all(process.killed for process in processes)
    assert len(meta.menu_images) == 2
