"""Tracker-scoped image collections used during concurrent uploads."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from src.domain_models.release import Meta

ImageCollection = Literal[
    "screenshots",
    "menu_images",
    "spectrograms_images",
    "dynamic_hdr_plot_images",
]
ImageDict = dict[str, Any]

_BASE_COLLECTION_FIELDS: dict[ImageCollection, str] = {
    "screenshots": "image_list",
    "menu_images": "menu_images",
    "spectrograms_images": "spectrograms_images",
    "dynamic_hdr_plot_images": "dynamic_hdr_plot_images",
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
    except TypeError, ValueError:
        return 3


def valid_screenshot_count(meta: Meta, tracker: str | None = None) -> int:
    images = _screenshot_images(meta, tracker)
    return sum(1 for image in images if _image_has_hosted_url(image))


def _screenshot_images(meta: Meta, tracker: str | None) -> list[Any]:
    images = get_tracker_image_collection(meta, tracker, "screenshots") if tracker else meta.image_list
    return cast(list[Any], images) if isinstance(images, list) else []


def _image_has_hosted_url(image: Any) -> bool:
    return any(_is_http_url(url) for url in _image_urls(image))


def _image_urls(image: Any) -> tuple[Any, ...]:
    if isinstance(image, str):
        return (image,)
    if not isinstance(image, Mapping):
        return ()
    image_mapping = cast(Mapping[str, Any], image)
    return (
        image_mapping.get("raw_url"),
        image_mapping.get("img_url"),
        image_mapping.get("web_url"),
    )


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def screenshot_requirement_error(
    meta: Meta,
    config: dict[str, Any],
    tracker: str | None = None,
    *,
    local_available: int | None = None,
) -> str | None:
    if meta.category not in {"MOVIE", "TV"}:
        return None
    required = configured_screenshot_minimum(config)
    actual = valid_screenshot_count(meta, tracker)
    if actual < required:
        scope = f" for {tracker}" if tracker else ""
        if local_available is not None:
            local_count = max(0, int(local_available))
            return f"{local_count} local screenshot(s) available, {actual} successfully hosted; minimum hosted required{scope}: {required}."
        return f"Minimum of {required} successfully hosted screenshots required{scope}, but only {actual} hosted screenshot(s) are available."
    return None


def has_tracker_image_collection(meta: Meta, tracker: str, collection: ImageCollection) -> bool:
    """Return whether a tracker has an explicit collection override."""
    return collection in meta.tracker_image_collections.get(tracker, {})


def set_tracker_image_collection(
    meta: Meta,
    tracker: str,
    collection: ImageCollection,
    images: Sequence[ImageDict],
) -> None:
    """Store a tracker-local image collection without mutating shared
    metadata.
    """
    meta.tracker_image_collections.setdefault(tracker, {})[collection] = [dict(image) for image in images]
