from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import MediaInfoError
from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease
from src.domain_models.release import Meta
from src.services import music_preparation


def _release(tmp_path: Path) -> MusicRelease:
    release = MusicRelease(root=str(tmp_path))
    track = tmp_path / "track.flac"
    track.write_bytes(b"audio")
    release.tracks = [
        AudioTrack(
            path=str(track),
            relative_path=track.name,
            format="FLAC",
            codec="FLAC",
        )
    ]
    release.set_field("artist", "Artist", MetadataSource.FILE_TAG, 1.0)
    release.set_field("album", "Album", MetadataSource.FILE_TAG, 1.0)
    release.set_field("year", "2020", MetadataSource.FILE_TAG, 1.0)
    release.set_field("format", "FLAC", MetadataSource.FILE_TAG, 1.0)
    release.set_field("media", "WEB", MetadataSource.INFERRED, 0.5)
    return release


def _meta(
    tmp_path: Path, release: MusicRelease | None = None, **values: object
) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "music",
        "path": str(tmp_path),
        "category": "MUSIC",
        "music_release": (release or _release(tmp_path)).to_dict(),
        "music_discogs_enabled": True,
    }
    state.update(values)
    return Meta(state)


def test_image_suffix_and_embedded_artwork_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert music_preparation._image_suffix(b"\x89PNG\r\n\x1a\n") == ".png"
    assert music_preparation._image_suffix(b"image", "image/png") == ".png"
    assert music_preparation._image_suffix(b"RIFFxxxxWEBP") == ".webp"
    assert music_preparation._image_suffix(b"image", "image/webp") == ".webp"
    assert music_preparation._image_suffix(b"jpeg") == ".jpg"

    class Picture:
        def __init__(
            self, data: bytes, type_: int = 3, mime: str = "image/png"
        ) -> None:
            self.data = data
            self.type = type_
            self.mime = mime

    output = tmp_path / "artwork"
    output.mkdir()

    sequence = iter(
        (
            music_preparation.mutagen.MutagenError("broken"),
            None,
            SimpleNamespace(
                pictures=[Picture(b"back", 4), Picture(b"front", 3)]
            ),
        )
    )

    def files(_path: str):
        value = next(sequence)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(music_preparation.mutagen, "File", files)
    result = music_preparation._extract_embedded_artwork(
        ["bad", "none", "picture"], output
    )
    assert result == output / "MUSIC_COVER.png"
    assert result.read_bytes() == b"front"

    apic = SimpleNamespace(type=3, data=b"apic", mime="image/jpeg")
    monkeypatch.setattr(
        music_preparation.mutagen,
        "File",
        lambda _path: SimpleNamespace(tags={"APIC:front": apic}),
    )
    result = music_preparation._extract_embedded_artwork(["apic"], output)
    assert result == output / "MUSIC_COVER.jpg"
    assert result.read_bytes() == b"apic"

    class Cover:
        def __init__(self) -> None:
            self.pictures: list[object] = []
            self.tags: dict[str, object] = {}

        def __getitem__(self, key: str):
            if key == "covr":
                return [b"RIFFxxxxWEBP"]
            raise KeyError(key)

    monkeypatch.setattr(
        music_preparation.mutagen, "File", lambda _path: Cover()
    )
    result = music_preparation._extract_embedded_artwork(["cover"], output)
    assert result == output / "MUSIC_COVER.webp"

    class MissingCover(Cover):
        def __getitem__(self, _key: str):
            raise TypeError("not subscriptable")

    monkeypatch.setattr(
        music_preparation.mutagen, "File", lambda _path: MissingCover()
    )
    assert (
        music_preparation._extract_embedded_artwork(["none"], output) is None
    )


def test_prepare_music_cover_configured_extracted_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    configured = tmp_path / "configured.png"
    configured.write_bytes(b"image")
    meta = _meta(tmp_path, release, artwork_path=str(configured))
    assert asyncio.run(
        music_preparation.prepare_music_cover(meta, release)
    ) == str(configured)

    extracted = tmp_path / "extracted.jpg"
    extracted.write_bytes(b"image")
    monkeypatch.setattr(
        music_preparation,
        "_extract_embedded_artwork",
        lambda *_args: extracted,
    )
    meta = _meta(tmp_path, release, artwork_path="")
    assert asyncio.run(
        music_preparation.prepare_music_cover(meta, release)
    ) == str(extracted)
    assert meta.artwork_path == str(extracted)

    monkeypatch.setattr(
        music_preparation, "_extract_embedded_artwork", lambda *_args: None
    )
    assert (
        asyncio.run(
            music_preparation.prepare_music_cover(
                _meta(tmp_path, release), release
            )
        )
        == ""
    )


def test_music_override_year_and_all_cli_override_fields(
    tmp_path: Path,
) -> None:
    assert music_preparation._music_override_year(None, "year") == ""
    assert music_preparation._music_override_year("invalid", "year") == ""
    assert music_preparation._music_override_year(999, "year") == ""
    assert music_preparation._music_override_year(3001, "year") == ""
    assert music_preparation._music_override_year("2025", "year") == "2025"

    release = MusicRelease(root=str(tmp_path))
    meta = Meta(
        music_artist="Artist One & Artist Two",
        music_album="Album",
        manual_year="1980",
        music_release_year="2024",
        music_edition_year="2025",
        music_media="",
        manual_source="cd",
        music_release_type="single",
        music_label="Label",
        music_catalogue_number="CAT-1",
        music_genres="Rock, Pop",
        manual_edition=["Deluxe", "Edition"],
    )
    music_preparation._apply_music_cli_overrides(meta, release)
    assert release.get("artists") == ["Artist One", "Artist Two"]
    assert release.get("album") == "Album"
    assert release.get("year") == "1980"
    assert release.get("release_year") == "2024"
    assert release.get("edition_year") == "2025"
    assert release.get("media") == "CD"
    assert release.get("release_type") == "Single"
    assert release.get("release_label") == "Label"
    assert release.get("release_catalogue_number") == "CAT-1"
    assert release.get("genres") == ["Rock", "Pop"]
    assert release.get("edition") == "Deluxe Edition"


def test_discogs_ids_cover_invalid_release_master_and_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    meta = _meta(
        tmp_path,
        release,
        music_discogs_release_id="invalid",
        music_discogs_master_id="master/222",
        music_discogs_id="release/111",
    )
    original = music_preparation.DiscogsEnricher.parse_reference
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "parse_reference",
        staticmethod(
            lambda value, kind="release": (
                None if value == "invalid" else original(value, kind)
            )
        ),
    )
    assert music_preparation._discogs_ids(meta, release) == ("111", "222")
    assert release.external_ids["discogs_release"] == "111"
    assert release.external_ids["discogs_master"] == "222"


def test_find_discogs_release_filtering_no_match_unattended_and_interactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    release.set_field(
        "directory_catalogue_number", "CAT", MetadataSource.INFERRED, 0.5
    )
    meta = _meta(tmp_path, release)

    find = AsyncMock(
        return_value=[
            {
                "id": 123,
                "title": "Artist - Album",
                "year": "2020",
                "country": "US",
                "catno": "CAT",
            }
        ]
    )
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher, "find_exact_releases", find
    )
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "filter_releases_by_media",
        staticmethod(lambda matches, _media: matches),
    )
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "filter_releases_by_catalogue",
        staticmethod(lambda matches, _cat: matches),
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == "123"
    )

    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "find_exact_releases",
        AsyncMock(return_value=[{"id": 1}, {"id": 2}]),
    )
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "filter_releases_by_catalogue",
        staticmethod(lambda matches, _cat: matches[:1]),
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == "1"
    )

    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "find_exact_releases",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "filter_releases_by_catalogue",
        staticmethod(lambda matches, _cat: matches),
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == ""
    )

    matches = [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}]
    monkeypatch.setattr(
        music_preparation.DiscogsEnricher,
        "find_exact_releases",
        AsyncMock(return_value=matches),
    )
    meta.unattended = True
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == ""
    )

    meta.unattended = False
    answers = iter(("bad", "3", "2"))
    monkeypatch.setattr(
        music_preparation.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: next(answers),
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == "2"
    )

    monkeypatch.setattr(
        music_preparation.cli_ui, "ask_string", lambda *_args, **_kwargs: "0"
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == ""
    )

    monkeypatch.setattr(
        music_preparation.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()),
    )
    assert (
        asyncio.run(
            music_preparation._find_discogs_release(meta, release, "token")
        )
        == ""
    )


def test_gather_music_prep_enrichment_and_media_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    analyzer = SimpleNamespace(analyze=lambda _path: release)
    monkeypatch.setattr(
        music_preparation, "MusicReleaseAnalyzer", lambda: analyzer
    )
    monkeypatch.setattr(
        music_preparation.MusicBrainzEnricher, "enrich", AsyncMock()
    )
    monkeypatch.setattr(
        music_preparation, "prepare_music_cover", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        music_preparation, "_write_music_release_snapshot", AsyncMock()
    )

    from src.integrations.media import media_info_export

    monkeypatch.setattr(
        media_info_export,
        "export_info",
        AsyncMock(
            side_effect=MediaInfoError(
                "bad", command=["mediainfo"], stderr="details"
            )
        ),
    )
    meta = _meta(tmp_path, release, music_enrichment=None, edit=False)
    asyncio.run(
        music_preparation.gather_music_prep(
            meta, {"DEFAULT": {"music_enrichment_enabled": True}}
        )
    )
    assert meta.mediainfo == {}

    monkeypatch.setattr(
        media_info_export,
        "export_info",
        AsyncMock(side_effect=RuntimeError("bad")),
    )
    asyncio.run(
        music_preparation.gather_music_prep(
            _meta(tmp_path, release, edit=False), {"DEFAULT": {}}
        )
    )

    empty = MusicRelease(root=str(tmp_path))
    monkeypatch.setattr(
        music_preparation,
        "MusicReleaseAnalyzer",
        lambda: SimpleNamespace(analyze=lambda _path: empty),
    )
    asyncio.run(
        music_preparation.gather_music_prep(
            _meta(tmp_path, empty, edit=True), {"DEFAULT": "invalid"}
        )
    )


def test_enrich_music_from_discogs_guards_existing_ids_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    assert not asyncio.run(
        music_preparation.enrich_music_from_discogs(Meta(category="MOVIE"), {})
    )
    assert not asyncio.run(
        music_preparation.enrich_music_from_discogs(
            Meta(category="MUSIC", music_discogs_enabled=False), {}
        )
    )

    meta = _meta(tmp_path, release, music_discogs_release_id="release/101")
    enrich = AsyncMock()
    monkeypatch.setattr(music_preparation.DiscogsEnricher, "enrich", enrich)
    monkeypatch.setattr(
        music_preparation, "_write_music_release_snapshot", AsyncMock()
    )
    assert asyncio.run(
        music_preparation.enrich_music_from_discogs(
            meta, {"DEFAULT": {"music_discogs_token": "token"}}
        )
    )
    enrich.assert_awaited_once()

    release.external_ids["discogs_master"] = "master/202"
    meta = _meta(tmp_path, release)
    enrich.reset_mock()
    assert asyncio.run(
        music_preparation.enrich_music_from_discogs(meta, {"DEFAULT": {}})
    )
    enrich.assert_awaited_once()

    release = _release(tmp_path)
    release.external_ids["discogs_release"] = "release/303"
    meta = _meta(tmp_path, release)
    enrich.reset_mock()
    assert asyncio.run(
        music_preparation.enrich_music_from_discogs(meta, {"DEFAULT": {}})
    )
    assert enrich.await_args.kwargs["release_id"] == "303"

    meta = _meta(tmp_path, _release(tmp_path))
    monkeypatch.setattr(
        music_preparation, "_find_discogs_release", AsyncMock(return_value="")
    )
    assert not asyncio.run(
        music_preparation.enrich_music_from_discogs(meta, {"DEFAULT": {}})
    )


def test_tracker_field_people_music_name_and_orpheus_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _release(tmp_path)
    music_preparation._set_tracker_field(
        release, "artist", "Tracker Artist", 0.99
    )
    assert release.get("artist") == "Artist"
    music_preparation._set_tracker_field(
        release, "label", "Tracker Label", 0.9
    )
    assert release.get("label") == "Tracker Label"

    assert music_preparation._orpheus_people({}, "artists") == []
    assert (
        music_preparation._orpheus_people({"musicInfo": []}, "artists") == []
    )
    assert (
        music_preparation._orpheus_people(
            {"musicInfo": {"artists": "bad"}}, "artists"
        )
        == []
    )
    assert music_preparation._orpheus_people(
        {
            "musicInfo": {
                "artists": [{"name": "A"}, {"name": "A"}, "bad", {"name": ""}]
            }
        },
        "artists",
    ) == ["A"]

    assert (
        music_preparation._music_name(
            {
                "artist": "A",
                "album": "B",
                "year": "2020",
                "media": "WEB",
                "format": "FLAC",
            }
        )
        == "A - B [2020] [WEB FLAC]"
    )
    assert (
        music_preparation._music_name({"artist": "A", "album": "B"}) == "A - B"
    )

    assert not asyncio.run(
        music_preparation.enrich_music_from_orpheus(Meta(category="MOVIE"), {})
    )
    meta = _meta(tmp_path, release)
    meta.set_tracker_ids({"ORPHEUS": 123})

    from src.integrations.trackers import orpheus as orpheus_module

    monkeypatch.setattr(
        orpheus_module.Orpheus, "get_torrent", AsyncMock(return_value=None)
    )
    assert not asyncio.run(
        music_preparation.enrich_music_from_orpheus(
            meta, {"TRACKERS": {"ORPHEUS": {}}}
        )
    )
    monkeypatch.setattr(
        orpheus_module.Orpheus,
        "get_torrent",
        AsyncMock(return_value={"group": [], "torrent": {}}),
    )
    assert not asyncio.run(
        music_preparation.enrich_music_from_orpheus(
            meta, {"TRACKERS": {"ORPHEUS": {}}}
        )
    )

    payload = {
        "group": {
            "id": 456,
            "name": "Tracker Album",
            "year": 2020,
            "releaseTypeName": "Album",
            "tags": ["rock"],
            "wikiImage": "https://img.invalid/cover.jpg",
            "wikiBBcode": "musicbrainz.org/release/12345678-1234-1234-1234-123456789abc discogs.com/release/999 discogs.com/master/888",
            "musicInfo": {
                "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                "composers": [{"name": "Composer"}],
            },
        },
        "torrent": {
            "media": "CD",
            "remasterYear": 2024,
            "remasterTitle": "Deluxe",
            "remasterRecordLabel": "Label",
            "remasterCatalogueNumber": "CAT",
            "encoding": "Lossless",
        },
    }
    monkeypatch.setattr(
        orpheus_module.Orpheus, "get_torrent", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(
        music_preparation, "_write_music_release_snapshot", AsyncMock()
    )
    assert asyncio.run(
        music_preparation.enrich_music_from_orpheus(
            meta, {"TRACKERS": {"ORPHEUS": {}}}
        )
    )
    assert meta.artwork_url == "https://img.invalid/cover.jpg"
    assert meta.music_release["external_ids"]["orpheus_group"] == "456"
