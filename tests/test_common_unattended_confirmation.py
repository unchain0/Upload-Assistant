import asyncio
from unittest.mock import AsyncMock, patch

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common


def test_common_confirmation_does_not_prompt_when_unattended():
    common = Common({"TRACKERS": {}})

    async def run_checks():
        for confirmed in (False, True):
            meta = Meta(unattended=True, unattended_confirm=confirmed)
            with patch(
                "src.integrations.trackers.common.prompt_in_thread",
                new=AsyncMock(
                    side_effect=AssertionError("interactive prompt called")
                ),
            ):
                assert (
                    await common.prompt_user_for_confirmation(
                        "Continue?", meta
                    )
                    is confirmed
                )

    asyncio.run(run_checks())


def test_common_adult_confirmation_uses_async_prompt_only_when_attended():
    common = Common({"TRACKERS": {}})

    async def run_checks():
        with patch.object(
            common,
            "prompt_user_for_confirmation",
            new=AsyncMock(return_value=True),
        ) as prompt:
            assert (
                await common.check_and_confirm_adult_media_upload(
                    Meta(adult_media=True), "TEST"
                )
                is True
            )
            prompt.assert_awaited_once()

        with patch.object(
            common,
            "prompt_user_for_confirmation",
            new=AsyncMock(
                side_effect=AssertionError("interactive prompt called")
            ),
        ):
            assert (
                await common.check_and_confirm_adult_media_upload(
                    Meta(
                        adult_media=True,
                        unattended=True,
                        unattended_confirm=True,
                    ),
                    "TEST",
                )
                is True
            )

    asyncio.run(run_checks())


def test_common_tv_patterns_ignore_markers_embedded_in_codec_tokens():
    assert Common.extract_tv_seasons(["Movie.DTS5.1.mkv"]) == set()
    assert Common.count_tv_episodes(["Movie.DTS5E1.mkv"]) == 0
    assert Common.extract_tv_seasons(["Show.S01E02.mkv"]) == {1}
    assert Common.count_tv_episodes(["Show.S01E02.mkv"]) == 1
    assert Common.extract_tv_seasons(["Show_S01E02.mkv"]) == {1}
    assert Common.count_tv_episodes(["Show_S01E02.mkv"]) == 1
    assert Common.count_tv_episodes(["Show.S01E01E02E03.mkv"]) == 3
    assert Common.count_tv_episodes(["Show.S01E01-E02.mkv"]) == 2


def test_common_portuguese_description_rejects_ambiguous_english_and_spanish_words():
    common = Common({"TRACKERS": {}})

    assert (
        common.is_portuguese_description(
            "For us, as requested, this is ready for release."
        )
        is False
    )
    assert (
        common.is_portuguese_description(
            "Por que se publica por separado y se comparte."
        )
        is False
    )
    assert (
        common.is_portuguese_description(
            "Esta descrição contém informações válidas para o lançamento."
        )
        is True
    )
