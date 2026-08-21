import asyncio
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.zentag_binary import ZentagBinaryManager

PROCESS_TIMEOUT = 3600.0
EBOOK_SUFFIXES = {".epub", ".pdf", ".djvu", ".mobi", ".azw3"}


def _zenith_selected(meta: Meta) -> bool:
    trackers = (
        [meta.trackers] if isinstance(meta.trackers, str) else meta.trackers
    )
    return any(
        str(tracker).strip().upper() == "ZENITH" for tracker in trackers
    )


def _contains_m4b(source: Path) -> bool:
    if source.is_file():
        return source.suffix.lower() == ".m4b"
    return source.is_dir() and any(
        path.is_file() for path in source.rglob("*.m4b")
    )


def should_prepare_zenith_audiobook(
    meta: Meta, config: dict[str, Any]
) -> bool:
    if meta.site_check or not meta.unattended or not _zenith_selected(meta):
        return False
    if not config.get("DEFAULT", {}).get("auto_zentag", True):
        return False
    source = Path(str(meta.path or "")).expanduser().resolve()
    return _contains_m4b(source)


def _ebook_source(meta: Meta) -> Path | None:
    source = Path(str(meta.path or "")).expanduser().resolve()
    if source.is_file() and source.suffix.lower() in EBOOK_SUFFIXES:
        return source
    candidates = [
        Path(str(path)).expanduser().resolve()
        for path in meta.filelist
        if Path(str(path)).suffix.lower() in EBOOK_SUFFIXES
    ]
    return (
        candidates[0]
        if len(candidates) == 1 and candidates[0].is_file()
        else None
    )


def should_prepare_zenith_ebook(meta: Meta, config: dict[str, Any]) -> bool:
    if meta.site_check or not meta.unattended or not _zenith_selected(meta):
        return False
    if not config.get("DEFAULT", {}).get("auto_zentag", True):
        return False
    return (
        meta.category == "BOOK"
        and not meta.audiobook
        and not (meta.comic or meta.manga or meta.magazine or meta.newspaper)
        and _ebook_source(meta) is not None
    )


async def _run_process(command: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=PROCESS_TIMEOUT
        )
    except BaseException:
        await _terminate_process(process)
        raise
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    with suppress(ProcessLookupError):
        await process.wait()


async def _run_transform(command: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if (
        process.stdin is None
        or process.stdout is None
        or process.stderr is None
    ):
        await _terminate_process(process)
        raise RuntimeError("zentag subprocess pipes are unavailable")
    stdin = cast(asyncio.StreamWriter, process.stdin)
    stdout = cast(asyncio.StreamReader, process.stdout)
    stderr_reader = cast(asyncio.StreamReader, process.stderr)

    stderr_task = asyncio.create_task(stderr_reader.read())
    output = bytearray()
    prompt_buffer = ""

    async def interact() -> None:
        nonlocal prompt_buffer
        while chunk := await stdout.read(1):
            output.extend(chunk)
            prompt_buffer = (
                prompt_buffer + chunk.decode("utf-8", errors="replace")
            )[-256:]
            if re.search(r"Choice \[-?\d+\]: $", prompt_buffer):
                stdin.write(b"\n")
                await stdin.drain()
                prompt_buffer = ""
            elif prompt_buffer.endswith("Proceed? [y/N]: "):
                stdin.write(b"y\n")
                await stdin.drain()
                prompt_buffer = ""

    try:
        await asyncio.wait_for(interact(), timeout=PROCESS_TIMEOUT)
        return_code = await asyncio.wait_for(
            process.wait(), timeout=PROCESS_TIMEOUT
        )
        stderr = await stderr_task
    except BaseException:
        await _terminate_process(process)
        stderr_task.cancel()
        with suppress(asyncio.CancelledError):
            await stderr_task
        raise
    return (
        return_code,
        output.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _written_output(stdout: str, output_root: Path) -> Path | None:
    match = re.search(r"Wrote (.+)$", stdout, re.MULTILINE)
    if not match:
        return None
    try:
        raw_output = match.group(1).strip()
        output = Path(
            json.loads(raw_output)
            if raw_output.startswith('"')
            else raw_output
        ).resolve()
        output.relative_to(output_root.resolve())
    except json.JSONDecodeError, OSError, ValueError:
        return None
    if output.is_dir():
        return output
    return output.parent if output.is_file() else None


def _zentag_paths(
    source: Path, base_dir: str, default_config: dict[str, Any]
) -> tuple[Path, Path]:
    output_root = source.parent / "zentag-output"
    config_path = Path(base_dir) / "tmp" / "zentag-auto.yaml"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"output_dir: {json.dumps(str(output_root))}",
        f"session_dir: {json.dumps(str(Path(base_dir) / 'tmp' / 'zentag-sessions'))}",
    ]
    ebook_meta_path = str(default_config.get("ebook_meta_path", "")).strip()
    if ebook_meta_path:
        resolved_ebook_meta_path = Path(ebook_meta_path).expanduser()
        if not resolved_ebook_meta_path.exists():
            raise RuntimeError(
                f"Configured ebook_meta_path does not exist: {resolved_ebook_meta_path}"
            )
        if not resolved_ebook_meta_path.is_file():
            raise RuntimeError(
                f"Configured ebook_meta_path is not a file: {resolved_ebook_meta_path}"
            )
        if os.name != "nt" and not os.access(
            resolved_ebook_meta_path, os.X_OK
        ):
            raise RuntimeError(
                f"Configured ebook_meta_path is not executable: {resolved_ebook_meta_path}"
            )
        lines.append(
            f"ebook_meta_path: {json.dumps(str(resolved_ebook_meta_path))}"
        )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_root, config_path


def _append_cli_values(command: list[str], values: dict[str, object]) -> None:
    for flag, value in values.items():
        normalized = str(value or "").strip()
        if normalized:
            command.extend([flag, normalized])


def _audiobook_asin(meta: Meta, source: Path) -> str:
    asin = str(meta.book_asin or meta.asin or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]{10}", asin):
        return asin
    match = re.search(r"\bB0[A-Z0-9]{8}\b", str(source).upper())
    return match.group(0) if match else ""


def _preferred_text(*values: object) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _zentag_audio_source(meta: Meta) -> str:
    source = str(meta.source or "").strip().upper()
    return source if source in {"WEB", "CD", "VINYL", "CASSETTE"} else ""


def _series_part(meta: Meta) -> str:
    if not str(meta.book_series or "").strip():
        return ""
    return str(meta.book_series_index or "").strip()


def _audiobook_override_values(meta: Meta, asin: str) -> dict[str, object]:
    return {
        "--author": _preferred_text(meta.author, meta.book_author),
        "--title": _preferred_text(meta.title, meta.book_title),
        "--publisher": _preferred_text(meta.publisher, meta.book_publisher),
        "--year": meta.year,
        "--narrator": meta.narrator,
        "--series": meta.book_series,
        "--series-part": _series_part(meta),
        "--language": _preferred_text(
            meta.book_language_iso, meta.book_language
        ),
        "--isbn": _preferred_text(meta.isbn, meta.book_isbn),
        "--asin": asin,
        "--edition": meta.edition,
        "--source": _zentag_audio_source(meta),
    }


def _audiobook_command(
    binary: str,
    config_path: Path,
    source: Path,
    meta: Meta,
) -> list[str]:
    command = [
        binary,
        "--config",
        str(config_path),
        "transform",
        str(source),
        "--clean",
    ]
    _append_cli_values(
        command,
        _audiobook_override_values(meta, _audiobook_asin(meta, source)),
    )
    return command


def _ebook_override_values(meta: Meta) -> dict[str, object]:
    return {
        "--author": _preferred_text(meta.author, meta.book_author),
        "--title": _preferred_text(meta.title, meta.book_title),
        "--year": meta.year,
        "--isbn": _preferred_text(meta.isbn, meta.book_isbn),
        "--series": meta.book_series,
        "--series-part": _series_part(meta),
        "--edition": meta.edition,
        "--publisher": _preferred_text(meta.publisher, meta.book_publisher),
        "--language": _preferred_text(
            meta.book_language_iso, meta.book_language
        ),
        "--description": _preferred_text(meta.book_overview, meta.overview),
        "--asin": _preferred_text(meta.book_asin, meta.asin),
    }


def _ebook_command(
    binary: str, config_path: Path, source: Path, meta: Meta
) -> list[str]:
    command = [binary, "--config", str(config_path), "ebook", str(source)]
    _append_cli_values(command, _ebook_override_values(meta))
    return command


def _run_error(
    return_code: int, stdout: str, stderr: str, fallback: str
) -> RuntimeError | None:
    if return_code == 0:
        return None
    return RuntimeError(stderr.strip() or stdout.strip() or fallback)


def _written_path(stdout: str, output_root: Path) -> Path | None:
    match = re.search(r"Wrote (.+)$", stdout, re.MULTILINE)
    if not match:
        return None
    try:
        raw = match.group(1).strip()
        value = json.loads(raw) if raw.startswith('"') else raw
        path = Path(str(value)).resolve()
        path.relative_to(output_root.resolve())
    except json.JSONDecodeError, OSError, ValueError:
        return None
    return path if path.exists() else None


def _ebook_output_directory(stdout: str, output_root: Path) -> Path | None:
    output = _written_path(stdout, output_root)
    if output is None or not output.is_file():
        return None
    if output.suffix.lower() not in EBOOK_SUFFIXES:
        return None
    return output.parent


async def _validate_audiobook_output(
    binary: str, config_path: Path, output: Path
) -> None:
    code, stdout, stderr = await _run_process(
        [binary, "--config", str(config_path), "check", str(output), "--json"]
    )
    error = _run_error(code, stdout, stderr, "zentag compliance check failed")
    if error is not None:
        raise error
    violations = json.loads(stdout or "[]")
    if violations:
        raise RuntimeError(
            f"zentag compliance check returned {len(violations)} violation(s)"
        )


async def _prepare_audiobook_copy(
    meta: Meta, source: Path, base_dir: str, config: dict[str, Any]
) -> str:
    binary = await ZentagBinaryManager.ensure_binary(base_dir)
    output_root, config_path = _zentag_paths(
        source, base_dir, config.get("DEFAULT", {})
    )
    command = _audiobook_command(binary, config_path, source, meta)
    logger.info(
        f"[cyan]ZENITH: preparing a compliant audiobook copy with zentag: {source.name}[/cyan]"
    )
    code, stdout, stderr = await _run_transform(command)
    error = _run_error(
        code, stdout, stderr, f"zentag exited with status {code}"
    )
    if error is not None:
        raise error
    output = _written_output(stdout, output_root)
    if output is None:
        raise RuntimeError("zentag did not report a valid output directory")
    await _validate_audiobook_output(binary, config_path, output)
    logger.info(
        f"[green]ZENITH: zentag prepared and validated: {output}[/green]"
    )
    return str(output)


async def prepare_zenith_audiobook(
    meta: Meta, base_dir: str, config: dict[str, Any]
) -> str | None:
    if not should_prepare_zenith_audiobook(meta, config):
        return None
    source = Path(str(meta.path or "")).expanduser().resolve()
    if not _contains_m4b(source):
        return None
    try:
        return await _prepare_audiobook_copy(meta, source, base_dir, config)
    except (
        TimeoutError,
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        logger.warning(
            "[yellow]ZENITH: automatic zentag preparation failed; keeping the "
            f"original for other trackers: {error}[/yellow]"
        )
        return None


async def _prepare_ebook_copy(
    meta: Meta, source: Path, base_dir: str, config: dict[str, Any]
) -> str:
    binary = await ZentagBinaryManager.ensure_binary(base_dir)
    output_root, config_path = _zentag_paths(
        source, base_dir, config.get("DEFAULT", {})
    )
    command = _ebook_command(binary, config_path, source, meta)
    logger.info(
        f"[cyan]ZENITH: preparing a compliant ebook copy with zentag: {source.name}[/cyan]"
    )
    code, stdout, stderr = await _run_process(command)
    error = _run_error(
        code, stdout, stderr, f"zentag exited with status {code}"
    )
    if error is not None:
        raise error
    output = _ebook_output_directory(stdout, output_root)
    if output is None:
        raise RuntimeError("zentag did not report a valid ebook output file")
    logger.info(
        f"[green]ZENITH: zentag prepared and validated ebook: {output}[/green]"
    )
    return str(output)


async def prepare_zenith_ebook(
    meta: Meta, base_dir: str, config: dict[str, Any]
) -> str | None:
    if not should_prepare_zenith_ebook(meta, config):
        return None
    source = _ebook_source(meta)
    if source is None:
        return None
    try:
        return await _prepare_ebook_copy(meta, source, base_dir, config)
    except (
        TimeoutError,
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        logger.warning(
            "[yellow]ZENITH: automatic ebook preparation failed; keeping the "
            f"original for other trackers: {error}[/yellow]"
        )
        return None
