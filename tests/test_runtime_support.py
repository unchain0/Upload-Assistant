from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from src.services import runtime_support


class _Console:
    def __init__(self) -> None:
        self.printed: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.exceptions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def print(self, *objects: object, **kwargs: object) -> None:
        self.printed.append((objects, kwargs))

    def print_exception(self, *args: object, **kwargs: object) -> None:
        self.exceptions.append((args, kwargs))


def test_console_proxy_configuration_and_progress_context() -> None:
    adapter = _Console()
    runtime_support.configure_runtime_support(console_adapter=adapter)
    runtime_support.console.print("hello", end="")
    runtime_support.console.print_exception("failure", width=80)

    assert adapter.printed == [(("hello",), {"end": ""})]
    assert adapter.exceptions == [(("failure",), {"width": 80})]
    assert runtime_support.is_cli_progress_suppressed() is False
    with runtime_support.suppress_cli_progress():
        assert runtime_support.is_cli_progress_suppressed() is True
    assert runtime_support.is_cli_progress_suppressed() is False


def test_buffer_factory_and_prompt_thread_are_explicitly_configured() -> None:
    events: list[str] = []
    adapter = _Console()

    @asynccontextmanager
    async def buffer():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    runtime_support.configure_runtime_support(console_adapter=adapter, buffer_factory=buffer)

    async def exercise() -> None:
        async with runtime_support.buffer_console_logs():
            events.append("body")
        result = await runtime_support.prompt_in_thread(lambda prefix, number: f"{prefix}{number}", "value-", 42)
        assert result == "value-42"

    asyncio.run(exercise())
    assert events == ["enter", "body", "exit", "enter", "exit"]

    runtime_support.configure_runtime_support(console_adapter=adapter, buffer_factory=None)
    asyncio.run(_exercise_without_buffer(events))
    assert events[-1] == "plain"


async def _exercise_without_buffer(events: list[str]) -> None:
    async with runtime_support.buffer_console_logs():
        events.append("plain")


def test_logging_console_emits_plain_messages_and_exception_trace(monkeypatch) -> None:
    messages: list[tuple[str, dict[str, Any]]] = []
    errors: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(runtime_support.logger, "info", lambda message, **kwargs: messages.append((message, kwargs)))
    monkeypatch.setattr(runtime_support.logger, "error", lambda message, **kwargs: errors.append((message, kwargs)))
    fallback = runtime_support._LoggingConsole()

    fallback.print("hello", 42)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        fallback.print_exception()

    assert messages == [("hello 42", {"extra": {"markup": False}})]
    assert "RuntimeError: boom" in errors[0][0]
    assert errors[0][1] == {"extra": {"markup": False}}


def test_integration_runtime_support_console_buffer_and_prompt(monkeypatch) -> None:
    import src.integrations.observability.runtime_support as integration_support

    messages: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(integration_support.logger, "info", lambda message, **_kwargs: messages.append(message))
    monkeypatch.setattr(integration_support.logger, "error", lambda message, **_kwargs: errors.append(message))

    fallback = integration_support._LoggingConsole()
    fallback.print("hello", 42)
    try:
        raise RuntimeError("integration boom")
    except RuntimeError:
        fallback.print_exception()
    assert messages == ["hello 42"]
    assert "integration boom" in errors[0]

    adapter = _Console()
    events: list[str] = []

    @asynccontextmanager
    async def buffer():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    integration_support.configure_runtime_support(console_adapter=adapter, buffer_factory=buffer)
    integration_support.console.print("value", end="")
    integration_support.console.print_exception("failure")
    assert adapter.printed and adapter.exceptions

    assert not integration_support.is_cli_progress_suppressed()
    with integration_support.suppress_cli_progress():
        assert integration_support.is_cli_progress_suppressed()
    assert not integration_support.is_cli_progress_suppressed()

    async def exercise() -> None:
        async with integration_support.buffer_console_logs():
            events.append("body")
        assert await integration_support.prompt_in_thread(lambda value: value + 1, 41) == 42
        integration_support.configure_runtime_support(console_adapter=adapter, buffer_factory=None)
        async with integration_support.buffer_console_logs():
            events.append("plain")

    asyncio.run(exercise())
    assert events == ["enter", "body", "exit", "enter", "exit", "plain"]
