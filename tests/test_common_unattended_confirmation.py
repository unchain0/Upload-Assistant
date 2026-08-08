# ruff: noqa: S101

import asyncio
from unittest.mock import AsyncMock, patch

from src.meta import Meta
from src.trackers.common import Common


def test_common_confirmation_does_not_prompt_when_unattended():
    common = Common({"TRACKERS": {}})

    async def run_checks():
        for confirmed in (False, True):
            meta = Meta(unattended=True, unattended_confirm=confirmed)
            with patch("src.trackers.common.prompt_in_thread", new=AsyncMock(side_effect=AssertionError("interactive prompt called"))):
                assert await common.prompt_user_for_confirmation("Continue?", meta) is confirmed

    asyncio.run(run_checks())
