# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, BinaryIO

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

from defusedxml import ElementTree

from src.domain_models.release import Meta

API_HIT_WINDOW_SECONDS = 24 * 60 * 60
API_HIT_COUNTER_DIRNAME = "usenet_api_hit_counters"
API_HIT_COUNTER_LOCK_TIMEOUT_SECONDS = 10.0
API_HIT_COUNTER_LOCK_POLL_SECONDS = 0.05


def get_newznab_search_category_id(meta: Meta) -> str:
    category = meta.category.upper()
    handlers = {
        "MOVIE": _movie_newznab_category,
        "TV": _tv_newznab_category,
        "BOOK": _book_newznab_category,
        "GAME": lambda _meta: "4050",
        "MUSIC": lambda _meta: "3000",
    }
    handler = handlers.get(category)
    return handler(meta) if handler is not None else "2000"


def _movie_newznab_category(meta: Meta) -> str:
    return _quality_newznab_category(
        meta.resolution, uhd="2045", hd="2040", sd="2030"
    )


def _tv_newznab_category(meta: Meta) -> str:
    return _quality_newznab_category(
        meta.resolution, uhd="5045", hd="5040", sd="5030"
    )


def _book_newznab_category(meta: Meta) -> str:
    return "3030" if meta.audiobook else "7020"


def _quality_newznab_category(
    resolution: str, *, uhd: str, hd: str, sd: str
) -> str:
    quality = _resolution_quality_band(resolution)
    return {"uhd": uhd, "hd": hd}.get(quality, sd)


def _resolution_quality_band(resolution: str) -> str:
    value = resolution.lower()
    if value in {"2160p", "4320p", "8640p"}:
        return "uhd"
    if value in {"1080p", "1080i", "720p", "1440p"}:
        return "hd"
    return "sd"


def build_newznab_search_query(meta: Meta) -> str:
    title = _search_title(meta)
    category = meta.category.upper()
    if category == "TV":
        return _tv_search_query(meta, title)
    if category == "MOVIE":
        return _movie_search_query(meta, title)
    return str(meta.basename_no_ext or title).strip()


def _search_title(meta: Meta) -> str:
    return str(meta.title or meta.original_title or "").strip()


def _search_year(meta: Meta) -> int:
    raw_year = meta.year or meta.search_year or 0
    try:
        return int(raw_year)
    except TypeError, ValueError:
        return 0


def _tv_search_query(meta: Meta, title: str) -> str:
    if not title:
        return str(meta.basename_no_ext or "").strip()
    suffix = _tv_episode_suffix(meta)
    return f"{title} {suffix}" if suffix else title


def _tv_episode_suffix(meta: Meta) -> str:
    season = meta.season_int
    if season <= 0:
        return ""
    episode = meta.episode_int
    return f"S{season:02d}E{episode:02d}" if episode > 0 else f"S{season:02d}"


def _movie_search_query(meta: Meta, title: str) -> str:
    if not title:
        return str(meta.basename_no_ext or "").strip()
    year = _search_year(meta)
    return f"{title} {year}" if year > 0 else title


def parse_newznab_dupes(
    response_text: str,
    torrent_url: str | None = None,
    *,
    use_guid_attr_as_id: bool = False,
) -> list[dict[str, Any]]:
    response_xml = ElementTree.fromstring(response_text)
    channel = response_xml.find("channel")
    if channel is None:
        return []
    return [
        _newznab_item_dupe(
            item, torrent_url, use_guid_attr_as_id=use_guid_attr_as_id
        )
        for item in channel.findall("item")
    ]


def _newznab_item_dupe(
    item: Any, torrent_url: str | None, *, use_guid_attr_as_id: bool
) -> dict[str, Any]:
    title = str(item.findtext("title") or "")
    guid = str(item.findtext("guid") or "")
    size_text = _newznab_enclosure_size(item)
    guid, size_text = _newznab_attributes(
        item, guid, size_text, use_guid_attr_as_id=use_guid_attr_as_id
    )
    item_link = _newznab_item_link(item, guid, torrent_url)
    return {
        "name": title,
        "files": title,
        "size": int(size_text) if size_text.isdigit() else 0,
        "link": item_link,
    }


def _newznab_enclosure_size(item: Any) -> str:
    enclosure = item.find("enclosure")
    return (
        "0"
        if enclosure is None
        else str(enclosure.attrib.get("length") or "0")
    )


def _newznab_attributes(
    item: Any, guid: str, size_text: str, *, use_guid_attr_as_id: bool
) -> tuple[str, str]:
    for attr in item.findall(
        "{http://www.newznab.com/DTD/2010/feeds/attributes/}attr"
    ):
        guid, size_text = _apply_newznab_attribute(
            attr, guid, size_text, use_guid_attr_as_id=use_guid_attr_as_id
        )
    return guid, size_text


def _apply_newznab_attribute(
    attr: Any, guid: str, size_text: str, *, use_guid_attr_as_id: bool
) -> tuple[str, str]:
    name = str(attr.attrib.get("name") or "").lower()
    value = str(attr.attrib.get("value") or "")
    if _is_size_attribute(name, value):
        return guid, value
    if _should_use_guid_attribute(name, value, guid, use_guid_attr_as_id):
        return value, size_text
    return guid, size_text


def _is_size_attribute(name: str, value: str) -> bool:
    return name == "size" and bool(value)


def _should_use_guid_attribute(
    name: str, value: str, guid: str, enabled: bool
) -> bool:
    if not enabled or name != "guid":
        return False
    return bool(value) and not guid


def _newznab_item_link(item: Any, guid: str, torrent_url: str | None) -> str:
    link = str(item.findtext("link") or guid)
    if _needs_torrent_url_prefix(link, guid, torrent_url):
        return f"{torrent_url}{guid}"
    return link


def _needs_torrent_url_prefix(
    link: str, guid: str, torrent_url: str | None
) -> bool:
    if not link or link.startswith(("http://", "https://")):
        return False
    return bool(guid and torrent_url)


def get_daily_api_hit_limit(tracker_cfg: dict[str, Any]) -> int:
    try:
        limit = int(tracker_cfg.get("daily_api_hit_limit", 0))
    except TypeError, ValueError:
        return 0
    return max(limit, 0)


def _get_api_hit_counter_filename(tracker: str) -> str:
    safe_tracker = "".join(
        char if char.isalnum() else "_" for char in tracker.strip().lower()
    )
    safe_tracker = safe_tracker.strip("_") or "default"
    return f"{safe_tracker}.json"


def _get_api_hit_counter_path(base_dir: str, tracker: str) -> Path:
    return (
        Path(base_dir)
        / "tmp"
        / API_HIT_COUNTER_DIRNAME
        / _get_api_hit_counter_filename(tracker)
    )


def _get_api_hit_counter_lock_path(base_dir: str, tracker: str) -> Path:
    return _get_api_hit_counter_path(base_dir, tracker).with_suffix(".lock")


def _lock_api_hit_counter_file(lock_file: BinaryIO) -> None:
    if msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore
        return
    raise RuntimeError("No supported file locking mechanism is available")


def _unlock_api_hit_counter_file(lock_file: BinaryIO) -> None:
    if msvcrt is not None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if fcntl is not None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore
        return


def _acquire_api_hit_counter_lock(lock_path: Path) -> BinaryIO:
    deadline = time.monotonic() + API_HIT_COUNTER_LOCK_TIMEOUT_SECONDS
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    while True:
        try:
            _lock_api_hit_counter_file(lock_file)
            return lock_file
        except BlockingIOError, OSError:
            if time.monotonic() >= deadline:
                lock_file.close()
                raise TimeoutError(
                    f"Timed out waiting for API hit counter lock: {lock_path}"
                ) from None
            time.sleep(API_HIT_COUNTER_LOCK_POLL_SECONDS)


def _release_api_hit_counter_lock(lock_file: BinaryIO) -> None:
    try:
        _unlock_api_hit_counter_file(lock_file)
    finally:
        lock_file.close()


def _write_api_hit_cache(cache_path: Path, tracker_hits: list[float]) -> None:
    temp_path = cache_path.with_suffix(
        f"{cache_path.suffix}.tmp.{os.getpid()}"
    )
    temp_path.write_text(json.dumps(tracker_hits, indent=2), encoding="utf-8")
    temp_path.replace(cache_path)


def _reserve_daily_api_hit_sync(
    base_dir: str, tracker: str, limit: int
) -> tuple[bool, int]:
    cache_path = _get_api_hit_counter_path(base_dir, tracker)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _acquire_api_hit_counter_lock(
        _get_api_hit_counter_lock_path(base_dir, tracker)
    )
    try:
        recent_hits = _recent_api_hits(
            _load_api_hit_cache(cache_path), time.time()
        )
        if len(recent_hits) >= limit:
            _write_api_hit_cache(cache_path, recent_hits)
            return False, len(recent_hits)
        recent_hits.append(time.time())
        _write_api_hit_cache(cache_path, recent_hits)
        return True, len(recent_hits)
    finally:
        _release_api_hit_counter_lock(lock_file)


def _load_api_hit_cache(cache_path: Path) -> list[Any]:
    if not cache_path.exists():
        return []
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _recent_api_hits(hits: list[Any], now: float) -> list[float]:
    cutoff = now - API_HIT_WINDOW_SECONDS
    recent: list[float] = []
    for hit in hits:
        value = _numeric_api_hit(hit)
        if value is not None and value >= cutoff:
            recent.append(value)
    return recent


def _numeric_api_hit(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


async def reserve_daily_api_hit(
    base_dir: str, tracker: str, limit: int
) -> tuple[bool, int]:
    return await asyncio.to_thread(
        _reserve_daily_api_hit_sync, base_dir, tracker, limit
    )
