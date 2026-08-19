# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import contextvars
import logging
import re
import threading
from collections.abc import AsyncGenerator, Callable, Generator
from pathlib import Path
from typing import Any, cast

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress

from src.integrations.observability.runtime_support import (
    ConsolePort,
    configure_runtime_support,
    is_cli_progress_suppressed,
    logger,
)

# Terminal rendering follows the active CLI capabilities.
console = Console(color_system="auto")

# Rich permits one Live renderable per Console. Long-running local jobs can
# overlap (for example, an early BASE torrent hash alongside Usenet archive
# preparation), so they share one multi-row progress panel instead of raising
# ``LiveError`` and forcing a slower fallback.
_live_progress_lock = threading.Lock()
_shared_progress: Progress | None = None
_shared_progress_users = 0


@contextlib.contextmanager
def progress_display(*columns: Any, **kwargs: Any) -> Generator[Progress]:
    """Yield a progress panel that safely shares the console's single Live display."""
    global _shared_progress, _shared_progress_users

    requested_disabled = bool(kwargs.get("disable", False)) or is_cli_progress_suppressed()
    if requested_disabled:
        kwargs["disable"] = True
    shared = not requested_disabled
    if shared:
        with _live_progress_lock:
            if _shared_progress is None:
                new_progress = Progress(*columns, **kwargs)
                new_progress.start()
                _shared_progress = new_progress
            progress = _shared_progress
            _shared_progress_users += 1
    else:
        progress = Progress(*columns, **kwargs)
        progress.start()

    try:
        yield progress
    finally:
        if shared:
            with _live_progress_lock:
                _shared_progress_users -= 1
                if _shared_progress_users == 0 and _shared_progress is not None:
                    _shared_progress.stop()
                    _shared_progress = None
        else:
            progress.stop()


# Configure the consumer-owned application logger with safe CLI defaults.
# The composition root applies user settings after selecting the active config.
rich_handler = RichHandler(
    console=console,
    show_time=False,
    show_level=False,
    show_path=False,
    markup=True,
)
rich_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(rich_handler)

_write_log_enabled = False


def configure_console(default_config: dict[str, Any]) -> None:
    """Apply active CLI logging settings at the composition boundary."""

    global _write_log_enabled
    log_render = cast(Any, rich_handler)._log_render
    log_render.show_time = bool(default_config.get("console_show_time", False))
    log_render.show_level = bool(default_config.get("console_show_level", False))
    log_render.show_path = bool(default_config.get("console_show_path", False))
    rich_handler.markup = bool(default_config.get("console_markup", True))
    _write_log_enabled = bool(default_config.get("write_log", False))


class LogBufferHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.buffer: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.append(record)


_log_buffer_lock: asyncio.Lock | None = None
_log_buffer_loop: asyncio.AbstractEventLoop | None = None
_log_buffer_state_lock = threading.Lock()


def _get_log_buffer_lock() -> asyncio.Lock:
    """Return a lock owned by the active event loop.

    In-process CLI tests may use consecutive ``asyncio.run`` calls. Asyncio
    locks cannot be reused after they have been bound to an earlier event loop,
    so replace the lock once that earlier loop has stopped.
    """
    global _log_buffer_lock, _log_buffer_loop

    current_loop = asyncio.get_running_loop()
    with _log_buffer_state_lock:
        if _log_buffer_loop is not current_loop:
            if _log_buffer_loop is not None and _log_buffer_loop.is_running():
                raise RuntimeError("Console log buffering cannot span concurrent event loops")
            _log_buffer_loop = current_loop
            _log_buffer_lock = None

        lock = _log_buffer_lock
        if lock is None:
            lock = asyncio.Lock()
            _log_buffer_lock = lock
        return lock


@contextlib.asynccontextmanager
async def buffer_console_logs() -> AsyncGenerator[None]:
    """Temporarily hold console log output in memory while user prompts are active."""
    async with _get_log_buffer_lock():
        root_logger = logger
        original_rich_handlers = [h for h in root_logger.handlers if isinstance(h, RichHandler)]
        buffer_handler = LogBufferHandler()

        for h in original_rich_handlers:
            root_logger.removeHandler(h)
        root_logger.addHandler(buffer_handler)

        try:
            yield
        finally:
            root_logger.removeHandler(buffer_handler)
            for h in original_rich_handlers:
                root_logger.addHandler(h)
            for record in buffer_handler.buffer:
                for h in original_rich_handlers:
                    h.handle(record)


async def prompt_in_thread[PromptResult](callback: Callable[..., PromptResult], /, *args: Any, **kwargs: Any) -> PromptResult:
    """Compatibility wrapper for callers still importing the Rich adapter."""
    async with buffer_console_logs():
        return await asyncio.to_thread(callback, *args, **kwargs)


configure_runtime_support(console_adapter=cast(ConsolePort, console), buffer_factory=buffer_console_logs)


# Context variable to hold the path to the current release's log file (e.g. /tmp/<uuid>/upload.log)
current_release_log_path: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_release_log_path", default=None)


class LogFileFormatter(logging.Formatter):
    def __init__(self, fmt: str = "[%(asctime)s] %(levelname)s: %(message)s", datefmt: str = "%Y-%m-%d %H:%M:%S") -> None:
        super().__init__(fmt, datefmt)
        self.console = Console(color_system=None, width=150)
        self.ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    def format(self, record: logging.LogRecord) -> str:
        # Format the record normally first
        formatted = super().format(record)

        # Strip ANSI escape sequences
        formatted = self.ansi_escape.sub("", formatted)

        # Strip Rich markup using Console
        with contextlib.suppress(Exception):
            formatted = self.console.render_str(formatted).plain

        return formatted


class DynamicFileHandler(logging.Handler):
    def __init__(self, formatter=None) -> None:
        super().__init__()
        if formatter:
            self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not _write_log_enabled:
                return

            log_path = current_release_log_path.get()
            if not log_path:
                return

            # Format message
            msg = self.format(record)

            # Ensure target directory exists
            log_dir = Path(log_path).parent
            if str(log_dir) and not log_dir.exists():
                log_dir.mkdir(parents=True, exist_ok=True)

            # Append message to file
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            self.handleError(record)


# Add the dynamic file handler to UploadAssistant logger
dynamic_file_handler = DynamicFileHandler(LogFileFormatter())
logger.addHandler(dynamic_file_handler)
