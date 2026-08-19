#!/usr/bin/env python3
"""Generate deterministic architecture, Radon complexity, test, and coverage metrics."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

from radon.complexity import cc_rank, cc_visit
from radon.visitors import Class as RadonClass

try:
    from scripts.check_masa_architecture import LAYERS, scan
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from check_masa_architecture import LAYERS, scan


@dataclass(frozen=True, slots=True)
class LayerMetrics:
    modules: int = 0
    physical_lines: int = 0
    statements: int = 0
    functions: int = 0
    classes: int = 0
    average_function_complexity: float = 0.0
    maximum_function_complexity: int = 0
    rank_a_functions: int = 0
    rank_b_or_worse_functions: int = 0


@dataclass(frozen=True, slots=True)
class CoverageMetrics:
    statements: int
    covered_lines: int
    missing_lines: int
    percent_covered: float


def _layer(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root)
    return relative.parts[0] if relative.parts[0] in LAYERS else "root"


def _radon_function_complexities(source: str) -> list[int]:
    return [int(block.complexity) for block in cc_visit(source, no_assert=True) if not isinstance(block, RadonClass)]


def _empty_layer_values() -> dict[str, Any]:
    return {
        "modules": 0,
        "physical_lines": 0,
        "statements": 0,
        "functions": 0,
        "classes": 0,
        "complexities": [],
    }


def _modules_for_node(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def _src_import_targets(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        yield from (module for module in _modules_for_node(node) if module.startswith("src."))


def _target_layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else "root"


def _update_layer_values(values: dict[str, Any], source: str, tree: ast.AST) -> None:
    values["modules"] += 1
    values["physical_lines"] += len(source.splitlines())
    values["statements"] += sum(isinstance(node, ast.stmt) for node in ast.walk(tree))
    values["functions"] += sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
    values["classes"] += sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
    values["complexities"].extend(_radon_function_complexities(source))


def _collect_project_data(source_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Counter[str]]]:
    raw: dict[str, dict[str, Any]] = defaultdict(_empty_layer_values)
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    for path in sorted(source_root.rglob("*.py")):
        owner = _layer(path, source_root)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        _update_layer_values(raw[owner], source, tree)
        matrix[owner].update(_target_layer(module) for module in _src_import_targets(tree))
    return raw, matrix


def _finalize_layer(values: dict[str, Any]) -> LayerMetrics:
    complexities = list(values["complexities"])
    return LayerMetrics(
        modules=int(values["modules"]),
        physical_lines=int(values["physical_lines"]),
        statements=int(values["statements"]),
        functions=int(values["functions"]),
        classes=int(values["classes"]),
        average_function_complexity=round(fmean(complexities), 2) if complexities else 0.0,
        maximum_function_complexity=max(complexities, default=0),
        rank_a_functions=sum(value <= 5 for value in complexities),
        rank_b_or_worse_functions=sum(value > 5 for value in complexities),
    )


def _project_metrics(source_root: Path) -> tuple[dict[str, LayerMetrics], dict[str, dict[str, int]]]:
    raw, matrix = _collect_project_data(source_root)
    layers = {name: _finalize_layer(values) for name, values in sorted(raw.items())}
    dependency_matrix = {owner: dict(sorted(targets.items())) for owner, targets in sorted(matrix.items())}
    return layers, dependency_matrix


def _test_metrics(tests_root: Path) -> dict[str, int]:
    paths = sorted(tests_root.rglob("test_*.py"))
    count = sum(_count_tests(path) for path in paths)
    return {"files": len(paths), "test_functions": count}


def _count_tests(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") for node in ast.walk(tree))


def _coverage_metrics(path: Path | None) -> CoverageMetrics | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    totals = payload.get("totals", {})
    required = ("num_statements", "covered_lines", "missing_lines", "percent_covered")
    if not all(key in totals for key in required):
        raise ValueError(f"Coverage JSON at {path} does not contain expected totals")
    return CoverageMetrics(
        statements=int(totals["num_statements"]),
        covered_lines=int(totals["covered_lines"]),
        missing_lines=int(totals["missing_lines"]),
        percent_covered=round(float(totals["percent_covered"]), 2),
    )


def _complexity_summary(layers: dict[str, LayerMetrics]) -> dict[str, Any]:
    maxima = [metrics.maximum_function_complexity for metrics in layers.values() if metrics.maximum_function_complexity]
    maximum = max(maxima, default=0)
    rank_b_or_worse = sum(metrics.rank_b_or_worse_functions for metrics in layers.values())
    return {
        "metric": "Radon cyclomatic complexity",
        "maximum_allowed": 5,
        "required_rank": "A",
        "maximum_observed": maximum,
        "maximum_observed_rank": cc_rank(max(maximum, 1)),
        "rank_b_or_worse_functions": rank_b_or_worse,
        "passes_rank_a_gate": rank_b_or_worse == 0,
    }


def _architecture_summary(source_root: Path) -> dict[str, Any]:
    violations = scan(source_root)
    by_edge = Counter(f"{item.owner}->{item.target}" for item in violations)
    return {"violation_count": len(violations), "violations_by_edge": dict(sorted(by_edge.items()))}


def build_report(source_root: Path, tests_root: Path, coverage_json: Path | None) -> dict[str, Any]:
    layers, matrix = _project_metrics(source_root)
    coverage = _coverage_metrics(coverage_json)
    return {
        "schema_version": 2,
        "source_root": str(source_root),
        "layers": {name: asdict(metrics) for name, metrics in layers.items()},
        "dependency_matrix": matrix,
        "complexity": _complexity_summary(layers),
        "architecture": _architecture_summary(source_root),
        "tests": _test_metrics(tests_root),
        "coverage": asdict(coverage) if coverage else None,
    }


def _quality_gate_lines(report: dict[str, Any]) -> list[str]:
    coverage = report["coverage"]
    coverage_value = f"{coverage['percent_covered']:.2f}%" if coverage else "not measured"
    return [
        "# Project quality report",
        "",
        "Generated deterministically from the checked-out source tree.",
        "",
        "## Quality gates",
        "",
        "| Gate | Result |",
        "|---|---:|",
        f"| MASA boundary violations | {report['architecture']['violation_count']} |",
        f"| Radon blocks above rank A | {report['complexity']['rank_b_or_worse_functions']} |",
        f"| Maximum Radon complexity | {report['complexity']['maximum_observed']} ({report['complexity']['maximum_observed_rank']}) |",
        f"| Python line coverage | {coverage_value} |",
        f"| Test functions | {report['tests']['test_functions']} |",
    ]


def _layer_lines(report: dict[str, Any]) -> list[str]:
    lines = [
        "",
        "## Layer metrics",
        "",
        "| Layer | Modules | Lines | AST statements | Functions | Classes | Avg Radon CC | Max Radon CC | Rank A | Rank B+ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_layer_row(name, metrics) for name, metrics in report["layers"].items())
    return lines


def _layer_row(name: str, metrics: dict[str, Any]) -> str:
    return (
        f"| {name} | {metrics['modules']} | {metrics['physical_lines']} | {metrics['statements']} | "
        f"{metrics['functions']} | {metrics['classes']} | {metrics['average_function_complexity']:.2f} | "
        f"{metrics['maximum_function_complexity']} | {metrics['rank_a_functions']} | {metrics['rank_b_or_worse_functions']} |"
    )


def _dependency_lines(report: dict[str, Any]) -> list[str]:
    matrix = report["dependency_matrix"]
    targets = sorted({target for values in matrix.values() for target in values})
    lines = [
        "",
        "## Dependency matrix",
        "",
        "Each value is a static `src.*` import count.",
        "",
        "| From / To | " + " | ".join(targets) + " |",
        "|---|" + "---:|" * len(targets),
    ]
    lines.extend(_dependency_row(owner, values, targets) for owner, values in matrix.items())
    return [*lines, ""]


def _dependency_row(owner: str, values: dict[str, int], targets: list[str]) -> str:
    return "| " + owner + " | " + " | ".join(str(values.get(target, 0)) for target in targets) + " |"


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join([*_quality_gate_lines(report), *_layer_lines(report), *_dependency_lines(report)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("src"))
    parser.add_argument("--tests-root", type=Path, default=Path("tests"))
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, default=Path("artifacts/quality-report.json"))
    parser.add_argument("--markdown", type=Path, default=Path("artifacts/quality-report.md"))
    args = parser.parse_args()

    report = build_report(args.source_root, args.tests_root, args.coverage_json)
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
