from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import aither as aither_module
from src.integrations.trackers.UNIT3D.aither import Aither


def _tracker() -> Aither:
    return Aither({"DEFAULT": {}, "TRACKERS": {"AITHER": {}}})


def test_aither_additional_checks_accept_valid_disc() -> None:
    tracker = _tracker()
    assert asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=True))
    )


@pytest.mark.parametrize(
    ("hdr", "expected"),
    (
        ("", {}),
        ("DV", {"dv": 1}),
        ("HDR", {"hdr": 1}),
        ("HLG", {"hdr": 1}),
        ("HDR10+", {"hdr10p": 1}),
        ("DV HDR10+", {"dv": 1, "hdr10p": 1}),
    ),
)
def test_aither_hdr_flag_edges(hdr: str, expected: dict[str, int]) -> None:
    assert Aither._hdr_flags(hdr) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    (
        (
            Meta(
                category="MOVIE",
                year=2025,
                manual_year=0,
                no_year=False,
            ),
            "2025",
        ),
        (
            Meta(
                category="TV",
                year=2025,
                search_year="",
                manual_year=0,
                no_year=False,
            ),
            "",
        ),
        (
            Meta(
                category="TV",
                year=2025,
                search_year="2025",
                manual_year=0,
                no_year=False,
            ),
            "2025",
        ),
        (
            Meta(
                category="MOVIE",
                year=2025,
                manual_year=2024,
                no_year=False,
            ),
            "2024",
        ),
        (
            Meta(
                category="MOVIE",
                year=2025,
                manual_year=2024,
                no_year=True,
            ),
            "",
        ),
    ),
)
def test_aither_resolved_year_edges(meta: Meta, expected: str) -> None:
    assert Aither._resolved_year(meta) == expected


def test_aither_foreign_language_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    process = AsyncMock(return_value=None)
    has_english = AsyncMock(return_value=False)
    monkeypatch.setattr(
        aither_module.languages_manager,
        "process_desc_language",
        process,
    )
    monkeypatch.setattr(
        aither_module.languages_manager,
        "has_english_language",
        has_english,
    )

    assert (
        asyncio.run(
            tracker._foreign_language(
                Meta(
                    language_checked=False,
                    audio_languages=["French"],
                )
            )
        )
        == "FRENCH"
    )
    process.assert_awaited_once()

    assert (
        asyncio.run(
            tracker._foreign_language(
                Meta(language_checked=True, audio_languages=[])
            )
        )
        == ""
    )

    has_english.return_value = True
    assert (
        asyncio.run(
            tracker._foreign_language(
                Meta(
                    language_checked=True,
                    audio_languages=["English"],
                )
            )
        )
        == ""
    )


@pytest.mark.parametrize(
    ("meta", "name", "year", "name_type", "source", "language", "expected"),
    (
        (
            Meta(resolution="1080p", is_disc=""),
            "Movie 1080p WEB-DL",
            "2025",
            "WEBDL",
            "WEB",
            "FRENCH",
            "Movie FRENCH 1080p WEB-DL",
        ),
        (
            Meta(resolution="1080p", is_disc="BDMV"),
            "Movie 1080p BluRay",
            "2025",
            "DISC",
            "BluRay",
            "FRENCH",
            "Movie 1080p BluRay",
        ),
        (
            Meta(resolution="576p", is_disc=""),
            "Movie 2025 DVD REMUX",
            "2025",
            "REMUX",
            "DVD",
            "FRENCH",
            "Movie 2025 FRENCH DVD REMUX",
        ),
        (
            Meta(resolution="576p", is_disc=""),
            "Movie DVD REMUX",
            "",
            "REMUX",
            "DVD",
            "FRENCH",
            "Movie DVD REMUX",
        ),
        (
            Meta(resolution="1080p", is_disc=""),
            "Movie 1080p WEB-DL",
            "2025",
            "WEBDL",
            "WEB",
            "",
            "Movie 1080p WEB-DL",
        ),
    ),
)
def test_aither_foreign_language_name_edges(
    meta: Meta,
    name: str,
    year: str,
    name_type: str,
    source: str,
    language: str,
    expected: str,
) -> None:
    assert (
        Aither._apply_foreign_language(
            meta,
            name,
            year,
            name_type,
            source,
            language,
        )
        == expected
    )


def test_aither_dvd_naming_helpers() -> None:
    dvdrip = Meta(
        source="DVD",
        video_encode="x264",
        audio="AAC",
    )
    assert (
        Aither._dvdrip_name(
            dvdrip,
            "Movie DVD x264 DVDRip AAC",
            "576p",
        )
        == "Movie  576p DVDRip AACx264"
    )

    disc = Meta(region="R1", audio="AAC")
    assert (
        Aither._dvd_disc_name(
            disc,
            "Movie R1 DVD AAC",
            "576p",
            "MPEG2",
            "DVD",
        )
        == "Movie 576p R1 DVD MPEG2 AAC"
    )

    remux = Meta(source="DVD", audio="AAC")
    assert (
        Aither._dvd_remux_name(
            remux,
            "Movie DVD AAC",
            "576p",
            "MPEG2",
        )
        == "Movie 576p DVD MPEG2 AAC"
    )

    assert Aither._joined_name_parts("R1", "", None, "DVD") == "R1 DVD"


def test_aither_dvd_adjustment_routing() -> None:
    dvdrip = Meta(
        source="DVD",
        video_encode="x264",
        audio="AAC",
    )
    assert "576p DVDRip" in Aither._dvd_adjusted_name(
        dvdrip,
        "Movie DVD x264 DVDRip AAC",
        "576p",
        "MPEG2",
        "DVDRIP",
        "DVD",
    )

    disc = Meta(is_disc="DVD", region="R1", audio="AAC")
    assert Aither._dvd_disc_applies(disc, "DISC", "DVD")
    assert "576p R1 DVD" in Aither._dvd_adjusted_name(
        disc,
        "Movie R1 DVD AAC",
        "576p",
        "MPEG2",
        "DISC",
        "DVD",
    )

    inferred_disc = Meta(is_disc="", region="R1", audio="AAC")
    assert Aither._dvd_disc_applies(inferred_disc, "DISC", "DVD")
    assert "576p R1 DVD" in Aither._dvd_adjusted_name(
        inferred_disc,
        "Movie R1 DVD AAC",
        "576p",
        "MPEG2",
        "DISC",
        "DVD",
    )

    remux = Meta(source="DVD", audio="AAC")
    assert Aither._foreign_dvd_remux("REMUX", "DVD")
    assert "576p DVD" in Aither._dvd_adjusted_name(
        remux,
        "Movie DVD AAC",
        "576p",
        "MPEG2",
        "REMUX",
        "DVD",
    )

    untouched = Meta(source="WEB", audio="AAC")
    assert not Aither._dvd_disc_applies(untouched, "WEBDL", "WEB")
    assert not Aither._foreign_dvd_remux("WEBDL", "WEB")
    assert (
        Aither._dvd_adjusted_name(
            untouched,
            "Movie WEB AAC",
            "1080p",
            "H264",
            "WEBDL",
            "WEB",
        )
        == "Movie WEB AAC"
    )


def test_aither_final_name_trump_and_alt_title_ordering() -> None:
    meta = Meta(
        aka="Alternate",
        no_aka=False,
        trump_reason="exact_match",
    )
    assert (
        Aither._final_name(
            meta,
            "Movie 2025 Alternate 1080p",
            "2025",
        )
        == "Movie Alternate 2025 1080p - TRUMP"
    )

    no_aka = Meta(aka="Alternate", no_aka=True)
    assert (
        Aither._final_name(
            no_aka,
            "Movie 2025 Alternate 1080p",
            "2025",
        )
        == "Movie 2025 Alternate 1080p"
    )


def test_aither_public_checks_and_additional_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="", valid_mi=True))
    )

    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=False))
    )
    assert asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=True))
    )

    monkeypatch.setattr(tracker, "get_flag", AsyncMock(return_value=1))
    assert asyncio.run(tracker.get_additional_data(Meta(hdr="HLG"))) == {
        "mod_queue_opt_in": 1,
        "hdr": 1,
    }


def test_aither_public_get_name_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        aither_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=True),
    )
    meta = Meta(
        category="MOVIE",
        name="Movie 2025 1080p WEB-DL",
        year=2025,
        manual_year=0,
        no_year=False,
        language_checked=True,
        audio_languages=["English"],
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        video_codec="H264",
        aka="",
        no_aka=True,
    )

    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie 2025 1080p WEB-DL"
    }
