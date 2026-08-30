# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import re
from collections.abc import Awaitable
from typing import Any, cast

import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.external_apis.imdb import imdb_manager
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.external_apis.tvdb import TvdbData
from src.integrations.external_apis.tvmaze import tvmaze_manager
from src.integrations.observability.runtime_support import logger


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _valid_series_year(value: Any) -> int | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not re.fullmatch(r"19\d\d|20[0-3]\d", text):
        return None
    return int(text)


def _tvdb_series_payload(episodes_data: Any) -> dict[str, Any]:
    return (
        cast(dict[str, Any], episodes_data)
        if isinstance(episodes_data, dict)
        else {}
    )


def _apply_tvdb_series_title(
    meta: Meta, payload: dict[str, Any], series_name: Any
) -> None:
    if series_name:
        meta.tvdb_series_name = series_name
        return
    series_title = payload.get("series_title")
    if series_title and not meta.tvdb_series_name:
        meta.tvdb_series_name = series_title


def _apply_tvdb_series_year(meta: Meta, payload: dict[str, Any]) -> None:
    series_year = _valid_series_year(payload.get("series_year"))
    if series_year is None:
        return
    meta.tvdb_series_year = series_year
    meta.search_year = str(series_year)


def _apply_tvdb_series_metadata(
    meta: Meta, episodes_data: Any, series_name: Any = None
) -> None:
    if meta.original_language == "en":
        return
    payload = _tvdb_series_payload(episodes_data)
    _apply_tvdb_series_title(meta, payload, series_name)
    _apply_tvdb_series_year(meta, payload)


def _manual_language(meta: Meta) -> str | None:
    return (
        meta.manual_language if isinstance(meta.manual_language, str) else None
    )


def _mal_manual_id(meta: Meta) -> int | None:
    value = meta.mal_manual
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _episode_identity(meta: Meta) -> tuple[int, int, int] | None:
    tmdb_id = _coerce_int(meta.tmdb_id)
    season = _coerce_int(meta.season_int)
    episode = _coerce_int(meta.episode_int)
    if tmdb_id is None or season is None or episode is None:
        return None
    return tmdb_id, season, episode


def _tvmaze_episode_identity(meta: Meta) -> tuple[int, int, int] | None:
    tvmaze_id = _coerce_int(meta.tvmaze_id)
    season = _coerce_int(meta.season_int)
    episode = _coerce_int(meta.episode_int)
    if tvmaze_id is None or season is None or episode is None:
        return None
    return tvmaze_id, season, episode


def _season_identity(meta: Meta) -> tuple[int, int] | None:
    tmdb_id = _coerce_int(meta.tmdb_id)
    season = _coerce_int(meta.season_int)
    if tmdb_id is None or season is None:
        return None
    return tmdb_id, season


def _tv_episode_requested(meta: Meta) -> bool:
    return (
        meta.category == "TV"
        and not meta.tv_pack
        and "season_int" in meta
        and "episode_int" in meta
        and meta.episode_int != 0
    )


def _tv_pack_requested(meta: Meta) -> bool:
    return meta.category == "TV" and meta.tv_pack and "season_int" in meta


def _append_task(
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
    name: str,
    task: Awaitable[Any],
) -> None:
    task_map[name] = len(tasks)
    tasks.append(task)


async def _gather_task_results(
    tasks: list[Awaitable[Any]], *, error_prefix: str = ""
) -> list[Any] | None:
    try:
        return await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as error:
        if error_prefix:
            logger.error(f"[red]{error_prefix}: {error}[/red]")
        return None


def _apply_tmdb_metadata_result(
    meta: Meta, result: Any, *, label: str = "TMDb metadata retrieval"
) -> None:
    if not isinstance(result, Exception) and result:
        meta.update(result)
        return
    logger.info(f"[yellow]{label} failed: {result}[/yellow]")


def _apply_imdb_info_result(meta: Meta, result: Any) -> None:
    if isinstance(result, dict):
        meta.imdb_info = result
        return
    if isinstance(result, Exception):
        logger.info(f"[red]IMDb API call failed: {result}[/red]")
        return
    logger.info(
        "[red]Unexpected IMDb response, setting imdb_info to empty.[/red]"
    )
    meta.imdb_info = {}


def _tvdb_episode_payload(result: Any) -> tuple[Any, Any] | None:
    if not isinstance(result, tuple):
        return None
    values = cast(tuple[Any, ...], result)
    if len(values) != 2:
        return None
    return values[0], values[1]


def _apply_tvdb_episode_result(meta: Meta, result: Any) -> None:
    if isinstance(result, Exception):
        logger.info(
            f"[yellow]TVDb episode data retrieval failed: {result}[/yellow]"
        )
        return
    if not result:
        return
    payload = _tvdb_episode_payload(result)
    if payload is None:
        logger.info(
            f"[yellow]Unexpected TVDb data format: {result!r}[/yellow]"
        )
        return
    episodes_data, series_name = payload
    if episodes_data is None:
        logger.info(
            f"[yellow]Unexpected TVDb data format: {result!r}[/yellow]"
        )
        return
    meta.tvdb_episode_data = episodes_data
    _apply_tvdb_series_metadata(meta, episodes_data, series_name)
    meta.we_checked_tvdb = True


def _apply_tvmaze_episode_result(meta: Meta, result: Any) -> None:
    if not isinstance(result, Exception) and result:
        meta.tvmaze_episode_data = result
        meta.we_asked_tvmaze = True
        return
    if isinstance(result, Exception):
        logger.info(
            f"[yellow]TVMaze episode data retrieval failed: {result}[/yellow]"
        )


def _apply_tmdb_episode_result(meta: Meta, result: Any) -> None:
    if not isinstance(result, Exception) and result:
        meta.tmdb_episode_data = result
        meta.we_checked_tmdb = True
        return
    if isinstance(result, Exception):
        logger.info(
            f"[yellow]TMDb episode data retrieval failed: {result}[/yellow]"
        )


def _apply_tmdb_season_result(meta: Meta, result: Any) -> None:
    if not isinstance(result, Exception) and result:
        meta.tmdb_season_data = result
        meta.we_checked_tmdb = True
        return
    if isinstance(result, Exception):
        logger.info(
            f"[yellow]TMDb season data retrieval failed: {result}[/yellow]"
        )


def _tmdb_metadata_task(
    meta: Meta,
    tmdb_manager: TmdbManager,
    filename: str,
    *,
    include_quickie: bool = False,
) -> Awaitable[Any]:
    kwargs: dict[str, Any] = {
        "tmdb_id": meta.tmdb_id,
        "path": meta.path,
        "search_year": meta.search_year,
        "category": meta.category,
        "imdb_id": meta.imdb_id,
        "manual_language": _manual_language(meta),
        "anime": meta.anime,
        "mal_manual": _mal_manual_id(meta),
        "aka": meta.aka,
        "original_language": meta.original_language,
        "poster": meta.artwork_url,
        "debug": meta.debug,
        "mode": meta.mode if meta.mode is not None else "cli",
        "tvdb_id": meta.tvdb_id,
        "filename": filename,
        "base_dir": meta.base_dir,
        "unattended": meta.unattended,
    }
    if include_quickie:
        kwargs["quickie_search"] = meta.quickie_search
    return tmdb_manager.tmdb_other_meta(**kwargs)


def _imdb_info_task(meta: Meta, tmdb_manager: TmdbManager) -> Awaitable[Any]:
    return imdb_manager.get_imdb_info_api(
        meta.imdb_id,
        manual_language=meta.manual_language,
        base_dir=meta.base_dir,
        config=tmdb_manager.config,
    )


def _tvdb_episodes_task(meta: Meta, tvdb_handler: Any) -> Awaitable[Any]:
    return tvdb_handler.get_tvdb_episodes(
        meta.tvdb_id,
        meta.base_dir,
        season=meta.season_int,
        episode=meta.episode_int,
        aired_date=meta.daily_episode_title,
        original_language=meta.original_language,
    )


def _tmdb_episode_task(
    meta: Meta, tmdb_manager: TmdbManager
) -> Awaitable[Any] | None:
    identity = _episode_identity(meta)
    if identity is None:
        return None
    tmdb_id, season, episode = identity
    return tmdb_manager.get_episode_details(tmdb_id, season, episode)


def _tmdb_season_task(
    meta: Meta, tmdb_manager: TmdbManager
) -> Awaitable[Any] | None:
    identity = _season_identity(meta)
    if identity is None:
        return None
    tmdb_id, season = identity
    return tmdb_manager.get_season_details(tmdb_id, season)


def _tvmaze_episode_task(meta: Meta) -> Awaitable[Any] | None:
    identity = _tvmaze_episode_identity(meta)
    if identity is None:
        return None
    tvmaze_id, season, episode = identity
    return tvmaze_manager.get_tvmaze_episode_data(tvmaze_id, season, episode)


def _mapped_result(results: Any, task_map: dict[str, int], name: str) -> Any:
    index = task_map.get(name)
    if index is None:
        return None
    try:
        return results[index]
    except Exception:
        return None


def _append_tv_episode_tasks(
    meta: Meta,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
    tmdb_manager: TmdbManager,
) -> None:
    tvmaze_task = _tvmaze_episode_task(meta)
    if tvmaze_task is not None:
        _append_task(tasks, task_map, "tvmaze_episode", tvmaze_task)
    tmdb_task = _tmdb_episode_task(meta, tmdb_manager)
    if tmdb_task is not None:
        _append_task(tasks, task_map, "tmdb_episode", tmdb_task)


def _append_tv_pack_task(
    meta: Meta,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
    tmdb_manager: TmdbManager,
) -> None:
    season_task = _tmdb_season_task(meta, tmdb_manager)
    if season_task is not None:
        _append_task(tasks, task_map, "tmdb_season", season_task)


def _all_id_tasks(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> tuple[list[Awaitable[Any]], dict[str, int]]:
    tasks: list[Awaitable[Any]] = []
    task_map: dict[str, int] = {}
    _append_task(
        tasks,
        task_map,
        "tmdb",
        _tmdb_metadata_task(meta, tmdb_manager, meta.filename),
    )
    _append_task(tasks, task_map, "imdb", _imdb_info_task(meta, tmdb_manager))
    if meta.category == "TV":
        _append_task(
            tasks, task_map, "tvdb", _tvdb_episodes_task(meta, tvdb_handler)
        )
    if _tv_episode_requested(meta):
        _append_tv_episode_tasks(meta, tasks, task_map, tmdb_manager)
    elif _tv_pack_requested(meta):
        _append_tv_pack_task(meta, tasks, task_map, tmdb_manager)
    return tasks, task_map


def _tvmaze_search_task(
    meta: Meta,
    filename: str,
    tmdb_manager: TmdbManager,
    *,
    full_tuple: bool = False,
) -> Awaitable[Any]:
    search_year = str(meta.search_year) if meta.search_year is not None else ""
    return tvmaze_manager.search_tvmaze(
        filename,
        search_year,
        meta.imdb_id,
        meta.tvdb_id,
        manual_date=meta.manual_date,
        tvmaze_manual=meta.tvmaze_manual,
        return_full_tuple=full_tuple,
        base_dir=meta.base_dir,
        config=tmdb_manager.config,
    )


def _apply_tvmaze_id_result(meta: Meta, result: Any) -> None:
    if isinstance(result, int):
        meta.tvmaze_id = result
        return
    if isinstance(result, Exception):
        logger.info(f"[yellow]TVMaze ID retrieval failed: {result}[/yellow]")
    meta.tvmaze_id = 0


def _tvmaze_tuple_ids(result: Any) -> tuple[int | None, int | None]:
    if not isinstance(result, tuple):
        return None, None
    values = cast(tuple[Any, ...], result)
    if len(values) != 3:
        return None, None
    tvmaze_id = values[0] if isinstance(values[0], int) else None
    tvdb_id = values[2] if isinstance(values[2], int) else None
    return tvmaze_id, tvdb_id


def _apply_tvmaze_tuple_ids(
    meta: Meta, tvmaze_id: int | None, tvdb_id: int | None
) -> bool:
    if tvmaze_id is None:
        return False
    meta.tvmaze_id = tvmaze_id
    if not meta.tvdb_id and tvdb_id is not None and tvdb_id > 0:
        meta.tvdb_id = tvdb_id
        logger.debug(f"[green]Set TVDb ID from TVMaze: {tvdb_id}[/green]")
    return True


def _log_unexpected_tvmaze_result(result: Any) -> None:
    if isinstance(result, Exception):
        logger.info(f"[red]TVMaze API call failed: {result}[/red]")
        return
    logger.info(f"[yellow]Unexpected TVMaze result type: {result!r}[/yellow]")


def _apply_tvmaze_search_result(meta: Meta, result: Any) -> None:
    tvmaze_id, tvdb_id = _tvmaze_tuple_ids(result)
    if _apply_tvmaze_tuple_ids(meta, tvmaze_id, tvdb_id):
        return
    if isinstance(result, int):
        meta.tvmaze_id = result
        return
    _log_unexpected_tvmaze_result(result)
    meta.tvmaze_id = 0


def _apply_tmdb_from_imdb_result(meta: Meta, result: Any) -> None:
    if not isinstance(result, tuple):
        return
    values = cast(tuple[Any, ...], result)
    if len(values) != 4:
        return
    category, tmdb_id, original_language, filename_search = values
    meta.category = category
    meta.tmdb_id = tmdb_id
    if original_language is not None:
        meta.original_language = original_language
    meta.no_ids = filename_search


def _apply_all_id_tv_results(
    meta: Meta, results: list[Any], task_map: dict[str, int]
) -> None:
    if meta.category != "TV":
        return
    _apply_tvdb_episode_result(meta, _mapped_result(results, task_map, "tvdb"))
    if _tv_episode_requested(meta):
        _apply_tvmaze_episode_result(
            meta, _mapped_result(results, task_map, "tvmaze_episode")
        )
        _apply_tmdb_episode_result(
            meta, _mapped_result(results, task_map, "tmdb_episode")
        )
        return
    if _tv_pack_requested(meta):
        _apply_tmdb_season_result(
            meta, _mapped_result(results, task_map, "tmdb_season")
        )


def _append_imdb_tmdb_tv_tasks(
    meta: Meta,
    filename: str,
    tmdb_manager: TmdbManager,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
) -> None:
    _append_task(
        tasks,
        task_map,
        "tvmaze",
        _tvmaze_search_task(meta, filename, tmdb_manager, full_tuple=True),
    )
    if _tv_episode_requested(meta):
        episode_task = _tmdb_episode_task(meta, tmdb_manager)
        if episode_task is not None:
            _append_task(tasks, task_map, "tmdb_episode", episode_task)
        return
    if _tv_pack_requested(meta):
        _append_tv_pack_task(meta, tasks, task_map, tmdb_manager)


def _apply_primary_tmdb_result(meta: Meta, result: Any) -> None:
    if isinstance(result, Exception):
        logger.info(
            f"[bold red]TMDB metadata retrieval failed: {result!s}[/bold red]"
        )
        return
    if not result:
        logger.info(
            f"[bold red]Failed to retrieve essential metadata from TMDB ID: {meta.tmdb_id}[/bold red]"
        )
        return
    meta.update(result)


def _apply_imdb_tmdb_tv_results(
    meta: Meta, results: list[Any], task_map: dict[str, int]
) -> None:
    if meta.category != "TV":
        return
    _apply_tvmaze_search_result(
        meta, _mapped_result(results, task_map, "tvmaze")
    )
    if _tv_episode_requested(meta):
        _apply_tmdb_episode_result(
            meta, _mapped_result(results, task_map, "tmdb_episode")
        )
        return
    if _tv_pack_requested(meta):
        _apply_tmdb_season_result(
            meta, _mapped_result(results, task_map, "tmdb_season")
        )


def _append_full_tv_tasks(
    meta: Meta,
    filename: str,
    tvdb_handler: Any,
    tmdb_manager: TmdbManager,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
) -> None:
    _append_task(
        tasks,
        task_map,
        "tvmaze",
        _tvmaze_search_task(meta, filename, tmdb_manager),
    )
    _append_task(
        tasks, task_map, "tvdb", _tvdb_episodes_task(meta, tvdb_handler)
    )
    if _tv_episode_requested(meta):
        tmdb_episode = _tmdb_episode_task(meta, tmdb_manager)
        if tmdb_episode is not None:
            _append_task(tasks, task_map, "tmdb_episode", tmdb_episode)
    elif _tv_pack_requested(meta):
        _append_tv_pack_task(meta, tasks, task_map, tmdb_manager)


def _full_id_tasks(
    meta: Meta,
    filename: str,
    tvdb_handler: Any,
    tmdb_manager: TmdbManager,
) -> tuple[list[Awaitable[Any]], dict[str, int]]:
    tasks: list[Awaitable[Any]] = []
    task_map: dict[str, int] = {}
    _append_task(
        tasks,
        task_map,
        "tmdb",
        _tmdb_metadata_task(meta, tmdb_manager, filename),
    )
    _append_task(tasks, task_map, "imdb", _imdb_info_task(meta, tmdb_manager))
    if meta.category == "TV":
        _append_full_tv_tasks(
            meta, filename, tvdb_handler, tmdb_manager, tasks, task_map
        )
    return tasks, task_map


class MetadataSearchingManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.tvdb_handler = TvdbData(config)
        self.tmdb_manager = TmdbManager(config)

    async def all_ids(self, meta: Meta) -> Meta:
        return await all_ids(meta, self.tvdb_handler, self.tmdb_manager)

    async def imdb_tmdb_tvdb(self, meta: Meta, filename: str) -> Meta:
        return await imdb_tmdb_tvdb(
            meta, filename, self.tvdb_handler, self.tmdb_manager
        )

    async def imdb_tvdb(self, meta: Meta, filename: str) -> Meta:
        return await imdb_tvdb(
            meta, filename, self.tvdb_handler, self.tmdb_manager
        )

    async def imdb_tmdb(self, meta: Meta, filename: str) -> Meta:
        return await imdb_tmdb(
            meta, filename, self.tvdb_handler, self.tmdb_manager
        )

    async def get_tvmaze_tvdb(
        self,
        filename: str,
        search_year: str,
        imdb: int | str | None,
        tmdb: int | str | None,
        manual_date: str | None = None,
        tvmaze_manual: str | None = None,
        year: str = "",
        tv_movie: bool = False,
        base_dir: str = "",
    ) -> tuple[int, int, Any | None, str]:
        return await get_tvmaze_tvdb(
            filename,
            search_year,
            imdb,
            tmdb,
            self.tvdb_handler,
            manual_date=manual_date,
            tvmaze_manual=tvmaze_manual,
            year=year,
            tv_movie=tv_movie,
            base_dir=base_dir,
            config=self.config,
        )

    async def get_tv_data(self, meta: Meta) -> Meta:
        return await get_tv_data(meta, self.tvdb_handler, self.tmdb_manager)

    async def get_tvdb_tvmaze_tmdb_episode_data(self, meta: Meta) -> Meta:
        return await get_tvdb_tvmaze_tmdb_episode_data(
            meta, self.tvdb_handler, self.tmdb_manager
        )


async def all_ids(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    logger.debug(
        "[yellow]Starting metadata retrieval with all IDs present[/yellow]"
    )
    tasks, task_map = _all_id_tasks(meta, tvdb_handler, tmdb_manager)
    results = await _gather_task_results(
        tasks, error_prefix="Error occurred while gathering tasks"
    )
    if results is None:
        return meta
    tmdb_result = _mapped_result(results, task_map, "tmdb")
    if not isinstance(tmdb_result, Exception) and tmdb_result:
        meta.update(tmdb_result)
    else:
        logger.warning("[yellow]Warning: Could not get TMDB metadata")
    _apply_imdb_info_result(meta, _mapped_result(results, task_map, "imdb"))
    _apply_all_id_tv_results(meta, results, task_map)
    return meta


async def imdb_tmdb_tvdb(
    meta: Meta, filename: str, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    logger.debug("[yellow]IMDb, TMDb, and TVDb IDs are all present[/yellow]")
    tasks, task_map = _full_id_tasks(
        meta, filename, tvdb_handler, tmdb_manager
    )
    results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)
    _apply_tmdb_metadata_result(
        meta, _mapped_result(results, task_map, "tmdb")
    )
    _apply_imdb_info_result(meta, _mapped_result(results, task_map, "imdb"))
    if meta.category != "TV":
        return meta
    _apply_tvmaze_id_result(meta, _mapped_result(results, task_map, "tvmaze"))
    _apply_tvdb_episode_result(meta, _mapped_result(results, task_map, "tvdb"))
    if _tv_episode_requested(meta):
        _apply_tmdb_episode_result(
            meta, _mapped_result(results, task_map, "tmdb_episode")
        )
    elif _tv_pack_requested(meta):
        _apply_tmdb_season_result(
            meta, _mapped_result(results, task_map, "tmdb_season")
        )
    return meta


async def imdb_tvdb(
    meta: Meta, filename: str, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    logger.debug("[yellow]Both IMDb and TVDB IDs are present[/yellow]")
    tasks: list[Awaitable[Any]] = [
        tmdb_manager.get_tmdb_from_imdb(
            meta.imdb_id,
            meta.tvdb_id,
            meta.search_year,
            filename,
            debug=meta.debug,
            mode=(meta.mode if meta.mode is not None else "non_cli"),
            category_preference=meta.category,
            unattended=meta.unattended,
        ),
        _tvmaze_search_task(meta, filename, tmdb_manager),
        _imdb_info_task(meta, tmdb_manager),
    ]
    if meta.category == "TV":
        tasks.append(_tvdb_episodes_task(meta, tvdb_handler))
    results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)
    _apply_tmdb_from_imdb_result(meta, results[0])
    _apply_tvmaze_id_result(meta, results[1])
    _apply_imdb_info_result(meta, results[2])
    if meta.category == "TV" and len(results) > 3:
        _apply_tvdb_episode_result(meta, results[3])
    return meta


async def imdb_tmdb(
    meta: Meta, filename: str, _tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    tasks: list[Awaitable[Any]] = [
        _tmdb_metadata_task(
            meta, tmdb_manager, filename, include_quickie=True
        ),
        _imdb_info_task(meta, tmdb_manager),
    ]
    task_map: dict[str, int] = {"tmdb": 0, "imdb": 1}
    if meta.category == "TV":
        _append_imdb_tmdb_tv_tasks(
            meta, filename, tmdb_manager, tasks, task_map
        )
    results = await _gather_task_results(tasks)
    if results is None:
        return meta
    _apply_primary_tmdb_result(meta, _mapped_result(results, task_map, "tmdb"))
    _apply_imdb_info_result(meta, _mapped_result(results, task_map, "imdb"))
    _apply_imdb_tmdb_tv_results(meta, results, task_map)
    return meta


def _has_external_identity(
    imdb: int | str | None, tmdb: int | str | None
) -> bool:
    return bool(imdb and imdb != 0) or bool(tmdb and tmdb != 0)


def _tmdb_only_identity(
    imdb: int | str | None, tmdb: int | str | None
) -> bool:
    return bool(tmdb and tmdb != 0) and not bool(imdb and imdb != 0)


def _tvmaze_lookup_id(result: Any) -> int:
    if isinstance(result, int):
        return result
    tvmaze_id, _tvdb_id = _tvmaze_tuple_ids(result)
    if tvmaze_id is not None:
        return tvmaze_id
    if isinstance(result, Exception):
        logger.info(f"[red]TVMaze API call failed: {result}[/red]")
    else:
        logger.info(
            f"[yellow]Unexpected TVMaze result type: {type(result)}[/yellow]"
        )
    return 0


def _tvdb_external_tuple(result: Any) -> tuple[int, str] | None:
    if not isinstance(result, tuple):
        return None
    values = cast(tuple[Any, ...], result)
    if len(values) != 2:
        return None
    tvdb_id, name = values
    tvdb = _coerce_int(tvdb_id) or 0
    tvdb_name = name if isinstance(name, str) else ""
    return tvdb, tvdb_name


def _log_tvdb_external_name(tvdb_name: str) -> None:
    if tvdb_name:
        logger.debug(f"[green]Got TVDb series name: {tvdb_name}[/green]")


def _log_unexpected_tvdb_external_result(result: Any) -> None:
    if result is not None:
        logger.info(
            f"[yellow]Unexpected TVDb lookup result type: {type(result)}[/yellow]"
        )


def _tvdb_external_result(result: Any) -> tuple[int, str]:
    if isinstance(result, Exception):
        logger.info(f"[yellow]TVDb lookup failed: {result}[/yellow]")
        return 0, ""
    if isinstance(result, int):
        return result, ""
    parsed = _tvdb_external_tuple(result)
    if parsed is None:
        _log_unexpected_tvdb_external_result(result)
        return 0, ""
    tvdb, tvdb_name = parsed
    _log_tvdb_external_name(tvdb_name)
    return tvdb, tvdb_name


def _tvdb_title_tuple(result: Any) -> tuple[int, Any | None] | None:
    if not isinstance(result, tuple):
        return None
    values = cast(tuple[Any, ...], result)
    if len(values) != 2:
        return None
    series_results, series_id = values
    return _coerce_int(
        series_id
    ) or 0, series_results if series_results else None


def _tvdb_title_result(result: Any) -> tuple[int, Any | None]:
    if isinstance(result, Exception):
        logger.info(
            f"[yellow]TVDb series data retrieval failed: {result}[/yellow]"
        )
        return 0, None
    if not result:
        return 0, None
    parsed = _tvdb_title_tuple(result)
    if parsed is None:
        logger.info(
            f"[yellow]Unexpected TVDb result format: {result}[/yellow]"
        )
        return 0, None
    tvdb, series_results = parsed
    if tvdb:
        logger.debug(f"[green]Got TVDb series ID: {tvdb}[/green]")
    return tvdb, series_results


def _tvmaze_tuple_tvdb_id(result: Any) -> int:
    _tvmaze_id, tvdb_id = _tvmaze_tuple_ids(result)
    return tvdb_id or 0


def _resolved_tvmaze_tvdb_ids(
    tvmaze: int,
    tvdb: int,
    tvmaze_result: Any,
    *,
    tmdb_only: bool,
) -> tuple[int, int]:
    if tmdb_only:
        return 0, tvdb
    if tvdb or not tvmaze:
        return tvmaze, tvdb
    return tvmaze, _tvmaze_tuple_tvdb_id(tvmaze_result)


async def get_tvmaze_tvdb(
    filename: str,
    search_year: str,
    imdb: int | str | None,
    tmdb: int | str | None,
    tvdb_handler: Any,
    manual_date: str | None = None,
    tvmaze_manual: str | None = None,
    year: str = "",
    tv_movie: bool = False,
    base_dir: str = "",
    config: dict[str, Any] | None = None,
) -> tuple[int, int, Any | None, str]:
    logger.debug("[yellow]Finding both TVMaze and TVDb IDs[/yellow]")
    external_identity = _has_external_identity(imdb, tmdb)
    tasks: list[Awaitable[Any]] = [
        tvmaze_manager.search_tvmaze(
            filename,
            search_year,
            imdb,
            0,
            manual_date=manual_date,
            tvmaze_manual=tvmaze_manual,
            return_full_tuple=True,
            base_dir=base_dir,
            config=config,
        )
    ]
    if external_identity:
        tasks.append(
            tvdb_handler.get_tvdb_by_external_id(
                imdb=imdb, tmdb=tmdb, tv_movie=tv_movie
            )
        )
    else:
        tasks.append(
            tvdb_handler.search_tvdb_series(filename=filename, year=year)
        )
    results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)
    tvmaze_result = results[0]
    tvmaze = _tvmaze_lookup_id(tvmaze_result)
    tvdb_result = results[1] if len(results) > 1 else None
    if external_identity:
        tvdb, tvdb_name = _tvdb_external_result(tvdb_result)
        tvdb_data = None
    else:
        tvdb, tvdb_data = _tvdb_title_result(tvdb_result)
        tvdb_name = ""
    tvmaze, tvdb = _resolved_tvmaze_tvdb_ids(
        tvmaze,
        tvdb,
        tvmaze_result,
        tmdb_only=_tmdb_only_identity(imdb, tmdb),
    )
    logger.debug(f"[blue]TVMaze ID: {tvmaze} | TVDb ID: {tvdb}[/blue]")
    return tvmaze, tvdb, tvdb_data, tvdb_name


def _ensure_tvdb_series_name(meta: Meta) -> None:
    if "tvdb_series_name" not in meta:
        meta.tvdb_series_name = None


def _single_tv_episode(meta: Meta) -> bool:
    return not meta.tv_pack and meta.episode_int != 0


def _should_fetch_combined_tv_episode(meta: Meta) -> bool:
    return all(
        (
            not meta.we_checked_tvdb,
            not meta.we_asked_tvmaze,
            meta.tvmaze_id != 0,
            meta.tvdb_id != 0,
            meta.tmdb_id != 0,
            not meta.anime,
        )
    )


async def _fetch_direct_tvdb_episode_data(
    meta: Meta, tvdb_handler: Any
) -> None:
    if not meta.tvdb_id or meta.we_checked_tvdb:
        return
    result = await tvdb_handler.get_tvdb_episodes(
        meta.tvdb_id,
        meta.base_dir,
        season=meta.season_int,
        episode=meta.episode_int,
        aired_date=meta.daily_episode_title,
        original_language=meta.original_language,
    )
    payload = _tvdb_episode_payload(result)
    if payload is None:
        return
    tvdb_episode_data, tvdb_name = payload
    if tvdb_episode_data:
        meta.tvdb_episode_data = tvdb_episode_data
    _apply_tvdb_series_metadata(meta, tvdb_episode_data, tvdb_name)


def _apply_specific_tvdb_episode(meta: Meta, result: Any) -> None:
    if not isinstance(result, tuple):
        raise ValueError("Unexpected TVDb episode detail payload")
    values = cast(tuple[Any, ...], result)
    if len(values) != 7:
        raise ValueError("Unexpected TVDb episode detail payload")
    (
        meta.tvdb_season_name,
        meta.tvdb_episode_name,
        meta.tvdb_overview,
        meta.tvdb_season,
        meta.tvdb_episode,
        meta.tvdb_episode_year,
        meta.tvdb_episode_id,
    ) = values


async def _fetch_specific_tvdb_episode(meta: Meta, tvdb_handler: Any) -> None:
    if not meta.tvdb_episode_data or not meta.tvdb_id:
        return
    try:
        result = await tvdb_handler.get_specific_episode_data(
            meta.tvdb_episode_data,
            meta.season_int,
            meta.episode_int,
            aired_date=meta.daily_episode_title,
        )
        _apply_specific_tvdb_episode(meta, result)
    except Exception as error:
        logger.error(f"[red]Error fetching TVDb episode data: {error}[/red]")


def _usable_episode_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if lowered.startswith("episode") or "tba" in lowered:
        return None
    return value


def _apply_tvdb_episode_summary(meta: Meta) -> None:
    if isinstance(meta.tvdb_episode_name, str):
        meta.auto_episode_title = _usable_episode_title(meta.tvdb_episode_name)
    if meta.tvdb_overview:
        meta.overview_meta = meta.tvdb_overview


def _should_apply_tvdb_season(meta: Meta, season: int | None) -> bool:
    if season is None or season == meta.season_int:
        return False
    return not meta.season and not meta.no_season and not meta.manual_date


def _should_apply_tvdb_episode(meta: Meta, episode: int | None) -> bool:
    if episode is None or episode == meta.episode_int:
        return False
    return not meta.episode and not meta.manual_date


def _apply_tvdb_numbering(meta: Meta) -> None:
    tvdb_season = _coerce_int(meta.tvdb_season)
    meta.tvdb_season_int = tvdb_season
    if _should_apply_tvdb_season(meta, tvdb_season):
        season_value = cast(int, tvdb_season)
        meta.season_int = season_value
        meta.season = f"S{season_value:02d}"
    tvdb_episode = _coerce_int(meta.tvdb_episode)
    meta.tvdb_episode_int = tvdb_episode
    if _should_apply_tvdb_episode(meta, tvdb_episode):
        episode_value = cast(int, tvdb_episode)
        meta.episode_int = episode_value
        meta.episode = f"E{episode_value:02d}"


def _needs_tvmaze_episode_data(meta: Meta) -> bool:
    return (
        "tvmaze_episode_data" not in meta or meta.tvmaze_episode_data is None
    )


async def _ensure_tvmaze_episode_data(meta: Meta) -> None:
    if not _needs_tvmaze_episode_data(meta):
        return
    meta.tvmaze_episode_data = {}
    identity = _tvmaze_episode_identity(meta)
    if identity is None:
        return
    tvmaze_id, season, episode = identity
    result = await tvmaze_manager.get_tvmaze_episode_data(
        tvmaze_id, season, episode, meta
    )
    if result:
        meta.tvmaze_episode_data = result


def _tvmaze_episode_payload(meta: Meta) -> dict[str, Any]:
    value = meta.tvmaze_episode_data or {}
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _apply_missing_episode_title(meta: Meta, value: Any) -> None:
    if meta.auto_episode_title is None and isinstance(value, str):
        meta.auto_episode_title = _usable_episode_title(value)


def _apply_missing_overview(meta: Meta, value: Any) -> None:
    if meta.overview_meta is None and value is not None:
        meta.overview_meta = value


def _apply_tvmaze_fallback(meta: Meta) -> None:
    if meta.auto_episode_title is not None and meta.overview_meta is not None:
        return
    data = _tvmaze_episode_payload(meta)
    _apply_missing_episode_title(
        meta, data.get("episode_name") or data.get("name")
    )
    _apply_missing_overview(meta, data.get("overview"))


def _uses_tvdb_absolute_mapping(meta: Meta) -> bool:
    return (
        "tvdb_episode_int" in meta
        and meta.tvdb_episode_int != 0
        and meta.tvdb_episode_int != meta.episode_int
    )


def _tmdb_fallback_episode_identity(
    meta: Meta,
) -> tuple[int | None, int | None]:
    episode = _coerce_int(meta.episode_int)
    if not _uses_tvdb_absolute_mapping(meta):
        return _coerce_int(meta.season_int), episode
    season = _coerce_int(meta.tvdb_season_int)
    logger.debug(
        f"[yellow]Using absolute episode number from TVDb: {episode}[/yellow]"
    )
    logger.debug(
        f"[yellow]Using matching season number from TVDb: {season}[/yellow]"
    )
    return season, episode


def _existing_tmdb_episode_data(meta: Meta) -> dict[str, Any] | None:
    value = meta.tmdb_episode_data
    if not value or not isinstance(value, dict):
        return None
    return cast(dict[str, Any], value)


def _tmdb_fallback_request(meta: Meta) -> tuple[int, int, int] | None:
    season, episode = _tmdb_fallback_episode_identity(meta)
    tmdb_id = _coerce_int(meta.tmdb_id)
    if tmdb_id is None or season is None or episode is None:
        return None
    return tmdb_id, season, episode


async def _tmdb_fallback_episode_details(
    meta: Meta, tmdb_manager: TmdbManager
) -> dict[str, Any]:
    existing = _existing_tmdb_episode_data(meta)
    if existing is not None:
        return existing
    request = _tmdb_fallback_request(meta)
    if request is None:
        return {}
    logger.debug("[yellow]Fetching TMDb episode metadata...")
    result = await tmdb_manager.get_episode_details(*request)
    return cast(dict[str, Any], result) if result else {}


def _apply_tmdb_episode_fallback(meta: Meta, details: dict[str, Any]) -> None:
    episode_name = details.get("name")
    if meta.auto_episode_title is None and isinstance(episode_name, str):
        meta.auto_episode_title = _usable_episode_title(episode_name)
    overview = details.get("overview")
    if not meta.overview_meta and overview is not None:
        meta.overview_meta = overview


async def _apply_tmdb_fallback_if_needed(
    meta: Meta, tmdb_manager: TmdbManager
) -> None:
    needs_fallback = (
        meta.auto_episode_title is None or meta.overview_meta is None
    )
    if not needs_fallback or not meta.episode_overview:
        return
    details = await _tmdb_fallback_episode_details(meta, tmdb_manager)
    _apply_tmdb_episode_fallback(meta, details)


async def _enrich_single_tv_episode(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    if _should_fetch_combined_tv_episode(meta):
        meta = await get_tvdb_tvmaze_tmdb_episode_data(
            meta, tvdb_handler, tmdb_manager
        )
    else:
        await _fetch_direct_tvdb_episode_data(meta, tvdb_handler)
    await _fetch_specific_tvdb_episode(meta, tvdb_handler)
    _apply_tvdb_episode_summary(meta)
    _apply_tvdb_numbering(meta)
    await _ensure_tvmaze_episode_data(meta)
    _apply_tvmaze_fallback(meta)
    await _apply_tmdb_fallback_if_needed(meta, tmdb_manager)
    return meta


async def _enrich_tv_pack(meta: Meta, tvdb_handler: Any) -> None:
    await _fetch_direct_tvdb_episode_data(meta, tvdb_handler)
    await _fetch_specific_tvdb_episode(meta, tvdb_handler)


async def _apply_tvdb_episode_imdb_id(meta: Meta, tvdb_handler: Any) -> None:
    episode_id = meta.tvdb_episode_id
    if episode_id is None:
        return
    meta.tvdb_imdb_id = await tvdb_handler.get_imdb_id_from_tvdb_episode_id(
        episode_id
    )


async def get_tv_data(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    _ensure_tvdb_series_name(meta)
    if _single_tv_episode(meta):
        meta = await _enrich_single_tv_episode(
            meta, tvdb_handler, tmdb_manager
        )
    elif meta.tv_pack:
        await _enrich_tv_pack(meta, tvdb_handler)
    await _apply_tvdb_episode_imdb_id(meta, tvdb_handler)
    return meta


def _append_combined_tvmaze_task(
    meta: Meta,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
) -> None:
    if not meta.tvmaze_id:
        return
    logger.debug("[yellow]Fetching TVMaze episode data...[/yellow]")
    task = _tvmaze_episode_task(meta)
    if task is not None:
        _append_task(tasks, task_map, "tvmaze", task)


def _append_combined_tvdb_task(
    meta: Meta,
    tvdb_handler: Any,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
) -> None:
    if not meta.tvdb_id:
        return
    logger.debug("[yellow]Fetching TVDb episode data...[/yellow]")
    _append_task(
        tasks, task_map, "tvdb", _tvdb_episodes_task(meta, tvdb_handler)
    )


def _append_combined_tmdb_task(
    meta: Meta,
    tmdb_manager: TmdbManager,
    tasks: list[Awaitable[Any]],
    task_map: dict[str, int],
) -> None:
    if not meta.tmdb_id:
        return
    logger.debug("[yellow]Fetching TMDb episode data...[/yellow]")
    task = _tmdb_episode_task(meta, tmdb_manager)
    if task is not None:
        _append_task(tasks, task_map, "tmdb", task)


def _combined_episode_tasks(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> tuple[list[Awaitable[Any]], dict[str, int]]:
    tasks: list[Awaitable[Any]] = []
    task_map: dict[str, int] = {}
    _append_combined_tvmaze_task(meta, tasks, task_map)
    _append_combined_tvdb_task(meta, tvdb_handler, tasks, task_map)
    _append_combined_tmdb_task(meta, tmdb_manager, tasks, task_map)
    return tasks, task_map


def _debug_tvdb_episode_payload(payload: tuple[Any, Any]) -> None:
    episodes_data, series_name = payload
    if isinstance(episodes_data, list):
        logger.info(
            f"[green]TVDb episodes list retrieved with {len(episodes_data)} episodes[/green]"
        )
    if series_name:
        logger.info(f"[green]TVDb series name: {series_name}[/green]")


def _debug_combined_episode_results(
    meta: Meta, tvdb_result: Any, tvdb_checked_before: bool
) -> None:
    should_log = (
        meta.debug and meta.we_checked_tvdb and not tvdb_checked_before
    )
    if not should_log:
        return
    payload = _tvdb_episode_payload(tvdb_result)
    if payload is not None:
        _debug_tvdb_episode_payload(payload)


def _apply_combined_tvmaze_result(meta: Meta, result: Any) -> None:
    if result is None:
        return
    _apply_tvmaze_episode_result(meta, result)
    if meta.we_asked_tvmaze:
        logger.debug(
            "[green]TVMaze episode data retrieved successfully.[/green]"
        )


def _apply_combined_tvdb_result(meta: Meta, result: Any) -> None:
    if result is None:
        return
    checked_before = bool(meta.we_checked_tvdb)
    _apply_tvdb_episode_result(meta, result)
    _debug_combined_episode_results(meta, result, checked_before)


def _apply_combined_tmdb_result(meta: Meta, result: Any) -> None:
    if result is None:
        return
    _apply_tmdb_episode_result(meta, result)
    if meta.we_checked_tmdb:
        logger.debug(
            "[green]TMDb episode data retrieved successfully.[/green]"
        )


def _apply_combined_episode_results(
    meta: Meta, results: list[Any], task_map: dict[str, int]
) -> None:
    _apply_combined_tvmaze_result(
        meta, _mapped_result(results, task_map, "tvmaze")
    )
    _apply_combined_tvdb_result(
        meta, _mapped_result(results, task_map, "tvdb")
    )
    _apply_combined_tmdb_result(
        meta, _mapped_result(results, task_map, "tmdb")
    )


async def get_tvdb_tvmaze_tmdb_episode_data(
    meta: Meta, tvdb_handler: Any, tmdb_manager: TmdbManager
) -> Meta:
    logger.debug("[yellow]Gathering TVDb and TVMaze episode data[/yellow]")
    tasks, task_map = _combined_episode_tasks(meta, tvdb_handler, tmdb_manager)
    if not tasks:
        return meta
    results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)
    _apply_combined_episode_results(meta, results, task_map)
    return meta


def _manual_douban_id(meta: Meta) -> int:
    try:
        return int(meta.douban_manual or 0)
    except ValueError, TypeError:
        logger.info(
            "[bold yellow]Invalid douban_manual value, ignoring.[/bold yellow]"
        )
        return 0


def _cached_douban_id(cached: Any) -> int | None:
    if is_cache_miss(cached) or not isinstance(cached, dict):
        return None
    return _coerce_int(cast(dict[str, Any], cached).get("id", 0)) or 0


def _douban_retryable_status(status_code: int) -> bool:
    return status_code in (408, 429) or 500 <= status_code < 600


async def _douban_request_attempt(
    client: httpx.AsyncClient, search_url: str
) -> tuple[httpx.Response | None, Exception | None]:
    try:
        response = await client.get(search_url)
        response.raise_for_status()
        return response, None
    except httpx.RequestError as error:
        return None, error
    except httpx.HTTPStatusError as error:
        if not _douban_retryable_status(error.response.status_code):
            raise
        return None, error


async def _douban_search_response(
    search_url: str, headers: dict[str, str]
) -> httpx.Response:
    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True
    ) as client:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            response, last_error = await _douban_request_attempt(
                client, search_url
            )
            if response is not None:
                return response
            if attempt < 3:
                logger.info(
                    f"[yellow]Douban request failed (attempt {attempt}/3). Retrying in 5 seconds...[/yellow]"
                )
                await asyncio.sleep(5)
        if last_error is not None:
            raise last_error
    raise RuntimeError("Douban request did not produce a response")


def _douban_id_from_html(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    result = soup.find("ul", class_="search_results_subjects")
    if result is None:
        return 0
    link_tag = result.find("a")
    if link_tag is None or "href" not in link_tag.attrs:
        return 0
    match = re.search(r"subject/(\d+)", str(link_tag["href"]))
    return int(match.group(1)) if match else 0


async def _cache_douban_id(cache: Any, imdb_id: Any, douban_id: int) -> None:
    await cache.set(
        "douban",
        "imdb_lookup",
        str(imdb_id),
        {"id": douban_id},
        **({"negative": True} if douban_id == 0 else {}),
    )


async def _uncached_douban_id(imdb_id: str) -> int:
    search_url = f"https://m.douban.com/search/?query={imdb_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = await _douban_search_response(search_url, headers)
    except Exception as error:
        logger.info(
            f"[bold yellow]Failed to fetch Douban ID for IMDb ID {imdb_id}: {error}[/bold yellow]"
        )
        return 0
    douban_id = _douban_id_from_html(response.text)
    if not douban_id:
        logger.info(
            f"[bold yellow]No Douban ID found for IMDb ID {imdb_id}.[/bold yellow]"
        )
    return douban_id


async def get_douban_id(meta: Meta) -> int:
    manual = _manual_douban_id(meta)
    if manual:
        logger.info(f"Using manual Douban ID: {manual}")
        return manual
    imdb_id = str(meta.imdb_tt or "")
    if not imdb_id:
        return 0
    cache = cache_for(meta.base_dir)
    cached = await cache.get("douban", "imdb_lookup", imdb_id)
    cached_id = _cached_douban_id(cached)
    if cached_id is not None:
        return cached_id
    douban_id = await _uncached_douban_id(imdb_id)
    await _cache_douban_id(cache, imdb_id, douban_id)
    return douban_id
