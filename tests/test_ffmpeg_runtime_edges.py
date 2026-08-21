from __future__ import annotations

import asyncio
import io
import runpy
import stat
import zipfile
from pathlib import Path
from typing import ClassVar

import pytest

from src.integrations.runtime_tools import ffmpeg, ffmpeg_docker


class _Response:
    payload: ClassVar[bytes] = b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self, *, chunk_size: int):
        del chunk_size
        yield self.payload


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


def _zip_bytes(
    name: str, payload: bytes = b"ffmpeg", *, mode: int = stat.S_IFREG | 0o755
) -> bytes:
    output = io.BytesIO()
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, payload)
    return output.getvalue()


def test_find_existing_binary_uses_windows_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []
    monkeypatch.setattr(ffmpeg.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        ffmpeg.shutil,
        "which",
        lambda name: requested.append(name) or "C:/ffmpeg.exe",
    )
    assert (
        ffmpeg.FfmpegBinaryManager.find_existing_binary(tmp_path)
        == "C:/ffmpeg.exe"
    )
    assert requested == ["ffmpeg.exe"]


def test_ensure_ffmpeg_existing_nonwindows_and_windows_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ffmpeg.FfmpegBinaryManager,
        "find_existing_binary",
        classmethod(lambda _cls, _base: "/usr/bin/ffmpeg"),
    )
    assert (
        asyncio.run(ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(tmp_path))
        == "/usr/bin/ffmpeg"
    )

    monkeypatch.setattr(
        ffmpeg.FfmpegBinaryManager,
        "find_existing_binary",
        classmethod(lambda _cls, _base: None),
    )
    monkeypatch.setattr(ffmpeg.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="system package manager"):
        asyncio.run(ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(tmp_path))

    monkeypatch.setattr(ffmpeg.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ffmpeg.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(ffmpeg, "verify_downloaded_asset", lambda *_args: None)
    _Response.payload = _zip_bytes("bundle/bin/ffmpeg.exe")
    result = asyncio.run(
        ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(tmp_path)
    )
    binary = Path(result)
    assert binary.read_bytes() == b"ffmpeg"
    assert (
        binary.parent / f"version_{ffmpeg.FfmpegBinaryManager.VERSION}"
    ).is_file()
    assert not (
        binary.parent / f"temp_{ffmpeg.FfmpegBinaryManager.ASSET_NAME}"
    ).exists()


def test_ensure_ffmpeg_missing_unsafe_and_verification_failure_clean_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ffmpeg.FfmpegBinaryManager,
        "find_existing_binary",
        classmethod(lambda _cls, _base: None),
    )
    monkeypatch.setattr(ffmpeg.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ffmpeg.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(ffmpeg, "verify_downloaded_asset", lambda *_args: None)

    _Response.payload = _zip_bytes("bundle/not-ffmpeg.exe")
    with pytest.raises(RuntimeError, match="was not found"):
        asyncio.run(
            ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(
                tmp_path / "missing"
            )
        )

    _Response.payload = _zip_bytes("../ffmpeg.exe")
    with pytest.raises(RuntimeError, match="Unsafe FFmpeg"):
        asyncio.run(
            ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(
                tmp_path / "unsafe"
            )
        )

    _Response.payload = _zip_bytes("ffmpeg.exe")
    monkeypatch.setattr(
        ffmpeg,
        "verify_downloaded_asset",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("checksum")),
    )
    failed_root = tmp_path / "checksum"
    with pytest.raises(RuntimeError, match="checksum"):
        asyncio.run(
            ffmpeg.FfmpegBinaryManager.ensure_ffmpeg_binary(failed_root)
        )
    assert (
        not ffmpeg.FfmpegBinaryManager.binary_path(failed_root)
        .parent.joinpath(f"temp_{ffmpeg.FfmpegBinaryManager.ASSET_NAME}")
        .exists()
    )


def test_docker_ffmpeg_linux_nonlinux_missing_and_module_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ffmpeg_docker.platform, "system", lambda: "Darwin")
    with pytest.raises(Exception, match="Docker/Linux only"):
        ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker(tmp_path)

    monkeypatch.setattr(ffmpeg_docker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ffmpeg_docker.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="operating system"):
        ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker(tmp_path)

    monkeypatch.setattr(
        ffmpeg_docker.shutil, "which", lambda _name: "/usr/bin/ffmpeg"
    )
    assert (
        ffmpeg_docker.FfmpegBinaryManager.download_ffmpeg_for_docker(tmp_path)
        == "/usr/bin/ffmpeg"
    )

    with pytest.warns(RuntimeWarning):
        runpy.run_module(ffmpeg_docker.__name__, run_name="__main__")
    assert "/usr/bin/ffmpeg" in capsys.readouterr().out
