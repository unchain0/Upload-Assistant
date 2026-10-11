# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import gc
import json
import os
import platform
import random
import re
import time
import traceback
import urllib.parse
import uuid
import zipfile
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

import ffmpeg
from defusedxml import ElementTree

from src.domain_models.errors import ScreenshotCaptureError
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.filesystem.screenshot_manifest import (
    clear_group as clear_screenshot_group,
)
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.filesystem.screenshot_manifest import (
    register as register_screenshots,
)
from src.integrations.filesystem.temp_paths import artwork_dir, screenshots_dir
from src.integrations.image_hosts.contracts import image_host_size_within_limit
from src.integrations.media.artwork import (
    is_public_http_url,
    is_valid_cover_image,
    is_valid_image_bytes,
)
from src.integrations.media.media_info import MediaInfo
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)

default_config: dict[str, Any] = {}
task_limit = 1
cutoff = 1
ffmpeg_limit = False
ffmpeg_is_good = False
use_libplacebo = True
tone_map = False
ffmpeg_compression = "6"
LOSTIMG_MIN_SIZE = 75_000
LOSTIMG_MAX_SIZE = 20_000_000
COMIC_IMAGE_MAX_BYTES = 64 * 1024 * 1024
COMIC_IMAGE_COPY_CHUNK_BYTES = 1024 * 1024


def is_valid_lostimg_image_size(image_size: int) -> bool:
    """Return whether an image meets Lostimg's accepted size range."""
    return LOSTIMG_MIN_SIZE < image_size <= LOSTIMG_MAX_SIZE


_UNRESTRICTED_CAPTURE_HOSTS = frozenset(
    {
        "lensdump",
        "ptscreens",
        "onlyimage",
        "dalexni",
        "zipline",
        "midnightscene",
        "passtheimage",
        "seedpool_cdn",
        "sharex",
        "utppm",
    }
)


def _capture_host_size_policy(host: str) -> str:
    if "imgbb" in host:
        return "imgbb"
    if host in {"imgbox", "pixhost"}:
        return host
    return host


def _capture_size_is_valid(
    host: str | None, image_size: int, *, minimum_size: int = 75_000
) -> bool:
    """Return whether a captured frame can be submitted to the selected host."""
    if image_size <= minimum_size:
        return False
    normalized = (host or "").strip().lower()
    policy = _capture_host_size_policy(normalized)
    if policy in {"imgbb", "imgbox", "pixhost"}:
        return image_host_size_within_limit(policy, image_size)
    if policy == "lostimg":
        return is_valid_lostimg_image_size(image_size)
    return policy in _UNRESTRICTED_CAPTURE_HOSTS


def _should_cleanup_after_capture(
    meta: Meta, cleanup_after_capture: bool
) -> bool:
    """Return whether capture owns the final cleanup for this release."""

    multi_screens = int(default_config.get("multiScreens", 2))
    one_disc = len(meta.discs) <= 1
    return cleanup_after_capture and (
        (not meta.tv_pack and one_disc) or multi_screens == 0
    )


def _positive_config_int(key: str, default: int) -> int:
    try:
        return max(1, int(default_config.get(key, default) or default))
    except TypeError, ValueError:
        return default


def xxx_contact_sheet_settings() -> tuple[int, int, int]:
    """Return configured XXX contact-sheet rows, columns, and video limit."""
    return (
        _positive_config_int("xxx_contact_sheet_rows", 12),
        _positive_config_int("xxx_contact_sheet_columns", 5),
        _positive_config_int("xxx_contact_sheet_max_videos", 6),
    )


def xxx_contact_sheet_animation_settings() -> tuple[bool, float]:
    """Return whether XXX contact sheets are animated and their duration."""
    animated = _as_bool(
        default_config.get("xxx_contact_sheet_animated_webp"), default=False
    )
    try:
        duration = max(
            0.1,
            float(
                default_config.get("xxx_contact_sheet_animation_seconds", 5)
                or 5
            ),
        )
    except TypeError, ValueError:
        duration = 5.0
    return animated, duration


def _xxx_contact_sheet_fontfile() -> str | None:
    candidates = (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _xxx_contact_sheet_title_filter(
    stream: Any, title: str, include_title: bool, fontfile: str | None
) -> Any:
    if not include_title or fontfile is None:
        return stream
    escaped_title = (
        title.replace("\\", r"\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
    )
    return stream.filter(
        "pad", width="iw", height="ih+44", x=0, y=44, color="black"
    ).filter(
        "drawtext",
        text=escaped_title,
        x="(w-text_w)/2",
        y=10,
        fontsize=24,
        fontcolor="white",
        fontfile=fontfile,
    )


def _xxx_contact_sheet_timestamp_filter(
    stream: Any, timestamp: str, fontfile: str | None
) -> Any:
    if fontfile is None:
        return stream
    return stream.filter(
        "drawtext",
        text=timestamp,
        x=8,
        y="h-text_h-8",
        fontsize=18,
        fontcolor="white",
        box=1,
        boxcolor="black@0.65",
        boxborderw=4,
        fontfile=fontfile,
    )


def _format_contact_sheet_timestamp(seconds: float) -> str:
    whole_seconds = max(0, int(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _xxx_contact_sheet_static_stream(
    video_path: Path,
    frame_count: int,
    duration: float,
    columns: int,
    rows: int,
    fontfile: str | None,
) -> Any:
    stream = ffmpeg.input(str(video_path))
    stream = _xxx_contact_sheet_timestamp_filter(
        stream, r"%{pts\\:hms}", fontfile
    )
    return (
        stream.filter("fps", fps=f"{frame_count}/{duration:.6f}")
        .filter("scale", 320, -2)
        .filter("tile", layout=f"{columns}x{rows}", margin=2, padding=2)
    )


def _xxx_contact_sheet_animated_stream(
    video_path: Path,
    frame_count: int,
    duration: float,
    columns: int,
    rows: int,
    animation_seconds: float,
    fontfile: str | None,
) -> Any:
    max_start = max(0.0, duration - animation_seconds)
    streams = []
    for index in range(frame_count):
        start_time = max_start * index / max(frame_count - 1, 1)
        stream = (
            ffmpeg.input(str(video_path), ss=start_time, t=animation_seconds)
            .filter("fps", fps=8)
            .filter("scale", 320, -2)
        )
        stream = _xxx_contact_sheet_timestamp_filter(
            stream, _format_contact_sheet_timestamp(start_time), fontfile
        ).filter("setpts", "PTS-STARTPTS")
        streams.append(stream)
    rows_streams = [
        ffmpeg.filter(
            streams[row * columns : (row + 1) * columns],
            "hstack",
            inputs=columns,
        )
        for row in range(rows)
    ]
    return ffmpeg.filter(rows_streams, "vstack", inputs=rows)


def _xxx_contact_sheet_video_paths(
    paths: list[str], max_videos: int
) -> list[Path]:
    return [Path(path) for path in paths if Path(path).is_file()][:max_videos]


def _should_reset_xxx_contact_sheets(
    existing_count: int, video_count: int, retake: bool
) -> bool:
    return retake or (0 < existing_count < video_count)


def _existing_xxx_contact_sheets(
    base_dir: str,
    folder_id: str,
    capture_group: str,
    video_count: int,
    retake: bool,
) -> list[str] | None:
    existing = manifest_files(base_dir, folder_id, capture_group)
    if retake:
        clear_screenshot_group(base_dir, folder_id, capture_group)
        return None
    if len(existing) < video_count:
        if existing:
            clear_screenshot_group(base_dir, folder_id, capture_group)
        return None
    return [str(path) for path in existing[:video_count]]


def _xxx_contact_sheet_output_path(
    screenshot_dir: Path, index: int, animated_webp: bool
) -> Path:
    suffix = "webp" if animated_webp else "png"
    return screenshot_dir / f"xxx-contact-sheet-{index}.{suffix}"


def _xxx_contact_sheet_output_options(
    animated_webp: bool, animation_seconds: float
) -> dict[str, Any]:
    if animated_webp:
        return {
            "vcodec": "libwebp_anim",
            "loop": 0,
            "r": 8,
            "t": animation_seconds,
        }
    return {"vframes": 1, "compression_level": ffmpeg_compression}


def _xxx_contact_sheet_stream(
    video_path: Path,
    frame_count: int,
    duration: float,
    columns: int,
    rows: int,
    animation_seconds: float,
    animated_webp: bool,
    fontfile: str | None,
) -> Any:
    if animated_webp:
        return _xxx_contact_sheet_animated_stream(
            video_path,
            frame_count,
            duration,
            columns,
            rows,
            animation_seconds,
            fontfile,
        )
    return _xxx_contact_sheet_static_stream(
        video_path, frame_count, duration, columns, rows, fontfile
    )


async def _xxx_contact_sheet_duration(video_path: Path) -> float:
    probe = await asyncio.to_thread(ffmpeg.probe, str(video_path))
    duration = float(probe["format"]["duration"])
    if duration <= 0:
        raise ValueError("duration must be positive")
    return duration


def _xxx_contact_sheet_command(
    video_path: Path,
    output_path: Path,
    frame_count: int,
    columns: int,
    rows: int,
    animation_seconds: float,
    animated_webp: bool,
    fontfile: str | None,
    include_title: bool,
    ffdebug: bool,
    duration: float,
) -> Any:
    stream = _xxx_contact_sheet_stream(
        video_path,
        frame_count,
        duration,
        columns,
        rows,
        animation_seconds,
        animated_webp,
        fontfile,
    )
    stream = _xxx_contact_sheet_title_filter(
        stream, video_path.name, include_title, fontfile
    )
    return ffmpeg.output(
        stream,
        str(output_path),
        **_xxx_contact_sheet_output_options(animated_webp, animation_seconds),
    ).global_args("-y", "-loglevel", "verbose" if ffdebug else "quiet")


def _xxx_contact_sheet_result(
    video_path: Path,
    output_path: Path,
    return_code: int | None,
    stderr: bytes,
) -> str | None:
    if return_code == 0 and output_path.is_file():
        return str(output_path)
    logger.warning(
        f"[yellow]Unable to create XXX contact sheet for {video_path.name}: {stderr.decode(errors='replace')}[/yellow]"
    )
    return None


async def _create_xxx_contact_sheet(
    video_path: Path,
    output_path: Path,
    frame_count: int,
    columns: int,
    rows: int,
    animation_seconds: float,
    animated_webp: bool,
    fontfile: str | None,
    include_title: bool,
    ffdebug: bool,
) -> str | None:
    try:
        duration = await _xxx_contact_sheet_duration(video_path)
        command = _xxx_contact_sheet_command(
            video_path,
            output_path,
            frame_count,
            columns,
            rows,
            animation_seconds,
            animated_webp,
            fontfile,
            include_title,
            ffdebug,
            duration,
        )
        return_code, _stdout, stderr = await run_ffmpeg(command)
    except Exception as error:
        logger.warning(
            f"[yellow]Unable to create XXX contact sheet for {video_path.name}: {error}[/yellow]"
        )
        return None
    return _xxx_contact_sheet_result(
        video_path, output_path, return_code, stderr
    )


def _xxx_contact_sheet_font() -> str | None:
    fontfile = _xxx_contact_sheet_fontfile()
    if fontfile is None:
        logger.warning(
            "[yellow]No system font found for XXX contact-sheet labels; generating sheets without labels.[/yellow]"
        )
    return fontfile


async def _xxx_contact_sheet_results(
    video_paths: list[Path],
    screenshot_dir: Path,
    rows: int,
    columns: int,
    animated_webp: bool,
    animation_seconds: float,
    fontfile: str | None,
    meta: Meta,
) -> list[str]:
    results: list[str] = []
    frame_count = rows * columns
    include_title = len(video_paths) > 1
    for index, video_path in enumerate(video_paths, start=1):
        output_path = _xxx_contact_sheet_output_path(
            screenshot_dir, index, animated_webp
        )
        if output_path.exists() and not meta.retake:
            results.append(str(output_path))
            continue
        created = await _create_xxx_contact_sheet(
            video_path,
            output_path,
            frame_count,
            columns,
            rows,
            animation_seconds,
            animated_webp,
            fontfile,
            include_title,
            bool(meta.ffdebug),
        )
        if created:
            results.append(created)
    return results


def _register_xxx_contact_sheet_results(
    base_dir: str,
    folder_id: str,
    capture_group: str,
    results: list[str],
) -> list[str]:
    if not results:
        return []
    return [
        str(path)
        for path in register_screenshots(
            base_dir, folder_id, results, capture_group
        )
    ]


async def xxx_contact_sheets(
    paths: list[str],
    folder_id: str,
    base_dir: str,
    meta: Meta,
    capture_group: str = "main",
) -> list[str]:
    """Create one evenly sampled contact sheet for each selected XXX video."""
    rows, columns, max_videos = xxx_contact_sheet_settings()
    animated_webp, animation_seconds = xxx_contact_sheet_animation_settings()
    video_paths = _xxx_contact_sheet_video_paths(paths, max_videos)
    if not video_paths:
        meta.screens = 0
        return []
    existing = _existing_xxx_contact_sheets(
        base_dir,
        folder_id,
        capture_group,
        len(video_paths),
        bool(meta.retake),
    )
    if existing is not None:
        meta.screens = len(existing)
        return existing
    results = await _xxx_contact_sheet_results(
        video_paths,
        screenshots_dir(base_dir, folder_id),
        rows,
        columns,
        animated_webp,
        animation_seconds,
        _xxx_contact_sheet_font(),
        meta,
    )
    sheets = _register_xxx_contact_sheet_results(
        base_dir, folder_id, capture_group, results
    )
    meta.screens = len(sheets)
    return sheets


def compile_ffmpeg_command(command: Any) -> list[str]:
    """Compile an ffmpeg-python command into subprocess-safe string arguments."""
    return [str(argument) for argument in command.compile()]


def _ffmpeg_node_filename(node: Any) -> str:
    kwargs = getattr(node, "kwargs", {})
    if not isinstance(kwargs, dict):
        return ""
    filename = cast(dict[str, Any], kwargs).get("filename")
    return str(filename) if filename is not None else ""


def _ffmpeg_upstream_nodes(node: Any) -> list[Any]:
    incoming_edges = getattr(node, "incoming_edges", ())
    return [
        upstream
        for edge in incoming_edges
        if (upstream := getattr(edge, "upstream_node", None)) is not None
    ]


def _ffmpeg_graph_output_path(command: Any) -> str:
    nodes = [getattr(command, "node", None)]
    visited: set[int] = set()
    while nodes:
        node = nodes.pop()
        if node is None or id(node) in visited:
            continue
        visited.add(id(node))
        filename = _ffmpeg_node_filename(node)
        if filename:
            return filename
        nodes.extend(_ffmpeg_upstream_nodes(node))
    return ""


def get_ffmpeg_output_path(command: Any, cmd_list: list[str]) -> str:
    """Return ffmpeg-python's output filename without relying on argument order."""
    return _ffmpeg_graph_output_path(command) or (
        cmd_list[-1] if cmd_list else ""
    )


algorithm = "mobius"
desat = 10.0


def _config_int(settings: Mapping[str, Any], key: str, default: int) -> int:
    try:
        return int(settings.get(key, default) or default)
    except TypeError, ValueError:
        return default


def _config_float(
    settings: Mapping[str, Any], key: str, default: float
) -> float:
    try:
        return float(settings.get(key, default) or default)
    except TypeError, ValueError:
        return default


def _default_capture_config(config: Mapping[str, Any]) -> dict[str, Any]:
    default_section = config.get("DEFAULT", {})
    return (
        cast(dict[str, Any], default_section)
        if isinstance(default_section, Mapping)
        else {}
    )


def _apply_config(config: Mapping[str, Any]) -> None:
    global default_config, task_limit, cutoff
    global ffmpeg_limit, ffmpeg_is_good, use_libplacebo
    global tone_map, ffmpeg_compression, algorithm, desat

    default_config = _default_capture_config(config)
    task_limit = _config_int(default_config, "process_limit", 1)
    cutoff = _config_int(default_config, "cutoff_screens", 1)
    ffmpeg_limit = default_config.get("ffmpeg_limit", False)
    ffmpeg_is_good = default_config.get("ffmpeg_is_good", False)
    use_libplacebo = default_config.get("use_libplacebo", True)
    tone_map = default_config.get("tone_map", False)
    ffmpeg_compression = str(default_config.get("ffmpeg_compression", "6"))
    algorithm = str(default_config.get("algorithm", "mobius")).strip()
    desat = _config_float(default_config, "desat", 10.0)


def discard_smallest_capture_result(capture_results: list[str]) -> str | None:
    """Delete and remove the smallest image produced by this capture batch."""
    smallest: str | None = None
    smallest_size = float("inf")
    for image in capture_results:
        try:
            image_size = Path(image).stat().st_size
        except FileNotFoundError:
            logger.info(f"[red]File not found: {image}[/red]")
            continue
        if image_size < smallest_size:
            smallest = image
            smallest_size = image_size

    if smallest is None:
        return None

    logger.debug(
        f"[yellow]Removing smallest image: {smallest} ({smallest_size} bytes)[/yellow]"
    )
    Path(smallest).unlink()
    capture_results.remove(smallest)
    return smallest


def _ffreport_output_allowed(output_path: str) -> bool:
    return bool(
        output_path
        and output_path not in {"-", "pipe:"}
        and not output_path.startswith("pipe:")
    )


def _ffreport_environment(command: Any, cmd_list: list[str]) -> dict[str, str]:
    process_env = os.environ.copy()
    output_path = get_ffmpeg_output_path(command, cmd_list)
    if not _ffreport_output_allowed(output_path):
        process_env.pop("FFREPORT", None)
        return process_env
    report_path = (
        Path(output_path).resolve().parent / f"ffmpeg-{uuid.uuid4().hex}.log"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path_value = report_path.as_posix().replace(":", r"\:")
    process_env["FFREPORT"] = f"file={report_path_value}:level=32"
    return process_env


def _linux_ffmpeg_arch() -> str | None:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd"
    if machine in {"aarch64", "arm64"}:
        return "arm"
    return None


def _bundled_ffmpeg_binary() -> str:
    if platform.system() != "Linux":
        return ""
    arch = _linux_ffmpeg_arch()
    if arch is None:
        return ""
    candidate = (
        Path(__file__).parent.parent / "bin" / "ffmpeg" / arch / "ffmpeg"
    )
    return str(candidate) if candidate.exists() else ""


def _resolved_ffmpeg_command(command: Any) -> tuple[list[str], dict[str, str]]:
    cmd_list = compile_ffmpeg_command(command)
    process_env = _ffreport_environment(command, cmd_list)
    configured = configured_binary("ffmpeg_path", {"DEFAULT": default_config})
    binary = configured or _bundled_ffmpeg_binary()
    if binary:
        cmd_list[0] = binary
    return cmd_list, process_env


async def _terminate_ffmpeg_process(
    process: asyncio.subprocess.Process,
) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def _communicate_ffmpeg_process(
    process: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    try:
        return await process.communicate()
    except asyncio.CancelledError:
        await _terminate_ffmpeg_process(process)
        raise


async def run_ffmpeg(command: Any) -> tuple[int | None, bytes, bytes]:
    cmd_list, process_env = _resolved_ffmpeg_command(command)
    # Spawn validated configured/bundled/system ffmpeg with exec-form argv. The env is local process state, not remote input.
    process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit,dangerous-asyncio-create-exec-tainted-env-args
        *cmd_list,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=process_env,
    )
    stdout, stderr = await _communicate_ffmpeg_process(process)
    return process.returncode, stdout, stderr


async def sanitize_filename(filename: str) -> str:
    # Replace invalid characters like colons with an underscore
    return re.sub(r'[<>:"/\\|?*]', "_", filename)


def round_to_even(value: float) -> int:
    rounded = round(value)
    if rounded % 2 != 0:
        rounded += 1
    return rounded


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def should_scale_screenshots_for_par(
    config: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether screenshots should be converted to square-pixel dimensions."""
    settings = default_config if config is None else config
    return _as_bool(settings.get("scale_screenshots_for_par"), default=False)


def screenshot_par_scale_factors(
    width: float,
    height: float,
    pixel_aspect_ratio: float,
    display_aspect_ratio: float,
    apply_par_scaling: bool | None = None,
) -> tuple[float, float]:
    """Return the width and height scale factors for a screenshot.

    Screenshots retain their MediaInfo-reported coded dimensions by default.
    PAR correction remains available for non-square-pixel sources when a user
    explicitly enables ``scale_screenshots_for_par``.
    """
    if apply_par_scaling is None:
        apply_par_scaling = should_scale_screenshots_for_par()
    if not apply_par_scaling or pixel_aspect_ratio == 1:
        return 1.0, 1.0
    if pixel_aspect_ratio < 1:
        new_height = display_aspect_ratio * height
        return 1.0, width / new_height
    return pixel_aspect_ratio, 1.0


def _frame_rate(meta: Meta) -> float:
    return 24.0 if meta.frame_rate is None else float(meta.frame_rate)


def _overlay_resolution(meta: Meta, fallback: str) -> int:
    raw_resolution = (
        fallback if meta.resolution is None else str(meta.resolution)
    )
    digits = "".join(filter(str.isdigit, raw_resolution))
    return int(digits)


def _reasonable_pts_time(pts_time: float, seek_time: float) -> bool:
    return pts_time > 1.0 and abs(pts_time - seek_time) < 10


def _overlay_frame_number(
    meta: Meta, seek_time: float, frame_info: Mapping[str, Any]
) -> int:
    frame_rate = _frame_rate(meta)
    frame_number = int(seek_time * frame_rate)
    try:
        pts_time = float(frame_info.get("pts_time", 0))
    except TypeError, ValueError:
        return frame_number
    if _reasonable_pts_time(pts_time, seek_time):
        return int(pts_time * frame_rate)
    return frame_number


def _overlay_drawtext(
    text: str,
    *,
    font_size: int,
    x_position: int,
    y_position: int,
    border_width: int,
) -> str:
    return (
        f"drawtext=text='{text}':fontcolor=white:fontsize={font_size}:"
        f"x={x_position}:y={y_position}:borderw={border_width}:bordercolor=black"
    )


def _frame_overlay_filters(
    meta: Meta,
    seek_time: float,
    *,
    hdr_tonemap: bool,
    base_resolution: int,
) -> list[str]:
    if not meta.frame_overlay:
        return []
    frame_info = cast(
        Mapping[str, Any], meta.frame_info_map.get(str(seek_time), {})
    )
    frame_number = _overlay_frame_number(meta, seek_time, frame_info)
    frame_type = frame_info.get("frame_type", "Unknown")
    text_size = int(default_config.get("overlay_text_size", 18))
    resolution = _overlay_resolution(meta, f"{base_resolution}p")
    font_size = round(text_size * resolution / base_resolution)
    border_width = round(2 * resolution / base_resolution)
    x_position = round(10 * resolution / base_resolution)
    line_spacing = round(font_size * 1.1)
    lines = [
        (rf"Frame Number\: {frame_number}", x_position),
        (rf"Frame Type\: {frame_type}", x_position + line_spacing),
    ]
    if hdr_tonemap:
        lines.append(("Tonemapped HDR", x_position + 2 * line_spacing))
    return [
        _overlay_drawtext(
            text,
            font_size=font_size,
            x_position=x_position,
            y_position=y_position,
            border_width=border_width,
        )
        for text, y_position in lines
    ]


def _software_tonemap_filters(enabled: bool) -> list[str]:
    if not enabled:
        return []
    return [
        "zscale=transfer=linear",
        f"tonemap=tonemap={algorithm}:desat={desat}",
        "zscale=transfer=bt709",
    ]


def _finalize_capture_filters(filters: list[str]) -> str:
    if not filters:
        filters.append("format=rgb24")
    return ",".join(filters)


def _ffmpeg_verbose(loglevel: str, meta: Meta) -> bool:
    return loglevel == "verbose" or meta.debug


def _log_ffmpeg_command(command: Any, loglevel: str, meta: Meta) -> None:
    if not _ffmpeg_verbose(loglevel, meta):
        return
    logger.info(
        f"[cyan]FFmpeg command: {' '.join(compile_ffmpeg_command(command))}[/cyan]"
    )


def _log_ffmpeg_streams(loglevel: str, stdout: bytes, stderr: bytes) -> None:
    if loglevel != "verbose":
        return
    if stdout:
        logger.info(
            f"[blue]FFmpeg stdout:[/blue]\n{stdout.decode('utf-8', errors='replace')}"
        )
    if stderr:
        logger.info(
            f"[yellow]FFmpeg stderr:[/yellow]\n{stderr.decode('utf-8', errors='replace')}"
        )


@dataclass(frozen=True)
class _DiscSourceInfo:
    file_path: str
    length: float
    frame_rate: float | None
    keyframe: str


@dataclass(frozen=True)
class _DiscCapturePlan:
    meta: Meta
    img_host: str | None
    base_dir: str
    folder_id: str
    sanitized_filename: str
    capture_group: str
    screenshot_dir: Path
    source: _DiscSourceInfo
    num_screens: int
    existing_screens: list[str]
    hdr_tonemap: bool
    ss_times: list[str]
    force_screenshots: bool
    cleanup_after_capture: bool
    start_time: float
    loglevel: str


def _disc_remote_images(meta: Meta) -> list[dict[str, Any]]:
    return [
        image
        for image in meta.image_list
        if str(image.get("img_url", "")).startswith("http")
    ]


def _disc_initial_count(
    screens: int,
    num_screens: int,
    image_list: list[dict[str, str]] | None,
) -> int | None:
    requested = num_screens or screens
    if requested == 0:
        return None
    if image_list and len(image_list) >= requested:
        return None
    return requested


def _disc_duration_seconds(length: object) -> float:
    return sum(
        float(value) * 60**index
        for index, value in enumerate(reversed(str(length or "0").split(":")))
    )


def _disc_file_path(root_path: str, target_name: str) -> str:
    for root, _dirs, files in os.walk(root_path):
        for name in files:
            if name.lower() == target_name.lower():
                return str(Path(root) / name)
    return ""


def _disc_longest_source(bdinfo: Mapping[str, Any]) -> tuple[str, float]:
    root_path = str(bdinfo.get("path", ""))
    longest_path = ""
    longest_length = 0.0
    for entry in cast(list[Mapping[str, Any]], bdinfo.get("files", [])):
        duration = _disc_duration_seconds(entry.get("length", "0"))
        if duration <= longest_length:
            continue
        longest_length = duration
        longest_path = _disc_file_path(root_path, str(entry.get("file", "")))
    return longest_path, longest_length


def _disc_video_entry(bdinfo: Mapping[str, Any]) -> Mapping[str, Any]:
    videos = cast(list[Mapping[str, Any]], bdinfo.get("video", []))
    return videos[0] if videos else {}


def _disc_frame_rate(bdinfo: Mapping[str, Any]) -> float | None:
    fps_string = _disc_video_entry(bdinfo).get("fps")
    if not fps_string:
        return None
    try:
        return float(str(fps_string).split(" ")[0])
    except ValueError:
        logger.error(
            "[red]Error: Unable to parse frame rate from bdinfo['video'][0]['fps']"
        )
        return None


def _disc_keyframe(bdinfo: Mapping[str, Any]) -> str:
    video = _disc_video_entry(bdinfo)
    if "VC-1" in str(video.get("codec", "")):
        return "nokey"
    return "nokey" if str(video.get("hdr_dv", "")) else "none"


def _disc_source_info(bdinfo: Mapping[str, Any]) -> _DiscSourceInfo:
    file_path, length = _disc_longest_source(bdinfo)
    return _DiscSourceInfo(
        file_path=file_path,
        length=length,
        frame_rate=_disc_frame_rate(bdinfo),
        keyframe=_disc_keyframe(bdinfo),
    )


def _disc_remaining_count(
    screens: int,
    requested: int,
    existing_screens: list[str],
    existing_images: list[dict[str, Any]],
    force_screenshots: bool,
) -> int:
    if force_screenshots:
        return requested
    return max(0, screens - len(existing_screens) - len(existing_images))


def _disc_hdr_tonemap(meta: Meta) -> bool:
    enabled = bool(tone_map and "HDR" in meta.hdr)
    if enabled:
        meta.tonemapped = True
    return enabled


def _disc_should_skip_remote(
    existing_images: list[dict[str, Any]], force_screenshots: bool
) -> bool:
    if force_screenshots:
        return False
    if len(existing_images) < cutoff:
        return False
    logger.info(
        f"[yellow]There are already at least {cutoff} images in the image list. Skipping additional screenshots."
    )
    return True


def _disc_log_reuse(num_screens: int, force_screenshots: bool) -> bool:
    if num_screens != 0 or force_screenshots:
        return False
    logger.info(
        "[bold green]Reusing existing screenshots. No additional screenshots needed."
    )
    return True


@dataclass(frozen=True)
class _DiscPreparation:
    img_host: str | None
    sanitized_filename: str
    group: str
    existing_images: list[dict[str, Any]]
    existing_screens: list[str]
    remaining: int


def _disc_group(capture_group: str | None, sanitized_filename: str) -> str:
    return capture_group if capture_group is not None else sanitized_filename


def _disc_existing_screens(
    base_dir: str, folder_id: str, group: str
) -> list[str]:
    return [path.name for path in manifest_files(base_dir, folder_id, group)]


async def _disc_preparation(
    meta: Meta,
    filename: str,
    folder_id: str,
    base_dir: str,
    image_list: list[dict[str, str]] | None,
    num_screens: int,
    force_screenshots: bool,
    capture_group: str | None,
) -> _DiscPreparation | None:
    img_host = await get_image_host(meta)
    existing_images = _disc_remote_images(meta)
    if _disc_should_skip_remote(existing_images, force_screenshots):
        return None
    requested = _disc_initial_count(meta.screens, num_screens, image_list)
    if requested is None:
        return None
    sanitized_filename = await sanitize_filename(filename)
    group = _disc_group(capture_group, sanitized_filename)
    existing_screens = _disc_existing_screens(base_dir, folder_id, group)
    remaining = _disc_remaining_count(
        meta.screens,
        requested,
        existing_screens,
        existing_images,
        force_screenshots,
    )
    if _disc_log_reuse(remaining, force_screenshots):
        return None
    return _DiscPreparation(
        img_host,
        sanitized_filename,
        group,
        existing_images,
        existing_screens,
        remaining,
    )


def _log_disc_plan(
    meta: Meta,
    source: _DiscSourceInfo,
    preparation: _DiscPreparation,
    force_screenshots: bool,
) -> None:
    logger.debug(
        f"File: {source.file_path}, Length: {source.length}, Frame Rate: {source.frame_rate}",
        extra={"markup": False},
    )
    if meta.debug and not force_screenshots:
        total_existing = len(preparation.existing_screens) + len(
            preparation.existing_images
        )
        logger.info(
            f"[bold yellow]Saving Screens... Total needed: {meta.screens}, Existing: {total_existing}, To capture: {preparation.remaining}"
        )


def _disc_effective_frame_rate(source: _DiscSourceInfo) -> float:
    return 24.0 if source.frame_rate is None else source.frame_rate


def _disc_start_time(meta: Meta) -> float:
    return time.time() if meta.debug else 0.0


def _disc_loglevel(ffdebug: bool) -> str:
    return "verbose" if ffdebug else "quiet"


async def _build_disc_capture_plan(
    meta: Meta,
    bdinfo: Mapping[str, Any],
    folder_id: str,
    base_dir: str,
    preparation: _DiscPreparation,
    ffdebug: bool,
    force_screenshots: bool,
    cleanup_after_capture: bool,
) -> _DiscCapturePlan:
    source = _disc_source_info(bdinfo)
    _log_disc_plan(meta, source, preparation, force_screenshots)
    ss_times = await valid_ss_time(
        [],
        preparation.remaining,
        source.length,
        _disc_effective_frame_rate(source),
        meta,
        retake=force_screenshots,
    )
    return _DiscCapturePlan(
        meta=meta,
        img_host=preparation.img_host,
        base_dir=base_dir,
        folder_id=folder_id,
        sanitized_filename=preparation.sanitized_filename,
        capture_group=preparation.group,
        screenshot_dir=screenshots_dir(base_dir, folder_id),
        source=source,
        num_screens=preparation.remaining,
        existing_screens=preparation.existing_screens,
        hdr_tonemap=_disc_hdr_tonemap(meta),
        ss_times=ss_times,
        force_screenshots=force_screenshots,
        cleanup_after_capture=cleanup_after_capture,
        start_time=_disc_start_time(meta),
        loglevel=_disc_loglevel(ffdebug),
    )


async def _prepare_disc_capture_plan(
    meta: Meta,
    filename: str,
    bdinfo: Mapping[str, Any],
    folder_id: str,
    base_dir: str,
    image_list: list[dict[str, str]] | None,
    ffdebug: bool,
    num_screens: int,
    force_screenshots: bool,
    cleanup_after_capture: bool,
    capture_group: str | None,
) -> _DiscCapturePlan | None:
    preparation = await _disc_preparation(
        meta,
        filename,
        folder_id,
        base_dir,
        image_list,
        num_screens,
        force_screenshots,
        capture_group,
    )
    if preparation is None:
        return None
    return await _build_disc_capture_plan(
        meta,
        bdinfo,
        folder_id,
        base_dir,
        preparation,
        ffdebug,
        force_screenshots,
        cleanup_after_capture,
    )


def _disc_capture_path(plan: _DiscCapturePlan, index: int) -> str:
    image_index = len(plan.existing_screens) + index
    return str(
        (
            plan.screenshot_dir
            / f"{plan.sanitized_filename}-{image_index}.png"
        ).resolve()
    )


def _disc_frame_info_required(plan: _DiscCapturePlan, index: int) -> bool:
    if plan.meta.retake:
        return True
    return not Path(_disc_capture_path(plan, index)).exists()


def _disc_frame_info_tasks(
    plan: _DiscCapturePlan,
) -> list[tuple[int, Awaitable[dict[str, Any]]]]:
    required_times = plan.num_screens + 1
    if len(plan.ss_times) < required_times:
        raise ValueError(
            "Disc capture plan requires "
            f"{required_times} screenshot timestamps, got {len(plan.ss_times)}"
        )
    return [
        (
            index,
            get_frame_info(
                plan.source.file_path, plan.ss_times[index], plan.meta
            ),
        )
        for index in range(plan.num_screens + 1)
        if _disc_frame_info_required(plan, index)
    ]


def _disc_frame_info_map(
    plan: _DiscCapturePlan,
    indexed_tasks: list[tuple[int, Awaitable[dict[str, Any]]]],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        plan.ss_times[index]: info
        for (index, _), info in zip(indexed_tasks, results, strict=False)
    }


async def _collect_disc_frame_info(plan: _DiscCapturePlan) -> None:
    if not plan.meta.frame_overlay:
        return
    logger.info("[yellow]Getting frame information for overlays...")
    indexed_tasks = _disc_frame_info_tasks(plan)
    results = await asyncio.gather(*[task for _, task in indexed_tasks])
    plan.meta.frame_info_map = _disc_frame_info_map(
        plan, indexed_tasks, results
    )
    logger.debug(
        f"[cyan]Collected frame information for {len(results)} frames"
    )


async def _disc_capture_with_semaphore(
    semaphore: asyncio.Semaphore,
    plan: _DiscCapturePlan,
    index: int,
) -> tuple[int, str] | None:
    async with semaphore:
        return await capture_disc_task(
            index,
            plan.source.file_path,
            plan.ss_times[index],
            _disc_capture_path(plan, index),
            plan.source.keyframe,
            plan.loglevel,
            plan.hdr_tonemap,
            plan.meta,
        )


def _disc_filtered_results(
    results: list[tuple[int, str] | None],
) -> list[str]:
    filtered = [result for result in results if result is not None]
    if len(filtered) != len(results):
        logger.warning(
            f"[yellow]Warning: {len(results) - len(filtered)} capture tasks returned invalid results."
        )
    filtered.sort(key=lambda item: item[0])
    return [item[1] for item in filtered]


def _discard_smallest_disc_result(
    capture_results: list[str], num_screens: int
) -> None:
    if not capture_results or len(capture_results) <= num_screens:
        return
    try:
        smallest = min(capture_results, key=os.path.getsize)
        logger.debug(
            f"[yellow]Removing smallest image: {smallest} ({Path(smallest).stat().st_size} bytes)"
        )
        Path(smallest).unlink()
        capture_results.remove(smallest)
    except Exception as error:
        logger.error(f"[red]Error removing smallest image: {error!s}")


async def _run_disc_capture_batch(plan: _DiscCapturePlan) -> list[str]:
    semaphore = asyncio.Semaphore(task_limit)
    tasks = [
        _disc_capture_with_semaphore(semaphore, plan, index)
        for index in range(plan.num_screens + 1)
    ]
    results = await asyncio.gather(*tasks)
    capture_results = _disc_filtered_results(results)
    _discard_smallest_disc_result(capture_results, plan.num_screens)
    if not plan.force_screenshots and plan.meta.debug:
        logger.info(
            f"[green]Successfully captured {len(capture_results)} screenshots."
        )
    return capture_results


def _disc_image_needs_retake(plan: _DiscCapturePlan, image_path: str) -> bool:
    image_size = Path(image_path).stat().st_size
    logger.debug(
        f"[yellow]Checking image {image_path} (size: {image_size} bytes) for image host: {plan.img_host}[/yellow]"
    )
    needs_retake = not _capture_size_is_valid(plan.img_host, image_size)
    if needs_retake:
        logger.info(
            f"[yellow]Image {image_path} ({image_size} bytes) does not meet the capture requirements for {plan.img_host or 'an unknown host'}, retaking."
        )
    else:
        logger.debug(
            f"[green]Image {image_path} meets size requirements for {plan.img_host}.[/green]"
        )
    return needs_retake


async def _disc_retake_attempt(
    plan: _DiscCapturePlan,
    image_path: str,
    index: int,
) -> bool:
    Path(image_path).unlink(missing_ok=True)
    random_time = random.uniform(0, plan.source.length)  # nosec B311 - Random screenshot timing, not cryptographic  # noqa: S311
    response = await capture_disc_task(
        index,
        plan.source.file_path,
        str(random_time),
        image_path,
        plan.source.keyframe,
        plan.loglevel,
        plan.hdr_tonemap,
        plan.meta,
    )
    new_size = Path(image_path).stat().st_size
    if _capture_size_is_valid(plan.img_host, new_size):
        logger.info(
            f"[green]Successfully retaken screenshot for: {image_path} ({new_size} bytes)[/green]"
        )
        return True
    logger.info(
        f"[red]Retaken image {response} does not meet the size requirements for {plan.img_host}. Retrying...[/red]"
    )
    return False


async def _disc_retake_image(plan: _DiscCapturePlan, image_path: str) -> bool:
    index = _image_capture_index(image_path)
    for attempt in range(1, 4):
        logger.info(
            f"[yellow]Retaking screenshot for: {image_path} (Attempt {attempt}/3)[/yellow]"
        )
        try:
            if await _disc_retake_attempt(plan, image_path, index):
                return True
        except Exception as error:
            logger.error(
                f"[red]Error retaking screenshot for {image_path}: {error}[/red]"
            )
    logger.info(
        f"[red]All retry attempts failed for {image_path}. Skipping.[/red]"
    )
    return False


async def _validated_disc_image(
    plan: _DiscCapturePlan, image_path: str
) -> tuple[str | None, str | None]:
    if "Error" in image_path:
        logger.info(f"[red]{image_path}")
        return None, None
    if not _disc_image_needs_retake(plan, image_path):
        return image_path, None
    if await _disc_retake_image(plan, image_path):
        return image_path, None
    return None, image_path


def _log_remaining_disc_retakes(remaining_retakes: list[str]) -> None:
    if remaining_retakes:
        logger.info(
            f"[red]The following images could not be retaken successfully: {remaining_retakes}[/red]"
        )


async def _validated_disc_results(
    plan: _DiscCapturePlan, capture_results: list[str]
) -> list[str]:
    valid_results: list[str] = []
    remaining_retakes: list[str] = []
    for image_path in capture_results:
        valid, failed = await _validated_disc_image(plan, image_path)
        if valid is not None:
            valid_results.append(valid)
        if failed is not None:
            remaining_retakes.append(failed)
    _log_remaining_disc_retakes(remaining_retakes)
    return valid_results


def _vapoursynth_disc_image_valid(
    plan: _DiscCapturePlan, image_path: Path, before: set[Path]
) -> bool:
    if image_path.resolve() in before:
        return False
    image_size = image_path.stat().st_size
    if plan.img_host != "lostimg":
        return True
    if is_valid_lostimg_image_size(image_size):
        return True
    logger.info(
        f"[red]Image {image_path} with size {image_size} bytes: does not meet size requirements for {plan.img_host}, skipping.[/red]"
    )
    return False


async def _vapoursynth_disc_results(plan: _DiscCapturePlan) -> list[str]:
    from src.integrations.media.vapoursynth import vs_screengn

    before = {path.resolve() for path in plan.screenshot_dir.glob("*.png")}
    vs_screengn(
        source=plan.source.file_path,
        encode=None,
        num=plan.num_screens,
        dir=f"{plan.screenshot_dir}/",
    )
    return [
        str(image_path)
        for image_path in plan.screenshot_dir.glob("*.png")
        if _vapoursynth_disc_image_valid(plan, image_path, before)
    ]


def _log_finished_disc_capture(
    plan: _DiscCapturePlan, valid_results: list[str]
) -> None:
    if not plan.force_screenshots and plan.meta.debug:
        logger.info(
            f"[green]Successfully captured {len(valid_results)} screenshots."
        )
    if plan.meta.debug:
        logger.debug(
            f"Screenshots processed in {time.time() - plan.start_time:.4f} seconds"
        )


def _register_disc_results(
    plan: _DiscCapturePlan, valid_results: list[str]
) -> list[Path]:
    if not valid_results:
        return []
    return register_screenshots(
        plan.base_dir,
        plan.folder_id,
        valid_results,
        plan.capture_group,
    )


async def _finish_disc_capture(
    plan: _DiscCapturePlan, valid_results: list[str]
) -> list[Path]:
    _log_finished_disc_capture(plan, valid_results)
    registered = _register_disc_results(plan, valid_results)
    if _should_cleanup_after_capture(plan.meta, plan.cleanup_after_capture):
        await cleanup_manager.cleanup()
    return registered


async def disc_screenshots(
    meta: Meta,
    filename: str,
    bdinfo: dict[str, Any],
    folder_id: str,
    base_dir: str,
    use_vs: bool,
    image_list: list[dict[str, str]] | None = None,
    ffdebug: bool = False,
    num_screens: int = 0,
    force_screenshots: bool = False,
    cleanup_after_capture: bool = True,
    capture_group: str | None = None,
) -> list[Path]:
    plan = await _prepare_disc_capture_plan(
        meta,
        filename,
        bdinfo,
        folder_id,
        base_dir,
        image_list,
        ffdebug,
        num_screens,
        force_screenshots,
        cleanup_after_capture,
        capture_group,
    )
    if plan is None:
        return []
    await _collect_disc_frame_info(plan)
    if use_vs:
        valid_results = await _vapoursynth_disc_results(plan)
    else:
        capture_results = await _run_disc_capture_batch(plan)
        valid_results = await _validated_disc_results(plan, capture_results)
    return await _finish_disc_capture(plan, valid_results)


def _disc_capture_filters(meta: Meta, ss_time: str, hdr_tonemap: bool) -> str:
    filters = _software_tonemap_filters(hdr_tonemap)
    if hdr_tonemap:
        filters.append("format=rgb24")
    filters.extend(
        _frame_overlay_filters(
            meta,
            float(ss_time),
            hdr_tonemap=hdr_tonemap,
            base_resolution=1080,
        )
    )
    return _finalize_capture_filters(filters)


def _disc_capture_command(
    file: str,
    ss_time: str,
    image_path: str,
    keyframe: str,
    loglevel: str,
    vf_chain: str,
) -> Any:
    return (
        cast(Any, ffmpeg)
        .input(file, ss=ss_time, skip_frame=keyframe)
        .output(
            image_path,
            vframes=1,
            vf=vf_chain,
            compression_level=ffmpeg_compression,
            pred="mixed",
        )
        .global_args("-y", "-loglevel", loglevel, "-hide_banner")
    )


def _disc_capture_result(
    index: int, image_path: str, returncode: int | None, stderr: bytes
) -> tuple[int, str] | None:
    if returncode == 0:
        return index, image_path
    logger.info(
        "[red]FFmpeg error capturing screenshot: "
        f"{stderr.decode('utf-8', errors='replace')}"
    )
    return None


async def capture_disc_task(
    index: int,
    file: str,
    ss_time: str,
    image_path: str,
    keyframe: str,
    loglevel: str,
    hdr_tonemap: bool,
    meta: Meta,
) -> tuple[int, str] | None:
    try:
        command = _disc_capture_command(
            file,
            ss_time,
            image_path,
            keyframe,
            loglevel,
            _disc_capture_filters(meta, ss_time, hdr_tonemap),
        )
        _log_ffmpeg_command(command, loglevel, meta)
        returncode, stdout, stderr = await run_ffmpeg(command)
        _log_ffmpeg_streams(loglevel, stdout, stderr)
        return _disc_capture_result(index, image_path, returncode, stderr)
    except Exception as error:
        logger.error(f"[red]Error capturing screenshot: {error}")
        return None


def _vob_track_duration(track: Mapping[str, Any]) -> float | None:
    try:
        duration = float(track.get("Duration", 0))
    except TypeError, ValueError:
        return None
    return duration if duration > 1 else None


def _vob_track_candidate(
    track: Mapping[str, Any], track_index: int
) -> tuple[float, int] | None:
    duration = _vob_track_duration(track)
    if duration is None:
        return None
    if not track.get("Width") or not track.get("Height"):
        return None
    return duration, track_index


def _vob_longest_valid_track(
    payload: Mapping[str, Any], track_index: int
) -> tuple[float, int] | None:
    tracks = cast(
        list[Mapping[str, Any]], payload.get("media", {}).get("track", [])
    )
    candidates = [
        candidate
        for track in tracks
        if (candidate := _vob_track_candidate(track, track_index)) is not None
    ]
    return max(candidates, key=lambda item: item[0]) if candidates else None


async def _find_good_vob(
    meta: Meta, disc_num: int, main_set: list[str]
) -> tuple[float, int]:
    if not main_set:
        return 300.0, 0
    for index in range(6):
        track_index = index % len(main_set)
        try:
            raw = MediaInfo.parse(
                f"{meta.discs[disc_num]['path']}/VTS_{main_set[track_index]}",
                output="JSON",
            )
            payload = cast(Mapping[str, Any], json.loads(raw))
            candidate = _vob_longest_valid_track(payload, track_index)
            if candidate is not None:
                return candidate
        except Exception as error:
            logger.error(f"[red]Error parsing VOB {track_index}: {error}")
    return 300.0, 0


@dataclass(frozen=True)
class _DvdGeometry:
    width: float
    height: float
    w_sar: float
    h_sar: float
    frame_rate: float


@dataclass(frozen=True)
class _DvdCapturePlan:
    meta: Meta
    disc_num: int
    num_screens: int
    retry_cap: bool
    cleanup_after_capture: bool
    sanitized_disc_name: str
    screenshot_dir: Path
    capture_vob: str
    input_file: str
    vob_length: float
    geometry: _DvdGeometry
    ss_times: list[str]
    image_paths: list[str]


def _dvd_remote_images(meta: Meta) -> list[dict[str, Any]]:
    return [
        image
        for image in meta.image_list
        if str(image.get("img_url", "")).startswith("http")
    ]


def _dvd_screens(meta: Meta) -> int:
    return 6 if meta.screens is None else meta.screens


def _dvd_should_skip_remote(
    existing_images: list[dict[str, Any]], retry_cap: bool
) -> bool:
    if retry_cap:
        return False
    if len(existing_images) < cutoff:
        return False
    logger.info(
        f"[yellow]There are already at least {cutoff} images in the image list. Skipping additional screenshots."
    )
    return True


def _dvd_requested_count(
    meta: Meta,
    disc_num: int,
    num_screens: int,
    existing_images: list[dict[str, Any]],
) -> int | None:
    screens = _dvd_screens(meta)
    requested = num_screens if num_screens else screens - len(existing_images)
    if requested == 0:
        return None
    if disc_num == 0 and len(meta.image_list) >= screens:
        return None
    return requested


def _dvd_main_set(meta: Meta, disc_num: int) -> list[str]:
    values = cast(list[str], meta.discs[disc_num]["main_set"])
    return values[1:] if len(values) > 1 else values


def _dvd_ifo_path(meta: Meta, disc_num: int) -> str:
    first = str(meta.discs[disc_num]["main_set"][0])
    return f"{meta.discs[disc_num]['path']}/VTS_{first[:2]}_0.IFO"


def _dvd_geometry_from_track(
    track: Any,
) -> tuple[float, float, float, float, float]:
    return (
        float(track.pixel_aspect_ratio),
        float(track.display_aspect_ratio),
        float(track.width),
        float(track.height),
        float(track.frame_rate),
    )


def _dvd_geometry(meta: Meta, disc_num: int) -> _DvdGeometry:
    media_info = MediaInfo.parse(
        _dvd_ifo_path(meta, disc_num),
        mediainfo_options={"inform_version": "1"},
    )
    par, dar, width, height, frame_rate = 1.0, 1.0, 0.0, 0.0, 24.0
    tracks = cast(list[Any], getattr(media_info, "tracks", []))
    for track in tracks:
        if track.track_type == "Video":
            par, dar, width, height, frame_rate = _dvd_geometry_from_track(
                track
            )
    w_sar, h_sar = screenshot_par_scale_factors(width, height, par, dar)
    return _DvdGeometry(width, height, w_sar, h_sar, frame_rate)


def _dvd_input_file(meta: Meta, disc_num: int, capture_vob: str) -> str:
    return f"{meta.discs[disc_num]['path']}/VTS_{capture_vob}"


def _dvd_image_paths(
    screenshot_dir: Path, sanitized_disc_name: str, num_screens: int
) -> list[str]:
    return [
        str(screenshot_dir / f"{sanitized_disc_name}-{index}.png")
        for index in range(num_screens + 1)
    ]


def _dvd_existing_local_count(image_paths: list[str], retake: bool) -> int:
    if retake:
        return 0
    return sum(1 for image_path in image_paths if Path(image_path).exists())


def _dvd_local_capture_complete(
    image_paths: list[str], num_screens: int, retake: bool
) -> bool:
    if _dvd_existing_local_count(image_paths, retake) != num_screens:
        return False
    logger.debug(
        "[yellow]The correct number of screenshots already exists. Skipping capture process."
    )
    return True


def _dvd_manifest_reused(
    meta: Meta, sanitized_disc_name: str, num_screens: int
) -> bool:
    existing = manifest_files(meta.base_dir, meta.uuid, sanitized_disc_name)
    if len(existing) < num_screens:
        return False
    logger.info("[bold green]Reusing screenshots")
    return True


async def _prepare_dvd_capture_plan(
    meta: Meta,
    disc_num: int,
    num_screens: int,
    retry_cap: bool,
    cleanup_after_capture: bool,
) -> _DvdCapturePlan | None:
    existing_images = _dvd_remote_images(meta)
    if _dvd_should_skip_remote(existing_images, retry_cap):
        return None
    requested = _dvd_requested_count(
        meta, disc_num, num_screens, existing_images
    )
    if requested is None:
        return None
    sanitized_disc_name = await sanitize_filename(meta.discs[disc_num]["name"])
    if _dvd_manifest_reused(meta, sanitized_disc_name, requested):
        return None
    geometry = _dvd_geometry(meta, disc_num)
    main_set = _dvd_main_set(meta, disc_num)
    vob_length, vob_index = await _find_good_vob(meta, disc_num, main_set)
    capture_vob = main_set[vob_index]
    ss_times = await valid_ss_time(
        [], requested, vob_length, geometry.frame_rate, meta, retake=retry_cap
    )
    screenshot_dir = screenshots_dir(meta.base_dir, meta.uuid)
    image_paths = _dvd_image_paths(
        screenshot_dir, sanitized_disc_name, requested
    )
    if _dvd_local_capture_complete(image_paths, requested, bool(meta.retake)):
        return None
    return _DvdCapturePlan(
        meta=meta,
        disc_num=disc_num,
        num_screens=requested,
        retry_cap=retry_cap,
        cleanup_after_capture=cleanup_after_capture,
        sanitized_disc_name=sanitized_disc_name,
        screenshot_dir=screenshot_dir,
        capture_vob=capture_vob,
        input_file=_dvd_input_file(meta, disc_num, capture_vob),
        vob_length=vob_length,
        geometry=geometry,
        ss_times=ss_times,
        image_paths=image_paths,
    )


def _dvd_frame_info_indices(plan: _DvdCapturePlan) -> list[int]:
    return [
        index
        for index, image_path in enumerate(plan.image_paths)
        if plan.meta.retake or not Path(image_path).exists()
    ]


async def _collect_dvd_frame_info(plan: _DvdCapturePlan) -> None:
    if not plan.meta.frame_overlay:
        return
    logger.debug("[yellow]Getting frame information for overlays...")
    indices = _dvd_frame_info_indices(plan)
    tasks = [
        get_frame_info(plan.input_file, plan.ss_times[index], plan.meta)
        for index in indices
    ]
    results = await asyncio.gather(*tasks)
    plan.meta.frame_info_map = {
        plan.ss_times[index]: info
        for index, info in zip(indices, results, strict=False)
    }
    logger.debug(
        f"[cyan]Collected frame information for {len(results)} frames"
    )


def _dvd_capture_args(
    plan: _DvdCapturePlan, index: int
) -> tuple[int, str, str, str, Meta, float, float, float, float]:
    geometry = plan.geometry
    return (
        index,
        plan.input_file,
        plan.image_paths[index],
        plan.ss_times[index],
        plan.meta,
        geometry.width,
        geometry.height,
        geometry.w_sar,
        geometry.h_sar,
    )


async def _dvd_capture_with_semaphore(
    semaphore: asyncio.Semaphore,
    args: tuple[int, str, str, str, Meta, float, float, float, float],
) -> tuple[int, str | None]:
    async with semaphore:
        return await capture_dvd_screenshot(args)


def _dvd_capture_required(plan: _DvdCapturePlan, index: int) -> bool:
    if plan.meta.retake:
        return True
    return not Path(plan.image_paths[index]).exists()


def _dvd_capture_tasks(
    plan: _DvdCapturePlan, semaphore: asyncio.Semaphore
) -> list[Awaitable[tuple[int, str | None]]]:
    return [
        _dvd_capture_with_semaphore(semaphore, _dvd_capture_args(plan, index))
        for index in range(plan.num_screens + 1)
        if _dvd_capture_required(plan, index)
    ]


def _log_dvd_capture_exceptions(results: list[object]) -> None:
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"[red]DVD screenshot task failed: {result}[/red]")


def _dvd_tuple_results(
    results: list[object],
) -> list[tuple[int, str | None]]:
    return [
        cast(tuple[int, str | None], result)
        for result in results
        if isinstance(result, tuple)
    ]


def _warn_invalid_dvd_results(
    results: list[object], filtered: list[tuple[int, str | None]]
) -> None:
    if len(filtered) == len(results):
        return
    logger.warning(
        f"[yellow]Warning: {len(results) - len(filtered)} capture tasks returned invalid results."
    )


def _dvd_filtered_capture_results(results: list[object]) -> list[str]:
    filtered = _dvd_tuple_results(results)
    _warn_invalid_dvd_results(results, filtered)
    filtered.sort(key=lambda item: item[0])
    return [path for _, path in filtered if path is not None]


async def _run_dvd_capture_batch(plan: _DvdCapturePlan) -> list[str]:
    workers = min(plan.num_screens + 1, task_limit)
    logger.debug(f"Using {workers} worker(s) for {plan.num_screens} image(s)")
    semaphore = asyncio.Semaphore(task_limit)
    tasks = _dvd_capture_tasks(plan, semaphore)
    results = cast(
        list[object], await asyncio.gather(*tasks, return_exceptions=True)
    )
    _log_dvd_capture_exceptions(results)
    capture_results = _dvd_filtered_capture_results(results)
    if capture_results and len(capture_results) > plan.num_screens:
        discard_smallest_capture_result(capture_results)
    return capture_results


def _dvd_needs_retake(image_path: str) -> bool:
    image_size = Path(image_path).stat().st_size
    if image_size > 120000:
        return False
    logger.info(f"[yellow]Image {image_path} is incredibly small, retaking.")
    return True


def _dvd_delete_for_retake(image_path: str) -> bool:
    if not Path(image_path).exists():
        return True
    try:
        Path(image_path).unlink()
    except Exception as error:
        logger.error(f"[red]Failed to delete {image_path}: {error}[/red]")
        return False
    return True


async def _dvd_retake_attempt(
    plan: _DvdCapturePlan,
    image_path: str,
    index: int,
    adjusted_time: float,
) -> bool:
    try:
        response = await capture_dvd_screenshot(
            (
                index,
                plan.input_file,
                image_path,
                str(adjusted_time),
                plan.meta,
                plan.geometry.width,
                plan.geometry.height,
                plan.geometry.w_sar,
                plan.geometry.h_sar,
            )
        )
    except Exception as error:
        logger.error(
            f"[red]Error capturing screenshot for {plan.input_file} at {adjusted_time}: {error}[/red]"
        )
        return False
    _returned_index, screenshot_result = response
    if screenshot_result is None:
        logger.error(
            f"[red]Failed to capture screenshot for {image_path}. Retrying...[/red]"
        )
        return False
    retaken_size = Path(screenshot_result).stat().st_size
    if retaken_size > 75000:
        logger.info(
            f"[green]Successfully retaken screenshot for: {screenshot_result} ({retaken_size} bytes)[/green]"
        )
        return True
    logger.info(
        f"[red]Retaken image {screenshot_result} is still too small. Retrying...[/red]"
    )
    return False


async def _dvd_retake_image(plan: _DvdCapturePlan, image_path: str) -> bool:
    index = _image_capture_index(image_path)
    for attempt in range(1, 4):
        logger.info(
            f"[yellow]Retaking screenshot for: {image_path} (Attempt {attempt}/3)[/yellow]"
        )
        adjusted_time = random.uniform(0, plan.vob_length)  # nosec B311 - Random screenshot timing, not cryptographic  # noqa: S311
        if not _dvd_delete_for_retake(image_path):
            return False
        if await _dvd_retake_attempt(plan, image_path, index, adjusted_time):
            return True
    logger.info(
        f"[red]All retry attempts failed for {image_path}. Skipping.[/red]"
    )
    return False


async def _validated_dvd_image(
    plan: _DvdCapturePlan, image_path: str
) -> tuple[str | None, str | None]:
    if "Error" in image_path:
        logger.info(f"[red]{image_path}")
        return None, None
    if not _dvd_needs_retake(image_path):
        return image_path, None
    if await _dvd_retake_image(plan, image_path):
        return image_path, None
    return None, image_path


async def _validated_dvd_results(
    plan: _DvdCapturePlan, capture_results: list[str]
) -> list[str]:
    valid_results: list[str] = []
    failed_results: list[str] = []
    for image_path in capture_results:
        valid, failed = await _validated_dvd_image(plan, image_path)
        if valid is not None:
            valid_results.append(valid)
        if failed is not None:
            failed_results.append(failed)
    if failed_results:
        logger.info(
            f"[red]The following images could not be retaken successfully: {failed_results}[/red]"
        )
    return valid_results


async def _finish_dvd_capture(
    plan: _DvdCapturePlan, valid_results: list[str]
) -> None:
    if valid_results:
        register_screenshots(
            plan.meta.base_dir,
            plan.meta.uuid,
            valid_results,
            plan.sanitized_disc_name,
        )
    if not plan.retry_cap and plan.meta.debug:
        logger.info(
            f"[green]Successfully captured {len(valid_results)} screenshots."
        )
    if _should_cleanup_after_capture(plan.meta, plan.cleanup_after_capture):
        await cleanup_manager.cleanup()


async def dvd_screenshots(
    meta: Meta,
    disc_num: int,
    num_screens: int = 0,
    retry_cap: bool = False,
    cleanup_after_capture: bool = True,
) -> None:
    plan = await _prepare_dvd_capture_plan(
        meta, disc_num, num_screens, retry_cap, cleanup_after_capture
    )
    if plan is None:
        return
    await _collect_dvd_frame_info(plan)
    capture_results = await _run_dvd_capture_batch(plan)
    valid_results = await _validated_dvd_results(plan, capture_results)
    await _finish_dvd_capture(plan, valid_results)


def _dvd_video_duration(input_file: str) -> float | None:
    media_info = MediaInfo.parse(input_file)
    tracks = cast(list[Any], getattr(media_info, "tracks", []))
    for track in tracks:
        if track.track_type != "Video":
            continue
        try:
            return (
                float(track.duration) if track.duration is not None else None
            )
        except TypeError, ValueError:
            return None
    return None


def _dvd_seek_time(seek_time: float, video_duration: float | None) -> float:
    if video_duration is None:
        return seek_time
    if seek_time <= video_duration:
        return seek_time
    return max(0, video_duration - 1)


def _dvd_filter_chain(
    meta: Meta,
    seek_time: float,
    width: float,
    height: float,
    w_sar: float,
    h_sar: float,
) -> str:
    filters = _capture_scale_filters(width, height, w_sar, h_sar, False)
    filters.extend(
        _frame_overlay_filters(
            meta,
            seek_time,
            hdr_tonemap=False,
            base_resolution=576,
        )
    )
    return _finalize_capture_filters(filters)


def _dvd_capture_command(
    input_file: str,
    image: str,
    seek_time: float,
    filter_chain: str,
    loglevel: str,
) -> Any:
    return (
        cast(Any, ffmpeg)
        .input(input_file, ss=str(seek_time), accurate_seek=None)
        .output(
            image,
            vframes=1,
            vf=filter_chain,
            compression_level=ffmpeg_compression,
            pred="mixed",
        )
        .global_args("-y", "-loglevel", loglevel, "-hide_banner")
    )


def _dvd_capture_result(
    index: int,
    input_file: str,
    image: str,
    seek_time: float,
    returncode: int | None,
    stderr: bytes,
) -> tuple[int, str | None]:
    if returncode != 0:
        logger.error(
            f"[red]Error capturing screenshot for {input_file} at {seek_time}s:[/red]\n"
            f"{stderr.decode('utf-8', errors='replace')}"
        )
        return index, None
    if Path(image).exists():
        return index, image
    logger.info(f"[red]Screenshot creation failed for {image}[/red]")
    return index, None


async def capture_dvd_screenshot(
    task: tuple[int, str, str, str, Meta, float, float, float, float],
) -> tuple[int, str | None]:
    (
        index,
        input_file,
        image,
        seek_time_str,
        meta,
        width,
        height,
        w_sar,
        h_sar,
    ) = task
    seek_time = float(seek_time_str)
    try:
        seek_time = _dvd_seek_time(seek_time, _dvd_video_duration(input_file))
        loglevel = "verbose" if meta.ffdebug else "quiet"
        command = _dvd_capture_command(
            input_file,
            image,
            seek_time,
            _dvd_filter_chain(meta, seek_time, width, height, w_sar, h_sar),
            loglevel,
        )
        _log_ffmpeg_command(command, loglevel, meta)
        returncode, _stdout, stderr = await run_ffmpeg(command)
        return _dvd_capture_result(
            index, input_file, image, seek_time, returncode, stderr
        )
    except Exception as error:
        logger.error(
            f"[red]Error capturing screenshot for {input_file} at {seek_time}s: {error}[/red]"
        )
        return index, None


_AUDIOBOOK_COVER_EXTENSIONS = frozenset(
    {".mp3", ".m4b", ".flac", ".aac", ".m4a", ".ogg", ".wav"}
)


def _single_existing_file(source: str) -> list[str]:
    if not source:
        return []
    return [source] if Path(source).is_file() else []


def _audiobook_cover_files(meta: Meta) -> list[str]:
    files = list(map(str, meta.filelist))
    if files:
        return files
    return _single_existing_file(str(meta.path or ""))


def _write_cover_payload(dest_path: str, data: Any) -> bool:
    with Path(dest_path).open("wb") as output:
        output.write(bytes(data))
    return True


def _selected_audio_picture(audio: Any, confirmed_only: bool) -> Any | None:
    pictures = getattr(audio, "pictures", None)
    if not pictures:
        return None
    values = list(pictures)
    if confirmed_only:
        return next(
            (
                picture
                for picture in values
                if getattr(picture, "type", None) == 3
            ),
            None,
        )
    return values[0]


def _apic_keys(tags: Any) -> list[Any]:
    return [key for key in tags if str(key).startswith("APIC")]


def _front_apic(tags: Any, keys: list[Any]) -> Any | None:
    for key in keys:
        picture = tags[key]
        if getattr(picture, "type", None) == 3:
            return picture
    return None


def _selected_apic(audio: Any, confirmed_only: bool) -> Any | None:
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    keys = _apic_keys(tags)
    front = _front_apic(tags, keys)
    if front is not None:
        return front
    if confirmed_only or not keys:
        return None
    return tags[keys[0]]


def _mp4_covr_payload(audio: Any) -> Any | None:
    if "covr" not in audio:
        return None
    covers = audio["covr"]
    if not isinstance(covers, list) or not covers:
        return None
    return covers[0]


def _extract_mutagen_cover(
    audio: Any, dest_path: str, confirmed_only: bool
) -> bool:
    picture = _selected_audio_picture(audio, confirmed_only)
    if picture is not None:
        return _write_cover_payload(dest_path, picture.data)
    apic = _selected_apic(audio, confirmed_only)
    if apic is not None:
        return _write_cover_payload(dest_path, apic.data)
    covr = _mp4_covr_payload(audio)
    return _write_cover_payload(dest_path, covr) if covr is not None else False


def _extract_mp4_cover_without_chapters(
    audio_path: str, dest_path: str
) -> bool:
    from mutagen.mp4 import MP4Tags
    from mutagen.mp4._atom import Atoms  # pyright: ignore[reportPrivateUsage]

    with Path(audio_path).open("rb") as fileobj:
        tags = MP4Tags(Atoms(fileobj), fileobj)
    covers = tags.get("covr", [])
    return _write_cover_payload(dest_path, covers[0]) if covers else False


def _audio_cover_candidate(audio_path: str) -> bool:
    path = Path(audio_path)
    return path.suffix.lower() in _AUDIOBOOK_COVER_EXTENSIONS and path.exists()


def _try_mp4_cover_fallback(audio_path: str, dest_path: str) -> bool:
    if Path(audio_path).suffix.lower() not in {".m4a", ".m4b"}:
        return False
    try:
        return _extract_mp4_cover_without_chapters(audio_path, dest_path)
    except Exception as error:
        logger.debug(
            f"[yellow]MP4 cover fallback failed for {audio_path}: {error}[/yellow]"
        )
        return False


def _extract_audiobook_cover_file(
    audio_path: str,
    dest_path: str,
    confirmed_only: bool,
    mutagen_module: Any,
) -> bool:
    if not _audio_cover_candidate(audio_path):
        return False
    try:
        audio = mutagen_module.File(audio_path)
        if audio is None:
            return False
        return _extract_mutagen_cover(audio, dest_path, confirmed_only)
    except Exception as error:
        if _try_mp4_cover_fallback(audio_path, dest_path):
            return True
        logger.debug(
            f"[yellow]Error extracting from {audio_path}: {error}[/yellow]"
        )
        return False


def _extract_audiobook_cover_sync(
    meta: Meta, dest_path: str, confirmed_only: bool
) -> bool:
    import mutagen

    for audio_path in _audiobook_cover_files(meta):
        if _extract_audiobook_cover_file(
            audio_path, dest_path, confirmed_only, mutagen
        ):
            return True
    return False


async def extract_embedded_cover_from_audiobook(
    meta: Meta, dest_path: str, confirmed_only: bool = False
) -> bool:
    try:
        return await asyncio.to_thread(
            _extract_audiobook_cover_sync, meta, dest_path, confirmed_only
        )
    except Exception as error:
        logger.info(
            f"[yellow]Error extracting embedded cover: {error}[/yellow]"
        )
        return False


def _artwork_download_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }


def _is_mam_artwork_url(url: str) -> bool:
    hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    return hostname == "myanonamouse.net" or hostname.endswith(
        ".myanonamouse.net"
    )


def _mam_artwork_api_key() -> str:
    candidates = (
        str(default_config.get("mam_api_key", "")).strip(),
        str(default_config.get("mam_id", "")).strip(),
        os.environ.get("MAM_API_KEY", "").strip(),
        os.environ.get("MAM_ID", "").strip(),
    )
    return next((value for value in candidates if value), "")


def _mam_artwork_cookies(url: str) -> dict[str, str]:
    if not _is_mam_artwork_url(url):
        return {}
    api_key = _mam_artwork_api_key()
    return {"mam_id": api_key} if api_key else {}


def _mam_artwork_candidates(url: str) -> list[str]:
    if not _is_mam_artwork_url(url):
        return [url]
    parsed = urllib.parse.urlparse(url)
    candidates = [url]
    for extension in ("jpg", "jpeg", "png", "gif"):
        candidate_path = re.sub(
            r"\.(?:jpe?g|png|gif)$",
            f".{extension}",
            parsed.path,
            flags=re.IGNORECASE,
        )
        candidate = urllib.parse.urlunparse(
            parsed._replace(path=candidate_path)
        )
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


async def _save_downloaded_artwork(
    meta: Meta, artwork_path: str, content: bytes, current_url: str
) -> bool:
    if not is_valid_image_bytes(content):
        logger.info(
            "[yellow]Warning: Downloaded artwork is not a valid supported image and will be ignored.[/yellow]"
        )
        return False
    await asyncio.to_thread(Path(artwork_path).write_bytes, content)
    if not is_valid_cover_image(artwork_path):
        return False
    meta.artwork_path = artwork_path
    logger.info(
        f"[green]Successfully downloaded artwork from {current_url}[/green]"
    )
    return True


def _artwork_redirect_target(current_url: str, response: Any) -> str:
    location = response.headers.get("Location")
    return urllib.parse.urljoin(current_url, location) if location else ""


async def _nonredirect_artwork_response(
    meta: Meta,
    artwork_path: str,
    current_url: str,
    response: Any,
    has_more_candidates: bool,
) -> tuple[str, str]:
    if response.status_code == 200:
        saved = await _save_downloaded_artwork(
            meta, artwork_path, response.content, current_url
        )
        return ("success" if saved else "next"), current_url
    if response.status_code == 404 and has_more_candidates:
        return "next", current_url
    logger.warning(
        f"[yellow]Warning: Failed to download poster, status code {response.status_code}[/yellow]"
    )
    return "fatal", current_url


async def _artwork_response_outcome(
    meta: Meta,
    artwork_path: str,
    current_url: str,
    response: Any,
    has_more_candidates: bool,
) -> tuple[str, str]:
    if response.is_redirect:
        target = _artwork_redirect_target(current_url, response)
        return ("redirect", target) if target else ("next", current_url)
    return await _nonredirect_artwork_response(
        meta, artwork_path, current_url, response, has_more_candidates
    )


async def _download_artwork_candidate(
    client: Any,
    meta: Meta,
    candidate_url: str,
    artwork_path: str,
    cookies: dict[str, str],
    headers: dict[str, str],
    *,
    has_more_candidates: bool,
) -> str:
    current_url = candidate_url
    for _attempt in range(4):
        if not is_public_http_url(current_url):
            logger.warning(
                "[yellow]Warning: Artwork download target is not a public HTTP(S) URL.[/yellow]"
            )
            return "fatal"
        response = await client.get(
            current_url, cookies=cookies, headers=headers
        )
        outcome, target = await _artwork_response_outcome(
            meta, artwork_path, current_url, response, has_more_candidates
        )
        if outcome == "redirect":
            current_url = target
            continue
        return outcome
    logger.warning(
        "[yellow]Warning: Artwork download exceeded the redirect limit.[/yellow]"
    )
    return "next"


async def _download_artwork_candidates(
    meta: Meta, artwork_path: str, artwork_url: str
) -> bool:
    import httpx

    candidates = _mam_artwork_candidates(artwork_url)
    cookies = _mam_artwork_cookies(artwork_url)
    headers = _artwork_download_headers()
    async with httpx.AsyncClient(
        timeout=30.0, follow_redirects=False
    ) as client:
        for index, candidate_url in enumerate(candidates):
            outcome = await _download_artwork_candidate(
                client,
                meta,
                candidate_url,
                artwork_path,
                cookies,
                headers,
                has_more_candidates=index < len(candidates) - 1,
            )
            if outcome == "success":
                return True
            if outcome == "fatal":
                return False
    logger.warning(
        "[yellow]Warning: No artwork candidate could be downloaded.[/yellow]"
    )
    return False


def _artwork_download_preflight(
    meta: Meta, artwork_path: str, force: bool
) -> bool | None:
    if not meta.artwork_url:
        return False
    if not force and is_valid_cover_image(artwork_path):
        meta.artwork_path = artwork_path
        return True
    return None


async def download_artwork_from_meta(
    meta: Meta, artwork_path: str, *, force: bool = False
) -> bool:
    preflight = _artwork_download_preflight(meta, artwork_path, force)
    if preflight is not None:
        return preflight
    artwork_url = str(meta.artwork_url)
    try:
        return await _download_artwork_candidates(
            meta, artwork_path, artwork_url
        )
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Error downloading poster: {error}[/yellow]"
        )
        return False


def _epub_joined_path(base_dir: str, relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    if not base_dir:
        return normalized
    return str(Path(base_dir) / normalized)


def _epub_pop_parent(parts: list[str]) -> None:
    if parts:
        parts.pop()


def _epub_resolve_path(base_dir: str, relative_path: str) -> str:
    parts: list[str] = []
    for part in _epub_joined_path(base_dir, relative_path).split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            _epub_pop_parent(parts)
            continue
        parts.append(part)
    return "/".join(parts)


def _epub_container_rootfile(archive: Any) -> str | None:
    try:
        root = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except Exception:
        return None
    for element in root.iter():
        if element.tag.endswith("rootfile"):
            return element.attrib.get("full-path") or None
    return None


def _epub_rootfile(archive: Any) -> str | None:
    rootfile = _epub_container_rootfile(archive)
    if rootfile:
        return rootfile
    return next(
        (name for name in archive.namelist() if name.endswith(".opf")), None
    )


def _epub_manifest_item(
    element: Any,
) -> tuple[str, dict[str, str], str | None] | None:
    item_id = element.attrib.get("id")
    href = element.attrib.get("href")
    if not item_id or not href:
        return None
    properties = element.attrib.get("properties", "")
    item = {
        "href": href,
        "media-type": element.attrib.get("media-type", "").lower(),
        "properties": properties,
    }
    direct_cover = href if "cover-image" in properties else None
    return item_id, item, direct_cover


def _epub_manifest_items(
    root: Any,
) -> tuple[dict[str, dict[str, str]], str | None]:
    items: dict[str, dict[str, str]] = {}
    direct_cover: str | None = None
    for element in root.iter():
        if element.tag.split("}")[-1] != "item":
            continue
        parsed = _epub_manifest_item(element)
        if parsed is None:
            continue
        item_id, item, candidate = parsed
        items[item_id] = item
        if candidate is not None:
            direct_cover = candidate
    return items, direct_cover


def _epub_meta_cover_id(root: Any) -> str | None:
    for element in root.iter():
        if element.tag.split("}")[-1] != "meta":
            continue
        if element.attrib.get("name") == "cover":
            return element.attrib.get("content") or None
    return None


def _epub_is_image_item(item: Mapping[str, str]) -> bool:
    if item.get("media-type", "").startswith("image/"):
        return True
    return (
        item.get("href", "")
        .lower()
        .endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"))
    )


def _epub_is_html_item(item: Mapping[str, str]) -> bool:
    if item.get("media-type") in {"application/xhtml+xml", "text/html"}:
        return True
    return item.get("href", "").lower().endswith((".xhtml", ".html", ".htm"))


def _epub_case_insensitive_name(names: list[str], target: str) -> str | None:
    if target in names:
        return target
    lowered = target.lower()
    return next((name for name in names if name.lower() == lowered), None)


def _epub_markup_image_source(content: str) -> str | None:
    image_match = re.search(
        r'<img[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE
    )
    if image_match:
        return image_match.group(1)
    svg_match = re.search(
        r'<image[^>]+(?:xlink:)?href=["\']([^"\']+)["\']',
        content,
        re.IGNORECASE,
    )
    return svg_match.group(1) if svg_match else None


def _epub_image_from_html(
    archive: Any, opf_dir: str, html_href: str
) -> str | None:
    html_path = _epub_resolve_path(opf_dir, html_href)
    matched = _epub_case_insensitive_name(archive.namelist(), html_path)
    if matched is None:
        return None
    try:
        content = archive.read(matched).decode("utf-8", errors="ignore")
    except Exception:
        return None
    image_source = _epub_markup_image_source(content)
    if image_source is None:
        return None
    html_dir = str(Path(html_path).parent)
    return _epub_resolve_path(html_dir, urllib.parse.unquote(image_source))


def _epub_cover_from_item(
    archive: Any, opf_dir: str, item: Mapping[str, str]
) -> str | None:
    if _epub_is_image_item(item):
        return _epub_resolve_path(opf_dir, item["href"])
    if _epub_is_html_item(item):
        return _epub_image_from_html(archive, opf_dir, item["href"])
    return None


def _epub_standard_id_cover(
    archive: Any, opf_dir: str, items: Mapping[str, Mapping[str, str]]
) -> str | None:
    for item_id in ("cover", "cover-image", "coverimage"):
        item = items.get(item_id)
        if item is None:
            continue
        cover = _epub_cover_from_item(archive, opf_dir, item)
        if cover:
            return cover
    return None


def _epub_standard_cover(
    archive: Any,
    opf_dir: str,
    items: Mapping[str, Mapping[str, str]],
    direct_cover: str | None,
    cover_item_id: str | None,
) -> str | None:
    if direct_cover:
        return _epub_resolve_path(opf_dir, direct_cover)
    if cover_item_id:
        item = items.get(cover_item_id)
        if item is not None:
            cover = _epub_cover_from_item(archive, opf_dir, item)
            if cover:
                return cover
    return _epub_standard_id_cover(archive, opf_dir, items)


def _epub_item_named_cover(item_id: str, item: Mapping[str, str]) -> bool:
    return (
        "cover" in item_id.lower() or "cover" in item.get("href", "").lower()
    )


def _epub_named_manifest_cover(
    opf_dir: str, items: Mapping[str, Mapping[str, str]]
) -> str | None:
    for item_id, item in items.items():
        if not _epub_is_image_item(item):
            continue
        if _epub_item_named_cover(item_id, item):
            return _epub_resolve_path(opf_dir, item["href"])
    return None


def _epub_named_archive_cover(names: list[str]) -> str | None:
    for name in names:
        basename = Path(name).name.lower()
        if "cover" not in basename:
            continue
        if basename.endswith((".jpg", ".jpeg", ".png", ".webp", ".svg")):
            return name
    return None


def _epub_first_manifest_image(
    opf_dir: str, items: Mapping[str, Mapping[str, str]]
) -> str | None:
    for item in items.values():
        if _epub_is_image_item(item):
            return _epub_resolve_path(opf_dir, item["href"])
    return None


def _epub_select_cover(
    archive: Any,
    opf_dir: str,
    items: Mapping[str, Mapping[str, str]],
    direct_cover: str | None,
    cover_item_id: str | None,
    confirmed_only: bool,
) -> str | None:
    cover = _epub_standard_cover(
        archive, opf_dir, items, direct_cover, cover_item_id
    )
    if cover:
        return cover
    if confirmed_only:
        return None
    return (
        _epub_named_manifest_cover(opf_dir, items)
        or _epub_named_archive_cover(archive.namelist())
        or _epub_first_manifest_image(opf_dir, items)
    )


def _epub_parsed_opf(archive: Any, rootfile_path: str) -> Any | None:
    try:
        return ElementTree.fromstring(archive.read(rootfile_path))
    except Exception:
        return None


def _epub_read_entry(archive: Any, name: str) -> bytes | None:
    try:
        return cast(bytes, archive.read(name))
    except Exception:
        return None


def _epub_cover_bytes_from_archive(
    archive: Any, confirmed_only: bool
) -> bytes | None:
    rootfile_path = _epub_rootfile(archive)
    if rootfile_path is None:
        return None
    root = _epub_parsed_opf(archive, rootfile_path)
    if root is None:
        return None
    items, direct_cover = _epub_manifest_items(root)
    cover_path = _epub_select_cover(
        archive,
        str(Path(rootfile_path).parent),
        items,
        direct_cover,
        _epub_meta_cover_id(root),
        confirmed_only,
    )
    if cover_path is None:
        return None
    matched = _epub_case_insensitive_name(archive.namelist(), cover_path)
    if matched is None:
        return None
    return _epub_read_entry(archive, matched)


def _epub_is_archive(epub_path: str) -> bool:
    return Path(epub_path).is_file() and zipfile.is_zipfile(epub_path)


def _write_epub_cover(dest_path: str, payload: bytes) -> bool:
    try:
        Path(dest_path).write_bytes(payload)
    except OSError:
        return False
    return True


def _extract_epub_cover_sync(
    epub_path: str, dest_path: str, confirmed_only: bool
) -> bool:
    if not _epub_is_archive(epub_path):
        return False
    try:
        with zipfile.ZipFile(epub_path, "r") as archive:
            payload = _epub_cover_bytes_from_archive(archive, confirmed_only)
    except Exception:
        return False
    if payload is None:
        return False
    return _write_epub_cover(dest_path, payload)


async def extract_epub_cover(
    epub_path: str, dest_path: str, confirmed_only: bool = False
) -> bool:
    return await asyncio.to_thread(
        _extract_epub_cover_sync, epub_path, dest_path, confirmed_only
    )


def _render_pdf_cover_sync(path: str, output_path: Path) -> bool:
    import fitz

    with fitz.open(path) as doc:
        if len(doc) == 0:
            return False
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        pix.save(output_path)
        return True


async def _extract_pdf_cover(path: str, output_path: Path) -> bool:
    try:
        return await asyncio.to_thread(
            _render_pdf_cover_sync, path, output_path
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: PDF cover extraction failed: {error}[/yellow]"
        )
        return False


def _comic_rar_class() -> Any:
    unrar_path = str(configured_binary("unrar_path") or "").strip()
    if unrar_path:
        import rarfile as _rarfile

        os.environ["UNRAR_TOOL"] = unrar_path
        _rarfile.CURRENT_SETUP = None
    from rarfile import RarFile

    return RarFile


def _natural_cover_sort_key(value: str) -> list[int | str]:
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", value)
    ]


def _open_comic_archive(path: str, extension: str, rar_class: Any) -> Any:
    archive_types = (
        (zipfile.ZipFile, rar_class)
        if extension == "cbz"
        else (rar_class, zipfile.ZipFile)
    )
    first, second = archive_types
    try:
        return first(path, "r")
    except Exception:
        return second(path, "r")


def _comic_cover_names(archive: Any) -> list[str]:
    names = [
        name
        for name in archive.namelist()
        if name.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        )
    ]
    return sorted(names, key=_natural_cover_sort_key)


def _materialize_comic_image_member(
    archive: Any,
    member_name: str,
    temp_extract: Path,
    *,
    max_bytes: int = COMIC_IMAGE_MAX_BYTES,
) -> Path:
    suffix = Path(member_name).suffix.lower()
    destination = temp_extract / f"comic-member{suffix}"
    total = 0
    try:
        with (
            archive.open(member_name) as source,
            destination.open("wb") as target,
        ):
            while chunk := source.read(COMIC_IMAGE_COPY_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Comic image member exceeds {max_bytes} bytes"
                    )
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _save_comic_cover_image(extracted_path: Path, output_path: Path) -> None:
    import shutil

    from PIL import Image

    if extracted_path.suffix.lower() == ".png":
        shutil.copy2(extracted_path, output_path)
        return
    with Image.open(extracted_path) as image:
        image.save(output_path, "PNG")


def _extract_comic_cover_sync(
    path: str,
    extension: str,
    output_path: Path,
    temp_extract: Path,
    rar_class: Any,
) -> bool:
    import shutil

    temp_extract.mkdir(parents=True, exist_ok=True)
    archive = None
    try:
        archive = _open_comic_archive(path, extension, rar_class)
        image_files = _comic_cover_names(archive)
        if not image_files:
            return False
        cover_name = image_files[0]
        extracted_path = _materialize_comic_image_member(
            archive, cover_name, temp_extract
        )
        _save_comic_cover_image(extracted_path, output_path)
        return True
    finally:
        if archive is not None:
            archive.close()
        shutil.rmtree(temp_extract, ignore_errors=True)


async def _extract_comic_cover(
    path: str, extension: str, output_path: Path
) -> bool:
    temp_extract = output_path.parent / "temp_cover_extract"
    rar_class = _comic_rar_class()
    try:
        return await asyncio.to_thread(
            _extract_comic_cover_sync,
            path,
            extension,
            output_path,
            temp_extract,
            rar_class,
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: Comic cover extraction failed: {error}[/yellow]"
        )
        return False


async def extract_document_cover(path: str, dest_path: str) -> bool:
    extension = Path(path).suffix.lower().lstrip(".")
    if extension not in {"pdf", "cbr", "cbz"}:
        return False
    output_path = Path(dest_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if extension == "pdf":
        return await _extract_pdf_cover(path, output_path)
    return await _extract_comic_cover(path, extension, output_path)


def _cached_book_cover(meta: Meta, artwork_path: Path) -> str:
    if meta.retake:
        return ""
    if meta.artwork_path and is_valid_cover_image(meta.artwork_path):
        return str(meta.artwork_path)
    if is_valid_cover_image(artwork_path):
        meta.artwork_path = str(artwork_path)
        return str(artwork_path)
    return ""


def _set_prepared_book_cover(meta: Meta, artwork_path: Path) -> str:
    meta.artwork_path = str(artwork_path)
    return str(artwork_path)


async def _prepare_audiobook_cover(
    meta: Meta, artwork_path: Path
) -> str | None:
    if await extract_embedded_cover_from_audiobook(
        meta, str(artwork_path), confirmed_only=True
    ):
        logger.debug(
            "[green]Audiobook confirmed cover extracted. Skipping API download.[/green]"
        )
        return _set_prepared_book_cover(meta, artwork_path)
    if await download_artwork_from_meta(
        meta, str(artwork_path), force=bool(meta.retake)
    ):
        return _set_prepared_book_cover(meta, artwork_path)
    if await extract_embedded_cover_from_audiobook(
        meta, str(artwork_path), confirmed_only=False
    ):
        return _set_prepared_book_cover(meta, artwork_path)
    return None


async def _prepare_epub_cover(
    path: str, meta: Meta, artwork_path: Path
) -> str | None:
    if await extract_epub_cover(path, str(artwork_path), confirmed_only=True):
        logger.debug(
            "[green]EPUB confirmed cover extracted. Skipping API download.[/green]"
        )
        return _set_prepared_book_cover(meta, artwork_path)
    if await download_artwork_from_meta(
        meta, str(artwork_path), force=bool(meta.retake)
    ):
        return _set_prepared_book_cover(meta, artwork_path)
    if await extract_epub_cover(path, str(artwork_path), confirmed_only=False):
        return _set_prepared_book_cover(meta, artwork_path)
    return None


async def _prepare_document_cover(
    path: str, meta: Meta, artwork_path: Path
) -> str | None:
    if await download_artwork_from_meta(
        meta, str(artwork_path), force=bool(meta.retake)
    ):
        return _set_prepared_book_cover(meta, artwork_path)
    if await extract_document_cover(path, str(artwork_path)):
        return _set_prepared_book_cover(meta, artwork_path)
    return None


async def prepare_book_cover(
    path: str, folder_id: str, base_dir: str, meta: Meta
) -> str | None:
    artwork_path = artwork_dir(base_dir, folder_id) / "POSTER.png"
    cached = _cached_book_cover(meta, artwork_path)
    if cached:
        return cached
    if meta.audiobook:
        return await _prepare_audiobook_cover(meta, artwork_path)
    extension = Path(path).suffix.lower().lstrip(".")
    if extension == "epub":
        return await _prepare_epub_cover(path, meta, artwork_path)
    if extension in {"pdf", "cbr", "cbz"}:
        return await _prepare_document_cover(path, meta, artwork_path)
    return None


def _book_banner_cached(banner_path: Path, meta: Meta) -> bool:
    if meta.retake:
        return False
    if not banner_path.exists():
        return False
    return banner_path.stat().st_size > 0


def _natural_sort_key(value: str) -> list[int | str]:
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", value)
    ]


def _comic_image_files(archive: Any) -> list[str]:
    image_files = [
        name
        for name in archive.namelist()
        if name.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
        )
    ]
    image_files.sort(key=_natural_sort_key)
    return image_files


def _open_comic_pages_archive(
    path: str, extension: str, rar_file: Any
) -> Any | None:
    preferred = (
        (zipfile.ZipFile, rar_file)
        if extension == "cbz"
        else (rar_file, zipfile.ZipFile)
    )
    for archive_type in preferred:
        try:
            return archive_type(path, "r")
        except Exception as error:
            logger.debug(
                f"[yellow]Unable to open {path} with {archive_type}: {error}[/yellow]"
            )
    return None


def _selected_book_pages(total: int, requested: int) -> list[int]:
    count = min(requested, total)
    return sorted(random.sample(range(total), count))


def _book_destination(
    output_dir: Path, poster_dir: Path, out_name: str
) -> Path:
    destination_dir = (
        poster_dir if out_name in {"POSTER", "POSTER_BANNER"} else output_dir
    )
    return destination_dir / f"{out_name}.png"


def _convert_comic_image(
    image_type: Any, source: Path, destination: Path
) -> None:
    with image_type.open(source) as image:
        image.save(destination, "PNG")


async def _process_comic_image(
    archive: Any,
    image_type: Any,
    image_files: list[str],
    image_index: int,
    out_name: str,
    temp_extract: Path,
    output_dir: Path,
    poster_dir: Path,
) -> str:
    import shutil

    image_name = image_files[image_index]
    source = await asyncio.to_thread(
        _materialize_comic_image_member,
        archive,
        image_name,
        temp_extract,
    )
    destination = _book_destination(output_dir, poster_dir, out_name)
    if image_name.lower().endswith(".png"):
        await asyncio.to_thread(shutil.copy2, source, destination)
    else:
        await asyncio.to_thread(
            _convert_comic_image, image_type, source, destination
        )
    return str(destination)


def _set_book_artwork_paths(
    meta: Meta, cover_path: Path, banner_path: Path
) -> None:
    meta.artwork_path = str(cover_path)
    meta.artwork_banner_path = str(banner_path)


async def _ensure_comic_artwork(
    archive: Any,
    image_type: Any,
    image_files: list[str],
    temp_extract: Path,
    output_dir: Path,
    poster_dir: Path,
    cover_path: Path,
    banner_path: Path,
    meta: Meta,
    prepared_cover: bool,
    banner_cached: bool,
) -> None:
    if not prepared_cover:
        await _process_comic_image(
            archive,
            image_type,
            image_files,
            0,
            "POSTER",
            temp_extract,
            output_dir,
            poster_dir,
        )
    if not banner_cached:
        await _process_comic_image(
            archive,
            image_type,
            image_files,
            len(image_files) - 1,
            "POSTER_BANNER",
            temp_extract,
            output_dir,
            poster_dir,
        )
    _set_book_artwork_paths(meta, cover_path, banner_path)


async def _generate_comic_screenshots(
    path: str,
    extension: str,
    sanitized_filename: str,
    output_dir: Path,
    poster_dir: Path,
    cover_path: Path,
    banner_path: Path,
    meta: Meta,
    num_screens: int,
    rar_file: Any,
    image_type: Any,
    prepared_cover: bool,
    banner_cached: bool,
) -> list[str]:
    import shutil

    temp_extract = output_dir / "temp_compressed_extract"
    temp_extract.mkdir(parents=True, exist_ok=True)
    archive = _open_comic_pages_archive(path, extension, rar_file)
    if archive is None:
        logger.info(f"[red]Invalid CBR/CBZ file: {path}[/red]")
        await asyncio.to_thread(
            shutil.rmtree, temp_extract, ignore_errors=True
        )
        return []
    try:
        image_files = _comic_image_files(archive)
        if not image_files:
            logger.info("[yellow]CBR/CBZ does not contain images[/yellow]")
            return []
        screenshots = [
            await _process_comic_image(
                archive,
                image_type,
                image_files,
                image_index,
                f"{sanitized_filename}-{position}",
                temp_extract,
                output_dir,
                poster_dir,
            )
            for position, image_index in enumerate(
                _selected_book_pages(len(image_files), num_screens)
            )
        ]
        await _ensure_comic_artwork(
            archive,
            image_type,
            image_files,
            temp_extract,
            output_dir,
            poster_dir,
            cover_path,
            banner_path,
            meta,
            prepared_cover,
            banner_cached,
        )
        return screenshots
    finally:
        archive.close()
        await asyncio.to_thread(
            shutil.rmtree, temp_extract, ignore_errors=True
        )


def _render_document_page_sync(
    document: Any,
    fitz_module: Any,
    page_number: int,
    destination: Path,
) -> str:
    page = document[page_number]
    pixmap = page.get_pixmap(matrix=fitz_module.Matrix(2.0, 2.0))
    pixmap.save(destination)
    return str(destination)


async def _render_document_page(
    document: Any,
    fitz_module: Any,
    page_number: int,
    out_name: str,
    output_dir: Path,
    poster_dir: Path,
) -> str:
    destination = _book_destination(output_dir, poster_dir, out_name)
    return await asyncio.to_thread(
        _render_document_page_sync,
        document,
        fitz_module,
        page_number,
        destination,
    )


async def _ensure_document_artwork(
    document: Any,
    fitz_module: Any,
    total_pages: int,
    output_dir: Path,
    poster_dir: Path,
    cover_path: Path,
    banner_path: Path,
    meta: Meta,
    prepared_cover: bool,
    banner_cached: bool,
) -> None:
    if not prepared_cover:
        await _render_document_page(
            document, fitz_module, 0, "POSTER", output_dir, poster_dir
        )
    if not banner_cached:
        await _render_document_page(
            document,
            fitz_module,
            total_pages - 1,
            "POSTER_BANNER",
            output_dir,
            poster_dir,
        )
    _set_book_artwork_paths(meta, cover_path, banner_path)


async def _fallback_epub_cover(
    path: str, cover_path: Path, meta: Meta, prepared_cover: bool
) -> bool:
    if prepared_cover:
        return True
    try:
        extracted = await extract_epub_cover(
            path, str(cover_path), confirmed_only=False
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Warning: EPUB cover extraction failed: {error}[/yellow]"
        )
        return False
    if extracted:
        meta.artwork_path = str(cover_path)
    return bool(extracted)


async def _generate_document_screenshots(
    path: str,
    extension: str,
    sanitized_filename: str,
    output_dir: Path,
    poster_dir: Path,
    cover_path: Path,
    banner_path: Path,
    meta: Meta,
    num_screens: int,
    fitz_module: Any,
    prepared_cover: bool,
    banner_cached: bool,
) -> list[str]:
    if extension == "epub":
        prepared_cover = await _fallback_epub_cover(
            path, cover_path, meta, prepared_cover
        )
    document = fitz_module.open(path)
    try:
        total_pages = len(document)
        if total_pages == 0:
            logger.info(
                f"[yellow]{extension.upper()} does not have pages[/yellow]"
            )
            return []
        screenshots = [
            await _render_document_page(
                document,
                fitz_module,
                page_number,
                f"{sanitized_filename}-{position}",
                output_dir,
                poster_dir,
            )
            for position, page_number in enumerate(
                _selected_book_pages(total_pages, num_screens)
            )
        ]
        await _ensure_document_artwork(
            document,
            fitz_module,
            total_pages,
            output_dir,
            poster_dir,
            cover_path,
            banner_path,
            meta,
            prepared_cover,
            banner_cached,
        )
        return screenshots
    finally:
        document.close()


def _configure_rar_file() -> Any:
    unrar_path = str(configured_binary("unrar_path") or "").strip()
    if unrar_path:
        import rarfile as rarfile_module

        os.environ["UNRAR_TOOL"] = unrar_path
        rarfile_module.CURRENT_SETUP = None
    from rarfile import RarFile

    return RarFile


async def generate_ebook_screenshots(
    path: str,
    filename: str,
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int = 5,
) -> list[str]:
    import fitz  # PyMuPDF
    from PIL import Image

    with contextlib.suppress(Exception):
        fitz.TOOLS.mupdf_display_errors(False)
    output_dir = screenshots_dir(base_dir, folder_id).resolve()
    poster_dir = artwork_dir(base_dir, folder_id)
    cover_path = poster_dir / "POSTER.png"
    banner_path = poster_dir / "POSTER_BANNER.png"
    sanitized_filename = await sanitize_filename(filename)
    extension = Path(path).suffix.lower().lstrip(".")
    prepared_cover = bool(
        await prepare_book_cover(path, folder_id, base_dir, meta)
    )
    banner_cached = _book_banner_cached(banner_path, meta)
    try:
        if extension in {"cbr", "cbz"}:
            return await _generate_comic_screenshots(
                path,
                extension,
                sanitized_filename,
                output_dir,
                poster_dir,
                cover_path,
                banner_path,
                meta,
                num_screens,
                _configure_rar_file(),
                Image,
                prepared_cover,
                banner_cached,
            )
        if extension in {"pdf", "mobi", "epub"}:
            return await _generate_document_screenshots(
                path,
                extension,
                sanitized_filename,
                output_dir,
                poster_dir,
                cover_path,
                banner_path,
                meta,
                num_screens,
                fitz,
                prepared_cover,
                banner_cached,
            )
    except Exception as error:
        logger.error(
            f"[red]Error while generating {extension.upper()} screenshots: {error}[/red]"
        )
        logger.info(traceback.format_exc())
    return []


def _media_float_mapping_value(value: Mapping[str, Any]) -> Any | None:
    for key in ("#value", "value", "duration", "Duration"):
        if key in value:
            return value[key]
    return None


def _media_float_string(value: str, default: float, field_name: str) -> float:
    try:
        return float(value)
    except ValueError:
        logger.warning(
            f"[yellow]Warning: Could not convert string '{value}' to float for {field_name}, using default {default}[/yellow]"
        )
        return default


def _media_float_mapping(
    value: Mapping[str, Any], default: float, field_name: str
) -> float:
    nested = _media_float_mapping_value(value)
    if nested is not None:
        return _media_float(nested, default, field_name)
    logger.warning(
        f"[yellow]Warning: {field_name} is a dict but no usable value found: {value}, using default {default}[/yellow]"
    )
    return default


def _media_float(
    value: Any, default: float = 0.0, field_name: str = ""
) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return _media_float_string(value, default, field_name)
    if isinstance(value, Mapping):
        return _media_float_mapping(value, default, field_name)
    logger.warning(
        f"[yellow]Warning: Unable to convert to float: {type(value)} {value} for {field_name}, using default {default}[/yellow]"
    )
    return default


def _media_capture_values(
    mi: Mapping[str, Any],
) -> tuple[float, float, float, float, float, float]:
    tracks = cast(list[Mapping[str, Any]], mi["media"]["track"])
    video_track = tracks[1]
    general_track = tracks[0]
    length = _media_float(
        video_track.get("Duration"),
        _media_float(
            general_track.get("Duration"), 3600.0, "General Duration"
        ),
        "Video Duration",
    )
    width = _media_float(video_track.get("Width"), 1920.0, "Width")
    height = _media_float(video_track.get("Height"), 1080.0, "Height")
    par = _media_float(
        video_track.get("PixelAspectRatio"), 1.0, "PixelAspectRatio"
    )
    dar = _media_float(
        video_track.get("DisplayAspectRatio"), 16.0 / 9.0, "DisplayAspectRatio"
    )
    frame_rate = _media_float(video_track.get("FrameRate"), 24.0, "FrameRate")
    return length, width, height, par, dar, frame_rate


@dataclass(frozen=True)
class _VideoCaptureInitial:
    img_host: str | None
    screens: int
    start_time: float
    group: str
    requested_screens: int
    registered_screens: list[str]
    existing_remote_count: int


@dataclass(frozen=True)
class _CaptureMediaDetails:
    length: float
    width: float
    height: float
    w_sar: float
    h_sar: float
    frame_rate: float


@dataclass(frozen=True)
class _VideoCapturePlan:
    path: str
    folder_id: str
    base_dir: str
    meta: Meta
    img_host: str | None
    start_time: float
    group: str
    requested_screens: int
    registered_screens: list[str]
    manual_frames: str | list[int] | list[str]
    force_screenshots: bool
    cleanup_after_capture: bool
    length: float
    width: float
    height: float
    w_sar: float
    h_sar: float
    frame_rate: float
    loglevel: str
    screenshot_dir: Path
    sanitized_filename: str
    ss_times: list[str]
    num_capture: int
    existing_images_count: int


async def _book_screenshot_result(
    path: str,
    filename: str,
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int,
) -> list[str]:
    if meta.audiobook:
        await prepare_book_cover(path, folder_id, base_dir, meta)
        return []
    requested = num_screens if num_screens > 0 else meta.screens
    return await generate_ebook_screenshots(
        path, filename, folder_id, base_dir, meta, requested
    )


def _remote_image_count(meta: Meta) -> int:
    return sum(
        1
        for image in meta.image_list
        if str(image.get("img_url", "")).startswith("http")
    )


def _skip_for_remote_images(
    existing_count: int, force_screenshots: bool
) -> bool:
    if force_screenshots:
        return False
    if existing_count < cutoff:
        return False
    logger.info(
        f"[yellow]There are already at least {cutoff} images in the image list. Skipping additional screenshots."
    )
    return True


def _manual_frame_count(manual_frames: str | list[int] | list[str]) -> int:
    if isinstance(manual_frames, str):
        return len(
            [frame for frame in manual_frames.split(",") if frame.strip()]
        )
    return len(manual_frames)


def _requested_screenshot_count(
    num_screens: int,
    manual_frames: str | list[int] | list[str],
    screens: int,
) -> int:
    if num_screens > 0:
        return num_screens
    manual_count = _manual_frame_count(manual_frames)
    return manual_count if manual_count > 0 else screens


def _registered_capture_screens(
    base_dir: str, folder_id: str, group: str, meta: Meta
) -> list[str]:
    if meta.retake:
        clear_screenshot_group(base_dir, folder_id, group)
    return [
        str(screen) for screen in manifest_files(base_dir, folder_id, group)
    ]


def _reusable_registered_screens(
    registered_screens: list[str],
    requested_screens: int,
    force_screenshots: bool,
    retake: bool,
    group: str,
) -> list[str] | None:
    if force_screenshots:
        return None
    if retake:
        return None
    if len(registered_screens) < requested_screens:
        return None
    logger.debug(
        f"[yellow]Reusing {len(registered_screens)} registered screenshots from group '{group}'.[/yellow]"
    )
    return registered_screens[:requested_screens]


async def _initial_video_capture(
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int,
    manual_frames: str | list[int] | list[str],
    force_screenshots: bool,
    capture_group: str | None,
) -> tuple[_VideoCaptureInitial | None, list[str] | None]:
    img_host = await get_image_host(meta)
    logger.debug(f"Image Host: {img_host}")
    existing_remote_count = _remote_image_count(meta)
    if _skip_for_remote_images(existing_remote_count, force_screenshots):
        return None, None
    screens = meta.screens
    group = capture_group or "main"
    requested_screens = _requested_screenshot_count(
        num_screens, manual_frames, screens
    )
    registered_screens = _registered_capture_screens(
        base_dir, folder_id, group, meta
    )
    reusable = _reusable_registered_screens(
        registered_screens,
        requested_screens,
        force_screenshots,
        bool(meta.retake),
        group,
    )
    if reusable is not None:
        return None, reusable
    return (
        _VideoCaptureInitial(
            img_host=img_host,
            screens=screens,
            start_time=time.time() if meta.debug else 0.0,
            group=group,
            requested_screens=requested_screens,
            registered_screens=registered_screens,
            existing_remote_count=existing_remote_count,
        ),
        None,
    )


def _log_media_info_error(error: Exception, meta: Meta) -> None:
    logger.error(f"[red]Error processing MediaInfo.json: {error}")
    if meta.debug:
        logger.debug(traceback.format_exc())


async def _capture_media_details(
    base_dir: str, folder_id: str, meta: Meta
) -> _CaptureMediaDetails | None:
    try:
        media_info_path = Path(base_dir) / "tmp" / folder_id / "MediaInfo.json"
        mi_text = await asyncio.to_thread(
            media_info_path.read_text, encoding="utf-8"
        )
        mi = cast(Mapping[str, Any], json.loads(mi_text))
        length, width, height, par, dar, frame_rate = _media_capture_values(mi)
        w_sar, h_sar = screenshot_par_scale_factors(width, height, par, dar)
        return _CaptureMediaDetails(
            length=length,
            width=width,
            height=height,
            w_sar=w_sar,
            h_sar=h_sar,
            frame_rate=frame_rate,
        )
    except Exception as error:
        _log_media_info_error(error, meta)
        return None


def _manual_frame_values(
    manual_frames: str | list[int] | list[str],
) -> list[int]:
    if isinstance(manual_frames, str):
        return [
            int(frame.strip())
            for frame in manual_frames.split(",")
            if frame.strip()
        ]
    return [int(frame) for frame in manual_frames]


def _manual_frames_enabled(
    manual_frames: str | list[int] | list[str], force_screenshots: bool
) -> bool:
    if force_screenshots:
        return False
    return bool(manual_frames)


def _log_manual_frames(
    manual_frames: str | list[int] | list[str], debug: bool
) -> None:
    if debug:
        logger.info(f"[yellow]Using manual frames: {manual_frames}")


def _raise_manual_frame_error(error: Exception, debug: bool) -> NoReturn:
    if debug:
        logger.error(f"[red]Error processing manual frames: {error}[/red]")
    raise ScreenshotCaptureError(
        f"Invalid manual frame selection: {error}"
    ) from error


def _manual_screenshot_times(
    manual_frames: str | list[int] | list[str],
    frame_rate: float,
    force_screenshots: bool,
    debug: bool,
) -> tuple[list[str], int]:
    if not _manual_frames_enabled(manual_frames, force_screenshots):
        return [], 0
    _log_manual_frames(manual_frames, debug)
    try:
        values = _manual_frame_values(manual_frames)
    except (TypeError, ValueError) as error:
        _raise_manual_frame_error(error, debug)
    return [str(frame / frame_rate) for frame in values], len(values)


def _remaining_capture_count(
    requested: int,
    screens: int,
    existing_remote_count: int,
    registered_count: int,
    force_screenshots: bool,
    retake: bool,
) -> int:
    count = requested
    if count <= 0:
        count = screens - existing_remote_count
    if not force_screenshots and not retake:
        count = max(0, count - registered_count)
    return count


def _existing_local_capture_paths(
    screenshot_dir: Path,
    sanitized_filename: str,
    num_screens: int,
    retake: bool,
) -> list[str]:
    if retake:
        return []
    paths = [
        str((screenshot_dir / f"{sanitized_filename}-{index}.png").resolve())
        for index in range(num_screens)
    ]
    return [path for path in paths if Path(path).exists()]


def _complete_local_capture(
    existing_paths: list[str], num_screens: int, retake: bool
) -> list[str] | None:
    if retake:
        return None
    if len(existing_paths) != num_screens:
        return None
    logger.debug(
        "[yellow]The correct number of screenshots already exists. Skipping capture process."
    )
    return existing_paths


def _paths_or_none(paths: list[str]) -> list[str] | None:
    return paths if paths else None


def _capture_requested_count(
    manual_count: int,
    num_screens: int,
    initial: _VideoCaptureInitial,
    force_screenshots: bool,
    retake: bool,
) -> int:
    requested = manual_count if manual_count > 0 else num_screens
    return _remaining_capture_count(
        requested,
        initial.screens,
        initial.existing_remote_count,
        len(initial.registered_screens),
        force_screenshots,
        retake,
    )


async def _capture_times(
    ss_times: list[str],
    num_capture: int,
    media: _CaptureMediaDetails,
    meta: Meta,
    force_screenshots: bool,
) -> list[str]:
    if ss_times:
        return ss_times
    return await valid_ss_time(
        [],
        num_capture,
        media.length,
        media.frame_rate,
        meta,
        retake=force_screenshots,
    )


async def _prepare_video_capture_plan(
    path: str,
    filename: str,
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int,
    force_screenshots: bool,
    manual_frames: str | list[int] | list[str],
    cleanup_after_capture: bool,
    initial: _VideoCaptureInitial,
) -> tuple[_VideoCapturePlan | None, list[str] | None]:
    media = await _capture_media_details(base_dir, folder_id, meta)
    if media is None:
        return None, None
    meta.frame_rate = media.frame_rate
    ss_times, manual_count = _manual_screenshot_times(
        manual_frames, media.frame_rate, force_screenshots, bool(meta.debug)
    )
    requested = _capture_requested_count(
        manual_count,
        num_screens,
        initial,
        force_screenshots,
        bool(meta.retake),
    )
    if requested <= 0:
        return None, _paths_or_none(initial.registered_screens)
    sanitized_filename = await sanitize_filename(filename)
    screenshot_dir = screenshots_dir(base_dir, folder_id)
    existing_paths = _existing_local_capture_paths(
        screenshot_dir, sanitized_filename, requested, bool(meta.retake)
    )
    complete = _complete_local_capture(
        existing_paths, requested, bool(meta.retake)
    )
    if complete is not None:
        return None, complete
    num_capture = requested - len(existing_paths)
    ss_times = await _capture_times(
        ss_times, num_capture, media, meta, force_screenshots
    )
    return (
        _VideoCapturePlan(
            path=path,
            folder_id=folder_id,
            base_dir=base_dir,
            meta=meta,
            img_host=initial.img_host,
            start_time=initial.start_time,
            group=initial.group,
            requested_screens=initial.requested_screens,
            registered_screens=initial.registered_screens,
            manual_frames=manual_frames,
            force_screenshots=force_screenshots,
            cleanup_after_capture=cleanup_after_capture,
            length=media.length,
            width=media.width,
            height=media.height,
            w_sar=media.w_sar,
            h_sar=media.h_sar,
            frame_rate=media.frame_rate,
            loglevel="verbose" if meta.ffdebug else "quiet",
            screenshot_dir=screenshot_dir,
            sanitized_filename=sanitized_filename,
            ss_times=ss_times,
            num_capture=num_capture,
            existing_images_count=len(existing_paths),
        ),
        None,
    )


def _frame_info_image_path(plan: _VideoCapturePlan, index: int) -> Path:
    return plan.screenshot_dir / (
        f"{plan.sanitized_filename}-{plan.existing_images_count + index}.png"
    )


def _frame_info_required(plan: _VideoCapturePlan, index: int) -> bool:
    if plan.meta.retake:
        return True
    return not _frame_info_image_path(plan, index).exists()


def _frame_info_mapping(
    plan: _VideoCapturePlan,
    tasks_with_index: Sequence[tuple[int, Awaitable[dict[str, Any]]]],
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for (index, _), info in zip(tasks_with_index, results, strict=False):
        mapping[plan.ss_times[index]] = info
    return mapping


async def _collect_video_frame_info(plan: _VideoCapturePlan) -> None:
    if not plan.meta.frame_overlay:
        return
    logger.debug("[yellow]Getting frame information for overlays...")
    tasks_with_index = [
        (index, get_frame_info(plan.path, plan.ss_times[index], plan.meta))
        for index in range(plan.num_capture)
        if _frame_info_required(plan, index)
    ]
    results = await asyncio.gather(*[task for _, task in tasks_with_index])
    plan.meta.frame_info_map = _frame_info_mapping(
        plan, tasks_with_index, results
    )
    logger.debug(
        f"[cyan]Collected frame information for {len(results)} frames"
    )


async def _capture_with_semaphore(
    semaphore: asyncio.Semaphore,
    args: tuple[
        int, str, float, str, float, float, float, float, str, bool, Meta
    ],
) -> tuple[int, str | None] | None:
    async with semaphore:
        return await capture_screenshot(args)


def _capture_task_arguments(
    plan: _VideoCapturePlan, hdr_tonemap: bool
) -> list[
    tuple[int, str, float, str, float, float, float, float, str, bool, Meta]
]:
    arguments = []
    for index in range(plan.num_capture):
        image_index = plan.existing_images_count + index
        image_path = str(
            (
                plan.screenshot_dir
                / f"{plan.sanitized_filename}-{image_index}.png"
            ).resolve()
        )
        if Path(image_path).exists() and not plan.meta.retake:
            continue
        arguments.append(
            (
                index,
                plan.path,
                float(plan.ss_times[index]),
                image_path,
                plan.width,
                plan.height,
                plan.w_sar,
                plan.h_sar,
                plan.loglevel,
                hdr_tonemap,
                plan.meta,
            )
        )
    return arguments


def _log_capture_batch_exceptions(results: list[object]) -> None:
    for result in results:
        if isinstance(result, Exception):
            logger.info(f"[red]Screenshot capture exception: {result}[/red]")


def _capture_paths_from_batch(results: list[object]) -> list[str]:
    tuples = [
        cast(tuple[int, str | None], result)
        for result in results
        if isinstance(result, tuple)
    ]
    tuples.sort(key=lambda item: item[0])
    return [path for _, path in tuples if path is not None]


async def _run_video_capture_batch(
    arguments: list[
        tuple[
            int, str, float, str, float, float, float, float, str, bool, Meta
        ]
    ],
) -> list[str]:
    workers = min(len(arguments), task_limit)
    logger.debug(f"Using {workers} worker(s) for {len(arguments)} image(s)")
    semaphore = asyncio.Semaphore(max(1, workers))
    tasks = [_capture_with_semaphore(semaphore, args) for args in arguments]
    try:
        results = cast(
            list[object], await asyncio.gather(*tasks, return_exceptions=True)
        )
    except KeyboardInterrupt:
        logger.info(
            "\n[red]CTRL+C detected. Cancelling capture tasks...[/red]"
        )
        gc.collect()
        cleanup_manager.reset_terminal()
        raise
    except asyncio.CancelledError:
        gc.collect()
        cleanup_manager.reset_terminal()
        raise
    except Exception as error:
        gc.collect()
        cleanup_manager.reset_terminal()
        raise ScreenshotCaptureError(
            f"Screenshot capture failed: {error}"
        ) from error
    finally:
        logger.debug(
            "[yellow]All capture tasks finished. Cleaning up...[/yellow]"
        )
    _log_capture_batch_exceptions(results)
    return _capture_paths_from_batch(results)


def _manual_lostimg_rejected(
    manual_frames: str | list[int] | list[str],
    img_host: str | None,
    image_size: int,
) -> bool:
    if not manual_frames:
        return False
    if img_host != "lostimg":
        return False
    return not is_valid_lostimg_image_size(image_size)


def _image_capture_index(image_path: str) -> int:
    return int(image_path.rsplit("-", 1)[-1].split(".")[0])


def _original_capture_time(index: int, ss_times: list[str]) -> str | None:
    if index >= len(ss_times):
        return None
    return ss_times[index]


def _random_retake_times(length: float) -> list[float]:
    return [
        random.uniform(0, length)  # nosec B311 - Screenshot timing is not cryptographic.  # noqa: S311
        for _ in range(5)
    ]


def _offset_retake_times(original_time: str) -> list[float]:
    offsets = [5.0, 10.0, -10.0, 100.0, -100.0]
    return [
        max(0, float(original_time) + offset)
        for _ in range(5)
        for offset in offsets
    ]


def _retake_candidate_times(
    image_path: str, ss_times: list[str], length: float
) -> tuple[int, list[float]]:
    index = _image_capture_index(image_path)
    original_time = _original_capture_time(index, ss_times)
    if original_time is None:
        return index, _random_retake_times(length)
    return index, _offset_retake_times(original_time)


def _retake_response_path(
    response: tuple[int, str | None] | None,
) -> str | None:
    if not isinstance(response, tuple):
        return None
    if len(response) != 2:
        return None
    screenshot_path = response[1]
    if not screenshot_path:
        return None
    return screenshot_path if Path(screenshot_path).exists() else None


def _retake_size_valid(plan: _VideoCapturePlan, screenshot_path: str) -> bool:
    size = Path(screenshot_path).stat().st_size
    return _capture_size_is_valid(plan.img_host, size)


async def _retake_capture_attempt(
    plan: _VideoCapturePlan,
    image_path: str,
    original_index: int,
    adjusted_time: float,
    hdr_tonemap: bool,
) -> str | None:
    try:
        Path(image_path).unlink(missing_ok=True)
        response = await capture_screenshot(
            (
                original_index,
                plan.path,
                adjusted_time,
                image_path,
                plan.width,
                plan.height,
                plan.w_sar,
                plan.h_sar,
                plan.loglevel,
                hdr_tonemap,
                plan.meta,
            )
        )
    except Exception as error:
        logger.error(
            f"[red]Error retaking screenshot for {image_path} at {adjusted_time:.2f}s: {error}[/red]"
        )
        return None
    screenshot_path = _retake_response_path(response)
    if screenshot_path is None:
        return None
    return (
        screenshot_path if _retake_size_valid(plan, screenshot_path) else None
    )


async def _retake_capture(
    plan: _VideoCapturePlan, image_path: str, hdr_tonemap: bool
) -> str | None:
    original_index, candidate_times = _retake_candidate_times(
        image_path, plan.ss_times, plan.length
    )
    for attempt, adjusted_time in enumerate(candidate_times, start=1):
        logger.info(
            f"[yellow]Retaking screenshot for: {image_path} (Attempt {attempt}/{len(candidate_times)}) at {adjusted_time:.2f}s[/yellow]"
        )
        screenshot_path = await _retake_capture_attempt(
            plan, image_path, original_index, adjusted_time, hdr_tonemap
        )
        if screenshot_path:
            new_size = Path(screenshot_path).stat().st_size
            logger.info(
                f"[green]Successfully retaken screenshot for: {screenshot_path} ({new_size} bytes)[/green]"
            )
            return screenshot_path
    logger.info(
        f"[red]All retry attempts failed for {image_path}. Skipping.[/red]"
    )
    gc.collect()
    return None


async def _validated_capture_path(
    plan: _VideoCapturePlan, image_path: str, hdr_tonemap: bool
) -> str | None:
    image_size = Path(image_path).stat().st_size
    logger.debug(
        f"[yellow]Checking image {image_path} (size: {image_size} bytes) for image host: {plan.img_host}[/yellow]"
    )
    if _manual_lostimg_rejected(plan.manual_frames, plan.img_host, image_size):
        logger.info(
            f"[red]Image {image_path} with size {image_size} bytes: does not meet size requirements for {plan.img_host}, skipping.[/red]"
        )
        return None
    if plan.manual_frames or _capture_size_is_valid(plan.img_host, image_size):
        return image_path
    return await _retake_capture(plan, image_path, hdr_tonemap)


async def _validated_capture_paths(
    plan: _VideoCapturePlan,
    capture_results: list[str],
    hdr_tonemap: bool,
) -> list[str]:
    valid_results: list[str] = []
    failed_results: list[str] = []
    for image_path in capture_results:
        valid_path = await _validated_capture_path(
            plan, image_path, hdr_tonemap
        )
        if valid_path is None:
            failed_results.append(image_path)
            continue
        valid_results.append(valid_path)
    if failed_results:
        logger.info(
            f"[red]The following images could not be retaken successfully: {failed_results}[/red]"
        )
    return valid_results


def _log_video_capture_timing(
    plan: _VideoCapturePlan, valid_results: list[str]
) -> None:
    logger.debug(
        f"[green]Successfully processed {len(valid_results)} screenshots."
    )
    if plan.meta.debug:
        logger.debug(
            f"Screenshots processed in {time.time() - plan.start_time:.4f} seconds"
        )


def _return_manifest_after_capture(plan: _VideoCapturePlan) -> bool:
    if plan.force_screenshots:
        return False
    return not plan.meta.retake


def _final_video_capture_paths(
    plan: _VideoCapturePlan, new_screens: list[Path]
) -> list[str] | None:
    if _return_manifest_after_capture(plan):
        return [
            str(screen)
            for screen in manifest_files(
                plan.base_dir, plan.folder_id, plan.group
            )[: plan.requested_screens]
        ]
    paths = [str(screen) for screen in new_screens]
    return paths if paths else None


def _tonemap_test_time(ss_times: list[str]) -> str:
    return str(ss_times[0]) if ss_times else "0"


def _log_capture_count(
    plan: _VideoCapturePlan, capture_results: list[str]
) -> None:
    if plan.force_screenshots:
        return
    if plan.meta.debug:
        logger.info(
            f"[green]Successfully captured {len(capture_results)} screenshots."
        )


async def _cleanup_video_capture(plan: _VideoCapturePlan) -> None:
    if _should_cleanup_after_capture(plan.meta, plan.cleanup_after_capture):
        await cleanup_manager.cleanup()


def _register_video_capture_results(
    plan: _VideoCapturePlan, valid_results: list[str]
) -> list[Path]:
    if not valid_results:
        return []
    return register_screenshots(
        plan.base_dir, plan.folder_id, valid_results, plan.group
    )


async def _execute_video_capture(plan: _VideoCapturePlan) -> list[str] | None:
    await _collect_video_frame_info(plan)
    test_time = _tonemap_test_time(plan.ss_times)
    test_image_path = str(
        (
            plan.screenshot_dir
            / f"{plan.sanitized_filename}-libplacebo-test.png"
        ).resolve()
    )
    hdr_tonemap = await determine_tonemapping(
        plan.w_sar,
        plan.h_sar,
        plan.width,
        plan.height,
        plan.path,
        test_time,
        test_image_path,
        plan.loglevel,
        plan.meta,
    )
    capture_results = await _run_video_capture_batch(
        _capture_task_arguments(plan, hdr_tonemap)
    )
    _log_capture_count(plan, capture_results)
    valid_results = await _validated_capture_paths(
        plan, capture_results, hdr_tonemap
    )
    _log_video_capture_timing(plan, valid_results)
    await _cleanup_video_capture(plan)
    new_screens = _register_video_capture_results(plan, valid_results)
    return _final_video_capture_paths(plan, new_screens)


async def _video_screenshots(
    path: str,
    filename: str,
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int,
    force_screenshots: bool,
    manual_frames: str | list[int] | list[str],
    cleanup_after_capture: bool,
    capture_group: str | None,
) -> list[str] | None:
    initial, early_result = await _initial_video_capture(
        folder_id,
        base_dir,
        meta,
        num_screens,
        manual_frames,
        force_screenshots,
        capture_group,
    )
    if initial is None:
        return early_result
    plan, early_result = await _prepare_video_capture_plan(
        path,
        filename,
        folder_id,
        base_dir,
        meta,
        num_screens,
        force_screenshots,
        manual_frames,
        cleanup_after_capture,
        initial,
    )
    if plan is None:
        return early_result
    return await _execute_video_capture(plan)


async def screenshots(
    path: str,
    filename: str,
    folder_id: str,
    base_dir: str,
    meta: Meta,
    num_screens: int = 0,
    force_screenshots: bool = False,
    manual_frames: str | list[int] | list[str] = "",
    cleanup_after_capture: bool = True,
    capture_group: str | None = None,
) -> list[str] | None:
    if meta.category == "GAME":
        return []
    if meta.category == "BOOK":
        return await _book_screenshot_result(
            path, filename, folder_id, base_dir, meta, num_screens
        )
    return await _video_screenshots(
        path,
        filename,
        folder_id,
        base_dir,
        meta,
        num_screens,
        force_screenshots,
        manual_frames,
        cleanup_after_capture,
        capture_group,
    )


def _capture_dimensions_valid(
    width: float, height: float, ss_time: float
) -> bool:
    return width > 0 and height > 0 and ss_time >= 0


def _capture_input_path(path: str, meta: Meta) -> str | None:
    normalized = os.path.normpath(path)
    if Path(normalized).is_dir():
        logger.info(
            f"[yellow]Error: Path is a directory, not a file: {normalized}[/yellow]"
        )
        if not meta.filelist:
            return None
        normalized = meta.filelist[0]
        logger.info(
            f"[green]Using first file from filelist: {normalized}[/green]"
        )
    if Path(normalized).exists():
        return normalized
    logger.info(f"[red]Error: Input file does not exist: {normalized}[/red]")
    return None


def _capture_ready_input(
    path: str, width: float, height: float, ss_time: float, meta: Meta
) -> str | None:
    if not _capture_dimensions_valid(width, height, ss_time):
        return None
    return _capture_input_path(path, meta)


def _log_capture_input(path: str, verbose: bool) -> None:
    if verbose:
        logger.info(f"[cyan]Processing file: {path}[/cyan]")


def _libplacebo_warmup_required(meta: Meta, hdr_tonemap: bool) -> bool:
    return bool(hdr_tonemap and meta.libplacebo and not meta.libplacebo_warmed)


async def _prepare_capture_libplacebo(
    path: str, meta: Meta, loglevel: str, hdr_tonemap: bool
) -> None:
    if meta.frame_overlay:
        return
    if not use_libplacebo:
        return
    meta.libplacebo_warmed = not _as_bool(
        default_config.get("ffmpeg_warmup"), default=False
    )
    if _libplacebo_warmup_required(meta, hdr_tonemap):
        await libplacebo_warmup(path, meta, loglevel)


def _capture_scale_filters(
    width: float,
    height: float,
    w_sar: float,
    h_sar: float,
    verbose: bool,
) -> list[str]:
    if w_sar == 1 and h_sar == 1:
        return []
    scaled_w = round_to_even(width * w_sar)
    scaled_h = round_to_even(height * h_sar)
    if verbose:
        logger.info(f"[cyan]Applied PAR scale -> {scaled_w}x{scaled_h}[/cyan]")
    return [f"scale={scaled_w}:{scaled_h}"]


def _capture_tonemap_filters(
    hdr_tonemap: bool, hardware_libplacebo: bool
) -> list[str]:
    if not hdr_tonemap:
        return []
    if hardware_libplacebo:
        return [
            "libplacebo=tonemapping=hable:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
        ]
    return _software_tonemap_filters(True)


def _capture_filter_chain(
    meta: Meta,
    ss_time: float,
    width: float,
    height: float,
    w_sar: float,
    h_sar: float,
    hdr_tonemap: bool,
    hardware_libplacebo: bool,
    verbose: bool,
) -> str:
    filters = _capture_scale_filters(width, height, w_sar, h_sar, verbose)
    filters.extend(_capture_tonemap_filters(hdr_tonemap, hardware_libplacebo))
    filters.extend(
        _frame_overlay_filters(
            meta,
            ss_time,
            hdr_tonemap=hdr_tonemap,
            base_resolution=1080,
        )
    )
    filters.append("format=rgb24")
    return ",".join(filters)


def _capture_global_args(
    loglevel: str, meta: Meta, hardware_libplacebo: bool
) -> list[str]:
    effective_loglevel = (
        "error" if meta.debug and loglevel == "quiet" else loglevel
    )
    global_args = [
        "-y",
        "-nostdin",
        "-loglevel",
        effective_loglevel,
        "-hide_banner",
        "-map",
        "0:v:0",
        "-an",
        "-sn",
    ]
    if hardware_libplacebo:
        global_args.extend(["-init_hw_device", "vulkan"])
    if ffmpeg_limit:
        global_args.extend(["-threads", "1"])
    return global_args


def _capture_command(
    path: str,
    ss_time: float,
    image_path: str,
    filter_chain: str,
    loglevel: str,
    meta: Meta,
    hardware_libplacebo: bool,
) -> Any:
    command = (
        cast(Any, ffmpeg)
        .input(path, ss=str(ss_time))
        .output(
            image_path,
            vframes=1,
            vf=filter_chain,
            compression_level=ffmpeg_compression,
            pred="mixed",
        )
    )
    return command.global_args(
        *_capture_global_args(loglevel, meta, hardware_libplacebo)
    )


def _log_capture_command(command: Any, verbose: bool) -> None:
    if not verbose:
        return
    try:
        logger.info(
            f"FFmpeg command: {' '.join(compile_ffmpeg_command(command))}",
            extra={"markup": False, "highlighter": None},
        )
    except Exception:
        logger.info("[cyan]FFmpeg command: (unable to render command)[/cyan]")


async def _run_capture_command(
    command: Any, timeout_seconds: float
) -> tuple[int | None, bytes, bytes]:
    try:
        return await asyncio.wait_for(
            run_ffmpeg(command), timeout=timeout_seconds
        )
    except TimeoutError:
        return -1, b"", b"Timeout"


def _hardware_libplacebo_enabled(meta: Meta, hdr_tonemap: bool) -> bool:
    return bool(hdr_tonemap and not meta.frame_overlay and meta.libplacebo)


def _build_capture_command(
    path: str,
    ss_time: float,
    image_path: str,
    width: float,
    height: float,
    w_sar: float,
    h_sar: float,
    loglevel: str,
    hdr_tonemap: bool,
    meta: Meta,
    hardware_libplacebo: bool,
    verbose: bool,
) -> Any:
    chain = _capture_filter_chain(
        meta,
        ss_time,
        width,
        height,
        w_sar,
        h_sar,
        hdr_tonemap,
        hardware_libplacebo,
        verbose,
    )
    return _capture_command(
        path,
        ss_time,
        image_path,
        chain,
        loglevel,
        meta,
        hardware_libplacebo,
    )


def _capture_result_succeeded(result: tuple[int | None, bytes, bytes]) -> bool:
    return result[0] == 0


async def _retry_libplacebo_capture(
    command: Any, verbose: bool
) -> tuple[int | None, bytes, bytes]:
    if verbose:
        logger.info(
            "[yellow]First libplacebo attempt failed; retrying once...[/yellow]"
        )
    await asyncio.sleep(1.0)
    return await _run_capture_command(command, 160)


async def _run_capture_with_libplacebo_fallback(
    command: Any,
    *,
    path: str,
    ss_time: float,
    image_path: str,
    width: float,
    height: float,
    w_sar: float,
    h_sar: float,
    loglevel: str,
    hdr_tonemap: bool,
    meta: Meta,
    hardware_libplacebo: bool,
    verbose: bool,
) -> tuple[int | None, bytes, bytes]:
    result = await _run_capture_command(command, 140)
    if not hardware_libplacebo:
        return result
    if _capture_result_succeeded(result):
        return result
    result = await _retry_libplacebo_capture(command, verbose)
    if _capture_result_succeeded(result):
        return result
    if verbose:
        logger.info(
            "[red]libplacebo failed twice; falling back to zscale tonemap[/red]"
        )
    meta.libplacebo = False
    fallback = _build_capture_command(
        path,
        ss_time,
        image_path,
        width,
        height,
        w_sar,
        h_sar,
        loglevel,
        hdr_tonemap,
        meta,
        False,
        verbose,
    )
    return await _run_capture_command(fallback, 140)


def _capture_output_exists(returncode: int | None, image_path: str) -> bool:
    return returncode == 0 and Path(image_path).exists()


def _log_capture_failure(stderr: bytes, verbose: bool) -> None:
    stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    if "Error initializing complex filters" in stderr_text:
        logger.info(
            "[red]FFmpeg complex filters error: see https://github.com/wastaken7/Upload-Assistant/blob/development/docs/ffmpeg-max-workers-issues.md[/red]"
        )
        return
    if verbose:
        logger.info(f"[red]FFmpeg process failed (final): {stderr_text}[/red]")


def _capture_result(
    index: int,
    image_path: str,
    returncode: int | None,
    stderr: bytes,
    verbose: bool,
) -> tuple[int, str | None]:
    if _capture_output_exists(returncode, image_path):
        if verbose:
            logger.info(
                f"[green]Screenshot captured successfully: {image_path}[/green]"
            )
        return index, image_path
    _log_capture_failure(stderr, verbose)
    return index, None


async def capture_screenshot(
    args: tuple[
        int, str, float, str, float, float, float, float, str, bool, Meta
    ],
) -> tuple[int, str | None] | None:
    """Capture one frame while keeping tonemapping and overlay policy explicit."""
    (
        index,
        path,
        ss_time,
        image_path,
        width,
        height,
        w_sar,
        h_sar,
        loglevel,
        hdr_tonemap,
        meta,
    ) = args
    try:
        path = _capture_ready_input(path, width, height, ss_time, meta)
        if path is None:
            return None
        verbose = loglevel == "verbose" or meta.debug
        _log_capture_input(path, verbose)
        await _prepare_capture_libplacebo(path, meta, loglevel, hdr_tonemap)
        hardware_libplacebo = _hardware_libplacebo_enabled(meta, hdr_tonemap)
        command = _build_capture_command(
            path,
            ss_time,
            image_path,
            width,
            height,
            w_sar,
            h_sar,
            loglevel,
            hdr_tonemap,
            meta,
            hardware_libplacebo,
            verbose,
        )
        _log_capture_command(command, verbose)
        (
            returncode,
            stdout,
            stderr,
        ) = await _run_capture_with_libplacebo_fallback(
            command,
            path=path,
            ss_time=ss_time,
            image_path=image_path,
            width=width,
            height=height,
            w_sar=w_sar,
            h_sar=h_sar,
            loglevel=loglevel,
            hdr_tonemap=hdr_tonemap,
            meta=meta,
            hardware_libplacebo=hardware_libplacebo,
            verbose=verbose,
        )
        _log_ffmpeg_streams(loglevel, stdout, stderr)
        return _capture_result(index, image_path, returncode, stderr, verbose)
    except asyncio.CancelledError:
        logger.info(traceback.format_exc())
        raise
    except Exception:
        logger.info(traceback.format_exc())
        return None


def _screenshot_retake_offset(meta: Meta, retake: bool) -> float:
    if not retake:
        return 0.0
    if meta.retake_call_count is None:
        meta.retake_call_count = 0
    meta.retake_call_count += 1
    offset = meta.retake_call_count * 0.01
    logger.debug(
        f"[cyan]Retake call #{meta.retake_call_count}, adding {offset:.1%} offset[/cyan]"
    )
    return offset


def _screenshot_start_fraction(meta: Meta, retake: bool) -> float:
    if retake and meta.category == "TV":
        return 0.1
    return 0.05


def _screenshot_frame_bounds(
    total_frames: int, meta: Meta, retake_offset: float, retake: bool
) -> tuple[int, int]:
    start_fraction = _screenshot_start_fraction(meta, retake) + retake_offset
    start_frame = int(total_frames * start_fraction)
    end_frame = int(total_frames * 0.9)
    return min(start_frame, int(total_frames * 0.4)), end_frame


def _screenshot_frame_schedule(
    total_screens: int, start_frame: int, end_frame: int
) -> tuple[list[int], int]:
    usable_frames = end_frame - start_frame
    interval = (
        usable_frames // total_screens if total_screens > 1 else usable_frames
    )
    frames = [
        start_frame + (index * interval) for index in range(total_screens)
    ]
    return frames, interval


def _log_screenshot_schedule(
    total_screens: int,
    total_frames: int,
    start_frame: int,
    end_frame: int,
    frame_interval: int,
    chosen_frames: list[int],
) -> None:
    usable_frames = end_frame - start_frame
    logger.debug(
        f"[purple]Screenshots information:[/purple] \n[slate_blue3]Screenshots: [gold3]{total_screens}[/gold3] \nTotal Frames: [gold3]{total_frames}[/gold3]"
    )
    logger.debug(
        f"[slate_blue3]Start frame: [gold3]{start_frame}[/gold3] \nEnd frame: [gold3]{end_frame}[/gold3] \nUsable frames: [gold3]{usable_frames}[/gold3][/slate_blue3]"
    )
    logger.debug(
        f"[yellow]frame interval: {frame_interval} \n[purple]Chosen Frames[/purple]\n[gold3]{chosen_frames}[/gold3]\n"
    )


async def valid_ss_time(
    ss_times: list[str],
    num_screens: int,
    length: float,
    frame_rate: float,
    meta: Meta,
    retake: bool = False,
) -> list[str]:
    total_screens = num_screens + 1 if meta.is_disc else num_screens
    total_frames = int(length * frame_rate)
    retake_offset = _screenshot_retake_offset(meta, retake)
    start_frame, end_frame = _screenshot_frame_bounds(
        total_frames, meta, retake_offset, retake
    )
    chosen_frames, frame_interval = _screenshot_frame_schedule(
        total_screens, start_frame, end_frame
    )
    result_times = [
        *ss_times,
        *(str(frame / frame_rate) for frame in chosen_frames),
    ]
    _log_screenshot_schedule(
        total_screens,
        total_frames,
        start_frame,
        end_frame,
        frame_interval,
        chosen_frames,
    )
    return sorted(result_times)


def _frame_info_command(path: str, ss_time: float) -> Any:
    info_ff = cast(Any, ffmpeg).input(path, ss=ss_time)
    filtered = info_ff["v:0"].filter("showinfo")
    return filtered.output("-", format="null", vframes=1).global_args(
        "-loglevel", "info"
    )


def _frame_rate_value(meta: Meta) -> float:
    return float(meta.frame_rate) if meta.frame_rate is not None else 24.0


def _frame_type_from_stderr(stderr_text: str) -> str:
    match = re.search(r"pict_type:(\w)", stderr_text)
    if match:
        return match.group(1)
    alternative = re.search(r"type:(\w)\s", stderr_text)
    return alternative.group(1) if alternative else "Unknown"


def _pts_time_from_stderr(stderr_text: str) -> float | None:
    match = re.search(r"pts_time:(\d+\.\d+)", stderr_text)
    return float(match.group(1)) if match else None


def _parsed_frame_info(
    stderr_text: str, ss_time_value: float, frame_rate: float
) -> dict[str, Any]:
    frame_info: dict[str, Any] = {
        "frame_type": _frame_type_from_stderr(stderr_text),
        "frame_number": int(ss_time_value * frame_rate),
    }
    exact_time = _pts_time_from_stderr(stderr_text)
    if exact_time is not None:
        frame_info["pts_time"] = exact_time
        frame_info["frame_number"] = int(exact_time * frame_rate)
    return frame_info


def _estimated_frame_info(ss_time: str | float, meta: Meta) -> dict[str, Any]:
    return {
        "frame_type": "Unknown",
        "frame_number": int(float(ss_time) * _frame_rate_value(meta)),
    }


async def get_frame_info(
    path: str, ss_time: str | float, meta: Meta
) -> dict[str, Any]:
    """Get frame information (type, exact timestamp) for a specific frame."""
    try:
        ss_time_value = float(ss_time)
        command = _frame_info_command(path, ss_time_value)
        cmd = compile_ffmpeg_command(command)
        logger.debug(f"[cyan]FFmpeg showinfo command: {' '.join(cmd)}[/cyan]")
        returncode, _stdout, stderr = await run_ffmpeg(command)
        if returncode is None:
            raise RuntimeError(
                "FFmpeg subprocess did not complete properly. The process may have been terminated unexpectedly or failed to start. "
                f"Command: {' '.join(cmd)}"
            )
        return _parsed_frame_info(
            stderr.decode("utf-8", errors="replace"),
            ss_time_value,
            _frame_rate_value(meta),
        )
    except Exception as error:
        logger.info(
            f"[yellow]Error getting frame info: {error}. Will use estimated values.[/yellow]"
        )
        logger.debug(traceback.format_exc())
        return _estimated_frame_info(ss_time, meta)


def _libplacebo_filter_complex(
    w_sar: float,
    h_sar: float,
    width: float,
    height: float,
) -> tuple[str, str]:
    filter_parts: list[str] = []
    input_label = "[0:v]"
    if w_sar != 1 or h_sar != 1:
        scaled_w = round_to_even(width * w_sar)
        scaled_h = round_to_even(height * h_sar)
        filter_parts.append(
            f"{input_label}scale={scaled_w}:{scaled_h}[scaled]"
        )
        input_label = "[scaled]"
    filter_parts.append(
        f"{input_label}libplacebo=tonemapping=auto:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv[out]"
    )
    return ",".join(filter_parts), "[out]"


def _libplacebo_test_command(
    w_sar: float,
    h_sar: float,
    width: float,
    height: float,
    path: str,
    ss_time: str,
    test_image_path: str,
) -> Any:
    filter_complex, output_map = _libplacebo_filter_complex(
        w_sar, h_sar, width, height
    )
    return (
        cast(Any, ffmpeg)
        .input(path, ss=ss_time)
        .output(test_image_path, vframes=1, pix_fmt="rgb24")
        .global_args(
            "-y",
            "-loglevel",
            "quiet",
            "-init_hw_device",
            "vulkan",
            "-filter_complex",
            filter_complex,
            "-map",
            output_map,
        )
    )


def _zscale_test_command(path: str, ss_time: str, test_image_path: str) -> Any:
    vf_chain = (
        f"zscale=transfer=linear,tonemap=tonemap={algorithm}:desat={desat},"
        "zscale=transfer=bt709,format=rgb24"
    )
    return (
        cast(Any, ffmpeg)
        .input(path, ss=ss_time)
        .output(test_image_path, vframes=1, vf=vf_chain, pix_fmt="rgb24")
        .global_args("-y", "-loglevel", "quiet")
    )


async def _run_tonemap_compatibility_command(
    command: Any, loglevel: str, meta: Meta
) -> bool:
    if loglevel == "verbose" or meta.debug:
        logger.info(
            f"[cyan]libplacebo compatibility test command: {' '.join(compile_ffmpeg_command(command))}[/cyan]"
        )
    try:
        retcode, _stdout, _stderr = await run_ffmpeg(command)
        return retcode == 0
    except Exception:
        return False


def _cleanup_libplacebo_test_image(test_image_path: str) -> None:
    with contextlib.suppress(Exception):
        path = Path(test_image_path)
        if path.exists():
            path.unlink()


async def check_libplacebo_compatibility(
    w_sar: float,
    h_sar: float,
    width: float,
    height: float,
    path: str,
    ss_time: str,
    image_path: str,
    loglevel: str,
    meta: Meta,
) -> tuple[bool, bool]:
    if meta.is_disc:
        return False, False
    test_image_path = image_path.replace(".png", "_test.png")
    libplacebo_ok = await _run_tonemap_compatibility_command(
        _libplacebo_test_command(
            w_sar,
            h_sar,
            width,
            height,
            path,
            ss_time,
            test_image_path,
        ),
        loglevel,
        meta,
    )
    if libplacebo_ok:
        logger.debug("[green]libplacebo compatibility test succeeded[/green]")
        _cleanup_libplacebo_test_image(test_image_path)
        return True, True
    zscale_ok = await _run_tonemap_compatibility_command(
        _zscale_test_command(path, ss_time, test_image_path),
        loglevel,
        meta,
    )
    if not zscale_ok:
        return False, False
    logger.debug(
        "[yellow]libplacebo compatibility test failed, but zscale HDR tonemapping is compatible[/yellow]"
    )
    _cleanup_libplacebo_test_image(test_image_path)
    return False, True


def _tonemapping_requested(meta: Meta) -> bool:
    return bool(
        tone_map and any(marker in meta.hdr for marker in ("HDR", "DV", "HLG"))
    )


def _hardware_tonemap_path_allowed(meta: Meta) -> bool:
    return bool(use_libplacebo and not meta.frame_overlay)


async def _select_verified_tonemap_path(
    w_sar: float,
    h_sar: float,
    width: float,
    height: float,
    path: str,
    ss_time: str,
    image_path: str,
    loglevel: str,
    meta: Meta,
) -> bool:
    if ffmpeg_is_good:
        meta.libplacebo = True
        return True
    libplacebo, compatible = await check_libplacebo_compatibility(
        w_sar,
        h_sar,
        width,
        height,
        path,
        ss_time,
        image_path,
        loglevel,
        meta,
    )
    if not compatible:
        logger.info("[yellow]FFMPEG failed tonemap checking.[/yellow]")
        return False
    meta.libplacebo = libplacebo
    return True


async def determine_tonemapping(
    w_sar: float,
    h_sar: float,
    width: float,
    height: float,
    path: str,
    ss_time: str,
    image_path: str,
    loglevel: str,
    meta: Meta,
) -> bool:
    """Select a verified tonemapping path and record its actual metadata state."""
    meta.libplacebo = False
    meta.tonemapped = False
    if not _tonemapping_requested(meta):
        return False
    if _hardware_tonemap_path_allowed(meta):
        selected = await _select_verified_tonemap_path(
            w_sar,
            h_sar,
            width,
            height,
            path,
            ss_time,
            image_path,
            loglevel,
            meta,
        )
        if not selected:
            return False
    meta.tonemapped = True
    return True


def _libplacebo_warmup_needed(path: str, meta: Meta) -> bool:
    return bool(
        meta.libplacebo and not meta.libplacebo_warmed and Path(path).exists()
    )


def _libplacebo_warmup_command(path: str) -> Any:
    return (
        cast(Any, ffmpeg)
        .input(path, ss="0.1")
        .output("-", format="null", vframes=1)
        .global_args(
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-init_hw_device",
            "vulkan",
            "-vf",
            "libplacebo=tonemapping=hable:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,format=rgb24",
            "-loglevel",
            "error",
        )
    )


def _verbose_warmup(meta: Meta, loglevel: str) -> bool:
    return bool(loglevel == "verbose" or meta.debug)


async def libplacebo_warmup(path: str, meta: Meta, loglevel: str) -> None:
    if not _libplacebo_warmup_needed(path, meta):
        return
    verbose = _verbose_warmup(meta, loglevel)
    if verbose:
        logger.info("[cyan]Running libplacebo warm-up...[/cyan]")
    try:
        await run_ffmpeg(_libplacebo_warmup_command(path))
    except Exception:
        if verbose:
            logger.info(
                "[yellow]libplacebo warm-up failed or errored (continuing anyway)[/yellow]"
            )
    meta.libplacebo_warmed = True


def _normalized_image_host_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.lower().strip()
    return normalized or None


def _first_image_host_from_list(values: list[Any]) -> str | None:
    for item in values:
        normalized = _normalized_image_host_string(item)
        if normalized is not None:
            return normalized
    return None


def _meta_image_host_value(value: Any) -> str | None:
    direct = _normalized_image_host_string(value)
    if direct is not None:
        return direct
    if not isinstance(value, list):
        return None
    return _first_image_host_from_list(cast(list[Any], value))


def _configured_primary_image_host() -> str | None:
    hosts = [
        str(default_config[key]).lower()
        for key in sorted(default_config)
        if key.startswith("img_host_1") and not key.endswith("0")
    ]
    return hosts[0] if hosts else None


async def get_image_host(meta: Meta) -> str | None:
    meta_host = _meta_image_host_value(meta.imghost)
    return (
        meta_host
        if meta.imghost is not None
        else _configured_primary_image_host()
    )


class TakeScreensManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        _apply_config(config)

    async def run_ffmpeg(
        self, command: Any
    ) -> tuple[int | None, bytes, bytes]:
        return await run_ffmpeg(command)

    async def sanitize_filename(self, filename: str) -> str:
        return await sanitize_filename(filename)

    async def disc_screenshots(
        self,
        meta: Meta,
        filename: str,
        bdinfo: dict[str, Any],
        folder_id: str,
        base_dir: str,
        use_vs: bool,
        image_list: list[dict[str, str]] | None = None,
        ffdebug: bool = False,
        num_screens: int = 0,
        force_screenshots: bool = False,
        cleanup_after_capture: bool = True,
        capture_group: str | None = None,
    ) -> list[Path]:
        return await disc_screenshots(
            meta,
            filename,
            bdinfo,
            folder_id,
            base_dir,
            use_vs,
            image_list,
            ffdebug,
            num_screens,
            force_screenshots,
            cleanup_after_capture,
            capture_group,
        )

    async def capture_disc_task(
        self,
        index: int,
        file: str,
        ss_time: str,
        image_path: str,
        keyframe: str,
        loglevel: str,
        hdr_tonemap: bool,
        meta: Meta,
    ) -> tuple[int, str] | None:
        return await capture_disc_task(
            index,
            file,
            ss_time,
            image_path,
            keyframe,
            loglevel,
            hdr_tonemap,
            meta,
        )

    async def dvd_screenshots(
        self,
        meta: Meta,
        disc_num: int,
        num_screens: int = 0,
        retry_cap: bool = False,
        cleanup_after_capture: bool = True,
    ) -> None:
        await dvd_screenshots(
            meta, disc_num, num_screens, retry_cap, cleanup_after_capture
        )

    async def capture_dvd_screenshot(
        self, task: tuple[int, str, str, str, Meta, float, float, float, float]
    ) -> tuple[int, str | None]:
        return await capture_dvd_screenshot(task)

    async def screenshots(
        self,
        path: str,
        filename: str,
        folder_id: str,
        base_dir: str,
        meta: Meta,
        num_screens: int = 0,
        force_screenshots: bool = False,
        manual_frames: str | list[int] | list[str] = "",
        cleanup_after_capture: bool = True,
        capture_group: str | None = None,
    ) -> list[str] | None:
        return await screenshots(
            path,
            filename,
            folder_id,
            base_dir,
            meta,
            num_screens,
            force_screenshots,
            manual_frames,
            cleanup_after_capture,
            capture_group,
        )

    def xxx_contact_sheet_settings(self) -> tuple[int, int, int]:
        return xxx_contact_sheet_settings()

    def xxx_contact_sheet_animation_settings(self) -> tuple[bool, float]:
        return xxx_contact_sheet_animation_settings()

    async def xxx_contact_sheets(
        self,
        paths: list[str],
        folder_id: str,
        base_dir: str,
        meta: Meta,
        capture_group: str = "main",
    ) -> list[str]:
        return await xxx_contact_sheets(
            paths, folder_id, base_dir, meta, capture_group
        )

    async def prepare_book_cover(
        self, path: str, folder_id: str, base_dir: str, meta: Meta
    ) -> str | None:
        return await prepare_book_cover(path, folder_id, base_dir, meta)

    async def capture_screenshot(
        self,
        args: tuple[
            int, str, float, str, float, float, float, float, str, bool, Meta
        ],
    ) -> tuple[int, str | None] | None:
        return await capture_screenshot(args)

    async def determine_tonemapping(
        self,
        w_sar: float,
        h_sar: float,
        width: float,
        height: float,
        path: str,
        ss_time: str,
        image_path: str,
        loglevel: str,
        meta: Meta,
    ) -> bool:
        return await determine_tonemapping(
            w_sar,
            h_sar,
            width,
            height,
            path,
            ss_time,
            image_path,
            loglevel,
            meta,
        )

    async def valid_ss_time(
        self,
        ss_times: list[str],
        num_screens: int,
        length: float,
        frame_rate: float,
        meta: Meta,
        retake: bool = False,
    ) -> list[str]:
        return await valid_ss_time(
            ss_times, num_screens, length, frame_rate, meta, retake
        )

    async def get_frame_info(
        self, path: str, ss_time: str, meta: Meta
    ) -> dict[str, Any]:
        return await get_frame_info(path, ss_time, meta)

    async def check_libplacebo_compatibility(
        self,
        w_sar: float,
        h_sar: float,
        width: float,
        height: float,
        path: str,
        ss_time: str,
        image_path: str,
        loglevel: str,
        meta: Meta,
    ) -> tuple[bool, bool]:
        return await check_libplacebo_compatibility(
            w_sar,
            h_sar,
            width,
            height,
            path,
            ss_time,
            image_path,
            loglevel,
            meta,
        )

    async def libplacebo_warmup(
        self, path: str, meta: Meta, loglevel: str
    ) -> None:
        await libplacebo_warmup(path, meta, loglevel)

    async def get_image_host(self, meta: Meta) -> str | None:
        return await get_image_host(meta)
