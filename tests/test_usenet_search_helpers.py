from __future__ import annotations

import builtins
import importlib.util
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from defusedxml import ElementTree

from src.domain_models.release import Meta
from src.integrations.trackers.USENET import search_helpers as helpers


def test_newznab_category_quality_and_book_branches() -> None:
    assert helpers.get_newznab_search_category_id(Meta(category="MOVIE", resolution="2160p")) == "2045"
    assert helpers.get_newznab_search_category_id(Meta(category="MOVIE", resolution="480p")) == "2030"
    assert helpers.get_newznab_search_category_id(Meta(category="TV", resolution="2160p")) == "5045"
    assert helpers.get_newznab_search_category_id(Meta(category="BOOK", audiobook=True)) == "3030"
    assert helpers.get_newznab_search_category_id(Meta(category="GAME")) == "4050"
    assert helpers.get_newznab_search_category_id(Meta(category="MUSIC")) == "3000"
    assert helpers.get_newznab_search_category_id(Meta(category="OTHER")) == "2000"
    assert helpers._resolution_quality_band("480p") == "sd"


def test_newznab_search_query_branches() -> None:
    assert helpers.build_newznab_search_query(Meta(category="TV", title="Show", season_int=1, episode_int=2)) == "Show S01E02"
    assert helpers.build_newznab_search_query(Meta(category="TV", title="Show", season_int=1, episode_int=0)) == "Show S01"
    assert helpers.build_newznab_search_query(Meta(category="TV", title="Show", season_int=0, episode_int=0)) == "Show"
    assert helpers.build_newznab_search_query(Meta(category="TV", title="", basename_no_ext="Fallback")) == "Fallback"
    assert helpers.build_newznab_search_query(Meta(category="MOVIE", title="Movie", year=2026)) == "Movie 2026"
    assert helpers.build_newznab_search_query(Meta(category="MOVIE", title="Movie", year="bad")) == "Movie"
    assert helpers.build_newznab_search_query(Meta(category="MOVIE", title="", basename_no_ext="Movie.Fallback")) == "Movie.Fallback"
    assert helpers.build_newznab_search_query(Meta(category="BOOK", title="Book", basename_no_ext="Book.File")) == "Book.File"


def test_newznab_episode_suffix_guard() -> None:
    assert helpers._tv_episode_suffix(Meta(season_int=0, episode_int=1)) == ""


def test_parse_newznab_dupes_without_channel() -> None:
    assert helpers.parse_newznab_dupes("<rss></rss>") == []


def test_parse_newznab_dupes_attributes_and_links() -> None:
    xml = """
    <rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
      <channel>
        <item>
          <title>Release One</title>
          <guid></guid>
          <enclosure length="100" />
          <newznab:attr name="guid" value="abc123" />
          <newznab:attr name="size" value="200" />
        </item>
        <item>
          <title>Release Two</title>
          <guid>https://example.invalid/2</guid>
          <link>https://example.invalid/link</link>
        </item>
        <item>
          <title>Release Three</title>
          <guid>relative-id</guid>
          <enclosure length="not-a-number" />
          <newznab:attr name="other" value="noop" />
        </item>
      </channel>
    </rss>
    """
    dupes = helpers.parse_newznab_dupes(xml, "https://indexer.invalid/details/", use_guid_attr_as_id=True)
    assert dupes[0] == {"name": "Release One", "files": "Release One", "size": 200, "link": "https://indexer.invalid/details/abc123"}
    assert dupes[1]["link"] == "https://example.invalid/link"
    assert dupes[2]["size"] == 0
    assert dupes[2]["link"] == "https://indexer.invalid/details/relative-id"


def test_newznab_attribute_and_link_helpers() -> None:
    item = ElementTree.fromstring('<item xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><newznab:attr name="other" value="x"/></item>')
    attr = item[0]
    assert helpers._apply_newznab_attribute(attr, "guid", "1", use_guid_attr_as_id=True) == ("guid", "1")
    assert not helpers._should_use_guid_attribute("guid", "x", "", False)
    assert helpers._needs_torrent_url_prefix("", "guid", "https://x/") is False
    assert helpers._needs_torrent_url_prefix("https://x/1", "guid", "https://x/") is False


def test_daily_limit_and_counter_filename_guards(tmp_path: Path) -> None:
    assert helpers.get_daily_api_hit_limit({"daily_api_hit_limit": "bad"}) == 0
    assert helpers._get_api_hit_counter_filename(" !! ") == "default.json"
    assert helpers._get_api_hit_counter_path(str(tmp_path), "NZB Geek").name == "nzb_geek.json"
    assert helpers._get_api_hit_counter_lock_path(str(tmp_path), "NZB Geek").suffix == ".lock"


class _FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self) -> None:
        self.calls: list[int] = []

    def locking(self, _fileno: int, mode: int, _size: int) -> None:
        self.calls.append(mode)


class _FakeFcntl:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 4

    def __init__(self) -> None:
        self.calls: list[int] = []

    def flock(self, _fileno: int, mode: int) -> None:
        self.calls.append(mode)


def test_lock_and_unlock_msvcrt_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeMsvcrt()
    monkeypatch.setattr(helpers, "msvcrt", fake)
    monkeypatch.setattr(helpers, "fcntl", None)
    with (tmp_path / "lock").open("a+b") as lock_file:
        helpers._lock_api_hit_counter_file(lock_file)
        helpers._unlock_api_hit_counter_file(lock_file)
    assert fake.calls == [fake.LK_NBLCK, fake.LK_UNLCK]


def test_lock_and_unlock_fcntl_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeFcntl()
    monkeypatch.setattr(helpers, "msvcrt", None)
    monkeypatch.setattr(helpers, "fcntl", fake)
    with (tmp_path / "lock").open("a+b") as lock_file:
        helpers._lock_api_hit_counter_file(lock_file)
        helpers._unlock_api_hit_counter_file(lock_file)
    assert fake.calls == [fake.LOCK_EX | fake.LOCK_NB, fake.LOCK_UN]


def test_lock_raises_without_platform_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(helpers, "msvcrt", None)
    monkeypatch.setattr(helpers, "fcntl", None)
    with (tmp_path / "lock").open("a+b") as lock_file, pytest.raises(RuntimeError, match="No supported file locking"):
        helpers._lock_api_hit_counter_file(lock_file)


def test_acquire_lock_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(helpers, "_lock_api_hit_counter_file", lambda _file: (_ for _ in ()).throw(BlockingIOError()))
    times = iter((0.0, 11.0))
    monkeypatch.setattr(helpers.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(helpers.time, "sleep", lambda _value: None)
    with pytest.raises(TimeoutError, match="Timed out"):
        helpers._acquire_api_hit_counter_lock(tmp_path / "counter.lock")


def test_release_lock_closes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "lock"
    handle = path.open("a+b")
    monkeypatch.setattr(helpers, "_unlock_api_hit_counter_file", lambda _file: None)
    helpers._release_api_hit_counter_lock(handle)
    assert handle.closed


def test_api_hit_cache_write_load_and_recent_filters(tmp_path: Path) -> None:
    cache = tmp_path / "counter.json"
    assert helpers._load_api_hit_cache(cache) == []
    helpers._write_api_hit_cache(cache, [1.0, 2.0])
    assert helpers._load_api_hit_cache(cache) == [1.0, 2.0]

    cache.write_text("not json", encoding="utf-8")
    assert helpers._load_api_hit_cache(cache) == []
    cache.write_text('{"not":"a list"}', encoding="utf-8")
    assert helpers._load_api_hit_cache(cache) == []

    now = time.time()
    recent = helpers._recent_api_hits([now, now - helpers.API_HIT_WINDOW_SECONDS - 1, "bad", True], now)
    assert recent == [float(now)]
    assert helpers._numeric_api_hit(True) is None
    assert helpers._numeric_api_hit(1) == 1.0


def test_reserve_daily_api_hit_sync_success_and_limit(tmp_path: Path) -> None:
    allowed, used = helpers._reserve_daily_api_hit_sync(str(tmp_path), "NZBGEEK", 1)
    assert allowed and used == 1
    allowed, used = helpers._reserve_daily_api_hit_sync(str(tmp_path), "NZBGEEK", 1)
    assert not allowed and used == 1


@pytest.mark.asyncio
async def test_reserve_daily_api_hit_async_wrapper(tmp_path: Path) -> None:
    allowed, used = await helpers.reserve_daily_api_hit(str(tmp_path), "DRUNKENSLUG", 2)
    assert allowed and used == 1


def test_import_fcntl_fallback_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    source_path = Path(helpers.__file__ or "")
    spec = importlib.util.spec_from_file_location("_search_helpers_without_fcntl", source_path)
    assert spec is not None and spec.loader is not None
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fcntl":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    assert module.fcntl is None


def test_search_helper_remaining_scalar_branches() -> None:
    assert helpers._resolution_quality_band("1080p") == "hd"
    assert helpers.get_daily_api_hit_limit({"daily_api_hit_limit": 5}) == 5


def test_acquire_lock_retries_before_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def flaky_lock(_file: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BlockingIOError()

    monkeypatch.setattr(helpers, "_lock_api_hit_counter_file", flaky_lock)
    monkeypatch.setattr(helpers.time, "sleep", lambda _value: None)
    lock_file = helpers._acquire_api_hit_counter_lock(tmp_path / "retry.lock")
    try:
        assert calls == 2
    finally:
        lock_file.close()
