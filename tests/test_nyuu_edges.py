from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.integrations.runtime_tools import nyuu


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _tar(path: Path, *members: tuple[str, bytes]) -> None:
    with tarfile.open(path, "w:xz") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(payload))


def _platform(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str
) -> None:
    monkeypatch.setattr(nyuu.platform, "system", lambda: system)
    monkeypatch.setattr(nyuu.platform, "machine", lambda: machine)
    monkeypatch.setattr(nyuu.httpx, "AsyncClient", _Client)


def test_unsupported_cache_and_linux_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "nyuu"
    target.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "FreeBSD", "x86_64")
    with pytest.raises(Exception, match="Unsupported platform"):
        asyncio.run(nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path))

    _platform(monkeypatch, "Linux", "x86_64")
    binary = target / "nyuu"
    binary.write_bytes(b"cached")
    binary.chmod(0o755)
    marker = target / "v0.4.2"
    marker.write_text("cached", encoding="utf-8")
    assert asyncio.run(
        nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path)
    ) == str(binary)

    binary.unlink()
    marker.unlink()
    stale = target / "v0.4.1"
    stale.write_text("stale", encoding="utf-8")

    async def download(
        _client, _url: str, destination: Path, asset: str
    ) -> None:
        assert asset.endswith("linux-amd64.tar.xz")
        _tar(destination, ("bundle/nyuu", b"linux-nyuu"))

    monkeypatch.setattr(nyuu, "download_verified_asset", download)
    result = asyncio.run(nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path))
    assert result == str(binary) and binary.read_bytes() == b"linux-nyuu"
    assert (
        binary.stat().st_mode & 0o100
        and marker.is_file()
        and not stale.exists()
    )
    assert not (target / ".nyuu-staging").exists()


def test_linux_oversize_and_download_failure_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "oversize"
    target.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "Linux", "arm64")

    async def download(
        _client, _url: str, destination: Path, _asset: str
    ) -> None:
        _tar(destination, ("nyuu", b"large"))

    monkeypatch.setattr(nyuu, "download_verified_asset", download)
    monkeypatch.setattr(nyuu, "MAX_EXTRACTED_BYTES", 4)

    def extract(_archive, destination: Path, **_kwargs: object) -> None:
        (destination / "nyuu").write_bytes(b"large")

    monkeypatch.setattr(nyuu, "safe_extract_tar", extract)
    with pytest.raises(Exception, match="exceeds the allowed size"):
        asyncio.run(nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path))
    assert not (target / ".nyuu-staging").exists()

    failed = tmp_path / "failed"
    failed.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: failed)

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("download failed")

    monkeypatch.setattr(nyuu, "download_verified_asset", fail)
    with pytest.raises(Exception, match="Failed to setup Nyuu"):
        asyncio.run(nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path))
    assert list(failed.iterdir()) == []


def test_windows_auto_7z_success_and_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "windows"
    target.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "Windows", "AMD64")

    async def download(
        _client, _url: str, destination: Path, _asset: str
    ) -> None:
        destination.write_bytes(b"archive")

    monkeypatch.setattr(nyuu, "download_verified_asset", download)
    from src.integrations.runtime_tools.seven_zip import SevenZipBinaryManager

    monkeypatch.setattr(
        SevenZipBinaryManager,
        "ensure_7z_binary",
        AsyncMock(return_value="7zr.exe"),
    )

    class Process:
        pid = 1
        returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    async def create(*args: object, **_kwargs: object):
        output = next(
            str(value)[2:] for value in args if str(value).startswith("-o")
        )
        Path(output).joinpath("nyuu.exe").write_bytes(b"windows")
        return Process()

    monkeypatch.setattr(nyuu.asyncio, "create_subprocess_exec", create)
    result = asyncio.run(nyuu.NyuuBinaryManager.ensure_nyuu_binary(tmp_path))
    assert Path(result).read_bytes() == b"windows"

    nonzero = tmp_path / "nonzero"
    nonzero.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: nonzero)

    class Failed(Process):
        returncode = 2

        async def communicate(self):
            return b"", b"extract error"

    monkeypatch.setattr(
        nyuu.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: _awaitable(Failed()),
    )
    with pytest.raises(Exception, match="7z extraction failed"):
        asyncio.run(
            nyuu.NyuuBinaryManager.ensure_nyuu_binary(
                tmp_path, path_7z="7zr.exe"
            )
        )


def _awaitable(value):
    async def result():
        return value

    return result()


def test_windows_extraction_timeout_calls_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "timeout"
    target.mkdir()
    monkeypatch.setattr(nyuu, "tool_install_dir", lambda *_args: target)
    _platform(monkeypatch, "Windows", "x86_64")

    async def download(
        _client, _url: str, destination: Path, _asset: str
    ) -> None:
        destination.write_bytes(b"archive")

    monkeypatch.setattr(nyuu, "download_verified_asset", download)

    class Process:
        pid = 2
        returncode = None

        async def communicate(self):
            await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9

    process = Process()
    monkeypatch.setattr(
        nyuu.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: _awaitable(process),
    )
    terminated = AsyncMock()
    monkeypatch.setattr(
        nyuu.NyuuBinaryManager, "_terminate_process_tree", terminated
    )
    original_wait_for = asyncio.wait_for

    async def timeout_first(awaitable, *args: object, **kwargs: object):
        timeout_value = kwargs.get("timeout", args[0] if args else None)
        if timeout_value == 120:
            awaitable.close()
            raise TimeoutError
        return await original_wait_for(awaitable, timeout_value)

    monkeypatch.setattr(nyuu.asyncio, "wait_for", timeout_first)
    with pytest.raises(Exception, match="timed out"):
        asyncio.run(
            nyuu.NyuuBinaryManager.ensure_nyuu_binary(
                tmp_path, path_7z="7zr.exe"
            )
        )
    terminated.assert_awaited_once_with(process)


def test_terminate_process_tree_windows_warning_timeout_oserror_and_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 42
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.communications = 0

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def communicate(self):
            self.communications += 1
            return b"", b""

    class TreeKiller(Process):
        returncode = 1

    process = Process()
    monkeypatch.setattr(nyuu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        nyuu.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: _awaitable(TreeKiller()),
    )
    asyncio.run(nyuu.NyuuBinaryManager._terminate_process_tree(process))
    assert process.killed and process.communications == 1

    process = Process()
    monkeypatch.setattr(
        nyuu.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("missing taskkill")
        ),
    )
    asyncio.run(nyuu.NyuuBinaryManager._terminate_process_tree(process))
    assert process.killed

    process = Process()
    monkeypatch.setattr(nyuu.platform, "system", lambda: "Linux")
    asyncio.run(nyuu.NyuuBinaryManager._terminate_process_tree(process))
    assert process.killed and process.communications == 1


def test_terminate_process_tree_windows_taskkill_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 77
        returncode = None

        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def communicate(self):
            return b"", b""

    class TreeKiller(Process):
        pass

    process = Process()
    tree_killer = TreeKiller()
    monkeypatch.setattr(nyuu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        nyuu.asyncio,
        "create_subprocess_exec",
        lambda *_args, **_kwargs: _awaitable(tree_killer),
    )
    calls = 0

    async def timeout_once(awaitable, *args: object, **kwargs: object):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            awaitable.close()
            raise TimeoutError
        return await awaitable

    monkeypatch.setattr(nyuu.asyncio, "wait_for", timeout_once)
    asyncio.run(nyuu.NyuuBinaryManager._terminate_process_tree(process))
    assert tree_killer.killed is True
    assert process.killed is True
    assert calls == 3
