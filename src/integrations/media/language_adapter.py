# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import langcodes
from langcodes.tag_parser import LanguageTagError

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import logger

_DISC_INFO_KEYS = {"Disc Title", "Disc Label", "Disc Size", "Protection"}
_PLAYLIST_INFO_KEYS = {"Playlist", "Size", "Length", "Total Bitrate"}
_MEDIAINFO_FIELDS = {
    "video": {"format", "duration", "bit rate", "encoding settings", "title"},
    "audio": {
        "format",
        "duration",
        "bit rate",
        "language",
        "commercial name",
        "channel",
        "channel (s)",
        "title",
    },
    "text": {"format", "duration", "bit rate", "language", "title"},
}
_MEDIAINFO_SECTION_RE = re.compile(
    r"^(General|Video|Audio|Text|Menu)(?:\s*#\d+)?$", re.IGNORECASE
)


class LanguagesManager:
    @staticmethod
    def _dedupe_preserve_order(values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    @staticmethod
    def _language_display_name(language: str) -> str:
        try:
            return langcodes.Language.get(language).display_name("en")
        except LanguageTagError, ValueError:
            return language.strip().title()

    @staticmethod
    def _audio_section_start(lines: list[str]) -> int | None:
        for index, line in enumerate(lines):
            if re.fullmatch(r"Audio(?:\s*#\d+)?", line.strip(), re.IGNORECASE):
                return index
        return None

    @staticmethod
    def _section_end(lines: list[str], start: int) -> int:
        section_header = re.compile(
            r"^(General|Video|Audio|Text|Menu)(?:\s*#\d+)?$", re.IGNORECASE
        )
        for index in range(start + 1, len(lines)):
            if section_header.fullmatch(lines[index].strip()):
                return index
        return len(lines)

    @staticmethod
    def _section_has_language(lines: list[str], start: int, end: int) -> bool:
        return any(
            re.match(r"^Language\s*:", line.strip(), re.IGNORECASE)
            for line in lines[start + 1 : end]
        )

    @staticmethod
    def _language_insert_index(lines: list[str], start: int, end: int) -> int:
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        return insert_at

    @classmethod
    def _add_language_to_audio_section(
        cls, content: str, language: str
    ) -> str:
        lines = content.splitlines()
        audio_start = cls._audio_section_start(lines)
        if audio_start is None:
            return content
        audio_end = cls._section_end(lines, audio_start)
        if cls._section_has_language(lines, audio_start, audio_end):
            return content
        insert_at = cls._language_insert_index(lines, audio_start, audio_end)
        lines.insert(
            insert_at, f"Language                                : {language}"
        )
        suffix = "\n" if content.endswith("\n") else ""
        return "\n".join(lines) + suffix

    @staticmethod
    def _is_unknown_language(value: str) -> bool:
        return value.lower() in {"und", "unknown", "undefined"}

    @classmethod
    def _confirmed_language_value(cls, meta: Meta) -> str | None:
        value = str(meta.manual_language or "").strip()
        if not value or cls._is_unknown_language(value):
            return None
        return value

    @staticmethod
    def _release_has_multi_audio_marker(meta: Meta) -> bool:
        release_name = " ".join(
            str(value or "") for value in (meta.uuid, meta.path, meta.name)
        )
        if meta.dual_audio:
            return True
        return (
            re.search(
                r"\b(?:DUAL(?:[ ._-]?AUDIO)?|MULTI(?:[ ._-]?AUDIO)?|DUBBED)\b",
                release_name,
                re.IGNORECASE,
            )
            is not None
        )

    @staticmethod
    def _typed_audio_tracks(tracks: list[Any]) -> list[dict[str, Any]]:
        audio_tracks: list[dict[str, Any]] = []
        for raw_track in tracks:
            if not isinstance(raw_track, dict):
                continue
            track = cast(dict[str, Any], raw_track)
            if track.get("@type") == "Audio":
                audio_tracks.append(track)
        return audio_tracks

    @classmethod
    def _single_audio_track(cls, meta: Meta) -> dict[str, Any] | None:
        media = cast(dict[str, Any], meta.mediainfo.get("media", {}))
        tracks = cast(list[Any], media.get("track", []))
        audio_tracks = cls._typed_audio_tracks(tracks)
        if len(audio_tracks) != 1:
            return None
        return audio_tracks[0]

    @classmethod
    def _track_needs_language(cls, audio_track: dict[str, Any]) -> bool:
        current = str(audio_track.get("Language") or "").strip()
        return not current or cls._is_unknown_language(current)

    @staticmethod
    async def _read_text(path: Path) -> str:
        async with aiofiles.open(path, encoding="utf-8") as source:
            return await source.read()

    @staticmethod
    async def _write_text(path: Path, content: str) -> None:
        async with aiofiles.open(
            path, "w", newline="", encoding="utf-8"
        ) as destination:
            await destination.write(content)

    @classmethod
    async def _patch_mediainfo_text_files(
        cls, release_dir: Path, language: str
    ) -> None:
        for filename in ("MEDIAINFO.txt", "MEDIAINFO_CLEANPATH.txt"):
            path = release_dir / filename
            if not path.exists():
                continue
            content = await cls._read_text(path)
            updated = cls._add_language_to_audio_section(content, language)
            if updated != content:
                await cls._write_text(path, updated)

    @staticmethod
    async def _write_mediainfo_json(release_dir: Path, meta: Meta) -> None:
        path = release_dir / "MediaInfo.json"
        if not path.exists():
            return
        async with aiofiles.open(path, "w", encoding="utf-8") as destination:
            await destination.write(json.dumps(meta.mediainfo, indent=4))

    @classmethod
    def _can_apply_confirmed_language(cls, meta: Meta) -> bool:
        if meta.category not in ("MOVIE", "TV"):
            return False
        return meta.is_disc != "BDMV"

    @classmethod
    def _eligible_confirmed_audio_track(
        cls, meta: Meta
    ) -> dict[str, Any] | None:
        audio_track = cls._single_audio_track(meta)
        if audio_track is None:
            return None
        if not cls._track_needs_language(audio_track):
            return None
        return audio_track

    async def apply_confirmed_single_audio_language(self, meta: Meta) -> bool:
        if not self._can_apply_confirmed_language(meta):
            return False
        confirmed_language = self._confirmed_language_value(meta)
        if confirmed_language is None:
            return False
        if self._release_has_multi_audio_marker(meta):
            return False
        audio_track = self._eligible_confirmed_audio_track(meta)
        if audio_track is None:
            return False
        language = self._language_display_name(confirmed_language)
        audio_track["Language"] = language
        release_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        await self._patch_mediainfo_text_files(release_dir, language)
        await self._write_mediainfo_json(release_dir, meta)
        logger.info(
            f"[cyan]Applied user-confirmed language {language} to the single untagged audio track in the upload MediaInfo.[/cyan]"
        )
        return True

    @staticmethod
    def _bluray_summary_path(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / "BD_SUMMARY_00.txt"

    @classmethod
    async def _read_bluray_summary(cls, meta: Meta) -> str | None:
        path = cls._bluray_summary_path(meta)
        if not path.exists():
            logger.info(
                f"[yellow]BD_SUMMARY_00.txt not found at {path}[/yellow]"
            )
            return None
        try:
            async with aiofiles.open(path, encoding="utf-8") as source:
                return await source.read()
        except Exception as error:
            logger.error(f"[red]Error reading BD_SUMMARY file: {error}[/red]")
            return None

    @staticmethod
    def _empty_bluray_data() -> dict[str, Any]:
        return {
            "disc_info": {},
            "playlist_info": {},
            "video": {},
            "audio": [],
            "subtitles": [],
        }

    @staticmethod
    def _bluray_key(key: str) -> str:
        return key.lower().replace(" ", "_")

    @classmethod
    def _set_bluray_info(
        cls, parsed: dict[str, Any], key: str, value: str
    ) -> None:
        target = "disc_info" if key in _DISC_INFO_KEYS else "playlist_info"
        section = cast(dict[str, Any], parsed[target])
        section[cls._bluray_key(key)] = value

    @staticmethod
    def _video_data(value: str) -> dict[str, str]:
        parts = [part.strip() for part in value.split("/")]
        if len(parts) < 6:
            return {"format": value}
        keys = (
            "format",
            "bitrate",
            "resolution",
            "framerate",
            "aspect_ratio",
            "profile",
        )
        return dict(zip(keys, parts[:6], strict=False))

    @staticmethod
    def _indexed_track(
        parts: list[str], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        return dict(zip(keys, parts, strict=False))

    @classmethod
    def _audio_data(cls, key: str, value: str) -> dict[str, Any]:
        parts = [part.strip() for part in value.split("/")]
        track = cls._indexed_track(
            parts[:4], ("language", "format", "channels", "sample_rate")
        )
        track["is_commentary"] = key.startswith("*")
        if len(parts) >= 5:
            bitrate = parts[4].strip()
            track["bitrate"] = bitrate
            match = re.search(r"(\d+)\s*kbps", bitrate)
            if match:
                track["bitrate_num"] = int(match.group(1))
        if len(parts) >= 6:
            track["bit_depth"] = parts[5].split("(")[0].strip()
        return track

    @classmethod
    def _subtitle_data(cls, key: str, value: str) -> dict[str, Any]:
        parts = [part.strip() for part in value.split("/")]
        track = cls._indexed_track(parts[:2], ("language", "bitrate"))
        track["is_commentary"] = key.startswith("*")
        return track

    @staticmethod
    def _is_audio_key(key: str) -> bool:
        return key == "Audio" or (key.startswith("*") and "Audio" in key)

    @staticmethod
    def _is_subtitle_key(key: str) -> bool:
        return key == "Subtitle" or (key.startswith("*") and "Subtitle" in key)

    @classmethod
    def _bluray_handlers(
        cls,
    ) -> tuple[
        tuple[
            Callable[[str], bool], Callable[[dict[str, Any], str, str], None]
        ],
        ...,
    ]:
        return (
            (
                lambda key: (
                    key in _DISC_INFO_KEYS or key in _PLAYLIST_INFO_KEYS
                ),
                cls._set_bluray_info,
            ),
            (
                lambda key: key == "Video",
                lambda parsed, _key, value: parsed.__setitem__(
                    "video", cls._video_data(value)
                ),
            ),
            (
                cls._is_audio_key,
                lambda parsed, key, value: cast(
                    list[dict[str, Any]], parsed["audio"]
                ).append(cls._audio_data(key, value)),
            ),
            (
                cls._is_subtitle_key,
                lambda parsed, key, value: cast(
                    list[dict[str, Any]], parsed["subtitles"]
                ).append(cls._subtitle_data(key, value)),
            ),
        )

    @classmethod
    def _parse_bluray_line(cls, parsed: dict[str, Any], line: str) -> None:
        if ":" not in line:
            return
        key, value = (part.strip() for part in line.split(":", 1))
        for predicate, handler in cls._bluray_handlers():
            if predicate(key):
                handler(parsed, key, value)
                return

    async def parse_blu_ray(self, meta: Meta) -> dict[str, Any]:
        content = await self._read_bluray_summary(meta)
        if content is None:
            return {}
        parsed = self._empty_bluray_data()
        for raw_line in content.strip().split("\n"):
            line = raw_line.strip()
            if line:
                self._parse_bluray_line(parsed, line)
        return parsed

    @staticmethod
    def _mediainfo_path(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / "MEDIAINFO.txt"

    @classmethod
    async def _read_mediainfo(cls, meta: Meta) -> str | None:
        path = cls._mediainfo_path(meta)
        if not path.exists():
            return None
        try:
            async with aiofiles.open(path, encoding="utf-8") as source:
                return await source.read()
        except Exception as error:
            logger.error(f"[red]Error reading MEDIAINFO file: {error}[/red]")
            return None

    @staticmethod
    def _empty_mediainfo_data() -> dict[str, Any]:
        return {"general": {}, "video": [], "audio": [], "text": []}

    @staticmethod
    def _store_mediainfo_track(
        parsed: dict[str, Any], section: str | None, track: dict[str, str]
    ) -> None:
        if not section or not track:
            return
        if section == "general":
            parsed["general"] = track
            return
        if section in _MEDIAINFO_FIELDS:
            cast(list[dict[str, str]], parsed[section]).append(track)

    @staticmethod
    def _mediainfo_key_value(
        line: str, section: str | None
    ) -> tuple[str, str] | None:
        if section is None or ":" not in line:
            return None
        key, value = line.split(":", 1)
        return key.strip().lower(), value.strip()

    @staticmethod
    def _record_mediainfo_field(
        section: str, track: dict[str, str], key: str, value: str
    ) -> None:
        if section == "general" or key in _MEDIAINFO_FIELDS.get(
            section, set()
        ):
            track[key.replace(" ", "_")] = value

    @classmethod
    def _parse_mediainfo_content(cls, content: str) -> dict[str, Any]:
        parsed = cls._empty_mediainfo_data()
        current_section: str | None = None
        current_track: dict[str, str] = {}
        for raw_line in content.strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            match = _MEDIAINFO_SECTION_RE.match(line)
            if match:
                cls._store_mediainfo_track(
                    parsed, current_section, current_track
                )
                current_section = match.group(1).lower()
                current_track = {}
                continue
            key_value = cls._mediainfo_key_value(line, current_section)
            if key_value is None:
                continue
            key, value = key_value
            cls._record_mediainfo_field(
                cast(str, current_section), current_track, key, value
            )
        cls._store_mediainfo_track(parsed, current_section, current_track)
        return parsed

    async def parsed_mediainfo(self, meta: Meta) -> dict[str, Any]:
        content = await self._read_mediainfo(meta)
        if content is None:
            return {}
        return self._parse_mediainfo_content(content)

    @staticmethod
    def _tracker_status(meta: Meta, tracker: str) -> dict[str, Any]:
        if not tracker:
            return {}
        status = meta.tracker_status.setdefault(tracker, {})
        if isinstance(status, dict):
            return cast(dict[str, Any], status)
        return {}

    @staticmethod
    def _interactive_language_prompt(meta: Meta) -> bool:
        if not meta.unattended:
            return True
        return bool(meta.unattended_confirm)

    @staticmethod
    def _language_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return cast(list[str], value)
        return []

    @classmethod
    def _normalized_languages(cls, values: list[str]) -> list[str]:
        normalized = [value.split()[0] for value in values]
        return cls._dedupe_preserve_order(normalized)

    @staticmethod
    def _mark_language_skip(
        meta: Meta, status: dict[str, Any], language_type: str
    ) -> None:
        if language_type == "audio":
            meta.unattended_audio_skip = True
        else:
            meta.unattended_subtitle_skip = True
        status["skip_upload"] = True

    @staticmethod
    async def _ask_language_string(prompt: str) -> str | None:
        try:
            return cli_ui.ask_string(prompt)
        except EOFError:
            logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
            await cleanup_manager.cleanup()
            cleanup_manager.reset_terminal()
            raise OperationAbortedError(
                "Language selection was cancelled by the user."
            ) from None

    @staticmethod
    def _track_title(track: dict[str, Any]) -> str:
        return str(track.get("title") or "")

    @classmethod
    def _is_commentary_track(cls, track: dict[str, Any]) -> bool:
        title = cls._track_title(track)
        return bool(title) and "commentary" in title.lower()

    def _audio_track_language(self, track: dict[str, Any]) -> str | None:
        direct = str(track.get("language") or "").strip()
        if direct:
            return direct
        title = self._track_title(track)
        if not title:
            return None
        logger.debug(f"Attempting to extract language from title: {title}")
        language = self.extract_language_from_title(title)
        if language:
            logger.info(f"Extracted language: {language}")
        return language

    @classmethod
    def _missing_track_label(cls, index: int, track: dict[str, Any]) -> str:
        label = f"Track #{index}"
        title = cls._track_title(track)
        if title:
            return f"{label} (Title: {title})"
        return label

    @staticmethod
    def _parsed_tracks(
        parsed_info: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        raw_tracks = parsed_info.get(key, [])
        if not isinstance(raw_tracks, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], raw_tracks)
            if isinstance(item, dict)
        ]

    def _audio_track_result(
        self, index: int, track: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        if self._is_commentary_track(track):
            logger.debug(
                f"Skipping commentary track: {self._track_title(track)}"
            )
            return None, None
        language = self._audio_track_language(track)
        if language:
            return language, None
        return None, self._missing_track_label(index, track)

    def _scan_audio_tracks(
        self, parsed_info: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        found: list[str] = []
        missing: list[str] = []
        for index, track in enumerate(
            self._parsed_tracks(parsed_info, "audio"), 1
        ):
            language, missing_label = self._audio_track_result(index, track)
            if language:
                found.append(language)
            if missing_label:
                missing.append(missing_label)
        return found, missing

    @staticmethod
    def _log_missing_tracks(kind: str, missing: list[str]) -> None:
        logger.info(f"No {kind} language/s found for the following tracks:")
        for track_info in missing:
            logger.info(f"  - {track_info}")
        logger.info("You must enter (comma-separated) languages")

    async def _prompt_audio_languages(
        self,
        meta: Meta,
        status: dict[str, Any],
        audio_languages: list[str],
        missing: list[str],
    ) -> None:
        if not self._interactive_language_prompt(meta):
            self._mark_language_skip(meta, status, "audio")
            return
        self._log_missing_tracks("audio", missing)
        response = await self._ask_language_string(
            "for all audio tracks, eg: English, Spanish:"
        )
        if not response:
            meta.audio_languages = None
            self._mark_language_skip(meta, status, "audio")
            return
        audio_languages.extend(
            language.strip() for language in response.split(",")
        )
        meta.audio_languages = audio_languages
        meta.write_audio_languages = True

    async def _process_audio_languages(
        self,
        meta: Meta,
        parsed_info: dict[str, Any],
        status: dict[str, Any],
        audio_languages: list[str],
    ) -> None:
        if meta.unattended_audio_skip or audio_languages:
            return
        found, missing = self._scan_audio_tracks(parsed_info)
        audio_languages.extend(found)
        if not found:
            await self._prompt_audio_languages(
                meta, status, audio_languages, missing
            )
        if audio_languages:
            normalized = self._normalized_languages(audio_languages)
            audio_languages[:] = normalized
            meta.audio_languages = audio_languages

    @classmethod
    def _subtitle_track_result(
        cls, index: int, track: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        language = str(track.get("language") or "").strip()
        if language:
            return language, None
        return None, cls._missing_track_label(index, track)

    @classmethod
    def _scan_subtitle_tracks(
        cls, parsed_info: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        found: list[str] = []
        missing: list[str] = []
        for index, track in enumerate(
            cls._parsed_tracks(parsed_info, "text"), 1
        ):
            language, missing_label = cls._subtitle_track_result(index, track)
            if language:
                found.append(language)
            if missing_label:
                missing.append(missing_label)
        return found, missing

    @staticmethod
    def _should_process_subtitles(
        meta: Meta, subtitle_languages: list[str]
    ) -> bool:
        if subtitle_languages:
            return False
        return (
            not meta.unattended_subtitle_skip or not meta.unattended_audio_skip
        )

    async def _prompt_subtitle_languages(
        self,
        meta: Meta,
        status: dict[str, Any],
        subtitle_languages: list[str],
        missing: list[str],
    ) -> None:
        if not self._interactive_language_prompt(meta):
            self._mark_language_skip(meta, status, "subtitle")
            if meta.debug:
                meta.subtitle_languages = ["English, Portuguese"]
            return
        self._log_missing_tracks("subtitle", missing)
        response = await self._ask_language_string(
            "for all subtitle tracks, eg: English, Spanish:"
        )
        if not response:
            meta.subtitle_languages = None
            self._mark_language_skip(meta, status, "subtitle")
            return
        subtitle_languages.extend(
            language.strip() for language in response.split(",")
        )
        meta.subtitle_languages = subtitle_languages
        meta.write_subtitle_languages = True

    async def _process_subtitle_languages(
        self,
        meta: Meta,
        parsed_info: dict[str, Any],
        status: dict[str, Any],
        subtitle_languages: list[str],
    ) -> None:
        if not self._should_process_subtitles(meta, subtitle_languages):
            return
        if "text" not in parsed_info:
            return
        found, missing = self._scan_subtitle_tracks(parsed_info)
        subtitle_languages.extend(found)
        if missing:
            await self._prompt_subtitle_languages(
                meta, status, subtitle_languages, missing
            )
        if subtitle_languages:
            normalized = self._normalized_languages(subtitle_languages)
            subtitle_languages[:] = normalized
            meta.subtitle_languages = subtitle_languages

    async def _process_hardcoded_subtitles(
        self, meta: Meta, status: dict[str, Any]
    ) -> None:
        if not meta.hardcoded_subs:
            return
        if not self._interactive_language_prompt(meta):
            meta.subtitle_languages = ["English"]
            meta.write_hc_languages = True
            return
        response = await self._ask_language_string(
            "What language/s are the hardcoded subtitles?"
        )
        if response:
            meta.subtitle_languages = [response]
            meta.write_hc_languages = True
            return
        meta.subtitle_languages = None
        self._mark_language_skip(meta, status, "subtitle")

    @staticmethod
    def _mark_no_subtitles(meta: Meta, parsed_info: dict[str, Any]) -> None:
        if "text" not in parsed_info and not meta.hardcoded_subs:
            meta.no_subs = True

    async def _process_file_languages(
        self, meta: Meta, status: dict[str, Any]
    ) -> None:
        parsed_info = await self.parsed_mediainfo(meta)
        audio_languages = self._language_list(meta.audio_languages)
        subtitle_languages = self._language_list(meta.subtitle_languages)
        meta.audio_languages = audio_languages
        meta.subtitle_languages = subtitle_languages
        if audio_languages and subtitle_languages:
            return
        await self._process_audio_languages(
            meta, parsed_info, status, audio_languages
        )
        await self._process_subtitle_languages(
            meta, parsed_info, status, subtitle_languages
        )
        await self._process_hardcoded_subtitles(meta, status)
        self._mark_no_subtitles(meta, parsed_info)

    @staticmethod
    def _typed_track_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], value)
            if isinstance(item, dict)
        ]

    @classmethod
    def _without_commentary_tracks(
        cls, tracks: list[dict[str, Any]], label: str
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for track in tracks:
            if track.get("is_commentary"):
                logger.debug(f"Skipping commentary {label}track: {track}")
            else:
                kept.append(track)
        return kept

    @classmethod
    def _merge_track_languages(
        cls, existing: list[str], tracks: list[dict[str, Any]]
    ) -> list[str]:
        ordered = cls._dedupe_preserve_order(existing)
        language_set = set(ordered)
        for track in tracks:
            language = str(track.get("language") or "").strip()
            if language and language not in language_set:
                ordered.append(language)
                language_set.add(language)
        return ordered

    @staticmethod
    def _subtitle_track_values(value: Any) -> list[Any]:
        if isinstance(value, list):
            return cast(list[Any], value)
        return []

    @staticmethod
    def _uses_dict_subtitle_payload(tracks: list[Any]) -> bool:
        return bool(tracks) and isinstance(tracks[0], dict)

    @staticmethod
    def _merge_string_languages(
        existing: list[str], tracks: list[Any]
    ) -> list[str]:
        ordered = list(existing)
        language_set = set(ordered)
        for track in tracks:
            if isinstance(track, str) and track and track not in language_set:
                ordered.append(track)
                language_set.add(track)
        return ordered

    @classmethod
    def _bluray_subtitle_languages(
        cls, existing: list[str], raw_tracks: Any
    ) -> list[str]:
        tracks = cls._subtitle_track_values(raw_tracks)
        ordered = cls._dedupe_preserve_order(existing)
        if not cls._uses_dict_subtitle_payload(tracks):
            return cls._merge_string_languages(ordered, tracks)
        typed = cls._without_commentary_tracks(
            cls._typed_track_list(tracks), "subtitle "
        )
        return cls._merge_track_languages(ordered, typed)

    async def _process_bluray_languages(self, meta: Meta) -> None:
        existing_audio = self._language_list(meta.audio_languages)
        existing_subtitles = self._language_list(meta.subtitle_languages)
        bluray = await self.parse_blu_ray(meta)
        audio_tracks = self._without_commentary_tracks(
            self._typed_track_list(bluray.get("audio", [])), ""
        )
        audio_languages = self._merge_track_languages(
            existing_audio, audio_tracks
        )
        subtitle_languages = self._bluray_subtitle_languages(
            existing_subtitles, bluray.get("subtitles", [])
        )
        if subtitle_languages:
            meta.subtitle_languages = subtitle_languages
        meta.audio_languages = audio_languages

    async def _run_language_processing(
        self, meta: Meta, status: dict[str, Any]
    ) -> None:
        try:
            if meta.is_disc == "BDMV":
                await self._process_bluray_languages(meta)
            else:
                await self._process_file_languages(meta, status)
        except OperationAbortedError:
            raise
        except Exception as error:
            source = "BDInfo" if meta.is_disc == "BDMV" else "mediainfo"
            logger.error(
                f"[red]Error processing {source} languages: {error}[/red]"
            )

    @staticmethod
    def _reset_non_video_languages(meta: Meta) -> None:
        meta.language_checked = True
        meta.audio_languages = []
        meta.subtitle_languages = []

    async def process_desc_language(
        self, meta: Meta, tracker: str = ""
    ) -> None:
        if meta.category not in ["MOVIE", "TV"]:
            self._reset_non_video_languages(meta)
            return
        if meta.language_checked:
            return
        status = self._tracker_status(meta, tracker)
        await self._run_language_processing(meta, status)
        meta.language_checked = True

    async def has_english_language(self, languages: list[str] | str) -> bool:
        """Check if any language in the list contains 'english'"""
        if isinstance(languages, str):
            languages = [languages]
        if not languages:
            return False
        return any("english" in lang.lower() for lang in languages)

    def extract_language_from_title(self, title: str | None) -> str | None:
        """Extract language from title field using langcodes library"""
        if not title:
            return None

        title_lower = title.lower()
        words = re.findall(r"\b[a-zA-Z]+\b", title_lower)

        for word in words:
            language = self._find_language_name(word)
            if language:
                return language

        return None

    def _find_language_name(self, word: str) -> str | None:
        try:
            lang = langcodes.find(word)
        except LanguageTagError, LookupError:
            return None
        if lang and lang.is_valid():
            return lang.display_name()
        return None


languages_manager = LanguagesManager()
