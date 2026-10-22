from __future__ import annotations

import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.immortalseed import ImmortalSeed

_SCHEDULE = """
<table>
  <tr>
    <td>2 Days Ago</td>
    <td>Yesterday</td>
    <td>Today (Wednesday)</td>
    <td>Tomorrow</td>
  </tr>
  <tr>
    <td>
      <a href="https://immortalseed.me/tvshows.php?show_id=1">Old Show</a>
    </td>
    <td>
      <a href="https://immortalseed.me/tvshows.php?show_id=2">Maigret (2025)</a>
    </td>
    <td>
      <a href="https://immortalseed.me/tvshows.php?show_id=3">Maigret</a>
      <a href="https://immortalseed.me/tvshows.php?show_id=5">UFC 300</a>
    </td>
    <td>
      <a href="https://immortalseed.me/tvshows.php?show_id=4">Tomorrow Show</a>
    </td>
  </tr>
</table>
"""


def _tracker(monkeypatch: pytest.MonkeyPatch) -> ImmortalSeed:
    tracker = ImmortalSeed({"TRACKERS": {"IMMORTALSEED": {}}})

    async def schedule(_meta: Meta) -> str:
        return _SCHEDULE

    monkeypatch.setattr(tracker, "_schedule_html", schedule, raising=False)
    return tracker


def _episode(title: str, **kwargs: object) -> Meta:
    values = {
        "category": "TV",
        "title": title,
        "resolution": "1080p",
        "video_codec": "H.264",
        "unattended": True,
    }
    values.update(kwargs)
    return Meta(**values)


@pytest.mark.asyncio
async def test_immortalseed_skips_a_show_airing_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    allowed = await tracker.get_additional_checks(_episode("Maigret"))
    assert allowed is False


@pytest.mark.asyncio
async def test_immortalseed_allows_a_show_from_two_days_ago(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    allowed = await tracker.get_additional_checks(_episode("Old Show"))
    assert allowed is True


@pytest.mark.asyncio
async def test_immortalseed_allows_2160p_during_the_air_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    allowed = await tracker.get_additional_checks(
        _episode("Maigret", resolution="2160p")
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_immortalseed_allows_xvid_during_the_air_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    allowed = await tracker.get_additional_checks(
        _episode("Maigret", video_codec="XviD")
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_immortalseed_allows_ufc_during_the_air_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _tracker(monkeypatch)
    allowed = await tracker.get_additional_checks(_episode("UFC 300"))
    assert allowed is True
