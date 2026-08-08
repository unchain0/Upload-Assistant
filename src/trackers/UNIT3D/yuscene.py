# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.console import logger
from src.meta import Meta
from src.trackers.common import Common
from src.trackers.UNIT3D import UNIT3D

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

    _ARCHIVE_EXTENSIONS: set[str] = {".rar", ".r00", ".r01", ".r02", ".r03", ".r04", ".r05", ".r06", ".r07", ".r08", ".r09"}
    _EXTRA_FILE_EXTENSIONS: set[str] = {
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
    _VIDEO_EXTENSIONS: set[str] = {
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

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="YUSCENE")
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _is_path_like_file(filename: Any) -> bool:
        return str(filename).strip() != ""

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [yellow]{message}[/yellow]")
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

    @staticmethod
    def _contains_other_tracker_mention(value: str) -> str:
        lowered = value.lower()
        track_words = ["yify", "yts", "rarbg", "tpb", "piratebay", "1337x"]
        tracker_words = ["kat", "thepiratebay", "nyaa", "eztv", "rutor", "torrentz", "nyaa.si", "kickasstorrents", "limetorrents"]
        if "http://" in lowered or "https://" in lowered or "www." in lowered:
            return "possible link text"
        if any(term in lowered for term in track_words):
            return next((term for term in track_words if term in lowered), "tracker term")
        if any(term in lowered for term in tracker_words):
            return next((term for term in tracker_words if term in lowered), "tracker term")
        return ""

    @staticmethod
    def _has_banned_title_chars(value: str) -> bool:
        if re.search(r"[\[\]()]", value):
            return True
        if "." in value:
            return True
        return bool(re.search(r"\s{2,}", value))

    @staticmethod
    def _is_tv_pack_ended(meta: Meta) -> bool | None:
        status_text = str(meta.imdb_info.get("status", "") if isinstance(meta.imdb_info, dict) else "").casefold().strip()
        ended_values = {"ended", "canceled", "cancelled", "finished", "completed", "completed (ended)", "in development", "ended (ended)"}
        ongoing_values = {"returning series", "in production", "ongoing", "planned", "pilot"}
        if any(value in status_text for value in ended_values):
            return True
        if any(value in status_text for value in ongoing_values):
            return False
        return None

    async def get_additional_checks(self, meta: Meta) -> bool:
        genres = f"{', '.join(meta.keywords)} {meta.combined_genres}"
        adult_keywords = ["xxx", "erotic", "porn", "adult", "orgy", "hentai", "adult animation", "softcore"]
        if meta.adult_media:
            if not await self._confirm_or_skip("Adult content is not allowed.", meta):
                return False

        if any(re.search(rf"(^|,\s*){re.escape(keyword)}(\s*,|$)", genres, re.IGNORECASE) for keyword in adult_keywords):
            logger.info(f"{self.tracker}: [bold red]Porn/xxx is not allowed at {self.tracker}.[/bold red]")
            if not await self._confirm_or_skip("Adult content is not allowed.", meta):
                return False

        category = str(meta.category or "").upper()
        release_name = str(meta.name or "")
        release_context = " ".join(part for part in (release_name, str(meta.description or "")) if part)
        filelist = [item for item in (meta.filelist or []) if self._is_path_like_file(item)]

        if category in {"MOVIE", "TV"} and self._has_banned_title_chars(release_name):
            if not await self._confirm_or_skip("The release name contains unsupported characters or extra spaces.", meta):
                return False

        if category in {"MOVIE", "TV", "BOOK"} and self._contains_other_tracker_mention(release_context):
            if not await self._confirm_or_skip("The title/description contains tracker references, links, or tracker names.", meta):
                return False

        if category in {"MOVIE", "TV"} and meta.keep_folder and len(filelist) <= 1:
            if not await self._confirm_or_skip("Single-file Movie/TV uploads should not be inside a folder for this tracker.", meta):
                return False

        if category in {"MOVIE", "TV"} and not meta.is_disc and not meta.mediainfo:
            if not await self._confirm_or_skip("Movie/TV uploads on this tracker require mediainfo in parser field.", meta):
                return False

        if category == "TV" and meta.tv_pack:
            tv_pack_ended = self._is_tv_pack_ended(meta)
            if tv_pack_ended is False:
                if not await self._confirm_or_skip("TV collections are only allowed for ended series on this tracker.", meta):
                    return False
            if tv_pack_ended is None:
                if not await self._confirm_or_skip("Unable to confirm TV series status. TV packs are allowed only for ended series at YUSCENE.", meta):
                    return False

        if category in {"MOVIE", "TV"} and meta.screens < 3:
            if not await self._confirm_or_skip("YUSCENE requires at least 3 screenshots for Movie/TV uploads.", meta):
                return False

        if category in {"MOVIE", "TV"}:
            extra_file = self._contains_video_extras(filelist)
            if extra_file:
                if not await self._confirm_or_skip(f"Extra file '{extra_file}' is not allowed for this tracker.", meta):
                    return False

        if category != "GAME":
            archive = self._contains_archive_file(filelist)
            if archive:
                if not await self._confirm_or_skip(f"Archive/RAR files are not allowed for {category} on {self.tracker}.", meta):
                    return False

        if category == "MOVIE":
            packed_keywords = ["boxset", "box set", "complete", "collection"]
            if any(keyword in release_name.lower() for keyword in packed_keywords):
                if not await self._confirm_or_skip("Movie boxset-style naming is not accepted. Upload each movie separately.", meta):
                    return False

        return True

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        cat_map = {
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
        if mapping_only:
            return cat_map
        if reverse:
            return {v: k for k, v in cat_map.items()}

        resolved_category = category if category is not None and category != "" else meta.category
        if resolved_category == "BOOK":
            resolved_category = "AUDIOBOOK" if meta.audiobook else "EBOOK"

        category_id = cat_map.get(resolved_category, "0")
        return {"category_id": category_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = {
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
        if mapping_only:
            return type_id
        if reverse:
            return {v: k for k, v in type_id.items()}

        resolved_type = type if type is not None and type != "" else meta.type
        if isinstance(resolved_type, str):
            resolved_type = resolved_type.upper().strip().lstrip(".")

        if meta.category == "MUSIC" and not str(type or "").strip():
            resolved_type = meta.format.upper()

        val = type_id.get(resolved_type or "", "0")
        if meta.category == "BOOK" and val == "0":
            val = "21"  # Default to PDF for unknown book types

        return {"type_id": val}
