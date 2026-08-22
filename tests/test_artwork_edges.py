from __future__ import annotations

import asyncio
import io
import socket
from pathlib import Path
from types import TracebackType
from typing import ClassVar, Self
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.media import artwork


def _png_bytes(mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (8, 12), 1).save(buffer, format="PNG")
    return buffer.getvalue()


def test_public_url_validation_all_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not artwork.is_public_http_url(None)
    assert not artwork.is_public_http_url("file:///tmp/a.png")
    assert not artwork.is_public_http_url("https:///missing-host")

    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )
    assert artwork.is_public_http_url("https://example.com/a.png")

    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ],
    )
    assert not artwork.is_public_http_url("https://example.com/a.png")

    monkeypatch.setattr(
        artwork.socket, "getaddrinfo", lambda *_args, **_kwargs: []
    )
    assert not artwork.is_public_http_url("https://example.com/a.png")
    monkeypatch.setattr(
        artwork.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("dns")),
    )
    assert not artwork.is_public_http_url("https://example.com/a.png")


def test_image_bytes_and_cover_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not artwork.is_valid_image_bytes(b"")
    assert not artwork.is_valid_image_bytes(b"not-image")
    assert artwork.is_valid_image_bytes(_png_bytes())

    class FakeImage:
        format = "BMP"
        width = 8
        height = 8

        def __enter__(self) -> FakeImage:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def verify(self) -> None:
            return None

    monkeypatch.setattr(
        artwork.Image, "open", lambda *_args, **_kwargs: FakeImage()
    )
    assert not artwork.is_valid_image_bytes(b"x")

    class EmptyImage(FakeImage):
        format = "PNG"
        width = 0

    monkeypatch.setattr(
        artwork.Image, "open", lambda *_args, **_kwargs: EmptyImage()
    )
    assert not artwork.is_valid_image_bytes(b"x")

    file = tmp_path / "cover.png"
    file.write_bytes(_png_bytes())
    original_stat = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stat")),
    )
    assert not artwork.is_valid_cover_image(file)
    monkeypatch.setattr(Path, "stat", original_stat)
    assert not artwork.is_valid_cover_image(tmp_path / "missing.png")


def test_local_artwork_discovery_priorities_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert artwork._find_local_artwork_sources("") == {}
    assert (
        artwork._find_local_artwork_sources(
            str(tmp_path / "missing" / "movie.mkv")
        )
        == {}
    )
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"video")
    assert not artwork._valid_local_artwork_candidate(tmp_path / "missing.png")
    assert artwork._candidate_artwork_match(Path("random.png")) is None
    for name, color in (
        ("z-artwork.jpg", "red"),
        ("cover.jpg", "blue"),
        ("front-poster.png", "green"),
        ("header.png", "yellow"),
        ("z-banner.webp", "white"),
        ("ignored.bmp", "black"),
    ):
        Image.new("RGB", (8, 12), color).save(tmp_path / name)
    sources = artwork._find_local_artwork_sources(str(media))
    assert sources["poster"].name == "front-poster.png"
    assert sources["banner"].name == "z-banner.webp"

    original_iterdir = Path.iterdir
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("list")),
    )
    with pytest.raises(OSError):
        artwork._find_local_artwork_sources(str(media))
    monkeypatch.setattr(Path, "iterdir", original_iterdir)


class _StreamResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        redirect: bool = False,
        url: str = "https://example.com/a.png",
    ) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks or []
        self.is_redirect = redirect
        self.url = httpx.URL(url)

    async def __aenter__(self) -> _StreamResponse:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _StreamClient:
    queue: ClassVar[list[object]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def stream(self, *_args: object, **_kwargs: object):
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def test_download_public_image_success_redirects_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(artwork, "is_public_http_url", lambda _url: True)
    monkeypatch.setattr(artwork.httpx, "AsyncClient", _StreamClient)
    valid = _png_bytes()

    _StreamClient.queue = [
        _StreamResponse(
            chunks=[valid], headers={"Content-Length": str(len(valid))}
        )
    ]
    assert (
        asyncio.run(
            artwork._download_public_image("https://example.com/a.png")
        )
        == valid
    )

    _StreamClient.queue = [
        _StreamResponse(redirect=True, headers={"Location": "/next"}),
        _StreamResponse(chunks=[valid]),
    ]
    assert (
        asyncio.run(
            artwork._download_public_image("https://example.com/a.png")
        )
        == valid
    )

    for response in (
        _StreamResponse(redirect=True),
        _StreamResponse(status=404),
        _StreamResponse(headers={"Content-Length": "bad"}),
        _StreamResponse(
            headers={"Content-Length": str(artwork.MAX_ARTWORK_BYTES + 1)}
        ),
        _StreamResponse(chunks=[b"bad"]),
        _StreamResponse(chunks=[b"x" * (artwork.MAX_ARTWORK_BYTES + 1)]),
    ):
        _StreamClient.queue = [response]
        assert (
            asyncio.run(
                artwork._download_public_image("https://example.com/a.png")
            )
            is None
        )

    monkeypatch.setattr(artwork, "is_public_http_url", lambda _url: False)
    assert (
        asyncio.run(
            artwork._download_public_image("https://example.com/a.png")
        )
        is None
    )

    monkeypatch.setattr(artwork, "is_public_http_url", lambda _url: True)
    _StreamClient.queue = [
        httpx.RequestError(
            "offline", request=httpx.Request("GET", "https://example.com")
        )
    ]
    assert (
        asyncio.run(
            artwork._download_public_image("https://example.com/a.png")
        )
        is None
    )

    _StreamClient.queue = [
        _StreamResponse(redirect=True, headers={"Location": "/next"})
        for _ in range(4)
    ]
    assert (
        asyncio.run(
            artwork._download_public_image("https://example.com/a.png")
        )
        is None
    )


def test_write_png_bytes_modes_limits_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "out.png"
    assert not artwork._write_png(b"", destination)
    assert not artwork._write_png(
        b"x" * (artwork.MAX_ARTWORK_BYTES + 1), destination
    )

    source = tmp_path / "palette.gif"
    image = Image.new("P", (8, 12))
    image.info["transparency"] = 0
    image.save(source, format="GIF")
    assert artwork._write_png(source, destination)
    assert Image.open(destination).mode == "RGBA"

    destination.unlink()
    source.write_bytes(b"bad")
    assert not artwork._write_png(source, destination)

    original_replace = Path.replace
    monkeypatch.setattr(
        Path,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace")),
    )
    assert not artwork._write_png(_png_bytes(), destination)
    monkeypatch.setattr(Path, "replace", original_replace)
    assert not destination.with_suffix(".tmp").exists()


def test_prepare_artwork_provider_url_invalid_explicit_and_failed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid = _png_bytes()
    download = AsyncMock(return_value=valid)
    monkeypatch.setattr(artwork, "_download_public_image", download)
    monkeypatch.setattr(
        artwork, "is_public_http_url", lambda value: bool(value)
    )
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="provider",
        artwork_url="https://provider.example/poster.png",
    )
    asyncio.run(artwork.prepare_artwork(meta))
    assert Path(meta.artwork_path).is_file()
    download.assert_awaited_once()

    invalid = Meta(
        base_dir=str(tmp_path), uuid="invalid", explicit_banner="not-a-path"
    )
    asyncio.run(artwork.prepare_artwork(invalid))
    assert not invalid.artwork_banner_path

    source = tmp_path / "source.png"
    source.write_bytes(valid)
    monkeypatch.setattr(artwork, "_write_png", lambda *_args, **_kwargs: False)
    failed = Meta(
        base_dir=str(tmp_path), uuid="failed", explicit_poster=str(source)
    )
    asyncio.run(artwork.prepare_artwork(failed))
    assert not failed.artwork_path


def test_local_discovery_skips_invalid_cover_and_write_png_rejects_unsupported_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"video")
    (tmp_path / "cover.jpg").write_bytes(b"not-an-image")
    assert artwork._find_local_artwork_sources(str(media)) == {}

    class UnsupportedImage:
        format = "BMP"
        width = 8
        height = 8
        mode = "RGB"
        info: ClassVar[dict[str, object]] = {}

        def __enter__(self) -> UnsupportedImage:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def load(self) -> None:
            return None

    monkeypatch.setattr(
        artwork.Image, "open", lambda *_args, **_kwargs: UnsupportedImage()
    )
    assert not artwork._write_png(b"image", tmp_path / "output.png")
