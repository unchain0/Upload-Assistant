# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import io
from collections.abc import Mapping, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import cli_ui
import click
import httpx
from PIL import Image

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.engines.tracker_description_policy import (
    DescriptionCandidate,
    add_candidate,
    description_fingerprint,
    resolve_description_mode,
    score_release_name,
)
from src.integrations.external_apis.btn import BtnIdManager
from src.integrations.filesystem.temp_paths import screenshots_dir
from src.integrations.mapping.value_coercion import to_int
from src.integrations.observability.runtime_support import (
    buffer_console_logs,
    logger,
)
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.trackers.common import Common
from src.integrations.trackers.registry import api_trackers

config: dict[str, Any] = {}
default_config: Mapping[str, Any] = {}
trackers_config: Mapping[str, Any] = {}

type ImageDict = dict[str, Any]


expected_images = 0


def _apply_config(next_config: dict[str, Any]) -> None:
    global config, default_config, trackers_config, expected_images
    config = next_config
    default_config = cast(Mapping[str, Any], next_config.get("DEFAULT", {}))
    trackers_config = cast(Mapping[str, Any], next_config.get("TRACKERS", {}))
    expected_images = to_int(default_config.get("screens", 0))


class TrackerMetaManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        _apply_config(config)

    async def prompt_user_for_confirmation(
        self, message: str, meta: Meta | None = None
    ) -> bool:
        return await prompt_user_for_confirmation(message, meta)

    async def check_images_concurrently(
        self, imagelist: Sequence[ImageDict], meta: Meta
    ) -> list[ImageDict]:
        return await check_images_concurrently(imagelist, meta)

    async def check_image_link(
        self, url: str, request_timeout: httpx.Timeout | None = None
    ) -> bool:
        return await check_image_link(url, request_timeout)

    async def update_meta_with_unit3d_data(
        self,
        meta: Meta,
        tracker_data: Sequence[Any],
        tracker_name: str,
        skip_tracker_descriptions: bool = False,
    ) -> bool:
        return await update_meta_with_unit3d_data(
            meta, tracker_data, tracker_name, skip_tracker_descriptions
        )

    async def update_metadata_from_tracker(
        self,
        tracker_name: str,
        tracker_instance: Any,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool = False,
        *,
        torrent_id: str = "",
    ) -> tuple[Meta, bool]:
        return await update_metadata_from_tracker(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
            torrent_id=torrent_id,
        )

    async def handle_image_list(
        self,
        meta: Meta,
        tracker_name: str,
        valid_images: Sequence[ImageDict] | None = None,
    ) -> None:
        await handle_image_list(meta, tracker_name, valid_images)


async def prompt_user_for_confirmation(
    message: str, meta: Meta | None = None
) -> bool:
    if meta and meta.unattended and not meta.unattended_confirm:
        return False
    try:
        async with buffer_console_logs():
            return cli_ui.ask_yes_no(message, default=True)
    except EOFError:
        raise OperationAbortedError(
            "Tracker metadata confirmation was cancelled by the user."
        ) from None


_RESOLUTION_HEIGHTS = {
    "8640p": 8640,
    "4320p": 4320,
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "1080i": 1080,
    "720p": 720,
    "576p": 576,
    "576i": 576,
    "480p": 480,
    "480i": 480,
}


def _log_duplicate_summary(
    meta: Meta, unique_count: int, total_count: int
) -> None:
    if not meta.debug:
        return
    if unique_count >= total_count:
        return
    logger.info(
        f"[yellow]Removed {total_count - unique_count} duplicate images from the list.[/yellow]"
    )


def _unique_images(
    imagelist: Sequence[ImageDict], meta: Meta
) -> list[ImageDict]:
    seen_urls: set[str] = set()
    unique: list[ImageDict] = []
    for image in imagelist:
        url = cast(str | None, image.get("raw_url"))
        if not url:
            continue
        if url in seen_urls:
            logger.debug(
                f"[yellow]Removing duplicate image URL: {url}[/yellow]"
            )
            continue
        seen_urls.add(url)
        unique.append(image)
    _log_duplicate_summary(meta, len(unique), len(imagelist))
    return unique


def _expected_image_height(meta: Meta) -> int | None:
    resolution = str(meta.resolution or "")
    expected = _RESOLUTION_HEIGHTS.get(resolution)
    if expected is None:
        logger.info(
            "[red]Meta resolution is invalid or missing. Skipping all images.[/red]"
        )
    return expected


def _is_tmdb_image(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "tmdb.org" or host.endswith(".tmdb.org")


async def _fetch_image_download(
    session: httpx.AsyncClient, url: str
) -> httpx.Response | None:
    try:
        return await session.get(url)
    except TimeoutError:
        logger.info(f"[red]Timeout downloading image: {url}")
    except httpx.HTTPError as error:
        logger.info(f"[red]Client error downloading image: {url} - {error}")
    return None


async def _download_image_bytes(
    url: str, request_timeout: httpx.Timeout
) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as session:
            response = await _fetch_image_download(session, url)
    except Exception as error:
        logger.info(f"[red]Session error for image: {url} - {error}")
        return None
    if response is None:
        return None
    if response.status_code != 200:
        logger.error(
            f"[red]Failed to fetch image {url}. Status: {response.status_code}. Skipping."
        )
        return None
    return response.content


def _open_image(content: bytes, url: str) -> Image.Image | None:
    try:
        return Image.open(BytesIO(content))
    except Exception as error:
        logger.error(f"[red]Failed to process image {url}: {error}")
        return None


def _image_height_bounds(
    expected_height: int, meta: Meta
) -> tuple[float, float]:
    upper_multiplier = 1.30 if meta.is_disc == "DVD" else 1.00
    return expected_height * 0.70, expected_height * upper_multiplier


def _image_resolution_allowed(
    image: Image.Image, expected_height: int, meta: Meta, url: str
) -> bool:
    lower, upper = _image_height_bounds(expected_height, meta)
    if lower <= image.height <= upper:
        return True
    logger.info(
        f"[red]Image {url} resolution ({image.height}p) "
        f"is outside the allowed range ({int(lower)}-{int(upper)}p). Skipping.[/red]"
    )
    return False


async def _save_image_content(
    content: bytes, url: str, image: Image.Image, meta: Meta
) -> None:
    save_directory = Path(meta.base_dir) / "tmp" / meta.uuid
    save_directory.mkdir(parents=True, exist_ok=True)
    image_filename = save_directory / Path(url).name
    await asyncio.to_thread(image_filename.write_bytes, content)
    logger.info(f"Saved {url} as {image_filename}")
    meta.image_sizes[url] = len(content)
    logger.debug(
        f"Valid image {url} with resolution {image.width}x{image.height} "
        f"and size {len(content) / 1024:.2f} KiB"
    )


async def _process_verified_image(
    image_dict: ImageDict,
    meta: Meta,
    expected_height: int,
    url: str,
    content: bytes,
) -> ImageDict | None:
    image = _open_image(content, url)
    if image is None:
        return None
    if not _image_resolution_allowed(image, expected_height, meta, url):
        return None
    await _save_image_content(content, url, image, meta)
    return image_dict


async def _verified_image_dict(
    image_dict: ImageDict,
    meta: Meta,
    expected_height: int,
    request_timeout: httpx.Timeout,
) -> ImageDict | None:
    url = _normalized_image_url(cast(str, image_dict["raw_url"]))
    if _is_tmdb_image(url):
        return None
    try:
        link_ok = await check_image_link(url, request_timeout)
    except Exception as error:
        logger.error(f"[red]Error checking image: {url} - {error}")
        return None
    if not link_ok:
        return None
    content = await _download_image_bytes(url, request_timeout)
    if content is None:
        return None
    return await _process_verified_image(
        image_dict, meta, expected_height, url, content
    )


async def _bounded_image_check(
    semaphore: asyncio.Semaphore,
    image_dict: ImageDict,
    meta: Meta,
    expected_height: int,
    request_timeout: httpx.Timeout,
) -> ImageDict | None:
    async with semaphore:
        return await _verified_image_dict(
            image_dict, meta, expected_height, request_timeout
        )


async def _gather_valid_images(tasks: Sequence[Any]) -> list[ImageDict]:
    try:
        results = await asyncio.gather(*tasks, return_exceptions=False)
    except Exception as error:
        logger.error(f"[red]Error during image processing: {error}")
        return []
    return [cast(ImageDict, image) for image in results if image is not None]


async def check_images_concurrently(
    imagelist: Sequence[ImageDict], meta: Meta
) -> list[ImageDict]:
    unique_images = _unique_images(imagelist, meta)
    expected_height = _expected_image_height(meta)
    if expected_height is None:
        return []
    timeout = httpx.Timeout(15.0, connect=5.0, read=5.0)
    semaphore = asyncio.Semaphore(2)
    tasks = [
        _bounded_image_check(semaphore, image, meta, expected_height, timeout)
        for image in unique_images
    ]
    valid_images = await _gather_valid_images(tasks)
    if expected_images < len(valid_images):
        return valid_images[:expected_images]
    return valid_images


def _normalized_image_url(url: str) -> str:
    if not url.startswith("https://pixhost.to/show/"):
        return url
    return url.replace(
        "https://pixhost.to/show/",
        "https://img1.pixhost.to/images/",
        1,
    )


def _verified_image_bytes(image_data: bytes, url: str) -> bool:
    try:
        image = Image.open(io.BytesIO(image_data))
        image.verify()
        return True
    except (OSError, SyntaxError) as error:
        logger.info(
            "[red]Image verification failed (corrupt image): "
            f"{url} {error}[/red]"
        )
        return False


def _verified_image_content(response: httpx.Response, url: str) -> bool:
    if response.status_code != 200:
        logger.error(
            f"[red]Failed to retrieve image: {url} "
            f"(status code: {response.status_code})[/red]"
        )
        return False
    content_type = response.headers.get("Content-Type", "").lower()
    if "image" not in content_type:
        logger.info(f"[red]Content type is not an image: {url}[/red]")
        return False
    return _verified_image_bytes(response.content, url)


async def _fetch_image_response(
    session: httpx.AsyncClient, url: str
) -> httpx.Response | None:
    try:
        return await session.get(url)
    except TimeoutError:
        logger.info(f"[red]Timeout checking image link: {url}[/red]")
        return None
    except Exception as error:
        logger.info(
            f"[red]Exception occurred while checking image: {url} - "
            f"{error!s}[/red]"
        )
        return None


async def check_image_link(
    url: str, request_timeout: httpx.Timeout | None = None
) -> bool:
    url = _normalized_image_url(url)
    request_timeout = request_timeout or httpx.Timeout(20.0, connect=10.0)
    try:
        async with httpx.AsyncClient(
            timeout=request_timeout,
            verify=False,  # noqa: S501 -- tracker artwork links may use hosts with invalid TLS.
        ) as session:
            response = await _fetch_image_response(session, url)
            return bool(response and _verified_image_content(response, url))
    except Exception as error:
        logger.info(
            f"[red]Session creation failed for: {url} - {error!s}[/red]"
        )
        return False


def _apply_unit3d_ids(
    meta: Meta, tmdb: Any, imdb: Any, tvdb: Any, mal: Any
) -> None:
    if tmdb:
        meta.tmdb_id = tmdb
        logger.debug(f"set TMDB ID: {meta.tmdb_id}")
    if imdb:
        meta.imdb_id = int(imdb)
        logger.debug(f"set IMDB ID: {meta.imdb_id}")
    if tvdb:
        meta.tvdb_id = tvdb
        logger.debug(f"set TVDB ID: {meta.tvdb_id}")
    if mal:
        meta.mal_id = mal
        logger.debug(f"set MAL ID: {meta.mal_id}")


def _unit3d_candidate(
    meta: Meta,
    tracker_name: str,
    desc: Any,
    filename: Any,
    imagelist: Any,
) -> DescriptionCandidate:
    tracker_id = meta.get_tracker_id(tracker_name) or ""
    raw_descriptions = getattr(meta, "tracker_description_raw", {}) or {}
    raw_description = str(raw_descriptions.get(tracker_name, desc))
    return DescriptionCandidate(
        source=tracker_name,
        release_id=tracker_id,
        release_name=str(filename or ""),
        raw_description=raw_description,
        cleaned_description=str(desc),
        image_count=len(imagelist or []),
        score=score_release_name(
            getattr(meta, "tracker_search_term", ""),
            filename,
            explicit_id=bool(tracker_id),
        ),
    )


def _apply_unit3d_description(
    meta: Meta,
    tracker_name: str,
    desc: Any,
    filename: Any,
    imagelist: Any,
    imports_text: bool,
) -> None:
    if not desc:
        return
    add_candidate(
        meta,
        _unit3d_candidate(meta, tracker_name, desc, filename, imagelist),
        selected=imports_text,
    )
    if imports_text:
        meta.description = desc
        meta.saved_description = True


def _unit3d_category(category: Any) -> str | None:
    if not category:
        return None
    value = str(category).upper()
    if "MOVIE" in value:
        return "MOVIE"
    if "TV" in value:
        return "TV"
    return None


def _apply_unit3d_category(meta: Meta, category: Any) -> None:
    if meta.manual_category:
        return
    resolved = _unit3d_category(category)
    if resolved is not None:
        meta.category = resolved
    if category:
        logger.debug(f"set Category: {meta.category}")


def _should_review_unit3d_images(meta: Meta) -> bool:
    if meta.unattended:
        return True
    return not bool(meta.tracker_ids)


async def _apply_unit3d_images(
    meta: Meta,
    tracker_name: str,
    imagelist: Any,
    imports_images: bool,
) -> None:
    if not imports_images or not imagelist:
        return
    typed = cast(list[ImageDict], imagelist)
    valid_images = await check_images_concurrently(typed, meta)
    if not valid_images:
        return
    meta.image_list = valid_images
    if _should_review_unit3d_images(meta):
        await handle_image_list(meta, tracker_name, valid_images)


def _finalize_unit3d_description(
    meta: Meta,
    tracker_name: str,
    desc: Any,
    filename: Any,
    imports_text: bool,
) -> None:
    if desc and imports_text:
        meta.description_fingerprint = description_fingerprint(
            meta, tracker_name
        )
    if filename:
        meta[f"{tracker_name.lower()}_filename"] = filename


async def update_meta_with_unit3d_data(
    meta: Meta,
    tracker_data: Sequence[Any],
    tracker_name: str,
    _skip_tracker_descriptions: bool = False,
) -> bool:
    (
        tmdb,
        imdb,
        tvdb,
        mal,
        desc,
        category,
        _infohash,
        imagelist,
        filename,
        *_rest,
    ) = tracker_data
    mode = resolve_description_mode(meta.tracker_description_mode)
    _apply_unit3d_ids(meta, tmdb, imdb, tvdb, mal)
    _apply_unit3d_description(
        meta, tracker_name, desc, filename, imagelist, mode.imports_text
    )
    _apply_unit3d_category(meta, category)
    await _apply_unit3d_images(
        meta, tracker_name, imagelist, mode.imports_images
    )
    _finalize_unit3d_description(
        meta, tracker_name, desc, filename, mode.imports_text
    )
    logger.debug(
        f"[green]{tracker_name} data successfully updated in meta[/green]"
    )
    return True


async def _tracker_description_images(
    tracker_instance: Any,
    torrent_id: int,
    meta: Meta,
    skip_tracker_descriptions: bool,
) -> list[ImageDict]:
    if skip_tracker_descriptions and not meta.keep_images:
        return []
    return cast(
        list[ImageDict],
        await tracker_instance.get_ptp_description(
            torrent_id, meta, meta.is_disc
        ),
    )


async def _apply_ptp_images(
    meta: Meta,
    tracker_name: str,
    images: list[ImageDict],
    *,
    review: bool,
) -> None:
    if not images:
        return
    valid_images = await check_images_concurrently(images, meta)
    if not valid_images:
        return
    meta.image_list = valid_images
    if review:
        await handle_image_list(meta, tracker_name, valid_images)


def _discard_ptp_match(meta: Meta, tracker_name: str) -> None:
    meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
    meta.clear_tracker_id(tracker_name)
    meta.description = ""
    meta.image_list = []


async def _accept_ptp_discovered(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    imdb_id: int,
    torrent_id: int,
    skip_tracker_descriptions: bool,
    *,
    record_tracker_id: bool,
    review_images: bool,
) -> bool:
    meta.imdb_id = imdb_id
    if record_tracker_id:
        meta.set_tracker_ids({tracker_name: torrent_id})
    images = await _tracker_description_images(
        tracker_instance, torrent_id, meta, skip_tracker_descriptions
    )
    await _apply_ptp_images(meta, tracker_name, images, review=review_images)
    return True


async def _handle_ptp_discovered(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    imdb_id: int,
    torrent_id: int,
    skip_tracker_descriptions: bool,
) -> bool:
    if imdb_id:
        logger.info(
            f"[green]{tracker_name} IMDb ID found: tt{str(imdb_id).zfill(7)}[/green]"
        )
    if meta.unattended:
        return await _accept_ptp_discovered(
            tracker_name,
            tracker_instance,
            meta,
            imdb_id,
            torrent_id,
            skip_tracker_descriptions,
            record_tracker_id=False,
            review_images=False,
        )
    accepted = await prompt_user_for_confirmation(
        "Do you want to use this ID data from PASSTHEPOPCORN?"
    )
    if not accepted:
        _discard_ptp_match(meta, tracker_name)
        return False
    return await _accept_ptp_discovered(
        tracker_name,
        tracker_instance,
        meta,
        imdb_id,
        torrent_id,
        skip_tracker_descriptions,
        record_tracker_id=True,
        review_images=True,
    )


async def _ptp_from_existing_id(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    torrent_id: str,
    skip_tracker_descriptions: bool,
) -> bool:
    torrent_id_int = int(torrent_id)
    result = await tracker_instance.get_imdb_from_torrent_id(torrent_id_int)
    imdb_id, meta.ext_torrenthash = cast(tuple[int, str | None], result)
    if not imdb_id:
        logger.info(
            f"[yellow]Could not find IMDb ID using PASSTHEPOPCORN ID: {torrent_id_int}[/yellow]"
        )
        return False
    meta.imdb_id = imdb_id
    meta.skipit = True
    logger.debug(
        f"[green]IMDb ID found: tt{str(meta.imdb_id).zfill(7)}[/green]"
    )
    images = await _tracker_description_images(
        tracker_instance, torrent_id_int, meta, skip_tracker_descriptions
    )
    await _apply_ptp_images(meta, tracker_name, images, review=False)
    if meta.image_list:
        logger.info("[green]PASSTHEPOPCORN images added to metadata.[/green]")
    return True


async def _update_from_ptp(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    search_file_folder: str,
    skip_tracker_descriptions: bool,
) -> bool:
    existing_id = meta.get_tracker_id(tracker_name)
    if existing_id is not None:
        return await _ptp_from_existing_id(
            tracker_name,
            tracker_instance,
            meta,
            existing_id,
            skip_tracker_descriptions,
        )
    result = await tracker_instance.get_ptp_id_imdb(
        search_term, search_file_folder, meta
    )
    imdb_id, torrent_id, meta.ext_torrenthash = cast(
        tuple[int, int | None, str | None], result
    )
    if not torrent_id:
        logger.debug(
            "[yellow]Skipping PASSTHEPOPCORN as no match found[/yellow]"
        )
        return False
    return await _handle_ptp_discovered(
        tracker_name,
        tracker_instance,
        meta,
        imdb_id,
        torrent_id,
        skip_tracker_descriptions,
    )


async def _api_tracker_data(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    skip_tracker_descriptions: bool,
    resolved_torrent_id: str | None,
) -> Sequence[Any]:
    common = Common(config)
    if resolved_torrent_id:
        logger.debug(
            f"[cyan]{tracker_name} ID found in meta, reusing existing ID: {resolved_torrent_id}[/cyan]"
        )
        return cast(
            Sequence[Any],
            await common.unit3d_torrent_info(
                tracker_name,
                tracker_instance.id_url,
                tracker_instance.search_url,
                meta,
                id=resolved_torrent_id,
                skip_tracker_descriptions=skip_tracker_descriptions,
                public_torrent_url=tracker_instance.torrent_url,
            ),
        )
    logger.debug(
        f"[yellow]No ID found in meta for {tracker_name}, searching by file name[/yellow]"
    )
    return cast(
        Sequence[Any],
        await common.unit3d_torrent_info(
            tracker_name,
            tracker_instance.id_url,
            tracker_instance.search_url,
            meta,
            file_name=search_term,
            skip_tracker_descriptions=skip_tracker_descriptions,
        ),
    )


def _has_api_tracker_ids(tracker_data: Sequence[Any]) -> bool:
    return any(item not in [None, 0] for item in tracker_data[:3])


async def _update_from_api_tracker(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    skip_tracker_descriptions: bool,
    torrent_id: str,
) -> bool:
    resolved_torrent_id = torrent_id or meta.get_tracker_id(tracker_name)
    tracker_data = await _api_tracker_data(
        tracker_name,
        tracker_instance,
        meta,
        search_term,
        skip_tracker_descriptions,
        resolved_torrent_id,
    )
    if not _has_api_tracker_ids(tracker_data):
        logger.debug(f"[yellow]No valid data found on {tracker_name}[/yellow]")
        return False
    logger.debug(f"[green]Valid data found on {tracker_name}[/green]")
    return await update_meta_with_unit3d_data(
        meta, tracker_data, tracker_name, skip_tracker_descriptions
    )


def _valid_bhd_credential(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) < 25:
        return None
    return value


def _bhd_credentials() -> tuple[str, str] | None:
    trackers_cfg = cast(Mapping[str, Any], config.get("TRACKERS", {}))
    tracker_cfg = cast(dict[str, Any], trackers_cfg.get("BEYONDHD", {}))
    api_key = _valid_bhd_credential(tracker_cfg.get("api_key"))
    rss_key = _valid_bhd_credential(tracker_cfg.get("bhd_rss_key"))
    if api_key is None or rss_key is None:
        return None
    return api_key, rss_key


def _bhd_uses_folder(meta: Meta) -> bool:
    if bool(meta.is_disc):
        return True
    if meta.keep_folder is True:
        return True
    return meta.isdir is True


def _bhd_foldername(meta: Meta) -> str:
    if meta.path:
        return Path(meta.path).name
    return meta.uuid


def _bhd_filename(meta: Meta) -> str | None:
    filelist = cast(list[str], meta.filelist or [])
    if not filelist:
        return None
    return Path(filelist[0]).name


async def _bhd_lookup(
    api_key: str,
    rss_key: str,
    meta: Meta,
    skip_tracker_descriptions: bool,
    tracker_name: str,
) -> tuple[int | None, int | None]:
    torrent_id = meta.get_tracker_id(tracker_name)
    kwargs: dict[str, Any] = {
        "skip_tracker_descriptions": skip_tracker_descriptions
    }
    if torrent_id:
        kwargs["torrent_id"] = int(torrent_id)
    elif _bhd_uses_folder(meta):
        kwargs["foldername"] = _bhd_foldername(meta)
    else:
        kwargs["filename"] = _bhd_filename(meta)
    return cast(
        tuple[int | None, int | None],
        await BtnIdManager.get_bhd_torrents(api_key, rss_key, meta, **kwargs),
    )


def _bhd_has_ids(imdb: Any, tmdb: Any) -> bool:
    return to_int(imdb) != 0 or to_int(tmdb) != 0


def _apply_bhd_ids(meta: Meta, imdb: Any, tmdb: Any) -> None:
    meta.imdb_id = to_int(imdb, to_int(meta.imdb_id))
    meta.tmdb_id = to_int(tmdb, to_int(meta.tmdb_id))


def _bhd_description_value(meta: Meta) -> str | None:
    value = meta.description
    if not isinstance(value, str):
        return None
    if not value:
        return None
    return value


def _bhd_description_choice() -> str:
    value = cli_ui.ask_string(
        "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
    )
    return (value or "").lower()


def _apply_bhd_description_edit(meta: Meta, description: str) -> None:
    edited = str(click.edit(text=description) or "")
    if edited:
        meta.description = edited.strip()
        meta.saved_description = True
    logger.info(
        f"[green]Final description after editing:[/green] {meta.description}",
        extra={"markup": False},
    )


def _discard_bhd_description(meta: Meta) -> None:
    meta.description = ""
    meta.image_list = []
    logger.info("[yellow]Description discarded.[/yellow]")


def _keep_bhd_description(meta: Meta, description: str) -> None:
    logger.info("[green]Keeping the original description.[/green]")
    meta.description = description
    meta.saved_description = True


def _edit_bhd_description(meta: Meta, description: str) -> None:
    logger.info(
        "[cyan]Do you want to edit, discard or keep the description?[/cyan]"
    )
    choice = _bhd_description_choice()
    if choice == "e":
        _apply_bhd_description_edit(meta, description)
        return
    if choice == "d":
        _discard_bhd_description(meta)
        return
    _keep_bhd_description(meta, description)


def _accept_bhd_description(meta: Meta, description: str) -> None:
    logger.info("[bold green]Successfully grabbed description from BEYONDHD")
    logger.info(
        f"Description after cleaning:\n{description[:1000]}...",
        extra={"markup": False},
    )
    if meta.skipit:
        meta.description = description
        meta.saved_description = True
        return
    _edit_bhd_description(meta, description)


def _bhd_nfo_path(meta: Meta) -> Path:
    return Path(meta.base_dir) / "tmp" / meta.uuid / "bhd.nfo"


async def _delete_bhd_nfo_with_retries(path: Path) -> None:
    try:
        import gc

        gc.collect()
        for attempt in range(3):
            try:
                path.unlink()
                logger.info("[yellow]NFO file successfully deleted.[/yellow]")
                return
            except Exception as error:
                if attempt < 2:
                    logger.info(
                        f"[yellow]Attempt {attempt + 1}: Could not delete file, retrying in 1 second...[/yellow]"
                    )
                    await asyncio.sleep(1)
                else:
                    logger.error(
                        f"[red]Failed to delete BEYONDHD NFO file after 3 attempts: {error}[/red]"
                    )
    except Exception as error:
        logger.error(f"[red]Error during file cleanup: {error}[/red]")


def _reviewable_bhd_nfo(meta: Meta) -> Path | None:
    if not meta.bhd_nfo or meta.skipit:
        return None
    path = _bhd_nfo_path(meta)
    return path if path.exists() else None


def _bhd_nfo_choice() -> str:
    value = cli_ui.ask_string(
        "Enter 'd' to discard, or press Enter to keep it as is: "
    )
    return (value or "").lower()


async def _review_bhd_nfo(meta: Meta) -> None:
    path = _reviewable_bhd_nfo(meta)
    if path is None:
        return
    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    logger.info("[bold green]Successfully grabbed FraMeSToR description")
    logger.info(
        f"Description content:\n{content[:1000]}...", extra={"markup": False}
    )
    logger.info("[cyan]Do you want to discard or keep the description?[/cyan]")
    if _bhd_nfo_choice() != "d":
        logger.info("[green]Keeping the original description.[/green]")
        return
    meta.description = ""
    meta.image_list = []
    await _delete_bhd_nfo_with_retries(path)
    meta.nfo = False
    meta.bhd_nfo = False
    logger.info("[yellow]Description discarded.[/yellow]")


async def _review_bhd_content(meta: Meta) -> None:
    description = _bhd_description_value(meta)
    if description is not None:
        _accept_bhd_description(meta, description)
        return
    await _review_bhd_nfo(meta)


async def _validate_bhd_images(
    meta: Meta, tracker_name: str, *, review: bool
) -> None:
    image_list = cast(Sequence[ImageDict] | None, meta.image_list)
    if not image_list:
        return
    valid_images = await check_images_concurrently(image_list, meta)
    if not valid_images:
        meta.image_list = []
        return
    meta.image_list = valid_images
    if review:
        await handle_image_list(meta, tracker_name, valid_images)


def _delete_declined_bhd_nfo(meta: Meta) -> None:
    path = _bhd_nfo_path(meta)
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception as error:
        logger.error(f"[red]Failed to delete BEYONDHD NFO file: {error}[/red]")


def _discard_bhd_match(meta: Meta, tracker_name: str) -> None:
    logger.info(f"[yellow]{tracker_name} data discarded.[/yellow]")
    meta.clear_tracker_id(tracker_name)
    meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
    meta.tmdb_id = meta.tmdb_id if meta.tmdb_id else 0
    meta.framestor = False
    meta.flux = False
    meta.description = ""
    meta.image_list = []
    meta.nfo = False
    meta.bhd_nfo = False
    _delete_declined_bhd_nfo(meta)


async def _accept_bhd_interactive(
    tracker_name: str, meta: Meta, imdb: Any, tmdb: Any
) -> bool:
    _apply_bhd_ids(meta, imdb, tmdb)
    await _review_bhd_content(meta)
    await _validate_bhd_images(meta, tracker_name, review=True)
    return True


async def _handle_bhd_interactive(
    tracker_name: str, meta: Meta, imdb: Any, tmdb: Any
) -> bool:
    logger.info(
        f"[green]{tracker_name} data found: IMDb ID: {imdb}, TMDb ID: {tmdb}[/green]"
    )
    accepted = await prompt_user_for_confirmation(
        f"Do you want to use the ID's found on {tracker_name}?"
    )
    if not accepted:
        _discard_bhd_match(meta, tracker_name)
        return False
    return await _accept_bhd_interactive(tracker_name, meta, imdb, tmdb)


async def _handle_bhd_unattended(
    tracker_name: str, meta: Meta, imdb: Any, tmdb: Any
) -> bool:
    _apply_bhd_ids(meta, imdb, tmdb)
    if not _bhd_has_ids(meta.imdb_id, meta.tmdb_id):
        return False
    logger.info(
        f"[green]{tracker_name} data found: IMDb ID: {meta.imdb_id}, TMDb ID: {meta.tmdb_id}[/green]"
    )
    await _validate_bhd_images(meta, tracker_name, review=False)
    return True


async def _update_from_bhd(
    tracker_name: str, meta: Meta, skip_tracker_descriptions: bool
) -> bool:
    credentials = _bhd_credentials()
    if credentials is None:
        logger.info(
            "[red]BEYONDHD API or RSS key not found. Please check your configuration.[/red]"
        )
        return False
    imdb, tmdb = await _bhd_lookup(
        *credentials,
        meta,
        skip_tracker_descriptions,
        tracker_name,
    )
    if not _bhd_has_ids(imdb, tmdb):
        logger.debug(
            f"[yellow]{tracker_name} returned invalid IDs (both 0)[/yellow]"
        )
        return False
    if meta.unattended:
        return await _handle_bhd_unattended(tracker_name, meta, imdb, tmdb)
    return await _handle_bhd_interactive(tracker_name, meta, imdb, tmdb)


def _hdb_has_data(imdb: Any, tvdb_id: Any, description: Any) -> bool:
    if imdb:
        return True
    if tvdb_id:
        return True
    return bool(description)


def _apply_hdb_ids(
    meta: Meta, imdb: int | None, tvdb_id: int | None, hdb_name: str | None
) -> None:
    if imdb:
        meta.imdb_id = imdb
    if tvdb_id:
        meta.tvdb_id = tvdb_id
    meta.hdb_name = hdb_name


def _clean_hdb_payload(meta: Meta) -> tuple[str | None, list[ImageDict]]:
    description_source = meta.hdb_description or ""
    return cast(
        tuple[str | None, list[ImageDict]],
        BBCODE().clean_hdb_description(description_source),
    )


def _apply_hdb_description_simple(
    meta: Meta,
    description: str | None,
    skip_tracker_descriptions: bool,
    *,
    prefix: str = "Description content",
    empty_log: bool,
) -> None:
    if description and not skip_tracker_descriptions:
        logger.info(
            f"{prefix}:\n{description[:500]}...", extra={"markup": False}
        )
        meta.description = description
        meta.saved_description = True
        return
    if empty_log:
        logger.info("[yellow]HDBITS description empty[/yellow]")


def _hdb_edit_choice() -> str:
    raw = cli_ui.ask_string(
        "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
    )
    return (raw or "").strip().lower()


def _apply_hdb_edited_description(meta: Meta, description: str) -> None:
    edited = str(click.edit(text=description) or "")
    if edited:
        description = edited.strip()
        meta.description = description
        meta.saved_description = True
    logger.info(
        f"[green]Final description after editing:[/green] {description}",
        extra={"markup": False},
    )


def _review_hdb_description(
    meta: Meta, description: str | None, skip_tracker_descriptions: bool
) -> None:
    if not description or skip_tracker_descriptions:
        logger.info("[yellow]HDBITS description empty[/yellow]")
        return
    logger.info("[bold green]Successfully grabbed description from HDBITS")
    logger.info(
        f"HDBITS Description content:\n{description[:1000]}.....",
        extra={"markup": False},
    )
    logger.info(
        "[cyan]Do you want to edit, discard or keep the description?[/cyan]"
    )
    choice = _hdb_edit_choice()
    if choice == "e":
        _apply_hdb_edited_description(meta, description)
        return
    if choice == "d":
        meta.hdb_description = ""
        logger.info("[yellow]Description discarded.[/yellow]")
        return
    logger.info("[green]Keeping the original description.[/green]")
    meta.description = description
    meta.saved_description = True


async def _apply_hdb_images(
    meta: Meta,
    tracker_name: str,
    image_list: list[ImageDict],
    *,
    clear_when_disabled: bool,
) -> None:
    if not image_list or not meta.keep_images:
        if clear_when_disabled:
            meta.image_list = []
        return
    valid_images = await check_images_concurrently(image_list, meta)
    if not valid_images:
        return
    meta.image_list = valid_images
    await handle_image_list(meta, tracker_name, valid_images)


def _log_hdb_found(
    tracker_name: str, imdb: Any, meta: Meta, hdb_name: Any
) -> None:
    logger.info(
        f"[green]{tracker_name} data found: IMDb ID: {imdb}, TVDb ID: {meta.tvdb_id}, HDBITS Name: {hdb_name}[/green]"
    )


async def _hdb_existing_result(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    torrent_id: str,
    skip_tracker_descriptions: bool,
) -> bool:
    meta["hdb_manual"] = torrent_id
    logger.info(
        f"[cyan]{tracker_name} ID found in meta, reusing existing ID: {torrent_id}[/cyan]"
    )
    hdb_info = await tracker_instance.get_info_from_torrent_id(torrent_id)
    (
        imdb,
        tvdb_id,
        hdb_name,
        meta.ext_torrenthash,
        meta.hdb_description,
    ) = cast(
        tuple[int | None, int | None, str | None, str | None, str | None],
        hdb_info,
    )
    if not _hdb_has_data(imdb, tvdb_id, meta.hdb_description):
        logger.info(
            f"[yellow]{tracker_name} data not found for ID: {torrent_id}[/yellow]"
        )
        return False
    _apply_hdb_ids(meta, imdb, tvdb_id, hdb_name)
    description, image_list = _clean_hdb_payload(meta)
    _apply_hdb_description_simple(
        meta,
        description,
        skip_tracker_descriptions,
        empty_log=True,
    )
    await _apply_hdb_images(
        meta, tracker_name, image_list, clear_when_disabled=True
    )
    _log_hdb_found(tracker_name, imdb, meta, meta.hdb_name)
    return True


def _discard_hdb_search(meta: Meta, tracker_name: str) -> None:
    logger.info(f"[yellow]{tracker_name} data discarded.[/yellow]")
    meta.clear_tracker_id(tracker_name)
    meta.tvdb_id = meta.tvdb_id if meta.tvdb_id else 0
    meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
    meta.hdb_name = None
    meta.hdb_description = ""


async def _accept_hdb_search(
    tracker_name: str,
    meta: Meta,
    imdb: int | None,
    tvdb_id: int | None,
    hdb_name: str | None,
    skip_tracker_descriptions: bool,
) -> bool:
    logger.info(f"[green]{tracker_name} data retained.[/green]")
    _apply_hdb_ids(meta, imdb, tvdb_id, hdb_name)
    description, image_list = _clean_hdb_payload(meta)
    _review_hdb_description(meta, description, skip_tracker_descriptions)
    await _apply_hdb_images(
        meta, tracker_name, image_list, clear_when_disabled=False
    )
    return True


async def _interactive_hdb_search(
    tracker_name: str,
    meta: Meta,
    imdb: int | None,
    tvdb_id: int | None,
    hdb_name: str | None,
    skip_tracker_descriptions: bool,
) -> bool:
    _log_hdb_found(tracker_name, imdb, meta, hdb_name)
    accepted = await prompt_user_for_confirmation(
        f"Do you want to use the ID's found on {tracker_name}?"
    )
    if not accepted:
        _discard_hdb_search(meta, tracker_name)
        return False
    return await _accept_hdb_search(
        tracker_name,
        meta,
        imdb,
        tvdb_id,
        hdb_name,
        skip_tracker_descriptions,
    )


async def _unattended_hdb_search(
    tracker_name: str,
    meta: Meta,
    imdb: int | None,
    tvdb_id: int | None,
    hdb_name: str | None,
    skip_tracker_descriptions: bool,
) -> bool:
    _apply_hdb_ids(meta, imdb, tvdb_id, hdb_name)
    description, image_list = _clean_hdb_payload(meta)
    _apply_hdb_description_simple(
        meta,
        description,
        skip_tracker_descriptions,
        prefix="HDBITS Description content",
        empty_log=False,
    )
    await _apply_hdb_images(
        meta, tracker_name, image_list, clear_when_disabled=False
    )
    _log_hdb_found(tracker_name, imdb, meta, hdb_name)
    return True


async def _hdb_search_result(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    search_file_folder: str,
    skip_tracker_descriptions: bool,
) -> bool:
    logger.debug(
        "[yellow]No ID found in meta for HDBITS, searching by file name[/yellow]"
    )
    result = await tracker_instance.search_filename(
        search_term, search_file_folder, meta
    )
    (
        imdb,
        tvdb_id,
        hdb_name,
        meta.ext_torrenthash,
        meta.hdb_description,
        tracker_id,
    ) = cast(
        tuple[
            int | None,
            int | None,
            str | None,
            str | None,
            str | None,
            int | None,
        ],
        result,
    )
    meta.hdb_name = hdb_name
    if tracker_id:
        meta.set_tracker_ids({tracker_name: tracker_id})
    if not _hdb_has_data(imdb, tvdb_id, meta.hdb_description):
        meta.hdb_name = None
        meta.hdb_description = ""
        meta.clear_tracker_id(tracker_name)
        return False
    if meta.unattended:
        return await _unattended_hdb_search(
            tracker_name,
            meta,
            imdb,
            tvdb_id,
            hdb_name,
            skip_tracker_descriptions,
        )
    return await _interactive_hdb_search(
        tracker_name,
        meta,
        imdb,
        tvdb_id,
        hdb_name,
        skip_tracker_descriptions,
    )


async def _update_from_hdbits(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    search_file_folder: str,
    skip_tracker_descriptions: bool,
) -> bool:
    existing_id = meta.get_tracker_id(tracker_name)
    if existing_id is not None:
        return await _hdb_existing_result(
            tracker_name,
            tracker_instance,
            meta,
            existing_id,
            skip_tracker_descriptions,
        )
    return await _hdb_search_result(
        tracker_name,
        tracker_instance,
        meta,
        search_term,
        search_file_folder,
        skip_tracker_descriptions,
    )


async def update_metadata_from_tracker(
    tracker_name: str,
    tracker_instance: Any,
    meta: Meta,
    search_term: str,
    search_file_folder: str,
    skip_tracker_descriptions: bool = False,
    *,
    torrent_id: str = "",
) -> tuple[Meta, bool]:
    meta.tracker_search_term = search_term
    found_match = False

    if tracker_name == "PASSTHEPOPCORN":
        found_match = await _update_from_ptp(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
        )

    elif tracker_name == "BEYONDHD":
        found_match = await _update_from_bhd(
            tracker_name, meta, skip_tracker_descriptions
        )

    elif tracker_name in api_trackers:
        found_match = await _update_from_api_tracker(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            skip_tracker_descriptions,
            torrent_id,
        )

    elif tracker_name == "HDBITS":
        found_match = await _update_from_hdbits(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
        )

    return meta, found_match


def _log_selected_images(
    meta: Meta,
    tracker_name: str,
    valid_images: Sequence[ImageDict] | None,
) -> None:
    valid_count = len(valid_images) if valid_images is not None else 0
    logger.info(
        f"[cyan]Selected the following {valid_count} valid images from {tracker_name}:"
    )
    for image in meta.image_list:
        logger.info(f"Image:[green]'{image.get('img_url')}'[/green]")


async def _should_keep_images(meta: Meta, tracker_name: str) -> bool:
    if meta.unattended:
        return True
    return await prompt_user_for_confirmation(
        f"Do you want to keep the images found on {tracker_name}?"
    )


def _delete_saved_images(meta: Meta) -> None:
    save_path = screenshots_dir(meta.base_dir, meta.uuid)
    try:
        png_files = list(Path(save_path).glob("*.png"))
        for png_file in png_files:
            png_file.unlink()
        if png_files:
            logger.info(
                f"[yellow]Successfully deleted {len(png_files)} image files.[/yellow]"
            )
        else:
            logger.info("[yellow]No image files found to delete.[/yellow]")
    except Exception as error:
        logger.error(f"[red]Failed to delete image files: {error}[/red]")


def _discard_images(meta: Meta, tracker_name: str) -> None:
    meta.image_list = []
    meta.image_sizes = {}
    _delete_saved_images(meta)
    logger.info(f"[yellow]Images discarded from {tracker_name}.")


async def handle_image_list(
    meta: Meta,
    tracker_name: str,
    valid_images: Sequence[ImageDict] | None = None,
) -> None:
    if not meta.image_list:
        return
    _log_selected_images(meta, tracker_name, valid_images)
    if await _should_keep_images(meta, tracker_name):
        logger.info(f"[green]Images retained from {tracker_name}.")
        return
    _discard_images(meta, tracker_name)
