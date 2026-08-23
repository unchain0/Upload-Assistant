# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Never, cast

import anitopy
import cli_ui
import guessit
import httpx

from src.domain_models.errors import OperationAbortedError
from src.domain_models.processing import *  # noqa: F403
from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.tags import get_tag
from src.integrations.observability.runtime_support import (
    console,
    logger,
    prompt_in_thread,
)

guessit_module: Any = cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


def _guessit_data(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return guessit_fn(value, options)


def _anitopy_parse(value: str) -> dict[str, Any]:
    anitopy_any = cast(Any, anitopy)
    return cast(dict[str, Any], anitopy_any.parse(value) or {})


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _clear_episode_metadata(meta: Meta) -> None:
    meta.auto_episode_title = None
    meta.overview_meta = None
    meta.episode_airdate = None
    meta.episode_name = ""
    meta.episode_overview = ""
    if not meta.manual_episode_title:
        meta.episode_title = ""
    meta.episode_tmdb_data = {}
    meta.tmdb_episode_data = None
    meta.tvdb_episode_data = {}
    meta.tvdb_episode_id = None
    meta.tvdb_episode_int = None
    meta.tvdb_episode_name = None
    meta.tvdb_episode_year = ""
    meta.tvdb_episode = None
    meta.tvdb_imdb_id = None
    meta.tvdb_overview = None
    meta.tvdb_season_name = ""
    meta.tvdb_season_int = None
    meta.tvdb_season = None
    meta.tvmaze_episode_data = {}
    meta.we_asked_tvmaze = False
    meta.we_checked_tmdb = False
    meta.we_checked_tvdb = False


_VIDEO_EXTENSIONS = frozenset(
    {
        ".avi",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".vob",
    }
)
_EPISODE_IDENTITY_PATTERN = re.compile(
    r"(?i)(?<![^\W_])S([0-9]{1,3})E([0-9]{1,4})(?![^\W_])"
)


def _has_manual_episode_identity(meta: Meta) -> bool:
    return bool(
        meta.manual_date
        or meta.manual_season is not None
        or meta.manual_episode is not None
    )


def _eligible_for_filename_episode_sync(meta: Meta) -> bool:
    if meta.category != "TV":
        return False
    if bool(meta.is_disc) or meta.tv_pack:
        return False
    return not _has_manual_episode_identity(meta)


def _is_video_file_path(path: object) -> bool:
    if not isinstance(path, (str, Path)):
        return False
    return Path(path).suffix.lower() in _VIDEO_EXTENSIONS


def _single_video_file(meta: Meta) -> Path | None:
    filelist = meta.filelist if isinstance(meta.filelist, list) else []
    video_files = [
        Path(path) for path in filelist if _is_video_file_path(path)
    ]
    if len(video_files) != 1:
        return None
    return video_files[0]


def _guessit_allows_single_episode(filename: str) -> bool:
    try:
        guessed_episodes = _guessit_data(filename).get("episode")
    except Exception:
        return False
    return not isinstance(guessed_episodes, list)


def _episode_identity_from_filename(filename: str) -> tuple[int, int] | None:
    if len(filename) > 1024:
        return None
    if not _guessit_allows_single_episode(filename):
        return None
    matches = _EPISODE_IDENTITY_PATTERN.findall(filename)
    if len(matches) != 1:
        return None
    season_value, episode_value = matches[0]
    return int(season_value), int(episode_value)


def _apply_episode_identity(
    meta: Meta, season_int: int, episode_int: int
) -> None:
    _clear_episode_metadata(meta)
    meta.season_int = season_int
    meta.episode_int = episode_int
    meta.season = f"S{season_int:02d}"
    meta.episode = f"E{episode_int:02d}"


def sync_single_episode_from_filename(meta: Meta) -> bool:
    if not _eligible_for_filename_episode_sync(meta):
        return False
    video_file = _single_video_file(meta)
    if video_file is None:
        return False
    identity = _episode_identity_from_filename(video_file.name)
    if identity is None:
        return False
    if identity == (meta.season_int, meta.episode_int):
        return False
    _apply_episode_identity(meta, *identity)
    return True


@dataclass
class _EpisodeState:
    season_int: int = 1
    episode_int: int = 0
    season: str = "S01"
    episode: str = ""
    is_daily: bool = False


@dataclass
class _AnimeContext:
    romaji: str = ""
    eng_title: str = ""
    anilist_episodes: int = 0


class SeasonEpisodeManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.tmdb_manager = TmdbManager(config)

    @staticmethod
    def _reset_standard_season(state: _EpisodeState) -> None:
        state.season_int = 1
        state.season = "S01"

    @staticmethod
    def _daily_match(video: str) -> re.Match[str] | None:
        return re.search(r"\d{4}[-\.]\d{2}[-\.]\d{2}", video)

    @staticmethod
    def _daily_requested(
        meta: Meta, daily_match: re.Match[str] | None
    ) -> bool:
        return bool(meta.manual_date or daily_match) and not bool(
            meta.manual_season
        )

    @staticmethod
    def _apply_daily_match(
        meta: Meta, daily_match: re.Match[str] | None
    ) -> None:
        if meta.manual_date is None and daily_match is not None:
            meta.manual_date = daily_match.group().replace(".", "-")

    @staticmethod
    def _daily_guess_date(video: str, meta: Meta) -> str:
        guess_data = _guessit_data(video)
        raw = meta.manual_date or guess_data.get("date")
        return str(raw) if raw else ""

    async def _apply_daily_identity(
        self,
        video: str,
        meta: Meta,
        state: _EpisodeState,
    ) -> bool:
        daily_match = self._daily_match(video)
        if not self._daily_requested(meta, daily_match):
            return False
        self._apply_daily_match(meta, daily_match)
        state.is_daily = True
        guess_date = self._daily_guess_date(video, meta)
        (
            state.season_int,
            state.episode_int,
        ) = await self.tmdb_manager.daily_to_tmdb_season_episode(
            _safe_int(meta.tmdb_id, 0), guess_date
        )
        state.season = f"S{state.season_int:02d}"
        state.episode = f"E{state.episode_int:02d}"
        meta.daily_episode_title = meta.manual_date or ""
        return True

    @staticmethod
    def _guess_year(video: str) -> str:
        try:
            return str(_guessit_data(video).get("year") or "")
        except Exception:
            return ""

    @classmethod
    def _apply_standard_season_guess(
        cls,
        video: str,
        state: _EpisodeState,
        season_guess: str,
        guess_year: str,
    ) -> None:
        if season_guess != guess_year:
            state.season_int = int(season_guess or 1)
            state.season = f"S{state.season_int:02d}"
            return
        if f"s{season_guess}" not in video.lower():
            cls._reset_standard_season(state)
            return
        state.season_int = int(season_guess)
        state.season = f"S{state.season_int:02d}"

    @classmethod
    def _guess_standard_season(cls, video: str, state: _EpisodeState) -> None:
        guess_year = cls._guess_year(video)
        try:
            guess_data = _guessit_data(video)
            season_guess = str(guess_data.get("season") or "")
            cls._apply_standard_season_guess(
                video, state, season_guess, guess_year
            )
        except Exception:
            logger.info(
                "[bold yellow]There was an error guessing the season number. Guessing S01. Use [bold green]--season #[/bold green] to correct if needed"
            )
            cls._reset_standard_season(state)

    async def _resolve_standard_season(
        self, video: str, meta: Meta, state: _EpisodeState
    ) -> None:
        try:
            if await self._apply_daily_identity(video, meta, state):
                return
            self._guess_standard_season(video, state)
        except Exception:
            console.print_exception()
            self._reset_standard_season(state)

    @staticmethod
    def _episode_values(value: Any, default: int) -> tuple[int, str]:
        if isinstance(value, list):
            values = cast(list[Any], value)
            episode_int = _safe_int(values[0], default) if values else default
            episode = "".join(f"E{_safe_int(item, 0):02d}" for item in values)
            return episode_int, episode
        episode_int = _safe_int(value, default)
        episode = f"E{episode_int:02d}" if value is not None else ""
        return episode_int, episode

    @staticmethod
    def _mark_episode_pack(meta: Meta, state: _EpisodeState) -> None:
        state.episode = ""
        state.episode_int = 0
        meta.tv_pack = True

    @classmethod
    def _resolve_standard_episode(
        cls,
        video: str,
        filelist: list[str],
        meta: Meta,
        state: _EpisodeState,
    ) -> None:
        if state.is_daily:
            return
        if len(filelist) != 1:
            cls._mark_episode_pack(meta, state)
            return
        try:
            episodes = _guessit_data(video).get("episode")
            state.episode_int, state.episode = cls._episode_values(episodes, 0)
        except Exception:
            cls._mark_episode_pack(meta, state)

    async def _standard_identity(
        self, video: str, meta: Meta, filelist: list[str]
    ) -> _EpisodeState:
        state = _EpisodeState()
        await self._resolve_standard_season(video, meta, state)
        self._resolve_standard_episode(video, filelist, meta, state)
        return state

    async def _prepare_anime_context(
        self, video: str, meta: Meta
    ) -> _AnimeContext:
        context = _AnimeContext()
        if meta.mal_id != 0 or meta.category != "TV":
            return context
        parsed = _anitopy_parse(Path(video).name)
        (
            context.romaji,
            mal_id,
            context.eng_title,
            season_year,
            anilist_episodes,
            meta.demographic,
        ) = await self.tmdb_manager.get_romaji(
            str(parsed.get("anime_title", "")),
            _safe_int(meta.mal_id, 0),
            meta,
        )
        mal_id_value = _safe_int(mal_id, 0)
        if mal_id_value:
            meta.mal_id = mal_id_value
        context.anilist_episodes = _safe_int(anilist_episodes, 0)
        if meta.tmdb_id == 0:
            await self._fill_anime_tmdb(meta, parsed, season_year)
        return context

    async def _fill_anime_tmdb(
        self, meta: Meta, parsed: Mapping[str, Any], season_year: Any
    ) -> None:
        year = str(parsed.get("anime_year") or season_year)
        guess_title = _guessit_data(
            str(parsed.get("anime_title", "")),
            {"excludes": ["country", "language"]},
        ).get("title", "")
        tmdb_id_value, category_value = await self.tmdb_manager.get_tmdb_id(
            str(guess_title), year, meta.category, meta.filename
        )
        meta.tmdb_id = tmdb_id_value
        meta.category = category_value

    @staticmethod
    def _apply_anime_tag(parsed: Mapping[str, Any], meta: Meta) -> None:
        tag = str(parsed.get("release_group", ""))
        if tag and meta.tag is None:
            meta.tag = f"-{tag}"

    @staticmethod
    def _anime_episode_value(
        parsed: Mapping[str, Any], guess_data: Mapping[str, Any]
    ) -> Any:
        episodes = parsed.get("episode_number", guess_data.get("episode", "1"))
        if isinstance(episodes, list) or str(episodes).isnumeric():
            return cast(Any, episodes)
        return guess_data.get("episode")

    @classmethod
    def _guess_anime_episode(
        cls, video: str, parsed: Mapping[str, Any]
    ) -> tuple[int, str]:
        guess_data = _guessit_data(video)
        episodes = cls._anime_episode_value(parsed, guess_data)
        return cls._episode_values(episodes, 1)

    @staticmethod
    def _uuid_episode(meta: Meta) -> int | None:
        if not meta.uuid:
            return None
        patterns = (
            r"[Ee](\d+)[Ee](\d+)",
            r"[Ee](\d+)",
            r"[Ee]pisode[\s_]*(\d+)",
            r"[\s_\-](\d+)[\s_\-]",
            r"[\s_\-](\d+)$",
            r"^(\d+)[\s_\-]",
        )
        for pattern in patterns:
            match = re.search(pattern, meta.uuid, re.IGNORECASE)
            if match is None:
                continue
            try:
                return int(match.group(1))
            except ValueError, IndexError:
                continue
        return None

    @classmethod
    async def _fallback_anime_episode(
        cls, meta: Meta, state: _EpisodeState
    ) -> None:
        state.episode_int = cls._uuid_episode(meta) or 1
        state.episode = f"E{state.episode_int:02d}"
        if state.episode_int == 1:
            logger.info(
                "[bold yellow]There was an error guessing the episode number. Guessing E01. Use [bold green]--episode #[/bold green] to correct if needed"
            )
        await asyncio.sleep(1.5)

    @classmethod
    async def _resolve_anime_episode(
        cls,
        video: str,
        parsed: Mapping[str, Any],
        filelist: list[str],
        meta: Meta,
        state: _EpisodeState,
    ) -> None:
        if len(filelist) != 1:
            cls._mark_episode_pack(meta, state)
            return
        try:
            state.episode_int, state.episode = cls._guess_anime_episode(
                video, parsed
            )
        except Exception:
            await cls._fallback_anime_episode(meta, state)

    @staticmethod
    def _guess_initial_anime_season(
        video: str, parsed: Mapping[str, Any], meta: Meta
    ) -> int:
        if meta.season_int:
            return _safe_int(meta.season_int, 1)
        guess_data = _guessit_data(video)
        value = parsed.get("anime_season", guess_data.get("season", "1"))
        return _safe_int(value, 1)

    @staticmethod
    async def _xem_single_mapping(
        meta: Meta, state: _EpisodeState, filelist: list[str]
    ) -> None:
        params = {
            "id": str(meta.tvdb_id),
            "origin": "tvdb",
            "absolute": str(state.episode_int),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = (
                await client.post(
                    "https://thexem.info/map/single", params=params
                )
            ).json()
        if response["result"] == "failure":
            raise XEMNotFoundError  # noqa: F405
        logger.debug(f"[cyan]TheXEM Absolute -> Standard[/cyan]\n{response}")
        scene = response["data"]["scene"]
        state.season_int = int(scene["season"])
        state.season = f"S{state.season_int:02d}"
        if len(filelist) == 1:
            state.episode_int = int(scene["episode"])
            state.episode = f"E{state.episode_int:02d}"

    @staticmethod
    def _xem_key(value: str) -> str:
        return re.sub(
            r"[^0-9a-zA-Z\[\\]]+", "", value.lower().replace(" ", "")
        )

    @classmethod
    def _xem_name_match(
        cls, target: str, candidate: str, difference: float
    ) -> tuple[bool, float]:
        target_key = cls._xem_key(target)
        candidate_key = cls._xem_key(candidate)
        ratio = SequenceMatcher(None, target_key, candidate_key).ratio()
        return target_key in candidate_key and ratio >= difference, ratio

    @staticmethod
    def _xem_language_target(
        context: _AnimeContext, language: str
    ) -> str | None:
        if language == "jp":
            return context.romaji
        if language == "us":
            return context.eng_title
        return None

    @classmethod
    def _xem_language_candidates(
        cls,
        season_num: str,
        language: str,
        names_raw: Any,
        context: _AnimeContext,
    ) -> list[tuple[str, str, str]]:
        target = cls._xem_language_target(context, language)
        if target is None or not isinstance(names_raw, list):
            return []
        return [
            (season_num, target, str(name))
            for name in cast(list[Any], names_raw)
        ]

    @classmethod
    def _xem_candidates(
        cls, data: Mapping[str, Any], context: _AnimeContext
    ) -> list[tuple[str, str, str]]:
        candidates: list[tuple[str, str, str]] = []
        for season_num, values_raw in data.items():
            if not isinstance(values_raw, Mapping):
                continue
            for language, names_raw in cast(
                Mapping[str, Any], values_raw
            ).items():
                candidates.extend(
                    cls._xem_language_candidates(
                        season_num, language, names_raw, context
                    )
                )
        return candidates

    @classmethod
    def _best_xem_season(
        cls, data: Mapping[str, Any], context: _AnimeContext
    ) -> int:
        selected = 1
        difference = 0.0
        for season_num, target, candidate in cls._xem_candidates(
            data, context
        ):
            matched, ratio = cls._xem_name_match(target, candidate, difference)
            if matched:
                selected = int(season_num) if season_num != "all" else 1
                difference = ratio
        return selected

    @classmethod
    async def _xem_names_mapping(
        cls, meta: Meta, state: _EpisodeState, context: _AnimeContext
    ) -> None:
        state.season_int = 1
        state.season = "S01"
        url = f"https://thexem.info/map/names?origin=tvdb&id={meta.tvdb_id!s}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = (await client.get(url)).json()
        logger.debug(f"[cyan]Matching Season Number from TheXEM\n{response}")
        if response["result"] != "success":
            raise XEMNotFoundError  # noqa: F405
        raw_data = response.get("data", {})
        data: Mapping[str, Any] = (
            cast(Mapping[str, Any], raw_data)
            if isinstance(raw_data, Mapping)
            else cast(Mapping[str, Any], {})
        )
        state.season_int = cls._best_xem_season(data, context)
        state.season = f"S{state.season_int:02d}"

    @classmethod
    async def _xem_season(
        cls,
        meta: Meta,
        state: _EpisodeState,
        context: _AnimeContext,
        filelist: list[str],
    ) -> None:
        if state.episode_int >= context.anilist_episodes:
            await cls._xem_single_mapping(meta, state, filelist)
            return
        await cls._xem_names_mapping(meta, state, context)

    @staticmethod
    async def _fallback_anime_season(
        video: str, meta: Meta, state: _EpisodeState
    ) -> None:
        if meta.debug:
            console.print_exception()
        try:
            state.season_int = int(_guessit_data(video).get("season", "1"))
            state.season = f"S{state.season_int:02d}"
        except Exception:
            state.season_int = 1
            state.season = "S01"
        logger.info(
            f"[bold yellow]{meta.title} does not exist on thexem, guessing {state.season}"
        )
        logger.info(
            f"[bold yellow]If [green]{state.season}[/green] is incorrect, use --season to correct"
        )
        await asyncio.sleep(3)

    @classmethod
    async def _resolve_anime_season(
        cls,
        video: str,
        parsed: Mapping[str, Any],
        meta: Meta,
        state: _EpisodeState,
        context: _AnimeContext,
        filelist: list[str],
    ) -> None:
        try:
            state.season_int = cls._guess_initial_anime_season(
                video, parsed, meta
            )
            state.season = f"S{state.season_int:02d}"
        except Exception:
            try:
                await cls._xem_season(meta, state, context, filelist)
            except Exception:
                await cls._fallback_anime_season(video, meta, state)

    @classmethod
    async def _anime_identity(
        cls,
        video: str,
        meta: Meta,
        filelist: list[str],
        context: _AnimeContext,
    ) -> _EpisodeState:
        state = _EpisodeState(episode_int=1, episode="E01")
        if meta.mal_id == 0 or meta.category != "TV":
            logger.info(
                "[bold red]Error determining if TV show is anime or not[/bold red]"
            )
            logger.info(
                "[bold yellow]Set manual season and episode[/bold yellow]"
            )
            return state
        parsed = _anitopy_parse(Path(video).name)
        cls._apply_anime_tag(parsed, meta)
        await cls._resolve_anime_episode(video, parsed, filelist, meta, state)
        await cls._resolve_anime_season(
            video, parsed, meta, state, context, filelist
        )
        return state

    @staticmethod
    def _apply_manual_season(meta: Meta, state: _EpisodeState) -> None:
        if meta.manual_season is None:
            meta.season = state.season
            return
        manual = str(meta.manual_season).lower().replace("s", "")
        meta.daily_episode_title = ""
        state.season_int = _safe_int(manual, 1)
        meta.season = f"S{manual.zfill(2)}"

    @staticmethod
    def _apply_manual_episode(meta: Meta, state: _EpisodeState) -> None:
        if meta.manual_episode is None:
            meta.episode = state.episode
            return
        manual = str(meta.manual_episode).lower().replace("e", "")
        state.episode_int = _safe_int(manual, 0)
        meta.episode = f"E{manual.zfill(2)}"
        meta.tv_pack = False

    @classmethod
    def _apply_manual_identity(cls, meta: Meta, state: _EpisodeState) -> None:
        cls._apply_manual_season(meta, state)
        cls._apply_manual_episode(meta, state)

    @staticmethod
    def _apply_identity_numbers(
        meta: Meta,
        state: _EpisodeState,
        previous_identity: tuple[Any, Any],
    ) -> None:
        meta.season_int = state.season_int
        meta.episode_int = state.episode_int
        if previous_identity != (state.season_int, state.episode_int):
            _clear_episode_metadata(meta)

    @staticmethod
    def _apply_manual_episode_title(meta: Meta) -> None:
        if "manual_episode_title" in meta and meta.manual_episode_title:
            meta.episode_title = meta.manual_episode_title

    @staticmethod
    def _apply_part(video: str, meta: Meta) -> None:
        meta.part = ""
        if meta.tv_pack != 1:
            return
        part = _guessit_data(str(Path(video).parent)).get("part")
        meta.part = f"Part {part}" if part else ""  # pyrefly: ignore [bad-assignment]

    @classmethod
    def _apply_episode_metadata(
        cls,
        video: str,
        meta: Meta,
        state: _EpisodeState,
        previous_identity: tuple[Any, Any],
    ) -> None:
        cls._apply_identity_numbers(meta, state, previous_identity)
        cls._apply_manual_episode_title(meta)
        cls._apply_part(video, meta)

    async def get_season_episode(self, video: str, meta: Meta) -> Meta:
        if meta.category != "TV":
            return meta
        previous_identity = (meta.season_int, meta.episode_int)
        filelist = cast(list[str], meta.filelist)
        meta.tv_pack = False
        if meta.anime:
            context = await self._prepare_anime_context(video, meta)
            state = await self._anime_identity(video, meta, filelist, context)
        else:
            state = await self._standard_identity(video, meta, filelist)
        self._apply_manual_identity(meta, state)
        self._apply_episode_metadata(video, meta, state, previous_identity)
        return meta

    @staticmethod
    def _missing_episode_labels(
        completeness: Mapping[str, Any],
    ) -> list[str]:
        try:
            return [
                f"S{s:02d}E{e:02d}"
                for s, e in completeness["missing_episodes"]
            ]
        except ValueError:
            logger.error(
                "[red]Error determining missing episodes, you should double check the pack manually."
            )
            return ["Unknown"]

    @staticmethod
    def _log_incomplete_pack(missing_list: list[str], meta: Meta) -> None:
        if "Unknown" in missing_list:
            logger.warning(
                "[red]Warning: Season pack appears incomplete (missing episodes could not be determined)."
            )
        else:
            logger.warning("[red]Warning: Season pack appears incomplete!")
            logger.info(f"[yellow]Missing episodes: {', '.join(missing_list)}")
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                "[yellow]Unattended mode: continuing despite incomplete season pack (no confirmation)."
            )

    @staticmethod
    def _show_file_range(filelist: list[Any], start: int, end: int) -> int:
        batch = filelist[start:end]
        for index, file in enumerate(batch, start=start + 1):
            logger.info(f"[cyan]  {index:2d}. {Path(file).name}")
        return start + len(batch)

    @staticmethod
    def _should_prompt_more(
        files_shown: int, total_files: int, meta: Meta
    ) -> bool:
        if files_shown >= total_files:
            return False
        return not meta.unattended or meta.unattended_confirm

    @staticmethod
    async def _pack_file_prompt(remaining_files: int, batch_size: int) -> str:
        if remaining_files > batch_size:
            response = await prompt_in_thread(
                cli_ui.ask_string,
                f"Show (n)ext {batch_size} files, (a)ll remaining files, (c)ontinue with incomplete pack, or (q)uit? (n/a/c/Q): ",
            )
        else:
            response = await prompt_in_thread(
                cli_ui.ask_string,
                f"Show (a)ll remaining {remaining_files} files, (c)ontinue with incomplete pack, or (q)uit? (a/c/Q): ",
            )
        return (response or "").lower()

    @staticmethod
    def _abort_incomplete_pack() -> Never:
        logger.info(
            "[red]Aborting torrent creation due to incomplete season pack"
        )
        raise OperationAbortedError(
            "Torrent creation cancelled because the season pack is incomplete."
        ) from None

    @classmethod
    def _apply_pack_file_response(
        cls,
        response: str,
        filelist: list[Any],
        files_shown: int,
        batch_size: int,
        remaining_files: int,
    ) -> tuple[int, bool]:
        if response == "n" and remaining_files > batch_size:
            return (
                cls._show_file_range(
                    filelist, files_shown, files_shown + batch_size
                ),
                False,
            )
        if response == "a":
            return cls._show_file_range(
                filelist, files_shown, len(filelist)
            ), False
        if response == "c":
            return files_shown, True
        return cls._abort_incomplete_pack()

    @classmethod
    async def _review_pack_files(cls, meta: Meta) -> bool:
        filelist = list(meta.filelist)
        batch_size = 15
        logger.info(f"[cyan]Filelist ({len(filelist)} files):")
        files_shown = cls._show_file_range(filelist, 0, batch_size)
        while cls._should_prompt_more(files_shown, len(filelist), meta):
            remaining_files = len(filelist) - files_shown
            logger.info(f"[yellow]... and {remaining_files} more files")
            response = await cls._pack_file_prompt(remaining_files, batch_size)
            files_shown, just_go = cls._apply_pack_file_response(
                response,
                filelist,
                files_shown,
                batch_size,
                remaining_files,
            )
            if just_go:
                return True
        return False

    @staticmethod
    def _needs_pack_confirmation(meta: Meta, just_go: bool) -> bool:
        if just_go:
            return False
        return not meta.unattended or meta.unattended_confirm

    @classmethod
    async def _confirm_incomplete_pack(cls, meta: Meta, just_go: bool) -> None:
        if not cls._needs_pack_confirmation(meta, just_go):
            return
        response = await prompt_in_thread(
            cli_ui.ask_string,
            "Continue with incomplete season pack? (y/N): ",
        )
        if (response or "").lower() != "y":
            cls._abort_incomplete_pack()

    @classmethod
    async def _review_incomplete_pack(
        cls, completeness: Mapping[str, Any], meta: Meta
    ) -> None:
        missing_list = cls._missing_episode_labels(completeness)
        cls._log_incomplete_pack(missing_list, meta)
        if "Unknown" in missing_list:
            return
        just_go = await cls._review_pack_files(meta)
        await cls._confirm_incomplete_pack(meta, just_go)

    @staticmethod
    def _log_pack_tags(completeness: Mapping[str, Any]) -> None:
        if completeness["consistent_tags"]:
            return
        logger.warning(
            "[yellow]Warning: Multiple group tags detected in season pack!"
        )
        tags_found = cast(Mapping[str, list[str]], completeness["tags_found"])
        for tag, files in tags_found.items():
            logger.info(f"[cyan]Tag: {tag} found in files:")
            for file in files:
                logger.info(f"[cyan]  - {file}")

    async def check_season_pack_completeness(self, meta: Meta) -> None:
        completeness = cast(
            Mapping[str, Any], await self.check_season_pack_detail(meta)
        )
        if completeness["complete"]:
            logger.debug("[green]Season pack completeness verified")
        else:
            await self._review_incomplete_pack(completeness, meta)
        self._log_pack_tags(completeness)

    @staticmethod
    def _empty_pack_detail(
        tags_found: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "complete": True,
            "missing_episodes": [],
            "found_episodes": [],
            "consistent_tags": True,
            "tags_found": tags_found or {},
        }

    @staticmethod
    def _default_pack_season(meta: Meta) -> int:
        try:
            return int(meta.season_int)
        except TypeError, ValueError:
            return 1

    @staticmethod
    def _standard_episode_identities(filename: str) -> list[tuple[int, int]]:
        identities: list[tuple[int, int]] = []
        matches = re.findall(
            r"[Ss](\d{1,2})[Ee](\d{1,3})(?:[Ee](\d{1,3}))?", filename
        )
        for season_str, first_str, second_str in matches:
            season_num = int(season_str)
            identities.append((season_num, int(first_str)))
            if second_str:
                identities.append((season_num, int(second_str)))
        return identities

    @staticmethod
    def _episode_only_identities(
        filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        identities: list[tuple[int, int]] = []
        matches = re.findall(r"\b[Ee](\d{1,3})(?:[Ee](\d{1,3}))?\b", filename)
        for first_str, second_str in matches:
            identities.append((default_season, int(first_str)))
            if second_str:
                identities.append((default_season, int(second_str)))
        return identities

    @staticmethod
    def _anime_episode_identities(
        filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        matches = re.findall(
            r"(?:\s-\s)?(\d{1,4})(?:v\d+)?\s*\((?:\d+[pi])\)", filename
        )
        return [(default_season, int(match)) for match in matches]

    @classmethod
    def _episode_identities(
        cls, filename: str, default_season: int
    ) -> list[tuple[int, int]]:
        identities = cls._standard_episode_identities(filename)
        if identities:
            return identities
        identities = cls._episode_only_identities(filename, default_season)
        if identities:
            return identities
        return cls._anime_episode_identities(filename, default_season)

    @staticmethod
    async def _record_pack_tag(
        tags_found: dict[str, list[str]],
        file_path: str,
        filename: str,
        meta: Meta,
    ) -> None:
        file_tag = await get_tag(file_path, meta, season_pack_check=True)
        if not file_tag:
            return
        tag_clean = file_tag.lstrip("-")
        tags_found.setdefault(tag_clean, []).append(filename)

    @classmethod
    async def _scan_pack_files(
        cls, files: list[str], meta: Meta, default_season: int
    ) -> tuple[list[tuple[int, int]], dict[str, list[str]]]:
        found_episodes: list[tuple[int, int]] = []
        tags_found: dict[str, list[str]] = {}
        for file_path in files:
            filename = Path(file_path).name
            await cls._record_pack_tag(tags_found, file_path, filename, meta)
            found_episodes.extend(
                cls._episode_identities(filename, default_season)
            )
        return found_episodes, tags_found

    @staticmethod
    def _missing_for_season(
        found_episodes: list[tuple[int, int]], season: int
    ) -> list[tuple[int, int]]:
        season_episodes = [
            episode
            for found_season, episode in found_episodes
            if found_season == season
        ]
        minimum = min(season_episodes)
        maximum = max(season_episodes)
        return [
            (season, episode)
            for episode in range(minimum, maximum + 1)
            if episode not in season_episodes
        ]

    @classmethod
    def _missing_pack_episodes(
        cls, found_episodes: list[tuple[int, int]]
    ) -> tuple[list[tuple[int, int]], set[int]]:
        seasons = {season for season, _episode in found_episodes}
        missing: list[tuple[int, int]] = []
        for season in seasons:
            missing.extend(cls._missing_for_season(found_episodes, season))
        return missing, seasons

    @staticmethod
    def _pack_detail_result(
        found_episodes: list[tuple[int, int]],
        missing_episodes: list[tuple[int, int]],
        seasons: set[int],
        tags_found: dict[str, list[str]],
    ) -> dict[str, Any]:
        return {
            "complete": not missing_episodes,
            "missing_episodes": missing_episodes,
            "found_episodes": found_episodes,
            "seasons": list(seasons),
            "consistent_tags": len(tags_found) <= 1,
            "tags_found": tags_found,
        }

    @staticmethod
    def _log_pack_detail(result: Mapping[str, Any]) -> None:
        found_episodes = result["found_episodes"]
        missing_episodes = result["missing_episodes"]
        tags_found = cast(Mapping[str, list[str]], result["tags_found"])
        logger.debug("[cyan]Season pack completeness check:")
        logger.debug(f"[cyan]Found episodes: {found_episodes}")
        if missing_episodes:
            logger.debug(f"[red]Missing episodes: {missing_episodes}")
        else:
            logger.debug("[green]Season pack episode list appears complete")
        if tags_found:
            logger.debug(f"[cyan]Group tags found: {list(tags_found.keys())}")
            if not result["consistent_tags"]:
                logger.debug(
                    "[yellow]Warning: Multiple group tags detected in season pack"
                )

    async def check_season_pack_detail(self, meta: Meta) -> dict[str, Any]:
        if not meta.tv_pack:
            return self._empty_pack_detail()
        files = cast(list[str], meta.filelist)
        if not files:
            return self._empty_pack_detail()
        found_episodes, tags_found = await self._scan_pack_files(
            files, meta, self._default_pack_season(meta)
        )
        if not found_episodes:
            logger.info("[red]No episodes found in the season pack files.")
            return self._empty_pack_detail(tags_found)
        found_episodes = sorted(set(found_episodes))
        missing_episodes, seasons = self._missing_pack_episodes(found_episodes)
        result = self._pack_detail_result(
            found_episodes, missing_episodes, seasons, tags_found
        )
        self._log_pack_detail(result)
        return result
