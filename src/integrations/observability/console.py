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


def _progress_options(kwargs: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    # Shared Live state belongs to this adapter's concrete Rich console. Some
    # integration callers hold only the narrow ConsolePort proxy, which cannot
    # satisfy Rich's broader Console API (for example, ``get_time``).
    kwargs["console"] = console
    disabled = (
        bool(kwargs.get("disable", False)) or is_cli_progress_suppressed()
    )
    if disabled:
        kwargs["disable"] = True
    return kwargs, not disabled


def _started_progress(
    columns: tuple[Any, ...], options: dict[str, Any]
) -> Progress:
    progress = Progress(*columns, **options)
    progress.start()
    return progress


def _acquire_progress(
    columns: tuple[Any, ...], options: dict[str, Any], shared: bool
) -> Progress:
    global _shared_progress, _shared_progress_users

    if not shared:
        return _started_progress(columns, options)
    with _live_progress_lock:
        progress = _shared_progress
        if progress is None:
            progress = _started_progress(columns, options)
            _shared_progress = progress
        _shared_progress_users += 1
        return progress


def _release_progress(progress: Progress, shared: bool) -> None:
    global _shared_progress, _shared_progress_users

    if not shared:
        progress.stop()
        return
    with _live_progress_lock:
        _shared_progress_users -= 1
        if _shared_progress_users == 0 and _shared_progress is not None:
            _shared_progress.stop()
            _shared_progress = None


@contextlib.contextmanager
def progress_display(*columns: Any, **kwargs: Any) -> Generator[Progress]:
    """Yield a progress panel that safely shares the console's single Live display."""
    options, shared = _progress_options(kwargs)
    progress = _acquire_progress(columns, options, shared)
    try:
        yield progress
    finally:
        _release_progress(progress, shared)


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
    log_render.show_level = bool(
        default_config.get("console_show_level", False)
    )
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
                raise RuntimeError(
                    "Console log buffering cannot span concurrent event loops"
                )
            _log_buffer_loop = current_loop
            _log_buffer_lock = None

        lock = _log_buffer_lock
        if lock is None:
            lock = asyncio.Lock()
            _log_buffer_lock = lock
        return lock


def _rich_handlers(root_logger: logging.Logger) -> list[RichHandler]:
    return [
        handler
        for handler in root_logger.handlers
        if isinstance(handler, RichHandler)
    ]


def _install_log_buffer(
    root_logger: logging.Logger,
    rich_handlers: list[RichHandler],
    buffer_handler: LogBufferHandler,
) -> None:
    for handler in rich_handlers:
        root_logger.removeHandler(handler)
    root_logger.addHandler(buffer_handler)


def _restore_log_buffer(
    root_logger: logging.Logger,
    rich_handlers: list[RichHandler],
    buffer_handler: LogBufferHandler,
) -> None:
    root_logger.removeHandler(buffer_handler)
    for handler in rich_handlers:
        root_logger.addHandler(handler)
    for record in buffer_handler.buffer:
        for handler in rich_handlers:
            handler.handle(record)


@contextlib.asynccontextmanager
async def buffer_console_logs() -> AsyncGenerator[None]:
    """Temporarily hold console log output while user prompts are active."""
    async with _get_log_buffer_lock():
        root_logger = logger
        rich_handlers = _rich_handlers(root_logger)
        buffer_handler = LogBufferHandler()
        _install_log_buffer(root_logger, rich_handlers, buffer_handler)
        try:
            yield
        finally:
            _restore_log_buffer(
                root_logger,
                rich_handlers,
                buffer_handler,
            )


async def prompt_in_thread[PromptResult](
    callback: Callable[..., PromptResult], /, *args: Any, **kwargs: Any
) -> PromptResult:
    """Compatibility wrapper for callers still importing the Rich adapter."""
    async with buffer_console_logs():
        return await asyncio.to_thread(callback, *args, **kwargs)


configure_runtime_support(
    console_adapter=cast(ConsolePort, console),
    buffer_factory=buffer_console_logs,
)


# Context variable to hold the path to the current release's log file (e.g. /tmp/<uuid>/upload.log)
current_release_log_path: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("current_release_log_path", default=None)
)


class LogFileFormatter(logging.Formatter):
    def __init__(
        self,
        fmt: str = "[%(asctime)s] %(levelname)s: %(message)s",
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> None:
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

    @staticmethod
    def _ensure_log_directory(log_path: str) -> None:
        log_dir = Path(log_path).parent
        if str(log_dir) and not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)

    def _append_record(
        self,
        record: logging.LogRecord,
        log_path: str,
    ) -> None:
        message = self.format(record)
        self._ensure_log_directory(log_path)
        with Path(log_path).open("a", encoding="utf-8") as file_handle:
            file_handle.write(message + "\n")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not _write_log_enabled:
                return
            log_path = current_release_log_path.get()
            if not log_path:
                return
            self._append_record(record, log_path)
        except Exception:
            self.handleError(record)


# Add the dynamic file handler to UploadAssistant logger
dynamic_file_handler = DynamicFileHandler(LogFileFormatter())
logger.addHandler(dynamic_file_handler)
