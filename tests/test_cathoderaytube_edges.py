from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.integrations.trackers.cathoderaytube import CathodeRayTube
from tests.test_cathoderaytube import meta, tracker


def test_crt_validate_credentials_success_and_failure() -> None:
    site = tracker()
    site.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert not asyncio.run(site.validate_credentials(meta()))

    cookies = httpx.Cookies({"session": "ok"})
    site.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    assert asyncio.run(site.validate_credentials(meta()))
    assert site.session.cookies.get("session") == "ok"


def test_crt_extract_auth_token_from_form() -> None:
    html = '<form><input type="hidden" name="auth" value="csrf-form"></form>'
    assert CathodeRayTube._extract_auth_token(html) == "csrf-form"


def test_crt_season_label_numeric_and_custom() -> None:
    assert CathodeRayTube._season_label("2") == "Season 2"
    assert CathodeRayTube._season_label("Season X") == "Season X"


def test_crt_cover_source_language_and_tag_helper_branches() -> None:
    item = meta(artwork_url="https://example.com/original.jpg")
    assert CathodeRayTube._cover_source_url(item) == "https://example.com/original.jpg"
    assert CathodeRayTube._language_values("English") == ["English"]

    tags: list[str] = []
    CathodeRayTube._append_year_tags(tags, "bad")
    assert tags == []

    tags = []
    CathodeRayTube._append_resolution_tags(tags, meta(resolution="OTHER", sd=True))
    assert tags == ["sd"]

    tags = []
    CathodeRayTube._append_feature_tags(tags, meta(is_disc="BDMV", three_d="3D", extras=True, has_commentary=True))
    assert tags == ["full.disc", "3d", "extras", "commentary"]

    assert CathodeRayTube._audio_codec_tag("unknown") == ""
    assert CathodeRayTube._valid_images("bad") == []


def test_crt_platform_and_audio_tag_remaining_branches() -> None:
    site = tracker()
    assert site.get_tags(meta(category="GAME", platform="DOS"))[-3:] == "dos"
    assert "nintendo" in site.get_tags(meta(category="GAME", platform="Nintendo Switch"))
    assert "atari" in site.get_tags(meta(category="GAME", platform="Atari 2600"))
    assert "ddp" in site.get_tags(meta(audio="DD+ 5.1"))
    assert "dd" in site.get_tags(meta(audio="Dolby Digital 2.0", channels="2.0"))
    assert "flac" in site.get_tags(meta(audio="FLAC 2.0", channels="2.0"))


@pytest.mark.asyncio
async def test_crt_host_cover_short_circuits_and_missing_cases(tmp_path: Path) -> None:
    site = tracker()
    approved = meta(hosted_artwork=[{"raw_url": "https://iili.io/cover.png"}])
    assert await site._host_cover(approved) == "https://iili.io/cover.png"

    skipped = meta(skip_imghost_upload=True)
    assert await site._host_cover(skipped) == ""

    missing = meta(base_dir=str(tmp_path), artwork_url="", tmdb_poster_path="")
    assert await site._ensure_local_cover(missing) is None

    cover = tmp_path / "cover.png"
    cover.write_bytes(b"cover")
    no_host = meta(base_dir=str(tmp_path), artwork_path=str(cover))
    assert await site._host_cover(no_host) == ""


@pytest.mark.asyncio
async def test_crt_cover_upload_invalid_result_and_no_success(tmp_path: Path) -> None:
    site = tracker()
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"cover")
    site.rehost_images_manager.uploadscreens_manager.upload_screens = AsyncMock(return_value=([{"raw_url": "https://evil.invalid/x.png"}], 1))
    item = meta(artwork_path=str(cover), imghost="original")
    assert await site._upload_cover_to_host(item, cover, 1) == ""
    assert await site._try_cover_hosts(item, cover, [1]) == ""


@pytest.mark.asyncio
async def test_crt_generate_description_debug_writes_file(tmp_path: Path) -> None:
    site = tracker()
    item = meta(base_dir=str(tmp_path), uuid="debug", debug=True)
    result = await site.generate_description(item)
    path = tmp_path / "tmp" / "debug" / "[CATHODERAYTUBE]DESCRIPTION.txt"
    assert path.read_text(encoding="utf-8") == result


def test_crt_upload_data_and_search_category_errors() -> None:
    site = tracker()
    with pytest.raises(ValueError, match="Unsupported Cathode-Ray category"):
        asyncio.run(site.get_upload_data(meta(category="BOOK"), "csrf"))
    with pytest.raises(ValueError, match="Unsupported Cathode-Ray category"):
        site.get_search_params(meta(category="BOOK"))


def test_crt_content_archive_screenshot_and_age_edge_branches() -> None:
    site = tracker()
    assert not asyncio.run(site.get_additional_checks(meta(adult_media=True)))
    assert not asyncio.run(site.get_additional_checks(meta(filelist=["release.rar"])))
    assert site._safe_screen_count("bad") == 0

    # Above six but not a multiple of three remains valid while exercising the guideline branch.
    assert asyncio.run(site.get_additional_checks(meta(screens=7)))

    # Malformed date falls back to the release year.
    assert asyncio.run(site.get_additional_checks(meta(release_date="not-a-date", year="2000")))
    assert site._year_is_old_enough("not-a-year")

    cutoff = site._ten_year_cutoff()
    assert site._date_is_old_enough(cutoff, cutoff)


def test_crt_content_name_and_bdinfo_guard_branches() -> None:
    assert CathodeRayTube._content_name("<html></html>") == ""
    file_only = """
    <div id='files_1'><table>
      <tr><td>File Name</td><td>Size</td></tr>
      <tr><td>single.mkv</td><td>1 GB</td></tr>
    </table></div>
    """
    assert CathodeRayTube._content_name(file_only) == "single.mkv"
    empty_table = "<div id='files_1'><table><tr><td>only one</td></tr></table></div>"
    assert CathodeRayTube._content_name(empty_table) == ""
    assert CathodeRayTube._bd_info("<div class='section-details'>No disc details</div>") == ""


def test_crt_link_extraction_guard_branches() -> None:
    request = httpx.Request("POST", "https://www.cathode-ray.tube/upload.php")
    response = httpx.Response(200, request=request, text='<a href="/torrents.php?id=44">Torrent</a>')
    assert CathodeRayTube._uploaded_torrent_url(response).endswith("id=44")

    no_link = httpx.Response(200, request=request, text="no link")
    assert CathodeRayTube._uploaded_torrent_url(no_link) == ""

    soup = BeautifulSoup("<tr><td>unrelated row</td></tr>", "html.parser")
    row = soup.find("tr")
    assert row is not None
    assert CathodeRayTube._log_row_url(row, "Release") == ""

    bad_link = BeautifulSoup('<a href="/details.php?id=abc">bad</a>', "html.parser").find("a")
    assert bad_link is not None
    assert CathodeRayTube._log_link_url(bad_link) == ""


def test_crt_row_link_and_detail_guard_branches() -> None:
    site = tracker()
    response = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/torrents.php"), text="")
    row = BeautifulSoup("<tr><td>missing links</td></tr>", "html.parser").find("tr")
    assert row is not None
    assert site._row_links(response, row) is None

    bad_title = BeautifulSoup('<tr><td><a href="">Title</a></td><td><a href="?action=download">DL</a></td></tr>', "html.parser").find("tr")
    assert bad_title is not None
    assert site._row_links(response, bad_title) is None

    seen = {"https://www.cathode-ray.tube/torrents.php?id=1"}
    assert site._seen_link("https://www.cathode-ray.tube/torrents.php?id=1", seen)

    detail = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/torrents.php?id=1"), text="<html></html>")
    assert site._detail_entry(row, "link", "download", detail) is None

    entry = {"name": "Release"}
    site._apply_bd_info(entry, meta(is_disc=""), "Disc Title: X\nDisc Size: 1")
    assert "bd_info" not in entry


@pytest.mark.asyncio
async def test_crt_search_existing_missing_cookie() -> None:
    site = tracker()
    site.cookie_validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await site.search_existing(meta()) == []


@pytest.mark.asyncio
async def test_crt_search_response_and_auth_helpers() -> None:
    site = tracker()
    login = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/login.php"), text="login")
    site.session.get = AsyncMock(return_value=login)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="cookies may be expired"):
        await site._search_response({})

    normal = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/torrents.php"), text="no auth")
    with pytest.raises(RuntimeError, match="did not contain an auth token"):
        site._update_auth_token(normal)


@pytest.mark.asyncio
async def test_crt_find_log_upload_success_and_failure() -> None:
    site = tracker()
    html = '<tr><td>Release was uploaded</td><td><a href="/details.php?id=77">77</a></td></tr>'
    response = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/log.php"), text=html)
    site.session.get = AsyncMock(return_value=response)  # type: ignore[method-assign]
    site.get_name = AsyncMock(return_value="Release")  # type: ignore[method-assign]
    assert await site._find_log_upload(meta()) == "https://www.cathode-ray.tube/torrents.php?id=77"

    site.session.get = AsyncMock(side_effect=httpx.RequestError("offline", request=httpx.Request("GET", site.base_url)))  # type: ignore[method-assign]
    assert await site._find_log_upload(meta()) == ""


@pytest.mark.asyncio
async def test_crt_upload_missing_auth_and_submit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    site = tracker()
    monkeypatch.setattr(site, "_load_upload_cookies", AsyncMock())
    monkeypatch.setattr(site, "_host_cover", AsyncMock(return_value=""))
    original = CathodeRayTube.auth_token
    try:
        CathodeRayTube.auth_token = ""
        item = meta(tracker_status={"CATHODERAYTUBE": {}})
        assert not await site.upload(item)
        assert "Failed to load authenticated upload form" in item.tracker_status["CATHODERAYTUBE"]["status_message"]

        CathodeRayTube.auth_token = "csrf"
        monkeypatch.setattr(site, "_submit_upload", AsyncMock(return_value=False))
        assert not await site.upload(meta(tracker_status={"CATHODERAYTUBE": {}}))
    finally:
        CathodeRayTube.auth_token = original


@pytest.mark.asyncio
async def test_crt_upload_cookie_submit_and_finalize_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    site = tracker()
    cookies = httpx.Cookies({"session": "ok"})
    site.cookie_validator.load_session_cookies = AsyncMock(return_value=cookies)  # type: ignore[method-assign]
    await site._load_upload_cookies(meta())
    assert site.session.cookies.get("session") == "ok"

    site.cookie_auth_uploader.handle_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(site, "get_upload_data", AsyncMock(return_value={"auth": "csrf"}))
    assert await site._submit_upload(meta(), "csrf")

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(site, "_find_log_upload", AsyncMock(return_value=""))
    assert await site._finalize_upload(meta(tracker_status={"CATHODERAYTUBE": {}}))

    monkeypatch.setattr(site, "_find_log_upload", AsyncMock(return_value="https://www.cathode-ray.tube/torrents.php?foo=bar"))
    assert await site._finalize_upload(meta(tracker_status={"CATHODERAYTUBE": {}}))

    monkeypatch.setattr(site, "_find_log_upload", AsyncMock(return_value="https://www.cathode-ray.tube/torrents.php?id=88"))
    monkeypatch.setattr(site, "_record_uploaded_torrent", AsyncMock())
    item = meta(tracker_status={"CATHODERAYTUBE": {}})
    assert await site._finalize_upload(item)
    site._record_uploaded_torrent.assert_awaited_once_with(item, "88", "https://www.cathode-ray.tube/torrents.php?id=88")


@pytest.mark.asyncio
async def test_crt_record_uploaded_torrent() -> None:
    site = tracker()
    site.config["TRACKERS"]["CATHODERAYTUBE"]["announce_url"] = "https://signal.cathode-ray.tube/passkey/announce"
    site.common.create_torrent_ready_to_seed = AsyncMock()  # type: ignore[method-assign]
    item = meta(tracker_status={"CATHODERAYTUBE": {}})
    await site._record_uploaded_torrent(item, "99", "https://www.cathode-ray.tube/torrents.php?id=99")
    assert item.tracker_status["CATHODERAYTUBE"]["torrent_id"] == "99"
    site.common.create_torrent_ready_to_seed.assert_awaited_once()


@pytest.mark.asyncio
async def test_crt_host_cover_returns_empty_when_local_cover_cannot_be_prepared(monkeypatch: pytest.MonkeyPatch) -> None:
    site = tracker()
    monkeypatch.setattr(site, "_ensure_local_cover", AsyncMock(return_value=None))
    assert await site._host_cover(meta()) == ""


@pytest.mark.asyncio
async def test_crt_search_row_entry_returns_none_when_detail_has_no_name(monkeypatch: pytest.MonkeyPatch) -> None:
    site = tracker()
    response = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/torrents.php"), text="")
    row = BeautifulSoup(
        '<tr><td><a href="/torrents.php?id=1">Title</a></td><td><a href="?action=download&id=1">DL</a></td></tr>',
        "html.parser",
    ).find("tr")
    assert row is not None
    detail = httpx.Response(200, request=httpx.Request("GET", "https://www.cathode-ray.tube/torrents.php?id=1"), text="<html></html>")
    monkeypatch.setattr(site, "_detail_response", AsyncMock(return_value=detail))
    assert await site._search_row_entry(meta(), response, row, set()) is None


def test_crt_log_link_rejects_unrelated_href() -> None:
    link = BeautifulSoup('<a href="/foo?id=1">foo</a>', "html.parser").find("a")
    assert link is not None
    assert CathodeRayTube._log_link_url(link) == ""


@pytest.mark.asyncio
async def test_crt_upload_success_delegates_to_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    site = tracker()
    original = CathodeRayTube.auth_token
    try:
        CathodeRayTube.auth_token = "csrf"
        monkeypatch.setattr(site, "_load_upload_cookies", AsyncMock())
        monkeypatch.setattr(site, "_host_cover", AsyncMock(return_value=""))
        monkeypatch.setattr(site, "_submit_upload", AsyncMock(return_value=True))
        monkeypatch.setattr(site, "_finalize_upload", AsyncMock(return_value=True))
        item = meta(tracker_status={"CATHODERAYTUBE": {}})
        assert await site.upload(item)
        site._finalize_upload.assert_awaited_once_with(item)
    finally:
        CathodeRayTube.auth_token = original
