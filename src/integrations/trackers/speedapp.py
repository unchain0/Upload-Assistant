# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import base64
import re
import unicodedata
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import console, logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import (
    DescriptionBuilder,
    html_to_bbcode,
)

Config = dict[str, Any]


class SpeedApp:
    """
    SPD Private Torrent Tracker
    """

    base_url = "https://speedapp.io"

    auth_type = "other_api"
    url = f"{base_url}"
    tracker = "SPEEDAPP"
    display_name = "SpeedApp"
    banned_groups = ()
    upload_url = f"{base_url}/api/upload"
    torrent_url = f"{base_url}/browse/"
    banned_url = f"{base_url}/api/torrent/release-group/blacklist"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("speedapp",)
    allowed_bloated_audio_languages = ("ro",)

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.common = Common(config)
        api_key = str(self.config["TRACKERS"][self.tracker]["api_key"])
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": "Upload-Assistant",
                "accept": "application/json",
                "Authorization": api_key,
            },
            timeout=30.0,
        )

    async def get_cat_id(self, meta: Meta) -> int:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        romanian = self._has_romanian_language(meta)
        special = self._special_category(meta, romanian)
        if special is not None:
            return special
        return self._base_category(meta, romanian)

    @classmethod
    def _special_category(cls, meta: Meta, romanian: bool) -> int | None:
        category = str(meta.category)
        origin = cls._origin_category(meta, category)
        if origin is not None:
            return origin
        documentary = cls._documentary_category(meta, romanian)
        if documentary is not None:
            return documentary
        return 3 if meta.anime else None

    def _base_category(self, meta: Meta, romanian: bool) -> int:
        resolver = {
            "TV": self._tv_category,
            "MOVIE": self._movie_category,
            "BOOK": self._book_category,
            "GAME": self._game_category,
            "MUSIC": self._music_category,
        }.get(str(meta.category))
        return 0 if resolver is None else resolver(meta, romanian)

    @classmethod
    def _has_romanian_language(cls, meta: Meta) -> bool:
        values = [
            *cls._language_values(meta.subtitle_languages),
            *cls._language_values(meta.audio_languages),
        ]
        return any(value.casefold() == "romanian" for value in values)

    @staticmethod
    def _language_values(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _origin_category(meta: Meta, category: str) -> int | None:
        countries = (
            meta.origin_country
            if isinstance(meta.origin_country, list)
            else []
        )
        if "RO" not in countries:
            return None
        return {"TV": 60, "MOVIE": 59}.get(category)

    @classmethod
    def _documentary_category(cls, meta: Meta, romanian: bool) -> int | None:
        if not cls._is_documentary(meta):
            return None
        return 63 if romanian else 9

    @staticmethod
    def _is_documentary(meta: Meta) -> bool:
        text = f"{meta.genres} {meta.keywords}".casefold()
        return "documentary" in text

    @classmethod
    def _tv_category(cls, meta: Meta, romanian: bool) -> int:
        tier = cls._tv_tier(meta)
        return {
            ("pack", False): 41,
            ("pack", True): 66,
            ("sd", False): 45,
            ("sd", True): 46,
            ("hd", False): 43,
            ("hd", True): 44,
        }[(tier, romanian)]

    @staticmethod
    def _tv_tier(meta: Meta) -> str:
        if meta.tv_pack:
            return "pack"
        return "sd" if meta.sd else "hd"

    @classmethod
    def _movie_category(cls, meta: Meta, romanian: bool) -> int:
        media_type = str(meta.type)
        if cls._is_uhd_non_disc(meta, media_type):
            return cls._romanian_choice(57, 61, romanian)
        category = {
            "REMUX": (29, 8),
            "WEBDL": (29, 8),
            "WEBRIP": (29, 8),
            "HDTV": (29, 8),
            "ENCODE": (29, 8),
            "DISC": (24, 17),
            "SD": (35, 10),
        }.get(media_type)
        return cls._category_choice(category, romanian)

    @staticmethod
    def _is_uhd_non_disc(meta: Meta, media_type: str) -> bool:
        return meta.resolution == "2160p" and media_type != "DISC"

    @staticmethod
    def _romanian_choice(
        romanian_id: int, default_id: int, romanian: bool
    ) -> int:
        return romanian_id if romanian else default_id

    @classmethod
    def _category_choice(
        cls, category: tuple[int, int] | None, romanian: bool
    ) -> int:
        if category is None:
            return 0
        return cls._romanian_choice(category[0], category[1], romanian)

    @staticmethod
    def _book_category(_meta: Meta, _romanian: bool) -> int:
        return 6

    @staticmethod
    def _game_category(meta: Meta, _romanian: bool) -> int:
        return 52 if meta.console_game else 11

    @staticmethod
    def _music_category(_meta: Meta, _romanian: bool) -> int:
        return 5

    async def get_file_info(self, meta: Meta) -> tuple[str | None, str | None]:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        if meta.bdinfo:
            return None, await self._read_text(root / "BD_SUMMARY_00.txt")
        return await self._read_text(root / "MEDIAINFO_CLEANPATH.txt"), None

    @staticmethod
    async def _read_text(path: Path) -> str:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    async def get_screenshots(self, meta: Meta) -> list[str]:
        images = (
            cast(list[dict[str, Any]], meta.menu_images)
            + meta.image_list
            + meta.spectrograms_images
            + meta.dynamic_hdr_plot_images
        )
        return [image["raw_url"] for image in images if image.get("raw_url")]

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        response = await self.session.get(
            url=f"{self.base_url}/api/torrent",
            params=self._search_params(meta),
            headers=self.session.headers,
        )
        response.raise_for_status()
        return [
            entry
            for item in self._search_payload(response)
            if (entry := self._search_entry(item)) is not None
        ]

    @staticmethod
    def _search_params(meta: Meta) -> dict[str, str]:
        if meta.imdb_id:
            return {"imdbId": str(meta.imdb_tt)}
        title = (
            f"{meta.artist} {meta.title}"
            if meta.category == "MUSIC"
            else str(meta.title)
        )
        return {
            "search": title.replace(":", "").replace("'", "").replace(",", "")
        }

    @staticmethod
    def _search_payload(response: httpx.Response) -> list[Any]:
        payload = response.json()
        return payload if isinstance(payload, list) else []

    @classmethod
    def _search_entry(cls, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not name:
            return None
        torrent_id = item.get("id")
        return {
            "name": str(name),
            "size": item.get("size"),
            "link": f"{cls.torrent_url}{torrent_id}/",
        }

    async def search_channel(self, meta: Meta) -> int | None:
        requested = self._requested_channel(meta)
        direct = self._direct_channel_id(requested)
        if direct is not None:
            return direct
        try:
            return await self._lookup_channel(str(requested))
        except Exception as error:
            logger.error(
                f"{self.tracker}: [bold red]Unexpected error: {escape(str(error))}[/bold red]"
            )
            console.print_exception()
            return None

    def _requested_channel(self, meta: Meta) -> Any:
        return meta.spd_channel or self.config["TRACKERS"][self.tracker].get(
            "channel", ""
        )

    @staticmethod
    def _direct_channel_id(value: Any) -> int | None:
        if not value:
            return 1
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    async def _lookup_channel(self, tag: str) -> int | None:
        response = await self.session.get(
            url=f"{self.url}/api/channel",
            params={"search": tag},
            headers=self.session.headers,
        )
        if response.status_code != 200:
            logger.info(
                f"{self.tracker}: [bold red]HTTP request failed. Status: {response.status_code}[/bold red]"
            )
            return None
        channel_id = self._matching_channel_id(response.json(), tag)
        if channel_id is None:
            logger.info(
                f"{self.tracker}: [{self.tracker}]Could not find the channel ID matching your input. Please check if you entered it correctly."
            )
        return channel_id

    @classmethod
    def _matching_channel_id(cls, payload: Any, tag: str) -> int | None:
        if not isinstance(payload, list):
            return None
        return next(
            (
                channel_id
                for entry in payload
                if (channel_id := cls._channel_entry_id(entry, tag))
                is not None
            ),
            None,
        )

    @staticmethod
    def _channel_entry_id(entry: Any, tag: str) -> int | None:
        if not isinstance(entry, dict) or entry.get("tag") != tag:
            return None
        value = entry.get("id")
        return int(value) if value else None

    async def edit_desc(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)

        return await builder.general_description_generator(
            meta,
            audio_spectrogram=False,
            bluray=False,
            book=False,
            custom_signature=False,
            description=False,
            dynamic_hdr_plot=False,
            game=False,
            mediainfo=False,
            menu_screenshots=False,
            nfo=False,
            screenshots=False,
            signature=f"\n[url=https://github.com/wastaken7/Upload-Assistant]{meta.ua_signature}[/url]",
        )

    async def get_name(self, meta: Meta) -> str:
        if not self._use_metadata_name():
            return self._fallback_name(meta)
        return self._sanitize_release_name(self._metadata_name(meta))

    @staticmethod
    def _fallback_name(meta: Meta) -> str:
        return (
            str(meta.scene_name)
            if meta.scene_name
            else str(meta.basename_no_ext)
        )

    @staticmethod
    def _metadata_name(meta: Meta) -> str:
        return (
            str(meta.scene_name)
            if meta.scene_name
            else str(meta.clean_name or "")
        )

    def _use_metadata_name(self) -> bool:
        return bool(
            self.config["TRACKERS"][self.tracker].get(
                "use_metadata_name", False
            )
        )

    @staticmethod
    def _sanitize_release_name(name: str) -> str:
        value = (
            name.replace("DD+", "DDP")
            .replace("DTS:", "DTS-")
            .replace("HDR10+", "HDR10P")
        )
        value = unicodedata.normalize("NFD", value)
        value = "".join(
            char
            for char in value
            if char.isascii() and (char.isalnum() or char in (" ", ".", "-"))
        )
        return value.replace("!", "")

    async def encode_to_base64(self, file_path: str) -> str:
        async with aiofiles.open(file_path, "rb") as binary_file:
            binary_file_data = await binary_file.read()
            base64_encoded_data = base64.b64encode(binary_file_data)
            return base64_encoded_data.decode("utf-8")

    async def get_nfo(self, meta: Meta) -> str | None:
        nfo_dir = release_temp_dir(meta.base_dir, meta.uuid)
        nfo_files = list(nfo_dir.glob("*.nfo"))

        if nfo_files:
            return await self.encode_to_base64(str(nfo_files[0]))

        return None

    def get_requirements(self, meta: Meta) -> str:
        requirements_minimum = html_to_bbcode(meta.requirements_minimum)
        requirements_recommended = html_to_bbcode(
            meta.requirements_recommended
        )
        requirements = ""

        if requirements_minimum:
            requirements += requirements_minimum
        if requirements_recommended:
            requirements += f"\n{requirements_recommended}"

        return re.sub(r"\[.+?\]", "", requirements)

    async def fetch_data(self, meta: Meta) -> dict[str, Any]:
        data = await self._base_upload_data(meta)
        await self._apply_category_upload_data(data, meta)
        await self._attach_torrent_data(data, meta)
        self._redact_debug_payload(data, meta)
        return data

    async def _base_upload_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "coverPhotoUrl": meta.backdrop,
            "description": str(meta.genres),
            "name": await self.get_name(meta),
            "nfo": await self.get_nfo(meta),
            "poster": meta.artwork_url,
            "technicalDetails": await self.edit_desc(meta),
            "screenshots": await self.get_screenshots(meta),
            "type": await self.get_cat_id(meta),
        }

    async def _apply_category_upload_data(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        if meta.category in {"MOVIE", "TV"}:
            await self._apply_video_upload_data(data, meta)
            return
        if meta.category == "GAME" and meta.console_game is False:
            requirements = self.get_requirements(meta)
            if requirements:
                data["systemRequirements"] = requirements

    async def _apply_video_upload_data(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        media_info, bd_info = await self.get_file_info(meta)
        data["plot"] = (meta.overview_meta or meta.overview,)
        data["bdInfo"] = bd_info
        data["media_info"] = media_info
        data["url"] = self._imdb_url(meta)

    @staticmethod
    def _imdb_url(meta: Meta) -> str:
        return (
            str(meta.imdb_info.get("imdb_url", ""))
            if isinstance(meta.imdb_info, dict)
            else ""
        )

    async def _attach_torrent_data(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        tracker_config = self.config.get("TRACKERS", {}).get(self.tracker, {})
        torrent_filename = await self.common.get_torrent_filename(
            meta, tracker_config
        )
        torrent_path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"{torrent_filename}.torrent"
        )
        data["file"] = await self.encode_to_base64(str(torrent_path))

    @staticmethod
    def _redact_debug_payload(data: dict[str, Any], meta: Meta) -> None:
        if meta.debug is not True:
            return
        data["file"] = f"{str(data['file'])[:50]}...[DEBUG MODE]"
        if data.get("nfo"):
            data["nfo"] = f"{str(data['nfo'])[:50]}...[DEBUG MODE]"

    async def upload(self, meta: Meta) -> bool | None:
        data = await self.fetch_data(meta)
        status = meta.tracker_status.setdefault(self.tracker, {})
        channel = await self.search_channel(meta)
        if channel is None:
            meta.skipping = self.tracker
            return None
        data["channel"] = str(channel)
        if meta.debug:
            return await self._debug_upload(meta, data, status)
        return await self._upload_release(meta, data, status)

    async def _debug_upload(
        self, meta: Meta, data: dict[str, Any], status: dict[str, Any]
    ) -> bool:
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

    async def _upload_release(
        self, meta: Meta, data: dict[str, Any], status: dict[str, Any]
    ) -> bool:
        try:
            response = await self._post_upload(data)
            return await self._handle_upload_response(meta, status, response)
        except httpx.HTTPStatusError as error:
            status["status_message"] = (
                f"data error: HTTP {error.response.status_code} - {error.response.text}"
            )
            return False
        except httpx.TimeoutException:
            status["status_message"] = (
                f"data error: Request timed out after {self.session.timeout.write} seconds"
            )
            return False
        except httpx.RequestError as error:
            status["status_message"] = self._request_error_message(error)
            return False
        except Exception as error:
            status["status_message"] = (
                f"data error: It may have uploaded, go check. Error: {error!r}.\nResponse: no response"
            )
            return False

    async def _post_upload(self, data: dict[str, Any]) -> httpx.Response:
        response = await self.session.post(
            url=self.upload_url, json=data, headers=self.session.headers
        )
        response.raise_for_status()
        return response

    async def _handle_upload_response(
        self, meta: Meta, status: dict[str, Any], response: httpx.Response
    ) -> bool:
        payload = self._response_mapping(response)
        if not self._upload_succeeded(payload):
            status["status_message"] = f"data error: {payload}"
            return False
        status["status_message"] = "Torrent uploaded successfully."
        if "downloadUrl" not in payload:
            status["status_message"] = (
                f"data error: No downloadUrl in response, check manually if it uploaded. Response: \n{payload}"
            )
            return False
        torrent_id = self._torrent_id(payload)
        if torrent_id:
            status["torrent_id"] = torrent_id
        await self.common.download_tracker_torrent(
            meta,
            tracker=self.tracker,
            headers={
                "Authorization": str(
                    self.config["TRACKERS"][self.tracker]["api_key"]
                )
            },
            downurl=f"{self.url}/api/torrent/{torrent_id}/download",
        )
        return True

    @staticmethod
    def _response_mapping(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _upload_succeeded(payload: dict[str, Any]) -> bool:
        return payload.get("status") is True and payload.get("error") is False

    @staticmethod
    def _torrent_id(payload: dict[str, Any]) -> str:
        torrent = payload.get("torrent")
        return str(torrent.get("id", "")) if isinstance(torrent, dict) else ""

    @staticmethod
    def _request_error_message(error: httpx.RequestError) -> str:
        response = getattr(error, "response", None)
        response_info = (
            getattr(response, "text", "no response")
            if response is not None
            else "no response"
        )
        return f"data error: Unable to upload. Error: {error!r}.\nResponse: {response_info}"
