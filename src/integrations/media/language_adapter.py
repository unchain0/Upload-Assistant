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

    async def process_desc_language(
        self, meta: Meta, tracker: str = ""
    ) -> None:
        if meta.category not in ["MOVIE", "TV"]:
            meta.language_checked = True
            meta.audio_languages = []
            meta.subtitle_languages = []
            return

        if meta.language_checked:
            return
        status_dict = (
            meta.tracker_status.setdefault(tracker, {}) if tracker else {}
        )
        if meta.is_disc != "BDMV":
            try:
                parsed_info = await self.parsed_mediainfo(meta)
                audio_languages: list[str] = cast(
                    list[str], meta.audio_languages or []
                )
                subtitle_languages: list[str] = cast(
                    list[str], meta.subtitle_languages or []
                )
                meta.audio_languages = audio_languages
                meta.subtitle_languages = subtitle_languages
                if not audio_languages or not subtitle_languages:
                    if not meta.unattended_audio_skip and not audio_languages:
                        found_any_language = False
                        tracks_without_language = []
                        audio_tracks = cast(
                            list[dict[str, Any]], parsed_info.get("audio", [])
                        )

                        for track_index, audio_track in enumerate(
                            audio_tracks, 1
                        ):
                            language_found: str | None = None

                            # Skip commentary tracks
                            if (
                                "title" in audio_track
                                and "commentary"
                                in audio_track["title"].lower()
                            ):
                                logger.debug(
                                    f"Skipping commentary track: {audio_track['title']}"
                                )
                                continue

                            if "language" in audio_track:
                                language_found = audio_track["language"]

                            if not language_found and "title" in audio_track:
                                logger.debug(
                                    f"Attempting to extract language from title: {audio_track['title']}"
                                )
                                title_language = (
                                    self.extract_language_from_title(
                                        audio_track["title"]
                                    )
                                )
                                if title_language:
                                    language_found = title_language
                                    logger.info(
                                        f"Extracted language: {title_language}"
                                    )

                            if language_found:
                                audio_languages.append(language_found)
                                found_any_language = True
                            else:
                                track_info = f"Track #{track_index}"
                                if "title" in audio_track:
                                    track_info += (
                                        f" (Title: {audio_track['title']})"
                                    )
                                tracks_without_language.append(track_info)

                        if not found_any_language:
                            if not meta.unattended or (
                                meta.unattended and meta.unattended_confirm
                            ):
                                logger.info(
                                    "No audio language/s found for the following tracks:"
                                )
                                for track_info in tracks_without_language:
                                    logger.info(f"  - {track_info}")
                                logger.info(
                                    "You must enter (comma-separated) languages"
                                )
                                try:
                                    audio_lang = cli_ui.ask_string(
                                        "for all audio tracks, eg: English, Spanish:"
                                    )
                                except EOFError:
                                    logger.info(
                                        "\n[red]Exiting on user request (Ctrl+C)[/red]"
                                    )
                                    await cleanup_manager.cleanup()
                                    cleanup_manager.reset_terminal()
                                    raise OperationAbortedError(
                                        "Language selection was cancelled by the user."
                                    ) from None
                                if audio_lang:
                                    audio_languages.extend(
                                        [
                                            lang.strip()
                                            for lang in audio_lang.split(",")
                                        ]
                                    )
                                    meta.audio_languages = audio_languages
                                    meta.write_audio_languages = True
                                else:
                                    meta.audio_languages = None
                                    meta.unattended_audio_skip = True
                                    status_dict["skip_upload"] = True
                            else:
                                meta.unattended_audio_skip = True
                                status_dict["skip_upload"] = True

                        if audio_languages:
                            audio_languages = [
                                lang.split()[0] for lang in audio_languages
                            ]
                            audio_languages = self._dedupe_preserve_order(
                                audio_languages
                            )
                            meta.audio_languages = audio_languages

                    if (
                        not meta.unattended_subtitle_skip
                        or not meta.unattended_audio_skip
                    ) and not subtitle_languages:
                        if "text" in parsed_info:
                            tracks_without_language: list[str] = []
                            text_tracks = cast(
                                list[dict[str, Any]],
                                parsed_info.get("text", []),
                            )

                            for track_index, text_track in enumerate(
                                text_tracks, 1
                            ):
                                if "language" not in text_track:
                                    track_info: str = f"Track #{track_index}"
                                    if "title" in text_track:
                                        track_info += (
                                            f" (Title: {text_track['title']})"
                                        )
                                    tracks_without_language.append(track_info)
                                else:
                                    subtitle_languages.append(
                                        text_track["language"]
                                    )

                            if tracks_without_language:
                                if not meta.unattended or (
                                    meta.unattended and meta.unattended_confirm
                                ):
                                    logger.info(
                                        "No subtitle language/s found for the following tracks:"
                                    )
                                    for track_info in tracks_without_language:
                                        logger.info(f"  - {track_info}")
                                    logger.info(
                                        "You must enter (comma-separated) languages"
                                    )
                                    try:
                                        subtitle_lang = cli_ui.ask_string(
                                            "for all subtitle tracks, eg: English, Spanish:"
                                        )
                                    except EOFError:
                                        logger.info(
                                            "\n[red]Exiting on user request (Ctrl+C)[/red]"
                                        )
                                        await cleanup_manager.cleanup()
                                        cleanup_manager.reset_terminal()
                                        raise OperationAbortedError(
                                            "Language selection was cancelled by the user."
                                        ) from None
                                    if subtitle_lang:
                                        subtitle_languages.extend(
                                            [
                                                lang.strip()
                                                for lang in subtitle_lang.split(
                                                    ","
                                                )
                                            ]
                                        )
                                        meta.subtitle_languages = (
                                            subtitle_languages
                                        )
                                        meta.write_subtitle_languages = True
                                    else:
                                        meta.subtitle_languages = None
                                        meta.unattended_subtitle_skip = True
                                        status_dict["skip_upload"] = True
                                else:
                                    meta.unattended_subtitle_skip = True
                                    status_dict["skip_upload"] = True
                                    if meta.debug:
                                        meta.subtitle_languages = [
                                            "English, Portuguese"
                                        ]

                            if subtitle_languages:
                                subtitle_languages = [
                                    lang.split()[0]
                                    for lang in subtitle_languages
                                ]
                                subtitle_languages = (
                                    self._dedupe_preserve_order(
                                        subtitle_languages
                                    )
                                )
                                meta.subtitle_languages = subtitle_languages

                        if meta.hardcoded_subs:
                            if not meta.unattended or (
                                meta.unattended and meta.unattended_confirm
                            ):
                                try:
                                    hc_lang = cli_ui.ask_string(
                                        "What language/s are the hardcoded subtitles?"
                                    )
                                except EOFError:
                                    logger.info(
                                        "\n[red]Exiting on user request (Ctrl+C)[/red]"
                                    )
                                    await cleanup_manager.cleanup()
                                    cleanup_manager.reset_terminal()
                                    raise OperationAbortedError(
                                        "Language selection was cancelled by the user."
                                    ) from None
                                if hc_lang:
                                    meta.subtitle_languages = [hc_lang]
                                    meta.write_hc_languages = True
                                else:
                                    meta.subtitle_languages = None
                                    meta.unattended_subtitle_skip = True
                                    status_dict["skip_upload"] = True
                            else:
                                meta.subtitle_languages = ["English"]
                                meta.write_hc_languages = True
                        if (
                            "text" not in parsed_info
                            and not meta.hardcoded_subs
                        ):
                            meta.no_subs = True

            except OperationAbortedError:
                raise
            except Exception as e:
                logger.error(
                    f"[red]Error processing mediainfo languages: {e}[/red]"
                )

            meta.language_checked = True
            return

        if meta.is_disc == "BDMV":
            existing_audio_languages: list[str] = meta.audio_languages or []
            existing_subtitle_languages: list[str] = (
                [meta.subtitle_languages]
                if isinstance(meta.subtitle_languages, str)
                else (meta.subtitle_languages or [])
            )
            try:
                bluray = await self.parse_blu_ray(meta)
                audio_tracks = bluray.get("audio", [])
                commentary_tracks = [
                    track
                    for track in audio_tracks
                    if track.get("is_commentary")
                ]
                if commentary_tracks:
                    for track in commentary_tracks:
                        logger.debug(f"Skipping commentary track: {track}")
                        audio_tracks.remove(track)
                audio_languages_ordered: list[str] = (
                    self._dedupe_preserve_order(existing_audio_languages)
                )
                audio_language_set: set[str] = set(audio_languages_ordered)
                for track in audio_tracks:
                    track_language = track.get("language")
                    if (
                        track_language
                        and track_language not in audio_language_set
                    ):
                        audio_languages_ordered.append(track_language)
                        audio_language_set.add(track_language)
                """
                for track in audio_tracks:
                    bitrate_str = track.get("bitrate", "")
                    bitrate_num = None
                    if bitrate_str:
                        match = re.search(r"([\\d.]+)\\s*([kM]?b(?:ps|/s))", bitrate_str.replace(",", ""), re.IGNORECASE)
                        if match:
                            value = float(match.group(1))
                            unit = match.group(2).lower()
                            if unit in ["mbps", "mb/s"]:
                                bitrate_num = int(value * 1000)
                            elif unit in ["kbps", "kb/s"]:
                                bitrate_num = int(value)
                            else:
                                bitrate_num = int(value)

                    lang = track.get("language", "")

                    if bitrate_num is not None and bitrate_num < 258 and lang and lang in audio_language_set and len(lang) > 1 and not meta.bluray_audio_skip:
                        if not meta.unattended or (meta.unattended and meta.unattended_confirm):
                            logger.info(f"Audio track '{lang}' has a bitrate of {bitrate_num} kbps. Probably commentary and should be removed.")
                            try:
                                if cli_ui.ask_yes_no(f"Remove '{lang}' from audio languages?", default=True):
                                    audio_language_set.discard(lang)
                                    audio_languages_ordered = [item for item in audio_languages_ordered if item != lang]
                            except EOFError:
                                logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
                                await cleanup_manager.cleanup()
                                cleanup_manager.reset_terminal()
                                raise OperationAbortedError("Language selection was cancelled by the user.") from None
                        else:
                            audio_language_set.discard(lang)
                            audio_languages_ordered = [item for item in audio_languages_ordered if item != lang]
                        meta.bluray_audio_skip = True
                    """
                subtitle_tracks = bluray.get("subtitles", [])
                sub_commentary_tracks = [
                    track
                    for track in subtitle_tracks
                    if isinstance(track, dict) and track.get("is_commentary")
                ]
                if sub_commentary_tracks:
                    for track in sub_commentary_tracks:
                        logger.debug(
                            f"Skipping commentary subtitle track: {track}"
                        )
                        subtitle_tracks.remove(track)
                subtitle_languages_ordered: list[str] = (
                    self._dedupe_preserve_order(existing_subtitle_languages)
                )
                subtitle_language_set: set[str] = set(
                    subtitle_languages_ordered
                )
                if subtitle_tracks and isinstance(subtitle_tracks[0], dict):
                    for track in subtitle_tracks:
                        if not isinstance(track, dict):
                            continue
                        track_language = track.get("language")
                        if (
                            track_language
                            and track_language not in subtitle_language_set
                        ):
                            subtitle_languages_ordered.append(track_language)
                            subtitle_language_set.add(track_language)
                else:
                    for track in subtitle_tracks:
                        if (
                            isinstance(track, str)
                            and track
                            and track not in subtitle_language_set
                        ):
                            subtitle_languages_ordered.append(track)
                            subtitle_language_set.add(track)
                if subtitle_language_set:
                    meta.subtitle_languages = subtitle_languages_ordered

                meta.audio_languages = audio_languages_ordered
            except OperationAbortedError:
                raise
            except Exception as e:
                logger.error(
                    f"[red]Error processing BDInfo languages: {e}[/red]"
                )

            meta.language_checked = True
            return

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
