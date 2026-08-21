from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import upload
from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta


def _meta(**values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": ".",
        "uuid": "release",
        "path": "/media/release.mkv",
        "category": "MOVIE",
        "debug": False,
        "is_disc": "",
        "menu_images": [],
        "path_to_menu_screenshots": "",
        "audio_spectrogram": False,
        "audio_spectrogram_tracks": [],
        "audiobook": False,
    }
    state.update(values)
    return Meta(state)


@pytest.mark.asyncio
async def test_mandatory_hosted_screenshots_block_optional_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optional = AsyncMock()
    monkeypatch.setattr(upload, "available_screens", lambda *_args: (4, 4))
    monkeypatch.setattr(
        upload,
        "screenshot_requirement_error",
        lambda *_args, **_kwargs: "4 local screenshot(s) available, 0 successfully hosted; minimum hosted required: 4.",
    )
    monkeypatch.setattr(upload, "_process_optional_image_artifacts", optional)

    with pytest.raises(ItemProcessingError, match="0 successfully hosted"):
        await upload._validate_screenshots_then_process_optional(
            _meta(),
            {"DEFAULT": {"min_successful_image_uploads": 4}},
            AsyncMock(),
        )

    optional.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_artifacts_run_only_after_screenshot_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def requirement(*_args: object, **_kwargs: object) -> None:
        events.append("validated")
        return

    async def optional(*_args: object, **_kwargs: object) -> None:
        events.append("optional")

    monkeypatch.setattr(upload, "available_screens", lambda *_args: (4, 4))
    monkeypatch.setattr(upload, "screenshot_requirement_error", requirement)
    monkeypatch.setattr(upload, "_process_optional_image_artifacts", optional)

    await upload._validate_screenshots_then_process_optional(_meta(), {"DEFAULT": {"min_successful_image_uploads": 4}}, AsyncMock())

    assert events == ["validated", "optional"]


@pytest.mark.asyncio
async def test_debug_local_screenshot_failure_blocks_optional_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optional = AsyncMock()
    monkeypatch.setattr(upload, "available_screens", lambda *_args: (3, 4))
    monkeypatch.setattr(upload, "_process_optional_image_artifacts", optional)

    with pytest.raises(ItemProcessingError, match="3 were captured"):
        await upload._validate_screenshots_then_process_optional(
            _meta(debug=True),
            {"DEFAULT": {"min_successful_image_uploads": 4}},
            AsyncMock(),
        )

    optional.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_spectrogram_failure_does_not_abort_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spectrogram = AsyncMock(side_effect=RuntimeError("host unavailable"))
    monkeypatch.setattr(upload, "process_audio_spectrograms", spectrogram)
    monkeypatch.setattr(upload, "dynamic_hdr_plot_enabled", lambda *_args: False)

    await upload._process_optional_image_artifacts(
        _meta(audio_spectrogram=True),
        {"DEFAULT": {"add_audio_spectrogram": True}},
        AsyncMock(),
    )

    spectrogram.assert_awaited_once()


@pytest.mark.asyncio
async def test_optional_disc_menu_and_hdr_failures_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    menus = AsyncMock(side_effect=RuntimeError("menu host failed"))
    hdr = AsyncMock(side_effect=RuntimeError("hdr host failed"))
    monkeypatch.setattr(upload, "process_disc_menus", menus)
    monkeypatch.setattr(upload, "dynamic_hdr_plot_enabled", lambda *_args: True)
    monkeypatch.setattr(upload, "process_dynamic_hdr_plots", hdr)

    await upload._process_optional_image_artifacts(
        _meta(is_disc="BDMV", path_to_menu_screenshots="requested"),
        {"DEFAULT": {"auto_dvd_menus": True}},
        AsyncMock(),
    )

    menus.assert_awaited_once()
    hdr.assert_awaited_once()
