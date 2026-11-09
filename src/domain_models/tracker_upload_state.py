from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrackerUploadState:
    tracker: str
    upload_allowed: bool
    skipped: bool = False
    banned: bool = False
    dupe: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TrackerUploadEligibility:
    tracker: str
    eligible: bool
    reason: str = ""
