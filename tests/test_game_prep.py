# ruff: noqa: S101

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.meta import Meta
from src.prep_game import clean_game_title, detect_platform_from_files, extract_release_group, gather_game_prep, missing_game_fields, required_game_fields


def test_game_title_fallback_removes_locale_build_date_and_extension() -> None:
    assert clean_game_title("dungeon_antiqua_2_enUS_20260717_.pkg") == "Dungeon Antiqua 2"


def test_game_title_fallback_removes_version_and_scene_group() -> None:
    assert clean_game_title("Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg") == "Native Instruments SuperStarSaw"
    assert extract_release_group("Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg") == "HCiSO"


def test_dmg_platform_is_detected_as_mac() -> None:
    assert asyncio.run(detect_platform_from_files(["Native_Instruments_SuperStarSaw.dmg"])) == "MAC"


def test_generic_pkg_platform_is_detected_as_mac() -> None:
    assert asyncio.run(detect_platform_from_files(["dungeon_antiqua_2_enUS_20260717_.pkg"])) == "MAC"


def test_pkg_platform_preserves_explicit_playstation_evidence() -> None:
    assert asyncio.run(detect_platform_from_files(["UP0001-NPUB12345_00-GAME.pkg"])) == "PS3"
    assert asyncio.run(detect_platform_from_files(["Game-CUSA12345.pkg"])) == "PS4"
    assert asyncio.run(detect_platform_from_files(["Game-PPSA12345.pkg"])) == "PS5"


@pytest.mark.asyncio
async def test_software_game_prep_uses_raw_filename_metadata(tmp_path) -> None:
    release_path = tmp_path / "Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg"
    meta = Meta(
        path=str(release_path),
        filename="Native Instruments SuperStarSaw 1 0 0 [HCiSO] dmg",
        title="Native Instruments SuperStarSaw 1 0 0 [HCiSO] dmg",
        filelist=[str(release_path)],
        unattended=True,
    )

    with patch("src.prep_game.IGDBAPI.search_game", new=AsyncMock(return_value=[])) as search:
        await gather_game_prep(
            meta,
            str(release_path),
            str(tmp_path),
            {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}},
        )

    search.assert_awaited_once_with("Native Instruments SuperStarSaw")
    assert meta.title == "Native Instruments SuperStarSaw"
    assert meta.game_version == "v1.0.0"
    assert meta.tag == "-HCiSO"
    assert meta.platform == "MAC"


@pytest.mark.asyncio
async def test_guitar_pro_pkg_is_prepared_as_mac_software(tmp_path) -> None:
    release = tmp_path / "Guitar_Pro_8.1.5-31_[atb]"
    release.mkdir()
    package = release / "Guitar Pro 8.1.5-31 [atb].pkg"
    package.write_bytes(b"installer")
    notes = release / "Read.txt"
    notes.write_text("install PKG\nUse Serial", encoding="utf-8")
    meta = Meta(path=str(release), filelist=[str(package), str(notes)], unattended=True)

    with patch("src.prep_game.IGDBAPI.search_game", new=AsyncMock(return_value=[])):
        await gather_game_prep(
            meta,
            str(package),
            str(tmp_path),
            {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}},
        )

    assert meta.category == "GAME"
    assert meta.software is True
    assert meta.title == "Guitar Pro"
    assert meta.game_version == "v8.1.5-31"
    assert meta.tag == "-atb"
    assert meta.platform == "MAC"
    assert meta.software_notes == "install PKG\nUse Serial"
    assert required_game_fields(meta) == ["title", "platform"]
    assert missing_game_fields(meta) == ["developer", "publisher", "cover", "languages", "overview"]
