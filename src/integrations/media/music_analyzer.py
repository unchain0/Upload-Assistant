"""Local, non-destructive music release analysis.

Only metadata is read.  No tag, filename, folder or audio content is changed.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import mutagen

from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease

AUDIO_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".m4a",
    ".aac",
    ".ac3",
    ".dts",
    ".wav",
    ".aiff",
    ".alac",
    ".ogg",
    ".opus",
    ".ape",
    ".wv",
}
ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LINEAGE_NAMES = ("lineage", "equipment", "transfer", "rip", "source")
DISC_RE = re.compile(
    r"(?:^|[ _.-])(?:cd|disc|disk)[ _.-]?(\d{1,2})(?:$|[ _.-])", re.I
)
YEAR_RE = re.compile(r"(?:^|[^0-9])((?:19|20)\d{2})(?:[^0-9]|$)")
LEADING_YEAR_RE = re.compile(r"^\s*[\[(]?\s*((?:19|20)\d{2})\b")
EDITION_RE = re.compile(
    r"(?:\[|\()([^\]\)]*(?:deluxe|remaster|anniversary|reissue|expanded|edition)[^\]\)]*)(?:\]|\))",
    re.I,
)
EDITION_WITH_YEAR_RE = re.compile(
    r"(?:\[|\()\s*((?:19|20)\d{2})[\s,.-]+([^\]\)]+)(?:\]|\))", re.I
)
BRACKET_RE = re.compile(r"\[([^\]]+)\]|\{([^\}]+)\}")
# Subsequent catalogue components must have an actual separator.  Allowing an
# optional separator before another ``\d+`` creates many equivalent ways to
# partition a long run of digits and can backtrack exponentially.
# Separator-required components avoid ambiguous nested digit partitions and ReDoS backtracking.
CATALOGUE_RE = re.compile(  # nosemgrep: regex_dos
    r"\b(?:[A-Z]{1,8}[- ]?\d{2,}(?:[- ]\d+)*|\d{1,2}[- ]\d{3,}(?:[- ]\d+)*|\d{5,})\b",
    re.I,
)
EDITION_MARKERS = (
    "remaster",
    "deluxe",
    "reissue",
    "expanded",
    "anniversary",
    "bonus tracks",
)
SOURCE_MARKERS = (
    "WEB",
    "DIGITAL",
    "CD",
    "VINYL",
    "LP",
    "SACD",
    "BLU-RAY",
    "BLURAY",
)
FORMAT_MARKERS = (
    "FLAC",
    "MP3",
    "AAC",
    "ALAC",
    "M4A",
    "OGG",
    "OPUS",
    "WAV",
    "AIFF",
)
REGIONAL_EDITION_RE = re.compile(
    r"\b(?:Japan|US|USA|UK|Europe|Germany|France|Canada|Australia)\b",
    re.I,
)


def _clean(value: Any) -> str:
    # Word joiners and other Unicode format controls are invisible but make
    # tracker titles compare differently.  Preserve visible artistic casing.
    text = "".join(
        char
        for char in str(value or "").replace("\x00", " ")
        if unicodedata.category(char) != "Cf"
    )
    return re.sub(r"\s+", " ", text).strip()


def _first(tags: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        for candidate in (key, key.upper(), key.lower()):
            values = tags.get(candidate)
            if values:
                return _clean(values[0])
    return ""


def _equivalent_tag_keys(key: str) -> tuple[str, str, str]:
    return key, key.upper(), key.lower()


def _append_unique_cleaned(values: list[str], candidates: list[str]) -> None:
    for value in candidates:
        cleaned = _clean(value)
        if cleaned and cleaned not in values:
            values.append(cleaned)


def _values(tags: dict[str, list[str]], *keys: str) -> list[str]:
    """Return all values for equivalent tag keys, preserving tag order."""
    values: list[str] = []
    for key in keys:
        for candidate in _equivalent_tag_keys(key):
            _append_unique_cleaned(values, tags.get(candidate, []))
    return values


def _split_main_artists(values: list[str]) -> list[str]:
    """Split only the common explicit collaboration separator.

    Tags with multiple values remain authoritative.  `` & `` is split only
    when it has whitespace around it, which avoids damaging names such as
    AC/DC.  Ambiguous aliases are intentionally left untouched.
    """
    artists: list[str] = []
    for value in values:
        parts = re.split(
            r"\s+&\s+|\s+(?:feat(?:uring)?|ft)\.?\s+|\s*;\s*",
            value,
            flags=re.I,
        )
        for part in parts:
            cleaned = _clean(part)
            if cleaned and cleaned not in artists:
                artists.append(cleaned)
    return artists


def _number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


_AUDIO_FORMAT_RULES: tuple[
    tuple[set[str], tuple[str, ...], tuple[str, str]], ...
] = (
    ({".flac"}, ("flac",), ("FLAC", "FLAC")),
    ({".mp3"}, ("mp3",), ("MP3", "MP3")),
    ({".m4a", ".aac"}, ("mp4", "aac"), ("AAC", "AAC")),
    ({".ogg"}, ("vorbis",), ("Ogg Vorbis", "Vorbis")),
    ({".ac3"}, (), ("AC3", "AC-3")),
    ({".dts"}, (), ("DTS", "DTS")),
)
_BITRATE_MODE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("VBR", ("VBR", "VARIABLE")),
    ("ABR", ("ABR", "AVERAGE")),
    ("CBR", ("CBR", "CONSTANT")),
)


def _matches_format_rule(
    ext: str,
    class_name: str,
    extensions: set[str],
    class_markers: tuple[str, ...],
) -> bool:
    return ext in extensions or any(
        marker in class_name for marker in class_markers
    )


def _format_for(path: Path, audio: Any) -> tuple[str, str]:
    ext = path.suffix.lower()
    class_name = audio.__class__.__name__.lower()
    for extensions, class_markers, result in _AUDIO_FORMAT_RULES:
        if _matches_format_rule(ext, class_name, extensions, class_markers):
            return result
    return ext.removeprefix(".").upper(), audio.__class__.__name__


def _bitrate_mode(info: Any) -> str | None:
    mode = getattr(info, "bitrate_mode", None)
    if mode is None:
        return None
    text = str(mode).upper()
    for value, markers in _BITRATE_MODE_RULES:
        if any(marker in text for marker in markers):
            return value
    return None


class MusicReleaseAnalyzer:
    """Build a normalized release from tags, sidecars and file structure."""

    @staticmethod
    def _release_files(supplied: Path) -> list[Path]:
        if supplied.is_file():
            return [supplied]
        return sorted(
            (entry for entry in supplied.rglob("*") if entry.is_file()),
            key=lambda entry: str(entry).casefold(),
        )

    def _classify_release_file(
        self, release: MusicRelease, file: Path, root: Path
    ) -> None:
        if file.suffix.lower() not in AUDIO_EXTENSIONS:
            self._classify_auxiliary(release, file, root)
            return
        track = self._read_track(file, root)
        if track is not None:
            release.tracks.append(track)

    def analyze(self, path: str | Path) -> MusicRelease:
        supplied = Path(path).expanduser()
        root = supplied if supplied.is_dir() else supplied.parent
        release = MusicRelease(root=str(root.resolve()))
        if not supplied.exists():
            release.warnings.append(f"Release path does not exist: {supplied}")
            return release
        for file in self._release_files(supplied):
            self._classify_release_file(release, file, root)
        display_name = supplied.stem if supplied.is_file() else supplied.name
        self._derive_release_fields(release, display_name)
        return release

    @staticmethod
    def _load_audio_objects(path: Path) -> tuple[Any | None, Any | None]:
        try:
            mutagen_module = cast(Any, mutagen)
            audio: Any | None = mutagen_module.File(path, easy=True)
            technical: Any | None = mutagen_module.File(path)
            return audio, technical
        except mutagen.MutagenError, OSError:  # pyright: ignore[reportPrivateImportUsage]
            return None, None

    @staticmethod
    def _normalized_tag_values(value: Any) -> list[str]:
        raw_values = (
            cast(list[Any], value) if isinstance(value, list) else [value]
        )
        cleaned: list[str] = []
        for item in raw_values:
            text = _clean(item)
            if text:
                cleaned.append(text)
        return cleaned

    @classmethod
    def _normalized_tags(cls, audio: Any | None) -> dict[str, list[str]]:
        tag_source = getattr(cast(Any, audio), "tags", None)
        if not isinstance(tag_source, Mapping):
            return {}
        tags: dict[str, list[str]] = {}
        for key, value in cast(Mapping[Any, Any], tag_source).items():
            tags[str(key)] = cls._normalized_tag_values(value)
        return tags

    @staticmethod
    def _resolved_disc_number(
        tagged_disc: int | None, folder_disc: int | None
    ) -> int | None:
        if folder_disc and folder_disc != tagged_disc:
            return folder_disc
        return tagged_disc or folder_disc

    @classmethod
    def _track_from_audio_objects(
        cls,
        path: Path,
        root: Path,
        audio: Any | None,
        technical: Any | None,
    ) -> AudioTrack | None:
        if audio is None and technical is None:
            return None
        source = audio or technical
        tags = cls._normalized_tags(audio)
        info = getattr(technical or source, "info", None)
        format_name, codec = _format_for(path, source)
        tagged_disc = _number(_first(tags, "discnumber", "disknumber", "disc"))
        folder_disc = cls._disc_from_path(path, root)
        return AudioTrack(
            path=str(path.resolve()),
            relative_path=str(path.relative_to(root)),
            format=format_name,
            codec=codec,
            bitrate=getattr(info, "bitrate", None),
            bitrate_mode=_bitrate_mode(info),
            bit_depth=getattr(info, "bits_per_sample", None),
            sample_rate=getattr(info, "sample_rate", None),
            channels=getattr(info, "channels", None),
            duration=getattr(info, "length", None),
            disc_number=cls._resolved_disc_number(tagged_disc, folder_disc),
            track_number=_number(_first(tags, "tracknumber", "track")),
            artist=_first(tags, "artist", "performer"),
            album_artist=_first(tags, "albumartist", "album artist"),
            album=_first(tags, "album"),
            title=_first(tags, "title"),
            date=_first(tags, "date", "year", "originaldate"),
            label=_first(tags, "organization", "label", "publisher"),
            catalogue_number=_first(
                tags,
                "catalognumber",
                "cataloguenumber",
                "catalog",
                "catalogue",
            ),
            genre=tags.get("genre", []),
            isrc=_first(tags, "isrc"),
            tags=tags,
        )

    def _read_track(self, path: Path, root: Path) -> AudioTrack | None:
        audio, technical = self._load_audio_objects(path)
        return self._track_from_audio_objects(path, root, audio, technical)

    @staticmethod
    def _auxiliary_bucket_name(ext: str) -> str | None:
        direct = {
            ".log": "logs",
            ".cue": "cues",
            ".nfo": "nfos",
            ".sfv": "sfvs",
            ".m3u": "playlists",
            ".m3u8": "playlists",
        }
        return direct.get(ext)

    @staticmethod
    def _is_scan_artwork(stem: str) -> bool:
        markers = ("scan", "booklet", "back", "tray", "obi", "inlay")
        return any(marker in stem for marker in markers)

    @staticmethod
    def _is_lineage_file(ext: str, stem: str) -> bool:
        return ext in {".txt", ".md", ".pdf"} and any(
            marker in stem for marker in LINEAGE_NAMES
        )

    def _classify_auxiliary(
        self, release: MusicRelease, path: Path, root: Path
    ) -> None:
        relative = str(path.relative_to(root))
        ext, stem = path.suffix.lower(), path.stem.lower()
        bucket_name = self._auxiliary_bucket_name(ext)
        if bucket_name:
            getattr(release.auxiliary, bucket_name).append(relative)
            return
        if ext in ARTWORK_EXTENSIONS:
            bucket = (
                release.auxiliary.scans
                if self._is_scan_artwork(stem)
                else release.auxiliary.artwork
            )
            bucket.append(relative)
            return
        if self._is_lineage_file(ext, stem):
            release.auxiliary.lineage.append(relative)
            return
        release.auxiliary.other.append(relative)

    @staticmethod
    def _disc_from_path(path: Path, root: Path) -> int | None:
        for parent in (path.parent, *path.parents):
            if parent == root.parent:
                break
            match = DISC_RE.search(parent.name)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _tag_years(release: MusicRelease) -> list[str]:
        return [
            track.date[:4]
            for track in release.tracks
            if re.fullmatch(r"(?:19|20)\d{2}.*", track.date)
        ]

    @staticmethod
    def _directory_year(release: MusicRelease) -> str:
        field = release.fields.get("year")
        if field is None or field.source != MetadataSource.DIRECTORY:
            return ""
        return str(release.get("year") or "")

    @classmethod
    def _should_set_tag_year(cls, release: MusicRelease) -> bool:
        if cls._directory_year(release):
            return False
        return not bool(release.get("edition") or release.get("edition_year"))

    @staticmethod
    def _tag_consensus_specs(
        release: MusicRelease, tag_years: list[str]
    ) -> tuple[tuple[str, list[str], float], ...]:
        labels = [track.label for track in release.tracks]
        catalogues = [track.catalogue_number for track in release.tracks]
        return (
            ("release_year", tag_years, 0.9),
            ("release_label", labels, 0.95),
            ("release_catalogue_number", catalogues, 0.95),
            ("label", labels, 0.95),
            ("catalogue_number", catalogues, 0.95),
        )

    def _apply_tag_consensus(self, release: MusicRelease) -> None:
        albums = [
            self._clean_album_tag(track.album) for track in release.tracks
        ]
        self._set_consensus(
            release, "album", albums, MetadataSource.FILE_TAG, 1.0
        )
        tag_years = self._tag_years(release)
        if self._should_set_tag_year(release):
            self._set_consensus(
                release, "year", tag_years, MetadataSource.FILE_TAG, 1.0
            )
        for name, values, confidence in self._tag_consensus_specs(
            release, tag_years
        ):
            self._set_consensus(
                release, name, values, MetadataSource.FILE_TAG, confidence
            )

    @staticmethod
    def _release_genres(release: MusicRelease) -> list[str]:
        return sorted(
            {
                genre
                for track in release.tracks
                for genre in track.genre
                if genre
            }
        )

    @staticmethod
    def _apply_inferred_release_fields(release: MusicRelease) -> None:
        release.set_field(
            "genres",
            MusicReleaseAnalyzer._release_genres(release),
            MetadataSource.FILE_TAG,
            0.8,
        )
        for name, value in (
            ("format", ", ".join(sorted(release.formats))),
            ("track_count", len(release.tracks)),
            ("disc_count", release.disc_count),
        ):
            release.set_field(name, value, MetadataSource.INFERRED, 1.0)

    @staticmethod
    def _apply_auxiliary_flags(release: MusicRelease) -> None:
        for name, value in (
            ("has_log", bool(release.auxiliary.logs)),
            ("has_cue", bool(release.auxiliary.cues)),
            ("has_nfo", bool(release.auxiliary.nfos)),
            ("has_sfv", bool(release.auxiliary.sfvs)),
            ("has_playlist", bool(release.auxiliary.playlists)),
        ):
            release.set_field(name, value, MetadataSource.AUXILIARY, 1.0)

    @staticmethod
    def _scene_release(folder_name: str, release: MusicRelease) -> bool:
        if not release.auxiliary.nfos:
            return False
        return bool(
            re.search(r"[-_][a-z0-9]{2,}(?:_[a-z0-9]+)?$", folder_name, re.I)
        )

    def _derive_release_fields(
        self, release: MusicRelease, folder_name: str
    ) -> None:
        if not release.tracks:
            release.warnings.append("No supported audio files found")
            return
        self._derive_from_directory(release, folder_name)
        self._set_artists(release)
        self._apply_tag_consensus(release)
        self._apply_inferred_release_fields(release)
        self._apply_auxiliary_flags(release)
        release.set_field(
            "scene",
            self._scene_release(folder_name, release),
            MetadataSource.INFERRED,
            0.7,
        )
        self._extract_nfo_metadata(release)
        self._inspect_playlists(release)
        self._inspect_sfvs(release)
        self._infer_media_from_logs(release)
        self._derive_release_type(release)

    @staticmethod
    def _read_sidecar(path: Path) -> str:
        """Read a small text sidecar using common scene encodings."""
        try:
            data = path.read_bytes()[:262_144]
        except OSError:
            return ""
        for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return ""

    @staticmethod
    def _nfo_line_field(line: str) -> tuple[str, str] | None:
        ascii_line = line.encode("ascii", "ignore").decode("ascii")
        match = re.search(
            r"(?:^|\s)(artist|album|label|publisher|genre|source|quality|url|www|retail\s*date|rel\s*date|release\s*date|rip\s*date)\s*[.:|]+\s*(.+?)\s*$",
            ascii_line,
            re.I,
        )
        if match is None:
            return None
        key = match.group(1).casefold().replace(" ", "_")
        value = _clean(match.group(2).strip(" |.:-"))
        return (key, value) if value else None

    @classmethod
    def _nfo_values(cls, release: MusicRelease) -> dict[str, list[str]]:
        values: dict[str, list[str]] = {}
        for relative in release.auxiliary.nfos:
            text = cls._read_sidecar(release.path / relative)
            for line in text.splitlines():
                field = cls._nfo_line_field(line)
                if field is None:
                    continue
                key, value = field
                bucket = values.setdefault(key, [])
                if value not in bucket:
                    bucket.append(value)
        return values

    @staticmethod
    def _nfo_first(values: dict[str, list[str]], *keys: str) -> str:
        for key in keys:
            for value in values.get(key, []):
                if value:
                    return value
        return ""

    @classmethod
    def _apply_nfo_identity(
        cls, release: MusicRelease, values: dict[str, list[str]]
    ) -> None:
        release.set_field(
            "artist",
            cls._nfo_first(values, "artist"),
            MetadataSource.AUXILIARY,
            0.7,
        )
        release.set_field(
            "album",
            cls._nfo_first(values, "album"),
            MetadataSource.AUXILIARY,
            0.7,
        )
        label = cls._nfo_first(values, "label", "publisher")
        release.set_field(
            "release_label", label, MetadataSource.AUXILIARY, 0.75
        )
        release.set_field("label", label, MetadataSource.AUXILIARY, 0.75)
        genre = cls._nfo_first(values, "genre")
        if genre:
            genres = [
                part.strip()
                for part in re.split(r"[,;/]", genre)
                if part.strip()
            ]
            release.set_field("genres", genres, MetadataSource.AUXILIARY, 0.75)
        source = cls._nfo_first(values, "source").upper()
        if source.startswith("WEB"):
            release.set_field("media", "WEB", MetadataSource.AUXILIARY, 0.75)

    @classmethod
    def _apply_nfo_store(
        cls, release: MusicRelease, values: dict[str, list[str]]
    ) -> None:
        store_url = cls._nfo_first(values, "url", "www")
        if not re.match(r"https?://", store_url, re.I):
            return
        release.set_field(
            "store_url", store_url, MetadataSource.AUXILIARY, 0.75
        )
        release.external_ids["store_url"] = store_url

    @staticmethod
    def _valid_nfo_date(value: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))

    @classmethod
    def _apply_nfo_dates(
        cls, release: MusicRelease, values: dict[str, list[str]]
    ) -> None:
        retail_date = cls._nfo_first(
            values, "retail_date", "rel_date", "release_date"
        )
        if cls._valid_nfo_date(retail_date):
            release.set_field(
                "retail_date", retail_date, MetadataSource.AUXILIARY, 0.8
            )
            release.set_field(
                "release_year", retail_date[:4], MetadataSource.AUXILIARY, 0.8
            )
        rip_date = cls._nfo_first(values, "rip_date")
        if cls._valid_nfo_date(rip_date):
            release.set_field(
                "rip_date", rip_date, MetadataSource.AUXILIARY, 0.7
            )

    @staticmethod
    def _nfo_quality_values(quality: str) -> tuple[int | None, int | None]:
        bit_depth_match = re.search(r"\b(16|24)\s*bit\b", quality, re.I)
        sample_rate_match = re.search(
            r"\b(44\.1|48|88\.2|96|176\.4|192)\s*k(?:hz|hertz)\b",
            quality,
            re.I,
        )
        bit_depth = int(bit_depth_match.group(1)) if bit_depth_match else None
        sample_rate = (
            int(float(sample_rate_match.group(1)) * 1000)
            if sample_rate_match
            else None
        )
        return bit_depth, sample_rate

    @staticmethod
    def _nfo_depth_conflicts(
        release: MusicRelease, declared_depth: int | None
    ) -> bool:
        if declared_depth is None:
            return False
        return any(
            track.bit_depth is not None and track.bit_depth != declared_depth
            for track in release.tracks
        )

    @staticmethod
    def _nfo_rate_conflicts(
        release: MusicRelease, declared_rate: int | None
    ) -> bool:
        if declared_rate is None:
            return False
        return any(
            track.sample_rate is not None
            and track.sample_rate != declared_rate
            for track in release.tracks
        )

    @classmethod
    def _nfo_quality_conflict(
        cls,
        release: MusicRelease,
        declared_depth: int | None,
        declared_rate: int | None,
    ) -> None:
        if cls._nfo_depth_conflicts(release, declared_depth):
            release.warnings.append(
                "NFO bit depth conflicts with the audio stream metadata."
            )
        if cls._nfo_rate_conflicts(release, declared_rate):
            release.warnings.append(
                "NFO sample rate conflicts with the audio stream metadata."
            )

    @classmethod
    def _apply_nfo_quality(
        cls, release: MusicRelease, values: dict[str, list[str]]
    ) -> None:
        quality = cls._nfo_first(values, "quality")
        if not quality:
            return
        release.set_field(
            "nfo_quality", quality, MetadataSource.AUXILIARY, 0.7
        )
        declared_depth, declared_rate = cls._nfo_quality_values(quality)
        if declared_depth is not None:
            release.set_field(
                "nfo_bit_depth", declared_depth, MetadataSource.AUXILIARY, 0.7
            )
        if declared_rate is not None:
            release.set_field(
                "nfo_sample_rate", declared_rate, MetadataSource.AUXILIARY, 0.7
            )
        cls._nfo_quality_conflict(release, declared_depth, declared_rate)

    def _extract_nfo_metadata(self, release: MusicRelease) -> None:
        """Use structured scene NFO fields only as lower-priority evidence."""
        values = self._nfo_values(release)
        self._apply_nfo_identity(release, values)
        self._apply_nfo_store(release, values)
        self._apply_nfo_dates(release, values)
        self._apply_nfo_quality(release, values)

    @staticmethod
    def _known_track_names(release: MusicRelease) -> set[str]:
        return {
            Path(track.relative_path).name.casefold()
            for track in release.tracks
        }

    @classmethod
    def _playlist_entries(cls, release: MusicRelease) -> list[str]:
        entries: list[str] = []
        for relative in release.auxiliary.playlists:
            for line in cls._read_sidecar(
                release.path / relative
            ).splitlines():
                value = line.strip()
                if value and not value.startswith("#"):
                    entries.append(
                        Path(value.replace("\\", "/")).name.casefold()
                    )
        return entries

    @classmethod
    def _sfv_entries(cls, release: MusicRelease) -> list[str]:
        entries: list[str] = []
        for relative in release.auxiliary.sfvs:
            for line in cls._read_sidecar(
                release.path / relative
            ).splitlines():
                match = re.match(
                    r"(.+?)\s+[A-F0-9]{8}\s*$", line.strip(), re.I
                )
                if match:
                    entries.append(Path(match.group(1)).name.casefold())
        return entries

    @classmethod
    def _record_sidecar_membership(
        cls,
        release: MusicRelease,
        entries: list[str],
        count_field: str,
        missing_field: str,
        label: str,
    ) -> None:
        if not entries:
            return
        known = cls._known_track_names(release)
        missing = sorted({entry for entry in entries if entry not in known})
        release.set_field(
            count_field, len(entries), MetadataSource.AUXILIARY, 0.9
        )
        release.set_field(
            missing_field, missing, MetadataSource.AUXILIARY, 0.9
        )
        if missing:
            release.warnings.append(
                f"{label} references {len(missing)} file(s) not present in the release."
            )

    def _inspect_playlists(self, release: MusicRelease) -> None:
        """Check playlist membership only; no playlist content is modified."""
        self._record_sidecar_membership(
            release,
            self._playlist_entries(release),
            "playlist_tracks",
            "playlist_missing_files",
            "Playlist",
        )

    def _inspect_sfvs(self, release: MusicRelease) -> None:
        """Validate SFV membership without the expensive/destructive hash pass."""
        self._record_sidecar_membership(
            release,
            self._sfv_entries(release),
            "sfv_entries",
            "sfv_missing_files",
            "SFV",
        )

    @staticmethod
    def _track_artist_tuple(track: AudioTrack) -> tuple[str, ...] | None:
        values = _values(track.tags, "albumartist", "album artist")
        if not values:
            values = _values(track.tags, "artist", "performer")
        artists = _split_main_artists(
            values or [track.album_artist or track.artist]
        )
        return tuple(artists) if artists else None

    @classmethod
    def _per_track_artists(
        cls, release: MusicRelease
    ) -> list[tuple[str, ...]]:
        return [
            artists
            for track in release.tracks
            if (artists := cls._track_artist_tuple(track)) is not None
        ]

    @staticmethod
    def _shared_artists(per_track: list[tuple[str, ...]]) -> set[str]:
        shared = {artist.casefold() for artist in per_track[0]}
        for item in per_track[1:]:
            shared.intersection_update(artist.casefold() for artist in item)
        return shared

    @staticmethod
    def _selected_artists(
        selected: tuple[str, ...], shared: set[str]
    ) -> list[str]:
        if not shared:
            return list(selected)
        return [artist for artist in selected if artist.casefold() in shared]

    @staticmethod
    def _record_artist_conflict(
        release: MusicRelease,
        per_track: list[tuple[str, ...]],
        shared: set[str],
    ) -> None:
        if shared:
            return
        unique = sorted({" & ".join(item) for item in per_track})
        if len(unique) > 1:
            release.conflicts["artist"] = unique

    @classmethod
    def _set_artists(cls, release: MusicRelease) -> None:
        per_track = cls._per_track_artists(release)
        if not per_track:
            return
        selected, count = Counter(per_track).most_common(1)[0]
        shared = cls._shared_artists(per_track)
        artists = cls._selected_artists(selected, shared)
        confidence = 1.0 if shared else count / len(per_track)
        release.set_field(
            "artists", artists, MetadataSource.FILE_TAG, confidence
        )
        release.set_field(
            "artist", " & ".join(artists), MetadataSource.FILE_TAG, confidence
        )
        cls._record_artist_conflict(release, per_track, shared)

    @staticmethod
    def _set_consensus(
        release: MusicRelease,
        name: str,
        values: list[str],
        source: MetadataSource,
        confidence: float,
    ) -> None:
        cleaned = [_clean(value) for value in values if _clean(value)]
        if not cleaned:
            return
        selected, _count = Counter(cleaned).most_common(1)[0]
        release.set_field(name, selected, source, confidence)
        unique = sorted(set(cleaned))
        if len(unique) > 1:
            release.conflicts[name] = unique

    @staticmethod
    def _clean_album_tag(value: str) -> str:
        """Remove obvious scene source/format suffixes from otherwise valid tags."""
        return re.sub(
            r"(?:[ _.-]+)(?:WEB|CD|FLAC|MP3|AAC)$",
            "",
            _clean(value),
            flags=re.I,
        ).strip()

    @staticmethod
    @staticmethod
    def _strip_directory_metadata(name: str) -> str:
        value = re.sub(
            r"\s+-\s+[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\s*$",
            "",
            name,
            flags=re.I,
        )
        value = re.sub(
            r"\s+-\s+(?:(?:16|24)bit\s+\d+(?:\.\d+)?kHz\s+)?(?:Digital Media|WEB|CD)(?:\s+(?:FLAC|MP3|AAC|ALAC))?\s*$",
            "",
            value,
            flags=re.I,
        )
        return re.sub(r"(?:\s*(?:\[[^\]]+\]|\{[^\}]+\}))*$", "", value)

    @staticmethod
    def _strip_trailing_directory_year(
        release: MusicRelease, value: str
    ) -> str:
        match = re.search(r"\s+\(((?:19|20)\d{2})\)\s*$", value)
        if match is None:
            return value
        release.set_field(
            "year", match.group(1), MetadataSource.DIRECTORY, 0.9
        )
        return value[: match.start()].rstrip()

    @staticmethod
    def _dated_directory_identity(value: str) -> tuple[str, str, str] | None:
        match = re.fullmatch(
            r"(.+?)\s+-\s+((?:19|20)\d{2})-\d{2}-\d{2}\s+-\s+(.+)",
            value,
        )
        if match is None:
            return None
        return match.group(1).strip(), match.group(2), match.group(3).strip()

    @staticmethod
    def _plain_directory_identity(value: str) -> tuple[str, str] | None:
        match = re.search(r"(?:^|\d{4}\s*-?\s*)(.+?)\s+-\s+(.+?)$", value)
        if match is None:
            return None
        return match.group(1).strip(), match.group(2).strip()

    @classmethod
    def _apply_directory_identity(
        cls, release: MusicRelease, normalized: str
    ) -> None:
        if release.get("artist") and release.get("album"):
            return
        value = cls._strip_directory_metadata(normalized)
        value = cls._strip_trailing_directory_year(release, value)
        dated = cls._dated_directory_identity(value)
        if dated is not None:
            artist, year, album = dated
            release.set_field("artist", artist, MetadataSource.DIRECTORY, 0.55)
            release.set_field("year", year, MetadataSource.DIRECTORY, 0.9)
            release.set_field("album", album, MetadataSource.DIRECTORY, 0.55)
            return
        identity = cls._plain_directory_identity(value)
        if identity is None:
            return
        artist, album = identity
        release.set_field("artist", artist, MetadataSource.DIRECTORY, 0.55)
        release.set_field("album", album, MetadataSource.DIRECTORY, 0.55)

    @staticmethod
    def _directory_media(normalized: str) -> str | None:
        upper = normalized.upper()
        rules = (
            ("WEB", ("WEB", "DIGITAL")),
            ("CD", (" CD", "CD-", "EAC")),
            ("Vinyl", ("VINYL", "LP")),
            ("SACD", ("SACD",)),
            ("BD", ("BLURAY", "BLU-RAY")),
        )
        for media, markers in rules:
            if any(marker in upper for marker in markers):
                return media
        return None

    @classmethod
    def _apply_directory_media(
        cls, release: MusicRelease, normalized: str
    ) -> None:
        media = cls._directory_media(normalized)
        if media:
            release.set_field("media", media, MetadataSource.DIRECTORY, 0.45)

    @staticmethod
    def _is_explicit_edition(detail: str) -> bool:
        lowered = detail.casefold()
        return any(marker in lowered for marker in EDITION_MARKERS)

    @classmethod
    def _apply_edition_with_year(
        cls, release: MusicRelease, normalized: str
    ) -> bool:
        match = EDITION_WITH_YEAR_RE.search(normalized)
        if match is None:
            return False
        year = match.group(1)
        detail = match.group(2).strip(" ,.-")
        release.set_field("release_year", year, MetadataSource.DIRECTORY, 0.95)
        if cls._is_explicit_edition(detail):
            release.set_field(
                "edition_year", year, MetadataSource.DIRECTORY, 0.95
            )
            release.set_field(
                "edition", detail, MetadataSource.DIRECTORY, 0.65
            )
            return True
        catalogue = CATALOGUE_RE.search(detail)
        if catalogue:
            release.set_field(
                "release_catalogue_number",
                catalogue.group(0),
                MetadataSource.DIRECTORY,
                0.65,
            )
        return True

    @staticmethod
    def _apply_edition_without_year(
        release: MusicRelease, normalized: str, has_year_edition: bool
    ) -> None:
        if has_year_edition:
            return
        match = EDITION_RE.search(normalized)
        if match is None:
            return
        detail = match.group(1).strip()
        if REGIONAL_EDITION_RE.search(detail):
            release.set_field(
                "release_title", detail, MetadataSource.DIRECTORY, 0.6
            )
            return
        release.set_field("edition", detail, MetadataSource.DIRECTORY, 0.55)

    @staticmethod
    def _directory_brackets(normalized: str) -> list[str]:
        return [
            _clean(value)
            for match in BRACKET_RE.findall(normalized)
            for value in match
            if value
        ]

    @staticmethod
    def _bracket_has_marker(detail: str, markers: tuple[str, ...]) -> bool:
        upper = detail.upper()
        return any(marker in upper for marker in markers)

    @classmethod
    def _edition_bracket_year(cls, detail: str) -> str | None:
        markers = (
            *SOURCE_MARKERS,
            *FORMAT_MARKERS,
            *(m.upper() for m in EDITION_MARKERS),
        )
        year_match = LEADING_YEAR_RE.search(detail)
        if year_match is None:
            return None
        if not cls._bracket_has_marker(detail, markers):
            return None
        return year_match.group(1)

    @classmethod
    def _record_bracket_release_year(
        cls, release: MusicRelease, detail: str, year: str
    ) -> None:
        if not cls._bracket_has_marker(
            detail, (*SOURCE_MARKERS, *FORMAT_MARKERS)
        ):
            return
        release.set_field("release_year", year, MetadataSource.DIRECTORY, 0.95)

    @classmethod
    def _edition_bracket_indexes(
        cls, release: MusicRelease, brackets: list[str]
    ) -> set[int]:
        indexes: set[int] = set()
        for index, detail in enumerate(brackets):
            year = cls._edition_bracket_year(detail)
            if year is None:
                continue
            cls._record_bracket_release_year(release, detail, year)
            indexes.add(index)
        return indexes

    @staticmethod
    def _set_directory_catalogue(
        release: MusicRelease, catalogue: str
    ) -> None:
        release.set_field(
            "release_catalogue_number",
            catalogue,
            MetadataSource.DIRECTORY,
            0.65,
        )
        release.set_field(
            "directory_catalogue_number",
            catalogue,
            MetadataSource.DIRECTORY,
            0.7,
        )

    @staticmethod
    def _catalogue_bracket_label(detail: str) -> str:
        value = re.sub(
            r"\b(?:CD|WEB|DIGITAL|VINYL|LP|SACD|DVD|BD|BLU-?RAY)\b",
            "",
            CATALOGUE_RE.sub("", detail),
            flags=re.I,
        )
        value = re.sub(r"[|,;/]+", " ", value)
        return re.sub(r"^[\s._-]+|[\s._-]+$", "", value)

    @classmethod
    def _apply_catalogue_bracket(
        cls, release: MusicRelease, detail: str
    ) -> bool:
        full = CATALOGUE_RE.fullmatch(detail)
        if full:
            cls._set_directory_catalogue(release, detail)
            return True
        catalogue = CATALOGUE_RE.search(detail)
        if catalogue is None:
            return False
        cls._set_directory_catalogue(release, catalogue.group(0))
        label = cls._catalogue_bracket_label(detail)
        if label and re.search(r"[A-Za-z]", label):
            release.set_field(
                "release_label", label, MetadataSource.DIRECTORY, 0.6
            )
        return True

    @classmethod
    def _is_label_bracket(cls, detail: str) -> bool:
        markers = (
            *SOURCE_MARKERS,
            *FORMAT_MARKERS,
            *(m.upper() for m in EDITION_MARKERS),
        )
        return not cls._bracket_has_marker(detail, markers) and bool(
            re.search(r"[A-Za-z]", detail)
        )

    @classmethod
    def _apply_bracket_release_metadata(
        cls, release: MusicRelease, normalized: str
    ) -> None:
        brackets = cls._directory_brackets(normalized)
        ignored = cls._edition_bracket_indexes(release, brackets)
        for index, detail in enumerate(brackets):
            if index in ignored:
                continue
            if cls._apply_catalogue_bracket(release, detail):
                continue
            if cls._is_label_bracket(detail):
                release.set_field(
                    "release_label", detail, MetadataSource.DIRECTORY, 0.6
                )

    @classmethod
    def _derive_from_directory(cls, release: MusicRelease, name: str) -> None:
        normalized = name.replace("_", " ")
        leading_year = LEADING_YEAR_RE.search(normalized)
        if leading_year:
            release.set_field(
                "year", leading_year.group(1), MetadataSource.DIRECTORY, 1.0
            )
        cls._apply_directory_identity(release, normalized)
        cls._apply_directory_media(release, normalized)
        has_year_edition = cls._apply_edition_with_year(release, normalized)
        cls._apply_edition_without_year(release, normalized, has_year_edition)
        cls._apply_bracket_release_metadata(release, normalized)

    @staticmethod
    def _read_log_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()[:262_144]
        except OSError:
            return b""

    @staticmethod
    def _decode_log(data: bytes) -> str:
        if (
            data.startswith((b"\xff\xfe", b"\xfe\xff"))
            or b"\x00" in data[:256]
        ):
            return data.decode("utf-16", errors="ignore")
        return data.decode("utf-8", errors="ignore")

    @classmethod
    def _log_indicates_cd(cls, path: Path) -> bool:
        data = cls._read_log_bytes(path)
        if not data:
            return False
        text = cls._decode_log(data)
        return bool(
            re.search(
                r"\b(?:Exact Audio Copy|X Lossless Decoder|CUERipper|whipper|CD-DA|CD-ROM)\b",
                text,
                re.I,
            )
        )

    @classmethod
    def _infer_media_from_logs(cls, release: MusicRelease) -> None:
        """Identify a CD rip from a rip log, never from a bare .log suffix."""
        if release.get("media") or not release.auxiliary.logs:
            return
        for relative in release.auxiliary.logs:
            if not cls._log_indicates_cd(release.path / relative):
                continue
            release.set_field("media", "CD", MetadataSource.AUXILIARY, 0.9)
            release.warnings.append(
                "Source media inferred as CD from the rip log."
            )
            return

    @staticmethod
    @staticmethod
    def _album_artist_credit(track: AudioTrack) -> tuple[str, ...] | None:
        artists = _split_main_artists(
            _values(track.tags, "albumartist", "album artist")
            or [track.album_artist]
        )
        if not artists:
            return None
        return tuple(artist.casefold() for artist in artists)

    @classmethod
    def _album_artist_credits(
        cls, release: MusicRelease
    ) -> set[tuple[str, ...]]:
        return {
            credit
            for track in release.tracks
            if (credit := cls._album_artist_credit(track)) is not None
        }

    @staticmethod
    def _track_artist_names(release: MusicRelease) -> set[str]:
        return {
            track.artist.casefold() for track in release.tracks if track.artist
        }

    @staticmethod
    def _shared_album_artists(
        credits: set[tuple[str, ...]],
    ) -> set[str]:
        if not credits:
            return set()
        shared = set(next(iter(credits)))
        for credit in credits:
            shared.intersection_update(credit)
        return shared

    @staticmethod
    def _has_explicit_various_artists(
        credits: set[tuple[str, ...]],
    ) -> bool:
        variants = {"various artists", "various", "va", "v.a."}
        for artists in credits:
            if any(credit in variants for credit in artists):
                return True
        return False

    @staticmethod
    def _has_stable_album_artist(
        shared: set[str], explicit_various: bool
    ) -> bool:
        return bool(shared) and not explicit_various

    @staticmethod
    def _inferred_compilation_from_tracks(
        credits: set[tuple[str, ...]], track_artists: set[str], count: int
    ) -> bool:
        if credits:
            return False
        return len(track_artists) > max(3, count // 2)

    @staticmethod
    def _album_artist_compilation_signal(
        credits: set[tuple[str, ...]], stable: bool, explicit_various: bool
    ) -> bool:
        if explicit_various:
            return True
        return len(credits) > 1 and not stable

    @classmethod
    def _is_compilation_release(
        cls,
        album: str,
        count: int,
        credits: set[tuple[str, ...]],
        track_artists: set[str],
        stable: bool,
        explicit_various: bool,
    ) -> bool:
        if "compilation" in album:
            return True
        if cls._album_artist_compilation_signal(
            credits, stable, explicit_various
        ):
            return True
        return cls._inferred_compilation_from_tracks(
            credits, track_artists, count
        )

    @staticmethod
    def _compilation_needs_artist_rewrite(
        explicit_various: bool,
        stable: bool,
        track_artists: set[str],
    ) -> bool:
        if explicit_various:
            return True
        return not stable and len(track_artists) > 3

    @staticmethod
    def _compilation_artists(release: MusicRelease) -> list[str]:
        artists: list[str] = []
        for track in release.tracks:
            values = _values(track.tags, "artist", "performer") or [
                track.artist
            ]
            for artist in _split_main_artists(values):
                if artist not in artists:
                    artists.append(artist)
        return artists

    @classmethod
    def _apply_compilation_artists(cls, release: MusicRelease) -> None:
        artists = cls._compilation_artists(release)
        if not artists:
            return
        release.set_field(
            "artists",
            artists,
            MetadataSource.INFERRED,
            1.0,
            force=True,
        )
        release.set_field(
            "artist",
            "Various Artists",
            MetadataSource.INFERRED,
            1.0,
            force=True,
        )

    @staticmethod
    def _album_title_release_type(album: str) -> str | None:
        if "soundtrack" in album or re.search(
            r"(?:^|[^\w])ost(?:$|[^\w])", album
        ):
            return "Soundtrack"
        if re.search(r"\blive\b", album):
            return "Live album"
        return None

    @staticmethod
    def _single_track_release_type(
        release: MusicRelease,
    ) -> tuple[str | None, bool]:
        if len(release.tracks) != 1:
            return None, False
        duration = release.tracks[0].duration or 0
        if duration <= 20 * 60:
            return "Single", False
        release.warnings.append(
            "A long one-track release cannot be safely classified as an official single without external metadata."
        )
        return None, True

    @staticmethod
    def _multi_track_release_type(album: str, count: int) -> str:
        if "ep" in album.split() or 2 <= count <= 6:
            return "EP"
        return "Album"

    @classmethod
    def _apply_compilation_release_type(
        cls, release: MusicRelease, album: str, count: int
    ) -> bool:
        credits = cls._album_artist_credits(release)
        track_artists = cls._track_artist_names(release)
        shared = cls._shared_album_artists(credits)
        explicit_various = cls._has_explicit_various_artists(credits)
        stable = cls._has_stable_album_artist(shared, explicit_various)
        if not cls._is_compilation_release(
            album,
            count,
            credits,
            track_artists,
            stable,
            explicit_various,
        ):
            return False
        if cls._compilation_needs_artist_rewrite(
            explicit_various, stable, track_artists
        ):
            cls._apply_compilation_artists(release)
        release.set_field(
            "release_type",
            "Compilation",
            MetadataSource.INFERRED,
            0.65,
        )
        return True

    @classmethod
    def _derive_release_type(cls, release: MusicRelease) -> None:
        album = str(release.get("album", "")).lower()
        count = len(release.tracks)
        title_type = cls._album_title_release_type(album)
        if title_type is not None:
            release.set_field(
                "release_type", title_type, MetadataSource.INFERRED, 0.65
            )
            return
        if cls._apply_compilation_release_type(release, album, count):
            return
        single_type, stop = cls._single_track_release_type(release)
        if stop:
            return
        value = single_type or cls._multi_track_release_type(album, count)
        release.set_field("release_type", value, MetadataSource.INFERRED, 0.65)
