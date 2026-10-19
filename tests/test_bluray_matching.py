from __future__ import annotations

import copy
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.release import Meta
from src.integrations.external_apis import bluray


def _meta(
    tmp_path: Path, *, interactive: bool = False, debug: bool = True
) -> Meta:
    release_id = "bluray-contract"
    temp = tmp_path / "tmp" / release_id
    temp.mkdir(parents=True)
    (temp / "BD_SUMMARY_00.txt").write_text(
        "\n".join(
            (
                "Subtitle: English / 12.5 kbps",
                "* Subtitle: Spanish / 0.5 kbps",
            )
        ),
        encoding="utf-8",
    )
    return Meta(
        base_dir=str(tmp_path),
        uuid=release_id,
        category="MOVIE",
        title="Example Film",
        name="Example Film 2026 1080p BluRay AVC DTS-HD MA 5.1",
        debug=debug,
        unattended=not interactive,
        unattended_confirm=interactive,
        bluray_score=75,
        bluray_single_score=75,
        discs=[
            {
                "type": "BDMV",
                "bdinfo": {
                    "size": 23.2,
                    "video": [{"codec": "AVC", "res": "1080p"}],
                    "audio": [
                        {
                            "language": "English",
                            "codec": "DTS-HD MA",
                            "channels": "5.1",
                            "sample_rate": "48 kHz",
                            "bit_depth": "24-bit",
                            "bitrate": "3500 kbps",
                        },
                        {
                            "language": "French",
                            "codec": "Dolby Digital Audio",
                            "channels": "2.0",
                            "sample_rate": "48 kHz",
                            "bit_depth": "DN -27 dB",
                            "bitrate": "192 kbps",
                        },
                    ],
                },
            }
        ],
    )


def _release(
    *,
    title: str = "Example Film",
    country: str = "United States",
    publisher: str = "Criterion",
    release_format: str = "BD-25",
    codec: str = "H.264 AVC",
    resolution: str = "1080p",
    audio: list[str] | None = None,
    subtitles: list[str] | None = None,
    include_specs: bool = True,
    include_cover: bool = True,
) -> MutableMapping[str, Any]:
    release: MutableMapping[str, Any] = {
        "title": title,
        "country": country,
        "publisher": publisher,
        "url": f"https://bluray.example/{title.replace(' ', '-').lower()}",
    }
    if include_cover:
        release["cover_images"] = ["https://images.example/cover.jpg"]
    if include_specs:
        release["specs"] = {
            "video": {"codec": codec, "resolution": resolution},
            "audio": audio
            if audio is not None
            else [
                "English DTS-HD MA 5.1 48 kHz 24-bit 3500 kbps",
                "French Dolby Digital 2.0 48 kHz 192 kbps",
            ],
            "subtitles": subtitles if subtitles is not None else ["English"],
            "discs": {"format": release_format},
        }
    return release


def _install_identity_fetch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fetch = AsyncMock(side_effect=lambda release, _meta: release)
    monkeypatch.setattr(bluray, "fetch_release_details", fetch)
    monkeypatch.setattr(bluray, "download_cover_images", AsyncMock())
    return fetch


@pytest.mark.asyncio
async def test_perfect_bluray_release_is_selected_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path)
    release = _release()
    fetch = _install_identity_fetch(monkeypatch)

    result = await bluray.process_all_releases([release], meta)

    assert result == [release]
    assert meta.region == "USA"
    assert meta.distributor == "CRITERION"
    assert meta.release_url == release["url"]
    assert meta.bluray_cover_urls == release["cover_images"]
    fetch.assert_awaited_once_with(release, meta)
    bluray.download_cover_images.assert_awaited_once_with(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "selected"), [(True, True), (False, False)]
)
async def test_single_imperfect_release_honors_interactive_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: bool,
    selected: bool,
) -> None:
    meta = _meta(tmp_path, interactive=True)
    release = _release(
        country="United Kingdom",
        publisher="Arrow",
        release_format="BD",
        codec="HEVC",
        resolution="2160p",
        audio=["English DTS 2.0", "German LPCM mono"],
        subtitles=["German", "French"],
    )
    _install_identity_fetch(monkeypatch)
    monkeypatch.setattr(
        bluray.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: answer
    )

    result = await bluray.process_all_releases([release], meta)

    assert bool(result) is selected
    if selected:
        assert meta.region == "GBR"
        assert meta.distributor == "ARROW"
    else:
        assert result == []


@pytest.mark.asyncio
async def test_multiple_close_releases_can_print_logs_then_select(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, interactive=True)
    first = _release(title="First Edition", release_format="BD")
    second = _release(
        title="Second Edition",
        country="Australia",
        publisher="Umbrella",
        audio=[
            "English DTS-HD MA 5.1 48 kHz 24-bit 3500 kbps",
            "French Dolby Digital stereo 48 kHz 192 kbps",
            "Japanese LPCM 2.0",
        ],
    )
    _install_identity_fetch(monkeypatch)
    answers = iter(("p", "1", "2"))
    monkeypatch.setattr(
        bluray.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers)
    )

    result = await bluray.process_all_releases([first, second], meta)

    assert result == [first, second]
    assert meta.region == "AUS"
    assert meta.distributor == "UMBRELLA"
    assert meta.release_url == second["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("threshold", "expected"), [(50, True), (101, False)])
async def test_unattended_multiple_match_respects_score_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    threshold: int,
    expected: bool,
) -> None:
    meta = _meta(tmp_path)
    meta.bluray_score = threshold
    first = _release(title="One", include_cover=False)
    second = _release(title="Two", country="Canada", include_cover=False)
    _install_identity_fetch(monkeypatch)

    result = await bluray.process_all_releases([first, second], meta)

    assert bool(result) is expected
    if expected:
        assert meta.release_url in {first["url"], second["url"]}
    else:
        assert result == []


@pytest.mark.asyncio
async def test_missing_specs_and_empty_catalog_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = _meta(tmp_path, interactive=True)
    release = _release(include_specs=False, include_cover=False)
    _install_identity_fetch(monkeypatch)
    monkeypatch.setattr(
        bluray.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False
    )

    assert await bluray.process_all_releases([], meta) == []
    assert (
        await bluray.process_all_releases([copy.deepcopy(release)], meta) == []
    )


def test_country_mapping_and_section_extraction_cover_unknown_and_mixed_nodes() -> (
    None
):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        '<td><span class="subheading">Video</span> AVC <b>1080p</b><span class="subheading">Audio</span>DTS</td>',
        "html.parser",
    )
    cell = soup.find("td")
    assert bluray.extract_section(cell, "Video") == " AVC 1080p"
    assert bluray.extract_section(cell, "Missing") is None
    assert bluray.map_country_to_region_code("Brazil") == "BRA"
    assert bluray.map_country_to_region_code("Unknownland") is None
