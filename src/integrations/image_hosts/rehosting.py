# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import glob
import inspect
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import httpx
from aiofiles import os as aio_os

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import (
    get_tracker_image_collection,
    has_tracker_image_collection,
    set_tracker_image_collection,
)
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.filesystem.temp_paths import (
    artwork_dir,
    dynamic_hdr_plots_dir,
    menu_screenshots_dir,
    screenshots_dir,
    spectrograms_dir,
)
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.mapping.value_coercion import to_int
from src.integrations.media.screenshot_capture import TakeScreensManager
from src.integrations.observability.runtime_support import logger


@dataclass(frozen=True)
class ImageHostPolicy:
    """Declarative image-host requirements for a tracker adapter."""

    url_host_mapping: Mapping[str, str]
    approved_image_hosts: tuple[str, ...]
    img_host_index: int = 1
    preferred_image_host: str | None = None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def has_restricted_image_hosts(
    target_trackers: Iterable[str],
    tracker_class_map: Mapping[str, Any],
) -> bool:
    """Return True if any of the target trackers define image-host restrictions."""
    for tracker_name in target_trackers:
        tracker_class = tracker_class_map.get(
            str(tracker_name).replace(" ", "").upper()
        )
        policy = getattr(tracker_class, "image_host_policy", None)
        if isinstance(policy, ImageHostPolicy) and policy.approved_image_hosts:
            return True

        approved_hosts = getattr(tracker_class, "approved_image_hosts", None)
        if (
            callable(getattr(tracker_class, "check_image_hosts", None))
            and isinstance(approved_hosts, (tuple, list, set))
            and any(isinstance(host, str) for host in approved_hosts)
        ):
            return True

    return False


def select_common_image_host(
    default_config: Mapping[str, Any],
    target_trackers: Iterable[str],
    tracker_class_map: Mapping[str, Any],
) -> str | None:
    """Return the preferred configured host accepted by every restricted target.

    Trackers without a declared policy do not constrain the selection. ``None``
    means no restricted targets or no common configured host, so callers retain
    the normal per-tracker rehosting fallback.
    """
    approved_sets: list[set[str]] = []
    for tracker_name in target_trackers:
        tracker_class = tracker_class_map.get(
            str(tracker_name).replace(" ", "").upper()
        )
        policy = getattr(tracker_class, "image_host_policy", None)
        if isinstance(policy, ImageHostPolicy):
            approved_sets.append(set(policy.approved_image_hosts))
            continue

        approved_hosts = getattr(tracker_class, "approved_image_hosts", None)
        if callable(
            getattr(tracker_class, "check_image_hosts", None)
        ) and isinstance(approved_hosts, (tuple, list, set)):
            approved_sets.append(
                {host for host in approved_hosts if isinstance(host, str)}
            )

    if not approved_sets:
        return None

    common_hosts = set.intersection(*approved_sets)
    if not common_hosts:
        return None

    configured_hosts = sorted(
        (
            (int(match.group(1)), host.strip().lower())
            for key, value in default_config.items()
            if (match := re.fullmatch(r"img_host_(\d+)", key))
            and (host := _as_str(value))
            and host.strip()
        ),
        key=lambda item: item[0],
    )
    return next(
        (host for _, host in configured_hosts if host in common_hosts), None
    )


def _safe_remove(path: str) -> bool:
    try:
        if Path(path).exists():
            Path(path).unlink()
            return True
    except Exception as e:
        logger.info(f"[yellow]Failed to delete file {path}: {e!s}[/yellow]")
    return False


async def match_host(hostname: str, approved_hosts: Iterable[str]) -> str:
    for approved_host in approved_hosts:
        if hostname == approved_host or hostname.endswith(f".{approved_host}"):
            return approved_host
    return hostname


async def sanitize_filename(filename: str) -> str:
    # Replace invalid characters like colons with an underscore
    return re.sub(r'[<>:"/\\|?*]', "_", filename)


class RehostImagesManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.default_config = cast(dict[str, Any], config.get("DEFAULT", {}))
        self.takescreens_manager = TakeScreensManager(config)
        self.uploadscreens_manager = UploadScreensManager(config)

    async def check_hosts(
        self,
        meta: Meta,
        tracker: str,
        url_host_mapping: dict[str, str],
        img_host_index: int = 1,
        approved_image_hosts: Sequence[str] | None = None,
    ) -> tuple[list[dict[str, str]], bool, bool]:
        images, retry_mode, images_reuploaded = await _check_hosts(
            meta,
            tracker,
            url_host_mapping,
            img_host_index=img_host_index,
            approved_image_hosts=approved_image_hosts,
            default_config=self.default_config,
            takescreens_manager=self.takescreens_manager,
            uploadscreens_manager=self.uploadscreens_manager,
        )
        if tracker != "covers":
            await _check_additional_image_collections(
                meta,
                tracker,
                url_host_mapping,
                approved_image_hosts=approved_image_hosts,
                default_config=self.default_config,
                uploadscreens_manager=self.uploadscreens_manager,
            )
        return images, retry_mode, images_reuploaded

    async def check_policy(
        self, meta: Meta, tracker: str, policy: ImageHostPolicy
    ) -> tuple[list[dict[str, str]], bool, bool]:
        """Apply a tracker's declarative image-host policy."""
        original_host = str(meta.imghost or "")
        preferred = policy.preferred_image_host
        use_preferred = False
        if preferred is not None and not getattr(
            meta, "imghost_from_cli", False
        ):
            meta.imghost = preferred
            use_preferred = True
        try:
            approved_hosts = list(policy.approved_image_hosts)
            effective_config = _effective_image_host_config(
                self.default_config,
                meta,
                approved_hosts,
                policy.img_host_index,
                use_preferred,
            )
            images, retry_mode, images_reuploaded = await _check_hosts(
                meta,
                tracker,
                dict(policy.url_host_mapping),
                img_host_index=policy.img_host_index,
                approved_image_hosts=approved_hosts,
                default_config=effective_config,
                takescreens_manager=self.takescreens_manager,
                uploadscreens_manager=self.uploadscreens_manager,
            )
            if tracker != "covers":
                await _check_additional_image_collections(
                    meta,
                    tracker,
                    dict(policy.url_host_mapping),
                    approved_image_hosts=approved_hosts,
                    default_config=effective_config,
                    uploadscreens_manager=self.uploadscreens_manager,
                )
            return images, retry_mode, images_reuploaded
        finally:
            meta.imghost = original_host

    async def handle_image_upload(
        self,
        meta: Meta,
        tracker: str,
        url_host_mapping: dict[str, str],
        approved_image_hosts: Sequence[str] | None = None,
        img_host_index: int = 1,
        file: str | None = None,
    ) -> tuple[list[dict[str, str]], bool, bool]:
        return await _handle_image_upload(
            meta,
            tracker,
            url_host_mapping,
            approved_image_hosts=approved_image_hosts,
            img_host_index=img_host_index,
            file=file,
            default_config=self.default_config,
            takescreens_manager=self.takescreens_manager,
            uploadscreens_manager=self.uploadscreens_manager,
        )


async def check_tracker_image_hosts(meta: Meta, tracker_class: Any) -> None:
    """Apply a tracker's image-host policy when it defines one."""
    # MUSIC artwork is hosted before tracker processing.  It has no video
    # screenshots, so a missing screenshot collection must not trigger the
    # generic reupload path (which would attempt to capture the audio file).
    if meta.category == "MUSIC":
        return

    policy = getattr(tracker_class, "image_host_policy", None)
    rehost_manager = getattr(tracker_class, "rehost_images_manager", None)
    if isinstance(policy, ImageHostPolicy) and rehost_manager is not None:
        await rehost_manager.check_policy(meta, tracker_class.tracker, policy)
        return

    check_hosts = getattr(tracker_class, "check_image_hosts", None)
    if callable(check_hosts):
        outcome = check_hosts(meta)
        if inspect.isawaitable(outcome):
            await outcome


def _image_host(raw_url: str, url_host_mapping: Mapping[str, str]) -> str:
    hostname = (urlparse(raw_url).hostname or "").lower()
    for source_host, mapped_host in url_host_mapping.items():
        normalized_source = source_host.lower()
        if hostname == normalized_source or hostname.endswith(
            f".{normalized_source}"
        ):
            return mapped_host
    return hostname


def _collection_directory(meta: Meta, collection_name: str) -> Path | None:
    if collection_name == "menu_images":
        return menu_screenshots_dir(meta.base_dir, meta.uuid)
    if collection_name == "spectrograms_images":
        return spectrograms_dir(meta.base_dir, meta.uuid)
    if collection_name == "dynamic_hdr_plot_images":
        return dynamic_hdr_plots_dir(meta.base_dir, meta.uuid)
    return None


async def _local_image_path(
    meta: Meta, collection_name: str, image: Mapping[str, Any]
) -> Path | None:
    local_file_path = _as_str(image.get("local_file_path"))
    if local_file_path:
        path = Path(local_file_path)
        if path.is_file():
            return path

    raw_url = _as_str(image.get("raw_url"))
    directory = _collection_directory(meta, collection_name)
    filename = Path(urlparse(raw_url or "").path).name
    if directory and filename:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


async def _download_image_for_rehost(
    meta: Meta, collection_name: str, raw_url: str
) -> Path | None:
    directory = (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / "rehosted_images"
        / collection_name
    )
    directory.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(raw_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        suffix = ".png"
    filename = await sanitize_filename(Path(parsed.path).stem or "image")
    destination = directory / f"{filename}{suffix}"
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=60.0
        ) as client:
            response = await client.get(raw_url)
            response.raise_for_status()
        await asyncio.to_thread(destination.write_bytes, response.content)
        return destination
    except (httpx.HTTPError, OSError) as error:
        logger.warning(
            f"[yellow]Could not download {collection_name} image for {raw_url}: {error!s}[/yellow]"
        )
        return None


async def _check_additional_image_collections(
    meta: Meta,
    tracker: str,
    url_host_mapping: Mapping[str, str],
    *,
    approved_image_hosts: Iterable[str] | None,
    default_config: Mapping[str, Any],
    uploadscreens_manager: UploadScreensManager,
) -> None:
    """Rehost uploaded assets kept outside ``meta.image_list``.

    Disc-menu screenshots and audio spectrograms are submitted alongside normal
    screenshots by several trackers.  They must therefore satisfy the same
    tracker host policy, while retaining their own metadata collections.
    """
    if meta.skip_imghost_upload:
        return

    approved_hosts = set(approved_image_hosts or [])
    if not approved_hosts:
        return
    configured_hosts = [
        value
        for key, value in default_config.items()
        if re.fullmatch(r"img_host_(\d+)", key)
        and isinstance(value, str)
        and value
    ]
    if not any(host in approved_hosts for host in configured_hosts):
        logger.warning(
            f"[yellow]No configured image host is approved by {tracker} for supplemental images.[/yellow]"
        )
        return

    original_imghost = meta.imghost
    try:
        for collection_name in (
            "menu_images",
            "spectrograms_images",
            "dynamic_hdr_plot_images",
        ):
            collection = getattr(meta, collection_name, [])
            if not isinstance(collection, list) or not collection:
                continue

            # Collections can originate from cached/external metadata. Dress
            # them at this integration boundary before persisting a tracker
            # override so malformed scalar entries cannot leak into the domain.
            normalized_collection = [
                dict(item) for item in collection if isinstance(item, dict)
            ]
            updated_images = list(normalized_collection)
            pending: list[tuple[int, dict[str, Any], Path]] = []
            for index, item in enumerate(normalized_collection):
                raw_url = _as_str(item.get("raw_url"))
                if (
                    raw_url
                    and _image_host(raw_url, url_host_mapping)
                    in approved_hosts
                ):
                    continue
                local_path = await _local_image_path(
                    meta, collection_name, item
                )
                if local_path is None and raw_url:
                    local_path = await _download_image_for_rehost(
                        meta, collection_name, raw_url
                    )
                if local_path is None:
                    logger.warning(
                        f"[yellow]{tracker}: cannot rehost {collection_name} image {index + 1}; keeping its original URL.[/yellow]"
                    )
                    continue
                pending.append((index, item, local_path))

            if not pending:
                continue

            uploaded, _ = await uploadscreens_manager.upload_screens(
                meta,
                len(pending),
                1,
                0,
                len(pending),
                [str(path) for _, _, path in pending],
                {},
                allowed_hosts=list(approved_hosts),
            )
            if len(uploaded) != len(pending):
                logger.warning(
                    f"[yellow]{tracker}: only rehosted {len(uploaded)}/{len(pending)} {collection_name} images.[/yellow]"
                )

            for (index, original, local_path), uploaded_image in zip(
                pending, uploaded, strict=False
            ):
                raw_url = _as_str(uploaded_image.get("raw_url"))
                if (
                    not raw_url
                    or _image_host(raw_url, url_host_mapping)
                    not in approved_hosts
                ):
                    logger.warning(
                        f"[yellow]{tracker}: rehosted {collection_name} image is not on an approved host; keeping its original URL.[/yellow]"
                    )
                    continue
                replacement = dict(original)
                replacement.update(uploaded_image)
                replacement["local_file_path"] = str(local_path)
                updated_images[index] = replacement
            set_tracker_image_collection(
                meta, tracker, collection_name, updated_images
            )
    finally:
        meta.imghost = original_imghost


def _effective_image_host_config(
    default_config: Mapping[str, Any],
    meta: Meta,
    approved_hosts: Sequence[str],
    img_host_index: int,
    allow_unconfigured_preferred: bool,
) -> Mapping[str, Any]:
    if not allow_unconfigured_preferred:
        return default_config
    preferred = str(meta.imghost or "").strip().lower()
    if not preferred or preferred not in approved_hosts:
        return default_config
    effective = dict(default_config)
    effective[f"img_host_{img_host_index}"] = preferred
    return effective


async def _check_hosts(
    meta: Meta,
    tracker: str,
    url_host_mapping: dict[str, str],
    img_host_index: int = 1,
    approved_image_hosts: Sequence[str] | None = None,
    default_config: Mapping[str, Any] | None = None,
    takescreens_manager: TakeScreensManager | None = None,
    uploadscreens_manager: UploadScreensManager | None = None,
) -> tuple[list[dict[str, str]], bool, bool]:
    if default_config is None:
        raise ValueError("default_config is required")
    if takescreens_manager is None:
        raise ValueError("takescreens_manager is required")
    if uploadscreens_manager is None:
        raise ValueError("uploadscreens_manager is required")
    if approved_image_hosts is None:
        approved_image_hosts = []
    if meta.skip_imghost_upload:
        logger.debug(
            f"[yellow]Skipping image host upload for {tracker} as per meta.skip_imghost_upload setting."
        )
        return (
            get_tracker_image_collection(meta, tracker, "screenshots"),
            False,
            False,
        )

    has_tracker_override = has_tracker_image_collection(
        meta, tracker, "screenshots"
    )
    tracker_images = get_tracker_image_collection(meta, tracker, "screenshots")

    logger.debug(
        f"[cyan]check_hosts debug: tracker={tracker} meta.imghost={meta.imghost} approved_image_hosts={approved_image_hosts} "
        f"image_list={len(meta.image_list or [])} tracker_screenshots={len(tracker_images)}[/cyan]"
    )

    # Check if we have main image_list but no tracker-specific images yet
    if meta.image_list and not has_tracker_override:
        logger.debug(
            f"[yellow]Checking if existing images in meta.image_list can be used for {tracker}..."
        )
        # Check if the URLs in image_list are from approved hosts
        approved_images: list[dict[str, str]] = []
        need_reupload = False

        image_list_entries = cast(list[dict[str, str]], meta.image_list)
        for image in image_list_entries:
            raw_url = _as_str(image.get("raw_url"))
            if not raw_url:
                continue

            parsed_url = urlparse(raw_url)
            hostname = parsed_url.netloc
            mapped_host = await match_host(hostname, url_host_mapping.keys())

            if mapped_host:
                mapped_host = url_host_mapping.get(mapped_host, mapped_host)
                if mapped_host in approved_image_hosts:
                    approved_images.append(image)
                    logger.debug(
                        f"[green]URL '{raw_url}' is from approved host '{mapped_host}'."
                    )
                else:
                    need_reupload = True
                    logger.debug(
                        f"[yellow]URL '{raw_url}' is not from an approved host for {tracker}."
                    )
            else:
                need_reupload = True

        # If all images are approved, use them directly
        if (
            approved_images
            and len(approved_images) == len(meta.image_list)
            and not need_reupload
        ):
            set_tracker_image_collection(
                meta, tracker, "screenshots", approved_images
            )
            logger.debug(
                f"[green]All existing images are from approved hosts for {tracker}."
            )
            return (
                get_tracker_image_collection(meta, tracker, "screenshots"),
                False,
                False,
            )

    if tracker == "covers":
        reuploaded_images_path = (
            Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json"
        )
    else:
        reuploaded_images_path = (
            Path(meta.base_dir) / "tmp" / meta.uuid / "reuploaded_images.json"
        )
    reuploaded_images: list[dict[str, str]] = []

    if Path(reuploaded_images_path).exists():
        try:
            async with aiofiles.open(
                reuploaded_images_path, encoding="utf-8"
            ) as f:
                content = await f.read()
                loaded = json.loads(content)
                if isinstance(loaded, list):
                    reuploaded_images = cast(list[dict[str, str]], loaded)
        except Exception as e:
            logger.error(f"[red]Failed to load reuploaded images: {e}")

    valid_reuploaded_images: list[dict[str, str]] = []
    for image in reuploaded_images:
        raw_url = _as_str(image.get("raw_url"))
        if not raw_url:
            continue

        # For covers, verify the release_url matches
        if (
            tracker == "covers"
            and "release_url" in meta
            and (
                "release_url" not in image
                or image["release_url"] != meta.release_url
            )
        ):
            if meta.debug:
                if "release_url" not in image:
                    logger.debug(
                        f"[yellow]Skipping image without release_url: {raw_url}"
                    )
                else:
                    logger.debug(
                        f"[yellow]Skipping image with mismatched release_url: {image['release_url']} != {meta.release_url}"
                    )
            continue

        parsed_url = urlparse(raw_url)
        hostname = parsed_url.netloc
        mapped_host = await match_host(hostname, url_host_mapping.keys())

        if mapped_host:
            mapped_host = url_host_mapping.get(mapped_host, mapped_host)
            if mapped_host in approved_image_hosts:
                valid_reuploaded_images.append(image)
            elif meta.debug:
                logger.info(
                    f"[red]URL '{raw_url}' from reuploaded_images.json is not recognized as an approved host."
                )

    if valid_reuploaded_images:
        set_tracker_image_collection(
            meta, tracker, "screenshots", valid_reuploaded_images
        )
        if tracker == "covers":
            logger.info("[green]Using valid images from covers.json.")
        else:
            logger.info(
                "[green]Using valid images from reuploaded_images.json."
            )
        return (
            get_tracker_image_collection(meta, tracker, "screenshots"),
            False,
            False,
        )

    # Check if the tracker-specific key has valid images
    has_valid_images = False
    if tracker_images:
        valid_hosts: list[bool] = []
        for image in cast(list[dict[str, str]], tracker_images):
            raw_url = _as_str(image.get("raw_url")) or ""
            netloc = urlparse(raw_url).netloc
            matched_host = await match_host(netloc, url_host_mapping.keys())
            mapped_host = url_host_mapping.get(matched_host, matched_host)
            valid_hosts.append(mapped_host in approved_image_hosts)

        # Then check if all are valid
        if all(valid_hosts) and tracker_images:
            has_valid_images = True

    if has_valid_images:
        logger.info(f"[green]Using valid tracker screenshots for {tracker}.")
        return (
            get_tracker_image_collection(meta, tracker, "screenshots"),
            False,
            False,
        )

    logger.debug(
        f"[yellow]No valid images found for {tracker}, will attempt to reupload..."
    )

    images_reuploaded = False
    configured_indices = sorted(
        int(match.group(1))
        for key in default_config
        if (match := re.fullmatch(r"img_host_(\d+)", key))
        and _as_str(default_config.get(key))
    )
    if not configured_indices:
        return [], True, images_reuploaded

    current_position = next(
        (
            i
            for i, index in enumerate(configured_indices)
            if index >= img_host_index
        ),
        len(configured_indices),
    )

    while current_position < len(configured_indices):
        current_index = configured_indices[current_position]
        image_list, retry_mode, images_reuploaded = await _handle_image_upload(
            meta,
            tracker,
            url_host_mapping,
            approved_image_hosts,
            img_host_index=current_index,
            default_config=default_config,
            takescreens_manager=takescreens_manager,
            uploadscreens_manager=uploadscreens_manager,
        )

        if image_list:
            set_tracker_image_collection(
                meta, tracker, "screenshots", image_list
            )

        if retry_mode:
            logger.info(
                f"[yellow]Switching to the next image host. Current index: {current_index}"
            )
            current_position += 1
            continue  # Retry with next host

        break

    if not get_tracker_image_collection(meta, tracker, "screenshots"):
        logger.info(
            "[red]All image hosts failed. Please check your configuration."
        )

    logger.debug(
        f"[cyan]check_hosts debug: done tracker={tracker} image_list={len(meta.image_list or [])} "
        f"tracker_screenshots={len(get_tracker_image_collection(meta, tracker, 'screenshots'))}[/cyan]"
    )

    return (
        get_tracker_image_collection(meta, tracker, "screenshots"),
        False,
        images_reuploaded,
    )


def _require_image_upload_dependencies(
    default_config: Mapping[str, Any] | None,
    takescreens_manager: TakeScreensManager | None,
    uploadscreens_manager: UploadScreensManager | None,
) -> tuple[Mapping[str, Any], TakeScreensManager, UploadScreensManager]:
    if default_config is None:
        raise ValueError("default_config is required")
    if takescreens_manager is None:
        raise ValueError("takescreens_manager is required")
    if uploadscreens_manager is None:
        raise ValueError("uploadscreens_manager is required")
    return default_config, takescreens_manager, uploadscreens_manager


def _image_generation_path(meta: Meta) -> str:
    if meta.is_disc == "HDDVD":
        return str(meta.discs[0].get("largest_evo", ""))
    paths = meta.filelist
    return str(paths[0]) if paths else ""


def _requested_screens(meta: Meta, default_config: Mapping[str, Any]) -> int:
    default_screens = to_int(default_config.get("screens", 6), 6)
    return to_int(meta.screens, default_screens)


def _manifest_screens(meta: Meta) -> list[str]:
    return [
        str(path) for path in manifest_files(meta.base_dir, meta.uuid, "main")
    ]


async def _directory_png_files(path: Path) -> list[str]:
    return [
        str(path / name)
        for name in await aio_os.listdir(path)
        if name.endswith(".png")
    ]


def _image_entry_filename(image: Mapping[str, str]) -> str | None:
    for key in ("raw_url", "img_url", "web_url"):
        value = _as_str(image.get(key))
        if value is None:
            continue
        filename = Path(urlparse(value).path).name
        if filename and filename.lower().endswith(".png"):
            return filename
    return None


def _image_list_filenames(meta: Meta) -> list[str]:
    entries = cast(list[dict[str, str]], meta.image_list)
    return [
        filename
        for image in entries
        if (filename := _image_entry_filename(image)) is not None
    ]


def _extend_unique(target: list[str], values: Sequence[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _log_image_list_candidates(
    meta: Meta, all_png_files: Sequence[str], names: Sequence[str]
) -> None:
    if all_png_files and meta.debug:
        logger.info(
            f"[cyan]Found {len(all_png_files)} PNG files in screenshots directory"
        )
    if names and meta.debug:
        logger.info(
            f"[cyan]Extracted {len(names)} filenames from image_list URLs: {list(names)}"
        )


def _matching_image_list_files(
    all_png_files: Sequence[str], names: Sequence[str]
) -> list[str]:
    expected = set(names)
    return [path for path in all_png_files if Path(path).name in expected]


def _needs_title_image_fallback(
    filename: str, current_count: int, selected_count: int, needed_total: int
) -> bool:
    return bool(filename and current_count + selected_count < needed_total)


async def _title_image_candidates(
    all_png_files: Sequence[str], filename: str
) -> list[str]:
    sanitized_title = await sanitize_filename(filename)
    logger.debug(
        f"[yellow]Searching for screenshots with pattern: {sanitized_title}*.png"
    )
    return [
        value
        for value in all_png_files
        if Path(value).name.startswith(sanitized_title)
    ]


async def _image_list_screenshot_candidates(
    meta: Meta,
    screenshot_path: Path,
    filename: str,
    needed_total: int,
    current_count: int,
) -> list[str]:
    if not meta.image_list or not isinstance(meta.image_list, list):
        return []
    all_png_files = await _directory_png_files(screenshot_path)
    names = _image_list_filenames(meta)
    _log_image_list_candidates(meta, all_png_files, names)
    selected = _matching_image_list_files(all_png_files, names)
    if _needs_title_image_fallback(
        filename, current_count, len(selected), needed_total
    ):
        _extend_unique(
            selected, await _title_image_candidates(all_png_files, filename)
        )
    return selected


async def _normal_existing_screens(
    meta: Meta, screenshot_path: Path, filename: str
) -> list[str]:
    if not meta.filelist:
        return []
    sanitized_title = await sanitize_filename(filename)
    filename_pattern = f"{glob.escape(sanitized_title)}*.png"
    logger.debug(
        f"[yellow]Searching for screenshots with pattern: {filename_pattern}"
    )
    if meta.is_disc == "DVD":
        disc_name = glob.escape(str(meta.discs[0]["name"]))
        return await asyncio.to_thread(
            lambda: [
                str(path)
                for path in screenshots_dir(meta.base_dir, meta.uuid).glob(
                    f"{disc_name}-*.png"
                )
            ]
        )
    return await asyncio.to_thread(
        lambda: [str(path) for path in screenshot_path.glob(filename_pattern)]
    )


async def _glob_screenshot_patterns(
    screenshot_path: Path, patterns: Sequence[str]
) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        matches = await asyncio.to_thread(
            lambda value=pattern: [
                str(path) for path in screenshot_path.glob(value)
            ]
        )
        values.extend(matches)
    return values


async def _unwanted_screenshot_files(screenshot_path: Path) -> set[str]:
    visible = await _glob_screenshot_patterns(
        screenshot_path, ("FILE*", "PLAYLIST*")
    )
    hidden = await _glob_screenshot_patterns(
        screenshot_path, (".FILE*", ".PLAYLIST*")
    )
    return set(visible + hidden)


async def _indexed_fallback_screens(screenshot_path: Path) -> list[str]:
    discovered = await _glob_screenshot_patterns(
        screenshot_path, ("*.png", ".[!.]*.png")
    )
    unwanted = await _unwanted_screenshot_files(screenshot_path)
    filtered = set(discovered) - unwanted
    pattern = re.compile(r".*-\d+\.png$")
    return [value for value in filtered if pattern.match(Path(value).name)]


async def _cover_screens(meta: Meta) -> list[str]:
    return await asyncio.to_thread(
        lambda: [
            str(path)
            for path in artwork_dir(meta.base_dir, meta.uuid).glob(
                "cover_*.jpg"
            )
        ]
    )


async def _collect_existing_screens(
    meta: Meta,
    tracker: str,
    filename: str,
    requested: int,
    screenshot_path: Path,
) -> list[str]:
    if tracker == "covers":
        return list(dict.fromkeys(await _cover_screens(meta)))
    screens = _manifest_screens(meta)
    candidates = await _image_list_screenshot_candidates(
        meta, screenshot_path, filename, requested, len(screens)
    )
    _extend_unique(screens, candidates)
    if len(screens) < requested:
        _extend_unique(
            screens,
            await _normal_existing_screens(meta, screenshot_path, filename),
        )
    if len(screens) < requested:
        _extend_unique(
            screens, await _indexed_fallback_screens(screenshot_path)
        )
    return list(dict.fromkeys(screens))


async def _capture_disc_screens(
    meta: Meta,
    manager: TakeScreensManager,
    filename: str,
    needed: int,
) -> bool:
    if meta.is_disc == "BDMV":
        await manager.disc_screenshots(
            meta,
            filename,
            meta.bdinfo,
            meta.uuid,
            meta.base_dir,
            meta.vapoursynth,
            [],
            meta.ffdebug,
            needed,
            True,
            capture_group="main",
        )
        return True
    if meta.is_disc == "DVD":
        await manager.dvd_screenshots(meta, disc_num=0, retry_cap=True)
        return True
    return False


async def _capture_non_disc_screens(
    meta: Meta,
    manager: TakeScreensManager,
    path: str,
    filename: str,
    needed: int,
) -> None:
    if meta.category == "XXX":
        await manager.xxx_contact_sheets(
            meta.filelist or [], meta.uuid, meta.base_dir, meta
        )
        return
    if not path:
        logger.info(
            "[red]No valid path available for screenshot generation.[/red]"
        )
        return
    await manager.screenshots(
        path,
        filename,
        meta.uuid,
        meta.base_dir,
        meta,
        needed,
        True,
        "",
        capture_group="main",
    )


async def _dispatch_screenshot_capture(
    meta: Meta,
    takescreens_manager: TakeScreensManager,
    path: str,
    filename: str,
    needed: int,
) -> None:
    if await _capture_disc_screens(
        meta, takescreens_manager, filename, needed
    ):
        return
    await _capture_non_disc_screens(
        meta, takescreens_manager, path, filename, needed
    )


async def _generated_screens(meta: Meta) -> list[str]:
    if meta.is_disc == "DVD":
        disc_name = glob.escape(str(meta.discs[0]["name"]))
        return await asyncio.to_thread(
            lambda: [
                str(path)
                for path in screenshots_dir(meta.base_dir, meta.uuid).glob(
                    f"{disc_name}-*.png"
                )
            ]
        )
    return _manifest_screens(meta)


async def _generate_missing_screens(
    meta: Meta,
    takescreens_manager: TakeScreensManager,
    screens: list[str],
    requested: int,
    path: str,
    filename: str,
) -> None:
    needed = requested - len(screens)
    logger.debug(
        f"[yellow]Found {len(screens)} screenshots, need {needed} more to "
        f"reach {requested} total."
    )
    try:
        await _dispatch_screenshot_capture(
            meta, takescreens_manager, path, filename, needed
        )
        _extend_unique(screens, await _generated_screens(meta))
    except Exception as error:
        logger.error(f"[red]Error during screenshot capture: {error}")
        import traceback

        logger.info(f"[dim]{traceback.format_exc()}[/dim]")


async def _ensure_screens(
    meta: Meta,
    takescreens_manager: TakeScreensManager,
    screens: list[str],
    requested: int,
    path: str,
    filename: str,
) -> list[str]:
    if len(screens) >= requested:
        return screens
    await _generate_missing_screens(
        meta, takescreens_manager, screens, requested, path, filename
    )
    return screens


def _image_list_basenames(meta: Meta) -> set[str]:
    entries = cast(list[dict[str, str]], meta.image_list)
    return {
        Path(urlparse(_as_str(image.get("raw_url")) or "").path).name
        for image in entries
    }


def _partition_screens(
    screens: Sequence[str], preferred_names: set[str]
) -> tuple[list[str], list[str]]:
    preferred: list[str] = []
    others: list[str] = []
    for screen in screens:
        target = preferred if Path(screen).name in preferred_names else others
        target.append(screen)
    return preferred, others


def _selected_screens(
    meta: Meta, tracker: str, screens: list[str], requested: int
) -> list[str]:
    ordered = sorted(screens)
    if tracker == "covers":
        return ordered
    preferred, others = _partition_screens(
        ordered, _image_list_basenames(meta)
    )
    remaining = requested - len(preferred)
    preferred.extend(others[: max(remaining, 0)])
    return preferred[:requested]


def _configured_host_indices(default_config: Mapping[str, Any]) -> list[int]:
    indices: list[int] = []
    for key, value in default_config.items():
        match = re.fullmatch(r"img_host_(\d+)", str(key))
        if match is not None and _as_str(value):
            indices.append(int(match.group(1)))
    return sorted(indices)


def _starting_host_position(indices: Sequence[int], start: int) -> int:
    for position, index in enumerate(indices):
        if index >= start:
            return position
    return len(indices)


def _select_approved_upload_host(
    meta: Meta,
    tracker: str,
    default_config: Mapping[str, Any],
    approved_hosts: Sequence[str],
    img_host_index: int,
) -> tuple[int | None, bool]:
    indices = _configured_host_indices(default_config)
    if not indices:
        return None, False
    position = _starting_host_position(indices, img_host_index)
    skipped = False
    for index in indices[position:]:
        host = _as_str(default_config.get(f"img_host_{index}"))
        if host is None or host not in approved_hosts:
            logger.info(
                f"[red]Your preferred image host '{host}' is not supported "
                f"at {tracker}, trying next host."
            )
            skipped = True
            continue
        meta.imghost = host
        logger.debug(f"[green]Uploading to approved host '{host}'.")
        return index, skipped
    logger.info("[red]No approved image host was selected; skipping upload.")
    return None, skipped


def _log_uploaded_images(
    tracker: str, images: Sequence[Mapping[str, str]]
) -> None:
    logger.debug(
        f"[debug] Updated tracker screenshots for {tracker} with "
        f"{len(images)} images."
    )
    for image in images:
        logger.debug(
            "[debug] Response in upload_image_task: "
            f"{image['img_url']}, {image['raw_url']}, {image['web_url']}"
        )


async def _upload_approved_screens(
    meta: Meta,
    tracker: str,
    uploadscreens_manager: UploadScreensManager,
    screens: list[str],
    upload_index: int,
    retry_mode: bool,
    approved_hosts: Sequence[str],
) -> list[dict[str, str]]:
    uploaded, _ = await uploadscreens_manager.upload_screens(
        meta,
        len(screens),
        upload_index,
        0,
        len(screens),
        screens,
        {},
        retry_mode,
        allowed_hosts=list(approved_hosts),
    )
    if uploaded:
        set_tracker_image_collection(meta, tracker, "screenshots", uploaded)
    _log_uploaded_images(tracker, uploaded)
    return get_tracker_image_collection(meta, tracker, "screenshots")


async def _mapped_uploaded_host(
    raw_url: str, url_host_mapping: Mapping[str, str]
) -> str:
    hostname = urlparse(raw_url).netloc
    matched = await match_host(hostname, url_host_mapping.keys())
    return url_host_mapping.get(matched, matched)


async def _first_unapproved_url(
    images: Sequence[Mapping[str, str]],
    url_host_mapping: Mapping[str, str],
    approved_hosts: Sequence[str],
) -> str | None:
    for image in images:
        raw_url = image["raw_url"]
        host = await _mapped_uploaded_host(raw_url, url_host_mapping)
        if host not in approved_hosts:
            return raw_url
    return None


def _image_output_file(
    meta: Meta, tracker: str, screenshot_path: Path
) -> Path:
    if tracker == "covers":
        return Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json"
    return screenshot_path / "reuploaded_images.json"


async def _existing_image_data(output_file: Path) -> list[dict[str, str]]:
    try:
        async with aiofiles.open(output_file, encoding="utf-8") as file_handle:
            raw = await file_handle.read()
        loaded: object = json.loads(raw) if raw else []
        if isinstance(loaded, list):
            return cast(list[dict[str, str]], loaded)
        logger.info(
            f"[red]Existing data in {output_file} is not a list. Resetting to "
            "an empty list."
        )
    except Exception:
        return []
    return []


def _unique_image_data(
    existing: list[dict[str, str]], images: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        dict(items)
        for items in {tuple(item.items()) for item in existing + images}
    ]


def _add_cover_release_url(meta: Meta, data: list[dict[str, str]]) -> None:
    for image in data:
        if "release_url" not in image:
            image["release_url"] = meta.release_url


def _cleanup_cover_files(meta: Meta, screens: Sequence[str]) -> None:
    deleted = 0
    for screen in screens:
        if _safe_remove(screen):
            deleted += 1
            logger.debug(f"[dim]Deleted cover image file: {screen}[/dim]")
    if deleted and meta.debug:
        logger.info(
            f"[green]Cleaned up {deleted} cover image files after successful upload[/green]"
        )


def _prepare_image_data_for_persistence(
    meta: Meta,
    tracker: str,
    existing: list[dict[str, str]],
    images: list[dict[str, str]],
) -> list[dict[str, str]]:
    updated = _unique_image_data(existing, images)
    if tracker == "covers" and "release_url" in meta:
        _add_cover_release_url(meta, updated)
        logger.info(
            f"[green]Added release URL to {len(updated)} cover images: "
            f"{meta.release_url}"
        )
    return updated


async def _write_image_data(
    output_file: Path, data: list[dict[str, str]]
) -> bool:
    try:
        async with aiofiles.open(output_file, "w", encoding="utf-8") as handle:
            await handle.write(json.dumps(data, indent=4))
        logger.debug(
            f"[green]Successfully updated reuploaded images in {output_file}."
        )
        return True
    except Exception as error:
        logger.error(f"[red]Failed to save reuploaded images: {error}")
        return False


async def _persist_uploaded_images(
    meta: Meta,
    tracker: str,
    screenshot_path: Path,
    screens: list[str],
    images: list[dict[str, str]],
) -> None:
    if not images:
        logger.info("[red]Tracker screenshots are not a valid list.")
        return
    output_file = _image_output_file(meta, tracker, screenshot_path)
    existing = await _existing_image_data(output_file)
    updated = _prepare_image_data_for_persistence(
        meta, tracker, existing, images
    )
    if not await _write_image_data(output_file, updated):
        return
    if tracker == "covers":
        _cleanup_cover_files(meta, screens)


async def _perform_image_upload(
    meta: Meta,
    tracker: str,
    url_host_mapping: dict[str, str],
    approved_hosts: Sequence[str],
    img_host_index: int,
    default_config: Mapping[str, Any],
    takescreens_manager: TakeScreensManager,
    uploadscreens_manager: UploadScreensManager,
) -> tuple[list[dict[str, str]], bool, bool]:
    filename = meta.title
    requested = _requested_screens(meta, default_config)
    path = _image_generation_path(meta)
    screenshot_path = screenshots_dir(meta.base_dir, meta.uuid)
    set_tracker_image_collection(meta, tracker, "screenshots", [])
    logger.debug(f"[yellow]Searching for screenshots in {screenshot_path}...")
    screens = await _collect_existing_screens(
        meta, tracker, filename, requested, screenshot_path
    )
    if tracker == "covers":
        requested = len(screens)
    screens = await _ensure_screens(
        meta, takescreens_manager, screens, requested, path, filename
    )
    if not screens:
        logger.info(
            "[red]No screenshots were generated or found. Please check the "
            "screenshot generation process."
        )
        return [], True, False
    screens = _selected_screens(meta, tracker, screens, requested)
    upload_index, retry_mode = _select_approved_upload_host(
        meta, tracker, default_config, approved_hosts, img_host_index
    )
    if upload_index is None:
        return [], True, retry_mode
    tracker_images = await _upload_approved_screens(
        meta,
        tracker,
        uploadscreens_manager,
        screens,
        upload_index,
        retry_mode,
        approved_hosts,
    )
    invalid_url = await _first_unapproved_url(
        tracker_images, url_host_mapping, approved_hosts
    )
    if invalid_url is not None:
        logger.info(
            f"[red]Unsupported image host detected in URL '{invalid_url}'. "
            "Please use one of the approved image hosts."
        )
        return tracker_images, True, True
    await _persist_uploaded_images(
        meta, tracker, screenshot_path, screens, tracker_images
    )
    return tracker_images, False, retry_mode


async def _handle_image_upload(
    meta: Meta,
    tracker: str,
    url_host_mapping: dict[str, str],
    approved_image_hosts: Sequence[str] | None = None,
    img_host_index: int = 1,
    file: str | None = None,
    default_config: Mapping[str, Any] | None = None,
    takescreens_manager: TakeScreensManager | None = None,
    uploadscreens_manager: UploadScreensManager | None = None,
) -> tuple[list[dict[str, str]], bool, bool]:
    del file
    config, screens_manager, uploader = _require_image_upload_dependencies(
        default_config, takescreens_manager, uploadscreens_manager
    )
    approved_hosts = list(approved_image_hosts or [])
    if meta.skip_imghost_upload:
        return (
            get_tracker_image_collection(meta, tracker, "screenshots"),
            False,
            False,
        )
    original_imghost = meta.imghost
    try:
        return await _perform_image_upload(
            meta,
            tracker,
            url_host_mapping,
            approved_hosts,
            img_host_index,
            config,
            screens_manager,
            uploader,
        )
    finally:
        if original_imghost:
            meta.imghost = original_imghost
