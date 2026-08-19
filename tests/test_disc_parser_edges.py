from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.media import disc_parser
from src.integrations.media.disc_parser import DiscParse


class _Process:
    def __init__(
        self,
        *,
        returncode: int | None = 0,
        pid: int | None = 123,
        stdout: bytes = b"stdout",
        stderr: bytes = b"stderr",
    ) -> None:
        self.returncode = returncode
        self.pid = pid
        self.stdout_value = stdout
        self.stderr_value = stderr
        self.killed = False
        self.waited = 0
        self.communicated = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        self.communicated += 1
        return self.stdout_value, self.stderr_value

    async def wait(self) -> int:
        self.waited += 1
        return int(self.returncode or 0)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _ProgressStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class _BDProcess(_Process):
    def __init__(self, chunks: list[bytes], returncode: int = 0) -> None:
        super().__init__(returncode=returncode)
        self.stderr = _ProgressStream(chunks)


class _Progress:
    instances: ClassVar[list[_Progress]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.updates: list[tuple[object, dict[str, object]]] = []
        type(self).instances.append(self)

    def __enter__(self) -> _Progress:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def add_task(self, *_args: object, **_kwargs: object) -> int:
        return 1

    def update(self, task: object, **kwargs: object) -> None:
        self.updates.append((task, kwargs))


def _parser(**defaults: object) -> DiscParse:
    return DiscParse({"DEFAULT": {"use_largest_playlist": False, **defaults}})


def test_process_group_options_and_playlist_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    original_os = disc_parser.os
    monkeypatch.setattr(disc_parser.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(disc_parser, "os", SimpleNamespace(name="nt"))
    assert DiscParse._process_group_options() == {"creationflags": 512}
    monkeypatch.setattr(disc_parser, "os", SimpleNamespace(name="posix"))
    assert DiscParse._process_group_options() == {"start_new_session": True}
    monkeypatch.setattr(disc_parser, "os", original_os)

    parser = _parser()
    assert parser._calculate_playlist_score({}) == 0.0
    assert parser._calculate_playlist_score({"items": []}) == 0.0
    score = parser._calculate_playlist_score(
        {
            "items": [
                {"file": "a.m2ts", "size": 100 * 1024**3},
                {"file": "a.m2ts", "size": 50 * 1024**3},
                {"file": "b.m2ts", "size": 1},
            ],
            "duration": 20000,
            "total_play_items": 0,
        }
    )
    assert 89.9 <= score <= 90.1
    concentrated = parser._calculate_playlist_score({"items": [{"file": "a", "size": 1}], "duration": 1, "total_play_items": 1})
    assert concentrated > 10


def test_terminate_process_tree_every_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        done = _Process(returncode=0)
        await DiscParse._terminate_process_tree(done)  # type: ignore[arg-type]
        assert not done.killed

        original_os = disc_parser.os
        tree = _Process(returncode=0)
        process = _Process(returncode=None, pid=44)
        create = AsyncMock(return_value=tree)
        monkeypatch.setattr(disc_parser, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", create)
        await DiscParse._terminate_process_tree(process)  # type: ignore[arg-type]
        assert process.killed
        create.assert_awaited_once()

        tree = _Process(returncode=None)
        process = _Process(returncode=None, pid=45)
        monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=tree))
        original_wait_for = disc_parser.asyncio.wait_for
        calls = 0

        async def timeout_once(awaitable: Any, **_kwargs: object) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                raise TimeoutError
            return await awaitable

        monkeypatch.setattr(disc_parser.asyncio, "wait_for", timeout_once)
        await DiscParse._terminate_process_tree(process)  # type: ignore[arg-type]
        assert tree.killed and process.killed
        monkeypatch.setattr(disc_parser.asyncio, "wait_for", original_wait_for)

        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            disc_parser,
            "os",
            SimpleNamespace(name="posix", killpg=lambda pid, sig: killed.append((pid, sig))),
        )
        process = _Process(returncode=None, pid=46)
        await DiscParse._terminate_process_tree(process)  # type: ignore[arg-type]
        assert killed == [(46, disc_parser.signal.SIGKILL)]

        process = _Process(returncode=None, pid=None)
        await DiscParse._terminate_process_tree(process)  # type: ignore[arg-type]
        assert process.killed
        monkeypatch.setattr(disc_parser, "os", original_os)

    asyncio.run(exercise())


def test_specialized_mediainfo_success_timeout_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()

    async def exercise() -> None:
        success = _Process(returncode=0, stdout=b"json", stderr=b"")
        monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=success))
        assert await parser._run_specialized_mediainfo("mediainfo", "file", env={"X": "1"}) == (b"json", b"", 0)

        timed = _Process(returncode=None)
        monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=timed))
        terminate = AsyncMock()
        monkeypatch.setattr(parser, "_terminate_process_tree", terminate)
        original_wait_for = disc_parser.asyncio.wait_for
        calls = 0

        async def timeout_first(awaitable: Any, **_kwargs: object) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                raise TimeoutError
            return await awaitable

        monkeypatch.setattr(disc_parser.asyncio, "wait_for", timeout_first)
        with pytest.raises(RuntimeError, match="timed out"):
            await parser._run_specialized_mediainfo("mediainfo", "file")
        terminate.assert_awaited_once_with(timed)

        cancelled = _Process(returncode=None)
        monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=cancelled))
        terminate.reset_mock()
        calls = 0

        async def cancel_first(awaitable: Any, **_kwargs: object) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
                raise asyncio.CancelledError
            return await awaitable

        monkeypatch.setattr(disc_parser.asyncio, "wait_for", cancel_first)
        with pytest.raises(asyncio.CancelledError):
            await parser._run_specialized_mediainfo("mediainfo", "file")
        terminate.assert_awaited_once_with(cancelled)
        monkeypatch.setattr(disc_parser.asyncio, "wait_for", original_wait_for)

    asyncio.run(exercise())


def test_setup_mediainfo_all_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/mediainfo")
    assert parser.setup_mediainfo_for_dvd(None)[0] == "/configured/mediainfo"  # type: ignore[index]

    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(disc_parser.platform, "system", lambda: "Windows")
    windows = tmp_path / "bin" / "MI" / "windows" / "dvd" / "MediaInfo.exe"
    windows.parent.mkdir(parents=True)
    windows.touch()
    assert parser.setup_mediainfo_for_dvd(str(tmp_path))[0] == str(windows)  # type: ignore[index]

    linux_root = tmp_path / "linux-root"
    linux_dir = linux_root / "bin" / "MI" / "linux" / "dvd"
    linux_dir.mkdir(parents=True)
    cli = linux_dir / "mediainfo"
    lib = linux_dir / "libmediainfo.so.0"
    cli.touch()
    lib.touch()
    monkeypatch.setattr(disc_parser.platform, "system", lambda: "Linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/existing")
    result = parser.setup_mediainfo_for_dvd(str(linux_root))
    assert result is not None and result[0] == str(cli)
    assert result[1]["LD_LIBRARY_PATH"] == f"{linux_dir}{os.pathsep}/existing"

    monkeypatch.delenv("LD_LIBRARY_PATH")
    parser = _parser()
    result = parser.setup_mediainfo_for_dvd(str(linux_root))
    assert result is not None and result[1]["LD_LIBRARY_PATH"] == str(linux_dir)

    parser = _parser()
    monkeypatch.setattr(disc_parser.platform, "system", lambda: "Other")
    assert parser.setup_mediainfo_for_dvd(None) is None
    monkeypatch.setattr(disc_parser, "find_dvd_mediainfo", lambda _base: {"cli": "/fallback", "lib": None})
    assert parser.setup_mediainfo_for_dvd(str(tmp_path))[0] == "/fallback"  # type: ignore[index]
    parser.mediainfo_config = {"cli": None}
    assert parser.setup_mediainfo_for_dvd(str(tmp_path)) is None


def test_bdinfo_progress_success_nonzero_missing_stream_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()
    monkeypatch.setattr(disc_parser, "progress_display", _Progress)
    _Progress.instances = []
    progress = b"Stream scan: 50.0% (1 GB / 2 GB, files 1/2, read 10 MB/s, ETA 1s)\r"
    process = _BDProcess([progress[:20], progress[20:], b""], 0)
    monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert asyncio.run(parser._run_bdinfo_with_progress(["bdinfo", "/disc"], "id")) == 0
    assert any(update[1].get("completed") == 100 for update in _Progress.instances[-1].updates)

    process = _BDProcess([progress, b""], 2)
    monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert asyncio.run(parser._run_bdinfo_with_progress(["bdinfo"], "id")) == 2

    process = _Process(returncode=0)
    process.stderr = None  # type: ignore[attr-defined]
    monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError, match="progress output"):
        asyncio.run(parser._run_bdinfo_with_progress(["bdinfo"], "id"))

    process = _BDProcess([b""], 0)
    process.returncode = None
    monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    terminate = AsyncMock()
    monkeypatch.setattr(parser, "_terminate_process_tree", terminate)
    asyncio.run(parser._run_bdinfo_with_progress(["bdinfo"], "id"))
    terminate.assert_awaited_once_with(process)


def test_parse_bdinfo_files_and_summary_variants() -> None:
    parser = _parser()
    files = """
    00001.m2ts 0 00:10:00 1 GB
    00002.m2ts (1) 0 00:05:00 500 MB
    short line
    """
    assert parser.parse_bdinfo_files(files) == [
        {"file": "00001.m2ts", "length": "00:10:00"},
        {"file": "00002.m2ts (1)", "length": "00:05:00"},
    ]

    class BrokenLine(str):
        def strip(self, *_args: object, **_kwargs: object) -> BrokenLine:
            return self

        def split(self, *_args: object, **_kwargs: object) -> list[str]:
            raise RuntimeError("broken line")

    class BrokenText(str):
        def splitlines(self, *_args: object, **_kwargs: object) -> list[str]:
            return [BrokenLine("broken")]

    assert parser.parse_bdinfo_files(BrokenText("broken")) == []

    summary = """
    * Playlist: 00001.MPLS
    Disc Size: 10,737,418,240 bytes
    Length: 01:30:00.000
    Video: HEVC / 25000 kbps / 1080p / 24 fps / 16:9 / Main 10 / 10 bits / HDR10 / BT.2020 / ignored / extras
    Video: MVC / 20000 kbps / Left Eye / 1080p / 23.976 fps / 16:9 / High / 8 bits / SDR / BT.709
    Audio: English / DTS-HD MA / Atmos / 7.1 / 48 kHz / 4000 kbps / 24-bit (core)
    Audio: French / AAC
    Disc Title: Example Disc
    Disc Label: EXAMPLE
    Subtitle: English / 20 kbps
    """
    parsed = parser.parse_bdinfo(summary, files, "/disc")
    assert parsed["playlist"] == "00001"
    assert parsed["size"] == 10.0
    assert parsed["length"] == "01:30:00"
    assert len(parsed["video"]) == 2 and parsed["video"][1]["3d"] == "Left Eye"
    assert parsed["audio"][0]["atmos_why_you_be_like_this"] == "Atmos"
    assert parsed["audio"][1]["codec"] == "AAC"
    assert parsed["title"].strip() == "Example Disc" and parsed["label"].strip() == "EXAMPLE"
    assert parsed["subtitles"] == ["English"]


def test_duration_and_timecode_helpers() -> None:
    parser = _parser()
    assert parser.format_duration("01:02:03:00") == "1 h 2 min"
    assert parser.format_duration("00:00:03:00") == ""
    assert parser.format_duration("bad") == "Unknown duration"
    assert parser.format_duration("aa:bb:cc:dd") == "Unknown duration"
    assert parser.timecode_to_seconds("01:02:03:00") == 3723
    assert parser.timecode_to_seconds("bad") == 0
    assert parser.timecode_to_seconds("aa:bb:cc:dd") == 0


def _dvd_files(root: Path, *, large: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("VTS_01_0.IFO", "VTS_01_1.VOB", "VTS_02_0.IFO", "VTS_02_1.VOB"):
        path = root / name
        if large and name == "VTS_02_1.VOB":
            with path.open("wb") as stream:
                stream.truncate(5 * 1024**3)
        else:
            path.write_bytes(b"dvd")


def test_get_dvdinfo_specialized_success_fallback_and_sizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small = tmp_path / "small-dvd"
    _dvd_files(small)
    large = tmp_path / "large-dvd"
    _dvd_files(large, large=True)
    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: ("dvd-mediainfo", {"ENV": "1"}))

    sequence = iter(
        (
            (json.dumps({"media": {"track": [{}, {"Duration": "100.0"}]}}).encode(), b"", 0),
            (json.dumps({"media": {"track": [{}, {"Duration": "50"}]}}).encode(), b"", 0),
            (b"VOB SPECIAL\r\n", b"", 0),
            (b"", b"ifo failed", 1),
            (json.dumps({"media": {"track": [{}, {"Duration": "100.0"}]}}).encode(), b"", 0),
            (json.dumps({"media": {"track": [{}, {"Duration": "50"}]}}).encode(), b"", 0),
            (b"VOB LARGE\r\n", b"", 0),
            (b"IFO LARGE\r\n", b"", 0),
        )
    )
    monkeypatch.setattr(parser, "_run_specialized_mediainfo", AsyncMock(side_effect=sequence))

    def standard(path: str, *, output: str, full: bool | None = None) -> str:
        del full
        if output == "JSON":
            return json.dumps({"media": {"track": [{}, {"Duration": "75"}]}})
        return f"STANDARD {path}\r\n"

    monkeypatch.setattr(disc_parser.MediaInfo, "parse", standard)
    cwd = Path.cwd()
    try:
        result = asyncio.run(
            parser.get_dvdinfo(
                [
                    {"path": str(small)},
                    {"path": ""},
                    {"path": 7},
                    {"path": str(large)},
                ],
                str(tmp_path),
            )
        )
    finally:
        os.chdir(cwd)
    assert result[0]["main_set"] == ["01_1.VOB"]
    assert result[0]["vob_mi"] == "VOB SPECIAL\n"
    assert result[0]["ifo_mi"] == "STANDARD VTS_01_0.IFO\n"
    assert result[0]["size"] == "DVD5"
    assert result[3]["size"] == "DVD9"


def async_value(value: Any) -> Any:
    async def inner() -> Any:
        return value

    return inner()


def test_get_dvdinfo_standard_errors_invalid_durations_and_outer_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "dvd"
    _dvd_files(root)
    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: None)
    calls: list[tuple[str, str]] = []

    def parse(path: str, *, output: str, full: bool | None = None) -> str:
        del full
        calls.append((path, output))
        if output == "JSON":
            if path.startswith("VTS_01"):
                return json.dumps({"media": {"track": [{}]}})
            return json.dumps({"media": {"track": [{}, {"Duration": "invalid"}]}})
        return f"MI {path}"

    monkeypatch.setattr(disc_parser.MediaInfo, "parse", parse)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_dvdinfo([{"path": str(root)}], str(tmp_path)))
    finally:
        os.chdir(cwd)
    assert result[0]["main_set"] == []
    assert "vob" not in result[0]

    # JSON-specialized failure falls back to standard, while repeated string
    # failures exercise the outer VOB/IFO recovery block.
    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: ("dvd-mi", {}))
    monkeypatch.setattr(
        parser,
        "_run_specialized_mediainfo",
        AsyncMock(side_effect=[RuntimeError("json failed"), RuntimeError("vob failed"), RuntimeError("ifo failed")]),
    )
    string_calls = 0

    def fallback_parse(path: str, *, output: str, full: bool | None = None) -> str:
        nonlocal string_calls
        del full
        if output == "JSON":
            return json.dumps({"media": {"track": [{}, {"Duration": "120"}]}})
        string_calls += 1
        if string_calls <= 1:
            raise RuntimeError("inner fallback failed")
        return f"RECOVERED {path}"

    monkeypatch.setattr(disc_parser.MediaInfo, "parse", fallback_parse)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_dvdinfo([{"path": str(root)}], str(tmp_path)))
    finally:
        os.chdir(cwd)
    assert result[0]["vob_mi"].startswith("RECOVERED")
    assert result[0]["ifo_mi"].startswith("RECOVERED")


def _hddvd_xml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<Playlist xmlns="http://www.dvdforum.org/2005/HDDVDVideo/Playlist">
  <Title titleNumber="1" id="main" description="Main Feature" titleDuration="01:30:00:00" displayName="Main" onEnd="stop" alternativeSDDisplayMode="letterbox">
    <PrimaryAudioVideoClip src="A.MAP" titleTimeBegin="00:00:00:00" titleTimeEnd="01:30:00:00" seamless="true">
      <Audio track="1" streamNumber="1" mediaAttr="audio" description="English" />
      <Subtitle track="1" streamNumber="2" mediaAttr="subtitle" description="French" />
    </PrimaryAudioVideoClip>
    <ChapterList><Chapter displayName="Chapter 1" titleTimeBegin="00:00:00:00" /></ChapterList>
    <TrackNavigationList>
      <AudioTrack track="1" langcode="en:English" description="Main audio" selectable="true" />
      <SubtitleTrack track="1" langcode="fr" selectable="true" />
    </TrackNavigationList>
    <ApplicationSegment src="app.xmu" titleTimeBegin="00:00:00:00" titleTimeEnd="00:01:00:00" sync="true" zOrder="1">
      <ApplicationResource src="asset.png" size="10" priority="1" multiplexed="false" />
    </ApplicationSegment>
  </Title>
  <Title titleNumber="2" id="short" titleDuration="00:05:00:00" />
</Playlist>
""",
        encoding="utf-8",
    )


def test_parse_hddvd_playlist_full_short_invalid_and_root_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()
    playlist = tmp_path / "playlist.xpl"
    _hddvd_xml(playlist)
    result = parser.parse_hddvd_playlist(str(playlist))
    assert len(result) == 1
    title = result[0]
    assert title["id"] == "main"
    assert title["primaryClips"][0]["audioTracks"][0]["track"] == "1"
    assert title["primaryClips"][0]["subtitleTracks"][0]["description"] == "French"
    assert title["chapters"][0]["displayName"] == "Chapter 1"
    assert title["audioTracks"][0]["language"] == "English"
    assert title["subtitleTracks"][0]["language"] == "French"
    assert title["applicationSegments"][0]["resources"][0]["src"] == "asset.png"

    invalid = tmp_path / "invalid.xpl"
    invalid.write_text("<broken>", encoding="utf-8")
    assert parser.parse_hddvd_playlist(str(invalid)) == []
    monkeypatch.setattr(disc_parser.ElementTree, "parse", lambda _path: SimpleNamespace(getroot=lambda: None))
    assert parser.parse_hddvd_playlist(str(playlist)) == []


def _playlist(
    *,
    id_: str,
    srcs: list[str],
    size: int,
    duration: str = "01:30:00:00",
) -> dict[str, Any]:
    return {
        "titleNumber": id_,
        "id": id_,
        "description": f"Playlist {id_}",
        "titleDuration": duration,
        "primaryClips": [{"src": src} for src in srcs],
        "audioTracks": [
            {"track": "1", "language": "English", "langcode": "en", "description": "Main"},
            {"track": "2", "language": "French", "langcode": "fr", "description": "Missing block"},
        ],
        "subtitleTracks": [
            {"track": "1", "language": "French", "langcode": "fr"},
            {"track": "2", "language": "Spanish", "langcode": "es"},
        ],
        "expectedSize": size,
    }


def test_get_hddvd_valid_interactive_and_language_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hddvd"
    adv = root / "ADV_OBJ"
    adv.mkdir(parents=True)
    (adv / "main.xpl").write_text("playlist", encoding="utf-8")
    a = root / "A.EVO"
    b = root / "B.EVO"
    c = root / "C.EVO"
    a.write_bytes(b"a" * 10)
    b.write_bytes(b"b" * 20)
    c.write_bytes(b"c" * 5)
    parser = _parser()
    monkeypatch.setattr(
        parser,
        "parse_hddvd_playlist",
        lambda _path: [
            _playlist(id_="one", srcs=["A.MAP", "B.MAP"], size=30),
            _playlist(id_="two", srcs=["C.MAP"], size=5, duration="01:00:00:00"),
        ],
    )
    answers = iter(("9", "1"))
    monkeypatch.setattr(disc_parser, "prompt_in_thread", lambda *_args, **_kwargs: async_value(next(answers)))
    monkeypatch.setattr(
        disc_parser.MediaInfo,
        "parse",
        lambda *_args, **_kwargs: "General\nFile size : 1 GiB\nDuration : 1 h\n\nAudio #1\nFormat : DTS\nCompression mode : Lossless\n\nText #1\nFormat : PGS",
    )
    meta = Meta(unattended=False, unattended_confirm=False)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(root)}], meta))
    finally:
        os.chdir(cwd)
    assert result[0]["largest_evo"] == str(b.resolve())
    assert "Language                                 : English" in result[0]["evo_mi"]
    assert "Language                                 : French" in result[0]["evo_mi"]
    assert meta.HDDVD_PLAYLIST["id"] == "one"


def test_get_hddvd_largest_unattended_and_fallbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "largest"
    adv = root / "ADV_OBJ"
    adv.mkdir(parents=True)
    (adv / "main.xpl").write_text("playlist", encoding="utf-8")
    a = root / "A.EVO"
    c = root / "C.EVO"
    a.write_bytes(b"a" * 10)
    c.write_bytes(b"c" * 50)
    monkeypatch.setattr(disc_parser.MediaInfo, "parse", lambda path, **_kwargs: f"MI {path}\nDuration : old\nFile size : old")

    for config, meta in (
        ({"DEFAULT": {"use_largest_playlist": True}}, Meta(unattended=False, unattended_confirm=False)),
        ({"DEFAULT": {"use_largest_playlist": False}}, Meta(unattended=True, unattended_confirm=False)),
    ):
        parser = DiscParse(config)
        monkeypatch.setattr(
            parser,
            "parse_hddvd_playlist",
            lambda _path: [
                _playlist(id_="small", srcs=["A.MAP"], size=10),
                _playlist(id_="large", srcs=["C.MAP"], size=50),
            ],
        )
        cwd = Path.cwd()
        try:
            result = asyncio.run(parser.get_hddvd_info([{"path": str(root)}, {"path": ""}, {"path": 1}], meta))
        finally:
            os.chdir(cwd)
        assert result[0]["largest_evo"] == str(c.resolve())

    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "small.EVO").write_bytes(b"1")
    (fallback / "large.EVO").write_bytes(b"12345")
    parser = _parser()
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(fallback)}], Meta()))
    finally:
        os.chdir(cwd)
    assert result[0]["largest_evo"].endswith("large.EVO")

    empty = tmp_path / "empty-hddvd"
    empty.mkdir()
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(empty)}], Meta()))
    finally:
        os.chdir(cwd)
    assert "largest_evo" not in result[0]


class _PlayItem:
    def __init__(self, clip: object, *, intime: object = 0, outtime: object = 45000) -> None:
        self.clip_information_filename = clip
        self.intime = intime
        self.outtime = outtime


class _BrokenClip:
    intime = 0
    outtime = 45000

    @property
    def clip_information_filename(self) -> str:
        raise AttributeError("broken clip")


class _FakeMplsParser:
    playlists: ClassVar[dict[str, object]] = {}

    def __init__(self, stream: Any) -> None:
        self.name = Path(stream.name).name

    def load_movie_playlist(self) -> object:
        value = type(self).playlists[self.name]
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(playlist_start_address=0)

    def load_playlist(self) -> object:
        value = type(self).playlists[self.name]
        if isinstance(value, BaseException):
            raise value
        return value


def _bdinfo_report(label: str, playlist: str = "00001.MPLS") -> str:
    return f"""FILES:
-------------
00001.m2ts 0 00:10:00 1 GB
-------------
CHAPTERS:
1 00:00:00
QUICK SUMMARY:
Disc Title: {label}
Disc Label: {label.upper()}
Playlist: {playlist}
Disc Size: 10,737,418,240 bytes
Length: 01:30:00.000
Video: HEVC / 25000 kbps / 1080p / 24 fps / 16:9 / Main 10 / 10 bits / HDR10 / BT.2020
Audio: English / DTS-HD MA / 5.1 / 48 kHz / 4000 kbps / 24-bit
Subtitle: English / 20 kbps
********************
[code]ignored[code]
Extended {label} Summary
FILES:
00001.m2ts
"""


def _bluray_tree(tmp_path: Path, *, folder: str = "BDMV") -> tuple[Path, Path, Path]:
    root = tmp_path / folder
    playlists = root / "PLAYLIST"
    streams = root / "STREAM"
    playlists.mkdir(parents=True)
    streams.mkdir()
    for name in ("valid1.mpls", "valid2.mpls", "empty.mpls", "bad.mpls", "ignore.txt"):
        (playlists / name).write_bytes(b"mpls")
    (streams / "A.m2ts").write_bytes(b"a" * 100)
    (streams / "B.m2ts").write_bytes(b"b" * 200)
    (streams / "C.m2ts").write_bytes(b"c" * 50)
    return root, playlists, streams


def _install_bluray_playlists(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMplsParser.playlists = {
        "valid1.mpls": SimpleNamespace(
            play_items=[
                _PlayItem("A", outtime=45000 * 3600),
                _PlayItem("A", outtime=45000 * 60),
                _PlayItem("B", outtime=45000 * 1800),
                _PlayItem("MISSING", outtime=45000 * 10),
                _PlayItem(7),
                _PlayItem("", intime=None),
                _BrokenClip(),
            ]
        ),
        "valid2.mpls": SimpleNamespace(
            play_items=[
                _PlayItem("C", outtime=45000 * 3000),
            ]
        ),
        "empty.mpls": SimpleNamespace(play_items=[]),
        "bad.mpls": RuntimeError("invalid mpls"),
    }
    monkeypatch.setattr(disc_parser, "MplsParser", _FakeMplsParser)


def test_get_bdinfo_interactive_all_playlists_reports_and_editions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, _playlists, _streams = _bluray_tree(tmp_path)
    _install_bluray_playlists(monkeypatch)
    parser = _parser()
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/bdinfo")

    answers = iter(("bad", "9", "all", "Director's Cut", ""))

    async def prompt(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    monkeypatch.setattr(disc_parser, "prompt_in_thread", prompt)

    async def scan(command: list[str], _progress_id: str) -> int:
        report = Path(command[command.index("--reportfilename") + 1])
        playlist = command[command.index("--playlist") + 1]
        report.write_text(_bdinfo_report(f"Report {playlist}", playlist), encoding="utf-8")
        return 0

    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    disc: dict[str, Any] = {"type": "BDMV", "path": str(root)}
    meta = Meta(debug=True, unattended=False, unattended_confirm=False)
    discs, primary = asyncio.run(parser.get_bdinfo(meta, [disc], "release", str(tmp_path), []))

    assert meta.discs_missing_certificate == [str(root)]
    assert primary["title"].strip().startswith("Report")
    assert len(discs[0]["playlists"]) == 2
    assert discs[0]["playlists"][0]["edition"] == "Director's Cut"
    assert discs[0]["bdinfo"]["edition"] == "Director's Cut"
    assert "summary_1" in discs[0] and "bdinfo_1" in discs[0]
    assert (tmp_path / "tmp" / "release" / "BD_SUMMARY_00.txt").is_file()
    assert (tmp_path / "tmp" / "release" / "BD_SUMMARY_EXT_00_1.txt").is_file()


def test_get_bdinfo_unattended_unique_durations_and_existing_meta_discs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, playlists, _streams = _bluray_tree(tmp_path, folder="BDMV2")
    (playlists / "valid3.mpls").write_bytes(b"mpls")
    _install_bluray_playlists(monkeypatch)
    _FakeMplsParser.playlists["valid3.mpls"] = SimpleNamespace(play_items=[_PlayItem("C", outtime=45000 * 3000.2)])
    parser = _parser(use_largest_playlist=True)
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/bdinfo")

    async def scan(command: list[str], _progress_id: str) -> int:
        report = Path(command[command.index("--reportfilename") + 1])
        playlist = command[command.index("--playlist") + 1]
        report.write_text(_bdinfo_report("Automatic", playlist), encoding="utf-8")
        return 0

    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    disc: dict[str, Any] = {"type": "BDMV", "path": str(root)}
    meta = Meta(debug=False, unattended=True, unattended_confirm=False)
    discs, primary = asyncio.run(parser.get_bdinfo(meta, [disc], "auto", str(tmp_path), []))
    assert primary["label"].strip() == "AUTOMATIC"
    assert len(discs[0]["all_valid_playlists"]) == 2
    assert discs[0]["all_valid_playlists"][0]["duration"] >= discs[0]["all_valid_playlists"][1]["duration"]

    cached_dir = tmp_path / "tmp" / "cached"
    cached_dir.mkdir(parents=True)
    (cached_dir / "BD_SUMMARY_00.txt").write_text("cached", encoding="utf-8")
    cached = [{"type": "BDMV", "path": str(root), "bdinfo": {"label": "CACHED"}}]
    result, primary = asyncio.run(parser.get_bdinfo(Meta(), [{"type": "BDMV", "path": str(root)}], "cached", str(tmp_path), cached))
    assert result is cached and primary == {"label": "CACHED"}


def test_get_bdinfo_missing_paths_no_valid_scanner_failures_and_safe_empty_return(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()
    missing_playlist = tmp_path / "no-playlists"
    missing_playlist.mkdir()
    no_valid, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(missing_playlist)}],
            "missing",
            str(tmp_path),
            [],
        )
    )
    assert primary == {} and "bdinfo" not in no_valid[0]

    root, _playlists, _streams = _bluray_tree(tmp_path, folder="no-valid")
    _install_bluray_playlists(monkeypatch)
    _FakeMplsParser.playlists["valid1.mpls"] = SimpleNamespace(play_items=[_PlayItem("MISSING")])
    _FakeMplsParser.playlists["valid2.mpls"] = SimpleNamespace(play_items=[_PlayItem("MISSING")])
    result, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "none",
            str(tmp_path),
            [],
        )
    )
    assert primary == {} and "bdinfo" not in result[0]

    # Valid playlist but no configured, bundled, or system scanner.
    root, _playlists, _streams = _bluray_tree(tmp_path, folder="no-scanner")
    _install_bluray_playlists(monkeypatch)
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(disc_parser.platform, "system", lambda: "unknown")
    monkeypatch.setattr(disc_parser.shutil, "which", lambda _name: None)
    result, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "scanner",
            str(tmp_path),
            [],
        )
    )
    assert primary == {} and "bdinfo" not in result[0]

    # Scanner non-zero and scanner exception both remain isolated per playlist.
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/bdinfo")
    parser._run_bdinfo_with_progress = AsyncMock(return_value=2)  # type: ignore[method-assign]
    result, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "nonzero",
            str(tmp_path),
            [],
        )
    )
    assert primary == {}

    parser._run_bdinfo_with_progress = AsyncMock(side_effect=RuntimeError("scan failed"))  # type: ignore[method-assign]
    result, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "error",
            str(tmp_path),
            [],
        )
    )
    assert primary == {}
    assert asyncio.run(parser.get_bdinfo(Meta(), [], "empty", str(tmp_path), [])) == ([], {})


def test_bdinfo_progress_nonmatching_update_and_final_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = _parser()
    monkeypatch.setattr(disc_parser, "progress_display", _Progress)
    _Progress.instances = []
    final = b"Stream scan: 75.0% (3 GB / 4 GB, files 3/4, read 20 MB/s, ETA 2s)"
    process = _BDProcess([b"unrelated status\r", final, b""], 0)
    monkeypatch.setattr(disc_parser.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert asyncio.run(parser._run_bdinfo_with_progress(["bdinfo"], "id")) == 0
    completed = [update[1].get("completed") for update in _Progress.instances[-1].updates]
    assert 75.0 in completed and 100 in completed


def _single_bluray(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str) -> Path:
    root, _playlists, _streams = _bluray_tree(tmp_path, folder=folder)
    _install_bluray_playlists(monkeypatch)
    _FakeMplsParser.playlists["valid1.mpls"] = SimpleNamespace(play_items=[_PlayItem("A"), _PlayItem(""), _BrokenClip()])
    _FakeMplsParser.playlists["valid2.mpls"] = SimpleNamespace(play_items=[_PlayItem("MISSING")])
    return root


def test_get_bdinfo_single_playlist_default_and_numeric_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Single valid playlist auto-selection.
    root = _single_bluray(tmp_path, monkeypatch, "single")
    parser = _parser()
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/bdinfo")

    async def scan(command: list[str], _progress_id: str) -> int:
        report = Path(command[command.index("--reportfilename") + 1])
        report.write_text(_bdinfo_report("Single"), encoding="utf-8")
        return 0

    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=True, unattended=False, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "single",
            str(tmp_path),
            [],
        )
    )
    assert primary["label"].strip() == "SINGLE" and len(discs[0]["playlists"]) == 1

    # Blank input selects the first of multiple playlists.
    root, _playlists, _streams = _bluray_tree(tmp_path, folder="blank")
    _install_bluray_playlists(monkeypatch)
    parser = _parser()
    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    monkeypatch.setattr(disc_parser, "prompt_in_thread", lambda *_args, **_kwargs: async_value(""))
    discs, _primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=False, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "blank",
            str(tmp_path),
            [],
        )
    )
    assert len(discs[0]["playlists"]) == 1

    # Numeric selection covers the valid-index branch.
    root, _playlists, _streams = _bluray_tree(tmp_path, folder="numeric")
    _install_bluray_playlists(monkeypatch)
    parser = _parser()
    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    monkeypatch.setattr(disc_parser, "prompt_in_thread", lambda *_args, **_kwargs: async_value("1"))
    discs, _primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=False, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "numeric",
            str(tmp_path),
            [],
        )
    )
    assert discs[0]["playlists"][0]["file"] == "valid2.mpls"


@pytest.mark.parametrize(
    ("system", "machine", "relative_binary"),
    [
        ("Linux", "x86_64", "bin/bdinfo/linux/amd64/bdinfo"),
        ("Linux", "aarch64", "bin/bdinfo/linux/arm64/bdinfo"),
        ("Linux", "armv7l", "bin/bdinfo/linux/arm/bdinfo"),
        ("Darwin", "arm64", "bin/bdinfo/macos/arm64/bdinfo"),
        ("Darwin", "x86_64", "bin/bdinfo/macos/x86_64/bdinfo"),
        ("Windows", "AMD64", "bin/bdinfo/windows/x86_64/bdinfo.exe"),
    ],
)
def test_get_bdinfo_bundled_binary_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    relative_binary: str,
) -> None:
    folder_id = f"bundle-{system}-{machine}"
    root = _single_bluray(tmp_path, monkeypatch, folder_id)
    binary = tmp_path / relative_binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"binary")
    parser = _parser(use_largest_playlist=True)
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(disc_parser.platform, "system", lambda: system)
    monkeypatch.setattr(disc_parser.platform, "machine", lambda: machine)
    commands: list[list[str]] = []

    async def scan(command: list[str], _progress_id: str) -> int:
        commands.append(command)
        Path(command[command.index("--reportfilename") + 1]).write_text(_bdinfo_report("Bundled"), encoding="utf-8")
        return 0

    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            folder_id,
            str(tmp_path),
            [],
        )
    )
    assert commands[0][0] == str(binary) and primary["label"].strip() == "BUNDLED"


def test_get_bdinfo_system_binary_existing_report_missing_report_and_malformed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _single_bluray(tmp_path, monkeypatch, "system")
    parser = _parser(use_largest_playlist=True)
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(disc_parser.platform, "system", lambda: "Unknown")
    monkeypatch.setattr(disc_parser.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands: list[list[str]] = []

    async def scan(command: list[str], _progress_id: str) -> int:
        commands.append(command)
        Path(command[command.index("--reportfilename") + 1]).write_text(_bdinfo_report("System"), encoding="utf-8")
        return 0

    monkeypatch.setattr(parser, "_run_bdinfo_with_progress", scan)
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "system",
            str(tmp_path),
            [],
        )
    )
    assert commands[0][0] == "bdinfo" and primary["label"].strip() == "SYSTEM"

    # Existing full report bypasses the scanner.
    root = _single_bluray(tmp_path, monkeypatch, "existing-report")
    save = tmp_path / "tmp" / "existing-report"
    save.mkdir(parents=True)
    existing = save / "Disc1_valid1_FULL.txt"
    existing.write_text(_bdinfo_report("Existing"), encoding="utf-8")
    parser = _parser(use_largest_playlist=True)
    parser._run_bdinfo_with_progress = AsyncMock(side_effect=AssertionError("scanner should not run"))  # type: ignore[method-assign]
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "existing-report",
            str(tmp_path),
            [],
        )
    )
    assert primary["label"].strip() == "EXISTING"

    # Successful scanner that creates no report is rejected.
    root = _single_bluray(tmp_path, monkeypatch, "missing-report")
    parser = _parser(use_largest_playlist=True)
    monkeypatch.setattr(disc_parser, "configured_binary", lambda *_args, **_kwargs: "/configured/bdinfo")
    parser._run_bdinfo_with_progress = AsyncMock(return_value=0)  # type: ignore[method-assign]
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "missing-report",
            str(tmp_path),
            [],
        )
    )
    assert primary == {}

    # Existing path can disappear before parsing; malformed content is logged
    # and terminates rather than looping forever.
    root = _single_bluray(tmp_path, monkeypatch, "disappearing")
    save = tmp_path / "tmp" / "disappearing"
    save.mkdir(parents=True)
    report = save / "Disc1_valid1_FULL.txt"
    report.write_text("malformed report", encoding="utf-8")
    parser = _parser(use_largest_playlist=True)
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "disappearing",
            str(tmp_path),
            [],
        )
    )
    assert primary == {}

    report.write_text(_bdinfo_report("Transient"), encoding="utf-8")
    original_is_file = Path.is_file

    def missing_at_parse(path: Path) -> bool:
        if path == report:
            return False
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", missing_at_parse)
    _discs, primary = asyncio.run(
        parser.get_bdinfo(
            Meta(debug=False, unattended=True, unattended_confirm=False),
            [{"type": "BDMV", "path": str(root)}],
            "disappearing",
            str(tmp_path),
            [],
        )
    )
    assert primary == {}


def test_parse_bdinfo_short_track_padding() -> None:
    parser = _parser()
    parsed = parser.parse_bdinfo(
        "Video: HEVC / 1000 kbps\nAudio: English / AAC",
        "00001.m2ts 0 00:01:00 1 MB",
        "/disc",
    )
    assert parsed["video"][0]["bit_depth"] == ""
    assert parsed["audio"][0]["bit_depth"] == ""


def test_get_dvdinfo_remaining_specialized_and_standard_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "dvd-remaining"
    root.mkdir()
    for name in ("VTS_01_0.IFO", "VTS_01_1.VOB"):
        (root / name).write_bytes(b"dvd")
    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: ("dvd-mi", {}))
    sequence = iter(
        (
            (b"", b"json stderr", 1),
            (b"", b"vob stderr", 1),
            RuntimeError("ifo specialized failed"),
        )
    )

    async def specialized(*_args: object, **_kwargs: object) -> tuple[bytes, bytes, int]:
        value = next(sequence)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(parser, "_run_specialized_mediainfo", specialized)

    def standard(path: str, *, output: str, full: bool | None = None) -> str:
        del full
        if output == "JSON":
            return json.dumps({"media": {"track": [{}, {"Duration": "120"}]}})
        return f"STANDARD {path}\r\n"

    monkeypatch.setattr(disc_parser.MediaInfo, "parse", standard)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_dvdinfo([{"path": str(root)}], str(tmp_path)))
    finally:
        os.chdir(cwd)
    assert result[0]["vob_mi"] == "STANDARD VTS_01_1.VOB\n"
    assert result[0]["ifo_mi"] == "STANDARD VTS_01_0.IFO\n"

    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: None)
    monkeypatch.setattr(disc_parser.MediaInfo, "parse", standard)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_dvdinfo([{"path": str(root)}], str(tmp_path)))
    finally:
        os.chdir(cwd)
    assert result[0]["vob_mi"].startswith("STANDARD") and result[0]["ifo_mi"].startswith("STANDARD")

    parser = _parser()
    monkeypatch.setattr(parser, "setup_mediainfo_for_dvd", lambda _base: None)

    def always_bad(path: str, *, output: str, full: bool | None = None) -> str:
        del path, full
        if output == "JSON":
            raise RuntimeError("json broken")
        return "unused"

    monkeypatch.setattr(disc_parser.MediaInfo, "parse", always_bad)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_dvdinfo([{"path": str(root)}], str(tmp_path)))
    finally:
        os.chdir(cwd)
    assert result[0]["main_set"] == []


def test_get_hddvd_invalid_playlists_and_transient_evo_disappearance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "hddvd-invalid"
    adv = root / "ADV_OBJ"
    adv.mkdir(parents=True)
    (adv / "main.xpl").write_text("playlist", encoding="utf-8")
    parser = _parser(use_largest_playlist=True)
    monkeypatch.setattr(parser, "parse_hddvd_playlist", lambda _path: [_playlist(id_="missing", srcs=["MISSING.MAP"], size=0)])
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(root)}], Meta()))
    finally:
        os.chdir(cwd)
    assert "largest_evo" not in result[0]

    target = root / "A.EVO"
    target.write_bytes(b"video")
    monkeypatch.setattr(parser, "parse_hddvd_playlist", lambda _path: [_playlist(id_="race", srcs=["A.MAP"], size=5)])
    monkeypatch.setattr(disc_parser.MediaInfo, "parse", lambda path, **_kwargs: f"MI {path}")
    original_exists = Path.exists
    calls = 0

    def transient_exists(path: Path) -> bool:
        nonlocal calls
        if path == target:
            calls += 1
            if calls == 2:
                return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", transient_exists)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(root)}], Meta()))
    finally:
        os.chdir(cwd)
    assert result[0]["largest_evo"].endswith("A.EVO")

    calls = 0

    def disappears_later(path: Path) -> bool:
        nonlocal calls
        if path == target:
            calls += 1
            if calls == 4:
                return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", disappears_later)
    cwd = Path.cwd()
    try:
        result = asyncio.run(parser.get_hddvd_info([{"path": str(root)}], Meta()))
    finally:
        os.chdir(cwd)
    assert result[0]["largest_evo"].endswith("A.EVO")
