# ruff: noqa: S101

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.meta import Meta
from src.prep_game import (
    clean_game_title,
    detect_platform_from_files,
    extract_release_group,
    extract_version_from_text,
    gather_game_prep,
    missing_game_fields,
    required_game_fields,
    resolve_game_filelist,
)


def test_game_title_fallback_removes_locale_build_date_and_extension() -> None:
    assert clean_game_title("dungeon_antiqua_2_enUS_20260717_.pkg") == "Dungeon Antiqua 2"


def test_game_title_fallback_removes_version_and_scene_group() -> None:
    assert clean_game_title("Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg") == "Native Instruments SuperStarSaw"
    assert extract_release_group("Native_Instruments_SuperStarSaw_1.0.0_[HCiSO].dmg") == "HCiSO"


def test_game_title_fallback_preserves_hyphenated_title() -> None:
    assert clean_game_title("Half-Life") == "Half Life"


def test_game_title_and_version_support_compact_letter_suffix() -> None:
    release = "Factory.Town.2.Paradise.v133f.MacOS.dmg"

    assert extract_version_from_text(release) == "v133f"
    assert clean_game_title(release) == "Factory Town 2 Paradise"


def test_dmg_platform_is_detected_as_mac() -> None:
    assert asyncio.run(detect_platform_from_files(["Native_Instruments_SuperStarSaw.dmg"])) == "MAC"


def test_generic_pkg_platform_is_detected_as_mac() -> None:
    assert asyncio.run(detect_platform_from_files(["dungeon_antiqua_2_enUS_20260717_.pkg"])) == "MAC"


def test_windows_installer_platform_is_detected_as_pc() -> None:
    assert asyncio.run(detect_platform_from_files(["RAM Saver Professional 26.7.1 Incl Keygen.exe"])) == "PC"
    assert asyncio.run(detect_platform_from_files(["RAM Saver Professional 26.7.1.msi"])) == "PC"


def test_game_filelist_places_selected_installer_first(tmp_path) -> None:
    release = tmp_path / "RAM Saver Professional 26.7.1 Incl Keygen - KhanPC"
    release.mkdir()
    notes = release / "How to Install.txt"
    notes.write_text("Install the application.\n", encoding="utf-8")
    installer = release / "ramsaverpro.exe"
    installer.write_bytes(b"installer")

    videopath, filelist, _, _ = resolve_game_filelist(Meta(), str(release))

    assert videopath == str(installer.resolve())
    assert filelist[0] == videopath


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
async def test_game_prep_extracts_compact_letter_version_from_dmg(tmp_path) -> None:
    release_path = tmp_path / "Factory.Town.2.Paradise.v133f.MacOS.dmg"
    meta = Meta(path=str(release_path), filelist=[str(release_path)], unattended=True)

    with patch("src.prep_game.IGDBAPI.search_game", new=AsyncMock(return_value=[])) as search:
        await gather_game_prep(
            meta,
            str(release_path),
            str(tmp_path),
            {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}},
        )

    search.assert_awaited_once_with("Factory Town 2 Paradise")
    assert meta.title == "Factory Town 2 Paradise"
    assert meta.game_version == "v133f"
    assert meta.platform == "MAC"


@pytest.mark.asyncio
async def test_game_prep_replaces_prepopulated_inferred_title_with_igdb_name(tmp_path) -> None:
    release_path = tmp_path / "Inferred.Release.Name-TENOKE"
    meta = Meta(path=str(release_path), title="Inferred Release Name", filelist=[str(release_path)], unattended=True)
    result = {"id": 1, "name": "Canonical IGDB Title"}

    with (
        patch("src.prep_game.IGDBAPI.search_game", new=AsyncMock(return_value=[result])),
        patch("src.prep_game.IGDBAPI.cache_game_details", new=AsyncMock()),
    ):
        await gather_game_prep(
            meta,
            str(release_path),
            str(tmp_path),
            {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}},
        )

    assert meta.title == "Canonical IGDB Title"


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


@pytest.mark.asyncio
async def test_windows_installer_is_prepared_as_pc_software(tmp_path) -> None:
    release = tmp_path / "RAM Saver Professional 26.7.1 Incl Keygen - KhanPC"
    release.mkdir()
    installer = release / "ramsaverpro.exe"
    installer.write_bytes(b"installer")
    notes = release / "How to Install.txt"
    notes.write_text("Install the application.\n", encoding="utf-8")
    meta = Meta(path=str(release), filelist=[str(installer), str(notes)], unattended=True)

    with patch("src.prep_game.IGDBAPI.search_game", new=AsyncMock(return_value=[])) as search:
        await gather_game_prep(
            meta,
            str(installer),
            str(tmp_path),
            {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}},
        )

    search.assert_awaited_once_with("RAM Saver Professional")
    assert meta.software is True
    assert meta.title == "RAM Saver Professional"
    assert meta.game_version == "v26.7.1"
    assert meta.tag == "-KhanPC"
    assert meta.platform == "PC"
    assert meta.software_notes == "Install the application."


@pytest.mark.asyncio
async def test_scene_game_uses_local_nfo_and_extracts_installation_steps(tmp_path) -> None:
    release = tmp_path / "Cellar.Keeper-TENOKE"
    release.mkdir()
    iso = release / "tenoke-cellar.keeper.iso"
    iso.touch()
    nfo = release / "tenoke-cellar.keeper.nfo"
    nfo.write_text(
        "TENOKE\n│ 1. Extract and burn or mount the .iso │\n"
        "│ 2. Run SETUP.exe and install the game │\n"
        "│ 3. Copy crack to install dir │\n"
        "│ 4. Play │\n",
        encoding="utf-8",
    )
    meta = Meta(path=str(release), filelist=[str(iso), str(nfo)], platform="PC", manual_platform="PC", unattended=True)

    await gather_game_prep(meta, str(iso), str(tmp_path), {"DEFAULT": {}})

    assert meta.scene_nfo_file == str(nfo)
    assert meta.software_notes == (
        "1. Extract and burn or mount the .iso\n"
        "2. Run SETUP.exe and install the game\n"
        "3. Copy crack to install dir\n"
        "4. Play"
    )
