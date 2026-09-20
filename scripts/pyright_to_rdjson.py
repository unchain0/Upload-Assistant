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


def _diagnostic_message(item: Mapping[str, object], message: str) -> str:
    rule = item.get("rule")
    return f"[{rule}] {message}" if isinstance(rule, str) and rule else message


def _reviewdog_range(
    raw_range: Mapping[str, object],
) -> dict[str, object] | None:
    start_value = raw_range.get("start")
    if not isinstance(start_value, dict):
        return None
    reviewdog_range: dict[str, object] = {"start": _position(start_value)}
    end_value = raw_range.get("end")
    if isinstance(end_value, dict):
        reviewdog_range["end"] = _position(end_value)
    return reviewdog_range


def _severity(item: Mapping[str, object]) -> str:
    value = item.get("severity", "warning")
    key = value.lower() if isinstance(value, str) else "warning"
    return _SEVERITY.get(key, "WARNING")


def _diagnostic_fields(
    item: Mapping[str, object],
) -> tuple[str, str, dict[str, object]] | None:
    file_value = item.get("file")
    message = item.get("message")
    raw_range = item.get("range")
    if (
        isinstance(file_value, str)
        and isinstance(message, str)
        and isinstance(raw_range, dict)
    ):
        return file_value, message, raw_range
    return None


def _convert_item(raw_item: object) -> dict[str, object] | None:
    if not isinstance(raw_item, dict):
        return None
    item = cast(dict[str, object], raw_item)
    fields = _diagnostic_fields(item)
    if fields is None:
        return None
    file_value, message, raw_range = fields
    reviewdog_range = _reviewdog_range(raw_range)
    if reviewdog_range is None:
        return None
    return {
        "message": _diagnostic_message(item, message),
        "location": {
            "path": _relative_path(file_value),
            "range": reviewdog_range,
        },
        "severity": _severity(item),
    }


def convert(payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_diagnostics = payload.get("generalDiagnostics", [])
    if not isinstance(raw_diagnostics, list):
        return []
    converted = (
        _convert_item(raw_item)
        for raw_item in cast(list[object], raw_diagnostics)
    )
    return [item for item in converted if item is not None]


def main() -> int:
    raw_payload = json.load(sys.stdin)
    if not isinstance(raw_payload, dict):
        raise TypeError("Pyright output must be a JSON object")
    payload = cast(dict[str, object], raw_payload)
    for diagnostic in convert(payload):
        print(
            json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":"))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
