# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.console import logger
from src.meta import Meta
from src.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class RailgunPT(NEXUSPHP):
    """
    RAILGUNPT is a CHINESE Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    banned_groups = ()
    display_name = "RailgunPT"
    base_url = "https://bilibili.download"
    source_flag = "[bilibili.download] RailgunPT"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://bilibili.download",)
    allows_bloated_audio = True
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".rar", ".r00", ".r01", ".r02", ".zip", ".7z"})
    _ATTACHMENT_ARCHIVE_MARKERS: tuple[str, ...] = ("sub", "subtitle", "font", "scan", "cover", "patch", "crack")
    _BANNED_EXTENSIONS: frozenset[str] = frozenset({".rm", ".rmvb", ".flv", ".torrent", ".url"})
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset({".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpg", ".mpeg", ".rm", ".rmvb", ".ts", ".vob", ".webm"})
    _LOW_QUALITY_MARKERS: tuple[str, ...] = ("cam", "hdcam", "tc", "telesync", "ts", "scr", "dvdscr", "r5", "r5 line", "halfcd")
    _SOURCE_TOKENS: tuple[str, ...] = ("blu-ray", "bluray", "hddvd", "hd dvd", "hdtv", "uhdtv", "dvd", "web-dl", "webdl", "remux")
    _VIDEO_CODEC_TOKENS: tuple[str, ...] = ("avc", "h.264", "h264", "hevc", "h.265", "h265", "mpeg-2", "mpeg2", "vc-1", "vc1", "x264", "x265", "xvid")
    _PACK_SOURCE_TOKENS: tuple[str, ...] = ("bluray", "hddvd", "hdtv", "uhdtv", "dvd", "webdl", "webrip")
    _PACK_CODEC_TOKENS: tuple[str, ...] = ("x264", "x265", "h264", "h265", "hevc", "avc", "mpeg2", "vc1", "xvid")
    _DISC_TYPES: frozenset[str] = frozenset({"bdmv", "dvd", "hddvd_ts", "video_ts"})

    def __init__(self, config: Config) -> None:
        super().__init__(config, "RAILGUNPT")

    @staticmethod
    def _normalized_token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @staticmethod
    def _metadata_values(value: Any) -> list[Any]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return []

    @staticmethod
    def _title_contains_token(title: str, token: Any) -> bool:
        parts = re.findall(r"[a-z0-9]+", str(token or "").casefold())
        if not parts:
            return False
        pattern = r"[\s._-]*".join(re.escape(part) for part in parts)
        return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", title.casefold()))

    @staticmethod
    def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
        normalized = re.sub(r"[._-]+", " ", value.casefold())
        return any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", normalized) for marker in markers)

    @classmethod
    def _archive_is_allowed_attachment(cls, path: Path) -> bool:
        stem = cls._normalized_token(path.stem)
        return any(marker in stem for marker in cls._ATTACHMENT_ARCHIVE_MARKERS)

    @classmethod
    def _pack_tokens(cls, paths: list[Path], tokens: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        normalized_tokens = {cls._normalized_token(token): token for token in tokens}
        for path in paths:
            normalized_name = cls._normalized_token(path.stem)
            matches = [canonical for token, canonical in normalized_tokens.items() if token in normalized_name]
            if matches:
                found.add(matches[0])
        return found

    @staticmethod
    def _resolution_height(value: Any) -> int | None:
        match = re.search(r"(\d{3,4})", str(value or ""))
        return int(match.group(1)) if match else None

    def _valid_sd_release(self, meta: Meta, height: int) -> bool:
        if height < 480:
            return False
        disc_type = str(meta.is_disc or "").casefold()
        release_type = self._normalized_token(meta.type)
        source = self._normalized_token(meta.source)
        if "dvd" in disc_type or release_type in {"dvd", "dvdrip", "cndvdrip"} or source in {"dvd", "dvdrip", "cndvdrip"}:
            return True
        return release_type == "encode" and source in {"bluray", "uhdbluray", "hddvd", "hdtv", "uhdtv"}

    def _title_has_required_video_tokens(self, meta: Meta, title: str) -> bool:
        resolution = str(meta.resolution or "").strip()
        if resolution and not self._title_contains_token(title, resolution):
            return False
        if not any(self._title_contains_token(title, token) for token in self._SOURCE_TOKENS):
            return False
        if not any(self._title_contains_token(title, token) for token in self._VIDEO_CODEC_TOKENS):
            return False
        if str(meta.category or "").upper() == "MOVIE" and meta.year and str(meta.year) not in title:
            return False
        if str(meta.category or "").upper() == "TV":
            tv_pattern = r"\bS\d{1,3}(?:E\d{1,4})?\b" if meta.tv_pack else r"(?:\bS\d{1,3}E\d{1,4}\b|\b\d{4}[.-]\d{2}[.-]\d{2}\b)"
            if not re.search(tv_pattern, title, re.IGNORECASE):
                return False
        return True

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()
        if category not in self.supported_categories:
            logger.info(f"{self.tracker}: [bold red]Only Movie and TV uploads are supported by this adapter.[/bold red]")
            return False

        if bool(meta.adult_media or meta.tmdb_adult_media or meta.nsfw):
            logger.info(f"{self.tracker}: [bold red]Pornographic or sensitive adult content is not allowed.[/bold red]")
            return False

        genre_values = [str(value).casefold().strip() for value in self._metadata_values(meta.genres)]
        keyword_values = [str(value).casefold().strip() for value in self._metadata_values(meta.keywords)]
        if {"politics", "political", "political propaganda"}.intersection(genre_values + keyword_values):
            logger.info(f"{self.tracker}: [bold red]Politically sensitive content is not allowed.[/bold red]")
            return False

        try:
            source_size = int(meta.source_size)
        except (TypeError, ValueError, OverflowError):
            source_size = 0
        if 0 < source_size < 100 * 1024 * 1024:
            logger.info(f"{self.tracker}: [bold red]Video torrents must be at least 100 MiB.[/bold red]")
            return False

        raw_filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]")
            return False
        paths = [Path(str(item)) for item in raw_filelist if str(item).strip()]
        video_paths = [path for path in paths if path.suffix.casefold() in self._VIDEO_EXTENSIONS]

        for path in paths:
            suffix = path.suffix.casefold()
            if suffix in self._BANNED_EXTENSIONS:
                logger.info(f"{self.tracker}: [bold red]Unsupported or spam file found: {path.name}.[/bold red]")
                return False
            if suffix in self._ARCHIVE_EXTENSIONS and not self._archive_is_allowed_attachment(path):
                logger.info(f"{self.tracker}: [bold red]Archived files are not allowed: {path.name}.[/bold red]")
                return False
            lowered_name = path.name.casefold()
            if "downloaded from" in lowered_name or "torrent downloaded" in lowered_name:
                logger.info(f"{self.tracker}: [bold red]Advertising or tracker-reference files are not allowed.[/bold red]")
                return False

        if video_paths and all("sample" in path.stem.casefold() for path in video_paths):
            logger.info(f"{self.tracker}: [bold red]An individual sample cannot be uploaded as the main torrent.[/bold red]")
            return False
        main_video_paths = [path for path in video_paths if "sample" not in path.stem.casefold()]

        release_context = " ".join(str(value or "") for value in (meta.name, meta.type, meta.source, meta.uuid))
        if self._contains_marker(release_context, self._LOW_QUALITY_MARKERS):
            logger.info(f"{self.tracker}: [bold red]CAM/TC/TS/SCR/R5 and similar low-quality sources are not allowed.[/bold red]")
            return False
        codec_context = " ".join(str(value or "") for value in (meta.video_codec, meta.video_encode))
        if "realvideo" in codec_context.casefold() or self._normalized_token(codec_context) in {"rv", "rv10", "rv20", "rv30", "rv40"}:
            logger.info(f"{self.tracker}: [bold red]RealVideo encodes are not allowed.[/bold red]")
            return False

        disc_type = str(meta.is_disc or "").casefold()
        height = self._resolution_height(meta.resolution)
        if not disc_type:
            if height is None:
                logger.info(f"{self.tracker}: [bold red]A supported video resolution is required.[/bold red]")
                return False
            if height < 720 and not self._valid_sd_release(meta, height):
                logger.info(f"{self.tracker}: [bold red]SD uploads must be at least 480p and sourced from HD media or DVD.[/bold red]")
                return False
            if height < 720 and "upscale" in release_context.casefold():
                logger.info(f"{self.tracker}: [bold red]Upscaled SD-mastered content is not allowed.[/bold red]")
                return False

        release_name = str(meta.name or "").strip()
        if not release_name or not self._title_has_required_video_tokens(meta, release_name):
            logger.info(f"{self.tracker}: [bold red]Title must include the required year/season, resolution, source, and video codec information.[/bold red]")
            return False

        if category == "MOVIE" and len(main_video_paths) > 1 and disc_type not in self._DISC_TYPES:
            pack_markers = ("boxset", "box set", "collection", "trilogy")
            if not any(marker in release_name.casefold() for marker in pack_markers):
                logger.info(f"{self.tracker}: [bold red]Movie packs must be identifiable official box-set collections.[/bold red]")
                return False

        if meta.tv_pack and len(main_video_paths) > 1:
            resolutions = {match.group(1).casefold() for path in main_video_paths if (match := re.search(r"\b(480[pi]|576[pi]|720p|1080[pi]|2160p)\b", path.name, re.IGNORECASE))}
            sources = self._pack_tokens(main_video_paths, self._PACK_SOURCE_TOKENS)
            codecs = self._pack_tokens(main_video_paths, self._PACK_CODEC_TOKENS)
            if any(len(values) > 1 for values in (resolutions, sources, codecs)):
                logger.info(f"{self.tracker}: [bold red]Packed videos must use the same source type, resolution, and video codec.[/bold red]")
                return False

        return True

    def get_category(self, meta: Meta) -> int:
        animations = 405
        documentaries = 404
        movies = 401
        tv_series = 402
        tv_shows = 403

        category = str(meta.category or "").upper()
        genres = ", ".join(str(value) for value in self._metadata_values(meta.genres)).lower()
        keywords = ", ".join(str(value) for value in self._metadata_values(meta.keywords)).lower()

        if "documentary" in genres or "documentary" in keywords:
            return documentaries
        if meta.anime or "animation" in genres or "animation" in keywords:
            return animations

        if category == "MOVIE":
            return movies
        if category == "TV":
            game_show_keywords = [
                "award show",
                "competition",
                "game show",
                "music show",
                "performance",
                "reality television",
                "reality tv",
                "reality",
                "stand-up",
                "talk show",
                "tv show",
                "variety",
            ]
            if any(re.search(rf"(^|,\s*){re.escape(keyword)}(\s*,|$)", genres, re.IGNORECASE) for keyword in game_show_keywords):
                return tv_shows
            return tv_series

        return movies

    def get_type(self, meta: Meta) -> int:
        blu_ray = 1
        dvd = 6
        encode = 7
        hdtv = 5
        remux = 3
        uhd = 2
        web_dl = 4

        is_disc = str(meta.is_disc or "").lower()
        mtype = str(meta.type).lower()
        resolution = str(meta.resolution or "").lower()

        if is_disc == "bdmv":
            if resolution == "2160p":
                return uhd
            return blu_ray
        if "dvd" in is_disc:
            return dvd

        if mtype == "remux":
            return remux
        if mtype in ("webdl", "webrip"):
            return web_dl
        if mtype == "hdtv":
            return hdtv
        if mtype == "encode":
            return encode

        return encode

    def get_codec(self, meta: Meta) -> int:
        h264 = 1
        h265 = 2
        mpeg2 = 4
        other = 6
        vc1 = 3
        xvid = 5

        codec = str(meta.video_codec or "").lower()

        if "h265" in codec or "x265" in codec or "hevc" in codec:
            return h265
        if "h264" in codec or "x264" in codec or "avc" in codec:
            return h264
        if "mpeg2" in codec or "mpeg-2" in codec:
            return mpeg2
        if "vc1" in codec or "vc-1" in codec:
            return vc1
        if "xvid" in codec:
            return xvid

        return other

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution or "").lower()

        if resolution == "1080p" or resolution == "1080i":
            return 2
        if resolution == "720p":
            return 3
        if meta.sd:
            return 4
        if resolution == "2160p":
            return 1

        return 5

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = str(meta.audio or "").lower()

        if "true" in audio_codec or "atmos" in audio_codec:
            return 1
        if "dts" in audio_codec:
            return 2
        if "dd" in audio_codec:
            return 3
        if "lpcm" in audio_codec:
            return 4
        if "flac" in audio_codec:
            return 5
        if "mp3" in audio_codec:
            return 6
        if "aac" in audio_codec:
            return 7
        if "ape" in audio_codec:
            return 8
        if "wav" in audio_codec:
            return 10

        return 9

    def get_checkboxes(self, meta: Meta) -> list[str]:
        chinese_audio = 5
        chinese_subtitle = 6
        hdr = 7
        reposting_prohibited = 1

        audio_tracks = meta.audio_languages or []
        mhdr = str(meta.hdr or "")
        subtitle_tracks = meta.subtitle_languages or []

        checkboxes: list[str] = []

        if meta.exclusive:
            checkboxes.append(str(reposting_prohibited))

        if "Chinese" in audio_tracks or "Mandarin" in audio_tracks:
            checkboxes.append(str(chinese_audio))

        if "Chinese" in subtitle_tracks or "Mandarin" in subtitle_tracks:
            checkboxes.append(str(chinese_subtitle))

        if "HDR" in mhdr.upper():
            checkboxes.append(str(hdr))

        return checkboxes

    def get_anonymous(self, meta: Meta) -> bool:
        return not (meta.anon == 0 and not self.config["TRACKERS"][self.tracker].get("anon", False))
