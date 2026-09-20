"""Generic and Orpheus-specific validation without mutating a release."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from src.domain_models.music import MusicRelease


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
        issues.extend(self._format_issues(release))
        issues.extend(self._conflict_issues(release))
        issues.extend(self._track_sequence_issues(release))
        return issues

    @staticmethod
    def _required_metadata_issues(
        release: MusicRelease,
    ) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                ValidationLevel.ERROR,
                f"missing_{required}",
                f"Missing required {required} metadata.",
            )
            for required in ("artist", "album")
            if not release.get(required)
        ]

    @staticmethod
    def _format_issues(release: MusicRelease) -> list[ValidationIssue]:
        if len(release.formats) <= 1:
            return []
        formats = ", ".join(sorted(release.formats))
        return [
            ValidationIssue(
                ValidationLevel.ERROR,
                "mixed_formats",
                f"Release mixes formats: {formats}.",
            )
        ]

    @classmethod
    def _conflict_issues(cls, release: MusicRelease) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        album = cls._album_conflict_issue(release)
        artist = cls._artist_conflict_issue(release)
        if album is not None:
            issues.append(album)
        if artist is not None:
            issues.append(artist)
        return issues

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

    @classmethod
    def _track_sequence_issues(
        cls, release: MusicRelease
    ) -> list[ValidationIssue]:
        tracks_by_disc: dict[int, list[int]] = {}
        issues: list[ValidationIssue] = []
        for track in release.tracks:
            issue = cls._collect_track_number(track, tracks_by_disc)
            if issue is not None:
                issues.append(issue)
        issues.extend(cls._disc_sequence_issues(tracks_by_disc))
        return issues

    @staticmethod
    def _collect_track_number(
        track: object, tracks_by_disc: dict[int, list[int]]
    ) -> ValidationIssue | None:
        track_number = getattr(track, "track_number", None)
        if track_number:
            disc_number = getattr(track, "disc_number", None) or 1
            tracks_by_disc.setdefault(disc_number, []).append(track_number)
            return None
        if getattr(track, "title", ""):
            return None
        return ValidationIssue(
            ValidationLevel.WARNING,
            "untagged_track",
            f"{getattr(track, 'relative_path', '')} has neither a title nor a track number.",
        )

    @staticmethod
    def _disc_sequence_issues(
        tracks_by_disc: dict[int, list[int]],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for disc, numbers in tracks_by_disc.items():
            unique = sorted(set(numbers))
            if unique and unique != list(range(1, max(unique) + 1)):
                issues.append(
                    ValidationIssue(
                        ValidationLevel.WARNING,
                        "non_contiguous_tracks",
                        f"Disc {disc} has non-contiguous track numbers.",
                    )
                )
        return issues


class OrpheusMusicValidator(MusicValidator):
    """Mechanical checks derived from the Orpheus music upload rules.

    Warnings identify evidence which requires a human/staff decision; only clear
    violations are errors.  This prevents the tool from pretending it can prove
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

    def validate(self, release: MusicRelease) -> list[ValidationIssue]:
        issues = super().validate(release)
        issues.extend(self._orpheus_required_metadata_issues(release))
        for track in release.tracks:
            issues.extend(self._orpheus_track_issues(track))
        hybrid = self._hybrid_technical_issue(release)
        if hybrid is not None:
            issues.append(hybrid)
        issues.extend(self._orpheus_release_issues(release))
        return issues

    @staticmethod
    def _orpheus_required_metadata_issues(
        release: MusicRelease,
    ) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                ValidationLevel.ERROR,
                f"missing_{required}",
                f"Missing required Orpheus music metadata: {required}.",
            )
            for required in ("year", "media", "release_type")
            if not release.get(required)
        ]

    @classmethod
    def _orpheus_track_issues(cls, track: object) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues.extend(cls._format_container_issues(track))
        issues.extend(cls._flac_technical_issues(track))
        mp3 = cls._mp3_bitrate_issue(track)
        if mp3 is not None:
            issues.append(mp3)
        return issues

    @classmethod
    def _format_container_issues(cls, track: object) -> list[ValidationIssue]:
        issues = (
            cls._unsupported_format_issue(track),
            cls._invalid_flac_container_issue(track),
            cls._invalid_aac_container_issue(track),
        )
        return [issue for issue in issues if issue is not None]

    @classmethod
    def _unsupported_format_issue(
        cls, track: object
    ) -> ValidationIssue | None:
        track_format = str(getattr(track, "format", ""))
        if track_format in cls.ALLOWED_FORMATS:
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "unsupported_format",
            f"{path}: {track_format} is not an allowed Orpheus music format.",
        )

    @staticmethod
    def _invalid_flac_container_issue(track: object) -> ValidationIssue | None:
        if (
            getattr(track, "format", "") != "FLAC"
            or Path(str(getattr(track, "path", ""))).suffix.lower() == ".flac"
        ):
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "invalid_container",
            f"{path}: FLAC must use the .flac container.",
        )

    @staticmethod
    def _invalid_aac_container_issue(track: object) -> ValidationIssue | None:
        suffix = Path(str(getattr(track, "path", ""))).suffix.lower()
        if getattr(track, "format", "") != "AAC" or suffix in {".m4a", ".aac"}:
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "invalid_container",
            f"{path}: AAC must use the .m4a or .aac container.",
        )

    @classmethod
    def _flac_technical_issues(cls, track: object) -> list[ValidationIssue]:
        if getattr(track, "format", "") != "FLAC":
            return []
        issues = (
            cls._flac_bit_depth_issue(track),
            cls._flac_sample_rate_issue(track),
            cls._flac_16bit_rate_issue(track),
        )
        return [issue for issue in issues if issue is not None]

    @staticmethod
    def _flac_bit_depth_issue(track: object) -> ValidationIssue | None:
        bit_depth = getattr(track, "bit_depth", None)
        if not bit_depth or bit_depth <= 24:
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "bit_depth",
            f"{path}: FLAC depth exceeds 24-bit.",
        )

    @classmethod
    def _flac_sample_rate_issue(cls, track: object) -> ValidationIssue | None:
        sample_rate = getattr(track, "sample_rate", None)
        if not sample_rate or sample_rate in cls.ALLOWED_SAMPLE_RATES:
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "sample_rate",
            f"{path}: sample rate is not allowed by Orpheus.",
        )

    @staticmethod
    def _flac_16bit_rate_issue(track: object) -> ValidationIssue | None:
        bit_depth = getattr(track, "bit_depth", None)
        sample_rate = getattr(track, "sample_rate", None)
        if bit_depth != 16 or not sample_rate or sample_rate <= 48000:
            return None
        path = str(getattr(track, "relative_path", ""))
        return ValidationIssue(
            ValidationLevel.ERROR,
            "16bit_high_rate",
            f"{path}: 16-bit FLAC is limited to 44.1/48 kHz.",
        )

    @staticmethod
    def _mp3_bitrate_issue(track: object) -> ValidationIssue | None:
        if getattr(track, "format", "") != "MP3":
            return None
        bitrate = getattr(track, "bitrate", None)
        if (
            not bitrate
            or bitrate <= 320_000
            or getattr(track, "bitrate_mode", None) != "CBR"
        ):
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "mp3_cbr_limit",
            f"{getattr(track, 'relative_path', '')}: MP3 CBR bitrate exceeds 320 kbps.",
        )

    @staticmethod
    def _hybrid_technical_issue(
        release: MusicRelease,
    ) -> ValidationIssue | None:
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

    @classmethod
    def _orpheus_release_issues(
        cls, release: MusicRelease
    ) -> list[ValidationIssue]:
        issues = (
            cls._single_track_issue(release),
            cls._missing_log_issue(release),
            cls._possible_unsplit_issue(release),
            cls._unknown_media_issue(release),
            cls._missing_lineage_issue(release),
        )
        return [issue for issue in issues if issue is not None]

    @staticmethod
    def _single_track_issue(release: MusicRelease) -> ValidationIssue | None:
        if (
            len(release.tracks) != 1
            or str(release.get("release_type", "")) == "Single"
        ):
            return None
        return ValidationIssue(
            ValidationLevel.ERROR,
            "single_track",
            "A one-track upload must be an officially released single.",
        )

    @staticmethod
    def _missing_log_issue(release: MusicRelease) -> ValidationIssue | None:
        physical = release.get("media") in {"CD", "SACD", "BD"}
        if not physical or not release.is_lossless or release.auxiliary.logs:
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
        ambiguous = (
            len(release.tracks) == 1
            and release.is_lossless
            and not release.auxiliary.cues
        )
        if not ambiguous or str(release.get("release_type", "")) == "Single":
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
