# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import httpx

from data.example_config import config as example_config
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.alpharatio import AlphaRatio
from src.integrations.trackers.amigosshare import AmigosShare
from src.integrations.trackers.anthelion import Anthelion
from src.integrations.trackers.AVISTAZ.avistaz import AvistaZ
from src.integrations.trackers.AVISTAZ.cinemaz import CinemaZ
from src.integrations.trackers.AVISTAZ.privatehd import PrivateHD
from src.integrations.trackers.beyondhd import BEYONDHD
from src.integrations.trackers.bithdtv import BitHDTV
from src.integrations.trackers.bjshare import BJShare
from src.integrations.trackers.brasiltracker import BrasilTracker
from src.integrations.trackers.cathoderaytube import CathodeRayTube
from src.integrations.trackers.digitalcore import DigitalCore
from src.integrations.trackers.filelist import FileList
from src.integrations.trackers.funfile import FunFile
from src.integrations.trackers.greatposterwall import GreatPosterWall
from src.integrations.trackers.hdbits import HDBits
from src.integrations.trackers.hdspace import HDSpace
from src.integrations.trackers.hdtorrents import HDTorrents
from src.integrations.trackers.immortalseed import ImmortalSeed
from src.integrations.trackers.iptorrents import IPTorrents
from src.integrations.trackers.makingoff import MakingOff
from src.integrations.trackers.mteam import MTeam
from src.integrations.trackers.nebulance import Nebulance
from src.integrations.trackers.NEXUSPHP.lajidui import Lajidui
from src.integrations.trackers.NEXUSPHP.lemonhd import LemonHD
from src.integrations.trackers.NEXUSPHP.longpt import LongPT
from src.integrations.trackers.NEXUSPHP.oneptba import OnePTBA
from src.integrations.trackers.NEXUSPHP.ptcafe import PTCafe
from src.integrations.trackers.NEXUSPHP.ptfans import PTFans
from src.integrations.trackers.NEXUSPHP.ptgtk import PTGTK
from src.integrations.trackers.NEXUSPHP.ptzone import PTZone
from src.integrations.trackers.NEXUSPHP.railgunpt import RailgunPT
from src.integrations.trackers.NEXUSPHP.xingyungept import XingyungePT
from src.integrations.trackers.orpheus import Orpheus
from src.integrations.trackers.passthepopcorn import PassThePopcorn
from src.integrations.trackers.pterclub import PTerClub
from src.integrations.trackers.ptskit import Ptskit
from src.integrations.trackers.retroflix import RetroFlix
from src.integrations.trackers.speedapp import SpeedApp
from src.integrations.trackers.swarmazon import Swarmazon
from src.integrations.trackers.torrentleech import TorrentLeech
from src.integrations.trackers.totheglory import ToTheGlory
from src.integrations.trackers.tvchaosuk import TVChaosUK
from src.integrations.trackers.UNIT3D.aither import Aither
from src.integrations.trackers.UNIT3D.asiancinema import AsianCinema
from src.integrations.trackers.UNIT3D.aura4k import Aura4K
from src.integrations.trackers.UNIT3D.bitporn import BitPorn
from src.integrations.trackers.UNIT3D.blutopia import Blutopia
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR
from src.integrations.trackers.UNIT3D.cinematik import Cinematik
from src.integrations.trackers.UNIT3D.darkpeers import DarkPeers
from src.integrations.trackers.UNIT3D.emuwarez import Emuwarez
from src.integrations.trackers.UNIT3D.hawkeuno import HawkeUno
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk
from src.integrations.trackers.UNIT3D.infinityhd import InfinityHD
from src.integrations.trackers.UNIT3D.itatorrents import ItaTorrents
from src.integrations.trackers.UNIT3D.lastdigitalunderground import LastDigitalUnderground
from src.integrations.trackers.UNIT3D.latteam import LatTeam
from src.integrations.trackers.UNIT3D.locadora import Locadora
from src.integrations.trackers.UNIT3D.lst import LST
from src.integrations.trackers.UNIT3D.luminarr import Luminarr
from src.integrations.trackers.UNIT3D.midnightscene import MidnightScene
from src.integrations.trackers.UNIT3D.nordicquality import NordicQuality
from src.integrations.trackers.UNIT3D.oldtoonsworld import OldToonsWorld
from src.integrations.trackers.UNIT3D.onlyencodes import OnlyEncodes
from src.integrations.trackers.UNIT3D.peergarden import PeerGarden
from src.integrations.trackers.UNIT3D.polishtorrent import PolishTorrent
from src.integrations.trackers.UNIT3D.portugas import Portugas
from src.integrations.trackers.UNIT3D.racing4everyone import Racing4Everyone
from src.integrations.trackers.UNIT3D.rastastugan import Rastastugan
from src.integrations.trackers.UNIT3D.reelflix import ReelFlix
from src.integrations.trackers.UNIT3D.retromoviesclub import RetroMoviesClub
from src.integrations.trackers.UNIT3D.samaritano import Samaritano
from src.integrations.trackers.UNIT3D.seedpool import Seedpool
from src.integrations.trackers.UNIT3D.shareisland import ShareIsland
from src.integrations.trackers.UNIT3D.skipthecommercials import SkipTheCommercials
from src.integrations.trackers.UNIT3D.theoldschool import TheOldSchool
from src.integrations.trackers.UNIT3D.tlzdigital import TheLeachZone
from src.integrations.trackers.UNIT3D.torrentdesi import DesiTorrents
from src.integrations.trackers.UNIT3D.torrenteros import Torrenteros
from src.integrations.trackers.UNIT3D.torrenthr import TorrentHR
from src.integrations.trackers.UNIT3D.ulcx import ULCX
from src.integrations.trackers.UNIT3D.unwalled import Unwalled
from src.integrations.trackers.UNIT3D.utopia import Utopia
from src.integrations.trackers.UNIT3D.yuscene import YUSCENE
from src.integrations.trackers.UNIT3D.znth import Zenith
from src.integrations.trackers.USENET.curupira import Curupira
from src.integrations.trackers.USENET.drunkenslug import DrunkenSlug
from src.integrations.trackers.USENET.nzbgeek import NZBGeek
from src.integrations.trackers.USENET.suio import Suio

JsonDict = dict[str, Any]
example_config: dict[str, Any]


class TrackerSetup:
    def __init__(self, config: dict[str, Any]):
        self.config: dict[str, Any] = config

    def _create_tracker_instance(self, tracker: str) -> Any | None:
        tracker_class = tracker_class_map.get(tracker.upper())
        if tracker_class is None:
            return None
        return tracker_class(self.config)

    def filter_unsupported_trackers(self, meta: Meta) -> None:
        category = meta.category
        trackers = meta.trackers
        if not category or not trackers:
            return
        meta.trackers = [tracker for tracker in trackers if self._tracker_is_supported(meta, str(tracker), str(category))]

    def _tracker_is_supported(self, meta: Meta, tracker_name: str, category: str) -> bool:
        tracker_class = tracker_class_map.get(tracker_name.upper())
        if tracker_class is None:
            return True
        if not self._required_tracker_config_present(meta, tracker_name):
            return False
        supported = getattr(tracker_class, "supported_categories", None)
        if supported is None:
            self._mark_tracker_skipped(meta, tracker_name)
            logger.info(f"{tracker_name}: [bold red]Error: Tracker does not have 'supported_categories' defined. Removing from queue.[/bold red]", extra={"markup": False})
            return False
        if self._category_supported(category, supported):
            return True
        logger.info(f"{tracker_name}: [bold red]category '{category}' is not supported. Removing from queue.[/bold red]")
        self._mark_tracker_skipped(meta, tracker_name)
        return False

    def _required_tracker_config_present(self, meta: Meta, tracker_name: str) -> bool:
        missing = self._missing_required_tracker_config(tracker_name)
        self._log_missing_tracker_config(tracker_name, missing)
        return bool(meta.debug or not missing)

    def _missing_required_tracker_config(self, tracker_name: str) -> list[str]:
        tracker_config = self._tracker_config(tracker_name)
        example_tracker = example_config.get("TRACKERS", {}).get(tracker_name, {})
        if not isinstance(example_tracker, dict):
            return []
        required = (("api_key", "API key"), ("announce_url", "announce URL"))
        return [label for key, label in required if key in example_tracker and not tracker_config.get(key)]

    @staticmethod
    def _log_missing_tracker_config(tracker_name: str, missing: list[str]) -> None:
        for label in missing:
            logger.info(f"{tracker_name}: [bold red]Tracker is missing an {label} and will be ignored.[/bold red]")

    def _tracker_config(self, tracker_name: str) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(tracker_name, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @staticmethod
    def _category_supported(category: str, supported: Any) -> bool:
        values = supported if isinstance(supported, tuple | list) else []
        return category.upper() in {str(value).upper() for value in values}

    @staticmethod
    def _mark_tracker_skipped(meta: Meta, tracker_name: str) -> None:
        status = meta.setdefault("tracker_status", {}).setdefault(tracker_name, {})
        status["upload"] = False
        status["skipped"] = True

    def trackers_enabled(self, meta: Meta) -> list[str]:
        trackers = self._normalized_tracker_names(meta)
        meta.trackers = trackers
        self.filter_unsupported_trackers(meta)
        active = list(meta.trackers)
        if meta.manual:
            active.insert(0, "MANUAL")
        valid = [tracker for tracker in active if self._valid_tracker_name(tracker)]
        self._warn_removed_trackers(active, valid)
        return valid

    def _normalized_tracker_names(self, meta: Meta) -> list[str]:
        value = meta.trackers if meta.trackers is not None else self.config["TRACKERS"]["default_trackers"]
        values = self._tracker_name_values(value)
        return [str(item).strip().upper() for item in values]

    @staticmethod
    def _tracker_name_values(value: Any) -> list[Any]:
        if isinstance(value, str):
            return value.split(",")
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _valid_tracker_name(name: str) -> bool:
        return name in tracker_class_map or name in {"MANUAL", "USENET"}

    @staticmethod
    def _warn_removed_trackers(active: list[str], valid: list[str]) -> None:
        for tracker in set(active) - set(valid):
            logger.warning(f"Warning: Tracker '{tracker}' is not recognized and will be ignored.", extra={"markup": False})

    async def get_banned_groups(self, meta: Meta, tracker: str) -> Path | Literal["empty"] | None:
        file_path = self._banned_groups_path(meta, tracker)
        instance = self._create_tracker_instance(tracker)
        if instance is None:
            return None
        special = await self._luminarr_banned_file(tracker, file_path)
        if special is not None:
            return special
        url = self._banned_groups_url(instance)
        if url is None:
            return None
        return await self._refresh_banned_groups(tracker, instance, url, file_path)

    @staticmethod
    def _banned_groups_path(meta: Meta, tracker: str) -> Path:
        return Path(meta.base_dir) / "data" / "banned" / f"{tracker}_banned_groups.json"

    @staticmethod
    def _banned_groups_url(instance: Any) -> str | None:
        value = getattr(instance, "banned_url", None)
        return value if isinstance(value, str) else None

    async def _refresh_banned_groups(
        self,
        tracker: str,
        instance: Any,
        url: str,
        file_path: Path,
    ) -> Path | Literal["empty"] | None:
        if not await self.should_update(file_path):
            return file_path
        data = await self._fetch_banned_groups(tracker, instance, url)
        if data is None:
            return None
        if not data:
            return "empty"
        await self.write_banned_groups_to_file(file_path, data)
        return file_path

    async def _luminarr_banned_file(self, tracker: str, file_path: Path) -> Path | None:
        if tracker.upper() != "LUMINARR":
            return None
        await self.sync_trash_groups(file_path)
        return file_path if file_path.exists() else None

    async def _fetch_banned_groups(self, tracker: str, instance: Any, url: str) -> list[Any] | None:
        api_key = str(self._tracker_config(tracker).get("api_key", "")).strip()
        auth_mode = getattr(instance, "banned_groups_auth_mode", "bearer")
        headers = self._banned_headers(api_key, auth_mode)
        data: list[Any] = []
        cursor: str | None = None
        async with httpx.AsyncClient() as client:
            while True:
                page = await self._banned_page(client, tracker, instance, url, headers, api_key, auth_mode, cursor)
                if page is None:
                    return None
                page_data, cursor, complete = page
                data.extend(page_data)
                if complete:
                    return data

    @staticmethod
    def _banned_headers(api_key: str, auth_mode: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def _banned_page(
        self,
        client: httpx.AsyncClient,
        tracker: str,
        instance: Any,
        url: str,
        headers: dict[str, str],
        api_key: str,
        auth_mode: str,
        cursor: str | None,
    ) -> tuple[list[Any], str | None, bool] | None:
        try:
            params = self._banned_params(api_key, auth_mode, cursor)
            response = await client.get(url=url, headers=headers, params=params)
            return self._parse_banned_page(response, tracker, instance)
        except httpx.RequestError as error:
            logger.info(f"[red]HTTP Request failed for tracker '{tracker}': {error}[/red]")
            return None
        except Exception as error:
            logger.info(f"[red]An unexpected error occurred: {error}[/red]")
            return None

    @staticmethod
    def _banned_params(api_key: str, auth_mode: str, cursor: str | None) -> JsonDict:
        if auth_mode == "api_token":
            return {"api_token": api_key}
        return {"cursor": cursor, "per_page": 100} if cursor else {"per_page": 100}

    def _parse_banned_page(self, response: Any, tracker: str, instance: Any) -> tuple[list[Any], str | None, bool] | None:
        if response.status_code != 200:
            self._log_banned_status(response.status_code, tracker)
            return None
        payload = response.json()
        if isinstance(payload, list):
            return self._banned_items(payload), None, True
        if not isinstance(payload, dict):
            logger.info(f"[red]Unexpected response format: {type(payload)}[/red]")
            return None
        return self._parse_banned_mapping(cast(JsonDict, payload), instance)

    def _parse_banned_mapping(self, payload: JsonDict, instance: Any) -> tuple[list[Any], str | None, bool] | None:
        key = getattr(instance, "banned_groups_response_key", "data")
        page = payload.get(key, [])
        if not isinstance(page, list):
            logger.info(f"[red]Unexpected '{key}' format: {type(page)}[/red]")
            return None
        meta_info = payload.get("meta", {})
        if not isinstance(meta_info, dict):
            logger.info(f"[red]Unexpected 'meta' format: {type(meta_info)}[/red]")
            return None
        cursor = cast(str | None, meta_info.get("next_cursor")) or None
        return self._banned_items(page), cursor, cursor is None

    @staticmethod
    def _banned_items(value: list[Any]) -> list[Any]:
        return [item for item in value if isinstance(item, dict | str)]

    @staticmethod
    def _mapping_items(value: list[Any]) -> list[JsonDict]:
        return [cast(JsonDict, item) for item in value if isinstance(item, dict)]

    @staticmethod
    def _log_banned_status(status_code: int, tracker: str) -> None:
        if status_code == 404:
            logger.info(f"Error: Tracker '{tracker}' returned 404 for the banned groups API.")
        else:
            logger.info(f"Error: Received status code {status_code} for tracker '{tracker}'.")

    async def write_banned_groups_to_file(self, file_path: str | Path, json_data: list[Any]) -> None:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            names = self._banned_group_names(json_data)
            content = self._banned_group_file_content(names, json_data)
            await asyncio.to_thread(self._write_file, path, content)
            logger.debug(f"File '{file_path}' updated successfully with {len(names)} groups.")
        except Exception as error:
            logger.info(f"An error occurred: {error}")

    @staticmethod
    def _banned_group_names(json_data: list[Any]) -> list[str]:
        names: list[str] = []
        for item in json_data:
            name = TrackerSetup._banned_group_name(item)
            if name:
                names.append(name)
        return names

    @staticmethod
    def _banned_group_name(item: Any) -> str:
        if isinstance(item, dict) and "name" in item:
            return str(item["name"])
        return item if isinstance(item, str) else ""

    @staticmethod
    def _banned_group_file_content(names: list[str], raw: list[Any]) -> dict[str, Any]:
        return {
            "last_updated": datetime.now(UTC).strftime("%Y-%m-%d"),
            "banned_groups": ", ".join(names),
            "raw_data": raw,
        }

    async def sync_trash_groups(self, file_path: str | Path) -> None:
        data = await self._trash_payload()
        if data is None:
            return
        groups = self._trash_group_names(data.get("specifications", []))
        if not groups:
            logger.debug("[yellow]No groups extracted from TRaSH data.[/yellow]")
            return
        await self.write_banned_groups_to_file(file_path, [{"name": group} for group in groups])

    async def _trash_payload(self) -> JsonDict | None:
        url = "https://raw.githubusercontent.com/TRaSH-Guides/Guides/refs/heads/master/docs/json/radarr/cf/lq.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
            if response.status_code != 200:
                logger.error(f"[red]Failed to fetch TRaSH groups: HTTP {response.status_code}[/red]")
                return None
            payload = response.json()
            return cast(JsonDict, payload) if isinstance(payload, dict) else {}
        except Exception as error:
            logger.error(f"[red]Failed to fetch TRaSH groups: {error}[/red]")
            return None

    @classmethod
    def _trash_group_names(cls, value: Any) -> list[str]:
        specs = value if isinstance(value, list) else []
        groups: list[str] = []
        for spec in specs:
            for group in cls._trash_spec_groups(spec):
                if group not in groups:
                    groups.append(group)
        return groups

    @classmethod
    def _trash_spec_groups(cls, spec: Any) -> list[str]:
        try:
            value = cls._trash_spec_value(spec)
            return cls._split_trash_group_value(value)
        except (KeyError, TypeError, ValueError, AttributeError, re.error) as error:
            logger.debug(f"[yellow]Skipped invalid TRaSH specification: {error}[/yellow]")
            return []

    @classmethod
    def _trash_spec_value(cls, spec: Any) -> str:
        fields = cls._release_group_spec_fields(spec)
        return str(fields.get("value", "") or "")

    @staticmethod
    def _release_group_spec_fields(spec: Any) -> JsonDict:
        if not isinstance(spec, dict) or spec.get("implementation") != "ReleaseGroupSpecification":
            return {}
        fields = spec.get("fields") or {}
        return cast(JsonDict, fields) if isinstance(fields, dict) else {}

    @classmethod
    def _split_trash_group_value(cls, value: str) -> list[str]:
        name = cls._trash_group_value(value)
        return [part.strip() for part in name.split("|") if part.strip()] if name else []

    @staticmethod
    def _trash_group_value(value: str) -> str:
        match = re.search(r"\(([^)]+)\)", value)
        if match:
            return match.group(1)
        cleaned = re.sub(r"[\\^\$\b]", "", value)
        return re.sub(r"[\(\)\[\]\|]", "", cleaned).strip()

    def _write_file(self, file_path: str | Path, data: JsonDict) -> None:
        """Blocking file write operation, runs in a background thread"""
        with Path(file_path).open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)

    async def should_update(self, file_path: str | Path) -> bool:
        try:
            content = await asyncio.to_thread(self._read_file, file_path)
            data = cast(JsonDict, json.loads(content))
            last_updated = datetime.strptime(str(data["last_updated"]), "%Y-%m-%d").replace(tzinfo=UTC)
            return datetime.now(UTC) >= last_updated + timedelta(days=1)
        except FileNotFoundError:
            return True
        except Exception as e:
            logger.info(f"Error reading file: {e}")
            return True

    def _read_file(self, file_path: str | Path) -> str:
        """Helper function to read the file in a blocking thread"""
        with Path(file_path).open(encoding="utf-8") as file:
            return file.read()

    async def check_banned_group(self, tracker: str, banned_group_list: list[Any], meta: Meta) -> bool:
        group = self._release_group(meta)
        if not group:
            return False
        dynamic = await self._dynamic_banned_groups(tracker, meta)
        if dynamic is not None:
            banned_group_list = dynamic
        matched = self._banned_group_match(group, banned_group_list, tracker, meta)
        if not matched:
            return False
        return await self._banned_group_decision(meta)

    @staticmethod
    def _release_group(meta: Meta) -> str:
        if not meta.tag:
            return ""
        group = meta.tag[1:].lower()
        return "taoe" if "taoe" in group else group

    async def _dynamic_banned_groups(self, tracker: str, meta: Meta) -> list[Any] | None:
        if tracker.upper() not in {"AITHER", "CAPYBARABR", "LST", "LUMINARR", "SPEEDAPP", "ZENITH"}:
            return None
        file_path = await self.get_banned_groups(meta, tracker)
        if file_path == "empty":
            logger.info(f"[bold red]No banned groups found for '{tracker}'.")
            return []
        if not file_path:
            logger.info(f"[bold red]Failed to load banned groups for '{tracker}'.")
            return []
        return await self._banned_groups_from_file(tracker, Path(file_path))

    async def _banned_groups_from_file(self, tracker: str, file_path: Path) -> list[str]:
        try:
            content = await asyncio.to_thread(self._read_file, file_path)
            payload = json.loads(content)
            value = payload.get("banned_groups", "") if isinstance(payload, dict) else ""
            return str(value).split(", ") if value else []
        except FileNotFoundError:
            logger.info(f"[bold red]Banned group file for '{tracker}' not found.")
            return []
        except json.JSONDecodeError:
            logger.info(f"[bold red]Failed to parse banned group file for '{tracker}'.")
            return []

    @classmethod
    def _banned_group_match(cls, group: str, values: list[Any], tracker: str, meta: Meta) -> bool:
        for value in values:
            name, note = cls._banned_group_entry(value)
            if name and group == name.lower():
                cls._log_banned_group_match(tracker, meta, note)
                return True
        return False

    @staticmethod
    def _banned_group_entry(value: Any) -> tuple[str, str]:
        if isinstance(value, list):
            items = [str(item) for item in value]
            return (items[0], items[1] if len(items) > 1 else "") if items else ("", "")
        return str(value), ""

    @staticmethod
    def _log_banned_group_match(tracker: str, meta: Meta, note: str) -> None:
        logger.info(f"[bold yellow]{meta.tag[1:]}[/bold yellow][bold red] was found on [bold yellow]{tracker}'s[/bold yellow] list of banned groups.")
        if note:
            logger.info(f"[bold red]NOTE: [bold yellow]{note}")

    async def _banned_group_decision(self, meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return True
        try:
            return not bool(cli_ui.ask_yes_no(cli_ui.red, "Do you want to continue anyway?", default=False))
        except EOFError:
            logger.info("\n[yellow]Prompt ended; keeping the banned-group safeguard active.[/yellow]")
            await cleanup_manager.cleanup()
            cleanup_manager.reset_terminal()
            return True

    async def write_internal_claims_to_file(self, file_path: str | Path, data: list[JsonDict]) -> None:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            extracted = [claim for item in data if (claim := self._claim_record(item)) is not None]
            if not extracted:
                logger.debug("No valid claims found to write.")
                return
            content = self._claims_file_content(extracted, data)
            await asyncio.to_thread(self._write_file, path, content)
            logger.debug(f"File '{file_path}' updated successfully with {len(extracted)} claims.")
        except Exception as error:
            logger.info(f"An error occurred: {error}")

    @staticmethod
    def _claim_record(item: JsonDict) -> JsonDict | None:
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            logger.info(f"Skipping invalid item: {item}")
            return None
        return {
            "title": attributes.get("title", "Unknown"),
            "season": attributes.get("season", "Unknown"),
            "tmdb_id": attributes.get("tmdb_id", "Unknown"),
            "resolutions": attributes.get("resolutions", []),
            "types": attributes.get("types", []),
        }

    @staticmethod
    def _claims_file_content(extracted: list[JsonDict], raw: list[JsonDict]) -> JsonDict:
        return {
            "last_updated": datetime.now(UTC).strftime("%Y-%m-%d"),
            "titles_csv": ", ".join(str(entry.get("title", "")) for entry in extracted),
            "extracted_data": extracted,
            "raw_data": raw,
        }

    async def get_torrent_claims(self, meta: Meta, tracker: str) -> bool | None:
        file_path = Path(meta.base_dir) / "data" / "banned" / f"{tracker}_claimed_releases.json"
        instance = self._create_tracker_instance(tracker)
        if instance is None:
            return None
        url = getattr(instance, "claims_url", None)
        if not isinstance(url, str):
            return None
        if not await self.should_update(file_path):
            return await self.check_tracker_claims(meta, tracker)
        data = await self._fetch_claims(tracker, url)
        if not data:
            return False
        await self.write_internal_claims_to_file(file_path, data)
        return await self.check_tracker_claims(meta, tracker)

    async def _fetch_claims(self, tracker: str, url: str) -> list[JsonDict] | None:
        headers = self._claim_headers(tracker)
        data: list[JsonDict] = []
        cursor: str | None = None
        async with httpx.AsyncClient() as client:
            while True:
                page = await self._claim_page(client, url, headers, cursor)
                if page is None:
                    return None
                page_data, cursor = page
                data.extend(page_data)
                if not cursor:
                    return data

    def _claim_headers(self, tracker: str) -> dict[str, str]:
        api_key = str(self._tracker_config(tracker).get("api_key", "")).strip()
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    async def _claim_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        cursor: str | None,
    ) -> tuple[list[JsonDict], str | None] | None:
        try:
            params: JsonDict = {"cursor": cursor, "per_page": 100} if cursor else {"per_page": 100}
            response = await client.get(url=url, headers=headers, params=params)
            return self._parse_claim_page(response)
        except httpx.RequestError as error:
            logger.info(f"[red]HTTP Request failed: {error}[/red]")
            return None
        except Exception as error:
            logger.info(f"[red]An unexpected error occurred: {error}[/red]")
            return None

    @classmethod
    def _parse_claim_page(cls, response: Any) -> tuple[list[JsonDict], str | None] | None:
        if response.status_code != 200:
            logger.error(f"[red]Error: Received status code {response.status_code}[/red]")
            return None
        payload = response.json()
        return cls._claim_payload_page(payload)

    @classmethod
    def _claim_payload_page(cls, payload: Any) -> tuple[list[JsonDict], str | None] | None:
        if not isinstance(payload, dict):
            logger.info(f"[red]Unexpected response format: {type(payload)}[/red]")
            return None
        page = cls._claim_data_list(payload.get("data", []))
        meta_info = cls._claim_meta_mapping(payload.get("meta", {}))
        if page is None or meta_info is None:
            return None
        cursor = cast(str | None, meta_info.get("next_cursor")) or None
        return cls._mapping_items(page), cursor

    @staticmethod
    def _claim_data_list(value: Any) -> list[Any] | None:
        if isinstance(value, list):
            return value
        logger.info(f"[red]Unexpected 'data' format: {type(value)}[/red]")
        return None

    @staticmethod
    def _claim_meta_mapping(value: Any) -> JsonDict | None:
        if isinstance(value, dict):
            return cast(JsonDict, value)
        logger.info(f"[red]Unexpected 'meta' format: {type(value)}[/red]")
        return None

    async def check_tracker_claims(self, meta: Meta, tracker: str | list[str]) -> bool:
        trackers = self._tracker_names(tracker)
        results = await asyncio.gather(*(self._check_single_tracker_claim(meta, name) for name in trackers))
        return any(results)

    @staticmethod
    def _tracker_names(value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [value.strip().upper()]
        return [str(item).upper() for item in value]

    async def _check_single_tracker_claim(self, meta: Meta, tracker_name: str) -> bool:
        try:
            instance = self._create_tracker_instance(tracker_name)
            if instance is None:
                logger.info(f"[red]Tracker {tracker_name} is not registered in tracker_class_map[/red]")
                return False
            ids = await self._claim_mapping_ids(instance, meta)
            claims = await self._claim_file_records(meta, tracker_name)
            return self._matching_claim(meta, tracker_name, claims, ids)
        except Exception as error:
            logger.error(f"[red]Error processing tracker {tracker_name}: {error}[/red]")
            return False

    @staticmethod
    async def _claim_mapping_ids(instance: Any, meta: Meta) -> dict[str, list[Any]]:
        type_mapping = cast(JsonDict, await instance.get_type_id(meta, mapping_only=True))
        resolution_mapping = cast(JsonDict, await instance.get_resolution_id(meta, mapping_only=True))
        return {
            "types": [type_mapping.get(meta.type)] if meta.type else [],
            "resolutions": [resolution_mapping.get(meta.resolution)] if meta.resolution else [],
            "tmdb": [] if meta.tmdb is None else [meta.tmdb],
        }

    @staticmethod
    async def _claim_file_records(meta: Meta, tracker_name: str) -> list[JsonDict]:
        path = Path(meta.base_dir) / "data" / "banned" / f"{tracker_name}_claimed_releases.json"
        if not path.exists():
            logger.info(f"[red]No claim data file found for {tracker_name}[/red]")
            return []
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        payload = json.loads(content)
        records = payload.get("extracted_data", []) if isinstance(payload, dict) else []
        return TrackerSetup._mapping_items(records) if isinstance(records, list) else []

    @classmethod
    def _matching_claim(cls, meta: Meta, tracker: str, claims: list[JsonDict], ids: dict[str, list[Any]]) -> bool:
        for claim in claims:
            if cls._claim_matches(meta, claim, ids):
                logger.info(
                    f"[green]Claimed match found at [cyan]{tracker}: [yellow]{claim.get('title')}, Season: {claim.get('season')}, TMDB ID: {claim.get('tmdb_id')}[/green]"
                )
                return True
        return False

    @classmethod
    def _claim_matches(cls, meta: Meta, claim: JsonDict, ids: dict[str, list[Any]]) -> bool:
        checks = (
            cls._claim_tmdb_matches(claim, ids),
            cls._claim_season_matches(meta, claim),
            cls._claim_values_match(claim.get("resolutions", []), ids["resolutions"]),
            cls._claim_values_match(claim.get("types", []), ids["types"]),
        )
        return all(checks)

    @staticmethod
    def _claim_tmdb_matches(claim: JsonDict, ids: dict[str, list[Any]]) -> bool:
        return claim.get("tmdb_id") in ids["tmdb"]

    @staticmethod
    def _claim_season_matches(meta: Meta, claim: JsonDict) -> bool:
        return meta.category == "MOVIE" or claim.get("season") == (meta.season_int or 0)

    @staticmethod
    def _claim_values_match(value: Any, expected: list[Any]) -> bool:
        return isinstance(value, list) and all(item in value for item in expected)

    async def get_tracker_requests(self, meta: Meta, tracker: str, url: str) -> list[JsonDict]:
        logger.debug(f"[bold green]Searching for existing requests on {tracker}[/bold green]")
        if meta.tmdb is None:
            return []
        headers = self._request_headers(tracker)
        params = self._tracker_request_params(meta, tracker)
        payload = await self._request_payload("GET", url, tracker, headers=headers, params=params)
        return self._unit3d_request_results(tracker, payload)

    def _request_headers(self, tracker: str) -> dict[str, str]:
        api_key = str(self._tracker_config(tracker).get("api_key", "")).strip()
        return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    @staticmethod
    def _tracker_request_params(meta: Meta, tracker: str) -> dict[str, Any]:
        return {"tmdbId": meta.tmdb} if tracker == "HAWKEUNO" else {"tmdb": meta.tmdb}

    async def _request_payload(
        self,
        method: str,
        url: str,
        tracker: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> JsonDict | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                request = client.get if method == "GET" else client.post
                response = await request(url=url, headers=headers, params=params)
            return self._response_payload_or_none(response, tracker)
        except httpx.TimeoutException:
            logger.info("[bold red]Request timed out after 5 seconds")
            return None
        except httpx.RequestError as error:
            logger.info(f"[bold red]Unable to search for existing torrents: {error}")
            return None
        except Exception as error:
            logger.error(f"[bold red]Unexpected error: {error}")
            return None

    @staticmethod
    def _response_payload_or_none(response: Any, tracker: str) -> JsonDict | None:
        if response.status_code != 200:
            logger.info(f"[bold red]Failed to search torrents on {tracker}. HTTP Status: {response.status_code}")
            return None
        payload = response.json()
        if not isinstance(payload, dict):
            logger.info(f"[bold red]Unexpected response format: {type(payload)}[/bold red]")
            return None
        return cast(JsonDict, payload)

    @classmethod
    def _request_result_items(cls, payload: JsonDict | None) -> list[JsonDict]:
        if payload is None:
            return []
        for key in ("data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return cls._mapping_items(value)
        logger.info("[bold red]Unexpected response format[/bold red]")
        return []

    @classmethod
    def _unit3d_request_results(cls, tracker: str, payload: JsonDict | None) -> list[JsonDict]:
        return [cls._unit3d_request_result(tracker, item) for item in cls._request_result_items(payload)]

    @staticmethod
    def _unit3d_request_result(tracker: str, item: JsonDict) -> JsonDict:
        attributes = item.get("attributes", item) if tracker == "HAWKEUNO" else item
        attrs = cast(JsonDict, attributes) if isinstance(attributes, dict) else {}
        return {
            "id": item.get("id") if tracker == "HAWKEUNO" else attrs.get("id"),
            "name": attrs.get("name"),
            "description": attrs.get("description"),
            "category": attrs.get("category_id"),
            "type": attrs.get("type_id"),
            "resolution": attrs.get("resolution_id"),
            "bounty": attrs.get("bounty"),
            "status": attrs.get("status"),
            "claimed": attrs.get("claimed"),
            "season": attrs.get("season_number"),
            "episode": attrs.get("episode_number"),
        }

    async def bhd_request_check(self, meta: Meta, tracker: str, url: str) -> list[JsonDict]:
        if not self._beyondhd_configured():
            logger.info("[red]BEYONDHD API key not configured. Skipping BEYONDHD request check.[/red]")
            return []
        logger.debug(f"[bold green]Searching for existing requests on {tracker}[/bold green]")
        params = {"action": "search", "tmdb_id": f"{(meta.category or '').lower()}/{meta.tmdb_id}"}
        payload = await self._request_payload("POST", url, tracker, params=params)
        return [self._beyondhd_request_result(item) for item in self._request_result_items(payload)]

    def _beyondhd_configured(self) -> bool:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return False
        value = trackers.get("BEYONDHD")
        return isinstance(value, dict) and bool(value.get("api_key"))

    @staticmethod
    def _beyondhd_request_result(item: JsonDict) -> JsonDict:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "type": item.get("source"),
            "resolution": item.get("type"),
            "dv": item.get("dv"),
            "hdr": item.get("hdr"),
            "bounty": item.get("bounty"),
            "status": item.get("status"),
            "internal": item.get("internal"),
            "url": item.get("url"),
        }

    async def tracker_request(self, meta: Meta, tracker: str | list[str]) -> bool:
        trackers = self._tracker_names(tracker)
        results = await asyncio.gather(*(self._process_tracker_request(meta, name) for name in trackers))
        return any(results)

    async def _process_tracker_request(self, meta: Meta, tracker_name: str) -> bool | list[JsonDict]:
        instance = self._create_tracker_instance(tracker_name)
        if instance is None:
            logger.info(f"[red]Tracker {tracker_name} is not registered in tracker_class_map[/red]")
            return False
        custom = await self._custom_tracker_request(meta, tracker_name, instance)
        if custom is not None:
            return custom
        context = await self._request_context(meta, tracker_name, instance)
        if context is None:
            return False
        requests, url, mappings = context
        request_data, existing_uuids, log_path = await self._load_request_log(meta, tracker_name)
        self._process_request_entries(meta, tracker_name, requests, url, mappings, request_data, existing_uuids)
        await self._save_request_log(log_path, request_data)
        return requests

    async def _custom_tracker_request(self, meta: Meta, tracker_name: str, instance: Any) -> bool | None:
        custom_trackers = {"AMIGOSSHARE", "BJSHARE", "FUNFILE", "HDSPACE", "AVISTAZ", "CINEMAZ", "PRIVATEHD", "MTEAM", "ORPHEUS"}
        if tracker_name not in custom_trackers:
            return None
        requests = cast(list[JsonDict], await instance.get_requests(meta))
        return bool(requests) if tracker_name == "ORPHEUS" else False

    async def _request_context(
        self,
        meta: Meta,
        tracker_name: str,
        instance: Any,
    ) -> tuple[list[JsonDict], str, dict[str, list[Any]]] | None:
        url = getattr(instance, "requests_url", None)
        if tracker_name == "BEYONDHD":
            if not isinstance(url, str):
                return None
            return await self.bhd_request_check(meta, tracker_name, url), url, {}
        if not isinstance(url, str):
            return None
        requests = await self.get_tracker_requests(meta, tracker_name, url)
        mappings = await self._request_mapping_ids(meta, instance)
        return requests, url, mappings

    @classmethod
    async def _request_mapping_ids(cls, meta: Meta, instance: Any) -> dict[str, list[Any]]:
        return {
            "types": await cls._mapped_id(instance.get_type_id, meta, meta.type, "Type"),
            "resolutions": await cls._mapped_id(instance.get_resolution_id, meta, meta.resolution, "Resolution"),
            "categories": await cls._mapped_id(instance.get_category_id, meta, meta.category, "Category"),
        }

    @staticmethod
    async def _mapped_id(method: Any, meta: Meta, name: Any, label: str) -> list[Any]:
        mapping = cast(JsonDict, await method(meta, mapping_only=True))
        values = [mapping.get(name)] if name else []
        if None in values:
            logger.warning(f"[yellow]Warning: {label} in meta not found in tracker mapping.[/yellow]")
        return values

    async def _load_request_log(self, meta: Meta, tracker_name: str) -> tuple[list[JsonDict], set[str], Path]:
        log_path = Path(meta.base_dir) / "tmp" / f"{tracker_name}_request_results.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        data = await self._read_request_log(log_path)
        uuids = {str(entry.get("uuid")) for entry in data}
        return data, uuids, log_path

    @classmethod
    async def _read_request_log(cls, log_path: Path) -> list[JsonDict]:
        content = await cls._read_request_log_text(log_path)
        return cls._parse_request_log_text(content)

    @staticmethod
    async def _read_request_log_text(log_path: Path) -> str:
        try:
            async with aiofiles.open(log_path, encoding="utf-8") as handle:
                return await handle.read()
        except OSError:
            return ""

    @classmethod
    def _parse_request_log_text(cls, content: str) -> list[JsonDict]:
        if not content.strip():
            return []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return []
        return cls._mapping_items(payload) if isinstance(payload, list) else []

    @staticmethod
    async def _save_request_log(log_path: Path, request_data: list[JsonDict]) -> None:
        if not request_data:
            return
        async with aiofiles.open(log_path, "w", encoding="utf-8") as handle:
            await handle.write(json.dumps(request_data, indent=4))

    def _process_request_entries(
        self,
        meta: Meta,
        tracker_name: str,
        requests: list[JsonDict],
        url: str,
        mappings: dict[str, list[Any]],
        request_data: list[JsonDict],
        existing_uuids: set[str],
    ) -> None:
        for request in requests:
            if tracker_name == "BEYONDHD":
                self._process_beyondhd_request(meta, tracker_name, request, request_data, existing_uuids)
            else:
                self._process_unit3d_request(meta, tracker_name, request, url, mappings, request_data, existing_uuids)

    def _process_unit3d_request(
        self,
        meta: Meta,
        tracker_name: str,
        request: JsonDict,
        url: str,
        mappings: dict[str, list[Any]],
        request_data: list[JsonDict],
        existing_uuids: set[str],
    ) -> None:
        if not self._request_category_matches(request, mappings):
            return
        match = self._unit3d_request_match(meta, request, mappings)
        request_url = re.sub(r"/api/requests/filter$", f"/requests/{request.get('id')}", url)
        self._log_unit3d_request(meta, tracker_name, request, request_url, match)
        if not request.get("claimed"):
            self._append_request_log(meta, request_data, existing_uuids, request, request_url, match)

    @staticmethod
    def _request_category_matches(request: JsonDict, mappings: dict[str, list[Any]]) -> bool:
        expected = {str(value) for value in mappings.get("categories", [])}
        return str(request.get("category")) in expected

    @classmethod
    def _unit3d_request_match(cls, meta: Meta, request: JsonDict, mappings: dict[str, list[Any]]) -> str:
        type_match, type_any = cls._mapped_or_any(request.get("type"), mappings.get("types", []))
        resolution_match, resolution_any = cls._mapped_or_any(request.get("resolution"), mappings.get("resolutions", []))
        exact = cls._unit3d_exact_request(meta, request, type_match, resolution_match)
        if exact:
            return "double_check" if type_any or resolution_any else "exact"
        return "partial"

    @staticmethod
    def _mapped_or_any(value: Any, expected: list[Any]) -> tuple[bool, bool]:
        if value is None:
            return True, True
        return str(value) in {str(item) for item in expected}, False

    @classmethod
    def _unit3d_exact_request(cls, meta: Meta, request: JsonDict, type_match: bool, resolution_match: bool) -> bool:
        if not type_match or not resolution_match or request.get("claimed"):
            return False
        if meta.category == "MOVIE":
            return True
        return cls._episode_request_matches(meta, request)

    @staticmethod
    def _episode_request_matches(meta: Meta, request: JsonDict) -> bool:
        season = TrackerSetup._safe_request_int(request.get("season"))
        episode = TrackerSetup._safe_request_int(request.get("episode"))
        return bool(season and episode and season == meta.season_int and episode == meta.episode_int)

    @staticmethod
    def _safe_request_int(value: Any) -> int:
        try:
            return int(value) if value is not None else 0
        except TypeError, ValueError:
            return 0

    @classmethod
    def _log_unit3d_request(cls, meta: Meta, tracker: str, request: JsonDict, url: str, match: str) -> None:
        exact = match in {"exact", "double_check"}
        label = "exact request match" if exact else "request"
        logger.info(
            f"[bold blue]Found {label} on [bold yellow]{tracker}[/bold yellow] with bounty [bold yellow]{request.get('bounty')}[/bold yellow] and with status [bold yellow]{request.get('status')}[/bold yellow][/bold blue]"
        )
        logger.info(f"[bold blue]Claimed status:[/bold blue] [bold yellow]{request.get('claimed')}[/bold yellow]")
        cls._log_request_name(meta, request, url)
        if match == "double_check":
            logger.info("[bold red]Type and/or resolution was set to ANY, double check any description requirements:[/bold red]")
        if match != "exact":
            logger.info(f"[bold green]Request desc: {str(request.get('description') or '')[:100]}[/bold green]")

    @staticmethod
    def _log_request_name(meta: Meta, request: JsonDict, url: str) -> None:
        name = str(request.get("name") or "")
        if meta.category == "MOVIE":
            logger.info(f"[bold yellow]{name}:[/bold yellow] {url}")
            return
        season = TrackerSetup._safe_request_int(request.get("season"))
        episode = TrackerSetup._safe_request_int(request.get("episode"))
        logger.info(f"[bold yellow]{name}[/bold yellow] - [bold yellow]S{season:02d} E{episode:02d}:[/bold yellow] {url}")

    @classmethod
    def _append_request_log(
        cls,
        meta: Meta,
        request_data: list[JsonDict],
        existing_uuids: set[str],
        request: JsonDict,
        url: str,
        match: str,
    ) -> None:
        uuid_value = str(meta.uuid or "")
        if not cls._request_log_appendable(uuid_value, existing_uuids):
            return
        request_data.append(cls._request_log_entry(meta, request, url, match))
        existing_uuids.add(uuid_value)

    @staticmethod
    def _request_log_appendable(uuid_value: str, existing_uuids: set[str]) -> bool:
        return bool(uuid_value and uuid_value not in existing_uuids)

    @staticmethod
    def _request_log_entry(meta: Meta, request: JsonDict, url: str, match: str) -> JsonDict:
        entry: JsonDict = {
            "uuid": str(meta.uuid or ""),
            "path": meta.path,
            "url": url,
            "name": str(request.get("name") or ""),
            "bounty": request.get("bounty"),
            "description": str(request.get("description") or ""),
            "claimed": request.get("claimed"),
        }
        if match == "partial":
            entry["match_type"] = "partial"
        return entry

    def _process_beyondhd_request(
        self,
        meta: Meta,
        tracker_name: str,
        request: JsonDict,
        request_data: list[JsonDict],
        existing_uuids: set[str],
    ) -> None:
        state = self._beyondhd_request_state(meta, request)
        match = self._beyondhd_match_kind(meta, state)
        self._log_beyondhd_request(meta, tracker_name, request, state, match)
        if match in {"exact", "hdr_mismatch"}:
            self._append_beyondhd_request_log(meta, request_data, existing_uuids, request, state)

    @classmethod
    def _beyondhd_request_state(cls, meta: Meta, request: JsonDict) -> JsonDict:
        resolution = str(request.get("resolution") or "")
        return {
            "unclaimed": request.get("status") == 1,
            "internal": request.get("internal") == 1,
            "claimed_status": cls._beyondhd_claimed_status(request.get("status")),
            "season": cls._beyondhd_season_matches(meta, str(request.get("name") or "")),
            "dv": cls._beyondhd_dv_matches(meta, request),
            "hdr": cls._beyondhd_hdr_matches(meta, request),
            "resolution": cls._beyondhd_resolution_matches(meta, resolution),
            "type": cls._beyondhd_type_matches(meta, str(request.get("type") or ""), resolution),
            "uhd": "uhd" in resolution.lower(),
        }

    @staticmethod
    def _beyondhd_claimed_status(value: Any) -> str:
        return {1: "Unfilled", 2: "Claimed", 3: "Pending"}.get(value, "")

    @staticmethod
    def _beyondhd_season_matches(meta: Meta, name: str) -> bool:
        match = re.search(r"S\d{2}", name)
        return bool(match and match.group(0) == meta.season)

    @staticmethod
    def _beyondhd_dv_matches(meta: Meta, request: JsonDict) -> bool:
        requested = bool(request.get("dv"))
        has_dv = meta.HDR == "DV"
        return requested == has_dv

    @staticmethod
    def _beyondhd_hdr_matches(meta: Meta, request: JsonDict) -> bool:
        requested = bool(request.get("hdr"))
        has_hdr = meta.HDR in {"HDR10", "HDR10+", "HDR"}
        return requested == has_hdr

    @classmethod
    def _beyondhd_resolution_matches(cls, meta: Meta, request_resolution: str) -> bool:
        lowered = request_resolution.lower()
        if "remux" in lowered:
            return cls._beyondhd_remux_resolution(meta, lowered)
        if meta.is_disc == "BDMV":
            return cls._beyondhd_disc_resolution(meta, lowered)
        return request_resolution == meta.resolution

    @staticmethod
    def _beyondhd_remux_resolution(meta: Meta, lowered: str) -> bool:
        if meta.type != "REMUX":
            return False
        return ("uhd" in lowered and meta.resolution == "2160p") or ("uhd" not in lowered and meta.resolution == "1080p")

    @staticmethod
    def _beyondhd_disc_resolution(meta: Meta, lowered: str) -> bool:
        return ("uhd" in lowered and meta.resolution == "2160p") or ("uhd" not in lowered and meta.resolution == "1080p")

    @classmethod
    def _beyondhd_type_matches(cls, meta: Meta, request_type: str, request_resolution: str) -> bool:
        if cls._beyondhd_remux_type_matches(meta, request_resolution):
            return True
        return cls._beyondhd_source_type_matches(str(meta.type or ""), request_type)

    @staticmethod
    def _beyondhd_remux_type_matches(meta: Meta, request_resolution: str) -> bool:
        return "remux" in request_resolution.lower() and meta.type == "REMUX"

    @staticmethod
    def _beyondhd_source_type_matches(meta_type: str, request_type: str) -> bool:
        if "Blu-ray" in request_type:
            return meta_type == "ENCODE"
        return "WEB" in request_type and "WEB" in meta_type

    @classmethod
    def _beyondhd_match_kind(cls, meta: Meta, state: JsonDict) -> str:
        if not cls._beyondhd_request_eligible(meta, state):
            return "partial"
        return cls._beyondhd_hdr_match_kind(state)

    @classmethod
    def _beyondhd_request_eligible(cls, meta: Meta, state: JsonDict) -> bool:
        return cls._beyondhd_request_fields_ok(state) and cls._beyondhd_request_category_ok(meta, state)

    @staticmethod
    def _beyondhd_request_fields_ok(state: JsonDict) -> bool:
        return bool(state["type"] and state["resolution"] and state["unclaimed"] and not state["internal"])

    @staticmethod
    def _beyondhd_request_category_ok(meta: Meta, state: JsonDict) -> bool:
        return meta.category == "MOVIE" or bool(state["season"])

    @staticmethod
    def _beyondhd_hdr_match_kind(state: JsonDict) -> str:
        if state["dv"] and state["hdr"]:
            return "exact"
        if not state["dv"] and not state["hdr"]:
            return "hdr_mismatch"
        return "partial"

    @classmethod
    def _log_beyondhd_request(cls, meta: Meta, tracker: str, request: JsonDict, state: JsonDict, match: str) -> None:
        logger.info(cls._beyondhd_request_log_line(tracker, request, state, match))
        cls._log_internal_request(state)
        cls._log_beyondhd_request_name(meta, request)

    @staticmethod
    def _beyondhd_request_log_line(tracker: str, request: JsonDict, state: JsonDict, match: str) -> str:
        bounty = request.get("bounty")
        status = state["claimed_status"]
        if match == "exact":
            return f"[bold blue]Found exact request match on [bold yellow]{tracker}[/bold yellow] with bounty [bold yellow]{bounty}[/bold yellow] and with status [bold yellow]{status}[/bold yellow][/bold blue]"
        if match == "hdr_mismatch":
            return (
                f"[bold blue]Found request match on [bold yellow]{tracker}[/bold yellow] with bounty [bold yellow]{bounty}[/bold yellow] with mismatched HDR or DV[/bold blue]"
            )
        return f"[bold blue]Found request on [bold yellow]{tracker}[/bold yellow] with bounty [bold yellow]{bounty}[/bold yellow] and with status [bold yellow]{status}[/bold yellow][/bold blue]"

    @staticmethod
    def _log_internal_request(state: JsonDict) -> None:
        if state["internal"]:
            logger.info("[bold red]Request is internal only[/bold red]")

    @staticmethod
    def _log_beyondhd_request_name(meta: Meta, request: JsonDict) -> None:
        name = str(request.get("name") or "")
        suffix = f" - {meta.season}" if meta.category == "TV" else ""
        logger.info(f"[bold yellow]{name}[/bold yellow]{suffix}: {request.get('url')}")

    @staticmethod
    def _append_beyondhd_request_log(
        meta: Meta,
        request_data: list[JsonDict],
        existing_uuids: set[str],
        request: JsonDict,
        state: JsonDict,
    ) -> None:
        uuid_value = str(meta.uuid or "")
        if not uuid_value or uuid_value in existing_uuids:
            return
        request_data.append(
            {
                "uuid": uuid_value,
                "path": meta.path,
                "url": request.get("url", ""),
                "name": str(request.get("name") or ""),
                "bounty": request.get("bounty"),
                "claimed": state["claimed_status"],
            }
        )
        existing_uuids.add(uuid_value)

    async def process_trumpables(self, meta: Meta, tracker: str) -> bool:
        context = self._trumpable_context(meta, tracker)
        if context is None:
            return False
        url, reported_torrent_id = context
        meta[f"{tracker}_reported_torrent_id"] = reported_torrent_id
        if tracker == "LST":
            logger.debug("[bold green]LST does not support searching existing trump reports[/bold green]")
            return True
        if not await self._existing_trump_reports_allow_upload(meta, tracker, url, reported_torrent_id):
            return False
        return await self._collect_trump_comparisons(meta, tracker)

    def _trumpable_context(self, meta: Meta, tracker: str) -> tuple[str, str] | None:
        instance = self._create_tracker_instance(tracker)
        if instance is None:
            logger.info(f"[red]Tracker {tracker} is not registered in tracker_class_map[/red]")
            return None
        url = getattr(instance, "trumping_url", None)
        if not isinstance(url, str):
            logger.info(f"[red]Tracker {tracker} does not support trumping reports.[/red]")
            return None
        reported = self._reported_torrent_id(meta, tracker)
        if not reported:
            logger.info(f"[red]No reported torrent ID found in meta for trumpable processing on {tracker}[/red]")
            return None
        return url, reported

    @classmethod
    def _reported_torrent_id(cls, meta: Meta, tracker: str) -> str:
        direct = str(meta.get(f"{tracker}_trumpable_id", "") or "")
        if direct:
            return direct
        matched = str(meta.get(f"{tracker}_matched_id", "") or "")
        return matched if matched else cls._first_matched_episode_id(meta, tracker)

    @staticmethod
    def _first_matched_episode_id(meta: Meta, tracker: str) -> str:
        episodes = meta.get(f"{tracker}_matched_episode_ids", [])
        if not isinstance(episodes, list) or not episodes:
            return ""
        first = episodes[0]
        return str(first.get("id", "")) if isinstance(first, dict) else ""

    async def _existing_trump_reports_allow_upload(self, meta: Meta, tracker: str, url: str, reported_id: str) -> bool:
        self._ensure_skip_upload_trackers(meta)
        reports, status = await self.get_tracker_trumps(tracker, url, reported_id)
        if status != 200:
            self._mark_trump_api_failure(meta, tracker, status)
            return False
        if not reports:
            logger.debug(f"[bold green]Will make a trumpable report for this upload at {tracker}[/bold green]")
            return True
        self._log_trump_reports(tracker, reports)
        return self._confirm_existing_trump_upload(meta, tracker)

    @staticmethod
    def _ensure_skip_upload_trackers(meta: Meta) -> None:
        if not isinstance(meta.skip_upload_trackers, list):
            meta.skip_upload_trackers = []

    @staticmethod
    def _append_skip_tracker(meta: Meta, tracker: str) -> None:
        if tracker not in meta.skip_upload_trackers:
            meta.skip_upload_trackers.append(tracker)

    @classmethod
    def _mark_trump_api_failure(cls, meta: Meta, tracker: str, status: int | None) -> None:
        logger.info(f"[bold red]Failed to retrieve trumping reports from {tracker}. HTTP Status: {status}[/bold red]")
        logger.info(f"[bold red]Marking {tracker} to be skipped due to API failure[/bold red]")
        cls._append_skip_tracker(meta, tracker)

    @classmethod
    def _log_trump_reports(cls, tracker: str, reports: list[JsonDict]) -> None:
        logger.info(f"[bold yellow]Found {len(reports)} existing trumping report/s on {tracker} for this release[/bold yellow]")
        for report in reports:
            cls._log_trump_report(report)

    @staticmethod
    def _log_trump_report(report: JsonDict) -> None:
        logger.info(f"  [cyan]Report ID:[/cyan] {report.get('id')} - [cyan]Title:[/cyan] {report.get('title')}")
        torrents = report.get("trumping_torrent", [])
        if not isinstance(torrents, list) or not torrents:
            logger.info("  [yellow]The trumping torrent for this report seems to be in modq.....[/yellow]")
            return
        for torrent in torrents:
            if isinstance(torrent, dict):
                logger.info(f"  [bold green]Already being trumped by:[/bold green] {torrent.get('name', 'Unknown')} (ID: {torrent.get('id', 'N/A')})")

    @classmethod
    def _confirm_existing_trump_upload(cls, meta: Meta, tracker: str) -> bool:
        try:
            confirmed = bool(cli_ui.ask_yes_no("Do you want to proceed with the upload anyway?", default=False))
        except EOFError, KeyboardInterrupt:
            logger.info("[yellow]Prompt cancelled; treating as 'no' for safety.[/yellow]")
            confirmed = False
        if confirmed:
            logger.info(f"[bold green]Proceeding with upload despite existing trumping reports on {tracker}[/bold green]")
            return True
        logger.info(f"[bold red]Marking {tracker} to be skipped[/bold red]")
        cls._append_skip_tracker(meta, tracker)
        return False

    async def _collect_trump_comparisons(self, meta: Meta, tracker: str) -> bool:
        if meta.tv_pack:
            logger.debug(f"[bold green]TV pack upload detected, skipping comparison images for trump report on {tracker}[/bold green]")
            return True
        logger.info(f"[yellow]{tracker} requires comparisons to be provided for trump reports.\nAre the comparison images in the description or are you adding links?")
        mode = self._comparison_mode()
        if mode is None:
            return False
        if mode == "d":
            meta.screenshots_in_description = True
            return True
        if mode == "l":
            return self._collect_comparison_links(meta)
        logger.info("[yellow]Skipping trump report creation as no comparison method provided.[/yellow]")
        return False

    @staticmethod
    def _comparison_mode() -> str | None:
        try:
            value = cli_ui.ask_string("Enter 'd' if in description, 'L' if you want to paste links, or press Enter to skip trumping:", default="")
        except EOFError, KeyboardInterrupt:
            logger.info("[yellow]Prompt cancelled; skipping trump report creation.[/yellow]")
            return None
        return str(value or "").strip().lower()

    @classmethod
    def _collect_comparison_links(cls, meta: Meta) -> bool:
        pair = cls._comparison_link_prompts()
        if pair is None:
            return False
        reported, trumping = pair
        reported_links = cls._split_links(reported)
        trumping_links = cls._split_links(trumping)
        if not reported_links or not trumping_links:
            logger.info("[yellow]No valid screenshot links provided. Skipping trump report creation.[/yellow]")
            return False
        meta.screenshots_reported_torrent = reported_links
        meta.screenshots_trumping_torrent = trumping_links
        return True

    @classmethod
    def _comparison_link_prompts(cls) -> tuple[str, str] | None:
        pair = cls._prompt_comparison_link_pair()
        if pair is None:
            return None
        reported, trumping = pair
        if reported and trumping:
            return reported, trumping
        logger.info("[yellow]No screenshot links provided. Skipping trump report creation.[/yellow]")
        return None

    @staticmethod
    def _prompt_comparison_link_pair() -> tuple[str, str] | None:
        try:
            reported = str(cli_ui.ask_string("Paste screenshot links for the reported torrent (comma-separated):", default="") or "").strip()
            trumping = str(cli_ui.ask_string("Paste screenshot links for the trumping torrent (comma-separated):", default="") or "").strip()
            return reported, trumping
        except EOFError, KeyboardInterrupt:
            logger.info("[yellow]Prompt cancelled; skipping trump report creation.[/yellow]")
            return None

    @staticmethod
    def _split_links(value: str) -> list[str]:
        return [link.strip() for link in value.split(",") if link.strip()]

    async def get_tracker_trumps(self, tracker: str, url: str, reported_torrent_id: str) -> tuple[list[JsonDict], int | None]:
        logger.debug(f"[bold green]Searching for trumps on {tracker}[/bold green]")
        headers = self._trump_headers(tracker)
        params: JsonDict = {"reported_torrent_id": str(reported_torrent_id)}
        data, status = await self._fetch_trump_pages(tracker, url, headers, params)
        results = [self._trump_result(item) for item in data]
        logger.debug(f"Total trumping reports retrieved: {len(results)}")
        return results, status

    def _trump_headers(self, tracker: str) -> dict[str, str]:
        api_key = str(self._tracker_config(tracker).get("api_key", "")).strip()
        return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    async def _fetch_trump_pages(
        self,
        tracker: str,
        url: str,
        headers: dict[str, str],
        params: JsonDict,
    ) -> tuple[list[JsonDict], int | None]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                return await self._trump_page_loop(client, tracker, url, headers, params)
        except httpx.TimeoutException:
            logger.info("[bold red]Request timed out after 10 seconds")
            return [], None
        except Exception as error:
            logger.error(f"[bold red]Unexpected error: {error}")
            return [], None

    async def _trump_page_loop(
        self,
        client: httpx.AsyncClient,
        tracker: str,
        url: str,
        headers: dict[str, str],
        params: JsonDict,
    ) -> tuple[list[JsonDict], int | None]:
        data: list[JsonDict] = []
        cursor: str | None = None
        status: int | None = None
        while True:
            page = await self._trump_page(client, tracker, url, headers, params, cursor)
            if page is None:
                return data, status
            page_data, cursor, status = page
            data.extend(page_data)
            if not cursor:
                return data, status
            logger.info(f"[cyan]Fetched {len(page_data)} trumping reports, waiting 1 second before next page...[/cyan]")
            await asyncio.sleep(1)

    async def _trump_page(
        self,
        client: httpx.AsyncClient,
        tracker: str,
        url: str,
        headers: dict[str, str],
        base_params: JsonDict,
        cursor: str | None,
    ) -> tuple[list[JsonDict], str | None, int] | None:
        params = dict(base_params)
        if cursor:
            params["cursor"] = cursor
        try:
            response = await client.get(url=url, headers=headers, params=params)
        except httpx.RequestError as error:
            logger.info(f"[bold red]HTTP Request failed: {error}[/bold red]")
            return None
        if response.status_code != 200:
            logger.info(f"[bold red]Failed to search trumps on {tracker}. HTTP Status: {response.status_code} - {response.text}[/bold red]")
            return [], None, response.status_code
        return self._parse_trump_page(response)

    @classmethod
    def _parse_trump_page(cls, response: Any) -> tuple[list[JsonDict], str | None, int] | None:
        payload = response.json()
        if not isinstance(payload, dict):
            logger.info(f"[bold red]Unexpected response format: {type(payload)}[/bold red]")
            return None
        data = cls._request_result_items(cast(JsonDict, payload))
        meta_info = payload.get("meta", {})
        if not isinstance(meta_info, dict):
            logger.info(f"[bold red]Unexpected 'meta' format: {type(meta_info)}[/bold red]")
            return data, None, response.status_code
        cursor = cast(str | None, meta_info.get("next_cursor")) or None
        return data, cursor, response.status_code

    @classmethod
    def _trump_result(cls, entry: JsonDict) -> JsonDict:
        return {
            "id": entry.get("id"),
            "type": entry.get("type"),
            "title": entry.get("title"),
            "solved": entry.get("solved"),
            "reported_torrents": entry.get("reported_torrents", []),
            "trumping_torrent": cls._normalized_trumping_torrents(entry.get("trumping_torrent")),
        }

    @staticmethod
    def _normalized_trumping_torrents(value: Any) -> list[JsonDict]:
        if isinstance(value, dict):
            return [cast(JsonDict, value)]
        if isinstance(value, list):
            return [cast(JsonDict, item) for item in value if isinstance(item, dict)]
        return []

    async def make_trumpable_report(self, meta: Meta, tracker: str) -> bool:
        """Create a tracker trump report."""
        logger.debug(f"[bold green]Creating trump report on {tracker}[/bold green]")
        context = self._trump_report_context(meta, tracker)
        if context is None:
            return False
        create_url, reported_id, trumping_id = context
        payload = self._trump_report_payload(meta, tracker, reported_id, trumping_id)
        if payload is None:
            return False
        if meta.debug:
            self._log_debug_trump_report(create_url, payload)
            return True
        return await self._post_trump_report(tracker, create_url, payload)

    def _trump_report_context(self, meta: Meta, tracker: str) -> tuple[str, str, Any] | None:
        base_url = self._trumping_base_url(tracker)
        if base_url is None:
            return None
        reported_id = self._stored_reported_torrent_id(meta, tracker)
        if not reported_id:
            return None
        create_url = self._trump_create_url(tracker, base_url, reported_id)
        if create_url is None:
            return None
        trumping_id = self._trumping_torrent_id(meta, tracker)
        return self._validated_trump_context(meta, create_url, reported_id, trumping_id)

    def _trumping_base_url(self, tracker: str) -> str | None:
        instance = self._create_tracker_instance(tracker)
        if instance is None:
            logger.info(f"[red]Tracker {tracker} is not registered in tracker_class_map[/red]")
            return None
        value = getattr(instance, "trumping_url", None)
        if isinstance(value, str):
            return value
        logger.info(f"[red]No trumping URL found for {tracker}[/red]")
        return None

    @staticmethod
    def _stored_reported_torrent_id(meta: Meta, tracker: str) -> str:
        value = str(meta.get(f"{tracker}_reported_torrent_id", "") or "")
        if not value:
            logger.info(f"[red]No reported torrent ID found in meta for trump report creation on {tracker}[/red]")
        return value

    @staticmethod
    def _validated_trump_context(meta: Meta, create_url: str, reported_id: str, trumping_id: Any) -> tuple[str, str, Any] | None:
        if trumping_id is None and not meta.debug:
            return None
        return create_url, reported_id, trumping_id

    @staticmethod
    def _trump_create_url(tracker: str, base_url: str, reported_id: str) -> str | None:
        if tracker != "LST":
            return base_url.replace("/filter", "/create")
        value = reported_id.strip()
        if not value.isdigit():
            logger.info(f"[red]Invalid or missing reported torrent ID for LST: {reported_id}[/red]")
            return None
        return base_url + f"{int(value)}/trump"

    @staticmethod
    def _trumping_torrent_id(meta: Meta, tracker: str) -> Any | None:
        status = meta.tracker_status or {}
        tracker_status = status.get(tracker, {}) if isinstance(status, dict) else {}
        if isinstance(tracker_status, dict) and "torrent_id" in tracker_status:
            return tracker_status["torrent_id"]
        logger.info(f"[red]No torrent ID found in meta for trumping torrent on {tracker}[/red]")
        logger.info("[red]Either the upload failed, or you're in debug[/red]")
        return None

    def _trump_report_payload(self, meta: Meta, tracker: str, reported_id: str, trumping_id: Any) -> JsonDict | None:
        message = self._trump_message(meta)
        if tracker == "LST":
            return self._lst_trump_payload(meta, message, trumping_id)
        return self._standard_trump_payload(meta, reported_id, trumping_id, message)

    @staticmethod
    def _trump_message(meta: Meta) -> str:
        if meta.tv_pack:
            return f"{meta.ua_name} season pack trump"
        if meta.trump_reason == "exact_match":
            return f"{meta.ua_name} exact filename trump"
        if meta.trump_reason == "trumpable_release":
            return f"{meta.ua_name} trumpable release trump"
        return f"{meta.ua_name} is trumping this torrent for reasons {meta.ua_name} has not correctly caught. User selected yes at a prompt."

    @staticmethod
    def _standard_trump_payload(meta: Meta, reported_id: str, trumping_id: Any, message: str) -> JsonDict:
        payload: JsonDict = {"reported_torrent_id": reported_id, "trumping_torrent_id": trumping_id, "message": message}
        TrackerSetup._append_trump_screenshots(payload, meta)
        return payload

    @staticmethod
    def _append_trump_screenshots(payload: JsonDict, meta: Meta) -> None:
        if "screenshots_reported_torrent" in meta:
            payload["screenshots_reported_torrent"] = ",".join(cast(list[str], meta.screenshots_reported_torrent))
        if "screenshots_trumping_torrent" in meta:
            payload["screenshots_trumping_torrent"] = ",".join(cast(list[str], meta.screenshots_trumping_torrent))
        if "screenshots_in_description" in meta and meta.screenshots_in_description:
            payload["message"] = f"{payload.get('message', '')} - User says comparison screenshots are in description."

    @staticmethod
    def _lst_trump_payload(meta: Meta, message: str, trumping_id: Any) -> JsonDict | None:
        if not meta.tv_pack:
            user_message = TrackerSetup._lst_user_message()
            message = f"{message}: {user_message or 'No additional message provided by user'}"
        message = f"{message}: https://lst.gg/torrents/{trumping_id}"
        return {"message": message}

    @staticmethod
    def _lst_user_message() -> str | None:
        try:
            value = cli_ui.ask_string("Enter a reason for the trump report on LST:")
            return str(value) if value else None
        except EOFError, KeyboardInterrupt:
            logger.info("[yellow]Prompt cancelled; no additional message provided.[/yellow]")
            return None

    def _trump_report_headers(self, tracker: str) -> dict[str, str]:
        api_key = str(self._tracker_config(tracker).get("api_key", "")).strip()
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    async def _post_trump_report(self, tracker: str, create_url: str, payload: JsonDict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url=create_url, headers=self._trump_report_headers(tracker), json=payload)
            return self._trump_post_success(tracker, response)
        except httpx.TimeoutException:
            logger.info("[bold red]Request timed out after 10 seconds[/bold red]")
            return False
        except httpx.RequestError as error:
            logger.info(f"[bold red]HTTP Request failed: {error}[/bold red]")
            return False
        except Exception as error:
            logger.error(f"[bold red]Unexpected error: {error}[/bold red]")
            return False

    @staticmethod
    def _trump_post_success(tracker: str, response: Any) -> bool:
        if response.status_code in {200, 201}:
            logger.info(f"[bold green]Successfully created trump report on {tracker}[/bold green]")
            return True
        logger.info(f"[bold red]Failed to create trump report. HTTP Status: {response.status_code}[/bold red]")
        return False

    @staticmethod
    def _log_debug_trump_report(create_url: str, payload: JsonDict) -> None:
        logger.info("[bold yellow]Debug mode enabled, skipping actual trump report creation.[/bold yellow]")
        logger.info(f"[cyan]POST URL: {create_url}[/cyan]")
        logger.info(f"[cyan]Payload: {payload}[/cyan]")


tracker_class_map: dict[str, Any] = {
    "AURA4K": Aura4K,
    "ASIANCINEMA": AsianCinema,
    "AITHER": Aither,
    "ANTHELION": Anthelion,
    "ALPHARATIO": AlphaRatio,
    "AMIGOSSHARE": AmigosShare,
    "AVISTAZ": AvistaZ,
    "BEYONDHD": BEYONDHD,
    "BITHDTV": BitHDTV,
    "BITPORN": BitPorn,
    "BJSHARE": BJShare,
    "BLUTOPIA": Blutopia,
    "BRASILTRACKER": BrasilTracker,
    "CAPYBARABR": CapybaraBR,
    "CATHODERAYTUBE": CathodeRayTube,
    "CURUPIRA": Curupira,
    "CINEMAZ": CinemaZ,
    "DIGITALCORE": DigitalCore,
    "DARKPEERS": DarkPeers,
    "DRUNKENSLUG": DrunkenSlug,
    "NZBGEEK": NZBGeek,
    "DESITORRENTS": DesiTorrents,
    "EMUWAREZ": Emuwarez,
    "FUNFILE": FunFile,
    "FILELIST": FileList,
    "GREATPOSTERWALL": GreatPosterWall,
    "HDBITS": HDBits,
    "HDSPACE": HDSpace,
    "HDTORRENTS": HDTorrents,
    "HOMIEHELPDESK": HomieHelpDesk,
    "HAWKEUNO": HawkeUno,
    "INFINITYHD": InfinityHD,
    "IPTORRENTS": IPTorrents,
    "IMMORTALSEED": ImmortalSeed,
    "ITATORRENTS": ItaTorrents,
    "LAJIDUI": Lajidui,
    "LEMONHD": LemonHD,
    "LOCADORA": Locadora,
    "LASTDIGITALUNDERGROUND": LastDigitalUnderground,
    "LONGPT": LongPT,
    "LST": LST,
    "LATTEAM": LatTeam,
    "LUMINARR": Luminarr,
    "MAKINGOFF": MakingOff,
    "MIDNIGHTSCENE": MidnightScene,
    "MTEAM": MTeam,
    "NEBULANCE": Nebulance,
    "NORDICQUALITY": NordicQuality,
    "ONLYENCODES": OnlyEncodes,
    "OLDTOONSWORLD": OldToonsWorld,
    "ORPHEUS": Orpheus,
    "PRIVATEHD": PrivateHD,
    "PORTUGAS": Portugas,
    "PTCAFE": PTCafe,
    "PTERCLUB": PTerClub,
    "PTFANS": PTFans,
    "PTGTK": PTGTK,
    "PTZONE": PTZone,
    "PASSTHEPOPCORN": PassThePopcorn,
    "PTSKIT": Ptskit,
    "PEERGARDEN": PeerGarden,
    "POLISHTORRENT": PolishTorrent,
    "RACING4EVERYONE": Racing4Everyone,
    "RASTASTUGAN": Rastastugan,
    "REELFLIX": ReelFlix,
    "RAILGUNPT": RailgunPT,
    "RETROFLIX": RetroFlix,
    "RETROMOVIESCLUB": RetroMoviesClub,
    "SAMARITANO": Samaritano,
    "SHAREISLAND": ShareIsland,
    "SWARMAZON": Swarmazon,
    "SEEDPOOL": Seedpool,
    "SPEEDAPP": SpeedApp,
    "SKIPTHECOMMERCIALS": SkipTheCommercials,
    "SUIO": Suio,
    "CINEMATIK": Cinematik,
    "TORRENTLEECH": TorrentLeech,
    "THELEACHZONE": TheLeachZone,
    "THEOLDSCHOOL": TheOldSchool,
    "TOTHEGLORY": ToTheGlory,
    "TORRENTEROS": Torrenteros,
    "TORRENTHR": TorrentHR,
    "TVCHAOSUK": TVChaosUK,
    "1PTBA": OnePTBA,
    "XINGYUNGEPT": XingyungePT,
    "ULCX": ULCX,
    "UNWALLED": Unwalled,
    "UTOPIA": Utopia,
    "YUSCENE": YUSCENE,
    "ZENITH": Zenith,
}


def get_tracker_comment_hosts(config: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Return tracker domains usable when parsing torrent-comment URLs."""
    tracker_config = _tracker_config_map(config)
    result: dict[str, tuple[str, ...]] = {}
    for tracker_name, tracker_class in tracker_class_map.items():
        domains = _comment_hosts_for_tracker(tracker_name, tracker_class, tracker_config)
        if domains:
            result[tracker_name] = domains
    return result


def _tracker_config_map(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("TRACKERS", {})
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _comment_hosts_for_tracker(tracker_name: str, tracker_class: Any, tracker_config: dict[str, Any]) -> tuple[str, ...]:
    values = [getattr(tracker_class, "base_url", "")]
    values.extend(_tracker_class_comment_values(tracker_class))
    values.extend(_configured_comment_values(tracker_config.get(tracker_name, {})))
    hosts = [host for value in values if (host := _comment_hostname(value))]
    return tuple(dict.fromkeys(hosts))


def _tracker_class_comment_values(tracker_class: Any) -> list[Any]:
    values: list[Any] = []
    for attribute_name in ("comment_hosts", "tracker_urls"):
        values.extend(_as_comment_values(getattr(tracker_class, attribute_name, ())))
    return values


def _configured_comment_values(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    return [value.get("base_url", ""), value.get("announce_url", "")]


def _as_comment_values(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [value]
    return list(value) if isinstance(value, tuple | list) else []


def _comment_hostname(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    return parsed.hostname.lower() if parsed.hostname else None


api_trackers: set[str] = {name for name, cls in tracker_class_map.items() if getattr(cls, "auth_type", None) == "unit3d_api"}
other_api_trackers: set[str] = {name for name, cls in tracker_class_map.items() if getattr(cls, "auth_type", None) == "other_api"}
http_trackers: set[str] = {name for name, cls in tracker_class_map.items() if getattr(cls, "auth_type", None) == "cookies"}
