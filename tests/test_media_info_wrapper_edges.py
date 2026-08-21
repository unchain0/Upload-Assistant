from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.integrations.media import media_info


def test_resolution_falls_back_to_path_and_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        media_info, "configured_binary", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "find_managed_binary",
        lambda _root: None,
    )
    monkeypatch.setattr(
        media_info.shutil,
        "which",
        lambda name: "/usr/bin/mediainfo" if name == "mediainfo" else None,
    )
    assert (
        media_info.resolve_mediainfo_binary(
            code_dir=tmp_path, state_dir=tmp_path
        )
        == "/usr/bin/mediainfo"
    )

    monkeypatch.setattr(
        media_info, "resolve_mediainfo_binary", lambda *_args, **_kwargs: None
    )

    async def download(root: Path) -> str:
        assert root == tmp_path
        return str(root / "mediainfo")

    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager, "ensure_mediainfo_binary", download
    )
    assert asyncio.run(
        media_info.ensure_mediainfo_binary({}, state_dir=tmp_path)
    ) == str(tmp_path / "mediainfo")


def test_binary_missing_and_track_attribute_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_info, "resolve_mediainfo_binary", lambda: None)
    with pytest.raises(RuntimeError, match="MediaInfo CLI is not installed"):
        media_info._binary()

    track = media_info.MediaInfoTrack({"Duration": "bad", "OtherValue": 7})
    assert track.duration == "bad"
    assert track.other_value == 7
    assert track.missing is None


def test_run_mediainfo_inform_and_output_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "report"
        stderr = ""

    def run(command: list[str], **_kwargs: object) -> Result:
        commands.append(command)
        return Result()

    monkeypatch.setattr(media_info, "_binary", lambda: "mediainfo")
    monkeypatch.setattr(media_info.subprocess, "run", run)
    assert (
        media_info.run_mediainfo("file.mkv", inform="General;%Duration%")
        == "report"
    )
    assert commands[-1] == [
        "mediainfo",
        "--Full",
        "--Inform=General;%Duration%",
        "file.mkv",
    ]
    assert (
        media_info.run_mediainfo("file.mkv", output="JSON", full=False)
        == "report"
    )
    assert commands[-1] == ["mediainfo", "--Output=JSON", "file.mkv"]
