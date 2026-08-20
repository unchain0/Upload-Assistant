from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.NEXUSPHP.railgunpt import RailgunPT
from tests.test_railgunpt_rules import _movie_meta, _music_meta, _tracker, _tv_meta


def test_railgunpt_contains_marker_skips_empty_marker() -> None:
    assert not RailgunPT._contains_marker("ordinary release", ("!!!",))


def test_railgunpt_music_payload_root_rejects_non_file_audio(tmp_path: Path) -> None:
    root = tmp_path / "album"
    root.mkdir()
    fake_audio = root / "track.flac"
    fake_audio.mkdir()
    meta = _music_meta(path=str(root))
    assert RailgunPT._music_payload_root(meta, {}, [fake_audio]) is None


def test_railgunpt_music_payload_root_rejects_filesystem_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"audio")
    components = {part.casefold() for part in audio.parent.parts if part and part != "/"}
    monkeypatch.setattr(RailgunPT, "_MUSIC_LAYOUT_DIRS", frozenset(components))
    meta = _music_meta(path="/")
    assert RailgunPT._music_payload_root(meta, {}, [audio]) is None


def test_railgunpt_music_payload_root_final_directory_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "album"
    root.mkdir()
    audio = root / "track.flac"
    audio.write_bytes(b"audio")
    real_is_dir = Path.is_dir
    calls = {"root": 0}

    def staged_is_dir(path: Path) -> bool:
        if path == root:
            calls["root"] += 1
            if calls["root"] >= 3:
                return False
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", staged_is_dir)
    meta = _music_meta(path=str(root))
    assert RailgunPT._music_payload_root(meta, {}, [audio]) is None


def test_railgunpt_music_payload_root_declared_root_resolution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "album"
    root.mkdir()
    audio = root / "track.flac"
    audio.write_bytes(b"audio")
    declared = tmp_path / "declared"
    real_resolve = Path.resolve

    def fail_declared(path: Path, *args: object, **kwargs: object) -> Path:
        if path == declared:
            raise OSError("declared root failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_declared)
    meta = _music_meta(path=str(root))
    assert RailgunPT._music_payload_root(meta, {"root": str(declared)}, [audio]) == root


def test_railgunpt_cue_read_error_returns_none(tmp_path: Path) -> None:
    assert RailgunPT._cue_references_audio(tmp_path / "missing.cue", tmp_path, []) is None


def test_railgunpt_cue_accepts_blank_lines(tmp_path: Path) -> None:
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"audio")
    cue = tmp_path / "album.cue"
    cue.write_text('FILE "track.flac" WAVE\n\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n', encoding="utf-8")
    assert RailgunPT._cue_references_audio(cue, tmp_path, [audio]) == {audio.resolve()}


def test_railgunpt_cue_audio_resolution_error_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"audio")
    cue = tmp_path / "album.cue"
    cue.write_text('FILE "track.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n', encoding="utf-8")
    real_resolve = Path.resolve

    def fail_audio(path: Path, *args: object, **kwargs: object) -> Path:
        if path == audio:
            raise OSError("audio resolve failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_audio)
    assert RailgunPT._cue_references_audio(cue, tmp_path, [audio]) is None


def test_railgunpt_cue_rejects_existing_unlisted_audio(tmp_path: Path) -> None:
    listed = tmp_path / "listed.flac"
    referenced = tmp_path / "referenced.flac"
    listed.write_bytes(b"listed")
    referenced.write_bytes(b"referenced")
    cue = tmp_path / "album.cue"
    cue.write_text('FILE "referenced.flac" WAVE\nTRACK 01 AUDIO\nINDEX 01 00:00:00\n', encoding="utf-8")
    assert RailgunPT._cue_references_audio(cue, tmp_path, [listed]) is None


def test_railgunpt_music_cue_required_audio_resolution_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"audio")
    tracker = _tracker()
    monkeypatch.setattr(RailgunPT, "_music_payload_root", classmethod(lambda _cls, *_args: tmp_path))
    real_resolve = Path.resolve

    def fail_audio(path: Path, *args: object, **kwargs: object) -> Path:
        if path == audio:
            raise OSError("required audio failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_audio)
    assert not tracker._music_cue_is_present(_music_meta(path=str(tmp_path)), {}, [audio])


def test_railgunpt_music_rules_require_audio() -> None:
    assert not _tracker()._validate_music_rules(_music_meta(), [Path("cover.jpg")])


def test_railgunpt_music_rules_propagate_audio_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_validate_audio_rules", lambda *_args: False)
    assert not tracker._validate_music_rules(_music_meta(), [Path("track.flac")])


def test_railgunpt_music_pack_requires_five_albums(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_validate_audio_rules", lambda *_args: True)
    meta = _music_meta(
        music_release={
            "tracks": [
                {"format": "FLAC", "album": "One"},
                {"format": "FLAC", "album": "Two"},
            ]
        }
    )
    assert not tracker._validate_music_rules(meta, [Path("one.flac"), Path("two.flac")])


def test_railgunpt_invalid_source_size_uses_zero() -> None:
    assert not asyncio.run(_tracker().get_additional_checks(_movie_meta(source_size="invalid")))


def test_railgunpt_advertising_filename_branch() -> None:
    files = ["Example.Movie.2024.1080p.BluRay.x264-GRP.mkv", "downloaded from other tracker"]
    assert not asyncio.run(_tracker().get_additional_checks(_movie_meta(filelist=files)))


def test_railgunpt_missing_resolution_height_branch() -> None:
    meta = _movie_meta(resolution="OTHER", name="Example Movie 2024 OTHER BluRay x264-GRP")
    assert not asyncio.run(_tracker().get_additional_checks(meta))


def test_railgunpt_tv_show_category_branch() -> None:
    assert _tracker().get_category(_tv_meta(genres=["Reality"])) == 403


@pytest.mark.parametrize(
    ("audio", "expected"),
    [
        ("LPCM 2.0", 4),
        ("MP3 2.0", 6),
        ("AAC 2.0", 7),
        ("APE 2.0", 8),
        ("WAV 2.0", 10),
    ],
)
def test_railgunpt_remaining_audio_codec_mappings(audio: str, expected: int) -> None:
    assert _tracker().get_audio_codec(Meta(audio=audio)) == expected
