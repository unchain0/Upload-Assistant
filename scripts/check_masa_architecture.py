#!/usr/bin/env python3
"""Deterministic MASA import-boundary validator.

The checker parses imports without importing project modules, so it is safe to
run in CI and against partially configured development checkouts.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

LAYERS: Final = frozenset({"domain_models", "engines", "services", "integrations", "delivery"})
ALLOWED_IMPORTS: Final[dict[str, frozenset[str]]] = {
    "domain_models": frozenset({"domain_models"}),
    "engines": frozenset({"domain_models", "engines"}),
    # Services may coordinate explicit adapter dependencies; concrete wiring
    # remains visible in the composition root and adapter types must not leak
    # through public service contracts.
    "services": frozenset({"domain_models", "engines", "services", "integrations"}),
    "integrations": frozenset({"domain_models", "integrations"}),
    "delivery": frozenset({"domain_models", "services", "delivery"}),
    # Only the composition root and package root may connect concrete layers.
    "root": frozenset({"domain_models", "engines", "services", "integrations", "delivery", "root"}),
}
COMPOSITION_ROOTS: Final = frozenset({Path("src/bootstrap.py")})


@dataclass(frozen=True, slots=True)
class Violation:
    owner: str
    target: str
    path: str
    line: int
    module: str
    reason: str = "forbidden layer dependency"


def _owner_for(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root)
    if path in COMPOSITION_ROOTS or relative == Path("bootstrap.py"):
        return "root"
    first = relative.parts[0]
    return first if first in LAYERS else "root"


def _target_for(module: str) -> str | None:
    if module == "src":
        return "root"
    if not module.startswith("src."):
        return None
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else "root"


def _absolute_relative_module(node: ast.ImportFrom, path: Path, source_root: Path) -> str | None:
    if node.level == 0:
        return node.module
    relative = path.relative_to(source_root).with_suffix("")
    package_parts = ["src", *relative.parts[:-1]]
    trim = node.level - 1
    if trim:
        if trim > len(package_parts):
            return None
        package_parts = package_parts[:-trim]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _compare_values(node: ast.AST) -> list[ast.AST] | None:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return None
    compare = node.test
    if len(compare.ops) != 1 or not isinstance(compare.ops[0], ast.Eq):
        return None
    return [compare.left, *compare.comparators]


def _has_name(values: list[ast.AST], name: str) -> bool:
    return any(isinstance(value, ast.Name) and value.id == name for value in values)


def _has_constant(values: list[ast.AST], expected: object) -> bool:
    return any(isinstance(value, ast.Constant) and value.value == expected for value in values)


def _is_main_guard(node: ast.AST) -> bool:
    values = _compare_values(node)
    return bool(values and _has_name(values, "__name__") and _has_constant(values, "__main__"))


def _inside_main_guard(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if _is_main_guard(current):
            return True
        current = parents.get(current)
    return False


def _raised_termination(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Raise):
        return None
    exception = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return "SystemExit" if isinstance(exception, ast.Name) and exception.id == "SystemExit" else None


def _named_termination(function: ast.AST) -> str | None:
    if isinstance(function, ast.Name) and function.id in {"exit", "quit"}:
        return function.id
    return None


def _attribute_termination(function: ast.AST) -> str | None:
    if not isinstance(function, ast.Attribute) or not isinstance(function.value, ast.Name):
        return None
    key = (function.value.id, function.attr)
    return {("sys", "exit"): "sys.exit", ("os", "_exit"): "os._exit"}.get(key)


def _termination_name(node: ast.AST) -> str | None:
    raised = _raised_termination(node)
    if raised or not isinstance(node, ast.Call):
        return raised
    return _named_termination(node.func) or _attribute_termination(node.func)


def _syntax_violation(path: Path, owner: str, error: SyntaxError) -> Violation:
    return Violation(
        owner=owner,
        target="syntax",
        path=str(path),
        line=error.lineno or 0,
        module="",
        reason=f"syntax error: {error.msg}",
    )


def _process_violation(node: ast.AST, owner: str, path: Path, parents: dict[ast.AST, ast.AST]) -> Violation | None:
    termination = _termination_name(node)
    if not termination or owner in {"delivery", "root"} or _inside_main_guard(node, parents):
        return None
    return Violation(
        owner=owner,
        target="process",
        path=str(path),
        line=node.lineno,
        module=termination,
        reason="process termination must be decided by delivery or a composition root",
    )


def _import_modules(node: ast.AST, path: Path, source_root: Path) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = _absolute_relative_module(node, path, source_root)
        return [module] if module else []
    return []


def _import_violations(node: ast.AST, owner: str, path: Path, source_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for module in _import_modules(node, path, source_root):
        target = _target_for(module)
        if target is None or target in ALLOWED_IMPORTS[owner]:
            continue
        violations.append(Violation(owner=owner, target=target, path=str(path), line=node.lineno, module=module))
    return violations


def _scan_tree(tree: ast.AST, owner: str, path: Path, source_root: Path) -> list[Violation]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    violations: list[Violation] = []
    for node in ast.walk(tree):
        process = _process_violation(node, owner, path, parents)
        if process is not None:
            violations.append(process)
        violations.extend(_import_violations(node, owner, path, source_root))
    return violations


def _scan_file(path: Path, source_root: Path) -> list[Violation]:
    owner = _owner_for(path, source_root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as error:
        return [_syntax_violation(path, owner, error)]
    return _scan_tree(tree, owner, path, source_root)


def scan(source_root: Path = Path("src")) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(source_root.rglob("*.py")):
        violations.extend(_scan_file(path, source_root))
    return violations


def _render_text(violations: list[Violation]) -> str:
    if not violations:
        return "MASA architecture: PASS (0 boundary violations)"
    groups = Counter((item.owner, item.target) for item in violations)
    lines = [f"MASA architecture: FAIL ({len(violations)} boundary violations)"]
    lines.extend(f"  {owner} -> {target}: {count}" for (owner, target), count in sorted(groups.items()))
    lines.append("")
    lines.extend(f"{item.path}:{item.line}: {item.owner} -> {item.target}: {item.module} ({item.reason})" for item in violations)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("src"))
    parser.add_argument("--json", dest="json_path", type=Path)
    parser.add_argument("--report-only", action="store_true", help="write the report without returning a failing status")
    args = parser.parse_args(argv)

    violations = scan(args.source_root)
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_root": str(args.source_root),
                    "violation_count": len(violations),
                    "violations": [asdict(item) for item in violations],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(_render_text(violations))
    return 0 if args.report_only or not violations else 1


if __name__ == "__main__":
    sys.exit(main())
