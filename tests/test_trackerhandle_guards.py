# ruff: noqa: S101
# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

import pytest

import src.trackerhandle as trackerhandle
from src.meta import Meta


class FakeClient:
    def __init__(self) -> None:
        self.added: list[str] = []

    async def add_to_client(self, _meta: Meta, tracker: str) -> None:
        self.added.append(tracker)


class FakeTracker:
    tracker = "TEST"
    upload_calls = 0
    fail_with_modqueue = False
    requires_book_cover = True

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def upload(self, meta: Meta) -> bool:
        type(self).upload_calls += 1
        if type(self).fail_with_modqueue:
            meta.tracker_status[self.tracker]["status_message"] = "data error: HTTP 404 - Modqueue limit reached."
            return False
        return True


def _image(number: int) -> dict[str, str]:
    return {"raw_url": f"https://images.example/{number}.png"}


def _meta(images: int = 4) -> Meta:
    return Meta(
        category="TV",
        name="Release",
        image_list=[_image(number) for number in range(images)],
        trackers=["TEST"],
        tracker_status={"TEST": {"upload": True}},
        print_tracker_links=False,
        print_tracker_messages=False,
    )


def _config(minimum: int = 4) -> dict[str, Any]:
    return {
        "DEFAULT": {
            "min_successful_image_uploads": str(minimum),
            "multiScreens": 2,
            "qbit_bandwidth_control": False,
            "show_upload_duration": False,
        }
    }


@pytest.fixture(autouse=True)
def tracker_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTracker.upload_calls = 0
    FakeTracker.fail_with_modqueue = False
    FakeTracker.requires_book_cover = True
    monkeypatch.setattr(trackerhandle.TrackerSetup, "trackers_enabled", lambda _self, _meta: ["TEST"])

    async def keep_images(_meta: Meta, _tracker: Any) -> None:
        return None

    monkeypatch.setattr(trackerhandle, "check_tracker_image_hosts", keep_images)


@pytest.mark.asyncio
async def test_tracker_specific_rehost_cannot_bypass_configured_minimum() -> None:
    meta = _meta()
    meta.tracker_image_collections["TEST"] = {"screenshots": [_image(1)]}

    await trackerhandle.process_trackers(meta, _config(), FakeClient(), ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 0
    assert meta.tracker_status["TEST"]["skipped"] is True


@pytest.mark.asyncio
async def test_tracker_upload_runs_when_configured_minimum_is_met() -> None:
    meta = _meta()
    client = FakeClient()

    await trackerhandle.process_trackers(meta, _config(), client, ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 1
    assert meta.tracker_status["TEST"]["upload_success"] is True
    assert client.added == ["TEST"]


@pytest.mark.asyncio
async def test_debug_runs_tracker_payload_without_rehosting_or_client_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(images=0)
    meta.debug = True
    client = FakeClient()
    rehost_calls = 0

    async def count_rehost(_meta: Meta, _tracker: Any) -> None:
        nonlocal rehost_calls
        rehost_calls += 1

    monkeypatch.setattr(trackerhandle, "check_tracker_image_hosts", count_rehost)

    await trackerhandle.process_trackers(meta, _config(), client, ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 1
    assert meta.tracker_status["TEST"]["upload_success"] is True
    assert rehost_calls == 0
    assert client.added == []


@pytest.mark.asyncio
async def test_book_without_cover_is_blocked_by_default() -> None:
    meta = _meta()
    meta.category = "BOOK"
    meta.artwork_path = ""

    await trackerhandle.process_trackers(meta, _config(), FakeClient(), ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 0
    assert "valid cover image" in meta.tracker_status["TEST"]["status_message"]


@pytest.mark.asyncio
async def test_tracker_can_allow_book_without_cover() -> None:
    FakeTracker.requires_book_cover = False
    meta = _meta()
    meta.category = "BOOK"
    meta.artwork_path = ""

    await trackerhandle.process_trackers(meta, _config(), FakeClient(), ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 1


@pytest.mark.asyncio
async def test_modqueue_limit_disables_tracker_for_remainder_of_run() -> None:
    config = _config()
    FakeTracker.fail_with_modqueue = True

    first_meta = _meta()
    await trackerhandle.process_trackers(first_meta, config, FakeClient(), ["TEST"], {"TEST": FakeTracker}, [], [])

    second_meta = _meta()
    await trackerhandle.process_trackers(second_meta, config, FakeClient(), ["TEST"], {"TEST": FakeTracker}, [], [])

    assert FakeTracker.upload_calls == 1
    assert second_meta.tracker_status["TEST"]["skipped"] is True
    assert "remainder of this run" in second_meta.tracker_status["TEST"]["status_message"]
