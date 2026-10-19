import asyncio
import json
import platform
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import src.integrations.trackers.makingoff as makingoff
from src.domain_models.release import Meta
from src.integrations.trackers.makingoff import MakingOff

CONFIG = {
    "DEFAULT": {},
    "TRACKERS": {"MAKINGOFF": {"trackers": []}},
}


def _meta(**values: object) -> Meta:
    defaults: dict[str, object] = {
        "base_dir": ".",
        "uuid": "release",
        "category": "MOVIE",
        "title": "Example",
        "original_title": "Original",
        "year": 2024,
        "runtime": 120,
        "resolution": "1080p",
        "video_width": 1920,
        "video_height": 1080,
        "video_codec": "AVC",
        "video_encode": "H.264",
        "video_bitrate": 8000,
        "audio": "AAC",
        "audio_bitrate": 640,
        "frame_rate": 23.976,
        "container": "MKV",
        "is_disc": "",
        "filelist": [],
        "subtitle_files": [],
        "subtitle_languages": [],
        "hardcoded_subs": False,
        "language_checked": True,
        "mediainfo": {"media": {"track": []}},
        "menu_images": [],
        "image_list": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "tmdb_localized_data": {},
        "tmdb_poster_path": "",
        "overview": "Overview",
        "genres": ["Drama"],
        "combined_genres": "Drama",
        "production_countries": [],
        "origin_country": ["US"],
        "audio_languages": ["English"],
        "original_language": "en",
        "imdb_info": {},
        "imdb_id": "",
        "imdb_tt": "",
        "basename_no_ext": "Example.Release",
        "name": "Example Release",
        "adult_media": False,
        "tmdb_adult_media": False,
        "debug": True,
        "unattended": False,
        "unattended_confirm": False,
        "skipping": None,
        "tracker_status": {"MAKINGOFF": {}},
        "awards": "",
        "premiacoes": "",
        "trivia": "",
        "curiosidades": "",
        "critic": "",
        "critica": "",
    }
    defaults.update(values)
    return Meta(**defaults)


def _tracker() -> MakingOff:
    tracker = object.__new__(MakingOff)
    tracker.config = {
        "DEFAULT": {},
        "TRACKERS": {"MAKINGOFF": {"trackers": []}},
    }
    tracker.common = SimpleNamespace(
        create_torrent_for_upload=AsyncMock(return_value=None)
    )
    tracker.cookie_validator = SimpleNamespace(
        load_session_cookies=AsyncMock(return_value=None),
        save_session_cookies=AsyncMock(return_value=None),
    )
    tracker._display_title_cache = {}
    tracker._csrf_token = ""
    tracker._public_trackers = []
    tracker.session = SimpleNamespace(
        get=AsyncMock(),
        post=AsyncMock(),
        cookies=SimpleNamespace(jar={}),
    )
    return tracker


def _response(
    *,
    text: str = "",
    status: int = 200,
    json_data: object | None = None,
    url: str = "https://www.makingoff.org/",
) -> httpx.Response:
    request = httpx.Request("GET", url)
    if json_data is not None:
        return httpx.Response(status, json=json_data, request=request)
    return httpx.Response(status, text=text, request=request)


def test_constructor_and_media_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(makingoff, "Common", lambda _config: object())
    monkeypatch.setattr(makingoff, "CookieValidator", lambda _config: object())
    fake_client = SimpleNamespace()
    monkeypatch.setattr(
        makingoff.httpx, "AsyncClient", lambda **_kwargs: fake_client
    )
    tracker = MakingOff(
        {
            "TRACKERS": {
                "MAKINGOFF": {
                    "trackers": "udp://a\n\nudp://b",
                }
            }
        }
    )
    assert tracker._public_trackers == ["udp://a", "udp://b"]
    assert tracker.session is fake_client
    tracker = _tracker()
    assert tracker._normalize_codec("h264", tracker.VIDEO_CODEC_MAP) == "H.264"
    assert (
        tracker._normalize_codec("Custom", tracker.VIDEO_CODEC_MAP) == "Custom"
    )
    assert (
        tracker._mediainfo_video_codec(_meta(), {"Format": "AVC"}) == "H.264"
    )
    assert (
        tracker._mediainfo_video_codec(_meta(video_encode="xvid"), {})
        == "XviD"
    )
    assert tracker._mediainfo_audio_codec(_meta(), {"Format": "AAC"}) == "AAC"
    assert tracker._mediainfo_audio_codec(_meta(audio="FLAC"), {}) == "FLAC"
    assert tracker._container_alias("matroska") == "MKV"
    assert tracker._container_alias("mpeg-4") == "MP4"
    assert tracker._container_alias("unknown") is None
    assert tracker._mediainfo_container({"Format": "AVI"}) == "AVI"
    assert tracker._mediainfo_container({}, "MKV") == "MKV"
    assert tracker._mediainfo_filesize(
        _meta(),
    )
    assert tracker._mediainfo_duration({}, {"Duration": "7500"}) == "125"
    assert tracker._aspect_ratio(1920, 1080) == "Widescreen (16x9)"
    assert tracker._aspect_ratio(0, 0) == "Widescreen (16x9)"
    assert tracker._html_encode("<tag>") == "<tag>"
    assert "a" in tracker._screen_pair("a", "b")
    rows = tracker._screen_rows(["1", "2", "3", "4", "5"])
    assert "[closeTab]" in rows


def test_ffmpeg_resolution_and_language_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    assert tracker._ffmpeg_arch("x86_64") == "amd"
    assert tracker._ffmpeg_arch("arm64") == "arm"
    assert tracker._ffmpeg_arch("riscv") is None
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    linux = tracker._bundled_ffmpeg_candidate(str(tmp_path))
    assert linux is not None and linux.name == "ffmpeg"
    linux.parent.mkdir(parents=True)
    linux.write_text("x")
    assert tracker._get_ffmpeg_path(_meta(base_dir=str(tmp_path))) == str(
        linux
    )
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert (
        tracker._bundled_ffmpeg_candidate(str(tmp_path)).name == "ffmpeg.exe"
    )
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert tracker._bundled_ffmpeg_candidate(str(tmp_path)) is None
    assert tracker._dimension_value("1080") == 1080
    assert tracker._dimension_value(object()) == 0
    assert tracker._dimensions_are_hidef(1920, 1080)
    assert tracker._is_hidef(
        _meta(video_width=0, video_height=0, resolution="720p")
    )
    assert not tracker._is_hidef(
        _meta(video_width=640, video_height=480, resolution="480p")
    )
    assert tracker._has_portuguese_language(["pt-BR"])
    assert tracker._has_portuguese_language("Portuguese")
    assert not tracker._has_portuguese_language("English")
    assert tracker._subtitle_name_is_portuguese("movie.pt-br.srt")
    assert not tracker._subtitle_name_is_portuguese("movie.en.srt")


def test_subtitle_content_sidecar_and_embedded_detection(
    tmp_path: Path,
) -> None:
    tracker = _tracker()
    pt = tmp_path / "movie.srt"
    pt.write_text("que não uma com mais para você", encoding="utf-8")
    en = tmp_path / "movie.en.srt"
    en.write_text("the and you that was for", encoding="utf-8")
    assert tracker._read_subtitle_sample(str(pt))
    assert tracker._is_subtitle_in_portuguese(str(pt))
    assert not tracker._is_subtitle_in_portuguese(str(en))
    assert tracker._sidecar_is_portuguese(pt)
    assert not tracker._sidecar_is_portuguese(tmp_path / "missing.srt")
    assert tracker._track_is_portuguese(
        {"@type": "Text", "Language": "pt", "Title": ""}
    )
    assert tracker._track_is_portuguese(
        {"@type": "Text", "Language": "", "Title": "Portuguese"}
    )
    assert not tracker._track_is_portuguese({"@type": "Video"})
    meta = _meta(
        subtitle_files=[str(pt)],
        mediainfo={"media": {"track": []}},
    )
    assert tracker._has_portuguese_subtitle(meta)
    meta = _meta(
        subtitle_files=[],
        mediainfo={
            "media": {
                "track": [{"@type": "Text", "Language": "pt", "Title": ""}]
            }
        },
    )
    assert tracker._embedded_portuguese_subtitle(meta)
    assert tracker._has_portuguese_subtitle(meta)
    assert tracker._has_portuguese_subtitle(_meta(hardcoded_subs=True))


def test_subtitle_extraction_helpers(tmp_path: Path) -> None:
    tracker = _tracker()
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"video")
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="u",
        filelist=[str(video)],
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Text",
                        "Language": "pt",
                        "Format": "ASS",
                        "Title": "PT BR",
                    },
                    {"@type": "Text", "Language": "en", "Format": "VTT"},
                ]
            }
        },
    )
    assert tracker._embedded_subtitle_video(meta) == str(video)
    assert len(tracker._embedded_text_tracks(meta)) == 2
    assert tracker._embedded_track_is_portuguese(
        meta.mediainfo["media"]["track"][0]
    )
    assert tracker._subtitle_extension({"Format": "SSA"}) == ".ass"
    assert tracker._subtitle_extension({"Format": "VTT"}) == ".vtt"
    assert tracker._subtitle_extension({"Format": "PGS"}) == ".sup"
    assert tracker._subtitle_extension({"Format": "SRT"}) == ".srt"
    assert tracker._subtitle_title_slug({"Title": "PT BR"}) == "-PT_BR"
    name, output = tracker._subtitle_output_path(
        meta, {"Format": "SRT", "Title": "PT"}, 0
    )
    assert name.endswith(".srt") and output.endswith(name)
    command = tracker._ffmpeg_command("ffmpeg", str(video), 0, output)
    assert command[-1] == output
    path = Path(output)
    path.write_text("subtitle")
    assert tracker._extracted_subtitle_valid(
        SimpleNamespace(returncode=0), output
    )
    assert not tracker._extracted_subtitle_valid(
        SimpleNamespace(returncode=1), output
    )


def test_extract_embedded_subtitle_debug_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"video")
    track = {"@type": "Text", "Language": "pt", "Format": "SRT", "Title": "PT"}
    meta = _meta(
        base_dir=str(tmp_path), uuid="u", debug=True, filelist=[str(video)]
    )
    assert (
        asyncio.run(
            tracker._extract_embedded_subtitle(meta, str(video), track, 0)
        )
        is None
    )
    meta.debug = False
    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"", b""))
    )

    async def create_process(*args: str, **_kwargs: object) -> object:
        Path(args[-1]).write_text("subtitle")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    output = asyncio.run(
        tracker._extract_embedded_subtitle(meta, str(video), track, 0)
    )
    assert output and Path(output).exists()
    bad = SimpleNamespace(
        returncode=1, communicate=AsyncMock(return_value=(b"", b"err"))
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=bad)
    )
    assert (
        asyncio.run(
            tracker._extract_embedded_subtitle(meta, str(video), track, 1)
        )
        is None
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(side_effect=OSError("ffmpeg missing")),
    )
    assert (
        asyncio.run(
            tracker._extract_embedded_subtitle(meta, str(video), track, 2)
        )
        is None
    )


def test_bbcode_localization_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    assert (
        tracker._release_label("")
        == "[release]Release não informado[/release][/tr]"
    )
    assert tracker._optional_info_line("X", "") == ""
    assert "Kbps" in tracker._bitrate_line("Vídeo Bitrate", "5000")
    assert tracker._bitrate_line("X", "None") == ""
    assert tracker._valid_resolution_text("1920x1080")
    assert not tracker._valid_resolution_text("1920x0")
    assert "Resolução" in tracker._resolution_line("1920x1080")
    assert tracker._extra_info_rows("Award", "Trivia", "Critic")
    bbcode = tracker._build_bbcode(
        title_br="Brasil",
        title_orig="Original",
        release="Release",
        poster_url="poster",
        overview="overview",
        image_urls=["1", "2", "3", "4"],
        cast_text="Cast",
        genres="Drama",
        directors="Director",
        duration="120",
        year="2024",
        countries="Brasil",
        audio="Português",
        subs="Embutidas",
        imdb_url="https://imdb",
        homepage_url="https://site",
        quality="WEB-DL",
        container="MKV",
        video_codec="H.264",
        video_brate="5000",
        audio_codec="AAC",
        audio_brate="640",
        res_str="1920x1080",
        aspect="16:9",
        fps_str="23.976 FPS",
        filesize="10 GiB",
        awards="Award",
        trivia="Trivia",
        critic="Critic",
    )
    assert "Brasil" in bbcode and "Premiações" in bbcode
    assert tracker._get_lang_name("pt")
    monkeypatch.setattr(
        makingoff.gettext, "translation", Mock(side_effect=OSError)
    )
    assert tracker._country_translations() == (None, None)
    assert tracker._country_codes(
        _meta(production_countries=[{"iso_3166_1": "BR"}])
    ) == ["BR"]
    assert tracker._country_codes(
        _meta(production_countries=[], origin_country=["US"])
    ) == ["US"]
    assert (
        tracker._localized_country_name("XC", None, None) == "Checoslováquia"
    )
    assert tracker._localized_country_name("US", None, None)
    assert tracker._localized_country_name("ZZ", None, None) == "ZZ"
    assert tracker._genre_list(["Drama", ""]) == ["Drama"]
    assert tracker._genre_list("Drama, Action") == ["Drama", "Action"]
    assert tracker._genre_list(1) == []
    assert (
        tracker._localizer_genres(_meta(genres=[], combined_genres=""))
        == "Desconhecido"
    )
    assert tracker._localizer_genres(_meta(genres=["Action"])) == "Ação"


def test_audio_quality_and_csrf_helpers() -> None:
    tracker = _tracker()
    assert tracker._localizer_audio_language(_meta(audio_languages=["pt"]))
    assert tracker._localizer_audio_language(_meta(audio_languages=[]))
    assert tracker._localizer_video_quality(_meta(type="WEBDL"))
    assert tracker._localizer_video_quality(_meta(type="REMUX"))
    assert tracker._tag_attribute(object(), "value") == ""
    assert tracker._get_csrf_token('<html data-csrf="abc"></html>') == "abc"
    assert (
        tracker._get_csrf_token('<input name="_xfToken" value="def">') == "def"
    )
    assert tracker._get_csrf_token('csrf: "ghi"') == "ghi"
    assert tracker._get_csrf_token("none") == ""
    assert tracker._page_logged_in('<html data-logged-in="true"></html>')
    assert not tracker._page_logged_in("<html></html>")


def test_session_and_post_token_flows() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(
        return_value=_response(
            text='<html data-logged-in="true" data-csrf="abc"></html>'
        )
    )
    assert asyncio.run(tracker.refresh_session())
    assert tracker._csrf_token == "abc"
    tracker.session.get = AsyncMock(
        return_value=_response(text="<html></html>")
    )
    assert not asyncio.run(tracker.refresh_session())
    tracker.session.get = AsyncMock(
        return_value=_response(
            text='<html data-logged-in="true" data-csrf="csrf"><input name="attachment_hash" value="hash"><input name="attachment_hash_combined" value="combined"></html>'
        )
    )
    assert asyncio.run(tracker.get_new_post_tokens(26)) == (
        "csrf",
        "hash",
        "combined",
    )
    tracker.session.get = AsyncMock(
        return_value=_response(text="<html></html>")
    )
    assert asyncio.run(tracker.get_new_post_tokens(26)) == ("", "", "")


def test_post_resolution_and_attachment_helpers(tmp_path: Path) -> None:
    tracker = _tracker()
    assert tracker._extract_post_height("Resolução: 1920x1080") == 1080
    assert tracker._extract_post_height("Movie.720p.WEB") == 720
    tracker.session.get = AsyncMock(
        return_value=_response(
            text="<div class='bbWrapper'>Resolução: 1280x720</div>"
        )
    )
    assert asyncio.run(tracker.get_post_resolution("https://topic")) == 720
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker.get_post_resolution("https://topic")) == 0
    assert tracker._parse_attachment_combined("") is None
    assert tracker._parse_attachment_combined("bad") is None
    combined = json.dumps({"type": "post", "context": {"node_id": 26}})
    assert tracker._attachment_context(combined, 1) == (
        "post",
        {"node_id": 26},
    )
    assert tracker._attachment_context("", 26) == ("post", {"node_id": 26})
    payload = tracker._attachment_payload("c", "h", "post", {"node_id": 26})
    assert payload["context[node_id]"] == "26"
    assert (
        tracker._attachment_mime_type("x.torrent", "x.torrent")
        == "application/x-bittorrent"
    )
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"data")
    assert asyncio.run(tracker._attachment_bytes(str(file_path))) == b"data"
    assert (
        tracker._attachment_error_message({"errorHtml": {"content": "bad"}})
        == "bad"
    )


def test_attachment_upload_success_error_and_unwanted(tmp_path: Path) -> None:
    tracker = _tracker()
    file_path = tmp_path / "file.torrent"
    file_path.write_bytes(b"data")
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"status": "ok"})
    )
    assert asyncio.run(
        tracker.upload_attachment(str(file_path), "c", "h", "", 26)
    )
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"errors": {"x": "bad"}})
    )
    assert not asyncio.run(
        tracker.upload_attachment(str(file_path), "c", "h", "", 26)
    )
    assert not asyncio.run(
        tracker.upload_attachment(str(tmp_path / "missing"), "c", "h", "", 26)
    )
    tracker.session.post = AsyncMock(return_value=_response(status=500))
    assert not asyncio.run(
        tracker.upload_attachment(str(file_path), "c", "h", "", 26)
    )


def test_search_helpers_and_candidate_flow() -> None:
    tracker = _tracker()
    payload = tracker._search_payload("Title", "csrf", 26, True)
    assert payload["c[title_only]"] == "1"
    assert payload["c[nodes][0]"] == "26"
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"redirect": "/search/1/"})
    )
    assert asyncio.run(tracker._search_redirect(payload)) == "/search/1/"
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"errors": {"x": "bad"}})
    )
    assert asyncio.run(tracker._search_redirect(payload)) is None
    html = """
    <div class='contentRow-title'><a href='/topicos/1/'>One</a></div>
    <a class='pageNav-jump--next' href='/search/2/'>Next</a>
    """
    results: dict[str, str] = {}
    assert tracker._parse_search_page(html, results) == "/search/2/"
    assert results["One"].endswith("/topicos/1/")
    tracker._csrf_token = "csrf"
    tracker._search_redirect = AsyncMock(return_value="/search/1/")  # type: ignore[method-assign]
    tracker._collect_search_pages = AsyncMock(return_value={"One": "u"})  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_candidate("One", 26)) == {"One": "u"}
    tracker._csrf_token = ""
    tracker.refresh_session = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_candidate("One", 26)) is None


def test_search_page_collection_and_index_parsing() -> None:
    tracker = _tracker()
    tracker.max_search_pages = 2
    tracker._search_page_html = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            "<div class='contentRow-title'><a href='/topicos/1/'>One</a></div><a class='pageNav-jump--next' href='/search/2/'></a>",
            "<div class='contentRow-title'><a href='/topicos/2/'>Two</a></div>",
        ]
    )
    results = asyncio.run(tracker._collect_search_pages("/search/1/"))
    assert set(results) == {"One", "Two"}
    assert tracker._search_page_allowed("x", set())
    assert not tracker._search_page_allowed("x", {"x"})
    html = """
    <div class='filme-card'>
      <h4 class='card-title'><a href='/topicos/1/'>Film</a></h4>
      <a href='?ano=2024'>2024</a>
      <a href='https://imdb.com/title/tt1234567'>IMDb</a>
    </div>
    """
    assert tracker._parse_index_results(html, "tt1234567") == {
        "Film (2024)": "https://www.makingoff.org/topicos/1/"
    }
    tracker.session.get = AsyncMock(return_value=_response(text=html))
    assert asyncio.run(tracker.search_index_by_imdb("tt1234567"))
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker.search_index_by_imdb("tt1234567")) is None


def test_topic_creation_and_credentials() -> None:
    tracker = _tracker()
    fields = tracker.get_topic_fields(26, "c", "h", "hc", "Title", "Body")
    assert fields["title"] == "Title"
    tracker.session.post = AsyncMock(
        return_value=_response(
            json_data={"status": "ok", "redirect": "/topicos/1/"}
        )
    )
    assert asyncio.run(
        tracker.create_topic(26, "c", "h", "hc", "Title", "Body")
    ).endswith("/topicos/1/")
    tracker.session.post = AsyncMock(
        return_value=_response(json_data={"errors": {"x": "bad"}})
    )
    assert (
        asyncio.run(tracker.create_topic(26, "c", "h", "hc", "Title", "Body"))
        == ""
    )
    meta = _meta()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    tracker.refresh_session = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.validate_credentials(meta))
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value=None
    )
    assert not asyncio.run(tracker.validate_credentials(meta))


def test_duplicate_and_forum_routing_helpers() -> None:
    tracker = _tracker()
    assert tracker._duplicate_candidates(
        _meta(origin_country=["BR"]), "Brasil"
    ) == ["Brasil"]
    assert tracker._duplicate_candidates(
        _meta(origin_country=["US"], title="T", original_title="O"), "P"
    ) == ["P", "T", "O"]
    results = {"T": "u1"}
    tracker._merge_search_result(results, "T", "u2")
    assert len(results) == 2
    assert tracker._unique_search_results({"A": "u", "B": "u"}) == [("A", "u")]
    assert tracker._resolution_height("1080p") == 1080
    assert tracker._resolution_height("bad") == 0
    assert tracker._year_is_compatible("Film (2024)", "2024", False)
    assert tracker._year_is_compatible("Film", "2024", True)
    assert tracker._duplicate_action(False, True, 1080, 480) == "hidef_exists"
    assert tracker._duplicate_action(True, False, 480, 1080) == "allow_upgrade"
    assert (
        tracker._duplicate_action(True, True, 1080, 720)
        == "equivalent_or_better"
    )
    assert tracker._duplicate_entry("T", "u", 1080, True)["size"] == "1080"
    assert tracker._is_documentary(_meta(genres=["Documentary"]))
    assert tracker._origin_countries(_meta(origin_country=["BR"])) == ["BR"]
    assert tracker._forum_for_country("BR") == 27
    assert tracker._forum_for_country("ZZ") is None
    assert tracker._selected_forum_id("7") == 27
    assert tracker._selected_forum_id("99") is None
    assert (
        asyncio.run(tracker.get_forum_id(_meta(genres=["Documentary"]))) == 28
    )
    assert asyncio.run(tracker.get_forum_id(_meta(runtime=20))) == 77
    assert (
        asyncio.run(tracker.get_forum_id(_meta(origin_country=["BR"]))) == 27
    )
    unattended = _meta(
        origin_country=["ZZ"], unattended=True, unattended_confirm=False
    )
    assert asyncio.run(tracker.get_forum_id(unattended)) == 26


def test_title_translation_and_name_helpers() -> None:
    tracker = _tracker()
    translations = {
        "translations": {
            "translations": [
                {
                    "iso_639_1": "pt",
                    "iso_3166_1": "BR",
                    "data": {"title": "Brasil", "overview": "PT"},
                },
                {
                    "iso_639_1": "en",
                    "iso_3166_1": "US",
                    "data": {"title": "English"},
                },
            ]
        }
    }
    assert (
        tracker._find_translation_title(translations, "pt", "BR") == "Brasil"
    )
    assert (
        tracker._find_translation_title(translations, "pt", "PT") == "Brasil"
    )
    assert tracker._translation_title(None) == ""
    meta = _meta(
        title="Original",
        original_title="Original",
        tmdb_localized_data={
            "pt-BR": {"main": translations},
            "en-US": {"main": translations},
        },
        uuid="u",
    )
    assert asyncio.run(tracker._resolve_display_title(meta)) == "Brasil"
    assert asyncio.run(tracker._resolve_display_title(meta)) == "Brasil"
    assert tracker._topic_title_part(meta, "Brasil") == "Brasil / Original"
    assert tracker._topic_year_suffix(meta) == " (2024)"
    assert asyncio.run(tracker.get_name(meta)).startswith(
        "[Hidef] Brasil / Original"
    )
    brazil = _meta(
        origin_country=["BR"], title="Brasil", original_title="Brasil"
    )
    assert tracker._topic_title_part(brazil, "Brasil") == "Brasil"


def test_image_subtitle_description_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert tracker._image_url("https://x") == "https://x"
    assert tracker._image_url({"raw_url": "https://raw"}) == "https://raw"
    assert tracker._image_url({}) == ""
    meta = _meta(
        menu_images=[{"raw_url": "m"}],
        image_list=[{"img_url": "i"}],
        spectrograms_images=[{"url": "s"}],
        dynamic_hdr_plot_images=[{"web_url": "d"}],
    )
    assert tracker._extract_image_urls(meta) == ["m", "i", "s", "d"]
    assert tracker._subtitle_language_is_portuguese("pt")
    tracker._has_external_portuguese_subtitle = Mock(return_value=True)  # type: ignore[method-assign]
    assert tracker._known_subtitle_type(_meta()) == "Anexas"
    tracker._has_external_portuguese_subtitle = Mock(return_value=False)  # type: ignore[method-assign]
    assert (
        tracker._known_subtitle_type(_meta(subtitle_languages=["pt"]))
        == "Embutidas"
    )
    assert (
        tracker._known_subtitle_type(
            _meta(unattended=True, unattended_confirm=False)
        )
        == "Sem Legenda"
    )
    monkeypatch.setattr(
        makingoff, "prompt_in_thread", AsyncMock(return_value="2")
    )
    assert asyncio.run(tracker._prompt_subtitle_type()) == "Anexas"


def test_description_building_helpers() -> None:
    tracker = _tracker()
    pt = {
        "poster_path": "/poster.jpg",
        "homepage": "https://br",
        "translations": {
            "translations": [
                {
                    "iso_639_1": "pt",
                    "iso_3166_1": "BR",
                    "data": {"overview": "Sinopse"},
                }
            ]
        },
    }
    en = {
        "homepage": "https://en",
        "credits": {
            "cast": [{"name": "Actor"}],
            "crew": [{"job": "Director", "name": "Director"}],
        },
    }
    meta = _meta(
        tmdb_localized_data={"pt-BR": {"main": pt}, "en-US": {"main": en}},
        imdb_id="123",
        imdb_info={"directors": ["IMDb Director"]},
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "FileSize/String": "10 GiB",
                        "Duration": "120000",
                    },
                    {"@type": "Video", "Format": "AVC"},
                    {"@type": "Audio", "Format": "AAC"},
                ]
            }
        },
        subtitle_languages=["pt"],
    )
    assert tracker._poster_url(meta, pt).endswith("/poster.jpg")
    assert tracker._translation_overview(pt, "BR") == "Sinopse"
    assert tracker._description_overview(meta, pt) == "Sinopse"
    assert tracker._description_cast(en) == "Actor"
    assert tracker._tmdb_directors(en) == ["Director"]
    assert tracker._description_directors(meta, en) == "Director"
    assert tracker._description_imdb_url(meta).endswith("tt0000123/")
    assert tracker._description_homepage(pt, en) == "https://br"
    general, video, audio = tracker._description_tracks(meta)
    assert general and video and audio
    assert tracker._description_fps(meta) == "23.976 FPS"
    assert tracker._description_dimensions(meta) == (1920, 1080)
    assert tracker._description_duration(meta, general, video) == "120"
    assert tracker._description_year(meta) == "2024"
    assert tracker._description_container_fallback(meta) == "MKV"
    description = asyncio.run(tracker.generate_description(meta))
    assert "Sinopse" in description and "Actor" in description


def test_additional_checks_each_failure_and_success(tmp_path: Path) -> None:
    tracker = _tracker()
    assert not asyncio.run(tracker.get_additional_checks(_meta(category="TV")))
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(adult_media=True))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(is_disc="BDMV"))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(container="MP4"))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(video_codec="HEVC"))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(
            _meta(video_codec="VP9", video_encode="VP9")
        )
    )
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(name="Movie.CAM.2024"))
    )
    bad = tmp_path / "bad.rar"
    bad.write_bytes(b"x")
    assert not asyncio.run(
        tracker.get_additional_checks(_meta(filelist=[str(bad)]))
    )
    assert not asyncio.run(tracker.get_additional_checks(_meta()))
    good = _meta(
        subtitle_languages=["pt"], video_codec="AVC", video_encode="H.264"
    )
    assert asyncio.run(tracker.get_additional_checks(good))


def test_upload_artifact_helpers(tmp_path: Path) -> None:
    tracker = _tracker()
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="u",
        debug=False,
        tracker_status={"MAKINGOFF": {}},
    )
    tracker._get_portuguese_subtitles = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert asyncio.run(tracker._prepare_upload_subtitles(meta)) is None
    meta.debug = True
    assert asyncio.run(tracker._prepare_upload_subtitles(meta)) == []
    existing = tmp_path / "sub.srt"
    existing.write_text("sub")
    tracker._get_portuguese_subtitles = AsyncMock(return_value=[str(existing)])  # type: ignore[method-assign]
    assert asyncio.run(tracker._prepare_upload_subtitles(meta)) == [
        str(existing)
    ]
    source_dir = tmp_path / "tmp" / "u"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "[MAKINGOFF].torrent").write_bytes(b"torrent")
    named, release = asyncio.run(tracker._create_upload_torrent(meta))
    assert Path(named).exists() and release
    zipped = tracker._zip_subtitles(meta, release, [str(existing)])
    assert zipped and zipfile.is_zipfile(zipped[0])
    assert tracker._zip_subtitles(meta, release, []) == []


def test_debug_and_live_upload_flow(tmp_path: Path) -> None:
    tracker = _tracker()
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="u",
        debug=True,
        hardcoded_subs=True,
        tracker_status={"MAKINGOFF": {}},
    )
    temp = tmp_path / "tmp" / "u"
    temp.mkdir(parents=True)
    (temp / "[MAKINGOFF].torrent").write_bytes(b"torrent")
    tracker.get_forum_id = AsyncMock(return_value=26)  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="Title")  # type: ignore[method-assign]
    tracker.generate_description = AsyncMock(return_value="Body")  # type: ignore[method-assign]
    tracker._get_portuguese_subtitles = AsyncMock(return_value=[])  # type: ignore[method-assign]
    assert asyncio.run(tracker.upload(meta))
    assert meta.tracker_status["MAKINGOFF"]["status_message"].startswith(
        "Debug"
    )
    meta.debug = False
    tracker._prepare_upload_artifacts = AsyncMock(
        return_value=(str(temp / "x.torrent"), [])
    )  # type: ignore[method-assign]
    tracker._live_upload_tokens = AsyncMock(return_value=("c", "h", "co"))  # type: ignore[method-assign]
    tracker._upload_forum_attachments = AsyncMock(return_value=True)  # type: ignore[method-assign]
    tracker._create_live_topic = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(tracker.upload(meta))
    tracker._live_upload_tokens = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert not asyncio.run(tracker._live_upload(meta, 26, "x", []))


def test_remaining_subtitle_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    missing = tmp_path / "missing.srt"
    assert tracker._external_subtitle_match(missing) == (False, False)
    plain = tmp_path / "plain.srt"
    plain.write_text("the and you that was for", encoding="utf-8")
    assert tracker._external_subtitle_match(plain) == (False, False)
    marked = tmp_path / "movie.pt.srt"
    marked.write_text("the and you", encoding="utf-8")
    meta = _meta(subtitle_files=[str(marked), str(plain)])
    assert tracker._external_portuguese_subtitles(meta) == [str(marked)]
    video = tmp_path / "movie.mkv"
    video.write_bytes(b"video")
    embedded = _meta(
        filelist=[str(video)],
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Text", "Language": "pt", "Format": "SRT"},
                    {"@type": "Text", "Language": "en", "Format": "SRT"},
                ]
            }
        },
    )
    tracker._extract_embedded_subtitle = AsyncMock(side_effect=["out.srt"])  # type: ignore[method-assign]
    assert asyncio.run(tracker._embedded_portuguese_subtitles(embedded)) == [
        "out.srt"
    ]
    tracker._external_portuguese_subtitles = Mock(return_value=["a.srt"])  # type: ignore[method-assign]
    tracker._embedded_portuguese_subtitles = AsyncMock(return_value=["b.srt"])  # type: ignore[method-assign]
    assert asyncio.run(tracker._get_portuguese_subtitles(_meta())) == [
        "a.srt",
        "b.srt",
    ]
    assert tracker._embedded_subtitle_video(_meta(is_disc="DVD")) is None
    assert tracker._embedded_subtitle_video(_meta(filelist=[])) is None
    invalid = tmp_path / "movie.txt"
    invalid.write_text("x")
    assert (
        tracker._embedded_subtitle_video(_meta(filelist=[str(invalid)]))
        is None
    )
    assert not tracker._embedded_track_is_portuguese(
        {"Language": "en", "Title": "English"}
    )
    assert tracker._subtitle_title_slug({}) == ""
    empty = tmp_path / "empty.srt"
    empty.write_text("")
    assert not tracker._is_subtitle_in_portuguese(str(empty))
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=OSError("bad")))
    assert tracker._read_subtitle_sample(str(empty)) == ""


def test_remaining_media_and_localization_branches(tmp_path: Path) -> None:
    tracker = _tracker()
    assert (
        tracker._mediainfo_filesize(_meta(source_size=2 * 1024**3))
        == "2.00 GB"
    )
    assert tracker._mediainfo_filesize(_meta(source_size="bad")) == "N/A"
    assert tracker._mediainfo_duration({}, {"Duration": "bad"}) == ""
    assert tracker._aspect_ratio(640, 480) == "Tela Cheia (4x3)"
    assert tracker._aspect_ratio(1920, 800) == "Scope (2.35:1)"
    assert tracker._dimension_value("bad") == 0
    assert tracker._valid_resolution_text("bad") is False
    assert tracker._resolution_line("0x1080") == ""
    assert tracker._linux_ffmpeg_candidate(str(tmp_path)) is not None
    assert tracker._historic_country_name("SU", None)
    assert (
        tracker._localizer_countries(_meta(origin_country=[]))
        == "Desconhecido"
    )


def test_remaining_session_http_error_branches() -> None:
    tracker = _tracker()
    tracker.session.get = AsyncMock(
        return_value=_response(status=403, text="forbidden")
    )
    assert asyncio.run(tracker._session_home_html()) == "forbidden"
    response = _response(status=500, text="error")
    tracker.session.get = AsyncMock(return_value=response)
    assert asyncio.run(tracker._session_home_html()) == "error"
    tracker.session.get = AsyncMock(side_effect=httpx.ConnectError("down"))
    assert asyncio.run(tracker._session_home_html()) is None
    tracker._session_home_html = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.refresh_session())
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker._new_post_page(26)) is None
    tracker._new_post_page = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_new_post_tokens(26)) == ("", "", "")
    tracker._new_post_page = AsyncMock(
        return_value='<html data-logged-in="true"><input name="attachment_hash" value="h"></html>'
    )  # type: ignore[method-assign]
    assert asyncio.run(tracker.get_new_post_tokens(26)) == ("", "h", "")


def test_remaining_search_http_error_branches() -> None:
    tracker = _tracker()
    payload = tracker._search_payload("x", "c", None, False)
    tracker.session.post = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker._search_redirect(payload)) is None
    invalid_json = httpx.Response(
        200, content=b"not-json", request=httpx.Request("POST", "https://x")
    )
    tracker.session.post = AsyncMock(return_value=invalid_json)
    assert asyncio.run(tracker._search_redirect(payload)) is None
    tracker.session.get = AsyncMock(return_value=_response(text="page"))
    assert asyncio.run(tracker._search_page_html("https://x")) == "page"
    tracker.session.get = AsyncMock(return_value=_response(status=500))
    assert asyncio.run(tracker._search_page_html("https://x")) is None
    tracker._search_page_html = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker._next_search_page("https://x", {})) is None
    tracker.max_search_pages = 1
    tracker._next_search_page = AsyncMock(return_value="https://next")  # type: ignore[method-assign]
    assert asyncio.run(tracker._collect_search_pages("https://start")) == {}
    tracker._warn_search_page_limit("https://next", {"https://start"})
    assert tracker._href_text(["/a", "/b"]) == "/a /b"
    assert (
        tracker._search_item_result(SimpleNamespace(find=lambda *_args: None))
        is None
    )
    anchor = SimpleNamespace(
        get_text=lambda *_args, **_kwargs: "T",
        get=lambda *_args, **_kwargs: "",
    )
    assert (
        tracker._search_item_result(
            SimpleNamespace(find=lambda *_args: anchor)
        )
        is None
    )
    results = {"T": "u1"}
    tracker._store_search_result(results, "T", "u2")
    assert len(results) == 2
    tracker._csrf_token = "csrf"
    tracker._search_redirect = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_candidate("T")) is None


def test_remaining_index_topic_and_credentials_branches() -> None:
    tracker = _tracker()
    empty_card = SimpleNamespace(select_one=lambda *_args: None)
    assert tracker._card_topic_result(empty_card) is None
    bad_anchor = SimpleNamespace(
        get_text=lambda *_args, **_kwargs: "", get=lambda *_args, **_kwargs: ""
    )
    bad_card = SimpleNamespace(
        select_one=lambda selector: (
            bad_anchor if "topicos" in selector else None
        )
    )
    assert tracker._card_topic_result(bad_card) is None
    html = """
    <div class='filme-card'>
      <a href='https://imdb.com/title/tt1234567'>IMDb</a>
    </div>
    """
    assert tracker._parse_index_results(html, "tt1234567") == {}
    tracker._post_topic = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        asyncio.run(tracker.create_topic(26, "c", "h", "hc", "T", "B")) == ""
    )
    tracker.session.post = AsyncMock(
        return_value=_response(status=500, text="bad")
    )
    assert asyncio.run(tracker._post_topic(26, {})) is None
    tracker.session.post = AsyncMock(
        return_value=httpx.Response(
            200, content=b"bad", request=httpx.Request("POST", "https://x")
        )
    )
    assert asyncio.run(tracker._post_topic(26, {})) is None
    meta = _meta()
    tracker.cookie_validator.load_session_cookies = AsyncMock(
        return_value={"sid": "x"}
    )
    tracker.refresh_session = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.validate_credentials(meta))


def test_remaining_duplicate_search_branches() -> None:
    tracker = _tracker()
    tracker.validate_credentials = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_existing(_meta())) == []
    tracker.validate_credentials = AsyncMock(return_value=True)  # type: ignore[method-assign]
    tracker._resolve_display_title = AsyncMock(return_value="Title")  # type: ignore[method-assign]
    tracker.get_forum_id = AsyncMock(return_value=26)  # type: ignore[method-assign]
    tracker._search_exact_imdb = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker._search_titles = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker.search_existing(_meta())) == []
    results: dict[str, str] = {}
    exact: set[str] = set()
    assert (
        asyncio.run(
            tracker._search_exact_imdb(_meta(imdb_tt=""), results, exact)
        )
        is None
    )
    tracker.get_post_resolution = AsyncMock(return_value=720)  # type: ignore[method-assign]
    meta = _meta(resolution="1080p", debug=True)
    assert (
        asyncio.run(
            tracker._existing_duplicate(
                meta, "Film (2020)", "u", True, "2024", set(), {}
            )
        )
        is None
    )
    assert (
        asyncio.run(
            tracker._existing_duplicate(
                meta, "Film (2024)", "u", True, "2024", set(), {}
            )
        )
        is None
    )


def test_remaining_forum_interactive_and_origin_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    assert tracker._genres_text(_meta(genres="Drama")) == "Drama"
    assert tracker._origin_countries(
        _meta(origin_country=[], production_countries=[{"iso_3166_1": "FR"}])
    ) == ["FR"]
    meta = _meta(origin_country=["ZZ"], unattended=False)
    assert tracker._unattended_forum_default(meta, ["ZZ"]) is None
    tracker._log_forum_options()
    monkeypatch.setattr(
        makingoff, "prompt_in_thread", AsyncMock(return_value="7")
    )
    assert asyncio.run(tracker._manual_forum_id(meta, ["ZZ"])) == 27
    monkeypatch.setattr(
        makingoff, "prompt_in_thread", AsyncMock(return_value="99")
    )
    assert asyncio.run(tracker._manual_forum_id(meta, ["ZZ"])) == 26


def test_remaining_title_and_description_branches() -> None:
    tracker = _tracker()
    brazil = _meta(
        origin_country=["BR"], title="Brasil", original_title="Brasil"
    )
    assert tracker._brazilian_display_title(brazil, {}) == "Brasil"
    foreign = _meta(title="Same", original_title="Same")
    en = {
        "translations": {
            "translations": [
                {
                    "iso_639_1": "en",
                    "iso_3166_1": "US",
                    "data": {"title": "English"},
                }
            ]
        }
    }
    assert tracker._foreign_english_title(foreign, en) == "English"
    assert (
        tracker._resolved_display_title(
            _meta(origin_country=["BR"], title="T", original_title="O")
        )
        == "O"
    )
    assert (
        tracker._topic_title_part(_meta(original_title="Same"), "Same")
        == "Same"
    )
    assert tracker._image_url(object()) == ""
    meta = _meta(language_checked=False, subtitle_languages=[])
    tracker._known_subtitle_type = Mock(return_value="Sem Legenda")  # type: ignore[method-assign]
    original_processor = makingoff.languages_manager.process_desc_language
    makingoff.languages_manager.process_desc_language = AsyncMock(
        return_value=None
    )
    try:
        assert asyncio.run(tracker._subtitles_ptbr(meta)) == "Sem Legenda"
    finally:
        makingoff.languages_manager.process_desc_language = original_processor
    pt = {"overview": "Direct"}
    assert tracker._description_overview(_meta(), pt) == "Direct"
    assert (
        tracker._description_overview(_meta(overview="Fallback"), {})
        == "Fallback"
    )
    assert (
        tracker._description_imdb_url(
            _meta(imdb_info={"imdb_url": "https://imdb"})
        )
        == "https://imdb"
    )
    assert tracker._description_imdb_url(_meta(imdb_id="")) == ""
    assert tracker._imdb_directors(
        _meta(imdb_info={"directors": ["D", 1]})
    ) == ["D"]
    assert tracker._track_by_type([], "Video") == {}
    assert tracker._first_meta_value(_meta(), "not_here") == ""
    assert tracker._description_duration(_meta(runtime=0), {}, {}) == ""


def test_remaining_check_and_upload_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    tracker._warn_low_hd_bitrate(_meta(video_bitrate=1000, video_height=1080))
    assert tracker._hd_bitrate_values(
        _meta(video_bitrate="bad", video_height="bad")
    ) == (0, 0)
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="u",
        debug=False,
        tracker_status={"MAKINGOFF": {}},
    )
    sub = tmp_path / "sub.srt"
    sub.write_text("sub")
    monkeypatch.setattr(
        zipfile.ZipFile, "__init__", Mock(side_effect=OSError("zip bad"))
    )
    assert tracker._zip_subtitles(meta, "release", [str(sub)]) is None
    meta.debug = True
    assert tracker._zip_subtitles(meta, "release", [str(sub)]) == []
    tracker._prepare_upload_subtitles = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker._prepare_upload_artifacts(meta)) is None
    tracker._prepare_upload_subtitles = AsyncMock(return_value=[str(sub)])  # type: ignore[method-assign]
    tracker._create_upload_torrent = AsyncMock(
        return_value=("torrent", "release")
    )  # type: ignore[method-assign]
    tracker._zip_subtitles = Mock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker._prepare_upload_artifacts(meta)) is None
    tracker.validate_credentials = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert asyncio.run(tracker._live_upload_tokens(meta, 26)) is None
    tracker.validate_credentials = AsyncMock(return_value=True)  # type: ignore[method-assign]
    tracker.get_new_post_tokens = AsyncMock(return_value=("", "", ""))  # type: ignore[method-assign]
    assert asyncio.run(tracker._live_upload_tokens(meta, 26)) is None
    tracker.get_new_post_tokens = AsyncMock(return_value=("c", "h", "co"))  # type: ignore[method-assign]
    assert asyncio.run(tracker._live_upload_tokens(meta, 26)) == (
        "c",
        "h",
        "co",
    )


def test_remaining_attachment_and_topic_upload_paths() -> None:
    tracker = _tracker()
    meta = _meta(tracker_status={"MAKINGOFF": {}}, debug=False)
    tracker.upload_attachment = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker._upload_forum_attachments(
            meta, 26, "torrent", [], ("c", "h", "co")
        )
    )
    tracker.upload_attachment = AsyncMock(side_effect=[True, False])  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker._upload_forum_attachments(
            meta, 26, "torrent", ["sub.zip"], ("c", "h", "co")
        )
    )
    tracker.upload_attachment = AsyncMock(side_effect=[True, True])  # type: ignore[method-assign]
    assert asyncio.run(
        tracker._upload_forum_attachments(
            meta, 26, "torrent", ["sub.zip"], ("c", "h", "co")
        )
    )
    tracker.create_topic = AsyncMock(return_value="")  # type: ignore[method-assign]
    tracker.get_name = AsyncMock(return_value="T")  # type: ignore[method-assign]
    tracker.generate_description = AsyncMock(return_value="B")  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker._create_live_topic(meta, 26, ("c", "h", "co"))
    )
    tracker.create_topic = AsyncMock(return_value="https://topic")  # type: ignore[method-assign]
    assert asyncio.run(tracker._create_live_topic(meta, 26, ("c", "h", "co")))
    tracker._live_upload_tokens = AsyncMock(return_value=("c", "h", "co"))  # type: ignore[method-assign]
    tracker._upload_forum_attachments = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(tracker._live_upload(meta, 26, "torrent", []))
    tracker._prepare_upload_artifacts = AsyncMock(return_value=None)  # type: ignore[method-assign]
    tracker.get_forum_id = AsyncMock(return_value=26)  # type: ignore[method-assign]
    assert not asyncio.run(tracker.upload(meta))


def test_final_http_and_pagination_branches(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.session.post = AsyncMock(
        return_value=_response(status=500, text="bad")
    )
    assert asyncio.run(tracker._post_topic(26, {})) is None
    tracker.session.post = AsyncMock(
        return_value=httpx.Response(
            200,
            content=b"not-json",
            request=httpx.Request("POST", "https://www.makingoff.org/topic"),
        )
    )
    assert asyncio.run(tracker._post_topic(26, {})) is None
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"data")
    tracker._post_attachment = AsyncMock(side_effect=ValueError("bad json"))  # type: ignore[method-assign]
    assert (
        asyncio.run(
            tracker._upload_attachment_response(
                str(file_path), {}, file_path.name
            )
        )
        is None
    )
    assert (
        tracker._attachment_mime_type("file.txt", "file.txt") == "text/plain"
    )
    assert (
        tracker._attachment_mime_type("file.unknownxyz", "file.unknownxyz")
        == "application/octet-stream"
    )
    tracker._next_search_page = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert asyncio.run(tracker._collect_search_pages("/search/1/")) == {}
    tracker._next_search_page = AsyncMock(return_value="https://same")  # type: ignore[method-assign]
    assert asyncio.run(tracker._collect_search_pages("https://same")) == {}
    tracker.session.get = AsyncMock(
        return_value=_response(text="<html></html>")
    )
    assert asyncio.run(tracker.get_post_resolution("https://topic")) == 0


def test_final_subtitle_ffmpeg_and_description_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        makingoff, "configured_binary", Mock(return_value="/custom/ffmpeg")
    )
    assert (
        tracker._get_ffmpeg_path(_meta(base_dir=str(tmp_path)))
        == "/custom/ffmpeg"
    )
    monkeypatch.setattr(platform, "machine", lambda: "riscv")
    assert tracker._linux_ffmpeg_candidate(str(tmp_path)) is None
    content = tmp_path / "unknown.srt"
    content.write_text("que não uma com mais para você", encoding="utf-8")
    assert tracker._sidecar_is_portuguese(content)
    marked = tmp_path / "movie.pt.srt"
    marked.write_text("english only", encoding="utf-8")
    assert tracker._sidecar_is_portuguese(marked)
    assert (
        asyncio.run(tracker._embedded_portuguese_subtitles(_meta(filelist=[])))
        == []
    )
    tracker._has_external_portuguese_subtitle = Mock(return_value=False)  # type: ignore[method-assign]
    assert tracker._known_subtitle_type(_meta(subtitle_languages=[])) is None
    tracker._resolve_display_title = AsyncMock(return_value="Brasil")  # type: ignore[method-assign]
    assert asyncio.run(
        tracker._description_titles(
            _meta(
                origin_country=["BR"], title="Brasil", original_title="Brasil"
            )
        )
    ) == ("Brasil", "Brasil")
    assert tracker._poster_url(_meta(tmdb_poster_path=""), {}) == ""
    assert (
        tracker._first_meta_value(_meta(awards="Award"), "awards") == "Award"
    )


def test_final_search_and_debug_branches(tmp_path: Path) -> None:
    tracker = _tracker()
    empty_results: dict[str, str] = {}
    empty_exact: set[str] = set()
    assert (
        asyncio.run(
            tracker._search_exact_imdb(
                _meta(imdb_tt=""), empty_results, empty_exact
            )
        )
        is None
    )
    tracker.search_index_by_imdb = AsyncMock(
        return_value={"Title": "https://topic"}
    )  # type: ignore[method-assign]
    results: dict[str, str] = {}
    exact: set[str] = set()
    asyncio.run(
        tracker._search_exact_imdb(_meta(imdb_tt="tt1234567"), results, exact)
    )
    assert results == {"Title": "https://topic"}
    assert exact == {"https://topic"}
    meta = _meta(
        base_dir=str(tmp_path),
        uuid="u",
        debug=True,
        tracker_status={"MAKINGOFF": {}},
    )
    (tmp_path / "tmp" / "u").mkdir(parents=True)
    tracker.get_name = AsyncMock(return_value="Title")  # type: ignore[method-assign]
    tracker.generate_description = AsyncMock(return_value="Body")  # type: ignore[method-assign]
    assert asyncio.run(tracker._debug_upload(meta, 26, ["sub.zip"]))
