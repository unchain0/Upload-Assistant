from types import SimpleNamespace

from src.domain_models.release import Meta
from src.integrations.torrent_clients.client_manager import Clients
from src.integrations.trackers.orpheus import Orpheus


def test_recognizes_brasiltracker_host_without_loading_tracker_classes():
    client = Clients({"TRACKERS": {}})

    tracker_ids = client._extract_tracker_ids_from_comment(
        "https://brasiltracker.org/torrents.php?id=39237"
    )

    assert tracker_ids == {}
    assert "BRASILTRACKER" in client._tracker_comment_hosts


def test_uses_configured_announce_host():
    client = Clients(
        {
            "TRACKERS": {
                "BRASILTRACKER": {
                    "announce_url": "https://custom.brasiltracker.org/announce/token"
                }
            }
        }
    )

    tracker_ids = client._extract_tracker_ids_from_comment(
        "https://custom.brasiltracker.org/torrents/12345"
    )

    assert tracker_ids == {"brasiltracker": "12345"}


def test_uses_orpheus_default_host_without_config_override():
    client = Clients({"TRACKERS": {}})

    tracker_ids = client._extract_tracker_ids_from_comment(
        "https://orpheus.network/torrents.php?torrentid=42"
    )

    assert tracker_ids == {"orpheus": "42"}


def test_uses_aither_torrent_comment_id():
    client = Clients({"TRACKERS": {}})

    tracker_ids = client._extract_tracker_ids_from_comment(
        "This torrent was downloaded from Aither.cc. https://aither.cc/torrents/50049"
    )

    assert tracker_ids == {"aither": "50049"}


def test_matches_single_file_inside_folder_torrent_content_path():
    client = Clients({"TRACKERS": {}})
    media = r"F:\Filmes\Heat\Heat.mkv"
    torrent = SimpleNamespace(name="Heat", content_path=media)

    assert client._matches_qbit_content_path(
        torrent, Meta({"path": media, "uuid": "Heat.mkv"})
    )


def test_matches_torrent_name_when_content_path_is_unavailable():
    client = Clients({"TRACKERS": {}})
    media = r"F:\Filmes\Heat\Heat.mkv"
    torrent = SimpleNamespace(name="Heat.mkv", content_path="")

    assert client._matches_qbit_content_path(
        torrent,
        Meta({"path": media, "filelist": [media], "uuid": "different"}),
    )


def test_orpheus_uses_its_fixed_base_url():
    tracker = Orpheus(
        {"TRACKERS": {"ORPHEUS": {"base_url": "https://not-orheus.example"}}}
    )

    assert tracker.base_url == "https://orpheus.network"
