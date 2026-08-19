from __future__ import annotations

from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease
from src.integrations.trackers.music_validation import MusicValidator, OrpheusMusicValidator, ValidationLevel


def _track(
    name: str,
    *,
    format: str = "FLAC",
    bitrate: int | None = None,
    bitrate_mode: str | None = None,
    bit_depth: int | None = 24,
    sample_rate: int | None = 96000,
    channels: int | None = 2,
    disc: int | None = 1,
    number: int | None = 1,
    artist: str = "Artist",
    album: str = "Album",
    title: str = "Track",
) -> AudioTrack:
    return AudioTrack(
        path=name,
        relative_path=name,
        format=format,
        codec=format,
        bitrate=bitrate,
        bitrate_mode=bitrate_mode,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        channels=channels,
        disc_number=disc,
        track_number=number,
        artist=artist,
        album_artist=artist,
        album=album,
        title=title,
    )


def _release(*tracks: AudioTrack) -> MusicRelease:
    release = MusicRelease("/music", tracks=list(tracks))
    release.set_field("artist", "Artist", MetadataSource.FILE_TAG, 1.0)
    release.set_field("album", "Album", MetadataSource.FILE_TAG, 1.0)
    release.set_field("year", "2024", MetadataSource.FILE_TAG, 1.0)
    release.set_field("media", "WEB", MetadataSource.INFERRED, 0.5)
    release.set_field("release_type", "Album", MetadataSource.INFERRED, 0.5)
    return release


def _codes(issues: list[object]) -> set[str]:
    return {issue.code for issue in issues}


def test_generic_music_validator_all_branches() -> None:
    assert _codes(MusicValidator().validate(MusicRelease("/music"))) == {"no_audio"}

    tracks = [
        _track("1.flac", number=1),
        _track("3.mp3", format="MP3", number=3, artist="Other"),
        _track("untagged.flac", number=None, title=""),
    ]
    release = MusicRelease("/music", tracks=tracks)
    release.conflicts = {"album": ["One", "Two"], "artist": ["Artist", "Other"]}
    issues = MusicValidator().validate(release)
    codes = _codes(issues)
    assert {"missing_artist", "missing_album", "mixed_formats", "inconsistent_album", "inconsistent_artist", "untagged_track", "non_contiguous_tracks"} <= codes
    assert next(issue.level for issue in issues if issue.code == "inconsistent_album") == ValidationLevel.ERROR

    release = _release(_track("disc1.flac", disc=1), _track("disc2.flac", disc=2))
    release.conflicts["album"] = ["Album", "Album Two"]
    assert next(issue.level for issue in MusicValidator().validate(release) if issue.code == "inconsistent_album") == ValidationLevel.WARNING

    various = MusicRelease("/music", tracks=[_track("various.flac")])
    various.set_field("artist", "Various Artists", MetadataSource.FILE_TAG, 1.0)
    various.set_field("album", "Album", MetadataSource.FILE_TAG, 1.0)
    various.conflicts["artist"] = ["One", "Two"]
    assert "inconsistent_artist" not in _codes(MusicValidator().validate(various))


def test_orpheus_required_format_container_and_flac_rules() -> None:
    release = MusicRelease(
        "/music",
        tracks=[
            _track("bad.wav", format="WAV", number=1),
            _track("wrong.mp3", format="FLAC", bit_depth=32, sample_rate=50000, number=2),
            _track("wrong.bin", format="AAC", number=3),
            _track("fast.flac", format="FLAC", bit_depth=16, sample_rate=96000, number=4),
            _track("loud.mp3", format="MP3", bitrate=321000, bitrate_mode="CBR", number=5),
        ],
    )
    release.set_field("artist", "Artist", MetadataSource.FILE_TAG, 1.0)
    release.set_field("album", "Album", MetadataSource.FILE_TAG, 1.0)
    codes = _codes(OrpheusMusicValidator().validate(release))
    assert {
        "missing_year",
        "missing_media",
        "missing_release_type",
        "unsupported_format",
        "invalid_container",
        "bit_depth",
        "sample_rate",
        "16bit_high_rate",
        "mp3_cbr_limit",
    } <= codes


def test_orpheus_hybrid_single_physical_lineage_and_media_warnings() -> None:
    hybrid = _release(
        _track("one.flac", bit_depth=24, sample_rate=96000, number=1),
        _track("two.flac", bit_depth=16, sample_rate=44100, number=2),
    )
    issues = OrpheusMusicValidator().validate(hybrid)
    hybrid_issue = next(issue for issue in issues if issue.code == "hybrid_technical")
    assert hybrid_issue.level == ValidationLevel.WARNING

    hybrid.set_field("media", "CD", MetadataSource.INFERRED, 1.0)
    issues = OrpheusMusicValidator().validate(hybrid)
    assert next(issue.level for issue in issues if issue.code == "hybrid_technical") == ValidationLevel.ERROR
    assert "missing_log" in _codes(issues)

    single = _release(_track("single.flac"))
    single.set_field("release_type", "Album", MetadataSource.INFERRED, 1.0)
    codes = _codes(OrpheusMusicValidator().validate(single))
    assert {"single_track", "possible_unsplit"} <= codes

    official = _release(_track("single.flac"))
    official.set_field("release_type", "Single", MetadataSource.INFERRED, 1.0)
    assert "single_track" not in _codes(OrpheusMusicValidator().validate(official))

    unknown = _release(_track("one.flac"), _track("two.flac", number=2))
    unknown.fields.pop("media", None)
    assert "unknown_media" in _codes(OrpheusMusicValidator().validate(unknown))

    lineage = _release(_track("one.flac"), _track("two.flac", number=2))
    lineage.set_field("media", "SACD", MetadataSource.INFERRED, 1.0)
    assert "missing_lineage" in _codes(OrpheusMusicValidator().validate(lineage))
