#!/usr/bin/env python3
"""Validate repository-wide CLI, uv, and coverage policies deterministically."""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

FORBIDDEN_TRACKED_PATHS: Final = (
    "requirements.txt",
    "requirements.lock",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "dangerfile.js",
    "bandit.yaml",
    ".prettierrc.json",
    ".prettierignore",
    ".coderabbit.yaml",
    ".pr_agent.toml",
)
FORBIDDEN_TRACKED_PREFIXES: Final = (
    "web_ui/",
    "src/delivery/http/",
)
PRODUCTION_ROOTS: Final = (Path("src"), Path("upload.py"), Path("config-generator.py"))


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    rule: str
    path: str
    detail: str


def _runtime_dependency_violations(data: dict[str, object]) -> list[PolicyViolation]:
    project = data.get("project", {})
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if isinstance(dependencies, list) and dependencies:
        return []
    return [PolicyViolation("UV-02", "pyproject.toml", "project.dependencies must contain runtime dependencies")]


def _tool_section(data: dict[str, object], name: str) -> dict[str, object]:
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        return {}
    section = tool.get(name, {})
    return section if isinstance(section, dict) else {}


def _failed_checks(checks: tuple[tuple[bool, str, str], ...]) -> list[PolicyViolation]:
    return [PolicyViolation(rule, "pyproject.toml", detail) for valid, rule, detail in checks if not valid]


def _coverage_policy_violations(data: dict[str, object]) -> list[PolicyViolation]:
    coverage = _tool_section(data, "coverage")
    report = coverage.get("report", {})
    run = coverage.get("run", {})
    report_dict = report if isinstance(report, dict) else {}
    run_dict = run if isinstance(run, dict) else {}
    checks = (
        (float(report_dict.get("fail_under", 0)) == 100.0, "COV-01", "tool.coverage.report.fail_under must equal 100"),
        (run_dict.get("source") == ["src"], "COV-02", "coverage source must be exactly ['src']"),
        (not run_dict.get("omit"), "COV-03", "coverage omit rules are forbidden"),
    )
    return _failed_checks(checks)


def _dev_dependencies(data: dict[str, object]) -> list[object]:
    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return []
    value = groups.get("dev", [])
    return value if isinstance(value, list) else []


def _has_pinned_radon(data: dict[str, object]) -> bool:
    return any(str(item).startswith("radon==") for item in _dev_dependencies(data))


def _quality_section(data: dict[str, object]) -> dict[str, object]:
    upload = _tool_section(data, "upload-assistant")
    value = upload.get("quality", {})
    return value if isinstance(value, dict) else {}


def _radon_no_assert(data: dict[str, object]) -> bool:
    return _tool_section(data, "radon").get("no_assert") is True


def _quality_max_is_a(data: dict[str, object]) -> bool:
    return int(_quality_section(data).get("max_cyclomatic_complexity", 0)) == 5


def _has_complexity_paths(data: dict[str, object]) -> bool:
    value = _quality_section(data).get("complexity_paths")
    return bool(value) if isinstance(value, list) else False


def _complexity_policy_violations(data: dict[str, object]) -> list[PolicyViolation]:
    checks = (
        (_has_pinned_radon(data), "CC-01", "Radon must be pinned in dependency-groups.dev"),
        (_radon_no_assert(data), "CC-06", "tool.radon.no_assert must be true for semantic block complexity"),
        (_quality_max_is_a(data), "CC-02", "maximum cyclomatic complexity must be 5 (Radon rank A)"),
        (_has_complexity_paths(data), "CC-03", "complexity_paths must define repository Python roots"),
    )
    return _failed_checks(checks)


def _required_quality_files(root: Path) -> list[PolicyViolation]:
    required = (
        ("uv.lock", "UV-03", "missing uv lockfile"),
        ("scripts/check_radon_complexity.py", "CC-04", "mandatory Radon complexity enforcement is missing"),
        (".githooks/pre-commit", "CC-05", "mandatory Radon complexity enforcement is missing"),
    )
    return [PolicyViolation(rule, path, detail) for path, rule, detail in required if not (root / path).is_file()]


def _validate_pyproject(root: Path) -> list[PolicyViolation]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return [PolicyViolation("UV-01", "pyproject.toml", "missing project manifest")]
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        *_runtime_dependency_violations(data),
        *_coverage_policy_violations(data),
        *_complexity_policy_violations(data),
        *_required_quality_files(root),
    ]


def _production_python_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for item in PRODUCTION_ROOTS:
        resolved = root / item
        paths.extend(resolved.rglob("*.py") if resolved.is_dir() else ([resolved] if resolved.is_file() else []))
    return sorted(paths)


def _coverage_source_violation(path: Path, root: Path) -> PolicyViolation | None:
    text = path.read_text(encoding="utf-8")
    if "pragma: no cover" not in text and "coverage: ignore" not in text:
        return None
    return PolicyViolation("COV-04", str(path.relative_to(root)), "production coverage exclusions are forbidden")


def _validate_sources(root: Path) -> list[PolicyViolation]:
    return [violation for path in _production_python_paths(root) if (violation := _coverage_source_violation(path, root)) is not None]


def _obsolete_path_violations(root: Path) -> list[PolicyViolation]:
    return [PolicyViolation("REPO-01", path, "obsolete tool or non-uv dependency file exists") for path in FORBIDDEN_TRACKED_PATHS if (root / path).exists()]


def _frontend_paths(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    return files or [directory]


def _frontend_path_violations(root: Path) -> list[PolicyViolation]:
    paths = [path for prefix in FORBIDDEN_TRACKED_PREFIXES for path in _frontend_paths(root / prefix)]
    return [PolicyViolation("CLI-01", str(path.relative_to(root)), "frontend/HTTP delivery is forbidden in the CLI-only project") for path in paths]


def scan(root: Path = Path()) -> list[PolicyViolation]:
    resolved = root.resolve()
    return [
        *_obsolete_path_violations(resolved),
        *_frontend_path_violations(resolved),
        *_validate_pyproject(resolved),
        *_validate_sources(resolved),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args(argv)
    violations = scan(args.root)
    if not violations:
        print("Repository policy: PASS")
        return 0
    print(f"Repository policy: FAIL ({len(violations)} violations)")
    for item in violations:
        print(f"{item.path}: {item.rule}: {item.detail}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
