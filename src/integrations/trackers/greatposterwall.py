# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
import unicodedata
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import httpx
from bs4 import BeautifulSoup
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.image_hosts.rehosting import RehostImagesManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder


class GreatPosterWall:
    """
    GPW Private Torrent Tracker
    """

    auth_type = "other_api"
    tracker = "GREATPOSTERWALL"
    display_name = "GreatPosterWall"
    allows_bloated_audio = True
    source_flag = "GreatPosterWall"
    base_url = "https://greatposterwall.com"
    auth_token = None
    tmdb_data: dict[str, Any]
    banned_groups = (
        "ALT",
        "aXXo",
        "BATWEB",
        "BitsTV",
        "BlackTV",
        "BMDRu",
        "BRrip",
        "CM8",
        "CrEwSaDe",
        "CTFOH",
        "CTRLHD",
        "DDHDTV",
        "DNL",
        "DreamHD",
        "ENTHD",
        "FaNGDiNG0",
        "FGT",
        "GPTHD",
        "HD2DVD",
        "HDT",
        "HDTime",
        "Huawei",
        "ION10",
        "iPlanet",
        "KiNGDOM",
        "Leffe",
        "mHD",
        "MiniHD",
        "MOMOWEB",
        "Mp4Ba",
        "mSD",
        "NhaNc3",
        "nHD",
        "nikt0",
        "NSBC",
        "nSD",
        "NukeHD",
        "OFT",
        "PRODJi",
        "RARBG",
        "RDN",
        "SANTi",
        "SeeHD",
        "SeeWEB",
        "SM737",
        "SonyHD",
        "STUTTERSHIT",
        "TAGWEB",
        "ViSION",
        "VXT",
        "WAF",
        "x0r",
        "Xiaomi",
        "YIFY",
    )
    approved_image_hosts = (
        "kshare",
        "pixhost",
        "pterclub",
        "ilikeshots",
        "imgbox",
    )
    can_rehost_unapproved_images = True
    torrent_url = f"{base_url}/torrents.php?torrentid="
    url_host_mapping: ClassVar = {
        "kshare.club": "kshare",
        "pixhost.to": "pixhost",
        "imgbox.com": "imgbox",
        "img.pterclub.com": "pterclub",
        "yes.ilikeshots.club": "ilikeshots",
    }
    supported_categories = ("MOVIE",)
    tracker_urls = ("https://tracker.greatposterwall.com",)
    group_id: str = ""
    tmdb_localization_requirements: ClassVar = {
        "zh-cn": {
            "main": "credits",
        }
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.tmdb_data = {}
        self.config = config
        self.rehost_images_manager = RehostImagesManager(config)
        self.common = Common(config)
        self.tmdb_manager = TmdbManager(config)
        self.tracker_config = self._tracker_config()
        self.announce = str(self.tracker_config.get("announce_url", ""))
        self.api_key = str(self.tracker_config.get("api_key", ""))

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        tracker_map = cast(dict[str, Any], trackers)
        value = tracker_map.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    async def load_cookies(self, meta: Meta) -> Any:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookie_file = find_cookie_file(
            meta.base_dir, self.tracker, self.config
        )
        if not Path(cookie_file).exists():
            return False

        return await self.common.parse_cookie_file(cookie_file)

    async def load_localized_data(self, meta: Meta) -> None:
        data = meta.tmdb_localized_data
        zh_cn_data = data.get("zh-cn")
        if not zh_cn_data or not zh_cn_data.get("main"):
            raise RuntimeError(
                f"{self.tracker}: Missing TMDB localized data (zh-cn)."
            )

        self.tmdb_data = zh_cn_data.get("main") or {}
        return

    def get_container(self, meta: Meta) -> str:
        container_value = meta.container
        container = container_value if isinstance(container_value, str) else ""
        if container == "m2ts":
            return container
        if container == "vob":
            return "VOB IFO"
        if container in ["avi", "mpg", "mp4", "mkv"]:
            return container.upper()

        return "Other"

    async def get_subtitle(self, meta: Meta) -> list[str]:
        await self._ensure_languages(meta)
        return [
            value.lower()
            for value in self._language_strings(meta.subtitle_languages)
        ]

    async def get_ch_dubs(self, meta: Meta) -> bool:
        await self._ensure_languages(meta)
        chinese_languages = {
            "mandarin",
            "chinese",
            "zh",
            "zh-cn",
            "zh-hans",
            "zh-hant",
            "putonghua",
            "国语",
            "普通话",
        }
        return any(
            value.strip().lower() in chinese_languages
            for value in self._language_strings(meta.audio_languages)
        )

    async def _ensure_languages(self, meta: Meta) -> None:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )

    @staticmethod
    def _language_strings(value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [item for item in values if isinstance(item, str)]

    def get_codec(self, meta: Meta) -> str:
        video_encode = meta.video_encode.strip().lower()
        codec_final = meta.video_codec.strip().lower()

        codec_map = {
            "divx": "DivX",
            "xvid": "XviD",
            "x264": "x264",
            "h.264": "H.264",
            "avc": "H.264",
            "x265": "x265",
            "h.265": "H.265",
            "hevc": "H.265",
        }

        for key, value in codec_map.items():
            if key in video_encode or key in codec_final:
                return value

        return "Other"

    def get_audio_codec(self, meta: Meta) -> str:
        description = meta.audio
        if not isinstance(description, str) or not description:
            return "Outro"
        return self._matched_audio_codec(description)

    @classmethod
    def _matched_audio_codec(cls, description: str) -> str:
        for codec, terms in cls._audio_codec_terms():
            if any(term in description for term in terms):
                return codec
        return "Outro"

    @staticmethod
    def _audio_codec_terms() -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("DTS-X", ("DTS:X",)),
            ("E-AC-3 JOC", ("DD+ 5.1 Atmos", "DD+ 7.1 Atmos")),
            ("TrueHD", ("TrueHD",)),
            ("DTS-HD", ("DTS-HD",)),
            ("PCM", ("LPCM",)),
            ("FLAC", ("FLAC",)),
            ("DTS-ES", ("DTS-ES",)),
            ("DTS", ("DTS",)),
            ("E-AC-3", ("DD+",)),
            ("AC3", ("DD",)),
            ("AAC", ("AAC",)),
            ("Opus", ("Opus",)),
            ("Vorbis", ("VORBIS",)),
            ("MP3", ("MP3",)),
            ("MP2", ("MP2",)),
        )

    def get_title(self, meta: Meta) -> str:
        title = self._localized_title_value()
        return title if title and title != meta.title else ""

    def _localized_title_value(self) -> str:
        value = self.tmdb_data.get("name") or self.tmdb_data.get("title") or ""
        return value if isinstance(value, str) else ""

    def is_approved_image_url(self, image_url: str) -> bool:
        hostname = urlparse(image_url).hostname or ""
        for domain, host_name in self.url_host_mapping.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return host_name in self.approved_image_hosts
        return False

    async def rehost_unapproved_images(self, meta: Meta) -> None:
        """Import public image URLs to GPW's KShare host before the normal host check."""
        images = self._image_entries(meta.image_list)
        if not images:
            return
        if not self.api_key:
            logger.warning(
                "[yellow]GREATPOSTERWALL: cannot rehost images because no API key is configured.[/yellow]"
            )
            return
        meta.image_list = await self._rehost_image_entries(images)

    @staticmethod
    def _image_entries(value: Any) -> list[dict[str, str]]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [
            cast(dict[str, str], item)
            for item in values
            if isinstance(item, dict)
        ]

    async def _rehost_image_entries(
        self, images: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=60) as client:
            return [
                await self._rehost_image_entry(client, image)
                for image in images
            ]

    async def _rehost_image_entry(
        self, client: httpx.AsyncClient, image: dict[str, str]
    ) -> dict[str, str]:
        raw_url = image.get("raw_url", "")
        if not self._needs_image_rehost(raw_url):
            return image
        hosted_url = await self._rehost_image_url(client, raw_url)
        if not hosted_url:
            return image
        result = image.copy()
        result.update(
            {
                "img_url": hosted_url,
                "raw_url": hosted_url,
                "web_url": hosted_url,
            }
        )
        return result

    def _needs_image_rehost(self, raw_url: str) -> bool:
        return raw_url.startswith(
            ("https://", "http://")
        ) and not self.is_approved_image_url(raw_url)

    async def _rehost_image_url(
        self, client: httpx.AsyncClient, raw_url: str
    ) -> str:
        try:
            response = await client.post(
                f"{self.base_url}/api.php",
                params={"action": "img_upload", "api_key": self.api_key},
                data={"urls[]": raw_url},
            )
            return self._hosted_image_url(response, raw_url)
        except (httpx.HTTPError, TypeError, ValueError) as error:
            logger.warning(
                f"[yellow]GREATPOSTERWALL: could not rehost {raw_url}: {error!s}[/yellow]"
            )
            return ""

    def _hosted_image_url(self, response: httpx.Response, raw_url: str) -> str:
        data, body = self._image_response_maps(response)
        hosted_url = self._first_hosted_image_url(body)
        if self._image_response_ok(response, data, hosted_url):
            return hosted_url
        logger.warning(
            f"[yellow]GREATPOSTERWALL: could not rehost {raw_url}: {body.get('Error', 'no image URL returned')}[/yellow]"
        )
        return ""

    @staticmethod
    def _image_response_maps(
        response: httpx.Response,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = response.json()
        data = (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )
        body_value = data.get("response", {})
        body = (
            cast(dict[str, Any], body_value)
            if isinstance(body_value, dict)
            else {}
        )
        return data, body

    @classmethod
    def _first_hosted_image_url(cls, body: dict[str, Any]) -> str:
        files = cls._mapping_items(body.get("files", []))
        return str(files[0].get("name", "")) if files else ""

    @staticmethod
    def _image_response_ok(
        response: httpx.Response, data: dict[str, Any], hosted_url: str
    ) -> bool:
        return (
            response.status_code == 200
            and data.get("status") == 200
            and bool(hosted_url)
        )

    @staticmethod
    def _mapping_items(value: Any) -> list[dict[str, Any]]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    async def check_image_hosts(self, meta: Meta) -> None:
        # Rule: 2.2.1. Screenshots: They have to be saved at kshare.club, pixhost.to, img.pterclub.com, yes.ilikeshots.club, imgbox.com, s3.pterclub.com
        await self.rehost_unapproved_images(meta)
        await self.rehost_images_manager.check_hosts(
            meta,
            self.tracker,
            url_host_mapping=self.url_host_mapping,
            img_host_index=1,
            approved_image_hosts=self.approved_image_hosts,
        )
        return

    async def get_release_desc(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        return await builder.general_description_generator(
            meta,
            bluray=False,
            book=False,
            custom_signature=False,
            game=False,
            logo=False,
            mediainfo=False,
            tv_info=False,
            signature=f"[align=right][url=https://github.com/wastaken7/Upload-Assistant][size=1]{meta.ua_signature}[/size][/url][/align]",
        )

    def get_trailer(self, meta: Meta) -> str:
        youtube = self._tmdb_trailer_key()
        return youtube if youtube else self._meta_trailer_key(meta.youtube)

    def _tmdb_trailer_key(self) -> str:
        videos = self.tmdb_data.get("videos")
        if not isinstance(videos, dict):
            return ""
        video_map = cast(dict[str, Any], videos)
        entries = self._mapping_items(video_map.get("results", []))
        if not entries:
            return ""
        value = entries[-1].get("key", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _meta_trailer_key(value: Any) -> str:
        if not value:
            return ""
        return (
            str(value)
            .replace("https://www.youtube.com/watch?v=", "")
            .replace("/", "")
        )

    async def get_tags(self, meta: Meta) -> str:
        tags = self._normalized_genre_tags(meta.genres)
        if tags:
            return tags
        return await self._prompt_genre_tags(meta)

    @classmethod
    def _normalized_genre_tags(cls, value: Any) -> str:
        names = cls._non_empty_strings(value)
        return ", ".join(cls._normalize_genre_name(name) for name in names)

    @staticmethod
    def _non_empty_strings(value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [
            text
            for item in values
            if isinstance(item, str)
            if (text := item.strip())
        ]

    @staticmethod
    def _normalize_genre_name(name: str) -> str:
        return (
            unicodedata.normalize("NFKD", name)
            .encode("ASCII", "ignore")
            .decode("utf-8")
            .replace(" ", ".")
            .lower()
        )

    async def _prompt_genre_tags(self, meta: Meta) -> str:
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: Enter genres not available. Skipping {self.tracker} upload.[/yellow]"
            )
            meta.skipping = self.tracker
            return ""
        value = await prompt_in_thread(
            cli_ui.ask_string, f"Enter the genres (in {self.tracker} format): "
        )
        return str(value or "").strip()

    async def get_additional_checks(self, meta: Meta) -> bool:
        reason = self._release_rejection_reason(meta)
        if not reason:
            return True
        logger.info(f"{self.tracker}: {reason}")
        return False

    def _release_rejection_reason(self, meta: Meta) -> str:
        media_type = str(meta.type).lower()
        tag = str(meta.tag or "").strip().lower()
        blocked = self._blocked_release_kind(media_type, tag)
        return (
            f"{blocked} from {meta.tag} are not allowed on {self.tracker}"
            if blocked
            else ""
        )

    @staticmethod
    def _blocked_release_kind(media_type: str, tag: str) -> str:
        if media_type == "remux" and tag in {"-hdt", "-frds"}:
            return "Remuxes"
        return "WEB-DLs" if media_type == "webdl" and tag == "-evo" else ""

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        if not await self.get_groupid(meta):
            return []
        imdb = self._imdb_identifier(meta)
        if not imdb:
            logger.info(
                f"{self.tracker}: IMDb ID not found in metadata. Skipping search."
            )
            return []
        cookies = await self.load_cookies(meta)
        if cookies:
            return await self._cookie_search_existing(meta, imdb, cookies)
        return await self._api_search_existing(imdb)

    @staticmethod
    def _imdb_identifier(meta: Meta) -> str:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return str(imdb.get("imdbID", "") or "")

    async def _api_search_existing(self, imdb: str) -> list[dict[str, str]]:
        url = f"{self.base_url}/api.php?api_key={self.api_key}&action=torrent&imdbID={imdb}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        return self._api_duplicate_entries(payload)

    @classmethod
    def _api_duplicate_entries(cls, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, dict):
            return []
        data = cast(dict[str, Any], payload)
        if data.get("status") != 200:
            return []
        rows = cls._mapping_items(data.get("response", []))
        return [{"name": cls._formatted_api_duplicate(row)} for row in rows]

    @staticmethod
    def _formatted_api_duplicate(item: dict[str, Any]) -> str:
        parts = (
            item.get("Name", ""),
            item.get("Year", ""),
            item.get("Resolution", ""),
            item.get("Source", ""),
            item.get("Processing", ""),
            item.get("RemasterTitle", ""),
            item.get("Codec", ""),
        )
        return re.sub(
            r"\s{2,}", " ", " ".join(str(value) for value in parts).strip()
        )

    async def _cookie_search_existing(
        self, meta: Meta, imdb: str, cookies: Any
    ) -> list[dict[str, str]]:
        url = f"{self.base_url}/torrents.php?groupname={imdb.upper()}"
        async with httpx.AsyncClient(
            cookies=cookies,
            timeout=30,
            headers={"User-Agent": self._user_agent(meta)},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            entries = self._html_duplicate_entries(response.text)
            if entries:
                await self.get_slots(meta, client, GreatPosterWall.group_id)
            return entries

    @staticmethod
    def _user_agent(meta: Meta) -> str:
        version = (
            meta.current_version
            if meta.current_version is not None
            else "github.com/wastaken7/Upload-Assistant"
        )
        return f"{meta.ua_name} {version}"

    def _html_duplicate_entries(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="torrent_table")
        if table is None:
            return []
        return [
            entry
            for row in table.find_all("tr", class_="TableTorrent-rowTitle")
            if (entry := self._html_duplicate_entry(row)) is not None
        ]

    def _html_duplicate_entry(self, row: Any) -> dict[str, str] | None:
        link = row.find("a", href=re.compile(r"torrentid=\d+"))
        if link is None:
            return None
        tooltip = link.get("data-tooltip")
        if not isinstance(tooltip, str):
            return None
        size_cell = row.find("td", class_="TableTorrent-cellStatSize")
        href = link.get("href")
        return {
            "name": tooltip,
            "size": size_cell.get_text(strip=True) if size_cell else "",
            "link": self._torrent_link_from_href(href),
        }

    def _torrent_link_from_href(self, href: Any) -> str:
        value = href if isinstance(href, str) else ""
        match = re.search(r"torrentid=(\d+)", value)
        return f"{self.torrent_url}{match.group(1)}" if match else ""

    async def get_slots(
        self, meta: Meta, client: httpx.AsyncClient, group_id: str
    ) -> None:
        response = await self._slots_response(client, group_id)
        if response is None:
            return
        soup = BeautifulSoup(response.text, "html.parser")
        for row in soup.find_all("tr", class_="TableTorrent-rowEmptySlotNote"):
            self._log_matching_slot(meta, row)

    async def _slots_response(
        self, client: httpx.AsyncClient, group_id: str
    ) -> httpx.Response | None:
        url = f"{self.base_url}/torrents.php?id={group_id}"
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            logger.info(
                f"{self.tracker}: Error on request: {error.response.status_code} - {error.response.reason_phrase}",
                extra={"markup": False},
            )
            return None

    def _log_matching_slot(self, meta: Meta, row: Any) -> None:
        resolution = self._slot_resolution(row)
        slots = self._slot_names(row)
        if not slots or resolution != meta.resolution:
            return
        logger.info(
            f"{self.tracker}: \n[green]Available Slots for[/green] {resolution}:"
        )
        logger.info(
            f"{self.tracker}: {'\n'.join(f'- {slot}' for slot in slots)}\n"
        )

    @staticmethod
    def _slot_resolution(row: Any) -> str:
        edition_id = row.get("edition-id")
        if edition_id == "1":
            return "SD"
        if edition_id == "3":
            return "2160p"
        cell = row.find("td", class_="TableTorrent-cellEmptySlotNote")
        tag = cell.find("i") if cell else None
        return (
            tag.get_text(strip=True).replace("empty slots:", "").strip()
            if tag
            else ""
        )

    @classmethod
    def _slot_names(cls, row: Any) -> list[str]:
        names = cls._direct_slot_names(row) + cls._tooltip_slot_names(row)
        cleaned = [cls._clean_slot_name(name) for name in names]
        return sorted({name for name in cleaned if name})

    @staticmethod
    def _direct_slot_names(row: Any) -> list[str]:
        return [
            tag.get_text(strip=True)
            for tag in row.find_all("i")
            if "empty slots:" not in tag.get_text(strip=True)
        ]

    @staticmethod
    def _tooltip_slot_names(row: Any) -> list[str]:
        return [
            icon.get_text(strip=True)
            for tag in row.find_all("span", class_="tooltipstered")
            if (icon := tag.find("i"))
        ]

    @staticmethod
    def _clean_slot_name(value: str) -> str:
        return value.replace("Slot", "").replace("Empty slots:", "").strip()

    async def get_media_info(self, meta: Meta) -> str:
        info_file_path = ""
        info_file_path = (
            f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/BD_SUMMARY_00.txt"
            if meta.is_disc == "BDMV"
            else f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/MEDIAINFO_CLEANPATH.txt"
        )

        if Path(info_file_path).exists():
            try:
                async with aiofiles.open(
                    info_file_path, encoding="utf-8"
                ) as f:
                    return await f.read()
            except Exception as e:
                logger.info(
                    f"{self.tracker}: [bold red]Error reading info file at {info_file_path}: {e}[/bold red]"
                )
                return ""
        else:
            logger.info(
                f"{self.tracker}: [bold red]Info file not found: {info_file_path}[/bold red]"
            )
            return ""

    def get_edition(self, meta: Meta) -> str:
        edition_str = meta.edition.lower()
        if not edition_str:
            return ""

        edition_map = {
            "director's cut": "Director's Cut",
            "theatrical": "Theatrical Cut",
            "extended": "Extended",
            "uncut": "Uncut",
            "unrated": "Unrated",
            "imax": "IMAX",
            "noir": "Noir",
            "remastered": "Remastered",
        }

        for keyword, label in edition_map.items():
            if keyword in edition_str:
                return label

        return ""

    def get_processing_other(self, meta: Meta) -> str:
        if meta.type != "DISC":
            return ""
        if meta.is_disc == "BDMV":
            return self._bluray_disc_size(meta)
        if meta.is_disc == "DVD":
            return (
                meta.dvd_size if meta.dvd_size in {"DVD9", "DVD5"} else "DVD9"
            )
        return ""

    @classmethod
    def _bluray_disc_size(cls, meta: Meta) -> str:
        if meta.disctype in {"BD100", "BD66", "BD50", "BD25"}:
            return str(meta.disctype)
        size = cls._bdinfo_size(meta.bdinfo)
        if size > 66:
            return "BD100"
        if size > 50:
            return "BD66"
        if size > 25:
            return "BD50"
        return "BD25"

    @staticmethod
    def _bdinfo_size(value: Any) -> float:
        if not isinstance(value, dict):
            return 0
        mapping = cast(dict[str, Any], value)
        try:
            return float(mapping.get("size", 0) or 0)
        except TypeError, ValueError:
            return 0

    def get_screens(self, meta: Meta) -> list[str]:
        return [
            url
            for image in self._mapping_items(meta.image_list)
            if (url := self._raw_image_url(image))
        ]

    @staticmethod
    def _raw_image_url(image: dict[str, Any]) -> str:
        value = image.get("raw_url")
        return value if isinstance(value, str) and value else ""

    def get_credits(self, meta: Meta) -> str:
        names = self._director_names(meta)
        return ", ".join(list(dict.fromkeys(names))[:5]) if names else "N/A"

    @classmethod
    def _director_names(cls, meta: Meta) -> list[str]:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return cls._string_items(
            imdb.get("directors", [])
        ) + cls._string_items(meta.tmdb_directors)

    @staticmethod
    def _string_items(value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [item for item in values if isinstance(item, str)]

    def get_remaster_title(self, meta: Meta) -> str:
        tags: list[str] = []
        self._append_unique_tag(
            tags, self._distributor_remaster_tag(meta.distributor)
        )
        self._append_unique_tag(tags, self._edition_remaster_tag(meta.edition))
        self._append_release_feature_tags(tags, meta)
        return " / ".join(tags)

    @staticmethod
    def _append_unique_tag(tags: list[str], value: str) -> None:
        if value and value not in tags:
            tags.append(value)

    @staticmethod
    def _distributor_remaster_tag(value: Any) -> str:
        distributor = str(value or "").upper()
        if distributor in {
            "WARNER ARCHIVE",
            "WARNER ARCHIVE COLLECTION",
            "WAC",
        }:
            return "warner_archive_collection"
        if distributor in {"CRITERION", "CRITERION COLLECTION", "CC"}:
            return "the_criterion_collection"
        if distributor in {"MASTERS OF CINEMA", "MOC"}:
            return "masters_of_cinema"
        return ""

    @staticmethod
    def _edition_remaster_tag(value: Any) -> str:
        edition = str(value or "").lower()
        mapping = (
            ("director's cut", "director_s_cut"),
            ("extended", "extended_edition"),
            ("theatrical", "theatrical_cut"),
            ("rifftrax", "rifftrax"),
            ("uncut", "uncut"),
            ("unrated", "unrated"),
        )
        return next(
            (tag for keyword, tag in mapping if keyword in edition), ""
        )

    @classmethod
    def _append_release_feature_tags(cls, tags: list[str], meta: Meta) -> None:
        if meta.dual_audio:
            cls._append_unique_tag(tags, "dual_audio")
        if meta.extras:
            cls._append_unique_tag(tags, "extras")
        if meta.has_commentary or meta.manual_commentary:
            cls._append_unique_tag(tags, "with_commentary")

    async def get_groupid(self, meta: Meta) -> bool:
        GreatPosterWall.group_id = ""
        imdb = self._imdb_identifier(meta)
        url = f"{self.base_url}/api.php?api_key={self.api_key}&action=torrent&req=group&imdbID={imdb}"
        payload = await self._groupid_payload(url)
        group_id = self._groupid_from_payload(payload)
        if not group_id:
            return False
        GreatPosterWall.group_id = group_id
        return True

    async def _groupid_payload(self, url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url)
                response.raise_for_status()
            payload = response.json()
            return (
                cast(dict[str, Any], payload)
                if isinstance(payload, dict)
                else {}
            )
        except httpx.HTTPStatusError as error:
            logger.info(
                f"{self.tracker}: [bold red]HTTP error when fetching groupid: Status {error.response.status_code}[/bold red]"
            )
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [bold red]Network error fetching groupid: {error}[/bold red]"
            )
        except ValueError as error:
            logger.info(
                f"{self.tracker}: [bold red]Error decoding JSON from groupid response: {error}[/bold red]"
            )
        return {}

    @staticmethod
    def _groupid_from_payload(payload: dict[str, Any]) -> str:
        if payload.get("status") != 200:
            return ""
        response = payload.get("response")
        if not isinstance(response, dict):
            return ""
        value = cast(dict[str, Any], response).get("ID")
        return str(value) if value is not None else ""

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        source, identifier = self._additional_identifier(meta)
        data: dict[str, Any] = {
            "data_source": source,
            "identifier": identifier,
            "desc": self.tmdb_data.get("overview", ""),
            "image": f"https://image.tmdb.org/t/p/original{meta.tmdb_poster_path}",
            "maindesc": meta.overview,
            "name": meta.title,
            "releasetype": self._get_movie_type(meta),
            "subname": self.get_title(meta),
            "tags": await self.get_tags(meta),
            "year": "" if meta.year is None else str(meta.year),
        }
        self._append_legacy_identifiers(data, meta)
        data.update(await self._get_artist_data(meta))
        data["main_artist_number"] = "1"
        return data

    @classmethod
    def _additional_identifier(cls, meta: Meta) -> tuple[str, str]:
        imdb = cls._imdb_identifier(meta).strip()
        tmdb = str(meta.tmdb_id or "").strip()
        if imdb:
            return "imdb", imdb
        if tmdb:
            return "tmdb", tmdb
        return "manual", ""

    @classmethod
    def _append_legacy_identifiers(
        cls, data: dict[str, Any], meta: Meta
    ) -> None:
        imdb = cls._imdb_identifier(meta).strip()
        tmdb = str(meta.tmdb_id or "").strip()
        if imdb:
            data["imdb"] = imdb
        if tmdb:
            data["tmdb"] = tmdb

    async def _get_artist_data(self, meta: Meta) -> dict[str, Any]:
        credits = await self._artist_credit_data(meta)
        director = await self._director_identity(meta, credits)
        if director is None:
            return {}
        director_id, director_name, director_sub = director
        post = self._new_artist_payload(
            director_id, director_name, director_sub
        )
        self._append_contributors(
            post, credits["writers"], credits["writer_ids"], "2"
        )
        self._append_contributors(
            post,
            credits["stars"],
            credits["star_ids"],
            "6",
            credits["characters"],
        )
        return post

    async def _artist_credit_data(self, meta: Meta) -> dict[str, Any]:
        full = await self._full_credit_data_for_meta(meta)
        return (
            full
            if self._has_director_credit(full)
            else self._fallback_credit_data(meta)
        )

    async def _full_credit_data_for_meta(self, meta: Meta) -> dict[str, Any]:
        imdb = (
            self._imdb_identifier(meta).strip() or str(meta.imdb or "").strip()
        )
        if not imdb:
            return self._empty_credit_data()
        movie_info = await self._fetch_gpw_movie_info(meta, "imdb", imdb)
        return self._full_credit_data(movie_info)

    @staticmethod
    def _has_director_credit(data: dict[str, Any]) -> bool:
        return bool(data["directors"] and data["director_ids"])

    @classmethod
    def _full_credit_data(cls, movie_info: Any) -> dict[str, Any]:
        info = cls._unwrap_movie_info(movie_info)
        values: Any = info.get("FullCredits") or info.get("fullCredits") or []
        credits = cls._mapping_items(values)
        data = cls._empty_credit_data()
        for credit in credits:
            cls._append_full_credit(data, credit)
        return data

    @staticmethod
    def _unwrap_movie_info(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        mapping = cast(dict[str, Any], value)
        response = mapping.get("response")
        return (
            cast(dict[str, Any], response)
            if isinstance(response, dict)
            else mapping
        )

    @staticmethod
    def _empty_credit_data() -> dict[str, Any]:
        return {
            "directors": [],
            "director_ids": [],
            "writers": [],
            "writer_ids": [],
            "stars": [],
            "star_ids": [],
            "characters": {},
        }

    @classmethod
    def _append_full_credit(
        cls, data: dict[str, Any], credit: dict[str, Any]
    ) -> None:
        identity = cls._credit_identity(credit)
        target = (
            cls._credit_target(identity[0]) if identity is not None else None
        )
        if identity is None or target is None:
            return
        role, person_id, person_name, character = identity
        names_key, ids_key = target
        if not cls._append_credit_identity(
            data, names_key, ids_key, person_id, person_name
        ):
            return
        cls._store_credit_character(data, role, person_id, character)

    @staticmethod
    def _append_credit_identity(
        data: dict[str, Any],
        names_key: str,
        ids_key: str,
        person_id: str,
        person_name: str,
    ) -> bool:
        names = cast(list[str], data[names_key])
        ids = cast(list[str], data[ids_key])
        if person_id in ids:
            return False
        names.append(person_name)
        ids.append(person_id)
        return True

    @staticmethod
    def _store_credit_character(
        data: dict[str, Any], role: str, person_id: str, character: str
    ) -> None:
        if role == "cast" and character:
            cast(dict[str, str], data["characters"])[person_id] = character

    @classmethod
    def _credit_identity(
        cls, credit: dict[str, Any]
    ) -> tuple[str, str, str, str] | None:
        role = cls._credit_text(credit, "role").lower()
        person_id = cls._credit_person_id(credit)
        name = cls._credit_text(credit, "name")
        character = cls._credit_text(credit, "character")
        return (
            (role, person_id, name, character)
            if cls._valid_credit_person(name, person_id)
            else None
        )

    @staticmethod
    def _credit_text(credit: dict[str, Any], key: str) -> str:
        return str(credit.get(key) or "").strip()

    @staticmethod
    def _credit_person_id(credit: dict[str, Any]) -> str:
        return str(credit.get("imdbId") or credit.get("imdbID") or "").strip()

    @staticmethod
    def _valid_credit_person(name: str, person_id: str) -> bool:
        return (
            bool(name)
            and name.lower() != "n/a"
            and re.fullmatch(r"nm\d+", person_id) is not None
        )

    @staticmethod
    def _credit_target(role: str) -> tuple[str, str] | None:
        return {
            "director": ("directors", "director_ids"),
            "writer": ("writers", "writer_ids"),
            "cast": ("stars", "star_ids"),
        }.get(role)

    @classmethod
    def _fallback_credit_data(cls, meta: Meta) -> dict[str, Any]:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return {
            "directors": cls._clean_names(imdb.get("directors", [])),
            "director_ids": cls._clean_person_ids(
                imdb.get("directors_id", [])
            ),
            "writers": cls._clean_names(imdb.get("writers", [])),
            "writer_ids": cls._clean_person_ids(imdb.get("writers_id", [])),
            "stars": cls._clean_names(imdb.get("stars", [])),
            "star_ids": cls._clean_person_ids(imdb.get("stars_id", [])),
            "characters": {},
        }

    @staticmethod
    def _clean_names(value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [
            text
            for item in values
            if isinstance(item, str)
            if (text := item.strip())
        ]

    @classmethod
    def _clean_person_ids(cls, value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return [
            person_id
            for item in values
            if (person_id := cls._clean_person_id(item))
        ]

    @staticmethod
    def _clean_person_id(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        person_id = value.strip()
        return person_id if re.fullmatch(r"nm\d+", person_id) else ""

    async def _director_identity(
        self, meta: Meta, credits: dict[str, Any]
    ) -> tuple[str, str, str] | None:
        director_id = self._first_string(credits.get("director_ids"))
        director_name = self._first_string(credits.get("directors"))
        if self._valid_director(director_id, director_name):
            return director_id, director_name, ""
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: Director details required for movie missing in database. Skipping {self.tracker} upload.[/yellow]"
            )
            meta.skipping = self.tracker
            return None
        return await self._prompt_director_identity()

    @staticmethod
    def _first_string(value: Any) -> str:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return str(values[0]).strip() if values else ""

    @staticmethod
    def _valid_director(person_id: str, name: str) -> bool:
        return (
            re.fullmatch(r"nm\d+", person_id) is not None
            and bool(name)
            and name.lower() != "n/a"
        )

    async def _prompt_director_identity(self) -> tuple[str, str, str]:
        logger.info(
            f"{self.tracker}: This movie is not registered in the {self.tracker} database, please enter the details of 1 director"
        )
        person_id = await self._prompt_person_id()
        name = await self._prompt_person_name()
        chinese_raw = await prompt_in_thread(
            cli_ui.ask_string,
            "Enter Director Chinese name (optional, press Enter to skip): ",
        )
        return person_id, name, str(chinese_raw or "").strip()

    async def _prompt_person_id(self) -> str:
        while True:
            value = await prompt_in_thread(
                cli_ui.ask_string, "Enter Director IMDb ID (e.g., nm0000138): "
            )
            if not isinstance(value, str):
                return ""
            person_id = value.strip()
            if re.fullmatch(r"nm\d+", person_id):
                return person_id
            logger.info(
                f"{self.tracker}: [red]Invalid IMDb person ID. Format must be like nm0000138.[/red]"
            )

    async def _prompt_person_name(self) -> str:
        while True:
            value = await prompt_in_thread(
                cli_ui.ask_string, "Enter Director English name: "
            )
            name = str(value or "").strip()
            if name:
                return name
            logger.info(
                f"{self.tracker}: [red]Director English name cannot be empty.[/red]"
            )

    @staticmethod
    def _new_artist_payload(
        person_id: str, name: str, chinese_name: str
    ) -> dict[str, Any]:
        return {
            "artist_ids[]": [person_id],
            "artists[]": [name],
            "importance[]": ["1"],
            "characters[]": [""],
            "artists_sub[]": [chinese_name],
        }

    @classmethod
    def _append_contributors(
        cls,
        post: dict[str, Any],
        names_value: Any,
        ids_value: Any,
        importance: str,
        characters_value: Any = None,
    ) -> None:
        rows = cls._contributor_rows(names_value, ids_value, characters_value)
        for name, person_id, character in rows:
            cls._append_contributor(
                post, name, person_id, importance, character
            )

    @classmethod
    def _contributor_rows(
        cls, names_value: Any, ids_value: Any, characters_value: Any
    ) -> list[tuple[str, str, str]]:
        names = cls._string_items(names_value)
        ids = cls._string_items(ids_value)
        characters = cls._string_mapping(characters_value)
        return [
            cls._contributor_row(index, name, ids, characters)
            for index, name in enumerate(names)
        ]

    @staticmethod
    def _string_mapping(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        mapping = cast(dict[Any, Any], value)
        return {str(key): str(item) for key, item in mapping.items()}

    @staticmethod
    def _contributor_row(
        index: int, name: str, ids: list[str], characters: dict[str, str]
    ) -> tuple[str, str, str]:
        person_id = ids[index].strip() if index < len(ids) else ""
        return name.strip(), person_id, characters.get(person_id, "")

    @classmethod
    def _append_contributor(
        cls,
        post: dict[str, Any],
        name: str,
        person_id: str,
        importance: str,
        character: str,
    ) -> None:
        if not cls._valid_credit_person(name, person_id):
            return
        artist_ids = cast(list[str], post["artist_ids[]"])
        if person_id in artist_ids:
            return
        cls._store_contributor(
            post,
            name,
            person_id,
            importance,
            cls._contributor_character(importance, character),
        )

    @staticmethod
    def _contributor_character(importance: str, character: str) -> str:
        return character or ("Unknown" if importance == "6" else "")

    @staticmethod
    def _store_contributor(
        post: dict[str, Any],
        name: str,
        person_id: str,
        importance: str,
        character: str,
    ) -> None:
        cast(list[str], post["artists[]"]).append(name)
        cast(list[str], post["artist_ids[]"]).append(person_id)
        cast(list[str], post["importance[]"]).append(importance)
        cast(list[str], post["artists_sub[]"]).append("")
        cast(list[str], post["characters[]"]).append(character)

    async def _fetch_gpw_movie_info(
        self, meta: Meta, data_source: str, identifier: str
    ) -> dict[str, Any]:
        if not data_source or not identifier:
            return {}
        candidates = await self._movie_info_candidates(
            meta, data_source, identifier
        )
        return self._best_movie_info(candidates)

    async def _movie_info_candidates(
        self, meta: Meta, data_source: str, identifier: str
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        cookies: Any = None
        for url, params, use_cookies in self._movie_info_endpoints(
            data_source, identifier
        ):
            if use_cookies and cookies is None:
                cookies = await self.load_cookies(meta)
            candidates.append(
                await self._movie_info_candidate(
                    meta, url, params, cookies if use_cookies else None
                )
            )
        return candidates

    @classmethod
    def _best_movie_info(
        cls, candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not candidates:
            return {}
        return max(candidates, key=cls._credits_score)

    def _movie_info_endpoints(
        self, data_source: str, identifier: str
    ) -> list[tuple[str, dict[str, str], bool]]:
        return [
            (
                f"{self.base_url}/upload.php",
                {
                    "action": "movie_info",
                    "source": data_source,
                    "identifier": identifier,
                },
                True,
            ),
            (
                f"{self.base_url}/api.php",
                {
                    "api_key": self.api_key,
                    "action": "movie_info",
                    "imdbid": identifier,
                },
                False,
            ),
        ]

    async def _movie_info_candidate(
        self, meta: Meta, url: str, params: dict[str, str], cookies: Any
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=15,
                cookies=cookies,
                headers={"User-Agent": self._user_agent(meta)},
            ) as client:
                response = await client.get(url, params=params)
            response.raise_for_status()
            return self._successful_movie_info_response(response.json())
        except (ValueError, KeyError, TypeError, IndexError) as error:
            logger.debug(
                f"{self.tracker}: Failed to process response payload on {self.tracker}: {escape(str(error))}",
                exc_info=True,
            )
            return {}

    @staticmethod
    def _successful_movie_info_response(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data = cast(dict[str, Any], payload)
        status = str(data.get("status", "")).strip().lower()
        if (
            status not in {"success", "ok", "200"}
            and data.get("status") != 200
        ):
            return {}
        response = data.get("response")
        return (
            cast(dict[str, Any], response)
            if isinstance(response, dict)
            else {}
        )

    @staticmethod
    def _credits_score(response: dict[str, Any]) -> int:
        value: Any = (
            response.get("FullCredits") or response.get("fullCredits") or []
        )
        return len(value) if isinstance(value, list) else 0

    def _get_movie_type(self, meta: Meta) -> str:
        movie_type = ""
        imdb_info = meta.imdb_info
        if imdb_info:
            imdb_type = imdb_info.get("type", "movie").lower()
            if imdb_type in ("movie", "tv movie", "tvmovie", "video"):
                runtime = int(imdb_info.get("runtime", "60"))
                movie_type = (
                    "1" if runtime >= 45 or runtime == 0 else "2"
                )  # Feature Film/Short Film

        return movie_type

    def get_source(self, meta: Meta) -> str:
        source_type = str(meta.type).lower()

        if source_type == "disc":
            is_disc = str(meta.is_disc).upper()
            if is_disc == "BDMV":
                return "Blu-ray"
            if is_disc in ("HDDVD", "DVD"):
                return "DVD"
            return "Other"

        keyword_map = {
            "webdl": "WEB",
            "webrip": "WEB",
            "web": "WEB",
            "remux": "Blu-ray",
            "encode": "Blu-ray",
            "bdrip": "Blu-ray",
            "brrip": "Blu-ray",
            "hdtv": "HDTV",
            "sdtv": "TV",
            "dvdrip": "DVD",
            "hd-dvd": "HD-DVD",
            "dvdscr": "DVD",
            "pdtv": "TV",
            "uhdtv": "HDTV",
            "vhs": "VHS",
            "tvrip": "TVRip",
        }

        return keyword_map.get(source_type, "Other")

    def get_processing(self, meta: Meta) -> str:
        type_map = {
            "ENCODE": "Encode",
            "REMUX": "Remux",
            "DIY": "DIY",
            "UNTOUCHED": "Untouched",
        }
        release_type = str(meta.type).strip().upper()
        return type_map.get(release_type, "Untouched")

    def get_media_flags(self, meta: Meta) -> dict[str, str]:
        flags: dict[str, str] = {}
        self._append_audio_flags(flags, meta)
        self._append_video_flags(flags, meta)
        return flags

    @staticmethod
    def _append_audio_flags(flags: dict[str, str], meta: Meta) -> None:
        audio = str(meta.audio or "").lower()
        if "atmos" in audio:
            flags["dolby_atmos"] = "on"
        if "dts:x" in audio:
            flags["dts_x"] = "on"
        channel_flag = {"5.1": "audio_51", "7.1": "audio_71"}.get(
            str(meta.channels)
        )
        if channel_flag:
            flags[channel_flag] = "on"

    @classmethod
    def _append_video_flags(cls, flags: dict[str, str], meta: Meta) -> None:
        hdr = str(meta.hdr or "")
        cls._append_bit_depth_flag(flags, hdr, meta.bit_depth)
        cls._append_hdr_flags(flags, hdr)

    @staticmethod
    def _append_bit_depth_flag(
        flags: dict[str, str], hdr: str, bit_depth: Any
    ) -> None:
        if not hdr.strip() and str(bit_depth) == "10":
            flags["10_bit"] = "on"

    @staticmethod
    def _append_hdr_flags(flags: dict[str, str], hdr: str) -> None:
        if "DV" not in hdr:
            return
        flags["dolby_vision"] = "on"
        if "HDR" in hdr:
            flags["hdr10plus" if "HDR10+" in hdr else "hdr10"] = "on"

    def get_resolution(self, meta: Meta) -> str:
        resolution = meta.resolution.lower()
        source = str(meta.source).upper()

        if source in ["NTSC", "PAL"]:
            return source.upper()
        if resolution.lower() in [
            "480p",
            "576p",
            "720p",
            "1080i",
            "1080p",
            "2160p",
        ]:
            return resolution.lower()
        return "Other"

    async def fetch_data(self, meta: Meta) -> dict[str, Any]:
        await self.load_localized_data(meta)
        await self.get_groupid(meta)
        data = await self._group_or_new_movie_data(meta)
        data.update(await self._release_upload_fields(meta))
        if await self.get_ch_dubs(meta):
            data["chinese_dubbed"] = "on"
        self._append_release_upload_flags(data, meta)
        data.update(self.get_media_flags(meta))
        return data

    async def _group_or_new_movie_data(self, meta: Meta) -> dict[str, Any]:
        if GreatPosterWall.group_id:
            return {"groupid": GreatPosterWall.group_id}
        return await self.get_additional_data(meta)

    async def _release_upload_fields(self, meta: Meta) -> dict[str, Any]:
        codec = self.get_codec(meta)
        container = self.get_container(meta)
        remaster = self.get_remaster_title(meta)
        return {
            "codec_other": meta.video_codec if codec == "Other" else "",
            "codec": codec,
            "container_other": meta.container if container == "Other" else "",
            "container": container,
            "mediainfo[]": await self.get_media_info(meta),
            "movie_edition_information": "on" if remaster else "",
            "processing_other": self.get_processing_other(meta)
            if meta.type == "DISC"
            else "",
            "processing": self.get_processing(meta),
            "release_desc": await self.get_release_desc(meta),
            "remaster_custom_title": "",
            "remaster_title": remaster,
            "remaster_year": "",
            "resolution_height": "",
            "resolution_width": "",
            "resolution": self.get_resolution(meta),
            "source_other": "",
            "source": self.get_source(meta),
            "submit": "true",
            "subtitle_type": self._subtitle_type(meta),
            "subtitles[]": await self.get_subtitle(meta),
        }

    @staticmethod
    def _subtitle_type(meta: Meta) -> str:
        if meta.hardcoded_subs:
            return "2"
        return "1" if meta.subtitle_languages else "3"

    def _append_release_upload_flags(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        self._append_optional_flag(
            data, "special_effects_subtitles", bool(meta.sfx_subtitles)
        )
        self._append_optional_flag(data, "scene", bool(meta.scene))
        self._append_personal_release_flag(data, meta)
        self._append_optional_flag(
            data,
            "jinzhuan",
            bool(
                meta.exclusive or self.tracker_config.get("exclusive", False)
            ),
        )

    @staticmethod
    def _append_optional_flag(
        data: dict[str, Any], key: str, enabled: bool
    ) -> None:
        if enabled:
            data[key] = "on"

    @staticmethod
    def _append_personal_release_flag(
        data: dict[str, Any], meta: Meta
    ) -> None:
        if not meta.personalrelease:
            return
        data["buy" if meta.is_disc else "diy"] = "on"

    async def upload(self, meta: Meta) -> bool:
        if self._upload_skipped(meta):
            return False
        await self.common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )
        data = await self.fetch_data(meta)
        if self._upload_skipped(meta):
            return False
        if meta.debug:
            return await self._debug_upload(meta, data)
        return await self._live_upload(meta, data)

    def _upload_skipped(self, meta: Meta) -> bool:
        return getattr(meta, "skipping", None) == self.tracker

    async def _debug_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _live_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        try:
            response = await self._upload_response(meta, data)
            return await self._handle_upload_response(meta, response)
        except httpx.TimeoutException:
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: Request timed out after 10 seconds"
            )
        except httpx.RequestError as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error: Unable to upload. Error: {error}."
            )
        except Exception as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error: It may have uploaded, go check. Error: {error}."
            )
        return False

    async def _upload_response(
        self, meta: Meta, data: dict[str, Any]
    ) -> httpx.Response:
        upload_url = (
            f"{self.base_url}/api.php?api_key={self.api_key}&action=upload"
        )
        torrent_path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            torrent_bytes = await torrent_file.read()
        files = {
            "file_input": (
                f"{self.tracker}.placeholder.torrent",
                torrent_bytes,
                "application/x-bittorrent",
            )
        }
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.post(url=upload_url, files=files, data=data)

    async def _handle_upload_response(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        payload = self._decoded_upload_payload(meta, response)
        if payload is None:
            return False
        torrent_id = self._successful_torrent_id(payload)
        if torrent_id:
            await self._record_successful_upload(meta, torrent_id)
            return True
        self._record_failed_upload(meta, payload)
        return False

    def _decoded_upload_payload(
        self, meta: Meta, response: httpx.Response
    ) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except Exception as error:
            logger.info(
                f"{self.tracker}: Failed to decode JSON response: {error}"
            )
            return None
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
        meta.tracker_status[self.tracker]["status_message"] = (
            f"data error: Invalid API response: {payload}"
        )
        return None

    @classmethod
    def _successful_torrent_id(cls, payload: dict[str, Any]) -> str:
        status = str(payload.get("status", "")).strip().lower()
        if status not in {"success", "ok", "200"}:
            return ""
        return cls._extract_torrent_id(payload.get("response"))

    @classmethod
    def _extract_torrent_id(cls, payload: Any) -> str:
        mapping = cls._torrent_id_mapping(payload)
        value = mapping.get("torrent_id")
        return str(value) if value is not None else ""

    @staticmethod
    def _torrent_id_mapping(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
        if (
            isinstance(payload, list)
            and payload
            and isinstance(payload[0], dict)
        ):
            return cast(dict[str, Any], payload[0])
        return {}

    async def _record_successful_upload(
        self, meta: Meta, torrent_id: str
    ) -> None:
        status = meta.tracker_status[self.tracker]
        status["torrent_id"] = torrent_id
        status["status_message"] = "Torrent uploaded successfully."
        await self.common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            self.announce,
            self.torrent_url + torrent_id,
        )

    def _record_failed_upload(
        self, meta: Meta, payload: dict[str, Any]
    ) -> None:
        message = str(
            payload.get("error") or payload.get("message") or "Upload failed"
        )
        duplicate = "the exact same torrent file already exists on the site"
        if duplicate in message.lower():
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: Torrent already exists on GREATPOSTERWALL (duplicate file)."
            )
            return
        meta.tracker_status[self.tracker]["status_message"] = (
            f"data error: {message}."
        )

    async def get_name(self, meta: Meta) -> str:
        return meta.title
