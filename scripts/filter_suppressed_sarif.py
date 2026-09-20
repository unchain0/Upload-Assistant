#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import cast


def _is_in_source_suppressed(result: Mapping[str, object]) -> bool:
    suppressions = result.get("suppressions", [])
    return isinstance(suppressions, list) and any(
        isinstance(item, dict) and item.get("kind") == "inSource"
        for item in suppressions
    )


def _filtered_results(value: object) -> object:
    if not isinstance(value, list):
        return value
    return [
        result
        for result in value
        if not isinstance(result, dict)
        or not _is_in_source_suppressed(cast(dict[str, object], result))
    ]


def _filtered_run(value: object) -> object:
    if not isinstance(value, dict):
        return value
    run = cast(dict[str, object], value)
    filtered = dict(run)
    filtered["results"] = _filtered_results(run.get("results", []))
    return filtered


def filter_suppressed_results(
    payload: Mapping[str, object],
) -> dict[str, object]:
    filtered = dict(payload)
    raw_runs = payload.get("runs", [])
    if isinstance(raw_runs, list):
        filtered["runs"] = [_filtered_run(run) for run in raw_runs]
    return filtered


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("SARIF output must be a JSON object")
    print(
        json.dumps(filter_suppressed_results(cast(dict[str, object], payload)))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
