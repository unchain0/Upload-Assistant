from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts import rehosting
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)


class _Uploader:
    results: ClassVar[list[list[dict[str, str]]]] = []
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, _config: dict[str, Any] | None = None) -> None:
        pass

    async def upload_screens(
        self,
        meta: Meta,
        screens: int,
        img_host_index: int,
        index: int,
        total: int,
        files: list[str],
        return_dict: dict[str, Any],
        retry_mode: bool = False,
        **kwargs: object,
    ) -> tuple[list[dict[str, str]], int]:
        del return_dict
        type(self).calls.append(
            {
                "meta": meta,
                "screens": screens,
                "img_host_index": img_host_index,
                "index": index,
                "total": total,
                "files": list(files),
                "retry_mode": retry_mode,
                **kwargs,
            }
        )
        value = type(self).results.pop(0) if type(self).results else []
        return value, len(value)

    @classmethod
    def reset(cls, *results: list[dict[str, str]]) -> None:
        cls.results = [list(result) for result in results]
        cls.calls = []


class _Screens:
    def __init__(self, _config: dict[str, Any] | None = None) -> None:
        self.disc_screenshots = AsyncMock()
        self.dvd_screenshots = AsyncMock()
        self.xxx_contact_sheets = AsyncMock()
        self.screenshots = AsyncMock()


def _meta(tmp_path: Path, **values: object) -> Meta:
    source = tmp_path / "source.mkv"
    source.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(source),
        "video": str(source),
        "filelist": [str(source)],
        "title": "Release",
        "category": "MOVIE",
        "screens": 2,
        "image_list": [],
        "menu_images": [],
        "spectrograms_images": [],
        "dynamic_hdr_plot_images": [],
        "tracker_image_collections": {},
        "imghost": "imgbb",
        "skip_imghost_upload": False,
        "debug": True,
        "is_disc": "",
        "discs": [],
        "bdinfo": {},
        "vapoursynth": False,
        "ffdebug": False,
    }
    state.update(values)
    return Meta(state)


def _dirs(meta: Meta) -> dict[str, Path]:
    from src.integrations.filesystem.temp_paths import (
        artwork_dir,
        dynamic_hdr_plots_dir,
        menu_screenshots_dir,
        screenshots_dir,
        spectrograms_dir,
    )

    result = {
        "screens": screenshots_dir(meta.base_dir, meta.uuid),
        "artwork": artwork_dir(meta.base_dir, meta.uuid),
        "menu": menu_screenshots_dir(meta.base_dir, meta.uuid),
        "spectrograms": spectrograms_dir(meta.base_dir, meta.uuid),
        "dynamic": dynamic_hdr_plots_dir(meta.base_dir, meta.uuid),
    }
    for path in result.values():
        path.mkdir(parents=True, exist_ok=True)
    return result


def _image(path: Path, name: str = "Release-0.png") -> Path:
    target = path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"image")
    return target


def _default(**values: object) -> dict[str, Any]:
    return {
        "img_host_1": "imgbb",
        "img_host_2": "imgbox",
        "screens": 2,
        **values,
    }


def test_policy_selection_and_simple_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class PolicyTracker:
        image_host_policy = ImageHostPolicy({"imgbb.com": "imgbb"}, ("imgbb",))

    class Legacy:
        approved_image_hosts: ClassVar[list[object]] = ["imgbox", 1]

        @staticmethod
        def check_image_hosts(_meta: Meta) -> None:
            return None

    class Empty:
        image_host_policy = ImageHostPolicy({}, ())

    mapping = {"POLICY": PolicyTracker, "LEGACY": Legacy, "EMPTY": Empty}
    assert rehosting.has_restricted_image_hosts([" policy "], mapping)
    assert rehosting.has_restricted_image_hosts(["legacy"], mapping)
    assert not rehosting.has_restricted_image_hosts(
        ["empty", "missing"], mapping
    )
    assert (
        rehosting.select_common_image_host(
            {"img_host_2": "imgbox", "img_host_1": " IMGBB "},
            ["POLICY"],
            mapping,
        )
        == "imgbb"
    )
    assert (
        rehosting.select_common_image_host(
            {"img_host_1": 1}, ["POLICY"], mapping
        )
        is None
    )
    assert (
        rehosting.select_common_image_host(
            {"img_host_1": "imgbb"}, ["POLICY", "LEGACY"], mapping
        )
        is None
    )
    assert rehosting.select_common_image_host({}, ["MISSING"], mapping) is None

    path = tmp_path / "delete.txt"
    path.write_text("x", encoding="utf-8")
    assert rehosting._safe_remove(str(path)) and not path.exists()
    assert not rehosting._safe_remove(str(path))
    monkeypatch.setattr(
        Path,
        "exists",
        lambda _path: (_ for _ in ()).throw(OSError("stat failed")),
    )
    assert not rehosting._safe_remove(str(path))

    assert (
        asyncio.run(rehosting.match_host("sub.imgbb.com", ["imgbb.com"]))
        == "imgbb.com"
    )
    assert (
        asyncio.run(rehosting.match_host("other.invalid", ["imgbb.com"]))
        == "other.invalid"
    )
    assert asyncio.run(rehosting.sanitize_filename('a:b/c*?"d')) == "a_b_c___d"
    assert (
        rehosting._image_host(
            "https://sub.imgbb.com/a.png", {"imgbb.com": "imgbb"}
        )
        == "imgbb"
    )
    assert rehosting._image_host("not a url", {}) == ""


def test_manager_wrappers_and_tracker_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rehosting, "TakeScreensManager", _Screens)
    monkeypatch.setattr(rehosting, "UploadScreensManager", _Uploader)
    check = AsyncMock(return_value=([{"raw_url": "raw"}], False, True))
    additional = AsyncMock()
    handle = AsyncMock(return_value=([], True, False))
    monkeypatch.setattr(rehosting, "_check_hosts", check)
    monkeypatch.setattr(
        rehosting, "_check_additional_image_collections", additional
    )
    monkeypatch.setattr(rehosting, "_handle_image_upload", handle)
    manager = RehostImagesManager({"DEFAULT": _default()})
    meta = _meta(tmp_path)
    assert asyncio.run(
        manager.check_hosts(meta, "TEST", {"imgbb.com": "imgbb"})
    ) == ([{"raw_url": "raw"}], False, True)
    additional.assert_awaited_once()
    additional.reset_mock()
    asyncio.run(manager.check_hosts(meta, "covers", {}))
    additional.assert_not_awaited()
    policy = ImageHostPolicy({"imgbb.com": "imgbb"}, ("imgbb",), 2)
    asyncio.run(manager.check_policy(meta, "TEST", policy))
    assert check.await_args.kwargs["img_host_index"] == 2
    assert asyncio.run(manager.handle_image_upload(meta, "TEST", {})) == (
        [],
        True,
        False,
    )

    assert (
        asyncio.run(
            rehosting.check_tracker_image_hosts(
                _meta(tmp_path, category="MUSIC"), object()
            )
        )
        is None
    )

    class PolicyClass:
        tracker = "POLICY"
        image_host_policy = policy
        rehost_images_manager = SimpleNamespace(check_policy=AsyncMock())

    asyncio.run(rehosting.check_tracker_image_hosts(meta, PolicyClass()))
    PolicyClass.rehost_images_manager.check_policy.assert_awaited_once()

    sync_calls: list[Meta] = []

    class SyncClass:
        @staticmethod
        def check_image_hosts(value: Meta) -> None:
            sync_calls.append(value)

    asyncio.run(rehosting.check_tracker_image_hosts(meta, SyncClass()))
    assert sync_calls == [meta]

    class AsyncClass:
        check_image_hosts = AsyncMock()

    asyncio.run(rehosting.check_tracker_image_hosts(meta, AsyncClass()))
    AsyncClass.check_image_hosts.assert_awaited_once_with(meta)


def test_collection_local_path_and_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path)
    dirs = _dirs(meta)
    assert rehosting._collection_directory(meta, "menu_images") == dirs["menu"]
    assert (
        rehosting._collection_directory(meta, "spectrograms_images")
        == dirs["spectrograms"]
    )
    assert (
        rehosting._collection_directory(meta, "dynamic_hdr_plot_images")
        == dirs["dynamic"]
    )
    assert rehosting._collection_directory(meta, "screenshots") is None

    local = _image(dirs["menu"], "menu.png")
    assert (
        asyncio.run(
            rehosting._local_image_path(
                meta, "menu_images", {"local_file_path": str(local)}
            )
        )
        == local
    )
    assert (
        asyncio.run(
            rehosting._local_image_path(
                meta,
                "menu_images",
                {
                    "local_file_path": str(tmp_path / "missing"),
                    "raw_url": "https://x/menu.png",
                },
            )
        )
        == local
    )
    assert (
        asyncio.run(
            rehosting._local_image_path(
                meta, "menu_images", {"raw_url": "https://x/missing.png"}
            )
        )
        is None
    )

    class Response:
        content = b"downloaded"

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str) -> Response:
            return Response()

    monkeypatch.setattr(rehosting.httpx, "AsyncClient", Client)
    downloaded = asyncio.run(
        rehosting._download_image_for_rehost(
            meta, "menu_images", "https://host.invalid/a:b.exe"
        )
    )
    assert (
        downloaded is not None
        and downloaded.suffix == ".png"
        and downloaded.read_bytes() == b"downloaded"
    )

    class FailedClient(Client):
        async def get(self, _url: str) -> Response:
            raise httpx.RequestError(
                "offline", request=httpx.Request("GET", "https://host.invalid")
            )

    monkeypatch.setattr(rehosting.httpx, "AsyncClient", FailedClient)
    assert (
        asyncio.run(
            rehosting._download_image_for_rehost(
                meta, "menu_images", "https://host.invalid/a.png"
            )
        )
        is None
    )

    monkeypatch.setattr(rehosting.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("write failed")
        ),
    )
    assert (
        asyncio.run(
            rehosting._download_image_for_rehost(
                meta, "menu_images", "https://host.invalid/a.jpg"
            )
        )
        is None
    )


def test_additional_collections_skip_missing_hosts_and_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploader_manager = _Uploader()
    _Uploader.reset()
    meta = _meta(tmp_path, skip_imghost_upload=True)
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            uploadscreens_manager=uploader_manager,
        )
    )
    assert _Uploader.calls == []

    meta.skip_imghost_upload = False
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config=_default(),
            uploadscreens_manager=uploader_manager,
        )
    )
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {},
            approved_image_hosts=["pixhost"],
            default_config=_default(),
            uploadscreens_manager=uploader_manager,
        )
    )
    assert _Uploader.calls == []

    dirs = _dirs(meta)
    local_menu = _image(dirs["menu"], "menu.png")
    local_spec = _image(dirs["spectrograms"], "spec.png")
    meta.menu_images = [
        "invalid",
        {"raw_url": "https://approved.imgbb.com/already.png"},
        {
            "raw_url": "https://bad.invalid/menu.png",
            "local_file_path": str(local_menu),
        },
        {"raw_url": "https://bad.invalid/no-local.png"},
    ]
    meta.spectrograms_images = [
        {
            "raw_url": "https://bad.invalid/spec.png",
            "local_file_path": str(local_spec),
        }
    ]
    meta.dynamic_hdr_plot_images = [
        {"raw_url": "https://bad.invalid/unavailable.png"}
    ]
    original_host = meta.imghost
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://imgbb.com/rehosted.png",
                "web_url": "w",
            }
        ],
        [
            {
                "img_url": "i",
                "raw_url": "https://bad.invalid/rejected.png",
                "web_url": "w",
            }
        ],
    )
    monkeypatch.setattr(
        rehosting, "_download_image_for_rehost", AsyncMock(return_value=None)
    )
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            uploadscreens_manager=uploader_manager,
        )
    )
    collection = meta.tracker_image_collections["TEST"]["menu_images"]
    assert collection[1]["raw_url"] == "https://imgbb.com/rehosted.png"
    assert collection[1]["local_file_path"] == str(local_menu)
    assert (
        meta.tracker_image_collections["TEST"]["spectrograms_images"][0][
            "raw_url"
        ]
        == "https://bad.invalid/spec.png"
    )
    assert meta.imghost == original_host


def test_check_hosts_dependencies_skip_approved_and_override(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    with pytest.raises(ValueError, match="default_config"):
        asyncio.run(rehosting._check_hosts(meta, "TEST", {}))
    with pytest.raises(ValueError, match="takescreens_manager"):
        asyncio.run(
            rehosting._check_hosts(meta, "TEST", {}, default_config=_default())
        )
    with pytest.raises(ValueError, match="uploadscreens_manager"):
        asyncio.run(
            rehosting._check_hosts(
                meta,
                "TEST",
                {},
                default_config=_default(),
                takescreens_manager=_Screens(),
            )
        )

    meta.skip_imghost_upload = True
    assert asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    ) == ([], False, False)

    approved = {
        "img_url": "i",
        "raw_url": "https://sub.imgbb.com/a.png",
        "web_url": "w",
    }
    missing_raw = {"img_url": "i", "raw_url": "", "web_url": "w"}
    meta = _meta(tmp_path, image_list=[approved, missing_raw])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config={},
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[1] is True

    meta = _meta(tmp_path, image_list=[approved])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([approved], False, False)

    meta = _meta(
        tmp_path,
        tracker_image_collections={"TEST": {"screenshots": [approved]}},
    )
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([approved], False, False)


def test_check_hosts_cached_json_covers_and_retry_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, release_url="https://release.invalid/1")
    state = Path(meta.base_dir) / "tmp" / meta.uuid
    state.mkdir(parents=True, exist_ok=True)
    approved = {
        "img_url": "i",
        "raw_url": "https://imgbb.com/a.png",
        "web_url": "w",
    }
    invalid = {
        "img_url": "i",
        "raw_url": "https://bad.invalid/a.png",
        "web_url": "w",
    }
    (state / "reuploaded_images.json").write_text(
        json.dumps([invalid, approved]), encoding="utf-8"
    )
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([approved], False, False)

    (state / "covers.json").write_text(
        json.dumps(
            [
                approved,
                {
                    **approved,
                    "raw_url": "https://imgbb.com/b.png",
                    "release_url": "other",
                },
                {
                    **approved,
                    "raw_url": "https://imgbb.com/c.png",
                    "release_url": meta.release_url,
                },
            ]
        ),
        encoding="utf-8",
    )
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "covers",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert (
        len(result[0]) == 1 and result[0][0]["release_url"] == meta.release_url
    )

    (state / "reuploaded_images.json").write_text("not-json", encoding="utf-8")
    calls = 0

    async def handle(
        *_args: object, **_kwargs: object
    ) -> tuple[list[dict[str, str]], bool, bool]:
        nonlocal calls
        calls += 1
        return ([], True, True) if calls == 1 else ([approved], False, True)

    monkeypatch.setattr(rehosting, "_handle_image_upload", handle)
    result = asyncio.run(
        rehosting._check_hosts(
            _meta(tmp_path),
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([approved], False, True) and calls == 2


def test_check_hosts_no_config_and_all_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path)
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config={},
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([], True, False)

    monkeypatch.setattr(
        rehosting,
        "_handle_image_upload",
        AsyncMock(return_value=([], False, False)),
    )
    result = asyncio.run(
        rehosting._check_hosts(
            _meta(tmp_path),
            "TEST",
            {},
            approved_image_hosts=[],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([], False, False)


def test_handle_dependencies_no_sources_and_skip_upload(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    with pytest.raises(ValueError, match="default_config"):
        asyncio.run(rehosting._handle_image_upload(meta, "TEST", {}))
    with pytest.raises(ValueError, match="takescreens_manager"):
        asyncio.run(
            rehosting._handle_image_upload(
                meta, "TEST", {}, default_config=_default()
            )
        )
    with pytest.raises(ValueError, match="uploadscreens_manager"):
        asyncio.run(
            rehosting._handle_image_upload(
                meta,
                "TEST",
                {},
                default_config=_default(),
                takescreens_manager=_Screens(),
            )
        )

    _dirs(meta)
    no_config = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config={},
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert no_config == ([], True, False)

    meta = _meta(
        tmp_path,
        skip_imghost_upload=True,
        tracker_image_collections={
            "TEST": {"screenshots": [{"raw_url": "raw"}]}
        },
    )
    _image(_dirs(meta)["screens"])
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result == ([{"raw_url": "raw"}], False, False)


def test_handle_existing_screens_upload_write_restore_and_covers_cleanup(
    tmp_path: Path,
) -> None:
    meta = _meta(
        tmp_path, imghost="original", release_url="https://release.invalid/1"
    )
    dirs = _dirs(meta)
    one = _image(dirs["screens"], "Release-0.png")
    two = _image(dirs["screens"], "Release-1.png")
    _Uploader.reset(
        [
            {
                "img_url": "i1",
                "raw_url": "https://imgbb.com/1.png",
                "web_url": "w1",
            },
            {
                "img_url": "i2",
                "raw_url": "https://imgbb.com/2.png",
                "web_url": "w2",
            },
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert len(result[0]) == 2 and result[1] is False
    assert meta.imghost == "original"
    output = dirs["screens"] / "reuploaded_images.json"
    assert len(json.loads(output.read_text())) == 2
    assert one.exists() and two.exists()

    cover = _image(dirs["artwork"], "cover_front.jpg")
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://imgbb.com/cover.jpg",
                "web_url": "w",
            }
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "covers",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[0][0]["raw_url"].endswith("cover.jpg") and not cover.exists()
    saved = json.loads(
        (Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json").read_text()
    )
    assert saved[0]["release_url"] == meta.release_url


def test_handle_existing_url_filename_title_fallback_and_indexed_files(
    tmp_path: Path,
) -> None:
    meta = _meta(
        tmp_path,
        image_list=[
            {
                "raw_url": "https://host.invalid/remote.png",
                "img_url": "i",
                "web_url": "w",
            }
        ],
        title="Release",
        screens=3,
    )
    dirs = _dirs(meta)
    _image(dirs["screens"], "remote.png")
    _image(dirs["screens"], "Release-extra.png")
    _image(dirs["screens"], "Other-2.png")
    _image(dirs["screens"], "FILE-9.png")
    _image(dirs["screens"], ".PLAYLIST-8.png")
    _Uploader.reset(
        [
            {
                "img_url": "i1",
                "raw_url": "https://imgbb.com/1.png",
                "web_url": "w1",
            },
            {
                "img_url": "i2",
                "raw_url": "https://imgbb.com/2.png",
                "web_url": "w2",
            },
            {
                "img_url": "i3",
                "raw_url": "https://imgbb.com/3.png",
                "web_url": "w3",
            },
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(screens=3),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert len(result[0]) == 3
    uploaded_files = _Uploader.calls[-1]["files"]
    assert str(dirs["screens"] / "remote.png") in uploaded_files
    assert all(
        "FILE-9" not in path and "PLAYLIST-8" not in path
        for path in uploaded_files
    )


def test_handle_generation_modes_failure_invalid_host_and_no_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        rehosting,
        "manifest_files",
        lambda base_dir, folder_id, _group: sorted(
            (Path(base_dir) / "tmp" / folder_id / "screenshots").glob("*.png")
        ),
    )
    for mode in ("BDMV", "DVD", "XXX", "VIDEO", "NOPATH"):
        meta = _meta(
            tmp_path, image_list=[], screens=1, uuid=f"release-{mode.lower()}"
        )
        dirs = _dirs(meta)
        screens = _Screens()
        generated = dirs["screens"] / "Release-0.png"

        async def make_file(
            *_args: object, _generated: Path = generated, **_kwargs: object
        ) -> None:
            _generated.write_bytes(b"image")

        if mode == "BDMV":
            meta.is_disc = "BDMV"
            screens.disc_screenshots.side_effect = make_file
        elif mode == "DVD":
            meta.is_disc = "DVD"
            meta.discs = [{"name": "DISC", "path": str(tmp_path)}]

            async def dvd(
                *_args: object,
                _directory: Path = dirs["screens"],
                **_kwargs: object,
            ) -> None:
                _image(_directory, "DISC-0.png")

            screens.dvd_screenshots.side_effect = dvd
        elif mode == "XXX":
            meta.category = "XXX"
            screens.xxx_contact_sheets.side_effect = make_file
        elif mode == "VIDEO":
            screens.screenshots.side_effect = make_file
        else:
            meta.video = ""
            meta.filelist = []
            meta.path = ""
        _Uploader.reset(
            [
                {
                    "img_url": "i",
                    "raw_url": "https://imgbb.com/a.png",
                    "web_url": "w",
                }
            ]
        )
        result = asyncio.run(
            rehosting._handle_image_upload(
                meta,
                "TEST",
                {"imgbb.com": "imgbb"},
                approved_image_hosts=["imgbb"],
                default_config=_default(screens=1),
                takescreens_manager=screens,
                uploadscreens_manager=_Uploader(),
            )
        )
        if mode == "NOPATH":
            assert result[1] is True
        else:
            assert result[0]

    meta = _meta(tmp_path, screens=1)
    _dirs(meta)
    screens = _Screens()
    screens.screenshots.side_effect = RuntimeError("capture failed")
    assert (
        asyncio.run(
            rehosting._handle_image_upload(
                meta,
                "TEST",
                {"imgbb.com": "imgbb"},
                approved_image_hosts=["imgbb"],
                default_config=_default(screens=1),
                takescreens_manager=screens,
                uploadscreens_manager=_Uploader(),
            )
        )[1]
        is True
    )

    meta = _meta(tmp_path, screens=1)
    dirs = _dirs(meta)
    _image(dirs["screens"])
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://bad.invalid/a.png",
                "web_url": "w",
            }
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(
                img_host_1="pixhost", img_host_2="imgbb", screens=1
            ),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[1] is True and result[2] is True

    meta = _meta(tmp_path, screens=1)
    dirs = _dirs(meta)
    _image(dirs["screens"])
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {},
            approved_image_hosts=["unconfigured"],
            default_config=_default(screens=1),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[1] is True


def test_handle_output_existing_invalid_json_write_failure_and_invalid_tracker_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, screens=1)
    dirs = _dirs(meta)
    _image(dirs["screens"])
    output = dirs["screens"] / "reuploaded_images.json"
    output.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://imgbb.com/a.png",
                "web_url": "w",
            }
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(screens=1),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[0]

    original_open = rehosting.aiofiles.open

    def fail_write(
        path: str | Path, mode: str = "r", *args: object, **kwargs: object
    ):
        if "w" in mode:
            raise OSError("read only")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(rehosting.aiofiles, "open", fail_write)
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://imgbb.com/b.png",
                "web_url": "w",
            }
        ]
    )
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST2",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(screens=1),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[0]

    monkeypatch.setattr(rehosting.aiofiles, "open", original_open)
    _Uploader.reset([])
    result = asyncio.run(
        rehosting._handle_image_upload(
            _meta(tmp_path, screens=1),
            "EMPTY",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(screens=1),
            takescreens_manager=_Screens(),
            uploadscreens_manager=_Uploader(),
        )
    )
    assert result[0] == []


def test_remaining_rehosting_branches(tmp_path: Path) -> None:
    approved = {
        "img_url": "i",
        "raw_url": "https://imgbb.com/a.png",
        "web_url": "w",
    }
    managers = {
        "takescreens_manager": _Screens(),
        "uploadscreens_manager": _Uploader(),
    }

    # None host policy is normalized, and mapped/unmapped URLs require rehosting.
    meta = _meta(
        tmp_path,
        image_list=[
            {
                "img_url": "i",
                "raw_url": "https://bad.com/a.png",
                "web_url": "w",
            }
        ],
    )
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"bad.com": "bad"},
            approved_image_hosts=None,
            default_config={},
            **managers,
        )
    )
    assert result == ([], True, False)

    relative = _meta(
        tmp_path,
        image_list=[
            {"img_url": "i", "raw_url": "relative.png", "web_url": "w"}
        ],
    )
    result = asyncio.run(
        rehosting._check_hosts(
            relative,
            "TEST",
            {},
            approved_image_hosts=["imgbb"],
            default_config={},
            **managers,
        )
    )
    assert result == ([], True, False)

    # Cached malformed entries are skipped before a valid cached upload.
    state = Path(meta.base_dir) / "tmp" / meta.uuid
    state.mkdir(parents=True, exist_ok=True)
    (state / "reuploaded_images.json").write_text(
        json.dumps([{"raw_url": ""}, approved]), encoding="utf-8"
    )
    cached = _meta(tmp_path)
    result = asyncio.run(
        rehosting._check_hosts(
            cached,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            **managers,
        )
    )
    assert result[0] == [approved]

    # None approved hosts in the low-level handler is normalized before the
    # explicit skip path.
    skipped = _meta(
        tmp_path,
        skip_imghost_upload=True,
        tracker_image_collections={"TEST": {"screenshots": [approved]}},
    )
    assert asyncio.run(
        rehosting._handle_image_upload(
            skipped,
            "TEST",
            {},
            approved_image_hosts=None,
            default_config=_default(),
            **managers,
        )
    ) == ([approved], False, False)

    # HDDVD selects the largest EVO as its capture source.
    hddvd = _meta(
        tmp_path,
        uuid="hddvd",
        is_disc="HDDVD",
        discs=[{"largest_evo": str(tmp_path / "largest.evo")}],
        screens=1,
    )
    dirs = _dirs(hddvd)
    _image(dirs["screens"])
    _Uploader.reset([approved])
    assert asyncio.run(
        rehosting._handle_image_upload(
            hddvd,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(screens=1),
            **managers,
        )
    )[0]

    # Existing screenshots with no configured hosts reach the host-selection
    # guard rather than the capture guard.
    no_hosts = _meta(tmp_path, uuid="no-hosts", screens=1)
    _image(_dirs(no_hosts)["screens"])
    assert asyncio.run(
        rehosting._handle_image_upload(
            no_hosts,
            "TEST",
            {},
            approved_image_hosts=["imgbb"],
            default_config={},
            **managers,
        )
    ) == ([], True, False)


def test_partial_additional_collection_upload(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    dirs = _dirs(meta)
    one = _image(dirs["menu"], "one.png")
    two = _image(dirs["menu"], "two.png")
    meta.menu_images = [
        {
            "raw_url": "https://bad.invalid/one.png",
            "local_file_path": str(one),
        },
        {
            "raw_url": "https://bad.invalid/two.png",
            "local_file_path": str(two),
        },
    ]
    _Uploader.reset(
        [
            {
                "img_url": "i",
                "raw_url": "https://imgbb.com/one.png",
                "web_url": "w",
            }
        ]
    )
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"imgbb.com": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config=_default(),
            uploadscreens_manager=_Uploader(),
        )
    )
    collection = meta.tracker_image_collections["TEST"]["menu_images"]
    assert collection[0]["raw_url"] == "https://imgbb.com/one.png"
    assert collection[1]["raw_url"] == "https://bad.invalid/two.png"
