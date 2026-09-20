"""Generic and Orpheus-specific validation without mutating a release."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from src.domain_models.music import AudioTrack, MusicRelease


class ValidationLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    level: ValidationLevel
    code: str
    message: str


class MusicValidator:
    """Validates portable invariants; tracker subclasses add policy."""

    @staticmethod
    def _required_metadata_issues(
        release: MusicRelease,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for required in ("artist", "album"):
            if release.get(required):
                continue
            issues.append(
                ValidationIssue(
                    ValidationLevel.ERROR,
                    f"missing_{required}",
                    f"Missing required {required} metadata.",
                )
            )
        return issues

    @staticmethod
    def _mixed_format_issue(release: MusicRelease) -> ValidationIssue | None:
        if len(release.formats) <= 1:
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "mixed_formats",
            f"Release mixes formats: {', '.join(sorted(release.formats))}.",
        )

    @staticmethod
    def _album_conflict_issue(release: MusicRelease) -> ValidationIssue | None:
        if not release.conflicts.get("album"):
            return None
        level = (
            ValidationLevel.WARNING
            if release.disc_count > 1
            else ValidationLevel.ERROR
        )
        return ValidationIssue(
            level,
            "inconsistent_album",
            "Audio tags do not agree on the album title.",
        )

    @staticmethod
    def _artist_conflict_issue(
        release: MusicRelease,
    ) -> ValidationIssue | None:
        if not release.conflicts.get("artist"):
            return None
        if str(release.get("artist", "")).casefold() == "various artists":
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "inconsistent_artist",
            "Audio tags contain multiple album artists.",
        )

    @staticmethod
    def _record_track_number(
        tracks_by_disc: dict[int, list[int]], track: AudioTrack
    ) -> None:
        if not track.track_number:
            return
        tracks_by_disc.setdefault(track.disc_number or 1, []).append(
            track.track_number
        )

    @staticmethod
    def _untagged_track_issue(track: AudioTrack) -> ValidationIssue | None:
        if track.track_number or track.title:
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "untagged_track",
            f"{track.relative_path} has neither a title nor a track number.",
        )

    @classmethod
    def _track_metadata_issues(
        cls, release: MusicRelease
    ) -> tuple[list[ValidationIssue], dict[int, list[int]]]:
        issues: list[ValidationIssue] = []
        tracks_by_disc: dict[int, list[int]] = {}
        for track in release.tracks:
            cls._record_track_number(tracks_by_disc, track)
            issue = cls._untagged_track_issue(track)
            if issue is not None:
                issues.append(issue)
        return issues, tracks_by_disc

    @staticmethod
    def _disc_sequence_issue(
        disc: int, numbers: list[int]
    ) -> ValidationIssue | None:
        unique = sorted(set(numbers))
        if not unique or unique == list(range(1, max(unique) + 1)):
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "non_contiguous_tracks",
            f"Disc {disc} has non-contiguous track numbers.",
        )

    @classmethod
    def _disc_sequence_issues(
        cls, tracks_by_disc: dict[int, list[int]]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for disc, numbers in tracks_by_disc.items():
            issue = cls._disc_sequence_issue(disc, numbers)
            if issue is not None:
                issues.append(issue)
        return issues

    @staticmethod
    def _append_optional(
        issues: list[ValidationIssue], issue: ValidationIssue | None
    ) -> None:
        if issue is not None:
            issues.append(issue)

    def validate(self, release: MusicRelease) -> list[ValidationIssue]:
        if not release.tracks:
            return [
                ValidationIssue(
                    ValidationLevel.ERROR,
                    "no_audio",
                    "No supported audio files were found.",
                )
            ]
        issues = self._required_metadata_issues(release)
        self._append_optional(issues, self._mixed_format_issue(release))
        self._append_optional(issues, self._album_conflict_issue(release))
        self._append_optional(issues, self._artist_conflict_issue(release))
        track_issues, tracks_by_disc = self._track_metadata_issues(release)
        issues.extend(track_issues)
        issues.extend(self._disc_sequence_issues(tracks_by_disc))
        return issues


class OrpheusMusicValidator(MusicValidator):
    """Mechanical checks derived from the Orpheus music upload rules.

    Warnings identify evidence which requires a human/staff decision; only clear
    violations are errors. This prevents the tool from pretending it can prove
    provenance or detect a transcode from tags alone.
    """

    ALLOWED_FORMATS: ClassVar[set[str]] = {
        "FLAC",
        "MP3",
        "Ogg Vorbis",
        "AAC",
        "AC3",
        "DTS",
    }
    ALLOWED_SAMPLE_RATES: ClassVar[set[int]] = {
        44100,
        48000,
        88200,
        96000,
        176400,
        192000,
    }

    @staticmethod
    def _orpheus_required_issues(
        release: MusicRelease,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for required in ("year", "media", "release_type"):
            if release.get(required):
                continue
            issues.append(
                ValidationIssue(
                    ValidationLevel.ERROR,
                    f"missing_{required}",
                    f"Missing required Orpheus music metadata: {required}.",
                )
            )
        return issues

    @classmethod
    def _format_issue(cls, track: AudioTrack) -> ValidationIssue | None:
        if track.format in cls.ALLOWED_FORMATS:
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "unsupported_format",
            f"{track.relative_path}: {track.format} is not an allowed Orpheus music format.",
        )

    @staticmethod
    def _container_issue(track: AudioTrack) -> ValidationIssue | None:
        suffix = Path(track.path).suffix.lower()
        if track.format == "FLAC" and suffix != ".flac":
            return ValidationIssue(
                ValidationLevel.ERROR,
                "invalid_container",
                f"{track.relative_path}: FLAC must use the .flac container.",
            )
        if track.format == "AAC" and suffix not in {".m4a", ".aac"}:
            return ValidationIssue(
                ValidationLevel.ERROR,
                "invalid_container",
                f"{track.relative_path}: AAC must use the .m4a or .aac container.",
            )
        return None

    @staticmethod
    def _flac_depth_issue(track: AudioTrack) -> ValidationIssue | None:
        if (
            track.format != "FLAC"
            or not track.bit_depth
            or track.bit_depth <= 24
        ):
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "bit_depth",
            f"{track.relative_path}: FLAC depth exceeds 24-bit.",
        )

    @classmethod
    def _flac_rate_issue(cls, track: AudioTrack) -> ValidationIssue | None:
        if track.format != "FLAC" or not track.sample_rate:
            return None
        if track.sample_rate in cls.ALLOWED_SAMPLE_RATES:
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "sample_rate",
            f"{track.relative_path}: sample rate is not allowed by Orpheus.",
        )

    @staticmethod
    def _flac_16bit_rate_issue(track: AudioTrack) -> ValidationIssue | None:
        if track.format != "FLAC" or track.bit_depth != 16:
            return None
        if not track.sample_rate or track.sample_rate <= 48000:
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "16bit_high_rate",
            f"{track.relative_path}: 16-bit FLAC is limited to 44.1/48 kHz.",
        )

    @staticmethod
    def _mp3_bitrate_issue(track: AudioTrack) -> ValidationIssue | None:
        if track.format != "MP3" or not track.bitrate:
            return None
        if track.bitrate <= 320_000 or track.bitrate_mode != "CBR":
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "mp3_cbr_limit",
            f"{track.relative_path}: MP3 CBR bitrate exceeds 320 kbps.",
        )

    @classmethod
    def _track_policy_issues(cls, track: AudioTrack) -> list[ValidationIssue]:
        return [
            issue
            for issue in (
                cls._format_issue(track),
                cls._container_issue(track),
                cls._flac_depth_issue(track),
                cls._flac_rate_issue(track),
                cls._flac_16bit_rate_issue(track),
                cls._mp3_bitrate_issue(track),
            )
            if issue is not None
        ]

    @staticmethod
    def _hybrid_issue(release: MusicRelease) -> ValidationIssue | None:
        if len(release.technical_variants) <= 1:
            return None
        media = str(release.get("media", "")).upper()
        level = (
            ValidationLevel.WARNING
            if media == "WEB"
            else ValidationLevel.ERROR
        )
        return ValidationIssue(
            level,
            "hybrid_technical",
            "Tracks have differing bit depth, sample rate, channels or bitrate mode; Orpheus requires evidence for a hybrid WEB release.",
        )

    @staticmethod
    def _single_track_issue(release: MusicRelease) -> ValidationIssue | None:
        if len(release.tracks) != 1:
            return None
        if str(release.get("release_type", "")) == "Single":
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "single_track",
            "A one-track upload must be an officially released single.",
        )

    @staticmethod
    def _missing_log_issue(release: MusicRelease) -> ValidationIssue | None:
        if release.get("media") not in {"CD", "SACD", "BD"}:
            return None
        if not release.is_lossless or release.auxiliary.logs:
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "missing_log",
            "A lossless physical-media rip has no rip log; it may be trumpable or require review.",
        )

    @staticmethod
    def _possible_unsplit_issue(
        release: MusicRelease,
    ) -> ValidationIssue | None:
        if len(release.tracks) != 1 or not release.is_lossless:
            return None
        if release.auxiliary.cues:
            return None
        if str(release.get("release_type", "")) == "Single":
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "possible_unsplit",
            "A one-file FLAC without a cue requires confirmation that it is an official single.",
        )

    @staticmethod
    def _unknown_media_issue(release: MusicRelease) -> ValidationIssue | None:
        if release.get("media"):
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "unknown_media",
            "Source media is unknown; do not guess. WEB is appropriate only for verified digital downloads.",
        )

    @staticmethod
    def _missing_lineage_issue(
        release: MusicRelease,
    ) -> ValidationIssue | None:
        media = str(release.get("media", "")).upper()
        if (
            media not in {"SACD", "BD", "CASSETTE"}
            or release.auxiliary.lineage
        ):
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "missing_lineage",
            "This source type requires or strongly benefits from lineage information.",
        )

    def validate(self, release: MusicRelease) -> list[ValidationIssue]:
        issues = super().validate(release)
        issues.extend(self._orpheus_required_issues(release))
        for track in release.tracks:
            issues.extend(self._track_policy_issues(track))
        for issue in (
            self._hybrid_issue(release),
            self._single_track_issue(release),
            self._missing_log_issue(release),
            self._possible_unsplit_issue(release),
            self._unknown_media_issue(release),
            self._missing_lineage_issue(release),
        ):
            self._append_optional(issues, issue)
        return issues
