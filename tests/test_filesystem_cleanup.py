from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace
from typing import ClassVar

import pytest

from src.integrations.filesystem import cleanup
from src.integrations.filesystem.cleanup import CleanupManager


class _Stream:
    def __init__(self, *, fail: bool = False) -> None:
        self.closed = False
        self.fail = fail

    def close(self) -> None:
        if self.fail:
            raise OSError("close failed")
        self.closed = True


class _Process:
    def __init__(self, *, returncode: int | None = None, wait_error: BaseException | None = None, terminate_error: BaseException | None = None) -> None:
        self.returncode = returncode
        self.pid = 123
        self.wait_error = wait_error
        self.terminate_error = terminate_error
        self.terminated = False
        self.killed = False
        self.stdout = _Stream()
        self.stderr = _Stream(fail=True)
        self.stdin = _Stream()

    def terminate(self) -> None:
        if self.terminate_error:
            raise self.terminate_error
        self.terminated = True

    def wait(self) -> int:
        if self.wait_error:
            raise self.wait_error
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _Task:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Executor:
    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self, *, wait: bool) -> None:
        assert wait
        self.shutdown_called = True


@pytest.fixture(autouse=True)
def _reset_globals() -> None:
    cleanup.running_subprocesses.clear()
    cleanup.thread_executor = None


def test_cleanup_executor_process_stream_tasks_and_result_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _Executor()
    cleanup.thread_executor = executor  # type: ignore[assignment]
    process = _Process()
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]
    task = _Task()
    current = object()

    monkeypatch.setattr(cleanup.asyncio, "all_tasks", lambda: {task, current})
    monkeypatch.setattr(cleanup.asyncio, "current_task", lambda: current)

    async def gather(*_tasks: object, **_kwargs: object):
        return [RuntimeError("task failed"), asyncio.CancelledError()]

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(cleanup.asyncio, "gather", gather)
    monkeypatch.setattr(cleanup.asyncio, "sleep", no_sleep)
    killed: list[bool] = []
    monkeypatch.setattr(CleanupManager, "kill_all_threads", lambda _self: killed.append(True))

    asyncio.run(CleanupManager().cleanup())

    assert executor.shutdown_called and cleanup.thread_executor is None
    assert process.terminated and process.stdout.closed and process.stdin.closed
    assert task.cancelled and killed == [True]


def test_cleanup_process_timeout_force_kill_and_android_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _Process(wait_error=TimeoutError())
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]

    async def timeout(awaitable: object, *_args: object, **_kwargs: object):
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(cleanup.asyncio, "wait_for", timeout)
    monkeypatch.setattr(cleanup.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(cleanup.asyncio, "all_tasks", lambda: set())
    monkeypatch.setattr(CleanupManager, "kill_all_threads", lambda _self: None)
    monkeypatch.setattr(cleanup, "IS_ANDROID", False)
    asyncio.run(CleanupManager().cleanup())
    assert process.killed

    process = _Process(wait_error=TimeoutError())
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]
    monkeypatch.setattr(cleanup, "IS_ANDROID", True)
    asyncio.run(CleanupManager().cleanup())
    assert not process.killed


def test_cleanup_process_permission_errors_and_task_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _Process(terminate_error=PermissionError("denied"))
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]
    monkeypatch.setattr(cleanup, "IS_ANDROID", False)
    monkeypatch.setattr(cleanup.asyncio, "all_tasks", lambda: (_ for _ in ()).throw(RuntimeError("closed loop")))
    monkeypatch.setattr(CleanupManager, "kill_all_threads", lambda _self: None)
    asyncio.run(CleanupManager().cleanup())

    process = _Process(terminate_error=OSError("denied"))
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]
    monkeypatch.setattr(cleanup, "IS_ANDROID", True)
    monkeypatch.setattr(cleanup.asyncio, "all_tasks", lambda: set())
    asyncio.run(CleanupManager().cleanup())


def test_cleanup_gather_runtime_error_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _Task()
    current = object()
    monkeypatch.setattr(cleanup.asyncio, "all_tasks", lambda: {task, current})
    monkeypatch.setattr(cleanup.asyncio, "current_task", lambda: current)

    async def gather(*_args: object, **_kwargs: object):
        raise RuntimeError("loop closed")

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(cleanup.asyncio, "gather", gather)
    monkeypatch.setattr(cleanup.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(CleanupManager, "kill_all_threads", lambda _self: None)
    asyncio.run(CleanupManager().cleanup())
    assert task.cancelled


class _Child:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.joined = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def join(self, _timeout: int) -> None:
        self.joined = True


class _PsProcess:
    children_value: ClassVar[list[_Child]] = []
    error: ClassVar[BaseException | None] = None

    def __init__(self) -> None:
        if type(self).error:
            raise type(self).error

    def children(self, *, recursive: bool) -> list[_Child]:
        assert recursive
        return type(self).children_value


def test_kill_all_threads_non_android_wait_kill_access_and_generic_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    children = [_Child(), _Child()]
    _PsProcess.children_value = children
    _PsProcess.error = None
    monkeypatch.setattr(cleanup, "IS_ANDROID", False)
    monkeypatch.setattr(cleanup, "IS_MACOS", False)
    monkeypatch.setattr(cleanup.psutil, "Process", _PsProcess)
    monkeypatch.setattr(cleanup.psutil, "wait_procs", lambda _children, **_kwargs: ([], [children[1]]))
    monkeypatch.setattr(cleanup.threading, "enumerate", lambda: [])
    CleanupManager().kill_all_threads()
    assert all(child.terminated for child in children) and children[1].killed

    monkeypatch.setattr(cleanup.psutil, "wait_procs", lambda *_args, **_kwargs: (_ for _ in ()).throw(cleanup.psutil.AccessDenied()))
    CleanupManager().kill_all_threads()

    _PsProcess.error = PermissionError("denied")
    CleanupManager().kill_all_threads()
    _PsProcess.error = RuntimeError("unexpected")
    CleanupManager().kill_all_threads()


def test_kill_all_threads_android_macos_and_thread_delete_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    child = _Child()
    process = _Process()
    cleanup.running_subprocesses.add(process)  # type: ignore[arg-type]
    monkeypatch.setattr(cleanup, "IS_ANDROID", True)
    monkeypatch.setattr(cleanup, "IS_MACOS", True)
    monkeypatch.setattr(cleanup.multiprocessing, "active_children", lambda: [child])

    deleted: list[bool] = []

    class DeadThread:
        def is_alive(self) -> bool:
            return False

        def _delete(self) -> None:
            deleted.append(True)

    current = SimpleNamespace(name="current")
    monkeypatch.setattr(cleanup.threading, "current_thread", lambda: current)
    monkeypatch.setattr(cleanup.threading, "enumerate", lambda: [current, DeadThread()])
    CleanupManager().kill_all_threads()
    assert process.terminated and child.terminated and child.joined and deleted == [True]

    monkeypatch.setattr(cleanup.threading, "enumerate", lambda: (_ for _ in ()).throw(RuntimeError("thread failed")))
    CleanupManager().kill_all_threads()


def test_reset_terminal_platform_tty_commands_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = CleanupManager()
    monkeypatch.setattr(cleanup.os, "name", "nt")
    manager.reset_terminal()
    monkeypatch.setattr(cleanup.os, "name", "posix")
    monkeypatch.setattr(cleanup, "IS_ANDROID", True)
    manager.reset_terminal()

    monkeypatch.setattr(cleanup, "IS_ANDROID", False)
    monkeypatch.setattr(cleanup, "IS_MACOS", False)

    class Input:
        closed = False

        @staticmethod
        def isatty() -> bool:
            return True

        @staticmethod
        def fileno() -> int:
            return 0

    class Output(StringIO):
        closed = False

    stderr = Output()
    stdout = Output()
    monkeypatch.setattr(cleanup.sys, "stdin", Input())
    monkeypatch.setattr(cleanup.sys, "stdout", stdout)
    monkeypatch.setattr(cleanup.sys, "stderr", stderr)
    calls: list[list[str]] = []
    monkeypatch.setattr(cleanup.subprocess, "run", lambda args, **_kwargs: calls.append(list(args)))
    monkeypatch.setattr(cleanup, "erase_key", "^?")
    monkeypatch.setattr(cleanup, "termios", SimpleNamespace(TCIOFLUSH=2, tcflush=lambda *_args: None))
    manager.reset_terminal()
    assert ["stty", "sane"] in calls and ["stty", "erase", "^?"] in calls and ["stty", "-ixon"] in calls
    assert "\x1b[0m" in stdout.getvalue() and "\x1b[?25h" in stdout.getvalue()

    monkeypatch.setattr(cleanup, "IS_MACOS", True)
    manager.reset_terminal()
    assert any("xargs kill" in " ".join(call) for call in calls)

    monkeypatch.setattr(cleanup.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stty failed")))
    manager.reset_terminal()


def test_read_erase_key_tty_missing_match_error_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class Input:
        closed = False

        @staticmethod
        def isatty() -> bool:
            return False

    monkeypatch.setattr(cleanup.sys, "stdin", Input())
    assert cleanup._read_erase_key() is None

    Input.isatty = staticmethod(lambda: True)
    monkeypatch.setattr(cleanup.subprocess, "check_output", lambda *_args, **_kwargs: b"speed 9600; erase = ^?; rows 24;")
    assert cleanup._read_erase_key() == "^?"
    monkeypatch.setattr(cleanup.subprocess, "check_output", lambda *_args, **_kwargs: b"no erase setting")
    assert cleanup._read_erase_key() is None
    monkeypatch.setattr(cleanup.subprocess, "check_output", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no tty")))
    assert cleanup._read_erase_key() is None


def test_reset_terminal_stdout_write_error_and_outer_error_reporting(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = CleanupManager()
    monkeypatch.setattr(cleanup.os, "name", "posix")
    monkeypatch.setattr(cleanup, "IS_ANDROID", False)
    monkeypatch.setattr(cleanup, "IS_MACOS", False)

    class Input:
        closed = True

        @staticmethod
        def isatty() -> bool:
            return False

    class BrokenOutput:
        closed = False

        @staticmethod
        def write(_value: str) -> None:
            raise OSError("write failed")

        @staticmethod
        def flush() -> None:
            return None

    stderr = StringIO()
    monkeypatch.setattr(cleanup.sys, "stdin", Input())
    monkeypatch.setattr(cleanup.sys, "stdout", BrokenOutput())
    monkeypatch.setattr(cleanup.sys, "stderr", stderr)
    monkeypatch.setattr(cleanup.subprocess, "run", lambda *_args, **_kwargs: None)
    manager.reset_terminal()

    class ErrorOnce(StringIO):
        calls = 0

        def flush(self) -> None:
            type(self).calls += 1
            if type(self).calls == 1:
                raise RuntimeError("first flush failed")
            super().flush()

    reporter = ErrorOnce()
    ErrorOnce.calls = 0
    monkeypatch.setattr(cleanup.sys, "stdout", StringIO())
    monkeypatch.setattr(cleanup.sys, "stderr", reporter)
    manager.reset_terminal()
    assert "Error during terminal reset: first flush failed" in reporter.getvalue()
