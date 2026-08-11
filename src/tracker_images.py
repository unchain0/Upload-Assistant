"""Tracker-scoped image collections used during concurrent uploads."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from src.meta import Meta

ImageCollection = Literal["screenshots", "menu_images", "spectrograms_images"]
ImageDict = dict[str, Any]

_BASE_COLLECTION_FIELDS: dict[ImageCollection, str] = {
    "screenshots": "image_list",
    "menu_images": "menu_images",
    "spectrograms_images": "spectrograms_images",
}


def get_tracker_image_collection(meta: Meta, tracker: str, collection: ImageCollection) -> list[Any]:
    """Return a tracker override or the release-wide collection as fallback."""
    tracker_collections = cast(dict[str, list[Any]], meta.tracker_image_collections.get(tracker, {}))
    if collection in tracker_collections:
        return tracker_collections[collection]
    images = getattr(meta, _BASE_COLLECTION_FIELDS[collection])
    return cast(list[Any], images) if isinstance(images, list) else []


def configured_screenshot_minimum(config: dict[str, Any]) -> int:
    default_config_value = config.get("DEFAULT", {})
    if not isinstance(default_config_value, dict):
        return 0
    default_config = cast(dict[str, Any], default_config_value)
    try:
        return max(0, int(default_config.get("min_successful_image_uploads", 3)))
    except (TypeError, ValueError):
        return 3


def valid_screenshot_count(meta: Meta, tracker: str | None = None) -> int:
    images = get_tracker_image_collection(meta, tracker, "screenshots") if tracker else meta.image_list
    if not isinstance(images, list):
        return 0

    count = 0
    for image in images:
        if isinstance(image, str):
            urls = (image,)
        elif isinstance(image, Mapping):
            image_mapping = cast(Mapping[str, Any], image)
            urls = (image_mapping.get("raw_url"), image_mapping.get("img_url"), image_mapping.get("web_url"))
        else:
            continue
        if any(isinstance(url, str) and url.startswith(("http://", "https://")) for url in urls):
            count += 1
    return count


def screenshot_requirement_error(meta: Meta, config: dict[str, Any], tracker: str | None = None) -> str | None:
    if meta.category not in {"MOVIE", "TV"}:
        return None
    required = configured_screenshot_minimum(config)
    actual = valid_screenshot_count(meta, tracker)
    if actual < required:
        scope = f" for {tracker}" if tracker else ""
        return f"Minimum of {required} successful screenshot uploads required{scope}, but only {actual} valid screenshot(s) are available."
    return None


def has_tracker_image_collection(meta: Meta, tracker: str, collection: ImageCollection) -> bool:
    """Return whether a tracker has an explicit collection override."""
    return collection in meta.tracker_image_collections.get(tracker, {})


def set_tracker_image_collection(meta: Meta, tracker: str, collection: ImageCollection, images: Sequence[ImageDict]) -> None:
    """Store a tracker-local image collection without mutating shared metadata."""
    meta.tracker_image_collections.setdefault(tracker, {})[collection] = [dict(image) for image in images]
