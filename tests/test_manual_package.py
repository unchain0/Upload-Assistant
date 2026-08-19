from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest

from src.domain_models.release import Meta
from src.integrations.packaging import manual_package
from src.integrations.packaging.manual_package import ManualPackageManager


class _Response:
    status_code = 200
    content = b"cover"
    payload: ClassVar[dict[str, Any]] = {"files": [{"url": "https://files.invalid/package.tar"}]}

    def json(self) -> dict[str, Any]:
        return type(self).payload


class _Client:
    get_response: ClassVar[_Response] = _Response()
    post_response: ClassVar[_Response] = _Response()

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return type(self).get_response

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return type(self).post_response


class _Torrent:
    copied: ClassVar[list[tuple[str, bool]]] = []

    @classmethod
    def read(cls, path: str) -> object:
        return {"path": path}

    @classmethod
    def copy(cls, _torrent: object) -> _Torrent:
        return cls()

    def write(self, path: str, *, overwrite: bool = False) -> None:
        type(self).copied.append((path, overwrite))
        Path(path).write_bytes(b"torrent")


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Release File.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "manual",
        "path": str(media),
        "name": "Release Name",
        "title": "Release: Title",
        "overview": "Overview",
        "category": "MOVIE",
        "resolution": "1080p",
        "source": "WEB",
        "type": "WEBDL",
        "tag": "-GROUP",
        "tmdb": 123,
        "imdb_id": 456,
        "tvdb_id": 789,
        "tvmaze_id": 321,
        "artwork_url": "",
        "rehosted_artwork_url": None,
        "skip_imghost_upload": True,
        "image_list": [
            {
                "web_url": "https://images.invalid/page",
                "img_url": "https://images.invalid/thumb.jpg",
                "raw_url": "https://images.invalid/raw.jpg",
            }
        ],
        "is_disc": "",
    }
    state.update(values)
    target = tmp_path / "tmp" / "manual"
    target.mkdir(parents=True, exist_ok=True)
    return Meta(state)


def test_constructor_validates_configuration_shapes() -> None:
    with pytest.raises(ValueError, match="DEFAULT"):
        ManualPackageManager({"DEFAULT": "bad", "TRACKERS": {}})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="TRACKERS"):
        ManualPackageManager({"DEFAULT": {}, "TRACKERS": "bad"})  # type: ignore[dict-item]


def test_filebrowser_package_removes_extra_torrents_and_copies_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manual_package, "Torrent", _Torrent)
    _Torrent.copied = []
    state = tmp_path / "tmp" / "manual"
    state.mkdir(parents=True, exist_ok=True)
    for name in ("BASE.torrent", "[RAND]keep.torrent", "REMOVE.torrent"):
        (state / name).write_bytes(b"torrent")
    manager = ManualPackageManager(
        {
            "DEFAULT": {},
            "TRACKERS": {"MANUAL": {"filebrowser": "https://files.invalid/root/"}},
        }
    )
    meta = _meta(tmp_path)

    result = asyncio.run(manager.package(meta))

    assert result == "https://files.invalid/root/tmp/manual"
    assert not (state / "REMOVE.torrent").exists()
    assert (state / "[RAND]keep.torrent").exists()
    assert _Torrent.copied and _Torrent.copied[0][1] is True
    generic = (state / "GENERIC_INFO.txt").read_text(encoding="utf-8")
    assert "TMDB" in generic and "IMDb" in generic and "TVDB" in generic and "TVMaze" in generic
    assert "Image Webpage" in generic and "Thumbnail Image" in generic


def test_remote_upload_package_and_existing_rehosted_artwork(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manual_package.httpx, "AsyncClient", _Client)
    manager = ManualPackageManager({"DEFAULT": {}, "TRACKERS": {"MANUAL": {}}})
    meta = _meta(tmp_path, rehosted_artwork_url="https://images.invalid/existing.jpg")
    poster = tmp_path / "tmp" / "manual" / "artwork" / "POSTER.png"
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"poster")

    result = asyncio.run(manager.package(meta))

    assert result == "https://files.invalid/package.tar"
    generic = (tmp_path / "tmp" / "manual" / "GENERIC_INFO.txt").read_text(encoding="utf-8")
    assert "TMDB Cover: https://images.invalid/existing.jpg" in generic


def test_artwork_fetch_failure_is_logged_but_package_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response()
    response.status_code = 500
    _Client.get_response = response
    monkeypatch.setattr(manual_package.httpx, "AsyncClient", _Client)
    manager = ManualPackageManager({"DEFAULT": {}, "TRACKERS": {"MANUAL": {"filebrowser": "https://files.invalid"}}})
    meta = _meta(tmp_path, artwork_url="https://images.invalid/missing.jpg")

    assert asyncio.run(manager.package(meta)) == "https://files.invalid/tmp/manual"


def test_package_returns_false_on_archive_or_upload_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ManualPackageManager({"DEFAULT": {}, "TRACKERS": {"MANUAL": {}}})
    monkeypatch.setattr(manual_package.shutil, "make_archive", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("archive failed")))
    assert asyncio.run(manager.package(_meta(tmp_path))) is False


def test_artwork_fetch_success_rehosts_and_persists_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response()
    response.status_code = 200
    response.content = b"poster"
    _Client.get_response = response
    monkeypatch.setattr(manual_package.httpx, "AsyncClient", _Client)
    manager = ManualPackageManager({"DEFAULT": {}, "TRACKERS": {"MANUAL": {"filebrowser": "https://files.invalid"}}})

    class Uploader:
        async def upload_screens(self, *_args: object, **_kwargs: object):
            return ([{"raw_url": "https://images.invalid/rehosted.jpg", "img_url": "https://images.invalid/thumb.jpg"}], 1)

    manager.uploadscreens_manager = Uploader()  # type: ignore[assignment]
    meta = _meta(
        tmp_path,
        artwork_url="https://images.invalid/source.jpg",
        rehosted_artwork_url=None,
        skip_imghost_upload=False,
    )

    assert asyncio.run(manager.package(meta)) == "https://files.invalid/tmp/manual"
    assert meta.rehosted_artwork_url == "https://images.invalid/rehosted.jpg"
    assert (tmp_path / "tmp" / "manual" / "artwork" / "POSTER.png").read_bytes() == b"poster"
    assert (tmp_path / "tmp" / "manual" / "meta.json").is_file()
