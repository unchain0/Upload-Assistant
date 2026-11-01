from __future__ import annotations

import asyncio

import pytest

from src.integrations.torrent_clients.origin_gate import (
    announce_host,
    check_origin,
    configured_origin_hosts,
    evaluate_origin,
)

CONFIG = {
    "TRACKERS": {
        "CAPYBARABR": {"announce_url": "https://capybarabr.com/announce/abc"},
        "MYANONAMOUSE": {
            "announce_url": "https://t.myanonamouse.net/tracker.php/x"
        },
        "DIGITALCORE": {
            "announce_url": "https://digitalcore.club/announce/xyz"
        },
    }
}


def test_configured_hosts_are_derived_from_the_tracker_config() -> None:
    assert configured_origin_hosts(CONFIG) == {
        "capybarabr.com",
        "t.myanonamouse.net",
        "digitalcore.club",
    }


def test_announce_host_normalizes_urls() -> None:
    assert (
        announce_host("https://CapybaraBR.com/announce/x") == "capybarabr.com"
    )
    assert announce_host("capybarabr.com/announce/x") == "capybarabr.com"
    assert announce_host("") is None


def test_private_origin_is_allowed() -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=["https://capybarabr.com/announce/abc"],
        allowed_hosts=configured_origin_hosts(CONFIG),
    )
    assert result.allowed is True


@pytest.mark.parametrize(
    "url",
    [
        "udp://tracker.opentrackr.org:1337/announce",
        "https://nyaa.si/announce",
    ],
)
def test_public_origin_is_skipped(url: str) -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=[url],
        allowed_hosts=configured_origin_hosts(CONFIG),
    )
    assert result.allowed is False
    assert "not a configured private tracker" in result.reason


def test_torrent_without_trackers_is_skipped() -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=[],
        allowed_hosts=configured_origin_hosts(CONFIG),
    )
    assert result.allowed is False
    assert "DHT only" in result.reason


def test_unknown_host_is_skipped() -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=["https://unknown.example/announce"],
        allowed_hosts=configured_origin_hosts(CONFIG),
    )
    assert result.allowed is False


def test_manual_run_is_not_blocked() -> None:
    result = evaluate_origin(
        unattended=False,
        tracker_urls=["https://nyaa.si/announce"],
        allowed_hosts=configured_origin_hosts(CONFIG),
    )
    assert result.allowed is True
    assert result.reason == "manual run"


def test_qbittorrent_failure_fails_closed() -> None:
    async def failing(_hash: str) -> list[str]:
        raise TimeoutError("qBittorrent timed out")

    result = asyncio.run(
        check_origin(
            unattended=True,
            tracker_hash="abc123",
            config=CONFIG,
            fetch_tracker_urls=failing,
        )
    )
    assert result.allowed is False
    assert "lookup failed" in result.reason


def test_unattended_without_hash_fails_closed() -> None:
    async def never_called(_hash: str) -> list[str]:
        raise AssertionError("must not be called without a hash")

    result = asyncio.run(
        check_origin(
            unattended=True,
            tracker_hash=None,
            config=CONFIG,
            fetch_tracker_urls=never_called,
        )
    )
    assert result.allowed is False
    assert "without a torrent hash" in result.reason


def test_check_origin_allows_a_private_origin() -> None:
    async def private(_hash: str) -> list[str]:
        return ["https://capybarabr.com/announce/abc"]

    result = asyncio.run(
        check_origin(
            unattended=True,
            tracker_hash="abc123",
            config=CONFIG,
            fetch_tracker_urls=private,
        )
    )
    assert result.allowed is True


def test_dht_pex_lsd_sentinels_are_ignored() -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=[
            "** [DHT] **",
            "** [PeX] **",
            "** [LSD] **",
            "https://immortalseed.me/announce.php?passkey=abc",
        ],
        allowed_hosts={"immortalseed.me"},
    )
    assert result.allowed is True


def test_only_sentinels_is_skipped() -> None:
    result = evaluate_origin(
        unattended=True,
        tracker_urls=["** [DHT] **", "** [PeX] **", "** [LSD] **"],
        allowed_hosts={"immortalseed.me"},
    )
    assert result.allowed is False
    assert "no usable announce URL" in result.reason


def test_ipv6_announce_host_is_parsed() -> None:
    assert announce_host("udp://[2001:db8::1]:1337/announce") == "2001:db8::1"

    result = evaluate_origin(
        unattended=True,
        tracker_urls=["udp://[2001:db8::1]:1337/announce"],
        allowed_hosts={"2001:db8::1"},
    )
    assert result.allowed is True


def test_runtime_torrent_hash_reads_the_cli_flag() -> None:
    import upload

    assert (
        upload._runtime_torrent_hash(
            [
                "--torrenthash",
                "8305575ce0fd222c97a1b26de39e3b227f1a923d",
                "--unattended",
            ]
        )
        == "8305575ce0fd222c97a1b26de39e3b227f1a923d"
    )
    assert upload._runtime_torrent_hash(["-th", "abc"]) == "abc"
    assert upload._runtime_torrent_hash(["--unattended", "path"]) is None
