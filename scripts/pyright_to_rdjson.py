#!/usr/bin/env python3
"""Convert Pyright JSON diagnostics to Reviewdog RDJSONL."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

_SEVERITY = {
    "error": "ERROR",
    "warning": "WARNING",
    "information": "INFO",
}


def _relative_path(value: str) -> str:
    path = Path(value)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return os.path.relpath(path, Path.cwd()).replace(os.sep, "/")


def _position(raw: Mapping[str, object]) -> dict[str, int]:
    # Pyright is zero-based; Reviewdog's RDJSON positions are one-based.
    line = raw.get("line", 0)
    column = raw.get("character", 0)
    return {
        "line": (line if isinstance(line, int) else 0) + 1,
        "column": (column if isinstance(column, int) else 0) + 1,
    }


def convert(payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_diagnostics = payload.get("generalDiagnostics", [])
    if not isinstance(raw_diagnostics, list):
        return []

    diagnostics: list[dict[str, object]] = []
    diagnostic_items = cast(list[object], raw_diagnostics)
    for raw_item in diagnostic_items:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)

        file_value = item.get("file")
        message = item.get("message")
        raw_range_value = item.get("range")
        if not isinstance(file_value, str) or not isinstance(message, str) or not isinstance(raw_range_value, dict):
            continue
        raw_range = cast(dict[str, object], raw_range_value)

        start_value = raw_range.get("start")
        end_value = raw_range.get("end")
        if not isinstance(start_value, dict):
            continue
        start = cast(dict[str, object], start_value)

        message_text = message
        rule = item.get("rule")
        if isinstance(rule, str) and rule:
            message_text = f"[{rule}] {message_text}"

        reviewdog_range: dict[str, object] = {"start": _position(start)}
        if isinstance(end_value, dict):
            reviewdog_range["end"] = _position(cast(dict[str, object], end_value))

        severity_value = item.get("severity", "warning")
        severity_key = severity_value.lower() if isinstance(severity_value, str) else "warning"
        severity = _SEVERITY.get(severity_key, "WARNING")
        diagnostics.append(
            {
                "message": message_text,
                "location": {
                    "path": _relative_path(file_value),
                    "range": reviewdog_range,
                },
                "severity": severity,
            }
        )
    return diagnostics


def main() -> int:
    raw_payload = json.load(sys.stdin)
    if not isinstance(raw_payload, dict):
        raise TypeError("Pyright output must be a JSON object")
    payload = cast(dict[str, object], raw_payload)
    for diagnostic in convert(payload):
        print(json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
