"""Unit tests for the tracker-neutral music domain model."""

from src.domain_models.music import AudioTrack, AuxiliaryFiles, MetadataSource, MetadataValue, MusicRelease


def test_music_release_from_dict_round_trips_serialized_release():
    release = MusicRelease(
        root="/music/example",
        tracks=[
            AudioTrack(
                path="/music/example/01.flac",
                relative_path="01.flac",
                format="FLAC",
                codec="FLAC",
                bitrate=900_000,
                genre=["Rock"],
                tags={"artist": ["Example Artist"]},
            )
        ],
        auxiliary=AuxiliaryFiles(logs=["rip.log"], artwork=["cover.jpg"]),
        fields={"album": MetadataValue("Example Album", MetadataSource.FILE_TAG, 1.0)},
        conflicts={"album": ["Example Album", "Other Album"]},
        warnings=["Example warning"],
        external_ids={"musicbrainz_release": "release-id"},
    )

    restored = MusicRelease.from_dict(release.to_dict())

    assert restored == release


def test_music_release_from_dict_defaults_malformed_top_level_collections():
    restored = MusicRelease.from_dict(
        {
            "root": 123,
            "tracks": "not-a-list",
            "auxiliary": "not-a-mapping",
            "fields": "not-a-mapping",
            "conflicts": "not-a-mapping",
            "warnings": "not-a-list",
            "external_ids": "not-a-mapping",
        }
    )

    assert restored.root == "123"
    assert restored.tracks == []
    assert restored.auxiliary == AuxiliaryFiles()
    assert restored.fields == {}
    assert restored.conflicts == {}
    assert restored.warnings == []
    assert restored.external_ids == {}


def test_music_release_from_dict_skips_or_normalizes_malformed_nested_values():
    restored = MusicRelease.from_dict(
        {
            "tracks": [{1: "invalid-key"}, None],
            "auxiliary": {1: "invalid-key"},
            "fields": {
                "ignored": [],
                42: {"value": "kept", "source": object(), "confidence": object()},
            },
            "conflicts": {
                "ignored": "not-a-list",
                7: [1, "two"],
            },
            "warnings": [1, "warning"],
            "external_ids": {7: 99},
        }
    )

    assert restored.tracks == []
    assert restored.auxiliary == AuxiliaryFiles()
    assert restored.fields == {"42": MetadataValue("kept", MetadataSource.INFERRED, 0.0)}
    assert restored.conflicts == {"7": ["1", "two"]}
    assert restored.warnings == ["1", "warning"]
    assert restored.external_ids == {"7": "99"}
