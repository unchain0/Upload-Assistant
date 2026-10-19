# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class PolishTorrent(UNIT3D):
    """
    Polish Torrent (PTT) is a PRIVATE tracker for MOVIES / TV / GENERAL
    """

    tracker = "POLISHTORRENT"
    display_name = "PolishTorrent"
    allows_bloated_audio = True
    base_url = "https://polishtorrent.top"
    banned_groups = (
        "ViP",
        "BiRD",
        "M@RTiNU$",
        "inTGrity",
        "CiNEMAET",
        "MusicET",
        "TeamET",
        "R2D2",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
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
            ".zip",
            ".7z",
        }
    )
    _SCREENSHOT_EXTENSIONS: tuple[str, ...] = (".png", ".tiff", ".tif")
    _TRACKER_DOMAINS: frozenset[str] = frozenset(
        {
            "1337x.to",
            "eztv.re",
            "katcr.co",
            "katcr.to",
            "limetorrents.cc",
            "nyaa.si",
            "rarbg.to",
            "rutracker.net",
            "thepiratebay.org",
            "torrentdownloads.me",
            "yts.mx",
        }
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
    _TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {
            "returning",
            "continuing",
            "in production",
            "upcoming",
            "ongoing",
            "pilot",
        }
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
        return [
            Path(str(item))
            for item in filelist
            if Path(str(item)).suffix.lower()
            in {
                ".mkv",
                ".mp4",
                ".avi",
                ".mov",
                ".m4v",
                ".mpg",
                ".mpeg",
                ".ts",
                ".m2ts",
                ".wmv",
                ".flv",
                ".webm",
            }
        ]

    @staticmethod
    def _contains_archive_file(filelist: list[Any]) -> str:
        for item in filelist:
            path = Path(str(item))
            lowered_name = path.name.casefold()
            if (
                path.suffix.lower() in PolishTorrent._ARCHIVE_EXTENSIONS
                or re.search(
                    r"(?:\.r\d{2,}|(?:\.rar|\.zip|\.7z)\.\d{3,})$",
                    lowered_name,
                )
            ):
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
        match = re.fullmatch(
            r"https?://(?:[a-z0-9-]+\.)*[a-z0-9-]+(?::\d+)?(/[^\s]*)?",
            url,
            re.IGNORECASE,
        )
        if match is None:
            return None
        path = (match.group(1) or "").split("?", 1)[0].split("#", 1)[0]
        return Path(path).suffix.lower()

    @classmethod
    def _is_allowed_screenshot_image(cls, image: dict[str, Any]) -> bool:
        urls = (
            (cls._extract_image_url(image, "raw_url"), True),
            (cls._extract_image_url(image, "web_url"), False),
            (cls._extract_image_url(image, "img_url"), True),
        )
        return all(
            cls._screenshot_url_allowed(url, required)
            for url, required in urls
        )

    @classmethod
    def _screenshot_url_allowed(
        cls, url: str, extension_required: bool
    ) -> bool:
        if not url:
            return True
        extension = cls._image_extension(url)
        if extension is None:
            return False
        if not extension:
            return not extension_required
        return extension in cls._SCREENSHOT_EXTENSIONS

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

    @classmethod
    def _contains_other_tracker_mention(cls, value: str) -> bool:
        lowered = value.lower()
        url_pattern = re.compile(r"(?:https?:)?//[^\s]+")
        if any(
            cls._is_forbidden_tracker_url(raw_url)
            for raw_url in url_pattern.findall(lowered)
        ):
            return True
        tracker_pattern = re.compile(
            r"(?<![a-z0-9])(?:"
            + "|".join(map(re.escape, cls._TRACKER_TERMS))
            + r")(?![a-z0-9])"
        )
        return bool(tracker_pattern.search(url_pattern.sub(" ", lowered)))

    @classmethod
    def _is_forbidden_tracker_url(cls, raw_url: str) -> bool:
        authority_match = re.match(r"(?:https?:)?//([^/?#\s]+)", raw_url)
        if authority_match is None:
            return False
        host = (
            authority_match.group(1)
            .rsplit("@", 1)[-1]
            .partition(":")[0]
            .lower()
            .strip("()[]{}<>")
            .rstrip(".")
        )
        return any(
            host == forbidden or host.endswith(f".{forbidden}")
            for forbidden in cls._TRACKER_DOMAINS
        )

    @staticmethod
    def _is_tv_pack_ended(meta: Meta) -> bool | None:
        return Common.is_tv_series_ended(
            meta,
            PolishTorrent._TV_ENDED_STATUSES,
            PolishTorrent._TV_ONGOING_STATUSES,
        )

    @staticmethod
    def _is_boxset_style(name: str, _filelist: list[Any]) -> bool:
        boxset_keywords = (
            "boxset",
            "box set",
            "collection",
            "kolekcja",
            "całość",
        )
        pattern = re.compile(
            r"(?<!\w)(?:"
            + "|".join(map(re.escape, boxset_keywords))
            + r")(?!\w)",
            re.IGNORECASE,
        )
        return bool(pattern.search(name))

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()
        release_name = (await self.get_name(meta))["name"]
        filelist = self._validated_filelist(meta)
        if filelist is None:
            return False
        checks = (
            self._adult_policy(meta),
            self._title_policy(category, release_name),
            self._tracker_reference_policy(meta, category, release_name),
            self._screenshot_policy(meta, category),
            self._mediainfo_policy(meta, category),
            self._archive_policy(category, filelist),
            self._collection_policy(category, release_name, filelist),
            self._tv_pack_policy(meta, category),
            self._folder_policy(meta, category, filelist),
        )
        return all(checks)

    def _validated_filelist(self, meta: Meta) -> list[Any] | None:
        raw = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw, (list, tuple, set)):
            logger.info(
                f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
            )
            return None
        return [item for item in raw if self._is_path_like_file(item)]

    def _adult_policy(self, meta: Meta) -> bool:
        if not meta.adult_media:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Pornographic/XXX content is not allowed.[/bold red]"
        )
        return False

    def _title_policy(self, category: str, release_name: str) -> bool:
        if category not in {"MOVIE", "TV"} or not self._has_banned_title_chars(
            release_name
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Tracker-formatted release name still contains banned characters or extra spaces.[/bold red]"
        )
        return False

    def _tracker_reference_policy(
        self, meta: Meta, category: str, release_name: str
    ) -> bool:
        if category not in {"MOVIE", "TV"}:
            return True
        context = self._release_context(meta, release_name)
        if not self._contains_other_tracker_mention(context):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Do not include links or other tracker references in title/description.[/bold red]"
        )
        return False

    @staticmethod
    def _release_context(meta: Meta, release_name: str) -> str:
        values = (
            release_name,
            str(meta.description or ""),
            str(meta.description_file_content or ""),
        )
        return " ".join(filter(None, values))

    def _screenshot_policy(self, meta: Meta, category: str) -> bool:
        if category not in {"MOVIE", "TV"}:
            return True
        screenshot_count = self._screenshot_count(meta.screens)
        if screenshot_count < 3:
            logger.info(
                f"{self.tracker}: [bold red]{self.tracker} requires at least 3 screenshots for Movie/TV uploads.[/bold red]"
            )
            return False
        return self._screenshot_metadata_policy(meta, screenshot_count)

    @staticmethod
    def _screenshot_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    def _screenshot_metadata_policy(
        self, meta: Meta, screenshot_count: int
    ) -> bool:
        images = self._image_metadata(meta.image_list)
        if images is None:
            logger.info(
                f"{self.tracker}: [bold red]Screenshot metadata is invalid.[/bold red]"
            )
            return False
        error = self._screenshot_metadata_error(images, screenshot_count)
        if error:
            logger.info(f"{self.tracker}: [bold red]{error}[/bold red]")
            return False
        logger.info(
            f"{self.tracker}: [yellow]Unable to validate screenshot layout from available metadata. Keep screenshots organized in rows (multi-column line layout).[/yellow]"
        )
        return True

    @staticmethod
    def _image_metadata(value: Any) -> list[dict[str, Any]] | None:
        if not isinstance(value, (list, tuple)):
            return None
        values = cast(list[Any] | tuple[Any, ...], value)
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    @classmethod
    def _screenshot_metadata_error(
        cls, images: list[dict[str, Any]], required: int
    ) -> str:
        if len(images) < required:
            return f"{cls.tracker} requires metadata for every required screenshot."
        if not cls._all_screenshot_formats_allowed(images):
            return f"{cls.tracker} requires screenshot uploads in .PNG or .TIFF and the current image metadata contains unsupported formats."
        if not cls._all_screenshot_links_complete(images):
            return f"{cls.tracker} requires thumbnail and full-size screenshot links (img_url + raw/web_url) in screenshot metadata."
        return ""

    @classmethod
    def _all_screenshot_formats_allowed(
        cls, images: list[dict[str, Any]]
    ) -> bool:
        return all(cls._is_allowed_screenshot_image(image) for image in images)

    @classmethod
    def _all_screenshot_links_complete(
        cls, images: list[dict[str, Any]]
    ) -> bool:
        return all(
            cls._has_valid_screenshot_thumb_and_full(image) for image in images
        )

    def _mediainfo_policy(self, meta: Meta, category: str) -> bool:
        valid = (
            category not in {"MOVIE", "TV"}
            or bool(meta.is_disc)
            or bool(meta.mediainfo)
        )
        if not valid:
            logger.info(
                f"{self.tracker}: [bold red]Movie/TV uploads must include mediainfo on {self.tracker}.[/bold red]"
            )
        return valid

    def _archive_policy(self, category: str, filelist: list[Any]) -> bool:
        archive = self._contains_archive_file(filelist)
        if category not in {"MOVIE", "TV"} or not archive:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Archive/multipart files are not allowed. Found: {archive}[/bold red]"
        )
        return False

    def _collection_policy(
        self, category: str, release_name: str, filelist: list[Any]
    ) -> bool:
        if category != "MOVIE" or not self._is_boxset_style(
            release_name, filelist
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Movie boxsets/collections are not allowed. Upload each movie separately.[/bold red]"
        )
        return False

    def _tv_pack_policy(self, meta: Meta, category: str) -> bool:
        if category != "TV" or not meta.tv_pack:
            return True
        status = self._is_tv_pack_ended(meta)
        if status is True:
            return True
        reason = (
            "Unable to confirm TV series status"
            if status is None
            else "TV packs are allowed only for completed series"
        )
        logger.info(
            f"{self.tracker}: [bold red]{reason} on {self.tracker}.[/bold red]"
        )
        return False

    def _folder_policy(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        if (
            category not in {"MOVIE", "TV"}
            or meta.is_disc
            or not meta.keep_folder
        ):
            return True
        if len(self._video_filelist(filelist)) > 1:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Single-file Movie/TV uploads should be uploaded without a folder on {self.tracker}.[/bold red]"
        )
        return False

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
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
        normalized = self._normalize_tracker_name(str(meta.name or ""))
        return {"name": self._localized_release_name(meta, normalized)}

    @classmethod
    def _localized_release_name(cls, meta: Meta, name: str) -> str:
        imdb_info = cls._imdb_mapping(meta.imdb_info)
        if not cls._uses_polish_title(meta, imdb_info):
            return name
        aka = cls._normalize_tracker_name(str(meta.aka or ""))
        localized = name.replace(aka, "") if aka else name
        title = cls._normalize_tracker_name(str(meta.title or ""))
        return localized.replace(title, str(imdb_info.get("aka", "")))

    @staticmethod
    def _imdb_mapping(value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @staticmethod
    def _uses_polish_title(meta: Meta, imdb_info: dict[str, Any]) -> bool:
        return meta.original_language == "pl" and bool(imdb_info)

    @staticmethod
    def _normalize_tracker_name(value: str) -> str:
        normalized = re.sub(r"[.()\[\]]+", " ", value)
        return re.sub(r"\s+", " ", normalized).strip()
