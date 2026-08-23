# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
import unicodedata
from pathlib import Path
from typing import Any, ClassVar

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class NordicQuality(UNIT3D):
    """NordicQuality UNIT3D tracker adapter."""

    tracker = "NORDICQUALITY"
    display_name = "NordicQuality"
    base_url = "https://nordicq.org"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "MUSIC", "BOOK", "GAME")
    tracker_urls = (base_url,)
    KNOWN_MEDIA_EXTENSIONS: ClassVar[frozenset[str]] = frozenset(
        {
            ".avi",
            ".flv",
            ".m2ts",
            ".m4v",
            ".mkv",
            ".mov",
            ".mp4",
            ".mpeg",
            ".mpg",
            ".rm",
            ".rmvb",
            ".ts",
            ".vob",
            ".webm",
            ".wmv",
        }
    )
    NORDIC_SUBTITLE_LANGUAGES: ClassVar[list[str]] = [
        "da",
        "dan",
        "danish",
        "fi",
        "fin",
        "finnish",
        "ice",
        "icelandic",
        "is",
        "isl",
        "no",
        "nno",
        "nob",
        "nor",
        "norwegian",
        "sv",
        "swe",
        "swedish",
    ]

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name=self.tracker)

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.category not in {"MOVIE", "TV"}:
            return True

        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=self.NORDIC_SUBTITLE_LANGUAGES,
            check_subtitle=True,
        )

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3",
            "GAME": "4",
            "BOOK": "7",
            "AUDIOBOOK": "8",
        }

    @staticmethod
    def _resolved_category(meta: Meta, category: str) -> str:
        resolved = category or meta.category
        if resolved == "BOOK" and meta.audiobook:
            return "AUDIOBOOK"
        return resolved

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = self._category_mapping()
        if mapping_only:
            return category_id
        if reverse:
            return {value: key for key, value in category_id.items()}
        resolved = self._resolved_category(meta, category)
        return {"category_id": category_id.get(resolved, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "DVDRIP": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "MP3": "7",
            "FLAC": "8",
            "EPUB": "9",
            "PDF": "10",
            "WINDOWS": "11",
            "MAC": "12",
            "MACOS": "12",
            "ANDROID": "13",
            "IOS": "14",
            "OTHER": "15",
            "LINUX": "17",
            "CONSOLE": "18",
        }

    @staticmethod
    def _normalized_type(value: str) -> str:
        return value.upper().strip().lstrip(".")

    @classmethod
    def _game_type(cls, meta: Meta) -> str:
        if meta.console_game:
            return "CONSOLE"
        platform = meta.platform.lower()
        rules = (
            ("WINDOWS", ("windows", "pc")),
            ("LINUX", ("linux",)),
            ("MAC", ("mac",)),
            ("ANDROID", ("android",)),
            ("IOS", ("ios",)),
        )
        for resolved_type, tokens in rules:
            if any(token in platform for token in tokens):
                return resolved_type
        return "OTHER"

    @classmethod
    def _resolved_type(cls, meta: Meta) -> str:
        if meta.category in {"MUSIC", "BOOK"}:
            return cls._normalized_type(meta.format)
        if meta.category == "GAME":
            return cls._game_type(meta)
        return cls._normalized_type(meta.type) if meta.type else ""

    @staticmethod
    def _type_fallback(meta: Meta) -> str:
        return "15" if meta.category in {"MUSIC", "BOOK", "GAME"} else "0"

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = self._type_mapping()
        if mapping_only:
            return type_id
        if reverse:
            return {value: key for key, value in type_id.items()}
        if type:
            return {"type_id": type_id.get(self._normalized_type(type), "0")}
        resolved = self._resolved_type(meta)
        return {"type_id": type_id.get(resolved, self._type_fallback(meta))}

    @staticmethod
    def _single_media_name(meta: Meta) -> str:
        if meta.is_disc or len(meta.filelist) != 1:
            return ""
        media_path = meta.filelist[0]
        if not isinstance(media_path, str) or not media_path.strip():
            return ""
        return Path(media_path).name

    @classmethod
    def _strip_known_media_extension(cls, source_name: str) -> str:
        extension = Path(source_name).suffix
        if extension.casefold() not in cls.KNOWN_MEDIA_EXTENSIONS:
            return source_name
        return source_name[: -len(extension)]

    @classmethod
    def _release_name_source(cls, meta: Meta) -> str:
        if meta.category not in {"MOVIE", "TV"}:
            return Path(meta.uuid or meta.name).stem
        source_name = cls._single_media_name(meta)
        if not source_name:
            source_name = Path(meta.uuid or meta.name).name
        return cls._strip_known_media_extension(source_name)

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self._release_name_source(meta).replace(" ", ".")

        name = name.translate(
            str.maketrans(
                {
                    "\u00c6": "AE",
                    "\u00e6": "ae",
                    "\u00d0": "D",
                    "\u00f0": "d",
                    "\u00d8": "O",
                    "\u00f8": "o",
                    "\u00de": "TH",
                    "\u00fe": "th",
                    "\u00c5": "A",
                    "\u00e5": "a",
                    "\u0152": "OE",
                    "\u0153": "oe",
                    "\u00df": "ss",
                }
            )
        )

        name = (
            name.replace("HDR10+", "HDR10P")
            .replace("DD+", "DDP")
            .replace("DTS:X", "DTS-X")
            .replace("&", "and")
        )
        name = (
            unicodedata.normalize("NFKD", name)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        name = re.sub(r"[^A-Za-z0-9._()\-]+", ".", name)
        name = re.sub(r"\.{2,}", ".", name).strip(".")

        return {"name": name}
