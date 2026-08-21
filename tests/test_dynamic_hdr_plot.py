import asyncio
import hashlib
from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.media.dynamic_hdr_plot import (
    _formats,
    _generate_plot,
    _run,
    _source_files,
    _terminate_process,
    dynamic_hdr_plot_enabled,
    process_dynamic_hdr_plots,
)
from src.integrations.runtime_tools import (
    dynamic_hdr_tools as get_dynamic_hdr_tools,
)
from src.integrations.trackers.description_builder import DescriptionBuilder


def test_formats_selects_each_dynamic_metadata_type() -> None:
    assert _formats(Meta(hdr="DV HDR10+")) == ["dovi", "hdr10plus"]
    assert _formats(Meta(hdr="HDR10+")) == ["hdr10plus"]
    assert _formats(Meta(hdr="HDR")) == []


def test_source_files_limits_to_supported_existing_video_files(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mp4"
    ignored = tmp_path / "notes.txt"
    first.touch()
    second.touch()
    ignored.touch()

    meta = Meta(filelist=[str(first), str(ignored), str(second)])

    assert _source_files(meta, 1) == [first]
    assert _source_files(meta, 2) == [first, second]


def test_description_section_uses_dynamic_hdr_plot_images() -> None:
    meta = Meta(
        dynamic_hdr_plot=True,
        dynamic_hdr_plot_images=[
            {
                "web_url": "https://host/view",
                "raw_url": "https://host/plot.png",
            }
        ],
    )
    builder = DescriptionBuilder(
        "TEST",
        {
            "DEFAULT": {"dynamic_hdr_plot_header": "[b]HDR plots[/b]"},
            "TRACKERS": {"TEST": {}},
        },
    )

    section = asyncio.run(builder.get_dynamic_hdr_plot_section(meta))

    assert "[b]HDR plots[/b]" in section
    assert "https://host/plot.png" in section


def test_existing_versioned_binary_does_not_download(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    binary_dir = tmp_path / "bin" / "dovi_tool" / "windows" / "amd64"
    binary_dir.mkdir(parents=True)
    binary = binary_dir / "dovi_tool.exe"
    binary.write_bytes(b"tool")
    (binary_dir / "2.3.3").write_text("dovi_tool 2.3.3\n", encoding="utf-8")

    monkeypatch.setattr(get_dynamic_hdr_tools.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "system", lambda: "Windows"
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "machine", lambda: "AMD64"
    )

    class NoDownloadClient:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError(
                "The downloader must not be initialized when the versioned binary exists"
            )

    monkeypatch.setattr(
        get_dynamic_hdr_tools.httpx, "AsyncClient", NoDownloadClient
    )

    result = asyncio.run(get_dynamic_hdr_tools.get_tool(str(tmp_path), "dovi"))

    assert result == str(binary)


def test_zero_byte_cached_dynamic_hdr_binary_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    binary_dir = tmp_path / "bin" / "dovi_tool" / "windows" / "amd64"
    binary_dir.mkdir(parents=True)
    (binary_dir / "dovi_tool.exe").touch()
    (binary_dir / "2.3.3").write_text("dovi_tool 2.3.3\n", encoding="utf-8")
    monkeypatch.setattr(get_dynamic_hdr_tools.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "system", lambda: "Windows"
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "machine", lambda: "AMD64"
    )

    class DownloadAttempt:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("download attempted")

    monkeypatch.setattr(
        get_dynamic_hdr_tools.httpx, "AsyncClient", DownloadAttempt
    )

    with pytest.raises(RuntimeError, match="download attempted"):
        asyncio.run(get_dynamic_hdr_tools.get_tool(str(tmp_path), "dovi"))


def test_downloaded_asset_checksum_is_verified(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    asset = "test-asset"
    content = b"known-good"
    downloaded = tmp_path / asset
    monkeypatch.setitem(
        get_dynamic_hdr_tools.ASSET_SHA256,
        asset,
        hashlib.sha256(content).hexdigest(),
    )

    downloaded.write_bytes(content)
    get_dynamic_hdr_tools._verify_checksum_file(asset, downloaded)
    downloaded.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        get_dynamic_hdr_tools._verify_checksum_file(asset, downloaded)


def test_unsupported_dynamic_hdr_architecture_is_rejected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "system", lambda: "Linux"
    )
    monkeypatch.setattr(
        get_dynamic_hdr_tools.platform, "machine", lambda: "armv7l"
    )

    with pytest.raises(RuntimeError, match="not supported on linux armv7l"):
        get_dynamic_hdr_tools._asset_name("dovi")


def test_mp4_is_remuxed_to_annex_b_hevc(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "release.mp4"
    source.touch()
    commands: list[list[str]] = []

    async def fake_run(
        command: list[str], _timeout_seconds: int = 3600
    ) -> None:
        commands.append(command)
        if command[-1].endswith(".png"):
            Path(command[-1]).touch()

    monkeypatch.setattr(
        "src.integrations.media.dynamic_hdr_plot._run", fake_run
    )

    asyncio.run(_generate_plot("dovi_tool", "dovi", source, tmp_path))

    assert commands[0][-3:-1] == ["-f", "hevc"]
    assert Path(commands[0][-1]).name.startswith("release_")
    assert commands[1][2] == commands[0][-1]


def test_plot_artifacts_are_unique_for_same_named_sources(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    first = tmp_path / "first" / "release.mkv"
    second = tmp_path / "second" / "release.mkv"
    first.parent.mkdir()
    second.parent.mkdir()
    first.touch()
    second.touch()

    async def fake_run(
        command: list[str], _timeout_seconds: int = 3600
    ) -> None:
        if command[-1].endswith(".png"):
            Path(command[-1]).touch()

    monkeypatch.setattr(
        "src.integrations.media.dynamic_hdr_plot._run", fake_run
    )

    first_plot = asyncio.run(
        _generate_plot("dovi_tool", "dovi", first, tmp_path)
    )
    second_plot = asyncio.run(
        _generate_plot("dovi_tool", "dovi", second, tmp_path)
    )

    assert first_plot != second_plot


def test_tracker_override_enables_dynamic_hdr_plot() -> None:
    meta = Meta(trackers=["TEST"])
    config = {
        "DEFAULT": {"add_dynamic_hdr_plot": False},
        "TRACKERS": {"TEST": {"add_dynamic_hdr_plot": True}},
    }

    assert dynamic_hdr_plot_enabled(meta, config)


def test_debug_mode_does_not_upload_dynamic_hdr_images(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    source = tmp_path / "release.mkv"
    source.touch()
    generated_plot = tmp_path / "plot.png"

    async def fake_get_tool(_base_dir: str, _kind: str) -> str:
        return "dovi_tool"

    async def fake_generate(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        generated_plot.touch()
        return generated_plot

    class UploadManager:
        async def upload_screens(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("debug mode must not upload images")

    monkeypatch.setattr(
        "src.integrations.media.dynamic_hdr_plot.get_tool", fake_get_tool
    )
    monkeypatch.setattr(
        "src.integrations.media.dynamic_hdr_plot._generate_plot", fake_generate
    )
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="debug-hdr",
        filelist=[str(source)],
        hdr="DV",
        debug=True,
    )

    generated = asyncio.run(
        process_dynamic_hdr_plots(meta, {"DEFAULT": {}}, UploadManager())
    )

    assert generated == [str(generated_plot)]
    assert meta.dynamic_hdr_plot_images == []


def test_dynamic_hdr_tool_timeout_is_reported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Process:
        returncode = None
        pid = None
        killed = False

        async def wait(self) -> int:
            await asyncio.sleep(60)
            return 0

        def kill(self) -> None:
            self.killed = True

    process = Process()

    async def create_process(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(RuntimeError, match="timed out after 1 seconds"):
        asyncio.run(_run(["dovi_tool", "plot"], timeout_seconds=1))

    assert process.killed is True


def test_dynamic_hdr_cancellation_kills_and_reaps_process(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Process:
        returncode = None
        pid = None

        def __init__(self) -> None:
            self.killed = False
            self.wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                await asyncio.sleep(60)
            self.returncode = -9
            return self.returncode

        def kill(self) -> None:
            self.killed = True

    process = Process()

    async def create_process(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return process

    async def exercise() -> None:
        task = asyncio.create_task(_run(["dovi_tool", "plot"]))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    asyncio.run(exercise())

    assert process.killed is True
    assert process.wait_calls == 2


def test_windows_dynamic_hdr_cleanup_kills_target_when_taskkill_fails(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    class Process:
        returncode = None
        pid = 42

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.waited = True
            self.returncode = -9
            return self.returncode

    async def fail_taskkill(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("taskkill unavailable")

    process = Process()
    monkeypatch.setattr(
        "src.integrations.media.dynamic_hdr_plot.os.name", "nt"
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_taskkill)

    asyncio.run(_terminate_process(process))  # type: ignore[arg-type]

    assert process.killed is True
    assert process.waited is True
