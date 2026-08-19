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
from src.integrations.filesystem.screenshot_manifest import files as manifest_files
from src.integrations.filesystem.temp_paths import screenshots_dir
from src.integrations.image_hosts.contracts import IMAGE_HOST_SPECS, MAX_IMAGE_HOST_SLOTS
from src.integrations.image_hosts.fallback import image_host_fallback_plan
from src.integrations.observability.runtime_support import logger

type ImageDict = dict[str, Any]


def _summarize_host_error(error: Any, limit: int = 300) -> str:
    raw = str(error or "Unknown error")
    status_match = re.search(r"(?:HTTP\s*)?(?<!\d)([45]\d{2})(?!\d)", raw, re.IGNORECASE)
    if status_match and ("something went wrong" in raw.lower() or "<!doctype html" in raw.lower() or "<html" in raw.lower()):
        status = status_match.group(1)
        return f"HTTP {status}: remote service error"

    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\b(?:body|div(?:\.[\w-]+)?|h\d|p)\s*\{[^{}]*\}", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _json_mapping(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Image host returned a non-object JSON response")
    return cast(dict[str, Any], payload)


def _image_host_error(payload: dict[str, Any], response: httpx.Response) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        error_map = cast(dict[str, Any], error)
        message = error_map.get("message") or error_map.get("error")
        if message:
            return _summarize_host_error(message)
    elif error:
        return _summarize_host_error(error)
    for key in ("message", "status_txt"):
        if payload.get(key):
            return _summarize_host_error(payload[key])
    return _summarize_host_error(response.text or f"HTTP {response.status_code}")


def _chevereto_urls(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    image = payload.get("image")
    if not isinstance(image, dict):
        return None
    image_map = cast(dict[str, Any], image)
    raw_url = image_map.get("url")
    web_url = image_map.get("url_viewer") or raw_url
    medium = image_map.get("medium")
    thumb = image_map.get("thumb")
    medium_url = cast(dict[str, Any], medium).get("url") if isinstance(medium, dict) else None
    thumb_url = cast(dict[str, Any], thumb).get("url") if isinstance(thumb, dict) else None
    img_url = medium_url or thumb_url or raw_url
    if all(isinstance(value, str) and value for value in (img_url, raw_url, web_url)):
        return cast(str, img_url), cast(str, raw_url), cast(str, web_url)
    return None


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
    if not api_key:
        return {"status": "failed", "reason": f"Missing {host_key} API key", "retryable": False}
    if not spec.upload_url:
        return {"status": "failed", "reason": f"No upload URL configured for {host_key}", "retryable": False}

    try:
        file_bytes = await _read_image_bytes(image)
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        data = {"nsfw": "1" if nsfw else "0"}
        files = {"source": (Path(image).name, file_bytes)}
        async with httpx.AsyncClient() as client:
            response = await client.post(spec.upload_url, headers=headers, data=data, files=files, timeout=request_timeout)
        try:
            payload = _json_mapping(response)
        except ValueError:
            return {
                "status": "failed",
                "reason": f"{host_key} returned invalid JSON (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                "host_unavailable": response.status_code >= 500,
            }
        status_code = payload.get("status_code")
        if response.status_code not in (200, 201) or status_code not in (None, 200):
            return {
                "status": "failed",
                "reason": f"{host_key} upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}",
                "host_unavailable": response.status_code >= 500,
                "retryable": response.status_code == 429 or response.status_code >= 500,
            }
        urls = _chevereto_urls(payload)
        if urls is None:
            return {"status": "failed", "reason": f"{host_key} returned an incomplete image response"}
        img_url, raw_url, web_url = urls
        return {
            "status": "success",
            "img_url": img_url,
            "raw_url": raw_url,
            "web_url": web_url,
            "local_file_path": image,
        }
    except httpx.TimeoutException:
        return {"status": "failed", "reason": f"{host_key} upload outcome unknown after timeout"}
    except httpx.RequestError as error:
        return {"status": "failed", "reason": f"{host_key} request failed: {_summarize_host_error(error)}", "host_unavailable": True}
    except OSError as error:
        return {"status": "failed", "reason": f"Could not read image for {host_key}: {_summarize_host_error(error)}"}


async def _upload_imgbb(image: str, api_key: str | None, *, request_timeout: float) -> dict[str, Any]:
    spec = IMAGE_HOST_SPECS["imgbb"]
    if not api_key:
        return {"status": "failed", "reason": "Missing imgbb API key", "retryable": False}
    try:
        file_bytes = await _read_image_bytes(image)
        files = {"image": (Path(image).name, file_bytes)}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                cast(str, spec.upload_url),
                params={"key": api_key},
                headers={"Accept": "application/json"},
                files=files,
                timeout=request_timeout,
            )
        try:
            payload = _json_mapping(response)
        except ValueError:
            return {
                "status": "failed",
                "reason": f"imgbb returned invalid JSON (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                "host_unavailable": response.status_code >= 500,
            }
        if response.status_code != 200 or payload.get("success") is not True:
            reason = _image_host_error(payload, response)
            normalized_reason = reason.casefold()
            rate_limited = response.status_code == 429 or "rate limit" in normalized_reason or "too many request" in normalized_reason
            credential_rejected = response.status_code in {401, 403} or "invalid api key" in normalized_reason
            return {
                "status": "failed",
                "reason": f"imgbb upload failed (HTTP {response.status_code}): {reason}",
                # A rate limit or rejected credential affects every image for
                # this configured host, so opening the run-level circuit avoids
                # retry storms while still allowing the fallback plan to run.
                "host_unavailable": rate_limited or credential_rejected or response.status_code >= 500,
                "retryable": False if rate_limited or credential_rejected else response.status_code >= 500,
            }
        data_value = payload.get("data")
        if not isinstance(data_value, dict):
            return {"status": "failed", "reason": "imgbb returned an incomplete image response"}
        data = cast(dict[str, Any], data_value)
        image_value = data.get("image")
        thumb_value = data.get("thumb")
        medium_value = data.get("medium")
        raw_url = cast(dict[str, Any], image_value).get("url") if isinstance(image_value, dict) else data.get("url")
        thumb_url = cast(dict[str, Any], thumb_value).get("url") if isinstance(thumb_value, dict) else None
        medium_url = cast(dict[str, Any], medium_value).get("url") if isinstance(medium_value, dict) else None
        img_url = medium_url or thumb_url or data.get("display_url") or raw_url
        web_url = data.get("url_viewer") or raw_url
        if not all(isinstance(value, str) and value for value in (img_url, raw_url, web_url)):
            return {"status": "failed", "reason": "imgbb returned incomplete image URLs"}
        return {
            "status": "success",
            "img_url": img_url,
            "raw_url": raw_url,
            "web_url": web_url,
            "local_file_path": image,
        }
    except httpx.TimeoutException:
        return {"status": "failed", "reason": "imgbb upload outcome unknown after timeout"}
    except httpx.RequestError as error:
        return {"status": "failed", "reason": f"imgbb request failed: {_summarize_host_error(error)}", "host_unavailable": True}
    except OSError as error:
        return {"status": "failed", "reason": f"Could not read image for imgbb: {_summarize_host_error(error)}"}


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
        """Initialize screenshot uploads with the application configuration."""
        self.config = config
        self.unavailable_hosts: set[str] = set()

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
        """Upload the selected screenshots and return uploaded image metadata."""
        return await _upload_screens(
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
            unavailable_hosts=self.unavailable_hosts,
        )


async def upload_image_task(args: Sequence[Any]) -> dict[str, Any]:
    """Upload one image to the selected host and return its generated URLs."""
    image, img_host, config, _meta = args
    try:
        timeout = 60  # Default timeout

        if img_host == "imgbox":
            imgbox_result: dict[str, Any] = {}
            image_list = await imgbox_upload(
                Path.cwd(),
                [image],
                return_dict=imgbox_result,
                adult=str(getattr(_meta, "category", "")).upper() == "XXX",
            )
            if image_list and all(key in image_list[0] for key in ("img_url", "raw_url", "web_url")):
                return {"status": "success", **image_list[0], "local_file_path": image}
            reason = str(imgbox_result.get("error") or "Imgbox did not return usable image URLs")
            return {
                "status": "failed",
                "reason": f"Imgbox unavailable or upload rejected: {_summarize_host_error(reason)}",
                "host_unavailable": bool(imgbox_result.get("host_unavailable", True)),
            }

        if img_host == "imgbb":
            return await _upload_imgbb(image, config.get("DEFAULT", {}).get("imgbb_api"), request_timeout=timeout)

        if img_host == "dalexni":
            spec = IMAGE_HOST_SPECS["dalexni"]
            api_key_value = config.get("DEFAULT", {}).get("dalexni_api")
            api_key = str(api_key_value or "").strip()
            if not api_key:
                return {"status": "failed", "reason": "Missing dalexni API key", "retryable": False}
            try:
                encoded_image = base64.b64encode(await _read_image_bytes(image)).decode("utf8")
                data = {"key": api_key, "image": encoded_image}
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        cast(str, spec.upload_url),
                        data=data,
                        headers={"Accept": "application/json"},
                        timeout=timeout,
                    )
                try:
                    payload = _json_mapping(response)
                except ValueError:
                    return {
                        "status": "failed",
                        "reason": f"dalexni returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                        "host_unavailable": response.status_code in {403, 502, 503, 504} or response.status_code >= 500,
                        "retryable": False,
                    }
                if response.status_code != 200 or payload.get("success") is not True:
                    return {
                        "status": "failed",
                        "reason": f"dalexni upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
                data_value = payload.get("data")
                if not isinstance(data_value, dict):
                    return {"status": "failed", "reason": "dalexni returned an incomplete response", "retryable": False}
                data_map = cast(dict[str, Any], data_value)
                image_value = data_map.get("image")
                thumb_value = data_map.get("thumb")
                medium_value = data_map.get("medium")
                raw_url = cast(dict[str, Any], image_value).get("url") if isinstance(image_value, dict) else data_map.get("url")
                thumb_url = cast(dict[str, Any], thumb_value).get("url") if isinstance(thumb_value, dict) else None
                medium_url = cast(dict[str, Any], medium_value).get("url") if isinstance(medium_value, dict) else None
                img_url = medium_url or thumb_url or raw_url
                web_url = data_map.get("url_viewer") or raw_url
                if not all(isinstance(value, str) and value for value in (img_url, raw_url, web_url)):
                    return {"status": "failed", "reason": "dalexni returned incomplete image URLs", "retryable": False}
                return {
                    "status": "success",
                    "img_url": img_url,
                    "raw_url": raw_url,
                    "web_url": web_url,
                    "local_file_path": image,
                }
            except httpx.TimeoutException:
                return {"status": "failed", "reason": "dalexni upload outcome unknown after timeout"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"dalexni request failed: {_summarize_host_error(error)}", "host_unavailable": True}
            except OSError as error:
                return {"status": "failed", "reason": f"Could not read image for dalexni: {_summarize_host_error(error)}", "retryable": False}

        if img_host == "ptscreens":
            return await _upload_chevereto(
                image,
                host_key="ptscreens",
                api_key=config.get("DEFAULT", {}).get("ptscreens_api"),
                request_timeout=timeout,
                nsfw=str(getattr(_meta, "category", "")).upper() == "XXX",
            )

        if img_host == "utppm":
            return await _upload_chevereto(
                image,
                host_key="utppm",
                api_key=config.get("DEFAULT", {}).get("utppm_api"),
                request_timeout=timeout,
                nsfw=str(getattr(_meta, "category", "")).upper() == "XXX",
            )

        if img_host == "onlyimage":
            return await _upload_chevereto(
                image,
                host_key="onlyimage",
                api_key=config.get("DEFAULT", {}).get("onlyimage_api"),
                request_timeout=timeout,
                nsfw=str(getattr(_meta, "category", "")).upper() == "XXX",
            )

        if img_host == "pixhost":
            spec = IMAGE_HOST_SPECS["pixhost"]
            try:
                image_size = Path(image).stat().st_size
                if spec.max_file_bytes is not None and image_size > spec.max_file_bytes:
                    return {"status": "failed", "reason": f"pixhost maximum image size is {spec.max_file_bytes} bytes", "retryable": False}
                file_bytes = await _read_image_bytes(image)
                data = {
                    "content_type": "1" if str(getattr(_meta, "category", "")).upper() == "XXX" else "0",
                    "max_th_size": "350",
                }
                files = {"img": (Path(image).name, file_bytes)}
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        cast(str, spec.upload_url),
                        data=data,
                        files=files,
                        headers={"Accept": "application/json"},
                        timeout=timeout,
                    )
                if response.status_code != 200:
                    return {
                        "status": "failed",
                        "reason": f"pixhost upload failed (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
                payload = _json_mapping(response)
                th_url = payload.get("th_url")
                web_url = payload.get("show_url")
                if not isinstance(th_url, str) or not th_url or not isinstance(web_url, str) or not web_url:
                    return {"status": "failed", "reason": "pixhost returned an incomplete API v2 response", "retryable": False}
                return {
                    "status": "success",
                    "img_url": th_url,
                    "raw_url": _pixhost_raw_url(th_url),
                    "web_url": web_url,
                    "local_file_path": image,
                }
            except ValueError:
                return {"status": "failed", "reason": "pixhost returned invalid JSON"}
            except httpx.TimeoutException:
                return {"status": "failed", "reason": "pixhost upload outcome unknown after timeout"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"pixhost request failed: {_summarize_host_error(error)}", "host_unavailable": True}
            except OSError as error:
                return {"status": "failed", "reason": f"Could not read image for pixhost: {_summarize_host_error(error)}"}

        if img_host == "lensdump":
            return await _upload_chevereto(
                image,
                host_key="lensdump",
                api_key=config.get("DEFAULT", {}).get("lensdump_api"),
                request_timeout=timeout,
                nsfw=str(getattr(_meta, "category", "")).upper() == "XXX",
            )

        if img_host in ("zipline", "midnightscene"):
            if img_host == "midnightscene":
                url = "https://img.midnightscene.cc/api/upload"
                api_key = config["DEFAULT"].get("midnightscene_api_key")
                host_name = "MidnightScene"
            else:
                url = config["DEFAULT"].get("zipline_url")
                api_key = config["DEFAULT"].get("zipline_api_key")
                host_name = "Zipline"

            if not url or not api_key:
                return {"status": "failed", "reason": f"Missing {host_name} URL or API key", "retryable": False}

            try:
                async with aiofiles.open(image, "rb") as img_file:
                    filename = Path(image).name
                    file_bytes = await img_file.read()
                headers = {
                    "Authorization": f"{api_key}",
                    "Accept": "application/json",
                }

                async with httpx.AsyncClient() as client:
                    response = await client.post(url, files={"file": (filename, file_bytes)}, headers=headers, timeout=timeout)
                    if response.status_code in (200, 201):
                        zipline_response_data: object = response.json()
                        zipline_response_mapping = cast(dict[str, Any], zipline_response_data) if isinstance(zipline_response_data, dict) else {}
                        zipline_files_value = zipline_response_mapping.get("files")
                        if not isinstance(zipline_files_value, list) or not zipline_files_value:
                            return {"status": "failed", "reason": f"No valid URL returned from {host_name}", "retryable": False}

                        file_entry: object = cast(list[object], zipline_files_value)[0]
                        zipline_img_url: str | None = None
                        if isinstance(file_entry, dict):
                            file_entry_dict = cast(dict[str, object], file_entry)
                            candidate_url = file_entry_dict.get("url")
                            if isinstance(candidate_url, str):
                                zipline_img_url = candidate_url
                        elif isinstance(file_entry, str):
                            zipline_img_url = file_entry
                        if not zipline_img_url:
                            return {"status": "failed", "reason": f"No valid URL returned from {host_name}", "retryable": False}
                        # Zipline's current API returns the canonical file URL in
                        # files[].url. Do not rewrite /u/ into legacy view/raw routes.
                        return {
                            "status": "success",
                            "img_url": zipline_img_url,
                            "raw_url": zipline_img_url,
                            "web_url": zipline_img_url,
                            "local_file_path": image,
                        }

                    try:
                        error_payload = _json_mapping(response)
                        reason = _image_host_error(error_payload, response)
                    except ValueError:
                        reason = _summarize_host_error(response.text)
                    return {
                        "status": "failed",
                        "reason": f"{host_name} upload failed (HTTP {response.status_code}): {reason}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
            except httpx.TimeoutException:
                return {"status": "failed", "reason": f"{host_name} upload outcome unknown after timeout"}
            except ValueError:
                return {"status": "failed", "reason": f"{host_name} returned invalid JSON"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"{host_name} request failed: {_summarize_host_error(error)}", "host_unavailable": True}

        if img_host == "passtheimage":
            return await _upload_chevereto(
                image,
                host_key="passtheimage",
                api_key=config.get("DEFAULT", {}).get("passtheima_ge_api"),
                request_timeout=timeout,
                nsfw=str(getattr(_meta, "category", "")).upper() == "XXX",
            )

        if img_host == "seedpool_cdn":
            spec = IMAGE_HOST_SPECS["seedpool_cdn"]
            api_key = config.get("DEFAULT", {}).get("seedpool_cdn_api")
            if not api_key:
                return {"status": "failed", "reason": "Missing seedpool_cdn API key", "retryable": False}
            try:
                file_bytes = await _read_image_bytes(image)
                headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
                files = {"files[]": (Path(image).name, file_bytes)}
                async with httpx.AsyncClient() as client:
                    response = await client.post(cast(str, spec.upload_url), headers=headers, files=files, timeout=timeout)
                try:
                    payload = _json_mapping(response)
                except ValueError:
                    return {
                        "status": "failed",
                        "reason": f"seedpool_cdn returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code >= 500,
                    }
                if response.status_code not in (200, 201):
                    return {
                        "status": "failed",
                        "reason": f"seedpool_cdn upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
                files_value = payload.get("files")
                if not isinstance(files_value, list) or not files_value or not isinstance(files_value[0], dict):
                    return {"status": "failed", "reason": "seedpool_cdn returned an empty or malformed files response", "retryable": False}
                file_data = cast(dict[str, Any], files_value[0])
                raw_url = file_data.get("url")
                variants = file_data.get("variants")
                variants_map = cast(dict[str, Any], variants) if isinstance(variants, dict) else {}
                img_url = file_data.get("thumbnail_url") or variants_map.get("thumb") or variants_map.get("medium") or raw_url
                if not isinstance(raw_url, str) or not raw_url or not isinstance(img_url, str) or not img_url:
                    return {"status": "failed", "reason": "seedpool_cdn returned incomplete image URLs", "retryable": False}
                return {
                    "status": "success",
                    "img_url": img_url,
                    "raw_url": raw_url,
                    "web_url": raw_url,
                    "local_file_path": image,
                }
            except httpx.TimeoutException:
                return {"status": "failed", "reason": "seedpool_cdn upload outcome unknown after timeout"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"seedpool_cdn request failed: {_summarize_host_error(error)}", "host_unavailable": True}
            except OSError as error:
                return {"status": "failed", "reason": f"Could not read image for seedpool_cdn: {_summarize_host_error(error)}", "retryable": False}

        if img_host == "sharex":
            url = config.get("DEFAULT", {}).get("sharex_url", "https://img.digitalcore.club/api/upload")
            api_key = config.get("DEFAULT", {}).get("sharex_api_key")
            if not url or not api_key:
                return {"status": "failed", "reason": "Missing ShareX image host URL or token", "retryable": False}
            try:
                file_bytes = await _read_image_bytes(image)
                headers = {"Authorization": f"{api_key}", "Accept": "application/json"}
                data = {"title": "Upload-Assistant screenshot"}
                files = {"file": (Path(image).name, file_bytes)}
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, headers=headers, data=data, files=files, timeout=timeout)
                try:
                    payload = _json_mapping(response)
                except ValueError:
                    return {
                        "status": "failed",
                        "reason": f"sharex host returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code >= 500,
                    }
                if response.status_code not in (200, 201):
                    return {
                        "status": "failed",
                        "reason": f"sharex upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
                data_value = payload.get("data")
                data_map = cast(dict[str, Any], data_value) if isinstance(data_value, dict) else {}
                link = data_map.get("link") or payload.get("link")
                if not isinstance(link, str) or not link:
                    return {"status": "failed", "reason": "sharex host response is missing link", "retryable": False}
                return {"status": "success", "img_url": link, "raw_url": link, "web_url": link, "local_file_path": image}
            except httpx.TimeoutException:
                return {"status": "failed", "reason": "sharex upload outcome unknown after timeout"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"sharex request failed: {_summarize_host_error(error)}", "host_unavailable": True}
            except OSError as error:
                return {"status": "failed", "reason": f"Could not read image for sharex: {_summarize_host_error(error)}", "retryable": False}

        if img_host == "lostimg":
            spec = IMAGE_HOST_SPECS["lostimg"]
            api_key = config.get("DEFAULT", {}).get("lostimg_api")
            if not api_key:
                return {"status": "failed", "reason": "Missing lostimg API key", "retryable": False}
            try:
                file_bytes = await _read_image_bytes(image)
                headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
                files = {"file[]": (Path(image).name, file_bytes)}
                async with httpx.AsyncClient() as client:
                    response = await client.post(cast(str, spec.upload_url), headers=headers, files=files, timeout=timeout)
                try:
                    payload = _json_mapping(response)
                except ValueError:
                    return {
                        "status": "failed",
                        "reason": f"lostimg returned non-JSON response (HTTP {response.status_code}): {_summarize_host_error(response.text)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code >= 500,
                    }
                if response.status_code not in (200, 201):
                    return {
                        "status": "failed",
                        "reason": f"lostimg upload failed (HTTP {response.status_code}): {_image_host_error(payload, response)}",
                        "host_unavailable": response.status_code >= 500,
                        "retryable": response.status_code == 429 or response.status_code >= 500,
                    }
                raw_url = payload.get("url")
                if not isinstance(raw_url, str) or not raw_url:
                    return {"status": "failed", "reason": "lostimg response is missing url", "retryable": False}
                return {"status": "success", "img_url": raw_url, "raw_url": raw_url, "web_url": raw_url, "local_file_path": image}
            except httpx.TimeoutException:
                return {"status": "failed", "reason": "lostimg upload outcome unknown after timeout"}
            except httpx.RequestError as error:
                return {"status": "failed", "reason": f"lostimg request failed: {_summarize_host_error(error)}", "host_unavailable": True}
            except OSError as error:
                return {"status": "failed", "reason": f"Could not read image for lostimg: {_summarize_host_error(error)}", "retryable": False}

        return {"status": "failed", "reason": f"Unsupported image host: {img_host}", "retryable": False}

    except Exception as error:
        return {"status": "failed", "reason": _summarize_host_error(error), "retryable": False}


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
    """Select screenshots, throttle uploads, and collect successful results."""
    default_config = config.get("DEFAULT", {})
    upload_start_time: float | None = None
    if meta.debug:
        upload_start_time = time.time()

    os.chdir(screenshots_dir(meta.base_dir, meta.uuid))

    initial_img_host = str(default_config.get(f"img_host_{img_host_num}") or "").strip().lower()
    image_list = meta.image_list

    if attempted_hosts is None:
        attempted_hosts = set()
    excluded_hosts = set(unavailable_hosts or ()) | attempted_hosts
    fallback_plan = image_host_fallback_plan(
        default_config,
        preferred_host=meta.imghost,
        allowed_hosts=allowed_hosts,
        unavailable_hosts=excluded_hosts,
    )
    if not fallback_plan:
        allowed_message = f" allowed by {allowed_hosts}" if allowed_hosts else ""
        logger.error(f"[red]No configured image host is available{allowed_message}. Configure DEFAULT.img_host_1 or another image-host slot.[/red]")
        return image_list, len(image_list)

    img_host = fallback_plan[0]
    if meta.imghost and str(meta.imghost).strip().lower() != img_host:
        logger.info(f"[yellow]Switching image host from '{meta.imghost}' to available host '{img_host}'.[/yellow]")
    meta.imghost = img_host
    for slot in range(1, MAX_IMAGE_HOST_SLOTS + 1):
        if str(default_config.get(f"img_host_{slot}") or "").strip().lower() == img_host:
            img_host_num = slot
            break

    logger.debug(f"[blue]Using image host: {img_host} (configured: {initial_img_host})[/blue]")
    using_custom_img_list = bool(custom_img_list)

    existing_raw_urls = {img["raw_url"] for img in image_list}

    def _record_uploaded_image(
        upload_image_list: list[ImageDict],
        upload_meta: Meta,
        upload: dict[str, Any],
        known_raw_urls: set[str],
    ) -> None:
        raw_url = upload["raw_url"]
        if raw_url in known_raw_urls:
            return

        new_image: ImageDict = {
            "img_url": upload["img_url"],
            "raw_url": raw_url,
            "web_url": upload["web_url"],
        }
        upload_image_list.append(new_image)
        known_raw_urls.add(raw_url)
        local_file_path = upload.get("local_file_path")
        if local_file_path:
            upload_meta.image_sizes[raw_url] = Path(local_file_path).stat().st_size

    # Handle image selection

    if using_custom_img_list:
        image_glob = [str(path) for path in custom_img_list]
        existing_images: list[ImageDict] = []
        existing_count = 0
    else:
        registered_screens = manifest_files(meta.base_dir, meta.uuid, "main")
        if registered_screens:
            image_glob = [str(path.relative_to(Path.cwd())) for path in registered_screens]
        else:
            image_patterns = ["*.png", ".[!.]*.png"]
            image_glob = []
            for pattern in image_patterns:
                glob_results = await asyncio.to_thread(lambda p=pattern: [str(path.relative_to(Path.cwd())) for path in Path.cwd().glob(p)])
                image_glob.extend(glob_results)

            unwanted_patterns = ["FILE*", "PLAYLIST*", "POSTER*"]
            unwanted_files: set[str] = set()
            for pattern in unwanted_patterns:
                glob_results = await asyncio.to_thread(lambda p=pattern: [str(path.relative_to(Path.cwd())) for path in Path.cwd().glob(p)])
                unwanted_files.update(glob_results)
                if pattern.startswith("FILE") or pattern.startswith("PLAYLIST") or pattern.startswith("POSTER"):
                    hidden_pattern = "." + pattern
                    hidden_glob_results = await asyncio.to_thread(lambda hp=hidden_pattern: [str(path.relative_to(Path.cwd())) for path in Path.cwd().glob(hp)])
                    unwanted_files.update(hidden_glob_results)

            image_glob = [file for file in image_glob if file not in unwanted_files]
            image_glob = list(set(image_glob))

        # Filter out menu screenshots from normal screenshot upload
        menu_basenames = set()
        if hasattr(meta, "menu_images") and meta.menu_images:
            for img in meta.menu_images:
                if isinstance(img, dict):
                    local_path = img.get("local_file_path") or img.get("raw_url")
                    if local_path:
                        menu_basenames.add(Path(local_path).name)

        def is_menu_screenshot(filename: str) -> bool:
            """Return whether filename belongs to a DVD menu screenshot."""
            if filename in menu_basenames:
                return True
            return "-VIDEO_TS-" in filename or "-VTS_" in filename

        image_glob = [file for file in image_glob if not is_menu_screenshot(file)]

        # Sort images by numeric suffix
        def extract_numeric_suffix(filename: str) -> float:
            """Return the numeric screenshot suffix for stable ordering."""
            match = re.search(r"-(\d+)\.png$", filename)
            return int(match.group(1)) if match else float("inf")

        image_glob.sort(key=extract_numeric_suffix)

        logger.debug(f"image globs (sorted): {image_glob}")

        existing_images = [img for img in image_list if img.get("img_url") and img.get("web_url")]
        existing_count = len(existing_images)

        uploaded_image_files = return_dict.get("_uploaded_image_files")
        if isinstance(uploaded_image_files, set):
            image_glob = [file for file in image_glob if str(Path(file).resolve()) not in uploaded_image_files]

    # Determine images needed
    images_needed = max(0, total_screens - existing_count) if not retry_mode else total_screens
    logger.debug(f"[blue]Existing images: {existing_count}, Images needed: {images_needed}, Total screens: {total_screens}[/blue]")

    # Some upload types (notably BOOK) legitimately have no screenshots.  The
    # selected host can differ from img_host_1 when supplied via --imghost, so
    # do not make this no-op conditional on the configured initial host.
    if total_screens <= 0:
        logger.debug("[yellow]Skipping upload: no screenshots required.[/yellow]")
        return image_list, 0

    if existing_count >= total_screens and not retry_mode and img_host == initial_img_host and not using_custom_img_list:
        logger.debug(f"[yellow]Skipping upload: {existing_count} existing, {total_screens} required.")
        return image_list, total_screens

    if images_needed == 0:
        logger.debug("[yellow]Skipping upload: no additional images required.[/yellow]")
        return image_list, total_screens

    if not image_glob:
        logger.debug("[yellow]Skipping upload: no new source images available.[/yellow]")
        return image_list, len(image_list)

    upload_tasks: list[tuple[int, str, str, dict[str, Any], Meta]] = [(index, image, img_host, config, meta) for index, image in enumerate(image_glob[:images_needed])]

    # Concurrency Control
    default_pool_size = len(upload_tasks)
    host_limits = {"imgbox": 1, "imgbb": 1, "onlyimage": 6, "ptscreens": 6, "lensdump": 1, "passtheimage": 6}
    configured_concurrency = default_config.get("image_upload_concurrency", 0)
    try:
        configured_concurrency = int(configured_concurrency)
    except OverflowError, TypeError, ValueError:
        configured_concurrency = 0
    pool_size = configured_concurrency if configured_concurrency > 0 else host_limits.get(img_host, default_pool_size)
    max_workers = min(len(upload_tasks), pool_size)
    semaphore = asyncio.Semaphore(max_workers)

    configured_delay = default_config.get("image_upload_delay", 0)
    try:
        parsed_delay = float(configured_delay)
        image_upload_delay = max(0.0, parsed_delay) if math.isfinite(parsed_delay) else 0.0
    except TypeError, ValueError:
        image_upload_delay = 0.0
    wait_for_image_start_slot = _build_image_start_limiter(image_upload_delay)

    # Track running tasks for cancellation
    running_tasks: set[asyncio.Task[dict[str, Any]]] = set()

    async def async_upload(
        task: tuple[int, str, str, dict[str, Any], Meta],
        max_retries: int = 3,
    ) -> tuple[int, dict[str, Any]] | None:
        """Upload image with concurrency control and retry logic."""
        index, *task_args = task
        retry_count = 0

        async with semaphore:
            if unavailable_hosts is not None and img_host in unavailable_hosts:
                return None
            while retry_count <= max_retries:
                future: asyncio.Task[dict[str, Any]] | None = None
                try:
                    await wait_for_image_start_slot()
                    future = asyncio.create_task(upload_image_task(task_args))
                    running_tasks.add(future)

                    try:
                        result = await asyncio.wait_for(future, timeout=60.0)
                        running_tasks.discard(future)

                        if result.get("status") == "success":
                            if not using_custom_img_list:
                                uploaded_image_files = return_dict.setdefault("_uploaded_image_files", set())
                                if isinstance(uploaded_image_files, set):
                                    uploaded_image_files.add(str(Path(str(task_args[0])).resolve()))
                            return (index, result)
                        reason = result.get("reason", "Unknown error")
                        if result.get("host_unavailable"):
                            if unavailable_hosts is not None:
                                unavailable_hosts.add(img_host)
                            logger.warning(f"[yellow]Image host {img_host} is unavailable: {reason}. Trying the next configured host.[/yellow]")
                            return None
                        if result.get("retryable") is False:
                            logger.info(f"[yellow]Not retrying {img_host} for image {index}: {reason}. Trying the next configured host.[/yellow]")
                            return None
                        if "upload outcome unknown" in reason.lower():
                            logger.warning(
                                f"[yellow]Not retrying image {index} on {img_host}: the host may already have stored it. "
                                "Trying the next configured image host instead.[/yellow]"
                            )
                            return None
                        if "duplicate" in reason.lower():
                            logger.info(f"[yellow]Skipping host because duplicate image {index}: {reason}[/yellow]")
                            return None
                        if "api key" in reason.lower():
                            logger.info(f"[red]API key error for {img_host}. Aborting further attempts.[/red]")
                            return None
                        if retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"[yellow]Retry {retry_count}/{max_retries} for image {index}: {reason}[/yellow]")
                            await asyncio.sleep(1.1 * retry_count)
                            continue
                        logger.error(f"[red]Failed to upload image {index} after {max_retries} attempts: {reason}[/red]")
                        return None

                    except TimeoutError:
                        logger.warning(
                            f"[yellow]Upload task {index} timed out after 60 seconds. Not retrying on {img_host} because the host may already have stored it.[/yellow]"
                        )
                        if future in running_tasks:
                            future.cancel()
                            running_tasks.discard(future)
                        return None

                except asyncio.CancelledError:
                    logger.info(f"[red]Upload task {index} cancelled.[/red]")
                    if future and future in running_tasks:
                        future.cancel()
                        running_tasks.discard(future)
                    raise

                except Exception as e:
                    logger.error(f"[red]Error during upload for image {index}: {e!s}[/red]")
                    if retry_count < max_retries:
                        retry_count += 1
                        logger.info(f"[yellow]Retry {retry_count}/{max_retries} for image {index}: {e!s}[/yellow]")
                        await asyncio.sleep(1.5 * retry_count)
                        continue
                    logger.error(f"[red]Error during upload for image {index} after {max_retries} attempts: {e!s}[/red]")
                    return None

        return None

    try:
        results: list[tuple[int, dict[str, Any]]] = []
        try:
            upload_results = await asyncio.gather(*[async_upload(task, max_retries) for task in upload_tasks])
            results = [res for res in upload_results if res is not None]
            results.sort(key=lambda x: x[0])
        except Exception as e:
            logger.error(f"[red]Error during uploads: {e!s}[/red]")

        successfully_uploaded = [(index, result) for index, result in results if result["status"] == "success"]
        logger.debug(f"[blue]Successfully uploaded {len(successfully_uploaded)} out of {len(upload_tasks)} attempted uploads.[/blue]")

        # Ensure we only switch hosts if necessary
        logger.debug(f"[blue]Double checking current image host: {img_host}, Initial image host: {initial_img_host}[/blue]")
        logger.debug(f"[blue]retry_mode: {retry_mode}, using_custom_img_list: {using_custom_img_list}[/blue]")
        logger.debug(f"[blue]successfully_uploaded={len(successfully_uploaded)}, meta.image_list={len(image_list)}, cutoff={meta.cutoff}[/blue]")
        if len(successfully_uploaded) < len(upload_tasks):
            # This host has completed its opportunity for the current batch.
            # Keep that per-release state separate from the run-level circuit:
            # payload-specific failures must be retried for the next release,
            # while true outages/rate limits are added to ``unavailable_hosts``
            # by ``async_upload``.
            attempted_hosts.add(img_host)

            # Preserve partial successes before recursing so the next host only
            # needs to handle failed source files and the accumulated list is
            # not lost when fallback completes.
            if not using_custom_img_list:
                for _index, upload in successfully_uploaded:
                    _record_uploaded_image(image_list, meta, upload, existing_raw_urls)

            remaining_plan = image_host_fallback_plan(
                default_config,
                preferred_host=None,
                allowed_hosts=allowed_hosts,
                unavailable_hosts=set(unavailable_hosts or ()) | attempted_hosts,
            )
            if remaining_plan:
                next_host = remaining_plan[0]
                next_host_num = 1
                for slot in range(1, MAX_IMAGE_HOST_SLOTS + 1):
                    if str(default_config.get(f"img_host_{slot}") or "").strip().lower() == next_host:
                        next_host_num = slot
                        break
                meta.imghost = next_host
                logger.info(f"[cyan]Switching to the next image host: {meta.imghost}[/cyan]")
                gc.collect()
                return await _upload_screens(
                    config,
                    meta,
                    screens,
                    next_host_num,
                    i,
                    total_screens,
                    custom_img_list,
                    return_dict,
                    retry_mode=True,
                    max_retries=max_retries,
                    allowed_hosts=allowed_hosts,
                    unavailable_hosts=unavailable_hosts,
                    attempted_hosts=attempted_hosts,
                )
            logger.info("[red]No more image hosts available. Aborting upload process.[/red]")
            return image_list, len(image_list)

        # Process and store successfully uploaded images
        new_images: list[ImageDict] = []
        for _index, upload in successfully_uploaded:
            raw_url = upload["raw_url"]
            new_image = {"img_url": upload["img_url"], "raw_url": raw_url, "web_url": upload["web_url"]}
            # Custom uploads (disc menus and spectrograms) are not added to
            # ``meta.image_list``.  Keep their local source so a tracker that
            # rejects the initially selected host can re-upload the same asset.
            local_file_path = upload.get("local_file_path")
            if local_file_path:
                new_image["local_file_path"] = str(local_file_path)
            new_images.append(new_image)
            if not using_custom_img_list:
                if raw_url not in existing_raw_urls:
                    logger.debug(f"[blue]Adding {raw_url} to image_list")
                _record_uploaded_image(image_list, meta, upload, existing_raw_urls)

        if not using_custom_img_list:
            logger.info(f"[green]Successfully obtained and uploaded {len(new_images)} images.")

        if meta.debug and upload_start_time is not None:
            logger.info(f"Screenshot uploads processed in {time.time() - upload_start_time:.4f} seconds")

        return (new_images, len(new_images)) if using_custom_img_list else (image_list, len(image_list))

    except asyncio.CancelledError:
        logger.info("\n[red]Upload process interrupted! Cancelling tasks...[/red]")

        # ``asyncio.gather`` cancels child uploads; ``async_upload`` owns the
        # per-host future and removes it from ``running_tasks`` before re-raising.
        raise

    finally:
        # Cleanup
        gc.collect()


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
        image_list: list[dict[str, str]] = []

        errors: list[str] = []
        pyimgbox_api = cast(Any, pyimgbox)
        async with pyimgbox_api.Gallery(thumb_width=350, square_thumbs=False, adult=adult) as gallery:

            async def process_image(image: str) -> None:
                """Upload one image through the active Imgbox gallery."""
                try:
                    async for submission in cast(Any, gallery).add([image]):
                        submission_data = cast(dict[str, Any], submission)
                        if not submission_data.get("success"):
                            error_summary = _summarize_host_error(submission_data.get("error"))
                            errors.append(error_summary)
                            logger.warning(f"[yellow]ImgBox upload failed: {error_summary}[/yellow]")
                        else:
                            web_url = cast(str | None, submission_data.get("web_url"))
                            img_url = cast(str | None, submission_data.get("thumbnail_url"))
                            raw_url = cast(str | None, submission_data.get("image_url"))
                            if web_url and img_url and raw_url:
                                image_dict: dict[str, str] = {"web_url": web_url, "img_url": img_url, "raw_url": raw_url}
                                image_list.append(image_dict)
                            else:
                                logger.warning(f"[yellow]ImgBox returned incomplete URLs for {Path(image).name}[/yellow]")
                except Exception as error:
                    error_summary = _summarize_host_error(error)
                    errors.append(error_summary)
                    logger.warning(f"[yellow]ImgBox upload failed for {Path(image).name}: {error_summary}[/yellow]")

            for image in image_glob:
                await process_image(image)

        return_dict["image_list"] = image_list
        if errors:
            return_dict["error"] = errors[0]
            return_dict["host_unavailable"] = any("500" in error or "something went wrong" in error.lower() for error in errors)
        return image_list

    except Exception as error:
        error_summary = _summarize_host_error(error)
        return_dict["error"] = error_summary
        return_dict["host_unavailable"] = True
        logger.warning(f"[yellow]ImgBox unavailable: {error_summary}[/yellow]")
        return []
