import asyncio
from pathlib import Path
from typing import Any, ClassVar

from src.domain_models.release import Meta
from src.integrations.trackers.USENET.nzbgeek import NZBGeek


class _Response:
    status_code = 200
    text = '{"response":{"@attributes":{"API":"OK","NFO":"OK","REGISTER":"OK"}}}'


class _Client:
    request: ClassVar[dict[str, Any]] = {}

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.request = {"url": url, **kwargs}
        type(self).request = self.request
        return _Response()


def test_nzbgeek_uses_documented_submission_fields(monkeypatch: Any, tmp_path: Path) -> None:
    nzb_path = tmp_path / "release.nzb"
    nzb_path.write_text("<nzb />", encoding="utf-8")
    nfo_dir = tmp_path / "tmp" / "test"
    nfo_dir.mkdir(parents=True)
    (nfo_dir / "MEDIAINFO_CLEANPATH.txt").write_text("MediaInfo", encoding="utf-8")
    meta = Meta(
        nzb_path=str(nzb_path),
        base_dir=str(tmp_path),
        uuid="test",
        category="MOVIE",
        resolution="2160p",
        basename_no_ext="release",
        tracker_status={},
    )
    tracker = NZBGeek({"TRACKERS": {"NZBGEEK": {"api_key": "secret"}}, "USENET": {}})
    monkeypatch.setattr("src.integrations.trackers.USENET.nzbgeek.httpx.AsyncClient", lambda **_kwargs: _Client())

    assert asyncio.run(tracker.upload(meta)) is True
    assert _Client.request["url"] == "https://api.nzbgeek.info/submit"
    assert _Client.request["params"] == {"apikey": "secret", "cat": "2045"}
    assert set(_Client.request["files"]) == {"nzb", "nfo"}
    assert meta.tracker_status["NZBGEEK"]["status_message"] == "Upload successful"


def test_nzbgeek_requires_all_success_attributes() -> None:
    assert NZBGeek._successful_response('{"response":{"@attributes":{"API":"OK","REGISTER":"OK"}}}', False)
    assert not NZBGeek._successful_response('{"response":{"@attributes":{"API":"OK","REGISTER":"OK"}}}', True)
    assert not NZBGeek._successful_response("not json", False)


def test_nzbgeek_maps_supported_categories() -> None:
    tracker = NZBGeek({"TRACKERS": {"NZBGEEK": {}}})
    assert tracker.get_category_id(Meta(category="TV", resolution="1080p")) == "5040"
    assert tracker.get_category_id(Meta(category="TV", resolution="1080p", anime=True)) == "5070"
    assert tracker.get_category_id(Meta(category="MUSIC", format="FLAC")) == "3040"
    assert tracker.get_category_id(Meta(category="BOOK")) == "7020"
