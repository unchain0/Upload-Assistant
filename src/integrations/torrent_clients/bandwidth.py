# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import os
import traceback
from collections import deque
from dataclasses import dataclass
from typing import Any, cast

import httpx
import qbittorrentapi

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import console, logger

_COMPLETED_STATES = frozenset(
    {"pausedUP", "seeding", "completed", "stalledUP", "uploading"}
)
_CHECKING_STATES = frozenset(
    {"checkingUP", "checkingDL", "checkingResumeData"}
)


@dataclass(frozen=True)
class _TorrentSnapshot:
    state: str
    progress: float


class Wait:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.proxy_url: str | None = None
        self.qbt_proxy_url: str | None = None
        self.qbt_session: httpx.AsyncClient | None = None
        self.qbt_client: qbittorrentapi.Client | None = None
        self.qbt_client = self._connect_qbittorrent()

    def _client_config(self) -> dict[str, Any]:
        default_section = cast(dict[str, Any], self.config.get("DEFAULT", {}))
        clients_section = cast(
            dict[str, Any], self.config.get("TORRENT_CLIENTS", {})
        )
        default_client = default_section.get("default_torrent_client", "")
        if not isinstance(default_client, str) or not default_client:
            raise ValueError(
                "DEFAULT.default_torrent_client is not configured"
            )
        client_obj = clients_section.get(default_client)
        if not isinstance(client_obj, dict):
            raise ValueError(
                f"No torrent client configuration for '{default_client}'"
            )
        return cast(dict[str, Any], client_obj)

    def _configure_proxy(
        self, client: dict[str, Any], use_proxy: bool
    ) -> bool:
        proxy_value = client.get("qui_proxy_url")
        self.proxy_url = (
            proxy_value
            if isinstance(proxy_value, str) and proxy_value
            else None
        )
        self.qbt_session = None
        self.qbt_client = None
        if not self.proxy_url or not use_proxy:
            return False
        self.qbt_proxy_url = self.proxy_url.rstrip("/")
        return True

    @staticmethod
    def _required_keys(client: dict[str, Any]) -> list[str]:
        if client.get("qbit_api_key"):
            return ["qbit_url", "qbit_port", "qbit_api_key"]
        return ["qbit_url", "qbit_port", "qbit_user", "qbit_pass"]

    @classmethod
    def _validate_required_keys(cls, client: dict[str, Any]) -> None:
        missing = [
            key for key in cls._required_keys(client) if key not in client
        ]
        if missing:
            raise ValueError(
                f"Missing required qBittorrent config keys: {', '.join(missing)}"
            )

    @staticmethod
    def _verify_certificate(client: dict[str, Any]) -> bool:
        value = client.get("VERIFY_WEBUI_CERTIFICATE", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)

    @staticmethod
    def _host(client: dict[str, Any]) -> str:
        host = str(client.get("qbit_url", "")).strip()
        if not host:
            raise ValueError("qbit_url is not configured")
        return host

    @staticmethod
    def _port(client: dict[str, Any]) -> int | str | None:
        value = client.get("qbit_port")
        if isinstance(value, (int, str)):
            return value
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _verify_api_key(
        client: qbittorrentapi.Client,
    ) -> qbittorrentapi.Client:
        try:
            client.app_version()
        except Exception as error:
            raise RuntimeError(
                f"qBittorrent API Key verification failed: {error}"
            ) from error
        return client

    @staticmethod
    def _verify_login(client: qbittorrentapi.Client) -> qbittorrentapi.Client:
        try:
            client.auth_log_in()
        except qbittorrentapi.LoginFailed as error:
            raise RuntimeError(f"qBittorrent login failed: {error}") from error
        return client

    @classmethod
    def _api_key_client(
        cls,
        client: dict[str, Any],
        host: str,
        port: int | str | None,
        verify_cert: bool,
    ) -> qbittorrentapi.Client:
        value = client.get("qbit_api_key")
        api_key = str(value) if value is not None else None
        qbt_client = qbittorrentapi.Client(
            host=host,
            port=port,
            api_key=api_key,
            VERIFY_WEBUI_CERTIFICATE=verify_cert,
        )
        return cls._verify_api_key(qbt_client)

    @classmethod
    def _password_client(
        cls,
        client: dict[str, Any],
        host: str,
        port: int | str | None,
        verify_cert: bool,
    ) -> qbittorrentapi.Client:
        username_value = client.get("qbit_user")
        password_value = client.get("qbit_pass")
        username = str(username_value) if username_value is not None else None
        password = str(password_value) if password_value is not None else None
        qbt_client = qbittorrentapi.Client(
            host=host,
            port=port,
            username=username,
            password=password,
            VERIFY_WEBUI_CERTIFICATE=verify_cert,
        )
        return cls._verify_login(qbt_client)

    def _connect_qbittorrent(
        self, *, use_proxy: bool = True
    ) -> qbittorrentapi.Client | None:
        client = self._client_config()
        if self._configure_proxy(client, use_proxy):
            return None
        self._validate_required_keys(client)
        host = self._host(client)
        port = self._port(client)
        verify_cert = self._verify_certificate(client)
        if client.get("qbit_api_key"):
            return self._api_key_client(client, host, port, verify_cert)
        return self._password_client(client, host, port, verify_cert)

    def _configured(self) -> bool:
        return bool(self.proxy_url or self.qbt_client)

    async def _proxy_completion_target(
        self, infohash: str
    ) -> dict[str, Any] | None:
        if self.qbt_session is None:
            raise RuntimeError("qbt_session is not initialized")
        response = await self.qbt_session.get(
            f"{self.qbt_proxy_url}/api/v2/torrents/info",
            params={"hashes": infohash},
        )
        if response.status_code != 200:
            logger.info(
                f"[ERROR] Failed to get torrent info via proxy: {response.status_code}",
                extra={"markup": False},
            )
            return None
        data = cast(list[dict[str, Any]], response.json())
        return data[0] if data else None

    def _direct_completion_target(self, infohash: str) -> Any | None:
        if self.qbt_client is None:
            raise RuntimeError("qbt_client is not initialized")
        torrents = self.qbt_client.torrents_info(hashes=infohash)
        return next(
            (torrent for torrent in torrents if torrent.hash == infohash), None
        )

    @staticmethod
    def _completion_state(target: Any, proxy: bool) -> str:
        if proxy:
            state_value = cast(dict[str, Any], target).get("state")
        else:
            state_value = getattr(target, "state", None)
        return str(state_value) if state_value is not None else "unknown"

    async def _completion_iteration(self, infohash: str) -> str:
        target = (
            await self._proxy_completion_target(infohash)
            if self.proxy_url
            else self._direct_completion_target(infohash)
        )
        if target is None:
            logger.info(
                f"[ERROR] Torrent with hash {infohash} not found!",
                extra={"markup": False},
            )
            return "stop"
        state = self._completion_state(target, bool(self.proxy_url))
        logger.info(
            f"[DEBUG] Torrent {infohash} state: {state}",
            extra={"markup": False},
        )
        if state in _COMPLETED_STATES:
            logger.info(
                f"[INFO] Torrent {infohash} has completed!",
                extra={"markup": False},
            )
            return "complete"
        return "continue"

    async def wait_for_completion(
        self, infohash: str, check_interval: int = 3
    ) -> None:
        if not self._configured():
            raise Exception("[ERROR] qBittorrent is not configured.")
        logger.info(
            f"Waiting for torrent {infohash} to complete...",
            extra={"markup": False},
        )
        if self.proxy_url:
            self.qbt_session = httpx.AsyncClient()
        try:
            while await self._completion_iteration(infohash) == "continue":  # noqa: ASYNC110 - qBittorrent polling intentionally waits between status checks
                await asyncio.sleep(check_interval)
        finally:
            if self.qbt_session:
                await self.qbt_session.aclose()

    def _bandwidth_client(self) -> qbittorrentapi.Client:
        bandwidth_client = self.qbt_client
        if self.proxy_url:
            bandwidth_client = self._connect_qbittorrent(use_proxy=False)
        if bandwidth_client is None:
            raise RuntimeError(
                "Bandwidth control requires qbit_url/qbit_port and qBittorrent credentials"
            )
        return bandwidth_client

    @staticmethod
    def _upload_speed(client: qbittorrentapi.Client) -> int:
        data = client.transfer_info()
        raw = (
            data.get("up_info_speed", 0)
            if hasattr(data, "get")
            else getattr(data, "up_info_speed", 0)
        )
        return int(cast(int | str | float, raw))

    @staticmethod
    def _bandwidth_average(speeds: deque[int]) -> float:
        return sum(speeds) / len(speeds)

    @staticmethod
    def _bandwidth_ready(
        speeds: deque[int], max_samples: int, average: float, threshold: int
    ) -> bool:
        return len(speeds) >= max_samples and average <= threshold

    @staticmethod
    def _print_bandwidth_status(
        average: float,
        threshold_kb: int,
        samples: int,
        check_interval: int,
        *,
        complete: bool,
    ) -> None:
        average_kbs = average / 1024
        total_seconds = samples * check_interval
        color = "green" if average <= threshold_kb * 1024 else "red"
        if complete:
            console.print(
                f"[yellow]Average speed of [{color}]{average_kbs:.0f}/{threshold_kb:.0f}[/{color}] KB/s in the last {total_seconds} seconds. [/yellow]"
            )
            return
        console.print(
            f"[yellow]Average speed of [{color}]{average_kbs:.0f}[/{color}]/[green]{threshold_kb:.0f}[/green] KB/s in the last {total_seconds} seconds. [/yellow]",
            end="\r",
        )

    async def _wait_for_bandwidth_samples(
        self, client: qbittorrentapi.Client, threshold_kb: int, wait_time: int
    ) -> None:
        threshold_bytes = threshold_kb * 1024
        check_interval = 5
        max_samples = max(1, wait_time // check_interval)
        speeds: deque[int] = deque(maxlen=max_samples)
        while True:
            speeds.append(self._upload_speed(client))
            average = self._bandwidth_average(speeds)
            ready = self._bandwidth_ready(
                speeds, max_samples, average, threshold_bytes
            )
            self._print_bandwidth_status(
                average,
                threshold_kb,
                len(speeds),
                check_interval,
                complete=ready,
            )
            if ready:
                return
            await asyncio.sleep(check_interval)

    async def wait_for_bandwidth(
        self, threshold_kb: int, wait_time: int
    ) -> bool:
        if not self._configured():
            return False
        if threshold_kb <= 0 or wait_time <= 0:
            logger.info(
                "[yellow]Bandwidth control enabled but threshold or time is 0. Skipping bandwidth check.[/yellow]"
            )
            return False
        try:
            await self._wait_for_bandwidth_samples(
                self._bandwidth_client(), threshold_kb, wait_time
            )
            return True
        except Exception as error:
            logger.error(f"\n[red]Error checking bandwidth: {error}[/red]")
            return False

    @staticmethod
    def _torrent_comments(meta: Meta) -> list[dict[str, Any]] | None:
        if not isinstance(meta.torrent_comments, list):
            logger.info("[red]No torrent comments found in metadata[/red]")
            return None
        return [
            cast(dict[str, Any], comment)
            for comment in meta.torrent_comments
            if isinstance(comment, dict)
        ]

    @staticmethod
    def _comment_path_matches(
        comment: dict[str, Any], target_path: str
    ) -> bool:
        content_path = str(comment.get("content_path", "") or "")
        if not content_path:
            return False
        return (
            os.path.normpath(content_path).lower()
            == os.path.normpath(target_path).lower()
        )

    @staticmethod
    def _comment_name_matches(
        comment: dict[str, Any], meta_name: str | None
    ) -> bool:
        name = comment.get("name")
        if not isinstance(name, str) or meta_name is None:
            return False
        return name.lower() == meta_name

    @classmethod
    def _comment_matches(
        cls, comment: dict[str, Any], target_path: str, meta_name: str | None
    ) -> bool:
        if not comment.get("has_working_tracker", False):
            return False
        return cls._comment_path_matches(
            comment, target_path
        ) or cls._comment_name_matches(comment, meta_name)

    @classmethod
    def _matching_torrents(
        cls, comments: list[dict[str, Any]], target_path: str, meta: Meta
    ) -> list[dict[str, Any]]:
        meta_name = meta.name.lower() if isinstance(meta.name, str) else None
        return [
            comment
            for comment in comments
            if cls._comment_matches(comment, target_path, meta_name)
        ]

    @staticmethod
    def _best_torrent_hash(matches: list[dict[str, Any]]) -> str | None:
        matches.sort(
            key=lambda item: int(item.get("seeders", 0) or 0), reverse=True
        )
        best = matches[0]
        value = best.get("hash")
        if not isinstance(value, str):
            logger.info("[red]Best torrent is missing a valid hash[/red]")
            return None
        logger.info(
            f"[green]Selected best torrent: {best.get('name')} with {best.get('seeders', 0)} seeders[/green]"
            f"[yellow] Tracker: {str(best.get('trackers', 'unknown'))[:20]}[/yellow]"
        )
        return value.lower()

    @classmethod
    def _selected_hash(
        cls, meta: Meta, target_path: str, comments: list[dict[str, Any]]
    ) -> tuple[str | None, bool | None]:
        if isinstance(meta.hash_used, str) and meta.hash_used:
            return meta.hash_used.lower(), None
        matches = cls._matching_torrents(comments, target_path, meta)
        if not matches:
            logger.info(
                "[yellow]No matching torrents with working trackers found in qBittorrent[/yellow]"
            )
            return None, True
        torrent_hash = cls._best_torrent_hash(matches)
        if torrent_hash is None:
            return None, False
        return torrent_hash, None

    def _proxy_ready(self) -> bool:
        if self.qbt_session is None:
            logger.info("[bold red]qbt_session is not initialized")
            return False
        if self.qbt_proxy_url is None:
            logger.info("[bold red]Proxy URL is not configured correctly")
            return False
        return True

    async def _proxy_recheck(self, torrent_hash: str) -> bool:
        if not self._proxy_ready():
            return False
        session = cast(httpx.AsyncClient, self.qbt_session)
        response = await session.post(
            f"{self.qbt_proxy_url}/api/v2/torrents/recheck",
            data={"hashes": torrent_hash},
        )
        if response.status_code == 200:
            return True
        logger.info(
            f"[bold red]Failed to recheck torrent via proxy: {response.status_code}"
        )
        return False

    def _direct_recheck(self, torrent_hash: str) -> bool:
        if self.qbt_client is None:
            logger.info("[bold red]qbt_client is not initialized")
            return False
        self.qbt_client.torrents_recheck(torrent_hashes=torrent_hash)
        return True

    async def _trigger_recheck(self, torrent_hash: str) -> bool:
        try:
            success = (
                await self._proxy_recheck(torrent_hash)
                if self.proxy_url
                else self._direct_recheck(torrent_hash)
            )
            if not success:
                return False
            await asyncio.sleep(3)
            return True
        except Exception as error:
            logger.info(f"[bold red]Failed to recheck torrent: {error}")
            return False

    @staticmethod
    def _progress_value(value: Any, *, tolerant: bool) -> float:
        try:
            return float(value or 0)
        except TypeError, ValueError:
            if tolerant:
                return 0.0
            raise

    @classmethod
    def _snapshot_from_proxy_data(
        cls, data: list[dict[str, Any]], *, tolerant: bool
    ) -> _TorrentSnapshot:
        if not data:
            raise Exception("No torrents found in response")
        torrent = data[0]
        state_value = torrent.get("state")
        state = str(state_value) if state_value is not None else "unknown"
        progress = cls._progress_value(
            torrent.get("progress", 0), tolerant=tolerant
        )
        return _TorrentSnapshot(state, progress)

    @staticmethod
    def _torrent_candidates(raw: Any) -> list[Any]:
        if raw is None:
            raise Exception("qBittorrent returned no torrent info")
        if isinstance(raw, list):
            return cast(list[Any], raw)
        if isinstance(raw, tuple):
            return list(cast(tuple[Any, ...], raw))
        return [raw]

    @classmethod
    def _snapshot_from_direct_data(cls, raw: Any) -> _TorrentSnapshot:
        candidates = cls._torrent_candidates(raw)
        if not candidates:
            raise Exception("No torrents found in TorrentInfoList")
        torrent = candidates[0]
        state_value = getattr(torrent, "state", None)
        state = str(state_value) if state_value is not None else "unknown"
        progress = float(getattr(torrent, "progress", 0) or 0)
        return _TorrentSnapshot(state, progress)

    async def _proxy_snapshot(
        self, torrent_hash: str, *, final: bool
    ) -> _TorrentSnapshot | None:
        if not self._proxy_ready():
            return None
        session = cast(httpx.AsyncClient, self.qbt_session)
        response = await session.get(
            f"{self.qbt_proxy_url}/api/v2/torrents/info",
            params={"hashes": torrent_hash},
        )
        if response.status_code != 200:
            label = "final torrent info" if final else "torrent info"
            logger.info(
                f"[bold red]Failed to get {label} via proxy: {response.status_code}"
            )
            return None
        data = cast(list[dict[str, Any]], response.json())
        return self._snapshot_from_proxy_data(data, tolerant=not final)

    def _direct_snapshot(self, torrent_hash: str) -> _TorrentSnapshot | None:
        if self.qbt_client is None:
            logger.info("[bold red]qbt_client is not initialized")
            return None
        raw = cast(Any, self.qbt_client.torrents_info(hashes=torrent_hash))
        return self._snapshot_from_direct_data(raw)

    async def _snapshot(
        self, torrent_hash: str, *, final: bool
    ) -> _TorrentSnapshot | None:
        if self.proxy_url:
            return await self._proxy_snapshot(torrent_hash, final=final)
        return self._direct_snapshot(torrent_hash)

    async def _wait_for_recheck(
        self, torrent_hash: str, check_interval: int
    ) -> bool:
        while True:
            snapshot = await self._snapshot(torrent_hash, final=False)
            if snapshot is None:
                return False
            logger.info(
                f"\r[INFO] Torrent is at {snapshot.progress * 100:.2f}% progress of {snapshot.state}...",
                extra={"markup": False},
            )
            if snapshot.state not in _CHECKING_STATES:
                logger.info("", extra={"markup": False})
                return True
            await asyncio.sleep(check_interval)

    async def _finish_recheck(
        self, meta: Meta, torrent_hash: str, check_interval: int
    ) -> bool:
        final = await self._snapshot(torrent_hash, final=True)
        if final is None:
            return False
        logger.info(
            f"[green]Recheck completed. State: {final.state}, Progress: {final.progress * 100:.2f}%[/green]"
        )
        meta.we_rechecked_torrent = True
        if final.state not in _COMPLETED_STATES:
            logger.info(
                "[yellow]Torrent needs to download missing data. Waiting for completion...[/yellow]"
            )
            await self.wait_for_completion(torrent_hash, check_interval)
        return True

    async def _run_recheck_flow(
        self, meta: Meta, torrent_hash: str, check_interval: int
    ) -> bool:
        try:
            if not await self._wait_for_recheck(torrent_hash, check_interval):
                return False
            return await self._finish_recheck(
                meta, torrent_hash, check_interval
            )
        except Exception as error:
            logger.info(f"[bold red]Error while waiting for recheck: {error}")
            traceback.print_exc()
            return False

    def _can_recheck(self, path: str) -> bool:
        if not self._configured():
            logger.info("[red]qBittorrent is not configured.[/red]")
            return False
        if path:
            return True
        logger.info(
            "[red]No target path available for matching torrents[/red]"
        )
        return False

    def _recheck_hash(
        self, meta: Meta, path: str
    ) -> tuple[str | None, bool | None]:
        comments = self._torrent_comments(meta)
        if comments is None:
            return None, False
        return self._selected_hash(meta, path, comments)

    async def _execute_selected_recheck(
        self, meta: Meta, torrent_hash: str, check_interval: int
    ) -> bool:
        if self.proxy_url:
            self.qbt_session = httpx.AsyncClient()
        try:
            if not await self._trigger_recheck(torrent_hash):
                return False
            return await self._run_recheck_flow(
                meta, torrent_hash, check_interval
            )
        finally:
            if self.qbt_session:
                await self.qbt_session.aclose()

    async def select_and_recheck_best_torrent(
        self, meta: Meta, path: str, check_interval: int = 5
    ) -> bool:
        if not self._can_recheck(path):
            return False
        torrent_hash, terminal_result = self._recheck_hash(meta, path)
        if terminal_result is not None:
            return terminal_result
        if torrent_hash is None:
            return False
        return await self._execute_selected_recheck(
            meta, torrent_hash, check_interval
        )
