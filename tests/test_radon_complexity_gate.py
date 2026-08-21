from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_radon_complexity


def _write_pyproject(root: Path, *, maximum: int = 5, paths: str = '["src"]') -> None:
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.upload-assistant.quality]",
                f"max_cyclomatic_complexity = {maximum}",
                f"complexity_paths = {paths}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_quality_config_and_python_file_discovery(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    simple = src / "simple.py"
    simple.write_text("def value():\n    return 1\n", encoding="utf-8")
    (src / "ignore.txt").write_text("not python", encoding="utf-8")

    maximum, paths = check_radon_complexity._quality_config(tmp_path)
    assert maximum == 5
    assert paths == ("src",)
    assert check_radon_complexity._python_files(tmp_path, paths) == [simple]


def test_quality_config_rejects_invalid_paths(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, paths='"src"')
    with pytest.raises(ValueError, match="list of strings"):
        check_radon_complexity._quality_config(tmp_path)


def test_scan_accepts_rank_a_and_rejects_rank_b(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    simple = src / "simple.py"
    simple.write_text("def simple(value):\n    if value:\n        return 1\n    return 0\n", encoding="utf-8")
    complex_file = src / "complex.py"
    complex_file.write_text(
        "\n".join(
            [
                "def complex_value(a, b, c, d, e, f):",
                "    if a:",
                "        return 1",
                "    if b:",
                "        return 2",
                "    if c:",
                "        return 3",
                "    if d:",
                "        return 4",
                "    if e:",
                "        return 5",
                "    if f:",
                "        return 6",
                "    return 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    violations = check_radon_complexity.scan(tmp_path, ("src",), 5)
    assert len(violations) == 1
    violation = violations[0]
    assert violation.path == Path("src/complex.py")
    assert violation.name == "complex_value"
    assert violation.complexity == 7
    assert violation.rank == "B"
    assert check_radon_complexity.scan(tmp_path, (str(simple.relative_to(tmp_path)),), 5) == []


def test_main_returns_failure_and_concise_diagnostic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_pyproject(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "complex.py").write_text(
        "def f(a,b,c,d,e,f):\n    return bool(a and b and c and d and e and f)\n",
        encoding="utf-8",
    )

    assert check_radon_complexity.main(["--root", str(tmp_path), "--concise"]) == 1
    output = capsys.readouterr().out
    assert "CC-A001" in output
    assert "rank B" in output

    (src / "complex.py").write_text("def f():\n    return True\n", encoding="utf-8")
    assert check_radon_complexity.main(["--root", str(tmp_path)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_parse_changed_lines_and_filter_to_touched_blocks(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    target = src / "complex.py"
    target.write_text(
        "\n".join(
            [
                "def untouched(a, b, c, d, e, f):",
                "    return bool(a and b and c and d and e and f)",
                "",
                "def touched(a, b, c, d, e, f):",
                "    return bool(a and b and c and d and e and f)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    diff = "\n".join(
        [
            "diff --git a/src/complex.py b/src/complex.py",
            "--- a/src/complex.py",
            "+++ b/src/complex.py",
            "@@ -5,0 +5,1 @@",
            "+    return bool(a and b and c and d and e and f)",
            "",
        ]
    )

    changed = check_radon_complexity._parse_changed_lines(diff)
    assert changed == {Path("src/complex.py"): {5}}
    violations = check_radon_complexity.scan(tmp_path, ("src",), 5, changed_lines=changed)
    assert [item.name for item in violations] == ["touched"]


def test_semantic_changed_lines_ignore_formatting_only_function_changes() -> None:
    previous = "def complex_value(a, b, c, d, e, f):\n    return bool(a and b and c and d and e and f)\n"
    current = "def complex_value(\n    a, b, c, d, e, f\n):\n    return bool(\n        a and b and c and d and e and f\n    )\n"

    assert check_radon_complexity._semantic_changed_lines(current, previous, set(range(1, 7))) == set()


def test_semantic_changed_lines_keep_real_function_changes() -> None:
    previous = "def complex_value(a, b, c, d, e, f):\n    return bool(a and b and c and d and e and f)\n"
    current = "def complex_value(a, b, c, d, e, f, g):\n    return bool(a and b and c and d and e and f and g)\n"

    assert check_radon_complexity._semantic_changed_lines(current, previous, {1, 2}) == {1, 2}


def test_parse_changed_lines_ignores_deleted_files_and_zero_length_hunks() -> None:
    diff = "\n".join(
        [
            "diff --git a/src/deleted.py b/src/deleted.py",
            "--- a/src/deleted.py",
            "+++ /dev/null",
            "@@ -1,2 +0,0 @@",
            "-old",
            "diff --git a/src/kept.py b/src/kept.py",
            "--- a/src/kept.py",
            "+++ b/src/kept.py",
            "@@ -3,1 +3,0 @@",
            "-removed",
            "",
        ]
    )

    assert check_radon_complexity._parse_changed_lines(diff) == {Path("src/kept.py"): set()}
