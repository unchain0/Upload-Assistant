from __future__ import annotations

import importlib
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.trackers.brasiltracker import BrasilTracker

bt_module = importlib.import_module("src.integrations.trackers.brasiltracker")


def _config(**tracker_values: object) -> dict[str, Any]:
    tracker: dict[str, Any] = {
        "anon": False,
        "internal": False,
        "internal_groups": [],
    }
    tracker.update(tracker_values)
    return {
        "TRACKERS": {"BRASILTRACKER": tracker},
        "DEFAULT": {"tmdb_api": "0123456789abcdef0123456789abcdef"},
    }


def _tracker(**tracker_values: object) -> BrasilTracker:
    return BrasilTracker(_config(**tracker_values))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "filename": "movie.mkv",
        "basename_no_ext": "Movie",
        "filelist": [str(root / "movie.mkv")],
        "video": str(root / "movie.mkv"),
        "name": "Movie 2024 1080p WEB-DL-GROUP",
        "title": "Movie",
        "category": "MOVIE",
        "platform": "PC",
        "container": "mkv",
        "audiobook": False,
        "magazine": False,
        "comic": False,
        "manga": False,
        "anime": False,
        "adult_media": False,
        "tmdb_adult_media": False,
        "imdb_info": {"imdbID": "tt123", "rating": "7.0", "directors": []},
        "tmdb_localized_data": {
            "pt-BR": {"main": {"original_language": "en"}, "episode": {}}
        },
        "tmdb_directors": [],
        "genres": ["Drama"],
        "keywords": [],
        "languages": {"English": {}},
        "audio_languages": ["English"],
        "subtitle_languages": [],
        "language_checked": True,
        "original_language": "en",
        "resolution": "1080p",
        "is_disc": "",
        "mediainfo": {
            "media": {
                "track": [
                    {"@type": "General"},
                    {"@type": "Video", "Width": "1920", "Height": "1080"},
                ]
            }
        },
        "video_encode": "x264",
        "video_codec": "AVC",
        "audio": "DD+ 5.1",
        "hdr": "",
        "edition": "",
        "disctype": "",
        "dvd_size": "DVD9",
        "type": "WEBDL",
        "year": 2024,
        "three_d": False,
        "scene": False,
        "tv_pack": False,
        "season": "S01",
        "episode": "E01",
        "runtime": 120,
        "screens": 2,
        "menu_images": [],
        "image_list": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "localized_overviews": {},
        "overview": "Overview",
        "artwork_path": "",
        "artwork_url": "",
        "hosted_artwork": [],
        "tag": "-GROUP",
        "anon": 0,
        "manual_edition": "",
        "manual_episode": "",
        "author": "Author",
        "publisher": "Publisher",
        "book_language_iso": "en",
        "audiobook_bitrate": 128,
        "skipping": "",
        "youtube": "",
        "igdb_first_release_date": "2024-01-01",
        "igdb_rating_count": 10,
        "igdb_rating": 8.5,
        "tmdb_poster_path": "/poster.jpg",
        "backdrop": "",
        "tracker_status": {"BRASILTRACKER": {}},
    }
    state.update(values)
    return Meta(state)


def _response(
    *,
    status: int = 200,
    text: str = "",
    url: str = "https://brasiltracker.org/torrents.php",
) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), text=text)


@pytest.mark.asyncio
async def test_brasiltracker_validate_credentials_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    cookies = httpx.Cookies({"sid": "x"})
    monkeypatch.setattr(
        tracker.cookie_validator,
        "load_session_cookies",
        AsyncMock(return_value=cookies),
    )
    assert await tracker.validate_credentials(_meta())
    assert tracker.session.cookies is not None


@pytest.mark.asyncio
async def test_brasiltracker_game_installation_policy_failure_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        bt_module.DescriptionBuilder,
        "get_user_description",
        AsyncMock(return_value=False),
    )
    assert not await tracker.get_additional_checks(
        _meta(category="GAME", platform="PC")
    )
    monkeypatch.setattr(
        bt_module.DescriptionBuilder,
        "get_user_description",
        AsyncMock(return_value=True),
    )
    assert await tracker._game_installation_policy(
        _meta(category="GAME", platform="PC")
    )


def test_brasiltracker_game_language_and_genre_branches() -> None:
    tracker = _tracker()
    assert (
        tracker.get_game_language(
            _meta(languages={"Portuguese": {}, "English": {}})
        )
        == "Multilinguagem"
    )
    assert (
        tracker.get_game_language(
            _meta(languages={"French": {}, "English": {}})
        )
        == "Francês"
    )
    assert tracker.get_game_genre(_meta(genres=["Action RPG"])) == "Ação"


def test_brasiltracker_game_os_and_format_direct() -> None:
    tracker = _tracker()
    assert tracker.get_game_os(_meta(platform="MAC")) == "Mac"
    assert (
        tracker.get_game_format(_meta(platform="MOBILE", container="zip"))
        == "APK"
    )


@pytest.mark.asyncio
async def test_brasiltracker_get_languages_success_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.main_tmdb_data = {"original_language": "en"}
    assert await tracker.get_languages(_meta())

    class BrokenLanguage:
        @staticmethod
        def display_name(_lang: str) -> str:
            raise bt_module.LanguageTagError("bad")

    monkeypatch.setattr(
        bt_module.langcodes.Language, "make", lambda _code: BrokenLanguage()
    )
    assert await tracker.get_languages(_meta()) == "en"


@pytest.mark.asyncio
async def test_brasiltracker_get_audio_portuguese() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_audio(
            _meta(
                audio_languages=["Portuguese", "English"],
                original_language="en",
            )
        )
        == "Dual Audio"
    )


@pytest.mark.asyncio
async def test_brasiltracker_get_resolution_invalid_bdmv() -> None:
    assert await _tracker().get_resolution(
        _meta(is_disc="BDMV", resolution="invalid")
    ) == ("", "")


@pytest.mark.asyncio
async def test_brasiltracker_video_codec_fallback() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_video_codec(
            _meta(video_encode="", video_codec="VC-1")
        )
        == "VC-1"
    )
    assert (
        await tracker.get_video_codec(_meta(video_encode="", video_codec=""))
        == "Outro"
    )


@pytest.mark.asyncio
async def test_brasiltracker_display_name_with_brazilian_title() -> None:
    localized = {
        "pt-BR": {"main": {"original_title": "Movie", "title": "Filme"}}
    }
    assert (
        await _tracker().get_name(_meta(tmdb_localized_data=localized))
        == "Filme [Movie]"
    )


def test_brasiltracker_genre_tag_helper_returns_mapped() -> None:
    tracker = _tracker()
    assert tracker._genre_tags_for_meta(_meta(genres=["Drama"]))


@pytest.mark.asyncio
async def test_brasiltracker_prompt_genre_unattended() -> None:
    tracker = _tracker()
    item = _meta(unattended=True, unattended_confirm=False)
    assert await tracker._prompt_genre_tags(item) == ""
    assert item.skipping == "BRASILTRACKER"


@pytest.mark.asyncio
async def test_brasiltracker_search_existing_collects_group_dupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    page = BeautifulSoup(
        '<table id="torrent_table"><tr><td><a href="torrents.php?id=1">Group</a></td></tr></table>',
        "html.parser",
    )
    monkeypatch.setattr(tracker, "_search_page", AsyncMock(return_value=page))
    monkeypatch.setattr(
        tracker, "_group_dupes", AsyncMock(return_value=[{"name": "Dupe"}])
    )
    assert await tracker.search_existing(_meta()) == [{"name": "Dupe"}]


@pytest.mark.asyncio
async def test_brasiltracker_search_page_login_token_failure_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    cookies = httpx.Cookies({"sid": "x"})
    monkeypatch.setattr(
        tracker.cookie_validator,
        "load_session_cookies",
        AsyncMock(return_value=cookies),
    )
    tracker.cookie_validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]

    tracker.session.get = AsyncMock(return_value=_response(text="login.php"))  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._search_page(item, "tt123") is None
    assert item.skipping == "BRASILTRACKER"

    tracker.session.get = AsyncMock(return_value=_response(text="no token"))  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._search_page(item, "tt123") is None

    html = 'logout.php?auth=abcdef <table id="torrent_table"></table>'
    tracker.session.get = AsyncMock(return_value=_response(text=html))  # type: ignore[method-assign]
    item = _meta()
    assert await tracker._search_page(item, "tt123") is not None
    assert BrasilTracker.secret_token == "abcdef"


def test_brasiltracker_group_link_helpers() -> None:
    tracker = _tracker()
    empty = BeautifulSoup("<html></html>", "html.parser")
    assert tracker._group_links(empty) == []

    page = BeautifulSoup(
        '<table id="torrent_table"><tr><td><a href="torrents.php?id=2">A</a></td></tr><tr><td><a href="torrents.php?id=3&torrentid=4">B</a></td></tr></table>',
        "html.parser",
    )
    assert tracker._group_links(page) == ["torrents.php?id=2"]
    row = BeautifulSoup("<tr><td>No link</td></tr>", "html.parser").find("tr")
    assert row is not None
    assert tracker._group_row_href(row) == ""


@pytest.mark.asyncio
async def test_brasiltracker_group_dupes_and_torrent_entry() -> None:
    tracker = _tracker()
    html = """
    <table><tr id='torrent7'><td><a onclick='gtoggle(7)'>Release Name</a></td><td>1 GB</td></tr></table>
    <div id='files_7'><div class='filelist_path'>/Folder.Name/</div><table class='filelist_table'><tr class='colhead_dark'><td>Header</td></tr><tr><td>track.mp3</td></tr></table></div>
    """
    tracker.session.get = AsyncMock(return_value=_response(text=html))  # type: ignore[method-assign]
    result = await tracker._group_dupes(
        _meta(category="BOOK", audiobook=True), "torrents.php?id=1"
    )
    assert result[0]["name"] == "track.mp3"
    assert result[0]["type"] == "audiobook"


def test_brasiltracker_torrent_row_and_file_helper_guards() -> None:
    tracker = _tracker()
    page = BeautifulSoup("<html></html>", "html.parser")
    row = BeautifulSoup(
        "<tr id='torrent1'><td>None</td></tr>", "html.parser"
    ).find("tr")
    assert row is not None
    assert tracker._torrent_row_entry(_meta(), page, row) is None
    assert tracker._row_description(row) == ""
    assert tracker._file_names(page) == []

    header = BeautifulSoup(
        "<tr class='colhead_dark'><td>Header</td></tr>", "html.parser"
    ).find("tr")
    assert header is not None
    assert tracker._file_row_name(header) == ""
    assert tracker._folder_name(page) == ""


def test_brasiltracker_torrent_entry_name_folder_and_fallback() -> None:
    tracker = _tracker()
    file_div = BeautifulSoup(
        "<div><div class='filelist_path'>/Folder/</div></div>", "html.parser"
    ).find("div")
    assert file_div is not None
    assert (
        tracker._torrent_entry_name(
            _meta(category="GAME"), "Description", [], file_div
        )
        == "Folder"
    )
    assert (
        tracker._torrent_entry_name(_meta(), "Description", [], file_div)
        == "Description"
    )


def test_brasiltracker_base_dupe_entry_without_size() -> None:
    tracker = _tracker()
    row = BeautifulSoup("<tr><td>only</td></tr>", "html.parser").find("tr")
    assert row is not None
    assert tracker._base_dupe_entry(row, "1", "Name", ["file"])["size"] == ""


def test_brasiltracker_book_dupe_audio() -> None:
    assert (
        _tracker()._book_dupe_type("Release", ["chapter.mp3"]) == "audiobook"
    )


@pytest.mark.asyncio
async def test_brasiltracker_media_info_missing_and_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    assert await tracker.get_media_info(_meta(tmp_path)) == ""
    path = tmp_path / "tmp" / "release" / "MEDIAINFO_CLEANPATH.txt"
    path.parent.mkdir(parents=True)
    path.write_text("info", encoding="utf-8")
    monkeypatch.setattr(
        bt_module.aiofiles,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("broken")),
    )
    assert await tracker.get_media_info(_meta(tmp_path)) == ""


@pytest.mark.asyncio
async def test_brasiltracker_edition_empty_and_unknown() -> None:
    tracker = _tracker()
    assert await tracker.get_edition(_meta(edition="")) == ""
    assert await tracker.get_edition(_meta(edition="Fan Cut")) == ""


@pytest.mark.asyncio
async def test_brasiltracker_bitrate_disc_sizes_and_invalid_bdinfo() -> None:
    tracker = _tracker()
    assert (
        await tracker.get_bitrate(
            _meta(type="DISC", is_disc="BDMV", disctype="BD100")
        )
        == "BD100"
    )
    assert (
        await tracker.get_bitrate(
            _meta(type="DISC", is_disc="BDMV", bdinfo={"size": 60})
        )
        == "BD66"
    )
    assert (
        await tracker.get_bitrate(
            _meta(type="DISC", is_disc="BDMV", bdinfo={"size": 40})
        )
        == "BD50"
    )
    assert (
        await tracker.get_bitrate(
            _meta(type="DISC", is_disc="BDMV", bdinfo={"size": 70})
        )
        == "BD100"
    )
    assert tracker._bdinfo_size({"size": "bad"}) == 0


@pytest.mark.asyncio
async def test_brasiltracker_credits_unique() -> None:
    item = _meta(imdb_info={"directors": ["A", "A"]}, tmdb_directors=["B"])
    assert await _tracker().get_credits(item) == "A, B"


def test_brasiltracker_magazine_month_and_internal() -> None:
    tracker = _tracker(internal=True, internal_groups=["GROUP"])
    data: dict[str, Any] = {}
    tracker._apply_magazine_fields(
        data,
        _meta(
            category="BOOK",
            magazine=True,
            title="Magazine January",
            basename_no_ext="Magazine",
        ),
    )
    assert data["mes_resvista"] == "Janeiro"
    tracker._apply_internal_flag(data, _meta(tag="-GROUP"))
    assert data["internal"] == 1


def test_brasiltracker_tracker_config_guard() -> None:
    tracker = BrasilTracker(
        {
            "TRACKERS": "bad",
            "DEFAULT": {"tmdb_api": "0123456789abcdef0123456789abcdef"},
        }
    )
    assert tracker._tracker_config() == {}


def test_brasiltracker_audiobook_bitrate_close_and_far() -> None:
    tracker = _tracker()
    assert (
        tracker.get_audiobook_bitrate(
            _meta(container="mp3", audiobook_bitrate=130)
        )
        == "128"
    )
    assert (
        tracker.get_audiobook_bitrate(
            _meta(container="mp3", audiobook_bitrate=500)
        )
        == "Outro"
    )


@pytest.mark.asyncio
async def test_brasiltracker_book_cover_hosted_and_japanese_audiobook() -> (
    None
):
    tracker = _tracker()
    item = _meta(hosted_artwork=[{"raw_url": "https://img/cover.jpg"}])
    assert await tracker.get_book_cover(item) == "https://img/cover.jpg"
    assert (
        await tracker.get_book_language(
            _meta(audiobook=True, book_language_iso="ja")
        )
        == "Outro"
    )


def test_brasiltracker_book_pages_guards_pdf_cbz_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    assert tracker.get_book_pages(_meta(audiobook=True)) == ""
    assert (
        tracker.get_book_pages(
            _meta(tmp_path, filelist=[str(tmp_path / "missing.pdf")])
        )
        == ""
    )

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr(bt_module.fitz, "open", lambda _path: [1, 2, 3])
    assert tracker.get_book_pages(_meta(tmp_path, filelist=[str(pdf)])) == "3"

    cbz = tmp_path / "comic.cbz"
    with zipfile.ZipFile(cbz, "w") as archive:
        archive.writestr("1.jpg", b"x")
        archive.writestr("notes.txt", b"x")
    assert tracker.get_book_pages(_meta(tmp_path, filelist=[str(cbz)])) == "1"

    monkeypatch.setattr(
        BrasilTracker,
        "_pdf_pages",
        staticmethod(lambda _path: (_ for _ in ()).throw(RuntimeError("bad"))),
    )
    assert tracker._book_page_count(pdf) == ""


@pytest.mark.asyncio
async def test_brasiltracker_upload_skip_cookie_success_and_post_data_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert not await tracker.upload(_meta(skipping="BRASILTRACKER"))

    monkeypatch.setattr(
        tracker.cookie_validator,
        "load_session_cookies",
        AsyncMock(return_value=None),
    )
    assert not await tracker.upload(_meta())

    cookies = httpx.Cookies({"sid": "x"})
    monkeypatch.setattr(
        tracker.cookie_validator,
        "load_session_cookies",
        AsyncMock(return_value=cookies),
    )

    async def data_and_skip(meta: Meta) -> dict[str, Any]:
        meta.skipping = "BRASILTRACKER"
        return {}

    monkeypatch.setattr(tracker, "get_data", data_and_skip)
    assert not await tracker.upload(_meta())

    monkeypatch.setattr(
        tracker, "get_data", AsyncMock(return_value={"name": "Release"})
    )
    monkeypatch.setattr(
        tracker.cookie_auth_uploader,
        "handle_upload",
        AsyncMock(return_value=True),
    )
    item = _meta()
    assert await tracker.upload(item)
    tracker.cookie_auth_uploader.handle_upload.assert_awaited_once()


def test_brasiltracker_torrent_row_missing_file_div() -> None:
    tracker = _tracker()
    page = BeautifulSoup("<html></html>", "html.parser")
    row = BeautifulSoup(
        "<tr id='torrent9'><td><a onclick='gtoggle(9)'>Release</a></td><td>1 GB</td></tr>",
        "html.parser",
    ).find("tr")
    assert row is not None
    assert tracker._torrent_row_entry(_meta(), page, row) is None


def test_brasiltracker_book_file_path_empty() -> None:
    assert BrasilTracker._book_file_path(_meta(filelist=[], path="")) is None


def test_brasiltracker_cbr_page_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "comic.cbr"
    path.write_bytes(b"rar")

    class Archive:
        def __enter__(self) -> Archive:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @staticmethod
        def namelist() -> list[str]:
            return ["1.jpg", "2.webp", "notes.txt"]

    monkeypatch.setattr(bt_module.rarfile, "RarFile", lambda _path: Archive())
    assert _tracker()._book_page_count(path) == "2"
