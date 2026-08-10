# ruff: noqa: S101
from pathlib import Path
from typing import Any, cast

import pytest as _pytest  # pyright: ignore[reportMissingImports]

import src.zentag as zentag
from src.meta import Meta

pytest: Any = cast(Any, _pytest)


@pytest.mark.asyncio
async def test_unattended_zenith_m4b_is_transformed_and_validated(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book [B07ZHYPJK1].m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Author - Book (2020) JPN {Narrator} [WEB] M4B AAC 64kbps"
    calls: list[list[str]] = []

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        output.mkdir(parents=True)
        return 0, f"Proceed? [y/N]: Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(command: list[str]) -> tuple[int, str, str]:
        calls.append(command)
        return 0, "[]", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH"], unattended=True)

    prepared = await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}})

    assert prepared == str(output.resolve())
    assert "--asin" in calls[0]
    assert calls[0][calls[0].index("--asin") + 1] == "B07ZHYPJK1"
    assert calls[1][-1] == "--json"


@pytest.mark.asyncio
async def test_failed_zentag_validation_keeps_original_for_other_trackers(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book [B07ZHYPJK1].m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Book"

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(_command: list[str]) -> tuple[int, str, str]:
        output.mkdir(parents=True)
        return 0, f"Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(_command: list[str]) -> tuple[int, str, str]:
        return 1, '[{"rule":"naming"}]', "violations found"

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH", "YUSCENE"], unattended=True)

    assert await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {"auto_zentag": True}}) is None


@pytest.mark.asyncio
async def test_debug_runs_zentag_without_uploading(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "Book.m4b"
    source.write_bytes(b"m4b")
    output = tmp_path / "zentag-output" / "Author - Book"

    async def fake_binary(_base_dir: str) -> str:
        return "/bin/zentag"

    async def fake_transform(_command: list[str]) -> tuple[int, str, str]:
        output.mkdir(parents=True)
        return 0, f"Wrote {zentag.json.dumps(str(output))}\n", ""

    async def fake_process(_command: list[str]) -> tuple[int, str, str]:
        return 0, "[]", ""

    monkeypatch.setattr(zentag.ZentagBinaryManager, "ensure_binary", staticmethod(fake_binary))
    monkeypatch.setattr(zentag, "_run_transform", fake_transform)
    monkeypatch.setattr(zentag, "_run_process", fake_process)
    meta = Meta(path=str(source), trackers=["ZENITH"], unattended=True, debug=True)

    assert await zentag.prepare_zenith_audiobook(meta, str(tmp_path), {"DEFAULT": {}}) == str(output.resolve())
