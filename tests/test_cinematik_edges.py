from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import cinematik as cinematik_module
from src.integrations.trackers.UNIT3D.cinematik import Cinematik


def _tracker() -> Cinematik:
    return Cinematik({"DEFAULT": {}, "TRACKERS": {"CINEMATIK": {}}})


def _meta(tmp_path: Path | None = None, **values: object) -> Meta:
    base_dir = str(tmp_path or Path())
    state: dict[str, object] = {
        "base_dir": base_dir,
        "uuid": "release",
        "category": "MOVIE",
        "type": "DISC",
        "is_disc": "BDMV",
        "title": "Film",
        "aka": "",
        "year": 2025,
        "search_year": 2025,
        "season": "S01",
        "disctype": "BD50",
        "resolution": "1080p",
        "video_codec": "AVC",
        "three_d": "",
        "source": "NTSC DVD",
        "dvd_size": "DVD9",
        "foreign": False,
        "opera": False,
        "asian": False,
        "description_link": "",
        "description_file": "",
        "discs": [],
        "region": "USA",
        "tmdb_poster_path": "/poster.jpg",
        "image_list": [],
        "overview": "Overview",
        "bdinfo": {},
        "imdb_info": {"imdb_url": "https://imdb.invalid/title/tt1/"},
        "imdb_rating": "8.0",
        "distributor": "Criterion",
        "distributor_link": "https://criterion.invalid",
        "untouched": False,
        "uploader_comments": "Notes",
        "unattended": True,
        "unattended_confirm": False,
    }
    state.update(values)
    return Meta(state)


def test_cinematik_rejects_non_disc_and_accepts_disc() -> None:
    tracker = _tracker()
    assert not asyncio.run(tracker.get_additional_checks(_meta(is_disc="")))
    assert asyncio.run(tracker.get_additional_checks(_meta(is_disc="BDMV")))


def test_cinematik_additional_data_delegates_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "get_flag", AsyncMock(return_value="1"))
    assert asyncio.run(tracker.get_additional_data(_meta())) == {"mod_queue_opt_in": "1"}


def test_cinematik_movie_name_variants() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_name(_meta())) == {"name": "Film (2025) BD50 1080p AVC"}
    assert asyncio.run(tracker.get_name(_meta(three_d="3D"))) == {"name": "Film (2025) BD50 1080p AVC [3D]"}
    assert asyncio.run(tracker.get_name(_meta(is_disc="DVD"))) == {"name": "Film (2025) NTSC DVD DVD9"}
    assert tracker._film_disc_name(_meta(is_disc="UNKNOWN")) == ""


def test_cinematik_tv_and_unknown_names() -> None:
    tracker = _tracker()
    tv = _meta(category="TV", is_disc="BDMV", search_year=2024, year=2025)
    assert asyncio.run(tracker.get_name(tv)) == {"name": "Film (2024) S01 BD50 1080p AVC"}
    dvd = _meta(category="TV", is_disc="DVD", search_year="", year=2025)
    assert asyncio.run(tracker.get_name(dvd)) == {"name": "Film (2025) S01 NTSC DVD DVD9"}
    assert tracker._tv_disc_name(_meta(category="TV", is_disc="UNKNOWN")) == ""
    assert asyncio.run(tracker.get_name(_meta(category="OTHER"))) == {"name": ""}


def test_cinematik_title_identity_and_categories() -> None:
    tracker = _tracker()
    meta = _meta(title="Film AKA Test", aka="AKA Alt")
    assert tracker._title_identity(meta, "2025") == "Film / Test / Alt (2025)"
    assert asyncio.run(tracker.get_category_id(_meta(foreign=True))) == {"category_id": "3"}
    assert asyncio.run(tracker.get_category_id(_meta(opera=True))) == {"category_id": "5"}
    assert asyncio.run(tracker.get_category_id(_meta(asian=True))) == {"category_id": "6"}
    assert asyncio.run(tracker.get_category_id(_meta(category="TV", foreign=True))) == {"category_id": "4"}
    assert asyncio.run(tracker.get_category_id(_meta(category="TV", opera=True))) == {"category_id": "5"}
    assert asyncio.run(tracker.get_category_id(_meta(category="TV"))) == {"category_id": "2"}
    assert asyncio.run(tracker.get_category_id(_meta(category="FILM"))) == {"category_id": "1"}
    assert asyncio.run(tracker.get_category_id(_meta(category="OTHER"))) == {"category_id": "0"}


def test_cinematik_type_and_resolution_ids() -> None:
    tracker = _tracker()
    assert asyncio.run(tracker.get_type_id(_meta(disctype="BD100"))) == {"type_id": "3"}
    assert asyncio.run(tracker.get_type_id(_meta(disctype=["NTSC DVD5"]))) == {"type_id": "8"}
    assert asyncio.run(tracker.get_type_id(_meta(disctype="Unknown"))) == {"type_id": "1"}
    with pytest.raises(ValueError, match="disctype is required"):
        asyncio.run(tracker.get_type_id(_meta(disctype="")))
    assert asyncio.run(tracker.get_resolution_id(_meta(resolution="2160p"))) == {"resolution_id": "2"}
    assert asyncio.run(tracker.get_resolution_id(_meta(resolution="Unknown"))) == {"resolution_id": "10"}


def test_cinematik_total_bitrate_and_screenshots() -> None:
    assert Cinematik._total_bitrate([]) == "Unknown"
    assert Cinematik._total_bitrate([{"summary": "No bitrate"}]) == "Unknown"
    assert Cinematik._total_bitrate([{"summary": "Total Bitrate: 35.5 Mbps"}]) == "35.5 Mbps"
    urls = Cinematik._screenshot_urls(_meta(image_list=[{"raw_url": "a"}, "bad", {"raw_url": "b"}]))
    assert urls[:2] == ["a", "b"]
    assert len(urls) == 6


def test_cinematik_poster_candidates_existing_and_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    existing = Cinematik._poster_candidates(meta)[0]
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"poster")
    assert tracker._existing_poster_path(meta) == existing

    existing.unlink()
    response = httpx.Response(200, request=httpx.Request("GET", "https://image.invalid/poster.jpg"), content=b"downloaded")
    monkeypatch.setattr(tracker, "_poster_response", AsyncMock(return_value=response))
    downloaded = asyncio.run(tracker._download_poster(meta, "https://image.invalid/poster.jpg"))
    assert downloaded is not None
    assert downloaded.read_bytes() == b"downloaded"


def test_cinematik_download_poster_failure_and_url_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_poster_response", AsyncMock(side_effect=httpx.RequestError("offline")))
    assert asyncio.run(tracker._download_poster(_meta(tmp_path), "https://image.invalid/poster.jpg")) is None
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        Cinematik._validate_poster_url("file:///tmp/poster.jpg")


def test_cinematik_rehost_poster_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    poster = tmp_path / "poster.jpg"
    poster.write_bytes(b"poster")
    monkeypatch.setattr(tracker.uploadscreens_manager, "upload_screens", AsyncMock(return_value=([{"raw_url": "https://host.invalid/poster.jpg"}], {})))
    assert asyncio.run(tracker._rehost_poster(_meta(tmp_path), poster, "fallback")) == "https://host.invalid/poster.jpg"
    monkeypatch.setattr(tracker.uploadscreens_manager, "upload_screens", AsyncMock(side_effect=ValueError("bad")))
    assert asyncio.run(tracker._rehost_poster(_meta(tmp_path), poster, "fallback")) == "fallback"
    assert tracker._first_raw_url([], "fallback") == "fallback"
    assert tracker._first_raw_url(["bad"], "fallback") == "fallback"


def test_cinematik_poster_url_existing_and_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path)
    poster = Cinematik._poster_candidates(meta)[1]
    poster.parent.mkdir(parents=True, exist_ok=True)
    poster.write_bytes(b"poster")
    monkeypatch.setattr(tracker, "_rehost_poster", AsyncMock(return_value="https://host.invalid/poster.jpg"))
    assert asyncio.run(tracker._poster_url(meta)) == "https://host.invalid/poster.jpg"

    poster.unlink()
    monkeypatch.setattr(tracker, "_download_poster", AsyncMock(return_value=None))
    assert asyncio.run(tracker._poster_url(meta)).startswith("https://image.tmdb.org/")


def test_cinematik_dvd_audio_subtitle_helpers() -> None:
    tracker = _tracker()
    section = "Audio\nFormat : AC-3\nChannel(s) : 6 channels"
    assert tracker._dvd_audio_section(f"Header\n\n{section}\n\nFooter") == "Format : AC-3\nChannel(s) : 6 channels"
    assert tracker._dvd_audio_section("No audio") == ""
    assert tracker._dvd_audio_codec("Format : DTS") == "DTS"
    assert tracker._dvd_audio_codec("Format : Unknown") == "Unknown"
    assert tracker._dvd_audio_channels("Channel(s) : 6 channels") == "5.1"
    assert tracker._dvd_audio_channels("No channels") == "Unknown"
    assert tracker._dvd_audio_language("Language : English\n") == "English"
    assert tracker._dvd_audio_language("No language") == "Unknown"
    subtitles = tracker.parse_subtitles("Text #1\nLanguage : English\nText #2\nLanguage : French")
    assert subtitles == {"English", "French"}


def test_cinematik_bdmv_helpers_and_video_source() -> None:
    tracker = _tracker()
    meta = _meta(
        bdinfo={
            "audio": [{"language": "English", "codec": "DTS", "channels": "5.1"}],
            "subtitles": ["English", "French"],
            "video": [{"resolution": "1080p"}],
        }
    )
    assert tracker._bdmv_audio_text(meta.bdinfo) == "English DTS 5.1"
    assert tracker._bdmv_subtitle_text(meta.bdinfo) == "English, French"
    assert tracker._bdmv_video_resolution(meta) == "1080p"
    assert tracker._bdmv_video_resolution(_meta(bdinfo={})) == "Unknown"
    assert "Video Format" in tracker._video_format_line(meta)
    assert "DVD Format" in tracker._video_format_line(_meta(is_disc="DVD"))
    assert "BD50" in tracker._technical_source_line(meta)
    assert "Criterion" in tracker._distributor_line(meta)


def test_cinematik_technical_sections() -> None:
    tracker = _tracker()
    meta = _meta(
        bdinfo={
            "label": "DISC",
            "length": "01:30:00",
            "audio": [{"language": "English", "codec": "DTS", "channels": "5.1"}],
            "subtitles": ["English"],
            "video": [{"resolution": "1080p"}],
        },
        untouched=True,
    )
    text = tracker._technical_section(meta, [], "35 Mbps", "United States")
    assert "Disc Label" in text
    assert "[X] Untouched" in text
    dvd = _meta(is_disc="DVD", bdinfo={}, discs=[{"vob_mi": "", "ifo_mi": ""}], untouched=False)
    text = tracker._technical_section(dvd, dvd.discs, "Unknown", "United States")
    assert "DVD Format" in text
    assert "[ ] Untouched" in text


def test_cinematik_description_section_helpers() -> None:
    tracker = _tracker()
    assert "poster" in tracker._cover_section("poster").lower()
    assert "a" in tracker._screens_section(["a", "b", "c", "d", "e", "f"])
    assert "No synopsis available" in tracker._synopsis_section(_meta(overview=None))
    assert "No comments" in tracker._comments_section(_meta(uploader_comments=None))
    assert "Extras" in tracker._extras_section()


def test_cinematik_description_edit_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    unattended = _meta(unattended=True, unattended_confirm=False)
    assert asyncio.run(tracker._maybe_edit_description(unattended, "original")) == "original"
    monkeypatch.setattr(cinematik_module.cli_ui, "ask_string", lambda *_args, **_kwargs: "")
    assert tracker._interactive_description("original") == "original"
    monkeypatch.setattr(cinematik_module.cli_ui, "ask_string", lambda *_args, **_kwargs: "e")
    monkeypatch.setattr(cinematik_module.click, "edit", lambda _value: " edited ")
    assert tracker._interactive_description("original") == "edited"
    monkeypatch.setattr(cinematik_module.click, "edit", lambda _value: None)
    assert tracker._edited_description("original") == "original"


def test_cinematik_country_names() -> None:
    tracker = _tracker()
    assert tracker.country_code_to_name("usa") == "United States"
    assert tracker.country_code_to_name("XXX") == "Unknown Country"


@pytest.mark.asyncio
async def test_cinematik_generated_description_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    meta = _meta(tmp_path, discs=[{"summary": "Total Bitrate: 20 Mbps"}], image_list=[{"raw_url": "https://img/1.png"}])
    monkeypatch.setattr(tracker, "_poster_url", AsyncMock(return_value="https://img/poster.jpg"))
    result = await tracker.get_description(meta)
    path = tmp_path / "tmp" / "release" / "[CINEMATIK]DESCRIPTION.txt"
    assert path.is_file()
    assert result["description"] == path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_cinematik_custom_description_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_custom_description", AsyncMock(return_value="custom"))
    assert await tracker.get_description(_meta(description_link="https://example.invalid")) == {"description": "custom"}


@pytest.mark.asyncio
async def test_cinematik_custom_description_uses_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    class Builder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def general_description_generator(self, *_args: object, **_kwargs: object) -> str:
            return "builder-description"

    monkeypatch.setattr(cinematik_module, "DescriptionBuilder", Builder)
    meta = _meta(description_link="https://example.invalid")
    assert await _tracker()._custom_description(meta) == "builder-description"


@pytest.mark.asyncio
async def test_cinematik_poster_response_uses_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(200, request=httpx.Request("GET", url), content=b"poster")

    monkeypatch.setattr(cinematik_module.httpx, "AsyncClient", lambda *_args, **_kwargs: Client())
    response = await Cinematik._poster_response("https://example.com/poster.jpg")
    assert response.content == b"poster"


def test_cinematik_append_dvd_audio_and_subtitles() -> None:
    tracker = _tracker()
    lines: list[str] = []
    disc = {
        "vob_mi": "Header\n\nAudio\nFormat : AC-3\nChannel(s) : 6 channels\n\nFooter",
        "ifo_mi_full": "Language : English\n",
        "ifo_mi": "Text #1\nLanguage : English",
    }
    tracker._append_dvd_audio_subtitles(lines, disc)
    text = "".join(lines)
    assert "English AC-3 5.1" in text
    assert "Subtitles..........: English" in text


@pytest.mark.asyncio
async def test_cinematik_attended_description_delegates_to_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker()
    monkeypatch.setattr(tracker, "_interactive_description", lambda _description: "interactive")
    meta = _meta(unattended=False)
    assert await tracker._maybe_edit_description(meta, "original") == "interactive"
