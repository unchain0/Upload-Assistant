from pathlib import Path

import pytest

from src.integrations.torrent_clients.path_utils import (
    coerce_str_list,
    is_path_under,
    tracker_directory,
)


def test_coerce_str_list_parses_literal_sequence() -> None:
    assert coerce_str_list("['/one', '', None, '/two']") == [
        "/one",
        "/two",
    ]
    assert coerce_str_list("[broken]") == ["[broken]"]
    assert coerce_str_list(123) == ["123"]


def test_is_path_under_rejects_shorter_path() -> None:
    assert not is_path_under("/local", "/local/release")


def test_tracker_directory_rejects_unsafe_component(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid tracker link directory"):
        tracker_directory(tmp_path, "../escape", "TRACKER")
