from __future__ import annotations

import asyncio

from src.domain_models.release import Meta
from src.services import tracker_upload_service


def test_preparation_runs_once_per_release(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def fake_prepare(meta, base_dir, config):  # type: ignore[no-untyped-def]
        calls.append(str(meta.uuid))
        return f"/tmp/{meta.uuid}/release.m4b"

    monkeypatch.setattr(
        tracker_upload_service, "prepare_zenith_audiobook", fake_prepare
    )
    monkeypatch.setattr(
        tracker_upload_service, "prepare_zenith_ebook", fake_prepare
    )
    tracker_upload_service._ZENITH_PREPARED.clear()
    meta = Meta(audiobook=True, category="BOOK", base_dir="/tmp", uuid="abc")
    first = asyncio.run(
        tracker_upload_service._zenith_prepared_path(meta, {})
    )
    second = asyncio.run(
        tracker_upload_service._zenith_prepared_path(meta, {})
    )
    assert first == second == "/tmp/abc/release.m4b"
    assert calls == ["abc"]


def test_different_releases_prepare_separately(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def fake_prepare(meta, base_dir, config):  # type: ignore[no-untyped-def]
        calls.append(str(meta.uuid))
        return f"/tmp/{meta.uuid}/release.m4b"

    monkeypatch.setattr(
        tracker_upload_service, "prepare_zenith_audiobook", fake_prepare
    )
    monkeypatch.setattr(
        tracker_upload_service, "prepare_zenith_ebook", fake_prepare
    )
    tracker_upload_service._ZENITH_PREPARED.clear()
    for uuid in ("one", "two"):
        asyncio.run(
            tracker_upload_service._zenith_prepared_path(
                Meta(audiobook=True, category="BOOK", base_dir="/tmp", uuid=uuid),
                {},
            )
        )
    assert calls == ["one", "two"]
