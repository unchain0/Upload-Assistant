# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]

_CATEGORY_IDS = {"MOVIE": "1", "TV": "2", "MUSIC": "3", "GAME": "4"}
_NIN_TERM = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode().upper()
_TYPE_IDS = {
    "DISC": "1",
    "REMUX": "2",
    "ENCODE": "3",
    "WEBDL": "4",
    "WEBRIP": "5",
    "HDTV": "6",
    "MP3": "7",
    "FLAC": "8",
    "PC": "9",
    "PLAYSTATION": "10",
    _NIN_TERM: "11",
    "XBOX": "12",
    "DOCUMENTARY": "13",
    "TTRPG": "14",
    "3DPRINT": "15",
    "3D_PRINT": "15",
    "3D PRINT": "15",
    "OTHER": "16",
}
_PLAYSTATION_MARKERS = (
    "playstation",
    "ps5",
    "ps4",
    "ps3",
    "ps2",
    "ps1",
    "psp",
    "vita",
)
_NIN_MARKERS = (_NIN_TERM.lower(), "switch", "wii", "3ds", "nds", "ds")
_RAR_SUFFIXES = {
    ".rar",
    ".r01",
    ".r00",
    ".r02",
    ".r03",
    ".r04",
    ".r05",
    ".r06",
    ".r07",
    ".r08",
    ".r09",
}


class MidnightScene(UNIT3D):
    """
    MidnightScene is a Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "MIDNIGHTSCENE"
    display_name = "MidnightScene"
    allows_bloated_audio = True
    base_url = "https://midnightscene.cc"
    banned_groups = (
        "4K4U",
        "AROMA",
        "aXXo",
        "BONE",
        "BRrip",
        "CK4",
        "CM8",
        "core",
        "CrEwSaDe",
        "d3g",
        "DNL",
        "EMBER",
        "EVO",
        "FaNGDiNG0",
        "FGT",
        "FooKaS",
        "FRDS",
        "FROZEN",
        "GalaxyRG",
        "Grym",
        "GrymLegacy",
        "HD2DVD",
        "HDTime",
        "ION10",
        "Judas",
        "LAMA",
        "Leffe",
        "LycanHD",
        "MeGusta",
        "MezRips",
        "mHD",
        "msd",
        "mSD",
        "NeXus",
        "NhaNc3",
        "nHD",
        "nikt0",
        "nSD",
        "OFT",
        "OsC",
        "PRODJi",
        "ProRes",
        "PYC",
        "QxR",
        "RARBG",
        "RCDiVX",
        "RDN",
        "SAMPA",
        "SANTi",
        "Sicario",
        "Silence",
        "SM737",
        "STUTTERSHIT",
        "Tigole",
        "TSP",
        "TSPxL",
        "UTR",
        "ViSION",
        "WAF",
        "Will1869",
        "x0r",
        "YIFY",
        "YTS",
        "ZMNT",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    supported_categories = ("TV", "MOVIE", "GAME", "MUSIC")
    tracker_urls = ("midnightscene.cc",)

    banned_release_markers: tuple[str, ...] = (
        "cam",
        "telesync",
        "ts",
        "telecine",
        "tc",
        "r5",
        "dvdscr",
        "screener",
        "preair",
    )

    video_min_height_by_rule: frozenset[str] = frozenset(
        {"480p", "480i", "576p", "576i", "sd"}
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="MIDNIGHTSCENE")
        self.config: Config = config
        self.common = Common(config)

    @staticmethod
    def _selected_category(meta: Meta, category: str | None) -> str:
        if category:
            return category
        return meta.category

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return _CATEGORY_IDS
        if reverse:
            return {value: key for key, value in _CATEGORY_IDS.items()}
        resolved = self._selected_category(meta, category)
        return {"category_id": _CATEGORY_IDS.get(resolved, "0")}

    @staticmethod
    def _normalized_type(value: object) -> str:
        return str(value or "").upper().strip().lstrip(".")

    @staticmethod
    def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
        return any(marker in value for marker in markers)

    @classmethod
    def _game_type_id(cls, meta: Meta) -> str:
        platform = meta.platform.lower()
        if cls._contains_marker(platform, _PLAYSTATION_MARKERS):
            return "10"
        if "xbox" in platform:
            return "12"
        if cls._contains_marker(platform, _NIN_MARKERS):
            return "11"
        return "9"

    @classmethod
    def _music_format(cls, meta: Meta) -> str:
        fields = cls._music_fields(meta)
        fallback = meta.format if meta.format else meta.type
        value = cls._music_field(fields, "format", fallback)
        return cls._normalized_type(value)

    @staticmethod
    def _is_documentary(meta: Meta) -> bool:
        values = [*meta.genres, *meta.keywords]
        return any(str(value).lower() == "documentary" for value in values)

    @classmethod
    def _category_type_id(cls, meta: Meta) -> str | None:
        if meta.category == "GAME":
            return cls._game_type_id(meta)
        if meta.category == "MUSIC":
            return _TYPE_IDS.get(cls._music_format(meta), "0")
        return None

    @staticmethod
    def _audio_type_id(meta: Meta) -> str | None:
        audio = (meta.audio or "").upper()
        if "FLAC" in audio:
            return "8"
        if "MP3" in audio:
            return "7"
        return None

    @classmethod
    def _fallback_type_id(cls, meta: Meta) -> str:
        if cls._is_documentary(meta):
            return "13"
        category_type = cls._category_type_id(meta)
        if category_type is not None:
            return category_type
        audio_type = cls._audio_type_id(meta)
        if audio_type is not None:
            return audio_type
        return _TYPE_IDS.get(cls._normalized_type(meta.type), "0")

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if mapping_only:
            return _TYPE_IDS
        if reverse:
            return {value: key for key, value in _TYPE_IDS.items()}
        requested = self._normalized_type(type)
        if requested in _TYPE_IDS:
            return {"type_id": _TYPE_IDS[requested]}
        return {"type_id": self._fallback_type_id(meta)}

    @classmethod
    def _contains_unofficial_release_tag(cls, meta: Meta) -> bool:
        values = (
            str(meta.scene_name or meta.name or "").lower(),
            str(meta.uuid).lower(),
        )
        return any(
            cls._contains_release_marker(value, marker)
            for value in values
            for marker in cls.banned_release_markers
        )

    @staticmethod
    def _contains_release_marker(value: str, marker: str) -> bool:
        return (
            re.search(rf"(?:^|[._ -]){re.escape(marker)}(?:$|[._ -])", value)
            is not None
        )

    @staticmethod
    def _files_contain(path_values: list[Any], suffixes: set[str]) -> bool:
        for path in path_values:
            filename = str(path).lower()
            if any(filename.endswith(suffix) for suffix in suffixes):
                return True
        return False

    @staticmethod
    def _collapsed_name(name: str) -> str:
        return " ".join((name or "").split())

    @staticmethod
    def _aka_name(aka: str) -> str:
        return re.sub(r"^AKA\s+", "", str(aka), flags=re.IGNORECASE).strip()

    @classmethod
    def _aka_year_match(
        cls, name: str, title: str, aka_name: str, year: str | int
    ) -> re.Match[str] | None:
        return re.search(
            rf"^(?P<title>{re.escape(title.strip())})\s+{re.escape(str(year))}\s+AKA\s+{re.escape(aka_name)}(?P<suffix>\s+.*)?$",
            name,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _can_normalize_aka_year(
        name: str, title: str, aka: str, year: str | int | None
    ) -> bool:
        return bool(name and title and aka and year)

    @classmethod
    def _normalize_aka_year_order(
        cls, name: str, title: str, aka: str, year: str | int | None
    ) -> str:
        if not cls._can_normalize_aka_year(name, title, aka, year):
            return cls._collapsed_name(name)
        aka_name = cls._aka_name(aka)
        if not aka_name:
            return cls._collapsed_name(name)
        match = cls._aka_year_match(
            name, title, aka_name, cast(str | int, year)
        )
        if match is None:
            return cls._collapsed_name(name)
        title_from_name = match.group("title").strip()
        suffix = (match.group("suffix") or "").strip()
        return cls._collapsed_name(
            f"{title_from_name} AKA {aka_name} {year} {suffix}"
        )

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [red]{message}[/red]")
        return await self.common.prompt_user_for_confirmation(
            "Do you want to continue anyway?", meta
        )

    @staticmethod
    def _validated_filelist(meta: Meta) -> list[str] | None:
        raw = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw, (list, tuple, set)):
            return None
        return [str(item) for item in raw]

    async def _check_screenshots(self, meta: Meta) -> bool:
        try:
            count = int(meta.screens)
        except TypeError, ValueError, OverflowError:
            count = 0
        if meta.category not in {"TV", "MOVIE"} or count >= 3:
            return True
        logger.info(
            f"{self.tracker}: [bold yellow]MidnightScene requires at least 3 sample images for TV and Movie uploads.[/bold yellow]"
        )
        return await self._confirm_or_skip(
            "Less than 3 sample images were provided.", meta
        )

    async def _check_upscale(self, meta: Meta) -> bool:
        title = str(meta.name or "").lower()
        if "upscale" not in str(meta.uuid).lower() or "upscale" in title:
            return True
        logger.info(
            f"{self.tracker}: [yellow]Upscaled content is not accepted without explicit marking in the title.[/yellow]"
        )
        return await self._confirm_or_skip(
            "This looks like an upscaled release.", meta
        )

    async def _check_unofficial_source(self, meta: Meta) -> bool:
        if not self._contains_unofficial_release_tag(meta):
            return True
        logger.info(
            f"{self.tracker}: [yellow]Unofficial source tags (telesync/cam/etc) are not accepted.[/yellow]"
        )
        return await self._confirm_or_skip(
            "Unofficial source tag detected in release title/uuid.", meta
        )

    @classmethod
    def _is_low_resolution(cls, meta: Meta) -> bool:
        resolution = str(meta.resolution or "").lower()
        if resolution in cls.video_min_height_by_rule:
            return True
        match = re.fullmatch(r"(\d{3,4})[pi]?", resolution)
        return bool(match and int(match.group(1)) < 720)

    async def _check_resolution(self, meta: Meta) -> bool:
        if meta.category not in {"TV", "MOVIE"}:
            return True
        if meta.is_disc or not self._is_low_resolution(meta):
            return True
        logger.info(
            f"{self.tracker}: [yellow]Low-resolution releases should be uploaded only when no higher quality exists.[/yellow]"
        )
        return await self._confirm_or_skip("This release is below 720p.", meta)

    async def _check_game_scene(self, meta: Meta) -> bool:
        if meta.scene:
            return True
        logger.info(
            f"{self.tracker}: [yellow]Only Scene releases are allowed for GAME on MidnightScene.[/yellow]"
        )
        return await self._confirm_or_skip(
            "Game release is not marked as Scene.", meta
        )

    @classmethod
    def _game_payload_complete(cls, files: list[str]) -> bool:
        return (
            cls._files_contain(files, _RAR_SUFFIXES)
            and cls._files_contain(files, {".sfv"})
            and cls._files_contain(files, {".nfo"})
        )

    async def _check_game_payload(self, meta: Meta, files: list[str]) -> bool:
        if self._game_payload_complete(files):
            return True
        logger.info(
            f"{self.tracker}: [yellow]Game uploads must be scene RAR with NFO and SFV.[/yellow]"
        )
        return await self._confirm_or_skip(
            "Game payload is missing required RAR/NFO/SFV format.", meta
        )

    async def _check_game(self, meta: Meta, files: list[str]) -> bool:
        if meta.category != "GAME":
            return True
        if not await self._check_game_scene(meta):
            return False
        return await self._check_game_payload(meta, files)

    async def _run_policy_checks(self, meta: Meta) -> bool:
        for check in (
            self._check_screenshots,
            self._check_upscale,
            self._check_unofficial_source,
            self._check_resolution,
        ):
            if not await check(meta):
                return False
        return True

    async def get_additional_checks(self, meta: Meta) -> bool:
        files = self._validated_filelist(meta)
        if files is None:
            logger.info(
                f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
            )
            return False
        if meta.adult_media:
            logger.info(
                f"{self.tracker}: [yellow]Adult content is not accepted on this tracker.[/yellow]"
            )
            return False
        if not await self._run_policy_checks(meta):
            return False
        return await self._check_game(meta, files)

    @staticmethod
    def _music_fields(meta: Meta) -> dict[str, Any]:
        release = (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )
        fields_raw = release.get("fields", {})
        if not isinstance(fields_raw, dict):
            return {}
        return cast(dict[str, Any], fields_raw)

    @staticmethod
    def _music_field(
        fields: dict[str, Any], name: str, fallback: Any = ""
    ) -> str:
        raw = fields.get(name, {})
        field = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        return str(field.get("value", fallback) or "").strip()

    @classmethod
    def _music_release_parts(cls, meta: Meta) -> tuple[str, ...]:
        fields = cls._music_fields(meta)
        artist = cls._music_field(fields, "artist", meta.artist)
        title = cls._music_field(fields, "album", meta.title)
        year = cls._music_field(
            fields, "release_year", cls._music_field(fields, "year", meta.year)
        )
        catalogue = cls._music_field(
            fields,
            "release_catalogue_number",
            cls._music_field(
                fields, "catalogue_number", meta.music_catalogue_number
            ),
        )
        edition = cls._music_field(
            fields, "edition", meta.manual_edition or meta.edition
        )
        media = cls._music_field(fields, "media", meta.source)
        format_name = cls._music_field(
            fields, "format", meta.format or meta.type
        ).upper()
        return artist, title, year, catalogue, edition, media, format_name

    @staticmethod
    def _append_year(name: str, year: str) -> str:
        if not year:
            return name
        return f"{name} ({year})" if name else f"({year})"

    @staticmethod
    def _append_bracket(name: str, value: str) -> str:
        if not value:
            return name
        return f"{name} [{value}]" if name else f"[{value}]"

    @staticmethod
    def _join_parts(*parts: str) -> str:
        return " - ".join(filter(None, parts))

    @classmethod
    def _music_directory_name(cls, meta: Meta) -> str:
        artist, title, year, catalogue, edition, media, format_name = (
            cls._music_release_parts(meta)
        )
        name = cls._join_parts(artist, title)
        name = cls._append_year(name, year)
        name = cls._append_bracket(name, cls._join_parts(catalogue, edition))
        return cls._append_bracket(name, cls._join_parts(media, format_name))

    @staticmethod
    def _scene_music_name(meta: Meta) -> str:
        for value in (meta.scene_name, meta.basename_no_ext, meta.name):
            if value:
                return str(value).strip().replace("_", " ")
        return ""

    @classmethod
    def _music_name(cls, meta: Meta) -> str:
        if meta.scene:
            scene_name = cls._scene_music_name(meta)
            if scene_name:
                return scene_name
        return cls._music_directory_name(meta)

    async def _audio_languages(self, meta: Meta) -> list[str]:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        return [] if not meta.audio_languages else meta.audio_languages

    @staticmethod
    def _remove_dual_audio(name: str) -> str:
        return " ".join(
            re.sub(r"\bDual-Audio\b", "", name, flags=re.IGNORECASE).split()
        )

    @staticmethod
    def _is_dvd_remux(meta: Meta) -> bool:
        return (meta.type or "") == "REMUX" and (meta.source or "") in {
            "PAL DVD",
            "NTSC DVD",
            "DVD",
        }

    @staticmethod
    def _dvd_foreign_language_name(
        meta: Meta, name: str, language: str
    ) -> str:
        if not meta.year:
            return name
        return name.replace(str(meta.year), f"{meta.year!s} {language}", 1)

    @classmethod
    def _foreign_language_name(
        cls, meta: Meta, name: str, language: str
    ) -> str:
        if cls._is_dvd_remux(meta):
            return cls._dvd_foreign_language_name(meta, name, language)
        if meta.is_disc == "BDMV":
            return name
        return name.replace(
            meta.resolution, f"{language} {meta.resolution}", 1
        )

    async def _video_name(self, meta: Meta) -> str:
        name = str(meta.name)
        audio_languages = await self._audio_languages(meta)
        has_english = await languages_manager.has_english_language(
            audio_languages
        )
        if audio_languages and not has_english:
            name = self._remove_dual_audio(name)
            name = self._foreign_language_name(
                meta, name, audio_languages[0].upper()
            )
        return self._normalize_aka_year_order(
            name,
            title=str(meta.title or ""),
            aka=str(meta.aka or ""),
            year=meta.year,
        )

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if meta.category == "MUSIC":
            return {"name": self._music_name(meta)}
        return {"name": await self._video_name(meta)}
