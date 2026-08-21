"""Cross-cutting runtime ports shared by services and integrations.

The application logger is a standard-library logger. Delivery/integration code
may attach handlers, while services depend only on this consumer-owned module.
Interactive buffering and console rendering are configured explicitly from the
composition root when the Rich adapter is imported.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import traceback
from collections.abc import AsyncIterator, Callable, Generator
from typing import Any, Protocol

logger = logging.getLogger("UploadAssistant")
logger.setLevel(logging.INFO)


class ConsolePort(Protocol):
    def print(self, *objects: Any, **kwargs: Any) -> None: ...

    def print_exception(self, *args: Any, **kwargs: Any) -> None: ...


class _LoggingConsole:
    def print(self, *objects: object, **_kwargs: object) -> None:
        logger.info(
            " ".join(str(item) for item in objects), extra={"markup": False}
        )

    def print_exception(self, *_args: object, **_kwargs: object) -> None:
        logger.error(traceback.format_exc(), extra={"markup": False})


class _ConsoleProxy:
    def __init__(self) -> None:
        self._target: ConsolePort = _LoggingConsole()

    def configure(self, target: ConsolePort) -> None:
        self._target = target

    def print(self, *objects: object, **kwargs: object) -> None:
        self._target.print(*objects, **kwargs)

    def print_exception(self, *args: object, **kwargs: object) -> None:
        self._target.print_exception(*args, **kwargs)


console = _ConsoleProxy()

type BufferFactory = Callable[[], contextlib.AbstractAsyncContextManager[None]]
_buffer_factory: BufferFactory | None = None
_suppress_cli_progress = contextvars.ContextVar(
    "suppress_cli_progress", default=False
)


def configure_runtime_support(
    *,
    console_adapter: ConsolePort,
    buffer_factory: BufferFactory | None = None,
) -> None:
    """Bind concrete terminal behavior at the composition boundary."""

    global _buffer_factory
    console.configure(console_adapter)
    _buffer_factory = buffer_factory


@contextlib.contextmanager
def suppress_cli_progress() -> Generator[None]:
    """Temporarily hide terminal progress for background application work."""

    token = _suppress_cli_progress.set(True)
    try:
        yield
    finally:
        _suppress_cli_progress.reset(token)


def is_cli_progress_suppressed() -> bool:
    return _suppress_cli_progress.get()


@contextlib.asynccontextmanager
async def buffer_console_logs() -> AsyncIterator[None]:
    """Delegate log buffering to the configured delivery adapter when present."""

    factory = _buffer_factory
    if factory is None:
        yield
        return
    async with factory():
        yield


async def prompt_in_thread[PromptResult](
    callback: Callable[..., PromptResult],
    /,
    *args: Any,
    **kwargs: Any,
) -> PromptResult:
    """Run an explicitly supplied prompt callback without blocking the event loop."""

    async with buffer_console_logs():
        return await asyncio.to_thread(callback, *args, **kwargs)
