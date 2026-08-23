# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
from typing import Any

from src.integrations.observability.runtime_support import logger

termios = import_module("termios") if os.name == "posix" else None

# Detect Android environment
IS_ANDROID = (
    "android" in platform.platform().lower()
    or Path("/system/build.prop").exists()
    or "ANDROID_ROOT" in os.environ
)

running_subprocesses: set[subprocess.Popen[Any]] = set()
thread_executor: ThreadPoolExecutor | None = None
IS_MACOS = sys.platform == "darwin"
erase_key: str | None = None


def _shutdown_thread_executor() -> None:
    global thread_executor
    if thread_executor is None:
        return
    thread_executor.shutdown(wait=True)
    thread_executor = None


def _close_process_streams(proc: subprocess.Popen[Any]) -> None:
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream:
            with contextlib.suppress(Exception):
                stream.close()


def _force_kill_after_timeout(proc: subprocess.Popen[Any]) -> None:
    if IS_ANDROID:
        return
    with contextlib.suppress(PermissionError, OSError):
        proc.kill()


def _log_termination_denied(proc: subprocess.Popen[Any]) -> None:
    if IS_ANDROID:
        return
    logger.info(
        f"[yellow]Cannot terminate process {proc.pid}: Permission denied[/yellow]"
    )


async def _terminate_live_process(proc: subprocess.Popen[Any]) -> None:
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
        except TimeoutError:
            _force_kill_after_timeout(proc)
    except PermissionError, OSError:
        _log_termination_denied(proc)


async def _terminate_tracked_process(proc: subprocess.Popen[Any]) -> None:
    if proc.returncode is None:
        await _terminate_live_process(proc)
    _close_process_streams(proc)


async def _terminate_tracked_subprocesses() -> None:
    while running_subprocesses:
        await _terminate_tracked_process(running_subprocesses.pop())


async def _settle_subprocess_transports() -> None:
    with contextlib.suppress(RuntimeError):
        await asyncio.sleep(0.1)


def _remaining_tasks() -> list[asyncio.Task[Any]]:
    current = asyncio.current_task()
    return [task for task in asyncio.all_tasks() if task is not current]


def _cancel_tasks(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()


async def _gather_cancelled_tasks(tasks: list[asyncio.Task[Any]]) -> list[Any]:
    if not tasks:
        return []
    try:
        return list(await asyncio.gather(*tasks, return_exceptions=True))
    except RuntimeError:
        return []


async def _cancel_remaining_tasks() -> list[Any]:
    try:
        tasks = _remaining_tasks()
    except RuntimeError:
        return []
    _cancel_tasks(tasks)
    with contextlib.suppress(RuntimeError):
        await asyncio.sleep(0.1)
    return await _gather_cancelled_tasks(tasks)


def _report_cleanup_results(results: list[Any]) -> None:
    for result in results:
        if isinstance(result, Exception) and not isinstance(
            result, asyncio.CancelledError
        ):
            logger.error(f"[red]Error during cleanup: {result}[/red]")


def _terminate_remaining_tracked_subprocesses() -> None:
    for proc in list(running_subprocesses):
        if proc.returncode is not None:
            continue
        with contextlib.suppress(PermissionError, OSError):
            proc.terminate()


def _delete_dead_thread(thread: Any, current: Any) -> None:
    if thread == current or thread.is_alive():
        return
    delete_fn = getattr(thread, "_delete", None)
    if callable(delete_fn):
        with contextlib.suppress(Exception):
            delete_fn()


def _cleanup_dead_threads() -> None:
    try:
        current = threading.current_thread()
        for thread in threading.enumerate():
            _delete_dead_thread(thread, current)
    except Exception as error:
        logger.error(f"[red]Error cleaning up threads: {error}[/red]")


def _shutdown_thread_executor() -> None:
    global thread_executor
    if thread_executor is None:
        return
    thread_executor.shutdown(wait=True)
    thread_executor = None


def _close_process_streams(proc: subprocess.Popen[Any]) -> None:
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream:
            with contextlib.suppress(Exception):
                stream.close()


async def _wait_for_process_exit(proc: subprocess.Popen[Any]) -> None:
    try:
        await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3)
    except TimeoutError:
        if IS_ANDROID:
            return
        with contextlib.suppress(PermissionError, OSError):
            proc.kill()


async def _terminate_tracked_process(proc: subprocess.Popen[Any]) -> None:
    if proc.returncode is None:
        try:
            proc.terminate()
            await _wait_for_process_exit(proc)
        except PermissionError, OSError:
            if not IS_ANDROID:
                logger.info(
                    f"[yellow]Cannot terminate process {proc.pid}: "
                    "Permission denied[/yellow]"
                )
    _close_process_streams(proc)


async def _cleanup_tracked_subprocesses() -> None:
    while running_subprocesses:
        await _terminate_tracked_process(running_subprocesses.pop())


async def _safe_cleanup_sleep() -> None:
    with contextlib.suppress(RuntimeError):
        await asyncio.sleep(0.1)


def _remaining_asyncio_tasks() -> list[Any]:
    current = asyncio.current_task()
    return [task for task in asyncio.all_tasks() if task is not current]


async def _gather_cancelled_tasks(tasks: list[Any]) -> list[Any]:
    for task in tasks:
        task.cancel()
    await _safe_cleanup_sleep()
    if not tasks:
        return []
    try:
        return await asyncio.gather(*tasks, return_exceptions=True)
    except RuntimeError:
        return []


async def _cancel_remaining_tasks() -> list[Any]:
    try:
        return await _gather_cancelled_tasks(_remaining_asyncio_tasks())
    except RuntimeError:
        return []


def _log_cleanup_results(results: list[Any]) -> None:
    for result in results:
        if isinstance(result, Exception) and not isinstance(
            result, asyncio.CancelledError
        ):
            logger.error(f"[red]Error during cleanup: {result}[/red]")


def _terminate_android_tracked_processes() -> None:
    with contextlib.suppress(Exception):
        for proc in list(running_subprocesses):
            if proc.returncode is not None:
                continue
            with contextlib.suppress(
                PermissionError, psutil.AccessDenied, OSError
            ):
                proc.terminate()


def _terminate_child_processes(children: list[Any]) -> None:
    for child in children:
        with contextlib.suppress(
            psutil.NoSuchProcess, psutil.AccessDenied, PermissionError
        ):
            child.terminate()


def _kill_stubborn_children(children: list[Any]) -> None:
    if IS_MACOS:
        return
    try:
        _, still_alive = psutil.wait_procs(children, timeout=3)
        for child in still_alive:
            with contextlib.suppress(
                psutil.NoSuchProcess, psutil.AccessDenied, PermissionError
            ):
                child.kill()
    except psutil.AccessDenied, PermissionError:
        return


def _terminate_non_android_children() -> None:
    try:
        children = psutil.Process().children(recursive=True)
        _terminate_child_processes(children)
        _kill_stubborn_children(children)
    except (PermissionError, psutil.AccessDenied, OSError) as error:
        if not IS_ANDROID:
            logger.info(f"[yellow]Limited process access: {error}[/yellow]")
    except Exception as error:
        logger.error(f"[red]Error during process cleanup: {error}[/red]")


def _terminate_macos_children() -> None:
    if not IS_MACOS or not hasattr(multiprocessing, "active_children"):
        return
    for child in multiprocessing.active_children():
        with contextlib.suppress(Exception):
            child.terminate()
            child.join(1)


def _delete_completed_thread_references() -> None:
    try:
        current = threading.current_thread()
        for thread in threading.enumerate():
            if thread == current or thread.is_alive():
                continue
            delete_fn = getattr(thread, "_delete", None)
            if callable(delete_fn):
                with contextlib.suppress(Exception):
                    delete_fn()
    except Exception as error:
        logger.error(f"[red]Error cleaning up threads: {error}[/red]")


class CleanupManager:
    async def cleanup(self) -> None:
        """Clean tracked task resources before application exit."""
        _shutdown_thread_executor()
        await _cleanup_tracked_subprocesses()
        await _safe_cleanup_sleep()
        _log_cleanup_results(await _cancel_remaining_tasks())
        self.kill_all_threads()

    def kill_all_threads(self) -> None:
        """Clean lingering child resources using the existing platform rules."""
        if IS_ANDROID:
            _terminate_android_tracked_processes()
        else:
            _terminate_non_android_children()
        _terminate_macos_children()
        _delete_completed_thread_references()

    def reset_terminal(self) -> None:
        """Reset the terminal without affecting unrelated processes."""
        if os.name != "posix" or IS_ANDROID:
            return
        try:
            _flush_stderr()
            _reset_stdin_terminal()
            _restore_stdout()
            _flush_stderr()
        except Exception as error:
            _report_terminal_reset_error(error)


def _stdin_is_usable_tty() -> bool:
    return bool(
        hasattr(sys.stdin, "isatty")
        and sys.stdin.isatty()
        and not sys.stdin.closed
    )


def _apply_stty_settings(stty: str) -> None:
    subprocess.run([stty, "sane"], check=False)  # noqa: S603
    if erase_key is not None:
        subprocess.run(  # noqa: S603
            [stty, "erase", erase_key], check=False
        )
    subprocess.run([stty, "-ixon"], check=False)  # noqa: S603


def _flush_terminal_input() -> None:
    if termios is None or not hasattr(termios, "tcflush"):
        return
    tciflush = getattr(termios, "TCIOFLUSH", None)
    if tciflush is None:
        return
    termios.tcflush(sys.stdin.fileno(), tciflush)


def _reset_stdin_terminal() -> None:
    if not _stdin_is_usable_tty():
        return
    try:
        stty = shutil.which("stty")
        if stty is not None:
            _apply_stty_settings(stty)
        _flush_terminal_input()
    except OSError:
        return


def _restore_stdout() -> None:
    if sys.stdout.closed:
        return
    try:
        sys.stdout.write("\033[0m")
        sys.stdout.flush()
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    except OSError, ValueError:
        return


def _flush_stderr() -> None:
    if not sys.stderr.closed:
        sys.stderr.flush()


def _report_terminal_reset_error(error: Exception) -> None:
    with contextlib.suppress(Exception):
        if sys.stderr.closed:
            return
        sys.stderr.write(f"Error during terminal reset: {error}\n")
        sys.stderr.flush()


def _stty_output(stty: str) -> str | None:
    try:
        return subprocess.check_output([stty, "-a"]).decode()  # noqa: S603
    except OSError:
        return None


def _erase_key_from_output(output: str | None) -> str | None:
    if not output:
        return None
    match = re.search(r" erase = (\S+);", output)
    return match.group(1) if match else None


def _read_erase_key() -> str | None:
    """Read the terminal erase key when a controlling TTY is available."""
    if not _stdin_is_usable_tty():
        return None
    stty = shutil.which("stty")
    if stty is None:
        return None
    return _erase_key_from_output(_stty_output(stty))


erase_key = _read_erase_key()
cleanup_manager = CleanupManager()
