import asyncio
import io
import json
import os
import wave
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import bencodepy
import httpx
import pytest
from PIL import Image
from torf import Torrent

from src.delivery.cli.arguments import Args
from src.domain_models.release import Meta
from src.integrations.media import media_info as mediainfo
from src.integrations.media.media_info_export import export_info
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.trackers.common import Common
from src.integrations.trackers.registry import tracker_class_map
from src.integrations.trackers.UNIT3D.unwalled import Unwalled
from src.services import podcast_preparation as podcast_prep
from src.services.podcast_preparation import gather_podcast_prep

bdecode = cast(Callable[[bytes], object], vars(bencodepy)["decode"])
bencode = cast(Callable[[object], bytes], vars(bencodepy)["encode"])


def _tracker(**settings: object) -> Unwalled:
    tracker_settings: dict[str, object] = {"api_key": "token", "announce_url": "https://unwalled.cc/announce/test-token", **settings}
    config: dict[str, object] = {
        "DEFAULT": {"screens": 0, "img_host_1": "imgbox"},
        "TRACKERS": {"UNWALLED": tracker_settings},
    }
    return Unwalled(config)


def _jpg(path: Path, size: tuple[int, int], color: str) -> None:
    Image.new("RGB", size, color).save(path, format="JPEG")


def test_unwalled_is_registered_as_a_podcast_tracker() -> None:
    assert tracker_class_map["UNWALLED"] is Unwalled
    assert Unwalled.supported_categories == ("PODCAST",)
    assert Unwalled.source_flag == "Unwalled"


def test_cli_accepts_podcast_and_unwalled_overrides(tmp_path: Path) -> None:
    meta = Meta()
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"

    parsed, _, _ = Args({"DEFAULT": {"screens": 0}}).parse(
        [
            str(tmp_path),
            "--category",
            "podcast",
            "--podcast-title",
            "Example Show [2026/MP3 - 128kbps]",
            "--podcast-cover",
            str(cover),
            "--podcast-banner",
            str(banner),
            "--unwalled-category",
            "Technology",
            "--unwalled-type",
            "Free Audio",
        ],
        meta,
    )

    assert parsed.manual_category == "podcast"
    assert parsed.podcast_title == "Example Show [2026/MP3 - 128kbps]"
    assert parsed.podcast_cover == str(cover)
    assert parsed.podcast_banner == str(banner)
    assert parsed.unwalled_category == "Technology"
    assert parsed.unwalled_type == "Free Audio"


def test_podcast_prep_rejects_mixed_audio_and_video(tmp_path: Path) -> None:
    (tmp_path / "episode.mp3").write_bytes(b"audio")
    (tmp_path / "episode.mp4").write_bytes(b"video")
    meta = Meta(path=str(tmp_path), category="PODCAST", manual_category="podcast")

    def detected_kind(path: Path) -> str:
        return "audio" if path.suffix == ".mp3" else "video"

    try:
        with patch("src.services.podcast_preparation._detected_media_kind", side_effect=detected_kind):
            asyncio.run(gather_podcast_prep(meta))
    except ValueError as error:
        assert "mixed audio and video" in str(error).lower()
    else:
        raise AssertionError("mixed podcast media must be rejected")


def test_podcast_prep_builds_an_audio_pack_without_tmdb(tmp_path: Path) -> None:
    episode = tmp_path / "001 - Pilot.mp3"
    episode.write_bytes(b"audio")
    meta = Meta(
        path=str(tmp_path),
        base_dir=str(tmp_path),
        uuid="podcast",
        category="PODCAST",
        manual_category="podcast",
        podcast_title="Example Show [2026/MP3 - 128kbps]",
    )

    with (
        patch("src.services.podcast_preparation._detected_media_kind", return_value="audio"),
        patch("src.services.podcast_preparation.export_info", new=AsyncMock(return_value={"media": {"track": []}})),
    ):
        asyncio.run(gather_podcast_prep(meta))

    assert meta.category == "PODCAST"
    assert meta.filelist == [str(episode.resolve())]
    assert meta.name == "Example Show [2026/MP3 - 128kbps]"
    assert meta.tmdb_id == 0 and meta.imdb_id == 0
    assert meta.resolution == ""


def test_unwalled_podcast_title_requires_year_format_and_audio_bitrate() -> None:
    assert Unwalled._valid_podcast_title("Example Show [2026/MP3 - 128kbps]", audio=True) is True
    assert Unwalled._valid_podcast_title("Example Show [2026-08-11/MP4]", audio=False) is True
    assert Unwalled._valid_podcast_title("Example Show [MP3 - 128kbps]", audio=True) is False
    assert Unwalled._valid_podcast_title("Example Show [2026/MP3]", audio=True) is False


@pytest.mark.asyncio
async def test_unwalled_additional_checks_reject_invalid_generated_title() -> None:
    meta = Meta(category="PODCAST", type="AUDIO", name="Example Show [MP3 - 128kbps]")

    assert await _tracker().get_additional_checks(meta) is False


@pytest.mark.asyncio
async def test_podcast_prep_offloads_per_file_media_scanning(tmp_path: Path) -> None:
    episode = tmp_path / "001 - Pilot.mp3"
    episode.write_bytes(b"audio")
    meta = Meta(path=str(tmp_path), base_dir=str(tmp_path), uuid="offloaded-scan", category="PODCAST", podcast_title="Example Show [2026/MP3 - 128kbps]")

    async def run_in_thread(function: Callable[..., object], *args: object) -> object:
        return function(*args)

    with (
        patch("src.services.podcast_preparation.asyncio.to_thread", new=AsyncMock(side_effect=run_in_thread)) as to_thread,
        patch("src.services.podcast_preparation._detected_media_kind", return_value="audio"),
        patch("src.services.podcast_preparation.export_info", new=AsyncMock(return_value={"media": {"track": []}})),
    ):
        await gather_podcast_prep(meta)

    offloaded_functions = [call.args[0].__name__ for call in to_thread.await_args_list]
    assert offloaded_functions == ["_source_files", "_media_files", "_audio_bitrate"]


@pytest.mark.asyncio
async def test_export_info_offloads_standard_mediainfo_parsing(tmp_path: Path) -> None:
    release_dir = tmp_path / "tmp" / "mediainfo-offload"
    release_dir.mkdir(parents=True)
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"audio")

    def parse_media(_path: str, *, output: str, **_kwargs: object) -> str:
        if output == "STRING":
            return "General\nComplete name : episode.mp3"
        return json.dumps({"media": {"@ref": "episode.mp3", "track": []}})

    async def run_in_thread(function: Callable[..., object], *args: object, **kwargs: object) -> object:
        return function(*args, **kwargs)

    with (
        patch("src.integrations.media.media_info_export.MediaInfo.parse", side_effect=parse_media),
        patch("src.integrations.media.media_info_export.asyncio.to_thread", new=AsyncMock(side_effect=run_in_thread)) as to_thread,
    ):
        result = await export_info(str(media), True, "mediainfo-offload", str(tmp_path))

    assert result["media"]["track"] == []
    assert to_thread.await_count == 2


@pytest.mark.asyncio
async def test_unwalled_single_episode_directory_keeps_folder_torrent(tmp_path: Path) -> None:
    content = tmp_path / "Example Show"
    content.mkdir()
    episode = content / "001 - Pilot.mp3"
    episode.write_bytes(b"audio")
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    meta = Meta(
        path=str(content),
        base_dir=str(tmp_path),
        uuid="single-folder",
        category="PODCAST",
        podcast_title="Example Show [2026/MP3 - 128kbps]",
        podcast_cover=str(cover),
        podcast_banner=str(banner),
        debug=True,
        max_piece_size=1,
        trackers=["UNWALLED"],
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)

    with (
        patch("src.services.podcast_preparation._detected_media_kind", return_value="audio"),
        patch("src.services.podcast_preparation.export_info", new=AsyncMock(return_value={"media": {"track": []}})),
    ):
        await gather_podcast_prep(meta)
    await TorrentCreator.create_torrent(meta, content, "BASE")

    filename = await _tracker().get_upload_torrent_filename(meta)
    torrent = Torrent.read(tmp_path / "tmp" / meta.uuid / f"{filename}.torrent")

    assert meta.keep_folder is True
    assert torrent.name == content.name


@pytest.mark.asyncio
async def test_unwalled_preserves_ampersands_in_final_title() -> None:
    tracker = _tracker()
    meta = Meta(name="Science & Society [2026/MP3 - 128kbps]")

    assert tracker.get_search_name(meta) == meta.name
    assert await tracker.get_name(meta) == {"name": meta.name}


def test_podcast_prep_includes_allowed_companion_files(tmp_path: Path) -> None:
    episode = tmp_path / "001 - Pilot.mp3"
    companion = tmp_path / "episode-notes.pdf"
    episode.write_bytes(b"audio")
    companion.write_bytes(b"notes")
    meta = Meta(path=str(tmp_path), base_dir=str(tmp_path), uuid="podcast-companion", category="PODCAST")

    with (
        patch("src.services.podcast_preparation._detected_media_kind", return_value="audio"),
        patch("src.services.podcast_preparation.export_info", new=AsyncMock(return_value={"media": {"track": []}})),
    ):
        asyncio.run(gather_podcast_prep(meta))

    assert meta.filelist == [str(episode.resolve()), str(companion.resolve())]


def test_podcast_prep_rejects_symlinks_and_disguised_archives(tmp_path: Path) -> None:
    external = tmp_path.parent / f"{tmp_path.name}-external.mp3"
    external.write_bytes(b"audio")
    linked = tmp_path / "linked.mp3"
    linked.symlink_to(external)
    meta = Meta(path=str(tmp_path), base_dir=str(tmp_path), uuid="podcast-symlink", category="PODCAST")

    with pytest.raises(ValueError, match="symbolic links"):
        asyncio.run(gather_podcast_prep(meta))

    linked.unlink()
    archive = tmp_path / "archive.mp3"
    archive.write_bytes(b"PK\x03\x04" + b"archive")
    with pytest.raises(ValueError, match="compressed archive"):
        asyncio.run(gather_podcast_prep(meta))

    archive.write_bytes(b"MSCF" + b"cab archive")
    with pytest.raises(ValueError, match="compressed archive"):
        asyncio.run(gather_podcast_prep(meta))


def test_podcast_prep_rejects_audio_archive_polyglot(tmp_path: Path) -> None:
    polyglot = tmp_path / "episode.wav"
    with wave.open(str(polyglot), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 800)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("hidden.txt", "archive payload")
    with polyglot.open("ab") as audio:
        audio.write(archive.getvalue())
    meta = Meta(path=str(polyglot), base_dir=str(tmp_path), uuid="polyglot", category="PODCAST")

    assert zipfile.is_zipfile(polyglot)
    with pytest.raises(ValueError, match="compressed archive"):
        asyncio.run(gather_podcast_prep(meta))


def test_podcast_prep_rejects_symlinked_ancestors_and_artwork(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    episode = real_dir / "episode.mp3"
    episode.write_bytes(b"audio")
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    ancestor_meta = Meta(path=str(linked_dir / episode.name), base_dir=str(tmp_path), uuid="ancestor-link", category="PODCAST")

    with pytest.raises(ValueError, match="symbolic links"):
        asyncio.run(gather_podcast_prep(ancestor_meta))

    real_cover = tmp_path / "real-cover.jpg"
    _jpg(real_cover, (500, 500), "red")
    linked_cover = tmp_path / "cover.jpg"
    linked_cover.symlink_to(real_cover)
    artwork_meta = Meta(
        path=str(episode),
        base_dir=str(tmp_path),
        uuid="artwork-link",
        category="PODCAST",
        podcast_cover=str(linked_cover),
    )
    with patch("src.services.podcast_preparation._detected_media_kind", return_value="audio"), pytest.raises(ValueError, match="symbolic links"):
        asyncio.run(gather_podcast_prep(artwork_meta))


def test_podcast_prep_rejects_media_with_a_mismatched_extension(tmp_path: Path) -> None:
    disguised_video = tmp_path / "video.mp3"
    disguised_video.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2")
    meta = Meta(path=str(disguised_video), base_dir=str(tmp_path), uuid="mismatched-media", category="PODCAST")

    with patch("src.services.podcast_preparation._detected_media_kind", return_value="video"), pytest.raises(ValueError, match="extension does not match"):
        asyncio.run(gather_podcast_prep(meta))


def test_podcast_prep_rejects_unidentified_declared_media(tmp_path: Path) -> None:
    fake_audio = tmp_path / "fake.mp3"
    fake_audio.write_text("not audio", encoding="utf-8")
    meta = Meta(path=str(fake_audio), base_dir=str(tmp_path), uuid="unidentified-media", category="PODCAST")

    with pytest.raises(ValueError, match="could not be identified"):
        asyncio.run(gather_podcast_prep(meta))


def test_unwalled_discovers_category_and_type_ids_from_unit3d_results() -> None:
    payload: dict[str, object] = {
        "data": [
            {
                "attributes": {
                    "category": "Technology",
                    "category_id": 14,
                    "type": "Free Audio",
                    "type_id": 3,
                }
            }
        ]
    }

    assert _tracker().catalog_from_response(payload) == {"categories": {"technology": "14"}, "types": {"free audio": "3"}}


@pytest.mark.asyncio
async def test_unwalled_discovers_options_across_all_result_pages() -> None:
    first_page: list[dict[str, object]] = [{"attributes": {"category": "Technology", "category_id": 14, "type": "Free Audio", "type_id": 3}} for _ in range(100)]
    second_page: list[dict[str, object]] = [{"attributes": {"category": "Science", "category_id": 15, "type": "Premium Audio", "type_id": 4}}]

    async def handler(request: httpx.Request) -> httpx.Response:
        payload: object = first_page if request.url.params["page"] == "1" else second_page
        return httpx.Response(200, json={"data": payload})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("src.integrations.trackers.UNIT3D.unwalled.httpx.AsyncClient", return_value=client):
        catalog = await _tracker().discover_options()

    assert catalog == {"categories": {"science": "15", "technology": "14"}, "types": {"free audio": "3", "premium audio": "4"}}


@pytest.mark.asyncio
async def test_unwalled_retries_incomplete_option_discovery_cache() -> None:
    tracker = _tracker()
    tracker.option_catalog["categories"]["technology"] = "14"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"attributes": {"type": "Free Audio", "type_id": 3}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("src.integrations.trackers.UNIT3D.unwalled.httpx.AsyncClient", return_value=client):
        catalog = await tracker.discover_options()

    assert catalog == {"categories": {"technology": "14"}, "types": {"free audio": "3"}}
    assert tracker.option_discovery_complete is True


@pytest.mark.asyncio
async def test_unwalled_bounds_option_and_upload_json_responses() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    option_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("src.integrations.trackers.UNIT3D.unwalled.httpx.AsyncClient", return_value=option_client):
        assert await _tracker().discover_options() == {"categories": {}, "types": {}}

    response_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with response_client.stream("GET", "https://unwalled.cc/api/test") as response:
        with pytest.raises(ValueError, match="size limit"):
            await _tracker()._bounded_response(response, 1024)


def test_unwalled_resolves_names_or_explicit_numeric_ids() -> None:
    tracker = _tracker()
    tracker.option_catalog = {"categories": {"technology": "14"}, "types": {"free audio": "3"}}
    named = Meta(category="PODCAST", unwalled_category="Technology", unwalled_type="Free Audio")
    numeric = Meta(category="PODCAST", unwalled_category="99", unwalled_type="42")

    assert asyncio.run(tracker.get_category_id(named)) == {"category_id": "14"}
    assert asyncio.run(tracker.get_type_id(named)) == {"type_id": "3"}
    assert asyncio.run(tracker.get_category_id(numeric)) == {"category_id": "99"}
    assert asyncio.run(tracker.get_type_id(numeric)) == {"type_id": "42"}


@pytest.mark.asyncio
async def test_unwalled_video_payload_omits_unknown_resolution() -> None:
    tracker = _tracker()
    meta = Meta(category="PODCAST", type="VIDEO", resolution="", name="Video Show", podcast_title="Video Show", unwalled_category="14", unwalled_type="3")

    with (
        patch.object(tracker, "get_description", new=AsyncMock(return_value={})),
        patch.object(tracker, "get_mediainfo", new=AsyncMock(return_value={})),
        patch.object(tracker, "get_bdinfo", new=AsyncMock(return_value={})),
    ):
        payload = await tracker.get_data(meta)

    assert "resolution_id" not in payload


@pytest.mark.asyncio
async def test_unwalled_duplicate_search_uses_upload_title_and_bounds_json() -> None:
    seen_names: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_names.append(request.url.params["name"])
        return httpx.Response(200, json={"data": []})

    meta = Meta(category="PODCAST", title="source-folder", name="Final Podcast Title", podcast_title="Final Podcast Title", unwalled_category="14", unwalled_type="3")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("src.integrations.trackers.UNIT3D.httpx.AsyncClient", return_value=client):
        assert await _tracker().search_existing(meta) == []

    assert seen_names == ["Final Podcast Title"]

    oversized = b'{"data":[],"padding":"' + b"x" * (2 * 1024 * 1024) + b'"}'

    async def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized)

    oversized_client = httpx.AsyncClient(transport=httpx.MockTransport(oversized_handler))
    with patch("src.integrations.trackers.UNIT3D.httpx.AsyncClient", return_value=oversized_client), pytest.raises(ValueError, match="size limit"):
        await _tracker().search_existing(meta)


def test_unwalled_requires_valid_distinct_jpeg_cover_and_banner(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    episode = tmp_path / "001 - Pilot.mp3"
    episode.write_bytes(b"audio")
    meta = Meta(
        path=str(tmp_path),
        category="PODCAST",
        name="Example Show [2026/MP3 - 128kbps]",
        filelist=[str(episode)],
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        unwalled_category="14",
        unwalled_type="3",
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is True
    meta.artwork_banner_path = str(cover)
    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


def test_unwalled_rejects_invalid_torrent_file_names(tmp_path: Path) -> None:
    assert _tracker()._valid_filename("bad:name.mp3") is False
    if os.name == "nt":
        return
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    invalid_file = tmp_path / "bad:name.mp3"
    invalid_file.write_bytes(b"audio")
    meta = Meta(
        path=str(tmp_path),
        category="PODCAST",
        name="Example Show [2026/MP3 - 128kbps]",
        filelist=[str(invalid_file)],
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        unwalled_category="14",
        unwalled_type="3",
    )

    assert asyncio.run(_tracker().get_additional_checks(meta)) is False


@pytest.mark.parametrize("filename", ["   ", "bad:name.mp3", "episode.mp3.", "episode.mp3 "])
def test_unwalled_rejects_windows_unsafe_or_blank_names(filename: str) -> None:
    assert _tracker()._valid_filename(filename) is False


def test_unwalled_rejects_invalid_nested_paths_and_missing_announce(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    if os.name != "nt":
        invalid_dir = tmp_path / "bad:name"
        invalid_dir.mkdir()
        invalid_episode = invalid_dir / "episode.mp3"
        invalid_episode.write_bytes(b"audio")
        invalid_meta = Meta(
            path=str(tmp_path),
            category="PODCAST",
            name="Example Show [2026/MP3 - 128kbps]",
            filelist=[str(invalid_episode)],
            artwork_path=str(cover),
            artwork_banner_path=str(banner),
            unwalled_category="14",
            unwalled_type="3",
        )
        assert asyncio.run(_tracker().get_additional_checks(invalid_meta)) is False

    valid_episode = tmp_path / "episode.mp3"
    valid_episode.write_bytes(b"audio")
    meta = Meta(
        path=str(tmp_path),
        category="PODCAST",
        name="Example Show [2026/MP3 - 128kbps]",
        filelist=[str(valid_episode)],
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        unwalled_category="14",
        unwalled_type="3",
    )
    assert asyncio.run(_tracker(announce_url="").get_additional_checks(meta)) is False


@pytest.mark.asyncio
async def test_unwalled_builds_private_v1_torrent_with_source_and_announce(tmp_path: Path) -> None:
    episode = tmp_path / "001 - Pilot.mp3"
    episode.write_bytes(b"audio data")
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    release_dir = tmp_path / "tmp" / "podcast-torrent"
    release_dir.mkdir(parents=True)
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="podcast-torrent",
        path=str(episode),
        filelist=[str(episode)],
        category="PODCAST",
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        isdir=False,
        max_piece_size=1,
        trackers=["UNWALLED"],
    )
    announce = "https://unwalled.cc/announce/example-token"

    await TorrentCreator.create_torrent(meta, episode, "BASE")
    base_path = release_dir / "BASE.torrent"
    metainfo = cast(dict[bytes, object], bdecode(base_path.read_bytes()))
    info = cast(dict[bytes, object], metainfo[b"info"])
    info.pop(b"private", None)
    base_path.write_bytes(bencode(metainfo))
    filename = await _tracker(announce_url=announce).get_upload_torrent_filename(meta)
    torrent = Torrent.read(release_dir / f"{filename}.torrent")

    assert filename == "[UNWALLED]"
    assert torrent.metainfo.get("announce") == announce
    assert torrent.metainfo["info"].get("private") == 1
    assert torrent.metainfo["info"].get("source") == "Unwalled"
    assert "file tree" not in torrent.metainfo["info"]

    mismatched = tmp_path / "other.mp3"
    mismatched.write_bytes(b"different")
    meta.filelist = [str(mismatched)]
    assert _tracker(announce_url=announce)._valid_upload_bundle(meta, release_dir / f"{filename}.torrent") is False


def test_unwalled_rejects_inconsistent_v1_piece_count() -> None:
    info: dict[bytes, object] = {
        b"name": b"episode.mp3",
        b"piece length": 16384,
        b"pieces": b"x" * 40,
        b"length": 1,
    }

    assert _tracker()._valid_v1_info(info) is False


def test_unwalled_rejects_huge_v1_lengths_without_overflow(tmp_path: Path) -> None:
    torrent_path = tmp_path / "huge-length.torrent"
    torrent_path.write_bytes(
        bencode(
            {
                b"announce": b"https://unwalled.cc/announce/test-token",
                b"info": {b"name": b"episode.mp3", b"piece length": 16384, b"pieces": b"x" * 20, b"length": 10**400},
            }
        )
    )

    assert _tracker()._torrent_is_v1(torrent_path) is False

    torrent_path.write_bytes(
        bencode(
            {
                b"announce": b"https://unwalled.cc/announce/test-token",
                b"info": {b"name": b"episode.mp3", b"piece length": 10**400, b"pieces": b"x" * 20, b"length": 1},
            }
        )
    )
    assert _tracker()._torrent_is_v1(torrent_path) is False


@pytest.mark.parametrize("piece_length", [16 * 1024, 32 * 1024 * 1024, 64 * 1024 * 1024, 128 * 1024 * 1024])
def test_unwalled_accepts_supported_v1_piece_sizes(piece_length: int) -> None:
    info: dict[bytes, object] = {b"name": b"episode.mp3", b"piece length": piece_length, b"pieces": b"x" * 20, b"length": 1}

    assert _tracker()._valid_v1_info(info) is True


@pytest.mark.parametrize("piece_length", [8 * 1024, 24 * 1024, 256 * 1024 * 1024])
def test_unwalled_rejects_unsupported_v1_piece_sizes(piece_length: int) -> None:
    info: dict[bytes, object] = {b"name": b"episode.mp3", b"piece length": piece_length, b"pieces": b"x" * 20, b"length": 1}

    assert _tracker()._valid_v1_info(info) is False


def test_unwalled_rejects_duplicate_v1_file_paths(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    episode = root / "episode.mp3"
    episode.write_bytes(b"a")
    meta = Meta(path=str(root), filelist=[str(episode)])
    info: dict[bytes, object] = {
        b"name": b"show",
        b"piece length": 16384,
        b"pieces": b"x" * 20,
        b"files": [
            {b"length": 1, b"path": [b"episode.mp3"]},
            {b"length": 1, b"path": [b"episode.mp3"]},
        ],
    }

    assert _tracker()._torrent_matches_files(info, meta) is False


@pytest.mark.asyncio
async def test_unwalled_rejects_cross_host_torrent_download_redirect(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.invalid/stolen.torrent"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = Meta(base_dir=str(tmp_path), uuid="secure-download")
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    with patch("src.integrations.trackers.registry.http_trackers", []), patch("src.integrations.trackers.common.httpx.AsyncClient", return_value=client):
        result = await Common({}).download_tracker_torrent(
            meta,
            "UNWALLED",
            headers={"authorization": "Bearer secret"},
            downurl="https://unwalled.cc/torrents/download/1",
            allowed_hosts=("unwalled.cc",),
            max_size=1024,
        )

    assert result is None
    assert [request.url.host for request in requests] == ["unwalled.cc"]
    assert not (tmp_path / "tmp" / meta.uuid / "[UNWALLED].torrent").exists()


@pytest.mark.asyncio
async def test_unwalled_option_discovery_does_not_follow_redirects() -> None:
    requests: list[httpx.Request] = []
    async_client_class = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.invalid/options"})

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return async_client_class(transport=httpx.MockTransport(handler), follow_redirects=False)

    with patch("src.integrations.trackers.UNIT3D.unwalled.httpx.AsyncClient", side_effect=client_factory):
        assert await _tracker().discover_options() == {"categories": {}, "types": {}}

    assert [request.url.host for request in requests] == ["unwalled.cc"]


@pytest.mark.asyncio
async def test_unwalled_rejects_oversized_torrent_download(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2048)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = Meta(base_dir=str(tmp_path), uuid="bounded-download")
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)
    with patch("src.integrations.trackers.registry.http_trackers", []), patch("src.integrations.trackers.common.httpx.AsyncClient", return_value=client):
        result = await Common({}).download_tracker_torrent(
            meta,
            "UNWALLED",
            downurl="https://unwalled.cc/torrents/download/1",
            allowed_hosts=("unwalled.cc",),
            max_size=1024,
        )

    assert result is None
    assert not (tmp_path / "tmp" / meta.uuid / "[UNWALLED].torrent").exists()


@pytest.mark.asyncio
async def test_unwalled_upload_uses_bounded_same_host_torrent_download(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, json={"success": True, "message": "uploaded", "data": "https://unwalled.cc/torrents/download/1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    release_dir = tmp_path / "tmp" / "upload-flow"
    release_dir.mkdir(parents=True)
    torrent_path = release_dir / "[UNWALLED].torrent"
    torrent_path.write_bytes(b"torrent")
    meta = Meta(base_dir=str(tmp_path), uuid="upload-flow", tracker_status={"UNWALLED": {}})
    tracker = _tracker()
    downloaded_path = str(torrent_path)

    with (
        patch("src.integrations.trackers.UNIT3D.httpx.AsyncClient", return_value=client),
        patch.object(tracker, "get_data", new=AsyncMock(return_value={})),
        patch.object(tracker, "get_upload_torrent_filename", new=AsyncMock(return_value="[UNWALLED]")),
        patch.object(tracker, "get_additional_files", new=AsyncMock(return_value={})),
        patch.object(tracker.common, "download_tracker_torrent", new=AsyncMock(return_value=downloaded_path)) as download_torrent,
    ):
        assert await tracker.upload(meta) is True

    download_torrent.assert_awaited_once()
    awaited_call = download_torrent.await_args
    assert awaited_call is not None
    assert awaited_call.kwargs["allowed_hosts"] == ("unwalled.cc",)
    assert awaited_call.kwargs["max_size"] == 1024 * 1024


@pytest.mark.asyncio
async def test_unwalled_upload_does_not_follow_redirects(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    async_client_class = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://attacker.invalid/collect"})

    release_dir = tmp_path / "tmp" / "upload-redirect"
    release_dir.mkdir(parents=True)
    (release_dir / "[UNWALLED].torrent").write_bytes(b"private torrent")
    meta = Meta(base_dir=str(tmp_path), uuid="upload-redirect", tracker_status={"UNWALLED": {}})
    tracker = _tracker()

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return async_client_class(transport=httpx.MockTransport(handler), follow_redirects=False)

    with (
        patch("src.integrations.trackers.UNIT3D.httpx.AsyncClient", side_effect=client_factory),
        patch.object(tracker, "get_data", new=AsyncMock(return_value={"name": "Private Show"})),
        patch.object(tracker, "get_upload_torrent_filename", new=AsyncMock(return_value="[UNWALLED]")),
        patch.object(tracker, "get_additional_files", new=AsyncMock(return_value={})),
    ):
        assert await tracker.upload(meta) is False

    assert [request.url.host for request in requests] == ["unwalled.cc"]
    assert meta.tracker_status["UNWALLED"]["status_message"] == "data error: Upload redirect rejected"


@pytest.mark.asyncio
async def test_unwalled_omits_tracker_controlled_error_messages(tmp_path: Path) -> None:
    sentinel = "reflected-sensitive-value"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": sentinel})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    release_dir = tmp_path / "tmp" / "redacted-error"
    release_dir.mkdir(parents=True)
    (release_dir / "[UNWALLED].torrent").write_bytes(b"private torrent")
    meta = Meta(base_dir=str(tmp_path), uuid="redacted-error", tracker_status={"UNWALLED": {}})
    tracker = _tracker()

    with (
        patch("src.integrations.trackers.UNIT3D.httpx.AsyncClient", return_value=client),
        patch.object(tracker, "get_data", new=AsyncMock(return_value={})),
        patch.object(tracker, "get_upload_torrent_filename", new=AsyncMock(return_value="[UNWALLED]")),
        patch.object(tracker, "get_additional_files", new=AsyncMock(return_value={})),
        patch("src.integrations.trackers.UNIT3D.logger.info") as log_info,
    ):
        assert await tracker.upload(meta) is False

    assert sentinel not in str(meta.tracker_status)
    assert all(sentinel not in str(call) for call in log_info.call_args_list)


@pytest.mark.asyncio
async def test_unwalled_rejects_v2_base_and_oversized_final_bundle(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    episode = content / "episode.mp3"
    episode.write_bytes(b"audio")
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    release_dir = tmp_path / "tmp" / "v2-check"
    release_dir.mkdir(parents=True)
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="v2-check",
        path=str(episode),
        filelist=[str(episode)],
        category="PODCAST",
        name="Example Show [2026/MP3 - 128kbps]",
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        unwalled_category="14",
        unwalled_type="3",
        max_piece_size=1,
        trackers=["UNWALLED"],
    )
    await TorrentCreator.create_torrent(meta, episode, "BASE")
    base_path = release_dir / "BASE.torrent"
    metainfo = cast(dict[bytes, object], bdecode(base_path.read_bytes()))
    info = cast(dict[bytes, object], metainfo[b"info"])
    info[b"meta version"] = 2
    info[b"file tree"] = {}
    base_path.write_bytes(bencode(metainfo))

    assert await _tracker().get_additional_checks(meta) is False

    await TorrentCreator.create_torrent(meta, episode, "BASE")
    await _tracker().get_upload_torrent_filename(meta)
    upload_path = release_dir / "[UNWALLED].torrent"
    padding = 1024 * 1024 - upload_path.stat().st_size - cover.stat().st_size - banner.stat().st_size
    with banner.open("ab") as banner_file:
        banner_file.write(b"x" * max(padding, 0))
    assert _tracker()._valid_upload_bundle(meta, upload_path) is False
    with pytest.raises(ValueError, match="bundle validation"):
        await _tracker().get_upload_torrent_filename(meta)


@pytest.mark.asyncio
async def test_unwalled_debug_torrent_never_contains_personal_announce(tmp_path: Path) -> None:
    content = tmp_path / "torrent-content"
    content.mkdir()
    episode = content / "episode.mp3"
    episode.write_bytes(b"audio")
    cover = tmp_path / "cover.jpg"
    banner = tmp_path / "banner.jpg"
    _jpg(cover, (500, 500), "red")
    _jpg(banner, (960, 540), "blue")
    release_dir = tmp_path / "tmp" / "debug-announce"
    release_dir.mkdir(parents=True)
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="debug-announce",
        path=str(episode),
        filelist=[str(episode)],
        category="PODCAST",
        artwork_path=str(cover),
        artwork_banner_path=str(banner),
        max_piece_size=1,
        trackers=["UNWALLED"],
        debug=True,
    )
    await TorrentCreator.create_torrent(meta, episode, "BASE")

    filename = await _tracker(announce_url="https://unwalled.cc/announce/personal-token").get_upload_torrent_filename(meta)
    torrent = Torrent.read(release_dir / f"{filename}.torrent")

    assert torrent.metainfo.get("announce") == "https://fake.tracker"


def test_unwalled_rejects_malformed_announce_ports() -> None:
    assert _tracker()._valid_announce_url("https://unwalled.cc:invalid/announce/token") is False


def test_unwalled_rejects_malformed_or_path_traversing_v1_torrents(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.torrent"
    malformed.write_bytes(b"d4:infod6:lengthi1eee")
    assert _tracker()._torrent_is_v1(malformed) is False

    recursive = tmp_path / "recursive.torrent"
    recursive.write_bytes(b"d4:info" + b"l" * 1000 + b"e" * 1000 + b"e")
    assert _tracker()._torrent_is_v1(recursive) is False

    content = tmp_path / "torrent-content"
    content.mkdir()
    episode = content / "episode.mp3"
    episode.write_bytes(b"audio")
    release_dir = tmp_path / "tmp" / "unsafe-metainfo"
    release_dir.mkdir(parents=True)
    meta = Meta(base_dir=str(tmp_path), uuid="unsafe-metainfo", path=str(content), filelist=[str(episode)], category="PODCAST", max_piece_size=1)
    asyncio.run(TorrentCreator.create_torrent(meta, content, "BASE"))
    base_path = release_dir / "BASE.torrent"
    metainfo = cast(dict[bytes, object], bdecode(base_path.read_bytes()))
    info = cast(dict[bytes, object], metainfo[b"info"])
    files = cast(list[object], info[b"files"])
    first_file = cast(dict[bytes, object], files[0])
    first_file[b"path"] = [b"..", b"episode.mp3"]
    base_path.write_bytes(bencode(metainfo))
    assert _tracker()._torrent_is_v1(base_path) is False


def test_unwalled_rejects_decompression_bomb_errors(tmp_path: Path) -> None:
    image_path = tmp_path / "bomb.jpg"
    image_path.write_bytes(b"not-an-image")
    with patch("src.integrations.trackers.UNIT3D.unwalled_validation.Image.open", side_effect=Image.DecompressionBombError("bomb")):
        assert _tracker()._image_details(str(image_path)) is None


def test_podcast_prep_uses_cli_backed_mediainfo() -> None:
    assert podcast_prep.MediaInfo is mediainfo.MediaInfo


def test_unwalled_catalog_guard_branches() -> None:
    tracker = _tracker()
    assert tracker.catalog_from_response({"data": "bad"}) == {"categories": {}, "types": {}}
    payload = {"data": ["bad-entry", {"attributes": "bad-attributes"}]}
    assert tracker.catalog_from_response(payload) == {"categories": {}, "types": {}}


@pytest.mark.asyncio
async def test_unwalled_discovery_reuses_completed_catalog() -> None:
    tracker = _tracker()
    tracker.option_catalog = {"categories": {"technology": "14"}, "types": {"free audio": "3"}}
    tracker.option_discovery_complete = True
    assert await tracker.discover_options() == tracker.option_catalog


@pytest.mark.asyncio
async def test_unwalled_discovery_stops_on_non_mapping_payload() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("src.integrations.trackers.UNIT3D.unwalled.httpx.AsyncClient", return_value=client):
        assert await _tracker().discover_options() == {"categories": {}, "types": {}}


@pytest.mark.asyncio
async def test_unwalled_mapping_modes_and_unknown_option() -> None:
    tracker = _tracker()
    tracker.option_catalog = {"categories": {"technology": "14"}, "types": {"free audio": "3"}}
    tracker.option_discovery_complete = True
    meta = Meta(category="PODCAST")

    assert await tracker.get_category_id(meta, mapping_only=True) == {"technology": "14"}
    assert await tracker.get_category_id(meta, reverse=True) == {"14": "technology"}
    assert await tracker.get_type_id(meta, mapping_only=True) == {"free audio": "3"}
    assert await tracker.get_type_id(meta, reverse=True) == {"3": "free audio"}
    with pytest.raises(ValueError, match="Unknown Unwalled category"):
        await tracker.get_category_id(meta, category="Missing")


@pytest.mark.asyncio
async def test_unwalled_additional_checks_require_title() -> None:
    assert not await _tracker().get_additional_checks(Meta(category="PODCAST", podcast_title="", name=""))


@pytest.mark.asyncio
async def test_unwalled_option_resolution_error_is_a_clean_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_valid_torrent_paths", lambda _meta: True)
    monkeypatch.setattr(tracker, "_valid_artwork", lambda _meta: True)
    tracker.get_category_id = AsyncMock(side_effect=ValueError("bad option"))  # type: ignore[method-assign]
    meta = Meta(
        category="PODCAST",
        podcast_title="Example Show [2026/MP3 - 128kbps]",
        type="AUDIO",
        filelist=["episode.mp3"],
        debug=True,
    )
    assert not await tracker.get_additional_checks(meta)


@pytest.mark.asyncio
async def test_unwalled_rejects_artwork_that_consumes_bundle_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x" * (1024 * 1024))
    monkeypatch.setattr(tracker, "_valid_torrent_paths", lambda _meta: True)
    monkeypatch.setattr(tracker, "_valid_artwork", lambda _meta: True)
    tracker.get_category_id = AsyncMock(return_value={"category_id": "14"})  # type: ignore[method-assign]
    tracker.get_type_id = AsyncMock(return_value={"type_id": "3"})  # type: ignore[method-assign]
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="art-budget",
        category="PODCAST",
        podcast_title="Example Show [2026/MP3 - 128kbps]",
        type="AUDIO",
        filelist=["episode.mp3"],
        artwork_path=str(cover),
        artwork_banner_path="",
        debug=True,
    )
    assert not await tracker.get_additional_checks(meta)


@pytest.mark.asyncio
async def test_unwalled_upload_filename_requires_valid_personal_announce() -> None:
    tracker = _tracker(announce_url="not-a-valid-url")
    with pytest.raises(ValueError, match="valid personal Unwalled announce URL"):
        await tracker.get_upload_torrent_filename(Meta(debug=False))
