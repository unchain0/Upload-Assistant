# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import base64
import gc
import math
import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
import pyimgbox

from src.domain_models.release import Meta
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.filesystem.temp_paths import screenshots_dir
from src.integrations.image_hosts.contracts import (
    IMAGE_HOST_SPECS,
    MAX_IMAGE_HOST_SLOTS,
)
from src.integrations.image_hosts.fallback import image_host_fallback_plan
from src.integrations.observability.runtime_support import logger

type ImageDict = dict[str, Any]


def _summarize_host_error(error: Any, limit: int = 300) -> str:
    raw = str(error or "Unknown error")
    status = _remote_html_status(raw)
    if status:
        return f"HTTP {status}: remote service error"
    return _clean_host_error_text(raw)[:limit]


def _remote_html_status(raw: str) -> str:
    match = re.search(r"(?:HTTP\s*)?(?<!\d)([45]\d{2})(?!\d)", raw, re.IGNORECASE)
    if match is None or not _looks_like_html_error(raw):
        return ""
    return match.group(1)


def _looks_like_html_error(raw: str) -> bool:
    lowered = raw.lower()
    return any(marker in lowered for marker in ("something went wrong", "<!doctype html", "<html"))


def _clean_host_error_text(raw: str) -> str:
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(
        r"\b(?:body|div(?:\.[\w-]+)?|h\d|p)\s*\{[^{}]*\}",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()


def _json_mapping(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Image host returned a non-object JSON response")
    return cast(dict[str, Any], payload)


def _image_host_error(payload: dict[str, Any], response: httpx.Response) -> str:
    payload_error = _payload_error_message(payload)
    if payload_error:
        return _summarize_host_error(payload_error)
    payload_message = _payload_status_message(payload)
    if payload_message:
        return _summarize_host_error(payload_message)
    return _summarize_host_error(response.text or f"HTTP {response.status_code}")


def _payload_error_message(payload: dict[str, Any]) -> Any:
    error = payload.get("error")
    if isinstance(error, dict):
        mapping = cast(dict[str, Any], error)
        return mapping.get("message") or mapping.get("error")
    return error


def _payload_status_message(payload: dict[str, Any]) -> Any:
    for key in ("message", "status_txt"):
        value = payload.get(key)
        if value:
            return value
    return None


def _chevereto_urls(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    image = _mapping_payload(payload.get("image"))
    if not image:
        return None
    raw_url = image.get("url")
    web_url = image.get("url_viewer") or raw_url
    img_url = _nested_url(image.get("medium")) or _nested_url(image.get("thumb")) or raw_url
    return _validated_url_triplet(img_url, raw_url, web_url)


def _mapping_payload(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _nested_url(value: Any) -> Any:
    return _mapping_payload(value).get("url")


def _validated_url_triplet(img_url: Any, raw_url: Any, web_url: Any) -> tuple[str, str, str] | None:
    values = (img_url, raw_url, web_url)
    if not all(isinstance(value, str) and value for value in values):
        return None
    return cast(str, img_url), cast(str, raw_url), cast(str, web_url)


async def _read_image_bytes(image: str) -> bytes:
    async with aiofiles.open(image, "rb") as img_file:
        return await img_file.read()


async def _upload_chevereto(
    image: str,
    *,
    host_key: str,
    api_key: str | None,
    request_timeout: float,
    nsfw: bool,
) -> dict[str, Any]:
    spec = IMAGE_HOST_SPECS[host_key]
    preflight = _chevereto_preflight(host_key, api_key, spec.upload_url)
    if preflight is not None:
        return preflight
    try:
        response = await _chevereto_request(
            image,
            cast(str, spec.upload_url),
            cast(str, api_key),
            request_timeout,
            nsfw,
        )
        return _chevereto_response_result(image, host_key, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _chevereto_exception_result(host_key, error)


def _chevereto_preflight(host_key: str, api_key: str | None, upload_url: str | None) -> dict[str, Any] | None:
    if not api_key:
        return {
            "status": "failed",
            "reason": f"Missing {host_key} API key",
            "retryable": False,
        }
    if not upload_url:
        return {
            "status": "failed",
            "reason": f"No upload URL configured for {host_key}",
            "retryable": False,
        }
    return None


async def _chevereto_request(
    image: str,
    upload_url: str,
    api_key: str,
    request_timeout: float,
    nsfw: bool,
) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    data = {"nsfw": "1" if nsfw else "0"}
    files = {"source": (Path(image).name, file_bytes)}
    async with httpx.AsyncClient() as client:
        return await client.post(
            upload_url,
            headers=headers,
            data=data,
            files=files,
            timeout=request_timeout,
        )


def _chevereto_response_result(image: str, host_key: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _invalid_json_result(host_key, response)
    if not _chevereto_response_success(response, payload):
        return _chevereto_http_failure(host_key, response, payload)
    urls = _chevereto_urls(payload)
    if urls is None:
        return {
            "status": "failed",
            "reason": f"{host_key} returned an incomplete image response",
        }
    return _successful_image_result(image, *urls)


def _safe_json_mapping(response: httpx.Response) -> dict[str, Any] | None:
    try:
        return _json_mapping(response)
    except ValueError:
        return None


def _invalid_json_result(host_key: str, response: httpx.Response) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": (f"{host_key} returned invalid JSON (HTTP {response.status_code}): {_summarize_host_error(response.text)}"),
        "host_unavailable": response.status_code >= 500,
    }


def _chevereto_response_success(response: httpx.Response, payload: dict[str, Any]) -> bool:
    return response.status_code in (200, 201) and payload.get("status_code") in (None, 200)


def _chevereto_http_failure(host_key: str, response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": (f"{host_key} upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}"),
        "host_unavailable": response.status_code >= 500,
        "retryable": response.status_code == 429 or response.status_code >= 500,
    }


def _chevereto_exception_result(host_key: str, error: Exception) -> dict[str, Any]:
    if isinstance(error, httpx.TimeoutException):
        return {
            "status": "failed",
            "reason": f"{host_key} upload outcome unknown after timeout",
        }
    if isinstance(error, httpx.RequestError):
        return {
            "status": "failed",
            "reason": (f"{host_key} request failed: {_summarize_host_error(error)}"),
            "host_unavailable": True,
        }
    return {
        "status": "failed",
        "reason": (f"Could not read image for {host_key}: {_summarize_host_error(error)}"),
    }


def _successful_image_result(image: str, img_url: str, raw_url: str, web_url: str) -> dict[str, Any]:
    return {
        "status": "success",
        "img_url": img_url,
        "raw_url": raw_url,
        "web_url": web_url,
        "local_file_path": image,
    }


async def _upload_imgbb(image: str, api_key: str | None, *, request_timeout: float) -> dict[str, Any]:
    if not api_key:
        return {
            "status": "failed",
            "reason": "Missing imgbb API key",
            "retryable": False,
        }
    try:
        response = await _imgbb_request(image, api_key, request_timeout)
        return _imgbb_response_result(image, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _imgbb_exception_result(error)


async def _imgbb_request(image: str, api_key: str, request_timeout: float) -> httpx.Response:
    spec = IMAGE_HOST_SPECS["imgbb"]
    file_bytes = await _read_image_bytes(image)
    files = {"image": (Path(image).name, file_bytes)}
    async with httpx.AsyncClient() as client:
        return await client.post(
            cast(str, spec.upload_url),
            params={"key": api_key},
            headers={"Accept": "application/json"},
            files=files,
            timeout=request_timeout,
        )


def _imgbb_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _invalid_json_result("imgbb", response)
    failure = _imgbb_response_failure(response, payload)
    if failure is not None:
        return failure
    urls = _imgbb_urls(payload)
    if urls is None:
        return {
            "status": "failed",
            "reason": "imgbb returned incomplete image URLs",
        }
    return _successful_image_result(image, *urls)


def _imgbb_response_failure(response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any] | None:
    if response.status_code != 200 or payload.get("success") is not True:
        return _imgbb_failure_result(response, payload)
    if not _mapping_payload(payload.get("data")):
        return {
            "status": "failed",
            "reason": "imgbb returned an incomplete image response",
        }
    return None


def _imgbb_failure_result(response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any]:
    reason = _image_host_error(payload, response)
    classification = _imgbb_failure_classification(response.status_code, reason)
    return {
        "status": "failed",
        "reason": (f"imgbb upload failed (HTTP {response.status_code}): {reason}"),
        "host_unavailable": classification["host_unavailable"],
        "retryable": classification["retryable"],
    }


def _imgbb_failure_classification(status_code: int, reason: str) -> dict[str, bool]:
    normalized = reason.casefold()
    rate_limited = _imgbb_rate_limited(status_code, normalized)
    credential_rejected = _imgbb_credential_rejected(status_code, normalized)
    unavailable = rate_limited or credential_rejected or status_code >= 500
    retryable = False if rate_limited or credential_rejected else status_code >= 500
    return {"host_unavailable": unavailable, "retryable": retryable}


def _imgbb_rate_limited(status_code: int, reason: str) -> bool:
    return status_code == 429 or "rate limit" in reason or "too many request" in reason


def _imgbb_credential_rejected(status_code: int, reason: str) -> bool:
    return status_code in {401, 403} or "invalid api key" in reason


def _imgbb_urls(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    data = _mapping_payload(payload.get("data"))
    if not data:
        return None
    raw_url = _imgbb_raw_url(data)
    img_url = _imgbb_display_url(data, raw_url)
    web_url = data.get("url_viewer") or raw_url
    return _validated_url_triplet(img_url, raw_url, web_url)


def _imgbb_raw_url(data: dict[str, Any]) -> Any:
    return _nested_url(data.get("image")) or data.get("url")


def _imgbb_display_url(data: dict[str, Any], raw_url: Any) -> Any:
    candidates = (
        _nested_url(data.get("medium")),
        _nested_url(data.get("thumb")),
        data.get("display_url"),
        raw_url,
    )
    return next((value for value in candidates if value), None)


def _imgbb_exception_result(error: Exception) -> dict[str, Any]:
    if isinstance(error, httpx.TimeoutException):
        return {
            "status": "failed",
            "reason": "imgbb upload outcome unknown after timeout",
        }
    if isinstance(error, httpx.RequestError):
        return {
            "status": "failed",
            "reason": f"imgbb request failed: {_summarize_host_error(error)}",
            "host_unavailable": True,
        }
    return {
        "status": "failed",
        "reason": (f"Could not read image for imgbb: {_summarize_host_error(error)}"),
    }


def _pixhost_raw_url(thumbnail_url: str) -> str:
    parsed = httpx.URL(thumbnail_url)
    hostname = parsed.host or ""
    match = re.fullmatch(r"t(\d+)\.(pixhost\.(?:to|cc)|pixho\.st)", hostname, re.IGNORECASE)
    path = parsed.path
    if match and "/thumbs/" in path:
        hostname = f"img{match.group(1)}.{match.group(2)}"
        path = path.replace("/thumbs/", "/images/", 1)
        return str(parsed.copy_with(host=hostname, path=path))
    return thumbnail_url


def _build_image_start_limiter(delay: float) -> Callable[[], Awaitable[None]]:
    """Create an async wait function that spaces image-upload starts."""
    start_lock = asyncio.Lock()
    last_start = 0.0

    async def wait_for_start_slot() -> None:
        """Wait until the next upload start interval is available."""
        nonlocal last_start
        if delay <= 0:
            return
        async with start_lock:
            now = time.monotonic()
            wait_time = delay - (now - last_start) if last_start else 0.0
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            last_start = time.monotonic()

    return wait_for_start_slot


class UploadScreensManager:
    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize image uploads with independent run-level host
        circuits.
        """
        self.config = config
        self._unavailable_hosts_by_purpose: dict[str, set[str]] = {}

    @property
    def unavailable_hosts(self) -> set[str]:
        """Return the mandatory screenshot circuit for backwards
        compatibility.
        """
        return self.unavailable_hosts_for("screenshots")

    def unavailable_hosts_for(self, purpose: str) -> set[str]:
        normalized = str(purpose or "screenshots").strip().lower() or "screenshots"
        return self._unavailable_hosts_by_purpose.setdefault(normalized, set())

    @staticmethod
    def image_upload_purpose(custom_img_list: Sequence[str | Path]) -> str:
        """Classify known optional image collections without coupling their
        circuits.
        """
        known = {"spectrograms", "dynamic_hdr_plots", "menu_screenshots"}
        for value in custom_img_list:
            parts = {part.casefold() for part in Path(value).parts}
            matched = known & parts
            if matched:
                return sorted(matched)[0]
        return "screenshots"

    def _log_host_plan(
        self,
        meta: Meta,
        purpose: str,
        allowed_hosts: Sequence[str] | None,
        unavailable_hosts: set[str],
    ) -> None:
        default = self.config.get("DEFAULT", {})
        default_map = cast(dict[str, Any], default) if isinstance(default, dict) else {}
        configured = list(image_host_fallback_plan(default_map, preferred_host=None))
        logger.debug(
            f"[cyan]Image host plan: purpose={purpose}"
            f" preferred={meta.imghost!r} configured={configured}"
            f" allowed={list(allowed_hosts or [])}"
            f" unavailable={sorted(unavailable_hosts)}[/cyan]"
        )

    async def upload_screens(
        self,
        meta: Meta,
        screens: int,
        img_host_num: int,
        i: int,
        total_screens: int,
        custom_img_list: Sequence[str | Path],
        return_dict: dict[str, Any],
        retry_mode: bool = False,
        max_retries: int = 3,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[list[ImageDict], int]:
        """Upload images while isolating auxiliary host failures from
        screenshots.
        """
        purpose = self.image_upload_purpose(custom_img_list)
        circuit = self.unavailable_hosts_for(purpose)
        self._log_host_plan(meta, purpose, allowed_hosts, circuit)
        local_available = self._local_image_count(meta, purpose, custom_img_list)
        result = await _upload_screens(
            self.config,
            meta,
            screens,
            img_host_num,
            i,
            total_screens,
            custom_img_list,
            return_dict,
            retry_mode=retry_mode,
            max_retries=max_retries,
            allowed_hosts=allowed_hosts,
            unavailable_hosts=circuit,
        )
        self._log_host_result(
            meta,
            purpose,
            local_available,
            total_screens,
            result[1],
            allowed_hosts,
            circuit,
        )
        return result

    @staticmethod
    def _local_image_count(meta: Meta, purpose: str, custom_img_list: Sequence[str | Path]) -> int:
        if purpose == "screenshots":
            return len(manifest_files(meta.base_dir, meta.uuid, "main"))
        return len(custom_img_list)

    def _log_host_result(
        self,
        meta: Meta,
        purpose: str,
        local_available: int,
        requested: int,
        hosted: int,
        allowed_hosts: Sequence[str] | None,
        unavailable_hosts: set[str],
    ) -> None:
        if hosted >= requested:
            return
        default = self.config.get("DEFAULT", {})
        default_map = cast(dict[str, Any], default) if isinstance(default, dict) else {}
        configured = list(image_host_fallback_plan(default_map, preferred_host=None))
        logger.warning(
            "[yellow]Image host fallback exhausted or incomplete:"
            f" purpose={purpose} preferred={meta.imghost!r}"
            f" configured={configured} allowed={list(allowed_hosts or [])}"
            f" unavailable={sorted(unavailable_hosts)}"
            f" local_available={local_available} hosted={hosted}"
            f" minimum_hosted_required={requested}.[/yellow]"
        )


def _upload_task_meta(meta: object, host: str) -> Meta:
    """Return metadata suitable for isolated and pipeline image uploads."""
    if isinstance(meta, Meta):
        meta.imghost = host
        return meta
    return Meta(imghost=host)


async def upload_image_task(args: Sequence[Any]) -> dict[str, Any]:
    """Upload one image to the selected host and return its generated URLs."""
    image, img_host, config, meta = args
    try:
        host = str(img_host)
        handler = _image_upload_handler(host)
        if handler is None:
            return _unsupported_host_result(host)
        task_meta = _upload_task_meta(meta, host)
        return await handler(str(image), cast(dict[str, Any], config), task_meta, 60.0)
    except Exception as error:
        return {
            "status": "failed",
            "reason": _summarize_host_error(error),
            "retryable": False,
        }


type ImageUploadHandler = Callable[[str, dict[str, Any], Meta, float], Awaitable[dict[str, Any]]]


def _image_upload_handler(host: str) -> ImageUploadHandler | None:
    handlers: dict[str, ImageUploadHandler] = {
        "imgbox": _upload_imgbox_host,
        "imgbb": _upload_imgbb_host,
        "dalexni": _upload_dalexni_host,
        "ptscreens": _upload_chevereto_host,
        "utppm": _upload_chevereto_host,
        "onlyimage": _upload_chevereto_host,
        "pixhost": _upload_pixhost_host,
        "lensdump": _upload_chevereto_host,
        "zipline": _upload_zipline_host,
        "midnightscene": _upload_zipline_host,
        "passtheimage": _upload_chevereto_host,
        "seedpool_cdn": _upload_seedpool_host,
        "sharex": _upload_sharex_host,
        "lostimg": _upload_lostimg_host,
    }
    return handlers.get(host)


def _default_image_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("DEFAULT", {})
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _meta_is_adult(meta: Meta) -> bool:
    return str(getattr(meta, "category", "")).upper() == "XXX"


def _unsupported_host_result(host: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": f"Unsupported image host: {host}",
        "retryable": False,
    }


async def _upload_imgbox_host(image: str, _config: dict[str, Any], meta: Meta, _timeout: float) -> dict[str, Any]:
    state: dict[str, Any] = {}
    images = await imgbox_upload(
        Path.cwd(),
        [image],
        return_dict=state,
        adult=_meta_is_adult(meta),
    )
    usable = _first_usable_image(images)
    if usable is not None:
        return {"status": "success", **usable, "local_file_path": image}
    reason = str(state.get("error") or "Imgbox did not return usable image URLs")
    return {
        "status": "failed",
        "reason": (f"Imgbox unavailable or upload rejected: {_summarize_host_error(reason)}"),
        "host_unavailable": bool(state.get("host_unavailable", True)),
    }


def _first_usable_image(
    images: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not images:
        return None
    first = images[0]
    required = ("img_url", "raw_url", "web_url")
    return first if all(first.get(key) for key in required) else None


async def _upload_imgbb_host(image: str, config: dict[str, Any], _meta: Meta, request_timeout: float) -> dict[str, Any]:
    api_key = _default_image_config(config).get("imgbb_api")
    return await _upload_imgbb(image, cast(str | None, api_key), request_timeout=request_timeout)


async def _upload_chevereto_host(image: str, config: dict[str, Any], meta: Meta, request_timeout: float) -> dict[str, Any]:
    host = _selected_host_from_meta(meta)
    api_key = _chevereto_api_key(_default_image_config(config), host)
    return await _upload_chevereto(
        image,
        host_key=host,
        api_key=api_key,
        request_timeout=request_timeout,
        nsfw=_meta_is_adult(meta),
    )


def _selected_host_from_meta(meta: Meta) -> str:
    return str(meta.imghost or "").strip().lower()


def _chevereto_api_key(default: dict[str, Any], host: str) -> str | None:
    keys = {
        "ptscreens": "ptscreens_api",
        "utppm": "utppm_api",
        "onlyimage": "onlyimage_api",
        "lensdump": "lensdump_api",
        "passtheimage": "passtheima_ge_api",
    }
    key = keys.get(host)
    value = default.get(key) if key else None
    return str(value) if value else None


async def _upload_dalexni_host(image: str, config: dict[str, Any], _meta: Meta, request_timeout: float) -> dict[str, Any]:
    api_key = str(_default_image_config(config).get("dalexni_api") or "").strip()
    if not api_key:
        return {
            "status": "failed",
            "reason": "Missing dalexni API key",
            "retryable": False,
        }
    try:
        response = await _dalexni_request(image, api_key, request_timeout)
        return _dalexni_response_result(image, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _named_host_exception_result("dalexni", error, file_retryable=False)


async def _dalexni_request(image: str, api_key: str, request_timeout: float) -> httpx.Response:
    encoded = base64.b64encode(await _read_image_bytes(image)).decode("utf8")
    data = {"key": api_key, "image": encoded}
    spec = IMAGE_HOST_SPECS["dalexni"]
    async with httpx.AsyncClient() as client:
        return await client.post(
            cast(str, spec.upload_url),
            data=data,
            headers={"Accept": "application/json"},
            timeout=request_timeout,
        )


def _dalexni_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _dalexni_non_json_failure(response)
    failure = _dalexni_response_failure(response, payload)
    if failure is not None:
        return failure
    urls = _dalexni_urls(_mapping_payload(payload.get("data")))
    if urls is None:
        return {
            "status": "failed",
            "reason": "dalexni returned incomplete image URLs",
            "retryable": False,
        }
    return _successful_image_result(image, *urls)


def _dalexni_non_json_failure(response: httpx.Response) -> dict[str, Any]:
    unavailable = response.status_code in {403, 502, 503, 504}
    unavailable = unavailable or response.status_code >= 500
    return {
        "status": "failed",
        "reason": (f"dalexni returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}"),
        "host_unavailable": unavailable,
        "retryable": False,
    }


def _dalexni_response_failure(response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any] | None:
    if response.status_code != 200 or payload.get("success") is not True:
        return _standard_http_failure("dalexni", response, payload)
    if not _mapping_payload(payload.get("data")):
        return {
            "status": "failed",
            "reason": "dalexni returned an incomplete response",
            "retryable": False,
        }
    return None


def _dalexni_urls(data: dict[str, Any]) -> tuple[str, str, str] | None:
    raw_url = _nested_url(data.get("image")) or data.get("url")
    img_url = _nested_url(data.get("medium")) or _nested_url(data.get("thumb")) or raw_url
    web_url = data.get("url_viewer") or raw_url
    return _validated_url_triplet(img_url, raw_url, web_url)


async def _upload_pixhost_host(
    image: str,
    _config: dict[str, Any],
    meta: Meta,
    request_timeout: float,
) -> dict[str, Any]:
    spec = IMAGE_HOST_SPECS["pixhost"]
    size_failure = _pixhost_size_failure(image, spec.max_file_bytes)
    if size_failure is not None:
        return size_failure
    try:
        response = await _pixhost_request(image, meta, request_timeout)
        return _pixhost_response_result(image, response)
    except (
        ValueError,
        httpx.TimeoutException,
        httpx.RequestError,
        OSError,
    ) as error:
        return _pixhost_exception_result(error)


def _pixhost_size_failure(image: str, limit: int | None) -> dict[str, Any] | None:
    size = Path(image).stat().st_size
    if limit is None or size <= limit:
        return None
    return {
        "status": "failed",
        "reason": f"pixhost maximum image size is {limit} bytes",
        "retryable": False,
    }


async def _pixhost_request(image: str, meta: Meta, request_timeout: float) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    data = {
        "content_type": "1" if _meta_is_adult(meta) else "0",
        "max_th_size": "350",
    }
    files = {"img": (Path(image).name, file_bytes)}
    spec = IMAGE_HOST_SPECS["pixhost"]
    async with httpx.AsyncClient() as client:
        return await client.post(
            cast(str, spec.upload_url),
            data=data,
            files=files,
            headers={"Accept": "application/json"},
            timeout=request_timeout,
        )


def _pixhost_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        return _plain_http_failure("pixhost", response)
    payload = _json_mapping(response)
    urls = _validated_url_triplet(
        payload.get("th_url"),
        _pixhost_raw_url(str(payload.get("th_url") or "")),
        payload.get("show_url"),
    )
    if urls is None:
        return {
            "status": "failed",
            "reason": "pixhost returned an incomplete API v2 response",
            "retryable": False,
        }
    return _successful_image_result(image, *urls)


def _pixhost_exception_result(error: Exception) -> dict[str, Any]:
    if isinstance(error, ValueError):
        return {"status": "failed", "reason": "pixhost returned invalid JSON"}
    return _named_host_exception_result("pixhost", error, file_retryable=None)


async def _upload_zipline_host(image: str, config: dict[str, Any], meta: Meta, request_timeout: float) -> dict[str, Any]:
    host = _selected_host_from_meta(meta)
    endpoint = _zipline_endpoint(_default_image_config(config), host)
    if endpoint is None:
        name = "MidnightScene" if host == "midnightscene" else "Zipline"
        return {
            "status": "failed",
            "reason": f"Missing {name} URL or API key",
            "retryable": False,
        }
    url, api_key, host_name = endpoint
    try:
        response = await _zipline_request(image, url, api_key, request_timeout)
        return _zipline_response_result(image, host_name, response)
    except (httpx.TimeoutException, ValueError, httpx.RequestError) as error:
        return _zipline_exception_result(host_name, error)


def _zipline_endpoint(default: dict[str, Any], host: str) -> tuple[str, str, str] | None:
    if host == "midnightscene":
        url = "https://img.midnightscene.cc/api/upload"
        api_key = default.get("midnightscene_api_key")
        name = "MidnightScene"
    else:
        url = default.get("zipline_url")
        api_key = default.get("zipline_api_key")
        name = "Zipline"
    if not url or not api_key:
        return None
    return str(url), str(api_key), name


async def _zipline_request(image: str, url: str, api_key: str, request_timeout: float) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    headers = {"Authorization": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        return await client.post(
            url,
            files={"file": (Path(image).name, file_bytes)},
            headers=headers,
            timeout=request_timeout,
        )


def _zipline_response_result(image: str, host_name: str, response: httpx.Response) -> dict[str, Any]:
    if response.status_code not in (200, 201):
        payload = _safe_json_mapping(response)
        reason = _image_host_error(payload, response) if payload is not None else _summarize_host_error(response.text)
        return _plain_http_failure(host_name, response, reason=reason)
    payload = _json_mapping(response)
    url = _zipline_file_url(payload.get("files"))
    if not url:
        return {
            "status": "failed",
            "reason": f"No valid URL returned from {host_name}",
            "retryable": False,
        }
    return _successful_image_result(image, url, url, url)


def _zipline_file_url(value: Any) -> str:
    first = _first_list_item(value)
    return _zipline_url_from_item(first)


def _first_list_item(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        return None
    return cast(list[Any], value)[0]


def _zipline_url_from_item(value: Any) -> str:
    if isinstance(value, dict):
        url = cast(dict[str, Any], value).get("url")
        return url if isinstance(url, str) else ""
    return value if isinstance(value, str) else ""


def _zipline_exception_result(host_name: str, error: Exception) -> dict[str, Any]:
    if isinstance(error, ValueError):
        return {
            "status": "failed",
            "reason": f"{host_name} returned invalid JSON",
        }
    return _named_host_exception_result(host_name, error, file_retryable=None)


async def _upload_seedpool_host(image: str, config: dict[str, Any], _meta: Meta, request_timeout: float) -> dict[str, Any]:
    api_key = _default_image_config(config).get("seedpool_cdn_api")
    if not api_key:
        return {
            "status": "failed",
            "reason": "Missing seedpool_cdn API key",
            "retryable": False,
        }
    try:
        response = await _seedpool_request(image, str(api_key), request_timeout)
        return _seedpool_response_result(image, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _named_host_exception_result("seedpool_cdn", error, file_retryable=False)


async def _seedpool_request(image: str, api_key: str, request_timeout: float) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    spec = IMAGE_HOST_SPECS["seedpool_cdn"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    files = {"files[]": (Path(image).name, file_bytes)}
    async with httpx.AsyncClient() as client:
        return await client.post(
            cast(str, spec.upload_url),
            headers=headers,
            files=files,
            timeout=request_timeout,
        )


def _seedpool_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _non_json_host_failure("seedpool_cdn", response)
    if response.status_code not in (200, 201):
        return _standard_http_failure("seedpool_cdn", response, payload)
    file_data = _first_mapping(payload.get("files"))
    if file_data is None:
        return {
            "status": "failed",
            "reason": ("seedpool_cdn returned an empty or malformed files response"),
            "retryable": False,
        }
    urls = _seedpool_urls(file_data)
    if urls is None:
        return {
            "status": "failed",
            "reason": "seedpool_cdn returned incomplete image URLs",
            "retryable": False,
        }
    return _successful_image_result(image, *urls)


def _first_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return None
    first = cast(list[Any], value)[0]
    return cast(dict[str, Any], first) if isinstance(first, dict) else None


def _seedpool_urls(data: dict[str, Any]) -> tuple[str, str, str] | None:
    raw_url = data.get("url")
    variants = _mapping_payload(data.get("variants"))
    img_url = data.get("thumbnail_url") or variants.get("thumb") or variants.get("medium") or raw_url
    return _validated_url_triplet(img_url, raw_url, raw_url)


async def _upload_sharex_host(image: str, config: dict[str, Any], _meta: Meta, request_timeout: float) -> dict[str, Any]:
    default = _default_image_config(config)
    url = default.get("sharex_url", "https://img.digitalcore.club/api/upload")
    api_key = default.get("sharex_api_key")
    if not url or not api_key:
        return {
            "status": "failed",
            "reason": "Missing ShareX image host URL or token",
            "retryable": False,
        }
    try:
        response = await _sharex_request(image, str(url), str(api_key), request_timeout)
        return _sharex_response_result(image, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _named_host_exception_result("sharex", error, file_retryable=False)


async def _sharex_request(image: str, url: str, api_key: str, request_timeout: float) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    headers = {"Authorization": api_key, "Accept": "application/json"}
    data = {"title": "Upload-Assistant screenshot"}
    files = {"file": (Path(image).name, file_bytes)}
    async with httpx.AsyncClient() as client:
        return await client.post(
            url,
            headers=headers,
            data=data,
            files=files,
            timeout=request_timeout,
        )


def _sharex_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _non_json_host_failure("sharex host", response)
    if response.status_code not in (200, 201):
        return _standard_http_failure("sharex", response, payload)
    link = _sharex_link(payload)
    if not link:
        return {
            "status": "failed",
            "reason": "sharex host response is missing link",
            "retryable": False,
        }
    return _successful_image_result(image, link, link, link)


def _sharex_link(payload: dict[str, Any]) -> str:
    data = _mapping_payload(payload.get("data"))
    value = data.get("link") or payload.get("link")
    return value if isinstance(value, str) else ""


async def _upload_lostimg_host(image: str, config: dict[str, Any], _meta: Meta, request_timeout: float) -> dict[str, Any]:
    api_key = _default_image_config(config).get("lostimg_api")
    if not api_key:
        return {
            "status": "failed",
            "reason": "Missing lostimg API key",
            "retryable": False,
        }
    try:
        response = await _lostimg_request(image, str(api_key), request_timeout)
        return _lostimg_response_result(image, response)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as error:
        return _named_host_exception_result("lostimg", error, file_retryable=False)


async def _lostimg_request(image: str, api_key: str, request_timeout: float) -> httpx.Response:
    file_bytes = await _read_image_bytes(image)
    spec = IMAGE_HOST_SPECS["lostimg"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    files = {"file[]": (Path(image).name, file_bytes)}
    async with httpx.AsyncClient() as client:
        return await client.post(
            cast(str, spec.upload_url),
            headers=headers,
            files=files,
            timeout=request_timeout,
        )


def _lostimg_response_result(image: str, response: httpx.Response) -> dict[str, Any]:
    payload = _safe_json_mapping(response)
    if payload is None:
        return _non_json_host_failure("lostimg", response)
    if response.status_code not in (200, 201):
        return _standard_http_failure("lostimg", response, payload)
    raw_url = payload.get("url")
    if not isinstance(raw_url, str) or not raw_url:
        return {
            "status": "failed",
            "reason": "lostimg response is missing url",
            "retryable": False,
        }
    return _successful_image_result(image, raw_url, raw_url, raw_url)


def _standard_http_failure(host: str, response: httpx.Response, payload: dict[str, Any]) -> dict[str, Any]:
    return _plain_http_failure(host, response, reason=_image_host_error(payload, response))


def _plain_http_failure(host: str, response: httpx.Response, *, reason: str | None = None) -> dict[str, Any]:
    message = reason if reason is not None else _summarize_host_error(response.text)
    return {
        "status": "failed",
        "reason": (f"{host} upload failed (HTTP {response.status_code}): {message}"),
        "host_unavailable": response.status_code >= 500,
        "retryable": response.status_code == 429 or response.status_code >= 500,
    }


def _non_json_host_failure(host: str, response: httpx.Response) -> dict[str, Any]:
    return {
        "status": "failed",
        "reason": (f"{host} returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}"),
        "host_unavailable": response.status_code >= 500,
        "retryable": response.status_code >= 500,
    }


def _named_host_exception_result(
    host: str,
    error: Exception,
    *,
    file_retryable: bool | None,
) -> dict[str, Any]:
    if isinstance(error, httpx.TimeoutException):
        return {
            "status": "failed",
            "reason": f"{host} upload outcome unknown after timeout",
        }
    if isinstance(error, httpx.RequestError):
        return {
            "status": "failed",
            "reason": f"{host} request failed: {_summarize_host_error(error)}",
            "host_unavailable": True,
        }
    result: dict[str, Any] = {
        "status": "failed",
        "reason": (f"Could not read image for {host}: {_summarize_host_error(error)}"),
    }
    if file_retryable is not None:
        result["retryable"] = file_retryable
    return result


async def _upload_screens(
    config: dict[str, Any],
    meta: Meta,
    screens: int,
    img_host_num: int,
    i: int,
    total_screens: int,
    custom_img_list: Sequence[str | Path],
    return_dict: dict[str, Any],
    retry_mode: bool = False,
    max_retries: int = 3,
    allowed_hosts: Sequence[str] | None = None,
    unavailable_hosts: set[str] | None = None,
    attempted_hosts: set[str] | None = None,
) -> tuple[list[ImageDict], int]:
    """Upload screenshots with deterministic fallback and scoped circuits."""
    del screens, i
    default_config = _default_image_config(config)
    started_at = time.time() if meta.debug else None
    os.chdir(screenshots_dir(meta.base_dir, meta.uuid))
    attempted: set[str] = attempted_hosts if attempted_hosts is not None else set()
    selection = _select_upload_host(
        meta,
        default_config,
        img_host_num,
        allowed_hosts,
        unavailable_hosts,
        attempted,
    )
    if selection is None:
        return meta.image_list, len(meta.image_list)
    img_host, selected_slot, initial_host = selection
    source_files, existing_count, using_custom = await _upload_source_files(meta, custom_img_list, return_dict)
    images_needed = _images_needed(total_screens, existing_count, retry_mode)
    early = _upload_early_result(
        meta,
        total_screens,
        existing_count,
        images_needed,
        source_files,
        retry_mode,
        using_custom,
        img_host,
        initial_host,
    )
    if early is not None:
        return early
    result = await _run_selected_upload_flow(
        config,
        meta,
        selected_slot,
        total_screens,
        custom_img_list,
        return_dict,
        max_retries,
        allowed_hosts,
        unavailable_hosts,
        attempted,
        img_host,
        source_files[:images_needed],
        images_needed,
        using_custom,
        started_at,
    )
    gc.collect()
    return result


async def _run_selected_upload_flow(
    config: dict[str, Any],
    meta: Meta,
    selected_slot: int,
    total_screens: int,
    custom_img_list: Sequence[str | Path],
    return_dict: dict[str, Any],
    max_retries: int,
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
    img_host: str,
    source_files: list[str],
    images_needed: int,
    using_custom: bool,
    started_at: float | None,
) -> tuple[list[ImageDict], int]:
    try:
        return await _run_selected_upload_batch(
            config,
            meta,
            selected_slot,
            total_screens,
            custom_img_list,
            return_dict,
            max_retries,
            allowed_hosts,
            unavailable_hosts,
            attempted_hosts,
            img_host,
            source_files,
            images_needed,
            using_custom,
            started_at,
        )
    except asyncio.CancelledError:
        logger.info("\n[red]Upload process interrupted! Cancelling tasks...[/red]")
        raise


async def _run_selected_upload_batch(
    config: dict[str, Any],
    meta: Meta,
    selected_slot: int,
    total_screens: int,
    custom_img_list: Sequence[str | Path],
    return_dict: dict[str, Any],
    max_retries: int,
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
    img_host: str,
    source_files: list[str],
    images_needed: int,
    using_custom: bool,
    started_at: float | None,
) -> tuple[list[ImageDict], int]:
    successes = await _execute_upload_batch(
        config,
        meta,
        img_host,
        source_files,
        using_custom,
        return_dict,
        max_retries,
        unavailable_hosts,
    )
    if len(successes) < images_needed:
        return await _fallback_after_partial_upload(
            config,
            meta,
            selected_slot,
            total_screens,
            custom_img_list,
            return_dict,
            max_retries,
            allowed_hosts,
            unavailable_hosts,
            attempted_hosts,
            img_host,
            successes,
            using_custom,
        )
    return _finalize_upload_batch(meta, successes, using_custom, started_at)


def _select_upload_host(
    meta: Meta,
    default_config: dict[str, Any],
    img_host_num: int,
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
) -> tuple[str, int, str] | None:
    initial_host = _configured_host(default_config, img_host_num)
    excluded = set(unavailable_hosts or ()) | attempted_hosts
    plan = image_host_fallback_plan(
        default_config,
        preferred_host=meta.imghost,
        allowed_hosts=allowed_hosts,
        unavailable_hosts=excluded,
    )
    if not plan:
        _log_exhausted_host_plan(
            meta,
            default_config,
            allowed_hosts,
            unavailable_hosts,
            attempted_hosts,
        )
        return None
    host = plan[0]
    _log_host_switch(meta, host)
    meta.imghost = host
    slot = _configured_host_slot(default_config, host, img_host_num)
    logger.debug(f"[blue]Using image host: {host} (configured: {initial_host})[/blue]")
    return host, slot, initial_host


def _configured_host(default_config: dict[str, Any], slot: int) -> str:
    return str(default_config.get(f"img_host_{slot}") or "").strip().lower()


def _configured_host_slot(default_config: dict[str, Any], host: str, fallback: int) -> int:
    for slot in range(1, MAX_IMAGE_HOST_SLOTS + 1):
        if _configured_host(default_config, slot) == host:
            return slot
    return fallback


def _log_host_switch(meta: Meta, host: str) -> None:
    preferred = str(meta.imghost or "").strip().lower()
    if preferred and preferred != host:
        logger.info(f"[yellow]Switching image host from '{meta.imghost}' to available host '{host}'.[/yellow]")


def _configured_host_plan(default_config: dict[str, Any]) -> list[str]:
    return list(image_host_fallback_plan(default_config, preferred_host=None))


def _log_exhausted_host_plan(
    meta: Meta,
    default_config: dict[str, Any],
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
) -> None:
    configured = _configured_host_plan(default_config)
    logger.error(
        "[red]No configured image host is available."
        f" preferred={meta.imghost!r} configured={configured}"
        f" allowed={list(allowed_hosts or [])}"
        f" attempted={sorted(attempted_hosts)}"
        f" unavailable={sorted(unavailable_hosts or set())}.[/red]"
    )


async def _upload_source_files(
    meta: Meta,
    custom_img_list: Sequence[str | Path],
    return_dict: dict[str, Any],
) -> tuple[list[str], int, bool]:
    if custom_img_list:
        return [str(path) for path in custom_img_list], 0, True
    files = await _main_screenshot_files(meta)
    files = _filter_menu_screenshots(meta, files)
    files.sort(key=_numeric_screenshot_suffix)
    files = _exclude_uploaded_source_files(files, return_dict)
    logger.debug(f"image globs (sorted): {files}")
    return files, _existing_hosted_image_count(meta.image_list), False


async def _main_screenshot_files(meta: Meta) -> list[str]:
    registered = manifest_files(meta.base_dir, meta.uuid, "main")
    if registered:
        return [str(path.relative_to(Path.cwd())) for path in registered]
    discovered = await _glob_relative_paths(("*.png", ".[!.]*.png"))
    unwanted = await _unwanted_screenshot_files()
    return list(set(discovered) - unwanted)


async def _glob_relative_paths(patterns: Sequence[str]) -> list[str]:
    results: list[str] = []
    for pattern in patterns:
        matches = await asyncio.to_thread(lambda p=pattern: [str(path.relative_to(Path.cwd())) for path in Path.cwd().glob(p)])
        results.extend(matches)
    return results


async def _unwanted_screenshot_files() -> set[str]:
    patterns = tuple(value for base in ("FILE*", "PLAYLIST*", "POSTER*") for value in (base, f".{base}"))
    return set(await _glob_relative_paths(patterns))


def _filter_menu_screenshots(meta: Meta, files: list[str]) -> list[str]:
    menu_basenames = _menu_screenshot_basenames(meta.menu_images)
    return [filename for filename in files if not _is_menu_screenshot(filename, menu_basenames)]


def _menu_screenshot_basenames(value: Any) -> set[str]:
    images = cast(list[Any], value) if isinstance(value, list) else []
    return {Path(source).name for image in images if isinstance(image, dict) if (source := _menu_image_source(cast(dict[str, Any], image)))}


def _menu_image_source(image: dict[str, Any]) -> str:
    value = image.get("local_file_path") or image.get("raw_url")
    return str(value) if value else ""


def _is_menu_screenshot(filename: str, menu_basenames: set[str]) -> bool:
    if filename in menu_basenames:
        return True
    return "-VIDEO_TS-" in filename or "-VTS_" in filename


def _numeric_screenshot_suffix(filename: str) -> float:
    match = re.search(r"-(\d+)\.png$", filename)
    return int(match.group(1)) if match else float("inf")


def _exclude_uploaded_source_files(files: list[str], return_dict: dict[str, Any]) -> list[str]:
    uploaded = return_dict.get("_uploaded_image_files")
    if not isinstance(uploaded, set):
        return files
    return [filename for filename in files if str(Path(filename).resolve()) not in uploaded]


def _existing_hosted_image_count(images: Any) -> int:
    values = cast(list[Any], images) if isinstance(images, list) else []
    return sum(1 for image in values if _has_hosted_image_urls(image))


def _has_hosted_image_urls(image: Any) -> bool:
    if not isinstance(image, dict):
        return False
    mapping = cast(dict[str, Any], image)
    return bool(mapping.get("img_url") and mapping.get("web_url"))


def _images_needed(total_screens: int, existing_count: int, retry_mode: bool) -> int:
    if retry_mode:
        return total_screens
    return max(0, total_screens - existing_count)


def _upload_early_result(
    meta: Meta,
    total_screens: int,
    existing_count: int,
    images_needed: int,
    source_files: list[str],
    retry_mode: bool,
    using_custom: bool,
    img_host: str,
    initial_host: str,
) -> tuple[list[ImageDict], int] | None:
    logger.debug(f"[blue]Existing images: {existing_count}, Images needed: {images_needed}, Total screens: {total_screens}[/blue]")
    reason = _early_upload_reason(
        total_screens,
        existing_count,
        images_needed,
        source_files,
        retry_mode,
        using_custom,
        img_host,
        initial_host,
    )
    if not reason:
        return None
    logger.debug(f"[yellow]Skipping upload: {reason}[/yellow]")
    return _early_upload_value(meta, total_screens, images_needed, source_files)


def _early_upload_reason(
    total_screens: int,
    existing_count: int,
    images_needed: int,
    source_files: list[str],
    retry_mode: bool,
    using_custom: bool,
    img_host: str,
    initial_host: str,
) -> str:
    if total_screens <= 0:
        return "no screenshots required."
    if _existing_images_satisfy_request(
        existing_count,
        total_screens,
        retry_mode,
        using_custom,
        img_host,
        initial_host,
    ):
        return f"{existing_count} existing, {total_screens} required."
    if images_needed == 0:
        return "no additional images required."
    return "no new source images available." if not source_files else ""


def _existing_images_satisfy_request(
    existing_count: int,
    total_screens: int,
    retry_mode: bool,
    using_custom: bool,
    img_host: str,
    initial_host: str,
) -> bool:
    return existing_count >= total_screens and not retry_mode and not using_custom and img_host == initial_host


def _early_upload_value(meta: Meta, total_screens: int, images_needed: int, source_files: list[str]) -> tuple[list[ImageDict], int]:
    if total_screens <= 0:
        return meta.image_list, 0
    if images_needed == 0:
        return meta.image_list, total_screens
    if not source_files:
        return meta.image_list, len(meta.image_list)
    return meta.image_list, total_screens


async def _execute_upload_batch(
    config: dict[str, Any],
    meta: Meta,
    img_host: str,
    source_files: list[str],
    using_custom: bool,
    return_dict: dict[str, Any],
    max_retries: int,
    unavailable_hosts: set[str] | None,
) -> list[tuple[int, dict[str, Any]]]:
    tasks = _upload_task_values(source_files, img_host, config, meta)
    semaphore = asyncio.Semaphore(_upload_worker_count(config, img_host, len(tasks)))
    wait_for_slot = _build_image_start_limiter(_image_upload_delay(config))
    running: set[asyncio.Task[dict[str, Any]]] = set()
    uploads = await _gather_upload_tasks(
        tasks,
        semaphore,
        wait_for_slot,
        running,
        max_retries,
        img_host,
        unavailable_hosts,
        using_custom,
        return_dict,
    )
    results = [result for result in uploads if result is not None]
    results.sort(key=lambda item: item[0])
    logger.debug(f"[blue]Successfully uploaded {len(results)} out of {len(tasks)} attempted uploads.[/blue]")
    return results


async def _gather_upload_tasks(
    tasks: list[tuple[int, str, str, dict[str, Any], Meta]],
    semaphore: asyncio.Semaphore,
    wait_for_slot: Callable[[], Awaitable[None]],
    running: set[asyncio.Task[dict[str, Any]]],
    max_retries: int,
    img_host: str,
    unavailable_hosts: set[str] | None,
    using_custom: bool,
    return_dict: dict[str, Any],
) -> list[tuple[int, dict[str, Any]] | None]:
    awaitables = [
        _upload_one_with_retries(
            task,
            semaphore,
            wait_for_slot,
            running,
            max_retries,
            img_host,
            unavailable_hosts,
            using_custom,
            return_dict,
        )
        for task in tasks
    ]
    try:
        return cast(
            list[tuple[int, dict[str, Any]] | None],
            await asyncio.gather(*awaitables),
        )
    except Exception as error:
        logger.error(f"[red]Error during uploads: {error!s}[/red]")
        return []


def _upload_task_values(
    source_files: list[str],
    img_host: str,
    config: dict[str, Any],
    meta: Meta,
) -> list[tuple[int, str, str, dict[str, Any], Meta]]:
    return [(index, image, img_host, config, meta) for index, image in enumerate(source_files)]


def _upload_worker_count(config: dict[str, Any], host: str, task_count: int) -> int:
    default = _default_image_config(config)
    configured = _positive_int(default.get("image_upload_concurrency"), 0)
    pool = configured if configured > 0 else _host_worker_limit(host, task_count)
    return min(task_count, pool)


def _host_worker_limit(host: str, default: int) -> int:
    limits = {
        "imgbox": 1,
        "imgbb": 1,
        "onlyimage": 6,
        "ptscreens": 6,
        "lensdump": 1,
        "passtheimage": 6,
    }
    return limits.get(host, default)


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except OverflowError, TypeError, ValueError:
        return default


def _image_upload_delay(config: dict[str, Any]) -> float:
    value = _default_image_config(config).get("image_upload_delay", 0)
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return 0.0
    return max(0.0, parsed) if math.isfinite(parsed) else 0.0


async def _upload_one_with_retries(
    task: tuple[int, str, str, dict[str, Any], Meta],
    semaphore: asyncio.Semaphore,
    wait_for_slot: Callable[[], Awaitable[None]],
    running: set[asyncio.Task[dict[str, Any]]],
    max_retries: int,
    img_host: str,
    unavailable_hosts: set[str] | None,
    using_custom: bool,
    return_dict: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    index, *args = task
    async with semaphore:
        if _host_circuit_open(unavailable_hosts, img_host):
            return None
        return await _retry_upload_attempts(
            index,
            args,
            wait_for_slot,
            running,
            max_retries,
            img_host,
            unavailable_hosts,
            using_custom,
            return_dict,
        )


def _host_circuit_open(unavailable_hosts: set[str] | None, host: str) -> bool:
    return unavailable_hosts is not None and host in unavailable_hosts


async def _retry_upload_attempts(
    index: int,
    args: list[Any],
    wait_for_slot: Callable[[], Awaitable[None]],
    running: set[asyncio.Task[dict[str, Any]]],
    max_retries: int,
    img_host: str,
    unavailable_hosts: set[str] | None,
    using_custom: bool,
    return_dict: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    for attempt in range(max_retries + 1):
        outcome = await _single_upload_attempt(index, args, wait_for_slot, running, img_host)
        if outcome is None:
            return None
        if outcome.get("status") == "success":
            _record_uploaded_source_file(args[0], using_custom, return_dict)
            return index, outcome
        action = _failed_upload_action(
            index,
            img_host,
            outcome,
            unavailable_hosts,
            attempt,
            max_retries,
        )
        if action != "retry":
            return None
        await asyncio.sleep(1.1 * (attempt + 1))
    return None


async def _single_upload_attempt(
    index: int,
    args: list[Any],
    wait_for_slot: Callable[[], Awaitable[None]],
    running: set[asyncio.Task[dict[str, Any]]],
    img_host: str,
) -> dict[str, Any] | None:
    future: asyncio.Task[dict[str, Any]] | None = None
    try:
        await wait_for_slot()
        future = asyncio.create_task(upload_image_task(args))
        running.add(future)
        result = await asyncio.wait_for(future, timeout=60.0)
        running.discard(future)
        return result
    except TimeoutError:
        _cancel_upload_future(index, img_host, future, running)
        return None
    except asyncio.CancelledError:
        _cancel_cancelled_upload(index, future, running)
        raise
    except Exception as error:
        logger.error(f"[red]Error during upload for image {index}: {error!s}[/red]")
        return {"status": "failed", "reason": str(error), "retryable": True}


def _cancel_upload_future(
    index: int,
    img_host: str,
    future: asyncio.Task[dict[str, Any]] | None,
    running: set[asyncio.Task[dict[str, Any]]],
) -> None:
    logger.warning(f"[yellow]Upload task {index} timed out after 60 seconds. Not retrying on {img_host} because the host may already have stored it.[/yellow]")
    _cancel_future(future, running)


def _cancel_cancelled_upload(
    index: int,
    future: asyncio.Task[dict[str, Any]] | None,
    running: set[asyncio.Task[dict[str, Any]]],
) -> None:
    logger.info(f"[red]Upload task {index} cancelled.[/red]")
    _cancel_future(future, running)


def _cancel_future(
    future: asyncio.Task[dict[str, Any]] | None,
    running: set[asyncio.Task[dict[str, Any]]],
) -> None:
    if future is None or future not in running:
        return
    future.cancel()
    running.discard(future)


def _record_uploaded_source_file(
    source: Any,
    using_custom: bool,
    return_dict: dict[str, Any],
) -> None:
    if using_custom:
        return
    existing = return_dict.get("_uploaded_image_files")
    uploaded: set[str]
    if isinstance(existing, set):
        uploaded = cast(set[str], existing)
    else:
        uploaded = set()
        return_dict["_uploaded_image_files"] = uploaded
    uploaded.add(str(Path(str(source)).resolve()))


def _failed_upload_action(
    index: int,
    img_host: str,
    result: dict[str, Any],
    unavailable_hosts: set[str] | None,
    attempt: int,
    max_retries: int,
) -> str:
    reason = str(result.get("reason", "Unknown error"))
    terminal = _terminal_upload_failure(index, img_host, result, reason, unavailable_hosts)
    if terminal:
        return "next_host"
    if attempt < max_retries:
        logger.info(f"[yellow]Retry {attempt + 1}/{max_retries} for image {index}: {reason}[/yellow]")
        return "retry"
    logger.error(f"[red]Failed to upload image {index} after {max_retries} attempts: {reason}[/red]")
    return "next_host"


def _terminal_upload_failure(
    index: int,
    img_host: str,
    result: dict[str, Any],
    reason: str,
    unavailable_hosts: set[str] | None,
) -> bool:
    if result.get("host_unavailable"):
        _open_host_circuit(img_host, reason, unavailable_hosts)
        return True
    if result.get("retryable") is False:
        logger.info(f"[yellow]Not retrying {img_host} for image {index}: {reason}. Trying the next configured host.[/yellow]")
        return True
    return _terminal_reason_failure(index, img_host, reason)


def _open_host_circuit(img_host: str, reason: str, unavailable_hosts: set[str] | None) -> None:
    if unavailable_hosts is not None:
        unavailable_hosts.add(img_host)
    logger.warning(f"[yellow]Image host {img_host} is unavailable: {reason}. Trying the next configured host.[/yellow]")


def _terminal_reason_failure(index: int, img_host: str, reason: str) -> bool:
    lowered = reason.lower()
    if "upload outcome unknown" in lowered:
        logger.warning(f"[yellow]Not retrying image {index} on {img_host}: the host may already have stored it. Trying the next configured image host instead.[/yellow]")
        return True
    if "duplicate" in lowered:
        logger.info(f"[yellow]Skipping host because duplicate image {index}: {reason}[/yellow]")
        return True
    if "api key" in lowered:
        logger.info(f"[red]API key error for {img_host}. Aborting further attempts.[/red]")
        return True
    return False


async def _fallback_after_partial_upload(
    config: dict[str, Any],
    meta: Meta,
    selected_slot: int,
    total_screens: int,
    custom_img_list: Sequence[str | Path],
    return_dict: dict[str, Any],
    max_retries: int,
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
    img_host: str,
    successes: list[tuple[int, dict[str, Any]]],
    using_custom: bool,
) -> tuple[list[ImageDict], int]:
    attempted_hosts.add(img_host)
    _store_partial_successes(meta, successes, using_custom)
    next_host = _next_fallback_host(config, allowed_hosts, unavailable_hosts, attempted_hosts)
    if next_host is None:
        _log_no_more_hosts(meta, config, allowed_hosts, unavailable_hosts, attempted_hosts)
        return meta.image_list, len(meta.image_list)
    meta.imghost = next_host
    next_slot = _configured_host_slot(_default_image_config(config), next_host, selected_slot)
    logger.info(f"[cyan]Switching to the next image host: {next_host}[/cyan]")
    gc.collect()
    return await _upload_screens(
        config,
        meta,
        total_screens,
        next_slot,
        0,
        total_screens,
        custom_img_list,
        return_dict,
        retry_mode=True,
        max_retries=max_retries,
        allowed_hosts=allowed_hosts,
        unavailable_hosts=unavailable_hosts,
        attempted_hosts=attempted_hosts,
    )


def _store_partial_successes(
    meta: Meta,
    successes: list[tuple[int, dict[str, Any]]],
    using_custom: bool,
) -> None:
    if using_custom:
        return
    known = _existing_raw_urls(meta.image_list)
    for _index, upload in successes:
        _record_uploaded_image(meta, upload, known)


def _next_fallback_host(
    config: dict[str, Any],
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
) -> str | None:
    plan = image_host_fallback_plan(
        _default_image_config(config),
        preferred_host=None,
        allowed_hosts=allowed_hosts,
        unavailable_hosts=set(unavailable_hosts or ()) | attempted_hosts,
    )
    return plan[0] if plan else None


def _log_no_more_hosts(
    meta: Meta,
    config: dict[str, Any],
    allowed_hosts: Sequence[str] | None,
    unavailable_hosts: set[str] | None,
    attempted_hosts: set[str],
) -> None:
    default = _default_image_config(config)
    configured = _configured_host_plan(default)
    logger.error(
        "[red]No more image hosts available."
        f" preferred={meta.imghost!r} configured={configured}"
        f" allowed={list(allowed_hosts or [])}"
        f" attempted={sorted(attempted_hosts)}"
        f" unavailable={sorted(unavailable_hosts or set())}."
        " Aborting image upload process.[/red]"
    )


def _finalize_upload_batch(
    meta: Meta,
    successes: list[tuple[int, dict[str, Any]]],
    using_custom: bool,
    started_at: float | None,
) -> tuple[list[ImageDict], int]:
    new_images = _new_uploaded_images(successes)
    _store_final_screenshot_images(meta, successes, new_images, using_custom)
    _log_upload_duration(meta, started_at)
    return _final_upload_value(meta, new_images, using_custom)


def _store_final_screenshot_images(
    meta: Meta,
    successes: list[tuple[int, dict[str, Any]]],
    new_images: list[ImageDict],
    using_custom: bool,
) -> None:
    if using_custom:
        return
    known = _existing_raw_urls(meta.image_list)
    for _index, upload in successes:
        _record_uploaded_image(meta, upload, known)
    logger.info(f"[green]Successfully obtained and uploaded {len(new_images)} images.")


def _log_upload_duration(meta: Meta, started_at: float | None) -> None:
    if not meta.debug or started_at is None:
        return
    logger.info(f"Screenshot uploads processed in {time.time() - started_at:.4f} seconds")


def _final_upload_value(meta: Meta, new_images: list[ImageDict], using_custom: bool) -> tuple[list[ImageDict], int]:
    if using_custom:
        return new_images, len(new_images)
    return meta.image_list, len(meta.image_list)


def _new_uploaded_images(
    successes: list[tuple[int, dict[str, Any]]],
) -> list[ImageDict]:
    return [_new_image_dict(upload) for _index, upload in successes]


def _new_image_dict(upload: dict[str, Any]) -> ImageDict:
    image: ImageDict = {
        "img_url": upload["img_url"],
        "raw_url": upload["raw_url"],
        "web_url": upload["web_url"],
    }
    local_file_path = upload.get("local_file_path")
    if local_file_path:
        image["local_file_path"] = str(local_file_path)
    return image


def _existing_raw_urls(images: Any) -> set[str]:
    values = cast(list[Any], images) if isinstance(images, list) else []
    return {str(mapping["raw_url"]) for image in values if isinstance(image, dict) if (mapping := cast(dict[str, Any], image)).get("raw_url")}


def _record_uploaded_image(meta: Meta, upload: dict[str, Any], known_raw_urls: set[str]) -> None:
    raw_url = str(upload["raw_url"])
    if raw_url in known_raw_urls:
        return
    meta.image_list.append(
        {
            "img_url": upload["img_url"],
            "raw_url": raw_url,
            "web_url": upload["web_url"],
        }
    )
    known_raw_urls.add(raw_url)
    _record_uploaded_image_size(meta, raw_url, upload.get("local_file_path"))


def _record_uploaded_image_size(meta: Meta, raw_url: str, local_file_path: Any) -> None:
    if not local_file_path:
        return
    try:
        meta.image_sizes[raw_url] = Path(str(local_file_path)).stat().st_size
    except OSError:
        logger.debug(f"[yellow]Could not stat uploaded image source {local_file_path}.[/yellow]")


async def imgbox_upload(
    chdir: str | Path,
    image_glob: list[str],
    return_dict: dict[str, Any],
    *,
    adult: bool = False,
) -> list[dict[str, str]]:
    """Upload images to Imgbox and store their returned URLs."""
    try:
        os.chdir(chdir)
        images, errors = await _imgbox_gallery_upload(image_glob, adult)
        _record_imgbox_result(return_dict, images, errors)
        return images
    except Exception as error:
        _record_imgbox_unavailable(return_dict, error)
        return []


async def _imgbox_gallery_upload(image_glob: list[str], adult: bool) -> tuple[list[dict[str, str]], list[str]]:
    images: list[dict[str, str]] = []
    errors: list[str] = []
    gallery_api = cast(Any, pyimgbox)
    async with gallery_api.Gallery(thumb_width=350, square_thumbs=False, adult=adult) as gallery:
        for image in image_glob:
            await _imgbox_process_image(gallery, image, images, errors)
    return images, errors


async def _imgbox_process_image(
    gallery: Any,
    image: str,
    images: list[dict[str, str]],
    errors: list[str],
) -> None:
    try:
        async for submission in cast(Any, gallery).add([image]):
            _record_imgbox_submission(image, submission, images, errors)
    except Exception as error:
        summary = _summarize_host_error(error)
        errors.append(summary)
        logger.warning(f"[yellow]ImgBox upload failed for {Path(image).name}: {summary}[/yellow]")


def _record_imgbox_submission(
    image: str,
    submission: Any,
    images: list[dict[str, str]],
    errors: list[str],
) -> None:
    data = cast(dict[str, Any], submission)
    if not data.get("success"):
        _record_imgbox_submission_error(data, errors)
        return
    urls = _imgbox_submission_urls(data)
    if urls is None:
        logger.warning(f"[yellow]ImgBox returned incomplete URLs for {Path(image).name}[/yellow]")
        return
    img_url, raw_url, web_url = urls
    images.append({"web_url": web_url, "img_url": img_url, "raw_url": raw_url})


def _record_imgbox_submission_error(data: dict[str, Any], errors: list[str]) -> None:
    summary = _summarize_host_error(data.get("error"))
    errors.append(summary)
    logger.warning(f"[yellow]ImgBox upload failed: {summary}[/yellow]")


def _imgbox_submission_urls(
    data: dict[str, Any],
) -> tuple[str, str, str] | None:
    return _validated_url_triplet(
        data.get("thumbnail_url"),
        data.get("image_url"),
        data.get("web_url"),
    )


def _record_imgbox_result(
    return_dict: dict[str, Any],
    images: list[dict[str, str]],
    errors: list[str],
) -> None:
    return_dict["image_list"] = images
    if not errors:
        return
    return_dict["error"] = errors[0]
    return_dict["host_unavailable"] = _imgbox_errors_unavailable(errors)


def _imgbox_errors_unavailable(errors: list[str]) -> bool:
    return any(_imgbox_error_unavailable(error) for error in errors)


def _imgbox_error_unavailable(error: str) -> bool:
    lowered = error.lower()
    return "500" in error or "something went wrong" in lowered


def _record_imgbox_unavailable(return_dict: dict[str, Any], error: Exception) -> None:
    summary = _summarize_host_error(error)
    return_dict["error"] = summary
    return_dict["host_unavailable"] = True
    logger.warning(f"[yellow]ImgBox unavailable: {summary}[/yellow]")
