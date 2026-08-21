# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import re
import zipfile
from pathlib import Path
from typing import Any, ClassVar, cast

import aiofiles
import cli_ui
import fitz
import httpx
import langcodes
import rarfile
from bs4 import BeautifulSoup
from langcodes.tag_parser import LanguageTagError
from rich.markup import escape
from unidecode import unidecode

from src.domain_models.genre_mapping import ENG_TO_PTBR_GENRE_MAP
from src.domain_models.release import Meta
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
from src.integrations.trackers.description_builder import (
    DescriptionBuilder,
    html_to_bbcode,
)


class BrasilTracker:
    """
    BT Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "BRASILTRACKER"
    display_name = "BrasilTracker"
    banned_groups: tuple[str, ...] = ()
    source_flag = "BT"
    base_url = "https://brasiltracker.org"
    auth_token: str | None = None
    torrent_url = f"{base_url}/torrents.php?id="
    ultimate_lang_map: ClassVar[dict[str, str]] = {}
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("t.brasiltracker.org",)
    allows_bloated_audio = True
    secret_token: str = ""
    tmdb_localization_requirements: ClassVar = {
        "pt-BR": {
            "main": "credits,videos,content_ratings",
            "episode": "",
        }
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config: dict[str, Any] = config
        self.main_tmdb_data: dict[str, Any] = {}
        self.episode_tmdb_data: dict[str, Any] = {}
        self.tmdb_manager = TmdbManager(config)
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": f"Upload-Assistant ({platform.system()} {platform.release()})"
            },
            timeout=60.0,
        )

        target_site_ids = {
            "arabic": "22",
            "bulgarian": "29",
            "chinese": "14",
            "croatian": "23",
            "czech": "30",
            "danish": "10",
            "dutch": "9",
            "english - forçada": "50",
            "english": "3",
            "estonian": "38",
            "finnish": "15",
            "french": "5",
            "german": "6",
            "greek": "26",
            "hebrew": "40",
            "hindi": "41",
            "hungarian": "24",
            "icelandic": "28",
            "indonesian": "47",
            "italian": "16",
            "japanese": "8",
            "korean": "19",
            "latvian": "37",
            "lithuanian": "39",
            "norwegian": "12",
            "persian": "52",
            "polish": "17",
            "português": "49",
            "romanian": "13",
            "russian": "7",
            "serbian": "31",
            "slovak": "42",
            "slovenian": "43",
            "spanish": "4",
            "swedish": "11",
            "thai": "20",
            "turkish": "18",
            "ukrainian": "34",
            "vietnamese": "25",
        }

        source_alias_map: dict[tuple[str, ...], str] = {
            ("Arabic", "ara", "ar"): "arabic",
            (
                "Brazilian Portuguese",
                "Brazilian",
                "Portuguese-BR",
                "pt-br",
                "pt-BR",
                "Portuguese",
                "por",
                "pt",
                "pt-PT",
                "Português Brasileiro",
                "Português",
            ): "português",
            ("Bulgarian", "bul", "bg"): "bulgarian",
            (
                "Chinese",
                "chi",
                "zh",
                "Chinese (Simplified)",
                "Chinese (Traditional)",
                "cmn-Hant",
                "cmn-Hans",
                "yue-Hant",
                "yue-Hans",
            ): "chinese",
            ("Croatian", "hrv", "hr", "scr"): "croatian",
            ("Czech", "cze", "cz", "cs"): "czech",
            ("Danish", "dan", "da"): "danish",
            ("Dutch", "dut", "nl"): "dutch",
            (
                "English - Forced",
                "English (Forced)",
                "en (Forced)",
                "en-US (Forced)",
            ): "english - forçada",
            (
                "English",
                "eng",
                "en",
                "en-US",
                "en-GB",
                "English (CC)",
                "English - SDH",
            ): "english",
            ("Estonian", "est", "et"): "estonian",
            ("Finnish", "fin", "fi"): "finnish",
            ("French", "fre", "fr", "fr-FR", "fr-CA"): "french",
            ("German", "ger", "de"): "german",
            ("Greek", "gre", "el"): "greek",
            ("Hebrew", "heb", "he"): "hebrew",
            ("Hindi", "hin", "hi"): "hindi",
            ("Hungarian", "hun", "hu"): "hungarian",
            ("Icelandic", "ice", "is"): "icelandic",
            ("Indonesian", "ind", "id"): "indonesian",
            ("Italian", "ita", "it"): "italian",
            ("Japanese", "jpn", "ja"): "japanese",
            ("Korean", "kor", "ko"): "korean",
            ("Latvian", "lav", "lv"): "latvian",
            ("Lithuanian", "lit", "lt"): "lithuanian",
            ("Norwegian", "nor", "no"): "norwegian",
            ("Persian", "fa", "far"): "persian",
            ("Polish", "pol", "pl"): "polish",
            ("Romanian", "rum", "ro"): "romanian",
            ("Russian", "rus", "ru"): "russian",
            ("Serbian", "srp", "sr", "scc"): "serbian",
            ("Slovak", "slo", "sk"): "slovak",
            ("Slovenian", "slv", "sl"): "slovenian",
            ("Spanish", "spa", "es", "es-ES", "es-419"): "spanish",
            ("Swedish", "swe", "sv"): "swedish",
            ("Thai", "tha", "th"): "thai",
            ("Turkish", "tur", "tr"): "turkish",
            ("Ukrainian", "ukr", "uk"): "ukrainian",
            ("Vietnamese", "vie", "vi"): "vietnamese",
        }

        for aliases_tuple, canonical_name in source_alias_map.items():
            if canonical_name in target_site_ids:
                correct_id = target_site_ids[canonical_name]
                for alias in aliases_tuple:
                    self.ultimate_lang_map[alias.lower()] = correct_id

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if not cookie_jar:
            return False
        self.session.cookies = cast(Any, cookie_jar)
        return True

    async def load_localized_data(self, meta: Meta) -> None:
        if meta.category in ("MOVIE", "TV"):
            ptbr_data = meta.tmdb_localized_data.get("pt-BR")
            if not ptbr_data or not ptbr_data.get("main"):
                raise RuntimeError(
                    f"{self.tracker}: Missing TMDB localized data (pt-BR)."
                )

            self.main_tmdb_data = ptbr_data["main"]
            self.episode_tmdb_data = ptbr_data.get("episode") or {}
            meta.episode_tmdb_data = self.episode_tmdb_data

    async def get_container(self, meta: Meta) -> str:
        container = str(meta.container or "")
        if meta.category == "BOOK":
            return self._book_container(meta, container.lower())
        return self._video_container(container)

    @classmethod
    def _book_container(cls, meta: Meta, container: str) -> str:
        if meta.audiobook:
            return cls._audiobook_container_map().get(container, "Outro")
        if meta.magazine or meta.comic:
            return cls._comic_container_map().get(container, "Outro")
        return cls._ebook_container_map().get(container, "")

    @staticmethod
    def _audiobook_container_map() -> dict[str, str]:
        return {
            "acc": "ACC",
            "aac": "ACC",
            "ac3": "AC3",
            "dff": "DFF",
            "mp2": "MP2",
            "dsf": "DSF",
            "flac": "FLAC",
            "m4a": "M4A",
            "m4b": "M4B",
            "mp3": "MP3",
            "ogg": "OGG",
            "wav": "WAV",
            "wma": "WMA",
        }

    @staticmethod
    def _comic_container_map() -> dict[str, str]:
        return {
            "cbr": "CBR",
            "cbz": "CBR",
            "docx": "DOCX",
            "doc": "DOC",
            "epub": "ePUB",
            "gif": "GIF",
            "img": "IMG",
            "iso": "ISO",
            "jpg": "JPG",
            "jpeg": "JPG",
            "mobi": "MOBI",
            "nrg": "NRG",
            "pdf": "PDF",
            "png": "PNG",
        }

    @staticmethod
    def _ebook_container_map() -> dict[str, str]:
        return {
            "azw3": "AZW3",
            "mobi": "MOBI",
            "pdf": "PDF",
            "epub": "ePub",
            "kfx": "KFX",
        }

    @staticmethod
    def _video_container(container: str) -> str:
        allowed = {"avi", "m2ts", "m4v", "mkv", "mp4", "ts", "vob", "wmv"}
        return container.upper() if container in allowed else "Outro"

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self._game_installation_policy(meta):
            return False
        if not self._imdb_policy(meta):
            return False
        return await self._portuguese_video_policy(meta)

    async def _game_installation_policy(self, meta: Meta) -> bool:
        if meta.category != "GAME" or meta.platform.upper().strip() not in {
            "PC",
            "MAC",
            "LINUX",
        }:
            return True
        builder = DescriptionBuilder(self.tracker, self.config)
        if await builder.get_user_description(meta):
            return True
        logger.info(
            f"{self.tracker}: [red]Installation notes are required for PC game uploads. Please provide them using [bold]-df[/bold] (path/to/file.txt) or [bold]-pb[/bold] (link to raw text).[/red]"
        )
        return False

    def _imdb_policy(self, meta: Meta) -> bool:
        if meta.category in {"BOOK", "GAME"}:
            return True
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        if imdb.get("imdbID") or meta.anime:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Ignorando upload devido à ausência de IMDb.[/bold red]"
        )
        return False

    async def _portuguese_video_policy(self, meta: Meta) -> bool:
        if meta.category not in {"MOVIE", "TV"}:
            return True
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    async def get_type(self, meta: Meta) -> str | None:
        if meta.anime:
            return "5"
        if meta.category == "BOOK":
            return self._book_type(meta)
        return {"TV": "1", "MOVIE": "0", "GAME": "8"}.get(str(meta.category))

    @staticmethod
    def _book_type(meta: Meta) -> str:
        if meta.audiobook:
            return "15"
        if meta.magazine:
            return "9"
        if meta.comic:
            return "11"
        return "12"

    def get_game_language(self, meta: Meta) -> str:
        """Map game languages from IGDB to BRASILTRACKER idioma_ori."""
        names = self._game_language_names(meta.languages)
        return self._game_language_label(names)

    @classmethod
    def _game_language_label(cls, names: list[str]) -> str:
        if not names:
            return ""
        if cls._multiple_with_portuguese(names):
            return "Multilinguagem"
        if len(names) == 1:
            return cls._mapped_game_language(names[0])
        return cls._first_mapped_language(names)

    @classmethod
    def _multiple_with_portuguese(cls, names: list[str]) -> bool:
        return len(names) > 1 and cls._has_portuguese_language(
            [name.lower() for name in names]
        )

    @classmethod
    def _first_mapped_language(cls, names: list[str]) -> str:
        for name in names:
            value = cls._mapped_game_language(name)
            if value != name:
                return value
        return names[0]

    @staticmethod
    def _game_language_names(value: Any) -> list[str]:
        return [str(name) for name in value] if isinstance(value, dict) else []

    @staticmethod
    def _has_portuguese_language(names: list[str]) -> bool:
        return any(
            "portuguese" in name or "português" in name for name in names
        )

    @classmethod
    def _mapped_game_language(cls, name: str) -> str:
        lowered = name.lower()
        mapping = cls._game_language_map()
        return next(
            (value for key, value in mapping.items() if key in lowered), name
        )

    @staticmethod
    def _game_language_map() -> dict[str, str]:
        return {
            "german": "Alemão",
            "spanish": "Espanhol",
            "french": "Francês",
            "english": "Inglês",
            "japanese": "Japonês",
            "portuguese": "Português",
            "russian": "Russo",
        }

    def get_game_genre(self, meta: Meta) -> str:
        values = meta.genres or meta.keywords or []
        return self._mapped_game_genre(values)

    @classmethod
    def _mapped_game_genre(cls, values: Any) -> str:
        genres = values if isinstance(values, list) else []
        for genre in genres:
            match = cls._first_game_genre_match(str(genre).strip().lower())
            if match:
                return match
        return ""

    @classmethod
    def _first_game_genre_match(cls, genre: str) -> str:
        return next(
            (
                value
                for key, value in cls._game_genre_map().items()
                if key in genre
            ),
            "",
        )

    @staticmethod
    def _game_genre_map() -> dict[str, str]:
        return {
            "action": "Ação",
            "adventure": "Aventura",
            "arcade": "Arcade",
            "card": "Jogos de Cartas e Tabuleiro",
            "board": "Jogos de Cartas e Tabuleiro",
            "racing": "Corrida",
            "driving": "Corrida",
            "sport": "Esporte",
            "sports": "Esporte",
            "strategy": "Estratégia Baseada em Turnos",
            "real time strategy": "RTS - Estratégia em Tempo Real",
            "turn-based strategy": "Estratégia Baseada em Turnos",
            "shooter": "Tiro",
            "fighting": "Luta",
            "moba": "Moba",
            "music": "Musical",
            "rhythm": "Musical",
            "platform": "Plataforma",
            "puzzle": "Puzzle",
            "rpg": "RPG",
            "role-playing": "RPG",
            "simulation": "Simulador",
            "simulator": "Simulador",
            "horror": "Terror",
            "hack and slash": "Hack and Slash Beat em Up",
            "indie": "Indie",
            "point-and-click": "Point and Click",
            "visual novel": "Ficção",
        }

    def get_game_platform_bt(self, meta: Meta) -> str:
        """Map meta.platform to BRASILTRACKER plataforma_jogo dropdown value."""
        nin_term = (
            bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        ).capitalize()
        platform_map: dict[str, str] = {
            "PC": "PC",
            "MAC": "PC",
            "LINUX": "PC",
            "MOBILE": "Celular/Tablet",
            "EMULATOR": "Emulador",
            "PS1": "PS1",
            "PS2": "PS2",
            "PS3": "PS3",
            "PS4": "PS4",
            "PSVITA": "PS Vita",
            "SWITCH": f"{nin_term} Switch",
            "WII": "Wii",
            "WIIU": "Wii U",
            "XBOX": "Xbox Clássico",
            "X360": "Xbox 360",
            "XONE": "Multiplataforma",
            "XSX": "Multiplataforma",
        }

        platform = meta.platform.upper().strip()
        return platform_map.get(platform, "")

    def get_game_os(self, meta: Meta) -> str:
        platform_name = meta.platform.upper().strip()
        direct = {
            "PC": "Windows",
            "MAC": "Mac",
            "LINUX": "Linux",
            "MOBILE": "Android",
        }.get(platform_name)
        if direct:
            return direct
        return "Console" if platform_name in self._console_platforms() else ""

    @staticmethod
    def _console_platforms() -> set[str]:
        return {
            "PS1",
            "PS2",
            "PS3",
            "PS4",
            "PS5",
            "PSVITA",
            "SWITCH",
            "WII",
            "WIIU",
            "XBOX",
            "X360",
            "XONE",
            "XSX",
        }

    def get_game_format(self, meta: Meta) -> str:
        platform_name = meta.platform.upper().strip()
        platform_format = self._platform_game_format(platform_name)
        if platform_format:
            return platform_format
        mapped = self._container_game_format(str(meta.container or "").lower())
        return mapped or "Outros"

    @staticmethod
    def _platform_game_format(platform_name: str) -> str:
        if platform_name == "MOBILE":
            return "APK"
        if platform_name in {"PS1", "PS2", "PS3", "PS4", "SWITCH"}:
            return "ISO"
        if platform_name == "PC":
            return "EXE"
        return ""

    @staticmethod
    def _container_game_format(container: str) -> str:
        return {
            "exe": "EXE",
            "iso": "ISO",
            "rar": "RAR/ZIP",
            "zip": "RAR/ZIP",
            "7z": "RAR/ZIP",
            "bin": "BIN",
            "nrg": "NRG",
            "ndf": "NDF",
        }.get(container, "")

    async def get_languages(self, _meta: Meta) -> str | None:
        lang_code = self.main_tmdb_data.get("original_language")

        if not isinstance(lang_code, str) or not lang_code:
            return None

        try:
            return (
                langcodes.Language.make(lang_code)
                .display_name("pt")
                .capitalize()
            )

        except LanguageTagError:
            return lang_code

    async def get_audio(self, meta: Meta) -> str:
        await self._ensure_languages(meta)
        languages = self._language_strings(meta.audio_languages)
        portuguese = self._has_portuguese_audio(languages)
        if not portuguese:
            return "Legendado"
        return self._portuguese_audio_label(meta, languages)

    async def _ensure_languages(self, meta: Meta) -> None:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )

    @staticmethod
    def _language_strings(value: Any) -> set[str]:
        values = value if isinstance(value, list) else []
        return {str(item).lower() for item in values if isinstance(item, str)}

    @staticmethod
    def _has_portuguese_audio(languages: set[str]) -> bool:
        return bool(languages.intersection({"portuguese", "português", "pt"}))

    @staticmethod
    def _portuguese_audio_label(meta: Meta, languages: set[str]) -> str:
        original = str(meta.original_language).lower()
        if original in {"portuguese", "português", "pt"}:
            return "Nacional"
        return "Dual Audio" if len(languages) > 1 else "Dublado"

    async def get_subtitle(self, meta: Meta) -> tuple[str, list[str]]:
        await self._ensure_languages(meta)
        subtitle_ids = self._subtitle_ids(meta.subtitle_languages)
        label = "Sim" if "49" in subtitle_ids else "Nao"
        values = sorted(subtitle_ids) or ["44"]
        return label, values

    def _subtitle_ids(self, value: Any) -> set[str]:
        languages = value if isinstance(value, list) else []
        return {
            target
            for language in languages
            if isinstance(language, str)
            if (target := self.ultimate_lang_map.get(language.lower()))
        }

    async def get_resolution(self, meta: Meta) -> tuple[str, str]:
        width = ""
        height = ""
        if meta.is_disc == "BDMV":
            resolution_str = meta.resolution
            try:
                height_num = int(
                    resolution_str.lower().replace("p", "").replace("i", "")
                )
                height = str(height_num)

                width_num = round((16 / 9) * height_num)
                width = str(width_num)
            except ValueError, TypeError:
                pass

        else:
            video_mi = meta.mediainfo["media"]["track"][1]
            width = str(video_mi.get("Width", ""))
            height = str(video_mi.get("Height", ""))

        return width, height

    async def get_video_codec(self, meta: Meta) -> str:
        encoded = self._encoded_video_codec(meta)
        if encoded:
            return encoded
        fallback = self._fallback_video_codec(meta)
        return fallback or "Outro"

    @classmethod
    def _encoded_video_codec(cls, meta: Meta) -> str:
        value = str(meta.video_encode or "").strip().lower()
        mapped = cls._first_codec_match(value, cls._encode_codec_map())
        return cls._hdr_codec(mapped, meta) if mapped else ""

    @classmethod
    def _fallback_video_codec(cls, meta: Meta) -> str:
        value = str(meta.video_codec or "").lower()
        mapped = cls._first_codec_match(value, cls._fallback_codec_map())
        return (
            cls._hdr_codec(mapped, meta)
            if mapped
            else str(meta.video_codec or "")
        )

    @staticmethod
    def _first_codec_match(value: str, mapping: dict[str, str]) -> str:
        return next(
            (mapped for key, mapped in mapping.items() if key in value), ""
        )

    @staticmethod
    def _encode_codec_map() -> dict[str, str]:
        return {
            "x265": "x265",
            "h.265": "H.265",
            "x264": "x264",
            "h.264": "H.264",
            "vp9": "VP9",
            "xvid": "XviD",
        }

    @staticmethod
    def _fallback_codec_map() -> dict[str, str]:
        return {
            "hevc": "x265",
            "avc": "x264",
            "mpeg-2": "MPEG-2",
            "vc-1": "VC-1",
        }

    @staticmethod
    def _hdr_codec(codec: str, meta: Meta) -> str:
        return (
            f"{codec} HDR"
            if codec in {"x265", "H.265"} and bool(meta.hdr)
            else codec
        )

    async def get_audio_codec(self, meta: Meta) -> str:
        description = meta.audio
        if not isinstance(description, str) or not description:
            return "Outro"
        return self._matched_audio_codec(description)

    @classmethod
    def _matched_audio_codec(cls, description: str) -> str:
        for codec, terms in cls._audio_codec_terms():
            if any(term in description for term in terms):
                return codec
        return "Outro"

    @staticmethod
    def _audio_codec_terms() -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("DTS-X", ("DTS:X",)),
            ("E-AC-3 JOC", ("DD+ 5.1 Atmos", "DD+ 7.1 Atmos")),
            ("TrueHD", ("TrueHD",)),
            ("DTS-HD", ("DTS-HD",)),
            ("PCM", ("LPCM",)),
            ("FLAC", ("FLAC",)),
            ("DTS-ES", ("DTS-ES",)),
            ("DTS", ("DTS",)),
            ("E-AC-3", ("DD+",)),
            ("AC3", ("DD",)),
            ("AAC", ("AAC",)),
            ("Opus", ("Opus",)),
            ("Vorbis", ("VORBIS",)),
            ("MP3", ("MP3",)),
            ("MP2", ("MP2",)),
        )

    async def get_name(self, meta: Meta) -> str:
        """This is for the terminal display of the name only, not the actual upload name."""
        original_title, brazilian_title = self.get_titles(meta)
        if not brazilian_title:
            return original_title
        return f"{brazilian_title} [{original_title}]"

    def get_titles(self, meta: Meta) -> tuple[str, str]:
        if meta.category == "BOOK":
            return self.common.portuguese_title_capitalization(meta.title), ""
        if meta.category == "GAME":
            return str(meta.title), ""
        if meta.category not in {"TV", "MOVIE"}:
            return "", ""
        return str(meta.title), self._localized_brazilian_title(meta)

    @classmethod
    def _localized_brazilian_title(cls, meta: Meta) -> str:
        data = cls._localized_main_data(meta)
        return cls._validated_localized_title(meta, data)

    @staticmethod
    def _localized_main_data(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.tmdb_localized_data, dict):
            return {}
        localized = meta.tmdb_localized_data.get("pt-BR")
        if not isinstance(localized, dict):
            return {}
        main = localized.get("main") or {}
        return cast(dict[str, Any], main) if isinstance(main, dict) else {}

    @classmethod
    def _validated_localized_title(
        cls, meta: Meta, data: dict[str, Any]
    ) -> str:
        title = data.get("name") or data.get("title")
        if not title:
            return ""
        return (
            str(title)
            if cls._localized_title_differs(meta, data, title)
            else ""
        )

    @staticmethod
    def _localized_title_differs(
        meta: Meta, data: dict[str, Any], title: Any
    ) -> bool:
        original = data.get("original_name") or data.get("original_title")
        return title != meta.title and original != title

    async def get_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        # Set episode_tmdb_data on meta for general_description_generator to pick it up
        meta.episode_tmdb_data = self.episode_tmdb_data

        return await builder.general_description_generator(
            meta,
            audio_spectrogram=False,
            bluray=False,
            custom_signature=False,
            description=False,
            dynamic_hdr_plot=False,
            game=True,
            languages=False,
            logo=True,
            mediainfo=True,
            menu_screenshots=False,
            nfo=False,
            screenshots=False,
            signature=f"[align=right][url=https://github.com/wastaken7/Upload-Assistant][size=1]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/size][/url][/align]",
        )

    async def get_trailer(self, meta: Meta) -> str:
        youtube = self._tmdb_trailer_key()
        if youtube:
            return youtube
        return self._meta_trailer_key(meta.youtube)

    def _tmdb_trailer_key(self) -> str:
        entries = self._tmdb_video_entries(self.main_tmdb_data.get("videos"))
        return self._video_key(entries[-1]) if entries else ""

    @staticmethod
    def _tmdb_video_entries(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        results = value.get("results")
        if not isinstance(results, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in results
            if isinstance(item, dict)
        ]

    @staticmethod
    def _video_key(value: dict[str, Any]) -> str:
        key = value.get("key", "")
        return key if isinstance(key, str) else ""

    @staticmethod
    def _meta_trailer_key(value: Any) -> str:
        if not value:
            return ""
        return (
            str(value)
            .replace("https://www.youtube.com/watch?v=", "")
            .replace("/", "")
        )

    async def get_tags(self, meta: Meta) -> str:
        if meta.category == "BOOK":
            return ""
        matched = self._genre_tags_for_meta(meta)
        return (
            unidecode(", ".join(matched))
            if matched
            else await self._prompt_genre_tags(meta)
        )

    def _genre_tags_for_meta(self, meta: Meta) -> list[str]:
        matched = self._mapped_genre_tags(meta.genres or meta.keywords or [])
        if matched:
            return matched
        return (
            self._tmdb_genre_tags() if meta.category in {"TV", "MOVIE"} else []
        )

    @classmethod
    def _mapped_genre_tags(cls, values: Any) -> list[str]:
        genres = values if isinstance(values, list) else []
        tags: list[str] = []
        for genre in genres:
            mapped = cls._mapped_genre(str(genre).strip().lower())
            if mapped and mapped not in tags:
                tags.append(mapped)
        return tags

    @staticmethod
    def _mapped_genre(value: str) -> str:
        mapped = ENG_TO_PTBR_GENRE_MAP.get(value)
        if mapped:
            return mapped
        return value if value in ENG_TO_PTBR_GENRE_MAP.values() else ""

    def _tmdb_genre_tags(self) -> list[str]:
        genres = self.main_tmdb_data.get("genres", [])
        values = genres if isinstance(genres, list) else []
        names = [
            str(item.get("name", "")).lower()
            for item in values
            if isinstance(item, dict)
        ]
        return self._mapped_genre_tags(names)

    async def _prompt_genre_tags(self, meta: Meta) -> str:
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Gêneros não encontrados em modo unattended. Plando upload para {self.tracker}.[/yellow]"
            )
            meta.skipping = self.tracker
            return ""
        value = await prompt_in_thread(
            cli_ui.ask_string,
            f"Digite os gêneros (no formato do {self.tracker}): ",
        )
        return unidecode(str(value or "").strip())

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        search_string = self._search_string(meta)
        page = await self._search_page(meta, search_string)
        if page is None:
            return []
        group_links = self._group_links(page)
        dupes: list[dict[str, Any]] = []
        for group_link in group_links:
            dupes.extend(await self._group_dupes(meta, group_link))
        return dupes

    @staticmethod
    def _search_string(meta: Meta) -> str:
        if meta.category in {"BOOK", "GAME"}:
            return str(meta.title)
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return str(meta.title) if meta.anime else str(imdb.get("imdbID", ""))

    async def _search_page(
        self, meta: Meta, search_string: str
    ) -> BeautifulSoup | None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is None:
            return None
        self.session.cookies = cast(Any, cookie_jar)
        response = await self.session.get(
            f"{self.base_url}/torrents.php?searchstr={search_string}"
        )
        if self._search_login_failed(response):
            await self.cookie_validator.handle_validation_failure(
                meta, self.tracker, response.text
            )
            meta.skipping = self.tracker
            return None
        if not self._capture_secret_token(response.text):
            logger.info(
                f"{self.tracker}: [bold red]Failed to find auth token on page.[/bold red]"
            )
            meta.skipping = self.tracker
            return None
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def _search_login_failed(response: httpx.Response) -> bool:
        return "login.php" in str(response.url) or "login.php" in response.text

    @classmethod
    def _capture_secret_token(cls, html: str) -> bool:
        match = re.search(r"logout\.php\?auth=([a-f0-9]+)", html)
        if match is None:
            return False
        cls.secret_token = match.group(1)
        return True

    @classmethod
    def _group_links(cls, page: BeautifulSoup) -> list[str]:
        table = page.find("table", id="torrent_table")
        if table is None:
            return []
        links = {
            href
            for row in table.find_all("tr")
            if (href := cls._group_row_href(row))
        }
        return sorted(links)

    @staticmethod
    def _group_row_href(row: Any) -> str:
        link = row.find("a", href=re.compile(r"torrents\.php\?id=\d+"))
        href = link.get("href") if link else None
        if not isinstance(href, str) or "torrentid" in href:
            return ""
        return href

    async def _group_dupes(
        self, meta: Meta, group_link: str
    ) -> list[dict[str, Any]]:
        response = await self.session.get(f"{self.base_url}/{group_link}")
        response.raise_for_status()
        page = BeautifulSoup(response.text, "html.parser")
        dupes: list[dict[str, Any]] = []
        for row in page.find_all("tr", id=re.compile(r"^torrent\d+$")):
            entry = self._torrent_row_entry(meta, page, row)
            if entry is not None:
                dupes.append(entry)
        return dupes

    def _torrent_row_entry(
        self, meta: Meta, page: BeautifulSoup, row: Any
    ) -> dict[str, Any] | None:
        description = self._row_description(row)
        torrent_id = self._row_torrent_id(row)
        if not description or not torrent_id:
            return None
        file_div = page.find("div", id=f"files_{torrent_id}")
        if file_div is None:
            return None
        files = self._file_names(file_div)
        name = self._torrent_entry_name(meta, description, files, file_div)
        entry = self._base_dupe_entry(row, torrent_id, name, files)
        if meta.category == "BOOK":
            entry["type"] = self._book_dupe_type(name, files)
        return entry

    @staticmethod
    def _row_description(row: Any) -> str:
        link = row.find("a", onclick=re.compile(r"gtoggle"))
        if link is None:
            return ""
        return " ".join(link.get_text(strip=True).split())

    @staticmethod
    def _row_torrent_id(row: Any) -> str:
        value = row.get("id")
        return value.replace("torrent", "") if isinstance(value, str) else ""

    @classmethod
    def _file_names(cls, file_div: Any) -> list[str]:
        table = file_div.find("table", class_="filelist_table")
        if table is None:
            return []
        names: list[str] = []
        for row in table.find_all("tr"):
            name = cls._file_row_name(row)
            if name:
                names.append(name)
        return names

    @staticmethod
    def _file_row_name(row: Any) -> str:
        classes = row.get("class")
        class_list = (
            [classes] if isinstance(classes, str) else list(classes or [])
        )
        if "colhead_dark" in class_list:
            return ""
        cell = row.find("td")
        return cell.get_text(strip=True) if cell is not None else ""

    @classmethod
    def _torrent_entry_name(
        cls, meta: Meta, description: str, files: list[str], file_div: Any
    ) -> str:
        if cls._folder_name_required(meta, description):
            folder = cls._folder_name(file_div)
            if folder:
                return folder
        if not cls._folder_name_required(meta, description) and files:
            return files[0]
        return description

    @staticmethod
    def _folder_name_required(meta: Meta, description: str) -> bool:
        disc_markers = (
            "bd25",
            "bd50",
            "bd66",
            "bd100",
            "dvd5",
            "dvd9",
            "m2ts",
        )
        existing_disc = any(
            marker in description.lower() for marker in disc_markers
        )
        return bool(existing_disc or meta.tv_pack or meta.category == "GAME")

    @staticmethod
    def _folder_name(file_div: Any) -> str:
        path_div = file_div.find("div", class_="filelist_path")
        if path_div is None:
            return ""
        return path_div.get_text(strip=True).strip("/")

    def _base_dupe_entry(
        self, row: Any, torrent_id: str, name: str, files: list[str]
    ) -> dict[str, Any]:
        cells = row.find_all("td")
        size = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        return {
            "name": name,
            "size": size,
            "link": f"{self.base_url}/torrents.php?torrentid={torrent_id}",
            "download": f"{self.base_url}/torrents.php?action=download&id={torrent_id}",
            "id": torrent_id,
            "files": files,
        }

    @classmethod
    def _book_dupe_type(cls, name: str, files: list[str]) -> str:
        if cls._book_dupe_is_audio(name, files):
            return "audiobook"
        lowered = name.lower()
        return next(
            (
                fmt
                for fmt in ("epub", "pdf", "mobi", "azw3", "cbr", "cbz")
                if fmt in lowered
            ),
            "ebook",
        )

    @staticmethod
    def _book_dupe_is_audio(name: str, files: list[str]) -> bool:
        audio_extensions = (
            ".mp3",
            ".m4b",
            ".flac",
            ".m4a",
            ".wav",
            ".ogg",
            ".aac",
            ".ac3",
            ".wma",
            ".opus",
        )
        lowered = name.lower()
        if "audiobook" in lowered or "audio book" in lowered:
            return True
        return any(
            file_name.lower().endswith(audio_extensions) for file_name in files
        )

    async def get_media_info(self, meta: Meta) -> str:
        info_file_path = ""
        info_file_path = (
            f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/BD_SUMMARY_00.txt"
            if meta.is_disc == "BDMV"
            else f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/MEDIAINFO_CLEANPATH.txt"
        )

        if Path(info_file_path).exists():
            try:
                async with aiofiles.open(
                    info_file_path, encoding="utf-8"
                ) as f:
                    return await f.read()
            except Exception as e:
                logger.info(
                    f"{self.tracker}: [bold red]Erro ao ler o arquivo de info em {escape(str(info_file_path))}: {escape(str(e))}[/bold red]"
                )
                return ""
        else:
            logger.info(
                f"{self.tracker}: [bold red]Arquivo de info não encontrado: {escape(str(info_file_path))}[/bold red]"
            )
            return ""

    async def get_edition(self, meta: Meta) -> str:
        edition_str = meta.edition.lower()
        if not edition_str:
            return ""

        edition_map = {
            "director's cut": "Director's Cut",
            "theatrical": "Theatrical Cut",
            "extended": "Extended",
            "uncut": "Uncut",
            "unrated": "Unrated",
            "imax": "IMAX",
            "noir": "Noir",
            "remastered": "Remastered",
        }

        for keyword, label in edition_map.items():
            if keyword in edition_str:
                return label

        return ""

    async def get_bitrate(self, meta: Meta) -> str:
        if meta.type == "DISC":
            disc = self._disc_bitrate(meta)
            if disc:
                return disc
        source_type = meta.type
        if not isinstance(source_type, str) or not source_type:
            return "Outro"
        return self._source_bitrate_map().get(source_type.lower(), "Outro")

    @classmethod
    def _disc_bitrate(cls, meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return cls._bluray_disc_size(meta)
        if meta.is_disc == "DVD":
            return (
                meta.dvd_size if meta.dvd_size in {"DVD9", "DVD5"} else "DVD9"
            )
        return ""

    @classmethod
    def _bluray_disc_size(cls, meta: Meta) -> str:
        if meta.disctype in {"BD100", "BD66", "BD50", "BD25"}:
            return str(meta.disctype)
        size = cls._bdinfo_size(meta.bdinfo)
        if size > 66:
            return "BD100"
        if size > 50:
            return "BD66"
        if size > 25:
            return "BD50"
        return "BD25"

    @staticmethod
    def _bdinfo_size(value: Any) -> float:
        if not isinstance(value, dict):
            return 0
        try:
            return float(value.get("size", 0) or 0)
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _source_bitrate_map() -> dict[str, str]:
        return {
            "remux": "Remux",
            "webdl": "WEB-DL",
            "webrip": "WEBRip",
            "web": "WEB",
            "encode": "Blu-ray",
            "bdrip": "BDRip",
            "brrip": "BRRip",
            "hdtv": "HDTV",
            "sdtv": "SDTV",
            "dvdrip": "DVDRip",
            "hd-dvd": "HD-DVD",
            "tvrip": "TVRip",
        }

    async def get_screens(self, meta: Meta) -> list[str]:
        images = self._all_image_entries(meta)
        return [url for image in images if (url := self._raw_image_url(image))]

    @classmethod
    def _all_image_entries(cls, meta: Meta) -> list[dict[str, Any]]:
        images: list[dict[str, Any]] = []
        for value in (
            meta.menu_images,
            meta.image_list,
            meta.spectrograms_images,
            meta.dynamic_hdr_plot_images,
        ):
            images.extend(cls._image_entries(value))
        return images

    @staticmethod
    def _image_entries(value: Any) -> list[dict[str, Any]]:
        values = value if isinstance(value, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    @staticmethod
    def _raw_image_url(image: dict[str, Any]) -> str:
        value = image.get("raw_url")
        return value if isinstance(value, str) and value else ""

    async def get_credits(self, meta: Meta) -> str:
        names = self._director_names(meta)
        if not names:
            return "N/A"
        return ", ".join(list(dict.fromkeys(names))[:5])

    @classmethod
    def _director_names(cls, meta: Meta) -> list[str]:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        values = [imdb.get("directors"), meta.tmdb_directors]
        return [name for value in values for name in cls._string_list(value)]

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [str(item) for item in values if isinstance(item, str)]

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        await self.load_localized_data(meta)
        description = await self.get_description(meta)
        original_title, brazilian_title = self.get_titles(meta)
        data = await self._base_upload_data(meta, original_title, description)
        data.update(
            await self._category_upload_data(
                meta, description, original_title, brazilian_title
            )
        )
        self._apply_anonymous_flag(data, meta)
        self._apply_internal_flag(data, meta)
        return data

    async def _base_upload_data(
        self, meta: Meta, original_title: str, _description: str
    ) -> dict[str, Any]:
        return {
            "submit": "true",
            "auth": BrasilTracker.secret_token,
            "year": "" if meta.year is None else str(meta.year),
            "title": original_title,
            "type": await self.get_type(meta),
        }

    async def _category_upload_data(
        self,
        meta: Meta,
        description: str,
        original_title: str,
        brazilian_title: str,
    ) -> dict[str, Any]:
        if meta.category == "GAME":
            return await self._game_upload_data(meta, description)
        if meta.category == "BOOK":
            return await self._book_upload_data(
                meta, description, original_title
            )
        if meta.category in {"MOVIE", "TV"}:
            return await self._video_upload_data(
                meta, description, brazilian_title
            )
        return {}

    async def _game_upload_data(
        self, meta: Meta, description: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "idioma_ori": self.get_game_language(meta),
            "genero_jogo": self.get_game_genre(meta),
            "plataforma_jogo": self.get_game_platform_bt(meta),
            "sys_jogo": self.get_game_os(meta),
            "format": self.get_game_format(meta),
            "tags": await self.get_tags(meta),
            "image": self._game_cover_url(meta),
            "sinopse": self._game_overview(meta),
            "especificas": description,
            "screen[]": await self.get_screens(meta),
            "releasedate": meta.igdb_first_release_date,
            "vote": str(meta.igdb_rating_count),
            "rating": str(meta.igdb_rating),
        }
        self._apply_game_version(data, meta)
        self._apply_game_youtube(data, meta)
        return data

    @staticmethod
    def _game_overview(meta: Meta) -> str:
        localized = meta.localized_overviews
        if isinstance(localized, dict):
            brazilian = localized.get("brazilian")
            if brazilian:
                return str(brazilian)
        return str(meta.overview or "")

    @classmethod
    def _game_cover_url(cls, meta: Meta) -> str:
        artwork_path = cls._remote_url(meta.artwork_path)
        return (
            artwork_path if artwork_path else cls._remote_url(meta.artwork_url)
        )

    @staticmethod
    def _apply_game_version(data: dict[str, Any], meta: Meta) -> None:
        if meta.platform.upper().strip() != "PC" or not meta.tag:
            return
        data["versaoapp"] = str(meta.tag).lstrip("-")

    @staticmethod
    def _apply_game_youtube(data: dict[str, Any], meta: Meta) -> None:
        if meta.youtube:
            data["youtube"] = meta.youtube

    async def _book_upload_data(
        self, meta: Meta, description: str, original_title: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": original_title,
            "idioma_ori": await self.get_book_language(meta),
            "format": await self.get_container(meta),
            "image": await self.get_book_cover(meta),
        }
        data.update(await self._book_variant_data(meta, description))
        return data

    async def _book_variant_data(
        self, meta: Meta, description: str
    ) -> dict[str, Any]:
        if meta.audiobook:
            return self._audiobook_upload_data(meta, description)
        if meta.magazine or meta.comic:
            return await self._periodical_upload_data(meta, description)
        return await self._ebook_upload_data(meta)

    def _audiobook_upload_data(
        self, meta: Meta, description: str
    ) -> dict[str, Any]:
        return {
            "banda": meta.author,
            "bitrate": self.get_audiobook_bitrate(meta),
            "especificas": description,
        }

    async def _periodical_upload_data(
        self, meta: Meta, description: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "diretor": meta.publisher or meta.author,
            "edicao": self._edition_digits(meta),
            "paginas": self.get_book_pages(meta),
            "tags": await self.get_tags(meta),
            "desc": html_to_bbcode(meta.overview),
            "especificas": description,
            "screen[]": await self.get_screens(meta),
        }
        if meta.magazine:
            self._apply_magazine_fields(data, meta)
        return data

    @classmethod
    def _edition_digits(cls, meta: Meta) -> str:
        value = cls._first_non_empty(
            meta.manual_edition,
            meta.edition,
            meta.episode,
            meta.manual_episode,
        )
        return "".join(
            character for character in str(value) if character.isdigit()
        )

    @staticmethod
    def _first_non_empty(*values: Any) -> Any:
        return next((value for value in values if value), "")

    def _apply_magazine_fields(self, data: dict[str, Any], meta: Meta) -> None:
        data["adulto"] = (
            "1" if meta.adult_media or meta.tmdb_adult_media else "0"
        )
        month = self._magazine_month(meta)
        if month:
            data.update({"mensal": "on", "mes_resvista": month})

    @staticmethod
    def _magazine_month(meta: Meta) -> str:
        months = (
            ("Janeiro", "January"),
            ("Fevereiro", "February"),
            ("Março", "March"),
            ("Abril", "April"),
            ("Maio", "May"),
            ("Junho", "June"),
            ("Julho", "July"),
            ("Agosto", "August"),
            ("Setembro", "September"),
            ("Outubro", "October"),
            ("Novembro", "November"),
            ("Dezembro", "December"),
        )
        text = f"{meta.title} {meta.basename_no_ext}".lower()
        return next(
            (
                portuguese
                for portuguese, english in months
                if portuguese.lower() in text or english.lower() in text
            ),
            "",
        )

    async def _ebook_upload_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "diretor": meta.author,
            "tags": await self.get_tags(meta),
            "desc": html_to_bbcode(meta.overview),
            "screen[]": await self.get_screens(meta),
        }

    async def _video_upload_data(
        self, meta: Meta, description: str, brazilian_title: str
    ) -> dict[str, Any]:
        subtitle_label, subtitle_ids = await self.get_subtitle(meta)
        width, height = await self.get_resolution(meta)
        data = await self._video_core_data(
            meta, description, subtitle_label, subtitle_ids, width, height
        )
        self._apply_video_identity_fields(data, meta, brazilian_title)
        self._apply_tv_anime_fields(data, meta)
        await self._apply_video_specific_fields(data, meta)
        return data

    async def _video_core_data(
        self,
        meta: Meta,
        description: str,
        subtitle_label: str,
        subtitle_ids: list[str],
        width: str,
        height: str,
    ) -> dict[str, Any]:
        return {
            "audio_c": await self.get_audio_codec(meta),
            "audio": await self.get_audio(meta),
            "bitrate": await self.get_bitrate(meta),
            "desc": "",
            "diretor": await self.get_credits(meta),
            "duracao": f"{meta.runtime!s} min",
            "especificas": description,
            "format": await self.get_container(meta),
            "idioma_ori": await self.get_languages(meta)
            or meta.original_language,
            "image": self._video_poster_url(meta),
            "legenda": subtitle_label,
            "mediainfo": await self.get_media_info(meta),
            "resolucao_1": width,
            "resolucao_2": height,
            "screen[]": await self.get_screens(meta),
            "sinopse": self.main_tmdb_data.get(
                "overview", "Nenhuma sinopse disponível."
            ),
            "subtitles[]": subtitle_ids,
            "tags": await self.get_tags(meta),
            "video_c": await self.get_video_codec(meta),
            "youtube": await self.get_trailer(meta),
        }

    def _video_poster_url(self, meta: Meta) -> str:
        path = (
            self.main_tmdb_data.get("poster_path", "") or meta.tmdb_poster_path
        )
        return f"https://image.tmdb.org/t/p/w500{path}"

    @staticmethod
    def _apply_video_identity_fields(
        data: dict[str, Any], meta: Meta, brazilian_title: str
    ) -> None:
        if meta.anime:
            return
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        data.update(
            {
                "3d": "Sim" if meta.three_d else "Nao",
                "adulto": "0",
                "imdb_input": imdb.get("imdbID", ""),
                "nota_imdb": str(imdb.get("rating", "")),
                "title_br": brazilian_title,
            }
        )
        if meta.scene:
            data["scene"] = "on"

    @classmethod
    def _apply_tv_anime_fields(cls, data: dict[str, Any], meta: Meta) -> None:
        if not cls._is_tv_or_anime(meta):
            return
        data.update(cls._tv_anime_payload(meta))

    @staticmethod
    def _is_tv_or_anime(meta: Meta) -> bool:
        return meta.category == "TV" or bool(meta.anime)

    @staticmethod
    def _tv_anime_payload(meta: Meta) -> dict[str, Any]:
        pack = bool(meta.tv_pack)
        return {
            "episodio": meta.episode,
            "ntorrent": f"{meta.season}{meta.episode}",
            "temporada_e": "" if pack else meta.season,
            "temporada": meta.season if pack else "",
            "tipo": "completa" if pack else "ep_individual",
        }

    async def _apply_video_specific_fields(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        if meta.category == "MOVIE":
            data["versao"] = await self.get_edition(meta)
            return
        if meta.anime:
            self._apply_anime_fields(data, meta)

    @staticmethod
    def _apply_anime_fields(data: dict[str, Any], meta: Meta) -> None:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        data.update(
            {
                "fundo_torrent": meta.backdrop,
                "horas": "",
                "minutos": "",
                "rating": str(imdb.get("rating", "")),
                "releasedate": "" if meta.year is None else str(meta.year),
                "vote": "",
            }
        )

    def _apply_anonymous_flag(self, data: dict[str, Any], meta: Meta) -> None:
        tracker_config = self._tracker_config()
        anonymous = not (
            meta.anon == 0 and not tracker_config.get("anon", False)
        )
        if anonymous:
            data["anonymous"] = "1"

    def _apply_internal_flag(self, data: dict[str, Any], meta: Meta) -> None:
        if not meta.tag:
            return
        tracker_config = self._tracker_config()
        groups = tracker_config.get("internal_groups", [])
        if (
            tracker_config.get("internal", False) is True
            and isinstance(groups, list)
            and meta.tag[1:] in groups
        ):
            data["internal"] = 1

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def get_audiobook_bitrate(self, meta: Meta) -> str:
        container_lower = meta.container.lower()
        if container_lower in ("flac", "wav", "alac", "ape", "dsf", "dff"):
            return "Lossless"

        avg_bitrate = meta.audiobook_bitrate
        if avg_bitrate is None:
            return "Outro"

        options = [96, 128, 192, 256, 320]

        # Find option with the minimum absolute difference
        closest_option = min(options, key=lambda opt: abs(opt - avg_bitrate))
        distance = abs(closest_option - avg_bitrate)

        # If distance is greater than 32 (meaning beyond midpoints), return "Outro"
        if distance > 32:
            return "Outro"

        return str(closest_option)

    def build_book_desc(self, meta: Meta) -> str:
        """Build the BBCode table for BOOK-category uploads."""
        builder = DescriptionBuilder(self.tracker, self.config)
        return builder._build_book_desc_section(
            meta, header_size=3, table=False
        )

    async def get_book_cover(self, meta: Meta) -> str:
        hosted = self._hosted_cover(meta.hosted_artwork)
        if hosted:
            return hosted
        return self._remote_url(meta.artwork_url)

    @staticmethod
    def _hosted_cover(value: Any) -> str:
        if (
            not isinstance(value, list)
            or not value
            or not isinstance(value[0], dict)
        ):
            return ""
        raw_url = value[0].get("raw_url")
        return str(raw_url) if raw_url else ""

    @staticmethod
    def _remote_url(value: Any) -> str:
        if isinstance(value, str) and value.startswith(
            ("http://", "https://")
        ):
            return value
        return ""

    async def get_book_language(self, meta: Meta) -> str:
        book_lang_code = meta.book_language_iso
        book_lang_code = (
            book_lang_code.lower() if isinstance(book_lang_code, str) else ""
        )

        lang_map = {
            "pt": "Português",
            "por": "Português",
            "en": "Inglês",
            "eng": "Inglês",
            "it": "Italiano",
            "ita": "Italiano",
            "de": "Alemão",
            "deu": "Alemão",
            "ger": "Alemão",
            "es": "Espanhol",
            "spa": "Espanhol",
            "ja": "Japonês",
            "jpn": "Japonês",
        }
        resolved_lang = lang_map.get(book_lang_code, "Outro")
        if meta.audiobook and resolved_lang == "Japonês":
            resolved_lang = "Outro"
        return resolved_lang

    def get_book_pages(self, meta: Meta) -> str:
        if meta.audiobook:
            return ""
        path = self._book_file_path(meta)
        if path is None:
            return ""
        return self._book_page_count(path)

    @staticmethod
    def _book_file_path(meta: Meta) -> Path | None:
        filelist = meta.filelist if isinstance(meta.filelist, list) else []
        value = filelist[0] if filelist else meta.path
        if not value:
            return None
        path = Path(str(value))
        return path if path.exists() else None

    @classmethod
    def _book_page_count(cls, path: Path) -> str:
        try:
            if path.suffix.lower() == ".pdf":
                return cls._pdf_pages(path)
            if path.suffix.lower() == ".cbz":
                return cls._cbz_pages(path)
            if path.suffix.lower() == ".cbr":
                return cls._cbr_pages(path)
        except Exception:
            return ""
        return ""

    @staticmethod
    def _pdf_pages(path: Path) -> str:
        document = fitz.open(path)
        return str(len(document))

    @staticmethod
    def _cbz_pages(path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            return str(
                sum(
                    1
                    for name in archive.namelist()
                    if BrasilTracker._image_archive_name(name)
                )
            )

    @staticmethod
    def _cbr_pages(path: Path) -> str:
        with rarfile.RarFile(path) as archive:
            names = cast(list[str], archive.namelist())
            return str(
                sum(
                    1
                    for name in names
                    if BrasilTracker._image_archive_name(name)
                )
            )

    @staticmethod
    def _image_archive_name(name: str) -> bool:
        return name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))

    async def upload(self, meta: Meta) -> bool:
        if getattr(meta, "skipping", None) == self.tracker:
            return False
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar is None:
            return False
        self.session.cookies = cast(Any, cookie_jar)
        data = await self.get_data(meta)
        if getattr(meta, "skipping", None) == self.tracker:
            return False

        return await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            data=data,
            torrent_field_name="file_input",
            upload_cookies=self.session.cookies,
            upload_url=f"{self.base_url}/upload.php",
            id_pattern=r"groupid=(\d+)",
            success_status_code="200, 302, 303",
        )
