# ruff: noqa: S101

from pathlib import Path
from typing import Any

import pytest

from src.meta import Meta
from src.trackers.UNIT3D.peergarden import PeerGarden
from src.trackers.UNIT3D.samaritano import Samaritano
from src.trackers.UNIT3D.znth import Zenith


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tracker_class", "tracker_name"),
    [
        (Zenith, "ZENITH"),
        (PeerGarden, "PEERGARDEN"),
        (Samaritano, "SAMARITANO"),
    ],
)
async def test_unit3d_game_trackers_attach_scene_nfo(tmp_path: Path, tracker_class: type[Any], tracker_name: str) -> None:
    nfo_path = tmp_path / "tenoke-scriptorium.master.of.manuscripts.update.v1.2.0.nfo"
    nfo_content = b"TENOKE release information\nPatchNotes: v1.2.0\n"
    nfo_path.write_bytes(nfo_content)
    meta = Meta(
        category="GAME",
        base_dir=str(tmp_path),
        uuid="scriptorium",
        path=str(tmp_path),
        scene_nfo_file=str(nfo_path),
        filelist=[str(nfo_path)],
    )
    tracker = tracker_class({"DEFAULT": {}, "TRACKERS": {tracker_name: {}}})

    files = await tracker.get_additional_files(meta)

    assert files["nfo"] == (nfo_path.name, nfo_content, "text/plain")


@pytest.mark.asyncio
async def test_unit3d_game_uses_nfo_from_filelist_when_scene_path_is_missing(tmp_path: Path) -> None:
    nfo_path = tmp_path / "release.nfo"
    nfo_path.write_bytes(b"release nfo")
    meta = Meta(category="GAME", base_dir=str(tmp_path), uuid="game", path=str(tmp_path), filelist=[str(nfo_path)])
    tracker = Zenith({"DEFAULT": {}, "TRACKERS": {"ZENITH": {}}})

    files = await tracker.get_additional_files(meta)

    assert files["nfo"][1] == b"release nfo"
