from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease
from src.integrations.media import music_analyzer
from src.integrations.media.music_analyzer import MusicReleaseAnalyzer


def _track(tmp_path: Path, **values: object) -> AudioTrack:
    state: dict[str, object] = {
        "path": str(tmp_path / "track.flac"),
        "relative_path": "track.flac",
        "format": "FLAC",
        "codec": "FLAC",
        "artist": "Artist",
        "album_artist": "Artist",
        "album": "Album",
        "title": "Track",
        "date": "2024",
        "duration": 180.0,
        "tags": {"artist": ["Artist"], "albumartist": ["Artist"]},
    }
    state.update(values)
    return AudioTrack(**state)  # type: ignore[arg-type]


def _release(
    tmp_path: Path, tracks: list[AudioTrack] | None = None
) -> MusicRelease:
    return MusicRelease(root=str(tmp_path), tracks=list(tracks or []))


def test_basic_helpers_cover_missing_formats_and_modes(tmp_path: Path) -> None:
    tags = {"ARTIST": [" Artist "], "album": ["Album"]}
    assert music_analyzer._first(tags, "artist") == "Artist"
    assert music_analyzer._first({}, "missing") == ""
    assert music_analyzer._number("Disc 12") == 12
    assert music_analyzer._number("") is None

    class MP4Audio:
        pass

    class VorbisAudio:
        pass

    class UnknownAudio:
        pass

    assert music_analyzer._format_for(tmp_path / "x.flac", UnknownAudio()) == (
        "FLAC",
        "FLAC",
    )
    assert music_analyzer._format_for(tmp_path / "x.aac", MP4Audio()) == (
        "AAC",
        "AAC",
    )
    assert music_analyzer._format_for(tmp_path / "x.ogg", VorbisAudio()) == (
        "Ogg Vorbis",
        "Vorbis",
    )
    assert music_analyzer._format_for(tmp_path / "x.ac3", UnknownAudio()) == (
        "AC3",
        "AC-3",
    )
    assert music_analyzer._format_for(tmp_path / "x.dts", UnknownAudio()) == (
        "DTS",
        "DTS",
    )
    assert music_analyzer._format_for(tmp_path / "x.wav", UnknownAudio()) == (
        "WAV",
        "UnknownAudio",
    )

    for value, expected in (
        ("VBR", "VBR"),
        ("variable", "VBR"),
        ("ABR", "ABR"),
        ("average", "ABR"),
        ("CBR", "CBR"),
        ("constant", "CBR"),
        ("other", None),
    ):
        assert (
            music_analyzer._bitrate_mode(SimpleNamespace(bitrate_mode=value))
            == expected
        )
    assert music_analyzer._bitrate_mode(SimpleNamespace()) is None


def test_analyze_missing_path_and_auxiliary_classification(
    tmp_path: Path,
) -> None:
    analyzer = MusicReleaseAnalyzer()
    missing = analyzer.analyze(tmp_path / "missing")
    assert missing.warnings and "does not exist" in missing.warnings[0]

    root = tmp_path / "release"
    root.mkdir()
    files = {
        "rip.log": "logs",
        "disc.cue": "cues",
        "scene.nfo": "nfos",
        "checks.sfv": "sfvs",
        "playlist.m3u": "playlists",
        "playlist.m3u8": "playlists",
        "cover.jpg": "artwork",
        "booklet.png": "scans",
        "lineage.txt": "lineage",
        "other.bin": "other",
    }
    release = _release(root)
    for name, bucket in files.items():
        path = root / name
        path.write_bytes(b"x")
        analyzer._classify_auxiliary(release, path, root)
        assert name in getattr(release.auxiliary, bucket)


def test_disc_from_path_and_track_read_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    disc = root / "Disc 2"
    disc.mkdir(parents=True)
    path = disc / "01.flac"
    path.write_bytes(b"audio")
    assert MusicReleaseAnalyzer._disc_from_path(path, root) == 2
    assert (
        MusicReleaseAnalyzer._disc_from_path(root / "plain.flac", root) is None
    )

    analyzer = MusicReleaseAnalyzer()
    monkeypatch.setattr(
        music_analyzer.mutagen,
        "File",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad")),
    )
    assert analyzer._read_track(path, root) is None

    calls = 0

    def none_files(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        return

    monkeypatch.setattr(music_analyzer.mutagen, "File", none_files)
    assert analyzer._read_track(path, root) is None and calls == 2


def test_read_track_builds_normalized_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    disc = root / "CD2"
    disc.mkdir(parents=True)
    path = disc / "01.mp3"
    path.write_bytes(b"audio")

    easy = SimpleNamespace(
        tags={
            "ARTIST": "Artist",
            "albumartist": ["Album Artist"],
            "album": ["Album"],
            "title": ["Track"],
            "date": ["2024-01-01"],
            "discnumber": ["1/2"],
            "tracknumber": ["01/10"],
            "genre": ["Rock"],
            "organization": ["Label"],
            "catalognumber": ["CAT-1"],
            "isrc": ["ISRC1"],
        },
        info=SimpleNamespace(),
    )
    technical = SimpleNamespace(
        info=SimpleNamespace(
            bitrate=320000,
            bitrate_mode="CBR",
            bits_per_sample=16,
            sample_rate=44100,
            channels=2,
            length=180.5,
        )
    )
    values = iter((easy, technical))
    monkeypatch.setattr(
        music_analyzer.mutagen, "File", lambda *_args, **_kwargs: next(values)
    )

    track = MusicReleaseAnalyzer()._read_track(path, root)
    assert track is not None
    assert track.format == "MP3" and track.codec == "MP3"
    assert track.disc_number == 2 and track.track_number == 1
    assert track.artist == "Artist" and track.album_artist == "Album Artist"
    assert track.bitrate == 320000 and track.bitrate_mode == "CBR"
    assert track.label == "Label" and track.catalogue_number == "CAT-1"


def test_read_sidecar_encodings_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    utf16 = tmp_path / "utf16.nfo"
    utf16.write_bytes("hello".encode("utf-16"))
    assert "hello" in MusicReleaseAnalyzer._read_sidecar(utf16)

    latin = tmp_path / "latin.nfo"
    latin.write_bytes("olá".encode("cp1252"))
    assert MusicReleaseAnalyzer._read_sidecar(latin)

    assert MusicReleaseAnalyzer._read_sidecar(tmp_path / "missing.nfo") == ""

    class AlwaysBad:
        def __getitem__(self, _key: object) -> AlwaysBad:
            return self

        def decode(self, _encoding: str, errors: str = "strict") -> str:
            del errors
            raise UnicodeDecodeError("bad", b"x", 0, 1, "bad")

    monkeypatch.setattr(Path, "read_bytes", lambda _path: AlwaysBad())
    assert MusicReleaseAnalyzer._read_sidecar(latin) == ""


def test_nfo_metadata_extracts_quality_store_dates_and_conflicts(
    tmp_path: Path,
) -> None:
    nfo = tmp_path / "scene.nfo"
    nfo.write_text(
        "Artist : Auxiliary Artist\nAlbum: Auxiliary Album\nLabel: Aux Label\nGenre: Rock; Pop\nSource: WEB\n"
        "URL: https://store.example/item\nRetail Date: 2024-02-03\nRip Date: 2024-02-04\nQuality: 24bit 96kHz\n",
        encoding="utf-8",
    )
    track = _track(tmp_path, bit_depth=16, sample_rate=44100)
    release = _release(tmp_path, [track])
    release.auxiliary.nfos.append("scene.nfo")
    MusicReleaseAnalyzer()._extract_nfo_metadata(release)
    assert release.get("release_label") == "Aux Label"
    assert release.get("genres") == ["Rock", "Pop"]
    assert release.get("media") == "WEB"
    assert release.get("store_url") == "https://store.example/item"
    assert release.get("release_year") == "2024"
    assert (
        release.get("nfo_bit_depth") == 24
        and release.get("nfo_sample_rate") == 96000
    )
    assert len(release.warnings) == 2


def test_playlist_and_sfv_membership_with_missing_files(
    tmp_path: Path,
) -> None:
    track = _track(tmp_path, relative_path="track.flac")
    release = _release(tmp_path, [track])
    (tmp_path / "list.m3u").write_text(
        "#EXTM3U\ntrack.flac\nmissing.flac\n", encoding="utf-8"
    )
    (tmp_path / "checks.sfv").write_text(
        "track.flac ABCDEF12\nmissing.flac 12345678\ninvalid\n",
        encoding="utf-8",
    )
    release.auxiliary.playlists.append("list.m3u")
    release.auxiliary.sfvs.append("checks.sfv")
    analyzer = MusicReleaseAnalyzer()
    analyzer._inspect_playlists(release)
    analyzer._inspect_sfvs(release)
    assert release.get("playlist_tracks") == 2 and release.get(
        "playlist_missing_files"
    ) == ["missing.flac"]
    assert release.get("sfv_entries") == 2 and release.get(
        "sfv_missing_files"
    ) == ["missing.flac"]
    assert len(release.warnings) == 2


def test_set_artists_conflict_and_empty(tmp_path: Path) -> None:
    empty = _release(
        tmp_path, [_track(tmp_path, artist="", album_artist="", tags={})]
    )
    MusicReleaseAnalyzer._set_artists(empty)
    assert not empty.get("artist")

    one = _track(tmp_path, artist="A", album_artist="", tags={"artist": ["A"]})
    two = _track(
        tmp_path,
        relative_path="two.flac",
        artist="B",
        album_artist="",
        tags={"artist": ["B"]},
    )
    release = _release(tmp_path, [one, two])
    MusicReleaseAnalyzer._set_artists(release)
    assert release.get("artist") in {"A", "B"}
    assert release.conflicts["artist"] == ["A", "B"]


def test_directory_derivation_regional_and_catalogue_paths(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path, [_track(tmp_path)])
    MusicReleaseAnalyzer._derive_from_directory(
        release, "Artist - Album [Japan Edition]"
    )
    assert release.get("release_title") == "Japan Edition"

    release = _release(tmp_path, [_track(tmp_path)])
    MusicReleaseAnalyzer._derive_from_directory(
        release, "Artist - Album [2014 WEB FLAC][Label Name][886444460446]"
    )
    assert release.get("release_year") == "2014"
    assert release.get("release_label") == "Label Name"
    assert release.get("release_catalogue_number") == "886444460446"

    release = _release(tmp_path, [_track(tmp_path)])
    MusicReleaseAnalyzer._derive_from_directory(
        release, "Artist - Album {Roc-A-Fella B001219802 CD}"
    )
    assert release.get("release_label") == "Roc-A-Fella"
    assert release.get("directory_catalogue_number") == "B001219802"


def test_log_inference_and_release_type_remaining_paths(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path, [_track(tmp_path)])
    release.auxiliary.logs.append("missing.log")
    MusicReleaseAnalyzer._infer_media_from_logs(release)
    assert not release.get("media")

    log = tmp_path / "rip.log"
    log.write_bytes("Exact Audio Copy".encode("utf-16"))
    release.auxiliary.logs.append("rip.log")
    MusicReleaseAnalyzer._infer_media_from_logs(release)
    assert release.get("media") == "CD"

    long_single = _release(tmp_path, [_track(tmp_path, duration=1300)])
    MusicReleaseAnalyzer._derive_release_type(long_single)
    assert long_single.warnings and not long_single.get("release_type")

    ep = _release(
        tmp_path,
        [_track(tmp_path), _track(tmp_path, relative_path="two.flac")],
    )
    MusicReleaseAnalyzer._derive_release_type(ep)
    assert ep.get("release_type") == "EP"


def test_release_type_various_artists_rewrites_artist(tmp_path: Path) -> None:
    tracks = [
        _track(
            tmp_path,
            relative_path=f"{index}.flac",
            artist=f"Artist {index}",
            album_artist="Various Artists",
            tags={
                "albumartist": ["Various Artists"],
                "artist": [f"Artist {index}"],
            },
        )
        for index in range(4)
    ]
    release = _release(tmp_path, tracks)
    MusicReleaseAnalyzer._derive_release_type(release)
    assert release.get("release_type") == "Compilation"
    assert release.get("artist") == "Various Artists"
    assert len(release.get("artists")) == 4


def test_final_directory_and_release_type_branches(tmp_path: Path) -> None:
    catalogue = _release(tmp_path, [_track(tmp_path)])
    MusicReleaseAnalyzer._derive_from_directory(
        catalogue, "Artist - Album [2015 B001219802]"
    )
    assert catalogue.get("release_catalogue_number") == "B001219802"

    edition = _release(tmp_path, [_track(tmp_path)])
    MusicReleaseAnalyzer._derive_from_directory(
        edition, "Artist - Album [Deluxe Edition]"
    )
    assert edition.get("edition") == "Deluxe Edition"

    soundtrack = _release(tmp_path, [_track(tmp_path, album="Movie OST")])
    soundtrack.set_field("album", "Movie OST", MetadataSource.FILE_TAG, 1.0)
    MusicReleaseAnalyzer._derive_release_type(soundtrack)
    assert soundtrack.get("release_type") == "Soundtrack"

    live = _release(tmp_path, [_track(tmp_path, album="Live at Home")])
    live.set_field("album", "Live at Home", MetadataSource.FILE_TAG, 1.0)
    MusicReleaseAnalyzer._derive_release_type(live)
    assert live.get("release_type") == "Live album"
