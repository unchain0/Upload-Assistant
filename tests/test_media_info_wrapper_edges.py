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


def test_report_binary_resolution_and_existing_ensure_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert (
        media_info.strip_report_by_line(
            "General\nReportBy : MediaInfoLib - v24\nVideo\n"
        )
        == "General\nVideo\n"
    )

    monkeypatch.setattr(
        media_info, "configured_binary", lambda *_args, **_kwargs: "/custom/mi"
    )
    assert media_info.resolve_mediainfo_binary({}) == "/custom/mi"

    monkeypatch.setattr(
        media_info, "configured_binary", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        media_info.MediaInfoBinaryManager,
        "find_managed_binary",
        lambda root: str(root / "mediainfo") if root == tmp_path else None,
    )
    assert media_info.resolve_mediainfo_binary(
        code_dir=tmp_path, state_dir=tmp_path / "state"
    ) == str(tmp_path / "mediainfo")

    monkeypatch.setattr(
        media_info,
        "resolve_mediainfo_binary",
        lambda *_args, **_kwargs: "/existing/mediainfo",
    )
    assert (
        asyncio.run(media_info.ensure_mediainfo_binary({}, state_dir=tmp_path))
        == "/existing/mediainfo"
    )
    assert media_info._binary() == "/existing/mediainfo"


def test_run_mediainfo_timeout_and_failure_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_info, "_binary", lambda: "mediainfo")

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise media_info.subprocess.TimeoutExpired(["mediainfo"], 900)

    monkeypatch.setattr(media_info.subprocess, "run", timeout)
    with pytest.raises(media_info.MediaInfoError, match="timed out"):
        media_info.run_mediainfo("file.mkv")

    failed = media_info.subprocess.CompletedProcess(
        ["mediainfo"],
        3,
        stdout="",
        stderr=" first\n second ",
    )
    assert media_info._mediainfo_failure_summary(failed) == "first second"
    with pytest.raises(media_info.MediaInfoError, match="exit code 3"):
        media_info._mediainfo_stdout(["mediainfo"], failed)

    silent = media_info.subprocess.CompletedProcess(
        ["mediainfo"], 2, stdout="", stderr=""
    )
    assert (
        media_info._mediainfo_failure_summary(silent) == "no diagnostic output"
    )


def test_track_result_and_parse_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    track = media_info.MediaInfoTrack(
        {"@type": "Video", "Duration": "1.5", "OtherValue": 7}
    )
    assert track.track_type == "Video"
    assert track.duration == 1500.0
    assert track.to_data() == {
        "type": "Video",
        "duration": "1.5",
        "other_value": 7,
    }
    assert media_info.MediaInfoTrack({}).track_type is None

    result = media_info.MediaInfoResult(
        {"media": {"track": [{"@type": "Audio"}, "invalid"]}}
    )
    assert len(result.tracks) == 1
    assert result.tracks[0].track_type == "Audio"

    calls: list[dict[str, object]] = []

    def run(
        filename: str | Path,
        *,
        output: str | None = None,
        full: bool = True,
        inform: str | None = None,
    ) -> str:
        calls.append(
            {
                "filename": filename,
                "output": output,
                "full": full,
                "inform": inform,
            }
        )
        if output == "JSON":
            return '{"media":{"track":[{"@type":"General"}]}}'
        return "text-report"

    monkeypatch.setattr(media_info, "run_mediainfo", run)
    assert (
        media_info.MediaInfo.parse("file.mkv", output="STRING")
        == "text-report"
    )
    assert (
        media_info.MediaInfo.parse(
            "file.mkv",
            mediainfo_options={"inform": "General;%Duration%"},
        )
        == "text-report"
    )
    parsed = media_info.MediaInfo.parse("file.mkv")
    assert isinstance(parsed, media_info.MediaInfoResult)
    assert parsed.tracks[0].track_type == "General"
    assert calls[-1]["output"] == "JSON"
