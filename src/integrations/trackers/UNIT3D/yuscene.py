# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import html
import re
from pathlib import Path
from typing import Any, cast

import httpx

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class YUSCENE(UNIT3D):
    """
    YU-SCENE is a Private Tracker for MOVIES / TV
    """

    tracker = "YUSCENE"
    display_name = "YUSCENE"
    allows_bloated_audio = True
    base_url = "https://yu-scene.net"
    banned_groups = (
        "ADDICTION",
        "B3LLUM",
        "BANDOLEROS",
        "BigEasy",
        "CINEMAXIS",
        "d3g",
        "D3US",
        "DUMMESCHWEDEN",
        "FGT",
        "GRANiTEN",
        "KiNGDOM",
        "Lama",
        "MeGusta",
        "MezRips",
        "mHD",
        "mRS",
        "msd",
        "NeXus",
        "NhaNc3",
        "nHD",
        "NorTekst",
        "NORViNE",
        "PANDEMONiUM",
        "PiTBULL",
        "Radarr",
        "RAPiDCOWS",
        "RARBG",
        "RCDiVX",
        "RDN",
        "ROCKETRACCOON",
        "SANTi",
        "SHOWTiME",
        "SOOSi",
        "SUXWIC",
        "TOXVIO",
        "TWA",
        "VXT",
        "Will1869",
        "x0r",
        "XS",
        "YIFY",
        "YOLAND",
        "YTS",
        "ZKBL",
        "ZmN",
        "ZMNT",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://yu-scene.net",)

    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".rar",
            ".r00",
            ".r01",
            ".r02",
            ".r03",
            ".r04",
            ".r05",
            ".r06",
            ".r07",
            ".r08",
            ".r09",
        }
    )
    _EXTRA_FILE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".nfo",
            ".srt",
            ".sub",
            ".ssa",
            ".ass",
            ".vtt",
            ".idx",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
        }
    )
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".m4v",
            ".mpg",
            ".mpeg",
            ".m2ts",
            ".ts",
            ".wmv",
            ".flv",
        }
    )
    _TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {
            "ended",
            "canceled",
            "cancelled",
            "finished",
            "completed",
            "completed (ended)",
            "ended (ended)",
        }
    )
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {
            "returning series",
            "in production",
            "ongoing",
            "planned",
            "pilot",
            "in development",
        }
    )

    _TRACKER_KEYWORDS: frozenset[str] = frozenset(
        {
            "yify",
            "yts",
            "rarbg",
            "tpb",
            "thepiratebay",
            "1337x",
            "kat",
            "nyaa",
            "eztv",
            "rutor",
            "torrentz",
            "nyaa.si",
            "kickasstorrents",
            "limetorrents",
            "rutracker",
            "torrentdownloads",
        }
    )

    _TRACKER_DOMAINS: frozenset[str] = frozenset(
        {
            "piratebay.org",
            "tpb.party",
            "thepiratebay.org",
            "yts.mx",
            "yts.rs",
            "rarbg.to",
            "1337x.to",
            "kickasstorrents.to",
            "kickasstorrents.info",
            "katcr.co",
            "katcr.to",
            "nyaa.si",
            "nyaa.land",
            "rutor.info",
            "limetorrents.cc",
            "torrentz2.eu",
            "rutracker.net",
            "eztv.re",
            "torrentdownloads.me",
        }
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="YUSCENE")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _is_path_like_file(filename: Any) -> bool:
        return str(filename).strip() != ""

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        logger.info(f"{self.tracker}: [yellow]{message}[/yellow]")
        if meta.unattended:
            confirmed = bool(meta.unattended_confirm)
            if not confirmed:
                logger.info(f"{self.tracker}: [bold red]{message} Skipping upload.[/bold red]")
            return confirmed
        return await self.common.prompt_user_for_confirmation("Do you want to continue anyway?", meta)

    @staticmethod
    def _contains_archive_file(files: list[Any]) -> str:
        for item in files:
            filename = str(item).lower()
            path = Path(filename)
            if path.suffix.lower() in YUSCENE._ARCHIVE_EXTENSIONS:
                return path.name
        return ""

    @staticmethod
    def _contains_video_extras(files: list[Any]) -> str:
        for item in files:
            filename = str(item).lower()
            path = Path(filename)
            if path.suffix.lower() in YUSCENE._EXTRA_FILE_EXTENSIONS:
                return path.name
            if "sample" in path.name and path.suffix.lower() in YUSCENE._VIDEO_EXTENSIONS:
                return path.name
        return ""

    @classmethod
    def _contains_other_tracker_mention(cls, value: str) -> str:
        lowered = str(value or "").lower()
        url_pattern = re.compile(r"(?:https?:)?//[^\s]+")
        forbidden = cls._first_forbidden_tracker_url(url_pattern.findall(lowered))
        return forbidden or cls._tracker_keyword(url_pattern.sub(" ", lowered))

    @classmethod
    def _first_forbidden_tracker_url(cls, urls: list[str]) -> str:
        return next(
            (host for raw_url in urls if (host := cls._forbidden_tracker_host(raw_url))),
            "",
        )

    @classmethod
    def _tracker_keyword(cls, value: str) -> str:
        pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, cls._TRACKER_KEYWORDS)) + r")(?![a-z0-9])")
        match = pattern.search(value)
        return match.group(0) if match else ""

    @classmethod
    def _forbidden_tracker_host(cls, raw_url: str) -> str:
        authority_match = re.match(r"(?:https?:)?//([^/?#\s]+)", raw_url)
        if authority_match is None:
            return ""
        host = authority_match.group(1).rsplit("@", 1)[-1].partition(":")[0].lower().rstrip(".")
        return next(
            (forbidden for forbidden in cls._TRACKER_DOMAINS if host == forbidden or host.endswith(f".{forbidden}")),
            "",
        )

    @staticmethod
    def _has_banned_title_chars(value: str) -> bool:
        if re.search(r"[\[\]()]", value):
            return True
        if "." in value:
            return True
        return bool(re.search(r"\s{2,}", value))

    @staticmethod
    def _english_word_count(value: Any) -> int:
        return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", str(value or "")))

    def _has_english_book_metadata(self, meta: Meta) -> bool:
        author, title, description = self._book_metadata_values(meta)
        counts = tuple(self._english_word_count(value) for value in (author, title, description))
        return counts[0] >= 1 and counts[1] >= 1 and counts[2] >= 8

    @staticmethod
    def _book_metadata_values(meta: Meta) -> tuple[str, str, str]:
        author = str(meta.author or meta.book_author or "").strip()
        title = str(meta.title or meta.book_title or "").strip()
        description = YUSCENE._first_book_description(meta)
        return author, title, description

    @staticmethod
    def _first_book_description(meta: Meta) -> str:
        values = (
            meta.book_overview,
            meta.overview,
            meta.description,
            meta.description_file_content,
        )
        return " ".join(str(value).strip() for value in values if str(value or "").strip())

    async def _translate_book_metadata_to_english(self, meta: Meta) -> bool:
        api_key = self._translation_api_key()
        source_values = self._translation_source_values(meta)
        if not api_key or source_values is None:
            return False
        translated = await self._translated_book_values(api_key, source_values)
        if translated is None:
            return False
        self._apply_book_translation(meta, source_values, translated)
        if not self._has_english_book_metadata(meta):
            return False
        logger.info(f"{self.tracker}: [green]Translated BOOK author, title, and description to English for tracker compliance.[/green]")
        return True

    def _translation_api_key(self) -> str:
        default = self.config.get("DEFAULT", {})
        mapping = cast(dict[str, Any], default) if isinstance(default, dict) else {}
        return str(mapping.get("google_translate_api_key", "")).strip()

    @classmethod
    def _translation_source_values(cls, meta: Meta) -> tuple[str, str, str] | None:
        author, title, overview = cls._book_metadata_values(meta)
        overview = re.sub(r"<[^>]+>", " ", overview).strip()
        if not all((author, title, overview)):
            return None
        return author, title, overview

    async def _translated_book_values(self, api_key: str, source_values: tuple[str, str, str]) -> tuple[str, str, str] | None:
        try:
            payload = await self._translation_payload(api_key, source_values)
            translated = self._translation_values(payload)
        except httpx.HTTPError:
            logger.info(f"{self.tracker}: [bold red]English book metadata translation HTTP request failed.[/bold red]")
            return None
        except (TypeError, ValueError) as error:
            logger.info(f"{self.tracker}: [bold red]English book metadata translation failed: {error}[/bold red]")
            return None
        if len(translated) != 3 or not all(translated):
            return None
        return translated[0], translated[1], translated[2]

    async def _translation_payload(self, api_key: str, source_values: tuple[str, str, str]) -> Any:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": api_key},
                json={
                    "q": list(source_values),
                    "target": "en",
                    "format": "text",
                },
                timeout=20.0,
            )
            response.raise_for_status()
            return response.json()

    @classmethod
    def _translation_values(cls, payload: Any) -> list[str]:
        return [cls._translated_text(item) for item in cls._translation_entries(payload)]

    @classmethod
    def _translation_entries(cls, payload: Any) -> list[dict[str, Any]]:
        data = cls._translation_data(payload)
        return cls._mapping_items(data.get("translations", []))

    @staticmethod
    def _translation_data(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        mapping = cast(dict[str, Any], payload)
        data = mapping.get("data", {})
        return cast(dict[str, Any], data) if isinstance(data, dict) else {}

    @staticmethod
    def _mapping_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        values = cast(list[Any], value)
        return [cast(dict[str, Any], item) for item in values if isinstance(item, dict)]

    @staticmethod
    def _translated_text(item: dict[str, Any]) -> str:
        return html.unescape(str(item.get("translatedText", "")).strip())

    @staticmethod
    def _apply_book_translation(
        meta: Meta,
        source: tuple[str, str, str],
        translated: tuple[str, str, str],
    ) -> None:
        original_author, original_title, _ = source
        author, title, overview = translated
        meta.author = meta.book_author = author
        meta.title = meta.book_title = title
        meta.book_overview = meta.overview = overview
        release_name = str(meta.name or "")
        if release_name:
            meta.name = release_name.replace(original_author, author, 1).replace(original_title, title, 1)

    async def get_additional_checks(self, meta: Meta) -> bool:
        context = self._additional_check_context(meta)
        if context is None:
            return False
        category, filelist = context
        if not await self._preflight_policies(meta, category):
            return False
        release_name = (await self.get_name(meta))["name"]
        return await self._run_release_policies(meta, category, filelist, release_name)

    def _additional_check_context(self, meta: Meta) -> tuple[str, list[Any]] | None:
        filelist = self._validated_filelist(meta)
        if filelist is None:
            return None
        return str(meta.category or "").upper(), filelist

    async def _preflight_policies(self, meta: Meta, category: str) -> bool:
        if not await self._adult_policy(meta):
            return False
        return await self._book_language_policy(meta, category)

    async def _run_release_policies(
        self,
        meta: Meta,
        category: str,
        filelist: list[Any],
        release_name: str,
    ) -> bool:
        policies = (
            lambda: self._title_policy(meta, category, release_name),
            lambda: self._tracker_reference_policy(meta, category, release_name),
            lambda: self._folder_policy(meta, category, filelist),
            lambda: self._mediainfo_policy(meta, category),
            lambda: self._tv_pack_policy(meta, category),
            lambda: self._screenshot_policy(meta, category),
            lambda: self._extra_file_policy(meta, category, filelist),
            lambda: self._archive_policy(meta, category, filelist),
            lambda: self._boxset_policy(meta, category, release_name),
        )
        for policy in policies:
            if not await policy():
                return False
        return await self._game_type_policy(meta, category)

    def _validated_filelist(self, meta: Meta) -> list[Any] | None:
        raw = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]")
            return None
        return [item for item in raw if self._is_path_like_file(item)]

    async def _adult_policy(self, meta: Meta) -> bool:
        if not self._is_adult_release(meta):
            return True
        return await self._confirm_or_skip("Adult content is not allowed.", meta)

    @classmethod
    def _is_adult_release(cls, meta: Meta) -> bool:
        if meta.adult_media:
            return True
        return bool(cls._genre_tokens(meta).intersection(cls._adult_keywords()))

    @classmethod
    def _genre_tokens(cls, meta: Meta) -> set[str]:
        values = cls._genre_values(meta.keywords) + cls._genre_values(meta.combined_genres)
        return {re.sub(r"\s+", " ", value.casefold()).strip() for value in values if value.strip()}

    @staticmethod
    def _genre_values(value: Any) -> list[str]:
        if isinstance(value, str):
            return re.split(r"[,;/|]", value)
        if isinstance(value, (list, tuple, set)):
            values = cast(list[Any] | tuple[Any, ...] | set[Any], value)
            return [str(item) for item in values]
        return []

    @staticmethod
    def _adult_keywords() -> set[str]:
        return {
            "xxx",
            "erotic",
            "porn",
            "adult",
            "orgy",
            "hentai",
            "adult animation",
            "softcore",
        }

    async def _book_language_policy(self, meta: Meta, category: str) -> bool:
        if category != "BOOK" or self._has_english_book_metadata(meta):
            return True
        if await self._translate_book_metadata_to_english(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]BOOK uploads require an English"
            " author, title, and description. Configure"
            " google_translate_api_key or provide verified English metadata"
            " before uploading.[/bold red]"
        )
        return False

    async def _title_policy(self, meta: Meta, category: str, release_name: str) -> bool:
        if category not in {"MOVIE", "TV"} or not self._has_banned_title_chars(release_name):
            return True
        return await self._confirm_or_skip(
            "The tracker-formatted release name contains unsupported characters or extra spaces.",
            meta,
        )

    async def _tracker_reference_policy(self, meta: Meta, category: str, release_name: str) -> bool:
        if category not in {"MOVIE", "TV", "BOOK"}:
            return True
        if not self._contains_other_tracker_mention(release_name):
            return True
        return await self._confirm_or_skip(
            "The title contains tracker references or tracker domain names.",
            meta,
        )

    async def _folder_policy(self, meta: Meta, category: str, filelist: list[Any]) -> bool:
        invalid = category in {"MOVIE", "TV"} and meta.keep_folder and len(filelist) <= 1
        if not invalid:
            return True
        return await self._confirm_or_skip(
            "Single-file Movie/TV uploads should not be inside a folder for this tracker.",
            meta,
        )

    async def _mediainfo_policy(self, meta: Meta, category: str) -> bool:
        invalid = category in {"MOVIE", "TV"} and not meta.is_disc and not meta.mediainfo
        if not invalid:
            return True
        return await self._confirm_or_skip(
            "Movie/TV uploads on this tracker require mediainfo in parser field.",
            meta,
        )

    async def _tv_pack_policy(self, meta: Meta, category: str) -> bool:
        if category != "TV" or not meta.tv_pack:
            return True
        ended = self.common.is_tv_series_ended(meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES)
        if ended is True:
            return True
        message = self._tv_pack_status_message(ended)
        return await self._confirm_or_skip(message, meta)

    @staticmethod
    def _tv_pack_status_message(ended: bool | None) -> str:
        if ended is None:
            return "Unable to confirm TV series status. TV packs are allowed only for ended series at YUSCENE."
        return "TV collections are only allowed for ended series on this tracker."

    async def _screenshot_policy(self, meta: Meta, category: str) -> bool:
        if category not in {"MOVIE", "TV"} or self._screenshot_count(meta.screens) >= 3:
            return True
        return await self._confirm_or_skip(
            "YUSCENE requires at least 3 screenshots for Movie/TV uploads.",
            meta,
        )

    @staticmethod
    def _screenshot_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    async def _extra_file_policy(self, meta: Meta, category: str, filelist: list[Any]) -> bool:
        if category not in {"MOVIE", "TV"}:
            return True
        extra = self._contains_video_extras(filelist)
        if not extra:
            return True
        return await self._confirm_or_skip(f"Extra file '{extra}' is not allowed for this tracker.", meta)

    async def _archive_policy(self, meta: Meta, category: str, filelist: list[Any]) -> bool:
        if category == "GAME":
            return True
        archive = self._contains_archive_file(filelist)
        if not archive:
            return True
        return await self._confirm_or_skip(
            f"Archive/RAR files are not allowed for {category} on {self.tracker}.",
            meta,
        )

    async def _game_type_policy(self, meta: Meta, category: str) -> bool:
        if category != "GAME":
            return True
        valid = (await self.get_type_id(meta)).get("type_id") != "0"
        if not valid:
            logger.info(f"{self.tracker}: [bold red]No valid tracker type is available for this GAME package format. Skipping upload.[/bold red]")
        return valid

    async def _boxset_policy(self, meta: Meta, category: str, release_name: str) -> bool:
        keywords = ("boxset", "box set", "complete", "collection")
        if category != "MOVIE" or not any(word in release_name.lower() for word in keywords):
            return True
        return await self._confirm_or_skip(
            "Movie boxset-style naming is not accepted. Upload each movie separately.",
            meta,
        )

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._category_map()
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        resolved = self._resolved_category(meta, category)
        return {"category_id": mapping.get(resolved, "0")}

    @staticmethod
    def _category_map() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "GAME": "7",
            "MUSIC": "8",
            "APPS": "9",
            "MUSIC_VIDEO": "10",
            "SPORT": "11",
            "EBOOK": "12",
            "AUDIOBOOK": "13",
        }

    @staticmethod
    def _resolved_category(meta: Meta, category: str | None) -> str:
        resolved = category if category else str(meta.category or "")
        if resolved == "BOOK":
            return "AUDIOBOOK" if meta.audiobook else "EBOOK"
        return resolved

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_map()
        mapping_result = self._mapping_mode_result(mapping, mapping_only, reverse)
        if mapping_result is not None:
            return mapping_result
        resolved = self._resolved_type(meta, type)
        return {"type_id": self._resolved_type_id(meta, mapping, resolved)}

    @staticmethod
    def _mapping_mode_result(mapping: dict[str, str], mapping_only: bool, reverse: bool) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        return None

    @staticmethod
    def _resolved_type_id(meta: Meta, mapping: dict[str, str], resolved: str) -> str:
        value = mapping.get(resolved, "0")
        if meta.category == "BOOK" and value == "0":
            return "21"
        return value

    @staticmethod
    def _type_map() -> dict[str, str]:
        return {
            "DISC": "17",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "MP3": "9",
            "FLAC": "16",
            "M4B": "23",
            "PDF": "21",
            "RAR": "22",
            "EPUB": "24",
            "MOBI": "25",
            "FB2": "26",
            "CBR": "27",
            "CBZ": "27",
            "AZW3": "28",
            "LIT": "29",
            "RTF": "30",
            "M4A": "31",
        }

    @classmethod
    def _resolved_type(cls, meta: Meta, override: str | None) -> str:
        override_text = str(override or "").strip()
        special = cls._special_resolved_type(meta, override_text)
        if special is not None:
            return special
        value = override_text or str(meta.type or "")
        return value.upper().strip().lstrip(".")

    @classmethod
    def _special_resolved_type(cls, meta: Meta, override_text: str) -> str | None:
        if override_text:
            return None
        if meta.category == "MUSIC":
            return cls._music_type(meta)
        return cls._game_special_type(meta)

    @staticmethod
    def _music_type(meta: Meta) -> str:
        value = meta.format
        return str(value).upper() if value else ""

    @classmethod
    def _game_special_type(cls, meta: Meta) -> str | None:
        if meta.category != "GAME":
            return None
        archive = cls._contains_archive_file(cls._meta_files(meta))
        return "RAR" if archive else None

    @staticmethod
    def _meta_files(meta: Meta) -> list[Any]:
        value = meta.filelist
        return list(value) if isinstance(value, (list, tuple, set)) else []

    async def get_name(self, meta: Meta) -> dict[str, str]:
        return {"name": self._normalize_tracker_name(str(meta.name or ""))}

    @staticmethod
    def _normalize_tracker_name(value: str) -> str:
        normalized = re.sub(r"[.()\[\]]+", " ", value)
        return re.sub(r"\s+", " ", normalized).strip()
