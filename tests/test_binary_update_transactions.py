from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.integrations.runtime_tools import bdinfo as get_bdinfo
from src.integrations.runtime_tools import (
    dynamic_hdr_tools as get_dynamic_hdr_tools,
)
from src.integrations.runtime_tools import mkbrr as get_mkbrr
from src.integrations.runtime_tools import par2 as get_par2
from src.integrations.runtime_tools import pesto as get_pesto
from src.integrations.runtime_tools import seven_zip as get_7z
from src.integrations.runtime_tools import zentag_binary as get_zentag


@pytest.mark.parametrize(
    ("module", "ensure", "relative_dir", "binary_name", "old_marker"),
    [
        (
            get_7z,
            get_7z.SevenZipBinaryManager.ensure_7z_binary,
            "bin/7z/linux/amd64",
            "7zz",
            "25.00",
        ),
        (
            get_bdinfo,
            get_bdinfo.BDInfoBinaryManager.ensure_bdinfo_binary,
            "bin/bdinfo/linux/amd64",
            "bdinfo",
            "v0.2.0",
        ),
        (
            get_mkbrr,
            lambda base_dir: get_mkbrr.MkbrrBinaryManager.ensure_mkbrr_binary(
                base_dir, "v1.24.0"
            ),
            "bin/mkbrr/linux/amd64",
            "mkbrr",
            "v1.23.0",
        ),
        (
            get_par2,
            get_par2.Par2BinaryManager.ensure_par2_binary,
            "bin/par2/linux/amd64",
            "par2",
            "v1.3.0",
        ),
        (
            get_pesto,
            get_pesto.PestoBinaryManager.ensure_pesto_binary,
            "bin/pesto/linux/amd64",
            "pesto",
            "pesto-v0.5.0",
        ),
    ],
)
def test_failed_binary_manager_update_preserves_existing_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    ensure: Any,
    relative_dir: str,
    binary_name: str,
    old_marker: str,
) -> None:
    async def fail_download(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated download failure")

    target = tmp_path / relative_dir
    target.mkdir(parents=True)
    binary = target / binary_name
    binary.write_bytes(b"working binary")
    binary.chmod(0o700)
    marker = target / old_marker
    marker.write_text("working marker", encoding="utf-8")
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(module, "download_verified_asset", fail_download)

    with pytest.raises(Exception, match="simulated download failure"):
        asyncio.run(ensure(tmp_path))

    assert binary.read_bytes() == b"working binary"
    assert marker.read_text(encoding="utf-8") == "working marker"


class _Client:
    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _configure_pesto_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_pesto.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_pesto.platform, "machine", lambda: "x86_64")


def test_pesto_cache_hit_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "bin/pesto/linux/amd64"
    target.mkdir(parents=True)
    binary = target / "pesto"
    binary.write_bytes(b"current")
    binary.chmod(0o700)
    (target / "pesto-v0.6.0").write_text("current marker", encoding="utf-8")
    _configure_pesto_platform(monkeypatch)

    async def unexpected_download(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("cache hit attempted a download")

    monkeypatch.setattr(
        get_pesto, "download_verified_asset", unexpected_download
    )

    assert asyncio.run(
        get_pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path)
    ) == str(binary)


def test_pesto_upgrade_replaces_stale_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "bin/pesto/linux/amd64"
    target.mkdir(parents=True)
    binary = target / "pesto"
    binary.write_bytes(b"old")
    binary.chmod(0o700)
    stale_marker = target / "pesto-v0.5.0"
    stale_marker.write_text("old marker", encoding="utf-8")
    _configure_pesto_platform(monkeypatch)
    monkeypatch.setattr(
        get_pesto.httpx, "AsyncClient", lambda **_kwargs: _Client()
    )

    async def download(
        _client: Any, _url: str, destination: Path, _asset: str
    ) -> None:
        destination.write_bytes(b"new")

    monkeypatch.setattr(get_pesto, "download_verified_asset", download)

    asyncio.run(get_pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))

    assert binary.read_bytes() == b"new"
    assert (target / "pesto-v0.6.0").is_file()
    assert not stale_marker.exists()


def test_pesto_promotion_failure_preserves_old_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "bin/pesto/linux/amd64"
    target.mkdir(parents=True)
    binary = target / "pesto"
    binary.write_bytes(b"old")
    binary.chmod(0o700)
    stale_marker = target / "pesto-v0.5.0"
    stale_marker.write_text("old marker", encoding="utf-8")
    _configure_pesto_platform(monkeypatch)
    monkeypatch.setattr(
        get_pesto.httpx, "AsyncClient", lambda **_kwargs: _Client()
    )

    async def download(
        _client: Any, _url: str, destination: Path, _asset: str
    ) -> None:
        destination.write_bytes(b"new")

    def fail_promotion(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("promotion failed")

    monkeypatch.setattr(get_pesto, "download_verified_asset", download)
    monkeypatch.setattr(
        get_pesto, "promote_files_with_rollback", fail_promotion
    )

    with pytest.raises(Exception, match="promotion failed"):
        asyncio.run(get_pesto.PestoBinaryManager.ensure_pesto_binary(tmp_path))

    assert binary.read_bytes() == b"old"
    assert stale_marker.read_text(encoding="utf-8") == "old marker"


def test_failed_dynamic_hdr_update_preserves_existing_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_download(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated download failure")

    target = tmp_path / "bin/dovi_tool/linux/x86_64"
    target.mkdir(parents=True)
    binary = target / "dovi_tool"
    binary.write_bytes(b"working binary")
    marker = target / "old-version"
    marker.write_text("working marker", encoding="utf-8")
    monkeypatch.setattr(
        get_dynamic_hdr_tools.shutil, "which", lambda _command: None
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "system", lambda: "Linux"
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "machine", lambda: "x86_64"
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.httpx, "AsyncClient", lambda **_kwargs: _Client()
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools, "download_bounded_asset", fail_download
    )

    with pytest.raises(RuntimeError, match="simulated download failure"):
        asyncio.run(get_dynamic_hdr_tools.get_tool(str(tmp_path), "dovi"))

    assert binary.read_bytes() == b"working binary"
    assert marker.read_text(encoding="utf-8") == "working marker"


def test_failed_zentag_update_preserves_existing_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_download(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated download failure")

    target = tmp_path / "bin/zentag/linux/amd64"
    target.mkdir(parents=True)
    binary = target / "zentag"
    binary.write_bytes(b"working binary")
    marker = target / get_zentag.ZentagBinaryManager.VERSION
    marker.write_text(get_zentag.ZentagBinaryManager.VERSION, encoding="utf-8")
    monkeypatch.setattr(get_zentag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(get_zentag.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        get_zentag,
        "HTTPX",
        type("HTTPXStub", (), {"AsyncClient": lambda **_kwargs: _Client()}),
    )
    monkeypatch.setattr(get_zentag, "download_bounded_asset", fail_download)

    with pytest.raises(RuntimeError, match="simulated download failure"):
        asyncio.run(get_zentag.ZentagBinaryManager.ensure_binary(tmp_path))

    assert binary.read_bytes() == b"working binary"
    assert (
        marker.read_text(encoding="utf-8")
        == get_zentag.ZentagBinaryManager.VERSION
    )
