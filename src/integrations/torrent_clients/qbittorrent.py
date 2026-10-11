# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import collections
import contextlib
import os
import platform
import re
import ssl
import subprocess
import time
import traceback
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, TypedDict, cast

import httpx
import qbittorrentapi
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.torrent_clients.path_utils import (
    coerce_str_list,
    is_path_under,
    map_save_path,
    tracker_directory,
)

# These have to be global variables to be shared across all instances since a new instance is made every time
qbittorrent_cached_clients: dict[
    tuple[str, int, str], qbittorrentapi.Client
] = {}  # Cache for qbittorrent clients that have been successfully logged into
qbittorrent_locks: collections.defaultdict[
    tuple[str, int, str], asyncio.Lock
] = collections.defaultdict(
    asyncio.Lock
)  # Locks for qbittorrent clients to prevent concurrent logins


class _CandidateEntry(TypedDict):
    path: str
    name: str
    size: int | None
    used: bool


class _TorrentFileEntry(TypedDict):
    relative_path: str
    length: int | None


class _PreparedBaseCandidate(TypedDict):
    hash: str
    torrent_path: str
    exported_path: str


class _PieceMatch(TypedDict):
    hash: str
    torrent_path: str
    piece_size: int


class _SubtitleFallback(TypedDict):
    hash: str
    torrent_path: str


@dataclass
class _BaseSelectionState:
    use_piece_preference: bool
    piece_size_best_match: _PieceMatch | None = None
    subtitle_fallback: _SubtitleFallback | None = None
    found_valid_torrent: bool = False


@dataclass(frozen=True)
class _CrossSeedContext:
    torrent_name: str
    multi_file: bool
    torrent_files: list[_TorrentFileEntry]
    destination_root: Path
    candidates: list[_CandidateEntry]


@dataclass
class _ReuseSelectionState:
    prefer_max_16: bool
    processed_hashes: set[str] = field(default_factory=set)
    video_only_fallback: str | None = None
    first_valid_torrent: str | None = None
    preferred_torrent: tuple[str, int] | None = None


@dataclass(frozen=True)
class _QbitAddPlan:
    save_path: str
    auto_management: bool
    category: Any
    content_layout: Any
    tag: Any
    paused_on_add: bool


def _claim_candidate(
    candidates: list[_CandidateEntry],
    predicate: Callable[[_CandidateEntry], bool],
    reason: str,
) -> tuple[str | None, str | None]:
    for entry in candidates:
        if entry["used"] or not predicate(entry):
            continue
        entry["used"] = True
        return entry["path"], reason
    return None, None


def _claim_name_size_candidate(
    candidates: list[_CandidateEntry], lower_name: str, length: int | None
) -> tuple[str | None, str | None]:
    if not lower_name or length is None:
        return None, None
    return _claim_candidate(
        candidates,
        lambda entry: entry["name"] == lower_name and entry["size"] == length,
        "name_size",
    )


def _claim_name_candidate(
    candidates: list[_CandidateEntry], lower_name: str
) -> tuple[str | None, str | None]:
    if not lower_name:
        return None, None
    return _claim_candidate(
        candidates,
        lambda entry: entry["name"] == lower_name,
        "name_only",
    )


def _claim_size_candidate(
    candidates: list[_CandidateEntry], length: int | None
) -> tuple[str | None, str | None]:
    if length is None:
        return None, None
    return _claim_candidate(
        candidates,
        lambda entry: entry["size"] == length,
        "size_only",
    )


def _pick_candidate(
    candidates: list[_CandidateEntry], filename: str | None, length: int | None
) -> tuple[str | None, str | None]:
    lower_name = (filename or "").lower()
    result = _claim_name_size_candidate(candidates, lower_name, length)
    if result[0] is not None:
        return result
    result = _claim_name_candidate(candidates, lower_name)
    if result[0] is not None:
        return result
    result = _claim_size_candidate(candidates, length)
    if result[0] is not None:
        return result
    return _claim_candidate(candidates, lambda _entry: True, "fallback")


class _RetryableProxyResponseError(Exception):
    """A qBittorrent proxy response which is safe to retry."""


class _ProxyResponseError(Exception):
    """A non-success qBittorrent proxy response which must not be retried."""


class QbittorrentClientMixin:
    config: dict[str, Any]

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, str]:
        raise NotImplementedError

    @staticmethod
    def _normalized_content_path(value: Any) -> str:
        text = str(value or "")
        return os.path.normcase(os.path.normpath(text)) if text else ""

    def _matches_qbit_content_path(self, torrent: Any, meta: Meta) -> bool:
        """Match a qBittorrent content path before falling back to its display name."""
        expected = self._normalized_content_path(meta.path)
        actual = self._normalized_content_path(
            getattr(torrent, "content_path", "")
        )
        if expected and actual and expected == actual:
            return True
        return self._torrent_name_matches(
            str(getattr(torrent, "name", "") or ""), meta
        )

    async def is_valid_torrent(
        self,
        meta: Meta,
        torrent_path: str,
        torrenthash: str,
        torrent_client: str,
        client: dict[str, Any],
    ) -> tuple[bool, str]:
        raise NotImplementedError

    @staticmethod
    def _torrent_includes_all_local_subtitles(
        torrent_path: str, meta: Meta
    ) -> bool:
        raise NotImplementedError

    @staticmethod
    def _torrent_has_no_subtitles(torrent_path: str) -> bool:
        raise NotImplementedError

    @staticmethod
    def _valid_hash_lookup(meta: Meta) -> str:
        infohash = meta.infohash
        if not isinstance(infohash, str) or not infohash or not meta.path:
            return ""
        return infohash

    @staticmethod
    def _ensure_hash_lookup_uuid(meta: Meta) -> str:
        if not meta.uuid:
            meta.uuid = Path(str(meta.path)).name
        directory = Path(meta.base_dir) / "tmp" / meta.uuid
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    async def _proxy_qbit_properties(
        self,
        session: httpx.AsyncClient,
        qbt_proxy_url: str,
        infohash: str,
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        response = await session.get(
            f"{qbt_proxy_url}/api/v2/torrents/properties",
            params={"hash": infohash},
            timeout=14.0,
        )
        logger.debug(
            f"[cyan]qBittorrent properties proxy response: status={response.status_code}, "
            f"elapsed={time.perf_counter() - started:.2f}s[/cyan]"
        )
        if response.status_code != 200:
            logger.info(
                f"[bold red]Failed to get torrent properties via proxy: {response.status_code}"
            )
            return None
        payload = response.json()
        logger.debug(
            f"[cyan]Retrieved torrent properties via proxy for hash: {infohash}"
        )
        return (
            cast(dict[str, Any], payload)
            if isinstance(payload, dict)
            else None
        )

    async def _direct_qbit_properties(
        self, client: qbittorrentapi.Client, infohash: str
    ) -> dict[str, Any] | None:
        started = time.perf_counter()
        try:
            payload = await self.retry_qbt_operation(
                lambda: asyncio.to_thread(
                    client.torrents_properties, torrent_hash=infohash
                ),
                f"Get torrent properties for hash {infohash}",
                initial_timeout=14.0,
            )
        except Exception as error:
            logger.info(f"[yellow]Failed to get properties: {error}")
            return None
        logger.debug(
            f"[cyan]qBittorrent properties direct response: "
            f"elapsed={time.perf_counter() - started:.2f}s[/cyan]"
        )
        logger.debug(
            f"[cyan]Retrieved torrent properties via client for hash: {infohash}"
        )
        return (
            cast(dict[str, Any], payload)
            if isinstance(payload, dict)
            else None
        )

    async def _qbit_hash_properties(
        self,
        infohash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        session: httpx.AsyncClient | None,
        client: qbittorrentapi.Client | None,
    ) -> dict[str, Any] | None:
        if proxy_url:
            if session is None:
                return None
            return await self._proxy_qbit_properties(
                session, qbt_proxy_url, infohash
            )
        if client is None:
            return None
        return await self._direct_qbit_properties(client, infohash)

    @staticmethod
    def _hash_lookup_comments(meta: Meta) -> list[dict[str, Any]]:
        raw = meta.torrent_comments
        if isinstance(raw, list):
            return cast(list[dict[str, Any]], raw)
        comments: list[dict[str, Any]] = []
        meta.torrent_comments = comments
        return comments

    @staticmethod
    def _hash_property_text(
        properties: dict[str, Any], key: str, fallback: str = ""
    ) -> str:
        value = properties.get(key)
        return str(value) if value not in (None, "") else fallback

    def _apply_hash_lookup_comment(
        self, meta: Meta, infohash: str, properties: dict[str, Any]
    ) -> None:
        comment = self._hash_property_text(properties, "comment")
        comments = self._hash_lookup_comments(meta)
        comments.append(
            {
                "hash": self._hash_property_text(
                    properties, "infohash_v1", infohash
                ),
                "name": self._hash_property_text(properties, "name"),
                "comment": comment,
            }
        )
        logger.debug(f"[cyan]Stored comment for torrent: {comment[:100]}...")
        meta.set_tracker_ids(self._extract_tracker_ids_from_comment(comment))
        if meta.debug:
            logger.info(
                f"[green]Stored {len(comments)} torrent comments for later use"
            )

    async def _save_hash_lookup_base(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        infohash: str,
        extracted_torrent_dir: str,
        proxy_url: str,
        session: httpx.AsyncClient | None,
        client: qbittorrentapi.Client | None,
    ) -> None:
        content = await self._export_torrent_content(
            infohash, proxy_url, session, client, ""
        )
        if content is None:
            return
        torrent_path = Path(extracted_torrent_dir) / f"{infohash}.torrent"
        await asyncio.to_thread(torrent_path.write_bytes, content)
        valid, resolved_path = await self.is_valid_torrent(
            meta,
            str(torrent_path),
            infohash,
            "qbit",
            client_config,
        )
        if not valid:
            logger.debug(f"[bold red]Validation failed for {torrent_path}")
            torrent_path.unlink(missing_ok=True)
            return
        await TorrentCreator.create_base_from_existing_torrent(
            str(resolved_path or torrent_path), meta.base_dir, meta.uuid
        )

    async def _maybe_export_hash_lookup_base(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        infohash: str,
        extracted_torrent_dir: str,
        proxy_url: str,
        session: httpx.AsyncClient | None,
        client: qbittorrentapi.Client | None,
        pathed: bool,
    ) -> None:
        if pathed or client_config.get("torrent_storage_dir"):
            return
        logger.debug(f"[cyan]Exporting .torrent file for hash: {infohash}")
        try:
            await self._save_hash_lookup_base(
                meta,
                client_config,
                infohash,
                extracted_torrent_dir,
                proxy_url,
                session,
                client,
            )
        except TimeoutError:
            logger.info(
                f"[bold red]Failed to export .torrent for {infohash} after retries"
            )

    @staticmethod
    def _hash_lookup_matches(
        properties: dict[str, Any], infohash: str
    ) -> bool:
        value = properties.get("infohash_v1")
        resolved = str(value) if value not in (None, "") else infohash
        return resolved == infohash

    @staticmethod
    def _log_hash_lookup_missing() -> None:
        logger.info(
            "[bold red]Matching site torrent with the specified infohash_v1 not found."
        )

    async def _run_hash_lookup(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        pathed: bool,
        infohash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> Meta:
        properties = await self._qbit_hash_properties(
            infohash,
            proxy_url,
            qbt_proxy_url,
            session,
            qbt_client,
        )
        if properties is None or not self._hash_lookup_matches(
            properties, infohash
        ):
            self._log_hash_lookup_missing()
            return meta
        extracted_dir = self._ensure_hash_lookup_uuid(meta)
        self._apply_hash_lookup_comment(meta, infohash, properties)
        await self._maybe_export_hash_lookup_base(
            meta,
            client_config,
            infohash,
            extracted_dir,
            proxy_url,
            session,
            qbt_client,
            pathed,
        )
        return meta

    async def _hash_lookup_handles(
        self, meta: Meta, client: dict[str, Any]
    ) -> (
        tuple[
            str,
            str,
            str,
            httpx.AsyncClient | None,
            qbittorrentapi.Client | None,
        ]
        | None
    ):
        infohash = self._valid_hash_lookup(meta)
        if not infohash:
            return None
        handles = await self._safe_qbit_search_handles(client)
        if handles is None:
            return None
        proxy_url, qbt_proxy_url, session, qbt_client = handles
        if not self._qbit_search_handles_usable(proxy_url, qbt_client):
            await self._close_qbit_search_session(session)
            return None
        return infohash, proxy_url, qbt_proxy_url, session, qbt_client

    async def get_ptp_from_hash_qbit(
        self, meta: Meta, client: dict[str, Any], pathed: bool = False
    ) -> Meta:
        lookup_started = time.perf_counter()
        handles = await self._hash_lookup_handles(meta, client)
        if handles is None:
            return meta
        infohash, proxy_url, qbt_proxy_url, session, qbt_client = handles
        logger.debug(f"[cyan]Searching for infohash: {infohash}")
        logger.debug(
            f"[cyan]Fetching qBittorrent properties "
            f"({'proxy' if proxy_url else 'direct'}, pathed={pathed})[/cyan]"
        )
        try:
            return await self._run_hash_lookup(
                meta,
                client,
                pathed,
                infohash,
                proxy_url,
                qbt_proxy_url,
                session,
                qbt_client,
            )
        except TimeoutError:
            logger.info(
                "[bold red]Getting torrents list timed out after retries"
            )
            return meta
        except Exception as error:
            logger.info(f"[bold red]Error getting torrents list: {error}")
            return meta
        finally:
            await self._close_qbit_search_session(session)
            logger.debug(
                f"[cyan]Completed qBittorrent hash lookup in "
                f"{time.perf_counter() - lookup_started:.2f}s[/cyan]"
            )

    @staticmethod
    def _verify_webui_certificate(client_config: dict[str, Any]) -> bool:
        value = client_config.get("VERIFY_WEBUI_CERTIFICATE", True)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"0", "false", "no", "off"}:
                return False
            if normalized in {"1", "true", "yes", "on"}:
                return True
        return bool(value)

    def create_ssl_context_for_client(
        self, client_config: dict[str, Any]
    ) -> ssl.SSLContext:
        """Create SSL context for qBittorrent client based on VERIFY_WEBUI_CERTIFICATE setting."""
        ssl_context = ssl.create_default_context()
        if not self._verify_webui_certificate(client_config):
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context

    async def retry_qbt_operation(
        self,
        operation_func: Callable[[], Awaitable[Any]],
        operation_name: str,
        max_retries: int = 2,
        initial_timeout: float = 10.0,
        retryable_errors: tuple[type[BaseException], ...] = (TimeoutError,),
    ) -> Any:
        for attempt in range(max_retries + 1):
            timeout = initial_timeout * (
                2**attempt
            )  # Exponential backoff: 10s, 20s, 40s
            try:
                result = await asyncio.wait_for(
                    operation_func(), timeout=timeout
                )
                if attempt > 0:
                    logger.info(
                        f"[green]{operation_name} succeeded on attempt {attempt + 1}"
                    )
                return result
            except retryable_errors as error:
                if attempt < max_retries:
                    logger.info(
                        f"[yellow]{operation_name} failed ({error}) after {timeout}s (attempt {attempt + 1}/{max_retries + 1}), retrying..."
                    )
                    await asyncio.sleep(1)  # Brief pause before retry
                else:
                    logger.info(
                        f"[bold red]{operation_name} failed after {max_retries + 1} attempts (final attempt timeout: {timeout}s)"
                    )
                    raise
        return None

    @staticmethod
    def _raise_for_proxy_response(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        if response.status_code in (502, 503, 504):
            raise _RetryableProxyResponseError(
                f"proxy returned HTTP {response.status_code}"
            )
        raise _ProxyResponseError(
            f"proxy returned HTTP {response.status_code}"
        )

    async def _post_proxy_command(
        self,
        qbt_session: httpx.AsyncClient,
        url: str,
        data: dict[str, str],
        operation_name: str,
        accepted_statuses: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        async def post_command() -> httpx.Response:
            response = await qbt_session.post(url, data=data)
            if response.status_code not in accepted_statuses:
                self._raise_for_proxy_response(response)
            return response

        return cast(
            httpx.Response,
            await self.retry_qbt_operation(
                post_command,
                operation_name,
                max_retries=2,
                retryable_errors=(
                    TimeoutError,
                    httpx.HTTPError,
                    _RetryableProxyResponseError,
                ),
            ),
        )

    async def _add_torrent_via_proxy(
        self,
        qbt_session: httpx.AsyncClient,
        qbt_proxy_url: str,
        infohash: str,
        data: dict[str, str],
        files: dict[str, Any],
    ) -> None:
        add_attempt = 0

        async def add_via_proxy() -> None:
            nonlocal add_attempt
            # A timeout or 5xx response may mean qBittorrent accepted the first
            # request while the proxy failed to return its response. Check before
            # submitting the same infohash again.
            if add_attempt:
                info_response = await qbt_session.get(
                    f"{qbt_proxy_url}/api/v2/torrents/info",
                    params={"hashes": infohash},
                )
                self._raise_for_proxy_response(info_response)
                if info_response.json():
                    logger.info(
                        "[green]Torrent was added to qBittorrent despite the previous proxy failure."
                    )
                    return

            add_attempt += 1
            response = await qbt_session.post(
                f"{qbt_proxy_url}/api/v2/torrents/add", data=data, files=files
            )
            self._raise_for_proxy_response(response)

        await self.retry_qbt_operation(
            add_via_proxy,
            "Add torrent to qBittorrent via proxy",
            initial_timeout=14.0,
            retryable_errors=(
                TimeoutError,
                httpx.HTTPError,
                _RetryableProxyResponseError,
            ),
        )

    @staticmethod
    async def _direct_torrent_present(
        qbt_client: qbittorrentapi.Client, infohash: str
    ) -> bool:
        torrents = await asyncio.to_thread(
            qbt_client.torrents_info, torrent_hashes=infohash
        )
        return bool(torrents)

    async def _recover_direct_add(
        self, qbt_client: qbittorrentapi.Client, infohash: str
    ) -> bool:
        with contextlib.suppress(Exception):
            if await self._direct_torrent_present(qbt_client, infohash):
                logger.info(
                    "[green]Torrent was added to qBittorrent despite the previous connection issue."
                )
                return True
        return False

    async def _verify_direct_add_result(
        self,
        qbt_client: qbittorrentapi.Client,
        infohash: str,
        result: Any,
    ) -> None:
        if not isinstance(result, str) or result.strip() != "Fails.":
            return
        if await self._direct_torrent_present(qbt_client, infohash):
            return
        raise qbittorrentapi.APIError(
            "qBittorrent returned 'Fails.' when adding torrent"
        )

    async def _add_torrent_direct(
        self,
        qbt_client: qbittorrentapi.Client,
        infohash: str,
        add_kwargs: dict[str, Any],
    ) -> None:
        add_attempt = 0

        async def add_direct() -> None:
            nonlocal add_attempt
            if add_attempt and await self._recover_direct_add(
                qbt_client, infohash
            ):
                return
            add_attempt += 1
            try:
                result = await asyncio.to_thread(
                    qbt_client.torrents_add, **add_kwargs
                )
                await self._verify_direct_add_result(
                    qbt_client, infohash, result
                )
            except qbittorrentapi.Conflict409Error:
                logger.info("[yellow]Torrent already exists in qBittorrent.")

        await self.retry_qbt_operation(
            add_direct,
            "Add torrent to qBittorrent",
            initial_timeout=14.0,
            retryable_errors=(
                TimeoutError,
                httpx.HTTPError,
                qbittorrentapi.APIConnectionError,
            ),
        )

    @staticmethod
    def _qbit_client_key(client: dict[str, Any]) -> tuple[str, int, str]:
        identity = (
            f"APIKEY:{client['qbit_api_key']}"
            if client.get("qbit_api_key")
            else str(client.get("qbit_user", ""))
        )
        return str(client["qbit_url"]), int(client["qbit_port"]), identity

    @staticmethod
    def _new_qbit_client(client: dict[str, Any]) -> qbittorrentapi.Client:
        common: dict[str, Any] = {
            "host": client["qbit_url"],
            "port": client["qbit_port"],
            "VERIFY_WEBUI_CERTIFICATE": QbittorrentClientMixin._verify_webui_certificate(
                client
            ),
        }
        if client.get("qbit_api_key"):
            return qbittorrentapi.Client(
                **common, api_key=client["qbit_api_key"]
            )
        return qbittorrentapi.Client(
            **common,
            username=client.get("qbit_user"),
            password=client.get("qbit_pass"),
        )

    async def _verify_qbit_api_key_client(
        self, qbt_client: qbittorrentapi.Client
    ) -> bool:
        try:
            await self.retry_qbt_operation(
                lambda: asyncio.to_thread(qbt_client.app_version),
                "qBittorrent API Key verification",
            )
            return True
        except TimeoutError:
            logger.info(
                "[bold red]Connection to qBittorrent timed out after retries"
            )
        except qbittorrentapi.APIConnectionError:
            logger.info(
                "[bold red]Failed to connect to qBittorrent - check host/port/API Key"
            )
        except Exception as error:
            logger.info(
                f"[bold red]Failed to verify qBittorrent API Key: {error}"
            )
        return False

    async def _login_qbit_password_client(
        self, qbt_client: qbittorrentapi.Client
    ) -> bool:
        try:
            await self.retry_qbt_operation(
                lambda: asyncio.to_thread(qbt_client.auth_log_in),
                "qBittorrent login",
            )
            return True
        except TimeoutError:
            logger.info(
                "[bold red]Connection to qBittorrent timed out after retries"
            )
        except qbittorrentapi.LoginFailed:
            logger.info(
                "[bold red]Failed to login to qBittorrent - incorrect credentials"
            )
        except qbittorrentapi.APIConnectionError:
            logger.info(
                "[bold red]Failed to connect to qBittorrent - check host/port"
            )
        return False

    async def _authenticate_qbit_client(
        self, qbt_client: qbittorrentapi.Client, client: dict[str, Any]
    ) -> bool:
        if client.get("qbit_api_key"):
            return await self._verify_qbit_api_key_client(qbt_client)
        return await self._login_qbit_password_client(qbt_client)

    async def init_qbittorrent_client(
        self, client: dict[str, Any]
    ) -> qbittorrentapi.Client | None:
        client_key = self._qbit_client_key(client)
        async with qbittorrent_locks[client_key]:
            cached = qbittorrent_cached_clients.get(client_key)
            if cached is not None:
                return cached
            qbt_client = self._new_qbit_client(client)
            if not await self._authenticate_qbit_client(qbt_client, client):
                return None
            qbittorrent_cached_clients[client_key] = qbt_client
            return qbt_client

    @staticmethod
    def _reuse_extracted_torrent_dir(meta: Meta) -> str | None:
        if (
            not str(meta.base_dir or "").strip()
            or not str(meta.uuid or "").strip()
        ):
            logger.info(
                "[bold red]Invalid extracted torrent directory path. Check `meta.base_dir` and `meta.uuid`."
            )
            return None
        directory = Path(meta.base_dir) / "tmp" / meta.uuid
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    async def _initialize_direct_reuse_client(
        self,
        client_config: dict[str, Any],
        qbt_client: qbittorrentapi.Client | None,
        proxy: str,
    ) -> qbittorrentapi.Client | None:
        if qbt_client is not None or proxy:
            return qbt_client
        return await self.init_qbittorrent_client(client_config)

    def _initialize_proxy_reuse_session(
        self,
        client_config: dict[str, Any],
        qbt_session: httpx.AsyncClient | None,
        proxy: str,
    ) -> tuple[httpx.AsyncClient | None, bool]:
        if not proxy or qbt_session is not None:
            return qbt_session, False
        ssl_context = self.create_ssl_context_for_client(client_config)
        return httpx.AsyncClient(timeout=10.0, verify=ssl_context), True

    async def _initialize_reuse_search_handles(
        self,
        client_config: dict[str, Any],
        qbt_client: qbittorrentapi.Client | None,
        qbt_session: httpx.AsyncClient | None,
        proxy: str,
    ) -> (
        tuple[
            qbittorrentapi.Client | None,
            httpx.AsyncClient | None,
            bool,
        ]
        | None
    ):
        qbt_client = await self._initialize_direct_reuse_client(
            client_config, qbt_client, proxy
        )
        if not proxy and qbt_client is None:
            return None
        qbt_session, created_session = self._initialize_proxy_reuse_session(
            client_config, qbt_session, proxy
        )
        return qbt_client, qbt_session, created_session

    async def _reuse_search_handles(
        self,
        client_config: dict[str, Any],
        qbt_client: qbittorrentapi.Client | None,
        qbt_session: httpx.AsyncClient | None,
        proxy_url: str | None,
    ) -> (
        tuple[
            qbittorrentapi.Client | None,
            httpx.AsyncClient | None,
            str,
            str,
            bool,
        ]
        | None
    ):
        proxy = str(proxy_url or "").strip()
        try:
            initialized = await self._initialize_reuse_search_handles(
                client_config, qbt_client, qbt_session, proxy
            )
        except qbittorrentapi.LoginFailed:
            logger.info("[bold red]INCORRECT QBIT LOGIN CREDENTIALS")
            return None
        except qbittorrentapi.APIConnectionError:
            logger.info("[bold red]APIConnectionError: INCORRECT HOST/PORT")
            return None
        if initialized is None:
            return None
        qbt_client, qbt_session, created_session = initialized
        return (
            qbt_client,
            qbt_session,
            proxy,
            proxy.rstrip("/"),
            created_session,
        )

    async def _reuse_search_torrents(
        self,
        meta: Meta,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> list[Any]:
        search_term = meta.uuid.replace("[", ".").replace("]", ".")
        return await self._fetch_torrents(
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            search_term,
        )

    @staticmethod
    def _reuse_torrent_identity(torrent: Any) -> tuple[str, str] | None:
        try:
            name = str(torrent.name)
            torrent_hash = str(torrent.hash)
        except AttributeError, TypeError, ValueError, RuntimeError:
            return None
        return name, torrent_hash

    @staticmethod
    def _reuse_tracker_url_value(value: Any) -> str:
        if isinstance(value, dict):
            raw = cast(dict[str, Any], value).get("url")
            return str(raw) if raw else ""
        return str(value) if value else ""

    @classmethod
    def _secondary_reuse_tracker_urls(cls, torrent: Any) -> list[str]:
        raw_trackers = getattr(torrent, "trackers", []) or []
        if not isinstance(raw_trackers, list):
            return []
        return [
            url
            for raw in cast(list[Any], raw_trackers)
            if (url := cls._reuse_tracker_url_value(raw))
        ]

    @classmethod
    def _reuse_tracker_urls(cls, torrent: Any) -> list[str]:
        urls = cls._secondary_reuse_tracker_urls(torrent)
        primary = cls._reuse_tracker_url_value(getattr(torrent, "tracker", ""))
        return [primary, *urls] if primary else urls

    async def _proxy_reuse_comment(
        self,
        torrent: Any,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> str:
        await self._proxy_torrent_comment(torrent, qbt_proxy_url, qbt_session)
        return str(getattr(torrent, "comment", "") or "")

    async def _direct_reuse_comment(
        self,
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str:
        if qbt_client is None:
            return ""
        properties = await self._direct_qbit_properties(
            qbt_client, torrent_hash
        )
        return str(properties.get("comment", "") or "") if properties else ""

    async def _fetch_reuse_comment(
        self,
        torrent: Any,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str:
        if proxy_url:
            return await self._proxy_reuse_comment(
                torrent, qbt_proxy_url, qbt_session
            )
        return await self._direct_reuse_comment(torrent_hash, qbt_client)

    async def _reuse_torrent_comment(
        self,
        torrent: Any,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str:
        comment = str(getattr(torrent, "comment", "") or "")
        if comment:
            return comment
        try:
            return await self._fetch_reuse_comment(
                torrent,
                torrent_hash,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Could not inspect torrent comment for {torrent_hash}: {error}[/yellow]"
            )
            return ""

    async def _apply_reuse_tracker_metadata(
        self,
        meta: Meta,
        torrent: Any,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        tracker_urls = self._reuse_tracker_urls(torrent)
        if tracker_urls:
            await match_tracker_url(tracker_urls, meta)
        comment = await self._reuse_torrent_comment(
            torrent,
            torrent_hash,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        tracker_ids = {
            key: value
            for key, value in self._extract_tracker_ids_from_comment(
                comment
            ).items()
            if not meta.get_tracker_id(key)
        }
        if not tracker_ids:
            return
        meta.set_tracker_ids(tracker_ids)
        logger.debug(
            f"[bold cyan]Found tracker IDs in matching torrent comment: {', '.join(sorted(tracker_ids))}"
        )

    async def _matching_reuse_torrent(
        self,
        torrent: Any,
        meta: Meta,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> dict[str, Any] | None:
        identity = self._reuse_torrent_identity(torrent)
        if identity is None:
            return None
        name, torrent_hash = identity
        if not self._matches_qbit_content_path(torrent, meta):
            return None
        logger.debug(f"[cyan]Matched Torrent: {torrent_hash}")
        logger.debug(f"Name: {name}")
        logger.debug(f"Save Path: {getattr(torrent, 'save_path', '')}")
        logger.debug(f"Content Path: {getattr(torrent, 'content_path', name)}")
        await self._apply_reuse_tracker_metadata(
            meta,
            torrent,
            torrent_hash,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        return {"hash": torrent_hash, "name": name}

    async def _matching_reuse_torrents(
        self,
        torrents: list[Any],
        meta: Meta,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        checked = 0
        for torrent in torrents:
            if self._reuse_torrent_identity(torrent) is not None:
                checked += 1
            match = await self._matching_reuse_torrent(
                torrent,
                meta,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
            if match is not None:
                matches.append(match)
        logger.debug(
            f"[cyan]DEBUG: Checked {checked} total torrents in qBittorrent[/cyan]"
        )
        return matches

    async def _validated_reuse_torrent_path(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        torrent_hash: str,
        torrent_file_path: str,
        extracted_torrent_dir: str,
    ) -> str | None:
        try:
            started = time.perf_counter()
            valid, resolved = await self.is_valid_torrent(
                meta,
                torrent_file_path,
                torrent_hash,
                "qbit",
                client_config,
            )
            logger.debug(
                f"[cyan]Validated exported torrent: hash={torrent_hash}, valid={valid}, "
                f"elapsed={time.perf_counter() - started:.2f}s[/cyan]"
            )
        except Exception as error:
            logger.info(
                f"[bold red]Error validating torrent {torrent_hash}: {error}"
            )
            valid = False
            resolved = None
        if valid:
            return str(resolved or torrent_file_path)
        logger.debug(f"[bold red]{torrent_hash} failed validation")
        self._cleanup_exported_candidate(
            torrent_file_path, extracted_torrent_dir
        )
        return None

    def _reuse_subtitle_candidate(
        self,
        meta: Meta,
        state: _ReuseSelectionState,
        torrent_hash: str,
        torrent_path: str,
    ) -> bool:
        if not meta.subtitle_files:
            return False
        if self._torrent_includes_all_local_subtitles(torrent_path, meta):
            return False
        if self._torrent_has_no_subtitles(torrent_path):
            state.video_only_fallback = torrent_hash
            meta.base_reuse_torrent_path = torrent_path
            logger.debug(
                f"[yellow]Keeping video-only torrent as fallback: {torrent_hash}"
            )
        else:
            logger.debug(
                f"[yellow]Skipping partial-subtitle torrent as fallback: {torrent_hash}"
            )
        return True

    @staticmethod
    def _reuse_hash_claimed(
        state: _ReuseSelectionState, torrent_hash: str
    ) -> bool:
        if torrent_hash in state.processed_hashes:
            return False
        state.processed_hashes.add(torrent_hash)
        return True

    @staticmethod
    def _reuse_piece_size(torrent_hash: str, torrent_path: str) -> int | None:
        try:
            return int(Torrent.read(torrent_path).piece_size)
        except Exception as error:
            logger.debug(
                f"[yellow]Unable to inspect piece size for {torrent_hash}: {error}"
            )
            return None

    @staticmethod
    def _better_reuse_piece(
        state: _ReuseSelectionState, piece_size: int
    ) -> bool:
        if piece_size > 16 * 1024 * 1024:
            return False
        current = state.preferred_torrent
        return current is None or piece_size < current[1]

    @classmethod
    def _record_reuse_piece_candidate(
        cls,
        state: _ReuseSelectionState,
        torrent_hash: str,
        torrent_path: str,
    ) -> None:
        if state.first_valid_torrent is None:
            state.first_valid_torrent = torrent_hash
        piece_size = cls._reuse_piece_size(torrent_hash, torrent_path)
        if piece_size is None or not cls._better_reuse_piece(
            state, piece_size
        ):
            return
        state.preferred_torrent = (torrent_hash, piece_size)
        logger.debug(
            f"[green]Keeping preferred qBittorrent candidate: {torrent_hash} ({piece_size} bytes)"
        )

    async def _prepared_reuse_candidate_path(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        torrent_hash: str,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str | None:
        torrent_file_path = await self._export_torrent_file(
            torrent_hash,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            torrent_storage_dir,
            extracted_torrent_dir,
        )
        if not torrent_file_path:
            return None
        return await self._validated_reuse_torrent_path(
            meta,
            client_config,
            torrent_hash,
            torrent_file_path,
            extracted_torrent_dir,
        )

    def _reuse_candidate_selection(
        self,
        meta: Meta,
        state: _ReuseSelectionState,
        torrent_hash: str,
        validated_path: str,
    ) -> str | None:
        if self._reuse_subtitle_candidate(
            meta, state, torrent_hash, validated_path
        ):
            return None
        if not state.prefer_max_16:
            logger.debug(
                f"[green]Returning first valid torrent: {torrent_hash}"
            )
            return torrent_hash
        self._record_reuse_piece_candidate(state, torrent_hash, validated_path)
        return None

    async def _evaluate_reuse_candidate(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        matching_torrent: dict[str, Any],
        state: _ReuseSelectionState,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str | None:
        torrent_hash = str(matching_torrent["hash"])
        if not self._reuse_hash_claimed(state, torrent_hash):
            return None
        validated_path = await self._prepared_reuse_candidate_path(
            meta,
            client_config,
            torrent_hash,
            torrent_storage_dir,
            extracted_torrent_dir,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        if validated_path is None:
            return None
        return self._reuse_candidate_selection(
            meta, state, torrent_hash, validated_path
        )

    @staticmethod
    def _final_reuse_selection(state: _ReuseSelectionState) -> str | None:
        if state.preferred_torrent is not None:
            result = state.preferred_torrent[0]
            logger.info(
                f"[green]Using preferred qBittorrent torrent with pieces up to 16 MiB: {result}"
            )
            return result
        if state.first_valid_torrent:
            logger.info(
                f"[yellow]No valid torrent met the 16 MiB preference; using first valid torrent: {state.first_valid_torrent}"
            )
            return state.first_valid_torrent
        if state.video_only_fallback:
            logger.info(
                f"[yellow]No matching torrent with all local subtitles found; using video-only fallback: {state.video_only_fallback}"
            )
            return state.video_only_fallback
        logger.debug("[yellow]No reusable torrents found in qBittorrent.")
        return None

    async def _search_qbit_reuse_flow(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        extracted_torrent_dir: str,
        qbt_client: qbittorrentapi.Client | None,
        qbt_session: httpx.AsyncClient | None,
        proxy_url: str,
        qbt_proxy_url: str,
    ) -> str | None:
        torrents = await self._reuse_search_torrents(
            meta,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        matching = await self._matching_reuse_torrents(
            torrents,
            meta,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        if not matching:
            logger.debug("[yellow]No matching torrents found in qBittorrent.")
            return None
        logger.debug(f"[green]Total Matching Torrents: {len(matching)}")
        state = _ReuseSelectionState(
            prefer_max_16=self._piece_preference_enabled()
        )
        storage = client_config.get("torrent_storage_dir")
        torrent_storage_dir = str(storage) if storage else None
        for matching_torrent in matching:
            selected = await self._evaluate_reuse_candidate(
                meta,
                client_config,
                matching_torrent,
                state,
                torrent_storage_dir,
                extracted_torrent_dir,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
            if selected is not None:
                return selected
        return self._final_reuse_selection(state)

    async def search_qbit_for_torrent(
        self,
        meta: Meta,
        client: dict[str, Any],
        qbt_client: qbittorrentapi.Client | None = None,
        qbt_session: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
    ) -> str | None:
        logger.debug("[green]Searching qBittorrent for an existing .torrent")
        extracted_torrent_dir = self._reuse_extracted_torrent_dir(meta)
        if extracted_torrent_dir is None:
            return None
        handles = await self._reuse_search_handles(
            client, qbt_client, qbt_session, proxy_url
        )
        if handles is None:
            return None
        qbt_client, qbt_session, proxy, qbt_proxy_url, created_session = (
            handles
        )
        try:
            return await self._search_qbit_reuse_flow(
                meta,
                client,
                extracted_torrent_dir,
                qbt_client,
                qbt_session,
                proxy,
                qbt_proxy_url,
            )
        finally:
            if created_session and qbt_session is not None:
                await qbt_session.aclose()

    @staticmethod
    def _normalized_qbit_add_path(
        path: str, meta: Meta, filelist: list[str]
    ) -> str:
        if meta.keep_folder:
            return str(Path(path).parent)
        is_directory = Path(path).is_dir()
        if len(filelist) != 1 or not is_directory:
            return str(Path(path).parent)
        return path

    @staticmethod
    def _single_qbit_source(meta: Meta) -> str:
        if meta.keep_folder or len(meta.filelist) != 1:
            return ""
        candidate = str(meta.filelist[0])
        return candidate if Path(candidate).is_file() else ""

    @classmethod
    def _qbit_source_path(cls, meta: Meta) -> str:
        source = cls._single_qbit_source(meta) or str(meta.path or "")
        if source:
            return source
        error_msg = "[red]No source path found in meta."
        logger.info(f"[bold red]{error_msg}")
        raise ValueError(error_msg)

    @staticmethod
    def _qbit_linking_flags(client: dict[str, Any]) -> tuple[bool, bool]:
        method = client.get("linking")
        logger.debug(f"Linking method: {method}")
        return method == "symlink", method == "hardlink"

    @staticmethod
    def _proc_mount_points(text: str) -> list[str]:
        mounted: list[str] = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mounted.append(parts[1])
        return mounted

    @staticmethod
    def _command_mount_points(text: str) -> list[str]:
        mounted: list[str] = []
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                mounted.append(parts[2])
        return mounted

    @classmethod
    async def _linux_mount_points(cls) -> list[str]:
        try:
            if Path("/proc/mounts").exists():
                text = await asyncio.to_thread(Path("/proc/mounts").read_text)
                mounted = cls._proc_mount_points(text)
            else:
                output = str(
                    await asyncio.to_thread(
                        subprocess.check_output, ["mount"], text=True
                    )
                )
                mounted = cls._command_mount_points(output)
        except Exception as error:
            logger.debug(f"[yellow]Error getting mount points: {error!s}")
            mounted = []
        mounted.sort(key=len, reverse=True)
        return mounted

    @staticmethod
    def _linked_root_fallback(src: str, linked_folders: list[str]) -> str:
        parts = src.strip("/").split("/")
        if not parts:
            return "/"
        root = f"/{parts[0]}"
        if any(root in folder or folder in root for folder in linked_folders):
            return root
        return "/"

    async def _qbit_source_drive(
        self, src: str, linked_folders: list[str]
    ) -> str:
        if platform.system() == "Windows":
            return os.path.splitdrive(src)[0]
        for mount_point in await self._linux_mount_points():
            if src.startswith(mount_point):
                logger.debug(
                    f"[cyan]Found mount point: {mount_point} for path: {src}"
                )
                return mount_point
        return self._linked_root_fallback(src, linked_folders)

    @staticmethod
    def _qbit_windows_link_target(
        src_drive: str, linked_folders: list[str]
    ) -> str | None:
        return next(
            (
                folder
                for folder in linked_folders
                if os.path.splitdrive(folder)[0] == src_drive
            ),
            None,
        )

    @staticmethod
    def _sibling_link_target(src_drive: str, folder: str) -> str | Path | None:
        folder_parts = folder.split("/")
        src_parts = src_drive.split("/")
        if len(folder_parts) < 2 or len(src_parts) < 2:
            return None
        if folder_parts[1] != src_parts[1]:
            return None
        candidate = Path(src_drive) / folder_parts[-1]
        return candidate if candidate.exists() else None

    @classmethod
    def _qbit_unix_link_target(
        cls, src: str, src_drive: str, linked_folders: list[str]
    ) -> str | Path | None:
        for folder in linked_folders:
            if folder.startswith(src_drive) or src.startswith(folder):
                return folder
            sibling = cls._sibling_link_target(src_drive, folder)
            if sibling is not None:
                logger.debug(
                    f"[cyan]Found sibling mount point linked folder: {sibling}"
                )
                return sibling
        return None

    def _platform_link_target(
        self, src: str, src_drive: str, linked_folders: list[str]
    ) -> str | Path | None:
        if platform.system() == "Windows":
            return self._qbit_windows_link_target(src_drive, linked_folders)
        return self._qbit_unix_link_target(src, src_drive, linked_folders)

    @staticmethod
    def _symlink_fallback_target(
        target: str | Path | None,
        linked_folders: list[str],
        use_symlink: bool,
    ) -> str | Path | None:
        if target is not None:
            return target
        if use_symlink and linked_folders:
            return linked_folders[0]
        return None

    async def _qbit_link_target(
        self,
        src: str,
        linked_folders: list[str],
        use_symlink: bool,
    ) -> str | Path | None:
        src_drive = await self._qbit_source_drive(src, linked_folders)
        target = self._platform_link_target(src, src_drive, linked_folders)
        logger.debug(f"Source drive: {src_drive}")
        logger.debug(f"Link target: {target}")
        return self._symlink_fallback_target(
            target, linked_folders, use_symlink
        )

    def _qbit_tracker_directory(
        self, link_target: str | Path, tracker: str
    ) -> Path:
        trackers = self.config.get("TRACKERS", {})
        tracker_map = (
            cast(dict[str, Any], trackers)
            if isinstance(trackers, dict)
            else {}
        )
        raw = tracker_map.get(tracker.upper(), {})
        tracker_cfg = (
            cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        )
        link_dir_name = str(tracker_cfg.get("link_dir_name", "")).strip()
        return tracker_directory(link_target, link_dir_name, tracker)

    @staticmethod
    def _torrent_requires_file_mapping(torrent: Torrent, src: str) -> bool:
        torrent_multi = bool(_cross_seed_info(torrent).get("files"))
        return Path(src).is_dir() != torrent_multi

    async def _attempt_qbit_link(
        self,
        meta: Meta,
        torrent: Torrent,
        tracker_dir: str | Path,
        src: str,
        use_hardlink: bool,
        cross: bool,
        requires_file_mapping: bool,
    ) -> bool:
        if cross or requires_file_mapping:
            return await create_cross_seed_links(
                meta=meta,
                torrent=torrent,
                tracker_dir=tracker_dir,
                use_hardlink=use_hardlink,
            )
        destination = Path(tracker_dir) / Path(src.rstrip(os.sep)).name
        return await async_link_directory(
            src=src, dst=destination, use_hardlink=use_hardlink
        )

    @staticmethod
    def _unlinked_qbit_result(
        use_symlink: bool, use_hardlink: bool, cross: bool
    ) -> tuple[str | Path | None, bool, bool, bool] | None:
        if use_symlink or use_hardlink:
            return None
        if cross:
            logger.info(
                "[yellow]Cross seed requested, but no linking method is configured. Proceeding with original path naming."
            )
        return None, use_symlink, use_hardlink, True

    async def _resolved_qbit_tracker_dir(
        self,
        src: str,
        client: dict[str, Any],
        tracker: str,
        use_symlink: bool,
    ) -> Path:
        linked_folders = coerce_str_list(client.get("linked_folder", []))
        logger.debug(f"Linked folders: {linked_folders}")
        link_target = await self._qbit_link_target(
            src, linked_folders, use_symlink
        )
        if link_target is None:
            src_drive = await self._qbit_source_drive(src, linked_folders)
            error_msg = (
                f"No suitable linked folder found for drive {src_drive}"
            )
            logger.info(f"[bold red]{error_msg}")
            raise ValueError(error_msg)
        tracker_dir = self._qbit_tracker_directory(link_target, tracker)
        await asyncio.to_thread(os.makedirs, tracker_dir, exist_ok=True)
        return tracker_dir

    async def _qbit_link_with_isolated_retry(
        self,
        meta: Meta,
        torrent: Torrent,
        tracker_dir: Path,
        src: str,
        use_hardlink: bool,
        cross: bool,
    ) -> tuple[Path, bool]:
        requires_mapping = self._torrent_requires_file_mapping(torrent, src)
        linked = await self._attempt_qbit_link(
            meta,
            torrent,
            tracker_dir,
            src,
            use_hardlink,
            cross,
            requires_mapping,
        )
        if linked:
            return tracker_dir, True
        isolated = tracker_dir / torrent.infohash.lower()
        await asyncio.to_thread(os.makedirs, isolated, exist_ok=True)
        logger.info(
            f"[yellow]Link destination is occupied by different content; retrying in isolated directory: {isolated}"
        )
        linked = await self._attempt_qbit_link(
            meta,
            torrent,
            isolated,
            src,
            use_hardlink,
            cross,
            requires_mapping,
        )
        return (isolated if linked else tracker_dir), linked

    @staticmethod
    def _qbit_link_failure_result(
        tracker_dir: Path,
        client: dict[str, Any],
        src: str,
        use_symlink: bool,
        use_hardlink: bool,
    ) -> tuple[str | Path | None, bool, bool, bool]:
        if client.get("allow_fallback", True):
            logger.info(f"[yellow]Using original path without linking: {src}")
            return tracker_dir, False, False, True
        logger.info(
            "[bold red]Linking failed and fallback is disabled; aborting qBittorrent add"
        )
        return tracker_dir, use_symlink, use_hardlink, False

    async def _prepare_qbit_links(
        self,
        meta: Meta,
        torrent: Torrent,
        client: dict[str, Any],
        tracker: str,
        src: str,
        use_symlink: bool,
        use_hardlink: bool,
        cross: bool,
    ) -> tuple[str | Path | None, bool, bool, bool]:
        unlinked = self._unlinked_qbit_result(use_symlink, use_hardlink, cross)
        if unlinked is not None:
            return unlinked
        tracker_dir = await self._resolved_qbit_tracker_dir(
            src, client, tracker, use_symlink
        )
        tracker_dir, linked = await self._qbit_link_with_isolated_retry(
            meta, torrent, tracker_dir, src, use_hardlink, cross
        )
        if linked:
            return tracker_dir, use_symlink, use_hardlink, True
        return self._qbit_link_failure_result(
            tracker_dir, client, src, use_symlink, use_hardlink
        )

    async def _qbit_add_handles(
        self, client: dict[str, Any]
    ) -> (
        tuple[str, str, httpx.AsyncClient | None, qbittorrentapi.Client | None]
        | None
    ):
        proxy_url = str(client.get("qui_proxy_url") or "").strip()
        if proxy_url:
            ssl_context = self.create_ssl_context_for_client(client)
            session = httpx.AsyncClient(timeout=10.0, verify=ssl_context)
            return proxy_url, proxy_url.rstrip("/"), session, None
        qbt_client = await self.init_qbittorrent_client(client)
        if qbt_client is None:
            return None
        return "", "", None, qbt_client

    @staticmethod
    def _qbit_save_path(
        path: str,
        tracker_dir: str | Path | None,
        use_symlink: bool,
        use_hardlink: bool,
        local_path: str,
        remote_path: str,
    ) -> str:
        raw = str(tracker_dir) if use_symlink or use_hardlink else path
        return map_save_path(raw, local_path, remote_path)

    @staticmethod
    def _qbit_auto_management(
        path: str,
        client: dict[str, Any],
        use_symlink: bool,
        use_hardlink: bool,
    ) -> bool:
        if use_symlink or use_hardlink:
            return False
        config = coerce_str_list(client.get("automatic_management_paths", ""))
        logger.debug(f"AM Config: {config}")
        return any(is_path_under(path, item) for item in config)

    @staticmethod
    def _qbit_category(client: dict[str, Any], meta: Meta, cross: bool) -> Any:
        if cross and client.get("qbit_cross_cat"):
            return client["qbit_cross_cat"]
        if meta.qbit_cat:
            return meta.qbit_cat
        return client.get("qbit_cat")

    @staticmethod
    def _standard_qbit_tag(
        client: dict[str, Any], meta: Meta, tracker: str
    ) -> Any:
        if meta.qbit_tag:
            return meta.qbit_tag
        if client.get("use_tracker_as_tag", False) and tracker:
            return tracker
        return client.get("qbit_tag")

    @classmethod
    def _qbit_tag(
        cls, client: dict[str, Any], meta: Meta, tracker: str, cross: bool
    ) -> Any:
        cross_tag = client.get("qbit_cross_tag")
        if cross and cross_tag:
            return cross_tag
        return cls._standard_qbit_tag(client, meta, tracker)

    @staticmethod
    def _qbit_proxy_add_payload(
        torrent: Torrent,
        save_path: str,
        auto_management: bool,
        paused_on_add: bool,
        content_layout: Any,
        category: Any,
        tag: Any,
    ) -> tuple[dict[str, tuple[str, bytes, str]], dict[str, Any]]:
        files = {
            "torrents": (
                "torrent.torrent",
                torrent.dump(),
                "application/x-bittorrent",
            )
        }
        data: dict[str, Any] = {
            "savepath": save_path,
            "autoTMM": str(auto_management).lower(),
            "skip_checking": "true",
            "paused": str(paused_on_add).lower(),
            "contentLayout": content_layout,
        }
        if category:
            data["category"] = category
        if tag:
            data["tags"] = tag
        return files, data

    @staticmethod
    def _qbit_direct_add_kwargs(
        torrent: Torrent,
        save_path: str,
        auto_management: bool,
        paused_on_add: bool,
        content_layout: Any,
        category: Any,
        tag: Any,
    ) -> dict[str, Any]:
        return {
            "torrent_files": torrent.dump(),
            "save_path": save_path,
            "use_auto_torrent_management": auto_management,
            "is_skip_checking": True,
            "is_paused": paused_on_add,
            "is_stopped": paused_on_add,
            "paused": paused_on_add,
            "content_layout": content_layout,
            "category": category,
            "tags": tag,
        }

    async def _qbit_torrent_present(
        self,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bool:
        if proxy_url:
            if qbt_session is None:
                return False
            response = await qbt_session.get(
                f"{qbt_proxy_url}/api/v2/torrents/info",
                params={"hashes": torrent_hash},
            )
            return response.status_code == 200 and bool(response.json())
        if qbt_client is None:
            return False
        torrents = await asyncio.to_thread(
            qbt_client.torrents_info, torrent_hashes=torrent_hash
        )
        return bool(torrents)

    async def _perform_qbit_add(
        self,
        torrent: Torrent,
        save_path: str,
        auto_management: bool,
        paused_on_add: bool,
        content_layout: Any,
        category: Any,
        tag: Any,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        if proxy_url:
            if qbt_session is None:
                raise RuntimeError(
                    "qBittorrent proxy session is not initialized"
                )
            files, data = self._qbit_proxy_add_payload(
                torrent,
                save_path,
                auto_management,
                paused_on_add,
                content_layout,
                category,
                tag,
            )
            logger.debug(
                f"[cyan]POSTing to {Redaction.redact_private_info(qbt_proxy_url)}/api/v2/torrents/add "
                f"with data: savepath={save_path}, autoTMM={auto_management}, skip_checking=True, "
                f"paused={paused_on_add}, contentLayout={content_layout}, category={category}, tags={tag}"
            )
            await self._add_torrent_via_proxy(
                qbt_session, qbt_proxy_url, torrent.infohash, data, files
            )
            return
        if qbt_client is None:
            raise RuntimeError("qBittorrent client is not initialized")
        await self._add_torrent_direct(
            qbt_client,
            torrent.infohash,
            self._qbit_direct_add_kwargs(
                torrent,
                save_path,
                auto_management,
                paused_on_add,
                content_layout,
                category,
                tag,
            ),
        )

    async def _add_qbit_with_recovery(
        self,
        torrent: Torrent,
        save_path: str,
        auto_management: bool,
        paused_on_add: bool,
        content_layout: Any,
        category: Any,
        tag: Any,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bool:
        try:
            await self._perform_qbit_add(
                torrent,
                save_path,
                auto_management,
                paused_on_add,
                content_layout,
                category,
                tag,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
            return True
        except _ProxyResponseError as error:
            logger.info(f"[bold red]Failed to add torrent via proxy: {error}")
            return False
        except (
            TimeoutError,
            httpx.HTTPError,
            qbittorrentapi.APIConnectionError,
        ) as error:
            with contextlib.suppress(Exception):
                if await self._qbit_torrent_present(
                    torrent.infohash,
                    proxy_url,
                    qbt_proxy_url,
                    qbt_session,
                    qbt_client,
                ):
                    logger.info("[green]Torrent was confirmed in qBittorrent.")
                    return True
            logger.info(
                f"[bold red]Failed to add torrent to qBittorrent: {error}"
            )
            return False
        except Exception as error:
            logger.info(f"[bold red]Error adding torrent: {error}")
            return False

    async def _proxy_qbit_addition_present(
        self,
        torrent_hash: str,
        tracker: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> bool:
        if qbt_session is None:
            return False
        response = await qbt_session.get(
            f"{qbt_proxy_url}/api/v2/torrents/info",
            params={"hashes": torrent_hash},
        )
        present = response.status_code == 200 and bool(response.json())
        if present:
            logger.debug(f"[green]Found {tracker} torrent in qBittorrent.")
        return present

    async def _direct_qbit_addition_present(
        self,
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bool:
        if qbt_client is None:
            return False
        torrents = await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_info,
                torrent_hashes=torrent_hash,
            ),
            "Check torrent addition",
            max_retries=1,
            initial_timeout=10.0,
        )
        return bool(torrents)

    async def _qbit_addition_present_once(
        self,
        torrent_hash: str,
        tracker: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bool:
        if proxy_url:
            return await self._proxy_qbit_addition_present(
                torrent_hash, tracker, qbt_proxy_url, qbt_session
            )
        return await self._direct_qbit_addition_present(
            torrent_hash, qbt_client
        )

    async def _wait_for_qbit_addition(
        self,
        torrent_hash: str,
        tracker: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bool:
        for _attempt in range(30):
            try:
                if await self._qbit_addition_present_once(
                    torrent_hash,
                    tracker,
                    proxy_url,
                    qbt_proxy_url,
                    qbt_session,
                    qbt_client,
                ):
                    return True
            except Exception as error:
                logger.debug(
                    f"[yellow]Waiting for qBittorrent addition retry after: {error}[/yellow]"
                )
            await asyncio.sleep(1)
        logger.info("[red]Torrent addition timed out.")
        return False

    async def _resume_proxy_qbit_torrent(
        self,
        torrent_hash: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> None:
        if qbt_session is None:
            return
        response = await self._post_proxy_command(
            qbt_session,
            f"{qbt_proxy_url}/api/v2/torrents/start",
            {"hashes": torrent_hash},
            "Start torrent via qBittorrent proxy",
            accepted_statuses=(200, 404),
        )
        if response.status_code != 404:
            return
        logger.debug(
            "[cyan]Start endpoint returned 404, trying legacy resume endpoint (pre-v5.0.0)..."
        )
        await self._post_proxy_command(
            qbt_session,
            f"{qbt_proxy_url}/api/v2/torrents/resume",
            {"hashes": torrent_hash},
            "Resume torrent via qBittorrent proxy",
        )

    async def _resume_direct_qbit_torrent(
        self,
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        if qbt_client is None:
            return
        await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_resume, torrent_hash
            ),
            "Resume torrent",
        )

    async def _resume_qbit_torrent(
        self,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        try:
            if proxy_url:
                await self._resume_proxy_qbit_torrent(
                    torrent_hash, qbt_proxy_url, qbt_session
                )
            else:
                await self._resume_direct_qbit_torrent(
                    torrent_hash, qbt_client
                )
        except TimeoutError:
            logger.info("[yellow]Failed to resume torrent after retries")
        except Exception as error:
            logger.info(f"[yellow]Error resuming torrent: {error}")

    async def _set_proxy_qbit_super_seed(
        self,
        torrent_hash: str,
        tracker: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> None:
        if qbt_session is None:
            return
        response = await qbt_session.post(
            f"{qbt_proxy_url}/api/v2/torrents/setSuperSeeding",
            data={"hashes": torrent_hash, "value": "true"},
        )
        if response.status_code != 200:
            logger.info(
                f"{tracker}: Failed to set super-seed via proxy: {response.status_code}"
            )

    async def _set_direct_qbit_super_seed(
        self,
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        if qbt_client is None:
            return
        await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_set_super_seeding,
                torrent_hashes=torrent_hash,
            ),
            "Set super-seed mode",
            initial_timeout=10.0,
        )

    async def _set_qbit_super_seed(
        self,
        torrent_hash: str,
        tracker: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        try:
            logger.debug(f"{tracker}: Setting super-seed mode.")
            if proxy_url:
                await self._set_proxy_qbit_super_seed(
                    torrent_hash, tracker, qbt_proxy_url, qbt_session
                )
            else:
                await self._set_direct_qbit_super_seed(
                    torrent_hash, qbt_client
                )
        except TimeoutError:
            logger.info(f"{tracker}: Super-seed request timed out")
        except Exception as error:
            logger.info(f"{tracker}: Super-seed error: {error}")

    @staticmethod
    def _log_proxy_qbit_debug_info(response: Any) -> None:
        if response.status_code != 200:
            logger.debug(
                f"[yellow]Failed to get torrent info via proxy: {response.status_code}"
            )
            return
        info = response.json()
        if info:
            logger.debug(
                f"[cyan]Actual qBittorrent save path: {info[0].get('save_path', 'Unknown')}"
            )
        else:
            logger.debug("[yellow]No torrent info returned from proxy")

    async def _debug_proxy_qbit_addition(
        self,
        torrent_hash: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> None:
        if qbt_session is None:
            return
        response = await qbt_session.get(
            f"{qbt_proxy_url}/api/v2/torrents/info",
            params={"hashes": torrent_hash},
        )
        self._log_proxy_qbit_debug_info(response)

    async def _debug_direct_qbit_addition(
        self,
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        if qbt_client is None:
            return
        info = await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_info, torrent_hashes=torrent_hash
            ),
            "Get torrent info for debug",
            initial_timeout=10.0,
        )
        if info:
            logger.debug(
                f"[cyan]Actual qBittorrent save path: {info[0].save_path}"
            )
        else:
            logger.debug("[yellow]No torrent info returned from qBittorrent")

    async def _debug_qbit_addition(
        self,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        try:
            if proxy_url:
                await self._debug_proxy_qbit_addition(
                    torrent_hash, qbt_proxy_url, qbt_session
                )
            else:
                await self._debug_direct_qbit_addition(
                    torrent_hash, qbt_client
                )
        except TimeoutError:
            logger.debug(
                "[yellow]Failed to get torrent info for debug after retries"
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Error getting torrent info for debug: {error}"
            )

    def _build_qbit_add_plan(
        self,
        path: str,
        tracker_dir: str | Path | None,
        use_symlink: bool,
        use_hardlink: bool,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        meta: Meta,
        tracker: str,
        cross: bool,
    ) -> _QbitAddPlan:
        save_path = self._qbit_save_path(
            path,
            tracker_dir,
            use_symlink,
            use_hardlink,
            local_path,
            remote_path,
        )
        plan = _QbitAddPlan(
            save_path=save_path,
            auto_management=self._qbit_auto_management(
                path, client, use_symlink, use_hardlink
            ),
            category=self._qbit_category(client, meta, cross),
            content_layout=client.get("content_layout", "Original"),
            tag=self._qbit_tag(client, meta, tracker, cross),
            paused_on_add=cross,
        )
        logger.debug(f"qbt_category: {plan.category}")
        logger.debug(f"Content Layout: {plan.content_layout}")
        logger.debug(f"[bold yellow]qBittorrent save path: {plan.save_path}")
        logger.debug(f"[cyan]Original path: {path}")
        logger.debug(f"[cyan]Mapped save path: {plan.save_path}")
        return plan

    @staticmethod
    def _qbit_super_seed_enabled(
        client: dict[str, Any], tracker: str, cross: bool
    ) -> bool:
        return not cross and tracker in client.get("super_seed_trackers", [])

    async def _post_qbit_add_actions(
        self,
        torrent: Torrent,
        tracker: str,
        client: dict[str, Any],
        meta: Meta,
        cross: bool,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        if not cross:
            await self._resume_qbit_torrent(
                torrent.infohash,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
        if self._qbit_super_seed_enabled(client, tracker, cross):
            await self._set_qbit_super_seed(
                torrent.infohash,
                tracker,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
        if meta.debug:
            await self._debug_qbit_addition(
                torrent.infohash,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )

    async def _execute_qbit_add_plan(
        self,
        torrent: Torrent,
        plan: _QbitAddPlan,
        tracker: str,
        client: dict[str, Any],
        meta: Meta,
        cross: bool,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> None:
        added = await self._add_qbit_with_recovery(
            torrent,
            plan.save_path,
            plan.auto_management,
            plan.paused_on_add,
            plan.content_layout,
            plan.category,
            plan.tag,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        if not added:
            return
        present = await self._wait_for_qbit_addition(
            torrent.infohash,
            tracker,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        if not present:
            return
        logger.debug(
            f"[green]Successfully added torrent to qBittorrent ({tracker})[/green]"
        )
        await self._post_qbit_add_actions(
            torrent,
            tracker,
            client,
            meta,
            cross,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        logger.debug(f"Added to: {plan.save_path}")

    async def qbittorrent(
        self,
        path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        _is_disc: str,
        filelist: list[str],
        meta: Meta,
        tracker: str,
        cross: bool = False,
    ) -> None:
        path = self._normalized_qbit_add_path(path, meta, filelist)
        src = self._qbit_source_path(meta)
        use_symlink, use_hardlink = self._qbit_linking_flags(client)
        (
            tracker_dir,
            use_symlink,
            use_hardlink,
            proceed,
        ) = await self._prepare_qbit_links(
            meta,
            torrent,
            client,
            tracker,
            src,
            use_symlink,
            use_hardlink,
            cross,
        )
        if not proceed:
            return
        handles = await self._qbit_add_handles(client)
        if handles is None:
            return
        proxy_url, qbt_proxy_url, qbt_session, qbt_client = handles
        plan = self._build_qbit_add_plan(
            path,
            tracker_dir,
            use_symlink,
            use_hardlink,
            local_path,
            remote_path,
            client,
            meta,
            tracker,
            cross,
        )
        try:
            await self._execute_qbit_add_plan(
                torrent,
                plan,
                tracker,
                client,
                meta,
                cross,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
        finally:
            if qbt_session is not None:
                await qbt_session.aclose()

    async def get_pathed_torrents(self, path: str, meta: Meta) -> None:
        try:
            matching_torrents = await self.find_qbit_torrents_by_path(
                path, meta
            )

            # If we found matches, use the hash from the first exact match
            if matching_torrents:
                exact_matches = list(matching_torrents)
                if exact_matches:
                    meta.infohash = exact_matches[0]["hash"]
                    logger.debug(
                        f"[green]Found exact torrent match with hash: {meta.infohash}"
                    )

            else:
                logger.debug(
                    "[yellow]No matching torrents for the path found in qBittorrent[/yellow]"
                )

        except TimeoutError:
            raise
        except Exception as e:
            logger.error(f"[red]Error searching for torrents: {e!s}[/red]")
            logger.info(f"[dim]{traceback.format_exc()}[/dim]")

    def _configure_qbit_piece_constraint(self, meta: Meta) -> None:
        defaults = self.config.get("DEFAULT", {})
        default_map = (
            cast(dict[str, Any], defaults)
            if isinstance(defaults, dict)
            else {}
        )
        prefer_limit = bool(default_map.get("prefer_max_16_torrent", False))
        meta.piece_size_constraints_enabled = (
            "16MiB" if prefer_limit else False
        )

    def _qbit_default_settings(self) -> dict[str, Any]:
        defaults = self.config.get("DEFAULT", {})
        return (
            cast(dict[str, Any], defaults)
            if isinstance(defaults, dict)
            else {}
        )

    @staticmethod
    def _configured_qbit_search_clients(defaults: dict[str, Any]) -> list[str]:
        return [
            name
            for name in coerce_str_list(
                defaults.get("searching_client_list", [])
            )
            if name and name != "none"
        ]

    @staticmethod
    def _default_qbit_search_client(defaults: dict[str, Any]) -> list[str]:
        default_client = defaults.get("default_torrent_client")
        if isinstance(default_client, str) and default_client != "none":
            return [default_client]
        return []

    def _qbit_search_client_names(self, meta: Meta) -> list[str]:
        meta_client = meta.client
        if isinstance(meta_client, str) and meta_client != "none":
            return [meta_client]
        defaults = self._qbit_default_settings()
        configured = self._configured_qbit_search_clients(defaults)
        return (
            configured
            if configured
            else self._default_qbit_search_client(defaults)
        )

    def _qbit_client_config(self, client_name: str) -> dict[str, Any] | None:
        clients = self.config.get("TORRENT_CLIENTS", {})
        client_map = (
            cast(dict[str, Any], clients) if isinstance(clients, dict) else {}
        )
        raw = client_map.get(client_name)
        if not isinstance(raw, dict):
            logger.debug(f"[yellow]Client {client_name} not found in config")
            return None
        config = cast(dict[str, Any], raw)
        if config.get("torrent_client") != "qbit":
            logger.debug(f"[yellow]Client {client_name} is not qBittorrent")
            return None
        return config

    @staticmethod
    def _qbit_search_should_stop(meta: Meta) -> bool:
        constraints = meta.piece_size_constraints_enabled
        found_piece_size = meta.found_preferred_piece_size
        return bool(
            not constraints
            or found_piece_size == "no_constraints"
            or (found_piece_size == "16MiB" and constraints == "16MiB")
        )

    async def _search_qbit_clients(
        self,
        content_path: str,
        meta: Meta,
        clients_to_search: list[str],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for client_name in clients_to_search:
            client_config = self._qbit_client_config(client_name)
            if client_config is None:
                continue
            logger.debug(f"[cyan]Searching qBittorrent client: {client_name}")
            torrents = await self._search_single_qbit_client(
                client_config, content_path, meta, client_name
            )
            if not torrents:
                logger.debug(f"[yellow]No matches in client {client_name}")
                continue
            matches.extend(torrents)
            if self._qbit_search_should_stop(meta):
                logger.debug(
                    "[green]Stopping search after finding preferred torrent"
                )
                break
        return matches

    @staticmethod
    def _deduplicate_qbit_matches(
        torrents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen_hashes: set[str] = set()
        unique: list[dict[str, Any]] = []
        for torrent in torrents:
            torrent_hash = str(torrent["hash"])
            if torrent_hash in seen_hashes:
                continue
            seen_hashes.add(torrent_hash)
            unique.append(torrent)
        return unique

    @staticmethod
    def _log_qbit_path_search_duration(
        meta: Meta,
        started: float,
        all_matches: list[dict[str, Any]],
        unique_matches: list[dict[str, Any]],
    ) -> None:
        duration = time.time() - started
        if meta.debug and len(all_matches) != len(unique_matches):
            logger.debug(
                f"[cyan]Deduplicated {len(all_matches)} torrents to {len(unique_matches)} unique torrents"
            )
        if meta.debug:
            logger.debug(
                f"Searching qBittorrent client data processed in {duration:.2f} seconds"
            )

    async def _find_qbit_torrents_by_path_flow(
        self, content_path: str, meta: Meta, started: float
    ) -> list[dict[str, Any]]:
        self._configure_qbit_piece_constraint(meta)
        clients_to_search = self._qbit_search_client_names(meta)
        if not clients_to_search:
            logger.debug("[yellow]No clients configured for searching")
            logger.debug(
                f"Searching qBittorrent client data processed in {time.time() - started:.2f} seconds"
            )
            return []
        all_matches = await self._search_qbit_clients(
            content_path, meta, clients_to_search
        )
        unique_matches = self._deduplicate_qbit_matches(all_matches)
        self._log_qbit_path_search_duration(
            meta, started, all_matches, unique_matches
        )
        return unique_matches

    async def find_qbit_torrents_by_path(
        self, content_path: str, meta: Meta
    ) -> list[dict[str, Any]]:
        started = time.time()
        logger.debug(
            f"[yellow]Searching for torrents in qBittorrent for path: {content_path}[/yellow]"
        )
        try:
            return await self._find_qbit_torrents_by_path_flow(
                content_path, meta, started
            )
        except TimeoutError:
            raise
        except Exception as error:
            logger.info(f"[bold red]Error finding torrents: {error!s}")
            logger.debug(traceback.format_exc())
            logger.debug(
                f"Searching qBittorrent client data processed in {time.time() - started:.2f} seconds"
            )
            return []

    def _build_proxy_search_url(
        self,
        qbt_proxy_url: str,
        search_term: str,
        qui_filters: dict[str, list[str]],
    ) -> str:
        query_parts = [
            f"search={urllib.parse.quote(search_term)}",
            "sort=added_on",
            "reverse=true",
            "limit=100",
        ]

        if qui_filters.get("excludeStatus"):
            filter_value = ",".join(qui_filters["excludeStatus"])
            query_parts.append(f"filter={urllib.parse.quote(filter_value)}")

        if qui_filters.get("categories"):
            category_value = ",".join(qui_filters["categories"])
            query_parts.append(
                f"category={urllib.parse.quote(category_value)}"
            )

        if qui_filters.get("tags"):
            tag_value = ",".join(qui_filters["tags"])
            query_parts.append(f"tag={urllib.parse.quote(tag_value)}")

        query_string = "&".join(query_parts)
        return f"{qbt_proxy_url}/api/v2/torrents/search?{query_string}"

    def _build_mock_torrents(
        self, torrents_data: list[dict[str, Any]]
    ) -> list[Any]:
        class MockTorrent:
            def __init__(self, data: dict[str, Any]):
                for key, value in data.items():
                    setattr(self, key, value)
                if "files" not in data:
                    self.files: list[Any] = []
                if "tracker" not in data:
                    self.tracker = ""
                if "comment" not in data:
                    self.comment = ""

            def __getattr__(self, name: str) -> Any:
                return None

        return [MockTorrent(torrent) for torrent in torrents_data]

    @staticmethod
    def _single_file_name_candidates(meta: Meta) -> set[str]:
        if meta.is_disc not in ("", None) or len(meta.filelist) != 1:
            return set()
        file_path = PureWindowsPath(str(meta.filelist[0]))
        values = (file_path.name, file_path.parent.name)
        return {value.casefold() for value in values if value}

    @staticmethod
    def _torrent_name_candidates(meta: Meta) -> set[str]:
        candidates = {str(meta.uuid or "").casefold()}
        candidates.update(
            QbittorrentClientMixin._single_file_name_candidates(meta)
        )
        return candidates

    def _torrent_name_matches(self, torrent_name: str, meta: Meta) -> bool:
        return torrent_name.casefold() in self._torrent_name_candidates(meta)

    @staticmethod
    def _pattern_tracker_match(
        comment: str,
        tracker_id: str,
        tracker_info: dict[str, str] | None,
        has_working_tracker: bool,
    ) -> dict[str, Any] | None:
        if not tracker_info or not has_working_tracker:
            return None
        if tracker_info["url"] not in comment:
            return None
        match = re.search(tracker_info["pattern"], comment)
        if match is None:
            return None
        return {"id": tracker_id, "tracker_id": match.group(1)}

    def _generic_tracker_matches(
        self,
        torrent: Any,
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        has_working_tracker: bool,
        meta: Meta,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        comment = str(getattr(torrent, "comment", "") or "")
        for tracker_id in tracker_priority:
            result = self._pattern_tracker_match(
                comment,
                tracker_id,
                tracker_patterns.get(tracker_id),
                has_working_tracker,
            )
            if result is None:
                continue
            matches.append(result)
            meta.set_tracker_ids({tracker_id: str(result["tracker_id"])})
        return matches

    @staticmethod
    def _huno_comment_id(comment: str) -> str:
        match = re.search(r"/torrents/(\d+)", comment)
        return match.group(1) if match is not None else ""

    @staticmethod
    def _huno_tracker_match(
        torrent: Any, has_working_tracker: bool
    ) -> dict[str, Any] | None:
        tracker = str(getattr(torrent, "tracker", ""))
        if not has_working_tracker:
            return None
        if "hawke.uno" not in tracker:
            return None
        huno_id = QbittorrentClientMixin._huno_comment_id(
            str(getattr(torrent, "comment", ""))
        )
        if not huno_id:
            return None
        return {"id": "huno", "tracker_id": huno_id}

    @staticmethod
    def _anthelion_tracker_match(
        torrent: Any, has_working_tracker: bool
    ) -> dict[str, Any] | None:
        tracker = str(getattr(torrent, "tracker", "") or "")
        if has_working_tracker and "tracker.anthelion.me" in tracker:
            return {"id": "ant", "tracker_id": 1}
        return None

    @staticmethod
    def _store_special_tracker_match(
        meta: Meta, match: dict[str, Any] | None
    ) -> None:
        if match is None:
            return
        if match["id"] == "huno":
            meta.set_tracker_ids({"HAWKEUNO": str(match["tracker_id"])})
        elif match["id"] == "ant":
            meta.set_tracker_ids({"anthelion": str(match["tracker_id"])})

    def _extract_tracker_matches(
        self,
        torrent: Any,
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        has_working_tracker: bool,
        meta: Meta,
    ) -> tuple[list[dict[str, Any]], bool]:
        matches = self._generic_tracker_matches(
            torrent,
            tracker_patterns,
            tracker_priority,
            has_working_tracker,
            meta,
        )
        for special in (
            self._huno_tracker_match(torrent, has_working_tracker),
            self._anthelion_tracker_match(torrent, has_working_tracker),
        ):
            if special is not None:
                matches.append(special)
                self._store_special_tracker_match(meta, special)
        return matches, bool(matches)

    def _sort_matching_torrents(
        self,
        matching_torrents: list[dict[str, Any]],
        tracker_priority: list[str],
    ) -> None:
        def get_priority_score(
            torrent: dict[str, Any],
        ) -> tuple[bool, int, bool]:
            priority_score = 100
            if torrent.get("tracker_urls"):
                for tracker_url in torrent["tracker_urls"]:
                    tracker_id = tracker_url.get("id")
                    if tracker_id in tracker_priority:
                        score = tracker_priority.index(tracker_id)
                        priority_score = min(priority_score, score)

            return (
                not torrent["has_working_tracker"],
                priority_score,
                not torrent["has_tracker"],
            )

        matching_torrents.sort(key=get_priority_score)

    @staticmethod
    def _hardcoded_tracker_urls() -> dict[str, str]:
        return {
            "PASSTHEPOPCORN": "passthepopcorn.me",
            "AITHER": "https://aither.cc",
            "LST": "https://lst.gg",
            "ONLYENCODES": "https://onlyencodes.cc",
            "BLUTOPIA": "https://blutopia.cc",
            "ULCX": "https://upload.cx",
            "HDBITS": "https://hdbits.org",
            "BTN": "https://broadcasthe.net",
            "BEYONDHD": "https://beyond-hd.me",
            "HAWKEUNO": "https://hawke.uno",
            "REELFLIX": "https://reelflix.xyz",
            "OLDTOONSWORLD": "https://oldtoons.world",
            "YUSCENE": "https://yu-scene.net",
            "DARKPEERS": "https://darkpeers.org",
            "SEEDPOOL": "https://seedpool.org",
        }

    @staticmethod
    def _tracker_id_pattern(name: str) -> str:
        if name == "PASSTHEPOPCORN":
            return r"torrentid=(\d+)"
        if name in ("HDBITS", "BTN"):
            return r"id=(\d+)"
        if name == "BEYONDHD":
            return r"details/(\d+)"
        return r"/(\d+)$"

    def _configured_tracker_url(self, name: str) -> str:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return ""
        raw = cast(dict[str, Any], trackers).get(name, {})
        if not isinstance(raw, dict):
            return ""
        return str(cast(dict[str, Any], raw).get("announce_url", "") or "")

    def _registry_tracker_url(
        self, name: str, tracker_class_map: dict[str, Any]
    ) -> str:
        tracker_class = tracker_class_map.get(name)
        if tracker_class is None:
            return ""
        with contextlib.suppress(Exception):
            instance = tracker_class(self.config)
            return str(getattr(instance, "base_url", "") or "")
        return ""

    def _resolved_tracker_url(
        self, name: str, tracker_class_map: dict[str, Any]
    ) -> str:
        return (
            self._registry_tracker_url(name, tracker_class_map)
            or self._configured_tracker_url(name)
            or self._hardcoded_tracker_urls().get(name, "")
        )

    @staticmethod
    def _tracker_priority(patterns: dict[str, dict[str, str]]) -> list[str]:
        prioritized = [
            "aither",
            "ulcx",
            "lst",
            "blu",
            "oe",
            "btn",
            "bhd",
            "huno",
            "hdb",
            "rf",
            "otw",
            "yus",
            "dp",
            "sp",
            "ptp",
        ]
        remaining = [
            name for name in sorted(patterns) if name not in prioritized
        ]
        return [*prioritized, *remaining]

    def _setup_tracker_patterns(
        self,
    ) -> tuple[dict[str, dict[str, str]], list[str]]:
        from src.integrations.trackers.registry import tracker_class_map

        names = set(tracker_class_map) | {
            "PASSTHEPOPCORN",
            "BEYONDHD",
            "BTN",
            "HDBITS",
        }
        patterns: dict[str, dict[str, str]] = {}
        for name in names:
            url = self._resolved_tracker_url(name, tracker_class_map)
            if not url:
                continue
            patterns[name.lower()] = {
                "url": url,
                "pattern": self._tracker_id_pattern(name),
            }
        return patterns, self._tracker_priority(patterns)

    @staticmethod
    def _proxy_search_filters() -> dict[str, list[str]]:
        return {
            "status": [],
            "excludeStatus": [],
            "categories": [],
            "excludeCategories": [],
            "tags": [],
            "excludeTags": [],
            "trackers": [],
            "excludeTrackers": [],
        }

    @staticmethod
    def _proxy_torrent_list(value: Any) -> list[Any]:
        if isinstance(value, dict):
            raw = cast(dict[str, Any], value).get("torrents", [])
        else:
            raw = value
        return cast(list[Any], raw) if isinstance(raw, list) else []

    @classmethod
    def _proxy_torrent_payload(cls, value: Any) -> list[dict[str, Any]]:
        return [
            cast(dict[str, Any], item)
            for item in cls._proxy_torrent_list(value)
            if isinstance(item, dict)
        ]

    @staticmethod
    def _log_proxy_search_result(
        torrents_data: list[dict[str, Any]], search_term: str
    ) -> None:
        if torrents_data:
            logger.debug(
                f"[cyan]qBittorrent proxy search returned {len(torrents_data)} torrents for '{search_term}'"
            )
            return
        logger.debug("[cyan]No matching torrents found via proxy search")

    async def _fetch_proxy_torrents(
        self,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient,
        search_term: str,
    ) -> list[Any]:
        url = self._build_proxy_search_url(
            qbt_proxy_url, search_term, self._proxy_search_filters()
        )
        logger.debug(
            f"[cyan]Searching qBittorrent via proxy: {Redaction.redact_private_info(url)}..."
        )
        started = time.perf_counter()
        try:
            response = await qbt_session.get(url)
        finally:
            self._log_slow_client_response(
                time.perf_counter() - started, using_proxy=True
            )
        if response.status_code == 200:
            torrents_data = self._proxy_torrent_payload(response.json())
            self._log_proxy_search_result(torrents_data, search_term)
            return self._build_mock_torrents(torrents_data)
        if response.status_code == 404:
            logger.debug(
                f"[yellow]No torrents found via proxy search for '[green]{search_term}' [yellow]Maybe tracker errors?"
            )
        else:
            logger.debug(
                f"[bold red]Failed to get torrents list via proxy: {response.status_code}"
            )
        return []

    async def _fetch_direct_torrents(
        self, qbt_client: qbittorrentapi.Client
    ) -> list[Any]:
        started = time.perf_counter()
        try:
            result = await self.retry_qbt_operation(
                lambda: asyncio.to_thread(qbt_client.torrents_info),
                "Get torrents list",
                initial_timeout=14.0,
            )
            return list(result or [])
        finally:
            self._log_slow_client_response(
                time.perf_counter() - started, using_proxy=False
            )

    async def _selected_torrent_source(
        self,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        search_term: str,
    ) -> list[Any]:
        if proxy_url:
            if qbt_session is None:
                return []
            return await self._fetch_proxy_torrents(
                qbt_proxy_url, qbt_session, search_term
            )
        if qbt_client is None:
            return []
        return await self._fetch_direct_torrents(qbt_client)

    async def _fetch_torrents(
        self,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        search_term: str,
    ) -> list[Any]:
        try:
            return await self._selected_torrent_source(
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
                search_term,
            )
        except TimeoutError:
            logger.info(
                "[bold red]Getting torrents list timed out after retries"
            )
            return []
        except Exception as error:
            logger.info(f"[bold red]Error getting torrents list: {error}")
            return []

    @staticmethod
    def _log_slow_client_response(duration: float, using_proxy: bool) -> None:
        if duration <= 5:
            return

        logger.info(
            f"[yellow]qBittorrent client response took {duration:.1f} seconds.[/yellow]"
        )
        if not using_proxy:
            logger.info(
                "[yellow]For faster searches, consider configuring 'qui_proxy_url' in your config.[/yellow]"
            )

    @staticmethod
    def _torrent_tracker_url(torrent: Any) -> str:
        return str(getattr(torrent, "tracker", "") or "")

    @staticmethod
    def _proxy_tracker_entries(torrent: Any) -> list[dict[str, Any]]:
        raw = getattr(torrent, "trackers", []) or []
        if not isinstance(raw, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], raw)
            if isinstance(item, dict)
        ]

    async def _proxy_torrent_comment(
        self,
        torrent: Any,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
    ) -> bool:
        if getattr(torrent, "comment", ""):
            return True
        if qbt_session is None:
            return False
        logger.debug(
            f"[cyan]Fetching torrent properties via proxy for torrent: {torrent.name}"
        )
        response = await qbt_session.get(
            f"{qbt_proxy_url}/api/v2/torrents/properties",
            params={"hash": torrent.hash},
        )
        if response.status_code != 200:
            logger.debug(
                f"[yellow]Failed to get properties for torrent {torrent.name} via proxy: {response.status_code}"
            )
            return False
        payload = response.json()
        properties = (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )
        torrent.comment = properties.get("comment", "")
        return True

    @staticmethod
    def _normalized_direct_tracker_entry(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return cast(dict[str, Any], value)
        getter = getattr(value, "get", None)
        if callable(getter):
            getter("url", "")
        return None

    def _normalize_direct_trackers(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        normalized = [
            self._normalized_direct_tracker_entry(item)
            for item in cast(list[Any], raw)
        ]
        return [entry for entry in normalized if entry is not None]

    async def _direct_torrent_trackers(
        self, torrent: Any, qbt_client: qbittorrentapi.Client | None
    ) -> list[dict[str, Any]]:
        if qbt_client is None:
            return []
        raw = await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_trackers, torrent_hash=torrent.hash
            ),
            f"Get trackers for torrent {torrent.name}",
        )
        return self._normalize_direct_trackers(raw)

    async def _match_tracker_entries(
        self,
        torrent: Any,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> list[dict[str, Any]] | None:
        try:
            if proxy_url:
                if not await self._proxy_torrent_comment(
                    torrent, qbt_proxy_url, qbt_session
                ):
                    return None
                return self._proxy_tracker_entries(torrent)
            return await self._direct_torrent_trackers(torrent, qbt_client)
        except TimeoutError, qbittorrentapi.APIError:
            logger.debug(
                f"[yellow]Failed to get trackers for torrent {torrent.name} after retries"
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Error getting trackers for torrent {torrent.name}: {error}"
            )
        return None

    @staticmethod
    def _display_tracker_entries(
        trackers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ignored = ("** [DHT]", "** [PeX]", "** [LSD]")
        return [
            tracker
            for tracker in trackers
            if not str(tracker.get("url", "")).startswith(ignored)
        ]

    @staticmethod
    def _tracker_status_text(status_code: Any) -> str:
        return {
            0: "Disabled",
            1: "Not contacted",
            2: "Working",
            3: "Updating",
            4: "Error",
        }.get(status_code, f"Unknown ({status_code})")

    @classmethod
    def _has_working_direct_tracker(
        cls, trackers: list[dict[str, Any]]
    ) -> bool:
        working = False
        for tracker in cls._display_tracker_entries(trackers):
            status = tracker.get("status", 0)
            if status != 2:
                continue
            working = True
            url = str(tracker.get("url", "Unknown URL"))
            logger.debug(
                f"[green]Tracker working: {url[:15]} - {cls._tracker_status_text(status)}"
            )
        return working

    @staticmethod
    def _torrent_comments(meta: Meta) -> list[dict[str, Any]]:
        raw = meta.torrent_comments
        if isinstance(raw, list):
            return cast(list[dict[str, Any]], raw)
        comments: list[dict[str, Any]] = []
        meta.torrent_comments = comments
        return comments

    @staticmethod
    def _torrent_match_info(
        torrent: Any, tracker_url: str, has_working_tracker: bool
    ) -> dict[str, Any]:
        return {
            "hash": torrent.hash,
            "name": torrent.name,
            "save_path": torrent.save_path,
            "content_path": os.path.normpath(
                Path(str(torrent.save_path)) / str(torrent.name)
            ),
            "size": torrent.size,
            "category": torrent.category,
            "seeders": torrent.num_complete,
            "trackers": tracker_url,
            "has_working_tracker": has_working_tracker,
            "comment": torrent.comment,
        }

    @staticmethod
    def _torrent_match_name(torrent: Any) -> str:
        name = str(getattr(torrent, "name", "") or "")
        if not name:
            logger.debug(
                "[yellow]Skipping torrent with missing name attribute"
            )
        return name

    @staticmethod
    def _working_tracker_state(
        proxy_url: str, trackers: list[dict[str, Any]]
    ) -> bool:
        return (
            True
            if proxy_url
            else QbittorrentClientMixin._has_working_direct_tracker(trackers)
        )

    async def _enriched_torrent_match(
        self,
        torrent: Any,
        info: dict[str, Any],
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        has_working: bool,
        meta: Meta,
    ) -> dict[str, Any]:
        tracker_matches, tracker_found = self._extract_tracker_matches(
            torrent,
            tracker_patterns,
            tracker_priority,
            has_working,
            meta,
        )
        info["tracker_urls"] = tracker_matches
        info["has_tracker"] = tracker_found
        if tracker_found:
            meta.found_tracker_match = True
        logger.debug(
            f"[cyan]Stored comment for torrent: {str(torrent.comment)[:100]}..."
        )
        return info

    async def _processed_torrent_match(
        self,
        torrent: Any,
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        meta: Meta,
    ) -> dict[str, Any] | None:
        name = self._torrent_match_name(torrent)
        if not name or not self._torrent_name_matches(name, meta):
            return None
        trackers = await self._match_tracker_entries(
            torrent,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
        )
        if trackers is None:
            return None
        has_working = self._working_tracker_state(proxy_url, trackers)
        tracker_url = self._torrent_tracker_url(torrent)
        await match_tracker_url([tracker_url] if tracker_url else [], meta)
        return await self._enriched_torrent_match(
            torrent,
            self._torrent_match_info(torrent, tracker_url, has_working),
            tracker_patterns,
            tracker_priority,
            has_working,
            meta,
        )

    async def _process_torrent_matches(
        self,
        torrents: list[Any],
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        meta: Meta,
    ) -> list[dict[str, Any]]:
        matching: list[dict[str, Any]] = []
        comments = self._torrent_comments(meta)
        for torrent in torrents:
            try:
                match = await self._processed_torrent_match(
                    torrent,
                    tracker_patterns,
                    tracker_priority,
                    proxy_url,
                    qbt_proxy_url,
                    qbt_session,
                    qbt_client,
                    meta,
                )
            except Exception as error:
                name = str(getattr(torrent, "name", "Unknown") or "Unknown")
                logger.debug(
                    f"[yellow]Error processing torrent {name}: {error!s}"
                )
                continue
            if match is None:
                continue
            comments.append(match)
            matching.append(match)
        return matching

    @staticmethod
    def _stored_torrent_path(
        torrent_storage_dir: str | None, torrent_hash: str
    ) -> Path | None:
        if not torrent_storage_dir:
            return None
        path = Path(torrent_storage_dir) / f"{torrent_hash}.torrent"
        return path if path.exists() else None

    async def _proxy_export_torrent_content(
        self,
        qbt_session: httpx.AsyncClient | None,
        proxy_url: str,
        torrent_hash: str,
        prefix: str,
    ) -> bytes | None:
        if qbt_session is None:
            logger.info("[bold red]Proxy session not initialized")
            return None
        try:
            response = await qbt_session.post(
                f"{proxy_url.rstrip('/')}/api/v2/torrents/export",
                data={"hash": torrent_hash},
            )
        except Exception as error:
            logger.error(
                f"[red]Error exporting {prefix}torrent via proxy: {error}"
            )
            return None
        if response.status_code != 200:
            logger.error(
                f"[red]Failed to export {prefix}torrent via proxy: {response.status_code}"
            )
            return None
        return response.content

    async def _direct_export_torrent_content(
        self,
        qbt_client: qbittorrentapi.Client | None,
        torrent_hash: str,
        prefix: str,
    ) -> bytes | None:
        if qbt_client is None:
            logger.info("[bold red]qBittorrent client not initialized")
            return None
        content = await self.retry_qbt_operation(
            lambda: asyncio.to_thread(
                qbt_client.torrents_export, torrent_hash=torrent_hash
            ),
            f"Export {prefix}torrent {torrent_hash}",
        )
        return (
            bytes(content) if isinstance(content, (bytes, bytearray)) else None
        )

    async def _export_torrent_content(
        self,
        torrent_hash: str,
        proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        prefix: str,
    ) -> bytes | None:
        if proxy_url:
            return await self._proxy_export_torrent_content(
                qbt_session, proxy_url, torrent_hash, prefix
            )
        return await self._direct_export_torrent_content(
            qbt_client, torrent_hash, prefix
        )

    @staticmethod
    async def _write_exported_torrent(
        extracted_torrent_dir: str, torrent_hash: str, content: bytes
    ) -> str:
        path = Path(extracted_torrent_dir) / f"{torrent_hash}.torrent"
        await asyncio.to_thread(path.write_bytes, content)
        return str(path)

    async def _export_torrent_file(
        self,
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        is_alternative: bool = False,
    ) -> str | None:
        _ = qbt_proxy_url
        prefix = "alternative " if is_alternative else ""
        stored = self._stored_torrent_path(torrent_storage_dir, torrent_hash)
        if stored is not None:
            logger.debug(
                f"[cyan]Found existing {prefix}.torrent file: {stored}"
            )
            return str(stored)
        logger.debug(
            f"[cyan]Exporting {prefix}.torrent file for hash: {torrent_hash}"
        )
        content = await self._export_torrent_content(
            torrent_hash, proxy_url, qbt_session, qbt_client, prefix
        )
        if content is None:
            logger.info(
                f"[bold red]Failed to export {prefix}.torrent for {torrent_hash} after retries"
            )
            return None
        path = await self._write_exported_torrent(
            extracted_torrent_dir, torrent_hash, content
        )
        logger.debug(f"[green]Exported {prefix}.torrent file to: {path}")
        return path

    @staticmethod
    def _base_selection_directories(
        client_config: dict[str, Any], meta: Meta
    ) -> tuple[str | None, str]:
        storage = client_config.get("torrent_storage_dir")
        storage_dir = str(storage) if storage else None
        extracted = str(Path(meta.base_dir) / "tmp" / meta.uuid)
        Path(extracted).mkdir(parents=True, exist_ok=True)
        return storage_dir, extracted

    def _piece_preference_enabled(self) -> bool:
        defaults = self.config.get("DEFAULT", {})
        values = (
            cast(dict[str, Any], defaults)
            if isinstance(defaults, dict)
            else {}
        )
        return bool(values.get("prefer_max_16_torrent", False))

    @staticmethod
    def _temporary_export_path(path: str, extracted_dir: str) -> bool:
        return is_path_under(path, extracted_dir)

    @classmethod
    def _cleanup_exported_candidate(
        cls, path: str, extracted_dir: str
    ) -> None:
        if not path or not cls._temporary_export_path(path, extracted_dir):
            return
        Path(path).unlink(missing_ok=True)

    async def _export_and_validate_base_candidate(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        is_alternative: bool,
    ) -> tuple[str, bool, str]:
        exported = await self._export_torrent_file(
            torrent_hash,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            torrent_storage_dir,
            extracted_torrent_dir,
            is_alternative=is_alternative,
        )
        exported_path = str(exported or "")
        if not exported_path:
            return "", False, ""
        try:
            valid, resolved = await self.is_valid_torrent(
                meta,
                exported_path,
                torrent_hash,
                "qbit",
                client_config,
            )
        except Exception:
            self._cleanup_exported_candidate(
                exported_path, extracted_torrent_dir
            )
            raise
        return exported_path, bool(valid), str(resolved or exported_path)

    async def _prepare_base_candidate(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        torrent_hash: str,
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        is_alternative: bool,
    ) -> _PreparedBaseCandidate | None:
        exported_path = ""
        try:
            (
                exported_path,
                valid,
                resolved_path,
            ) = await self._export_and_validate_base_candidate(
                meta,
                client_config,
                torrent_hash,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
                torrent_storage_dir,
                extracted_torrent_dir,
                is_alternative,
            )
        except Exception as error:
            label = "alternative " if is_alternative else ""
            logger.info(
                f"[bold red]Error preparing {label}torrent {torrent_hash}: {error}"
            )
            self._cleanup_exported_candidate(
                exported_path, extracted_torrent_dir
            )
            return None
        if not exported_path:
            return None
        if not valid:
            logger.debug(f"[bold red]{torrent_hash} failed validation")
            self._cleanup_exported_candidate(
                exported_path, extracted_torrent_dir
            )
            return None
        return {
            "hash": torrent_hash,
            "torrent_path": resolved_path,
            "exported_path": exported_path,
        }

    def _subtitle_candidate_state(
        self, meta: Meta, candidate: _PreparedBaseCandidate
    ) -> str:
        if not meta.subtitle_files:
            return "complete"
        path = candidate["torrent_path"]
        if self._torrent_includes_all_local_subtitles(path, meta):
            return "complete"
        if self._torrent_has_no_subtitles(path):
            return "video_only"
        return "partial"

    def _handle_subtitle_candidate(
        self,
        meta: Meta,
        candidate: _PreparedBaseCandidate,
        state: _BaseSelectionState,
        is_alternative: bool,
    ) -> bool:
        subtitle_state = self._subtitle_candidate_state(meta, candidate)
        if subtitle_state == "complete":
            return False
        label = " alternative" if is_alternative else ""
        if subtitle_state == "video_only":
            state.subtitle_fallback = {
                "hash": candidate["hash"],
                "torrent_path": candidate["torrent_path"],
            }
            logger.debug(
                f"[yellow]Keeping video-only{label} torrent as fallback: {candidate['hash']}"
            )
        else:
            logger.debug(
                f"[yellow]Skipping partial-subtitle{label} torrent as fallback: {candidate['hash']}"
            )
        return True

    def _preferred_piece_candidate(
        self,
        candidate: _PreparedBaseCandidate,
        current: _PieceMatch | None,
        extracted_torrent_dir: str,
    ) -> _PieceMatch | None:
        try:
            piece_size = int(
                Torrent.read(candidate["torrent_path"]).piece_size
            )
        except Exception as error:
            logger.info(
                f"[bold red]Error reading torrent data for {candidate['hash']}: {error}"
            )
            self._cleanup_exported_candidate(
                candidate["exported_path"], extracted_torrent_dir
            )
            return current
        if piece_size > 16 * 1024 * 1024:
            return current
        if current is not None and piece_size >= current["piece_size"]:
            return current
        best: _PieceMatch = {
            "hash": candidate["hash"],
            "torrent_path": candidate["torrent_path"],
            "piece_size": piece_size,
        }
        logger.debug(f"[green]Updated best match: {best}")
        return best

    async def _create_base_candidate(
        self,
        meta: Meta,
        candidate: _PreparedBaseCandidate | _SubtitleFallback | _PieceMatch,
        *,
        set_infohash: bool,
        log_label: str,
    ) -> bool:
        try:
            await TorrentCreator.create_base_from_existing_torrent(
                candidate["torrent_path"], meta.base_dir, meta.uuid
            )
        except Exception as error:
            logger.info(
                f"[bold red]Error creating BASE.torrent{log_label}: {error}"
            )
            return False
        torrent_hash = candidate["hash"]
        if set_infohash:
            meta.infohash = torrent_hash
        meta.base_torrent_created = True
        meta.hash_used = torrent_hash
        return True

    @staticmethod
    def _log_created_base_candidate(
        candidate: _PreparedBaseCandidate, is_alternative: bool
    ) -> None:
        if is_alternative:
            logger.debug(
                f"[green]Created BASE.torrent from alternative torrent {candidate['hash']}"
            )
            return
        logger.debug(
            f"[green]Created BASE.torrent from first valid torrent: {candidate['hash']}"
        )

    async def _create_immediate_base_candidate(
        self,
        meta: Meta,
        candidate: _PreparedBaseCandidate,
        state: _BaseSelectionState,
        is_alternative: bool,
    ) -> None:
        created = await self._create_base_candidate(
            meta,
            candidate,
            set_infohash=is_alternative,
            log_label=" for alternative" if is_alternative else "",
        )
        if not created:
            return
        state.found_valid_torrent = True
        self._log_created_base_candidate(candidate, is_alternative)

    async def _evaluate_base_candidate(
        self,
        meta: Meta,
        candidate: _PreparedBaseCandidate,
        state: _BaseSelectionState,
        extracted_torrent_dir: str,
        is_alternative: bool,
    ) -> None:
        if self._handle_subtitle_candidate(
            meta, candidate, state, is_alternative
        ):
            return
        if state.use_piece_preference:
            state.piece_size_best_match = self._preferred_piece_candidate(
                candidate,
                state.piece_size_best_match,
                extracted_torrent_dir,
            )
            return
        await self._create_immediate_base_candidate(
            meta, candidate, state, is_alternative
        )

    async def _apply_subtitle_fallback(
        self, meta: Meta, state: _BaseSelectionState
    ) -> None:
        fallback = state.subtitle_fallback
        if (
            fallback is None
            or state.found_valid_torrent
            or state.piece_size_best_match
        ):
            return
        created = await self._create_base_candidate(
            meta,
            fallback,
            set_infohash=True,
            log_label=" from video-only fallback",
        )
        if not created:
            return
        state.found_valid_torrent = True
        logger.info(
            f"[yellow]No torrent with all local subtitles found; using video-only fallback: {fallback['hash']}"
        )

    @staticmethod
    def _piece_best_ready(state: _BaseSelectionState) -> bool:
        return all(
            (
                state.use_piece_preference,
                state.piece_size_best_match is not None,
                not state.found_valid_torrent,
            )
        )

    @staticmethod
    def _log_piece_best_debug(meta: Meta, best: _PieceMatch) -> None:
        if not meta.debug:
            return
        piece_size_mib = best["piece_size"] / 1024 / 1024
        logger.debug(
            f"[green]Created BASE.torrent from best match torrent: {best['hash']} "
            f"(piece size: {piece_size_mib:.1f} MiB)"
        )

    async def _apply_piece_best_match(
        self, meta: Meta, state: _BaseSelectionState
    ) -> None:
        if not self._piece_best_ready(state):
            return
        best = cast(_PieceMatch, state.piece_size_best_match)
        logger.info(
            f"[green]Using best match torrent (16 MiB piece limit) with hash: {best['hash']}"
        )
        created = await self._create_base_candidate(
            meta,
            best,
            set_infohash=True,
            log_label=" from best match",
        )
        if not created:
            return
        state.found_valid_torrent = True
        meta.found_preferred_piece_size = "16MiB"
        self._log_piece_best_debug(meta, best)

    @staticmethod
    def _mark_checked_if_needed(
        meta: Meta, state: _BaseSelectionState
    ) -> None:
        if not state.found_valid_torrent:
            meta.we_checked_them_all = True

    @staticmethod
    def _mark_piece_preference_failure(
        meta: Meta, state: _BaseSelectionState
    ) -> None:
        if not state.use_piece_preference:
            return
        if state.piece_size_best_match is not None:
            return
        logger.info(
            "[yellow]No preferred torrents found matching piece size preferences."
        )
        meta.we_checked_them_all = True
        meta.found_preferred_piece_size = None

    @staticmethod
    def _mark_no_constraint_success(
        meta: Meta, state: _BaseSelectionState
    ) -> None:
        if state.use_piece_preference:
            return
        if state.found_valid_torrent:
            meta.found_preferred_piece_size = "no_constraints"

    async def _finalize_base_selection(
        self, meta: Meta, state: _BaseSelectionState
    ) -> None:
        await self._apply_subtitle_fallback(meta, state)
        self._mark_checked_if_needed(meta, state)
        await self._apply_piece_best_match(meta, state)
        self._mark_piece_preference_failure(meta, state)
        self._mark_no_constraint_success(meta, state)

    @staticmethod
    def _base_creation_needed(
        meta: Meta, matching_torrents: list[dict[str, Any]]
    ) -> bool:
        return bool(matching_torrents) and not meta.base_torrent_created

    @staticmethod
    def _base_loop_should_stop(state: _BaseSelectionState) -> bool:
        return state.found_valid_torrent and not state.use_piece_preference

    async def _process_base_match(
        self,
        meta: Meta,
        client_config: dict[str, Any],
        torrent_match: dict[str, Any],
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        torrent_storage_dir: str | None,
        extracted_torrent_dir: str,
        state: _BaseSelectionState,
        is_alternative: bool,
    ) -> None:
        candidate = await self._prepare_base_candidate(
            meta,
            client_config,
            str(torrent_match["hash"]),
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            torrent_storage_dir,
            extracted_torrent_dir,
            is_alternative=is_alternative,
        )
        if candidate is None:
            return
        await self._evaluate_base_candidate(
            meta,
            candidate,
            state,
            extracted_torrent_dir,
            is_alternative=is_alternative,
        )

    async def _process_base_torrent_creation(
        self,
        matching_torrents: list[dict[str, Any]],
        client_config: dict[str, Any],
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
        meta: Meta,
    ) -> None:
        if not self._base_creation_needed(meta, matching_torrents):
            return
        torrent_storage_dir, extracted_torrent_dir = (
            self._base_selection_directories(client_config, meta)
        )
        state = _BaseSelectionState(
            use_piece_preference=self._piece_preference_enabled()
        )
        for index, torrent_match in enumerate(matching_torrents):
            await self._process_base_match(
                meta,
                client_config,
                torrent_match,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
                torrent_storage_dir,
                extracted_torrent_dir,
                state,
                is_alternative=index > 0,
            )
            if self._base_loop_should_stop(state):
                break
        await self._finalize_base_selection(meta, state)

    async def _qbit_search_handles(
        self, client_config: dict[str, Any]
    ) -> tuple[
        str, str, httpx.AsyncClient | None, qbittorrentapi.Client | None
    ]:
        proxy_url = str(client_config.get("qui_proxy_url", "") or "").strip()
        if proxy_url:
            ssl_context = self.create_ssl_context_for_client(client_config)
            session = httpx.AsyncClient(timeout=10.0, verify=ssl_context)
            return proxy_url, proxy_url.rstrip("/"), session, None
        client = await self.init_qbittorrent_client(client_config)
        return "", "", None, client

    @staticmethod
    def _best_tracker_entries(
        best_match: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw = best_match.get("tracker_urls", [])
        if not isinstance(raw, list):
            return []
        return [
            cast(dict[str, Any], entry)
            for entry in cast(list[Any], raw)
            if isinstance(entry, dict)
        ]

    @staticmethod
    def _store_tracker_entry(meta: Meta, tracker: dict[str, Any]) -> None:
        tracker_id = tracker.get("id")
        value = tracker.get("tracker_id")
        if not tracker_id or not value:
            return
        key = str(tracker_id)
        meta[key] = value
        logger.debug(
            f"[bold cyan]Found {key.upper()} ID: {value} in torrent comment"
        )

    @classmethod
    def _store_best_tracker_ids(
        cls, meta: Meta, best_match: dict[str, Any]
    ) -> None:
        meta.infohash = best_match["hash"]
        if not best_match.get("has_tracker"):
            return
        for tracker in cls._best_tracker_entries(best_match):
            cls._store_tracker_entry(meta, tracker)

    @staticmethod
    def _log_qbit_search_summary(
        meta: Meta, matching_torrents: list[dict[str, Any]], client_name: str
    ) -> None:
        if not meta.debug:
            return
        if not matching_torrents:
            logger.debug(
                f"[yellow]No matching torrents found in {client_name}"
            )
            return
        working = sum(
            1
            for torrent in matching_torrents
            if torrent.get("has_working_tracker", False)
        )
        logger.debug(
            f"[green]Found {len(matching_torrents)} matching torrents in {client_name}"
        )
        logger.debug(f"[green]Torrents with working trackers: {working}")

    async def _search_qbit_matches(
        self,
        client_config: dict[str, Any],
        meta: Meta,
        tracker_patterns: dict[str, dict[str, str]],
        tracker_priority: list[str],
        proxy_url: str,
        qbt_proxy_url: str,
        qbt_session: httpx.AsyncClient | None,
        qbt_client: qbittorrentapi.Client | None,
    ) -> list[dict[str, Any]]:
        search_term = meta.uuid.replace("[", ".").replace("]", ".")
        torrents = await self._fetch_torrents(
            proxy_url, qbt_proxy_url, qbt_session, qbt_client, search_term
        )
        if not torrents:
            return []
        matches = await self._process_torrent_matches(
            torrents,
            tracker_patterns,
            tracker_priority,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            meta,
        )
        if not matches:
            return []
        self._sort_matching_torrents(matches, tracker_priority)
        self._store_best_tracker_ids(meta, matches[0])
        await self._process_base_torrent_creation(
            matches,
            client_config,
            proxy_url,
            qbt_proxy_url,
            qbt_session,
            qbt_client,
            meta,
        )
        return matches

    async def _safe_qbit_search_handles(
        self, client_config: dict[str, Any]
    ) -> (
        tuple[
            str,
            str,
            httpx.AsyncClient | None,
            qbittorrentapi.Client | None,
        ]
        | None
    ):
        try:
            return await self._qbit_search_handles(client_config)
        except Exception as error:
            logger.info(
                f"[bold red]Failed to connect to qBittorrent proxy: {error}"
            )
            return None

    @staticmethod
    async def _close_qbit_search_session(
        session: httpx.AsyncClient | None,
    ) -> None:
        if session is not None:
            await session.aclose()

    @staticmethod
    def _qbit_search_handles_usable(
        proxy_url: str, qbt_client: qbittorrentapi.Client | None
    ) -> bool:
        return bool(proxy_url or qbt_client is not None)

    async def _search_single_qbit_client_flow(
        self,
        client_config: dict[str, Any],
        meta: Meta,
        client_name: str,
    ) -> list[dict[str, Any]]:
        tracker_patterns, tracker_priority = self._setup_tracker_patterns()
        handles = await self._safe_qbit_search_handles(client_config)
        if handles is None:
            return []
        proxy_url, qbt_proxy_url, qbt_session, qbt_client = handles
        try:
            if not self._qbit_search_handles_usable(proxy_url, qbt_client):
                return []
            matches = await self._search_qbit_matches(
                client_config,
                meta,
                tracker_patterns,
                tracker_priority,
                proxy_url,
                qbt_proxy_url,
                qbt_session,
                qbt_client,
            )
            self._log_qbit_search_summary(meta, matches, client_name)
            return matches
        finally:
            await self._close_qbit_search_session(qbt_session)

    async def _search_single_qbit_client(
        self,
        client_config: dict[str, Any],
        _content_path: str,
        meta: Meta,
        client_name: str,
    ) -> list[dict[str, Any]]:
        """Search a single qBittorrent client for matching torrents."""
        try:
            return await self._search_single_qbit_client_flow(
                client_config, meta, client_name
            )
        except TimeoutError:
            raise
        except Exception as error:
            logger.info(
                f"[bold red]Error finding torrents in {client_name}: {error!s}"
            )
            logger.debug(traceback.format_exc())
            return []


_cached_tracker_url_patterns: dict[str, list[str]] | None = None


def _tracker_class_urls(tracker_class: Any) -> list[str]:
    urls = getattr(tracker_class, "tracker_urls", None)
    if not isinstance(urls, list):
        return []
    return [str(url) for url in cast(list[Any], urls) if url]


def _build_tracker_url_patterns() -> dict[str, list[str]]:
    from src.integrations.trackers.registry import tracker_class_map

    patterns = {
        name.lower(): urls
        for name, tracker_class in tracker_class_map.items()
        if (urls := _tracker_class_urls(tracker_class))
    }
    patterns.setdefault("btn", ["https://broadcasthe.net"])
    return patterns


def _tracker_url_patterns() -> dict[str, list[str]]:
    global _cached_tracker_url_patterns
    if _cached_tracker_url_patterns is None:
        _cached_tracker_url_patterns = _build_tracker_url_patterns()
    return _cached_tracker_url_patterns


def _warn_insecure_ptp_tracker(tracker_id: str, tracker_url: str) -> None:
    insecure = all(
        (
            tracker_id.upper() == "PASSTHEPOPCORN",
            "passthepopcorn.me" in tracker_url,
            tracker_url.startswith("http://"),
        )
    )
    if not insecure:
        return
    logger.info(
        "[red]Found PASSTHEPOPCORN announce URL using plaintext HTTP.\n"
    )
    logger.info(
        "[red]PASSTHEPOPCORN is turning off their plaintext HTTP tracker soon. You must update your announce URLS. See PASSTHEPOPCORN/forums.php?page=1&action=viewthread&threadid=46663"
    )


def _matched_tracker_ids(tracker_urls: list[str]) -> set[str]:
    found_ids: set[str] = set()
    for tracker_url in tracker_urls:
        for tracker_id, patterns in _tracker_url_patterns().items():
            if not any(pattern in tracker_url for pattern in patterns):
                continue
            normalized = tracker_id.upper()
            found_ids.add(normalized)
            logger.debug(
                f"[bold cyan]Matched {normalized} in tracker URL: {Redaction.redact_private_info(tracker_url)}"
            )
            _warn_insecure_ptp_tracker(tracker_id, tracker_url)
    return found_ids


def _tracker_removal_list(meta: Meta) -> list[str]:
    if "remove_trackers" not in meta or not isinstance(
        meta.remove_trackers, list
    ):
        meta.remove_trackers = []
    return cast(list[str], meta.remove_trackers)


async def match_tracker_url(tracker_urls: list[str], meta: Meta) -> None:
    remove_trackers = _tracker_removal_list(meta)
    for tracker_id in _matched_tracker_ids(tracker_urls):
        if tracker_id not in remove_trackers:
            remove_trackers.append(tracker_id)
    logger.debug(
        f"[bold cyan]Storing matched tracker IDs for later removal: {remove_trackers}"
    )


def _cross_seed_info(torrent: Torrent) -> dict[str, Any]:
    metainfo = getattr(torrent, "metainfo", {})
    if not isinstance(metainfo, dict):
        return {}
    info = cast(dict[str, Any], metainfo).get("info", {})
    return cast(dict[str, Any], info) if isinstance(info, dict) else {}


def _cross_seed_torrent_name(info: dict[str, Any], torrent: Torrent) -> str:
    value = (
        info.get("name.utf-8")
        or info.get("name")
        or getattr(torrent, "name", None)
    )
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value) if value else ""


def _decode_torrent_component(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _normalized_cross_seed_relative_path(raw_path: Any) -> str:
    if isinstance(raw_path, (list, tuple)):
        parts = cast(list[Any] | tuple[Any, ...], raw_path)
        components = [_decode_torrent_component(part) for part in parts]
        path = str(Path(*components)) if components else ""
    else:
        path = _decode_torrent_component(raw_path)
    path = path.replace("/", os.sep).replace("\\", os.sep)
    path = os.path.normpath(path)
    return path.lstrip(".\\/") if path.startswith("..") else path


def _torrent_file_length(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _multi_file_entry(value: Any) -> _TorrentFileEntry | None:
    if not isinstance(value, dict):
        return None
    item = cast(dict[str, Any], value)
    raw_path: Any = item.get("path.utf-8")
    if not raw_path:
        raw_path = item.get("path")
    if raw_path is None:
        raw_path = []
    return {
        "relative_path": _normalized_cross_seed_relative_path(raw_path),
        "length": _torrent_file_length(item.get("length")),
    }


def _multi_file_entries(info: dict[str, Any]) -> list[_TorrentFileEntry]:
    raw_files = info.get("files", [])
    if not isinstance(raw_files, list):
        return []
    entries = [_multi_file_entry(raw) for raw in cast(list[Any], raw_files)]
    return [entry for entry in entries if entry is not None]


def _cross_seed_torrent_files(
    info: dict[str, Any], torrent_name: str
) -> tuple[bool, list[_TorrentFileEntry]]:
    multi_file = bool(info.get("files"))
    if multi_file:
        return True, _multi_file_entries(info)
    return False, [
        {
            "relative_path": torrent_name,
            "length": _torrent_file_length(info.get("length")),
        }
    ]


def _cross_seed_destination_root(
    tracker_dir: str | Path, torrent_name: str, multi_file: bool
) -> Path | None:
    tracker_root = Path(tracker_dir).resolve()
    destination = (
        Path(tracker_dir) / torrent_name if multi_file else Path(tracker_dir)
    )
    if not is_path_under(destination.resolve(), tracker_root):
        logger.info(
            f"[bold red]Refusing to create link directory outside tracker directory: {destination}"
        )
        return None
    return destination


async def _ensure_cross_seed_destination(
    tracker_dir: str | Path, destination_root: Path, multi_file: bool
) -> None:
    target = destination_root if multi_file else Path(tracker_dir)
    await asyncio.to_thread(os.makedirs, target, exist_ok=True)


def _walk_cross_seed_files(root: str) -> list[str]:
    return [
        str(Path(directory) / filename)
        for directory, _dirs, files in os.walk(root)
        for filename in files
    ]


def _meta_cross_seed_filelist(meta: Meta) -> list[str]:
    raw = meta.filelist
    if isinstance(raw, list):
        return [str(path) for path in cast(list[Any], raw) if path]
    return [str(raw)] if raw else []


def _cross_seed_parent_guess(
    release_root: str | None, filelist: list[str]
) -> str:
    if filelist:
        return str(Path(filelist[0]).parent)
    return str(Path(release_root or "").parent)


def _directory_cross_seed_candidates(
    release_root: str | None,
) -> list[str] | None:
    if not release_root:
        return None
    if not Path(release_root).is_dir():
        return None
    return _walk_cross_seed_files(release_root)


def _cross_seed_candidate_paths(meta: Meta) -> list[str]:
    release_root = meta.path if isinstance(meta.path, str) else None
    directory_candidates = _directory_cross_seed_candidates(release_root)
    if directory_candidates is not None:
        return directory_candidates
    filelist = _meta_cross_seed_filelist(meta)
    paths = list(filelist)
    parent_guess = _cross_seed_parent_guess(release_root, filelist)
    if parent_guess and Path(parent_guess).is_dir():
        paths.extend(_walk_cross_seed_files(parent_guess))
    return paths


def _candidate_is_tracker_file(candidate: str, tracker_root: str) -> bool:
    try:
        return os.path.commonpath([candidate, tracker_root]) == tracker_root
    except ValueError:
        return False


def _candidate_file_size(candidate: str) -> int | None:
    try:
        return Path(candidate).stat().st_size
    except OSError:
        return None


def _candidate_entry(candidate: str) -> _CandidateEntry:
    return {
        "path": candidate,
        "name": Path(candidate).name.lower(),
        "size": _candidate_file_size(candidate),
        "used": False,
    }


def _cross_seed_candidates(
    candidate_paths: list[str], tracker_dir: str | Path
) -> list[_CandidateEntry]:
    tracker_root = str(Path(tracker_dir).resolve())
    seen: set[str] = set()
    candidates: list[_CandidateEntry] = []
    for candidate in candidate_paths:
        resolved = str(Path(candidate).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        if not Path(resolved).is_file():
            continue
        if _candidate_is_tracker_file(resolved, tracker_root):
            continue
        candidates.append(_candidate_entry(resolved))
    return candidates


def _cross_seed_file_destination(
    tracker_dir: str | Path,
    torrent_name: str,
    relative_path: str,
    multi_file: bool,
) -> str | None:
    destination = (
        Path(tracker_dir) / torrent_name / relative_path
        if multi_file
        else Path(tracker_dir) / torrent_name
    )
    normalized = os.path.normpath(destination)
    tracker_root = str(Path(tracker_dir).resolve())
    try:
        inside = (
            os.path.commonpath([tracker_root, str(Path(normalized).resolve())])
            == tracker_root
        )
    except ValueError:
        inside = False
    if not inside:
        logger.info(
            f"[bold red]Refusing to create link outside tracker directory: {normalized}"
        )
        return None
    return normalized


async def _link_cross_seed_torrent_file(
    tracker_dir: str | Path,
    torrent_name: str,
    torrent_file: _TorrentFileEntry,
    multi_file: bool,
    candidates: list[_CandidateEntry],
    use_hardlink: bool,
) -> bool:
    relative_path = torrent_file["relative_path"]
    destination = _cross_seed_file_destination(
        tracker_dir, torrent_name, relative_path, multi_file
    )
    if destination is None:
        return False
    source_file, match_reason = _pick_candidate(
        candidates,
        Path(relative_path).name,
        torrent_file.get("length"),
    )
    if not source_file:
        logger.info(
            f"[bold red]Failed to map cross-seed file: {relative_path}"
        )
        return False
    if match_reason == "fallback":
        logger.debug(
            f"[yellow]Cross-seed mapping fallback used for: {relative_path}"
        )
    await asyncio.to_thread(
        os.makedirs, str(Path(destination).parent), exist_ok=True
    )
    linked = await async_link_directory(
        source_file, destination, use_hardlink=use_hardlink
    )
    if linked:
        return True
    logger.info(
        f"[bold red]Linking failed for cross-seed file: {relative_path}"
    )
    return False


async def _prepare_cross_seed_context(
    meta: Meta, torrent: Torrent, tracker_dir: str | Path
) -> _CrossSeedContext | None:
    info = _cross_seed_info(torrent)
    torrent_name = _cross_seed_torrent_name(info, torrent)
    if not torrent_name:
        logger.info(
            "[bold red]Cross-seed torrent is missing an info name; cannot build link structure"
        )
        return None
    multi_file, torrent_files = _cross_seed_torrent_files(info, torrent_name)
    destination_root = _cross_seed_destination_root(
        tracker_dir, torrent_name, multi_file
    )
    if destination_root is None:
        return None
    await _ensure_cross_seed_destination(
        tracker_dir, destination_root, multi_file
    )
    candidates = _cross_seed_candidates(
        _cross_seed_candidate_paths(meta), tracker_dir
    )
    if not candidates:
        logger.info(
            "[bold red]Unable to find source files for cross-seed linking"
        )
        return None
    return _CrossSeedContext(
        torrent_name=torrent_name,
        multi_file=multi_file,
        torrent_files=torrent_files,
        destination_root=destination_root,
        candidates=candidates,
    )


async def _link_all_cross_seed_files(
    context: _CrossSeedContext,
    tracker_dir: str | Path,
    use_hardlink: bool,
) -> bool:
    for torrent_file in context.torrent_files:
        linked = await _link_cross_seed_torrent_file(
            tracker_dir,
            context.torrent_name,
            torrent_file,
            context.multi_file,
            context.candidates,
            use_hardlink,
        )
        if not linked:
            return False
    return True


async def create_cross_seed_links(
    meta: Meta, torrent: Torrent, tracker_dir: str | Path, use_hardlink: bool
) -> bool:
    context = await _prepare_cross_seed_context(meta, torrent, tracker_dir)
    if context is None:
        return False
    if not await _link_all_cross_seed_files(
        context, tracker_dir, use_hardlink
    ):
        return False
    prepared_root = (
        context.destination_root if context.multi_file else Path(tracker_dir)
    )
    logger.debug(f"[green]Prepared cross-seed link tree at {prepared_root}")
    return True


async def _existing_link_matches(src: str, dst: str | Path) -> bool:
    if not await asyncio.to_thread(os.path.lexists, dst):
        return False
    try:
        return bool(await asyncio.to_thread(os.path.samefile, src, dst))
    except OSError:
        return False


def _file_hardlink(src: str, dst: str | Path) -> bool:
    try:
        os.link(src, dst)
    except OSError as error:
        logger.info(f"[yellow]Hard link failed: {error}")
        return False
    logger.debug(f"[green]Hard link created: {dst} -> {src}")
    return True


def _file_symlink(src: str, dst: str | Path) -> bool:
    try:
        # target_is_directory must remain explicit for Windows-compatible callers.
        os.symlink(src, dst, target_is_directory=False)  # noqa: PTH211
    except OSError as error:
        logger.info(f"[yellow]Symlink failed: {error}")
        return False
    logger.debug(f"[green]Symbolic link created: {dst} -> {src}")
    return True


async def _link_single_file(
    src: str, dst: str | Path, use_hardlink: bool, destination_exists: bool
) -> bool:
    if destination_exists:
        logger.info(
            f"[yellow]Link destination contains different content: {dst}"
        )
        return False
    linker = _file_hardlink if use_hardlink else _file_symlink
    return await asyncio.to_thread(linker, src, dst)


def _collect_link_files(src: str, dst: str) -> list[tuple[str, str, str]]:
    items: list[tuple[str, str, str]] = []
    for root, _dirs, files in os.walk(src):
        for file in files:
            src_path = Path(root) / file
            rel_path = os.path.relpath(src_path, src)
            items.append((str(src_path), str(Path(dst) / rel_path), rel_path))
    return items


async def _ensure_link_subdirectories(
    items: list[tuple[str, str, str]],
) -> None:
    subdirs = {str(Path(dst_path).parent) for _src, dst_path, _rel in items}
    for subdir in filter(None, subdirs):
        await asyncio.to_thread(os.makedirs, subdir, exist_ok=True)


def _hardlink_destination_state(src_path: str, dst_path: str) -> bool | None:
    if not os.path.lexists(dst_path):
        return None
    try:
        return Path(src_path).samefile(dst_path)
    except OSError:
        return False


def _log_first_tree_hardlink(
    src_path: str, dst_path: str, rel_path: str, first_rel_path: str
) -> None:
    if rel_path == first_rel_path:
        logger.debug(
            f"[green]Hard link created for file: {dst_path} -> {src_path}"
        )


def _hardlink_tree_item(
    src_path: str, dst_path: str, rel_path: str, first_rel_path: str
) -> bool:
    try:
        destination_state = _hardlink_destination_state(src_path, dst_path)
        if destination_state is True:
            return True
        if destination_state is False:
            logger.info(
                f"[yellow]Hard link destination contains different content: {dst_path}"
            )
            return False
        os.link(src_path, dst_path)
        _log_first_tree_hardlink(src_path, dst_path, rel_path, first_rel_path)
        return True
    except OSError as error:
        logger.info(f"[yellow]Hard link failed for file {rel_path}: {error}")
        return False


async def _hardlink_directory(src: str, dst: str | Path) -> bool:
    await asyncio.to_thread(os.makedirs, dst, exist_ok=True)
    items = await asyncio.to_thread(_collect_link_files, src, str(dst))
    await _ensure_link_subdirectories(items)
    first_rel = os.path.relpath(items[0][0], src) if items else ""
    for src_path, dst_path, rel_path in items:
        linked = await asyncio.to_thread(
            _hardlink_tree_item,
            src_path,
            dst_path,
            rel_path,
            first_rel,
        )
        if not linked:
            return False
    return True


def _directory_symlink(src: str, dst: str | Path) -> bool:
    try:
        # target_is_directory must remain explicit for Windows-compatible callers.
        os.symlink(src, dst, target_is_directory=True)  # noqa: PTH211
    except OSError as error:
        logger.info(f"[yellow]Symlink failed: {error}")
        return False
    logger.debug(f"[green]Symbolic link created: {dst} -> {src}")
    return True


async def _directory_destination_usable(
    dst: str | Path, destination_exists: bool
) -> bool:
    if not destination_exists:
        return True
    is_directory = await asyncio.to_thread(os.path.isdir, dst)
    is_link = await asyncio.to_thread(os.path.islink, dst)
    if is_directory and not is_link:
        return True
    logger.info(
        f"[yellow]Directory link destination contains different content: {dst}"
    )
    return False


async def _link_directory(
    src: str, dst: str | Path, use_hardlink: bool, destination_exists: bool
) -> bool:
    if not await _directory_destination_usable(dst, destination_exists):
        return False
    if use_hardlink:
        return await _hardlink_directory(src, dst)
    return await asyncio.to_thread(_directory_symlink, src, dst)


async def async_link_directory(
    src: str, dst: str | Path, use_hardlink: bool = True
) -> bool:
    try:
        await asyncio.to_thread(
            os.makedirs, str(Path(dst).parent), exist_ok=True
        )
        destination_exists = await asyncio.to_thread(os.path.lexists, dst)
        if destination_exists and await _existing_link_matches(src, dst):
            logger.debug(
                f"[green]Existing link already points to source: {dst}"
            )
            return True
        if await asyncio.to_thread(os.path.isfile, src):
            return await _link_single_file(
                src, dst, use_hardlink, destination_exists
            )
        return await _link_directory(
            src, dst, use_hardlink, destination_exists
        )
    except Exception as error:
        logger.info(f"[bold red]Error during linking: {error}")
        return False
