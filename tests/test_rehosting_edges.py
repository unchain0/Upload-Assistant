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
from src.domain_models.tracker_image_policy import (
    get_tracker_image_collection,
    set_tracker_image_collection,
)
from src.integrations.image_hosts import rehosting
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)


class _Response:
    def __init__(
        self, content: bytes = b"image", *, error: Exception | None = None
    ) -> None:
        self.content = content
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error


class _AsyncClient:
    response: ClassVar[_Response] = _Response()
    error: ClassVar[Exception | None] = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> _Response:
        if self.error is not None:
            raise self.error
        return self.response


class _TakeScreens:
    def __init__(self) -> None:
        self.disc_screenshots = AsyncMock()
        self.dvd_screenshots = AsyncMock()
        self.xxx_contact_sheets = AsyncMock()
        self.screenshots = AsyncMock()


class _Uploads:
    def __init__(self, result: list[dict[str, str]] | None = None) -> None:
        self.result = result or []
        self.upload_screens = AsyncMock(side_effect=self._upload)

    async def _upload(self, *_args: object, **_kwargs: object):
        return self.result, len(self.result)


def _image(
    host: str = "i.ibb.co", name: str = "screen-0.png"
) -> dict[str, str]:
    return {
        "img_url": f"https://{host}/{name}",
        "raw_url": f"https://{host}/{name}",
        "web_url": f"https://{host}/view/{name}",
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    video = tmp_path / "video.mkv"
    video.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "release",
        "path": str(video),
        "video": str(video),
        "filelist": [str(video)],
        "title": "Example: Release",
        "category": "MOVIE",
        "screens": 2,
        "image_list": [],
        "imghost": "lostimg",
        "is_disc": "",
        "discs": [],
        "bdinfo": {},
        "skip_imghost_upload": False,
        "debug": True,
        "tracker_image_collections": {},
    }
    state.update(values)
    meta = Meta(state)
    (tmp_path / "tmp" / meta.uuid / "screenshots").mkdir(
        parents=True, exist_ok=True
    )
    return meta


def _run_handle(
    meta: Meta,
    takes: _TakeScreens,
    uploads: _Uploads,
    *,
    tracker: str = "TEST",
    config: dict[str, Any] | None = None,
    approved: list[str] | None = None,
    mapping: dict[str, str] | None = None,
    index: int = 1,
):
    return asyncio.run(
        rehosting._handle_image_upload(
            meta,
            tracker,
            mapping or {"i.ibb.co": "imgbb", "lostimg.cc": "lostimg"},
            approved_image_hosts=approved
            if approved is not None
            else ["imgbb"],
            img_host_index=index,
            default_config=config
            if config is not None
            else {"img_host_1": "imgbb", "screens": 2},
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )


def test_small_helpers_and_manager_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert rehosting._as_str("value") == "value"
    assert rehosting._as_str(1) is None
    assert (
        asyncio.run(rehosting.match_host("sub.imgbox.com", ["imgbox.com"]))
        == "imgbox.com"
    )
    assert (
        asyncio.run(rehosting.match_host("other.invalid", ["imgbox.com"]))
        == "other.invalid"
    )
    assert (
        asyncio.run(rehosting.sanitize_filename('a:b/c?d*e"f'))
        == "a_b_c_d_e_f"
    )
    assert (
        rehosting._image_host(
            "https://sub.i.ibb.co/a.png", {"i.ibb.co": "imgbb"}
        )
        == "imgbb"
    )
    assert rehosting._image_host("not-a-url", {}) == ""
    assert rehosting._collection_directory(_meta(tmp_path), "unknown") is None

    missing = tmp_path / "missing"
    assert not rehosting._safe_remove(str(missing))
    target = tmp_path / "remove.txt"
    target.write_text("remove", encoding="utf-8")
    assert rehosting._safe_remove(str(target)) and not target.exists()
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    target.write_text("keep", encoding="utf-8")
    assert not rehosting._safe_remove(str(target))

    meta = _meta(tmp_path)
    with pytest.raises(ValueError, match="default_config"):
        asyncio.run(rehosting._check_hosts(meta, "TEST", {}))
    with pytest.raises(ValueError, match="takescreens_manager"):
        asyncio.run(
            rehosting._check_hosts(meta, "TEST", {}, default_config={})
        )
    with pytest.raises(ValueError, match="uploadscreens_manager"):
        asyncio.run(
            rehosting._check_hosts(
                meta,
                "TEST",
                {},
                default_config={},
                takescreens_manager=_TakeScreens(),
            )
        )
    with pytest.raises(ValueError, match="default_config"):
        asyncio.run(rehosting._handle_image_upload(meta, "TEST", {}))
    with pytest.raises(ValueError, match="takescreens_manager"):
        asyncio.run(
            rehosting._handle_image_upload(meta, "TEST", {}, default_config={})
        )
    with pytest.raises(ValueError, match="uploadscreens_manager"):
        asyncio.run(
            rehosting._handle_image_upload(
                meta,
                "TEST",
                {},
                default_config={},
                takescreens_manager=_TakeScreens(),
            )
        )


def test_tracker_policy_sync_async_legacy_and_music(tmp_path: Path) -> None:
    meta = _meta(tmp_path)

    class PolicyTracker:
        tracker = "POLICY"
        image_host_policy = ImageHostPolicy({"i.ibb.co": "imgbb"}, ("imgbb",))
        rehost_images_manager = SimpleNamespace(check_policy=AsyncMock())

    asyncio.run(rehosting.check_tracker_image_hosts(meta, PolicyTracker()))
    PolicyTracker.rehost_images_manager.check_policy.assert_awaited_once()

    calls: list[str] = []

    class AsyncTracker:
        async def check_image_hosts(self, _meta: Meta) -> None:
            calls.append("async")

    class SyncTracker:
        def check_image_hosts(self, _meta: Meta) -> None:
            calls.append("sync")

    asyncio.run(rehosting.check_tracker_image_hosts(meta, AsyncTracker()))
    asyncio.run(rehosting.check_tracker_image_hosts(meta, SyncTracker()))
    asyncio.run(
        rehosting.check_tracker_image_hosts(
            Meta(category="MUSIC"), AsyncTracker()
        )
    )
    assert calls == ["async", "sync"]


def test_local_paths_and_download_success_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path)
    local = tmp_path / "local.png"
    local.write_bytes(b"local")
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
                {"local_file_path": str(tmp_path / "missing")},
            )
        )
        is None
    )

    menu_dir = rehosting.menu_screenshots_dir(meta.base_dir, meta.uuid)
    menu_dir.mkdir(parents=True, exist_ok=True)
    candidate = menu_dir / "menu.png"
    candidate.write_bytes(b"menu")
    assert (
        asyncio.run(
            rehosting._local_image_path(
                meta, "menu_images", {"raw_url": "https://x.invalid/menu.png"}
            )
        )
        == candidate
    )
    assert (
        asyncio.run(
            rehosting._local_image_path(
                meta, "unknown", {"raw_url": "https://x.invalid/menu.png"}
            )
        )
        is None
    )

    monkeypatch.setattr(rehosting.httpx, "AsyncClient", _AsyncClient)
    _AsyncClient.error = None
    _AsyncClient.response = _Response(b"downloaded")
    downloaded = asyncio.run(
        rehosting._download_image_for_rehost(
            meta, "menu_images", "https://x.invalid/no-extension"
        )
    )
    assert (
        downloaded is not None
        and downloaded.suffix == ".png"
        and downloaded.read_bytes() == b"downloaded"
    )

    _AsyncClient.error = httpx.RequestError("offline")
    assert (
        asyncio.run(
            rehosting._download_image_for_rehost(
                meta, "menu_images", "https://x.invalid/a.jpg"
            )
        )
        is None
    )
    _AsyncClient.error = None
    _AsyncClient.response = _Response(
        error=httpx.HTTPStatusError(
            "bad",
            request=httpx.Request("GET", "https://x"),
            response=httpx.Response(500),
        )
    )
    assert (
        asyncio.run(
            rehosting._download_image_for_rehost(
                meta, "menu_images", "https://x.invalid/a.webp"
            )
        )
        is None
    )


def test_additional_collections_guards_download_partial_and_invalid_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, skip_imghost_upload=True)
    uploads = _Uploads()
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbb"},
            uploadscreens_manager=uploads,
        )
    )
    uploads.upload_screens.assert_not_awaited()

    meta.skip_imghost_upload = False
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {},
            approved_image_hosts=[],
            default_config={"img_host_1": "imgbb"},
            uploadscreens_manager=uploads,
        )
    )
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbox"},
            uploadscreens_manager=uploads,
        )
    )

    local = tmp_path / "menu-local.png"
    local.write_bytes(b"menu")
    meta = _meta(
        tmp_path,
        menu_images=[
            {"raw_url": "https://i.ibb.co/approved.png"},
            {"raw_url": "https://lostimg.cc/remote.jpg"},
            {"raw_url": "https://lostimg.cc/missing.jpg"},
            {
                "raw_url": "https://lostimg.cc/local.png",
                "local_file_path": str(local),
            },
        ],
        spectrograms_images="invalid",
        dynamic_hdr_plot_images=[],
    )
    remote = tmp_path / "downloaded.jpg"
    remote.write_bytes(b"remote")
    downloads = iter((remote, None))
    monkeypatch.setattr(
        rehosting,
        "_download_image_for_rehost",
        AsyncMock(side_effect=lambda *_args: next(downloads)),
    )
    uploads = _Uploads(
        [
            _image("i.ibb.co", "remote.jpg"),
            _image("not-approved.invalid", "local.png"),
        ]
    )
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"i.ibb.co": "imgbb", "lostimg.cc": "lostimg"},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbb"},
            uploadscreens_manager=uploads,
        )
    )
    collection = get_tracker_image_collection(meta, "TEST", "menu_images")
    assert collection[1]["raw_url"].startswith("https://i.ibb.co/")
    assert collection[2]["raw_url"] == "https://lostimg.cc/missing.jpg"
    assert collection[3]["raw_url"] == "https://lostimg.cc/local.png"
    assert meta.imghost == "lostimg"


def test_check_hosts_existing_saved_tracker_and_retry_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    takes = _TakeScreens()
    uploads = _Uploads()
    config = {"img_host_1": "imgbb", "img_host_3": "imgbox"}
    mapping = {"i.ibb.co": "imgbb", "images.imgbox.com": "imgbox"}

    meta = _meta(tmp_path, skip_imghost_upload=True)
    set_tracker_image_collection(meta, "TEST", "screenshots", [_image()])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result == ([_image()], False, False)

    meta = _meta(tmp_path, image_list=[_image(), {"raw_url": ""}])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result[0] == []

    meta = _meta(tmp_path, image_list=[_image()])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result[0] == [_image()]

    saved = tmp_path / "tmp" / "release" / "reuploaded_images.json"
    saved.write_text(
        json.dumps([{"raw_url": ""}, _image(), _image("bad.invalid")]),
        encoding="utf-8",
    )
    meta = _meta(tmp_path)
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result[0] == [_image()]

    saved.write_text("not json", encoding="utf-8")
    meta = _meta(tmp_path)
    set_tracker_image_collection(meta, "TEST", "screenshots", [_image()])
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result[0] == [_image()]

    meta = _meta(tmp_path)
    monkeypatch.setattr(
        rehosting,
        "_handle_image_upload",
        AsyncMock(
            side_effect=[
                ([], True, True),
                ([_image("images.imgbox.com")], False, True),
            ]
        ),
    )
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbox"],
            default_config=config,
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result[0] == [_image("images.imgbox.com")]
    assert result[2] is True

    meta = _meta(tmp_path)
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            mapping,
            approved_image_hosts=["imgbb"],
            default_config={},
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert result == ([], True, False)


def test_check_hosts_cover_release_filter_and_manager_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    covers = tmp_path / "tmp" / "release" / "covers.json"
    covers.parent.mkdir(parents=True, exist_ok=True)
    covers.write_text(
        json.dumps(
            [
                _image(),
                {**_image(name="wrong.png"), "release_url": "https://wrong"},
                {**_image(name="right.png"), "release_url": "https://right"},
            ]
        ),
        encoding="utf-8",
    )
    meta = _meta(tmp_path, release_url="https://right")
    takes = _TakeScreens()
    uploads = _Uploads()
    result = asyncio.run(
        rehosting._check_hosts(
            meta,
            "covers",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbb"},
            takescreens_manager=takes,
            uploadscreens_manager=uploads,
        )
    )
    assert [Path(item["raw_url"]).name for item in result[0]] == ["right.png"]

    manager = RehostImagesManager({"DEFAULT": {"img_host_1": "imgbb"}})
    monkeypatch.setattr(
        rehosting,
        "_check_hosts",
        AsyncMock(return_value=([_image()], False, True)),
    )
    extra = AsyncMock()
    monkeypatch.setattr(
        rehosting, "_check_additional_image_collections", extra
    )
    assert asyncio.run(
        manager.check_hosts(
            meta, "TEST", {"i.ibb.co": "imgbb"}, approved_image_hosts=["imgbb"]
        )
    ) == ([_image()], False, True)
    extra.assert_awaited_once()
    extra.reset_mock()
    asyncio.run(
        manager.check_hosts(
            meta,
            "covers",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=["imgbb"],
        )
    )
    extra.assert_not_awaited()
    policy = ImageHostPolicy({"i.ibb.co": "imgbb"}, ("imgbb",), 2)
    asyncio.run(manager.check_policy(meta, "TEST", policy))

    monkeypatch.setattr(
        rehosting,
        "_handle_image_upload",
        AsyncMock(return_value=([_image()], False, False)),
    )
    assert asyncio.run(
        manager.handle_image_upload(
            meta, "TEST", {"i.ibb.co": "imgbb"}, ["imgbb"]
        )
    )[0] == [_image()]


def test_handle_existing_upload_persistence_invalid_host_and_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, screens=1)
    screenshot = (
        tmp_path / "tmp" / "release" / "screenshots" / "Example_ Release-0.png"
    )
    screenshot.write_bytes(b"screen")
    meta.image_list = [_image(name=screenshot.name)]
    monkeypatch.setattr(
        rehosting, "manifest_files", lambda *_args: [screenshot]
    )
    uploads = _Uploads([_image()])
    takes = _TakeScreens()
    result = _run_handle(meta, takes, uploads)
    assert result[0] == [_image()]
    saved = screenshot.parent / "reuploaded_images.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == [_image()]
    assert meta.imghost == "lostimg"

    saved.write_text(json.dumps({"bad": True}), encoding="utf-8")
    meta = _meta(tmp_path, screens=1)
    result = _run_handle(meta, takes, uploads)
    assert result[0]

    uploads = _Uploads([_image("not-approved.invalid")])
    meta = _meta(tmp_path, screens=1)
    result = _run_handle(meta, takes, uploads)
    assert result[1] is True
    assert meta.imghost == "lostimg"

    meta = _meta(tmp_path, screens=1)
    result = _run_handle(
        meta,
        takes,
        _Uploads(),
        config={"img_host_1": "imgbox"},
        approved=["imgbb"],
    )
    assert result == ([], True, True)

    meta = _meta(tmp_path, screens=1, skip_imghost_upload=True)
    result = _run_handle(meta, takes, uploads)
    assert result[0] == [] and result[1] is False


def test_handle_generation_variants_no_path_no_screens_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    takes = _TakeScreens()
    uploads = _Uploads([_image()])
    generated = tmp_path / "generated-0.png"
    generated.write_bytes(b"generated")

    def generation_manifest() -> object:
        calls = iter(([], [generated]))
        monkeypatch.setattr(
            rehosting,
            "manifest_files",
            lambda *_args: next(calls, [generated]),
        )

    generation_manifest()
    bdmv = _meta(tmp_path, uuid="bdmv", is_disc="BDMV", screens=1)
    assert _run_handle(bdmv, takes, uploads)[0]
    takes.disc_screenshots.assert_awaited()

    dvd = _meta(
        tmp_path,
        uuid="dvd",
        is_disc="DVD",
        discs=[{"name": "DISC"}],
        screens=2,
    )
    dvd_screen = tmp_path / "tmp" / "dvd" / "screenshots" / "DISC-0.png"
    dvd_screen.write_bytes(b"dvd")
    generation_manifest()
    assert _run_handle(dvd, takes, uploads)[0]
    takes.dvd_screenshots.assert_awaited()

    generation_manifest()
    xxx = _meta(tmp_path, uuid="xxx", category="XXX", screens=1)
    assert _run_handle(xxx, takes, uploads)[0]
    takes.xxx_contact_sheets.assert_awaited()

    generation_manifest()
    normal = _meta(tmp_path, uuid="normal", screens=1)
    assert _run_handle(normal, takes, uploads)[0]
    takes.screenshots.assert_awaited()

    no_path = _meta(
        tmp_path, uuid="no-path", video="", filelist=[], path="", screens=1
    )
    monkeypatch.setattr(rehosting, "manifest_files", lambda *_args: [])
    assert _run_handle(no_path, takes, uploads) == ([], True, False)

    takes.screenshots.side_effect = RuntimeError("capture failed")
    failed = _meta(tmp_path, uuid="failed", screens=1)
    assert _run_handle(failed, takes, uploads) == ([], True, False)


def test_handle_covers_release_url_cleanup_write_failure_and_hidden_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artwork = rehosting.artwork_dir(str(tmp_path), "release")
    artwork.mkdir(parents=True, exist_ok=True)
    cover = artwork / "cover_1.jpg"
    cover.write_bytes(b"cover")
    meta = _meta(tmp_path, release_url="https://release", screens=1)
    uploads = _Uploads([_image()])
    result = _run_handle(meta, _TakeScreens(), uploads, tracker="covers")
    assert result[0][0]["raw_url"] == _image()["raw_url"]
    saved = tmp_path / "tmp" / "release" / "covers.json"
    assert (
        json.loads(saved.read_text(encoding="utf-8"))[0]["release_url"]
        == "https://release"
    )
    assert not cover.exists()

    screenshot_dir = tmp_path / "tmp" / "release" / "screenshots"
    hidden = screenshot_dir / ".Example_ Release-0.png"
    indexed = screenshot_dir / "Other-1.png"
    unwanted = screenshot_dir / "FILE-2.png"
    for path in (hidden, indexed, unwanted):
        path.write_bytes(b"image")
    meta = _meta(tmp_path, screens=1)
    result = _run_handle(meta, _TakeScreens(), uploads)
    assert result[0]

    real_open = rehosting.aiofiles.open

    def fail_write(
        path: object, mode: str = "r", *args: object, **kwargs: object
    ):
        if "w" in mode:
            raise OSError("read only")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(rehosting.aiofiles, "open", fail_write)
    meta = _meta(tmp_path, screens=1)
    assert _run_handle(meta, _TakeScreens(), uploads)[0]


def test_remaining_policy_collection_and_host_edge_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unrestricted:
        pass

    class Left:
        image_host_policy = ImageHostPolicy({}, ("imgbb",))

    class Right:
        image_host_policy = ImageHostPolicy({}, ("imgbox",))

    assert (
        rehosting.select_common_image_host(
            {"img_host_1": "imgbb"}, ["NONE"], {"NONE": Unrestricted}
        )
        is None
    )
    assert (
        rehosting.select_common_image_host(
            {"img_host_1": "imgbb"},
            ["LEFT", "RIGHT"],
            {"LEFT": Left, "RIGHT": Right},
        )
        is None
    )

    meta = _meta(tmp_path)
    assert rehosting._collection_directory(
        meta, "spectrograms_images"
    ) == rehosting.spectrograms_dir(meta.base_dir, meta.uuid)
    assert rehosting._collection_directory(
        meta, "dynamic_hdr_plot_images"
    ) == rehosting.dynamic_hdr_plots_dir(meta.base_dir, meta.uuid)

    local = tmp_path / "local.png"
    local.write_bytes(b"image")
    meta.menu_images = [
        "malformed",
        {"raw_url": "https://i.ibb.co/already.png"},
    ]
    uploads = _Uploads()
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbb"},
            uploadscreens_manager=uploads,
        )
    )
    uploads.upload_screens.assert_not_awaited()
    assert (
        get_tracker_image_collection(meta, "TEST", "menu_images")
        == meta.menu_images
    )

    meta.menu_images = [
        {
            "raw_url": "https://bad.invalid/one.png",
            "local_file_path": str(local),
        },
        {
            "raw_url": "https://bad.invalid/two.png",
            "local_file_path": str(local),
        },
    ]
    uploads = _Uploads([_image(name="one.png")])
    asyncio.run(
        rehosting._check_additional_image_collections(
            meta,
            "TEST",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=["imgbb"],
            default_config={"img_host_1": "imgbb"},
            uploadscreens_manager=uploads,
        )
    )
    assert get_tracker_image_collection(meta, "TEST", "menu_images")[0][
        "raw_url"
    ].endswith("one.png")

    takes = _TakeScreens()
    meta = _meta(
        tmp_path,
        image_list=[
            {"raw_url": "https://bad.invalid/a.png"},
            {"raw_url": "/relative.png"},
        ],
    )
    monkeypatch.setattr(
        rehosting,
        "_handle_image_upload",
        AsyncMock(return_value=([], False, False)),
    )
    asyncio.run(
        rehosting._check_hosts(
            meta,
            "TEST",
            {"i.ibb.co": "imgbb"},
            approved_image_hosts=None,
            default_config={"img_host_1": "imgbb"},
            takescreens_manager=takes,
            uploadscreens_manager=_Uploads(),
        )
    )


def test_handle_remaining_title_hddvd_empty_upload_and_no_config(
    tmp_path: Path,
) -> None:
    takes = _TakeScreens()
    screenshot_dir = tmp_path / "tmp" / "title" / "screenshots"
    screenshot_dir.mkdir(parents=True)
    title_match = screenshot_dir / "Example_ Release-title.png"
    title_match.write_bytes(b"image")
    meta = _meta(
        tmp_path,
        uuid="title",
        screens=1,
        image_list=[{"raw_url": "https://host.invalid/not-the-same.png"}],
    )
    result = _run_handle(meta, takes, _Uploads([_image()]))
    assert result[0]

    hddvd_screen = tmp_path / "hddvd-0.png"
    hddvd_screen.write_bytes(b"image")
    hddvd = _meta(
        tmp_path,
        uuid="hddvd",
        is_disc="HDDVD",
        discs=[{"largest_evo": str(tmp_path / "movie.evo")}],
        screens=1,
    )
    rehosting.set_tracker_image_collection(hddvd, "TEST", "screenshots", [])
    original_manifest = rehosting.manifest_files
    rehosting.manifest_files = lambda *_args: [hddvd_screen]
    try:
        assert _run_handle(hddvd, takes, _Uploads([_image()]), approved=None)[
            0
        ]
    finally:
        rehosting.manifest_files = original_manifest

    existing = tmp_path / "existing-0.png"
    existing.write_bytes(b"image")
    no_config = _meta(tmp_path, uuid="no-config", screens=1)
    rehosting.manifest_files = lambda *_args: [existing]
    try:
        assert _run_handle(
            no_config, takes, _Uploads(), config={"screens": 1}
        ) == ([], True, False)
        empty_upload = _meta(tmp_path, uuid="empty-upload", screens=1)
        assert _run_handle(
            empty_upload,
            takes,
            _Uploads(),
            config={"img_host_1": "imgbb", "screens": 1},
        ) == ([], False, False)
    finally:
        rehosting.manifest_files = original_manifest


def test_handle_defaults_approved_hosts_when_unspecified(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path, skip_imghost_upload=True)
    result = asyncio.run(
        rehosting._handle_image_upload(
            meta,
            "TEST",
            {},
            approved_image_hosts=None,
            default_config={},
            takescreens_manager=_TakeScreens(),
            uploadscreens_manager=_Uploads(),
        )
    )
    assert result == ([], False, False)
