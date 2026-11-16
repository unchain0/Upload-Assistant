import asyncio
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import pytest

from src.domain_models.release import Meta
from src.integrations.image_hosts.uploader import (
    UploadScreensManager,
    _build_image_start_limiter,
    _summarize_host_error,
    _upload_screens,
)


def test_image_start_limiter_staggers_concurrent_starts() -> None:
    async def exercise() -> list[float]:
        limiter = _build_image_start_limiter(0.02)
        starts: list[float] = []

        async def start_upload() -> None:
            await limiter()
            starts.append(asyncio.get_running_loop().time())

        await asyncio.gather(*(start_upload() for _ in range(3)))
        return starts

    starts = sorted(asyncio.run(exercise()))
    assert all(later - earlier >= 0.015 for earlier, later in pairwise(starts))


def test_image_host_error_does_not_dump_html_response() -> None:
    html = "<!DOCTYPE html><html><head><title>We're sorry, but something went wrong (500)</title></head><body>internal details</body></html>"

    summary = _summarize_host_error(html)

    assert summary == "HTTP 500: remote service error"
    assert "<!DOCTYPE" not in summary


def test_upload_screens_does_not_reupload_source_on_fallback(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, str]:
        image = str(args[0]) if isinstance(args, list) else ""
        calls.append(image)
        return {
            "status": "success",
            "img_url": f"https://img.example/{len(calls)}.png",
            "raw_url": f"https://img.example/{len(calls)}.png",
            "web_url": f"https://img.example/{len(calls)}.png",
        }

    async def exercise() -> None:
        image_path = tmp_path / "image-1.png"
        image_path.write_bytes(b"image")
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "imgbox"}
        )
        config = {
            "DEFAULT": {
                "img_host_1": "imgbox",
                "img_host_2": "ptscreens",
                "image_upload_concurrency": 1,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        shared_return_dict: dict[str, object] = {}
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.Path.cwd",
                return_value=tmp_path,
            ),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            await _upload_screens(
                config, meta, 1, 1, 0, 1, [], shared_return_dict
            )
            meta.image_list = []
            meta.imghost = "ptscreens"
            await _upload_screens(
                config, meta, 1, 2, 0, 1, [], shared_return_dict
            )

    asyncio.run(exercise())
    assert calls == ["image-1.png"]


def test_upload_screens_preserves_partial_successes_across_fallback(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_upload(args: object) -> dict[str, str]:
        assert isinstance(args, list)
        image = str(args[0])
        host = str(args[1])
        filename = Path(image).name
        calls.append((filename, host))
        if filename == "image-2.png" and host == "imgbox":
            return {"status": "failed", "reason": "duplicate image"}
        return {
            "status": "success",
            "img_url": f"https://img.example/{filename}/{host}",
            "raw_url": f"https://img.example/{filename}/{host}",
            "web_url": f"https://img.example/{filename}/{host}",
        }

    async def exercise() -> tuple[list[dict[str, str]], int]:
        for filename in ("image-1.png", "image-2.png"):
            (tmp_path / filename).write_bytes(b"image")
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "imgbox"}
        )
        config = {
            "DEFAULT": {
                "img_host_1": "imgbox",
                "img_host_2": "ptscreens",
                "image_upload_concurrency": 1,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.Path.cwd",
                return_value=tmp_path,
            ),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            return await _upload_screens(config, meta, 1, 1, 0, 2, [], {})

    image_list, uploaded_count = asyncio.run(exercise())
    assert uploaded_count == 2
    assert len(image_list) == 2
    assert calls == [
        ("image-1.png", "imgbox"),
        ("image-2.png", "imgbox"),
        ("image-2.png", "ptscreens"),
    ]


def test_upload_screens_handles_infinite_concurrency(tmp_path: Path) -> None:
    async def fake_upload(_: object) -> dict[str, str]:
        return {
            "status": "success",
            "img_url": "https://img.example/image.png",
            "raw_url": "https://img.example/image.png",
            "web_url": "https://img.example/image.png",
        }

    async def exercise() -> tuple[list[dict[str, str]], int]:
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "imgbox"}
        )
        config = {
            "DEFAULT": {
                "img_host_1": "imgbox",
                "image_upload_concurrency": float("inf"),
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            return await _upload_screens(
                config, meta, 1, 1, 0, 1, ["image.png"], {}
            )

    result = asyncio.run(exercise())
    assert result[1] == 1


@pytest.mark.parametrize(
    ("configured_delay", "expected_delay"),
    [
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        (float("nan"), 0.0),
        (0.75, 0.75),
    ],
)
def test_upload_screens_normalizes_image_upload_delay_before_limiter(
    tmp_path: Path,
    configured_delay: float,
    expected_delay: float,
) -> None:
    async def fake_upload(_: object) -> dict[str, str]:
        return {
            "status": "success",
            "img_url": "https://img.example/image.png",
            "raw_url": "https://img.example/image.png",
            "web_url": "https://img.example/image.png",
        }

    async def exercise() -> tuple[list[float], int]:
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "imgbox"}
        )
        config = {
            "DEFAULT": {
                "img_host_1": "imgbox",
                "image_upload_concurrency": 1,
                "image_upload_delay": configured_delay,
            },
            "TRACKERS": {},
        }
        captured_delays: list[float] = []

        def fake_build_image_start_limiter(delay: float):
            captured_delays.append(delay)

            async def wait_for_start_slot() -> None:
                return None

            return wait_for_start_slot

        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
            patch(
                "src.integrations.image_hosts.uploader._build_image_start_limiter",
                side_effect=fake_build_image_start_limiter,
            ),
        ):
            return captured_delays, (
                await _upload_screens(
                    config, meta, 1, 1, 0, 1, ["image.png"], {}
                )
            )[1]

    delays, uploaded_count = asyncio.run(exercise())
    assert delays == [expected_delay]
    assert uploaded_count == 1


def test_upload_screens_propagates_cancellation(tmp_path: Path) -> None:
    async def blocked_upload(_: object) -> dict[str, str]:
        await asyncio.Event().wait()
        return {"status": "failed", "reason": "unreachable"}

    async def exercise() -> None:
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "onlyimage"}
        )
        config = {
            "DEFAULT": {
                "img_host_1": "onlyimage",
                "image_upload_concurrency": 1,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=blocked_upload,
            ),
        ):
            task = asyncio.create_task(
                _upload_screens(config, meta, 1, 1, 0, 1, ["image.png"], {})
            )
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(exercise())


def test_upload_manager_retries_payload_specific_failure_for_next_release(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, str]:
        assert isinstance(args, list)
        host = str(args[1])
        calls.append(host)
        if host == "onlyimage":
            return {"status": "failed", "reason": "No URLs received"}
        return {
            "status": "success",
            "img_url": "https://img.test/1",
            "raw_url": "https://img.test/1",
            "web_url": "https://img.test/1",
        }

    async def exercise() -> None:
        config = {
            "DEFAULT": {
                "img_host_1": "onlyimage",
                "img_host_2": "imgbb",
                "image_upload_concurrency": 1,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        manager = UploadScreensManager(config)
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            first = Meta(
                {
                    "base_dir": str(tmp_path),
                    "uuid": "one",
                    "imghost": "onlyimage",
                }
            )
            await manager.upload_screens(
                first, 1, 1, 0, 1, ["image.png"], {}, max_retries=0
            )
            second = Meta(
                {
                    "base_dir": str(tmp_path),
                    "uuid": "two",
                    "imghost": "onlyimage",
                }
            )
            await manager.upload_screens(
                second, 1, 1, 0, 1, ["image.png"], {}, max_retries=0
            )

    asyncio.run(exercise())
    assert calls == ["onlyimage", "imgbb", "onlyimage", "imgbb"]


def test_imgbox_outage_opens_circuit_after_first_failed_image(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, object]:
        assert isinstance(args, list)
        host = str(args[1])
        calls.append(host)
        if host == "imgbox":
            return {
                "status": "failed",
                "reason": "HTTP 500: remote service error",
                "host_unavailable": True,
            }
        return {
            "status": "success",
            "img_url": f"https://img.test/{len(calls)}.png",
            "raw_url": f"https://img.test/{len(calls)}.png",
            "web_url": f"https://img.test/{len(calls)}.png",
        }

    async def exercise() -> tuple[list[dict[str, str]], int]:
        config = {
            "DEFAULT": {
                "img_host_1": "imgbox",
                "img_host_2": "imgbb",
                "image_upload_concurrency": 0,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "imgbox"}
        )
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            return await _upload_screens(
                config,
                meta,
                3,
                1,
                0,
                3,
                ["one.png", "two.png", "three.png"],
                {},
                max_retries=3,
                unavailable_hosts=set(),
            )

    image_list, uploaded_count = asyncio.run(exercise())
    assert uploaded_count == 3
    assert len(image_list) == 3
    assert calls.count("imgbox") == 1
    assert calls.count("imgbb") == 3


def test_upload_screens_does_not_retry_ambiguous_onlyimage_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, str]:
        assert isinstance(args, list)
        host = str(args[1])
        calls.append(host)
        if host == "onlyimage":
            return {
                "status": "failed",
                "reason": "OnlyImage upload outcome unknown after timeout",
            }
        return {
            "status": "success",
            "img_url": "https://img.test/image.png",
            "raw_url": "https://img.test/image.png",
            "web_url": "https://img.test/image.png",
        }

    async def exercise() -> tuple[list[dict[str, str]], int]:
        config = {
            "DEFAULT": {
                "img_host_1": "onlyimage",
                "img_host_2": "imgbb",
                "image_upload_concurrency": 1,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "test", "imghost": "onlyimage"}
        )
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            return await _upload_screens(
                config, meta, 1, 1, 0, 1, ["image.png"], {}, max_retries=3
            )

    image_list, uploaded_count = asyncio.run(exercise())
    assert uploaded_count == 1
    assert len(image_list) == 1
    assert calls == ["onlyimage", "imgbb"]


def test_configured_image_hosts_continue_after_imgbb_rate_limit_and_imgbox_outage(
    tmp_path: Path,
) -> None:
    """Regression for a batch that previously stopped after imgbb and imgbox."""

    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, object]:
        assert isinstance(args, list)
        image = Path(str(args[0])).name
        host = str(args[1])
        calls.append(host)
        if host == "imgbb":
            return {
                "status": "failed",
                "reason": "imgbb upload failed (HTTP 400): Rate limit reached.",
                "host_unavailable": True,
                "retryable": False,
            }
        if host in {"onlyimage", "pixhost"}:
            return {
                "status": "failed",
                "reason": f"{host} rejected this payload",
                "retryable": False,
            }
        if host == "imgbox":
            return {
                "status": "failed",
                "reason": "HTTP 500: remote service error",
                "host_unavailable": True,
                "retryable": True,
            }
        assert host == "zipline"
        url = f"https://zip.example/{image}"
        return {
            "status": "success",
            "img_url": url,
            "raw_url": url,
            "web_url": url,
        }

    async def exercise() -> tuple[list[dict[str, str]], int]:
        config = {
            "DEFAULT": {
                "img_host_1": "onlyimage",
                "img_host_2": "pixhost",
                "img_host_3": "imgbb",
                "img_host_4": "imgbox",
                "img_host_5": "zipline",
                "img_host_6": "",
                "image_upload_concurrency": 0,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        meta = Meta(
            {"base_dir": str(tmp_path), "uuid": "release", "imghost": "imgbb"}
        )
        images = [f"image-{index}.png" for index in range(4)]
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            return await _upload_screens(
                config,
                meta,
                4,
                3,
                0,
                4,
                images,
                {},
                max_retries=0,
                unavailable_hosts=set(),
            )

    uploaded, count = asyncio.run(exercise())

    assert count == 4
    assert len(uploaded) == 4
    assert calls.count("imgbb") == 1
    assert calls.count("imgbox") == 1
    assert calls.count("zipline") == 4
    assert calls[-4:] == ["zipline"] * 4


def test_upload_manager_opens_run_level_circuit_after_rate_limit(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_upload(args: object) -> dict[str, object]:
        assert isinstance(args, list)
        host = str(args[1])
        calls.append(host)
        if host == "imgbb":
            return {
                "status": "failed",
                "reason": "imgbb upload failed (HTTP 400): Rate limit reached",
                "host_unavailable": True,
                "retryable": False,
            }
        return {
            "status": "success",
            "img_url": f"https://imgbox.test/{len(calls)}.png",
            "raw_url": f"https://imgbox.test/{len(calls)}.png",
            "web_url": f"https://imgbox.test/{len(calls)}.png",
            "local_file_path": str(tmp_path / "image.png"),
        }

    async def exercise() -> None:
        image = tmp_path / "image.png"
        image.write_bytes(b"image")
        config = {
            "DEFAULT": {
                "img_host_1": "imgbb",
                "img_host_2": "imgbox",
                "image_upload_concurrency": 4,
                "image_upload_delay": 0,
            },
            "TRACKERS": {},
        }
        manager = UploadScreensManager(config)
        with (
            patch(
                "src.integrations.image_hosts.uploader.screenshots_dir",
                return_value=tmp_path,
            ),
            patch("src.integrations.image_hosts.uploader.os.chdir"),
            patch(
                "src.integrations.image_hosts.uploader.upload_image_task",
                new=fake_upload,
            ),
        ):
            first = Meta(
                {
                    "base_dir": str(tmp_path),
                    "uuid": "first",
                    "imghost": "imgbb",
                }
            )
            await manager.upload_screens(
                first, 1, 1, 0, 1, [str(image)], {}, max_retries=0
            )
            second = Meta(
                {
                    "base_dir": str(tmp_path),
                    "uuid": "second",
                    "imghost": "imgbb",
                }
            )
            await manager.upload_screens(
                second, 1, 1, 0, 1, [str(image)], {}, max_retries=0
            )

        assert manager.unavailable_hosts == {"imgbb"}

    asyncio.run(exercise())
    assert calls == ["imgbb", "imgbox", "imgbox"]
