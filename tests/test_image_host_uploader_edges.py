from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts import uploader
from src.integrations.image_hosts.contracts import IMAGE_HOST_SPECS


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        payload: object = None,
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = (
            text
            if text is not None
            else (
                str(payload)
                if isinstance(payload, BaseException)
                else json.dumps(payload)
            )
        )

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


class _Client:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[tuple[tuple[object, ...], dict[str, object]]]] = []

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

    async def post(self, *args: object, **kwargs: object) -> _Response:
        type(self).calls.append((args, kwargs))
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, _Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


def _request_error(message: str = "network") -> httpx.RequestError:
    return httpx.RequestError(
        message, request=httpx.Request("POST", "https://images.invalid/upload")
    )


def _image(tmp_path: Path, name: str = "screen.png", size: int = 5) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _config(**values: object) -> dict[str, Any]:
    return {
        "DEFAULT": {
            "img_host_1": "imgbb",
            "img_host_2": "imgbox",
            "imgbb_api": "imgbb-key",
            "dalexni_api": "dalexni-key",
            "ptscreens_api": "ptscreens-key",
            "utppm_api": "utppm-key",
            "onlyimage_api": "onlyimage-key",
            "lensdump_api": "lensdump-key",
            "passtheima_ge_api": "passtheimage-key",
            "zipline_url": "https://zipline.invalid/api/upload",
            "zipline_api_key": "zipline-key",
            "midnightscene_api_key": "midnight-key",
            "seedpool_cdn_api": "seedpool-key",
            "sharex_url": "https://sharex.invalid/api/upload",
            "sharex_api_key": "sharex-key",
            "lostimg_api": "lostimg-key",
            **values,
        }
    }


@pytest.fixture(autouse=True)
def _http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _Client.reset()
    monkeypatch.setattr(uploader.httpx, "AsyncClient", _Client)


def test_payload_error_and_chevereto_helpers_cover_all_shapes() -> None:
    assert (
        uploader._summarize_host_error(
            "<html><style>x{}</style><body>HTTP 503 something went wrong</body></html>"
        )
        == "HTTP 503: remote service error"
    )
    assert (
        uploader._summarize_host_error("<script>bad</script><p>Hello</p>", 4)
        == "Hell"
    )

    response = _Response(500, {}, "fallback")
    assert (
        uploader._image_host_error({"error": {"message": "nested"}}, response)
        == "nested"
    )
    assert (
        uploader._image_host_error(
            {"error": {"error": "nested-error"}}, response
        )
        == "nested-error"
    )
    assert (
        uploader._image_host_error({"error": "scalar"}, response) == "scalar"
    )
    assert (
        uploader._image_host_error({"message": "message"}, response)
        == "message"
    )
    assert (
        uploader._image_host_error({"status_txt": "status"}, response)
        == "status"
    )
    assert uploader._image_host_error({}, response) == "fallback"

    with pytest.raises(ValueError, match="non-object"):
        uploader._json_mapping(_Response(payload=[]))  # type: ignore[arg-type]

    assert uploader._chevereto_urls({}) is None
    assert uploader._chevereto_urls(
        {
            "image": {
                "url": "raw",
                "url_viewer": "web",
                "medium": {"url": "medium"},
            }
        }
    ) == (
        "medium",
        "raw",
        "web",
    )
    assert uploader._chevereto_urls(
        {"image": {"url": "raw", "thumb": {"url": "thumb"}}}
    ) == ("thumb", "raw", "raw")
    assert uploader._chevereto_urls({"image": {"url": ""}}) is None


def test_chevereto_missing_configuration_invalid_json_status_and_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    assert (
        asyncio.run(
            uploader._upload_chevereto(
                str(image),
                host_key="ptscreens",
                api_key=None,
                request_timeout=1,
                nsfw=False,
            )
        )["reason"]
        == "Missing ptscreens API key"
    )

    original = IMAGE_HOST_SPECS["ptscreens"]
    monkeypatch.setitem(
        IMAGE_HOST_SPECS, "ptscreens", replace(original, upload_url=None)
    )
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=False,
        )
    )
    assert result["reason"] == "No upload URL configured for ptscreens"
    monkeypatch.setitem(IMAGE_HOST_SPECS, "ptscreens", original)

    _Client.reset(
        _Response(
            500,
            ValueError("bad json"),
            "<!doctype html><html>HTTP 500 something went wrong</html>",
        )
    )
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=False,
        )
    )
    assert result["host_unavailable"] and "invalid JSON" in result["reason"]

    _Client.reset(
        _Response(429, {"status_code": 429, "error": {"message": "limited"}})
    )
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=True,
        )
    )
    assert result["retryable"] and "limited" in result["reason"]
    assert _Client.calls[0][1]["data"] == {"nsfw": "1"}

    _Client.reset(_Response(200, {"status_code": 200, "image": {"url": ""}}))
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=False,
        )
    )
    assert "incomplete image" in result["reason"]


def test_chevereto_timeout_request_and_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    _Client.reset(
        httpx.ReadTimeout(
            "timeout", request=httpx.Request("POST", "https://images.invalid")
        )
    )
    assert (
        "outcome unknown"
        in asyncio.run(
            uploader._upload_chevereto(
                str(image),
                host_key="ptscreens",
                api_key="key",
                request_timeout=1,
                nsfw=False,
            )
        )["reason"]
    )

    _Client.reset(_request_error())
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=False,
        )
    )
    assert result["host_unavailable"] and "request failed" in result["reason"]

    monkeypatch.setattr(
        uploader,
        "_read_image_bytes",
        lambda _path: (_ for _ in ()).throw(OSError("read failed")),
    )
    result = asyncio.run(
        uploader._upload_chevereto(
            str(image),
            host_key="ptscreens",
            api_key="key",
            request_timeout=1,
            nsfw=False,
        )
    )
    assert "Could not read image" in result["reason"]


def test_imgbb_success_url_fallbacks_and_all_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    assert (
        asyncio.run(
            uploader._upload_imgbb(str(image), None, request_timeout=1)
        )["retryable"]
        is False
    )

    _Client.reset(
        _Response(
            200,
            {
                "success": True,
                "data": {
                    "image": {"url": "raw"},
                    "medium": {"url": "medium"},
                    "thumb": {"url": "thumb"},
                    "display_url": "display",
                    "url_viewer": "viewer",
                },
            },
        )
    )
    result = asyncio.run(
        uploader._upload_imgbb(str(image), "key", request_timeout=1)
    )
    assert (
        result["img_url"] == "medium"
        and result["raw_url"] == "raw"
        and result["web_url"] == "viewer"
    )

    _Client.reset(
        _Response(
            200,
            {
                "success": True,
                "data": {"url": "raw", "display_url": "display"},
            },
        )
    )
    result = asyncio.run(
        uploader._upload_imgbb(str(image), "key", request_timeout=1)
    )
    assert result["img_url"] == "display" and result["web_url"] == "raw"

    _Client.reset(_Response(500, ValueError("bad"), "HTML"))
    result = asyncio.run(
        uploader._upload_imgbb(str(image), "key", request_timeout=1)
    )
    assert result["host_unavailable"] and "invalid JSON" in result["reason"]

    for status, payload in (
        (403, {"success": False, "error": {"message": "invalid API key"}}),
        (500, {"success": False, "error": "offline"}),
        (400, {"success": False, "error": "payload"}),
    ):
        _Client.reset(_Response(status, payload))
        result = asyncio.run(
            uploader._upload_imgbb(str(image), "key", request_timeout=1)
        )
        assert result["status"] == "failed"
        if status in {403, 500}:
            assert result["host_unavailable"]

    _Client.reset(_Response(200, {"success": True, "data": []}))
    assert (
        "incomplete image response"
        in asyncio.run(
            uploader._upload_imgbb(str(image), "key", request_timeout=1)
        )["reason"]
    )
    _Client.reset(
        _Response(200, {"success": True, "data": {"image": {"url": ""}}})
    )
    assert (
        "incomplete image URLs"
        in asyncio.run(
            uploader._upload_imgbb(str(image), "key", request_timeout=1)
        )["reason"]
    )

    _Client.reset(
        httpx.ReadTimeout(
            "timeout", request=httpx.Request("POST", "https://images.invalid")
        )
    )
    assert (
        "outcome unknown"
        in asyncio.run(
            uploader._upload_imgbb(str(image), "key", request_timeout=1)
        )["reason"]
    )
    _Client.reset(_request_error())
    assert asyncio.run(
        uploader._upload_imgbb(str(image), "key", request_timeout=1)
    )["host_unavailable"]
    monkeypatch.setattr(
        uploader,
        "_read_image_bytes",
        lambda _path: (_ for _ in ()).throw(OSError("read failed")),
    )
    assert (
        "Could not read image"
        in asyncio.run(
            uploader._upload_imgbb(str(image), "key", request_timeout=1)
        )["reason"]
    )


def test_pixhost_url_patterns_and_upload_task_missing_hosts(
    tmp_path: Path,
) -> None:
    image = _image(tmp_path)
    assert (
        uploader._pixhost_raw_url("https://t12.pixhost.to/thumbs/1/a.png")
        == "https://img12.pixhost.to/images/1/a.png"
    )
    assert (
        uploader._pixhost_raw_url("https://t3.pixho.st/thumbs/1/a.png")
        == "https://img3.pixho.st/images/1/a.png"
    )

    meta = Meta(category="MOVIE")
    for host, config_key in (
        ("dalexni", "dalexni_api"),
        ("zipline", "zipline_api_key"),
        ("midnightscene", "midnightscene_api_key"),
        ("seedpool_cdn", "seedpool_cdn_api"),
        ("sharex", "sharex_api_key"),
        ("lostimg", "lostimg_api"),
    ):
        config = _config(**{config_key: ""})
        result = asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )
        assert result["status"] == "failed" and "Missing" in result["reason"]
    result = asyncio.run(
        uploader.upload_image_task(
            [str(image), "unsupported", _config(), meta]
        )
    )
    assert "Unsupported image host" in result["reason"]


def test_dalexni_success_and_response_failures(tmp_path: Path) -> None:
    image = _image(tmp_path)
    meta = Meta(category="MOVIE")
    config = _config()

    _Client.reset(
        _Response(
            200,
            {
                "success": True,
                "data": {
                    "image": {"url": "raw"},
                    "medium": {"url": "medium"},
                    "url_viewer": "web",
                },
            },
        )
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "dalexni", config, meta])
    )
    assert result["raw_url"] == "raw" and result["img_url"] == "medium"

    _Client.reset(
        _Response(
            502,
            ValueError("bad"),
            "<!doctype html>HTTP 502 something went wrong",
        )
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "dalexni", config, meta])
    )
    assert result["host_unavailable"] and "non-JSON" in result["reason"]

    _Client.reset(_Response(500, {"success": False, "message": "offline"}))
    result = asyncio.run(
        uploader.upload_image_task([str(image), "dalexni", config, meta])
    )
    assert result["retryable"] and result["host_unavailable"]

    _Client.reset(_Response(200, {"success": True, "data": []}))
    assert (
        "incomplete response"
        in asyncio.run(
            uploader.upload_image_task([str(image), "dalexni", config, meta])
        )["reason"]
    )
    _Client.reset(
        _Response(200, {"success": True, "data": {"image": {"url": ""}}})
    )
    assert (
        "incomplete image URLs"
        in asyncio.run(
            uploader.upload_image_task([str(image), "dalexni", config, meta])
        )["reason"]
    )


def test_pixhost_success_size_invalid_json_status_and_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    meta = Meta(category="XXX")
    config = _config()

    original = IMAGE_HOST_SPECS["pixhost"]
    monkeypatch.setitem(
        IMAGE_HOST_SPECS, "pixhost", replace(original, max_file_bytes=1)
    )
    assert (
        "maximum image size"
        in asyncio.run(
            uploader.upload_image_task([str(image), "pixhost", config, meta])
        )["reason"]
    )
    monkeypatch.setitem(IMAGE_HOST_SPECS, "pixhost", original)

    _Client.reset(
        _Response(
            200,
            {
                "th_url": "https://t1.pixhost.to/thumbs/a.png",
                "show_url": "https://pixhost.to/show/a",
            },
        )
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "pixhost", config, meta])
    )
    assert result["raw_url"] == "https://img1.pixhost.to/images/a.png"
    assert _Client.calls[0][1]["data"]["content_type"] == "1"

    _Client.reset(_Response(500, {}, "offline"))
    result = asyncio.run(
        uploader.upload_image_task([str(image), "pixhost", config, meta])
    )
    assert result["host_unavailable"] and result["retryable"]
    _Client.reset(_Response(200, {"bad": True}))
    assert (
        "incomplete API"
        in asyncio.run(
            uploader.upload_image_task([str(image), "pixhost", config, meta])
        )["reason"]
    )
    _Client.reset(_Response(200, ValueError("bad")))
    assert (
        "invalid JSON"
        in asyncio.run(
            uploader.upload_image_task([str(image), "pixhost", config, meta])
        )["reason"]
    )
    _Client.reset(
        httpx.ReadTimeout(
            "timeout", request=httpx.Request("POST", "https://images.invalid")
        )
    )
    assert (
        "outcome unknown"
        in asyncio.run(
            uploader.upload_image_task([str(image), "pixhost", config, meta])
        )["reason"]
    )
    _Client.reset(_request_error())
    assert asyncio.run(
        uploader.upload_image_task([str(image), "pixhost", config, meta])
    )["host_unavailable"]


def test_zipline_midnightscene_success_shapes_and_failures(
    tmp_path: Path,
) -> None:
    image = _image(tmp_path)
    meta = Meta(category="MOVIE")
    config = _config()

    for host in ("zipline", "midnightscene"):
        for file_value in (
            {"url": "https://files.invalid/a.png"},
            "https://files.invalid/a.png",
        ):
            _Client.reset(_Response(201, {"files": [file_value]}))
            result = asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )
            assert result["raw_url"] == "https://files.invalid/a.png"
        for payload in ({"files": []}, {"files": [{}]}, []):
            _Client.reset(_Response(200, payload))
            result = asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )
            assert result["status"] == "failed"
        _Client.reset(_Response(500, {"message": "offline"}))
        assert asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )["host_unavailable"]
        _Client.reset(_Response(400, ValueError("bad"), "invalid"))
        assert (
            "upload failed"
            in asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )["reason"]
        )
        _Client.reset(
            httpx.ReadTimeout(
                "timeout",
                request=httpx.Request("POST", "https://images.invalid"),
            )
        )
        assert (
            "outcome unknown"
            in asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )["reason"]
        )
        _Client.reset(_request_error())
        assert asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )["host_unavailable"]


def test_seedpool_sharex_lostimg_success_and_failures(tmp_path: Path) -> None:
    image = _image(tmp_path)
    meta = Meta(category="MOVIE")
    config = _config()

    _Client.reset(
        _Response(
            201,
            {
                "files": [
                    {
                        "url": "raw",
                        "thumbnail_url": "thumb",
                        "variants": {"medium": "medium"},
                    }
                ]
            },
        )
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "seedpool_cdn", config, meta])
    )
    assert result["img_url"] == "thumb" and result["raw_url"] == "raw"
    _Client.reset(
        _Response(
            200,
            {
                "files": [
                    {"url": "raw", "variants": {"thumb": "variant-thumb"}}
                ]
            },
        )
    )
    assert (
        asyncio.run(
            uploader.upload_image_task(
                [str(image), "seedpool_cdn", config, meta]
            )
        )["img_url"]
        == "variant-thumb"
    )
    for payload in (
        {"files": []},
        {"files": ["bad"]},
        {"files": [{"url": ""}]},
    ):
        _Client.reset(_Response(200, payload))
        assert (
            asyncio.run(
                uploader.upload_image_task(
                    [str(image), "seedpool_cdn", config, meta]
                )
            )["status"]
            == "failed"
        )
    _Client.reset(_Response(500, {"message": "offline"}))
    assert asyncio.run(
        uploader.upload_image_task([str(image), "seedpool_cdn", config, meta])
    )["host_unavailable"]

    for payload, expected in (
        ({"data": {"link": "share-link"}}, "share-link"),
        ({"link": "root-link"}, "root-link"),
    ):
        _Client.reset(_Response(200, payload))
        result = asyncio.run(
            uploader.upload_image_task([str(image), "sharex", config, meta])
        )
        assert result["raw_url"] == expected
    _Client.reset(_Response(200, {}))
    assert (
        "missing link"
        in asyncio.run(
            uploader.upload_image_task([str(image), "sharex", config, meta])
        )["reason"]
    )
    _Client.reset(_Response(500, {"message": "offline"}))
    assert asyncio.run(
        uploader.upload_image_task([str(image), "sharex", config, meta])
    )["host_unavailable"]

    _Client.reset(_Response(200, {"url": "lost-url"}))
    assert (
        asyncio.run(
            uploader.upload_image_task([str(image), "lostimg", config, meta])
        )["raw_url"]
        == "lost-url"
    )
    _Client.reset(_Response(200, {}))
    assert (
        "missing url"
        in asyncio.run(
            uploader.upload_image_task([str(image), "lostimg", config, meta])
        )["reason"]
    )
    _Client.reset(_Response(500, {"message": "offline"}))
    assert asyncio.run(
        uploader.upload_image_task([str(image), "lostimg", config, meta])
    )["host_unavailable"]


def test_upload_image_task_imgbox_chevereto_routing_and_outer_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    config = _config()
    meta = Meta(category="XXX")

    monkeypatch.setattr(
        uploader,
        "imgbox_upload",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[{"img_url": "thumb", "raw_url": "raw", "web_url": "web"}],
        ),
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "imgbox", config, meta])
    )
    assert result["status"] == "success"

    async def imgbox_failure(
        *_args: object, **kwargs: object
    ) -> list[dict[str, str]]:
        kwargs["return_dict"].update(
            error="HTTP 500 something went wrong", host_unavailable=True
        )
        return []

    monkeypatch.setattr(uploader, "imgbox_upload", imgbox_failure)
    result = asyncio.run(
        uploader.upload_image_task([str(image), "imgbox", config, meta])
    )
    assert (
        result["host_unavailable"]
        and "remote service error" in result["reason"]
    )

    routed: list[tuple[str, bool]] = []

    async def chevereto(
        _image: str,
        *,
        host_key: str,
        api_key: str | None,
        request_timeout: float,
        nsfw: bool,
    ) -> dict[str, Any]:
        assert api_key and request_timeout == 60
        routed.append((host_key, nsfw))
        return {
            "status": "success",
            "img_url": "i",
            "raw_url": "r",
            "web_url": "w",
        }

    monkeypatch.setattr(uploader, "_upload_chevereto", chevereto)
    for host in (
        "ptscreens",
        "utppm",
        "onlyimage",
        "lensdump",
        "passtheimage",
    ):
        assert (
            asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )["status"]
            == "success"
        )
    assert routed == [
        (host, True)
        for host in (
            "ptscreens",
            "utppm",
            "onlyimage",
            "lensdump",
            "passtheimage",
        )
    ]

    monkeypatch.setattr(
        uploader,
        "_upload_imgbb",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = asyncio.run(
        uploader.upload_image_task([str(image), "imgbb", config, meta])
    )
    assert result["reason"] == "boom" and result["retryable"] is False


class _Gallery:
    submissions: ClassVar[dict[str, list[object]]] = {}
    enter_error: ClassVar[BaseException | None] = None
    kwargs: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).kwargs = kwargs

    async def __aenter__(self) -> Self:
        if type(self).enter_error:
            raise type(self).enter_error
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def add(self, images: list[str]) -> AsyncIterator[object]:
        async def iterator() -> AsyncIterator[object]:
            for value in type(self).submissions.get(images[0], []):
                if isinstance(value, BaseException):
                    raise value
                yield value

        return iterator()


def test_imgbox_upload_success_incomplete_failure_exception_and_gallery_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = _image(tmp_path, "one.png")
    two = _image(tmp_path, "two.png")
    three = _image(tmp_path, "three.png")
    four = _image(tmp_path, "four.png")
    _Gallery.enter_error = None
    _Gallery.submissions = {
        str(one): [
            {
                "success": True,
                "web_url": "web",
                "thumbnail_url": "thumb",
                "image_url": "raw",
            }
        ],
        str(two): [
            {
                "success": True,
                "web_url": "web",
                "thumbnail_url": None,
                "image_url": "raw",
            }
        ],
        str(three): [
            {"success": False, "error": "HTTP 500 something went wrong"}
        ],
        str(four): [RuntimeError("per-image failed")],
    }
    monkeypatch.setattr(uploader.pyimgbox, "Gallery", _Gallery)
    state: dict[str, Any] = {}
    result = asyncio.run(
        uploader.imgbox_upload(
            tmp_path,
            [str(one), str(two), str(three), str(four)],
            state,
            adult=True,
        )
    )
    assert result == [{"web_url": "web", "img_url": "thumb", "raw_url": "raw"}]
    assert (
        state["host_unavailable"] is True
        and "remote service error" in state["error"]
    )
    assert _Gallery.kwargs["adult"] is True

    _Gallery.enter_error = RuntimeError("gallery unavailable")
    state = {}
    assert (
        asyncio.run(uploader.imgbox_upload(tmp_path, [str(one)], state)) == []
    )
    assert (
        state["host_unavailable"] and state["error"] == "gallery unavailable"
    )


def test_upload_screens_no_plan_zero_existing_missin_4824c1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    image = _image(shot_dir, "Release-1.png")
    cwd = Path.cwd()
    try:
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="",
            debug=False,
        )
        config = {"DEFAULT": {}}
        assert asyncio.run(
            uploader._upload_screens(config, meta, 1, 1, 0, 1, [], {})
        ) == ([], 0)

        config = _config()
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            debug=False,
        )
        assert asyncio.run(
            uploader._upload_screens(config, meta, 0, 1, 0, 0, [], {})
        ) == ([], 0)

        existing = {"img_url": "i", "raw_url": "r", "web_url": "w"}
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[existing],
            imghost="imgbb",
            debug=False,
        )
        assert asyncio.run(
            uploader._upload_screens(config, meta, 1, 1, 0, 1, [], {})
        ) == ([existing], 1)

        image.unlink()
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            debug=False,
        )
        assert asyncio.run(
            uploader._upload_screens(config, meta, 1, 1, 0, 1, [], {})
        ) == ([], 0)
        image.write_bytes(b"image")

        async def success(args: list[object]) -> dict[str, Any]:
            return {
                "status": "success",
                "img_url": "thumb",
                "raw_url": "raw",
                "web_url": "web",
                "local_file_path": args[0],
            }

        monkeypatch.setattr(uploader, "upload_image_task", success)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            image_sizes={},
            imghost="imgbb",
            debug=True,
            cutoff=1,
        )
        result, count = asyncio.run(
            uploader._upload_screens(
                _config(
                    image_upload_concurrency="bad", image_upload_delay="bad"
                ),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
            )
        )
        assert count == 1 and result[0]["local_file_path"] == str(image)
    finally:
        os.chdir(cwd)


def test_private_host_transport_and_json_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = _image(tmp_path)
    meta = Meta(category="MOVIE")
    config = _config()

    for host in ("dalexni", "seedpool_cdn", "sharex", "lostimg"):
        _Client.reset(
            httpx.ReadTimeout(
                "timeout",
                request=httpx.Request("POST", "https://images.invalid"),
            )
        )
        assert (
            "outcome unknown"
            in asyncio.run(
                uploader.upload_image_task([str(image), host, config, meta])
            )["reason"]
        )
        _Client.reset(_request_error())
        assert asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )["host_unavailable"]

    for host in ("seedpool_cdn", "sharex", "lostimg"):
        _Client.reset(_Response(500, ValueError("bad json"), "invalid"))
        result = asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )
        assert "non-JSON" in result["reason"] and result["host_unavailable"]

    original_read = uploader._read_image_bytes

    async def fail_read(_path: str) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(uploader, "_read_image_bytes", fail_read)
    for host in ("dalexni", "pixhost", "seedpool_cdn", "sharex", "lostimg"):
        result = asyncio.run(
            uploader.upload_image_task([str(image), host, config, meta])
        )
        assert "Could not read image" in result["reason"]
    monkeypatch.setattr(uploader, "_read_image_bytes", original_read)


def test_zipline_invalid_success_json_is_distinguished(tmp_path: Path) -> None:
    image = _image(tmp_path)
    _Client.reset(_Response(200, ValueError("bad json"), "invalid"))
    result = asyncio.run(
        uploader.upload_image_task([str(image), "zipline", _config(), Meta()])
    )
    assert "invalid JSON" in result["reason"]


def test_upload_screens_manifest_menu_duplicate_stat_and_images_needed_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    one = _image(shot_dir, "Release-1.png")
    menu = _image(shot_dir, "Release-VIDEO_TS-2.png")
    cwd = Path.cwd()

    async def success(args: list[object]) -> dict[str, Any]:
        source = str(args[0])
        return {
            "status": "success",
            "img_url": f"thumb:{Path(source).name}",
            "raw_url": f"raw:{Path(source).name}",
            "web_url": f"web:{Path(source).name}",
            "local_file_path": source,
        }

    monkeypatch.setattr(uploader, "upload_image_task", success)
    monkeypatch.setattr(uploader, "manifest_files", lambda *_args: [one, menu])
    try:
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            image_sizes={},
            imghost="imgbb",
            cutoff=1,
            menu_images=[{"local_file_path": str(menu)}],
        )
        images, count = asyncio.run(
            uploader._upload_screens(_config(), meta, 1, 1, 0, 1, [], {})
        )
        assert count == 1 and images[0]["raw_url"].endswith("Release-1.png")
        assert meta.image_sizes[images[0]["raw_url"]] == one.stat().st_size

        duplicate = {
            "img_url": "old",
            "raw_url": "raw:Release-1.png",
            "web_url": "old",
        }
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[duplicate],
            image_sizes={},
            imghost="imgbb",
            cutoff=2,
        )
        images, count = asyncio.run(
            uploader._upload_screens(_config(), meta, 2, 1, 0, 2, [one], {})
        )
        assert count == 1 and len(images) == 1

        config = _config(img_host_1="imgbox", img_host_2="imgbb")
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[duplicate],
            imghost="imgbb",
            cutoff=1,
        )
        images, count = asyncio.run(
            uploader._upload_screens(config, meta, 1, 1, 0, 1, [], {})
        )
        assert images == [duplicate] and count == 1
    finally:
        os.chdir(cwd)


def test_upload_screens_api_key_retry_exception_gather_and_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    image = _image(shot_dir, "Release-1.png")
    cwd = Path.cwd()

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(uploader.asyncio, "sleep", no_sleep)
    try:
        calls = 0

        async def api_key_then_unused(_args: list[object]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"status": "failed", "reason": "API key invalid"}

        monkeypatch.setattr(uploader, "upload_image_task", api_key_then_unused)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        assert asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
                max_retries=2,
            )
        ) == ([], 0)
        assert calls == 1

        calls = 0

        async def retry_then_success(args: list[object]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "failed",
                    "reason": "temporary payload failure",
                }
            return {
                "status": "success",
                "img_url": "i",
                "raw_url": "r",
                "web_url": "w",
                "local_file_path": args[0],
            }

        monkeypatch.setattr(uploader, "upload_image_task", retry_then_success)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        images, count = asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
                max_retries=2,
            )
        )
        assert count == 1 and images[0]["raw_url"] == "r" and calls == 2

        calls = 0

        async def raise_then_success(args: list[object]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient exception")
            return {
                "status": "success",
                "img_url": "i",
                "raw_url": "r2",
                "web_url": "w",
                "local_file_path": args[0],
            }

        monkeypatch.setattr(uploader, "upload_image_task", raise_then_success)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        images, count = asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
                max_retries=1,
            )
        )
        assert count == 1 and images[0]["raw_url"] == "r2"

        real_gather = uploader.asyncio.gather

        async def fail_gather(*aws: object, **_kwargs: object) -> list[object]:
            for awaitable in aws:
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()
            raise RuntimeError("gather failed")

        monkeypatch.setattr(uploader.asyncio, "gather", fail_gather)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        assert asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""), meta, 1, 1, 0, 1, [image], {}
            )
        ) == ([], 0)
        monkeypatch.setattr(uploader.asyncio, "gather", real_gather)
    finally:
        os.chdir(cwd)


def test_upload_screens_wait_timeout_and_cancellation_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    image = _image(shot_dir, "Release-1.png")
    cwd = Path.cwd()

    async def pending(_args: list[object]) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"status": "success"}

    monkeypatch.setattr(uploader, "upload_image_task", pending)
    real_wait_for = uploader.asyncio.wait_for

    async def timeout_wait(awaitable: Any, **_kwargs: object) -> Any:
        if isinstance(awaitable, asyncio.Task):
            awaitable.cancel()
            with pytest.raises(asyncio.CancelledError):
                await awaitable
        raise TimeoutError

    monkeypatch.setattr(uploader.asyncio, "wait_for", timeout_wait)
    try:
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        assert asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""), meta, 1, 1, 0, 1, [image], {}
            )
        ) == ([], 0)
    finally:
        monkeypatch.setattr(uploader.asyncio, "wait_for", real_wait_for)
        os.chdir(cwd)

    started = asyncio.Event()

    async def cancellable(_args: list[object]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {"status": "success"}

    monkeypatch.setattr(uploader, "upload_image_task", cancellable)

    async def exercise() -> None:
        task = asyncio.create_task(
            uploader._upload_screens(
                _config(img_host_2=""),
                Meta(
                    base_dir=str(root),
                    uuid="release",
                    image_list=[],
                    imghost="imgbb",
                    cutoff=1,
                ),
                1,
                1,
                0,
                1,
                [image],
                {},
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(exercise())
    finally:
        os.chdir(cwd)


def test_upload_screens_duplicate_raw_and_negative_retry_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    image = _image(shot_dir, "Release-1.png")
    cwd = Path.cwd()

    async def same_raw(args: list[object]) -> dict[str, Any]:
        return {
            "status": "success",
            "img_url": "new-thumb",
            "raw_url": "same-raw",
            "web_url": "new-web",
            "local_file_path": args[0],
        }

    monkeypatch.setattr(uploader, "upload_image_task", same_raw)
    try:
        existing_raw_only = {
            "raw_url": "same-raw",
            "img_url": "",
            "web_url": "",
        }
        monkeypatch.setattr(uploader, "manifest_files", lambda *_args: [image])
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[existing_raw_only],
            image_sizes={},
            imghost="imgbb",
            cutoff=1,
        )
        images, count = asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""), meta, 1, 1, 0, 1, [], {}, max_retries=0
            )
        )
        assert images == [existing_raw_only] and count == 1

        calls = 0

        async def should_not_run(_args: list[object]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"status": "success"}

        monkeypatch.setattr(uploader, "upload_image_task", should_not_run)
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        assert asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
                max_retries=-1,
            )
        ) == ([], 0)
        assert calls == 0
    finally:
        os.chdir(cwd)


def test_upload_screens_exception_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    shot_dir = root / "tmp" / "release" / "screenshots"
    shot_dir.mkdir(parents=True)
    image = _image(shot_dir, "Release-1.png")
    cwd = Path.cwd()

    async def always_fail(_args: list[object]) -> dict[str, Any]:
        raise RuntimeError("always fails")

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(uploader, "upload_image_task", always_fail)
    monkeypatch.setattr(uploader.asyncio, "sleep", no_sleep)
    try:
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            imghost="imgbb",
            cutoff=1,
        )
        assert asyncio.run(
            uploader._upload_screens(
                _config(img_host_2=""),
                meta,
                1,
                1,
                0,
                1,
                [image],
                {},
                max_retries=0,
            )
        ) == ([], 0)
    finally:
        os.chdir(cwd)


def test_upload_manager_scopes_unavailable_hosts_by_image_purpose() -> None:
    manager = uploader.UploadScreensManager(_config())
    manager.unavailable_hosts_for("spectrograms").update({"imgbb", "imgbox"})

    assert manager.unavailable_hosts_for("spectrograms") == {"imgbb", "imgbox"}
    assert manager.unavailable_hosts_for("screenshots") == set()
    assert manager.unavailable_hosts == set()


def test_auxiliary_host_failures_do_not_poison_screenshot_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    screenshots = root / "tmp" / "release" / "screenshots"
    spectrograms = root / "tmp" / "release" / "spectrograms"
    screenshots.mkdir(parents=True)
    spectrograms.mkdir(parents=True)
    screenshot = _image(screenshots, "Release-1.png")
    spectrogram = _image(spectrograms, "spec-1.png")
    config = _config(img_host_2="imgbox", img_host_3="onlyimage")
    manager = uploader.UploadScreensManager(config)
    seen: list[tuple[str, str]] = []
    phase = "spectrogram"

    async def fake_upload(args: list[object]) -> dict[str, Any]:
        nonlocal phase
        source = str(args[0])
        host = str(args[1])
        seen.append((phase, host))
        if phase == "spectrogram":
            return {
                "status": "failed",
                "reason": f"{host} unavailable",
                "host_unavailable": True,
            }
        if host != "onlyimage":
            return {
                "status": "failed",
                "reason": f"{host} unavailable",
                "host_unavailable": True,
            }
        return {
            "status": "success",
            "img_url": "https://onlyimage.org/thumb.png",
            "raw_url": "https://onlyimage.org/raw.png",
            "web_url": "https://onlyimage.org/view/1",
            "local_file_path": source,
        }

    monkeypatch.setattr(uploader, "upload_image_task", fake_upload)
    cwd = Path.cwd()
    try:
        meta = Meta(
            base_dir=str(root),
            uuid="release",
            image_list=[],
            image_sizes={},
            imghost="imgbb",
            cutoff=1,
        )
        auxiliary, _ = asyncio.run(
            manager.upload_screens(
                meta,
                1,
                1,
                0,
                1,
                [spectrogram],
                {},
            )
        )
        assert auxiliary == []
        assert manager.unavailable_hosts_for("spectrograms") == {
            "imgbb",
            "imgbox",
            "onlyimage",
        }
        assert manager.unavailable_hosts_for("screenshots") == set()

        phase = "screenshot"
        meta.imghost = "imgbb"
        images, count = asyncio.run(
            manager.upload_screens(meta, 1, 1, 0, 1, [], {})
        )
        assert count == 1
        assert images[0]["raw_url"] == "https://onlyimage.org/raw.png"
        assert ("screenshot", "onlyimage") in seen
        assert [host for label, host in seen if label == "screenshot"] == [
            "imgbb",
            "imgbox",
            "onlyimage",
        ]
        assert screenshot.exists()
    finally:
        os.chdir(cwd)


def test_exhausted_host_plan_logs_all_exclusion_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meta = Meta(imghost="onlyimage")
    config = _config(img_host_3="onlyimage")
    default = config["DEFAULT"]

    uploader._log_exhausted_host_plan(
        meta,
        default,
        ["imgbb", "imgbox", "onlyimage"],
        {"imgbb"},
        {"imgbox", "onlyimage"},
    )

    message = caplog.text
    assert "preferred='onlyimage'" in message
    assert "configured=['imgbb', 'imgbox', 'onlyimage']" in message
    assert "allowed=['imgbb', 'imgbox', 'onlyimage']" in message
    assert "attempted=['imgbox', 'onlyimage']" in message
    assert "unavailable=['imgbb']" in message


def test_image_start_limiter_waits_between_calls() -> None:
    limiter = uploader._build_image_start_limiter(0.001)

    async def run_twice() -> None:
        await limiter()
        await limiter()

    asyncio.run(run_twice())


def test_configured_host_slot_returns_fallback_for_unconfigured_host() -> None:
    assert (
        uploader._configured_host_slot(_config()["DEFAULT"], "unknown", 7) == 7
    )


def test_upload_one_with_retries_stops_on_open_circuit() -> None:
    async def noop() -> None:
        return None

    task = (0, "source.png", "imgbb", _config(), Meta())
    result = asyncio.run(
        uploader._upload_one_with_retries(
            task,
            asyncio.Semaphore(1),
            noop,
            set(),
            1,
            "imgbb",
            {"imgbb"},
            False,
            {},
        )
    )
    assert result is None


def test_store_partial_successes_ignores_custom_auxiliary_images() -> None:
    meta = Meta(image_list=[])
    uploader._store_partial_successes(
        meta,
        [
            (
                0,
                {
                    "img_url": "https://host/thumb.png",
                    "raw_url": "https://host/raw.png",
                    "web_url": "https://host/view",
                },
            )
        ],
        True,
    )
    assert meta.image_list == []


def test_store_partial_successes_preserves_main_screenshot_urls() -> None:
    meta = Meta(image_list=[], image_sizes={})
    uploader._store_partial_successes(
        meta,
        [
            (
                0,
                {
                    "img_url": "https://host/thumb.png",
                    "raw_url": "https://host/raw.png",
                    "web_url": "https://host/view",
                },
            )
        ],
        False,
    )
    assert meta.image_list == [
        {
            "img_url": "https://host/thumb.png",
            "raw_url": "https://host/raw.png",
            "web_url": "https://host/view",
        }
    ]
