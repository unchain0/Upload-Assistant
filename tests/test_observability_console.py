"""Complete behavioral coverage for CLI observability adapters."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

import pytest

import src.integrations.observability.console as console_module
from src.integrations.observability.runtime_support import suppress_cli_progress
from src.integrations.observability.terminal_link_formatting import format_terminal_link, should_embed_links


class _Progress:
    instances: ClassVar[list[_Progress]] = []

    def __init__(self, *columns: object, **kwargs: object) -> None:
        self.columns = columns
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def test_terminal_link_modes_escape_markup_and_invalid_urls() -> None:
    assert should_embed_links({"embed_links": False}) is False
    assert should_embed_links({"embed_links": True}) is True
    assert should_embed_links({"embed_dupe_links": False}) is False
    assert should_embed_links({}) is True

    linked = format_terminal_link("[Release]", "https://example.invalid/a path?q=one two#fragment value", {"embed_links": True})
    assert linked.startswith("[link=https://example.invalid/a%20path")
    assert "[Release]" in linked
    assert "%20" in linked
    plain = format_terminal_link("ignored", "https://example.invalid/[release]", {"embed_links": False})
    assert plain == r"https://example.invalid/\[release]"
    malformed = format_terminal_link("bad", "http://[invalid", {"embed_links": True})
    assert malformed.startswith("[link=http://%5Binvalid]")


def test_progress_display_shares_live_instance_and_honors_disabled_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _Progress.instances = []
    monkeypatch.setattr(console_module, "Progress", _Progress)
    console_module._shared_progress = None
    console_module._shared_progress_users = 0

    with console_module.progress_display("first") as first:
        with console_module.progress_display("second") as second:
            assert first is second
            assert console_module._shared_progress_users == 2
        assert first.stopped == 0
    assert first.started == 1 and first.stopped == 1
    assert console_module._shared_progress is None

    with console_module.progress_display(disable=True) as disabled:
        assert disabled.kwargs["disable"] is True
    assert disabled.started == 1 and disabled.stopped == 1

    with suppress_cli_progress(), console_module.progress_display() as suppressed:
        assert suppressed.kwargs["disable"] is True


def test_console_configuration_buffering_and_loop_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    console_module.configure_console(
        {
            "console_show_time": True,
            "console_show_level": True,
            "console_show_path": True,
            "console_markup": False,
            "write_log": True,
        }
    )
    render = console_module.rich_handler._log_render  # pyright: ignore[reportPrivateUsage]
    assert render.show_time and render.show_level and render.show_path
    assert console_module.rich_handler.markup is False
    assert console_module._write_log_enabled is True

    handler = console_module.LogBufferHandler()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)
    handler.emit(record)
    assert handler.buffer == [record]

    handled: list[str] = []
    monkeypatch.setattr(console_module.rich_handler, "handle", lambda item: handled.append(item.getMessage()))

    async def exercise() -> None:
        first = console_module._get_log_buffer_lock()
        assert first is console_module._get_log_buffer_lock()
        async with console_module.buffer_console_logs():
            console_module.logger.info("buffered")
        assert await console_module.prompt_in_thread(lambda value: value + 1, 41) == 42

    asyncio.run(exercise())
    assert "buffered" in handled

    class RunningLoop:
        def is_running(self) -> bool:
            return True

    console_module._log_buffer_loop = RunningLoop()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="concurrent event loops"):
        asyncio.run(_get_lock_once())
    console_module._log_buffer_loop = None
    console_module._log_buffer_lock = None


async def _get_lock_once() -> None:
    console_module._get_log_buffer_lock()


def test_log_formatter_and_dynamic_file_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    formatter = console_module.LogFileFormatter(fmt="%(message)s")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "\x1b[31m[bold]message[/bold]\x1b[0m", (), None)
    assert formatter.format(record) == "message"

    handler = console_module.DynamicFileHandler(formatter)
    console_module._write_log_enabled = False
    handler.emit(record)

    console_module._write_log_enabled = True
    token = console_module.current_release_log_path.set(None)
    handler.emit(record)
    console_module.current_release_log_path.reset(token)

    log_path = tmp_path / "nested" / "upload.log"
    token = console_module.current_release_log_path.set(str(log_path))
    handler.emit(record)
    console_module.current_release_log_path.reset(token)
    assert log_path.read_text(encoding="utf-8") == "message\n"

    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "format", lambda _record: (_ for _ in ()).throw(RuntimeError("format failure")))
    monkeypatch.setattr(handler, "handleError", lambda item: errors.append(item))
    token = console_module.current_release_log_path.set(str(log_path))
    handler.emit(record)
    console_module.current_release_log_path.reset(token)
    assert errors == [record]


def test_logging_console_adapter_forwards_to_standard_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.integrations.observability import runtime_support

    info: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(runtime_support.logger, "info", lambda message, **_kwargs: info.append(message))
    monkeypatch.setattr(runtime_support.logger, "error", lambda message, **_kwargs: errors.append(message))
    adapter = runtime_support._LoggingConsole()
    adapter.print("one", 2)
    try:
        raise ValueError("failure")
    except ValueError:
        adapter.print_exception()
    assert info == ["one 2"]
    assert "ValueError: failure" in errors[0]
