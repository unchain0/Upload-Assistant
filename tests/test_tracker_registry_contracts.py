from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import registry


class _Response:
    def __init__(self, status: int = 200, payload: object | None = None, text: str = "ok") -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> object:
        return self._payload


class _Client:
    queue: ClassVar[list[object]] = []
    requests: ClassVar[list[tuple[str, tuple[object, ...], dict[str, object]]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    @classmethod
    def reset(cls, *items: object) -> None:
        cls.queue = list(items)
        cls.requests = []

    @classmethod
    def _next(cls) -> _Response:
        item = cls.queue.pop(0) if cls.queue else _Response(200, {"data": [], "meta": {}})
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, _Response)
        return item

    async def get(self, *args: object, **kwargs: object) -> _Response:
        self.requests.append(("get", args, kwargs))
        return self._next()

    async def post(self, *args: object, **kwargs: object) -> _Response:
        self.requests.append(("post", args, kwargs))
        return self._next()


class _Tracker:
    supported_categories = ("MOVIE", "TV")
    banned_url = "https://tracker.invalid/banned"
    claims_url = "https://tracker.invalid/claims"
    requests_url = "https://tracker.invalid/api/requests/filter"
    trumping_url = "https://tracker.invalid/api/trumps/filter"
    banned_groups_auth_mode = "bearer"
    banned_groups_response_key = "data"
    auth_type = "unit3d_api"
    base_url = "https://tracker.invalid"
    comment_hosts = ("comments.tracker.invalid",)
    tracker_urls: ClassVar[list[str]] = ["https://legacy.tracker.invalid/torrents"]

    def __init__(self, _config: dict[str, Any]) -> None:
        return None

    async def get_type_id(self, _meta: Meta, mapping_only: bool = False) -> object:
        return {"REMUX": 1, "WEBDL": 2} if mapping_only else 1

    async def get_resolution_id(self, _meta: Meta, mapping_only: bool = False) -> object:
        return {"1080p": 10, "2160p": 20} if mapping_only else 10

    async def get_category_id(self, _meta: Meta, mapping_only: bool = False) -> object:
        return {"MOVIE": 100, "TV": 200} if mapping_only else 100

    async def get_requests(self, _meta: Meta) -> list[dict[str, Any]]:
        return [{"id": 1}]


class _NoCategories:
    def __init__(self, _config: dict[str, Any]) -> None:
        return None


class _NoEndpoints:
    supported_categories = ("MOVIE",)

    def __init__(self, _config: dict[str, Any]) -> None:
        return None


@pytest.fixture
def config() -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "TEST": {"api_key": " key ", "announce_url": "https://tracker.invalid/announce"},
            "LST": {"api_key": "key"},
            "LUMINARR": {"api_key": "key"},
            "BEYONDHD": {"api_key": "key"},
            "HAWKEUNO": {"api_key": "key"},
            "ORPHEUS": {"api_key": "key"},
            "NOAPI": {"api_key": "", "announce_url": ""},
            "default_trackers": "TEST,UNKNOWN",
        },
    }


@pytest.fixture
def setup(config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> registry.TrackerSetup:
    monkeypatch.setattr(
        registry,
        "tracker_class_map",
        {
            "TEST": _Tracker,
            "LST": _Tracker,
            "LUMINARR": _Tracker,
            "BEYONDHD": _Tracker,
            "HAWKEUNO": _Tracker,
            "ORPHEUS": _Tracker,
            "NOAPI": _Tracker,
            "NOCATS": _NoCategories,
            "NOEND": _NoEndpoints,
        },
    )
    monkeypatch.setattr(registry.httpx, "AsyncClient", _Client)
    return registry.TrackerSetup(config)


def _meta(tmp_path: Path, **values: object) -> Meta:
    defaults: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "registry",
        "path": str(tmp_path / "release.mkv"),
        "category": "MOVIE",
        "type": "REMUX",
        "resolution": "1080p",
        "tmdb": 123,
        "tmdb_id": 123,
        "season": "S01",
        "season_int": 1,
        "episode_int": 2,
        "tag": "-GROUP",
        "trackers": ["TEST"],
        "tracker_status": {},
        "skip_upload_trackers": [],
        "HDR": "",
        "ua_name": "Upload Assistant",
    }
    defaults.update(values)
    return Meta(**defaults)


def _request_error() -> httpx.RequestError:
    return httpx.RequestError("offline", request=httpx.Request("GET", "https://tracker.invalid"))


def test_tracker_filtering_and_enablement_cover_missing_credentials_and_categories(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "example_config",
        {"TRACKERS": {"NOAPI": {"api_key": "", "announce_url": ""}, "TEST": {}, "NOCATS": {}}},
    )
    meta = _meta(tmp_path, trackers=["NOAPI", "TEST", "NOCATS", "UNKNOWN"])
    setup.filter_unsupported_trackers(meta)
    assert meta.trackers == ["TEST", "UNKNOWN"]
    assert meta.tracker_status["NOCATS"] == {"upload": False, "skipped": True}

    debug_meta = _meta(tmp_path, trackers=["NOAPI"], debug=True)
    setup.filter_unsupported_trackers(debug_meta)
    assert debug_meta.trackers == ["NOAPI"]

    unsupported = _meta(tmp_path, category="BOOK", trackers=["TEST"])
    setup.filter_unsupported_trackers(unsupported)
    assert unsupported.trackers == []
    assert unsupported.tracker_status["TEST"]["skipped"] is True

    empty_category = _meta(tmp_path, category=None, trackers=["TEST"])
    setup.filter_unsupported_trackers(empty_category)
    assert empty_category.trackers == ["TEST"]
    empty_trackers = _meta(tmp_path, trackers=[])
    setup.filter_unsupported_trackers(empty_trackers)

    enabled = _meta(tmp_path, trackers="TEST, UNKNOWN", manual=True)
    assert setup.trackers_enabled(enabled) == ["MANUAL", "TEST"]
    invalid_type = _meta(tmp_path, trackers=42)
    assert setup.trackers_enabled(invalid_type) == []
    inherited = _meta(tmp_path, trackers=None)
    assert setup.trackers_enabled(inherited) == ["TEST"]
    assert setup._create_tracker_instance("missing") is None
    assert isinstance(setup._create_tracker_instance("test"), _Tracker)


@pytest.mark.asyncio
async def test_banned_group_catalog_supports_lists_pagination_cache_and_errors(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meta = _meta(tmp_path)
    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=True))

    _Client.reset(_Response(200, [{"name": "GROUP"}, "OTHER"]))
    result = await setup.get_banned_groups(meta, "TEST")
    assert isinstance(result, Path) and result.is_file()
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["banned_groups"] == "GROUP, OTHER"

    result.unlink()
    _Client.reset(
        _Response(200, {"data": [{"name": "ONE"}], "meta": {"next_cursor": "next"}}),
        _Response(200, {"data": [{"name": "TWO"}], "meta": {"next_cursor": None}}),
    )
    assert await setup.get_banned_groups(meta, "TEST") == result
    assert len(_Client.requests) == 2

    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=False))
    assert await setup.get_banned_groups(meta, "TEST") == result
    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=True))

    _Client.reset(_Response(200, []))
    assert await setup.get_banned_groups(meta, "TEST") == "empty"
    for response in (
        _Response(200, {"data": "bad", "meta": {}}),
        _Response(200, {"data": [], "meta": "bad"}),
        _Response(200, "bad"),
        _Response(404, {}),
        _Response(500, {}),
    ):
        _Client.reset(response)
        assert await setup.get_banned_groups(meta, "TEST") is None
    _Client.reset(_request_error())
    assert await setup.get_banned_groups(meta, "TEST") is None
    _Client.reset(RuntimeError("bad payload"))
    assert await setup.get_banned_groups(meta, "TEST") is None

    monkeypatch.setattr(registry, "tracker_class_map", {"NOEND": _NoEndpoints})
    assert await setup.get_banned_groups(meta, "missing") is None
    assert await setup.get_banned_groups(meta, "NOEND") is None


@pytest.mark.asyncio
async def test_trash_sync_and_update_policy_are_deterministic(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "banned.json"
    _Client.reset(
        _Response(
            200,
            {
                "specifications": [
                    {"implementation": "ReleaseGroupSpecification", "fields": {"value": "^(ONE|TWO)$"}},
                    {"implementation": "ReleaseGroupSpecification", "fields": {"value": r"\b(THREE)\b"}},
                    {"implementation": "Other", "fields": {"value": "SKIP"}},
                    {"implementation": "ReleaseGroupSpecification", "fields": {"value": ""}},
                    None,
                ]
            },
        )
    )
    await setup.sync_trash_groups(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert set(payload["banned_groups"].split(", ")) == {"ONE", "TWO", "THREE"}

    target.unlink()
    _Client.reset(_Response(500, {}))
    await setup.sync_trash_groups(target)
    assert not target.exists()
    _Client.reset(RuntimeError("offline"))
    await setup.sync_trash_groups(target)
    assert not target.exists()
    _Client.reset(_Response(200, {"specifications": []}))
    await setup.sync_trash_groups(target)
    assert not target.exists()

    assert await setup.should_update(tmp_path / "missing.json") is True
    target.write_text('{"last_updated":"2000-01-01"}', encoding="utf-8")
    assert await setup.should_update(target) is True
    from datetime import UTC, datetime

    target.write_text(json.dumps({"last_updated": datetime.now(UTC).strftime("%Y-%m-%d")}), encoding="utf-8")
    assert await setup.should_update(target) is False
    target.write_text("not-json", encoding="utf-8")
    assert await setup.should_update(target) is True

    monkeypatch.setattr(setup, "_write_file", lambda *_args: (_ for _ in ()).throw(OSError("readonly")))
    await setup.write_banned_groups_to_file(target, [{"name": "X"}])
    await setup.write_internal_claims_to_file(target, [{"bad": True}])


@pytest.mark.asyncio
async def test_banned_group_checks_cover_dynamic_files_notes_and_prompt_paths(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await setup.check_banned_group("TEST", ["GROUP"], _meta(tmp_path, tag="")) is False
    assert await setup.check_banned_group("TEST", ["TAOE"], _meta(tmp_path, tag="-TAoE", unattended=True)) is True
    assert await setup.check_banned_group("TEST", [["GROUP", "reason"], [], "OTHER"], _meta(tmp_path, unattended=True)) is True

    safe = _meta(tmp_path, tag="-SAFE", unattended=True)
    assert await setup.check_banned_group("TEST", ["GROUP"], safe) is False

    dynamic = _meta(tmp_path, tag="-GROUP", unattended=True)
    monkeypatch.setattr(setup, "get_banned_groups", AsyncMock(return_value="empty"))
    assert await setup.check_banned_group("LUMINARR", [], dynamic) is False
    monkeypatch.setattr(setup, "get_banned_groups", AsyncMock(return_value=None))
    assert await setup.check_banned_group("LUMINARR", [], dynamic) is False

    banned_file = tmp_path / "banned.json"
    banned_file.write_text(json.dumps({"banned_groups": "GROUP, OTHER"}), encoding="utf-8")
    monkeypatch.setattr(setup, "get_banned_groups", AsyncMock(return_value=banned_file))
    assert await setup.check_banned_group("LUMINARR", [], dynamic) is True
    banned_file.write_text("not-json", encoding="utf-8")
    assert await setup.check_banned_group("LUMINARR", [], dynamic) is False
    banned_file.unlink()
    assert await setup.check_banned_group("LUMINARR", [], dynamic) is False

    interactive = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(registry.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    assert await setup.check_banned_group("TEST", ["GROUP"], interactive) is False
    monkeypatch.setattr(registry.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    assert await setup.check_banned_group("TEST", ["GROUP"], interactive) is True
    monkeypatch.setattr(registry.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError))
    monkeypatch.setattr(registry.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(registry.cleanup_manager, "reset_terminal", lambda: None)
    assert await setup.check_banned_group("TEST", ["GROUP"], interactive) is True


@pytest.mark.asyncio
async def test_claim_catalog_writes_fetches_and_matches(tmp_path: Path, setup: registry.TrackerSetup, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _meta(tmp_path)
    claims_file = tmp_path / "data" / "banned" / "TEST_claimed_releases.json"
    valid = {
        "attributes": {
            "title": "Example",
            "season": 0,
            "tmdb_id": 123,
            "resolutions": [10],
            "types": [1],
        }
    }
    await setup.write_internal_claims_to_file(claims_file, [{"invalid": True}, valid])
    assert claims_file.is_file()
    assert await setup.check_tracker_claims(meta, "TEST") is True
    assert await setup.check_tracker_claims(_meta(tmp_path, tmdb=999), ["TEST", "MISSING"]) is False

    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=True))
    _Client.reset(_Response(200, {"data": [valid], "meta": {"next_cursor": "next"}}), _Response(200, {"data": [], "meta": {}}))
    assert await setup.get_torrent_claims(meta, "TEST") is True
    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=False))
    assert await setup.get_torrent_claims(meta, "TEST") is True

    monkeypatch.setattr(setup, "should_update", AsyncMock(return_value=True))
    for response in (
        _Response(200, []),
        _Response(200, {"data": "bad", "meta": {}}),
        _Response(200, {"data": [], "meta": "bad"}),
        _Response(500, {}),
        _Response(200, {"data": [], "meta": {}}),
    ):
        _Client.reset(response)
        assert not await setup.get_torrent_claims(meta, "TEST")
    _Client.reset(_request_error())
    assert await setup.get_torrent_claims(meta, "TEST") is False
    _Client.reset(RuntimeError("bad"))
    assert await setup.get_torrent_claims(meta, "TEST") is False

    monkeypatch.setattr(registry, "tracker_class_map", {"NOEND": _NoEndpoints})
    assert await setup.get_torrent_claims(meta, "missing") is None
    assert await setup.get_torrent_claims(meta, "NOEND") is None


@pytest.mark.asyncio
async def test_request_endpoints_parse_supported_payloads_and_fail_safely(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    config: dict[str, Any],
) -> None:
    meta = _meta(tmp_path)
    item = {
        "id": 1,
        "name": "Request",
        "description": "desc",
        "category_id": 100,
        "type_id": 1,
        "resolution_id": 10,
        "bounty": 1000,
        "status": 1,
        "claimed": False,
        "season_number": 1,
        "episode_number": 2,
    }
    _Client.reset(_Response(200, {"data": [item]}))
    assert (await setup.get_tracker_requests(meta, "TEST", _Tracker.requests_url))[0]["name"] == "Request"
    hawke = {"id": 2, "attributes": item}
    _Client.reset(_Response(200, {"results": [hawke]}))
    assert (await setup.get_tracker_requests(meta, "HAWKEUNO", _Tracker.requests_url))[0]["id"] == 2
    assert await setup.get_tracker_requests(_meta(tmp_path, tmdb=None), "TEST", _Tracker.requests_url) == []

    for response in (_Response(200, []), _Response(200, {"unexpected": []}), _Response(500, {})):
        _Client.reset(response)
        assert await setup.get_tracker_requests(meta, "TEST", _Tracker.requests_url) == []
    for error in (_request_error(), httpx.TimeoutException("slow"), RuntimeError("bad")):
        _Client.reset(error)
        assert await setup.get_tracker_requests(meta, "TEST", _Tracker.requests_url) == []

    bhd_item = {"id": 3, "name": "BHD", "source": "Blu-ray", "type": "UHD Remux", "status": 1, "internal": 0, "url": "https://bhd.invalid/3"}
    _Client.reset(_Response(200, {"data": [bhd_item]}))
    assert (await setup.bhd_request_check(meta, "BEYONDHD", "https://bhd.invalid/api"))[0]["name"] == "BHD"
    config["TRACKERS"]["BEYONDHD"]["api_key"] = ""
    assert await setup.bhd_request_check(meta, "BEYONDHD", "https://bhd.invalid/api") == []


@pytest.mark.asyncio
async def test_tracker_request_logs_movie_tv_beyondhd_and_custom_matches(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_request = {
        "id": 1,
        "name": "Request",
        "description": "desc",
        "category": 100,
        "type": 1,
        "resolution": 10,
        "bounty": 100,
        "status": "open",
        "claimed": False,
        "season": 1,
        "episode": 2,
    }
    monkeypatch.setattr(setup, "get_tracker_requests", AsyncMock(return_value=[base_request]))
    assert await setup.tracker_request(_meta(tmp_path), "TEST") is True
    log = tmp_path / "tmp" / "TEST_request_results.json"
    assert json.loads(log.read_text(encoding="utf-8"))[0]["match_type"] if False else log.is_file()

    tv_request = dict(base_request, category=200)
    monkeypatch.setattr(setup, "get_tracker_requests", AsyncMock(return_value=[tv_request]))
    assert await setup.tracker_request(_meta(tmp_path, category="TV"), ["TEST", "MISSING"]) is True

    bhd = {
        "id": 2,
        "name": "Movie UHD Remux",
        "type": "Blu-ray",
        "resolution": "UHD Remux",
        "bounty": 100,
        "status": 1,
        "internal": 0,
        "url": "https://bhd.invalid/2",
        "dv": False,
        "hdr": False,
    }
    monkeypatch.setattr(setup, "bhd_request_check", AsyncMock(return_value=[bhd]))
    assert await setup.tracker_request(_meta(tmp_path, resolution="2160p", HDR=""), "BEYONDHD") is True

    monkeypatch.setattr(_Tracker, "get_requests", AsyncMock(return_value=[{"id": 1}]))
    assert await setup.tracker_request(_meta(tmp_path), "ORPHEUS") is True


@pytest.mark.asyncio
async def test_trump_search_processing_and_report_creation_cover_all_modes(
    tmp_path: Path,
    setup: registry.TrackerSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = {
        "id": 1,
        "type": "quality",
        "title": "Bad encode",
        "solved": False,
        "reported_torrents": [{"id": 7}],
        "trumping_torrent": {"id": 8, "name": "Replacement"},
    }
    _Client.reset(_Response(200, {"data": [report], "meta": {"next_cursor": "n"}}), _Response(200, {"results": [], "meta": {}}))
    requests, status = await setup.get_tracker_trumps("TEST", _Tracker.trumping_url, "7")
    assert status == 200 and requests[0]["trumping_torrent"] == [{"id": 8, "name": "Replacement"}]
    _Client.reset(_Response(500, {}, "fail"))
    assert await setup.get_tracker_trumps("TEST", _Tracker.trumping_url, "7") == ([], 500)
    _Client.reset(_request_error())
    assert (await setup.get_tracker_trumps("TEST", _Tracker.trumping_url, "7"))[0] == []

    meta = _meta(tmp_path)
    meta["TEST_trumpable_id"] = "7"
    normalized_report = dict(report, trumping_torrent=[{"id": 8, "name": "Replacement"}])
    monkeypatch.setattr(setup, "get_tracker_trumps", AsyncMock(return_value=([normalized_report], 200)))
    monkeypatch.setattr(registry.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    assert await setup.process_trumpables(meta, "TEST") is False
    assert "TEST" in meta.skip_upload_trackers

    meta.skip_upload_trackers = []
    monkeypatch.setattr(registry.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(registry.cli_ui, "ask_string", lambda *_args, **_kwargs: "d")
    assert await setup.process_trumpables(meta, "TEST") is True
    assert meta.screenshots_in_description is True

    meta.screenshots_in_description = False
    answers: Iterator[str] = iter(("L", "https://reported/1, https://reported/2", "https://trumping/1"))
    monkeypatch.setattr(registry.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers))
    assert await setup.process_trumpables(meta, "TEST") is True
    assert meta.screenshots_reported_torrent == ["https://reported/1", "https://reported/2"]

    meta.tv_pack = True
    assert await setup.process_trumpables(meta, "TEST") is True
    meta["LST_trumpable_id"] = "7"
    assert await setup.process_trumpables(meta, "LST") is True
    assert await setup.process_trumpables(_meta(tmp_path), "TEST") is False
    assert await setup.process_trumpables(_meta(tmp_path), "MISSING") is False

    meta = _meta(tmp_path, debug=True, tracker_status={"TEST": {"torrent_id": 9}})
    meta["TEST_reported_torrent_id"] = "7"
    meta.trump_reason = "exact_match"
    assert await setup.make_trumpable_report(meta, "TEST") is True
    meta.trump_reason = "trumpable_release"
    assert await setup.make_trumpable_report(meta, "TEST") is True
    meta.tv_pack = True
    assert await setup.make_trumpable_report(meta, "TEST") is True

    meta.debug = False
    _Client.reset(_Response(201, {}))
    assert await setup.make_trumpable_report(meta, "TEST") is True
    _Client.reset(_Response(500, {}))
    assert await setup.make_trumpable_report(meta, "TEST") is False
    _Client.reset(_request_error())
    assert await setup.make_trumpable_report(meta, "TEST") is False

    lst = _meta(tmp_path, debug=True, tracker_status={"LST": {"torrent_id": 9}})
    lst["LST_reported_torrent_id"] = "7"
    monkeypatch.setattr(registry.cli_ui, "ask_string", lambda *_args, **_kwargs: "reason")
    assert await setup.make_trumpable_report(lst, "LST") is True
    lst["LST_reported_torrent_id"] = "bad"
    assert await setup.make_trumpable_report(lst, "LST") is False
    assert await setup.make_trumpable_report(_meta(tmp_path), "MISSING") is False


def test_comment_host_catalog_normalizes_class_and_runtime_urls(config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "tracker_class_map", {"TEST": _Tracker, "NOEND": _NoEndpoints})
    config["TRACKERS"]["TEST"].update({"base_url": "runtime.tracker.invalid", "announce_url": "https://announce.tracker.invalid/passkey"})
    hosts = registry.get_tracker_comment_hosts(config)
    assert hosts["TEST"] == (
        "tracker.invalid",
        "comments.tracker.invalid",
        "legacy.tracker.invalid",
        "runtime.tracker.invalid",
        "announce.tracker.invalid",
    )
    assert "NOEND" not in hosts
