# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.console import logger
from src.meta import Meta
from src.trackers.common import Common
from src.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class PolishTorrent(UNIT3D):
    """
    Polish Torrent (PTT) is a PRIVATE tracker for MOVIES / TV / GENERAL
    """

    tracker = "POLISHTORRENT"
    display_name = "PolishTorrent"
    allows_bloated_audio = True
    base_url = "https://polishtorrent.top"
    banned_groups = ("ViP", "BiRD", "M@RTiNU$", "inTGrity", "CiNEMAET", "MusicET", "TeamET", "R2D2")
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".rar", ".r00", ".r01", ".r02", ".r03", ".r04", ".r05", ".r06", ".r07", ".r08", ".r09", ".zip", ".7z"})
    _SCREENSHOT_EXTENSIONS: tuple[str, ...] = (".png", ".tiff", ".tif")
    _TRACKER_DOMAINS: frozenset[str] = frozenset(
        {"1337x.to", "eztv.re", "katcr.co", "katcr.to", "limetorrents.cc", "nyaa.si", "rarbg.to", "rutracker.net", "thepiratebay.org", "torrentdownloads.me", "yts.mx"}
    )
    _TRACKER_TERMS: tuple[str, ...] = (
        "yify",
        "yts",
        "rarbg",
        "tpb",
        "thepiratebay",
        "1337x",
        "kat",
        "nyaa",
        "nzbgeek",
        "torrentdownloads",
        "rutor",
        "limetorrents",
        "kickasstorrents",
        "torrentz",
        "torrentproject",
        "rutracker",
        "zooqle",
        "eztv",
        "showrss",
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="POLISHTORRENT")
        self.config: Config = config
        self.common = Common(config)

    @staticmethod
    def _is_path_like_file(filename: Any) -> bool:
        return str(filename).strip() != ""

    @staticmethod
    def _video_filelist(filelist: list[Any]) -> list[Path]:
        return [Path(str(item)) for item in filelist if Path(str(item)).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".wmv", ".flv", ".webm"}]

    @staticmethod
    def _contains_archive_file(filelist: list[Any]) -> str:
        for item in filelist:
            path = Path(str(item))
            if path.suffix.lower() in PolishTorrent._ARCHIVE_EXTENSIONS:
                return path.name
        return ""

    @staticmethod
    def _extract_image_url(image: dict[str, Any], key: str) -> str:
        if not isinstance(image, dict):
            return ""
        raw_value = image.get(key, "")
        if not isinstance(raw_value, str):
            return ""
        return raw_value.strip()

    @staticmethod
    def _image_extension(url: str) -> str | None:
        match = re.fullmatch(r"https?://(?:[a-z0-9-]+\.)*[a-z0-9-]+(?::\d+)?(/[^\s]*)?", url, re.IGNORECASE)
        if match is None:
            return None
        path = (match.group(1) or "").split("?", 1)[0].split("#", 1)[0]
        return Path(path).suffix.lower()

    @staticmethod
    def _is_allowed_screenshot_image(image: dict[str, Any]) -> bool:
        raw_url = PolishTorrent._extract_image_url(image, "raw_url")
        web_url = PolishTorrent._extract_image_url(image, "web_url")
        img_url = PolishTorrent._extract_image_url(image, "img_url")

        extensions: list[str] = []
        for source_url, extension_required in ((raw_url, True), (web_url, False), (img_url, True)):
            if not source_url:
                continue
            extension = PolishTorrent._image_extension(source_url)
            if extension is None:
                return False
            if not extension:
                if extension_required:
                    return False
                continue
            extensions.append(extension)
        return not extensions or all(extension in PolishTorrent._SCREENSHOT_EXTENSIONS for extension in extensions)

    @staticmethod
    def _has_valid_screenshot_thumb_and_full(image: dict[str, Any]) -> bool:
        raw_url = PolishTorrent._extract_image_url(image, "raw_url")
        web_url = PolishTorrent._extract_image_url(image, "web_url")
        img_url = PolishTorrent._extract_image_url(image, "img_url")

        has_full = bool(raw_url or web_url)
        has_thumb = bool(img_url)
        return has_full and has_thumb

    @staticmethod
    def _has_banned_title_chars(value: str) -> bool:
        if re.search(r"[.()\[\]]", value):
            return True
        return bool(re.search(r"\s{2,}", value))

    @staticmethod
    def _contains_other_tracker_mention(value: str) -> bool:
        lowered = value.lower()
        url_pattern = re.compile(r"(?:https?:)?//[^\s]+")
        for raw_url in url_pattern.findall(lowered):
            authority_match = re.match(r"(?:https?:)?//([^/\s]+)", raw_url)
            if authority_match is None:
                continue
            host = authority_match.group(1).rsplit("@", 1)[-1].partition(":")[0].lower().rstrip(".")
            if any(host == forbidden or host.endswith(f".{forbidden}") for forbidden in PolishTorrent._TRACKER_DOMAINS):
                return True

        tracker_pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, PolishTorrent._TRACKER_TERMS)) + r")(?![a-z0-9])")
        return bool(tracker_pattern.search(url_pattern.sub(" ", lowered)))

    @staticmethod
    def _is_tv_pack_ended(meta: Meta) -> bool | None:
        if not isinstance(meta.imdb_info, dict):
            return None

        status_text = str(meta.imdb_info.get("status", "")).casefold().strip()
        if not status_text:
            return None

        ended_values = {"ended", "canceled", "cancelled", "finished", "completed"}
        ongoing_values = {"returning", "continuing", "in production", "upcoming", "ongoing", "pilot"}

        if any(value in status_text for value in ended_values):
            return True
        if any(value in status_text for value in ongoing_values):
            return False
        return None

    @staticmethod
    def _is_boxset_style(name: str, filelist: list[Any]) -> bool:
        normalized = f" {name.lower()} "
        boxset_keywords = ["boxset", "box set", "collection", "complete", "kolekc", "całość", "kolekcja", "odcinki"]
        if any(keyword in normalized for keyword in boxset_keywords):
            return True

        video_count = len(PolishTorrent._video_filelist(filelist))
        return video_count > 1

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()

        if meta.adult_media:
            logger.info(f"{self.tracker}: [bold red]Pornographic/XXX content is not allowed.[/bold red]")
            return False

        release_name = str(meta.name or "")
        release_context = " ".join(part for part in (release_name, str(meta.description or ""), str(meta.description_file_content or "")) if part)
        raw_filelist = meta.filelist or []
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]")
            return False
        filelist = [item for item in raw_filelist if self._is_path_like_file(item)]

        if category in {"MOVIE", "TV"} and self._has_banned_title_chars(release_name):
            logger.info(f"{self.tracker}: [bold red]Release name contains banned characters or extra spaces. Remove . ( ) [ ] and extra spaces.[/bold red]")
            return False

        if category in {"MOVIE", "TV"} and self._contains_other_tracker_mention(release_context):
            logger.info(f"{self.tracker}: [bold red]Do not include links or other tracker references in title/description.[/bold red]")
            return False

        try:
            screenshot_count = int(meta.screens)
        except (TypeError, ValueError):
            screenshot_count = 0

        if category in {"MOVIE", "TV"} and screenshot_count < 3:
            logger.info(f"{self.tracker}: [bold red]{self.tracker} requires at least 3 screenshots for Movie/TV uploads.[/bold red]")
            return False

        if category in {"MOVIE", "TV"} and meta.image_list:
            if not all(PolishTorrent._is_allowed_screenshot_image(entry) for entry in meta.image_list):
                logger.info(
                    f"{self.tracker}: [bold red]{self.tracker} requires screenshot uploads in .PNG or .TIFF and the current image metadata contains unsupported formats.[/bold red]"
                )
                return False
            if not all(PolishTorrent._has_valid_screenshot_thumb_and_full(entry) for entry in meta.image_list):
                logger.info(
                    f"{self.tracker}: [bold red]{self.tracker} requires thumbnail and full-size screenshot links (img_url + raw/web_url) in screenshot metadata.[/bold red]"
                )
                return False
            logger.info(f"{self.tracker}: [yellow]Unable to validate screenshot layout from available metadata. Keep screenshots organized in rows (multi-column line layout).[/yellow]")

        if category in {"MOVIE", "TV"} and not meta.is_disc and not meta.mediainfo:
            logger.info(f"{self.tracker}: [bold red]Movie/TV uploads must include mediainfo on {self.tracker}.[/bold red]")
            return False

        archive = self._contains_archive_file(filelist)
        if category in {"MOVIE", "TV"} and archive:
            logger.info(f"{self.tracker}: [bold red]Archive/multipart files are not allowed. Found: {archive}[/bold red]")
            return False

        if category == "MOVIE" and self._is_boxset_style(release_name, filelist):
            logger.info(f"{self.tracker}: [bold red]Movie boxsets/collections are not allowed. Upload each movie separately.[/bold red]")
            return False

        if category == "TV" and meta.tv_pack:
            series_ended = self._is_tv_pack_ended(meta)
            if series_ended is None:
                logger.info(f"{self.tracker}: [bold red]Unable to confirm TV series status. TV season packs are only allowed for ended series on {self.tracker}.[/bold red]")
                return False
            if not series_ended:
                logger.info(f"{self.tracker}: [bold red]TV packs are allowed only for completed series on {self.tracker}.[/bold red]")
                return False

        if category in {"MOVIE", "TV"}:
            video_paths = self._video_filelist(filelist)
            if len(video_paths) <= 1 and meta.keep_folder:
                logger.info(f"{self.tracker}: [bold red]Single-file Movie/TV uploads should be uploaded without a folder on {self.tracker}.[/bold red]")
                return False

        return self.common.check_and_confirm_adult_media_upload(meta, self.tracker)

    async def get_category_id(self, meta: Meta, category: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        category_id = {
            "MOVIE": "1",
            "TV": "9",
        }
        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}
        if category:
            return {"category_id": category_id.get(category, "0")}
        meta_category = meta.category
        resolved_id = category_id.get(meta_category, "0")
        return {"category_id": resolved_id}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        ptt_name = meta.name
        imdb_info = meta.imdb_info
        if meta.original_language == "pl" and imdb_info:
            ptt_name = ptt_name.replace(meta.aka, "")
            ptt_name = ptt_name.replace(meta.title, str(imdb_info.get("aka", "")))
        return {"name": ptt_name.strip()}
