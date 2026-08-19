from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.domain_models.release import Meta
from src.services import comparison_service
from src.services.comparison_service import ComparisonManager


class _Uploader:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, list[str]]] = []

    async def upload_screens(
        self,
        _meta: Meta,
        screens: int,
        host_number: int,
        _index: int,
        _total: int,
        custom_images: list[str],
        _return_dict: dict[str, object],
    ) -> tuple[list[dict[str, str]], int]:
        self.calls.append((screens, host_number, list(custom_images)))
        return (
            [
                {
                    "img_url": f"https://img.test/{Path(path).name}",
                    "raw_url": f"https://raw.test/{Path(path).name}",
                    "web_url": f"https://web.test/{Path(path).name}",
                    "ignored": "value",
                }
                for path in custom_images
            ],
            len(custom_images),
        )


def _meta(tmp_path: Path, comparison: Path, *, index: str | int | None = None, debug: bool = False) -> Meta:
    state = tmp_path / "tmp" / "comparison"
    state.mkdir(parents=True, exist_ok=True)
    return Meta(
        base_dir=str(tmp_path),
        uuid="comparison",
        comparison=str(comparison),
        comparison_index=index,
        debug=debug,
        image_list=[],
    )


def _manager(meta: Meta, config: dict[str, Any] | None = None) -> tuple[ComparisonManager, _Uploader]:
    manager = ComparisonManager(meta, config or {"DEFAULT": {"img_host_1": "imgbb"}})
    uploader = _Uploader()
    manager.uploadscreens_manager = uploader  # type: ignore[assignment]
    return manager, uploader


def _saved_path(tmp_path: Path) -> Path:
    path = tmp_path / "tmp" / "comparison" / "comparison_data.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_constructor_and_missing_comparison_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DEFAULT"):
        ComparisonManager(Meta(), {"DEFAULT": "invalid"})  # type: ignore[dict-item]

    manager, _ = _manager(Meta(comparison=None))
    assert asyncio.run(manager.add_comparison()) == []
    manager, _ = _manager(Meta(comparison=str(tmp_path / "missing")))
    assert asyncio.run(manager.add_comparison()) == []


def test_saved_dict_loads_selected_group_and_deduplicates(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    selected = {"img_url": "https://img/one", "raw_url": "https://raw/one", "web_url": "https://web/one"}
    data = {"0": {"files": ["1-0-Source.png"], "urls": [selected], "name": "Source"}}
    _saved_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")
    meta = _meta(tmp_path, comparison, index="0", debug=True)
    meta.image_list = [selected]
    manager, uploader = _manager(meta)

    assert asyncio.run(manager.add_comparison()) == data
    assert meta.image_list == [selected]
    assert uploader.calls == []

    meta = _meta(tmp_path, comparison, index="missing")
    manager, _ = _manager(meta)
    assert asyncio.run(manager.add_comparison()) == data
    assert meta.image_list == []


def test_saved_list_handles_valid_invalid_and_out_of_range_indices(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    selected = {"img_url": "https://img/one", "raw_url": "https://raw/one", "web_url": "https://web/one"}
    data = [{"files": ["1-0-Source.png"], "urls": [selected], "name": "Source"}]
    _saved_path(tmp_path).write_text(json.dumps(data), encoding="utf-8")

    for index, expected_images in ((0, [selected]), (5, []), ("not-an-int", [])):
        meta = _meta(tmp_path, comparison, index=index, debug=True)
        manager, _ = _manager(meta)
        assert asyncio.run(manager.add_comparison()) == data
        assert meta.image_list == expected_images


def test_invalid_saved_formats_fall_back_and_no_hosts_is_semantic_error(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    path = _saved_path(tmp_path)
    for payload in ({"0": "invalid"}, ["invalid"], 1):
        path.write_text(json.dumps(payload), encoding="utf-8")
        manager, _ = _manager(_meta(tmp_path, comparison), {"DEFAULT": {}})
        with pytest.raises(ValueError, match="No image hosts"):
            asyncio.run(manager.add_comparison())


def test_generates_uploads_prompts_saves_and_selects_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    for name in ("2-0-Source.png", "1-0-Source.png", "1-1-Encode.png", "ignored.jpg", "bad.png"):
        (comparison / name).write_bytes(b"image")

    answers = iter(("invalid", "1"))
    monkeypatch.setattr(comparison_service.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers))
    meta = _meta(tmp_path, comparison, index=None, debug=True)
    manager, uploader = _manager(meta, {"DEFAULT": {"img_host_2": 123, "img_host_bad": "ignored"}})

    result = asyncio.run(manager.add_comparison())

    assert isinstance(result, dict)
    assert list(result) == ["0", "1"]
    assert result["0"]["files"] == ["1-0-Source.png", "2-0-Source.png"]
    assert result["0"]["name"] == "Source"
    assert result["0"]["img_host"] == "123"
    assert result["1"]["name"] == "Encode"
    assert len(uploader.calls) == 2
    assert len(meta.image_list) == 1
    assert meta.image_list[0]["img_url"].endswith("1-1-Encode.png")
    saved = json.loads(_saved_path(tmp_path).read_text(encoding="utf-8"))
    assert saved == result


def test_save_failure_is_non_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    meta = _meta(tmp_path, comparison, index="0")
    manager, _ = _manager(meta)
    real_to_thread = asyncio.to_thread

    async def fail_write(function, *args, **kwargs):
        if getattr(function, "__name__", "") == "write_text":
            raise OSError("read only")
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(comparison_service.asyncio, "to_thread", fail_write)
    assert asyncio.run(manager.add_comparison()) == {}
