"""End-to-end contracts for the preparation helper orchestration paths."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

import src.services.preparation_helpers as helpers
from src.domain_models.release import Meta
from tests.test_service_contracts import (
    _config,
    _ManagerPort,
    _meta,
    _PreparationPort,
    _VideoPort,
)


def _bdinfo() -> dict[str, Any]:
    return {
        "title": "Example Release 2024",
        "label": "Example Release 2024",
        "size": 25_000_000_000,
        "playlist": "00000.MPLS",
        "video": [
            {
                "fps": "24.000",
                "3d": "",
                "codec": "AVC",
                "resolution": "1080p",
                "bitrate": "20000 kbps",
            }
        ],
        "audio": [
            {"codec": "E-AC-3", "channels": "5.1", "bitrate": "640 kbps"}
        ],
        "subtitles": ["English"],
    }


def _configure_boundaries(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any], root: Path
) -> None:
    manager = _ManagerPort(config, root)
    monkeypatch.setattr(helpers, "video_manager", _VideoPort(config, root))
    monkeypatch.setattr(helpers, "imdb_manager", manager)
    monkeypatch.setattr(helpers, "tvmaze_manager", manager)

    async def export_info(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {
            "media": {
                "track": [
                    {
                        "@type": "General",
                        "Format": "Matroska",
                        "Duration": "7200",
                        "UniqueID": "1",
                        "OverallBitRate": "12000000",
                    },
                    {
                        "@type": "Video",
                        "Format": "AVC",
                        "Width": "1920",
                        "Height": "1080",
                        "FrameRate": "24.000",
                        "BitRate": "12000000",
                        "Encoded_Library_Settings": "cabac=1",
                    },
                    {
                        "@type": "Audio",
                        "Format": "E-AC-3",
                        "Channels": "6",
                        "BitRate": "640000",
                        "Language": "en",
                    },
                ]
            }
        }

    async def region(_value: object = None) -> str:
        return "USA"

    async def distributor(_value: object = None) -> str:
        return "CRITERION"

    async def service(
        *_args: object, **kwargs: object
    ) -> tuple[str, str] | dict[str, str]:
        if kwargs.get("get_services_only"):
            return {"Amazon": "AMZN", "Netflix": "NF"}
        return "AMZN", "Amazon"

    async def source(
        release_type: str, *_args: object, **_kwargs: object
    ) -> tuple[str, str]:
        return "WEB", release_type or "WEBDL"

    async def edition(
        *_args: object, **_kwargs: object
    ) -> tuple[str, str, bool]:
        return "", "", False

    async def tag(_video: str, meta: Meta, **_kwargs: object) -> str:
        return meta.tag or "-GROUP"

    async def tag_override(meta: Meta) -> Meta:
        return meta

    async def releases(_meta: Meta) -> list[dict[str, Any]]:
        return []

    async def no_conformance(_meta: Meta) -> bool:
        return False

    monkeypatch.setattr(helpers, "export_info", export_info)
    monkeypatch.setattr(helpers, "get_region", region)
    monkeypatch.setattr(helpers, "get_distributor", distributor)
    monkeypatch.setattr(helpers, "get_service", service)
    monkeypatch.setattr(helpers, "get_source", source)
    monkeypatch.setattr(helpers, "get_edition", edition)
    monkeypatch.setattr(helpers, "get_tag", tag)
    monkeypatch.setattr(helpers, "tag_override", tag_override)
    monkeypatch.setattr(helpers, "get_bluray_releases", releases)
    monkeypatch.setattr(helpers, "get_conformance_error", no_conformance)
    monkeypatch.setattr(
        helpers, "validate_mediainfo", lambda *_args, **_kwargs: True
    )


def _text_or(value: str, fallback: str) -> str:
    return value or fallback


def _mapping_or(
    value: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    return value or fallback


def _meta_path(meta: Meta) -> str:
    return str(meta.path or "")


@pytest.mark.parametrize(
    ("profile", "category", "disc_type"),
    [
        (0, "MOVIE", ""),
        (1, "TV", ""),
        (2, "MUSIC", ""),
        (3, "BOOK", ""),
        (4, "GAME", ""),
        (5, "XXX", ""),
        (6, "MOVIE", "BDMV"),
        (7, "MOVIE", "DVD"),
        (8, "MOVIE", "HDDVD"),
    ],
)
def test_preparation_helpers_complete_representative_release_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: int,
    category: str,
    disc_type: str,
) -> None:
    config = _config()
    config["DEFAULT"].update(
        {
            "use_sonarr": True,
            "use_radarr": True,
            "ping_unit3d": True,
            "user_overrides": True,
            "tracker_description_mode": "text",
            "cutoff_screens": 1,
            "skip_auto_torrent": False,
        }
    )
    _configure_boundaries(monkeypatch, config, tmp_path)
    prep = _PreparationPort(config, tmp_path)
    meta = _meta(tmp_path, profile % 6)
    meta.category = category
    meta.manual_category = category
    meta.is_disc = disc_type
    meta.discs = []
    meta.tmdb_id = 123
    meta.imdb_id = 1234567
    meta.tvdb_id = 456
    meta.tvmaze_id = 789
    meta.mal_id = 0
    meta.skip_trackers = False
    meta.matched_tracker = False
    meta.no_override = False
    meta.region = "US"
    meta.distributor = "Criterion"
    meta.original_language = "en"
    meta.search_year = 2024
    meta.year = 2024
    bdinfo = _bdinfo() if disc_type == "BDMV" else {}
    if disc_type:
        meta.discs = [
            {
                "name": "DISC",
                "type": disc_type,
                "bdinfo": copy.deepcopy(bdinfo),
            }
        ]

    async def exercise() -> None:
        (
            video_location,
            detected_bdinfo,
        ) = await helpers.detect_disc_and_category(prep, meta)
        video_location = _text_or(video_location, _meta_path(meta))
        effective_bdinfo = _mapping_or(bdinfo, detected_bdinfo)
        (
            filename,
            untouched_filename,
            video_path,
            search_term,
            search_file_folder,
            mediainfo,
            video,
        ) = await helpers.process_media_files(
            prep, meta, video_location, effective_bdinfo
        )
        video_path = _text_or(video_path, _meta_path(meta))
        filename = _text_or(filename, _text_or(meta.title, "Example Release"))
        untouched_filename = _text_or(untouched_filename, filename)
        effective_mediainfo = _mapping_or(mediainfo, meta.mediainfo)
        await helpers.search_metadata(
            prep,
            meta,
            filename,
            untouched_filename,
            video_path,
            _text_or(search_term, filename),
            _text_or(search_file_folder, "file"),
            True,
            True,
            False,
            _ManagerPort(config, tmp_path),
            effective_bdinfo,
            effective_mediainfo,
        )
        await helpers.finalize_metadata(
            prep,
            meta,
            video_path,
            effective_bdinfo,
            effective_mediainfo,
            filename,
            untouched_filename,
            _text_or(video, video_path),
        )

    asyncio.run(exercise())
    assert meta.category == category
    assert meta.title
    assert meta.source
    assert meta.type
    assert meta.resolution
