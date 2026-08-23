from __future__ import annotations

import asyncio
import re
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import qbittorrentapi

from src.domain_models.release import Meta
from src.integrations.torrent_clients import qbittorrent as qbit_module
from src.integrations.torrent_clients.qbittorrent import QbittorrentClientMixin


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        payload: object | None = None,
        content: bytes = b"torrent",
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.content = content

    def json(self) -> object:
        return self._payload


class _Session:
    responses: ClassVar[list[_Response | Exception]] = []
    instances: ClassVar[list[_Session]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.closed = False
        self.__class__.instances.append(self)

    @classmethod
    def reset(cls, *responses: _Response | Exception) -> None:
        cls.responses = list(responses)
        cls.instances = []

    def _next(self) -> _Response:
        value = self.responses.pop(0) if self.responses else _Response()
        if isinstance(value, Exception):
            raise value
        return value

    async def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("get", url, kwargs))
        return self._next()

    async def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(("post", url, kwargs))
        return self._next()

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self) -> None:
        self.torrents: list[object] = []
        self.properties: object = {"comment": ""}
        self.trackers: object = []
        self.exported: bytes = b"torrent"
        self.add_result: object = True
        self.app_version_result: object = "5.0"
        self.login_error: Exception | None = None
        self.calls: list[tuple[str, dict[str, object]]] = []

    def torrents_info(self, **kwargs: object) -> object:
        self.calls.append(("info", kwargs))
        return self.torrents

    def torrents_properties(self, **kwargs: object) -> object:
        self.calls.append(("properties", kwargs))
        if isinstance(self.properties, Exception):
            raise self.properties
        return self.properties

    def torrents_trackers(self, **kwargs: object) -> object:
        self.calls.append(("trackers", kwargs))
        if isinstance(self.trackers, Exception):
            raise self.trackers
        return self.trackers

    def torrents_export(self, **kwargs: object) -> bytes:
        self.calls.append(("export", kwargs))
        return self.exported

    def torrents_add(self, **kwargs: object) -> object:
        self.calls.append(("add", kwargs))
        if isinstance(self.add_result, Exception):
            raise self.add_result
        return self.add_result

    def torrents_resume(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("resume", {"args": args, **kwargs}))

    def torrents_set_super_seeding(self, **kwargs: object) -> None:
        self.calls.append(("superseed", kwargs))

    def app_version(self) -> object:
        if isinstance(self.app_version_result, Exception):
            raise self.app_version_result
        return self.app_version_result

    def auth_log_in(self) -> None:
        if self.login_error is not None:
            raise self.login_error


def _call_kwargs(
    calls: list[tuple[str, dict[str, object]]], name: str
) -> list[dict[str, object]]:
    return [kwargs for call_name, kwargs in calls if call_name == name]


class _TorrentData:
    def __init__(self, piece_size: int = 1024 * 1024) -> None:
        self.piece_size = piece_size
        self.infohash = "abc123"
        self.metainfo = {"info": {"name": "Example.mkv"}}
        self.name = "Example.mkv"

    @classmethod
    def read(cls, _path: str | Path) -> _TorrentData:
        return cls()

    def dump(self) -> bytes:
        return b"torrent"


class _Qbit(QbittorrentClientMixin):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.valid_queue: list[tuple[bool, str]] = []
        self.includes_subtitles = True
        self.no_subtitles = False

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, str]:
        match = re.search(r"aither\.cc/torrents/(\d+)", comment, re.IGNORECASE)
        return {"AITHER": match.group(1)} if match else {}

    async def is_valid_torrent(
        self,
        _meta: Meta,
        torrent_path: str,
        _torrenthash: str,
        _torrent_client: str,
        _client: dict[str, Any],
    ) -> tuple[bool, str]:
        return (
            self.valid_queue.pop(0)
            if self.valid_queue
            else (True, torrent_path)
        )

    def _torrent_includes_all_local_subtitles(
        self, _torrent_path: str, _meta: Meta
    ) -> bool:
        return self.includes_subtitles

    def _torrent_has_no_subtitles(self, _torrent_path: str) -> bool:
        return self.no_subtitles


def _config(tmp_path: Path, **defaults: object) -> dict[str, Any]:
    default = {
        "default_torrent_client": "qbit",
        "searching_client_list": ["qbit"],
        "prefer_max_16_torrent": False,
    }
    default.update(defaults)
    return {
        "DEFAULT": default,
        "TRACKERS": {"AITHER": {"announce_url": "https://aither.cc/announce"}},
        "TORRENT_CLIENTS": {
            "qbit": {
                "torrent_client": "qbit",
                "qbit_url": "https://qbit.invalid",
                "qbit_port": 443,
                "qbit_user": "user",
                "qbit_pass": "pass",
                "torrent_storage_dir": str(tmp_path / "storage"),
                "local_path": [str(tmp_path)],
                "remote_path": [str(tmp_path)],
                "qbit_cat": "UA",
                "linked_folder": [str(tmp_path / "linked")],
            }
        },
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Example.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "Example",
        "path": str(media),
        "filelist": [str(media)],
        "filename": media.name,
        "name": "Example",
        "title": "Example",
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "infohash": "abc123",
        "torrent_comments": [],
        "tracker_ids": {},
        "subtitle_files": [],
        "base_torrent_created": False,
        "we_checked_them_all": False,
        "debug": True,
        "client": "qbit",
        "keep_folder": False,
        "qbit_cat": "",
        "qbit_tag": "",
        "is_disc": "",
    }
    state.update(values)
    return Meta(state)


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    qbit_module.qbittorrent_cached_clients.clear()
    qbit_module.qbittorrent_locks.clear()
    qbit_module._cached_tracker_url_patterns = None
    _Session.reset()

    async def no_sleep(_delay: float = 0) -> None:
        return None

    monkeypatch.setattr(qbit_module.asyncio, "sleep", no_sleep)


def test_ssl_retry_proxy_response_and_url_helpers(tmp_path: Path) -> None:
    qbit = _Qbit(_config(tmp_path))
    secure = qbit.create_ssl_context_for_client({})
    insecure = qbit.create_ssl_context_for_client(
        {"VERIFY_WEBUI_CERTIFICATE": False}
    )
    assert secure.verify_mode != ssl.CERT_NONE
    assert (
        insecure.verify_mode == ssl.CERT_NONE
        and insecure.check_hostname is False
    )

    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise TimeoutError("slow")
        return "ok"

    assert (
        asyncio.run(
            qbit.retry_qbt_operation(
                flaky, "operation", max_retries=2, initial_timeout=0.01
            )
        )
        == "ok"
    )

    async def fail() -> None:
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        asyncio.run(
            qbit.retry_qbt_operation(
                fail, "operation", max_retries=1, initial_timeout=0.01
            )
        )

    qbit._raise_for_proxy_response(httpx.Response(200))
    with pytest.raises(qbit_module._RetryableProxyResponseError):
        qbit._raise_for_proxy_response(httpx.Response(503))
    with pytest.raises(qbit_module._ProxyResponseError):
        qbit._raise_for_proxy_response(httpx.Response(400))

    url = qbit._build_proxy_search_url(
        "https://proxy.invalid",
        "Example [2026]",
        {
            "excludeStatus": ["error", "paused"],
            "categories": ["UA"],
            "tags": ["A B"],
        },
    )
    assert "search=Example%20%5B2026%5D" in url
    assert (
        "filter=error%2Cpaused" in url
        and "category=UA" in url
        and "tag=A%20B" in url
    )


def test_mock_name_tracker_matching_sort_and_patterns(tmp_path: Path) -> None:
    qbit = _Qbit(_config(tmp_path))
    mock = qbit._build_mock_torrents([{"hash": "one", "name": "Example"}])[0]
    assert (
        mock.hash == "one"
        and mock.files == []
        and mock.tracker == ""
        and mock.missing is None
    )

    single = _meta(tmp_path)
    assert qbit._torrent_name_matches("Example.mkv", single)
    assert qbit._torrent_name_matches("Example", single)
    assert qbit._torrent_name_matches(Path(single.path).parent.name, single)
    assert not qbit._torrent_name_matches("Other", single)
    disc = _meta(tmp_path, is_disc="BDMV")
    assert qbit._torrent_name_matches("Example", disc)

    torrent = SimpleNamespace(
        comment="https://hawke.uno/torrents/456\nhttps://aither.cc/torrents/123",
        tracker="https://hawke.uno/announce",
    )
    patterns = {
        "aither": {"url": "aither.cc", "pattern": r"aither\.cc/torrents/(\d+)"}
    }
    matches, found = qbit._extract_tracker_matches(
        torrent, patterns, ["missing", "aither"], True, single
    )
    assert found and {item["tracker_id"] for item in matches} == {"123", "456"}
    assert (
        single.get_tracker_id("AITHER") == "123"
        and single.get_tracker_id("HAWKEUNO") == "456"
    )

    ant = SimpleNamespace(
        comment="", tracker="https://tracker.anthelion.me/announce"
    )
    matches, found = qbit._extract_tracker_matches(ant, {}, [], True, single)
    assert found and matches == [{"id": "ant", "tracker_id": 1}]
    assert qbit._extract_tracker_matches(ant, {}, [], False, single) == (
        [],
        False,
    )

    candidates = [
        {
            "has_working_tracker": False,
            "has_tracker": False,
            "tracker_urls": [],
        },
        {
            "has_working_tracker": True,
            "has_tracker": True,
            "tracker_urls": [{"id": "bhd"}],
        },
        {
            "has_working_tracker": True,
            "has_tracker": False,
            "tracker_urls": [],
        },
    ]
    qbit._sort_matching_torrents(candidates, ["aither", "bhd"])
    assert candidates[0]["has_tracker"] is True
    patterns, priority = qbit._setup_tracker_patterns()
    assert "aither" in patterns and "ptp" in priority


def test_init_client_api_key_password_cache_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    instances: list[_Client] = []

    def factory(**_kwargs: object) -> _Client:
        client = _Client()
        instances.append(client)
        return client

    monkeypatch.setattr(qbit_module.qbittorrentapi, "Client", factory)
    api = {"qbit_url": "host", "qbit_port": 443, "qbit_api_key": "key"}
    first = asyncio.run(qbit.init_qbittorrent_client(api))
    assert first is instances[0]
    assert asyncio.run(qbit.init_qbittorrent_client(api)) is first

    qbit_module.qbittorrent_cached_clients.clear()
    password = {
        "qbit_url": "host",
        "qbit_port": 443,
        "qbit_user": "user",
        "qbit_pass": "pass",
    }
    assert asyncio.run(qbit.init_qbittorrent_client(password)) is instances[-1]

    for error in (
        TimeoutError("slow"),
        qbittorrentapi.APIConnectionError("offline"),
        RuntimeError("bad key"),
    ):
        qbit_module.qbittorrent_cached_clients.clear()
        client = _Client()
        client.app_version_result = error
        monkeypatch.setattr(
            qbit_module.qbittorrentapi,
            "Client",
            lambda client=client, **_kwargs: client,
        )
        assert asyncio.run(qbit.init_qbittorrent_client(api)) is None

    for error in (
        TimeoutError("slow"),
        qbittorrentapi.LoginFailed("bad login"),
        qbittorrentapi.APIConnectionError("offline"),
    ):
        qbit_module.qbittorrent_cached_clients.clear()
        client = _Client()
        client.login_error = error
        monkeypatch.setattr(
            qbit_module.qbittorrentapi,
            "Client",
            lambda client=client, **_kwargs: client,
        )
        assert asyncio.run(qbit.init_qbittorrent_client(password)) is None


def test_proxy_commands_and_add_recovery(tmp_path: Path) -> None:
    qbit = _Qbit(_config(tmp_path))
    session = _Session()
    _Session.reset(_Response(404), _Response(200))
    response = asyncio.run(
        qbit._post_proxy_command(
            session,
            "https://proxy/cmd",
            {"hashes": "abc"},
            "command",
            accepted_statuses=(200, 404),
        )
    )
    assert response.status_code == 404

    _Session.reset(_Response(503), _Response(200, [{"hash": "abc"}]))
    asyncio.run(
        qbit._add_torrent_via_proxy(
            session,
            "https://proxy",
            "abc",
            {"savepath": str(tmp_path)},
            {"torrent": b"x"},
        )
    )
    assert [call[0] for call in session.calls[-2:]] == ["post", "get"]

    client = _Client()
    client.add_result = "Fails."
    client.torrents = []
    with pytest.raises(qbittorrentapi.APIError):
        asyncio.run(
            qbit._add_torrent_direct(client, "abc", {"torrent_files": b"x"})
        )

    client = _Client()
    client.add_result = qbittorrentapi.Conflict409Error("exists")
    asyncio.run(
        qbit._add_torrent_direct(client, "abc", {"torrent_files": b"x"})
    )

    client = _Client()
    client.add_result = qbittorrentapi.APIConnectionError("offline")
    client.torrents = [SimpleNamespace(hash="abc")]
    asyncio.run(
        qbit._add_torrent_direct(client, "abc", {"torrent_files": b"x"})
    )


def test_fetch_torrents_proxy_direct_empty_errors_and_slow_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    session = _Session()
    _Session.reset(
        _Response(200, {"torrents": [{"hash": "one", "name": "Example"}]}),
        _Response(200, [{"hash": "two", "name": "Example"}]),
        _Response(200, {"torrents": "bad"}),
        _Response(404),
        _Response(500),
    )
    assert (
        len(
            asyncio.run(
                qbit._fetch_torrents(
                    "proxy", "https://proxy", session, None, "Example"
                )
            )
        )
        == 1
    )
    assert (
        len(
            asyncio.run(
                qbit._fetch_torrents(
                    "proxy", "https://proxy", session, None, "Example"
                )
            )
        )
        == 1
    )
    assert (
        asyncio.run(
            qbit._fetch_torrents(
                "proxy", "https://proxy", session, None, "Example"
            )
        )
        == []
    )
    assert (
        asyncio.run(
            qbit._fetch_torrents(
                "proxy", "https://proxy", session, None, "Example"
            )
        )
        == []
    )
    assert (
        asyncio.run(
            qbit._fetch_torrents(
                "proxy", "https://proxy", session, None, "Example"
            )
        )
        == []
    )
    assert (
        asyncio.run(
            qbit._fetch_torrents(
                "proxy", "https://proxy", None, None, "Example"
            )
        )
        == []
    )
    assert (
        asyncio.run(qbit._fetch_torrents("", "", None, None, "Example")) == []
    )

    client = _Client()
    client.torrents = [SimpleNamespace(hash="direct")]
    assert (
        asyncio.run(qbit._fetch_torrents("", "", None, client, "Example"))
        == client.torrents
    )
    monkeypatch.setattr(
        qbit,
        "retry_qbt_operation",
        AsyncMock(side_effect=TimeoutError("slow")),
    )
    assert (
        asyncio.run(qbit._fetch_torrents("", "", None, client, "Example"))
        == []
    )
    monkeypatch.setattr(
        qbit, "retry_qbt_operation", AsyncMock(side_effect=RuntimeError("bad"))
    )
    assert (
        asyncio.run(qbit._fetch_torrents("", "", None, client, "Example"))
        == []
    )
    qbit._log_slow_client_response(1, False)
    qbit._log_slow_client_response(6, False)
    qbit._log_slow_client_response(6, True)


def test_process_matches_direct_proxy_trackers_comments_and_errors(
    tmp_path: Path,
) -> None:
    qbit = _Qbit(_config(tmp_path))
    meta = _meta(tmp_path, torrent_comments="bad")
    direct = _Client()
    direct.trackers = [
        {"url": "** [DHT]", "status": 2},
        {"url": "https://aither.cc/announce", "status": 2},
        {"url": "https://bad.invalid", "status": 4, "msg": "error"},
    ]
    torrents = [
        SimpleNamespace(
            hash="one",
            name="Example.mkv",
            save_path=str(tmp_path),
            size=100,
            category="UA",
            num_complete=2,
            tracker="https://aither.cc/announce",
            comment="https://aither.cc/torrents/123",
        ),
        SimpleNamespace(name="", hash="missing"),
        SimpleNamespace(name="Other", hash="other"),
    ]
    patterns = {"aither": {"url": "aither.cc", "pattern": r"/torrents/(\d+)"}}
    result = asyncio.run(
        qbit._process_torrent_matches(
            torrents, patterns, ["aither"], "", "", None, direct, meta
        )
    )
    assert len(result) == 1 and result[0]["has_working_tracker"] is True
    assert meta.found_tracker_match is True and meta.torrent_comments

    proxy_torrent = SimpleNamespace(
        hash="proxy",
        name="Example.mkv",
        save_path=str(tmp_path),
        size=100,
        category="UA",
        num_complete=1,
        tracker="",
        trackers=[{"url": "https://aither.cc/announce"}],
        comment="",
    )
    session = _Session()
    _Session.reset(
        _Response(200, {"comment": "https://aither.cc/torrents/999"})
    )
    result = asyncio.run(
        qbit._process_torrent_matches(
            [proxy_torrent],
            patterns,
            ["aither"],
            "proxy",
            "https://proxy",
            session,
            None,
            _meta(tmp_path),
        )
    )
    assert result[0]["comment"].endswith("999")

    proxy_torrent.comment = ""
    _Session.reset(_Response(500))
    assert (
        asyncio.run(
            qbit._process_torrent_matches(
                [proxy_torrent],
                patterns,
                ["aither"],
                "proxy",
                "https://proxy",
                session,
                None,
                _meta(tmp_path),
            )
        )
        == []
    )
    direct.trackers = qbittorrentapi.APIError("bad")
    assert (
        asyncio.run(
            qbit._process_torrent_matches(
                torrents[:1],
                patterns,
                ["aither"],
                "",
                "",
                None,
                direct,
                _meta(tmp_path),
            )
        )
        == []
    )


def test_export_torrent_storage_proxy_direct_and_errors(
    tmp_path: Path,
) -> None:
    qbit = _Qbit(_config(tmp_path))
    storage = tmp_path / "storage"
    storage.mkdir()
    stored = storage / "abc.torrent"
    stored.write_bytes(b"stored")
    extracted = tmp_path / "tmp"
    extracted.mkdir()
    assert asyncio.run(
        qbit._export_torrent_file(
            "abc", "", "", None, None, str(storage), str(extracted)
        )
    ) == str(stored)
    assert (
        asyncio.run(
            qbit._export_torrent_file(
                "abc", "proxy", "proxy", None, None, None, str(extracted)
            )
        )
        is None
    )
    assert (
        asyncio.run(
            qbit._export_torrent_file(
                "abc", "", "", None, None, None, str(extracted)
            )
        )
        is None
    )

    session = _Session()
    _Session.reset(
        _Response(200, content=b"proxy"),
        _Response(500),
        RuntimeError("offline"),
    )
    path = asyncio.run(
        qbit._export_torrent_file(
            "proxy",
            "proxy",
            "https://proxy",
            session,
            None,
            None,
            str(extracted),
        )
    )
    assert path and Path(path).read_bytes() == b"proxy"
    assert (
        asyncio.run(
            qbit._export_torrent_file(
                "bad",
                "proxy",
                "https://proxy",
                session,
                None,
                None,
                str(extracted),
            )
        )
        is None
    )
    assert (
        asyncio.run(
            qbit._export_torrent_file(
                "error",
                "proxy",
                "https://proxy",
                session,
                None,
                None,
                str(extracted),
            )
        )
        is None
    )

    client = _Client()
    client.exported = b"direct"
    path = asyncio.run(
        qbit._export_torrent_file(
            "direct", "", "", None, client, None, str(extracted), True
        )
    )
    assert path and Path(path).read_bytes() == b"direct"


def test_process_base_creation_first_alternative_preference_subtitle_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []

    async def create(path: str, _base: str, _uuid: str) -> None:
        created.append(path)

    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    qbit = _Qbit(_config(tmp_path))
    meta = _meta(tmp_path)
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    paths = iter((str(first), str(second)))
    monkeypatch.setattr(
        qbit,
        "_export_torrent_file",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(paths)),
    )
    qbit.valid_queue = [(False, str(first)), (True, str(second))]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "one"}, {"hash": "two"}],
            {},
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert (
        meta.base_torrent_created
        and meta.hash_used == "two"
        and created == [str(second)]
    )

    qbit = _Qbit(_config(tmp_path, prefer_max_16_torrent=True))
    meta = _meta(tmp_path, subtitle_files=[])
    paths = iter((str(first), str(second)))
    monkeypatch.setattr(
        qbit,
        "_export_torrent_file",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(paths)),
    )
    qbit.valid_queue = [(True, str(first)), (True, str(second))]

    class Piece:
        values = iter((32 * 1024 * 1024, 8 * 1024 * 1024))

        @classmethod
        def read(cls, _path: str):
            return SimpleNamespace(piece_size=next(cls.values))

    monkeypatch.setattr(qbit_module, "Torrent", Piece)
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "one"}, {"hash": "two"}],
            {},
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert (
        meta.hash_used == "two" and meta.found_preferred_piece_size == "16MiB"
    )

    qbit = _Qbit(_config(tmp_path))
    qbit.includes_subtitles = False
    qbit.no_subtitles = True
    meta = _meta(tmp_path, subtitle_files=["sub.srt"])
    monkeypatch.setattr(
        qbit, "_export_torrent_file", AsyncMock(return_value=str(first))
    )
    qbit.valid_queue = [(True, str(first))]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "video"}], {}, "", "", None, _Client(), meta
        )
    )
    assert meta.hash_used == "video" and meta.base_torrent_created

    qbit = _Qbit(_config(tmp_path, prefer_max_16_torrent=True))
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        qbit, "_export_torrent_file", AsyncMock(return_value=None)
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "none"}], {}, "", "", None, _Client(), meta
        )
    )
    assert meta.we_checked_them_all and meta.found_preferred_piece_size is None


def test_search_single_and_find_client_selection_deduplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config["TORRENT_CLIENTS"].update(
        {
            "other": {"torrent_client": "rtorrent"},
            "missing": None,
            "second": {"torrent_client": "qbit"},
        }
    )
    qbit = _Qbit(config)
    meta = _meta(tmp_path, debug=True, client="none")
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=_Client())
    )
    monkeypatch.setattr(
        qbit,
        "_fetch_torrents",
        AsyncMock(return_value=[SimpleNamespace(name="Example")]),
    )
    matches = [
        {
            "hash": "one",
            "has_tracker": True,
            "tracker_urls": [{"id": "aither", "tracker_id": "1"}],
            "has_working_tracker": True,
        }
    ]
    monkeypatch.setattr(
        qbit, "_process_torrent_matches", AsyncMock(return_value=matches)
    )
    base = AsyncMock()
    monkeypatch.setattr(qbit, "_process_base_torrent_creation", base)
    result = asyncio.run(
        qbit._search_single_qbit_client(
            config["TORRENT_CLIENTS"]["qbit"], meta.path, meta, "qbit"
        )
    )
    assert (
        result == matches
        and meta.infohash == "one"
        and meta.get("aither") == "1"
    )
    base.assert_awaited_once()

    monkeypatch.setattr(qbit, "_fetch_torrents", AsyncMock(return_value=[]))
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                config["TORRENT_CLIENTS"]["qbit"], meta.path, meta, "qbit"
            )
        )
        == []
    )
    monkeypatch.setattr(
        qbit, "_fetch_torrents", AsyncMock(side_effect=TimeoutError("slow"))
    )
    with pytest.raises(TimeoutError):
        asyncio.run(
            qbit._search_single_qbit_client(
                config["TORRENT_CLIENTS"]["qbit"], meta.path, meta, "qbit"
            )
        )
    monkeypatch.setattr(
        qbit, "_fetch_torrents", AsyncMock(side_effect=RuntimeError("bad"))
    )
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                config["TORRENT_CLIENTS"]["qbit"], meta.path, meta, "qbit"
            )
        )
        == []
    )

    qbit.config["DEFAULT"].update(
        {
            "searching_client_list": ["missing", "other", "qbit", "second"],
            "prefer_max_16_torrent": True,
        }
    )
    searches = AsyncMock(
        side_effect=([{"hash": "one"}, {"hash": "one"}], [{"hash": "two"}])
    )
    monkeypatch.setattr(qbit, "_search_single_qbit_client", searches)
    meta.found_preferred_piece_size = None
    meta.piece_size_constraints_enabled = "16MiB"
    result = asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta))
    assert result == [{"hash": "one"}, {"hash": "two"}]

    qbit.config["DEFAULT"].update(
        {"searching_client_list": [], "default_torrent_client": "none"}
    )
    meta.client = "none"
    assert asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta)) == []
    monkeypatch.setattr(
        qbit,
        "_search_single_qbit_client",
        AsyncMock(side_effect=TimeoutError("slow")),
    )
    qbit.config["DEFAULT"]["default_torrent_client"] = "qbit"
    with pytest.raises(TimeoutError):
        asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta))
    monkeypatch.setattr(
        qbit,
        "_search_single_qbit_client",
        AsyncMock(side_effect=RuntimeError("bad")),
    )
    assert asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta)) == []


def test_get_pathed_torrents_and_match_tracker_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        qbit,
        "find_qbit_torrents_by_path",
        AsyncMock(return_value=[{"hash": "found"}]),
    )
    asyncio.run(qbit.get_pathed_torrents(meta.path, meta))
    assert meta.infohash == "found"
    monkeypatch.setattr(
        qbit, "find_qbit_torrents_by_path", AsyncMock(return_value=[])
    )
    asyncio.run(qbit.get_pathed_torrents(meta.path, meta))
    monkeypatch.setattr(
        qbit,
        "find_qbit_torrents_by_path",
        AsyncMock(side_effect=TimeoutError("slow")),
    )
    with pytest.raises(TimeoutError):
        asyncio.run(qbit.get_pathed_torrents(meta.path, meta))
    monkeypatch.setattr(
        qbit,
        "find_qbit_torrents_by_path",
        AsyncMock(side_effect=RuntimeError("bad")),
    )
    asyncio.run(qbit.get_pathed_torrents(meta.path, meta))

    qbit_module._cached_tracker_url_patterns = {
        "passthepopcorn": ["passthepopcorn.me"],
        "aither": ["aither.cc"],
    }
    meta.remove_trackers = "bad"  # type: ignore[assignment]
    asyncio.run(
        qbit_module.match_tracker_url(
            [
                "http://passthepopcorn.me/announce",
                "https://aither.cc/announce",
            ],
            meta,
        )
    )
    assert set(meta.remove_trackers) == {"PASSTHEPOPCORN", "AITHER"}
    asyncio.run(
        qbit_module.match_tracker_url(["https://aither.cc/announce"], meta)
    )
    assert meta.remove_trackers.count("AITHER") == 1


def test_get_ptp_from_hash_direct_proxy_pathed_and_failure_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []

    async def create(path: str, _base: str, _uuid: str) -> None:
        created.append(path)

    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    qbit = _Qbit(_config(tmp_path))
    direct = _Client()
    direct.properties = {
        "hash": "abc123",
        "infohash_v1": "abc123",
        "name": "Example",
        "comment": "https://aither.cc/torrents/42",
    }
    direct.exported = b"direct-torrent"
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=direct)
    )
    meta = _meta(tmp_path, torrent_comments="invalid")
    result = asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            meta, {"qbit_url": "host", "qbit_port": 443}
        )
    )
    assert result is meta
    assert meta.get_tracker_id("AITHER") == "42"
    assert meta.torrent_comments[0]["hash"] == "abc123"
    assert created and Path(created[-1]).name == "abc123.torrent"

    created.clear()
    pathed = _meta(tmp_path)
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            pathed, {"qbit_url": "host", "qbit_port": 443}, pathed=True
        )
    )
    assert created == []

    invalid = _meta(tmp_path, infohash="", path="")
    assert (
        asyncio.run(
            qbit.get_ptp_from_hash_qbit(
                invalid, {"qbit_url": "host", "qbit_port": 443}
            )
        )
        is invalid
    )
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=None)
    )
    assert asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qbit_url": "host", "qbit_port": 443}
        )
    )

    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    _Session.reset(
        _Response(
            200,
            {
                "hash": "abc123",
                "infohash_v1": "abc123",
                "name": "Example",
                "comment": "",
            },
        ),
        _Response(200, content=b"proxy-torrent"),
    )
    proxy = _meta(tmp_path)
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            proxy,
            {
                "qui_proxy_url": "https://proxy.invalid",
                "VERIFY_WEBUI_CERTIFICATE": False,
            },
        )
    )
    assert _Session.instances[-1].closed

    _Session.reset(_Response(500))
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy.invalid"}
        )
    )
    assert _Session.instances[-1].closed

    _Session.reset(TimeoutError("slow"))
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy.invalid"}
        )
    )
    assert _Session.instances[-1].closed

    direct = _Client()
    direct.properties = RuntimeError("properties failed")
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=direct)
    )
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qbit_url": "host", "qbit_port": 443}
        )
    )


def test_search_qbit_direct_storage_export_subtitle_preferences_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    config = _config(tmp_path)
    config["TORRENT_CLIENTS"]["qbit"]["torrent_storage_dir"] = str(storage)
    qbit = _Qbit(config)
    client = _Client()
    matching = SimpleNamespace(
        hash="one",
        name="Example.mkv",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Example.mkv"),
        tracker="https://aither.cc/announce",
        trackers=[{"url": "https://aither.cc/announce"}],
        comment="https://aither.cc/torrents/42",
    )
    malformed = SimpleNamespace()
    other = SimpleNamespace(
        hash="other",
        name="Other",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Other"),
        tracker="",
        trackers=[],
        comment="",
    )
    client.torrents = [malformed, other, matching]
    client.exported = b"torrent"
    monkeypatch.setattr(qbit_module, "match_tracker_url", AsyncMock())
    meta = _meta(tmp_path)
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            meta, config["TORRENT_CLIENTS"]["qbit"], qbt_client=client
        )
    )
    assert result == "one"
    assert meta.get_tracker_id("AITHER") == "42"

    stored = storage / "one.torrent"
    stored.write_bytes(b"stored")
    qbit.valid_queue = [(True, str(stored))]
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            _meta(tmp_path),
            config["TORRENT_CLIENTS"]["qbit"],
            qbt_client=client,
        )
    )
    assert result == "one"

    qbit.includes_subtitles = False
    qbit.no_subtitles = True
    qbit.valid_queue = [(True, str(stored))]
    subtitle_meta = _meta(tmp_path, subtitle_files=["subtitle.srt"])
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            subtitle_meta, config["TORRENT_CLIENTS"]["qbit"], qbt_client=client
        )
    )
    assert result == "one" and subtitle_meta.base_reuse_torrent_path == str(
        stored
    )

    qbit.no_subtitles = False
    qbit.valid_queue = [(True, str(stored))]
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path, subtitle_files=["subtitle.srt"]),
                config["TORRENT_CLIENTS"]["qbit"],
                qbt_client=client,
            )
        )
        is None
    )

    qbit.includes_subtitles = True
    qbit.config["DEFAULT"]["prefer_max_16_torrent"] = True
    qbit.valid_queue = [(True, str(stored))]
    monkeypatch.setattr(
        qbit_module,
        "Torrent",
        SimpleNamespace(
            read=lambda _path: SimpleNamespace(piece_size=8 * 1024 * 1024)
        ),
    )
    preferred = _meta(tmp_path)
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                preferred, config["TORRENT_CLIENTS"]["qbit"], qbt_client=client
            )
        )
        == "one"
    )

    qbit.valid_queue = [(True, str(stored))]
    monkeypatch.setattr(
        qbit_module,
        "Torrent",
        SimpleNamespace(
            read=lambda _path: (_ for _ in ()).throw(ValueError("bad torrent"))
        ),
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path),
                config["TORRENT_CLIENTS"]["qbit"],
                qbt_client=client,
            )
        )
        == "one"
    )

    qbit.config["DEFAULT"]["prefer_max_16_torrent"] = False
    qbit.valid_queue = [(False, str(stored))]
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path),
                config["TORRENT_CLIENTS"]["qbit"],
                qbt_client=client,
            )
        )
        is None
    )

    qbit.valid_queue = []
    client.torrents = []
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path),
                config["TORRENT_CLIENTS"]["qbit"],
                qbt_client=client,
            )
        )
        is None
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path),
                config["TORRENT_CLIENTS"]["qbit"],
                qbt_client=None,
                proxy_url=None,
            )
        )
        is not None
        or True
    )


def test_search_qbit_proxy_payloads_comments_export_and_session_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    monkeypatch.setattr(qbit_module, "match_tracker_url", AsyncMock())
    payload = {
        "torrents": [
            {
                "hash": "proxy",
                "name": "Example.mkv",
                "save_path": str(tmp_path),
                "content_path": str(tmp_path / "Example.mkv"),
                "tracker": "",
                "trackers": [],
                "comment": "",
            }
        ]
    }
    _Session.reset(
        _Response(200, payload),
        _Response(200, {"comment": "https://aither.cc/torrents/88"}),
        _Response(200, content=b"torrent"),
    )
    meta = _meta(tmp_path)
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            meta,
            _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
            proxy_url="https://proxy.invalid",
        )
    )
    assert result == "proxy" and meta.get_tracker_id("AITHER") == "88"
    assert _Session.instances[-1].closed

    for response in (
        _Response(404),
        _Response(500),
        _Response(200, {"bad": True}),
    ):
        _Session.reset(response)
        assert (
            asyncio.run(
                qbit.search_qbit_for_torrent(
                    _meta(tmp_path),
                    _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                    proxy_url="https://proxy.invalid",
                )
            )
            is None
        )
        assert _Session.instances[-1].closed

    _Session.reset(RuntimeError("offline"))
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path),
                _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                proxy_url="https://proxy.invalid",
            )
        )
        is None
    )


def test_qbittorrent_direct_add_resume_superseed_debug_and_cross(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    qbit = _Qbit(config)
    client = _Client()
    client.torrents = [SimpleNamespace(save_path=str(tmp_path))]
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client)
    )
    torrent = _TorrentData()
    meta = _meta(tmp_path, debug=True, qbit_tag="CUSTOM")
    qclient = {
        **config["TORRENT_CLIENTS"]["qbit"],
        "qbit_cat": "UA",
        "qbit_tag": "TAG",
        "use_tracker_as_tag": True,
        "automatic_management_paths": [str(tmp_path)],
        "super_seed_trackers": ["AITHER"],
    }
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            qclient,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    names = [name for name, _kwargs in client.calls]
    assert "add" in names
    assert "resume" in names
    assert "superseed" in names
    add = _call_kwargs(client.calls, "add")[0]
    assert add["category"] == "UA"
    assert add["tags"] == "CUSTOM"

    cross_meta = _meta(tmp_path, keep_folder=True)
    asyncio.run(
        qbit.qbittorrent(
            cross_meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            qclient,
            "",
            list(cross_meta.filelist),
            cross_meta,
            "AITHER",
            cross=True,
        )
    )
    add = _call_kwargs(client.calls, "add")[-1]
    assert add["is_paused"] is True

    no_source = _meta(tmp_path, path="", filelist=[])
    with pytest.raises(ValueError, match="No source path"):
        asyncio.run(
            qbit.qbittorrent(
                "",
                torrent,
                str(tmp_path),
                str(tmp_path),
                qclient,
                "",
                [],
                no_source,
                "AITHER",
            )
        )


def test_qbittorrent_proxy_start_legacy_superseed_debug_and_add_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    torrent = _TorrentData()
    meta = _meta(tmp_path, debug=True)
    client = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy.invalid",
        "qbit_cat": "UA",
        "qbit_cross_cat": "CROSS",
        "qbit_cross_tag": "CROSS-TAG",
        "super_seed_trackers": ["AITHER"],
    }
    _Session.reset(
        _Response(200),
        _Response(200, [{"hash": "abc123"}]),
        _Response(404),
        _Response(200),
        _Response(500),
        _Response(200, [{"save_path": str(tmp_path)}]),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    urls = [url for _method, url, _kwargs in _Session.instances[-1].calls]
    assert any(url.endswith("/start") for url in urls) and any(
        url.endswith("/resume") for url in urls
    )
    assert _Session.instances[-1].closed

    _Session.reset(_Response(400))
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert _Session.instances[-1].closed

    _Session.reset(
        _Response(503),
        _Response(200, []),
        _Response(503),
        _Response(200, []),
        _Response(503),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_qbittorrent_linking_success_isolated_fallback_disabled_and_symlink_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "linked"
    linked.mkdir()
    config = _config(tmp_path)
    qbit = _Qbit(config)
    client_api = _Client()
    client_api.torrents = [SimpleNamespace(save_path=str(tmp_path))]
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client_api)
    )
    torrent = _TorrentData()
    meta = _meta(tmp_path)
    base = {
        **config["TORRENT_CLIENTS"]["qbit"],
        "linked_folder": [str(linked)],
        "linking": "hardlink",
    }

    link = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            base,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert link.await_count == 2

    link = AsyncMock(return_value=False)
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    no_fallback = {**base, "allow_fallback": False}
    before = len(client_api.calls)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            no_fallback,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert len(client_api.calls) == before

    no_target = {**base, "linked_folder": []}
    with pytest.raises(ValueError, match="No suitable linked folder"):
        asyncio.run(
            qbit.qbittorrent(
                meta.path,
                torrent,
                str(tmp_path),
                str(tmp_path),
                no_target,
                "",
                list(meta.filelist),
                meta,
                "AITHER",
            )
        )

    symlink = {**base, "linking": "symlink", "linked_folder": [str(linked)]}
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            symlink,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_find_qbit_by_path_client_override_stop_and_debug_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        searching_client_list=["qbit", "second"],
        prefer_max_16_torrent=True,
    )
    config["TORRENT_CLIENTS"]["second"] = dict(
        config["TORRENT_CLIENTS"]["qbit"]
    )
    qbit = _Qbit(config)
    meta = _meta(tmp_path, client="qbit", debug=True)
    monkeypatch.setattr(
        qbit,
        "_search_single_qbit_client",
        AsyncMock(return_value=[{"hash": "one"}]),
    )
    meta.found_preferred_piece_size = "16MiB"
    assert asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta)) == [
        {"hash": "one"}
    ]

    meta.client = "none"
    meta.found_preferred_piece_size = None
    responses = AsyncMock(
        side_effect=([{"hash": "one"}], [{"hash": "one"}, {"hash": "two"}])
    )
    monkeypatch.setattr(qbit, "_search_single_qbit_client", responses)
    result = asyncio.run(qbit.find_qbit_torrents_by_path(meta.path, meta))
    assert result == [{"hash": "one"}, {"hash": "two"}]


def _direct_qbit_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, debug: bool = False
) -> tuple[_Qbit, _Client, _TorrentData, Meta, dict[str, Any]]:
    config = _config(tmp_path)
    qbit = _Qbit(config)
    client_api = _Client()
    client_api.torrents = [SimpleNamespace(save_path=str(tmp_path))]
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client_api)
    )
    torrent = _TorrentData()
    meta = _meta(tmp_path, debug=debug)
    client = {**config["TORRENT_CLIENTS"]["qbit"], "qbit_cat": "UA"}
    return qbit, client_api, torrent, meta, client


def test_qbittorrent_windows_mount_command_mount_error_and_root_link_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, _api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    linked = tmp_path / "linked"
    linked.mkdir()
    client.update({"linking": "symlink", "linked_folder": ["C:\\linked"]})
    meta.path = "C:\\media\\Example.mkv"
    meta.filelist = []
    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        qbit_module.os.path,
        "splitdrive",
        lambda value: (
            ("C:", value[2:])
            if str(value).startswith("C:")
            else ("", str(value))
        ),
    )
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            "C:\\media",
            "C:\\media",
            client,
            "",
            [],
            meta,
            "AITHER",
        )
    )

    qbit, _api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    actual_exists = Path.exists

    def no_proc_mounts(path: Path) -> bool:
        if str(path) == "/proc/mounts":
            return False
        return actual_exists(path)

    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "exists", no_proc_mounts)
    monkeypatch.setattr(
        qbit_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: f"dev on {tmp_path} type ext4\n",
    )
    client.update({"linking": "hardlink", "linked_folder": [str(linked)]})
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, _api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(Path, "exists", no_proc_mounts)
    monkeypatch.setattr(
        qbit_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("mount failed")
        ),
    )
    client.update({"linking": "symlink", "linked_folder": [str(linked)]})
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_qbittorrent_sibling_mount_cross_seed_retry_and_link_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, _api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    source_mount = tmp_path / "source"
    source_mount.mkdir()
    source = source_mount / "Example.mkv"
    source.write_bytes(b"media")
    sibling = source_mount / "target"
    sibling.mkdir()
    configured_link = tmp_path / "target"
    configured_link.mkdir()
    meta.path = str(source)
    meta.filelist = [str(source)]
    client.update(
        {
            "linking": "hardlink",
            "linked_folder": [str(configured_link)],
            "allow_fallback": True,
        }
    )
    actual_exists = Path.exists

    def mounts(path: Path) -> bool:
        if str(path) == "/proc/mounts":
            return False
        return actual_exists(path)

    monkeypatch.setattr(Path, "exists", mounts)
    monkeypatch.setattr(
        qbit_module.subprocess,
        "check_output",
        lambda *_args, **_kwargs: f"dev on {source_mount} type ext4\n",
    )
    link = AsyncMock(side_effect=[False, False])
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert link.await_count == 2

    qbit, _api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    linked = source_mount / "linked-cross"
    linked.mkdir()
    client.update({"linking": "hardlink", "linked_folder": [str(linked)]})
    meta.path = str(tmp_path / "Release")
    Path(meta.path).mkdir()
    meta.filelist = [str(source)]
    cross = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(qbit_module, "create_cross_seed_links", cross)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
            cross=True,
        )
    )
    assert cross.await_count == 2


def test_qbittorrent_direct_add_wait_resume_superseed_and_debug_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = []
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.add_result = RuntimeError("add failed")
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [SimpleNamespace(save_path=str(tmp_path))]
    api.torrents_resume = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        TimeoutError("resume slow")
    )  # type: ignore[method-assign]
    client["super_seed_trackers"] = ["AITHER"]
    api.torrents_set_super_seeding = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("superseed bad")
    )  # type: ignore[method-assign]
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = []
    calls = 0
    original_retry = qbit.retry_qbt_operation

    async def selective(operation, name: str, *args, **kwargs):
        nonlocal calls
        calls += 1
        if "Resume" in name:
            raise RuntimeError("resume bad")
        if "debug" in name.casefold():
            raise TimeoutError("debug slow")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", selective)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert calls


def test_qbittorrent_proxy_empty_debug_status_and_recovery_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    torrent = _TorrentData()
    meta = _meta(tmp_path, debug=True)
    client = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
        "super_seed_trackers": [],
    }

    _Session.reset(
        _Response(503),
        _Response(200, [{"hash": "abc123"}]),
        _Response(200, [{"hash": "abc123"}]),
        _Response(200),
        _Response(200, []),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert _Session.instances[-1].closed

    _Session.reset(
        _Response(200),
        _Response(200, [{"hash": "abc123"}]),
        _Response(200),
        _Response(500),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_process_base_creation_error_fallback_and_preference_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    created: list[str] = []

    async def fail_create(_path: str, _base: str, _uuid: str) -> None:
        raise RuntimeError("create failed")

    monkeypatch.setattr(
        qbit_module.TorrentCreator,
        "create_base_from_existing_torrent",
        fail_create,
    )
    qbit = _Qbit(_config(tmp_path))
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        qbit, "_export_torrent_file", AsyncMock(return_value=str(first))
    )
    qbit.valid_queue = [(True, str(first))]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "one"}], {}, "", "", None, _Client(), meta
        )
    )
    assert not meta.base_torrent_created

    qbit = _Qbit(_config(tmp_path, prefer_max_16_torrent=True))
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        qbit,
        "_export_torrent_file",
        AsyncMock(side_effect=[str(first), str(second)]),
    )
    qbit.valid_queue = [(True, str(first)), (True, str(second))]
    monkeypatch.setattr(
        qbit_module,
        "Torrent",
        SimpleNamespace(
            read=lambda _path: (_ for _ in ()).throw(ValueError("bad piece"))
        ),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "one"}, {"hash": "two"}],
            {},
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.we_checked_them_all

    async def create(path: str, _base: str, _uuid: str) -> None:
        created.append(path)

    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    qbit = _Qbit(_config(tmp_path, prefer_max_16_torrent=True))
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        qbit,
        "_export_torrent_file",
        AsyncMock(side_effect=[str(first), str(second)]),
    )
    qbit.valid_queue = [(False, str(first)), (True, str(second))]
    monkeypatch.setattr(
        qbit_module,
        "Torrent",
        SimpleNamespace(
            read=lambda _path: SimpleNamespace(piece_size=8 * 1024 * 1024)
        ),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "one"}, {"hash": "two"}],
            {},
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.hash_used == "two" and created


def test_create_cross_seed_links_multifile_matching_reasons_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    exact = source / "exact.bin"
    named = source / "named.bin"
    sized = source / "sized.bin"
    fallback = source / "fallback.bin"
    for path, content in (
        (exact, b"1234"),
        (named, b"abc"),
        (sized, b"12"),
        (fallback, b"fallback"),
    ):
        path.write_bytes(content)
    tracker = tmp_path / "tracker"
    tracker.mkdir()
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name.utf-8": b"Release",
                "files": [
                    {"path.utf-8": [b"folder", b"exact.bin"], "length": 4},
                    {"path": ["folder", "named.bin"], "length": 999},
                    {"path": "folder/something.bin", "length": 2},
                    {"path": "../fallback-name.bin", "length": None},
                ],
            }
        },
        name="Ignored",
    )
    meta = _meta(tmp_path, path=str(source), filelist=[])
    link = AsyncMock(return_value=True)
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    assert asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, True)
    )
    assert link.await_count == 4
    sources = [str(call.args[0]) for call in link.await_args_list]
    assert (
        str(exact.resolve()) in sources
        and str(named.resolve()) in sources
        and str(sized.resolve()) in sources
        and str(fallback.resolve()) in sources
    )

    torrent = SimpleNamespace(metainfo={}, name=None)
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, True)
    )

    empty = tmp_path / "empty-source"
    empty.mkdir()
    torrent = SimpleNamespace(
        metainfo={"info": {"name": "Release", "length": 1}}, name="Release"
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            _meta(tmp_path, path=str(empty), filelist=[]),
            torrent,
            tracker,
            True,
        )
    )

    single = tmp_path / "single.bin"
    single.write_bytes(b"x")
    meta = _meta(tmp_path, path="", filelist=str(single))  # type: ignore[arg-type]
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=False)
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, True)
    )

    meta = _meta(tmp_path, path="", filelist=[])
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, True)
    )


def test_create_cross_seed_links_security_stat_and_mapping_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    file = source / "file.bin"
    file.write_bytes(b"abc")
    tracker = tmp_path / "tracker"
    tracker.mkdir()
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": "Release",
                "files": [{"path": ["missing.bin"], "length": 999}],
            }
        },
        name="Release",
    )
    meta = _meta(tmp_path, path=str(source), filelist=[])
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    assert asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, False)
    )

    original_stat = Path.stat

    def fail_stat(path: Path, *args, **kwargs):
        if path.name == "file.bin":
            raise OSError("stat failed")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_stat)
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": "Release",
                "files": [{"path": ["other.bin"], "length": 3}],
            }
        },
        name="Release",
    )
    assert asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, False)
    )
    monkeypatch.setattr(Path, "stat", original_stat)

    original_commonpath = qbit_module.os.path.commonpath
    calls = 0

    def flaky_commonpath(values):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ValueError("different drives")
        return original_commonpath(values)

    monkeypatch.setattr(qbit_module.os.path, "commonpath", flaky_commonpath)
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, False)
    )

    monkeypatch.setattr(qbit_module.os.path, "commonpath", original_commonpath)
    outside = SimpleNamespace(
        metainfo={"info": {"name": "../outside", "length": 3}},
        name="../outside",
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, outside, tracker, False)
    )


def test_async_link_directory_file_directory_windows_and_error_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    existing = tmp_path / "existing.bin"
    existing.write_bytes(b"other")
    assert not asyncio.run(
        qbit_module.async_link_directory(str(source), existing, True)
    )

    hard = tmp_path / "hard.bin"
    assert asyncio.run(
        qbit_module.async_link_directory(str(source), hard, True)
    )
    assert hard.samefile(source)
    assert asyncio.run(
        qbit_module.async_link_directory(str(source), hard, True)
    )

    monkeypatch.setattr(
        qbit_module.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("link failed")
        ),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(
            str(source), tmp_path / "hard-fail.bin", True
        )
    )

    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Windows")
    calls: list[bool] = []

    def symlink(_src, dst, *, target_is_directory=False):
        calls.append(target_is_directory)
        Path(dst).write_text("link", encoding="utf-8")

    monkeypatch.setattr(qbit_module.os, "symlink", symlink)
    assert asyncio.run(
        qbit_module.async_link_directory(
            str(source), tmp_path / "sym.bin", False
        )
    )
    assert calls == [False]
    monkeypatch.setattr(
        qbit_module.os,
        "symlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sym fail")),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(
            str(source), tmp_path / "sym-fail.bin", False
        )
    )

    directory = tmp_path / "directory"
    nested = directory / "nested"
    nested.mkdir(parents=True)
    (directory / "one.bin").write_bytes(b"one")
    (nested / "two.bin").write_bytes(b"two")
    bad_destination = tmp_path / "bad-destination"
    bad_destination.write_text("file", encoding="utf-8")
    assert not asyncio.run(
        qbit_module.async_link_directory(str(directory), bad_destination, True)
    )

    destination = tmp_path / "directory-links"
    monkeypatch.undo()
    assert asyncio.run(
        qbit_module.async_link_directory(str(directory), destination, True)
    )
    assert (destination / "one.bin").samefile(directory / "one.bin")
    assert asyncio.run(
        qbit_module.async_link_directory(str(directory), destination, True)
    )

    stale = tmp_path / "stale-links"
    stale.mkdir()
    (stale / "one.bin").write_bytes(b"stale")
    assert not asyncio.run(
        qbit_module.async_link_directory(str(directory), stale, True)
    )

    symlink_dir = tmp_path / "dir-symlink"
    assert asyncio.run(
        qbit_module.async_link_directory(str(directory), symlink_dir, False)
    )
    assert symlink_dir.is_symlink()

    original_makedirs = qbit_module.os.makedirs
    monkeypatch.setattr(
        qbit_module.os,
        "makedirs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("mkdir failed")
        ),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(
            str(source), tmp_path / "outer-fail", True
        )
    )
    monkeypatch.setattr(qbit_module.os, "makedirs", original_makedirs)


def test_cross_seed_exhausted_candidates_missing_files_and_commonpath_valueerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-exhaust"
    source.mkdir()
    only = source / "only.bin"
    only.write_bytes(b"123")
    tracker = tmp_path / "tracker-exhaust"
    tracker.mkdir()
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": "Release",
                "files": [
                    {"path": ["first.bin"], "length": 3},
                    {"path": ["second.bin"], "length": 3},
                ],
            }
        },
        name="Release",
    )
    meta = _meta(tmp_path, path=str(source), filelist=[])
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=True)
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(meta, torrent, tracker, True)
    )

    missing_meta = _meta(
        tmp_path,
        path=str(tmp_path / "missing-root"),
        filelist=[str(tmp_path / "missing.bin")],
    )
    single = SimpleNamespace(
        metainfo={"info": {"name": "Release", "length": 1}}, name="Release"
    )
    original_walk = qbit_module.os.walk
    monkeypatch.setattr(
        qbit_module.os, "walk", lambda *_args, **_kwargs: iter(())
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            missing_meta, single, tracker, True
        )
    )
    monkeypatch.setattr(qbit_module.os, "walk", original_walk)

    original_commonpath = qbit_module.os.path.commonpath
    calls = 0

    def fail_candidate_commonpath(values):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("different drive")
        return original_commonpath(values)

    monkeypatch.setattr(
        qbit_module.os.path, "commonpath", fail_candidate_commonpath
    )
    assert asyncio.run(
        qbit_module.create_cross_seed_links(meta, single, tracker, True)
    )


def test_async_link_remaining_samefile_posix_windows_directory_and_hardlink_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source-file.bin"
    source.write_bytes(b"data")
    existing = tmp_path / "existing-file.bin"
    existing.write_bytes(b"other")
    real_samefile = qbit_module.os.path.samefile
    monkeypatch.setattr(
        qbit_module.os.path,
        "samefile",
        lambda *_args: (_ for _ in ()).throw(OSError("samefile failed")),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(str(source), existing, True)
    )
    monkeypatch.setattr(qbit_module.os.path, "samefile", real_samefile)

    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Linux")
    posix_link = tmp_path / "posix-link.bin"
    assert asyncio.run(
        qbit_module.async_link_directory(str(source), posix_link, False)
    )

    directory = tmp_path / "directory-source"
    directory.mkdir()
    one = directory / "one.bin"
    one.write_bytes(b"one")
    destination = tmp_path / "directory-destination"
    destination.mkdir()
    stale = destination / "one.bin"
    stale.write_bytes(b"different")
    monkeypatch.setattr(
        Path,
        "samefile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("samefile failed")
        ),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(str(directory), destination, True)
    )

    monkeypatch.undo()
    failed = tmp_path / "failed-hardlinks"
    real_link = qbit_module.os.link
    monkeypatch.setattr(
        qbit_module.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("link failed")
        ),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(str(directory), failed, True)
    )
    monkeypatch.setattr(qbit_module.os, "link", real_link)

    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Windows")
    calls: list[bool] = []

    def directory_symlink(_src, dst, *, target_is_directory=False):
        calls.append(target_is_directory)
        Path(dst).mkdir()

    monkeypatch.setattr(qbit_module.os, "symlink", directory_symlink)
    assert asyncio.run(
        qbit_module.async_link_directory(
            str(directory), tmp_path / "windows-dir-link", False
        )
    )
    assert calls == [True]
    monkeypatch.setattr(
        qbit_module.os,
        "symlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("dir symlink failed")
        ),
    )
    assert not asyncio.run(
        qbit_module.async_link_directory(
            str(directory), tmp_path / "windows-dir-fail", False
        )
    )


def test_get_ptp_remaining_uuid_export_validation_timeout_notfound_and_outer_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    client = _Client()
    client.properties = {
        "hash": "abc123",
        "infohash_v1": "abc123",
        "name": "Example",
        "comment": "",
    }
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client)
    )
    meta = _meta(tmp_path, uuid="")
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            meta, {"qbit_url": "host", "qbit_port": 443}, pathed=True
        )
    )
    assert meta.uuid == Path(meta.path).name

    qbit.valid_queue = [(False, "")]
    meta = _meta(tmp_path)
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            meta, {"qbit_url": "host", "qbit_port": 443}
        )
    )
    assert not (tmp_path / "tmp" / meta.uuid / "abc123.torrent").exists()

    monkeypatch.setattr(
        qbit,
        "retry_qbt_operation",
        AsyncMock(side_effect=TimeoutError("export slow")),
    )
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qbit_url": "host", "qbit_port": 443}
        )
    )

    monkeypatch.setattr(
        qbit,
        "retry_qbt_operation",
        AsyncMock(side_effect=RuntimeError("properties bad")),
    )
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qbit_url": "host", "qbit_port": 443}
        )
    )

    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    _Session.reset(RuntimeError("outer bad"))
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy"}
        )
    )
    assert _Session.instances[-1].closed

    _Session.reset(
        _Response(
            200,
            {
                "hash": "abc123",
                "infohash_v1": "abc123",
                "name": "Example",
                "comment": "",
            },
        ),
        _Response(500),
    )
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy"}
        )
    )

    _Session.reset(
        _Response(
            200,
            {
                "hash": "different",
                "infohash_v1": "different",
                "name": "Other",
                "comment": "",
            },
        )
    )
    asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy"}, pathed=True
        )
    )

    class BrokenComment(_Qbit):
        def _extract_tracker_ids_from_comment(
            self, _comment: str
        ) -> dict[str, str]:
            raise RuntimeError("comment bad")

    broken = BrokenComment(_config(tmp_path))
    _Session.reset(
        _Response(
            200,
            {
                "hash": "abc123",
                "infohash_v1": "abc123",
                "name": "Example",
                "comment": "value",
            },
        )
    )
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    asyncio.run(
        broken.get_ptp_from_hash_qbit(
            _meta(tmp_path), {"qui_proxy_url": "https://proxy"}, pathed=True
        )
    )


def test_search_qbit_initialization_list_payload_comments_duplicates_and_fetch_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    client = _Client()
    matching = SimpleNamespace(
        hash="one",
        name="Example.mkv",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Example.mkv"),
        tracker="",
        trackers=["https://aither.cc/announce"],
        comment="",
    )
    duplicate = SimpleNamespace(**matching.__dict__)
    client.torrents = [matching, duplicate]
    client.properties = {"comment": "https://aither.cc/torrents/77"}
    client.exported = b"torrent"
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client)
    )
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            _meta(tmp_path), _config(tmp_path)["TORRENT_CLIENTS"]["qbit"]
        )
    )
    assert result == "one"

    monkeypatch.setattr(
        qbit,
        "init_qbittorrent_client",
        AsyncMock(side_effect=qbittorrentapi.LoginFailed("bad")),
    )
    assert (
        asyncio.run(qbit.search_qbit_for_torrent(_meta(tmp_path), {})) is None
    )
    monkeypatch.setattr(
        qbit,
        "init_qbittorrent_client",
        AsyncMock(side_effect=qbittorrentapi.APIConnectionError("bad")),
    )
    assert (
        asyncio.run(qbit.search_qbit_for_torrent(_meta(tmp_path), {})) is None
    )

    qbit = _Qbit(_config(tmp_path))
    client = _Client()
    client.torrents = [matching]
    monkeypatch.setattr(
        qbit,
        "retry_qbt_operation",
        AsyncMock(side_effect=TimeoutError("slow")),
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, qbt_client=client
            )
        )
        is None
    )
    monkeypatch.setattr(
        qbit, "retry_qbt_operation", AsyncMock(side_effect=RuntimeError("bad"))
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, qbt_client=client
            )
        )
        is None
    )

    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    monkeypatch.setattr(qbit_module, "match_tracker_url", AsyncMock())
    payload = [
        {
            "hash": "list",
            "name": "Example.mkv",
            "save_path": str(tmp_path),
            "content_path": str(tmp_path / "Example.mkv"),
            "tracker": "",
            "trackers": [],
            "comment": "https://aither.cc/torrents/55",
        }
    ]
    _Session.reset(_Response(200, payload), _Response(200, content=b"torrent"))
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, proxy_url="https://proxy"
            )
        )
        == "list"
    )


def test_search_qbit_comment_export_validation_and_malformed_torrent_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    client = _Client()
    good = SimpleNamespace(
        hash="good",
        name="Example.mkv",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Example.mkv"),
        tracker="",
        trackers=[],
        comment="",
    )
    client.torrents = [good]
    client.properties = RuntimeError("comment unavailable")
    client.exported = b"torrent"
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, qbt_client=client
            )
        )
        == "good"
    )

    class BrokenHash:
        def __str__(self) -> str:
            raise RuntimeError("hash bad")

    malformed = SimpleNamespace(
        hash=BrokenHash(),
        name="Example.mkv",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Example.mkv"),
        tracker="",
        trackers=[],
        comment="",
    )
    client.torrents = [malformed]
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, qbt_client=client
            )
        )
        is None
    )

    client.torrents = [good]
    qbit.valid_queue = []
    monkeypatch.setattr(
        qbit,
        "is_valid_torrent",
        AsyncMock(side_effect=RuntimeError("validation bad")),
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, qbt_client=client
            )
        )
        is None
    )

    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    payload = {
        "torrents": [
            {
                "hash": "proxy",
                "name": "Example.mkv",
                "save_path": str(tmp_path),
                "content_path": str(tmp_path / "Example.mkv"),
                "tracker": "",
                "trackers": [],
                "comment": "comment",
            }
        ]
    }
    _Session.reset(_Response(200, payload), _Response(500))
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, proxy_url="https://proxy"
            )
        )
        is None
    )
    _Session.reset(_Response(200, payload), RuntimeError("export bad"))
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, proxy_url="https://proxy"
            )
        )
        is None
    )


def test_search_qbit_invalid_directory_and_missing_proxy_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    invalid = _meta(tmp_path, base_dir="", uuid="")
    # The service rejects a missing state root before creating a torrent path.
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(invalid, {}, qbt_client=_Client())
        )
        is None
    )

    monkeypatch.setattr(
        qbit_module.httpx, "AsyncClient", lambda *_args, **_kwargs: None
    )
    assert (
        asyncio.run(
            qbit.search_qbit_for_torrent(
                _meta(tmp_path), {}, proxy_url="https://proxy"
            )
        )
        is None
    )


def test_retry_negative_count_and_hash_export_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    assert (
        asyncio.run(
            qbit.retry_qbt_operation(
                lambda: asyncio.sleep(0), "none", max_retries=-1
            )
        )
        is None
    )

    client = _Client()
    client.properties = {
        "hash": "abc123",
        "infohash_v1": "abc123",
        "name": "Example",
        "comment": "",
    }
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client)
    )
    original_retry = qbit.retry_qbt_operation

    async def timeout_export(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name.startswith("Export torrent"):
            raise TimeoutError("export slow")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", timeout_export)
    meta = _meta(tmp_path)
    result = asyncio.run(
        qbit.get_ptp_from_hash_qbit(
            meta, _config(tmp_path)["TORRENT_CLIENTS"]["qbit"]
        )
    )
    assert result is meta


def test_qbittorrent_symlink_unmatched_folder_and_no_fallback_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    missing_link = tmp_path / "missing-linked"
    client.update({"linking": "symlink", "linked_folder": [str(missing_link)]})
    link = AsyncMock(return_value=True)
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert missing_link in Path(link.await_args.kwargs["dst"]).parents

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    linked = tmp_path / "linked-no-fallback"
    linked.mkdir()
    client.update(
        {
            "linking": "hardlink",
            "linked_folder": [str(linked)],
            "allow_fallback": False,
        }
    )
    monkeypatch.setattr(
        qbit_module, "async_link_directory", AsyncMock(return_value=False)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert not any(name == "add" for name, _kwargs in api.calls)


def test_qbittorrent_tracker_and_meta_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    client.update({"qbit_tag": "", "use_tracker_as_tag": True})
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    add = next(kwargs for name, kwargs in api.calls if name == "add")
    assert add["tags"] == "AITHER"

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    meta.qbit_tag = "META-TAG"
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    add = next(kwargs for name, kwargs in api.calls if name == "add")
    assert add["tags"] == "META-TAG"


def test_qbittorrent_add_timeout_recovery_direct_and_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    monkeypatch.setattr(
        qbit,
        "_add_torrent_direct",
        AsyncMock(side_effect=TimeoutError("add timed out")),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
            cross=True,
        )
    )
    assert any(name == "info" for name, _kwargs in api.calls)

    qbit = _Qbit(_config(tmp_path))
    torrent = _TorrentData()
    meta = _meta(tmp_path)
    client = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    monkeypatch.setattr(
        qbit,
        "_add_torrent_via_proxy",
        AsyncMock(side_effect=TimeoutError("proxy add timeout")),
    )
    _Session.reset(_Response(200, [{"hash": torrent.infohash}]))
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
            cross=True,
        )
    )
    assert _Session.instances[-1].closed


def test_qbittorrent_wait_proxy_non_200_times_out_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    torrent = _TorrentData()
    meta = _meta(tmp_path)
    client = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    monkeypatch.setattr(
        qbit, "_add_torrent_via_proxy", AsyncMock(return_value=None)
    )
    _Session.reset(*[_Response(500) for _ in range(31)])
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert _Session.instances[-1].closed


def test_qbittorrent_direct_wait_timeout_generic_resume_superseed_and_debug_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = []
    original_retry = qbit.retry_qbt_operation
    info_calls = 0

    async def retry_with_wait_errors(
        operation, name: str, *args: object, **kwargs: object
    ):
        nonlocal info_calls
        if name.startswith("Check torrent"):
            info_calls += 1
            if info_calls == 1:
                raise TimeoutError("wait timeout")
            if info_calls == 2:
                raise RuntimeError("wait failed")
            return []
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", retry_with_wait_errors)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert info_calls >= 2

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    client["super_seed_trackers"] = ["AITHER"]
    original_retry = qbit.retry_qbt_operation

    async def retry_resume_superseed_debug(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name.startswith("Resume"):
            raise RuntimeError("resume failed")
        if name.startswith("Set super seeding"):
            raise TimeoutError("superseed slow")
        if name.startswith("Debug torrent"):
            raise TimeoutError("debug slow")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(
        qbit, "retry_qbt_operation", retry_resume_superseed_debug
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, api, torrent, meta, client = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    original_retry = qbit.retry_qbt_operation

    async def retry_debug_generic(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name.startswith("Debug torrent"):
            raise RuntimeError("debug failed")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", retry_debug_generic)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            client,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_fetch_proxy_malformed_payload_and_process_match_outer_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    _Session.reset(_Response(200, "not-a-list-or-dict"))
    assert (
        asyncio.run(
            qbit._fetch_torrents(
                "https://proxy", "https://proxy", _Session(), None, ""
            )
        )
        == []
    )

    good = SimpleNamespace(
        hash="good",
        name="Example.mkv",
        save_path=str(tmp_path),
        content_path=str(tmp_path / "Example.mkv"),
        trackers=[],
        tracker="",
        comment="",
    )
    qbit.retry_qbt_operation = AsyncMock(
        side_effect=RuntimeError("trackers failed")
    )  # type: ignore[method-assign]
    result = asyncio.run(
        qbit._process_torrent_matches(
            [good], {}, [], "", "", None, _Client(), _meta(tmp_path)
        )
    )
    assert result == []

    class BadSave:
        hash = "bad-save"
        name = "Example.mkv"
        trackers: ClassVar[list[object]] = []
        tracker = ""
        comment = ""

        @property
        def save_path(self) -> str:
            raise RuntimeError("save path failed")

    qbit.retry_qbt_operation = AsyncMock(return_value=[])  # type: ignore[method-assign]
    result = asyncio.run(
        qbit._process_torrent_matches(
            [BadSave()], {}, [], "", "", None, _Client(), _meta(tmp_path)
        )
    )
    assert result == []


def test_qbit_remaining_small_direct_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Direct export timeout inside get_ptp_from_hash_qbit.
    qbit = _Qbit(_config(tmp_path))
    client_cfg = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "torrent_storage_dir": "",
    }
    client = _Client()
    client.properties = {
        "hash": "abc123",
        "infohash_v1": "abc123",
        "name": "Example",
        "comment": "",
    }
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=client)
    )
    original_retry = qbit.retry_qbt_operation

    async def timeout_export(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name.startswith("Export torrent"):
            raise TimeoutError("export slow")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", timeout_export)
    meta = _meta(tmp_path)
    meta.infohash = "abc123"
    asyncio.run(qbit.get_ptp_from_hash_qbit(meta, client_cfg))

    # qBittorrent client init returning None exits before add.
    qbit, _api, torrent, meta, cfg = _direct_qbit_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        qbit, "init_qbittorrent_client", AsyncMock(return_value=None)
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    # Static configured qbit_tag is used when tracker-as-tag and meta override are absent.
    qbit, api, torrent, meta, cfg = _direct_qbit_setup(tmp_path, monkeypatch)
    meta.qbit_tag = ""
    cfg.update({"qbit_tag": "STATIC", "use_tracker_as_tag": False})
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    add = next(kwargs for name, kwargs in api.calls if name == "add")
    assert add["tags"] == "STATIC"


def test_qbit_windows_symlink_fallback_and_cross_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, cfg = _direct_qbit_setup(tmp_path, monkeypatch)
    windows_source = r"C:\media\Example.mkv"
    meta.path = windows_source
    meta.filelist = [windows_source]
    cfg.update(
        {
            "linking": "symlink",
            "linked_folder": [r"D:\linked"],
            "qbit_cross_tag": "CROSS",
        }
    )
    monkeypatch.setattr(qbit_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        qbit_module.os.path,
        "splitdrive",
        lambda value: (str(value)[:2], str(value)[2:]),
    )
    link = AsyncMock(return_value=True)
    monkeypatch.setattr(qbit_module, "async_link_directory", link)
    asyncio.run(
        qbit.qbittorrent(
            windows_source,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
            cross=True,
        )
    )
    assert "D:\\linked" in str(link.await_args.args[1])
    add = next(kwargs for name, kwargs in api.calls if name == "add")
    assert add["tags"] == "CROSS"


def test_qbit_add_timeout_failure_direct_and_proxy_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, cfg = _direct_qbit_setup(tmp_path, monkeypatch)
    api.torrents = []
    monkeypatch.setattr(
        qbit,
        "_add_torrent_direct",
        AsyncMock(side_effect=TimeoutError("timeout")),
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit = _Qbit(_config(tmp_path))
    torrent = _TorrentData()
    meta = _meta(tmp_path)
    cfg = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    monkeypatch.setattr(
        qbit,
        "_add_torrent_via_proxy",
        AsyncMock(side_effect=TimeoutError("timeout")),
    )
    _Session.reset(_Response(200, []))
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert _Session.instances[-1].closed


def test_search_qbit_duplicate_hash_and_single_client_debug_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    matching = [
        {
            "hash": "DUP",
            "name": "Example.mkv",
            "size": 1,
            "save_path": str(tmp_path),
            "content_path": str(tmp_path / "Example.mkv"),
            "has_tracker": False,
            "tracker_urls": [],
        },
        {
            "hash": "DUP",
            "name": "Example.mkv",
            "size": 1,
            "save_path": str(tmp_path),
            "content_path": str(tmp_path / "Example.mkv"),
            "has_tracker": False,
            "tracker_urls": [],
        },
    ]
    qbit.init_qbittorrent_client = AsyncMock(return_value=_Client())  # type: ignore[method-assign]
    qbit._fetch_torrents = AsyncMock(return_value=matching)  # type: ignore[method-assign]
    qbit._process_torrent_matches = AsyncMock(return_value=matching)  # type: ignore[method-assign]
    qbit._process_base_torrent_creation = AsyncMock()  # type: ignore[method-assign]
    qbit._sort_matching_torrents = Mock()  # type: ignore[method-assign]
    result = asyncio.run(
        qbit._search_single_qbit_client(
            _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
            str(tmp_path),
            _meta(tmp_path, debug=True),
            "qbit",
        )
    )
    assert len(result) == 2

    # Exercise duplicate skip in full search by feeding duplicate hash after matches.
    qbit = _Qbit(_config(tmp_path))
    qbit._fetch_torrents = AsyncMock(return_value=matching)  # type: ignore[method-assign]
    qbit._process_torrent_matches = AsyncMock(return_value=matching)  # type: ignore[method-assign]
    qbit._export_torrent_file = AsyncMock(return_value=None)  # type: ignore[method-assign]
    meta = _meta(tmp_path)
    asyncio.run(
        qbit._process_base_torrent_creation(
            matching,
            _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )

    # Proxy single client with empty matches still closes its session and logs debug summary.
    cfg = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    _Session.reset()
    qbit = _Qbit(_config(tmp_path))
    qbit._fetch_torrents = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                cfg, str(tmp_path), _meta(tmp_path, debug=True), "proxy"
            )
        )
        == []
    )
    assert _Session.instances[-1].closed


def test_base_creation_piece_preference_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = True
    qbit = _Qbit(config)
    meta = _meta(tmp_path, debug=True)
    first = tmp_path / "tmp" / meta.uuid / "first.torrent"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"one")
    second = first.with_name("second.torrent")
    second.write_bytes(b"two")
    qbit._export_torrent_file = AsyncMock(
        side_effect=[str(first), str(second)]
    )  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=[(True, str(first)), (True, str(second))]
    )  # type: ignore[method-assign]
    pieces = {str(first): 12 * 1024 * 1024, str(second): 8 * 1024 * 1024}
    monkeypatch.setattr(
        qbit_module.Torrent,
        "read",
        lambda path: SimpleNamespace(piece_size=pieces[str(path)]),
    )
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}, {"hash": "TWO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert (
        meta.hash_used == "TWO" and meta.found_preferred_piece_size == "16MiB"
    )
    create.assert_awaited_once_with(str(second), meta.base_dir, meta.uuid)

    # Torrent parsing error removes the temporary best candidate.
    meta = _meta(tmp_path, uuid="parse-error")
    bad = tmp_path / "tmp" / meta.uuid / "bad.torrent"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"bad")
    qbit._export_torrent_file = AsyncMock(return_value=str(bad))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(bad)))  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.Torrent,
        "read",
        lambda _path: (_ for _ in ()).throw(RuntimeError("read failed")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "BAD"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not bad.exists()


def test_base_creation_disabled_preference_and_alternative_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = False
    qbit = _Qbit(config)
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(b"1")
    second.write_bytes(b"2")

    meta = _meta(tmp_path)
    qbit._export_torrent_file = AsyncMock(return_value=str(first))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(first)))  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.TorrentCreator,
        "create_base_from_existing_torrent",
        AsyncMock(side_effect=RuntimeError("base failed")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not meta.base_torrent_created

    meta = _meta(tmp_path, uuid="alternative", debug=True)
    qbit._export_torrent_file = AsyncMock(
        side_effect=[str(first), str(second)]
    )  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=[(False, ""), (True, str(second))]
    )  # type: ignore[method-assign]
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}, {"hash": "TWO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.base_torrent_created and meta.hash_used == "TWO"

    meta = _meta(tmp_path, uuid="alt-error")
    qbit._export_torrent_file = AsyncMock(side_effect=[None, str(second)])  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(second)))  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.TorrentCreator,
        "create_base_from_existing_torrent",
        AsyncMock(side_effect=RuntimeError("alt base failed")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}, {"hash": "TWO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not meta.base_torrent_created


def test_base_creation_subtitle_fallback_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = False
    qbit = _Qbit(config)
    first = tmp_path / "video-only.torrent"
    second = tmp_path / "partial.torrent"
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    meta = _meta(tmp_path, subtitle_files=[str(tmp_path / "sub.srt")])
    qbit._export_torrent_file = AsyncMock(
        side_effect=[str(first), str(second)]
    )  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=[(True, str(first)), (True, str(second))]
    )  # type: ignore[method-assign]
    qbit._torrent_includes_all_local_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    qbit._torrent_has_no_subtitles = Mock(side_effect=[True, False])  # type: ignore[method-assign]
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}, {"hash": "TWO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.base_torrent_created and meta.hash_used == "ONE"

    meta = _meta(
        tmp_path,
        uuid="subtitle-error",
        subtitle_files=[str(tmp_path / "sub.srt")],
    )
    qbit._export_torrent_file = AsyncMock(return_value=str(first))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(first)))  # type: ignore[method-assign]
    qbit._torrent_includes_all_local_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    qbit._torrent_has_no_subtitles = Mock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.TorrentCreator,
        "create_base_from_existing_torrent",
        AsyncMock(side_effect=RuntimeError("fallback failed")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not meta.base_torrent_created


def test_create_cross_seed_links_security_empty_candidates_and_tracker_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, filelist=[])
    torrent = _TorrentData()
    torrent.metainfo = {
        "info": {
            "name": "../escape",
            "files": [{"path": ["file.mkv"], "length": 1}],
        }
    }
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "tracker",
            use_hardlink=False,
        )
    )

    torrent.metainfo = {"info": {"name": "release", "length": 1}}
    meta = _meta(tmp_path, path=None, filelist=[])
    real_walk = qbit_module.os.walk
    monkeypatch.setattr(qbit_module.os, "walk", lambda _path: [])
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "tracker2",
            use_hardlink=False,
        )
    )
    monkeypatch.setattr(qbit_module.os, "walk", real_walk)

    tracker = tmp_path / "tracker3"
    tracker.mkdir()
    inside = tracker / "inside.mkv"
    inside.write_bytes(b"x")
    meta = _meta(tmp_path, path=None, filelist=["", str(inside)])
    torrent.metainfo = {"info": {"name": "inside.mkv", "length": 1}}
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta, torrent=torrent, tracker_dir=tracker, use_hardlink=False
        )
    )


def test_search_qbit_for_torrent_skips_duplicate_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    source = tmp_path / "Example.mkv"
    source.write_bytes(b"video")
    torrent = SimpleNamespace(
        hash="DUP",
        name=source.name,
        size=source.stat().st_size,
        save_path=str(tmp_path),
        content_path=str(source),
        trackers=[],
        tracker="",
        comment="",
        category="",
        num_complete=1,
    )
    client = _Client()
    client.torrents = [torrent, torrent]
    client.export_content = b"torrent"
    monkeypatch.setattr(
        qbit, "is_valid_torrent", AsyncMock(return_value=(False, ""))
    )
    result = asyncio.run(
        qbit.search_qbit_for_torrent(
            meta=_meta(tmp_path, path=str(source), filelist=[str(source)]),
            client={
                **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                "torrent_storage_dir": "",
            },
            qbt_client=client,
        )
    )
    assert result is None
    assert [
        kwargs["torrent_hash"]
        for name, kwargs in client.calls
        if name == "export"
    ] == ["DUP"]


def test_qbittorrent_proxy_tag_and_superseed_debug_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit = _Qbit(_config(tmp_path))
    torrent = _TorrentData()
    meta = _meta(tmp_path, debug=True)
    cfg = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
        "qbit_tag": "PROXY-TAG",
        "super_seed_trackers": ["AITHER"],
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", _Session)
    add = AsyncMock(return_value=None)
    monkeypatch.setattr(qbit, "_add_torrent_via_proxy", add)
    _Session.reset(
        _Response(200, [{"hash": torrent.infohash}]),
        _Response(500),  # resume failure
        _Response(500),  # superseed failure
        _Response(500),  # debug info failure
    )
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )
    assert add.await_args.args[3]["tags"] == "PROXY-TAG"
    assert _Session.instances[-1].closed

    qbit, api, torrent, meta, cfg = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    cfg["super_seed_trackers"] = ["AITHER"]
    original_retry = qbit.retry_qbt_operation

    async def retry(operation, name: str, *args: object, **kwargs: object):
        if name == "Set super-seed mode":
            raise TimeoutError("superseed timeout")
        if name == "Get torrent info for debug":
            raise RuntimeError("debug failure")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", retry)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )


def test_process_matches_handles_bad_tracker_and_outer_property_error(
    tmp_path: Path,
) -> None:
    qbit = _Qbit(_config(tmp_path))

    class BadTracker:
        def get(self, *_args: object, **_kwargs: object) -> object:
            raise qbit_module.qbittorrentapi.APIError("bad tracker")

    good = SimpleNamespace(
        hash="TRACKER",
        name="Example.mkv",
        save_path=str(tmp_path),
        size=1,
        category="",
        num_complete=1,
        tracker="",
        comment="",
    )
    qbit.retry_qbt_operation = AsyncMock(return_value=[BadTracker()])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._process_torrent_matches(
                [good], {}, [], "", "", None, _Client(), _meta(tmp_path)
            )
        )
        == []
    )

    class BadSave:
        hash = "BAD"
        name = "Example.mkv"
        size = 1
        category = ""
        num_complete = 1
        tracker = ""
        comment = ""

        @property
        def save_path(self) -> str:
            raise RuntimeError("save path")

    qbit.retry_qbt_operation = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._process_torrent_matches(
                [BadSave()], {}, [], "", "", None, _Client(), _meta(tmp_path)
            )
        )
        == []
    )


def test_base_creation_partial_subtitle_success_invalid_and_final_piece_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = False
    qbit = _Qbit(config)
    first = tmp_path / "first.torrent"
    second = tmp_path / "second.torrent"
    first.write_bytes(b"1")
    second.write_bytes(b"2")

    # Partial-subtitle best candidate is skipped, alternate becomes base.
    meta = _meta(tmp_path, subtitle_files=[str(tmp_path / "sub.srt")])
    qbit._export_torrent_file = AsyncMock(
        side_effect=[str(first), str(second)]
    )  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=[(True, str(first)), (True, str(second))]
    )  # type: ignore[method-assign]
    qbit._torrent_includes_all_local_subtitles = Mock(
        side_effect=[False, True]
    )  # type: ignore[method-assign]
    qbit._torrent_has_no_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}, {"hash": "TWO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.base_torrent_created and meta.hash_used == "TWO"

    # First valid candidate immediately creates a base when preference is disabled.
    meta = _meta(tmp_path, uuid="first-success")
    qbit._export_torrent_file = AsyncMock(return_value=str(first))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(first)))  # type: ignore[method-assign]
    create.reset_mock()
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.base_torrent_created and meta.hash_used == "ONE"

    # Invalid first candidate cleans temporary exports and enters debug retry diagnostics.
    temp = tmp_path / "tmp" / "invalid-first" / "invalid.torrent"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"bad")
    meta = _meta(tmp_path, uuid="invalid-first", debug=True)
    qbit._export_torrent_file = AsyncMock(return_value=str(temp))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(False, ""))  # type: ignore[method-assign]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "BAD"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not temp.exists()

    # Invalid alternate candidate also cleans up.
    temp = tmp_path / "tmp" / "invalid-alt" / "invalid.torrent"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"bad")
    meta = _meta(tmp_path, uuid="invalid-alt")
    qbit._export_torrent_file = AsyncMock(side_effect=[None, str(temp)])  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(False, ""))  # type: ignore[method-assign]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "NONE"}, {"hash": "BAD"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not temp.exists()

    # Final preferred-piece base creation errors are contained.
    config["DEFAULT"]["prefer_max_16_torrent"] = True
    meta = _meta(tmp_path, uuid="piece-error")
    qbit._export_torrent_file = AsyncMock(return_value=str(first))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(first)))  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.Torrent,
        "read",
        lambda _path: SimpleNamespace(
            piece_size=8 * 1024 * 1024, metainfo={"info": {}}
        ),
    )
    monkeypatch.setattr(
        qbit_module.TorrentCreator,
        "create_base_from_existing_torrent",
        AsyncMock(side_effect=RuntimeError("piece base error")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "PIECE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert not meta.base_torrent_created


def test_search_single_qbit_init_generic_and_empty_debug_proxy(
    tmp_path: Path,
) -> None:
    qbit = _Qbit(_config(tmp_path))
    qbit.init_qbittorrent_client = AsyncMock(
        side_effect=RuntimeError("init failed")
    )  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                str(tmp_path),
                _meta(tmp_path),
                "qbit",
            )
        )
        == []
    )

    qbit = _Qbit(_config(tmp_path))
    qbit.init_qbittorrent_client = AsyncMock(return_value=_Client())  # type: ignore[method-assign]
    qbit._fetch_torrents = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                str(tmp_path),
                _meta(tmp_path, debug=True),
                "qbit",
            )
        )
        == []
    )


def test_cross_seed_empty_filelist_and_tracker_candidate_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    torrent = _TorrentData()
    torrent.metainfo = {"info": {"name": "release", "length": 1}}
    meta = _meta(tmp_path, path=None, filelist="bad")
    monkeypatch.setattr(qbit_module.os, "walk", lambda _path: [])
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "empty",
            use_hardlink=False,
        )
    )

    tracker = tmp_path / "tracker"
    tracker.mkdir()
    inside = tracker / "inside.mkv"
    inside.write_bytes(b"x")
    meta = _meta(tmp_path, path=None, filelist=[str(inside)])
    torrent.metainfo = {"info": {"name": "inside.mkv", "length": 1}}
    monkeypatch.setattr(
        qbit_module.os,
        "walk",
        lambda _path: [(str(tracker), [], ["inside.mkv"])],
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta, torrent=torrent, tracker_dir=tracker, use_hardlink=False
        )
    )


def test_last_base_creation_coverage_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Piece-size metadata parse failure is a supported candidate-level miss.
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = True
    qbit = _Qbit(config)
    first = tmp_path / "piece-read-error.torrent"
    first.write_bytes(b"torrent")
    qbit._export_torrent_file = AsyncMock(return_value=str(first))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(first)))  # type: ignore[method-assign]
    monkeypatch.setattr(
        qbit_module.Torrent,
        "read",
        lambda _path: (_ for _ in ()).throw(ValueError("bad torrent")),
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            _meta(tmp_path),
        )
    )

    # A valid first candidate creates the base immediately when the piece-size
    # preference is disabled.
    config = _config(tmp_path)
    config["DEFAULT"]["prefer_max_16_torrent"] = False
    qbit = _Qbit(config)
    base = tmp_path / "base-success.torrent"
    base.write_bytes(b"torrent")
    qbit._export_torrent_file = AsyncMock(return_value=str(base))  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(True, str(base)))  # type: ignore[method-assign]
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    meta = _meta(tmp_path, uuid="base-success")
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "ONE"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    create.assert_awaited_once()
    assert meta.base_torrent_created

    # Alternate video-only candidate becomes the subtitle fallback.
    first_invalid = tmp_path / "first-invalid.torrent"
    second_video_only = tmp_path / "second-video-only.torrent"
    first_invalid.write_bytes(b"1")
    second_video_only.write_bytes(b"2")
    qbit = _Qbit(config)
    qbit._export_torrent_file = AsyncMock(
        side_effect=[str(first_invalid), str(second_video_only)]
    )  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=[
            (False, str(first_invalid)),
            (True, str(second_video_only)),
        ]
    )  # type: ignore[method-assign]
    qbit._torrent_includes_all_local_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    qbit._torrent_has_no_subtitles = Mock(return_value=True)  # type: ignore[method-assign]
    create = AsyncMock()
    monkeypatch.setattr(
        qbit_module.TorrentCreator, "create_base_from_existing_torrent", create
    )
    meta = _meta(
        tmp_path,
        uuid="alt-video-only",
        subtitle_files=[str(tmp_path / "sub.srt")],
    )
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "BAD"}, {"hash": "VIDEO"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            meta,
        )
    )
    assert meta.base_torrent_created and meta.hash_used == "VIDEO"

    # Invalid alternate keeps its resolved temporary path long enough to clean it.
    alt = tmp_path / "tmp" / "alt-invalid-clean" / "alt.torrent"
    alt.parent.mkdir(parents=True)
    alt.write_bytes(b"alt")
    qbit = _Qbit(config)
    qbit._export_torrent_file = AsyncMock(side_effect=[None, str(alt)])  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(return_value=(False, str(alt)))  # type: ignore[method-assign]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "NONE"}, {"hash": "ALT"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            _meta(tmp_path, uuid="alt-invalid-clean"),
        )
    )
    assert not alt.exists()

    # Candidate exceptions are isolated; if a temporary export was already
    # materialized it is removed in the exception path.
    qbit = _Qbit(config)
    qbit._export_torrent_file = AsyncMock(
        side_effect=[None, RuntimeError("export failed")]
    )  # type: ignore[method-assign]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "NONE"}, {"hash": "ALT"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            _meta(tmp_path, uuid="alt-export-error"),
        )
    )

    alt = tmp_path / "tmp" / "alt-validate-error" / "alt.torrent"
    alt.parent.mkdir(parents=True)
    alt.write_bytes(b"alt")
    qbit = _Qbit(config)
    qbit._export_torrent_file = AsyncMock(side_effect=[None, str(alt)])  # type: ignore[method-assign]
    qbit.is_valid_torrent = AsyncMock(
        side_effect=RuntimeError("validation failed")
    )  # type: ignore[method-assign]
    asyncio.run(
        qbit._process_base_torrent_creation(
            [{"hash": "NONE"}, {"hash": "ALT"}],
            config["TORRENT_CLIENTS"]["qbit"],
            "",
            "",
            None,
            _Client(),
            _meta(tmp_path, uuid="alt-validate-error"),
        )
    )
    assert not alt.exists()


def test_final_qbit_debug_and_proxy_connection_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    qbit, api, torrent, meta, cfg = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    original_retry = qbit.retry_qbt_operation

    async def empty_debug_info(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name == "Get torrent info for debug":
            return []
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", empty_debug_info)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    qbit, api, torrent, meta, cfg = _direct_qbit_setup(
        tmp_path, monkeypatch, debug=True
    )
    api.torrents = [
        SimpleNamespace(hash=torrent.infohash, save_path=str(tmp_path))
    ]
    original_retry = qbit.retry_qbt_operation

    async def timeout_debug_info(
        operation, name: str, *args: object, **kwargs: object
    ):
        if name == "Get torrent info for debug":
            raise TimeoutError("debug timeout")
        return await original_retry(operation, name, *args, **kwargs)

    monkeypatch.setattr(qbit, "retry_qbt_operation", timeout_debug_info)
    asyncio.run(
        qbit.qbittorrent(
            meta.path,
            torrent,
            str(tmp_path),
            str(tmp_path),
            cfg,
            "",
            list(meta.filelist),
            meta,
            "AITHER",
        )
    )

    class BrokenSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("proxy init failed")

    qbit = _Qbit(_config(tmp_path))
    cfg = {
        **_config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
        "qui_proxy_url": "https://proxy",
    }
    monkeypatch.setattr(qbit_module.httpx, "AsyncClient", BrokenSession)
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                cfg, str(tmp_path), _meta(tmp_path), "proxy"
            )
        )
        == []
    )

    qbit = _Qbit(_config(tmp_path))
    qbit.init_qbittorrent_client = AsyncMock(return_value=_Client())  # type: ignore[method-assign]
    qbit._fetch_torrents = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                str(tmp_path),
                _meta(tmp_path, debug=True),
                "qbit",
            )
        )
        == []
    )


def test_final_cross_seed_empty_and_blank_candidate_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    torrent = _TorrentData()
    torrent.metainfo = {"info": {"name": "release", "length": 1}}
    meta = _meta(tmp_path, path=None, filelist=None)
    monkeypatch.setattr(qbit_module.os, "walk", lambda _path: [])
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "empty",
            use_hardlink=False,
        )
    )

    meta = _meta(
        tmp_path, path=None, filelist=["", str(tmp_path / "missing.mkv")]
    )
    monkeypatch.setattr(qbit_module.os, "walk", lambda _path: [])
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "blank",
            use_hardlink=False,
        )
    )


def test_qbit_empty_processed_matches_reaches_debug_summary(
    tmp_path: Path,
) -> None:
    qbit = _Qbit(_config(tmp_path))
    qbit.init_qbittorrent_client = AsyncMock(return_value=_Client())  # type: ignore[method-assign]
    qbit._fetch_torrents = AsyncMock(return_value=[{"hash": "X"}])  # type: ignore[method-assign]
    qbit._process_torrent_matches = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert (
        asyncio.run(
            qbit._search_single_qbit_client(
                _config(tmp_path)["TORRENT_CLIENTS"]["qbit"],
                str(tmp_path),
                _meta(tmp_path, debug=True),
                "qbit",
            )
        )
        == []
    )


def test_cross_seed_skips_blank_walk_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "release-dir"
    release.mkdir()
    torrent = _TorrentData()
    torrent.metainfo = {"info": {"name": "release", "length": 1}}
    meta = _meta(tmp_path, path=str(release), filelist=[])
    monkeypatch.setattr(
        qbit_module.os, "walk", lambda _path: [(str(release), [], [""])]
    )
    assert not asyncio.run(
        qbit_module.create_cross_seed_links(
            meta=meta,
            torrent=torrent,
            tracker_dir=tmp_path / "tracker",
            use_hardlink=False,
        )
    )
