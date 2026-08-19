#!/usr/bin/env python3
"""Fail when any Python block exceeds Radon rank A (cyclomatic complexity 5)."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radon.complexity import cc_rank, cc_visit
from radon.visitors import Class as RadonClass

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _diff_header_path(line: str) -> tuple[bool, Path | None]:
    if not line.startswith("+++ "):
        return False, None
    value = line[4:].strip()
    return True, None if value == "/dev/null" else Path(value.removeprefix("b/"))


def _hunk_lines(line: str) -> range:
    match = _HUNK_RE.match(line)
    if match is None:
        return range(0)
    start = int(match.group(1))
    count = int(match.group(2) or "1")
    return range(start, start + max(count, 0))


def _parse_changed_lines(diff_text: str) -> dict[Path, set[int]]:
    changed: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in diff_text.splitlines():
        is_header, header_path = _diff_header_path(line)
        if is_header:
            current = header_path
            if current is not None:
                changed.setdefault(current, set())
        elif current is not None:
            changed[current].update(_hunk_lines(line))
    return changed


def _staged_changed_lines(root: Path) -> dict[Path, set[int]]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required for --staged complexity checks")
    result = subprocess.run(  # noqa: S603 -- resolved git executable; arguments are constant.
        [git, "diff", "--cached", "--unified=0", "--no-color", "--", "*.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return _parse_changed_lines(result.stdout)


def _block_touches_lines(block: Any, lines: set[int]) -> bool:
    start = int(block.lineno)
    end = int(getattr(block, "endline", start))
    return any(start <= line <= end for line in lines)


@dataclass(frozen=True, slots=True)
class ComplexityViolation:
    path: Path
    line: int
    column: int
    name: str
    complexity: int
    rank: str


def _quality_config(root: Path) -> tuple[int, tuple[str, ...]]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    quality = data.get("tool", {}).get("upload-assistant", {}).get("quality", {})
    maximum = int(quality.get("max_cyclomatic_complexity", 5))
    configured_paths = quality.get("complexity_paths", ["src", "upload.py", "config-generator.py", "scripts", "tests"])
    if not isinstance(configured_paths, list) or not all(isinstance(item, str) for item in configured_paths):
        raise ValueError("tool.upload-assistant.quality.complexity_paths must be a list of strings")
    return maximum, tuple(configured_paths)


def _files_for_path(path: Path) -> set[Path]:
    if path.is_dir():
        return set(path.rglob("*.py"))
    if path.is_file() and path.suffix == ".py":
        return {path}
    return set()


def _python_files(root: Path, values: Iterable[str]) -> list[Path]:
    files: set[Path] = set()
    for value in values:
        files.update(_files_for_path((root / value).resolve()))
    return sorted(files)


def _nested_blocks(block: Any) -> Iterable[Any]:
    yield block
    for child in getattr(block, "closures", ()) or ():
        yield from _nested_blocks(child)


def _radon_no_assert(root: Path) -> bool:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return bool(data.get("tool", {}).get("radon", {}).get("no_assert", False))


def _violation_for_block(block: Any, relative_path: Path, maximum: int) -> ComplexityViolation | None:
    if isinstance(block, RadonClass):
        return None
    complexity = int(block.complexity)
    if complexity <= maximum:
        return None
    return ComplexityViolation(
        path=relative_path,
        line=int(block.lineno),
        column=int(block.col_offset) + 1,
        name=str(block.name),
        complexity=complexity,
        rank=cc_rank(complexity),
    )


def _filtered_violation(
    block: Any,
    relative_path: Path,
    maximum: int,
    changed_lines: set[int] | None,
) -> ComplexityViolation | None:
    if changed_lines is not None and not _block_touches_lines(block, changed_lines):
        return None
    return _violation_for_block(block, relative_path, maximum)


def _violations_for_file(
    path: Path,
    root: Path,
    maximum: int,
    *,
    no_assert: bool,
    changed_lines: set[int] | None = None,
) -> list[ComplexityViolation]:
    relative_path = path.relative_to(root)
    source = path.read_text(encoding="utf-8")
    blocks = (block for top_level in cc_visit(source, no_assert=no_assert) for block in _nested_blocks(top_level))
    return [violation for block in blocks if (violation := _filtered_violation(block, relative_path, maximum, changed_lines)) is not None]


def scan(
    root: Path,
    paths: Iterable[str],
    maximum: int,
    *,
    no_assert: bool = False,
    changed_lines: dict[Path, set[int]] | None = None,
) -> list[ComplexityViolation]:
    violations: list[ComplexityViolation] = []
    for path in _python_files(root, paths):
        relative_path = path.relative_to(root)
        if changed_lines is not None and relative_path not in changed_lines:
            continue
        lines = None if changed_lines is None else changed_lines[relative_path]
        violations.extend(_violations_for_file(path, root, maximum, no_assert=no_assert, changed_lines=lines))
    return sorted(violations, key=lambda item: (str(item.path), item.line, item.column, item.name))


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional Python files/directories. Defaults to pyproject complexity_paths.")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--concise", action="store_true", help="Emit Reviewdog-friendly path:line:column diagnostics.")
    parser.add_argument("--staged", action="store_true", help="Check only blocks touched by staged Python additions/modifications.")
    return parser


def _print_violations(violations: list[ComplexityViolation], maximum: int, *, concise: bool) -> None:
    if not concise:
        print(f"Radon complexity: FAIL ({len(violations)} blocks exceed rank A / CC {maximum})")
    for item in violations:
        message = f"CC-A001 {item.name} has cyclomatic complexity {item.complexity} (rank {item.rank}); maximum allowed is rank A (CC <= {maximum})"
        print(f"{item.path}:{item.line}:{item.column}: {message}")


def _run_check(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    maximum, configured_paths = _quality_config(root)
    paths = tuple(args.paths) or configured_paths
    changed_lines = _staged_changed_lines(root) if args.staged else None
    violations = scan(root, paths, maximum, no_assert=_radon_no_assert(root), changed_lines=changed_lines)
    if not violations:
        print(f"Radon complexity: PASS (all blocks rank A, CC <= {maximum})")
        return 0
    _print_violations(violations, maximum, concise=args.concise)
    return 1


def main(argv: list[str] | None = None) -> int:
    return _run_check(_argument_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
