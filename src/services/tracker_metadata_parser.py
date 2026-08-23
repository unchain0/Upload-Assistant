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
    tracker_key = "hdb" if tracker_name == "HDBITS" else tracker_name.lower()
    meta.tracker_search_term = search_term
    manual_key = f"{tracker_key}_manual"
    found_match = False

    if tracker_name == "PASSTHEPOPCORN":
        imdb_id: int = 0
        ptp_imagelist: list[ImageDict] = []
        ptp_torrent_id = meta.get_tracker_id(tracker_name)
        if ptp_torrent_id is None:
            ptp_result = await tracker_instance.get_ptp_id_imdb(
                search_term, search_file_folder, meta
            )
            imdb_id, ptp_torrent_id, meta.ext_torrenthash = cast(
                tuple[int, int | None, str | None], ptp_result
            )
            if ptp_torrent_id:
                if imdb_id:
                    logger.info(
                        f"[green]{tracker_name} IMDb ID found: tt{str(imdb_id).zfill(7)}[/green]"
                    )

                if not meta.unattended:
                    if await prompt_user_for_confirmation(
                        "Do you want to use this ID data from PASSTHEPOPCORN?"
                    ):
                        meta.imdb_id = imdb_id
                        found_match = True
                        meta.set_tracker_ids({tracker_name: ptp_torrent_id})

                        if not skip_tracker_descriptions or meta.keep_images:
                            ptp_imagelist = cast(
                                list[ImageDict],
                                await tracker_instance.get_ptp_description(
                                    ptp_torrent_id, meta, meta.is_disc
                                ),
                            )
                        if ptp_imagelist:
                            valid_images = await check_images_concurrently(
                                ptp_imagelist, meta
                            )
                            if valid_images:
                                meta.image_list = valid_images
                                await handle_image_list(
                                    meta, tracker_name, valid_images
                                )

                    else:
                        found_match = False
                        meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
                        meta.clear_tracker_id(tracker_name)
                        meta.description = ""
                        meta.image_list = []

                else:
                    found_match = True
                    meta.imdb_id = imdb_id
                    if not skip_tracker_descriptions or meta.keep_images:
                        ptp_imagelist = cast(
                            list[ImageDict],
                            await tracker_instance.get_ptp_description(
                                ptp_torrent_id, meta, meta.is_disc
                            ),
                        )
                    if ptp_imagelist:
                        valid_images = await check_images_concurrently(
                            ptp_imagelist, meta
                        )
                        if valid_images:
                            meta.image_list = valid_images
            else:
                logger.debug(
                    "[yellow]Skipping PASSTHEPOPCORN as no match found[/yellow]"
                )
                found_match = False

        else:
            ptp_torrent_id_int = int(ptp_torrent_id)
            ptp_imdb_result = await tracker_instance.get_imdb_from_torrent_id(
                ptp_torrent_id_int
            )
            imdb_id, meta.ext_torrenthash = cast(
                tuple[int, str | None], ptp_imdb_result
            )
            if imdb_id:
                meta.imdb_id = imdb_id
                logger.debug(
                    f"[green]IMDb ID found: tt{str(meta.imdb_id).zfill(7)}[/green]"
                )
                found_match = True
                meta.skipit = True
                if not skip_tracker_descriptions or meta.keep_images:
                    ptp_imagelist = cast(
                        list[ImageDict],
                        await tracker_instance.get_ptp_description(
                            ptp_torrent_id_int, meta, meta.is_disc
                        ),
                    )
                if ptp_imagelist:
                    valid_images = await check_images_concurrently(
                        ptp_imagelist, meta
                    )
                    if valid_images:
                        meta.image_list = valid_images
                        logger.info(
                            "[green]PASSTHEPOPCORN images added to metadata.[/green]"
                        )
            else:
                logger.info(
                    f"[yellow]Could not find IMDb ID using PASSTHEPOPCORN ID: {ptp_torrent_id_int}[/yellow]"
                )
                found_match = False

    elif tracker_name == "BEYONDHD":
        trackers_cfg = cast(Mapping[str, Any], config.get("TRACKERS", {}))
        tracker_cfg = cast(dict[str, Any], trackers_cfg.get("BEYONDHD", {}))
        bhd_api = tracker_cfg.get("api_key")
        bhd_api = bhd_api if isinstance(bhd_api, str) else None
        if bhd_api and len(bhd_api) < 25:
            bhd_api = None

        bhd_rss_key = tracker_cfg.get("bhd_rss_key")
        bhd_rss_key = bhd_rss_key if isinstance(bhd_rss_key, str) else None
        if bhd_rss_key and len(bhd_rss_key) < 25:
            bhd_rss_key = None

        if not bhd_api or not bhd_rss_key:
            logger.info(
                "[red]BEYONDHD API or RSS key not found. Please check your configuration.[/red]"
            )
            return meta, False
        use_foldername = (
            bool(meta.is_disc)
            or meta.keep_folder is True
            or meta.isdir is True
        )

        bhd_torrent_id = meta.get_tracker_id(tracker_name)
        if bhd_torrent_id:
            imdb, tmdb = cast(
                tuple[int | None, int | None],
                await BtnIdManager.get_bhd_torrents(
                    bhd_api,
                    bhd_rss_key,
                    meta,
                    skip_tracker_descriptions=skip_tracker_descriptions,
                    torrent_id=int(bhd_torrent_id),
                ),
            )
        elif use_foldername:
            # Use folder name from path if available, fall back to UUID
            folder_path = meta.path
            foldername = Path(folder_path).name if folder_path else meta.uuid
            imdb, tmdb = cast(
                tuple[int | None, int | None],
                await BtnIdManager.get_bhd_torrents(
                    bhd_api,
                    bhd_rss_key,
                    meta,
                    skip_tracker_descriptions=skip_tracker_descriptions,
                    foldername=foldername,
                ),
            )
        else:
            # Only use filename if none of the folder conditions are met
            filelist = cast(list[str], meta.filelist or [])
            filename = Path(filelist[0]).name if filelist else None
            imdb, tmdb = cast(
                tuple[int | None, int | None],
                await BtnIdManager.get_bhd_torrents(
                    bhd_api,
                    bhd_rss_key,
                    meta,
                    skip_tracker_descriptions=skip_tracker_descriptions,
                    filename=filename,
                ),
            )

        if to_int(imdb) != 0 or to_int(tmdb) != 0:
            if not meta.unattended:
                logger.info(
                    f"[green]{tracker_name} data found: IMDb ID: {imdb}, TMDb ID: {tmdb}[/green]"
                )
                if await prompt_user_for_confirmation(
                    f"Do you want to use the ID's found on {tracker_name}?"
                ):
                    found_match = True
                    meta.imdb_id = to_int(imdb, to_int(meta.imdb_id))
                    meta.tmdb_id = to_int(tmdb, to_int(meta.tmdb_id))
                    description_value = meta.description
                    if (
                        isinstance(description_value, str)
                        and description_value
                    ):
                        description = description_value
                        logger.info(
                            "[bold green]Successfully grabbed description from BEYONDHD"
                        )
                        logger.info(
                            f"Description after cleaning:\n{description[:1000]}...",
                            extra={"markup": False},
                        )

                        if not meta.skipit:
                            logger.info(
                                "[cyan]Do you want to edit, discard or keep the description?[/cyan]"
                            )
                            edit_choice = cli_ui.ask_string(
                                "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
                            )

                            if (edit_choice or "").lower() == "e":
                                # pyrefly: ignore [bad-argument-type]
                                edited_description = str(
                                    click.edit(text=description) or ""
                                )
                                if edited_description:
                                    desc = edited_description.strip()
                                    meta.description = desc
                                    meta.saved_description = True
                                logger.info(
                                    f"[green]Final description after editing:[/green] {meta.description}",
                                    extra={"markup": False},
                                )
                            elif (edit_choice or "").lower() == "d":
                                meta.description = ""
                                meta.image_list = []
                                logger.info(
                                    "[yellow]Description discarded.[/yellow]"
                                )
                            else:
                                logger.info(
                                    "[green]Keeping the original description.[/green]"
                                )
                                meta.description = description
                                meta.saved_description = True
                        else:
                            meta.description = description
                            meta.saved_description = True
                    elif meta.bhd_nfo:
                        if not meta.skipit:
                            nfo_file_path = (
                                Path(meta.base_dir)
                                / "tmp"
                                / meta.uuid
                                / "bhd.nfo"
                            )
                            if Path(nfo_file_path).exists():
                                nfo_content = await asyncio.to_thread(
                                    Path(nfo_file_path).read_text,
                                    encoding="utf-8",
                                )
                                logger.info(
                                    "[bold green]Successfully grabbed FraMeSToR description"
                                )
                                logger.info(
                                    f"Description content:\n{nfo_content[:1000]}...",
                                    extra={"markup": False},
                                )
                                logger.info(
                                    "[cyan]Do you want to discard or keep the description?[/cyan]"
                                )
                                edit_choice = cli_ui.ask_string(
                                    "Enter 'd' to discard, or press Enter to keep it as is: "
                                )

                                if (edit_choice or "").lower() == "d":
                                    meta.description = ""
                                    meta.image_list = []
                                    nfo_file_path = (
                                        Path(meta.base_dir)
                                        / "tmp"
                                        / meta.uuid
                                        / "bhd.nfo"
                                    )

                                    try:
                                        import gc

                                        gc.collect()  # Force garbage collection to close any lingering handles
                                        for attempt in range(3):
                                            try:
                                                nfo_file_path.unlink()
                                                logger.info(
                                                    "[yellow]NFO file successfully deleted.[/yellow]"
                                                )
                                                break
                                            except Exception as e:
                                                if attempt < 2:
                                                    logger.info(
                                                        f"[yellow]Attempt {attempt + 1}: Could not delete file, retrying in 1 second...[/yellow]"
                                                    )
                                                    await asyncio.sleep(1)
                                                else:
                                                    logger.error(
                                                        f"[red]Failed to delete BEYONDHD NFO file after 3 attempts: {e}[/red]"
                                                    )
                                    except Exception as e:
                                        logger.error(
                                            f"[red]Error during file cleanup: {e}[/red]"
                                        )
                                    meta.nfo = False
                                    meta.bhd_nfo = False
                                    logger.info(
                                        "[yellow]Description discarded.[/yellow]"
                                    )
                                else:
                                    logger.info(
                                        "[green]Keeping the original description.[/green]"
                                    )

                    image_list = cast(
                        Sequence[ImageDict] | None, meta.image_list
                    )
                    if image_list:
                        valid_images = await check_images_concurrently(
                            image_list, meta
                        )
                        if valid_images:
                            meta.image_list = valid_images
                            await handle_image_list(
                                meta, tracker_name, valid_images
                            )
                        else:
                            meta.image_list = []

                else:
                    logger.info(
                        f"[yellow]{tracker_name} data discarded.[/yellow]"
                    )
                    meta.clear_tracker_id(tracker_name)
                    meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
                    meta.tmdb_id = meta.tmdb_id if meta.tmdb_id else 0
                    meta.framestor = False
                    meta.flux = False
                    meta.description = ""
                    meta.image_list = []
                    meta.nfo = False
                    meta.bhd_nfo = False
                    save_path = Path(meta.base_dir) / "tmp" / meta.uuid
                    nfo_file_path = Path(save_path) / "bhd.nfo"
                    if Path(nfo_file_path).exists():
                        try:
                            nfo_file_path.unlink()
                        except Exception as e:
                            logger.error(
                                f"[red]Failed to delete BEYONDHD NFO file: {e}[/red]"
                            )
                    found_match = False
            else:
                # Only treat as match if we actually got valid IDs
                meta.imdb_id = to_int(imdb, to_int(meta.imdb_id))
                meta.tmdb_id = to_int(tmdb, to_int(meta.tmdb_id))
                if to_int(meta.imdb_id) != 0 or to_int(meta.tmdb_id) != 0:
                    logger.info(
                        f"[green]{tracker_name} data found: IMDb ID: {meta.imdb_id}, TMDb ID: {meta.tmdb_id}[/green]"
                    )
                    found_match = True
                    image_list = cast(
                        Sequence[ImageDict] | None, meta.image_list
                    )
                    if image_list:
                        valid_images = await check_images_concurrently(
                            image_list, meta
                        )
                        if valid_images:
                            meta.image_list = valid_images
                        else:
                            meta.image_list = []
        else:
            logger.debug(
                f"[yellow]{tracker_name} returned invalid IDs (both 0)[/yellow]"
            )
            found_match = False

    elif tracker_name in api_trackers:
        resolved_torrent_id = torrent_id or meta.get_tracker_id(tracker_name)
        if resolved_torrent_id:
            logger.debug(
                f"[cyan]{tracker_name} ID found in meta, reusing existing ID: {resolved_torrent_id}[/cyan]"
            )
            tracker_data = cast(
                Sequence[Any],
                await Common(config).unit3d_torrent_info(
                    tracker_name,
                    tracker_instance.id_url,
                    tracker_instance.search_url,
                    meta,
                    id=resolved_torrent_id,
                    skip_tracker_descriptions=skip_tracker_descriptions,
                    public_torrent_url=tracker_instance.torrent_url,
                ),
            )
        else:
            logger.debug(
                f"[yellow]No ID found in meta for {tracker_name}, searching by file name[/yellow]"
            )
            tracker_data = cast(
                Sequence[Any],
                await Common(config).unit3d_torrent_info(
                    tracker_name,
                    tracker_instance.id_url,
                    tracker_instance.search_url,
                    meta,
                    file_name=search_term,
                    skip_tracker_descriptions=skip_tracker_descriptions,
                ),
            )

        if any(
            item not in [None, 0] for item in tracker_data[:3]
        ):  # Check for valid tmdb, imdb, or tvdb
            logger.debug(f"[green]Valid data found on {tracker_name}[/green]")
            selected = await update_meta_with_unit3d_data(
                meta, tracker_data, tracker_name, skip_tracker_descriptions
            )
            found_match = selected
        else:
            logger.debug(
                f"[yellow]No valid data found on {tracker_name}[/yellow]"
            )
            found_match = False

    elif tracker_name == "HDBITS":
        bbcode = BBCODE()
        hdb_torrent_id = meta.get_tracker_id(tracker_name)
        if hdb_torrent_id is not None:
            meta[manual_key] = hdb_torrent_id
            logger.info(
                f"[cyan]{tracker_name} ID found in meta, reusing existing ID: {hdb_torrent_id}[/cyan]"
            )

            # Use get_info_from_torrent_id function if ID is found in meta
            hdb_info = await tracker_instance.get_info_from_torrent_id(
                hdb_torrent_id
            )
            (
                imdb,
                tvdb_id,
                hdb_name,
                meta.ext_torrenthash,
                meta.hdb_description,
            ) = cast(
                tuple[
                    int | None, int | None, str | None, str | None, str | None
                ],
                hdb_info,
            )

            if imdb or tvdb_id or meta.hdb_description:
                meta.imdb_id = imdb if imdb else meta.imdb_id
                meta.tvdb_id = tvdb_id if tvdb_id else meta.tvdb_id
                meta.hdb_name = hdb_name
                found_match = True
                description_source = meta.hdb_description or ""
                description, image_list = cast(
                    tuple[str | None, list[ImageDict]],
                    bbcode.clean_hdb_description(description_source),
                )
                if (
                    description
                    and len(description) > 0
                    and not skip_tracker_descriptions
                ):
                    logger.info(
                        f"Description content:\n{description[:500]}...",
                        extra={"markup": False},
                    )
                    meta.description = description
                    meta.saved_description = True
                else:
                    logger.info("[yellow]HDBITS description empty[/yellow]")
                if image_list and meta.keep_images:
                    valid_images = await check_images_concurrently(
                        image_list, meta
                    )
                    if valid_images:
                        meta.image_list = valid_images
                        await handle_image_list(
                            meta, tracker_name, valid_images
                        )
                else:
                    meta.image_list = []

                logger.info(
                    f"[green]{tracker_name} data found: IMDb ID: {imdb}, TVDb ID: {meta.tvdb_id}, HDBITS Name: {meta.hdb_name}[/green]"
                )
            else:
                logger.info(
                    f"[yellow]{tracker_name} data not found for ID: {hdb_torrent_id}[/yellow]"
                )
                found_match = False
        else:
            logger.debug(
                "[yellow]No ID found in meta for HDBITS, searching by file name[/yellow]"
            )

            # Use search_filename function if ID is not found in meta
            hdb_search = await tracker_instance.search_filename(
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
                hdb_search,
            )
            meta.hdb_name = hdb_name
            if tracker_id:
                meta.set_tracker_ids({tracker_name: tracker_id})

            if imdb or tvdb_id or meta.hdb_description:
                if not meta.unattended:
                    logger.info(
                        f"[green]{tracker_name} data found: IMDb ID: {imdb}, TVDb ID: {meta.tvdb_id}, HDBITS Name: {meta.hdb_name}[/green]"
                    )
                    if await prompt_user_for_confirmation(
                        f"Do you want to use the ID's found on {tracker_name}?"
                    ):
                        logger.info(
                            f"[green]{tracker_name} data retained.[/green]"
                        )
                        meta.imdb_id = imdb if imdb else meta.imdb_id
                        meta.tvdb_id = tvdb_id if tvdb_id else meta.tvdb_id
                        found_match = True
                        description_source = meta.hdb_description or ""
                        description, image_list = cast(
                            tuple[str | None, list[ImageDict]],
                            bbcode.clean_hdb_description(description_source),
                        )
                        if (
                            description
                            and len(description) > 0
                            and not skip_tracker_descriptions
                        ):
                            logger.info(
                                "[bold green]Successfully grabbed description from HDBITS"
                            )
                            logger.info(
                                f"HDBITS Description content:\n{description[:1000]}.....",
                                extra={"markup": False},
                            )
                            logger.info(
                                "[cyan]Do you want to edit, discard or keep the description?[/cyan]"
                            )
                            edit_choice_raw = cli_ui.ask_string(
                                "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
                            )
                            edit_choice = (
                                (edit_choice_raw or "").strip().lower()
                            )

                            if edit_choice.lower() == "e":
                                # pyrefly: ignore [bad-argument-type]
                                edited_description = str(
                                    click.edit(text=description) or ""
                                )
                                if edited_description:
                                    description = edited_description.strip()
                                    meta.description = description
                                    meta.saved_description = True
                                logger.info(
                                    f"[green]Final description after editing:[/green] {description}",
                                    extra={"markup": False},
                                )
                            elif edit_choice.lower() == "d":
                                meta.hdb_description = ""
                                logger.info(
                                    "[yellow]Description discarded.[/yellow]"
                                )
                            else:
                                logger.info(
                                    "[green]Keeping the original description.[/green]"
                                )
                                meta.description = description
                                meta.saved_description = True
                        else:
                            logger.info(
                                "[yellow]HDBITS description empty[/yellow]"
                            )
                        if image_list and meta.keep_images:
                            valid_images = await check_images_concurrently(
                                image_list, meta
                            )
                            if valid_images:
                                meta.image_list = valid_images
                                await handle_image_list(
                                    meta, tracker_name, valid_images
                                )
                    else:
                        logger.info(
                            f"[yellow]{tracker_name} data discarded.[/yellow]"
                        )
                        meta.clear_tracker_id(tracker_name)
                        meta.tvdb_id = meta.tvdb_id if meta.tvdb_id else 0
                        meta.imdb_id = meta.imdb_id if meta.imdb_id else 0
                        meta.hdb_name = None
                        meta.hdb_description = ""
                        found_match = False
                else:
                    meta.imdb_id = imdb if imdb else meta.imdb_id
                    meta.tvdb_id = tvdb_id if tvdb_id else meta.tvdb_id
                    description_source = meta.hdb_description or ""
                    description, image_list = cast(
                        tuple[str | None, list[ImageDict]],
                        bbcode.clean_hdb_description(description_source),
                    )
                    if (
                        description
                        and len(description) > 0
                        and not skip_tracker_descriptions
                    ):
                        logger.info(
                            f"HDBITS Description content:\n{description[:500]}.....",
                            extra={"markup": False},
                        )
                        meta.description = description
                        meta.saved_description = True
                    if image_list and meta.keep_images:
                        valid_images = await check_images_concurrently(
                            image_list, meta
                        )
                        if valid_images:
                            meta.image_list = valid_images
                            await handle_image_list(
                                meta, tracker_name, valid_images
                            )
                    logger.info(
                        f"[green]{tracker_name} data found: IMDb ID: {imdb}, TVDb ID: {meta.tvdb_id}, HDBITS Name: {hdb_name}[/green]"
                    )
                    found_match = True
            else:
                meta.hdb_name = None
                meta.hdb_description = ""
                meta.clear_tracker_id(tracker_name)
                found_match = False

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
