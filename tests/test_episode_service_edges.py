from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import episode_service
from src.services.episode_service import SeasonEpisodeManager


class _Response:
    payload: ClassVar[dict[str, Any]] = {}

    def json(self) -> dict[str, Any]:
        return self.payload


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_delay: float = 0) -> None:
        return None

    monkeypatch.setattr(episode_service.asyncio, "sleep", no_sleep)


def _anime_meta(*, filelist: list[str], **values: object) -> Meta:
    state: dict[str, object] = {
        "category": "TV",
        "anime": True,
        "mal_id": 1,
        "tmdb_id": 1,
        "tvdb_id": 2,
        "filelist": filelist,
        "uuid": "Anime.E05",
        "title": "Anime",
        "tag": None,
        "season_int": 1,
    }
    state.update(values)
    return Meta(state)


def test_anime_episode_failure_uses_uuid_then_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(episode_service, "_anitopy_parse", lambda _value: {})
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("bad episode")
        ),
    )

    meta = _anime_meta(
        filelist=[str(tmp_path / "Anime.E05.mkv")], uuid="Anime.E05"
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.episode == "E05" and result.episode_int == 5

    fallback = _anime_meta(
        filelist=[str(tmp_path / "Anime.mkv")], uuid="Anime"
    )
    result = asyncio.run(
        manager.get_season_episode(fallback.filelist[0], fallback)
    )
    assert result.episode == "E01" and result.episode_int == 1


def test_anime_pack_and_xem_single_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"episode": 12, "part": 2},
    )

    pack = _anime_meta(
        filelist=[str(tmp_path / "one.mkv"), str(tmp_path / "two.mkv")]
    )
    result = asyncio.run(manager.get_season_episode(pack.filelist[0], pack))
    assert (
        result.tv_pack is True
        and result.episode == ""
        and result.part == "Part 2"
    )

    class Parsed(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "anime_season":
                raise ValueError("bad season")
            return super().get(key, default)

    monkeypatch.setattr(
        episode_service,
        "_anitopy_parse",
        lambda _value: Parsed(episode_number="12"),
    )
    monkeypatch.setattr(episode_service.httpx, "AsyncClient", _Client)
    _Response.payload = {
        "result": "success",
        "data": {"scene": {"season": "3", "episode": "4"}},
    }
    mapped = _anime_meta(
        filelist=[str(tmp_path / "Anime.12.mkv")], season_int=0
    )
    result = asyncio.run(
        manager.get_season_episode(mapped.filelist[0], mapped)
    )
    assert result.season == "S03" and result.episode == "E04"


def test_anime_xem_names_us_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})

    class Parsed(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            if key == "anime_season":
                raise ValueError("bad season")
            return super().get(key, default)

    parsed = Parsed(anime_title="Anime", anime_year=2025, episode_number="5")
    monkeypatch.setattr(
        episode_service, "_anitopy_parse", lambda _value: parsed
    )
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {
            "episode": 5,
            "season": "2",
            "title": "Anime",
        },
    )
    monkeypatch.setattr(
        manager.tmdb_manager,
        "get_romaji",
        AsyncMock(
            return_value=("Romaji", 1, "English Show", 2025, 100, "shounen")
        ),
    )
    monkeypatch.setattr(episode_service.httpx, "AsyncClient", _Client)
    _Response.payload = {
        "result": "success",
        "data": {"2": {"us": ["English Show"]}},
    }
    meta = _anime_meta(
        filelist=[str(tmp_path / "Anime.05.mkv")], mal_id=0, season_int=0
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.season == "S02"

    _Response.payload = {"result": "failure"}
    debug = _anime_meta(
        filelist=[str(tmp_path / "Anime.05.mkv")],
        mal_id=0,
        season_int=0,
        debug=True,
    )
    result = asyncio.run(manager.get_season_episode(debug.filelist[0], debug))
    assert result.season == "S02"


def _detail(
    *,
    complete: bool = False,
    missing: list[object] | None = None,
    consistent: bool = True,
) -> dict[str, Any]:
    return {
        "complete": complete,
        "missing_episodes": missing if missing is not None else [(1, 2)],
        "found_episodes": [(1, 1), (1, 3)],
        "consistent_tags": consistent,
        "tags_found": {"ONE": ["one.mkv"], "TWO": ["two.mkv"]}
        if not consistent
        else {},
    }


def test_season_pack_completeness_unknown_unattended_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(
        manager,
        "check_season_pack_detail",
        AsyncMock(return_value=_detail(missing=[("bad", 1)])),
    )
    asyncio.run(
        manager.check_season_pack_completeness(
            Meta(tv_pack=True, unattended=True, filelist=[])
        )
    )

    files = [f"Show.S01E{episode:02}.mkv" for episode in range(1, 21)]
    monkeypatch.setattr(
        manager,
        "check_season_pack_detail",
        AsyncMock(return_value=_detail(consistent=False)),
    )
    asyncio.run(
        manager.check_season_pack_completeness(
            Meta(tv_pack=True, unattended=True, filelist=files)
        )
    )

    monkeypatch.setattr(
        manager,
        "check_season_pack_detail",
        AsyncMock(return_value=_detail(complete=True, consistent=False)),
    )
    asyncio.run(
        manager.check_season_pack_completeness(
            Meta(tv_pack=True, filelist=files)
        )
    )


def test_season_pack_prompt_batches_continue_abort_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    files = [f"Show.S01E{episode:02}.mkv" for episode in range(1, 41)]
    monkeypatch.setattr(
        manager, "check_season_pack_detail", AsyncMock(return_value=_detail())
    )

    answers = iter(("n", "a", "y"))
    monkeypatch.setattr(
        episode_service,
        "prompt_in_thread",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(answers)),
    )
    asyncio.run(
        manager.check_season_pack_completeness(
            Meta(tv_pack=True, filelist=files)
        )
    )

    monkeypatch.setattr(
        episode_service, "prompt_in_thread", AsyncMock(return_value="c")
    )
    asyncio.run(
        manager.check_season_pack_completeness(
            Meta(tv_pack=True, filelist=files)
        )
    )

    monkeypatch.setattr(
        episode_service, "prompt_in_thread", AsyncMock(return_value="q")
    )
    with pytest.raises(OperationAbortedError):
        asyncio.run(
            manager.check_season_pack_completeness(
                Meta(tv_pack=True, filelist=files)
            )
        )

    short = files[:2]
    monkeypatch.setattr(
        episode_service, "prompt_in_thread", AsyncMock(return_value="n")
    )
    with pytest.raises(OperationAbortedError):
        asyncio.run(
            manager.check_season_pack_completeness(
                Meta(tv_pack=True, filelist=short)
            )
        )


def test_season_pack_detail_standard_episode_only_anime_missing_and_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    files = [
        tmp_path / "Show.S01E01-GRP.mkv",
        tmp_path / "Show.S01E01E03-OTHER.mkv",
        tmp_path / "Show.E04-GRP.mkv",
        tmp_path / "Anime - 5 (1080p)-OTHER.mkv",
    ]
    for path in files:
        path.write_bytes(b"video")

    tags = iter(("-GRP", "-OTHER", "-GRP", "-OTHER"))
    monkeypatch.setattr(
        episode_service,
        "get_tag",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(tags)),
    )
    result = asyncio.run(
        manager.check_season_pack_detail(
            Meta(
                tv_pack=True,
                filelist=[str(path) for path in files],
                season_int="bad",
            )
        )
    )

    assert result["found_episodes"] == [(1, 1), (1, 3), (1, 4), (1, 5)]
    assert result["missing_episodes"] == [(1, 2)]
    assert result["complete"] is False
    assert result["consistent_tags"] is False
    assert set(result["tags_found"]) == {"GRP", "OTHER"}

    assert (
        asyncio.run(manager.check_season_pack_detail(Meta(tv_pack=False)))[
            "complete"
        ]
        is True
    )
    assert (
        asyncio.run(
            manager.check_season_pack_detail(Meta(tv_pack=True, filelist=[]))
        )["complete"]
        is True
    )

    no_match = tmp_path / "No Episode.mkv"
    no_match.write_bytes(b"video")
    monkeypatch.setattr(episode_service, "get_tag", AsyncMock(return_value=""))
    result = asyncio.run(
        manager.check_season_pack_detail(
            Meta(tv_pack=True, filelist=[str(no_match)])
        )
    )
    assert result["complete"] is True and result["found_episodes"] == []


def test_safe_int_and_single_episode_no_change(tmp_path: Path) -> None:
    assert episode_service._safe_int("bad", 7) == 7
    video = tmp_path / "Show.S01E02.mkv"
    video.write_bytes(b"video")
    meta = Meta(
        category="TV",
        filelist=[str(video)],
        season_int=1,
        episode_int=2,
        season="S01",
        episode="E02",
    )
    assert episode_service.sync_single_episode_from_filename(meta) is False


def test_standard_tv_season_year_list_scalar_pack_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})

    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {
            "year": 2024,
            "season": 2024,
            "episode": [1, 2],
        },
    )
    meta = Meta(
        category="TV",
        anime=False,
        filelist=[str(tmp_path / "Show.S2024E01E02.mkv")],
        manual_date=None,
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert (
        result.season == "S2024"
        and result.episode == "E01E02"
        and result.episode_int == 1
    )

    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"year": 2024, "season": 2024, "episode": 5},
    )
    meta = Meta(
        category="TV",
        anime=False,
        filelist=[str(tmp_path / "Show.2024.E05.mkv")],
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.season == "S01" and result.episode == "E05"

    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"year": 2024, "season": 3, "episode": None},
    )
    meta = Meta(
        category="TV", anime=False, filelist=[str(tmp_path / "Show.S03.mkv")]
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.season == "S03" and result.episode == ""

    calls = 0

    def broken_season(_value, _options=None):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ValueError("bad season")
        return {"episode": 7}

    monkeypatch.setattr(episode_service, "_guessit_data", broken_season)
    meta = Meta(
        category="TV", anime=False, filelist=[str(tmp_path / "Show.E07.mkv")]
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.season == "S01" and result.episode == "E07"

    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"season": 1, "episode": 1},
    )
    pack = Meta(category="TV", anime=False, filelist=["one.mkv", "two.mkv"])
    result = asyncio.run(manager.get_season_episode("one.mkv", pack))
    assert result.tv_pack is True and result.episode == ""

    calls = 0

    def broken_episode(_value, _options=None):
        nonlocal calls
        calls += 1
        if calls < 3:
            return {"year": 2024, "season": 1}
        if calls == 3:
            raise ValueError("bad episode")
        return {}

    monkeypatch.setattr(episode_service, "_guessit_data", broken_episode)
    meta = Meta(
        category="TV", anime=False, filelist=[str(tmp_path / "Show.mkv")]
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.tv_pack is True and result.episode_int == 0


def test_standard_tv_outer_exception_and_manual_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"episode": 9},
    )
    meta = Meta(
        category="TV",
        anime=False,
        filelist=[str(tmp_path / "Show.mkv")],
        manual_season="S4",
        manual_episode="E6",
        manual_episode_title="Manual title",
        daily_episode_title="old",
    )
    result = asyncio.run(manager.get_season_episode(None, meta))  # type: ignore[arg-type]
    assert result.season == "S04" and result.episode == "E06"
    assert result.season_int == 4 and result.episode_int == 6
    assert result.daily_episode_title == ""
    assert result.episode_title == "Manual title"


def test_anime_tmdb_tag_episode_list_and_regular_season(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(
        manager.tmdb_manager,
        "get_romaji",
        AsyncMock(return_value=("Romaji", 99, "English", 2025, 12, "shounen")),
    )
    tmdb_lookup = AsyncMock(return_value=(456, "TV"))
    monkeypatch.setattr(manager.tmdb_manager, "get_tmdb_id", tmdb_lookup)
    monkeypatch.setattr(
        episode_service,
        "_anitopy_parse",
        lambda _value: {
            "anime_title": "Anime",
            "anime_year": 2025,
            "release_group": "GROUP",
            "episode_number": "bad",
            "anime_season": 3,
        },
    )
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {
            "title": "Anime",
            "episode": [3, 4],
            "season": 3,
        },
    )
    meta = _anime_meta(
        filelist=[str(tmp_path / "Anime.03-04.mkv")],
        mal_id=0,
        tmdb_id=0,
        tag=None,
        season_int=0,
        filename="Anime.03-04.mkv",
        unattended=True,
        unattended_confirm=True,
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.mal_id == 99 and result.tmdb_id == 456
    assert result.tag == "-GROUP"
    assert result.episode == "E03E04" and result.episode_int == 3
    assert result.season == "S03"
    assert tmdb_lookup.await_args.kwargs["unattended"] is True


def test_anime_uuid_bad_match_xem_failure_jp_and_double_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})

    real_search = episode_service.re.search

    class BadMatch:
        def group(self, _index: int) -> str:
            raise ValueError("bad group")

    calls = 0

    def flaky_search(pattern, value, flags=0):
        nonlocal calls
        if "[Ee](\\d+)[Ee]" in str(pattern) and calls == 0:
            calls += 1
            return BadMatch()
        return real_search(pattern, value, flags)

    monkeypatch.setattr(episode_service.re, "search", flaky_search)
    monkeypatch.setattr(episode_service, "_anitopy_parse", lambda _value: {})
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad")),
    )
    meta = _anime_meta(
        filelist=[str(tmp_path / "Anime.mkv")], uuid="Anime.E05"
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.episode == "E05"

    monkeypatch.setattr(episode_service.re, "search", real_search)
    monkeypatch.setattr(
        episode_service,
        "_anitopy_parse",
        lambda _value: {"anime_title": "Anime", "episode_number": "20"},
    )
    monkeypatch.setattr(
        manager.tmdb_manager,
        "get_romaji",
        AsyncMock(
            return_value=("Romaji Anime", 1, "English", 2025, 10, "shounen")
        ),
    )
    monkeypatch.setattr(episode_service.httpx, "AsyncClient", _Client)
    _Response.payload = {"result": "failure"}
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("fallback fails")
        ),
    )
    failure = _anime_meta(
        filelist=[str(tmp_path / "Anime.20.mkv")],
        mal_id=1,
        season_int=0,
        debug=True,
    )
    result = asyncio.run(
        manager.get_season_episode(failure.filelist[0], failure)
    )
    assert result.season == "S01"

    guess_calls = 0

    def jp_guess(_value, _options=None):
        nonlocal guess_calls
        guess_calls += 1
        if guess_calls == 1:
            return {"episode": 5}
        raise ValueError("force XEM names lookup")

    monkeypatch.setattr(episode_service, "_guessit_data", jp_guess)
    _Response.payload = {
        "result": "success",
        "data": {"all": {"jp": ["Romaji Anime"]}},
    }
    jp = _anime_meta(
        filelist=[str(tmp_path / "Anime.05.mkv")],
        mal_id=0,
        tmdb_id=1,
        season_int=0,
    )
    monkeypatch.setattr(
        manager.tmdb_manager,
        "get_romaji",
        AsyncMock(
            return_value=("Romaji Anime", 1, "English", 2025, 100, "shounen")
        ),
    )
    result = asyncio.run(manager.get_season_episode(jp.filelist[0], jp))
    assert result.season == "S01"


def test_anime_unclassified_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    monkeypatch.setattr(
        episode_service,
        "_anitopy_parse",
        lambda _value: {"anime_title": "Unknown"},
    )
    monkeypatch.setattr(
        manager.tmdb_manager,
        "get_romaji",
        AsyncMock(return_value=("", 0, "", 0, 0, "")),
    )
    monkeypatch.setattr(
        manager.tmdb_manager, "get_tmdb_id", AsyncMock(return_value=(0, "TV"))
    )
    monkeypatch.setattr(
        episode_service,
        "_guessit_data",
        lambda *_args, **_kwargs: {"title": "Unknown"},
    )
    meta = _anime_meta(
        filelist=[str(tmp_path / "Unknown.mkv")], mal_id=0, tmdb_id=0
    )
    result = asyncio.run(manager.get_season_episode(meta.filelist[0], meta))
    assert result.season == "S01" and result.episode == "E01"


def test_episode_only_range_complete_pack_and_tag_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SeasonEpisodeManager({"DEFAULT": {"tmdb_api": "key"}})
    files = [tmp_path / "Show.E01E03-GRP.mkv", tmp_path / "Show.E02-OTHER.mkv"]
    for path in files:
        path.write_bytes(b"video")
    tags = iter(("-GRP", "-OTHER"))
    monkeypatch.setattr(
        episode_service,
        "get_tag",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(tags)),
    )
    result = asyncio.run(
        manager.check_season_pack_detail(
            Meta(
                tv_pack=True,
                filelist=[str(path) for path in files],
                season_int=1,
            )
        )
    )
    assert result["found_episodes"] == [(1, 1), (1, 2), (1, 3)]
    assert result["missing_episodes"] == []
    assert result["complete"] is True
    assert result["consistent_tags"] is False
