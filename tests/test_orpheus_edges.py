from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.music import AudioTrack, MetadataSource, MusicRelease
from src.domain_models.release import Meta
from src.integrations.trackers import orpheus as orpheus_module
from src.integrations.trackers.music_validation import ValidationIssue, ValidationLevel
from src.integrations.trackers.orpheus import Orpheus


def _config(*, api_key: str = "api-key", announce_url: str = "https://home.opsfet.ch/passkey/announce") -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "ORPHEUS": {
                "api_key": api_key,
                "announce_url": announce_url,
            }
        },
    }


def _tracker(*, api_key: str = "api-key", announce_url: str = "https://home.opsfet.ch/passkey/announce") -> Orpheus:
    return Orpheus(_config(api_key=api_key, announce_url=announce_url))


def _music_release(
    root: Path | None = None,
    *,
    artist: str = "Example Artist",
    album: str = "Example Album",
    year: str = "2020",
    media: str = "WEB",
    release_type: str = "Album",
    format_name: str = "FLAC",
    bitrate: int | None = 900_000,
    bitrate_mode: str | None = None,
    bit_depth: int | None = 16,
    sample_rate: int | None = 44_100,
    channels: int | None = 2,
    track_count: int = 2,
) -> MusicRelease:
    base = root or Path()
    release = MusicRelease(root=str(base))
    release.tracks = _music_tracks(
        base,
        artist=artist,
        album=album,
        format_name=format_name,
        bitrate=bitrate,
        bitrate_mode=bitrate_mode,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        channels=channels,
        track_count=track_count,
    )
    _set_music_fields(release, artist=artist, album=album, year=year, media=media, release_type=release_type)
    return release


def _music_tracks(
    root: Path,
    *,
    artist: str,
    album: str,
    format_name: str,
    bitrate: int | None,
    bitrate_mode: str | None,
    bit_depth: int | None,
    sample_rate: int | None,
    channels: int | None,
    track_count: int,
) -> list[AudioTrack]:
    suffix = ".flac" if format_name == "FLAC" else ".mp3"
    return [
        _music_track(
            root,
            index,
            suffix=suffix,
            artist=artist,
            album=album,
            format_name=format_name,
            bitrate=bitrate,
            bitrate_mode=bitrate_mode,
            bit_depth=bit_depth,
            sample_rate=sample_rate,
            channels=channels,
        )
        for index in range(1, track_count + 1)
    ]


def _music_track(
    root: Path,
    index: int,
    *,
    suffix: str,
    artist: str,
    album: str,
    format_name: str,
    bitrate: int | None,
    bitrate_mode: str | None,
    bit_depth: int | None,
    sample_rate: int | None,
    channels: int | None,
) -> AudioTrack:
    return AudioTrack(
        path=str(root / f"track-{index}{suffix}"),
        relative_path=f"track-{index}{suffix}",
        format=format_name,
        codec=format_name,
        bitrate=bitrate,
        bitrate_mode=bitrate_mode,
        bit_depth=bit_depth,
        sample_rate=sample_rate,
        channels=channels,
        duration=60.0 + index,
        disc_number=1,
        track_number=index,
        artist=artist,
        album_artist=artist,
        album=album,
        title=f"Track {index}",
    )


def _set_music_fields(release: MusicRelease, *, artist: str, album: str, year: str, media: str, release_type: str) -> None:
    fields: dict[str, object] = {
        "artist": artist,
        "artists": [artist],
        "album": album,
        "year": year,
        "media": media,
        "release_type": release_type,
        "genres": ["Electronic", "Ambient"],
        "release_label": "Example Label",
        "release_catalogue_number": "CAT-001",
    }
    for key, value in fields.items():
        release.set_field(key, value, MetadataSource.USER, 1.0)


def _meta(root: Path | None = None, release: MusicRelease | None = None, **values: object) -> Meta:
    base = root or Path()
    music = release or _music_release(base)
    state: dict[str, object] = {
        "base_dir": str(base),
        "uuid": "release",
        "category": "MUSIC",
        "music_release": music.to_dict(),
        "tracker_status": {"ORPHEUS": {}},
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "debug": False,
        "scene": False,
        "artwork_url": "https://images.example.com/cover.jpg",
    }
    state.update(values)
    return Meta(state)


def _response(payload: Any, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://orpheus.network/ajax.php"), json=payload)


@pytest.mark.asyncio
async def test_orpheus_blacklist_checks_artist_release_and_label(tmp_path: Path) -> None:
    tracker = _tracker()
    release = _music_release(tmp_path, artist="Vap0rwave", album="Example Album")
    release.set_field("release_label", "Sandero Classic Sound", MetadataSource.USER, 2.0, force=True)
    meta = _meta(tmp_path, release)
    assert not await tracker.get_additional_checks(meta)
    reasons = meta.tracker_status["ORPHEUS"]["blocked_reasons"]
    assert "artist Vap0rwave" in reasons
    assert "blacklisted label Sandero Classic Sound" in reasons

    blocked_release = _music_release(tmp_path, artist="Dr. Dre", album="Detox")
    assert Orpheus._blocked_releases(blocked_release) == ["Dr. Dre - Detox"]


@pytest.mark.asyncio
async def test_orpheus_search_existing_guards_and_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    missing_artist = _music_release(tmp_path, artist="")
    assert await tracker.search_existing(_meta(tmp_path, missing_artist)) == []

    payload = {
        "status": "success",
        "response": {
            "results": [
                {
                    "artist": "Example Artist",
                    "groupName": "Example Album",
                    "groupId": 44,
                    "maxSize": 123,
                    "releaseType": "Album",
                    "torrents": [{"media": "WEB", "format": "FLAC", "encoding": "Lossless"}],
                },
                {"groupName": "Malformed", "torrents": "bad"},
            ]
        },
    }
    monkeypatch.setattr(tracker, "_browse_payload", AsyncMock(return_value=payload))
    results = await tracker.search_existing(_meta(tmp_path))
    assert results == [
        {
            "name": "Example Artist - Example Album",
            "size": 123,
            "id": 44,
            "link": "https://orpheus.network/torrents.php?id=44",
            "flags": ["WEB FLAC Lossless"],
            "type": "Album",
        }
    ]


def test_orpheus_response_result_guards() -> None:
    assert Orpheus._response_results(None) == []
    assert Orpheus._response_results({"status": "failure"}) == []
    assert Orpheus._response_results({"status": "success", "response": "bad"}) == []
    assert Orpheus._response_results({"status": "success", "response": {"results": "bad"}}) == []
    assert Orpheus._torrent_response({"status": "success", "response": "bad"}) is None


@pytest.mark.asyncio
async def test_orpheus_browse_and_torrent_payload_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_gazelle_json", AsyncMock(side_effect=httpx.RequestError("offline")))
    assert await tracker._browse_payload(_meta(tmp_path), "Artist", "Album") is None
    assert await tracker._torrent_payload(_meta(tmp_path), "1") is None

    assert await tracker.get_torrent("bad", _meta(tmp_path)) is None
    monkeypatch.setattr(tracker, "_torrent_payload", AsyncMock(return_value={"status": "success", "response": {"torrent": 1}}))
    assert await tracker.get_torrent("7", _meta(tmp_path)) == {"torrent": 1}


@pytest.mark.asyncio
async def test_orpheus_gazelle_json_rejects_non_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return _response([])

    monkeypatch.setattr(orpheus_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    with pytest.raises(ValueError, match="JSON object"):
        await _tracker()._gazelle_json(_meta(tmp_path), {"action": "browse"}, request_timeout=1.0)


@pytest.mark.asyncio
async def test_orpheus_requests_match_exact_partial_and_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    payload = {
        "status": "success",
        "response": {
            "results": [
                {
                    "requestId": 1,
                    "title": "Example Album",
                    "artists": [[{"name": "Example Artist"}, {"name": "Example Artist"}]],
                    "year": "2020",
                    "bounty": 100,
                    "releaseType": "Album",
                },
                {
                    "requestId": 2,
                    "title": "Example Album",
                    "artists": [[{"name": "Different Artist"}]],
                    "year": "2019",
                },
                {"requestId": 3, "title": "Other Album", "artists": [[{"name": "Example Artist"}]]},
                {"requestId": 4, "title": "Example Album", "isFilled": True},
                {"requestId": {}, "title": "Example Album"},
            ]
        },
    }
    monkeypatch.setattr(tracker, "_requests_payload", AsyncMock(return_value=payload))
    meta = _meta(tmp_path)
    matches = await tracker.get_requests(meta)
    assert [match["match_type"] for match in matches] == ["exact", "partial"]
    assert matches[0]["name"] == "Example Artist - Example Album"
    assert meta.tracker_status["ORPHEUS"]["request_matches"] == matches


@pytest.mark.asyncio
async def test_orpheus_requests_guard_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    assert await tracker.get_requests(_meta(tmp_path, category="MOVIE")) == []
    empty_album = _music_release(tmp_path, album="")
    assert await tracker.get_requests(_meta(tmp_path, empty_album)) == []
    monkeypatch.setattr(tracker, "_gazelle_json", AsyncMock(side_effect=ValueError("bad")))
    assert await tracker._requests_payload(_meta(tmp_path), "Album") is None
    tracker._log_request_matches([])


def test_orpheus_request_artist_and_match_helpers(tmp_path: Path) -> None:
    release = _music_release(tmp_path)
    record = {"artists": [[{"name": "Example Artist"}, "bad"], "bad"]}
    assert Orpheus._request_artists(record) == ["Example Artist"]
    assert Orpheus._request_match_type(release, {"title": "Other"}) is None
    assert Orpheus._request_match_type(release, {"title": "Example Album", "artists": [], "year": ""}) == "partial"


@pytest.mark.asyncio
async def test_orpheus_upload_preflight_validation_and_debug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_additional_checks", AsyncMock(return_value=False))
    assert not await tracker.upload(_meta(tmp_path))

    monkeypatch.setattr(tracker, "get_additional_checks", AsyncMock(return_value=True))

    class Validator:
        def validate(self, _release: MusicRelease) -> list[ValidationIssue]:
            return [ValidationIssue(ValidationLevel.ERROR, "bad", "invalid release")]

    monkeypatch.setattr(orpheus_module, "OrpheusMusicValidator", Validator)
    invalid = _meta(tmp_path)
    assert not await tracker.upload(invalid)
    assert "Validation failed" in invalid.tracker_status["ORPHEUS"]["status_message"]

    class ValidValidator:
        def validate(self, _release: MusicRelease) -> list[ValidationIssue]:
            return []

    monkeypatch.setattr(orpheus_module, "OrpheusMusicValidator", ValidValidator)
    debug = _meta(tmp_path, debug=True)
    assert await tracker.upload(debug)
    assert "Debug mode" in debug.tracker_status["ORPHEUS"]["status_message"]
    assert "debug_payload" in debug.tracker_status["ORPHEUS"]


@pytest.mark.asyncio
async def test_orpheus_upload_credentials_torrent_and_post_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    no_credentials = _tracker(api_key="", announce_url="")
    monkeypatch.setattr(no_credentials, "get_additional_checks", AsyncMock(return_value=True))
    monkeypatch.setattr(no_credentials, "_validation_passes", lambda *_args: True)
    assert not await no_credentials.upload(_meta(tmp_path))

    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_additional_checks", AsyncMock(return_value=True))
    monkeypatch.setattr(tracker, "_validation_passes", lambda *_args: True)
    monkeypatch.setattr(tracker, "_prepare_upload_torrent", AsyncMock(return_value=None))
    assert not await tracker.upload(_meta(tmp_path))

    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")
    monkeypatch.setattr(tracker, "_prepare_upload_torrent", AsyncMock(return_value=torrent))
    monkeypatch.setattr(tracker, "_post_release", AsyncMock(return_value=None))
    assert not await tracker.upload(_meta(tmp_path))


@pytest.mark.asyncio
async def test_orpheus_prepare_upload_torrent_success_and_missing(tmp_path: Path) -> None:
    tracker = _tracker()
    tracker.common.create_torrent_for_upload = AsyncMock()  # type: ignore[method-assign]
    root = tmp_path / "tmp" / "release"
    root.mkdir(parents=True)
    torrent = root / "[ORPHEUS].torrent"
    torrent.write_bytes(b"torrent")
    assert await tracker._prepare_upload_torrent(_meta(tmp_path)) == torrent

    torrent.unlink()
    missing_meta = _meta(tmp_path)
    assert await tracker._prepare_upload_torrent(missing_meta) is None
    assert "not created" in missing_meta.tracker_status["ORPHEUS"]["status_message"]


@pytest.mark.asyncio
async def test_orpheus_post_release_handles_transport_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tracker = _tracker()
    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")
    monkeypatch.setattr(tracker, "_upload_json", AsyncMock(side_effect=OSError("broken")))
    meta = _meta(tmp_path)
    assert await tracker._post_release(meta, _music_release(tmp_path), torrent) is None
    assert "Upload request failed" in meta.tracker_status["ORPHEUS"]["status_message"]


@pytest.mark.asyncio
async def test_orpheus_upload_json_and_log_handles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    release = _music_release(tmp_path)
    release.auxiliary.logs = ["rip.log"]
    (tmp_path / "rip.log").write_text("log", encoding="utf-8")
    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return _response({"status": "success", "response": {"torrentId": 1}})

    monkeypatch.setattr(orpheus_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    payload = await _tracker()._upload_json(_meta(tmp_path), release, torrent, {"submit": 1})
    assert payload["status"] == "success"


def test_orpheus_record_upload_response_variants(tmp_path: Path) -> None:
    tracker = _tracker()
    malformed = _meta(tmp_path)
    assert not tracker._record_upload_response(malformed, [])
    assert "malformed" in malformed.tracker_status["ORPHEUS"]["status_message"]

    rejected = _meta(tmp_path)
    assert not tracker._record_upload_response(rejected, {"status": "failure", "error": "nope"})
    assert "nope" in rejected.tracker_status["ORPHEUS"]["status_message"]

    no_result = _meta(tmp_path)
    assert tracker._record_upload_response(no_result, {"status": "success", "response": None})
    assert "no upload result" in no_result.tracker_status["ORPHEUS"]["status_message"]

    success = _meta(tmp_path)
    payload = {
        "status": "success",
        "response": {
            "torrentId": 5,
            "groupId": 6,
            "newgroup": True,
            "warnings": [" warning one ", None, ""],
        },
    }
    assert tracker._record_upload_response(success, payload)
    status = success.tracker_status["ORPHEUS"]
    assert status["torrent_id"] == 5
    assert status["group_id"] == 6
    assert status["new_group"] is True
    assert status["warnings"] == ["warning one"]

    clean = _meta(tmp_path)
    assert tracker._record_upload_response(clean, {"status": "success", "response": {}})
    assert clean.tracker_status["ORPHEUS"]["status_message"] == "Upload accepted by Orpheus."


def test_orpheus_payload_cover_encoding_and_artists(tmp_path: Path) -> None:
    tracker = _tracker()
    release = _music_release(tmp_path, bit_depth=24)
    release.set_field("edition_year", "2021", MetadataSource.USER, 1.0)
    release.set_field("edition", "Deluxe", MetadataSource.USER, 1.0)
    release.set_field("store_url", "https://store.invalid/album", MetadataSource.USER, 1.0)
    release.set_field("retail_date", "2021-01-01", MetadataSource.USER, 1.0)
    release.auxiliary.cues = ["album.cue"]
    release.auxiliary.logs = ["rip.log"]
    payload = tracker.build_upload_payload(_meta(tmp_path), release)
    assert payload["bitrate"] == "24bit Lossless"
    assert payload["remaster"] == 1
    assert payload["remaster_year"] == "2021"
    assert payload["image"] == "https://images.example.com/cover.jpg"
    assert "Cue sheet included." in str(payload["release_desc"])

    no_media = _music_release(tmp_path, media="")
    with pytest.raises(ValueError, match="media/source"):
        tracker.build_upload_payload(_meta(tmp_path), no_media)

    assert Orpheus._cover_url(_meta(tmp_path, artwork_url="https://sub.discogs.com/x.jpg")) == ""
    assert Orpheus._cover_url(_meta(tmp_path, artwork_url="file:///tmp/x.jpg")) == ""


def test_orpheus_mp3_encoding_and_artist_fallback(tmp_path: Path) -> None:
    cbr = _music_release(tmp_path, format_name="MP3", bitrate=320_000, bitrate_mode="CBR", bit_depth=None)
    assert Orpheus._encoding(cbr, "MP3") == ("320", "", False)

    vbr = _music_release(tmp_path, format_name="MP3", bitrate=210_000, bitrate_mode="VBR", bit_depth=None)
    encoding = Orpheus._encoding(vbr, "MP3")
    assert encoding[0] == "Other"
    assert encoding[2] is True

    release = _music_release(tmp_path)
    release.fields.pop("artists", None)
    assert Orpheus._artists(release) == ["Example Artist"]


def test_orpheus_form_album_duration_and_release_description(tmp_path: Path) -> None:
    release = _music_release(tmp_path)
    release.auxiliary.cues = ["album.cue"]
    release.auxiliary.logs = ["rip.log"]
    form = Orpheus._form_data({"artists[]": ["A", "B"], "submit": 1})
    assert form == [("artists[]", "A"), ("artists[]", "B"), ("submit", 1)]
    description = Orpheus._album_description(release)
    assert "Tracklist" in description
    assert "Total length" in description
    assert Orpheus._format_duration(3661) == "1:01:01"
    assert Orpheus._format_duration(61) == "01:01"
    release_description = Orpheus._release_description(release)
    assert "Technical audio" in release_description
    assert "Cue sheet included." in release_description
    assert "Rip log included." in release_description


def test_orpheus_edition_year_fallback_and_empty_artists(tmp_path: Path) -> None:
    release = _music_release(tmp_path, year="1999")
    assert Orpheus._edition_year_for_upload(release) == "1999"
    release.fields.pop("artists", None)
    release.fields.pop("artist", None)
    assert Orpheus._artists(release) == []


def test_orpheus_torrent_response_rejects_failure() -> None:
    assert Orpheus._torrent_response({"status": "failure"}) is None


@pytest.mark.asyncio
async def test_orpheus_upload_json_rejects_non_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    torrent = tmp_path / "upload.torrent"
    torrent.write_bytes(b"torrent")
    release = _music_release(tmp_path)

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return _response([])

    monkeypatch.setattr(orpheus_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    with pytest.raises(ValueError, match="JSON object"):
        await _tracker()._upload_json(_meta(tmp_path), release, torrent, {"submit": 1})


def test_orpheus_payload_includes_other_bitrate_for_vbr(tmp_path: Path) -> None:
    release = _music_release(tmp_path, format_name="MP3", bitrate=210_000, bitrate_mode="VBR", bit_depth=None)
    payload = _tracker().build_upload_payload(_meta(tmp_path), release)
    assert payload["bitrate"] == "Other"
    assert payload["other_bitrate"] == "210"
