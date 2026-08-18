# ruff: noqa: S101

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import pytest

from src.image_hosts import IMAGE_HOST_SPECS, MAX_IMAGE_HOST_SLOTS, image_host_config_map, image_host_size_within_limit
from src.meta import Meta
from src.uploadscreens import _pixhost_raw_url, upload_image_task


class _FakeResponse:
    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": "application/json"}

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse, requests: list[tuple[tuple[object, ...], dict[str, object]]]) -> None:
        self.response = response
        self.requests = requests

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        self.requests.append((args, kwargs))
        return self.response


def _upload(
    tmp_path: Path,
    *,
    host: str,
    defaults: dict[str, Any],
    response: _FakeResponse,
    category: str = "MOVIE",
) -> tuple[dict[str, Any], list[tuple[tuple[object, ...], dict[str, object]]]]:
    image = tmp_path / "screen.png"
    image.write_bytes(b"image-bytes")
    requests: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def exercise() -> dict[str, Any]:
        return await upload_image_task((str(image), host, {"DEFAULT": defaults}, Meta(category=category)))

    with patch("src.uploadscreens.httpx.AsyncClient", return_value=_FakeClient(response, requests)):
        result = asyncio.run(exercise())
    return result, requests


def test_image_host_registry_covers_every_configurable_host() -> None:
    expected = {
        "imgbb",
        "imgbox",
        "pixhost",
        "lensdump",
        "ptscreens",
        "onlyimage",
        "dalexni",
        "zipline",
        "midnightscene",
        "passtheimage",
        "seedpool_cdn",
        "sharex",
        "utppm",
        "lostimg",
    }

    assert set(IMAGE_HOST_SPECS) == expected
    assert set(image_host_config_map()) == expected
    assert MAX_IMAGE_HOST_SLOTS == 10
    assert all(spec.documentation_url or not spec.public_api for spec in IMAGE_HOST_SPECS.values())


def test_documented_image_size_limits_match_provider_contracts() -> None:
    assert image_host_size_within_limit("imgbb", 32_000_000)
    assert not image_host_size_within_limit("imgbb", 32_000_001)
    assert image_host_size_within_limit("imgbox", 10_000_000)
    assert not image_host_size_within_limit("imgbox", 10_000_001)
    assert image_host_size_within_limit("pixhost", 10_000_000)
    assert not image_host_size_within_limit("pixhost", 10_000_001)


def test_imgbb_uses_official_multipart_v1_contract(tmp_path: Path) -> None:
    payload = {
        "success": True,
        "status": 200,
        "data": {
            "url_viewer": "https://ibb.co/abc",
            "url": "https://i.ibb.co/raw.png",
            "image": {"url": "https://i.ibb.co/raw.png"},
            "thumb": {"url": "https://i.ibb.co/thumb.png"},
            "medium": {"url": "https://i.ibb.co/medium.png"},
        },
    }
    result, requests = _upload(tmp_path, host="imgbb", defaults={"imgbb_api": "secret-key"}, response=_FakeResponse(200, payload))

    assert result["status"] == "success"
    assert result["img_url"] == "https://i.ibb.co/medium.png"
    assert result["raw_url"] == "https://i.ibb.co/raw.png"
    assert result["web_url"] == "https://ibb.co/abc"
    assert requests == [
        (
            ("https://api.imgbb.com/1/upload",),
            {
                "params": {"key": "secret-key"},
                "headers": {"Accept": "application/json"},
                "files": {"image": ("screen.png", b"image-bytes")},
                "timeout": 60,
            },
        )
    ]


@pytest.mark.parametrize(
    ("host", "api_key_name", "endpoint"),
    [
        ("lensdump", "lensdump_api", "https://lensdump.com/api/1/upload"),
        ("ptscreens", "ptscreens_api", "https://ptscreens.com/api/1/upload"),
        ("onlyimage", "onlyimage_api", "https://onlyimage.org/api/1/upload"),
        ("utppm", "utppm_api", "https://utp.pm/api/1/upload"),
        ("passtheimage", "passtheima_ge_api", "https://passtheima.ge/api/1/upload"),
    ],
)
def test_chevereto_hosts_use_source_and_header_auth(tmp_path: Path, host: str, api_key_name: str, endpoint: str) -> None:
    payload = {
        "status_code": 200,
        "success": {"message": "file uploaded", "code": 200},
        "image": {
            "url": f"https://{host}.example/raw.png",
            "url_viewer": f"https://{host}.example/view/abc",
            "thumb": {"url": f"https://{host}.example/thumb.png"},
            "medium": {"url": f"https://{host}.example/medium.png"},
        },
    }
    result, requests = _upload(tmp_path, host=host, defaults={api_key_name: "host-key"}, response=_FakeResponse(200, payload))

    assert result["status"] == "success"
    assert result["img_url"] == f"https://{host}.example/medium.png"
    assert requests[0][0] == (endpoint,)
    assert requests[0][1]["headers"] == {"X-API-Key": "host-key", "Accept": "application/json"}
    assert requests[0][1]["files"] == {"source": ("screen.png", b"image-bytes")}
    assert requests[0][1]["data"] == {"nsfw": "0"}


def test_chevereto_xxx_upload_marks_nsfw(tmp_path: Path) -> None:
    payload = {
        "status_code": 200,
        "image": {"url": "https://host/raw.png", "url_viewer": "https://host/view", "thumb": {"url": "https://host/thumb.png"}},
    }
    _result, requests = _upload(
        tmp_path,
        host="onlyimage",
        defaults={"onlyimage_api": "host-key"},
        response=_FakeResponse(200, payload),
        category="XXX",
    )

    assert requests[0][1]["data"] == {"nsfw": "1"}


def test_chevereto_error_message_is_preserved_without_html_noise(tmp_path: Path) -> None:
    payload = {"status_code": 400, "error": {"message": "Invalid API key.", "code": 100}, "status_txt": "Bad Request"}
    result, _requests = _upload(
        tmp_path,
        host="onlyimage",
        defaults={"onlyimage_api": "bad-key"},
        response=_FakeResponse(400, payload),
    )

    assert result == {
        "status": "failed",
        "reason": "onlyimage upload failed (HTTP 400): Invalid API key.",
        "host_unavailable": False,
        "retryable": False,
    }


def test_pixhost_v2_uses_accept_header_and_nsfw_content_type(tmp_path: Path) -> None:
    payload = {
        "name": "screen.png",
        "show_url": "https://pixhost.to/show/123/screen.png",
        "th_url": "https://t1.pixhost.to/thumbs/123/screen.png",
    }
    result, requests = _upload(tmp_path, host="pixhost", defaults={}, response=_FakeResponse(200, payload), category="XXX")

    assert result["status"] == "success"
    assert result["raw_url"] == "https://img1.pixhost.to/images/123/screen.png"
    assert requests[0][0] == ("https://api.pixhost.to/images",)
    assert requests[0][1]["headers"] == {"Accept": "application/json"}
    assert requests[0][1]["data"] == {"content_type": "1", "max_th_size": "350"}
    assert requests[0][1]["files"] == {"img": ("screen.png", b"image-bytes")}


def test_pixhost_raw_url_falls_back_when_thumbnail_shape_is_unknown() -> None:
    assert _pixhost_raw_url("https://cdn.example/thumb.png") == "https://cdn.example/thumb.png"


def test_imgbox_failure_is_marked_unavailable_for_fast_fallback(tmp_path: Path) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"image")

    async def exercise() -> dict[str, Any]:
        return await upload_image_task((str(image), "imgbox", {"DEFAULT": {}}, Meta(category="MOVIE")))

    async def fake_imgbox(*_args: object, return_dict: dict[str, Any], **_kwargs: object) -> list[dict[str, str]]:
        return_dict["error"] = "HTTP 500: service unavailable"
        return_dict["host_unavailable"] = True
        return []

    with patch("src.uploadscreens.imgbox_upload", new=AsyncMock(side_effect=fake_imgbox)):
        result = asyncio.run(exercise())

    assert result["status"] == "failed"
    assert result["host_unavailable"] is True
    assert "service unavailable" in result["reason"]
