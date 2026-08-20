# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
import re
from typing import Any
from urllib.parse import urlencode

import aiofiles
import cli_ui
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.media.media_info import strip_report_by_line
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common

Config = dict[str, Any]


class Nebulance:
    """
    NBL Private Torrent Tracker
    """

    auth_type = "other_api"
    tracker = "NEBULANCE"
    display_name = "Nebulance"
    allows_bloated_audio = True
    source_flag = "NBL"
    banned_groups = (
        "[Oj]",
        "0neshot",
        "3LTON",
        "4yEo",
        "AFG",
        "AkihitoSubs",
        "AniHLS",
        "Anime Time",
        "AnimeRG",
        "AniURL",
        "ASW",
        "BakedFish",
        "bonkai77",
        "Cleo",
        "DeadFish",
        "DeeJayAhmed",
        "ELiTE",
        "EMBER",
        "eSc",
        "EVO",
        "FGT",
        "FUM",
        "GERMini",
        "HAiKU",
        "Hi10",
        "ION10",
        "JacobSwaggedUp",
        "JIVE",
        "Judas",
        "LOAD",
        "MeGusta",
        "Mr.Deadpool",
        "mSD",
        "NemDiggers",
        "neoHEVC",
        "NhaNc3",
        "NOIVTC",
        "PlaySD",
        "playXD",
        "project-gxs",
        "PSA",
        "QaS",
        "Ranger",
        "RAPiDCOWS",
        "Raze",
        "Reaktor",
        "REsuRRecTioN",
        "RMTeam",
        "ROBOTS",
        "SpaceFish",
        "SPASM",
        "SSA",
        "Telly",
        "Tenrai-Sensei",
        "TM",
        "Trix",
        "URANiME",
        "VipapkStudios",
        "ViSiON",
        "Wardevil",
        "xRed",
        "XS",
        "YakuboEncodes",
        "YuiSubs",
        "ZKBL",
        "ZmN",
        "ZMNT",
    )
    base_url = "https://nebulance.io"
    upload_url = f"{base_url}/api.php"
    search_url = f"{base_url}/api.php"
    torrent_url = f"{base_url}/torrents.php?id="
    supported_categories = ("TV",)
    tracker_urls = ("tracker.nebulance",)

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.common = Common(config)
        self.api_key = str(self.config["TRACKERS"][self.tracker]["api_key"]).strip()

    async def get_cat_id(self, meta: Meta) -> int:
        return 3 if meta.tv_pack == 1 else 1

    async def edit_desc(self, _meta: Meta) -> None:
        # Leave this in so manual works
        return

    async def upload(self, meta: Meta) -> bool:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        media_dump = await self._read_media_dump(meta)
        files = await self._upload_files(meta)
        data = await self._upload_data(meta, media_dump)
        status = meta.tracker_status.setdefault(self.tracker, {})
        try:
            if meta.debug:
                return await self._debug_upload(meta, media_dump, data, status)
            response = await self._post_upload(files, data)
            return self._handle_upload_response(response, status)
        except Exception as error:
            status["status_message"] = f"data error: Upload failed: {error}"
            return False

    async def _read_media_dump(self, meta: Meta) -> str:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        path = root / ("BD_SUMMARY_00.txt" if meta.bdinfo else "MEDIAINFO.txt")
        async with aiofiles.open(path, encoding="utf-8") as handle:
            value = await handle.read()
        return value if meta.bdinfo else strip_report_by_line(value)

    async def _upload_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        async with aiofiles.open(root / f"[{self.tracker}].torrent", "rb") as handle:
            torrent_bytes = await handle.read()
        return {"file_input": ("torrent.torrent", torrent_bytes, "application/x-bittorrent")}

    async def _upload_data(self, meta: Meta, media_dump: str) -> dict[str, Any]:
        return {
            "action": "upload",
            "api_key": self.api_key,
            "tvmazeid": self._tvmaze_upload_id(meta.tvmaze_id),
            "mediainfo": media_dump,
            "category": await self.get_cat_id(meta),
            "ignoredupes": "1",
        }

    @staticmethod
    def _tvmaze_upload_id(value: Any) -> str | int:
        return "" if not value else int(value)

    async def _debug_upload(self, meta: Meta, media_dump: str, data: dict[str, Any], status: dict[str, Any]) -> bool:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        debug_path = root / f"{self.tracker}_MEDIAINFO.txt"
        async with aiofiles.open(debug_path, "w", newline="", encoding="utf-8") as handle:
            await handle.write(media_dump)
        logger.info(f"{self.tracker}: [green]Final MediaInfo payload written to {debug_path}[/green]")
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        status["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _post_upload(self, files: dict[str, tuple[str, bytes, str]], data: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(url=self.upload_url, files=files, data=data)

    def _handle_upload_response(self, response: httpx.Response, status: dict[str, Any]) -> bool:
        if response.status_code not in {200, 201}:
            status["status_message"] = {
                "error": f"Unexpected status code: {response.status_code}",
                "response_content": response.text,
            }
            return False
        try:
            response_data = response.json()
        except json.JSONDecodeError:
            status["status_message"] = f"data error: {self.tracker} json decode error, the API is probably down"
            return False
        return self._record_upload_success(response_data, status)

    def _record_upload_success(self, response_data: Any, status: dict[str, Any]) -> bool:
        status["status_message"] = response_data
        if not isinstance(response_data, dict):
            return True
        torrent_id = self._torrent_id_from_link(str(response_data.get("link", "")))
        if torrent_id:
            status["torrent_id"] = torrent_id
        return True

    def _torrent_id_from_link(self, link: str) -> str:
        match = re.search(rf"{re.escape(self.base_url)}/torrents\.php\?id=(\d+)", link)
        return match.group(1) if match else ""

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self._category_policy_passes(meta):
            return False
        if not await self._language_policy_passes(meta):
            return False
        if meta.valid_mi is False:
            logger.info(f"{self.tracker}: [bold red]No unique ID in mediainfo, skipping {self.tracker} upload.")
            return False
        if meta.is_disc:
            self._log_attended(meta, "does not allow raw discs")
            return False
        return True

    async def _category_policy_passes(self, meta: Meta) -> bool:
        if meta.category == "TV":
            return True
        if not meta.tvmaze_id:
            self._log_attended(meta, f"Only TV Is allowed at {self.tracker}")
            return False
        return self._confirm_tv_movie(meta)

    def _confirm_tv_movie(self, meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        logger.info(f"{self.tracker}: [red]Only TV or TV Movies are allowed at {self.tracker}, this has a tvmaze ID[/red]")
        return bool(cli_ui.ask_yes_no("Do you want to upload it?", default=False))

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc == "BDMV":
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
        )

    def _log_attended(self, meta: Meta, message: str) -> None:
        if not meta.unattended:
            logger.info(f"{self.tracker}: [red]{message}[/red]")

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        params = self._search_params(meta)
        max_pages = int(self.config["TRACKERS"][self.tracker].get("search_max_pages", 10))
        dupes: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for page in range(max_pages):
                response = await client.get(self._page_url(params, page))
                if self._is_terminal_page_error(response, page):
                    break
                response.raise_for_status()
                items = self._search_items(response.json())
                if not items:
                    break
                dupes.extend(self._matching_items(meta, items))
        return dupes

    def _search_params(self, meta: Meta) -> dict[str, Any]:
        params: dict[str, Any] = {"action": "search", "api_key": self.api_key}
        season = self._search_season(meta)
        if season > 0:
            params["season"] = season
        params.update(self._search_identity(meta))
        params["tags"] = [meta.resolution]
        params["per_page"] = 100
        return params

    @staticmethod
    def _search_season(meta: Meta) -> int:
        season = meta.season_int
        data = meta.tvmaze_episode_data
        if isinstance(data, dict):
            season = data.get("season_number", season)
        return int(season) if season is not None else 0

    @staticmethod
    def _search_identity(meta: Meta) -> dict[str, Any]:
        if int(meta.tvmaze_id or 0) != 0:
            return {"tvmaze": meta.tvmaze_id}
        if int(meta.imdb_id or 0) != 0:
            return {"imdb": meta.imdb_id}
        return {"series": meta.title}

    def _page_url(self, params: dict[str, Any], page: int) -> str:
        page_params = {**params, "page": page}
        return f"{self.search_url}?{urlencode(page_params, doseq=True)}"

    @classmethod
    def _is_terminal_page_error(cls, response: httpx.Response, page: int) -> bool:
        if response.status_code != 400 or page <= 0:
            return False
        message = cls._error_message(response)
        lowered = message.casefold()
        return "out of range" in lowered and "valid pages" in lowered

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return ""
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error", {})
        return str(error.get("message", "")) if isinstance(error, dict) else ""

    @classmethod
    def _search_items(cls, payload: Any) -> list[dict[str, Any]]:
        items = cls._raw_search_items(payload)
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _raw_search_items(payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("items")
        if isinstance(items, list):
            return items
        result = payload.get("result", {})
        if not isinstance(result, dict):
            return []
        nested = result.get("items", [])
        return nested if isinstance(nested, list) else []

    def _matching_items(self, meta: Meta, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._dupe_from_item(item) for item in items if self._item_matches_resolution(meta, item)]

    @staticmethod
    def _item_matches_resolution(meta: Meta, item: dict[str, Any]) -> bool:
        tags = item.get("tags", [])
        return isinstance(tags, list) and meta.resolution in tags

    def _dupe_from_item(self, item: dict[str, Any]) -> dict[str, Any]:
        files = self._item_files(item.get("file_list", []))
        return {
            "name": str(item.get("rls_name", "")),
            "files": self._files_text(item.get("file_list", []), files),
            "size": int(item.get("size", 0)),
            "link": f"{self.base_url}/torrents.php?id={item.get('group_id', '')}",
            "file_count": len(files) if files else 1,
            "download": str(item.get("download", "")),
        }

    @staticmethod
    def _item_files(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _files_text(raw_value: Any, files: list[Any]) -> str:
        if files:
            return ", ".join(str(item) for item in files)
        return str(raw_value)

    async def get_name(self, meta: Meta) -> str:
        return meta.title
