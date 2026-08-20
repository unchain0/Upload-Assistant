from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.trackers import swarmazon
from src.integrations.trackers.swarmazon import Swarmazon


class FakeCommon:
    created: ClassVar[list[tuple[object, ...]]] = []
    ready: ClassVar[list[tuple[object, ...]]] = []

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    async def create_torrent_for_upload(self, *args: object, **_kwargs: object) -> None:
        type(self).created.append(args)

    async def create_torrent_ready_to_seed(self, *args: object, **_kwargs: object) -> None:
        type(self).ready.append(args)

    @classmethod
    def reset(cls) -> None:
        cls.created = []
        cls.ready = []


class Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def raise_for_status(self) -> None:
        return None


class Client:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[tuple[str, dict[str, object]]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> Response:
        return self._next("POST", url, kwargs)

    async def get(self, url: str, **kwargs: object) -> Response:
        return self._next("GET", url, kwargs)

    @classmethod
    def _next(cls, method: str, url: str, kwargs: dict[str, object]) -> Response:
        cls.calls.append((f"{method} {url}", kwargs))
        value = cls.queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


def _config() -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "SWARMAZON": {
                "api_key": "test-key",
                "announce_url": "https://tracker.invalid/announce",
            }
        },
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "swarmazon",
        "name": "Example Release",
        "title": "Example",
        "category": "MOVIE",
        "source": "Web",
        "type": "WEBDL",
        "resolution": "1080p",
        "imdb": 1234567,
        "imdb_id": 1234567,
        "season": "S01",
        "tv_pack": False,
        "mal_id": 0,
        "demographic": None,
        "bdinfo": {},
        "debug": False,
        "tracker_status": {},
        "image_list": [],
    }
    state.update(values)
    return Meta(state)


def _files(meta: Meta, *, bdinfo: bool = False) -> Path:
    root = release_temp_dir(meta.base_dir, meta.uuid)
    (root / "[SWARMAZON]DESCRIPTION.txt").write_text("description", encoding="utf-8")
    (root / "[SWARMAZON].torrent").write_bytes(b"torrent")
    if bdinfo:
        (root / "BD_SUMMARY_00.txt").write_text("bd summary", encoding="utf-8")
    else:
        (root / "MEDIAINFO.txt").write_text("mediainfo", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _doubles(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeCommon.reset()
    Client.reset()
    monkeypatch.setattr(swarmazon, "Common", FakeCommon)
    monkeypatch.setattr(swarmazon.httpx, "AsyncClient", Client)


def test_category_mapping_movie_tv_anime_and_unknown(tmp_path: Path) -> None:
    tracker = Swarmazon(_config())
    assert asyncio.run(tracker._upload_category(_meta(tmp_path))) == ("1", "1")
    assert asyncio.run(tracker._upload_category(_meta(tmp_path, category="TV", tv_pack=False))) == ("2", "5")
    assert asyncio.run(tracker._upload_category(_meta(tmp_path, category="TV", tv_pack=True))) == ("2", "6")
    assert asyncio.run(tracker._upload_category(_meta(tmp_path, category="MUSIC", mal_id=10, demographic="Seinen"))) == ("7", "28")
    assert asyncio.run(tracker._upload_category(_meta(tmp_path, category="MUSIC", mal_id=10, demographic="Unknown"))) == ("7", "47")
    assert asyncio.run(tracker._upload_category(_meta(tmp_path, category="MUSIC", mal_id=0))) == ("", "")


def test_media_dump_and_upload_file_readers(tmp_path: Path) -> None:
    tracker = Swarmazon(_config())
    regular = _meta(tmp_path, uuid="regular")
    _files(regular)
    assert asyncio.run(tracker._read_media_dump(regular)) == ("mediainfo", None)
    assert asyncio.run(tracker._read_upload_files(regular)) == ("description", b"torrent")

    disc = _meta(tmp_path, uuid="disc", bdinfo={"video": []})
    _files(disc, bdinfo=True)
    assert asyncio.run(tracker._read_media_dump(disc)) == (None, "bd summary")


def test_upload_success_creates_ready_torrent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Swarmazon(_config())
    meta = _meta(tmp_path)
    _files(meta)
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())
    Client.reset(Response({"success": True, "link": "https://tracker.invalid/1"}))

    assert asyncio.run(tracker.upload(meta))
    assert meta.tracker_status["SWARMAZON"]["status_message"] == "https://tracker.invalid/1"
    assert FakeCommon.ready


def test_upload_request_error_and_response_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Swarmazon(_config())
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())

    request = httpx.RequestError("offline", request=httpx.Request("POST", "https://swarmazon.club"))
    for uuid, response in (
        ("request", request),
        ("no-link", Response({"success": True})),
        ("failed", Response({"success": False})),
        ("bad-json", Response(ValueError("bad json"))),
        ("non-dict", Response(["bad"])),
    ):
        meta = _meta(tmp_path, uuid=uuid)
        _files(meta)
        Client.reset(response)
        assert not asyncio.run(tracker.upload(meta))


def test_debug_upload_does_not_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Swarmazon(_config())
    meta = _meta(tmp_path, debug=True)
    _files(meta)
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())

    assert asyncio.run(tracker.upload(meta))
    assert Client.calls == []
    assert meta.tracker_status["SWARMAZON"]["status_message"].startswith("Debug mode")
    assert len(FakeCommon.created) == 2


def test_disc_upload_appends_bd_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = Swarmazon(_config())
    meta = _meta(tmp_path, bdinfo={"video": []}, debug=True)
    _files(meta, bdinfo=True)
    monkeypatch.setattr(tracker, "edit_desc", AsyncMock())

    assert asyncio.run(tracker.upload(meta))


def test_edit_desc_skips_incomplete_images_and_keeps_complete_image(tmp_path: Path) -> None:
    tracker = Swarmazon(_config())
    meta = _meta(
        tmp_path,
        image_list=[
            {"web_url": "", "img_url": "https://img.invalid/skip"},
            {"web_url": "https://web.invalid/1", "img_url": "https://img.invalid/1"},
        ],
    )
    root = release_temp_dir(meta.base_dir, meta.uuid)

    asyncio.run(tracker.edit_desc(meta))

    text = (root / "[SWARMAZON]DESCRIPTION.txt").read_text(encoding="utf-8")
    assert "https://img.invalid/skip" not in text
    assert "https://img.invalid/1" in text


def test_search_existing_all_parameter_paths(tmp_path: Path) -> None:
    tracker = Swarmazon(_config())
    Client.reset(Response({"data": [{"name": "One"}, {"name": ""}, "bad"]}))
    assert asyncio.run(tracker.search_existing(_meta(tmp_path, imdb_id=0, category="MOVIE", title="Movie"))) == ["One"]
    assert Client.calls[-1][1]["params"]["filter"] == "Movie"

    Client.reset(Response({"data": [{"name": "Two"}]}))
    assert asyncio.run(tracker.search_existing(_meta(tmp_path, imdb_id=0, category="TV", title="Show", season="S02"))) == ["Two"]
    assert Client.calls[-1][1]["params"]["filter"] == "ShowS02"

    Client.reset(Response({"data": [{"name": "Three"}]}))
    assert asyncio.run(tracker.search_existing(_meta(tmp_path, imdb_id=1, category="TV", season="S03"))) == ["Three"]
    assert Client.calls[-1][1]["params"]["filter"] == "S03"

    Client.reset(Response({"data": [{"name": "Four"}]}))
    assert asyncio.run(tracker.search_existing(_meta(tmp_path, imdb_id=1, category="MOVIE", resolution="2160p"))) == ["Four"]
    assert Client.calls[-1][1]["params"]["filter"] == "2160p"


def test_search_name_guards() -> None:
    assert Swarmazon._search_names([]) == []
    assert Swarmazon._search_names({"data": "bad"}) == []
    assert Swarmazon._search_names({"data": [{"name": None}, {"name": "ok"}]}) == ["ok"]
