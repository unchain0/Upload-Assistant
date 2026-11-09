from __future__ import annotations

from src.domain_models.tracker_catalog import TrackerDefinition
from src.services import tracker_catalog_service as catalog


def test_hostname_and_runtime_config_edges() -> None:
    assert catalog._hostname(None) is None
    assert catalog._hostname("   ") is None
    assert catalog._hostname("https://Example.COM/path") == "example.com"
    assert catalog._hostname("tracker.example") == "tracker.example"
    assert catalog._tracker_runtime_config({"TRACKERS": "bad"}) == {}


def test_configured_and_merged_comment_hosts() -> None:
    assert catalog._configured_comment_hosts("bad") == []
    configured = {
        "base_url": "runtime.example",
        "announce_url": "https://announce.example/passkey",
    }
    assert catalog._configured_comment_hosts(configured) == [
        "runtime.example",
        "announce.example",
    ]
    assert catalog._merged_comment_hosts(
        ("catalog.example", "runtime.example"), configured
    ) == (
        "catalog.example",
        "runtime.example",
        "announce.example",
    )


def test_tracker_comment_hosts_merges_catalog_and_runtime(monkeypatch) -> None:
    definitions = {
        "TEST": TrackerDefinition(
            "TEST",
            "unit3d_api",
            ("MOVIE",),
            ("catalog.example",),
        ),
        "EMPTY": TrackerDefinition("EMPTY", "cookies", ("TV",), ()),
    }
    monkeypatch.setattr(catalog, "TRACKER_DEFINITIONS", definitions)
    config = {
        "TRACKERS": {
            "TEST": {
                "base_url": "runtime.example",
                "announce_url": "https://catalog.example/announce",
            },
            "EMPTY": "bad",
        }
    }

    assert catalog.get_tracker_comment_hosts(config) == {
        "TEST": ("catalog.example", "runtime.example"),
    }


def test_known_tracker_includes_pseudo_trackers() -> None:
    assert catalog.is_known_tracker(" digitalcore ")
    assert catalog.is_known_tracker("manual")
    assert catalog.is_known_tracker("usenet")
    assert not catalog.is_known_tracker("definitely-missing")
