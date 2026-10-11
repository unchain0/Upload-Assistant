# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import glob
import io
import json
import platform
import re
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import click
import httpx
from rich.markup import escape

from src.domain_models.processing import LoginError, UploadError
from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.filesystem.temp_paths import artwork_dir, screenshots_dir
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.screenshot_capture import TakeScreensManager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.security.redaction import PathAwareEncoder, Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator


class PassThePopcorn:
    """
    PTP Private Torrent Tracker
    """

    base_url = "https://passthepopcorn.me"

    tracker = "PASSTHEPOPCORN"
    display_name = "PassThePopcorn"
    allows_bloated_audio = True
    source_flag = "PTP"
    banned_groups = (
        "aXXo",
        "BMDru",
        "BRrip",
        "CM8",
        "CrEwSaDe",
        "CTFOH",
        "d3g",
        "DNL",
        "FaNGDiNG0",
        "HD2DVD",
        "HDT",
        "HDTime",
        "ION10",
        "iPlanet",
        "KiNGDOM",
        "mHD",
        "mSD",
        "nHD",
        "nikt0",
        "nSD",
        "NhaNc3",
        "OFT",
        "PRODJi",
        "SANTi",
        "SPiRiT",
        "STUTTERSHIT",
        "ViSION",
        "VXT",
        "WAF",
        "x0r",
        "YIFY",
        "LAMA",
        "WORLD",
    )
    approved_image_hosts = ("pixhost",)
    image_host_policy = ImageHostPolicy(
        {"pixhost.to": "pixhost"}, approved_image_hosts
    )
    sub_lang_map: ClassVar[dict[tuple[str, ...], int]] = {
        ("Arabic", "ara", "ar"): 22,
        (
            "Brazilian Portuguese",
            "Brazilian",
            "Portuguese-BR",
            "pt-br",
            "pt-BR",
        ): 49,
        ("Bulgarian", "bul", "bg"): 29,
        (
            "Chinese",
            "chi",
            "zh",
            "Chinese (Simplified)",
            "Chinese (Traditional)",
            "cmn-Hant",
            "cmn-Hans",
            "yue-Hant",
            "yue-Hans",
        ): 14,
        ("Croatian", "hrv", "hr", "scr"): 23,
        ("Czech", "cze", "cz", "cs"): 30,
        ("Danish", "dan", "da"): 10,
        ("Dutch", "dut", "nl"): 9,
        (
            "English",
            "eng",
            "en",
            "en-US",
            "en-GB",
            "English (CC)",
            "English - SDH",
        ): 3,
        (
            "English - Forced",
            "English (Forced)",
            "en (Forced)",
            "en-US (Forced)",
        ): 50,
        (
            "English Intertitles",
            "English (Intertitles)",
            "English - Intertitles",
            "en (Intertitles)",
            "en-US (Intertitles)",
        ): 51,
        ("Estonian", "est", "et"): 38,
        ("Finnish", "fin", "fi"): 15,
        ("French", "fre", "fr", "fr-FR", "fr-CA"): 5,
        ("German", "ger", "de"): 6,
        ("Greek", "gre", "el"): 26,
        ("Hebrew", "heb", "he"): 40,
        ("Hindi", "hin", "hi"): 41,
        ("Hungarian", "hun", "hu"): 24,
        ("Icelandic", "ice", "is"): 28,
        ("Indonesian", "ind", "id"): 47,
        ("Italian", "ita", "it"): 16,
        ("Japanese", "jpn", "ja"): 8,
        ("Korean", "kor", "ko"): 19,
        ("Latvian", "lav", "lv"): 37,
        ("Lithuanian", "lit", "lt"): 39,
        ("Norwegian", "nor", "no"): 12,
        ("Polish", "pol", "pl"): 17,
        ("Portuguese", "por", "pt", "pt-PT"): 21,
        ("Romanian", "rum", "ro"): 13,
        ("Russian", "rus", "ru"): 7,
        ("Serbian", "srp", "sr", "scc"): 31,
        ("Slovak", "slo", "sk"): 42,
        ("Slovenian", "slv", "sl"): 43,
        ("Spanish", "spa", "es", "es-ES", "es-419"): 4,
        ("Swedish", "swe", "sv"): 11,
        ("Thai", "tha", "th"): 20,
        ("Turkish", "tur", "tr"): 18,
        ("Ukrainian", "ukr", "uk"): 34,
        ("Vietnamese", "vie", "vi"): 25,
    }
    supported_categories = ("MOVIE",)
    tracker_urls = ("passthepopcorn.me",)

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.rehost_images_manager = RehostImagesManager(config)
        self.takescreens_manager = TakeScreensManager(config)
        self.uploadscreens_manager = UploadScreensManager(config)
        self.api_user = (
            config["TRACKERS"][self.tracker].get("ApiUser", "").strip()
        )
        self.api_key = (
            config["TRACKERS"][self.tracker].get("api_key", "").strip()
        )
        announce_url = (
            config["TRACKERS"][self.tracker].get("announce_url", "").strip()
        )
        if announce_url and announce_url.startswith("http://"):
            logger.info(
                f"{self.tracker}: [red]announce URL is using plaintext HTTP.\n"
            )
            logger.info(
                f"{self.tracker}: [red]is turning off their plaintext HTTP tracker soon. You must update your announce URLS. See PassThePopcorn/forums.php?page=1&action=viewthread&threadid=46663"
            )
            logger.info(
                f"{self.tracker}: [yellow]Modifying the url to use HTTPS. Update your config file to avoid this message in the future."
            )
            self.announce_url = announce_url.replace(
                "http://", "https://"
            ).replace(":2710", "")
        else:
            self.announce_url = announce_url
        self.username = (
            config["TRACKERS"][self.tracker].get("username", "").strip()
        )
        self.password = (
            config["TRACKERS"][self.tracker].get("password", "").strip()
        )
        self.web_source = self._is_true(
            config["TRACKERS"][self.tracker].get(
                "add_web_source_to_desc", True
            )
        )
        self.user_agent = (
            f"Upload-Assistant/2.3 ({platform.system()} {platform.release()})"
        )

        self.cookie_validator = CookieValidator(config)

    def _is_true(self, value: Any) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes"}

    def _group_allowed(self, meta: Meta) -> bool:
        if not meta.tag:
            return True
        tag_clean = meta.tag.strip().lstrip("-").lower()
        banned = {group.lower() for group in self.banned_groups}
        if tag_clean not in banned:
            return True
        logger.info(
            f"{self.tracker}: [red]Release group {meta.tag} is banned. "
            "Skipping upload.[/red]"
        )
        return False

    def _credentials_configured(self) -> bool:
        if not self.api_user:
            logger.info(
                f"{self.tracker}: [red]API User is missing in config. "
                "Skipping upload.[/red]"
            )
            return False
        if self.username and self.password:
            return True
        logger.info(
            f"{self.tracker}: [red]Username or Password is missing in config. "
            "Skipping upload.[/red]"
        )
        return False

    def _announce_url_valid(self) -> bool:
        matched = re.match(
            r"https?://please\.passthepopcorn\.me:?\d*/(.+)/announce",
            self.announce_url,
        )
        if matched is not None:
            return True
        logger.info(
            f"{self.tracker}: [red]Failed to extract passkey from "
            "PassThePopcorn announce URL. Skipping upload.[/red]"
        )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        return bool(
            self._group_allowed(meta)
            and self._credentials_configured()
            and self._announce_url_valid()
        )

    def _api_headers(self) -> dict[str, str]:
        return {
            "ApiUser": self.api_user,
            "api_key": self.api_key,
            "User-Agent": self.user_agent,
        }

    @staticmethod
    def _torrent_match(
        torrents: list[dict[str, Any]], search_value: str
    ) -> tuple[int | str | None, str | None]:
        normalized = search_value.lower().strip()
        if normalized:
            for torrent in torrents:
                release_name = str(torrent.get("ReleaseName", "")).lower()
                if normalized in release_name:
                    return torrent.get("Id"), torrent.get("InfoHash")
        if not torrents:
            return None, None
        first = torrents[0]
        return first.get("Id"), first.get("InfoHash")

    @classmethod
    def _movie_search_result(
        cls, movie: dict[str, Any], search_value: str
    ) -> tuple[int, int | str | None, str | None] | None:
        imdb_value = movie.get("ImdbId")
        if not imdb_value:
            return None
        torrents = cast(list[dict[str, Any]], movie.get("Torrents", []) or [])
        torrent_id, info_hash = cls._torrent_match(torrents, search_value)
        return int(imdb_value), torrent_id, info_hash

    def _log_search_status(self, status_code: int) -> None:
        if status_code in {400, 401, 403}:
            logger.info(
                f"{self.tracker}: [bold red]Error: 400/401/403 - Invalid "
                "request or authentication failed[/bold red]"
            )
        elif status_code == 503:
            logger.info(f"{self.tracker}: [bold yellow]Unavailable (503)")

    async def _ptp_search_response(self, search_value: str) -> Any | None:
        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True
            ) as client:
                return await client.get(
                    url=f"{self.base_url}/torrents.php",
                    headers=self._api_headers(),
                    params={"searchstr": search_value},
                )
        except Exception as error:
            logger.info(
                f"{self.tracker}: [red]An error occurred: {error!s}[/red]"
            )
            return None

    def _ptp_search_movies(
        self, response: Any, search_value: str
    ) -> tuple[int | None, int | str | None, str | None]:
        data = cast(dict[str, Any], response.json())
        movies = cast(list[dict[str, Any]], data.get("Movies", []))
        for movie in movies:
            result = self._movie_search_result(movie, search_value)
            if result is not None:
                return result
        logger.info(
            f"{self.tracker}: [yellow]Could not find any release matching "
            f"[bold yellow]{search_value}[/bold yellow] on PassThePopcorn"
        )
        return None, None, None

    async def get_ptp_id_imdb(
        self,
        search_term: str,
        _search_file_folder: str,
        _meta: dict[str, Any],
    ) -> tuple[int | None, int | str | None, str | None]:
        del _meta
        search_value = search_term or _search_file_folder
        response = await self._ptp_search_response(search_value)
        if response is None:
            return None, None, None
        if response.status_code != 200:
            self._log_search_status(response.status_code)
            return None, None, None
        return self._ptp_search_movies(response, search_value)

    @staticmethod
    def _torrent_infohash(
        torrents: list[dict[str, Any]], torrent_id: int | str
    ) -> str | None:
        target = str(torrent_id)
        for torrent in torrents:
            if str(torrent.get("Id", "")) == target:
                value = torrent.get("InfoHash")
                return str(value) if value else None
        return None

    def _imdb_lookup_error(self, response: Any) -> None:
        if response.status_code in {400, 401, 403}:
            logger.info(response.text)
        elif response.status_code == 503:
            logger.info(f"{self.tracker}: [bold yellow]Unavailable (503)")

    async def get_imdb_from_torrent_id(
        self, ptp_torrent_id: int | str
    ) -> tuple[int | None, str | None]:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(
                f"{self.base_url}/torrents.php",
                params={"torrentid": ptp_torrent_id},
                headers=self._api_headers(),
            )
        await asyncio.sleep(1)
        if response.status_code != 200:
            self._imdb_lookup_error(response)
            return None, None
        try:
            data = cast(dict[str, Any], response.json())
            imdb_id = int(data.get("ImdbId", 0) or 0)
            torrents = cast(list[dict[str, Any]], data.get("Torrents", []))
            return imdb_id, self._torrent_infohash(torrents, ptp_torrent_id)
        except Exception:
            return None, None

    @staticmethod
    def _save_description(meta: Meta, description: str | None) -> None:
        meta.description = cast(str, description)
        meta.saved_description = True

    def _edited_description(
        self, meta: Meta, description: str | None
    ) -> str | None:
        edited = cast(str | None, click.edit(cast(Any, description)))
        if edited:
            description = edited.strip()
            self._save_description(meta, description)
        logger.info(
            f"{self.tracker}: [green]Final description after editing:[/green] "
            f"{escape(str(description))}"
        )
        return description

    async def _interactive_description(
        self, meta: Meta, description: str | None
    ) -> None:
        logger.info(
            f"{self.tracker}: [cyan]Do you want to edit, discard or keep the "
            "description?[/cyan]"
        )
        choice = await prompt_in_thread(
            cli_ui.ask_string,
            "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: ",
        )
        normalized = (choice or "").lower()
        if normalized == "e":
            self._edited_description(meta, description)
            return
        if normalized == "d":
            logger.info(
                f"{self.tracker}: [yellow]Description discarded.[/yellow]"
            )
            return
        logger.info(
            f"{self.tracker}: [green]Keeping the original description.[/green]"
        )
        self._save_description(meta, description)

    async def _apply_ptp_description(
        self, meta: Meta, description: str | None
    ) -> None:
        if meta.skip_tracker_descriptions:
            return
        logger.info(
            f"{self.tracker}: [bold green]Successfully grabbed description "
            "from PassThePopcorn"
        )
        logger.info(
            f"{self.tracker}: Description after cleaning:\n"
            f"{str(description)[:1000]}...",
            extra={"markup": False},
        )
        if not meta.skipit and not meta.unattended:
            await self._interactive_description(meta, description)
            return
        self._save_description(meta, description)

    async def get_ptp_description(
        self, ptp_torrent_id: int | str, meta: Meta, is_disc: str
    ) -> list[Any]:
        url = f"{self.base_url}/torrents.php"
        logger.info(
            f"{self.tracker}: [yellow]Requesting description from {url} "
            f"with ID {ptp_torrent_id}"
        )
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(
                url,
                params={"id": ptp_torrent_id, "action": "get_description"},
                headers=self._api_headers(),
            )
        await asyncio.sleep(1)
        description, imagelist = BBCODE().clean_ptp_description(
            response.text, is_disc
        )
        await self._apply_ptp_description(meta, description)
        return imagelist if meta.keep_images else []

    def _log_group_response_error(self, response: Any) -> None:
        logger.info(
            f"{self.tracker}: [red]group lookup failed with HTTP "
            f"{response.status_code}[/red]"
        )
        if response.text:
            logger.info(
                f"{self.tracker}: [red]Response body (truncated): "
                f"{response.text[:200]}[/red]"
            )

    def _group_response_json(self, response: Any) -> dict[str, Any] | None:
        try:
            return cast(dict[str, Any], response.json())
        except json.JSONDecodeError:
            content_type = response.headers.get("content-type", "unknown")
            logger.info(
                f"{self.tracker}: [red]group lookup returned non-JSON content "
                f"(content-type: {content_type})[/red]"
            )
            if response.text:
                logger.info(
                    f"{self.tracker}: [red]Response body (truncated): "
                    f"{response.text[:200]}[/red]"
                )
            return None

    @staticmethod
    def _group_choice(movie: dict[str, Any]) -> str:
        title = movie.get("Title", "Unknown")
        year = movie.get("Year", "Unknown")
        group_id = movie.get("GroupId", "Unknown")
        return f"{title} ({year}) - Group ID: {group_id}"

    def _single_group_result(
        self, movie: dict[str, Any], imdb: int | str
    ) -> str | None:
        group_value = movie.get("GroupId")
        group_id = str(group_value) if group_value is not None else None
        logger.info(
            f"{self.tracker}: [green]Found single match for IMDb: "
            f"[yellow]tt{imdb}[/yellow] -> Group ID: "
            f"[yellow]{group_id}[/yellow][/green]"
        )
        logger.info(
            f"{self.tracker}: [green]Title: "
            f"[yellow]{movie.get('Title', 'Unknown')}[/yellow] "
            f"([yellow]{movie.get('Year', 'Unknown')}[/yellow])"
        )
        return group_id

    @staticmethod
    def _selected_group_id(
        movies: list[dict[str, Any]], selected: object
    ) -> str | None:
        for movie in movies:
            if PassThePopcorn._group_choice(movie) == selected:
                value = movie.get("GroupId")
                return str(value) if value is not None else None
        return None

    async def _multiple_group_result(
        self,
        movies: list[dict[str, Any]],
        imdb: int | str,
    ) -> str | None:
        logger.info(
            f"{self.tracker}: [yellow]Found {len(movies)} matches for IMDb: "
            f"tt{imdb}[/yellow]"
        )
        choices = [self._group_choice(movie) for movie in movies]
        skip_choice = "Skip - Don't use any of these matches"
        choices.append(skip_choice)
        try:
            selected = await prompt_in_thread(
                cli_ui.ask_choice,
                "Select the correct movie:",
                choices=choices,
            )
        except KeyboardInterrupt:
            logger.info(
                f"{self.tracker}: [yellow]Selection cancelled by user[/yellow]"
            )
            return None
        if selected == skip_choice:
            logger.info(
                f"{self.tracker}: [yellow]User chose to skip all matches[/yellow]"
            )
            return None
        group_id = self._selected_group_id(movies, selected)
        logger.info(
            f"{self.tracker}: [green]User selected: Group ID "
            f"[yellow]{group_id}[/yellow][/green]"
        )
        return group_id

    async def _search_group_result(
        self, data: dict[str, Any], imdb: int | str
    ) -> str | None:
        movies = cast(list[dict[str, Any]], data.get("Movies", []))
        total = int(data.get("TotalResults", len(movies)) or 0)
        if total == 0 or not movies:
            logger.info(
                f"{self.tracker}: [yellow]No results found for IMDb: "
                f"tt{imdb}[/yellow]"
            )
            return None
        if total == 1:
            return self._single_group_result(movies[0], imdb)
        return await self._multiple_group_result(movies, imdb)

    def _details_group_result(
        self, data: dict[str, Any], imdb: int | str
    ) -> str | None:
        group_id = data.get("GroupId")
        logger.info(
            f"{self.tracker}: [green]Matched IMDb: [yellow]tt{imdb}[/yellow] "
            f"to Group ID: [yellow]{group_id}[/yellow][/green]"
        )
        logger.info(
            f"{self.tracker}: [green]Title: [yellow]{data.get('Name')}[/yellow] "
            f"([yellow]{data.get('Year')}[/yellow])"
        )
        return str(group_id) if group_id is not None else None

    async def _group_from_response(
        self, data: dict[str, Any], imdb: int | str
    ) -> str | None:
        if "TotalResults" in data:
            return await self._search_group_result(data, imdb)
        if data.get("Page") == "Browse":
            return None
        if data.get("Page") == "Details":
            return self._details_group_result(data, imdb)
        return None

    async def get_group_by_imdb(self, imdb: int | str) -> str | None:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(
                url=f"{self.base_url}/torrents.php",
                headers=self._api_headers(),
                params={"imdb": imdb},
            )
        await asyncio.sleep(1)
        if response.status_code != 200:
            self._log_group_response_error(response)
            return None
        data = self._group_response_json(response)
        if data is None:
            return None
        try:
            return await self._group_from_response(data, imdb)
        except Exception:
            logger.info(
                f"{self.tracker}: [red]An error has occurred trying to find "
                "a group ID"
            )
            logger.info(
                f"{self.tracker}: [red]Please check that the site is online "
                "and your ApiUser/api_key values are correct"
            )
            return None

    async def get_torrent_info(
        self, imdb: int | str, meta: Meta
    ) -> dict[str, Any]:
        params = {"imdb": imdb, "action": "torrent_info", "fast": 1}
        headers = {
            "ApiUser": self.api_user,
            "api_key": self.api_key,
            "User-Agent": self.user_agent,
        }
        url = f"{self.base_url}/ajax.php"
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(
                url=url, params=params, headers=headers
            )
        await asyncio.sleep(1)
        tinfo = {}
        with contextlib.suppress(Exception):
            response = response.json()
            # console.print(f"[blue]Raw info API Response: {response}[/blue]")
            # title, plot, art, year, tags, Countries, Languages
            tinfo = {
                key: value
                for key, value in response[0].items()
                if value not in (None, "")
            }
            if not tinfo.get("tags"):
                tags = await self.get_tags(
                    [meta.genres, meta.keywords, meta.imdb_info["genres"]]
                )
                tinfo["tags"] = ", ".join(tags)
        return tinfo

    async def get_torrent_info_tmdb(self, meta: Meta) -> dict[str, Any]:
        tinfo = {
            "title": meta.title,
            "year": meta.year,
            "album_desc": meta.overview,
        }
        tags = await self.get_tags([meta.genres, meta.keywords])
        tinfo["tags"] = ", ".join(tags)
        return tinfo

    @staticmethod
    def _normalize_tag(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
        aliases = {
            "sciencefiction": "scifi",
            "sciencefictionmovie": "scifi",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _normalized_tag_list(cls, items: list[object]) -> list[str]:
        return [
            cls._normalize_tag(entry)
            for entry in items
            if isinstance(entry, str) and entry.strip()
        ]

    @classmethod
    def _normalized_tag_string(cls, item: str) -> list[str]:
        return [
            cls._normalize_tag(entry)
            for entry in item.split(",")
            if entry.strip()
        ]

    @classmethod
    def _normalized_tag_item(cls, item: object) -> list[str]:
        if isinstance(item, list):
            return cls._normalized_tag_list(cast(list[object], item))
        if isinstance(item, str) and item.strip():
            return cls._normalized_tag_string(item)
        return []

    @classmethod
    def _normalized_tag_inputs(cls, value: Any) -> list[str]:
        items = value if isinstance(value, list) else [value]
        normalized: list[str] = []
        for item in items:
            normalized.extend(cls._normalized_tag_item(item))
        return normalized

    @staticmethod
    def _ptp_tags() -> tuple[str, ...]:
        return (
            "action",
            "adventure",
            "animation",
            "arthouse",
            "asian",
            "biography",
            "camp",
            "comedy",
            "crime",
            "cult",
            "documentary",
            "drama",
            "experimental",
            "exploitation",
            "family",
            "fantasy",
            "film.noir",
            "history",
            "horror",
            "martial.arts",
            "musical",
            "mystery",
            "performance",
            "philosophy",
            "politics",
            "romance",
            "sci.fi",
            "short",
            "silent",
            "sport",
            "thriller",
            "video.art",
            "war",
            "western",
        )

    async def get_tags(self, check_against: Any) -> list[str]:
        normalized = self._normalized_tag_inputs(check_against)
        return [
            tag
            for tag in self._ptp_tags()
            if any(self._normalize_tag(tag) in item for item in normalized)
        ]

    @staticmethod
    def _ptp_quality(meta: Meta) -> str | None:
        if meta.sd == 1:
            return "Standard Definition"
        if meta.resolution in {"1440p", "1080p", "1080i", "720p"}:
            return "High Definition"
        if meta.resolution in {"2160p", "4320p", "8640p"}:
            return "Ultra High Definition"
        return None

    @staticmethod
    def _existing_release_names(
        torrents: list[dict[str, Any]], quality: str | None
    ) -> list[str]:
        if quality is None:
            return []
        return [
            f"[{torrent.get('Resolution')}] "
            f"{torrent.get('ReleaseName', 'RELEASE NAME NOT FOUND')}"
            for torrent in torrents
            if torrent.get("Quality") == quality
        ]

    async def search_existing(
        self, group_id: int | str, meta: Meta
    ) -> list[str]:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True
        ) as client:
            response = await client.get(
                f"{self.base_url}/torrents.php",
                headers=self._api_headers(),
                params={"id": group_id},
            )
        await asyncio.sleep(1)
        response.raise_for_status()
        data = cast(dict[str, Any], response.json())
        torrents = cast(list[dict[str, Any]], data.get("Torrents", []))
        return self._existing_release_names(torrents, self._ptp_quality(meta))

    def _selected_poster_host(self, meta: Meta) -> str:
        default_config = cast(dict[str, Any], self.config.get("DEFAULT", {}))
        return str(
            meta.imghost or default_config.get("img_host_1") or ""
        ).strip()

    def _poster_already_on_selected_host(
        self, image_url: str, selected_host: str
    ) -> bool:
        if not selected_host:
            return False
        hostname = (urlparse(image_url).hostname or "").lower()
        host_aliases = {
            "imgbb": ("ibb.co", "imgbb.com"),
            "imgbox": ("imgbox.com",),
            "pixhost": ("pixhost.to",),
            "lensdump": ("lensdump.com",),
            "onlyimage": ("onlyimage.org",),
            "ptscreens": ("ptscreens.com",),
            "passtheimage": ("passtheima.ge",),
            "seedpool_cdn": ("cdn.seedpool.org",),
            "utppm": ("utp.pm",),
        }
        aliases = host_aliases.get(selected_host, (selected_host,))
        return any(
            hostname == alias or hostname.endswith(f".{alias}")
            for alias in aliases
        )

    def _poster_extension(self, image_url: str, content_type: str) -> str:
        url_extension = Path(urlparse(image_url).path).suffix.lower()
        if url_extension in {".jpg", ".jpeg", ".png", ".webp"}:
            return url_extension

        content_type = content_type.split(";", 1)[0].strip().lower()
        content_type_extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        return content_type_extensions.get(content_type, ".jpg")

    async def _download_poster(self, meta: Meta, image_url: str) -> Path:
        directory = artwork_dir(meta.base_dir, meta.uuid)
        directory.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(image_url)
            response.raise_for_status()
        extension = self._poster_extension(
            image_url, response.headers.get("content-type", "")
        )
        path = directory / f"PTP_POSTER{extension}"
        await asyncio.to_thread(path.write_bytes, response.content)
        return path

    @staticmethod
    def _restore_imghost(meta: Meta, original_host: object) -> None:
        if original_host is None:
            meta.pop("imghost", None)
            return
        meta.imghost = str(original_host)

    async def _upload_poster(
        self, meta: Meta, poster_path: Path, selected_host: str
    ) -> str | None:
        original_host = meta.imghost
        meta.imghost = selected_host
        try:
            (
                uploaded_images,
                _,
            ) = await self.uploadscreens_manager.upload_screens(
                meta, 1, 1, 0, 1, [str(poster_path)], {}
            )
        finally:
            self._restore_imghost(meta, original_host)
        if not uploaded_images:
            return None
        value = uploaded_images[0].get("raw_url") or uploaded_images[0].get(
            "img_url"
        )
        return value if isinstance(value, str) and value else None

    def _poster_rehost_required(
        self, meta: Meta, image_url: str, selected_host: str
    ) -> bool:
        if not selected_host or meta.skip_imghost_upload:
            return False
        return not self._poster_already_on_selected_host(
            image_url, selected_host
        )

    async def rehost_poster_to_selected_host(
        self, meta: Meta, image_url: str
    ) -> str:
        selected_host = self._selected_poster_host(meta)
        if not self._poster_rehost_required(meta, image_url, selected_host):
            return image_url
        try:
            poster_path = await self._download_poster(meta, image_url)
            uploaded_url = await self._upload_poster(
                meta, poster_path, selected_host
            )
            return uploaded_url or image_url
        except Exception as error:
            logger.info(
                f"{self.tracker}: [red]poster rehost to {selected_host} failed: "
                f"{error}"
            )
            return image_url

    @staticmethod
    def _runtime_value(runtime: object) -> int:
        if not isinstance(runtime, (str, int, float)):
            return 60
        try:
            return int(runtime)
        except TypeError, ValueError:
            return 60

    @classmethod
    def _runtime_type(cls, runtime: object) -> str:
        value = cls._runtime_value(runtime)
        return "Feature Film" if value >= 45 or value == 0 else "Short Film"

    @classmethod
    def _imdb_release_type(cls, imdb_info: dict[str, Any]) -> str | None:
        value = imdb_info.get("type")
        if value is None:
            return None
        imdb_type = str(value).lower()
        mapping = {
            "short": "Short Film",
            "tv mini series": "Miniseries",
            "comedy": "Stand-up Comedy",
            "concert": "Live Performance",
        }
        if imdb_type in {"movie", "tv movie", "tvmovie"}:
            return cls._runtime_type(imdb_info.get("runtime", 60))
        return mapping.get(imdb_type)

    @staticmethod
    def _keyword_release_type(keywords: set[str]) -> str | None:
        rules = (
            ({"short", "short film"}, "Short Film"),
            ({"stand-up comedy"}, "Stand-up Comedy"),
            ({"concert"}, "Live Performance"),
            ({"miniseries"}, "Miniseries"),
        )
        for terms, label in rules:
            if keywords & terms:
                return label
        return None

    @classmethod
    def _tmdb_release_type(cls, meta: Meta) -> str | None:
        tmdb_type = str(meta.tmdb_type or "movie").lower()
        if tmdb_type == "miniseries":
            return "Miniseries"
        if tmdb_type == "movie":
            return cls._runtime_type(meta.runtime)
        return None

    @classmethod
    def _metadata_release_type(cls, meta: Meta) -> str | None:
        keywords = {str(keyword).lower() for keyword in meta.keywords}
        keyword_type = cls._keyword_release_type(keywords)
        return keyword_type or cls._tmdb_release_type(meta)

    @staticmethod
    def _prompt_release_type() -> str | None:
        choices = [
            "Feature Film",
            "Short Film",
            "Miniseries",
            "Stand-up Comedy",
            "Concert",
            "Movie Collection",
        ]
        value = cli_ui.ask_choice("Select the proper type", choices=choices)
        return "Live Performance" if value == "Concert" else value

    def get_type(self, imdb_info: dict[str, Any], meta: Meta) -> str | None:
        ptp_type = self._imdb_release_type(imdb_info)
        if imdb_info.get("type") is None:
            ptp_type = self._metadata_release_type(meta)
        mode = meta.mode if meta.mode is not None else "non_cli"
        if ptp_type is None and mode == "cli":
            return self._prompt_release_type()
        return ptp_type

    @staticmethod
    def _bd_codec(meta: Meta) -> str:
        try:
            size = float(meta.bdinfo["size"])
        except KeyError, TypeError, ValueError:
            size = 0.0
        for threshold in (25, 50, 66, 100):
            if size < threshold:
                return f"BD{threshold}"
        return "BD100"

    @staticmethod
    def _dvd_codec(meta: Meta) -> str:
        if "DVD5" in meta.dvd_size:
            return "DVD5"
        if "DVD9" in meta.dvd_size:
            return "DVD9"
        return ""

    @staticmethod
    def _file_codec(meta: Meta) -> str:
        mapping = {
            "AVC": "H.264",
            "H.264": "H.264",
            "HEVC": "H.265",
            "H.265": "H.265",
        }
        value = (
            meta.video_codec
            if meta.video_codec is not None
            else meta.video_encode
        )
        search_codec = value if isinstance(value, str) else ""
        codec = mapping.get(search_codec, search_codec)
        return (
            codec.replace("H.", "x")
            if meta.has_encode_settings is True
            else codec
        )

    def get_codec(self, meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return self._bd_codec(meta)
        if meta.is_disc == "DVD":
            return self._dvd_codec(meta)
        return self._file_codec(meta)

    @staticmethod
    def _uses_custom_resolution(meta: Meta, resolution: str) -> bool:
        if resolution == "OTHER" and meta.is_disc != "BDMV":
            return True
        return bool(meta.sd == 1 and meta.type in {"WEBDL", "DVDRIP"})

    @staticmethod
    def _custom_resolution(meta: Meta) -> str | None:
        if meta.video_width and meta.video_height:
            return f"{meta.video_width}x{meta.video_height}"
        return None

    def get_resolution(self, meta: Meta) -> tuple[str, str | None]:
        resolution = (
            meta.resolution if meta.resolution is not None else "OTHER"
        )
        other_res = None
        if self._uses_custom_resolution(meta, resolution):
            resolution = "Other"
            other_res = self._custom_resolution(meta)
        if meta.is_disc == "DVD":
            resolution = str(meta.source or "")
        return resolution, other_res

    def get_container(self, meta: Meta) -> str | None:
        container = None
        if meta.is_disc == "BDMV":
            container = "m2ts"
        elif meta.is_disc == "DVD":
            container = "VOB IFO"
        else:
            ext = Path(meta.filelist[0]).suffix
            containermap = {".mkv": "MKV", ".mp4": "MP4"}
            container = containermap.get(ext, "Other")
        return container

    def get_source(self, source: str) -> str:
        sources = {
            "Blu-ray": "Blu-ray",
            "BluRay": "Blu-ray",
            "HD DVD": "HD-DVD",
            "HDDVD": "HD-DVD",
            "Web": "WEB",
            "HDTV": "HDTV",
            "UHDTV": "HDTV",
            "NTSC": "DVD",
            "PAL": "DVD",
        }
        return sources.get(source, "OtherR")

    @staticmethod
    def _subtitle_language(track: dict[str, Any]) -> object:
        language = track.get("Language_String2", track.get("Language"))
        if language != "en":
            return language
        if track.get("Forced", "") == "Yes":
            return "en (Forced)"
        title = track.get("Title", "")
        if isinstance(title, str) and "intertitles" in title.lower():
            return "en (Intertitles)"
        return language

    def _subtitle_id(self, language: object) -> int | None:
        for aliases, sub_id in self.sub_lang_map.items():
            if language in aliases:
                return sub_id
        return None

    def _subtitle_ids_from_tracks(
        self, tracks: list[dict[str, Any]]
    ) -> list[int]:
        ids: list[int] = []
        for track in tracks:
            if track.get("@type") != "Text":
                continue
            sub_id = self._subtitle_id(self._subtitle_language(track))
            if sub_id is not None and sub_id not in ids:
                ids.append(sub_id)
        return ids

    def _subtitle_ids_from_languages(self, languages: list[Any]) -> list[int]:
        ids: list[int] = []
        for language in languages:
            sub_id = self._subtitle_id(language)
            if sub_id is not None and sub_id not in ids:
                ids.append(sub_id)
        return ids

    @staticmethod
    def _subtitle_tracks(meta: Meta) -> list[dict[str, Any]]:
        media_info = meta.mediainfo
        if meta.is_disc == "DVD":
            media_info = json.loads(
                MediaInfo.parse(meta.discs[0]["ifo"], output="JSON")
            )
        return cast(
            list[dict[str, Any]],
            media_info.get("media", {}).get("track", []),
        )

    def get_subtitles(self, meta: Meta) -> list[int]:
        if meta.is_disc == "BDMV":
            languages = cast(list[Any], meta.bdinfo.get("subtitles", []))
            ids = self._subtitle_ids_from_languages(languages)
        else:
            ids = self._subtitle_ids_from_tracks(self._subtitle_tracks(meta))
        return ids or [44]

    @staticmethod
    def _remove_no_subtitle(sub_langs: list[int]) -> None:
        if 44 in sub_langs:
            sub_langs.remove(44)

    @staticmethod
    def _append_unique(values: list[int], value: int) -> None:
        if value not in values:
            values.append(value)

    def _apply_hardcoded_language(self, sub_langs: list[int]) -> None:
        value = (
            cli_ui.ask_string("Enter language code for HC Subtitle languages")
            or ""
        ).strip()
        if not value:
            return
        sub_id = self._subtitle_id(value)
        if sub_id is not None:
            self._append_unique(sub_langs, sub_id)

    def _apply_trumpable_option(
        self,
        option: str,
        sub_langs: list[int],
        trumpable: list[int],
    ) -> None:
        if option == "English Hardcoded Subs (Full)":
            self._append_unique(trumpable, 4)
            self._append_unique(sub_langs, 3)
            self._remove_no_subtitle(sub_langs)
            return
        if option == "English Hardcoded Subs (Forced)":
            self._append_unique(trumpable, 50)
            self._append_unique(sub_langs, 50)
            self._remove_no_subtitle(sub_langs)
            return
        if option == "No English Subs":
            self._append_unique(trumpable, 14)
            return
        if option == "Hardcoded Subs (Non-English)":
            self._append_unique(trumpable, 15)
            self._apply_hardcoded_language(sub_langs)

    def get_trumpable(
        self, sub_langs: list[int]
    ) -> tuple[list[int] | None, list[int]]:
        choices = [
            "English Hardcoded Subs (Full)",
            "English Hardcoded Subs (Forced)",
            "No English Subs",
            "English Softsubs Exist (Mislabeled)",
            "Hardcoded Subs (Non-English)",
        ]
        options = cli_ui.select_choices(
            "Please select any/all applicable options:", choices=choices
        )
        trumpable: list[int] = []
        for option in options:
            self._apply_trumpable_option(option, sub_langs, trumpable)
        unique_subs = list(dict.fromkeys(sub_langs))
        unique_trumpable = list(dict.fromkeys(trumpable))
        return (unique_trumpable or None), unique_subs

    @staticmethod
    def _collection_remaster(meta: Meta) -> str:
        mapping = {
            "WARNER ARCHIVE": "Warner Archive Collection",
            "WARNER ARCHIVE COLLECTION": "Warner Archive Collection",
            "WAC": "Warner Archive Collection",
            "CRITERION": "The Criterion Collection",
            "CRITERION COLLECTION": "The Criterion Collection",
            "CC": "The Criterion Collection",
            "MASTERS OF CINEMA": "Masters of Cinema",
            "MOC": "Masters of Cinema",
        }
        return mapping.get(meta.distributor, "")

    @staticmethod
    def _edition_remaster(meta: Meta) -> str:
        edition = str(meta.edition or "")
        lowered = edition.lower()
        aliases = (
            ("director's cut", "Director's Cut"),
            ("extended", "Extended Edition"),
            ("theatrical", "Theatrical Cut"),
            ("rifftrax", "Theatrical Cut"),
            ("uncut", "Uncut"),
            ("unrated", "Unrated"),
        )
        for token, label in aliases:
            if token in lowered:
                return label
        return edition

    @staticmethod
    def _audio_remaster_tags(meta: Meta) -> list[str]:
        tags: list[str] = []
        mapping = (
            ("DTS:X", "DTS:X"),
            ("Atmos", "Dolby Atmos"),
            ("Dual", "Dual Audio"),
            ("Dubbed", "English Dub"),
        )
        for token, label in mapping:
            if token in meta.audio:
                tags.append(label)
        return tags

    @staticmethod
    def _ten_bit_tag(meta: Meta) -> str:
        return (
            "10-bit" if not meta.hdr.strip() and meta.bit_depth == "10" else ""
        )

    @staticmethod
    def _dolby_vision_tag(meta: Meta) -> str:
        return "Dolby Vision" if "DV" in meta.hdr else ""

    @staticmethod
    def _hdr_standard_tag(meta: Meta) -> str:
        if "HDR10+" in meta.hdr:
            return "HDR10+"
        return "HDR10" if "HDR" in meta.hdr else ""

    @staticmethod
    def _hlg_tag(meta: Meta) -> str:
        return "HLG" if "HLG" in meta.hdr else ""

    @classmethod
    def _hdr_remaster_tags(cls, meta: Meta) -> list[str]:
        return list(
            filter(
                None,
                (
                    cls._ten_bit_tag(meta),
                    cls._dolby_vision_tag(meta),
                    cls._hdr_standard_tag(meta),
                    cls._hlg_tag(meta),
                ),
            )
        )

    def get_remaster_title(self, meta: Meta) -> str:
        tags: list[str] = []
        collection = self._collection_remaster(meta)
        edition = self._edition_remaster(meta)
        if collection:
            tags.append(collection)
        if edition:
            tags.append(edition)
        if meta.type == "REMUX":
            tags.append("Remux")
        tags.extend(self._audio_remaster_tags(meta))
        tags.extend(self._hdr_remaster_tags(meta))
        if meta.has_commentary is True:
            tags.append("With Commentary")
        return " / ".join(tags)

    def convert_bbcode(self, desc: str) -> str:
        desc = desc.replace("[spoiler", "[hide").replace(
            "[/spoiler]", "[/hide]"
        )
        desc = desc.replace("[center]", "[align=center]").replace(
            "[/center]", "[/align]"
        )
        desc = desc.replace("[left]", "[align=left]").replace(
            "[/left]", "[/align]"
        )
        desc = desc.replace("[right]", "[align=right]").replace(
            "[/right]", "[/align]"
        )
        desc = desc.replace("[sup]", "").replace("[/sup]", "")
        desc = desc.replace("[sub]", "").replace("[/sub]", "")
        desc = desc.replace("[alert]", "").replace("[/alert]", "")
        desc = desc.replace("[note]", "").replace("[/note]", "")
        desc = desc.replace("[h1]", "[u][b]").replace("[/h1]", "[/b][/u]")
        desc = desc.replace("[h2]", "[u][b]").replace("[/h2]", "[/b][/u]")
        desc = desc.replace("[h3]", "[u][b]").replace("[/h3]", "[/b][/u]")
        desc = desc.replace("[list]", "").replace("[/list]", "")
        desc = desc.replace("[ul]", "").replace("[/ul]", "")
        desc = desc.replace("[ol]", "").replace("[/ol]", "")
        return re.sub(r"\[img=[^\]]+\]", "[img]", desc)

    @staticmethod
    def _description_base(meta: Meta) -> str:
        from src.domain_models.release_description import base_description

        base = base_description(meta)
        if not meta.scene_nfo_file:
            return base
        return re.sub(
            r"\[center\]\[spoiler=.*? NFO:\]\[code\](.*?)\[/code\]\[/spoiler\]\[/center\]",
            "",
            base,
            flags=re.DOTALL,
        )

    def _multi_screen_count(self) -> int:
        count = int(self.config["DEFAULT"].get("multiScreens", 2))
        if count >= 2:
            return count
        logger.info(
            f"{self.tracker}: [yellow]requires at least 2 screenshots for "
            "multi disc/file content, overriding config"
        )
        return 2

    def _description_images(self, meta: Meta) -> list[dict[str, Any]]:
        if meta.skip_imghost_upload:
            return []
        value = get_tracker_image_collection(meta, self.tracker, "screenshots")
        return (
            cast(list[dict[str, Any]], value)
            if isinstance(value, list)
            else []
        )

    @staticmethod
    def _pack_image_path(meta: Meta) -> Path:
        return (
            Path(meta.base_dir) / "tmp" / meta.uuid / "pack_image_links.json"
        )

    def _approved_pack_image(self, meta: Meta, image: dict[str, Any]) -> bool:
        raw_url = str(image.get("raw_url", ""))
        try:
            hostname = urlparse(raw_url).netloc
            host_key = hostname.split(".")[0] if hostname else ""
        except Exception:
            logger.debug(
                f"{self.tracker}: [yellow]Could not parse URL: {raw_url}[/yellow]"
            )
            return False
        if host_key in self.approved_image_hosts:
            return True
        if meta.debug:
            logger.info(
                f"{self.tracker}: [yellow]Filtering out image from "
                f"non-approved host: {hostname}[/yellow]"
            )
        return False

    def _filter_pack_key(
        self,
        meta: Meta,
        key_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        images = cast(list[dict[str, Any]], key_data.get("images", []))
        return [
            image for image in images if self._approved_pack_image(meta, image)
        ]

    def _filtered_pack_keys(
        self, meta: Meta, keys: dict[str, Any]
    ) -> dict[str, Any]:
        filtered: dict[str, Any] = {}
        for key_name, key_data in keys.items():
            if not isinstance(key_data, dict):
                continue
            images = self._filter_pack_key(meta, key_data)
            if images:
                filtered[key_name] = {"images": images, "count": len(images)}
                continue
            logger.debug(
                f"{self.tracker}: [yellow]Removed key '{key_name}' - "
                "no approved image hosts[/yellow]"
            )
        return filtered

    @staticmethod
    def _pack_image_total(keys: dict[str, Any]) -> int:
        return sum(
            int(cast(dict[str, Any], value).get("count", 0) or 0)
            for value in keys.values()
        )

    def _filtered_pack_data(
        self, meta: Meta, data: dict[str, Any]
    ) -> dict[str, Any]:
        keys = cast(dict[str, Any], data.get("keys", {}))
        filtered = self._filtered_pack_keys(meta, keys)
        total = self._pack_image_total(filtered)
        if total >= 3:
            return {"keys": filtered, "total_count": total}
        logger.debug(
            f"{self.tracker}: [yellow]Invalidating pack images - less "
            "than 3 approved images total[/yellow]"
        )
        return {}

    async def _load_pack_images(self, meta: Meta) -> dict[str, Any]:
        pack_path = self._pack_image_path(meta)
        if not pack_path.exists():
            return {}
        try:
            async with aiofiles.open(
                pack_path, encoding="utf-8"
            ) as file_handle:
                content = await file_handle.read()
            raw = (
                cast(dict[str, Any], json.loads(content))
                if content.strip()
                else {}
            )
            data = self._filtered_pack_data(meta, raw)
        except Exception as error:
            logger.warning(
                f"{self.tracker}: [yellow]Warning: Could not load pack image "
                f"data: {error!s}[/yellow]"
            )
            return {}
        if data:
            logger.debug(
                f"{self.tracker}: [green]Loaded previously uploaded images "
                f"from {pack_path}"
            )
            logger.debug(
                f"{self.tracker}: [blue]Found {data.get('total_count', 0)} "
                f"approved images across {len(data.get('keys', {}))} keys[/blue]"
            )
        return data

    def _write_converted_base(self, desc: io.StringIO, base: str) -> None:
        converted = self.convert_bbcode(base)
        if converted.strip():
            desc.write(converted)
            desc.write("\n\n")

    def _write_tonemapped_header(self, desc: io.StringIO, meta: Meta) -> None:
        try:
            header = self.config["DEFAULT"].get("tonemapped_header")
            if not meta.tonemapped or not header:
                return
            desc.write(self.convert_bbcode(str(header)))
            desc.write("\n\n")
        except Exception as error:
            logger.warning(
                f"{self.tracker}: [yellow]Warning: Error setting tonemapped "
                f"header: {error!s}[/yellow]"
            )

    @staticmethod
    def _write_images(
        desc: io.StringIO,
        images: list[dict[str, Any]],
        limit: int | None = None,
    ) -> None:
        selected = images if limit is None else images[:limit]
        for image in selected:
            raw_url = str(image.get("raw_url", ""))
            desc.write(f"[img]{raw_url}[/img]\n")

    @staticmethod
    def _image_copy(image: dict[str, Any]) -> dict[str, str]:
        return {
            "img_url": str(image.get("img_url", "")),
            "raw_url": str(image.get("raw_url", "")),
            "web_url": str(image.get("web_url", "")),
        }

    @classmethod
    def _pack_key_images(
        cls, data: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        keys = data.get("keys", {})
        if not isinstance(keys, dict):
            return []
        key_data = keys.get(key, {})
        if not isinstance(key_data, dict):
            return []
        images = key_data.get("images", [])
        return (
            cast(list[dict[str, Any]], images)
            if isinstance(images, list)
            else []
        )

    def _restore_pack_images(
        self,
        meta: Meta,
        pack_data: dict[str, Any],
        key: str,
    ) -> bool:
        saved = self._pack_key_images(pack_data, key)
        if not saved:
            return False
        logger.debug(
            f"{self.tracker}: [yellow]Using saved images from "
            f"pack_image_links.json for {key}"
        )
        meta[key] = [self._image_copy(image) for image in saved]
        return True

    async def _persist_meta(self, meta: Meta) -> None:
        path = Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"
        async with aiofiles.open(path, "w", encoding="utf-8") as file_handle:
            await file_handle.write(
                json.dumps(meta.to_dict(), indent=4, cls=PathAwareEncoder)
            )

    async def _upload_extra_images(
        self,
        meta: Meta,
        key: str,
        screens: list[str],
        multi_screens: int,
    ) -> list[dict[str, Any]]:
        if not screens or meta.skip_imghost_upload:
            return []
        uploaded, _ = await self.uploadscreens_manager.upload_screens(
            meta,
            multi_screens,
            1,
            0,
            multi_screens,
            screens,
            {key: meta[key]},
            allowed_hosts=self.approved_image_hosts,
        )
        if uploaded:
            await self.save_image_links(meta, key, uploaded)
        return uploaded

    def _append_uploaded_images(
        self,
        desc: io.StringIO,
        meta: Meta,
        key: str,
        images: list[dict[str, Any]],
    ) -> None:
        for image in images:
            copied = self._image_copy(image)
            meta[key].append(copied)
            desc.write(f"[img]{copied['raw_url']}[/img]\n")

    def _write_dvd_info(self, desc: io.StringIO, disc: dict[str, Any]) -> None:
        desc.write(f"[b][size=3]{disc['name']}:[/size][/b]\n")
        desc.write(f"[mediainfo]{disc['ifo_mi_full']}[/mediainfo]\n")
        desc.write(f"[mediainfo]{disc['vob_mi_full']}[/mediainfo]\n\n")

    def _write_bdmv_info(
        self, desc: io.StringIO, disc: dict[str, Any]
    ) -> None:
        desc.write(f"[mediainfo]{disc['summary']}[/mediainfo]\n\n")

    def _write_initial_bdmv_media(
        self, desc: io.StringIO, meta: Meta, disc: dict[str, Any]
    ) -> list[str]:
        keys = [key for key in disc if key.startswith("bdinfo")]
        if len(keys) > 1:
            edition = str(meta.bdinfo.get("edition", "Unknown Edition"))
            desc.write(f"[b]{edition}[/b]\n\n")
        self._write_bdmv_info(desc, disc)
        return keys

    def _write_initial_disc_media(
        self, desc: io.StringIO, meta: Meta, disc: dict[str, Any]
    ) -> list[str]:
        disc_type = disc["type"]
        if disc_type == "DVD":
            self._write_dvd_info(desc, disc)
            return []
        return (
            self._write_initial_bdmv_media(desc, meta, disc)
            if disc_type == "BDMV"
            else []
        )

    def _write_initial_disc_block(
        self,
        desc: io.StringIO,
        meta: Meta,
        disc: dict[str, Any],
        base: str,
        images: list[dict[str, Any]],
        image_limit: int,
    ) -> list[str]:
        keys = self._write_initial_disc_media(desc, meta, disc)
        self._write_converted_base(desc, base)
        if disc["type"] == "BDMV":
            self._write_tonemapped_header(desc, meta)
        self._write_images(desc, images, image_limit)
        desc.write("\n")
        return keys

    async def _write_playlist_block(
        self,
        desc: io.StringIO,
        meta: Meta,
        disc: dict[str, Any],
        pack_data: dict[str, Any],
        index: int,
        key: str,
        multi_screens: int,
    ) -> None:
        image_key = f"new_images_playlist_{index}"
        bdinfo = cast(dict[str, Any], disc[key])
        edition = bdinfo.get("edition", "Unknown Edition")
        summary = disc.get(f"summary_{index}", "No summary available")
        restored = self._restore_pack_images(meta, pack_data, image_key)
        desc.write(
            f"\n[b]{edition}[/b]\n\n" if restored else f"\n[b]{edition}[/b]\n"
        )
        desc.write(f"[mediainfo]{summary}[/mediainfo]\n\n")
        if restored:
            logger.debug(
                f"{self.tracker}: [yellow]Using original uploaded images "
                "for first disc"
            )
            self._write_images(
                desc, cast(list[dict[str, Any]], meta[image_key])
            )
            return
        meta.retry_count += 1
        meta[image_key] = []
        screens = [
            file.name
            for file in manifest_files(
                meta.base_dir, meta.uuid, f"PLAYLIST_{index}"
            )
        ]
        if not screens:
            logger.warning(
                f"{self.tracker}: Missing prepared screenshots for "
                f"PLAYLIST_{index}; skipping its images."
            )
        uploaded = await self._upload_extra_images(
            meta, image_key, screens, multi_screens
        )
        self._append_uploaded_images(desc, meta, image_key, uploaded)
        await self._persist_meta(meta)

    async def _write_single_disc(
        self,
        desc: io.StringIO,
        meta: Meta,
        disc: dict[str, Any],
        base: str,
        images: list[dict[str, Any]],
        pack_data: dict[str, Any],
        multi_screens: int,
    ) -> None:
        keys = self._write_initial_disc_block(
            desc, meta, disc, base, images, meta.screens
        )
        for index, key in enumerate(keys[1:], start=1):
            await self._write_playlist_block(
                desc,
                meta,
                disc,
                pack_data,
                index,
                key,
                multi_screens,
            )

    async def _multi_bdmv_screens(
        self,
        desc: io.StringIO,
        meta: Meta,
        image_key: str,
        index: int,
        multi_screens: int,
    ) -> None:
        screens = [
            file.name
            for file in manifest_files(
                meta.base_dir, meta.uuid, f"FILE_{index}"
            )
        ]
        if not screens:
            logger.warning(
                f"{self.tracker}: Missing prepared screenshots for FILE_{index}; "
                "skipping its images."
            )
        uploaded = await self._upload_extra_images(
            meta, image_key, screens, multi_screens
        )
        if not uploaded:
            return
        self._append_uploaded_images(desc, meta, image_key, uploaded)
        desc.write("\n")
        await self._persist_meta(meta)

    async def _write_multi_bdmv_disc(
        self,
        desc: io.StringIO,
        meta: Meta,
        disc: dict[str, Any],
        base: str,
        images: list[dict[str, Any]],
        pack_data: dict[str, Any],
        index: int,
        multi_screens: int,
    ) -> None:
        if index == 0:
            self._write_bdmv_info(desc, disc)
            self._write_converted_base(desc, base)
            self._write_tonemapped_header(desc, meta)
            self._write_images(desc, images, multi_screens)
            desc.write("\n")
            return
        self._write_bdmv_info(desc, disc)
        self._write_converted_base(desc, base)
        image_key = f"new_images_disc_{index}"
        restored = self._restore_pack_images(meta, pack_data, image_key)
        if restored:
            self._write_images(
                desc, cast(list[dict[str, Any]], meta[image_key])
            )
            desc.write("\n")
        else:
            meta.retry_count += 1
            meta[image_key] = []
        await self._multi_bdmv_screens(
            desc, meta, image_key, index, multi_screens
        )

    def _dvd_screen_names(self, meta: Meta, index: int) -> list[str]:
        disc_name = glob.escape(str(meta.discs[index]["name"]))
        return [
            file.name
            for file in screenshots_dir(meta.base_dir, meta.uuid).glob(
                f"{disc_name}-*.png"
            )
        ]

    async def _ensure_dvd_screens(
        self, meta: Meta, index: int, multi_screens: int
    ) -> list[str]:
        screens = self._dvd_screen_names(meta, index)
        if screens:
            return screens
        try:
            await self.takescreens_manager.dvd_screenshots(
                meta, index, multi_screens, True
            )
        except Exception as error:
            logger.info(
                f"{self.tracker}: Error during DVD screenshot capture: {error}",
                extra={"markup": False},
            )
        return self._dvd_screen_names(meta, index)

    async def _write_multi_dvd_disc(
        self,
        desc: io.StringIO,
        meta: Meta,
        disc: dict[str, Any],
        base: str,
        images: list[dict[str, Any]],
        pack_data: dict[str, Any],
        index: int,
        multi_screens: int,
    ) -> None:
        self._write_dvd_info(desc, disc)
        self._write_converted_base(desc, base)
        if index == 0:
            self._write_images(desc, images, multi_screens)
            desc.write("\n")
            return
        image_key = f"new_images_disc_{index}"
        if self._restore_pack_images(meta, pack_data, image_key):
            self._write_images(
                desc, cast(list[dict[str, Any]], meta[image_key])
            )
            desc.write("\n")
        else:
            meta.retry_count += 1
            meta[image_key] = []
            screens = await self._ensure_dvd_screens(
                meta, index, multi_screens
            )
            uploaded = await self._upload_extra_images(
                meta, image_key, screens, multi_screens
            )
            if uploaded:
                self._append_uploaded_images(desc, meta, image_key, uploaded)
                desc.write("\n")
        await self._persist_meta(meta)

    async def _write_multiple_discs(
        self,
        desc: io.StringIO,
        meta: Meta,
        discs: list[dict[str, Any]],
        base: str,
        images: list[dict[str, Any]],
        pack_data: dict[str, Any],
        multi_screens: int,
    ) -> None:
        if "retry_count" not in meta:
            meta.retry_count = 0
        for index, disc in enumerate(discs):
            if disc["type"] == "BDMV":
                await self._write_multi_bdmv_disc(
                    desc,
                    meta,
                    disc,
                    base,
                    images,
                    pack_data,
                    index,
                    multi_screens,
                )
            elif disc["type"] == "DVD":
                await self._write_multi_dvd_disc(
                    desc,
                    meta,
                    disc,
                    base,
                    images,
                    pack_data,
                    index,
                    multi_screens,
                )

    def _write_source_quote(self, desc: io.StringIO, meta: Meta) -> None:
        if (
            meta.type == "WEBDL"
            and meta.service_longname != ""
            and not meta.description
            and self.web_source is True
        ):
            desc.write(
                f"[quote][align=center]This release is sourced from "
                f"{meta.service_longname}[/align][/quote]"
            )

    @staticmethod
    def _mediainfo_path(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / "MEDIAINFO.txt"

    async def _read_primary_mediainfo(self, meta: Meta) -> str:
        async with aiofiles.open(
            self._mediainfo_path(meta), encoding="utf-8"
        ) as file_handle:
            return await file_handle.read()

    @staticmethod
    def _comparison_groups(meta: Meta) -> dict[str, Any]:
        groups = meta.comparison_groups
        if not meta.comparison or not isinstance(groups, dict):
            return {}
        return groups

    @staticmethod
    def _comparison_keys(groups: dict[str, Any]) -> list[str]:
        return sorted(groups.keys(), key=lambda value: int(value))

    @staticmethod
    def _comparison_row(
        groups: dict[str, Any], keys: list[str], image_index: int
    ) -> str:
        images: list[str] = []
        for key in keys:
            urls = groups[key]["urls"]
            if image_index >= len(urls):
                continue
            raw_url = urls[image_index].get("raw_url", "")
            if raw_url:
                images.append(f"[img]{raw_url}[/img] ")
        return "".join(images)

    def _write_comparison(self, desc: io.StringIO, meta: Meta) -> None:
        groups = self._comparison_groups(meta)
        if not groups:
            return
        keys = self._comparison_keys(groups)
        names = [groups[key].get("name", f"Group {key}") for key in keys]
        counts = [len(groups[key]["urls"]) for key in keys]
        desc.write("\n")
        desc.write(f"[comparison={', '.join(names)}]\n")
        for image_index in range(min(counts)):
            desc.write(self._comparison_row(groups, keys, image_index))
            desc.write("\n")
        desc.write("[/comparison]\n\n")

    async def _write_single_file(
        self,
        desc: io.StringIO,
        meta: Meta,
        base: str,
        images: list[dict[str, Any]],
    ) -> None:
        self._write_source_quote(desc, meta)
        desc.write(
            f"[mediainfo]{await self._read_primary_mediainfo(meta)}[/mediainfo]\n"
        )
        self._write_converted_base(desc, base)
        self._write_comparison(desc, meta)
        self._write_tonemapped_header(desc, meta)
        self._write_images(desc, images, meta.screens)
        desc.write("\n")

    async def _additional_file_mediainfo(self, meta: Meta, file: str) -> str:
        media_info = str(MediaInfo.parse(file, output="STRING", full=False))
        path = (
            Path(meta.base_dir) / "tmp" / meta.uuid / "TEMP_PTP_MEDIAINFO.txt"
        )
        async with aiofiles.open(
            path, "w", newline="", encoding="utf-8"
        ) as file_handle:
            await file_handle.write(media_info.replace(file, Path(file).name))
        async with aiofiles.open(path, encoding="utf-8") as file_handle:
            return await file_handle.read()

    async def _ensure_file_screens(
        self,
        meta: Meta,
        file: str,
        index: int,
        multi_screens: int,
    ) -> list[str]:
        group = f"FILE_{index}"
        screens = [
            item.name
            for item in manifest_files(meta.base_dir, meta.uuid, group)
        ]
        if screens:
            return screens
        try:
            await self.takescreens_manager.screenshots(
                file,
                group,
                meta.uuid,
                meta.base_dir,
                meta,
                multi_screens,
                True,
                "",
                capture_group=group,
            )
        except Exception as error:
            logger.info(
                f"{self.tracker}: Error during generic screenshot capture: "
                f"{error}",
                extra={"markup": False},
            )
        return [
            item.name
            for item in manifest_files(meta.base_dir, meta.uuid, group)
        ]

    async def _write_additional_file(
        self,
        desc: io.StringIO,
        meta: Meta,
        file: str,
        index: int,
        pack_data: dict[str, Any],
        multi_screens: int,
    ) -> None:
        desc.write(
            f"[mediainfo]{await self._additional_file_mediainfo(meta, file)}"
            "[/mediainfo]\n"
        )
        image_key = f"new_images_file_{index}"
        if self._restore_pack_images(meta, pack_data, image_key):
            self._write_images(
                desc, cast(list[dict[str, Any]], meta[image_key])
            )
            desc.write("\n")
        else:
            meta.retry_count += 1
            meta[image_key] = []
            screens = await self._ensure_file_screens(
                meta, file, index, multi_screens
            )
            uploaded = await self._upload_extra_images(
                meta, image_key, screens, multi_screens
            )
            self._append_uploaded_images(desc, meta, image_key, uploaded)
            if uploaded:
                desc.write("\n")
        await self._persist_meta(meta)

    async def _write_multiple_files(
        self,
        desc: io.StringIO,
        meta: Meta,
        filelist: list[str],
        base: str,
        images: list[dict[str, Any]],
        pack_data: dict[str, Any],
        multi_screens: int,
    ) -> None:
        for index, file in enumerate(filelist):
            if index == 0:
                self._write_source_quote(desc, meta)
                self._write_converted_base(desc, base)
                desc.write(
                    f"[mediainfo]{await self._read_primary_mediainfo(meta)}"
                    "[/mediainfo]\n"
                )
                self._write_tonemapped_header(desc, meta)
                self._write_images(desc, images, multi_screens)
                desc.write("\n")
                continue
            await self._write_additional_file(
                desc,
                meta,
                file,
                index,
                pack_data,
                multi_screens,
            )

    async def _write_description_file(
        self, meta: Meta, description: str
    ) -> None:
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as file_handle:
            await file_handle.write(description)

    async def edit_desc(self, meta: Meta) -> None:
        base = self._description_base(meta)
        multi_screens = self._multi_screen_count()
        images = self._description_images(meta)
        pack_data = await self._load_pack_images(meta)
        desc = io.StringIO()
        discs = cast(list[dict[str, Any]], meta.discs)
        filelist = cast(list[str], meta.filelist)
        if len(discs) == 1:
            await self._write_single_disc(
                desc,
                meta,
                discs[0],
                base,
                images,
                pack_data,
                multi_screens,
            )
        elif len(discs) > 1:
            await self._write_multiple_discs(
                desc,
                meta,
                discs,
                base,
                images,
                pack_data,
                multi_screens,
            )
        elif len(filelist) == 1:
            await self._write_single_file(desc, meta, base, images)
        elif len(filelist) > 1:
            await self._write_multiple_files(
                desc,
                meta,
                filelist,
                base,
                images,
                pack_data,
                multi_screens,
            )
        await self._write_description_file(meta, desc.getvalue())

    @staticmethod
    def _image_links_path(meta: Meta) -> Path:
        directory = Path(meta.base_dir) / "tmp" / meta.uuid
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "pack_image_links.json"

    async def _existing_image_links(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"keys": {}, "total_count": 0}
        try:
            async with aiofiles.open(path, encoding="utf-8") as file_handle:
                content = await file_handle.read()
            data = (
                cast(dict[str, Any], json.loads(content))
                if content.strip()
                else {}
            )
        except Exception as error:
            logger.warning(
                f"{self.tracker}: [yellow]Warning: Could not load existing "
                f"image data: {error!s}[/yellow]"
            )
            data = {}
        return data or {"keys": {}, "total_count": 0}

    @staticmethod
    def _image_link_bucket(
        data: dict[str, Any], image_key: str
    ) -> dict[str, Any]:
        keys = cast(dict[str, Any], data.setdefault("keys", {}))
        bucket = keys.setdefault(image_key, {"count": 0, "images": []})
        return cast(dict[str, Any], bucket)

    @staticmethod
    def _image_link_entry(image: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            "index": index,
            "raw_url": str(image.get("raw_url", "")),
            "web_url": str(image.get("web_url", "")),
            "img_url": str(image.get("img_url", "")),
        }

    @classmethod
    def _append_image_links(
        cls,
        data: dict[str, Any],
        image_key: str,
        image_list: list[dict[str, Any]],
    ) -> None:
        bucket = cls._image_link_bucket(data, image_key)
        images = cast(list[dict[str, Any]], bucket.setdefault("images", []))
        start = int(bucket.get("count", 0) or 0)
        images.extend(
            cls._image_link_entry(image, start + index)
            for index, image in enumerate(image_list)
        )
        bucket["count"] = len(images)
        keys = cast(dict[str, Any], data.get("keys", {}))
        data["total_count"] = sum(
            int(cast(dict[str, Any], value).get("count", 0) or 0)
            for value in keys.values()
        )

    async def _write_image_links(
        self,
        path: Path,
        data: dict[str, Any],
        image_key: str,
        added_count: int,
    ) -> Path | None:
        try:
            async with aiofiles.open(
                path, "w", encoding="utf-8"
            ) as file_handle:
                await file_handle.write(json.dumps(data, indent=2))
        except Exception as error:
            logger.info(
                f"{self.tracker}: [bold red]Error saving image links: "
                f"{error}[/bold red]"
            )
            return None
        logger.debug(
            f"{self.tracker}: [green]Saved {added_count} new images for key "
            f"'{image_key}' (total: {data['total_count']}):[/green]"
        )
        logger.debug(f"{self.tracker}: [blue]  - JSON: {path}[/blue]")
        return path

    async def save_image_links(
        self,
        meta: Meta,
        image_key: str,
        image_list: list[dict[str, Any]] | None = None,
    ) -> Path | None:
        if image_list is None:
            logger.info(
                f"{self.tracker}: [yellow]No image links to save.[/yellow]"
            )
            return None
        path = self._image_links_path(meta)
        data = await self._existing_image_links(path)
        self._append_image_links(data, image_key, image_list)
        return await self._write_image_links(
            path, data, image_key, len(image_list)
        )

    def _cookie_file(self, meta: Meta) -> Path:
        cookie_dir = Path(str(meta.base_dir)) / "data" / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        from src.integrations.trackers.cookie_auth import find_cookie_file

        return Path(find_cookie_file(meta.base_dir, self.tracker, self.config))

    def _saved_cookie_values(self, cookie_file: Path) -> dict[str, str]:
        raw_cookies = self.cookie_validator._load_cookies_dict_secure(
            str(cookie_file)
        )  # pyright: ignore[reportPrivateUsage]
        return {
            name: str(data.get("value", ""))
            for name, data in raw_cookies.items()
        }

    @staticmethod
    def _csrf_token_from_html(text: str) -> str | None:
        match = re.search(r'data-AntiCsrfToken="(.*)"', text)
        return match.group(1) if match else None

    async def _saved_session_token(
        self, cookies: dict[str, str]
    ) -> str | None:
        async with httpx.AsyncClient(
            cookies=cookies, timeout=30.0, follow_redirects=True
        ) as client:
            response = await client.get(f"{self.base_url}/upload.php")
            if not await self.validate_login(response):
                return None
            return self._csrf_token_from_html(response.text)

    def _clear_expired_cookie(self, cookie_file: Path) -> None:
        logger.info(
            f"{self.tracker}: [yellow]session expired. Clearing cookies and "
            "re-authenticating."
        )
        with contextlib.suppress(OSError):
            cookie_file.unlink()

    def _passkey(self) -> str:
        match = re.match(
            r"https?://please\.passthepopcorn\.me:?\d*/(.+)/announce",
            self.announce_url,
        )
        if match is None:
            raise LoginError(
                "Failed to extract passkey from PassThePopcorn announce URL."
            )
        return match.group(1)

    def _login_data(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "password": self.password,
            "passkey": self._passkey(),
            "keeplogged": "1",
        }

    @staticmethod
    def _redacted_login_response(response: httpx.Response) -> str:
        try:
            parsed = json.loads(response.text)
            redacted = Redaction.redact_private_info(parsed)
            return json.dumps(redacted)
        except json.JSONDecodeError:
            return str(Redaction.redact_private_info(response.text))

    def _login_response_error(self, response: httpx.Response) -> LoginError:
        redacted = self._redacted_login_response(response)
        return LoginError(
            "Got exception while loading JSON login response from "
            f"PassThePopcorn. Response: {redacted}"
        )

    @staticmethod
    def _login_payload(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("PassThePopcorn login response must be an object")
        return cast(dict[str, Any], payload)

    async def _post_login(
        self,
        client: httpx.AsyncClient,
        data: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        response = await client.post(
            f"{self.base_url}/ajax.php?action=login",
            data=data,
            headers=headers,
        )
        await asyncio.sleep(2)
        return response

    async def _complete_tfa(
        self,
        meta: Meta,
        client: httpx.AsyncClient,
        data: dict[str, Any],
        headers: dict[str, str],
        response: httpx.Response,
        payload: dict[str, Any],
    ) -> tuple[httpx.Response, dict[str, Any]]:
        if payload.get("Result") != "TfaRequired":
            return response, payload
        if meta.unattended and not meta.unattended_confirm:
            raise LoginError(
                f"{self.tracker}: 2FA is required in unattended mode."
            )
        data["TfaType"] = "normal"
        data["TfaCode"] = await prompt_in_thread(
            cli_ui.ask_string,
            "2FA Required: Please enter PassThePopcorn 2FA code",
        )
        response = await self._post_login(client, data, headers)
        return response, self._login_payload(response)

    def _successful_login_token(
        self,
        client: httpx.AsyncClient,
        cookie_file: Path,
        payload: dict[str, Any],
    ) -> str:
        if payload.get("Result") != "Ok":
            raise LoginError(
                "Failed to login to PassThePopcorn. Probably due to the bad "
                "user name, password, announce url, or 2FA code."
            )
        token = payload["AntiCsrfToken"]
        self.cookie_validator._save_cookies_secure(
            client.cookies.jar, str(cookie_file)
        )  # pyright: ignore[reportPrivateUsage]
        return cast(str, token)

    async def _fresh_session_token(
        self,
        meta: Meta,
        cookie_file: Path,
        cookies: dict[str, str],
    ) -> str:
        data = self._login_data()
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(
            cookies=cookies, timeout=30.0, follow_redirects=True
        ) as client:
            response = await self._post_login(client, data, headers)
            try:
                payload = self._login_payload(response)
                response, payload = await self._complete_tfa(
                    meta, client, data, headers, response, payload
                )
                return self._successful_login_token(
                    client, cookie_file, payload
                )
            except Exception as error:
                raise self._login_response_error(response) from error

    async def get_anti_csrf_token(self, meta: Meta) -> str:
        cookie_file = self._cookie_file(meta)
        if cookie_file.exists():
            cookies = self._saved_cookie_values(cookie_file)
            token = await self._saved_session_token(cookies)
            if token is not None:
                return token
            self._clear_expired_cookie(cookie_file)
        else:
            cookies = {}
            logger.info(
                f"{self.tracker}: [yellow]Cookies not found. Creating new session."
            )
        return await self._fresh_session_token(meta, cookie_file, cookies)

    async def validate_login(self, response: httpx.Response) -> bool:
        logged_in = False
        if response.text.find("""<a href="login.php?act=recover">""") != -1:
            logger.info(
                f"{self.tracker}: Looks like you are not logged in to PassThePopcorn. Probably due to the bad user name, password, or expired session."
            )
        elif (
            "Your popcorn quota has been reached, come back later!"
            in response.text
        ):
            raise LoginError(
                "Your PassThePopcorn request/popcorn quota has been reached, try again later"
            )
        else:
            logged_in = True
        return logged_in

    @staticmethod
    def _upload_description_path(meta: Meta, tracker: str) -> Path:
        return (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{tracker}]DESCRIPTION.txt"
        )

    async def _upload_description(self, meta: Meta) -> str:
        path = self._upload_description_path(meta, self.tracker)
        try:
            path.stat()
            async with aiofiles.open(path, encoding="utf-8") as file_handle:
                return await file_handle.read()
        except OSError as error:
            logger.info(
                f"{self.tracker}: File error: {error}",
                extra={"markup": False},
            )
            return ""

    @staticmethod
    def _first_audio_language(tracks: list[dict[str, Any]]) -> str:
        if not tracks:
            return ""
        return str(tracks[0].get("Language", "")).lower()

    @classmethod
    def _audio_flags_from_tracks(
        cls, tracks: list[dict[str, Any]], no_tracks_is_missing: bool
    ) -> tuple[bool, bool]:
        if not tracks:
            return no_tracks_is_missing, False
        language = cls._first_audio_language(tracks)
        if not language:
            return True, False
        return False, language.startswith("en")

    def _audio_flags(self, meta: Meta) -> tuple[bool, bool]:
        if meta.is_disc == "BDMV":
            tracks = cast(list[dict[str, Any]], meta.bdinfo.get("audio", []))
            return self._audio_flags_from_tracks(tracks, False)
        tracks = [
            track
            for track in cast(
                list[dict[str, Any]],
                meta.mediainfo.get("media", {}).get("track", []),
            )
            if track.get("@type") == "Audio"
        ]
        logger.debug(
            f"{self.tracker}: [Debug] Found {len(tracks)} audio tracks"
        )
        if not tracks:
            logger.info(
                f"{self.tracker}: [yellow]No audio tracks found in mediainfo"
            )
        else:
            logger.debug(
                f"{self.tracker}: [Debug] First audio track language: "
                f"{self._first_audio_language(tracks)}"
            )
        return self._audio_flags_from_tracks(tracks, True)

    @staticmethod
    def _has_english_subtitle(subtitles: list[int]) -> bool:
        return any(value in {3, 50} for value in subtitles)

    @staticmethod
    def _replace_trumpable_value(
        trumpable: list[int], old: int, new: int
    ) -> None:
        if old in trumpable:
            trumpable.remove(old)
            trumpable.append(new)

    def _normalize_forced_trumpable(
        self, trumpable: list[int], subtitles: list[int]
    ) -> None:
        self._replace_trumpable_value(trumpable, 50, 4)
        if 14 in trumpable and 44 in subtitles:
            subtitles.remove(44)

    def _normalize_other_hardcoded_trumpable(
        self,
        trumpable: list[int],
        subtitles: list[int],
        english_audio: bool,
    ) -> None:
        if 15 not in trumpable:
            return
        self._replace_trumpable_value(trumpable, 15, 4)
        if 44 in subtitles:
            subtitles.remove(44)
        if not english_audio and not self._has_english_subtitle(subtitles):
            trumpable.append(14)

    def _normalize_hardcoded_trumpable(
        self,
        trumpable: list[int] | None,
        subtitles: list[int],
        english_audio: bool,
    ) -> tuple[list[int] | None, list[int]]:
        if not trumpable:
            return trumpable, subtitles
        self._normalize_forced_trumpable(trumpable, subtitles)
        self._normalize_other_hardcoded_trumpable(
            trumpable, subtitles, english_audio
        )
        return trumpable, subtitles

    @staticmethod
    def _can_prompt_trumpable(meta: Meta) -> bool:
        return bool(not meta.unattended or meta.unattended_confirm)

    async def _prompt_trumpable(
        self,
        meta: Meta,
        message: str,
        subtitles: list[int],
    ) -> tuple[list[int] | None, list[int]]:
        cli_ui.info(message)
        if not self._can_prompt_trumpable(meta):
            return None, subtitles
        confirmed = await prompt_in_thread(
            cli_ui.ask_yes_no, "Mark trumpable?", default=True
        )
        return (
            self.get_trumpable(subtitles) if confirmed else (None, subtitles)
        )

    def _hardcoded_subtitle_fields(
        self, subtitles: list[int], english_audio: bool
    ) -> tuple[list[int] | None, list[int]]:
        trumpable, subtitles = self.get_trumpable(subtitles)
        return self._normalize_hardcoded_trumpable(
            trumpable, subtitles, english_audio
        )

    async def _missing_english_subtitle_fields(
        self,
        meta: Meta,
        subtitles: list[int],
        no_audio: bool,
        english_audio: bool,
    ) -> tuple[list[int] | None, list[int]]:
        if self._has_english_subtitle(subtitles):
            return None, subtitles
        if no_audio:
            return await self._prompt_trumpable(
                meta,
                "No English subs and no audio tracks found should this be "
                "trumpable?",
                subtitles,
            )
        if not english_audio:
            return await self._prompt_trumpable(
                meta,
                "No English subs and English audio is not the first audio "
                "track, should this be trumpable?",
                subtitles,
            )
        return None, subtitles

    async def _upload_subtitle_fields(
        self, meta: Meta
    ) -> tuple[list[int] | None, list[int]]:
        subtitles = self.get_subtitles(meta)
        no_audio, english_audio = self._audio_flags(meta)
        if meta.hardcoded_subs:
            return self._hardcoded_subtitle_fields(subtitles, english_audio)
        return await self._missing_english_subtitle_fields(
            meta, subtitles, no_audio, english_audio
        )

    @staticmethod
    def _imdb_form_value(meta: Meta) -> str:
        value = meta.imdb_id
        normalized = value if isinstance(value, (int, str)) else 0
        return "0" if normalized == 0 else str(normalized).zfill(7)

    async def _base_form_fields(
        self,
        meta: Meta,
        resolution: str,
        description: str,
        trumpable: list[int] | None,
        subtitles: list[int],
    ) -> dict[str, Any]:
        return {
            "submit": "true",
            "remaster_year": "",
            "remaster_title": self.get_remaster_title(meta),
            "type": self.get_type(meta.imdb_info, meta),
            "codec": "Other",
            "other_codec": self.get_codec(meta),
            "container": "Other",
            "other_container": self.get_container(meta),
            "resolution": resolution,
            "source": "Other",
            "other_source": self.get_source(meta.source or ""),
            "release_desc": description,
            "nfo_text": "",
            "subtitles[]": subtitles,
            "trumpable[]": trumpable,
            "AntiCsrfToken": await self.get_anti_csrf_token(meta),
            "imdb": self._imdb_form_value(meta),
        }

    @staticmethod
    def _has_remaster_fields(data: dict[str, Any]) -> bool:
        return bool(
            data["remaster_year"] != "" or data["remaster_title"] != ""
        )

    @classmethod
    def _apply_common_form_flags(
        cls,
        data: dict[str, Any],
        meta: Meta,
        resolution: str,
        other_resolution: str | None,
    ) -> None:
        if cls._has_remaster_fields(data):
            data["remaster"] = "on"
        if meta.scene is True:
            data["scene"] = "on"
        if resolution == "Other":
            data["other_resolution"] = other_resolution
        if meta.personalrelease is True:
            data["internalrip"] = "on"

    async def _base_upload_form_data(
        self,
        meta: Meta,
        resolution: str,
        other_resolution: str | None,
        description: str,
        trumpable: list[int] | None,
        subtitles: list[int],
    ) -> dict[str, Any]:
        data = await self._base_form_fields(
            meta, resolution, description, trumpable, subtitles
        )
        self._apply_common_form_flags(data, meta, resolution, other_resolution)
        return data

    async def _new_group_torrent_info(
        self, meta: Meta, imdb_value: str
    ) -> dict[str, Any]:
        if imdb_value == "0":
            return await self.get_torrent_info_tmdb(meta)
        return await self.get_torrent_info(meta.imdb or "0", meta)

    async def _ensure_youtube(self, meta: Meta) -> None:
        if meta.youtube is not None and "youtube" in meta.youtube:
            return
        youtube = (
            ""
            if meta.unattended
            else await prompt_in_thread(
                cli_ui.ask_string,
                "Unable to find youtube trailer, please link one "
                "e.g.(https://www.youtube.com/watch?v=dQw4w9WgXcQ)",
                default="",
            )
        )
        meta.youtube = youtube

    async def _initial_cover(self, meta: Meta) -> Any:
        cover = meta.imdb_info.get("cover")
        if cover is None:
            cover = meta.artwork_url
        if isinstance(cover, str) and cover.strip():
            return await self.rehost_poster_to_selected_host(meta, cover)
        if isinstance(cover, str):
            return None
        return cover

    def _ensure_cover_prompt_allowed(self, meta: Meta) -> None:
        if not meta.unattended or meta.unattended_confirm:
            return
        meta.skipping = self.tracker
        raise UploadError(
            f"{self.tracker}: Cover is required in unattended mode."
        )

    @staticmethod
    def _valid_cover_url(cover: str) -> bool:
        return cover.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))

    async def _prompt_cover_candidate(self, meta: Meta) -> str | None:
        cover = (
            await prompt_in_thread(
                cli_ui.ask_string,
                "No Cover was found. Please input a link to a cover: \n",
                default="",
            )
            or ""
        )
        if not cover:
            return None
        if not self._valid_cover_url(cover):
            logger.info(
                f"{self.tracker}: [red]Cover URL must end with .jpg, "
                ".jpeg, .png, or .webp"
            )
            return None
        return await self.rehost_poster_to_selected_host(meta, cover)

    async def _prompt_cover(self, meta: Meta) -> str:
        self._ensure_cover_prompt_allowed(meta)
        while True:
            cover = await self._prompt_cover_candidate(meta)
            if cover is not None:
                return cover

    async def _required_cover(self, meta: Meta) -> Any:
        cover = await self._initial_cover(meta)
        return cover if cover is not None else await self._prompt_cover(meta)

    @staticmethod
    def _new_group_year(meta: Meta, info: dict[str, Any]) -> Any:
        year = info.get("year", meta.imdb_info.get("year", meta.year))
        if year in {"", "0", 0, None} and meta.manual_year not in {
            0,
            "",
            None,
        }:
            return meta.manual_year
        return year

    def _ensure_tag_prompt_allowed(self, meta: Meta) -> None:
        mode = meta.mode if meta.mode is not None else "non_cli"
        if mode != "cli":
            raise UploadError(
                "PassThePopcorn requires at least one valid tag."
            )
        if not meta.unattended or meta.unattended_confirm:
            return
        logger.info(
            f"{self.tracker}: [yellow]Unattended mode: Unable to match "
            f"any tags. Skipping {self.tracker} upload.[/yellow]"
        )
        meta.skipping = self.tracker
        raise UploadError(
            f"{self.tracker}: Unable to match any tags in unattended mode."
        )

    async def _prompt_required_tags(self) -> Any:
        value: Any = None
        while not value:
            logger.info(f"{self.tracker}: [yellow]Unable to match any tags")
            logger.info(
                f"{self.tracker}: Valid tags can be found on the "
                "PassThePopcorn upload form"
            )
            value = await prompt_in_thread(
                cli_ui.ask_string,
                "Please enter at least one tag. Comma separated "
                "(action, animation, short):",
            )
        return value

    async def _required_tags(self, meta: Meta, tags: Any) -> Any:
        if tags:
            return tags
        self._ensure_tag_prompt_allowed(meta)
        return await self._prompt_required_tags()

    @staticmethod
    def _director_names(meta: Meta) -> tuple[str, ...]:
        value = meta.imdb_info.get("directors")
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            director
            for director in cast(list[Any], value)
            if isinstance(director, str)
        )

    async def _new_group_fields(
        self, meta: Meta, data: dict[str, Any]
    ) -> dict[str, Any]:
        info = await self._new_group_torrent_info(meta, str(data["imdb"]))
        await self._ensure_youtube(meta)
        cover = await self._required_cover(meta)
        fields: dict[str, Any] = {
            "title": info.get(
                "title", meta.imdb_info.get("title", meta.title)
            ),
            "year": self._new_group_year(meta, info),
            "image": cover,
            "tags": await self._required_tags(meta, info.get("tags", "")),
            "album_desc": info.get("plot", meta.overview),
            "trailer": meta.youtube,
        }
        directors = self._director_names(meta)
        if directors:
            fields["artist[]"] = directors
            fields["importance[]"] = "1"
        return fields

    async def _upload_form_target(
        self,
        group_id: int | str | None,
        meta: Meta,
        data: dict[str, Any],
    ) -> str:
        if group_id is not None:
            data["groupid"] = group_id
            return f"{self.base_url}/upload.php?groupid={group_id}"
        data.update(await self._new_group_fields(meta, data))
        return f"{self.base_url}/upload.php"

    async def fill_upload_form(
        self, group_id: int | str | None, meta: Meta
    ) -> tuple[str, dict[str, Any]]:
        resolution, other_resolution = self.get_resolution(meta)
        await self.edit_desc(meta)
        description = await self._upload_description(meta)
        trumpable, subtitles = await self._upload_subtitle_fields(meta)
        logger.debug(f"{self.tracker}: ptp_trumpable: {trumpable}")
        logger.debug(f"{self.tracker}: ptp_subtitles: {subtitles}")
        data = await self._base_upload_form_data(
            meta,
            resolution,
            other_resolution,
            description,
            trumpable,
            subtitles,
        )
        url = await self._upload_form_target(group_id, meta, data)
        return url, data

    @staticmethod
    def _torrent_upload_path(meta: Meta, tracker: str) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / f"[{tracker}].torrent"

    @staticmethod
    def _needs_rehash(meta: Meta) -> bool:
        return bool((meta.base_torrent_piece_mb or 0) > 16 and not meta.nohash)

    def _rehash_cooldown(self) -> int:
        try:
            return int(
                self.config.get("DEFAULT", {}).get("rehash_cooldown", 0) or 0
            )
        except TypeError, ValueError:
            return 0

    async def _rehash_for_ptp(self, common: Common, meta: Meta) -> None:
        logger.info(
            f"{self.tracker}: [red]Piece size is OVER 16M and does not work "
            "on PassThePopcorn. Generating a new .torrent"
        )
        tracker_url = (
            self.announce_url.strip()
            if self.announce_url
            else "https://fake.tracker"
        )
        cooldown = self._rehash_cooldown()
        if cooldown > 0:
            await asyncio.sleep(cooldown)
        torrent_name = f"[{self.tracker}]"
        await TorrentCreator.create_torrent(
            meta,
            str(meta.path),
            torrent_name,
            tracker_url=tracker_url,
            piece_size=16,
        )
        await common.create_torrent_for_upload(
            meta,
            self.tracker,
            self.source_flag,
            torrent_filename=torrent_name,
        )

    async def _prepare_ptp_torrent(self, common: Common, meta: Meta) -> None:
        if self._needs_rehash(meta):
            await self._rehash_for_ptp(common, meta)
            return
        await common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )

    async def _torrent_upload_files(self, meta: Meta) -> dict[str, Any]:
        path = self._torrent_upload_path(meta, self.tracker)
        async with aiofiles.open(path, "rb") as file_handle:
            torrent_bytes = await file_handle.read()
        return {
            "file_input": (
                "placeholder.torrent",
                torrent_bytes,
                "application/x-bittorent",
            )
        }

    @staticmethod
    def _upload_headers(user_agent: str) -> dict[str, str]:
        return {"User-Agent": user_agent}

    async def _debug_upload(
        self,
        common: Common,
        meta: Meta,
        url: str,
        data: dict[str, Any],
    ) -> bool:
        debug_data = data.copy()
        if "AntiCsrfToken" in debug_data:
            debug_data["AntiCsrfToken"] = "[REDACTED]"
        logger.debug(url)
        logger.debug(Redaction.redact_private_info(debug_data))
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

    @staticmethod
    def _upload_failure_path(meta: Meta, tracker: str) -> Path:
        return (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{tracker}]PTP_upload_failure.html"
        )

    def _upload_cookie_values(self, meta: Meta) -> dict[str, str]:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookie_file = find_cookie_file(
            meta.base_dir, self.tracker, self.config
        )
        raw = self.cookie_validator._load_cookies_dict_secure(cookie_file)  # pyright: ignore[reportPrivateUsage]
        return {
            name: str(cookie.get("value", "")) for name, cookie in raw.items()
        }

    async def _post_torrent_upload(
        self,
        meta: Meta,
        url: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            cookies=self._upload_cookie_values(meta),
            timeout=60.0,
            follow_redirects=True,
        ) as client:
            return await client.post(
                url=url,
                data=data,
                headers=self._upload_headers(self.user_agent),
                files=files,
            )

    @staticmethod
    def _upload_error_message(response_text: str) -> str:
        match = re.search(
            r'<div class="alert alert--error.*?>(.+?)</div>',
            response_text,
        )
        return match.group(1) if match is not None else ""

    @staticmethod
    async def _write_failure_response(path: Path, response_text: str) -> None:
        async with aiofiles.open(path, "w", encoding="utf-8") as file_handle:
            await file_handle.write(response_text)

    def _upload_success_match(self, response_url: str) -> re.Match[str] | None:
        expected_host = urlparse(self.base_url).netloc
        return re.match(
            rf".*?{re.escape(expected_host)}/torrents\.php\?id=(\d+)&torrentid=(\d+)",
            response_url,
        )

    async def _record_upload_page_error(
        self,
        meta: Meta,
        response_text: str,
        failure_path: Path,
    ) -> None:
        if self.announce_url not in response_text:
            return
        error_message = self._upload_error_message(response_text)
        await self._write_failure_response(failure_path, response_text)
        meta.tracker_status[self.tracker]["status_message"] = (
            f"data error: see {failure_path} | {error_message}"
        )

    async def _complete_upload_response(
        self,
        common: Common,
        meta: Meta,
        response: httpx.Response,
    ) -> bool:
        logger.info(f"{self.tracker}: [cyan]{response.url}")
        response_text = response.text
        failure_path = self._upload_failure_path(meta, self.tracker)
        await self._record_upload_page_error(meta, response_text, failure_path)
        if self._upload_success_match(str(response.url)) is None:
            await self._write_failure_response(failure_path, response_text)
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error: see {failure_path}"
            )
            return False
        meta.tracker_status[self.tracker]["status_message"] = str(response.url)
        await common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            self.announce_url,
            str(response.url),
        )
        return True

    async def upload(self, meta: Meta, url: str, data: dict[str, Any]) -> bool:
        common = Common(config=self.config)
        await self._prepare_ptp_torrent(common, meta)
        files = await self._torrent_upload_files(meta)
        if meta.debug:
            return await self._debug_upload(common, meta, url, data)
        response = await self._post_torrent_upload(meta, url, data, files)
        return await self._complete_upload_response(common, meta, response)
