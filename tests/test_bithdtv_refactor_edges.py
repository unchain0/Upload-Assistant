from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.trackers import bithdtv as bithdtv_module
from src.integrations.trackers.bithdtv import BitHDTV


def _config(*, announce: bool = True) -> dict[str, Any]:
    tracker: dict[str, Any] = {"api_key": " key "}
    if announce:
        tracker["my_announce_url"] = "https://tracker.invalid/announce"
    return {"DEFAULT": {}, "TRACKERS": {"BITHDTV": tracker}}


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "bithd-edges",
        "name": "Example 1080p WEB-DL H.264",
        "category": "MOVIE",
        "type": "WEBDL",
        "resolution": "1080p",
        "three_d": False,
        "bdinfo": {},
        "is_disc": "",
        "filelist": [],
        "path": "",
        "mediainfo": {"media": {"track": []}},
        "tvmaze_id": 321,
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt2"},
        "image_list": [],
        "tracker_status": {"BITHDTV": {}},
    }
    state.update(values)
    return Meta(state)


def test_bithdtv_tv_and_movie_mapping_edges(tmp_path: Path) -> None:
    tracker = BitHDTV(_config())
    episode = _meta(tmp_path, category="TV", type="HDTV", tv_pack=False)
    pack = _meta(tmp_path, category="TV", type="HDTV", tv_pack=True)

    assert tracker._is_tv_episode(episode)
    assert asyncio.run(tracker._sub_category_id(episode)) == "7"
    assert asyncio.run(tracker._sub_category_id(pack)) == "13"
    assert asyncio.run(tracker.get_cat_id(episode)) == "10"
    assert asyncio.run(tracker.get_cat_id(pack)) == "12"

    cases = (
        (Meta(type="DISC", name="Movie", three_d=False), "2"),
        (Meta(type="DISC", name="Movie", three_d=True), "46"),
        (Meta(type="REMUX", name="Movie", three_d=False), "2"),
        (Meta(type="REMUX", name="Movie", three_d=True), "45"),
        (Meta(type="HDTV", name="Movie"), "6"),
        (Meta(type="ENCODE", name="Movie", three_d=False), "1"),
        (Meta(type="ENCODE", name="Movie", three_d=True), "44"),
        (Meta(type="WEBDL", name="Movie"), "5"),
        (Meta(type="WEBRIP", name="Movie"), "5"),
        (Meta(type="OTHER", name="Movie"), "0"),
    )
    for meta, expected in cases:
        assert asyncio.run(tracker.get_type_movie_id(meta)) == expected

    assert asyncio.run(tracker.get_type_tv_id("REMUX")) == "11"
    assert asyncio.run(tracker.get_type_tv_pack_id("REMUX")) == "17"


def test_bithdtv_bd_dump_description_and_manual_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = BitHDTV(_config())
    meta = _meta(
        tmp_path,
        category="TV",
        is_disc="BDMV",
        bdinfo={"disc": True},
        image_list=[
            {
                "web_url": "https://image.invalid/view",
                "img_url": "https://image.invalid/raw.jpg",
            }
        ],
    )
    root = release_temp_dir(meta.base_dir, meta.uuid)
    (root / "BD_SUMMARY_00.txt").write_text("bd-summary", encoding="utf-8")

    assert asyncio.run(tracker._metadata_dumps(meta)) == (None, "bd-summary")
    assert tracker._short_mediainfo(meta) == ""
    assert tracker._external_url(meta) == "https://www.tvmaze.com/shows/321"

    monkeypatch.setattr(
        bithdtv_module,
        "base_description",
        lambda _meta: "[img=250]poster[/img]",
    )
    asyncio.run(tracker.edit_desc(meta))
    description = (root / "[BITHDTV]DESCRIPTION.txt").read_text(
        encoding="utf-8"
    )
    assert "[img=250x250]" in description
    assert "https://image.invalid/raw.jpg" in description
    assert asyncio.run(tracker.search_existing({})) == []


class _FalseResponse:
    def __bool__(self) -> bool:
        return False


class _BrokenResponse:
    def __bool__(self) -> bool:
        return True

    def json(self) -> object:
        raise ValueError("broken json")


def test_bithdtv_response_and_seed_failure_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = BitHDTV(_config())
    meta = _meta(tmp_path)
    monkeypatch.setattr(bithdtv_module.traceback, "print_exc", lambda: None)

    assert (
        tracker._parsed_response(
            meta,
            cast(Any, _FalseResponse()),
            {"api_key": "secret"},
        )
        is None
    )
    assert (
        tracker._parsed_response(
            meta,
            cast(Any, _BrokenResponse()),
            {"api_key": "secret"},
        )
        is None
    )
    assert tracker._view_url(None) is None
    assert tracker._view_url({"data": "bad"}) is None

    common = SimpleNamespace(create_torrent_ready_to_seed=AsyncMock())
    assert not asyncio.run(
        tracker._seed_uploaded_torrent(cast(Any, common), meta, None)
    )

    no_announce = BitHDTV(_config(announce=False))
    assert not asyncio.run(
        no_announce._seed_uploaded_torrent(
            cast(Any, common), meta, "https://tracker.invalid/view/1"
        )
    )
