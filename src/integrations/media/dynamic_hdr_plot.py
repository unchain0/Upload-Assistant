"""Generate dynamic HDR metadata plots with the official quietvoid tools.

The tools are downloaded lazily, like mkbrr, so a normal upload never needs an
extra dependency.  Dolby Vision and HDR10+ are deliberately kept as separate
plots: their dynamic metadata has different semantics.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import (
    dynamic_hdr_plots_dir,
    release_temp_dir,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)
from src.integrations.runtime_tools.dynamic_hdr_tools import TOOLS, get_tool

CACHE_VERSION = 1
VIDEO_EXTENSIONS = {".m2ts", ".mkv", ".mp4", ".ts", ".hevc", ".h265"}
MAX_TOOL_TIMEOUT_SECONDS = 7200


def _positive_config_int(
    config: dict[str, Any], key: str, default: int
) -> int:
    try:
        return max(1, int(config["DEFAULT"].get(key, default)))
    except KeyError, TypeError, ValueError:
        return default


def _first_bdinfo_stream(bdinfo: dict[str, Any]) -> str:
    files = bdinfo.get("files", [])
    if not isinstance(files, list) or not files:
        return ""
    typed_files = cast(list[Any], files)
    first = typed_files[0]
    if not isinstance(first, dict):
        return ""
    typed_first = cast(dict[str, Any], first)
    stream = typed_first.get("file")
    return str(stream) if stream else ""


def _bdinfo_source(meta: Meta) -> Path | None:
    if not meta.bdinfo:
        return None
    disc_path = meta.bdinfo.get("path", "")
    stream = _first_bdinfo_stream(meta.bdinfo)
    if not disc_path or not stream:
        return None
    candidate = Path(disc_path) / "STREAM" / stream
    return candidate if candidate.is_file() else None


def _filelist_sources(meta: Meta) -> list[Path]:
    sources: list[Path] = []
    for file in meta.filelist:
        candidate = Path(file)
        if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        if candidate.is_file():
            sources.append(candidate)
    return list(dict.fromkeys(sources))


def _source_files(meta: Meta, max_files: int) -> list[Path]:
    disc_source = _bdinfo_source(meta)
    if disc_source is not None:
        return [disc_source]
    return _filelist_sources(meta)[:max_files]


def _formats(meta: Meta) -> list[str]:
    hdr = str(meta.hdr or "").upper()
    formats: list[str] = []
    if "DV" in hdr or "DOLBY VISION" in hdr:
        formats.append("dovi")
    if "HDR10+" in hdr:
        formats.append("hdr10plus")
    return formats


def _fingerprint(sources: list[Path], formats: list[str]) -> str:
    digest = hashlib.sha256()
    for source in sources:
        info = source.stat()
        digest.update(
            f"{source.resolve()}:{info.st_size}:{info.st_mtime_ns}".encode()
        )
    digest.update(",".join(formats).encode())
    return digest.hexdigest()


def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.kill()


async def _wait_taskkill(killer: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(killer.wait(), timeout=5)
    except TimeoutError:
        with suppress(ProcessLookupError):
            killer.kill()


async def _terminate_windows_process(
    process: asyncio.subprocess.Process, pid: int
) -> None:
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await _wait_taskkill(killer)
    except OSError:
        pass
    finally:
        _kill_process(process)


def _terminate_posix_group(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


async def _wait_terminated_process(
    process: asyncio.subprocess.Process,
) -> None:
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    pid = getattr(process, "pid", None)
    if os.name == "nt" and pid is not None:
        await _terminate_windows_process(process, pid)
    elif pid is not None:
        _terminate_posix_group(pid)
    else:
        _kill_process(process)
    await _wait_terminated_process(process)


async def _run(command: list[str], timeout_seconds: int = 3600) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt"
        else 0,
        start_new_session=os.name != "nt",
    )
    try:
        returncode = await asyncio.wait_for(
            process.wait(), timeout=timeout_seconds
        )
    except TimeoutError:
        await _terminate_process(process)
        raise RuntimeError(
            f"{' '.join(command[:2])} timed out after {timeout_seconds} seconds"
        ) from None
    except BaseException:
        await _terminate_process(process)
        raise
    if returncode:
        raise RuntimeError(
            f"{' '.join(command[:2])} failed with exit code {returncode}"
        )


async def _generate_plot(
    binary: str,
    kind: str,
    source: Path,
    output_dir: Path,
    timeout_seconds: int = 3600,
    ffmpeg_binary: str = "ffmpeg",
) -> Path:
    stem = source.stem
    artifact_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[
        :12
    ]
    artifact_name = f"{stem}_{artifact_id}"
    output = output_dir / f"dynamic_hdr_{kind}_{artifact_name}.png"
    work_dir = output_dir / ".metadata"
    work_dir.mkdir(exist_ok=True)
    input_source = source
    if source.suffix.lower() in {".m2ts", ".mp4", ".ts"}:
        # The third-party tools accept MKV or elementary HEVC streams. Convert
        # transport streams and MP4 containers with a stream copy, never a re-encode.
        input_source = work_dir / f"{artifact_name}.hevc"
        await _run(
            [
                ffmpeg_binary,
                "-y",
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-c:v",
                "copy",
                "-bsf:v",
                "hevc_mp4toannexb",
                "-f",
                "hevc",
                str(input_source),
            ],
            timeout_seconds,
        )
    if kind == "dovi":
        rpu = work_dir / f"{artifact_name}.rpu.bin"
        await _run(
            [binary, "extract-rpu", str(input_source), "-o", str(rpu)],
            timeout_seconds,
        )
        await _run(
            [
                binary,
                "plot",
                str(rpu),
                "-t",
                f"Dolby Vision L1 Plot - {stem}",
                "-o",
                str(output),
            ],
            timeout_seconds,
        )
    else:
        metadata = work_dir / f"{artifact_name}.hdr10plus.json"
        await _run(
            [binary, "extract", str(input_source), "-o", str(metadata)],
            timeout_seconds,
        )
        await _run(
            [
                binary,
                "plot",
                str(metadata),
                "-t",
                f"HDR10+ Plot - {stem}",
                "-o",
                str(output),
            ],
            timeout_seconds,
        )
    if not output.is_file():
        raise RuntimeError(
            f"{TOOLS[kind]['command']} did not create {output.name}"
        )
    return output


def _selected_trackers(meta: Meta) -> list[Any]:
    selected = meta.trackers
    if isinstance(selected, str):
        return [selected]
    return list(selected)


def _tracker_plot_enabled(
    tracker: Any, tracker_configs: dict[str, Any]
) -> bool:
    if not isinstance(tracker, str):
        return False
    tracker_config = tracker_configs.get(tracker.upper())
    if not isinstance(tracker_config, dict):
        return False
    typed_config = cast(dict[str, Any], tracker_config)
    return bool(typed_config.get("add_dynamic_hdr_plot", False))


def dynamic_hdr_plot_enabled(meta: Meta, config: dict[str, Any]) -> bool:
    """Return whether plots are enabled globally, explicitly, or by an active tracker."""
    if meta.dynamic_hdr_plot or config["DEFAULT"].get(
        "add_dynamic_hdr_plot", False
    ):
        return True
    tracker_configs = config.get("TRACKERS", {})
    return any(
        _tracker_plot_enabled(tracker, tracker_configs)
        for tracker in _selected_trackers(meta)
    )


def _plot_cache_path(meta: Meta) -> Path:
    return (
        release_temp_dir(meta.base_dir, meta.uuid)
        / "dynamic_hdr_plot_images.json"
    )


def _cached_plot_images(
    cache_path: Path, fingerprint: str
) -> list[Any] | None:
    try:
        cache = cast(
            dict[str, Any], json.loads(cache_path.read_text(encoding="utf-8"))
        )
    except OSError, ValueError, TypeError:
        return None
    if cache.get("cache_version") != CACHE_VERSION:
        return None
    if cache.get("fingerprint") != fingerprint:
        return None
    images = cache.get("dynamic_hdr_plot_images")
    return cast(list[Any], images) if isinstance(images, list) else None


def _plot_jobs(
    sources: list[Path], formats: list[str]
) -> list[tuple[str, Path]]:
    return [(kind, source) for source in sources for kind in formats]


def _tool_timeout(config: dict[str, Any]) -> int:
    return min(
        _positive_config_int(config, "dynamic_hdr_plot_tool_timeout", 3600),
        MAX_TOOL_TIMEOUT_SECONDS,
    )


async def _resolved_tool(
    kind: str,
    tools: dict[str, str],
    meta: Meta,
    config: dict[str, Any],
) -> str:
    if kind not in tools:
        tools[kind] = configured_binary(
            f"{TOOLS[kind]['command']}_path", config
        ) or await get_tool(meta.base_dir, kind)
    return tools[kind]


async def _generate_jobs(
    meta: Meta,
    config: dict[str, Any],
    jobs: list[tuple[str, Path]],
    output_dir: Path,
    timeout_seconds: int,
) -> list[str]:
    tools: dict[str, str] = {}
    ffmpeg_binary = configured_binary("ffmpeg_path", config) or "ffmpeg"
    generated: list[str] = []
    for kind, source in jobs:
        try:
            binary = await _resolved_tool(kind, tools, meta, config)
            plot = await _generate_plot(
                binary,
                kind,
                source,
                output_dir,
                timeout_seconds,
                ffmpeg_binary,
            )
            generated.append(str(plot))
        except Exception as error:
            detail = f"{kind} plot failed for {source.name}: {error!s}"
            logger.warning(f"[yellow]{detail}[/yellow]")
    return generated


def _should_upload_plots(
    meta: Meta, generated: list[str], uploadscreens_manager: Any
) -> bool:
    return bool(
        generated
        and uploadscreens_manager
        and not meta.skip_imghost_upload
        and not meta.debug
    )


def _write_plot_cache(
    cache_path: Path, fingerprint: str, images: list[Any]
) -> None:
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": CACHE_VERSION,
                "fingerprint": fingerprint,
                "dynamic_hdr_plot_images": images,
            },
            indent=4,
        ),
        encoding="utf-8",
    )


async def _upload_generated_plots(
    meta: Meta,
    generated: list[str],
    uploadscreens_manager: Any,
    cache_path: Path,
    fingerprint: str,
) -> None:
    if not _should_upload_plots(meta, generated, uploadscreens_manager):
        return
    try:
        images, _ = await uploadscreens_manager.upload_screens(
            meta, len(generated), 1, 0, len(generated), generated, {}
        )
    except Exception as error:
        logger.error(
            f"[red]Error uploading dynamic HDR plots: {error!s}[/red]"
        )
        return
    if not images:
        return
    meta.dynamic_hdr_plot_images = images
    _write_plot_cache(cache_path, fingerprint, images)


async def process_dynamic_hdr_plots(
    meta: Meta, config: dict[str, Any], uploadscreens_manager: Any = None
) -> list[str]:
    """Generate, cache and upload plots for detected dynamic HDR formats."""
    if meta.dynamic_hdr_plot_images:
        return []
    formats = _formats(meta)
    if not formats:
        logger.info(
            "[cyan]Dynamic HDR plot skipped: no Dolby Vision or HDR10+ metadata detected.[/cyan]"
        )
        return []
    sources = _source_files(
        meta, _positive_config_int(config, "dynamic_hdr_plot_max_files", 1)
    )
    if not sources:
        logger.warning(
            "[yellow]Dynamic HDR plot skipped: no supported video source found.[/yellow]"
        )
        return []

    output_dir = dynamic_hdr_plots_dir(meta.base_dir, meta.uuid)
    cache_path = _plot_cache_path(meta)
    fingerprint = _fingerprint(sources, formats)
    cached_images = _cached_plot_images(cache_path, fingerprint)
    if cached_images is not None:
        meta.dynamic_hdr_plot_images = cached_images
        return []

    logger.info(
        "[yellow]Generating dynamic HDR plots reads each selected video file in full; this may take a while for large releases.[/yellow]"
    )
    generated = await _generate_jobs(
        meta,
        config,
        _plot_jobs(sources, formats),
        output_dir,
        _tool_timeout(config),
    )
    await _upload_generated_plots(
        meta,
        generated,
        uploadscreens_manager,
        cache_path,
        fingerprint,
    )
    return generated
