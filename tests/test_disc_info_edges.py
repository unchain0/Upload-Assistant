from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.media import disc_info
from src.integrations.media.disc_info import DiscInfoManager


def _meta(tmp_path: Path, path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(path),
        "uuid": "disc",
        "site_check": False,
        "edit": False,
        "discs": [],
    }
    state.update(values)
    return Meta(state)


def test_no_disc_and_dvd_size_variants(tmp_path: Path) -> None:
    source = tmp_path / "plain"
    source.mkdir()
    manager = DiscInfoManager({"DEFAULT": {}})
    assert asyncio.run(manager.get_disc(_meta(tmp_path, source))) == ("", str(source), None, [])

    discs = [{"size": "DVD9"}, {"size": "DVD5"}, {"size": "DVD9"}]
    assert asyncio.run(manager.get_dvd_size(discs, None)) == "2xDVD9 DVD5"
    assert asyncio.run(manager.get_dvd_size(discs, "3xDVD9")) == "3xDVD9"


def test_bdmv_site_check_ensure_error_edit_and_normal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "movies"
    (root / "Zulu" / "BDMV").mkdir(parents=True)
    (root / "Alpha" / "BDMV").mkdir(parents=True)
    manager = DiscInfoManager({"DEFAULT": {}})
    parser = SimpleNamespace(get_bdinfo=AsyncMock(return_value=([{"name": "Zulu"}, {"name": "Alpha"}], {"title": "Disc"})))
    manager._parser = parser  # type: ignore[assignment]
    ensure = AsyncMock()
    monkeypatch.setattr(disc_info.BDInfoBinaryManager, "ensure_bdinfo_binary", ensure)

    with pytest.raises(RuntimeError, match="site_check"):
        asyncio.run(manager.get_disc(_meta(tmp_path, root, site_check=True)))

    ensure.side_effect = RuntimeError("download failed")
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(manager.get_disc(_meta(tmp_path, root)))

    ensure.side_effect = None
    result = asyncio.run(manager.get_disc(_meta(tmp_path, root, edit=False)))
    assert result[0] == "BDMV" and result[2] == {"title": "Disc"}
    assert [item["name"] for item in result[3]] == ["Alpha", "Zulu"]
    call = parser.get_bdinfo.await_args
    assert call.args[1][0]["type"] == "BDMV"

    existing = [{"name": "Existing", "path": "existing", "type": "BDMV"}]
    asyncio.run(manager.get_disc(_meta(tmp_path, root, edit=True, discs=existing)))
    assert parser.get_bdinfo.await_args.args[1] == existing


def test_dvd_and_hddvd_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dvd_root = tmp_path / "dvd"
    (dvd_root / "Movie" / "VIDEO_TS").mkdir(parents=True)
    manager = DiscInfoManager({"DEFAULT": {}})
    parser = SimpleNamespace(
        get_dvdinfo=AsyncMock(return_value=[{"name": "Movie", "size": "DVD9"}]),
        get_hddvd_info=AsyncMock(),
    )
    manager._parser = parser  # type: ignore[assignment]
    downloads: list[str] = []
    monkeypatch.setattr(disc_info, "download_dvd_mediainfo", lambda base: downloads.append(base))
    result = asyncio.run(manager.get_disc(_meta(tmp_path, dvd_root)))
    assert result[0] == "DVD" and result[3] == [{"name": "Movie", "size": "DVD9"}]
    assert downloads == [str(tmp_path)]
    parser.get_dvdinfo.assert_awaited_once()

    hd_root = tmp_path / "hddvd"
    (hd_root / "Movie" / "HVDVD_TS").mkdir(parents=True)
    output = tmp_path / "tmp" / "disc"
    output.mkdir(parents=True)
    parser.get_hddvd_info.return_value = [{"name": "Movie", "evo_mi": "MEDIA INFO"}]
    result = asyncio.run(manager.get_disc(_meta(tmp_path, hd_root)))
    assert result[0] == "HDDVD" and result[3][0]["evo_mi"] == "MEDIA INFO"
    assert (output / "MEDIAINFO.txt").read_text(encoding="utf-8") == "MEDIA INFO"
