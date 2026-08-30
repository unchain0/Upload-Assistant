# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import platform
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import httpx
import langcodes
import pycountry
from bs4 import BeautifulSoup, Tag
from langcodes.tag_parser import LanguageTagError
from unidecode import unidecode

from src.domain_models.genre_mapping import ENG_TO_PTBR_GENRE_MAP
from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.temp_paths import screenshots_dir
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

Config = dict[str, Any]

_VIDEO_CONTAINERS = frozenset({"mkv", "mp4", "avi", "vob", "m2ts", "ts"})
_AUDIOBOOK_CONTAINERS = frozenset(
    {
        "aac",
        "ac3",
        "dff",
        "dsf",
        "flac",
        "m4a",
        "m4b",
        "mp3",
        "ogg",
        "wav",
        "wma",
    }
)
_BOOK_TYPE_RULES: tuple[tuple[str, int], ...] = (
    ("audiobook", 10),
    ("manga", 4),
    ("comic", 11),
    ("newspaper", 23),
    ("magazine", 8),
)
_GAME_LANGUAGE_MAP: tuple[tuple[str, str], ...] = (
    ("german", "Alemão"),
    ("spanish", "Espanhol"),
    ("french", "Francês"),
    ("english", "Inglês"),
    ("japanese", "Japonês"),
    ("portuguese", "Português"),
    ("russian", "Russo"),
)
_VIDEO_CODEC_MAP: tuple[tuple[str, str], ...] = (
    ("x265", "x265"),
    ("h.265", "H.265"),
    ("x264", "x264"),
    ("h.264", "H.264"),
    ("av1", "AV1"),
    ("divx", "DivX"),
    ("h.263", "H.263"),
    ("kvcd", "KVCD"),
    ("mpeg-1", "MPEG-1"),
    ("mpeg-2", "MPEG-2"),
    ("realvideo", "RealVideo"),
    ("vc-1", "VC-1"),
    ("vp6", "VP6"),
    ("vp8", "VP8"),
    ("vp9", "VP9"),
    ("windows media video", "Windows Media Video"),
    ("xvid", "XviD"),
    ("hevc", "H.265"),
    ("avc", "H.264"),
)
_AUDIO_CODEC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DTS-X", ("DTS:X", "DTS-X")),
    ("E-AC-3 JOC", ("E-AC-3 JOC", "DD+ JOC")),
    ("TrueHD", ("TRUEHD",)),
    ("DTS-HD", ("DTS-HD", "DTSHD")),
    ("LPCM", ("LPCM",)),
    ("PCM", ("PCM",)),
    ("FLAC", ("FLAC",)),
    ("DTS-ES", ("DTS-ES",)),
    ("DTS", ("DTS",)),
    ("E-AC-3", ("E-AC-3", "DD+")),
    ("AC3", ("AC3", "DD")),
    ("AAC", ("AAC",)),
    ("Opus", ("OPUS",)),
    ("Vorbis", ("VORBIS",)),
    ("MP3", ("MP3",)),
    ("MP2", ("MP2",)),
)
_VALID_BR_RATINGS = frozenset({"L", "10", "12", "14", "16", "18"})
_PORTUGUESE_LANGUAGE_NAMES = frozenset({"portuguese", "português", "pt"})
_POSSIBLE_LANGUAGES = frozenset(
    {
        "Alemão",
        "Árabe",
        "Argelino",
        "Búlgaro",
        "Cantonês",
        "Chinês",
        "Coreano",
        "Croata",
        "Dinamarquês",
        "Egípcio",
        "Espanhol",
        "Estoniano",
        "Filipino",
        "Finlandês",
        "Francês",
        "Grego",
        "Hebraico",
        "Hindi",
        "Holandês",
        "Húngaro",
        "Indonésio",
        "Inglês",
        "Islandês",
        "Italiano",
        "Japonês",
        "Macedônio",
        "Malaio",
        "Marati",
        "Nigeriano",
        "Norueguês",
        "Persa",
        "Polaco",
        "Polonês",
        "Português",
        "Português (pt)",
        "Romeno",
        "Russo",
        "Sueco",
        "Tailandês",
        "Tamil",
        "Tcheco",
        "Telugo",
        "Turco",
        "Ucraniano",
        "Urdu",
        "Vietnamita",
        "Zulu",
        "Outro",
    }
)


def _book_container(meta: Meta, container: str) -> str:
    if meta.audiobook:
        return (
            container.upper()
            if container in _AUDIOBOOK_CONTAINERS
            else "Outro"
        )
    return {"pdf": "PDF", "epub": "ePub"}.get(container, "")


def _book_type(meta: Meta) -> int:
    for attribute, type_id in _BOOK_TYPE_RULES:
        if bool(getattr(meta, attribute)):
            return type_id
    return 9


def _language_display_name(lang_code: str, origin_countries: list[str]) -> str:
    if lang_code == "pt":
        return "Português (pt)" if "PT" in origin_countries else "Português"
    try:
        return (
            langcodes.Language.make(lang_code).display_name("pt").capitalize()
        )
    except LanguageTagError:
        return lang_code


def _game_language_names(languages: object) -> list[str]:
    if not isinstance(languages, dict):
        return []
    return [str(name).lower() for name in languages]


def _mapped_game_language(names: list[str]) -> str:
    for name in names:
        for key, value in _GAME_LANGUAGE_MAP:
            if key in name:
                return value
    return "Outro"


def _has_portuguese(names: list[str]) -> bool:
    return any(
        language in name
        for name in names
        for language in _PORTUGUESE_LANGUAGE_NAMES
    )


def _single_platform_system(platform: str) -> str:
    normalized = platform.lower()
    aliases = (
        ("pc", "Windows"),
        ("windows", "Windows"),
        ("mac", "Mac"),
        ("linux", "Linux"),
    )
    for token, label in aliases:
        if token in normalized:
            return label
    return ""


def _audio_label(audio_languages: set[str], original_language: str) -> str:
    normalized = {language.lower() for language in audio_languages}
    has_pt_audio = bool(normalized & _PORTUGUESE_LANGUAGE_NAMES)
    if not has_pt_audio:
        return "Legendado"
    if original_language.lower() in _PORTUGUESE_LANGUAGE_NAMES:
        return "Nacional"
    return "Dual Áudio" if len(audio_languages) > 1 else "Dublado"


def _matching_codec(
    text: str, aliases: tuple[tuple[str, str], ...]
) -> str | None:
    for token, label in aliases:
        if token in text:
            return label
    return None


def _matching_audio_codec(text: str) -> str:
    upper = text.upper()
    for codec, aliases in _AUDIO_CODEC_ALIASES:
        if any(alias in upper for alias in aliases):
            return codec
    return "Outro"


def _br_rating(item: dict[str, Any]) -> str | None:
    rating = item.get("rating")
    if item.get("iso_3166_1") != "BR" or rating not in _VALID_BR_RATINGS:
        return None
    value = str(rating)
    return "Livre" if value == "L" else f"{value} anos"


def _us_rating(item: dict[str, Any]) -> str:
    return (
        str(item.get("rating", "")) if item.get("iso_3166_1") == "US" else ""
    )


def _mapped_genre(genre: str) -> str | None:
    normalized = genre.strip().lower()
    mapped = ENG_TO_PTBR_GENRE_MAP.get(normalized)
    if mapped is not None:
        return mapped
    return normalized if normalized in ENG_TO_PTBR_GENRE_MAP.values() else None


def _mapped_genres(genres: list[str]) -> list[str]:
    matched: list[str] = []
    for genre in genres:
        mapped = _mapped_genre(genre)
        if mapped is not None and mapped not in matched:
            matched.append(mapped)
    return matched


def _tmdb_genres(data: dict[str, Any]) -> list[str]:
    genres = data.get("genres", [])
    names = [
        str(item.get("name", "")) for item in genres if isinstance(item, dict)
    ]
    return _mapped_genres(names)


def _localized_main(meta: Meta) -> dict[str, Any]:
    value = meta.tmdb_localized_data.get("pt-BR", {}).get("main")
    return dict(value or {})


def _localized_brazilian_title(meta: Meta, localized: dict[str, Any]) -> str:
    tmdb_title = localized.get("name") or localized.get("title")
    if not tmdb_title:
        return ""
    originals = {
        meta.title,
        meta.imdb_info.get("title"),
        localized.get("original_name"),
        localized.get("original_title"),
    }
    return "" if tmdb_title in originals else str(tmdb_title)


def _video_titles(meta: Meta, database_title: str) -> tuple[str, str]:
    localized = _localized_main(meta)
    original = database_title or str(meta.imdb_info.get("title") or meta.title)
    return original, _localized_brazilian_title(meta, localized)


def _rating_from_items(ratings: list[dict[str, Any]]) -> str:
    us_fallback = ""
    for item in ratings:
        brazil = _br_rating(item)
        if brazil is not None:
            return brazil
        us_fallback = us_fallback or _us_rating(item)
    return us_fallback


def _tags_from_metadata(meta: Meta, tmdb_data: dict[str, Any]) -> list[str]:
    matched = _mapped_genres(list(meta.genres or meta.keywords or []))
    if matched:
        return matched
    if meta.category not in ("TV", "MOVIE"):
        return []
    return _tmdb_genres(tmdb_data)


async def _prompt_bjshare_tags(meta: Meta, tracker: str) -> str:
    if meta.unattended and not meta.unattended_confirm:
        logger.info(
            f"{tracker}: [yellow]Unattended mode: Gêneros não encontrados. "
            f"Pulando upload para {tracker}.[/yellow]"
        )
        meta.skipping = tracker
        return ""
    value = await prompt_in_thread(
        cli_ui.ask_string,
        f"Digite os gêneros (no formato do {tracker}): ",
    )
    return unidecode((value or "").strip())


def _information_box(soup: BeautifulSoup) -> Tag | None:
    for box in soup.find_all("div", class_="box"):
        if not isinstance(box, Tag):
            continue
        header = box.find("div", class_="head")
        if isinstance(header, Tag) and "Informações" in header.get_text():
            return box
    return None


def _row_cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all("td") if isinstance(cell, Tag)]


def _is_database_title_label(label: str) -> bool:
    return "Título Original:" in label or "Título:" in label


def _database_title_from_row(row: object) -> str | None:
    if not isinstance(row, Tag):
        return None
    cells = _row_cells(row)
    if len(cells) < 2:
        return None
    if not _is_database_title_label(cells[0].get_text(strip=True)):
        return None
    return cells[1].get_text(strip=True)


def _database_title_from_box(box: Tag | None) -> str:
    if box is None:
        return ""
    for row in box.find_all("tr"):
        title = _database_title_from_row(row)
        if title is not None:
            return title
    return ""


def _identifier_from_href(label: str, href: str) -> str | None:
    if "imdb" in label:
        match = re.search(r"tt\d+", href, re.IGNORECASE)
        return match.group(0).lower() if match else None
    if "tmdb" not in label:
        return None
    match = re.search(r"themoviedb\.org/(movie|tv)/(\d+)", href, re.IGNORECASE)
    return f"{match.group(1).lower()}/{match.group(2)}" if match else None


def _database_identifier_from_row(row: object) -> str | None:
    if not isinstance(row, Tag):
        return None
    cells = _row_cells(row)
    if len(cells) < 2:
        return None
    link = cells[1].find("a", href=True)
    href = str(link.get("href", "")) if isinstance(link, Tag) else ""
    return _identifier_from_href(
        cells[0].get_text(" ", strip=True).lower(), href
    )


def _database_identifier_from_box(box: Tag | None) -> str:
    if box is None:
        return ""
    for row in box.find_all("tr"):
        identifier = _database_identifier_from_row(row)
        if identifier is not None:
            return identifier
    return ""


def _tag_class_names(tag: Tag) -> set[str]:
    classes = tag.get("class")
    if not isinstance(classes, list):
        return set()
    return {str(value) for value in classes}


def _blockquote_is_decorative(blockquote: Tag) -> bool:
    has_iframe = blockquote.find("iframe") is not None
    return has_iframe or "center" in _tag_class_names(blockquote)


def _blockquote_overview(blockquote: Tag) -> str | None:
    if _blockquote_is_decorative(blockquote):
        return None
    return blockquote.get_text(strip=True) or None


def _description_body_text(desc_box: Tag) -> str:
    body = desc_box.find("div", class_="body")
    target = body if isinstance(body, Tag) else desc_box
    for tag in target.find_all(["iframe", "script", "style"]):
        tag.decompose()
    return target.get_text(strip=True)


class BJShare:
    """
    BJ-Share is a BRAZILIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    auth_type = "cookies"
    tracker = "BJSHARE"
    display_name = "BJShare"
    banned_groups: tuple[str, ...] = ()
    source_flag = "BJ"
    base_url = "https://bj-share.info"
    auth_token = None
    torrent_url = f"{base_url}/torrents.php?torrentid="
    torrent_download_url = f"{base_url}/torrents.php?action=download&id="
    requests_url = f"{base_url}/requests.php?"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("tracker.bj-share.info",)
    allows_bloated_audio = True
    secret_token: str = ""
    already_has_the_info: bool = False
    database_title: str = ""
    database_identifier: str = ""
    database_overview: str = ""
    tmdb_localization_requirements: ClassVar = {
        "pt-BR": {
            "main": "credits,videos,content_ratings",
            "episode": "",
        }
    }
    file_extensions: ClassVar = {
        "mkv",
        "mp4",
        "avi",
        "ts",
        "m2ts",
        "wmv",
        "mov",
        "flv",
        "webm",
        "mpg",
        "mpeg",
        "vob",
        "divx",
        "xvid",
        "mp3",
        "m4b",
        "flac",
        "aac",
        "m4a",
        "ogg",
        "wav",
        "opus",
        "wma",
        "ape",
        "cue",
        "m3u",
        "epub",
        "pdf",
        "mobi",
        "azw3",
        "kfx",
        "cbz",
        "cbr",
        "cbt",
        "fb2",
        "ibooks",
        "djvu",
        "txt",
        "html",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "bz2",
        "iso",
        "dmg",
        "pkg",
        "exe",
        "bin",
        "msi",
        "apk",
        "srt",
        "ass",
        "vtt",
        "sub",
        "idx",
    }

    def has_extension(self, name: str) -> bool:
        ext = Path(name).suffix
        return ext.lower().lstrip(".") in self.file_extensions

    def __init__(self, config: Config):
        self.config = config
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
        self.semaphore = asyncio.Semaphore(1)

    def _book_upload_allowed(self, meta: Meta) -> bool:
        if meta.book_language_iso == "por":
            return True
        logger.info(
            f"{self.tracker}: [red]Only books in Portuguese are allowed.[/red]"
        )
        return False

    async def _game_install_notes_allowed(self, meta: Meta) -> bool:
        if meta.platform.upper().strip() not in {"PC", "MAC", "LINUX"}:
            return True
        builder = DescriptionBuilder(self.tracker, self.config)
        if await builder.get_user_description(meta):
            return True
        logger.info(
            f"{self.tracker}: [red]Installation notes are required for PC game "
            "uploads. Please provide them using [bold]-df[/bold] "
            "(path/to/file.txt) or [bold]-pb[/bold] (link to raw text).[/red]"
        )
        return False

    @staticmethod
    def _scene_game_is_archived(meta: Meta) -> bool:
        archives = {"rar", "zip", "7z", "tar", "gz"}
        return bool(meta.scene and meta.container in archives)

    async def _game_upload_allowed(self, meta: Meta) -> bool:
        if not await self._game_install_notes_allowed(meta):
            return False
        if not self._scene_game_is_archived(meta):
            return True
        logger.info(
            f"{self.tracker}: [red]Skipping upload: Scene games must be "
            "unpacked (Rule 5.4.1.1).[/red]"
        )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.category == "BOOK":
            return self._book_upload_allowed(meta)
        if meta.category == "GAME":
            return await self._game_upload_allowed(meta)
        if meta.subtitle_files:
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["portuguese", "português"],
            check_audio=True,
            check_subtitle=True,
        )

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar
            return True

        return False

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

    def get_container(self, meta: Meta) -> str:
        container = str(meta.container)
        if meta.category in ("MOVIE", "TV"):
            return (
                container.upper()
                if container in _VIDEO_CONTAINERS
                else "Outro"
            )
        if meta.category == "BOOK":
            return _book_container(meta, container)
        return ""

    def get_type(self, meta: Meta) -> int:
        if meta.anime:
            return 13
        if meta.category == "BOOK":
            return _book_type(meta)
        return {"TV": 1, "MOVIE": 0, "GAME": 3}.get(meta.category, 0)

    def get_languages(self) -> str:
        lang_code = str(self.main_tmdb_data.get("original_language") or "")
        if not lang_code:
            return "Outro"
        countries = self.main_tmdb_data.get("origin_country", [])
        origin_countries = [str(country) for country in countries]
        language_name = _language_display_name(lang_code, origin_countries)
        return (
            language_name if language_name in _POSSIBLE_LANGUAGES else "Outro"
        )

    def get_game_platform(self, meta: Meta) -> str:
        """Map meta.platform to BJSHARE platform ID for the Jogos category."""
        platform_map: dict[str, str] = {
            "3DS": "13",
            "MOBILE": "2",
            "DS": "12",
            "NDS": "12",
            "EMULATOR": "1",
            "PC": "3",
            "MAC": "3",
            "LINUX": "3",
            "PSVITA": "15",
            "PS1": "4",
            "PS2": "5",
            "PS3": "6",
            "PS4": "7",
            "PS5": "18",
            "PSP": "14",
            "SWITCH": "16",
            "WII": "8",
            "WIIU": "9",
            "XBOX": "17",
            "XONE": "17",
            "X360": "10",
            "XSX": "17",
        }

        platform = meta.platform.upper().strip()
        return platform_map.get(platform, "3")  # Default to PC

    def get_game_language(self, meta: Meta) -> str:
        """Map game languages from IGDB/Steam to BJSHARE idioma field."""
        names = _game_language_names(meta.languages)
        if not names:
            return "Outro"
        if len(names) > 1 and _has_portuguese(names):
            return "Multilinguagem"
        return _mapped_game_language(names)

    def get_game_subcategory(self, meta: Meta) -> str:
        """Get the game subcategory for BJSHARE."""
        subcategory = meta.game_subcategory
        subcategory_values = {
            "full_game": "1",
            "full_game_dlc": "2",
            "dlc": "3",
            "update": "4",
        }
        return subcategory_values.get(subcategory, "1")

    def get_sistema(self, meta: Meta) -> str:
        platforms = meta.available_platforms
        if len(platforms) > 1:
            return "Multiplataforma"
        if not platforms:
            return ""
        return _single_platform_system(str(platforms[0]))

    async def get_audio(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        audio_languages = set(meta.audio_languages or [])
        return _audio_label(audio_languages, str(meta.original_language))

    async def get_subtitle(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        found_language_strings = meta.subtitle_languages

        subtitle_type = "Nenhuma"

        if (
            found_language_strings is not None
            and "Portuguese" in found_language_strings
        ):
            subtitle_type = "Embutida"

        return subtitle_type

    def get_resolution(self, meta: Meta) -> tuple[str, str]:
        width, height = "0", "0"

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
            width = video_mi["Width"]
            height = video_mi["Height"]

        return width, height

    def get_video_codec(self, meta: Meta) -> str:
        video_codec = str(meta.video_codec or "")
        search_text = f"{str(meta.video_encode).lower()} {video_codec.lower()}"
        return (
            _matching_codec(search_text, _VIDEO_CODEC_MAP)
            or video_codec
            or "Outro"
        )

    def get_audio_codec(self, meta: Meta) -> str:
        audio_description = meta.audio
        if not isinstance(audio_description, str) or not audio_description:
            return "Outro"
        return _matching_audio_codec(audio_description)

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
            return meta.title, ""
        if meta.category in ("TV", "MOVIE"):
            return _video_titles(meta, BJShare.database_title)
        return "", ""

    async def build_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        meta.episode_tmdb_data = self.episode_tmdb_data

        return await builder.general_description_generator(
            meta,
            menu_screenshots=False,
            nfo=False,
            screenshots=False,
            signature=f"[align=right][url=https://github.com/wastaken7/Upload-Assistant][size=1]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/size][/url][/align]",
        )

    def get_trailer(self, meta: Meta) -> str:
        video_results: list[dict[str, Any]] = dict(
            self.main_tmdb_data.get("videos", {})
        ).get("results", [])
        youtube_code = (
            video_results[-1].get("key", "") if video_results else ""
        )
        return (
            f"http://www.youtube.com/watch?v={youtube_code}"
            if youtube_code
            else meta.youtube or ""
        )

    def get_rating(self) -> str:
        ratings = dict(self.main_tmdb_data.get("content_ratings", {})).get(
            "results", []
        )
        items = [item for item in ratings if isinstance(item, dict)]
        return _rating_from_items(items)

    async def get_tags(self, meta: Meta) -> str:
        """Map genres from metadata to BJShare tags."""
        matched = _tags_from_metadata(meta, self.main_tmdb_data)
        if matched:
            return unidecode(", ".join(matched))
        return await _prompt_bjshare_tags(meta, self.tracker)

    def get_database_title(self, soup: BeautifulSoup) -> str:
        """Extract the canonical title used by the BJShare database."""
        return _database_title_from_box(_information_box(soup))

    def get_database_identifier(self, soup: BeautifulSoup) -> str:
        """Return the IMDb or TMDb identifier used by an existing group."""
        return _database_identifier_from_box(_information_box(soup))

    def get_database_overview(self, soup: BeautifulSoup) -> str:
        """Extract the existing overview/synopsis from a BJShare group."""
        desc_box = soup.find("div", class_="torrent_description")
        if not isinstance(desc_box, Tag):
            return ""
        for blockquote in desc_box.find_all("blockquote"):
            if not isinstance(blockquote, Tag):
                continue
            overview = _blockquote_overview(blockquote)
            if overview is not None:
                return overview
        return _description_body_text(desc_box)

    def _search_title(self, meta: Meta) -> str:
        if meta.category == "BOOK" and meta.title:
            return self.common.portuguese_title_capitalization(meta.title)
        return meta.title

    def _base_search_params(self, meta: Meta, title: str) -> dict[str, str]:
        if meta.category == "BOOK":
            filter_cat = "11" if meta.audiobook else "10"
            return {
                "searchstr": title,
                f"filter_cat[{filter_cat}]": "1",
                "action": "basic",
                "searchsubmit": "1",
            }
        if meta.category == "GAME":
            return {
                "searchstr": title,
                "filter_cat[4]": "1",
                "plataforma": self.get_game_platform(meta),
                "action": "basic",
                "searchsubmit": "1",
            }
        return {"searchstr": title}

    @staticmethod
    def _canonical_imdb_search_term(value: object) -> str:
        match = re.search(r"tt\d+", str(value or ""), re.IGNORECASE)
        return match.group(0).lower() if match else ""

    @classmethod
    def _media_search_terms(cls, meta: Meta) -> list[str]:
        if meta.category not in ("TV", "MOVIE"):
            return []
        terms: list[str] = []
        imdb_id = cls._canonical_imdb_search_term(
            dict(meta.imdb_info).get("imdbID", "")
        )
        if imdb_id:
            terms.append(imdb_id)
        tmdb_id = str(meta.tmdb_id or "").strip()
        if tmdb_id:
            terms.append(f"{meta.category.lower()}/{tmdb_id}")
        return list(dict.fromkeys(terms))

    @staticmethod
    def _media_search_params(term: str) -> dict[str, str]:
        return {
            "search": term,
            "active": "1",
            "search_type": "1",
        }

    def _search_queries(
        self, meta: Meta, title: str
    ) -> tuple[list[dict[str, str]], list[str], bool]:
        base = self._base_search_params(meta, title)
        terms = self._media_search_terms(meta)
        if meta.category not in ("TV", "MOVIE"):
            return [base], terms, False
        if terms:
            return [self._media_search_params(term) for term in terms], terms, False
        return [{"searchstr": title}], terms, True

    async def _load_search_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar

    @staticmethod
    def _reset_database_match_state() -> None:
        BJShare.already_has_the_info = False
        BJShare.database_title = ""
        BJShare.database_identifier = ""
        BJShare.database_overview = ""

    @staticmethod
    def _response_has_details(response: httpx.Response) -> bool:
        soup = BeautifulSoup(response.text, "html.parser")
        return soup.find("div", class_="main_column") is not None

    async def _request_search_page(
        self,
        meta: Meta,
        search_url: str,
        params: dict[str, str],
    ) -> httpx.Response | None:
        response = await self.session.get(
            search_url, params=params, follow_redirects=True
        )
        response.raise_for_status()
        if "login.php" in str(response.url) or "login.php" in response.text:
            await self.cookie_validator.handle_validation_failure(
                meta, self.tracker, response.text
            )
            meta.skipping = self.tracker
            return None
        auth_match = re.search(r"logout\.php\?auth=([a-f0-9]+)", response.text)
        if auth_match is None:
            logger.info(
                f"{self.tracker}: [bold red]Failed to find auth token on page.[/bold red]"
            )
            meta.skipping = self.tracker
            return None
        BJShare.secret_token = auth_match.group(1)
        return response

    async def _run_search_queries(
        self,
        meta: Meta,
        search_url: str,
        queries: list[dict[str, str]],
    ) -> tuple[httpx.Response | None, httpx.Response | None]:
        details: httpx.Response | None = None
        fallback: httpx.Response | None = None
        for params in queries:
            candidate = await self._request_search_page(
                meta, search_url, params
            )
            if candidate is None:
                return None, None
            fallback = candidate
            if details is None and self._response_has_details(candidate):
                details = candidate
        return details, fallback

    @staticmethod
    def _needs_title_fallback(
        meta: Meta,
        details: httpx.Response | None,
        title: str,
        title_already_queried: bool,
        media_terms: list[str],
    ) -> bool:
        return bool(
            meta.category in ("TV", "MOVIE")
            and details is None
            and title
            and not title_already_queried
            and title not in media_terms
        )

    async def _title_fallback_response(
        self,
        meta: Meta,
        search_url: str,
        title: str,
    ) -> httpx.Response | None:
        return await self._request_search_page(
            meta, search_url, {"searchstr": title}
        )

    @staticmethod
    def _details_row_id(row: Tag) -> str | None:
        row_id = row.get("id")
        if not isinstance(row_id, str):
            return None
        if not row_id.startswith("torrent") or row_id.startswith("torrent_"):
            return None
        torrent_id = row_id.removeprefix("torrent")
        return torrent_id or None

    @staticmethod
    def _recognized_book_format(value: object) -> str | None:
        formats = ("epub", "pdf", "mobi", "azw3", "cbr", "cbz")
        normalized = str(value or "").lower().strip()
        return normalized if normalized in formats else None

    @staticmethod
    def _book_format_from_name(name: str) -> str:
        formats = ("epub", "pdf", "mobi", "azw3", "cbr", "cbz")
        lowered = name.lower()
        for format_name in formats:
            if format_name in lowered:
                return format_name
        return "ebook"

    def _book_row_type(
        self, meta: Meta, format_value: object, name: str
    ) -> str:
        if meta.audiobook:
            return "audiobook"
        configured = self._recognized_book_format(format_value)
        return configured or self._book_format_from_name(name)

    @staticmethod
    def _details_row_size(row: Tag) -> str:
        size_tag = row.find("td", class_="number_column nobr")
        return (
            size_tag.get_text(strip=True) if isinstance(size_tag, Tag) else ""
        )

    def _dupe_entries(
        self,
        meta: Meta,
        names: list[str],
        torrent_id: str,
        size: str,
        row_type: str,
    ) -> list[dict[str, str | list[str]]]:
        entries: list[dict[str, str | list[str]]] = []
        for name in names:
            entry: dict[str, str | list[str]] = {
                "name": name,
                "size": size,
                "link": f"{self.torrent_url}{torrent_id}",
                "download": f"{self.torrent_download_url}{torrent_id}",
                "id": torrent_id,
            }
            if self.has_extension(name):
                entry["files"] = [name]
            if meta.category == "BOOK":
                entry["type"] = row_type
            entries.append(entry)
        return entries

    @staticmethod
    def _details_row_name(row: Tag) -> str:
        raw_name = row.get("data-torrentname", "")
        return str(raw_name).strip() if raw_name else ""

    @staticmethod
    def _details_row_names(meta: Meta, name: str) -> list[str]:
        names = [name]
        include_database_title = meta.category in ("BOOK", "GAME")
        if include_database_title and BJShare.database_title:
            names.append(BJShare.database_title.strip())
        return names

    def _details_row_dupes(
        self, meta: Meta, row: Tag
    ) -> list[dict[str, str | list[str]]]:
        torrent_id = self._details_row_id(row)
        if torrent_id is None:
            return []
        name = self._details_row_name(row)
        if not name:
            return []
        row_type = self._book_row_type(meta, row.get("data-format"), name)
        names = self._details_row_names(meta, name)
        return self._dupe_entries(
            meta, names, torrent_id, self._details_row_size(row), row_type
        )

    def _details_page_dupes(
        self, meta: Meta, soup: BeautifulSoup, table: Tag
    ) -> list[dict[str, str | list[str]]]:
        BJShare.already_has_the_info = True
        BJShare.database_title = self.get_database_title(soup)
        BJShare.database_identifier = self.get_database_identifier(soup)
        BJShare.database_overview = self.get_database_overview(soup)
        dupes: list[dict[str, str | list[str]]] = []
        for row in table.find_all("tr"):
            if isinstance(row, Tag):
                dupes.extend(self._details_row_dupes(meta, row))
        return dupes

    @staticmethod
    def _torrent_id_from_link(link: object, pattern: str) -> str | None:
        if not isinstance(link, Tag):
            return None
        href = link.get("href", "")
        if not isinstance(href, str):
            return None
        match = re.search(pattern, href)
        return match.group(1) if match else None

    def _search_row_id(self, row: Tag) -> tuple[str | None, Tag | None]:
        title_link = row.find("a", href=re.compile(r"torrentid=\d+"))
        torrent_id = self._torrent_id_from_link(title_link, r"torrentid=(\d+)")
        if torrent_id is not None:
            return torrent_id, title_link if isinstance(
                title_link, Tag
            ) else None
        download_link = row.find(
            "a", href=re.compile(r"action=download&id=\d+")
        )
        return self._torrent_id_from_link(download_link, r"id=(\d+)"), None

    @staticmethod
    def _torrent_info_name(info: Tag | None) -> str:
        if info is None:
            return ""
        value = info.get("data-torrentname", "") or info.get("data-name", "")
        return str(value).strip() if value else ""

    @staticmethod
    def _present_names(*values: str) -> list[str]:
        return [value.strip() for value in values if value]

    @classmethod
    def _search_row_names(
        cls, category: str, data_name: str, site_name: str
    ) -> list[str]:
        if category == "BOOK":
            return cls._present_names(data_name, site_name)
        return cls._present_names(data_name or site_name)

    @staticmethod
    def _size_cell_texts(row: Tag) -> list[str]:
        return [
            cell.get_text(strip=True)
            for cell in row.find_all("td")
            if isinstance(cell, Tag)
        ]

    @staticmethod
    def _first_size_text(values: list[str]) -> str | None:
        pattern = re.compile(
            r"\d+(\.\d+)?\s*(B|KiB|MiB|GiB|TiB|KB|MB|GB|TB)",
            re.IGNORECASE,
        )
        for value in values:
            if pattern.search(value):
                return value
        return None

    @classmethod
    def _search_row_size(cls, row: Tag) -> str:
        values = cls._size_cell_texts(row)
        if len(values) < 5:
            return ""
        return cls._first_size_text(values) or values[4]

    @staticmethod
    def _search_row_info(row: Tag) -> Tag | None:
        info = row.find("div", class_="torrent_info")
        return info if isinstance(info, Tag) else None

    @staticmethod
    def _search_row_site_name(title_link: Tag | None) -> str:
        return title_link.get_text(strip=True) if title_link else ""

    def _search_row_dupes(
        self, meta: Meta, row: Tag
    ) -> list[dict[str, str | list[str]]]:
        torrent_id, title_link = self._search_row_id(row)
        if torrent_id is None:
            return []
        info = self._search_row_info(row)
        data_name = self._torrent_info_name(info)
        site_name = self._search_row_site_name(title_link)
        names = self._search_row_names(meta.category, data_name, site_name)
        if not names:
            return []
        format_value = info.get("data-format", "") if info else ""
        row_type = self._book_row_type(
            meta, format_value, data_name or site_name
        )
        return self._dupe_entries(
            meta, names, torrent_id, self._search_row_size(row), row_type
        )

    def _search_table_dupes(
        self, meta: Meta, table: Tag
    ) -> list[dict[str, str | list[str]]]:
        dupes: list[dict[str, str | list[str]]] = []
        for row in table.find_all("tr", class_="torrent"):
            if isinstance(row, Tag):
                dupes.extend(self._search_row_dupes(meta, row))
        return dupes

    def _parse_search_response(
        self, meta: Meta, response: httpx.Response
    ) -> list[dict[str, str | list[str]]]:
        soup = BeautifulSoup(response.text, "html.parser")
        details = soup.find("div", class_="main_column")
        if isinstance(details, Tag):
            return self._details_page_dupes(meta, soup, details)
        table = soup.find("table", id="torrent_table")
        if isinstance(table, Tag):
            return self._search_table_dupes(meta, table)
        return []

    async def _apply_title_fallback(
        self,
        meta: Meta,
        search_url: str,
        title: str,
        details: httpx.Response | None,
        fallback: httpx.Response | None,
        title_queried: bool,
        media_terms: list[str],
    ) -> tuple[httpx.Response | None, httpx.Response | None]:
        needed = self._needs_title_fallback(
            meta, details, title, title_queried, media_terms
        )
        if not needed:
            return details, fallback
        candidate = await self._title_fallback_response(
            meta, search_url, title
        )
        if candidate is None:
            return None, None
        next_details = (
            candidate if self._response_has_details(candidate) else details
        )
        return next_details, candidate

    async def search_existing(
        self, meta: Meta
    ) -> list[dict[str, str | list[str]]]:
        title = self._search_title(meta)
        queries, media_terms, title_queried = self._search_queries(meta, title)
        await self._load_search_cookies(meta)
        self._reset_database_match_state()
        search_url = f"{self.base_url}/torrents.php"
        details, fallback = await self._run_search_queries(
            meta, search_url, queries
        )
        if getattr(meta, "skipping", None) == self.tracker:
            return []
        details, fallback = await self._apply_title_fallback(
            meta,
            search_url,
            title,
            details,
            fallback,
            title_queried,
            media_terms,
        )
        response = details or fallback
        return self._parse_search_response(meta, response) if response else []

    def get_edition(self, meta: Meta) -> str:
        edition_str = meta.edition.lower()
        if not edition_str:
            return ""

        edition_map = {
            "director's cut": "Director's Cut",
            "extended": "Extended Edition",
            "imax": "IMAX",
            "open matte": "Open Matte",
            "noir": "Noir Edition",
            "theatrical": "Theatrical Cut",
            "uncut": "Uncut",
            "unrated": "Unrated",
            "uncensored": "Uncensored",
        }

        for keyword, label in edition_map.items():
            if keyword in edition_str:
                return label

        return ""

    @staticmethod
    def _bdmv_size(meta: Meta) -> float:
        try:
            return float(meta.bdinfo["size"])
        except KeyError, IndexError, TypeError, ValueError:
            return 0.0

    @classmethod
    def _bdmv_bitrate(cls, meta: Meta) -> str:
        if meta.disctype in {"BD100", "BD66", "BD50", "BD25"}:
            return meta.disctype
        size_in_gb = cls._bdmv_size(meta)
        if size_in_gb > 66:
            return "BD100"
        if size_in_gb > 50:
            return "BD66"
        if size_in_gb > 25:
            return "BD50"
        return "BD25"

    @staticmethod
    def _dvd_bitrate(meta: Meta) -> str:
        return meta.dvd_size if meta.dvd_size in {"DVD9", "DVD5"} else "DVD9"

    @classmethod
    def _disc_bitrate(cls, meta: Meta) -> str | None:
        if meta.type != "DISC":
            return None
        if meta.is_disc == "BDMV":
            return cls._bdmv_bitrate(meta)
        if meta.is_disc == "DVD":
            return cls._dvd_bitrate(meta)
        return None

    @staticmethod
    def _source_bitrate(source_type: object) -> str:
        if not isinstance(source_type, str) or not source_type:
            return "Outro"
        keyword_map = {
            "webdl": "WEB-DL",
            "webrip": "WEBRip",
            "web": "WEB",
            "remux": "Blu-ray",
            "encode": "Blu-ray",
            "bdrip": "BDRip",
            "brrip": "BRRip",
            "hdtv": "HDTV",
            "sdtv": "SDTV",
            "dvdrip": "DVDRip",
            "hd-dvd": "HD DVD",
            "dvdscr": "DVDScr",
            "hdrip": "HDRip",
            "hdtc": "HDTC",
            "pdtv": "PDTV",
            "tc": "TC",
            "uhdtv": "UHDTV",
            "vhsrip": "VHSRip",
            "tvrip": "TVRip",
        }
        return keyword_map.get(source_type.lower(), "Outro")

    def get_bitrate(self, meta: Meta) -> str:
        return self._disc_bitrate(meta) or self._source_bitrate(meta.type)

    def get_audiobook_bitrate(self, meta: Meta) -> str:
        """
        Extracts the audiobook bitrate from metadata, finds the closest option
        from [64, 128, 192, 256, 320] within a threshold, otherwise returns 'Outro'.
        """
        avg_bitrate = meta.audiobook_bitrate
        if avg_bitrate is None:
            return "Outro"

        options = [64, 128, 192, 256, 320]

        # Find option with the minimum absolute difference
        closest_option = min(options, key=lambda opt: abs(opt - avg_bitrate))
        distance = abs(closest_option - avg_bitrate)

        # If distance is greater than 32 (meaning beyond midpoints), return "Outro"
        if distance > 32:
            return "Outro"

        return str(closest_option)

    async def img_host(self, image_bytes: bytes, filename: str) -> str | None:
        upload_url = f"{self.base_url}/ajax.php?action=screen_up"
        headers = {
            "Referer": f"{self.base_url}/upload.php",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }
        files = {"file": (filename, image_bytes, "image/png")}

        try:
            response = await self.session.post(
                upload_url, headers=headers, files=files, timeout=120
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()

            img_url = None
            if data.get("url") and str(data.get("url", "")).startswith("http"):
                img_url = str(data.get("url", "")).replace("\\/", "/")
            else:
                logger.info(
                    f"{self.tracker}: [bold red]The image host appears to be down.[/bold red]"
                )

            return img_url
        except Exception as e:
            logger.info(
                f"Exceção no upload de {filename}: {e}",
                extra={"markup": False},
            )
            return None

    def _tmdb_cover_url(self, meta: Meta) -> tuple[str, str] | None:
        cover_path = (
            self.main_tmdb_data.get("poster_path") or meta.tmdb_poster_path
        )
        if not cover_path:
            logger.info(
                f"{self.tracker}: Nenhum poster_path encontrado nos dados do TMDB.",
                extra={"markup": False},
            )
            return None
        path = str(cover_path)
        return f"https://image.tmdb.org/t/p/w500{path}", Path(path).name

    async def _upload_remote_cover(
        self, url: str, filename: str
    ) -> str | None:
        try:
            response = await self.session.get(url, timeout=120)
            response.raise_for_status()
            return await self.img_host(response.content, filename)
        except Exception as error:
            logger.info(
                f"{self.tracker}: Falha ao processar pôster da URL {url}: {error}",
                extra={"markup": False},
            )
            return None

    async def _media_cover(self, meta: Meta) -> str | None:
        cover = self._tmdb_cover_url(meta)
        if cover is None:
            return None
        url, filename = cover
        if BJShare.already_has_the_info:
            return url
        return await self._upload_remote_cover(url, filename)

    async def _local_cover(self, meta: Meta) -> str | None:
        cover_path = meta.artwork_path
        if (
            not isinstance(cover_path, (str, Path))
            or not str(cover_path).strip()
        ):
            logger.info(
                "Nenhum cover_path válido encontrado.",
                extra={"markup": False},
            )
            return None
        if not await self.common.path_exists(cover_path):
            logger.info(
                "Nenhum cover_path válido encontrado.",
                extra={"markup": False},
            )
            return None
        try:
            async with aiofiles.open(cover_path, "rb") as file_handle:
                image_bytes = await file_handle.read()
            return await self.img_host(image_bytes, Path(cover_path).name)
        except Exception as error:
            logger.info(
                f"{self.tracker}: Falha ao ler ou enviar capa {cover_path}: {error}",
                extra={"markup": False},
            )
            return None

    async def get_cover(self, meta: Meta) -> str | None:
        if meta.category in ("MOVIE", "TV"):
            return await self._media_cover(meta)
        if meta.category in ("BOOK", "GAME"):
            return await self._local_cover(meta)
        return None

    async def _upload_local_screenshot(self, path: Path) -> str | None:
        async with aiofiles.open(path, "rb") as file_handle:
            image_bytes = await file_handle.read()
        return await self.img_host(image_bytes, path.name)

    async def _upload_remote_screenshot(self, url: str) -> str | None:
        try:
            response = await self.session.get(url, timeout=120)
            response.raise_for_status()
            filename = Path(urlparse(url).path).name or "screenshot.png"
            return await self.img_host(response.content, filename)
        except Exception as error:
            logger.info(
                f"{self.tracker}: Failed to process screenshot from URL {url}: {error}",
                extra={"markup": False},
            )
            return None

    @staticmethod
    def _raw_image_urls(images: list[dict[str, Any]], limit: int) -> list[str]:
        urls = [
            str(image.get("raw_url"))
            for image in images
            if image.get("raw_url")
        ]
        return urls[:limit]

    async def _collect_remote_screenshots(
        self, urls: list[str], results: list[str]
    ) -> None:
        for coro in asyncio.as_completed(
            [self._upload_remote_screenshot(url) for url in urls]
        ):
            result = await coro
            if result:
                results.append(result)

    async def _collect_local_screenshots(
        self, paths: list[Path], results: list[str]
    ) -> None:
        for coro in asyncio.as_completed(
            [self._upload_local_screenshot(path) for path in paths]
        ):
            result = await coro
            if result:
                results.append(result)

    async def get_screenshots(self, meta: Meta) -> list[str]:
        results: list[str] = []
        menu_urls = self._raw_image_urls(meta.menu_images, 3)
        await self._collect_remote_screenshots(menu_urls, results)
        remaining = max(0, 6 - len(results))
        local_files = sorted(
            screenshots_dir(meta.base_dir, meta.uuid).glob("*.png")
        )[:remaining]
        if local_files:
            await self._collect_local_screenshots(local_files, results)
            return results
        image_urls = self._raw_image_urls(meta.image_list, remaining)
        await self._collect_remote_screenshots(image_urls, results)
        return results

    def get_runtime(self, meta: Meta) -> tuple[int, int]:
        """
        Extracts runtime from metadata and converts total minutes into hours and minutes.
        """
        total_minutes = (
            meta.video_duration if meta.video_duration is not None else 60
        )
        hours, minutes = divmod(total_minutes, 60)

        return hours, minutes

    def get_release_date(self) -> str:
        raw_date_string = self.main_tmdb_data.get(
            "first_air_date"
        ) or self.main_tmdb_data.get("release_date")

        if not raw_date_string:
            return ""

        try:
            date_object = datetime.strptime(
                raw_date_string, "%Y-%m-%d"
            ).replace(tzinfo=UTC)
            return date_object.strftime("%d %b %Y")

        except ValueError:
            return ""

    @staticmethod
    def _bdmv_is_10_bit(meta: Meta) -> bool:
        try:
            bit_depth = meta.discs[0]["bdinfo"]["video"][0]["bit_depth"]
        except KeyError, IndexError, TypeError:
            return False
        return "10" in str(bit_depth)

    @classmethod
    def _is_10_bit(cls, meta: Meta) -> bool:
        if meta.is_disc == "BDMV":
            return cls._bdmv_is_10_bit(meta)
        return meta.bit_depth == "10"

    @staticmethod
    def _hdr_tags(hdr: str) -> set[str]:
        hdr_upper = hdr.upper()
        tags: set[str] = set()
        if "DV" in hdr_upper:
            tags.add("Dolby Vision")
        if "HDR10+" in hdr_upper:
            tags.add("HDR10+")
        elif "HDR" in hdr_upper:
            tags.add("HDR10")
        return tags

    @staticmethod
    def _release_feature_tags(meta: Meta) -> set[str]:
        tags: set[str] = set()
        if meta.type == "REMUX":
            tags.add("Remux")
        if meta.extras:
            tags.add("Com extras")
        if meta.has_commentary or meta.manual_commentary:
            tags.add("Com comentários")
        return tags

    def find_remaster_tags(self, meta: Meta) -> set[str]:
        found_tags = self._release_feature_tags(meta) | self._hdr_tags(
            meta.hdr
        )
        edition = self.get_edition(meta)
        if edition:
            found_tags.add(edition)
        if "Atmos" in meta.audio:
            found_tags.add("Dolby Atmos")
        if self._is_10_bit(meta):
            found_tags.add("10-bit")
        return found_tags

    def build_remaster_title(self, meta: Meta) -> str:
        tag_priority = [
            "Dolby Atmos",
            "Remux",
            "Director's Cut",
            "Extended Edition",
            "IMAX",
            "Open Matte",
            "Noir Edition",
            "Theatrical Cut",
            "Uncut",
            "Unrated",
            "Uncensored",
            "10-bit",
            "Dolby Vision",
            "HDR10+",
            "HDR10",
            "Com extras",
            "Com comentários",
        ]
        available_tags = self.find_remaster_tags(meta)

        ordered_tags = [tag for tag in tag_priority if tag in available_tags]

        return " / ".join(ordered_tags)

    def _normalize_credit_name(self, name: str) -> str:
        normalized = re.sub(r"\s+", " ", unidecode(name).strip())
        normalized = re.sub(r"[^A-Za-z0-9 .'\-]", "", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _normalized_credit_candidate(self, raw_name: object) -> str | None:
        if not isinstance(raw_name, str):
            return None
        normalized = self._normalize_credit_name(raw_name)
        return normalized or None

    def _collect_credit_names(
        self, raw_names: list[Any], limit: int
    ) -> list[str]:
        normalized_names: list[str] = []
        seen: set[str] = set()
        for raw_name in raw_names:
            normalized = self._normalized_credit_candidate(raw_name)
            if normalized is None:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized_names.append(normalized)
            if len(normalized_names) >= limit:
                break
        return normalized_names

    @staticmethod
    def _credit_role_config(role: str) -> tuple[str, str, str, int] | None:
        configs = {
            "director": ("directors", "tmdb_directors", "Diretor", 1),
            "creator": ("creators", "tmdb_creators", "Criador", 1),
            "cast": ("stars", "tmdb_cast", "Elenco", 5),
        }
        return configs.get(role)

    @staticmethod
    def _credit_source_names(
        meta: Meta, role: str, imdb_key: str, tmdb_key: str
    ) -> list[Any]:
        if role == "cast":
            return list(meta.cast)
        imdb_names = meta.imdb_info.get(imdb_key, [])
        tmdb_names = meta.get(tmdb_key, [])
        return list(imdb_names) + list(tmdb_names)

    def _skip_missing_credit(self, meta: Meta, display_name: str) -> str:
        logger.info(
            f"{self.tracker}: [yellow]Unattended mode: {display_name} não "
            f"encontrado(s). Pulando upload para {self.tracker}.[/yellow]"
        )
        meta.skipping = self.tracker
        return "skipped"

    @staticmethod
    def _credit_prompt(display_name: str, limit: int) -> str:
        suffix = (
            " (apenas uma pessoa)"
            if limit == 1
            else " (separados por vírgula)"
        )
        return (
            f"{display_name} não encontrado(s).\n"
            f"Por favor, insira manualmente{suffix}: "
        )

    async def _prompt_credit(self, display_name: str, limit: int) -> str:
        raw = await prompt_in_thread(
            cli_ui.ask_string, self._credit_prompt(display_name, limit)
        )
        entered = [
            name.strip() for name in (raw or "").split(",") if name.strip()
        ]
        normalized = self._collect_credit_names(entered, limit)
        return ", ".join(normalized) if normalized else "skipped"

    def _existing_credit_value(
        self, meta: Meta, role: str, config: tuple[str, str, str, int]
    ) -> str | None:
        imdb_key, tmdb_key, _display_name, limit = config
        names = self._credit_source_names(meta, role, imdb_key, tmdb_key)
        unique_names = self._collect_credit_names(names, limit)
        return ", ".join(unique_names) if unique_names else None

    async def _missing_credit_value(
        self, meta: Meta, display_name: str, limit: int
    ) -> str:
        if meta.unattended and not meta.unattended_confirm:
            return self._skip_missing_credit(meta, display_name)
        return await self._prompt_credit(display_name, limit)

    async def get_credits(self, meta: Meta, role: str) -> str:
        if BJShare.already_has_the_info:
            return "N/A"
        config = self._credit_role_config(role)
        if config is None:
            return "N/A"
        existing = self._existing_credit_value(meta, role, config)
        if existing is not None:
            return existing
        _imdb_key, _tmdb_key, display_name, limit = config
        return await self._missing_credit_value(meta, display_name, limit)

    def get_imdb_rating(self, meta: Meta):
        imdb_info = dict(meta.imdb_info)
        rating = imdb_info.get("rating")

        if not rating:
            return "N/A"

        return str(rating)

    def _request_search_enabled(self, meta: Meta) -> bool:
        configured = bool(self.config["DEFAULT"].get("search_requests", False))
        return configured or bool(meta.search_requests)

    def _request_title(self, meta: Meta) -> str:
        if meta.category == "BOOK":
            return self.common.portuguese_title_capitalization(meta.title)
        return meta.title

    @staticmethod
    def _request_category(meta: Meta) -> str | int:
        if meta.anime:
            return 14
        return {"TV": 2, "MOVIE": 1}.get(meta.category, meta.category)

    def _request_search_url(self, meta: Meta) -> str:
        title = self._request_title(meta)
        category = self._request_category(meta)
        return (
            f"{self.requests_url}submit=true&search={title}&showall=on&"
            f"filter_cat[{category}]=1"
        )

    @staticmethod
    def _request_reward(cell: Tag) -> str:
        parts = [
            item.text.replace("\xa0", " ").strip()
            for item in cell.select("tr > td:first-child")
        ]
        return " / ".join(parts)

    @staticmethod
    def _request_row_cells(row: Tag) -> list[Tag]:
        return [cell for cell in row.find_all("td") if isinstance(cell, Tag)]

    @staticmethod
    def _request_link_and_quality(info_cell: Tag) -> tuple[Tag, Tag] | None:
        link = info_cell.select_one('a[href*="requests.php?action=view"]')
        quality = info_cell.select_one("b")
        if not isinstance(link, Tag) or not isinstance(quality, Tag):
            return None
        return link, quality

    @classmethod
    def _parse_request_row(cls, row: Tag) -> dict[str, str] | None:
        cells = cls._request_row_cells(row)
        if len(cells) < 5:
            return None
        parts = cls._request_link_and_quality(cells[1])
        if parts is None:
            return None
        link_element, quality_element = parts
        href = link_element.get("href")
        return {
            "Name": link_element.get_text(strip=True),
            "Quality": quality_element.get_text(strip=True),
            "Reward": cls._request_reward(cells[3]),
            "Link": href if isinstance(href, str) else "",
        }

    @classmethod
    def _parse_request_results(cls, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str]] = []
        for row in soup.select("#torrent_table tr.torrent"):
            if not isinstance(row, Tag):
                continue
            parsed = cls._parse_request_row(row)
            if parsed is not None:
                results.append(parsed)
        return results

    def _request_message(self, results: list[dict[str, str]]) -> str:
        lines = [
            "",
            f"{self.tracker}: [bold yellow]Seu upload pode atender o(s) "
            "seguinte(s) pedido(s), confira:[/bold yellow]",
            "",
        ]
        for result in results:
            lines.extend(
                [
                    f"[bold green]Nome:[/bold green] {result['Name']}",
                    f"[bold green]Qualidade:[/bold green] {result['Quality']}",
                    f"[bold green]Recompensa:[/bold green] {result['Reward']}",
                    f"[bold green]Link:[/bold green] {self.base_url}/{result['Link']}",
                    "",
                ]
            )
        return "\n".join(lines)

    async def _request_results(self, meta: Meta) -> list[dict[str, str]]:
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar
        response = await self.session.get(self._request_search_url(meta))
        response.raise_for_status()
        return self._parse_request_results(response.text)

    async def get_requests(self, meta: Meta) -> list[dict[str, str]]:
        if not self._request_search_enabled(meta):
            return []
        try:
            results = await self._request_results(meta)
        except Exception as error:
            logger.info(
                f"{self.tracker}: [bold red]Ocorreu um erro ao buscar pedido(s) "
                f"no {self.tracker}: {error}[/bold red]"
            )
            import traceback

            logger.info(traceback.format_exc())
            return []
        if results:
            logger.info(self._request_message(results))
        return results

    @staticmethod
    def _book_language_name(meta: Meta) -> str:
        return {
            "por": "Português",
            "spa": "Espanhol",
            "eng": "Inglês",
        }.get(meta.book_language_iso, "Outro")

    async def _book_upload_data(
        self, meta: Meta, original_title: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": original_title,
            "diretor": meta.author,
            "idioma": self._book_language_name(meta),
            "release_desc": await self.build_description(meta),
        }
        if meta.audiobook:
            bitrate = self.get_audiobook_bitrate(meta)
            data.update({"bitrateTypes": bitrate, "bitrate": bitrate})
        return data

    @staticmethod
    def _game_release_description(meta: Meta) -> str:
        localized = meta.localized_overviews
        brazilian = (
            localized.get("brazilian", "")
            if isinstance(localized, dict)
            else ""
        )
        return str(brazilian or meta.overview)

    @staticmethod
    def _pc_game_fields(meta: Meta) -> dict[str, Any]:
        if meta.platform != "PC":
            return {}
        fields: dict[str, Any] = {}
        if meta.tag:
            fields["release"] = meta.tag.lstrip("-")
        if meta.game_version:
            fields["versao"] = meta.game_version
        return fields

    @staticmethod
    def _game_unlock_type(meta: Meta) -> str:
        container = meta.container.upper()
        allowed = {"NSP", "XCI", "NSZ", "XCZ", "LT", "JTAG/RGH"}
        return container if container in allowed else ""

    @classmethod
    def _console_game_fields(cls, meta: Meta) -> dict[str, Any]:
        platform = meta.platform.upper().strip()
        if platform in {"PC", "MAC", "LINUX", "EMULATOR"}:
            return {}
        fields: dict[str, Any] = {}
        if meta.game_system:
            fields["sistema"] = meta.game_system
        if meta.game_region:
            fields["regiao"] = meta.game_region
        unlock_type = cls._game_unlock_type(meta)
        if unlock_type:
            fields["destravamento"] = unlock_type
        return fields

    async def _game_upload_data(
        self, meta: Meta, original_title: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": original_title,
            "plataforma": self.get_game_platform(meta),
            "idioma": self.get_game_language(meta),
            "tags": await self.get_tags(meta),
            "adulto": self.get_adulto(meta),
            "release_desc": self._game_release_description(meta),
            "fichatecnica": await self.build_description(meta),
            "traileryoutube": meta.youtube,
            "subcategoria": self.get_game_subcategory(meta),
        }
        data.update(self._pc_game_fields(meta))
        system = self.get_sistema(meta)
        if system:
            data["sistema"] = system
        if meta.repack:
            data["repack"] = "on"
        data.update(self._console_game_fields(meta))
        return data

    async def _media_common_data(
        self,
        meta: Meta,
        original_title: str,
        brazilian_title: str,
    ) -> dict[str, Any]:
        width, height = self.get_resolution(meta)
        hours, minutes = self.get_runtime(meta)
        return {
            "audio": await self.get_audio(meta),
            "codecaudio": self.get_audio_codec(meta),
            "codecvideo": self.get_video_codec(meta),
            "duracaoHR": str(hours),
            "duracaoMIN": str(minutes),
            "duracaotipo": "selectbox",
            "fichatecnica": await self.build_description(meta),
            "idioma": self.get_languages(),
            "imdblink": self.get_imdblink(meta),
            "qualidade": self.get_bitrate(meta),
            "release": meta.service_longname,
            "remaster_title": self.build_remaster_title(meta),
            "resolucaoh": height,
            "resolucaow": width,
            "sinopse": await self.get_overview(meta),
            "tags": await self.get_tags(meta),
            "tipolegenda": await self.get_subtitle(meta),
            "title": original_title,
            "titulobrasileiro": brazilian_title,
            "traileryoutube": self.get_trailer(meta),
        }

    async def _movie_fields(self, meta: Meta) -> dict[str, Any]:
        return {
            "adulto": self.get_adulto(meta),
            "diretor": await self.get_credits(meta, "director"),
        }

    async def _tv_fields(self, meta: Meta) -> dict[str, Any]:
        return {
            "diretor": await self.get_credits(meta, "creator"),
            "tipo": "episode" if meta.tv_pack == 0 else "season",
            "season": meta.season_int,
            "episode": meta.episode_int,
        }

    @staticmethod
    def _country_names(codes: list[str]) -> list[str]:
        names: list[str] = []
        for code in codes:
            country = pycountry.countries.get(alpha_2=code)
            if country is not None:
                names.append(str(country.name))
        return names

    def _series_director_names(self, meta: Meta) -> list[str]:
        names = list(
            meta.tmdb_directors or meta.imdb_info.get("directors", [])
        )
        return self._collect_credit_names(names, 1)

    def _tv_non_anime_fields(self, meta: Meta) -> dict[str, Any]:
        countries = [
            str(code) for code in self.main_tmdb_data.get("origin_country", [])
        ]
        networks = [
            str(item.get("name", ""))
            for item in self.main_tmdb_data.get("networks", [])
            if isinstance(item, dict)
        ]
        return {
            "network": ", ".join(networks) or "",
            "numtemporadas": self.main_tmdb_data.get("number_of_seasons", ""),
            "datalancamento": self.get_release_date(),
            "pais": ", ".join(self._country_names(countries)),
            "diretorserie": ", ".join(self._series_director_names(meta)),
            "avaliacao": self.get_rating(),
        }

    async def _non_anime_fields(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "validimdb": "yes",
            "imdbrating": self.get_imdb_rating(meta),
            "elenco": await self.get_credits(meta, "cast"),
        }
        if meta.category == "MOVIE":
            data["datalancamento"] = self.get_release_date()
        if meta.category == "TV":
            data.update(self._tv_non_anime_fields(meta))
        return data

    def _anime_fields(self, meta: Meta) -> dict[str, Any]:
        if meta.category == "MOVIE":
            return {"tipo": "movie"}
        if meta.category == "TV":
            return {"adulto": self.get_adulto(meta)}
        return {}

    async def _media_upload_data(
        self,
        meta: Meta,
        original_title: str,
        brazilian_title: str,
    ) -> dict[str, Any]:
        data = await self._media_common_data(
            meta, original_title, brazilian_title
        )
        if meta.category == "MOVIE":
            data.update(await self._movie_fields(meta))
        if meta.category == "TV":
            data.update(await self._tv_fields(meta))
        extra = (
            self._anime_fields(meta)
            if meta.anime
            else await self._non_anime_fields(meta)
        )
        data.update(extra)
        return data

    def _tracker_config(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.config["TRACKERS"][self.tracker])

    def _anonymous_fields(self, meta: Meta) -> dict[str, Any]:
        tracker_config = self._tracker_config()
        anonymous = not (
            meta.anon == 0 and not tracker_config.get("anon", False)
        )
        if not anonymous:
            return {}
        fields: dict[str, Any] = {"anonymous": "on"}
        if tracker_config.get("show_group_if_anon", False):
            fields["anonymousshowgroup"] = "on"
        return fields

    def _internal_fields(self, meta: Meta) -> dict[str, Any]:
        if not meta.tag:
            return {}
        tracker_config = self._tracker_config()
        if tracker_config.get("internal", False) is not True:
            return {}
        groups = tracker_config.get("internal_groups", [])
        return {"internalrel": 1} if meta.tag[1:] in groups else {}

    async def _image_fields(self, meta: Meta) -> dict[str, Any]:
        if meta.debug:
            return {}
        fields: dict[str, Any] = {"image": await self.get_cover(meta)}
        if not meta.audiobook:
            fields["screenshots[]"] = await self.get_screenshots(meta)
        return fields

    async def _category_upload_data(
        self,
        meta: Meta,
        original_title: str,
        brazilian_title: str,
    ) -> dict[str, Any]:
        if meta.category == "BOOK":
            return await self._book_upload_data(meta, original_title)
        if meta.category == "GAME":
            return await self._game_upload_data(meta, original_title)
        if meta.category in ("MOVIE", "TV"):
            return await self._media_upload_data(
                meta, original_title, brazilian_title
            )
        return {}

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        await self.load_localized_data(meta)
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookie_jar:
            self.session.cookies = cookie_jar
        original_title, brazilian_title = self.get_titles(meta)
        data: dict[str, Any] = {
            "submit": "true",
            "auth": BJShare.secret_token,
            "formato": self.get_container(meta),
            "type": str(self.get_type(meta)),
            "year": self.get_year(meta),
        }
        data.update(
            await self._category_upload_data(
                meta, original_title, brazilian_title
            )
        )
        data.update(self._anonymous_fields(meta))
        data.update(self._internal_fields(meta))
        if meta.repack:
            data["repack"] = "on"
        data.update(await self._image_fields(meta))
        return data

    @staticmethod
    def _numeric_year(value: object) -> str | None:
        text = str(value or "")
        return text if text.isdigit() else None

    def get_year(self, meta: Meta) -> str:
        year = str(meta.year) if meta.year is not None else "N/A"
        if meta.category == "MOVIE":
            return year
        tvdb_year = self._numeric_year(meta.tvdb_episode_year)
        if tvdb_year is not None:
            return tvdb_year
        imdb_year = self._numeric_year(meta.imdb_info.get("tv_year", ""))
        return imdb_year or year

    @staticmethod
    def _adult_keyword_match(genres: str) -> bool:
        adult_keywords = ("xxx", "erotic", "porn", "adult", "orgy")
        for keyword in adult_keywords:
            pattern = rf"(^|,\s*){re.escape(keyword)}(\s*,|$)"
            if re.search(pattern, genres, re.IGNORECASE):
                return True
        return False

    @classmethod
    def _metadata_is_adult(cls, meta: Meta) -> bool:
        if meta.adult_media:
            return True
        genres = f"{', '.join(meta.keywords)} {meta.combined_genres}"
        if meta.anime and "hentai" in genres.lower():
            return True
        return cls._adult_keyword_match(genres)

    def get_adulto(self, meta: Meta) -> str:
        return "1" if self._metadata_is_adult(meta) else "2"

    def get_imdblink(self, meta: Meta) -> str:
        """
        Get the media identifier for the upload.
        Uses the identifier from an existing BJShare group when available, then
        falls back to IMDb and TMDb metadata.

        Accepted formats:
            IMDb: tt12345
            TMDb: movie/12345 or tv/12345
        """
        if BJShare.database_identifier:
            return BJShare.database_identifier

        imdb_info = dict(meta.imdb_info)
        imdbid = str(imdb_info.get("imdbID", ""))
        if imdbid:
            return imdbid

        category = (meta.category).upper()
        tmdb_id = meta.tmdb_id

        if category in ["MOVIE", "TV"] and tmdb_id:
            return f"{category}/{tmdb_id}".lower()

        return ""

    def _existing_overview(self) -> str | None:
        database_overview = BJShare.database_overview
        if database_overview:
            logger.debug(
                f"{self.tracker}: Using database overview: "
                f"{database_overview[:50]}..."
            )
            return database_overview
        overview = self.main_tmdb_data.get("overview", "")
        if isinstance(overview, str) and overview.strip():
            return overview
        return None

    def _skip_missing_overview(self, meta: Meta) -> str:
        logger.info(
            f"{self.tracker}: [yellow]Sinopse não encontrada em modo unattended. "
            f"Pulando upload para {self.tracker}.[/yellow]"
        )
        meta.skipping = self.tracker
        return ""

    async def _prompt_overview(self) -> str:
        logger.info(
            f"{self.tracker}: [bold red]Sinopse não encontrada no TMDb. "
            "Por favor, insira manualmente.[/bold red]"
        )
        raw = await prompt_in_thread(
            cli_ui.ask_string,
            f'"{self.tracker}: [green]Digite a sinopse:[/green]"',
        )
        return (raw or "").strip() or "N/A"

    async def get_overview(self, meta: Meta | None = None) -> str:
        overview = self._existing_overview()
        if overview is not None:
            return overview
        if meta and meta.unattended and not meta.unattended_confirm:
            return self._skip_missing_overview(meta)
        return await self._prompt_overview()

    @staticmethod
    def _screenshots_issue(meta: Meta, data: dict[str, Any]) -> str:
        screenshots = data.get("screenshots[]", [])
        if not meta.debug and len(screenshots) < 2:
            return (
                "The number of successful screenshots uploaded is less than 2."
            )
        return ""

    @staticmethod
    def _media_data_issue(meta: Meta, data: dict[str, Any]) -> str:
        screenshot_issue = BJShare._screenshots_issue(meta, data)
        if screenshot_issue:
            return screenshot_issue
        credits = (
            data.get("diretor"),
            data.get("elenco"),
            data.get("creators"),
        )
        if "skipped" in credits:
            return (
                "Missing required credits information (director/cast/creator)."
            )
        if not data.get("imdblink"):
            return "Missing IMDb or TMDb identifier."
        return ""

    @staticmethod
    def _game_data_issue(meta: Meta, data: dict[str, Any]) -> str:
        if not data.get("plataforma"):
            return "Missing game platform."
        return BJShare._screenshots_issue(meta, data)

    def check_data(self, meta: Meta, data: dict[str, Any]) -> str:
        if meta.category in ("TV", "MOVIE"):
            return self._media_data_issue(meta, data)
        if meta.category == "GAME":
            return self._game_data_issue(meta, data)
        if meta.category == "BOOK" and not data.get("formato"):
            return "Missing compatible ebook format."
        return ""

    async def upload(self, meta: Meta):
        if getattr(meta, "skipping", None) == self.tracker:
            return False

        data = await self.get_data(meta)
        if getattr(meta, "skipping", None) == self.tracker:
            return False

        issue = self.check_data(meta, data)
        if issue:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error - {issue}"
            )
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
            id_pattern=r"torrentid=(\d+)",
            success_text="action=download&id=",
        )
