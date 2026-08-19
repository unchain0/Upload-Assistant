"""Edge contracts for the small integration adapters."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import pytest
from rich.progress import Progress

import src.integrations.observability.console as console_module
from src.domain_models.configuration import ApplicationConfiguration, ConfigurationSource, ConfigurationSourceKind
from src.domain_models.errors import ConfigurationNotFoundError, ConfigurationSyntaxError
from src.domain_models.release import Meta
from src.integrations.cache.metadata_cache import MetadataCache, is_cache_miss
from src.integrations.configuration.python_config_repository import PythonConfigurationRepository
from src.integrations.mapping.value_coercion import to_int
from src.integrations.observability.console import DynamicFileHandler, LogFileFormatter, buffer_console_logs, configure_console, progress_display
from src.integrations.observability.terminal_link_formatting import format_terminal_link
from src.integrations.security.redaction import Redaction, clean_meta_for_export, extract_json_blocks, redact_private_info, redact_value


class _Response:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.content = b"poster"
        self._payload = payload or {"files": [{"url": "https://uguu.invalid/package.tar"}]}

    def json(self) -> dict[str, Any]:
        return self._payload


class _AsyncClient:
    response = _Response()

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return self.response

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return self.response


def _configuration(path: Path) -> ApplicationConfiguration:
    return ApplicationConfiguration.from_mapping(
        {"DEFAULT": {"tmdb_api": "key", "nested": {"value": [1, 2]}}},
        ConfigurationSource(str(path), ConfigurationSourceKind.RUNTIME),
    )


def test_value_coercion_covers_every_supported_input() -> None:
    assert to_int(True) == 1
    assert to_int(False) == 0
    assert to_int(4) == 4
    assert to_int(4.9) == 4
    assert to_int("5") == 5
    assert to_int("bad", 7) == 7
    assert to_int(float("inf"), 8) == 8
    assert to_int(object(), 9) == 9


def test_python_config_repository_reports_all_invalid_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = PythonConfigurationRepository()
    missing = tmp_path / "missing.py"
    with pytest.raises(ConfigurationNotFoundError):
        repository.load(missing, ConfigurationSourceKind.RUNTIME)
    with pytest.raises(ConfigurationNotFoundError):
        repository.copy_atomically(missing, tmp_path / "copy.py")

    unreadable = tmp_path / "unreadable.py"
    unreadable.write_text("config = {}", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == unreadable.resolve():
            raise OSError("denied")
        return original_read_text(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(ConfigurationNotFoundError, match="denied"):
        repository.load(unreadable, ConfigurationSourceKind.RUNTIME)
    monkeypatch.setattr(Path, "read_text", original_read_text)

    cases = {
        "syntax.py": "config = {",
        "missing_assignment.py": "value = {}",
        "non_literal.py": "config = dict()",
        "not_dict.py": "config = []",
        "bad_section.py": "config = {'DEFAULT': 1}",
    }
    for filename, source in cases.items():
        path = tmp_path / filename
        path.write_text(source, encoding="utf-8")
        with pytest.raises(ConfigurationSyntaxError):
            repository.load(path, ConfigurationSourceKind.RUNTIME)

    annotated = tmp_path / "annotated.py"
    annotated.write_text("config: dict[str, object] = {'DEFAULT': {'value': 1}}", encoding="utf-8")
    assert repository.load(annotated, ConfigurationSourceKind.RUNTIME).section("DEFAULT")["value"] == 1


def test_python_config_repository_atomic_copy_and_write_backups(tmp_path: Path) -> None:
    repository = PythonConfigurationRepository()
    source = tmp_path / "source.py"
    source.write_text("config = {'DEFAULT': {'value': 'new'}}\n", encoding="utf-8")
    source.chmod(0o640)
    destination = tmp_path / "runtime" / "config.py"
    destination.parent.mkdir()
    destination.write_text("config = {'DEFAULT': {'value': 'old'}}\n", encoding="utf-8")

    backup = repository.copy_atomically(source, destination)
    assert backup is not None and backup.read_text(encoding="utf-8").endswith("'old'}}\n")
    assert destination.read_bytes() == source.read_bytes()
    assert repository.copy_atomically(source, destination) is None

    fresh = tmp_path / "fresh" / "config.py"
    repository.write_atomically(_configuration(source), fresh)
    assert fresh.stat().st_mode & 0o777 == 0o600
    repository.write_atomically(_configuration(source), fresh)
    assert fresh.with_name("config.py.pre-masa.bak").is_file()
    assert repository.load(fresh, ConfigurationSourceKind.RUNTIME).section("DEFAULT")["tmdb_api"] == "key"


def test_metadata_cache_handles_invalid_settings_serialization_and_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "DEFAULT": {
            "metadata_cache_default_ttl_hours": object(),
            "metadata_cache_negative_ttl_minutes": object(),
            "metadata_cache_services": {"tmdb": "invalid", "imdb": {"ttl_hours": object()}},
        }
    }
    cache = MetadataCache(tmp_path, config)
    assert cache.default_ttl == 168 * 3600
    assert cache.negative_ttl == 60 * 60
    assert cache.ttl("tmdb", "movie", negative=True) == 60 * 60
    assert cache.ttl("imdb", "movie") == cache.default_ttl

    async def exercise() -> None:
        zero = MetadataCache(tmp_path, {"DEFAULT": {"metadata_cache_default_ttl_hours": 0}})
        await zero.set("tmdb", "movie", "zero", {"id": 1})
        assert not zero._path("tmdb", "movie", "zero").exists()

        await cache.set("tmdb", "movie", "unserializable", {"bad": object()})
        assert not cache._path("tmdb", "movie", "unserializable").exists()

        original_replace = Path.replace

        def fail_replace(path: Path, _destination: Path) -> Path:
            if path.suffix.startswith(".tmp") or ".tmp." in path.name:
                raise OSError("disk full")
            return original_replace(path, _destination)

        monkeypatch.setattr(Path, "replace", fail_replace)
        await cache.set("tmdb", "movie", "io-error", {"id": 1})
        assert is_cache_miss(await cache.get("tmdb", "movie", "io-error"))

    asyncio.run(exercise())


def test_terminal_link_falls_back_for_invalid_ipv6_url() -> None:
    value = format_terminal_link("Open [now]", "https://[broken/path?q=1", {"embed_links": True})
    assert value.startswith("[link=https://")
    assert "Open \\[now]" in value


def test_console_progress_buffer_and_dynamic_file_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[int] = []
    stops: list[int] = []
    monkeypatch.setattr(Progress, "start", lambda self: starts.append(id(self)))
    monkeypatch.setattr(Progress, "stop", lambda self: stops.append(id(self)))

    with progress_display() as first, progress_display() as second:
        assert first is second
    with progress_display(disable=True) as disabled:
        assert disabled is not first
    monkeypatch.setattr(console_module, "is_cli_progress_suppressed", lambda: True)
    with progress_display() as suppressed:
        assert suppressed is not first
    assert len(starts) == len(stops) == 3

    async def buffer_records() -> None:
        async with buffer_console_logs():
            console_module.logger.info("buffered message")

    asyncio.run(buffer_records())

    class _RunningLoop:
        def is_running(self) -> bool:
            return True

    async def concurrent_loop_error() -> None:
        monkeypatch.setattr(console_module, "_log_buffer_loop", _RunningLoop())
        with pytest.raises(RuntimeError, match="concurrent event loops"):
            console_module._get_log_buffer_lock()
        monkeypatch.setattr(console_module, "_log_buffer_loop", None)
        monkeypatch.setattr(console_module, "_log_buffer_lock", None)

    asyncio.run(concurrent_loop_error())

    formatter = LogFileFormatter()
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "\x1b[31m[bold]message[/bold]", (), None)
    assert "message" in formatter.format(record)
    monkeypatch.setattr(formatter.console, "render_str", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("markup")))
    assert "message" in formatter.format(record)

    handler = DynamicFileHandler(formatter)
    configure_console({"write_log": False})
    handler.emit(record)
    configure_console({"write_log": True, "console_show_time": True, "console_show_level": True, "console_show_path": True, "console_markup": False})
    console_module.current_release_log_path.set(None)
    handler.emit(record)
    log_path = tmp_path / "logs" / "upload.log"
    console_module.current_release_log_path.set(str(log_path))
    handler.emit(record)
    assert "message" in log_path.read_text(encoding="utf-8")

    errors: list[logging.LogRecord] = []
    monkeypatch.setattr(handler, "handleError", errors.append)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")))
    handler.emit(record)
    assert errors == [record]
    configure_console({"write_log": False})
    console_module.current_release_log_path.set(None)


def test_redaction_handles_nested_json_invalid_blocks_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = 'prefix {"token":"secret","nested":[{"value":"ok"}]} suffix [1,{"password":"x"}] trailing {'
    blocks = extract_json_blocks(text)
    assert len(blocks) == 2
    assert Redaction.extract_json_blocks('{"escaped":"a\\"b"}') == [(0, 18)]
    assert Redaction.extract_json_blocks('"unterminated') == []

    redacted = redact_value("https://tracker.invalid/abcdefghijkl/announce?api_key=secret " + text)
    assert "secret" not in redacted
    assert "[REDACTED]" in redacted
    assert redact_value(5) == 5
    assert "[REDACTED]" in redact_private_info('[{"passkey":"x"}]')
    assert redact_private_info([{"token": "x"}, 3])[0]["token"] == "[REDACTED]"
    assert redact_private_info(5) == 5

    original = Redaction.redact_private_info
    monkeypatch.setattr(Redaction, "redact_private_info", lambda *_args, **_kwargs: object())
    assert "token" in Redaction.redact_value('{"token":"x"}')
    monkeypatch.setattr(Redaction, "redact_private_info", original)
    assert Redaction.redact_value("{not json}") == "{not json}"

    meta = Meta(
        base_dir=str(tmp_path),
        uuid="redaction",
        tracker_status={"BHD": {"status": "ready", "status_message": "secret"}},
        torrent_comments={"BHD": "secret"},
        matched_episode_ids=[1],
        archive_password="".join(("sec", "ret")),
    )
    result = asyncio.run(clean_meta_for_export(meta))
    assert result.tracker_status["BHD"] == {"status": "ready"}
    exported = json.loads((tmp_path / "tmp" / "redaction" / "meta.json").read_text(encoding="utf-8"))
    assert "archive_password" not in exported
