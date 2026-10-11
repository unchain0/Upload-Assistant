from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.ulcx import ULCX
from src.services.preparation_helpers import check_pre_release


def make_ulcx_meta(**kwargs) -> Meta:
    default_kwargs = {
        "category": "MOVIE",
        "title": "Test Movie",
        "tmdb_id": 12345,
        "tmdb": 12345,
        "type": "WEBDL",
        "resolution": "1080p",
        "video_height": 1080,
        "video_codec": "H.264",
        "container": "mkv",
        "source": "WEB",
        "is_disc": "",
        "language": "en",
        "original_language": "en",
        "language_checked": True,
        "audio_languages": ["English"],
        "unattended": True,
        "valid_mi_settings": True,
        "image_list": [
            "http://img1.png",
            "http://img2.png",
            "http://img3.png",
        ],
        "keywords": [],
        "genres": [],
        "filelist": ["Test.Movie.1080p.WEB-DL.H.264.mkv"],
        "mediainfo": {
            "media": {
                "track": [
                    {
                        "@type": "Audio",
                        "Format": "AAC",
                        "Channels": "2",
                        "Language": "en",
                    }
                ]
            }
        },
    }
    default_kwargs.update(kwargs)
    meta = Meta()
    for key, value in default_kwargs.items():
        setattr(meta, key, value)
    if "pre_release" not in kwargs:
        meta.pre_release = check_pre_release(meta)
    return meta


def make_ulcx() -> ULCX:
    config = {
        "TRACKERS": {
            "ULCX": {
                "api_key": "fake_key",
                "announce_url": "https://upload.cx/announce/123",
            }
        }
    }
    return ULCX(config)


@pytest.mark.asyncio
async def test_ulcx_valid_upload():
    tracker = make_ulcx()
    meta = make_ulcx_meta()
    assert await tracker.get_additional_checks(meta) is True


@pytest.mark.asyncio
async def test_ulcx_forbidden_content():
    tracker = make_ulcx()

    # Concerts / live performance
    meta_concert = make_ulcx_meta(keywords=["concert", "live performance"])
    assert await tracker.get_additional_checks(meta_concert) is False

    # Adult content
    meta_adult = make_ulcx_meta(adult_media=True)
    assert await tracker.get_additional_checks(meta_adult) is False

    # CAM / DCP / Pre-release
    meta_cam = make_ulcx_meta(type="CAM")
    assert await tracker.get_additional_checks(meta_cam) is False

    meta_dcp = make_ulcx_meta(type="DCP")
    assert await tracker.get_additional_checks(meta_dcp) is False

    meta_screener = make_ulcx_meta(type="SCREENER")
    assert await tracker.get_additional_checks(meta_screener) is False


@pytest.mark.asyncio
async def test_ulcx_file_and_folder_structure():
    tracker = make_ulcx()

    # Non-mkv container for WEBDL
    meta_mp4 = make_ulcx_meta(container="mp4")
    assert await tracker.get_additional_checks(meta_mp4) is False

    # HDTV allowed container (.ts or .mkv)
    meta_hdtv_ts = make_ulcx_meta(type="HDTV", container="ts")
    assert await tracker.get_additional_checks(meta_hdtv_ts) is True

    # DVD disc missing VIDEO_TS
    meta_dvd = make_ulcx_meta(
        is_disc="DVD", filelist=["other_folder/file.vob"]
    )
    assert await tracker.get_additional_checks(meta_dvd) is False


@pytest.mark.asyncio
async def test_ulcx_dvdrip_rejection():
    tracker = make_ulcx()
    meta_dvdrip = make_ulcx_meta(type="DVDRip")
    assert await tracker.get_additional_checks(meta_dvdrip) is False


@pytest.mark.asyncio
async def test_ulcx_encode_rules():
    tracker = make_ulcx()

    # SD encode (height < 720) rejected
    meta_sd = make_ulcx_meta(
        type="ENCODE", resolution="480p", video_height=480
    )
    assert await tracker.get_additional_checks(meta_sd) is False

    # Live-action HEVC encode from 1080p source rejected
    meta_hevc_hd = make_ulcx_meta(
        type="ENCODE",
        video_codec="HEVC",
        resolution="1080p",
        video_height=1080,
        anime=False,
    )
    assert await tracker.get_additional_checks(meta_hevc_hd) is False

    # Live-action HEVC encode from 2160p source allowed
    meta_hevc_uhd = make_ulcx_meta(
        type="ENCODE",
        video_codec="HEVC",
        resolution="2160p",
        video_height=2160,
        uhd="UHD",
        anime=False,
    )
    assert await tracker.get_additional_checks(meta_hevc_uhd) is True

    # Live-action AV1 encode rejected
    meta_av1_live = make_ulcx_meta(
        type="ENCODE", video_codec="AV1", anime=False
    )
    assert await tracker.get_additional_checks(meta_av1_live) is False

    # Animated AV1 encode allowed
    meta_av1_anime = make_ulcx_meta(
        type="ENCODE", video_codec="AV1", anime=True
    )
    assert await tracker.get_additional_checks(meta_av1_anime) is True


@pytest.mark.asyncio
async def test_ulcx_audio_subtitle_mediainfo_rules():
    tracker = make_ulcx()

    # Non-disc LPCM audio rejected
    meta_lpcm = make_ulcx_meta(
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Format": "LPCM", "Channels": "2"},
                ]
            }
        }
    )
    assert await tracker.get_additional_checks(meta_lpcm) is False

    # Non-disc FLAC > 2ch rejected
    meta_flac_51 = make_ulcx_meta(
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Format": "FLAC", "Channels": "6"},
                ]
            }
        }
    )
    assert await tracker.get_additional_checks(meta_flac_51) is False

    # Remux lossless stereo (DTS-HD MA 2.0) must be FLAC 2.0
    meta_remux_dts_stereo = make_ulcx_meta(
        type="REMUX",
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "Audio",
                        "Format": "DTS",
                        "Format_Profile": "MA / Core",
                        "Channels": "2",
                    },
                ]
            }
        },
    )
    assert await tracker.get_additional_checks(meta_remux_dts_stereo) is False

    # TrueHD without AC3 compatibility track rejected
    meta_truehd_no_ac3 = make_ulcx_meta(
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Format": "TrueHD", "Channels": "6"},
                ]
            }
        }
    )
    assert await tracker.get_additional_checks(meta_truehd_no_ac3) is False

    # Default subtitles on English content rejected
    meta_default_sub_english = make_ulcx_meta(
        personalrelease=True,
        language="en",
        original_language="en",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Format": "AAC", "Channels": "2"},
                    {"@type": "Text", "Language": "english", "Default": "Yes"},
                ]
            }
        },
    )
    assert (
        await tracker.get_additional_checks(meta_default_sub_english) is False
    )

    # Default subtitles on non-personal English releases are permitted by the SHOULD rule
    meta_default_sub_non_personal = make_ulcx_meta(
        language="en",
        original_language="en",
        mediainfo={
            "media": {
                "track": [
                    {"@type": "Audio", "Format": "AAC", "Channels": "2"},
                    {"@type": "Text", "Language": "english", "Default": "Yes"},
                ]
            }
        },
    )
    assert (
        await tracker.get_additional_checks(meta_default_sub_non_personal)
        is True
    )


@pytest.mark.asyncio
async def test_ulcx_language_policy_failure(monkeypatch):
    tracker = make_ulcx()
    monkeypatch.setattr(
        tracker.common,
        "check_language_requirements",
        AsyncMock(return_value=False),
    )
    assert await tracker.get_additional_checks(make_ulcx_meta()) is False


@pytest.mark.asyncio
async def test_ulcx_banned_encode_group_rejected():
    tracker = make_ulcx()
    meta = make_ulcx_meta(type="ENCODE", tag="-edge2020", video_codec="H.264")
    assert await tracker.get_additional_checks(meta) is False


def test_ulcx_remux_audio_helpers_cover_mono_multi_and_encode_lossless():
    tracker = make_ulcx()
    assert "lossless mono" in tracker._remux_track_reason(
        {"Format": "PCM", "Channels": "1"}
    )
    assert "multi-channel" in tracker._remux_track_reason(
        {"Format": "FLAC", "Channels": "6"}
    )
    assert (
        tracker._invalid_encode_audio_track(
            {"Format": "FLAC", "Channels": "6"}
        )
        is True
    )
    assert tracker._is_lossless({"Format": "TrueHD"}) is True


def test_ulcx_personal_subtitle_policy_without_default_is_allowed():
    tracker = make_ulcx()
    meta = make_ulcx_meta(
        personalrelease=True, original_language="en", language="en"
    )
    assert (
        tracker._subtitle_policy_passes(
            meta, [{"@type": "Text", "Default": "No"}]
        )
        is True
    )


@pytest.mark.asyncio
async def test_ulcx_hybrid_remux_reaches_success():
    tracker = make_ulcx()
    meta = make_ulcx_meta(
        type="REMUX",
        edition="Hybrid",
        mediainfo={
            "media": {
                "track": [{"@type": "Audio", "Format": "AAC", "Channels": "2"}]
            }
        },
    )
    assert await tracker.get_additional_checks(meta) is True


@pytest.mark.asyncio
async def test_ulcx_adult_description_keeps_non_image_center_block(
    tmp_path, monkeypatch
):
    class Builder:
        def __init__(self, *_args, **_kwargs):
            pass

        async def general_description_generator(self, *_args, **_kwargs):
            return "[center]plain text[/center][center][img]https://img.invalid/a.png[/img][/center]"

    monkeypatch.setattr(
        "src.integrations.trackers.UNIT3D.ulcx.DescriptionBuilder", Builder
    )
    root = tmp_path / "tmp" / "ulcx-desc"
    root.mkdir(parents=True)
    meta = make_ulcx_meta(
        base_dir=str(tmp_path), uuid="ulcx-desc", adult_media=True
    )
    result = await make_ulcx().get_description(meta)
    assert "[center]plain text[/center]" in result["description"]
    assert "[spoiler=Screenshots]" in result["description"]


@pytest.mark.asyncio
async def test_ulcx_name_uses_imdb_identity_aka_and_year():
    tracker = make_ulcx()
    meta = make_ulcx_meta(
        name="Local AKA 2020 1080p WEB-DL",
        title="Local",
        aka="AKA",
        year=2020,
        imdb_info={"title": "Canonical", "aka": "Alternate", "year": "2021"},
        no_aka=False,
        anime=False,
        edition="",
        webdv=False,
    )
    assert await tracker.get_name(meta) == {
        "name": "Canonical AKA Alternate 2021 1080p WEB-DL"
    }
