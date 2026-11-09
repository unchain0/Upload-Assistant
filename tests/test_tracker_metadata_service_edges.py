from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import httpx
import pytest
import requests

from src.domain_models.release import Meta
from src.services import tracker_metadata_service
from src.services.tracker_metadata_service import TrackerDataManager


def _config(**default: object) -> dict[str, Any]:
    return {
        "DEFAULT": {"tracker_comment_only": True, **default},
        "TRACKERS": {},
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "name": "Release",
        "filename": "Release.mkv",
        "category": "TV",
        "is_disc": "",
        "keep_images": True,
        "skip_tracker_descriptions": False,
        "unattended": True,
        "unattended_confirm": False,
        "persist_description": True,
        "description": "",
        "description_provenance": {},
        "image_list": [],
        "tracker_ids": {},
        "trackers": [],
        "site_check": False,
        "debug": True,
        "matched_tracker": None,
        "torrent_comments": [],
        "region": "",
        "distributor": "",
    }
    state.update(values)
    return Meta(state)


class _Cache:
    def __init__(self, value: object = "MISS") -> None:
        self.value = value
        self.set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def get(self, *_args: object, **_kwargs: object) -> object:
        return self.value

    async def set(self, *args: object, **kwargs: object) -> None:
        self.set_calls.append((args, kwargs))


def _patch_cache(monkeypatch: pytest.MonkeyPatch, cache: _Cache) -> None:
    monkeypatch.setattr(
        tracker_metadata_service,
        "tracker_metadata_cache_for",
        lambda *_args, **_kwargs: cache,
    )
    monkeypatch.setattr(
        tracker_metadata_service,
        "is_cache_miss",
        lambda value: value == "MISS",
    )


def test_constructor_config_validation_and_search_flags() -> None:
    with pytest.raises(ValueError, match="TRACKERS"):
        TrackerDataManager({"TRACKERS": [], "DEFAULT": {}})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="DEFAULT"):
        TrackerDataManager({"TRACKERS": {}, "DEFAULT": []})  # type: ignore[dict-item]

    manager = TrackerDataManager(
        {
            "DEFAULT": {},
            "TRACKERS": {
                "TRUE": {"use_for_search": True},
                "FALSE": {"use_for_search": False},
                "API": {"useAPI": "true"},
            },
        }
    )
    assert manager._search_enabled("TRUE")
    assert not manager._search_enabled("FALSE")
    assert manager._search_enabled("API")
    assert not manager._search_enabled("MISSING")
    assert dict(manager.get_tracker_config("MISSING")) == {}


def test_explicit_tracker_metadata_without_id_cache_hit_miss_and_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TrackerDataManager(_config())
    updater = AsyncMock(return_value=(_meta(tmp_path, title="Updated"), True))
    manager.tracker_meta_manager.update_metadata_from_tracker = updater

    meta = _meta(tmp_path)
    result, match = asyncio.run(
        manager.update_metadata_from_explicit_tracker(
            "AITHER", object(), meta, "term", "folder", False
        )
    )
    assert match and result.title == "Updated"
    updater.assert_awaited_once()

    cache = _Cache({"match": True, "metadata": {"title": "Cached"}})
    _patch_cache(monkeypatch, cache)
    meta = _meta(tmp_path, tracker_ids={"AITHER": "123"})
    result, match = asyncio.run(
        manager.update_metadata_from_explicit_tracker(
            "AITHER", object(), meta, "term", "folder", False
        )
    )
    assert match and result.title == "Cached"

    cache = _Cache("MISS")
    _patch_cache(monkeypatch, cache)
    updated = _meta(tmp_path, tracker_ids={"AITHER": "123"}, title="Fetched")
    manager.tracker_meta_manager.update_metadata_from_tracker = AsyncMock(
        return_value=(updated, False)
    )
    result, match = asyncio.run(
        manager.update_metadata_from_explicit_tracker(
            "AITHER", object(), meta, "term", "folder", False
        )
    )
    assert not match and result.title == "Fetched"
    assert cache.set_calls[-1][1] == {"negative": True}

    cache.set_calls.clear()
    manager.tracker_meta_manager.update_metadata_from_tracker = AsyncMock(
        return_value=(updated, True)
    )
    asyncio.run(
        manager.update_metadata_from_explicit_tracker(
            "AITHER", object(), meta, "term", "folder", False, use_cache=False
        )
    )
    assert cache.set_calls == []


def test_candidate_score_and_btn_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TrackerDataManager(_config(btn_api="x" * 30))
    original = _meta(tmp_path, tmdb_id=1, imdb_id=2)
    candidate = original.copy()
    candidate.tmdb_id = 3
    candidate.description = "description"
    candidate.description_provenance = {"score": 5}
    candidate.image_list = [{"img_url": "one"}, {"img_url": "two"}]
    assert manager._candidate_score(original, candidate) == 47

    short = TrackerDataManager(_config(btn_api="short"))
    original.set_tracker_ids({"BTN": "10"})
    assert (
        asyncio.run(
            short._collect_explicit_tracker_candidate(
                "BTN", original, "term", "folder", False
            )
        )
        is None
    )

    monkeypatch.setattr(
        tracker_metadata_service.BtnIdManager,
        "get_btn_torrents",
        AsyncMock(return_value=(0, 0)),
    )
    assert (
        asyncio.run(
            manager._collect_explicit_tracker_candidate(
                "BTN", original, "term", "folder", False
            )
        )
        is None
    )

    monkeypatch.setattr(
        tracker_metadata_service.BtnIdManager,
        "get_btn_torrents",
        AsyncMock(return_value=(123, 456)),
    )
    result = asyncio.run(
        manager._collect_explicit_tracker_candidate(
            "BTN", original, "term", "folder", False
        )
    )
    assert result is not None
    name, selected, score = result
    assert (
        name == "BTN"
        and selected.imdb_id == 123
        and selected.tvdb_id == 456
        and score >= 40
    )
    assert not (tmp_path / "tmp" / selected.uuid).exists()


def test_anthelion_generic_missing_and_candidate_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TrackerDataManager(_config())
    meta = _meta(tmp_path)

    class Anthelion:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def get_data_from_files(self, _meta: Meta) -> object:
            return [{"title": "ANT Title"}, {"imdb_id": 99}]

    monkeypatch.setitem(
        tracker_metadata_service.tracker_class_map, "ANTHELION", Anthelion
    )
    result = asyncio.run(
        manager._collect_explicit_tracker_candidate(
            "ANTHELION", meta, "term", "folder", False
        )
    )
    assert (
        result is not None
        and result[1].title == "ANT Title"
        and result[1].imdb_id == 99
    )

    Anthelion.get_data_from_files = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        asyncio.run(
            manager._collect_explicit_tracker_candidate(
                "ANTHELION", meta, "term", "folder", False
            )
        )
        is None
    )
    assert (
        asyncio.run(
            manager._collect_explicit_tracker_candidate(
                "MISSING", meta, "term", "folder", False
            )
        )
        is None
    )

    class Generic:
        def __init__(self, **_kwargs: object) -> None:
            pass

    monkeypatch.setitem(
        tracker_metadata_service.tracker_class_map, "GENERIC", Generic
    )
    manager.update_metadata_from_explicit_tracker = AsyncMock(
        return_value=(_meta(tmp_path, title="Generic"), True)
    )  # type: ignore[method-assign]
    result = asyncio.run(
        manager._collect_explicit_tracker_candidate(
            "GENERIC", meta, "term", "folder", False
        )
    )
    assert result is not None and result[1].title == "Generic"

    manager.update_metadata_from_explicit_tracker = AsyncMock(
        return_value=(meta, False)
    )  # type: ignore[method-assign]
    assert (
        asyncio.run(
            manager._collect_explicit_tracker_candidate(
                "GENERIC", meta, "term", "folder", False
            )
        )
        is None
    )

    for error in (
        httpx.ConnectError("network"),
        requests.exceptions.ConnectionError("network"),
        RuntimeError("generic"),
    ):
        manager.update_metadata_from_explicit_tracker = AsyncMock(
            side_effect=error
        )  # type: ignore[method-assign]
        assert (
            asyncio.run(
                manager._collect_explicit_tracker_candidate(
                    "GENERIC", meta, "term", "folder", False
                )
            )
            is None
        )


def test_choose_apply_and_review_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TrackerDataManager(_config())
    meta = _meta(tmp_path, unattended=True)
    one = _meta(tmp_path, name="One")
    two = _meta(tmp_path, name="Two")
    assert (
        asyncio.run(manager._choose_explicit_tracker_candidate(meta, []))
        is None
    )
    assert asyncio.run(
        manager._choose_explicit_tracker_candidate(
            meta, [("ONE", one, 1), ("TWO", two, 9)]
        )
    ) == ("TWO", two)

    meta.unattended = False
    monkeypatch.setattr(
        tracker_metadata_service,
        "prompt_in_thread",
        AsyncMock(return_value="2"),
    )
    assert asyncio.run(
        manager._choose_explicit_tracker_candidate(
            meta, [("ONE", one, 9), ("TWO", two, 1)]
        )
    ) == ("TWO", two)
    monkeypatch.setattr(
        tracker_metadata_service,
        "prompt_in_thread",
        AsyncMock(return_value="99"),
    )
    assert asyncio.run(
        manager._choose_explicit_tracker_candidate(
            meta, [("ONE", one, 9), ("TWO", two, 1)]
        )
    ) == ("ONE", one)
    monkeypatch.setattr(
        tracker_metadata_service,
        "prompt_in_thread",
        AsyncMock(return_value="bad"),
    )
    assert asyncio.run(
        manager._choose_explicit_tracker_candidate(
            meta, [("ONE", one, 9), ("TWO", two, 1)]
        )
    ) == ("ONE", one)

    candidate = _meta(
        tmp_path,
        uuid="worker",
        unattended=True,
        title="Candidate",
        description="text",
    )
    asyncio.run(
        manager._apply_explicit_tracker_candidate(meta, "ONE", candidate)
    )
    assert (
        meta.title == "Candidate"
        and meta.uuid == "release"
        and meta.matched_tracker == "ONE"
    )

    unattended = _meta(tmp_path, unattended=True, description="original")
    asyncio.run(
        manager._review_explicit_tracker_description(
            unattended, "ONE", candidate
        )
    )
    assert candidate.description == "text"
    empty = _meta(tmp_path, unattended=False, description="")
    asyncio.run(
        manager._review_explicit_tracker_description(empty, "ONE", empty)
    )

    interactive = _meta(tmp_path, unattended=False)
    candidate = _meta(
        tmp_path, description="original", description_provenance={"score": 1}
    )
    monkeypatch.setattr(
        tracker_metadata_service,
        "prompt_in_thread",
        AsyncMock(return_value="e"),
    )
    monkeypatch.setattr(
        tracker_metadata_service.click, "edit", lambda _text: " edited "
    )
    asyncio.run(
        manager._review_explicit_tracker_description(
            interactive, "ONE", candidate
        )
    )
    assert candidate.description == "edited" and candidate.saved_description
    assert candidate.description_provenance["edited"] is True

    candidate.description = "original"
    monkeypatch.setattr(
        tracker_metadata_service.click, "edit", lambda _text: None
    )
    asyncio.run(
        manager._review_explicit_tracker_description(
            interactive, "ONE", candidate
        )
    )
    assert candidate.description == "original"

    monkeypatch.setattr(
        tracker_metadata_service,
        "prompt_in_thread",
        AsyncMock(return_value="d"),
    )
    asyncio.run(
        manager._review_explicit_tracker_description(
            interactive, "ONE", candidate
        )
    )
    assert candidate.description == "" and not candidate.saved_description
    assert candidate.description_provenance["discarded"] is True


def test_timestamp_load_save_and_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = TrackerDataManager(_config())
    assert asyncio.run(manager.get_tracker_timestamps(str(tmp_path))) == {}
    assert asyncio.run(manager.get_tracker_timestamps(None)) == {}
    asyncio.run(manager.save_tracker_timestamp("AITHER", None))
    assert not (tmp_path / "None").exists()

    target = tmp_path / "data" / "banned" / "tracker_timestamps.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"AITHER": 90.0}), encoding="utf-8")
    assert asyncio.run(manager.get_tracker_timestamps(str(tmp_path))) == {
        "AITHER": 90.0
    }
    target.write_text("not json", encoding="utf-8")
    assert asyncio.run(manager.get_tracker_timestamps(str(tmp_path))) == {}

    monkeypatch.setattr(tracker_metadata_service.time, "time", lambda: 100.0)
    asyncio.run(manager.save_tracker_timestamp("AITHER", str(tmp_path)))
    assert json.loads(target.read_text())["AITHER"] == 100.0

    original_write = Path.write_text

    def fail_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if path.name == "tracker_timestamps.json":
            raise OSError("read only")
        return original_write(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    asyncio.run(manager.save_tracker_timestamp("BHD", str(tmp_path)))
    monkeypatch.setattr(Path, "write_text", original_write)

    manager.get_tracker_timestamps = AsyncMock(
        return_value={"PASSTHEPOPCORN": 50.0, "AITHER": 90.0}
    )  # type: ignore[method-assign]
    available, waiting = asyncio.run(
        manager.get_available_trackers(
            ["PASSTHEPOPCORN", "AITHER", "BHD"], str(tmp_path)
        )
    )
    assert available == ["BHD"]
    assert waiting == [("PASSTHEPOPCORN", 10.0), ("AITHER", 5.0)]


def test_specific_tracker_orchestration_filters_cooldown_selects_and_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "DEFAULT": {
            "tracker_comment_only": True,
            "tracker_search_concurrency": "bad",
        },
        "TRACKERS": {
            "AITHER": {"use_for_search": True},
            "BTN": {"use_for_search": True},
            "ANTHELION": {"use_for_search": True},
            "DISABLED": {"use_for_search": False},
        },
    }
    manager = TrackerDataManager(config)
    meta = _meta(
        tmp_path,
        category="TV",
        tracker_ids={
            "AITHER": "1",
            "BTN": "2",
            "ANTHELION": "3",
            "DISABLED": "4",
        },
        trackers="AITHER, OTHER",
        site_check=True,
    )
    available_calls = 0

    async def available(
        trackers: list[str], *_args: object, **_kwargs: object
    ):
        nonlocal available_calls
        available_calls += 1
        if available_calls == 1:
            return [], [(tracker, 0.0) for tracker in trackers]
        return ["BTN"], [("AITHER", 1.0)]

    manager.get_available_trackers = available  # type: ignore[method-assign]
    candidate = _meta(tmp_path, title="Candidate")
    manager._collect_explicit_tracker_candidate = AsyncMock(
        return_value=("BTN", candidate, 10)
    )  # type: ignore[method-assign]
    manager.save_tracker_timestamp = AsyncMock()  # type: ignore[method-assign]
    manager._review_explicit_tracker_description = AsyncMock()  # type: ignore[method-assign]
    manager._apply_explicit_tracker_candidate = AsyncMock(
        side_effect=lambda target, tracker, _selected: setattr(
            target, "matched_tracker", tracker
        )
    )  # type: ignore[method-assign]

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(tracker_metadata_service.asyncio, "sleep", no_sleep)
    result = asyncio.run(
        manager.get_tracker_data(None, meta, "term", "folder")
    )
    assert result.matched_tracker == "BTN"
    assert result.trackers == ["OTHER"]
    assert available_calls == 2

    disc = _meta(tmp_path, is_disc="BDMV", tracker_ids={"ANTHELION": "3"})
    assert (
        asyncio.run(manager.get_tracker_data(None, disc, "term", "folder"))
        is disc
    )
    movie = _meta(tmp_path, category="MOVIE", tracker_ids={"BTN": "2"})
    assert (
        asyncio.run(manager.get_tracker_data(None, movie, "term", "folder"))
        is movie
    )


def test_specific_tracker_no_match_and_no_search_term(tmp_path: Path) -> None:
    manager = TrackerDataManager(
        {
            "DEFAULT": {"tracker_comment_only": True},
            "TRACKERS": {"AITHER": {"use_for_search": True}},
        }
    )
    meta = _meta(tmp_path, tracker_ids={"AITHER": "1"}, trackers=7)
    manager.get_available_trackers = AsyncMock(return_value=(["AITHER"], []))  # type: ignore[method-assign]
    manager._collect_explicit_tracker_candidate = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager.save_tracker_timestamp = AsyncMock()  # type: ignore[method-assign]
    result = asyncio.run(
        manager.get_tracker_data(None, meta, "term", "folder")
    )
    assert result.matched_tracker is None
    assert result.trackers == []
    assert (
        asyncio.run(manager.get_tracker_data(None, meta, None, None)) is meta
    )


def test_filename_search_missing_success_and_connection_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.integrations.trackers import registry

    config = {
        "DEFAULT": {"tracker_comment_only": False},
        "TRACKERS": {
            "PASSTHEPOPCORN": {"use_for_search": False},
            "HDBITS": {"use_for_search": False},
            "BEYONDHD": {"use_for_search": False},
            "AITHER": {"use_for_search": True},
            "BLUTOPIA": {"use_for_search": True},
            "LST": {"use_for_search": True},
            "MISSING": {"use_for_search": True},
        },
    }
    manager = TrackerDataManager(config)
    monkeypatch.setattr(
        registry, "api_trackers", {"AITHER", "BLUTOPIA", "LST", "MISSING"}
    )

    class Factory:
        def __init__(self, **_kwargs: object) -> None:
            pass

    for name in ("AITHER", "BLUTOPIA", "LST"):
        monkeypatch.setitem(
            tracker_metadata_service.tracker_class_map, name, Factory
        )
    monkeypatch.delitem(
        tracker_metadata_service.tracker_class_map, "MISSING", raising=False
    )

    async def update(
        name: str,
        _instance: object,
        meta: Meta,
        *_args: object,
        **_kwargs: object,
    ):
        if name == "AITHER":
            raise httpx.ConnectError("ssl")
        if name == "BLUTOPIA":
            raise requests.exceptions.ConnectionError("network")
        meta.title = "Matched"
        return meta, True

    manager.update_metadata_from_explicit_tracker = update  # type: ignore[method-assign]
    result = asyncio.run(
        manager.get_tracker_data(
            None, _meta(tmp_path, category="MOVIE"), "term", "folder"
        )
    )
    assert result.title == "Matched" and result.matched_tracker == "LST"

    manager.update_metadata_from_explicit_tracker = AsyncMock(
        return_value=(_meta(tmp_path), False)
    )  # type: ignore[method-assign]
    no_match = asyncio.run(
        manager.get_tracker_data(
            None, _meta(tmp_path, category="TV"), "term", "folder", cat="TV"
        )
    )
    assert no_match.no_tracker_match is True


def test_tracker_comment_only_skips_filename_search(tmp_path: Path) -> None:
    manager = TrackerDataManager(_config(tracker_comment_only=True))
    meta = _meta(tmp_path, tracker_ids={})
    assert (
        asyncio.run(manager.get_tracker_data(None, meta, "term", "folder"))
        is meta
    )


class _TrackerFactory:
    def __init__(
        self, config: dict[str, Any] | None = None, **_kwargs: object
    ) -> None:
        self.config = config or {}
        self.base_url = "https://aither.cc"
        self.torrent_url = "https://aither.cc/torrents"


class _Common:
    updates: ClassVar[dict[str, str]] = {
        "region": "US",
        "distributor": "Criterion",
    }

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    async def unit3d_region_distributor(
        self, meta: Meta, *_args: object
    ) -> None:
        for key, value in self.updates.items():
            setattr(meta, key, value)


def test_ping_unit3d_cache_miss_and_cached_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.integrations.trackers import common, registry

    monkeypatch.setattr(registry, "api_trackers", {"AITHER"})
    monkeypatch.setitem(
        tracker_metadata_service.tracker_class_map, "AITHER", _TrackerFactory
    )
    monkeypatch.setattr(common, "Common", _Common)

    cache = _Cache("MISS")
    _patch_cache(monkeypatch, cache)
    manager = TrackerDataManager(_config())
    meta = _meta(
        tmp_path,
        torrent_comments=[
            {"comment": "download https://aither.cc/torrents/123"}
        ],
        region="",
        distributor="",
    )
    asyncio.run(manager.ping_unit3d(meta))
    assert meta.aither == "123"
    assert meta.region == "US" and meta.distributor == "Criterion"
    assert cache.set_calls[-1][0][-1]["metadata"] == {
        "region": "US",
        "distributor": "Criterion",
    }

    cache = _Cache({"metadata": {"region": "CA", "distributor": "Sony"}})
    _patch_cache(monkeypatch, cache)
    cached = _meta(
        tmp_path,
        torrent_comments=[{"comment": "https://aither.cc/torrents/456"}],
    )
    asyncio.run(manager.ping_unit3d(cached))
    assert cached.region == "CA" and cached.distributor == "Sony"
    assert cache.set_calls == []


def test_ping_unit3d_host_fallback_announce_invalid_comments_and_early_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.integrations.trackers import common, registry

    class BrokenFactory:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("broken")

    class AnnounceFactory:
        def __init__(
            self, config: dict[str, Any] | None = None, **_kwargs: object
        ) -> None:
            self.config = config or {}
            self.base_url = ""
            self.torrent_url = "https://aither.cc/torrents"

    monkeypatch.setattr(registry, "api_trackers", {"AITHER", "BLUTOPIA"})
    monkeypatch.setitem(
        tracker_metadata_service.tracker_class_map, "AITHER", AnnounceFactory
    )
    monkeypatch.setitem(
        tracker_metadata_service.tracker_class_map, "BLUTOPIA", BrokenFactory
    )
    monkeypatch.setattr(common, "Common", _Common)
    cache = _Cache("MISS")
    _patch_cache(monkeypatch, cache)
    config = _config()
    config["TRACKERS"] = {
        "AITHER": {"announce_url": "https://aither.cc/announce"}
    }
    manager = TrackerDataManager(config)

    invalid = _meta(
        tmp_path,
        torrent_comments=[
            {"comment": "https://evil.invalid/torrents/1"},
            {"comment": "https://aither.cc/torrents/not-an-id"},
            {"comment": "http://aither.cc/torrents/789"},
        ],
    )
    asyncio.run(manager.ping_unit3d(invalid))
    assert invalid.aither == "789"

    complete = _meta(
        tmp_path,
        region="US",
        distributor="Criterion",
        torrent_comments=[{"comment": "https://aither.cc/torrents/1"}],
    )
    asyncio.run(manager.ping_unit3d(complete))
    assert not complete.get("aither")

    asyncio.run(manager.ping_unit3d(_meta(tmp_path, torrent_comments=[])))
