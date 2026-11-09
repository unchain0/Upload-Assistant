from __future__ import annotations

import asyncio
import json

import pytest

from src.integrations.media import zentag


def _patch(monkeypatch, payload: str, code: int = 1, stderr: str = "violations found") -> None:  # type: ignore[no-untyped-def]
    async def fake(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return code, payload, stderr

    monkeypatch.setattr(zentag, "_run_process", fake)


def test_warning_findings_do_not_abort(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _patch(
        monkeypatch,
        json.dumps(
            [
                {
                    "Rule": "audnexus_chapters",
                    "Severity": "warn",
                    "Message": "audnexus reports 69 chapters, item has 11",
                }
            ]
        ),
    )
    asyncio.run(zentag._validate_audiobook_output("zentag", tmp_path, tmp_path))


def test_error_findings_abort(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _patch(monkeypatch, json.dumps([{"Rule": "x", "Severity": "error"}]))
    with pytest.raises(RuntimeError, match="blocking"):
        asyncio.run(
            zentag._validate_audiobook_output("zentag", tmp_path, tmp_path)
        )


def test_unparseable_output_still_fails(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _patch(monkeypatch, "not json")
    with pytest.raises(RuntimeError):
        asyncio.run(
            zentag._validate_audiobook_output("zentag", tmp_path, tmp_path)
        )
