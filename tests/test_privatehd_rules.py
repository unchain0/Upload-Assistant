from types import SimpleNamespace

from src.integrations.trackers.AVISTAZ.privatehd import PrivateHD


def make_meta(**overrides):
    values = {
        "category": "MOVIE",
        "anime": False,
        "is_disc": "",
        "video_codec": "H.264",
        "video_encode": "H.264",
        "type": "WEBDL",
        "source": "WEB",
        "container": "mkv",
        "resolution": "1080p",
        "bit_depth": "8",
        "video_bitrate": 5000,
        "mediainfo": {"media": {"track": [{"@type": "Video", "BitRate": "5000000"}, {"@type": "Audio", "Format": "AAC", "Language": "en"}]}},
        "original_language": "en",
        "origin_country": ["US"],
        "year": 2020,
        "tag": "GROUP",
        "sd": False,
        "name": "Example 2020 1080p WEB-DL H.264-GROUP",
        "bloated": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def tracker():
    return PrivateHD({"TRACKERS": {"PRIVATEHD": {}}})


def test_hdtv_transport_stream_is_allowed():
    meta = make_meta(type="HDTV", source="HDTV", container="ts")

    warnings = tracker().rules(meta)

    assert warnings == ""


def test_eac3_audio_is_allowed_when_format_commercial_name_is_missing():
    meta = make_meta(mediainfo={"media": {"track": [{"@type": "Video", "BitRate": "5000000"}, {"@type": "Audio", "Format": "E-AC-3", "Language": "en"}]}})

    warnings = tracker().rules(meta)

    assert warnings == ""


def test_crf_above_twenty_is_reported():
    meta = make_meta(
        type="ENCODE",
        source="BluRay",
        video_encode="x264",
        video_bitrate=6000,
        mediainfo={
            "media": {
                "track": [{"@type": "Video", "BitRate": "6000000", "Encoded_Library_Settings": "cabac=1 / crf=21.5"}, {"@type": "Audio", "Format": "AAC", "Language": "en"}]
            }
        },
    )

    warnings = tracker().rules(meta)

    assert "CRF 21.5 exceeds" in warnings


def test_invalid_year_is_ignored_without_crashing():
    warnings = tracker().rules(make_meta(year="not-a-year"))
    assert "50+ years old" not in warnings


def test_cinemaz_and_avistaz_region_redirects():
    assert "CINEMAZ.to" in tracker().rules(make_meta(origin_country=["FR"]))
    asia_warning = tracker().rules(make_meta(origin_country=["JP"]))
    assert "Avistaz.to" in asia_warning
    assert "JP" in asia_warning


def test_non_web_evo_and_uhd_h264_are_rejected():
    warnings = tracker().rules(
        make_meta(
            tag="EVO",
            source="BluRay",
            type="ENCODE",
            resolution="2160p",
            video_codec="H.264",
            video_encode="x264",
            bit_depth="8",
        )
    )
    assert "non-web EVO" in warnings
    assert "H.264/x264 only allowed for 1080p and below" in warnings


def test_truehd_atmos_requires_compatibility_track():
    warnings = tracker().rules(
        make_meta(
            original_language="en",
            mediainfo={
                "media": {
                    "track": [
                        {"@type": "Video", "BitRate": "5000000"},
                        {"@type": "Audio", "Format_Commercial_IfAny": "Dolby TrueHD Atmos", "Language": "en"},
                    ]
                }
            },
        )
    )
    assert "no AC-3 (Dolby Digital) compatibility track" in warnings


def test_blank_forbidden_and_unknown_audio_codecs_cover_validation_paths():
    warnings = tracker().rules(
        make_meta(
            mediainfo={
                "media": {
                    "track": [
                        {"@type": "Video", "BitRate": "5000000"},
                        {"@type": "Audio", "Format": "", "Language": "en"},
                        {"@type": "Audio", "Format": "LPCM", "Language": "en"},
                        {"@type": "Audio", "Format": "Opus", "Language": "en"},
                    ]
                }
            },
        )
    )
    assert "Unallowed audio codec(s) detected: LPCM, Opus" in warnings


def test_sub_720p_and_bloated_audio_are_rejected():
    warnings = tracker().rules(make_meta(resolution="480p", bloated=True))
    assert "Video must be at least 720p" in warnings
    assert "Audio dubs are never preferred" in warnings


def test_unknown_rip_type_display_name_is_empty():
    assert tracker().get_rip_type(make_meta(type="UNKNOWN"), display_name=True) == ""
