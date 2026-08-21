# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import os
import re
import shutil
import urllib.parse
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
    def _read_torrent_compat(
        torrent_path: str, normalized_path: Path
    ) -> tuple[Torrent, str]:
        try:
            return Torrent.read(torrent_path), torrent_path
        except Exception as original_error:
            metainfo = bencodepy.decode(Path(torrent_path).read_bytes())
            if not isinstance(metainfo, dict) or not isinstance(
                metainfo.get(b"info"), dict
            ):
                raise original_error

            info = metainfo[b"info"]
            entries = [info]
            files = info.get(b"files", [])
            if isinstance(files, list):
                entries.extend(
                    entry for entry in files if isinstance(entry, dict)
                )

            changed = False
            for entry in entries:
                md5sum = entry.get(b"md5sum")
                if isinstance(md5sum, bytes):
                    entry[b"md5sum"] = md5sum.hex().encode("ascii")
                    changed = True

            if not changed:
                raise original_error

            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            normalized_path.write_bytes(bencodepy.encode(metainfo))
            logger.info(
                f"[yellow]Normalized legacy binary md5sum metadata in a working copy: {normalized_path}[/yellow]"
            )
            return Torrent.read(normalized_path), str(normalized_path)

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

    def _extract_tracker_ids_from_comment(
        self, comment: str
    ) -> dict[str, str]:
        """Extract known tracker IDs from a torrent comment URL set."""
        if not comment:
            return {}

        def _last_path_id(path: str) -> str | None:
            """Extract a numeric tracker ID from the end of a URL path."""
            match = re.search(r"/(\d+)$", path)
            return match.group(1) if match else None

        def _query_id(query: str, key: str) -> str | None:
            """Extract the first value for key from a URL query string."""
            values = urllib.parse.parse_qs(query).get(key)
            return values[0] if values else None

        tracker_ids: dict[str, str] = {}
        urls: list[str] = re.findall(r"https?://[^\s\"'<>]+", comment)
        tracker_hosts = self._get_tracker_comment_hosts()
        for url in urls:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path

            matched_tracker = self._matches_tracker_host(host, tracker_hosts)

            if not matched_tracker:
                continue

            # Canonical-class-name → established metadata key mapping
            _tracker_key_aliases: dict[str, str] = {
                "PASSTHEPOPCORN": "ptp",
                "HDBITS": "hdb",
                "BEYONDHD": "bhd",
                "BLUTOPIA": "blu",
                "ONLYENCODES": "oe",
                "BTN": "btn",
            }
            tracker_key = _tracker_key_aliases.get(
                matched_tracker, matched_tracker.lower()
            )

            if matched_tracker == "PASSTHEPOPCORN":
                ptp_id = _query_id(parsed.query, "torrentid")
                if ptp_id:
                    tracker_ids[tracker_key] = ptp_id
            elif matched_tracker == "HDBITS":
                hdb_id = _query_id(parsed.query, "id")
                if hdb_id:
                    tracker_ids[tracker_key] = hdb_id
            elif matched_tracker == "BTN":
                btn_id = _query_id(parsed.query, "id")
                if btn_id:
                    tracker_ids[tracker_key] = btn_id
            elif matched_tracker in {"BeyondHD", "BEYONDHD"}:
                match = re.search(r"/details/(\d+)", path)
                if match:
                    tracker_ids[tracker_key] = match.group(1)
            elif matched_tracker == "ORPHEUS":
                torrent_id = _query_id(parsed.query, "torrentid")
                if torrent_id:
                    tracker_ids[tracker_key] = torrent_id
            else:
                # UNIT3D style: last path ID
                tracker_id = _last_path_id(path)
                if tracker_id:
                    tracker_ids[tracker_key] = tracker_id

        return tracker_ids

    async def add_to_client(
        self, meta: Meta, tracker: str, cross: bool = False
    ) -> None:
        """Add the prepared torrent to each configured client."""
        if meta.path is None:
            logger.info("[bold red]meta.path is None, cannot add to client")
            return
        if cross:
            torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}_cross].torrent"
        elif meta.debug:
            torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}_DEBUG].torrent"
        else:
            torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}].torrent"
        if meta.no_seed is True:
            logger.info(
                "[bold red]--no-seed was passed, so the torrent will not be added to the client"
            )
            logger.info("[bold yellow]Add torrent manually to the client")
            return
        if Path(torrent_path).exists():
            torrent = Torrent.read(torrent_path)
        else:
            logger.info(
                f"[bold red]Torrent file {torrent_path} does not exist, cannot add to client"
            )
            return

        inject_clients: list[str] = []
        client_value = meta.client
        if isinstance(client_value, str) and client_value != "none":
            inject_clients = [client_value]
            logger.debug(
                f"[cyan]DEBUG: Using client from meta: {inject_clients}[/cyan]"
            )
        elif client_value == "none":
            logger.debug(
                "[cyan]DEBUG: meta client is 'none', skipping adding to client[/cyan]"
            )
            return
        else:
            try:
                inject_clients_config = self.config["DEFAULT"].get(
                    "injecting_client_list"
                )
                if (
                    isinstance(inject_clients_config, str)
                    and inject_clients_config.strip()
                ):
                    inject_clients = [inject_clients_config]
                    logger.debug(
                        f"[cyan]DEBUG: Converted injecting_client_list string to list: {inject_clients}[/cyan]"
                    )
                elif isinstance(inject_clients_config, list):
                    # Filter out empty strings and whitespace-only strings
                    inject_clients_list = cast(
                        list[Any], inject_clients_config
                    )
                    inject_clients = [
                        str(c).strip()
                        for c in inject_clients_list
                        if str(c).strip()
                    ]
                    logger.debug(
                        f"[cyan]DEBUG: Using injecting_client_list from config: {inject_clients}[/cyan]"
                    )
                else:
                    inject_clients = []
            except Exception as e:
                logger.debug(
                    f"[cyan]DEBUG: Error reading injecting_client_list from config: {e}[/cyan]"
                )

            if not inject_clients:
                default_client = self.config["DEFAULT"].get(
                    "default_torrent_client"
                )
                if (
                    isinstance(default_client, str)
                    and default_client != "none"
                ):
                    logger.debug(
                        f"[cyan]DEBUG: Falling back to default_torrent_client: {default_client}[/cyan]"
                    )
                    inject_clients = [default_client]

        if not inject_clients:
            logger.debug(
                "[cyan]DEBUG: No clients configured for injecting[/cyan]"
            )
            return

        logger.debug(
            f"[cyan]DEBUG: Clients to inject into: {inject_clients}[/cyan]"
        )

        for client_name in inject_clients:
            client_to_skip = self.config["TRACKERS"][tracker].get(
                "client_to_skip", []
            )
            if client_name in client_to_skip:
                logger.debug(
                    f"[cyan]DEBUG: Skipping client '{client_name}' for tracker '{tracker}' as it's in client_to_skip list[/cyan]"
                )
                continue
            if client_name == "none" or not client_name:
                continue

            if client_name not in self.config["TORRENT_CLIENTS"]:
                logger.info(
                    f"[bold red]Torrent client '{client_name}' not found in config."
                )
                continue

            client = self.config["TORRENT_CLIENTS"][client_name]
            torrent_client = client["torrent_client"]
            await self.inject_delay(meta, tracker, client_name)

            # Must pass client_name to remote_path_map
            local_path, remote_path = await self.remote_path_map(
                meta, client_name
            )

            logger.debug(
                f"[bold green]Adding to {client_name} ({torrent_client})"
            )

            try:
                if torrent_client.lower() == "rtorrent":
                    self.rtorrent(
                        meta.path,
                        torrent_path,
                        torrent,
                        meta,
                        local_path,
                        remote_path,
                        client,
                        tracker,
                    )
                elif torrent_client == "qbit":
                    await self.qbittorrent(
                        meta.path,
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
                elif torrent_client.lower() == "deluge":
                    self.deluge(
                        meta.path,
                        torrent_path,
                        torrent,
                        local_path,
                        remote_path,
                        client,
                    )
                elif torrent_client.lower() == "transmission":
                    self.transmission(
                        meta.path,
                        torrent,
                        local_path,
                        remote_path,
                        client,
                        meta,
                    )
                elif torrent_client.lower() == "watch":
                    shutil.copy(torrent_path, client["watch_folder"])
            except Exception as e:
                logger.info(
                    f"[bold red]Failed to add torrent to {client_name}: {e}"
                )
        return

    async def inject_delay(
        self, meta: Meta, tracker: str, client_name: str
    ) -> None:
        """
        Applies an optional delay before injecting a torrent into the client.

        The delay can be configured either per tracker or globally in the default settings.
        When both are defined, the tracker-specific value takes precedence over the client setting.

        This mechanism exists to handle cases where a tracker requires a short amount
        of time to register the uploaded torrent hash. Injecting the torrent too early
        may cause connectivity issues, such as failing to discover peers even though
        they are already available.

        By waiting before injection, this function helps ensure proper tracker
        synchronization and more reliable peer discovery.
        """
        tracker_cfg = self.config.get("TRACKERS", {}).get(tracker, {})
        has_tracker_delay = (
            isinstance(tracker_cfg, dict) and "inject_delay" in tracker_cfg
        )
        inject_delay = (
            tracker_cfg.get("inject_delay")
            if has_tracker_delay
            else self.config["DEFAULT"].get("inject_delay", 0)
        )
        if inject_delay is None or (
            isinstance(inject_delay, str) and not inject_delay.strip()
        ):
            return

        try:
            inject_delay = int(inject_delay)
        except ValueError, TypeError:
            if has_tracker_delay:
                logger.info(
                    f"{tracker}: [bold red]CONFIG ERROR: 'inject_delay' must be an integer"
                )
            else:
                logger.info(
                    "[bold red]CONFIG ERROR: 'inject_delay' must be an integer"
                )
            inject_delay = 0

        if inject_delay < 0:
            logger.info("[bold red]CONFIG ERROR: 'inject_delay' must be >= 0")
            inject_delay = 0
        if inject_delay > 0:
            if meta.debug or inject_delay > 5:
                if has_tracker_delay:
                    logger.info(
                        f"{tracker}: [cyan]Waiting {inject_delay} seconds before adding to client '{client_name}'[/cyan]"
                    )
                else:
                    logger.info(
                        f"[cyan]Waiting {inject_delay} seconds before adding to client '{client_name}'[/cyan]"
                    )
            await asyncio.sleep(inject_delay)

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
            metainfo = bencodepy.decode(Path(torrent_path).read_bytes())
        except OSError, bencodepy.BencodeDecodeError, TypeError, ValueError:
            return None
        if not isinstance(metainfo, dict):
            return None
        info = metainfo.get(b"info")
        if not isinstance(info, dict):
            return None
        piece_length = info.get(b"piece length")
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
        torrent_path = str(torrent_path)
        valid = False
        filelist = cast(list[str], meta.filelist)
        meta_path = meta.path
        if meta_path is None:
            return False, torrent_path
        meta_uuid = meta.uuid

        # Normalize the torrent hash based on the client
        if torrent_client in ("qbit", "deluge"):
            torrenthash = torrenthash.lower().strip()
            torrent_path = torrent_path.replace(
                torrenthash.upper(), torrenthash
            )
        elif torrent_client == "rtorrent":
            torrenthash = torrenthash.upper().strip()
            torrent_path = torrent_path.replace(
                torrenthash.upper(), torrenthash
            )

        if meta.debug:
            logger.debug(f"Torrent path after normalization: {torrent_path}")

        # Check if torrent file exists
        torrent: Torrent | None = None
        if Path(torrent_path).exists():
            try:
                normalized_path = (
                    Path(meta.base_dir)
                    / "tmp"
                    / meta_uuid
                    / Path(torrent_path).name
                )
                torrent, torrent_path = self._read_torrent_compat(
                    torrent_path, normalized_path
                )
            except Exception as e:
                logger.info(f"[bold red]Error reading torrent file: {e}")
                return valid, torrent_path

            # Reuse if disc and basename matches or --keep-folder was specified
            if (meta.is_disc and meta.is_disc != "") or (
                meta.keep_folder and meta.isdir
            ):
                torrent_name = torrent.metainfo["info"]["name"]
                if meta_uuid != torrent_name and meta.debug:
                    logger.info("Modified file structure, skipping hash")
                    valid = False
                torrent_filepath = os.path.commonpath(torrent.files)
                if Path(meta_path).name in torrent_filepath:
                    valid = True
                logger.debug(
                    f"Torrent is valid based on disc/basename or keep-folder: {valid}"
                )

            # Otherwise we match either only videos (no subtitles) OR videos + subtitles (if subtitles are present)
            else:
                subtitle_files = meta.subtitle_files
                candidates = [filelist]
                if subtitle_files:
                    candidates.append(filelist + subtitle_files)

                for cand in candidates:
                    # If one file, check for folder
                    if len(torrent.files) == len(cand) == 1:
                        if (
                            Path(torrent.files[0]).name == Path(cand[0]).name
                            and "length" in torrent.metainfo["info"]
                        ):
                            valid = True
                            break
                        logger.debug(
                            f"Single file match status: valid={valid}"
                        )

                    # Check complete relative layouts, not only filenames. Matching
                    # basenames alone can reuse a torrent from a different folder
                    # structure when releases have repeated filenames.
                    elif len(torrent.files) == len(cand):

                        def relative_layout(paths: list[str]) -> list[str]:
                            """Normalize relative file layout for structural comparison."""
                            root = Path(os.path.commonpath(paths))
                            return sorted(
                                str(Path(path).relative_to(root)).replace(
                                    "\\", "/"
                                )
                                for path in paths
                            )

                        torrent_layout = relative_layout(
                            [str(file) for file in torrent.files]
                        )
                        candidate_layout = relative_layout(
                            [str(file) for file in cand]
                        )

                        logger.debug(f"Torrent layout: {torrent_layout}")
                        logger.debug(f"Candidate layout: {candidate_layout}")

                        if torrent_layout == candidate_layout:
                            valid = True
                            break
                        logger.debug(
                            f"Multiple file match status: valid={valid}"
                        )

            if valid:
                verify_path = (
                    filelist[0]
                    if len(filelist) == 1 and Path(filelist[0]).is_file()
                    else meta_path
                )
                try:
                    valid = bool(
                        await asyncio.to_thread(
                            torrent.verify, str(verify_path), threads=1
                        )
                    )
                except Exception as error:
                    logger.debug(
                        f"Torrent content verification failed: {error}"
                    )
                    valid = False
                if not valid:
                    logger.info(
                        "[yellow]Existing torrent matches the file layout but not the current content; forcing a fresh torrent hash[/yellow]"
                    )

        else:
            logger.debug(
                f"No reusable torrent found at {torrent_path}; a fresh torrent can be created if needed"
            )

        # Additional checks if the torrent is valid so far
        if valid and torrent is not None:
            if Path(torrent_path).exists():
                try:
                    reuse_torrent = torrent
                    piece_size = reuse_torrent.piece_size
                    piece_in_mib = piece_size / 1024 / 1024
                    torrent_storage_dir_valid = torrent_path
                    torrent_file_size_kib = round(
                        Path(torrent_storage_dir_valid).stat().st_size / 1024,
                        2,
                    )
                    logger.debug(
                        f"Checking piece size, count and size: pieces={reuse_torrent.pieces}, piece_size={piece_in_mib} MiB, .torrent size={torrent_file_size_kib} KiB"
                    )

                    # Piece size and count validations
                    max_piece_size = meta.max_piece_size
                    if (
                        reuse_torrent.pieces >= 5000
                        and reuse_torrent.piece_size < 4294304
                        and (max_piece_size is None or max_piece_size >= 4)
                    ):
                        logger.debug(
                            "[bold red]Torrent needs to have less than 5000 pieces with a 4 MiB piece size"
                        )
                        valid = False
                    elif (
                        reuse_torrent.pieces >= 8000
                        and reuse_torrent.piece_size < 8488608
                        and (max_piece_size is None or max_piece_size >= 8)
                        and not meta.prefer_small_pieces
                    ):
                        logger.debug(
                            "[bold red]Torrent needs to have less than 8000 pieces with a 8 MiB piece size"
                        )
                        valid = False
                    elif (
                        max_piece_size is None
                        and reuse_torrent.pieces >= 12000
                    ):
                        logger.debug(
                            "[bold red]Torrent needs to have less than 12000 pieces to be valid"
                        )
                        valid = False
                    elif reuse_torrent.piece_size < 32768:
                        logger.debug("[bold red]Piece size too small to reuse")
                        valid = False
                    elif (
                        max_piece_size is None and torrent_file_size_kib > 250
                    ):
                        logger.debug(
                            "[bold red]Torrent file size exceeds 250 KiB"
                        )
                        valid = False
                    else:
                        logger.debug(
                            f"[bold green]REUSING .torrent with infohash: [bold yellow]{torrenthash}"
                        )
                except Exception as e:
                    logger.info(f"[bold red]Error checking reuse torrent: {e}")
                    valid = False

            if meta.debug:
                logger.debug(
                    f"Final validity after piece checks: valid={valid}"
                )
        else:
            if meta.debug:
                logger.debug("[bold yellow]Unwanted Files/Folders Identified")

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

    async def remote_path_map(
        self,
        meta: Meta,
        torrent_client_name: str | dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Return the local and remote roots matching the torrent metadata path."""
        if isinstance(torrent_client_name, dict):
            client_config: dict[str, Any] = torrent_client_name
        elif isinstance(torrent_client_name, str) and torrent_client_name:
            try:
                client_config = cast(
                    dict[str, Any],
                    self.config["TORRENT_CLIENTS"][torrent_client_name],
                )
            except KeyError as exc:
                raise KeyError(
                    f"Torrent client '{torrent_client_name}' not found in TORRENT_CLIENTS"
                ) from exc
        else:
            raise ValueError(
                "torrent_client_name must be a client name or client config dict"
            )

        local_paths = coerce_str_list(
            client_config.get("local_path", ["/LocalPath"])
        )
        remote_paths = coerce_str_list(
            client_config.get("remote_path", ["/RemotePath"])
        )
        if not local_paths:
            local_paths = ["/LocalPath"]
        if not remote_paths:
            remote_paths = ["/RemotePath"]

        list_local_path = local_paths[0]
        list_remote_path = remote_paths[0]
        meta_path = str(meta.path)

        for i, local_path_value in enumerate(local_paths):
            if is_path_under(meta_path, local_path_value):
                list_local_path = local_path_value
                list_remote_path = (
                    remote_paths[i]
                    if i < len(remote_paths)
                    else remote_paths[0]
                )
                break

        local_path = os.path.normpath(list_local_path)
        remote_path = os.path.normpath(list_remote_path)
        if local_path.endswith(os.sep):
            remote_path = remote_path + os.sep

        return local_path, remote_path

    async def get_ptp_from_hash(
        self, meta: Meta, pathed: bool = False, client_name: str | None = None
    ) -> Meta:
        """Fetch PTP metadata through the configured torrent client when available."""
        default_config = self.config.get("DEFAULT", {})
        clients_config = self.config.get("TORRENT_CLIENTS", {})
        default_torrent_client = client_name or (
            default_config.get("default_torrent_client")
            if isinstance(default_config, dict)
            else None
        )
        if (
            not isinstance(default_torrent_client, str)
            or not default_torrent_client
        ):
            logger.debug(
                "[yellow]Skipping torrent metadata lookup: no default torrent client configured.[/yellow]"
            )
            return meta

        client = (
            clients_config.get(default_torrent_client)
            if isinstance(clients_config, dict)
            else None
        )
        if not isinstance(client, dict):
            logger.debug(
                f"[yellow]Skipping torrent metadata lookup: client '{default_torrent_client}' is not configured.[/yellow]"
            )
            return meta

        torrent_client = client.get("torrent_client")
        if torrent_client == "rtorrent":
            await self.get_ptp_from_hash_rtorrent(meta, pathed, client)
            return meta
        if torrent_client == "qbit":
            return await self.get_ptp_from_hash_qbit(meta, client, pathed)
        return meta
