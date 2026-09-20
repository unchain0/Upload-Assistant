# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import os
import re
import shutil
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import bencodepy
import defusedxml.xmlrpc
import httpx
import qbittorrentapi
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.torrent.torrent_creator import SUBTITLE_EXTENSIONS
from src.integrations.torrent_clients import (
    DelugeClientMixin,
    QbittorrentClientMixin,
    RtorrentClientMixin,
    TransmissionClientMixin,
)
from src.integrations.torrent_clients.path_utils import (
    coerce_str_list,
    is_path_under,
)

# Secure XML-RPC client using defusedxml to prevent XML attacks
defusedxml.xmlrpc.monkey_patch()


def _decode_bencode(value: bytes) -> Any:
    decoder = cast(Callable[[bytes], Any], vars(bencodepy)["decode"])
    return decoder(value)


def _encode_bencode(value: Any) -> bytes:
    encoder = cast(Callable[[Any], bytes], vars(bencodepy)["encode"])
    return encoder(value)


@dataclass
class _TorrentSearchState:
    piece_limit: bool
    best_match: dict[str, Any] | None = None
    video_only_fallback: tuple[str, str] | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> _TorrentSearchState:
        piece_limit = bool(
            config["DEFAULT"].get("prefer_max_16_torrent", False)
        )
        return cls(piece_limit=piece_limit)


ClientDispatcher = Callable[
    [Meta, str, Torrent, str, str, dict[str, Any], str, bool], Awaitable[None]
]


class Clients(
    QbittorrentClientMixin,
    RtorrentClientMixin,
    DelugeClientMixin,
    TransmissionClientMixin,
):
    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize torrent-client operations with the application config."""
        self.config = config
        self._tracker_comment_hosts: dict[str, tuple[str, ...]] | None = None

    @staticmethod
    def _legacy_torrent_info(metainfo: Any) -> dict[bytes, Any] | None:
        if not isinstance(metainfo, dict):
            return None
        metainfo_map = cast(dict[bytes, Any], metainfo)
        info = metainfo_map.get(b"info")
        if not isinstance(info, dict):
            return None
        return cast(dict[bytes, Any], info)

    @staticmethod
    def _legacy_torrent_entries(
        info: dict[bytes, Any],
    ) -> list[dict[bytes, Any]]:
        entries = [info]
        files = info.get(b"files", [])
        if not isinstance(files, list):
            return entries
        entries.extend(
            cast(dict[bytes, Any], entry)
            for entry in cast(list[Any], files)
            if isinstance(entry, dict)
        )
        return entries

    @classmethod
    def _normalize_legacy_md5(cls, info: dict[bytes, Any]) -> bool:
        changed = False
        for entry in cls._legacy_torrent_entries(info):
            md5sum = entry.get(b"md5sum")
            if isinstance(md5sum, bytes):
                entry[b"md5sum"] = md5sum.hex().encode("ascii")
                changed = True
        return changed

    @classmethod
    def _legacy_compatible_torrent(
        cls,
        torrent_path: str,
        normalized_path: Path,
        original_error: Exception,
    ) -> tuple[Torrent, str]:
        metainfo = cast(
            dict[bytes, Any], _decode_bencode(Path(torrent_path).read_bytes())
        )
        info = cls._legacy_torrent_info(metainfo)
        if info is None or not cls._normalize_legacy_md5(info):
            raise original_error
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_bytes(_encode_bencode(metainfo))
        logger.info(
            f"[yellow]Normalized legacy binary md5sum metadata in a working copy: {normalized_path}[/yellow]"
        )
        return Torrent.read(normalized_path), str(normalized_path)

    @classmethod
    def _read_torrent_compat(
        cls, torrent_path: str, normalized_path: Path
    ) -> tuple[Torrent, str]:
        try:
            return Torrent.read(torrent_path), torrent_path
        except Exception as original_error:
            return cls._legacy_compatible_torrent(
                torrent_path, normalized_path, original_error
            )

    @staticmethod
    def _matches_tracker_host(
        host: str, tracker_hosts: dict[str, tuple[str, ...]]
    ) -> str | None:
        for tracker_name, domains in tracker_hosts.items():
            if any(
                host == domain or host.endswith(f".{domain}")
                for domain in domains
            ):
                return tracker_name
        return None

    def _get_tracker_comment_hosts(self) -> dict[str, tuple[str, ...]]:
        if self._tracker_comment_hosts is None:
            from src.integrations.trackers.registry import (
                get_tracker_comment_hosts,
            )

            self._tracker_comment_hosts = get_tracker_comment_hosts(
                self.config
            )
        return self._tracker_comment_hosts

    @staticmethod
    def _last_path_id(path: str) -> str | None:
        match = re.search(r"/(\d+)$", path)
        return match.group(1) if match else None

    @staticmethod
    def _query_id(query: str, key: str) -> str | None:
        values = urllib.parse.parse_qs(query).get(key)
        return values[0] if values else None

    @staticmethod
    def _tracker_key(tracker_name: str) -> str:
        aliases = {
            "PASSTHEPOPCORN": "ptp",
            "HDBITS": "hdb",
            "BEYONDHD": "bhd",
            "BLUTOPIA": "blu",
            "ONLYENCODES": "oe",
            "BTN": "btn",
        }
        return aliases.get(tracker_name, tracker_name.lower())

    @staticmethod
    def _tracker_query_key(tracker_name: str) -> str | None:
        return {
            "PASSTHEPOPCORN": "torrentid",
            "HDBITS": "id",
            "BTN": "id",
            "ORPHEUS": "torrentid",
        }.get(tracker_name)

    @staticmethod
    def _beyondhd_path_id(path: str) -> str | None:
        match = re.search(r"/details/(\d+)", path)
        return match.group(1) if match else None

    @classmethod
    def _tracker_id_from_url(
        cls, tracker_name: str, parsed: urllib.parse.ParseResult
    ) -> str | None:
        query_key = cls._tracker_query_key(tracker_name)
        if query_key is not None:
            return cls._query_id(parsed.query, query_key)
        if tracker_name in {"BeyondHD", "BEYONDHD"}:
            return cls._beyondhd_path_id(parsed.path)
        return cls._last_path_id(parsed.path)

    def _comment_tracker_id_pair(
        self, url: str, tracker_hosts: dict[str, tuple[str, ...]]
    ) -> tuple[str, str] | None:
        parsed = urllib.parse.urlparse(url)
        tracker_name = self._matches_tracker_host(
            (parsed.hostname or "").lower(), tracker_hosts
        )
        if tracker_name is None:
            return None
        tracker_id = self._tracker_id_from_url(tracker_name, parsed)
        if not tracker_id:
            return None
        return self._tracker_key(tracker_name), tracker_id

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, str]:
        """Extract known tracker IDs from a torrent comment URL set."""
        if not comment:
            return {}
        tracker_ids: dict[str, str] = {}
        tracker_hosts = self._get_tracker_comment_hosts()
        for url in re.findall(r"https?://[^\s\"'<>]+", comment):
            pair = self._comment_tracker_id_pair(url, tracker_hosts)
            if pair is not None:
                key, tracker_id = pair
                tracker_ids[key] = tracker_id
        return tracker_ids

    @staticmethod
    def _prepared_torrent_path(meta: Meta, tracker: str, cross: bool) -> str:
        state_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        if cross:
            return str(state_dir / f"[{tracker}_cross].torrent")
        if meta.debug:
            return str(state_dir / f"[{tracker}_DEBUG].torrent")
        return str(state_dir / f"[{tracker}].torrent")

    @staticmethod
    def _torrent_for_injection(torrent_path: str) -> Torrent | None:
        if Path(torrent_path).exists():
            return Torrent.read(torrent_path)
        logger.info(
            f"[bold red]Torrent file {torrent_path} does not exist, cannot add to client"
        )
        return None

    @staticmethod
    def _clean_injecting_client_list(value: list[Any]) -> list[str]:
        clients: list[str] = []
        for client in value:
            normalized = str(client).strip()
            if normalized:
                clients.append(normalized)
        return clients

    @classmethod
    def _injecting_clients_from_value(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return cls._clean_injecting_client_list(cast(list[Any], value))
        return []

    def _configured_injecting_clients(self) -> list[str]:
        try:
            value = self.config["DEFAULT"].get("injecting_client_list")
        except Exception as exc:
            logger.debug(
                f"[cyan]DEBUG: Error reading injecting_client_list from config: {exc}[/cyan]"
            )
            return []
        clients = self._injecting_clients_from_value(value)
        if isinstance(value, str) and clients:
            logger.debug(
                f"[cyan]DEBUG: Converted injecting_client_list string to list: {clients}[/cyan]"
            )
        elif isinstance(value, list):
            logger.debug(
                f"[cyan]DEBUG: Using injecting_client_list from config: {clients}[/cyan]"
            )
        return clients

    def _default_injecting_client(self) -> list[str]:
        default_client = self.config["DEFAULT"].get("default_torrent_client")
        if not isinstance(default_client, str) or default_client == "none":
            return []
        logger.debug(
            f"[cyan]DEBUG: Falling back to default_torrent_client: {default_client}[/cyan]"
        )
        return [default_client]

    def _injecting_clients(self, meta: Meta) -> list[str]:
        client_value = meta.client
        if isinstance(client_value, str):
            if client_value == "none":
                logger.debug(
                    "[cyan]DEBUG: meta client is 'none', skipping adding to client[/cyan]"
                )
                return []
            clients = [client_value]
            logger.debug(
                f"[cyan]DEBUG: Using client from meta: {clients}[/cyan]"
            )
            return clients
        configured = self._configured_injecting_clients()
        return configured or self._default_injecting_client()

    def _client_is_skipped(self, tracker: str, client_name: str) -> bool:
        client_to_skip = self.config["TRACKERS"][tracker].get(
            "client_to_skip", []
        )
        if client_name not in client_to_skip:
            return False
        logger.debug(
            f"[cyan]DEBUG: Skipping client '{client_name}' for tracker '{tracker}' as it's in client_to_skip list[/cyan]"
        )
        return True

    def _torrent_client_config(
        self, client_name: str
    ) -> dict[str, Any] | None:
        clients = self.config["TORRENT_CLIENTS"]
        if client_name in clients:
            return cast(dict[str, Any], clients[client_name])
        logger.info(
            f"[bold red]Torrent client '{client_name}' not found in config."
        )
        return None

    async def _dispatch_rtorrent(
        self,
        meta: Meta,
        torrent_path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        tracker: str,
        _cross: bool,
    ) -> None:
        self.rtorrent(
            cast(str, meta.path),
            torrent_path,
            torrent,
            meta,
            local_path,
            remote_path,
            client,
            tracker,
        )

    async def _dispatch_qbit(
        self,
        meta: Meta,
        _torrent_path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        tracker: str,
        cross: bool,
    ) -> None:
        await self.qbittorrent(
            cast(str, meta.path),
            torrent,
            local_path,
            remote_path,
            client,
            meta.is_disc,
            meta.filelist,
            meta,
            tracker,
            cross,
        )

    async def _dispatch_deluge(
        self,
        meta: Meta,
        torrent_path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        _tracker: str,
        _cross: bool,
    ) -> None:
        self.deluge(
            cast(str, meta.path),
            torrent_path,
            torrent,
            local_path,
            remote_path,
            client,
        )

    async def _dispatch_transmission(
        self,
        meta: Meta,
        _torrent_path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        _tracker: str,
        _cross: bool,
    ) -> None:
        self.transmission(
            cast(str, meta.path),
            torrent,
            local_path,
            remote_path,
            client,
            meta,
        )

    async def _dispatch_watch(
        self,
        _meta: Meta,
        torrent_path: str,
        _torrent: Torrent,
        _local_path: str,
        _remote_path: str,
        client: dict[str, Any],
        _tracker: str,
        _cross: bool,
    ) -> None:
        shutil.copy(torrent_path, client["watch_folder"])

    async def _dispatch_to_client(
        self,
        torrent_client: str,
        meta: Meta,
        torrent_path: str,
        torrent: Torrent,
        local_path: str,
        remote_path: str,
        client: dict[str, Any],
        tracker: str,
        cross: bool,
    ) -> None:
        dispatchers: dict[str, ClientDispatcher] = {
            "rtorrent": self._dispatch_rtorrent,
            "qbit": self._dispatch_qbit,
            "deluge": self._dispatch_deluge,
            "transmission": self._dispatch_transmission,
            "watch": self._dispatch_watch,
        }
        dispatcher = dispatchers.get(torrent_client.lower())
        if dispatcher is not None:
            await dispatcher(
                meta,
                torrent_path,
                torrent,
                local_path,
                remote_path,
                client,
                tracker,
                cross,
            )

    def _ignore_inject_client(self, tracker: str, client_name: str) -> bool:
        if not client_name or client_name == "none":
            return True
        return self._client_is_skipped(tracker, client_name)

    async def _inject_into_client(
        self,
        meta: Meta,
        tracker: str,
        cross: bool,
        torrent_path: str,
        torrent: Torrent,
        client_name: str,
    ) -> None:
        if self._ignore_inject_client(tracker, client_name):
            return
        client = self._torrent_client_config(client_name)
        if client is None:
            return
        torrent_client = str(client["torrent_client"])
        await self.inject_delay(meta, tracker, client_name)
        local_path, remote_path = await self.remote_path_map(meta, client_name)
        logger.debug(f"[bold green]Adding to {client_name} ({torrent_client})")
        try:
            await self._dispatch_to_client(
                torrent_client,
                meta,
                torrent_path,
                torrent,
                local_path,
                remote_path,
                client,
                tracker,
                cross,
            )
        except Exception as exc:
            logger.info(
                f"[bold red]Failed to add torrent to {client_name}: {exc}"
            )

    def _prepared_injection(
        self, meta: Meta, tracker: str, cross: bool
    ) -> tuple[str, Torrent] | None:
        if meta.path is None:
            logger.info("[bold red]meta.path is None, cannot add to client")
            return None
        if meta.no_seed is True:
            logger.info(
                "[bold red]--no-seed was passed, so the torrent will not be added to the client"
            )
            logger.info("[bold yellow]Add torrent manually to the client")
            return None
        torrent_path = self._prepared_torrent_path(meta, tracker, cross)
        torrent = self._torrent_for_injection(torrent_path)
        if torrent is None:
            return None
        return torrent_path, torrent

    async def add_to_client(
        self, meta: Meta, tracker: str, cross: bool = False
    ) -> None:
        """Add the prepared torrent to each configured client."""
        prepared = self._prepared_injection(meta, tracker, cross)
        if prepared is None:
            return
        torrent_path, torrent = prepared
        inject_clients = self._injecting_clients(meta)
        if not inject_clients:
            logger.debug(
                "[cyan]DEBUG: No clients configured for injecting[/cyan]"
            )
            return
        logger.debug(
            f"[cyan]DEBUG: Clients to inject into: {inject_clients}[/cyan]"
        )
        for client_name in inject_clients:
            await self._inject_into_client(
                meta, tracker, cross, torrent_path, torrent, client_name
            )

    def _inject_delay_value(self, tracker: str) -> tuple[Any, bool]:
        trackers = self.config.get("TRACKERS", {})
        tracker_map = (
            cast(dict[str, Any], trackers)
            if isinstance(trackers, dict)
            else {}
        )
        tracker_cfg = tracker_map.get(tracker, {})
        tracker_config = (
            cast(dict[str, Any], tracker_cfg)
            if isinstance(tracker_cfg, dict)
            else {}
        )
        has_tracker_delay = "inject_delay" in tracker_config
        if has_tracker_delay:
            return tracker_config.get("inject_delay"), True
        return self.config["DEFAULT"].get("inject_delay", 0), False

    @staticmethod
    def _empty_inject_delay(value: Any) -> bool:
        if value is None:
            return True
        return isinstance(value, str) and not value.strip()

    @staticmethod
    def _parsed_inject_delay(value: Any) -> int | None:
        try:
            return int(value)
        except ValueError, TypeError:
            return None

    @staticmethod
    def _log_invalid_inject_delay(
        tracker: str, tracker_specific: bool
    ) -> None:
        prefix = f"{tracker}: " if tracker_specific else ""
        logger.info(
            f"{prefix}[bold red]CONFIG ERROR: 'inject_delay' must be an integer"
        )

    @staticmethod
    def _nonnegative_inject_delay(value: int) -> int:
        if value >= 0:
            return value
        logger.info("[bold red]CONFIG ERROR: 'inject_delay' must be >= 0")
        return 0

    @staticmethod
    def _log_inject_delay(
        meta: Meta,
        tracker: str,
        client_name: str,
        delay: int,
        tracker_specific: bool,
    ) -> None:
        if not meta.debug and delay <= 5:
            return
        prefix = f"{tracker}: " if tracker_specific else ""
        logger.info(
            f"{prefix}[cyan]Waiting {delay} seconds before adding to client '{client_name}'[/cyan]"
        )

    async def inject_delay(
        self, meta: Meta, tracker: str, client_name: str
    ) -> None:
        """Apply the configured tracker/global delay before client injection."""
        raw_delay, tracker_specific = self._inject_delay_value(tracker)
        if self._empty_inject_delay(raw_delay):
            return
        parsed = self._parsed_inject_delay(raw_delay)
        if parsed is None:
            self._log_invalid_inject_delay(tracker, tracker_specific)
            return
        delay = self._nonnegative_inject_delay(parsed)
        if delay <= 0:
            return
        self._log_inject_delay(
            meta, tracker, client_name, delay, tracker_specific
        )
        await asyncio.sleep(delay)

    async def find_existing_torrent(self, meta: Meta) -> str | None:
        """Find a reusable torrent once and cache the search result."""
        if meta.reuse_torrent_search_completed:
            return meta.reuse_torrent_path
        result = await self._find_existing_torrent_uncached(meta)
        meta.reuse_torrent_search_completed = True
        meta.reuse_torrent_path = result
        self._log_rejected_reuse_fallback(meta, result)
        return result

    async def _find_existing_torrent_uncached(self, meta: Meta) -> str | None:
        if meta.get("skip_auto_torrent", False):
            return None
        state = _TorrentSearchState.from_config(self.config)
        for client_name in self._clients_to_search(meta):
            result = await self._search_configured_client(
                meta, client_name, state
            )
            selected = self._apply_search_result(
                meta, client_name, result, state
            )
            if selected is not None:
                return selected
        return self._final_search_result(meta, state)

    def _clients_to_search(self, meta: Meta) -> list[str]:
        meta_client = self._meta_search_client(meta)
        if meta_client is not None:
            logger.debug(
                f"[cyan]DEBUG: Using client from meta: {[meta_client]}[/cyan]"
            )
            return [meta_client]
        configured = self._configured_search_clients()
        if configured:
            logger.debug(
                "[cyan]DEBUG: Using searching_client_list from config: "
                f"{configured}[/cyan]"
            )
            return configured
        return self._default_search_clients()

    @staticmethod
    def _meta_search_client(meta: Meta) -> str | None:
        value = meta.client
        return value if isinstance(value, str) and value != "none" else None

    def _configured_search_clients(self) -> list[str]:
        value = self.config["DEFAULT"].get("searching_client_list", [])
        values = cast(list[object], value) if isinstance(value, list) else []
        return [
            str(client)
            for client in values
            if str(client) and str(client) != "none"
        ]

    def _default_search_clients(self) -> list[str]:
        value = self.config["DEFAULT"].get("default_torrent_client")
        client = str(value) if value else ""
        if client and client != "none":
            logger.debug(
                "[cyan]DEBUG: Falling back to default_torrent_client: "
                f"{client}[/cyan]"
            )
            return [client]
        logger.info("[yellow]No clients configured for searching...[/yellow]")
        return []

    async def _search_configured_client(
        self,
        meta: Meta,
        client_name: str,
        state: _TorrentSearchState,
    ) -> dict[str, Any] | str | None:
        if client_name not in self.config["TORRENT_CLIENTS"]:
            logger.info(
                f"[yellow]Client '{client_name}' not found in TORRENT_CLIENTS "
                "config, skipping..."
            )
            return None
        return await self._search_single_client_for_torrent(
            meta,
            client_name,
            state.piece_limit,
            state.piece_limit,
            state.best_match,
        )

    def _apply_search_result(
        self,
        meta: Meta,
        client_name: str,
        result: dict[str, Any] | str | None,
        state: _TorrentSearchState,
    ) -> str | None:
        if result is None:
            return None
        candidate_path = self._candidate_path(result)
        if self._needs_video_only_fallback(meta, candidate_path):
            self._store_video_only_fallback(candidate_path, client_name, state)
            return None
        if isinstance(result, dict):
            return self._apply_best_match_result(
                meta, client_name, result, state
            )
        meta.reuse_torrent_client = client_name
        logger.debug(
            "[green]Found valid torrent with preferred piece size in client "
            f"'{client_name}', stopping search[/green]"
        )
        return result

    @staticmethod
    def _candidate_path(result: dict[str, Any] | str) -> str | None:
        value = (
            result.get("torrent_path") if isinstance(result, dict) else result
        )
        return value if isinstance(value, str) else None

    def _needs_video_only_fallback(
        self, meta: Meta, candidate_path: str | None
    ) -> bool:
        return bool(
            meta.subtitle_files
            and candidate_path
            and not self._torrent_includes_all_local_subtitles(
                candidate_path, meta
            )
        )

    def _store_video_only_fallback(
        self,
        candidate_path: str | None,
        client_name: str,
        state: _TorrentSearchState,
    ) -> None:
        if candidate_path is None or not self._torrent_has_no_subtitles(
            candidate_path
        ):
            return
        current = state.video_only_fallback
        if self._prefer_video_only_candidate(candidate_path, current, state):
            state.video_only_fallback = (candidate_path, client_name)

    def _prefer_video_only_candidate(
        self,
        candidate_path: str,
        current: tuple[str, str] | None,
        state: _TorrentSearchState,
    ) -> bool:
        if current is None or not state.piece_limit:
            return True
        return self._is_preferred_piece_size_candidate(
            candidate_path, current[0], state.piece_limit
        )

    def _apply_best_match_result(
        self,
        meta: Meta,
        client_name: str,
        result: dict[str, Any],
        state: _TorrentSearchState,
    ) -> str | None:
        state.best_match = {**result, "client_name": client_name}
        if state.piece_limit:
            return None
        logger.info(
            f"[green]Found valid torrent in client '{client_name}', "
            "stopping search[/green]"
        )
        meta.reuse_torrent_client = client_name
        return self._best_match_path(state.best_match)

    def _final_search_result(
        self, meta: Meta, state: _TorrentSearchState
    ) -> str | None:
        if state.piece_limit and state.best_match:
            return self._use_best_match(meta, state.best_match)
        if state.video_only_fallback:
            return self._use_video_only_fallback(
                meta, state.video_only_fallback
            )
        logger.debug(
            "[yellow]No reusable .torrent found; a fresh torrent will be "
            "created."
        )
        return None

    @staticmethod
    def _best_match_path(best_match: dict[str, Any]) -> str | None:
        value = best_match.get("torrent_path")
        return value if isinstance(value, str) else None

    def _use_best_match(
        self, meta: Meta, best_match: dict[str, Any]
    ) -> str | None:
        logger.info(
            "[yellow]Using best match torrent with hash: [bold yellow]"
            f"{best_match['torrenthash']}[/bold yellow]"
        )
        meta.reuse_torrent_client = cast(
            str | None, best_match.get("client_name")
        )
        return self._best_match_path(best_match)

    @staticmethod
    def _use_video_only_fallback(meta: Meta, fallback: tuple[str, str]) -> str:
        logger.info(
            "[yellow]No matching torrent with all local subtitles found; "
            "using the video-only fallback.[/yellow]"
        )
        meta.reuse_torrent_client = fallback[1]
        return fallback[0]

    @staticmethod
    def _log_rejected_reuse_fallback(meta: Meta, result: str | None) -> None:
        if result is not None or meta.rejected_reuse_piece_length is None:
            return
        logger.warning(
            "[yellow]The reusable torrent was rejected. Creating a fresh "
            "BASE.torrent in the background; hashing large releases can take "
            "a while.[/yellow]"
        )

    async def _search_single_client_for_torrent(
        self,
        meta: Meta,
        client_name: str,
        prefer_small_pieces: bool,
        piece_limit: bool,
        best_match: dict[str, Any] | None,
    ) -> dict[str, Any] | str | None:
        """Search one configured client and return a validated reusable torrent."""
        client = cast(
            dict[str, Any], self.config["TORRENT_CLIENTS"][client_name]
        )
        torrent_client = str(client.get("torrent_client", "")).lower()
        storage_dir = self._torrent_storage_dir(client)
        preset = await self._find_prespecified_hash_torrent(
            meta, client, torrent_client, storage_dir
        )
        if preset is not None:
            return preset
        if not self._qbit_search_enabled(client, torrent_client):
            return best_match
        found_hash, qbt_client = await self._search_qbit_hash(meta, client)
        if not found_hash:
            return best_match
        candidate = await self._prepare_qbit_candidate(
            meta, client, storage_dir, found_hash, qbt_client
        )
        if candidate is None:
            return best_match
        return await self._validated_qbit_search_result(
            meta,
            client,
            candidate,
            found_hash,
            prefer_small_pieces,
            piece_limit,
            best_match,
        )

    @staticmethod
    def _torrent_storage_dir(client: dict[str, Any]) -> str | None:
        value = client.get("torrent_storage_dir")
        return str(value) if value else None

    @staticmethod
    def _qbit_search_enabled(
        client: dict[str, Any], torrent_client: str
    ) -> bool:
        return torrent_client == "qbit" and bool(client.get("enable_search"))

    @staticmethod
    def _prespecified_hashes(meta: Meta) -> tuple[str, ...]:
        values = (meta.get("torrenthash"), meta.get("ext_torrenthash"))
        return tuple(dict.fromkeys(str(value) for value in values if value))

    @staticmethod
    def _piece_length_from_file(torrent_path: str) -> int | None:
        try:
            metainfo = cast(
                dict[bytes, Any],
                _decode_bencode(Path(torrent_path).read_bytes()),
            )
        except OSError, bencodepy.BencodeDecodeError, TypeError, ValueError:
            return None
        info = metainfo.get(b"info")
        if not isinstance(info, dict):
            return None
        info_map = cast(dict[bytes, Any], info)
        piece_length = info_map.get(b"piece length")
        return piece_length if isinstance(piece_length, int) else None

    @staticmethod
    def _unsupported_piece_length(torrent_path: str) -> int | None:
        piece_length = Clients._piece_length_from_file(torrent_path)
        if piece_length is None:
            return None
        supported = (
            16 * 1024 <= piece_length <= 16 * 1024 * 1024
            and piece_length % (16 * 1024) == 0
        )
        return None if supported else piece_length

    async def _validate_prespecified_candidate(
        self,
        meta: Meta,
        client: dict[str, Any],
        torrent_client: str,
        torrent_hash: str,
        candidate: str,
    ) -> str | None:
        piece_length = self._unsupported_piece_length(candidate)
        if piece_length is not None:
            meta.rejected_reuse_torrent_path = candidate
            meta.rejected_reuse_piece_length = piece_length
            logger.warning(
                "[yellow]Ignoring reusable .torrent with unsupported piece length "
                f"{piece_length}: {candidate}. A fresh torrent will be created instead.[/yellow]"
            )
            return None
        valid, resolved_path = await self.is_valid_torrent(
            meta, candidate, torrent_hash, torrent_client, client
        )
        return resolved_path if valid else None

    async def _find_prespecified_hash_torrent(
        self,
        meta: Meta,
        client: dict[str, Any],
        torrent_client: str,
        storage_dir: str | None,
    ) -> str | None:
        for torrent_hash in self._prespecified_hashes(meta):
            candidate = await self._prepare_hash_candidate(
                meta, client, torrent_client, storage_dir, torrent_hash
            )
            if candidate is None:
                continue
            resolved_path = await self._validate_prespecified_candidate(
                meta,
                client,
                torrent_client,
                torrent_hash,
                candidate,
            )
            if resolved_path is not None:
                return resolved_path
        return None

    async def _prepare_hash_candidate(
        self,
        meta: Meta,
        client: dict[str, Any],
        torrent_client: str,
        storage_dir: str | None,
        torrent_hash: str,
    ) -> str | None:
        existing = self._existing_torrent_candidate(
            meta, storage_dir, torrent_hash
        )
        if existing is not None:
            return existing
        if storage_dir:
            return str(Path(storage_dir) / f"{torrent_hash}.torrent")
        if torrent_client != "qbit":
            return None
        return await self._export_qbit_torrent(
            meta, client, torrent_hash, qbt_client=None
        )

    @classmethod
    def _existing_torrent_candidate(
        cls, meta: Meta, storage_dir: str | None, torrent_hash: str
    ) -> str | None:
        for path in cls._candidate_torrent_paths(
            meta, storage_dir, torrent_hash
        ):
            if path.exists():
                return str(path)
        return None

    @staticmethod
    def _candidate_torrent_paths(
        meta: Meta, storage_dir: str | None, torrent_hash: str
    ) -> tuple[Path, ...]:
        extracted = (
            Path(meta.base_dir) / "tmp" / meta.uuid / f"{torrent_hash}.torrent"
        )
        if storage_dir:
            return Path(storage_dir) / f"{torrent_hash}.torrent", extracted
        return (extracted,)

    async def _search_qbit_hash(
        self,
        meta: Meta,
        client: dict[str, Any],
    ) -> tuple[str | None, qbittorrentapi.Client | None]:
        qbt_client, qbt_session, proxy_url = await self._qbit_search_resources(
            client
        )
        try:
            return await self._run_qbit_search(
                meta, client, qbt_client, qbt_session, proxy_url
            )
        finally:
            if qbt_session is not None:
                await qbt_session.aclose()

    async def _qbit_search_resources(
        self,
        client: dict[str, Any],
    ) -> tuple[
        qbittorrentapi.Client | None, httpx.AsyncClient | None, str | None
    ]:
        proxy_url = self._proxy_url(client)
        if proxy_url:
            session = httpx.AsyncClient(
                timeout=10.0, verify=self.create_ssl_context_for_client(client)
            )
            return None, session, proxy_url
        return await self.init_qbittorrent_client(client), None, None

    async def _run_qbit_search(
        self,
        meta: Meta,
        client: dict[str, Any],
        qbt_client: qbittorrentapi.Client | None,
        qbt_session: httpx.AsyncClient | None,
        proxy_url: str | None,
    ) -> tuple[str | None, qbittorrentapi.Client | None]:
        try:
            found_hash = await self.search_qbit_for_torrent(
                meta, client, qbt_client, qbt_session, proxy_url
            )
            return found_hash, qbt_client
        except KeyboardInterrupt:
            logger.info("[bold red]Search cancelled by user")
            raise
        except TimeoutError:
            raise
        except Exception as error:
            logger.info(f"[bold red]Error searching qBittorrent: {error}")
            return None, qbt_client

    @staticmethod
    def _proxy_url(client: dict[str, Any]) -> str | None:
        value = client.get("qui_proxy_url")
        return str(value) if value else None

    async def _prepare_qbit_candidate(
        self,
        meta: Meta,
        client: dict[str, Any],
        storage_dir: str | None,
        found_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str | None:
        existing = self._existing_torrent_candidate(
            meta, storage_dir, found_hash
        )
        if existing is not None:
            logger.debug(
                f"[cyan]DEBUG: .torrent file already exists at {existing}[/cyan]"
            )
            return existing
        logger.info(
            f"[yellow]Exporting .torrent file from qBittorrent for hash: {found_hash}[/yellow]"
        )
        return await self._export_qbit_torrent(
            meta, client, found_hash, qbt_client=qbt_client
        )

    async def _export_qbit_torrent(
        self,
        meta: Meta,
        client: dict[str, Any],
        torrent_hash: str,
        *,
        qbt_client: qbittorrentapi.Client | None,
    ) -> str | None:
        try:
            content = await self._qbit_export_content(
                client, torrent_hash, qbt_client
            )
        except (TimeoutError, qbittorrentapi.APIError) as error:
            logger.error(f"[red]Error exporting torrent: {error}")
            return None
        except Exception as error:
            logger.error(
                f"[bold red]Unexpected error fetching .torrent from qBittorrent: {error}"
            )
            return None
        if not content:
            logger.info(
                f"[bold red]qBittorrent returned an empty response for hash {torrent_hash}"
            )
            return None
        target = (
            Path(meta.base_dir) / "tmp" / meta.uuid / f"{torrent_hash}.torrent"
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, content)
        except (OSError, TypeError, ValueError) as error:
            logger.error(
                f"[bold red]Unexpected error saving exported .torrent: {error}"
            )
            return None
        logger.info(f"[green]Successfully saved .torrent file: {target}")
        return str(target)

    async def _qbit_export_content(
        self,
        client: dict[str, Any],
        torrent_hash: str,
        qbt_client: qbittorrentapi.Client | None,
    ) -> bytes | None:
        proxy_url = self._proxy_url(client)
        if proxy_url:
            return await self._proxy_export_content(proxy_url, torrent_hash)
        active_client = qbt_client or await self.init_qbittorrent_client(
            client
        )
        if active_client is None:
            return None
        return cast(
            bytes | None,
            await self.retry_qbt_operation(
                lambda: asyncio.to_thread(
                    active_client.torrents_export, torrent_hash=torrent_hash
                ),
                f"Export torrent {torrent_hash}",
            ),
        )

    @staticmethod
    async def _proxy_export_content(
        proxy_url: str, torrent_hash: str
    ) -> bytes | None:
        async with httpx.AsyncClient() as session:
            response = await session.post(
                f"{proxy_url.rstrip('/')}/api/v2/torrents/export",
                data={"hash": torrent_hash},
            )
        if response.status_code == 200:
            return response.content
        logger.error(
            f"[red]Failed to export torrent via proxy: {response.status_code}"
        )
        return None

    async def _validated_qbit_search_result(
        self,
        meta: Meta,
        client: dict[str, Any],
        candidate: str,
        found_hash: str,
        prefer_small_pieces: bool,
        piece_limit: bool,
        best_match: dict[str, Any] | None,
    ) -> dict[str, Any] | str | None:
        valid, resolved_path = await self.is_valid_torrent(
            meta, candidate, found_hash, "qbit", client
        )
        if not valid:
            return best_match
        piece_size = Torrent.read(resolved_path).piece_size
        if not prefer_small_pieces:
            logger.debug(
                f"[green]Found a valid torrent from client search with piece size {piece_size / 1024 / 1024} MiB: [bold yellow]{found_hash}"
            )
            return resolved_path
        if piece_limit and piece_size < 16 * 1024 * 1024:
            logger.info(
                f"[green]Found a valid torrent with piece size under 16 MiB from client search: [bold yellow]{found_hash}"
            )
            return resolved_path
        return self._better_piece_match(
            best_match, found_hash, resolved_path, piece_size
        )

    @staticmethod
    def _better_piece_match(
        best_match: dict[str, Any] | None,
        torrent_hash: str,
        torrent_path: str,
        piece_size: int,
    ) -> dict[str, Any]:
        if best_match is None or piece_size < int(best_match["piece_size"]):
            logger.info(
                f"[yellow]Storing valid torrent from client search as best match: [bold yellow]{torrent_hash}"
            )
            return {
                "torrenthash": torrent_hash,
                "torrent_path": torrent_path,
                "piece_size": piece_size,
            }
        return best_match

    @staticmethod
    def _normalized_reuse_candidate(
        torrent_path: str,
        torrenthash: str,
        torrent_client: str,
    ) -> tuple[str, str]:
        path = str(torrent_path)
        if torrent_client in ("qbit", "deluge"):
            normalized_hash = torrenthash.lower().strip()
            return path.replace(
                normalized_hash.upper(), normalized_hash
            ), normalized_hash
        if torrent_client == "rtorrent":
            normalized_hash = torrenthash.upper().strip()
            return path.replace(
                normalized_hash.upper(), normalized_hash
            ), normalized_hash
        return path, torrenthash

    @staticmethod
    def _relative_layout(paths: list[str]) -> list[str]:
        root = Path(os.path.commonpath(paths))
        return sorted(
            str(Path(path).relative_to(root)).replace("\\", "/")
            for path in paths
        )

    @staticmethod
    def _single_file_layout_matches(
        torrent: Torrent, candidate: list[str]
    ) -> bool:
        if len(torrent.files) != 1 or len(candidate) != 1:
            return False
        info = cast(dict[str, Any], torrent.metainfo["info"])
        return bool(
            Path(str(torrent.files[0])).name == Path(candidate[0]).name
            and "length" in info
        )

    @classmethod
    def _multi_file_layout_matches(
        cls, torrent: Torrent, candidate: list[str]
    ) -> bool:
        if len(torrent.files) != len(candidate):
            return False
        torrent_layout = cls._relative_layout(
            [str(file) for file in torrent.files]
        )
        candidate_layout = cls._relative_layout(
            [str(file) for file in candidate]
        )
        logger.debug(f"Torrent layout: {torrent_layout}")
        logger.debug(f"Candidate layout: {candidate_layout}")
        return torrent_layout == candidate_layout

    @classmethod
    def _candidate_layout_matches(
        cls, torrent: Torrent, candidate: list[str]
    ) -> bool:
        if len(torrent.files) == len(candidate) == 1:
            return cls._single_file_layout_matches(torrent, candidate)
        return cls._multi_file_layout_matches(torrent, candidate)

    @staticmethod
    def _reuse_layout_candidates(meta: Meta) -> list[list[str]]:
        filelist = [str(path) for path in cast(list[str], meta.filelist)]
        candidates = [filelist]
        if meta.subtitle_files:
            candidates.append(
                filelist + [str(path) for path in meta.subtitle_files]
            )
        return candidates

    @staticmethod
    def _folder_layout_matches(meta: Meta, torrent: Torrent) -> bool:
        meta_path = meta.path
        if meta_path is None:
            return False
        torrent_path = os.path.commonpath(
            [str(path) for path in torrent.files]
        )
        return Path(meta_path).name in torrent_path

    @staticmethod
    def _uses_folder_layout(meta: Meta) -> bool:
        has_disc = bool(meta.is_disc and meta.is_disc != "")
        return has_disc or bool(meta.keep_folder and meta.isdir)

    @classmethod
    def _matches_any_reuse_layout(cls, meta: Meta, torrent: Torrent) -> bool:
        for candidate in cls._reuse_layout_candidates(meta):
            if cls._candidate_layout_matches(torrent, candidate):
                return True
        return False

    @classmethod
    def _torrent_layout_matches(cls, meta: Meta, torrent: Torrent) -> bool:
        if not cls._uses_folder_layout(meta):
            return cls._matches_any_reuse_layout(meta, torrent)
        valid = cls._folder_layout_matches(meta, torrent)
        logger.debug(
            f"Torrent is valid based on disc/basename or keep-folder: {valid}"
        )
        return valid

    @staticmethod
    def _reuse_verify_path(meta: Meta) -> str | None:
        meta_path = meta.path
        if meta_path is None:
            return None
        filelist = [str(path) for path in cast(list[str], meta.filelist)]
        if len(filelist) == 1 and Path(filelist[0]).is_file():
            return filelist[0]
        return str(meta_path)

    @classmethod
    async def _verify_reuse_content(cls, meta: Meta, torrent: Torrent) -> bool:
        verify_path = cls._reuse_verify_path(meta)
        if verify_path is None:
            return False
        try:
            return bool(
                await asyncio.to_thread(torrent.verify, verify_path, threads=1)
            )
        except Exception as error:
            logger.debug(f"Torrent content verification failed: {error}")
            return False

    def _load_reuse_torrent(
        self, meta: Meta, torrent_path: str
    ) -> tuple[Torrent | None, str]:
        unsupported_piece_length = self._unsupported_piece_length(torrent_path)
        if unsupported_piece_length is not None:
            meta.rejected_reuse_torrent_path = torrent_path
            meta.rejected_reuse_piece_length = unsupported_piece_length
            logger.warning(
                "[yellow]Ignoring reusable .torrent with unsupported piece length "
                f"{unsupported_piece_length}: {torrent_path}. A fresh torrent "
                "will be created instead.[/yellow]"
            )
            return None, torrent_path
        normalized_path = (
            Path(meta.base_dir) / "tmp" / meta.uuid / Path(torrent_path).name
        )
        try:
            return self._read_torrent_compat(torrent_path, normalized_path)
        except Exception as error:
            logger.info(f"[bold red]Error reading torrent file: {error}")
            return None, torrent_path

    @staticmethod
    def _fails_four_mib_piece_policy(meta: Meta, torrent: Torrent) -> bool:
        max_piece_size = meta.max_piece_size
        size_policy = max_piece_size is None or max_piece_size >= 4
        return bool(
            torrent.pieces >= 5000
            and torrent.piece_size < 4294304
            and size_policy
        )

    @staticmethod
    def _fails_eight_mib_piece_policy(meta: Meta, torrent: Torrent) -> bool:
        max_piece_size = meta.max_piece_size
        size_policy = max_piece_size is None or max_piece_size >= 8
        return bool(
            torrent.pieces >= 8000
            and torrent.piece_size < 8488608
            and size_policy
            and not meta.prefer_small_pieces
        )

    @staticmethod
    def _fails_piece_count_policy(meta: Meta, torrent: Torrent) -> bool:
        return bool(meta.max_piece_size is None and torrent.pieces >= 12000)

    @staticmethod
    def _fails_minimum_piece_size(torrent: Torrent) -> bool:
        return torrent.piece_size < 32768

    @staticmethod
    def _fails_torrent_file_size(meta: Meta, size_kib: float) -> bool:
        return bool(meta.max_piece_size is None and size_kib > 250)

    @classmethod
    def _piece_policy_reason(
        cls,
        meta: Meta,
        torrent: Torrent,
        torrent_file_size_kib: float,
    ) -> str | None:
        checks = (
            (
                cls._fails_four_mib_piece_policy(meta, torrent),
                "Torrent needs to have less than 5000 pieces with a 4 MiB piece size",
            ),
            (
                cls._fails_eight_mib_piece_policy(meta, torrent),
                "Torrent needs to have less than 8000 pieces with a 8 MiB piece size",
            ),
            (
                cls._fails_piece_count_policy(meta, torrent),
                "Torrent needs to have less than 12000 pieces to be valid",
            ),
            (
                cls._fails_minimum_piece_size(torrent),
                "Piece size too small to reuse",
            ),
            (
                cls._fails_torrent_file_size(meta, torrent_file_size_kib),
                "Torrent file size exceeds 250 KiB",
            ),
        )
        for failed, reason in checks:
            if failed:
                return reason
        return None

    @classmethod
    def _piece_policy_allows(
        cls,
        meta: Meta,
        torrent: Torrent,
        torrent_path: str,
        torrenthash: str,
    ) -> bool:
        try:
            torrent_file_size_kib = round(
                Path(torrent_path).stat().st_size / 1024, 2
            )
            logger.debug(
                "Checking piece size, count and size: "
                f"pieces={torrent.pieces}, "
                f"piece_size={torrent.piece_size / 1024 / 1024} MiB, "
                f".torrent size={torrent_file_size_kib} KiB"
            )
            reason = cls._piece_policy_reason(
                meta, torrent, torrent_file_size_kib
            )
            if reason is not None:
                logger.debug(f"[bold red]{reason}")
                return False
            logger.debug(
                "[bold green]REUSING .torrent with infohash: "
                f"[bold yellow]{torrenthash}"
            )
            return True
        except Exception as error:
            logger.info(f"[bold red]Error checking reuse torrent: {error}")
            return False

    @staticmethod
    def _log_normalized_reuse_path(meta: Meta, torrent_path: str) -> None:
        if meta.debug:
            logger.debug(f"Torrent path after normalization: {torrent_path}")

    @staticmethod
    def _reuse_candidate_available(meta: Meta, torrent_path: str) -> bool:
        if meta.path is None:
            return False
        if Path(torrent_path).exists():
            return True
        logger.debug(
            f"No reusable torrent found at {torrent_path}; a fresh torrent "
            "can be created if needed"
        )
        return False

    @staticmethod
    def _log_invalid_reuse_layout(meta: Meta) -> None:
        if meta.debug:
            logger.debug("[bold yellow]Unwanted Files/Folders Identified")

    @classmethod
    async def _loaded_reuse_is_valid(
        cls,
        meta: Meta,
        torrent: Torrent,
        torrent_path: str,
        torrenthash: str,
    ) -> bool:
        if not cls._torrent_layout_matches(meta, torrent):
            cls._log_invalid_reuse_layout(meta)
            return False
        if not await cls._verify_reuse_content(meta, torrent):
            logger.info(
                "[yellow]Existing torrent matches the file layout but not the "
                "current content; forcing a fresh torrent hash[/yellow]"
            )
            return False
        valid = cls._piece_policy_allows(
            meta, torrent, torrent_path, torrenthash
        )
        if meta.debug:
            logger.debug(f"Final validity after piece checks: valid={valid}")
        return valid

    async def is_valid_torrent(
        self,
        meta: Meta,
        torrent_path: str,
        torrenthash: str,
        torrent_client: str,
        client: dict[str, Any],
    ) -> tuple[bool, str]:
        """Validate a candidate torrent against files, layout, and piece limits."""
        del client
        torrent_path, torrenthash = self._normalized_reuse_candidate(
            torrent_path, torrenthash, torrent_client
        )
        self._log_normalized_reuse_path(meta, torrent_path)
        if not self._reuse_candidate_available(meta, torrent_path):
            return False, torrent_path
        torrent, torrent_path = self._load_reuse_torrent(meta, torrent_path)
        if torrent is None:
            return False, torrent_path
        valid = await self._loaded_reuse_is_valid(
            meta, torrent, torrent_path, torrenthash
        )
        return valid, torrent_path

    @staticmethod
    def _torrent_includes_all_local_subtitles(
        torrent_path: str, meta: Meta
    ) -> bool:
        """Whether a validated torrent includes every subtitle selected locally."""
        if not meta.subtitle_files:
            return True
        try:
            torrent = Torrent.read(torrent_path)
        except Exception:
            return False

        torrent_names = {
            Path(str(path)).name.casefold() for path in torrent.files
        }
        subtitle_names = {
            Path(str(path)).name.casefold() for path in meta.subtitle_files
        }
        return subtitle_names.issubset(torrent_names)

    @staticmethod
    def _torrent_has_no_subtitles(torrent_path: str) -> bool:
        """Whether a torrent contains no external subtitle files."""
        try:
            torrent = Torrent.read(torrent_path)
        except Exception:
            return False
        return not any(
            Path(str(path)).suffix.casefold() in SUBTITLE_EXTENSIONS
            for path in torrent.files
        )

    @staticmethod
    def _is_preferred_piece_size_candidate(
        candidate_path: str, current_path: str, piece_limit: bool
    ) -> bool:
        """Whether a candidate outranks the current fallback by configured piece preference."""
        try:
            candidate_piece_size = Torrent.read(candidate_path).piece_size
            current_piece_size = Torrent.read(current_path).piece_size
        except Exception:
            return False

        if piece_limit:
            limit = 16 * 1024 * 1024
            candidate_within_limit = candidate_piece_size <= limit
            current_within_limit = current_piece_size <= limit
            return candidate_within_limit and (
                not current_within_limit
                or candidate_piece_size < current_piece_size
            )
        return False

    def _remote_path_client_config(
        self, torrent_client_name: str | dict[str, Any] | None
    ) -> dict[str, Any]:
        if isinstance(torrent_client_name, dict):
            return torrent_client_name
        if not isinstance(torrent_client_name, str) or not torrent_client_name:
            raise ValueError(
                "torrent_client_name must be a client name or client config dict"
            )
        try:
            return cast(
                dict[str, Any],
                self.config["TORRENT_CLIENTS"][torrent_client_name],
            )
        except KeyError as exc:
            raise KeyError(
                f"Torrent client '{torrent_client_name}' not found in TORRENT_CLIENTS"
            ) from exc

    @staticmethod
    def _path_roots(
        client_config: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        local_paths = coerce_str_list(
            client_config.get("local_path", ["/LocalPath"])
        ) or ["/LocalPath"]
        remote_paths = coerce_str_list(
            client_config.get("remote_path", ["/RemotePath"])
        ) or ["/RemotePath"]
        return local_paths, remote_paths

    @staticmethod
    def _remote_root_for_index(remote_paths: list[str], index: int) -> str:
        if index < len(remote_paths):
            return remote_paths[index]
        return remote_paths[0]

    @classmethod
    def _matching_path_roots(
        cls, meta_path: str, local_paths: list[str], remote_paths: list[str]
    ) -> tuple[str, str]:
        for index, local_path in enumerate(local_paths):
            if is_path_under(meta_path, local_path):
                return local_path, cls._remote_root_for_index(
                    remote_paths, index
                )
        return local_paths[0], remote_paths[0]

    @staticmethod
    def _normalized_path_roots(
        local_root: str, remote_root: str
    ) -> tuple[str, str]:
        local_path = os.path.normpath(local_root)
        remote_path = os.path.normpath(remote_root)
        if local_path.endswith(os.sep):
            remote_path = remote_path + os.sep
        return local_path, remote_path

    async def remote_path_map(
        self,
        meta: Meta,
        torrent_client_name: str | dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Return the local and remote roots matching the torrent metadata path."""
        client_config = self._remote_path_client_config(torrent_client_name)
        local_paths, remote_paths = self._path_roots(client_config)
        roots = self._matching_path_roots(
            str(meta.path), local_paths, remote_paths
        )
        return self._normalized_path_roots(*roots)

    def _ptp_client_name(self, client_name: str | None) -> str | None:
        if client_name:
            return client_name
        default_config = self.config.get("DEFAULT", {})
        if not isinstance(default_config, dict):
            return None
        value = cast(dict[str, Any], default_config).get(
            "default_torrent_client"
        )
        return value if isinstance(value, str) and value else None

    def _ptp_client_config(self, client_name: str) -> dict[str, Any] | None:
        clients_config = self.config.get("TORRENT_CLIENTS", {})
        client = (
            cast(dict[str, Any], clients_config).get(client_name)
            if isinstance(clients_config, dict)
            else None
        )
        if isinstance(client, dict):
            return cast(dict[str, Any], client)
        logger.debug(
            f"[yellow]Skipping torrent metadata lookup: client '{client_name}' is not configured.[/yellow]"
        )
        return None

    async def _ptp_from_rtorrent(
        self, meta: Meta, client: dict[str, Any], pathed: bool
    ) -> Meta:
        await self.get_ptp_from_hash_rtorrent(meta, pathed, client)
        return meta

    async def _ptp_from_qbit(
        self, meta: Meta, client: dict[str, Any], pathed: bool
    ) -> Meta:
        return await self.get_ptp_from_hash_qbit(meta, client, pathed)

    async def _ptp_from_client(
        self, meta: Meta, client: dict[str, Any], pathed: bool
    ) -> Meta:
        dispatchers = {
            "rtorrent": self._ptp_from_rtorrent,
            "qbit": self._ptp_from_qbit,
        }
        dispatcher = dispatchers.get(str(client.get("torrent_client", "")))
        if dispatcher is None:
            return meta
        return await dispatcher(meta, client, pathed)

    async def get_ptp_from_hash(
        self, meta: Meta, pathed: bool = False, client_name: str | None = None
    ) -> Meta:
        """Fetch PTP metadata through the configured torrent client when available."""
        resolved_name = self._ptp_client_name(client_name)
        if resolved_name is None:
            logger.debug(
                "[yellow]Skipping torrent metadata lookup: no default torrent client configured.[/yellow]"
            )
            return meta
        client = self._ptp_client_config(resolved_name)
        if client is None:
            return meta
        return await self._ptp_from_client(meta, client, pathed)
