# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, ClassVar, cast

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class RailgunPT(NEXUSPHP):
    """
    RAILGUNPT is a CHINESE Private Torrent Tracker for MOVIES / TV / MUSIC / GAME.
    """

    banned_groups = ()
    display_name = "RailgunPT"
    base_url = "https://bilibili.download"
    source_flag = "[bilibili.download] RailgunPT"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE", "MUSIC", "GAME")
    tracker_urls = ("https://bilibili.download",)
    allows_bloated_audio = True
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
        {".rar", ".r00", ".r01", ".r02", ".zip", ".7z"}
    )
    _ATTACHMENT_ARCHIVE_MARKERS: tuple[str, ...] = (
        "sub",
        "subtitle",
        "font",
        "scan",
        "cover",
        "patch",
        "crack",
    )
    _AUDIO_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".aac",
            ".ac3",
            ".ape",
            ".dts",
            ".flac",
            ".m4a",
            ".mp3",
            ".ogg",
            ".opus",
            ".wav",
            ".wma",
        }
    )
    _MUSIC_LAYOUT_DIRS: frozenset[str] = frozenset(
        {
            "aac",
            "ac3",
            "ape",
            "dts",
            "flac",
            "m4a",
            "mp3",
            "ogg",
            "opus",
            "wav",
            "wma",
        }
    )
    _LOSSY_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
        {".aac", ".ac3", ".dts", ".m4a", ".mp3", ".ogg", ".opus", ".wma"}
    )
    _GAME_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
        {".bin", ".chd", ".cso", ".img", ".iso", ".mdf", ".nrg", ".wbfs"}
    )
    _MUSIC_FORMAT_BY_EXTENSION: ClassVar[dict[str, str]] = {
        ".aac": "aac",
        ".ac3": "ac3",
        ".ape": "ape",
        ".dts": "dts",
        ".flac": "flac",
        ".m4a": "aac",
        ".mp3": "mp3",
        ".ogg": "ogg vorbis",
        ".opus": "opus",
        ".wav": "wav",
        ".wma": "wma",
    }
    _BANNED_EXTENSIONS: frozenset[str] = frozenset(
        {".rm", ".rmvb", ".flv", ".torrent", ".url"}
    )
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".avi",
            ".m2ts",
            ".m4v",
            ".mkv",
            ".mov",
            ".mp4",
            ".mpg",
            ".mpeg",
            ".rm",
            ".rmvb",
            ".ts",
            ".vob",
            ".webm",
        }
    )
    _LOW_QUALITY_MARKERS: tuple[str, ...] = (
        "cam",
        "hdcam",
        "tc",
        "telesync",
        "ts",
        "scr",
        "dvdscr",
        "r5",
        "r5 line",
        "halfcd",
    )
    _SOURCE_TOKENS: tuple[str, ...] = (
        "blu-ray",
        "bluray",
        "hddvd",
        "hd dvd",
        "hdtv",
        "uhdtv",
        "dvd",
        "web-dl",
        "webdl",
        "remux",
        "dsr",
        "tv",
    )
    _VIDEO_CODEC_TOKENS: tuple[str, ...] = (
        "avc",
        "h.264",
        "h264",
        "hevc",
        "h.265",
        "h265",
        "mpeg-2",
        "mpeg2",
        "vc-1",
        "vc1",
        "x264",
        "x265",
        "xvid",
    )
    _PACK_SOURCE_TOKENS: tuple[str, ...] = (
        "bluray",
        "hddvd",
        "hdtv",
        "uhdtv",
        "dvd",
        "webdl",
        "webrip",
        "remux",
        "dsr",
        "tv",
    )
    _PACK_CODEC_TOKENS: tuple[str, ...] = (
        "x264",
        "x265",
        "h264",
        "h265",
        "hevc",
        "avc",
        "mpeg2",
        "vc1",
        "xvid",
    )
    _DISC_TYPES: frozenset[str] = frozenset(
        {"bdmv", "dvd", "hddvd_ts", "video_ts"}
    )
    _GAME_PROHIBITED_MARKERS: tuple[str, ...] = (
        "portable",
        "highly compressed",
        "compressed",
        "repack",
        "re packed",
        "repacked",
        "cracked",
        "keygen",
        "unofficial",
        "third party mod",
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, "RAILGUNPT")

    async def load_localized_data(self, meta: Meta) -> None:
        if str(meta.category or "").upper() in {"GAME", "MUSIC"}:
            self.tmdb_data = {}
            return
        await super().load_localized_data(meta)

    async def get_technical_info(self, meta: Meta) -> dict[str, str]:
        if str(meta.category or "").upper() in {"GAME", "MUSIC"}:
            return {}
        return await super().get_technical_info(meta)

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        if str(meta.category or "").upper() not in {"GAME", "MUSIC"}:
            return await super().search_existing(meta)

        original_season, original_episode, original_tv_pack = (
            meta.season,
            meta.episode,
            meta.tv_pack,
        )
        meta.season, meta.episode, meta.tv_pack = "", "", False
        try:
            return await super().search_existing(meta)
        finally:
            meta.season, meta.episode, meta.tv_pack = (
                original_season,
                original_episode,
                original_tv_pack,
            )

    @staticmethod
    def _normalized_token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @staticmethod
    def _metadata_values(value: Any) -> list[Any]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return []

    @staticmethod
    def _music_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        raw_value = cast(dict[object, Any], value)
        return {str(key): item for key, item in raw_value.items()}

    @classmethod
    def _music_tracks(cls, release: dict[str, Any]) -> list[dict[str, Any]]:
        tracks = release.get("tracks")
        if not isinstance(tracks, list):
            return []
        raw_tracks = cast(list[Any], tracks)
        return [
            cls._music_dict(track)
            for track in raw_tracks
            if isinstance(track, dict)
        ]

    @classmethod
    def _music_field(
        cls, release: dict[str, Any], name: str, default: Any = ""
    ) -> Any:
        field = cls._music_dict(
            cls._music_dict(release.get("fields")).get(name)
        )
        return field.get("value", default)

    @classmethod
    def _canonical_music_format(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        return {
            "aac": "aac",
            "ac3": "ac3",
            "ape": "ape",
            "dts": "dts",
            "flac": "flac",
            "m4a": "aac",
            "mp3": "mp3",
            "ogg": "ogg vorbis",
            "oggvorbis": "ogg vorbis",
            "opus": "opus",
            "vorbis": "ogg vorbis",
            "wav": "wav",
            "wma": "wma",
        }.get(token, token)

    @classmethod
    def _release_music_formats(cls, release: dict[str, Any]) -> set[str]:
        formats = {
            cls._canonical_music_format(
                track.get("format") or track.get("codec") or ""
            )
            for track in cls._music_tracks(release)
        }
        formats.discard("")
        return formats

    @classmethod
    def _payload_music_formats(cls, paths: list[Path]) -> set[str]:
        return {
            cls._MUSIC_FORMAT_BY_EXTENSION[path.suffix.casefold()]
            for path in paths
            if path.suffix.casefold() in cls._AUDIO_EXTENSIONS
        }

    @classmethod
    def _music_track_formats(
        cls, release: dict[str, Any], paths: list[Path]
    ) -> set[str]:
        return cls._payload_music_formats(paths) | cls._release_music_formats(
            release
        )

    @staticmethod
    def _title_contains_token(title: str, token: Any) -> bool:
        parts = re.findall(r"[a-z0-9]+", str(token or "").casefold())
        if not parts:
            return False
        pattern = r"[\s._-]*".join(re.escape(part) for part in parts)
        return bool(
            re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", title.casefold())
        )

    @staticmethod
    def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        tokens = normalized.split()
        for marker in markers:
            marker_tokens = (
                re.sub(r"[^a-z0-9]+", " ", marker.casefold()).strip().split()
            )
            if not marker_tokens:
                continue
            marker_text = " ".join(marker_tokens)
            if re.search(
                rf"(?<![a-z0-9]){re.escape(marker_text)}(?![a-z0-9])",
                normalized,
            ):
                return True
            compact_marker = "".join(marker_tokens)
            if compact_marker in tokens:
                return True
        return False

    @classmethod
    def _archive_is_allowed_attachment(cls, path: Path) -> bool:
        return cls._is_attachment_file(path)

    @classmethod
    def _is_archive_file(cls, path: Path) -> bool:
        return (
            path.suffix.casefold() in cls._ARCHIVE_EXTENSIONS
            or cls._is_multipart_archive(path)
        )

    @staticmethod
    def _is_multipart_archive(path: Path) -> bool:
        return bool(
            re.search(
                r"(?:\.r\d{2,}|(?:\.rar|\.zip|\.7z)\.\d{3,}|\.part\d+\.(?:rar|zip|7z))$",
                path.name.casefold(),
            )
        )

    @classmethod
    def _is_attachment_file(cls, path: Path) -> bool:
        stem = re.sub(r"[._-]+", " ", path.stem.casefold())
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(marker)}s?(?![a-z0-9])", stem)
            for marker in cls._ATTACHMENT_ARCHIVE_MARKERS
        )

    @classmethod
    def _pack_token_for_path(
        cls, path: Path, normalized_tokens: dict[str, str]
    ) -> str:
        normalized_name = cls._normalized_token(path.stem)
        matches = [
            canonical
            for token, canonical in normalized_tokens.items()
            if token in normalized_name
        ]
        if not matches:
            return ""
        return max(
            matches, key=lambda token: len(cls._normalized_token(token))
        )

    @classmethod
    def _pack_tokens(
        cls, paths: list[Path], tokens: tuple[str, ...]
    ) -> set[str]:
        normalized_tokens = {
            cls._normalized_token(token): token for token in tokens
        }
        found = {
            cls._pack_token_for_path(path, normalized_tokens) for path in paths
        }
        found.discard("")
        return found

    @staticmethod
    def _resolution_height(value: Any) -> int | None:
        match = re.search(r"(\d{3,4})", str(value or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _sd_sports_source_allowed(meta: Meta, source: str) -> bool:
        return bool(
            str(meta.category or "").upper() == "TV"
            and source in {"tv", "dsr"}
        )

    def _sd_is_sports_exception(self, meta: Meta, source: str) -> bool:
        if not self._sd_sports_source_allowed(meta, source):
            return False
        values = [
            str(value).casefold().strip()
            for value in [
                *self._metadata_values(meta.genres),
                *self._metadata_values(meta.keywords),
            ]
        ]
        return any(value in {"sport", "sports"} for value in values)

    @staticmethod
    def _sd_is_dvd_exception(
        disc_type: str, release_type: str, source: str
    ) -> bool:
        if "dvd" in disc_type:
            return True
        if release_type in {"dvd", "dvdrip", "cndvdrip"}:
            return True
        return source in {"dvd", "dvdrip", "cndvdrip"}

    @staticmethod
    def _sd_is_hd_encode_exception(release_type: str, source: str) -> bool:
        if release_type != "encode":
            return False
        return source in {"bluray", "uhdbluray", "hddvd", "hdtv", "uhdtv"}

    def _valid_sd_release(self, meta: Meta, height: int) -> bool:
        if height < 480:
            return False
        source = self._normalized_token(meta.source)
        if self._sd_is_sports_exception(meta, source):
            return True
        release_type = self._normalized_token(meta.type)
        disc_type = str(meta.is_disc or "").casefold()
        if self._sd_is_dvd_exception(disc_type, release_type, source):
            return True
        return self._sd_is_hd_encode_exception(release_type, source)

    def _size_exception_applies(self, meta: Meta, category: str) -> bool:
        if category == "GAME" and meta.software:
            return True
        if category != "MUSIC" or not isinstance(meta.music_release, dict):
            return False
        release_type = (
            str(self._music_field(meta.music_release, "release_type", ""))
            .casefold()
            .strip()
        )
        return release_type in {"single", "single album", "single track"}

    @staticmethod
    def _channel_count(value: Any) -> float | None:
        match = re.search(
            r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*(?:channels?|ch))?",
            str(value or "").casefold(),
        )
        return float(match.group(1)) if match else None

    @classmethod
    def _music_channel_counts(
        cls, release: dict[str, Any]
    ) -> list[float | None]:
        return [
            cls._channel_count(track.get("channels"))
            for track in cls._music_tracks(release)
        ]

    @classmethod
    def _music_audio_paths(cls, paths: list[Path]) -> list[Path]:
        return [
            path
            for path in paths
            if path.suffix.casefold() in cls._AUDIO_EXTENSIONS
        ]

    @staticmethod
    def _music_source_root(meta: Meta) -> Path | None:
        if not meta.path:
            return None
        try:
            source_root = Path(str(meta.path)).resolve(strict=True)
        except OSError, RuntimeError:
            return None
        return source_root if source_root.is_dir() else None

    @staticmethod
    def _resolved_music_audio_path(
        listed_path: Path, source_root: Path
    ) -> Path | None:
        try:
            resolved_path = listed_path.resolve(strict=True)
            resolved_path.relative_to(source_root)
        except OSError, RuntimeError, ValueError:
            return None
        if listed_path != resolved_path or not resolved_path.is_file():
            return None
        return resolved_path

    @classmethod
    def _resolved_music_audio(
        cls, audio_paths: list[Path], source_root: Path
    ) -> list[Path] | None:
        if not source_root.is_dir():
            return None
        resolved: list[Path] = []
        for listed_path in audio_paths:
            resolved_path = cls._resolved_music_audio_path(
                listed_path, source_root
            )
            if resolved_path is None:
                return None
            resolved.append(resolved_path)
        return resolved

    @staticmethod
    def _common_music_root(resolved_audio: list[Path]) -> Path:
        payload_root = resolved_audio[0].parent
        for audio_path in resolved_audio[1:]:
            while (
                payload_root != audio_path.parent
                and payload_root not in audio_path.parent.parents
            ):
                payload_root = payload_root.parent
        return payload_root

    @classmethod
    def _is_music_layout_root(cls, path: Path) -> bool:
        numbered = re.fullmatch(
            r"(?:cd|disc|disk)[ ._-]?\d+", path.name, re.IGNORECASE
        )
        return bool(numbered or path.name.casefold() in cls._MUSIC_LAYOUT_DIRS)

    @classmethod
    def _promote_music_root(
        cls, payload_root: Path, source_root: Path
    ) -> tuple[Path, list[Path]]:
        candidates = [payload_root]
        while payload_root != source_root and cls._is_music_layout_root(
            payload_root
        ):
            payload_root = payload_root.parent
            candidates.append(payload_root)
        return payload_root, candidates

    @staticmethod
    def _music_root_contains_audio(
        payload_root: Path, resolved_audio: list[Path]
    ) -> bool:
        if payload_root.parent == payload_root or not payload_root.is_dir():
            return False
        try:
            for audio_path in resolved_audio:
                audio_path.relative_to(payload_root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _declared_music_root(
        release: dict[str, Any], candidates: list[Path]
    ) -> Path | None:
        declared_root = release.get("root")
        if not declared_root:
            return None
        try:
            declared_path = Path(str(declared_root)).resolve()
        except OSError, RuntimeError:
            return None
        return declared_path if declared_path in candidates else None

    @classmethod
    def _absolute_music_audio_paths(
        cls, paths: list[Path]
    ) -> list[Path] | None:
        audio_paths = cls._music_audio_paths(paths)
        if not audio_paths:
            return None
        if not all(path.is_absolute() for path in audio_paths):
            return None
        return audio_paths

    @classmethod
    def _selected_music_payload_root(
        cls,
        release: dict[str, Any],
        payload_root: Path,
        candidates: list[Path],
    ) -> Path:
        declared = cls._declared_music_root(release, candidates)
        return declared if declared is not None else payload_root

    @classmethod
    def _music_payload_root(
        cls, meta: Meta, release: dict[str, Any], paths: list[Path]
    ) -> Path | None:
        audio_paths = cls._absolute_music_audio_paths(paths)
        if audio_paths is None:
            return None
        source_root = cls._music_source_root(meta)
        if source_root is None:
            return None
        resolved_audio = cls._resolved_music_audio(audio_paths, source_root)
        if not resolved_audio:
            return None
        payload_root = cls._common_music_root(resolved_audio)
        payload_root, candidates = cls._promote_music_root(
            payload_root, source_root
        )
        if not cls._music_root_contains_audio(payload_root, resolved_audio):
            return None
        return cls._selected_music_payload_root(
            release, payload_root, candidates
        )

    @staticmethod
    def _cue_lines(cue_path: Path) -> list[str] | None:
        try:
            content = cue_path.read_text(encoding="utf-8", errors="replace")
        except OSError, UnicodeError:
            return None
        return content.splitlines()

    @staticmethod
    def _cue_patterns() -> tuple[
        re.Pattern[str], re.Pattern[str], re.Pattern[str], re.Pattern[str]
    ]:
        return (
            re.compile(
                r"^\s*FILE\s+(?:\"([^\"]+)\"|(\S+))\s+"
                r"(?:BINARY|MOTOROLA|WAVE|AIFF|MP3)\s*$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^\s*TRACK\s+(?:0[1-9]|[1-9]\d)\s+"
                r"(?:AUDIO|MODE\d/\d+|CDI/\d+)\s*$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^\s*INDEX\s+(\d{2})\s+(\d{2}):([0-5]\d):"
                r"(?:[0-6]\d|7[0-4])\s*$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^\s*(?:REM(?:\s+.*)?|(?:PERFORMER|TITLE|SONGWRITER|"
                r"CATALOG|ISRC|CDTEXTFILE|FLAGS|PREGAP|POSTGAP)\s+.+)\s*$",
                re.IGNORECASE,
            ),
        )

    @staticmethod
    def _cue_reference(match: re.Match[str]) -> tuple[str, str]:
        first = match.group(1) or ""
        second = match.group(2) or ""
        return first, second

    @classmethod
    def _consume_cue_line(
        cls,
        line_number: int,
        line: str,
        patterns: tuple[
            re.Pattern[str], re.Pattern[str], re.Pattern[str], re.Pattern[str]
        ],
        references: list[tuple[str, str]],
        file_positions: list[int],
        track_positions: list[int],
        index_positions: list[tuple[int, int]],
    ) -> bool:
        if not line.strip():
            return True
        file_match = patterns[0].fullmatch(line)
        if file_match is not None:
            references.append(cls._cue_reference(file_match))
            file_positions.append(line_number)
            return True
        if patterns[1].fullmatch(line) is not None:
            track_positions.append(line_number)
            return True
        index_match = patterns[2].fullmatch(line)
        if index_match is not None:
            index_positions.append((line_number, int(index_match.group(1))))
            return True
        return patterns[3].fullmatch(line) is not None

    @classmethod
    def _parse_cue_document(
        cls, lines: list[str]
    ) -> (
        tuple[
            list[tuple[str, str]], list[int], list[int], list[tuple[int, int]]
        ]
        | None
    ):
        references: list[tuple[str, str]] = []
        file_positions: list[int] = []
        track_positions: list[int] = []
        index_positions: list[tuple[int, int]] = []
        patterns = cls._cue_patterns()
        for line_number, line in enumerate(lines):
            valid = cls._consume_cue_line(
                line_number,
                line,
                patterns,
                references,
                file_positions,
                track_positions,
                index_positions,
            )
            if not valid:
                return None
        return references, file_positions, track_positions, index_positions

    @staticmethod
    def _cue_sections_present(
        references: list[tuple[str, str]],
        file_positions: list[int],
        track_positions: list[int],
        index_positions: list[tuple[int, int]],
    ) -> bool:
        return bool(
            references
            and file_positions
            and track_positions
            and index_positions
            and track_positions[0] >= file_positions[0]
        )

    @staticmethod
    def _cue_files_have_tracks(
        lines: list[str], file_positions: list[int], track_positions: list[int]
    ) -> bool:
        for index, file_position in enumerate(file_positions):
            next_file = (
                file_positions[index + 1]
                if index + 1 < len(file_positions)
                else len(lines)
            )
            if not any(
                file_position < track_position < next_file
                for track_position in track_positions
            ):
                return False
        return True

    @staticmethod
    def _track_has_index_one(
        track_position: int,
        next_track: int,
        index_positions: list[tuple[int, int]],
    ) -> bool:
        return any(
            track_position < index_position < next_track and index_number == 1
            for index_position, index_number in index_positions
        )

    @classmethod
    def _cue_tracks_have_index_one(
        cls,
        lines: list[str],
        track_positions: list[int],
        index_positions: list[tuple[int, int]],
    ) -> bool:
        for index, track_position in enumerate(track_positions):
            next_track = (
                track_positions[index + 1]
                if index + 1 < len(track_positions)
                else len(lines)
            )
            if not cls._track_has_index_one(
                track_position, next_track, index_positions
            ):
                return False
        return True

    @classmethod
    def _cue_structure_valid(
        cls,
        lines: list[str],
        parsed: tuple[
            list[tuple[str, str]],
            list[int],
            list[int],
            list[tuple[int, int]],
        ],
    ) -> bool:
        references, file_positions, track_positions, index_positions = parsed
        if not cls._cue_sections_present(
            references, file_positions, track_positions, index_positions
        ):
            return False
        if not cls._cue_files_have_tracks(
            lines, file_positions, track_positions
        ):
            return False
        return cls._cue_tracks_have_index_one(
            lines, track_positions, index_positions
        )

    @staticmethod
    def _resolved_cue_audio(audio_paths: list[Path]) -> set[Path] | None:
        resolved: set[Path] = set()
        try:
            for path in audio_paths:
                candidate = path.resolve(strict=True)
                if candidate.is_file():
                    resolved.add(candidate)
        except OSError, RuntimeError:
            return None
        return resolved

    @staticmethod
    def _resolved_cue_reference(
        cue_path: Path,
        payload_root: Path,
        reference: str,
        resolved_audio: set[Path],
    ) -> Path | None:
        reference_path = Path(reference.replace("\\", "/"))
        if reference_path.is_absolute() or ".." in reference_path.parts:
            return None
        try:
            candidate = (cue_path.parent / reference_path).resolve(strict=True)
            candidate.relative_to(payload_root)
        except OSError, RuntimeError, ValueError:
            return None
        return candidate if candidate in resolved_audio else None

    @classmethod
    def _resolved_cue_references(
        cls,
        cue_path: Path,
        payload_root: Path,
        references: list[tuple[str, str]],
        resolved_audio: set[Path],
    ) -> set[Path] | None:
        resolved: set[Path] = set()
        for quoted, bare in references:
            candidate = cls._resolved_cue_reference(
                cue_path, payload_root, quoted or bare, resolved_audio
            )
            if candidate is None:
                return None
            resolved.add(candidate)
        return resolved

    @classmethod
    def _cue_references_audio(
        cls, cue_path: Path, payload_root: Path, audio_paths: list[Path]
    ) -> set[Path] | None:
        lines = cls._cue_lines(cue_path)
        if lines is None:
            return None
        parsed = cls._parse_cue_document(lines)
        if parsed is None or not cls._cue_structure_valid(lines, parsed):
            return None
        resolved_audio = cls._resolved_cue_audio(audio_paths)
        if resolved_audio is None:
            return None
        references = parsed[0]
        return cls._resolved_cue_references(
            cue_path, payload_root, references, resolved_audio
        )

    @staticmethod
    def _music_cue_candidate(
        cue_path: Path, payload_root: Path
    ) -> Path | None:
        if ".." in cue_path.parts:
            return None
        return cue_path if cue_path.is_absolute() else payload_root / cue_path

    @staticmethod
    def _validated_music_cue(
        candidate: Path, payload_root: Path
    ) -> Path | None:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(payload_root)
        except OSError, RuntimeError, ValueError:
            return None
        if candidate.absolute() != resolved:
            return None
        return resolved if resolved.is_file() else None

    @classmethod
    def _resolve_music_cue(
        cls, cue_path: Path, payload_root: Path
    ) -> Path | None:
        candidate = cls._music_cue_candidate(cue_path, payload_root)
        if candidate is None:
            return None
        return cls._validated_music_cue(candidate, payload_root)

    @staticmethod
    def _direct_music_cues(paths: list[Path]) -> list[Path]:
        return [path for path in paths if path.suffix.casefold() == ".cue"]

    @classmethod
    def _auxiliary_music_cues(cls, release: dict[str, Any]) -> list[Path]:
        cues_value = cls._music_dict(release.get("auxiliary")).get("cues")
        if not isinstance(cues_value, list):
            return []
        cue_paths: list[Path] = []
        for cue in cast(list[Any], cues_value):
            cue_path = Path(str(cue))
            if (
                not cue_path.is_absolute()
                and cue_path.suffix.casefold() == ".cue"
            ):
                cue_paths.append(cue_path)
        return cue_paths

    @classmethod
    def _music_cue_coverage(
        cls,
        cue_paths: list[Path],
        payload_root: Path,
        audio_paths: list[Path],
    ) -> set[Path]:
        covered: set[Path] = set()
        for cue_path in cue_paths:
            resolved_cue = cls._resolve_music_cue(cue_path, payload_root)
            if resolved_cue is None:
                continue
            references = cls._cue_references_audio(
                resolved_cue, payload_root, audio_paths
            )
            if references is not None:
                covered.update(references)
        return covered

    @staticmethod
    def _required_music_audio(audio_paths: list[Path]) -> set[Path] | None:
        try:
            return {path.resolve(strict=True) for path in audio_paths}
        except OSError, RuntimeError:
            return None

    @classmethod
    def _music_cue_is_present(
        cls, meta: Meta, release: dict[str, Any], paths: list[Path]
    ) -> bool:
        audio_paths = cls._music_audio_paths(paths)
        payload_root = cls._music_payload_root(meta, release, paths)
        if payload_root is None:
            return False
        cue_paths = [
            *cls._direct_music_cues(paths),
            *cls._auxiliary_music_cues(release),
        ]
        covered_audio = cls._music_cue_coverage(
            cue_paths, payload_root, audio_paths
        )
        required_audio = cls._required_music_audio(audio_paths)
        if required_audio is None:
            return False
        return required_audio.issubset(covered_audio)

    @classmethod
    def _lossy_audio_paths(cls, audio_paths: list[Path]) -> list[Path]:
        return [
            path
            for path in audio_paths
            if path.suffix.casefold() in cls._LOSSY_AUDIO_EXTENSIONS
        ]

    def _audio_channel_counts(
        self, meta: Meta, release: dict[str, Any]
    ) -> list[float]:
        counts = [self._channel_count(meta.channels)]
        if str(meta.category or "").upper() == "MUSIC":
            counts.extend(self._music_channel_counts(release))
        return [channel for channel in counts if channel is not None]

    def _lossy_channels_allowed(
        self, lossy_paths: list[Path], channel_counts: list[float]
    ) -> bool:
        if not lossy_paths:
            return True
        if channel_counts and all(
            channel >= 5.1 for channel in channel_counts
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Lossy audio files must meet the "
            "5.1-channel minimum.[/bold red]"
        )
        return False

    def _multitrack_cue_allowed(
        self,
        meta: Meta,
        release: dict[str, Any],
        paths: list[Path],
        audio_paths: list[Path],
    ) -> bool:
        if len(audio_paths) <= 1:
            return True
        if self._music_cue_is_present(meta, release, paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Multi-track audio uploads must include "
            "a cue sheet.[/bold red]"
        )
        return False

    def _validate_audio_rules(self, meta: Meta, paths: list[Path]) -> bool:
        audio_paths = self._music_audio_paths(paths)
        if not audio_paths:
            return True
        release = (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )
        channel_counts = self._audio_channel_counts(meta, release)
        if not self._lossy_channels_allowed(
            self._lossy_audio_paths(audio_paths), channel_counts
        ):
            return False
        return self._multitrack_cue_allowed(meta, release, paths, audio_paths)

    def _music_encoding_allowed(
        self, release: dict[str, Any], audio_paths: list[Path]
    ) -> bool:
        if len(self._music_track_formats(release, audio_paths)) <= 1:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Packed audio releases must use one "
            "encoding format.[/bold red]"
        )
        return False

    def _music_album_pack_allowed(self, release: dict[str, Any]) -> bool:
        albums = {
            str(track.get("album", "")).casefold().strip()
            for track in self._music_tracks(release)
            if str(track.get("album", "")).strip()
        }
        if len(albums) <= 1 or len(albums) >= 5:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Music packs must contain at least five "
            "albums.[/bold red]"
        )
        return False

    def _validate_music_rules(self, meta: Meta, paths: list[Path]) -> bool:
        audio_paths = self._music_audio_paths(paths)
        if not audio_paths:
            logger.info(
                f"{self.tracker}: [bold red]Music uploads must contain "
                "supported audio files.[/bold red]"
            )
            return False
        if not self._validate_audio_rules(meta, paths):
            return False
        release = (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )
        if not self._music_encoding_allowed(release, audio_paths):
            return False
        return self._music_album_pack_allowed(release)

    @classmethod
    def _game_image_paths(cls, paths: list[Path]) -> list[Path]:
        return [
            path
            for path in paths
            if path.suffix.casefold() in cls._GAME_IMAGE_EXTENSIONS
        ]

    @staticmethod
    def _game_context(meta: Meta, paths: list[Path]) -> str:
        return " ".join(
            [str(meta.name or ""), *(path.name for path in paths)]
        ).casefold()

    def _game_has_prohibited_marker(self, context: str) -> bool:
        return any(
            self._contains_marker(context, (marker,))
            for marker in self._GAME_PROHIBITED_MARKERS
        )

    def _validate_game_rules(self, meta: Meta, paths: list[Path]) -> bool:
        if meta.software:
            return True
        if not self._game_image_paths(paths):
            logger.info(
                f"{self.tracker}: [bold red]PC game uploads must contain an "
                "original disc image.[/bold red]"
            )
            return False
        if self._game_has_prohibited_marker(self._game_context(meta, paths)):
            logger.info(
                f"{self.tracker}: [bold red]Portable, highly compressed, "
                "repacked, or modified game releases are not allowed.[/bold red]"
            )
            return False
        return True

    def _validate_pack_consistency(self, paths: list[Path]) -> bool:
        signatures: list[tuple[str, str, str]] = []
        for path in paths:
            resolution = re.search(
                r"\b(480[pi]|576[pi]|720p|1080[pi]|2160p)\b",
                path.name,
                re.IGNORECASE,
            )
            sources = self._pack_tokens([path], self._PACK_SOURCE_TOKENS)
            codecs = self._pack_tokens([path], self._PACK_CODEC_TOKENS)
            if not resolution or len(sources) != 1 or len(codecs) != 1:
                return False
            signatures.append(
                (
                    resolution.group(1).casefold(),
                    next(iter(sources)),
                    next(iter(codecs)),
                )
            )
        return len(set(signatures)) == 1

    def _title_resolution_allowed(self, meta: Meta, title: str) -> bool:
        resolution = str(meta.resolution or "").strip()
        if not resolution:
            return True
        return self._title_contains_token(title, resolution)

    def _title_source_allowed(self, title: str) -> bool:
        return any(
            self._title_contains_token(title, token)
            for token in self._SOURCE_TOKENS
        )

    def _title_codec_allowed(self, title: str) -> bool:
        return any(
            self._title_contains_token(title, token)
            for token in self._VIDEO_CODEC_TOKENS
        )

    @staticmethod
    def _title_movie_year_allowed(meta: Meta, title: str) -> bool:
        if str(meta.category or "").upper() != "MOVIE":
            return True
        if not meta.year:
            return True
        return str(meta.year) in title

    @staticmethod
    def _tv_title_pattern(meta: Meta) -> str:
        if meta.tv_pack:
            return r"\bS\d{1,3}(?:E\d{1,4})?\b"
        return r"(?:\bS\d{1,3}E\d{1,4}\b|\b\d{4}[.-]\d{2}[.-]\d{2}\b)"

    @classmethod
    def _title_tv_marker_allowed(cls, meta: Meta, title: str) -> bool:
        if str(meta.category or "").upper() != "TV":
            return True
        return bool(
            re.search(cls._tv_title_pattern(meta), title, re.IGNORECASE)
        )

    def _title_has_required_video_tokens(self, meta: Meta, title: str) -> bool:
        return all(
            (
                self._title_resolution_allowed(meta, title),
                self._title_source_allowed(title),
                self._title_codec_allowed(title),
                self._title_movie_year_allowed(meta, title),
                self._title_tv_marker_allowed(meta, title),
            )
        )

    def _category_supported(self, category: str) -> bool:
        if category in self.supported_categories:
            return True
        logger.info(
            f"{self.tracker}: [bold red]This upload category is not supported "
            "by RailgunPT.[/bold red]"
        )
        return False

    def _adult_content_allowed(self, meta: Meta) -> bool:
        if not bool(meta.adult_media or meta.tmdb_adult_media or meta.nsfw):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Pornographic or sensitive adult content "
            "is not allowed.[/bold red]"
        )
        return False

    def _political_content_allowed(self, meta: Meta) -> bool:
        values = [
            str(value).casefold().strip()
            for value in [
                *self._metadata_values(meta.genres),
                *self._metadata_values(meta.keywords),
            ]
        ]
        markers = {"politics", "political", "political propaganda"}
        if not markers.intersection(values):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Politically sensitive content is not "
            "allowed.[/bold red]"
        )
        return False

    @staticmethod
    def _source_size(meta: Meta) -> int:
        try:
            return int(meta.source_size)
        except TypeError, ValueError, OverflowError:
            return 0

    def _torrent_size_allowed(self, meta: Meta, category: str) -> bool:
        source_size = self._source_size(meta)
        if source_size >= 100 * 1024 * 1024:
            return True
        if self._size_exception_applies(meta, category):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Torrents must be at least 100 MiB unless "
            "a RailgunPT exception applies.[/bold red]"
        )
        return False

    def _basic_content_checks(self, meta: Meta, category: str) -> bool:
        if not self._category_supported(category):
            return False
        if not self._adult_content_allowed(meta):
            return False
        if not self._political_content_allowed(meta):
            return False
        return self._torrent_size_allowed(meta, category)

    def _file_paths(self, meta: Meta) -> list[Path] | None:
        raw_filelist = [] if meta.filelist is None else meta.filelist
        if isinstance(raw_filelist, (list, tuple, set)):
            return [
                Path(str(item)) for item in raw_filelist if str(item).strip()
            ]
        logger.info(
            f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
        )
        return None

    @classmethod
    def _video_paths(cls, paths: list[Path]) -> list[Path]:
        return [
            path
            for path in paths
            if path.suffix.casefold() in cls._VIDEO_EXTENSIONS
        ]

    def _banned_file_allowed(self, path: Path) -> bool:
        if path.suffix.casefold() not in self._BANNED_EXTENSIONS:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Unsupported or spam file found: "
            f"{path.name}.[/bold red]"
        )
        return False

    def _archive_file_allowed(self, path: Path) -> bool:
        if self._is_archive_file(
            path
        ) and not self._archive_is_allowed_attachment(path):
            logger.info(
                f"{self.tracker}: [bold red]Archived files are not allowed: "
                f"{path.name}.[/bold red]"
            )
            return False
        if self._is_multipart_archive(path):
            logger.info(
                f"{self.tracker}: [bold red]Multipart archives are not allowed: "
                f"{path.name}.[/bold red]"
            )
            return False
        return True

    def _advertising_file_allowed(self, path: Path) -> bool:
        lowered_name = path.name.casefold()
        markers = ("downloaded from", "torrent downloaded")
        if not any(marker in lowered_name for marker in markers):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Advertising or tracker-reference "
            "files are not allowed.[/bold red]"
        )
        return False

    def _file_path_allowed(self, path: Path) -> bool:
        if not self._banned_file_allowed(path):
            return False
        if not self._archive_file_allowed(path):
            return False
        return self._advertising_file_allowed(path)

    def _all_file_paths_allowed(self, paths: list[Path]) -> bool:
        return all(self._file_path_allowed(path) for path in paths)

    @classmethod
    def _attachment_paths(cls, paths: list[Path]) -> list[Path]:
        return [path for path in paths if cls._is_attachment_file(path)]

    @classmethod
    def _attachments_consistent(cls, paths: list[Path]) -> bool:
        attachments = cls._attachment_paths(paths)
        if not attachments:
            return True
        archive_states = {cls._is_archive_file(path) for path in attachments}
        return len(archive_states) <= 1

    def _attachment_rules_allowed(self, paths: list[Path]) -> bool:
        if self._attachments_consistent(paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Subtitle, crack, patch, font, and scan "
            "attachments must be consistently archived or unarchived.[/bold red]"
        )
        return False

    def _validated_file_payload(self, meta: Meta) -> list[Path] | None:
        paths = self._file_paths(meta)
        if paths is None:
            return None
        if not self._all_file_paths_allowed(paths):
            return None
        if not self._attachment_rules_allowed(paths):
            return None
        if not self._validate_audio_rules(meta, paths):
            return None
        return paths

    def _non_video_category_result(
        self, meta: Meta, category: str, paths: list[Path]
    ) -> bool | None:
        if category == "MUSIC":
            return self._validate_music_rules(meta, paths)
        if category == "GAME":
            return self._validate_game_rules(meta, paths)
        return None

    def _sample_video_allowed(self, video_paths: list[Path]) -> bool:
        if not video_paths:
            return True
        if not all("sample" in path.stem.casefold() for path in video_paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]An individual sample cannot be uploaded "
            "as the main torrent.[/bold red]"
        )
        return False

    @staticmethod
    def _main_video_paths(video_paths: list[Path]) -> list[Path]:
        return [
            path
            for path in video_paths
            if "sample" not in path.stem.casefold()
        ]

    def _main_video_payload_allowed(
        self, meta: Meta, main_video_paths: list[Path]
    ) -> bool:
        if meta.is_disc or main_video_paths:
            return True
        logger.info(
            f"{self.tracker}: [bold red]A non-disc upload must contain at least "
            "one recognized video file.[/bold red]"
        )
        return False

    @staticmethod
    def _release_context(meta: Meta) -> str:
        return " ".join(
            str(value or "")
            for value in (meta.name, meta.type, meta.source, meta.uuid)
        )

    def _low_quality_source_allowed(self, release_context: str) -> bool:
        if not self._contains_marker(
            release_context, self._LOW_QUALITY_MARKERS
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]CAM/TC/TS/SCR/R5 and similar low-quality "
            "sources are not allowed.[/bold red]"
        )
        return False

    def _codec_is_realvideo(self, value: str) -> bool:
        if "realvideo" in value.casefold():
            return True
        token = self._normalized_token(value)
        return token in {"rv", "rv10", "rv20", "rv30", "rv40"}

    def _realvideo_allowed(self, meta: Meta) -> bool:
        codec_values = (
            str(meta.video_codec or ""),
            str(meta.video_encode or ""),
        )
        if not any(self._codec_is_realvideo(value) for value in codec_values):
            return True
        logger.info(
            f"{self.tracker}: [bold red]RealVideo encodes are not allowed.[/bold red]"
        )
        return False

    def _video_quality_context_allowed(
        self, meta: Meta, release_context: str
    ) -> bool:
        if not self._low_quality_source_allowed(release_context):
            return False
        return self._realvideo_allowed(meta)

    def _sd_resolution_allowed(
        self, meta: Meta, height: int, release_context: str
    ) -> bool:
        if height >= 720:
            return True
        if not self._valid_sd_release(meta, height):
            logger.info(
                f"{self.tracker}: [bold red]SD uploads must be at least 480p "
                "and sourced from HD media or DVD.[/bold red]"
            )
            return False
        if "upscale" in release_context.casefold():
            logger.info(
                f"{self.tracker}: [bold red]Upscaled SD-mastered content is not "
                "allowed.[/bold red]"
            )
            return False
        return True

    def _video_resolution_allowed(
        self, meta: Meta, release_context: str
    ) -> bool:
        if str(meta.is_disc or "").casefold():
            return True
        height = self._resolution_height(meta.resolution)
        if height is None:
            logger.info(
                f"{self.tracker}: [bold red]A supported video resolution is "
                "required.[/bold red]"
            )
            return False
        return self._sd_resolution_allowed(meta, height, release_context)

    def _video_title_allowed(self, meta: Meta) -> bool:
        release_name = str(meta.name or "").strip()
        if release_name and self._title_has_required_video_tokens(
            meta, release_name
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Title must include the required "
            "year/season, resolution, source, and video codec "
            "information.[/bold red]"
        )
        return False

    @staticmethod
    def _movie_pack_required(
        category: str, main_video_paths: list[Path], disc_type: str
    ) -> bool:
        return bool(
            category == "MOVIE"
            and len(main_video_paths) > 1
            and disc_type not in RailgunPT._DISC_TYPES
        )

    def _movie_pack_allowed(
        self,
        release_name: str,
        main_video_paths: list[Path],
    ) -> bool:
        markers = ("boxset", "box set", "collection", "trilogy")
        if not any(marker in release_name.casefold() for marker in markers):
            logger.info(
                f"{self.tracker}: [bold red]Movie packs must be identifiable "
                "official box-set collections.[/bold red]"
            )
            return False
        if self._validate_pack_consistency(main_video_paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Packed videos must use the same source "
            "type, resolution, and video codec.[/bold red]"
        )
        return False

    def _tv_pack_allowed(
        self, meta: Meta, main_video_paths: list[Path]
    ) -> bool:
        if not meta.tv_pack or len(main_video_paths) <= 1:
            return True
        if self._validate_pack_consistency(main_video_paths):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Packed videos must use the same source "
            "type, resolution, and video codec.[/bold red]"
        )
        return False

    def _video_pack_rules_allowed(
        self,
        meta: Meta,
        category: str,
        main_video_paths: list[Path],
    ) -> bool:
        release_name = str(meta.name or "").strip()
        disc_type = str(meta.is_disc or "").casefold()
        if self._movie_pack_required(category, main_video_paths, disc_type):
            return self._movie_pack_allowed(release_name, main_video_paths)
        return self._tv_pack_allowed(meta, main_video_paths)

    def _video_core_context(
        self, meta: Meta, paths: list[Path]
    ) -> tuple[list[Path], str] | None:
        video_paths = self._video_paths(paths)
        if not self._sample_video_allowed(video_paths):
            return None
        main_video_paths = self._main_video_paths(video_paths)
        if not self._main_video_payload_allowed(meta, main_video_paths):
            return None
        release_context = self._release_context(meta)
        if not self._video_quality_context_allowed(meta, release_context):
            return None
        return main_video_paths, release_context

    def _video_identity_rules_allowed(
        self, meta: Meta, release_context: str
    ) -> bool:
        if not self._video_resolution_allowed(meta, release_context):
            return False
        return self._video_title_allowed(meta)

    def _video_rules_allowed(
        self, meta: Meta, category: str, paths: list[Path]
    ) -> bool:
        context = self._video_core_context(meta, paths)
        if context is None:
            return False
        main_video_paths, release_context = context
        if not self._video_identity_rules_allowed(meta, release_context):
            return False
        return self._video_pack_rules_allowed(meta, category, main_video_paths)

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()
        if not self._basic_content_checks(meta, category):
            return False
        paths = self._validated_file_payload(meta)
        if paths is None:
            return False
        category_result = self._non_video_category_result(
            meta, category, paths
        )
        if category_result is not None:
            return category_result
        return self._video_rules_allowed(meta, category, paths)

    def _category_text(self, value: Any) -> str:
        return ", ".join(
            str(item) for item in self._metadata_values(value)
        ).lower()

    @staticmethod
    def _category_keyword_match(text: str, keywords: tuple[str, ...]) -> bool:
        return any(
            re.search(
                rf"(^|,\s*){re.escape(keyword)}(\s*,|$)",
                text,
                re.IGNORECASE,
            )
            for keyword in keywords
        )

    @staticmethod
    def _game_category_id(meta: Meta) -> int:
        return 410 if meta.software else 412

    @classmethod
    def _non_video_category_id(cls, meta: Meta, category: str) -> int | None:
        if category == "MUSIC":
            return 408
        if category == "GAME":
            return cls._game_category_id(meta)
        return None

    @classmethod
    def _sports_category_id(cls, category: str, text: str) -> int | None:
        if category != "TV":
            return None
        if cls._category_keyword_match(text, ("sport", "sports")):
            return 407
        return None

    @staticmethod
    def _documentary_category_id(text: str) -> int | None:
        return 404 if "documentary" in text else None

    @staticmethod
    def _animation_category_id(meta: Meta, text: str) -> int | None:
        if meta.anime:
            return 405
        return 405 if "animation" in text else None

    @classmethod
    def _themed_category_id(
        cls, meta: Meta, category: str, genres: str, keywords: str
    ) -> int | None:
        combined = f"{genres}, {keywords}"
        sports = cls._sports_category_id(category, combined)
        if sports is not None:
            return sports
        documentary = cls._documentary_category_id(combined)
        if documentary is not None:
            return documentary
        return cls._animation_category_id(meta, combined)

    @classmethod
    def _tv_category_id(cls, genres: str) -> int:
        show_keywords = (
            "award show",
            "competition",
            "game show",
            "music show",
            "performance",
            "reality television",
            "reality tv",
            "reality",
            "stand-up",
            "talk show",
            "tv show",
            "variety",
        )
        return (
            403 if cls._category_keyword_match(genres, show_keywords) else 402
        )

    def get_category(self, meta: Meta) -> int:
        category = str(meta.category or "").upper()
        non_video = self._non_video_category_id(meta, category)
        if non_video is not None:
            return non_video
        genres = self._category_text(meta.genres)
        keywords = self._category_text(meta.keywords)
        themed = self._themed_category_id(meta, category, genres, keywords)
        if themed is not None:
            return themed
        if category == "TV":
            return self._tv_category_id(genres)
        return 401

    @staticmethod
    def _bdmv_type_id(meta: Meta) -> int:
        return 2 if str(meta.resolution or "").lower() == "2160p" else 1

    @classmethod
    def _disc_type_id(cls, meta: Meta) -> int | None:
        is_disc = str(meta.is_disc or "").lower()
        if is_disc == "bdmv":
            return cls._bdmv_type_id(meta)
        return 6 if "dvd" in is_disc else None

    @staticmethod
    def _file_type_id(meta: Meta) -> int:
        mapping = {
            "remux": 3,
            "webdl": 4,
            "webrip": 4,
            "hdtv": 5,
            "encode": 7,
        }
        return mapping.get(str(meta.type).lower(), 7)

    def get_type(self, meta: Meta) -> int:
        if str(meta.category or "").upper() == "MUSIC":
            return 8
        disc_type = self._disc_type_id(meta)
        return disc_type if disc_type is not None else self._file_type_id(meta)

    @staticmethod
    def _video_codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (2, ("h265", "x265", "hevc")),
            (1, ("h264", "x264", "avc")),
            (4, ("mpeg2", "mpeg-2")),
            (3, ("vc1", "vc-1")),
            (5, ("xvid",)),
        )

    def get_codec(self, meta: Meta) -> int:
        codec = str(meta.video_codec or "").lower()
        for codec_id, tokens in self._video_codec_rules():
            if any(token in codec for token in tokens):
                return codec_id
        return 6

    @staticmethod
    def _resolution_value(meta: Meta) -> str:
        value = meta.resolution
        return str(value).lower() if value else ""

    def get_resolution(self, meta: Meta) -> int:
        resolution = self._resolution_value(meta)
        if resolution in {"1080p", "1080i"}:
            return 2
        if resolution == "720p":
            return 3
        if meta.sd:
            return 4
        return 1 if resolution == "2160p" else 5

    @classmethod
    def _music_audio_codec_values(cls, meta: Meta) -> list[str]:
        if not isinstance(meta.music_release, dict):
            return []
        tracks = cls._music_tracks(cls._music_dict(meta.music_release))
        if not tracks:
            return []
        first = tracks[0]
        return [str(first.get("format") or ""), str(first.get("codec") or "")]

    @classmethod
    def _audio_codec_text(cls, meta: Meta) -> str:
        values = [str(meta.audio or ""), str(meta.format or "")]
        values.extend(cls._music_audio_codec_values(meta))
        return " ".join(values).lower()

    @staticmethod
    def _audio_codec_rules() -> tuple[tuple[int, tuple[str, ...]], ...]:
        return (
            (1, ("true", "atmos")),
            (2, ("dts",)),
            (3, ("dd",)),
            (4, ("lpcm",)),
            (5, ("flac",)),
            (6, ("mp3",)),
            (7, ("aac",)),
            (8, ("ape",)),
            (10, ("wav",)),
        )

    def get_audio_codec(self, meta: Meta) -> int:
        audio_codec = self._audio_codec_text(meta)
        for codec_id, tokens in self._audio_codec_rules():
            if any(token in audio_codec for token in tokens):
                return codec_id
        return 9

    @staticmethod
    def _has_chinese_language(values: Any) -> bool:
        languages = set(values or [])
        return bool({"Chinese", "Mandarin"}.intersection(languages))

    @classmethod
    def _checkbox_options(cls, meta: Meta) -> tuple[tuple[bool, int], ...]:
        return (
            (bool(meta.exclusive), 1),
            (cls._has_chinese_language(meta.audio_languages), 5),
            (cls._has_chinese_language(meta.subtitle_languages), 6),
            ("HDR" in str(meta.hdr or "").upper(), 7),
        )

    def get_checkboxes(self, meta: Meta) -> list[str]:
        return [
            str(value)
            for enabled, value in self._checkbox_options(meta)
            if enabled
        ]

    def get_anonymous(self, meta: Meta) -> bool:
        return not (
            meta.anon == 0
            and not self.config["TRACKERS"][self.tracker].get("anon", False)
        )
