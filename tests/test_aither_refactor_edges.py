from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import aither as aither_module
from src.integrations.trackers.UNIT3D.aither import Aither


def _tracker() -> Aither:
    return Aither({"DEFAULT": {}, "TRACKERS": {"AITHER": {}}})


def test_aither_positive_checks_and_hdr_flag_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    assert asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="", valid_mi=True))
    )
    tracker.common.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="", valid_mi=True))
    )
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=False))
    )

    monkeypatch.setattr(tracker, "get_flag", AsyncMock(return_value=1))
    assert asyncio.run(tracker.get_additional_data(Meta(hdr="DV HDR10+"))) == {
        "mod_queue_opt_in": 1,
        "dv": 1,
        "hdr10p": 1,
    }
    assert tracker._hdr_flags("DV HDR") == {"dv": 1, "hdr": 1}


def test_aither_year_helper_edges() -> None:
    tracker = _tracker()
    assert tracker._base_year(Meta(category="MOVIE", year=None)) == ""
    assert (
        tracker._base_year(Meta(category="TV", year=2025, search_year=""))
        == ""
    )
    assert (
        tracker._base_year(Meta(category="TV", year=2025, search_year=2025))
        == "2025"
    )
    assert tracker._manual_year(Meta(manual_year=2024)) == "2024"
    assert tracker._manual_year(Meta(manual_year=0)) == ""
    assert (
        tracker._resolved_year(
            Meta(category="MOVIE", year=2025, manual_year=2024)
        )
        == "2024"
    )
    assert (
        tracker._resolved_year(
            Meta(category="MOVIE", year=2025, manual_year=2024, no_year=True)
        )
        == ""
    )


def test_aither_foreign_language_helper_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    process = AsyncMock(return_value=None)
    english = AsyncMock(return_value=True)
    monkeypatch.setattr(
        aither_module.languages_manager,
        "process_desc_language",
        process,
    )
    monkeypatch.setattr(
        aither_module.languages_manager,
        "has_english_language",
        english,
    )

    empty = Meta(language_checked=False, audio_languages=[])
    assert asyncio.run(tracker._foreign_language(empty)) == ""
    process.assert_awaited_once()

    meta = Meta(language_checked=True, audio_languages=["English"])
    assert asyncio.run(tracker._foreign_language(meta)) == ""
    english.assert_awaited_once_with(["English"])

    monkeypatch.setattr(
        aither_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=False),
    )
    foreign = Meta(language_checked=True, audio_languages=["French"])
    assert asyncio.run(tracker._foreign_language(foreign)) == "FRENCH"


def test_aither_foreign_language_application_edges() -> None:
    tracker = _tracker()
    remux = Meta(
        is_disc="",
        resolution="480p",
        audio="AAC",
        video_encode="x264",
    )
    assert (
        tracker._apply_foreign_language(
            remux,
            "Movie 480p",
            "2025",
            "WEBDL",
            "WEB",
            "",
        )
        == "Movie 480p"
    )
    assert (
        tracker._apply_foreign_language(
            remux,
            "Movie 480p",
            "2025",
            "WEBDL",
            "WEB",
            "FRENCH",
        )
        == "Movie FRENCH 480p"
    )
    assert (
        tracker._apply_foreign_language(
            remux,
            "Movie 2025 DVD REMUX",
            "2025",
            "REMUX",
            "DVD",
            "FRENCH",
        )
        == "Movie 2025 FRENCH DVD REMUX"
    )
    assert (
        tracker._apply_foreign_language(
            remux,
            "Movie DVD REMUX",
            "",
            "REMUX",
            "DVD",
            "FRENCH",
        )
        == "Movie DVD REMUX"
    )

    bdmv = Meta(is_disc="BDMV", resolution="1080p")
    assert (
        tracker._apply_foreign_language(
            bdmv,
            "Movie 1080p",
            "2025",
            "DISC",
            "Blu-ray",
            "FRENCH",
        )
        == "Movie 1080p"
    )


def test_aither_dvd_name_helper_edges() -> None:
    tracker = _tracker()
    dvdrip = Meta(
        source="PAL DVD",
        video_encode="x264",
        audio="AAC",
    )
    assert (
        tracker._dvdrip_name(
            dvdrip,
            "Movie PAL DVD DVDRip x264 AAC",
            "576p",
        )
        == "Movie 576p DVDRip  AACx264"
    )
    assert tracker._joined_name_parts("576p", "", None, "DVD") == "576p DVD"

    disc = Meta(region="R1", audio="AAC")
    assert (
        tracker._dvd_disc_name(
            disc,
            "Movie R1 DVD AAC",
            "480p",
            "MPEG-2",
            "DVD",
        )
        == "Movie 480p R1 DVD MPEG-2 AAC"
    )

    no_region = Meta(region="", audio="AAC")
    assert (
        tracker._dvd_disc_name(
            no_region,
            "Movie AAC",
            "480p",
            "MPEG-2",
            "",
        )
        == "Movie MPEG-2 AAC"
    )


def test_aither_dvd_adjustment_and_final_name_edges() -> None:
    tracker = _tracker()
    dvdrip = Meta(source="DVD", video_encode="x264", audio="AAC")
    assert "576p DVDRip" in tracker._dvd_adjusted_name(
        dvdrip,
        "Movie DVD DVDRip x264 AAC",
        "576p",
        "MPEG-2",
        "DVDRIP",
        "DVD",
    )

    disc = Meta(is_disc="DVD", region="R2", audio="AAC")
    assert "480p R2 DVD" in tracker._dvd_adjusted_name(
        disc,
        "Movie R2 DVD AAC",
        "480p",
        "MPEG-2",
        "DISC",
        "DVD",
    )

    inferred_disc = Meta(is_disc="", region="R2", audio="AAC")
    assert "480p R2 DVD" in tracker._dvd_adjusted_name(
        inferred_disc,
        "Movie R2 DVD AAC",
        "480p",
        "MPEG-2",
        "DISC",
        "DVD",
    )

    regular = Meta(is_disc="", source="WEB", audio="AAC")
    assert (
        tracker._dvd_adjusted_name(
            regular,
            "Movie WEB",
            "1080p",
            "H.264",
            "WEBDL",
            "WEB",
        )
        == "Movie WEB"
    )

    remux = Meta(source="DVD", audio="AAC")
    assert tracker._dvd_remux_name(
        remux,
        "Movie DVD AAC",
        "480p",
        "MPEG-2",
    ) == "Movie 480p DVD MPEG-2 AAC"
    assert "480p DVD" in tracker._dvd_adjusted_name(
        remux,
        "Movie DVD AAC",
        "480p",
        "MPEG-2",
        "REMUX",
        "DVD",
    )

    trump = Meta(trump_reason="exact_match", aka="", no_aka=True)
    assert (
        tracker._final_name(trump, "Movie 2025", "2025")
        == "Movie 2025 - TRUMP"
    )
    aka = Meta(trump_reason="", aka="AKA Title", no_aka=False)
    assert tracker._final_name(
        aka,
        "Movie 2025 AKA Title",
        "2025",
    ) == "Movie AKA Title 2025"


def test_aither_get_name_end_to_end(
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
        name="Movie 2025 1080p",
        year=2025,
        manual_year=0,
        no_year=False,
        language_checked=True,
        audio_languages=["English"],
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        video_codec="H.264",
        aka="",
        no_aka=True,
    )

    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie 2025 1080p"
    }


def test_aither_additional_check_failure_and_disc_edges() -> None:
    tracker = _tracker()
    language_check = AsyncMock(return_value=False)
    tracker.common.check_language_requirements = language_check  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="", valid_mi=True))
    )

    language_check.reset_mock()
    assert asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="BDMV", valid_mi=True))
    )
    language_check.assert_not_awaited()

    tracker.common.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert not asyncio.run(
        tracker.get_additional_checks(Meta(is_disc="", valid_mi=False))
    )


def test_aither_hdr_and_year_fallback_edges() -> None:
    assert Aither._hdr_flags("HDR") == {"hdr": 1}
    assert Aither._hdr_flags("HLG") == {"hdr": 1}
    assert Aither._hdr_flags("") == {}
    assert Aither._base_year(Meta(category="MOVIE", year=2025)) == "2025"
    assert Aither._manual_year(Meta(manual_year=0)) == ""
    assert (
        Aither._resolved_year(
            Meta(category="MOVIE", year=2025, manual_year=0)
        )
        == "2025"
    )


def test_aither_non_english_language_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker()
    monkeypatch.setattr(
        aither_module.languages_manager,
        "has_english_language",
        AsyncMock(return_value=False),
    )
    meta = Meta(language_checked=True, audio_languages=["French"])
    assert asyncio.run(tracker._foreign_language(meta)) == "FRENCH"


def test_aither_foreign_language_injection_edges() -> None:
    web = Meta(is_disc="", resolution="1080p")
    assert (
        Aither._apply_foreign_language(
            web,
            "Movie 1080p",
            "2025",
            "WEBDL",
            "WEB",
            "",
        )
        == "Movie 1080p"
    )
    assert (
        Aither._apply_foreign_language(
            web,
            "Movie 1080p",
            "2025",
            "WEBDL",
            "WEB",
            "FRENCH",
        )
        == "Movie FRENCH 1080p"
    )

    dvd = Meta(is_disc="", resolution="480p")
    assert (
        Aither._apply_foreign_language(
            dvd,
            "Movie 2025 DVD REMUX",
            "2025",
            "REMUX",
            "DVD",
            "FRENCH",
        )
        == "Movie 2025 FRENCH DVD REMUX"
    )


def test_aither_dvd_remux_and_alt_title_edges() -> None:
    meta = Meta(source="PAL DVD", audio="AAC")
    assert (
        Aither._dvd_adjusted_name(
            meta,
            "Movie PAL DVD AAC",
            "480p",
            "H.264",
            "REMUX",
            "PAL DVD",
        )
        == "Movie 480p PAL DVD H.264 AAC"
    )

    aka = Meta(aka="AKA Title", no_aka=False)
    assert (
        Aither._final_name(aka, "Movie 2025 AKA Title", "2025")
        == "Movie AKA Title 2025"
    )


def test_aither_get_name_orchestration(
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
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        video_codec="H.264",
        language_checked=True,
        audio_languages=["English"],
        aka="",
        no_aka=True,
    )
    assert asyncio.run(tracker.get_name(meta)) == {
        "name": "Movie 2025 1080p WEB-DL"
    }
