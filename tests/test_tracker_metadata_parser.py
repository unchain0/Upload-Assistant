"""Behavioral coverage for tracker metadata reconciliation and images."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from PIL import Image

import src.services.tracker_metadata_parser as parser
from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta


def _config(*, screens: object = 2) -> dict[str, Any]:
    return {
        "DEFAULT": {
            "screens": screens,
            "tracker_description_mode": "text_and_images",
        },
        "TRACKERS": {
            "BEYONDHD": {"api_key": "a" * 32, "bhd_rss_key": "r" * 32},
        },
    }


def _meta(tmp_path: Path, **values: Any) -> Meta:
    defaults: dict[str, Any] = {
        "base_dir": str(tmp_path),
        "uuid": "metadata",
        "resolution": "1080p",
        "category": "MOVIE",
        "type": "WEBDL",
        "name": "Example.2026.1080p.WEB-DL-GRP",
        "path": str(tmp_path / "Example.2026.1080p.WEB-DL-GRP.mkv"),
        "filelist": [str(tmp_path / "Example.2026.1080p.WEB-DL-GRP.mkv")],
        "image_list": [],
        "tracker_ids": {},
        "tracker_description_mode": "text_and_images",
        "tracker_search_term": "Example 2026 1080p",
        "unattended": True,
        "unattended_confirm": False,
        "manual_category": False,
        "keep_images": True,
        "debug": True,
        "is_disc": "",
    }
    defaults.update(values)
    return Meta(defaults)


def _png(width: int, height: int) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), color=(1, 2, 3)).save(
        stream, format="PNG"
    )
    return stream.getvalue()


class _Response:
    status_code = 200
    content = _png(1920, 1080)
    headers: ClassVar[dict[str, str]] = {"Content-Type": "image/png"}


class _AsyncClient:
    response: ClassVar[object] = _Response()
    creation_error: ClassVar[BaseException | None] = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        if self.creation_error is not None:
            raise self.creation_error

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> Any:
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_manager_configuration_and_confirmation_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = parser.TrackerMetaManager(_config(screens="3"))
    assert parser.expected_images == 3
    assert manager.config["DEFAULT"]["screens"] == "3"

    unattended = _meta(tmp_path, unattended=True, unattended_confirm=False)
    assert (
        asyncio.run(
            manager.prompt_user_for_confirmation("continue?", unattended)
        )
        is False
    )

    monkeypatch.setattr(
        parser.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: True
    )
    assert (
        asyncio.run(
            manager.prompt_user_for_confirmation(
                "continue?", _meta(tmp_path, unattended=False)
            )
        )
        is True
    )

    monkeypatch.setattr(
        parser.cli_ui,
        "ask_yes_no",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError),
    )
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(parser.prompt_user_for_confirmation("continue?"))


def test_check_image_link_success_and_all_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parser.httpx, "AsyncClient", _AsyncClient)
    _AsyncClient.creation_error = None
    _AsyncClient.response = _Response()
    assert (
        asyncio.run(
            parser.check_image_link("https://pixhost.to/show/1/image.png")
        )
        is True
    )

    non_image = _Response()
    non_image.headers = {"Content-Type": "text/html"}
    _AsyncClient.response = non_image
    assert (
        asyncio.run(parser.check_image_link("https://img.invalid/not-image"))
        is False
    )

    corrupt = _Response()
    corrupt.content = b"broken"
    _AsyncClient.response = corrupt
    assert (
        asyncio.run(parser.check_image_link("https://img.invalid/corrupt"))
        is False
    )

    failed = _Response()
    failed.status_code = 503
    _AsyncClient.response = failed
    assert (
        asyncio.run(parser.check_image_link("https://img.invalid/failure"))
        is False
    )

    for error in (
        TimeoutError("timeout"),
        httpx.ReadError("read error"),
        RuntimeError("other"),
    ):
        _AsyncClient.response = error
        assert (
            asyncio.run(parser.check_image_link("https://img.invalid/error"))
            is False
        )

    _AsyncClient.creation_error = RuntimeError("session")
    assert (
        asyncio.run(parser.check_image_link("https://img.invalid/session"))
        is False
    )
    _AsyncClient.creation_error = None


def test_check_images_concurrently_filters_saves_limits_and_handles_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(_config(screens=1))
    monkeypatch.setattr(
        parser,
        "check_image_link",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(parser.httpx, "AsyncClient", _AsyncClient)
    _AsyncClient.creation_error = None
    _AsyncClient.response = _Response()

    meta = _meta(tmp_path)
    images = [
        {"raw_url": "https://img.invalid/one.png", "img_url": "one"},
        {"raw_url": "https://img.invalid/one.png", "img_url": "duplicate"},
        {"raw_url": "https://pixhost.to/show/2/two.png", "img_url": "two"},
        {"raw_url": "https://image.tmdb.org/poster.png", "img_url": "tmdb"},
        {"img_url": "missing"},
    ]
    valid = asyncio.run(parser.check_images_concurrently(images, meta))
    assert len(valid) == 1
    assert (tmp_path / "tmp" / "metadata" / "one.png").is_file()
    assert meta.image_sizes["https://img.invalid/one.png"] > 0

    assert (
        asyncio.run(
            parser.check_images_concurrently(
                images, _meta(tmp_path, resolution="OTHER")
            )
        )
        == []
    )

    out_of_range = _Response()
    out_of_range.content = _png(720, 400)
    _AsyncClient.response = out_of_range
    assert (
        asyncio.run(
            parser.check_images_concurrently([images[0]], _meta(tmp_path))
        )
        == []
    )

    dvd = _Response()
    dvd.content = _png(720, 600)
    _AsyncClient.response = dvd
    assert asyncio.run(
        parser.check_images_concurrently(
            [images[0]], _meta(tmp_path, resolution="576p", is_disc="DVD")
        )
    )

    for response in (
        SimpleNamespace(status_code=500, content=b"", headers={}),
        TimeoutError("timeout"),
        httpx.ReadError("read"),
    ):
        _AsyncClient.response = response
        assert (
            asyncio.run(
                parser.check_images_concurrently([images[0]], _meta(tmp_path))
            )
            == []
        )

    _AsyncClient.creation_error = RuntimeError("session")
    assert (
        asyncio.run(
            parser.check_images_concurrently([images[0]], _meta(tmp_path))
        )
        == []
    )
    _AsyncClient.creation_error = None

    monkeypatch.setattr(
        parser,
        "check_image_link",
        lambda *_args, **_kwargs: _async_value(False),
    )
    assert (
        asyncio.run(
            parser.check_images_concurrently([images[0]], _meta(tmp_path))
        )
        == []
    )
    monkeypatch.setattr(
        parser,
        "check_image_link",
        lambda *_args, **_kwargs: _async_error(RuntimeError("check")),
    )
    assert (
        asyncio.run(
            parser.check_images_concurrently([images[0]], _meta(tmp_path))
        )
        == []
    )


def _async_value(value: Any):
    async def resolve() -> Any:
        return value

    return resolve()


def _async_error(error: BaseException):
    async def fail() -> Any:
        raise error

    return fail()


@pytest.mark.parametrize(
    ("category", "expected"),
    [("Movie", "MOVIE"), ("TV Show", "TV"), ("Other", "MOVIE")],
)
def test_update_unit3d_data_imports_ids_description_images_and_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    expected: str,
) -> None:
    meta = _meta(tmp_path, category="MOVIE", tracker_ids={"AITHER": "77"})
    images = [{"raw_url": "https://img.invalid/a.png", "img_url": "a"}]
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda *_args, **_kwargs: _async_value(images),
    )
    handled: list[str] = []
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda _meta, tracker, _images: _async_value(handled.append(tracker)),
    )
    data = (
        123,
        7654321,
        456,
        789,
        "description",
        category,
        "hash",
        images,
        "Release.Name",
        "extra",
    )

    assert (
        asyncio.run(parser.update_meta_with_unit3d_data(meta, data, "AITHER"))
        is True
    )
    assert (meta.tmdb_id, meta.imdb_id, meta.tvdb_id, meta.mal_id) == (
        123,
        7654321,
        456,
        789,
    )
    assert meta.category == expected
    assert meta.description == "description"
    assert meta.image_list == images
    assert handled == ["AITHER"]
    assert meta.aither_filename == "Release.Name"
    assert meta.description_fingerprint
    assert meta.description_candidates[-1]["selected"] is True

    manual = _meta(
        tmp_path, manual_category=True, tracker_description_mode="ids"
    )
    asyncio.run(
        parser.update_meta_with_unit3d_data(
            manual, (0, 0, 0, 0, "ignored", "TV", "", images, ""), "AITHER"
        )
    )
    assert manual.category == "MOVIE"
    assert manual.description == ""
    assert manual.image_list == []


class _PTP:
    ptp_id: int | None = 12
    imdb_id = 1234567

    async def get_ptp_id_imdb(
        self, *_args: object
    ) -> tuple[int, int | None, str]:
        return self.imdb_id, self.ptp_id, "hash"

    async def get_ptp_description(
        self, *_args: object
    ) -> list[dict[str, str]]:
        return [{"raw_url": "https://img.invalid/ptp.png", "img_url": "ptp"}]

    async def get_imdb_from_torrent_id(
        self, _torrent_id: int
    ) -> tuple[int, str]:
        return self.imdb_id, "hash"


@pytest.mark.asyncio
async def test_update_metadata_passthepopcorn_all_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = [{"raw_url": "https://img.invalid/ptp.png", "img_url": "ptp"}]
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda *_args, **_kwargs: _async_value(images),
    )
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda *_args, **_kwargs: _async_value(None),
    )

    accepted = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN", _PTP(), accepted, "term", "folder"
    )
    assert (
        matched
        and updated.imdb_id == 1234567
        and updated.get_tracker_id("PTP") == "12"
    )

    declined = _meta(
        tmp_path,
        unattended=False,
        imdb_id=0,
        description="old",
        image_list=images,
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(False),
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN", _PTP(), declined, "term", "folder"
    )
    assert (
        not matched
        and updated.get_tracker_id("PTP") is None
        and updated.image_list == []
    )

    unattended = _meta(tmp_path, unattended=True)
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN", _PTP(), unattended, "term", "folder"
    )
    assert matched and updated.image_list == images

    existing = _meta(tmp_path, tracker_ids={"PASSTHEPOPCORN": "12"})
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN", _PTP(), existing, "term", "folder"
    )
    assert matched and updated.skipit

    no_imdb = _PTP()
    no_imdb.imdb_id = 0
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN",
        no_imdb,
        _meta(tmp_path, tracker_ids={"PASSTHEPOPCORN": "12"}),
        "term",
        "folder",
    )
    assert not matched

    no_match = _PTP()
    no_match.ptp_id = None
    updated, matched = await parser.update_metadata_from_tracker(
        "PASSTHEPOPCORN", no_match, _meta(tmp_path), "term", "folder"
    )
    assert not matched


@pytest.mark.asyncio
async def test_update_metadata_beyondhd_api_and_unit3d_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(_config())
    calls: list[dict[str, Any]] = []

    async def bhd_result(*_args: object, **kwargs: Any) -> tuple[int, int]:
        calls.append(kwargs)
        return 1234567, 789

    monkeypatch.setattr(parser.BtnIdManager, "get_bhd_torrents", bhd_result)
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda images, _meta: _async_value(list(images)),
    )
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda *_args, **_kwargs: _async_value(None),
    )

    for meta in (
        _meta(tmp_path, tracker_ids={"BEYONDHD": "9"}),
        _meta(tmp_path, is_disc="BDMV"),
        _meta(tmp_path, is_disc="", keep_folder=False, isdir=False),
    ):
        updated, matched = await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), meta, "term", "folder"
        )
        assert (
            matched and updated.tmdb_id == 789 and updated.imdb_id == 1234567
        )
    assert any("torrent_id" in call for call in calls)
    assert any("foldername" in call for call in calls)
    assert any("filename" in call for call in calls)

    parser._apply_config(
        {
            "DEFAULT": {"screens": 1},
            "TRACKERS": {
                "BEYONDHD": {"api_key": "short", "bhd_rss_key": None}
            },
        }
    )
    assert (
        await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), _meta(tmp_path), "term", "folder"
        )
    )[1] is False

    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((0, 0)),
    )
    assert (
        await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), _meta(tmp_path), "term", "folder"
        )
    )[1] is False

    class Unit3D:
        id_url = "id"
        search_url = "search"
        torrent_url = "torrent"

    unit_calls: list[dict[str, Any]] = []

    async def unit_info(*_args: object, **kwargs: Any) -> tuple[Any, ...]:
        unit_calls.append(kwargs)
        return (
            123,
            456,
            789,
            0,
            "description",
            "MOVIE",
            "hash",
            [],
            "Release",
        )

    monkeypatch.setattr(parser.Common, "unit3d_torrent_info", unit_info)
    parser.api_trackers.add("AITHER")
    try:
        assert (
            await parser.update_metadata_from_tracker(
                "AITHER",
                Unit3D(),
                _meta(tmp_path, tracker_ids={"AITHER": "3"}),
                "term",
                "folder",
            )
        )[1]
        assert (
            await parser.update_metadata_from_tracker(
                "AITHER",
                Unit3D(),
                _meta(tmp_path),
                "term",
                "folder",
                torrent_id="4",
            )
        )[1]
        assert (
            await parser.update_metadata_from_tracker(
                "AITHER", Unit3D(), _meta(tmp_path), "term", "folder"
            )
        )[1]
        monkeypatch.setattr(
            parser.Common,
            "unit3d_torrent_info",
            lambda *_args, **_kwargs: _async_value(
                (0, 0, 0, 0, "", "", "", [], "")
            ),
        )
        assert not (
            await parser.update_metadata_from_tracker(
                "AITHER", Unit3D(), _meta(tmp_path), "term", "folder"
            )
        )[1]
    finally:
        parser.api_trackers.discard("AITHER")
    assert any("id" in call for call in unit_calls)
    assert any("file_name" in call for call in unit_calls)


class _HDB:
    async def get_info_from_torrent_id(
        self, _torrent_id: object
    ) -> tuple[int, int, str, str, str]:
        return (
            1234567,
            456,
            "Release",
            "hash",
            "[img]https://img.invalid/a.png[/img]Description",
        )

    async def search_filename(
        self, *_args: object
    ) -> tuple[int, int, str, str, str, int]:
        return 1234567, 456, "Release", "hash", "Description", 12


@pytest.mark.asyncio
async def test_update_metadata_hdbits_existing_search_accept_decline_and_unattended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        parser.BBCODE,
        "clean_hdb_description",
        lambda _self, _desc: (
            "cleaned",
            [{"raw_url": "https://img.invalid/a.png"}],
        ),
    )
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda images, _meta: _async_value(list(images)),
    )
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda *_args, **_kwargs: _async_value(None),
    )

    existing = _meta(tmp_path, tracker_ids={"HDBITS": "12"})
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), existing, "term", "folder"
    )
    assert matched and updated.description == "cleaned"

    accepted = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), accepted, "term", "folder"
    )
    assert matched and updated.get_tracker_id("HDBITS") == "12"

    edited = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: "e"
    )
    monkeypatch.setattr(parser.click, "edit", lambda **_kwargs: "edited")
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), edited, "term", "folder"
    )
    assert matched and updated.description == "edited"

    discarded = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: "d"
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), discarded, "term", "folder"
    )
    assert matched and updated.hdb_description == ""

    declined = _meta(tmp_path, unattended=False)
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(False),
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), declined, "term", "folder"
    )
    assert not matched and updated.get_tracker_id("HDBITS") is None

    unattended = _meta(tmp_path, unattended=True)
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), unattended, "term", "folder"
    )
    assert matched and updated.description == "cleaned"

    class EmptyHDB(_HDB):
        async def get_info_from_torrent_id(
            self, _torrent_id: object
        ) -> tuple[None, None, None, None, None]:
            return None, None, None, None, None

        async def search_filename(
            self, *_args: object
        ) -> tuple[None, None, None, None, None, None]:
            return None, None, None, None, None, None

    assert not (
        await parser.update_metadata_from_tracker(
            "HDBITS",
            EmptyHDB(),
            _meta(tmp_path, tracker_ids={"HDBITS": "12"}),
            "term",
            "folder",
        )
    )[1]
    assert not (
        await parser.update_metadata_from_tracker(
            "HDBITS", EmptyHDB(), _meta(tmp_path), "term", "folder"
        )
    )[1]


@pytest.mark.asyncio
async def test_handle_image_list_keep_discard_delete_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = [
        {
            "img_url": "https://img.invalid/a.png",
            "raw_url": "https://img.invalid/a.png",
        }
    ]
    unattended = _meta(tmp_path, image_list=images, unattended=True)
    await parser.handle_image_list(unattended, "AITHER", images)
    assert unattended.image_list == images

    accepted = _meta(tmp_path, image_list=images, unattended=False)
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    await parser.handle_image_list(accepted, "AITHER", images)
    assert accepted.image_list == images

    directory = tmp_path / "tmp" / "metadata" / "screenshots"
    directory.mkdir(parents=True)
    (directory / "one.png").write_bytes(b"image")
    declined = _meta(
        tmp_path, image_list=images, image_sizes={"x": 1}, unattended=False
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(False),
    )
    monkeypatch.setattr(parser, "screenshots_dir", lambda *_args: directory)
    await parser.handle_image_list(declined, "AITHER")
    assert declined.image_list == [] and declined.image_sizes == {}
    assert not (directory / "one.png").exists()

    broken = directory / "broken.png"
    broken.write_bytes(b"image")
    monkeypatch.setattr(
        Path, "unlink", lambda _self: (_ for _ in ()).throw(OSError("denied"))
    )
    await parser.handle_image_list(
        _meta(tmp_path, image_list=images, unattended=False), "AITHER"
    )

    empty = _meta(tmp_path, image_list=[])
    await parser.handle_image_list(empty, "AITHER")


def test_manager_wrapper_methods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = parser.TrackerMetaManager(_config())
    monkeypatch.setattr(
        parser,
        "check_image_link",
        lambda *_args, **_kwargs: _async_value(True),
    )
    assert asyncio.run(manager.check_image_link("https://img.invalid/a.png"))
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda images, _meta: _async_value(list(images)),
    )
    images = [{"raw_url": "https://img.invalid/a.png"}]
    assert (
        asyncio.run(manager.check_images_concurrently(images, _meta(tmp_path)))
        == images
    )
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda *_args, **_kwargs: _async_value(None),
    )
    asyncio.run(manager.handle_image_list(_meta(tmp_path), "AITHER", images))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("choice", "edited", "expected_description"),
    [
        ("e", "edited description", "edited description"),
        ("d", None, ""),
        ("", None, "imported description"),
    ],
)
async def test_beyondhd_interactive_description_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
    edited: str | None,
    expected_description: str,
) -> None:
    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((1234567, 789)),
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: choice
    )
    monkeypatch.setattr(parser.click, "edit", lambda **_kwargs: edited)
    images = [{"raw_url": "https://img.invalid/a.png", "img_url": "a"}]
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda value, _meta: _async_value(list(value)),
    )
    handled: list[str] = []
    monkeypatch.setattr(
        parser,
        "handle_image_list",
        lambda _meta, tracker, _images: _async_value(handled.append(tracker)),
    )

    meta = _meta(
        tmp_path,
        unattended=False,
        description="imported description",
        image_list=images,
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), meta, "term", "folder"
    )
    assert matched
    assert updated.description == expected_description
    if choice == "d":
        assert updated.image_list == []
    else:
        assert handled == ["BEYONDHD"]

    skipped = _meta(
        tmp_path,
        unattended=False,
        description="imported description",
        skipit=True,
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), skipped, "term", "folder"
    )
    assert matched and updated.saved_description


@pytest.mark.asyncio
async def test_beyondhd_nfo_keep_discard_retry_cleanup_and_decline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((1234567, 789)),
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(
        parser.asyncio, "sleep", lambda *_args, **_kwargs: _async_value(None)
    )

    nfo_dir = tmp_path / "tmp" / "metadata"
    nfo_dir.mkdir(parents=True, exist_ok=True)
    nfo_path = nfo_dir / "bhd.nfo"
    nfo_path.write_text("NFO description", encoding="utf-8")

    keep = _meta(
        tmp_path, unattended=False, bhd_nfo=True, nfo=True, description=""
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), keep, "term", "folder"
    )
    assert matched and nfo_path.exists() and updated.bhd_nfo

    nfo_path.write_text("NFO description", encoding="utf-8")
    discard = _meta(
        tmp_path, unattended=False, bhd_nfo=True, nfo=True, description=""
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: "d"
    )
    original_unlink = Path.unlink
    attempts = 0

    def flaky_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal attempts
        if path == nfo_path:
            attempts += 1
            if attempts < 3:
                raise OSError("busy")
        original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", flaky_unlink)
        updated, matched = await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), discard, "term", "folder"
        )
    assert matched and attempts == 3 and not nfo_path.exists()
    assert not updated.nfo and not updated.bhd_nfo

    nfo_path.write_text("NFO description", encoding="utf-8")
    declined = _meta(
        tmp_path,
        unattended=False,
        bhd_nfo=True,
        nfo=True,
        description="",
        image_list=[{"raw_url": "x"}],
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(False),
    )

    def denied_unlink(_path: Path, *_args: object, **_kwargs: object) -> None:
        raise OSError("denied")

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", denied_unlink)
        updated, matched = await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), declined, "term", "folder"
        )
    assert not matched and updated.image_list == [] and not updated.bhd_nfo


@pytest.mark.asyncio
async def test_beyondhd_image_rejection_and_nfo_cleanup_outer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc

    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((1234567, 789)),
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda *_args, **_kwargs: _async_value([]),
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    images = [{"raw_url": "https://img.invalid/a.png"}]
    meta = _meta(
        tmp_path, unattended=False, description="imported", image_list=images
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), meta, "term", "folder"
    )
    assert matched and updated.image_list == []

    nfo_dir = tmp_path / "tmp" / "metadata"
    nfo_dir.mkdir(parents=True, exist_ok=True)
    (nfo_dir / "bhd.nfo").write_text("NFO", encoding="utf-8")
    nfo = _meta(tmp_path, unattended=False, bhd_nfo=True, description="")
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: "d"
    )
    with monkeypatch.context() as context:
        context.setattr(
            gc,
            "collect",
            lambda: (_ for _ in ()).throw(RuntimeError("gc failed")),
        )
        updated, matched = await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), nfo, "term", "folder"
        )
    assert matched and not updated.bhd_nfo


@pytest.mark.asyncio
async def test_hdbits_empty_description_and_disabled_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        parser.BBCODE, "clean_hdb_description", lambda _self, _desc: ("", [])
    )
    existing = _meta(tmp_path, tracker_ids={"HDBITS": "12"}, keep_images=False)
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS",
        _HDB(),
        existing,
        "term",
        "folder",
        skip_tracker_descriptions=True,
    )
    assert matched and updated.image_list == []

    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    interactive = _meta(tmp_path, unattended=False, keep_images=False)
    updated, matched = await parser.update_metadata_from_tracker(
        "HDBITS", _HDB(), interactive, "term", "folder"
    )
    assert matched and updated.description == ""


def test_manager_update_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = parser.TrackerMetaManager(_config())
    monkeypatch.setattr(
        parser,
        "update_meta_with_unit3d_data",
        lambda *_args, **_kwargs: _async_value(True),
    )
    assert asyncio.run(
        manager.update_meta_with_unit3d_data(
            _meta(tmp_path), (0,) * 9, "AITHER"
        )
    )

    expected = (_meta(tmp_path), True)
    monkeypatch.setattr(
        parser,
        "update_metadata_from_tracker",
        lambda *_args, **_kwargs: _async_value(expected),
    )
    assert (
        asyncio.run(
            manager.update_metadata_from_tracker(
                "AITHER",
                SimpleNamespace(),
                _meta(tmp_path),
                "term",
                "folder",
                torrent_id="3",
            )
        )
        == expected
    )


@pytest.mark.asyncio
async def test_image_processing_and_gather_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(_config())
    monkeypatch.setattr(
        parser,
        "check_image_link",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(parser.httpx, "AsyncClient", _AsyncClient)
    _AsyncClient.creation_error = None
    _AsyncClient.response = _Response()
    image = {"raw_url": "https://img.invalid/broken.png", "img_url": "broken"}

    with monkeypatch.context() as context:
        context.setattr(
            parser.Image,
            "open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("broken image")
            ),
        )
        assert (
            await parser.check_images_concurrently([image], _meta(tmp_path))
            == []
        )

    with monkeypatch.context() as context:

        async def failed_gather(
            *_args: object, **_kwargs: object
        ) -> list[object]:
            for coroutine in _args:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
            raise RuntimeError("gather failed")

        context.setattr(parser.asyncio, "gather", failed_gather)
        assert (
            await parser.check_images_concurrently([image], _meta(tmp_path))
            == []
        )


@pytest.mark.asyncio
async def test_beyondhd_short_rss_and_unattended_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(
        {
            "DEFAULT": {"screens": 1},
            "TRACKERS": {
                "BEYONDHD": {"api_key": "a" * 32, "bhd_rss_key": "short"}
            },
        }
    )
    assert not (
        await parser.update_metadata_from_tracker(
            "BEYONDHD", SimpleNamespace(), _meta(tmp_path), "term", "folder"
        )
    )[1]

    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((1234567, 789)),
    )
    images = [{"raw_url": "https://img.invalid/a.png", "img_url": "a"}]
    valid = _meta(tmp_path, unattended=True, image_list=images)
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda value, _meta: _async_value(list(value)),
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), valid, "term", "folder"
    )
    assert matched and updated.image_list == images

    rejected = _meta(tmp_path, unattended=True, image_list=images)
    monkeypatch.setattr(
        parser,
        "check_images_concurrently",
        lambda *_args, **_kwargs: _async_value([]),
    )
    updated, matched = await parser.update_metadata_from_tracker(
        "BEYONDHD", SimpleNamespace(), rejected, "term", "folder"
    )
    assert matched and updated.image_list == []


@pytest.mark.asyncio
async def test_beyondhd_nfo_terminal_delete_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser._apply_config(_config())
    monkeypatch.setattr(
        parser.BtnIdManager,
        "get_bhd_torrents",
        lambda *_args, **_kwargs: _async_value((1234567, 789)),
    )
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(True),
    )
    monkeypatch.setattr(
        parser.cli_ui, "ask_string", lambda *_args, **_kwargs: "d"
    )
    monkeypatch.setattr(
        parser.asyncio, "sleep", lambda *_args, **_kwargs: _async_value(None)
    )
    nfo_dir = tmp_path / "tmp" / "metadata"
    nfo_dir.mkdir(parents=True, exist_ok=True)
    (nfo_dir / "bhd.nfo").write_text("NFO", encoding="utf-8")

    def always_fails(_path: Path, *_args: object, **_kwargs: object) -> None:
        raise OSError("still busy")

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", always_fails)
        updated, matched = await parser.update_metadata_from_tracker(
            "BEYONDHD",
            SimpleNamespace(),
            _meta(tmp_path, unattended=False, bhd_nfo=True, nfo=True),
            "term",
            "folder",
        )
    assert matched and not updated.bhd_nfo


@pytest.mark.asyncio
async def test_handle_image_list_decline_without_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "empty-screens"
    directory.mkdir()
    monkeypatch.setattr(parser, "screenshots_dir", lambda *_args: directory)
    monkeypatch.setattr(
        parser,
        "prompt_user_for_confirmation",
        lambda *_args, **_kwargs: _async_value(False),
    )
    meta = _meta(
        tmp_path,
        unattended=False,
        image_list=[{"img_url": "https://img.invalid/a.png"}],
    )
    await parser.handle_image_list(meta, "AITHER")
    assert meta.image_list == []
