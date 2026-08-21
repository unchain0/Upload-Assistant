from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain_models.release import Meta
from src.services import podcast_preparation


def test_source_files_rejects_symlink_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        podcast_preparation, "_has_symlink_component", lambda _path: True
    )
    with pytest.raises(ValueError, match="symbolic links"):
        podcast_preparation._source_files(tmp_path)


def test_archive_signature_handles_tar_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "plain.bin"
    path.write_bytes(b"not an archive")
    monkeypatch.setattr(
        podcast_preparation.zipfile, "is_zipfile", lambda _path: False
    )

    def fail(_path: Path) -> bool:
        raise OSError("bad archive")

    monkeypatch.setattr(podcast_preparation.tarfile, "is_tarfile", fail)
    assert podcast_preparation._has_archive_signature(path) is False


@pytest.mark.parametrize(
    ("tracks", "expected"),
    [
        (
            [SimpleNamespace(track_type="Video", internet_media_type=None)],
            "video",
        ),
        (
            [SimpleNamespace(track_type="Audio", internet_media_type=None)],
            "audio",
        ),
        (
            [
                SimpleNamespace(
                    track_type="General", internet_media_type="video/mp4"
                )
            ],
            "video",
        ),
        (
            [
                SimpleNamespace(
                    track_type="General", internet_media_type="audio/mpeg"
                )
            ],
            "audio",
        ),
        (
            [
                SimpleNamespace(
                    track_type="General",
                    internet_media_type="application/octet-stream",
                )
            ],
            None,
        ),
    ],
)
def test_detected_media_kind_tracks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tracks,
    expected: str | None,
) -> None:
    monkeypatch.setattr(
        podcast_preparation.MediaInfo,
        "parse",
        lambda _path: SimpleNamespace(tracks=tracks),
    )
    assert (
        podcast_preparation._detected_media_kind(tmp_path / "media")
        == expected
    )


def test_detected_media_kind_handles_parser_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        podcast_preparation.MediaInfo,
        "parse",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid")),
    )
    assert podcast_preparation._detected_media_kind(tmp_path / "media") is None


def test_audio_bitrate_majority_and_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    files = [tmp_path / f"{index}.mp3" for index in range(4)]
    for path in files:
        path.write_bytes(b"audio")

    values = iter(
        (
            SimpleNamespace(info=SimpleNamespace(bitrate=128000)),
            SimpleNamespace(info=SimpleNamespace(bitrate=128000)),
            SimpleNamespace(info=SimpleNamespace(bitrate=128000)),
            SimpleNamespace(info=SimpleNamespace(bitrate=320000)),
        )
    )
    monkeypatch.setattr(
        podcast_preparation, "mutagen_file", lambda _path: next(values)
    )
    assert podcast_preparation._audio_bitrate(files) == 128

    values = iter(
        (
            SimpleNamespace(info=SimpleNamespace(bitrate=128000)),
            SimpleNamespace(info=SimpleNamespace(bitrate=320000)),
        )
    )
    monkeypatch.setattr(
        podcast_preparation, "mutagen_file", lambda _path: next(values)
    )
    assert podcast_preparation._audio_bitrate(files[:2]) is None

    monkeypatch.setattr(
        podcast_preparation,
        "mutagen_file",
        lambda _path: (_ for _ in ()).throw(ValueError("bad")),
    )
    assert podcast_preparation._audio_bitrate(files) is None

    monkeypatch.setattr(
        podcast_preparation,
        "mutagen_file",
        lambda _path: SimpleNamespace(info=None),
    )
    assert podcast_preparation._audio_bitrate(files) is None


def test_generated_title_includes_bitrate(tmp_path: Path) -> None:
    media = tmp_path / "episode.mp3"
    meta = Meta(title="Example", year=2026)
    assert (
        podcast_preparation._generated_title(meta, media, [media], 128)
        == "Example [2026/MP3 - 128kbps]"
    )


def test_gather_rejects_missing_and_empty_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = Meta(path=str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="does not exist"):
        asyncio.run(podcast_preparation.gather_podcast_prep(missing))

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no supported"):
        asyncio.run(
            podcast_preparation.gather_podcast_prep(Meta(path=str(empty)))
        )

    monkeypatch.setattr(
        podcast_preparation,
        "_has_symlink_component",
        lambda path: path.name == "banner.png",
    )
    track = tmp_path / "track.mp3"
    track.write_bytes(b"audio")
    banner = tmp_path / "banner.png"
    banner.write_bytes(b"banner")
    meta = Meta(path=str(track), podcast_banner=str(banner))
    monkeypatch.setattr(
        podcast_preparation, "_detected_media_kind", lambda _path: "audio"
    )
    with pytest.raises(ValueError, match="symbolic links"):
        asyncio.run(podcast_preparation.gather_podcast_prep(meta))
