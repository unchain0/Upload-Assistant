# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import httpx
import requests

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.announce_url import required_announce_url
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.trackers.common import Common

Config = dict[str, Any]


class TVChaosUK:
    """
    TVC Private Torrent Tracker
    """

    base_url = "https://tvchaosuk.com"

    auth_type = "other_api"
    tracker = "TVCHAOSUK"
    display_name = "TVChaosUK"
    allows_bloated_audio = True
    source_flag = "TVCHAOS"
    signature = ""
    banned_groups = ()
    approved_image_hosts = ("imgbb", "imgbox", "pixhost", "bam", "onlyimage")
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "imgbox.com": "imgbox",
            "pixhost.to": "pixhost",
            "imagebam.com": "bam",
            "onlyimage.org": "onlyimage",
        },
        approved_image_hosts,
    )
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    tv_type_map: ClassVar = {
        "comedy": "29",
        "current affairs": "45",
        "documentary": "5",
        "drama": "11",
        "entertainment": "14",
        "factual": "19",
        "foreign": "43",
        "holding bin": "53",
        "kids": "32",
        "movies": "44",
        "news": "54",
        "reality": "52",
        "sci-fi": "33",
        "soaps": "30",
        "sport": "42",
    }
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://tvchaosuk.com",)
    # Constants for the class
    DEFAULT_LOGO_SIZE = "300"
    SCREENSHOT_THUMB_SIZE = "350"
    COMPARISON_COLLAPSE_THRESHOLD = 1000
    MIN_SCREENSHOTS_REQUIRED = 2

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.rehost_images_manager = RehostImagesManager(config)
        self.tmdb_manager = TmdbManager(config)
        self.common = Common(config=config)

        # TV type mapping as a dict for clarity and maintainability

    def format_date_ddmmyyyy(self, date_str: str) -> str:
        """
        Convert a date string from 'YYYY-MM-DD' to 'DD-MM-YYYY'.

        Args:
            date_str (str): Input date string.

        Returns:
            str: Reformatted date string, or the original if parsing fails.
        """
        try:
            return (
                datetime.strptime(date_str, "%Y-%m-%d")
                .replace(tzinfo=UTC)
                .strftime("%d-%m-%Y")
            )
        except ValueError, TypeError:
            return date_str

    async def _read_base_description(self, meta: Meta) -> str:
        """Return the current CLI-provided base description."""
        from src.domain_models.release_description import base_description

        return base_description(meta)

    def _ensure_desc_directory(self, meta: Meta, tracker: str) -> str:
        """Create description directory and return file path."""
        desc_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        Path(desc_dir).mkdir(parents=True, exist_ok=True)
        return str(Path(desc_dir) / f"[{tracker}]DESCRIPTION.txt")

    def _build_disc_info(self, discs: list[dict[str, Any]]) -> str:
        """
        Build disc information section.

        Note: TVCHAOSUK does not currently accept BDMV/Blu-ray disc releases (only HDTV and WEB-DL).
        This method exists for code compatibility/future use and will not be called during
        normal TVCHAOSUK uploads due to the disc blocking in search_existing().
        """
        parts: list[str] = []

        # Process all discs uniformly
        for disc in discs:
            if disc["type"] == "BDMV":
                name = disc.get("name", "BDINFO")
                parts.append(
                    f"[center][spoiler={name}][code]{disc['summary']}[/code][/spoiler][/center]\n\n"
                )
            elif disc["type"] == "DVD":
                # For first DVD disc, use VOB MediaInfo label
                if not parts:  # First disc
                    parts.append(
                        f"[center][spoiler=VOB MediaInfo][code]{disc['vob_mi']}[/code][/spoiler][/center]\n\n"
                    )
                else:  # Subsequent DVD discs
                    vob_name = Path(disc["vob"]).name
                    ifo_name = Path(disc["ifo"]).name
                    parts.append(
                        f"[center]{disc['name']}:\n"
                        f"[spoiler={vob_name}][code]{disc['vob_mi']}[/code][/spoiler] "
                        f"[spoiler={ifo_name}][code]{disc['ifo_mi']}[/code][/spoiler][/center]\n\n"
                    )

        return "".join(parts)

    def _build_movie_desc(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        parts = [
            self._movie_release_block(meta),
            self._logo_block(meta),
            self._movie_title_block(meta),
            self._overview_block(meta.overview),
            self._release_date_block(meta),
            self._external_links_block(meta),
            self._screenshots_block(meta, image_list),
        ]
        return "".join(part for part in parts if part)

    def _movie_release_block(self, meta: Meta) -> str:
        info = self._get_movie_release_info(meta)
        return f"[center]{info}[/center]\n\n" if info else ""

    def _logo_block(self, meta: Meta) -> str:
        if not meta.logo:
            return ""
        default = self.config.get("DEFAULT", {})
        logo_size = (
            default.get("logo_size", self.DEFAULT_LOGO_SIZE)
            if isinstance(default, dict)
            else self.DEFAULT_LOGO_SIZE
        )
        return f"[center][img={logo_size}]{meta.logo}[/img][/center]\n\n"

    @staticmethod
    def _movie_title_block(meta: Meta) -> str:
        title = meta.title if meta.title is not None else "Unknown Movie"
        return f"[center][b]Movie Title:[/b] {title}[/center]\n\n"

    @staticmethod
    def _overview_block(value: Any) -> str:
        overview = str(value or "").strip()
        return f"[center]{overview}[/center]\n\n" if overview else ""

    def _release_date_block(self, meta: Meta) -> str:
        if "release_date" not in meta or not meta.release_date:
            return ""
        formatted = self.format_date_ddmmyyyy(meta.release_date)
        return f"[center][b]Released on:[/b] {formatted}[/center]\n\n"

    def _external_links_block(self, meta: Meta) -> str:
        links = self.get_links(meta).strip()
        return f"[center]{links}[/center]\n\n" if links else ""

    def _screenshots_block(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        screenshots = self._add_screenshots(meta, image_list).strip()
        return f"[center]{screenshots}[/center]\n\n" if screenshots else ""

    def _build_tv_pack_desc(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        parts = [
            self._logo_block(meta),
            self._series_info_block(meta),
            self._episode_list_block(meta),
            self._external_links_block(meta),
            self._screenshots_block(meta, image_list),
        ]
        return "".join(part for part in parts if part)

    def _series_info_block(self, meta: Meta) -> str:
        if "season_air_first_date" not in meta:
            return ""
        channel = meta.networks if meta.networks is not None else "N/A"
        airdate = self.format_date_ddmmyyyy(meta.season_air_first_date or "")
        series_name = (
            meta.season_name
            if meta.season_name is not None
            else "Unknown Series"
        )
        return f"[center][b]Series Title:[/b] {series_name}[/center]\n[center][b]This series premiered on:[/b] {channel} on {airdate}[/center]\n\n"

    def _episode_list_block(self, meta: Meta) -> str:
        if not meta.episodes:
            return ""
        return f"[center][b]Episode List[/b]\n{self._build_episode_list(meta.episodes)}[/center]\n\n"

    def _build_episode_desc(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        parts = [
            self._logo_block(meta),
            self._episode_title_block(meta),
            self._overview_block(meta.episode_overview),
            self._broadcast_block(meta),
            self._external_links_block(meta),
            self._screenshots_block(meta, image_list),
        ]
        return "".join(part for part in parts if part)

    @staticmethod
    def _episode_title_block(meta: Meta) -> str:
        name = str(meta.episode_name or "").strip()
        return (
            f"[center][b]Episode Title:[/b] {name}[/center]\n\n"
            if name
            else ""
        )

    def _broadcast_block(self, meta: Meta) -> str:
        if not meta.episode_airdate:
            return ""
        channel = meta.networks if meta.networks is not None else "N/A"
        date = self.format_date_ddmmyyyy(meta.episode_airdate)
        return f"[center][b]Broadcast on:[/b] {channel} on {date}[/center]\n\n"

    def _build_fallback_desc(self, meta: Meta) -> str:
        """Build fallback description for other categories."""
        overview = meta.overview.strip()
        if overview:
            return f"[center]{overview}[/center]\n\n"
        return ""

    def _get_movie_release_info(self, meta: Meta) -> str:
        if not meta.release_dates:
            return str(meta.release_date or "")
        return "".join(self._release_date_entries(meta.release_dates))

    @classmethod
    def _release_date_entries(cls, payload: Any) -> list[str]:
        groups = (
            payload.get("results", []) if isinstance(payload, dict) else []
        )
        entries: list[str] = []
        for group in groups if isinstance(groups, list) else []:
            entries.extend(cls._country_release_entries(group))
        return entries

    @classmethod
    def _country_release_entries(cls, group: Any) -> list[str]:
        if not isinstance(group, dict):
            return []
        country = str(group.get("iso_3166_1", ""))
        releases = group.get("release_dates", [])
        values = releases if isinstance(releases, list) else []
        return [
            entry
            for release in values
            if (entry := cls._tv_release_entry(country, release))
        ]

    @staticmethod
    def _tv_release_entry(country: str, release: Any) -> str:
        if not isinstance(release, dict) or release.get("type") != 6:
            return ""
        channel = release.get("note") or "N/A Channel"
        date = str(release.get("release_date", ""))[:10]
        return f"[color=orange][size=15]{country} TV Release info [/size][/color]\n{date} on {channel}\n"

    def _build_episode_list(self, episodes: list[dict[str, Any]]) -> str:
        """Build formatted episode list."""
        parts: list[str] = []

        for ep in episodes:
            ep_num = ep.get("code", "")
            ep_title = ep.get("title", "").strip()
            ep_date = ep.get("airdate", "")
            ep_overview = ep.get("overview", "").strip()

            # Episode number and title
            parts.append(f"[b]{ep_num}[/b]")
            if ep_title:
                parts.append(f" - {ep_title}")
            if ep_date:
                formatted_date = self.format_date_ddmmyyyy(ep_date)
                parts.append(f" ({formatted_date})")
            parts.append("\n")

            # Overview
            if ep_overview:
                parts.append(f"{ep_overview}\n")

        return "".join(parts)

    def _add_screenshots(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        """Add screenshots section if requirements are met."""
        screens_count = meta.screens or 0
        required_count = self.config["TRACKERS"][self.tracker].get(
            "image_count", self.MIN_SCREENSHOTS_REQUIRED
        )

        if not image_list or screens_count < required_count:
            return ""

        parts = ["[b]Screenshots[/b]\n"]

        for img in image_list[:required_count]:
            web_url = img["web_url"]
            img_url = img["img_url"]
            parts.append(
                f"[url={web_url}][img={self.SCREENSHOT_THUMB_SIZE}]{img_url}[/img][/url] "
            )

        return "".join(parts)

    def _build_notes_section(self, base: str) -> str:
        """Build notes/extra info section."""
        return (
            f"[center][b]Notes / Extra Info[/b]\n{base.strip()}[/center]\n\n"
        )

    def _apply_bbcode_transforms(self, desc: str, comparison: bool) -> str:
        """Apply BBCode transformations."""
        bbcode = BBCODE()
        desc = bbcode.convert_pre_to_code(desc)
        desc = bbcode.convert_hide_to_spoiler(desc)

        if not comparison:
            desc = bbcode.convert_comparison_to_collapse(
                desc, self.COMPARISON_COLLAPSE_THRESHOLD
            )

        return desc

    def _normalize_tvc_formatting(self, desc: str) -> str:
        """Normalize whitespace for TVCHAOSUK (multi-block style)."""
        # Collapse any run of 3+ newlines into exactly 2 (preserve spacing between blocks)
        return re.sub(r"\n{3,}", "\n\n", desc)

    async def _write_description_file(
        self, filepath: str, content: str
    ) -> None:
        """Write description content to file asynchronously."""
        try:

            def _write():
                with Path(filepath).open("w", encoding="utf-8") as f:
                    f.write(content)

            await asyncio.to_thread(_write)
        except OSError as e:
            logger.warning(
                f"{self.tracker}: [yellow]Warning: Failed to write description file: {e}[/yellow]"
            )

    async def get_cat_id(self, genres: list[str]) -> str:
        """
        Determine TVCHAOSUK category ID based on genre list.

        Args:
            genres (list[str]): List of genre names (e.g. ["Drama", "Comedy"]).

        Returns:
            str: Category ID string from tv_type_map. Defaults to "holding bin" if no match.
        """
        # Note sections are based on Genre not type, source, resolution etc..
        # Uses tv_type_map dict for genre → category ID mapping
        if not genres:
            return self.tv_type_map["holding bin"]
        for g in genres:
            g = g.lower().replace(",", "").strip()
            if g and g in self.tv_type_map:
                return self.tv_type_map[g]

        # fallback to holding bin/misc id
        return self.tv_type_map["holding bin"]

    async def get_res_id(self, tv_pack: bool, resolution: str) -> str:
        if tv_pack:
            resolution_id = {
                "1080p": "HD1080p Pack",
                "1080i": "HD1080p Pack",
                "720p": "HD720p Pack",
                "576p": "SD Pack",
                "576i": "SD Pack",
                "540p": "SD Pack",
                "540i": "SD Pack",
                "480p": "SD Pack",
                "480i": "SD Pack",
            }.get(resolution, "SD")
        else:
            resolution_id = {
                "1080p": "HD1080p",
                "1080i": "HD1080p",
                "720p": "HD720p",
                "576p": "SD",
                "576i": "SD",
                "540p": "SD",
                "540": "SD",
                "480p": "SD",
                "480i": "SD",
            }.get(resolution, "SD")
        return resolution_id

    async def append_country_code(self, meta: Meta, name: str) -> str:
        """
        Append ISO country code suffix to release name based on origin_country_code.

        Args:
            meta (dict): Metadata containing 'origin_country_code' list.
            name (str): Base release name.

        Returns:
            str: Release name with appended country code (e.g. "Show Title [IRL]").
        """
        country_map = {
            "AT": "AUT",
            "AU": "AUS",
            "BE": "BEL",
            "CA": "CAN",
            "CH": "CHE",
            "CZ": "CZE",
            "DE": "GER",
            "DK": "DNK",
            "EE": "EST",
            "ES": "SPA",
            "FI": "FIN",
            "FR": "FRA",
            "IE": "IRL",
            "IS": "ISL",
            "IT": "ITA",
            "NL": "NLD",
            "NO": "NOR",
            "NZ": "NZL",
            "PL": "POL",
            "PT": "POR",
            "RU": "RUS",
            "SE": "SWE",
        }

        if meta.origin_country_code:
            for code in meta.origin_country_code:
                if code in country_map:
                    name += f" [{country_map[code]}]"
                    break  # append only the first match

        return name

    async def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """
        Async helper to read a text file safely.
        Uses a with-block to ensure the file handle is closed.
        """

        def _read():
            with Path(path).open(encoding=encoding) as f:
                return f.read()

        return await asyncio.to_thread(_read)

    async def upload(self, meta: Meta) -> bool | None:
        image_list = self._tracker_screenshots(meta)
        await self.common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )
        await self.get_tmdb_data(meta)
        media_info = await self._load_mediainfo_json(meta)
        category_id = await self._upload_category(meta, media_info)
        meta.language_checked = True
        resolution_id = await self.get_res_id(meta.tv_pack, meta.resolution)
        mediainfo_dump, bdinfo_dump = await self._technical_dumps(meta)
        description = await self.edit_desc(
            meta, self.tracker, self.signature, image_list
        )
        name = await self._upload_name(meta, category_id, media_info)
        if not self._confirm_upload_name(meta, name):
            return None
        data = self._upload_data(
            meta,
            name,
            description,
            mediainfo_dump,
            bdinfo_dump,
            category_id,
            resolution_id,
        )
        if meta.debug:
            return await self._debug_upload(meta, data)
        return await self._submit_upload(meta, data)

    def _tracker_screenshots(self, meta: Meta) -> list[dict[str, Any]]:
        raw = get_tracker_image_collection(meta, self.tracker, "screenshots")
        if isinstance(raw, tuple):
            raw = list(raw)
        if not isinstance(raw, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in raw
            if isinstance(item, dict)
        ]

    async def _load_mediainfo_json(self, meta: Meta) -> dict[str, Any]:
        path = release_temp_dir(meta.base_dir, meta.uuid) / "MediaInfo.json"
        try:
            content = await self.read_file(str(path))
            payload = json.loads(content)
            return (
                cast(dict[str, Any], payload)
                if isinstance(payload, dict)
                else {}
            )
        except (FileNotFoundError, json.JSONDecodeError) as error:
            logger.warning(
                f"{self.tracker}: [yellow]Warning: Could not load MediaInfo.json: {error}"
            )
            return {}

    async def _upload_category(
        self, meta: Meta, media_info: dict[str, Any]
    ) -> str:
        category_id = (
            await self.get_cat_id(meta.genres)
            if meta.category == "TV"
            else "44"
        )
        if self._foreign_original_language(meta.original_language):
            return self.tv_type_map["foreign"]
        if not meta.original_language and self._foreign_audio(media_info):
            return self.tv_type_map["foreign"]
        return category_id

    @staticmethod
    def _foreign_original_language(value: Any) -> bool:
        language = str(value or "")
        return (
            bool(language)
            and not language.startswith("en")
            and language not in {"ga", "gd", "cy"}
        )

    def _foreign_audio(self, media_info: dict[str, Any]) -> bool:
        languages = self.get_audio_languages(media_info)
        return bool(languages) and "English" not in languages

    async def _technical_dumps(self, meta: Meta) -> tuple[str, str]:
        if meta.bdinfo:
            return "", await self._temp_text(meta, "BD_SUMMARY_00.txt")
        return await self._temp_text(meta, "MEDIAINFO.txt"), ""

    @staticmethod
    async def _temp_text(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    async def _upload_name(
        self, meta: Meta, category_id: str, media_info: dict[str, Any]
    ) -> str:
        release_type = self._release_type(meta)
        name = self._base_upload_name(meta, release_type)
        name = self._localized_upload_name(meta, category_id, name)
        if not meta.is_disc:
            self.get_subs_info(meta, media_info)
        name = self._apply_codec_subtitle_markers(meta, name)
        return await self.append_country_code(meta, name)

    @staticmethod
    def _release_type(meta: Meta) -> str:
        path = str(meta.path).lower()
        if meta.type == "ENCODE" and any(
            marker in path for marker in ("bluray", "brrip", "bdrip")
        ):
            return "BRRip"
        return str(meta.type).replace("WEBDL", "WEB-DL")

    @classmethod
    def _base_upload_name(cls, meta: Meta, release_type: str) -> str:
        if meta.category == "MOVIE":
            return cls._movie_upload_name(meta, release_type)
        if meta.category == "TV":
            return cls._tv_upload_name(meta, release_type)
        raise ValueError(
            f"Unsupported category for TVCHAOSUK: {meta.category}"
        )

    @staticmethod
    def _movie_upload_name(meta: Meta, release_type: str) -> str:
        year = "" if meta.year is None else str(meta.year)
        return f"{meta.title} ({year}) [{meta.resolution} {release_type} {str(meta.video[-3:]).upper()}]"

    @classmethod
    def _tv_upload_name(cls, meta: Meta, release_type: str) -> str:
        year = cls._tv_year(meta)
        if meta.tv_pack:
            season_year = cls._season_year(meta, year)
            return f"{meta.title} - Series {meta.season_int} ({season_year}) [{meta.resolution} {release_type} {str(meta.video[-3:]).upper()}]"
        return cls._episode_upload_name(meta, release_type, year)

    @staticmethod
    def _tv_year(meta: Meta) -> str:
        value = meta.search_year if meta.search_year else meta.year
        if meta.no_year:
            return ""
        return "" if value is None else str(value)

    @staticmethod
    def _season_year(meta: Meta, fallback: str) -> str:
        first = str(meta.season_air_first_date or "")[:4]
        return first or fallback

    @classmethod
    def _episode_upload_name(
        cls, meta: Meta, release_type: str, year: str
    ) -> str:
        year_label = f" ({year})" if year else ""
        date_label = cls._episode_date_label(meta)
        return f"{meta.title}{year_label} {meta.season}{meta.episode}{date_label} [{meta.resolution} {release_type} {str(meta.video[-3:]).upper()}]"

    @classmethod
    def _episode_date_label(cls, meta: Meta) -> str:
        if not meta.episode_airdate:
            return ""
        return f" ({cls._format_date(meta.episode_airdate)})"

    @staticmethod
    def _format_date(value: str) -> str:
        try:
            return (
                datetime.strptime(value, "%Y-%m-%d")
                .replace(tzinfo=UTC)
                .strftime("%d-%m-%Y")
            )
        except ValueError, TypeError:
            return value

    @staticmethod
    def _localized_upload_name(meta: Meta, category_id: str, name: str) -> str:
        if category_id != TVChaosUK.tv_type_map["foreign"]:
            return name
        if not meta.original_title or meta.original_title == meta.title:
            return name
        return name.replace(
            str(meta.title), f"{meta.title} ({meta.original_title})"
        )

    @classmethod
    def _apply_codec_subtitle_markers(cls, meta: Meta, name: str) -> str:
        result = cls._append_hevc_marker(meta, name)
        result = cls._append_english_sub_marker(meta, result)
        return cls._append_sdh_marker(meta, result)

    @staticmethod
    def _append_hevc_marker(meta: Meta, name: str) -> str:
        return (
            name.replace("]", " HEVC]") if meta.video_codec == "HEVC" else name
        )

    @staticmethod
    def _append_english_sub_marker(meta: Meta, name: str) -> str:
        return name.replace("]", " SUBS]") if meta.eng_subs else name

    @staticmethod
    def _append_sdh_marker(meta: Meta, name: str) -> str:
        if not meta.sdh_subs:
            return name
        if meta.eng_subs:
            return name.replace(" SUBS]", " (ENG + SDH SUBS)]")
        return name.replace("]", " (SDH SUBS)]")

    @staticmethod
    def _confirm_upload_name(meta: Meta, name: str) -> bool:
        if meta.unattended:
            return True
        if cli_ui.ask_yes_no(
            f"Upload to TVCHAOSUK with the name {name}?", default=False
        ):
            return True
        new_name = cli_ui.ask_string("Please enter New Name:") or name
        return bool(
            cli_ui.ask_yes_no(
                f"Upload to TVCHAOSUK with the name {new_name}?", default=False
            )
        )

    def _upload_data(
        self,
        meta: Meta,
        name: str,
        description: str,
        mediainfo: str,
        bdinfo: str,
        category_id: str,
        resolution_id: str,
    ) -> dict[str, str | int]:
        data: dict[str, str | int] = {
            "name": name,
            "description": description,
            "mediainfo": mediainfo,
            "bdinfo": bdinfo,
            "category_id": category_id,
            "type": resolution_id,
            "tmdb": meta.tmdb or 0,
            "imdb": meta.imdb or 0,
            "tvdb": meta.tvdb_id or 0,
            "mal": meta.mal_id or 0,
            "igdb": 0,
            "anonymous": self._anonymous(meta),
            "stream": int(meta.stream),
            "sd": int(meta.sd),
            "keywords": ", ".join(meta.keywords),
            "personal_release": int(meta.personalrelease),
            "internal": 0,
            "featured": 0,
            "free": 0,
            "doubleup": 0,
            "sticky": 0,
        }
        self._apply_episode_numbers(data, meta)
        return data

    def _anonymous(self, meta: Meta) -> int:
        configured = bool(self._tracker_config().get("anon", False))
        return 0 if meta.anon == 0 and not configured else 1

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _announce_url(self) -> str | list[str]:
        return required_announce_url(
            self._tracker_config().get("announce_url"),
            self.tracker,
        )

    @staticmethod
    def _apply_episode_numbers(data: dict[str, str | int], meta: Meta) -> None:
        if meta.category != "TV":
            return
        data["season_number"] = (
            meta.season_int if meta.season_int is not None else "0"
        )
        data["episode_number"] = (
            meta.episode_int if meta.episode_int is not None else "0"
        )

    async def _submit_upload(
        self, meta: Meta, data: dict[str, str | int]
    ) -> bool:
        response: httpx.Response | None = None
        try:
            response = await self._upload_response(meta, data)
            return await self._handle_upload_response(meta, response)
        except httpx.TimeoutException:
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: Request timed out after 30 seconds"
            )
            return False
        except httpx.RequestError as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                self._request_error_message(error, response)
            )
            return False
        except Exception as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                self._unexpected_error_message(error, response)
            )
            return False

    async def _upload_response(
        self, meta: Meta, data: dict[str, str | int]
    ) -> httpx.Response:
        torrent_path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(torrent_path, "rb") as handle:
            torrent_bytes = await handle.read()
        files = {"torrent": (torrent_path.name, torrent_bytes)}
        params = {
            "api_token": str(self._tracker_config().get("api_key", "")).strip()
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(
                self.upload_url,
                files=files,
                data=data,
                headers={"User-Agent": "Mozilla/5.0"},
                params=params,
            )

    async def _handle_upload_response(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        if response.status_code != 200:
            meta.tracker_status[self.tracker]["status_message"] = (
                self._http_error_message(response)
            )
            return False
        payload = self._upload_json(response)
        meta.tracker_status[self.tracker]["status_message"] = payload
        torrent_id = self._uploaded_torrent_id(payload)
        meta.tracker_status[self.tracker]["torrent_id"] = torrent_id
        await self.common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            self._announce_url(),
            f"{self.base_url}/torrents/{torrent_id}",
        )
        return True

    @staticmethod
    def _http_error_message(response: httpx.Response) -> str:
        if response.status_code == 403:
            return "data error: Forbidden (403). This may indicate that you do not have upload permission."
        if response.status_code in {301, 302, 303, 307, 308}:
            return f"data error: Redirect ({response.status_code}). Please verify that your API key is valid."
        return f"data error: HTTP {response.status_code} - {response.text}"

    @staticmethod
    def _upload_json(response: httpx.Response) -> dict[str, Any]:
        payload = json.loads(response.text.split("\n", 1)[-1])
        if not isinstance(payload, dict):
            raise ValueError(
                "Invalid TVCHAOSUK response: JSON payload must be an object"
            )
        return cast(dict[str, Any], payload)

    @staticmethod
    def _uploaded_torrent_id(payload: dict[str, Any]) -> str:
        data = payload.get("data")
        if not isinstance(data, str):
            raise ValueError(
                f"Invalid TVCHAOSUK response: 'data' missing or not a string: {data}"
            )
        segments = [
            segment for segment in urlparse(data).path.split("/") if segment
        ]
        if not segments:
            raise ValueError(
                f"Invalid TVCHAOSUK response format: no path segments in {data}"
            )
        return segments[-1]

    @staticmethod
    def _request_error_message(
        error: httpx.RequestError, response: httpx.Response | None
    ) -> str:
        text = response.text if response is not None else "No response"
        return (
            f"data error: Unable to upload. Error: {error}.\nResponse: {text}"
        )

    @staticmethod
    def _unexpected_error_message(
        error: Exception, response: httpx.Response | None
    ) -> str:
        text = response.text if response is not None else "No response"
        return f"data error: It may have uploaded, go check. Error: {error}.\nResponse: {text}"

    async def _debug_upload(
        self, meta: Meta, data: dict[str, str | int]
    ) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    def get_audio_languages(self, mi: dict[str, Any]) -> list[str]:
        languages = {
            language
            for track in self._media_tracks(mi, "Audio")
            if (language := self._normalized_audio_language(track))
        }
        return sorted(languages)

    @classmethod
    def _media_tracks(
        cls, media_info: Any, track_type: str
    ) -> list[dict[str, Any]]:
        tracks = cls._all_media_tracks(media_info)
        return [track for track in tracks if track.get("@type") == track_type]

    @classmethod
    def _all_media_tracks(cls, media_info: Any) -> list[dict[str, Any]]:
        media = cls._media_mapping(media_info)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _media_mapping(media_info: Any) -> dict[str, Any]:
        if not isinstance(media_info, dict):
            return {}
        media = media_info.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in value
            if isinstance(track, dict)
        ]

    @classmethod
    def _normalized_audio_language(cls, track: dict[str, Any]) -> str:
        language = cls._audio_language_value(track)
        if not language:
            return ""
        if cls._is_english_audio(language):
            return "English"
        return language.title()

    @staticmethod
    def _audio_language_value(track: dict[str, Any]) -> str:
        for key in (
            "Language/String",
            "Language/String1",
            "Language/String2",
            "Language",
        ):
            value = track.get(key)
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _is_english_audio(language: str) -> bool:
        return language.lower() in {
            "en",
            "eng",
            "en-us",
            "en-gb",
            "en-ie",
            "en-au",
        }

    async def get_tmdb_data(self, meta: Meta) -> dict[str, Any]:
        meta.origin_country_code = self._origin_country_codes(meta)
        if meta.category == "MOVIE":
            self._log_existing_movie_tmdb(meta)
            return {}
        if meta.category != "TV":
            raise ValueError(
                f"Unsupported category for TVCHAOSUK: {meta.category}"
            )
        self._normalize_network(meta)
        if meta.tmdb is None:
            self._apply_missing_tmdb_dates(meta)
            return {}
        try:
            await self._populate_tv_metadata(meta)
        except (
            httpx.RequestError,
            requests.exceptions.RequestException,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
        ) as error:
            self._log_tmdb_error(meta, error)
            self._apply_missing_tmdb_dates(meta)
        return {}

    @classmethod
    def _origin_country_codes(cls, meta: Meta) -> list[str]:
        direct = cls._direct_origin_country_codes(meta.origin_country)
        if direct:
            return direct
        production = cls._production_country_codes(meta.production_countries)
        if production:
            return production
        return cls._company_origin_country(meta.production_companies)

    @staticmethod
    def _direct_origin_country_codes(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(code) for code in value]
        return [str(value)] if value else []

    @staticmethod
    def _production_country_codes(value: Any) -> list[str]:
        countries = value if isinstance(value, list) else []
        return [
            str(country["iso_3166_1"])
            for country in countries
            if isinstance(country, dict) and "iso_3166_1" in country
        ]

    @staticmethod
    def _company_origin_country(value: Any) -> list[str]:
        companies = value if isinstance(value, list) else []
        if not companies or not isinstance(companies[0], dict):
            return []
        country = str(companies[0].get("origin_country", ""))
        return [country] if country else []

    def _log_existing_movie_tmdb(self, meta: Meta) -> None:
        if meta.debug and meta.tmdb is not None:
            logger.info(
                f"{self.tracker}: [cyan]DEBUG: TMDb movie ID already resolved: {meta.tmdb}[/cyan]"
            )

    @staticmethod
    def _normalize_network(meta: Meta) -> None:
        networks = meta.networks
        if not isinstance(networks, list) or not networks:
            return
        first = networks[0]
        if isinstance(first, dict) and "name" in first:
            meta.networks = first["name"]

    @staticmethod
    def _apply_missing_tmdb_dates(meta: Meta) -> None:
        year = "" if meta.year is None else str(meta.year)
        meta.setdefault("season_air_first_date", f"{year}-N/A-N/A")
        meta.setdefault("first_air_date", f"{year}-N/A-N/A")

    async def _populate_tv_metadata(self, meta: Meta) -> None:
        if meta.tv_pack:
            season = await self._season_info(meta)
            self._apply_season_info(meta, season)
            return
        episode = await self._episode_info(meta)
        self._apply_episode_info(meta, episode)

    async def _episode_info(self, meta: Meta) -> dict[str, Any]:
        cached = (
            meta.tmdb_episode_data
            if isinstance(meta.tmdb_episode_data, dict)
            else {}
        )
        if cached:
            return cached
        tmdb_id = self._required_number(meta.tmdb, "TMDb ID")
        season = self._required_number(meta.season_int, "season")
        episode = self._required_number(meta.episode_int, "episode")
        return await self.tmdb_manager.get_episode_details(
            tmdb_id, season, episode
        )

    @staticmethod
    def _apply_episode_info(meta: Meta, episode: dict[str, Any]) -> None:
        meta.episode_airdate = episode.get("air_date", "")
        meta.episode_name = episode.get("name", "")
        meta.episode_overview = episode.get("overview", "")

    async def _season_info(self, meta: Meta) -> dict[str, Any]:
        cached = (
            meta.tmdb_season_data
            if isinstance(meta.tmdb_season_data, dict)
            else {}
        )
        if cached:
            return cached
        tmdb_id = self._required_number(meta.tmdb, "TMDb ID")
        season = self._required_number(meta.season_int, "season")
        return await self.tmdb_manager.get_season_details(tmdb_id, season)

    @staticmethod
    def _required_number(value: int | None, label: str) -> int:
        if value is None:
            raise ValueError(f"TVCHAOSUK: {label} is missing")
        return value

    @classmethod
    def _apply_season_info(cls, meta: Meta, season: dict[str, Any]) -> None:
        meta.season_air_first_date = season.get("air_date") or ""
        meta.season_name = season.get("name", f"Season {meta.season_int}")
        meta.episodes = cls._season_episode_rows(season.get("episodes", []))

    @classmethod
    def _season_episode_rows(cls, value: Any) -> list[dict[str, str]]:
        episodes = value if isinstance(value, list) else []
        return [
            row
            for episode in episodes
            if (row := cls._season_episode_row(episode)) is not None
        ]

    @staticmethod
    def _season_episode_row(episode: Any) -> dict[str, str] | None:
        if not isinstance(episode, dict):
            return None
        season_num = str(episode.get("season_number", 0)).zfill(2)
        episode_num = str(episode.get("episode_number", 0)).zfill(2)
        return {
            "code": f"S{season_num}E{episode_num}",
            "title": str(episode.get("name") or "").strip(),
            "airdate": str(episode.get("air_date") or ""),
            "overview": str(episode.get("overview") or "").strip(),
        }

    def _log_tmdb_error(self, meta: Meta, error: Exception) -> None:
        logger.info(
            f"{self.tracker}: [yellow]Expected error while fetching TV episode/season info: {error}"
        )
        logger.info(traceback.format_exc())
        logger.info(
            f"{self.tracker}: Unable to get episode information, Make sure episode {meta.season}{meta.episode} exists in TMDB.\n"
            f"https://www.themoviedb.org/tv/{meta.tmdb}/season/{meta.season_int}"
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        if self._forbidden_release(meta):
            logger.info(
                f"{self.tracker}: [bold red]No UHD, Discs, Remuxes or non-1080p HEVC allowed at TVCHAOSUK[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _forbidden_release(meta: Meta) -> bool:
        if meta.resolution == "2160p":
            return True
        if meta.is_disc or "REMUX" in str(meta.type):
            return True
        return meta.video_codec == "HEVC" and meta.resolution != "1080p"

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:  # noqa: ARG002
        # Search on TVCUK has been DISABLED due to issues, but we can still skip uploads based on criteria
        dupes: list[dict[str, Any]] = []

        logger.info(
            f"{self.tracker}: [red]Cannot search for dupes on TVCHAOSUK at this time.[/red]"
        )
        logger.info(
            f"{self.tracker}: [red]Please make sure you are not uploading duplicates."
        )
        await asyncio.sleep(2)

        return dupes

    async def edit_desc(
        self,
        meta: Meta,
        tracker: str,
        signature: str,
        image_list: list[dict[str, Any]],
        comparison: bool = False,
    ) -> str:
        base = await self._read_base_description(meta)
        path = self._ensure_desc_directory(meta, tracker)
        parts = self._description_parts(meta, image_list, base)
        description = self._finalize_description(
            "".join(parts), comparison, signature
        )
        await self._write_description_file(path, description)
        return description

    def _description_parts(
        self, meta: Meta, image_list: list[dict[str, Any]], base: str
    ) -> list[str]:
        parts: list[str] = []
        if meta.discs:
            parts.append(self._build_disc_info(meta.discs))
        parts.append(self._category_description(meta, image_list))
        notes = self._notes_if_relevant(base)
        if notes:
            parts.append(notes)
        return parts

    def _category_description(
        self, meta: Meta, image_list: list[dict[str, Any]]
    ) -> str:
        if meta.category == "MOVIE":
            return self._build_movie_desc(meta, image_list)
        if meta.category == "TV" and meta.tv_pack == 1:
            return self._build_tv_pack_desc(meta, image_list)
        if meta.category == "TV":
            return self._build_episode_desc(meta, image_list)
        return self._build_fallback_desc(meta)

    def _notes_if_relevant(self, base: str) -> str:
        stripped = base.strip()
        if not stripped or stripped.lower() == "ptp":
            return ""
        return self._build_notes_section(base)

    def _finalize_description(
        self, description: str, comparison: bool, signature: str
    ) -> str:
        result = self._apply_bbcode_transforms(description, comparison)
        result = re.sub(r"\[center\]\s+", "[center]", result)
        result = re.sub(r"\s+\[/center\]", "[/center]", result)
        result = self._normalize_tvc_formatting(result)
        if not result.strip():
            result = "[center][i]No description available[/i][/center]\n"
        if signature:
            result += f"\n{signature}\n"
        return result

    def get_links(self, meta: Meta) -> str:
        links = [
            link
            for key, url in self._metadata_links(meta)
            if (link := self._icon_link(key, url))
        ]
        if not links:
            return ""
        return "[b]External Info Sources:[/b]\n\n" + "".join(links)

    @classmethod
    def _metadata_links(cls, meta: Meta) -> list[tuple[str, str]]:
        return [
            ("imdb_75", cls._imdb_link(meta)),
            ("tmdb_75", cls._tmdb_link(meta)),
            ("tvdb_75", cls._tvdb_link(meta)),
            ("tvmaze_75", cls._tvmaze_link(meta)),
            ("mal_75", cls._mal_link(meta)),
        ]

    @staticmethod
    def _imdb_link(meta: Meta) -> str:
        if not meta.get("imdb_id", 0):
            return ""
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return str(imdb.get("imdb_url", ""))

    @staticmethod
    def _tmdb_link(meta: Meta) -> str:
        tmdb_id = meta.get("tmdb_id", 0)
        if not tmdb_id:
            return ""
        return f"https://www.themoviedb.org/{str(meta.category).lower()}/{tmdb_id}"

    @staticmethod
    def _tvdb_link(meta: Meta) -> str:
        tvdb_id = meta.get("tvdb_id", 0)
        return (
            f"https://www.thetvdb.com/?id={tvdb_id}&tab=series"
            if tvdb_id
            else ""
        )

    @staticmethod
    def _tvmaze_link(meta: Meta) -> str:
        tvmaze_id = meta.get("tvmaze_id", 0)
        return f"https://www.tvmaze.com/shows/{tvmaze_id}" if tvmaze_id else ""

    @staticmethod
    def _mal_link(meta: Meta) -> str:
        mal_id = meta.get("mal_id", 0)
        return f"https://myanimelist.net/anime/{mal_id}" if mal_id else ""

    def _icon_link(self, image_key: str, url: str) -> str:
        if not url:
            return ""
        images = self.config.get("IMAGES", {})
        image = (
            str(images.get(image_key, "")) if isinstance(images, dict) else ""
        )
        return f"[URL={url}][img]{image}[/img][/URL] " if image else ""

    # get subs function
    # used in naming conventions

    def get_subs_info(self, meta: Meta, mi: dict[str, Any]) -> None:
        tracks = self._media_tracks(mi, "Text")
        meta.has_subs = int(bool(tracks))
        meta.pop("eng_subs", None)
        meta.pop("sdh_subs", None)
        for track in tracks:
            self._apply_subtitle_flags(meta, track)

    @classmethod
    def _apply_subtitle_flags(cls, meta: Meta, track: dict[str, Any]) -> None:
        language = cls._subtitle_language(track)
        if language and cls._is_english_subtitle(language):
            meta.eng_subs = 1
        if "sdh" in str(track).lower():
            meta.sdh_subs = 1

    @staticmethod
    def _subtitle_language(track: dict[str, Any]) -> str:
        value = track.get("Language")
        return str(value).strip() if value else ""

    @staticmethod
    def _is_english_subtitle(language: str) -> bool:
        return language.lower() in {
            "en",
            "eng",
            "en-us",
            "en-gb",
            "en-ie",
            "en-au",
            "english",
        }
