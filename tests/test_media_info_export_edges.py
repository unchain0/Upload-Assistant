from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from src.domain_models.processing import NoAudioMediaError
from src.domain_models.release import Meta
from src.integrations.media import media_info_export


def _base(tmp_path: Path, folder: str = "case") -> Path:
    target = tmp_path / "tmp" / folder
    target.mkdir(parents=True, exist_ok=True)
    return target


def _media_payload() -> dict[str, object]:
    return {
        "creatingLibrary": {"name": "MediaInfo"},
        "media": {
            "@ref": "/secret/path/video.mkv",
            "track": [
                {
                    "@type": "General",
                    "UniqueID": "123",
                    "VideoCount": "1",
                    "AudioCount": "1",
                    "Album": "Album",
                    "performer": "Artist",
                    "extra": {"ConformanceErrors": {}},
                },
                {
                    "@type": "Video",
                    "Width": "1920",
                    "Height": "1080",
                    "Encoded_Library_Settings": "settings",
                },
                {
                    "@type": "Audio",
                    "Format": "AAC",
                    "Channels": "2",
                    "Language": "en",
                },
                {"@type": "Text", "Format": "UTF-8", "Language": "en"},
                {"@type": "Menu", "extra": {"00:00:00.000": "Chapter 1"}},
                {"@type": "Other", "ignored": True},
            ],
        },
    }


def _patch_parse(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    text: str = "General\nComplete name : /secret/path/video.mkv\n",
) -> None:
    def parse(_video: str, **kwargs: object) -> str:
        return json.dumps(payload) if kwargs.get("output") == "JSON" else text

    monkeypatch.setattr(media_info_export.MediaInfo, "parse", parse)


def test_declared_resolution_and_validation_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not media_info_export._declared_resolution_is_compatible(
        "unknown", "1920x1080p", 1920, "p"
    )
    assert not media_info_export._declared_resolution_is_compatible(
        "1080p", "bad", "bad", "p"
    )
    assert media_info_export._declared_resolution_is_compatible(
        "1080p", "1920x1080p", 1920, "p"
    )

    with pytest.raises(ValueError, match="cannot be empty"):
        media_info_export.validate_file_path("")
    with pytest.raises(ValueError, match="does not exist"):
        media_info_export.validate_file_path(str(tmp_path / "missing"))

    real_resolve = Path.resolve

    def broken_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path.name == "broken":
            raise OSError("resolve failed")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    with pytest.raises(ValueError, match="Invalid file path"):
        media_info_export.validate_file_path(str(tmp_path / "broken"))

    existing = tmp_path / "existing"
    existing.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "exists", lambda _path: True)
    monkeypatch.setattr(Path, "is_file", lambda _path: False)
    monkeypatch.setattr(Path, "is_dir", lambda _path: False)
    with pytest.raises(ValueError, match="neither a file nor directory"):
        media_info_export.validate_file_path(str(existing))


def test_find_dvd_mediainfo_remaining_platform_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_info_export, "configured_binary", lambda _name: None
    )
    monkeypatch.setattr(
        media_info_export.platform, "system", lambda: "Windows"
    )
    assert media_info_export.find_dvd_mediainfo(tmp_path) is None

    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    lib_dir = tmp_path / "bin" / "MI" / "linux" / "dvd"
    lib_dir.mkdir(parents=True)
    (lib_dir / "libmediainfo.so.0").write_bytes(b"lib")
    (lib_dir / "mediainfo").write_bytes(b"cli")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing")
    result = media_info_export.find_dvd_mediainfo(tmp_path)
    assert result and result["cli"] == lib_dir / "mediainfo"
    assert os.environ["LD_LIBRARY_PATH"].startswith(f"{lib_dir}{os.pathsep}")

    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Darwin")
    assert media_info_export.find_dvd_mediainfo(tmp_path) is None


def test_mi_resolution_unknown_guess_and_width_fallbacks() -> None:
    assert (
        asyncio.run(
            media_info_export.mi_resolution(
                "1111x777p", {"screen_size": "1080p"}, 1111, "p"
            )
        )
        == "1080p"
    )
    assert (
        asyncio.run(
            media_info_export.mi_resolution("1111x777p", {}, 1920, "p")
        )
        == "1080p"
    )
    assert (
        asyncio.run(
            media_info_export.mi_resolution(
                "1111x777p", {"screen_size": "1550p"}, 999, "p"
            )
        )
        == "OTHER"
    )
    assert (
        asyncio.run(
            media_info_export.mi_resolution(
                "1111x777p", {"screen_size": "1550p"}, 2560, "p"
            )
        )
        == "OTHER"
    )
    assert (
        asyncio.run(
            media_info_export.mi_resolution("1111x777p", None, 854, "p")
        )
        == "480p"
    )  # type: ignore[arg-type]


def test_export_standard_filters_every_track_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "standard"
    output = _base(tmp_path, folder)
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    _patch_parse(
        monkeypatch, _media_payload(), f"General\nComplete name : {video}\n"
    )
    cwd = Path.cwd()
    try:
        result = asyncio.run(
            media_info_export.export_info(
                str(video), False, folder, str(tmp_path)
            )
        )
    finally:
        os.chdir(cwd)

    tracks = result["media"]["track"]
    assert [track["@type"] for track in tracks] == [
        "General",
        "Video",
        "Audio",
        "Text",
        "Menu",
    ]
    assert tracks[0]["Album"] == "Album" and tracks[0]["performer"] == "Artist"
    assert tracks[1]["Width"] == "1920"
    assert tracks[2]["Format"] == "AAC"
    assert tracks[3]["Language"] == "en"
    assert tracks[4]["extra"]
    assert str(video) not in (output / "MEDIAINFO.txt").read_text(
        encoding="utf-8"
    )


def test_export_filter_handles_missing_media_and_non_list_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    for folder, payload in (
        ("missing-media", {"creatingLibrary": "x"}),
        ("bad-tracks", {"media": {"@ref": "x", "track": {"bad": True}}}),
    ):
        _base(tmp_path, folder)
        _patch_parse(monkeypatch, payload)
        result = asyncio.run(
            media_info_export.export_info(
                str(video), True, folder, str(tmp_path)
            )
        )
        assert result["media"]["track"] == []


def _completed(
    *, code: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["mediainfo"], code, stdout=stdout, stderr=stderr
    )


def test_export_dvd_specialized_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "dvd-success"
    _base(tmp_path, folder)
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    cli = tmp_path / "mediainfo"
    cli.write_bytes(b"cli")
    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {"cli": cli, "lib": None, "lib_dir": None},
    )
    payload = _media_payload()
    calls = iter(
        (
            _completed(stdout="Specialized text"),
            _completed(stdout=json.dumps(payload)),
        )
    )
    monkeypatch.setattr(
        media_info_export.subprocess,
        "run",
        lambda *_args, **_kwargs: next(calls),
    )
    result = asyncio.run(
        media_info_export.export_info(
            str(video), True, folder, str(tmp_path), is_dvd=True
        )
    )
    assert result["media"]["track"]
    assert (tmp_path / "tmp" / folder / "MEDIAINFO.txt").read_text(
        encoding="utf-8"
    ) == "Specialized text"


def test_export_dvd_no_cli_and_unsupported_platform_use_standard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    payload = _media_payload()
    _patch_parse(monkeypatch, payload)

    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {"cli": None, "lib": None, "lib_dir": None},
    )
    _base(tmp_path, "no-cli")
    assert asyncio.run(
        media_info_export.export_info(
            str(video), True, "no-cli", str(tmp_path), is_dvd=True
        )
    )["media"]["track"]

    monkeypatch.setattr(
        media_info_export, "find_dvd_mediainfo", lambda _base: None
    )
    _base(tmp_path, "no-config")
    assert asyncio.run(
        media_info_export.export_info(
            str(video), True, "no-config", str(tmp_path), is_dvd=True
        )
    )["media"]["track"]

    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Darwin")
    _base(tmp_path, "unsupported")
    assert asyncio.run(
        media_info_export.export_info(
            str(video), True, "unsupported", str(tmp_path), is_dvd=True
        )
    )["media"]["track"]


def test_export_dvd_timeout_falls_back_for_text_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "dvd-timeout"
    _base(tmp_path, folder)
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    cli = tmp_path / "mediainfo"
    cli.write_bytes(b"cli")
    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {"cli": cli, "lib": None, "lib_dir": None},
    )
    monkeypatch.setattr(
        media_info_export.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("mediainfo", 30)
        ),
    )
    _patch_parse(monkeypatch, _media_payload(), "fallback text")
    result = asyncio.run(
        media_info_export.export_info(
            str(video), True, folder, str(tmp_path), is_dvd=True
        )
    )
    assert (
        result["media"]["track"]
        and (tmp_path / "tmp" / folder / "MEDIAINFO.txt").read_text()
        == "fallback text"
    )


def test_export_dvd_path_validation_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "dvd-path"
    _base(tmp_path, folder)
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {
            "cli": tmp_path / "missing-cli",
            "lib": None,
            "lib_dir": None,
        },
    )
    _patch_parse(monkeypatch, _media_payload(), "fallback text")
    result = asyncio.run(
        media_info_export.export_info(
            str(video), True, folder, str(tmp_path), is_dvd=True
        )
    )
    assert result["media"]["track"]


def test_export_dvd_subprocess_and_json_errors_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "dvd-errors"
    _base(tmp_path, folder)
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    cli = tmp_path / "mediainfo"
    cli.write_bytes(b"cli")
    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {"cli": cli, "lib": None, "lib_dir": None},
    )
    calls = iter(
        (
            _completed(code=1, stdout="bad", stderr="text error"),
            _completed(stdout="not-json"),
        )
    )
    monkeypatch.setattr(
        media_info_export.subprocess,
        "run",
        lambda *_args, **_kwargs: next(calls),
    )
    _patch_parse(monkeypatch, _media_payload(), "fallback text")
    result = asyncio.run(
        media_info_export.export_info(
            str(video), True, folder, str(tmp_path), is_dvd=True
        )
    )
    assert result["media"]["track"]


def test_validate_mediainfo_and_conformance_paths() -> None:
    non_mkv = Meta(path="file.mp4", filelist=["file.mp4"], mediainfo={})
    assert media_info_export.validate_mediainfo(non_mkv)

    no_audio = Meta(
        path="file.mkv",
        filelist=["file.mkv"],
        ua_name="UA",
        mediainfo={"media": {"track": [{"@type": "General"}]}},
    )
    with pytest.raises(NoAudioMediaError):
        media_info_export.validate_mediainfo(no_audio)

    meta = Meta(
        path="file.mkv",
        filelist=["file.mkv"],
        mediainfo={
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "UniqueID": "123",
                        "extra": {"ConformanceErrors": {"bad": True}},
                    },
                    {"@type": "Video", "Encoded_Library_Settings": "settings"},
                    {"@type": "Audio"},
                ]
            }
        },
        is_disc="",
    )
    assert media_info_export.validate_mediainfo(meta)
    assert media_info_export.validate_mediainfo(meta, settings=True)
    assert asyncio.run(media_info_export.get_conformance_error(meta))

    meta.mediainfo["media"]["track"][0]["extra"] = {}
    assert not asyncio.run(media_info_export.get_conformance_error(meta))
    meta.is_disc = "BDMV"
    assert not asyncio.run(media_info_export.get_conformance_error(meta))


def test_validate_mediainfo_missing_unique_and_settings() -> None:
    meta = Meta(
        path="file.mkv",
        filelist=["file.mkv"],
        mediainfo={
            "media": {
                "track": [
                    {"@type": "General", "UniqueID": {}},
                    {"@type": "Video", "Encoded_Library_Settings": {}},
                    {"@type": "Audio"},
                ]
            }
        },
    )
    assert not media_info_export.validate_mediainfo(meta)
    assert not media_info_export.validate_mediainfo(meta, settings=True)
    empty = Meta(path="file.mkv", filelist=["file.mkv"], mediainfo={})
    assert not media_info_export.validate_mediainfo(empty)
    assert not asyncio.run(media_info_export.get_conformance_error(empty))


def test_export_dvd_json_called_process_error_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = "dvd-json-error"
    _base(tmp_path, folder)
    video = tmp_path / "dvd.vob"
    video.write_bytes(b"video")
    cli = tmp_path / "mediainfo"
    cli.write_bytes(b"cli")
    monkeypatch.setattr(media_info_export.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        media_info_export,
        "find_dvd_mediainfo",
        lambda _base: {"cli": cli, "lib": None, "lib_dir": None},
    )
    calls = iter(
        (
            _completed(stdout="specialized text"),
            _completed(code=1, stdout="bad-json", stderr="json error"),
        )
    )
    monkeypatch.setattr(
        media_info_export.subprocess,
        "run",
        lambda *_args, **_kwargs: next(calls),
    )
    _patch_parse(monkeypatch, _media_payload(), "fallback text")
    result = asyncio.run(
        media_info_export.export_info(
            str(video), True, folder, str(tmp_path), is_dvd=True
        )
    )
    assert result["media"]["track"]
