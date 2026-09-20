# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import platform
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import aiofiles
import bbcode
import cli_ui
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import screenshots_dir
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class AZTrackerBase:
    auth_type = "cookies"
    supported_categories: tuple[str, ...] = ("TV", "MOVIE")
    tracker: str = ""
    source_flag: ClassVar[str] = ""
    secret_token: ClassVar[str] = ""
    banned_groups: tuple[str, ...] = ()

    def __init__(self, config: Config, tracker_name: str):
        self.config = config
        self.tracker = tracker_name
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.az_class = type(self)
        tracker_config = self._tracker_config()
        self.base_url = self._configured_value(tracker_config, "base_url")
        self.requests_url = self._configured_value(
            tracker_config, "requests_url"
        )
        self.announce_url = self._configured_value(
            tracker_config, "announce_url"
        )
        self._source_flag = str(
            tracker_config.get("source_flag") or type(self).source_flag
        )
        self.torrent_url = f"{self.base_url}/torrent/" if self.base_url else ""
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": f"Upload-Assistant/2.3 ({platform.system()} {platform.release()})"
            },
            timeout=60.0,
        )
        self.media_code = ""
        self.upload_url_step2 = ""

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        tracker_map = cast(dict[str, Any], trackers)
        value = tracker_map.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _configured_value(
        self, tracker_config: dict[str, Any], key: str
    ) -> str:
        value = tracker_config.get(key) or getattr(type(self), key, "")
        return str(value or "")

    def rules(self, meta: Meta) -> str:
        meta = meta
        return ""

    def get_video_quality(self, meta: Meta) -> str:
        resolution: str = meta.resolution

        if self.tracker != "PRIVATEHD":
            resolution_int = int(
                resolution.lower().replace("p", "").replace("i", "")
            )
            if resolution_int < 720 or meta.sd:
                return "1"

        keyword_map = {
            "1080i": "7",
            "1080p": "3",
            "2160p": "6",
            "4320p": "8",
            "720p": "2",
        }

        return keyword_map.get(resolution.lower(), "0")

    async def get_media_code(self, meta: Meta) -> bool:
        self.media_code = ""
        category = self._media_category(meta.category)
        if category is None:
            return False
        identifiers = self._media_identifiers(meta)
        headers = self._media_lookup_headers(meta)
        first_match = await self._media_lookup_attempt(
            meta, category, identifiers, headers, delayed=False
        )
        if first_match:
            return True
        if not await self._handle_missing_media(meta, category, identifiers):
            return False
        return await self._media_lookup_attempt(
            meta, category, identifiers, headers, delayed=True
        )

    @staticmethod
    def _media_category(category: str) -> str | None:
        return {"MOVIE": "1", "TV": "2"}.get(category)

    @staticmethod
    def _media_identifiers(meta: Meta) -> dict[str, str]:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return {
            "imdb": str(imdb.get("imdbID", "")),
            "tmdb": "" if meta.tmdb is None else str(meta.tmdb),
            "title": str(meta.title),
        }

    def _media_lookup_headers(self, meta: Meta) -> dict[str, str]:
        return {
            "Referer": f"{self.base_url}/upload/{meta.category.lower()}",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def _media_lookup_attempt(
        self,
        meta: Meta,
        category: str,
        identifiers: dict[str, str],
        headers: dict[str, str],
        *,
        delayed: bool,
    ) -> bool:
        try:
            if delayed:
                logger.info(
                    f"{self.tracker}: Trying to search again by ID after adding to media to database...\n"
                )
                await asyncio.sleep(5)
            data = await self._media_lookup_data(
                category, identifiers, headers
            )
            match = self._matching_media_item(data, identifiers)
            if match is None:
                return False
            self.media_code = str(match["id"])
            if delayed:
                logger.info(
                    f"{self.tracker}: [green]Found new ID at:[/green] {self.base_url}/{meta.category.lower()}/{self.media_code}"
                )
            return True
        except Exception as error:
            logger.info(
                f"{self.tracker}: Error while trying to fetch media code: {error}"
            )
            return False

    async def _media_lookup_data(
        self,
        category: str,
        identifiers: dict[str, str],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        imdb = identifiers["imdb"]
        if imdb:
            data = await self._media_lookup_request(category, imdb, headers)
        if not data.get("data"):
            data = await self._media_lookup_request(
                category, identifiers["title"], headers
            )
        return data

    async def _media_lookup_request(
        self, category: str, term: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        response = await self.session.get(
            f"{self.base_url}/ajax/movies/{category}?term={term}",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        return (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _matching_media_item(
        data: dict[str, Any], identifiers: dict[str, str]
    ) -> dict[str, Any] | None:
        values = data.get("data", [])
        items = cast(list[Any], values) if isinstance(values, list) else []
        for value in items:
            if isinstance(value, dict) and AZTrackerBase._media_item_matches(
                value, identifiers
            ):
                return cast(dict[str, Any], value)
        return None

    @staticmethod
    def _media_item_matches(
        item: dict[str, Any], identifiers: dict[str, str]
    ) -> bool:
        imdb = identifiers["imdb"]
        tmdb = identifiers["tmdb"]
        return bool(
            (imdb and item.get("imdb") == imdb)
            or (tmdb and str(item.get("tmdb")) == tmdb)
        )

    async def _handle_missing_media(
        self, meta: Meta, category: str, identifiers: dict[str, str]
    ) -> bool:
        logger.info(
            f"{self.tracker}: \nThe media [[yellow]IMDB:{identifiers['imdb']}[/yellow]] [[blue]TMDB:{identifiers['tmdb']}[/blue]] appears to be missing from the site's database."
        )
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: Media missing from site database. Skipping {self.tracker} upload.[/yellow]"
            )
            meta.skipping = self.tracker
            return False
        add = await prompt_in_thread(
            cli_ui.ask_yes_no,
            f"{self.tracker}: Do you want to add it to the site database?\n",
        )
        if not add:
            logger.info(
                f"{self.tracker}: User chose not to add media. Aborting."
            )
            return False
        success = await self.add_media_to_db(
            meta,
            identifiers["title"],
            category,
            identifiers["imdb"],
            identifiers["tmdb"],
        )
        if not success:
            logger.info(f"{self.tracker}: Failed to add media. Aborting.")
        return success

    async def add_media_to_db(
        self, meta: Meta, title: str, category: str, imdb_id: str, tmdb_id: str
    ) -> bool:
        data = self._add_media_payload(meta, title, category, imdb_id, tmdb_id)
        url = f"{self.base_url}/add/{meta.category.lower()}"
        try:
            logger.info(f"{self.tracker}: Trying to add to database...")
            response = await self.session.post(
                url, data=data, headers={"Referer": f"{self.base_url}/upload"}
            )
            return await self._record_add_media_response(meta, response)
        except Exception as error:
            logger.info(
                f"{self.tracker}: Exception when trying to add media to the database: {error}"
            )
            return False

    def _add_media_payload(
        self, meta: Meta, title: str, category: str, imdb_id: str, tmdb_id: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "_token": self.az_class.secret_token,
            "type_id": category,
            "title": title,
            "imdb_id": imdb_id,
            "tmdb_id": tmdb_id,
        }
        if meta.category == "TV" and meta.tvdb:
            data["tvdb_id"] = str(meta.tvdb)
        return data

    async def _record_add_media_response(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        if response.status_code == 302:
            logger.info(
                f"{self.tracker}: The attempt to add the media to the database appears to have been successful.."
            )
            return True
        logger.info(
            f"{self.tracker}: Error adding media to the database. Status: {response.status_code}"
        )
        failure_path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]Failed_DB_attempt.html"
        )
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            failure_path, "w", encoding="utf-8"
        ) as handle:
            await handle.write(response.text)
        logger.info(
            f"{self.tracker}: The server response was saved to {failure_path} for analysis."
        )
        return False

    async def validate_credentials(self, meta: Meta):
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar
            return await self.cookie_validator.cookie_validation(
                meta=meta,
                tracker=self.tracker,
                test_url=f"{self.base_url}/torrents",
                error_text="Page not found",
                token_pattern=r'name="_token" content="([^"]+)"',  # noqa: S106  # nosec B106 -- regex pattern, not a credential
            )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self._rules_policy(meta):
            return False
        if not await self._privatehd_group_policy(meta):
            return False
        await self._apply_session_cookies(meta)
        if await self.get_media_code(meta):
            return True
        logger.info(
            f"{self.tracker}: This media is not registered, please add it to the database by following this link: {self.base_url}/add/{meta.category.lower()}"
        )
        return False

    async def _rules_policy(self, meta: Meta) -> bool:
        if not self._tracker_config().get("check_for_rules", True):
            return True
        warnings = self.rules(meta)
        if not warnings:
            return True
        logger.info(
            f"{self.tracker}: [red]Rule check returned the following warning(s):[/red]\n\n{warnings}"
        )
        return await self._confirm_policy_override(meta)

    async def _privatehd_group_policy(self, meta: Meta) -> bool:
        if not self._restricted_privatehd_group(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Group {meta.tag} is only allowed for web-dl[/bold red]"
        )
        return await self._confirm_policy_override(meta)

    def _restricted_privatehd_group(self, meta: Meta) -> bool:
        return (
            self.tracker == "PRIVATEHD"
            and meta.type != "WEBDL"
            and meta.tag in {"FGT", "EVO"}
        )

    @staticmethod
    async def _confirm_policy_override(meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        return bool(
            await prompt_in_thread(
                cli_ui.ask_yes_no,
                "Do you want to continue anyway?",
                default=False,
            )
        )

    async def _apply_session_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        await self._apply_session_cookies(meta)
        resolution = self._search_resolution(meta)
        rip_type = self.get_rip_type(meta, display_name=True)
        if not self.media_code:
            await self.get_media_code(meta)
        page_url = f"{self.base_url}/movies/torrents/{self.media_code}?quality={resolution}"
        return await self._search_pages(meta, page_url, rip_type)

    @staticmethod
    def _search_resolution(meta: Meta) -> str:
        if meta.resolution == "2160p":
            return "UHD"
        if meta.resolution in {"720p", "1080p"}:
            return meta.resolution or "all"
        return "all"

    async def _search_pages(
        self, meta: Meta, page_url: str, rip_type: str
    ) -> list[dict[str, str]]:
        duplicates: list[dict[str, str]] = []
        visited: set[str] = set()
        while page_url and page_url not in visited:
            visited.add(page_url)
            page = await self._search_page_soup(page_url)
            duplicates.extend(
                await self._page_duplicates(meta, page, rip_type)
            )
            page_url = self._next_page_url(page)
        return duplicates

    async def _search_page_soup(self, page_url: str) -> BeautifulSoup:
        response = await self.session.get(page_url)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    async def _page_duplicates(
        self, meta: Meta, soup: BeautifulSoup, rip_type: str
    ) -> list[dict[str, str]]:
        rows = self._torrent_rows(soup)
        duplicates: list[dict[str, str]] = []
        for row in rows:
            entry = await self._row_duplicate(meta, row, rip_type)
            if entry is not None:
                duplicates.append(entry)
        return duplicates

    @staticmethod
    def _torrent_rows(soup: BeautifulSoup) -> list[Any]:
        table = soup.find("table", class_="table-bordered")
        if table is None:
            return []
        tbody = table.find("tbody")
        return (
            []
            if tbody is None
            else list(tbody.find_all("tr", recursive=False))
        )

    async def _row_duplicate(
        self, meta: Meta, row: Any, rip_type: str
    ) -> dict[str, str] | None:
        if not self._row_matches_rip_type(row, rip_type):
            return None
        entry = self._base_row_duplicate(row)
        await self._append_duplicate_bdinfo(meta, entry)
        return entry

    @staticmethod
    def _row_matches_rip_type(row: Any, rip_type: str) -> bool:
        if not rip_type:
            return True
        badges = [
            badge.get_text(strip=True)
            for badge in row.find_all("span", class_="badge-extra")
        ]
        return rip_type in badges

    async def _append_duplicate_bdinfo(
        self, meta: Meta, entry: dict[str, str]
    ) -> None:
        if meta.is_disc != "BDMV":
            return
        value = await self.get_dupe_bdinfo(entry["link"])
        if value:
            entry["bd_info"] = value

    def _base_row_duplicate(self, row: Any) -> dict[str, str]:
        name_tag = row.find("a", class_="torrent-filename")
        name = name_tag.get_text(strip=True) if name_tag else ""
        href = name_tag.get("href") if name_tag else None
        link = self._torrent_details_link(href)
        return {"name": name, "size": self._row_size(row), "link": link}

    def _torrent_details_link(self, href: Any) -> str:
        value = href if isinstance(href, str) else ""
        match = re.search(r"/(\d+)", value)
        return f"{self.torrent_url}{match.group(1)}" if match else value

    @staticmethod
    def _row_size(row: Any) -> str:
        cells = row.find_all("td")
        if len(cells) <= 4:
            return ""
        span = cells[4].find("span")
        return (
            span.get_text(strip=True)
            if span
            else cells[4].get_text(strip=True)
        )

    @staticmethod
    def _next_page_url(soup: BeautifulSoup) -> str:
        tag = soup.select_one("a[rel='next']")
        href = tag.get("href") if tag is not None else None
        return href if isinstance(href, str) else ""

    async def get_dupe_bdinfo(self, torrent_link: str) -> str:
        try:
            response = await self.session.get(
                torrent_link, follow_redirects=True
            )
            response.raise_for_status()
            value = self._bdinfo_from_html(response.text)
            if value:
                return value
            logger.info(
                f"{self.tracker}: [yellow]MediaInfo/BDInfo block not found at {torrent_link}[/yellow]"
            )
            return ""
        except Exception as error:
            self._log_dupe_bdinfo_error(torrent_link, error)
            if not isinstance(
                error, (httpx.HTTPError, AttributeError, TypeError, ValueError)
            ):
                raise
            return ""

    def _log_dupe_bdinfo_error(
        self, torrent_link: str, error: Exception
    ) -> None:
        if isinstance(error, httpx.HTTPStatusError):
            logger.info(
                f"{self.tracker}: [red]HTTP error {error.response.status_code} from {torrent_link}[/red]"
            )
            return
        if isinstance(error, httpx.RequestError):
            logger.info(
                f"{self.tracker}: [red]Request failed to {torrent_link}. {error}[/red]"
            )
            return
        if isinstance(error, (AttributeError, TypeError, ValueError)):
            logger.info(
                f"{self.tracker}: [red]Parsing failed for {torrent_link}. {error}[/red]"
            )
            return
        logger.error(
            f"{self.tracker}: [red]Unexpected error parsing {torrent_link}. {error}[/red]",
            exc_info=True,
        )

    @staticmethod
    def _bdinfo_from_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        container = soup.find("div", id="collapseMediaInfo")
        if container is None:
            return ""
        pre = container.find("pre")
        return pre.get_text("\n", strip=True) if pre is not None else ""

    def get_cat_id(self, category_name: str) -> str:
        return {
            "MOVIE": "1",
            "TV": "2",
        }.get(category_name, "0")

    async def get_file_info(self, meta: Meta) -> str:
        info_file_path = ""
        file_info = ""
        if meta.is_disc == "BDMV":
            summary_file = (
                "BD_SUMMARY_EXT_00"
                if self.tracker == "CINEMAZ"
                else "BD_SUMMARY_00"
            )
            info_file_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/{summary_file}.txt"
        else:
            info_file_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/MEDIAINFO_CLEANPATH.txt"

        if Path(info_file_path).exists():
            async with aiofiles.open(info_file_path, encoding="utf-8") as f:
                file_info = await f.read()

        return file_info

    async def get_lang(self, meta: Meta) -> dict[str, list[str]]:
        self.language_map(meta)
        if meta.is_disc:
            audio_ids, subtitle_ids = await self._disc_language_ids(meta)
        else:
            audio_ids, subtitle_ids = await self._file_language_ids(meta)
        return {
            "subtitles[]": sorted(subtitle_ids),
            "languages[]": sorted(audio_ids),
        }

    async def _disc_language_ids(
        self, meta: Meta
    ) -> tuple[set[str], set[str]]:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        audio = self._mapped_language_ids(meta.audio_languages)
        subtitles = self._mapped_language_ids(meta.subtitle_languages)
        return audio, subtitles

    def _mapped_language_ids(self, value: Any) -> set[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return {
            target
            for item in values
            if isinstance(item, str)
            if (target := self.lang_map.get(item.lower()))
        }

    async def _file_language_ids(
        self, meta: Meta
    ) -> tuple[set[str], set[str]]:
        try:
            tracks = await self._media_info_tracks(meta)
            return await self._language_ids_from_tracks(meta, tracks)
        except FileNotFoundError:
            logger.warning(
                f"{self.tracker}: Warning: MediaInfo.json not found for uuid {meta.uuid}. No languages will be processed.",
                extra={"markup": False},
            )
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            logger.info(
                f"{self.tracker}: Error processing MediaInfo.json for uuid {meta.uuid}: {error}",
                extra={"markup": False},
            )
        return set(), set()

    async def _media_info_tracks(self, meta: Meta) -> list[dict[str, Any]]:
        path = Path(meta.base_dir) / "tmp" / meta.uuid / "MediaInfo.json"
        async with aiofiles.open(path, encoding="utf-8") as handle:
            payload = json.loads(await handle.read())
        return self._tracks_from_payload(payload)

    @classmethod
    def _tracks_from_payload(cls, payload: Any) -> list[dict[str, Any]]:
        media = cls._mapping_value(payload, "media")
        return cls._mapping_list(media.get("track", []))

    @staticmethod
    def _mapping_value(value: Any, key: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        mapping = cast(dict[str, Any], value)
        nested = mapping.get(key, {})
        return cast(dict[str, Any], nested) if isinstance(nested, dict) else {}

    @staticmethod
    def _mapping_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        values = cast(list[Any], value)
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    async def _language_ids_from_tracks(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> tuple[set[str], set[str]]:
        audio_ids: set[str] = set()
        subtitle_ids: set[str] = set()
        missing_audio: list[dict[str, Any]] = []
        for track in tracks:
            self._classify_track_language(
                track, audio_ids, subtitle_ids, missing_audio
            )
        if missing_audio:
            prompted = await self._prompt_missing_audio_languages(meta)
            audio_ids.update(prompted)
        return audio_ids, subtitle_ids

    def _classify_track_language(
        self,
        track: dict[str, Any],
        audio_ids: set[str],
        subtitle_ids: set[str],
        missing_audio: list[dict[str, Any]],
    ) -> None:
        track_type = str(track.get("@type", ""))
        target = self._track_language_target(track)
        if not target:
            self._append_missing_audio(track_type, track, missing_audio)
            return
        self._append_track_language_id(
            track_type, target, audio_ids, subtitle_ids
        )

    def _track_language_target(self, track: dict[str, Any]) -> str:
        language = track.get("Language")
        return self._language_target_id(str(language)) if language else ""

    @staticmethod
    def _append_missing_audio(
        track_type: str,
        track: dict[str, Any],
        missing_audio: list[dict[str, Any]],
    ) -> None:
        if track_type == "Audio":
            missing_audio.append(track)

    @staticmethod
    def _append_track_language_id(
        track_type: str,
        target: str,
        audio_ids: set[str],
        subtitle_ids: set[str],
    ) -> None:
        if track_type == "Audio":
            audio_ids.add(target)
        elif track_type == "Text":
            subtitle_ids.add(target)

    def _language_target_id(self, language: str) -> str:
        target = self.lang_map.get(language.lower())
        if target or "-" not in language:
            return target or ""
        return self.lang_map.get(language.split("-", 1)[0].lower(), "")

    async def _prompt_missing_audio_languages(self, meta: Meta) -> set[str]:
        if self._skip_missing_audio_prompt(meta):
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: Missing audio languages. Skipping {self.tracker} upload.[/yellow]"
            )
            meta.skipping = self.tracker
            return set()
        raw = await self._ask_missing_audio_languages()
        return self._prompted_language_ids(raw)

    @staticmethod
    def _skip_missing_audio_prompt(meta: Meta) -> bool:
        return bool(meta.unattended and not meta.unattended_confirm)

    async def _ask_missing_audio_languages(self) -> str:
        logger.info(f"{self.tracker}: No audio language/s found.")
        logger.info(
            f"{self.tracker}: You must enter (comma-separated) languages for all audio tracks, eg: English, Spanish: "
        )
        raw = await prompt_in_thread(
            cli_ui.ask_string, "[bold yellow]Enter languages: [/bold yellow]"
        )
        return str(raw or "")

    def _prompted_language_ids(self, raw: str) -> set[str]:
        values = [item.strip() for item in raw.split(",")]
        return {
            target
            for value in values
            if (target := self.lang_map.get(value.lower()))
        }

    async def img_host(
        self, _meta: Meta, referer: str, image_bytes: bytes, filename: str
    ) -> str | None:
        upload_url = f"{self.base_url}/ajax/image/upload"

        headers = {
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
            "Origin": self.base_url,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0",
        }

        data: dict[str, Any] = {
            "_token": self.az_class.secret_token,
            "qquuid": str(uuid.uuid4()),
            "qqfilename": filename,
            "qqtotalfilesize": str(len(image_bytes)),
        }

        files = {"qqfile": (filename, image_bytes, "image/png")}

        try:
            response = await self.session.post(
                upload_url, headers=headers, data=data, files=files
            )

            if response.is_success:
                json_data = response.json()
                if json_data.get("success"):
                    image_id = json_data.get("imageId")
                    return str(image_id) if image_id is not None else None
                error_message = json_data.get(
                    "error", "Unknown image host error."
                )
                logger.info(
                    f"{self.tracker}: Error uploading {filename}: {error_message}",
                    extra={"markup": False},
                )
                return None
            logger.info(
                f"{self.tracker}: Error uploading {filename}: Status {response.status_code} - {response.text}",
                extra={"markup": False},
            )
            return None
        except Exception as e:
            logger.info(
                f"{self.tracker}: Exception when uploading {filename}: {e}",
                extra={"markup": False},
            )
            return None

    async def get_screenshots(self, meta: Meta) -> list[str] | None:
        sources = await self._screenshot_sources(meta)
        results: list[str] = []
        results = await self._append_remote_images(
            meta, results, sources["menu"], sources["limit"]
        )
        reserved = len(sources["audio"]) + len(sources["hdr"])
        local_limit = max(0, sources["limit"] - reserved)
        results = await self._append_local_images(
            meta, results, sources["local"], local_limit
        )
        results = await self._append_remote_images(
            meta, results, sources["images"], local_limit
        )
        results = await self._append_remote_images(
            meta, results, sources["audio"], sources["limit"]
        )
        return await self._append_remote_images(
            meta, results, sources["hdr"], sources["limit"]
        )

    async def _screenshot_sources(self, meta: Meta) -> dict[str, Any]:
        root = screenshots_dir(meta.base_dir, meta.uuid)
        local = sorted(path for path in root.glob("*.png") if path.is_file())
        limit = 3 if meta.category == "TV" and meta.tv_pack == 0 else 15
        return {
            "local": local,
            "limit": limit,
            "menu": self._raw_urls(meta.menu_images)[:12],
            "images": self._raw_urls(meta.image_list),
            "audio": self._audio_plot_urls(meta),
            "hdr": self._dynamic_hdr_urls(meta),
        }

    @staticmethod
    def _raw_urls(value: Any) -> list[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        urls: list[str] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            mapping = cast(dict[str, Any], item)
            raw_url = mapping.get("raw_url")
            if raw_url:
                urls.append(str(raw_url))
        return urls

    def _audio_plot_urls(self, meta: Meta) -> list[str]:
        if not self._tracker_config().get("add_audio_spectrogram", False):
            return []
        return self._raw_urls(meta.spectrograms_images)

    def _dynamic_hdr_urls(self, meta: Meta) -> list[str]:
        default = self.config.get("DEFAULT", {})
        enabled = bool(meta.dynamic_hdr_plot)
        if isinstance(default, dict):
            default_map = cast(dict[str, Any], default)
            enabled = enabled or bool(
                default_map.get("add_dynamic_hdr_plot", False)
            )
        enabled = enabled or bool(
            self._tracker_config().get("add_dynamic_hdr_plot", False)
        )
        return self._raw_urls(meta.dynamic_hdr_plot_images) if enabled else []

    async def _append_local_images(
        self, meta: Meta, results: list[str], paths: list[Path], limit: int
    ) -> list[str]:
        for path in paths:
            if len(results) >= limit:
                break
            image_id = await self._upload_local_screenshot(meta, path)
            if image_id:
                results.append(image_id)
        return results

    async def _append_remote_images(
        self, meta: Meta, results: list[str], urls: list[str], limit: int
    ) -> list[str]:
        for url in urls:
            if len(results) >= limit:
                break
            image_id = await self._upload_remote_screenshot(meta, url)
            if image_id:
                results.append(image_id)
        return results

    async def _upload_local_screenshot(
        self, meta: Meta, path: Path
    ) -> str | None:
        try:
            async with aiofiles.open(path, "rb") as handle:
                content = await handle.read()
            return await self.img_host(
                meta, self._upload_referer(meta), content, path.name
            )
        except Exception as error:
            logger.info(
                f"{self.tracker}: Failed to process local screenshot {path}: {error}",
                extra={"markup": False},
            )
            return None

    async def _upload_remote_screenshot(
        self, meta: Meta, url: str
    ) -> str | None:
        try:
            response = await self.session.get(url)
            response.raise_for_status()
            filename = self._remote_image_filename(url)
            return await self.img_host(
                meta, self._upload_referer(meta), response.content, filename
            )
        except Exception as error:
            logger.info(
                f"{self.tracker}: Failed to process screenshot from URL {url}: {error}",
                extra={"markup": False},
            )
            return None

    def _upload_referer(self, meta: Meta) -> str:
        return f"{self.base_url}/upload/{meta.category.lower()}"

    @staticmethod
    def _remote_image_filename(url: str) -> str:
        filename = Path(urlparse(url).path).name or "screenshot.png"
        return filename if Path(filename).suffix else f"{filename}.png"

    async def get_requests(self, meta: Meta) -> list[dict[str, Any]] | None:
        if not self._request_search_enabled(meta):
            return []
        try:
            await self._apply_session_cookies(meta)
            response = await self.session.get(self._request_search_url(meta))
            response.raise_for_status()
            results = self._parse_request_rows(response.text)
            self._log_request_results(results)
            return results
        except Exception as error:
            logger.info(
                f"{self.tracker}: An error occurred while fetching requests: {error}"
            )
            return []

    def _request_search_enabled(self, meta: Meta) -> bool:
        default = self.config.get("DEFAULT", {})
        default_map = (
            cast(dict[str, Any], default) if isinstance(default, dict) else {}
        )
        configured = bool(default_map.get("search_requests", False))
        return configured or bool(meta.search_requests)

    def _request_search_url(self, meta: Meta) -> str:
        category = meta.category.lower()
        query = (
            f"{meta.title} {meta.season}{meta.episode}"
            if category == "tv"
            else str(meta.title)
        )
        return (
            f"{self.requests_url}?type={category}&search={query}&condition=new"
        )

    @classmethod
    def _parse_request_rows(cls, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        return [
            result
            for row in soup.select(".table-responsive table tbody tr")
            if (result := cls._request_row(row))
        ]

    @staticmethod
    def _request_row(row: Any) -> dict[str, Any] | None:
        link = row.select_one("a.torrent-filename")
        if link is None:
            return None
        cells = row.find_all("td")
        reward = cells[5].text.strip() if len(cells) > 5 else "N/A"
        return {
            "Name": link.text.strip(),
            "Link": link.get("href"),
            "Reward": reward,
        }

    def _log_request_results(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return
        lines = [
            f"\n{self.tracker}: [bold yellow]Your upload may fulfill the following request(s), check it out:[/bold yellow]\n"
        ]
        lines.extend(self._request_log_lines(result) for result in results)
        logger.info("\n".join(lines))

    @staticmethod
    def _request_log_lines(result: dict[str, Any]) -> str:
        return f"[bold green]Name:[/bold green] {result['Name']}\n[bold green]Reward:[/bold green] {result['Reward']}\n[bold green]Link:[/bold green] {result['Link']}\n"

    async def fetch_tag_id(self, word: str) -> int:
        tags_url = f"{self.base_url}/ajax/tags"
        params = {"term": word}

        headers = {
            "Referer": f"{self.base_url}/upload",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response = await self.session.get(
                tags_url, headers=headers, params=params
            )
            response.raise_for_status()

            json_data = response.json()

            for tag_info in json_data.get("data", []):
                if tag_info.get("tag") == word:
                    try:
                        return int(tag_info.get("id", 0))
                    except ValueError:
                        return 0

        except Exception as e:
            logger.info(
                f"{self.tracker}: An unexpected error occurred while processing the tag '{word}': {e}",
                extra={"markup": False},
            )

        return 0

    async def get_tags(self, meta: Meta) -> list[str]:
        phrases = self._tag_phrases(meta.keywords)
        if not phrases:
            return []
        ids = await asyncio.gather(
            *(self.fetch_tag_id(word) for word in phrases)
        )
        tags = [str(value) for value in ids if value]
        self._prepend_release_tags(tags, meta)
        return tags

    @staticmethod
    def _tag_phrases(value: Any) -> set[str]:
        values = cast(list[Any], value) if isinstance(value, list) else []
        return {
            re.sub(r"\s+", " ", str(item).strip().lower())
            for item in values
            if str(item).strip()
        }

    def _prepend_release_tags(self, tags: list[str], meta: Meta) -> None:
        personal = (
            self._tracker_tag_id("personal") if meta.personalrelease else None
        )
        internal = (
            self._tracker_tag_id("internal")
            if self._tracker_config().get("internal", False)
            else None
        )
        for value in (personal, internal):
            if value:
                tags.insert(0, value)

    def _tracker_tag_id(self, kind: str) -> str | None:
        mapping = {
            "personal": {
                "AVISTAZ": "3773",
                "CINEMAZ": "1594",
                "PRIVATEHD": "1448",
            },
            "internal": {
                "AVISTAZ": "943",
                "CINEMAZ": "938",
                "PRIVATEHD": "415",
            },
        }
        return mapping[kind].get(self.tracker)

    async def edit_desc(self, meta: Meta) -> str:
        description = await self._raw_description(meta)
        if not description:
            return ""
        processed = self._sanitize_description(description)
        rendered = self._render_description(processed)
        await self._write_description(meta, rendered)
        return rendered

    async def _raw_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        parts: list[str] = []
        title, overview = await builder.get_tv_info(meta)
        if overview:
            parts.extend(
                (f"[b]Episode:[/b] {title}", f"[b]Overview:[/b] {overview}")
            )
        parts.append(await builder.get_user_description(meta))
        parts.append(await builder.get_tonemapped_header(meta))
        return "\n\n".join(part for part in parts if part.strip()).replace(
            "[*]", "• "
        )

    def _sanitize_description(self, description: str) -> str:
        value = description
        value = self._logged_substitution(
            value,
            r"\[center\]\[spoiler=.*? NFO:\]\[code\](.*?)\[/code\]\[/spoiler\]\[/center\]",
            "",
            "NFO section",
            flags=re.DOTALL,
        )
        value = self._logged_substitution(
            value, r"http[s]?://\S+|www\.\S+", "", "link(s)"
        )
        pattern = r"\[/?(size|align|left|center|right|img|table|tr|td|spoiler|url)[^\]]*\]"
        return self._logged_substitution(
            value, pattern, "", "BBCode tag(s)", flags=re.IGNORECASE
        )

    def _logged_substitution(
        self,
        value: str,
        pattern: str,
        replacement: str,
        label: str,
        *,
        flags: int = 0,
    ) -> str:
        updated, amount = re.subn(pattern, replacement, value, flags=flags)
        if amount:
            logger.info(
                f"{self.tracker}: Deleted from description: {amount} {label}."
            )
        return updated

    @staticmethod
    def _render_description(value: str) -> str:
        render_html = getattr(bbcode, "render_html", None)
        if callable(render_html):
            return cast(Callable[[str], str], render_html)(value)
        return cast(Any, bbcode).Parser().format(value)

    async def _write_description(self, meta: Meta, value: str) -> None:
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(value)

    async def create_task_id(self, meta: Meta) -> dict[str, Any]:
        await self.get_media_code(meta)
        data = await self._task_payload(meta)
        if meta.debug:
            logger.info(Redaction.redact_private_info(data))
            meta.tracker_status[self.tracker]["status_message"] = (
                "Debug mode enabled, not uploading."
            )
            return {}
        try:
            response = await self._submit_task_step_one(meta, data)
            task = await self._task_from_response(meta, response)
            if task is not None:
                return task
            await self._record_step_one_failure(meta, response)
        except Exception as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"[red]An unexpected error occurred while uploading to {self.tracker}: {error}[/red]"
            )
            meta.skipping = self.tracker
        return {}

    async def _task_payload(self, meta: Meta) -> dict[str, Any]:
        return {
            "_token": self.az_class.secret_token,
            "type_id": self.get_cat_id(meta.category),
            "movie_id": self.media_code,
            "media_info": await self.get_file_info(meta),
        }

    def _default_announce_url(self) -> str:
        return {
            "AVISTAZ": "https://tracker.avistaz.to/announce",
            "CINEMAZ": "https://tracker.cinemaz.to/announce",
            "PRIVATEHD": "https://tracker.privatehd.to/announce",
        }.get(self.tracker, "")

    async def _submit_task_step_one(
        self, meta: Meta, data: dict[str, Any]
    ) -> httpx.Response:
        await self.common.create_torrent_for_upload(
            meta,
            self.tracker,
            self._source_flag,
            announce_url=self._default_announce_url(),
        )
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(path, "rb") as handle:
            content = await handle.read()
        files = {
            "torrent_file": (path.name, content, "application/x-bittorrent")
        }
        return await self.session.post(
            f"{self.base_url}/upload/{meta.category.lower()}",
            data=data,
            files=files,
        )

    async def _task_from_response(
        self, meta: Meta, response: httpx.Response
    ) -> dict[str, Any] | None:
        redirect = self._task_redirect(response)
        if redirect is None:
            return None
        task_id = self._task_id_from_redirect(redirect)
        if task_id is None:
            logger.info(
                f"{self.tracker}: Could not extract 'task_id' from redirect URL: {redirect}"
            )
            logger.info(
                f"{self.tracker}: The cookie appears to be expired or invalid."
            )
            meta.skipping = self.tracker
            return {}
        return {
            "task_id": task_id,
            "info_hash": await self.common.get_torrent_hash(
                meta, self.tracker
            ),
            "redirect_url": redirect,
        }

    @staticmethod
    def _task_redirect(response: httpx.Response) -> str | None:
        if response.status_code != 302:
            return None
        value = response.headers.get("Location")
        return value if isinstance(value, str) else None

    @staticmethod
    def _task_id_from_redirect(value: str) -> str | None:
        match = re.search(r"/(\d+)$", value)
        return match.group(1) if match else None

    async def _record_step_one_failure(
        self, meta: Meta, response: httpx.Response
    ) -> None:
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]FailedUpload_Step1.html"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(response.text)
        meta.tracker_status[self.tracker]["status_message"] = (
            f"[red]Step 1 of upload failed to {self.tracker}. Status: {response.status_code}, URL: {response.url}[/red]. "
            f"[yellow]The HTML response was saved to '{path}' for analysis.[/yellow]"
        )

    async def get_name(self, meta: Meta) -> str:
        name = self._initial_upload_name(meta)
        name = self._normalize_site_title(meta, name)
        name = self._normalize_encode_codecs(meta, name)
        name = self._apply_group_marker(meta, name)
        name = self._apply_tv_year(meta, name)
        name = self._apply_source_title_rules(meta, name)
        return re.sub(r"\s{2,}", " ", name)

    @staticmethod
    def _initial_upload_name(meta: Meta) -> str:
        name = str(meta.name or "")
        removals = (
            meta.aka,
            "Dubbed",
            "Dual-Audio",
            meta.manual_episode_title,
            meta.daily_episode_title,
        )
        for value in removals:
            if value:
                name = name.replace(str(value), "")
        return name

    def _normalize_site_title(self, meta: Meta, name: str) -> str:
        if self.tracker not in {"CINEMAZ", "PRIVATEHD"}:
            return name
        value = self._strip_forbidden_title_terms(name)
        value = self._normalize_cut_terms(value)
        value = value.replace("[", "").replace("]", "")
        if self.tracker == "CINEMAZ":
            value = self._reposition_cinemaz_hybrid(meta, value)
        return re.sub(r"\s{2,}", " ", value).strip()

    @staticmethod
    def _strip_forbidden_title_terms(value: str) -> str:
        patterns = (
            r"\bLIMITED\b",
            r"\bCriterion Collection\b",
            r"\b\d{1,3}(?:st|nd|rd|th)\s+Anniversary Edition\b",
        )
        result = value
        for pattern in patterns:
            result = re.sub(pattern, "", result, flags=re.IGNORECASE).strip()
        return result

    def _normalize_cut_terms(self, value: str) -> str:
        result = re.sub(
            r"\bDirector[\u2019'`]s\s+Cut\b", "DC", value, flags=re.IGNORECASE
        )
        extended = "EXT" if self.tracker == "CINEMAZ" else "Extended"
        theatrical = "TC" if self.tracker == "CINEMAZ" else "Theatrical"
        result = re.sub(
            r"\bExtended\s+Cut\b", extended, result, flags=re.IGNORECASE
        )
        return re.sub(
            r"\bTheatrical\s+Cut\b", theatrical, result, flags=re.IGNORECASE
        )

    @classmethod
    def _reposition_cinemaz_hybrid(cls, meta: Meta, value: str) -> str:
        if not cls._should_reposition_hybrid(meta):
            return value
        positions = cls._hybrid_positions(meta, value)
        if positions is None:
            return value
        start, end = positions
        without = f"{value[:start]}{value[end:]}"
        resolution = re.search(
            r"\b(?:\d{3,4}[pi]|4K|UHD|SD)\b", without, flags=re.IGNORECASE
        )
        if resolution is None:
            return value
        return f"{without[: resolution.end()]} HYBRID{without[resolution.end() :]}"

    @staticmethod
    def _should_reposition_hybrid(meta: Meta) -> bool:
        return (
            bool(meta.webdv) or "hybrid" in str(meta.edition or "").casefold()
        )

    @staticmethod
    def _hybrid_positions(meta: Meta, value: str) -> tuple[int, int] | None:
        title_match = (
            re.search(re.escape(str(meta.title)), value, flags=re.IGNORECASE)
            if meta.title
            else None
        )
        search_start = title_match.end() if title_match else 0
        hybrid = re.search(
            r"\bHYBRID\b", value[search_start:], flags=re.IGNORECASE
        )
        if hybrid is None:
            return None
        return search_start + hybrid.start(), search_start + hybrid.end()

    @staticmethod
    def _normalize_encode_codecs(meta: Meta, value: str) -> str:
        if not meta.has_encode_settings:
            return value
        return value.replace("H.264", "x264").replace("H.265", "x265")

    def _apply_group_marker(self, meta: Meta, value: str) -> str:
        if not self._missing_group_marker(meta.tag):
            return value
        cleaned = self._strip_invalid_group_markers(value)
        suffix = {"CINEMAZ": "-NoGroup", "PRIVATEHD": "-NOGROUP"}.get(
            self.tracker, ""
        )
        return f"{cleaned}{suffix}" if suffix else cleaned

    @staticmethod
    def _missing_group_marker(tag: Any) -> bool:
        text = str(tag or "").lower()
        return not text or any(
            marker in text
            for marker in ("nogrp", "nogroup", "unknown", "-unk-")
        )

    @staticmethod
    def _strip_invalid_group_markers(value: str) -> str:
        result = value
        for marker in ("nogrp", "nogroup", "unknown", "-unk-"):
            result = re.sub(f"-{marker}", "", result, flags=re.IGNORECASE)
        return result

    def _apply_tv_year(self, meta: Meta, value: str) -> str:
        if meta.category != "TV":
            return value
        year = self._tv_year(meta)
        result = self._insert_tv_year(meta, value, year)
        return self._site_tv_year_rules(meta, result, year)

    @classmethod
    def _insert_tv_year(cls, meta: Meta, value: str, year: Any) -> str:
        if not year or not cls._should_insert_tv_year(meta):
            return value
        return value.replace(str(meta.title), f"{meta.title} {year}", 1)

    def _site_tv_year_rules(self, meta: Meta, value: str, year: Any) -> str:
        if not year:
            return value
        if self.tracker == "PRIVATEHD":
            return value.replace(str(year), "")
        if self.tracker == "AVISTAZ" and meta.tv_pack:
            return value.replace(
                f"{meta.title} {year} {meta.season}",
                f"{meta.title} {meta.season} {year}",
            )
        return value

    @classmethod
    def _tv_year(cls, meta: Meta) -> Any:
        year = meta.year
        if not cls._should_insert_tv_year(meta):
            return year
        season_year = cls._season_year(meta)
        return season_year if season_year else year

    @staticmethod
    def _should_insert_tv_year(meta: Meta) -> bool:
        return not meta.no_year and not meta.search_year

    @classmethod
    def _season_year(cls, meta: Meta) -> Any:
        seasons = cls._season_summary_entries(meta.imdb_info)
        match = next(
            (
                season
                for season in seasons
                if season.get("season") == meta.season_int
            ),
            None,
        )
        return match.get("year") if match is not None else None

    @staticmethod
    def _season_summary_entries(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        mapping = cast(dict[str, Any], value)
        summaries = mapping.get("seasons_summary", [])
        if not isinstance(summaries, list):
            return []
        values = cast(list[Any], summaries)
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    def _apply_source_title_rules(self, meta: Meta, value: str) -> str:
        result = value
        if meta.type == "DVDRIP" and meta.source:
            result = result.replace(str(meta.source), "")
        if meta.is_disc == "DVD":
            result = self._apply_dvd_title_rules(meta, result)
        return result

    @classmethod
    def _apply_dvd_title_rules(cls, meta: Meta, value: str) -> str:
        result = cls._remove_region_from_name(meta, value)
        result = cls._replace_dvd_source(meta, result)
        return cls._append_dvd_audio_codec(meta, result)

    @staticmethod
    def _remove_region_from_name(meta: Meta, value: str) -> str:
        return value.replace(str(meta.region), "") if meta.region else value

    @staticmethod
    def _replace_dvd_source(meta: Meta, value: str) -> str:
        if meta.source and meta.resolution:
            return value.replace(str(meta.source), str(meta.resolution))
        return value

    @staticmethod
    def _append_dvd_audio_codec(meta: Meta, value: str) -> str:
        if not meta.audio:
            return value
        codec = str(meta.video_codec or "").strip()
        suffix = f" {codec}" if codec else ""
        return value.replace(str(meta.audio), f"{meta.audio}{suffix}")

    def get_rip_type(self, meta: Meta, display_name: bool = False) -> str:
        label = self._rip_type_label(meta)
        if display_name:
            return label
        return self._rip_type_ids().get(label, "0")

    @classmethod
    def _rip_type_label(cls, meta: Meta) -> str:
        source_type = str(meta.type or "").strip().lower()
        special = cls._special_rip_type(meta, source_type)
        return (
            special
            if special is not None
            else cls._rip_type_translation().get(source_type, "")
        )

    @classmethod
    def _special_rip_type(cls, meta: Meta, source_type: str) -> str | None:
        if source_type == "disc":
            return cls._disc_rip_type(str(meta.is_disc or "").strip().lower())
        if source_type == "remux":
            return cls._remux_rip_type(str(meta.source or "").strip().lower())
        return None

    @staticmethod
    def _disc_rip_type(is_disc: str) -> str:
        if is_disc == "bdmv":
            return "BluRay Raw"
        return "DVD" if is_disc in {"dvd", "hddvd"} else ""

    @staticmethod
    def _remux_rip_type(source: str) -> str:
        if "dvd" in source:
            return "DVD Remux"
        if source in {"bluray", "blu-ray"}:
            return "BluRay REMUX"
        return ""

    @staticmethod
    def _rip_type_translation() -> dict[str, str]:
        return {
            "bdrip": "BDRip",
            "brrip": "BRRip",
            "encode": "BluRay",
            "dvdrip": "DVDRip",
            "hdrip": "HDRip",
            "hdtv": "HDTV",
            "sdtv": "SDTV",
            "vcd": "VCD",
            "vcdrip": "VCDRip",
            "vhsrip": "VHSRip",
            "vodrip": "VODRip",
            "webdl": "WEB-DL",
            "webrip": "WEBRip",
        }

    @staticmethod
    def _rip_type_ids() -> dict[str, str]:
        return {
            "BDRip": "1",
            "BluRay": "2",
            "BRRip": "3",
            "DVD": "4",
            "DVDRip": "5",
            "HDRip": "6",
            "HDTV": "7",
            "VCD": "8",
            "VCDRip": "9",
            "VHSRip": "10",
            "VODRip": "11",
            "WEB-DL": "12",
            "WEBRip": "13",
            "BluRay REMUX": "14",
            "BluRay Raw": "15",
            "SDTV": "16",
            "DVD Remux": "17",
        }

    async def fetch_data(self, meta: Meta) -> dict[str, Any]:
        await self._apply_session_cookies(meta)
        lang_info = await self.get_lang(meta) or {}
        if getattr(meta, "skipping", None) == self.tracker:
            return {}
        task_info = await self.create_task_id(meta)
        data = await self._base_upload_form(meta, lang_info)
        self._apply_tv_upload_fields(data, meta)
        self._apply_anon_upload(data, meta)
        await self._apply_task_upload_fields(data, meta, task_info)
        return data

    async def _base_upload_form(
        self, meta: Meta, lang_info: dict[str, list[str]]
    ) -> dict[str, Any]:
        return {
            "_token": self.az_class.secret_token,
            "torrent_id": "",
            "type_id": self.get_cat_id(meta.category),
            "file_name": await self.get_name(meta),
            "anon_upload": "",
            "description": await self.edit_desc(meta),
            "qqfile": "",
            "rip_type_id": self.get_rip_type(meta),
            "video_quality_id": self.get_video_quality(meta),
            "video_resolution": f"{meta.video_width}x{meta.video_height}",
            "movie_id": self.media_code,
            "languages[]": lang_info.get("languages[]"),
            "subtitles[]": lang_info.get("subtitles[]"),
            "media_info": await self.get_file_info(meta),
            "tags[]": await self.get_tags(meta),
            "screenshots[]": [""],
        }

    @staticmethod
    def _apply_tv_upload_fields(data: dict[str, Any], meta: Meta) -> None:
        if meta.category != "TV":
            return
        data.update(
            {
                "tv_collection": "1" if meta.tv_pack == 0 else "2",
                "tv_season": meta.season_int,
                "tv_episode": meta.episode_int,
            }
        )

    def _apply_anon_upload(self, data: dict[str, Any], meta: Meta) -> None:
        anonymous = not (
            meta.anon == 0 and not self._tracker_config().get("anon", False)
        )
        if anonymous:
            data["anon_upload"] = "1"

    async def _apply_task_upload_fields(
        self, data: dict[str, Any], meta: Meta, task_info: dict[str, Any]
    ) -> None:
        if meta.debug:
            return
        try:
            self.upload_url_step2 = str(task_info.get("redirect_url", ""))
            screenshots = await self.get_screenshots(meta) or []
            data.update(
                {
                    "info_hash": task_info.get("info_hash"),
                    "task_id": task_info.get("task_id"),
                    "screenshots[]": screenshots,
                }
            )
        except Exception as error:
            logger.info(
                f"{self.tracker}: An unexpected error occurred while uploading: {error}"
            )

    def check_data(self, meta: Meta, data: dict[str, Any]) -> str | bool:
        live_issue = self._live_upload_issue(meta, data)
        if live_issue:
            return live_issue
        return self._mapping_upload_issue(data)

    def _live_upload_issue(self, meta: Meta, data: dict[str, Any]) -> str:
        if meta.debug:
            return ""
        screenshot_issue = self._screenshot_upload_issue(data)
        if screenshot_issue:
            return screenshot_issue
        return self._task_upload_issue(data)

    def _screenshot_upload_issue(self, data: dict[str, Any]) -> str:
        screenshots = data.get("screenshots[]", [])
        if isinstance(screenshots, list) and len(screenshots) >= 3:
            return ""
        return f"UPLOAD FAILED: The {self.tracker} image host did not return the minimum number of screenshots."

    def _task_upload_issue(self, data: dict[str, Any]) -> str:
        values = (
            self.upload_url_step2,
            data.get("task_id"),
            data.get("info_hash"),
        )
        if all(values):
            return ""
        return "UPLOAD FAILED: Step 1 did not complete (missing redirect/task_id/info_hash)."

    @staticmethod
    def _mapping_upload_issue(data: dict[str, Any]) -> str | bool:
        checks = (
            (
                "rip_type_id",
                "UPLOAD FAILED: Unable to determine rip type for this upload.",
            ),
            (
                "type_id",
                "UPLOAD FAILED: Unable to determine category for this upload.",
            ),
            (
                "video_quality_id",
                "UPLOAD FAILED: Unable to determine the resolution for this upload.",
            ),
        )
        for key, message in checks:
            if data.get(key) == "0":
                return message
        return False

    async def upload(self, meta: Meta) -> bool:
        data = await self.fetch_data(meta)
        if getattr(meta, "skipping", None) == self.tracker:
            return False
        issue = self.check_data(meta, data)
        if issue:
            meta.tracker_status[self.tracker] = f"data error - {issue}"
            return False
        if meta.debug:
            return await self._debug_upload(meta, data)
        return await self._live_upload(meta, data)

    async def _live_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        response = await self.session.post(self.upload_url_step2, data=data)
        if response.status_code == 302:
            return await self._record_successful_upload(meta, response)
        await self._record_step_two_failure(meta, response)
        return False

    async def _record_successful_upload(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        torrent_url = response.headers["Location"]
        download_url = torrent_url.replace("/torrent/", "/download/torrent/")
        registered = await self.session.get(download_url)
        if registered.status_code != 200:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error - Unable to register your upload in your download history, please go to the URL and download the torrent file before you can start seeding: {torrent_url}\n"
                f"Error: {registered.status_code}"
            )
            return False
        await self.common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self._source_flag,
            self.announce_url,
            torrent_url,
        )
        status = meta.tracker_status[self.tracker]
        status["status_message"] = (
            f"{self.tracker} torrent uploaded successfully."
        )
        torrent_id = self._torrent_id_from_url(torrent_url)
        if torrent_id:
            status["torrent_id"] = torrent_id
        return True

    @staticmethod
    def _torrent_id_from_url(url: str) -> str:
        match = re.search(r"/torrent/(\d+)", url)
        return match.group(1) if match else ""

    async def _record_step_two_failure(
        self, meta: Meta, response: httpx.Response
    ) -> None:
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]FailedUpload_Step2.html"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(response.text)
        meta.tracker_status[self.tracker]["status_message"] = (
            f"data error - It may have uploaded, go check\nStep 2 of upload to {self.tracker} failed.\nStatus code: {response.status_code}\n"
            f"URL: {response.url}\nThe HTML response has been saved to '{path}' for analysis."
        )

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

    def language_map(self, meta: Meta) -> None:
        all_lang_map: dict[tuple[str, ...], str] = {
            ("Abkhazian", "abk", "ab"): "1",
            ("Afar", "aar", "aa"): "2",
            ("Afrikaans", "afr", "af"): "3",
            ("Akan", "aka", "ak"): "4",
            ("Albanian", "sqi", "sq"): "5",
            ("Amharic", "amh", "am"): "6",
            ("Arabic", "ara", "ar"): "7",
            ("Aragonese", "arg", "an"): "8",
            ("Armenian", "hye", "hy"): "9",
            ("Assamese", "asm", "as"): "10",
            ("Avaric", "ava", "av"): "11",
            ("Avestan", "ave", "ae"): "12",
            ("Aymara", "aym", "ay"): "13",
            ("Azerbaijani", "aze", "az"): "14",
            ("Bambara", "bam", "bm"): "15",
            ("Bashkir", "bak", "ba"): "16",
            ("Basque", "eus", "eu"): "17",
            ("Belarusian", "bel", "be"): "18",
            ("Bengali", "ben", "bn"): "19",
            ("Bihari languages", "bih", "bh"): "20",
            ("Bislama", "bis", "bi"): "21",
            ("Bokmål, Norwegian", "nob", "nb"): "22",
            ("Bosnian", "bos", "bs"): "23",
            ("Breton", "bre", "br"): "24",
            ("Bulgarian", "bul", "bg"): "25",
            ("Burmese", "mya", "my"): "26",
            ("Cantonese", "yue", "zh"): "27",
            ("Catalan", "cat", "ca"): "28",
            ("Central Khmer", "khm", "km"): "29",
            ("Chamorro", "cha", "ch"): "30",
            ("Chechen", "che", "ce"): "31",
            ("Chichewa", "nya", "ny"): "32",
            ("Chinese", "zho", "zh"): "33",
            ("Church Slavic", "chu", "cu"): "34",
            ("Chuvash", "chv", "cv"): "35",
            ("Cornish", "cor", "kw"): "36",
            ("Corsican", "cos", "co"): "37",
            ("Cree", "cre", "cr"): "38",
            ("Croatian", "hrv", "hr"): "39",
            ("Czech", "ces", "cs"): "40",
            ("Danish", "dan", "da"): "41",
            ("Dhivehi", "div", "dv"): "42",
            ("Dutch", "nld", "nl"): "43",
            ("Dzongkha", "dzo", "dz"): "44",
            ("English", "eng", "en"): "45",
            ("Esperanto", "epo", "eo"): "46",
            ("Estonian", "est", "et"): "47",
            ("Ewe", "ewe", "ee"): "48",
            ("Faroese", "fao", "fo"): "49",
            ("Fijian", "fij", "fj"): "50",
            ("Finnish", "fin", "fi"): "51",
            ("French", "fra", "fr"): "52",
            ("Fulah", "ful", "ff"): "53",
            ("Gaelic", "gla", "gd"): "54",
            ("Galician", "glg", "gl"): "55",
            ("Ganda", "lug", "lg"): "56",
            ("Georgian", "kat", "ka"): "57",
            ("German", "deu", "de"): "58",
            ("Greek", "ell", "el"): "59",
            ("Guarani", "grn", "gn"): "60",
            ("Gujarati", "guj", "gu"): "61",
            ("Haitian", "hat", "ht"): "62",
            ("Hausa", "hau", "ha"): "63",
            ("Hebrew", "heb", "he"): "64",
            ("Herero", "her", "hz"): "65",
            ("Hindi", "hin", "hi"): "66",
            ("Hiri Motu", "hmo", "ho"): "67",
            ("Hungarian", "hun", "hu"): "68",
            ("Icelandic", "isl", "is"): "69",
            ("Ido", "ido", "io"): "70",
            ("Igbo", "ibo", "ig"): "71",
            ("Indonesian", "ind", "id"): "72",
            ("Interlingua", "ina", "ia"): "73",
            ("Interlingue", "ile", "ie"): "74",
            ("Inuktitut", "iku", "iu"): "75",
            ("Inupiaq", "ipk", "ik"): "76",
            ("Irish", "gle", "ga"): "77",
            ("Italian", "ita", "it"): "78",
            ("Japanese", "jpn", "ja"): "79",
            ("Javanese", "jav", "jv"): "80",
            ("Kalaallisut", "kal", "kl"): "81",
            ("Kannada", "kan", "kn"): "82",
            ("Kanuri", "kau", "kr"): "83",
            ("Kashmiri", "kas", "ks"): "84",
            ("Kazakh", "kaz", "kk"): "85",
            ("Kikuyu", "kik", "ki"): "86",
            ("Kinyarwanda", "kin", "rw"): "87",
            ("Kirghiz", "kir", "ky"): "88",
            ("Komi", "kom", "kv"): "89",
            ("Kongo", "kon", "kg"): "90",
            ("Korean", "kor", "ko"): "91",
            ("Kuanyama", "kua", "kj"): "92",
            ("Kurdish", "kur", "ku"): "93",
            ("Lao", "lao", "lo"): "94",
            ("Latin", "lat", "la"): "95",
            ("Latvian", "lav", "lv"): "96",
            ("Limburgan", "lim", "li"): "97",
            ("Lingala", "lin", "ln"): "98",
            ("Lithuanian", "lit", "lt"): "99",
            ("Luba-Katanga", "lub", "lu"): "100",
            ("Luxembourgish", "ltz", "lb"): "101",
            ("Macedonian", "mkd", "mk"): "102",
            ("Malagasy", "mlg", "mg"): "103",
            ("Malay", "msa", "ms"): "104",
            ("Malayalam", "mal", "ml"): "105",
            ("Maltese", "mlt", "mt"): "106",
            ("Mandarin", "cmn", "cmn"): "107",
            ("Manx", "glv", "gv"): "108",
            ("Maori", "mri", "mi"): "109",
            ("Marathi", "mar", "mr"): "110",
            ("Marshallese", "mah", "mh"): "111",
            ("Mongolian", "mon", "mn"): "112",
            ("Nauru", "nau", "na"): "113",
            ("Navajo", "nav", "nv"): "114",
            ("Ndebele, North", "nde", "nd"): "115",
            ("Ndebele, South", "nbl", "nr"): "116",
            ("Ndonga", "ndo", "ng"): "117",
            ("Nepali", "nep", "ne"): "118",
            ("Northern Sami", "sme", "se"): "119",
            ("Norwegian", "nor", "no"): "120",
            ("Norwegian Nynorsk", "nno", "nn"): "121",
            ("Occitan (post 1500)", "oci", "oc"): "122",
            ("Ojibwa", "oji", "oj"): "123",
            ("Oriya", "ori", "or"): "124",
            ("Oromo", "orm", "om"): "125",
            ("Ossetian", "oss", "os"): "126",
            ("Pali", "pli", "pi"): "127",
            ("Panjabi", "pan", "pa"): "128",
            ("Persian", "fas", "fa"): "129",
            ("Polish", "pol", "pl"): "130",
            ("Portuguese", "por", "pt"): "131",
            ("Pushto", "pus", "ps"): "132",
            ("Quechua", "que", "qu"): "133",
            ("Romanian", "ron", "ro"): "134",
            ("Romansh", "roh", "rm"): "135",
            ("Rundi", "run", "rn"): "136",
            ("Russian", "rus", "ru"): "137",
            ("Samoan", "smo", "sm"): "138",
            ("Sango", "sag", "sg"): "139",
            ("Sanskrit", "san", "sa"): "140",
            ("Sardinian", "srd", "sc"): "141",
            ("Serbian", "srp", "sr"): "142",
            ("Shona", "sna", "sn"): "143",
            ("Sichuan Yi", "iii", "ii"): "144",
            ("Sindhi", "snd", "sd"): "145",
            ("Sinhala", "sin", "si"): "146",
            ("Slovak", "slk", "sk"): "147",
            ("Slovenian", "slv", "sl"): "148",
            ("Somali", "som", "so"): "149",
            ("Sotho, Southern", "sot", "st"): "150",
            ("Spanish", "spa", "es"): "151",
            ("Sundanese", "sun", "su"): "152",
            ("Swahili", "swa", "sw"): "153",
            ("Swati", "ssw", "ss"): "154",
            ("Swedish", "swe", "sv"): "155",
            ("Tagalog", "tgl", "tl"): "156",
            ("Tahitian", "tah", "ty"): "157",
            ("Tajik", "tgk", "tg"): "158",
            ("Tamil", "tam", "ta"): "159",
            ("Tatar", "tat", "tt"): "160",
            ("Telugu", "tel", "te"): "161",
            ("Thai", "tha", "th"): "162",
            ("Tibetan", "bod", "bo"): "163",
            ("Tigrinya", "tir", "ti"): "164",
            ("Tongan", "ton", "to"): "165",
            ("Tsonga", "tso", "ts"): "166",
            ("Tswana", "tsn", "tn"): "167",
            ("Turkish", "tur", "tr"): "168",
            ("Turkmen", "tuk", "tk"): "169",
            ("Twi", "twi", "tw"): "170",
            ("Uighur", "uig", "ug"): "171",
            ("Ukrainian", "ukr", "uk"): "172",
            ("Urdu", "urd", "ur"): "173",
            ("Uzbek", "uzb", "uz"): "174",
            ("Venda", "ven", "ve"): "175",
            ("Vietnamese", "vie", "vi"): "176",
            ("Volapük", "vol", "vo"): "177",
            ("Walloon", "wln", "wa"): "178",
            ("Welsh", "cym", "cy"): "179",
            ("Western Frisian", "fry", "fy"): "180",
            ("Wolof", "wol", "wo"): "181",
            ("Xhosa", "xho", "xh"): "182",
            ("Yiddish", "yid", "yi"): "183",
            ("Yoruba", "yor", "yo"): "184",
            ("Zhuang", "zha", "za"): "185",
            ("Zulu", "zul", "zu"): "186",
        }

        all_lang_map.update(self._language_map_overrides(meta))
        self.lang_map = self._flatten_language_map(all_lang_map)

    def _language_map_overrides(
        self, meta: Meta
    ) -> dict[tuple[str, ...], str]:
        overrides = self._tracker_language_overrides()
        if meta.is_disc:
            overrides.update(self._disc_language_overrides())
        return overrides

    def _tracker_language_overrides(self) -> dict[tuple[str, ...], str]:
        return {
            "PRIVATEHD": {
                ("Portuguese (BR)", "por", "pt-br"): "187",
                ("Filipino", "fil", "fil"): "189",
                ("Mooré", "mos", "mos"): "188",
            },
            "AVISTAZ": {
                ("Portuguese (BR)", "por", "pt-br"): "189",
                ("Filipino", "fil", "fil"): "188",
                ("Mooré", "mos", "mos"): "187",
            },
            "CINEMAZ": {
                ("Portuguese (BR)", "por", "pt-br"): "187",
                ("Mooré", "mos", "mos"): "188",
                ("Filipino", "fil", "fil"): "189",
                ("Bissa", "bib", "bib"): "190",
                ("Romani", "rom", "rom"): "191",
            },
        }.get(self.tracker, {})

    def _disc_language_overrides(self) -> dict[tuple[str, ...], str]:
        value = {"CINEMAZ": "187", "AVISTAZ": "189", "PRIVATEHD": "187"}.get(
            self.tracker
        )
        return {("Portuguese", "por", "pt-br"): value} if value else {}

    @staticmethod
    def _flatten_language_map(
        mapping: dict[tuple[str, ...], str],
    ) -> dict[str, str]:
        return {
            alias.lower(): lang_id
            for aliases, lang_id in mapping.items()
            for alias in aliases
            if alias
        }
