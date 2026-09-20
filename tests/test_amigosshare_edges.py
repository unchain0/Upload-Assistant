import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

data_config = types.ModuleType("data.config")
data_config.__file__ = str(Path(__file__).parents[1] / "data" / "config.py")
data_config.DEFAULT = {}
data_config.config = {}
sys.modules.setdefault("data.config", data_config)

import src.integrations.trackers.amigosshare as amigos  # noqa: E402
from src.domain_models.release import Meta  # noqa: E402
from src.integrations.trackers.amigosshare import AmigosShare  # noqa: E402


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "base_dir": ".",
        "uuid": "release",
        "category": "MOVIE",
        "title": "Example",
        "original_title": "Original",
        "author": "Author",
        "year": 2024,
        "type": "WEBDL",
        "is_disc": "",
        "disctype": "",
        "dvd_size": "DVD9",
        "bdinfo": {},
        "audiobook": False,
        "comic": False,
        "manga": False,
        "magazine": False,
        "anime": False,
        "platform": "PC",
        "genres": ["Drama"],
        "keywords": [],
        "languages": {},
        "tag": "-GROUP",
        "filelist": ["Example.2024.1080p.WEB-DL.H.264-GROUP.mkv"],
        "path": "Example.2024.1080p.WEB-DL.H.264-GROUP.mkv",
        "mediainfo": {
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "FileExtension": "mkv",
                    }
                ]
            }
        },
        "audio": "AAC",
        "audio_languages": ["English"],
        "subtitle_languages": [],
        "subtitle_files": [],
        "original_language": "en",
        "language_checked": True,
        "video_width": 1920,
        "video_height": 1080,
        "video_encode": "x264",
        "video_codec": "AVC",
        "hdr": "",
        "tmdb_localized_data": {
            "pt-BR": {
                "main": {
                    "title": "Brasil",
                    "original_title": "Original",
                    "overview": "Sinopse",
                    "poster_path": "/poster.jpg",
                    "genres": [{"name": "Drama"}],
                    "production_countries": [{"name": "Brasil"}],
                    "production_companies": [],
                    "credits": {"cast": []},
                    "vote_average": 8.0,
                    "videos": {"results": []},
                },
                "season": {},
                "episode": {},
            }
        },
        "tmdb_poster_path": "/fallback.jpg",
        "tmdb": "1",
        "imdb_id": "1234567",
        "imdb_info": {"imdbID": "tt1234567", "rating": "7.0"},
        "season": "S01",
        "episode": "E01",
        "tv_pack": False,
        "hosted_artwork": [],
        "artwork_url": "https://img/art.jpg",
        "image_list": [],
        "book_language_iso": "eng",
        "book_language": "English",
        "source_size": 1024 * 1024 * 1024,
        "screens": 4,
        "adult_media": False,
        "tmdb_adult_media": False,
        "nsfw": False,
        "source": "Web",
        "release_type": "",
        "license_type": "",
        "unattended": False,
        "unattended_confirm": False,
        "description": "Descrição em português para teste.",
        "genre": "",
        "youtube": "",
        "search_requests": False,
        "three_d": False,
        "ua_name": "UA",
        "current_version": "1.0",
        "skipping": None,
        "tracker_status": {"AMIGOSSHARE": {"torrent_id": "123"}},
        "modq": False,
    }
    defaults.update(values)
    return Meta(**defaults)


def _tracker() -> AmigosShare:
    tracker = object.__new__(AmigosShare)
    tracker.config = {
        "DEFAULT": {
            "custom_description_header": "",
            "search_requests": False,
        },
        "TRACKERS": {
            "AMIGOSSHARE": {
                "custom_layout": "2",
                "internal": False,
                "internal_groups": [],
                "uploader_status": False,
            }
        },
    }
    tracker.season_tmdb_data = {}
    tracker.episode_tmdb_data = {}
    tracker.layout = "2"
    tracker.common = SimpleNamespace(
        portuguese_title_capitalization=lambda value: str(value).title(),
        check_portuguese_description_requirements=AsyncMock(return_value=True),
        check_portuguese_video_requirements=AsyncMock(return_value=True),
        prompt_user_for_confirmation=AsyncMock(return_value=True),
        count_tv_episodes=lambda _paths: 1,
        is_tv_series_ended=lambda *_args: False,
    )
    tracker.cookie_validator = SimpleNamespace(
        load_session_cookies=AsyncMock(return_value=None),
        handle_validation_failure=AsyncMock(return_value=None),
    )
    tracker.cookie_auth_uploader = SimpleNamespace(
        handle_upload=AsyncMock(return_value=True)
    )
    tracker.session = SimpleNamespace(
        cookies=None,
        get=AsyncMock(),
        post=AsyncMock(),
    )
    return tracker


def _response(
    *,
    text: str = "",
    status: int = 200,
    json_data: object | None = None,
    url: str = "https://cliente.amigos-share.club/",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_data is not None:
        return httpx.Response(status, json=json_data, request=request)
    return httpx.Response(status, text=text, request=request)


def test_constructor_credentials_and_localized_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(amigos, "TmdbManager", lambda _config: sentinel)
    monkeypatch.setattr(amigos, "Common", lambda _config: sentinel)
    monkeypatch.setattr(amigos, "CookieValidator", lambda _config: sentinel)
    monkeypatch.setattr(amigos, "CookieAuthUploader", lambda _config: sentinel)
    monkeypatch.setattr(
        amigos.httpx, "AsyncClient", lambda **_kwargs: sentinel
    )
    tracker = AmigosShare(
        {"DEFAULT": {}, "TRACKERS": {"AMIGOSSHARE": {"custom_layout": "3"}}}
    )
    assert tracker.layout == "3"
    assert tracker.session is sentinel
    client = _tracker()
    meta = _meta()
    client.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    assert asyncio.run(client.validate_credentials(meta))
    assert client.session.cookies == {"sid": "x"}
    client.cookie_validator.load_session_cookies = AsyncMock(return_value=None)
    assert not asyncio.run(client.validate_credentials(meta))
    asyncio.run(client.load_localized_data(meta))
    with pytest.raises(RuntimeError):
        asyncio.run(client.load_localized_data(_meta(tmdb_localized_data={})))


def test_container_and_type_mapping_edges() -> None:
    tracker = _tracker()
    assert tracker._book_container_id(_meta(filelist=["book.epub"])) == "52"
    assert tracker._book_container_id(_meta(filelist=["book.xyz"])) == "17"
    assert tracker._general_track(_meta()) is not None
    assert tracker._general_track(_meta(mediainfo={})) is None
    assert tracker._file_container_id(_meta()) == "6"
    assert tracker._file_container_id(_meta(mediainfo={})) is None
    assert (
        asyncio.run(
            tracker.get_container(_meta(category="BOOK", filelist=["a.pdf"]))
        )
        == "38"
    )
    assert asyncio.run(tracker.get_container(_meta(is_disc="BDMV"))) == "5"
    assert asyncio.run(tracker.get_container(_meta(is_disc="DVD"))) == "15"
    assert asyncio.run(tracker.get_container(_meta())) == "6"
    assert tracker._bd_size(_meta(bdinfo={"size": 70})) == 70.0
    assert tracker._bd_size(_meta(bdinfo={})) == 0.0
    assert tracker._bluray_disc_type_id(_meta(disctype="BD50")) == "41"
    assert (
        tracker._bluray_disc_type_id(_meta(disctype="", bdinfo={"size": 70}))
        == "43"
    )
    assert (
        tracker._bluray_disc_type_id(_meta(disctype="", bdinfo={"size": 55}))
        == "42"
    )
    assert (
        tracker._bluray_disc_type_id(_meta(disctype="", bdinfo={"size": 30}))
        == "41"
    )
    assert (
        tracker._bluray_disc_type_id(_meta(disctype="", bdinfo={"size": 10}))
        == "40"
    )
    assert tracker._disc_type_id(_meta(is_disc="HDDVD")) == 15
    assert tracker._disc_type_id(_meta(is_disc="DVD", dvd_size="DVD5")) == "45"
    assert asyncio.run(tracker.get_type(_meta(type="REMUX"))) == "39"
    assert asyncio.run(tracker.get_type(_meta(type="UNKNOWN"))) == "0"


def test_language_audio_subtitle_resolution_codec_edges() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_languages(_meta(anime=False))) is None
    anime = _meta(
        anime=True, original_language="ja", audio_languages=["Portuguese"]
    )
    info = asyncio.run(tracker.get_languages(anime))
    assert info and info["type"] == "116"
    assert tracker._audio_languages(
        _meta(audio_languages=["Portuguese", "EN"])
    ) == {"portuguese", "en"}
    assert tracker._has_portuguese_audio({"portuguese"})
    assert tracker._portuguese_audio_type({"portuguese"}, "portuguese") == "4"
    assert (
        tracker._portuguese_audio_type({"portuguese", "english"}, "en") == "2"
    )
    assert (
        asyncio.run(tracker.get_subtitle(_meta(subtitle_languages=["pt"])))
        == "Embutida"
    )
    assert (
        asyncio.run(tracker.get_subtitle(_meta(subtitle_languages=[])))
        == "S_legenda"
    )
    assert asyncio.run(
        tracker.get_resolution(_meta(video_width=None, video_height=None))
    ) == {"width": "", "height": ""}
    assert tracker._codec_from_encode(object()) == ""
    assert (
        tracker._video_codec_name(_meta(video_encode="", video_codec="VC-1"))
        == "VC-1"
    )
    assert (
        asyncio.run(
            tracker.get_video_codec(_meta(video_encode="x265", hdr="HDR"))
        )
        == "28"
    )
    assert (
        asyncio.run(
            tracker.get_video_codec(_meta(video_encode="", video_codec="VP9"))
        )
        == "23"
    )
    assert (
        asyncio.run(
            tracker.get_video_codec(
                _meta(video_encode="", video_codec="Unknown")
            )
        )
        == "16"
    )
    assert (
        asyncio.run(tracker.get_audio_codec(_meta(audio="DTS-HD MA 5.1")))
        == "24"
    )
    assert asyncio.run(tracker.get_audio_codec(_meta(audio="Unknown"))) == "20"


def test_names_covers_and_book_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    assert tracker._localized_main(_meta())["title"] == "Brasil"
    assert tracker._localized_title_is_distinct(
        "Original", "Brasil", "Original"
    )
    movie = _meta(title="Original")
    assert tracker._media_name(movie) == "Brasil (Original)"
    tv = _meta(category="TV", title="Original", season="S01", episode="E02")
    tv.tmdb_localized_data["pt-BR"]["main"].update(
        {"name": "Brasil", "original_name": "Original"}
    )
    assert tracker._media_name(tv).endswith(" - S01E02")
    assert (
        asyncio.run(
            tracker.get_name(
                _meta(category="BOOK", title="my book", author="Author")
            )
        )
        == "Author - My Book"
    )
    assert (
        asyncio.run(
            tracker.get_name(_meta(category="GAME", title="Game", tag="-G"))
        )
        == "Game [G]"
    )
    assert (
        tracker._hosted_book_cover(
            _meta(hosted_artwork=[{"raw_url": "https://cover"}])
        )
        == "https://cover"
    )
    assert tracker._hosted_book_cover(_meta(hosted_artwork=[])) == ""
    assert (
        tracker.get_book_cover(
            _meta(hosted_artwork=[], artwork_url="https://art")
        )
        == "https://art"
    )
    assert (
        tracker.get_book_cover(
            _meta(hosted_artwork=[], artwork_url="local.jpg")
        )
        == ""
    )
    monkeypatch.setattr(
        amigos,
        "DescriptionBuilder",
        lambda *_args: SimpleNamespace(
            _build_book_desc_section=lambda *_args, **_kwargs: "BOOK SECTION"
        ),
    )
    base_dir = tmp_path
    (base_dir / "tmp" / "u").mkdir(parents=True)
    meta = _meta(
        base_dir=str(base_dir),
        uuid="u",
        category="BOOK",
        hosted_artwork=[{"raw_url": "https://cover"}],
    )
    tracker.config["DEFAULT"]["custom_description_header"] = "HEADER"
    description = asyncio.run(tracker.build_book_description(meta))
    assert "BOOK SECTION" in description and "HEADER" in description
    assert (base_dir / "tmp" / "u" / "[AMIGOSSHARE]DESCRIPTION.txt").exists()


def test_description_helper_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    layout = {"BARRINHA_CAPA": "capa", "Other": "x"}
    assert tracker._layout_images(layout) == {"BARRINHA_CAPA": "capa"}
    parts: list[str] = []
    asyncio.run(
        tracker._append_layout_section(
            parts, layout, "BARRINHA_CAPA", "content"
        )
    )
    assert parts
    empty_parts: list[str] = []
    asyncio.run(
        tracker._append_layout_section(
            empty_parts, layout, "BARRINHA_CAPA", None
        )
    )
    assert empty_parts == []
    season, main, episode = tracker._description_tmdb_sections(_meta())
    assert main and season == {} and episode == {}
    assert tracker._poster_url(_meta(), {}, main).endswith("/poster.jpg")
    assert tracker._existing_overview({}, main) == "Sinopse"
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert (
        asyncio.run(tracker._description_overview(unattended, {}, {})) is None
    )
    assert unattended.skipping == "AMIGOSSHARE"
    monkeypatch.setattr(
        amigos, "prompt_in_thread", AsyncMock(return_value="Manual")
    )
    assert (
        asyncio.run(tracker._description_overview(_meta(), {}, {})) == "Manual"
    )
    episode_data = {
        "name": "Ep",
        "overview": "Overview",
        "still_path": "/still.jpg",
    }
    assert tracker._episode_section_data(
        _meta(category="TV"), episode_data
    ) == ("Ep", "Overview", "/still.jpg")
    target: list[str] = []
    asyncio.run(
        tracker._append_episode_section(
            target, _meta(category="TV"), episode_data
        )
    )
    assert "Episódio" in "".join(target)
    assert tracker._formatted_runtime(130) == "2 horas e 10 minutos"
    assert tracker._formatted_runtime(30) == "30 minutos"
    assert tracker._formatted_runtime(0) == ""


def test_technical_company_cast_season_rating_helpers() -> None:
    tracker = _tracker()
    main = {
        "runtime": 120,
        "release_date": "2024-01-02",
        "homepage": "https://site",
        "production_countries": [{"name": "Brasil"}],
        "genres": [{"name": "Drama"}],
        "production_companies": [{"name": "Studio", "logo_path": "/logo.png"}],
        "credits": {"cast": [{"name": "Actor"}]},
        "vote_average": 8.5,
    }
    sheet = asyncio.run(tracker._technical_sheet(_meta(), {}, main, {}))
    assert "Duração" in sheet and "02/01/2024" in sheet
    companies = asyncio.run(tracker._production_company_lines(main))
    assert "Studio" in companies
    assert tracker._credits_cast(main) == [{"name": "Actor"}]
    assert tracker._description_cast_data(
        _meta(category="MOVIE"), {}, main, {}
    ) == [{"name": "Actor"}]
    season = {
        "name": "Season 1",
        "poster_path": "/s.jpg",
        "overview": "Season overview",
        "air_date": "2024-01-01",
        "episode_count": 10,
    }
    spoiler = asyncio.run(tracker._season_spoiler(season))
    assert "Season 1" in spoiler and "Episódios: 10" in spoiler
    tv = _meta(category="TV")
    assert asyncio.run(tracker._seasons_content(tv, {"seasons": [season]}))
    ratings = tracker._description_ratings(_meta(), {}, main)
    assert any(item["Source"] == "TMDb" for item in ratings)
    assert (
        tracker._ratings_layout_key(_meta(), {"BARRINHA_INFORMACOES": "x"})
        == "BARRINHA_INFORMACOES"
    )


def test_media_description_and_dispatch(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.fetch_layout_data = AsyncMock(
        return_value={
            "BARRINHA_APRESENTA": "a",
            "BARRINHA_CAPA": "c",
            "BARRINHA_SINOPSE": "s",
        }
    )  # type: ignore[method-assign]
    tracker.media_info = AsyncMock(return_value="MEDIAINFO")  # type: ignore[method-assign]
    tracker.build_cast_bbcode = AsyncMock(return_value="CAST")  # type: ignore[method-assign]
    tracker.build_ratings_bbcode = AsyncMock(return_value="RATING")  # type: ignore[method-assign]
    base = tmp_path
    (base / "tmp" / "u").mkdir(parents=True)
    meta = _meta(base_dir=str(base), uuid="u")
    description = asyncio.run(tracker.build_description(meta))
    assert "Sinopse" in description
    tracker.fetch_layout_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    assert "Error" in asyncio.run(tracker.build_description(meta))


def test_trailer_tags_and_game_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tracker = _tracker()
    video_meta = _meta()
    video_meta.tmdb_localized_data["pt-BR"]["main"]["videos"] = {
        "results": [{"key": "yt"}]
    }
    assert asyncio.run(tracker.get_trailer(video_meta)).endswith("yt")
    assert (
        asyncio.run(tracker.get_trailer(_meta(youtube="fallback")))
        == "fallback"
    )
    assert tracker._tmdb_genre_names(_meta()) == ["Drama"]
    assert asyncio.run(tracker.get_tags(_meta())) == "Drama"
    monkeypatch.setattr(
        amigos, "prompt_in_thread", AsyncMock(return_value="Action")
    )
    no_genres = _meta(
        tmdb_localized_data={"pt-BR": {"main": {}}}, genres=[], genre=""
    )
    assert asyncio.run(tracker.get_tags(no_genres)) == "Action"
    unattended = _meta(
        tmdb_localized_data={"pt-BR": {"main": {}}}, genres=[], unattended=True
    )
    assert asyncio.run(tracker.get_tags(unattended)) == ""
    assert unattended.skipping == "AMIGOSSHARE"
    assert (
        tracker.get_game_name(_meta(category="GAME", title="Game", tag=""))
        == "Game [NoGroup]"
    )
    assert (
        tracker.get_game_type(_meta(category="GAME", platform="PS4")) == "79"
    )
    assert (
        tracker.get_game_type(_meta(category="GAME", platform="UNKNOWN"))
        == "47"
    )
    assert (
        tracker.get_game_genre(_meta(category="GAME", genres=["RPG"])) == "12"
    )
    assert (
        tracker.get_game_genre(_meta(category="GAME", genres=["Unknown"]))
        == "0"
    )
    assert (
        tracker.get_game_idioma(
            _meta(category="GAME", languages={"Portuguese": {}, "English": {}})
        )
        == "7"
    )
    assert tracker.get_game_idioma(_meta(category="GAME", languages={})) == "6"
    monkeypatch.setattr(
        amigos,
        "DescriptionBuilder",
        lambda *_args: SimpleNamespace(
            _build_game_desc_section=lambda *_args, **_kwargs: "GAME",
            get_user_description=AsyncMock(return_value="USER"),
        ),
    )
    (tmp_path / "tmp" / "g").mkdir(parents=True)
    game = _meta(base_dir=str(tmp_path), uuid="g", category="GAME")
    built = asyncio.run(tracker.build_game_description(game))
    assert "GAME" in built and "USER" in built


def test_file_info_and_search_helpers() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(
        return_value=_response(
            text="<li class='list-group-item'>Movie.File.mkv</li>"
        )
    )
    info = asyncio.run(tracker._fetch_file_info("1", "link", "1 GB"))
    assert info == {"name": "Movie.File.mkv", "size": "1 GB", "link": "link"}
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    failed = asyncio.run(tracker._fetch_file_info("1", "link", "1 GB"))
    assert failed["name"] == "N/A"
    assert tracker._book_search_url(
        _meta(category="BOOK", title="Book", author="Author")
    ).endswith("Author+Book")
    assert "cat=79" in tracker._game_search_url(
        _meta(category="GAME", platform="PS4")
    )
    assert tracker._media_search_url(_meta(category="MOVIE")).endswith(
        "imdb=tt1234567"
    )
    assert "S01E01" in tracker._media_search_url(_meta(category="TV"))
    assert asyncio.run(tracker._search_url(_meta(category="OTHER"))) is None
    assert tracker._year_badge("2024") == ("year", "2024")
    assert tracker._resolution_badge("4K") == ("resolution", "2160p")
    assert tracker._disc_type_badge("BD50") == ("disk_type", "BD50")
    assert tracker._video_codec_badge("HEVC") == ("video_codec", "HEVC")
    assert tracker._audio_codec_badge("DTS-HD") == ("audio_codec", "DTS-HD")
    fields = tracker._disc_fields(["2024", "4K", "BD50", "HEVC", "DTS"])
    assert fields["disk_type"] == "BD50" and fields["resolution"] == "2160p"


def test_search_existing_disc_game_and_session_failure() -> None:
    tracker = _tracker()
    disc_html = """
    <li class='list-group-item dark-gray'>
      <a href='torrents-details.php?id=1'>Details</a>
      <span class='badge-info'>10 GB</span>
      <span class='badge'>2024</span><span class='badge'>4K</span>
      <span class='badge'>BD50</span><span class='badge'>HEVC</span><span class='badge'>DTS</span>
    </li>
    """
    tracker.session.get = AsyncMock(return_value=_response(text=disc_html))
    results = asyncio.run(tracker.search_existing(_meta()))
    assert results and "BD50" in results[0]["name"]
    game_html = """
    <li class='list-group-item dark-gray'>
      <a href='torrents-details.php?id=2'>Details</a>
      <div class='tooltips'><p><a>Game Name</a></p></div>
      <span class='badge-info'>1 GB</span>
    </li>
    """
    tracker.session.get = AsyncMock(return_value=_response(text=game_html))
    game = asyncio.run(
        tracker.search_existing(_meta(category="GAME", platform="PC"))
    )
    assert game[0]["name"] == "Game Name"
    login = _response(
        text="login.php Esqueceu sua senha",
        url="https://cliente.amigos-share.club/login.php",
    )
    tracker.session.get = AsyncMock(return_value=login)
    meta = _meta()
    assert asyncio.run(tracker.search_existing(meta)) == []
    assert meta.skipping == "AMIGOSSHARE"


def test_upload_url_date_media_info(tmp_path: Path) -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_upload_url(_meta(category="BOOK"))
    ).endswith("enviar-ebook.php")
    assert asyncio.run(
        tracker.get_upload_url(_meta(category="GAME"))
    ).endswith("enviar-jogos.php")
    assert asyncio.run(tracker.get_upload_url(_meta(anime=True))).endswith(
        "enviar-anime.php"
    )
    assert asyncio.run(
        tracker.get_upload_url(_meta(category="MOVIE"))
    ).endswith("enviar-filme.php")
    assert asyncio.run(tracker.get_upload_url(_meta(category="TV"))).endswith(
        "enviar-series.php"
    )
    assert (
        asyncio.run(tracker.format_image("https://x"))
        == "[img]https://x[/img]"
    )
    assert asyncio.run(tracker.format_image(None)) == ""
    assert asyncio.run(tracker.format_date("2024-01-02")) == "02/01/2024"
    assert asyncio.run(tracker.format_date("02 Jan 2024")) == "02/01/2024"
    assert asyncio.run(tracker.format_date("bad")) == "bad"
    assert asyncio.run(tracker.format_date(None)) == "N/A"
    temp = tmp_path / "tmp" / "u"
    temp.mkdir(parents=True)
    summary = temp / "BD_SUMMARY_00.txt"
    summary.write_text("BDINFO")
    assert (
        asyncio.run(
            tracker.media_info(
                _meta(base_dir=str(tmp_path), uuid="u", is_disc="BDMV")
            )
        )
        == "BDINFO"
    )
    assert asyncio.run(tracker.media_info(_meta(is_disc="DVD"))) is None


def test_layout_ratings_and_cast(tmp_path: Path) -> None:
    tracker = _tracker()
    cache = tmp_path / "tmp" / "ASC_layout_cache_2.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"BARRINHA_CAPA": "x"}))
    assert asyncio.run(
        tracker.fetch_layout_data(_meta(base_dir=str(tmp_path)))
    ) == {"BARRINHA_CAPA": "x"}
    cache.unlink()
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"ASC": {"BARRINHA_CAPA": "y"}})
    )
    assert asyncio.run(
        tracker.fetch_layout_data(_meta(base_dir=str(tmp_path)))
    ) == {"BARRINHA_CAPA": "y"}
    assert (
        tracker._imdb_rating_url(_meta(imdb_info={"imdb_url": "https://imdb"}))
        == "https://imdb"
    )
    assert "imdb" in tracker._rating_bbcode(
        _meta(), "Internet Movie Database", "8/10", "IMG"
    )
    ratings = asyncio.run(
        tracker.build_ratings_bbcode(
            _meta(),
            [
                {"Source": "TMDb", "Value": "8/10"},
                {"Source": "Unknown", "Value": "x"},
            ],
        )
    )
    assert "8/10" in ratings
    cast = asyncio.run(
        tracker.build_cast_bbcode(
            [
                {
                    "id": 1,
                    "name": "Actor",
                    "character": "Role",
                    "profile_path": None,
                }
            ]
        )
    )
    assert "Actor" in cast


def test_requests_success_disabled_and_error() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_requests(_meta())) is False
    meta = _meta(search_requests=True)
    html = """
    <div class='table-responsive'><table>
      <tr><td></td><td><a href='pedidos.php?action=ver&id=1'>Request</a></td><td></td><td></td><td>10 GB</td><td></td></tr>
    </table></div>
    """
    tracker.session.get = AsyncMock(return_value=_response(text=html))
    results = asyncio.run(tracker.get_requests(meta))
    assert isinstance(results, list) and results[0]["Name"] == "Request"
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker.get_requests(meta)) == []


def test_payload_builders_book_game_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        amigos.languages_manager,
        "process_desc_language",
        AsyncMock(return_value=None),
    )
    book = _meta(
        category="BOOK",
        language_checked=False,
        image_list=[{"raw_url": "s2"}, {"raw_url": "s3"}],
        hosted_artwork=[{"raw_url": "cover"}],
    )
    book_data = asyncio.run(tracker._book_upload_data(book, "67"))
    assert book_data["idioma"] == "4" and book_data["screens2"] == "s2"
    game = _meta(category="GAME", image_list=[{"raw_url": "g1"}])
    game_data = asyncio.run(tracker._game_upload_data(game, "47"))
    assert game_data["screens1"] == "g1"
    tracker.get_audio = AsyncMock(return_value="7")  # type: ignore[method-assign]
    tracker.get_audio_codec = AsyncMock(return_value="10")  # type: ignore[method-assign]
    tracker.get_video_codec = AsyncMock(return_value="17")  # type: ignore[method-assign]
    tracker.get_container = AsyncMock(return_value="6")  # type: ignore[method-assign]
    tracker.get_tags = AsyncMock(return_value="Drama")  # type: ignore[method-assign]
    tracker.get_subtitle = AsyncMock(return_value="S_legenda")  # type: ignore[method-assign]
    tracker.get_trailer = AsyncMock(return_value="")  # type: ignore[method-assign]
    media = _meta(image_list=[{"raw_url": "m1"}])
    media_data = asyncio.run(tracker._media_upload_data(media, "23"))
    assert media_data["screens1"] == "m1" and media_data["imdb"] == "tt1234567"
    anime = _meta(anime=True)
    tracker.get_languages = AsyncMock(
        return_value={"idioma": "8", "lang": "8", "type": "116"}
    )  # type: ignore[method-assign]
    data: dict[str, object] = {}
    asyncio.run(tracker._apply_anime_upload_data(anime, data))
    assert data["type"] == "116"


def test_get_data_and_upload_paths() -> None:
    tracker = _tracker()
    tracker.load_localized_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.build_description = AsyncMock(return_value="DESC")  # type: ignore[method-assign]
    tracker.get_type = AsyncMock(return_value="23")  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="NAME")  # type: ignore[method-assign]
    tracker._category_upload_data = AsyncMock(return_value={"x": "y"})  # type: ignore[method-assign]
    data = asyncio.run(tracker.get_data(_meta()))
    assert data["name"] == "NAME" and data["x"] == "y"
    assert not tracker._upload_preconditions_allowed(
        _meta(skipping="AMIGOSSHARE")
    )
    assert not tracker._upload_preconditions_allowed(
        _meta(category="BOOK", source_size=100)
    )
    assert tracker._upload_preconditions_allowed(_meta())
    meta = _meta()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    tracker.get_upload_url = AsyncMock(return_value="upload")  # type: ignore[method-assign]
    tracker._perform_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    tracker._post_upload_actions = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.upload(meta))
    tracker._perform_upload = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.upload(meta))


def test_post_upload_approval_and_internal_actions() -> None:
    tracker = _tracker()
    meta = _meta(tag="-GROUP")
    tracker.config["TRACKERS"]["AMIGOSSHARE"].update(
        {
            "uploader_status": True,
            "internal": True,
            "internal_groups": ["GROUP"],
        }
    )
    assert asyncio.run(tracker.get_approval(meta))
    assert not asyncio.run(tracker.get_approval(_meta(modq=True)))
    assert tracker._internal_upload(meta)
    tracker.auto_approval = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.set_internal_flag = AsyncMock(return_value=None)  # type: ignore[method-assign]
    asyncio.run(tracker._post_upload_actions(meta))
    tracker.auto_approval.assert_awaited_once()
    tracker.set_internal_flag.assert_awaited_once()
    debug = _meta(debug=True)
    asyncio.run(tracker.auto_approval(debug))
    asyncio.run(tracker.set_internal_flag(debug))


def test_remaining_pure_guards_and_dispatches() -> None:
    tracker = _tracker()
    assert (
        asyncio.run(tracker.get_type(_meta(category="BOOK", audiobook=True)))
        == "121"
    )
    assert (
        asyncio.run(tracker.get_type(_meta(category="GAME", platform="PC")))
        == "47"
    )
    assert (
        asyncio.run(
            tracker.get_type(
                _meta(type="DISC", is_disc="BDMV", disctype="BD25")
            )
        )
        == "40"
    )
    assert (
        tracker._disc_type_id(_meta(is_disc="BDMV", disctype="BD25")) == "40"
    )
    assert tracker._hdr_codec_id("VP9", "HDR") is None
    assert (
        tracker._general_track(_meta(mediainfo={"media": {"track": []}}))
        is None
    )
    assert tracker._hosted_book_cover(_meta(hosted_artwork=["bad"])) == ""
    assert tracker._metadata_values(_meta(), "not_here") == []
    assert tracker._metadata_values(_meta(genres="Drama"), "genres") == [
        "Drama"
    ]
    assert tracker._game_language_names(_meta(languages=[])) == []
    assert tracker._source_size(_meta(source_size="bad")) == 0
    assert not tracker._category_supported("OTHER")
    invalid = _meta(filelist="bad")
    assert tracker._file_paths(invalid) is None
    assert tracker._screenshot_count(_meta(screens="bad")) == 0
    disc = _meta(is_disc="DVD")
    assert tracker._video_files_allowed(disc, [])
    assert tracker._category_specific_rules_allowed
    assert asyncio.run(
        tracker._category_specific_rules_allowed(_meta(), "OTHER", [])
    )
    assert tracker._raw_image_url("bad") == ""
    assert tracker._media_language_code(_meta(original_language="")) == "1"
    assert tracker._classified_badge("unknown") is None
    assert tracker._internal_upload(_meta(tag="")) is False
    assert tracker._internal_upload(_meta(tag="-GROUP")) is False


def test_remaining_rule_branch_helpers() -> None:
    tracker = _tracker()
    assert not tracker._inferior_dvd_source(_meta(is_disc=""))
    assert tracker._inferior_dvd_source(
        _meta(is_disc="DVD", name="Movie.R5.DVD", source="DVD", type="DISC")
    )
    assert not tracker._dvd_source_allowed(
        _meta(is_disc="DVD", name="Movie.R5.DVD", source="DVD", type="DISC")
    )
    assert tracker._dvd_source_allowed(_meta())
    assert asyncio.run(
        tracker._unknown_tv_status_allowed(_meta(), True, 0, None)
    )
    tracker.common.count_tv_episodes = lambda _paths: 1
    tracker.common.is_tv_series_ended = lambda *_args: False
    assert asyncio.run(
        tracker._tv_rules_allowed(
            _meta(category="TV", tv_pack=False),
            [Path("Show.S01E01.1080p.WEB-DL.H.264-GRP.mkv")],
        )
    )
    tracker._initial_rules_allowed = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker._validated_common_payload(_meta(), "MOVIE"))
        is None
    )
    tracker._initial_rules_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
    tracker._file_paths = lambda _meta_value: None  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker._validated_common_payload(_meta(), "MOVIE"))
        is None
    )


def test_remaining_search_dispatch_and_async_file_result() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    asyncio.run(tracker._load_search_cookies(_meta()))
    assert tracker.session.cookies == {"sid": "x"}
    tracker.load_localized_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="Anime Name")  # type: ignore[method-assign]
    assert "Anime+Name" in asyncio.run(
        tracker._anime_search_url(_meta(anime=True))
    )
    assert asyncio.run(tracker._search_url(_meta(anime=True)))
    assert (
        tracker._search_imdb(_meta(imdb_info={}, imdb_id="5")) == "tt0000005"
    )
    no_detail = SimpleNamespace(
        find=lambda *_args, **_kwargs: None,
        find_all=lambda *_args, **_kwargs: [],
        select_one=lambda *_args, **_kwargs: None,
    )
    entry, task = tracker._release_search_result(_meta(), no_detail)
    assert entry is None and task is None
    release = SimpleNamespace(
        find=lambda *_args, **kwargs: (
            SimpleNamespace(get=lambda _key: "torrents-details.php?id=8")
            if kwargs.get("href") is not None
            else None
        ),
        find_all=lambda *_args, **_kwargs: [],
        select_one=lambda *_args, **_kwargs: None,
    )
    tracker._fetch_file_info = AsyncMock(
        return_value={"name": "Fetched", "size": "", "link": "link"}
    )  # type: ignore[method-assign]

    async def exercise() -> list[dict[str, str]]:
        return await tracker._parse_search_releases(_meta(), [release])

    parsed = asyncio.run(exercise())
    assert parsed and parsed[0]["name"] == "Fetched"
    tracker._search_url = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_existing(_meta())) == []


def test_remaining_request_parsing_and_categories() -> None:
    tracker = _tracker()
    assert (
        asyncio.run(tracker._request_category(_meta(category="BOOK"))) == "67"
    )
    assert (
        asyncio.run(
            tracker._request_category(_meta(anime=True, category="MOVIE"))
        )
        == 116
    )
    assert (
        asyncio.run(
            tracker._request_category(_meta(anime=True, category="TV"))
        )
        == 118
    )
    short = SimpleNamespace(find_all=lambda *_args: [SimpleNamespace()])
    assert tracker._parse_request_row(short) is None
    cells = [SimpleNamespace() for _ in range(6)]
    cells[1].select_one = lambda *_args: None
    row = SimpleNamespace(find_all=lambda *_args: cells)
    assert tracker._parse_request_row(row) is None
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    asyncio.run(tracker._load_request_cookies(_meta()))
    assert tracker.session.cookies == {"sid": "x"}


def test_remaining_payload_category_dispatches() -> None:
    tracker = _tracker()
    tracker._book_upload_data = AsyncMock(return_value={"kind": "book"})  # type: ignore[method-assign]
    tracker._game_upload_data = AsyncMock(return_value={"kind": "game"})  # type: ignore[method-assign]
    tracker._media_upload_data = AsyncMock(return_value={"kind": "media"})  # type: ignore[method-assign]
    assert asyncio.run(
        tracker._category_upload_data(_meta(category="BOOK"), "x")
    ) == {"kind": "book"}
    assert asyncio.run(
        tracker._category_upload_data(_meta(category="GAME"), "x")
    ) == {"kind": "game"}
    assert asyncio.run(
        tracker._category_upload_data(_meta(category="MOVIE"), "x")
    ) == {"kind": "media"}
    no_info: dict[str, object] = {}
    tracker.get_languages = AsyncMock(return_value=None)  # type: ignore[method-assign]
    asyncio.run(tracker._apply_anime_upload_data(_meta(anime=True), no_info))
    assert no_info == {}
    untouched: dict[str, object] = {}
    asyncio.run(
        tracker._apply_anime_upload_data(_meta(anime=False), untouched)
    )
    assert untouched == {}


def test_remaining_media_info_and_cast_rating_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        amigos.DescriptionBuilder,
        "format_short_mediainfo_json",
        lambda _mediainfo, video_file: f"MI:{video_file}",
    )
    assert (
        tracker._file_media_info(_meta(filelist=["video.mkv"]))
        == "MI:video.mkv"
    )
    assert (
        asyncio.run(tracker.media_info(_meta(filelist=["video.mkv"])))
        == "MI:video.mkv"
    )
    assert (
        asyncio.run(
            tracker._bd_media_info(_meta(base_dir=".", uuid="missing"))
        )
        is None
    )
    assert tracker._credits_cast({"credits": []}) == []
    assert tracker._credits_cast({"credits": {"cast": "bad"}}) == []
    assert tracker._layout_ratings({"Ratings": "bad"}) == []
    assert tracker._imdb_rating(_meta(imdb_info={})) is None
    ratings = [{"Source": "TMDb", "Value": "7/10"}]
    tracker._append_tmdb_rating(ratings, {"vote_average": 8.0})
    assert len(ratings) == 1
    assert (
        tracker._rating_bbcode(_meta(tmdb=""), "Other", "5/10", "IMG")
        == "IMG\n[b]5/10[/b]\n"
    )
    assert asyncio.run(tracker.build_cast_bbcode([])) == ""


def test_remaining_description_dispatch_and_lines() -> None:
    tracker = _tracker()
    tracker.build_book_description = AsyncMock(return_value="BOOK")  # type: ignore[method-assign]
    tracker.build_game_description = AsyncMock(return_value="GAME")  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker.build_description(_meta(category="BOOK")))
        == "BOOK"
    )
    assert (
        asyncio.run(tracker.build_description(_meta(category="GAME")))
        == "GAME"
    )
    assert tracker._description_tmdb_sections(
        _meta(tmdb_localized_data={"pt-BR": "bad"})
    ) == ({}, {}, {})
    assert tracker._named_values("bad") == []
    assert tracker._production_company_line
    assert asyncio.run(tracker._production_company_line("bad")) == ""
    assert asyncio.run(tracker._technical_sheet(_meta(), {}, {}, {})) == ""
    assert (
        tracker._technical_runtime(
            _meta(runtime=50), {"runtime": 60}, {"runtime": None}
        )
        == 60
    )
    parts: list[str] = []
    tracker.config["DEFAULT"]["custom_description_header"] = "HEADER"
    tracker._append_base_description(parts, _meta(description=""))
    assert any("HEADER" in value for value in parts)


def test_layout_fetch_invalid_cache_fallback_and_empty(tmp_path: Path) -> None:
    tracker = _tracker()
    cache = tmp_path / "tmp" / "ASC_layout_cache_2.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("not-json")
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"ASC": {"BARRINHA_CAPA": "fresh"}})
    )
    assert asyncio.run(
        tracker.fetch_layout_data(_meta(base_dir=str(tmp_path)))
    ) == {"BARRINHA_CAPA": "fresh"}
    cache.unlink(missing_ok=True)
    tracker.session.post = AsyncMock(
        side_effect=[
            _response(json_data={"ASC": {}}),
            _response(json_data={"ASC": {"FALLBACK": "yes"}}),
        ]
    )
    assert asyncio.run(
        tracker.fetch_layout_data(_meta(base_dir=str(tmp_path)))
    ) == {"FALLBACK": "yes"}
    cache.unlink(missing_ok=True)
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"ASC": []})
    )
    assert (
        asyncio.run(tracker._fetch_layout_payload({"imdb": "x"}, cache)) == {}
    )
    tracker.session.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    assert (
        asyncio.run(tracker.fetch_layout_data(_meta(base_dir=str(tmp_path))))
        == {}
    )


def test_live_upload_approval_and_internal_success_error() -> None:
    tracker = _tracker()
    meta = _meta(
        debug=False, tracker_status={"AMIGOSSHARE": {"torrent_id": "55"}}
    )
    tracker.session.get = AsyncMock(return_value=_response())
    asyncio.run(tracker.auto_approval(meta))
    tracker.session.get.assert_awaited_once()
    tracker.session.get = AsyncMock(
        side_effect=RuntimeError("approval failed")
    )
    asyncio.run(tracker.auto_approval(meta))
    tracker.session.post = AsyncMock(return_value=_response())
    asyncio.run(tracker.set_internal_flag(meta))
    tracker.session.post.assert_awaited_once()
    tracker.session.post = AsyncMock(
        side_effect=RuntimeError("internal failed")
    )
    asyncio.run(tracker.set_internal_flag(meta))
    tracker.config["TRACKERS"]["AMIGOSSHARE"]["uploader_status"] = False
    assert not asyncio.run(tracker.get_approval(meta))


def test_upload_cookie_perform_and_skip_after_data() -> None:
    tracker = _tracker()
    meta = _meta()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    asyncio.run(tracker._load_upload_cookies(meta))
    assert tracker.session.cookies == {"sid": "x"}
    assert asyncio.run(tracker._perform_upload(meta, {"a": "b"}, "upload"))
    tracker.get_data = AsyncMock(
        side_effect=lambda current: (
            setattr(current, "skipping", "AMIGOSSHARE") or {}
        )
    )  # type: ignore[method-assign]
    assert not asyncio.run(tracker.upload(_meta()))


def test_final_description_and_tv_branches() -> None:
    tracker = _tracker()
    assert (
        tracker._technical_runtime(_meta(runtime=50), {}, {"runtime": 33})
        == 33
    )
    season_cast = {"credits": {"cast": [{"name": "Season Actor"}]}}
    episode_cast = {"credits": {"cast": [{"name": "Episode Actor"}]}}
    assert tracker._description_cast_data(
        _meta(category="TV", tv_pack=True), season_cast, {}, episode_cast
    ) == [{"name": "Season Actor"}]
    assert tracker._description_cast_data(
        _meta(category="TV", tv_pack=False), season_cast, {}, episode_cast
    ) == [{"name": "Episode Actor"}]
    assert (
        asyncio.run(
            tracker._seasons_content(_meta(category="TV"), {"seasons": "bad"})
        )
        == ""
    )
    invalid_genres = _meta()
    invalid_genres.tmdb_localized_data["pt-BR"]["main"]["genres"] = [
        "bad",
        {"name": ""},
    ]
    assert tracker._tmdb_genre_names(invalid_genres) == []
    assert asyncio.run(tracker._manual_tags(_meta(genre="Manual"))) == "Manual"
    tracker.fetch_layout_data = AsyncMock(return_value={"layout": "x"})  # type: ignore[method-assign]
    tracker.media_info = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker._media_description_parts = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.build_description(_meta())) == ""


def test_final_media_description_company_and_missing_overview() -> None:
    tracker = _tracker()
    tracker.get_name = AsyncMock(return_value="Title")  # type: ignore[method-assign]
    tracker._description_overview = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker._media_description_parts(_meta(), {}, None))
        is None
    )
    tracker = _tracker()
    tracker.get_name = AsyncMock(return_value="Title")  # type: ignore[method-assign]
    tracker._description_overview = AsyncMock(return_value="Overview")  # type: ignore[method-assign]
    tracker._technical_sheet = AsyncMock(return_value="Tech")  # type: ignore[method-assign]
    tracker._production_company_lines = AsyncMock(return_value="Company")  # type: ignore[method-assign]
    tracker.build_cast_bbcode = AsyncMock(return_value="Cast")  # type: ignore[method-assign]
    tracker._seasons_content = AsyncMock(return_value="")  # type: ignore[method-assign]
    tracker.build_ratings_bbcode = AsyncMock(return_value="Ratings")  # type: ignore[method-assign]
    parts = asyncio.run(tracker._media_description_parts(_meta(), {}, None))
    assert parts is not None and any("Company" in part for part in parts)


def test_final_rule_early_returns() -> None:
    tracker = _tracker()
    assert not asyncio.run(
        tracker._initial_rules_allowed(_meta(category="OTHER"), "OTHER")
    )
    tracker._confirm_rule_exception = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker._unknown_tv_status_allowed(
            _meta(category="TV"), False, 1, None
        )
    )
    tracker.common.count_tv_episodes = lambda _paths: 1
    tracker.common.is_tv_series_ended = lambda *_args: None
    tracker._unknown_tv_status_allowed = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker._tv_rules_allowed(
            _meta(category="TV"),
            [Path("Show.S01E01.1080p.WEB-DL.H.264-GRP.mkv")],
        )
    )
    assert not asyncio.run(
        tracker._media_context_rules_allowed(
            _meta(
                is_disc="DVD",
                name="Movie.R5.DVD",
                source="DVD",
                type="DISC",
            ),
            "MOVIE",
            [],
        )
    )


def test_final_debug_and_upload_precondition_branches() -> None:
    tracker = _tracker()
    asyncio.run(tracker.auto_approval(SimpleNamespace(debug=True)))
    asyncio.run(tracker.set_internal_flag(SimpleNamespace(debug=True)))
    assert not asyncio.run(tracker.upload(_meta(skipping="AMIGOSSHARE")))


def test_layout_cache_write_failure_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"ASC": {"BARRINHA_CAPA": "x"}})
    )

    class FailingWriter:
        async def write(self, _value: str) -> None:
            raise OSError("disk full")

    class FailingContext:
        async def __aenter__(self) -> FailingWriter:
            return FailingWriter()

        async def __aexit__(
            self,
            _exc_type: object,
            _exc: object,
            _tb: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        amigos.aiofiles,
        "open",
        lambda *_args, **_kwargs: FailingContext(),
    )
    result = asyncio.run(
        tracker.fetch_layout_data(_meta(base_dir=str(tmp_path)))
    )
    assert result == {"BARRINHA_CAPA": "x"}
