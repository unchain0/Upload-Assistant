from __future__ import annotations

from pathlib import Path

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D.portugas import Portugas


def _tracker() -> Portugas:
    return Portugas({"DEFAULT": {}, "TRACKERS": {"PORTUGAS": {}}})


def test_portugas_nogroup_name_cleanup() -> None:
    tracker = _tracker()
    meta = Meta(name="Movie 1080p-UNKNOWN", tag="unknown")
    assert tracker._needs_nogroup_tag("")
    assert tracker._needs_nogroup_tag("unknown")
    assert tracker._strip_invalid_group_tags("Movie-unknown") == "Movie"
    assert __import__("asyncio").run(tracker.get_name(meta)) == {"name": "Movie.1080p-NOGROUP"}


def test_portugas_mediainfo_missing_text_is_false() -> None:
    assert not Portugas._mediainfo_has_portuguese(Meta(), "Audio")


def test_portugas_section_accepts_portuguese_and_rejects_brazilian() -> None:
    assert Portugas._section_is_portuguese("Language : Portuguese\nTitle : European")
    assert not Portugas._section_is_portuguese("Language : Portuguese\nTitle : Brazilian")
    assert not Portugas._section_is_portuguese("Language : Portuguese (BR)")


def test_portugas_read_text_handles_race_and_generic_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "MEDIAINFO.txt"
    path.write_text("data", encoding="utf-8")

    real_read_text = Path.read_text

    def missing_read_text(current: Path, *args: object, **kwargs: object) -> str:
        if current == path:
            raise FileNotFoundError(path)
        return real_read_text(current, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", missing_read_text)
    assert Portugas._read_text_file(path) == ""

    def broken_read_text(current: Path, *args: object, **kwargs: object) -> str:
        if current == path:
            raise OSError("broken")
        return real_read_text(current, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_read_text)
    assert Portugas._read_text_file(path) == ""
