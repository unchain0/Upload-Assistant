from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.integrations.media import video
from src.integrations.media.video import VideoManager


def test_uhd_all_source_and_type_paths() -> None:
    manager = VideoManager()
    assert asyncio.run(manager.get_uhd("WEBDL", {"Source": "Blu-ray", "Other": "Ultra HD"}, "1080p", "movie")) == "UHD"
    assert asyncio.run(manager.get_uhd("WEBDL", {"Source": "Ultra HD Blu-ray"}, "1080p", "movie")) == "UHD"
    assert asyncio.run(manager.get_uhd("WEBDL", {}, "1080p", "Movie.UHD.mkv")) == "UHD"
    assert asyncio.run(manager.get_uhd("DISC", {}, "1080p", "movie")) == ""
    assert asyncio.run(manager.get_uhd("ENCODE", {}, "2160p", "movie")) == "UHD"


def test_hdr_disc_and_file_variants() -> None:
    manager = VideoManager()
    bdinfo = {
        "video": [
            {"hdr_dv": "HDR10 Dolby Vision"},
            {"hdr_dv": "HDR10+"},
        ]
    }
    assert asyncio.run(manager.get_hdr({}, bdinfo)) == "DV HDR10+"

    assert asyncio.run(manager.get_hdr({"media": {"track": []}}, None)) == ""
    base = {"@type": "Video", "colour_primaries": "BT.2020"}
    cases = [
        ({**base, "HDR_Format_Compatibility": "HDR10+"}, "HDR10+"),
        ({**base, "HDR_Format_String": "HDR10"}, "HDR"),
        ({**base, "HDR_Format": "SMPTE ST 2094 App 4"}, "HDR"),
        ({**base, "HDR_Format": "HLG"}, "HLG"),
        ({**base, "transfer_characteristics": "PQ"}, "PQ10"),
        ({**base, "transfer_characteristics_Original": "HLG"}, "HLG"),
        ({**base, "transfer_characteristics_Original": "BT.2020 (10-bit)"}, "WCG"),
        ({**base, "HDR_Format": "Dolby Vision"}, "DV"),
    ]
    for track, expected in cases:
        result = asyncio.run(manager.get_hdr({"media": {"track": [{"@type": "General"}, track]}}, None))
        assert expected in result

    malformed = {"media": {"track": [{"@type": "Video", "colour_primaries": object()}]}}
    assert asyncio.run(manager.get_hdr(malformed, None)) == ""


def test_video_codec_and_encode_matrix() -> None:
    manager = VideoManager()
    assert asyncio.run(manager.get_video_codec({"video": [{"codec": "MPEG-H HEVC Video"}]})) == "HEVC"
    assert asyncio.run(manager.get_video_codec({"video": [{"codec": "Unknown"}]})) == ""

    def mi(format_name: str, **values: object) -> dict[str, object]:
        return {"media": {"track": [{"@type": "General"}, {"Format": format_name, **values}]}}

    cases = [
        (mi("AV1"), "WEBDL", {}, "AV1"),
        (mi("VP9"), "WEBDL", {}, "VP9"),
        (mi("VC-1"), "ENCODE", {}, "VC-1"),
        (mi("AVC"), "ENCODE", {}, "x264"),
        (mi("HEVC"), "WEBRIP", {}, "x265"),
        (mi("MPEG-4 Visual", Encoded_Library_Name="XviD 1.3"), "DVDRIP", {}, "XviD"),
        (mi("MPEG-4 Visual", Encoded_Library_Name="DivX"), "ENCODE", {}, "DivX"),
        (mi("AVC"), "WEBDL", {}, "H.264"),
        (mi("HEVC"), "WEBDL", {}, "H.265"),
        (mi("AVC", Encoded_Library_Settings="settings"), "HDTV", {}, "x264"),
    ]
    for media_info, type_name, bdinfo, expected in cases:
        encode, _codec, _settings, _depth = asyncio.run(manager.get_video_encode(media_info, type_name, bdinfo))
        assert expected in encode

    high10 = mi("AVC", Format_Profile="High 10", BitDepth="10")
    encode, codec, settings, depth = asyncio.run(manager.get_video_encode(high10, "ENCODE", {}))
    assert encode == "Hi10P x264" and codec == "AVC" and not settings and depth == "10"

    mpeg = mi("MPEG Video", Format_Version="2")
    _encode, codec, _settings, _depth = asyncio.run(manager.get_video_encode(mpeg, "WEBDL", {}))
    assert codec == "MPEG-2"

    fallback = {"video": [{"codec": "HEVC", "profile": "Main 10"}]}
    encode, codec, _settings, depth = asyncio.run(manager.get_video_encode({}, "ENCODE", fallback))
    assert encode == "x265" and codec == "HEVC" and depth == "0"


def _cleanup_doubles(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    cleanup = AsyncMock()
    monkeypatch.setattr(video.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(video.cleanup_manager, "reset_terminal", lambda: None)
    return cleanup


def test_get_video_directory_filters_samples_sorting_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = VideoManager()
    root = tmp_path / "release"
    root.mkdir()
    sample = root / "sample.mkv"
    kept_sample = root / "!sample.mkv"
    small = root / "small.mp4"
    large = root / "large.mkv"
    note = root / "note.txt"
    for path, size in ((sample, 2), (kept_sample, 3), (small, 4), (large, 10), (note, 1)):
        path.write_bytes(b"x" * size)

    first, files = asyncio.run(manager.get_video(str(root), "cli"))
    assert str(sample.resolve()) not in files and str(note.resolve()) not in files
    assert first == sorted(files)[0]

    largest, sorted_files = asyncio.run(manager.get_video(str(root), "cli", sorted_filelist=True))
    assert largest == str(large.resolve()) and sorted_files[0] == str(large.resolve())

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ItemProcessingError, match="No Video files found"):
        asyncio.run(manager.get_video(str(empty), "cli"))
    assert asyncio.run(manager.get_video(str(empty), "batch")) == ("", [])

    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "movie.rar").write_bytes(b"rar")
    with pytest.raises(ItemProcessingError, match="archive-only"):
        asyncio.run(manager.get_video(str(archive), "cli"))

    original_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda _path: (_ for _ in ()).throw(OSError("list failed")))
    with pytest.raises(ItemProcessingError, match="No Video"):
        asyncio.run(manager.get_video(str(root), "cli"))
    monkeypatch.setattr(Path, "iterdir", original_iterdir)


def test_get_video_arr_prompts_file_and_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = VideoManager()
    cleanup = _cleanup_doubles(monkeypatch)
    arr_file = tmp_path / "Movie.{tmdb-123}.mkv"
    arr_file.write_bytes(b"video")

    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    assert asyncio.run(manager.get_video(str(arr_file), "cli"))[0] == str(arr_file.resolve())

    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    with pytest.raises(ItemProcessingError, match="rejected"):
        asyncio.run(manager.get_video(str(arr_file), "cli"))
    cleanup.assert_awaited()

    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(ItemProcessingError, match="cancelled"):
        asyncio.run(manager.get_video(str(arr_file), "cli"))

    root = tmp_path / "arrdir"
    root.mkdir()
    (root / "Movie.{imdb-tt1}.mkv").write_bytes(b"video")
    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True)
    assert asyncio.run(manager.get_video(str(root), "cli"))[0].endswith("Movie.{imdb-tt1}.mkv")

    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    with pytest.raises(ItemProcessingError, match="rejected"):
        asyncio.run(manager.get_video(str(root), "cli"))

    monkeypatch.setattr(video.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(ItemProcessingError, match="cancelled"):
        asyncio.run(manager.get_video(str(root), "cli"))


def test_get_resolution_file_and_dvd_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = VideoManager()
    folder = "resolution"
    output = tmp_path / "tmp" / folder
    output.mkdir(parents=True)
    media = {
        "media": {
            "track": [
                {"@type": "General"},
                {"@type": "Video", "Width": "1920", "Height": "1080", "FrameRate": "60", "ScanType": "Progressive"},
            ]
        }
    }
    (output / "MediaInfo.json").write_text(json.dumps(media), encoding="utf-8")
    monkeypatch.setattr(video, "mi_resolution", AsyncMock(return_value="1080p"))
    resolution, hfr = asyncio.run(manager.get_resolution({}, folder, str(tmp_path), Meta(is_disc="")))
    assert resolution == "1080p" and hfr

    dvd_text = "Width : 720\nHeight : 480\nFrame rate : 29.970\nScan type : Interlaced\n"
    dvd = Meta(is_disc="DVD", discs=[{"ifo_mi_json": "bad-json", "vob_mi": dvd_text}])
    resolution, hfr = asyncio.run(manager.get_resolution({}, "Movie.480i", str(tmp_path), dvd))
    assert resolution == "1080p" and not hfr
    args = video.mi_resolution.await_args.args
    assert args[1] == {} and args[3] == "i"

    string_json = Meta(
        is_disc="DVD",
        discs=[{"ifo_mi_json": json.dumps({"media": {"track": [{}, {"Width": "1280", "Height": "720"}]}})}],
    )
    _resolution, hfr = asyncio.run(manager.get_resolution({}, "Movie", str(tmp_path), string_json))
    assert not hfr

    malformed = Meta(
        is_disc="DVD",
        discs=[{"ifo_mi_json": {"media": {"track": [{}, {"Width": "bad", "Height": object(), "FrameRate": "bad"}]}}}],
    )
    _resolution, hfr = asyncio.run(manager.get_resolution({}, "Movie.1080i", str(tmp_path), malformed))
    assert not hfr

    no_rate = Meta(is_disc="DVD", discs=[{"ifo_mi_json": {"media": {"track": [{}, {"Width": "1280", "Height": "720"}]}}}])
    _resolution, hfr = asyncio.run(manager.get_resolution({}, "Movie", str(tmp_path), no_rate))
    assert not hfr


def test_closest_type_3d_sd_duration_and_container(tmp_path: Path) -> None:
    manager = VideoManager()
    assert manager.closest([0, 720, 1080], 721) == 1080
    assert manager.closest([0, 720], 9999) == 0

    meta = Meta(manual_type="REMUX")
    assert asyncio.run(manager.get_type("anything", False, "", meta)) == "REMUX"
    for filename, expected in (
        ("Movie.Remux.mkv", "REMUX"),
        ("Movie WEB DL.mkv", "WEBDL"),
        ("Movie.WEBRIP.mkv", "WEBRIP"),
        ("Movie.HDTV.mkv", "HDTV"),
        ("Movie.DVDRip.mkv", "DVDRIP"),
        ("Movie.mkv", "ENCODE"),
    ):
        assert asyncio.run(manager.get_type(filename, False, "", Meta())) == expected
    assert asyncio.run(manager.get_type("Movie.mkv", False, "BDMV", Meta())) == "DISC"

    assert asyncio.run(manager.is_3d({"video": [{"3d": "MVC"}]})) == "3D"
    assert asyncio.run(manager.is_3d({"video": [{"3d": ""}]})) == ""
    assert asyncio.run(manager.is_3d(None)) == ""
    assert asyncio.run(manager.is_sd("576p")) == 1 and asyncio.run(manager.is_sd("1080p")) == 0

    assert asyncio.run(manager.get_video_duration(Meta(category="BOOK"))) is None
    media = Meta(category="MOVIE", is_disc="", mediainfo={"media": {"track": [{"@type": "General", "Duration": "3661"}]}})
    assert asyncio.run(manager.get_video_duration(media)) == 61
    media.mediainfo["media"]["track"][0]["Duration"] = "bad"
    assert asyncio.run(manager.get_video_duration(media)) is None
    media.mediainfo = {"media": {"track": [{"@type": "Audio"}]}}
    assert asyncio.run(manager.get_video_duration(media)) is None
    disc = Meta(category="MOVIE", is_disc="BDMV", bdinfo={"length": "01:23:45"}, mediainfo={})
    assert asyncio.run(manager.get_video_duration(disc)) == 83
    disc.bdinfo["length"] = "bad"
    assert asyncio.run(manager.get_video_duration(disc)) is None
    disc.bdinfo = {}
    assert asyncio.run(manager.get_video_duration(disc)) is None

    for disc_type, expected in (("BDMV", "m2ts"), ("HDDVD", "evo"), ("DVD", "vob")):
        assert asyncio.run(manager.get_container(Meta(is_disc=disc_type))) == expected
    assert asyncio.run(manager.get_container(Meta(is_disc="", filelist=[]))) == ""
    one = tmp_path / "one.MKV"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"1")
    two.write_bytes(b"22")
    assert asyncio.run(manager.get_container(Meta(is_disc="", filelist=[str(one), str(two)]))) == "mp4"
    assert asyncio.run(manager.get_container(Meta(is_disc="", filelist=[str(tmp_path / "missing")]))) == ""
