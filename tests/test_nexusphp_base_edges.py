from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP import NEXUSPHP

nexus_module = importlib.import_module("src.integrations.trackers.NEXUSPHP")


class FakeNexus(NEXUSPHP):
    base_url = "https://nexus.invalid"
    torrent_url = "https://nexus.invalid/details.php?id="
    upload_url = "https://nexus.invalid/takeupload.php"
    source_flag = "FAKE"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, "TEST")

    def get_category(self, _meta: Meta) -> int:
        return 101

    def get_type(self, _meta: Meta) -> int:
        return 202

    def get_codec(self, _meta: Meta) -> int:
        return 303

    def get_resolution(self, _meta: Meta) -> int:
        return 404

    def get_group_tag(self, _meta: Meta) -> int:
        return 505

    def get_checkboxes(self, _meta: Meta) -> list[str]:
        return ["tag-a", "tag-b"]

    def get_audio_codec(self, _meta: Meta) -> int:
        return 606

    def get_region(self, _meta: Meta) -> int:
        return 707

    def get_container(self, _meta: Meta) -> int:
        return 808


def _config(
    *,
    announce_url: object = "https://nexus.invalid/announce",
    anon: bool = False,
) -> dict[str, Any]:
    return {
        "DEFAULT": {"tmdb_api": "test"},
        "TRACKERS": {"TEST": {"announce_url": announce_url, "anon": anon}},
    }


def _tracker(**kwargs: object) -> FakeNexus:
    return FakeNexus(_config(**kwargs))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base,
        "uuid": "release",
        "name": "Example Movie 2024 1080p",
        "title": "Example Movie",
        "category": "MOVIE",
        "type": "ENCODE",
        "resolution": "1080p",
        "year": 2024,
        "season": "S01",
        "episode": "E02",
        "tv_pack": False,
        "is_disc": "",
        "tmdb_localized_data": {"zh-cn": {"main": {"name": "示例电影"}}},
        "imdb_info": {},
        "douban_rating": "",
        "douban_votes": "",
        "douban_id": 0,
        "runtime": 120,
        "ua_signature": "UA",
        "anon": 0,
        "imdb_id": 0,
        "tracker_status": {"TEST": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    text: str = "",
    *,
    status: int = 200,
    url: str = "https://nexus.invalid/torrents.php",
) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_nexus_load_localized_data_success_and_failure() -> None:
    tracker = _tracker()
    await tracker.load_localized_data(_meta())
    assert tracker.tmdb_data == {"name": "示例电影"}

    with pytest.raises(RuntimeError, match="Missing TMDB localized data"):
        await tracker.load_localized_data(_meta(tmdb_localized_data={}))


@pytest.mark.asyncio
async def test_nexus_search_requires_announce_and_cookie_login() -> None:
    no_announce = _tracker(announce_url="   ")
    meta = _meta()
    assert await no_announce.search_existing(meta) == []
    assert meta.skipping == "TEST"

    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=httpx.Cookies({"sid": "1"})
    )  # type: ignore[method-assign]
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]
    tracker.session.get = AsyncMock(
        return_value=_response(
            "login.php", url="https://nexus.invalid/login.php"
        )
    )  # type: ignore[method-assign]
    meta = _meta()
    assert await tracker.search_existing(meta) == []
    assert meta.skipping == "TEST"
    tracker.cookie_validator.handle_validation_failure.assert_awaited_once()


def test_nexus_search_params_and_names() -> None:
    tracker = _tracker()
    movie = _meta()
    params = tracker._search_params(movie)
    assert params["cat101"] == "1"
    assert params["medium202"] == "1"
    assert params["standard404"] == "1"
    assert params["search"] == "Example Movie 2024"

    assert (
        tracker._search_name(
            _meta(category="TV", title="Show", tv_pack=True, season="S02")
        )
        == "Show S02"
    )
    assert (
        tracker._search_name(
            _meta(
                category="TV",
                title="Show",
                tv_pack=False,
                season="S02",
                episode="E03",
            )
        )
        == "Show S02E03"
    )
    assert (
        tracker._search_name(
            _meta(
                category="TV",
                title="Show",
                tv_pack=False,
                season="",
                episode="",
            )
        )
        == "Show"
    )


def test_nexus_search_html_helpers() -> None:
    html = """
    <table class='torrents'>
      <tr><th>header</th></tr>
      <tr><td><table class='torrentname'><tr><td><a href='details.php?id=77&hit=1' title='Release Title'>Release</a></td></tr></table></td></tr>
      <tr><td>no nested table</td></tr>
    </table>
    """
    rows = NEXUSPHP._search_rows(html)
    assert len(rows) == 2
    link = NEXUSPHP._torrent_name_link(rows[0])
    assert link is not None
    assert NEXUSPHP._torrent_id_from_link(link) == "77"
    assert NEXUSPHP._torrent_name(link) == "Release Title"
    assert NEXUSPHP._torrent_name_link(rows[1]) is None
    assert NEXUSPHP._search_rows("<html></html>") == []

    list_link = BeautifulSoup(
        '<a href="details.php?id=7" title="Release"></a>', "html.parser"
    ).find("a")
    assert list_link is not None
    list_link.attrs["href"] = ["details.php?id=8"]
    list_link.attrs["title"] = ["One", "Two"]
    assert NEXUSPHP._torrent_id_from_link(list_link) == "8"
    assert NEXUSPHP._torrent_name(list_link) == "One Two"

    bad_link = BeautifulSoup('<a href="bad.php">bad</a>', "html.parser").find(
        "a"
    )
    assert bad_link is not None
    assert NEXUSPHP._torrent_id_from_link(bad_link) == ""


@pytest.mark.asyncio
async def test_nexus_parse_search_results_with_bdinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    html = """
    <table class='torrents'>
      <tr><th>header</th></tr>
      <tr><td><table class='torrentname'><tr><td><a href='details.php?id=77' title='Release'>Release</a></td></tr></table></td></tr>
      <tr><td><table class='torrentname'><tr><td><a href='bad.php' title='Bad'>Bad</a></td></tr></table></td></tr>
    </table>
    """
    monkeypatch.setattr(
        tracker, "get_dupe_bdinfo", AsyncMock(return_value="BDINFO")
    )
    results = await tracker._parse_search_results(_meta(is_disc="BDMV"), html)
    assert results == [
        {
            "name": "Release",
            "link": "https://nexus.invalid/details.php?id=77",
            "bd_info": "BDINFO",
        }
    ]

    monkeypatch.setattr(tracker, "get_dupe_bdinfo", AsyncMock(return_value=""))
    row = NEXUSPHP._search_rows(html)[0]
    assert await tracker._search_row(_meta(is_disc="BDMV"), row) == {
        "name": "Release",
        "link": "https://nexus.invalid/details.php?id=77",
    }


@pytest.mark.asyncio
async def test_nexus_search_existing_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    html = """
    <table class='torrents'>
      <tr><th>header</th></tr>
      <tr><td><table class='torrentname'><tr><td><a href='details.php?id=77' title='Release'>Release</a></td></tr></table></td></tr>
    </table>
    """
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "_search_response", AsyncMock(return_value=_response(html))
    )
    assert await tracker.search_existing(_meta()) == [
        {"name": "Release", "link": "https://nexus.invalid/details.php?id=77"}
    ]


@pytest.mark.asyncio
async def test_nexus_get_dupe_bdinfo_success_empty_and_error() -> None:
    tracker = _tracker()
    success_html = (
        '<div class="nexus-media-info-raw"><pre>DISC INFO</pre></div>'
    )
    tracker.session.get = AsyncMock(
        return_value=_response(
            success_html, url="https://nexus.invalid/details.php?id=1"
        )
    )  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo("1") == "DISC INFO"

    tracker.session.get = AsyncMock(return_value=_response("<html></html>"))  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo("2") == ""

    tracker.session.get = AsyncMock(
        side_effect=httpx.RequestError(
            "offline", request=httpx.Request("GET", "https://nexus.invalid")
        )
    )  # type: ignore[method-assign]
    assert await tracker.get_dupe_bdinfo("3") == ""


@pytest.mark.asyncio
async def test_nexus_validate_credentials_paths() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    assert not await tracker.validate_credentials(_meta())
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=httpx.Cookies({"sid": "1"})
    )  # type: ignore[method-assign]
    assert await tracker.validate_credentials(_meta())
    assert tracker.session.cookies.get("sid") == "1"


def _tmdb_movie_data() -> dict[str, Any]:
    return {
        "name": "示例电影",
        "original_name": "Original",
        "poster_path": "/poster.jpg",
        "release_date": "2024-03-01",
        "production_countries": [{"name": "中国"}],
        "genres": [{"name": "剧情"}],
        "spoken_languages": [{"name": "中文"}],
        "runtime": 123,
        "credits": {
            "crew": [
                {
                    "job": "Director",
                    "name": "导演",
                    "original_name": "Director",
                },
                {"job": "Writer", "name": "编剧", "original_name": "Writer"},
            ],
            "cast": [
                {
                    "name": "演员一",
                    "original_name": "Actor One",
                    "character": "Hero",
                },
                {"name": "演员二", "original_name": "Actor Two"},
            ],
        },
        "overview": "剧情简介",
    }


@pytest.mark.asyncio
async def test_nexus_standard_desc_movie_and_empty() -> None:
    tracker = _tracker()
    assert await tracker.standard_desc(_meta()) == ""
    tracker.tmdb_data = _tmdb_movie_data()
    text = await tracker.standard_desc(
        _meta(
            imdb_info={
                "rating": "8.5",
                "votes": "1000",
                "imdb_url": "https://imdb.invalid/title/tt1",
            },
            douban_rating="9.0",
            douban_votes="2000",
            douban_id=123,
        )
    )
    assert "示例电影" in text
    assert "Original" in text
    assert "2024" in text
    assert "中国" in text
    assert "IMDb评分" in text
    assert "豆瓣评分" in text
    assert "导演 Director" in text
    assert "演员一 Actor One (饰 Hero)" in text
    assert "剧情简介" in text


@pytest.mark.asyncio
async def test_nexus_standard_desc_tv_runtime_branches() -> None:
    tracker = _tracker()
    tracker.tmdb_data = {
        "name": "示例剧",
        "original_name": "示例剧",
        "first_air_date": "2020-01-01",
        "seasons": [
            {"season_number": "S01", "name": "第一季", "episode_count": 10}
        ],
        "episode_run_time": [45],
        "credits": {},
    }
    text = await tracker.standard_desc(
        _meta(category="TV", season="S01", year=2020)
    )
    assert "示例剧 第一季" in text
    assert "集　　数　10" in text
    assert "片　　长　45分钟" in text

    tracker.tmdb_data["episode_run_time"] = []
    tracker.tmdb_data["last_episode_to_air"] = {"runtime": 50}
    assert NEXUSPHP._tv_runtime(tracker.tmdb_data) == 50
    assert NEXUSPHP._tv_runtime({"last_episode_to_air": "bad"}) is None


def test_nexus_standard_desc_helpers_remaining_branches() -> None:
    lines: list[str] = []
    NEXUSPHP._append_poster(lines, {})
    assert lines == []
    assert (
        NEXUSPHP._localized_title(_meta(category="MOVIE"), {"name": "Movie"})
        == "Movie"
    )
    assert (
        NEXUSPHP._season_title("Show 第 S01 季", "S01", []) == "Show 第 S01 季"
    )
    assert NEXUSPHP._season_info("bad", "S01") == {}
    assert NEXUSPHP._release_date({}) == ""

    lines = []
    NEXUSPHP._append_original_name(lines, {"original_name": "Same"}, "Same")
    assert lines == []
    NEXUSPHP._append_year(lines, _meta(year=None), "")
    assert lines == []
    NEXUSPHP._append_release_date(lines, "", [])
    assert lines == []
    NEXUSPHP._append_release_date(lines, "2024-01-01", [])
    assert lines[-1] == "◎上映日期　2024-01-01"
    assert NEXUSPHP._named_values("bad") == []
    NEXUSPHP._append_joined(lines, "label", [])
    NEXUSPHP._append_rating(lines, "label", "", "")

    lines = []
    NEXUSPHP._append_runtime(lines, _meta(category="MOVIE", runtime=None), {})
    assert lines == []
    NEXUSPHP._append_tv_runtime(
        lines, _meta(category="TV", season=""), {"episode_run_time": []}
    )
    assert lines == ["◎季　　数　"]

    assert NEXUSPHP._crew_names(
        ["bad", {"job": "Director", "name": "D", "original_name": "DO"}],
        {"Director"},
    ) == ["D DO"]
    assert NEXUSPHP._cast_people("bad") == []
    assert NEXUSPHP._cast_prefix(0) == "◎主　　演　"
    assert NEXUSPHP._cast_prefix(1) == "　　　　　　"
    assert NEXUSPHP._actor_name({"name": "A", "original_name": "AO"}) == "A AO"
    lines = []
    NEXUSPHP._append_overview(lines, {})
    assert lines == []


@pytest.mark.asyncio
async def test_nexus_get_description_uses_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Builder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def general_description_generator(
            self, meta: Meta, **kwargs: object
        ) -> str:
            assert meta.nexusphp_description == "localized"
            assert kwargs["logo"] is False
            return "description"

    tracker = _tracker()
    monkeypatch.setattr(nexus_module, "DescriptionBuilder", Builder)
    monkeypatch.setattr(
        tracker, "standard_desc", AsyncMock(return_value="localized")
    )
    assert await tracker.get_description(_meta()) == {"descr": "description"}


def test_nexus_base_mapping_methods_and_urls() -> None:
    base = NEXUSPHP(_config(), "TEST")
    with pytest.raises(NotImplementedError):
        base.get_category(_meta())
    with pytest.raises(NotImplementedError):
        base.get_type(_meta())
    with pytest.raises(NotImplementedError):
        base.get_codec(_meta())
    with pytest.raises(NotImplementedError):
        base.get_resolution(_meta())
    assert base.get_group_tag(_meta()) == 0
    assert base.get_checkboxes(_meta()) == []
    assert base.get_audio_codec(_meta()) == 0
    assert base.get_region(_meta()) == 0
    assert base.get_container(_meta()) == 0
    assert (
        base.get_douban_url(_meta(douban_id=123))
        == "https://movie.douban.com/subject/123/"
    )
    assert base.get_douban_url(_meta(douban_id=0)) == ""
    assert (
        base.get_imdb_url(
            _meta(imdb_id=1, imdb_info={"imdb_url": "https://imdb.invalid"})
        )
        == "https://imdb.invalid"
    )
    assert base.get_imdb_url(_meta(imdb_id=0)) == ""


@pytest.mark.asyncio
async def test_nexus_technical_info_and_data_wrappers(tmp_path: Path) -> None:
    tracker = _tracker(anon=True)
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("MI", encoding="utf-8")
    (root / "BD_SUMMARY_00.txt").write_text("BD", encoding="utf-8")
    assert await tracker.get_technical_info(_meta(tmp_path)) == {
        "technical_info": "MI"
    }
    assert await tracker.get_technical_info(
        _meta(tmp_path, is_disc="BDMV")
    ) == {"technical_info": "BD"}
    assert await tracker.get_name(_meta()) == {
        "name": "Example Movie 2024 1080p"
    }
    assert await tracker.get_category_data(_meta()) == {"type": 101}
    assert await tracker.get_type_data(_meta()) == {"medium_sel[4]": 202}
    assert await tracker.get_codec_data(_meta()) == {"codec_sel[4]": 303}
    assert await tracker.get_resolution_data(_meta()) == {
        "standard_sel[4]": 404
    }
    assert await tracker.get_group_tag_data(_meta()) == {"team_sel[4]": 505}
    assert await tracker.get_checkboxes_data(_meta()) == {
        "tags[4][]": ["tag-a", "tag-b"]
    }
    assert await tracker.get_anonymous_data(_meta()) == {"uplver": "yes"}
    assert await tracker.get_imdb_data(
        _meta(imdb_id=1, imdb_info={"imdb_url": "https://imdb.invalid"})
    ) == {"url": "https://imdb.invalid"}
    assert await tracker.get_douban_data(_meta(douban_id=2)) == {
        "pt_gen": "https://movie.douban.com/subject/2/"
    }
    assert await tracker.get_audio_codec_data(_meta()) == {
        "audiocodec_sel[4]": 606
    }
    assert await tracker.get_region_data(_meta()) == {"source_sel[4]": 707}
    assert await tracker.get_container_data(_meta()) == {
        "processing_sel[4]": 808
    }

    base = NEXUSPHP(_config(), "TEST")
    assert await base.get_group_tag_data(_meta()) == {}
    assert await base.get_checkboxes_data(_meta()) == {}
    assert await base.get_anonymous_data(_meta(anon=0)) == {}
    assert await base.get_imdb_data(_meta(imdb_id=0)) == {}
    assert await base.get_douban_data(_meta(douban_id=0)) == {}
    assert await base.get_audio_codec_data(_meta()) == {}
    assert await base.get_region_data(_meta()) == {}
    assert await base.get_container_data(_meta()) == {}


@pytest.mark.asyncio
async def test_nexus_get_data_merges_all_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    (root / "MEDIAINFO_CLEANPATH.txt").write_text("MI", encoding="utf-8")
    monkeypatch.setattr(
        tracker, "get_description", AsyncMock(return_value={"descr": "desc"})
    )
    data = await tracker.get_data(_meta(tmp_path))
    assert data["name"] == "Example Movie 2024 1080p"
    assert data["descr"] == "desc"
    assert data["technical_info"] == "MI"
    assert data["small_descr"]
    assert data["type"] == 101


@pytest.mark.asyncio
async def test_nexus_upload_delegates_cookie_uploader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=httpx.Cookies({"sid": "1"})
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        tracker, "get_data", AsyncMock(return_value={"type": 101})
    )
    tracker.cookie_auth_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert await tracker.upload(_meta())
    call = tracker.cookie_auth_uploader.handle_upload.await_args.kwargs
    assert call["torrent_field_name"] == "file"
    assert call["upload_url"] == "https://nexus.invalid/takeupload.php"

    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )  # type: ignore[method-assign]
    assert await tracker.upload(_meta())


@pytest.mark.asyncio
async def test_nexus_search_row_rejects_empty_torrent_id() -> None:
    row = BeautifulSoup(
        "<tr><td><table class='torrentname'><tr><td><a href='details.php?id=' title='Empty'>Empty</a></td></tr></table></td></tr>",
        "html.parser",
    ).find("tr")
    assert row is not None
    assert await _tracker()._search_row(_meta(), row) is None
