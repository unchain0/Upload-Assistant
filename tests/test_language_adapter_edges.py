from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.media import language_adapter
from src.integrations.media.language_adapter import LanguagesManager


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "language",
        "category": "MOVIE",
        "is_disc": "",
        "manual_language": "",
        "dual_audio": False,
        "path": "Movie.mkv",
        "name": "Movie",
        "mediainfo": {"media": {"track": []}},
        "tracker_status": {},
        "language_checked": False,
        "audio_languages": [],
        "subtitle_languages": [],
        "unattended": True,
        "unattended_confirm": False,
        "unattended_audio_skip": False,
        "unattended_subtitle_skip": False,
        "no_subs": False,
        "hardcoded_subs": False,
        "write_audio_languages": False,
        "write_subtitle_languages": False,
        "write_hc_languages": False,
        "bluray_audio_skip": False,
        "debug": False,
    }
    state.update(values)
    return Meta(state)


def _release_dir(meta: Meta) -> Path:
    path = Path(meta.base_dir) / "tmp" / meta.uuid
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_display_name_dedupe_title_extraction_and_english(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    assert manager._dedupe_preserve_order(["English", "French", "English"]) == ["English", "French"]
    assert manager._language_display_name("pt") == "Portuguese"
    assert asyncio.run(manager.has_english_language("English"))
    assert asyncio.run(manager.has_english_language(["French", "British English"]))
    assert not asyncio.run(manager.has_english_language([]))
    assert manager.extract_language_from_title(None) is None
    assert manager.extract_language_from_title("Commentary in French") == "French"
    monkeypatch.setattr(language_adapter.langcodes.Language, "get", lambda _value: (_ for _ in ()).throw(ValueError("bad")))
    assert manager._language_display_name(" xx ") == "Xx"
    monkeypatch.setattr(language_adapter.langcodes, "find", lambda _word: (_ for _ in ()).throw(LookupError("bad")))
    assert manager.extract_language_from_title("Unknownword") is None


def test_add_language_section_missing_existing_and_insert() -> None:
    manager = LanguagesManager()
    assert manager._add_language_to_audio_section("General\nFormat : Matroska\n", "English") == "General\nFormat : Matroska\n"
    existing = "Audio\nFormat : AAC\nLanguage : French\n\nText\nLanguage : English\n"
    assert manager._add_language_to_audio_section(existing, "English") == existing
    missing = "General\n\nAudio #1\nFormat : AAC\n\nText\nLanguage : English\n"
    updated = manager._add_language_to_audio_section(missing, "Portuguese")
    assert "Language                                : Portuguese" in updated
    assert updated.index("Portuguese") < updated.index("Text")


def test_confirmed_single_audio_language_all_guards(tmp_path: Path) -> None:
    manager = LanguagesManager()
    assert not asyncio.run(manager.apply_confirmed_single_audio_language(_meta(tmp_path, category="BOOK", manual_language="en")))
    assert not asyncio.run(manager.apply_confirmed_single_audio_language(_meta(tmp_path, is_disc="BDMV", manual_language="en")))
    assert not asyncio.run(manager.apply_confirmed_single_audio_language(_meta(tmp_path, manual_language="und")))
    assert not asyncio.run(manager.apply_confirmed_single_audio_language(_meta(tmp_path, manual_language="en", dual_audio=True)))
    assert not asyncio.run(
        manager.apply_confirmed_single_audio_language(
            _meta(tmp_path, manual_language="en", mediainfo={"media": {"track": [{"@type": "Audio"}, {"@type": "Audio"}]}}),
        )
    )
    assert not asyncio.run(
        manager.apply_confirmed_single_audio_language(
            _meta(tmp_path, manual_language="en", mediainfo={"media": {"track": [{"@type": "Audio", "Language": "French"}]}}),
        )
    )


def test_confirmed_language_writes_only_existing_files(tmp_path: Path) -> None:
    manager = LanguagesManager()
    meta = _meta(
        tmp_path,
        manual_language="en",
        mediainfo={"media": {"track": [{"@type": "Audio", "Language": "unknown"}]}},
    )
    directory = _release_dir(meta)
    (directory / "MEDIAINFO.txt").write_text("Audio\nFormat : AAC\n", encoding="utf-8")
    assert asyncio.run(manager.apply_confirmed_single_audio_language(meta))
    assert "English" in (directory / "MEDIAINFO.txt").read_text(encoding="utf-8")
    assert not (directory / "MediaInfo.json").exists()


def test_parse_bluray_missing_error_and_rich_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    meta = _meta(tmp_path, is_disc="BDMV")
    assert asyncio.run(manager.parse_blu_ray(meta)) == {}

    directory = _release_dir(meta)
    summary = directory / "BD_SUMMARY_00.txt"
    summary.write_text(
        "Disc Title: Example\nDisc Label: LABEL\nDisc Size: 50 GB\nProtection: AACS\n"
        "Playlist: 00001.MPLS\nSize: 40 GB\nLength: 01:20:00\nTotal Bitrate: 40 Mbps\n"
        "Video: HEVC / 30000 kbps / 2160p / 23.976 / 16:9 / Main 10\n"
        "Audio: English / DTS-HD Master Audio / 5.1 / 48 kHz / 3000 kbps / 24-bit\n"
        "* Audio: English / AC-3 / 2.0 / 48 kHz / 192 kbps / 16-bit\n"
        "Subtitle: English / 20 kbps\n* Subtitle: French / 10 kbps\n",
        encoding="utf-8",
    )
    parsed = asyncio.run(manager.parse_blu_ray(meta))
    assert parsed["disc_info"]["disc_title"] == "Example"
    assert parsed["playlist_info"]["playlist"] == "00001.MPLS"
    assert parsed["video"]["format"] == "HEVC"
    assert parsed["audio"][0]["bitrate_num"] == 3000
    assert parsed["audio"][1]["is_commentary"] is True
    assert parsed["subtitles"][1]["is_commentary"] is True

    summary.write_text("Video: AVC\nAudio: English\nSubtitle: French\n", encoding="utf-8")
    parsed = asyncio.run(manager.parse_blu_ray(meta))
    assert parsed["video"]["format"] == "AVC"
    assert parsed["audio"][0]["language"] == "English"

    original_open = language_adapter.aiofiles.open
    monkeypatch.setattr(language_adapter.aiofiles, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))
    assert asyncio.run(manager.parse_blu_ray(meta)) == {}
    monkeypatch.setattr(language_adapter.aiofiles, "open", original_open)


def test_parsed_mediainfo_sections_missing_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    meta = _meta(tmp_path)
    assert asyncio.run(manager.parsed_mediainfo(meta)) == {}
    directory = _release_dir(meta)
    (directory / "MEDIAINFO.txt").write_text(
        "General\nComplete name : Movie.mkv\nIgnored line\n\n"
        "Video #1\nFormat : AVC\nDuration : 100\nIgnored : x\n\n"
        "Audio\nFormat : AAC\nLanguage : English\nCommercial name : AAC\nChannel(s) : 2\nTitle : Main\n\n"
        "Text #1\nFormat : UTF-8\nLanguage : French\nTitle : Subs\n\nMenu\n00:00:00 : Chapter\n",
        encoding="utf-8",
    )
    parsed = asyncio.run(manager.parsed_mediainfo(meta))
    assert parsed["general"]["complete_name"] == "Movie.mkv"
    assert parsed["video"][0]["format"] == "AVC"
    assert parsed["audio"][0]["commercial_name"] == "AAC"
    assert parsed["text"][0]["language"] == "French"

    original_open = language_adapter.aiofiles.open
    monkeypatch.setattr(language_adapter.aiofiles, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))
    assert asyncio.run(manager.parsed_mediainfo(meta)) == {}
    monkeypatch.setattr(language_adapter.aiofiles, "open", original_open)


def test_process_non_video_and_already_checked(tmp_path: Path) -> None:
    manager = LanguagesManager()
    meta = _meta(tmp_path, category="MUSIC", audio_languages=["English"], subtitle_languages=["French"])
    asyncio.run(manager.process_desc_language(meta))
    assert meta.language_checked and meta.audio_languages == [] and meta.subtitle_languages == []
    checked = _meta(tmp_path, language_checked=True)
    asyncio.run(manager.process_desc_language(checked))
    assert checked.language_checked


def test_process_file_languages_from_tracks_and_titles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    meta = _meta(tmp_path)
    monkeypatch.setattr(
        manager,
        "parsed_mediainfo",
        AsyncMock(
            return_value={
                "audio": [
                    {"language": "English", "title": "Main"},
                    {"title": "French Dub"},
                    {"title": "Director Commentary"},
                ],
                "text": [{"language": "Spanish"}, {"language": "Spanish"}],
            }
        ),
    )
    asyncio.run(manager.process_desc_language(meta, "TEST"))
    assert meta.audio_languages == ["English", "French"]
    assert meta.subtitle_languages == ["Spanish"]
    assert meta.language_checked


def test_process_file_missing_audio_attended_prompt_and_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"title": "Unknown"}], "text": []}))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: "English, French")
    meta = _meta(tmp_path, unattended=False)
    asyncio.run(manager.process_desc_language(meta, "TEST"))
    assert meta.audio_languages == ["English", "French"] and meta.write_audio_languages

    cleanup = AsyncMock()
    monkeypatch.setattr(language_adapter.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(language_adapter.cleanup_manager, "reset_terminal", lambda: None)
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    meta = _meta(tmp_path, uuid="cancel", unattended=False)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.process_desc_language(meta, "TEST"))
    cleanup.assert_awaited()


def test_process_file_unattended_missing_audio_subtitles_and_hardcoded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{}], "text": [{}]}))
    meta = _meta(tmp_path, debug=True)
    asyncio.run(manager.process_desc_language(meta, "TEST"))
    assert meta.unattended_audio_skip and meta.unattended_subtitle_skip
    assert meta.tracker_status["TEST"]["skip_upload"] is True
    assert meta.subtitle_languages == ["English, Portuguese"]

    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"language": "English"}]}))
    hardcoded = _meta(tmp_path, uuid="hc", hardcoded_subs=True)
    asyncio.run(manager.process_desc_language(hardcoded, "TEST"))
    assert hardcoded.subtitle_languages == ["English"] and hardcoded.write_hc_languages

    no_subs = _meta(tmp_path, uuid="nosubs")
    asyncio.run(manager.process_desc_language(no_subs, "TEST"))
    assert no_subs.no_subs


def test_process_file_subtitle_and_hardcoded_prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"language": "English"}], "text": [{"title": "Unknown"}]}))
    answers = iter(("Portuguese, French",))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: next(answers))
    meta = _meta(tmp_path, unattended=False)
    asyncio.run(manager.process_desc_language(meta, "TEST"))
    assert meta.subtitle_languages == ["Portuguese", "French"] and meta.write_subtitle_languages

    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"language": "English"}], "text": []}))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: "Spanish")
    hardcoded = _meta(tmp_path, uuid="hc-prompt", hardcoded_subs=True, unattended=False)
    asyncio.run(manager.process_desc_language(hardcoded, "TEST"))
    assert hardcoded.subtitle_languages == ["Spanish"] and hardcoded.write_hc_languages


def test_process_bluray_commentary_mapping_string_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()
    monkeypatch.setattr(
        manager,
        "parse_blu_ray",
        AsyncMock(
            return_value={
                "audio": [
                    {"language": "English", "is_commentary": False},
                    {"language": "French", "is_commentary": True},
                ],
                "subtitles": [
                    {"language": "Spanish", "is_commentary": False},
                    {"language": "French", "is_commentary": True},
                ],
            }
        ),
    )
    meta = _meta(tmp_path, is_disc="BDMV", audio_languages=["Japanese"], subtitle_languages="German")
    asyncio.run(manager.process_desc_language(meta))
    assert meta.audio_languages == ["Japanese", "English"]
    assert meta.subtitle_languages == ["German", "Spanish"]

    monkeypatch.setattr(manager, "parse_blu_ray", AsyncMock(return_value={"audio": [], "subtitles": ["English", "French", "English"]}))
    meta = _meta(tmp_path, uuid="plain-subs", is_disc="BDMV")
    asyncio.run(manager.process_desc_language(meta))
    assert meta.subtitle_languages == ["English", "French"]

    monkeypatch.setattr(manager, "parse_blu_ray", AsyncMock(side_effect=RuntimeError("bad bdinfo")))
    meta = _meta(tmp_path, uuid="error", is_disc="BDMV")
    asyncio.run(manager.process_desc_language(meta))
    assert meta.language_checked


def test_remaining_language_adapter_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = LanguagesManager()

    # Blank lines in BD summaries are skipped.
    meta = _meta(tmp_path, is_disc="BDMV", uuid="blank-summary")
    directory = _release_dir(meta)
    (directory / "BD_SUMMARY_00.txt").write_text("Disc Title: Test\n\nAudio: English / AAC\n", encoding="utf-8")
    assert asyncio.run(manager.parse_blu_ray(meta))["disc_info"]["disc_title"] == "Test"

    # Ending in General flushes that final section.
    regular = _meta(tmp_path, uuid="final-general")
    directory = _release_dir(regular)
    (directory / "MEDIAINFO.txt").write_text("General\nFormat : Matroska\n", encoding="utf-8")
    assert asyncio.run(manager.parsed_mediainfo(regular))["general"]["format"] == "Matroska"

    # Empty attended audio response marks the tracker ineligible.
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{}], "text": []}))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: "")
    blank_audio = _meta(tmp_path, uuid="blank-audio", unattended=False)
    asyncio.run(manager.process_desc_language(blank_audio, "TEST"))
    assert blank_audio.unattended_audio_skip and blank_audio.tracker_status["TEST"]["skip_upload"]

    # Subtitle cancellation must escape the integration instead of being swallowed.
    cleanup = AsyncMock()
    monkeypatch.setattr(language_adapter.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(language_adapter.cleanup_manager, "reset_terminal", lambda: None)
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"language": "English"}], "text": [{}]}))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    cancelled_sub = _meta(tmp_path, uuid="cancel-sub", unattended=False)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.process_desc_language(cancelled_sub, "TEST"))

    # Empty subtitle response is an explicit skip.
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: "")
    blank_sub = _meta(tmp_path, uuid="blank-sub", unattended=False)
    asyncio.run(manager.process_desc_language(blank_sub, "TEST"))
    assert blank_sub.unattended_subtitle_skip and blank_sub.tracker_status["TEST"]["skip_upload"]

    # Hardcoded subtitle prompt cancellation/blank values have the same semantics.
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(return_value={"audio": [{"language": "English"}], "text": []}))
    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    hardcoded_cancel = _meta(tmp_path, uuid="hc-cancel", hardcoded_subs=True, unattended=False)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.process_desc_language(hardcoded_cancel, "TEST"))

    monkeypatch.setattr(language_adapter.cli_ui, "ask_string", lambda *_args, **_kwargs: "")
    hardcoded_blank = _meta(tmp_path, uuid="hc-blank", hardcoded_subs=True, unattended=False)
    asyncio.run(manager.process_desc_language(hardcoded_blank, "TEST"))
    assert hardcoded_blank.unattended_subtitle_skip and hardcoded_blank.tracker_status["TEST"]["skip_upload"]

    # Unexpected parser errors are logged and converted to a completed language check.
    monkeypatch.setattr(manager, "parsed_mediainfo", AsyncMock(side_effect=RuntimeError("parse failed")))
    parser_error = _meta(tmp_path, uuid="parser-error")
    asyncio.run(manager.process_desc_language(parser_error, "TEST"))
    assert parser_error.language_checked

    # Mixed external subtitle payloads ignore malformed non-dict entries once the dict path is selected.
    monkeypatch.setattr(
        manager,
        "parse_blu_ray",
        AsyncMock(return_value={"audio": [], "subtitles": [{"language": "English"}, "bad"]}),
    )
    mixed = _meta(tmp_path, uuid="mixed", is_disc="BDMV")
    asyncio.run(manager.process_desc_language(mixed))
    assert mixed.subtitle_languages == ["English"]

    monkeypatch.setattr(manager, "parse_blu_ray", AsyncMock(side_effect=OperationAbortedError("cancelled")))
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(manager.process_desc_language(_meta(tmp_path, uuid="bd-cancel", is_disc="BDMV")))

    class InvalidLanguage:
        def is_valid(self) -> bool:
            return False

    monkeypatch.setattr(language_adapter.langcodes, "find", lambda _word: InvalidLanguage())
    assert manager._find_language_name("invalid") is None
