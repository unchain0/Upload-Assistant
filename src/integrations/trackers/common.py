# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import aiofiles
import bencodepy
import cli_ui
import click
import httpx
import langcodes
from langcodes import tag_parser
from torf import Torrent

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.media.media_info_export import export_info
from src.integrations.observability.runtime_support import (
    console,
    logger,
    prompt_in_thread,
)
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.usenet.creator import verify_nzb_has_password


@dataclass(frozen=True)
class _LanguageRequirementState:
    required: list[str]
    audio: list[str]
    subtitles: list[str]
    required_expanded: set[str]
    audio_expanded: set[str]
    subtitle_expanded: set[str]
    original_display: str | None
    original_ok: bool


class Common:
    TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {
            "returning series",
            "in production",
            "ongoing",
            "planned",
            "pilot",
            "in development",
        }
    )
    PORTUGUESE_SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(
        {".ass", ".ssa", ".srt", ".sub", ".vtt"}
    )
    PORTUGUESE_SUBTITLE_WORDS: frozenset[str] = frozenset(
        {
            "agora",
            "aqui",
            "bem",
            "como",
            "com",
            "entao",
            "essa",
            "esse",
            "esta",
            "estao",
            "isso",
            "muito",
            "nao",
            "obrigada",
            "obrigado",
            "onde",
            "para",
            "porque",
            "posso",
            "pode",
            "quando",
            "que",
            "senhor",
            "senhora",
            "sua",
            "suas",
            "seu",
            "seus",
            "tambem",
            "tenho",
            "temos",
            "uma",
            "voce",
            "vamos",
        }
    )
    PORTUGUESE_DESCRIPTION_WORDS: frozenset[str] = frozenset(
        {
            "ainda",
            "agora",
            "ano",
            "ao",
            "aos",
            "aquele",
            "aqueles",
            "aquela",
            "aquelas",
            "ate",
            "bem",
            "essa",
            "esse",
            "esta",
            "estas",
            "estava",
            "estao",
            "este",
            "estes",
            "estou",
            "eu",
            "exemplo",
            "foi",
            "fiz",
            "ficou",
            "hoje",
            "isso",
            "mais",
            "muito",
            "nao",
            "nada",
            "onde",
            "para",
            "porque",
            "qual",
            "quando",
            "quem",
            "segundo",
            "sem",
            "tambem",
            "temos",
            "voce",
            "vida",
            "vou",
            "varios",
            "vao",
        }
    )
    PORTUGUESE_DESCRIPTION_MARKERS = re.compile(r"[ãõÃÕ]")
    LANGUAGE_EQUIVALENCE_GROUPS: tuple[set[str], ...] = (
        {
            "chinese",
            "mandarin",
            "zh",
            "zho",
            "chi",
            "cmn",
            "chinese simplified",
            "chinese traditional",
            "zh hans",
            "zh hant",
        },
        {
            "english",
            "eng",
            "en",
            "en us",
            "en gb",
            "english cc",
            "english sdh",
            "english forced",
        },
        {
            "french",
            "fra",
            "fre",
            "fr",
            "francais",
            "français",
            "french canada",
            "french canadian",
        },
        {
            "portuguese",
            "por",
            "pt",
            "pt pt",
            "brazilian portuguese",
            "portuguese brazil",
            "portuguese br",
            "pt br",
            "brazilian",
        },
        {
            "spanish",
            "spa",
            "es",
            "es es",
            "spanish latin america",
            "latin american spanish",
            "es 419",
            "es mx",
            "castilian",
            "espanol",
            "español",
            "latino",
        },
    )

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.parser = self.MediaInfoParser()

    def _normalize_language_token(self, language: str) -> str:
        normalized = unicodedata.normalize("NFKD", language)
        normalized = "".join(
            char for char in normalized if not unicodedata.combining(char)
        )
        normalized = normalized.casefold()
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _build_language_alias_lookup(self) -> dict[str, set[str]]:
        alias_lookup: dict[str, set[str]] = {}
        for group in self.LANGUAGE_EQUIVALENCE_GROUPS:
            normalized_group = {
                self._normalize_language_token(value)
                for value in group
                if value
            }
            for value in normalized_group:
                alias_lookup[value] = set(normalized_group)
        return alias_lookup

    def _coerce_language_values(self, values: Any) -> list[str]:
        if isinstance(values, str):
            return [values]
        if isinstance(values, list):
            return [
                value
                for value in cast(list[Any], values)
                if isinstance(value, str)
            ]
        return []

    def _base_language_candidates(
        self, language: str, normalized: str
    ) -> set[str]:
        candidates = {normalized}
        first_token = normalized.split(maxsplit=1)[0]
        if first_token:
            candidates.add(first_token)
        first_chunk = language.split(",", 1)[0].strip()
        if first_chunk and first_chunk != language:
            chunk = self._normalize_language_token(first_chunk)
            if chunk:
                candidates.add(chunk)
        return candidates

    def _parsed_language_candidates(self, parse_input: str) -> set[str]:
        try:
            parsed = langcodes.Language.get(parse_input)
            values = (
                parsed.display_name(),
                parsed.language_name(),
                parsed.language,
            )
        except (
            tag_parser.LanguageTagError,
            LookupError,
            AttributeError,
            ValueError,
        ):
            return set()
        return {
            normalized
            for value in values
            if value
            if (normalized := self._normalize_language_token(value))
        }

    @staticmethod
    def _with_language_aliases(
        candidates: set[str], alias_lookup: dict[str, set[str]]
    ) -> set[str]:
        expanded = set(candidates)
        for candidate in candidates:
            expanded.update(alias_lookup.get(candidate, set()))
        return expanded

    def _expand_language_candidates(
        self, language: str, alias_lookup: dict[str, set[str]]
    ) -> set[str]:
        normalized = self._normalize_language_token(language)
        if not normalized:
            return set()
        candidates = self._base_language_candidates(language, normalized)
        for parse_input in {language.strip(), normalized.replace(" ", "-")}:
            candidates.update(self._parsed_language_candidates(parse_input))
        return self._with_language_aliases(candidates, alias_lookup)

    def _expand_language_list(
        self, values: list[str], alias_lookup: dict[str, set[str]]
    ) -> set[str]:
        expanded: set[str] = set()
        for value in values:
            expanded.update(
                self._expand_language_candidates(value, alias_lookup)
            )
        return expanded

    @staticmethod
    def extract_tv_seasons(filelist: list[Any]) -> set[int]:
        seasons: set[int] = set()
        season_pattern = re.compile(
            r"(?<![A-Za-z0-9])[sS](\d{1,3})(?:[eE]\d{1,3}(?:[eE]\d{1,3})?)?"
        )
        for item in filelist:
            seasons.update(
                int(match) for match in season_pattern.findall(str(item))
            )
        return seasons

    @staticmethod
    def count_tv_episodes(filelist: list[Any]) -> int:
        episodes: set[tuple[int, int]] = set()
        episode_pattern = re.compile(
            r"(?<![A-Za-z0-9])[sS](\d{1,3})[eE](\d{1,3})((?:[-_. ]?[eE]\d{1,3})*)"
        )
        for item in filelist:
            for (
                season,
                first_episode,
                remaining_episodes,
            ) in episode_pattern.findall(str(item)):
                for episode in (
                    first_episode,
                    *re.findall(r"[eE](\d{1,3})", remaining_episodes),
                ):
                    episodes.add((int(season), int(episode)))
        return len(episodes)

    @staticmethod
    def _status_contains_any(
        status_text: str, values: set[str] | frozenset[str]
    ) -> bool:
        return any(value in status_text for value in values)

    @staticmethod
    def _series_status_value(
        raw_status: Any,
        ended_values: set[str] | frozenset[str],
        ongoing_values: set[str] | frozenset[str],
    ) -> bool | None:
        status_text = str(raw_status or "").casefold().strip()
        if Common._status_contains_any(status_text, ended_values):
            return True
        if Common._status_contains_any(status_text, ongoing_values):
            return False
        return None

    @staticmethod
    def _series_statuses(meta: Meta) -> tuple[Any, Any]:
        imdb_status = (
            meta.imdb_info.get("status", "")
            if isinstance(meta.imdb_info, dict)
            else ""
        )
        return getattr(meta, "series_status", ""), imdb_status

    @staticmethod
    def is_tv_series_ended(
        meta: Meta,
        ended_values: set[str] | frozenset[str],
        ongoing_values: set[str] | frozenset[str],
    ) -> bool | None:
        for raw_status in Common._series_statuses(meta):
            resolved = Common._series_status_value(
                raw_status, ended_values, ongoing_values
            )
            if resolved is not None:
                return resolved
        return None

    @classmethod
    def is_completed_tv_episode(cls, meta: Meta) -> bool:
        return (
            meta.category == "TV"
            and not meta.tv_pack
            and int(meta.episode_int or 0) > 0
            and cls.is_tv_series_ended(
                meta, cls.TV_ENDED_STATUSES, cls.TV_ONGOING_STATUSES
            )
            is True
        )

    @staticmethod
    def _read_subtitle_text(path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeError:
                continue
            except OSError:
                return ""
        return ""

    def _portuguese_subtitle_aliases(self) -> set[str]:
        aliases = {
            "brazilian",
            "brazilian portuguese",
            "por",
            "portuguese",
            "portugues",
            "pt",
            "pt br",
            "ptbr",
            "pt brasil",
        }
        return {self._normalize_language_token(alias) for alias in aliases}

    def _subtitle_filename_is_portuguese(
        self, path: Path, aliases: set[str]
    ) -> bool:
        tokens = self._normalize_language_token(path.stem).split()
        while tokens and tokens[-1] in {"forced", "sdh"}:
            tokens.pop()
        filename = " ".join(tokens)
        return any(
            filename == alias or filename.endswith(f" {alias}")
            for alias in aliases
        )

    def _portuguese_subtitle_text_paths(
        self, meta: Meta, aliases: set[str]
    ) -> tuple[bool, list[Path]]:
        text_paths: list[Path] = []
        for subtitle_file in meta.subtitle_files or []:
            path = Path(str(subtitle_file))
            if self._subtitle_filename_is_portuguese(path, aliases):
                return True, []
            if path.suffix.casefold() in self.PORTUGUESE_SUBTITLE_EXTENSIONS:
                text_paths.append(path)
        return False, text_paths

    async def _subtitle_text_is_portuguese(self, path: Path) -> bool:
        text = await asyncio.to_thread(self._read_subtitle_text, path)
        words = set(
            re.findall(r"[a-z]+", self._normalize_language_token(text))
        )
        return len(words & self.PORTUGUESE_SUBTITLE_WORDS) >= 3

    async def has_portuguese_external_subtitle(self, meta: Meta) -> bool:
        """Check external subtitle filenames and textual content for Portuguese."""
        aliases = self._portuguese_subtitle_aliases()
        filename_match, text_paths = self._portuguese_subtitle_text_paths(
            meta, aliases
        )
        if filename_match:
            return True
        for path in text_paths:
            if await self._subtitle_text_is_portuguese(path):
                return True
        return False

    async def check_portuguese_video_requirements(
        self, meta: Meta, tracker: str
    ) -> bool:
        if await self.has_portuguese_external_subtitle(meta):
            return True

        subtitles = await self.check_language_requirements(
            meta,
            tracker,
            languages_to_check=[
                "portuguese",
                "português",
                "por",
                "pt",
                "pt-br",
                "pt br",
                "brazilian portuguese",
            ],
            check_audio=True,
            check_subtitle=True,
            prompt_on_failure=False,
        )
        if subtitles:
            return True
        if meta.unattended:
            return bool(meta.unattended_confirm)
        return await self.prompt_user_for_confirmation(
            f"{tracker}: No Portuguese audio or subtitles found. Do you want to proceed with the upload?"
        )

    def _strip_bbcode_and_markup(self, text: str) -> str:
        without_bbcode = re.sub(r"\[[^\]]+\]", " ", text)
        return re.sub(r"<[^>]+>", " ", without_bbcode)

    def is_portuguese_description(self, description: str) -> bool:
        description = self._strip_bbcode_and_markup(description or "")
        if not description.strip():
            return False

        if self.PORTUGUESE_DESCRIPTION_MARKERS.search(description):
            return True

        normalized = self._normalize_language_token(description)
        words = set(re.findall(r"[a-z]+", normalized))
        return len(words & self.PORTUGUESE_DESCRIPTION_WORDS) >= 3

    async def check_portuguese_description_requirements(
        self, description: str, tracker: str, meta: Meta
    ) -> bool:
        candidates = self._portuguese_description_candidates(description, meta)
        if any(
            self.is_portuguese_description(candidate)
            for candidate in candidates
        ):
            return True

        if meta.unattended:
            return bool(meta.unattended_confirm)

        return await self.prompt_user_for_confirmation(
            f"{tracker}: Description does not appear to be in Portuguese. Do you want to proceed with the upload?"
        )

    @classmethod
    def _portuguese_description_candidates(
        cls, description: str, meta: Meta
    ) -> tuple[str, ...]:
        localized = cls._localized_ptbr_overview(meta)
        if localized:
            return localized, description
        return (description,)

    @staticmethod
    def _localized_ptbr_overview(meta: Meta) -> str:
        localized_raw = getattr(meta, "tmdb_localized_data", {})
        if not isinstance(localized_raw, dict):
            return ""
        localized = cast(dict[str, Any], localized_raw)
        ptbr_raw = localized.get("pt-BR")
        if not isinstance(ptbr_raw, dict):
            return ""
        ptbr = cast(dict[str, Any], ptbr_raw)
        main_raw = ptbr.get("main")
        if not isinstance(main_raw, dict):
            return ""
        main = cast(dict[str, Any], main_raw)
        overview = main.get("overview")
        return overview.strip() if isinstance(overview, str) else ""

    def _format_language_for_display(self, language: str) -> str:
        if not language:
            return ""
        try:
            parsed_lang = langcodes.Language.get(language)
            display_name = parsed_lang.display_name()
            return display_name.lower() if display_name else language.lower()
        except (
            tag_parser.LanguageTagError,
            LookupError,
            AttributeError,
            ValueError,
        ):
            return language.lower()

    async def path_exists(self, path: str) -> bool:
        """Async wrapper for os.path.exists"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, os.path.exists, path)

    async def remove_file(self, path: str) -> None:
        """Async wrapper for os.remove"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, os.remove, path)

    async def makedirs(self, path: str, exist_ok: bool = True) -> None:
        """Async wrapper for os.makedirs"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda p, e: Path(p).mkdir(parents=True, exist_ok=e),
            path,
            exist_ok,
        )

    async def get_torrent_filename(
        self, meta: Meta, tracker_config: Any
    ) -> str:
        """
        Decide which torrent filename/prefix to use (BASE or BASE_SUBS) depending on
        the allow_ext_subtitles setting and presence of the subtitles torrent.
        """
        torrent_filename = "BASE"
        allow_ext_subtitles = False
        if isinstance(tracker_config, dict):
            config = cast(dict[str, Any], tracker_config)
            allow_ext_subtitles = bool(
                config.get("allow_ext_subtitles", False)
            )
        if allow_ext_subtitles:
            subs_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/BASE_SUBS.torrent"
            if await self.path_exists(subs_path):
                torrent_filename = "BASE_SUBS"
        return torrent_filename

    @staticmethod
    def _allowed_torrent_metainfo_keys() -> frozenset[str]:
        return frozenset(
            {
                "announce",
                "comment",
                "creation date",
                "created by",
                "encoding",
                "info",
                "imdb",
                "tmdb",
                "tvdb",
                "tvmaze",
                "mal",
                "douban",
                "igdb",
                "asin",
                "isbn",
            }
        )

    @staticmethod
    def _torrent_metainfo(torrent: Any) -> dict[str, Any]:
        return cast(dict[str, Any], torrent.metainfo)

    @classmethod
    def _sanitize_torrent_metainfo(cls, torrent: Any) -> dict[str, Any]:
        metainfo = cls._torrent_metainfo(torrent)
        allowed = cls._allowed_torrent_metainfo_keys()
        for key in list(metainfo):
            if key not in allowed:
                metainfo.pop(key, None)
        return metainfo

    @staticmethod
    def _torrent_info(metainfo: dict[str, Any]) -> dict[str, Any]:
        raw = metainfo.get("info")
        if isinstance(raw, dict):
            return cast(dict[str, Any], raw)
        info: dict[str, Any] = {}
        metainfo["info"] = info
        return info

    @staticmethod
    def _apply_public_torrent_policy(
        metainfo: dict[str, Any], public_trackers: list[str] | None
    ) -> None:
        Common._torrent_info(metainfo).pop("private", None)
        if public_trackers:
            metainfo["announce"] = public_trackers[0]
            metainfo["announce-list"] = [
                [tracker] for tracker in public_trackers
            ]
            return
        metainfo.pop("announce", None)
        metainfo.pop("announce-list", None)

    def _private_announce_url(self, tracker: str, announce_url: str) -> str:
        if announce_url:
            return announce_url
        trackers = self.config.get("TRACKERS", {})
        tracker_map = (
            cast(dict[str, Any], trackers)
            if isinstance(trackers, dict)
            else {}
        )
        raw_config = tracker_map.get(tracker, {})
        config = (
            cast(dict[str, Any], raw_config)
            if isinstance(raw_config, dict)
            else {}
        )
        raw_announce = config.get("announce_url")
        return (
            str(raw_announce).strip()
            if raw_announce
            else "https://fake.tracker"
        )

    def _apply_private_torrent_policy(
        self, metainfo: dict[str, Any], tracker: str, announce_url: str
    ) -> None:
        metainfo["announce"] = self._private_announce_url(
            tracker, announce_url
        )
        self._torrent_info(metainfo)["private"] = True

    @staticmethod
    def _update_torrent_created_by(
        metainfo: dict[str, Any], meta: Meta
    ) -> None:
        created_by = metainfo.get("created by")
        if (
            not isinstance(created_by, str)
            or "mkbrr" not in created_by.lower()
        ):
            return
        metainfo["created by"] = (
            f"{created_by} using {meta.ua_name} {meta.current_version}"
        )

    @staticmethod
    def _entropy_bits(value: Any) -> int | None:
        try:
            parsed = int(value)
        except TypeError, ValueError:
            return None
        return parsed if parsed in {32, 64} else None

    @classmethod
    def _apply_torrent_entropy(
        cls, metainfo: dict[str, Any], value: Any
    ) -> None:
        bits = cls._entropy_bits(value)
        if bits is None:
            return
        cls._torrent_info(metainfo)["entropy"] = secrets.randbelow(2**bits)

    async def _load_upload_torrent(self, path: str) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: Torrent.read(path))

    async def _write_upload_torrent(
        self, torrent: Any, meta: Meta, tracker: str
    ) -> None:
        out_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}].torrent"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: Torrent.copy(torrent).write(out_path, overwrite=True),
        )

    async def create_torrent_for_upload(
        self,
        meta: Meta,
        tracker: str,
        source_flag: str,
        torrent_filename: str = "BASE",
        announce_url: str = "",
        is_public: bool = False,
        public_trackers: list[str] | None = None,
    ) -> None:
        trackers = self.config.get("TRACKERS", {})
        tracker_map = (
            cast(dict[str, Any], trackers)
            if isinstance(trackers, dict)
            else {}
        )
        tracker_cfg = tracker_map.get(tracker, {})
        if torrent_filename == "BASE":
            torrent_filename = await self.get_torrent_filename(
                meta, tracker_cfg
            )
        path = (
            f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/"
            f"{torrent_filename}.torrent"
        )
        if not await self.path_exists(path):
            return
        torrent = await self._load_upload_torrent(path)
        metainfo = self._sanitize_torrent_metainfo(torrent)
        if is_public:
            self._apply_public_torrent_policy(metainfo, public_trackers)
        else:
            self._apply_private_torrent_policy(metainfo, tracker, announce_url)
        self._torrent_info(metainfo)["source"] = source_flag
        self._update_torrent_created_by(metainfo, meta)
        metainfo["comment"] = ""
        self._apply_torrent_entropy(metainfo, meta.entropy)
        await self._write_upload_torrent(torrent, meta, tracker)

    @staticmethod
    def _tracker_download_path(meta: Meta, tracker: str, cross: bool) -> str:
        suffix = (
            f"[{tracker}_cross].torrent" if cross else f"[{tracker}].torrent"
        )
        return f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/{suffix}"

    async def _tracker_cookie_jar(
        self, meta: Meta, tracker: str, use_cookie_auth: bool
    ) -> Any:
        if not use_cookie_auth:
            return None
        with contextlib.suppress(Exception):
            from src.integrations.trackers.cookie_auth import CookieValidator

            validator = CookieValidator(self.config)
            return await validator.load_session_cookies(meta, tracker)
        return None

    @staticmethod
    def _normalized_tracker_hosts(allowed_hosts: tuple[str, ...]) -> set[str]:
        return {host.casefold() for host in allowed_hosts}

    @staticmethod
    def _validate_tracker_download_url(
        current_url: str, normalized_hosts: set[str]
    ) -> None:
        if not normalized_hosts:
            return
        parsed_url = urlsplit(current_url)
        valid = all(
            (
                parsed_url.scheme == "https",
                (parsed_url.hostname or "").casefold() in normalized_hosts,
                parsed_url.port in (None, 443),
                parsed_url.username is None,
                parsed_url.password is None,
            )
        )
        if not valid:
            raise ValueError(
                "Tracker download URL is outside the allowed HTTPS hosts"
            )

    @staticmethod
    def _redirect_download_url(current_url: str, response: Any) -> str:
        location = response.headers.get("location")
        if not location:
            raise ValueError("Tracker download redirect is missing a location")
        return urljoin(current_url, location)

    @staticmethod
    def _validate_tracker_content_length(
        response: Any, max_size: int | None
    ) -> None:
        if max_size is None:
            return
        content_length = str(response.headers.get("content-length", ""))
        if content_length.isdigit() and int(content_length) > max_size:
            raise ValueError(
                "Tracker torrent download exceeds the configured size limit"
            )

    @staticmethod
    def _validate_downloaded_size(
        downloaded: int, max_size: int | None
    ) -> None:
        if max_size is not None and downloaded > max_size:
            raise ValueError(
                "Tracker torrent download exceeds the configured size limit"
            )

    async def _write_tracker_download(
        self, response: Any, path: str, max_size: int | None
    ) -> None:
        downloaded = 0
        async with aiofiles.open(path, "wb") as torrent_file:
            async for chunk in response.aiter_bytes():
                downloaded += len(chunk)
                self._validate_downloaded_size(downloaded, max_size)
                await torrent_file.write(chunk)

    async def _consume_tracker_response(
        self,
        response: Any,
        current_url: str,
        path: str,
        normalized_hosts: set[str],
        max_size: int | None,
    ) -> tuple[bool, str]:
        if normalized_hosts and response.is_redirect:
            return False, self._redirect_download_url(current_url, response)
        response.raise_for_status()
        self._validate_tracker_content_length(response, max_size)
        await self._write_tracker_download(response, path, max_size)
        return True, current_url

    async def _stream_tracker_download(
        self,
        session: Any,
        downurl: str,
        path: str,
        normalized_hosts: set[str],
        max_size: int | None,
    ) -> None:
        current_url = downurl
        for _redirect in range(6):
            self._validate_tracker_download_url(current_url, normalized_hosts)
            async with session.stream("GET", current_url) as response:
                complete, current_url = await self._consume_tracker_response(
                    response,
                    current_url,
                    path,
                    normalized_hosts,
                    max_size,
                )
                if complete:
                    return
        raise ValueError(
            "Tracker torrent download exceeded the redirect limit"
        )

    async def _tracker_download_result(
        self,
        meta: Meta,
        tracker: str,
        path: str,
        hash_is_id: bool,
        cross: bool,
    ) -> str | None:
        if cross:
            return None
        if hash_is_id:
            return await self.get_torrent_hash(meta, tracker)
        return path

    @staticmethod
    def _cleanup_failed_tracker_download(
        path: str, allowed_hosts: tuple[str, ...], max_size: int | None
    ) -> None:
        if allowed_hosts or max_size is not None:
            Path(path).unlink(missing_ok=True)

    async def download_tracker_torrent(
        self,
        meta: Meta,
        tracker: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        downurl: str = "",
        hash_is_id: bool = False,
        cross: bool = False,
        allowed_hosts: tuple[str, ...] = (),
        max_size: int | None = None,
        use_cookie_auth: bool = False,
    ) -> str | None:
        if not downurl:
            return None
        path = self._tracker_download_path(meta, tracker, cross)
        try:
            cookie_jar = await self._tracker_cookie_jar(
                meta, tracker, use_cookie_auth
            )
            normalized_hosts = self._normalized_tracker_hosts(allowed_hosts)
            async with httpx.AsyncClient(
                headers=headers,
                params=params,
                cookies=cookie_jar,
                follow_redirects=not normalized_hosts,
                timeout=30.0,
            ) as session:
                await self._stream_tracker_download(
                    session, downurl, path, normalized_hosts, max_size
                )
            return await self._tracker_download_result(
                meta, tracker, path, hash_is_id, cross
            )
        except Exception as error:
            self._cleanup_failed_tracker_download(
                path, allowed_hosts, max_size
            )
            logger.warning(
                f"[yellow]Warning: Could not download torrent file: {error!s}[/yellow]"
            )
            logger.info("[yellow]Download manually from the tracker.[/yellow]")
            return None

    async def create_torrent_ready_to_seed(
        self,
        meta: Meta,
        tracker: str,
        source_flag: str,
        new_tracker: str | list[str],
        comment: str = "",
        hash_is_id: bool = False,
    ) -> str | None:
        """
        Modifies the torrent file to include the tracker's announce URL, a comment, and a source flag.
        """
        path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}].torrent"
        if await self.path_exists(path):
            loop = asyncio.get_running_loop()
            new_torrent = await loop.run_in_executor(
                None, lambda: Torrent.read(path)
            )
            if isinstance(new_tracker, list):
                if not new_tracker:
                    logger.error(
                        f"[red]Error: Empty tracker list provided for {tracker}. Cannot create torrent.[/red]"
                    )
                    return None
                new_torrent.metainfo["announce"] = new_tracker[0]
                new_torrent.metainfo["announce-list"] = [new_tracker]
            else:
                new_torrent.metainfo["announce"] = new_tracker
            new_torrent.metainfo["info"]["source"] = source_flag

            # Calculate hash only when hash_is_id is True
            torrent_hash: str | None = None
            if hash_is_id:
                info_data = new_torrent.metainfo.get("info", {})
                bencode_module = cast(Any, bencodepy)
                encode = cast(Callable[[Any], bytes], bencode_module.encode)
                info_bytes = encode(info_data)
                # BitTorrent v1 mandates SHA-1 for the info-hash; it is not used as a signature.
                # nosemgrep: insecure-hash-algorithm-sha1
                torrent_hash = hashlib.sha1(
                    info_bytes, usedforsecurity=False
                ).hexdigest()  # SHA1 required for torrent info hash
                new_torrent.metainfo["comment"] = comment + torrent_hash
            else:
                new_torrent.metainfo["comment"] = comment

            await loop.run_in_executor(
                None,
                lambda: Torrent.copy(new_torrent).write(path, overwrite=True),
            )

            return torrent_hash

        return None

    async def get_torrent_hash(self, meta: Meta, tracker: str) -> str:
        torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}].torrent"
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            torrent_content = await torrent_file.read()
            bencode_module = cast(Any, bencodepy)
            decode = cast(Callable[[bytes], Any], bencode_module.decode)
            torrent_data = decode(torrent_content)
            if not isinstance(torrent_data, dict):
                return ""
            torrent_dict = cast(dict[bytes, Any], torrent_data)
            info_value = torrent_dict.get(b"info")
            if not isinstance(info_value, dict):
                return ""
            bencode_module = cast(Any, bencodepy)
            encode = cast(Callable[[Any], bytes], bencode_module.encode)
            info = encode(info_value)
            # BitTorrent v1 mandates SHA-1 for the info-hash; it is not used as a signature.
            # nosemgrep: insecure-hash-algorithm-sha1
            return hashlib.sha1(
                info, usedforsecurity=False
            ).hexdigest()  # SHA1 required for torrent info hash

    @staticmethod
    def _image_links_path(meta: Meta) -> Path:
        output_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / "pack_image_links.json"

    @staticmethod
    def _default_image_links_data() -> dict[str, Any]:
        return {"keys": {}, "total_count": 0}

    @staticmethod
    def _valid_image_links_data(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        data = cast(dict[str, Any], value)
        if not isinstance(data.get("keys"), dict):
            return None
        if not isinstance(data.get("total_count"), int):
            return None
        return data

    async def _load_image_links_data(
        self, output_file: Path
    ) -> dict[str, Any]:
        if not output_file.exists():
            return self._default_image_links_data()
        try:
            async with aiofiles.open(output_file, encoding="utf-8") as handle:
                content = await handle.read()
            if not content.strip():
                return self._default_image_links_data()
            parsed = json.loads(content)
            valid = self._valid_image_links_data(parsed)
            if valid is not None:
                return valid
            logger.warning(
                "[yellow]Warning: Existing image data has invalid schema, reinitializing.[/yellow]"
            )
        except (json.JSONDecodeError, OSError) as error:
            logger.warning(
                f"[yellow]Warning: Could not load existing image data: {error!s}[/yellow]"
            )
        return self._default_image_links_data()

    @staticmethod
    def _image_keys(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = data.get("keys", {})
        return cast(dict[str, dict[str, Any]], raw)

    @staticmethod
    def _image_key_entry(
        keys_data: dict[str, dict[str, Any]], image_key: str
    ) -> dict[str, Any]:
        entry = keys_data.get(image_key)
        if isinstance(entry, dict):
            return cast(dict[str, Any], entry)
        new_entry: dict[str, Any] = {"count": 0, "images": []}
        keys_data[image_key] = new_entry
        return new_entry

    @staticmethod
    def _image_entry_list(key_entry: dict[str, Any]) -> list[dict[str, Any]]:
        raw = key_entry.get("images")
        if isinstance(raw, list):
            return cast(list[dict[str, Any]], raw)
        images: list[dict[str, Any]] = []
        key_entry["images"] = images
        return images

    @staticmethod
    def _append_image_entries(
        key_entry: dict[str, Any], image_list: list[dict[str, str]]
    ) -> None:
        images = Common._image_entry_list(key_entry)
        cached_count = key_entry.get("count")
        base_count = (
            cached_count if isinstance(cached_count, int) else len(images)
        )
        images.extend(
            {
                "index": base_count + index,
                "raw_url": image.get("raw_url", ""),
                "web_url": image.get("web_url", ""),
                "img_url": image.get("img_url", ""),
            }
            for index, image in enumerate(image_list)
        )
        key_entry["count"] = len(images)

    @staticmethod
    def _image_total_count(keys_data: dict[str, dict[str, Any]]) -> int:
        return sum(
            count
            for entry in keys_data.values()
            if isinstance((count := entry.get("count")), int)
        )

    async def _write_image_links_data(
        self, output_file: Path, data: dict[str, Any]
    ) -> bool:
        try:
            async with aiofiles.open(
                output_file, "w", encoding="utf-8"
            ) as handle:
                await handle.write(json.dumps(data, indent=2))
            return True
        except Exception as error:
            logger.info(
                f"[bold red]Error saving image links: {error}[/bold red]"
            )
            return False

    async def save_image_links(
        self,
        meta: Meta,
        image_key: str,
        image_list: list[dict[str, str]] | None,
    ) -> str | None:
        if image_list is None:
            logger.info("[yellow]No image links to save.[/yellow]")
            return None
        output_file = self._image_links_path(meta)
        data = await self._load_image_links_data(output_file)
        keys_data = self._image_keys(data)
        key_entry = self._image_key_entry(keys_data, image_key)
        self._append_image_entries(key_entry, image_list)
        data["total_count"] = self._image_total_count(keys_data)
        if not await self._write_image_links_data(output_file, data):
            return None
        logger.debug(
            f"[green]Saved {len(image_list)} new images for key '{image_key}' "
            f"(total: {data['total_count']}):[/green]"
        )
        logger.debug(f"[blue]  - JSON: {output_file}[/blue]")
        return str(output_file)

    @staticmethod
    def _reverse_mapped_identifier(
        mapping: dict[str, int], raw_id: Any
    ) -> str:
        try:
            target = int(raw_id)
        except TypeError, ValueError:
            return ""
        return next(
            (name for name, value in mapping.items() if value == target),
            "",
        )

    @staticmethod
    def _mapped_identifier(
        mapping: dict[str, int], name: str, reverse: bool, raw_id: Any
    ) -> str:
        if reverse:
            return Common._reverse_mapped_identifier(mapping, raw_id)
        value = mapping.get(name)
        return str(value) if value else ""

    async def unit3d_region_ids(
        self, region: str = "", reverse: bool = False, region_id: int = 0
    ) -> str:
        region_map = {
            "AFG": 1,
            "AIA": 2,
            "ALA": 3,
            "ALG": 4,
            "AND": 5,
            "ANG": 6,
            "ARG": 7,
            "ARM": 8,
            "ARU": 9,
            "ASA": 10,
            "ATA": 11,
            "ATF": 12,
            "ATG": 13,
            "AUS": 14,
            "AUT": 15,
            "AZE": 16,
            "BAH": 17,
            "BAN": 18,
            "BDI": 19,
            "BEL": 20,
            "BEN": 21,
            "BER": 22,
            "BES": 23,
            "BFA": 24,
            "BHR": 25,
            "BHU": 26,
            "BIH": 27,
            "BLM": 28,
            "BLR": 29,
            "BLZ": 30,
            "BOL": 31,
            "BOT": 32,
            "BRA": 33,
            "BRB": 34,
            "BRU": 35,
            "BVT": 36,
            "CAM": 37,
            "CAN": 38,
            "CAY": 39,
            "CCK": 40,
            "CEE": 41,
            "CGO": 42,
            "CHA": 43,
            "CHI": 44,
            "CHN": 45,
            "CIV": 46,
            "CMR": 47,
            "COD": 48,
            "COK": 49,
            "COL": 50,
            "COM": 51,
            "CPV": 52,
            "CRC": 53,
            "CRO": 54,
            "CTA": 55,
            "CUB": 56,
            "CUW": 57,
            "CXR": 58,
            "CYP": 59,
            "DJI": 60,
            "DMA": 61,
            "DOM": 62,
            "ECU": 63,
            "EGY": 64,
            "ENG": 65,
            "EQG": 66,
            "ERI": 67,
            "ESH": 68,
            "ESP": 69,
            "ETH": 70,
            "FIJ": 71,
            "FLK": 72,
            "FRA": 73,
            "FRO": 74,
            "FSM": 75,
            "GAB": 76,
            "GAM": 77,
            "GBR": 78,
            "GEO": 79,
            "GER": 80,
            "GGY": 81,
            "GHA": 82,
            "GIB": 83,
            "GLP": 84,
            "GNB": 85,
            "GRE": 86,
            "GRL": 87,
            "GRN": 88,
            "GUA": 89,
            "GUF": 90,
            "GUI": 91,
            "GUM": 92,
            "GUY": 93,
            "HAI": 94,
            "HKG": 95,
            "HMD": 96,
            "HON": 97,
            "HUN": 98,
            "IDN": 99,
            "IMN": 100,
            "IND": 101,
            "IOT": 102,
            "IRL": 103,
            "IRN": 104,
            "IRQ": 105,
            "ISL": 106,
            "ISR": 107,
            "ITA": 108,
            "JAM": 109,
            "JEY": 110,
            "JOR": 111,
            "JPN": 112,
            "KAZ": 113,
            "KEN": 114,
            "KGZ": 115,
            "KIR": 116,
            "KNA": 117,
            "KOR": 118,
            "KSA": 119,
            "KUW": 120,
            "KVX": 121,
            "LAO": 122,
            "LBN": 123,
            "LBR": 124,
            "LBY": 125,
            "LCA": 126,
            "LES": 127,
            "LIE": 128,
            "LKA": 129,
            "LUX": 130,
            "MAC": 131,
            "MAD": 132,
            "MAF": 133,
            "MAR": 134,
            "MAS": 135,
            "MDA": 136,
            "MDV": 137,
            "MEX": 138,
            "MHL": 139,
            "MKD": 140,
            "MLI": 141,
            "MLT": 142,
            "MNG": 143,
            "MNP": 144,
            "MON": 145,
            "MOZ": 146,
            "MRI": 147,
            "MSR": 148,
            "MTN": 149,
            "MTQ": 150,
            "MWI": 151,
            "MYA": 152,
            "MYT": 153,
            "NAM": 154,
            "NCA": 155,
            "NCL": 156,
            "NEP": 157,
            "NFK": 158,
            "NIG": 159,
            "NIR": 160,
            "NIU": 161,
            "NLD": 162,
            "NOR": 163,
            "NRU": 164,
            "NZL": 165,
            "OMA": 166,
            "PAK": 167,
            "PAN": 168,
            "PAR": 169,
            "PCN": 170,
            "PER": 171,
            "PHI": 172,
            "PLE": 173,
            "PLW": 174,
            "PNG": 175,
            "POL": 176,
            "POR": 177,
            "PRK": 178,
            "PUR": 179,
            "QAT": 180,
            "REU": 181,
            "ROU": 182,
            "RSA": 183,
            "RUS": 184,
            "RWA": 185,
            "SAM": 186,
            "SCO": 187,
            "SDN": 188,
            "SEN": 189,
            "SEY": 190,
            "SGS": 191,
            "SHN": 192,
            "SIN": 193,
            "SJM": 194,
            "SLE": 195,
            "SLV": 196,
            "SMR": 197,
            "SOL": 198,
            "SOM": 199,
            "SPM": 200,
            "SRB": 201,
            "SSD": 202,
            "STP": 203,
            "SUI": 204,
            "SUR": 205,
            "SWZ": 206,
            "SXM": 207,
            "SYR": 208,
            "TAH": 209,
            "TAN": 210,
            "TCA": 211,
            "TGA": 212,
            "THA": 213,
            "TJK": 214,
            "TKL": 215,
            "TKM": 216,
            "TLS": 217,
            "TOG": 218,
            "TRI": 219,
            "TUN": 220,
            "TUR": 221,
            "TUV": 222,
            "TWN": 223,
            "UAE": 224,
            "UGA": 225,
            "UKR": 226,
            "UMI": 227,
            "URU": 228,
            "USA": 229,
            "UZB": 230,
            "VAN": 231,
            "VAT": 232,
            "VEN": 233,
            "VGB": 234,
            "VIE": 235,
            "VIN": 236,
            "VIR": 237,
            "WAL": 238,
            "WLF": 239,
            "YEM": 240,
            "ZAM": 241,
            "ZIM": 242,
            "EUR": 243,
        }

        return self._mapped_identifier(region_map, region, reverse, region_id)

    async def unit3d_distributor_ids(
        self,
        distributor: str = "",
        reverse: bool = False,
        distributor_id: int = 0,
    ) -> str:
        distributor_map = {
            "01 DISTRIBUTION": 1,
            "100 DESTINATIONS TRAVEL FILM": 2,
            "101 FILMS": 3,
            "1FILMS": 4,
            "2 ENTERTAIN VIDEO": 5,
            "20TH CENTURY FOX": 6,
            "2L": 7,
            "3D CONTENT HUB": 8,
            "3D MEDIA": 9,
            "3L FILM": 10,
            "4DIGITAL": 11,
            "4DVD": 12,
            "4K ULTRA HD MOVIES": 13,
            "4K UHD": 13,
            "8-FILMS": 14,
            "84 ENTERTAINMENT": 15,
            "88 FILMS": 16,
            "@ANIME": 17,
            "ANIME": 17,
            "A CONTRACORRIENTE": 18,
            "A CONTRACORRIENTE FILMS": 19,
            "A&E HOME VIDEO": 20,
            "A&E": 20,
            "A&M RECORDS": 21,
            "A+E NETWORKS": 22,
            "A+R": 23,
            "A-FILM": 24,
            "AAA": 25,
            "AB VIDÉO": 26,
            "AB VIDEO": 26,
            "ABC - (AUSTRALIAN BROADCASTING CORPORATION)": 27,
            "ABC": 27,
            "ABKCO": 28,
            "ABSOLUT MEDIEN": 29,
            "ABSOLUTE": 30,
            "ACCENT FILM ENTERTAINMENT": 31,
            "ACCENTUS": 32,
            "ACORN MEDIA": 33,
            "AD VITAM": 34,
            "ADA": 35,
            "ADITYA VIDEOS": 36,
            "ADSO FILMS": 37,
            "AFM RECORDS": 38,
            "AGFA": 39,
            "AIX RECORDS": 40,
            "ALAMODE FILM": 41,
            "ALBA RECORDS": 42,
            "ALBANY RECORDS": 43,
            "ALBATROS": 44,
            "ALCHEMY": 45,
            "ALIVE": 46,
            "ALL ANIME": 47,
            "ALL INTERACTIVE ENTERTAINMENT": 48,
            "ALLEGRO": 49,
            "ALLIANCE": 50,
            "ALPHA MUSIC": 51,
            "ALTERDYSTRYBUCJA": 52,
            "ALTERED INNOCENCE": 53,
            "ALTITUDE FILM DISTRIBUTION": 54,
            "ALUCARD RECORDS": 55,
            "AMAZING D.C.": 56,
            "AMAZING DC": 56,
            "AMMO CONTENT": 57,
            "AMUSE SOFT ENTERTAINMENT": 58,
            "ANCONNECT": 59,
            "ANEC": 60,
            "ANIMATSU": 61,
            "ANIME HOUSE": 62,
            "ANIME LTD": 63,
            "ANIME WORKS": 64,
            "ANIMEIGO": 65,
            "ANIPLEX": 66,
            "ANOLIS ENTERTAINMENT": 67,
            "ANOTHER WORLD ENTERTAINMENT": 68,
            "AP INTERNATIONAL": 69,
            "APPLE": 70,
            "ARA MEDIA": 71,
            "ARBELOS": 72,
            "ARC ENTERTAINMENT": 73,
            "ARP SÉLECTION": 74,
            "ARP SELECTION": 74,
            "ARROW": 75,
            "ART SERVICE": 76,
            "ART VISION": 77,
            "ARTE ÉDITIONS": 78,
            "ARTE EDITIONS": 78,
            "ARTE VIDÉO": 79,
            "ARTE VIDEO": 79,
            "ARTHAUS MUSIK": 80,
            "ARTIFICIAL EYE": 81,
            "ARTSPLOITATION FILMS": 82,
            "ARTUS FILMS": 83,
            "ASCOT ELITE HOME ENTERTAINMENT": 84,
            "ASIA VIDEO": 85,
            "ASMIK ACE": 86,
            "ASTRO RECORDS & FILMWORKS": 87,
            "ASYLUM": 88,
            "ATLANTIC FILM": 89,
            "ATLANTIC RECORDS": 90,
            "ATLAS FILM": 91,
            "AUDIO VISUAL ENTERTAINMENT": 92,
            "AURO-3D CREATIVE LABEL": 93,
            "AURUM": 94,
            "AV VISIONEN": 95,
            "AV-JET": 96,
            "AVALON": 97,
            "AVENTI": 98,
            "AVEX TRAX": 99,
            "AXIOM": 100,
            "AXIS RECORDS": 101,
            "AYNGARAN": 102,
            "BAC FILMS": 103,
            "BACH FILMS": 104,
            "BANDAI VISUAL": 105,
            "BARCLAY": 106,
            "BBC": 107,
            "BRITISH BROADCASTING CORPORATION": 107,
            "BBI FILMS": 108,
            "BBI": 108,
            "BCI HOME ENTERTAINMENT": 109,
            "BEGGARS BANQUET": 110,
            "BEL AIR CLASSIQUES": 111,
            "BELGA FILMS": 112,
            "BELVEDERE": 113,
            "BENELUX FILM DISTRIBUTORS": 114,
            "BENNETT-WATT MEDIA": 115,
            "BERLIN CLASSICS": 116,
            "BERLINER PHILHARMONIKER RECORDINGS": 117,
            "BEST ENTERTAINMENT": 118,
            "BEYOND HOME ENTERTAINMENT": 119,
            "BFI VIDEO": 120,
            "BFI": 120,
            "BRITISH FILM INSTITUTE": 120,
            "BFS ENTERTAINMENT": 121,
            "BFS": 121,
            "BHAVANI": 122,
            "BIBER RECORDS": 123,
            "BIG HOME VIDEO": 124,
            "BILDSTÖRUNG": 125,
            "BILDSTORUNG": 125,
            "BILL ZEBUB": 126,
            "BIRNENBLATT": 127,
            "BIT WEL": 128,
            "BLACK BOX": 129,
            "BLACK HILL PICTURES": 130,
            "BLACK HILL": 130,
            "BLACK HOLE RECORDINGS": 131,
            "BLACK HOLE": 131,
            "BLAQOUT": 132,
            "BLAUFIELD MUSIC": 133,
            "BLAUFIELD": 133,
            "BLOCKBUSTER ENTERTAINMENT": 134,
            "BLOCKBUSTER": 134,
            "BLU PHASE MEDIA": 135,
            "BLU-RAY ONLY": 136,
            "BLU-RAY": 136,
            "BLURAY ONLY": 136,
            "BLURAY": 136,
            "BLUE GENTIAN RECORDS": 137,
            "BLUE KINO": 138,
            "BLUE UNDERGROUND": 139,
            "BMG/ARISTA": 140,
            "BMG": 140,
            "BMGARISTA": 140,
            "BMG ARISTA": 140,
            "ARISTA": 140,
            "ARISTA/BMG": 140,
            "ARISTABMG": 140,
            "ARISTA BMG": 140,
            "BONTON FILM": 141,
            "BONTON": 141,
            "BOOMERANG PICTURES": 142,
            "BOOMERANG": 142,
            "BQHL ÉDITIONS": 143,
            "BQHL EDITIONS": 143,
            "BQHL": 143,
            "BREAKING GLASS": 144,
            "BRIDGESTONE": 145,
            "BRINK": 146,
            "BROAD GREEN PICTURES": 147,
            "BROAD GREEN": 147,
            "BUSCH MEDIA GROUP": 148,
            "BUSCH": 148,
            "C MAJOR": 149,
            "C.B.S.": 150,
            "CAICHANG": 151,
            "CALIFÓRNIA FILMES": 152,
            "CALIFORNIA FILMES": 152,
            "CALIFORNIA": 152,
            "CAMEO": 153,
            "CAMERA OBSCURA": 154,
            "CAMERATA": 155,
            "CAMP MOTION PICTURES": 156,
            "CAMP MOTION": 156,
            "CAPELIGHT PICTURES": 157,
            "CAPELIGHT": 157,
            "CAPITOL": 159,
            "CAPITOL RECORDS": 159,
            "CAPRICCI": 160,
            "CARGO RECORDS": 161,
            "CARLOTTA FILMS": 162,
            "CARLOTTA": 162,
            "CARLOTA": 162,
            "CARMEN FILM": 163,
            "CASCADE": 164,
            "CATCHPLAY": 165,
            "CAULDRON FILMS": 166,
            "CAULDRON": 166,
            "CBS TELEVISION STUDIOS": 167,
            "CBS": 167,
            "CCTV": 168,
            "CCV ENTERTAINMENT": 169,
            "CCV": 169,
            "CD BABY": 170,
            "CD LAND": 171,
            "CECCHI GORI": 172,
            "CENTURY MEDIA": 173,
            "CHUAN XUN SHI DAI MULTIMEDIA": 174,
            "CINE-ASIA": 175,
            "CINÉART": 176,
            "CINEART": 176,
            "CINEDIGM": 177,
            "CINEFIL IMAGICA": 178,
            "CINEMA EPOCH": 179,
            "CINEMA GUILD": 180,
            "CINEMA LIBRE STUDIOS": 181,
            "CINEMA MONDO": 182,
            "CINEMATIC VISION": 183,
            "CINEPLOIT RECORDS": 184,
            "CINESTRANGE EXTREME": 185,
            "CITEL VIDEO": 186,
            "CITEL": 186,
            "CJ ENTERTAINMENT": 187,
            "CJ": 187,
            "CLASSIC MEDIA": 188,
            "CLASSICFLIX": 189,
            "CLASSICLINE": 190,
            "CLAUDIO RECORDS": 191,
            "CLEAR VISION": 192,
            "CLEOPATRA": 193,
            "CLOSE UP": 194,
            "CMS MEDIA LIMITED": 195,
            "CMV LASERVISION": 196,
            "CN ENTERTAINMENT": 197,
            "CODE RED": 198,
            "COHEN MEDIA GROUP": 199,
            "COHEN": 199,
            "COIN DE MIRE CINÉMA": 200,
            "COIN DE MIRE CINEMA": 200,
            "COLOSSEO FILM": 201,
            "COLUMBIA": 203,
            "COLUMBIA PICTURES": 203,
            "COLUMBIA/TRI-STAR": 204,
            "TRI-STAR": 204,
            "COMMERCIAL MARKETING": 205,
            "CONCORD MUSIC GROUP": 206,
            "CONCORDE VIDEO": 207,
            "CONDOR": 208,
            "CONSTANTIN FILM": 209,
            "CONSTANTIN": 209,
            "CONSTANTINO FILMES": 210,
            "CONSTANTINO": 210,
            "CONSTRUCTIVE MEDIA SERVICE": 211,
            "CONSTRUCTIVE": 211,
            "CONTENT ZONE": 212,
            "CONTENTS GATE": 213,
            "COQUEIRO VERDE": 214,
            "CORNERSTONE MEDIA": 215,
            "CORNERSTONE": 215,
            "CP DIGITAL": 216,
            "CREST MOVIES": 217,
            "CRITERION": 218,
            "CRITERION COLLECTION": 218,
            "CC": 218,
            "CRYSTAL CLASSICS": 219,
            "CULT EPICS": 220,
            "CULT FILMS": 221,
            "CULT VIDEO": 222,
            "CURZON FILM WORLD": 223,
            "D FILMS": 224,
            "D'AILLY COMPANY": 225,
            "DAILLY COMPANY": 225,
            "D AILLY COMPANY": 225,
            "D'AILLY": 225,
            "DAILLY": 225,
            "D AILLY": 225,
            "DA CAPO": 226,
            "DA MUSIC": 227,
            "DALL'ANGELO PICTURES": 228,
            "DALLANGELO PICTURES": 228,
            "DALL'ANGELO": 228,
            "DALL ANGELO PICTURES": 228,
            "DALL ANGELO": 228,
            "DAREDO": 229,
            "DARK FORCE ENTERTAINMENT": 230,
            "DARK FORCE": 230,
            "DARK SIDE RELEASING": 231,
            "DARK SIDE": 231,
            "DAZZLER MEDIA": 232,
            "DAZZLER": 232,
            "DCM PICTURES": 233,
            "DCM": 233,
            "DEAPLANETA": 234,
            "DECCA": 235,
            "DEEPJOY": 236,
            "DEFIANT SCREEN ENTERTAINMENT": 237,
            "DEFIANT SCREEN": 237,
            "DEFIANT": 237,
            "DELOS": 238,
            "DELPHIAN RECORDS": 239,
            "DELPHIAN": 239,
            "DELTA MUSIC & ENTERTAINMENT": 240,
            "DELTA MUSIC AND ENTERTAINMENT": 240,
            "DELTA MUSIC ENTERTAINMENT": 240,
            "DELTA MUSIC": 240,
            "DELTAMAC CO. LTD.": 241,
            "DELTAMAC CO LTD": 241,
            "DELTAMAC CO": 241,
            "DELTAMAC": 241,
            "DEMAND MEDIA": 242,
            "DEMAND": 242,
            "DEP": 243,
            "DEUTSCHE GRAMMOPHON": 244,
            "DFW": 245,
            "DGM": 246,
            "DIAPHANA": 247,
            "DIGIDREAMS STUDIOS": 248,
            "DIGIDREAMS": 248,
            "DIGITAL ENVIRONMENTS": 249,
            "DIGITAL": 249,
            "DISCOTEK MEDIA": 250,
            "DISCOVERY CHANNEL": 251,
            "DISCOVERY": 251,
            "DISK KINO": 252,
            "DISNEY / BUENA VISTA": 253,
            "DISNEY": 253,
            "BUENA VISTA": 253,
            "DISNEY BUENA VISTA": 253,
            "DISTRIBUTION SELECT": 254,
            "DIVISA": 255,
            "DNC ENTERTAINMENT": 256,
            "DNC": 256,
            "DOGWOOF": 257,
            "DOLMEN HOME VIDEO": 258,
            "DOLMEN": 258,
            "DONAU FILM": 259,
            "DONAU": 259,
            "DORADO FILMS": 260,
            "DORADO": 260,
            "DRAFTHOUSE FILMS": 261,
            "DRAFTHOUSE": 261,
            "DRAGON FILM ENTERTAINMENT": 262,
            "DRAGON ENTERTAINMENT": 262,
            "DRAGON FILM": 262,
            "DRAGON": 262,
            "DREAMWORKS": 263,
            "DRIVE ON RECORDS": 264,
            "DRIVE ON": 264,
            "DRIVE-ON": 264,
            "DRIVEON": 264,
            "DS MEDIA": 265,
            "DTP ENTERTAINMENT AG": 266,
            "DTP ENTERTAINMENT": 266,
            "DTP AG": 266,
            "DTP": 266,
            "DTS ENTERTAINMENT": 267,
            "DTS": 267,
            "DUKE MARKETING": 268,
            "DUKE VIDEO DISTRIBUTION": 269,
            "DUKE": 269,
            "DUTCH FILMWORKS": 270,
            "DUTCH": 270,
            "DVD INTERNATIONAL": 271,
            "DVD": 271,
            "DYBEX": 272,
            "DYNAMIC": 273,
            "DYNIT": 274,
            "E1 ENTERTAINMENT": 275,
            "E1": 275,
            "EAGLE ENTERTAINMENT": 276,
            "EAGLE HOME ENTERTAINMENT PVT.LTD.": 277,
            "EAGLE HOME ENTERTAINMENT PVTLTD": 277,
            "EAGLE HOME ENTERTAINMENT PVT LTD": 277,
            "EAGLE HOME ENTERTAINMENT": 277,
            "EAGLE PICTURES": 278,
            "EAGLE ROCK ENTERTAINMENT": 279,
            "EAGLE ROCK": 279,
            "EAGLE VISION MEDIA": 280,
            "EAGLE VISION": 280,
            "EARMUSIC": 281,
            "EARTH ENTERTAINMENT": 282,
            "EARTH": 282,
            "ECHO BRIDGE ENTERTAINMENT": 283,
            "ECHO BRIDGE": 283,
            "EDEL GERMANY GMBH": 284,
            "EDEL GERMANY": 284,
            "EDEL RECORDS": 285,
            "EDITION TONFILM": 286,
            "EDITIONS MONTPARNASSE": 287,
            "EDKO FILMS LTD.": 288,
            "EDKO FILMS LTD": 288,
            "EDKO FILMS": 288,
            "EDKO": 288,
            "EIN'S M&M CO": 289,
            "EINS M&M CO": 289,
            "EIN'S M&M": 289,
            "EINS M&M": 289,
            "ELEA-MEDIA": 290,
            "ELEA MEDIA": 290,
            "ELEA": 290,
            "ELECTRIC PICTURE": 291,
            "ELECTRIC": 291,
            "ELEPHANT FILMS": 292,
            "ELEPHANT": 292,
            "ELEVATION": 293,
            "EMI": 294,
            "EMON": 295,
            "EMS": 296,
            "EMYLIA": 297,
            "ENE MEDIA": 298,
            "ENE": 298,
            "ENTERTAINMENT IN VIDEO": 299,
            "ENTERTAINMENT IN": 299,
            "ENTERTAINMENT ONE": 300,
            "ENTERTAINMENT ONE FILMS CANADA INC.": 301,
            "ENTERTAINMENT ONE FILMS CANADA INC": 301,
            "ENTERTAINMENT ONE FILMS CANADA": 301,
            "ENTERTAINMENT ONE CANADA INC": 301,
            "ENTERTAINMENT ONE CANADA": 301,
            "ENTERTAINMENTONE": 302,
            "EONE": 303,
            "EOS": 304,
            "EPIC PICTURES": 305,
            "EPIC": 305,
            "EPIC RECORDS": 306,
            "ERATO": 307,
            "EROS": 308,
            "ESC EDITIONS": 309,
            "ESCAPI MEDIA BV": 310,
            "ESOTERIC RECORDINGS": 311,
            "ESPN FILMS": 312,
            "EUREKA ENTERTAINMENT": 313,
            "EUREKA": 313,
            "EURO PICTURES": 314,
            "EURO VIDEO": 315,
            "EUROARTS": 316,
            "EUROPA FILMES": 317,
            "EUROPA": 317,
            "EUROPACORP": 318,
            "EUROZOOM": 319,
            "EXCEL": 320,
            "EXPLOSIVE MEDIA": 321,
            "EXPLOSIVE": 321,
            "EXTRALUCID FILMS": 322,
            "EXTRALUCID": 322,
            "EYE SEE MOVIES": 323,
            "EYE SEE": 323,
            "EYK MEDIA": 324,
            "EYK": 324,
            "FABULOUS FILMS": 325,
            "FABULOUS": 325,
            "FACTORIS FILMS": 326,
            "FACTORIS": 326,
            "FARAO RECORDS": 327,
            "FARBFILM HOME ENTERTAINMENT": 328,
            "FARBFILM ENTERTAINMENT": 328,
            "FARBFILM HOME": 328,
            "FARBFILM": 328,
            "FEELGOOD ENTERTAINMENT": 329,
            "FEELGOOD": 329,
            "FERNSEHJUWELEN": 330,
            "FILM CHEST": 331,
            "FILM MEDIA": 332,
            "FILM MOVEMENT": 333,
            "FILM4": 334,
            "FILMART": 335,
            "FILMAURO": 336,
            "FILMAX": 337,
            "FILMCONFECT HOME ENTERTAINMENT": 338,
            "FILMCONFECT ENTERTAINMENT": 338,
            "FILMCONFECT HOME": 338,
            "FILMCONFECT": 338,
            "FILMEDIA": 339,
            "FILMJUWELEN": 340,
            "FILMOTEKA NARODAWA": 341,
            "FILMRISE": 342,
            "FINAL CUT ENTERTAINMENT": 343,
            "FINAL CUT": 343,
            "FIREHOUSE 12 RECORDS": 344,
            "FIREHOUSE 12": 344,
            "FIRST INTERNATIONAL PRODUCTION": 345,
            "FIRST INTERNATIONAL": 345,
            "FIRST LOOK STUDIOS": 346,
            "FIRST LOOK": 346,
            "FLAGMAN TRADE": 347,
            "FLASHSTAR FILMES": 348,
            "FLASHSTAR": 348,
            "FLICKER ALLEY": 349,
            "FNC ADD CULTURE": 350,
            "FOCUS FILMES": 351,
            "FOCUS": 351,
            "FOKUS MEDIA": 352,
            "FOKUSA": 352,
            "FOX PATHE EUROPA": 353,
            "FOX PATHE": 353,
            "FOX EUROPA": 353,
            "FOX/MGM": 354,
            "FOX MGM": 354,
            "MGM": 354,
            "MGM/FOX": 354,
            "FOX": 354,
            "FPE": 355,
            "FRANCE TÉLÉVISIONS DISTRIBUTION": 356,
            "FRANCE TELEVISIONS DISTRIBUTION": 356,
            "FRANCE TELEVISIONS": 356,
            "FRANCE": 356,
            "FREE DOLPHIN ENTERTAINMENT": 357,
            "FREE DOLPHIN": 357,
            "FREESTYLE DIGITAL MEDIA": 358,
            "FREESTYLE DIGITAL": 358,
            "FREESTYLE": 358,
            "FREMANTLE HOME ENTERTAINMENT": 359,
            "FREMANTLE ENTERTAINMENT": 359,
            "FREMANTLE HOME": 359,
            "FREMANTL": 359,
            "FRENETIC FILMS": 360,
            "FRENETIC": 360,
            "FRONTIER WORKS": 361,
            "FRONTIER": 361,
            "FRONTIERS MUSIC": 362,
            "FRONTIERS RECORDS": 363,
            "FS FILM OY": 364,
            "FS FILM": 364,
            "FULL MOON FEATURES": 365,
            "FULL MOON": 365,
            "FUN CITY EDITIONS": 366,
            "FUN CITY": 366,
            "FUNIMATION ENTERTAINMENT": 367,
            "FUNIMATION": 367,
            "FUSION": 368,
            "FUTUREFILM": 369,
            "G2 PICTURES": 370,
            "G2": 370,
            "GAGA COMMUNICATIONS": 371,
            "GAGA": 371,
            "GAIAM": 372,
            "GALAPAGOS": 373,
            "GAMMA HOME ENTERTAINMENT": 374,
            "GAMMA ENTERTAINMENT": 374,
            "GAMMA HOME": 374,
            "GAMMA": 374,
            "GARAGEHOUSE PICTURES": 375,
            "GARAGEHOUSE": 375,
            "GARAGEPLAY (車庫娛樂)": 376,
            "車庫娛樂": 376,
            "GARAGEPLAY (Che Ku Yu Le )": 376,
            "GARAGEPLAY": 376,
            "Che Ku Yu Le": 376,
            "GAUMONT": 377,
            "GEFFEN": 378,
            "GENEON ENTERTAINMENT": 379,
            "GENEON": 379,
            "GENEON UNIVERSAL ENTERTAINMENT": 380,
            "GENERAL VIDEO RECORDING": 381,
            "GLASS DOLL FILMS": 382,
            "GLASS DOLL": 382,
            "GLOBE MUSIC MEDIA": 383,
            "GLOBE MUSIC": 383,
            "GLOBE MEDIA": 383,
            "GLOBE": 383,
            "GO ENTERTAIN": 384,
            "GO": 384,
            "GOLDEN HARVEST": 385,
            "GOOD!MOVIES": 386,
            "GOOD! MOVIES": 386,
            "GOOD MOVIES": 386,
            "GRAPEVINE VIDEO": 387,
            "GRAPEVINE": 387,
            "GRASSHOPPER FILM": 388,
            "GRASSHOPPER FILMS": 388,
            "GRASSHOPPER": 388,
            "GRAVITAS VENTURES": 389,
            "GRAVITAS": 389,
            "GREAT MOVIES": 390,
            "GREAT": 390,
            "GREEN APPLE ENTERTAINMENT": 391,
            "GREEN ENTERTAINMENT": 391,
            "GREEN APPLE": 391,
            "GREEN": 391,
            "GREENNARAE MEDIA": 392,
            "GREENNARAE": 392,
            "GRINDHOUSE RELEASING": 393,
            "GRINDHOUSE": 393,
            "GRIND HOUSE": 393,
            "GRYPHON ENTERTAINMENT": 394,
            "GRYPHON": 394,
            "GUNPOWDER & SKY": 395,
            "GUNPOWDER AND SKY": 395,
            "GUNPOWDER SKY": 395,
            "GUNPOWDER + SKY": 395,
            "GUNPOWDER": 395,
            "HANABEE ENTERTAINMENT": 396,
            "HANABEE": 396,
            "HANNOVER HOUSE": 397,
            "HANNOVER": 397,
            "HANSESOUND": 398,
            "HANSE SOUND": 398,
            "HANSE": 398,
            "HAPPINET": 399,
            "HARMONIA MUNDI": 400,
            "HARMONIA": 400,
            "HBO": 401,
            "HDC": 402,
            "HEC": 403,
            "HELL & BACK RECORDINGS": 404,
            "HELL AND BACK RECORDINGS": 404,
            "HELL & BACK": 404,
            "HELL AND BACK": 404,
            "HEN'S TOOTH VIDEO": 405,
            "HENS TOOTH VIDEO": 405,
            "HEN'S TOOTH": 405,
            "HENS TOOTH": 405,
            "HIGH FLIERS": 406,
            "HIGHLIGHT": 407,
            "HILLSONG": 408,
            "HISTORY CHANNEL": 409,
            "HISTORY": 409,
            "HK VIDÉO": 410,
            "HK VIDEO": 410,
            "HK": 410,
            "HMH HAMBURGER MEDIEN HAUS": 411,
            "HAMBURGER MEDIEN HAUS": 411,
            "HMH HAMBURGER MEDIEN": 411,
            "HMH HAMBURGER": 411,
            "HMH": 411,
            "HOLLYWOOD CLASSIC ENTERTAINMENT": 412,
            "HOLLYWOOD CLASSIC": 412,
            "HOLLYWOOD PICTURES": 413,
            "HOLLYWOOD": 413,
            "HOPSCOTCH ENTERTAINMENT": 414,
            "HOPSCOTCH": 414,
            "HPM": 415,
            "HÄNNSLER CLASSIC": 416,
            "HANNSLER CLASSIC": 416,
            "HANNSLER": 416,
            "I-CATCHER": 417,
            "I CATCHER": 417,
            "ICATCHER": 417,
            "I-ON NEW MEDIA": 418,
            "I ON NEW MEDIA": 418,
            "ION NEW MEDIA": 418,
            "ION MEDIA": 418,
            "I-ON": 418,
            "ION": 418,
            "IAN PRODUCTIONS": 419,
            "IAN": 419,
            "ICESTORM": 420,
            "ICON FILM DISTRIBUTION": 421,
            "ICON DISTRIBUTION": 421,
            "ICON FILM": 421,
            "ICON": 421,
            "IDEALE AUDIENCE": 422,
            "IDEALE": 422,
            "IFC FILMS": 423,
            "IFC": 423,
            "IFILM": 424,
            "ILLUSIONS UNLTD.": 425,
            "ILLUSIONS UNLTD": 425,
            "ILLUSIONS": 425,
            "IMAGE ENTERTAINMENT": 426,
            "IMAGE": 426,
            "IMAGEM FILMES": 427,
            "IMAGEM": 427,
            "IMOVISION": 428,
            "IMPERIAL CINEPIX": 429,
            "IMPRINT": 430,
            "IMPULS HOME ENTERTAINMENT": 431,
            "IMPULS ENTERTAINMENT": 431,
            "IMPULS HOME": 431,
            "IMPULS": 431,
            "IN-AKUSTIK": 432,
            "IN AKUSTIK": 432,
            "INAKUSTIK": 432,
            "INCEPTION MEDIA GROUP": 433,
            "INCEPTION MEDIA": 433,
            "INCEPTION GROUP": 433,
            "INCEPTION": 433,
            "INDEPENDENT": 434,
            "INDICAN": 435,
            "INDIE RIGHTS": 436,
            "INDIE": 436,
            "INDIGO": 437,
            "INFO": 438,
            "INJOINGAN": 439,
            "INKED PICTURES": 440,
            "INKED": 440,
            "INSIDE OUT MUSIC": 441,
            "INSIDE MUSIC": 441,
            "INSIDE OUT": 441,
            "INSIDE": 441,
            "INTERCOM": 442,
            "INTERCONTINENTAL VIDEO": 443,
            "INTERCONTINENTAL": 443,
            "INTERGROOVE": 444,
            "INTERSCOPE": 445,
            "INVINCIBLE PICTURES": 446,
            "INVINCIBLE": 446,
            "ISLAND/MERCURY": 447,
            "ISLAND MERCURY": 447,
            "ISLANDMERCURY": 447,
            "ISLAND & MERCURY": 447,
            "ISLAND AND MERCURY": 447,
            "ISLAND": 447,
            "ITN": 448,
            "ITV DVD": 449,
            "ITV": 449,
            "IVC": 450,
            "IVE ENTERTAINMENT": 451,
            "IVE": 451,
            "J&R ADVENTURES": 452,
            "J&R": 452,
            "JR": 452,
            "JAKOB": 453,
            "JONU MEDIA": 454,
            "JONU": 454,
            "JRB PRODUCTIONS": 455,
            "JRB": 455,
            "JUST BRIDGE ENTERTAINMENT": 456,
            "JUST BRIDGE": 456,
            "JUST ENTERTAINMENT": 456,
            "JUST": 456,
            "KABOOM ENTERTAINMENT": 457,
            "KABOOM": 457,
            "KADOKAWA ENTERTAINMENT": 458,
            "KADOKAWA": 458,
            "KAIROS": 459,
            "KALEIDOSCOPE ENTERTAINMENT": 460,
            "KALEIDOSCOPE": 460,
            "KAM & RONSON ENTERPRISES": 461,
            "KAM & RONSON": 461,
            "KAM&RONSON ENTERPRISES": 461,
            "KAM&RONSON": 461,
            "KAM AND RONSON ENTERPRISES": 461,
            "KAM AND RONSON": 461,
            "KANA HOME VIDEO": 462,
            "KARMA FILMS": 463,
            "KARMA": 463,
            "KATZENBERGER": 464,
            "KAZE": 465,
            "KBS MEDIA": 466,
            "KBS": 466,
            "KD MEDIA": 467,
            "KD": 467,
            "KING MEDIA": 468,
            "KING": 468,
            "KING RECORDS": 469,
            "KINO LORBER": 470,
            "KINO": 470,
            "KINO SWIAT": 471,
            "KINOKUNIYA": 472,
            "KINOWELT HOME ENTERTAINMENT/DVD": 473,
            "KINOWELT HOME ENTERTAINMENT": 473,
            "KINOWELT ENTERTAINMENT": 473,
            "KINOWELT HOME DVD": 473,
            "KINOWELT ENTERTAINMENT/DVD": 473,
            "KINOWELT DVD": 473,
            "KINOWELT": 473,
            "KIT PARKER FILMS": 474,
            "KIT PARKER": 474,
            "KITTY MEDIA": 475,
            "KNM HOME ENTERTAINMENT": 476,
            "KNM ENTERTAINMENT": 476,
            "KNM HOME": 476,
            "KNM": 476,
            "KOBA FILMS": 477,
            "KOBA": 477,
            "KOCH ENTERTAINMENT": 478,
            "KOCH MEDIA": 479,
            "KOCH": 479,
            "KRAKEN RELEASING": 480,
            "KRAKEN": 480,
            "KSCOPE": 481,
            "KSM": 482,
            "KULTUR": 483,
            "L'ATELIER D'IMAGES": 484,
            "LATELIER D'IMAGES": 484,
            "L'ATELIER DIMAGES": 484,
            "LATELIER DIMAGES": 484,
            "L ATELIER D'IMAGES": 484,
            "L'ATELIER D IMAGES": 484,
            "L ATELIER D IMAGES": 484,
            "L'ATELIER": 484,
            "L ATELIER": 484,
            "LATELIER": 484,
            "LA AVENTURA AUDIOVISUAL": 485,
            "LA AVENTURA": 485,
            "LACE GROUP": 486,
            "LACE": 486,
            "LASER PARADISE": 487,
            "LAYONS": 488,
            "LCJ EDITIONS": 489,
            "LCJ": 489,
            "LE CHAT QUI FUME": 490,
            "LE PACTE": 491,
            "LEDICK FILMHANDEL": 492,
            "LEGEND": 493,
            "LEOMARK STUDIOS": 494,
            "LEOMARK": 494,
            "LEONINE FILMS": 495,
            "LEONINE": 495,
            "LICHTUNG MEDIA LTD": 496,
            "LICHTUNG LTD": 496,
            "LICHTUNG MEDIA LTD.": 496,
            "LICHTUNG LTD.": 496,
            "LICHTUNG MEDIA": 496,
            "LICHTUNG": 496,
            "LIGHTHOUSE HOME ENTERTAINMENT": 497,
            "LIGHTHOUSE ENTERTAINMENT": 497,
            "LIGHTHOUSE HOME": 497,
            "LIGHTHOUSE": 497,
            "LIGHTYEAR": 498,
            "LIONSGATE FILMS": 499,
            "LIONSGATE": 499,
            "LIZARD CINEMA TRADE": 500,
            "LLAMENTOL": 501,
            "LOBSTER FILMS": 502,
            "LOBSTER": 502,
            "LOGON": 503,
            "LORBER FILMS": 504,
            "LORBER": 504,
            "LOS BANDITOS FILMS": 505,
            "LOS BANDITOS": 505,
            "LOUD & PROUD RECORDS": 506,
            "LOUD AND PROUD RECORDS": 506,
            "LOUD & PROUD": 506,
            "LOUD AND PROUD": 506,
            "LSO LIVE": 507,
            "LUCASFILM": 508,
            "LUCKY RED": 509,
            "LUMIÈRE HOME ENTERTAINMENT": 510,
            "LUMIERE HOME ENTERTAINMENT": 510,
            "LUMIERE ENTERTAINMENT": 510,
            "LUMIERE HOME": 510,
            "LUMIERE": 510,
            "M6 VIDEO": 511,
            "M6": 511,
            "MAD DIMENSION": 512,
            "MADMAN ENTERTAINMENT": 513,
            "MADMAN": 513,
            "MAGIC BOX": 514,
            "MAGIC PLAY": 515,
            "MAGNA HOME ENTERTAINMENT": 516,
            "MAGNA ENTERTAINMENT": 516,
            "MAGNA HOME": 516,
            "MAGNA": 516,
            "MAGNOLIA PICTURES": 517,
            "MAGNOLIA": 517,
            "MAIDEN JAPAN": 518,
            "MAIDEN": 518,
            "MAJENG MEDIA": 519,
            "MAJENG": 519,
            "MAJESTIC HOME ENTERTAINMENT": 520,
            "MAJESTIC ENTERTAINMENT": 520,
            "MAJESTIC HOME": 520,
            "MAJESTIC": 520,
            "MANGA HOME ENTERTAINMENT": 521,
            "MANGA ENTERTAINMENT": 521,
            "MANGA HOME": 521,
            "MANGA": 521,
            "MANTA LAB": 522,
            "MAPLE STUDIOS": 523,
            "MAPLE": 523,
            "MARCO POLO PRODUCTION": 524,
            "MARCO POLO": 524,
            "MARIINSKY": 525,
            "MARVEL STUDIOS": 526,
            "MARVEL": 526,
            "MASCOT RECORDS": 527,
            "MASCOT": 527,
            "MASSACRE VIDEO": 528,
            "MASSACRE": 528,
            "MATCHBOX": 529,
            "MATRIX D": 530,
            "MAXAM": 531,
            "MAYA HOME ENTERTAINMENT": 532,
            "MAYA ENTERTAINMENT": 532,
            "MAYA HOME": 532,
            "MAYAT": 532,
            "MDG": 533,
            "MEDIA BLASTERS": 534,
            "MEDIA FACTORY": 535,
            "MEDIA TARGET DISTRIBUTION": 536,
            "MEDIA TARGET": 536,
            "MEDIAINVISION": 537,
            "MEDIATOON": 538,
            "MEDIATRES ESTUDIO": 539,
            "MEDIATRES STUDIO": 539,
            "MEDIATRES": 539,
            "MEDICI ARTS": 540,
            "MEDICI CLASSICS": 541,
            "MEDIUMRARE ENTERTAINMENT": 542,
            "MEDIUMRARE": 542,
            "MEDUSA": 543,
            "MEGASTAR": 544,
            "MEI AH": 545,
            "MELI MÉDIAS": 546,
            "MELI MEDIAS": 546,
            "MEMENTO FILMS": 547,
            "MEMENTO": 547,
            "MENEMSHA FILMS": 548,
            "MENEMSHA": 548,
            "MERCURY": 549,
            "MERCURY STUDIOS": 550,
            "MERGE SOFT PRODUCTIONS": 551,
            "MERGE PRODUCTIONS": 551,
            "MERGE SOFT": 551,
            "MERGE": 551,
            "METAL BLADE RECORDS": 552,
            "METAL BLADE": 552,
            "METEOR": 553,
            "METRO-GOLDWYN-MAYER": 554,
            "METRO GOLDWYN MAYER": 554,
            "METROGOLDWYNMAYER": 554,
            "METRODOME VIDEO": 555,
            "METRODOME": 555,
            "METROPOLITAN": 556,
            "MFA+": 557,
            "MFA": 557,
            "MIG FILMGROUP": 558,
            "MIG": 558,
            "MILESTONE": 559,
            "MILL CREEK ENTERTAINMENT": 560,
            "MILL CREEK": 560,
            "MILLENNIUM MEDIA": 561,
            "MILLENNIUM": 561,
            "MIRAGE ENTERTAINMENT": 562,
            "MIRAGE": 562,
            "MIRAMAX": 563,
            "MISTERIYA ZVUKA": 564,
            "MK2": 565,
            "MODE RECORDS": 566,
            "MODE": 566,
            "MOMENTUM PICTURES": 567,
            "MONDO HOME ENTERTAINMENT": 568,
            "MONDO ENTERTAINMENT": 568,
            "MONDO HOME": 568,
            "MONDO MACABRO": 569,
            "MONGREL MEDIA": 570,
            "MONOLIT": 571,
            "MONOLITH VIDEO": 572,
            "MONOLITH": 572,
            "MONSTER PICTURES": 573,
            "MONSTER": 573,
            "MONTEREY VIDEO": 574,
            "MONTEREY": 574,
            "MONUMENT RELEASING": 575,
            "MONUMENT": 575,
            "MORNINGSTAR": 576,
            "MORNING STAR": 576,
            "MOSERBAER": 577,
            "MOVIEMAX": 578,
            "MOVINSIDE": 579,
            "MPI MEDIA GROUP": 580,
            "MPI MEDIA": 580,
            "MPI": 580,
            "MR. BONGO FILMS": 581,
            "MR BONGO FILMS": 581,
            "MR BONGO": 581,
            "MRG (MERIDIAN)": 582,
            "MRG MERIDIAN": 582,
            "MRG": 582,
            "MERIDIAN": 582,
            "MUBI": 583,
            "MUG SHOT PRODUCTIONS": 584,
            "MUG SHOT": 584,
            "MULTIMUSIC": 585,
            "MULTI-MUSIC": 585,
            "MULTI MUSIC": 585,
            "MUSE": 586,
            "MUSIC BOX FILMS": 587,
            "MUSIC BOX": 587,
            "MUSICBOX": 587,
            "MUSIC BROKERS": 588,
            "MUSIC THEORIES": 589,
            "MUSIC VIDEO DISTRIBUTORS": 590,
            "MUSIC VIDEO": 590,
            "MUSTANG ENTERTAINMENT": 591,
            "MUSTANG": 591,
            "MVD VISUAL": 592,
            "MVD": 592,
            "MVD/VSC": 593,
            "MVL": 594,
            "MVM ENTERTAINMENT": 595,
            "MVM": 595,
            "MYNDFORM": 596,
            "MYSTIC NIGHT PICTURES": 597,
            "MYSTIC NIGHT": 597,
            "NAMELESS MEDIA": 598,
            "NAMELESS": 598,
            "NAPALM RECORDS": 599,
            "NAPALM": 599,
            "NATIONAL ENTERTAINMENT MEDIA": 600,
            "NATIONAL ENTERTAINMENT": 600,
            "NATIONAL MEDIA": 600,
            "NATIONAL FILM ARCHIVE": 601,
            "NATIONAL ARCHIVE": 601,
            "NATIONAL FILM": 601,
            "NATIONAL GEOGRAPHIC": 602,
            "NAT GEO TV": 602,
            "NAT GEO": 602,
            "NGO": 602,
            "NAXOS": 603,
            "NBCUNIVERSAL ENTERTAINMENT JAPAN": 604,
            "NBC UNIVERSAL ENTERTAINMENT JAPAN": 604,
            "NBCUNIVERSAL JAPAN": 604,
            "NBC UNIVERSAL JAPAN": 604,
            "NBC JAPAN": 604,
            "NBO ENTERTAINMENT": 605,
            "NBO": 605,
            "NEOS": 606,
            "NETFLIX": 607,
            "NETWORK": 608,
            "NEW BLOOD": 609,
            "NEW DISC": 610,
            "NEW KSM": 611,
            "NEW LINE CINEMA": 612,
            "NEW LINE": 612,
            "NEW MOVIE TRADING CO. LTD": 613,
            "NEW MOVIE TRADING CO LTD": 613,
            "NEW MOVIE TRADING CO": 613,
            "NEW MOVIE TRADING": 613,
            "NEW WAVE FILMS": 614,
            "NEW WAVE": 614,
            "NFI": 615,
            "NHK": 616,
            "NIPPONART": 617,
            "NIS AMERICA": 618,
            "NJUTAFILMS": 619,
            "NOBLE ENTERTAINMENT": 620,
            "NOBLE": 620,
            "NORDISK FILM": 621,
            "NORDISK": 621,
            "NORSK FILM": 622,
            "NORSK": 622,
            "NORTH AMERICAN MOTION PICTURES": 623,
            "NOS AUDIOVISUAIS": 624,
            "NOTORIOUS PICTURES": 625,
            "NOTORIOUS": 625,
            "NOVA MEDIA": 626,
            "NOVA": 626,
            "NOVA SALES AND DISTRIBUTION": 627,
            "NOVA SALES & DISTRIBUTION": 627,
            "NSM": 628,
            "NSM RECORDS": 629,
            "NUCLEAR BLAST": 630,
            "NUCLEUS FILMS": 631,
            "NUCLEUS": 631,
            "OBERLIN MUSIC": 632,
            "OBERLIN": 632,
            "OBRAS-PRIMAS DO CINEMA": 633,
            "OBRAS PRIMAS DO CINEMA": 633,
            "OBRASPRIMAS DO CINEMA": 633,
            "OBRAS-PRIMAS CINEMA": 633,
            "OBRAS PRIMAS CINEMA": 633,
            "OBRASPRIMAS CINEMA": 633,
            "OBRAS-PRIMAS": 633,
            "OBRAS PRIMAS": 633,
            "OBRASPRIMAS": 633,
            "ODEON": 634,
            "OFDB FILMWORKS": 635,
            "OFDB": 635,
            "OLIVE FILMS": 636,
            "OLIVE": 636,
            "ONDINE": 637,
            "ONSCREEN FILMS": 638,
            "ONSCREEN": 638,
            "OPENING DISTRIBUTION": 639,
            "OPERA AUSTRALIA": 640,
            "OPTIMUM HOME ENTERTAINMENT": 641,
            "OPTIMUM ENTERTAINMENT": 641,
            "OPTIMUM HOME": 641,
            "OPTIMUM": 641,
            "OPUS ARTE": 642,
            "ORANGE STUDIO": 643,
            "ORANGE": 643,
            "ORLANDO EASTWOOD FILMS": 644,
            "ORLANDO FILMS": 644,
            "ORLANDO EASTWOOD": 644,
            "ORLANDO": 644,
            "ORUSTAK PICTURES": 645,
            "ORUSTAK": 645,
            "OSCILLOSCOPE PICTURES": 646,
            "OSCILLOSCOPE": 646,
            "OUTPLAY": 647,
            "PALISADES TARTAN": 648,
            "PAN VISION": 649,
            "PANVISION": 649,
            "PANAMINT CINEMA": 650,
            "PANAMINT": 650,
            "PANDASTORM ENTERTAINMENT": 651,
            "PANDA STORM ENTERTAINMENT": 651,
            "PANDASTORM": 651,
            "PANDA STORM": 651,
            "PANDORA FILM": 652,
            "PANDORA": 652,
            "PANEGYRIC": 653,
            "PANORAMA": 654,
            "PARADE DECK FILMS": 655,
            "PARADE DECK": 655,
            "PARADISE": 656,
            "PARADISO FILMS": 657,
            "PARADOX": 658,
            "PARAMOUNT PICTURES": 659,
            "PARAMOUNT": 659,
            "PARIS FILMES": 660,
            "PARIS FILMS": 660,
            "PARIS": 660,
            "PARK CIRCUS": 661,
            "PARLOPHONE": 662,
            "PASSION RIVER": 663,
            "PATHE DISTRIBUTION": 664,
            "PATHE": 664,
            "PBS": 665,
            "PEACE ARCH TRINITY": 666,
            "PECCADILLO PICTURES": 667,
            "PEPPERMINT": 668,
            "PHASE 4 FILMS": 669,
            "PHASE 4": 669,
            "PHILHARMONIA BAROQUE": 670,
            "PICTURE HOUSE ENTERTAINMENT": 671,
            "PICTURE ENTERTAINMENT": 671,
            "PICTURE HOUSE": 671,
            "PICTURE": 671,
            "PIDAX": 672,
            "PINK FLOYD RECORDS": 673,
            "PINK FLOYD": 673,
            "PINNACLE FILMS": 674,
            "PINNACLE": 674,
            "PLAIN": 675,
            "PLATFORM ENTERTAINMENT LIMITED": 676,
            "PLATFORM ENTERTAINMENT LTD": 676,
            "PLATFORM ENTERTAINMENT LTD.": 676,
            "PLATFORM ENTERTAINMENT": 676,
            "PLATFORM": 676,
            "PLAYARTE": 677,
            "PLG UK CLASSICS": 678,
            "PLG UK": 678,
            "PLG": 678,
            "POLYBAND & TOPPIC VIDEO/WVG": 679,
            "POLYBAND AND TOPPIC VIDEO/WVG": 679,
            "POLYBAND & TOPPIC VIDEO WVG": 679,
            "POLYBAND & TOPPIC VIDEO AND WVG": 679,
            "POLYBAND & TOPPIC VIDEO & WVG": 679,
            "POLYBAND AND TOPPIC VIDEO WVG": 679,
            "POLYBAND AND TOPPIC VIDEO AND WVG": 679,
            "POLYBAND AND TOPPIC VIDEO & WVG": 679,
            "POLYBAND & TOPPIC VIDEO": 679,
            "POLYBAND AND TOPPIC VIDEO": 679,
            "POLYBAND & TOPPIC": 679,
            "POLYBAND AND TOPPIC": 679,
            "POLYBAND": 679,
            "WVG": 679,
            "POLYDOR": 680,
            "PONY": 681,
            "PONY CANYON": 682,
            "POTEMKINE": 683,
            "POWERHOUSE FILMS": 684,
            "POWERHOUSE": 684,
            "POWERSTATIOM": 685,
            "PRIDE & JOY": 686,
            "PRIDE AND JOY": 686,
            "PRINZ MEDIA": 687,
            "PRINZ": 687,
            "PRIS AUDIOVISUAIS": 688,
            "PRO VIDEO": 689,
            "PRO-VIDEO": 689,
            "PRO-MOTION": 690,
            "PRO MOTION": 690,
            "PROD. JRB": 691,
            "PROD JRB": 691,
            "PRODISC": 692,
            "PROKINO": 693,
            "PROVOGUE RECORDS": 694,
            "PROVOGUE": 694,
            "PROWARE": 695,
            "PULP VIDEO": 696,
            "PULP": 696,
            "PULSE VIDEO": 697,
            "PULSE": 697,
            "PURE AUDIO RECORDINGS": 698,
            "PURE AUDIO": 698,
            "PURE FLIX ENTERTAINMENT": 699,
            "PURE FLIX": 699,
            "PURE ENTERTAINMENT": 699,
            "PYRAMIDE VIDEO": 700,
            "PYRAMIDE": 700,
            "QUALITY FILMS": 701,
            "QUALITY": 701,
            "QUARTO VALLEY RECORDS": 702,
            "QUARTO VALLEY": 702,
            "QUESTAR": 703,
            "R SQUARED FILMS": 704,
            "R SQUARED": 704,
            "RAPID EYE MOVIES": 705,
            "RAPID EYE": 705,
            "RARO VIDEO": 706,
            "RARO": 706,
            "RAROVIDEO U.S.": 707,
            "RAROVIDEO US": 707,
            "RARO VIDEO US": 707,
            "RARO VIDEO U.S.": 707,
            "RARO U.S.": 707,
            "RARO US": 707,
            "RAVEN BANNER RELEASING": 708,
            "RAVEN BANNER": 708,
            "RAVEN": 708,
            "RAZOR DIGITAL ENTERTAINMENT": 709,
            "RAZOR DIGITAL": 709,
            "RCA": 710,
            "RCO LIVE": 711,
            "RCO": 711,
            "RCV": 712,
            "REAL GONE MUSIC": 713,
            "REAL GONE": 713,
            "REANIMEDIA": 714,
            "REANI MEDIA": 714,
            "REDEMPTION": 715,
            "REEL": 716,
            "RELIANCE HOME VIDEO & GAMES": 717,
            "RELIANCE HOME VIDEO AND GAMES": 717,
            "RELIANCE HOME VIDEO": 717,
            "RELIANCE VIDEO": 717,
            "RELIANCE HOME": 717,
            "RELIANCE": 717,
            "REM CULTURE": 718,
            "REMAIN IN LIGHT": 719,
            "REPRISE": 720,
            "RESEN": 721,
            "RETROMEDIA": 722,
            "REVELATION FILMS LTD.": 723,
            "REVELATION FILMS LTD": 723,
            "REVELATION FILMS": 723,
            "REVELATION LTD.": 723,
            "REVELATION LTD": 723,
            "REVELATION": 723,
            "REVOLVER ENTERTAINMENT": 724,
            "REVOLVER": 724,
            "RHINO MUSIC": 725,
            "RHINO": 725,
            "RHV": 726,
            "RIGHT STUF": 727,
            "RIMINI EDITIONS": 728,
            "RISING SUN MEDIA": 729,
            "RLJ ENTERTAINMENT": 730,
            "RLJ": 730,
            "ROADRUNNER RECORDS": 731,
            "ROADSHOW ENTERTAINMENT": 732,
            "ROADSHOW": 732,
            "RONE": 733,
            "RONIN FLIX": 734,
            "ROTANA HOME ENTERTAINMENT": 735,
            "ROTANA ENTERTAINMENT": 735,
            "ROTANA HOME": 735,
            "ROTANA": 735,
            "ROUGH TRADE": 736,
            "ROUNDER": 737,
            "SAFFRON HILL FILMS": 738,
            "SAFFRON HILL": 738,
            "SAFFRON": 738,
            "SAMUEL GOLDWYN FILMS": 739,
            "SAMUEL GOLDWYN": 739,
            "SAN FRANCISCO SYMPHONY": 740,
            "SANDREW METRONOME": 741,
            "SAPHRANE": 742,
            "SAVOR": 743,
            "SCANBOX ENTERTAINMENT": 744,
            "SCANBOX": 744,
            "SCENIC LABS": 745,
            "SCHRÖDERMEDIA": 746,
            "SCHRODERMEDIA": 746,
            "SCHRODER MEDIA": 746,
            "SCORPION RELEASING": 747,
            "SCORPION": 747,
            "SCREAM TEAM RELEASING": 748,
            "SCREAM TEAM": 748,
            "SCREEN MEDIA": 749,
            "SCREEN": 749,
            "SCREENBOUND PICTURES": 750,
            "SCREENBOUND": 750,
            "SCREENWAVE MEDIA": 751,
            "SCREENWAVE": 751,
            "SECOND RUN": 752,
            "SECOND SIGHT": 753,
            "SEEDSMAN GROUP": 754,
            "SELECT VIDEO": 755,
            "SELECTA VISION": 756,
            "SENATOR": 757,
            "SENTAI FILMWORKS": 758,
            "SENTAI": 758,
            "SEVEN7": 759,
            "SEVERIN FILMS": 760,
            "SEVERIN": 760,
            "SEVILLE": 761,
            "SEYONS ENTERTAINMENT": 762,
            "SEYONS": 762,
            "SF STUDIOS": 763,
            "SGL ENTERTAINMENT": 764,
            "SGL": 764,
            "SHAMELESS": 765,
            "SHAMROCK MEDIA": 766,
            "SHAMROCK": 766,
            "SHANGHAI EPIC MUSIC ENTERTAINMENT": 767,
            "SHANGHAI EPIC ENTERTAINMENT": 767,
            "SHANGHAI EPIC MUSIC": 767,
            "SHANGHAI MUSIC ENTERTAINMENT": 767,
            "SHANGHAI ENTERTAINMENT": 767,
            "SHANGHAI MUSIC": 767,
            "SHANGHAI": 767,
            "SHEMAROO": 768,
            "SHOCHIKU": 769,
            "SHOCK": 770,
            "SHOGAKU KAN": 771,
            "SHOUT FACTORY": 772,
            "SHOUT! FACTORY": 772,
            "SHOUT": 772,
            "SHOUT!": 772,
            "SHOWBOX": 773,
            "SHOWTIME ENTERTAINMENT": 774,
            "SHOWTIME": 774,
            "SHRIEK SHOW": 775,
            "SHUDDER": 776,
            "SIDONIS": 777,
            "SIDONIS CALYSTA": 778,
            "SIGNAL ONE ENTERTAINMENT": 779,
            "SIGNAL ONE": 779,
            "SIGNATURE ENTERTAINMENT": 780,
            "SIGNATURE": 780,
            "SILVER VISION": 781,
            "SINISTER FILM": 782,
            "SINISTER": 782,
            "SIREN VISUAL ENTERTAINMENT": 783,
            "SIREN VISUAL": 783,
            "SIREN ENTERTAINMENT": 783,
            "SIREN": 783,
            "SKANI": 784,
            "SKY DIGI": 785,
            "SLASHER // VIDEO": 786,
            "SLASHER / VIDEO": 786,
            "SLASHER VIDEO": 786,
            "SLASHER": 786,
            "SLOVAK FILM INSTITUTE": 787,
            "SLOVAK FILM": 787,
            "SFI": 787,
            "SM LIFE DESIGN GROUP": 788,
            "SMOOTH PICTURES": 789,
            "SMOOTH": 789,
            "SNAPPER MUSIC": 790,
            "SNAPPER": 790,
            "SODA PICTURES": 791,
            "SODA": 791,
            "SONO LUMINUS": 792,
            "SONY MUSIC": 793,
            "SONY PICTURES": 794,
            "SONY": 794,
            "SONY PICTURES CLASSICS": 795,
            "SONY CLASSICS": 795,
            "SOUL MEDIA": 796,
            "SOUL": 796,
            "SOULFOOD MUSIC DISTRIBUTION": 797,
            "SOULFOOD DISTRIBUTION": 797,
            "SOULFOOD MUSIC": 797,
            "SOULFOOD": 797,
            "SOYUZ": 798,
            "SPECTRUM": 799,
            "SPENTZOS FILM": 800,
            "SPENTZOS": 800,
            "SPIRIT ENTERTAINMENT": 801,
            "SPIRIT": 801,
            "SPIRIT MEDIA GMBH": 802,
            "SPIRIT MEDIA": 802,
            "SPLENDID ENTERTAINMENT": 803,
            "SPLENDID FILM": 804,
            "SPO": 805,
            "SQUARE ENIX": 806,
            "SRI BALAJI VIDEO": 807,
            "SRI BALAJI": 807,
            "SRI": 807,
            "SRI VIDEO": 807,
            "SRS CINEMA": 808,
            "SRS": 808,
            "SSO RECORDINGS": 809,
            "SSO": 809,
            "ST2 MUSIC": 810,
            "ST2": 810,
            "STAR MEDIA ENTERTAINMENT": 811,
            "STAR ENTERTAINMENT": 811,
            "STAR MEDIA": 811,
            "STAR": 811,
            "STARLIGHT": 812,
            "STARZ / ANCHOR BAY": 813,
            "STARZ ANCHOR BAY": 813,
            "STARZ": 813,
            "ANCHOR BAY": 813,
            "STER KINEKOR": 814,
            "STERLING ENTERTAINMENT": 815,
            "STERLING": 815,
            "STINGRAY": 816,
            "STOCKFISCH RECORDS": 817,
            "STOCKFISCH": 817,
            "STRAND RELEASING": 818,
            "STRAND": 818,
            "STUDIO 4K": 819,
            "STUDIO CANAL": 820,
            "STUDIO GHIBLI": 821,
            "GHIBLI": 821,
            "STUDIO HAMBURG ENTERPRISES": 822,
            "HAMBURG ENTERPRISES": 822,
            "STUDIO HAMBURG": 822,
            "HAMBURG": 822,
            "STUDIO S": 823,
            "SUBKULTUR ENTERTAINMENT": 824,
            "SUBKULTUR": 824,
            "SUEVIA FILMS": 825,
            "SUEVIA": 825,
            "SUMMIT ENTERTAINMENT": 826,
            "SUMMIT": 826,
            "SUNFILM ENTERTAINMENT": 827,
            "SUNFILM": 827,
            "SURROUND RECORDS": 828,
            "SURROUND": 828,
            "SVENSK FILMINDUSTRI": 829,
            "SVENSK": 829,
            "SWEN FILMES": 830,
            "SWEN FILMS": 830,
            "SWEN": 830,
            "SYNAPSE FILMS": 831,
            "SYNAPSE": 831,
            "SYNDICADO": 832,
            "SYNERGETIC": 833,
            "T- SERIES": 834,
            "T-SERIES": 834,
            "T SERIES": 834,
            "TSERIES": 834,
            "T.V.P.": 835,
            "TVP": 835,
            "TACET RECORDS": 836,
            "TACET": 836,
            "TAI SENG": 837,
            "TAI SHENG": 838,
            "TAKEONE": 839,
            "TAKESHOBO": 840,
            "TAMASA DIFFUSION": 841,
            "TC ENTERTAINMENT": 842,
            "TC": 842,
            "TDK": 843,
            "TEAM MARKETING": 844,
            "TEATRO REAL": 845,
            "TEMA DISTRIBUCIONES": 846,
            "TEMPE DIGITAL": 847,
            "TF1 VIDÉO": 848,
            "TF1 VIDEO": 848,
            "TF1": 848,
            "THE BLU": 849,
            "BLU": 849,
            "THE ECSTASY OF FILMS": 850,
            "THE FILM DETECTIVE": 851,
            "FILM DETECTIVE": 851,
            "THE JOKERS": 852,
            "JOKERS": 852,
            "THE ON": 853,
            "ON": 853,
            "THIMFILM": 854,
            "THIM FILM": 854,
            "THIM": 854,
            "THIRD WINDOW FILMS": 855,
            "THIRD WINDOW": 855,
            "3RD WINDOW FILMS": 855,
            "3RD WINDOW": 855,
            "THUNDERBEAN ANIMATION": 856,
            "THUNDERBEAN": 856,
            "THUNDERBIRD RELEASING": 857,
            "THUNDERBIRD": 857,
            "TIBERIUS FILM": 858,
            "TIME LIFE": 859,
            "TIMELESS MEDIA GROUP": 860,
            "TIMELESS MEDIA": 860,
            "TIMELESS GROUP": 860,
            "TIMELESS": 860,
            "TLA RELEASING": 861,
            "TLA": 861,
            "TOBIS FILM": 862,
            "TOBIS": 862,
            "TOEI": 863,
            "TOHO": 864,
            "TOKYO SHOCK": 865,
            "TOKYO": 865,
            "TONPOOL MEDIEN GMBH": 866,
            "TONPOOL MEDIEN": 866,
            "TOPICS ENTERTAINMENT": 867,
            "TOPICS": 867,
            "TOUCHSTONE PICTURES": 868,
            "TOUCHSTONE": 868,
            "TRANSMISSION FILMS": 869,
            "TRANSMISSION": 869,
            "TRAVEL VIDEO STORE": 870,
            "TRIART": 871,
            "TRIGON FILM": 872,
            "TRIGON": 872,
            "TRINITY HOME ENTERTAINMENT": 873,
            "TRINITY ENTERTAINMENT": 873,
            "TRINITY HOME": 873,
            "TRINITY": 873,
            "TRIPICTURES": 874,
            "TRI-PICTURES": 874,
            "TRI PICTURES": 874,
            "TROMA": 875,
            "TURBINE MEDIEN": 876,
            "TURTLE RECORDS": 877,
            "TURTLE": 877,
            "TVA FILMS": 878,
            "TVA": 878,
            "TWILIGHT TIME": 879,
            "TWILIGHT": 879,
            "TT": 879,
            "TWIN CO., LTD.": 880,
            "TWIN CO, LTD.": 880,
            "TWIN CO., LTD": 880,
            "TWIN CO, LTD": 880,
            "TWIN CO LTD": 880,
            "TWIN LTD": 880,
            "TWIN CO.": 880,
            "TWIN CO": 880,
            "TWIN": 880,
            "UCA": 881,
            "UDR": 882,
            "UEK": 883,
            "UFA/DVD": 884,
            "UFA DVD": 884,
            "UFADVD": 884,
            "UGC PH": 885,
            "ULTIMATE3DHEAVEN": 886,
            "ULTRA": 887,
            "UMBRELLA ENTERTAINMENT": 888,
            "UMBRELLA": 888,
            "UMC": 889,
            "UNCORK'D ENTERTAINMENT": 890,
            "UNCORKD ENTERTAINMENT": 890,
            "UNCORK D ENTERTAINMENT": 890,
            "UNCORK'D": 890,
            "UNCORK D": 890,
            "UNCORKD": 890,
            "UNEARTHED FILMS": 891,
            "UNEARTHED": 891,
            "UNI DISC": 892,
            "UNIMUNDOS": 893,
            "UNITEL": 894,
            "UNIVERSAL MUSIC": 895,
            "UNIVERSAL SONY PICTURES HOME ENTERTAINMENT": 896,
            "UNIVERSAL SONY PICTURES ENTERTAINMENT": 896,
            "UNIVERSAL SONY PICTURES HOME": 896,
            "UNIVERSAL SONY PICTURES": 896,
            "UNIVERSAL HOME ENTERTAINMENT": 896,
            "UNIVERSAL ENTERTAINMENT": 896,
            "UNIVERSAL HOME": 896,
            "UNIVERSAL STUDIOS": 897,
            "UNIVERSAL": 897,
            "UNIVERSE LASER & VIDEO CO.": 898,
            "UNIVERSE LASER AND VIDEO CO.": 898,
            "UNIVERSE LASER & VIDEO CO": 898,
            "UNIVERSE LASER AND VIDEO CO": 898,
            "UNIVERSE LASER CO.": 898,
            "UNIVERSE LASER CO": 898,
            "UNIVERSE LASER": 898,
            "UNIVERSUM FILM": 899,
            "UNIVERSUM": 899,
            "UTV": 900,
            "VAP": 901,
            "VCI": 902,
            "VENDETTA FILMS": 903,
            "VENDETTA": 903,
            "VERSÁTIL HOME VIDEO": 904,
            "VERSÁTIL VIDEO": 904,
            "VERSÁTIL HOME": 904,
            "VERSÁTIL": 904,
            "VERSATIL HOME VIDEO": 904,
            "VERSATIL VIDEO": 904,
            "VERSATIL HOME": 904,
            "VERSATIL": 904,
            "VERTICAL ENTERTAINMENT": 905,
            "VERTICAL": 905,
            "VÉRTICE 360º": 906,
            "VÉRTICE 360": 906,
            "VERTICE 360o": 906,
            "VERTICE 360": 906,
            "VERTIGO BERLIN": 907,
            "VÉRTIGO FILMS": 908,
            "VÉRTIGO": 908,
            "VERTIGO FILMS": 908,
            "VERTIGO": 908,
            "VERVE PICTURES": 909,
            "VIA VISION ENTERTAINMENT": 910,
            "VIA VISION": 910,
            "VICOL ENTERTAINMENT": 911,
            "VICOL": 911,
            "VICOM": 912,
            "VICTOR ENTERTAINMENT": 913,
            "VICTOR": 913,
            "VIDEA CDE": 914,
            "VIDEO FILM EXPRESS": 915,
            "VIDEO FILM": 915,
            "VIDEO EXPRESS": 915,
            "VIDEO MUSIC, INC.": 916,
            "VIDEO MUSIC, INC": 916,
            "VIDEO MUSIC INC.": 916,
            "VIDEO MUSIC INC": 916,
            "VIDEO MUSIC": 916,
            "VIDEO SERVICE CORP.": 917,
            "VIDEO SERVICE CORP": 917,
            "VIDEO SERVICE": 917,
            "VIDEO TRAVEL": 918,
            "VIDEOMAX": 919,
            "VIDEO MAX": 919,
            "VII PILLARS ENTERTAINMENT": 920,
            "VII PILLARS": 920,
            "VILLAGE FILMS": 921,
            "VINEGAR SYNDROME": 922,
            "VINEGAR": 922,
            "VS": 922,
            "VINNY MOVIES": 923,
            "VINNY": 923,
            "VIRGIL FILMS & ENTERTAINMENT": 924,
            "VIRGIL FILMS AND ENTERTAINMENT": 924,
            "VIRGIL ENTERTAINMENT": 924,
            "VIRGIL FILMS": 924,
            "VIRGIL": 924,
            "VIRGIN RECORDS": 925,
            "VIRGIN": 925,
            "VISION FILMS": 926,
            "VISION": 926,
            "VISUAL ENTERTAINMENT GROUP": 927,
            "VISUAL GROUP": 927,
            "VISUAL ENTERTAINMENT": 927,
            "VISUAL": 927,
            "VIVENDI VISUAL ENTERTAINMENT": 928,
            "VIVENDI VISUAL": 928,
            "VIVENDI": 928,
            "VIZ PICTURES": 929,
            "VIZ": 929,
            "VLMEDIA": 930,
            "VL MEDIA": 930,
            "VL": 930,
            "VOLGA": 931,
            "VVS FILMS": 932,
            "VVS": 932,
            "VZ HANDELS GMBH": 933,
            "VZ HANDELS": 933,
            "WARD RECORDS": 934,
            "WARD": 934,
            "WARNER BROS.": 935,
            "WARNER BROS": 935,
            "WARNER ARCHIVE": 935,
            "WARNER ARCHIVE COLLECTION": 935,
            "WAC": 935,
            "WARNER": 935,
            "WARNER MUSIC": 936,
            "WEA": 937,
            "WEINSTEIN COMPANY": 938,
            "WEINSTEIN": 938,
            "WELL GO USA": 939,
            "WELL GO": 939,
            "WELTKINO FILMVERLEIH": 940,
            "WEST VIDEO": 941,
            "WEST": 941,
            "WHITE PEARL MOVIES": 942,
            "WHITE PEARL": 942,
            "WICKED-VISION MEDIA": 943,
            "WICKED VISION MEDIA": 943,
            "WICKEDVISION MEDIA": 943,
            "WICKED-VISION": 943,
            "WICKED VISION": 943,
            "WICKEDVISION": 943,
            "WIENERWORLD": 944,
            "WILD BUNCH": 945,
            "WILD EYE RELEASING": 946,
            "WILD EYE": 946,
            "WILD SIDE VIDEO": 947,
            "WILD SIDE": 947,
            "WME": 948,
            "WOLFE VIDEO": 949,
            "WOLFE": 949,
            "WORD ON FIRE": 950,
            "WORKS FILM GROUP": 951,
            "WORLD WRESTLING": 952,
            "WVG MEDIEN": 953,
            "WWE STUDIOS": 954,
            "WWE": 954,
            "X RATED KULT": 955,
            "X-RATED KULT": 955,
            "X RATED CULT": 955,
            "X-RATED CULT": 955,
            "X RATED": 955,
            "X-RATED": 955,
            "XCESS": 956,
            "XLRATOR": 957,
            "XT VIDEO": 958,
            "XT": 958,
            "YAMATO VIDEO": 959,
            "YAMATO": 959,
            "YASH RAJ FILMS": 960,
            "YASH RAJS": 960,
            "ZEITGEIST FILMS": 961,
            "ZEITGEIST": 961,
            "ZENITH PICTURES": 962,
            "ZENITH": 962,
            "ZIMA": 963,
            "ZYLO": 964,
            "ZYX MUSIC": 965,
            "ZYX": 965,
        }

        return self._mapped_identifier(
            distributor_map, distributor, reverse, distributor_id
        )

    @staticmethod
    def _display_tracker_name(tracker_name: str | None) -> str:
        return tracker_name or "Tracker"

    @staticmethod
    def _normalized_imdb_display(imdb: str | int | None) -> str:
        return str(imdb).zfill(7) if imdb else ""

    @staticmethod
    def _tracker_id_message(label: str, value: Any) -> str:
        return f"{label}: {value}" if value else ""

    @staticmethod
    def _tracker_id_messages(
        tmdb: str | int | None,
        imdb: str,
        tvdb: str | int | None,
        mal: str | int | None,
    ) -> list[str]:
        entries = (
            Common._tracker_id_message("TMDb ID", tmdb),
            Common._tracker_id_message(
                "IMDb ID",
                f"https://www.imdb.com/title/tt{imdb}" if imdb else "",
            ),
            Common._tracker_id_message("TVDb ID", tvdb),
            Common._tracker_id_message("MAL ID", mal),
        )
        return list(filter(None, entries))

    @staticmethod
    def _log_tracker_ids(
        tracker_name: str,
        tmdb: str | int | None,
        imdb: str,
        tvdb: str | int | None,
        mal: str | int | None,
    ) -> None:
        messages = Common._tracker_id_messages(tmdb, imdb, tvdb, mal)
        if not messages:
            return
        logger.info(f"[cyan]Found the following IDs on {tracker_name}:")
        for message in messages:
            logger.info(message)

    async def _confirm_tracker_id_selection(self, tracker_name: str) -> bool:
        try:
            selection = await prompt_in_thread(
                cli_ui.ask_string,
                f"Do you want to use these IDs from {tracker_name}? (Y/n): ",
                default="",
            )
        except KeyboardInterrupt, EOFError:
            raise OperationAbortedError(
                "Tracker metadata selection was cancelled by the user."
            ) from None
        return str(selection or "").strip().lower() in {"", "y", "yes"}

    async def prompt_user_for_id_selection(
        self,
        meta: Meta,
        tmdb: str | int | None = None,
        imdb: str | int | None = None,
        tvdb: str | int | None = None,
        mal: str | int | None = None,
        filename: str | list[str] | None = None,
        tracker_name: str | None = None,
    ) -> bool:
        display_name = self._display_tracker_name(tracker_name)
        imdb_display = self._normalized_imdb_display(imdb)
        self._log_tracker_ids(display_name, tmdb, imdb_display, tvdb, mal)
        if filename:
            logger.info(f"Filename: {filename}")
        if meta.unattended:
            return True
        return await self._confirm_tracker_id_selection(display_name)

    async def prompt_user_for_confirmation(
        self, message: str, meta: Meta | None = None
    ) -> bool:
        if meta and meta.unattended:
            return bool(meta.unattended_confirm)
        response = (
            (
                await prompt_in_thread(
                    cli_ui.ask_string, f"{message} (Y/n): ", default=""
                )
                or ""
            )
            .strip()
            .lower()
        )
        return response == "" or response == "y"

    async def _apply_region_id(self, meta: Meta, region_id: Any) -> None:
        if meta.region or not region_id:
            return
        region_name = await self.unit3d_region_ids(
            reverse=True, region_id=region_id
        )
        if not region_name:
            return
        meta.region = region_name
        logger.debug(
            f"[green]Mapped region_id {region_id} to '{region_name}'[/green]"
        )

    async def _apply_distributor_id(
        self, meta: Meta, distributor_id: Any
    ) -> None:
        if meta.distributor or not distributor_id:
            return
        distributor_name = await self.unit3d_distributor_ids(
            reverse=True, distributor_id=distributor_id
        )
        if not distributor_name:
            return
        meta.distributor = distributor_name
        logger.debug(
            f"[green]Mapped distributor_id {distributor_id} to '{distributor_name}'[/green]"
        )

    async def _apply_region_distributor(
        self, meta: Meta, attributes: dict[str, Any]
    ) -> None:
        region_id = attributes.get("region_id", 0)
        distributor_id = attributes.get("distributor_id", 0)
        logger.debug(f"[blue]Region ID: {region_id}[/blue]")
        logger.debug(f"[blue]Distributor ID: {distributor_id}[/blue]")
        await self._apply_region_id(meta, region_id)
        await self._apply_distributor_id(meta, distributor_id)

    def _tracker_api_key(self, tracker: str) -> str:
        trackers = self.config.get("TRACKERS", {})
        tracker_map = (
            cast(dict[str, Any], trackers)
            if isinstance(trackers, dict)
            else {}
        )
        raw_config = tracker_map.get(tracker, {})
        config = (
            cast(dict[str, Any], raw_config)
            if isinstance(raw_config, dict)
            else {}
        )
        raw_key = config.get("api_key")
        return str(raw_key).strip() if raw_key else ""

    def _unit3d_auth(
        self, tracker: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        api_key = self._tracker_api_key(tracker)
        return (
            {"api_token": api_key},
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    async def _unit3d_json_request(
        self,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
        request_timeout: float,
        operation: str,
    ) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.get(
                    url=url, params=params, headers=headers
                )
            payload = response.json()
        except (httpx.RequestError, httpx.TimeoutException) as error:
            logger.info(
                f"[yellow]Request error in {operation}: {error}[/yellow]"
            )
            return None
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return cast(dict[str, Any], payload)

    @staticmethod
    def _unit3d_attributes_from_value(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        attributes = cast(dict[str, Any], value).get("attributes", {})
        return (
            cast(dict[str, Any], attributes)
            if isinstance(attributes, dict)
            else None
        )

    @classmethod
    def _unit3d_list_attributes(cls, data: Any) -> dict[str, Any] | None:
        if not isinstance(data, list) or not data:
            return None
        return cls._unit3d_attributes_from_value(cast(list[Any], data)[0])

    @staticmethod
    def _unit3d_direct_attributes(
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        attributes = payload.get("attributes", {})
        if not isinstance(attributes, dict) or not attributes:
            return None
        return cast(dict[str, Any], attributes)

    @classmethod
    def _unit3d_attributes(
        cls, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        data = payload.get("data", [])
        if data == "404":
            logger.info(
                "[yellow]No data found (404). Returning None.[/yellow]"
            )
            return None
        listed = cls._unit3d_list_attributes(data)
        return (
            listed
            if listed is not None
            else cls._unit3d_direct_attributes(payload)
        )

    async def unit3d_region_distributor(
        self, meta: Meta, tracker: str, torrent_url: str, id: str = ""
    ) -> None:
        """Get region and distributor information from API response."""
        params, headers = self._unit3d_auth(tracker)
        payload = await self._unit3d_json_request(
            f"{torrent_url}{id}",
            params,
            headers,
            30.0,
            "unit3d_region_distributor",
        )
        if payload is None:
            return
        try:
            attributes = self._unit3d_attributes(payload)
            if attributes is not None:
                await self._apply_region_distributor(meta, attributes)
        except Exception as error:
            console.print_exception()
            logger.info(
                f"[yellow]Invalid Response from {tracker} API. Error: {error!s}[/yellow]"
            )

    @staticmethod
    def _empty_unit3d_torrent_info() -> tuple[
        int | None,
        int | None,
        int | None,
        int | None,
        str | None,
        str | None,
        str | None,
        list[dict[str, str]],
        str | list[str] | None,
    ]:
        return None, None, None, None, None, None, None, [], None

    @staticmethod
    def _unit3d_search_target(
        file_name: str | list[str] | None,
        torrent_id: str | int | None,
        torrent_url: str,
        search_url: str,
    ) -> tuple[str, dict[str, Any]] | None:
        if file_name:
            logger.debug(
                f"[green]Searching by file name: [bold yellow]{file_name}[/bold yellow]"
            )
            return search_url, {"file_name": file_name}
        if torrent_id:
            url = f"{torrent_url}{torrent_id}"
            logger.debug(
                f"[green]Searching by ID: [bold yellow]{torrent_id}[/bold yellow] via {url}"
            )
            return url, {}
        logger.debug("[red]No ID or file name provided for search.[/red]")
        return None

    @staticmethod
    def _log_unit3d_lookup(
        tracker: str,
        torrent_id: str | int | None,
        public_torrent_url: str | None,
    ) -> None:
        if torrent_id and public_torrent_url:
            logger.info(
                f"Searching for information on [bold cyan]{tracker}[/bold cyan] "
                f"({public_torrent_url.rstrip('/')}/{torrent_id})"
            )
            return
        logger.info(
            f"Searching for information on [bold cyan]{tracker}[/bold cyan]"
        )

    async def _unit3d_torrent_payload(
        self,
        tracker: str,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
        torrent_id: str | int | None,
        public_torrent_url: str | None,
    ) -> dict[str, Any] | None:
        self._log_unit3d_lookup(tracker, torrent_id, public_torrent_url)
        return await self._unit3d_json_request(
            url,
            params,
            headers,
            5.0,
            "unit3d_torrent_info",
        )

    @staticmethod
    def _unit3d_numeric_attribute(attributes: dict[str, Any], key: str) -> int:
        raw = attributes.get(key)
        if raw in (None, "", 0, "0"):
            return 0
        return int(raw)

    @staticmethod
    def _unit3d_core_attributes(
        attributes: dict[str, Any],
    ) -> tuple[int, int, int, int, str | None, str | None, str | None]:
        return (
            Common._unit3d_numeric_attribute(attributes, "tmdb_id"),
            Common._unit3d_numeric_attribute(attributes, "imdb_id"),
            Common._unit3d_numeric_attribute(attributes, "tvdb_id"),
            Common._unit3d_numeric_attribute(attributes, "mal_id"),
            str(attributes.get("description"))
            if attributes.get("description") is not None
            else None,
            str(attributes.get("category"))
            if attributes.get("category") is not None
            else None,
            str(attributes.get("info_hash"))
            if attributes.get("info_hash") is not None
            else None,
        )

    @staticmethod
    def _unit3d_payload_has_list_data(payload: dict[str, Any]) -> bool:
        data = payload.get("data")
        return isinstance(data, list) and bool(data)

    @staticmethod
    def _unit3d_file_name_values(attributes: dict[str, Any]) -> list[str]:
        files = attributes.get("files", [])
        if not isinstance(files, list):
            return []
        values: list[str] = []
        for item in cast(list[Any], files)[:5]:
            if not isinstance(item, dict):
                continue
            name = cast(dict[str, Any], item).get("name")
            if name:
                values.append(str(name))
        return values

    @classmethod
    def _unit3d_direct_file_names(
        cls,
        attributes: dict[str, Any],
        current: str | list[str] | None,
        direct_payload: bool,
    ) -> str | list[str] | None:
        if not direct_payload:
            return current
        names = cls._unit3d_file_name_values(attributes)
        if len(names) == 1:
            return names[0]
        return names if names else current

    async def _apply_unit3d_disc_location(
        self, meta: Meta, attributes: dict[str, Any]
    ) -> None:
        if meta.is_disc not in ("BDMV", "DVD"):
            return
        await self._apply_region_distributor(meta, attributes)

    async def _unit3d_ids_accepted(
        self,
        meta: Meta,
        tracker: str,
        torrent_id: str | int | None,
        tmdb: int,
        imdb: int,
        tvdb: int,
        mal: int,
        file_name: str | list[str] | None,
    ) -> bool:
        if torrent_id or not any((tmdb, imdb, tvdb)):
            return True
        accepted = await self.prompt_user_for_id_selection(
            meta,
            tmdb,
            imdb,
            tvdb,
            mal,
            file_name,
            tracker_name=tracker,
        )
        if not accepted:
            logger.info("[yellow]User chose to skip based on IDs.[/yellow]")
        return accepted

    @staticmethod
    def _store_tracker_raw_description(
        meta: Meta, tracker: str, description: str
    ) -> None:
        raw = getattr(meta, "tracker_description_raw", {})
        descriptions = (
            cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        )
        descriptions[tracker] = description
        meta.tracker_description_raw = descriptions

    @staticmethod
    def _clean_unit3d_description(
        description: str, torrent_url: str
    ) -> tuple[str, list[dict[str, str]]]:
        return BBCODE().clean_unit3d_description(description, torrent_url)

    @staticmethod
    def _review_unit3d_description(description: str) -> str | None:
        logger.info(
            "[cyan]Do you want to edit, discard or keep the description?[/cyan]"
        )
        choice = cli_ui.ask_string(
            "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is:"
        )
        normalized = str(choice or "").lower()
        if normalized == "d":
            logger.info("[yellow]Description discarded.[/yellow]")
            return None
        if normalized != "e":
            logger.info("[green]Keeping the original description.[/green]")
            return description
        edited = cast(str | None, click.edit(cast(Any, description)))
        return edited.strip() if edited else description

    @staticmethod
    def _visible_unit3d_images(
        meta: Meta, images: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        return images if meta.keep_images else []

    @staticmethod
    def _log_unit3d_description(tracker: str, description: str) -> None:
        logger.info(f"[green]Successfully grabbed description from {tracker}")
        logger.info(
            f"Extracted description: \n\n{description}\n\n",
            extra={"markup": False, "highlighter": None},
        )

    def _unit3d_description_result(
        self,
        meta: Meta,
        tracker: str,
        torrent_url: str,
        description: str | None,
        skip_tracker_descriptions: bool,
    ) -> tuple[str | None, list[dict[str, str]]]:
        if not description:
            return description, []
        self._store_tracker_raw_description(meta, tracker, description)
        cleaned, images = self._clean_unit3d_description(
            description, torrent_url
        )
        visible_images = self._visible_unit3d_images(meta, images)
        if skip_tracker_descriptions:
            return "", visible_images
        self._log_unit3d_description(tracker, cleaned)
        if meta.unattended:
            return cleaned, images
        return self._review_unit3d_description(cleaned), visible_images

    @staticmethod
    def _unit3d_direct_payload(
        payload: dict[str, Any], torrent_id: str | int | None
    ) -> bool:
        return bool(torrent_id) and not Common._unit3d_payload_has_list_data(
            payload
        )

    def _unit3d_resolved_file_name(
        self,
        attributes: dict[str, Any],
        payload: dict[str, Any],
        torrent_id: str | int | None,
        current: str | list[str] | None,
    ) -> str | list[str] | None:
        direct_payload = self._unit3d_direct_payload(payload, torrent_id)
        resolved = self._unit3d_direct_file_names(
            attributes, current, direct_payload
        )
        if direct_payload:
            logger.debug(f"[blue]Extracted filename(s): {resolved}[/blue]")
        return resolved

    @staticmethod
    def _unit3d_info_tuple(
        core: tuple[int, int, int, int, str | None, str | None, str | None],
        description: str | None,
        images: list[dict[str, str]],
        file_name: str | list[str] | None,
    ) -> tuple[
        int | None,
        int | None,
        int | None,
        int | None,
        str | None,
        str | None,
        str | None,
        list[dict[str, str]],
        str | list[str] | None,
    ]:
        tmdb, imdb, tvdb, mal, _description, category, infohash = core
        return (
            tmdb,
            imdb,
            tvdb,
            mal,
            description,
            category,
            infohash,
            images,
            file_name,
        )

    async def _process_unit3d_torrent_payload(
        self,
        payload: dict[str, Any],
        meta: Meta,
        tracker: str,
        torrent_url: str,
        torrent_id: str | int | None,
        file_name: str | list[str] | None,
        skip_tracker_descriptions: bool,
    ) -> tuple[
        int | None,
        int | None,
        int | None,
        int | None,
        str | None,
        str | None,
        str | None,
        list[dict[str, str]],
        str | list[str] | None,
    ]:
        attributes = self._unit3d_attributes(payload)
        if attributes is None:
            return self._empty_unit3d_torrent_info()
        core = self._unit3d_core_attributes(attributes)
        tmdb, imdb, tvdb, mal, description, _category, _infohash = core
        await self._apply_unit3d_disc_location(meta, attributes)
        resolved_file_name = self._unit3d_resolved_file_name(
            attributes, payload, torrent_id, file_name
        )
        accepted = await self._unit3d_ids_accepted(
            meta,
            tracker,
            torrent_id,
            tmdb,
            imdb,
            tvdb,
            mal,
            resolved_file_name,
        )
        if not accepted:
            return self._empty_unit3d_torrent_info()
        description, images = self._unit3d_description_result(
            meta,
            tracker,
            torrent_url,
            description,
            skip_tracker_descriptions,
        )
        return self._unit3d_info_tuple(
            core, description, images, resolved_file_name
        )

    async def _safe_process_unit3d_torrent_payload(
        self,
        payload: dict[str, Any],
        meta: Meta,
        tracker: str,
        torrent_url: str,
        torrent_id: str | int | None,
        file_name: str | list[str] | None,
        skip_tracker_descriptions: bool,
    ) -> tuple[
        int | None,
        int | None,
        int | None,
        int | None,
        str | None,
        str | None,
        str | None,
        list[dict[str, str]],
        str | list[str] | None,
    ]:
        try:
            return await self._process_unit3d_torrent_payload(
                payload,
                meta,
                tracker,
                torrent_url,
                torrent_id,
                file_name,
                skip_tracker_descriptions,
            )
        except Exception as error:
            console.print_exception()
            logger.info(
                f"[yellow]Invalid Response from {tracker} API. Error: {error!s}[/yellow]"
            )
            return self._empty_unit3d_torrent_info()

    async def unit3d_torrent_info(
        self,
        tracker: str,
        torrent_url: str,
        search_url: str,
        meta: Meta,
        id: str | int | None = None,
        file_name: str | list[str] | None = None,
        skip_tracker_descriptions: bool = False,
        public_torrent_url: str | None = None,
    ) -> tuple[
        int | None,
        int | None,
        int | None,
        int | None,
        str | None,
        str | None,
        str | None,
        list[dict[str, str]],
        str | list[str] | None,
    ]:
        target = self._unit3d_search_target(
            file_name, id, torrent_url, search_url
        )
        if target is None:
            return self._empty_unit3d_torrent_info()
        url, extra_params = target
        params, headers = self._unit3d_auth(tracker)
        params.update(extra_params)
        payload = await self._unit3d_torrent_payload(
            tracker, url, params, headers, id, public_torrent_url
        )
        if payload is None:
            return self._empty_unit3d_torrent_info()
        return await self._safe_process_unit3d_torrent_payload(
            payload,
            meta,
            tracker,
            torrent_url,
            id,
            file_name,
            skip_tracker_descriptions,
        )

    @staticmethod
    def _cookie_fields(line: str) -> list[str]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return []
        return list(filter(None, re.split(r"[ \t]+", stripped)))

    @staticmethod
    def _parsed_cookie_line(line: str) -> tuple[str, str] | None:
        fields = Common._cookie_fields(line)
        if len(fields) < 7:
            return None
        return fields[5], fields[6]

    async def parse_cookie_file(self, cookiefile: str) -> dict[str, str]:
        """Parse a Netscape cookies.txt file into a requests-compatible mapping."""
        async with aiofiles.open(cookiefile) as handle:
            content = await handle.read()
        cookies: dict[str, str] = {}
        for line in content.splitlines():
            parsed = self._parsed_cookie_line(line)
            if parsed is not None:
                name, value = parsed
                cookies[name] = value
        return cookies

    @staticmethod
    def _ptgen_url(ptgen_site: str) -> str:
        return ptgen_site or "https://ptgen.zhenzhen.workers.dev"

    @staticmethod
    async def _fetch_ptgen_once(
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            response = await client.get(url, params=params, timeout=30.0)
            payload = response.json()
        except httpx.RequestError, httpx.TimeoutException, ValueError:
            return None
        return (
            cast(dict[str, Any], payload)
            if isinstance(payload, dict)
            else None
        )

    @staticmethod
    def _valid_ptgen_response(payload: dict[str, Any] | None) -> bool:
        return payload is not None and payload.get("error") is None

    async def _fetch_ptgen_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, Any],
        retries: int,
    ) -> dict[str, Any] | None:
        payload = await self._fetch_ptgen_once(client, url, params)
        if self._valid_ptgen_response(payload):
            return payload
        for _attempt in range(retries):
            payload = await self._fetch_ptgen_once(client, url, params)
            if self._valid_ptgen_response(payload):
                return payload
        return payload

    @staticmethod
    def _first_ptgen_data_item(
        payload: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not payload:
            return None
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        first = cast(list[Any], data)[0]
        return cast(dict[str, Any], first) if isinstance(first, dict) else None

    @staticmethod
    def _douban_link_from_ptgen(payload: dict[str, Any] | None) -> str:
        first = Common._first_ptgen_data_item(payload)
        return str(first.get("link") or "") if first is not None else ""

    @staticmethod
    def _ptgen_prompt_allowed(meta: Meta) -> bool:
        return not meta.unattended or bool(meta.unattended_confirm)

    async def _prompt_douban_link(self, meta: Meta) -> str:
        if not self._ptgen_prompt_allowed(meta):
            return ""
        value = await prompt_in_thread(
            cli_ui.ask_string,
            "Please enter Douban link:",
            default="",
        )
        return str(value or "")

    async def _resolve_douban_link(
        self,
        client: httpx.AsyncClient,
        url: str,
        meta: Meta,
        retries: int,
    ) -> str:
        if meta.imdb_id is None or meta.imdb_id == 0:
            logger.info("[red]No IMDb id was found.")
            return await self._prompt_douban_link(meta)
        search = {"search": f"tt{meta.imdb_id}"}
        payload = await self._fetch_ptgen_with_retries(
            client, url, search, retries
        )
        link = self._douban_link_from_ptgen(payload)
        if link:
            return link
        logger.info("[red]Unable to get data from ptgen using IMDb")
        return await self._prompt_douban_link(meta)

    @staticmethod
    def _ptgen_meta_path(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"

    async def _save_ptgen_meta(
        self, meta: Meta, payload: dict[str, Any]
    ) -> None:
        meta.ptgen = payload
        async with aiofiles.open(
            self._ptgen_meta_path(meta), "w", encoding="utf-8"
        ) as handle:
            await handle.write(json.dumps(meta.to_dict(), indent=4))

    @staticmethod
    def _ptgen_rendered_text(meta: Meta, payload: dict[str, Any]) -> str:
        text = str(payload.get("format", ""))
        if "[/img]" in text:
            text = text.split("[/img]")[1]
        cover = meta.imdb_info.get("cover", meta.artwork_url)
        return f"[img]{cover}[/img]{text}"

    async def _ptgen_flow(self, meta: Meta, url: str, retries: int) -> str:
        async with httpx.AsyncClient() as client:
            douban_link = await self._resolve_douban_link(
                client, url, meta, retries
            )
            payload = await self._fetch_ptgen_with_retries(
                client, url, {"url": douban_link}, retries
            )
        if not self._valid_ptgen_response(payload):
            logger.info(
                "[bold red]Failed to get valid ptgen response after retries"
            )
            return ""
        resolved_payload = cast(dict[str, Any], payload)
        await self._save_ptgen_meta(meta, resolved_payload)
        return self._ptgen_rendered_text(meta, resolved_payload)

    async def ptgen(
        self, meta: Meta, ptgen_site: str = "", ptgen_retry: int = 3
    ) -> str:
        try:
            return await self._ptgen_flow(
                meta, self._ptgen_url(ptgen_site), ptgen_retry
            )
        except Exception:
            console.print_exception()
            logger.info(
                "[bold red]There was an error getting the ptgen \\nUploading without ptgen"
            )
            return ""

    class MediaInfoParser:
        def parse_mediainfo(self, mediainfo_text: str) -> dict[str, Any]:
            # Patterns for matching sections and fields
            section_pattern = re.compile(
                r"^(General|Video|Audio|Text|Menu)(?:\s#\d+)?", re.IGNORECASE
            )
            parsed_data: dict[str, Any] = {
                "general": {},
                "video": [],
                "audio": [],
                "text": [],
            }
            current_section: str | None = None
            current_track: dict[str, str] = {}

            # Field lists based on PHP definitions
            general_fields = {
                "file_name",
                "format",
                "duration",
                "file_size",
                "bit_rate",
            }
            video_fields = {
                "format",
                "format_version",
                "codec",
                "width",
                "height",
                "stream_size",
                "framerate_mode",
                "frame_rate",
                "aspect_ratio",
                "bit_rate",
                "bit_rate_mode",
                "bit_rate_nominal",
                "bit_pixel_frame",
                "bit_depth",
                "language",
                "format_profile",
                "color_primaries",
                "title",
                "scan_type",
                "transfer_characteristics",
                "hdr_format",
            }
            audio_fields = {
                "codec",
                "format",
                "bit_rate",
                "channels",
                "title",
                "language",
                "format_profile",
                "stream_size",
            }
            # text_fields = {'title', 'language'}

            # Split MediaInfo by lines and process each line
            for line in mediainfo_text.splitlines():
                line = line.strip()

                # Detect a new section
                section_match = section_pattern.match(line)
                if section_match:
                    # Save the last track data if moving to a new section
                    if current_section and current_track:
                        if current_section in ["video", "audio", "text"]:
                            parsed_data[current_section].append(current_track)
                        else:
                            parsed_data[current_section] = current_track
                        # Debug output for finalizing the current track data
                        # print(f"Final processed track data for section '{current_section}': {current_track}")
                        current_track = {}  # Reset current track

                    # Update the current section
                    current_section = section_match.group(1).lower()
                    continue

                # Split each line on the first colon to separate property and value
                if ":" in line:
                    property_name, property_value = map(
                        str.strip, line.split(":", 1)
                    )
                    property_name = property_name.lower().replace(" ", "_")

                    # Add property if it's a recognized field for the current section
                    if (
                        (
                            current_section == "general"
                            and property_name in general_fields
                        )
                        or (
                            current_section == "video"
                            and property_name in video_fields
                        )
                        or (
                            current_section == "audio"
                            and property_name in audio_fields
                        )
                    ):
                        current_track[property_name] = property_value
                    elif current_section == "text":
                        # Processing specific properties for text
                        # Process title field
                        if (
                            property_name == "title"
                            and "title" not in current_track
                        ):
                            # print(f"\nProcessing Title: '{property_value}'")  # Debugging output

                            # Store the title as-is since it should remain descriptive
                            current_track["title"] = property_value
                            # print(f"Stored title: '{property_value}'")

                        # Process language field only if it hasn't already been set
                        elif (
                            property_name == "language"
                            and "language" not in current_track
                        ):
                            current_track["language"] = property_value

            # Append the last track to the parsed data if it exists
            if current_section and current_track:
                if current_section in ["video", "audio", "text"]:
                    parsed_data[current_section].append(current_track)
                else:
                    parsed_data[current_section] = current_track
                # Final debug output for the last track data
                # print(f"Final processed track data for last section '{current_section}': {current_track}")

            # Debug output for the complete parsed_data
            # print("\nComplete Parsed Data:")
            # for section, data in parsed_data.items():
            #    print(f"{section}: {data}")

            return parsed_data

        def format_bbcode(self, parsed_mediainfo: dict[str, Any]) -> str:
            bbcode_output = "\n"

            # Format General Section
            if "general" in parsed_mediainfo:
                bbcode_output += "[b]General[/b]\n"
                for prop, value in parsed_mediainfo["general"].items():
                    bbcode_output += f"[b]{prop.replace('_', ' ').capitalize()}:[/b] {value}\n"

            # Format Video Section
            if "video" in parsed_mediainfo:
                bbcode_output += "\n[b]Video[/b]\n"
                for track in parsed_mediainfo["video"]:
                    for prop, value in track.items():
                        bbcode_output += f"[b]{prop.replace('_', ' ').capitalize()}:[/b] {value}\n"

            # Format Audio Section
            if "audio" in parsed_mediainfo:
                bbcode_output += "\n[b]Audio[/b]\n"
                for index, track in enumerate(
                    parsed_mediainfo["audio"], start=1
                ):  # Start enumeration at 1
                    parts = [
                        f"{index}."
                    ]  # Start with track number without a trailing slash

                    language = track.get("language", "").lower()
                    parts.append(language.capitalize() if language else "")

                    # Other properties to concatenate (language already handled above)
                    properties = [
                        "codec",
                        "format",
                        "channels",
                        "bit_rate",
                        "format_profile",
                        "stream_size",
                    ]
                    parts.extend(
                        [track[prop] for prop in properties if track.get(prop)]
                    )

                    # Join parts (starting from index 1, after the track number) with slashes and add to bbcode_output
                    bbcode_output += (
                        f"{parts[0]} " + " / ".join(parts[1:]) + "\n"
                    )

            # Format Text Section - Centered, spaced apart
            if "text" in parsed_mediainfo:
                bbcode_output += "\n[b]Subtitles[/b]\n"
                subtitle_entries: list[str] = []
                for track in parsed_mediainfo["text"]:
                    language_display = track.get("language", "")
                    subtitle_entries.append(language_display)
                bbcode_output += " ".join(subtitle_entries)

            bbcode_output += "\n"
            return bbcode_output

    @staticmethod
    def _bdmv_mediainfo_path(meta: Meta) -> Path:
        return (
            Path(meta.base_dir) / "tmp" / meta.uuid / "MEDIAINFO_CLEANPATH.txt"
        )

    @staticmethod
    def _bdmv_playlist(meta: Meta) -> dict[str, Any]:
        return cast(dict[str, Any], meta.discs[0]["playlists"][0])

    @staticmethod
    def _bdmv_line_allowed(line: str, remove: list[str] | None) -> bool:
        if not remove:
            return True
        stripped = line.strip()
        return not any(stripped.startswith(prefix) for prefix in remove)

    async def _read_clean_bdmv_mediainfo(
        self, path: Path, remove: list[str] | None
    ) -> str:
        if not path.is_file():
            return ""
        async with aiofiles.open(path, encoding="utf-8") as handle:
            lines = await handle.readlines()
        return "".join(
            line for line in lines if self._bdmv_line_allowed(line, remove)
        )

    @staticmethod
    def _bdmv_items(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], value)
            if isinstance(item, dict)
        ]

    @staticmethod
    def _bdmv_item_size(item: dict[str, Any]) -> int:
        try:
            return int(item.get("size", 0) or 0)
        except TypeError, ValueError:
            return 0

    @classmethod
    def _largest_bdmv_item(cls, playlist: dict[str, Any]) -> str:
        items = cls._bdmv_items(playlist.get("items", []))
        if not items:
            return ""
        largest = max(items, key=cls._bdmv_item_size)
        return str(largest.get("file") or "")

    async def _export_bdmv_mediainfo(self, meta: Meta, source: str) -> None:
        await export_info(
            source, False, meta.uuid, meta.base_dir, is_dvd=False
        )

    async def _ensure_bdmv_playlist_mediainfo(
        self, meta: Meta, mi_path: Path, playlist: dict[str, Any]
    ) -> None:
        if mi_path.is_file():
            return
        logger.debug("[blue]Generating MediaInfo for BDMV...[/blue]")
        await self._export_bdmv_mediainfo(meta, str(playlist["path"]))

    async def _fallback_bdmv_mediainfo(
        self,
        meta: Meta,
        playlist: dict[str, Any],
        mi_path: Path,
        remove: list[str] | None,
    ) -> str | None:
        largest_m2ts = self._largest_bdmv_item(playlist)
        if not largest_m2ts:
            return None
        logger.debug(
            f"[blue]Selected largest M2TS from meta: {Path(largest_m2ts).name}[/blue]"
        )
        await self._export_bdmv_mediainfo(meta, largest_m2ts)
        return await self._read_clean_bdmv_mediainfo(mi_path, remove)

    async def get_bdmv_mediainfo(
        self, meta: Meta, remove: list[str] | None = None, char_limit: int = 0
    ) -> str:
        """Generate and sanitize MediaInfo for BDMV discs."""
        if meta.is_disc != "BDMV":
            return ""
        mi_path = self._bdmv_mediainfo_path(meta)
        playlist = self._bdmv_playlist(meta)
        await self._ensure_bdmv_playlist_mediainfo(meta, mi_path, playlist)
        mediainfo = await self._read_clean_bdmv_mediainfo(mi_path, remove)
        if not char_limit or len(mediainfo) <= char_limit:
            return mediainfo
        logger.debug(
            f"[yellow]MediaInfo length ({len(mediainfo)}) exceeds limit ({char_limit}). Falling back to largest M2TS...[/yellow]"
        )
        fallback = await self._fallback_bdmv_mediainfo(
            meta, playlist, mi_path, remove
        )
        return fallback if fallback is not None else mediainfo

    @staticmethod
    def _book_language_matches(
        book_language: str, languages_to_check: list[str]
    ) -> bool:
        required = {language.lower() for language in languages_to_check}
        return not required or book_language.lower() in required

    async def _book_language_requirement(
        self,
        meta: Meta,
        tracker: str,
        languages_to_check: list[str],
        prompt_on_failure: bool,
    ) -> bool:
        book_language = meta.book_language
        if not book_language:
            return True
        if self._book_language_matches(book_language, languages_to_check):
            return True
        logger.info(
            f"[red]Language requirement not met for [bold]{tracker}[/bold].[/red]\n"
            f"[yellow]Required one of:[/yellow] {', '.join(languages_to_check)}\n"
            f"[cyan]Found book language:[/cyan] {book_language}"
        )
        if prompt_on_failure:
            return await self.prompt_user_for_confirmation(
                f"{tracker}: Language requirements not met. Do you want to proceed with the upload?",
                meta,
            )
        return False

    @staticmethod
    def _first_original_language(meta: Meta) -> str:
        raw = meta.original_language
        if isinstance(raw, str):
            return raw.strip()
        if not isinstance(raw, list) or not raw:
            return ""
        first = raw[0]
        return first.strip() if isinstance(first, str) else ""

    def _debug_original_language_candidates(
        self, meta: Meta, candidates: set[str], original_ok: bool
    ) -> None:
        if not meta.debug or original_ok:
            return
        rendered = ", ".join(sorted(candidates)) or "None"
        logger.info(
            f"[blue]Debug: Original language expanded candidates: {rendered}[/blue]"
        )

    def _original_language_state(
        self,
        meta: Meta,
        original_language: bool,
        alias_lookup: dict[str, set[str]],
        audio_expanded: set[str],
    ) -> tuple[str | None, bool]:
        if not original_language:
            return None, False
        first_original = self._first_original_language(meta)
        if not first_original:
            return None, False
        display = self._format_language_for_display(first_original)
        candidates = self._expand_language_candidates(
            first_original, alias_lookup
        )
        original_ok = bool(candidates.intersection(audio_expanded))
        self._debug_original_language_candidates(meta, candidates, original_ok)
        return display, original_ok

    def _language_requirement_state(
        self,
        meta: Meta,
        languages_to_check: list[str],
        original_language: bool,
    ) -> _LanguageRequirementState:
        alias_lookup = self._build_language_alias_lookup()
        raw_audio = self._coerce_language_values(meta.audio_languages)
        raw_subtitles = self._coerce_language_values(meta.subtitle_languages)
        required = [language.lower() for language in languages_to_check]
        audio = [language.lower() for language in raw_audio]
        subtitles = [language.lower() for language in raw_subtitles]
        required_expanded = self._expand_language_list(required, alias_lookup)
        audio_expanded = self._expand_language_list(raw_audio, alias_lookup)
        subtitle_expanded = self._expand_language_list(
            raw_subtitles, alias_lookup
        )
        original_display, original_ok = self._original_language_state(
            meta, original_language, alias_lookup, audio_expanded
        )
        return _LanguageRequirementState(
            required=required,
            audio=audio,
            subtitles=subtitles,
            required_expanded=required_expanded,
            audio_expanded=audio_expanded,
            subtitle_expanded=subtitle_expanded,
            original_display=original_display,
            original_ok=original_ok,
        )

    @staticmethod
    def _language_requirement_match(
        enabled: bool, required: set[str], available: set[str]
    ) -> bool:
        if not enabled:
            return True
        return bool(required.intersection(available))

    @staticmethod
    def _audio_subtitle_matches(
        state: _LanguageRequirementState,
        check_audio: bool,
        check_subtitle: bool,
    ) -> tuple[bool, bool]:
        return (
            Common._language_requirement_match(
                check_audio, state.required_expanded, state.audio_expanded
            ),
            Common._language_requirement_match(
                check_subtitle,
                state.required_expanded,
                state.subtitle_expanded,
            ),
        )

    async def _original_required_gate(
        self,
        meta: Meta,
        tracker: str,
        state: _LanguageRequirementState,
        original_required: bool,
        prompt_on_failure: bool,
    ) -> bool | None:
        if not original_required or state.original_ok:
            return None
        logger.info(
            f"[red]Original language requirement not met for [bold]{tracker}[/bold].[/red]\n"
            f"[yellow]Required original audio language:[/yellow] {state.original_display}\n"
            f"[cyan]Found Audio Languages:[/cyan] {', '.join(state.audio) or 'None'}"
        )
        if prompt_on_failure:
            return await self.prompt_user_for_confirmation(
                f"{tracker}: Language requirements not met. Do you want to proceed with the upload?",
                meta,
            )
        return False

    @staticmethod
    def _log_language_debug(
        state: _LanguageRequirementState, audio_ok: bool, subtitle_ok: bool
    ) -> None:
        logger.debug(
            f"[blue]Debug: Audio Languages Found: {state.audio}[/blue]"
        )
        logger.debug(
            f"[blue]Debug: Subtitle Languages Found: {state.subtitles}[/blue]"
        )
        logger.debug(
            f"[blue]Debug: Original Audio Language: {state.original_display}[/blue]"
        )
        logger.debug(
            f"[blue]Debug: Audio OK: {audio_ok}, Subtitle OK: {subtitle_ok}, Original OK: {state.original_ok}[/blue]"
        )

    @staticmethod
    def _language_list_display(values: list[str]) -> str:
        rendered = ", ".join(values)
        return rendered if rendered else "None"

    @staticmethod
    def _original_audio_subtitle_gate(
        tracker: str,
        state: _LanguageRequirementState,
        audio_ok: bool,
        subtitle_ok: bool,
    ) -> bool | None:
        if audio_ok:
            return None
        if not state.original_ok:
            return None
        if subtitle_ok:
            return True
        logger.info(
            f"[red]Language requirement not met for [bold]{tracker}[/bold].[/red]\n"
            f"[yellow]Required subtitles in one of the following with an original audio track:[/yellow] "
            f"{', '.join(state.required)}\n"
            f"[cyan]Found Audio:[/cyan] {Common._language_list_display(state.audio)}\n"
            f"[cyan]Found Subtitles:[/cyan] {Common._language_list_display(state.subtitles)}\n"
            f"[cyan]Original Audio Language:[/cyan] {state.original_display}"
        )
        return False

    @staticmethod
    def _combined_language_requirement(
        check_audio: bool,
        check_subtitle: bool,
        require_both: bool,
        audio_ok: bool,
        subtitle_ok: bool,
    ) -> bool:
        if require_both:
            return all((audio_ok, subtitle_ok))
        return any((check_audio and audio_ok, check_subtitle and subtitle_ok))

    @staticmethod
    def _log_combined_language_failure(
        tracker: str,
        state: _LanguageRequirementState,
        require_both: bool,
    ) -> None:
        if require_both:
            requirement = (
                "Required both audio and subtitles in one of the following:"
            )
        else:
            requirement = "Required at least one of the following:"
        logger.info(
            f"[red]Language requirement not met for [bold]{tracker}[/bold].[/red]\n"
            f"[yellow]{requirement}[/yellow] {', '.join(state.required)}\n"
            f"[cyan]Found Audio:[/cyan] {', '.join(state.audio) or 'None'}\n"
            f"[cyan]Found Subtitles:[/cyan] {', '.join(state.subtitles) or 'None'}"
        )

    async def _confirmed_language_result(
        self,
        meta: Meta,
        tracker: str,
        meets_requirement: bool,
        prompt_on_failure: bool,
    ) -> bool:
        if meets_requirement or not prompt_on_failure:
            return meets_requirement
        return await self.prompt_user_for_confirmation(
            f"{tracker}: Language requirements not met. Do you want to proceed with the upload?",
            meta,
        )

    @staticmethod
    def _language_checks_disabled(
        check_audio: bool, check_subtitle: bool
    ) -> bool:
        return not any((check_audio, check_subtitle))

    @staticmethod
    def _log_language_failure_if_needed(
        tracker: str,
        state: _LanguageRequirementState,
        require_both: bool,
        meets_requirement: bool,
    ) -> None:
        if not meets_requirement:
            Common._log_combined_language_failure(tracker, state, require_both)

    async def _video_language_requirement(
        self,
        meta: Meta,
        tracker: str,
        languages_to_check: list[str],
        check_audio: bool,
        check_subtitle: bool,
        require_both: bool,
        original_language: bool,
        original_required: bool,
        prompt_on_failure: bool,
    ) -> bool:
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=tracker
            )
        state = self._language_requirement_state(
            meta, languages_to_check, original_language
        )
        original_gate = await self._original_required_gate(
            meta,
            tracker,
            state,
            original_required,
            prompt_on_failure,
        )
        if original_gate is not None:
            return original_gate
        audio_ok, subtitle_ok = self._audio_subtitle_matches(
            state, check_audio, check_subtitle
        )
        self._log_language_debug(state, audio_ok, subtitle_ok)
        fallback = self._original_audio_subtitle_gate(
            tracker, state, audio_ok, subtitle_ok
        )
        if fallback is not None:
            return fallback
        if self._language_checks_disabled(check_audio, check_subtitle):
            return True
        meets_requirement = self._combined_language_requirement(
            check_audio,
            check_subtitle,
            require_both,
            audio_ok,
            subtitle_ok,
        )
        self._log_language_failure_if_needed(
            tracker, state, require_both, meets_requirement
        )
        return await self._confirmed_language_result(
            meta, tracker, meets_requirement, prompt_on_failure
        )

    async def check_language_requirements(
        self,
        meta: Meta,
        tracker: str,
        languages_to_check: list[str],
        check_audio: bool = False,
        check_subtitle: bool = False,
        require_both: bool = False,
        original_language: bool = False,
        original_required: bool = False,
        prompt_on_failure: bool = True,
    ) -> bool:
        """Check tracker language requirements for books and video media."""
        if meta.category not in ("TV", "MOVIE", "BOOK"):
            return True
        if meta.category == "BOOK":
            return await self._book_language_requirement(
                meta, tracker, languages_to_check, prompt_on_failure
            )
        try:
            return await self._video_language_requirement(
                meta,
                tracker,
                languages_to_check,
                check_audio,
                check_subtitle,
                require_both,
                original_language,
                original_required,
                prompt_on_failure,
            )
        except Exception as error:
            console.print_exception()
            logger.error(
                f"[red]Error checking language requirements: {error}[/red]"
            )
            return False

    async def save_html_file(
        self, meta: Meta, tracker: str, text: str = "", file_name: str = ""
    ) -> str:
        """
        Save provided text as an HTML file.

        :param tracker: Name of the tracker for naming the file.
        :param text: The HTML content to save.
        :param file_name: Optional custom file name (without extension).
        :return: Path to the saved HTML file.
        :rtype: str
        """
        html_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}]{file_name}.html"
        Path(html_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(html_path, "w", encoding="utf-8") as f:
            await f.write(text)
        return html_path

    def get_small_description(self, meta: Meta) -> str:
        """
        Generate a small description from meta data.
        Mainly used for Chinese trackers.

        :param meta: Meta data.
        :return: Small description.
        :rtype: str
        """
        resolution = meta.resolution
        audio = meta.audio
        video_bitrate = meta.video_bitrate if meta.video_bitrate else 0
        audio_bitrate = meta.audio_bitrate if meta.audio_bitrate else 0

        return f"{resolution} @ {video_bitrate} kbps - {audio} @ {audio_bitrate} kbps"

    async def check_and_confirm_adult_media_upload(
        self, meta: Meta, tracker: str
    ) -> bool:
        """
        Check if the media is categorized as adult/pornographic and prompt the user for confirmation before uploading to a non-adult tracker.

        :param meta: Metadata dictionary containing category and genre information.
        :param tracker: The tracker name for display in the prompt.
        :return: True if the user confirms or if the media is not adult, False otherwise.
        """
        if meta.adult_media:
            logger.info(
                f"[bold red]Pornography is not allowed at {tracker}.[/bold red]"
            )
            if meta.unattended:
                return bool(meta.unattended_confirm)
            return await self.prompt_user_for_confirmation(
                "Do you want to upload anyway?", meta
            )

        return True

    @staticmethod
    def _portuguese_lowercase_words() -> frozenset[str]:
        return frozenset(
            {
                "a",
                "o",
                "as",
                "os",
                "um",
                "uma",
                "uns",
                "umas",
                "de",
                "do",
                "da",
                "dos",
                "das",
                "em",
                "no",
                "na",
                "nos",
                "nas",
                "por",
                "pelo",
                "pela",
                "pelos",
                "pelas",
                "para",
                "com",
                "sob",
                "sobre",
                "sem",
                "e",
                "ou",
                "mas",
                "nem",
                "que",
                "se",
            }
        )

    @staticmethod
    def _portuguese_title_separator(value: str) -> bool:
        return re.fullmatch(r"[:\-\(\)\[\]]", value) is not None

    @staticmethod
    def _title_token_affixes(token: str) -> tuple[str, str, str]:
        prefix_match = re.match(r"^[^\w]+", token)
        prefix = prefix_match.group(0) if prefix_match else ""
        suffix_match = re.search(r"[^\w]+$", token)
        suffix = suffix_match.group(0) if suffix_match else ""
        end = len(token) - len(suffix) if suffix else len(token)
        return prefix, token[len(prefix) : end], suffix

    @staticmethod
    def _capitalized_title_core(
        core: str, word_index: int, lowercase_words: frozenset[str]
    ) -> str:
        lowered = core.lower()
        if word_index > 0 and lowered in lowercase_words:
            return lowered
        return core[0].upper() + core[1:]

    @classmethod
    def _format_portuguese_title_token(
        cls,
        token: str,
        word_index: int,
        lowercase_words: frozenset[str],
    ) -> tuple[str, int]:
        if not token or token.isspace():
            return token, word_index
        prefix, core, suffix = cls._title_token_affixes(token)
        if not core:
            return token, word_index + 1
        formatted = cls._capitalized_title_core(
            core, word_index, lowercase_words
        )
        return f"{prefix}{formatted}{suffix}", word_index + 1

    @classmethod
    def _format_portuguese_title_segment(
        cls, segment: str, lowercase_words: frozenset[str]
    ) -> str:
        formatted_tokens: list[str] = []
        word_index = 0
        for token in re.split(r"(\s+)", segment):
            formatted, word_index = cls._format_portuguese_title_token(
                token, word_index, lowercase_words
            )
            formatted_tokens.append(formatted)
        return "".join(formatted_tokens)

    def portuguese_title_capitalization(self, title: str) -> str:
        """Capitalize a Portuguese title while preserving minor words."""
        lowercase_words = self._portuguese_lowercase_words()
        formatted_parts: list[str] = []
        for part in re.split(r"([:\-\(\)\[\]])", title):
            if not part or self._portuguese_title_separator(part):
                formatted_parts.append(part)
                continue
            formatted_parts.append(
                self._format_portuguese_title_segment(part, lowercase_words)
            )
        return "".join(formatted_parts)

    def _usenet_config(self) -> dict[str, Any]:
        raw = self.config.get("USENET", {})
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}

    def _nzb_password_applies(self, meta: Meta) -> bool:
        config = self._usenet_config()
        configured_password = config.get("archive_password")
        has_password = bool(meta.archive_password or configured_password)
        return has_password and not bool(config.get("skip_archive", False))

    @staticmethod
    def _nzb_file_exists(meta: Meta) -> bool:
        return bool(meta.nzb_path and Path(meta.nzb_path).exists())

    async def check_nzb_file(self, tracker: str, meta: Meta) -> bool:
        if not self._nzb_file_exists(meta):
            logger.error(
                f"{tracker}: [red]Error: The NZB file is missing. Aborting upload...[/red]"
            )
            return False
        nzb_path = str(meta.nzb_path)
        if self._nzb_password_applies(
            meta
        ) and not await verify_nzb_has_password(nzb_path):
            logger.error(
                f"{tracker}: [red]Error: The NZB file does not contain the password in its metadata header. Aborting upload...[/red]"
            )
            return False
        return True

    def has_bdinfo(self, content: str) -> bool:
        """
        Check if the provided content contains BDInfo information.
        """
        if not content or not isinstance(content, str):
            return False

        bdinfo_pattern = [
            r"DISC INFO:",
            r"Disc Title:\s*",
            r"Disc Label:\s*",
            r"PLAYLIST REPORT:",
            r"\(\*\)\s*Indicates included stream hidden",
        ]

        combined_regex = "|".join(bdinfo_pattern)

        return bool(re.search(combined_regex, content, re.IGNORECASE))
