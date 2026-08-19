from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent_clients import bandwidth as bandwidth_service
from src.integrations.torrent_clients.bandwidth import Wait


@dataclass
class _Torrent:
    hash: str = "abc"
    state: str = "completed"
    progress: float = 1.0


class _QbitClient:
    instances: ClassVar[list[_QbitClient]] = []
    fail_version = False
    fail_login = False

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.info_queue: list[object] = [[_Torrent()]]
        self.transfer_queue: list[object] = [{"up_info_speed": 0}]
        self.rechecked: list[str] = []
        self.__class__.instances.append(self)

    def app_version(self) -> str:
        if self.fail_version:
            raise RuntimeError("bad key")
        return "5.0"

    def auth_log_in(self) -> None:
        if self.fail_login:
            raise bandwidth_service.qbittorrentapi.LoginFailed("bad login")

    def torrents_info(self, **_kwargs: object) -> object:
        if len(self.info_queue) > 1:
            return self.info_queue.pop(0)
        return self.info_queue[0]

    def transfer_info(self) -> object:
        if len(self.transfer_queue) > 1:
            return self.transfer_queue.pop(0)
        return self.transfer_queue[0]

    def torrents_recheck(self, *, torrent_hashes: str) -> None:
        self.rechecked.append(torrent_hashes)


class _Response:
    def __init__(self, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else [{"hash": "abc", "state": "completed", "progress": 1.0}]

    def json(self) -> object:
        return self._payload


class _AsyncClient:
    queue: ClassVar[list[_Response]] = []
    instances: ClassVar[list[_AsyncClient]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.closed = False
        self.__class__.instances.append(self)

    @classmethod
    def reset(cls, *responses: _Response) -> None:
        cls.queue = list(responses)
        cls.instances = []

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return self.queue.pop(0) if self.queue else _Response()

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return self.queue.pop(0) if self.queue else _Response()

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _boundary_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _QbitClient.instances = []
    _QbitClient.fail_version = False
    _QbitClient.fail_login = False
    _AsyncClient.reset()
    monkeypatch.setattr(bandwidth_service.qbittorrentapi, "Client", _QbitClient)
    monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", _AsyncClient)

    async def no_sleep(_delay: float = 0) -> None:
        return None

    monkeypatch.setattr(bandwidth_service.asyncio, "sleep", no_sleep)


def _config(*, proxy: bool = False, api_key: bool = False) -> dict[str, Any]:
    client: dict[str, Any] = {
        "qbit_url": "https://qbit.invalid",
        "qbit_port": 443,
        "qbit_user": "user",
        "qbit_pass": "pass",
        "VERIFY_WEBUI_CERTIFICATE": "false",
    }
    if proxy:
        client["qui_proxy_url"] = "https://proxy.invalid/"
    if api_key:
        client["qbit_api_key"] = "key"
    return {
        "DEFAULT": {"default_torrent_client": "primary"},
        "TORRENT_CLIENTS": {"primary": client},
    }


def _meta(tmp_path: Path, *, comments: object | None = None, hash_used: str = "") -> Meta:
    path = tmp_path / "Release.mkv"
    path.write_bytes(b"media")
    return Meta(
        base_dir=str(tmp_path),
        uuid="bandwidth",
        path=str(path),
        name="Release",
        torrent_comments=comments if comments is not None else [],
        hash_used=hash_used,
    )


def test_connection_validation_and_authentication_paths() -> None:
    with pytest.raises(ValueError, match="default_torrent_client"):
        Wait({"DEFAULT": {}, "TORRENT_CLIENTS": {}})
    with pytest.raises(ValueError, match="No torrent client"):
        Wait({"DEFAULT": {"default_torrent_client": "missing"}, "TORRENT_CLIENTS": {}})
    with pytest.raises(ValueError, match="Missing required"):
        Wait({"DEFAULT": {"default_torrent_client": "primary"}, "TORRENT_CLIENTS": {"primary": {"qbit_url": "host"}}})
    empty_host = _config()
    empty_host["TORRENT_CLIENTS"]["primary"]["qbit_url"] = ""
    with pytest.raises(ValueError, match="qbit_url"):
        Wait(empty_host)

    password_wait = Wait(_config())
    assert password_wait.qbt_client is _QbitClient.instances[-1]
    assert _QbitClient.instances[-1].kwargs["VERIFY_WEBUI_CERTIFICATE"] is False

    key_wait = Wait(_config(api_key=True))
    assert key_wait.qbt_client is _QbitClient.instances[-1]
    assert _QbitClient.instances[-1].kwargs["api_key"] == "key"

    _QbitClient.fail_version = True
    with pytest.raises(RuntimeError, match="API Key verification"):
        Wait(_config(api_key=True))
    _QbitClient.fail_version = False
    _QbitClient.fail_login = True
    with pytest.raises(RuntimeError, match="login failed"):
        Wait(_config())

    proxy_wait = Wait(_config(proxy=True))
    assert proxy_wait.proxy_url == "https://proxy.invalid/"
    assert proxy_wait.qbt_proxy_url == "https://proxy.invalid"
    assert proxy_wait.qbt_client is None


def test_wait_for_completion_direct_and_proxy_paths() -> None:
    async def exercise() -> None:
        direct = Wait(_config())
        client = direct.qbt_client
        assert isinstance(client, _QbitClient)
        client.info_queue = [[_Torrent(state="checkingUP", progress=0.5)], [_Torrent(state="completed")]]
        await direct.wait_for_completion("abc", 0)

        client.info_queue = [[]]
        await direct.wait_for_completion("missing", 0)

        unconfigured = object.__new__(Wait)
        unconfigured.proxy_url = None
        unconfigured.qbt_client = None
        unconfigured.qbt_session = None
        with pytest.raises(Exception, match="not configured"):
            await unconfigured.wait_for_completion("abc", 0)

        proxy = Wait(_config(proxy=True))
        _AsyncClient.reset(
            _Response(200, [{"hash": "abc", "state": "checkingDL", "progress": 0.2}]),
            _Response(200, [{"hash": "abc", "state": "seeding", "progress": 1.0}]),
        )
        await proxy.wait_for_completion("abc", 0)
        assert _AsyncClient.instances[-1].closed is True

        proxy = Wait(_config(proxy=True))
        _AsyncClient.reset(_Response(500, {}))
        await proxy.wait_for_completion("abc", 0)

    asyncio.run(exercise())


def test_wait_for_bandwidth_success_proxy_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        direct = Wait(_config())
        client = direct.qbt_client
        assert isinstance(client, _QbitClient)
        assert await direct.wait_for_bandwidth(0, 10) is False
        client.transfer_queue = [{"up_info_speed": 2048}, {"up_info_speed": 0}]
        assert await direct.wait_for_bandwidth(1, 10) is True

        class _Transfer:
            up_info_speed = 0

        client.transfer_queue = [_Transfer()]
        assert await direct.wait_for_bandwidth(1, 5) is True
        client.transfer_queue = [{"up_info_speed": "bad"}]
        assert await direct.wait_for_bandwidth(1, 5) is False

        proxy = Wait(_config(proxy=True))
        fallback = _QbitClient(qbit_url="host")
        fallback.transfer_queue = [{"up_info_speed": 0}]
        monkeypatch.setattr(proxy, "_connect_qbittorrent", lambda *, use_proxy=True: fallback if not use_proxy else None)
        assert await proxy.wait_for_bandwidth(1, 5) is True

        monkeypatch.setattr(proxy, "_connect_qbittorrent", lambda **_kwargs: None)
        assert await proxy.wait_for_bandwidth(1, 5) is False

    asyncio.run(exercise())


def test_select_and_recheck_direct_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        wait = Wait(_config())
        assert await wait.select_and_recheck_best_torrent(_meta(tmp_path, comments="bad"), str(tmp_path)) is False
        assert await wait.select_and_recheck_best_torrent(_meta(tmp_path, comments=[]), "") is False
        no_match = _meta(tmp_path, comments=[{"name": "other", "hash": "other", "has_working_tracker": True}])
        assert await wait.select_and_recheck_best_torrent(no_match, str(tmp_path / "Release.mkv")) is True
        invalid_hash = _meta(
            tmp_path,
            comments=[{"name": "Release", "hash": 1, "has_working_tracker": True, "seeders": 3}],
        )
        assert await wait.select_and_recheck_best_torrent(invalid_hash, str(tmp_path / "Release.mkv")) is False

        meta = _meta(
            tmp_path,
            comments=[
                {"name": "Release", "hash": "abc", "has_working_tracker": True, "seeders": 5, "trackers": "tracker"},
                {"name": "Release", "hash": "low", "has_working_tracker": True, "seeders": 1},
            ],
        )
        client = wait.qbt_client
        assert isinstance(client, _QbitClient)
        client.info_queue = [[_Torrent(state="checkingUP", progress=0.5)], [_Torrent(state="seeding", progress=1.0)]]
        assert await wait.select_and_recheck_best_torrent(meta, str(tmp_path / "Release.mkv"), 0) is True
        assert client.rechecked == ["abc"]
        assert meta.we_rechecked_torrent is True

        meta = _meta(tmp_path, comments=[], hash_used="ABC")
        client.info_queue = [(_Torrent(state="completed"),), (_Torrent(state="completed"),)]
        assert await wait.select_and_recheck_best_torrent(meta, str(tmp_path / "Release.mkv"), 0) is True

        failing = Wait(_config())
        failing_client = failing.qbt_client
        assert isinstance(failing_client, _QbitClient)

        def fail_recheck(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("recheck failed")

        monkeypatch.setattr(failing_client, "torrents_recheck", fail_recheck)
        assert await failing.select_and_recheck_best_torrent(_meta(tmp_path, comments=[], hash_used="abc"), str(tmp_path), 0) is False

    asyncio.run(exercise())


def test_select_and_recheck_proxy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        wait = Wait(_config(proxy=True))
        meta = _meta(tmp_path, comments=[], hash_used="ABC")
        _AsyncClient.reset(
            _Response(200, {}),
            _Response(200, [{"state": "checkingResumeData", "progress": "bad"}]),
            _Response(200, [{"state": "downloading", "progress": 0.9}]),
            _Response(200, [{"state": "downloading", "progress": 0.9}]),
        )
        completed: list[str] = []

        async def finished(infohash: str, _interval: int = 0) -> None:
            completed.append(infohash)

        monkeypatch.setattr(wait, "wait_for_completion", finished)
        assert await wait.select_and_recheck_best_torrent(meta, str(tmp_path), 0) is True
        assert completed == ["abc"]
        assert _AsyncClient.instances[-1].closed is True

        wait = Wait(_config(proxy=True))
        _AsyncClient.reset(_Response(500, {}))
        assert await wait.select_and_recheck_best_torrent(_meta(tmp_path, comments=[], hash_used="abc"), str(tmp_path), 0) is False

        wait = Wait(_config(proxy=True))
        wait.qbt_proxy_url = None
        assert await wait.select_and_recheck_best_torrent(_meta(tmp_path, comments=[], hash_used="abc"), str(tmp_path), 0) is False

    asyncio.run(exercise())


def test_connection_port_variants_and_unconfigured_bandwidth() -> None:
    none_port = _config()
    none_port["TORRENT_CLIENTS"]["primary"]["qbit_port"] = None
    wait = Wait(none_port)
    assert isinstance(wait.qbt_client, _QbitClient)
    assert wait.qbt_client.kwargs["port"] is None

    object_port = _config()
    object_port["TORRENT_CLIENTS"]["primary"]["qbit_port"] = object()
    wait = Wait(object_port)
    assert isinstance(wait.qbt_client, _QbitClient)
    assert isinstance(wait.qbt_client.kwargs["port"], str)

    async def exercise() -> None:
        unconfigured = object.__new__(Wait)
        unconfigured.proxy_url = None
        unconfigured.qbt_client = None
        unconfigured.qbt_session = None
        assert await unconfigured.wait_for_bandwidth(1, 5) is False
        assert await unconfigured.select_and_recheck_best_torrent(Meta(torrent_comments=[]), str(Path.cwd())) is False

    asyncio.run(exercise())


def test_wait_for_completion_uninitialized_boundary_members(monkeypatch: pytest.MonkeyPatch) -> None:
    class DirectSequenceWait(Wait):
        def __init__(self) -> None:
            self.config = {}
            self.proxy_url = None
            self.qbt_proxy_url = None
            self.qbt_session = None
            self._reads = 0

        @property
        def qbt_client(self) -> object | None:  # type: ignore[override]
            self._reads += 1
            return object() if self._reads == 1 else None

        @qbt_client.setter
        def qbt_client(self, _value: object | None) -> None:
            return None

    async def exercise() -> None:
        direct = DirectSequenceWait()
        with pytest.raises(RuntimeError, match="qbt_client"):
            await direct.wait_for_completion("abc", 0)

        proxy = object.__new__(Wait)
        proxy.config = _config(proxy=True)
        proxy.proxy_url = "https://proxy.invalid/"
        proxy.qbt_proxy_url = "https://proxy.invalid"
        proxy.qbt_client = None
        proxy.qbt_session = None
        monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", lambda: None)
        with pytest.raises(RuntimeError, match="qbt_session"):
            await proxy.wait_for_completion("abc", 0)

    asyncio.run(exercise())


def test_select_and_recheck_all_error_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        # Non-working entries are ignored before path/name matching.
        wait = Wait(_config())
        ignored = _meta(tmp_path, comments=[{"name": "Release", "hash": "abc", "has_working_tracker": False}])
        assert await wait.select_and_recheck_best_torrent(ignored, str(tmp_path / "Release.mkv"), 0) is True

        # A direct client can disappear after the initial configured check.
        class DirectSequenceWait(Wait):
            def __init__(self, values: list[object | None]) -> None:
                self.config = {}
                self.proxy_url = None
                self.qbt_proxy_url = None
                self.qbt_session = None
                self.values = values

            @property
            def qbt_client(self) -> object | None:  # type: ignore[override]
                return self.values.pop(0) if len(self.values) > 1 else self.values[0]

            @qbt_client.setter
            def qbt_client(self, value: object | None) -> None:
                self.values = [value]

        disappearing = DirectSequenceWait([object(), None])
        assert await disappearing.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        disappearing_loop = DirectSequenceWait([client, client, client, None])
        assert await disappearing_loop.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        client.info_queue = [None]
        assert await DirectSequenceWait([client] * 7).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        client.info_queue = [[]]
        assert await DirectSequenceWait([client] * 7).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        client.info_queue = [_Torrent(state="completed"), None]
        assert await DirectSequenceWait([client] * 7).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        client.info_queue = [(_Torrent(state="completed"),), ()]
        assert await DirectSequenceWait([client] * 7).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        client = _QbitClient()
        client.info_queue = [_Torrent(state="completed"), _Torrent(state="completed")]
        assert await DirectSequenceWait([client] * 7).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is True

        client = _QbitClient()
        assert (
            await DirectSequenceWait([client, client, client, client, client, None]).select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0)
            is False
        )

        # Proxy session construction and mutation failures at every phase.
        proxy = Wait(_config(proxy=True))
        monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", lambda: None)
        assert await proxy.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        class MutatingSession(_AsyncClient):
            def __init__(self, owner: Wait, *, mutation: str, responses: list[_Response]) -> None:
                super().__init__()
                self.owner = owner
                self.mutation = mutation
                self.responses = responses
                self.get_calls = 0

            async def post(self, *_args: object, **_kwargs: object) -> _Response:
                if self.mutation == "session-after-post":
                    self.owner.qbt_session = None
                if self.mutation == "url-after-post":
                    self.owner.qbt_proxy_url = None
                return self.responses.pop(0) if self.responses else _Response()

            async def get(self, *_args: object, **_kwargs: object) -> _Response:
                self.get_calls += 1
                response = self.responses.pop(0) if self.responses else _Response()
                if self.mutation == "session-before-final" and self.get_calls == 1:
                    self.owner.qbt_session = None
                if self.mutation == "url-before-final" and self.get_calls == 1:
                    self.owner.qbt_proxy_url = None
                return response

        for mutation in ("session-after-post", "url-after-post"):
            proxy = Wait(_config(proxy=True))
            session = MutatingSession(proxy, mutation=mutation, responses=[_Response(200, {})])
            monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", lambda session=session: session)
            assert await proxy.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        for mutation in ("session-before-final", "url-before-final"):
            proxy = Wait(_config(proxy=True))
            session = MutatingSession(
                proxy,
                mutation=mutation,
                responses=[_Response(200, {}), _Response(200, [{"state": "completed", "progress": 1.0}])],
            )
            monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", lambda session=session: session)
            assert await proxy.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

        for responses in (
            [_Response(200, {}), _Response(200, [])],
            [_Response(200, {}), _Response(500, {})],
            [_Response(200, {}), _Response(200, [{"state": "completed", "progress": 1.0}]), _Response(200, [])],
            [_Response(200, {}), _Response(200, [{"state": "completed", "progress": 1.0}]), _Response(500, {})],
        ):
            proxy = Wait(_config(proxy=True))
            session = MutatingSession(proxy, mutation="none", responses=list(responses))
            monkeypatch.setattr(bandwidth_service.httpx, "AsyncClient", lambda session=session: session)
            assert await proxy.select_and_recheck_best_torrent(_meta(tmp_path, hash_used="abc"), str(tmp_path), 0) is False

    asyncio.run(exercise())
