# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from src.meta import Meta
from src.tracker_images import screenshot_requirement_error, valid_screenshot_count


def _image(number: int) -> dict[str, str]:
    return {"raw_url": f"https://images.example/{number}.png"}


def test_configured_screenshot_minimum_is_not_reduced_to_available_images() -> None:
    meta = Meta(category="TV", image_list=[_image(1)])
    config = {"DEFAULT": {"min_successful_image_uploads": "4"}}

    assert screenshot_requirement_error(meta, config) == "Minimum of 4 successful screenshot uploads required, but only 1 valid screenshot(s) are available."


def test_configured_screenshot_minimum_allows_exact_count() -> None:
    meta = Meta(category="MOVIE", image_list=[_image(number) for number in range(4)])
    config = {"DEFAULT": {"min_successful_image_uploads": "4"}}

    assert screenshot_requirement_error(meta, config) is None


def test_tracker_override_is_validated_instead_of_global_images() -> None:
    meta = Meta(category="TV", image_list=[_image(number) for number in range(4)])
    meta.tracker_image_collections["TEST"] = {"screenshots": [_image(1)]}
    config = {"DEFAULT": {"min_successful_image_uploads": "4"}}

    assert valid_screenshot_count(meta, "TEST") == 1
    assert screenshot_requirement_error(meta, config, "TEST") == "Minimum of 4 successful screenshot uploads required for TEST, but only 1 valid screenshot(s) are available."


def test_non_video_categories_do_not_require_screenshots() -> None:
    config = {"DEFAULT": {"min_successful_image_uploads": "4"}}

    assert screenshot_requirement_error(Meta(category="MUSIC"), config) is None
    assert screenshot_requirement_error(Meta(category="GAME"), config) is None
