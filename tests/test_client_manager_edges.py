from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock, Mock

import pytest

from src.domain_models.release import Meta
from src.integrations.torrent_clients import client_manager
from src.integrations.torrent_clients.client_manager import Clients


class FakeTorrent:
    def __init__(self, *, piece_size: int = 4 * 1024 * 1024, files: list[str] | None = None, pieces: int = 10) -> None:
        self.piece_size = piece_size
        self.files = files or ["video.mkv"]
        self.pieces = pieces
        self.metainfo: dict[str, Any] = {"info": {"name": "release", "length": 1}}

    def verify(self, *_args: object, **_kwargs: object) -> bool:
        return True


async def _valid_path(_meta: Meta, path: str, *_args: object) -> tuple[bool, str]:
    return True, path


class Qbt:
    def __init__(self, content: bytes = b"torrent") -> None:
        self.content = content
        self.exports: list[str] = []

    def torrents_export(self, *, torrent_hash: str) -> bytes:
        self.exports.append(torrent_hash)
        return self.content


class Response:
    def __init__(self, status: int = 200, content: bytes = b"torrent") -> None:
        self.status_code = status
        self.content = content


class Session:
    queue: ClassVar[list[object]] = []
    closed: ClassVar[int] = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> Response:
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    async def aclose(self) -> None:
        type(self).closed += 1

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.closed = 0


def _config(tmp_path: Path, *, torrent_client: str = "qbit", **defaults: object) -> dict[str, Any]:
    client = {
        "torrent_client": torrent_client,
        "local_path": [str(tmp_path)],
        "remote_path": ["/remote"],
        "torrent_storage_dir": str(tmp_path / "storage"),
        "enable_search": False,
    }
    return {
        "DEFAULT": {
            "default_torrent_client": "main",
            "injecting_client_list": [],
            "searching_client_list": [],
            "inject_delay": 0,
            **defaults,
        },
        "TRACKERS": {"TEST": {}},
        "TORRENT_CLIENTS": {"main": client},
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "video.mkv"
    media.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(media),
        "filelist": [str(media)],
        "subtitle_files": [],
        "client": None,
        "debug": False,
        "no_seed": False,
        "is_disc": "",
        "keep_folder": False,
        "isdir": False,
        "max_piece_size": None,
        "prefer_small_pieces": False,
        "skip_auto_torrent": False,
        "torrenthash": None,
        "ext_torrenthash": None,
        "reuse_torrent_client": None,
    }
    state.update(values)
    return Meta(state)


def _torrent_file(meta: Meta, tracker: str = "TEST", suffix: str = "") -> Path:
    target = Path(meta.base_dir) / "tmp" / meta.uuid / f"[{tracker}{suffix}].torrent"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"torrent")
    return target


def test_read_torrent_compat_invalid_and_no_md5(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "bad.torrent"
    source.write_bytes(b"raw")
    original = RuntimeError("torf failed")
    monkeypatch.setattr(client_manager.Torrent, "read", Mock(side_effect=original))
    monkeypatch.setattr(client_manager.bencodepy, "decode", lambda _data: [])
    with pytest.raises(RuntimeError, match="torf failed"):
        Clients._read_torrent_compat(str(source), tmp_path / "normalized.torrent")

    monkeypatch.setattr(client_manager.bencodepy, "decode", lambda _data: {b"info": {b"name": b"x"}})
    with pytest.raises(RuntimeError, match="torf failed"):
        Clients._read_torrent_compat(str(source), tmp_path / "normalized.torrent")


def test_comment_id_extraction_covers_all_tracker_styles(tmp_path: Path) -> None:
    clients = Clients(_config(tmp_path))
    clients._tracker_comment_hosts = {
        "PASSTHEPOPCORN": ("passthepopcorn.me",),
        "HDBITS": ("hdbits.org",),
        "BTN": ("broadcasthe.net",),
        "BEYONDHD": ("beyond-hd.me",),
        "ORPHEUS": ("orpheus.network",),
        "AITHER": ("aither.cc",),
    }
    comment = " ".join(
        [
            "https://unknown.invalid/torrents/1",
            "https://passthepopcorn.me/torrents.php?id=1&torrentid=101",
            "https://hdbits.org/details.php?id=202",
            "https://broadcasthe.net/torrents.php?id=303",
            "https://beyond-hd.me/details/404",
            "https://orpheus.network/torrents.php?torrentid=505",
            "https://aither.cc/torrents/606",
            "https://aither.cc/torrents/no-id",
        ]
    )
    assert clients._extract_tracker_ids_from_comment(comment) == {
        "ptp": "101",
        "hdb": "202",
        "btn": "303",
        "bhd": "404",
        "orpheus": "505",
        "aither": "606",
    }
    assert clients._extract_tracker_ids_from_comment("") == {}


def test_add_to_client_guards_and_client_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients = Clients(_config(tmp_path))
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent())
    assert asyncio.run(clients.add_to_client(_meta(tmp_path, path=None), "TEST")) is None
    assert asyncio.run(clients.add_to_client(_meta(tmp_path, no_seed=True), "TEST")) is None
    assert asyncio.run(clients.add_to_client(_meta(tmp_path), "TEST")) is None

    meta = _meta(tmp_path, client="none")
    _torrent_file(meta)
    assert asyncio.run(clients.add_to_client(meta, "TEST")) is None

    config = _config(tmp_path, injecting_client_list="main")
    clients = Clients(config)
    meta = _meta(tmp_path)
    _torrent_file(meta)
    fake = FakeTorrent()
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: fake)
    clients.qbittorrent = AsyncMock()  # type: ignore[method-assign]
    clients.remote_path_map = AsyncMock(return_value=(str(tmp_path), "/remote"))  # type: ignore[method-assign]
    clients.inject_delay = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(clients.add_to_client(meta, "TEST"))
    clients.qbittorrent.assert_awaited_once()

    config["TRACKERS"]["TEST"]["client_to_skip"] = ["main"]
    clients.qbittorrent.reset_mock()
    asyncio.run(clients.add_to_client(meta, "TEST"))
    clients.qbittorrent.assert_not_awaited()


def test_add_to_client_dispatch_all_clients_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["injecting_client_list"] = ["rt", "qbit", "deluge", "transmission", "watch", "missing", "", "none"]
    for name, kind in (("rt", "rtorrent"), ("qbit", "qbit"), ("deluge", "deluge"), ("transmission", "transmission"), ("watch", "watch")):
        config["TORRENT_CLIENTS"][name] = {
            "torrent_client": kind,
            "local_path": [str(tmp_path)],
            "remote_path": ["/remote"],
            "watch_folder": str(tmp_path / "watch"),
        }
    (tmp_path / "watch").mkdir()
    clients = Clients(config)
    meta = _meta(tmp_path)
    torrent_path = _torrent_file(meta)
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent())
    clients.inject_delay = AsyncMock()  # type: ignore[method-assign]
    clients.remote_path_map = AsyncMock(return_value=(str(tmp_path), "/remote"))  # type: ignore[method-assign]
    clients.rtorrent = Mock()  # type: ignore[method-assign]
    clients.qbittorrent = AsyncMock()  # type: ignore[method-assign]
    clients.deluge = Mock()  # type: ignore[method-assign]
    clients.transmission = Mock()  # type: ignore[method-assign]
    monkeypatch.setattr(client_manager.shutil, "copy", Mock())

    asyncio.run(clients.add_to_client(meta, "TEST"))
    clients.rtorrent.assert_called_once()
    clients.qbittorrent.assert_awaited_once()
    clients.deluge.assert_called_once()
    clients.transmission.assert_called_once()
    client_manager.shutil.copy.assert_called_once_with(str(torrent_path), str(tmp_path / "watch"))

    clients.qbittorrent.side_effect = RuntimeError("client failed")
    config["DEFAULT"]["injecting_client_list"] = ["qbit"]
    asyncio.run(clients.add_to_client(meta, "TEST"))


def test_add_to_client_cross_debug_lists_default_and_bad_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    clients = Clients(config)
    fake = FakeTorrent()
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: fake)
    clients.qbittorrent = AsyncMock()  # type: ignore[method-assign]
    clients.inject_delay = AsyncMock()  # type: ignore[method-assign]
    clients.remote_path_map = AsyncMock(return_value=(str(tmp_path), "/remote"))  # type: ignore[method-assign]

    cross = _meta(tmp_path)
    _torrent_file(cross, suffix="_cross")
    asyncio.run(clients.add_to_client(cross, "TEST", cross=True))
    assert clients.qbittorrent.await_count == 1

    debug = _meta(tmp_path, debug=True)
    _torrent_file(debug, suffix="_DEBUG")
    asyncio.run(clients.add_to_client(debug, "TEST"))
    assert clients.qbittorrent.await_count == 2

    config["DEFAULT"]["injecting_client_list"] = ["", " main ", None]
    normal = _meta(tmp_path)
    _torrent_file(normal)
    asyncio.run(clients.add_to_client(normal, "TEST"))
    assert clients.qbittorrent.await_count == 3

    config["DEFAULT"].pop("injecting_client_list")
    config["DEFAULT"]["default_torrent_client"] = "none"
    asyncio.run(clients.add_to_client(normal, "TEST"))


def test_inject_delay_invalid_negative_and_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(client_manager.asyncio, "sleep", sleep)
    config = _config(tmp_path, inject_delay="bad")
    clients = Clients(config)
    asyncio.run(clients.inject_delay(_meta(tmp_path), "TEST", "main"))
    sleep.assert_not_awaited()

    config["TRACKERS"]["TEST"]["inject_delay"] = "-2"
    asyncio.run(clients.inject_delay(_meta(tmp_path), "TEST", "main"))
    sleep.assert_not_awaited()

    config["TRACKERS"]["TEST"]["inject_delay"] = "6"
    asyncio.run(clients.inject_delay(_meta(tmp_path, debug=True), "TEST", "main"))
    sleep.assert_awaited_once_with(6)

    config["TRACKERS"]["TEST"]["inject_delay"] = ""
    asyncio.run(clients.inject_delay(_meta(tmp_path), "TEST", "main"))


def test_remote_path_map_defaults_overflow_and_errors(tmp_path: Path) -> None:
    clients = Clients(_config(tmp_path))
    meta = _meta(tmp_path, path=str(tmp_path / "nested" / "video.mkv"))
    local, remote = asyncio.run(clients.remote_path_map(meta, {"local_path": [], "remote_path": []}))
    assert local == os.path.normpath("/LocalPath") and remote == os.path.normpath("/RemotePath")

    local, remote = asyncio.run(
        clients.remote_path_map(
            meta,
            {"local_path": [str(tmp_path / "elsewhere"), str(tmp_path)], "remote_path": ["/one"]},
        )
    )
    assert local == str(tmp_path) and remote == os.path.normpath("/one")

    with pytest.raises(KeyError, match="not found"):
        asyncio.run(clients.remote_path_map(meta, "missing"))
    with pytest.raises(ValueError, match="client name"):
        asyncio.run(clients.remote_path_map(meta, None))


def test_get_ptp_from_hash_guards_dispatch(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    clients = Clients({"DEFAULT": {}, "TORRENT_CLIENTS": {}})
    assert asyncio.run(clients.get_ptp_from_hash(meta)) is meta

    clients = Clients({"DEFAULT": {"default_torrent_client": "missing"}, "TORRENT_CLIENTS": {}})
    assert asyncio.run(clients.get_ptp_from_hash(meta)) is meta

    clients = Clients(
        {
            "DEFAULT": {"default_torrent_client": "rt"},
            "TORRENT_CLIENTS": {"rt": {"torrent_client": "rtorrent"}, "q": {"torrent_client": "qbit"}, "x": {"torrent_client": "other"}},
        }
    )
    clients.get_ptp_from_hash_rtorrent = AsyncMock()  # type: ignore[method-assign]
    assert asyncio.run(clients.get_ptp_from_hash(meta, True)) is meta
    clients.get_ptp_from_hash_rtorrent.assert_awaited_once()
    clients.get_ptp_from_hash_qbit = AsyncMock(return_value=meta)  # type: ignore[method-assign]
    assert asyncio.run(clients.get_ptp_from_hash(meta, client_name="q")) is meta
    clients.get_ptp_from_hash_qbit.assert_awaited_once()
    assert asyncio.run(clients.get_ptp_from_hash(meta, client_name="x")) is meta


def test_search_single_hash_storage_success_and_invalid(tmp_path: Path) -> None:
    config = _config(tmp_path)
    storage = tmp_path / "storage"
    storage.mkdir()
    clients = Clients(config)
    meta = _meta(tmp_path, torrenthash="ABC")
    clients.is_valid_torrent = AsyncMock(return_value=(True, str(storage / "ABC.torrent")))  # type: ignore[method-assign]
    result = asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None))
    assert result == str(storage / "ABC.torrent")

    clients.is_valid_torrent = AsyncMock(return_value=(False, ""))  # type: ignore[method-assign]
    meta.torrenthash = None
    meta.ext_torrenthash = "DEF"
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None

    config["TORRENT_CLIENTS"]["main"]["torrent_client"] = "rtorrent"
    config["TORRENT_CLIENTS"]["main"]["torrent_storage_dir"] = ""
    meta.ext_torrenthash = None
    meta.torrenthash = "XYZ"
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None


def test_search_single_hash_qbit_export_local_success_empty_and_errors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["TORRENT_CLIENTS"]["main"]["torrent_storage_dir"] = ""
    clients = Clients(config)
    qbt = Qbt(b"torrent")
    clients.init_qbittorrent_client = AsyncMock(return_value=qbt)  # type: ignore[method-assign]

    async def retry(operation: Any, _description: str) -> bytes:
        return await operation()

    clients.retry_qbt_operation = retry  # type: ignore[method-assign]
    clients.is_valid_torrent = AsyncMock(side_effect=_valid_path)  # type: ignore[method-assign]
    meta = _meta(tmp_path, torrenthash="ABC")
    result = asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None))
    assert result and Path(result).read_bytes() == b"torrent"

    clients.init_qbittorrent_client = AsyncMock(return_value=None)  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="none", torrenthash="ABC")
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None

    clients.init_qbittorrent_client = AsyncMock(return_value=Qbt(b""))  # type: ignore[method-assign]
    clients.retry_qbt_operation = AsyncMock(return_value=b"")  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="empty", torrenthash="ABC")
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None

    clients.retry_qbt_operation = AsyncMock(side_effect=TimeoutError("slow"))  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="timeout", torrenthash="ABC")
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None

    clients.init_qbittorrent_client = AsyncMock(side_effect=client_manager.qbittorrentapi.APIError("bad"))  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="api", torrenthash="ABC")
    assert asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None)) is None


def test_search_single_hash_proxy_status_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["torrent_storage_dir"] = ""
    client["qui_proxy_url"] = "https://proxy.example/"
    monkeypatch.setattr(client_manager.httpx, "AsyncClient", Session)
    clients = Clients(config)
    clients.is_valid_torrent = AsyncMock(side_effect=_valid_path)  # type: ignore[method-assign]

    Session.reset(Response(200, b"proxy-torrent"))
    meta = _meta(tmp_path, torrenthash="ABC")
    result = asyncio.run(clients._search_single_client_for_torrent(meta, "main", False, False, None))
    assert result and Path(result).read_bytes() == b"proxy-torrent"

    Session.reset(Response(500))
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="status", torrenthash="ABC"), "main", False, False, None)) is None
    Session.reset(RuntimeError("proxy failed"))
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="error", torrenthash="ABC"), "main", False, False, None)) is None


def test_search_qbit_search_session_cancellation_timeout_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["enable_search"] = True
    client["torrent_storage_dir"] = str(tmp_path / "storage")
    clients = Clients(config)
    session = Session()
    monkeypatch.setattr(client_manager.httpx, "AsyncClient", lambda *_args, **_kwargs: session)
    clients.create_ssl_context_for_client = Mock(return_value=False)  # type: ignore[method-assign]

    client["qui_proxy_url"] = "https://proxy"
    clients.search_qbit_for_torrent = AsyncMock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None))
    assert Session.closed >= 1

    clients.search_qbit_for_torrent = AsyncMock(side_effect=TimeoutError("slow"))  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None))

    clients.search_qbit_for_torrent = AsyncMock(side_effect=RuntimeError("bad"))  # type: ignore[method-assign]
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None)) is None


def test_search_qbit_found_storage_and_piece_preferences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["enable_search"] = True
    storage = tmp_path / "storage"
    storage.mkdir()
    clients = Clients(config)
    clients.init_qbittorrent_client = AsyncMock(return_value=Qbt())  # type: ignore[method-assign]
    clients.search_qbit_for_torrent = AsyncMock(return_value="FOUND")  # type: ignore[method-assign]
    clients.is_valid_torrent = AsyncMock(return_value=(True, str(storage / "FOUND.torrent")))  # type: ignore[method-assign]
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent(piece_size=8 * 1024 * 1024))

    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None))
    assert result == str(storage / "FOUND.torrent")

    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", True, True, None))
    assert result == str(storage / "FOUND.torrent")

    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent(piece_size=32 * 1024 * 1024))
    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", True, True, None))
    assert isinstance(result, dict) and result["torrenthash"] == "FOUND"
    best = {"torrenthash": "OLD", "torrent_path": "old", "piece_size": 64 * 1024 * 1024}
    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", True, True, best))
    assert isinstance(result, dict) and result["piece_size"] == 32 * 1024 * 1024


def test_search_qbit_found_export_local_proxy_and_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["enable_search"] = True
    client["torrent_storage_dir"] = ""
    clients = Clients(config)
    qbt = Qbt(b"exported")
    clients.init_qbittorrent_client = AsyncMock(return_value=qbt)  # type: ignore[method-assign]
    clients.search_qbit_for_torrent = AsyncMock(return_value="FOUND")  # type: ignore[method-assign]
    clients.retry_qbt_operation = AsyncMock(return_value=b"exported")  # type: ignore[method-assign]
    clients.is_valid_torrent = AsyncMock(side_effect=_valid_path)  # type: ignore[method-assign]
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent())
    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None))
    assert result and Path(result).exists()

    clients.init_qbittorrent_client = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="none"), "main", False, False, None)) is None

    clients.init_qbittorrent_client = AsyncMock(return_value=qbt)  # type: ignore[method-assign]
    clients.retry_qbt_operation = AsyncMock(side_effect=TimeoutError("export"))  # type: ignore[method-assign]
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="timeout"), "main", False, False, None)) is None

    client["qui_proxy_url"] = "https://proxy"
    monkeypatch.setattr(client_manager.httpx, "AsyncClient", Session)
    clients.search_qbit_for_torrent = AsyncMock(return_value="PROXY")  # type: ignore[method-assign]
    Session.reset(Response(200, b"proxy"))
    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="proxy"), "main", False, False, None))
    assert result and Path(result).exists()

    Session.reset(Response(500))
    clients.search_qbit_for_torrent = AsyncMock(return_value="BAD")  # type: ignore[method-assign]
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="bad"), "main", False, False, None)) is None


def test_find_existing_torrent_result_modes_subtitle_fallback_and_best(tmp_path: Path) -> None:
    config = _config(tmp_path, prefer_max_16_torrent=True)
    config["DEFAULT"]["searching_client_list"] = ["missing", "main"]
    clients = Clients(config)
    meta = _meta(tmp_path)
    assert asyncio.run(clients.find_existing_torrent(_meta(tmp_path, skip_auto_torrent=True))) is None

    clients._search_single_client_for_torrent = AsyncMock(return_value="ideal.torrent")  # type: ignore[method-assign]
    assert asyncio.run(clients.find_existing_torrent(meta)) == "ideal.torrent"
    assert meta.reuse_torrent_client == "main"

    config["DEFAULT"]["prefer_max_16_torrent"] = False
    clients = Clients(config)
    clients._search_single_client_for_torrent = AsyncMock(return_value={"torrenthash": "A", "torrent_path": "best.torrent", "piece_size": 1})  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="dict")
    assert asyncio.run(clients.find_existing_torrent(meta)) == "best.torrent"
    assert meta.reuse_torrent_client == "main"

    config["DEFAULT"]["prefer_max_16_torrent"] = True
    clients = Clients(config)
    clients._search_single_client_for_torrent = AsyncMock(return_value={"torrenthash": "A", "torrent_path": "best.torrent", "piece_size": 20})  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="best")
    assert asyncio.run(clients.find_existing_torrent(meta)) == "best.torrent"

    clients = Clients(config)
    clients._search_single_client_for_torrent = AsyncMock(return_value="video-only.torrent")  # type: ignore[method-assign]
    clients._torrent_includes_all_local_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    clients._torrent_has_no_subtitles = Mock(return_value=True)  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="subs", subtitle_files=[str(tmp_path / "sub.srt")])
    assert asyncio.run(clients.find_existing_torrent(meta)) == "video-only.torrent"
    assert meta.reuse_torrent_client == "main"

    clients._torrent_has_no_subtitles = Mock(return_value=False)  # type: ignore[method-assign]
    assert asyncio.run(clients.find_existing_torrent(_meta(tmp_path, uuid="partial", subtitle_files=["sub.srt"]))) is None


def test_find_existing_client_lists_and_no_clients(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["default_torrent_client"] = "none"
    assert asyncio.run(Clients(config).find_existing_torrent(_meta(tmp_path))) is None

    config["DEFAULT"]["searching_client_list"] = "invalid"
    assert asyncio.run(Clients(config).find_existing_torrent(_meta(tmp_path, uuid="invalid"))) is None

    config["DEFAULT"]["default_torrent_client"] = "main"
    meta = _meta(tmp_path, uuid="chosen", client="main")
    clients = Clients(config)
    clients._search_single_client_for_torrent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(clients.find_existing_torrent(meta)) is None


def test_add_to_client_meta_client_and_broken_default_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    clients = Clients(config)
    meta = _meta(tmp_path, client="main")
    _torrent_file(meta)
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent())
    clients.inject_delay = AsyncMock()  # type: ignore[method-assign]
    clients.remote_path_map = AsyncMock(return_value=(str(tmp_path), "/remote"))  # type: ignore[method-assign]
    clients.qbittorrent = AsyncMock()  # type: ignore[method-assign]
    asyncio.run(clients.add_to_client(meta, "TEST"))
    clients.qbittorrent.assert_awaited_once()

    class FlakyDefault(dict[str, Any]):
        calls = 0

        def get(self, key: str, default: object = None) -> Any:
            type(self).calls += 1
            if type(self).calls == 1:
                raise RuntimeError("bad injecting list")
            return super().get(key, default)

    config = _config(tmp_path)
    config["DEFAULT"] = FlakyDefault(config["DEFAULT"])
    clients = Clients(config)
    clients.qbittorrent = AsyncMock()  # type: ignore[method-assign]
    clients.inject_delay = AsyncMock()  # type: ignore[method-assign]
    clients.remote_path_map = AsyncMock(return_value=(str(tmp_path), "/remote"))  # type: ignore[method-assign]
    meta = _meta(tmp_path, uuid="flaky")
    _torrent_file(meta)
    asyncio.run(clients.add_to_client(meta, "TEST"))
    clients.qbittorrent.assert_awaited_once()


def test_inject_delay_tracker_invalid_and_global_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(client_manager.asyncio, "sleep", sleep)
    config = _config(tmp_path)
    config["TRACKERS"]["TEST"]["inject_delay"] = "bad"
    asyncio.run(Clients(config).inject_delay(_meta(tmp_path), "TEST", "main"))
    sleep.assert_not_awaited()

    config["TRACKERS"]["TEST"].pop("inject_delay")
    config["DEFAULT"]["inject_delay"] = 6
    asyncio.run(Clients(config).inject_delay(_meta(tmp_path), "TEST", "main"))
    sleep.assert_awaited_once_with(6)


def test_find_existing_falls_back_to_default_when_search_list_empty(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["DEFAULT"]["searching_client_list"] = []
    clients = Clients(config)
    clients._search_single_client_for_torrent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    asyncio.run(clients.find_existing_torrent(_meta(tmp_path)))
    clients._search_single_client_for_torrent.assert_awaited_once()


def test_search_qbit_proxy_generic_export_error_and_outer_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["enable_search"] = True
    client["torrent_storage_dir"] = ""
    client["qui_proxy_url"] = "https://proxy"
    clients = Clients(config)
    clients.search_qbit_for_torrent = AsyncMock(return_value="FOUND")  # type: ignore[method-assign]
    monkeypatch.setattr(client_manager.httpx, "AsyncClient", Session)
    Session.reset(RuntimeError("proxy export failed"))
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path), "main", False, False, None)) is None

    client.pop("qui_proxy_url")
    clients.init_qbittorrent_client = AsyncMock(return_value=Qbt())  # type: ignore[method-assign]
    clients.search_qbit_for_torrent = AsyncMock(return_value="WRITE")  # type: ignore[method-assign]
    clients.retry_qbt_operation = AsyncMock(return_value=object())  # type: ignore[method-assign]
    assert asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="write"), "main", False, False, None)) is None


def test_search_qbit_existing_found_torrent_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    client = config["TORRENT_CLIENTS"]["main"]
    client["enable_search"] = True
    client["torrent_storage_dir"] = ""
    clients = Clients(config)
    clients.init_qbittorrent_client = AsyncMock(return_value=Qbt())  # type: ignore[method-assign]
    clients.search_qbit_for_torrent = AsyncMock(return_value="FOUND")  # type: ignore[method-assign]
    existing = Path(tmp_path) / "tmp" / "existing" / "FOUND.torrent"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"torrent")
    clients.is_valid_torrent = AsyncMock(return_value=(True, str(existing)))  # type: ignore[method-assign]
    monkeypatch.setattr(client_manager.Torrent, "read", lambda _path: FakeTorrent())
    result = asyncio.run(clients._search_single_client_for_torrent(_meta(tmp_path, uuid="existing"), "main", False, False, None))
    assert result == str(existing)


def test_is_valid_torrent_missing_path_read_error_and_rtorrent_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients = Clients(_config(tmp_path))
    assert asyncio.run(clients.is_valid_torrent(_meta(tmp_path, path=None), "missing", "ABC", "qbit", {})) == (False, "missing")

    path = tmp_path / "bad.torrent"
    path.write_bytes(b"bad")
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(side_effect=RuntimeError("read failed")))
    valid, resolved = asyncio.run(clients.is_valid_torrent(_meta(tmp_path), str(path), "ABC", "qbit", {}))
    assert not valid and resolved.endswith("bad.torrent")

    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(FakeTorrent(), str(path))))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path, debug=True), str(path), "abc", "rtorrent", {}))
    assert valid


def test_is_valid_torrent_disc_layout_single_multi_and_verify_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "reuse.torrent"
    path.write_bytes(b"torrent")
    clients = Clients(_config(tmp_path))

    disc_torrent = FakeTorrent(files=[str(tmp_path / "Disc" / "video.m2ts")])
    disc_torrent.metainfo = {"info": {"name": "other", "length": 1}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(disc_torrent, str(path))))
    disc_meta = _meta(tmp_path, path=str(tmp_path / "Disc"), uuid="release", is_disc="BDMV", filelist=[str(tmp_path / "Disc" / "video.m2ts")], debug=True)
    (tmp_path / "Disc").mkdir(exist_ok=True)
    (tmp_path / "Disc" / "video.m2ts").write_bytes(b"v")
    valid, _ = asyncio.run(clients.is_valid_torrent(disc_meta, str(path), "ABC", "qbit", {}))
    assert valid

    single_file = Path(_meta(tmp_path).filelist[0])
    single = FakeTorrent(files=[str(single_file)])
    single.metainfo = {"info": {"name": "release", "length": single_file.stat().st_size}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(single, str(path))))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path), str(path), "ABC", "qbit", {}))
    assert valid

    single.verify = Mock(side_effect=RuntimeError("verify failed"))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path), str(path), "ABC", "qbit", {}))
    assert not valid

    folder = tmp_path / "multi"
    (folder / "a").mkdir(parents=True)
    (folder / "b").mkdir(parents=True)
    local_a = folder / "a" / "same.mkv"
    local_b = folder / "b" / "same.mkv"
    local_a.write_bytes(b"a")
    local_b.write_bytes(b"b")
    torrent = FakeTorrent(files=[str(folder / "a" / "same.mkv"), str(folder / "b" / "same.mkv")])
    torrent.metainfo = {"info": {"name": "multi"}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(torrent, str(path))))
    multi_meta = _meta(tmp_path, path=str(folder), filelist=[str(local_a), str(local_b)])
    valid, _ = asyncio.run(clients.is_valid_torrent(multi_meta, str(path), "ABC", "qbit", {}))
    assert valid


def test_is_valid_torrent_piece_restrictions_and_stat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "reuse.torrent"
    path.write_bytes(b"torrent")
    media = Path(_meta(tmp_path).filelist[0])
    clients = Clients(_config(tmp_path))

    def run_with(torrent: FakeTorrent, **meta_values: object) -> bool:
        torrent.files = [str(media)]
        torrent.metainfo = {"info": {"name": "release", "length": media.stat().st_size}}
        monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(torrent, str(path))))
        valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path, **meta_values), str(path), "ABC", "qbit", {}))
        return valid

    assert not run_with(FakeTorrent(piece_size=1024 * 1024, pieces=5000))
    assert not run_with(FakeTorrent(piece_size=4 * 1024 * 1024, pieces=8000))
    assert not run_with(FakeTorrent(piece_size=16 * 1024 * 1024, pieces=12000))
    assert not run_with(FakeTorrent(piece_size=16 * 1024, pieces=1))

    path.write_bytes(b"x" * (251 * 1024))
    assert not run_with(FakeTorrent(piece_size=4 * 1024 * 1024, pieces=1))
    path.write_bytes(b"torrent")

    original_stat = Path.stat

    def bad_stat(target: Path, *args: object, **kwargs: object):
        if target == path:
            raise OSError("stat failed")
        return original_stat(target, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", bad_stat)
    assert not run_with(FakeTorrent(piece_size=4 * 1024 * 1024, pieces=1))


def test_torrent_subtitle_and_piece_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients = Clients(_config(tmp_path))
    meta = _meta(tmp_path, subtitle_files=[])
    assert clients._torrent_includes_all_local_subtitles("unused", meta)

    monkeypatch.setattr(client_manager.Torrent, "read", Mock(side_effect=RuntimeError("bad")))
    assert not clients._torrent_includes_all_local_subtitles("bad", _meta(tmp_path, subtitle_files=["sub.srt"]))
    assert not clients._torrent_has_no_subtitles("bad")
    assert not clients._is_preferred_piece_size_candidate("bad", "bad2", True)

    torrents = {
        "candidate": FakeTorrent(piece_size=8 * 1024 * 1024),
        "current": FakeTorrent(piece_size=32 * 1024 * 1024),
    }
    monkeypatch.setattr(client_manager.Torrent, "read", lambda value: torrents[str(value)])
    assert clients._is_preferred_piece_size_candidate("candidate", "current", True)
    assert not clients._is_preferred_piece_size_candidate("candidate", "current", False)


def test_remote_path_map_root_preserves_separator(tmp_path: Path) -> None:
    clients = Clients(_config(tmp_path))
    meta = _meta(tmp_path, path="/video.mkv")
    local, remote = asyncio.run(clients.remote_path_map(meta, {"local_path": ["/"], "remote_path": ["/remote"]}))
    assert local == os.path.normpath("/") and remote.endswith(os.sep)


def test_final_client_manager_validation_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clients = Clients(_config(tmp_path))
    torrent_path = tmp_path / "reuse.torrent"
    torrent_path.write_bytes(b"torrent")

    # Subtitle-inclusive layout is considered after the video-only candidate.
    video_file = Path(_meta(tmp_path).filelist[0])
    subtitle = tmp_path / "video.srt"
    subtitle.write_bytes(b"sub")
    torrent = FakeTorrent(files=[str(video_file), str(subtitle)])
    torrent.metainfo = {"info": {"name": "release"}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(torrent, str(torrent_path))))
    valid, _ = asyncio.run(
        clients.is_valid_torrent(
            _meta(tmp_path, subtitle_files=[str(subtitle)]),
            str(torrent_path),
            "ABC",
            "qbit",
            {},
        )
    )
    assert valid

    # Single-file name mismatch remains invalid and reaches the debug branch.
    mismatch = FakeTorrent(files=[str(tmp_path / "different.mkv")])
    mismatch.metainfo = {"info": {"name": "release", "length": 1}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(mismatch, str(torrent_path))))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path, debug=True), str(torrent_path), "ABC", "qbit", {}))
    assert not valid

    # Missing reusable torrent is a normal miss and also exercises the debug
    # diagnostics for an unwanted candidate.
    missing = tmp_path / "missing.torrent"
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path, debug=True), str(missing), "ABC", "qbit", {}))
    assert not valid

    # 8k-piece boundary uses the second piece-size policy (the 4 MiB guard must
    # not match first).
    boundary = FakeTorrent(piece_size=5 * 1024 * 1024, pieces=8000, files=[str(video_file)])
    boundary.metainfo = {"info": {"name": "release", "length": video_file.stat().st_size}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(boundary, str(torrent_path))))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path), str(torrent_path), "ABC", "qbit", {}))
    assert not valid

    class BrokenPieceTorrent(FakeTorrent):
        @property
        def piece_size(self) -> int:  # type: ignore[override]
            raise RuntimeError("piece metadata failed")

        @piece_size.setter
        def piece_size(self, _value: int) -> None:
            return None

    broken = BrokenPieceTorrent(files=[str(video_file)])
    broken.metainfo = {"info": {"name": "release", "length": video_file.stat().st_size}}
    monkeypatch.setattr(clients, "_read_torrent_compat", Mock(return_value=(broken, str(torrent_path))))
    valid, _ = asyncio.run(clients.is_valid_torrent(_meta(tmp_path), str(torrent_path), "ABC", "qbit", {}))
    assert not valid
