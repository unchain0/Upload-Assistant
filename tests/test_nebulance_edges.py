from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.nebulance import Nebulance


def _config() -> dict:
    return {"TRACKERS": {"NEBULANCE": {"api_key": "test-key", "search_max_pages": 3}}}


def _tracker() -> Nebulance:
    return Nebulance(_config())


def _response(status: int, payload: object | None = None, *, text: str = "") -> httpx.Response:
    request = httpx.Request("GET", "https://nebulance.io/api.php")
    if payload is None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=payload, request=request)


@pytest.mark.asyncio
async def test_nebulance_upload_catches_submission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    monkeypatch.setattr(tracker, "_read_media_dump", AsyncMock(return_value="mi"))
    monkeypatch.setattr(tracker, "_upload_files", AsyncMock(return_value={}))
    monkeypatch.setattr(tracker, "_upload_data", AsyncMock(return_value={}))
    monkeypatch.setattr(tracker, "_post_upload", AsyncMock(side_effect=RuntimeError("offline")))
    meta = Meta(debug=False, tracker_status={})

    assert not await tracker.upload(meta)
    assert "offline" in meta.tracker_status["NEBULANCE"]["status_message"]


def test_nebulance_upload_response_json_error_and_success_ids() -> None:
    tracker = _tracker()
    status: dict = {}
    assert not tracker._handle_upload_response(_response(200, text="not-json"), status)
    assert "json decode error" in status["status_message"]

    status = {}
    assert tracker._record_upload_success(["unexpected"], status)
    assert status["status_message"] == ["unexpected"]

    status = {}
    payload = {"link": "https://nebulance.io/torrents.php?id=123"}
    assert tracker._record_upload_success(payload, status)
    assert status["torrent_id"] == "123"


@pytest.mark.asyncio
async def test_nebulance_additional_check_rejections() -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    assert not await tracker.get_additional_checks(Meta(category="MOVIE", tvmaze_id=0, unattended=False))
    assert not await tracker.get_additional_checks(Meta(category="TV", valid_mi=False, is_disc=""))
    assert not await tracker.get_additional_checks(Meta(category="TV", valid_mi=True, is_disc="DVD", unattended=False))

    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not await tracker.get_additional_checks(Meta(category="TV", valid_mi=True, is_disc=""))


@pytest.mark.asyncio
async def test_nebulance_tv_movie_unattended_rejected() -> None:
    tracker = _tracker()
    meta = Meta(category="MOVIE", tvmaze_id=123, unattended=True, unattended_confirm=False)
    assert not await tracker.get_additional_checks(meta)


def test_nebulance_search_params_include_positive_season() -> None:
    params = _tracker()._search_params(Meta(season_int=2, tvmaze_episode_data={}, tvmaze_id=0, imdb_id=0, title="Show", resolution="1080p"))
    assert params["season"] == 2
    assert params["series"] == "Show"


def test_nebulance_terminal_page_error_and_error_message_guards() -> None:
    terminal = _response(400, {"error": {"message": "Page out of range; valid pages are 0-1"}})
    assert Nebulance._is_terminal_page_error(terminal, 2)

    invalid_json = _response(400, text="broken")
    assert Nebulance._error_message(invalid_json) == ""

    non_mapping = _response(400, ["bad"])
    assert Nebulance._error_message(non_mapping) == ""


def test_nebulance_raw_search_items_guards() -> None:
    assert Nebulance._raw_search_items([]) == []
    assert Nebulance._raw_search_items({"result": "bad"}) == []


class _SearchClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.responses = [
            _response(
                200,
                {
                    "items": [
                        {
                            "tags": ["1080p"],
                            "rls_name": "Show.S01.1080p",
                            "file_list": ["episode.mkv"],
                            "size": 123,
                            "group_id": 4,
                            "download": "https://download.invalid/4",
                        }
                    ]
                },
            ),
            _response(400, {"error": {"message": "Page out of range; valid pages are 0-0"}}),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> httpx.Response:
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_nebulance_search_stops_on_terminal_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.integrations.trackers.nebulance.httpx.AsyncClient", _SearchClient)
    meta = Meta(season_int=1, tvmaze_episode_data={}, tvmaze_id=10, imdb_id=0, title="Show", resolution="1080p")
    dupes = await _tracker().search_existing(meta)
    assert len(dupes) == 1
    assert dupes[0]["name"] == "Show.S01.1080p"
