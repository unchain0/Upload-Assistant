import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.domain_models.release import Meta
from src.integrations.torrent_clients.client_manager import Clients
from src.integrations.torrent_clients.path_utils import (
    coerce_str_list,
    is_path_under,
    map_save_path,
    tracker_directory,
)
from src.integrations.torrent_clients.qbittorrent import (
    QbittorrentClientMixin,
    async_link_directory,
    create_cross_seed_links,
)


def test_qbittorrent_coerce_str_list_parses_stringified_paths() -> None:
    assert coerce_str_list("['/local', '/remote']") == ["/local", "/remote"]
    assert coerce_str_list("/local") == ["/local"]


def test_qbittorrent_map_save_path_accepts_path_objects() -> None:
    mapped_path = map_save_path(
        Path("/local/links/AMIGOSSHARE"), Path("/local"), Path("/remote")
    )

    assert mapped_path == "/remote/links/AMIGOSSHARE/"


def test_map_save_path_does_not_rewrite_sibling_paths() -> None:
    mapped_path = map_save_path("/locality/release", "/local", "/remote")

    assert mapped_path == "/locality/release/"


def test_map_save_path_preserves_case_insensitive_mapping_and_client_format() -> (
    None
):
    assert (
        map_save_path("/Local/Release", "/local", "/remote")
        == "/remote/Release/"
    )
    assert (
        map_save_path(
            "/local/Release", "/local", "/remote", trailing_slash=False
        )
        == "/remote/Release"
    )


def test_clients_remote_path_map_parses_stringified_path_lists() -> None:
    async def exercise() -> tuple[str, str]:
        clients = Clients({"TORRENT_CLIENTS": {}})
        meta = Meta({"path": "/local/content/release"})
        return await clients.remote_path_map(
            meta,
            {
                "local_path": "['/local', '/other']",
                "remote_path": "['/remote', '/elsewhere']",
            },
        )

    assert asyncio.run(exercise()) == (
        os.path.normpath("/local"),
        os.path.normpath("/remote"),
    )


def test_rtorrent_coerce_str_list_parses_stringified_paths() -> None:
    assert coerce_str_list("['/local', '/remote']") == ["/local", "/remote"]


def test_tracker_directory_falls_back_to_tracker_name() -> None:
    assert tracker_directory("/links", "", "AMIGOSSHARE") == Path(
        "/links/AMIGOSSHARE"
    )


def test_tracker_directory_rejects_paths_outside_link_root() -> None:
    for directory_name in (
        "/outside/exposed",
        "../exposed",
        "nested/exposed",
        "C:tmp",
        "C:",
        "C:/tmp",
        "C:\\tmp",
        "CON",
        "NUL",
        "AUX",
        "COM1",
        "LPT1",
        "CON.txt",
        "CON.foo.bar",
        "NUL.tar.gz",
        "COM1.backup.txt",
        "LPT9.archive.part",
    ):
        try:
            tracker_directory("/links", directory_name, "AMIGOSSHARE")
        except ValueError:
            continue
        raise AssertionError(
            f"accepted unsafe tracker directory: {directory_name}"
        )


def test_automatic_management_paths_require_path_boundaries() -> None:
    assert is_path_under("/media/local/release", "/media/local")
    assert not is_path_under("/media/locality/release", "/media/local")


def test_cross_seed_links_normalize_component_paths(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "episode.mkv").write_bytes(b"episode")
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": "Release",
                "files": [{"path": ["Season 1", "episode.mkv"], "length": 7}],
            }
        },
        name="Release",
    )
    meta = Meta(
        {
            "path": str(source_dir),
            "filelist": [str(source_dir / "episode.mkv")],
        }
    )

    async def exercise() -> bool:
        with patch(
            "src.integrations.torrent_clients.qbittorrent.async_link_directory",
            new=AsyncMock(return_value=True),
        ):
            return await create_cross_seed_links(
                meta, torrent, str(tmp_path / "tracker"), use_hardlink=False
            )

    assert asyncio.run(exercise())


def test_cross_seed_links_reject_torrent_name_outside_tracker_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"episode")
    tracker_dir = tmp_path / "tracker"
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": "../escaped",
                "files": [{"path": ["episode.mkv"], "length": 7}],
            }
        },
        name="../escaped",
    )
    meta = Meta(path=str(source), filelist=[str(source)])

    assert not asyncio.run(
        create_cross_seed_links(
            meta, torrent, str(tracker_dir), use_hardlink=False
        )
    )
    assert not (tmp_path / "escaped").exists()


def test_cross_seed_links_reject_single_file_name_outside_tracker_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.mkv"
    source.write_bytes(b"episode")
    tracker_dir = tmp_path / "tracker"
    torrent = SimpleNamespace(
        metainfo={
            "info": {"name": "../escaped", "length": source.stat().st_size}
        },
        name="../escaped",
    )
    meta = Meta(path=str(source), filelist=[str(source)])

    assert not asyncio.run(
        create_cross_seed_links(
            meta, torrent, str(tracker_dir), use_hardlink=False
        )
    )
    assert not (tmp_path / "escaped").exists()


def test_qbittorrent_maps_single_file_torrent_from_kept_source_folder(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source_file = source_dir / "01-track.mp3"
    source_file.write_bytes(b"audio")
    links = tmp_path / "links"
    torrent = SimpleNamespace(
        metainfo={
            "info": {
                "name": source_file.name,
                "length": source_file.stat().st_size,
            }
        },
        name=source_file.name,
        infohash="abc123",
    )
    meta = Meta(
        path=str(source_dir),
        filelist=[str(source_file)],
        keep_folder=True,
        qbit_cat="",
    )
    client = {
        "linking": "hardlink",
        "linked_folder": [str(links)],
        "qbit_cat": "",
        "content_layout": "Original",
    }
    qbit = QbittorrentClientMixin()
    qbit.config = {"TRACKERS": {"ZENITH": {}}, "TORRENT_CLIENTS": {}}

    async def exercise() -> None:
        with (
            patch.object(
                qbit,
                "init_qbittorrent_client",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "src.integrations.torrent_clients.qbittorrent.create_cross_seed_links",
                new=AsyncMock(return_value=True),
            ) as mapper,
            patch(
                "src.integrations.torrent_clients.qbittorrent.async_link_directory",
                new=AsyncMock(return_value=True),
            ) as direct_link,
        ):
            await qbit.qbittorrent(
                path=str(source_dir),
                torrent=torrent,
                local_path=str(tmp_path),
                remote_path=str(tmp_path),
                client=client,
                _is_disc="",
                filelist=[str(source_file)],
                meta=meta,
                tracker="ZENITH",
            )
            mapper.assert_awaited_once()
            direct_link.assert_not_awaited()

    asyncio.run(exercise())


def test_async_link_directory_reuses_matching_hardlink(tmp_path: Path) -> None:
    source = tmp_path / "source.m4b"
    destination = tmp_path / "links" / "source.m4b"
    source.write_bytes(b"audiobook")
    destination.parent.mkdir()
    os.link(source, destination)

    assert asyncio.run(async_link_directory(str(source), str(destination)))
    assert source.samefile(destination)


def test_async_link_directory_rejects_stale_file(tmp_path: Path) -> None:
    source = tmp_path / "source.m4b"
    destination = tmp_path / "links" / "source.m4b"
    source.write_bytes(b"current audiobook")
    destination.parent.mkdir()
    destination.write_bytes(b"stale audiobook")

    assert not asyncio.run(async_link_directory(str(source), str(destination)))
    assert destination.read_bytes() == b"stale audiobook"


def test_async_link_directory_repairs_partial_tree_and_rejects_stale_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "links" / "source"
    source.mkdir()
    destination.mkdir(parents=True)
    first_source = source / "01.mp3"
    second_source = source / "02.mp3"
    first_destination = destination / "01.mp3"
    second_destination = destination / "02.mp3"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    os.link(first_source, first_destination)

    assert asyncio.run(async_link_directory(str(source), str(destination)))
    assert second_source.samefile(second_destination)

    second_destination.unlink()
    second_destination.write_bytes(b"stale")
    assert not asyncio.run(async_link_directory(str(source), str(destination)))
    assert second_destination.read_bytes() == b"stale"
