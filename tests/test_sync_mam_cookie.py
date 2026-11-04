from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.sync_mam_cookie import (
    cookie_is_valid,
    mask_cookie,
    sync_cookie,
    write_ua_cookie,
)


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


class _Error(Exception):
    pass


class _Opener:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.requests: list[object] = []

    def open(self, request: object, timeout: int = 0) -> _Response:
        self.requests.append(request)
        status = self.statuses.pop(0)
        if status >= 400:
            import urllib.error

            raise urllib.error.HTTPError("url", status, "no", {}, io.BytesIO())
        return _Response(status)


def _database(path: Path, cookie: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE indexer (id INTEGER, settings TEXT)")
        connection.execute(
            "INSERT INTO indexer VALUES (?, ?)",
            (6, json.dumps({"cookie": f"mam_id={cookie}"})),
        )


def test_mask_hides_the_cookie() -> None:
    masked = mask_cookie("secret-cookie-value")
    assert "secret" not in masked
    assert masked == "mam_id(19 chars)"


def test_invalid_cookie_is_not_written(tmp_path: Path) -> None:
    database = tmp_path / "autobrr.db"
    _database(database, "expired")
    config = tmp_path / "config.py"
    config.write_text('config = {"DEFAULT": {"mam_api_key": "old"}}\n')
    opener = _Opener([403])

    assert (
        sync_cookie(
            database=database,
            autobrr_indexer=6,
            config_path=config,
            prowlarr_url="",
            prowlarr_key="",
            prowlarr_indexer=33,
            check_only=False,
            opener=opener,
        )
        == 2
    )
    assert config.read_text() == 'config = {"DEFAULT": {"mam_api_key": "old"}}\n'


def test_valid_cookie_updates_the_config(tmp_path: Path) -> None:
    database = tmp_path / "autobrr.db"
    _database(database, "fresh")
    config = tmp_path / "config.py"
    config.write_text('config = {"DEFAULT": {"mam_api_key": "old"}}\n')

    assert cookie_is_valid("fresh", _Opener([200]))
    assert (
        sync_cookie(
            database=database,
            autobrr_indexer=6,
            config_path=config,
            prowlarr_url="",
            prowlarr_key="",
            prowlarr_indexer=33,
            check_only=False,
            opener=_Opener([200]),
        )
        == 0
    )
    assert '"mam_api_key": "fresh"' in config.read_text()


def test_check_mode_does_not_write(tmp_path: Path) -> None:
    database = tmp_path / "autobrr.db"
    _database(database, "fresh")
    config = tmp_path / "config.py"
    original = 'config = {"DEFAULT": {"mam_api_key": "old"}}\n'
    config.write_text(original)

    assert (
        sync_cookie(
            database=database,
            autobrr_indexer=6,
            config_path=config,
            prowlarr_url="",
            prowlarr_key="",
            prowlarr_indexer=33,
            check_only=True,
            opener=_Opener([200]),
        )
        == 0
    )
    assert config.read_text() == original


def test_write_refuses_a_missing_key(tmp_path: Path) -> None:
    config = tmp_path / "config.py"
    config.write_text("config = {}\n")
    with pytest.raises(LookupError):
        write_ua_cookie(config, "fresh")


def test_auth_rejection_refreshes_the_cookie_once(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from src.integrations.external_apis.myanonamouse import MyAnonamouseManager
    from src.services import book_preparation as books

    calls = {"refresh": 0}

    def refresh() -> bool:
        calls["refresh"] += 1
        return True

    class Manager(MyAnonamouseManager):
        def __init__(self) -> None:
            super().__init__()
            self.searches = 0

        async def search_by_id(self, *_args, **_kwargs):
            self.searches += 1
            if self.searches == 1:
                self.auth_rejected = True
                return None
            return {"title": "Book"}

    manager = Manager()
    monkeypatch.setattr(books, "_refresh_mam_cookie", refresh)
    monkeypatch.setattr(
        "src.integrations.external_apis.myanonamouse.myanonamouse_manager",
        manager,
        raising=False,
    )
    import src.integrations.external_apis.myanonamouse as mam

    monkeypatch.setattr(mam, "myanonamouse_manager", manager)
    ctx = books._BookPrepContext.__new__(books._BookPrepContext)
    ctx.base_dir = str(tmp_path)
    ctx.config = {"DEFAULT": {"mam_api_key": "old"}}

    result = asyncio.run(books._fetch_mam_metadata(ctx, "123"))

    assert result == {"title": "Book"}
    assert calls["refresh"] == 1
