from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.media import dynamic_hdr_plot


def _video(tmp_path: Path, name: str = "video.mkv") -> Path:
    path = tmp_path / name
    path.write_bytes(b"video")
    return path


def _meta(tmp_path: Path, **values: object) -> Meta:
    video = _video(tmp_path)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "hdr",
        "filelist": [str(video)],
        "hdr": "DV",
        "dynamic_hdr_plot": False,
        "dynamic_hdr_plot_images": [],
        "trackers": [],
        "skip_imghost_upload": False,
        "debug": False,
        "bdinfo": {},
    }
    state.update(values)
    return Meta(state)


def test_positive_config_int_and_bdinfo_source(tmp_path: Path) -> None:
    assert (
        dynamic_hdr_plot._positive_config_int(
            {"DEFAULT": {"value": 0}}, "value", 3
        )
        == 1
    )
    assert (
        dynamic_hdr_plot._positive_config_int(
            {"DEFAULT": {"value": "bad"}}, "value", 3
        )
        == 3
    )
    assert dynamic_hdr_plot._positive_config_int({}, "value", 3) == 3

    disc = tmp_path / "disc"
    stream = disc / "STREAM"
    stream.mkdir(parents=True)
    file = _video(stream, "00001.m2ts")
    meta = _meta(
        tmp_path,
        bdinfo={"files": [{"file": file.name}], "path": str(disc)},
        filelist=[str(tmp_path / "missing.mkv")],
    )
    assert dynamic_hdr_plot._source_files(meta, 1) == [file]

    meta.bdinfo = {"files": [{"file": "missing.m2ts"}], "path": str(disc)}
    assert dynamic_hdr_plot._source_files(meta, 1) == []


def test_terminate_process_all_platform_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = SimpleNamespace(returncode=0)
    asyncio.run(dynamic_hdr_plot._terminate_process(done))  # type: ignore[arg-type]

    class Process:
        def __init__(self, pid: int | None) -> None:
            self.returncode: int | None = None
            self.pid = pid
            self.killed = False
            self.waited = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            self.waited = True
            return int(self.returncode or 0)

    posix = Process(42)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(dynamic_hdr_plot.os, "name", "posix")
    monkeypatch.setattr(
        dynamic_hdr_plot.os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    asyncio.run(dynamic_hdr_plot._terminate_process(posix))
    assert killed == [(42, dynamic_hdr_plot.signal.SIGKILL)] and posix.waited

    no_pid = Process(None)
    asyncio.run(dynamic_hdr_plot._terminate_process(no_pid))
    assert no_pid.killed and no_pid.waited


def test_windows_taskkill_timeout_kills_helper_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Helper:
        def __init__(self) -> None:
            self.killed = False

        async def wait(self) -> int:
            await asyncio.Event().wait()
            return 0

        def kill(self) -> None:
            self.killed = True

    class Process:
        returncode: int | None = None
        pid = 42

        def __init__(self) -> None:
            self.killed = False

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    helper = Helper()
    target = Process()
    monkeypatch.setattr(dynamic_hdr_plot.os, "name", "nt")
    monkeypatch.setattr(
        dynamic_hdr_plot.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=helper),
    )

    real_wait_for = asyncio.wait_for
    calls = 0

    async def wait_for(awaitable: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError
        return await real_wait_for(awaitable, timeout=float(kwargs["timeout"]))

    monkeypatch.setattr(dynamic_hdr_plot.asyncio, "wait_for", wait_for)
    asyncio.run(dynamic_hdr_plot._terminate_process(target))  # type: ignore[arg-type]
    assert helper.killed and target.killed


def test_run_nonzero_and_generate_hdr10plus_missing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = SimpleNamespace(
        wait=AsyncMock(return_value=3), returncode=3, pid=1
    )
    monkeypatch.setattr(
        dynamic_hdr_plot.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    with pytest.raises(RuntimeError, match="exit code 3"):
        asyncio.run(dynamic_hdr_plot._run(["tool", "run"]))

    source = _video(tmp_path, "video.mkv")
    commands: list[list[str]] = []

    async def run(command: list[str], _timeout: int) -> None:
        commands.append(command)

    monkeypatch.setattr(dynamic_hdr_plot, "_run", run)
    with pytest.raises(RuntimeError, match="did not create"):
        asyncio.run(
            dynamic_hdr_plot._generate_plot(
                "hdr10plus_tool", "hdr10plus", source, tmp_path
            )
        )
    assert commands[0][1] == "extract" and commands[1][1] == "plot"


def test_dynamic_hdr_enabled_global_explicit_and_string_tracker() -> None:
    assert dynamic_hdr_plot.dynamic_hdr_plot_enabled(
        Meta(dynamic_hdr_plot=True), {"DEFAULT": {}, "TRACKERS": {}}
    )
    assert dynamic_hdr_plot.dynamic_hdr_plot_enabled(
        Meta(), {"DEFAULT": {"add_dynamic_hdr_plot": True}, "TRACKERS": {}}
    )
    assert dynamic_hdr_plot.dynamic_hdr_plot_enabled(
        Meta(trackers="test"),
        {"DEFAULT": {}, "TRACKERS": {"TEST": {"add_dynamic_hdr_plot": True}}},
    )
    assert not dynamic_hdr_plot.dynamic_hdr_plot_enabled(
        Meta(trackers=[1, "missing"]),
        {"DEFAULT": {}, "TRACKERS": {"MISSING": "bad"}},
    )


def test_process_short_circuits_cache_generation_and_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"DEFAULT": {}}
    existing = _meta(
        tmp_path, dynamic_hdr_plot_images=[{"raw_url": "existing"}]
    )
    assert (
        asyncio.run(
            dynamic_hdr_plot.process_dynamic_hdr_plots(existing, config)
        )
        == []
    )

    no_format = _meta(tmp_path, hdr="SDR")
    assert (
        asyncio.run(
            dynamic_hdr_plot.process_dynamic_hdr_plots(no_format, config)
        )
        == []
    )

    no_source = _meta(tmp_path, filelist=[str(tmp_path / "missing.mkv")])
    assert (
        asyncio.run(
            dynamic_hdr_plot.process_dynamic_hdr_plots(no_source, config)
        )
        == []
    )

    cached = _meta(tmp_path)
    source = Path(cached.filelist[0])
    output_dir = dynamic_hdr_plot.dynamic_hdr_plots_dir(
        cached.base_dir, cached.uuid
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path = (
        dynamic_hdr_plot.release_temp_dir(cached.base_dir, cached.uuid)
        / "dynamic_hdr_plot_images.json"
    )
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": dynamic_hdr_plot.CACHE_VERSION,
                "fingerprint": dynamic_hdr_plot._fingerprint(
                    [source], ["dovi"]
                ),
                "dynamic_hdr_plot_images": [{"raw_url": "cached"}],
            }
        ),
        encoding="utf-8",
    )
    assert (
        asyncio.run(dynamic_hdr_plot.process_dynamic_hdr_plots(cached, config))
        == []
    )
    assert cached.dynamic_hdr_plot_images == [{"raw_url": "cached"}]

    generated = _meta(tmp_path, uuid="generated")
    source = Path(generated.filelist[0])
    plot = tmp_path / "plot.png"
    monkeypatch.setattr(
        dynamic_hdr_plot, "get_tool", AsyncMock(return_value="dovi_tool")
    )
    monkeypatch.setattr(
        dynamic_hdr_plot, "_generate_plot", AsyncMock(return_value=plot)
    )
    uploader = SimpleNamespace(
        upload_screens=AsyncMock(return_value=([{"raw_url": "uploaded"}], 1))
    )
    result = asyncio.run(
        dynamic_hdr_plot.process_dynamic_hdr_plots(generated, config, uploader)
    )
    assert result == [str(plot)]
    assert generated.dynamic_hdr_plot_images == [{"raw_url": "uploaded"}]
    cache = (
        dynamic_hdr_plot.release_temp_dir(generated.base_dir, generated.uuid)
        / "dynamic_hdr_plot_images.json"
    )
    assert json.loads(cache.read_text(encoding="utf-8"))[
        "dynamic_hdr_plot_images"
    ] == [{"raw_url": "uploaded"}]


def test_process_generation_and_upload_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "DEFAULT": {
            "dynamic_hdr_plot_tool_timeout": dynamic_hdr_plot.MAX_TOOL_TIMEOUT_SECONDS
            + 100
        }
    }
    meta = _meta(tmp_path, hdr="DV HDR10+")
    monkeypatch.setattr(
        dynamic_hdr_plot, "configured_binary", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        dynamic_hdr_plot, "get_tool", AsyncMock(return_value="tool")
    )
    calls = 0

    async def generate(
        _binary: str,
        kind: str,
        _source: Path,
        _output: Path,
        timeout_seconds: int,
        _ffmpeg: str,
    ) -> Path:
        nonlocal calls
        calls += 1
        assert timeout_seconds == dynamic_hdr_plot.MAX_TOOL_TIMEOUT_SECONDS
        if kind == "dovi":
            raise RuntimeError("dovi failed")
        plot = tmp_path / "hdr10plus.png"
        plot.write_bytes(b"plot")
        return plot

    monkeypatch.setattr(dynamic_hdr_plot, "_generate_plot", generate)
    uploader = SimpleNamespace(
        upload_screens=AsyncMock(side_effect=RuntimeError("upload failed"))
    )
    result = asyncio.run(
        dynamic_hdr_plot.process_dynamic_hdr_plots(meta, config, uploader)
    )
    assert result == [str(tmp_path / "hdr10plus.png")] and calls == 2
    assert meta.dynamic_hdr_plot_images == []
