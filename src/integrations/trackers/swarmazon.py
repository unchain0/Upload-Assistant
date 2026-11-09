# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import console, logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common

Config = dict[str, Any]


class Swarmazon:
    """
    SWARMAZON is a Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    base_url = "https://swarmazon.club"

    auth_type = "other_api"
    tracker = "SWARMAZON"
    display_name = "Swarmazon"
    allows_bloated_audio = True
    source_flag = "Swarmazon"
    banned_groups = ("",)
    upload_url = f"{base_url}/api/upload.php"
    forum_link = f"{base_url}/php/forum.php?forum_page=2-swarmazon-rules"
    search_url = f"{base_url}/api/search.php"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        self.config: Config = config

    async def get_type_id(self, media_type: str) -> str:
        return {"BluRay": "3", "Web": "1", "DVD": "2"}.get(media_type, "0")

    async def upload(self, meta: Meta) -> bool:
        common = Common(config=self.config)
        await common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )
        await self.edit_desc(meta)
        category_id, type_id = await self._upload_category(meta)
        mi_dump, bd_dump = await self._read_media_dump(meta)
        desc, torrent_bytes = await self._read_upload_files(meta)
        if bd_dump:
            desc = f"{desc}\n\n{bd_dump}"
            mi_dump = ""
        data = await self._upload_data(
            meta, category_id, type_id, desc, mi_dump
        )
        files = {"torrent": (f"{meta.name}.torrent", torrent_bytes)}
        return await self._submit_upload(meta, common, data, files)

    async def _upload_category(self, meta: Meta) -> tuple[str, str]:
        standard = await self._standard_upload_category(meta)
        if standard is not None:
            return standard
        return self._anime_upload_category(meta)

    async def _standard_upload_category(
        self, meta: Meta
    ) -> tuple[str, str] | None:
        if meta.category == "MOVIE":
            return "1", await self.get_type_id(str(meta.source))
        if meta.category == "TV":
            return "2", "6" if meta.tv_pack else "5"
        return None

    @staticmethod
    def _anime_upload_category(meta: Meta) -> tuple[str, str]:
        if not meta.mal_id:
            return "", ""
        demographics = {
            "Shounen": "27",
            "Seinen": "28",
            "Shoujo": "29",
            "Josei": "30",
            "Kodomo": "31",
            "Mina": "47",
        }
        return "7", demographics.get(meta.demographic or "Mina", "47")

    async def _read_media_dump(
        self, meta: Meta
    ) -> tuple[str | None, str | None]:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        if meta.bdinfo:
            async with aiofiles.open(
                root / "BD_SUMMARY_00.txt", encoding="utf-8"
            ) as handle:
                return None, await handle.read()
        async with aiofiles.open(
            root / "MEDIAINFO.txt", encoding="utf-8"
        ) as handle:
            return await handle.read(), None

    async def _read_upload_files(self, meta: Meta) -> tuple[str, bytes]:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        async with aiofiles.open(
            root / f"[{self.tracker}]DESCRIPTION.txt", encoding="utf-8"
        ) as handle:
            desc = await handle.read()
        async with aiofiles.open(
            root / f"[{self.tracker}].torrent", "rb"
        ) as handle:
            torrent_bytes = await handle.read()
        return desc, torrent_bytes

    async def _upload_data(
        self,
        meta: Meta,
        category_id: str,
        type_id: str,
        desc: str,
        mi_dump: str | None,
    ) -> dict[str, Any]:
        return {
            "api_key": str(
                self.config["TRACKERS"][self.tracker]["api_key"]
            ).strip(),
            "name": await self.get_name(meta),
            "category_id": category_id,
            "type_id": type_id,
            "media_ref": f"tt{meta.imdb}",
            "description": desc,
            "media_info": mi_dump,
        }

    async def _submit_upload(
        self,
        meta: Meta,
        common: Common,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes]],
    ) -> bool:
        if meta.debug:
            return await self._debug_upload(meta, common, data)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.upload_url, data=data, files=files
                )
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [red]Request failed with error: {error}"
            )
            return False
        return await self._handle_upload_response(meta, common, data, response)

    async def _handle_upload_response(
        self,
        meta: Meta,
        common: Common,
        data: dict[str, Any],
        response: httpx.Response,
    ) -> bool:
        try:
            payload = response.json()
        except Exception:
            logger.error(
                f"{self.tracker}: [red]Error! It may have uploaded, go check"
            )
            logger.info(Redaction.redact_private_info(data))
            console.print_exception()
            return False
        if not isinstance(payload, dict) or not payload.get("success"):
            logger.info(f"{self.tracker}: [red]Did not upload successfully")
            logger.info(payload)
            return False
        return await self._handle_success(meta, common, payload)

    async def _handle_success(
        self, meta: Meta, common: Common, payload: dict[str, Any]
    ) -> bool:
        link = str(payload.get("link", "")).strip()
        if not link:
            logger.info(f"{self.tracker}: [red]No Link in Response")
            return False
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = (
            link
        )
        announce_url = str(
            self.config["TRACKERS"][self.tracker].get("announce_url", "")
        )
        await common.create_torrent_ready_to_seed(
            meta, self.tracker, self.source_flag, announce_url, link
        )
        return True

    async def _debug_upload(
        self, meta: Meta, common: Common, data: dict[str, Any]
    ) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        await common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def edit_desc(self, meta: Meta) -> None:
        from src.domain_models.release_description import base_description

        base = base_description(meta)

        parts: list[str] = [base]
        images = meta.image_list
        if images:
            parts.append("[center]")
            for image in images:
                web_url = image.get("web_url")
                img_url = image.get("img_url")
                if not web_url or not img_url:
                    continue
                parts.append(f"[url={web_url}][img=720]{img_url}[/img][/url]")
            parts.append("[/center]")
        parts.append(
            f"\n[center][url={self.forum_link}]Simplicity, Socializing and Sharing![/url][/center]"
        )

        async with aiofiles.open(
            f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt",
            "w",
            encoding="utf-8",
        ) as desc:
            await desc.write("".join(parts))
        return

    async def search_existing(self, meta: Meta) -> list[str]:
        params = self._search_params(meta)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(self.search_url, params=params)
            response.raise_for_status()
            payload = response.json()
        return self._search_names(payload)

    def _search_params(self, meta: Meta) -> dict[str, str]:
        params = {
            "api_key": str(
                self.config["TRACKERS"][self.tracker]["api_key"]
            ).strip()
        }
        if not meta.imdb_id:
            params["filter"] = (
                f"{meta.title}{meta.season}"
                if meta.category == "TV"
                else meta.title
            )
            return params
        params["media_ref"] = f"tt{meta.imdb}"
        params["filter"] = (
            str(meta.season) if meta.category == "TV" else meta.resolution
        )
        return params

    @classmethod
    def _search_names(cls, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("data", [])
        if not isinstance(items, list):
            return []
        return [
            name for item in items if (name := cls._search_item_name(item))
        ]

    @staticmethod
    def _search_item_name(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        return str(item.get("name") or "")

    async def get_name(self, meta: Meta) -> str:
        return meta.name
