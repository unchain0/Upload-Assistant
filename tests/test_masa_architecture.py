from __future__ import annotations

import ast
from pathlib import Path

from scripts.check_masa_architecture import ALLOWED_IMPORTS, Violation, scan


def test_masa_import_boundaries_have_no_violations() -> None:
    assert scan(Path("src")) == []


def test_matrix_keeps_domain_and_engines_independent_of_effectful_layers() -> None:
    assert ALLOWED_IMPORTS["domain_models"] == frozenset({"domain_models"})
    assert ALLOWED_IMPORTS["engines"] == frozenset({"domain_models", "engines"})
    assert "integrations" in ALLOWED_IMPORTS["services"]
    assert ALLOWED_IMPORTS["integrations"] == frozenset({"domain_models", "integrations"})
    assert "integrations" not in ALLOWED_IMPORTS["delivery"]


def test_violation_model_is_immutable() -> None:
    violation = Violation("services", "integrations", "src/services/example.py", 3, "src.integrations.example")
    try:
        violation.line = 4  # type: ignore[misc]
    except AttributeError, TypeError:
        pass
    else:  # pragma: no cover - dataclass contract failure is intentionally unreachable
        raise AssertionError("Violation must be immutable")


def test_checker_source_is_parseable_without_importing_application() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "check_masa_architecture.py"
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_checker_rejects_process_termination_outside_delivery(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    service = source_root / "services" / "bad_service.py"
    service.parent.mkdir(parents=True)
    service.write_text("import sys\n\ndef abort() -> None:\n    sys.exit(1)\n", encoding="utf-8")

    violations = scan(source_root)

    assert len(violations) == 1
    assert violations[0].target == "process"
    assert violations[0].module == "sys.exit"


def test_checker_allows_standalone_tool_exit_only_under_main_guard(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    integration = source_root / "integrations" / "tool.py"
    integration.parent.mkdir(parents=True)
    integration.write_text(
        "def main() -> int:\n    return 1\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    assert scan(source_root) == []
