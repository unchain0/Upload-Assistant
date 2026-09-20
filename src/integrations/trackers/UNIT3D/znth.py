import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from src.domain_models.book_language import (
    extract_first_author as _primary_name,
)
from src.domain_models.book_language import resolve_book_language
from src.domain_models.release import Meta
from src.domain_models.release_identity import ReleaseYearIdentity
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D, ParamsList

Config = dict[str, Any]


def prepare_zenith_music_layout(meta: Meta) -> None:
    if not _uses_zenith_music_layout(meta):
        return
    meta.keep_folder = True
    meta.rehash = True
    meta.reuse_torrent_path = None
    meta.base_reuse_torrent_path = None


def _uses_zenith_music_layout(meta: Meta) -> bool:
    if str(meta.category or "").upper() != "MUSIC":
        return False
    return "ZENITH" in _normalized_tracker_names(meta.trackers)


def _normalized_tracker_names(value: Any) -> set[str]:
    values = _tracker_name_values(value)
    return {text.upper() for item in values if (text := str(item).strip())}


def _tracker_name_values(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return cast(list[Any], value)
    if isinstance(value, tuple):
        return list(cast(tuple[Any, ...], value))
    if isinstance(value, set):
        return list(cast(set[Any], value))
    return []


def _iso_639_2_code(iso3: str) -> str:
    """Uppercase 3-letter language code (e.g. 'ENG') from a normalized ISO 639-2 code, or ''."""
    code = (iso3 or "").strip().upper()
    return code if len(code) == 3 else ""


def _is_misc(meta: Meta) -> bool:
    """True for comic/manga/magazine/newspaper (Zenith Misc, not ebook/audiobook)."""
    return meta.comic or meta.manga or meta.magazine or meta.newspaper


def _book_format(meta: Meta) -> str:
    """Uppercased format token, e.g. 'EPUB', 'M4B'."""
    return (meta.type or meta.container or "").strip().upper().lstrip(".")


class Zenith(UNIT3D):
    """
    Zenith is an Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "ZENITH"
    display_name = "Zenith"
    allows_bloated_audio = True
    book_cover_max_size = 3 * 1024 * 1024
    base_url = "https://znth.cx"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    banned_url = f"{base_url}/api/bannedReleaseGroups"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://znth.cx",)
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".rar",
            ".r00",
            ".r01",
            ".r02",
            ".r03",
            ".r04",
            ".r05",
            ".r06",
            ".r07",
            ".r08",
            ".r09",
            ".zip",
            ".7z",
        }
    )
    _KNOWN_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
        {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".m4v",
            ".mpg",
            ".mpeg",
            ".m2ts",
            ".ts",
            ".wmv",
            ".flv",
        }
    )
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset(
        {".mkv", ".mp4", ".ts", ".ps", ".mpg"}
    )
    _VIDEO_RESOLUTIONS: tuple[str, ...] = (
        "480i",
        "480p",
        "576i",
        "576p",
        "720p",
        "1080i",
        "1080p",
        "2160p",
        "4320p",
        "360p",
    )
    _AUDIO_TRACK_PATTERN: re.Pattern[str] = re.compile(
        r"^(?:\d{1,3}(?:-\d{1,2})?\.\s+.+|\d{1,3}(?:-\d{1,2})?\s+-\s+.+|\d{1,3}(?:-\d{1,2})?-(?!-).+|.+-\d{1,3}(?:-\d{1,2})?-(?!-).+)$"
    )
    _VIDEO_SOURCE_HINTS: tuple[str, ...] = (
        "WEB-DL",
        "WEBRIP",
        "HDTV",
        "UHDTV",
        "BluRay",
        "Blu-ray",
        "UHD Blu-ray",
        "WEB",
        "AMZN",
        "NF",
        "ATVP",
        "HMAX",
    )
    _VIDEO_CODEC_HINTS: tuple[str, ...] = (
        "H.264",
        "H.265",
        "XviD",
        "x264",
        "x265",
        "AV1",
        "VC-1",
        "MPEG-2",
        "MPEG2",
        "VP9",
        "HEVC",
    )
    _VIDEO_AUDIO_CODEC_HINTS: tuple[str, ...] = (
        "DD",
        "DD+",
        "AAC",
        "AC3",
        "DTS",
        "DTS-HD",
        "TrueHD",
        "FLAC",
        "OPUS",
        "ALAC",
    )
    _TV_ENDED_STATUSES: frozenset[str] = frozenset(
        {"ended", "canceled", "cancelled", "finished", "completed"}
    )
    _TV_ONGOING_STATUSES: frozenset[str] = frozenset(
        {"returning", "continuing", "in production", "upcoming", "ongoing"}
    )
    _VIDEO_CHANNEL_HINTS: tuple[str, ...] = (
        "1.0",
        "2.0",
        "3.0",
        "4.0",
        "5.1",
        "6.1",
        "7.1",
        "9.1",
    )
    _BANNED_BOOK_WORKS: tuple[str, ...] = (
        "FOUR AGAINST DARKNESS EXPANDED EDITION",
    )

    _banned_authors_raw = (
        "J.R.R. Tolkien",
        "Anne Perry",
        "Simon Scarrow",
        "Sara Gruen",
        "Joan Elliott",
        "Alan Dart",
        "Chris Mead",
        "Paul Moore & Gavin Jones",
        "Noah K Sturdevant",
        "Benedict Brown",
        "Erika T Wurth",
        "Randolph Lalonde",
        "Andrea Sfiligoi",
        "Ana-Maria Babanica",
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="ZENITH")
        self.config = config
        self.common = Common(config)
        self.banned_author_sets = self._build_banned_author_sets(
            self._banned_authors_raw
        )

    @classmethod
    def _build_banned_author_sets(
        cls, authors: tuple[str, ...]
    ) -> list[set[str]]:
        normalized: list[set[str]] = []
        for author in authors:
            for part in cls._split_banned_author_parts(author):
                normalized.extend(cls._normalized_author_variants(part))
        return normalized

    @staticmethod
    def _split_banned_author_parts(author: str) -> list[str]:
        return [
            part
            for part in re.split(
                r"\s*(?:\&|\band\b)\s*", author, flags=re.IGNORECASE
            )
            if part.strip()
        ]

    @classmethod
    def _normalized_author_variants(cls, author: str) -> list[set[str]]:
        variants = [cls._normalize_author(author)]
        variants.extend(cls._without_middle_initial_variants(author))
        return [variant for variant in variants if variant]

    @classmethod
    def _without_middle_initial_variants(cls, author: str) -> list[set[str]]:
        words = author.split()
        if len(words) <= 2:
            return []
        return [
            cls._normalize_author(" ".join(words[:index] + words[index + 1 :]))
            for index, word in enumerate(words[1:-1], start=1)
            if len(word.strip(".")) == 1
        ]

    @classmethod
    def _normalize_author(cls, name: str) -> set[str]:
        if not name:
            return set()
        words = cls._clean_author_words(name)
        return set(cls._merge_initial_words(words))

    @staticmethod
    def _clean_author_words(name: str) -> list[str]:
        nfkd_form = unicodedata.normalize("NFKD", name)
        cleaned = "".join(
            char for char in nfkd_form if not unicodedata.combining(char)
        )
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", cleaned).lower()
        conjunctions = {"and", "e", "y", "with", "und", "et"}
        return [word for word in cleaned.split() if word not in conjunctions]

    @classmethod
    def _merge_initial_words(cls, words: list[str]) -> list[str]:
        merged: list[str] = []
        initials: list[str] = []
        for word in words:
            if cls._is_initial_word(word):
                initials.append(word)
                continue
            cls._flush_initials(merged, initials)
            merged.append(word)
        cls._flush_initials(merged, initials)
        return merged

    @staticmethod
    def _is_initial_word(word: str) -> bool:
        return len(word) == 1 and word.isalpha()

    @staticmethod
    def _flush_initials(merged: list[str], initials: list[str]) -> None:
        if not initials:
            return
        merged.append("".join(initials))
        initials.clear()

    @classmethod
    def _split_authors(cls, author_str: str) -> list[str]:
        if not author_str:
            return []
        major_pattern = (
            r"\s*(?:;|&|/|\+|\band\b|\be\b|\by\b|\bwith\b|\s+-\s+)\s*"
        )
        candidates = re.split(major_pattern, author_str, flags=re.IGNORECASE)
        authors: list[str] = []
        for candidate in candidates:
            authors.extend(cls._split_author_candidate(candidate))
        return authors

    @classmethod
    def _split_author_candidate(cls, candidate: str) -> list[str]:
        value = candidate.strip()
        if not value:
            return []
        if "," not in value:
            return [value]
        return cls._split_comma_author(value)

    @classmethod
    def _split_comma_author(cls, value: str) -> list[str]:
        parts = cls._nonempty_comma_parts(value)
        if len(parts) != 2:
            return parts
        return [value] if cls._looks_like_last_first(parts[1]) else parts

    @staticmethod
    def _nonempty_comma_parts(value: str) -> list[str]:
        return [part for raw in value.split(",") if (part := raw.strip())]

    @staticmethod
    def _looks_like_last_first(second_part: str) -> bool:
        words = second_part.split()
        if len(words) == 1:
            return True
        return all(len(word.strip(".")) <= 3 for word in words)

    def _is_banned_author(self, meta_author: str) -> bool:
        if not meta_author:
            return False
        return any(
            self._author_part_is_banned(part)
            for part in self._split_authors(meta_author)
        )

    def _author_part_is_banned(self, part: str) -> bool:
        normalized = self._normalize_author(part)
        if not normalized:
            return False
        return any(
            banned.issubset(normalized) for banned in self.banned_author_sets
        )

    @staticmethod
    def _is_path_like_file(filename: Any) -> bool:
        return bool(str(filename).strip())

    @classmethod
    def _collect_video_paths(cls, filelist: list[Any]) -> list[Path]:
        return [
            Path(str(item))
            for item in filelist
            if cls._is_path_like_file(item)
            and Path(str(item)).suffix.lower() in cls._KNOWN_VIDEO_EXTENSIONS
        ]

    @classmethod
    def _renamed_tagged_video_file(
        cls, video_paths: list[Path], tag: str | None
    ) -> str:
        group = cls._normalized_group(tag)
        if not group:
            return ""
        return next(
            (
                path.name
                for path in video_paths
                if cls._looks_renamed_for_group(path, group)
            ),
            "",
        )

    @staticmethod
    def _normalized_group(tag: str | None) -> str:
        return str(tag or "").lstrip("-").strip().casefold()

    @staticmethod
    def _looks_renamed_for_group(path: Path, group: str) -> bool:
        return any(
            char.isspace() for char in path.stem
        ) and path.stem.casefold().endswith(f"-{group}")

    @staticmethod
    def _contains_archive_file(filelist: list[Any]) -> str:
        for item in filelist:
            path = Path(str(item))
            if path.suffix.lower() in Zenith._ARCHIVE_EXTENSIONS:
                return path.name
        return ""

    @staticmethod
    def _disc_is_supported(meta: Meta) -> bool:
        disctype = (
            str(meta.is_disc or "")
            .upper()
            .strip()
            .replace(" ", "")
            .replace("-", "")
        )
        if not disctype:
            return True
        return disctype in {"BDMV", "3DBDMV", "VIDEO_TS"}

    @classmethod
    def _video_extensions_for_type(cls, meta: Meta) -> set[str]:
        media_type = str(meta.type or "").upper().strip()
        if media_type in {"HDTV", "UHDTV"}:
            return cls._broadcast_video_extensions(meta.video_encode)
        mappings = {
            "SDTV": {".ps", ".mpg"},
            "DVDRIP": {".avi", ".mkv", ".mp4"},
        }
        return mappings.get(media_type, {".mkv", ".mp4"})

    @staticmethod
    def _broadcast_video_extensions(video_encode: Any) -> set[str]:
        encode = str(video_encode or "").casefold().replace(".", "")
        return (
            {".mkv", ".mp4", ".avi"}
            if encode in {"x264", "x265", "xvid"}
            else {".ts"}
        )

    @staticmethod
    def _contains_disallowed_video_file(
        filelist: list[Any], allowed: set[str]
    ) -> str:
        for item in filelist:
            path = Path(str(item))
            suffix = path.suffix.lower()
            if suffix not in Zenith._KNOWN_VIDEO_EXTENSIONS:
                continue
            if suffix not in allowed:
                return path.name
        return ""

    @classmethod
    def _has_video_resolution(cls, title: str) -> bool:
        normalized = str(title).replace(".", " ").upper().replace("HD", " HD ")
        return any(
            f" {res.upper()} " in f" {normalized} "
            for res in cls._VIDEO_RESOLUTIONS
        )

    @classmethod
    def _has_tv_pattern(cls, title: str) -> bool:
        return bool(
            re.search(
                r"\bS\d{1,2}(?:E\d{1,3}(?:E\d{1,3})?|\s)?\b",
                str(title or ""),
                re.IGNORECASE,
            )
        )

    @classmethod
    def _contains_source_or_type_token(cls, title: str) -> bool:
        haystack = str(title or "").upper()
        tokens = (
            cls._VIDEO_SOURCE_HINTS
            + cls._VIDEO_CODEC_HINTS
            + cls._VIDEO_AUDIO_CODEC_HINTS
            + cls._VIDEO_CHANNEL_HINTS
        )
        return any(
            re.search(
                rf"(?<![A-Z0-9]){re.escape(token.upper())}(?![A-Z0-9])",
                haystack,
            )
            for token in tokens
        )

    @staticmethod
    def _has_valid_music_path_format(file_path: Path) -> bool:
        return len(str(file_path)) <= 180 and not any(
            part.startswith(" ")
            for part in str(file_path).replace("\\", "/").split("/")
        )

    @staticmethod
    def _normalized_music_component(value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return " ".join(
            "".join(
                char if char.isalnum() else " " for char in normalized
            ).split()
        )

    @classmethod
    def _music_release_error(cls, meta: Meta, filelist: list[Any]) -> str:
        release = cls._music_release(meta)
        root = Path(str(meta.path or ""))
        checks: tuple[Callable[[Path], str], ...] = (
            cls._music_root_error,
            lambda current: cls._music_artist_root_error(
                release, meta, current
            ),
            lambda current: cls._music_album_root_error(
                release, meta, current
            ),
        )
        for check in checks:
            if error := check(root):
                return error
        tracks = cls._music_tracks(release)
        if error := cls._music_track_count_error(tracks, filelist):
            return error
        return cls._music_track_metadata_error(tracks)

    @staticmethod
    def _music_release(meta: Meta) -> dict[str, Any]:
        return (
            cast(dict[str, Any], meta.music_release)
            if isinstance(meta.music_release, dict)
            else {}
        )

    @staticmethod
    def _music_root_error(root: Path) -> str:
        if not root.name:
            return "music uploads must be inside a directory"
        audio_suffixes = {
            ".flac",
            ".mp3",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".wav",
            ".alac",
        }
        return (
            "music uploads must be inside a directory"
            if root.suffix.lower() in audio_suffixes
            else ""
        )

    @classmethod
    def _music_artist_root_error(
        cls, release: dict[str, Any], meta: Meta, root: Path
    ) -> str:
        normalized_root = cls._normalized_music_component(root.name)
        artists = cls._music_artist_values(release, meta)
        normalized = {
            value
            for item in artists
            if (value := cls._normalized_music_component(item))
        }
        if cls._artist_root_matches(normalized_root, normalized):
            return ""
        return "music directory must contain the artist name"

    @classmethod
    def _music_artist_values(
        cls, release: dict[str, Any], meta: Meta
    ) -> list[Any]:
        values: list[Any] = [cls._music_field(release, "artist", meta.artist)]
        artists = cls._music_field(release, "artists", [])
        if isinstance(artists, list):
            values.extend(artists)
        conflicts = release.get("conflicts")
        if isinstance(conflicts, dict):
            conflict_map = cast(dict[str, Any], conflicts)
            artists = conflict_map.get("artist")
            if isinstance(artists, list):
                values.extend(cast(list[Any], artists))
        return values

    @staticmethod
    def _artist_root_matches(
        normalized_root: str, normalized_artists: set[str]
    ) -> bool:
        if not normalized_artists:
            return False
        if any(value in normalized_root for value in normalized_artists):
            return True
        return (
            "various artists" in normalized_artists
            and "va" in normalized_root.split()
        )

    @classmethod
    def _music_album_root_error(
        cls, release: dict[str, Any], meta: Meta, root: Path
    ) -> str:
        album = cls._normalized_music_component(
            cls._music_field(release, "album", meta.title)
        )
        normalized_root = cls._normalized_music_component(root.name)
        return (
            ""
            if album and album in normalized_root
            else "music directory must contain the album name"
        )

    @staticmethod
    def _music_tracks(release: dict[str, Any]) -> list[Any]:
        tracks = release.get("tracks")
        return cast(list[Any], tracks) if isinstance(tracks, list) else []

    @classmethod
    def _music_track_count_error(
        cls, tracks: list[Any], filelist: list[Any]
    ) -> str:
        if not tracks:
            return "music metadata does not contain any audio tracks"
        audio_file_count = cls._music_audio_file_count(filelist)
        if len(tracks) == audio_file_count:
            return ""
        return f"music metadata contains {len(tracks)} tracks for {audio_file_count} audio files"

    @staticmethod
    def _music_audio_file_count(filelist: list[Any]) -> int:
        audio_suffixes = {
            ".flac",
            ".mp3",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".wav",
            ".alac",
            ".pcm",
        }
        return sum(
            Path(str(item)).suffix.lower() in audio_suffixes
            for item in filelist
        )

    @classmethod
    def _music_track_metadata_error(cls, tracks: list[Any]) -> str:
        for index, raw_track in enumerate(tracks, start=1):
            if error := cls._single_music_track_metadata_error(
                raw_track, index
            ):
                return error
        return ""

    @classmethod
    def _single_music_track_metadata_error(
        cls, raw_track: Any, index: int
    ) -> str:
        if not isinstance(raw_track, dict):
            return f"music track {index} metadata is invalid"
        track = cast(dict[str, Any], raw_track)
        missing = cls._missing_music_track_tags(track)
        if not missing:
            return ""
        filename = cls._music_track_filename(track, index)
        return f"{filename} is missing required tags: {', '.join(missing)}"

    @staticmethod
    def _missing_music_track_tags(track: dict[str, Any]) -> list[str]:
        values = (
            ("Artist", track.get("artist")),
            ("Album", track.get("album")),
            ("Title", track.get("title")),
            ("Track Number", track.get("track_number")),
        )
        return [label for label, value in values if value in (None, "", 0)]

    @staticmethod
    def _music_track_filename(track: dict[str, Any], index: int) -> str:
        value = (
            track.get("relative_path") or track.get("path") or f"track {index}"
        )
        return Path(str(value)).name

    @classmethod
    def _validate_music_track_naming(
        cls,
        filelist: list[Any],
        release_root: str | Path | None = None,
        *,
        enforce_filenames: bool = True,
    ) -> str:
        audio_paths = cls._music_audio_paths(filelist)
        root = cls._music_release_root(release_root)
        for source_path in audio_paths:
            path = cls._relative_music_path(source_path, root)
            torrent_path = cls._torrent_music_path(path, root)
            if not cls._has_valid_music_path_format(torrent_path):
                return path.name
            if enforce_filenames and cls._invalid_music_filename(
                path, len(audio_paths)
            ):
                return path.name
        return ""

    @staticmethod
    def _music_audio_paths(filelist: list[Any]) -> list[Path]:
        audio_suffixes = {
            ".flac",
            ".mp3",
            ".m4a",
            ".aac",
            ".ogg",
            ".opus",
            ".wav",
            ".alac",
            ".pcm",
            ".m4b",
        }
        return [
            path
            for item in filelist
            if (path := Path(str(item))).suffix.lower() in audio_suffixes
        ]

    @staticmethod
    def _music_release_root(release_root: str | Path | None) -> Path | None:
        if not release_root:
            return None
        root = Path(release_root)
        return root.parent if root.is_file() else root

    @staticmethod
    def _relative_music_path(source_path: Path, root: Path | None) -> Path:
        if root is None or not source_path.is_absolute():
            return source_path
        try:
            return source_path.relative_to(root)
        except ValueError:
            return Path(source_path.name)

    @staticmethod
    def _torrent_music_path(path: Path, root: Path | None) -> Path:
        if root is None:
            return path
        if path.parts and path.parts[0] == root.name:
            return path
        return Path(root.name) / path

    @classmethod
    def _invalid_music_filename(cls, path: Path, audio_count: int) -> bool:
        stem = path.stem
        if cls._AUDIO_TRACK_PATTERN.match(stem):
            return False
        if cls._valid_single_track_name(stem, audio_count):
            return False
        if cls._invalid_track_stem(stem):
            return True
        return audio_count > 1

    @staticmethod
    def _valid_single_track_name(stem: str, audio_count: int) -> bool:
        return (
            audio_count == 1
            and not stem.startswith(".")
            and any(char.isalnum() for char in stem)
        )

    @staticmethod
    def _invalid_track_stem(stem: str) -> bool:
        if " " not in stem or stem.startswith(".") or "." in stem:
            return True
        return not any(char.isalnum() for char in stem)

    @staticmethod
    def _is_valid_language3(meta: Meta) -> bool:
        code = _iso_639_2_code(meta.book_language_iso)
        return bool(code and code.isalpha() and len(code) == 3)

    @classmethod
    def _is_banned_book_work(cls, meta: Meta) -> bool:
        values = cls._book_work_values(meta)
        return any(cls._contains_banned_work(value) for value in values)

    @staticmethod
    def _book_work_values(meta: Meta) -> tuple[str, str]:
        return (
            (meta.title or meta.name or "").upper(),
            (meta.author or "").upper(),
        )

    @classmethod
    def _contains_banned_work(cls, value: str) -> bool:
        return any(work in value for work in cls._BANNED_BOOK_WORKS)

    @classmethod
    def _audiobook_layout_error(
        cls, meta: Meta, filelist: list[Any], expected_name: str
    ) -> str:
        root = Path(str(meta.path or ""))
        if root.name != expected_name:
            return f"audiobook directory must be named '{expected_name}'"
        audio_paths = cls._audiobook_audio_paths(filelist)
        if not audio_paths:
            return "audiobook does not contain a supported audio file"
        if len(audio_paths) == 1:
            return cls._single_audiobook_layout_error(
                audio_paths[0], expected_name
            )
        return cls._multi_audiobook_layout_error(meta, audio_paths)

    @staticmethod
    def _audiobook_audio_paths(filelist: list[Any]) -> list[Path]:
        audio_extensions = {".m4b", ".mp3", ".flac"}
        return [
            path
            for item in filelist
            if (path := Path(str(item))).suffix.lower() in audio_extensions
        ]

    @staticmethod
    def _single_audiobook_layout_error(path: Path, expected_name: str) -> str:
        if path.stem == expected_name:
            return ""
        return f"single audiobook file must be named '{expected_name}{path.suffix.lower()}'"

    @classmethod
    def _multi_audiobook_layout_error(
        cls, meta: Meta, audio_paths: list[Path]
    ) -> str:
        if cls._contains_m4b(audio_paths):
            return "M4B audiobooks must contain one file unless the retail source is split into Disc folders"
        invalid = cls._invalid_audiobook_track_name(meta, audio_paths)
        return (
            f"multi-file audiobook track has invalid name: {invalid}"
            if invalid
            else ""
        )

    @staticmethod
    def _contains_m4b(audio_paths: list[Path]) -> bool:
        return any(path.suffix.lower() == ".m4b" for path in audio_paths)

    @staticmethod
    def _invalid_audiobook_track_name(
        meta: Meta, audio_paths: list[Path]
    ) -> str:
        title_year = f"{meta.title} ({meta.year})"
        pattern = re.compile(
            rf"^\d+\.\s+.+\s+-\s+{re.escape(title_year)}$", re.IGNORECASE
        )
        return next(
            (
                path.name
                for path in audio_paths
                if not pattern.fullmatch(path.stem)
            ),
            "",
        )

    @classmethod
    def _audiobook_language_error(cls, meta: Meta) -> str:
        audio_tracks = cls._audiobook_audio_tracks(meta)
        if not audio_tracks:
            return "MediaInfo does not contain an audio track"
        expected = _iso_639_2_code(meta.book_language_iso)
        return next(
            (
                error
                for track in audio_tracks
                if (
                    error := cls._audiobook_track_language_error(
                        track, expected
                    )
                )
            ),
            "",
        )

    @classmethod
    def _audiobook_audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        tracks = cls._mediainfo_track_values(meta)
        return [track for track in tracks if track.get("@type") == "Audio"]

    @classmethod
    def _mediainfo_track_values(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._mediainfo_media(meta)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _mediainfo_media(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        tracks = cast(list[Any], value)
        return [
            cast(dict[str, Any], track)
            for track in tracks
            if isinstance(track, dict)
        ]

    @staticmethod
    def _audiobook_track_language_error(
        track: dict[str, Any], expected: str
    ) -> str:
        raw_language = str(
            track.get("Language") or track.get("language") or ""
        ).strip()
        _language, actual = resolve_book_language(raw_language)
        if not actual:
            return "audio track is missing the required language metadata"
        if actual.upper() != expected:
            return f"audio track language is {actual.upper()}, but the audiobook metadata is {expected}"
        return ""

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = self._category_name(meta)
        filelist = self._validated_filelist(meta)
        if filelist is None or not self._base_upload_policy(meta):
            return False
        if not await self._category_upload_policy(meta, category, filelist):
            return False
        if not self._video_title_policy(meta, category):
            return False
        return await self.common.check_and_confirm_adult_media_upload(
            meta, self.tracker
        )

    @staticmethod
    def _category_name(meta: Meta) -> str:
        return str(meta.category or "").upper()

    def _base_upload_policy(self, meta: Meta) -> bool:
        if not meta.software:
            return True
        logger.info(
            f"{self.tracker}: [yellow]Software uploads are not mapped to a dedicated tracker category. Skipping upload.[/yellow]"
        )
        return False

    def _validated_filelist(self, meta: Meta) -> list[Any] | None:
        raw = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw, (list, tuple, set)):
            logger.info(
                f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]"
            )
            return None
        return [item for item in raw if self._is_path_like_file(item)]

    async def _category_upload_policy(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        if category in {"MOVIE", "TV"}:
            return await self._video_upload_policy(meta, category, filelist)
        if category == "BOOK" and not _is_misc(meta):
            return await self._book_upload_policy(meta, filelist)
        if category == "MUSIC":
            return self._music_upload_policy(meta, filelist)
        return True

    async def _video_upload_policy(
        self, meta: Meta, category: str, filelist: list[Any]
    ) -> bool:
        video_paths = self._collect_video_paths(filelist)
        if not self._video_file_policy(meta, filelist, video_paths):
            return False
        if category != "TV":
            return True
        return await self._tv_scope_policy(meta, filelist)

    def _video_file_policy(
        self, meta: Meta, filelist: list[Any], video_paths: list[Path]
    ) -> bool:
        if not self._renamed_video_policy(meta, video_paths):
            return False
        if not self._screenshot_policy(meta):
            return False
        return self._video_storage_policy(meta, filelist, video_paths)

    def _renamed_video_policy(
        self, meta: Meta, video_paths: list[Path]
    ) -> bool:
        if meta.is_disc:
            return True
        renamed = self._renamed_tagged_video_file(video_paths, meta.tag)
        if not renamed:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Tagged release file appears to have been renamed with spaces: {renamed}. Restore the original filename before uploading.[/bold red]"
        )
        return False

    def _screenshot_policy(self, meta: Meta) -> bool:
        if self._screen_count(meta.screens) >= 3:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Video uploads require at least 3 screenshots on {self.tracker}.[/bold red]"
        )
        return False

    @staticmethod
    def _screen_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    def _video_storage_policy(
        self, meta: Meta, filelist: list[Any], video_paths: list[Path]
    ) -> bool:
        if not self._disc_structure_policy(meta):
            return False
        if not self._video_container_policy(meta, video_paths):
            return False
        return self._archive_policy(filelist)

    def _disc_structure_policy(self, meta: Meta) -> bool:
        if not meta.is_disc or self._disc_is_supported(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Full-disc uploads on {self.tracker} must use BDMV or VIDEO_TS structures.[/bold red]"
        )
        return False

    def _video_container_policy(
        self, meta: Meta, video_paths: list[Path]
    ) -> bool:
        if meta.is_disc:
            return True
        disallowed = self._contains_disallowed_video_file(
            video_paths, self._video_extensions_for_type(meta)
        )
        if not disallowed:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Video container '{Path(disallowed).suffix}' is not allowed for this release type on {self.tracker}.[/bold red]"
        )
        return False

    def _archive_policy(self, filelist: list[Any]) -> bool:
        archive = self._contains_archive_file(filelist)
        if not archive:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Archive or multipart files are not allowed. Found: {archive}[/bold red]"
        )
        return False

    async def _tv_scope_policy(self, meta: Meta, filelist: list[Any]) -> bool:
        if len(self.common.extract_tv_seasons(filelist)) > 1:
            logger.info(
                f"{self.tracker}: [bold red]TV uploads must target a single season on {self.tracker}.[/bold red]"
            )
            return False
        if meta.tv_pack:
            return await self._tv_pack_policy(meta)
        return self._tv_episode_policy(meta, filelist)

    async def _tv_pack_policy(self, meta: Meta) -> bool:
        ended = self.common.is_tv_series_ended(
            meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES
        )
        if ended is False:
            logger.info(
                f"{self.tracker}: [bold red]TV season packs are restricted to ended series on {self.tracker}.[/bold red]"
            )
            return False
        if ended is True:
            return True
        return await self._unknown_tv_pack_policy(meta)

    async def _unknown_tv_pack_policy(self, meta: Meta) -> bool:
        logger.info(
            f"{self.tracker}: [yellow]Unable to confirm whether this TV series has ended.[/yellow]"
        )
        if meta.unattended:
            return bool(meta.unattended_confirm)
        return await self.common.prompt_user_for_confirmation(
            "Do you want to upload this TV pack anyway?", meta
        )

    def _tv_episode_policy(self, meta: Meta, filelist: list[Any]) -> bool:
        if self.common.count_tv_episodes(filelist) > 1:
            logger.info(
                f"{self.tracker}: [bold red]Non-pack TV uploads should contain a single episode on {self.tracker}.[/bold red]"
            )
            return False
        if (
            self.common.is_tv_series_ended(
                meta, self._TV_ENDED_STATUSES, self._TV_ONGOING_STATUSES
            )
            is True
        ):
            logger.info(
                f"{self.tracker}: [bold red]Completed TV seasons must be uploaded as season packs on {self.tracker}.[/bold red]"
            )
            return False
        return True

    async def _book_upload_policy(
        self, meta: Meta, filelist: list[Any]
    ) -> bool:
        defer = self._defer_zentag_validation(meta)
        if not self._book_folder_policy(meta, filelist, defer):
            return False
        if not await self._book_format_policy(meta, filelist, defer):
            return False
        return self._book_identity_policy(meta)

    @staticmethod
    def _defer_zentag_validation(meta: Meta) -> bool:
        return bool(meta.get("defer_zentag_validation", False)) and not bool(
            meta.get("zentag_prepared", False)
        )

    def _book_folder_policy(
        self, meta: Meta, filelist: list[Any], defer: bool
    ) -> bool:
        if defer:
            return True
        if not meta.isdir:
            logger.info(
                f"{self.tracker}: [bold red]Books and audiobooks must be uploaded inside a directory. Use zentag to create a compliant copy.[/bold red]"
            )
            return False
        if len(filelist) == 1 and not meta.keep_folder:
            logger.info(
                f"{self.tracker}: [bold red]Single-file book torrents must retain their directory. Re-run with --keep-folder after preparing the release with zentag.[/bold red]"
            )
            return False
        return True

    async def _book_format_policy(
        self, meta: Meta, filelist: list[Any], defer: bool
    ) -> bool:
        if not meta.isbn and not meta.asin:
            logger.info(
                f"{self.tracker}: [bold red]ISBN or ASIN is required for ebooks and audiobooks. Skipping upload...[/bold red]"
            )
            return False
        if meta.audiobook:
            return await self._audiobook_policy(meta, filelist, defer)
        return self._ebook_format_policy(meta)

    async def _audiobook_policy(
        self, meta: Meta, filelist: list[Any], defer: bool
    ) -> bool:
        if not self._audiobook_metadata_policy(meta):
            return False
        if defer:
            return True
        expected_name = (await self.get_name(meta))["name"]
        return self._audiobook_prepared_policy(meta, filelist, expected_name)

    def _audiobook_metadata_policy(self, meta: Meta) -> bool:
        if not meta.narrator:
            logger.info(
                f"{self.tracker}: [bold red]Narrator is required for audiobooks. Skipping upload...[/bold red]"
            )
            return False
        if _book_format(meta) in {"MP3", "FLAC", "M4B"}:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Audiobooks must be MP3, FLAC, or M4B. Skipping upload...[/bold red]"
        )
        return False

    def _audiobook_prepared_policy(
        self, meta: Meta, filelist: list[Any], expected_name: str
    ) -> bool:
        layout_error = self._audiobook_layout_error(
            meta, filelist, expected_name
        )
        if layout_error:
            logger.info(
                f"{self.tracker}: [bold red]Invalid audiobook layout: {layout_error}. Use zentag transform before uploading.[/bold red]"
            )
            return False
        language_error = self._audiobook_language_error(meta)
        if language_error:
            logger.info(
                f"{self.tracker}: [bold red]Invalid audiobook metadata: {language_error}. Use zentag transform before uploading.[/bold red]"
            )
            return False
        return True

    def _ebook_format_policy(self, meta: Meta) -> bool:
        if _book_format(meta) in {"EPUB", "PDF", "MOBI", "AZW3", "DJVU"}:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Ebooks must be EPUB, PDF, MOBI, AZW3, or DJVU. Skipping upload...[/bold red]"
        )
        return False

    def _book_identity_policy(self, meta: Meta) -> bool:
        if not self._book_author_policy(meta):
            return False
        if not self._book_work_policy(meta):
            return False
        return self._book_language_policy(meta)

    def _book_author_policy(self, meta: Meta) -> bool:
        if not meta.author or not self._is_banned_author(meta.author):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Author '{meta.author}' is banned on {self.tracker}. Skipping upload...[/bold red]"
        )
        return False

    def _book_work_policy(self, meta: Meta) -> bool:
        if not self._is_banned_book_work(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Title/author '{meta.title or meta.name}' is blocked by banned works list on {self.tracker}. Skipping upload...[/bold red]"
        )
        return False

    def _book_language_policy(self, meta: Meta) -> bool:
        if self._is_valid_language3(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Books and audiobooks require a valid 3-letter language code. Skipping upload...[/bold red]"
        )
        return False

    def _music_upload_policy(self, meta: Meta, filelist: list[Any]) -> bool:
        release_error = self._music_release_error(meta, filelist)
        if release_error:
            logger.info(
                f"{self.tracker}: [bold red]{release_error}. Skipping upload...[/bold red]"
            )
            return False
        invalid_track = self._validate_music_track_naming(
            filelist, meta.path, enforce_filenames=meta.personalrelease
        )
        if not invalid_track:
            return True
        reason = (
            "filename structure" if meta.personalrelease else "path structure"
        )
        logger.info(
            f"{self.tracker}: [bold red]Invalid music {reason} for {invalid_track}. Skipping upload...[/bold red]"
        )
        return False

    def _video_title_policy(self, meta: Meta, category: str) -> bool:
        if category not in {"MOVIE", "TV"}:
            return True
        release_name = str(meta.name or "")
        if not self._video_resolution_title_policy(release_name):
            return False
        if not self._video_episode_title_policy(meta, category, release_name):
            return False
        return self._video_source_title_policy(release_name)

    def _video_resolution_title_policy(self, release_name: str) -> bool:
        if self._has_video_resolution(release_name):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Release title does not include a supported resolution on {self.tracker}. Skipping upload...[/bold red]"
        )
        return False

    def _video_episode_title_policy(
        self, meta: Meta, category: str, release_name: str
    ) -> bool:
        if category == "TV":
            return self._tv_title_pattern_policy(meta, release_name)
        if re.search(r"\bS\d{1,2}E\d{1,3}\b", release_name, re.IGNORECASE):
            logger.info(
                f"{self.tracker}: [bold red]Movie title appears to contain TV episode tokens on {self.tracker}. Skipping upload...[/bold red]"
            )
            return False
        return True

    def _tv_title_pattern_policy(self, meta: Meta, release_name: str) -> bool:
        if self._has_tv_pattern(release_name) or self._has_tv_pattern(
            meta.episode_title
        ):
            return True
        logger.info(
            f"{self.tracker}: [bold red]TV release title is missing Sxx(Eyy) season/episode token on {self.tracker}. Skipping upload...[/bold red]"
        )
        return False

    def _video_source_title_policy(self, release_name: str) -> bool:
        if self._contains_source_or_type_token(release_name):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Release title is missing source/type metadata required by {self.tracker} naming rules.[/bold red]"
        )
        return False

    async def get_search_urls(
        self, meta: Meta, request_params: ParamsList
    ) -> list[tuple[str, ParamsList, bool]]:
        urls = await super().get_search_urls(meta, request_params)
        if meta.category == "BOOK":
            if meta.isbn:
                urls.append(
                    (
                        self.search_url,
                        [("bookId", meta.isbn), ("perPage", "100")],
                        False,
                    )
                )
            if meta.asin:
                urls.append(
                    (
                        self.search_url,
                        [("bookId", meta.asin), ("perPage", "100")],
                        False,
                    )
                )
        return urls

    async def get_name(self, meta: Meta) -> dict[str, str]:
        category = str(meta.category or "")
        if category == "MUSIC":
            return {"name": self._music_name(meta)}
        if category == "BOOK":
            return {"name": self._book_release_name(meta)}
        if category in {"TV", "MOVIE"}:
            return {"name": self._video_release_name(meta)}
        return {"name": meta.name}

    def _book_release_name(self, meta: Meta) -> str:
        if _is_misc(meta):
            return meta.name
        identity = self._book_name_identity(meta)
        return (
            self._audiobook_release_name(meta, identity)
            if meta.audiobook
            else self._ebook_release_name(meta, identity)
        )

    @classmethod
    def _book_name_identity(cls, meta: Meta) -> tuple[str, str, str, str, str]:
        return (
            cls._book_author(meta),
            cls._book_title(meta),
            cls._book_year(meta),
            _book_format(meta),
            cls._book_tag(meta),
        )

    @staticmethod
    def _book_author(meta: Meta) -> str:
        return _primary_name(str(meta.author or ""))

    @staticmethod
    def _book_title(meta: Meta) -> str:
        value = meta.title if meta.title else meta.name
        return str(value or "").strip()

    @staticmethod
    def _book_year(meta: Meta) -> str:
        return "" if meta.year is None else str(meta.year)

    @staticmethod
    def _book_tag(meta: Meta) -> str:
        return str(meta.tag or "").strip()

    @classmethod
    def _audiobook_release_name(
        cls, meta: Meta, identity: tuple[str, str, str, str, str]
    ) -> str:
        author, title, year, format_value, tag = identity
        parts = cls._author_title_parts(author, title)
        parts.extend(cls._audiobook_detail_parts(meta, year, format_value))
        return f"{cls._compact_parts(parts)}{tag}"

    @classmethod
    def _audiobook_detail_parts(
        cls, meta: Meta, year: str, format_value: str
    ) -> list[str]:
        container, codec = cls._audiobook_codec_parts(format_value)
        values = (
            cls._parenthesized(year),
            _iso_639_2_code(meta.book_language_iso),
            cls._preferred_edition(meta),
            cls._braced_narrator(meta),
            cls._bracketed_source(cls._audiobook_source(meta)),
            container,
            codec,
            cls._audiobook_bitrate(meta),
        )
        return cls._nonempty_parts(values)

    @staticmethod
    def _parenthesized(value: str) -> str:
        return f"({value})" if value else ""

    @staticmethod
    def _preferred_edition(meta: Meta) -> str:
        value = meta.manual_edition if meta.manual_edition else meta.edition
        return str(value or "").strip()

    @staticmethod
    def _braced_narrator(meta: Meta) -> str:
        narrator = _primary_name(str(meta.narrator or ""))
        return f"{{{narrator}}}" if narrator else ""

    @staticmethod
    def _bracketed_source(source: str) -> str:
        return f"[{source}]" if source else ""

    @staticmethod
    def _audiobook_bitrate(meta: Meta) -> str:
        return (
            f"{meta.audiobook_bitrate}kbps" if meta.audiobook_bitrate else ""
        )

    @staticmethod
    def _audiobook_source(meta: Meta) -> str:
        return (
            (meta.manual_source or "").strip()
            or (meta.source or "").strip()
            or "WEB"
        ).upper()

    @staticmethod
    def _audiobook_codec_parts(format_value: str) -> tuple[str, str]:
        mapping = {
            "FLAC": ("", "FLAC"),
            "MP3": ("", "MP3"),
            "M4B": ("M4B", "AAC"),
        }
        return mapping.get(format_value, ("", format_value))

    @classmethod
    def _ebook_release_name(
        cls, meta: Meta, identity: tuple[str, str, str, str, str]
    ) -> str:
        author, title, year, format_value, tag = identity
        language = _iso_639_2_code(meta.book_language_iso)
        series = cls._ebook_series(meta)
        edition = cls._ebook_edition(meta)
        source = cls._ebook_source(meta, format_value)
        parts = cls._ebook_identity_parts(author, series, title)
        parts.extend(
            cls._nonempty_parts(
                (
                    year,
                    language,
                    edition,
                    format_value,
                    "Retail"
                    if source == "RETAIL"
                    or "retail" in meta.basename_no_ext.lower()
                    else "",
                )
            )
        )
        return f"{cls._compact_parts(parts)}{tag}"

    @staticmethod
    def _ebook_series(meta: Meta) -> str:
        series = (meta.book_series or "").strip()
        index = (meta.book_series_index or "").strip()
        if not series:
            return ""
        return f"{series} #{index}" if index else series

    @classmethod
    def _ebook_edition(cls, meta: Meta) -> str:
        edition = cls._preferred_edition(meta)
        if not edition:
            return ""
        lower = edition.lower()
        if cls._is_first_edition(lower):
            return ""
        return (
            edition if cls._has_edition_token(lower) else f"{edition} Edition"
        )

    @staticmethod
    def _is_first_edition(value: str) -> bool:
        return "1st" in value or "first" in value

    @staticmethod
    def _has_edition_token(value: str) -> bool:
        tokens = value.replace(".", " ").split()
        return any(token in {"edition", "ed"} for token in tokens)

    @classmethod
    def _ebook_source(cls, meta: Meta, format_value: str) -> str:
        declared = cls._declared_ebook_source(meta)
        if declared:
            return declared
        inferred = cls._filename_ebook_source(meta)
        return (
            inferred if inferred else cls._default_ebook_source(format_value)
        )

    @staticmethod
    def _declared_ebook_source(meta: Meta) -> str:
        for value in (meta.manual_source, meta.source):
            source = str(value or "").strip().upper()
            if source in {"RETAIL", "SCAN", "HYBRID"}:
                return source
        return ""

    @staticmethod
    def _filename_ebook_source(meta: Meta) -> str:
        filename = f"{meta.basename_no_ext} {meta.title}".lower()
        return next(
            (
                candidate.upper()
                for candidate in ("scan", "hybrid", "retail")
                if candidate in filename
            ),
            "",
        )

    @staticmethod
    def _default_ebook_source(format_value: str) -> str:
        return "SCAN" if format_value == "PDF" else "RETAIL"

    @classmethod
    def _ebook_identity_parts(
        cls, author: str, series: str, title: str
    ) -> list[str]:
        parts: list[str] = []
        cls._append_named_segment(parts, author)
        cls._append_named_segment(parts, series, separated=bool(parts))
        cls._append_named_segment(parts, title, separated=bool(parts))
        return parts

    @classmethod
    def _author_title_parts(cls, author: str, title: str) -> list[str]:
        parts: list[str] = []
        cls._append_named_segment(parts, author)
        cls._append_named_segment(parts, title, separated=bool(parts))
        return parts

    @staticmethod
    def _append_named_segment(
        parts: list[str], value: str, *, separated: bool = False
    ) -> None:
        if not value:
            return
        if separated:
            parts.append("-")
        parts.append(value)

    @staticmethod
    def _nonempty_parts(values: tuple[str, ...]) -> list[str]:
        return [value for value in values if value]

    @staticmethod
    def _compact_parts(parts: list[str]) -> str:
        return " ".join(" ".join(parts).split())

    def _video_release_name(self, meta: Meta) -> str:
        name = self._without_episode_title(meta.name, meta)
        identity = ReleaseYearIdentity.from_release(meta)
        return self._apply_aka_year_order(name, meta, identity.canonical_text)

    def _apply_aka_year_order(self, name: str, meta: Meta, year: str) -> str:
        title = str(meta.title or "").strip()
        if not self._has_name_year_title(name, year, title):
            return name
        return self._normalize_aka_year_order(
            name, title=title, aka=str(meta.aka or ""), year=year
        )

    @staticmethod
    def _has_name_year_title(name: str, year: str, title: str) -> bool:
        return bool(name) and bool(year) and bool(title)

    @staticmethod
    def _without_episode_title(name: str, meta: Meta) -> str:
        if meta.category != "TV" or meta.episode_title == "":
            return name
        return name.replace(
            f"{meta.episode_title} {meta.resolution}", f"{meta.resolution}", 1
        )

    @staticmethod
    def _release_year(meta: Meta) -> str:
        return ReleaseYearIdentity.from_release(meta).canonical_text

    @classmethod
    def _normalize_aka_year_order(
        cls, name: str, title: str, aka: str, year: str
    ) -> str:
        compact = cls._compact_name(name)
        if not cls._aka_year_inputs(name, title, aka, year):
            return compact
        aka_name = cls._aka_name(aka)
        if not aka_name:
            return compact
        match = cls._aka_year_match(name, title, aka_name, year)
        if match is None:
            return compact
        return cls._format_aka_year_match(match, aka_name, year)

    @staticmethod
    def _compact_name(name: str) -> str:
        return " ".join(name.split())

    @staticmethod
    def _format_aka_year_match(
        match: re.Match[str], aka_name: str, year: str
    ) -> str:
        suffix = str(match.group("suffix") or "").strip()
        return " ".join(
            f"{match.group('title')} AKA {aka_name} {year} {suffix}".split()
        )

    @staticmethod
    def _aka_year_inputs(name: str, title: str, aka: str, year: str) -> bool:
        return bool(name and title and aka and year)

    @staticmethod
    def _aka_name(aka: str) -> str:
        return re.sub(r"^AKA\s+", "", aka, flags=re.IGNORECASE).strip()

    @staticmethod
    def _aka_year_match(
        name: str, title: str, aka_name: str, year: str
    ) -> re.Match[str] | None:
        return re.match(
            rf"^(?P<title>{re.escape(title)})\s+{re.escape(year)}\s+AKA\s+{re.escape(aka_name)}(?P<suffix>\s+.*)?$",
            name,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _music_field(
        release: dict[str, Any], name: str, default: Any = ""
    ) -> Any:
        """Read a serialized MusicRelease field, ignoring its provenance metadata."""
        fields_raw = release.get("fields")
        fields = (
            cast(dict[str, Any], fields_raw)
            if isinstance(fields_raw, dict)
            else {}
        )
        field_raw = fields.get(name)
        field = (
            cast(dict[str, Any], field_raw)
            if isinstance(field_raw, dict)
            else {}
        )
        return (
            field.get("value", default) if isinstance(field, dict) else default
        )

    @staticmethod
    def _music_source(value: Any) -> str:
        """Use the source spelling prescribed by Zenith's music naming guide."""
        source = str(value or "").strip()
        aliases = {
            "cd": "CD",
            "web": "WEB",
            "vinyl": "Vinyl",
            "sacd": "SACD",
            "dvd": "DVD",
            "bd": "BD",
            "soundboard": "Soundboard",
            "dat": "DAT",
            "cassette": "Cassette",
        }
        return aliases.get(source.casefold(), source)

    @staticmethod
    def _music_sample_rate(value: Any) -> str:
        try:
            return f"{float(value) / 1000:g}kHz"
        except TypeError, ValueError:
            return ""

    @classmethod
    def _music_name(cls, meta: Meta) -> str:
        release = cls._music_release(meta)
        artist = cls._music_artist_name(release, meta)
        album = cls._music_field(release, "album", meta.title or meta.name)
        year = cls._music_field(
            release,
            "release_year",
            cls._music_field(release, "year", meta.year),
        )
        source = cls._music_source(
            cls._music_field(release, "media", meta.source)
        )
        track = cls._music_first_track(release)
        codec = cls._music_codec(release, track, meta)
        format_parts = cls._music_format_parts(release, track, source, codec)
        title_parts = cls._music_title_parts(artist, album, year, format_parts)
        return (
            f"{cls._compact_parts(title_parts)}{str(meta.tag or '').strip()}"
        )

    @classmethod
    def _music_artist_name(cls, release: dict[str, Any], meta: Meta) -> str:
        artist = cls._music_field(release, "artist", meta.artist)
        direct = cls._direct_artist_name(artist)
        if direct:
            return direct
        return cls._artist_collection_name(
            cls._music_field(release, "artists", [])
        )

    @staticmethod
    def _direct_artist_name(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    @staticmethod
    def _artist_collection_name(value: Any) -> str:
        if isinstance(value, list):
            items = cast(list[Any], value)
            return " & ".join(
                text for item in items if (text := str(item).strip())
            )
        return str(value or "").strip()

    @staticmethod
    def _music_first_track(release: dict[str, Any]) -> dict[str, Any]:
        tracks = release.get("tracks")
        if (
            not isinstance(tracks, list)
            or not tracks
            or not isinstance(tracks[0], dict)
        ):
            return {}
        return cast(dict[str, Any], tracks[0])

    @classmethod
    def _music_codec(
        cls, release: dict[str, Any], track: dict[str, Any], meta: Meta
    ) -> str:
        fallback = (
            track.get("codec")
            or track.get("format")
            or meta.format
            or meta.type
        )
        value = cls._music_field(release, "format", fallback)
        return str(value or "").upper().strip()

    @classmethod
    def _music_format_parts(
        cls,
        release: dict[str, Any],
        track: dict[str, Any],
        source: str,
        codec: str,
    ) -> list[str]:
        values = (
            source,
            codec,
            cls._music_quality_label(release, track),
            cls._music_bitrate_label(track, codec),
            cls._music_release_type_label(release),
        )
        return [value for value in values if value]

    @classmethod
    def _music_release_type_label(cls, release: dict[str, Any]) -> str:
        return (
            "Single"
            if str(cls._music_field(release, "release_type", "")).casefold()
            == "single"
            else ""
        )

    @classmethod
    def _music_quality_label(
        cls, release: dict[str, Any], track: dict[str, Any]
    ) -> str:
        bit_depth = track.get("bit_depth") or cls._music_field(
            release, "nfo_bit_depth"
        )
        sample_rate = track.get("sample_rate") or cls._music_field(
            release, "nfo_sample_rate"
        )
        bit_depth_name = f"{bit_depth}bit" if bit_depth else ""
        sample_rate_name = (
            cls._music_sample_rate(sample_rate) if sample_rate else ""
        )
        return cls._join_quality_parts(bit_depth_name, sample_rate_name)

    @staticmethod
    def _join_quality_parts(bit_depth: str, sample_rate: str) -> str:
        if bit_depth and sample_rate:
            return f"{bit_depth}-{sample_rate}"
        return bit_depth or sample_rate

    @staticmethod
    def _music_bitrate_label(track: dict[str, Any], codec: str) -> str:
        bitrate = track.get("bitrate")
        if not bitrate or codec in {"FLAC", "ALAC", "WAV", "AIFF"}:
            return ""
        try:
            kbps = round(float(bitrate) / 1000)
        except TypeError, ValueError:
            return ""
        mode = str(track.get("bitrate_mode") or "").upper().strip()
        return f"{kbps} {mode}".strip()

    @classmethod
    def _music_title_parts(
        cls, artist: str, album: Any, year: Any, format_parts: list[str]
    ) -> list[str]:
        parts = cls._author_title_parts(
            str(artist or "").strip(), str(album or "").strip()
        )
        parts.extend(cls._nonempty_parts((cls._music_year_label(year),)))
        label = cls._music_format_label(format_parts)
        if label:
            parts.extend(["-", label])
        return parts

    @staticmethod
    def _music_year_label(year: Any) -> str:
        return f"({year})" if year else ""

    @staticmethod
    def _music_format_label(parts: list[str]) -> str:
        return f"[{' '.join(parts)}]" if parts else ""

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
            "AUDIOBOOK": "7",
            "BOOK": "6",
            "MISC": "9",
            "GAME": "3",
            "MUSIC": "5",
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

    @staticmethod
    def _resolved_category(meta: Meta, requested: str) -> str:
        if requested:
            return requested
        if meta.audiobook:
            return "AUDIOBOOK"
        return "MISC" if _is_misc(meta) else str(meta.category)

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
        if type:
            return {"type_id": mapping.get(type.upper().strip(), "0")}
        return {"type_id": self._resolved_meta_type(meta, mapping)}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "DVDRIP": "11",
            "FLAC": "7",
            "MP3": "8",
            "EPUB": "9",
            "M4B": "10",
            "PDF": "19",
            "OTHER": "16",
        }

    @classmethod
    def _resolved_meta_type(cls, meta: Meta, mapping: dict[str, str]) -> str:
        category = str(meta.category or "")
        special = cls._special_category_type(meta, mapping, category)
        if special is not None:
            return special
        value = str(meta.type or "").upper().strip().lstrip(".")
        return mapping.get(value, "0")

    @classmethod
    def _special_category_type(
        cls, meta: Meta, mapping: dict[str, str], category: str
    ) -> str | None:
        if category == "GAME":
            return "16"
        if category == "BOOK":
            return mapping.get(_book_format(meta), "16")
        if category == "MUSIC":
            return cls._music_type_id(meta, mapping)
        return None

    @classmethod
    def _music_type_id(cls, meta: Meta, mapping: dict[str, str]) -> str:
        value: Any = meta.format
        if not value and isinstance(meta.music_release, dict):
            value = cls._music_field(meta.music_release, "format")
        return mapping.get(str(value or "").upper(), "0")

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        data: dict[str, str] = {}
        if meta.category == "MUSIC":
            data.update(self._music_external_data(meta))
        if meta.category == "BOOK" and not _is_misc(meta):
            data.update(self._book_identifier_data(meta))
        return data

    @classmethod
    def _music_external_data(cls, meta: Meta) -> dict[str, str]:
        release = cls._music_release(meta)
        external_ids = cls._external_ids(release)
        data = cls._musicbrainz_data(external_ids)
        if meta.music_discogs_enabled:
            data.update(cls._discogs_data(meta, external_ids))
        return data

    @staticmethod
    def _external_ids(release: dict[str, Any]) -> dict[str, Any]:
        value = release.get("external_ids")
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @classmethod
    def _musicbrainz_data(cls, external_ids: dict[str, Any]) -> dict[str, str]:
        release_id = str(external_ids.get("musicbrainz_release") or "").strip()
        group_id = str(
            external_ids.get("musicbrainz_release_group") or ""
        ).strip()
        valid_release = cls._valid_musicbrainz_id(release_id)
        valid_group = cls._valid_musicbrainz_id(group_id)
        if not cls._has_any_valid_id(valid_release, valid_group):
            return {}
        return cls._musicbrainz_fields(
            release_id, group_id, valid_release, valid_group
        )

    @staticmethod
    def _has_any_valid_id(first: bool, second: bool) -> bool:
        return first or second

    @staticmethod
    def _musicbrainz_fields(
        release_id: str, group_id: str, valid_release: bool, valid_group: bool
    ) -> dict[str, str]:
        data = {"exists_on_musicbrainz": "1"}
        if valid_release:
            data["musicbrainz_release_id"] = release_id
        if valid_group:
            data["musicbrainz_release_group_id"] = group_id
        return data

    @staticmethod
    def _valid_musicbrainz_id(value: str) -> bool:
        pattern = re.compile(
            r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE
        )
        return bool(pattern.fullmatch(value))

    @classmethod
    def _discogs_data(
        cls, meta: Meta, external_ids: dict[str, Any]
    ) -> dict[str, str]:
        release_id = cls._discogs_release_id(meta, external_ids)
        master_id = cls._discogs_master_id(meta, external_ids)
        valid_release = release_id.isdecimal()
        valid_master = master_id.isdecimal()
        if not cls._has_any_valid_id(valid_release, valid_master):
            return {}
        return cls._discogs_fields(
            release_id, master_id, valid_release, valid_master
        )

    @staticmethod
    def _discogs_release_id(meta: Meta, external_ids: dict[str, Any]) -> str:
        for value in (
            external_ids.get("discogs_release"),
            meta.music_discogs_release_id,
            meta.music_discogs_id,
        ):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _discogs_master_id(meta: Meta, external_ids: dict[str, Any]) -> str:
        for value in (
            external_ids.get("discogs_master"),
            meta.music_discogs_master_id,
        ):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _discogs_fields(
        release_id: str,
        master_id: str,
        valid_release: bool,
        valid_master: bool,
    ) -> dict[str, str]:
        data = {"exists_on_discogs": "1"}
        if valid_release:
            data["discogs_release_id"] = release_id
        if valid_master:
            data["discogs_master_id"] = master_id
        return data

    @staticmethod
    def _book_identifier_data(meta: Meta) -> dict[str, str]:
        data: dict[str, str] = {}
        if meta.isbn:
            data["isbn"] = meta.isbn
        if meta.asin:
            data["asin"] = meta.asin
        return data

    async def get_additional_files(
        self, meta: Meta
    ) -> dict[str, tuple[str, bytes, str]]:
        files = await super().get_additional_files(meta)
        # Zenith only accepts the original audiobook cover when it is at most 3 MiB.
        if meta.audiobook and meta.artwork_path:
            cover_file = await self.get_image_file(
                meta.artwork_path, max_size=self.book_cover_max_size
            )
            if cover_file:
                files["torrent-cover"] = cover_file
        return files
