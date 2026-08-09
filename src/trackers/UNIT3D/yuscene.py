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

    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".rar", ".r00", ".r01", ".r02", ".r03", ".r04", ".r05", ".r06", ".r07", ".r08", ".r09"})
    _EXTRA_FILE_EXTENSIONS: frozenset[str] = frozenset({
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
    })
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset({
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
    })
    _TV_ENDED_STATUSES: frozenset[str] = frozenset({"ended", "canceled", "cancelled", "finished", "completed", "completed (ended)", "ended (ended)"})
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset({"returning series", "in production", "ongoing", "planned", "pilot", "in development"})

    _TRACKER_KEYWORDS: frozenset[str] = frozenset({
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
    })

    _TRACKER_DOMAINS: frozenset[str] = frozenset({
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
    })

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
        lowered = str(value or "").lower()
        url_pattern = re.compile(r"(?:https?:)?//[^\s]+")

        if urls := url_pattern.findall(lowered):
            for raw_url in urls:
                authority_match = re.match(r"(?:https?:)?//([^/\s]+)", raw_url)
                if authority_match is None:
                    continue
                host = authority_match.group(1).rsplit("@", 1)[-1].partition(":")[0].lower().rstrip(".")
                if host:
                    for forbidden in YUSCENE._TRACKER_DOMAINS:
                        if host == forbidden or host.endswith(f".{forbidden}"):
                            return forbidden

        tracker_terms_pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, YUSCENE._TRACKER_KEYWORDS)) + r")(?![a-z0-9])")
        if match := tracker_terms_pattern.search(url_pattern.sub(" ", lowered)):
            return match.group(0)
        return ""

    @staticmethod
    def _has_banned_title_chars(value: str) -> bool:
        if re.search(r"[\[\]()]", value):
            return True
        if "." in value:
            return True
        return bool(re.search(r"\s{2,}", value))

    async def get_additional_checks(self, meta: Meta) -> bool:
        raw_keywords = meta.keywords or []
        genre_values: list[str]
        if isinstance(raw_keywords, str):
            genre_values = [raw_keywords]
        elif isinstance(raw_keywords, (list, tuple, set)):
            genre_values = [str(value) for value in raw_keywords]
        else:
            genre_values = []
        if isinstance(meta.combined_genres, str):
            genre_values.extend(re.split(r"[,;/|]", meta.combined_genres))
        elif isinstance(meta.combined_genres, (list, tuple, set)):
            genre_values.extend(str(value) for value in meta.combined_genres)
        genre_tokens = {re.sub(r"\s+", " ", value.casefold()).strip() for value in genre_values if value.strip()}
        adult_keywords = {"xxx", "erotic", "porn", "adult", "orgy", "hentai", "adult animation", "softcore"}
        if meta.adult_media and not await self._confirm_or_skip("Adult content is not allowed.", meta):
            return False

        if genre_tokens.intersection(adult_keywords):
            logger.info(f"{self.tracker}: [bold red]Porn/xxx is not allowed at {self.tracker}.[/bold red]")
            if not await self._confirm_or_skip("Adult content is not allowed.", meta):
                return False

        category = str(meta.category or "").upper()
        release_name = str(meta.name or "")
        raw_filelist = meta.filelist or []
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]")
            return False
        filelist = [item for item in raw_filelist if self._is_path_like_file(item)]

        if category in {"MOVIE", "TV"} and self._has_banned_title_chars(release_name) and not await self._confirm_or_skip(
            "The release name contains unsupported characters or extra spaces.", meta
        ):
            return False

        if category in {"MOVIE", "TV", "BOOK"} and self._contains_other_tracker_mention(release_name) and not await self._confirm_or_skip(
            "The title contains tracker references or tracker domain names.", meta
        ):
            return False

        if category in {"MOVIE", "TV"} and meta.keep_folder and len(filelist) <= 1 and not await self._confirm_or_skip(
            "Single-file Movie/TV uploads should not be inside a folder for this tracker.", meta
        ):
            return False

        if category in {"MOVIE", "TV"} and not meta.is_disc and not meta.mediainfo and not await self._confirm_or_skip(
            "Movie/TV uploads on this tracker require mediainfo in parser field.", meta
        ):
            return False

        if category == "TV" and meta.tv_pack:
            tv_pack_ended = self.common.is_tv_series_ended(meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES)
            if tv_pack_ended is False and not await self._confirm_or_skip("TV collections are only allowed for ended series on this tracker.", meta):
                return False
            if tv_pack_ended is None and not await self._confirm_or_skip(
                "Unable to confirm TV series status. TV packs are allowed only for ended series at YUSCENE.", meta
            ):
                return False

        try:
            screenshot_count = int(meta.screens)
        except (TypeError, ValueError):
            screenshot_count = 0

        if category in {"MOVIE", "TV"} and screenshot_count < 3 and not await self._confirm_or_skip(
            "YUSCENE requires at least 3 screenshots for Movie/TV uploads.", meta
        ):
            return False

        if category in {"MOVIE", "TV"}:
            extra_file = self._contains_video_extras(filelist)
            if extra_file and not await self._confirm_or_skip(f"Extra file '{extra_file}' is not allowed for this tracker.", meta):
                return False

        if category != "GAME":
            archive = self._contains_archive_file(filelist)
            if archive and not await self._confirm_or_skip(f"Archive/RAR files are not allowed for {category} on {self.tracker}.", meta):
                return False

        if category == "MOVIE":
            packed_keywords = ["boxset", "box set", "complete", "collection"]
            if any(keyword in release_name.lower() for keyword in packed_keywords) and not await self._confirm_or_skip(
                "Movie boxset-style naming is not accepted. Upload each movie separately.", meta
            ):
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
