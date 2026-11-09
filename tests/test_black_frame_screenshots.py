from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.integrations.media import screenshot_capture as capture


def _write_frame(path: Path, colour: str) -> Path:
    Image.new("RGB", (320, 180), colour).save(path)
    return path


def _capture_plan(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        img_host="sharex",
        manual_frames=[],
        path=str(tmp_path / "video.mkv"),
        width=1920,
        height=1080,
        w_sar=1.0,
        h_sar=1.0,
        loglevel="quiet",
        meta=SimpleNamespace(),
    )


def test_mean_luma_separates_black_from_bright_frames(
    tmp_path: Path,
) -> None:
    black = _write_frame(tmp_path / "black.png", "black")
    bright = _write_frame(tmp_path / "bright.png", "white")

    assert capture._mean_luma(black) < capture.BLACK_FRAME_MEAN_LUMA
    assert capture._mean_luma(bright) >= capture.BLACK_FRAME_MEAN_LUMA


@pytest.mark.asyncio
async def test_black_frame_is_detected_and_bright_frame_is_not(
    tmp_path: Path,
) -> None:
    black = _write_frame(tmp_path / "black.png", "black")
    bright = _write_frame(tmp_path / "bright.png", "white")

    assert await capture._is_black_frame(black) is True
    assert await capture._is_black_frame(bright) is False


@pytest.mark.asyncio
async def test_undecodable_image_is_not_declared_black(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not-a-png")

    assert await capture._is_black_frame(broken) is False


@pytest.mark.asyncio
async def test_validated_capture_path_retakes_a_black_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    black = _write_frame(tmp_path / "Show-0.png", "black")
    retaken = _write_frame(tmp_path / "Show-1.png", "white")
    plan = SimpleNamespace(manual_frames=[], img_host="sharex")
    retakes: list[str] = []

    async def fake_retake(
        _plan: object, image_path: str, _hdr_tonemap: bool
    ) -> str:
        retakes.append(image_path)
        return str(retaken)

    monkeypatch.setattr(
        capture, "_capture_size_is_valid", lambda *_a, **_k: True
    )
    monkeypatch.setattr(capture, "_retake_capture", fake_retake)

    result = await capture._validated_capture_path(plan, str(black), False)

    assert retakes == [str(black)]
    assert result == str(retaken)


@pytest.mark.asyncio
async def test_retake_attempt_rejects_a_still_black_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _write_frame(tmp_path / "Show-0.png", "black")
    still_black = _write_frame(tmp_path / "Show-1.png", "black")
    plan = _capture_plan(tmp_path)

    async def fake_capture(_task: tuple[object, ...]) -> tuple[int, str]:
        return 0, str(still_black)

    monkeypatch.setattr(capture, "capture_screenshot", fake_capture)
    monkeypatch.setattr(
        capture, "_capture_size_is_valid", lambda *_a, **_k: True
    )

    result = await capture._retake_capture_attempt(
        plan, str(original), 0, 5.0, False
    )

    assert result is None


@pytest.mark.asyncio
async def test_retake_attempt_keeps_a_bright_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _write_frame(tmp_path / "Show-0.png", "black")
    bright = _write_frame(tmp_path / "Show-1.png", "white")
    plan = _capture_plan(tmp_path)

    async def fake_capture(_task: tuple[object, ...]) -> tuple[int, str]:
        return 0, str(bright)

    monkeypatch.setattr(capture, "capture_screenshot", fake_capture)
    monkeypatch.setattr(
        capture, "_capture_size_is_valid", lambda *_a, **_k: True
    )

    result = await capture._retake_capture_attempt(
        plan, str(original), 0, 5.0, False
    )

    assert result == str(bright)


@pytest.mark.asyncio
async def test_validated_capture_path_keeps_a_usable_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _write_frame(tmp_path / "Show-0.png", "grey")
    plan = SimpleNamespace(manual_frames=[], img_host="sharex")

    async def fail_retake(
        _plan: object, _image_path: str, _hdr_tonemap: bool
    ) -> str:
        raise AssertionError("a usable frame must not be retaken")

    monkeypatch.setattr(
        capture, "_capture_size_is_valid", lambda *_a, **_k: True
    )
    monkeypatch.setattr(capture, "_retake_capture", fail_retake)

    assert await capture._validated_capture_path(
        plan, str(frame), False
    ) == str(frame)
