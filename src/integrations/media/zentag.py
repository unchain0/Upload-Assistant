import asyncio
import json
import os
import re
import shutil
import tempfile
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


def _zentag_auto_allowed(meta: Meta, config: dict[str, Any]) -> bool:
    if meta.site_check or not meta.unattended:
        return False
    if not _zenith_selected(meta):
        return False
    return bool(config.get("DEFAULT", {}).get("auto_zentag", True))


def should_prepare_zenith_audiobook(
    meta: Meta, config: dict[str, Any]
) -> bool:
    reason = _auto_skip_reason(meta, config)
    if reason:
        logger.info(f"ZENITH: not preparing the audiobook with zentag: {reason}")
        return False
    source = Path(str(meta.path or "")).expanduser().resolve()
    if not _contains_m4b(source):
        logger.info(
            "ZENITH: not preparing the audiobook with zentag: "
            f"no M4B file under {source}"
        )
        return False
    return True


def _resolved_ebook_path(value: object) -> Path | None:
    candidate = Path(str(value)).expanduser().resolve()
    if candidate.suffix.lower() not in EBOOK_SUFFIXES:
        return None
    return candidate if candidate.is_file() else None


def _ebook_candidates(meta: Meta) -> list[Path]:
    return [
        candidate
        for value in meta.filelist
        if (candidate := _resolved_ebook_path(value)) is not None
    ]


def _ebook_source(meta: Meta) -> Path | None:
    source = _resolved_ebook_path(meta.path)
    if source is not None:
        return source
    candidates = _ebook_candidates(meta)
    return candidates[0] if len(candidates) == 1 else None


def _plain_ebook(meta: Meta) -> bool:
    if meta.category != "BOOK" or meta.audiobook:
        return False
    return not any((meta.comic, meta.manga, meta.magazine, meta.newspaper))


def _auto_skip_reason(meta: Meta, config: dict[str, Any]) -> str:
    if meta.site_check:
        return "site_check is enabled"
    if not meta.unattended:
        return "the run is not unattended"
    if not _zenith_selected(meta):
        return "ZENITH is not among the selected trackers"
    if not config.get("DEFAULT", {}).get("auto_zentag", True):
        return "auto_zentag is disabled in DEFAULT"
    return ""


def _ebook_skip_reason(meta: Meta) -> str:
    if meta.category != "BOOK":
        return f"category is {meta.category!r}, not BOOK"
    if meta.audiobook:
        return "the book is an audiobook"
    if any((meta.comic, meta.manga, meta.magazine, meta.newspaper)):
        return "the book is a comic, manga, magazine or newspaper"
    if _ebook_source(meta) is None:
        return (
            f"no ebook file could be resolved (path={meta.path!r}, "
            f"filelist={list(meta.filelist)!r})"
        )
    return ""


def should_prepare_zenith_ebook(meta: Meta, config: dict[str, Any]) -> bool:
    reason = _auto_skip_reason(meta, config) or _ebook_skip_reason(meta)
    if reason:
        logger.info(f"ZENITH: not preparing the ebook with zentag: {reason}")
        return False
    return True


async def _run_process(command: list[str]) -> tuple[int, str, str]:
    # zentag is a managed checksum-verified binary; argv is exec-form.
    process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
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
    # zentag is a managed checksum-verified binary; argv is exec-form.
    process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
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


def _reported_output_path(stdout: str, output_root: Path) -> Path | None:
    match = re.search(r"Wrote (.+)$", stdout, re.MULTILINE)
    if not match:
        return None
    try:
        raw_output = match.group(1).strip()
        value = (
            json.loads(raw_output)
            if raw_output.startswith('"')
            else raw_output
        )
        output = Path(value).resolve()
        output.relative_to(output_root.resolve())
        return output
    except json.JSONDecodeError, OSError, ValueError:
        return None


def _output_directory(output: Path | None) -> Path | None:
    if output is None:
        return None
    if output.is_dir():
        return output
    return output.parent if output.is_file() else None


def _written_output(stdout: str, output_root: Path) -> Path | None:
    return _output_directory(_reported_output_path(stdout, output_root))


def _zentag_config_lines(output_root: Path, base_dir: str) -> list[str]:
    session_dir = Path(base_dir) / "tmp" / "zentag-sessions"
    return [
        f"output_dir: {json.dumps(str(output_root))}",
        f"session_dir: {json.dumps(str(session_dir))}",
    ]


def _existing_ebook_meta_path(value: str) -> Path:
    resolved = Path(value).expanduser()
    if not resolved.exists():
        raise RuntimeError(
            f"Configured ebook_meta_path does not exist: {resolved}"
        )
    if not resolved.is_file():
        raise RuntimeError(
            f"Configured ebook_meta_path is not a file: {resolved}"
        )
    return resolved


def _ensure_ebook_meta_executable(path: Path) -> None:
    if os.name == "nt" or os.access(path, os.X_OK):
        return
    raise RuntimeError(f"Configured ebook_meta_path is not executable: {path}")


def _validated_ebook_meta_path(value: object) -> Path | None:
    ebook_meta_path = str(value or "").strip()
    if not ebook_meta_path:
        return None
    resolved = _existing_ebook_meta_path(ebook_meta_path)
    _ensure_ebook_meta_executable(resolved)
    return resolved


def _zentag_paths(
    source: Path, base_dir: str, default_config: dict[str, Any]
) -> tuple[Path, Path]:
    output_root = source.parent / "zentag-output"
    config_path = Path(base_dir) / "tmp" / "zentag-auto.yaml"
    output_root.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = _zentag_config_lines(output_root, base_dir)
    ebook_meta_path = _validated_ebook_meta_path(
        default_config.get("ebook_meta_path", "")
    )
    if ebook_meta_path is not None:
        lines.append(f"ebook_meta_path: {json.dumps(str(ebook_meta_path))}")
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
        "--title": _without_book_subtitle(
            _preferred_text(meta.title, meta.book_title)
        ),
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
    try:
        violations = json.loads(stdout or "[]")
    except json.JSONDecodeError:
        violations = None
    if not isinstance(violations, list):
        error = _run_error(
            code, stdout, stderr, "zentag compliance check failed"
        )
        if error is not None:
            raise error
        return
    blocking = [
        item
        for item in violations
        if isinstance(item, dict)
        and str(item.get("Severity", "")).casefold() in {"error", "fatal"}
    ]
    if blocking:
        raise RuntimeError(
            f"zentag compliance check returned {len(blocking)} blocking violation(s)"
        )
    if violations:
        logger.warning(
            f"[yellow]ZENITH: zentag reported {len(violations)} non-blocking "
            "finding(s); continuing[/yellow]"
        )
    if code != 0 and not violations:
        error = _run_error(
            code, stdout, stderr, "zentag compliance check failed"
        )
        if error is not None:
            raise error


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


_ZENTAG_INFRA_MARKERS = (
    "ebook-meta",
    "calibre",
    "not usable",
    "no such file",
    "command not found",
)

_FILENAME_TRANSLATION = str.maketrans(
    {"/": "-", ":": "-", "?": "", "<": "", ">": "", "|": "-", "*": "", '"': "'"}
)


def _without_book_subtitle(value: str) -> str:
    """Zenith titles are the official title only; a long colon suffix is the subtitle."""
    head, separator, tail = value.partition(":")
    if not separator:
        return value
    if len(tail.split()) < 6:
        return value
    return head.strip()


def _natural_title(value: str) -> str:
    match = re.match(r"^(?P<body>.+),\s*(?P<article>The|A|An)$", value, re.IGNORECASE)
    if match:
        return f"{match.group('article').capitalize()} {match.group('body').strip()}"
    return value


def fallback_ebook_filename(meta: Meta) -> str | None:
    """Build the wiki filename when zentag/ebook-meta cannot run.

    Only the data already present in the prepared metadata is used; the
    function returns None when a required field is missing, so the tracker
    keeps rejecting the upload instead of inventing a name.
    """
    author = str(meta.author or "").strip()
    title = _without_book_subtitle(_natural_title(str(meta.title or "").strip()))
    year = "" if meta.year is None else str(meta.year)
    language = str(meta.book_language_iso or "").strip().upper()
    isbn = str(meta.isbn or "").strip()
    missing = [
        name
        for name, value in (
            ("author", author),
            ("title", title),
            ("year", year),
            ("book_language_iso", language),
            ("isbn", isbn),
        )
        if not value
    ]
    if missing:
        logger.warning(
            "[yellow]ZENITH: cannot build the fallback ebook filename; missing: "
            f"{', '.join(missing)}[/yellow]"
        )
        return None
    series = str(meta.book_series or "").strip()
    index = str(meta.book_series_index or "").strip()
    series_part = f"{series} #{index}" if series and index else series
    edition = str(meta.manual_edition or meta.edition or "").strip()
    details = [language]
    if edition:
        details.append(edition)
    details.append(isbn)
    source = str(meta.manual_source or meta.source or "").strip().upper()
    if source == "RETAIL":
        details.append("Retail")
    parts = [author]
    if series_part:
        parts.extend(["-", series_part])
    parts.extend(["-", title])
    name = f"{' '.join(parts)} ({year}) [{' '.join(details)}]"
    extension = Path(str(meta.path or title)).suffix
    return f"{name.translate(_FILENAME_TRANSLATION)}{extension}"


def _fallback_ebook_copy(meta: Meta, source: Path, base_dir: str) -> str | None:
    filename = fallback_ebook_filename(meta)
    if not filename:
        return None
    try:
        container = Path(base_dir) / "tmp" / str(meta.uuid)
        container.mkdir(parents=True, exist_ok=True)
        target_dir = Path(tempfile.mkdtemp(prefix="zenith-ebook-", dir=container))
        target = target_dir / Path(filename).stem
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / filename)
    except OSError as error:
        logger.warning(
            f"[yellow]ZENITH: could not copy the ebook with the fallback name: {error}[/yellow]"
        )
        return None
    logger.info(
        f"[green]ZENITH: copied the ebook with the wiki filename: {target / filename}[/green]"
    )
    return str(target)


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
    except (TimeoutError, OSError) as error:
        logger.warning(
            "[yellow]ZENITH: zentag is unavailable; copying the ebook with the "
            f"wiki filename instead: {error}[/yellow]"
        )
        return _fallback_ebook_copy(meta, source, base_dir)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        text = str(error).casefold()
        if any(marker in text for marker in _ZENTAG_INFRA_MARKERS):
            logger.warning(
                "[yellow]ZENITH: zentag dependency is missing; copying the ebook "
                f"with the wiki filename instead: {error}[/yellow]"
            )
            return _fallback_ebook_copy(meta, source, base_dir)
        logger.warning(
            "[yellow]ZENITH: automatic ebook preparation failed; keeping the "
            f"original for other trackers: {error}[/yellow]"
        )
        return None
