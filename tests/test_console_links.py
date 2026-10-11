import asyncio

from src.integrations.observability.console import (
    buffer_console_logs,
    prompt_in_thread,
)


def test_prompt_in_thread_returns_prompt_result() -> None:
    async def ask() -> str:
        return await prompt_in_thread(
            lambda prefix, value: f"{prefix}{value}", "answer-", 42
        )

    assert asyncio.run(ask()) == "answer-42"


def test_buffer_console_logs_can_contend_across_consecutive_event_loops() -> (
    None
):
    async def contend_for_buffer() -> None:
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        order: list[str] = []

        async def first_user() -> None:
            async with buffer_console_logs():
                order.append("first-entered")
                first_entered.set()
                await release_first.wait()
                order.append("first-leaving")

        async def second_user() -> None:
            await first_entered.wait()
            second_started.set()
            async with buffer_console_logs():
                order.append("second-entered")

        first_task = asyncio.create_task(first_user())
        second_task = asyncio.create_task(second_user())
        await second_started.wait()
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first_task, second_task)

        assert order == ["first-entered", "first-leaving", "second-entered"]

    asyncio.run(contend_for_buffer())
    asyncio.run(contend_for_buffer())
