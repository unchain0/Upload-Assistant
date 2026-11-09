# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any

from src.domain_models.book_naming import (
    canonical_book_author,
    clean_book_name,
    clean_book_title,
)
from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D

_AUDIO_IN_FILENAME_RE = re.compile(
    r"(?ix)(?:ddp|dd\+|dd|dts|aac|flac|truehd|opus|eac3|ac3)"
    r"(?:[.\s_-]|hd|ma|atmos)*"
    r"\d\.\d"
)
_GROUP_ALREADY_MARKED_RE = re.compile(
    r"(?i)(?:^|[.\s])(?:dual|multi)(?:$|[.\s-])"
)
_VIDEO_SUFFIXES = (".mkv", ".mp4", ".avi", ".m2ts", ".ts")
_SIDECAR_SUFFIXES = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup")
_COMPRESSED_RE = re.compile(r"(?i)\.(?:rar|zip|7z|r\d{2,3}|\d{3})$")
_SIDECAR_LANGUAGE_RE = re.compile(
    r"(?i)(?:[._-](?:pt[-_. ]?br|pb|pt|por|eng|en|es|spa|lat|fre|fra|ger|deu|"
    r"jpn|jap|ita|rus|chi|zho|kor|dublado|legendado))+$"
)
_SCREENSHOTS_MINIMUM = 3


class CapybaraBR(UNIT3D):
    """
    CapybaraBR is a BRAZILIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "CAPYBARABR"
    display_name = "CapybaraBR"
    base_url = "https://capybarabr.com"
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    banned_url = f"{base_url}/api/banned-groups"
    banned_groups_auth_mode = "api_token"
    banned_groups_response_key = "groups"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    pending_url = f"{base_url}/api/torrents/pending"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME")
    tracker_urls = ("capybarabr.com",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="CAPYBARABR")
        self.config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._category_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = self._resolved_category(meta, category)
        return {"category_id": mapping.get(resolved, "0")}

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "ANIMES": "4",
            "BOOK": "11",
            "COMIC_MANGA": "10",
            "GAME": "5",
        }

    @staticmethod
    def _mapping_mode(
        mapping: dict[str, str], *, reverse: bool, mapping_only: bool
    ) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        return (
            {value: key for key, value in mapping.items()} if reverse else None
        )

    @classmethod
    def _resolved_category(cls, meta: Meta, requested: str) -> str:
        resolved = cls._base_category(meta, requested)
        if cls._is_anime_tv(meta, resolved):
            return "ANIMES"
        return (
            "COMIC_MANGA"
            if cls._is_comic_category(meta, resolved)
            else resolved
        )

    @staticmethod
    def _base_category(meta: Meta, requested: str) -> str:
        return requested if requested else str(meta.category or "")

    @staticmethod
    def _is_anime_tv(meta: Meta, resolved: str) -> bool:
        return meta.anime is True and resolved == "TV"

    @classmethod
    def _is_comic_category(cls, meta: Meta, resolved: str) -> bool:
        return resolved == "BOOK" and cls._is_comic_book(meta)

    @staticmethod
    def _is_comic_book(meta: Meta) -> bool:
        return (
            str(meta.type).upper() in {"CBR", "CBZ"}
            or bool(meta.manga)
            or bool(meta.comic)
        )

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = type or str(meta.type or "")
        if self._is_game_type(meta, resolved, mapping):
            return {"type_id": self._game_type_id(meta)}
        return {"type_id": mapping.get(resolved, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        console = (
            bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode().upper()
        )
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "DVDRIP": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "AZW3": "13",
            "CBR": "14",
            "CBZ": "15",
            "MOBI": "16",
            "PDF": "17",
            "EPUB": "18",
            "KFX": "19",
            "MP4": "21",
            "TS": "22",
            "MKV": "23",
            "MP3": "24",
            "M4B": "43",
            "FLAC": "43",
            "AAC": "43",
            "M4A": "43",
            "OGG": "43",
            "WAV": "43",
            "AUDIOBOOK": "24",
            "OUTROS": "43",
            "PC": "46",
            "PLAYSTATION": "48",
            "XBOX": "49",
            console: "50",
        }

    @staticmethod
    def _is_game_type(
        meta: Meta, resolved: str, mapping: dict[str, str]
    ) -> bool:
        return resolved == "GAME" or (
            meta.category == "GAME" and resolved not in mapping
        )

    @staticmethod
    def _game_type_id(meta: Meta) -> str:
        platform = str(meta.platform or "").casefold()
        console = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
        if CapybaraBR._contains_any(
            platform,
            ("playstation", "ps5", "ps4", "ps3", "ps2", "ps1", "psp", "vita"),
        ):
            return "48"
        if "xbox" in platform:
            return "49"
        if CapybaraBR._contains_any(
            platform, (console, "switch", "wii", "3ds", "nds", "ds")
        ):
            return "50"
        return "46"

    @staticmethod
    def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
        return any(needle in value for needle in needles)

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
            "Other": "10",
        }

        if mapping_only:
            return resolution_id
        if reverse:
            return {v: k for k, v in resolution_id.items()}
        if resolution:
            return {"resolution_id": resolution_id.get(resolution, "10")}
        meta_resolution = meta.resolution
        resolved_id = resolution_id.get(meta_resolution, "10")
        return {"resolution_id": resolved_id}

    async def get_description(self, meta: Meta) -> dict[str, str]:
        signature = f"[right][url=https://github.com/wastaken7/Upload-Assistant][size=4]Compartilhado com {meta.ua_name} {meta.current_version} (fork)[/size][/url][/right]"
        return {
            "description": await DescriptionBuilder(
                self.tracker, self.config
            ).general_description_generator(
                meta,
                mediainfo=False,
                nfo=False,
                signature=signature,
            )
        }

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = self._name_for_category(meta, str(meta.category or ""))
        return {"name": re.sub(r"\s{2,}", " ", name)}

    def _name_for_category(self, meta: Meta, category: str) -> str:
        if category == "BOOK":
            return self._book_name(meta)
        if category == "GAME":
            return self._game_name(meta)
        if category in {"MOVIE", "TV"}:
            return self._video_name(meta)
        return str(meta.name or "")

    def _book_name(self, meta: Meta) -> str:
        author = canonical_book_author(meta)
        title = self.common.portuguese_title_capitalization(
            self._book_title(meta)
        )
        name = f"{title} - {author} [{self._year_text(meta)}]{self._audiobook_suffix(meta)}"
        return clean_book_name(f"{name}{self._book_language_suffix(meta)}")

    @staticmethod
    def _book_title(meta: Meta) -> str:
        """Normalize the title so the name is always "<titulo> - <autor>"."""
        return clean_book_title(
            meta.title or "", canonical_book_author(meta)
        )

    @staticmethod
    def _strip_author_segment(title: str, author: str) -> str:
        parts = re.split(r"\s[-\u2013]\s", title)
        if len(parts) > 1:
            if CapybaraBR._same_author(parts[0], author):
                return " - ".join(parts[1:]).strip()
            if CapybaraBR._looks_like_author(parts[-1], author):
                return " - ".join(parts[:-1]).strip()
        for separator in (" - ", " \u2013 ", ": "):
            prefix = f"{author}{separator}"
            if title.casefold().startswith(prefix.casefold()):
                return title[len(prefix) :].strip()
        return title

    @staticmethod
    def _normalized_author(value: str) -> str:
        return re.sub(r"[.\s]+", " ", value.casefold()).strip(" ,")

    @staticmethod
    def _same_author(candidate: str, author: str) -> bool:
        return CapybaraBR._normalized_author(
            candidate
        ) == CapybaraBR._normalized_author(author)

    @staticmethod
    def _looks_like_author(candidate: str, author: str) -> bool:
        fragment = CapybaraBR._normalized_author(candidate)
        full = CapybaraBR._normalized_author(author)
        if not fragment or not full:
            return False
        return fragment == full or full.startswith(f"{fragment} ")

    @staticmethod
    def _year_text(meta: Meta) -> str:
        return "" if meta.year is None else str(meta.year)

    @staticmethod
    def _audiobook_suffix(meta: Meta) -> str:
        return " [AUDIOBOOK]" if meta.audiobook else ""

    @staticmethod
    def _book_language_suffix(meta: Meta) -> str:
        language = str(meta.book_language_iso or "")
        if not language or language == "por":
            return ""
        return f" [{language.upper()}]"

    @staticmethod
    def _game_name(meta: Meta) -> str:
        tag = str(meta.tag or "").lstrip("-")
        language = CapybaraBR._game_language_tag(meta)
        subcategory = str(meta.game_subcategory or "").casefold()
        update = "Update" if subcategory == "update" else ""
        dlc = CapybaraBR._game_dlc_tag(subcategory)
        year = "" if meta.year is None else str(meta.year)
        return f"{meta.title} {update} {meta.game_version} {year} - {tag} {language}{dlc}"

    @classmethod
    def _game_language_tag(cls, meta: Meta) -> str:
        languages = cls._game_languages(meta)
        if meta.manual_multi or cls._is_multilingual_portuguese(languages):
            return "[MULTI]"
        if cls._contains_language(languages, "ENGLISH"):
            return "[INGLÊS]"
        return f"[{str(meta.language or '').upper()}]"

    @staticmethod
    def _game_languages(meta: Meta) -> list[str]:
        return (
            [str(value) for value in meta.languages]
            if isinstance(meta.languages, list)
            else []
        )

    @classmethod
    def _is_multilingual_portuguese(cls, languages: list[str]) -> bool:
        if len(languages) <= 1:
            return False
        return cls._contains_language(languages, "PORTUGUESE")

    @staticmethod
    def _contains_language(languages: list[str], expected: str) -> bool:
        return any(expected in value.upper() for value in languages)

    @staticmethod
    def _game_dlc_tag(subcategory: str) -> str:
        if subcategory == "dlc":
            return " [DLC]"
        if subcategory == "full_game_dlc":
            return " [+DLC]"
        return ""

    def _video_name(self, meta: Meta) -> str:
        name = self._normalized_video_base(meta)
        name = self._localized_video_title(name, meta)
        if self.tracker == "CAPYBARABR" and meta.type == "DVDRIP":
            name = self._dvdrip_name(meta)
        name = self._apply_video_audio_tag(name, meta)
        return self._ensure_nogroup(name, meta.tag)

    @staticmethod
    def _normalized_video_base(meta: Meta) -> str:
        name = (
            str(meta.name or "")
            .replace("DD+ ", "DDP")
            .replace("DD ", "DD")
            .replace("AAC ", "AAC")
            .replace("FLAC ", "FLAC")
            .replace("Dubbed", "")
            .replace("Dual-Audio", "")
        )
        if meta.category in {"TV", "ANIMES"}:
            year = "" if meta.year is None else str(meta.year)
            if year:
                name = name.replace(f"({year})", "").replace(year, "").strip()
        return name

    @staticmethod
    def _localized_video_title(name: str, meta: Meta) -> str:
        if meta.original_language != "pt":
            return name.replace(str(meta.aka or ""), "")
        if not meta.aka:
            return name
        aka = str(meta.aka).replace("AKA", "").strip()
        return (
            name.replace(str(meta.aka), "")
            .replace(str(meta.title), aka)
            .strip()
        )

    @classmethod
    def _dvdrip_name(cls, meta: Meta) -> str:
        parts = (
            cls._dvdrip_title(meta),
            str(meta.year or ""),
            cls._episode_code(meta),
            str(meta.resolution or ""),
            "DVDRip",
            cls._normalized_audio(meta.audio),
            str(meta.video_encode or ""),
        )
        name = cls._compact_parts(parts)
        return f"{name}{meta.tag}" if meta.tag else name

    @staticmethod
    def _dvdrip_title(meta: Meta) -> str:
        if meta.original_language == "pt" and meta.aka:
            return str(meta.aka).replace("AKA", "").strip()
        return str(meta.title)

    @staticmethod
    def _episode_code(meta: Meta) -> str:
        return f"{meta.season}{meta.episode}" if meta.category == "TV" else ""

    @staticmethod
    def _normalized_audio(value: Any) -> str:
        return (
            str(value)
            .replace("DD+ ", "DDP")
            .replace("DD ", "DD")
            .replace("AAC ", "AAC")
            .replace("FLAC ", "FLAC")
        )

    @staticmethod
    def _compact_parts(parts: tuple[str, ...]) -> str:
        return " ".join(part for part in parts if part)

    def _apply_video_audio_tag(self, name: str, meta: Meta) -> str:
        if meta.is_disc:
            return name
        audio_tag = self._audio_tag(meta.audio_languages)
        if not audio_tag:
            return name
        return self._insert_audio_tag(name, audio_tag, meta)

    @classmethod
    def _audio_tag(cls, audio_languages: Any) -> str:
        languages = cls._unique_audio_languages(audio_languages)
        if not languages or not cls._has_portuguese(languages):
            return ""
        return cls._audio_count_tag(len(languages))

    @staticmethod
    def _unique_audio_languages(audio_languages: Any) -> set[Any]:
        if not audio_languages:
            return set()
        try:
            return set(audio_languages)
        except TypeError:
            return set()

    @staticmethod
    def _has_portuguese(languages: set[Any]) -> bool:
        return any(
            str(language).casefold() in {"portuguese", "português"}
            for language in languages
        )

    @staticmethod
    def _audio_count_tag(count: int) -> str:
        if count >= 3:
            return " MULTI"
        return " DUAL" if count == 2 else ""

    @classmethod
    def _insert_audio_tag(cls, name: str, audio_tag: str, meta: Meta) -> str:
        if "-" not in name:
            return f"{name}{audio_tag}"
        prefix, suffix = name.rsplit("-", 1)
        suffix = cls._strip_leading_audio_mark(suffix)
        if cls._group_already_marked(suffix):
            return name
        return cls._insert_marked_group(prefix, suffix, audio_tag, meta)

    @staticmethod
    def _strip_leading_audio_mark(suffix: str) -> str:
        stripped = re.sub(
            r"(?i)^(?:dual|multi)(?:[.\s-]+|$)", "", suffix, count=1
        )
        return stripped or suffix

    @staticmethod
    def _group_already_marked(suffix: str) -> bool:
        return _GROUP_ALREADY_MARKED_RE.search(suffix) is not None

    @classmethod
    def _insert_marked_group(
        cls, prefix: str, suffix: str, audio_tag: str, meta: Meta
    ) -> str:
        original_group = cls._source_group(meta)
        configured_group = str(meta.tag or "").lstrip("-")
        if cls._use_source_group(original_group, configured_group):
            return f"{prefix}-{original_group}{audio_tag}-{suffix}"
        return f"{prefix}{audio_tag}-{suffix}"

    @staticmethod
    def _use_source_group(original_group: str, configured_group: str) -> bool:
        if not original_group:
            return False
        return original_group.casefold() != configured_group.casefold()

    @staticmethod
    def _source_group(meta: Meta) -> str:
        for source in (meta.path, meta.uuid):
            match = re.search(
                r"-([^.-]+)\.(?:DUAL|MULTI)(?=-|\.|$)",
                str(source or ""),
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _ensure_nogroup(cls, name: str, tag: str | None) -> str:
        if cls._valid_group_tag(tag):
            return name
        return f"{cls._strip_invalid_group_tags(name)}-NoGroup"

    @staticmethod
    def _valid_group_tag(tag: str | None) -> bool:
        if not tag:
            return False
        lowered = str(tag).casefold()
        return not any(
            value in lowered
            for value in ("nogrp", "nogroup", "unknown", "-unk-")
        )

    @staticmethod
    def _strip_invalid_group_tags(name: str) -> str:
        result = name
        for value in ("nogrp", "nogroup", "unknown", "-unk-"):
            result = re.sub(f"-{value}", "", result, flags=re.IGNORECASE)
        return result

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_additional_checks(self, meta: Meta) -> bool:
        if self._missing_audiobook_narrator(meta):
            logger.info(
                f"{self.tracker}: [bold red]Narrator is required for audiobooks. Skipping upload...[/bold red]"
            )
            return False
        reason = self._payload_skip_reason(meta)
        if reason:
            logger.info(
                f"{self.tracker}: [bold red]{reason} Skipping upload...[/bold red]"
            )
            return False
        if meta.category not in {"MOVIE", "TV"}:
            return True
        return await self._video_additional_checks(meta)

    def _payload_skip_reason(self, meta: Meta) -> str:
        category = str(meta.category or "").upper()
        if CapybaraBR._compressed_media(meta) and category not in {
            "PROGRAMS",
            "PROGRAM",
            "GAME",
        }:
            return "compressed archives are only allowed as cracks for programs and games"
        extra = CapybaraBR._forbidden_extra_file(meta)
        if extra:
            return f"the torrent contains a file that is not allowed: {extra}"
        if category not in {"MOVIE", "TV"}:
            return ""
        if not meta.is_disc:
            reason = CapybaraBR._multipart_skip_reason(meta)
            if reason:
                return reason
            reason = CapybaraBR._extras_skip_reason(meta)
            if reason:
                return reason
        return CapybaraBR._technical_skip_reason(meta)

    @staticmethod
    def _missing_audiobook_narrator(meta: Meta) -> bool:
        return (
            meta.category == "BOOK"
            and bool(meta.audiobook)
            and not bool(meta.narrator)
        )

    async def _video_additional_checks(self, meta: Meta) -> bool:
        if not self._screenshot_allowed(meta):
            return False
        if not await self._dual_release_allowed(meta):
            return False
        reason = self._video_release_skip_reason(meta)
        if reason:
            logger.info(
                f"{self.tracker}: [bold red]{reason} Skipping upload...[/bold red]"
            )
            return False
        return await self.common.check_portuguese_video_requirements(
            meta, self.tracker
        )

    async def _dual_release_allowed(self, meta: Meta) -> bool:
        """Reject a mono release when the tracker already holds a DUAL one.

        CapybaraBR staff require new releases of a title to carry DUAL audio
        once any DUAL release exists, regardless of quality. The check is
        best effort: a missing API key or a failing search never blocks.
        """
        if meta.dual_audio:
            return True
        if not str(getattr(self, "api_key", "") or "").strip():
            logger.debug(
                f"{self.tracker}: api_key is not configured; skipping the DUAL release check"
            )
            return True
        try:
            existing = await self.search_existing(meta)
        except Exception as error:
            logger.warning(
                f"[yellow]{self.tracker}: could not check for an existing DUAL release: {error}[/yellow]"
            )
            return True
        for result in existing:
            name = str(result.get("name") or "")
            if not _GROUP_ALREADY_MARKED_RE.search(name):
                continue
            logger.info(
                f"{self.tracker}: [bold red]a DUAL release already exists on the tracker, "
                "so new releases must be DUAL. Skipping upload...[/bold red]"
            )
            return False
        return True

    def _screenshot_allowed(self, meta: Meta) -> bool:
        """Enforce the tracker minimum of 3 screenshots for movies and shows.

        The count is unknown when ``meta.screens`` is unset, so the check
        fails open instead of blocking on missing data.
        """
        if meta.category not in {"MOVIE", "TV"} or meta.screens is None:
            return True
        count = CapybaraBR._screen_count(meta.screens)
        if count >= _SCREENSHOTS_MINIMUM:
            return True
        logger.info(
            f"{self.tracker}: [bold red]at least {_SCREENSHOTS_MINIMUM} screenshots are required "
            f"for movies and shows (found {count}). Skipping upload...[/bold red]"
        )
        return False

    @staticmethod
    def _screen_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    def _video_release_skip_reason(self, meta: Meta) -> str:
        release_type = str(meta.type or "").casefold()
        if release_type == "encode" and not meta.has_encode_settings:
            return "'Encode settings' field in the MediaInfo is required for encodes."
        if self._remux_requires_bdinfo(meta, release_type):
            return (
                "BDInfo is required for Blu-ray remuxes that include 'Encode settings' field in the MediaInfo. "
                "You can add BDInfo to the description using -df (path/to/file.txt) or -pb (Pastebin link)."
            )
        return self._filename_skip_reason(meta)

    @staticmethod
    def _filename_skip_reason(meta: Meta) -> str:
        if CapybaraBR._compressed_media(meta):
            return "compressed media archives are not allowed for movies or shows; extract the release first"
        orphan = CapybaraBR._orphan_sidecar_name(meta)
        if orphan:
            return f"sidecar subtitle '{orphan}' has no matching video file in the payload"
        if CapybaraBR._video_missing_audio_name(meta):
            return "the video filename is missing the audio codec and channels"
        if CapybaraBR._sidecar_duplicates_embedded_subs(meta):
            return "the video already has embedded subtitles, so sidecar subtitle files cannot be included"
        return ""

    @staticmethod
    def _video_missing_audio_name(meta: Meta) -> bool:
        names = CapybaraBR._video_filenames(meta)
        if not names:
            return False
        return any(not CapybaraBR._filename_has_audio(name) for name in names)

    @staticmethod
    def _video_filenames(meta: Meta) -> list[str]:
        return [
            name
            for name in CapybaraBR._content_names(meta.filelist)
            if CapybaraBR._has_suffix(name, _VIDEO_SUFFIXES)
        ]

    @staticmethod
    def _sidecar_duplicates_embedded_subs(meta: Meta) -> bool:
        if not CapybaraBR._sidecar_names(meta):
            return False
        return CapybaraBR._has_embedded_text(meta)

    @staticmethod
    def _sidecar_names(meta: Meta) -> list[str]:
        names = CapybaraBR._content_names(meta.subtitle_files)
        names.extend(CapybaraBR._content_names(meta.filelist))
        return [
            name
            for name in names
            if CapybaraBR._has_suffix(name, _SIDECAR_SUFFIXES)
        ]

    @staticmethod
    def _content_names(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _has_suffix(name: str, suffixes: tuple[str, ...]) -> bool:
        base = name.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        return base.endswith(suffixes)

    @staticmethod
    def _filename_has_audio(name: str) -> bool:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        return _AUDIO_IN_FILENAME_RE.search(stem) is not None

    @staticmethod
    def _forbidden_extra_file(meta: Meta) -> str:
        suffixes = (".nfo", ".sample", ".txt", ".exe", ".msi", ".bat", ".cmd", ".sh", ".dmg")
        for name in CapybaraBR._content_names(meta.filelist):
            lower = name.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if lower.endswith(suffixes) or ".sample." in lower or lower.startswith("sample."):
                return name
        return ""

    @staticmethod
    def _multipart_skip_reason(meta: Meta) -> str:
        pattern = re.compile(
            r"(?i)(?:^|[.\s_-])(?:part\s*\d{1,2}|cd\s*\d{1,2}|disc\s*\d{1,2}|disk\s*\d{1,2})(?:$|[.\s_-])"
        )
        for name in CapybaraBR._content_names(meta.filelist):
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if pattern.search(Path(base).stem):
                return "releases cannot be split into parts; upload a single torrent"
        return ""

    @staticmethod
    def _extras_skip_reason(meta: Meta) -> str:
        pattern = re.compile(
            r"(?i)(?:^|[.\s_-])(?:extras?|bonus|featurettes?|behind[.\s_-]*the[.\s_-]*scenes|deleted[.\s_-]*scenes)(?:$|[.\s_-])"
        )
        for name in CapybaraBR._content_names(meta.filelist):
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if "sample" in base.casefold():
                continue
            if pattern.search(Path(base).stem):
                return "bonus discs and extras must be uploaded separately from the main movie or show"
        return ""

    @staticmethod
    def _technical_skip_reason(meta: Meta) -> str:
        first_file = next(iter(CapybaraBR._content_names(meta.filelist)), "")
        values = " ".join(
            str(value or "")
            for value in (
                meta.name,
                meta.type,
                meta.source,
                meta.video_encode,
                first_file,
            )
        )
        if re.search(r"(?i)(?:^|[.\s_-])(?:cam|hdcam|hdts|dcp)(?:$|[.\s_-])", values):
            return "CAM, TS, HDTS and DCP sources are not allowed"
        if re.search(r"(?i)(?:nvenc|quicksync|qsv|amf|videotoolbox)", values):
            return "GPU encodes are not allowed; use a CPU encode"
        codec = str(meta.video_codec or meta.video_encode or "").casefold()
        if "av1" in codec:
            return "AV1 is not accepted"
        disc = str(meta.is_disc or "").upper()
        if disc in {"BDMV", "VIDEO_TS"}:
            return "authored Blu-ray and DVD structures are not allowed"
        bitrate = meta.video_bitrate
        if isinstance(bitrate, int) and bitrate > 0:
            minimum = CapybaraBR._minimum_video_bitrate(meta)
            if minimum and bitrate < minimum:
                return (
                    f"video bitrate {bitrate} kbps is below the {minimum} kbps minimum; "
                    "release it as a BRRip"
                )
        return ""

    @staticmethod
    def _minimum_video_bitrate(meta: Meta) -> int:
        animation = bool(meta.anime)
        resolution = str(meta.resolution or "").casefold()
        if resolution == "720p":
            return 3500 if animation else 5000
        if resolution == "1080p":
            return 6000 if animation else 8000
        return 0

    @staticmethod
    def _compressed_media(meta: Meta) -> bool:
        return any(
            _COMPRESSED_RE.search(name.replace("\\", "/").rsplit("/", 1)[-1])
            for name in CapybaraBR._content_names(meta.filelist)
        )

    @staticmethod
    def _orphan_sidecar_name(meta: Meta) -> str:
        videos = {
            CapybaraBR._stem(name)
            for name in CapybaraBR._video_filenames(meta)
        }
        if not videos:
            return ""
        for name in CapybaraBR._sidecar_names(meta):
            stem = CapybaraBR._stem(name)
            if stem in videos:
                continue
            if _SIDECAR_LANGUAGE_RE.sub("", stem) in videos:
                continue
            return name
        return ""

    @staticmethod
    def _stem(name: str) -> str:
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        return base.rsplit(".", 1)[0].casefold()

    @staticmethod
    def _has_embedded_text(meta: Meta) -> bool:
        return any(
            track.get("@type") == "Text"
            for track in CapybaraBR._mediainfo_tracks(meta)
        )

    @staticmethod
    def _mediainfo_tracks(meta: Meta) -> list[dict[str, Any]]:
        tracks = CapybaraBR._mediainfo_media(meta).get("track", [])
        if not isinstance(tracks, list):
            return []
        return [track for track in tracks if isinstance(track, dict)]

    @staticmethod
    def _mediainfo_media(meta: Meta) -> dict[str, Any]:
        mediainfo = meta.mediainfo if isinstance(meta.mediainfo, dict) else {}
        media = mediainfo.get("media", {})
        return media if isinstance(media, dict) else {}

    def _remux_requires_bdinfo(self, meta: Meta, release_type: str) -> bool:
        if release_type != "remux" or str(
            meta.source or ""
        ).casefold() not in {"bluray", "blu-ray"}:
            return False
        if not meta.has_encode_settings:
            return False
        return not self.common.has_bdinfo(self._description_text(meta))

    @staticmethod
    def _description_text(meta: Meta) -> str:
        return f"{meta.description}\n{meta.description_link_content}\n{meta.description_file_content}"
