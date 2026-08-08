# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.console import logger
from src.languages import languages_manager
from src.meta import Meta
from src.trackers.common import Common
from src.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


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
        "dvdrip",
        "vodrip",
        "screener",
        "preair",
    )

    video_min_height_by_rule = {"480p", "480i", "576p", "576i", "sd"}

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="MIDNIGHTSCENE")
        self.config: Config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3",
            "GAME": "4",
        }
        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}

        resolved_category = category if category is not None and category != "" else meta.category
        resolved_id = category_id.get(resolved_category, "0")
        return {"category_id": resolved_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        nin_term = (bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()).upper()
        type_id = {
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
            f"{nin_term}": "11",
            "XBOX": "12",
            "DOCUMENTARY": "13",
            "TTRPG": "14",
            "3DPRINT": "15",
            "3D_PRINT": "15",
            "3D PRINT": "15",
            "OTHER": "16",
        }
        if mapping_only:
            return type_id
        if reverse:
            return {v: k for k, v in type_id.items()}

        if type:
            resolved_type = type.upper().strip().lstrip(".")
            if resolved_type in type_id:
                return {"type_id": type_id[resolved_type]}

        # Fallbacks
        genres = [g.lower() for g in meta.genres]
        keywords = [k.lower() for k in meta.keywords]

        if "documentary" in genres or "documentary" in keywords:
            val = "13"
        elif meta.category == "GAME":
            platform = meta.platform.lower()
            nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()

            if any(word in platform for word in ["playstation", "ps5", "ps4", "ps3", "ps2", "ps1", "psp", "vita"]):
                val = "10"
            elif "xbox" in platform:
                val = "12"
            elif any(word in platform for word in [f"{nin_term}", "switch", "wii", "3ds", "nds", "ds"]):
                val = "11"
            else:
                val = "9"  # PC
        elif meta.category == "MUSIC":
            release = meta.music_release if isinstance(meta.music_release, dict) else {}
            fields = release.get("fields", {}) if isinstance(release.get("fields"), dict) else {}
            format_field = fields.get("format", {}) if isinstance(fields.get("format"), dict) else {}
            music_format = str(meta.format or format_field.get("value", "") or meta.type or "").upper().strip().lstrip(".")
            val = type_id.get(music_format, "0")
        elif "FLAC" in (meta.audio or "").upper():
            val = "8"
        elif "MP3" in (meta.audio or "").upper():
            val = "7"
        else:
            meta_type = (meta.type or "").upper().strip().lstrip(".")
            val = type_id.get(meta_type, "0")

        return {"type_id": val}

    @staticmethod
    def _contains_unofficial_release_tag(meta: Meta) -> bool:
        title = str(meta.scene_name or meta.name or "").lower()
        uuid = str(meta.uuid).lower()
        for marker in MidnightScene.banned_release_markers:
            if re.search(rf"(?:^|[._ -]){re.escape(marker)}(?:$|[._ -])", title):
                return True
            if marker == "upscale":
                continue
            if f"{marker}" in uuid:
                return True
        return False

    @staticmethod
    def _files_contain(path_values: list[Any], suffixes: set[str]) -> bool:
        for path in path_values:
            filename = str(path).lower()
            if any(filename.endswith(suffix) for suffix in suffixes):
                return True
        return False

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        if meta.unattended:
            return bool(meta.unattended_confirm)
        logger.info(f"{self.tracker}: [red]{message}[/red]")
        return await self.common.prompt_user_for_confirmation("Do you want to continue anyway?", meta)

    async def get_additional_checks(self, meta: Meta) -> bool:
        # Block explicit adult uploads
        if meta.adult_media:
            logger.info(f"{self.tracker}: [yellow]Adult content is not accepted on this tracker.[/yellow]")
            return False

        # Minimum screenshot requirement for TV/Movie rule page requires samples in description
        if meta.category in {"TV", "MOVIE"} and meta.screens < 3:
            logger.info(f"{self.tracker}: [bold yellow]MidnightScene requires at least 3 sample images for TV and Movie uploads.[/bold yellow]")
            if not await self._confirm_or_skip("Less than 3 sample images were provided.", meta):
                return False

        # Reject clearly upscaled or unofficially sourced releases
        release_name = str(meta.name or "")
        title = release_name.lower()
        if "upscale" in str(meta.uuid).lower() and "upscale" not in title:
            logger.info(f"{self.tracker}: [yellow]Upscaled content is not accepted without explicit marking in the title.[/yellow]")
            if not await self._confirm_or_skip("This looks like an upscaled release.", meta):
                return False

        if self._contains_unofficial_release_tag(meta):
            logger.info(f"{self.tracker}: [yellow]Unofficial source tags (telesync/cam/etc) are not accepted.[/yellow]")
            if not await self._confirm_or_skip("Unofficial source tag detected in release title/uuid.", meta):
                return False

        # TV/Movie rule requires 720p/1080p/2160p or best available
        if meta.category in {"TV", "MOVIE"} and meta.resolution.lower() in self.video_min_height_by_rule and not meta.is_disc:
            logger.info(f"{self.tracker}: [yellow]Low-resolution releases should be uploaded only when no higher quality exists.[/yellow]")
            if not await self._confirm_or_skip("This release is below 720p.", meta):
                return False

        # Game uploads are scene-only, provided as original RAR with NFO+SFV
        if meta.category == "GAME":
            if not meta.scene:
                logger.info(f"{self.tracker}: [yellow]Only Scene releases are allowed for GAME on MidnightScene.[/yellow]")
                if not await self._confirm_or_skip("Game release is not marked as Scene.", meta):
                    return False

            files = [str(item) for item in (meta.filelist or [])]
            has_rar = self._files_contain(files, {".rar", ".r01", ".r00", ".r02", ".r03", ".r04", ".r05", ".r06", ".r07", ".r08", ".r09"})
            has_sfv = self._files_contain(files, {".sfv"})
            has_nfo = self._files_contain(files, {".nfo"})
            if not has_rar or not has_sfv or not has_nfo:
                logger.info(f"{self.tracker}: [yellow]Game uploads must be scene RAR with NFO and SFV.[/yellow]")
                if not await self._confirm_or_skip("Game payload is missing required RAR/NFO/SFV format.", meta):
                    return False

        return True

    async def get_name(self, meta: Meta):
        if meta.category == "MUSIC":
            # Scene titles retain their original segment separators; only the
            # artist/title underscores are rendered as spaces on the site.
            if meta.scene:
                scene_name = str(meta.scene_name or meta.basename_no_ext or meta.name or "").strip()
                if scene_name:
                    return {"name": scene_name.replace("_", " ")}

            release = meta.music_release if isinstance(meta.music_release, dict) else {}
            fields = release.get("fields", {}) if isinstance(release.get("fields"), dict) else {}

            def release_field(name: str, fallback: Any = "") -> str:
                field = fields.get(name, {}) if isinstance(fields.get(name), dict) else {}
                return str(field.get("value", fallback) or "").strip()

            artist = release_field("artist", meta.artist)
            title = release_field("album", meta.title)
            year = release_field("release_year", release_field("year", meta.year))
            catalogue = release_field("release_catalogue_number", release_field("catalogue_number", meta.music_catalogue_number))
            edition = release_field("edition", meta.manual_edition or meta.edition)
            media = release_field("media", meta.source)
            format_name = release_field("format", meta.format or meta.type).upper()

            name = " - ".join(part for part in (artist, title) if part)
            if year:
                name = f"{name} ({year})" if name else f"({year})"
            catalogue_edition = " - ".join(part for part in (catalogue, edition) if part)
            if catalogue_edition:
                name = f"{name} [{catalogue_edition}]" if name else f"[{catalogue_edition}]"
            media_format = " - ".join(part for part in (media, format_name) if part)
            if media_format:
                name = f"{name} [{media_format}]" if name else f"[{media_format}]"
            return {"name": name}

        ms_name: str = meta.name
        name_type: str = meta.type or ""
        source: str = meta.source or ""

        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)

        audio_languages: list[str] = [] if not meta.audio_languages else meta.audio_languages
        has_english_audio = await languages_manager.has_english_language(audio_languages)

        if audio_languages and not has_english_audio:
            ms_name = re.sub(r"\bDual-Audio\b", "", ms_name, flags=re.IGNORECASE)
            ms_name = " ".join(ms_name.split())
            foreign_lang = audio_languages[0].upper()
            if name_type == "REMUX" and source in ("PAL DVD", "NTSC DVD", "DVD"):
                if meta.year:
                    ms_name = ms_name.replace(str(meta.year), f"{meta.year!s} {foreign_lang}", 1)
            elif meta.is_disc != "BDMV":
                ms_name = ms_name.replace(meta.resolution, f"{foreign_lang} {meta.resolution}", 1)

        return {"name": ms_name}
