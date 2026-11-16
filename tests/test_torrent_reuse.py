import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.torrent_clients.client_manager import Clients


@pytest.mark.asyncio
async def test_qbittorrent_search_propagates_keyboard_interrupt(
    tmp_path, monkeypatch
):
    config = {
        "DEFAULT": {
            "default_torrent_client": "qbit",
            "prefer_max_16_torrent": False,
        },
        "TRACKERS": {},
        "TORRENT_CLIENTS": {
            "qbit": {"torrent_client": "qbit", "enable_search": True}
        },
    }
    clients = Clients(config)
    monkeypatch.setattr(
        clients, "init_qbittorrent_client", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        clients,
        "search_qbit_for_torrent",
        AsyncMock(side_effect=KeyboardInterrupt),
    )
    meta = Meta(base_dir=str(tmp_path), uuid="interrupt", client="qbit")

    with pytest.raises(KeyboardInterrupt):
        await clients._search_single_client_for_torrent(
            meta, "qbit", False, False, None
        )


@pytest.mark.asyncio
async def test_base_subs_contains_external_subtitle_with_custom_torrent(
    tmp_path,
):
    video = tmp_path / "release.mkv"
    subtitle = tmp_path / "release.pt-BR.srt"
    video.write_bytes(b"video data")
    subtitle.write_text("subtitle data", encoding="utf-8")
    (release / "release.p.srt").write_text("unselected subtitle", encoding="utf-8")

    meta = Meta(
        {
            "base_dir": str(tmp_path),
            "uuid": "test-release",
            "path": str(video if file_input else release),
            "filelist": [str(video)],
            "subtitle_files": [str(subtitle)],
            # The early base-torrent task runs before MOVIE/TV is detected.
            "category": category,
            "isdir": not file_input,
            "is_disc": "",
            "keep_folder": keep_folder,
            "mkbrr": False,
            "max_piece_size": 1,
            "trackers": [],
        }
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)

    await TorrentCreator.create_torrent(meta, meta.path, "BASE_SUBS")

    from torf import Torrent

    torrent = Torrent.read(tmp_path / "tmp" / meta.uuid / "BASE_SUBS.torrent")
    assert sorted(path.name for path in torrent.files) == sorted(
        [video.name, subtitle.name]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("file_input", [False, True])
async def test_base_subs_excludes_unselected_subtitles(tmp_path, file_input):
    video = tmp_path / "release.mkv"
    selected_subtitle = tmp_path / "release.pt-BR.srt"
    unrelated_subtitle = tmp_path / "release.en.srt"
    video.write_bytes(b"video data")
    selected_subtitle.write_text("selected", encoding="utf-8")
    unrelated_subtitle.write_text("unselected", encoding="utf-8")

    meta = Meta(
        {
            "base_dir": str(tmp_path),
            "uuid": "selected-subs",
            "path": str(video if file_input else tmp_path),
            "filelist": [str(video)],
            "subtitle_files": [str(selected_subtitle)],
            "category": "MOVIE",
            "isdir": not file_input,
            "is_disc": "",
            "keep_folder": False,
            "mkbrr": False,
            "max_piece_size": 1,
            "trackers": [],
        }
    )
    (tmp_path / "tmp" / meta.uuid).mkdir(parents=True)

    await TorrentCreator.create_torrent(meta, meta.path, "BASE_SUBS")

    from torf import Torrent

    torrent = Torrent.read(tmp_path / "tmp" / meta.uuid / "BASE_SUBS.torrent")
    assert sorted(path.name for path in torrent.files) == sorted(
        [video.name, selected_subtitle.name]
    )


@pytest.mark.asyncio
async def test_reuse_validation_rejects_same_basenames_in_different_layouts(
    tmp_path, monkeypatch
):
    torrent_path = tmp_path / "candidate.torrent"
    torrent_path.write_bytes(b"placeholder")
    local_files = [
        tmp_path / "release" / "season-01" / "episode.mkv",
        tmp_path / "release" / "season-02" / "episode.mkv",
    ]
    for path in local_files:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"video")

    fake_torrent = SimpleNamespace(
        files=["Release/episode.mkv", "Release/bonus/episode.mkv"]
    )
    monkeypatch.setattr(
        "src.integrations.torrent_clients.client_manager.Torrent.read",
        lambda _path: fake_torrent,
    )

    meta = Meta(
        path=str(release),
        uuid=release.name,
        filelist=[] if mode == "disc" else [str(path) for path in local_files],
        is_disc="BDMV" if mode == "disc" else "",
        keep_folder=mode == "keep_folder",
        isdir=True,
        category="TV",
    )

    valid, _ = await client.is_valid_torrent(
        meta, str(torrent_path), "hash", "qbit", {}
    )

    assert not valid


@pytest.mark.asyncio
async def test_reuse_validation_normalizes_binary_md5sum_in_working_copy(
    tmp_path,
):
    import bencodepy

    book = tmp_path / "book.epub"
    book.write_bytes(b"book")
    torrent_path = tmp_path / "client-storage" / "candidate.torrent"
    torrent_path.parent.mkdir()
    original_metainfo = bencodepy.encode(
        {
            b"announce": b"https://example.invalid/announce",
            b"info": {
                b"length": 4,
                b"md5sum": b"\xe9\xd9E\xa4H\x12 \xaf\x1f\xe3\x87\xedX\x8d\xcfV",
                b"name": b"book.epub",
                b"piece length": 32768,
                b"pieces": hashlib.sha1(b"book").digest(),  # noqa: S324
            },
        }
    )
    torrent_path.write_bytes(original_metainfo)
    meta = Meta(
        path=str(book),
        filelist=[str(book)],
        subtitle_files=[],
        is_disc="",
        keep_folder=False,
        isdir=False,
        uuid="book.epub",
        base_dir=str(tmp_path),
        debug=False,
    )

    valid, resolved_path = await Clients(
        {"DEFAULT": {}, "TORRENT_CLIENTS": {}}
    ).is_valid_torrent(meta, str(torrent_path), "hash", "qbit", {})

    assert valid
    normalized_path = tmp_path / "tmp" / "book.epub" / "candidate.torrent"
    assert resolved_path == str(normalized_path)
    assert torrent_path.read_bytes() == original_metainfo
    assert Torrent.read(normalized_path).files == [Path("book.epub")]


@pytest.mark.asyncio
async def test_reuse_validation_rejects_same_size_content_changes(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"original")
    torrent_path = tmp_path / "candidate.torrent"
    torrent = Torrent(path=book, piece_size=32768)
    torrent.generate(threads=1)
    torrent.write(torrent_path)
    book.write_bytes(b"modified")
    meta = Meta(
        path=str(book),
        filelist=[str(book)],
        subtitle_files=[],
        is_disc="",
        keep_folder=False,
        isdir=False,
        uuid="book.epub",
        base_dir=str(tmp_path),
        debug=False,
    )

    valid, _ = await Clients(
        {"DEFAULT": {}, "TORRENT_CLIENTS": {}}
    ).is_valid_torrent(meta, str(torrent_path), "hash", "qbit", {})

    assert not valid


@pytest.mark.asyncio
async def test_client_search_prefers_torrent_with_all_local_subtitles(
    tmp_path, monkeypatch
):
    video_only = tmp_path / "video-only.torrent"
    with_subtitles = tmp_path / "with-subtitles.torrent"
    video_only.touch()
    with_subtitles.touch()
    subtitle = tmp_path / "release.srt"
    subtitle.touch()

    torrents = {
        str(video_only): SimpleNamespace(files=["release.mkv"]),
        str(with_subtitles): SimpleNamespace(
            files=["release.mkv", "release.srt"]
        ),
    }
    monkeypatch.setattr(
        "src.integrations.torrent_clients.client_manager.Torrent.read",
        lambda path: torrents[str(path)],
    )

    async def fake_search(_self, _meta, client_name, *_args):
        return (
            str(video_only) if client_name == "first" else str(with_subtitles)
        )

    monkeypatch.setattr(
        Clients, "_search_single_client_for_torrent", fake_search
    )
    config = {
        "DEFAULT": {
            "default_torrent_client": "first",
            "searching_client_list": ["first", "second"],
            "prefer_max_16_torrent": False,
        },
        "TRACKERS": {},
        "TORRENT_CLIENTS": {"first": {}, "second": {}},
    }
    meta = Meta({"client": "none", "subtitle_files": [str(subtitle)]})

    found = await Clients(config)._find_existing_torrent(meta)

    assert found == str(with_subtitles)
    assert meta.reuse_torrent_client == "second"


@pytest.mark.asyncio
async def test_client_search_rejects_partially_subtitled_fallback(
    tmp_path, monkeypatch
):
    partial_subtitles = tmp_path / "partial-subtitles.torrent"
    partial_subtitles.touch()
    selected_subtitle = tmp_path / "release.pt-BR.srt"
    missing_subtitle = tmp_path / "release.en.srt"
    selected_subtitle.touch()
    missing_subtitle.touch()

    monkeypatch.setattr(
        "src.integrations.torrent_clients.client_manager.Torrent.read",
        lambda _path: SimpleNamespace(
            files=["release.mkv", selected_subtitle.name]
        ),
    )

    async def fake_search(_self, _meta, _client_name, *_args):
        return str(partial_subtitles)

    monkeypatch.setattr(
        Clients, "_search_single_client_for_torrent", fake_search
    )
    config = {
        "DEFAULT": {
            "default_torrent_client": "first",
            "searching_client_list": ["first"],
            "prefer_max_16_torrent": False,
        },
        "TRACKERS": {},
        "TORRENT_CLIENTS": {"first": {}},
    }
    meta = Meta(
        {
            "client": "none",
            "subtitle_files": [str(selected_subtitle), str(missing_subtitle)],
        }
    )

    assert await Clients(config).find_existing_torrent(meta) is None


@pytest.mark.asyncio
async def test_client_search_keeps_best_piece_size_video_only_fallback(
    tmp_path, monkeypatch
):
    small_piece_torrent = tmp_path / "small-piece.torrent"
    large_piece_torrent = tmp_path / "large-piece.torrent"
    selected_subtitle = tmp_path / "release.srt"
    small_piece_torrent.touch()
    large_piece_torrent.touch()
    selected_subtitle.touch()

    torrents = {
        str(small_piece_torrent): SimpleNamespace(
            files=["release.mkv"], piece_size=4 * 1024 * 1024
        ),
        str(large_piece_torrent): SimpleNamespace(
            files=["release.mkv"], piece_size=32 * 1024 * 1024
        ),
    }
    monkeypatch.setattr(
        "src.integrations.torrent_clients.client_manager.Torrent.read",
        lambda path: torrents[str(path)],
    )

    async def fake_search(_self, _meta, client_name, *_args):
        return (
            str(large_piece_torrent)
            if client_name == "first"
            else str(small_piece_torrent)
        )

    monkeypatch.setattr(
        Clients, "_search_single_client_for_torrent", fake_search
    )
    config = {
        "DEFAULT": {
            "default_torrent_client": "first",
            "searching_client_list": ["first", "second"],
            "prefer_max_16_torrent": True,
        },
        "TRACKERS": {},
        "TORRENT_CLIENTS": {"first": {}, "second": {}},
    }
    meta = Meta({"client": "none", "subtitle_files": [str(selected_subtitle)]})

    assert await Clients(config).find_existing_torrent(meta) == str(
        small_piece_torrent
    )
    assert meta.reuse_torrent_client == "second"


@pytest.mark.asyncio
async def test_client_search_registers_all_variants_from_one_client(tmp_path, monkeypatch):
    media = tmp_path / "release.mkv"
    media.touch()
    candidates = []
    for piece_size in (8 * MIB, 16 * MIB):
        torrent = Torrent()
        torrent.metainfo["info"] = {
            "name": media.name,
            "length": 64 * MIB,
            "piece length": piece_size,
            "pieces": b"x" * (20 * (64 * MIB // piece_size)),
        }
        candidate = tmp_path / f"{piece_size}.torrent"
        torrent.write(candidate)
        candidates.append(str(candidate))

    async def fake_search(_self, _meta, _client_name, _prefer_small, _piece_limit, _best_match, collect_all=False):
        assert collect_all  # noqa: S101
        return candidates

    monkeypatch.setattr(Clients, "_search_single_client_for_torrent", fake_search)
    config = {
        "DEFAULT": {"default_torrent_client": "qbit", "searching_client_list": ["qbit"]},
        "TORRENT_CLIENTS": {"qbit": {"torrent_client": "qbit"}},
    }
    meta = Meta(base_dir=str(tmp_path), uuid="release", path=str(media), filelist=[str(media)], client="none")

    paths = await Clients(config).find_existing_torrents(meta)

    manifest = TorrentManifest(meta.base_dir, meta.uuid)
    assert len(paths) == 2  # noqa: S101
    assert {entry.piece_size for entry in manifest.entries()} == {8 * MIB, 16 * MIB}  # noqa: S101
    eight = manifest.select("EIGHT", "base", TorrentPolicy(validator=lambda stats: stats.piece_size == 8 * MIB))
    sixteen = manifest.select("SIXTEEN", "base", TorrentPolicy(validator=lambda stats: stats.piece_size == 16 * MIB))
    assert eight is not None and eight.piece_size == 8 * MIB  # noqa: S101
    assert sixteen is not None and sixteen.piece_size == 16 * MIB  # noqa: S101


@pytest.mark.asyncio
async def test_qbit_search_returns_all_valid_matches_when_collecting(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    hashes = ["eight", "sixteen"]
    for torrent_hash in hashes:
        (storage / f"{torrent_hash}.torrent").touch()

    qbt_client = SimpleNamespace(
        torrents_info=lambda: [
            SimpleNamespace(hash=torrent_hash, name="release", save_path=str(tmp_path), content_path=str(tmp_path / "release.mkv"), tracker="", trackers=[], comment="metadata")
            for torrent_hash in hashes
        ]
    )
    config = {"DEFAULT": {}, "TORRENT_CLIENTS": {}}
    clients = Clients(config)
    monkeypatch.setattr(clients, "_matches_qbit_content_path", lambda _torrent, _meta: True)

    async def valid_torrent(_meta, path, _torrent_hash, _client_type, _client):
        return True, str(path)

    monkeypatch.setattr(clients, "is_valid_torrent", valid_torrent)
    meta = Meta(base_dir=str(tmp_path), uuid="release", path=str(tmp_path / "release.mkv"), subtitle_files=[], debug=False)
    client = {"torrent_storage_dir": str(storage)}

    found = await clients.search_qbit_for_torrent(meta, client, qbt_client=qbt_client, collect_all=True)

    assert found == hashes  # noqa: S101


@pytest.mark.asyncio
async def test_metadata_lookup_uses_reuse_source_client(monkeypatch):
    source_client = {"torrent_client": "qbit"}
    config = {
        "DEFAULT": {"default_torrent_client": "default"},
        "TORRENT_CLIENTS": {
            "default": {"torrent_client": "qbit"},
            "source": source_client,
        },
    }
    clients = Clients(config)
    called_with = None

    async def fake_lookup(_meta, client, _pathed):
        nonlocal called_with
        called_with = client
        return _meta

    monkeypatch.setattr(clients, "get_ptp_from_hash_qbit", fake_lookup)

    await clients.get_ptp_from_hash(
        Meta({}), pathed=True, client_name="source"
    )

    assert called_with is source_client
