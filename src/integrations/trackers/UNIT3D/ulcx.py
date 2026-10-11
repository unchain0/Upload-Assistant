# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

import aiofiles

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class ULCX(UNIT3D):
    """
    upload.cx (ULCX) is a Private Torrent Tracker for MOVIES / TV
    """

    tracker = "ULCX"
    display_name = "ULCX"
    reject_english_original_bloat = True
    base_url = "https://upload.cx"
    banned_groups = (
        "4K4U",
        "Alcaide_Kira",
        "AROMA",
        "d3g",
        "EMBER",
        "FGT",
        "FnP",
        "FRDS",
        "Grym",
        "HDT",
        "Hi10",
        "iAHD",
        "INFINITY",
        "ION10",
        "iVy",
        "Judas",
        "LAMA",
        "MeGusta",
        "NAHOM",
        "Niblets",
        "nikt0",
        "OFT",
        "PHOCiS",
        "PiRaTeS",
        "QxR",
        "R&H",
        "RARBG",
        "seedpool",
        "Sicario",
        "SM737",
        "SPDVD",
        "SPx",
        "SWTYBLZ",
        "TAoE",
        "TGx",
        "Tigole",
        "TSP",
        "TSPxL",
        "VXT",
        "Vyndros",
        "Will1869",
        "x0r",
        "YIFY",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("upload.cx",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="ULCX")
        self.config = config

    async def get_additional_checks(self, meta: Meta) -> bool:
        terms = self._content_terms(meta)
        if not self._pre_media_policy_passes(meta, terms):
            return False
        if not self._release_and_media_policy_passes(meta, terms):
            return False
        if not await self._language_policy_passes(meta):
            return False
        if not self._post_media_policy_passes(meta):
            return False
        self._log_hybrid_remux_note(meta)
        return True

    def _pre_media_policy_passes(self, meta: Meta, terms: set[str]) -> bool:
        if not self._content_policy_passes(meta, terms):
            return False
        if not self._disc_structure_policy_passes(meta):
            return False
        return self._container_policy_passes(meta)

    def _release_and_media_policy_passes(
        self, meta: Meta, terms: set[str]
    ) -> bool:
        if not self._release_policy_passes(meta, terms):
            return False
        return self._media_policy_passes(meta)

    def _post_media_policy_passes(self, meta: Meta) -> bool:
        if not self._mediainfo_settings_policy_passes(meta):
            return False
        return self._personal_release_policy_passes(meta)

    @classmethod
    def _content_terms(cls, meta: Meta) -> set[str]:
        values = [
            *cls._string_list(meta.keywords),
            *cls._string_list(meta.genres),
        ]
        return {value.casefold() for value in values if value.strip()}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _content_policy_passes(self, meta: Meta, terms: set[str]) -> bool:
        if self._has_forbidden_content_term(terms):
            logger.info(
                f"{self.tracker}: [bold red]Concerts, live performances, and music videos are forbidden.[/bold red]"
            )
            return False
        if self._is_adult_content(meta):
            logger.info(
                f"{self.tracker}: [bold red]Adult / pornographic content is forbidden.[/bold red]"
            )
            return False
        if meta.pre_release:
            logger.info(
                f"{self.tracker}: [bold red]Camera recordings and pre-release content are forbidden.[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _has_forbidden_content_term(terms: set[str]) -> bool:
        forbidden = ("concert", "live performance", "music video", "musical")
        return any(keyword in term for keyword in forbidden for term in terms)

    @staticmethod
    def _is_adult_content(meta: Meta) -> bool:
        return bool(meta.adult_media) or bool(meta.tmdb_adult_media)

    def _disc_structure_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc == "BDMV" and meta.discs_missing_certificate:
            logger.info(
                f"{self.tracker}: [bold red]Disc source(s) missing BD certificate, skipping upload.[/bold red]"
            )
            return False
        if self._dvd_missing_video_ts(meta):
            logger.info(
                f"{self.tracker}: [bold red]DVD full-disc must contain a VIDEO_TS folder.[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _dvd_missing_video_ts(meta: Meta) -> bool:
        if meta.is_disc != "DVD" or not meta.filelist:
            return False
        return not any(
            "VIDEO_TS" in str(path).upper() for path in meta.filelist
        )

    def _container_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc or not meta.container:
            return True
        container = str(meta.container).casefold()
        if meta.type == "HDTV":
            return self._hdtv_container_policy_passes(container)
        if container == "mkv":
            return True
        logger.info(
            f"{self.tracker}: [bold red]All non-disc files must be .mkv (found '.{container}').[/bold red]"
        )
        return False

    def _hdtv_container_policy_passes(self, container: str) -> bool:
        if container in {"mkv", "ts"}:
            return True
        logger.info(
            f"{self.tracker}: [bold red]HDTV uploads must be .mkv or .ts.[/bold red]"
        )
        return False

    def _release_policy_passes(self, meta: Meta, terms: set[str]) -> bool:
        if self._banned_encode_group(meta):
            logger.info(
                f"{self.tracker}: [bold red]Encodes from {meta.tag} are not allowed.[/bold red]"
            )
            return False
        if self._is_dvdrip(meta):
            self._log_attended(meta, "DVDRIPs are not allowed.")
            return False
        if not self._encode_resolution_policy_passes(meta):
            return False
        return self._encode_codec_policy_passes(meta, terms)

    @staticmethod
    def _banned_encode_group(meta: Meta) -> bool:
        if meta.type != "ENCODE" or not meta.tag:
            return False
        return meta.tag[1:].casefold() in {"edge2020", "nubz", "ralphy"}

    @staticmethod
    def _is_dvdrip(meta: Meta) -> bool:
        release_type = str(meta.type or "").casefold()
        return "dvd" in release_type and "rip" in release_type

    def _encode_resolution_policy_passes(self, meta: Meta) -> bool:
        if meta.type != "ENCODE":
            return True
        height = int(meta.video_height or 0)
        if height <= 0 or height >= 720:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Encodes must be at least 720p resolution. Standard definition encodes are forbidden.[/bold red]"
        )
        return False

    def _encode_codec_policy_passes(self, meta: Meta, terms: set[str]) -> bool:
        if meta.type != "ENCODE":
            return True
        reason = self._encode_codec_failure(meta, terms)
        if not reason:
            return True
        logger.info(f"{self.tracker}: [bold red]{reason}[/bold red]")
        return False

    def _encode_codec_failure(self, meta: Meta, terms: set[str]) -> str:
        codec = str(meta.video_codec or "").upper()
        is_animation = self._is_animation(meta, terms)
        if self._invalid_hevc_codec(meta, codec, is_animation):
            return "x265 (HEVC) for live-action encodes is permitted ONLY if source is UHD (2160p)."
        if self._invalid_av1_codec(codec, is_animation):
            return "AV1 codec is permitted ONLY for animated content. Live-action AV1 encodes are forbidden."
        return ""

    def _invalid_hevc_codec(
        self, meta: Meta, codec: str, is_animation: bool
    ) -> bool:
        if codec != "HEVC":
            return False
        return self._invalid_live_action_hevc(meta, is_animation)

    @staticmethod
    def _invalid_av1_codec(codec: str, is_animation: bool) -> bool:
        return codec == "AV1" and not is_animation

    @staticmethod
    def _is_animation(meta: Meta, terms: set[str]) -> bool:
        return bool(meta.anime) or "animation" in terms

    @staticmethod
    def _invalid_live_action_hevc(meta: Meta, is_animation: bool) -> bool:
        if is_animation or meta.uhd or meta.resolution == "2160p":
            return False
        return int(meta.video_height or 0) < 2160

    def _media_policy_passes(self, meta: Meta) -> bool:
        audio_tracks = self._audio_tracks(meta)
        subtitle_tracks = self._subtitle_tracks(meta)
        reason = self._audio_policy_reason(meta, audio_tracks)
        if reason:
            logger.info(f"{self.tracker}: [bold red]{reason}[/bold red]")
            return False
        return self._subtitle_policy_passes(meta, subtitle_tracks)

    @classmethod
    def _audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            track
            for track in cls._media_tracks(meta)
            if track.get("@type") == "Audio"
        ]

    @classmethod
    def _subtitle_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            track
            for track in cls._media_tracks(meta)
            if track.get("@type") in {"Text", "Subtitle"}
        ]

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        return cls._track_mappings(media.get("track", []))

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return media if isinstance(media, dict) else {}

    @staticmethod
    def _track_mappings(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [track for track in value if isinstance(track, dict)]

    def _audio_policy_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        checks = (
            self._non_disc_lpcm_reason,
            self._non_disc_flac_reason,
            self._remux_audio_reason,
            self._encode_audio_reason,
            self._truehd_compatibility_reason,
        )
        return next(
            (reason for check in checks if (reason := check(meta, tracks))), ""
        )

    def _non_disc_lpcm_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        if meta.is_disc:
            return ""
        return (
            "LPCM audio tracks are not allowed on non-disc uploads."
            if any(self._format(track) in {"PCM", "LPCM"} for track in tracks)
            else ""
        )

    def _non_disc_flac_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        if meta.is_disc:
            return ""
        invalid = any(
            self._format(track) == "FLAC" and self._channels(track) > 2
            for track in tracks
        )
        return (
            "FLAC audio is allowed ONLY for Mono or Stereo (1 or 2 channels) content."
            if invalid
            else ""
        )

    def _remux_audio_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        if meta.type != "REMUX":
            return ""
        return next(
            (
                reason
                for track in tracks
                if (reason := self._remux_track_reason(track))
            ),
            "",
        )

    def _remux_track_reason(self, track: dict[str, Any]) -> str:
        if not self._is_lossless(track):
            return ""
        fmt = self._format(track)
        channels = self._channels(track)
        reason = self._remux_stereo_reason(fmt, channels)
        if reason:
            return reason
        reason = self._remux_mono_reason(fmt, channels)
        return reason or self._remux_multichannel_reason(fmt, channels)

    @staticmethod
    def _remux_stereo_reason(fmt: str, channels: int) -> str:
        if channels != 2 or fmt == "FLAC":
            return ""
        return f"Remux lossless stereo track ({fmt}) must be converted to FLAC 2.0."

    @staticmethod
    def _remux_mono_reason(fmt: str, channels: int) -> str:
        if channels != 1 or fmt not in {"PCM", "LPCM", "TRUEHD"}:
            return ""
        return f"Remux lossless mono track ({fmt}) must be converted to FLAC 1.0 or DTS-HD MA 1.0."

    @staticmethod
    def _remux_multichannel_reason(fmt: str, channels: int) -> str:
        if channels <= 2 or fmt not in {"FLAC", "PCM", "LPCM"}:
            return ""
        return f"Remux multi-channel lossless track cannot be {fmt} (must be DTS-HD MA or TrueHD)."

    def _encode_audio_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        if meta.type != "ENCODE":
            return ""
        if not self._is_1080p_or_lower(meta):
            return ""
        invalid = any(
            self._invalid_encode_audio_track(track) for track in tracks
        )
        return (
            "Lossless multi-channel audio is not permitted on 1080p or lower encodes."
            if invalid
            else ""
        )

    def _invalid_encode_audio_track(self, track: dict[str, Any]) -> bool:
        if not self._is_lossless(track):
            return False
        return self._channels(track) > 2

    @staticmethod
    def _is_1080p_or_lower(meta: Meta) -> bool:
        if str(meta.resolution or "").casefold() in {"720p", "1080p", "1080i"}:
            return True
        height = int(meta.video_height or 0)
        return 0 < height <= 1080

    def _truehd_compatibility_reason(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> str:
        if meta.is_disc:
            return ""
        if not self._has_audio_format(tracks, {"TRUEHD"}):
            return ""
        return (
            ""
            if self._has_audio_format(tracks, {"AC-3", "AC3"})
            else "TrueHD audio tracks must include an AC3 compatibility track."
        )

    def _has_audio_format(
        self, tracks: list[dict[str, Any]], formats: set[str]
    ) -> bool:
        return any(self._format(track) in formats for track in tracks)

    @staticmethod
    def _format(track: dict[str, Any]) -> str:
        return str(track.get("Format", "")).upper()

    @staticmethod
    def _channels(track: dict[str, Any]) -> int:
        value = (
            track.get("Channels_Original")
            or track.get("Channels")
            or track.get("Channel(s)")
            or 0
        )
        match = re.search(r"\d+", str(value))
        return int(match.group(0)) if match else 0

    @classmethod
    def _is_lossless(cls, track: dict[str, Any]) -> bool:
        fmt = cls._format(track)
        profile = str(track.get("Format_Profile", "")).upper()
        if fmt in {"PCM", "LPCM", "TRUEHD", "FLAC"}:
            return True
        return fmt.startswith("DTS") and "MA" in profile

    def _subtitle_policy_passes(
        self, meta: Meta, tracks: list[dict[str, Any]]
    ) -> bool:
        if not self._default_subtitles_forbidden(meta):
            return True
        if not any(self._is_default_subtitle(track) for track in tracks):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Subtitles should not be marked default on English content.[/bold red]"
        )
        return False

    @staticmethod
    def _default_subtitles_forbidden(meta: Meta) -> bool:
        if not meta.personalrelease or meta.is_disc:
            return False
        language = str(
            meta.original_language or meta.language or ""
        ).casefold()
        return language in {"en", "eng", "english"}

    @staticmethod
    def _is_default_subtitle(track: dict[str, Any]) -> bool:
        return str(track.get("Default", "")).casefold() == "yes"

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if meta.is_disc:
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
        )

    def _mediainfo_settings_policy_passes(self, meta: Meta) -> bool:
        if meta.valid_mi_settings:
            return True
        logger.info(
            f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping upload.[/bold red]"
        )
        return False

    def _personal_release_policy_passes(self, meta: Meta) -> bool:
        if not meta.personalrelease:
            return True
        if meta.has_multiple_default_audio_tracks:
            logger.info(
                f"{self.tracker}: [bold red]Multiple default audio tracks detected, skipping upload.[/bold red]"
            )
            return False
        if meta.has_multiple_default_subtitle_tracks:
            logger.info(
                f"{self.tracker}: [bold red]Multiple default subtitle tracks detected, skipping upload.[/bold red]"
            )
            return False
        return True

    def _log_hybrid_remux_note(self, meta: Meta) -> None:
        if meta.type != "REMUX":
            return
        if self._is_hybrid_release(meta):
            logger.info(
                f"{self.tracker}: [yellow]WEB DV/HDR10+ Hybrid Remuxes require a grade check as per rule 3.4.1.[/yellow]"
            )

    @staticmethod
    def _is_hybrid_release(meta: Meta) -> bool:
        return (
            "hybrid" in str(meta.edition or "").casefold()
            or "hybrid" in str(meta.name or "").casefold()
            or bool(meta.webdv)
        )

    def _log_attended(self, meta: Meta, message: str) -> None:
        if not meta.unattended:
            logger.info(f"{self.tracker}: [bold red]{message}[/bold red]")

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_description(self, meta: Meta) -> dict[str, str]:
        desc = await DescriptionBuilder(
            self.tracker, self.config
        ).general_description_generator(
            meta,
            mediainfo=False,
            nfo=False,
        )

        if meta.adult_media:
            pattern = r"(\[center\](?:(?!\[/center\]).)*\[/center\])"

            def wrap_in_spoiler(match: re.Match[str]) -> str:
                center_block = match.group(1)
                if "[img" not in center_block.lower():
                    return center_block
                return f"[center][spoiler=Screenshots]{center_block}[/spoiler][/center]"

            desc = re.sub(pattern, wrap_in_spoiler, desc, flags=re.DOTALL)
            async with aiofiles.open(
                f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt",
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(desc)

        return {"description": desc}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        imdb = self._imdb_info(meta)
        name = self._apply_imdb_identity(meta.name, meta, imdb)
        name = self._strip_webdl_hybrid(name, meta)
        name = self._apply_imdb_year(name, meta, imdb)
        return {"name": name}

    @staticmethod
    def _imdb_info(meta: Meta) -> dict[str, Any]:
        return meta.imdb_info if isinstance(meta.imdb_info, dict) else {}

    @classmethod
    def _apply_imdb_identity(
        cls, name: str, meta: Meta, imdb: dict[str, Any]
    ) -> str:
        imdb_name = str(imdb.get("title", "")).strip()
        if not imdb_name:
            return name
        name = cls._strip_aka(name, meta.aka)
        name = name.replace(str(meta.title), imdb_name, 1)
        return cls._insert_imdb_aka(
            name, imdb_name, str(imdb.get("aka", "")).strip(), meta
        )

    @staticmethod
    def _strip_aka(name: str, aka: str) -> str:
        return name.replace(f"{aka} ", "", 1) if aka else name

    @staticmethod
    def _insert_imdb_aka(
        name: str, imdb_name: str, imdb_aka: str, meta: Meta
    ) -> str:
        if not imdb_aka or imdb_aka == imdb_name or meta.no_aka or meta.anime:
            return name
        return name.replace(imdb_name, f"{imdb_name} AKA {imdb_aka}", 1)

    @classmethod
    def _strip_webdl_hybrid(cls, name: str, meta: Meta) -> str:
        if meta.type != "WEBDL":
            return name
        if "Hybrid" not in name and not cls._is_hybrid_release(meta):
            return name
        return name.replace("Hybrid ", "", 1)

    @classmethod
    def _apply_imdb_year(
        cls, name: str, meta: Meta, imdb: dict[str, Any]
    ) -> str:
        if meta.category == "TV":
            return name
        years = cls._replacement_years(meta, imdb)
        if years is None:
            return name
        local_year, imdb_year = years
        return name.replace(local_year, imdb_year, 1)

    @staticmethod
    def _replacement_years(
        meta: Meta, imdb: dict[str, Any]
    ) -> tuple[str, str] | None:
        imdb_year = str(imdb.get("year", "")).strip()
        local_year = "" if meta.year is None else str(meta.year)
        if not imdb_year or not local_year:
            return None
        if imdb_year == local_year:
            return None
        return local_year, imdb_year
