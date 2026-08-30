# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
# Restricted-use credential — permitted only under UAPL v1.0 and associated service provider terms
import asyncio
import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tvdb_v4_official import TVDB

from src.integrations.observability.runtime_support import logger

YEAR_PATTERN = re.compile(r"\((19\d\d|20[0-3]\d)\)")


tvdb: TVDB | None = None
_tvdb_init_error: Exception | None = None
_tvdb_error_reported = False


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], value)
            if isinstance(item, dict)
        ]
    return []


def _english_alias_names(aliases: list[dict[str, Any]]) -> list[str]:
    return [
        str(alias.get("name", "")).strip()
        for alias in aliases
        if alias.get("language") == "eng"
        and str(alias.get("name", "")).strip()
    ]


def _pick_eng_alias(
    aliases: list[dict[str, Any]],
) -> str | None:
    if not aliases:
        return None

    eng_aliases = _english_alias_names(aliases)
    if not eng_aliases:
        return None

    eng_alias = eng_aliases[-1]
    logger.debug(f"[blue]English alias: {eng_alias}[/blue]")
    return eng_alias


def _extract_year_from_text(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None

    match = re.search(r"(19\d\d|20[0-3]\d)", str(value))
    return match.group(1) if match else None


def _best_effort_series_year(series_info: dict[str, Any] | None) -> str | None:
    if not series_info:
        return None

    return _extract_year_from_text(
        series_info.get("year")
    ) or _extract_year_from_text(series_info.get("slug"))


def _translation_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _translation_aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(alias).strip()
        for alias in cast(list[Any], value)
        if str(alias).strip()
    ]


def _english_translation(
    client: Any, series_id: int
) -> tuple[str | None, list[str]]:
    try:
        translation = cast(
            dict[str, Any], client.get_series_translation(series_id, "eng")
        )
    except Exception as error:
        logger.debug(
            f"[yellow]Could not retrieve TVDB English series translation: {error}[/yellow]"
        )
        return None, []
    return (
        _translation_name(translation.get("name")),
        _translation_aliases(translation.get("aliases")),
    )


def _first_alias_year(aliases: list[str]) -> str | None:
    for alias in aliases:
        year = _extract_year_from_text(alias)
        if year:
            return year
    return None


def _series_title(
    translation_name: str | None,
    translation_aliases: list[str],
    aliases: list[dict[str, Any]],
) -> str | None:
    if translation_name:
        return translation_name
    if translation_aliases:
        return translation_aliases[-1]
    return _pick_eng_alias(aliases)


def _series_translation_metadata(
    client: Any,
    series_id: int,
    aliases: list[dict[str, Any]],
    _series_info: dict[str, Any] | None = None,
) -> dict[str, str | None]:
    translation_name, translation_aliases = _english_translation(
        client, series_id
    )
    english_aliases = [*translation_aliases, *_english_alias_names(aliases)]
    title = _series_title(translation_name, translation_aliases, aliases)
    year = _first_alias_year(english_aliases)
    if year is None and not english_aliases:
        year = _best_effort_series_year(_series_info)
    if title:
        suffix = f" ({year})" if year else ""
        logger.debug(
            f"[blue]TVDB English series title: {title}{suffix}[/blue]"
        )
    return {"series_title": title, "series_year": year}


def _tvdb_api_key(config: dict[str, Any] | None) -> str:
    if not isinstance(config, dict):
        return ""
    defaults = config.get("DEFAULT", {})
    if not isinstance(defaults, dict):
        return ""
    value = cast(dict[str, Any], defaults).get("tvdb_api", "")
    return value.strip() if isinstance(value, str) else ""


def _report_missing_tvdb_key() -> None:
    global _tvdb_error_reported
    if _tvdb_error_reported:
        return
    _tvdb_error_reported = True
    logger.info(
        "[yellow]TVDB API key is missing in config.py under DEFAULT section. Continuing without TVDB.[/yellow]"
    )


def _create_tvdb_client(api_key: str) -> TVDB | None:
    global _tvdb_init_error
    try:
        return TVDB(api_key)
    except Exception as error:
        _tvdb_init_error = error
        return None


def _report_tvdb_unavailable() -> None:
    global _tvdb_error_reported
    if _tvdb_error_reported:
        return
    _tvdb_error_reported = True
    if _tvdb_init_error is None:
        logger.info(
            "[yellow]TVDB unavailable; continuing without TVDB.[/yellow]"
        )
        return
    logger.info(
        f"[yellow]TVDB login failed; continuing without TVDB. Reason: {_tvdb_init_error}[/yellow]"
    )
    logger.info(
        "[yellow]This is usually a local Python CA/cert issue. "
        "Fix options: install/update Windows roots, or set SSL_CERT_FILE to certifi's bundle "
        '(e.g. `python -c "import certifi; print(certifi.where())"`).[/yellow]'
    )


def _get_tvdb_or_warn(config: dict[str, Any] | None = None) -> TVDB | None:
    global tvdb
    if tvdb is not None:
        return tvdb
    api_key = _tvdb_api_key(config)
    if not api_key:
        _report_missing_tvdb_key()
        return None
    tvdb = _create_tvdb_client(api_key)
    if tvdb is not None:
        return tvdb
    _report_tvdb_unavailable()
    return None


def _alias_name(alias: Any) -> str:
    if isinstance(alias, dict):
        return str(cast(dict[str, Any], alias).get("name", ""))
    return str(alias)


def _result_has_alias_year(result: dict[str, Any], year: str) -> bool:
    aliases = result.get("aliases", [])
    if not isinstance(aliases, list):
        return False
    return any(
        f"({year})" in _alias_name(alias)
        for alias in cast(list[Any], aliases)
    )


def _first_exact_year_result(
    results: list[dict[str, Any]], year: str
) -> dict[str, Any] | None:
    for result in results:
        if result.get("year") == year:
            return result
    return None


def _first_alias_year_result(
    results: list[dict[str, Any]], year: str
) -> dict[str, Any] | None:
    for result in results:
        if _result_has_alias_year(result, year):
            return result
    return None


def _best_series_result(
    results: list[dict[str, Any]], year: str | None
) -> dict[str, Any]:
    if not year:
        return results[0]
    exact = _first_exact_year_result(results, year)
    if exact is not None:
        return exact
    alias_match = _first_alias_year_result(results, year)
    return alias_match if alias_match is not None else results[0]


@dataclass(frozen=True)
class _EpisodeRequest:
    season: int | None
    episode: int | None
    absolute: int | None
    aired: str | None


def _normalized_air_date(value: str | None) -> str | None:
    return value.strip().replace(".", "-") if value else None


def _episode_request(
    season: int | str | None,
    episode: int | str | None,
    absolute_number: int | str | None,
    aired_date: str | None,
) -> _EpisodeRequest:
    return _EpisodeRequest(
        season=_coerce_int(season),
        episode=_coerce_int(episode),
        absolute=_coerce_int(absolute_number),
        aired=_normalized_air_date(aired_date),
    )


def _request_accepts_any_episode(request: _EpisodeRequest) -> bool:
    return (
        request.episode in (None, 0)
        and request.absolute is None
        and request.aired is None
    )


def _episode_matches_air_date(
    episode: dict[str, Any], request: _EpisodeRequest
) -> bool:
    return bool(request.aired) and episode.get("aired") == request.aired


def _episode_matches_absolute(
    episode: dict[str, Any], request: _EpisodeRequest
) -> bool:
    return (
        request.absolute is not None
        and episode.get("absoluteNumber") == request.absolute
    )


def _episode_matches_number(
    episode: dict[str, Any], request: _EpisodeRequest
) -> bool:
    if request.season is None or request.episode in (None, 0):
        return False
    return (
        episode.get("seasonNumber") == request.season
        and episode.get("number") == request.episode
    )


def _episode_matches_request(
    episode: dict[str, Any], request: _EpisodeRequest
) -> bool:
    return any(
        (
            _episode_matches_air_date(episode, request),
            _episode_matches_absolute(episode, request),
            _episode_matches_number(episode, request),
        )
    )


def _episode_is_present(
    episodes: list[dict[str, Any]], request: _EpisodeRequest
) -> bool:
    if not episodes:
        return False
    if _request_accepts_any_episode(request):
        return True
    return any(
        _episode_matches_request(episode, request) for episode in episodes
    )


def _series_alias_for_language(
    episodes_data: dict[str, Any], original_language: str | None
) -> str | None:
    if original_language == "en":
        return None
    value = episodes_data.get("series_title")
    return value if isinstance(value, str) else None


class TvdbData:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def search_tvdb_series(
        self,
        filename: str,
        year: str | None = None,
    ) -> tuple[list[dict[str, Any]] | None, int | None]:
        logger.debug(f"filename for TVDB search: {filename} year: {year}")
        client = _get_tvdb_or_warn(self.config)
        if client is None:
            return None, None
        results = _as_dict_list(
            cast(Any, client).search(
                {filename}, year=year, type="series", lang="eng"
            )
        )
        await asyncio.sleep(0.1)
        if not results:
            logger.info("[yellow]No TVDB results found[/yellow]")
            return None, None
        try:
            series_id = _best_series_result(results, year)["tvdb_id"]
        except Exception as error:
            logger.error(f"[red]Error: {error}[/red]")
            return None, None
        logger.debug(f"[blue]TVDB series ID: {series_id}[/blue]")
        return results, _coerce_int(series_id)

    @staticmethod
    def _cache_path(
        base_dir: str | bool | None, series_id: int
    ) -> Path | None:
        if not isinstance(base_dir, str) or not base_dir:
            return None
        return Path(base_dir) / "data" / "tvdb" / f"{series_id}.json"

    @staticmethod
    def _cached_episode_payload(
        cached: dict[str, Any], episodes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        aliases = cached.get("aliases", [])
        return {
            "episodes": episodes,
            "aliases": aliases if isinstance(aliases, list) else [],
            "slug": cached.get("slug")
            if isinstance(cached.get("slug"), str)
            else None,
            "series_title": cached.get("series_title")
            if isinstance(cached.get("series_title"), str)
            else None,
            "series_year": cached.get("series_year")
            if isinstance(cached.get("series_year"), str)
            else None,
        }

    @staticmethod
    def _needs_series_metadata(data: dict[str, Any]) -> bool:
        return not data.get("series_title") and not data.get("series_year")

    @staticmethod
    def _series_metadata(
        client: Any,
        series_id: int,
        aliases: list[dict[str, Any]],
    ) -> dict[str, str | None]:
        series_info = cast(
            dict[str, Any], cast(Any, client).get_series_extended(series_id)
        )
        series_aliases = _as_dict_list(series_info.get("aliases", aliases))
        return _series_translation_metadata(
            client, series_id, series_aliases, _series_info=series_info
        )

    def _refresh_cached_series_metadata(
        self, series_id: int, data: dict[str, Any]
    ) -> None:
        if not self._needs_series_metadata(data):
            return
        client = _get_tvdb_or_warn(self.config)
        if client is None:
            return
        try:
            aliases = _as_dict_list(data.get("aliases", []))
            data.update(self._series_metadata(client, series_id, aliases))
        except Exception as error:
            logger.debug(
                f"[yellow]Could not refresh cached TVDB series metadata: {error}[/yellow]"
            )

    @staticmethod
    def _read_cached_episode_file(
        cache_path: Path | None, series_id: int
    ) -> dict[str, Any] | None:
        if cache_path is None:
            return None
        try:
            if not cache_path.exists():
                return None
            with cache_path.open("r", encoding="utf-8") as handle:
                cached = json.load(handle)
        except Exception as error:
            logger.debug(
                f"[yellow]Failed to read TVDB cache for {series_id}: {error}[/yellow]"
            )
            return None
        return (
            cast(dict[str, Any], cached) if isinstance(cached, dict) else None
        )

    async def _cached_episode_result(
        self,
        cache_path: Path | None,
        series_id: int,
        request: _EpisodeRequest,
        original_language: str | None,
    ) -> tuple[dict[str, Any], str | None] | None:
        cached_dict = self._read_cached_episode_file(cache_path, series_id)
        if cached_dict is None:
            return None
        episodes = _as_dict_list(cached_dict.get("episodes", []))
        if not _episode_is_present(episodes, request):
            logger.debug(
                f"[yellow]Cached TVDB data for {series_id} does not include requested episode; refreshing from TVDB[/yellow]"
            )
            return None
        logger.debug(
            f"[cyan]Using cached TVDB episodes for {series_id}[/cyan]"
        )
        data = self._cached_episode_payload(cached_dict, episodes)
        self._refresh_cached_series_metadata(series_id, data)
        return data, _series_alias_for_language(data, original_language)

    @staticmethod
    def _page_payload(
        response: Any, page: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not isinstance(response, dict):
            return _as_dict_list(response), None
        payload = cast(dict[str, Any], response)
        slug_value = payload.get("slug") if page == 0 else None
        slug = slug_value if isinstance(slug_value, str) else None
        return _as_dict_list(payload.get("episodes", [])), slug

    @staticmethod
    async def _fetch_episode_page(
        client: Any, series_id: int, page: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        if page > 0:
            logger.debug(
                f"[cyan]Fetching TVDB episodes page {page + 1}[/cyan]"
            )
        response = cast(Any, client).get_series_episodes(
            series_id,
            season_type="default",
            page=page,
            lang="eng",
        )
        return TvdbData._page_payload(response, page)

    @classmethod
    async def _episode_page_or_error(
        cls, client: Any, series_id: int, page: int
    ) -> tuple[list[dict[str, Any]], str | None] | None:
        try:
            return await cls._fetch_episode_page(client, series_id, page)
        except Exception as error:
            logger.debug(
                f"[yellow]Error fetching page {page + 1}: {error}[/yellow]"
            )
            if page == 0:
                raise
            return None

    @staticmethod
    def _updated_series_slug(
        current: str | None, incoming: str | None
    ) -> str | None:
        return incoming if incoming is not None else current

    @classmethod
    async def _fetch_episode_pages(
        cls, client: Any, series_id: int
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        all_episodes: list[dict[str, Any]] = []
        series_slug: str | None = None
        pages_fetched = 0
        for page in range(20):
            page_data = await cls._episode_page_or_error(
                client, series_id, page
            )
            if page_data is None:
                break
            current, slug = page_data
            series_slug = cls._updated_series_slug(series_slug, slug)
            if not current:
                logger.debug(
                    f"[yellow]No episodes found on page {page + 1}, stopping pagination[/yellow]"
                )
                break
            all_episodes.extend(current)
            pages_fetched += 1
            logger.debug(
                f"[cyan]Retrieved {len(current)} episodes from page {page + 1} (total: {len(all_episodes)})[/cyan]"
            )
            if len(current) < 500:
                logger.debug(
                    f"[cyan]Page {page + 1} returned {len(current)} episodes (< 500), pagination complete[/cyan]"
                )
                break
            await asyncio.sleep(0.1)
        logger.debug(
            f"[green]Total episodes retrieved: {len(all_episodes)} across {max(pages_fetched, 1)} page(s)[/green]"
        )
        return all_episodes, series_slug, pages_fetched

    @staticmethod
    def _fresh_episode_payload(
        episodes: list[dict[str, Any]], slug: str | None
    ) -> dict[str, Any]:
        return {
            "episodes": episodes,
            "aliases": [],
            "slug": slug,
            "series_title": None,
            "series_year": None,
        }

    @classmethod
    def _enrich_episode_payload(
        cls, client: Any, series_id: int, data: dict[str, Any]
    ) -> None:
        if not data.get("episodes"):
            return
        try:
            series_info = cast(
                dict[str, Any],
                cast(Any, client).get_series_extended(series_id),
            )
            aliases = series_info.get("aliases")
            if aliases is not None:
                data["aliases"] = aliases
            aliases_list = _as_dict_list(data.get("aliases", []))
            data.update(
                _series_translation_metadata(
                    client,
                    series_id,
                    aliases_list,
                    _series_info=series_info,
                )
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Could not retrieve series aliases: {error}[/yellow]"
            )

    @staticmethod
    def _prepare_cache_directory(cache_path: Path) -> None:
        if os.name != "posix":
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(Exception):
            cache_path.parent.chmod(0o700)

    @classmethod
    def _write_episode_cache(
        cls, cache_path: Path, data: dict[str, Any], series_id: int
    ) -> None:
        try:
            cls._prepare_cache_directory(cache_path)
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            if os.name == "posix":
                with contextlib.suppress(Exception):
                    cache_path.chmod(0o644)
            logger.debug(
                f"[green]Cached TVDB episodes to {cache_path}[/green]"
            )
        except Exception as error:
            logger.debug(
                f"[yellow]Failed to write TVDB cache for {series_id}: {error}[/yellow]"
            )

    async def _fresh_episode_result(
        self,
        series_id: int,
        cache_path: Path | None,
        original_language: str | None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        client = _get_tvdb_or_warn(self.config)
        if client is None:
            return None, None
        try:
            episodes, slug, pages_fetched = await self._fetch_episode_pages(
                client, series_id
            )
        except Exception as error:
            logger.error(f"[red]Error getting episodes: {error}[/red]")
            return None, None
        data = self._fresh_episode_payload(episodes, slug)
        self._enrich_episode_payload(client, series_id, data)
        if cache_path is not None and pages_fetched > 1:
            self._write_episode_cache(cache_path, data, series_id)
        return data, _series_alias_for_language(data, original_language)

    async def get_tvdb_episodes(
        self,
        series_id: int | str,
        base_dir: str | bool | None = None,
        season: int | str | None = None,
        episode: int | str | None = None,
        absolute_number: int | str | None = None,
        aired_date: str | None = None,
        original_language: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if isinstance(base_dir, bool):
            base_dir = None
        series_id_int = _coerce_int(series_id)
        if series_id_int is None:
            logger.debug(
                f"[yellow]Invalid TVDB series ID: {series_id}[/yellow]"
            )
            return None, None
        request = _episode_request(
            season, episode, absolute_number, aired_date
        )
        cache_path = self._cache_path(base_dir, series_id_int)
        cached = await self._cached_episode_result(
            cache_path, series_id_int, request, original_language
        )
        if cached is not None:
            return cached
        return await self._fresh_episode_result(
            series_id_int, cache_path, original_language
        )

    @staticmethod
    def _format_imdb_remote_id(value: int | str) -> int | str:
        if isinstance(value, int):
            return f"tt{value:07d}"
        if not isinstance(value, str):
            return value
        if value.startswith("tt"):
            return value
        return f"tt{int(value):07d}" if value.isdigit() else value

    @staticmethod
    def _remote_payload(
        result: dict[str, Any], key: str
    ) -> dict[str, Any] | None:
        value = result.get(key)
        return cast(dict[str, Any], value) if isinstance(value, dict) else None

    @staticmethod
    def _remote_result_types(results: list[dict[str, Any]]) -> list[str]:
        return [next(iter(result.keys())) for result in results if result]

    @staticmethod
    def _translated_series_name(
        client: Any, series_id_value: Any, fallback: Any
    ) -> str | None:
        series_id = _coerce_int(series_id_value)
        fallback_name = str(fallback).strip() if fallback else None
        if series_id is None:
            return fallback_name
        try:
            series_info = cast(
                dict[str, Any],
                cast(Any, client).get_series_extended(series_id),
            )
            aliases = _as_dict_list(series_info.get("aliases", []))
            metadata = _series_translation_metadata(
                client, series_id, aliases, _series_info=series_info
            )
            return metadata.get("series_title") or fallback_name
        except Exception as error:
            logger.debug(
                f"[yellow]Could not retrieve translated TVDB series name: {error}[/yellow]"
            )
            return fallback_name

    @classmethod
    def _series_remote_match(
        cls, client: Any, results: list[dict[str, Any]], source: str
    ) -> tuple[int | None, str | None] | None:
        for result in results:
            series = cls._remote_payload(result, "series")
            if series is None:
                continue
            series_id = series.get("id")
            series_name = cls._translated_series_name(
                client, series_id, series.get("name")
            )
            logger.debug(
                f"[blue]TVDB series ID from {source}: {series_id}[/blue]"
            )
            return _coerce_int(series_id), series_name
        return None

    @classmethod
    def _episode_remote_match(
        cls, client: Any, results: list[dict[str, Any]]
    ) -> tuple[int | None, str | None] | None:
        for result in results:
            episode = cls._remote_payload(result, "episode")
            if episode is None or not episode.get("seriesId"):
                continue
            series_id = episode.get("seriesId")
            series_name = cls._translated_series_name(
                client, series_id, episode.get("seriesName")
            )
            logger.debug(
                f"[blue]TVDB series ID from episode entry (tv_movie): {series_id}[/blue]"
            )
            return _coerce_int(series_id), series_name
        return None

    @classmethod
    def _movie_remote_match(
        cls, results: list[dict[str, Any]], source: str
    ) -> tuple[int | None, str | None] | None:
        for result in results:
            movie = cls._remote_payload(result, "movie")
            if movie is None:
                continue
            movie_id = movie.get("id")
            movie_name = movie.get("name")
            logger.debug(
                f"[blue]TVDB movie ID from {source} (tv_movie): {movie_id}[/blue]"
            )
            return _coerce_int(movie_id), movie_name
        return None

    @classmethod
    def _remote_match(
        cls,
        client: Any,
        results: list[dict[str, Any]],
        source: str,
        tv_movie: bool,
    ) -> tuple[int | None, str | None] | None:
        series_match = cls._series_remote_match(client, results, source)
        if series_match is not None or not tv_movie:
            return series_match
        episode_match = cls._episode_remote_match(client, results)
        if episode_match is not None:
            return episode_match
        return cls._movie_remote_match(results, source)

    @classmethod
    async def _lookup_remote_id(
        cls,
        client: Any,
        remote_id: int | str,
        source: str,
        tv_movie: bool,
    ) -> tuple[int | None, str | None] | None:
        logger.debug(
            f"[cyan]Trying TVDB lookup with {source} ID: {remote_id}[/cyan]"
        )
        try:
            results = _as_dict_list(
                cast(Any, client).search_by_remote_id(remote_id)
            )
            await asyncio.sleep(0.1)
        except Exception as error:
            logger.debug(
                f"[red]Error getting TVDB by {source} ID: {error}[/red]"
            )
            return None
        if not results:
            logger.debug(
                f"[yellow]No TVDB series found for {source} ID[/yellow]"
            )
            return None
        logger.debug(f"[blue]results: {results}[/blue]")
        match = cls._remote_match(client, results, source, tv_movie)
        if match is not None:
            return match
        result_types = cls._remote_result_types(results)
        expected = "series or movie" if tv_movie else "series"
        logger.debug(
            f"[yellow]{source} search returned results but no {expected} found (got: {result_types})[/yellow]"
        )
        return None

    @classmethod
    def _external_queries(
        cls,
        imdb: int | str | None,
        tmdb: int | str | None,
    ) -> list[tuple[int | str, str]]:
        queries: list[tuple[int | str, str]] = []
        if imdb:
            queries.append((cls._format_imdb_remote_id(imdb), "IMDB"))
        if tmdb:
            queries.append((str(tmdb), "TMDB"))
        return queries

    async def get_tvdb_by_external_id(
        self,
        imdb: int | str | None,
        tmdb: int | str | None,
        tv_movie: bool = False,
    ) -> tuple[int | None, str | None]:
        client = _get_tvdb_or_warn(self.config)
        if client is None:
            return None, None
        for remote_id, source in self._external_queries(imdb, tmdb):
            result = await self._lookup_remote_id(
                client, remote_id, source, tv_movie
            )
            if result is not None:
                return result
        expected = "series or movie" if tv_movie else "series"
        logger.info(
            f"[yellow]No TVDB {expected} found for any available external ID[/yellow]"
        )
        return None, None

    @staticmethod
    def _is_imdb_remote_id(remote_id: dict[str, Any]) -> bool:
        return (
            remote_id.get("type") == 2 or remote_id.get("sourceName") == "IMDB"
        )

    @classmethod
    def _episode_imdb_id(cls, episode_data: dict[str, Any]) -> str | None:
        for remote_id in _as_dict_list(episode_data.get("remoteIds", [])):
            if cls._is_imdb_remote_id(remote_id):
                value = remote_id.get("id")
                return str(value) if value else None
        return None

    @staticmethod
    def _log_episode_imdb_id(
        episode_id: int | str, imdb_id: str | None
    ) -> None:
        if imdb_id:
            logger.debug(
                f"[blue]TVDB episode ID: {episode_id} maps to IMDB ID: {imdb_id}[/blue]"
            )
            return
        logger.debug(
            f"[yellow]No IMDB ID found for TVDB episode ID: {episode_id}[/yellow]"
        )

    async def get_imdb_id_from_tvdb_episode_id(
        self,
        episode_id: int | str,
    ) -> str | None:
        client = _get_tvdb_or_warn(self.config)
        if client is None:
            return None
        episode_id_int = _coerce_int(episode_id)
        if episode_id_int is None:
            logger.debug(
                f"[yellow]Invalid TVDB episode ID: {episode_id}[/yellow]"
            )
            return None
        try:
            episode_data = cast(
                dict[str, Any],
                cast(Any, client).get_episode_extended(episode_id_int),
            )
        except Exception as error:
            logger.error(
                f"[red]Error getting IMDB ID from TVDB episode ID: {error}[/red]"
            )
            return None
        logger.debug(
            f"[yellow]Episode data retrieved for episode ID {episode_id}[/yellow]"
        )
        imdb_id = self._episode_imdb_id(episode_data)
        self._log_episode_imdb_id(episode_id, imdb_id)
        return imdb_id

    @staticmethod
    def _episode_list(data: Any) -> tuple[list[dict[str, Any]], bool]:
        if isinstance(data, dict):
            payload = cast(dict[str, Any], data)
            return _as_dict_list(payload.get("episodes", [])), True
        if isinstance(data, list):
            return _as_dict_list(data), True
        return [], False

    @staticmethod
    def _episode_numbers(
        season: int | str | None, episode: int | str | None
    ) -> tuple[int, int | None] | None:
        try:
            season_int = int(season) if season is not None else None
            episode_int = int(episode) if episode not in (None, 0) else None
        except ValueError, TypeError:
            return None
        if season_int is None:
            return None
        return season_int, episode_int

    @staticmethod
    def _episode_values(episode: dict[str, Any]) -> tuple[Any, ...]:
        return (
            episode.get("seasonName"),
            episode.get("name"),
            episode.get("overview"),
            episode.get("seasonNumber"),
            episode.get("number"),
            episode.get("year"),
            episode.get("id"),
        )

    @staticmethod
    def _episode_by_air_date(
        episodes: list[dict[str, Any]], aired_date: str | None
    ) -> dict[str, Any] | None:
        if not aired_date:
            return None
        target = aired_date.strip().replace(".", "-")
        return next(
            (
                episode
                for episode in episodes
                if episode.get("aired") == target
            ),
            None,
        )

    @staticmethod
    def _first_episode_in_season(
        episodes: list[dict[str, Any]], season: int
    ) -> dict[str, Any] | None:
        return next(
            (
                episode
                for episode in episodes
                if episode.get("seasonNumber") == season
            ),
            None,
        )

    @staticmethod
    def _exact_episode(
        episodes: list[dict[str, Any]], season: int, episode_number: int | None
    ) -> dict[str, Any] | None:
        if episode_number is None:
            return None
        return next(
            (
                episode
                for episode in episodes
                if episode.get("seasonNumber") == season
                and episode.get("number") == episode_number
            ),
            None,
        )

    @staticmethod
    def _absolute_episode(
        episodes: list[dict[str, Any]], episode_number: int | None
    ) -> dict[str, Any] | None:
        return next(
            (
                episode
                for episode in episodes
                if episode.get("absoluteNumber") == episode_number
            ),
            None,
        )

    @classmethod
    def _specific_episode_match(
        cls,
        episodes: list[dict[str, Any]],
        season: int,
        episode_number: int | None,
        aired_date: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        daily = cls._episode_by_air_date(episodes, aired_date)
        if daily is not None:
            return daily, "daily"
        if episode_number is None:
            first = cls._first_episode_in_season(episodes, season)
            if first is not None:
                return first, "season"
        exact = cls._exact_episode(episodes, season, episode_number)
        if exact is not None:
            return exact, "exact"
        absolute = cls._absolute_episode(episodes, episode_number)
        return absolute, "absolute"

    @staticmethod
    def _log_specific_episode_match(
        matched: dict[str, Any],
        mode: str,
        season: int,
        episode_number: int | None,
    ) -> None:
        mapped_season = matched.get("seasonNumber")
        mapped_episode = matched.get("number")
        if mode == "daily":
            logger.debug(
                f"[green]Matched daily episode: S{mapped_season:02d}E{mapped_episode:02d} - {matched.get('name')}[/green]"
            )
            return
        if mode == "season":
            logger.debug(
                f"[green]Found first episode of season {season}: S{season:02d}E{mapped_episode:02d} - {matched.get('name')}[/green]"
            )
            return
        if mode == "exact":
            logger.debug(
                f"[green]Found exact match: S{season:02d}E{episode_number:02d} - {matched.get('name')}[/green]"
            )
            return
        logger.debug(
            f"[green]Mapped absolute #{episode_number} -> S{mapped_season:02d}E{mapped_episode:02d} - {matched.get('name')}[/green]"
        )

    async def get_specific_episode_data(
        self,
        data: Any,
        season: int | str | None,
        episode: int | str | None,
        aired_date: str | None = None,
    ) -> tuple[
        Any | None,
        Any | None,
        Any | None,
        Any | None,
        Any | None,
        Any | None,
        Any | None,
    ]:
        logger.debug(
            "[yellow]Getting specific episode data from TVDB data[/yellow]"
        )
        episodes, valid_format = self._episode_list(data)
        if not valid_format:
            logger.info(
                "[red]No episode data available or invalid format[/red]"
            )
            return None, None, None, None, None, None, None
        if not episodes:
            logger.info("[red]No episodes found in data[/red]")
            return None, None, None, None, None, None, None
        numbers = self._episode_numbers(season, episode)
        if numbers is None:
            logger.info(
                f"[red]Invalid season or episode format: season={season}, episode={episode}[/red]"
            )
            return None, None, None, None, None, None, None
        season_int, episode_int = numbers
        logger.debug(
            f"[blue]Total episodes retrieved from TVDB: {len(episodes)}[/blue]"
        )
        logger.debug(
            f"[blue]Looking for Season: {season_int}, Episode: {episode_int}[/blue]"
        )
        matched, mode = self._specific_episode_match(
            episodes, season_int, episode_int, aired_date
        )
        if matched is not None:
            self._log_specific_episode_match(
                matched, mode, season_int, episode_int
            )
            return self._episode_values(matched)
        logger.info(
            f"[red]Could not find episode for season {season_int}, episode {episode_int}, or absolute #{episode_int}[/red]"
        )
        return None, None, None, None, None, None, None
