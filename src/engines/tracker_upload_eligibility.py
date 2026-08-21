from __future__ import annotations

from collections.abc import Sequence

from src.domain_models.tracker_upload_state import (
    TrackerUploadEligibility,
    TrackerUploadState,
)


def evaluate_tracker_upload_eligibility(
    states: Sequence[TrackerUploadState],
) -> tuple[TrackerUploadEligibility, ...]:
    return tuple(_eligibility(state) for state in states)


def eligible_tracker_names(
    states: Sequence[TrackerUploadState],
) -> tuple[str, ...]:
    return tuple(result.tracker for result in evaluate_tracker_upload_eligibility(states) if result.eligible)


def _eligibility(state: TrackerUploadState) -> TrackerUploadEligibility:
    blocker = _blocker(state)
    if blocker:
        return TrackerUploadEligibility(tracker=state.tracker, eligible=False, reason=blocker)
    return TrackerUploadEligibility(tracker=state.tracker, eligible=True)


def _blocker(state: TrackerUploadState) -> str:
    if state.banned:
        return _state_reason(state.reason, "banned")
    if state.skipped:
        return _state_reason(state.reason, "skipped")
    if state.dupe:
        return _state_reason(state.reason, "duplicate")
    if not state.upload_allowed:
        return _state_reason(state.reason, "upload not allowed")
    return ""


def _state_reason(reason: str, fallback: str) -> str:
    return reason if reason else fallback
