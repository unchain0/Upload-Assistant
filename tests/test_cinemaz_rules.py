from types import SimpleNamespace

from src.integrations.trackers.AVISTAZ.cinemaz import CinemaZ


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
        "video_width": 1920,
        "video_bitrate": 5000,
        "audio_bitrate": 192,
        "mediainfo": {"media": {"track": [{"@type": "Audio", "Format": "AAC", "BitRate": "192000"}]}},
        "origin_country": ["FR"],
        "year": 2020,
        "sd": False,
        "edition": "",
        "webdv": False,
        "debug": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def tracker():
    return CinemaZ({"TRACKERS": {"CINEMAZ": {}}})


def test_sd_content_from_major_english_country_is_allowed():
    meta = make_meta(origin_country=["US"], resolution="480p", video_width=640, video_bitrate=1200, sd=True)

    warnings = tracker().rules(meta)

    assert warnings == ""


def test_vp9_is_an_allowed_video_codec():
    meta = make_meta(video_codec="VP9", video_encode="VP9")

    warnings = tracker().rules(meta)

    assert warnings == ""


def test_low_video_bitrate_is_reported():
    meta = make_meta(video_bitrate=2999)

    warnings = tracker().rules(meta)

    assert "at least 3000 kbit/s" in warnings


def test_raw_remux_and_4k_uploads_require_six_screenshots():
    meta = make_meta(type="REMUX")
    cinema = tracker()
    cinema.upload_url_step2 = "https://cinemaz.to/upload"
    data = {"screenshots[]": ["a", "b", "c", "d", "e"], "task_id": "1", "info_hash": "hash", "rip_type_id": "2", "type_id": "1", "video_quality_id": "3"}

    issue = cinema.check_data(meta, data)

    assert issue == "UPLOAD FAILED: CinemaZ requires at least 6 screenshots for this upload."


def test_invalid_year_is_treated_as_unknown():
    warnings = tracker().rules(make_meta(year="not-a-year"))
    assert "DO NOT upload recent mainstream English content" not in warnings


def test_recent_mainstream_english_content_is_redirected():
    warnings = tracker().rules(make_meta(origin_country=["US"], year=2026))
    assert "PRIVATEHD.to" in warnings


def test_asian_content_is_redirected():
    warnings = tracker().rules(make_meta(origin_country=["JP"]))
    assert "AVISTAZ.to" in warnings


def test_sd_x265_and_invalid_audio_codec_are_rejected():
    meta = make_meta(
        video_codec="HEVC",
        video_encode="x265",
        resolution="480p",
        video_width=640,
        sd=True,
        mediainfo={"media": {"track": [{"@type": "Audio", "Format": "Opus", "BitRate": "192000"}]}},
    )
    warnings = tracker().rules(meta)
    assert "x265/HEVC is not allowed for SD content" in warnings
    assert "Unallowed audio codec(s) detected: Opus" in warnings


def test_check_data_clean_payload_returns_false():
    cinema = tracker()
    cinema.upload_url_step2 = "https://cinemaz.to/upload"
    meta = make_meta(type="WEBDL", debug=False)
    data = {"screenshots[]": ["a", "b", "c"], "task_id": "1", "info_hash": "hash", "rip_type_id": "2", "type_id": "1", "video_quality_id": "3"}
    assert cinema.check_data(meta, data) is False
