# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

import aiofiles
import cli_ui
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.domain_models.release_description import base_description
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import (
    CookieAuthUploader,
    CookieValidator,
)
from src.integrations.trackers.description_builder import DescriptionBuilder


class AmigosShare:
    """
    Amigos Share Club (ASC) is a BRAZILIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    auth_type = "cookies"
    tracker = "AMIGOSSHARE"
    display_name = "AmigosShare"
    source_flag = "ASC"
    banned_groups: tuple[str, ...] = ()
    base_url = "https://cliente.amigos-share.club"
    torrent_url = "https://cliente.amigos-share.club/torrents-details.php?id="
    requests_url = f"{base_url}/pedidos.php"
    language_map: ClassVar[dict[str, str]] = {
        "bg": "15",
        "da": "12",
        "de": "3",
        "en": "1",
        "es": "6",
        "fi": "14",
        "fr": "2",
        "hi": "23",
        "it": "4",
        "ja": "5",
        "ko": "20",
        "nl": "17",
        "no": "16",
        "pl": "19",
        "pt": "8",
        "ru": "7",
        "sv": "13",
        "th": "21",
        "tr": "25",
        "zh": "10",
    }
    anime_language_map: ClassVar[dict[str, str]] = {
        "de": "3",
        "en": "4",
        "es": "1",
        "ja": "8",
        "ko": "11",
        "pt": "5",
        "ru": "2",
        "zh": "9",
    }
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("amigos-share.club",)
    allows_bloated_audio = True
    _ARCHIVE_EXTENSIONS: ClassVar[frozenset[str]] = frozenset(
        {".7z", ".rar", ".r00", ".r01", ".zip"}
    )
    _VIDEO_EXTENSIONS: ClassVar[frozenset[str]] = frozenset(
        {".avi", ".m2ts", ".mkv", ".mp4", ".mpg", ".mpeg", ".ts", ".vob"}
    )
    _TV_ENDED_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    _TV_ONGOING_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"returning", "continuing", "in production", "upcoming", "ongoing"}
    )
    tmdb_localization_requirements: ClassVar[dict[str, dict[str, str]]] = {
        "pt-BR": {
            "main": "credits,videos,content_ratings",
            "season": "credits",
            "episode": "credits",
        }
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.season_tmdb_data: dict[str, Any] = {}
        self.episode_tmdb_data: dict[str, Any] = {}
        self.tmdb_manager = TmdbManager(config)
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.layout = self.config["TRACKERS"][self.tracker].get(
            "custom_layout", "2"
        )
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": f"Upload-Assistant ({platform.system()} {platform.release()})"
            },
            timeout=60.0,
        )

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is not None:
            self.session.cookies = cast(Any, cookie_jar)
            return True
        return False

    async def load_localized_data(self, meta: Meta) -> None:
        if meta.category in ("MOVIE", "TV"):
            ptbr_data = meta.tmdb_localized_data.get("pt-BR")
            if not ptbr_data or not ptbr_data.get("main"):
                raise RuntimeError(
                    f"{self.tracker}: Missing TMDB localized data (pt-BR)."
                )

    @staticmethod
    def _book_container_id(meta: Meta) -> str:
        filelist = meta.filelist or []
        file_path = filelist[0] if filelist else (meta.path or "")
        extension = Path(str(file_path)).suffix.lower().strip(".")
        extensions = {
            "mp3": "31",
            "png": "36",
            "jpg": "37",
            "jpeg": "37",
            "pdf": "38",
            "doc": "39",
            "docx": "39",
            "epub": "52",
            "mobi": "54",
            "cbr": "55",
            "cbz": "55",
            "html": "58",
            "htm": "58",
        }
        return extensions.get(extension, "17")

    @staticmethod
    def _general_track(meta: Meta) -> dict[str, Any] | None:
        try:
            tracks = meta.mediainfo["media"]["track"]
        except KeyError, TypeError, AttributeError:
            return None
        for track in tracks:
            if isinstance(track, dict) and track.get("@type") == "General":
                return track
        return None

    @classmethod
    def _file_container_id(cls, meta: Meta) -> str | None:
        track = cls._general_track(meta)
        if track is None:
            return None
        extension = str(track.get("FileExtension", "")).lower()
        return {"mkv": "6", "mp4": "8"}.get(extension)

    async def get_container(self, meta: Meta) -> str | None:
        if meta.category == "BOOK":
            return self._book_container_id(meta)
        if meta.is_disc == "BDMV":
            return "5"
        if meta.is_disc == "DVD":
            return "15"
        return self._file_container_id(meta)

    @staticmethod
    def _book_type_id(meta: Meta) -> str:
        rules = (
            (meta.audiobook, "121"),
            (meta.comic, "112"),
            (meta.manga, "147"),
            (meta.magazine, "68"),
        )
        for enabled, type_id in rules:
            if enabled:
                return type_id
        return "67"

    @staticmethod
    def _bd_size(meta: Meta) -> float:
        try:
            return float(meta.bdinfo["size"])
        except KeyError, IndexError, TypeError, ValueError:
            return 0.0

    @classmethod
    def _bluray_disc_type_id(cls, meta: Meta) -> str:
        mapped = {
            "BD25": "40",
            "BD50": "41",
            "BD66": "42",
            "BD100": "43",
        }.get(meta.disctype)
        if mapped is not None:
            return mapped
        size = cls._bd_size(meta)
        if size > 66:
            return "43"
        if size > 50:
            return "42"
        if size > 25:
            return "41"
        return "40"

    @classmethod
    def _disc_type_id(cls, meta: Meta) -> str | int:
        if meta.is_disc == "HDDVD":
            return 15
        if meta.is_disc == "DVD":
            return {"DVD5": "45", "DVD9": "46"}[meta.dvd_size]
        return cls._bluray_disc_type_id(meta)

    async def get_type(self, meta: Meta) -> str | int:
        if meta.category == "BOOK":
            return self._book_type_id(meta)
        if meta.category == "GAME":
            return self.get_game_type(meta)
        if meta.type == "DISC":
            return self._disc_type_id(meta)
        standard = {
            "ENCODE": "9",
            "REMUX": "39",
            "WEBDL": "23",
            "WEBRIP": "38",
            "BDRIP": "8",
            "DVDRIP": "3",
        }
        return standard.get(meta.type or "", "0")

    async def get_languages(self, meta: Meta) -> dict[str, str] | None:
        if meta.anime:
            type_ = "116" if meta.category == "MOVIE" else "118"

            original_language = (
                meta.original_language.lower()
                if meta.original_language
                else ""
            )
            anime_language = self.anime_language_map.get(
                original_language, "6"
            )

            lang = (
                "8"
                if await self.get_audio(meta) in ("2", "3", "4")
                else self.language_map.get(original_language, "11")
            )

            return {"type": type_, "idioma": anime_language, "lang": lang}

        return None

    @staticmethod
    def _portuguese_languages() -> frozenset[str]:
        return frozenset({"portuguese", "português", "pt"})

    @classmethod
    def _audio_languages(cls, meta: Meta) -> set[str]:
        return {
            str(language).lower() for language in (meta.audio_languages or [])
        }

    @classmethod
    def _has_portuguese_audio(cls, audio_languages: set[str]) -> bool:
        return bool(audio_languages & cls._portuguese_languages())

    @classmethod
    def _portuguese_audio_type(
        cls, audio_languages: set[str], original_language: str
    ) -> str:
        portuguese = cls._portuguese_languages()
        if original_language.lower() in portuguese:
            return "4"
        return "2" if audio_languages - portuguese else "3"

    @classmethod
    def _audio_type_id(
        cls,
        audio_languages: set[str],
        original_language: str,
        has_pt_subtitles: bool,
    ) -> str:
        if cls._has_portuguese_audio(audio_languages):
            return cls._portuguese_audio_type(
                audio_languages, original_language
            )
        return "1" if has_pt_subtitles else "7"

    async def get_audio(self, meta: Meta) -> str:
        has_pt_subtitles = (await self.get_subtitle(meta)) == "Embutida"
        return self._audio_type_id(
            self._audio_languages(meta),
            str(meta.original_language or ""),
            has_pt_subtitles,
        )

    async def get_subtitle(self, meta: Meta) -> str:
        portuguese_languages = {"portuguese", "português", "pt"}

        meta_subtitle_languages = (
            meta.subtitle_languages if meta.subtitle_languages else []
        )
        found_languages = {lang.lower() for lang in meta_subtitle_languages}

        if any(lang in portuguese_languages for lang in found_languages):
            return "Embutida"
        return "S_legenda"

    async def get_resolution(self, meta: Meta) -> dict[str, str]:
        width = str(meta.video_width) if meta.video_width is not None else ""
        height = (
            str(meta.video_height) if meta.video_height is not None else ""
        )
        return {"width": width, "height": height}

    @staticmethod
    def _codec_from_encode(video_encode: object) -> str:
        if not isinstance(video_encode, str):
            return ""
        normalized = video_encode.strip().lower()
        if "264" in normalized:
            return "H264"
        if "265" in normalized:
            return "HEVC"
        return ""

    @classmethod
    def _video_codec_name(cls, meta: Meta) -> str:
        encoded = cls._codec_from_encode(meta.video_encode)
        if encoded:
            return encoded
        return (
            str(meta.video_codec) if isinstance(meta.video_codec, str) else ""
        )

    @staticmethod
    def _hdr_codec_id(codec: str, hdr: object) -> str | None:
        if not hdr:
            return None
        if codec in {"HEVC", "H265"}:
            return "28"
        if codec in {"AVC", "H264"}:
            return "32"
        return None

    async def get_video_codec(self, meta: Meta) -> str:
        codec_map = {
            "MPEG-4": "31",
            "AV1": "29",
            "AVC": "30",
            "DivX": "9",
            "H264": "17",
            "H265": "18",
            "HEVC": "27",
            "M4V": "20",
            "MPEG-1": "10",
            "MPEG-2": "11",
            "RMVB": "12",
            "VC-1": "21",
            "VP6": "22",
            "VP9": "23",
            "WMV": "13",
            "XviD": "15",
        }
        codec = self._video_codec_name(meta)
        return self._hdr_codec_id(codec, meta.hdr) or codec_map.get(
            codec, "16"
        )

    async def get_audio_codec(self, meta: Meta) -> str:
        audio_type = (meta.audio or "").upper()

        codec_map = {
            "ATMOS": "43",
            "DTS:X": "25",
            "DTS-HD MA": "24",
            "DTS-HD": "23",
            "TRUEHD": "29",
            "DD+": "26",
            "DD": "11",
            "DTS": "12",
            "FLAC": "13",
            "LPCM": "21",
            "PCM": "28",
            "AAC": "10",
            "OPUS": "27",
            "MPEG": "17",
        }

        for key, code in codec_map.items():
            if key in audio_type:
                return code

        return "20"

    @staticmethod
    def _localized_main(meta: Meta) -> dict[str, Any]:
        value = meta.tmdb_localized_data.get("pt-BR", {}).get("main", {})
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _localized_title_is_distinct(
        title: str, localized: str, original_title: str
    ) -> bool:
        if not localized or localized.lower() == title.lower():
            return False
        return (
            not original_title or localized.lower() != original_title.lower()
        )

    @classmethod
    def _localized_display_title(
        cls, title: str, translated: object, original: object
    ) -> str:
        localized = str(translated or "")
        original_title = str(original or "")
        if not cls._localized_title_is_distinct(
            title, localized, original_title
        ):
            return title
        return f"{localized} ({title})"

    @classmethod
    def _media_name(cls, meta: Meta) -> str:
        main = cls._localized_main(meta)
        original = main.get("original_name") or main.get("original_title")
        translated = (
            main.get("name") if meta.category == "TV" else main.get("title")
        )
        base = cls._localized_display_title(meta.title, translated, original)
        if meta.category == "TV":
            return f"{base} - {meta.season}{meta.episode}"
        return base

    async def get_name(self, meta: Meta) -> str:
        if meta.category == "BOOK":
            author = meta.author.strip()
            title = self.common.portuguese_title_capitalization(meta.title)
            return f"{author} - {title}"
        if meta.category == "GAME":
            return self.get_game_name(meta)
        return self._media_name(meta)

    @staticmethod
    def _hosted_book_cover(meta: Meta) -> str:
        covers = meta.hosted_artwork
        if not isinstance(covers, list) or not covers:
            return ""
        first = covers[0]
        if not isinstance(first, dict):
            return ""
        return str(first.get("raw_url") or "")

    def get_book_cover(self, meta: Meta) -> str:
        hosted = self._hosted_book_cover(meta)
        if hosted:
            return hosted
        poster_url = meta.artwork_url
        if isinstance(poster_url, str) and poster_url.startswith(
            ("http://", "https://")
        ):
            return poster_url
        return ""

    @staticmethod
    def _clean_base_description(description: str) -> str:
        cleaned = description
        replacements = (
            ("[user]", ""),
            ("[/user]", ""),
            ("[align=left]", ""),
            ("[/align]", ""),
            ("[align=right]", ""),
            ("[/align]", ""),
            ("[alert]", ""),
            ("[/alert]", ""),
            ("[note]", ""),
            ("[/note]", ""),
            ("[h1]", "[u][b]"),
            ("[/h1]", "[/b][/u]"),
            ("[h2]", "[u][b]"),
            ("[/h2]", "[/b][/u]"),
            ("[h3]", "[u][b]"),
            ("[/h3]", "[/b][/u]"),
        )
        for source, target in replacements:
            cleaned = cleaned.replace(source, target)
        return re.sub(r"(\[img=\d+)]", "[img]", cleaned, flags=re.IGNORECASE)

    async def _book_intro(self, meta: Meta) -> list[str]:
        parts = ["[center]", f"[size=4][b]{meta.title}[/b][/size]"]
        if meta.author:
            parts.append(f"[size=3]por {meta.author}[/size]\n\n")
        cover = self.get_book_cover(meta)
        if cover:
            parts.append(await self.format_image(cover))
        parts.append("[/center]")
        return parts

    def _book_section(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        return builder._build_book_desc_section(
            meta, header_size=3, table=False
        )

    async def _write_description_file(
        self, meta: Meta, description: str
    ) -> None:
        path = (
            Path(str(meta.base_dir))
            / "tmp"
            / str(meta.uuid)
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as descfile:
            await descfile.write(description)

    async def build_book_description(self, meta: Meta) -> str:
        parts = await self._book_intro(meta)
        book_section = self._book_section(meta)
        if book_section:
            parts.append(book_section)
        base = base_description(meta).strip()
        if base:
            parts.append(self._clean_base_description(base))
        custom_header = self.config["DEFAULT"].get(
            "custom_description_header", ""
        )
        if custom_header:
            parts.append(str(custom_header) + "\n")
        parts.append(
            f"\n[center][url=https://github.com/wastaken7/Upload-Assistant]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/url][/center]"
        )
        description = "\n".join(filter(None, parts))
        await self._write_description_file(meta, description)
        return description

    @staticmethod
    def _layout_images(user_layout: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(value)
            for key, value in user_layout.items()
            if key.startswith("BARRINHA_") and value
        }

    async def _append_layout_section(
        self,
        parts: list[str],
        layout_images: dict[str, str],
        key: str,
        content: str | None,
    ) -> None:
        image = layout_images.get(key)
        if not content or not image:
            return
        parts.append(f"\n{await self.format_image(image)}")
        parts.append(f"\n{content}\n")

    @staticmethod
    def _description_tmdb_sections(
        meta: Meta,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        localized = meta.tmdb_localized_data.get("pt-BR", {})
        if not isinstance(localized, dict):
            return {}, {}, {}
        sections: list[dict[str, Any]] = []
        for key in ("season", "main", "episode"):
            value = localized.get(key, {})
            sections.append(dict(value) if isinstance(value, dict) else {})
        return sections[0], sections[1], sections[2]

    @staticmethod
    def _poster_url(
        meta: Meta, season: dict[str, Any], main: dict[str, Any]
    ) -> str:
        poster_path = (
            season.get("poster_path")
            or main.get("poster_path")
            or meta.tmdb_poster_path
        )
        return (
            f"https://image.tmdb.org/t/p/w500{poster_path}"
            if poster_path
            else ""
        )

    @staticmethod
    def _existing_overview(
        season: dict[str, Any], main: dict[str, Any]
    ) -> str:
        return str(season.get("overview") or main.get("overview") or "")

    def _skip_missing_overview(self, meta: Meta) -> None:
        logger.info(
            f"{self.tracker}: [yellow]No TMDb overview was found in unattended "
            f"mode. Skipping upload to {self.tracker}.[/yellow]"
        )
        meta.skipping = self.tracker

    async def _prompt_overview(self) -> str:
        raw = await prompt_in_thread(
            cli_ui.ask_string,
            f"{self.tracker}: No TMDb overview was found. Enter one manually.",
        )
        return (raw or "").strip() or "Sinopse não encontrada."

    async def _description_overview(
        self,
        meta: Meta,
        season: dict[str, Any],
        main: dict[str, Any],
    ) -> str | None:
        overview = self._existing_overview(season, main)
        if overview:
            return overview
        if meta.unattended and not meta.unattended_confirm:
            self._skip_missing_overview(meta)
            return None
        return await self._prompt_overview()

    @staticmethod
    def _episode_text(episode: dict[str, Any], key: str) -> str:
        value = episode.get(key)
        return str(value) if value else ""

    @classmethod
    def _episode_section_values(
        cls, episode: dict[str, Any]
    ) -> tuple[str, str, str]:
        return (
            cls._episode_text(episode, "name"),
            cls._episode_text(episode, "overview"),
            cls._episode_text(episode, "still_path"),
        )

    @classmethod
    def _episode_section_data(
        cls, meta: Meta, episode: dict[str, Any]
    ) -> tuple[str, str, str] | None:
        if meta.category != "TV" or not episode:
            return None
        values = cls._episode_section_values(episode)
        return values if all(values) else None

    async def _append_episode_section(
        self,
        parts: list[str],
        meta: Meta,
        episode: dict[str, Any],
    ) -> None:
        data = self._episode_section_data(meta, episode)
        if data is None:
            return
        name, overview, still_path = data
        still_url = f"https://image.tmdb.org/t/p/w300{still_path}"
        parts.append(f"\n[size=4][b]Episódio:[/b] {name}[/size]\n")
        parts.append(f"\n{await self.format_image(still_url)}\n\n{overview}\n")

    @staticmethod
    def _formatted_runtime(runtime: object) -> str:
        if not isinstance(runtime, (int, float)) or runtime <= 0:
            return ""
        hours, minutes = divmod(int(runtime), 60)
        if hours > 0:
            plural = "s" if hours > 1 else ""
            return f"{hours} hora{plural} e {minutes:02d} minutos"
        return f"{minutes:02d} minutos"

    @staticmethod
    def _description_release_date(
        meta: Meta,
        season: dict[str, Any],
        main: dict[str, Any],
        episode: dict[str, Any],
    ) -> str | None:
        value = (
            main.get("release_date")
            if meta.category == "MOVIE"
            else episode.get("air_date") or season.get("air_date")
        )
        return str(value) if value else None

    @staticmethod
    def _named_values(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return [
            str(item.get("name"))
            for item in values
            if isinstance(item, dict) and item.get("name")
        ]

    @staticmethod
    def _labeled_values(label: str, values: list[str]) -> str:
        return f"{label}: {', '.join(values)}" if values else ""

    async def _release_date_line(self, release_date: str | None) -> str:
        if release_date is None:
            return ""
        return f"Data de Lançamento: {await self.format_date(release_date)}"

    @staticmethod
    def _homepage_line(main: dict[str, Any]) -> str:
        homepage = main.get("homepage")
        return f"Site: [url={homepage}]Clique aqui[/url]" if homepage else ""

    @staticmethod
    def _technical_runtime(
        meta: Meta, main: dict[str, Any], episode: dict[str, Any]
    ) -> object:
        episode_runtime = episode.get("runtime")
        if episode_runtime:
            return episode_runtime
        main_runtime = main.get("runtime")
        return main_runtime if main_runtime else meta.runtime

    @staticmethod
    def _runtime_line(runtime_text: str) -> str:
        return f"Duração: {runtime_text}" if runtime_text else ""

    async def _technical_sheet(
        self,
        meta: Meta,
        season: dict[str, Any],
        main: dict[str, Any],
        episode: dict[str, Any],
    ) -> str:
        if not main:
            return ""
        runtime_text = self._formatted_runtime(
            self._technical_runtime(meta, main, episode)
        )
        release_date = self._description_release_date(
            meta, season, main, episode
        )
        items = [
            self._runtime_line(runtime_text),
            self._labeled_values(
                "País de Origem",
                self._named_values(main.get("production_countries")),
            ),
            self._labeled_values(
                "Gêneros", self._named_values(main.get("genres"))
            ),
            await self._release_date_line(release_date),
            self._homepage_line(main),
        ]
        return "\n".join(filter(None, items))

    async def _production_company_line(self, company: object) -> str:
        if not isinstance(company, dict):
            return ""
        logo_path = company.get("logo_path")
        logo = (
            await self.format_image(
                f"https://image.tmdb.org/t/p/w45{logo_path}"
            )
            if logo_path
            else ""
        )
        name = str(company.get("name", ""))
        prefix = f"{logo}[size=2] - " if logo else "[size=2]"
        return f"{prefix}[b]{name}[/b][/size]"

    async def _production_company_lines(self, main: dict[str, Any]) -> str:
        companies = main.get("production_companies", [])
        if not isinstance(companies, list) or not companies:
            return ""
        lines = [
            await self._production_company_line(company)
            for company in companies
        ]
        return "\n".join(
            ["[size=4][b]Produtoras[/b][/size]", *filter(None, lines)]
        )

    @staticmethod
    def _credits_cast(data: dict[str, Any]) -> list[dict[str, Any]]:
        credits = data.get("credits", {})
        if not isinstance(credits, dict):
            return []
        cast_data = credits.get("cast", [])
        if not isinstance(cast_data, list):
            return []
        return [item for item in cast_data if isinstance(item, dict)]

    @classmethod
    def _description_cast_data(
        cls,
        meta: Meta,
        season: dict[str, Any],
        main: dict[str, Any],
        episode: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if meta.category == "MOVIE":
            return cls._credits_cast(main)
        return cls._credits_cast(season if meta.tv_pack else episode)

    async def _season_spoiler(self, season: dict[str, Any]) -> str:
        name = str(
            season.get("name", f"Temporada {season.get('season_number')}")
        ).strip()
        poster_path = season.get("poster_path")
        poster = (
            await self.format_image(
                f"https://image.tmdb.org/t/p/w185{poster_path}"
            )
            if poster_path
            else ""
        )
        overview = (
            f"\n\nSinopse:\n{season.get('overview')}"
            if season.get("overview")
            else ""
        )
        lines: list[str] = []
        air_date = season.get("air_date")
        if air_date:
            lines.append(f"Data: {await self.format_date(air_date)}")
        episode_count = season.get("episode_count")
        if episode_count is not None:
            lines.append(f"Episódios: {episode_count}")
        lines.extend((poster, overview))
        return f"\n[spoiler={name}]{'\n'.join(lines)}[/spoiler]\n"

    async def _seasons_content(self, meta: Meta, main: dict[str, Any]) -> str:
        if meta.category != "TV":
            return ""
        seasons = main.get("seasons", [])
        if not isinstance(seasons, list):
            return ""
        parts = [
            await self._season_spoiler(season)
            for season in seasons
            if isinstance(season, dict)
        ]
        return "".join(parts)

    @staticmethod
    def _layout_ratings(user_layout: dict[str, Any]) -> list[dict[str, Any]]:
        raw = user_layout.get("Ratings", [])
        if not isinstance(raw, list):
            return []
        return [dict(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _imdb_rating(meta: Meta) -> dict[str, Any] | None:
        value = meta.imdb_info.get("rating")
        if not value:
            return None
        return {
            "Source": "Internet Movie Database",
            "Value": f"{value}/10",
        }

    @staticmethod
    def _append_tmdb_rating(
        ratings: list[dict[str, Any]], main: dict[str, Any]
    ) -> None:
        value = main.get("vote_average") if main else None
        if not value or any(item.get("Source") == "TMDb" for item in ratings):
            return
        ratings.append({"Source": "TMDb", "Value": f"{float(value):.1f}/10"})

    @classmethod
    def _description_ratings(
        cls, meta: Meta, user_layout: dict[str, Any], main: dict[str, Any]
    ) -> list[dict[str, Any]]:
        ratings = cls._layout_ratings(user_layout)
        if not ratings:
            imdb_rating = cls._imdb_rating(meta)
            if imdb_rating is not None:
                ratings.append(imdb_rating)
        cls._append_tmdb_rating(ratings, main)
        return ratings

    @staticmethod
    def _ratings_layout_key(meta: Meta, layout_images: dict[str, str]) -> str:
        if (
            meta.category == "MOVIE"
            and "BARRINHA_INFORMACOES" in layout_images
        ):
            return "BARRINHA_INFORMACOES"
        return "BARRINHA_CRITICAS"

    async def _append_custom_bars(
        self, parts: list[str], layout_images: dict[str, str], prefix: str
    ) -> None:
        parts.extend(
            [
                await self.format_image(layout_images.get(f"{prefix}_{index}"))
                for index in range(1, 4)
            ]
        )

    def _append_base_description(self, parts: list[str], meta: Meta) -> None:
        description = base_description(meta).strip()
        if description:
            parts.append(self._clean_base_description(description))
        custom_header = self.config["DEFAULT"].get(
            "custom_description_header", ""
        )
        if custom_header:
            parts.append(str(custom_header) + "\n")

    async def _media_description_parts(
        self,
        meta: Meta,
        user_layout: dict[str, Any],
        fileinfo_dump: str | None,
    ) -> list[str] | None:
        layout_images = self._layout_images(user_layout)
        parts = ["[center]"]
        await self._append_custom_bars(
            parts, layout_images, "BARRINHA_CUSTOM_T"
        )
        parts.append(
            f"\n{await self.format_image(layout_images.get('BARRINHA_APRESENTA'))}\n"
        )
        parts.append(f"\n[size=3]{await self.get_name(meta)}[/size]\n")
        season, main, episode = self._description_tmdb_sections(meta)
        poster = self._poster_url(meta, season, main)
        await self._append_layout_section(
            parts,
            layout_images,
            "BARRINHA_CAPA",
            await self.format_image(poster),
        )
        overview = await self._description_overview(meta, season, main)
        if overview is None:
            return None
        await self._append_layout_section(
            parts, layout_images, "BARRINHA_SINOPSE", overview
        )
        await self._append_episode_section(parts, meta, episode)
        technical = await self._technical_sheet(meta, season, main, episode)
        await self._append_layout_section(
            parts, layout_images, "BARRINHA_FICHA_TECNICA", technical
        )
        companies = await self._production_company_lines(main)
        if companies:
            parts.append(f"\n{companies}\n")
        cast_data = self._description_cast_data(meta, season, main, episode)
        await self._append_layout_section(
            parts,
            layout_images,
            "BARRINHA_ELENCO",
            await self.build_cast_bbcode(cast_data),
        )
        seasons = await self._seasons_content(meta, main)
        await self._append_layout_section(
            parts, layout_images, "BARRINHA_EPISODIOS", seasons
        )
        ratings = self._description_ratings(meta, user_layout, main)
        ratings_key = self._ratings_layout_key(meta, layout_images)
        await self._append_layout_section(
            parts,
            layout_images,
            ratings_key,
            await self.build_ratings_bbcode(meta, ratings),
        )
        if fileinfo_dump:
            parts.append(
                f"\n[spoiler=Informações do Arquivo]\n[left][font=Courier New]"
                f"{fileinfo_dump}[/font][/left][/spoiler]\n"
            )
        await self._append_custom_bars(
            parts, layout_images, "BARRINHA_CUSTOM_B"
        )
        parts.append("[/center]")
        self._append_base_description(parts, meta)
        parts.append(
            f"[center][url=https://github.com/wastaken7/Upload-Assistant]"
            f"Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/url][/center]"
        )
        return parts

    async def build_description(self, meta: Meta) -> str:
        if meta.category == "BOOK":
            return await self.build_book_description(meta)
        if meta.category == "GAME":
            return await self.build_game_description(meta)
        user_layout = await self.fetch_layout_data(meta)
        fileinfo_dump = await self.media_info(meta)
        if not user_layout:
            return "[center]Error: The description layout could not be loaded.[/center]"
        parts = await self._media_description_parts(
            meta, user_layout, fileinfo_dump
        )
        if parts is None:
            return ""
        final_description = "\n".join(filter(None, parts))
        await self._write_description_file(meta, final_description)
        return final_description

    async def get_trailer(self, meta: Meta) -> str:
        video_results = (
            meta.tmdb_localized_data.get("pt-BR", {})
            .get("main", {})
            .get("videos", {})
            .get("results", [])
        )
        youtube_code = (
            video_results[-1].get("key", "") if video_results else ""
        )
        return (
            f"http://www.youtube.com/watch?v={youtube_code}"
            if youtube_code
            else meta.youtube or ""
        )

    @classmethod
    def _tmdb_genre_names(cls, meta: Meta) -> list[str]:
        genres = cls._localized_main(meta).get("genres", [])
        names: list[str] = []
        for genre in genres:
            if not isinstance(genre, dict):
                continue
            name = genre.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name)
        return names

    def _skip_missing_genres(self, meta: Meta) -> str:
        logger.info(
            f"{self.tracker}: [yellow]No genres were found in unattended mode. "
            f"Skipping upload to {self.tracker}.[/yellow]"
        )
        meta.skipping = self.tracker
        return ""

    async def _manual_tags(self, meta: Meta) -> str:
        if meta.genre:
            return str(meta.genre).strip()
        if meta.unattended and not meta.unattended_confirm:
            return self._skip_missing_genres(meta)
        raw = await prompt_in_thread(
            cli_ui.ask_string,
            f"Enter genres in the {self.tracker} format: ",
        )
        return (raw or "").strip()

    async def get_tags(self, meta: Meta) -> str:
        tags = ", ".join(self._tmdb_genre_names(meta))
        return tags if tags else await self._manual_tags(meta)

    async def _fetch_file_info(
        self, torrent_id: str, torrent_link: str, size: str
    ) -> dict[str, str]:
        """
        Helper function to fetch file info for a single release in parallel.
        """
        file_page_url = (
            f"{self.base_url}/torrents-arquivos.php?id={torrent_id}"
        )
        filename = "N/A"

        try:
            file_page_response = await self.session.get(
                file_page_url, timeout=15
            )
            file_page_response.raise_for_status()
            file_page_soup = BeautifulSoup(
                file_page_response.text, "html.parser"
            )
            file_li_tag = file_page_soup.find("li", class_="list-group-item")

            if file_li_tag and file_li_tag.contents:
                first_content = file_li_tag.contents[0]
                filename = (
                    first_content.strip()
                    if isinstance(first_content, str)
                    else first_content.get_text(strip=True)
                )

        except Exception as e:
            logger.info(
                f"{self.tracker}: [bold red]Failed to retrieve the filename for torrent ID {torrent_id}: {e}[/bold red]"
            )

        return {"name": filename, "size": size, "link": torrent_link}

    def get_game_name(self, meta: Meta) -> str:
        """Build the torrent name for GAME category."""
        tag = str(meta.tag or "NoGroup").lstrip("-")

        name = f"{meta.title} [{tag}]"
        return re.sub(r"\s{2,}", " ", name).strip()

    def get_game_type(self, meta: Meta) -> str:
        """Map meta.platform to AMIGOSSHARE game category (type field) value."""
        platform_map: dict[str, str] = {
            "ANDROID": "57",
            "DREAMCAST": "52",
            "EMULATOR": "109",
            "DS": "58",
            "NDS": "58",
            "SWITCH": "110",
            "PC": "47",
            "MAC": "48",
            "PS1": "49",
            "PS2": "50",
            "PS3": "51",
            "PS4": "79",
            "PSP": "82",
            "WII": "55",
            "X360": "54",
            "XBOX": "56",
            "XONE": "78",
        }
        platform = meta.platform.upper().strip()
        return platform_map.get(platform, "47")  # Default to PC

    def get_game_genre(self, meta: Meta) -> str:
        """Map IGDB genres to AMIGOSSHARE genero field value."""
        genre_map: dict[str, str] = {
            "action": "1",
            "hack and slash": "1",
            "hack and slash/beat 'em up": "1",
            "adventure": "2",
            "point-and-click": "2",
            "visual novel": "2",
            "arcade": "3",
            "racing": "14",
            "driving": "14",
            "sport": "15",
            "sports": "15",
            "strategy": "22",
            "real time strategy": "22",
            "rts": "22",
            "turn-based strategy": "22",
            "tactical": "22",
            "shooter": "21",
            "fps": "21",
            "fighting": "13",
            "music": "16",
            "rhythm": "16",
            "puzzle": "18",
            "rpg": "12",
            "role-playing": "12",
            "role-playing (rpg)": "12",
            "simulation": "5",
            "simulator": "5",
            "board": "7",
            "board game": "7",
            "platform": "1",
            "platformer": "1",
        }
        genres_list = meta.genres or meta.keywords or []
        for genre in genres_list:
            genre_clean = genre.strip().lower()
            if genre_clean in genre_map:
                return genre_map[genre_clean]

        return "0"

    @staticmethod
    def _game_language_names(meta: Meta) -> list[str]:
        languages = meta.languages
        if not isinstance(languages, dict):
            return []
        return [str(name).lower() for name in languages]

    @staticmethod
    def _game_language_id(names: list[str]) -> str:
        mapping = (
            ("german", "3"),
            ("chinese", "9"),
            ("spanish", "1"),
            ("english", "4"),
            ("japanese", "8"),
            ("portuguese", "5"),
            ("russian", "2"),
        )
        for name in names:
            for token, language_id in mapping:
                if token in name:
                    return language_id
        return "6"

    @staticmethod
    def _has_portuguese_game_language(names: list[str]) -> bool:
        return any(
            "portuguese" in name or "português" in name for name in names
        )

    @classmethod
    def get_game_idioma(cls, meta: Meta) -> str:
        """Map game languages to AMIGOSSHARE idioma field value."""
        names = cls._game_language_names(meta)
        if not names:
            return "6"
        multilingual_pt = len(names) > 1 and cls._has_portuguese_game_language(
            names
        )
        return "7" if multilingual_pt else cls._game_language_id(names)

    async def build_game_description(self, meta: Meta) -> str:
        """Build GAME description using only the _build_game_desc_section block."""
        builder = DescriptionBuilder(self.tracker, self.config)
        desc_parts: list[str] = []

        game_section = builder._build_game_desc_section(
            meta, header_size=5, table=False
        )
        if game_section:
            desc_parts.append(game_section)

        desc_parts.append(await builder.get_user_description(meta))
        desc_parts.append(
            f"[center][url=https://github.com/wastaken7/Upload-Assistant]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/url][/center]"
        )

        final_description = "\n\n".join(
            part for part in desc_parts if part.strip()
        )

        final_desc_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(
            final_desc_path, "w", encoding="utf-8"
        ) as descfile:
            await descfile.write(final_description)

        return final_description

    async def _confirm_rule_exception(self, message: str, meta: Meta) -> bool:
        if getattr(meta, "unattended", False):
            return bool(getattr(meta, "unattended_confirm", False))
        return await self.common.prompt_user_for_confirmation(
            f"{self.tracker}: {message}"
        )

    @staticmethod
    def _metadata_values(meta: Meta, field: str) -> list[str]:
        value = getattr(meta, field, None)
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return []

    @classmethod
    def _has_prohibited_subject(cls, meta: Meta) -> bool:
        values = [
            str(getattr(meta, field, "") or "") for field in ("name", "title")
        ]
        values.extend(cls._metadata_values(meta, "genres"))
        values.extend(cls._metadata_values(meta, "keywords"))
        context = " ".join(values).casefold()
        return bool(
            re.search(
                r"(?<![a-z])(?:pedofilia|pedophilia|zoofilia|zoophilia)(?![a-z])",
                context,
            )
        )

    @staticmethod
    def _has_serial_or_key(description: str) -> bool:
        return bool(
            re.search(
                r"(?i)\b(?:serial(?:[ ._-]+(?:key|number))?|cd[ ._-]?key|product[ ._-]?key|license[ ._-]?key)\s*[:=]",
                description,
            )
        )

    @classmethod
    def _is_archive_file(cls, path: Path) -> bool:
        name = path.name.casefold()
        return path.suffix.casefold() in cls._ARCHIVE_EXTENSIONS or bool(
            re.search(r"(?:\.r\d{2,}|\.7z\.\d+)$", name)
        )

    @staticmethod
    def _is_advertising_file(path: Path) -> bool:
        name = path.name.casefold()
        return path.suffix.casefold() in {".torrent", ".url"} or any(
            marker in name
            for marker in ("downloaded from", "torrent downloaded", "www.")
        )

    @staticmethod
    def _has_valid_video_filename(path: Path) -> bool:
        name = path.stem
        resolution = re.search(
            r"(?<![A-Za-z0-9])(?:2160|1080|720|576|480|360|240)[pi]?(?![A-Za-z0-9])",
            name,
            re.IGNORECASE,
        )
        source = re.search(
            r"(?<![A-Za-z0-9])(?:UHD[ ._-]?BluRay|BluRay|BDRip|BRRip|WEB[ ._-]?DL|WEBRip|DVDRip|DVD|HDTV)(?![A-Za-z0-9])",
            name,
            re.IGNORECASE,
        )
        codec = re.search(
            r"(?<![A-Za-z0-9])(?:H[ .]?26[45]|x26[45]|HEVC|AVC|MPEG[ ._-]?2|XviD|DivX)(?![A-Za-z0-9])",
            name,
            re.IGNORECASE,
        )
        release = (
            re.search(r"^-[A-Za-z0-9][A-Za-z0-9._-]*$", name[codec.end() :])
            if codec
            else None
        )
        return bool(resolution and source and codec and release)

    @staticmethod
    def _is_unreleased_game_build(meta: Meta) -> bool:
        release_fields = " ".join(
            str(getattr(meta, field, "") or "")
            for field in ("type", "release_type", "license_type")
        )
        name = str(getattr(meta, "name", "") or "")
        return bool(
            re.search(
                r"(?i)(?<![A-Za-z0-9])(?:demo|beta|freeware|open[ ._-]?source)(?![A-Za-z0-9])",
                release_fields,
            )
            or re.search(
                r"(?i)(?:\(|\[)(?:demo|beta|freeware|open[ ._-]?source)(?:\)|\])",
                name,
            )
        )

    @staticmethod
    def _source_size(meta: Meta) -> int:
        try:
            return int(getattr(meta, "source_size", 0) or 0)
        except TypeError, ValueError, OverflowError:
            return 0

    def _category_supported(self, category: str) -> bool:
        if category in self.supported_categories:
            return True
        logger.info(
            f"{self.tracker}: [bold red]This category is not supported by the upload rules.[/bold red]"
        )
        return False

    def _book_size_allowed(self, category: str, source_size: int) -> bool:
        if category != "BOOK" or source_size > 1024 * 1024:
            return True
        logger.info(
            f"{self.tracker}: [bold red]BOOK uploads must be larger than 1 MB.[/bold red]"
        )
        return False

    async def _small_torrent_allowed(
        self, meta: Meta, category: str, source_size: int
    ) -> bool:
        requires_approval = (
            0 < source_size < 20 * 1024 * 1024 and category != "BOOK"
        )
        if not requires_approval:
            return True
        return await self._confirm_rule_exception(
            "Torrents below 20 MB require staff approval. Do you want to continue?",
            meta,
        )

    async def _size_rules_allowed(self, meta: Meta, category: str) -> bool:
        source_size = self._source_size(meta)
        if not self._book_size_allowed(category, source_size):
            return False
        return await self._small_torrent_allowed(meta, category, source_size)

    def _subject_allowed(self, meta: Meta) -> bool:
        if not self._has_prohibited_subject(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Content involving pedophilia or zoophilia is prohibited.[/bold red]"
        )
        return False

    @staticmethod
    def _adult_media(meta: Meta) -> bool:
        return bool(
            getattr(meta, "adult_media", False)
            or getattr(meta, "tmdb_adult_media", False)
            or getattr(meta, "nsfw", False)
        )

    @staticmethod
    def _release_name_context(meta: Meta) -> str:
        return " ".join(
            str(getattr(meta, field, "") or "") for field in ("name", "title")
        )

    def _adult_rules_allowed(self, meta: Meta, source_size: int) -> bool:
        if not self._adult_media(meta):
            return True
        if re.search(
            r"(?i)(?<![A-Za-z0-9])amateur(?![A-Za-z0-9])",
            self._release_name_context(meta),
        ):
            logger.info(
                f"{self.tracker}: [bold red]Amateur adult content is not allowed.[/bold red]"
            )
            return False
        if source_size >= 100 * 1024 * 1024:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Adult videos must be at least 100 MB.[/bold red]"
        )
        return False

    async def _initial_rules_allowed(self, meta: Meta, category: str) -> bool:
        if not self._category_supported(category):
            return False
        if not await self._size_rules_allowed(meta, category):
            return False
        if not self._subject_allowed(meta):
            return False
        return self._adult_rules_allowed(meta, self._source_size(meta))

    def _file_paths(self, meta: Meta) -> list[Path] | None:
        raw = getattr(meta, "filelist", None) or []
        if not isinstance(raw, (list, tuple, set)):
            logger.info(
                f"{self.tracker}: [bold red]Invalid file list.[/bold red]"
            )
            return None
        return [Path(str(item)) for item in raw if str(item).strip()]

    def _archive_rules_allowed(self, category: str, paths: list[Path]) -> bool:
        if category == "GAME" or not any(
            self._is_archive_file(path) for path in paths
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Archives are allowed only for games.[/bold red]"
        )
        return False

    def _advertising_rules_allowed(self, paths: list[Path]) -> bool:
        if not any(self._is_advertising_file(path) for path in paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Website references, advertisements, and nested .torrent files are not allowed.[/bold red]"
        )
        return False

    @staticmethod
    def _game_payload_paths(paths: list[Path]) -> list[Path]:
        ignored_names = {"readme", "readme.txt", "readme.md"}
        return [
            path
            for path in paths
            if path.suffix.casefold() != ".nfo"
            and path.name.casefold() not in ignored_names
        ]

    @staticmethod
    def _is_game_tool_payload(path: Path) -> bool:
        markers = (
            "crack",
            "keygen",
            "patch",
            "update",
            "traducao",
            "tradução",
        )
        stem = path.stem.casefold()
        return any(marker in stem for marker in markers)

    def _game_rules_allowed(self, meta: Meta, paths: list[Path]) -> bool:
        if self._is_unreleased_game_build(meta):
            logger.info(
                f"{self.tracker}: [bold red]Demos, betas, freeware, and open-source software are not allowed as torrents.[/bold red]"
            )
            return False
        payload = self._game_payload_paths(paths)
        if not payload or not all(
            self._is_game_tool_payload(path) for path in payload
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Cracks, keygens, patches, and updates cannot be uploaded separately.[/bold red]"
        )
        return False

    async def _description_rules_allowed(self, meta: Meta) -> bool:
        description = base_description(meta)
        if self._has_serial_or_key(description):
            logger.info(
                f"{self.tracker}: [bold red]The description cannot contain serials, CD keys, or license keys.[/bold red]"
            )
            return False
        valid = await self.common.check_portuguese_description_requirements(
            description, self.tracker, meta
        )
        if valid:
            return True
        logger.info(
            f"{self.tracker}: [bold red]The description was not identified as Portuguese. Skipping upload.[/bold red]"
        )
        return False

    async def _payload_rules_allowed(
        self, meta: Meta, category: str, paths: list[Path]
    ) -> bool:
        if not self._archive_rules_allowed(category, paths):
            return False
        if not self._advertising_rules_allowed(paths):
            return False
        if category == "GAME" and not self._game_rules_allowed(meta, paths):
            return False
        return await self._description_rules_allowed(meta)

    async def _validated_common_payload(
        self, meta: Meta, category: str
    ) -> list[Path] | None:
        if not await self._initial_rules_allowed(meta, category):
            return None
        paths = self._file_paths(meta)
        if paths is None:
            return None
        if not await self._payload_rules_allowed(meta, category, paths):
            return None
        return paths

    def _imdb_metadata_allowed(self, meta: Meta) -> bool:
        if getattr(meta, "imdb_id", None) or getattr(meta, "anime", False):
            return True
        logger.info(
            f"{self.tracker}: [bold red]IMDb metadata is required. Skipping upload.[/bold red]"
        )
        return False

    @classmethod
    def _video_paths(cls, paths: list[Path]) -> list[Path]:
        return [
            path
            for path in paths
            if path.suffix.casefold() in cls._VIDEO_EXTENSIONS
            and "sample" not in path.stem.casefold()
        ]

    @classmethod
    def _invalid_video_filename(cls, video_paths: list[Path]) -> str:
        for path in video_paths:
            if not cls._has_valid_video_filename(path):
                return path.name
        return ""

    def _video_files_allowed(
        self, meta: Meta, video_paths: list[Path]
    ) -> bool:
        if getattr(meta, "is_disc", None):
            return True
        if not video_paths:
            logger.info(
                f"{self.tracker}: [bold red]No recognized video file was found.[/bold red]"
            )
            return False
        invalid_name = self._invalid_video_filename(video_paths)
        if not invalid_name:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Video filename does not follow the ASC technical standard: {invalid_name}.[/bold red]"
        )
        return False

    @staticmethod
    def _screenshot_count(meta: Meta) -> int:
        try:
            return int(getattr(meta, "screens", 0) or 0)
        except TypeError, ValueError, OverflowError:
            return 0

    def _screenshots_allowed(
        self, meta: Meta, video_paths: list[Path], adult_media: bool
    ) -> bool:
        required = max(1, len(video_paths)) if adult_media else 1
        if self._screenshot_count(meta) >= required:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Real screenshots from the uploaded content are required.[/bold red]"
        )
        return False

    def _tv_episode_marker_allowed(
        self, meta: Meta, episode_count: int
    ) -> bool:
        if getattr(meta, "is_disc", None) or episode_count > 0:
            return True
        logger.info(
            f"{self.tracker}: [bold red]No valid TV episode marker was found in the uploaded files.[/bold red]"
        )
        return False

    async def _tv_pack_status_allowed(
        self, meta: Meta, tv_pack: bool, status: bool | None
    ) -> bool:
        if not tv_pack or status is True:
            return True
        return await self._confirm_rule_exception(
            "Packs are allowed only after the season or series has ended. Do you want to continue?",
            meta,
        )

    @staticmethod
    def _ongoing_episode_mode_invalid(
        tv_pack: bool, episode_count: int
    ) -> bool:
        return not tv_pack and episode_count > 1

    @staticmethod
    def _completed_episode_mode_invalid(
        tv_pack: bool, episode_count: int, status: bool | None
    ) -> bool:
        return bool(not tv_pack and episode_count and status is True)

    def _tv_episode_mode_allowed(
        self, tv_pack: bool, episode_count: int, status: bool | None
    ) -> bool:
        if self._ongoing_episode_mode_invalid(tv_pack, episode_count):
            logger.info(
                f"{self.tracker}: [bold red]Episodes from ongoing series must be uploaded individually.[/bold red]"
            )
            return False
        if self._completed_episode_mode_invalid(
            tv_pack, episode_count, status
        ):
            logger.info(
                f"{self.tracker}: [bold red]Completed series accept only complete season packs.[/bold red]"
            )
            return False
        return True

    async def _unknown_tv_status_allowed(
        self,
        meta: Meta,
        tv_pack: bool,
        episode_count: int,
        status: bool | None,
    ) -> bool:
        if tv_pack or not episode_count or status is not None:
            return True
        return await self._confirm_rule_exception(
            "The season status could not be confirmed. Do you want to continue?",
            meta,
        )

    def _tv_extras_allowed(
        self, tv_pack: bool, video_paths: list[Path]
    ) -> bool:
        has_extras = any(
            re.search(r"(?i)(?:extra|bonus|bônus)", path.stem)
            for path in video_paths
        )
        if tv_pack or not has_extras:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Extras must accompany the complete season or series.[/bold red]"
        )
        return False

    async def _tv_rules_allowed(
        self, meta: Meta, video_paths: list[Path]
    ) -> bool:
        episode_count = self.common.count_tv_episodes(video_paths)
        tv_pack = bool(getattr(meta, "tv_pack", False))
        if not self._tv_episode_marker_allowed(meta, episode_count):
            return False
        status = self.common.is_tv_series_ended(
            meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES
        )
        if not await self._tv_pack_status_allowed(meta, tv_pack, status):
            return False
        if not self._tv_episode_mode_allowed(tv_pack, episode_count, status):
            return False
        if not await self._unknown_tv_status_allowed(
            meta, tv_pack, episode_count, status
        ):
            return False
        return self._tv_extras_allowed(tv_pack, video_paths)

    @staticmethod
    def _inferior_dvd_source(meta: Meta) -> bool:
        if (
            not getattr(meta, "is_disc", None)
            or "dvd" not in str(meta.is_disc).casefold()
        ):
            return False
        context = " ".join(
            str(getattr(meta, field, "") or "")
            for field in ("name", "source", "type")
        )
        return bool(
            re.search(
                r"(?i)(?<![A-Za-z0-9])(?:R5|CAM|HDCAM|TC|TS|DVDSCR)(?![A-Za-z0-9])",
                context,
            )
        )

    def _dvd_source_allowed(self, meta: Meta) -> bool:
        if not self._inferior_dvd_source(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Authored DVD-R releases or DVD-R conversions from inferior sources are not allowed.[/bold red]"
        )
        return False

    def _media_file_rules_allowed(
        self, meta: Meta, video_paths: list[Path]
    ) -> bool:
        if not self._video_files_allowed(meta, video_paths):
            return False
        return self._screenshots_allowed(
            meta, video_paths, self._adult_media(meta)
        )

    async def _media_context_rules_allowed(
        self, meta: Meta, category: str, video_paths: list[Path]
    ) -> bool:
        if category == "TV" and not await self._tv_rules_allowed(
            meta, video_paths
        ):
            return False
        if not self._dvd_source_allowed(meta):
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    async def _media_rules_allowed(
        self, meta: Meta, category: str, paths: list[Path]
    ) -> bool:
        if not self._imdb_metadata_allowed(meta):
            return False
        video_paths = self._video_paths(paths)
        if not self._media_file_rules_allowed(meta, video_paths):
            return False
        return await self._media_context_rules_allowed(
            meta, category, video_paths
        )

    async def _category_specific_rules_allowed(
        self, meta: Meta, category: str, paths: list[Path]
    ) -> bool:
        if category in {"BOOK", "GAME"}:
            return True
        if category in {"MOVIE", "TV"}:
            return await self._media_rules_allowed(meta, category, paths)
        return True

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(getattr(meta, "category", "") or "").upper()
        paths = await self._validated_common_payload(meta, category)
        if paths is None:
            return False
        return await self._category_specific_rules_allowed(
            meta, category, paths
        )

    async def _load_search_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is not None:
            self.session.cookies = cast(Any, cookie_jar)

    def _book_search_url(self, meta: Meta) -> str:
        search_name = f"{meta.author} {meta.title}".strip()
        query = search_name.replace(" ", "+")
        return f"{self.base_url}/torrents-search.php?search={query}"

    def _game_search_url(self, meta: Meta) -> str:
        query = meta.title.replace(" ", "+")
        return (
            f"{self.base_url}/torrents-search.php?search={query}"
            f"&cat={self.get_game_type(meta)}"
        )

    async def _anime_search_url(self, meta: Meta) -> str:
        await self.load_localized_data(meta)
        query = (await self.get_name(meta)).replace(" ", "+")
        return f"{self.base_url}/torrents-search.php?search={query}"

    @staticmethod
    def _search_imdb(meta: Meta) -> str:
        configured = meta.imdb_info.get("imdbID")
        return str(configured or f"tt{str(meta.imdb_id).zfill(7)}")

    def _media_search_url(self, meta: Meta) -> str:
        imdb = self._search_imdb(meta)
        if meta.category == "MOVIE":
            return f"{self.base_url}/busca-filmes.php?search=&imdb={imdb}"
        return (
            f"{self.base_url}/busca-series.php?search="
            f"{meta.season}{meta.episode}&imdb={imdb}"
        )

    async def _search_url(self, meta: Meta) -> str | None:
        if meta.anime:
            return await self._anime_search_url(meta)
        builders = {
            "BOOK": self._book_search_url,
            "GAME": self._game_search_url,
        }
        builder = builders.get(meta.category)
        if builder is not None:
            return builder(meta)
        if meta.category in {"MOVIE", "TV"}:
            return self._media_search_url(meta)
        return None

    @staticmethod
    def _search_session_invalid(response: httpx.Response) -> bool:
        text = response.text
        return bool(
            "Esqueceu sua senha" in text
            or "login.php" in str(response.url)
            or "login.php" in text
        )

    async def _search_response(
        self, meta: Meta, search_url: str
    ) -> httpx.Response | None:
        response = await self.session.get(search_url, timeout=30)
        if self._search_session_invalid(response):
            await self.cookie_validator.handle_validation_failure(
                meta, self.tracker, response.text
            )
            meta.skipping = self.tracker
            return None
        response.raise_for_status()
        return response

    @staticmethod
    def _details_link(release: Any) -> Any | None:
        def has_details_link(href: str | None) -> bool:
            return bool(href and "torrents-details.php?id=" in href)

        return release.find("a", href=has_details_link)

    @staticmethod
    def _release_link(details_link: Any | None) -> str:
        value = details_link.get("href") if details_link is not None else None
        return value if isinstance(value, str) else ""

    @staticmethod
    def _release_size(release: Any) -> str:
        def has_size_text(text: str | None) -> bool:
            return bool(
                text and ("GB" in text.upper() or "MB" in text.upper())
            )

        tag = release.find("span", string=has_size_text, class_="badge-info")
        return tag.get_text(strip=True).strip() if tag else ""

    @staticmethod
    def _badge_texts(release: Any) -> list[str]:
        return [
            badge.text.strip()
            for badge in release.find_all("span", class_="badge")
        ]

    @staticmethod
    def _disc_type_badge(text: str) -> tuple[str, str] | None:
        types = {"BD25", "BD50", "BD66", "BD100", "DVD5", "DVD9"}
        return ("disk_type", text) if text.upper() in types else None

    @staticmethod
    def _year_badge(text: str) -> tuple[str, str] | None:
        return ("year", text) if text.isdigit() and len(text) == 4 else None

    @staticmethod
    def _resolution_badge(text: str) -> tuple[str, str] | None:
        upper = text.upper()
        if upper not in {"4K", "2160P", "1080P", "720P", "480P"}:
            return None
        return "resolution", "2160p" if upper == "4K" else text

    @staticmethod
    def _video_codec_badge(text: str) -> tuple[str, str] | None:
        terms = (
            "MPEG-4",
            "AV1",
            "AVC",
            "H264",
            "H265",
            "HEVC",
            "MPEG-1",
            "MPEG-2",
            "VC-1",
            "VP6",
            "VP9",
        )
        upper = text.upper()
        return (
            ("video_codec", text)
            if any(term in upper for term in terms)
            else None
        )

    @staticmethod
    def _audio_codec_badge(text: str) -> tuple[str, str] | None:
        terms = (
            "DTS",
            "AC3",
            "DDP",
            "E-AC-3",
            "TRUEHD",
            "ATMOS",
            "LPCM",
            "AAC",
            "FLAC",
        )
        upper = text.upper()
        return (
            ("audio_codec", text)
            if any(term in upper for term in terms)
            else None
        )

    @classmethod
    def _classified_badge(cls, text: str) -> tuple[str, str] | None:
        classifiers = (
            cls._year_badge,
            cls._resolution_badge,
            cls._video_codec_badge,
            cls._audio_codec_badge,
            cls._disc_type_badge,
        )
        for classifier in classifiers:
            result = classifier(text)
            if result is not None:
                return result
        return None

    @classmethod
    def _disc_fields(cls, badges: list[str]) -> dict[str, str]:
        fields = {
            "year": "N/A",
            "resolution": "N/A",
            "disk_type": "N/A",
            "video_codec": "N/A",
            "audio_codec": "N/A",
        }
        for badge in badges:
            classified = cls._classified_badge(badge)
            if classified is not None:
                key, value = classified
                fields[key] = value
        return fields

    @classmethod
    def _is_disc_result(cls, badges: list[str]) -> bool:
        return any(cls._disc_type_badge(badge) is not None for badge in badges)

    @classmethod
    def _disc_search_entry(
        cls, meta: Meta, badges: list[str], size: str, link: str
    ) -> dict[str, str]:
        fields = cls._disc_fields(badges)
        name = f"{meta.title} {fields['year']} {fields['resolution']} {fields['disk_type']} {fields['video_codec']} {fields['audio_codec']}"
        return {"name": name, "size": size, "link": link}

    @staticmethod
    def _game_search_entry(
        release: Any, size: str, link: str
    ) -> dict[str, str]:
        title_tag = release.select_one(".tooltips p a")
        title = title_tag.get_text(strip=True) if title_tag else "N/A"
        return {"name": title, "size": size, "link": link}

    def _release_search_result(
        self, meta: Meta, release: Any
    ) -> tuple[dict[str, str] | None, asyncio.Task[dict[str, str]] | None]:
        details_link = self._details_link(release)
        link = self._release_link(details_link)
        size = self._release_size(release)
        badges = self._badge_texts(release)
        if self._is_disc_result(badges):
            return self._disc_search_entry(meta, badges, size, link), None
        if details_link is None or not link:
            return None, None
        if meta.category == "GAME":
            return self._game_search_entry(release, size, link), None
        torrent_id = link.split("id=")[-1]
        return None, asyncio.create_task(
            self._fetch_file_info(torrent_id, link, size)
        )

    async def _parse_search_releases(
        self, meta: Meta, releases: list[Any]
    ) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        tasks: list[asyncio.Task[dict[str, str]]] = []
        for release in releases:
            entry, task = self._release_search_result(meta, release)
            if entry is not None:
                found.append(entry)
            if task is not None:
                tasks.append(task)
        if tasks:
            found.extend(await asyncio.gather(*tasks))
        return found

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        await self._load_search_cookies(meta)
        search_url = await self._search_url(meta)
        if search_url is None:
            return []
        response = await self._search_response(meta, search_url)
        if response is None:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        releases = list(
            soup.find_all("li", class_="list-group-item dark-gray")
        )
        return await self._parse_search_releases(meta, releases)

    async def get_upload_url(self, meta: Meta) -> str:
        if meta.category == "BOOK":
            return f"{self.base_url}/enviar-ebook.php"
        if meta.category == "GAME":
            return f"{self.base_url}/enviar-jogos.php"
        if meta.anime:
            return f"{self.base_url}/enviar-anime.php"
        if meta.category == "MOVIE":
            return f"{self.base_url}/enviar-filme.php"
        return f"{self.base_url}/enviar-series.php"

    async def format_image(self, url: str | None) -> str:
        return f"[img]{url}[/img]" if isinstance(url, str) and url else ""

    async def format_date(self, date_str: str | None) -> str:
        if not date_str or date_str == "N/A":
            return "N/A"

        def _try_format(fmt: str) -> str | None:
            try:
                return (
                    datetime.strptime(date_str, fmt)
                    .replace(tzinfo=UTC)
                    .strftime("%d/%m/%Y")
                )
            except ValueError, TypeError:
                return None

        for fmt in ("%Y-%m-%d", "%d %b %Y"):
            formatted = _try_format(fmt)
            if formatted:
                return formatted
        return date_str

    @staticmethod
    def _bd_summary_path(meta: Meta) -> Path:
        return (
            Path(str(meta.base_dir))
            / "tmp"
            / str(meta.uuid)
            / "BD_SUMMARY_00.txt"
        )

    @classmethod
    async def _bd_media_info(cls, meta: Meta) -> str | None:
        path = cls._bd_summary_path(meta)
        if not path.exists():
            return None
        async with aiofiles.open(path, encoding="utf-8") as file_handle:
            return await file_handle.read()

    @staticmethod
    def _file_media_info(meta: Meta) -> str | None:
        filelist = cast(list[str], meta.filelist or [])
        video_file = filelist[0] if filelist else str(meta.path or "")
        return (
            DescriptionBuilder.format_short_mediainfo_json(
                meta.mediainfo, video_file
            )
            or None
        )

    async def media_info(self, meta: Meta) -> str | None:
        if meta.is_disc == "BDMV":
            return await self._bd_media_info(meta)
        if not meta.is_disc:
            return self._file_media_info(meta)
        return None

    def _layout_cache_path(self, meta: Meta) -> Path:
        return (
            Path(meta.base_dir)
            / "tmp"
            / f"ASC_layout_cache_{self.layout}.json"
        )

    async def _read_layout_cache(
        self, cache_path: Path
    ) -> dict[str, Any] | None:
        if not cache_path.exists():
            return None
        try:
            async with aiofiles.open(
                cache_path, encoding="utf-8"
            ) as file_handle:
                return cast(
                    dict[str, Any], json.loads(await file_handle.read())
                )
        except OSError, json.JSONDecodeError:
            logger.info(
                f"{self.tracker}: [yellow]Failed to read cached layout data.[/yellow]"
            )
            return None

    async def _write_layout_cache(
        self, cache_path: Path, layout_data: dict[str, Any]
    ) -> None:
        if not layout_data:
            return
        try:
            async with aiofiles.open(
                cache_path, "w", encoding="utf-8"
            ) as file_handle:
                await file_handle.write(json.dumps(layout_data))
        except Exception as error:
            logger.error(
                f"{self.tracker}: [red]Failed to cache layout data: {error}[/red]"
            )

    async def _fetch_layout_payload(
        self,
        payload: dict[str, Any],
        cache_path: Path,
    ) -> dict[str, Any]:
        cached = await self._read_layout_cache(cache_path)
        if cached is not None:
            return cached
        try:
            response = await self.session.post(
                f"{self.base_url}/search.php", data=payload, timeout=20
            )
            response.raise_for_status()
            response_json = cast(dict[str, Any], response.json())
            layout_data = response_json.get("ASC", {})
        except Exception:
            return {}
        if not isinstance(layout_data, dict):
            return {}
        await self._write_layout_cache(cache_path, layout_data)
        return layout_data

    async def fetch_layout_data(self, meta: Meta) -> dict[str, Any]:
        cache_path = self._layout_cache_path(meta)
        primary_payload: dict[str, Any] = {
            "imdb": meta.imdb_info.get("imdbID")
            or f"tt{str(meta.imdb_id).zfill(7)}",
            "layout": self.layout,
        }
        layout_data = await self._fetch_layout_payload(
            primary_payload, cache_path
        )
        if layout_data:
            return layout_data
        fallback_payload: dict[str, Any] = {
            "imdb": "tt0013442",
            "layout": self.layout,
        }
        return await self._fetch_layout_payload(fallback_payload, cache_path)

    @staticmethod
    def _rating_icons() -> dict[str, str]:
        return {
            "Internet Movie Database": "[img]https://i.postimg.cc/Pr8Gv4RQ/IMDB.png[/img]",
            "Rotten Tomatoes": "[img]https://i.postimg.cc/rppL76qC/rotten.png[/img]",
            "Metacritic": "[img]https://i.postimg.cc/SKkH5pNg/Metacritic45x45.png[/img]",
            "TMDb": "[img]https://i.postimg.cc/T13yyzyY/tmdb.png[/img]",
        }

    @staticmethod
    def _imdb_rating_url(meta: Meta) -> str:
        configured = str(meta.imdb_info.get("imdb_url", "") or "")
        if configured:
            return configured
        return f"https://www.imdb.com/title/tt{str(meta.imdb_id).zfill(7)}"

    @classmethod
    def _rating_bbcode(
        cls,
        meta: Meta,
        source: str,
        value: str,
        image: str,
    ) -> str:
        if source == "Internet Movie Database":
            return f"\n[url={cls._imdb_rating_url(meta)}]{image}[/url]\n[b]{value}[/b]\n"
        if source == "TMDb" and meta.tmdb:
            url = f"https://www.themoviedb.org/{meta.category.lower()}/{meta.tmdb}"
            return f"[url={url}]{image}[/url]\n[b]{value}[/b]\n"
        return f"{image}\n[b]{value}[/b]\n"

    async def build_ratings_bbcode(
        self, meta: Meta, ratings_list: list[dict[str, Any]]
    ) -> str:
        icons = self._rating_icons()
        parts: list[str] = []
        for rating in ratings_list:
            source = rating.get("Source")
            if not isinstance(source, str) or source not in icons:
                continue
            value = str(rating.get("Value", "")).strip()
            parts.append(
                self._rating_bbcode(meta, source, value, icons[source])
            )
        return "\n".join(parts)

    async def build_cast_bbcode(self, cast_list: list[dict[str, Any]]) -> str:
        if not cast_list:
            return ""

        parts: list[str] = []
        for person in cast_list[:10]:
            profile_path = person.get("profile_path")
            profile_url = (
                f"https://image.tmdb.org/t/p/w45{profile_path}"
                if profile_path
                else "https://i.imgur.com/eCCCtFA.png"
            )
            tmdb_url = f"https://www.themoviedb.org/person/{person.get('id')}?language=pt-BR"
            img_tag = await self.format_image(profile_url)
            character_info = f"({person.get('name', '')}) como {person.get('character', '')}"
            parts.append(
                f"[url={tmdb_url}]{img_tag}[/url]\n[size=2][b]{character_info}[/b][/size]\n"
            )
        return "".join(parts)

    def _request_search_enabled(self, meta: Meta) -> bool:
        return bool(
            self.config["DEFAULT"].get("search_requests", False)
            or meta.search_requests
        )

    async def _load_request_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is not None:
            self.session.cookies = cast(Any, cookie_jar)

    async def _request_category(self, meta: Meta) -> int | str:
        if meta.category in {"BOOK", "GAME"}:
            return await self.get_type(meta)
        if meta.anime:
            return 118 if meta.category == "TV" else 116
        return 120 if meta.category == "TV" else 119

    async def _request_url(self, meta: Meta) -> str:
        category = await self._request_category(meta)
        return f"{self.requests_url}?search={meta.title}&category={category}"

    @staticmethod
    def _request_row_cells(row: Any) -> list[Any]:
        return list(row.find_all("td"))

    @staticmethod
    def _request_link(info_cell: Any) -> Any | None:
        return info_cell.select_one('a[href*="pedidos.php?action=ver"]')

    @classmethod
    def _parse_request_row(cls, row: Any) -> dict[str, str] | None:
        cells = cls._request_row_cells(row)
        if len(cells) < 6:
            return None
        link_element = cls._request_link(cells[1])
        if link_element is None:
            return None
        href = link_element.get("href")
        return {
            "Name": link_element.text.strip(),
            "Reward": cells[4].text.strip(),
            "Link": str(href) if href is not None else "",
        }

    @classmethod
    def _parse_request_results(cls, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        for row in soup.select(".table-responsive table tr"):
            parsed = cls._parse_request_row(row)
            if parsed is not None:
                results.append(parsed)
        return results

    def _request_results_message(self, results: list[dict[str, str]]) -> str:
        lines = [
            "",
            f"{self.tracker}: [bold yellow]Your upload may fill the following request(s):[/bold yellow]",
            "",
        ]
        for result in results:
            lines.extend(
                [
                    f"[bold green]Name:[/bold green] {result['Name']}",
                    f"[bold green]Reward:[/bold green] {result['Reward']}",
                    f"[bold green]Link:[/bold green] {self.base_url}/{result['Link']}",
                    "",
                ]
            )
        return "\n".join(lines)

    async def _request_results(self, meta: Meta) -> list[dict[str, str]]:
        response = await self.session.get(await self._request_url(meta))
        response.raise_for_status()
        return self._parse_request_results(response.text)

    async def get_requests(self, meta: Meta) -> bool | list[dict[str, str]]:
        if not self._request_search_enabled(meta):
            return False
        await self._load_request_cookies(meta)
        try:
            results = await self._request_results(meta)
        except httpx.HTTPError as error:
            logger.info(
                f"{self.tracker}: [bold red]An error occurred while searching "
                f"requests on {self.tracker}: {error}[/bold red]"
            )
            import traceback

            logger.info(traceback.format_exc())
            return []
        if results:
            logger.info(self._request_results_message(results))
        return results

    @staticmethod
    def _base_upload_data(
        meta: Meta, name: str, description: str
    ) -> dict[str, Any]:
        return {
            "takeupload": "yes",
            "name": name,
            "descr": description,
            "ano": str(meta.year) if meta.year is not None else "",
        }

    @staticmethod
    def _book_language_code(meta: Meta) -> str:
        language = str(
            meta.book_language_iso or meta.book_language or ""
        ).lower()
        mapping = {
            "chi": "9",
            "de": "3",
            "deu": "3",
            "en": "4",
            "eng": "4",
            "es": "1",
            "esp": "1",
            "ger": "3",
            "ja": "8",
            "jpn": "8",
            "ko": "11",
            "kor": "11",
            "por": "5",
            "pt": "5",
            "ru": "2",
            "rus": "2",
            "spa": "1",
            "zh": "9",
            "zho": "9",
        }
        return mapping.get(language, "6")

    @staticmethod
    def _raw_image_url(image: object) -> str:
        if not isinstance(image, dict):
            return ""
        return str(image.get("raw_url") or "")

    @classmethod
    def _screenshot_fields(
        cls, images: list[Any], limit: int = 4
    ) -> dict[str, str]:
        fields: dict[str, str] = {}
        for index, image in enumerate(images[:limit], start=1):
            raw_url = cls._raw_image_url(image)
            if raw_url:
                fields[f"screens{index}"] = raw_url
        return fields

    async def _ensure_description_languages(self, meta: Meta) -> None:
        if meta.language_checked:
            return
        await languages_manager.process_desc_language(
            meta, tracker=self.tracker
        )

    async def _book_upload_data(
        self, meta: Meta, upload_type: object
    ) -> dict[str, Any]:
        await self._ensure_description_languages(meta)
        cover_url = self.get_book_cover(meta)
        data: dict[str, Any] = {
            "capa": cover_url,
            "extencao": await self.get_container(meta),
            "idioma": self._book_language_code(meta),
            "screens1": meta.author,
            "screens2": cover_url,
            "screens3": cover_url,
            "type": upload_type,
        }
        images = list(meta.image_list or [])
        if images:
            data["screens2"] = self._raw_image_url(images[0])
        if len(images) > 1:
            data["screens3"] = self._raw_image_url(images[1])
        return data

    async def _game_upload_data(
        self, meta: Meta, upload_type: object
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "capa": meta.artwork_url,
            "genero": self.get_game_genre(meta),
            "idioma": self.get_game_idioma(meta),
            "type": upload_type,
        }
        data.update(self._screenshot_fields(list(meta.image_list or [])))
        return data

    @staticmethod
    def _media_poster(meta: Meta) -> str:
        localized = meta.tmdb_localized_data.get("pt-BR", {})
        main = localized.get("main", {}) if isinstance(localized, dict) else {}
        poster = main.get("poster_path") if isinstance(main, dict) else None
        path = poster or meta.tmdb_poster_path
        return f"https://image.tmdb.org/t/p/w500{path}"

    @classmethod
    def _media_language_code(cls, meta: Meta) -> str:
        if not meta.original_language:
            return "1"
        return cls.language_map.get(meta.original_language.lower(), "11")

    @staticmethod
    def _media_imdb(meta: Meta) -> str:
        configured = meta.imdb_info.get("imdbID")
        return str(configured or f"tt{str(meta.imdb_id).zfill(7)}")

    async def _media_upload_data(
        self, meta: Meta, upload_type: object
    ) -> dict[str, Any]:
        await self._ensure_description_languages(meta)
        resolution = await self.get_resolution(meta)
        data: dict[str, Any] = {
            "altura": resolution["height"],
            "audio": await self.get_audio(meta),
            "capa": self._media_poster(meta),
            "codecaudio": await self.get_audio_codec(meta),
            "codecvideo": await self.get_video_codec(meta),
            "extencao": await self.get_container(meta),
            "genre": await self.get_tags(meta),
            "imdb": self._media_imdb(meta),
            "lang": self._media_language_code(meta),
            "largura": resolution["width"],
            "layout": self.layout,
            "legenda": await self.get_subtitle(meta),
            "qualidade": upload_type,
            "tresd": "1" if meta.three_d else "2",
            "tube": await self.get_trailer(meta),
        }
        await self._apply_anime_upload_data(meta, data)
        data.update(self._screenshot_fields(list(meta.image_list or [])))
        return data

    async def _apply_anime_upload_data(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        if not meta.anime:
            return
        anime_info = await self.get_languages(meta)
        if not anime_info:
            return
        data.update(
            {
                "idioma": anime_info["idioma"],
                "lang": anime_info["lang"],
                "type": anime_info["type"],
            }
        )

    async def _category_upload_data(
        self, meta: Meta, upload_type: object
    ) -> dict[str, Any]:
        if meta.category == "BOOK":
            return await self._book_upload_data(meta, upload_type)
        if meta.category == "GAME":
            return await self._game_upload_data(meta, upload_type)
        return await self._media_upload_data(meta, upload_type)

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        await self.load_localized_data(meta)
        description = await self.build_description(meta)
        upload_type = await self.get_type(meta)
        data = self._base_upload_data(
            meta, await self.get_name(meta), description
        )
        data.update(await self._category_upload_data(meta, upload_type))
        return data

    def _upload_preconditions_allowed(self, meta: Meta) -> bool:
        if getattr(meta, "skipping", None) == self.tracker:
            return False
        if meta.category != "BOOK" or meta.source_size > 1024 * 1024:
            return True
        logger.info(
            f"{self.tracker}: [bold red]BOOK uploads must be larger than 1 MB.[/bold red]"
        )
        return False

    async def _load_upload_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is not None:
            self.session.cookies = cast(Any, cookie_jar)

    async def _perform_upload(
        self, meta: Meta, data: dict[str, Any], upload_url: str
    ) -> bool:
        return await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            data=data,
            torrent_field_name="torrent",
            upload_cookies=self.session.cookies,
            upload_url=upload_url,
            id_pattern=r"torrents-details\.php\?id=(\d+)",
            success_text="torrents-details.php?id=",
        )

    def _internal_upload(self, meta: Meta) -> bool:
        if not meta.tag:
            return False
        tracker_config = self.config["TRACKERS"][self.tracker]
        if tracker_config.get("internal", False) is not True:
            return False
        return meta.tag[1:] in tracker_config.get("internal_groups", [])

    async def _post_upload_actions(self, meta: Meta) -> None:
        if await self.get_approval(meta):
            await self.auto_approval(meta)
        if self._internal_upload(meta):
            await self.set_internal_flag(meta)

    async def upload(self, meta: Meta) -> bool:
        if not self._upload_preconditions_allowed(meta):
            return False
        await self._load_upload_cookies(meta)
        data = await self.get_data(meta)
        if getattr(meta, "skipping", None) == self.tracker:
            return False
        uploaded = await self._perform_upload(
            meta, data, await self.get_upload_url(meta)
        )
        if not uploaded:
            return False
        await self._post_upload_actions(meta)
        return True

    async def auto_approval(self, meta: Meta) -> None:
        if meta.debug:
            logger.debug(
                f"{self.tracker}: Debug mode, skipping automatic approval."
            )
        else:
            torrent_id = meta.tracker_status[self.tracker]["torrent_id"]
            try:
                approval_url = (
                    f"{self.base_url}/uploader_app.php?id={torrent_id}"
                )
                approval_response = await self.session.get(
                    approval_url, timeout=30
                )
                approval_response.raise_for_status()
            except Exception as e:
                logger.info(
                    f"{self.tracker}: [bold red]Error during automatic approval attempt: {e}[/bold red]"
                )

    async def get_approval(self, meta: Meta) -> bool:
        if not self.config["TRACKERS"][self.tracker].get(
            "uploader_status", False
        ):
            return False

        if meta.modq:
            logger.info(f"{self.tracker}: Sending to the moderation queue.")
            return False

        return True

    async def set_internal_flag(self, meta: Meta) -> None:
        if meta.debug:
            logger.debug(
                f"{self.tracker}: [bold yellow]Debug mode, skipping setting internal flag.[/bold yellow]"
            )
        else:
            data: dict[str, str] = {
                "id": meta.tracker_status[self.tracker]["torrent_id"],
                "internal": "yes",
            }

            try:
                response = await self.session.post(
                    f"{self.base_url}/torrents-edit.php?action=doedit",
                    data=data,
                )
                response.raise_for_status()

            except Exception as e:
                logger.info(
                    f"{self.tracker}: [bold red]Error setting internal flag: {e}[/bold red]"
                )
                return
