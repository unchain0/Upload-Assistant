from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

_SIGNALS = (
    (
        "could not download torrent file",
        "registered_torrent_download_unavailable",
    ),
    ("does not exist, cannot add to client", "client_injection_blocked"),
    ("tracker announce failed", "tracker_announce_failed"),
)
_TRACKER = re.compile(
    r"(?:INFO:\s+|^\s*)(?P<tracker>[A-Z0-9]+)\s*:", re.IGNORECASE
)
_NON_TRACKERS = frozenset({"INFO", "WARNING", "ERROR"})


@dataclass(frozen=True)
class Detection:
    event_id: str
    observed_at: str
    tracker: str
    classification: str
    reason: str

    def to_record(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "observed_at": self.observed_at,
            "tracker": self.tracker,
            "classification": self.classification,
            "reason": self.reason,
        }


class UploadPipelineDetector:
    def __init__(self, *, dry_run: bool, max_items: int) -> None:
        self.dry_run = dry_run
        self.max_items = max_items

    def detect(self, log_lines: list[str]) -> list[Detection]:
        if not self.dry_run:
            raise PermissionError("--dry-run is required")

        detections: list[Detection] = []
        for line in log_lines:
            if len(detections) >= self.max_items:
                break
            reason = self._reason(line)
            tracker = self._tracker(line)
            if reason is None or tracker is None:
                continue
            detections.append(self._event(tracker, reason, line=line))
        return detections

    def detect_log(
        self, log_lines: list[str], *, source: str = ""
    ) -> list[Detection]:
        if not self.dry_run:
            raise PermissionError("--dry-run is required")

        detections: list[Detection] = []
        for index, line in enumerate(log_lines):
            if len(detections) >= self.max_items:
                break
            reason = self._reason(line)
            if reason is None:
                continue
            tracker = self._tracker(line) or self._following_tracker(
                log_lines, index
            )
            if tracker is not None:
                detections.append(
                    self._event(tracker, reason, source=source, line=line)
                )
        return detections

    @staticmethod
    def _reason(line: str) -> str | None:
        lower = line.lower()
        reason = next(
            (reason for marker, reason in _SIGNALS if marker in lower), None
        )
        if (
            reason == "registered_torrent_download_unavailable"
            and "404" not in lower
        ):
            return None
        return reason

    @staticmethod
    def _tracker(line: str) -> str | None:
        match = _TRACKER.search(line)
        if not match:
            return None
        tracker = match.group("tracker").upper()
        return None if tracker in _NON_TRACKERS else tracker

    def _following_tracker(
        self, log_lines: list[str], index: int
    ) -> str | None:
        for line in log_lines[index + 1 : index + 4]:
            tracker = self._tracker(line)
            if tracker is not None:
                return tracker
        return None

    @staticmethod
    def _event(
        tracker: str, reason: str, *, source: str = "", line: str = ""
    ) -> Detection:
        # Deterministic id: rescanning retained logs must not create a new event.
        fingerprint = f"{source}|{line}|{tracker}|{reason}"
        return Detection(
            event_id=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[
                :32
            ],
            observed_at=datetime.now(UTC).isoformat(),
            tracker=tracker,
            classification="TECHNICAL_UPLOAD_PIPELINE",
            reason=reason,
        )


class AuditLog:
    def __init__(self, directory: Path, *, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes

    def append(self, events: list[Detection]) -> Path:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        path = self.directory / "events.jsonl"
        with _audit_lock(self.directory / "events.lock"):
            known = _existing_event_ids(path)
            fresh = [event for event in events if event.event_id not in known]
            if not fresh:
                return path
            encoded = [
                json.dumps(event.to_record(), sort_keys=True) + "\n"
                for event in fresh
            ]
            size = path.stat().st_size if path.exists() else 0
            if size and size + sum(map(len, encoded)) > self.max_bytes:
                path.replace(self.directory / f"events-{uuid4().hex}.jsonl")
            descriptor = os.open(
                path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600
            )
            try:
                path.chmod(0o600)
                with os.fdopen(
                    descriptor, "a", encoding="utf-8", closefd=False
                ) as handle:
                    handle.writelines(encoded)
            finally:
                os.close(descriptor)
        return path


def _existing_event_ids(path: Path) -> set[str]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return set()
    known: set[str] = set()
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        event_id = record.get("event_id") if isinstance(record, dict) else None
        if isinstance(event_id, str):
            known.add(event_id)
    return known


@contextmanager
def _audit_lock(lock_path: Path) -> Iterator[None]:
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows
            import msvcrt

            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise BlockingIOError("audit log is locked") from None
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BlockingIOError("audit log is locked") from None
        try:
            yield
        finally:
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def run_dry_run(
    log_root: Path, *, dry_run: bool, max_logs: int, max_items: int
) -> list[Detection]:
    if max_logs < 0 or max_items < 0:
        raise ValueError("limits must be non-negative")
    detector = UploadPipelineDetector(dry_run=dry_run, max_items=max_items)
    detections: list[Detection] = []
    for path in sorted(log_root.rglob("upload*.log"))[:max_logs]:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        detections.extend(detector.detect_log(lines, source=str(path)))
        if len(detections) >= max_items:
            return detections[:max_items]
    return detections


def summarize_qbittorrent(
    torrents: list[dict[str, object]],
) -> dict[str, object]:
    try:
        states = Counter(
            str(torrent.get("state", "unknown")) for torrent in torrents
        )
        return {
            "total": len(torrents),
            "states": dict(sorted(states.items())),
            "incomplete": sum(
                float(torrent.get("progress", 0.0)) < 1.0
                for torrent in torrents
            ),
            "payload_missing": sum(
                not bool(torrent.get("content_path")) for torrent in torrents
            ),
        }
    except TypeError, ValueError:
        return {"status": "unknown"}


def load_qbittorrent_summary(
    base_url: str, fetch: Callable[[str], list[dict[str, object]]]
) -> dict[str, object]:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or not _is_local_host(host)
    ):
        raise ValueError("local qBittorrent base URL is required")
    return summarize_qbittorrent(
        fetch(f"{base_url.rstrip('/')}/torrents/info")
    )


def _is_local_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback
