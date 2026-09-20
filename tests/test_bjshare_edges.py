import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from bs4 import BeautifulSoup

import src.integrations.trackers.bjshare as bj
from src.domain_models.release import Meta
from src.integrations.trackers.bjshare import BJShare

CONFIG = {
    "DEFAULT": {"search_requests": False},
    "TRACKERS": {
        "BJSHARE": {
            "anon": False,
            "show_group_if_anon": False,
            "internal": False,
            "internal_groups": [],
        }
    },
}


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "base_dir": ".",
        "uuid": "release",
        "category": "MOVIE",
        "title": "Example",
        "year": 2024,
        "container": "mkv",
        "type": "WEBDL",
        "is_disc": "",
        "disctype": "",
        "dvd_size": "",
        "bdinfo": {},
        "audiobook": False,
        "audiobook_bitrate": None,
        "manga": False,
        "comic": False,
        "newspaper": False,
        "magazine": False,
        "anime": False,
        "book_language_iso": "eng",
        "platform": "PC",
        "available_platforms": [],
        "languages": {},
        "game_subcategory": "full_game",
        "game_system": "",
        "game_region": "",
        "game_version": "",
        "scene": False,
        "artwork_path": "",
        "language_checked": True,
        "audio_languages": [],
        "original_language": "English",
        "subtitle_languages": [],
        "subtitle_files": [],
        "resolution": "1080p",
        "mediainfo": {
            "media": {"track": [{}, {"Width": "1920", "Height": "1080"}]}
        },
        "video_encode": "H.264",
        "video_codec": "AVC",
        "audio": "AAC 2.0",
        "edition": "",
        "video_duration": 60,
        "tmdb_localized_data": {},
        "tmdb_poster_path": "",
        "menu_images": [],
        "image_list": [],
        "hdr": "",
        "bit_depth": "8",
        "discs": [],
        "extras": False,
        "has_commentary": False,
        "manual_commentary": False,
        "imdb_info": {},
        "cast": [],
        "tmdb_directors": [],
        "tmdb_creators": [],
        "tmdb_cast": [],
        "search_requests": False,
        "tmdb_id": "",
        "localized_overviews": {},
        "overview": "Overview",
        "youtube": "",
        "tag": "",
        "repack": "",
        "service_longname": "Service",
        "tv_pack": 0,
        "season_int": 1,
        "episode_int": 1,
        "tvdb_episode_year": "",
        "adult_media": False,
        "keywords": [],
        "combined_genres": "",
        "anon": 0,
        "debug": True,
        "ua_name": "UA",
        "current_version": "1.0",
        "skipping": None,
        "tracker_status": {"BJSHARE": {}},
    }
    defaults.update(values)
    return Meta(**defaults)


def _common() -> SimpleNamespace:
    return SimpleNamespace(
        portuguese_title_capitalization=lambda value: str(value).title(),
        path_exists=AsyncMock(return_value=True),
        check_language_requirements=AsyncMock(return_value=True),
    )


def _tracker() -> BJShare:
    BJShare.already_has_the_info = False
    BJShare.database_title = ""
    BJShare.database_identifier = ""
    BJShare.database_overview = ""
    BJShare.secret_token = ""
    tracker = object.__new__(BJShare)
    tracker.config = {
        "DEFAULT": dict(CONFIG["DEFAULT"]),
        "TRACKERS": {"BJSHARE": dict(CONFIG["TRACKERS"]["BJSHARE"])},
    }
    tracker.main_tmdb_data = {}
    tracker.episode_tmdb_data = {}
    tracker.common = _common()
    tracker.cookie_validator = SimpleNamespace(
        load_session_cookies=AsyncMock(return_value=None),
        handle_validation_failure=AsyncMock(return_value=None),
    )
    tracker.session = SimpleNamespace(
        cookies=None,
        get=AsyncMock(),
        post=AsyncMock(),
    )
    return tracker


def _response(
    text: str = "",
    status: int = 200,
    url: str = "https://bj-share.info/torrents.php",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status, text=text, request=request)


def test_module_helpers_cover_mapping_contracts() -> None:
    assert bj._book_container(_meta(audiobook=True), "flac") == "FLAC"
    assert bj._book_container(_meta(audiobook=True), "xyz") == "Outro"
    assert bj._book_container(_meta(), "pdf") == "PDF"
    assert bj._book_container(_meta(), "epub") == "ePub"
    assert bj._book_container(_meta(), "txt") == ""
    assert bj._book_type(_meta(audiobook=True)) == 10
    assert bj._book_type(_meta(manga=True)) == 4
    assert bj._book_type(_meta(comic=True)) == 11
    assert bj._book_type(_meta(newspaper=True)) == 23
    assert bj._book_type(_meta(magazine=True)) == 8
    assert bj._book_type(_meta()) == 9
    assert bj._language_display_name("pt", ["PT"]) == "Português (pt)"
    assert bj._language_display_name("pt", ["BR"]) == "Português"
    assert bj._language_display_name("en", []) == "Inglês"
    assert bj._game_language_names({"English": {}, "Portuguese": {}}) == [
        "english",
        "portuguese",
    ]
    assert bj._game_language_names([]) == []
    assert bj._mapped_game_language(["german"]) == "Alemão"
    assert bj._mapped_game_language(["klingon"]) == "Outro"
    assert bj._has_portuguese(["brazilian portuguese"])
    assert not bj._has_portuguese(["english"])
    assert bj._single_platform_system("PC") == "Windows"
    assert bj._single_platform_system("macOS") == "Mac"
    assert bj._single_platform_system("Linux") == "Linux"
    assert bj._single_platform_system("Switch") == ""
    assert bj._audio_label({"Portuguese"}, "Portuguese") == "Nacional"
    assert (
        bj._audio_label({"Portuguese", "English"}, "English") == "Dual Áudio"
    )
    assert bj._audio_label({"Portuguese"}, "English") == "Dublado"
    assert bj._audio_label({"English"}, "English") == "Legendado"
    assert bj._matching_codec("x265 hevc", (("x265", "x265"),)) == "x265"
    assert bj._matching_codec("avc", (("hevc", "H.265"),)) is None
    assert bj._matching_audio_codec("DTS:X 7.1") == "DTS-X"
    assert bj._matching_audio_codec("unknown") == "Outro"


def test_module_helpers_cover_rating_genres_and_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert bj._br_rating({"iso_3166_1": "BR", "rating": "L"}) == "Livre"
    assert bj._br_rating({"iso_3166_1": "BR", "rating": "16"}) == "16 anos"
    assert bj._br_rating({"iso_3166_1": "US", "rating": "R"}) is None
    assert bj._us_rating({"iso_3166_1": "US", "rating": "R"}) == "R"
    assert bj._us_rating({"iso_3166_1": "BR", "rating": "16"}) == ""
    assert bj._mapped_genre("Action") == "ação"
    assert bj._mapped_genre("ação") == "ação"
    assert bj._mapped_genre("unknown") is None
    assert bj._mapped_genres(["Action", "ação", "Drama"]) == ["ação", "drama"]
    assert bj._tmdb_genres({"genres": [{"name": "Action"}, "bad"]}) == ["ação"]
    meta = _meta(
        title="Original",
        imdb_info={"title": "IMDb"},
        tmdb_localized_data={
            "pt-BR": {
                "main": {
                    "title": "Brasil",
                    "original_title": "Original",
                }
            }
        },
    )
    assert bj._localized_main(meta)["title"] == "Brasil"
    assert (
        bj._localized_brazilian_title(meta, bj._localized_main(meta))
        == "Brasil"
    )
    assert bj._video_titles(meta, "Database") == ("Database", "Brasil")
    assert (
        bj._rating_from_items(
            [
                {"iso_3166_1": "US", "rating": "PG"},
                {"iso_3166_1": "BR", "rating": "12"},
            ]
        )
        == "12 anos"
    )
    assert (
        bj._rating_from_items([{"iso_3166_1": "US", "rating": "PG"}]) == "PG"
    )
    assert bj._tags_from_metadata(_meta(genres=["Action"]), {}) == ["ação"]
    assert bj._tags_from_metadata(_meta(category="GAME"), {}) == []
    assert bj._tags_from_metadata(
        _meta(category="MOVIE"), {"genres": [{"name": "Drama"}]}
    ) == ["drama"]
    monkeypatch.setattr(
        bj.langcodes.Language,
        "make",
        Mock(side_effect=bj.LanguageTagError("bad")),
    )
    assert bj._language_display_name("bad_tag", []) == "bad_tag"


def test_html_helpers_cover_title_identifier_and_overview() -> None:
    soup = BeautifulSoup(
        """
        <div class='box'><div class='head'>Informações</div><table>
        <tr><td>Título Original:</td><td>Canonical</td></tr>
        <tr><td>IMDB:</td><td><a href='https://imdb.com/title/tt1234567'>IMDb</a></td></tr>
        </table></div>
        <div class='torrent_description'><div class='body'>
        <blockquote class='center'>ignore</blockquote><blockquote>Overview</blockquote>
        </div></div>
        """,
        "html.parser",
    )
    box = bj._information_box(soup)
    assert box is not None
    assert bj._database_title_from_box(box) == "Canonical"
    assert bj._database_identifier_from_box(box) == "tt1234567"
    assert (
        bj._identifier_from_href("tmdb", "https://themoviedb.org/movie/44")
        == "movie/44"
    )
    assert bj._identifier_from_href("other", "https://example.com") is None
    decorative = soup.find("blockquote", class_="center")
    assert decorative is not None
    assert bj._blockquote_overview(decorative) is None
    tracker = _tracker()
    assert tracker.get_database_title(soup) == "Canonical"
    assert tracker.get_database_identifier(soup) == "tt1234567"
    assert tracker.get_database_overview(soup) == "Overview"
    assert (
        tracker.get_database_overview(
            BeautifulSoup("<div></div>", "html.parser")
        )
        == ""
    )


def test_constructor_and_basic_metadata_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setattr(bj, "TmdbManager", lambda _config: sentinel)
    monkeypatch.setattr(bj, "Common", lambda _config: sentinel)
    monkeypatch.setattr(bj, "CookieValidator", lambda _config: sentinel)
    monkeypatch.setattr(bj, "CookieAuthUploader", lambda _config: sentinel)
    fake_client = SimpleNamespace()
    monkeypatch.setattr(bj.httpx, "AsyncClient", lambda **_kwargs: fake_client)
    tracker = BJShare(CONFIG)
    assert tracker.tmdb_manager is sentinel
    assert tracker.common is sentinel
    assert tracker.cookie_validator is sentinel
    assert tracker.cookie_auth_uploader is sentinel
    assert tracker.session is fake_client
    assert tracker.has_extension("movie.mkv")
    assert not tracker.has_extension("README")


def test_category_checks_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert tracker._book_upload_allowed(_meta(book_language_iso="por"))
    assert not tracker._book_upload_allowed(_meta(book_language_iso="eng"))
    assert tracker._scene_game_is_archived(_meta(scene=True, container="rar"))
    assert not tracker._scene_game_is_archived(
        _meta(scene=False, container="rar")
    )
    monkeypatch.setattr(
        bj,
        "DescriptionBuilder",
        lambda *_args: SimpleNamespace(
            get_user_description=AsyncMock(return_value=True)
        ),
    )
    assert asyncio.run(
        tracker._game_install_notes_allowed(_meta(platform="PC"))
    )
    assert asyncio.run(
        tracker._game_install_notes_allowed(_meta(platform="SWITCH"))
    )
    assert asyncio.run(
        tracker._game_upload_allowed(_meta(platform="PC", scene=False))
    )
    assert not asyncio.run(
        tracker._game_upload_allowed(
            _meta(platform="PC", scene=True, container="rar")
        )
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            _meta(category="BOOK", book_language_iso="por")
        )
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            _meta(category="GAME", platform="SWITCH")
        )
    )
    assert asyncio.run(
        tracker.get_additional_checks(
            _meta(category="MOVIE", subtitle_files=["x.srt"])
        )
    )
    assert asyncio.run(tracker.get_additional_checks(_meta(category="MOVIE")))
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"session": "cookie"}
    )
    assert asyncio.run(tracker.validate_credentials(_meta()))
    assert tracker.session.cookies == {"session": "cookie"}
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )
    assert not asyncio.run(tracker.validate_credentials(_meta()))


def test_localized_data_and_basic_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    media = _meta(
        category="TV",
        tmdb_localized_data={
            "pt-BR": {
                "main": {"original_language": "pt", "origin_country": ["PT"]},
                "episode": {"name": "Ep"},
            }
        },
    )
    asyncio.run(tracker.load_localized_data(media))
    assert tracker.main_tmdb_data["original_language"] == "pt"
    assert media.episode_tmdb_data == {"name": "Ep"}
    with pytest.raises(RuntimeError):
        asyncio.run(tracker.load_localized_data(_meta(category="MOVIE")))
    assert (
        tracker.get_container(_meta(category="MOVIE", container="mkv"))
        == "MKV"
    )
    assert (
        tracker.get_container(_meta(category="MOVIE", container="mov"))
        == "Outro"
    )
    assert (
        tracker.get_container(
            _meta(category="BOOK", audiobook=True, container="mp3")
        )
        == "MP3"
    )
    assert tracker.get_container(_meta(category="GAME")) == ""
    assert tracker.get_type(_meta(anime=True)) == 13
    assert tracker.get_type(_meta(category="BOOK", audiobook=True)) == 10
    assert tracker.get_type(_meta(category="GAME")) == 3
    tracker.main_tmdb_data = {
        "original_language": "pt",
        "origin_country": ["PT"],
    }
    assert tracker.get_languages() == "Português (pt)"
    tracker.main_tmdb_data = {}
    assert tracker.get_languages() == "Outro"
    assert tracker.get_game_platform(_meta(platform="PS5")) == "18"
    assert tracker.get_game_platform(_meta(platform="unknown")) == "3"
    assert (
        tracker.get_game_language(
            _meta(languages={"Portuguese": {}, "English": {}})
        )
        == "Multilinguagem"
    )
    assert (
        tracker.get_game_language(_meta(languages={"English": {}})) == "Inglês"
    )
    assert tracker.get_game_language(_meta(languages={})) == "Outro"
    assert tracker.get_game_subcategory(_meta(game_subcategory="dlc")) == "3"
    assert (
        tracker.get_game_subcategory(_meta(game_subcategory="unknown")) == "1"
    )
    assert (
        tracker.get_sistema(_meta(available_platforms=["PC", "MAC"]))
        == "Multiplataforma"
    )
    assert tracker.get_sistema(_meta(available_platforms=["Linux"])) == "Linux"
    assert tracker.get_sistema(_meta(available_platforms=[])) == ""
    monkeypatch.setattr(
        bj.languages_manager,
        "process_desc_language",
        AsyncMock(return_value=None),
    )
    audio_meta = _meta(
        language_checked=False,
        audio_languages=["Portuguese"],
        original_language="English",
    )
    assert asyncio.run(tracker.get_audio(audio_meta)) == "Dublado"
    subtitle_meta = _meta(
        language_checked=False, subtitle_languages=["Portuguese"]
    )
    assert asyncio.run(tracker.get_subtitle(subtitle_meta)) == "Embutida"
    assert (
        asyncio.run(tracker.get_subtitle(_meta(subtitle_languages=[])))
        == "Nenhuma"
    )


def test_resolution_codec_name_title_description_and_trailer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert tracker.get_resolution(
        _meta(is_disc="BDMV", resolution="1080p")
    ) == ("1920", "1080")
    assert tracker.get_resolution(_meta(is_disc="BDMV", resolution="bad")) == (
        "0",
        "0",
    )
    assert tracker.get_resolution(_meta()) == ("1920", "1080")
    assert (
        tracker.get_video_codec(_meta(video_encode="x265", video_codec="HEVC"))
        == "x265"
    )
    assert (
        tracker.get_video_codec(_meta(video_encode="", video_codec="Custom"))
        == "Custom"
    )
    assert tracker.get_audio_codec(_meta(audio="TRUEHD Atmos")) == "TrueHD"
    assert tracker.get_audio_codec(_meta(audio="")) == "Outro"
    tracker.common.portuguese_title_capitalization = lambda value: (
        f"PT:{value}"
    )
    assert tracker.get_titles(_meta(category="BOOK", title="book")) == (
        "PT:book",
        "",
    )
    assert tracker.get_titles(_meta(category="GAME", title="game")) == (
        "game",
        "",
    )
    assert tracker.get_titles(_meta(category="OTHER")) == ("", "")
    tracker.main_tmdb_data = {"videos": {"results": [{"key": "yt"}]}}
    assert tracker.get_trailer(_meta()) == "http://www.youtube.com/watch?v=yt"
    tracker.main_tmdb_data = {}
    assert tracker.get_trailer(_meta(youtube="fallback")) == "fallback"
    tracker.episode_tmdb_data = {"name": "Episode"}
    generator = AsyncMock(return_value="description")
    monkeypatch.setattr(
        bj,
        "DescriptionBuilder",
        lambda *_args: SimpleNamespace(
            general_description_generator=generator
        ),
    )
    meta = _meta()
    assert asyncio.run(tracker.build_description(meta)) == "description"
    assert meta.episode_tmdb_data == {"name": "Episode"}
    tracker.common.portuguese_title_capitalization = lambda value: str(value)
    assert (
        asyncio.run(tracker.get_name(_meta(category="GAME", title="Game")))
        == "Game"
    )


def test_rating_tags_and_prompt_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    tracker.main_tmdb_data = {
        "content_ratings": {"results": [{"iso_3166_1": "BR", "rating": "14"}]}
    }
    assert tracker.get_rating() == "14 anos"
    tracker.main_tmdb_data = {"genres": [{"name": "Action"}]}
    assert asyncio.run(tracker.get_tags(_meta(category="MOVIE"))) == "acao"
    unattended = _meta(
        category="GAME", unattended=True, unattended_confirm=False
    )
    tracker.main_tmdb_data = {}
    assert asyncio.run(tracker.get_tags(unattended)) == ""
    assert unattended.skipping == "BJSHARE"
    monkeypatch.setattr(
        bj, "prompt_in_thread", AsyncMock(return_value="Ação, Drama")
    )
    assert (
        asyncio.run(tracker.get_tags(_meta(category="GAME"))) == "Acao, Drama"
    )


def test_bitrate_runtime_release_and_remaster() -> None:
    tracker = _tracker()
    assert (
        tracker.get_bitrate(
            _meta(type="DISC", is_disc="BDMV", disctype="BD50")
        )
        == "BD50"
    )
    assert (
        tracker.get_bitrate(
            _meta(
                type="DISC", is_disc="BDMV", disctype="", bdinfo={"size": 70}
            )
        )
        == "BD100"
    )
    assert (
        tracker.get_bitrate(
            _meta(
                type="DISC", is_disc="BDMV", disctype="", bdinfo={"size": 55}
            )
        )
        == "BD66"
    )
    assert (
        tracker.get_bitrate(
            _meta(
                type="DISC", is_disc="BDMV", disctype="", bdinfo={"size": 30}
            )
        )
        == "BD50"
    )
    assert (
        tracker.get_bitrate(
            _meta(
                type="DISC", is_disc="BDMV", disctype="", bdinfo={"size": 10}
            )
        )
        == "BD25"
    )
    assert (
        tracker.get_bitrate(_meta(type="DISC", is_disc="DVD", dvd_size="DVD5"))
        == "DVD5"
    )
    assert (
        tracker.get_bitrate(_meta(type="DISC", is_disc="DVD", dvd_size=""))
        == "DVD9"
    )
    assert tracker.get_bitrate(_meta(type="WEBRIP")) == "WEBRip"
    assert tracker.get_bitrate(_meta(type="unknown")) == "Outro"
    assert (
        tracker.get_audiobook_bitrate(_meta(audiobook_bitrate=None)) == "Outro"
    )
    assert tracker.get_audiobook_bitrate(_meta(audiobook_bitrate=130)) == "128"
    assert (
        tracker.get_audiobook_bitrate(_meta(audiobook_bitrate=400)) == "Outro"
    )
    assert tracker.get_runtime(_meta(video_duration=125)) == (2, 5)
    assert tracker.get_runtime(_meta(video_duration=None)) == (1, 0)
    tracker.main_tmdb_data = {"release_date": "2024-01-02"}
    assert tracker.get_release_date() == "02 Jan 2024"
    tracker.main_tmdb_data = {"release_date": "bad"}
    assert tracker.get_release_date() == ""
    tracker.main_tmdb_data = {}
    assert tracker.get_release_date() == ""
    meta = _meta(
        edition="Extended",
        audio="Atmos",
        bit_depth="10",
        hdr="DV HDR10+",
        type="REMUX",
        extras=True,
        has_commentary=True,
    )
    tags = tracker.find_remaster_tags(meta)
    assert {
        "Extended Edition",
        "Dolby Atmos",
        "10-bit",
        "Dolby Vision",
        "HDR10+",
        "Remux",
        "Com extras",
        "Com comentários",
    } <= tags
    assert "Extended Edition" in tracker.build_remaster_title(meta)


def test_year_adult_imdb_overview_and_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert tracker.get_year(_meta(category="MOVIE", year=2024)) == "2024"
    assert (
        tracker.get_year(
            _meta(
                category="TV",
                tvdb_episode_year="2023",
                imdb_info={"tv_year": "2022"},
            )
        )
        == "2023"
    )
    assert (
        tracker.get_year(
            _meta(
                category="TV",
                tvdb_episode_year="",
                imdb_info={"tv_year": "2022"},
            )
        )
        == "2022"
    )
    assert tracker.get_year(_meta(category="TV", year=None)) == "N/A"
    assert tracker.get_adulto(_meta(adult_media=True)) == "1"
    assert (
        tracker.get_adulto(_meta(anime=True, combined_genres="hentai")) == "1"
    )
    assert tracker.get_adulto(_meta(combined_genres=", porn,")) == "1"
    assert tracker.get_adulto(_meta()) == "2"
    BJShare.database_identifier = "ttdb"
    assert tracker.get_imdblink(_meta()) == "ttdb"
    BJShare.database_identifier = ""
    assert tracker.get_imdblink(_meta(imdb_info={"imdbID": "tt1"})) == "tt1"
    assert tracker.get_imdblink(_meta(category="TV", tmdb_id="42")) == "tv/42"
    assert tracker.get_imdblink(_meta(category="GAME")) == ""
    BJShare.database_overview = ""
    tracker.main_tmdb_data = {"overview": "TMDB"}
    assert asyncio.run(tracker.get_overview(_meta())) == "TMDB"
    tracker.main_tmdb_data = {}
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert asyncio.run(tracker.get_overview(unattended)) == ""
    assert unattended.skipping == "BJSHARE"
    monkeypatch.setattr(
        bj, "prompt_in_thread", AsyncMock(return_value="Manual")
    )
    assert asyncio.run(tracker.get_overview(_meta())) == "Manual"
    assert tracker.check_data(
        _meta(category="MOVIE", debug=False),
        {"screenshots[]": [], "imdblink": "tt1"},
    )
    assert tracker.check_data(
        _meta(category="MOVIE"),
        {"screenshots[]": [], "diretor": "skipped", "imdblink": "tt1"},
    )
    assert (
        tracker.check_data(
            _meta(category="MOVIE"), {"screenshots[]": [], "imdblink": ""}
        )
        == "Missing IMDb or TMDb identifier."
    )
    assert (
        tracker.check_data(_meta(category="GAME"), {"plataforma": ""})
        == "Missing game platform."
    )
    assert (
        tracker.check_data(_meta(category="BOOK"), {"formato": ""})
        == "Missing compatible ebook format."
    )
    assert tracker.check_data(_meta(category="BOOK"), {"formato": "PDF"}) == ""


def test_credits_and_request_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert tracker._normalize_credit_name("José   Álvarez") == "Jose Alvarez"
    assert tracker._collect_credit_names(["A", "a", None, "B"], 2) == [
        "A",
        "B",
    ]
    assert tracker._credit_role_config("director") == (
        "directors",
        "tmdb_directors",
        "Diretor",
        1,
    )
    assert tracker._credit_role_config("bad") is None
    assert tracker._credit_source_names(
        _meta(cast=["Cast"]), "cast", "stars", "tmdb_cast"
    ) == ["Cast"]
    meta = _meta(imdb_info={"directors": ["Director"]})
    assert asyncio.run(tracker.get_credits(meta, "director")) == "Director"
    BJShare.already_has_the_info = True
    assert asyncio.run(tracker.get_credits(meta, "director")) == "N/A"
    BJShare.already_has_the_info = False
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert (
        asyncio.run(tracker.get_credits(unattended, "director")) == "skipped"
    )
    monkeypatch.setattr(
        bj, "prompt_in_thread", AsyncMock(return_value="Manual Person")
    )
    assert (
        asyncio.run(tracker.get_credits(_meta(), "director"))
        == "Manual Person"
    )
    assert tracker.get_imdb_rating(_meta(imdb_info={"rating": "7.5"})) == "7.5"
    assert tracker.get_imdb_rating(_meta()) == "N/A"
    row = BeautifulSoup(
        "<tr class='torrent'><td></td><td><a href='requests.php?action=view&id=1'>Name</a><b>1080p</b></td><td></td><td><table><tr><td>10 GB</td></tr></table></td><td></td></tr>",
        "html.parser",
    ).find("tr")
    assert row is not None
    parsed = tracker._parse_request_row(row)
    assert parsed == {
        "Name": "Name",
        "Quality": "1080p",
        "Reward": "10 GB",
        "Link": "requests.php?action=view&id=1",
    }
    table_html = f"<table id='torrent_table'>{row}</table>"
    assert tracker._parse_request_results(table_html) == [parsed]
    assert "Name" in tracker._request_message([parsed])


def test_requests_success_disabled_and_error() -> None:
    tracker = _tracker()
    meta = _meta(search_requests=False)
    assert asyncio.run(tracker.get_requests(meta)) == []
    meta.search_requests = True
    html = "<table id='torrent_table'><tr class='torrent'><td></td><td><a href='requests.php?action=view&id=1'>Name</a><b>1080p</b></td><td></td><td><table><tr><td>10 GB</td></tr></table></td><td></td></tr></table>"
    tracker.session.get = AsyncMock(return_value=_response(html))
    results = asyncio.run(tracker.get_requests(meta))
    assert results[0]["Name"] == "Name"
    tracker.session.get = AsyncMock(side_effect=RuntimeError("boom"))
    assert asyncio.run(tracker.get_requests(meta)) == []


def test_search_parser_details_and_table_paths() -> None:
    tracker = _tracker()
    BJShare.database_title = "Canonical"
    details_html = """
    <a href='logout.php?auth=abcdef'></a>
    <div class='box'><div class='head'>Informações</div><table><tr><td>Título:</td><td>Canonical</td></tr></table></div>
    <div class='main_column'><tr id='torrent12' data-torrentname='Book.pdf' data-format='pdf'><td class='number_column nobr'>1 GB</td></tr></div>
    """
    details = tracker._parse_search_response(
        _meta(category="BOOK"), _response(details_html)
    )
    assert details and details[0]["id"] == "12"
    table_html = """
    <table id='torrent_table'><tr class='torrent'>
      <td></td><td><a href='torrents.php?torrentid=99'>Site Name</a><div class='torrent_info' data-torrentname='Data Name.mkv'></div></td>
      <td></td><td></td><td>2 GB</td>
    </tr></table>
    """
    table = tracker._parse_search_response(
        _meta(category="MOVIE"), _response(table_html)
    )
    assert table and table[0]["id"] == "99"
    assert (
        tracker._parse_search_response(_meta(), _response("<div></div>")) == []
    )


def test_search_request_auth_failure_and_fallback() -> None:
    tracker = _tracker()
    meta = _meta(category="TV", title="Title", imdb_info={}, tmdb_id="")
    login = _response("login.php", url="https://bj-share.info/login.php")
    tracker.session.get = AsyncMock(return_value=login)
    assert (
        asyncio.run(
            tracker._request_search_page(meta, "url", {"searchstr": "x"})
        )
        is None
    )
    assert meta.skipping == "BJSHARE"
    meta.skipping = None
    tracker.session.get = AsyncMock(return_value=_response("<html></html>"))
    assert (
        asyncio.run(
            tracker._request_search_page(meta, "url", {"searchstr": "x"})
        )
        is None
    )
    assert meta.skipping == "BJSHARE"
    meta.skipping = None
    response = _response(
        '<a href="logout.php?auth=abcdef"></a><table id="torrent_table"></table>'
    )
    tracker.session.get = AsyncMock(return_value=response)
    assert asyncio.run(tracker.search_existing(meta)) == []
    assert BJShare.secret_token == "abcdef"
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"session": "1"}
    )
    asyncio.run(tracker._load_search_cookies(meta))
    assert tracker.session.cookies == {"session": "1"}


def test_cover_and_screenshot_paths(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.img_host = AsyncMock(return_value="https://host/image.png")  # type: ignore[method-assign]
    tracker.main_tmdb_data = {"poster_path": "/poster.jpg"}
    BJShare.already_has_the_info = True
    assert (
        asyncio.run(tracker.get_cover(_meta(category="MOVIE")))
        == "https://image.tmdb.org/t/p/w500/poster.jpg"
    )
    BJShare.already_has_the_info = False
    tracker.session.get = AsyncMock(return_value=_response(status=200))
    assert (
        asyncio.run(tracker.get_cover(_meta(category="MOVIE")))
        == "https://host/image.png"
    )
    tracker.main_tmdb_data = {}
    assert asyncio.run(tracker.get_cover(_meta(category="MOVIE"))) is None
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"image")
    tracker.common.path_exists = AsyncMock(return_value=True)
    assert (
        asyncio.run(
            tracker.get_cover(_meta(category="BOOK", artwork_path=str(cover)))
        )
        == "https://host/image.png"
    )
    tracker.common.path_exists = AsyncMock(return_value=False)
    assert (
        asyncio.run(
            tracker.get_cover(_meta(category="BOOK", artwork_path=str(cover)))
        )
        is None
    )
    tracker.common.path_exists = AsyncMock(return_value=True)
    assert (
        asyncio.run(
            tracker.get_cover(
                _meta(category="BOOK", artwork_path=cast(Any, True))
            )
        )
        is None
    )
    tracker.common.path_exists.assert_not_awaited()
    screens = tmp_path / "tmp" / "release" / "screenshots"
    screens.mkdir(parents=True)
    (screens / "1.png").write_bytes(b"image")
    tracker.session.get = AsyncMock(return_value=_response(status=200))
    result = asyncio.run(
        tracker.get_screenshots(_meta(base_dir=str(tmp_path), uuid="release"))
    )
    assert result == ["https://host/image.png"]


def test_img_host_success_bad_payload_and_error() -> None:
    tracker = _tracker()
    success = httpx.Response(
        200,
        json={"url": "https:\\/\\/img.example\\/a.png"},
        request=httpx.Request("POST", "https://x"),
    )
    tracker.session.post = AsyncMock(return_value=success)
    assert (
        asyncio.run(tracker.img_host(b"x", "a.png"))
        == "https://img.example/a.png"
    )
    bad = httpx.Response(
        200, json={"url": "bad"}, request=httpx.Request("POST", "https://x")
    )
    tracker.session.post = AsyncMock(return_value=bad)
    assert asyncio.run(tracker.img_host(b"x", "a.png")) is None
    tracker.session.post = AsyncMock(side_effect=RuntimeError("boom"))
    assert asyncio.run(tracker.img_host(b"x", "a.png")) is None


def test_payload_helpers_and_data_builder() -> None:
    tracker = _tracker()
    tracker.load_localized_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"s": "1"}
    )
    tracker.build_description = AsyncMock(return_value="desc")  # type: ignore[method-assign]
    tracker.get_tags = AsyncMock(return_value="tags")  # type: ignore[method-assign]
    tracker.get_cover = AsyncMock(return_value="cover")  # type: ignore[method-assign]
    tracker.get_screenshots = AsyncMock(return_value=["s1", "s2"])  # type: ignore[method-assign]
    tracker.get_audio = AsyncMock(return_value="Nacional")  # type: ignore[method-assign]
    tracker.get_subtitle = AsyncMock(return_value="Nenhuma")  # type: ignore[method-assign]
    tracker.get_overview = AsyncMock(return_value="overview")  # type: ignore[method-assign]
    tracker.get_credits = AsyncMock(return_value="Person")  # type: ignore[method-assign]
    BJShare.secret_token = "token"
    book = _meta(
        category="BOOK",
        title="Book",
        book_language_iso="por",
        author="Author",
        debug=False,
    )
    data = asyncio.run(tracker.get_data(book))
    assert data["title"] == "Book"
    assert data["idioma"] == "Português"
    assert data["image"] == "cover"
    game = _meta(
        category="GAME",
        platform="PC",
        tag="-GROUP",
        game_version="1.2",
        available_platforms=["PC"],
        debug=False,
    )
    data = asyncio.run(tracker.get_data(game))
    assert data["release"] == "GROUP"
    assert data["versao"] == "1.2"
    assert data["plataforma"] == "3"
    movie = _meta(
        category="MOVIE",
        debug=False,
        imdb_info={"rating": "8.0", "imdbID": "tt1"},
    )
    tracker.main_tmdb_data = {
        "original_language": "en",
        "origin_country": ["US"],
    }
    data = asyncio.run(tracker.get_data(movie))
    assert data["diretor"] == "Person"
    assert data["elenco"] == "Person"
    assert data["screenshots[]"] == ["s1", "s2"]
    tv = _meta(
        category="TV", debug=True, imdb_info={"rating": "8.0", "imdbID": "tt1"}
    )
    tracker.main_tmdb_data = {
        "original_language": "en",
        "origin_country": ["US"],
        "networks": [{"name": "Net"}],
        "number_of_seasons": 2,
    }
    data = asyncio.run(tracker.get_data(tv))
    assert data["tipo"] == "episode"
    assert data["network"] == "Net"


def test_payload_flags_and_console_fields() -> None:
    tracker = _tracker()
    assert (
        tracker._book_language_name(_meta(book_language_iso="spa"))
        == "Espanhol"
    )
    assert (
        tracker._book_language_name(_meta(book_language_iso="xyz")) == "Outro"
    )
    assert (
        tracker._game_release_description(
            _meta(localized_overviews={"brazilian": "BR"})
        )
        == "BR"
    )
    assert tracker._pc_game_fields(
        _meta(platform="PC", tag="-G", game_version="1")
    ) == {"release": "G", "versao": "1"}
    assert tracker._pc_game_fields(_meta(platform="SWITCH")) == {}
    assert tracker._game_unlock_type(_meta(container="NSP")) == "NSP"
    assert tracker._game_unlock_type(_meta(container="zip")) == ""
    assert tracker._console_game_fields(
        _meta(
            platform="SWITCH",
            game_system="Switch",
            game_region="US",
            container="NSP",
        )
    ) == {"sistema": "Switch", "regiao": "US", "destravamento": "NSP"}
    tracker.config["TRACKERS"]["BJSHARE"].update(
        {"anon": True, "show_group_if_anon": True}
    )
    assert tracker._anonymous_fields(_meta()) == {
        "anonymous": "on",
        "anonymousshowgroup": "on",
    }
    tracker.config["TRACKERS"]["BJSHARE"].update(
        {"internal": True, "internal_groups": ["GROUP"]}
    )
    assert tracker._internal_fields(_meta(tag="-GROUP")) == {"internalrel": 1}
    assert tracker._internal_fields(_meta(tag="-OTHER")) == {}
    assert tracker._anime_fields(_meta(category="MOVIE")) == {"tipo": "movie"}
    assert tracker._anime_fields(_meta(category="TV")) == {"adulto": "2"}


def test_upload_paths() -> None:
    tracker = _tracker()
    meta = _meta(skipping="BJSHARE")
    assert asyncio.run(tracker.upload(meta)) is False
    meta = _meta()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    tracker.check_data = Mock(return_value="issue")  # type: ignore[method-assign]
    assert asyncio.run(tracker.upload(meta)) is False
    assert (
        meta.tracker_status["BJSHARE"]["status_message"]
        == "data error - issue"
    )


def test_remaining_html_negative_branches() -> None:
    fake_soup = SimpleNamespace(find_all=lambda *_args, **_kwargs: [object()])
    assert bj._information_box(fake_soup) is None
    assert bj._database_title_from_row(object()) is None
    short_row = BeautifulSoup("<tr><td>Only</td></tr>", "html.parser").find(
        "tr"
    )
    assert short_row is not None
    assert bj._database_title_from_row(short_row) is None
    wrong_row = BeautifulSoup(
        "<tr><td>Other:</td><td>Value</td></tr>", "html.parser"
    ).find("tr")
    assert wrong_row is not None
    assert bj._database_title_from_row(wrong_row) is None
    empty_box = BeautifulSoup(
        "<div class='box'><table><tr><td>X</td></tr></table></div>",
        "html.parser",
    ).find("div")
    assert empty_box is not None
    assert bj._database_title_from_box(empty_box) == ""
    assert bj._database_identifier_from_row(object()) is None
    assert bj._database_identifier_from_row(short_row) is None
    desc = BeautifulSoup(
        "<div class='torrent_description'>Text<script>x</script><style>x</style><iframe></iframe></div>",
        "html.parser",
    ).find("div")
    assert desc is not None
    assert bj._description_body_text(desc) == "Text"


def test_remaining_game_and_name_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        bj,
        "DescriptionBuilder",
        lambda *_args: SimpleNamespace(
            get_user_description=AsyncMock(return_value=False)
        ),
    )
    assert not asyncio.run(
        tracker._game_install_notes_allowed(_meta(platform="PC"))
    )
    assert not asyncio.run(tracker._game_upload_allowed(_meta(platform="PC")))
    tracker.common.portuguese_title_capitalization = lambda value: str(value)
    BJShare.database_title = ""
    meta = _meta(
        category="MOVIE",
        title="Original",
        tmdb_localized_data={
            "pt-BR": {
                "main": {"title": "Brazil", "original_title": "Original"}
            }
        },
    )
    assert asyncio.run(tracker.get_name(meta)) == "Brazil [Original]"


def test_remaining_search_query_and_fallback_branches() -> None:
    tracker = _tracker()
    tracker.common.portuguese_title_capitalization = lambda value: (
        f"PT:{value}"
    )
    book = _meta(category="BOOK", title="book", audiobook=True)
    assert tracker._search_title(book) == "PT:book"
    assert (
        tracker._base_search_params(book, "PT:book")["filter_cat[11]"] == "1"
    )
    game = _meta(category="GAME", title="game", platform="PS5")
    assert tracker._base_search_params(game, "game")["plataforma"] == "18"
    assert tracker._media_search_terms(game) == []
    queries, terms, title_queried = tracker._search_queries(game, "game")
    assert queries[0]["searchstr"] == "game"
    assert terms == []
    assert title_queried is False
    tracker._request_search_page = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker._title_fallback_response(game, "url", "game"))
        is None
    )


def test_remaining_search_row_negative_branches() -> None:
    tracker = _tracker()
    invalid = BeautifulSoup("<tr id='torrent_1'></tr>", "html.parser").find(
        "tr"
    )
    assert invalid is not None
    assert tracker._details_row_id(invalid) is None
    no_id = BeautifulSoup("<tr></tr>", "html.parser").find("tr")
    assert no_id is not None
    assert tracker._details_row_id(no_id) is None
    assert tracker._book_format_from_name("unknown") == "ebook"
    assert (
        tracker._book_row_type(_meta(audiobook=True), "pdf", "book.pdf")
        == "audiobook"
    )
    assert tracker._details_row_dupes(_meta(), no_id) == []
    no_name = BeautifulSoup("<tr id='torrent1'></tr>", "html.parser").find(
        "tr"
    )
    assert no_name is not None
    assert tracker._details_row_dupes(_meta(), no_name) == []
    assert tracker._torrent_id_from_link(object(), r"id=(\d+)") is None
    plain_link = BeautifulSoup("<a>No href</a>", "html.parser").find("a")
    assert plain_link is not None
    assert tracker._torrent_id_from_link(plain_link, r"id=(\d+)") is None
    download = BeautifulSoup(
        "<tr><td><a href='torrents.php?action=download&id=77'>D</a></td></tr>",
        "html.parser",
    ).find("tr")
    assert download is not None
    assert tracker._search_row_id(download) == ("77", None)
    assert tracker._torrent_info_name(None) == ""
    assert tracker._search_row_names("BOOK", "Data", "Site") == [
        "Data",
        "Site",
    ]
    assert tracker._first_size_text(["not size"]) is None
    assert tracker._search_row_size(no_id) == ""
    assert tracker._search_row_dupes(_meta(), no_id) == []


def test_remaining_title_fallback_branches() -> None:
    tracker = _tracker()
    meta = _meta(category="TV")
    current = _response("<table></table>")
    result = asyncio.run(
        tracker._apply_title_fallback(
            meta,
            "url",
            "Title",
            current,
            current,
            False,
            [],
        )
    )
    assert result == (current, current)
    tracker._title_fallback_response = AsyncMock(return_value=None)  # type: ignore[method-assign]
    result = asyncio.run(
        tracker._apply_title_fallback(
            meta,
            "url",
            "Title",
            None,
            current,
            False,
            [],
        )
    )
    assert result == (None, None)
    candidate = _response("<div class='main_column'></div>")
    tracker._title_fallback_response = AsyncMock(return_value=candidate)  # type: ignore[method-assign]
    result = asyncio.run(
        tracker._apply_title_fallback(
            meta,
            "url",
            "Title",
            None,
            current,
            False,
            [],
        )
    )
    assert result == (candidate, candidate)


def test_remaining_edition_bitrate_and_bit_depth_branches() -> None:
    tracker = _tracker()
    assert tracker.get_edition(_meta(edition="unknown")) == ""
    assert tracker._bdmv_size(_meta(bdinfo={})) == 0.0
    assert tracker._disc_bitrate(_meta(type="DISC", is_disc="OTHER")) is None
    assert tracker._source_bitrate(None) == "Outro"
    broken = _meta(is_disc="BDMV", discs=[])
    assert not tracker._bdmv_is_10_bit(broken)
    bdmv = _meta(
        is_disc="BDMV",
        discs=[{"bdinfo": {"video": [{"bit_depth": "10 bits"}]}}],
    )
    assert tracker._bdmv_is_10_bit(bdmv)
    assert tracker._is_10_bit(bdmv)
    assert tracker._hdr_tags("HDR") == {"HDR10"}


def test_remaining_cover_failure_and_unknown_category(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.img_host = AsyncMock(return_value="host")  # type: ignore[method-assign]
    tracker.session.get = AsyncMock(side_effect=RuntimeError("network"))
    assert (
        asyncio.run(
            tracker._upload_remote_cover("https://x/poster.jpg", "poster.jpg")
        )
        is None
    )
    tracker.common.path_exists = AsyncMock(return_value=True)
    missing = tmp_path / "missing.png"
    assert (
        asyncio.run(
            tracker._local_cover(
                _meta(category="BOOK", artwork_path=str(missing))
            )
        )
        is None
    )
    assert asyncio.run(tracker.get_cover(_meta(category="OTHER"))) is None


def test_remaining_remote_screenshot_and_image_list_path(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    tracker.img_host = AsyncMock(return_value="host")  # type: ignore[method-assign]
    tracker.session.get = AsyncMock(return_value=_response(status=200))
    assert (
        asyncio.run(tracker._upload_remote_screenshot("https://x/screen.png"))
        == "host"
    )
    tracker.session.get = AsyncMock(side_effect=RuntimeError("network"))
    assert (
        asyncio.run(tracker._upload_remote_screenshot("https://x/screen.png"))
        is None
    )
    tracker.session.get = AsyncMock(return_value=_response(status=200))
    result = asyncio.run(
        tracker.get_screenshots(
            _meta(
                base_dir=str(tmp_path),
                uuid="remote-only",
                image_list=[{"raw_url": "https://x/screen.png"}],
            )
        )
    )
    assert result == ["host"]


def test_remaining_credit_and_request_invalid_branches() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_credits(_meta(), "bad")) == "N/A"
    assert (
        tracker._request_title(_meta(category="BOOK", title="book")) == "Book"
    )
    assert tracker._request_category(_meta(anime=True)) == 14
    info = BeautifulSoup("<td><span>No link</span></td>", "html.parser").find(
        "td"
    )
    assert info is not None
    assert tracker._request_link_and_quality(info) is None
    short = BeautifulSoup("<tr><td>x</td></tr>", "html.parser").find("tr")
    assert short is not None
    assert tracker._parse_request_row(short) is None
    bad = BeautifulSoup(
        "<tr><td></td><td>No link</td><td></td><td></td><td></td></tr>",
        "html.parser",
    ).find("tr")
    assert bad is not None
    assert tracker._parse_request_row(bad) is None
    assert (
        tracker._parse_request_results(
            "<table id='torrent_table'><tr class='torrent'><td>x</td></tr></table>"
        )
        == []
    )


def test_remaining_request_cookie_and_payload_branches() -> None:
    tracker = _tracker()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"a": "b"}
    )
    tracker.session.get = AsyncMock(
        return_value=_response("<table id='torrent_table'></table>")
    )
    assert (
        asyncio.run(tracker._request_results(_meta(search_requests=True)))
        == []
    )
    assert tracker.session.cookies == {"a": "b"}
    tracker.build_description = AsyncMock(return_value="desc")  # type: ignore[method-assign]
    book = _meta(
        category="BOOK", audiobook=True, audiobook_bitrate=128, author="A"
    )
    book_data = asyncio.run(tracker._book_upload_data(book, "Book"))
    assert book_data["bitrate"] == "128"
    tracker.get_tags = AsyncMock(return_value="tags")  # type: ignore[method-assign]
    game = _meta(
        category="GAME",
        platform="SWITCH",
        available_platforms=["SWITCH"],
        game_system="Switch",
        game_region="EU",
        container="NSP",
        repack="REPACK",
    )
    game_data = asyncio.run(tracker._game_upload_data(game, "Game"))
    assert game_data["repack"] == "on"
    assert game_data["destravamento"] == "NSP"
    assert tracker._anime_fields(_meta(category="OTHER")) == {}
    assert (
        asyncio.run(
            tracker._category_upload_data(_meta(category="OTHER"), "x", "")
        )
        == {}
    )


def test_remaining_get_data_repack_and_validation_success() -> None:
    tracker = _tracker()
    tracker.load_localized_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )
    tracker._category_upload_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    tracker._image_fields = AsyncMock(return_value={})  # type: ignore[method-assign]
    data = asyncio.run(tracker.get_data(_meta(repack="REPACK")))
    assert data["repack"] == "on"
    assert (
        tracker._media_data_issue(
            _meta(category="MOVIE", debug=True),
            {
                "screenshots[]": [],
                "diretor": "A",
                "elenco": "B",
                "imdblink": "tt1",
            },
        )
        == ""
    )
    assert (
        tracker._game_data_issue(
            _meta(category="GAME", debug=True),
            {"plataforma": "3", "screenshots[]": []},
        )
        == ""
    )


def test_upload_success_and_skip_after_data() -> None:
    tracker = _tracker()
    meta = _meta()
    tracker.get_data = AsyncMock(return_value={})  # type: ignore[method-assign]
    tracker.check_data = Mock(return_value="")  # type: ignore[method-assign]
    tracker.cookie_auth_uploader = SimpleNamespace(
        handle_upload=AsyncMock(return_value=True)
    )
    assert asyncio.run(tracker.upload(meta)) is True
    tracker.get_data = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda current: (
            setattr(current, "skipping", "BJSHARE") or {}
        )
    )
    assert asyncio.run(tracker.upload(_meta())) is False


def test_final_overview_and_search_details_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    soup = BeautifulSoup(
        "<div class='torrent_description'><div class='body'>Fallback</div></div>",
        "html.parser",
    )
    assert tracker.get_database_overview(soup) == "Fallback"
    desc = soup.find("div", class_="torrent_description")
    assert desc is not None
    monkeypatch.setattr(desc, "find_all", Mock(return_value=[object()]))
    monkeypatch.setattr(
        bj, "_description_body_text", Mock(return_value="Guarded")
    )
    assert tracker.get_database_overview(soup) == "Guarded"
    candidate = _response("<div class='main_column'></div>")
    tracker._request_search_page = AsyncMock(return_value=candidate)  # type: ignore[method-assign]
    tracker._response_has_details = Mock(return_value=True)  # type: ignore[method-assign]
    details, fallback = asyncio.run(
        tracker._run_search_queries(_meta(), "url", [{"searchstr": "x"}])
    )
    assert details is candidate
    assert fallback is candidate
    tracker._request_search_page = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(
        tracker._run_search_queries(_meta(), "url", [{"searchstr": "x"}])
    ) == (None, None)


def test_final_search_guards_and_skip_branch() -> None:
    tracker = _tracker()
    assert tracker._book_format_from_name("book.epub") == "epub"
    link = BeautifulSoup("<a href='x'>x</a>", "html.parser").find("a")
    assert link is not None
    link.attrs["href"] = ["not", "a", "string"]
    assert tracker._torrent_id_from_link(link, r"id=(\d+)") is None
    unnamed = BeautifulSoup(
        "<tr><td><a href='torrents.php?torrentid=1'></a></td></tr>",
        "html.parser",
    ).find("tr")
    assert unnamed is not None
    assert tracker._search_row_dupes(_meta(), unnamed) == []
    tracker._load_search_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker._run_search_queries = AsyncMock(return_value=(None, None))  # type: ignore[method-assign]
    meta = _meta(skipping="BJSHARE")
    assert asyncio.run(tracker.search_existing(meta)) == []


def test_final_invalid_request_row_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_soup = SimpleNamespace(select=lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(bj, "BeautifulSoup", Mock(return_value=fake_soup))
    assert BJShare._parse_request_results("ignored") == []
