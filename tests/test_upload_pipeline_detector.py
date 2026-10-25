from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.services.upload_pipeline_detector import (
    AuditLog,
    UploadPipelineDetector,
    load_qbittorrent_summary,
    run_dry_run,
    summarize_qbittorrent,
)


def test_detector_rejects_execution_without_dry_run() -> None:
    detector = UploadPipelineDetector(dry_run=False, max_items=5)

    with pytest.raises(PermissionError, match="--dry-run"):
        detector.detect([])


def test_detector_classifies_download_404_without_retaining_sensitive_log_content() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    events = detector.detect(
        [
            "DIGITALCORE: Could not download torrent file: 404 https://example.invalid/path?token=secret"
        ]
    )

    assert len(events) == 1
    record = events[0].to_record()
    assert record["classification"] == "TECHNICAL_UPLOAD_PIPELINE"
    assert record["reason"] == "registered_torrent_download_unavailable"
    assert record["tracker"] == "DIGITALCORE"
    assert "secret" not in repr(record)
    assert "example.invalid" not in repr(record)


def test_detector_classifies_missing_client_torrent_without_path() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    events = detector.detect(["ULCX: Torrent file /private/release.torrent does not exist, cannot add to client"])

    assert [event.to_record()["reason"] for event in events] == ["client_injection_blocked"]
    assert "/private" not in repr(events[0].to_record())


def test_detector_classifies_tracker_announce_failure() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    events = detector.detect(["SEEDPOOL: Tracker announce failed after transport timeout"])

    assert [event.to_record()["reason"] for event in events] == ["tracker_announce_failed"]


def test_audit_log_uses_private_permissions_and_appends_sanitized_records(tmp_path: Path) -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)
    event = detector.detect(["DIGITALCORE: Could not download torrent file: 404 https://private.invalid/token"])[0]
    audit = AuditLog(tmp_path / "audit", max_bytes=1024)

    path = audit.append([event])

    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert "private.invalid" not in path.read_text()
    assert len(path.read_text().splitlines()) == 1


def test_dry_run_scans_at_most_the_requested_number_of_log_files(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "upload-first.log").write_text("DARKPEERS: Could not download torrent file: 404\n")
    (logs / "upload-second.log").write_text("ULCX: Torrent file missing does not exist, cannot add to client\n")

    events = run_dry_run(logs, dry_run=True, max_logs=1, max_items=5)

    assert len(events) == 1


def test_cli_refuses_to_run_without_dry_run_flag(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "upload_pipeline_detector.py"

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(script), "--log-root", str(tmp_path), "--state-dir", str(tmp_path / "audit")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--dry-run" in result.stderr


def test_detector_ignores_unrelated_404_false_positive() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    assert detector.detect(["DIGITALCORE: duplicate lookup received 404"]) == []


def test_qbittorrent_summary_redacts_individual_torrents() -> None:
    summary = summarize_qbittorrent(
        [
            {"state": "stalledUP", "progress": 1.0, "content_path": "/private/a"},
            {"state": "downloading", "progress": 0.5, "content_path": ""},
        ]
    )

    assert summary == {
        "total": 2,
        "states": {"downloading": 1, "stalledUP": 1},
        "incomplete": 1,
        "payload_missing": 1,
    }


def test_qbittorrent_loader_uses_only_torrents_info_endpoint() -> None:
    requested: list[str] = []

    def fetch(url: str) -> list[dict[str, object]]:
        requested.append(url)
        return [{"state": "stalledUP", "progress": 1.0, "content_path": "/private"}]

    summary = load_qbittorrent_summary("http://localhost:8080/api/v2", fetch)

    assert requested == ["http://localhost:8080/api/v2/torrents/info"]
    assert summary["total"] == 1


def test_audit_log_rotates_without_deleting_prior_records(tmp_path: Path) -> None:
    event = UploadPipelineDetector(dry_run=True, max_items=5).detect(
        ["DIGITALCORE: Could not download torrent file: 404"]
    )[0]
    audit = AuditLog(tmp_path / "audit", max_bytes=1)

    audit.append([event])
    current = audit.append([event])

    assert current.name == "events.jsonl"
    assert len(list(current.parent.glob("events*.jsonl"))) == 2


def test_audit_log_refuses_to_wait_for_existing_writer(tmp_path: Path) -> None:
    event = UploadPipelineDetector(dry_run=True, max_items=5).detect(
        ["DIGITALCORE: Could not download torrent file: 404"]
    )[0]
    audit = AuditLog(tmp_path / "audit", max_bytes=1024)
    path = audit.append([])
    descriptor = os.open(path, os.O_WRONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError, match="locked"):
            audit.append([event])
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_qbittorrent_summary_marks_malformed_payload_unknown() -> None:
    summary = summarize_qbittorrent([{"state": "stalledUP", "progress": "bad", "content_path": "/private"}])

    assert summary == {"status": "unknown"}


def test_dry_run_recurses_into_upload_runtime_directories(tmp_path: Path) -> None:
    logs = tmp_path / "logs" / "nested"
    logs.mkdir(parents=True)
    (logs / "upload-nested.log").write_text("ZENITH: Could not download torrent file: 404\n")

    events = run_dry_run(tmp_path / "logs", dry_run=True, max_logs=5, max_items=5)

    assert len(events) == 1


def test_detector_accepts_tracker_labels_padded_before_colon() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    events = detector.detect(["DIGITALCORE  : Could not download torrent file: 404"])

    assert len(events) == 1
    assert events[0].to_record()["tracker"] == "DIGITALCORE"


def test_dry_run_reads_runtime_upload_underscore_logs(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "upload_123.log").write_text("YUSCENE: Could not download torrent file: 404\n")

    events = run_dry_run(logs, dry_run=True, max_logs=5, max_items=5)

    assert len(events) == 1


def test_detector_reads_tracker_label_from_upload_assistant_info_line() -> None:
    detector = UploadPipelineDetector(dry_run=True, max_items=5)

    events = detector.detect(["[2026-10-25 10:00:00] INFO: DIGITALCORE  : Could not download torrent file: 404"])

    assert len(events) == 1
    assert events[0].to_record()["tracker"] == "DIGITALCORE"


def test_dry_run_correlates_error_with_following_tracker_result(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "upload_123.log").write_text(
        "WARNING: Could not download torrent file: 404\n"
        "INFO: Torrent file /private/release.torrent does not exist, cannot add to client\n"
        "INFO: DIGITALCORE  : [link]\n"
    )

    events = run_dry_run(logs, dry_run=True, max_logs=5, max_items=5)

    assert [event.to_record()["tracker"] for event in events] == ["DIGITALCORE", "DIGITALCORE"]


def test_qbittorrent_loader_rejects_nonlocal_base_url() -> None:
    with pytest.raises(ValueError, match="local qBittorrent"):
        load_qbittorrent_summary("https://example.com/api/v2", lambda _url: [])


def test_dry_run_rejects_negative_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limits"):
        run_dry_run(tmp_path, dry_run=True, max_logs=-1, max_items=5)
