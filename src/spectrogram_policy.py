# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
from collections.abc import Mapping
from typing import Any

from src.meta import Meta


def _eligible_trackers(meta: Meta) -> set[str]:
    statuses: Any = meta.tracker_status
    if isinstance(statuses, dict) and statuses:
        return {
            str(tracker).replace(" ", "").upper()
            for tracker, status in statuses.items()
            if isinstance(status, Mapping) and status.get("upload") is True
        }
    selected: Any = meta.trackers
    if isinstance(selected, str):
        return {selected.replace(" ", "").upper()}
    if isinstance(selected, (list, tuple, set)):
        return {str(tracker).replace(" ", "").upper() for tracker in selected}
    return set()


def should_process_audio_spectrogram(meta: Meta, config: dict[str, Any]) -> bool:
    category = str(meta.category or "").strip().upper()
    category_supported = category not in {"BOOK", "GAME"} or bool(meta.audiobook)
    default_config = config.get("DEFAULT")
    configured = bool(default_config.get("add_audio_spectrogram", False)) if isinstance(default_config, dict) else False
    requested = bool(meta.audio_spectrogram or meta.audio_spectrogram_tracks or configured)
    if not category_supported or not requested:
        return False

    eligible_trackers = _eligible_trackers(meta)
    if not eligible_trackers:
        return False
    return category == "MUSIC" or eligible_trackers != {"DARKPEERS"}
