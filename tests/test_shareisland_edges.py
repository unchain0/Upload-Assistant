from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.shareisland import ShareIsland

share_module = importlib.import_module(
    "src.integrations.trackers.UNIT3D.shareisland"
)


def _config(*, use_italian_title: bool = True) -> dict[str, Any]:
    return {
        "DEFAULT": {
            "tmdb_api": "test-key",
            "custom_description_header": "HEADER",
            "tonemapped_header": "[b]TONE[/b]",
        },
        "TRACKERS": {
            "SHAREISLAND": {
                "api_key": "api-key",
                "announce_url": "https://tracker.invalid/announce",
                "use_italian_title": use_italian_title,
                "modq": False,
            }
        },
    }


def _tracker(*, use_italian_title: bool = True) -> ShareIsland:
    return ShareIsland(_config(use_italian_title=use_italian_title))


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    root = tmp_path or Path()
    state: dict[str, object] = {
        "base_dir": str(root),
        "uuid": "release",
        "path": str(root / "movie.mkv"),
        "filename": "movie.mkv",
        "filelist": [str(root / "movie.mkv")],
        "name": "Example.Movie.2024.1080p.WEB-DL-GROUP",
        "title": "Example Movie",
        "year": 2024,
        "category": "MOVIE",
        "type": "WEBDL",
        "source": "WEB",
        "service": "AMZN",
        "resolution": "1080p",
        "video_codec": "H.264",
        "video_encode": "H.264",
        "audio": "DDP 5.1",
        "audio_languages": ["Italian"],
        "language_checked": True,
        "imdb_info": {"akas": []},
        "imdb": 123,
        "tmdb": 456,
        "tmdb_logo": "",
        "genres": ["Drama"],
        "keywords": [],
        "edition": "",
        "webdv": False,
        "season": "",
        "episode": "",
        "episode_title": "",
        "part": "",
        "hdr": "",
        "uhd": "",
        "three_d": "",
        "repack": "",
        "tag": "-GROUP",
        "region": "US",
        "distributor": "Criterion",
        "is_disc": "",
        "dvd_size": "DVD9",
        "bdinfo": {},
        "mediainfo": {
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Format": "Matroska",
                        "FileName": "movie",
                    },
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "BitDepth": "8",
                        "BitRate": "5000000",
                        "DisplayAspectRatio": "1.78",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Compression_Mode": "Lossy",
                        "Channels": "6",
                        "BitRate": "640000",
                        "Language": "it",
                    },
                    {"@type": "Text", "Language": "it"},
                ]
            }
        },
        "screens": 2,
        "image_list": [],
        "comparison": False,
        "comparison_groups": {},
        "tonemapped": False,
        "tv_pack": False,
        "unattended": True,
        "unattended_confirm": False,
        "tracker_status": {"SHAREISLAND": {}},
        "ua_signature": "UA",
    }
    state.update(values)
    return Meta(state)


def test_shareisland_language_resolution_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        share_module.pycountry.languages,
        "get",
        lambda **_kwargs: (_ for _ in ()).throw(LookupError("bad")),
    )
    assert ShareIsland._resolved_alpha2("foo") == "foo"


def test_shareisland_audio_language_tag_branches() -> None:
    tracker = _tracker()
    assert (
        tracker._audio_language_tag(
            _meta(audio_languages=["Italian", "English"])
        )
        == "ITA - ENG"
    )
    assert (
        tracker._audio_language_tag(
            _meta(audio_languages=["Italian", "English", "French"])
        )
        == "ITA - MULTI"
    )
    assert (
        tracker._audio_language_tag(
            _meta(audio_languages=["English", "French", "German"])
        )
        == "MULTI"
    )


def test_shareisland_finalized_name_and_group_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    item = _meta(name="Fallback Name", tag="")
    monkeypatch.setattr(
        tracker, "_basename_release_group", lambda _meta: "NoGroup"
    )
    monkeypatch.setattr(
        tracker, "_valid_marker_group", lambda _candidate: False
    )
    assert tracker._extract_clean_release_group(item) == "NoGroup"
    monkeypatch.setattr(
        tracker, "_extract_clean_release_group", lambda _meta: ""
    )
    assert tracker._finalized_name(item, "") == "Fallback Name"
    fresh = _tracker()
    monkeypatch.setattr(fresh, "get_basename", lambda _meta: "")
    assert fresh._basename_release_group(item) == ""


def test_shareisland_mediainfo_extension_and_marker_guard() -> None:
    tracker = _tracker()
    assert tracker._mediainfo_extension(_meta(mediainfo={})) == ""
    assert not tracker._valid_marker_group("bad-marker")


@pytest.mark.asyncio
async def test_shareisland_disc_metadata_validation_and_accessors() -> None:
    tracker = _tracker()
    item = _meta(is_disc="DVD", region="US", distributor="Criterion")
    tracker.common.unit3d_region_ids = AsyncMock(return_value="12")  # type: ignore[method-assign]
    tracker.common.unit3d_distributor_ids = AsyncMock(return_value="34")  # type: ignore[method-assign]
    await tracker._validate_disc_metadata(item)
    assert await tracker.get_region_id(item) == {"region_id": "12"}
    assert await tracker.get_distributor_id(item) == {"distributor_id": "34"}


@pytest.mark.asyncio
async def test_shareisland_required_region_unattended_missing() -> None:
    tracker = _tracker()
    with pytest.raises(ValueError, match="Region required"):
        await tracker._required_region_name(
            _meta(region="", unattended=True, unattended_confirm=False)
        )


def test_shareisland_prompt_region_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    answers = iter(("", " eu "))
    monkeypatch.setattr(
        share_module.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: next(answers),
    )
    assert tracker._prompt_region() == "EU"


@pytest.mark.asyncio
async def test_shareisland_region_distributor_validation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.unit3d_region_ids = AsyncMock(return_value="7")  # type: ignore[method-assign]
    assert await tracker._validated_region_id("EU") == "7"

    monkeypatch.setattr(
        share_module.cli_ui, "ask_string", lambda *_args, **_kwargs: " warner "
    )
    assert (
        tracker._distributor_name(_meta(distributor="", unattended=False))
        == "WARNER"
    )
    assert await tracker._optional_distributor_id("") is None


@pytest.mark.asyncio
async def test_shareisland_optional_distributor_and_session_accessors() -> (
    None
):
    tracker = _tracker()
    tracker.common.unit3d_distributor_ids = AsyncMock(return_value="9")  # type: ignore[method-assign]
    assert await tracker._optional_distributor_id("WARNER") == "9"


def test_shareisland_type_detection_remux_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_has_remux_marker", lambda _meta: True)
    assert (
        tracker._detect_type_from_technical_analysis(_meta(type="ENCODE"))
        == "REMUX"
    )

    monkeypatch.setattr(tracker, "_has_remux_marker", lambda _meta: False)
    assert (
        tracker._detect_type_from_technical_analysis(_meta(type="BRRIP"))
        == "BRRIP"
    )


def test_shareisland_remux_makemkv_branches() -> None:
    tracker = _tracker()
    assert tracker._has_remux_marker(
        _meta(
            name="Movie.VU1080.mkv",
            filename="Movie.VU1080.mkv",
            filelist=["Movie.VU1080.mkv"],
        )
    )
    item = _meta(
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "Encoded_Application": "MakeMKV v1"},
                    {"@type": "Video", "Encoded_Library_Settings": {}},
                ]
            }
        }
    )
    assert tracker._makemkv_without_encoding(item)
    assert tracker._video_has_no_encoding_settings(
        tracker._mediainfo_tracks(item)
    )


def test_shareisland_analyze_encode_fallback_and_specific_helpers() -> None:
    tracker = _tracker()
    assert (
        tracker._analyze_encode_type(
            _meta(type="OTHER", source="OTHER", service="")
        )
        == "OTHER"
    )
    general = {"Encoded_Application": "muxer"}
    video = {"HDR_Format_Profile": "dvhe.05"}
    assert tracker._streaming_dv_type(general, video, "") == "WEBDL"
    assert (
        tracker._service_fingerprint_type(
            "CR", {}, "bitrate=1", "core 142", True
        )
        == "WEBDL"
    )
    assert tracker._crunchyroll_type("CR", "", "core 142", True) == "WEBDL"
    assert (
        tracker._bluray_encode_type({}, {}, ["BLURAY"], False, False) is None
    )
    assert tracker._disc_fallback_type(["BLURAY"], False, False) == "REMUX"


def test_shareisland_aka_title_country_and_language() -> None:
    tracker = _tracker()
    akas = [
        {
            "country": "Other",
            "language": "Italy",
            "attributes": ["x"],
            "title": "Skipped",
        },
        {"country": "Italy", "attributes": [], "title": "Titolo"},
    ]
    assert tracker._aka_title(akas, "country") == "Titolo"


def test_shareisland_language_name_empty_and_unknown() -> None:
    tracker = _tracker()
    assert tracker._get_language_name("") == ""
    assert tracker._get_italian_language_name("") == ""
    assert tracker._get_italian_language_name("zz") == "Zz"


@pytest.mark.asyncio
async def test_shareisland_best_audio_bdinfo_and_mediainfo() -> None:
    tracker = _tracker()
    tracker.audio_manager.get_audio_v2 = AsyncMock(
        return_value=("DDP 5.1-ITA", "", "")
    )  # type: ignore[method-assign]
    bd = _meta(
        bdinfo={
            "audio": [
                {
                    "language": "Italian",
                    "codec": "TrueHD",
                    "channels": "7.1",
                    "bitrate": "3000",
                }
            ]
        }
    )
    assert await tracker._get_best_italian_audio_format(bd) == "DDP 5.1"
    tracker.audio_manager.get_audio_v2.assert_awaited()

    tracker.audio_manager.get_audio_v2 = AsyncMock(
        return_value=("DD 5.1-ITA", "", "")
    )  # type: ignore[method-assign]
    mi = _meta(bdinfo={})
    assert await tracker._get_best_italian_audio_format(mi) == "DD 5.1"


def test_shareisland_non_italian_audio_track() -> None:
    assert not _tracker()._is_italian_audio_track(
        {"@type": "Audio", "Language": "en"}
    )


def test_shareisland_description_title_uses_italian() -> None:
    tracker = _tracker()
    item = _meta(
        imdb_info={
            "akas": [
                {
                    "country": "Italy",
                    "attributes": [],
                    "title": "Titolo Italiano",
                }
            ]
        }
    )
    assert tracker._description_title(item) == "Titolo Italiano"


@pytest.mark.asyncio
async def test_shareisland_tmdb_fetch_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_tmdb_media_payload",
        AsyncMock(side_effect=RuntimeError("offline")),
    )
    assert await tracker._fetch_tmdb_italian(_meta()) == (
        "Riassunto non disponibile.",
        "",
    )


@pytest.mark.asyncio
async def test_shareisland_tmdb_media_and_logo_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()

    class Response:
        status_code = 500

        @staticmethod
        def json() -> dict[str, Any]:
            return {}

    monkeypatch.setattr(
        tracker, "_tmdb_get", AsyncMock(return_value=Response())
    )
    assert await tracker._tmdb_media_payload(_meta()) == {}
    assert await tracker._tmdb_logo_entries(_meta()) == []


def test_shareisland_preferred_logo_languages() -> None:
    logos = [
        {"iso_639_1": "en", "file_path": "/en.png"},
        {"iso_639_1": "it", "file_path": "/it.png"},
    ]
    assert ShareIsland._preferred_logo_path(logos) == "/it.png"
    assert (
        ShareIsland._preferred_logo_path(
            [{"iso_639_1": "en", "file_path": "/en.png"}]
        )
        == "/en.png"
    )


@pytest.mark.asyncio
async def test_shareisland_no_screens() -> None:
    assert (
        await _tracker()._format_screens_italian(_meta(image_list=[]))
        == "[center]Nessuno screenshot disponibile[/center]"
    )


@pytest.mark.asyncio
async def test_shareisland_synthetic_mediainfo_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        tracker,
        "_mediainfo_tracks",
        lambda _meta: (_ for _ in ()).throw(RuntimeError("bad")),
    )
    assert await tracker._get_synthetic_mediainfo(_meta()) is None


def test_shareisland_synthetic_aspect_and_audio_names() -> None:
    tracker = _tracker()
    assert tracker._synthetic_aspect_ratio("1.78") == "16:9"
    assert tracker._synthetic_aspect_ratio("1.33") == "4:3"
    assert tracker._synthetic_aspect_ratio("2.39") == "2.39:1"
    assert (
        tracker._synthetic_audio_name(
            {"Format_Commercial_IfAny": "Dolby Atmos"}, "fallback"
        )
        == "Dolby Atmos"
    )
    assert (
        tracker._synthetic_audio_name({"Title": "Italian Track"}, "fallback")
        == "Italian Track"
    )


def test_shareisland_generic_shoutout_and_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        share_module.random, "choice", lambda values: values[0]
    )
    assert ShareIsland._shoutouts("NoGroup").startswith("SHOUTOUTS : ")
    item = _meta()
    links = ShareIsland._links_section(item, "MOVIE")
    assert "--- LINKS ---" in links


def test_shareisland_italian_language_name_falls_back_when_babel_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LocaleValue:
        @staticmethod
        def get_display_name(_language: str) -> None:
            return None

    monkeypatch.setattr(
        share_module.Locale, "parse", lambda _value: LocaleValue()
    )
    assert _tracker()._get_italian_language_name("en") == "Eng"


def test_shareisland_commentary_audio_is_not_selected() -> None:
    tracker = _tracker()
    assert not tracker._is_italian_audio_track(
        {"@type": "Audio", "Language": "it", "Title": "Director Commentary"}
    )
