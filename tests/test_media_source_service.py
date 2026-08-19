from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.services import media_source_service


def _write_mediainfo(tmp_path: Path, folder: str, tracks: list[dict[str, Any]]) -> None:
    target = tmp_path / "tmp" / folder
    target.mkdir(parents=True, exist_ok=True)
    (target / "MediaInfo.json").write_text(json.dumps({"media": {"track": tracks}}), encoding="utf-8")


def _source(type_name: str, video: str, is_disc: str, meta: Meta, tmp_path: Path) -> tuple[str, str]:
    return asyncio.run(media_source_service.get_source(type_name, video, str(tmp_path), is_disc, meta, "release", str(tmp_path)))


def test_manual_bluray_web_hddvd_and_ultra_hdtv_sources(tmp_path: Path) -> None:
    assert _source("DISC", "Movie", "", Meta(manual_source="BluRay"), tmp_path) == ("Blu-ray", "DISC")
    assert _source("ENCODE", "Movie", "", Meta(manual_source="BluRay"), tmp_path) == ("BluRay", "ENCODE")
    assert _source("ENCODE", "Movie", "", Meta(manual_source="WEB"), tmp_path) == ("Web", "WEBRIP")
    assert _source("WEBDL", "Movie", "", Meta(manual_source="BluRay"), tmp_path) == ("Web", "WEBDL")
    assert _source("REMUX", "Movie", "HDDVD", Meta(manual_source="HD-DVD"), tmp_path) == ("HDDVD", "REMUX")
    assert _source("DISC", "Movie", "HDDVD", Meta(manual_source="HD DVD"), tmp_path) == ("HD DVD", "DISC")
    assert _source("ENCODE", "Movie", "", Meta(manual_source="Ultra HDTV"), tmp_path) == ("UHDTV", "ENCODE")


def test_guessit_primary_fallback_and_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fallback(value: str, _options=None) -> dict[str, str]:
        calls.append(value)
        if value == "video":
            raise ValueError("bad video")
        return {"source": "WEB"}

    monkeypatch.setattr(media_source_service, "guessit_fn", fallback)
    assert _source("ENCODE", "video", "", Meta(), tmp_path) == ("Web", "WEBRIP")
    assert calls == ["video", str(tmp_path)]

    monkeypatch.setattr(media_source_service, "guessit_fn", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad")))
    assert _source("ENCODE", "video", "", Meta(), tmp_path) == ("BluRay", "ENCODE")


def test_dvd_system_from_mediainfo_and_remux(tmp_path: Path) -> None:
    _write_mediainfo(tmp_path, "release", [{"@type": "General"}, {"@type": "Video", "Standard": "PAL", "FrameRate": "25"}])
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("PAL", "DISC")
    assert _source("REMUX", "Movie", "DVD", Meta(), tmp_path) == ("PAL DVD", "REMUX")


def test_dvd_system_falls_back_to_guessit_and_frame_rate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_mediainfo(tmp_path, "release", [{"@type": "General"}, {"@type": "Video", "Standard": "SECAM", "FrameRate": "25.000"}])
    monkeypatch.setattr(media_source_service, "guessit_fn", lambda *_args, **_kwargs: {"source": "DVD", "other": ["PAL"]})
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("PAL", "DISC")

    monkeypatch.setattr(media_source_service, "guessit_fn", lambda *_args, **_kwargs: {"source": "DVD", "other": ["NTSC"]})
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("NTSC", "DISC")

    monkeypatch.setattr(media_source_service, "guessit_fn", lambda *_args, **_kwargs: {"source": "DVD", "other": []})
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("PAL", "DISC")

    _write_mediainfo(tmp_path, "release", [{"@type": "General"}, {"@type": "Video", "Standard": "", "FrameRate": "23.976"}])
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("NTSC", "DISC")

    _write_mediainfo(tmp_path, "release", [{"@type": "General"}, {"@type": "Video", "Standard": "", "FrameRate": ""}])
    assert _source("DISC", "Movie", "DVD", Meta(), tmp_path) == ("", "DISC")


def test_dvd_fallback_handles_guess_and_media_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_mediainfo(tmp_path, "release", [])
    monkeypatch.setattr(media_source_service, "guessit_fn", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad guess")))
    assert _source("DISC", "Movie", "DVD", Meta(manual_source="DVD"), tmp_path) == ("", "DISC")

    (tmp_path / "tmp" / "release" / "MediaInfo.json").write_text("not json", encoding="utf-8")
    assert _source("DISC", "Movie", "DVD", Meta(manual_source="DVD"), tmp_path) == ("", "DISC")


def test_outer_errors_fall_back_to_bluray(tmp_path: Path) -> None:
    class BadSource:
        def __eq__(self, _other: object) -> bool:
            raise RuntimeError("bad source")

    meta = Meta()
    meta.manual_source = BadSource()  # type: ignore[assignment]
    assert _source("ENCODE", "Movie", "", meta, tmp_path) == ("BluRay", "ENCODE")
