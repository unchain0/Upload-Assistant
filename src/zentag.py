import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

from bin.get_zentag import ZentagBinaryManager
from src.console import logger
from src.meta import Meta

PROCESS_TIMEOUT = 3600.0


def _zenith_selected(meta: Meta) -> bool:
    trackers = [meta.trackers] if isinstance(meta.trackers, str) else meta.trackers
    return any(str(tracker).strip().upper() == "ZENITH" for tracker in trackers)


def _contains_m4b(source: Path) -> bool:
    if source.is_file():
        return source.suffix.lower() == ".m4b"
    return source.is_dir() and any(path.is_file() for path in source.rglob("*.m4b"))


async def _run_process(command: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=PROCESS_TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


async def _run_transform(command: list[str]) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
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
            prompt_buffer = (prompt_buffer + chunk.decode("utf-8", errors="replace"))[-256:]
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
        return_code = await asyncio.wait_for(process.wait(), timeout=PROCESS_TIMEOUT)
        stderr = await stderr_task
    except TimeoutError:
        process.kill()
        await process.wait()
        stderr_task.cancel()
        raise
    return return_code, output.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _written_output(stdout: str, output_root: Path) -> Path | None:
    match = re.search(r'Wrote ("(?:[^"\\]|\\.)*")$', stdout, re.MULTILINE)
    if not match:
        return None
    try:
        output = Path(json.loads(match.group(1))).resolve()
        output.relative_to(output_root.resolve())
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return output if output.is_dir() else None


async def prepare_zenith_audiobook(meta: Meta, base_dir: str, config: dict[str, Any]) -> str | None:
    if meta.debug or meta.site_check or not meta.unattended or not _zenith_selected(meta):
        return None
    if not config.get("DEFAULT", {}).get("auto_zentag", True):
        return None

    source = Path(str(meta.path or "")).expanduser().resolve()
    if not _contains_m4b(source):
        return None

    try:
        binary = await ZentagBinaryManager.ensure_binary(base_dir)
        output_root = source.parent / "zentag-output"
        session_root = Path(base_dir) / "tmp" / "zentag-sessions"
        config_path = Path(base_dir) / "tmp" / "zentag-auto.yaml"
        output_root.mkdir(parents=True, exist_ok=True)
        session_root.mkdir(parents=True, exist_ok=True)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f"output_dir: {json.dumps(str(output_root))}\nsession_dir: {json.dumps(str(session_root))}\n",
            encoding="utf-8",
        )

        asin = str(meta.book_asin or meta.asin or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            asin_match = re.search(r"\bB0[A-Z0-9]{8}\b", str(source).upper())
            asin = asin_match.group(0) if asin_match else ""

        command = [binary, "--config", str(config_path), "transform", str(source), "--clean"]
        if asin:
            command.extend(["--asin", asin])
        logger.info(f"[cyan]ZENITH: preparing a compliant audiobook copy with zentag: {source.name}[/cyan]")
        return_code, stdout, stderr = await _run_transform(command)
        if return_code != 0:
            raise RuntimeError(stderr.strip() or stdout.strip() or f"zentag exited with status {return_code}")

        output = _written_output(stdout, output_root)
        if output is None:
            raise RuntimeError("zentag did not report a valid output directory")

        check_code, check_stdout, check_stderr = await _run_process([binary, "--config", str(config_path), "check", str(output), "--json"])
        if check_code != 0:
            raise RuntimeError(check_stderr.strip() or check_stdout.strip() or "zentag compliance check failed")
        violations = json.loads(check_stdout or "[]")
        if violations:
            raise RuntimeError(f"zentag compliance check returned {len(violations)} violation(s)")

        logger.info(f"[green]ZENITH: zentag prepared and validated: {output}[/green]")
        return str(output)
    except (TimeoutError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(f"[yellow]ZENITH: automatic zentag preparation failed; keeping the original for other trackers: {error}[/yellow]")
        return None
