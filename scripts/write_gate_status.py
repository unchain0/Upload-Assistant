#!/usr/bin/env python3
"""Persist a machine-readable, secret-free summary of local quality evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_GATES = (
    "compileall",
    "ruff",
    "masa",
    "diff-check",
    "focused",
    "pytest-a-f",
    "pytest-g-m",
    "pytest-n-s",
    "pytest-t-z",
    "typecheck",
    "coverage-100",
)


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required to write gate status")
    return subprocess.check_output([git, *args], text=True).strip()  # noqa: S603 -- arguments are fixed by internal callers


def _coverage(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    totals = json.loads(path.read_text(encoding="utf-8")).get("totals", {})
    return {
        "statements": int(totals.get("num_statements", 0)),
        "covered_lines": int(totals.get("covered_lines", 0)),
        "missing_lines": int(totals.get("missing_lines", 0)),
        "percent_covered": round(float(totals.get("percent_covered", 0.0)), 2),
    }


def _architecture(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"violation_count": int(payload.get("violation_count", -1))}


def _passed_gates(artifacts: Path) -> list[str]:
    gates_dir = artifacts / "gates"
    if not gates_dir.is_dir():
        return []
    return sorted(path.name.removesuffix(".pass") for path in gates_dir.glob("*.pass"))


def _merge_allowed(missing: list[str], coverage: dict[str, Any] | None) -> bool:
    return bool(not missing and coverage is not None and coverage["percent_covered"] == 100.0)


def build_status(artifacts: Path) -> dict[str, Any]:
    passed = _passed_gates(artifacts)
    missing = [name for name in EXPECTED_GATES if name not in passed]
    coverage = _coverage(artifacts / "coverage.json")
    architecture = _architecture(artifacts / "masa-architecture.json")
    status_lines = _git("status", "--short").splitlines()
    return {
        "schema_version": 1,
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "base_candidate": "development",
        "working_tree": {"changed_entries": len(status_lines), "clean": not status_lines},
        "gates": {"expected": list(EXPECTED_GATES), "passed": passed, "missing_or_failed": missing},
        "architecture": architecture,
        "coverage": coverage,
        "merge_allowed": _merge_allowed(missing, coverage),
    }


def _coverage_line(coverage: dict[str, Any] | None) -> str:
    if coverage is None:
        return "- Coverage: not measured"
    return f"- Coverage: {coverage['percent_covered']:.2f}% ({coverage['missing_lines']} missing lines)"


def _architecture_line(architecture: dict[str, Any] | None) -> str:
    value = architecture["violation_count"] if architecture else "not measured"
    return f"- MASA violations: {value}"


def _gate_lines(title: str, values: list[str]) -> list[str]:
    items = [f"- {name}" for name in values] or ["- None"]
    return ["", f"## {title}", "", *items]


def _markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Local gate status",
        "",
        f"- Branch: `{status['branch']}`",
        f"- HEAD: `{status['head']}`",
        _architecture_line(status["architecture"]),
        _coverage_line(status["coverage"]),
        f"- Merge allowed: **{'yes' if status['merge_allowed'] else 'no'}**",
        *_gate_lines("Passed gates", status["gates"]["passed"]),
        *_gate_lines("Missing or failed gates", status["gates"]["missing_or_failed"]),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    status = build_status(args.artifacts)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    (args.artifacts / "gate-status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.artifacts / "gate-status.md").write_text(_markdown(status), encoding="utf-8")
    print(json.dumps(status, sort_keys=True))
    return 0 if status["merge_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
