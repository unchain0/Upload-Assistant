from __future__ import annotations

from src.domain_models.tracker_catalog import KNOWN_TRACKERS, TRACKER_DEFINITIONS
from src.integrations.trackers.registry import tracker_class_map


def test_domain_tracker_catalog_matches_concrete_registry() -> None:
    assert frozenset(tracker_class_map) == KNOWN_TRACKERS


def test_tracker_catalog_preserves_auth_and_category_capabilities() -> None:
    for tracker_name, tracker_class in tracker_class_map.items():
        definition = TRACKER_DEFINITIONS[tracker_name]
        assert definition.name == tracker_name
        assert definition.auth_type == str(getattr(tracker_class, "auth_type", "") or "")
        assert definition.supported_categories == tuple(str(value).upper() for value in (getattr(tracker_class, "supported_categories", ()) or ()))
        assert definition.is_usenet is bool(getattr(tracker_class, "is_usenet", False))
