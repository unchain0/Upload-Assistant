"""Edge contracts that keep pure MASA layers completely exercised."""

from __future__ import annotations

import copy
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from src import bootstrap
from src.domain_models import application_version
from src.domain_models.book_language import extract_first_author, is_valid_book_language, resolve_book_language
from src.domain_models.configuration import (
    ApplicationConfiguration,
    ConfigurationMigration,
    ConfigurationSource,
    ConfigurationSourceKind,
)
from src.domain_models.errors import TmdbCredentialMissingError
from src.domain_models.external_api import TmdbCredential, TmdbCredentialMode
from src.domain_models.image_upload import HostedImage, ImageUploadFailure, ImageUploadFailureKind, ImageUploadOutcome
from src.domain_models.media_identifiers import parse_tmdb_id
from src.domain_models.music import AudioTrack, AuxiliaryFiles, MetadataSource, MusicRelease
from src.domain_models.processing import ItemProcessingError, LoginError, ManualDateError, NoAudioMediaError, UploadError, WeirdSystemError, XEMNotFoundError
from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import (
    configured_screenshot_minimum,
    get_tracker_image_collection,
    has_tracker_image_collection,
    screenshot_requirement_error,
    set_tracker_image_collection,
    valid_screenshot_count,
)
from src.engines.configuration_reconciliation import reconcile_runtime_configuration
from src.engines.configuration_selection import configuration_has_user_settings, is_user_setting, select_configuration
from src.engines.music_validation import MusicValidator, OrpheusMusicValidator, ValidationLevel
from src.engines.region_mapping import get_distributor, get_region, get_service
from src.engines.tracker_description_policy import DescriptionCandidate, TrackerDescriptionMode, add_candidate, resolve_description_mode, score_release_name
from src.engines.upload_safety_policy import blocks_automatic_upload, book_metadata_cjk_fields, content_paths_with_spaces


def _configuration(data: dict[str, object], kind: ConfigurationSourceKind, path: str) -> ApplicationConfiguration:
    return ApplicationConfiguration.from_mapping(data, ConfigurationSource(path=path, kind=kind))


def test_application_version_metadata_is_named() -> None:
    assert application_version.__version__.startswith("v")
    assert "Full Changelog" in application_version.CHANGELOG


def test_configuration_domain_rejects_invalid_shapes_and_thaws_nested_values() -> None:
    source = ConfigurationSource("config.py", ConfigurationSourceKind.RUNTIME)
    configuration = ApplicationConfiguration.from_mapping(
        {"DEFAULT": {"hosts": ["imgbb", {"nested": (1, True, None)}]}},
        source,
    )
    assert configuration.section("MISSING") == {}
    mutable = configuration.mutable_copy()
    assert mutable == {"DEFAULT": {"hosts": ["imgbb", {"nested": [1, True, None]}]}}
    mutable["DEFAULT"]["hosts"] = []
    assert configuration.section("DEFAULT")["hosts"] != []

    with pytest.raises(TypeError, match="must be a mapping"):
        ApplicationConfiguration.from_mapping({"DEFAULT": 1}, source)
    with pytest.raises(TypeError, match="Unsupported configuration value"):
        ApplicationConfiguration.from_mapping({"DEFAULT": {"bad": object()}}, source)

    assert not ConfigurationMigration(configuration).changed
    assert ConfigurationMigration(configuration, migrated_paths=(("DEFAULT", "tmdb_api"),)).changed


def test_configuration_selection_covers_every_source_priority() -> None:
    defaults = _configuration(
        {"DEFAULT": {"tmdb_api": "", "img_host_1": "", "default_trackers": [], "nested": {"secret": ""}}},
        ConfigurationSourceKind.DEFAULT,
        "default.py",
    )
    runtime_empty = _configuration({"DEFAULT": {"tmdb_api": "", "img_host_1": ""}}, ConfigurationSourceKind.RUNTIME, "runtime.py")
    runtime = _configuration({"DEFAULT": {"tmdb_api": " runtime-key ", "img_host_1": "imgbb"}}, ConfigurationSourceKind.RUNTIME, "runtime.py")
    legacy = _configuration({"DEFAULT": {"tmdb_api": "legacy-key"}}, ConfigurationSourceKind.LEGACY, "legacy.py")
    explicit = _configuration({"DEFAULT": {"tmdb_api": "explicit-key"}}, ConfigurationSourceKind.EXPLICIT, "explicit.py")

    assert select_configuration([defaults, legacy, runtime, explicit], defaults) is explicit
    assert select_configuration([defaults, legacy, runtime], defaults) is runtime
    assert select_configuration([defaults, legacy, runtime_empty], defaults) is legacy
    assert select_configuration([defaults, runtime_empty], defaults) is runtime_empty
    assert select_configuration([defaults, legacy], defaults) is legacy
    legacy_empty = _configuration({"DEFAULT": {"tmdb_api": ""}}, ConfigurationSourceKind.LEGACY, "legacy-empty.py")
    assert select_configuration([defaults, legacy_empty], defaults) is legacy_empty
    assert select_configuration([defaults], defaults) is defaults
    tuple_credential = _configuration({"DEFAULT": {"api_key": ("one", "two")}}, ConfigurationSourceKind.RUNTIME, "tuple.py")
    assert configuration_has_user_settings(tuple_credential, defaults)
    import src.engines.configuration_selection as configuration_selection

    assert configuration_selection._normalized(MappingProxyType({"key": " value "})) == (("key", "value"),)
    assert configuration_has_user_settings(runtime, defaults)
    assert not configuration_has_user_settings(runtime_empty, defaults)

    assert not is_user_setting((), "value")
    for value in (None, "", "api key", "your token", (), MappingProxyType({})):
        assert not is_user_setting(("DEFAULT", "api_key"), value)
    assert is_user_setting(("DEFAULT", "img_host_9"), "pixhost")
    assert is_user_setting(("DEFAULT", "torrent_client"), "qbit")
    assert is_user_setting(("DEFAULT", "tmdb_access_token"), "token-value")
    assert is_user_setting(("TRACKERS", "BHD", "passkey"), "pass")
    assert not is_user_setting(("DEFAULT", "screens"), 4)


def test_configuration_reconciliation_preserves_runtime_and_fills_nested_values() -> None:
    defaults = _configuration(
        {
            "DEFAULT": {
                "tmdb_api": "",
                "img_host_1": "",
                "screens": 6,
                "new_list": ["default"],
                "whole_mapping": {"items": ["default"]},
                "nested": {"new_key": "default", "existing": "default"},
            }
        },
        ConfigurationSourceKind.DEFAULT,
        "default.py",
    )
    runtime = _configuration(
        {"DEFAULT": {"tmdb_api": "your key", "img_host_1": "", "api_key": None, "cookie": [], "numeric": 1, "nested": "invalid-runtime-shape"}},
        ConfigurationSourceKind.RUNTIME,
        "runtime.py",
    )
    legacy = _configuration(
        {
            "DEFAULT": {
                "tmdb_api": "legacy-key",
                "img_host_1": "onlyimage",
                "api_key": "legacy-api",
                "cookie": ["legacy-cookie"],
                "numeric": 2,
                "nested": {"secret": ["legacy"]},
            }
        },
        ConfigurationSourceKind.LEGACY,
        "legacy.py",
    )

    migration = reconcile_runtime_configuration(runtime, legacy, defaults, runtime_path="materialized.py")
    result = migration.configuration.mutable_copy()
    assert migration.changed
    assert migration.configuration.source.path == "materialized.py"
    assert result["DEFAULT"]["tmdb_api"] == "legacy-key"
    assert result["DEFAULT"]["img_host_1"] == "onlyimage"
    assert result["DEFAULT"]["nested"] == {"secret": ["legacy"], "new_key": "default", "existing": "default"}
    assert result["DEFAULT"]["screens"] == 6
    assert result["DEFAULT"]["new_list"] == ["default"]
    assert result["DEFAULT"]["whole_mapping"] == {"items": ["default"]}
    assert result["DEFAULT"]["api_key"] == "legacy-api"
    assert result["DEFAULT"]["cookie"] == ["legacy-cookie"]
    assert result["DEFAULT"]["numeric"] == 1
    import src.engines.configuration_reconciliation as configuration_reconciliation

    assert not configuration_reconciliation._is_empty(1)
    assert ("DEFAULT", "tmdb_api") in migration.migrated_paths
    assert ("DEFAULT", "screens") in migration.added_default_paths


def test_tmdb_and_image_upload_domain_outcomes() -> None:
    with pytest.raises(TmdbCredentialMissingError, match="must be a string"):
        TmdbCredential.parse(None)
    with pytest.raises(TmdbCredentialMissingError, match="empty"):
        TmdbCredential.parse("  ")
    assert TmdbCredential.parse(" key ") == TmdbCredential("key", TmdbCredentialMode.V3_API_KEY)
    token = "eyJheader.payload.signature"
    assert TmdbCredential.parse(token).mode is TmdbCredentialMode.V4_READ_ACCESS_TOKEN
    assert TmdbCredential.parse("x" * 65).mode is TmdbCredentialMode.V4_READ_ACCESS_TOKEN

    image = HostedImage("thumb", "raw", "page", "local")
    assert ImageUploadOutcome(image=image).succeeded
    failure = ImageUploadFailure("imgbb", ImageUploadFailureKind.HOST_UNAVAILABLE, "offline")
    assert not ImageUploadOutcome(failure=failure).succeeded


def test_book_language_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.domain_models.book_language as book_language

    class SameLanguage:
        def display_name(self, _language: str) -> str:
            return "same"

        def to_alpha3(self) -> str:
            return ""

    monkeypatch.setattr(book_language.langcodes, "get", lambda _value: SameLanguage())
    monkeypatch.setattr(book_language.langcodes, "find", lambda _value: (_ for _ in ()).throw(LookupError("unknown")))
    assert resolve_book_language("same") == ("Same", "")
    assert not is_valid_book_language("unknown", "eng")
    assert not is_valid_book_language("English", "und")
    assert extract_first_author("") == ""


def test_identifier_parsing_rejects_untrusted_urls_and_bad_ids() -> None:
    assert parse_tmdb_id("https://evil.invalid/movie/123", None) == ("", 0)
    assert parse_tmdb_id("https://www.themoviedb.org/movie/not-a-number", "TV") == ("TV", 0)
    assert parse_tmdb_id("movie/321/slug", None) == ("MOVIE", 321)
    assert parse_tmdb_id("tv/456", None) == ("TV", 456)
    assert parse_tmdb_id(" 789 ", "MOVIE") == ("MOVIE", 789)


def test_processing_errors_have_semantic_defaults_and_paths() -> None:
    assert str(LoginError()) == "An error occurred while logging in"
    assert str(LoginError("custom")) == "custom"
    assert str(UploadError()) == "An error occurred while uploading"
    assert str(UploadError("custom")) == "custom"
    for error_type in (XEMNotFoundError, WeirdSystemError, ManualDateError, NoAudioMediaError):
        assert isinstance(error_type("message"), Exception)
    error = ItemProcessingError("failed", "/release")
    assert str(error) == "failed"
    assert error.item_path == "/release"


def test_meta_mapping_compatibility_and_tracker_ids() -> None:
    meta = Meta({"title": "Original", "tracker_ids": {"BHD": 12}}, year=2025)
    assert meta.get_tracker_id("BEYONDHD") == "12"
    meta.set_tracker_ids({"PTP": 34, "custom": 56})
    assert meta.get_tracker_id("PASSTHEPOPCORN") == "34"
    assert meta.get_tracker_id("CUSTOM") == "56"
    meta.clear_tracker_id("PTP")
    assert meta.get_tracker_id("PTP") is None

    meta.manual_cast = " Alice , Bob "  # type: ignore[assignment]
    meta.imdb_info = {"stars": ["alice", "Carol"]}
    meta.tmdb_cast = ["Dave", 3]  # type: ignore[list-item]
    meta.populate_cast(limit=3)
    assert meta.cast == ["Alice", "Bob", "Carol"]
    meta.populate_cast(limit=0)
    meta.manual_cast = []
    meta.imdb_info = {}
    meta.tmdb_cast = [3]  # type: ignore[list-item]
    meta.populate_cast()
    assert meta.cast == []

    shallow = copy.copy(meta)
    deep = copy.deepcopy(meta)
    assert isinstance(shallow, Meta) and isinstance(deep, Meta)
    deep.cast.append("Changed")
    assert "Changed" not in meta.cast

    meta.update({"title": "Updated", "dynamic": "value"})
    assert meta["title"] == "Updated"
    assert meta["dynamic"] == "value"
    replacement = Meta(title="Replacement", year=2030)
    meta.update(replacement)
    assert meta.title == "Replacement" and meta.year == 2030

    assert meta.get("missing", "fallback") == "fallback"
    assert meta.setdefault("new_field", "created") == "created"
    assert "new_field" in meta
    meta["another"] = 1
    assert meta.pop("another") == 1
    meta.image_list = [{"raw_url": "x"}]
    assert meta.pop("image_list") == [{"raw_url": "x"}]
    assert meta.image_list == []
    assert meta.pop("missing", "fallback") == "fallback"
    del meta["dynamic"]
    assert meta.get("dynamic") is None
    with pytest.raises(KeyError):
        _ = meta["definitely_missing"]
    assert list(meta.items()) and list(meta.keys()) and list(meta.values())


def test_tracker_image_collection_and_requirements_cover_invalid_inputs() -> None:
    meta = Meta(category="MOVIE", image_list="invalid")  # type: ignore[arg-type]
    assert valid_screenshot_count(meta) == 0
    meta.image_list = [
        "https://img.invalid/a.png",
        {"raw_url": "ftp://invalid", "img_url": "https://img.invalid/b.png"},
        {"raw_url": None},
        42,
    ]  # type: ignore[list-item]
    assert valid_screenshot_count(meta) == 2
    assert configured_screenshot_minimum({"DEFAULT": "invalid"}) == 0
    assert configured_screenshot_minimum({"DEFAULT": {"min_successful_image_uploads": "bad"}}) == 3
    assert configured_screenshot_minimum({"DEFAULT": {"min_successful_image_uploads": -5}}) == 0
    assert "for BHD" in (screenshot_requirement_error(meta, {"DEFAULT": {"min_successful_image_uploads": 3}}, "BHD") or "")
    assert screenshot_requirement_error(Meta(category="BOOK"), {"DEFAULT": {}}) is None

    set_tracker_image_collection(meta, "BHD", "screenshots", [{"raw_url": "https://bhd.invalid/a"}])
    assert has_tracker_image_collection(meta, "BHD", "screenshots")
    assert get_tracker_image_collection(meta, "BHD", "screenshots")[0]["raw_url"].startswith("https://")
    meta.menu_images = "invalid"  # type: ignore[assignment]
    assert get_tracker_image_collection(meta, "OTHER", "menu_images") == []


def _track(path: str, **values: Any) -> AudioTrack:
    defaults: dict[str, Any] = {
        "path": path,
        "relative_path": Path(path).name,
        "format": "FLAC",
        "codec": "FLAC",
        "bit_depth": 16,
        "sample_rate": 44_100,
        "channels": 2,
        "disc_number": 1,
        "track_number": 1,
        "title": "Track",
    }
    defaults.update(values)
    return AudioTrack(**defaults)


def test_music_validators_cover_errors_warnings_and_orpheus_rules() -> None:
    assert MusicValidator().validate(MusicRelease("/music"))[0].code == "no_audio"

    release = MusicRelease(
        "/music",
        tracks=[
            _track("one.flac", title="", track_number=None),
            _track("two.mp3", format="MP3", codec="MP3", track_number=3, bitrate=400_000, bitrate_mode="CBR"),
        ],
        conflicts={"album": ["A", "B"], "artist": ["A", "B"]},
    )
    release.set_field("artist", "Artist", MetadataSource.FILE_TAG, 1.0)
    issues = MusicValidator().validate(release)
    codes = {issue.code for issue in issues}
    assert {"missing_album", "mixed_formats", "inconsistent_album", "inconsistent_artist", "untagged_track", "non_contiguous_tracks"} <= codes

    physical = MusicRelease(
        "/music",
        tracks=[
            _track("bad.wav", format="WAV", codec="PCM", bit_depth=32, sample_rate=12_345),
            _track("flac-in-wav.wav", bit_depth=32, sample_rate=12_345, track_number=2),
            _track("high-rate.flac", bit_depth=16, sample_rate=96_000, track_number=3),
            _track("aac-in-mp3.mp3", format="AAC", codec="AAC", track_number=4),
            _track("bad.mp3", format="MP3", codec="MP3", bitrate=400_000, bitrate_mode="CBR", track_number=5),
        ],
        auxiliary=AuxiliaryFiles(),
    )
    physical.set_field("artist", "Artist", MetadataSource.USER, 1.0)
    physical.set_field("album", "Album", MetadataSource.USER, 1.0)
    physical.set_field("year", 2025, MetadataSource.USER, 1.0)
    physical.set_field("media", "SACD", MetadataSource.USER, 1.0)
    physical.set_field("release_type", "Album", MetadataSource.USER, 1.0)
    issues = OrpheusMusicValidator().validate(physical)
    codes = {issue.code for issue in issues}
    assert {
        "unsupported_format",
        "invalid_container",
        "bit_depth",
        "sample_rate",
        "16bit_high_rate",
        "mp3_cbr_limit",
        "hybrid_technical",
        "missing_lineage",
    } <= codes

    lossless_physical = MusicRelease("/music", tracks=[_track("album.flac")])
    for field_name, field_value in (("artist", "Artist"), ("album", "Album"), ("year", 2025), ("media", "CD"), ("release_type", "Single")):
        lossless_physical.set_field(field_name, field_value, MetadataSource.USER, 1.0)
    assert "missing_log" in {issue.code for issue in OrpheusMusicValidator().validate(lossless_physical)}

    single = MusicRelease("/music", tracks=[_track("single.flac")])
    single.set_field("artist", "Artist", MetadataSource.USER, 1.0)
    single.set_field("album", "Single", MetadataSource.USER, 1.0)
    single.set_field("year", 2025, MetadataSource.USER, 1.0)
    single.set_field("release_type", "Album", MetadataSource.USER, 1.0)
    single_issues = OrpheusMusicValidator().validate(single)
    assert {"missing_media", "single_track", "possible_unsplit", "unknown_media"} <= {issue.code for issue in single_issues}
    assert any(issue.level is ValidationLevel.WARNING for issue in single_issues)


def test_music_validator_accepts_valid_edge_variants() -> None:
    various = MusicRelease(
        "/music",
        tracks=[_track("titled.flac", track_number=None, title="Named Track")],
        conflicts={"artist": ["Artist A", "Artist B"]},
    )
    various.set_field("artist", "Various Artists", MetadataSource.FILE_TAG, 1.0)
    various.set_field("album", "Compilation", MetadataSource.FILE_TAG, 1.0)
    codes = {issue.code for issue in MusicValidator().validate(various)}
    assert "inconsistent_artist" not in codes
    assert "untagged_track" not in codes

    legal_mp3 = MusicRelease(
        "/music",
        tracks=[_track("legal.mp3", format="MP3", codec="MP3", bitrate=320_000, bitrate_mode="CBR")],
    )
    assert OrpheusMusicValidator._mp3_bitrate_issue(legal_mp3.tracks[0]) is None


def test_region_distributor_and_service_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.engines.region_mapping as region_mapping

    assert awaitable_result(get_region({"label": "MOVIE USA DISC"})) == "USA"
    assert awaitable_result(get_region({"title": "UNKNOWN"})) == ""
    assert awaitable_result(get_region({}, "gbR")) == "GBR"
    assert awaitable_result(get_distributor("criterion")) == "CRITERION"
    assert awaitable_result(get_distributor(None)) == ""
    assert awaitable_result(get_distributor("not-listed")) == ""

    services = awaitable_result(get_service(get_services_only=True))
    assert isinstance(services, dict) and services["Amazon Prime"] == "AMZN"
    assert awaitable_result(get_service()) == ("", "")
    assert isinstance(region_mapping.guessit_fn("Example.2025.1080p"), dict)

    calls = iter(
        [
            {"streaming_service": "Amazon Prime"},
            {"title": "Example"},
        ]
    )
    monkeypatch.setattr(region_mapping, "guessit_fn", lambda *_args, **_kwargs: next(calls))
    assert awaitable_result(get_service("Example.Amazon.Prime.DTS-HD.MA-GRP", "-GRP", "DTS-HD MA", "Example")) == ("AMZN", "Amazon")


def awaitable_result(value: Any) -> Any:
    import asyncio

    return asyncio.run(value)


def test_description_policy_covers_invalid_modes_scoring_and_audit() -> None:
    for mode in TrackerDescriptionMode:
        resolved = resolve_description_mode(mode.value.upper())
        assert resolved is mode
    with pytest.raises(ValueError):
        resolve_description_mode(None)
    with pytest.raises(ValueError):
        resolve_description_mode("invalid")
    assert score_release_name("anything", "other", explicit_id=True) == 100
    assert score_release_name(None, "name") == 0
    assert score_release_name("", "name") == 0
    assert 0 < score_release_name("Movie 2025 1080p", "Movie.2025.1080p-GRP") <= 100

    meta = Meta(description_candidates=[])
    candidate = DescriptionCandidate("BHD", "12", "https://tracker.invalid/12", "Movie", "raw", "clean", 2, 90)
    add_candidate(meta, candidate, selected=False)
    add_candidate(meta, candidate, selected=True)
    assert len(meta.description_candidates) == 2
    assert meta.description_provenance["selected"] is True
    assert "raw_description" not in meta.description_provenance
    assert len(meta.description_provenance["raw_sha256"]) == 64


def test_upload_safety_handles_relative_absolute_and_windows_paths() -> None:
    relative = Meta(path="Release Folder", filelist=["disc one/file.mkv", "disc one/file.mkv", "", None])
    assert content_paths_with_spaces(relative) == ["Release Folder", "disc one"]
    assert blocks_automatic_upload(relative)
    relative.allow_spaces = True
    assert not blocks_automatic_upload(relative)

    posix = Meta(path="/media/root", filelist=["/media/root/Season 01/Episode.mkv", "/other/Other File.mkv"])
    assert content_paths_with_spaces(posix) == ["Season 01", "Other File.mkv"]
    windows = Meta(path=r"C:\Media\Root", filelist=[r"C:\Media\Root\Season 01\Episode.mkv"])
    assert content_paths_with_spaces(windows) == ["Season 01"]
    assert content_paths_with_spaces(Meta(path="", filelist="invalid")) == []
    assert content_paths_with_spaces(Meta(path="", filelist=["/absolute/Space Name.mkv"])) == ["Space Name.mkv"]

    assert book_metadata_cjk_fields(Meta(category="MOVIE")) == []
    book = Meta(category="BOOK", name="日本語", author="Author", title="中文", book_overview="説明")
    assert book_metadata_cjk_fields(book) == ["release name", "title", "description"]


def test_bootstrap_load_runtime_configuration_uses_prepared_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = _configuration({"DEFAULT": {}}, ConfigurationSourceKind.RUNTIME, "runtime.py")
    migration = ConfigurationMigration(configuration)
    monkeypatch.setattr(bootstrap, "prepare_runtime_configuration", lambda: migration)
    assert bootstrap.load_runtime_configuration() is configuration
