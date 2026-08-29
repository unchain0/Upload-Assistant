# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

import anitopy
import cli_ui
import guessit

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common

guessit_module: Any = cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


TRACKER_DISC_REQUIREMENTS = {
    "ULCX": {"region": "mandatory", "distributor": "mandatory"},
    "SHAREISLAND": {"region": "mandatory", "distributor": "optional"},
    "OLDTOONSWORLD": {"region": "mandatory", "distributor": "optional"},
}

_AKA_PATTERNS = (" AKA ", ".aka.", " aka ", ".AKA.")
_YEAR_PATTERN = r"(18|19|20)\d{2}"
_RESOLUTION_PATTERN = r"\b(480|576|720|1080|2160)[pi]\b"
_RELEASE_TYPE_PATTERN = (
    r"(WEBDL|BluRay|REMUX|HDRip|Blu-Ray|Web-DL|webrip|web-rip|DVD|BD100|BD50|"
    r"BD25|HDTV|UHD|HDR|DOVI|REPACK|Season)(?=[._\-\s]|$)"
)
_SEASON_PATTERN = r"\bS(\d{1,3})\b"
_SEASON_EPISODE_PATTERN = r"\bS(\d{1,3})E(\d{1,3})\b"
_DATE_PATTERN = r"\b(20\d{2})\.(\d{1,2})\.(\d{1,2})\b"
_EXTENSION_PATTERN = r"\.(mkv|mp4)$"
_DOUBLE_YEAR_PATTERN = r"\b(18|19|20)\d{2}\.(18|19|20)\d{2}\b"
_AKA_RELEASE_PATTERN = (
    r"\b(19|20)\d{2}\b|\bBluRay\b|\bREMUX\b|\b\d+p\b|\bDTS-HD\b|\bAVC\b"
)
_TITLE_REPLACEMENTS = {
    "_": " ",
    ".": " ",
    "DVD9": "",
    "DVD5": "",
    "DVDR": "",
    "BDR": "",
    "HDDVD": "",
    "WEB-DL": "",
    "WEBRip": "",
    "WEB": "",
    "BluRay": "",
    "Blu-ray": "",
    "HDTV": "",
    "DVDRip": "",
    "REMUX": "",
    "HDR": "",
    "UHD": "",
    "4K": "",
    "DVD": "",
    "HDRip": "",
    "BDMV": "",
    "R1": "",
    "R2": "",
    "R3": "",
    "R4": "",
    "R5": "",
    "R6": "",
    "Director's Cut": "",
    "Extended Edition": "",
    "directors cut": "",
    "director cut": "",
    "itunes": "",
}


@dataclass(frozen=True)
class _TitleScan:
    folder_name_for_title: str
    actual_year: str | None
    indices: list[tuple[str, int, str]]


@dataclass(frozen=True)
class _NamingContext:
    release_type: str
    title: str
    alt_title: str
    year: str
    resolution: str
    audio: str
    service: str
    season: str
    episode: str
    episode_title: str
    part: str
    repack: str
    three_d: str
    tag: str
    source: str
    uhd: str
    hdr: str
    hybrid: str
    video_codec: str
    video_encode: str
    region: str
    dvd_size: str
    edition: str


class NameManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config=config)

    @staticmethod
    def _active_disc_trackers(meta: Meta) -> list[str]:
        return [
            tracker
            for tracker in TRACKER_DISC_REQUIREMENTS
            if tracker in meta.trackers
        ]

    @staticmethod
    def _remove_disc_trackers(meta: Meta, trackers: list[str]) -> None:
        configured = meta.trackers
        if not isinstance(configured, list):
            return
        for tracker in trackers:
            if tracker not in configured:
                continue
            if meta.unattended:
                logger.info("")
                logger.info(
                    f"[yellow]Removing tracker {tracker} due to missing "
                    "distributor/region info.[/yellow]"
                )
            configured.remove(tracker)

    @staticmethod
    def _assign_disc_info(meta: Meta, region: str, distributor: str) -> None:
        if distributor and "SKIPPED" not in distributor:
            meta.distributor = distributor
        if region and "SKIPPED" not in region:
            meta.region = region

    async def _apply_disc_requirements(self, meta: Meta) -> None:
        active = self._active_disc_trackers(meta)
        if not active:
            return
        region, distributor, trackers_to_remove = await self.missing_disc_info(
            meta, active
        )
        self._remove_disc_trackers(meta, trackers_to_remove)
        self._assign_disc_info(meta, region, distributor)

    @staticmethod
    def _base_year(meta: Meta) -> str:
        year = str(meta.year) if meta.year is not None else ""
        manual_year = meta.manual_year
        return (
            str(manual_year)
            if manual_year is not None and manual_year > 0
            else year
        )

    @classmethod
    def _effective_year(cls, meta: Meta) -> str:
        year = cls._base_year(meta)
        if meta.category == "TV":
            year = (
                str(meta.year)
                if meta.year is not None and meta.search_year != ""
                else ""
            )
        return "" if meta.no_year is True else year

    @staticmethod
    def _episode_title(meta: Meta) -> str:
        if meta.manual_episode_title:
            return meta.manual_episode_title
        return meta.daily_episode_title or ""

    @staticmethod
    def _season_episode(meta: Meta) -> tuple[str, str]:
        season = str(meta.season)
        episode = str(meta.episode)
        if meta.category == "TV" and meta.manual_date:
            return "", ""
        if meta.no_season is True:
            season = ""
        return season, episode

    @staticmethod
    def _video_fields(meta: Meta) -> tuple[str, str, str, str]:
        if meta.is_disc == "BDMV":
            return meta.video_codec, "", str(meta.region or ""), ""
        if meta.is_disc == "DVD":
            return "", "", str(meta.region or ""), meta.dvd_size
        return meta.video_codec, meta.video_encode, "", ""

    @staticmethod
    def _edition(meta: Meta) -> str:
        edition = meta.edition
        if "HYBRID" not in edition.upper():
            return edition
        return re.sub(r"(?i)\bhybrid\b", "", edition).strip()

    @classmethod
    def _naming_context(cls, meta: Meta) -> _NamingContext:
        season, episode = cls._season_episode(meta)
        video_codec, video_encode, region, dvd_size = cls._video_fields(meta)
        resolution = "" if meta.resolution == "OTHER" else meta.resolution
        alt_title = "" if meta.no_aka is True else meta.aka
        return _NamingContext(
            release_type=str(meta.type).upper(),
            title=meta.title,
            alt_title=alt_title,
            year=cls._effective_year(meta),
            resolution=resolution,
            audio=meta.audio,
            service=str(meta.service),
            season=season,
            episode=episode,
            episode_title=cls._episode_title(meta),
            part=meta.part,
            repack=meta.repack,
            three_d=meta.three_d,
            tag=meta.tag or "",
            source=str(meta.source),
            uhd=str(meta.uhd),
            hdr=meta.hdr,
            hybrid="Hybrid" if meta.webdv else "",
            video_codec=video_codec,
            video_encode=video_encode,
            region=region,
            dvd_size=dvd_size,
            edition=cls._edition(meta),
        )

    @staticmethod
    def _movie_disc_name(
        meta: Meta, ctx: _NamingContext
    ) -> tuple[str, list[str]]:
        if meta.is_disc == "BDMV":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.three_d} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.region} {ctx.uhd} {ctx.source} {ctx.hdr} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "region", "distributor"]
        if meta.is_disc == "DVD":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.repack} {ctx.edition} {ctx.region} {ctx.source} {ctx.dvd_size} {ctx.audio}"
            return name, ["edition", "distributor"]
        if meta.is_disc == "HDDVD":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.repack} {ctx.resolution} {ctx.source} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "region", "distributor"]
        return "", []

    @staticmethod
    def _movie_remux_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        if ctx.source in {"BluRay", "HDDVD"}:
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.three_d} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.source} REMUX {ctx.hdr} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "description"]
        if ctx.source in {"PAL DVD", "NTSC DVD", "DVD"}:
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.repack} {ctx.source} REMUX  {ctx.audio}"
            return name, ["edition", "description"]
        return "", []

    @staticmethod
    def _movie_encode_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.source} {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "description"]

    @staticmethod
    def _movie_webdl_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.service} WEB-DL {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "service"]

    @staticmethod
    def _movie_webrip_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.service} WEBRip {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "service"]

    @staticmethod
    def _movie_hdtv_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.repack} {ctx.resolution} {ctx.source} {ctx.audio} {ctx.video_encode}"
        return name, []

    @staticmethod
    def _movie_dvdrip_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.source} {ctx.video_encode} DVDRip {ctx.audio}"
        return name, []

    def _movie_name(
        self, meta: Meta, ctx: _NamingContext
    ) -> tuple[str, list[str]]:
        if ctx.release_type == "DISC":
            return self._movie_disc_name(meta, ctx)
        if ctx.release_type == "REMUX":
            return self._movie_remux_name(ctx)
        builders = {
            "ENCODE": self._movie_encode_name,
            "WEBDL": self._movie_webdl_name,
            "WEBRIP": self._movie_webrip_name,
            "HDTV": self._movie_hdtv_name,
            "DVDRIP": self._movie_dvdrip_name,
        }
        builder = builders.get(ctx.release_type)
        return builder(ctx) if builder is not None else ("", [])

    @staticmethod
    def _tv_disc_name(
        meta: Meta, ctx: _NamingContext
    ) -> tuple[str, list[str]]:
        if meta.is_disc == "BDMV":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.three_d} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.region} {ctx.uhd} {ctx.source} {ctx.hdr} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "region", "distributor"]
        if meta.is_disc == "DVD":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode}{ctx.three_d} {ctx.repack} {ctx.edition} {ctx.region} {ctx.source} {ctx.dvd_size} {ctx.audio}"
            return name, ["edition", "distributor"]
        if meta.is_disc == "HDDVD":
            name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.edition} {ctx.repack} {ctx.resolution} {ctx.source} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "region", "distributor"]
        return "", []

    @staticmethod
    def _tv_remux_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        prefix = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.episode_title} {ctx.part}"
        if ctx.source in {"BluRay", "HDDVD"}:
            name = f"{prefix} {ctx.three_d} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.source} REMUX {ctx.hdr} {ctx.video_codec} {ctx.audio}"
            return name, ["edition", "description"]
        if ctx.source in {"PAL DVD", "NTSC DVD", "DVD"}:
            name = f"{prefix} {ctx.edition} {ctx.repack} {ctx.source} REMUX {ctx.audio}"
            return name, ["edition", "description"]
        return "", []

    @staticmethod
    def _tv_encode_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.episode_title} {ctx.part} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.source} {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "description"]

    @staticmethod
    def _tv_webdl_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.episode_title} {ctx.part} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.service} WEB-DL {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "service"]

    @staticmethod
    def _tv_webrip_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.episode_title} {ctx.part} {ctx.edition} {ctx.hybrid} {ctx.repack} {ctx.resolution} {ctx.uhd} {ctx.service} WEBRip {ctx.audio} {ctx.hdr} {ctx.video_encode}"
        return name, ["edition", "service"]

    @staticmethod
    def _tv_hdtv_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season}{ctx.episode} {ctx.episode_title} {ctx.part} {ctx.edition} {ctx.repack} {ctx.resolution} {ctx.source} {ctx.audio} {ctx.video_encode}"
        return name, []

    @staticmethod
    def _tv_dvdrip_name(ctx: _NamingContext) -> tuple[str, list[str]]:
        name = f"{ctx.title} {ctx.alt_title} {ctx.year} {ctx.season} {ctx.source} DVDRip {ctx.audio} {ctx.video_encode}"
        return name, []

    def _tv_name(
        self, meta: Meta, ctx: _NamingContext
    ) -> tuple[str, list[str]]:
        if ctx.release_type == "DISC":
            return self._tv_disc_name(meta, ctx)
        if ctx.release_type == "REMUX":
            return self._tv_remux_name(ctx)
        builders = {
            "ENCODE": self._tv_encode_name,
            "WEBDL": self._tv_webdl_name,
            "WEBRIP": self._tv_webrip_name,
            "HDTV": self._tv_hdtv_name,
            "DVDRIP": self._tv_dvdrip_name,
        }
        builder = builders.get(ctx.release_type)
        return builder(ctx) if builder is not None else ("", [])

    @staticmethod
    def _xxx_name(meta: Meta) -> str:
        release_name = str(
            meta.scene_name or meta.basename_no_ext or meta.uuid or meta.title
        )
        return release_name.replace(".", " ")

    def _other_category_name(self, meta: Meta) -> str:
        builders = {
            "BOOK": self.extract_book_name,
            "GAME": self.extract_game_name,
            "MUSIC": self.extract_music_name,
        }
        builder = builders.get(meta.category)
        if builder is not None:
            return builder(meta)
        if meta.category == "PODCAST":
            return meta.podcast_title or meta.name or meta.title
        return ""

    def _raw_name(
        self, meta: Meta, ctx: _NamingContext
    ) -> tuple[str, list[str]]:
        if meta.manual_name is not None:
            return str(meta.manual_name).strip(), []
        if meta.category == "XXX":
            return self._xxx_name(meta), []
        if meta.category == "MOVIE":
            return self._movie_name(meta, ctx)
        if meta.category == "TV":
            return self._tv_name(meta, ctx)
        return self._other_category_name(meta), []

    @staticmethod
    def _normalize_name(meta: Meta, name: str) -> str:
        try:
            return " ".join(name.split())
        except Exception:
            logger.info(
                "[bold red]Unable to generate name. Please re-run and correct "
                "any of the following args if needed."
            )
            logger.info(f"--category [yellow]{meta.category}")
            logger.info(f"--type [yellow]{meta.type}")
            logger.info(f"--source [yellow]{meta.source}")
            logger.info(
                "[bold green]If you specified type, try also specifying source"
            )
            raise OperationAbortedError(
                "Release naming was cancelled because required metadata is unavailable."
            ) from None

    @staticmethod
    def _tagged_name(meta: Meta, name_notag: str, tag: str) -> str:
        tag_already_present = bool(
            meta.category == "XXX"
            and tag
            and name_notag.casefold().endswith(tag.casefold())
        )
        if meta.manual_name is not None or tag_already_present:
            return name_notag
        return name_notag + tag

    @staticmethod
    def _log_name_context(meta: Meta) -> None:
        if not meta.debug:
            return
        logger.debug("[cyan]get_name cat/type")
        logger.debug(f"CATEGORY: {meta.category}")
        logger.debug(f"TYPE: {meta.type}")
        logger.debug("[cyan]get_name meta:")

    async def get_name(self, meta: Meta) -> tuple[str, str, str, list[str]]:
        await self._apply_disc_requirements(meta)
        ctx = self._naming_context(meta)
        self._log_name_context(meta)
        raw_name, potential_missing = self._raw_name(meta, ctx)
        name_notag = self._normalize_name(meta, raw_name)
        name = self._tagged_name(meta, name_notag, ctx.tag)
        clean_name = await self.clean_filename(name)
        return name_notag, name, clean_name, potential_missing

    @staticmethod
    def _preferred_text(primary: Any, fallback: Any = "") -> str:
        if primary:
            return str(primary).strip()
        return str(fallback or "").strip()

    @classmethod
    def _book_edition(cls, meta: Meta) -> str:
        edition = cls._preferred_text(meta.manual_edition, meta.edition)
        if not edition:
            return ""
        if meta.audiobook:
            return edition
        if re.search(r"edition|ed\.|ed", edition, re.IGNORECASE):
            return edition
        return f"{edition} Edition"

    @staticmethod
    def _book_language(meta: Meta) -> str:
        language = meta.book_language.strip() or meta.book_language_iso.strip()
        if language.lower() in ("english", "eng", "en"):
            return ""
        return language.upper().replace("I", "i")

    @staticmethod
    def _inferred_book_source(meta: Meta) -> str:
        filename_lower = (meta.uuid + " " + meta.title).lower()
        if "scan" in filename_lower:
            return "SCAN"
        if "hybrid" in filename_lower:
            return "HYBRiD"
        if "retail" in filename_lower:
            return "RETAiL"
        return "SCAN" if str(meta.type).upper() == "PDF" else "RETAiL"

    @classmethod
    def _book_source(cls, meta: Meta) -> str:
        source = meta.source or ""
        manual_source = str(meta.manual_source or "").strip().upper()
        if manual_source in ("RETAIL", "SCAN", "HYBRID"):
            source = manual_source
        if source not in ("RETAIL", "SCAN", "HYBRID"):
            return cls._inferred_book_source(meta)
        aliases = {"RETAIL": "RETAiL", "HYBRID": "HYBRiD", "SCAN": "SCAN"}
        return aliases[source]

    @staticmethod
    def _book_format(meta: Meta) -> str:
        ebook_type = str(meta.type).strip().upper()
        aliases = {"EPUB": "ePUB", "PDF": ""}
        return aliases.get(ebook_type, ebook_type)

    @staticmethod
    def _remaining_book_subtype(meta: Meta) -> str:
        return "newspaper" if meta.newspaper else "book"

    @classmethod
    def _book_subtype(cls, meta: Meta) -> str:
        if meta.audiobook:
            return "audiobook"
        if meta.comic:
            return "comic"
        if meta.manga:
            return "manga"
        if meta.magazine:
            return "magazine"
        return cls._remaining_book_subtype(meta)

    @staticmethod
    def _audiobook_parts(
        author_or_publisher: str,
        title: str,
        edition: str,
        year: str,
        language: str,
    ) -> list[str]:
        tail = [title, edition, year, language, "AUDIOBOOK"]
        return (
            [author_or_publisher, "-", *tail] if author_or_publisher else tail
        )

    @staticmethod
    def _comic_parts(
        title: str,
        volume: str,
        issue: str,
        year: str,
        language: str,
        source: str,
        ebook_type: str,
    ) -> list[str]:
        return [
            title,
            f"Vol {volume}" if volume else "",
            f"No {issue}" if issue else "",
            year,
            language,
            source,
            ebook_type,
            "COMiC",
            "eBOOK",
        ]

    @staticmethod
    def _manga_parts(
        title: str,
        volume: str,
        year: str,
        language: str,
        source: str,
        ebook_type: str,
    ) -> list[str]:
        return [
            title,
            f"Vol {volume}" if volume else "",
            year,
            language,
            source,
            ebook_type,
            "MANGA",
            "eBOOK",
        ]

    @staticmethod
    def _magazine_parts(
        title: str,
        issue: str,
        year: str,
        language: str,
        source: str,
        ebook_type: str,
    ) -> list[str]:
        return [
            title,
            f"No {issue}" if issue else "",
            year,
            language,
            source,
            ebook_type,
            "MAGAZiNE",
            "eBOOK",
        ]

    @classmethod
    def _book_author_or_publisher(cls, meta: Meta) -> str:
        return cls._preferred_text(meta.author, meta.publisher)

    @staticmethod
    def _book_year(meta: Meta) -> str:
        if meta.year is None:
            return ""
        return str(meta.year).strip()

    @staticmethod
    def _join_name_parts(parts: list[str]) -> str:
        return " ".join(" ".join(filter(None, parts)).split())

    def extract_book_name(self, meta: Meta) -> str:
        author_or_publisher = self._book_author_or_publisher(meta)
        title = meta.title.strip()
        title_without_author = self._strip_prefix_author_or_publisher(
            title, author_or_publisher
        )
        edition = self._book_edition(meta)
        year = self._book_year(meta)
        volume = self._preferred_text(meta.manual_season, meta.season)
        issue = self._preferred_text(meta.manual_episode, meta.episode)
        language = self._book_language(meta)
        source = self._book_source(meta)
        ebook_type = self._book_format(meta)
        builders: dict[str, Callable[[], list[str]]] = {
            "audiobook": lambda: self._audiobook_parts(
                author_or_publisher,
                title_without_author,
                edition,
                year,
                language,
            ),
            "comic": lambda: self._comic_parts(
                title, volume, issue, year, language, source, ebook_type
            ),
            "manga": lambda: self._manga_parts(
                title, volume, year, language, source, ebook_type
            ),
            "magazine": lambda: self._magazine_parts(
                title, issue, year, language, source, ebook_type
            ),
            "newspaper": lambda: [
                title,
                year,
                language,
                source,
                ebook_type,
                "eBOOK",
            ],
            "book": lambda: [
                author_or_publisher,
                "-",
                title_without_author,
                edition,
                year,
                language,
                source,
                ebook_type,
                "eBOOK",
            ],
        }
        parts = builders[self._book_subtype(meta)]()
        return self._join_name_parts(parts)

    @staticmethod
    def _strip_prefix_author_or_publisher(
        title: str, author_or_publisher: str
    ) -> str:
        if not title or not author_or_publisher:
            return title.strip()
        return re.sub(
            rf"^{re.escape(author_or_publisher)}\s*-\s*",
            "",
            title.strip(),
            flags=re.IGNORECASE,
        ).strip()

    @staticmethod
    def _multi_language_tag(
        lang_count: int, source_has_multi: bool, force_multi: bool
    ) -> str | None:
        if lang_count <= 1:
            return None
        if source_has_multi:
            return f"MULTI{lang_count}"
        if force_multi:
            return f"MULTI{lang_count}"
        return None

    @staticmethod
    def _single_game_language_tag(lang_names: list[str]) -> str:
        if len(lang_names) != 1:
            return ""
        single = lang_names[0].upper()
        return "" if single in ("ENGLISH", "ENG", "EN") else single

    @staticmethod
    def _game_language_names(meta: Meta) -> list[str]:
        languages: dict[str, Any] | list[Any] = meta.languages
        if not languages:
            return []
        return list(filter(None, languages))

    @classmethod
    def _game_language_tag(cls, meta: Meta) -> str:
        lang_names = cls._game_language_names(meta)
        source_path = str(cls._first_nonempty(meta.path, meta.uuid, ""))
        source_has_multi = "multi" in Path(source_path).name.lower()
        force_multi = bool(meta.manual_multi)
        multi_tag = cls._multi_language_tag(
            len(lang_names), source_has_multi, force_multi
        )
        if multi_tag is not None:
            return multi_tag
        if force_multi:
            return "MULTI"
        return cls._single_game_language_tag(lang_names)

    @staticmethod
    def _game_version(meta: Meta) -> str:
        game_version = meta.game_version or ""
        if not game_version:
            return ""
        return (
            game_version
            if game_version.lower().startswith("v")
            else f"v{game_version}"
        )

    @staticmethod
    def _game_platform(meta: Meta) -> str:
        platform_name = (
            str(meta.manual_platform or meta.platform or "").strip().upper()
        )
        return (
            "" if platform_name in ("PC", "WINDOWS", "WIN") else platform_name
        )

    @staticmethod
    def _game_repack(meta: Meta) -> str:
        return str(meta.repack) if meta.repack else ""

    def extract_game_name(self, meta: Meta) -> str:
        """Build a game release name losely based on the SCENE 2021_GAMEiSO ruleset."""
        tokens = [
            meta.title.strip(),
            self._preferred_text(meta.manual_edition, meta.edition),
            self._game_version(meta),
            self._preferred_text(meta.manual_year, meta.year),
            self._game_language_tag(meta),
            self._game_platform(meta),
            self._game_repack(meta),
        ]
        base_name = " ".join(filter(None, tokens))
        return re.sub(r"\.{2,}", " ", base_name)

    @staticmethod
    def _string_any_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return cast(dict[str, Any], value)

    @classmethod
    def _music_release_field(
        cls, release: dict[str, Any], name: str, default: Any = ""
    ) -> Any:
        """Read a serialized MusicRelease field without its provenance."""
        fields = cls._string_any_dict(release.get("fields"))
        value = cls._string_any_dict(fields.get(name))
        return value.get("value", default)

    @staticmethod
    def _music_codec(value: Any) -> str:
        codec = str(value or "").upper().strip()
        aliases = {
            "OGG VORBIS": "VORBIS",
            "OGG": "VORBIS",
            "MPEG AUDIO": "MP3",
            "MPEG-4 AAC": "AAC",
            "M4A": "AAC",
        }
        return aliases.get(codec, codec)

    @staticmethod
    def _music_source(value: Any) -> str:
        source = str(value or "").strip().casefold()
        aliases = {
            "cd": "CD",
            "hdcd": "HDCD",
            "dts-cd": "DTS-CD",
            "dts cd": "DTS-CD",
            "8-track": "8-Track",
            "8 track": "8-Track",
            "vinyl": "Vinyl",
            "web": "WEB",
            "cassette": "Cassette",
        }
        return aliases.get(source, str(value or "").strip())

    @classmethod
    def _first_music_track(cls, release: dict[str, Any]) -> dict[str, Any]:
        tracks = release.get("tracks", [])
        if not isinstance(tracks, list):
            return {}
        if not tracks:
            return {}
        return cls._string_any_dict(tracks[0])

    @staticmethod
    def _first_nonempty(*values: Any) -> Any:
        for value in values:
            if value:
                return value
        return ""

    @classmethod
    def _music_codec_from_track(
        cls, first_track: dict[str, Any], meta: Meta
    ) -> str:
        value = cls._first_nonempty(
            first_track.get("codec"),
            first_track.get("format"),
            meta.format,
            meta.type,
        )
        return cls._music_codec(value)

    @classmethod
    def _lossless_music_parts(
        cls,
        release: dict[str, Any],
        first_track: dict[str, Any],
        codec: str,
    ) -> list[str]:
        if codec not in {"FLAC", "ALAC"}:
            return []
        depth = cls._first_nonempty(
            first_track.get("bit_depth"),
            cls._music_release_field(release, "nfo_bit_depth"),
        )
        rate = cls._first_nonempty(
            first_track.get("sample_rate"),
            cls._music_release_field(release, "nfo_sample_rate"),
        )
        depth_part = f"{depth}-bit" if depth else ""
        rate_part = f"{int(rate) / 1000:g} kHz" if rate else ""
        return [depth_part, rate_part]

    def extract_music_name(self, meta: Meta) -> str:
        """Build MUSIC names with the LST Discogs-based naming convention."""
        release = (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )
        artist = self._music_release_field(release, "artist", meta.artist)
        title = self._music_release_field(release, "album", meta.title)
        year = self._music_release_field(
            release,
            "release_year",
            self._music_release_field(release, "year", meta.year),
        )
        source = self._music_source(
            self._music_release_field(release, "media", meta.source)
        )
        first_track = self._first_music_track(release)
        codec = self._music_codec_from_track(first_track, meta)
        parts = [
            str(artist),
            "-",
            str(title),
            str(year),
            source,
            codec,
            *self._lossless_music_parts(release, first_track, codec),
        ]
        return " ".join(
            part.strip() for part in parts if str(part or "").strip()
        )

    async def clean_filename(self, name: str) -> str:
        invalid = '<>:"/\\|?*'
        for char in invalid:
            name = name.replace(char, "-")
        return name

    @staticmethod
    def _aka_parts(basename: str) -> tuple[str, str] | None:
        for pattern in _AKA_PATTERNS:
            if pattern in basename:
                primary, secondary = basename.split(pattern, 1)
                return primary.strip(), secondary.strip()
        return None

    @staticmethod
    def _year_in_text(text: str) -> str | None:
        match = re.search(r"\b(19|20)\d{2}\b", text)
        return match.group(0) if match is not None else None

    @staticmethod
    def _aka_release_is_year(
        match: re.Match[str] | None, existing_year: str | None
    ) -> bool:
        if match is None:
            return False
        if existing_year:
            return False
        return re.fullmatch(r"(19|20)\d{2}", match.group(0)) is not None

    @classmethod
    def _aka_secondary(
        cls, secondary_part: str, year: str | None
    ) -> tuple[str, str | None]:
        secondary_match = re.match(r"^(\d+)", secondary_part)
        if secondary_match is not None:
            return secondary_match.group(1), year
        release_match = re.search(_AKA_RELEASE_PATTERN, secondary_part)
        if cls._aka_release_is_year(release_match, year):
            year_match = cast(re.Match[str], release_match)
            secondary = secondary_part[: year_match.start()].strip()
            return secondary, year_match.group(0)
        return secondary_part, year

    @staticmethod
    def _normalize_dotted_title(value: str) -> str:
        return value.replace(".", " ").strip()

    @classmethod
    def _aka_title_result(
        cls, basename: str
    ) -> tuple[str, str | None, str | None] | None:
        parts = cls._aka_parts(basename)
        if parts is None:
            return None
        primary_title, secondary_part = parts
        year = cls._year_in_text(primary_title)
        secondary_title, year = cls._aka_secondary(secondary_part, year)
        return (
            cls._normalize_dotted_title(primary_title),
            cls._normalize_dotted_title(secondary_title),
            year,
        )

    @staticmethod
    def _year_start_result(
        basename: str,
    ) -> tuple[str, None, str] | None:
        year_start_match = re.match(r"^(19|20)\d{2}", basename)
        if year_start_match is None:
            return None
        title = year_start_match.group(0)
        rest = basename[len(title) :].lstrip(". _-")
        year_match = re.search(r"\b(19|20)\d{2}\b", rest)
        if year_match is None:
            return None
        return title, None, year_match.group(0)

    @staticmethod
    def _release_folder_name(meta: Meta) -> str:
        if not meta.uuid:
            return ""
        return Path(meta.uuid).name

    @staticmethod
    def _subsplease_title(folder_name: str) -> str | None:
        if "subsplease" not in folder_name.lower():
            return None
        guess_data = guessit_fn(
            folder_name, {"excludes": ["country", "language"]}
        )
        parsed = cast(
            dict[str, Any] | None,
            cast(Any, anitopy).parse(cast(str, guess_data.get("title", ""))),
        )
        if not parsed:
            return None
        parsed_title = parsed.get("anime_title")
        return str(parsed_title) if parsed_title else None

    @staticmethod
    def _match_index(
        label: str, match: re.Match[str] | None
    ) -> list[tuple[str, int, str]]:
        if match is None:
            return []
        return [(label, match.start(), match.group())]

    @classmethod
    def _common_title_indices(
        cls, folder_name: str
    ) -> list[tuple[str, int, str]]:
        indices: list[tuple[str, int, str]] = []
        indices.extend(
            cls._match_index(
                "res",
                re.search(_RESOLUTION_PATTERN, folder_name, re.IGNORECASE),
            )
        )
        indices.extend(
            cls._match_index(
                "season",
                re.search(_SEASON_PATTERN, folder_name, re.IGNORECASE),
            )
        )
        indices.extend(
            cls._match_index(
                "season_episode",
                re.search(_SEASON_EPISODE_PATTERN, folder_name, re.IGNORECASE),
            )
        )
        indices.extend(
            cls._match_index(
                "extension",
                re.search(_EXTENSION_PATTERN, folder_name, re.IGNORECASE),
            )
        )
        indices.extend(
            cls._match_index(
                "type",
                re.search(_RELEASE_TYPE_PATTERN, folder_name, re.IGNORECASE),
            )
        )
        return indices

    @staticmethod
    def _year_without_date(
        year_match: re.Match[str] | None, date_match: re.Match[str] | None
    ) -> str | None:
        if year_match is None:
            return None
        if date_match is not None:
            return None
        return year_match.group()

    @classmethod
    def _standard_title_scan(cls, folder_name: str) -> _TitleScan:
        date_match = re.search(_DATE_PATTERN, folder_name)
        year_match = re.search(_YEAR_PATTERN, folder_name)
        indices = cls._match_index("date", date_match)
        if date_match is None:
            indices.extend(cls._match_index("year", year_match))
        indices.extend(cls._common_title_indices(folder_name))
        return _TitleScan(
            folder_name,
            cls._year_without_date(year_match, date_match),
            indices,
        )

    @classmethod
    def _double_year_title_scan(
        cls, folder_name: str, match: re.Match[str]
    ) -> _TitleScan:
        full_match = match.group(0)
        first_year, second_year = full_match.split(".")
        logger.debug(
            f"[cyan]Found double year pattern: {full_match}, using {second_year} as year[/cyan]"
        )
        modified_folder_name = folder_name.replace(full_match, first_year)
        year_boundary = match.start()
        if year_boundary == 0:
            year_boundary += len(first_year)
        indices = [("year", year_boundary, second_year)]
        indices.extend(cls._common_title_indices(modified_folder_name))
        return _TitleScan(modified_folder_name, second_year, indices)

    @classmethod
    def _title_scan(cls, folder_name: str) -> _TitleScan:
        double_year_match = re.search(_DOUBLE_YEAR_PATTERN, folder_name)
        if double_year_match is None:
            return cls._standard_title_scan(folder_name)
        return cls._double_year_title_scan(folder_name, double_year_match)

    @staticmethod
    def _title_part_from_scan(scan: _TitleScan) -> tuple[str, int | None]:
        if not scan.indices:
            return scan.folder_name_for_title, None
        first_index = min(scan.indices, key=lambda item: item[1])[1]
        title_part = scan.folder_name_for_title[:first_index]
        return re.sub(r"[\.\-_ ]+$", "", title_part), first_index

    @staticmethod
    def _unmatched_parenthetical(
        title_part: str,
        folder_name_for_title: str,
        first_index: int | None,
        secondary_title: str | None,
    ) -> tuple[str, str | None]:
        if first_index is None:
            return title_part, secondary_title
        if title_part.count("(") <= title_part.count(")"):
            return title_part, secondary_title
        paren_pos = title_part.rfind("(")
        content = folder_name_for_title[paren_pos + 1 : first_index].strip()
        if content:
            secondary_title = content
        return title_part[:paren_pos].rstrip(), secondary_title

    async def _clean_title_components(
        self, title_part: str, secondary_title: str | None
    ) -> tuple[str, str | None]:
        title = (
            await self.multi_replace(title_part, _TITLE_REPLACEMENTS)
        ).strip()
        secondary = (
            await self.multi_replace(
                secondary_title or "", _TITLE_REPLACEMENTS
            )
        ).strip()
        return title, secondary if secondary else None

    async def _extract_bracket_secondary(
        self, title: str, secondary_title: str | None
    ) -> tuple[str, str | None]:
        if not title:
            return title, secondary_title
        bracket_pattern = r"\s*\(([^)]+)\)\s*"
        bracket_match = re.search(bracket_pattern, title)
        if bracket_match is None:
            return title, secondary_title
        bracket_content = bracket_match.group(1).strip()
        bracket_content = await self.multi_replace(
            bracket_content, _TITLE_REPLACEMENTS
        )
        if not secondary_title and bracket_content:
            secondary_title = re.sub(r"[\.\-_ ]+$", "", bracket_content)
        title = re.sub(bracket_pattern, " ", title)
        return re.sub(r"\s+", " ", title).strip(), secondary_title

    @staticmethod
    def _final_title_result(
        title: str,
        secondary_title: str | None,
        actual_year: str | None,
        basename: str,
    ) -> tuple[str | None, str | None, str | None]:
        if title:
            return title, secondary_title, actual_year
        year_match = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", basename)
        if year_match is not None:
            return None, None, year_match.group(0)
        return None, None, None

    async def extract_title_and_year(
        self, meta: Meta, filename: str
    ) -> tuple[str | None, str | None, str | None]:
        basename = Path(filename).stem
        aka_result = self._aka_title_result(basename)
        if aka_result is not None:
            return aka_result
        year_start_result = self._year_start_result(basename)
        if year_start_result is not None:
            return year_start_result

        folder_name = self._release_folder_name(meta)
        logger.debug(
            f"[cyan]Extracting title and year from folder name: {folder_name}[/cyan]"
        )
        subsplease_title = self._subsplease_title(folder_name)
        if subsplease_title is not None:
            return subsplease_title, None, None

        scan = self._title_scan(folder_name)
        title_part, first_index = self._title_part_from_scan(scan)
        title_part, secondary_title = self._unmatched_parenthetical(
            title_part,
            scan.folder_name_for_title,
            first_index,
            None,
        )
        title, secondary_title = await self._clean_title_components(
            title_part, secondary_title
        )
        title, secondary_title = await self._extract_bracket_secondary(
            title, secondary_title
        )
        return self._final_title_result(
            title, secondary_title, scan.actual_year, basename
        )

    async def multi_replace(
        self, text: str, replacements: dict[str, str]
    ) -> str:
        for old, new in replacements.items():
            text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _promote_requirement(current: str, candidate: Any) -> str:
        return "mandatory" if candidate == "mandatory" else current

    @classmethod
    def _strictest_disc_requirements(
        cls, active_trackers: Sequence[str]
    ) -> dict[str, str]:
        strictest = {"region": "optional", "distributor": "optional"}
        for tracker in active_trackers:
            requirements = TRACKER_DISC_REQUIREMENTS.get(tracker, {})
            strictest["region"] = cls._promote_requirement(
                strictest["region"], requirements.get("region")
            )
            strictest["distributor"] = cls._promote_requirement(
                strictest["distributor"], requirements.get("distributor")
            )
        return strictest

    async def _resolve_disc_region(
        self,
        meta: Meta,
        region_name: str,
        region_id: Any,
        is_mandatory: bool,
    ) -> tuple[str, Any]:
        if region_id:
            return region_name, region_id
        region_name = await self._prompt_for_field(
            meta, "Region code", is_mandatory
        )
        if region_name == "SKIPPED":
            return region_name, region_id
        region_id = await self.common.unit3d_region_ids(region_name)
        return region_name, region_id

    async def _resolve_disc_distributor(
        self,
        meta: Meta,
        distributor_name: str,
        distributor_id: Any,
        is_mandatory: bool,
    ) -> tuple[str, Any]:
        if distributor_id:
            return distributor_name, distributor_id
        distributor_name = await self._prompt_for_field(
            meta, "Distributor", is_mandatory
        )
        if distributor_name == "SKIPPED":
            return distributor_name, distributor_id
        logger.info(f"Looking up distributor ID for: {distributor_name}")
        distributor_id = await self.common.unit3d_distributor_ids(
            distributor_name
        )
        logger.info(f"Found distributor ID: {distributor_id}")
        return distributor_name, distributor_id

    @staticmethod
    def _tracker_missing_disc_requirement(
        requirements: dict[str, str], region_name: str, distributor_name: str
    ) -> bool:
        if (
            requirements.get("region") == "mandatory"
            and region_name == "SKIPPED"
        ):
            return True
        if requirements.get("distributor") == "mandatory":
            return distributor_name == "SKIPPED"
        return False

    @classmethod
    def _trackers_missing_disc_requirements(
        cls,
        active_trackers: Sequence[str],
        region_name: str,
        distributor_name: str,
    ) -> list[str]:
        trackers_to_remove: list[str] = []
        for tracker in active_trackers:
            requirements = TRACKER_DISC_REQUIREMENTS.get(tracker, {})
            if cls._tracker_missing_disc_requirement(
                requirements, region_name, distributor_name
            ):
                trackers_to_remove.append(tracker)
        return trackers_to_remove

    async def missing_disc_info(
        self, meta: Meta, active_trackers: Sequence[str]
    ) -> tuple[str, str, list[str]]:
        distributor_id = await self.common.unit3d_distributor_ids(
            meta.distributor
        )
        region_id = await self.common.unit3d_region_ids(str(meta.region))
        region_name = str(meta.region)
        distributor_name = meta.distributor
        if meta.is_disc != "BDMV":
            return region_name, distributor_name, []

        strictest = self._strictest_disc_requirements(active_trackers)
        region_name, region_id = await self._resolve_disc_region(
            meta,
            region_name,
            region_id,
            strictest["region"] == "mandatory",
        )
        (
            distributor_name,
            distributor_id,
        ) = await self._resolve_disc_distributor(
            meta,
            distributor_name,
            distributor_id,
            strictest["distributor"] == "mandatory",
        )
        trackers_to_remove = self._trackers_missing_disc_requirements(
            active_trackers, region_name, distributor_name
        )
        return region_name, distributor_name, trackers_to_remove

    @staticmethod
    def _skip_disc_prompt(meta: Meta) -> bool:
        if not meta.unattended:
            return False
        return not meta.unattended_confirm

    @staticmethod
    def _disc_prompt_suffix(is_mandatory: bool) -> str:
        suffixes = {
            True: " (MANDATORY): ",
            False: " (optional, press Enter to skip): ",
        }
        return suffixes[is_mandatory]

    @staticmethod
    def _normalize_prompt_value(value: str | None) -> str:
        return value.upper() if value else "SKIPPED"

    @staticmethod
    async def _abort_missing_field(field_name: str) -> NoReturn:
        logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise OperationAbortedError(
            f"Required release field was not provided: {field_name}"
        )

    async def _prompt_for_field(
        self, meta: Meta, field_name: str, is_mandatory: bool
    ) -> str:
        """Prompt user for disc field with appropriate mandatory/optional text."""
        if self._skip_disc_prompt(meta):
            return "SKIPPED"
        suffix = self._disc_prompt_suffix(is_mandatory)
        prompt = f"{field_name} not found for disc. Please enter it manually{suffix}"
        try:
            return self._normalize_prompt_value(cli_ui.ask_string(prompt))
        except EOFError:
            await self._abort_missing_field(field_name)
