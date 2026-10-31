from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.services.upload_pipeline_detector import (
    AuditLog,
    load_qbittorrent_summary,
    run_dry_run,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only upload pipeline detector")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--max-logs", type=int, default=200)
    parser.add_argument("--max-items", type=int, default=100)
    parser.add_argument("--max-audit-bytes", type=int, default=1_000_000)
    parser.add_argument("--qbit-base-url")
    args = parser.parse_args()
    if not args.dry_run:
        parser.error("--dry-run is required")

    events = run_dry_run(
        args.log_root,
        dry_run=True,
        max_logs=args.max_logs,
        max_items=args.max_items,
    )
    AuditLog(args.state_dir, max_bytes=args.max_audit_bytes).append(events)
    qbit: dict[str, object] = {"status": "not_requested"}
    if args.qbit_base_url:
        try:
            qbit = load_qbittorrent_summary(args.qbit_base_url, _fetch_qbit_torrents)
        except (OSError, ValueError, json.JSONDecodeError):
            qbit = {"status": "unknown"}
    classification = (
        "unknown"
        if qbit.get("status") == "unknown"
        else "TECHNICAL_UPLOAD_PIPELINE"
        if events
        else "NO_CORRELATED_TECHNICAL_FAILURE"
    )
    print(json.dumps({"classification": classification, "events": len(events), "qbit": qbit}))
    return 0


def _fetch_qbit_torrents(url: str) -> list[dict[str, object]]:
    with urlopen(url, timeout=5) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("unexpected qBittorrent response")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
