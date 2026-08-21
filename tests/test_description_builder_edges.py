from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers import description_builder
from src.integrations.trackers.description_builder import (
    DescriptionBuilder,
    gen_desc,
    html_to_bbcode,
)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    queue: ClassVar[list[object]] = []
    urls: ClassVar[list[str]] = []

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

    async def get(self, url: str) -> _Response:
        type(self).urls.append(url)
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.urls = []


def _config(
    tracker: str = "TEST",
    *,
    defaults: dict[str, object] | None = None,
    tracker_values: object = None,
) -> dict[str, Any]:
    return {
        "DEFAULT": dict(defaults or {}),
        "TRACKERS": {
            tracker: {} if tracker_values is None else tracker_values
        },
    }


def _builder(
    tracker: str = "TEST",
    *,
    defaults: dict[str, object] | None = None,
    tracker_values: object = None,
) -> DescriptionBuilder:
    return DescriptionBuilder(
        tracker,
        _config(tracker, defaults=defaults, tracker_values=tracker_values),
    )


def _meta(tmp_path: Path, **values: object) -> Meta:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "description",
        "path": str(source),
        "description_link": "",
        "description_file": "",
        "description_template": "",
        "description_template_content": "",
        "description_link_content": "",
        "description_file_content": "",
        "description_nfo_content": "",
        "description": "",
        "saved_description": False,
        "skip_gen_desc": False,
        "nfo": False,
        "auto_nfo": False,
        "bhd_nfo": False,
        "category": "MOVIE",
        "is_disc": "",
        "discs": [],
        "filelist": [str(tmp_path / "movie.mkv")],
        "mediainfo": {},
        "tag": "-GROUP",
        "tonemapped": False,
        "logo": "",
        "tmdb_logo": "",
        "tvmaze_episode_data": {},
        "episode_tmdb_data": {},
        "tvdb_season_name": "",
        "season": "",
        "episode": "",
        "overview_meta": "",
        "auto_episode_title": "",
        "release_url": "",
        "hosted_artwork": [],
        "audio_spectrogram": False,
        "audio_spectrogram_tracks": [],
        "dynamic_hdr_plot": False,
        "tracker_image_collections": {},
        "menu_images": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
    }
    state.update(values)
    state_dir = tmp_path / "tmp" / str(state["uuid"])
    state_dir.mkdir(parents=True, exist_ok=True)
    return Meta(state)


@pytest.fixture(autouse=True)
def _http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.reset()
    monkeypatch.setattr(description_builder.httpx, "AsyncClient", _Client)


def test_html_to_bbcode_empty_and_all_tags() -> None:
    assert html_to_bbcode("") == ""
    html = (
        "<p><b>B</b><i>I</i><u>U</u><s>S</s><em>E</em><strong>Strong</strong>"
        "<strike>Strike</strike><del>Delete</del></p>"
        "<ul class='x'><li><br/>One<br/></li><li>Two</li></ul><unknown>gone</unknown>"
    )
    converted = html_to_bbcode(html)
    assert "[b]B[/b]" in converted
    assert "[i]I[/i]" in converted
    assert "[u]U[/u]" in converted
    assert "[s]S[/s]" in converted
    assert "* One" in converted and "* Two" in converted
    assert "<" not in converted


def test_gen_desc_template_missing_nfo_and_default_description(
    tmp_path: Path,
) -> None:
    template = tmp_path / "data" / "templates" / "release.txt"
    template.parent.mkdir(parents=True)
    template.write_text("Title: {{ title }}\r\n", encoding="utf-8")
    meta = _meta(tmp_path, title="Example", description_template="release")
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert result.description == "Title: Example"
    assert result.description_template_content == "Title: Example"
    assert result.saved_description

    missing = _meta(tmp_path, description_template="missing", nfo=True)
    result = asyncio.run(gen_desc(missing, object(), object()))  # type: ignore[arg-type]
    assert result.description == "" and not result.saved_description

    plain = _meta(tmp_path, description="  Existing description  ")
    result = asyncio.run(gen_desc(plain, object(), object()))  # type: ignore[arg-type]
    assert result.description == "Existing description"

    none_text = _meta(tmp_path, description="None")
    result = asyncio.run(gen_desc(none_text, object(), object()))  # type: ignore[arg-type]
    assert result.description == ""


def test_gen_desc_scene_bhd_source_and_latin1_nfo(tmp_path: Path) -> None:
    state = tmp_path / "tmp" / "description"
    source = tmp_path / "source"
    state.mkdir(parents=True, exist_ok=True)
    source.mkdir(parents=True, exist_ok=True)

    scene = state / "scene.nfo"
    scene.write_text("scene nfo", encoding="utf-8")
    meta = _meta(tmp_path, nfo=True, auto_nfo=True)
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert (
        "Scene NFO" in result.description
        and result.description_nfo_content == "scene nfo"
    )

    scene.unlink()
    bhd = state / "framestor.nfo"
    bhd.write_text("bhd nfo", encoding="utf-8")
    meta = _meta(tmp_path, nfo=True, bhd_nfo=True)
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert "FraMeSToR NFO" in result.description

    bhd.unlink()
    normal = source / "normal.nfo"
    normal.write_text("normal nfo", encoding="utf-8")
    meta = _meta(tmp_path, nfo=True)
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert result.description == "[code]normal nfo[/code]"

    normal.write_bytes(b"latin-\xff")
    meta = _meta(tmp_path, nfo=True)
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert "latin-ÿ" in result.description


def test_gen_desc_link_file_precedence_not_found_and_error(
    tmp_path: Path,
) -> None:
    description_file = tmp_path / "description.txt"
    description_file.write_text(" file text \r\n", encoding="utf-8")
    _Client.reset(_Response(" linked text \r\n"))
    meta = _meta(
        tmp_path,
        description_link="https://paste.invalid/path/item",
        description_file=str(description_file),
    )
    result = asyncio.run(gen_desc(meta, object(), object()))  # type: ignore[arg-type]
    assert result.description == "linked text"
    assert result.description_link_content == "linked text"
    assert result.description_file_content == "file text"
    assert _Client.urls == ["https://paste.invalid/path/raw/item"]

    _Client.reset(_Response("Not Found"))
    meta = _meta(tmp_path, description_link="https://paste.invalid/item")
    assert asyncio.run(gen_desc(meta, object(), object())).description == ""  # type: ignore[arg-type]

    _Client.reset(
        httpx.RequestError(
            "offline", request=httpx.Request("GET", "https://paste.invalid")
        )
    )
    with pytest.raises(httpx.RequestError):
        asyncio.run(
            gen_desc(
                _meta(tmp_path, description_link="https://paste.invalid/item"),
                object(),
                object(),
            )
        )  # type: ignore[arg-type]


def test_constructor_and_config_helpers_cover_shapes() -> None:
    with pytest.raises(KeyError, match="TRACKERS"):
        DescriptionBuilder("TEST", {"DEFAULT": {}})
    with pytest.raises(KeyError, match="Missing tracker config"):
        DescriptionBuilder("TEST", {"DEFAULT": {}, "TRACKERS": {"OTHER": {}}})

    builder = _builder(
        defaults={
            "fallback_bool": "yes",
            "fallback_int": "bad",
            "fallback_str": None,
        },
        tracker_values={
            "true": " on ",
            "false": "0",
            "numeric": 2,
            "bad": "invalid",
            "integer": "7",
            "bad_int": object(),
            "direct": 123,
            "none": None,
            "tag_overrides": {
                "-GROUP": {"direct": "tagged"},
            },
        },
    )
    assert builder._get_bool_config("true")
    assert not builder._get_bool_config("false", True)
    assert builder._get_bool_config("numeric")
    assert not builder._get_bool_config("bad")
    assert builder._get_bool_config("fallback_bool")
    assert builder._get_int_config("integer") == 7
    assert builder._get_int_config("bad_int", "also-bad") == 0
    assert builder._get_int_config("fallback_int", 3) == 3
    meta = Meta(tag="-GROUP")
    assert builder._get_str_config("direct", meta=meta) == "tagged"
    assert builder._get_str_config("direct") == "123"
    assert builder._get_str_config("none", "fallback") == "fallback"
    assert builder._get_str_config("fallback_str", "fallback") == "fallback"
    assert builder._get_tag_override("direct", Meta(tag="")) is None

    malformed = _builder(
        defaults={"tag_overrides": []},
        tracker_values={"tag_overrides": ["bad"]},
    )
    assert malformed._get_tag_override("x", meta) is None


def test_headers_logo_tv_info_and_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _builder(
        "BJSHARE",
        defaults={
            "custom_description_header": "Header",
            "tonemapped_header": "Tone",
            "add_logo": True,
            "logo_size": 400,
            "episode_overview": True,
        },
    )
    meta = _meta(
        tmp_path,
        tonemapped=True,
        tmdb_logo="/logo.svg",
        category="TV",
        episode_tmdb_data={"name": "Episode", "overview": "Overview"},
    )
    assert asyncio.run(builder.get_custom_header(meta)) == "Header"
    assert asyncio.run(builder.get_tonemapped_header(meta)) == "Tone"
    assert asyncio.run(builder.get_logo_section(meta)) == (
        "https://image.tmdb.org/t/p/w300//logo.png",
        "300",
    )
    assert asyncio.run(builder.get_tv_info(meta)) == ("Episode", "Overview")

    generic = _builder(
        defaults={
            "add_logo": True,
            "logo_size": "450",
            "episode_overview": True,
        },
    )
    meta = _meta(
        tmp_path,
        logo="https://logo.invalid/logo.png",
        category="TV",
        season="S01",
        episode="E02",
        tvdb_season_name="Season One",
        tvmaze_episode_data={
            "season_name": "",
            "overview": "<p><b>Overview</b></p>",
            "episode_name": "Episode TBA",
        },
        auto_episode_title="Manual Episode",
    )
    title, overview = asyncio.run(generic.get_tv_info(meta))
    assert title == "Season One - S01E02: Manual Episode"
    assert "[b]Overview[/b]" in overview
    assert asyncio.run(generic.get_logo_section(meta)) == (meta.logo, "450")

    no_logo = _builder(defaults={"add_logo": False})
    assert asyncio.run(no_logo.get_logo_section(meta)) == ("", "")
    assert asyncio.run(no_logo.get_tv_info(_meta(tmp_path))) == ("", "")

    for method_name in (
        "get_custom_header",
        "get_tonemapped_header",
        "get_logo_section",
        "get_tv_info",
    ):
        method = getattr(generic, method_name)
        monkeypatch.setattr(
            generic,
            "_get_str_config"
            if "header" in method_name
            else "_get_bool_config",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("broken")
            ),
        )
        result = asyncio.run(method(meta))
        assert result in ("", ("", ""))
        monkeypatch.undo()


def test_mediainfo_section_cache_full_short_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _builder(defaults={"full_mediainfo": True})
    meta = _meta(tmp_path, is_disc="BDMV")
    assert asyncio.run(builder.get_mediainfo_section(meta)) == ""
    assert (
        asyncio.run(
            builder.get_mediainfo_section(_meta(tmp_path, category="BOOK"))
        )
        == ""
    )

    state = tmp_path / "tmp" / "description"
    full = state / "MEDIAINFO_CLEANPATH.txt"
    full.write_text("full mediainfo", encoding="utf-8")
    meta = _meta(tmp_path, is_disc="DVD")
    builder.common.path_exists = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(builder.get_mediainfo_section(meta)) == "full mediainfo"

    short = state / "MEDIAINFO_SHORT.txt"
    short.write_text("cached short", encoding="utf-8")
    builder = _builder(defaults={"full_mediainfo": False})
    meta = _meta(tmp_path)
    assert asyncio.run(builder.get_mediainfo_section(meta)) == "cached short"

    short.unlink()
    report = {
        "media": {
            "track": [
                {
                    "@type": "General",
                    "CompleteName": "/path/Movie.mkv",
                    "FileSize": str(5 * 1024**3),
                    "Format": "Matroska",
                    "Duration": "3661.2345",
                },
                {
                    "@type": "Video",
                    "Format": "AVC",
                    "Encoded_Library": "x264",
                    "HDR_Format_String": "HDR10",
                    "transfer_characteristics": "PQ",
                    "Width": "1920",
                    "Height": "1080",
                    "BitRate": "12000000",
                    "FrameRate": "23.976",
                },
                {
                    "@type": "Audio",
                    "Format": "AAC",
                    "Format_Commercial_IfAny": "Dolby Digital",
                    "Channels": "1",
                    "SamplingRate": "48000",
                    "BitRate": "640000",
                    "Language": "pt-BR",
                    "Title": "Commentary",
                },
                {
                    "@type": "Text",
                    "Language": "bad_tag_!",
                    "Format": "SRT",
                    "Title": "Forced",
                },
                "ignored",
            ]
        }
    }
    meta = _meta(tmp_path, mediainfo=report)
    result = asyncio.run(builder.get_mediainfo_section(meta))
    assert "12.0 Mb/s" in result and "Portuguese (BR)" in result
    assert "bad_tag_!" in result
    assert short.is_file()

    assert (
        builder.format_short_mediainfo_json({"media": {"track": "bad"}}) == ""
    )
    assert (
        builder.format_short_mediainfo_json(
            {"media": {"track": [{"@type": "Video"}]}}
        )
        == ""
    )
    invalid = {
        "media": {
            "track": [
                {"@type": "General", "FileSize": "bad", "Duration": "bad"},
                {"@type": "Video", "BitRate": "bad"},
                {
                    "@type": "Audio",
                    "SamplingRate": "bad",
                    "BitRate": "bad",
                    "Channels": "2",
                },
            ]
        }
    }
    rendered = builder.format_short_mediainfo_json(invalid, "fallback.mkv")
    assert "fallback" in rendered

    monkeypatch.setattr(
        builder.common, "makedirs", AsyncMock(side_effect=OSError("read only"))
    )
    meta = _meta(tmp_path, uuid="description-error", mediainfo=report)
    assert asyncio.run(builder.get_mediainfo_section(meta))


def test_bdinfo_headers_user_signature_bluray_and_plot_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = {
        "screenshot_header": "Screens",
        "disc_menu_header": "Menus",
        "custom_signature": "Signature",
        "add_bluray_link": True,
        "use_bluray_images": True,
        "bluray_image_size": 250,
        "add_audio_spectrogram": True,
        "audio_spectrogram_header": "Spectrograms",
        "add_dynamic_hdr_plot": True,
        "dynamic_hdr_plot_header": "HDR Plots",
        "screens_per_row": 2,
    }
    builder = _builder(defaults=defaults)
    image = {
        "web_url": "https://web",
        "raw_url": "https://raw",
        "img_url": "https://thumb",
    }
    meta = _meta(
        tmp_path,
        is_disc="BDMV",
        discs=[{"summary": "Disc 1"}, {"summary": ""}],
        menu_images=[image],
        description_file_content=" File Description ",
        description_link_content=" Link Description ",
        release_url="https://bluray.invalid/release",
        hosted_artwork=[image],
        audio_spectrogram=True,
        dynamic_hdr_plot=True,
        tracker_image_collections={
            "TEST": {
                "menu_images": [image],
                "spectrograms_images": [image, "bad", {"web_url": ""}],
                "dynamic_hdr_plot_images": [image, "bad", {"raw_url": ""}],
            }
        },
    )
    assert asyncio.run(builder.get_bdinfo_section(meta)) == "Disc 1"
    assert (
        asyncio.run(
            builder.get_bdinfo_section(
                _meta(tmp_path, is_disc="BDMV", discs=["bad"])
            )
        )
        == ""
    )
    assert asyncio.run(builder.screenshot_header(meta)) == "Screens"
    assert asyncio.run(builder.menu_screenshot_header(meta)) == "Menus"
    assert (
        asyncio.run(builder.get_user_description(meta)) == "File Description"
    )
    meta.description_file_content = ""
    assert (
        asyncio.run(builder.get_user_description(meta)) == "Link Description"
    )
    assert asyncio.run(builder.get_custom_signature(meta)) == "Signature"
    release_url, covers = asyncio.run(builder.get_bluray_section(meta))
    assert release_url == meta.release_url and "https://raw" in covers
    assert "Spectrograms" in asyncio.run(
        builder.get_audio_spectrogram_section(meta)
    )
    assert "HDR Plots" in asyncio.run(
        builder.get_dynamic_hdr_plot_section(meta)
    )

    covers_file = tmp_path / "tmp" / meta.uuid / "covers.json"
    covers_file.write_text(json.dumps([image]), encoding="utf-8")
    meta.hosted_artwork = []
    assert asyncio.run(builder.get_bluray_section(meta))[1]
    covers_file.write_text("not json", encoding="utf-8")
    assert asyncio.run(builder.get_bluray_section(meta))[1] == ""

    for tracker in ("TORRENTLEECH", "HDTORRENTS"):
        special = _builder(tracker, defaults=defaults)
        assert asyncio.run(
            special.get_bluray_section(
                _meta(
                    tmp_path,
                    is_disc="DVD",
                    hosted_artwork=[image],
                    release_url="x",
                )
            )
        )[1]

    assert (
        asyncio.run(builder.get_audio_spectrogram_section(_meta(tmp_path)))
        == ""
    )
    assert (
        asyncio.run(builder.get_dynamic_hdr_plot_section(_meta(tmp_path)))
        == ""
    )

    monkeypatch.setattr(
        builder,
        "_get_bool_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert asyncio.run(builder.get_audio_spectrogram_section(meta)) == ""
    assert asyncio.run(builder.get_bluray_section(meta)) == ("", "")


def _general_meta(tmp_path: Path, **values: object) -> Meta:
    image = {
        "web_url": "https://web",
        "raw_url": "https://raw",
        "img_url": "https://thumb",
    }
    base = _meta(
        tmp_path,
        image_list=[image],
        sorted_filelist=False,
        language_checked=False,
        audio_languages=["English"],
        subtitle_languages=["French"],
        write_audio_languages=True,
        write_subtitle_languages=True,
        write_hc_languages=True,
        description="API Description",
        description_nfo_content="NFO content",
        mteam_description="MTEAM Description",
        nexusphp_description="Nexus Description",
        framestor=False,
        ua_signature="Upload Assistant",
        debug=True,
        comparison=False,
        comparison_groups={},
        screens=1,
        skip_imghost_upload=False,
        retry_count=0,
        filelist=[str(tmp_path / "movie.mkv")],
    )
    for key, value in values.items():
        setattr(base, key, value)
    return base


def _stub_general_sections(
    builder: DescriptionBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        builder, "get_custom_header", AsyncMock(return_value="Custom Header")
    )
    monkeypatch.setattr(
        builder,
        "get_logo_section",
        AsyncMock(return_value=("https://logo", "300")),
    )
    monkeypatch.setattr(
        builder, "get_mediainfo_section", AsyncMock(return_value="MEDIAINFO")
    )
    monkeypatch.setattr(
        builder, "get_bdinfo_section", AsyncMock(return_value="BDINFO")
    )
    monkeypatch.setattr(
        builder,
        "get_bluray_section",
        AsyncMock(return_value=("https://bluray", "COVERS")),
    )
    monkeypatch.setattr(
        builder,
        "get_tv_info",
        AsyncMock(return_value=("Episode Title", "Episode Overview")),
    )
    monkeypatch.setattr(
        builder, "_build_book_desc_section", lambda _meta: "BOOK SECTION"
    )
    monkeypatch.setattr(
        builder, "_build_game_desc_section", lambda _meta: "GAME SECTION"
    )
    monkeypatch.setattr(
        builder, "_build_music_desc_section", lambda _meta: "MUSIC SECTION"
    )
    monkeypatch.setattr(
        builder,
        "get_user_description",
        AsyncMock(return_value="USER DESCRIPTION"),
    )
    monkeypatch.setattr(
        builder, "menu_section", AsyncMock(return_value="MENU SECTION")
    )
    monkeypatch.setattr(
        builder, "get_tonemapped_header", AsyncMock(return_value="TONEMAPPED")
    )
    monkeypatch.setattr(
        builder,
        "_handle_discs_and_screenshots",
        AsyncMock(return_value="SCREENSHOTS"),
    )
    monkeypatch.setattr(
        builder,
        "get_audio_spectrogram_section",
        AsyncMock(return_value="SPECTROGRAMS"),
    )
    monkeypatch.setattr(
        builder,
        "get_dynamic_hdr_plot_section",
        AsyncMock(return_value="HDR PLOTS"),
    )
    monkeypatch.setattr(
        builder,
        "get_custom_signature",
        AsyncMock(return_value="CUSTOM SIGNATURE"),
    )
    monkeypatch.setattr(
        builder, "tracker_specific_formats", lambda _tracker, text: text
    )


def test_general_description_all_media_tracker_formats_and_signatures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    language = AsyncMock()
    monkeypatch.setattr(
        description_builder.languages_manager,
        "process_desc_language",
        language,
    )
    cases = [
        ("BJSHARE", "TV", "DVD"),
        ("DIGITALCORE", "MOVIE", "BDMV"),
        ("FUNFILE", "BOOK", ""),
        ("PTSKIT", "GAME", ""),
        ("HDTORRENTS", "MUSIC", ""),
        ("TORRENTLEECH", "MOVIE", ""),
        ("TEST", "MOVIE", ""),
    ]
    for index, (tracker, category, disc) in enumerate(cases):
        builder = _builder(tracker, defaults={"multiScreens": 3})
        _stub_general_sections(builder, monkeypatch)
        meta = _general_meta(
            tmp_path,
            uuid=f"general-{index}",
            category=category,
            is_disc=disc,
        )
        (tmp_path / "tmp" / meta.uuid).mkdir(parents=True, exist_ok=True)
        result = asyncio.run(builder.general_description_generator(meta))
        assert "Custom Header" in result
        assert "Audio Language/s" in result and "Subtitle Language/s" in result
        assert "https://logo" in result
        assert "https://bluray" in result and "Episode Overview" in result
        assert "SCREENSHOTS" in result and "CUSTOM SIGNATURE" in result
        assert (
            tmp_path / "tmp" / meta.uuid / f"[{tracker}]DESCRIPTION.txt"
        ).is_file()
    assert language.await_count == len(cases)


def test_general_description_special_descriptions_nfo_and_user_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        description_builder.languages_manager,
        "process_desc_language",
        AsyncMock(),
    )

    builder = _builder("AITHER")
    _stub_general_sections(builder, monkeypatch)
    nfo_url = "https://i.imgur.com/e9o0zpQ.png"
    meta = _general_meta(
        tmp_path,
        uuid="framestor-nfo",
        framestor=True,
        description_nfo_content=f"NFO {nfo_url}",
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder.general_description_generator(
            meta, signature="EXPLICIT SIGNATURE"
        )
    )
    assert "beyondhd.co" in result and "EXPLICIT SIGNATURE" in result

    builder = _builder("AITHER")
    _stub_general_sections(builder, monkeypatch)
    meta = _general_meta(
        tmp_path,
        uuid="framestor-no-nfo",
        framestor=True,
        description_nfo_content="",
        description="[center][spoiler=Scene NFO:][code]remove[/code][/spoiler][/center]Kept",
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    assert "Kept" in asyncio.run(builder.general_description_generator(meta))

    for tracker, expected in (
        ("DIGITALCORE", "[nfo]"),
        ("TORRENTLEECH", "background-color"),
        ("TEST", "[pre]"),
    ):
        builder = _builder(tracker)
        _stub_general_sections(builder, monkeypatch)
        meta = _general_meta(
            tmp_path, uuid=f"nfo-{tracker}", description_nfo_content="NFO"
        )
        (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
        assert expected in asyncio.run(
            builder.general_description_generator(meta, description=False)
        )

    builder = _builder("MTEAM")
    _stub_general_sections(builder, monkeypatch)
    meta = _general_meta(tmp_path, uuid="mteam", description=None)
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    assert "MTEAM Description" in asyncio.run(
        builder.general_description_generator(meta)
    )

    builder = _builder("RAILGUNPT")
    _stub_general_sections(builder, monkeypatch)
    meta = _general_meta(tmp_path, uuid="nexus", description=123)
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    result = asyncio.run(builder.general_description_generator(meta))
    assert "Nexus Description" in result and "123" in result

    builder = _builder("TEST")
    _stub_general_sections(builder, monkeypatch)
    builder.get_user_description = AsyncMock(return_value="SAME")  # type: ignore[method-assign]
    meta = _general_meta(tmp_path, uuid="dedup", description="SAME")
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    result = asyncio.run(builder.general_description_generator(meta))
    assert result.count("SAME") == 1
    result = asyncio.run(
        builder.general_description_generator(meta, description=False)
    )
    assert "SAME" in result


def test_general_description_sorted_no_images_language_error_and_section_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _builder(defaults={"multiScreens": "bad"})
    _stub_general_sections(builder, monkeypatch)
    language = AsyncMock(side_effect=RuntimeError("language failed"))
    monkeypatch.setattr(
        description_builder.languages_manager,
        "process_desc_language",
        language,
    )
    meta = _general_meta(
        tmp_path,
        uuid="controls",
        image_list=[],
        sorted_filelist=True,
        audio_languages=[],
        subtitle_languages=[],
        description=None,
        debug=False,
    )
    result = asyncio.run(
        builder.general_description_generator(
            meta,
            custom_header=False,
            languages=True,
            logo=False,
            mediainfo=False,
            bluray=False,
            tv_info=False,
            book=False,
            game=False,
            music=False,
            description=False,
            nfo=False,
            user_description=False,
            menu_screenshots=False,
            tonemapped_header=False,
            screenshots=False,
            audio_spectrogram=False,
            dynamic_hdr_plot=False,
            custom_signature=False,
            ua_signature=False,
        )
    )
    assert result == ""
    language.assert_awaited_once()


def test_saved_pack_image_links_filters_hosts_invalid_urls_counts_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _builder()
    state = tmp_path / "tmp" / "pack"
    state.mkdir(parents=True)
    pack_file = state / "pack_image_links.json"
    data = {
        "keys": {
            "one": {
                "count": 5,
                "images": [
                    {"raw_url": "https://imgbb.com/1.png"},
                    {"raw_url": "https://sub.imgbb.com/2.png"},
                    {"raw_url": "https://bad.invalid/3.png"},
                    {"raw_url": "https://imgbb.com/5.png"},
                    {"raw_url": "not a url"},
                ],
            },
            "two": {
                "count": 1,
                "images": [{"raw_url": "https://bad.invalid/4.png"}],
            },
        },
        "total_count": 6,
    }
    pack_file.write_text(json.dumps(data), encoding="utf-8")
    meta = _general_meta(tmp_path, uuid="pack")
    result = asyncio.run(
        builder._check_saved_pack_image_links(meta, ["imgbb.com"])
    )
    assert result["total_count"] == 3
    assert "two" not in result["keys"]

    result = asyncio.run(builder._check_saved_pack_image_links(meta, []))
    assert result["total_count"] == 6

    original_urlparse = description_builder.urllib.parse.urlparse

    def fail_url(value: str):
        if "bad.invalid" in value:
            raise ValueError("bad url")
        return original_urlparse(value)

    monkeypatch.setattr(description_builder.urllib.parse, "urlparse", fail_url)
    result = asyncio.run(
        builder._check_saved_pack_image_links(meta, ["imgbb.com"])
    )
    assert result["total_count"] == 3
    monkeypatch.setattr(
        description_builder.urllib.parse, "urlparse", original_urlparse
    )

    pack_file.write_text(
        json.dumps(
            {
                "keys": {
                    "one": {
                        "count": 1,
                        "images": [{"raw_url": "https://imgbb.com/1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert (
        asyncio.run(builder._check_saved_pack_image_links(meta, ["imgbb.com"]))
        == {}
    )
    pack_file.write_text("not-json", encoding="utf-8")
    assert (
        asyncio.run(builder._check_saved_pack_image_links(meta, ["imgbb.com"]))
        == {}
    )
    pack_file.unlink()
    builder.common.path_exists = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert (
        asyncio.run(builder._check_saved_pack_image_links(meta, ["imgbb.com"]))
        == {}
    )


def test_book_section_table_list_ptbr_audiobook_and_empty(
    tmp_path: Path,
) -> None:
    all_fields = _meta(
        tmp_path,
        category="BOOK",
        asin="B012345678",
        author="Author",
        book_translator="Translator",
        edition="First Edition",
        isbn="9780306406157",
        narrator="Narrator",
        overview="<p><b>Overview</b></p>",
        publisher="Publisher",
        book_language="English",
        page_count=321,
        book_series="Series",
        book_series_index="2",
        manual_source="WEB",
        source="",
        year=2026,
        audiobook=True,
        audiobook_duration_formatted="10h 20m",
        audiobook_bitrate=128,
        epubmeta_output="EPUB META",
    )
    builder = _builder()
    table = builder._build_book_desc_section(all_fields, underline=True)
    assert (
        "[table]" in table and "EPUB Metadata" in table and "128 kbps" in table
    )
    assert "[b]Overview[/b]" in table

    for tracker in ("TORRENTLEECH", "IMMORTALSEED", "IPTORRENTS", "SPEEDAPP"):
        section = _builder(tracker)._build_book_desc_section(
            all_fields, bullet="*"
        )
        assert "[table]" not in section and "[b]Author:[/b]" in section

    for tracker in ("BJSHARE", "BRASILTRACKER", "AMIGOSSHARE"):
        section = _builder(tracker)._build_book_desc_section(all_fields)
        assert "Autor" in section and "Visão Geral" in section

    empty = _meta(tmp_path, category="BOOK", year=None)
    assert builder._build_book_desc_section(empty) == ""
    overview_only = _meta(
        tmp_path, category="BOOK", overview="Overview", year=None
    )
    assert "Overview" in builder._build_book_desc_section(
        overview_only, table=False, underline=True
    )


def test_game_section_table_simple_software_requirements_languages_and_empty(
    tmp_path: Path,
) -> None:
    files = [
        tmp_path / "setup.exe",
        tmp_path / "package.pkg",
        tmp_path / "image.dmg",
        tmp_path / "ignore.txt",
    ]
    for path in files:
        path.write_bytes(b"x")
    meta = _meta(
        tmp_path,
        category="GAME",
        software=True,
        platform="Windows",
        game_version="1.2.3",
        genres=["Action", "Adventure"],
        developer="Developer",
        publisher="Publisher",
        filelist=[str(path) for path in files],
        tag="-GROUP",
        steam_url="https://store.steampowered.com/app/1",
        localized_overviews={"brazilian": "<p>Resumo</p>"},
        overview="<p>Overview</p>",
        software_notes="<b>Install carefully</b>",
        requirements_minimum="<b>Minimum:</b> CPU &amp; RAM",
        requirements_recommended="<b>Recommended:</b> Better CPU",
        languages={"English": ["Interface", "Audio"], "": []},
    )
    section = _builder()._build_game_desc_section(meta)
    assert "[table]" in section and "DMG, EXE, PKG" in section
    assert (
        "Installation and Usage" in section
        and "System Requirements" in section
    )
    assert "Officially Supported Languages" in section

    simple = _builder("TORRENTLEECH")._build_game_desc_section(
        meta, table=False
    )
    assert "[table]" not in simple and "Minimum" in simple

    ptbr = _builder("BRASILTRACKER")._build_game_desc_section(meta)
    assert "Detalhes Técnicos" in ptbr and "Visão Geral" in ptbr

    non_game = _meta(tmp_path, category="MOVIE")
    assert _builder()._build_game_desc_section(non_game) == ""
    empty_game = _meta(tmp_path, category="GAME")
    assert _builder()._build_game_desc_section(empty_game) == ""


def test_music_section_invalid_shapes_links_tracks_and_non_table(
    tmp_path: Path,
) -> None:
    uuid = "12345678-1234-1234-1234-123456789abc"
    fields = {
        "artists": {"value": ["Artist", "Guest"]},
        "album": {"value": "Album"},
        "year": {"value": 2020},
        "release_year": {"value": 2026},
        "edition": {"value": "Deluxe"},
        "edition_year": {"value": 2026},
        "release_type": {"value": "Album"},
        "media": {"value": "CD"},
        "release_label": {"value": "Label"},
        "release_catalogue_number": {"value": "CAT-1"},
        "genres": {"value": ["Rock", "Pop"]},
        "track_count": {"value": 3},
        "disc_count": {"value": 2},
        "format": {"value": "FLAC"},
        "artist": {"value": "Artist"},
    }
    tracks: list[object] = [
        {
            "track_number": 1,
            "disc_number": 2,
            "title": "One",
            "artist": "Artist",
            "format": "FLAC",
            "codec": "FLAC",
            "bit_depth": 24,
            "sample_rate": 96000,
            "channels": 2,
            "bitrate": 1000000,
        },
        {
            "track_number": "bad",
            "disc_number": "bad",
            "title": "Two",
            "artist": "Guest",
            "format": ["unhashable"],
            "codec": None,
            "bit_depth": "bad",
            "sample_rate": "bad",
            "channels": "bad",
            "bitrate": "bad",
        },
        "ignored",
    ]
    external_ids = {
        "musicbrainz_release": uuid,
        "musicbrainz_release_group": "invalid",
        "discogs_release": "release/123-title",
        "discogs_master": "https://www.discogs.com/master/456-name?x=1",
    }
    meta = _meta(
        tmp_path,
        category="MUSIC",
        artist="Fallback Artist",
        title="Fallback Album",
        year=2020,
        source="WEB",
        music_release={
            "fields": fields,
            "tracks": tracks,
            "external_ids": external_ids,
        },
    )
    table = _builder()._build_music_desc_section(meta)
    assert "[table]" in table and "musicbrainz.org/release" in table
    assert "discogs.com/release/123" in table
    assert "2-01. One" in table and "1-02. Guest - Two" in table

    simple = _builder("TORRENTLEECH")._build_music_desc_section(meta)
    assert "[table]" not in simple and "Tracklist" in simple
    ptbr = _builder("BJSHARE")._build_music_desc_section(meta)
    assert "Detalhes da Música" in ptbr

    for release in (
        None,
        [],
        {},
        {"fields": [], "tracks": "bad", "external_ids": "bad"},
    ):
        invalid = _meta(tmp_path, category="MUSIC", music_release=release)
        assert _builder()._build_music_desc_section(invalid) == ""
    assert (
        _builder()._build_music_desc_section(
            _meta(tmp_path, category="MOVIE", music_release={})
        )
        == ""
    )


def _image(value: str = "1") -> dict[str, str]:
    return {
        "web_url": f"https://web/{value}",
        "raw_url": f"https://raw/{value}",
        "img_url": f"https://thumb/{value}",
    }


def _handle_builder(
    _tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DescriptionBuilder:
    builder = _builder(
        defaults={
            "charLimit": 10000,
            "fileLimit": 2,
            "pack_thumb_size": 300,
            "processLimit": 5,
            "screens_per_row": 2,
        }
    )
    builder.screenshot_header = AsyncMock(return_value="Screen Header")  # type: ignore[method-assign]
    builder._check_saved_pack_image_links = AsyncMock(return_value={})  # type: ignore[method-assign]
    builder.common.save_image_links = AsyncMock()  # type: ignore[method-assign]
    builder.uploadscreens_manager.upload_screens = AsyncMock(
        return_value=([_image("uploaded")], 1)
    )  # type: ignore[method-assign]
    builder.takescreens_manager.sanitize_filename = AsyncMock(
        side_effect=lambda value: str(value).replace(" ", "_")
    )  # type: ignore[method-assign]
    builder.takescreens_manager.screenshots = AsyncMock()  # type: ignore[method-assign]
    builder.parser.parse_mediainfo = lambda _value: {
        "General": {"Format": "Matroska"}
    }  # type: ignore[method-assign]
    builder.parser.format_bbcode = lambda _value: "FORMATTED MEDIAINFO"  # type: ignore[method-assign]
    monkeypatch.setattr(description_builder.asyncio, "sleep", AsyncMock())
    return builder


def test_handle_no_images_game_single_file_and_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _handle_builder(tmp_path, monkeypatch)
    assert (
        asyncio.run(
            builder._handle_discs_and_screenshots(
                _general_meta(tmp_path), [], [], 0
            )
        )
        == ""
    )

    game = _general_meta(
        tmp_path, uuid="handle-game", category="GAME", screens=None
    )
    (tmp_path / "tmp" / game.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            game, [], [_image("1"), _image("2")], 0
        )
    )
    assert "Screen Header" in result and result.count("https://raw") == 2

    movie = _general_meta(
        tmp_path,
        uuid="handle-single",
        category="MOVIE",
        filelist=[str(tmp_path / "single.mkv")],
        comparison=True,
        comparison_groups={
            "0": {
                "name": "Source",
                "urls": [_image("source-1"), _image("source-2")],
            },
            "1": {
                "name": "Encode",
                "urls": [_image("encode-1"), _image("encode-2")],
            },
        },
        screens=1,
    )
    (tmp_path / "tmp" / movie.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(movie, [], [_image("main")], 0)
    )
    assert "[comparison=Source, Encode]" in result and "source-1" in result

    movie.comparison_groups = [
        {"name": "One", "urls": [_image("one")]},
        {"name": "Two", "urls": [_image("two")]},
    ]
    result = asyncio.run(
        builder._handle_discs_and_screenshots(movie, [], [_image("main")], 0)
    )
    assert "[comparison=One, Two]" in result


def test_handle_single_dvd_and_bdmv_saved_uploaded_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _handle_builder(tmp_path, monkeypatch)
    dvd = _general_meta(
        tmp_path,
        uuid="single-dvd",
        discs=[
            {
                "type": "DVD",
                "vob": "/disc/VTS_01_1.VOB",
                "vob_mi": "DVD MEDIAINFO",
            }
        ],
        screens=1,
    )
    (tmp_path / "tmp" / dvd.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(dvd, [], [_image()], 0)
    )
    assert "VTS_01_1.VOB" in result and "DVD MEDIAINFO" in result

    bdmv = _general_meta(
        tmp_path,
        uuid="single-bdmv-saved",
        discs=[
            {
                "type": "BDMV",
                "bdinfo": {"edition": "Main"},
                "bdinfo_1": {"edition": "Director"},
                "summary_1": "Director Summary",
            }
        ],
        screens=1,
    )
    (tmp_path / "tmp" / bdmv.uuid).mkdir(parents=True)
    builder._check_saved_pack_image_links = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "keys": {
                "new_images_playlist_1": {
                    "images": [_image("saved")],
                    "count": 1,
                }
            },
            "total_count": 1,
        }
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            bdmv, ["imgbb.com"], [_image()], 2
        )
    )
    assert "Director Summary" in result and "saved" in result

    upload = _general_meta(
        tmp_path,
        uuid="single-bdmv-upload",
        discs=[
            {
                "type": "BDMV",
                "bdinfo": {"edition": "Main"},
                "bdinfo_1": {"edition": "Extended"},
                "summary_1": "Extended Summary",
            }
        ],
        screens=1,
    )
    (tmp_path / "tmp" / upload.uuid).mkdir(parents=True)
    builder._check_saved_pack_image_links = AsyncMock(return_value={})  # type: ignore[method-assign]
    monkeypatch.setattr(
        description_builder,
        "manifest_files",
        lambda *_args: [Path("PLAYLIST_1-0.png")],
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            upload, ["imgbb.com"], [_image()], 2
        )
    )
    assert "Extended Summary" in result and "uploaded" in result
    builder.common.save_image_links.assert_awaited()

    missing = _general_meta(
        tmp_path,
        uuid="single-bdmv-missing",
        discs=[
            {
                "type": "BDMV",
                "bdinfo": {},
                "bdinfo_1": {"edition": "Missing"},
                "summary_1": "Missing Summary",
            }
        ],
        screens=1,
        skip_imghost_upload=True,
    )
    (tmp_path / "tmp" / missing.uuid).mkdir(parents=True)
    monkeypatch.setattr(
        description_builder, "manifest_files", lambda *_args: []
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(missing, [], [_image()], 2)
    )
    assert "Missing Summary" in result


def test_handle_multiple_discs_saved_and_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _handle_builder(tmp_path, monkeypatch)
    discs = [
        {"type": "BDMV", "name": "Main Disc", "summary": "Main Summary"},
        {
            "type": "DVD",
            "name": "Bonus DVD",
            "vob": "/dvd/VTS_01_1.VOB",
            "vob_mi": "VOB MI",
            "ifo": "/dvd/VTS_01_0.IFO",
            "ifo_mi": "IFO MI",
        },
        {"type": "BDMV", "name": "Second BD", "summary": "Second Summary"},
    ]
    meta = _general_meta(tmp_path, uuid="multi-discs", discs=discs, screens=1)
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    builder._check_saved_pack_image_links = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "keys": {
                "new_images_disc_1": {
                    "images": [_image("dvd-saved")],
                    "count": 1,
                }
            },
            "total_count": 3,
        }
    )
    monkeypatch.setattr(
        description_builder,
        "manifest_files",
        lambda *_args: [Path("disc.png")],
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            meta, ["imgbb.com"], [_image("main")], 2
        )
    )
    assert (
        "Main Disc" in result
        and "Bonus DVD" in result
        and "Second Summary" in result
    )
    assert "dvd-saved" in result and "uploaded" in result

    no_multi = _general_meta(
        tmp_path, uuid="multi-discs-zero", discs=discs, screens=1
    )
    (tmp_path / "tmp" / no_multi.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(no_multi, [], [_image()], 0)
    )
    assert "Main Disc" in result


def test_handle_multiple_files_generation_saved_upload_limits_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = [tmp_path / f"Episode.[{index}].mkv" for index in range(4)]
    for path in files:
        path.write_bytes(b"video")
    builder = _handle_builder(tmp_path, monkeypatch)
    builder.tracker_config.update(
        {"fileLimit": 1, "processLimit": 3, "charLimit": 10000}
    )
    builder._check_saved_pack_image_links = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "keys": {
                "new_images_file_1": {
                    "images": [_image("saved-file")],
                    "count": 1,
                }
            },
            "total_count": 3,
        }
    )
    calls: dict[str, int] = {}

    def manifests(_base: str, _uuid: str, group: str):
        calls[group] = calls.get(group, 0) + 1
        if group == "FILE_2" and calls[group] == 1:
            return []
        return [Path(f"{group}-0.png")]

    monkeypatch.setattr(description_builder, "manifest_files", manifests)
    monkeypatch.setattr(
        description_builder.MediaInfo,
        "parse",
        lambda *_args, **_kwargs: "MEDIAINFO",
    )
    meta = _general_meta(
        tmp_path,
        uuid="multi-files",
        filelist=[str(path) for path in files],
        screens=1,
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            meta, ["imgbb.com"], [_image("main")], 2
        )
    )
    assert "saved-file" in result and "uploaded" in result
    assert "FORMATTED MEDIAINFO" in result and "Other files" in result
    builder.takescreens_manager.screenshots.assert_awaited()

    builder.takescreens_manager.screenshots = AsyncMock(
        side_effect=RuntimeError("capture failed")
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        description_builder, "manifest_files", lambda *_args: []
    )
    error_meta = _general_meta(
        tmp_path,
        uuid="multi-files-error",
        filelist=[str(path) for path in files[:2]],
        screens=1,
    )
    (tmp_path / "tmp" / error_meta.uuid).mkdir(parents=True)
    result = asyncio.run(
        builder._handle_discs_and_screenshots(error_meta, [], [_image()], 2)
    )
    assert "Episode.0" in result


def test_handle_header_error_rows_and_debug_char_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _handle_builder(tmp_path, monkeypatch)
    builder.screenshot_header = AsyncMock(
        side_effect=RuntimeError("header failed")
    )  # type: ignore[method-assign]
    meta = _general_meta(
        tmp_path,
        uuid="header-error",
        filelist=[str(tmp_path / "one.mkv"), str(tmp_path / "two.mkv")],
        screens=2,
        debug=True,
    )
    (tmp_path / "one.mkv").write_bytes(b"x")
    (tmp_path / "two.mkv").write_bytes(b"x")
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    monkeypatch.setattr(
        description_builder,
        "manifest_files",
        lambda *_args: [Path("screen.png")],
    )
    monkeypatch.setattr(
        description_builder.MediaInfo, "parse", lambda *_args, **_kwargs: "MI"
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            meta, [], [_image("1"), _image("2")], 2
        )
    )
    assert "https://raw/1" in result


def test_tracker_specific_formats_all_trackers_and_image_regexes() -> None:
    source = (
        "<p>HTML &amp; text</p>"
        "[user]u[/user][align=left]left[/align][right]right[/right][align=right]ar[/align]"
        "[sup]sup[/sup][sub]sub[/sub][alert]alert[/alert][note]note[/note][hr]"
        "[h1]h1[/h1][h2]h2[/h2][h3]h3[/h3][ul][*] one[/ul][ol][*]two[/ol]"
        "[list][*]item[/list][hide=Named]hide[/hide][spoiler=Named]spoiler[/spoiler]"
        "[center][spoiler=Scene NFO:][code]NFO[/code][/spoiler][/center]"
        "[comparison=A,B][img]one[/img][/comparison]"
        "[url=https://not-imgbox.invalid][img=300]https://raw/one.png[/img][/url]"
        "[url=https://imgbox.com/a][img]https://raw/two.png[/img][/url]"
        "[img=200]https://raw/three.png[/img]"
        "[c]code[/c] • \u2019 \u2013 \u201cquotes\u201d"
    )
    trackers = [
        "BRASILTRACKER",
        "BJSHARE",
        "ANTHELION",
        "DIGITALCORE",
        "FUNFILE",
        "GREATPOSTERWALL",
        "HDSPACE",
        "IPTORRENTS",
        "HDTORRENTS",
        "PTSKIT",
        "SPEEDAPP",
        "TORRENTLEECH",
        "IMMORTALSEED",
        "AITHER",
        "UNKNOWN",
    ]
    for tracker in trackers:
        result = _builder(tracker).tracker_specific_formats(tracker, source)
        assert isinstance(result, str) and result
    hds = _builder("HDSPACE").tracker_specific_formats("HDSPACE", source)
    assert "not-imgbox.invalid" in hds and "imgbox.com" in hds
    assert "\n" in hds
    assert "[" not in _builder("IMMORTALSEED").tracker_specific_formats(
        "IMMORTALSEED", source
    )


def test_screenshot_format_rows_screens_per_row_and_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ("https://web", "https://raw", "https://thumb")
    nexus = (
        "1PTBA",
        "LAJIDUI",
        "LEMONHD",
        "LONGPT",
        "PTCAFE",
        "PTFANS",
        "PTGTK",
        "PTZONE",
        "RAILGUNPT",
        "XINGYUNGEPT",
        "NEXUSPHP",
    )
    for tracker in nexus:
        assert (
            _builder(tracker).format_screenshot(*urls)
            == "[img]https://raw[/img]"
        )
    assert "height=137" in _builder("HDTORRENTS").format_screenshot(*urls)
    assert "max-width: 350px" in _builder("TORRENTLEECH").format_screenshot(
        *urls
    )
    assert 'target="_blank"' in _builder("FUNFILE").format_screenshot(
        *urls, thumb_size=200
    )
    assert (
        _builder("GREATPOSTERWALL")
        .format_screenshot(*urls)
        .startswith("[img]")
    )
    assert (
        _builder("HDSPACE")
        .format_screenshot("https://other", "raw", "thumb")
        .endswith("\n")
    )
    assert (
        _builder("IPTORRENTS")
        .format_screenshot("https://imgbox.com/a", "raw", "thumb")
        .endswith(" ")
    )
    assert "[img=350]" in _builder().format_screenshot("web", "raw", "")

    assert asyncio.run(_builder("TORRENTLEECH").get_screens_per_row()) == 2
    assert (
        asyncio.run(
            _builder(
                "HAWKEUNO",
                tracker_values={"screens_per_row": 4, "thumbnail_size": 400},
            ).get_screens_per_row()
        )
        == 2
    )
    broken = _builder()
    monkeypatch.setattr(
        broken,
        "_get_int_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert asyncio.run(broken.get_screens_per_row()) == 2

    parts: list[str] = []
    assert _builder()._append_screenshot_row_separator(parts, 0, 0) == ""
    assert _builder()._append_screenshot_row_separator(parts, 0, 2) == ""
    assert _builder()._append_screenshot_row_separator(parts, 1, 2) == "\n"
    parts = []
    assert (
        _builder("TORRENTLEECH")._append_screenshot_row_separator(parts, 1, 2)
        == "<br><br>"
    )

    images = [
        _image("1"),
        {"web_url": "", "raw_url": "raw", "img_url": "thumb"},
        _image("3"),
    ]
    meta = _general_meta(
        tmp_path,
        is_disc="DVD",
        tracker_image_collections={"TEST": {"menu_images": images}},
    )
    builder = _builder()
    builder.menu_screenshot_header = AsyncMock(return_value="Menus")  # type: ignore[method-assign]
    builder.get_screens_per_row = AsyncMock(return_value=2)  # type: ignore[method-assign]
    section = asyncio.run(builder.menu_section(meta))
    assert (
        "Menus" in section
        and "https://raw/1" in section
        and "https://raw/3" in section
    )
    assert (
        asyncio.run(builder.menu_section(_general_meta(tmp_path, is_disc="")))
        == ""
    )
    builder.menu_screenshot_header = AsyncMock(side_effect=RuntimeError("bad"))  # type: ignore[method-assign]
    assert asyncio.run(builder.menu_section(meta)) == ""


def test_remaining_small_description_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _builder(
        defaults={
            "screenshot_header": "Screens",
            "disc_menu_header": "Menus",
            "custom_signature": "Sig",
        }
    )
    meta = _general_meta(tmp_path)
    monkeypatch.setattr(
        builder,
        "_get_str_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert asyncio.run(builder.screenshot_header(meta)) == ""
    assert asyncio.run(builder.menu_screenshot_header(meta)) == ""
    assert asyncio.run(builder.get_user_description(meta)) == ""
    assert asyncio.run(builder.get_custom_signature(meta)) == ""

    tagged = _builder(
        tracker_values={"tag_overrides": {"-GROUP": {"header": "Tagged"}}}
    )
    assert tagged._get_tag_override("header", None) is None
    assert tagged._get_tag_override("missing", Meta(tag="-GROUP")) is None

    spectrogram = _builder(defaults={"add_audio_spectrogram": True})
    spectrogram._get_str_config = lambda *_args, **_kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("bad"))  # type: ignore[method-assign]
    assert (
        asyncio.run(
            spectrogram.get_audio_spectrogram_section(
                _general_meta(tmp_path, audio_spectrogram=True)
            )
        )
        == ""
    )

    # Cached short MediaInfo read errors are isolated and regenerated.
    state = tmp_path / "tmp" / "mi-read-error"
    state.mkdir(parents=True)
    short = state / "MEDIAINFO_SHORT.txt"
    short.write_text("cached", encoding="utf-8")
    media_builder = _builder(defaults={"full_mediainfo": False})
    original_read = Path.read_text

    def fail_read(path: Path, *_args: object, **_kwargs: object) -> str:
        if path == short:
            raise OSError("read failed")
        return original_read(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)
    report = {
        "media": {"track": [{"@type": "General", "CompleteName": "Movie.mkv"}]}
    }
    assert asyncio.run(
        media_builder.get_mediainfo_section(
            _general_meta(tmp_path, uuid="mi-read-error", mediainfo=report)
        )
    )


def test_final_uncovered_description_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # File-only description is the first written source.
    source = tmp_path / "only-description.txt"
    source.write_text("file-only", encoding="utf-8")
    result = asyncio.run(
        gen_desc(
            _meta(tmp_path, description_file=str(source)), object(), object()
        )
    )  # type: ignore[arg-type]
    assert result.description == "file-only"

    builder = _builder()
    assert builder._get_tag_override("anything", Meta(tag="---")) is None
    builder.config["DEFAULT"] = []
    assert builder._get_tag_override("anything", Meta(tag="-GROUP")) is None

    # Nonempty but non-renderable MediaInfo reaches the final empty result.
    media = _general_meta(
        tmp_path, uuid="empty-render", mediainfo={"media": {"track": []}}
    )
    assert (
        asyncio.run(
            _builder(defaults={"full_mediainfo": False}).get_mediainfo_section(
                media
            )
        )
        == ""
    )

    # Menu/user-description exception guards.
    menu_builder = _builder(defaults={"disc_menu_header": "Menus"})
    menu_meta = _general_meta(
        tmp_path,
        is_disc="DVD",
        tracker_image_collections={"TEST": {"menu_images": [_image("menu")]}},
    )
    monkeypatch.setattr(
        menu_builder,
        "_get_str_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("menu")),
    )
    assert asyncio.run(menu_builder.menu_screenshot_header(menu_meta)) == ""

    user_meta = _general_meta(tmp_path)
    user_meta.description_file_content = None  # type: ignore[assignment]
    assert asyncio.run(_builder().get_user_description(user_meta)) == ""

    # Explicitly disabled optional sections.
    assert (
        asyncio.run(
            _builder().get_audio_spectrogram_section(
                _general_meta(
                    tmp_path,
                    audio_spectrogram=False,
                    audio_spectrogram_tracks=None,
                )
            )
        )
        == ""
    )
    assert (
        asyncio.run(
            _builder().get_dynamic_hdr_plot_section(
                _general_meta(tmp_path, dynamic_hdr_plot=False)
            )
        )
        == ""
    )

    # Header-size -1 is a supported table style for book details.
    book = _general_meta(tmp_path, category="BOOK", author="Author", year=2024)
    assert "[b]Technical Details[/b]" in _builder()._build_book_desc_section(
        book, header_size=-1
    )

    # The collection boundary may return no screenshots; generator normalizes it.
    generator = _builder()
    monkeypatch.setattr(
        description_builder,
        "get_tracker_image_collection",
        lambda *_args, **_kwargs: None,
    )
    generated = asyncio.run(
        generator.general_description_generator(
            _general_meta(tmp_path, image_list=[]),
            audio_spectrogram=False,
            bluray=False,
            book=False,
            custom_header=False,
            custom_signature=False,
            description=False,
            game=False,
            languages=False,
            logo=False,
            mediainfo=False,
            menu_screenshots=False,
            nfo=False,
            screenshots=False,
            tonemapped_header=False,
            tv_info=False,
            ua_signature=False,
            user_description=False,
            music=False,
            dynamic_hdr_plot=False,
        )
    )
    assert isinstance(generated, str)

    # HDSPACE line-break policy applies when the host name truly does not contain imgbox.
    hds = _builder("HDSPACE").tracker_specific_formats(
        "HDSPACE",
        "[url=https://other.invalid/a][img]https://raw.invalid/a.png[/img][/url]",
    )
    assert hds.endswith("\n")


def test_final_disc_and_capture_description_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _handle_builder(tmp_path, monkeypatch)

    first_dvd = {
        "type": "DVD",
        "name": "Main DVD",
        "vob": "/dvd/VTS_01_1.VOB",
        "vob_mi": "MAIN VOB",
        "ifo": "/dvd/VTS_01_0.IFO",
        "ifo_mi": "MAIN IFO",
    }
    saved_bd = {"type": "BDMV", "name": "Bonus BD", "summary": "BONUS SUMMARY"}
    saved_meta = _general_meta(
        tmp_path,
        uuid="first-dvd-saved-bd",
        discs=[first_dvd, saved_bd],
        screens=1,
    )
    (tmp_path / "tmp" / saved_meta.uuid).mkdir(parents=True)
    builder._check_saved_pack_image_links = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "keys": {
                "new_images_disc_1": {
                    "images": [_image("saved-bd")],
                    "count": 1,
                }
            }
        }
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            saved_meta, [], [_image("main")], 2
        )
    )
    assert (
        "Main DVD" in result
        and "MAIN VOB" in result
        and "BONUS SUMMARY" in result
    )

    second_dvd = {
        "type": "DVD",
        "name": "Second DVD",
        "vob": "/dvd/VTS_02_1.VOB",
        "vob_mi": "SECOND VOB",
        "ifo": "/dvd/VTS_02_0.IFO",
        "ifo_mi": "SECOND IFO",
    }
    missing_meta = _general_meta(
        tmp_path,
        uuid="second-dvd-missing",
        discs=[
            {"type": "BDMV", "name": "Main BD", "summary": "MAIN SUMMARY"},
            second_dvd,
        ],
        screens=1,
        skip_imghost_upload=True,
    )
    (tmp_path / "tmp" / missing_meta.uuid).mkdir(parents=True)
    builder._check_saved_pack_image_links = AsyncMock(return_value={})  # type: ignore[method-assign]
    monkeypatch.setattr(
        description_builder, "manifest_files", lambda *_args: []
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            missing_meta, [], [_image("main")], 2
        )
    )
    assert (
        "Second DVD" in result
        and "SECOND VOB" in result
        and "SECOND IFO" in result
    )

    files = [tmp_path / f"capture-{index}.mkv" for index in range(3)]
    for path in files:
        path.write_bytes(b"video")
    capture_meta = _general_meta(
        tmp_path,
        uuid="capture-error-explicit",
        filelist=[str(path) for path in files],
        screens=1,
        debug=True,
    )
    (tmp_path / "tmp" / capture_meta.uuid).mkdir(parents=True)
    builder.takescreens_manager.screenshots = AsyncMock(
        side_effect=RuntimeError("capture failed")
    )  # type: ignore[method-assign]
    monkeypatch.setattr(
        description_builder, "manifest_files", lambda *_args: []
    )
    monkeypatch.setattr(
        description_builder.MediaInfo, "parse", lambda *_args, **_kwargs: "MI"
    )
    result = asyncio.run(
        builder._handle_discs_and_screenshots(
            capture_meta, [], [_image("main")], 2
        )
    )
    assert isinstance(result, str)
    builder.takescreens_manager.screenshots.assert_awaited()
