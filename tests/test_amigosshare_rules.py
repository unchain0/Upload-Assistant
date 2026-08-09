# ruff: noqa: S101

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

data_config = types.ModuleType("data.config")
data_config.__file__ = str(Path(__file__).parents[1] / "data" / "config.py")
data_config.DEFAULT = {}
data_config.config = {}
sys.modules.setdefault("data.config", data_config)

from src.trackers.amigosshare import AmigosShare  # noqa: E402


def make_meta(**overrides):
    workspace_root = Path(__file__).resolve().parent.parent
    values = {
        "category": "MOVIE",
        "anime": False,
        "imdb_id": "1234567",
        "name": "Filme de Exemplo",
        "title": "Filme de Exemplo",
        "base_dir": str(workspace_root),
        "uuid": "unit-test",
        "source_size": 1024 * 1024 * 1024,
        "filelist": ["Filme.de.Exemplo.2024.1080p.WEB-DL.DDP.5.1.H.264-GRP.mkv"],
        "screens": 3,
        "is_disc": "",
        "tv_pack": False,
        "imdb_info": {"status": ""},
        "adult_media": False,
        "tmdb_adult_media": False,
        "nsfw": False,
        "language_checked": True,
        "audio_languages": [],
        "subtitle_languages": [],
        "subtitle_files": [],
        "unattended": False,
        "unattended_confirm": False,
        "description": "Sinopse de teste em português para validação do tracker.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def tracker() -> AmigosShare:
    return AmigosShare({"DEFAULT": {"tmdb_api": "test-key"}, "TRACKERS": {"AMIGOSSHARE": {}}})


async def run_checks(
    meta: SimpleNamespace,
    *,
    confirm_result: bool | None = None,
    guard_language_call: bool = False,
    guard_video_language_call: bool = False,
) -> bool:
    client = tracker()
    try:
        if guard_language_call:
            client.common.check_language_requirements = AsyncMock(side_effect=AssertionError("language check should not run"))
        if guard_video_language_call:
            client.common.check_portuguese_video_requirements = AsyncMock(side_effect=AssertionError("video language check should not run"))

        if confirm_result is not None:
            client.common.prompt_user_for_confirmation = AsyncMock(return_value=confirm_result)
        else:
            client.common.prompt_user_for_confirmation = AsyncMock(side_effect=AssertionError("confirmation should not run"))

        return await client.get_additional_checks(meta)
    finally:
        await client.session.aclose()


def test_movie_passes_with_portuguese_audio():
    meta = make_meta(audio_languages=["portuguese"])

    assert asyncio.run(run_checks(meta))


def test_movie_passes_with_portuguese_language_aliases():
    meta = make_meta(audio_languages=["por"])

    assert asyncio.run(run_checks(meta))


def test_movie_passes_with_portuguese_subtitles():
    meta = make_meta(subtitle_languages=["portuguese"])

    assert asyncio.run(run_checks(meta))


def test_movie_rejects_missing_language_when_unattended():
    meta = make_meta(unattended=True)

    assert not asyncio.run(run_checks(meta))


def test_movie_allows_attended_confirmation_after_missing_language():
    meta = make_meta(unattended=False)

    assert asyncio.run(run_checks(meta, confirm_result=True))


def test_movie_passes_with_portuguese_external_subtitles():
    meta = make_meta(subtitle_files=["movie.pt-BR.srt"])

    assert asyncio.run(run_checks(meta, guard_language_call=True))


def test_movie_passes_with_accented_portuguese_external_subtitles():
    meta = make_meta(subtitle_files=["movie.português.srt"])

    assert asyncio.run(run_checks(meta, guard_language_call=True))


@pytest.mark.parametrize(
    "subtitle_file",
    ["movie.pt-BR.forced.srt", "movie.portuguese.sdh.srt"],
)
def test_movie_passes_with_tagged_portuguese_external_subtitles(subtitle_file: str) -> None:
    meta = make_meta(subtitle_files=[subtitle_file])

    assert asyncio.run(run_checks(meta, guard_language_call=True))


def test_movie_does_not_treat_unidentified_external_subtitles_as_portuguese():
    meta = make_meta(subtitle_files=["external.srt"], unattended=True)

    assert not asyncio.run(run_checks(meta))


def test_movie_does_not_treat_title_words_as_language_markers():
    meta = make_meta(subtitle_files=["Amor.Por.Acaso.srt"], unattended=True)

    assert not asyncio.run(run_checks(meta))


def test_movie_unattended_confirmation_does_not_prompt():
    meta = make_meta(unattended=True, unattended_confirm=True)

    assert asyncio.run(run_checks(meta))


def test_book_and_game_bypass_video_language_validation():
    book_meta = make_meta(category="BOOK", imdb_id=None, source_size=2 * 1024 * 1024, filelist=["Livro.pdf"])
    game_meta = make_meta(category="GAME", imdb_id=None, filelist=["Jogo.iso"])

    assert asyncio.run(run_checks(book_meta, guard_video_language_call=True))
    assert asyncio.run(run_checks(game_meta, guard_video_language_call=True))


@pytest.mark.parametrize("companion", ["release.nfo", "README.txt"])
def test_amigosshare_rejects_standalone_crack_with_documentation(companion: str) -> None:
    meta = make_meta(category="GAME", imdb_id=None, filelist=["Crack.exe", companion])

    assert not asyncio.run(run_checks(meta, guard_video_language_call=True))


def test_amigosshare_rejects_video_category_without_video_files() -> None:
    meta = make_meta(filelist=["release.nfo"])

    assert not asyncio.run(run_checks(meta, guard_language_call=True))


def test_amigosshare_excludes_sample_files_from_tv_episode_count() -> None:
    meta = make_meta(
        category="TV",
        audio_languages=["portuguese"],
        filelist=[
            "Show.S01E01.1080p.WEB-DL.DDP.5.1.H.264-GRP.mkv",
            "Show.S01E02.sample.mkv",
        ],
        imdb_info={"status": "continuing"},
    )

    assert asyncio.run(run_checks(meta))


@pytest.mark.asyncio
async def test_amigosshare_request_search_handles_http_errors_but_propagates_parser_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = tracker()
    meta = make_meta(search_requests=True)
    client.cookie_validator.load_session_cookies = AsyncMock(return_value=None)
    try:
        request = httpx.Request("GET", client.requests_url)
        client.session.get = AsyncMock(side_effect=httpx.ConnectError("network failure", request=request))
        assert await client.get_requests(meta) == []

        response = SimpleNamespace(text="<html></html>", raise_for_status=lambda: None)
        client.session.get = AsyncMock(return_value=response)

        def fail_parse(*_args, **_kwargs):
            raise ValueError("parser failure")

        monkeypatch.setattr("src.trackers.amigosshare.BeautifulSoup", fail_parse)
        with pytest.raises(ValueError, match="parser failure"):
            await client.get_requests(meta)
    finally:
        await client.session.aclose()


def test_book_blocks_non_portuguese_description_when_unattended():
    meta = make_meta(category="BOOK", imdb_id=None, source_size=2 * 1024 * 1024, unattended=True, description="This release contains a Portuguese tracker release with title and files.")

    assert not asyncio.run(run_checks(meta))


def test_book_allows_non_portuguese_description_with_confirmation():
    meta = make_meta(category="BOOK", imdb_id=None, source_size=2 * 1024 * 1024, description="This release contains a Portuguese tracker release with title and files.")

    assert asyncio.run(run_checks(meta, confirm_result=True))


def test_book_allows_non_portuguese_description_in_confirmed_unattended_mode_without_prompt():
    meta = make_meta(
        category="BOOK",
        imdb_id=None,
        source_size=2 * 1024 * 1024,
        unattended=True,
        unattended_confirm=True,
        description="This release contains an English description with technical details.",
    )

    assert asyncio.run(run_checks(meta))


@pytest.mark.parametrize(
    "description",
    [
        "La película está disponible con audio original y subtítulos.",
        "Le résumé présente une édition française avec des sous-titres.",
    ],
)
def test_book_does_not_treat_spanish_or_french_accents_as_portuguese(description: str):
    meta = make_meta(category="BOOK", imdb_id=None, source_size=2 * 1024 * 1024, unattended=True, description=description)

    assert not asyncio.run(run_checks(meta))


def test_book_blocks_non_portuguese_description_in_unattended_without_confirmation():
    meta = make_meta(category="BOOK", imdb_id=None, source_size=2 * 1024 * 1024, unattended=True, description="This release contains a Portuguese tracker release with title and files.")

    assert not asyncio.run(run_checks(meta))


def test_book_size_rejection_happens_before_other_checks():
    meta = make_meta(category="BOOK", imdb_id=None, source_size=1024)

    assert not asyncio.run(run_checks(meta, guard_language_call=True))


def test_imdb_rejection_happens_before_language_validation():
    meta = make_meta(imdb_id=None, audio_languages=["portuguese"], subtitle_languages=["portuguese"])

    assert not asyncio.run(run_checks(meta, guard_language_call=True))


def test_amigosshare_rejects_archives_except_for_games():
    assert not asyncio.run(run_checks(make_meta(filelist=["release.rar"]), guard_language_call=True))
    assert not asyncio.run(
        run_checks(make_meta(filelist=["Filme.2024.1080p.WEB-DL.H.264-GRP.mkv", "Filme.r02"]), guard_language_call=True)
    )
    assert asyncio.run(run_checks(make_meta(category="GAME", imdb_id=None, filelist=["Jogo.rar"]), guard_language_call=True))


@pytest.mark.parametrize("filename", ["baixado de outro tracker.url", "release.torrent", "www.outro-tracker.txt"])
def test_amigosshare_rejects_advertising_and_tracker_files(filename: str):
    assert not asyncio.run(run_checks(make_meta(filelist=[filename]), guard_language_call=True))


def test_amigosshare_rejects_prohibited_subjects_and_amateur_adult_content():
    assert not asyncio.run(run_checks(make_meta(keywords=["zoofilia"]), guard_language_call=True))
    assert not asyncio.run(run_checks(make_meta(name="Cena Amateur", adult_media=True), guard_language_call=True))


def test_amigosshare_enforces_adult_size_and_screenshot_per_video():
    assert not asyncio.run(run_checks(make_meta(adult_media=True, source_size=100 * 1024 * 1024 - 1), guard_language_call=True))
    files = [
        "Cena.2024.1080p.WEB-DL.H.264-GRP.mkv",
        "Cena.2.2024.1080p.WEB-DL.H.264-GRP.mkv",
    ]
    assert not asyncio.run(run_checks(make_meta(adult_media=True, filelist=files, screens=1), guard_language_call=True))


@pytest.mark.parametrize("label", ["CD-Key", "Serial Key", "Serial Number"])
def test_amigosshare_rejects_serial_keys_in_description(label: str):
    meta = make_meta(description=f"Descrição em português. {label}: ABCD-EFGH-IJKL")

    assert not asyncio.run(run_checks(meta, guard_language_call=True))


def test_amigosshare_rejects_standalone_game_cracks_and_unreleased_builds():
    crack = make_meta(category="GAME", imdb_id=None, filelist=["Crack.exe"])
    beta = make_meta(category="GAME", imdb_id=None, filelist=["Jogo.iso"], release_type="beta")

    assert not asyncio.run(run_checks(crack, guard_language_call=True))
    assert not asyncio.run(run_checks(beta, guard_language_call=True))


def test_amigosshare_rejects_invalid_video_filename_and_accepts_nogroup():
    invalid = make_meta(audio_languages=["portuguese"], filelist=["Filme.mkv"])
    missing_group = make_meta(audio_languages=["portuguese"], filelist=["Filme.2024.1080p.WEB-DL.H.264.mkv"])
    valid = make_meta(audio_languages=["portuguese"], filelist=["Filme.2024.1080p.BluRay.H.264-NoGroup.mkv"])

    assert not asyncio.run(run_checks(invalid, guard_language_call=True))
    assert not asyncio.run(run_checks(missing_group, guard_language_call=True))
    assert asyncio.run(run_checks(valid))


def test_amigosshare_allows_single_episode_only_for_ongoing_series():
    episode = "Serie.S01E01.1080p.WEB-DL.DDP.5.1.H.264-GRP.mkv"
    ongoing = make_meta(category="TV", filelist=[episode], imdb_info={"status": "Returning Series"}, audio_languages=["portuguese"])
    ended = make_meta(category="TV", filelist=[episode], imdb_info={"status": "Ended"}, audio_languages=["portuguese"])

    assert asyncio.run(run_checks(ongoing))
    assert not asyncio.run(run_checks(ended, guard_language_call=True))


def test_amigosshare_allows_season_pack_only_after_series_ends():
    files = [
        "Serie.S01E01.1080p.WEB-DL.DDP.5.1.H.264-GRP.mkv",
        "Serie.S01E02.1080p.WEB-DL.DDP.5.1.H.264-GRP.mkv",
    ]
    ended = make_meta(category="TV", tv_pack=True, filelist=files, imdb_info={"status": "Ended"}, audio_languages=["portuguese"])
    ongoing = make_meta(category="TV", tv_pack=True, filelist=files, imdb_info={"status": "Returning Series"}, unattended=True)

    assert asyncio.run(run_checks(ended))
    assert not asyncio.run(run_checks(ongoing, guard_language_call=True))


def test_amigosshare_rejects_multi_episode_non_pack_and_standalone_extras():
    files = [
        "Serie.S01E01.1080p.WEB-DL.H.264-GRP.mkv",
        "Serie.S01E02.1080p.WEB-DL.H.264-GRP.mkv",
    ]
    extras = ["Serie.S01E01.Extras.1080p.WEB-DL.H.264-GRP.mkv"]

    assert not asyncio.run(run_checks(make_meta(category="TV", filelist=files, imdb_info={"status": "Returning Series"}), guard_language_call=True))
    assert not asyncio.run(run_checks(make_meta(category="TV", filelist=extras, imdb_info={"status": "Returning Series"}), guard_language_call=True))


def test_amigosshare_rejects_tv_upload_without_episode_markers():
    files = ["Serie.Special.2024.1080p.WEB-DL.H.264-GRP.mkv"]

    assert not asyncio.run(run_checks(make_meta(category="TV", filelist=files), guard_language_call=True))


def test_amigosshare_small_general_torrent_requires_confirmation():
    unattended = make_meta(source_size=19 * 1024 * 1024, unattended=True)
    attended = make_meta(source_size=19 * 1024 * 1024)

    assert not asyncio.run(run_checks(unattended, guard_language_call=True))
    assert asyncio.run(run_checks(attended, confirm_result=True))


def test_amigosshare_game_name_follows_tracker_display_pattern():
    client = tracker()
    try:
        assert client.get_game_name(SimpleNamespace(title="Dead Island: Retro Revenge", tag="-CODEX")) == "Dead Island: Retro Revenge [CODEX]"
        assert client.get_game_name(SimpleNamespace(title="Jogo Antigo", tag="")) == "Jogo Antigo [NoGroup]"
    finally:
        asyncio.run(client.session.aclose())
