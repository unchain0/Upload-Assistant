# ruff: noqa: S101

import asyncio

from src.prep_game import clean_game_title, detect_platform_from_files


def test_game_title_fallback_removes_locale_build_date_and_extension() -> None:
    assert clean_game_title("dungeon_antiqua_2_enUS_20260717_.pkg") == "Dungeon Antiqua 2"


def test_game_title_fallback_removes_version_and_scene_group() -> None:
    assert clean_game_title("Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg") == "Native Instruments SuperStarSaw"


def test_dmg_platform_is_detected_as_mac() -> None:
    assert asyncio.run(detect_platform_from_files(["Native_Instruments_SuperStarSaw.dmg"])) == "MAC"
