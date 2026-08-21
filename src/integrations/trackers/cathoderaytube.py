# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import datetime
import platform
import re
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import parse_qs, urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.filesystem.temp_paths import artwork_dir
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.media.screenshot_capture import (
    download_artwork_from_meta,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import (
    CookieAuthUploader,
    CookieValidator,
)
from src.integrations.trackers.description_builder import DescriptionBuilder


class CathodeRayTube:
    """Cathode-Ray.Tube (CRT) is a Private Torrent Tracker for CLASSIC MOVIES / TV"""

    auth_type = "cookies"
    tracker = "CATHODERAYTUBE"
    display_name = "Cathode-Ray.Tube"
    source_flag = "CRT"
    base_url = "https://www.cathode-ray.tube"
    upload_url = f"{base_url}/upload.php"
    torrent_url = f"{base_url}/torrents.php"
    tracker_urls = ("signal.cathode-ray.tube",)
    supported_categories = ("MOVIE", "TV", "GAME")
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    auth_token: ClassVar[str] = ""
    approved_image_hosts = (
        "ptpimg",
        "catbox",
        "imgbb",
        "postimages",
        "freeimage",
        "imgbox",
    )
    image_host_policy = ImageHostPolicy(
        {
            "ptpimg.me": "ptpimg",
            "catbox.moe": "catbox",
            "ibb.co": "imgbb",
            "postimg.cc": "postimages",
            "iili.io": "freeimage",
            "imgbox.com": "imgbox",
        },
        approved_image_hosts,
    )

    category_map: ClassVar = {"MOVIE": "1", "TV": "2", "GAME": "13"}

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.rehost_images_manager = RehostImagesManager(config)
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": f"Upload-Assistant ({platform.system()} {platform.release()})"
            },
            timeout=60.0,
            follow_redirects=True,
        )

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar
            return True

        return False

    @staticmethod
    def _extract_auth_token(html: str) -> str:
        """Read the auth token exposed by CRT forms and authenticated page scripts."""
        auth_input = BeautifulSoup(html, "html.parser").select_one(
            'input[name="auth"]'
        )
        if auth_input and (auth := auth_input.get("value", "")):
            return str(auth)
        match = re.search(r"\bauthkey\s*=\s*['\"]([^'\"]+)['\"]", html)
        return match.group(1) if match else ""

    async def get_name(self, meta: Meta) -> str:
        """Format CRT titles according to its category-specific upload rules."""
        category = str(meta.category).upper()
        formatter = {
            "MOVIE": self._movie_name,
            "TV": self._tv_name,
            "GAME": self._game_name,
        }.get(category)
        return (
            str(meta.title or meta.name).strip()
            if formatter is None
            else formatter(meta)
        )

    @classmethod
    def _movie_name(cls, meta: Meta) -> str:
        return cls._join_name_parts(
            str(meta.title or meta.name).strip(),
            str(meta.aka or "").strip(),
            cls._year_label(meta.year),
            str(meta.edition or "").strip(),
        )

    @classmethod
    def _tv_name(cls, meta: Meta) -> str:
        name = str(meta.title or meta.name).strip()
        season_label = cls._season_label(str(meta.season or "").strip())
        suffix = cls._join_name_parts(
            cls._year_label(meta.year), str(meta.edition or "").strip()
        )
        if not season_label:
            return cls._join_name_parts(name, suffix)
        return cls._join_name_parts(name, "-", season_label, suffix)

    @classmethod
    def _game_name(cls, meta: Meta) -> str:
        return cls._join_name_parts(
            str(meta.title or meta.name).strip(),
            cls._year_label(meta.year),
            str(meta.platform or "").strip(),
        )

    @staticmethod
    def _year_label(value: Any) -> str:
        year = str(value or "").strip()
        return f"({year})" if year else ""

    @staticmethod
    def _join_name_parts(*parts: str) -> str:
        return " ".join(part for part in parts if part)

    @staticmethod
    def _season_label(season: str) -> str:
        """Convert Upload Assistant season tokens into CRT's human-readable labels."""
        if not season:
            return ""
        season_str = str(season).strip()
        if season_str.upper() == "S00":
            return "Specials"
        match = re.fullmatch(r"S(\d{1,2})", season_str, re.IGNORECASE)
        if match:
            return f"Season {int(match.group(1))}"
        if season_str.isdigit():
            return f"Season {int(season_str)}"
        return season_str

    def get_cover(self, meta: Meta) -> str:
        """Return a cover URL hosted by one of CRT's approved image hosts."""
        return next(
            (
                url
                for url in self._cover_candidates(meta)
                if self._is_approved_cover_url(url)
            ),
            "",
        )

    @classmethod
    def _cover_candidates(cls, meta: Meta) -> list[str]:
        candidates = cls._hosted_artwork_urls(meta.hosted_artwork)
        candidates.extend(cls._direct_cover_urls(meta))
        return [candidate for candidate in candidates if candidate]

    @staticmethod
    def _hosted_artwork_urls(value: Any) -> list[str]:
        artwork = value if isinstance(value, list) else []
        return [
            str(entry.get("raw_url", ""))
            for entry in artwork
            if isinstance(entry, dict)
        ]

    @staticmethod
    def _direct_cover_urls(meta: Meta) -> list[str]:
        return [
            str(getattr(meta, "rehosted_artwork_url", "") or ""),
            str(meta.artwork_url or ""),
        ]

    @staticmethod
    def _is_approved_cover_url(url: str) -> bool:
        """Check whether a cover URL belongs to a CRT-approved host."""
        hostname = (urlparse(url).hostname or "").lower()
        return any(
            CathodeRayTube._hostname_matches(hostname, approved)
            for approved in CathodeRayTube.image_host_policy.url_host_mapping
        )

    @staticmethod
    def _hostname_matches(hostname: str, approved: str) -> bool:
        return hostname == approved or hostname.endswith(f".{approved}")

    async def _host_cover(self, meta: Meta) -> str:
        """Host the release cover on an image host accepted by CRT."""
        existing_cover = self.get_cover(meta)
        if existing_cover or getattr(meta, "skip_imghost_upload", False):
            return existing_cover
        cover_path = await self._ensure_local_cover(meta)
        if cover_path is None:
            return ""
        indices = self._configured_cover_host_indices()
        if not indices:
            logger.warning(
                f"{self.tracker}: no approved image host is configured for the cover."
            )
            return ""
        return await self._upload_cover(meta, cover_path, indices)

    async def _ensure_local_cover(self, meta: Meta) -> Path | None:
        existing = self._existing_local_cover(meta)
        if existing is not None:
            return existing
        target = artwork_dir(meta.base_dir, meta.uuid) / "POSTER.png"
        await self._download_missing_cover(meta, target)
        if target.is_file():
            return target
        logger.warning(f"{self.tracker}: no local cover is available to host.")
        return None

    @staticmethod
    def _existing_local_cover(meta: Meta) -> Path | None:
        configured = Path(str(getattr(meta, "artwork_path", "") or ""))
        if configured.is_file():
            return configured
        target = artwork_dir(meta.base_dir, meta.uuid) / "POSTER.png"
        return target if target.is_file() else None

    async def _download_missing_cover(self, meta: Meta, target: Path) -> None:
        artwork_url = self._cover_source_url(meta)
        if artwork_url:
            await self._download_cover(meta, target, artwork_url)

    @staticmethod
    def _cover_source_url(meta: Meta) -> str:
        artwork_url = str(getattr(meta, "artwork_url", "") or "")
        if artwork_url:
            return artwork_url
        poster_path = str(getattr(meta, "tmdb_poster_path", "") or "")
        return (
            f"https://image.tmdb.org/t/p/w500{poster_path}"
            if poster_path
            else ""
        )

    @staticmethod
    async def _download_cover(
        meta: Meta, target: Path, artwork_url: str
    ) -> None:
        original_artwork_url = meta.artwork_url
        meta.artwork_url = artwork_url
        try:
            await download_artwork_from_meta(meta, str(target))
        finally:
            meta.artwork_url = original_artwork_url

    def _configured_cover_host_indices(self) -> list[int]:
        default_config = self.config.get("DEFAULT", {})
        indices: list[int] = []
        for key, value in default_config.items():
            index = self._approved_host_index(key, value)
            if index is not None:
                indices.append(index)
        return sorted(indices)

    def _approved_host_index(self, key: str, value: Any) -> int | None:
        match = re.fullmatch(r"img_host_(\d+)", key)
        if match is None or value not in self.approved_image_hosts:
            return None
        return int(match.group(1))

    async def _upload_cover(
        self, meta: Meta, cover_path: Path, indices: list[int]
    ) -> str:
        original_imghost = getattr(meta, "imghost", "")
        try:
            return await self._try_cover_hosts(meta, cover_path, indices)
        finally:
            meta.imghost = original_imghost

    async def _try_cover_hosts(
        self, meta: Meta, cover_path: Path, indices: list[int]
    ) -> str:
        for img_host_num in indices:
            raw_url = await self._upload_cover_to_host(
                meta, cover_path, img_host_num
            )
            if raw_url:
                meta.rehosted_artwork_url = raw_url
                return raw_url
        logger.warning(
            f"{self.tracker}: failed to host the cover on an approved image host."
        )
        return ""

    async def _upload_cover_to_host(
        self, meta: Meta, cover_path: Path, img_host_num: int
    ) -> str:
        (
            uploaded,
            _,
        ) = await self.rehost_images_manager.uploadscreens_manager.upload_screens(
            meta,
            1,
            img_host_num,
            0,
            1,
            [str(cover_path)],
            {},
            allowed_hosts=list(self.approved_image_hosts),
        )
        raw_url = uploaded[0].get("raw_url") if uploaded else ""
        if not isinstance(raw_url, str) or not self._is_approved_cover_url(
            raw_url
        ):
            return ""
        return raw_url

    @classmethod
    def _has_english(cls, values: list[str] | str | None) -> bool:
        return any(
            cls._is_english_language(value)
            for value in cls._language_values(values)
        )

    @staticmethod
    def _language_values(values: list[str] | str | None) -> list[str]:
        if isinstance(values, str):
            return [values]
        return values or []

    @staticmethod
    def _is_english_language(value: str) -> bool:
        return str(value).strip().lower() in {"en", "eng", "english"}

    def get_tags(self, meta: Meta) -> str:
        """Build common CRT tags from the available release metadata."""
        tags = self._base_tags(meta)
        self._append_year_tags(tags, meta.year)
        tags.extend(self._genre_tags(meta))
        if str(meta.category).upper() == "GAME":
            self._append_game_tags(tags, meta)
        else:
            self._append_video_tags(tags, meta)
        return ", ".join(dict.fromkeys(tags))

    @classmethod
    def _base_tags(cls, meta: Meta) -> list[str]:
        mapping = {"MOVIE": ["movies"], "TV": ["tv"], "GAME": ["games"]}
        return mapping.get(str(meta.category).upper(), []).copy()

    @staticmethod
    def _append_year_tags(tags: list[str], value: Any) -> None:
        year = str(value or "")
        if not re.fullmatch(r"\d{4}", year):
            return
        tags.extend((year, f"{year[:3]}0s"))

    @classmethod
    def _genre_tags(cls, meta: Meta) -> list[str]:
        mapping = cls._genre_tag_map()
        genres = meta.genres if meta.genres else [meta.genre]
        return [
            mapping[key]
            for genre in genres
            if (key := str(genre).lower().strip()) in mapping
        ]

    @staticmethod
    def _genre_tag_map() -> dict[str, str]:
        return {
            "action": "action",
            "adventure": "adventure",
            "animation": "animation",
            "comedy": "comedy",
            "crime": "crime",
            "documentary": "documentary",
            "drama": "drama",
            "family": "family",
            "fantasy": "fantasy",
            "history": "history",
            "horror": "horror",
            "music": "music",
            "musical": "musical",
            "mystery": "mystery",
            "romance": "romance",
            "science fiction": "scifi",
            "sci-fi": "scifi",
            "short": "short",
            "thriller": "thriller",
            "war": "war",
            "western": "western",
        }

    @classmethod
    def _append_game_tags(cls, tags: list[str], meta: Meta) -> None:
        tags.extend(cls._platform_tags(meta.platform))
        if meta.scene:
            tags.append("scene")

    @staticmethod
    def _platform_tags(platform: Any) -> list[str]:
        value = str(platform or "").lower()
        rules = (
            ("windows", ["pc", "windows"]),
            ("pc", ["pc"]),
            ("dos", ["dos"]),
            ("nintendo", ["nintendo"]),
            ("atari", ["atari"]),
        )
        return next((tags for marker, tags in rules if marker in value), [])

    @classmethod
    def _append_video_tags(cls, tags: list[str], meta: Meta) -> None:
        cls._append_resolution_tags(tags, meta)
        tags.extend(cls._release_tags(meta))
        cls._append_feature_tags(tags, meta)
        cls._append_codec_tags(tags, meta)
        cls._append_audio_tags(tags, meta)

    @staticmethod
    def _append_resolution_tags(tags: list[str], meta: Meta) -> None:
        resolution = str(meta.resolution or "").lower()
        if re.fullmatch(r"\d{3,4}[pi]", resolution):
            tags.append(resolution)
        if meta.sd:
            tags.append("sd")

    @staticmethod
    def _release_tags(meta: Meta) -> list[str]:
        release = f"{meta.type or ''} {meta.source or ''}".lower().replace(
            "-", ""
        )
        mapping = (
            ("webdl", "webdl"),
            ("webrip", "webrip"),
            ("bluray", "bluray"),
            ("dvdrip", "dvdrip"),
            ("remux", "remux"),
            ("dvd", "dvd"),
            ("encode", "encode"),
        )
        return [tag for value, tag in mapping if value in release]

    @staticmethod
    def _append_feature_tags(tags: list[str], meta: Meta) -> None:
        for enabled, tag in (
            (meta.is_disc, "full.disc"),
            (meta.three_d, "3d"),
            (meta.extras, "extras"),
            (meta.has_commentary, "commentary"),
        ):
            if enabled:
                tags.append(tag)

    @classmethod
    def _append_codec_tags(cls, tags: list[str], meta: Meta) -> None:
        codec = str(meta.video_codec or "").lower()
        if cls._contains_any(codec, ("avc", "h264", "x264")):
            tags.append("h.264")
        elif cls._contains_any(codec, ("hevc", "h265", "x265")):
            tags.append("h.265")

    @staticmethod
    def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
        return any(needle in value for needle in needles)

    @classmethod
    def _append_audio_tags(cls, tags: list[str], meta: Meta) -> None:
        tags.extend(cls._audio_codec_tags(meta.audio))
        cls._append_channel_tags(tags, meta)
        if cls._has_english(meta.audio_languages):
            tags.append("english.audio")
        if cls._has_english(meta.subtitle_languages):
            tags.append("english.sub")

    @classmethod
    def _audio_codec_tags(cls, audio_value: Any) -> list[str]:
        tag = cls._audio_codec_tag(str(audio_value or "").lower())
        return [tag] if tag else []

    @staticmethod
    def _audio_codec_tag(audio: str) -> str:
        rules = (
            (r"ddp|dd\+", "ddp"),
            (r"ac3|ac-3", "ac3"),
            (r"dolby digital|\bdd\b", "dd"),
            (r"aac", "aac"),
            (r"flac", "flac"),
        )
        for pattern, tag in rules:
            if re.search(pattern, audio):
                return tag
        return ""

    @classmethod
    def _append_channel_tags(cls, tags: list[str], meta: Meta) -> None:
        audio = str(meta.audio or "").lower()
        if cls._channel_present(
            audio, meta.channels, "2.0", ("stereo", "2.0")
        ):
            tags.append("stereo")
        if cls._channel_present(audio, meta.channels, "5.1", ("5.1",)):
            tags.append("5.1")

    @staticmethod
    def _channel_present(
        audio: str, channels: Any, expected: str, aliases: tuple[str, ...]
    ) -> bool:
        return str(channels) == expected or any(
            alias in audio for alias in aliases
        )

    @classmethod
    def _metadata_links(cls, meta: Meta) -> str:
        """Return the relevant canonical metadata links for CRT's info section."""
        links = cls._metadata_link_values(meta)
        return "\n".join(dict.fromkeys(link for link in links if link))

    @classmethod
    def _metadata_link_values(cls, meta: Meta) -> list[str]:
        return [
            cls._imdb_link(meta),
            cls._tmdb_link(meta),
            cls._tvdb_link(meta),
            cls._steam_link(meta),
        ]

    @staticmethod
    def _imdb_link(meta: Meta) -> str:
        return (
            f"https://www.imdb.com/title/{meta.imdb_tt}/"
            if meta.imdb_tt
            else ""
        )

    @staticmethod
    def _tmdb_link(meta: Meta) -> str:
        tmdb_id = meta.tmdb or meta.tmdb_id
        if not tmdb_id:
            return ""
        tmdb_type = "tv" if meta.category == "TV" else "movie"
        return f"https://www.themoviedb.org/{tmdb_type}/{tmdb_id}"

    @staticmethod
    def _tvdb_link(meta: Meta) -> str:
        return (
            f"https://thetvdb.com/?tab=series&id={meta.tvdb}"
            if meta.category == "TV" and meta.tvdb
            else ""
        )

    @staticmethod
    def _steam_link(meta: Meta) -> str:
        return (
            str(meta.steam_url)
            if meta.category == "GAME" and meta.steam_url
            else ""
        )

    def _valid_screenshot_images(self, meta: Meta) -> list[dict[str, Any]]:
        """Return CRT image records with usable raw URLs in publication order."""
        images: list[dict[str, Any]] = []
        for collection in self._screenshot_collections(meta):
            images.extend(self._valid_images(collection))
        return images

    def _screenshot_collections(self, meta: Meta) -> tuple[Any, ...]:
        return (
            get_tracker_image_collection(meta, self.tracker, "menu_images"),
            get_tracker_image_collection(meta, self.tracker, "screenshots"),
            get_tracker_image_collection(
                meta, self.tracker, "spectrograms_images"
            ),
            get_tracker_image_collection(
                meta, self.tracker, "dynamic_hdr_plot_images"
            ),
        )

    @staticmethod
    def _valid_images(collection: Any) -> list[dict[str, Any]]:
        if not isinstance(collection, list):
            return []
        return [
            cast(dict[str, Any], image)
            for image in collection
            if CathodeRayTube._image_has_raw_url(image)
        ]

    @staticmethod
    def _image_has_raw_url(image: Any) -> bool:
        return (
            isinstance(image, dict)
            and isinstance(image.get("raw_url"), str)
            and bool(image["raw_url"])
        )

    async def generate_description(self, meta: Meta) -> str:
        """Render CRT's category-specific upload template from prepared metadata."""
        builder = DescriptionBuilder(self.tracker, self.config)
        sections = await self._description_sections(meta, builder)
        description = "\n".join(part for part in sections if part.strip())
        if meta.debug:
            await self._write_debug_description(meta, description)
        return description

    async def _description_sections(
        self, meta: Meta, builder: DescriptionBuilder
    ) -> list[str]:
        sections = self._text_description_sections(
            meta, await builder.get_user_description(meta)
        )
        if str(meta.category).upper() in {"MOVIE", "TV"}:
            media_section = await self._media_description_section(
                meta, builder
            )
            if media_section:
                sections.append(media_section)
        sections.append(
            f"\n[align=right][url=https://github.com/wastaken7/Upload-Assistant][size=1]{meta.ua_signature}[/size][/url][/align]"
        )
        return sections

    def _text_description_sections(
        self, meta: Meta, user_description: str
    ) -> list[str]:
        sections: list[str] = []
        values = (
            ("info", self._metadata_links(meta)),
            ("plot", self._overview_text(meta)),
            ("notes", self._notes_text(meta, user_description)),
            ("screens", self._screenshots_text(meta)),
        )
        for tag, value in values:
            self._append_wrapped_section(sections, tag, value)
        return sections

    @staticmethod
    def _overview_text(meta: Meta) -> str:
        return str(meta.overview or meta.overview_meta or "")

    @staticmethod
    def _notes_text(meta: Meta, user_description: str) -> str:
        return "\n\n".join(
            part
            for part in (str(meta.description or "").strip(), user_description)
            if part
        )

    def _screenshots_text(self, meta: Meta) -> str:
        return "\n".join(
            str(image["raw_url"])
            for image in self._valid_screenshot_images(meta)
        )

    @staticmethod
    def _append_wrapped_section(
        sections: list[str], tag: str, value: str
    ) -> None:
        if value:
            sections.append(f"[{tag}]\n{value}\n[/{tag}]")

    @staticmethod
    async def _media_description_section(
        meta: Meta, builder: DescriptionBuilder
    ) -> str:
        media_info = await builder.get_bdinfo_section(
            meta
        ) or await builder.get_mediainfo_section(meta)
        return (
            f"[details]\n[mediainfo]\n{media_info}\n[/mediainfo]\n[/details]"
            if media_info
            else ""
        )

    async def _write_debug_description(
        self, meta: Meta, description: str
    ) -> None:
        desc_file = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        desc_file.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(
            f"DEBUG: Saving final description to [yellow]{desc_file}[/yellow]"
        )
        async with aiofiles.open(
            desc_file, "w", encoding="utf-8"
        ) as description_file:
            await description_file.write(description)

    async def get_upload_data(self, meta: Meta, auth: str) -> dict[str, str]:
        """Build CRT form fields, including its category-specific description template."""
        category = self.category_map.get(str(meta.category).upper())
        if not category:
            raise ValueError(
                f"Unsupported Cathode-Ray category: {meta.category}"
            )
        return {
            "submit": "true",
            "auth": auth,
            "category": category,
            "MAX_FILE_SIZE": "2097152",
            "title": await self.get_name(meta),
            "taglist": self.get_tags(meta),
            "image": self.get_cover(meta),
            "desc": await self.generate_description(meta),
            "anonymous": "1"
            if (
                meta.anon
                or self.config.get("TRACKERS", {})
                .get(self.tracker, {})
                .get("anon", False)
            )
            else "0",
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        """Enforce Cathode-Ray.Tube upload rules and guidelines."""
        category = str(meta.category).upper()
        if not self._content_policy_passes(meta, category):
            return False
        if not self._archive_disc_policy_passes(meta, category):
            return False
        if not self._video_policy_passes(meta, category):
            return False
        return self._age_policy_passes(meta, category)

    def _content_policy_passes(self, meta: Meta, category: str) -> bool:
        if self._adult_content(meta):
            logger.warning(
                f"{self.tracker}: [red]Explicit pornography is forbidden.[/red]"
            )
            return False
        if not self._forbidden_tv_genre(meta, category):
            return True
        logger.warning(
            f"{self.tracker}: [red]Sports and News broadcasts are forbidden in the TV category (must be in WOC).[/red]"
        )
        return False

    @staticmethod
    def _adult_content(meta: Meta) -> bool:
        return bool(meta.adult_media) or bool(meta.tmdb_adult_media)

    @staticmethod
    def _forbidden_tv_genre(meta: Meta, category: str) -> bool:
        if category != "TV":
            return False
        values = meta.genres if meta.genres else [meta.genre]
        genres = {str(value).lower().strip() for value in values if value}
        return bool(genres.intersection({"sports", "news"}))

    def _archive_disc_policy_passes(self, meta: Meta, category: str) -> bool:
        if category == "GAME":
            return True
        archive = self._archive_file(meta.filelist)
        if archive:
            logger.warning(
                f"{self.tracker}: [red]Archives are not allowed outside Games: {archive}[/red]"
            )
            return False
        if self._invalid_iso(meta):
            logger.warning(
                f"{self.tracker}: [red]ISO disc images are not allowed outside 3D Blu-ray on CRT.[/red]"
            )
            return False
        return True

    @staticmethod
    def _archive_file(filelist: Any) -> str:
        values = filelist if isinstance(filelist, (list, tuple, set)) else []
        archives = {".zip", ".rar", ".7z"}
        return next(
            (
                Path(str(item)).name
                for item in values
                if Path(str(item)).suffix.lower() in archives
            ),
            "",
        )

    @classmethod
    def _invalid_iso(cls, meta: Meta) -> bool:
        if meta.three_d:
            return False
        return (
            cls._has_iso_file(meta.filelist)
            or str(meta.is_disc or "").upper() == "ISO"
        )

    @staticmethod
    def _has_iso_file(filelist: Any) -> bool:
        values = filelist if isinstance(filelist, (list, tuple, set)) else []
        return any(Path(str(item)).suffix.lower() == ".iso" for item in values)

    def _video_policy_passes(self, meta: Meta, category: str) -> bool:
        if category not in {"MOVIE", "TV"}:
            return True
        if not self._language_policy_passes(meta):
            return False
        if not self._screenshot_policy_passes(meta):
            return False
        if getattr(meta, "valid_mi", None) is False:
            logger.warning(
                f"{self.tracker}: [red]Invalid or missing MediaInfo/BDInfo data.[/red]"
            )
            return False
        return True

    def _language_policy_passes(self, meta: Meta) -> bool:
        if self._has_english(meta.audio_languages) or self._has_english(
            meta.subtitle_languages
        ):
            return True
        logger.warning(
            f"{self.tracker}: [red]CRT requires English audio or English subtitles.[/red]"
        )
        return False

    def _screenshot_policy_passes(self, meta: Meta) -> bool:
        count = self._screenshot_count(meta)
        if count < 6:
            logger.warning(
                f"{self.tracker}: [red]CRT requires at least 6 screenshots for video content (found {count}).[/red]"
            )
            return False
        if count > 6 and count % 3 != 0:
            logger.warning(
                f"{self.tracker}: [yellow]CRT guidelines state screenshot count above 6 should be in multiples of 3 (found {count}).[/yellow]"
            )
        return True

    def _screenshot_count(self, meta: Meta) -> int:
        count = len(self._valid_screenshot_images(meta))
        if count:
            return count
        return self._safe_screen_count(getattr(meta, "screens", 0))

    @staticmethod
    def _safe_screen_count(value: Any) -> int:
        try:
            return int(value or 0)
        except ValueError, TypeError, OverflowError:
            return 0

    def _age_policy_passes(self, meta: Meta, category: str) -> bool:
        if self._age_exempt(meta, category):
            return True
        cutoff = self._ten_year_cutoff()
        date_value = self._release_age_date(meta, category)
        if date_value is not None:
            return self._date_is_old_enough(date_value, cutoff)
        return self._year_is_old_enough(meta.year)

    @staticmethod
    def _age_exempt(meta: Meta, category: str) -> bool:
        return bool(
            meta.edition
            or (category == "GAME" and getattr(meta, "extras", False))
        )

    @staticmethod
    def _ten_year_cutoff() -> datetime.date:
        today = datetime.datetime.now(datetime.UTC).date()
        return today.replace(year=today.year - 10)

    @classmethod
    def _release_age_date(
        cls, meta: Meta, category: str
    ) -> datetime.date | None:
        raw = cls._raw_release_date(meta, category)
        if not raw:
            return None
        try:
            return datetime.date.fromisoformat(str(raw).strip()[:10])
        except ValueError, TypeError:
            return None

    @staticmethod
    def _raw_release_date(meta: Meta, category: str) -> Any:
        if category == "MOVIE":
            return getattr(meta, "release_date", None)
        if category == "TV":
            return getattr(meta, "last_air_date", None) or getattr(
                meta, "release_date", None
            )
        return None

    def _date_is_old_enough(
        self, release_date: datetime.date, cutoff: datetime.date
    ) -> bool:
        if release_date <= cutoff:
            return True
        logger.warning(
            f"{self.tracker}: [red]Content must be at least 10 years old relative to current date (Release/Air date: {release_date}).[/red]"
        )
        return False

    def _year_is_old_enough(self, value: Any) -> bool:
        year_str = str(value or "").strip()
        if not year_str.isdigit():
            return True
        year = int(year_str)
        current_year = datetime.datetime.now(datetime.UTC).year
        if current_year - year >= 10:
            return True
        logger.warning(
            f"{self.tracker}: [red]Content must be at least 10 years old relative to current date (Release year: {year}).[/red]"
        )
        return False

    def get_search_params(self, meta: Meta) -> dict[str, str]:
        """Build CRT's advanced-search query."""
        category = self.category_map.get(str(meta.category).upper())
        if not category:
            raise ValueError(
                f"Unsupported Cathode-Ray category: {meta.category}"
            )

        return {
            "action": "advanced",
            f"filter_cat[{category}]": "1",
            "title": meta.title,
        }

    @staticmethod
    def get_imdb_search_params(meta: Meta) -> dict[str, str]:
        """Build CRT's standalone IMDb search without title or category filters."""
        return {"action": "advanced", "searchtext": meta.imdb_tt}

    @classmethod
    def _content_name(cls, html: str) -> str:
        """Extract the torrent's top-level directory or sole file name."""
        file_table = BeautifulSoup(html, "html.parser").select_one(
            "div[id^='files_'] table"
        )
        if file_table is None:
            return ""
        directory = cls._directory_name(file_table)
        return directory or cls._first_file_name(file_table)

    @staticmethod
    def _directory_name(file_table: Any) -> str:
        directory = file_table.select_one("tr.smallhead td")
        if directory is None:
            return ""
        return directory.get_text(" ", strip=True).strip("/")

    @staticmethod
    def _first_file_name(file_table: Any) -> str:
        for row in file_table.select("tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue
            name = cells[0].get_text(" ", strip=True)
            if name != "File Name":
                return name
        return ""

    @staticmethod
    def _bd_info(html: str) -> str:
        """Extract CRT's plain-text BDInfo block from a torrent details page."""
        details = BeautifulSoup(html, "html.parser").select_one(
            "div.section-details"
        )
        details = details.get_text("\n", strip=True) if details else ""
        if (
            "disc title:" not in details.lower()
            or "disc size:" not in details.lower()
        ):
            return ""
        return details

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        """Search CRT's advanced form for existing torrents matching the release metadata."""
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if not cookie_jar:
            return []
        self.session.cookies = cast(Any, cookie_jar)
        results: list[dict[str, str]] = []
        seen_links: set[str] = set()
        for params in self._search_variants(meta):
            response = await self._search_response(params)
            self._update_auth_token(response)
            await self._append_search_rows(meta, response, results, seen_links)
        return results

    def _search_variants(self, meta: Meta) -> list[dict[str, str]]:
        searches = [self.get_search_params(meta)]
        if meta.imdb_tt:
            searches.append(self.get_imdb_search_params(meta))
        return searches

    async def _search_response(self, params: dict[str, str]) -> httpx.Response:
        response = await self.session.get(
            f"{self.base_url}/torrents.php", params=params
        )
        response.raise_for_status()
        if response.status_code != 200 or "login.php" in str(response.url):
            raise RuntimeError(
                f"{self.tracker}: [yellow]Could not perform duplicate search; cookies may be expired.[/yellow]"
            )
        return response

    def _update_auth_token(self, response: httpx.Response) -> None:
        auth = self._extract_auth_token(response.text)
        if not auth:
            raise RuntimeError(
                f"{self.tracker}: [yellow]Advanced-search response did not contain an auth token.[/yellow]"
            )
        CathodeRayTube.auth_token = auth

    async def _append_search_rows(
        self,
        meta: Meta,
        response: httpx.Response,
        results: list[dict[str, str]],
        seen_links: set[str],
    ) -> None:
        for row in BeautifulSoup(response.text, "html.parser").select(
            "table#torrent_table tr.torrent"
        ):
            entry = await self._search_row_entry(
                meta, response, row, seen_links
            )
            if entry is not None:
                results.append(entry)

    async def _search_row_entry(
        self,
        meta: Meta,
        response: httpx.Response,
        row: Any,
        seen_links: set[str],
    ) -> dict[str, str] | None:
        links = self._row_links(response, row)
        if links is None:
            return None
        link, download = links
        if self._seen_link(link, seen_links):
            return None
        detail_response = await self._detail_response(link)
        entry = self._detail_entry(row, link, download, detail_response)
        if entry is None:
            return None
        self._apply_bd_info(entry, meta, detail_response.text)
        return entry

    @staticmethod
    def _seen_link(link: str, seen_links: set[str]) -> bool:
        if link in seen_links:
            return True
        seen_links.add(link)
        return False

    async def _detail_response(self, link: str) -> httpx.Response:
        response = await self.session.get(link)
        response.raise_for_status()
        return response

    def _detail_entry(
        self, row: Any, link: str, download: str, response: httpx.Response
    ) -> dict[str, str] | None:
        name = self._content_name(response.text)
        if not name:
            return None
        return {
            "name": name,
            "size": self._row_size(row),
            "link": link,
            "download": download,
        }

    def _apply_bd_info(
        self, entry: dict[str, str], meta: Meta, html: str
    ) -> None:
        if meta.is_disc != "BDMV":
            return
        bd_info = self._bd_info(html)
        if bd_info:
            entry["bd_info"] = bd_info

    @staticmethod
    def _row_links(
        response: httpx.Response, row: Any
    ) -> tuple[str, str] | None:
        download_link = row.select_one('a[href*="action=download"]')
        title = row.select_one(
            'a[href^="/torrents.php?id="], a[href^="torrents.php?id="]'
        )
        if download_link is None or title is None:
            return None
        href = str(title.get("href", ""))
        base = httpx.URL(str(response.url))
        download_href = str(download_link.get("href", ""))
        return str(base.join(href)), str(
            base.join(download_href)
        ) if download_href else ""

    @staticmethod
    def _row_size(row: Any) -> str:
        cells = row.select("td.nobr")
        return cells[-1].get_text(" ", strip=True) if cells else ""

    @classmethod
    def _uploaded_torrent_url(cls, response: httpx.Response) -> str:
        url = str(response.url)
        if cls._is_torrent_url(url):
            return url
        return cls._linked_torrent_url(response.text, url)

    @staticmethod
    def _is_torrent_url(url: str) -> bool:
        return re.search(r"/torrents\.php\?(?:[^#]*&)?id=\d+", url) is not None

    @staticmethod
    def _linked_torrent_url(html: str, base_url: str) -> str:
        for link in BeautifulSoup(html, "html.parser").select(
            'a[href*="torrents.php?id="]'
        ):
            href = str(link.get("href", ""))
            if href:
                return str(httpx.URL(base_url).join(href))
        return ""

    @classmethod
    def _log_upload_url(cls, html: str, torrent_name: str) -> str:
        """Extract the newest matching uploaded torrent from CRT's site log."""
        soup = BeautifulSoup(html, "html.parser")
        return next(
            (
                url
                for row in soup.select("tr")
                if (url := cls._log_row_url(row, torrent_name))
            ),
            "",
        )

    @classmethod
    def _log_row_url(cls, row: Any, torrent_name: str) -> str:
        row_text = row.get_text(" ", strip=True)
        if (
            torrent_name not in row_text
            or "was uploaded" not in row_text.lower()
        ):
            return ""
        return next(
            (
                url
                for link in row.select("a[href]")
                if (url := cls._log_link_url(link))
            ),
            "",
        )

    @classmethod
    def _log_link_url(cls, link: Any) -> str:
        href = str(link.get("href", ""))
        if "details.php" not in href and "torrents.php" not in href:
            return ""
        torrent_id = parse_qs(urlparse(href).query).get("id", [""])[0]
        return (
            f"{cls.base_url}/torrents.php?id={torrent_id}"
            if torrent_id.isdigit()
            else ""
        )

    async def _find_log_upload(self, meta: Meta) -> str:
        """Find the uploaded torrent in CRT's authenticated site log."""
        try:
            response = await self.session.get(f"{self.base_url}/log.php")
            response.raise_for_status()
            return self._log_upload_url(
                response.text, await self.get_name(meta)
            )
        except httpx.HTTPError as error:
            logger.warning(
                f"{self.tracker}: could not verify upload in site log: {error}"
            )
            return ""

    async def upload(self, meta: Meta) -> bool:
        await self._load_upload_cookies(meta)
        await self._host_cover(meta)
        auth = CathodeRayTube.auth_token
        if not auth:
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: Failed to load authenticated upload form."
            )
            return False
        if not await self._submit_upload(meta, auth):
            return False
        return await self._finalize_upload(meta)

    async def _load_upload_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar

    async def _submit_upload(self, meta: Meta, auth: str) -> bool:
        return await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=f"{self.base_url}/torrents.php?id=",
            data=await self.get_upload_data(meta, auth),
            torrent_field_name="file_input",
            upload_cookies=self.session.cookies,
            upload_url=self.upload_url,
            id_pattern=r"torrents\.php\?(?:[^#]*&)?id=(\d+)",
            success_status_code="500",
        )

    async def _finalize_upload(self, meta: Meta) -> bool:
        await asyncio.sleep(5)
        torrent_url = await self._find_log_upload(meta)
        if not torrent_url:
            logger.warning(
                f"{self.tracker}: upload was accepted, but no matching entry was found in site log."
            )
            return True
        torrent_id = parse_qs(urlparse(torrent_url).query).get("id", [""])[0]
        if not torrent_id:
            logger.warning(
                f"{self.tracker}: site log returned an invalid torrent URL: {torrent_url}"
            )
            return True
        await self._record_uploaded_torrent(meta, torrent_id, torrent_url)
        return True

    async def _record_uploaded_torrent(
        self, meta: Meta, torrent_id: str, torrent_url: str
    ) -> None:
        meta.tracker_status[self.tracker]["torrent_id"] = torrent_id
        announce_url = (
            self.config.get("TRACKERS", {})
            .get(self.tracker, {})
            .get("announce_url", "")
        )
        await self.common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            str(announce_url),
            torrent_url,
        )
