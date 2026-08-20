from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.trackers.greatposterwall import GreatPosterWall

gpw_module = importlib.import_module("src.integrations.trackers.greatposterwall")


def _config(**tracker_values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "api_key": "api-key",
        "announce_url": "https://tracker.invalid/announce",
        "exclusive": False,
        "anon": False,
    }
    tracker.update(tracker_values)
    return {"DEFAULT": {"tmdb_api": "test"}, "TRACKERS": {"GREATPOSTERWALL": tracker}}


def _tracker(**tracker_values: object) -> GreatPosterWall:
    return GreatPosterWall(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "video": str(root / "movie.mkv"),
        "filelist": [str(root / "movie.mkv")],
        "filename": "movie.mkv",
        "basename_no_ext": "Movie",
        "name": "Movie 2024 1080p WEB-DL-GROUP",
        "title": "Movie",
        "year": 2024,
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "resolution": "1080p",
        "container": "mkv",
        "video_encode": "x264",
        "video_codec": "AVC",
        "audio": "DD+ 5.1",
        "channels": "5.1",
        "bit_depth": "8",
        "hdr": "",
        "edition": "",
        "distributor": "",
        "dual_audio": False,
        "extras": False,
        "has_commentary": False,
        "manual_commentary": False,
        "hardcoded_subs": False,
        "subtitle_languages": ["English"],
        "audio_languages": ["English"],
        "language_checked": True,
        "unattended": False,
        "unattended_confirm": False,
        "tag": "-GROUP",
        "imdb": "tt1234567",
        "imdb_info": {
            "imdbID": "tt1234567",
            "type": "movie",
            "runtime": "120",
            "directors": ["Director"],
            "directors_id": ["nm0000001"],
            "writers": [],
            "writers_id": [],
            "stars": [],
            "stars_id": [],
        },
        "tmdb_id": 123,
        "tmdb_poster_path": "/poster.jpg",
        "tmdb_localized_data": {"zh-cn": {"main": {"overview": "Overview", "name": "电影"}}},
        "tmdb_directors": [],
        "overview": "Overview",
        "genres": ["Drama"],
        "keywords": [],
        "image_list": [],
        "scene": False,
        "sfx_subtitles": False,
        "personalrelease": False,
        "exclusive": False,
        "is_disc": "",
        "disctype": "",
        "dvd_size": "DVD9",
        "bdinfo": {},
        "youtube": "",
        "silent": False,
        "ua_name": "Upload Assistant",
        "ua_signature": "UA",
        "current_version": "1.0",
        "debug": False,
        "skipping": "",
        "tracker_status": {"GREATPOSTERWALL": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://greatposterwall.com/api.php",
    payload: Any | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if payload is not None:
        return httpx.Response(status, request=request, json=payload)
    return httpx.Response(status, request=request, text=text)


def test_greatposterwall_config_container_codec_and_trailer_edges() -> None:
    assert GreatPosterWall({"DEFAULT": {"tmdb_api": "test"}, "TRACKERS": "bad"})._tracker_config() == {}
    tracker = _tracker()
    assert tracker.get_container(_meta(container="m2ts")) == "m2ts"
    assert tracker.get_container(_meta(container="vob")) == "VOB IFO"
    assert tracker.get_container(_meta(container="avi")) == "AVI"
    assert tracker.get_codec(_meta(video_encode="", video_codec="unknown")) == "Other"
    tracker.tmdb_data = {"videos": {"results": [{"key": "tmdb-key"}]}}
    assert tracker.get_trailer(_meta(youtube="https://www.youtube.com/watch?v=fallback")) == "tmdb-key"
    tracker.tmdb_data = {"videos": {"results": []}}
    assert tracker._tmdb_trailer_key() == ""


@pytest.mark.asyncio
async def test_greatposterwall_load_cookies_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cookie = tmp_path / "cookie.txt"
    cookie.write_text("cookie", encoding="utf-8")
    monkeypatch.setattr("src.integrations.trackers.cookie_auth.find_cookie_file", lambda *_args, **_kwargs: str(cookie))
    tracker.common.parse_cookie_file = AsyncMock(return_value={"sid": "x"})  # type: ignore[method-assign]
    assert await tracker.load_cookies(_meta(tmp_path)) == {"sid": "x"}


@pytest.mark.asyncio
async def test_greatposterwall_rehost_no_images_and_failed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    await tracker.rehost_unapproved_images(_meta(image_list=[]))
    client = AsyncMock()
    error = httpx.RequestError("offline", request=httpx.Request("POST", tracker.base_url))
    client.post = AsyncMock(side_effect=error)
    assert await tracker._rehost_image_url(client, "https://lostimg.cc/a.png") == ""
    monkeypatch.setattr(tracker, "_rehost_image_url", AsyncMock(return_value=""))
    image = {"raw_url": "https://lostimg.cc/a.png"}
    assert await tracker._rehost_image_entry(client, image) == image


@pytest.mark.asyncio
async def test_greatposterwall_tags_and_release_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    item = _meta(genres=[], unattended=True, unattended_confirm=False)
    assert await tracker.get_tags(item) == ""
    assert item.skipping == "GREATPOSTERWALL"
    monkeypatch.setattr(gpw_module, "prompt_in_thread", AsyncMock(return_value="Drama, Action"))
    assert await tracker._prompt_genre_tags(_meta()) == "Drama, Action"
    assert not await tracker.get_additional_checks(_meta(type="REMUX", tag="-HDT"))
    assert not await tracker.get_additional_checks(_meta(type="WEBDL", tag="-EVO"))


@pytest.mark.asyncio
async def test_greatposterwall_search_existing_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_groupid", AsyncMock(return_value=False))
    assert await tracker.search_existing(_meta()) == []

    monkeypatch.setattr(tracker, "get_groupid", AsyncMock(return_value=True))
    assert await tracker.search_existing(_meta(imdb_info={})) == []

    monkeypatch.setattr(tracker, "load_cookies", AsyncMock(return_value={"sid": "x"}))
    monkeypatch.setattr(tracker, "_cookie_search_existing", AsyncMock(return_value=[{"name": "cookie"}]))
    assert await tracker.search_existing(_meta()) == [{"name": "cookie"}]

    monkeypatch.setattr(tracker, "load_cookies", AsyncMock(return_value=False))
    monkeypatch.setattr(tracker, "_api_search_existing", AsyncMock(return_value=[{"name": "api"}]))
    assert await tracker.search_existing(_meta()) == [{"name": "api"}]


def test_greatposterwall_api_and_html_duplicate_helpers() -> None:
    tracker = _tracker()
    assert tracker._api_duplicate_entries([]) == []
    assert tracker._api_duplicate_entries({"status": 500}) == []
    payload = {
        "status": 200,
        "response": [{"Name": "Movie", "Year": 2024, "Resolution": "1080p", "Source": "WEB", "Processing": "Encode", "Codec": "H.264"}],
    }
    assert tracker._api_duplicate_entries(payload)[0]["name"].startswith("Movie 2024 1080p")

    html = """
    <table id='torrent_table'>
      <tr class='TableTorrent-rowTitle'><td><a href='torrents.php?torrentid=7' data-tooltip='Release'>R</a></td><td class='TableTorrent-cellStatSize'>1 GB</td></tr>
    </table>
    """
    entry = tracker._html_duplicate_entries(html)[0]
    assert entry == {"name": "Release", "size": "1 GB", "link": f"{tracker.torrent_url}7"}
    row = BeautifulSoup("<tr></tr>", "html.parser").find("tr")
    assert row is not None and tracker._html_duplicate_entry(row) is None


@pytest.mark.asyncio
async def test_greatposterwall_cookie_search_calls_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    html = "<table id='torrent_table'><tr class='TableTorrent-rowTitle'><td><a href='?torrentid=7' data-tooltip='Release'>R</a></td></tr></table>"

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            return _response(text=html)

    monkeypatch.setattr(gpw_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(tracker, "get_slots", AsyncMock())
    GreatPosterWall.group_id = "11"
    result = await tracker._cookie_search_existing(_meta(), "tt123", {"sid": "x"})
    assert result and result[0]["name"] == "Release"
    tracker.get_slots.assert_awaited_once()


@pytest.mark.asyncio
async def test_greatposterwall_slots_success_and_http_error() -> None:
    tracker = _tracker()
    html = """
    <tr class='TableTorrent-rowEmptySlotNote' edition-id='3'>
      <td class='TableTorrent-cellEmptySlotNote'><i>empty slots: 2160p</i><i>Slot Encode</i></td>
    </tr>
    """
    client = AsyncMock()
    client.get = AsyncMock(return_value=_response(text=html))
    await tracker.get_slots(_meta(resolution="2160p"), client, "1")
    row = BeautifulSoup(html, "html.parser").find("tr")
    assert row is not None
    tracker._log_matching_slot(_meta(resolution="2160p"), row)
    assert tracker._slot_resolution(row) == "2160p"
    assert tracker._slot_names(row)

    client.get = AsyncMock(return_value=_response(status=500))
    assert await tracker._slots_response(client, "1") is None


@pytest.mark.asyncio
async def test_greatposterwall_media_info_success_and_missing(tmp_path: Path) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("MEDIAINFO", encoding="utf-8")
    assert await tracker.get_media_info(_meta(tmp_path)) == "MEDIAINFO"
    assert await tracker.get_media_info(_meta(tmp_path / "missing")) == ""


def test_greatposterwall_edition_disc_size_and_groupid_helpers() -> None:
    tracker = _tracker()
    assert tracker.get_edition(_meta(edition="")) == ""
    assert tracker.get_edition(_meta(edition="Unknown Edition")) == ""
    assert tracker._bluray_disc_size(_meta(disctype="", bdinfo={"size": 70})) == "BD100"
    assert tracker._bluray_disc_size(_meta(disctype="", bdinfo={"size": 55})) == "BD66"
    assert tracker._bluray_disc_size(_meta(disctype="", bdinfo={"size": 40})) == "BD50"
    assert tracker._bdinfo_size({"size": "bad"}) == 0
    assert tracker._groupid_from_payload({"status": 500}) == ""
    assert tracker._groupid_from_payload({"status": 200, "response": "bad"}) == ""
    assert tracker._groupid_from_payload({"status": 200, "response": {"ID": 9}}) == "9"


@pytest.mark.asyncio
async def test_greatposterwall_get_groupid_success(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_groupid_payload", AsyncMock(return_value={"status": 200, "response": {"ID": 99}}))
    assert await tracker.get_groupid(_meta())
    assert GreatPosterWall.group_id == "99"


@pytest.mark.asyncio
async def test_greatposterwall_groupid_payload_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    request = httpx.Request("GET", tracker.base_url)
    response = httpx.Response(500, request=request)

    class Client:
        error: Exception

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> httpx.Response:
            raise self.error

    client = Client()
    monkeypatch.setattr(gpw_module.httpx, "AsyncClient", lambda *_args, **_kwargs: client)
    client.error = httpx.HTTPStatusError("bad", request=request, response=response)
    assert await tracker._groupid_payload(tracker.base_url) == {}
    client.error = httpx.RequestError("offline", request=request)
    assert await tracker._groupid_payload(tracker.base_url) == {}
    client.error = ValueError("json")
    assert await tracker._groupid_payload(tracker.base_url) == {}


def test_greatposterwall_identifier_legacy_and_media_flags() -> None:
    tracker = _tracker()
    assert tracker._additional_identifier(_meta(imdb_info={}, tmdb_id=123)) == ("tmdb", "123")
    assert tracker._additional_identifier(_meta(imdb_info={}, tmdb_id=None)) == ("manual", "")
    data: dict[str, Any] = {}
    tracker._append_legacy_identifiers(data, _meta())
    assert data["imdb"] == "tt1234567" and data["tmdb"] == "123"
    flags = tracker.get_media_flags(_meta(audio="DTS:X Atmos", channels="7.1", bit_depth="10", hdr="DV HDR10+"))
    assert flags["dts_x"] == "on"
    assert flags["dolby_atmos"] == "on"
    assert flags["dolby_vision"] == "on"
    assert flags["hdr10plus"] == "on"
    plain: dict[str, str] = {}
    tracker._append_bit_depth_flag(plain, "", 10)
    assert plain["10_bit"] == "on"


def test_greatposterwall_full_credit_and_contributor_helpers() -> None:
    tracker = _tracker()
    credits = tracker._empty_credit_data()
    tracker._append_full_credit(credits, {"role": "director", "imdbId": "nm1", "name": "Director"})
    tracker._append_full_credit(credits, {"role": "cast", "imdbId": "nm2", "name": "Actor", "character": "Hero"})
    tracker._append_full_credit(credits, {"role": "unknown", "imdbId": "nm3", "name": "X"})
    assert credits["directors"] == ["Director"]
    assert credits["characters"] == {"nm2": "Hero"}
    assert not tracker._append_credit_identity(credits, "directors", "director_ids", "nm1", "Duplicate")

    post = tracker._new_artist_payload("nm1", "Director", "导演")
    tracker._append_contributors(post, ["Writer"], ["nm3"], "2")
    tracker._append_contributor(post, "Actor", "nm4", "6", "")
    assert "nm3" in post["artist_ids[]"] and "nm4" in post["artist_ids[]"]
    assert "Unknown" in post["characters[]"]


@pytest.mark.asyncio
async def test_greatposterwall_artist_data_and_director_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    credits = {
        "directors": ["Director"],
        "director_ids": ["nm1"],
        "writers": ["Writer"],
        "writer_ids": ["nm2"],
        "stars": ["Actor"],
        "star_ids": ["nm3"],
        "characters": {"nm3": "Hero"},
    }
    monkeypatch.setattr(tracker, "_artist_credit_data", AsyncMock(return_value=credits))
    result = await tracker._get_artist_data(_meta())
    assert result["artist_ids[]"] == ["nm1", "nm2", "nm3"]

    item = _meta(unattended=True, unattended_confirm=False)
    assert await tracker._director_identity(item, tracker._empty_credit_data()) is None
    assert item.skipping == "GREATPOSTERWALL"


@pytest.mark.asyncio
async def test_greatposterwall_prompt_person_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    answers = iter(("bad", "nm123"))

    async def prompt_id(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    monkeypatch.setattr(gpw_module, "prompt_in_thread", prompt_id)
    assert await tracker._prompt_person_id() == "nm123"

    names = iter(("", "Director"))

    async def prompt_name(*_args: object, **_kwargs: object) -> str:
        return next(names)

    monkeypatch.setattr(gpw_module, "prompt_in_thread", prompt_name)
    assert await tracker._prompt_person_name() == "Director"


@pytest.mark.asyncio
async def test_greatposterwall_movie_info_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert await tracker._fetch_gpw_movie_info(_meta(), "", "") == {}
    assert tracker._best_movie_info([]) == {}
    assert tracker._successful_movie_info_response([]) == {}
    assert tracker._successful_movie_info_response({"status": "failure"}) == {}
    assert tracker._successful_movie_info_response({"status": 200, "response": {"FullCredits": [1]}}) == {"FullCredits": [1]}

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return _response(payload=[])

    monkeypatch.setattr(gpw_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    assert await tracker._movie_info_candidate(_meta(), "https://x", {}, None) == {}


@pytest.mark.asyncio
async def test_greatposterwall_fetch_data_group_and_new_movie(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "load_localized_data", AsyncMock())
    monkeypatch.setattr(tracker, "get_groupid", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_release_upload_fields", AsyncMock(return_value={"base": 1}))
    monkeypatch.setattr(tracker, "get_ch_dubs", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "get_media_flags", lambda _meta: {"flag": "on"})
    GreatPosterWall.group_id = "77"
    data = await tracker.fetch_data(_meta())
    assert data["groupid"] == "77" and data["chinese_dubbed"] == "on"

    GreatPosterWall.group_id = ""
    monkeypatch.setattr(tracker, "get_additional_data", AsyncMock(return_value={"new": 1}))
    assert await tracker._group_or_new_movie_data(_meta()) == {"new": 1}


@pytest.mark.asyncio
async def test_greatposterwall_upload_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    assert not await tracker.upload(_meta(skipping="GREATPOSTERWALL"))

    monkeypatch.setattr(tracker, "fetch_data", AsyncMock(return_value={"x": 1}))
    monkeypatch.setattr(tracker, "_debug_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=True))

    monkeypatch.setattr(tracker, "_live_upload", AsyncMock(return_value=True))
    assert await tracker.upload(_meta(debug=False))


@pytest.mark.asyncio
async def test_greatposterwall_live_upload_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    item = _meta()
    for error in (
        httpx.TimeoutException("timeout"),
        httpx.RequestError("offline", request=httpx.Request("POST", tracker.base_url)),
        RuntimeError("unexpected"),
    ):
        monkeypatch.setattr(tracker, "_upload_response", AsyncMock(side_effect=error))
        assert not await tracker._live_upload(item, {})
        assert item.tracker_status["GREATPOSTERWALL"]["status_message"]


@pytest.mark.asyncio
async def test_greatposterwall_upload_response_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    item = _meta()
    bad = _response(text="not-json")
    assert tracker._decoded_upload_payload(item, bad) is None
    invalid = _response(payload=[])
    assert tracker._decoded_upload_payload(item, invalid) is None

    monkeypatch.setattr(tracker, "_record_successful_upload", AsyncMock())
    success = _response(payload={"status": "success", "response": {"torrent_id": 7}})
    assert await tracker._handle_upload_response(item, success)
    tracker._record_successful_upload.assert_awaited_once_with(item, "7")

    failure = _response(payload={"status": "failure", "error": "bad upload"})
    assert not await tracker._handle_upload_response(item, failure)
    assert "bad upload" in item.tracker_status["GREATPOSTERWALL"]["status_message"]

    tracker._record_failed_upload(item, {"error": "The exact same torrent file already exists on the site"})
    assert "already exists" in item.tracker_status["GREATPOSTERWALL"]["status_message"]
    assert tracker._torrent_id_mapping([{"torrent_id": 8}]) == {"torrent_id": 8}


@pytest.mark.asyncio
async def test_greatposterwall_record_successful_upload() -> None:
    tracker = _tracker()
    tracker.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    item = _meta()
    await tracker._record_successful_upload(item, "9")
    assert item.tracker_status["GREATPOSTERWALL"]["torrent_id"] == "9"
    tracker.common.create_torrent_ready_to_seed.assert_awaited_once()


def test_greatposterwall_get_tags_with_genres() -> None:
    assert __import__("asyncio").run(_tracker().get_tags(_meta(genres=["Science Fiction"]))) == "science.fiction"


def test_greatposterwall_html_duplicate_entry_rejects_nonstring_tooltip() -> None:
    tracker = _tracker()
    row = BeautifulSoup("<tr><td><a href='?torrentid=7'>R</a></td></tr>", "html.parser").find("tr")
    assert row is not None
    assert tracker._html_duplicate_entry(row) is None


@pytest.mark.asyncio
async def test_greatposterwall_get_slots_none_and_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_slots_response", AsyncMock(return_value=None))
    await tracker.get_slots(_meta(), AsyncMock(), "1")
    row = BeautifulSoup("<tr class='TableTorrent-rowEmptySlotNote' edition-id='3'><td><i>Slot Encode</i></td></tr>", "html.parser").find("tr")
    assert row is not None
    tracker._log_matching_slot(_meta(resolution="1080p"), row)


def test_greatposterwall_slot_resolution_from_cell() -> None:
    row = BeautifulSoup("<tr><td class='TableTorrent-cellEmptySlotNote'><i>empty slots: 720p</i></td></tr>", "html.parser").find("tr")
    assert row is not None
    assert GreatPosterWall._slot_resolution(row) == "720p"


@pytest.mark.asyncio
async def test_greatposterwall_media_info_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    path = root / "MEDIAINFO_CLEANPATH.txt"
    path.write_text("MEDIAINFO", encoding="utf-8")
    monkeypatch.setattr(gpw_module.aiofiles, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("broken")))
    assert await tracker.get_media_info(_meta(tmp_path)) == ""


def test_greatposterwall_additional_identifier_prefers_imdb() -> None:
    assert _tracker()._additional_identifier(_meta()) == ("imdb", "tt1234567")


@pytest.mark.asyncio
async def test_greatposterwall_artist_data_without_director(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_artist_credit_data", AsyncMock(return_value=tracker._empty_credit_data()))
    monkeypatch.setattr(tracker, "_director_identity", AsyncMock(return_value=None))
    assert await tracker._get_artist_data(_meta()) == {}


def test_greatposterwall_full_credit_data_and_duplicate_append() -> None:
    tracker = _tracker()
    data = tracker._full_credit_data({"FullCredits": [{"role": "director", "imdbId": "nm1", "name": "Director"}]})
    assert data["directors"] == ["Director"]
    tracker._append_full_credit(data, {"role": "director", "imdbId": "nm1", "name": "Director Again"})
    assert data["directors"] == ["Director"]


def test_greatposterwall_append_contributor_duplicate() -> None:
    tracker = _tracker()
    post = tracker._new_artist_payload("nm1", "Director", "")
    tracker._append_contributor(post, "Director", "nm1", "2", "")
    assert post["artist_ids[]"] == ["nm1"]


@pytest.mark.asyncio
async def test_greatposterwall_movie_info_candidate_handles_payload_error(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            response = _response(payload={"status": 200, "response": {}})
            response.json = lambda: (_ for _ in ()).throw(ValueError("bad"))  # type: ignore[method-assign]
            return response

    monkeypatch.setattr(gpw_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    assert await tracker._movie_info_candidate(_meta(), "https://x", {}, None) == {}


@pytest.mark.asyncio
async def test_greatposterwall_upload_stops_if_fetch_marks_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]

    async def fetch(meta: Meta) -> dict[str, Any]:
        meta.skipping = "GREATPOSTERWALL"
        return {}

    monkeypatch.setattr(tracker, "fetch_data", fetch)
    assert not await tracker.upload(_meta())


@pytest.mark.asyncio
async def test_greatposterwall_handle_upload_response_none_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_decoded_upload_payload", lambda *_args: None)
    assert not await tracker._handle_upload_response(_meta(), _response())
