# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.domain_models.release_description import base_description
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder


class BitHDTV:
    """
    BHDTV Private Torrent Tracker
    """

    auth_type = "other_api"
    tracker = "BITHDTV"
    display_name = "BitHDTV"
    allows_bloated_audio = True
    source_flag = "BIT-HDTV"
    banned_groups = ()
    base_url = "https://www.bit-hdtv.com"
    upload_url = f"{base_url}/takeupload.php"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @staticmethod
    def _is_tv_episode(meta: Meta) -> bool:
        return meta.category == "TV" and not meta.tv_pack

    async def _sub_category_id(self, meta: Meta) -> str:
        if meta.category == "MOVIE":
            return await self.get_type_movie_id(meta)
        if self._is_tv_episode(meta):
            return await self.get_type_tv_id(meta.type or "")
        return await self.get_type_tv_pack_id(meta.type or "")

    @staticmethod
    def _temp_path(meta: Meta, filename: str) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / filename

    async def _metadata_dumps(
        self, meta: Meta
    ) -> tuple[str | None, str | None]:
        if meta.bdinfo:
            path = self._temp_path(meta, "BD_SUMMARY_00.txt")
            async with aiofiles.open(path, encoding="utf-8") as handle:
                return None, await handle.read()
        path = self._temp_path(meta, "MEDIAINFO_CLEANPATH.txt")
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read(), None

    async def _description_text(self, meta: Meta) -> str:
        path = self._temp_path(meta, f"[{self.tracker}]DESCRIPTION.txt")
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    def _short_mediainfo(meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return ""
        filelist = cast(list[str], meta.filelist or [])
        video = filelist[0] if filelist else (meta.path or "")
        return DescriptionBuilder.format_short_mediainfo_json(
            meta.mediainfo, video
        )

    @staticmethod
    def _external_url(meta: Meta) -> str:
        if meta.category == "TV":
            return f"https://www.tvmaze.com/shows/{meta.tvmaze_id}"
        return str(meta.imdb_info.get("imdb_url", ""))

    async def _upload_data(
        self,
        meta: Meta,
        cat_id: str,
        sub_cat_id: str,
        resolution_id: str,
        mi_dump: str | None,
        bd_dump: str | None,
        desc: str,
    ) -> dict[str, Any]:
        media_info = self._short_mediainfo(meta)
        return {
            "api_key": str(
                self.config["TRACKERS"][self.tracker]["api_key"]
            ).strip(),
            "name": await self.get_name(meta),
            "mediainfo": mi_dump if bd_dump is None else bd_dump,
            "cat": cat_id,
            "subcat": sub_cat_id,
            "resolution": resolution_id,
            "sdescr": " ",
            "descr": (
                media_info
                if bd_dump is None
                else "Disc so Check Mediainfo dump "
            ),
            "screen": desc,
            "url": self._external_url(meta),
            "format": "json",
        }

    async def _torrent_files(
        self, meta: Meta
    ) -> dict[str, tuple[str, bytes, str]]:
        torrent_path = self._temp_path(meta, f"[{self.tracker}].torrent")
        async with aiofiles.open(torrent_path, "rb") as handle:
            torrent_bytes = await handle.read()
        return {
            "file": (
                torrent_path.name,
                torrent_bytes,
                "application/x-bittorrent",
            )
        }

    async def _post_upload(
        self,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            return await client.post(
                url=self.upload_url, data=data, files=files
            )

    def _parsed_response(
        self,
        meta: Meta,
        response: httpx.Response,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not response:
            return None
        try:
            parsed = response.json()
            meta.tracker_status[self.tracker]["status_message"] = parsed
            return (
                cast(dict[str, Any], parsed)
                if isinstance(parsed, dict)
                else None
            )
        except Exception:
            logger.info(
                f"{self.tracker}: [cyan]It may have uploaded, go check"
            )
            logger.info(Redaction.redact_private_info(data))
            traceback.print_exc()
            return None

    @staticmethod
    def _view_url(parsed: dict[str, Any] | None) -> str | None:
        if parsed is None:
            return None
        data_block = parsed.get("data")
        if not isinstance(data_block, dict) or "view" not in data_block:
            return None
        return str(data_block["view"])

    async def _seed_uploaded_torrent(
        self,
        common: Common,
        meta: Meta,
        view_url: str | None,
    ) -> bool:
        if view_url is None:
            return False
        announce = self.config["TRACKERS"][self.tracker].get("my_announce_url")
        if not announce:
            return False
        await common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            announce,
            view_url,
        )
        return True

    async def _debug_upload(
        self,
        common: Common,
        meta: Meta,
        data: dict[str, Any],
    ) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        debug_tracker = f"{self.tracker}_DEBUG"
        await common.create_torrent_for_upload(
            meta,
            debug_tracker,
            debug_tracker,
            announce_url="https://fake.tracker",
        )
        return True

    async def upload(self, meta: Meta) -> bool:
        common = Common(config=self.config)
        await common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )
        await self.edit_desc(meta)
        cat_id = await self.get_cat_id(meta)
        sub_cat_id = await self._sub_category_id(meta)
        resolution_id = await self.get_res_id(meta.resolution)
        mi_dump, bd_dump = await self._metadata_dumps(meta)
        desc = await self._description_text(meta)
        data = await self._upload_data(
            meta,
            cat_id,
            sub_cat_id,
            resolution_id,
            mi_dump,
            bd_dump,
            desc,
        )
        files = await self._torrent_files(meta)
        if meta.debug:
            return await self._debug_upload(common, meta, data)
        response = await self._post_upload(data, files)
        parsed = self._parsed_response(meta, response, data)
        return await self._seed_uploaded_torrent(
            common,
            meta,
            self._view_url(parsed),
        )

    async def get_cat_id(self, meta: Meta) -> str:
        category_id = "0"
        if meta.category == "MOVIE":
            category_id = "7"
        elif meta.tv_pack:
            category_id = "12"
        else:
            # must be tv episode
            category_id = "10"
        return category_id

    @staticmethod
    def _movie_disc_type_id(meta: Meta) -> str:
        return "46" if meta.three_d else "2"

    @staticmethod
    def _movie_remux_type_id(meta: Meta) -> str:
        if "265" in meta.name:
            return "48"
        return "45" if meta.three_d else "2"

    @staticmethod
    def _movie_encode_type_id(meta: Meta) -> str:
        if "265" in meta.name:
            return "43"
        return "44" if meta.three_d else "1"

    def _movie_type_handler(
        self, release_type: str
    ) -> Callable[[Meta], str] | None:
        return {
            "DISC": self._movie_disc_type_id,
            "REMUX": self._movie_remux_type_id,
            "ENCODE": self._movie_encode_type_id,
        }.get(release_type)

    async def get_type_movie_id(self, meta: Meta) -> str:
        handler = self._movie_type_handler(str(meta.type))
        if handler is not None:
            return handler(meta)
        return {"HDTV": "6", "WEBDL": "5", "WEBRIP": "5"}.get(
            str(meta.type), "0"
        )

    async def get_type_tv_id(self, type: str) -> str:
        return {
            "HDTV": "7",
            "WEBDL": "8",
            "WEBRIP": "8",
            # 'WEBRIP': '55',
            # 'SD': '59',
            "ENCODE": "10",
            "REMUX": "11",
            "DISC": "12",
        }.get(type, "0")

    async def get_type_tv_pack_id(self, type: str) -> str:
        return {
            "HDTV": "13",
            "WEBDL": "14",
            "WEBRIP": "8",
            # 'WEBRIP': '55',
            # 'SD': '59',
            "ENCODE": "16",
            "REMUX": "17",
            "DISC": "18",
        }.get(type, "0")

    async def get_res_id(self, resolution: str) -> str:
        return {"2160p": "4", "1080p": "3", "1080i": "2", "720p": "1"}.get(
            resolution, "10"
        )

    async def edit_desc(self, meta: Meta) -> None:
        base = base_description(meta)
        parts: list[str] = [base.replace("[img=250]", "[img=250x250]")]
        images = meta.image_list or []
        if len(images) > 0:
            for each in range(len(images)):
                web_url = images[each]["web_url"]
                img_url = images[each]["img_url"]
                parts.append(f"[url={web_url}][img]{img_url}[/img][/url] ")
        async with aiofiles.open(
            f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt",
            "w",
            encoding="utf-8",
        ) as desc:
            await desc.write("".join(parts))
        return

    async def search_existing(self, _meta: dict[str, Any]) -> list[str]:
        logger.info(f"{self.tracker}: [red]Dupes must be checked Manually")
        return []

    async def get_name(self, meta: Meta) -> str:
        return (
            meta.name.replace(" ", ".")
            .replace(":.", ".")
            .replace(":", ".")
            .replace("DD+", "DDP")
        )
