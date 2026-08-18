# ruff: noqa: S101

from pathlib import Path

from scripts.pyright_to_rdjson import convert


def test_convert_pyright_diagnostic_to_reviewdog_rdjson() -> None:
    file_path = Path.cwd() / "src" / "example.py"
    payload = {
        "generalDiagnostics": [
            {
                "file": str(file_path),
                "severity": "error",
                "message": "Type mismatch",
                "rule": "reportAssignmentType",
                "range": {
                    "start": {"line": 4, "character": 2},
                    "end": {"line": 4, "character": 7},
                },
            }
        ]
    }

    assert convert(payload) == [
        {
            "message": "[reportAssignmentType] Type mismatch",
            "location": {
                "path": "src/example.py",
                "range": {
                    "start": {"line": 5, "column": 3},
                    "end": {"line": 5, "column": 8},
                },
            },
            "severity": "ERROR",
        }
    ]


def test_convert_skips_incomplete_diagnostics() -> None:
    assert convert({"generalDiagnostics": [{"severity": "warning"}]}) == []
