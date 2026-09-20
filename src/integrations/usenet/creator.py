# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import aiofiles
import aiofiles.os
import aiofiles.ospath
import psutil
from rich.progress import BarColumn, TaskID, TaskProgressColumn, TextColumn

from src.domain_models.release import Meta
from src.integrations.observability.console import progress_display
from src.integrations.observability.runtime_support import console, logger


def generate_random_poster() -> str:
    """Generate a fully random poster name and email for Usenet anonymity."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    digits = "0123456789"

    first_len = 5 + secrets.randbelow(6)
    last_len = 5 + secrets.randbelow(6)
    user_len = 6 + secrets.randbelow(7)
    domain_len = 5 + secrets.randbelow(6)

    first = "".join(secrets.choice(letters) for _ in range(first_len))
    last = "".join(secrets.choice(letters) for _ in range(last_len))
    email_user = "".join(
        secrets.choice(letters + digits) for _ in range(user_len)
    )
    domain = "".join(secrets.choice(letters) for _ in range(domain_len))
    tld = secrets.choice(["com", "net", "org", "info", "biz", "xyz", "io"])

    return f"{first.capitalize()} {last.capitalize()} <{email_user}@{domain}.{tld}>"


def _safe_file_size(path: Path) -> int:
    if path.is_symlink():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def get_path_size(path: str) -> int:
    """Calculate the total size of a file or directory in bytes."""
    candidate = Path(path)
    if candidate.is_file():
        return _safe_file_size(candidate)
    return sum(
        _safe_file_size(Path(dirpath) / filename)
        for dirpath, _dirs, filenames in os.walk(path)
        for filename in filenames
    )


def get_dynamic_volume_size(total_bytes: int) -> str:
    """Determine a dynamic volume size based on the total size in bytes."""
    gb = 1024 * 1024 * 1024
    if total_bytes < 2 * gb:
        return "100m"
    if total_bytes < 10 * gb:
        return "200m"
    if total_bytes < 50 * gb:
        return "500m"
    return "1g"


def compute_nyuu_connections(
    total_connections: int,
    nyuu_check_enabled: bool,
    check_connections_cfg: str | int | None,
) -> tuple[int, int | None]:
    """Split the configured connection budget between nyuu posting and checking.

    Like pesto, nyuu checks concurrently with posting over its own connections,
    and throttles posting speed to match if the check connections can't keep up.
    So by default (check_connections_cfg empty), `total_connections` is split between posting
    and checking, keeping the combined socket count within what was configured
    instead of adding check connections on top of it. An explicit
    check_connections_cfg overrides this and posts at the full connection count,
    with that many additional connections dedicated to checking.

    Returns (post_connections, check_connections), where check_connections is
    None when checking is disabled.
    """
    if not nyuu_check_enabled:
        return total_connections, None

    if check_connections_cfg not in (None, ""):
        return total_connections, int(check_connections_cfg)

    post_connections = max(1, total_connections // 2)
    check_connections = max(1, total_connections - post_connections)
    return post_connections, check_connections


async def _managed_binary(
    binary_name: str, base_dir: str, path_7z: str | None
) -> str | None:
    if binary_name == "7z":
        from src.integrations.runtime_tools.seven_zip import (
            SevenZipBinaryManager,
        )

        return await SevenZipBinaryManager.ensure_7z_binary(base_dir)
    if binary_name == "nyuu":
        from src.integrations.runtime_tools.nyuu import NyuuBinaryManager

        return await NyuuBinaryManager.ensure_nyuu_binary(
            base_dir, path_7z=path_7z
        )
    if binary_name == "par2":
        from src.integrations.runtime_tools.par2 import Par2BinaryManager

        return await Par2BinaryManager.ensure_par2_binary(base_dir)
    if binary_name == "pesto":
        from src.integrations.runtime_tools.pesto import PestoBinaryManager

        return await PestoBinaryManager.ensure_pesto_binary(base_dir)
    return None


async def _automatic_binary(
    binary_name: str, meta: Meta | None, path_7z: str | None
) -> str | None:
    if meta is None or not meta.base_dir:
        return None
    try:
        return await _managed_binary(binary_name, meta.base_dir, path_7z)
    except Exception as error:
        logger.debug(
            f"[yellow]Automatic download of '{binary_name}' failed: {error}[/yellow]"
        )
        return None


async def check_binary(
    binary_name: str,
    config_path: str | None = None,
    meta: Meta | None = None,
    path_7z: str | None = None,
) -> str:
    """Ensure binary exists, returning the resolved path or raising FileNotFoundError."""
    path = config_path or binary_name
    resolved = shutil.which(path)
    if resolved:
        return resolved
    downloaded = await _automatic_binary(binary_name, meta, path_7z)
    if downloaded:
        return downloaded
    raise FileNotFoundError(
        f"Binary '{path}' not found in PATH or config. Please install it."
    )


_REDACTED_COMMAND_OPTIONS = {
    "-p",
    "-u",
    "--password",
    "--auth-password",
    "--nzb-password",
    "--username",
}


def _inline_password_arg(arg: str) -> str | None:
    if arg.startswith("-p") and len(arg) > 2 and not arg.startswith("-P"):
        return "-p********"
    return None


def _redacted_command(cmd: list[str]) -> str:
    redacted: list[str] = []
    index = 0
    while index < len(cmd):
        arg = cmd[index]
        inline = _inline_password_arg(arg)
        if inline is not None:
            redacted.append(inline)
            index += 1
            continue
        redacted.append(arg)
        if arg in _REDACTED_COMMAND_OPTIONS and index + 1 < len(cmd):
            redacted.append("********")
            index += 2
            continue
        index += 1
    return " ".join(redacted)


def _log_command_failure(
    description: str, returncode: int | None, stdout: bytes, stderr: bytes
) -> None:
    logger.error(
        f"[red]Error running {description} (exit code {returncode}):[/red]"
    )
    if stdout:
        logger.info(f"[red]STDOUT:[/red]\n{stdout.decode(errors='replace')}")
    if stderr:
        logger.info(f"[red]STDERR:[/red]\n{stderr.decode(errors='replace')}")


async def run_command_with_logging(cmd: list[str], description: str) -> None:
    """Execute a command asynchronously and log failures with redacted arguments."""
    redacted_str = _redacted_command(cmd)
    logger.debug(f"[cyan]Running command: {redacted_str}[/cyan]")
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return
        _log_command_failure(description, process.returncode, stdout, stderr)
        raise RuntimeError(
            f"Command '{redacted_str}' failed with exit code {process.returncode}"
        )
    except Exception as error:
        raise RuntimeError(
            f"Failed to execute command '{redacted_str}': {error}"
        ) from error


def format_byte_size(value: int) -> str:
    """Return a compact binary byte-size string for progress details."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    scaled_value = float(value)
    unit_index = 0
    while scaled_value >= 1024 and unit_index < len(units) - 1:
        scaled_value /= 1024
        unit_index += 1
    unit = units[unit_index]
    return f"{scaled_value:.1f} {unit}" if unit != "B" else f"{value} B"


@dataclass
class _SevenZipProgressState:
    reported_percent: float = 0.0
    bytes_written: int = 0
    bytes_read: int = 0
    archive_process: psutil.Process | None = None
    read_bytes_start: int | None = None


def _progress_percentage(text: str) -> float:
    values = [
        min(float(match.group(1)), 99.9)
        for match in re.finditer(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%", text)
    ]
    return max(values, default=0.0)


async def _consume_7z_progress(
    stream: asyncio.StreamReader | None, state: _SevenZipProgressState
) -> str:
    if stream is None:
        return ""
    chunks: list[bytes] = []
    buffer = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        buffer += chunk
        parts = re.split(rb"[\r\n]+", buffer)
        buffer = parts.pop()
        for part in parts:
            state.reported_percent = max(
                state.reported_percent,
                _progress_percentage(part.decode(errors="replace")),
            )
    state.reported_percent = max(
        state.reported_percent,
        _progress_percentage(buffer.decode(errors="replace")),
    )
    return b"".join(chunks).decode(errors="replace")


def _seven_zip_process_state(
    pid: int,
) -> tuple[psutil.Process | None, int | None]:
    try:
        process = psutil.Process(pid)
        return process, process.io_counters().read_bytes
    except psutil.AccessDenied, psutil.NoSuchProcess, OSError:
        return None, None


def _update_7z_read_bytes(state: _SevenZipProgressState) -> None:
    if state.archive_process is None or state.read_bytes_start is None:
        return
    try:
        state.bytes_read = max(
            state.archive_process.io_counters().read_bytes
            - state.read_bytes_start,
            0,
        )
    except psutil.AccessDenied, psutil.NoSuchProcess, OSError:
        state.archive_process = None


def _updated_archive_bytes(
    usenet_dir: str | Path, safe_name: str, previous: int
) -> int:
    try:
        prefix = f"{safe_name}.7z"
        return sum(
            entry.stat().st_size
            for entry in Path(usenet_dir).iterdir()
            if entry.is_file() and entry.name.startswith(prefix)
        )
    except OSError:
        return previous


def _seven_zip_progress_values(
    state: _SevenZipProgressState, progress_total: int, total_size: int
) -> tuple[float, str]:
    byte_percent = min((state.bytes_written / progress_total) * 100, 99.9)
    read_percent = min((state.bytes_read / progress_total) * 100, 99.9)
    observed = max(state.reported_percent, byte_percent, read_percent)
    processed = max(state.bytes_read, state.bytes_written)
    detail = f"{format_byte_size(processed)} / {format_byte_size(total_size)} processed"
    if state.reported_percent > max(byte_percent, read_percent):
        detail = f"{detail} | 7z: {state.reported_percent:.1f}%"
    return observed, detail


def _monitor_7z_progress(
    stop: Any,
    progress: Any,
    task: Any,
    state: _SevenZipProgressState,
    usenet_dir: str | Path,
    safe_name: str,
    progress_total: int,
    total_size: int,
) -> None:
    while not stop.is_set():
        _update_7z_read_bytes(state)
        state.bytes_written = _updated_archive_bytes(
            usenet_dir, safe_name, state.bytes_written
        )
        observed, detail = _seven_zip_progress_values(
            state, progress_total, total_size
        )
        progress.update(
            task,
            completed=observed,
            description=f"Archiving/Splitting with 7z | {detail}",
        )
        stop.wait(0.25)


def _seven_zip_progress_command(cmd: list[str]) -> list[str]:
    if "-bsp1" in cmd:
        return cmd
    return [cmd[0], cmd[1], "-bsp1", *cmd[2:]]


def _log_7z_failure(returncode: int | None, stdout: str, stderr: str) -> None:
    logger.error(
        f"[red]Error running 7z Archiver (exit code {returncode}):[/red]"
    )
    if stdout:
        logger.info(f"[red]STDOUT:[/red]\n{stdout}")
    if stderr:
        logger.info(f"[red]STDERR:[/red]\n{stderr}")


async def run_7z_with_progress(
    cmd: list[str],
    usenet_dir: str | Path,
    safe_name: str,
    _volume_size: str | None,
    total_size: int,
) -> None:
    """Execute 7z archiving/splitting with byte-based real-time progress monitoring."""
    redacted_str = _redacted_command(cmd)
    logger.debug(f"[cyan]Running command: {redacted_str}[/cyan]")
    try:
        process = await asyncio.create_subprocess_exec(
            *_seven_zip_progress_command(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        progress_total = max(total_size, 1)
        archive_process, read_start = _seven_zip_process_state(process.pid)
        state = _SevenZipProgressState(
            archive_process=archive_process, read_bytes_start=read_start
        )
        stdout_task = asyncio.create_task(
            _consume_7z_progress(process.stdout, state)
        )
        stderr_task = asyncio.create_task(
            _consume_7z_progress(process.stderr, state)
        )
        with progress_display(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=False,
            disable=False,
        ) as progress:
            task = progress.add_task(
                "Archiving/Splitting with 7z", total=progress_total
            )
            stop = threading.Event()
            monitor = threading.Thread(
                target=lambda: _monitor_7z_progress(
                    stop,
                    progress,
                    task,
                    state,
                    usenet_dir,
                    safe_name,
                    progress_total,
                    total_size,
                ),
                name="usenet-archive-progress",
                daemon=True,
            )
            monitor.start()
            try:
                await process.wait()
                stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            finally:
                stop.set()
                await asyncio.to_thread(monitor.join, 2)
            if process.returncode != 0:
                _log_7z_failure(process.returncode, stdout, stderr)
                raise RuntimeError(
                    f"Command '{redacted_str}' failed with exit code {process.returncode}"
                )
            progress.update(
                task, completed=progress_total, total=progress_total
            )
    except Exception as error:
        raise RuntimeError(
            f"Failed to execute command '{redacted_str}': {error}"
        ) from error


def _par2_action(line: str) -> str:
    rules = (
        ("Constructing", "Computing PAR2 recovery matrix"),
        ("Processing", "Computing PAR2"),
        ("Computing", "Computing PAR2"),
        ("Writing", "Writing PAR2"),
        ("Loading", "Loading PAR2"),
    )
    return next(
        (action for marker, action in rules if marker in line),
        "Generating PAR2",
    )


def _update_par2_progress(
    line: str, progress: Any, tasks: dict[str, TaskID]
) -> None:
    match = re.search(r"(\d+(?:\.\d+)?)%", line)
    if match is None:
        return
    percent = float(match.group(1))
    action = _par2_action(line)
    if "par2" not in tasks:
        tasks["par2"] = progress.add_task(action, total=100)
    progress.update(tasks["par2"], description=action, completed=percent)


def _update_par2_parts(
    buffer: bytes, progress: Any, tasks: dict[str, TaskID]
) -> bytes:
    parts = re.split(rb"[\r\n]+", buffer)
    remainder = parts.pop()
    for part in parts:
        _update_par2_progress(
            part.decode(errors="replace").strip(), progress, tasks
        )
    return remainder


async def _consume_par2_progress(
    stream: asyncio.StreamReader, progress: Any, tasks: dict[str, TaskID]
) -> str:
    chunks: list[str] = []
    buffer = b""
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk.decode(errors="replace"))
        buffer = _update_par2_parts(buffer + chunk, progress, tasks)
    _update_par2_progress(
        buffer.decode(errors="replace").strip(), progress, tasks
    )
    return "".join(chunks)


def _finish_par2_progress(progress: Any, tasks: dict[str, TaskID]) -> None:
    task = tasks.get("par2")
    if task is not None:
        progress.update(task, completed=100)


def _ensure_par2_success(
    returncode: int | None, stdout_str: str, redacted_str: str
) -> None:
    if returncode == 0:
        return
    logger.error(
        f"[red]Error running PAR2 Creator (exit code {returncode}):[/red]"
    )
    if stdout_str:
        logger.info(f"[red]OUTPUT:[/red]\n{stdout_str}")
    raise RuntimeError(
        f"Command '{redacted_str}' failed with exit code {returncode}"
    )


async def run_par2_with_progress(
    cmd: list[str], cwd: str | None = None
) -> None:
    """Execute par2 c with real-time percentage progress."""
    redacted_str = _redacted_command(cmd)
    cwd_str = f" in {cwd}" if cwd else ""
    logger.debug(f"[cyan]Running command: {redacted_str}{cwd_str}[/cyan]")
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        if process.stdout is None:
            raise RuntimeError("Process stdout is None")
        tasks: dict[str, TaskID] = {}
        with progress_display(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=False,
            disable=False,
        ) as progress:
            stdout_str = await _consume_par2_progress(
                process.stdout, progress, tasks
            )
            _finish_par2_progress(progress, tasks)
        await process.wait()
        _ensure_par2_success(process.returncode, stdout_str, redacted_str)
    except Exception as error:
        raise RuntimeError(
            f"Failed to execute command '{redacted_str}': {error}"
        ) from error


@dataclass
class _NyuuProgressState:
    total_articles: int = 0


_NYUU_TOTAL_RE = re.compile(r"Uploading (\d+) article\(s\)")
_NYUU_PROGRESS_RE = re.compile(
    r"Article posting progress: \d+ read, (\d+) posted(?:, (\d+) checked)?"
)


def _nyuu_progress_values(
    posted: int, checked_text: str | None, total: int
) -> tuple[float, str]:
    if checked_text is None:
        return posted / total * 100, "Posting to Usenet"
    checked = int(checked_text)
    percent = ((checked + posted) / 2) / total * 100
    description = (
        "Verifying articles on server"
        if posted >= total
        else "Posting & verifying to Usenet"
    )
    return percent, description


def _update_nyuu_progress(
    line: str,
    state: _NyuuProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    total_match = _NYUU_TOTAL_RE.search(line)
    if total_match is not None:
        state.total_articles = int(total_match.group(1))
    progress_match = _NYUU_PROGRESS_RE.search(line)
    if progress_match is None or not state.total_articles:
        return
    posted = int(progress_match.group(1))
    percent, description = _nyuu_progress_values(
        posted, progress_match.group(2), state.total_articles
    )
    if "upload" not in tasks:
        tasks["upload"] = progress.add_task(description, total=100)
    progress.update(
        tasks["upload"], description=description, completed=percent
    )


async def _consume_nyuu_output(
    stream: asyncio.StreamReader,
    state: _NyuuProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> str:
    lines: list[str] = []
    while True:
        raw = await stream.readline()
        if not raw:
            break
        line = raw.decode(errors="replace")
        lines.append(line)
        _update_nyuu_progress(line.strip(), state, progress, tasks)
    return "".join(lines)


def _finish_nyuu_progress(
    progress: Any, tasks: dict[str, TaskID], result: int
) -> None:
    task = tasks.get("upload")
    if result == 0 and task is not None:
        progress.update(task, completed=100)


def _ensure_nyuu_success(
    result: int, stdout_str: str, redacted_str: str
) -> None:
    if result == 0:
        return
    logger.error(
        f"[red]Error running Nyuu Uploader (exit code {result}):[/red]"
    )
    if stdout_str:
        logger.info(f"[red]OUTPUT:[/red]\n{stdout_str}")
    raise RuntimeError(
        f"Command '{redacted_str}' failed with exit code {result}"
    )


async def run_nyuu_with_progress(
    cmd: list[str], cwd: str | None = None
) -> None:
    """Execute nyuu upload and render its periodic log progress."""
    redacted_str = _redacted_command(cmd)
    cwd_str = f" in {cwd}" if cwd else ""
    logger.debug(f"[cyan]Running command: {redacted_str}{cwd_str}[/cyan]")
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        if process.stdout is None:
            raise RuntimeError("Process stdout is None")
        state = _NyuuProgressState()
        tasks: dict[str, TaskID] = {}
        with progress_display(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=False,
        ) as progress:
            stdout_str = await _consume_nyuu_output(
                process.stdout, state, progress, tasks
            )
            result = await process.wait()
            _finish_nyuu_progress(progress, tasks, result)
        _ensure_nyuu_success(result, stdout_str, redacted_str)
    except Exception as error:
        raise RuntimeError(
            f"Failed to execute command '{redacted_str}': {error}"
        ) from error


@dataclass
class _PestoProgressState:
    check_missing_count: int = 0
    check_checked_total: int = 0
    check_reposted_total: int = 0
    check_expected_total: int = 0


def _ensure_pesto_task(
    progress: Any,
    tasks: dict[str, TaskID],
    key: str,
    description: str,
    total: int | None,
) -> TaskID:
    if key not in tasks:
        tasks[key] = progress.add_task(description, total=total)
    return tasks[key]


def _pesto_segment_done(
    event: dict[str, Any],
    state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    task = _ensure_pesto_task(
        progress, tasks, "upload", "Posting to Usenet", 100
    )
    progress.update(task, completed=float(event.get("progress_pct", 0) or 0))
    state.check_expected_total = int(
        event.get("total_segments", state.check_expected_total)
        or state.check_expected_total
    )


def _pesto_status(
    event: dict[str, Any],
    _state: _PestoProgressState,
    _progress: Any,
    _tasks: dict[str, TaskID],
) -> None:
    text = str(event.get("text", "")).strip()
    if text:
        logger.debug(f"[cyan]{text}[/cyan]")


def _pesto_failed(
    event: dict[str, Any],
    _state: _PestoProgressState,
    _progress: Any,
    _tasks: dict[str, TaskID],
) -> None:
    logger.info(
        f"[red]Pesto segment failed: {event.get('description', 'unknown failure')}[/red]"
    )


def _pesto_par2_encode_started(
    event: dict[str, Any],
    _state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    total = int(event.get("input_slices", 0) or 0) or 1
    task = _ensure_pesto_task(
        progress, tasks, "par2_encode", "Calculating PAR2 parity", total
    )
    progress.update(task, total=total, completed=0)


def _pesto_par2_encode_progress(
    event: dict[str, Any],
    _state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    total = int(event.get("total", 0) or 0) or 1
    done = int(event.get("done", 0) or 0)
    task = _ensure_pesto_task(
        progress, tasks, "par2_encode", "Calculating PAR2 parity", total
    )
    progress.update(task, total=total, completed=done)


def _pesto_par2_write_started(
    event: dict[str, Any],
    _state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    total = int(event.get("total", 0) or 0) or 1
    task = _ensure_pesto_task(
        progress, tasks, "par2_write", "Writing PAR2 recovery files", total
    )
    progress.update(task, total=total, completed=0)


def _pesto_par2_slice_written(
    _event: dict[str, Any],
    _state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    task = _ensure_pesto_task(
        progress, tasks, "par2_write", "Writing PAR2 recovery files", 1
    )
    progress.advance(task)


def _pesto_check_progress(
    event: dict[str, Any],
    state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    checked = int(event.get("checked", 0) or 0)
    state.check_checked_total = checked
    if not bool(event.get("ok", True)):
        state.check_missing_count += 1
    total = state.check_expected_total or None
    task = _ensure_pesto_task(
        progress, tasks, "check", "Verifying articles on server", total
    )
    progress.update(task, total=total)
    description = "Verifying articles on server"
    if state.check_missing_count:
        description += f" ({state.check_missing_count} failed so far)"
    progress.update(task, description=description, completed=checked)


def _finish_pesto_check_task(
    state: _PestoProgressState, progress: Any, tasks: dict[str, TaskID]
) -> None:
    task = tasks.get("check")
    if task is None:
        return
    total = state.check_expected_total or state.check_checked_total or 1
    progress.update(task, total=total, completed=total)


def _log_pesto_check_done(failed: int) -> None:
    if failed:
        logger.info(
            f"[yellow]Article check: {failed} article(s) still missing after every repost attempt.[/yellow]"
        )
        return
    logger.info(
        "[green]Article check: all articles verified on server.[/green]"
    )


def _pesto_check_done(
    event: dict[str, Any],
    state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    failed = int(event.get("failed", 0) or 0)
    state.check_missing_count = failed
    _finish_pesto_check_task(state, progress, tasks)
    _log_pesto_check_done(failed)


def _pesto_check_retrying(
    event: dict[str, Any],
    _state: _PestoProgressState,
    _progress: Any,
    _tasks: dict[str, TaskID],
) -> None:
    logger.debug(
        "[cyan]Article check: retry "
        f"{event.get('attempt', 0)}/{event.get('max_attempts', 0)} in "
        f"{event.get('delay_secs', 0)}s ({event.get('reason', 'article not found')})...[/cyan]"
    )


def _pesto_check_reposted(
    event: dict[str, Any],
    state: _PestoProgressState,
    _progress: Any,
    _tasks: dict[str, TaskID],
) -> None:
    reposted = int(event.get("reposted", 0) or 0)
    state.check_reposted_total = reposted
    logger.debug(
        f"[yellow]Article check: {reposted} article(s) reposted so far.[/yellow]"
    )


_PESTO_EVENT_HANDLERS: dict[str, Any] = {
    "segment_done": _pesto_segment_done,
    "status": _pesto_status,
    "failed": _pesto_failed,
    "par2_encode_started": _pesto_par2_encode_started,
    "par2_encode_progress": _pesto_par2_encode_progress,
    "par2_write_started": _pesto_par2_write_started,
    "par2_slice_written": _pesto_par2_slice_written,
    "check_progress": _pesto_check_progress,
    "check_done": _pesto_check_done,
    "check_retrying": _pesto_check_retrying,
    "check_reposted": _pesto_check_reposted,
}


def _handle_pesto_event(
    event: dict[str, Any],
    state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    handler = _PESTO_EVENT_HANDLERS.get(str(event.get("type", "")))
    if handler is not None:
        handler(event, state, progress, tasks)


async def _drain_pesto_stderr(
    stream: asyncio.StreamReader, output: list[str]
) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        output.append(line.decode(errors="replace"))


def _parsed_pesto_event(line: str) -> dict[str, Any] | None:
    if not line:
        return None
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


async def _consume_pesto_events(
    stream: asyncio.StreamReader,
    state: _PestoProgressState,
    progress: Any,
    tasks: dict[str, TaskID],
) -> None:
    while True:
        raw = await stream.readline()
        if not raw:
            return
        event = _parsed_pesto_event(raw.decode(errors="replace").strip())
        if event is not None:
            _handle_pesto_event(event, state, progress, tasks)


def _raise_missing_pesto_articles(
    state: _PestoProgressState, stderr_str: str
) -> None:
    total = (
        state.check_expected_total
        or state.check_checked_total
        or state.check_missing_count
    )
    repost_note = (
        f" despite {state.check_reposted_total} repost attempt(s)"
        if state.check_reposted_total
        else ""
    )
    logger.error(
        f"[red]Pesto could not confirm {state.check_missing_count}/{total} article(s) on the server{repost_note} "
        "— the NZB is incomplete and will be discarded. Pesto already reposted and reverified these under "
        "fresh Message-IDs before giving up, so this usually means the provider itself is rejecting or "
        "dropping these specific articles (check account status, retention, or group access) rather than "
        "a one-off network blip.[/red]"
    )
    if stderr_str:
        logger.info(f"[red]STDERR:[/red]\n{stderr_str}")
    raise RuntimeError(
        f"Pesto upload failed: {state.check_missing_count}/{total} article(s) could not be confirmed on the server after every repost attempt."
    )


def _raise_generic_pesto_failure(
    result: int, stderr_str: str, redacted_str: str
) -> None:
    logger.error(
        f"[red]Error running Pesto Uploader (exit code {result}):[/red]"
    )
    if stderr_str:
        logger.info(f"[red]STDERR:[/red]\n{stderr_str}")
    raise RuntimeError(
        f"Command '{redacted_str}' failed with exit code {result}"
    )


def _raise_pesto_failure(
    result: int,
    state: _PestoProgressState,
    stderr_str: str,
    redacted_str: str,
) -> None:
    if state.check_missing_count:
        _raise_missing_pesto_articles(state, stderr_str)
    _raise_generic_pesto_failure(result, stderr_str, redacted_str)


def _finish_pesto_progress(
    progress: Any, tasks: dict[str, TaskID], result: int
) -> None:
    task = tasks.get("upload")
    if result == 0 and task is not None:
        progress.update(task, completed=100)


async def _spawn_pesto_process(
    cmd: list[str], cwd: str | None
) -> tuple[
    asyncio.subprocess.Process,
    asyncio.StreamReader,
    asyncio.StreamReader,
]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Process stdout or stderr is None")
    return process, process.stdout, process.stderr


async def run_pesto_with_progress(
    cmd: list[str], cwd: str | None = None
) -> None:
    """Execute pesto upload consuming its JSON event stream for progress reporting."""
    redacted_str = _redacted_command(cmd)
    cwd_str = f" in {cwd}" if cwd else ""
    logger.debug(f"[cyan]Running command: {redacted_str}{cwd_str}[/cyan]")
    try:
        process, stdout, stderr = await _spawn_pesto_process(cmd, cwd)
        state = _PestoProgressState()
        stderr_lines: list[str] = []
        stderr_task = asyncio.create_task(
            _drain_pesto_stderr(stderr, stderr_lines)
        )
        tasks: dict[str, TaskID] = {}
        with progress_display(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=False,
        ) as progress:
            await _consume_pesto_events(stdout, state, progress, tasks)
            await stderr_task
            result = await process.wait()
            _finish_pesto_progress(progress, tasks, result)
        if result != 0:
            _raise_pesto_failure(
                result, state, "".join(stderr_lines), redacted_str
            )
    except Exception as error:
        raise RuntimeError(
            f"Failed to execute command '{redacted_str}': {error}"
        ) from error


async def is_valid_nzb(path: str | Path) -> bool:
    """Check if an NZB file exists, is non-empty, and ends with proper XML/NZB closing tag."""
    if not await aiofiles.ospath.isfile(path):
        return False
    try:
        size = await aiofiles.ospath.getsize(path)
        if size < 100:
            return False
        async with aiofiles.open(path, "rb") as f:
            if size > 1024:
                await f.seek(size - 1024)
                chunk = await f.read(1024)
            else:
                chunk = await f.read()
            content_sample = chunk.decode("utf-8", errors="ignore").strip()
            return "</nzb>" in content_sample
    except Exception:
        return False


async def inject_nzb_password(nzb_path: str | Path, password: str) -> None:
    """Inject <meta type="password">password</meta> into the NZB's <head> section."""
    if not await aiofiles.ospath.exists(nzb_path):
        return
    try:
        async with aiofiles.open(
            nzb_path, encoding="utf-8", errors="ignore"
        ) as f:
            content = await f.read()

        head_match = re.search(r"<head\b[^>]*>", content, re.IGNORECASE)
        if head_match:
            idx = head_match.end()
            new_content = (
                content[:idx]
                + f'\n    <meta type="password">{password}</meta>'
                + content[idx:]
            )
        else:
            nzb_match = re.search(r"<nzb\b[^>]*>", content, re.IGNORECASE)
            if nzb_match:
                idx = nzb_match.end()
                inserted = f'\n  <head>\n    <meta type="password">{password}</meta>\n  </head>'
                new_content = content[:idx] + inserted + content[idx:]
            else:
                return

        async with aiofiles.open(nzb_path, "w", encoding="utf-8") as f:
            await f.write(new_content)
    except Exception as e:
        logger.error(f"[red]Error injecting password into NZB file: {e}[/red]")


async def verify_nzb_has_password(nzb_path: str) -> bool:
    """Verify that the NZB file contains a password tag inside the head section."""
    if not await aiofiles.ospath.isfile(nzb_path):
        return False
    with contextlib.suppress(Exception):
        # Read the first 4096 bytes (the head section is always at the beginning)
        async with aiofiles.open(
            nzb_path, encoding="utf-8", errors="ignore"
        ) as f:
            header_sample = await f.read(4096)

        head_match = re.search(
            r"<head\b[^>]*>(.*?)</head>",
            header_sample,
            re.IGNORECASE | re.DOTALL,
        )
        if head_match:
            head_content = head_match.group(1)
            if re.search(
                r'<meta\s+type=["\']password["\']', head_content, re.IGNORECASE
            ):
                return True
    return False


@dataclass(frozen=True)
class _UsenetNames:
    base_dir: str
    input_path: str
    uuid: str
    name: str
    safe_name: str
    safe_nzb_name: str
    archive_name: str
    archive_password: str | None


@dataclass(frozen=True)
class _UsenetBinaries:
    seven_zip: str | None
    par2: str | None
    nyuu: str | None
    pesto: str | None
    uploader: str
    use_pesto: bool


@dataclass
class _UsenetUploadState:
    usenet_dir: Path
    upload_root: Path
    cleanup_upload_root: bool
    upload_files: list[Path]
    skip_archive: bool
    use_prepared_files: bool
    volume_size: str | None
    par2_percentage: str
    total_size: int


@dataclass(frozen=True)
class _UsenetPostIdentity:
    poster: str
    subject: str
    random_poster: bool
    obscure_subject: bool
    custom_subject: str


def _usenet_config(config: dict[str, Any]) -> dict[str, Any] | None:
    value = config.get("USENET", {})
    if not isinstance(value, dict) or not value:
        logger.error(
            "[red]Error: USENET section is missing from configuration.[/red]"
        )
        return None
    return cast(dict[str, Any], value)


def _generated_archive_password() -> str:
    while True:
        value = secrets.token_urlsafe(16)
        if not value.startswith("-"):
            return value


def _archive_password_mode(meta: Meta, configured: Any) -> bool:
    configured_random = str(configured).lower() == "random"
    stored = meta.usenet_archive_password_is_random
    return bool(stored if stored is not None else configured_random)


def _resolved_archive_password_value(value: Any, random_mode: bool) -> str:
    if random_mode and str(value).lower() == "random":
        logger.debug(
            "[cyan]Generated a random Usenet archive password for this upload.[/cyan]"
        )
        return _generated_archive_password()
    if random_mode:
        logger.debug(
            "[cyan]Reusing the random Usenet archive password prepared for this upload.[/cyan]"
        )
        return str(value)
    logger.info(
        "[cyan]Using configured static password for Usenet archive encryption.[/cyan]"
    )
    return str(value)


def _archive_password(meta: Meta, usenet_cfg: dict[str, Any]) -> str | None:
    configured = usenet_cfg.get("archive_password")
    random_mode = _archive_password_mode(meta, configured)
    value = meta.archive_password or configured
    if not value:
        return None
    resolved = _resolved_archive_password_value(value, random_mode)
    meta.archive_password = resolved
    meta.usenet_archive_password_is_random = random_mode
    return resolved


def _safe_usenet_uuid(value: str) -> str:
    clean = "".join(
        character
        for character in value
        if character.isalnum() or character in "._-"
    )[:30]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{clean}_{digest}" if clean else digest


def _safe_usenet_name(value: str) -> str:
    return "".join(
        character
        for character in value
        if character.isalnum() or character in "._- "
    ).replace(" ", ".")


def _safe_nzb_name(safe_name: str, input_path: str) -> str:
    if safe_name:
        return safe_name
    path = Path(input_path)
    return _safe_usenet_name(path.name) if path.is_dir() else safe_name


def _archive_name(meta: Meta, safe_name: str, password: str | None) -> str:
    existing = str(meta.get("usenet_archive_name") or safe_name)
    if not password or meta.get("usenet_archive_name"):
        return existing
    value = secrets.token_hex(16)
    meta.usenet_archive_name = value
    logger.debug(f"[cyan]Obfuscating archive filenames to: {value}[/cyan]")
    return value


def _usenet_names(
    meta: Meta, usenet_cfg: dict[str, Any]
) -> _UsenetNames | None:
    input_path = str(meta.path or "")
    if not input_path:
        logger.error("[red]Error: Input path is missing.[/red]")
        return None
    safe_name = _safe_usenet_name(str(meta.basename_no_ext or ""))
    password = _archive_password(meta, usenet_cfg)
    return _UsenetNames(
        base_dir=str(meta.base_dir),
        input_path=input_path,
        uuid=_safe_usenet_uuid(str(meta.uuid)),
        name=str(meta.basename_no_ext or ""),
        safe_name=safe_name,
        safe_nzb_name=_safe_nzb_name(safe_name, input_path),
        archive_name=_archive_name(meta, safe_name, password),
        archive_password=password,
    )


def _default_usenet_tmp(names: _UsenetNames) -> Path:
    return Path(names.base_dir) / "tmp" / Path(names.input_path).name


def _nzb_output_dir(names: _UsenetNames, usenet_cfg: dict[str, Any]) -> Path:
    fallback = _default_usenet_tmp(names)
    requested = usenet_cfg.get("nzb_output_dir")
    target = Path(str(requested)) if requested else fallback
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Could not create nzb_output_dir '{target}' ({error}). Falling back to default tmp dir.[/yellow]"
        )
        with contextlib.suppress(Exception):
            fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _usenet_tmp_base(names: _UsenetNames, usenet_cfg: dict[str, Any]) -> Path:
    fallback = _default_usenet_tmp(names)
    requested = usenet_cfg.get("usenet_tmp_dir")
    if not requested:
        return fallback
    target = Path(str(requested))
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception as error:
        logger.warning(
            f"[yellow]Warning: Could not create usenet_tmp_dir '{target}' ({error}). Falling back to default tmp dir.[/yellow]"
        )
        return fallback


async def _existing_usenet_nzb(
    final_path: Path, staged_path: Path
) -> str | None:
    for candidate in (final_path, staged_path):
        if await is_valid_nzb(candidate):
            return str(candidate)
    return None


async def _resolved_stage_binary(
    name: str,
    configured: Any,
    meta: Meta,
    *,
    path_7z: str | None = None,
) -> tuple[bool, str | None]:
    try:
        path = await check_binary(
            name,
            str(configured) if configured else None,
            meta=meta,
            path_7z=path_7z,
        )
        return True, path
    except FileNotFoundError as error:
        if meta.debug:
            logger.warning(
                f"[yellow]Warning: {error} Using simulation mode for {name}.[/yellow]"
            )
            return True, None
        logger.info(f"[bold red]Configuration Error: {error}[/bold red]")
        return False, None


async def _pesto_binaries(
    meta: Meta,
    usenet_cfg: dict[str, Any],
    prepare_only: bool,
    seven_zip: str | None,
    uploader: str,
) -> _UsenetBinaries | None:
    pesto: str | None = None
    if not prepare_only:
        ok, pesto = await _resolved_stage_binary(
            "pesto", usenet_cfg.get("pesto_path"), meta
        )
        if not ok:
            return None
    return _UsenetBinaries(seven_zip, None, None, pesto, uploader, True)


async def _nyuu_binaries(
    meta: Meta,
    usenet_cfg: dict[str, Any],
    prepare_only: bool,
    seven_zip: str | None,
    uploader: str,
) -> _UsenetBinaries | None:
    ok, par2 = await _resolved_stage_binary(
        "par2", usenet_cfg.get("par2_path"), meta
    )
    if not ok:
        return None
    nyuu: str | None = None
    if not prepare_only:
        ok, nyuu = await _resolved_stage_binary(
            "nyuu", usenet_cfg.get("nyuu_path"), meta, path_7z=seven_zip
        )
        if not ok:
            return None
    return _UsenetBinaries(seven_zip, par2, nyuu, None, uploader, False)


async def _usenet_binaries(
    meta: Meta, usenet_cfg: dict[str, Any], prepare_only: bool
) -> _UsenetBinaries | None:
    uploader = str(usenet_cfg.get("usenet_uploader", "nyuu")).lower()
    ok, seven_zip = await _resolved_stage_binary(
        "7z", usenet_cfg.get("7z_path"), meta
    )
    if not ok:
        return None
    if uploader == "pesto":
        return await _pesto_binaries(
            meta, usenet_cfg, prepare_only, seven_zip, uploader
        )
    return await _nyuu_binaries(
        meta, usenet_cfg, prepare_only, seven_zip, uploader
    )


def _prepared_file_values(meta: Meta) -> list[Any] | None:
    value = meta.get("usenet_prepared_files", [])
    if not isinstance(value, list) or not value:
        return None
    return cast(list[Any], value)


def _existing_prepared_paths(values: list[Any]) -> list[Path] | None:
    paths = [Path(str(item)) for item in values]
    if not paths:
        return None
    return paths if all(path.is_file() for path in paths) else None


def _prepared_upload_files(
    meta: Meta, skip_archive: bool
) -> list[Path] | None:
    if skip_archive:
        return None
    values = _prepared_file_values(meta)
    return _existing_prepared_paths(values) if values is not None else None


async def _debug_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "wb") as handle:
        await handle.write(content)


def _warn_skip_archive_password(password: str | None) -> None:
    if not password:
        return
    logger.warning(
        "[yellow]Warning: 'archive_password' is set but 'skip_archive' is enabled — no archive "
        "will be created, so this upload will proceed as if no password were configured (the "
        "password is only meaningful when compressing into a 7z/rar).[/yellow]"
    )


async def _skip_archive_file_state(
    source: Path, usenet_dir: Path, meta: Meta
) -> tuple[Path, bool, list[Path]]:
    if meta.debug and not await aiofiles.ospath.exists(source):
        staged = usenet_dir / source.name
        await _debug_file(staged, b"mock single file content")
        return usenet_dir, True, [staged]
    return source.parent, False, [source]


async def _skip_archive_state(
    names: _UsenetNames, usenet_dir: Path, meta: Meta
) -> tuple[Path, bool, list[Path]]:
    _warn_skip_archive_password(names.archive_password)
    logger.info(
        "[cyan]Skipping archive step; uploading directly from source files...[/cyan]"
    )
    source = Path(names.input_path)
    if await aiofiles.ospath.isdir(names.input_path):
        files = [path for path in sorted(source.rglob("*")) if path.is_file()]
        return source, False, files
    return await _skip_archive_file_state(source, usenet_dir, meta)


def _resolved_volume_size(
    value: Any, total_size: int, debug: bool
) -> str | None:
    if value is None or value == "":
        return None
    volume = str(value)
    if volume.lower() != "auto":
        return volume
    dynamic = get_dynamic_volume_size(total_size)
    if debug:
        logger.info(
            f"[cyan]Dynamic volume size chosen based on upload size ({total_size / (1024**3):.2f} GB): {dynamic.upper()}[/cyan]"
        )
    return dynamic


def _remove_stale_archive_parts(usenet_dir: Path, archive_name: str) -> None:
    pattern = re.compile(rf"{re.escape(archive_name)}\.7z(?:\.\d+)?")
    for candidate in usenet_dir.iterdir():
        if candidate.is_file() and pattern.fullmatch(candidate.name):
            candidate.unlink()


def _seven_zip_command(
    binary: str | None,
    archive_out: Path,
    input_path: str,
    volume_size: str | None,
    password: str | None,
) -> list[str]:
    command = [binary or "7z", "a", "-mx=0"]
    if volume_size:
        command.append(f"-v{volume_size.lower()}")
    if password:
        command.extend([f"-p{password}", "-mhe=on"])
    command.extend([str(archive_out), input_path])
    return command


async def _simulate_or_run_archive(
    names: _UsenetNames,
    usenet_dir: Path,
    meta: Meta,
    binaries: _UsenetBinaries,
    volume_size: str | None,
    total_size: int,
    archive_out: Path,
) -> list[Path]:
    _remove_stale_archive_parts(usenet_dir, names.archive_name)
    command = _seven_zip_command(
        binaries.seven_zip,
        archive_out,
        names.input_path,
        volume_size,
        names.archive_password,
    )
    if meta.debug and not binaries.seven_zip:
        logger.info(
            f"[yellow][DEBUG SIMULATION] Would run: {_redacted_command(command)}[/yellow]"
        )
        mock = Path(f"{archive_out}.001") if volume_size else archive_out
        await _debug_file(mock, b"mock 7z volume content")
        return [mock]
    await run_7z_with_progress(
        command, usenet_dir, names.archive_name, volume_size, total_size
    )
    return []


async def _copy_single_upload(
    names: _UsenetNames, usenet_dir: Path, meta: Meta
) -> list[Path]:
    logger.info("[cyan]Copying single file for upload...[/cyan]")
    destination = usenet_dir / Path(names.input_path).name
    if meta.debug and not await aiofiles.ospath.exists(names.input_path):
        logger.info(
            f"[yellow][DEBUG SIMULATION] Input path '{names.input_path}' doesn't exist, writing dummy file to '{destination}'[/yellow]"
        )
        await _debug_file(destination, b"mock single file content")
    else:
        await asyncio.to_thread(shutil.copy, names.input_path, destination)
    return [destination]


async def _archive_or_copy(
    names: _UsenetNames,
    usenet_dir: Path,
    meta: Meta,
    binaries: _UsenetBinaries,
    volume_size: str | None,
    total_size: int,
) -> list[Path]:
    archive_out = usenet_dir / f"{names.archive_name}.7z"
    needs_archive = bool(
        await aiofiles.ospath.isdir(names.input_path)
        or volume_size
        or names.archive_password
    )
    if needs_archive:
        return await _simulate_or_run_archive(
            names,
            usenet_dir,
            meta,
            binaries,
            volume_size,
            total_size,
            archive_out,
        )
    return await _copy_single_upload(names, usenet_dir, meta)


def _collected_upload_files(root: Path) -> list[Path]:
    return [path for path in sorted(root.rglob("*")) if path.is_file()]


async def _prepared_usenet_upload(
    names: _UsenetNames,
    usenet_dir: Path,
    meta: Meta,
    usenet_cfg: dict[str, Any],
    binaries: _UsenetBinaries,
) -> _UsenetUploadState:
    skip_archive = bool(usenet_cfg.get("skip_archive", False))
    total_size = await asyncio.to_thread(get_path_size, names.input_path)
    prepared = _prepared_upload_files(meta, skip_archive)
    if prepared is not None:
        logger.debug(
            "[cyan]Reusing prepared Usenet archive and PAR2 files.[/cyan]"
        )
        upload_root, cleanup, files = usenet_dir, True, prepared
        use_prepared = True
        volume_size = _resolved_volume_size(
            usenet_cfg.get("rar_volume_size"), total_size, meta.debug
        )
    elif skip_archive:
        upload_root, cleanup, files = await _skip_archive_state(
            names, usenet_dir, meta
        )
        use_prepared = False
        volume_size = None
    else:
        volume_size = _resolved_volume_size(
            usenet_cfg.get("rar_volume_size"), total_size, meta.debug
        )
        upload_root, cleanup = usenet_dir, True
        files = await _archive_or_copy(
            names, usenet_dir, meta, binaries, volume_size, total_size
        )
        use_prepared = False
    if not files:
        files = _collected_upload_files(upload_root)
    return _UsenetUploadState(
        usenet_dir=usenet_dir,
        upload_root=upload_root,
        cleanup_upload_root=cleanup,
        upload_files=files,
        skip_archive=skip_archive,
        use_prepared_files=use_prepared,
        volume_size=volume_size,
        par2_percentage=str(usenet_cfg.get("par2_percentage", "10")),
        total_size=total_size,
    )


def _par2_target_files(state: _UsenetUploadState) -> list[Path]:
    return [
        path
        for path in state.upload_files
        if path.is_file() and not path.name.endswith(".par2")
    ]


def _par2_output_dir(state: _UsenetUploadState) -> Path:
    return state.usenet_dir if state.skip_archive else state.upload_root


def _par2_command(
    state: _UsenetUploadState,
    binaries: _UsenetBinaries,
    targets: list[Path],
    par2_file: Path,
) -> list[str]:
    relative = [str(path.relative_to(state.upload_root)) for path in targets]
    return [
        binaries.par2 or "par2",
        "c",
        f"-r{state.par2_percentage}",
        f"-B{state.upload_root}",
        str(par2_file),
        *relative,
    ]


async def _execute_par2_generation(
    command: list[str],
    par2_file: Path,
    state: _UsenetUploadState,
    meta: Meta,
    binaries: _UsenetBinaries,
) -> None:
    if meta.debug and not binaries.par2:
        logger.info(
            f"[yellow][DEBUG SIMULATION] Would run: {_redacted_command(command)}[/yellow]"
        )
        await _debug_file(par2_file, b"mock par2 content")
        return
    await run_par2_with_progress(command, cwd=str(state.upload_root))


def _append_generated_par2(
    state: _UsenetUploadState, output_dir: Path, archive_name: str
) -> None:
    generated = [
        path
        for path in sorted(output_dir.glob(f"{archive_name}.par2*"))
        if path.is_file()
    ]
    state.upload_files.extend(
        path for path in generated if path not in state.upload_files
    )


async def _generate_usenet_par2(
    state: _UsenetUploadState,
    names: _UsenetNames,
    meta: Meta,
    binaries: _UsenetBinaries,
) -> None:
    if binaries.use_pesto or state.use_prepared_files:
        return
    targets = _par2_target_files(state)
    if not targets:
        return
    logger.debug("[cyan]Generating PAR2 parity files...[/cyan]")
    output_dir = _par2_output_dir(state)
    par2_file = output_dir / f"{names.archive_name}.par2"
    command = _par2_command(state, binaries, targets, par2_file)
    await _execute_par2_generation(command, par2_file, state, meta, binaries)
    _append_generated_par2(state, output_dir, names.archive_name)


def _save_prepared_usenet_files(meta: Meta, state: _UsenetUploadState) -> None:
    if state.skip_archive:
        return
    meta.usenet_prepared_files = [str(path) for path in state.upload_files]


def _poster_identity(
    meta: Meta, usenet_cfg: dict[str, Any]
) -> tuple[str, bool]:
    random_poster = bool(usenet_cfg.get("random_poster", True))
    poster = str(usenet_cfg.get("poster", "Uploader <upload@assistant.org>"))
    if not random_poster:
        return poster, False
    generated = generate_random_poster()
    if meta.debug:
        logger.info(
            f"[cyan]Generated anonymous poster: {generated.split('<')[0].strip()}[/cyan]"
        )
    return generated, True


def _post_subject(
    meta: Meta, usenet_cfg: dict[str, Any], name: str
) -> tuple[str, bool, str]:
    obscure = bool(usenet_cfg.get("obscure_subject", True))
    custom = str(meta.usenet_subject or "")
    if custom:
        return custom, obscure, custom
    if not obscure:
        return name, obscure, custom
    subject = secrets.token_hex(16)
    if meta.debug:
        logger.info(f"[cyan]Obfuscating post subject: {subject}[/cyan]")
    return subject, obscure, custom


def _post_identity(
    meta: Meta, usenet_cfg: dict[str, Any], name: str
) -> _UsenetPostIdentity:
    poster, random_poster = _poster_identity(meta, usenet_cfg)
    subject, obscure, custom = _post_subject(meta, usenet_cfg, name)
    return _UsenetPostIdentity(poster, subject, random_poster, obscure, custom)


def _upload_file_arguments(state: _UsenetUploadState) -> list[str]:
    values: list[str] = []
    for path in state.upload_files:
        if not path.is_file():
            continue
        try:
            values.append(str(path.relative_to(state.upload_root)))
        except ValueError:
            values.append(str(path))
    return values


def _mock_nzb_content(password: str | None) -> str:
    password_tag = (
        f'  <meta type="password">{password}</meta>\n' if password else ""
    )
    return (
        '<?xml version="1.0" encoding="utf-8" ?>\n'
        '<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">\n'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
        "  <!-- Mock NZB file generated in debug/simulation mode -->\n"
        f"  <head>\n{password_tag}  </head>\n"
        '  <meta type="title">Mock Upload</meta>\n'
        "</nzb>\n"
    )


async def _cleanup_usenet_upload(
    state: _UsenetUploadState, debug: bool
) -> None:
    cleanup_path = (
        state.upload_root if state.cleanup_upload_root else state.usenet_dir
    )
    try:
        if not await aiofiles.ospath.exists(cleanup_path):
            return
        if debug:
            logger.info(
                f"[cyan][DEBUG SIMULATION] Would delete temporary Usenet folder: {cleanup_path}[/cyan]"
            )
            return
        await asyncio.to_thread(shutil.rmtree, cleanup_path)
        logger.info(
            "[green]Cleaned up temporary compressed Usenet files.[/green]"
        )
    except OSError as error:
        logger.warning(
            f"[yellow]Warning: Could not clean up temporary Usenet folder '{cleanup_path}' ({error})[/yellow]"
        )


def _positive_external_id(value: Any) -> bool:
    return bool(value and str(value).isdigit() and int(value) > 0)


def _tmdb_external_value(meta: Meta) -> str:
    tmdb_type = str(meta.category or "").lower()
    if tmdb_type not in {"movie", "tv"}:
        return ""
    if not _positive_external_id(meta.tmdb_id):
        return ""
    return f"{tmdb_type}/{meta.tmdb_id}"


def _external_id_values(meta: Meta) -> dict[str, str]:
    values: dict[str, str] = {}
    tmdb_value = _tmdb_external_value(meta)
    if tmdb_value:
        values["tmdb"] = tmdb_value
    if meta.imdb_tt:
        values["imdb"] = str(meta.imdb_tt)
    if _positive_external_id(meta.tvdb_id):
        values["tvdb"] = str(meta.tvdb_id)
    if _positive_external_id(meta.mal_id):
        values["mal"] = str(meta.mal_id)
    return values


def _pesto_external_ids(meta: Meta) -> list[str]:
    flags = {
        "tmdb": "--tmdb",
        "imdb": "--imdb-id",
        "tvdb": "--tvdb-id",
        "mal": "--mal-id",
    }
    command: list[str] = []
    for key, value in _external_id_values(meta).items():
        command.extend([flags[key], value])
    return command


def _append_nonempty_option(
    command: list[str], usenet_cfg: dict[str, Any], key: str, flag: str
) -> None:
    value = usenet_cfg.get(key)
    if value is not None and str(value).strip() != "":
        command.extend([flag, str(value)])


def _pesto_check_options(usenet_cfg: dict[str, Any]) -> list[str]:
    if not usenet_cfg.get("pesto_check", True):
        return ["--no-check"]
    command = ["--check"]
    _append_nonempty_option(
        command, usenet_cfg, "pesto_check_delay", "--check-delay"
    )
    _append_nonempty_option(
        command, usenet_cfg, "pesto_check_retries", "--check-retries"
    )
    _append_nonempty_option(
        command, usenet_cfg, "pesto_check_connections", "--check-connections"
    )
    post_retries = usenet_cfg.get("pesto_check_post_retries", 3)
    if str(post_retries).strip() != "":
        command.extend(["--check-post-retries", str(post_retries)])
    return command


def _pesto_transport_options(
    usenet_cfg: dict[str, Any], identity: _UsenetPostIdentity
) -> list[str]:
    options: list[str] = []
    if not usenet_cfg.get("ssl", True):
        options.append("--no-ssl")
    if not identity.random_poster:
        options.extend(["-f", identity.poster])
    return options


def _pesto_privacy_options(
    state: _UsenetUploadState,
    names: _UsenetNames,
    identity: _UsenetPostIdentity,
) -> list[str]:
    options: list[str] = []
    if identity.obscure_subject and not identity.custom_subject:
        options.append("--obfuscate=full")
    if names.archive_password and not state.skip_archive:
        options.extend(["--nzb-password", names.archive_password])
    return options


def _pesto_optional_options(
    usenet_cfg: dict[str, Any],
    state: _UsenetUploadState,
    names: _UsenetNames,
    identity: _UsenetPostIdentity,
) -> list[str]:
    return [
        *_pesto_transport_options(usenet_cfg, identity),
        *_pesto_privacy_options(state, names, identity),
    ]


def _pesto_command(
    meta: Meta,
    usenet_cfg: dict[str, Any],
    binaries: _UsenetBinaries,
    state: _UsenetUploadState,
    names: _UsenetNames,
    identity: _UsenetPostIdentity,
    nzb_file: Path,
    upload_files: list[str],
) -> list[str]:
    command = [
        binaries.pesto or "pesto",
        "-s",
        str(usenet_cfg.get("host", "")),
        "-P",
        str(usenet_cfg.get("port", 563)),
        "-u",
        str(usenet_cfg.get("username", "")),
        "-p",
        str(usenet_cfg.get("password", "")),
        "-n",
        str(usenet_cfg.get("connections", 20)),
        "-g",
        str(usenet_cfg.get("newsgroups", "")),
        "--out",
        str(nzb_file),
        "--par2",
        state.par2_percentage,
        "--output-format",
        "json",
        "--no-hooks",
    ]
    command.extend(_pesto_optional_options(usenet_cfg, state, names, identity))
    command.extend(_pesto_external_ids(meta))
    command.extend(_pesto_check_options(usenet_cfg))
    command.extend(upload_files)
    return command


def _nyuu_external_ids(meta: Meta) -> list[str]:
    labels = {
        "tmdb": "tmdbid",
        "imdb": "imdbid",
        "tvdb": "tvdbid",
        "mal": "malid",
    }
    command: list[str] = []
    for key, value in _external_id_values(meta).items():
        command.extend(["-M", f"{labels[key]}: {value}"])
    return command


def _nyuu_check_options(usenet_cfg: dict[str, Any]) -> tuple[int, list[str]]:
    total = int(usenet_cfg.get("connections", 20))
    enabled = bool(usenet_cfg.get("nyuu_check", True))
    post_connections, check_connections = compute_nyuu_connections(
        total, enabled, usenet_cfg.get("nyuu_check_connections")
    )
    if not enabled:
        return post_connections, []
    options = ["--check-connections", str(check_connections)]
    _append_nonempty_option(
        options, usenet_cfg, "nyuu_check_delay", "--check-delay"
    )
    _append_nonempty_option(
        options, usenet_cfg, "nyuu_check_retries", "--check-tries"
    )
    return post_connections, options


def _nyuu_command(
    meta: Meta,
    usenet_cfg: dict[str, Any],
    binaries: _UsenetBinaries,
    identity: _UsenetPostIdentity,
    nzb_file: Path,
    upload_files: list[str],
) -> list[str]:
    post_connections, check_options = _nyuu_check_options(usenet_cfg)
    command = [
        binaries.nyuu or "nyuu",
        "-h",
        str(usenet_cfg.get("host", "")),
        "-P",
        str(usenet_cfg.get("port", 563)),
        "-u",
        str(usenet_cfg.get("username", "")),
        "-p",
        str(usenet_cfg.get("password", "")),
        "-n",
        str(post_connections),
        "-g",
        str(usenet_cfg.get("newsgroups", "")),
        "-f",
        identity.poster,
        "-s",
        identity.subject,
        "-o",
        str(nzb_file),
        "--progress",
        "log:2s",
    ]
    if usenet_cfg.get("ssl", True):
        command.append("-S")
    if identity.obscure_subject and not identity.custom_subject:
        command.extend(["--filename", "${rand(16)}"])
    command.extend(check_options)
    command.extend(_nyuu_external_ids(meta))
    command.extend(upload_files)
    return command


async def _write_mock_nzb(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(content)


async def _remove_partial_nzb(path: Path) -> None:
    with contextlib.suppress(Exception):
        if await aiofiles.ospath.exists(path):
            await aiofiles.os.remove(path)


async def _run_pesto_upload(
    command: list[str],
    nzb_file: Path,
    state: _UsenetUploadState,
    meta: Meta,
    mock_content: str,
) -> None:
    if meta.debug:
        logger.info(
            f"[yellow][DEBUG SIMULATION] Would run Pesto upload: {_redacted_command(command)}[/yellow]"
        )
        await _write_mock_nzb(nzb_file, mock_content)
        return
    try:
        await run_pesto_with_progress(command, cwd=str(state.upload_root))
    except Exception:
        await _remove_partial_nzb(nzb_file)
        await _cleanup_usenet_upload(state, meta.debug)
        raise


async def _execute_nyuu_command(
    command: list[str],
    nzb_file: Path,
    state: _UsenetUploadState,
    meta: Meta,
    mock_content: str,
) -> None:
    if meta.debug:
        logger.info(
            f"[yellow][DEBUG SIMULATION] Would run Nyuu upload: {_redacted_command(command)}[/yellow]"
        )
        await _write_mock_nzb(nzb_file, mock_content)
        return
    try:
        await run_nyuu_with_progress(command, cwd=str(state.upload_root))
    except Exception:
        await _remove_partial_nzb(nzb_file)
        await _cleanup_usenet_upload(state, meta.debug)
        raise


async def _inject_nyuu_archive_password(
    nzb_file: Path, state: _UsenetUploadState, names: _UsenetNames
) -> None:
    if not names.archive_password or state.skip_archive:
        return
    if not await aiofiles.ospath.exists(nzb_file):
        return
    logger.info("[cyan]Injecting password into NZB metadata...[/cyan]")
    await inject_nzb_password(nzb_file, names.archive_password)


async def _run_nyuu_upload(
    command: list[str],
    nzb_file: Path,
    state: _UsenetUploadState,
    names: _UsenetNames,
    meta: Meta,
    mock_content: str,
) -> None:
    await _execute_nyuu_command(command, nzb_file, state, meta, mock_content)
    await _inject_nyuu_archive_password(nzb_file, state, names)


async def _post_usenet(
    meta: Meta,
    usenet_cfg: dict[str, Any],
    binaries: _UsenetBinaries,
    state: _UsenetUploadState,
    names: _UsenetNames,
    identity: _UsenetPostIdentity,
    nzb_file: Path,
) -> None:
    upload_files = _upload_file_arguments(state)
    logger.debug(
        f"[yellow]Posting {len(upload_files)} files to Usenet via NNTP ({binaries.uploader})...[/yellow]"
    )
    mock = _mock_nzb_content(names.archive_password)
    if binaries.use_pesto:
        command = _pesto_command(
            meta,
            usenet_cfg,
            binaries,
            state,
            names,
            identity,
            nzb_file,
            upload_files,
        )
        await _run_pesto_upload(command, nzb_file, state, meta, mock)
        return
    command = _nyuu_command(
        meta, usenet_cfg, binaries, identity, nzb_file, upload_files
    )
    await _run_nyuu_upload(command, nzb_file, state, names, meta, mock)


async def _relocate_usenet_nzb(
    nzb_file: Path, final_path: Path, debug: bool
) -> Path:
    if not await aiofiles.ospath.exists(nzb_file):
        return final_path
    try:
        await asyncio.to_thread(shutil.move, nzb_file, final_path)
        if debug:
            logger.info(
                f"[bold green]NZB file saved to: {final_path}[/bold green]"
            )
        return final_path
    except Exception as error:
        logger.error(
            f"[red]Error moving NZB file to final destination: {error}[/red]"
        )
        return nzb_file


async def _cleanup_usenet_uuid(tmp_base: Path, uuid: str, debug: bool) -> None:
    directory = tmp_base / uuid
    with contextlib.suppress(Exception):
        if debug or not await aiofiles.ospath.exists(directory):
            return
        if [path.name for path in directory.iterdir()]:
            return
        await asyncio.to_thread(os.rmdir, directory)


def _required_usenet_setup(
    meta: Meta, config: dict[str, Any]
) -> tuple[dict[str, Any], _UsenetNames] | None:
    usenet_cfg = _usenet_config(config)
    if usenet_cfg is None:
        return None
    names = _usenet_names(meta, usenet_cfg)
    if names is None:
        return None
    return usenet_cfg, names


async def prepare_and_upload_usenet(
    meta: Meta, config: dict[str, Any], *, prepare_only: bool = False
) -> str | None:
    """Prepare Usenet payloads and optionally post them via Nyuu or Pesto."""
    setup = _required_usenet_setup(meta, config)
    if setup is None:
        return None
    usenet_cfg, names = setup
    output_dir = _nzb_output_dir(names, usenet_cfg)
    tmp_base = _usenet_tmp_base(names, usenet_cfg)
    final_nzb = output_dir / f"{names.safe_nzb_name}.nzb"
    staged_nzb = tmp_base / names.uuid / f"{names.safe_nzb_name}.nzb"
    existing = await _existing_usenet_nzb(final_nzb, staged_nzb)
    if existing is not None:
        return existing
    usenet_dir = tmp_base / names.uuid / "usenet"
    await aiofiles.os.makedirs(usenet_dir, exist_ok=True)
    binaries = await _usenet_binaries(meta, usenet_cfg, prepare_only)
    if binaries is None:
        return None
    state = await _prepared_usenet_upload(
        names, usenet_dir, meta, usenet_cfg, binaries
    )
    await _generate_usenet_par2(state, names, meta, binaries)
    _save_prepared_usenet_files(meta, state)
    if prepare_only:
        logger.debug(
            "[cyan]Usenet archive and PAR2 preparation completed; posting will run later.[/cyan]"
        )
        return str(state.upload_root)
    identity = _post_identity(meta, usenet_cfg, names.name)
    await _post_usenet(
        meta, usenet_cfg, binaries, state, names, identity, staged_nzb
    )
    await _cleanup_usenet_upload(state, meta.debug)
    final_path = await _relocate_usenet_nzb(staged_nzb, final_nzb, meta.debug)
    await _cleanup_usenet_uuid(tmp_base, names.uuid, meta.debug)
    return str(final_path)
