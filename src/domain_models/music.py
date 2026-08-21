"""Typed, tracker-neutral representation of a music release.

The model deliberately carries provenance for every value.  Tracker adapters can
therefore make conservative decisions without silently replacing file-tag data
with a lower-confidence directory or remote-service guess.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast


def _object_mapping(value: object) -> Mapping[object, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[object, object], value)


def _object_list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _keyword_mapping(value: object) -> Mapping[str, Any] | None:
    values = _object_mapping(value)
    if values is None or not all(isinstance(key, str) for key in values):
        return None
    return cast(Mapping[str, Any], values)


class MetadataSource(StrEnum):
    USER = "user"
    FILE_TAG = "file_tag"
    AUXILIARY = "auxiliary"
    DIRECTORY = "directory"
    EXTERNAL = "external"
    TRACKER = "tracker"
    INFERRED = "inferred"


@dataclass(frozen=True)
class MetadataValue:
    value: Any
    source: MetadataSource
    confidence: float


@dataclass
class AudioTrack:
    path: str
    relative_path: str
    format: str
    codec: str
    bitrate: int | None = None
    bitrate_mode: str | None = None
    bit_depth: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    duration: float | None = None
    disc_number: int | None = None
    track_number: int | None = None
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    title: str = ""
    date: str = ""
    label: str = ""
    catalogue_number: str = ""
    genre: list[str] = field(default_factory=list)
    isrc: str = ""
    tags: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class AuxiliaryFiles:
    logs: list[str] = field(default_factory=list)
    cues: list[str] = field(default_factory=list)
    nfos: list[str] = field(default_factory=list)
    sfvs: list[str] = field(default_factory=list)
    playlists: list[str] = field(default_factory=list)
    artwork: list[str] = field(default_factory=list)
    scans: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)


@dataclass
class MusicRelease:
    root: str
    tracks: list[AudioTrack] = field(default_factory=list)
    auxiliary: AuxiliaryFiles = field(default_factory=AuxiliaryFiles)
    fields: dict[str, MetadataValue] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _source_tier(source: MetadataSource) -> int:
        """Return the provenance priority used when deciding real conflicts."""
        return {
            MetadataSource.USER: 4,
            MetadataSource.FILE_TAG: 3,
            MetadataSource.AUXILIARY: 2,
            MetadataSource.DIRECTORY: 1,
            MetadataSource.EXTERNAL: 0,
            MetadataSource.TRACKER: 0,
            MetadataSource.INFERRED: 0,
        }[source]

    def set_field(self, name: str, value: Any, source: MetadataSource, confidence: float, *, force: bool = False) -> None:
        if self._empty_field_value(value):
            return
        existing = self.fields.get(name)
        if self._should_replace_field(existing, confidence, force):
            self.fields[name] = MetadataValue(value=value, source=source, confidence=confidence)
            return
        self._record_same_tier_conflict(name, value, source, existing)

    @staticmethod
    def _empty_field_value(value: Any) -> bool:
        return value in (None, "", [], {})

    @staticmethod
    def _should_replace_field(existing: MetadataValue | None, confidence: float, force: bool) -> bool:
        return force or existing is None or confidence > existing.confidence

    def _record_same_tier_conflict(
        self,
        name: str,
        value: Any,
        source: MetadataSource,
        existing: MetadataValue | None,
    ) -> None:
        if existing is None or existing.value == value:
            return
        if self._source_tier(source) != self._source_tier(existing.source):
            return
        values = self.conflicts.setdefault(name, [str(existing.value)])
        text = str(value)
        if text not in values:
            values.append(text)

    def get(self, name: str, default: Any = "") -> Any:
        item = self.fields.get(name)
        return item.value if item else default

    @property
    def formats(self) -> set[str]:
        return {track.format for track in self.tracks}

    @property
    def is_lossless(self) -> bool:
        return bool(self.tracks) and self.formats == {"FLAC"}

    @property
    def disc_count(self) -> int:
        return max((track.disc_number or 1 for track in self.tracks), default=1)

    @property
    def technical_variants(self) -> set[tuple[str, int | None, int | None, int | None, str | None]]:
        return {(t.format, t.bit_depth, t.sample_rate, t.channels, t.bitrate_mode) for t in self.tracks}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> MusicRelease:
        release = cls(root=str(data.get("root", "")))
        release.tracks = cls._tracks_from_dict(data.get("tracks", []))
        release.auxiliary = cls._auxiliary_from_dict(data.get("auxiliary", {}))
        release.fields = cls._fields_from_dict(data.get("fields", {}))
        release.conflicts = cls._conflicts_from_dict(data.get("conflicts", {}))
        release.warnings = cls._warnings_from_dict(data.get("warnings", []))
        release.external_ids = cls._external_ids_from_dict(data.get("external_ids", {}))
        return release

    @staticmethod
    def _tracks_from_dict(value: object) -> list[AudioTrack]:
        values = _object_list(value)
        if values is None:
            return []
        tracks: list[AudioTrack] = []
        for track_value in values:
            track = _keyword_mapping(track_value)
            if track is not None:
                tracks.append(AudioTrack(**track))
        return tracks

    @staticmethod
    def _auxiliary_from_dict(value: object) -> AuxiliaryFiles:
        auxiliary = _keyword_mapping(value)
        return AuxiliaryFiles(**auxiliary) if auxiliary is not None else AuxiliaryFiles()

    @staticmethod
    def _fields_from_dict(value: object) -> dict[str, MetadataValue]:
        values = _object_mapping(value)
        if values is None:
            return {}
        fields: dict[str, MetadataValue] = {}
        for key, field_value in values.items():
            parsed = MusicRelease._metadata_value_from_dict(field_value)
            if parsed is not None:
                fields[str(key)] = parsed
        return fields

    @staticmethod
    def _metadata_value_from_dict(value: object) -> MetadataValue | None:
        values = _object_mapping(value)
        if values is None:
            return None
        source_value = values.get("source", MetadataSource.INFERRED)
        source = MetadataSource(source_value) if isinstance(source_value, str) else MetadataSource.INFERRED
        confidence_value = values.get("confidence", 0)
        confidence = float(confidence_value) if isinstance(confidence_value, str | int | float) else 0.0
        return MetadataValue(value=values.get("value"), source=source, confidence=confidence)

    @staticmethod
    def _conflicts_from_dict(value: object) -> dict[str, list[str]]:
        values = _object_mapping(value)
        if values is None:
            return {}
        conflicts: dict[str, list[str]] = {}
        for key, conflict_value in values.items():
            conflict_items = _object_list(conflict_value)
            if conflict_items is not None:
                conflicts[str(key)] = [str(item) for item in conflict_items]
        return conflicts

    @staticmethod
    def _warnings_from_dict(value: object) -> list[str]:
        values = _object_list(value)
        return [str(item) for item in values] if values is not None else []

    @staticmethod
    def _external_ids_from_dict(value: object) -> dict[str, str]:
        values = _object_mapping(value)
        if values is None:
            return {}
        return {str(key): str(external_id) for key, external_id in values.items()}

    @property
    def path(self) -> Path:
        return Path(self.root)
