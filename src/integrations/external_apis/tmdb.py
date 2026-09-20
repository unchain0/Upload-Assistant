# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import partial
from typing import Any, NoReturn
from typing import cast as typing_cast

import anitopy
import cli_ui
import guessit
import httpx

from src.domain_models.errors import (
    AmbiguousMetadataError,
    OperationAbortedError,
    TmdbCredentialMissingError,
)
from src.domain_models.external_api import TmdbCredential
from src.domain_models.media_identifiers import parse_tmdb_id
from src.domain_models.release import Meta
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.external_apis.imdb import imdb_manager
from src.integrations.external_apis.tmdb_authentication import (
    build_tmdb_authentication,
)
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)

default_config: dict[str, Any] = {}
_tmdb_credential: TmdbCredential | None = None
TMDB_BASE_URL = "https://api.themoviedb.org/3"


def _apply_config(config: dict[str, Any]) -> None:
    global _tmdb_credential, default_config
    default_cfg = typing_cast(dict[str, Any], config.get("DEFAULT", {}))
    default_config = default_cfg
    raw_credential = default_cfg.get("tmdb_access_token") or default_cfg.get(
        "tmdb_api"
    )
    _tmdb_credential = TmdbCredential.parse(raw_credential)


def _tmdb_client(**kwargs: Any) -> httpx.AsyncClient:
    """Build an authenticated TMDb client from the normalized credential."""
    if _tmdb_credential is None:
        raise TmdbCredentialMissingError(
            "TMDb API credential is missing. Configure DEFAULT.tmdb_api or DEFAULT.tmdb_access_token in the active runtime config."
        )
    authentication = build_tmdb_authentication(_tmdb_credential)
    supplied_headers = typing_cast(dict[str, str], kwargs.pop("headers", {}))
    supplied_params = typing_cast(dict[str, Any], kwargs.pop("params", {}))
    headers = {**authentication.headers, **supplied_headers}
    params: dict[str, Any] = dict(supplied_params)
    for key, value in authentication.query.items():
        params[str(key)] = str(value)
    return httpx.AsyncClient(headers=headers, params=params, **kwargs)


anitopy_parse_fn: Any = typing_cast(Any, anitopy).parse
guessit_module: Any = typing_cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return typing_cast(dict[str, Any], guessit_module.guessit(value, options))


class TmdbManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        _apply_config(config)

    async def normalize_title(self, title: str) -> str:
        return await normalize_title(title)

    async def get_tmdb_from_imdb(
        self,
        imdb_id: str | int | None,
        tvdb_id: int | None = None,
        search_year: str | int | None = None,
        filename: str | None = None,
        debug: bool = False,
        mode: str = "non_cli",
        category_preference: str | None = None,
        imdb_info: dict[str, Any] | None = None,
        unattended: bool = False,
    ) -> tuple[str, int | str, str, bool]:
        return await get_tmdb_from_imdb(
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            search_year=search_year,
            filename=filename,
            debug=debug,
            mode=mode,
            category_preference=category_preference,
            imdb_info=imdb_info,
            unattended=unattended,
        )

    async def get_tmdb_id(
        self,
        filename: str,
        search_year: str | int | None,
        category: str | dict[str, Any],
        untouched_filename: str | None = "",
        attempted: int = 0,
        debug: bool = False,
        secondary_title: str | None = None,
        path: str | None = None,
        final_attempt: bool | None = None,
        new_category: str | None = None,
        unattended: bool = False,
    ) -> tuple[int, str]:
        return await get_tmdb_id(
            filename=filename,
            search_year=search_year,
            category=category,
            untouched_filename=untouched_filename,
            attempted=attempted,
            debug=debug,
            secondary_title=secondary_title,
            path=path,
            final_attempt=final_attempt,
            new_category=new_category,
            unattended=unattended,
        )

    async def tmdb_other_meta(
        self,
        tmdb_id: int | None,
        path: str | None = None,
        search_year: str | int | None = None,
        category: str | None = None,
        imdb_id: int | str | None = 0,
        manual_language: str | None = None,
        anime: bool = False,
        mal_manual: int | None = None,
        aka: str = "",
        original_language: str | None = None,
        poster: str | None = None,
        debug: bool = False,
        mode: str = "non_cli",
        tvdb_id: int | None = 0,
        quickie_search: bool = False,
        filename: str | None = None,
        base_dir: str = "",
        unattended: bool = False,
    ) -> dict[str, Any]:
        return await tmdb_other_meta(
            tmdb_id=tmdb_id,
            path=path,
            search_year=search_year,
            category=category,
            imdb_id=imdb_id,
            manual_language=manual_language,
            anime=anime,
            mal_manual=mal_manual,
            aka=aka,
            original_language=original_language,
            poster=poster,
            debug=debug,
            mode=mode,
            tvdb_id=tvdb_id,
            quickie_search=quickie_search,
            filename=filename,
            base_dir=base_dir,
            config=self.config,
            unattended=unattended,
        )

    async def get_keywords(self, tmdb_id: int, category: str) -> list[str]:
        return await get_keywords(tmdb_id=tmdb_id, category=category)

    async def get_genres(
        self, response_data: dict[str, Any] | None
    ) -> dict[str, Any]:
        return await get_genres(response_data=response_data)

    async def get_directors(self, tmdb_id: int, category: str) -> list[str]:
        return await get_directors(tmdb_id=tmdb_id, category=category)

    async def get_anime(
        self,
        response: dict[str, Any],
        meta: Meta,
    ) -> tuple[int, str, bool, str]:
        return await get_anime(response=response, meta=meta)

    async def get_romaji(
        self,
        tmdb_name: str,
        mal: int | None,
        meta: Meta,
    ) -> tuple[str, int, str, str, int, str]:
        return await get_romaji(tmdb_name=tmdb_name, mal=mal, meta=meta)

    async def get_tmdb_imdb_from_mediainfo(
        self,
        mediainfo: dict[str, Any],
        meta: Meta,
    ) -> tuple[str, int, int | None, int | None]:
        category_value = meta.category or "MOVIE"
        is_disc = bool(meta.is_disc)
        tmdbid = meta.tmdb_id or 0
        imdbid = meta.imdb_id or 0
        tvdbid = meta.tvdb_id or 0
        return await get_tmdb_imdb_from_mediainfo(
            mediainfo=mediainfo,
            category=category_value,
            is_disc=is_disc,
            tmdbid=tmdbid,
            imdbid=imdbid,
            tvdbid=tvdbid,
        )

    def extract_imdb_id(self, value: str) -> int | None:
        return extract_imdb_id(value=value)

    async def daily_to_tmdb_season_episode(
        self,
        tmdbid: int,
        date: str | datetime,
    ) -> tuple[int, int]:
        return await daily_to_tmdb_season_episode(tmdbid=tmdbid, date=date)

    async def get_episode_details(
        self,
        tmdb_id: int,
        season_number: int,
        episode_number: int,
    ) -> dict[str, Any]:
        return await get_episode_details(
            tmdb_id=tmdb_id,
            season_number=season_number,
            episode_number=episode_number,
        )

    async def get_season_details(
        self,
        tmdb_id: int,
        season_number: int,
    ) -> dict[str, Any]:
        return await get_season_details(
            tmdb_id=tmdb_id, season_number=season_number
        )

    async def get_logo(
        self,
        tmdb_id: int,
        category: str,
        logo_languages: list[str] | str | None = None,
        tmdb_api_key: str | None = None,
        tmdb_base_url: str | None = None,
        logo_json: dict[str, Any] | None = None,
    ) -> str:
        return await get_logo(
            tmdb_id=tmdb_id,
            category=category,
            logo_languages=logo_languages,
            tmdb_api_key=tmdb_api_key,
            tmdb_base_url=tmdb_base_url,
            logo_json=logo_json,
        )

    async def get_tmdb_translations(
        self,
        tmdb_id: int,
        category: str,
        target_language: str = "en",
    ) -> str:
        return await get_tmdb_translations(
            tmdb_id=tmdb_id,
            category=category,
            target_language=target_language,
        )

    async def set_tmdb_metadata(
        self, meta: Meta, filename: str | None = None
    ) -> None:
        return await set_tmdb_metadata(meta=meta, filename=filename)

    async def get_tmdb_localized_data(
        self,
        meta: Meta,
        data_type: str,
        language: str,
        append_to_response: str,
    ) -> dict[str, Any]:
        return await get_tmdb_localized_data(
            meta=meta,
            data_type=data_type,
            language=language,
            append_to_response=append_to_response,
        )


async def normalize_title(title: str) -> str:
    return title.lower().replace("&", "and").replace("  ", " ").strip()


def _external_imdb_id(value: Any) -> int:
    text = str(value or "").removeprefix("tt")
    return int(text) if text.isdigit() else 0


def _reconcile_tmdb_imdb_id(
    original_id: int, external_id: Any, quickie_search: bool
) -> tuple[int, bool, int]:
    external = _external_imdb_id(external_id)
    if not external:
        resolved = 0 if quickie_search else original_id
        return resolved, False, 0
    mismatch = bool(original_id and external != original_id)
    if mismatch:
        return original_id, quickie_search, external
    return external, False, 0


def _tmdb_result_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        typing_cast(dict[str, Any], item)
        for item in typing_cast(list[Any], value)
        if isinstance(item, dict)
    ]


def _typed_external_results(
    category: str, results: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    return [(category, result) for result in results]


def _preferred_external_results(
    preference: str,
    movie_results: list[dict[str, Any]],
    tv_results: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]] | None:
    if preference == "MOVIE" and movie_results:
        return _typed_external_results("MOVIE", movie_results)
    if preference == "TV" and tv_results:
        return _typed_external_results("TV", tv_results)
    return None


def _external_find_candidates(
    info: dict[str, Any], category_preference: str | None
) -> list[tuple[str, dict[str, Any]]]:
    movie_results = _tmdb_result_dicts(info.get("movie_results"))
    tv_results = _tmdb_result_dicts(info.get("tv_results"))
    preference = str(category_preference or "").upper()
    preferred = _preferred_external_results(
        preference, movie_results, tv_results
    )
    if preferred is not None:
        return preferred
    return [
        *_typed_external_results("MOVIE", movie_results),
        *_typed_external_results("TV", tv_results),
    ]


def _reject_ambiguous_external_find(
    info: dict[str, Any], category_preference: str | None, unattended: bool
) -> None:
    if not unattended:
        return
    candidates = _external_find_candidates(info, category_preference)
    if len(candidates) <= 1:
        return
    candidate_ids = ", ".join(
        f"{category.lower()}/{result.get('id', '')}"
        for category, result in candidates[:3]
    )
    logger.warning(
        "[yellow]Ambiguous TMDb lookup from IMDb ID in unattended mode; "
        f"refusing to guess between candidates {candidate_ids}.[/yellow]"
    )
    raise AmbiguousMetadataError(
        "IMDb/TMDb identity is ambiguous; automatic mode will skip this release."
    )


def _reject_unattended_imdb_mismatch(
    unattended: bool, original_imdb_id: Any, mismatched_imdb_id: int
) -> None:
    if not unattended or not original_imdb_id or not mismatched_imdb_id:
        return
    logger.warning(
        "[yellow]TMDb returned an IMDb ID that conflicts with the release identity; "
        "automatic mode will not choose between them.[/yellow]"
    )
    raise AmbiguousMetadataError(
        "IMDb/TMDb identity conflict is ambiguous; automatic mode will skip this release."
    )


def _formatted_imdb_external_id(text: str) -> str:
    if text.startswith("tt"):
        return text
    return f"tt{int(text):07d}" if text.isdigit() else text


def _normalized_imdb_external_id(imdb_id: str | int | None) -> str | None:
    if imdb_id is None or imdb_id == 0:
        return None
    text = str(imdb_id).strip()
    return _formatted_imdb_external_id(text) if text else None


async def _tmdb_find_by_external_source(
    external_id: str | int, source: str
) -> dict[str, Any]:
    url = f"{TMDB_BASE_URL}/find/{external_id}"
    async with _tmdb_client() as client:
        response: httpx.Response | None = None
        try:
            response = await client.get(
                url, params={"external_source": source}, timeout=10
            )
            response.raise_for_status()
            return typing_cast(dict[str, Any], response.json())
        except Exception:
            status = (
                response.status_code if response is not None else "unknown"
            )
            logger.info(
                f"[bold red]Failed to fetch TMDb data: {status}[/bold red]"
            )
            return {}


def _first_external_match(
    info: dict[str, Any], category: str
) -> tuple[str, int | str, str] | None:
    key = "movie_results" if category == "MOVIE" else "tv_results"
    results = _tmdb_result_dicts(info.get(key))
    if not results:
        return None
    first = results[0]
    return (
        category,
        first.get("id", 0),
        str(first.get("original_language") or ""),
    )


def _external_preference_available(info: dict[str, Any]) -> bool:
    return bool(
        _tmdb_result_dicts(info.get("movie_results"))
        and _tmdb_result_dicts(info.get("tv_results"))
    )


def _preferred_external_match(
    info: dict[str, Any], category_preference: str | None
) -> tuple[str, int | str, str] | None:
    if not category_preference or not _external_preference_available(info):
        return None
    preference = str(category_preference).upper()
    if preference not in {"MOVIE", "TV"}:
        return None
    logger.debug(
        f"[green]Found both movie and TV results, using {preference.lower()} based on preference"
    )
    return _first_external_match(info, preference)


def _default_external_match(
    info: dict[str, Any],
) -> tuple[str, int | str, str] | None:
    movie = _first_external_match(info, "MOVIE")
    if movie is not None:
        logger.debug(f"Movie INFO: {info}")
        return movie
    tv = _first_external_match(info, "TV")
    if tv is not None:
        logger.debug(f"TV INFO: {info}")
    return tv


async def _tvdb_external_match(
    tvdb_id: int | None,
) -> tuple[str, int | str, str] | None:
    if not tvdb_id:
        return None
    info = await _tmdb_find_by_external_source(str(tvdb_id), "tvdb_id")
    logger.debug(f"TVDB INFO: {info}")
    return _first_external_match(info, "TV")


async def _imdb_fallback_info(
    imdb_external_id: str,
    imdb_info: dict[str, Any] | None,
) -> dict[str, Any]:
    if imdb_info is not None:
        return imdb_info
    clean = imdb_external_id.replace("tt", "")
    numeric = int(clean) if clean.isdigit() else 0
    return await imdb_manager.get_imdb_info_api(numeric, {})


async def _fallback_tmdb_title_search(
    title: str,
    year: Any,
    secondary_title: str,
    debug: bool,
    unattended: bool,
) -> tuple[int, str]:
    tmdb_id, category = await get_tmdb_id(
        title,
        year,
        "MOVIE",
        secondary_title=secondary_title,
        debug=debug,
        unattended=unattended,
    )
    if tmdb_id:
        return tmdb_id, category
    return await get_tmdb_id(
        title,
        year,
        "TV",
        secondary_title=secondary_title,
        debug=debug,
        unattended=unattended,
    )


async def _manual_tmdb_id_if_needed(
    tmdb_id: int | str,
    category: str,
    mode: str,
    unattended: bool,
) -> tuple[str, int | str]:
    missing = tmdb_id in ("None", "", None, 0, "0")
    if not missing or mode != "cli" or unattended:
        return category, tmdb_id
    logger.info("[yellow]Unable to find a matching TMDb entry[/yellow]")
    raw = await prompt_in_thread(
        cli_ui.ask_string,
        "Please enter TMDb ID (format: tv/12345 or movie/12345): ",
        default="",
    )
    return parse_tmdb_id(raw or "", category)


async def _external_tmdb_match(
    imdb_external_id: str,
    tvdb_id: int | None,
    category_preference: str | None,
    unattended: bool,
) -> tuple[str, int | str, str] | None:
    info = await _tmdb_find_by_external_source(imdb_external_id, "imdb_id")
    _reject_ambiguous_external_find(info, category_preference, unattended)
    direct = _preferred_external_match(
        info, category_preference
    ) or _default_external_match(info)
    if direct is not None:
        return direct
    logger.debug(
        "[yellow]TMDb was unable to find anything with that IMDb ID, checking TVDb..."
    )
    return await _tvdb_external_match(tvdb_id)


def _first_nonempty(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _imdb_fallback_fields(
    info: dict[str, Any], filename: str | None, search_year: str | int | None
) -> tuple[str, Any, str, str]:
    title = str(_first_nonempty(info.get("title"), filename))
    year = _first_nonempty(info.get("year"), search_year, default=None)
    language = str(
        _first_nonempty(
            info.get("original_language"),
            info.get("original language"),
            default="en",
        )
    )
    secondary = str(
        _first_nonempty(
            info.get("original title"), info.get("localized title"), default=""
        )
    )
    return title, year, language, secondary


async def _title_fallback_from_imdb(
    imdb_external_id: str,
    imdb_info: dict[str, Any] | None,
    filename: str | None,
    search_year: str | int | None,
    debug: bool,
    mode: str,
    unattended: bool,
) -> tuple[str, int | str, str, bool]:
    info = await _imdb_fallback_info(imdb_external_id, imdb_info)
    title, year, language, secondary = _imdb_fallback_fields(
        info, filename, search_year
    )
    logger.info(
        f"[yellow]TMDb was unable to find anything from external IDs, searching TMDb for {title} ({year})[/yellow]"
    )
    tmdb_id, category = await _fallback_tmdb_title_search(
        title, year, secondary, debug, unattended
    )
    resolved_category = category if category else "MOVIE"
    resolved_id: int | str = tmdb_id if tmdb_id else 0
    resolved_category, resolved_id = await _manual_tmdb_id_if_needed(
        resolved_id, resolved_category, mode, unattended
    )
    return resolved_category, resolved_id, language, True


async def get_tmdb_from_imdb(
    imdb_id: str | int | None,
    tvdb_id: int | None = None,
    search_year: str | int | None = None,
    filename: str | None = None,
    debug: bool = False,
    mode: str = "non_cli",
    category_preference: str | None = None,
    imdb_info: dict[str, Any] | None = None,
    unattended: bool = False,
) -> tuple[str, int | str, str, bool]:
    """Fetch TMDb identity from IMDb/TVDb, then title fallback."""
    imdb_external_id = _normalized_imdb_external_id(imdb_id)
    if imdb_external_id is None:
        return "", 0, "", False
    match = await _external_tmdb_match(
        imdb_external_id, tvdb_id, category_preference, unattended
    )
    if match is not None:
        category, tmdb_id, language = match
        return category, tmdb_id, language, False
    return await _title_fallback_from_imdb(
        imdb_external_id,
        imdb_info,
        filename,
        search_year,
        debug,
        mode,
        unattended,
    )


def _tmdb_category_value(category: Any) -> str:
    if isinstance(category, dict):
        values = typing_cast(dict[str, Any], category)
        return str(values.get("category", "MOVIE"))
    if isinstance(category, str):
        return category
    getter = getattr(category, "get", None)
    if not callable(getter):
        return "MOVIE"
    get_value = typing_cast(Callable[[str, str], Any], getter)
    try:
        return str(get_value("category", "MOVIE"))
    except Exception:
        return "MOVIE"


def _normalized_search_category(
    category: Any, category_preference: str | None, new_category: str | None
) -> str:
    value = str(
        category_preference
        or new_category
        or _tmdb_category_value(category)
        or "MOVIE"
    ).upper()
    return value if value in {"MOVIE", "TV"} else "MOVIE"


def _tmdb_search_params(
    filename: str, search_year: str | int | None, category: str
) -> dict[str, str]:
    params = {
        "query": filename,
        "language": "en-US",
        "include_adult": "true",
    }
    if search_year:
        key = "year" if category == "MOVIE" else "first_air_date_year"
        params[key] = str(search_year)
    return params


async def _tmdb_search_results(
    filename: str, search_year: str | int | None, category: str
) -> list[dict[str, Any]]:
    endpoint = "movie" if category == "MOVIE" else "tv"
    label = "movie" if category == "MOVIE" else "TV show"
    logger.debug(
        f"[green]Searching TMDb for {label}:[/] [cyan]{filename}[/cyan] (Year: {search_year})"
    )
    async with _tmdb_client() as client:
        response = await client.get(
            f"{TMDB_BASE_URL}/search/{endpoint}",
            params=_tmdb_search_params(filename, search_year, category),
        )
        try:
            response.raise_for_status()
            data = typing_cast(dict[str, Any], response.json())
        except Exception:
            logger.info(
                f"[bold red]Failure with primary {label} search: {response.status_code}[/bold red]"
            )
            return []
    results = _dict_list(data.get("results", []))
    logger.debug(
        f"[yellow]TMDB search results (primary): {json.dumps(results[:4], indent=2)}[/yellow]"
    )
    return results


def _tmdb_result_year(result: dict[str, Any]) -> int:
    value = result.get("release_date") or result.get("first_air_date") or "0"
    with contextlib.suppress(Exception):
        return int(str(value)[:4] or 0)
    return 0


def _parsed_search_year(search_year: str | int | None) -> int:
    if search_year in (None, ""):
        return 0
    try:
        return int(search_year)
    except TypeError, ValueError:
        return 0


def _year_near_result(result: dict[str, Any], year: int) -> bool:
    return abs(_tmdb_result_year(result) - year) <= 2


def _limited_tmdb_results(
    results: list[dict[str, Any]], search_year: str | int | None
) -> list[dict[str, Any]]:
    year = _parsed_search_year(search_year)
    if year <= 0:
        return results[:8]
    filtered = [
        result for result in results if _year_near_result(result, year)
    ]
    selected = filtered if filtered else results
    return selected[:8]


async def _normalized_result_titles(result: dict[str, Any]) -> tuple[str, str]:
    primary = result.get("title") or result.get("name") or ""
    original = (
        result.get("original_title") or result.get("original_name") or ""
    )
    return await normalize_title(str(primary)), await normalize_title(
        str(original)
    )


def _year_exact_match(search_year: int, result_year: int) -> bool:
    if search_year <= 0 or result_year <= 0:
        return False
    return result_year in {search_year, search_year + 1}


async def _optional_normalized_title(value: str | None) -> str | None:
    return await normalize_title(value) if value else None


def _exact_title_matches(
    filename_norm: str,
    secondary_norm: str | None,
    title: str,
    original: str,
) -> bool:
    if filename_norm == title:
        return True
    if secondary_norm is None:
        return False
    return secondary_norm in {title, original}


async def _exact_tmdb_result_id(
    result: dict[str, Any],
    filename_norm: str,
    secondary_norm: str | None,
    year: int,
) -> int | None:
    if not _year_exact_match(year, _tmdb_result_year(result)):
        return None
    title, original = await _normalized_result_titles(result)
    if not _exact_title_matches(
        filename_norm, secondary_norm, title, original
    ):
        return None
    raw_id = result.get("id")
    return int(raw_id) if raw_id is not None else None


async def _exact_tmdb_match_ids(
    results: list[dict[str, Any]],
    filename: str,
    secondary_title: str | None,
    search_year: str | int | None,
) -> set[int]:
    filename_norm = await normalize_title(filename)
    secondary_norm = await _optional_normalized_title(secondary_title)
    year = _parsed_search_year(search_year)
    matches: set[int] = set()
    for result in results:
        match_id = await _exact_tmdb_result_id(
            result, filename_norm, secondary_norm, year
        )
        if match_id is not None:
            matches.add(match_id)
    return matches


async def _translated_similarity(
    result: dict[str, Any],
    category: str,
    filename_norm: str,
    title: str,
    original: str,
) -> tuple[str, float]:
    if not original or original == title:
        return "", 0.0
    translated = await get_tmdb_translations(int(result["id"]), category, "en")
    if not translated:
        return "", 0.0
    normalized = await normalize_title(translated)
    similarity = SequenceMatcher(None, filename_norm, normalized).ratio()
    logger.debug(
        f"[cyan]  TMDb translation: '{translated}' (similarity: {similarity:.3f})[/cyan]"
    )
    return translated, similarity


async def _secondary_similarity(
    secondary_norm: str | None,
    title: str,
    original: str,
    translated: str,
) -> float:
    if secondary_norm is None:
        return 0.0
    values = [
        SequenceMatcher(None, secondary_norm, title).ratio(),
        SequenceMatcher(None, secondary_norm, original).ratio(),
    ]
    if translated:
        values.append(
            SequenceMatcher(
                None, secondary_norm, await normalize_title(translated)
            ).ratio()
        )
    return max(values)


def _combined_tmdb_similarity(
    main: float, original: float, translated: float, secondary: float
) -> float:
    if translated == 0.0:
        if secondary == 0.0:
            return max(main, original)
        return main * 0.3 + original * 0.3 + secondary * 0.4
    return main * 0.5 + (translated if secondary == 0.0 else secondary) * 0.5


def _year_boost(
    similarity: float, search_year: int, result_year: int
) -> float:
    if similarity < 0.9 or not _year_exact_match(search_year, result_year):
        return similarity
    return similarity + 0.1


async def _score_tmdb_result(
    result: dict[str, Any],
    filename_norm: str,
    secondary_norm: str | None,
    search_year: int,
    category: str,
) -> float:
    title, original = await _normalized_result_titles(result)
    main = SequenceMatcher(None, filename_norm, title).ratio()
    original_similarity = SequenceMatcher(
        None, filename_norm, original
    ).ratio()
    translated, translated_similarity = await _translated_similarity(
        result, category, filename_norm, title, original
    )
    secondary = await _secondary_similarity(
        secondary_norm, title, original, translated
    )
    similarity = _combined_tmdb_similarity(
        main, original_similarity, translated_similarity, secondary
    )
    logger.debug(
        f"[cyan]ID {result.get('id')}: '{title}' vs '{filename_norm}'[/cyan]"
    )
    logger.debug(f"[cyan]  Main similarity: {main:.3f}[/cyan]")
    logger.debug(
        f"[cyan]  Original similarity: {original_similarity:.3f}[/cyan]"
    )
    if translated_similarity > 0:
        logger.debug(
            f"[cyan]  Translated similarity: {translated_similarity:.3f}[/cyan]"
        )
    if secondary > 0:
        logger.debug(f"[cyan]  Secondary similarity: {secondary:.3f}[/cyan]")
    logger.debug(f"[cyan]  Final similarity: {similarity:.3f}[/cyan]")
    return _year_boost(similarity, search_year, _tmdb_result_year(result))


async def _scored_tmdb_results(
    results: list[dict[str, Any]],
    filename_norm: str,
    secondary_norm: str | None,
    year: int,
    category: str,
) -> list[tuple[dict[str, Any], float]]:
    return [
        (
            result,
            await _score_tmdb_result(
                result, filename_norm, secondary_norm, year, category
            ),
        )
        for result in results
    ]


def _boost_first_tv_result(
    ranked: list[tuple[dict[str, Any], float]], category: str
) -> None:
    if category != "TV" or not ranked:
        return
    result, score = ranked[0]
    boosted = score + 0.05
    ranked[0] = (result, boosted)
    logger.debug(
        f"[cyan]Boosted first TV result similarity from {score:.3f} to {boosted:.3f}[/cyan]"
    )


def _filter_low_similarity_ranked(
    ranked: list[tuple[dict[str, Any], float]],
) -> list[tuple[dict[str, Any], float]]:
    if not ranked or ranked[0][1] < 0.90:
        return ranked
    filtered = [item for item in ranked if item[1] >= 0.75]
    logger.debug(
        f"[yellow]Filtered out low similarity results (< 0.70) since best match has {filtered[0][1]:.2f} similarity[/yellow]"
    )
    return filtered


async def _rank_tmdb_results(
    results: list[dict[str, Any]],
    filename: str,
    secondary_title: str | None,
    search_year: str | int | None,
    category: str,
) -> list[tuple[dict[str, Any], float]]:
    filename_norm = await normalize_title(filename)
    secondary_norm = await _optional_normalized_title(secondary_title)
    ranked = await _scored_tmdb_results(
        results,
        filename_norm,
        secondary_norm,
        _parsed_search_year(search_year),
        category,
    )
    _boost_first_tv_result(ranked, category)
    ranked.sort(key=lambda item: item[1], reverse=True)
    return _filter_low_similarity_ranked(ranked)


def _confident_ranked_id(
    ranked: list[tuple[dict[str, Any], float]],
) -> int | None:
    if not ranked:
        return None
    best = ranked[0][1]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best >= 0.75 and best - second >= 0.10:
        return int(ranked[0][0]["id"])
    return None


async def _ranked_titles(
    ranked: list[tuple[dict[str, Any], float]],
) -> list[tuple[dict[str, Any], float, str]]:
    titled: list[tuple[dict[str, Any], float, str]] = []
    for result, score in ranked:
        title, _original = await _normalized_result_titles(result)
        titled.append((result, score, title))
    return titled


def _single_the_candidate(
    titled: list[tuple[dict[str, Any], float, str]],
) -> tuple[dict[str, Any], float, str] | None:
    the_items = [item for item in titled if item[2].startswith("the ")]
    if len(the_items) != 1:
        return None
    if len(titled) == 1:
        return None
    return the_items[0]


def _updated_ranked_score(
    ranked: list[tuple[dict[str, Any], float]],
    target_id: Any,
    new_score: float,
) -> list[tuple[dict[str, Any], float]]:
    updated = [
        (
            candidate,
            new_score if candidate.get("id") == target_id else similarity,
        )
        for candidate, similarity in ranked
    ]
    updated.sort(key=lambda item: item[1], reverse=True)
    return updated


async def _the_prefix_rank_adjustment(
    ranked: list[tuple[dict[str, Any], float]], filename: str
) -> list[tuple[dict[str, Any], float]]:
    if len(ranked) <= 1:
        return ranked
    candidate = _single_the_candidate(await _ranked_titles(ranked))
    if candidate is None:
        return ranked
    result, score, title = candidate
    without_the = title[4:]
    filename_norm = await normalize_title(filename)
    new_score = SequenceMatcher(None, filename_norm, without_the).ratio()
    logger.debug(
        f"[cyan]Checking 'The' prefix: '{title}' -> '{without_the}'[/cyan]"
    )
    logger.debug(
        f"[cyan]Original similarity: {score:.3f}, New similarity: {new_score:.3f}[/cyan]"
    )
    if new_score <= score + 0.05:
        return ranked
    return _updated_ranked_score(ranked, result.get("id"), new_score)


def _reject_ambiguous_ranked(
    ranked: list[tuple[dict[str, Any], float]],
) -> None:
    candidate_ids = [
        str(result.get("id", "")) for result, _score in ranked[:3]
    ]
    logger.warning(
        "[yellow]Ambiguous TMDb match in unattended mode; "
        f"refusing to guess between candidates {', '.join(candidate_ids)}.[/yellow]"
    )
    raise AmbiguousMetadataError(
        "TMDb metadata match is ambiguous; automatic mode will skip this release."
    )


def _tmdb_result_title(result: dict[str, Any]) -> str:
    return str(result.get("title") or result.get("name") or "")


def _tmdb_web_base_url(category: str) -> str:
    return (
        "https://www.themoviedb.org/movie/"
        if category == "MOVIE"
        else "https://www.themoviedb.org/tv/"
    )


def _ranked_tmdb_year(result: dict[str, Any]) -> str:
    value = _first_nonempty(
        result.get("release_date"), result.get("first_air_date"), default=""
    )
    return str(value)[:4]


def _log_ranked_tmdb_entry(
    index: int,
    result: dict[str, Any],
    score: float,
    base_url: str,
) -> None:
    logger.info(
        f"[cyan]{index}.[/cyan] [bold]{_tmdb_result_title(result)}[/bold] "
        f"({_ranked_tmdb_year(result)}) [yellow]ID:[/yellow] {base_url}{result['id']} "
        f"[dim](similarity: {score:.2f})[/dim]"
    )
    overview = str(result.get("overview", ""))
    if overview:
        suffix = "..." if len(overview) > 200 else ""
        logger.info(f"[green]Overview:[/green] {overview[:200]}{suffix}")
    logger.info("")


def _log_ranked_tmdb_results(
    ranked: list[tuple[dict[str, Any], float]], category: str
) -> None:
    logger.info("")
    logger.info(
        "[bold yellow]Multiple TMDb results found. Please select the correct entry:[/bold yellow]"
    )
    base_url = _tmdb_web_base_url(category)
    for index, (result, score) in enumerate(ranked, start=1):
        _log_ranked_tmdb_entry(index, result, score, base_url)


async def _cancel_tmdb_selection() -> NoReturn:
    logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
    await cleanup_manager.cleanup()
    cleanup_manager.reset_terminal()
    raise OperationAbortedError("TMDb selection was cancelled by the user.")


async def _read_ranked_tmdb_selection() -> Any:
    try:
        value = await prompt_in_thread(
            cli_ui.ask_string,
            "Or push enter to try a different search: ",
        )
    except EOFError, KeyboardInterrupt:
        await _cancel_tmdb_selection()
    return value if value is not None else ""


def _is_manual_tmdb_selection(selection: Any) -> bool:
    if not isinstance(selection, str):
        return False
    return "/" in selection and selection.lower().startswith(("tv/", "movie/"))


async def _parsed_manual_tmdb_selection(
    selection: str, category: str
) -> tuple[int, str] | None:
    try:
        parsed_category, parsed_id = parse_tmdb_id(selection, category)
    except KeyboardInterrupt:
        await _cancel_tmdb_selection()
    except Exception as error:
        logger.info(
            f"[bold red]Error parsing TMDb ID: {error}. Please try again.[/bold red]"
        )
        return None
    if not parsed_id:
        logger.info(
            "[bold red]Invalid TMDb ID format. Please try again.[/bold red]"
        )
        return None
    logger.info(
        f"[green]Using manual TMDb ID: {parsed_id} and category: {parsed_category}[/green]"
    )
    return int(parsed_id), str(parsed_category)


async def _ranked_numeric_selection(
    selection: Any,
    ranked: list[tuple[dict[str, Any], float]],
    category: str,
) -> tuple[int, str] | None:
    try:
        selection_int = int(selection)
    except KeyboardInterrupt:
        await _cancel_tmdb_selection()
    except TypeError, ValueError:
        logger.info(
            "[bold red]Invalid input. Please enter a number or TMDb ID (tv/12345 or movie/12345).[/bold red]"
        )
        return None
    if not 1 <= selection_int <= len(ranked):
        logger.info(
            "[bold red]Selection out of range. Please try again.[/bold red]"
        )
        return None
    return int(ranked[selection_int - 1][0]["id"]), category


async def _prompt_ranked_tmdb_selection(
    ranked: list[tuple[dict[str, Any], float]], category: str
) -> tuple[int, str] | None:
    _log_ranked_tmdb_results(ranked, category)
    while True:
        logger.info(
            "Enter the number of the correct entry, or manual TMDb ID (tv/12345 or movie/12345):"
        )
        selection = await _read_ranked_tmdb_selection()
        if not selection.strip():
            return None
        if _is_manual_tmdb_selection(selection):
            selected = await _parsed_manual_tmdb_selection(selection, category)
        else:
            selected = await _ranked_numeric_selection(
                selection, ranked, category
            )
        if selected is not None:
            return selected


async def _direct_tmdb_search_match(
    limited: list[dict[str, Any]],
    filename: str,
    secondary_title: str | None,
    search_year: str | int | None,
    category: str,
) -> tuple[int, str] | None:
    if len(limited) == 1:
        return int(limited[0]["id"]), category
    exact_ids = await _exact_tmdb_match_ids(
        limited, filename, secondary_title, search_year
    )
    if len(exact_ids) == 1:
        return exact_ids.pop(), category
    return None


def _log_confident_ranked(
    ranked: list[tuple[dict[str, Any], float]], label: str
) -> None:
    logger.debug(
        f"[green]Auto-selecting {label}: {_tmdb_result_title(ranked[0][0])} "
        f"(similarity: {ranked[0][1]:.2f})[/green]"
    )


async def _ranked_tmdb_search_match(
    limited: list[dict[str, Any]],
    filename: str,
    secondary_title: str | None,
    search_year: str | int | None,
    category: str,
    unattended: bool,
) -> tuple[int, str]:
    ranked = await _rank_tmdb_results(
        limited, filename, secondary_title, search_year, category
    )
    confident = _confident_ranked_id(ranked)
    if confident is not None:
        _log_confident_ranked(ranked, "best match")
        return confident, category
    adjusted = await _the_prefix_rank_adjustment(ranked, filename)
    confident = _confident_ranked_id(adjusted)
    if confident is not None:
        _log_confident_ranked(adjusted, "'The' prefixed match")
        return confident, category
    if unattended:
        _reject_ambiguous_ranked(adjusted)
    selected = await _prompt_ranked_tmdb_selection(adjusted, category)
    return selected if selected is not None else (0, category)


async def _search_tmdb_id_once(
    filename: str,
    search_year: str | int | None,
    category: str,
    secondary_title: str | None,
    unattended: bool,
) -> tuple[int, str]:
    try:
        limited = _limited_tmdb_results(
            await _tmdb_search_results(filename, search_year, category),
            search_year,
        )
        if not limited:
            return 0, category
        direct = await _direct_tmdb_search_match(
            limited, filename, secondary_title, search_year, category
        )
        if direct is not None:
            return direct
        return await _ranked_tmdb_search_match(
            limited,
            filename,
            secondary_title,
            search_year,
            category,
            unattended,
        )
    except OperationAbortedError:
        raise
    except Exception:
        return 0, category


_ROMAN_NUMERALS = {
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
    "IX": "9",
    "X": "10",
}


def _roman_converted_title(filename: str) -> str | None:
    words = filename.split()
    converted = False
    for index, word in enumerate(words):
        replacement = _ROMAN_NUMERALS.get(word.upper())
        if replacement is not None:
            words[index] = replacement
            converted = True
    return " ".join(words) if converted else None


def _safe_year_plus_one(search_year: str | int | None) -> int | None:
    try:
        year = int(search_year) if search_year is not None else 0
    except Exception:
        return None
    return year + 1 if year > 0 else None


def _anime_search_title(untouched_filename: str | None) -> str:
    try:
        guessed = guessit_fn(
            untouched_filename or "", {"excludes": ["country", "language"]}
        )
        parsed = typing_cast(
            dict[str, Any], anitopy_parse_fn(guessed.get("title", "")) or {}
        )
        return str(parsed.get("anime_title", ""))
    except KeyError:
        logger.info(
            "[bold red]Failed to parse title for TMDb search.[/bold red]"
        )
        return ""


def _without_media_extension_word(filename: str) -> list[str]:
    words = filename.split()
    extensions = {"mp4", "mkv", "avi", "webm", "mov", "wmv"}
    return [word for word in words if word.lower() not in extensions]


def _reduced_search_title(filename: str, drop_count: int) -> str:
    words = _without_media_extension_word(filename)
    if len(words) <= drop_count:
        return ""
    return " ".join(words[:-drop_count])


async def _search_candidate(
    title: str,
    search_year: str | int | None,
    category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int = 0,
) -> tuple[int, str]:
    if not title:
        return 0, category
    if attempted:
        await asyncio.sleep(1)
    return await _search_tmdb_id_once(
        title, search_year, category, secondary_title, unattended
    )


async def _roman_search_fallback(
    filename: str,
    search_year: str | int | None,
    original_category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
) -> tuple[int, str]:
    try:
        roman = _roman_converted_title(filename)
        if not roman:
            return 0, original_category
        logger.debug(
            f"[bold yellow]Trying with roman numerals converted: {roman}[/bold yellow]"
        )
        return await _search_candidate(
            roman,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted + 1,
        )
    except OperationAbortedError:
        raise
    except Exception as error:
        logger.info(
            f"[bold red]Roman numeral conversion error:[/bold red] {error}"
        )
        return 0, original_category


async def _secondary_search_fallback(
    secondary_title: str | None,
    search_year: str | int | None,
    category: str,
    unattended: bool,
) -> tuple[int, str]:
    if not secondary_title:
        return 0, category
    logger.debug(f"[yellow]Trying secondary title: {secondary_title}[/yellow]")
    return await _search_candidate(
        secondary_title, search_year, category, secondary_title, unattended
    )


async def _primary_search_fallback(
    filename: str,
    search_year: str | int | None,
    category: str,
    secondary_title: str | None,
    unattended: bool,
) -> tuple[int, str]:
    logger.debug(f"[yellow]Trying primary filename: {filename}[/yellow]")
    return await _search_candidate(
        filename, search_year, category, secondary_title, unattended
    )


async def _next_year_search_fallback(
    filename: str,
    search_year: str | int | None,
    category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
) -> tuple[int, str]:
    next_year = _safe_year_plus_one(search_year)
    if next_year is None:
        return 0, category
    logger.debug("[yellow]Retrying with year +1...[/yellow]")
    return await _search_candidate(
        filename,
        next_year,
        category,
        secondary_title,
        unattended,
        attempted + 1,
    )


async def _switched_category_search_fallback(
    filename: str,
    search_year: str | int | None,
    category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
) -> tuple[int, str]:
    switched = "TV" if category == "MOVIE" else "MOVIE"
    logger.debug(
        f"[bold yellow]Switching category to {switched} and retrying...[/bold yellow]"
    )
    return await _search_candidate(
        filename,
        search_year,
        switched,
        secondary_title,
        unattended,
        attempted + 1,
    )


async def _anime_search_fallback(
    untouched_filename: str | None,
    search_year: str | int | None,
    original_category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
) -> tuple[int, str]:
    anime_title = _anime_search_title(untouched_filename)
    if not anime_title:
        return 0, original_category
    logger.debug(
        f"[bold yellow]Trying parsed anime title: {anime_title}[/bold yellow]"
    )
    return await _search_candidate(
        anime_title,
        search_year,
        original_category,
        secondary_title,
        unattended,
        attempted + 1,
    )


async def _reduced_search_fallback(
    filename: str,
    search_year: str | int | None,
    original_category: str,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
    drop_count: int,
    label: str,
) -> tuple[int, str]:
    try:
        reduced = _reduced_search_title(filename, drop_count)
        if not reduced:
            return 0, original_category
        logger.debug(
            f"[bold yellow]Trying {label} name: {reduced}[/bold yellow]"
        )
        return await _search_candidate(
            reduced,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted + 1,
        )
    except OperationAbortedError:
        raise
    except Exception as error:
        logger.info(f"[bold red]Reduced name search error:[/bold red] {error}")
        return 0, original_category


async def _first_successful_fallback(
    steps: list[Callable[[], Awaitable[tuple[int, str]]]], category: str
) -> tuple[int, str]:
    for step in steps:
        result = await step()
        if result[0]:
            return result
    return 0, category


async def _fallback_tmdb_searches(
    filename: str,
    search_year: str | int | None,
    category: str,
    original_category: str,
    untouched_filename: str | None,
    secondary_title: str | None,
    unattended: bool,
    attempted: int,
) -> tuple[int, str]:
    steps: list[Callable[[], Awaitable[tuple[int, str]]]] = [
        partial(
            _roman_search_fallback,
            filename,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted,
        ),
        partial(
            _secondary_search_fallback,
            secondary_title,
            search_year,
            category,
            unattended,
        ),
        partial(
            _primary_search_fallback,
            filename,
            search_year,
            category,
            secondary_title,
            unattended,
        ),
        partial(
            _next_year_search_fallback,
            filename,
            search_year,
            category,
            secondary_title,
            unattended,
            attempted,
        ),
        partial(
            _switched_category_search_fallback,
            filename,
            search_year,
            category,
            secondary_title,
            unattended,
            attempted,
        ),
        partial(
            _anime_search_fallback,
            untouched_filename,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted,
        ),
        partial(
            _reduced_search_fallback,
            filename,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted,
            1,
            "reduced",
        ),
        partial(
            _reduced_search_fallback,
            filename,
            search_year,
            original_category,
            secondary_title,
            unattended,
            attempted,
            2,
            "further reduced",
        ),
    ]
    return await _first_successful_fallback(steps, category)


async def _cancel_final_tmdb_lookup() -> NoReturn:
    logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
    await cleanup_manager.cleanup()
    cleanup_manager.reset_terminal()
    raise OperationAbortedError(
        "TMDb lookup was cancelled because no metadata selection was accepted."
    )


async def _read_final_tmdb_input() -> str:
    try:
        value = await prompt_in_thread(
            cli_ui.ask_string,
            "Please enter TMDb ID in this format: tv/12345 or movie/12345",
        )
    except EOFError, KeyboardInterrupt:
        await _cancel_final_tmdb_lookup()
    return str(value or "")


async def _parse_final_tmdb_input(
    raw: str, category: str
) -> tuple[int, str] | None:
    try:
        parsed_category, parsed_id = parse_tmdb_id(raw, category)
        tmdb_id = int(parsed_id)
    except KeyboardInterrupt:
        await _cancel_final_tmdb_lookup()
    except TypeError, ValueError:
        logger.info(
            "[bold red]Invalid TMDb ID. Use tv/12345 or movie/12345.[/bold red]"
        )
        return None
    if tmdb_id <= 0:
        logger.info(
            "[bold red]Invalid TMDb ID. Use tv/12345 or movie/12345.[/bold red]"
        )
        return None
    resolved_category = str(parsed_category) if parsed_category else category
    return tmdb_id, resolved_category.upper()


async def _final_manual_tmdb_prompt(category: str) -> tuple[int, str]:
    logger.info(
        "[bold red]Unable to find TMDb match using any search[/bold red]"
    )
    while True:
        parsed = await _parse_final_tmdb_input(
            await _read_final_tmdb_input(), category
        )
        if parsed is not None:
            return parsed


async def get_tmdb_id(
    filename: str,
    search_year: str | int | None,
    category: str | dict[str, Any],
    untouched_filename: str | None = "",
    attempted: int = 0,
    debug: bool = False,
    secondary_title: str | None = None,
    path: str | None = None,
    final_attempt: bool | None = None,
    new_category: str | None = None,
    unattended: bool = False,
    category_preference: str | None = None,
) -> tuple[int, str]:
    _ = debug
    _ = path
    _ = final_attempt
    category_str = _normalized_search_category(
        category, category_preference, new_category
    )
    original_category = category_str
    result = await _fallback_tmdb_searches(
        filename,
        search_year,
        category_str,
        original_category,
        untouched_filename,
        secondary_title,
        unattended,
        attempted,
    )
    if result[0] or unattended:
        if not result[0]:
            logger.info(
                "[bold red]Unable to find TMDb match using any search[/bold red]"
            )
        return result
    return await _final_manual_tmdb_prompt(category_str)


def _configured_tmdb_defaults(
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    configured = config.get("DEFAULT")
    return (
        typing_cast(dict[str, Any], configured)
        if isinstance(configured, dict)
        else None
    )


def _refresh_tmdb_call_authentication(
    config: dict[str, Any] | None, defaults: dict[str, Any]
) -> None:
    raw = _first_nonempty(
        defaults.get("tmdb_access_token"),
        defaults.get("tmdb_api"),
        default=None,
    )
    if isinstance(raw, str) and raw.strip() and isinstance(config, dict):
        _apply_config(config)


def _runtime_tmdb_defaults(config: dict[str, Any] | None) -> dict[str, Any]:
    configured = _configured_tmdb_defaults(config)
    if configured is None:
        return default_config
    _refresh_tmdb_call_authentication(config, configured)
    return configured


def _tmdb_lookup_context(
    search_year: str | int | None,
    debug: bool,
    category: str | None,
    mode: str,
) -> dict[str, Any]:
    return {
        "tmdb_id": 0,
        "search_year": search_year,
        "debug": debug,
        "category": category,
        "mode": mode,
    }


def _tmdb_lookup_titles(path: str | None) -> tuple[str, str]:
    guessed = guessit_fn(path or "", {"excludes": ["country", "language"]})
    raw_title = str(guessed.get("title", "")).lower().split("aka")[0]
    normalized = str(
        guessit_fn(raw_title, {"excludes": ["country", "language"]}).get(
            "title", ""
        )
    )
    return raw_title, normalized


async def _search_missing_tmdb_id(
    path: str | None,
    search_year: str | int | None,
    category: str | None,
    debug: bool,
    mode: str,
    unattended: bool,
) -> int:
    raw_title, normalized_title = _tmdb_lookup_titles(path)
    tmdb_id, _category = await get_tmdb_id(
        normalized_title,
        search_year,
        _tmdb_lookup_context(search_year, debug, category, mode),
        category,
        unattended=unattended,
    )
    if tmdb_id:
        return int(tmdb_id)
    tmdb_id, _category = await get_tmdb_id(
        raw_title,
        "",
        _tmdb_lookup_context("", debug, category, mode),
        category,
        unattended=unattended,
    )
    return int(tmdb_id or 0)


def _missing_tmdb_lookup_result(mode: str) -> None:
    if mode == "cli":
        logger.info("[bold red]Unable to find tmdb entry. Exiting.")
        raise OperationAbortedError(
            "TMDb metadata selection was cancelled by the user."
        )
    logger.info("[bold red]Unable to find tmdb entry")


async def _looked_up_tmdb_other_id(
    path: str | None,
    search_year: str | int | None,
    category: str | None,
    debug: bool,
    mode: str,
    unattended: bool,
) -> int | None:
    resolved = await _search_missing_tmdb_id(
        path, search_year, category, debug, mode, unattended
    )
    if resolved:
        return resolved
    _missing_tmdb_lookup_result(mode)
    return None


async def _resolved_tmdb_other_id(
    tmdb_id: int,
    path: str | None,
    search_year: str | int | None,
    category: str | None,
    debug: bool,
    mode: str,
    unattended: bool,
) -> int | None:
    if tmdb_id:
        return tmdb_id
    try:
        return await _looked_up_tmdb_other_id(
            path, search_year, category, debug, mode, unattended
        )
    except AmbiguousMetadataError, OperationAbortedError:
        raise
    except Exception:
        _missing_tmdb_lookup_result(mode)
        return None


def _tmdb_main_url(category: str | None, tmdb_id: int) -> str:
    endpoint = "movie" if category == "MOVIE" else "tv"
    return f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}"


async def _main_tmdb_media_data(
    client: httpx.AsyncClient,
    cache: Any,
    category: str | None,
    tmdb_id: int,
) -> dict[str, Any] | None:
    cache_key = json.dumps(
        {"category": category, "id": tmdb_id}, sort_keys=True
    )
    cached = await cache.get("tmdb", "main", cache_key)
    if not is_cache_miss(cached) and isinstance(cached, dict):
        return typing_cast(dict[str, Any], cached)
    response = await client.get(_tmdb_main_url(category, tmdb_id), params={})
    try:
        response.raise_for_status()
        media_data = typing_cast(dict[str, Any], response.json())
    except Exception:
        logger.info(
            f"[bold red]Failed to fetch media data: {response.status_code}[/bold red]"
        )
        return None
    await cache.set("tmdb", "main", cache_key, media_data)
    return media_data


def _date_year(raw_date: Any, fallback: Any) -> Any:
    if not raw_date:
        return fallback
    return (
        datetime.strptime(str(raw_date), "%Y-%m-%d").replace(tzinfo=UTC).year
    )


def _movie_imdb_mismatch(original_imdb_id: int, external_id: int) -> bool:
    return bool(original_imdb_id and external_id != original_imdb_id)


def _movie_main_imdb(
    media_data: dict[str, Any],
    original_imdb_id: int,
    quickie_search: bool,
    unattended: bool,
) -> tuple[int, bool, int]:
    if not quickie_search and original_imdb_id:
        return original_imdb_id, False, 0
    external_id = _external_imdb_id(media_data.get("imdb_id"))
    if not external_id:
        return original_imdb_id, False, 0
    if not _movie_imdb_mismatch(original_imdb_id, external_id):
        return external_id, False, 0
    _reject_unattended_imdb_mismatch(unattended, original_imdb_id, external_id)
    return original_imdb_id, True, external_id


def _movie_core_fields(
    media_data: dict[str, Any],
    search_year: str | int | None,
    original_imdb_id: int,
    quickie_search: bool,
    unattended: bool,
) -> dict[str, Any]:
    title = str(media_data["title"])
    release_date = media_data.get("release_date") or None
    imdb_id, mismatch, mismatched_id = _movie_main_imdb(
        media_data, original_imdb_id, quickie_search, unattended
    )
    return {
        "title": title,
        "original_title": str(media_data.get("original_title") or title),
        "year": _date_year(release_date, search_year or 0),
        "runtime": media_data.get("runtime") or 0,
        "release_date": release_date,
        "first_air_date": None,
        "last_air_date": None,
        "series_status": "",
        "tmdb_type": "Movie",
        "networks": [],
        "imdb_id": imdb_id,
        "imdb_mismatch": mismatch,
        "mismatched_imdb_id": mismatched_id,
    }


def _year_from_title(title: str) -> int | None:
    match = re.search(r"(18|19|20)\d{2}", title)
    return int(match.group(0)) if match else None


def _tv_year(
    media_data: dict[str, Any], search_year: str | int | None, title: str
) -> Any:
    first_air_date = media_data.get("first_air_date")
    year = _date_year(first_air_date, search_year)
    if year:
        return year
    title_year = _year_from_title(title)
    if title_year is not None:
        return title_year
    return _date_year(media_data.get("last_air_date"), 0)


def _tv_runtime(media_data: dict[str, Any]) -> Any:
    raw = media_data.get("episode_run_time", [60])
    if isinstance(raw, list) and raw:
        return typing_cast(list[Any], raw)[0]
    return 60


def _tv_networks(media_data: dict[str, Any]) -> list[Any]:
    raw = media_data.get("networks", [])
    return typing_cast(list[Any], raw) if isinstance(raw, list) else []


def _tv_core_fields(
    media_data: dict[str, Any],
    search_year: str | int | None,
    original_imdb_id: int,
) -> dict[str, Any]:
    title = str(media_data["name"])
    return {
        "title": title,
        "original_title": str(media_data.get("original_name") or title),
        "year": _tv_year(media_data, search_year, title),
        "runtime": _tv_runtime(media_data),
        "release_date": None,
        "first_air_date": media_data.get("first_air_date"),
        "last_air_date": media_data.get("last_air_date"),
        "series_status": str(media_data.get("status") or ""),
        "tmdb_type": media_data.get("type", "Scripted"),
        "networks": _tv_networks(media_data),
        "imdb_id": original_imdb_id,
        "imdb_mismatch": False,
        "mismatched_imdb_id": 0,
    }


def _tmdb_core_fields(
    category: str | None,
    media_data: dict[str, Any],
    search_year: str | int | None,
    original_imdb_id: int,
    quickie_search: bool,
    unattended: bool,
) -> dict[str, Any]:
    if category == "MOVIE":
        return _movie_core_fields(
            media_data,
            search_year,
            original_imdb_id,
            quickie_search,
            unattended,
        )
    return _tv_core_fields(media_data, search_year, original_imdb_id)


def _tmdb_visual_fields(
    media_data: dict[str, Any], poster: str | None
) -> dict[str, Any]:
    poster_path = media_data.get("poster_path", "")
    artwork_url = poster
    if artwork_url is None and poster_path:
        artwork_url = f"https://image.tmdb.org/t/p/original{poster_path}"
    backdrop_path = media_data.get("backdrop_path", "")
    backdrop = (
        f"https://image.tmdb.org/t/p/original{backdrop_path}"
        if backdrop_path
        else ""
    )
    return {
        "artwork_url": artwork_url,
        "tmdb_poster_path": poster_path,
        "backdrop": backdrop,
    }


def _supplemental_tmdb_urls(
    main_url: str,
    category: str | None,
    tmdb_id: int,
    add_logo: bool,
) -> dict[str, str]:
    urls = {
        "external": f"{main_url}/external_ids",
        "videos": f"{main_url}/videos",
        "keywords": f"{main_url}/keywords",
        "credits": f"{main_url}/credits",
    }
    if add_logo:
        endpoint = "movie" if category == "MOVIE" else "tv"
        urls["logo"] = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/images"
    return urls


async def _supplemental_tmdb_results(
    client: httpx.AsyncClient,
    main_url: str,
    category: str | None,
    tmdb_id: int,
    add_logo: bool,
) -> dict[str, Any]:
    urls = _supplemental_tmdb_urls(main_url, category, tmdb_id, add_logo)
    keys = list(urls)
    results = await asyncio.gather(
        *(client.get(url, params={}) for url in urls.values()),
        return_exceptions=True,
    )
    return dict(zip(keys, results, strict=True))


def _json_from_supplement(
    value: Any, fetch_message: str, process_message: str
) -> dict[str, Any] | None:
    if isinstance(value, BaseException):
        logger.info(fetch_message)
        return None
    try:
        data = value.json()
        return (
            typing_cast(dict[str, Any], data)
            if isinstance(data, dict)
            else None
        )
    except Exception:
        logger.info(process_message)
        return None


def _positive_numeric_text(value: str) -> int:
    text = value.strip()
    if not text.isdigit():
        return 0
    parsed = int(text)
    return parsed if parsed > 0 else 0


def _positive_tvdb_id(value: Any) -> int:
    if isinstance(value, int):
        return value if value > 0 else 0
    return _positive_numeric_text(value) if isinstance(value, str) else 0


def _processed_external_ids(
    value: Any,
    original_imdb_id: int,
    current_imdb_id: int,
    current_tvdb_id: int,
    quickie_search: bool,
    unattended: bool,
) -> tuple[int, int, bool, int]:
    data = _json_from_supplement(
        value,
        "[bold red]Failed to fetch external IDs[/bold red]",
        "[bold red]Failed to process external IDs[/bold red]",
    )
    if data is None:
        return current_imdb_id, current_tvdb_id, False, 0
    imdb_id, mismatch, mismatched_id = _reconcile_tmdb_imdb_id(
        original_imdb_id, data.get("imdb_id"), quickie_search
    )
    _reject_unattended_imdb_mismatch(
        unattended, original_imdb_id, mismatched_id
    )
    if not quickie_search and mismatched_id:
        logger.warning(
            f"[yellow]Warning: TMDb IMDb ID ({mismatched_id}) does not match provided IMDb ID ({original_imdb_id}). Using original IMDb ID.[/yellow]"
        )
    tvdb_id = current_tvdb_id or _positive_tvdb_id(data.get("tvdb_id"))
    return imdb_id, tvdb_id, mismatch, mismatched_id


def _youtube_from_supplement(value: Any) -> str:
    data = _json_from_supplement(
        value,
        "[yellow]Unable to grab videos from TMDb.[/yellow]",
        "[yellow]Unable to process videos from TMDb.[/yellow]",
    )
    if data is None:
        return ""
    for entry in _dict_list(data.get("results", [])):
        if entry.get("site") == "YouTube" and entry.get("type") == "Trailer":
            return f"https://www.youtube.com/watch?v={entry.get('key')}"
    return ""


def _keywords_from_supplement(value: Any, category: str | None) -> list[str]:
    data = _json_from_supplement(
        value,
        "[bold red]Failed to fetch keywords[/bold red]",
        "[bold red]Failed to process keywords[/bold red]",
    )
    return _keyword_names(data, category or "TV") if data is not None else []


def _unique_first_five(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))[:5]


def _creator_names(media_data: dict[str, Any]) -> list[str]:
    names = [
        str(entry.get("original_name") or entry.get("name") or "")
        for entry in _dict_list(media_data.get("created_by", []))
    ]
    return _unique_first_five(names)


def _is_actor_credit(entry: dict[str, Any]) -> bool:
    if entry.get("known_for_department") == "Acting":
        return True
    return entry.get("job") in {"Actor", "Actress"}


def _director_credit_names(entries: list[dict[str, Any]]) -> list[str]:
    return [
        _credit_name(entry) for entry in entries if _is_director_credit(entry)
    ]


def _actor_credit_names(entries: list[dict[str, Any]]) -> list[str]:
    return [
        _credit_name(entry) for entry in entries if _is_actor_credit(entry)
    ]


def _credits_people(value: Any) -> tuple[list[str], list[str]]:
    data = _json_from_supplement(
        value,
        "[bold red]Failed to fetch credits[/bold red]",
        "[bold red]Failed to process credits[/bold red]",
    )
    if data is None:
        return [], []
    entries = _credit_entries(data)
    return (
        _unique_first_five(_director_credit_names(entries)),
        _unique_first_five(_actor_credit_names(entries)),
    )


def _logo_supplement_available(value: Any, add_logo: bool) -> bool:
    return bool(
        add_logo and value is not None and not isinstance(value, BaseException)
    )


def _logo_supplement_json(value: Any, add_logo: bool) -> dict[str, Any] | None:
    if not _logo_supplement_available(value, add_logo):
        return None
    try:
        raw = value.json()
    except Exception:
        logger.info("[yellow]Failed to process logo[/yellow]")
        return None
    return typing_cast(dict[str, Any], raw) if isinstance(raw, dict) else None


async def _logo_from_json(
    raw: dict[str, Any],
    tmdb_id: int,
    category: str | None,
    runtime_default_config: dict[str, Any],
) -> tuple[str, str]:
    logo_path = await get_logo(
        tmdb_id,
        category or "MOVIE",
        logo_languages=runtime_default_config.get("logo_language"),
        tmdb_base_url=TMDB_BASE_URL,
        logo_json=raw,
    )
    return logo_path, logo_path.split("/")[-1] if logo_path else ""


async def _logo_from_supplement(
    value: Any,
    add_logo: bool,
    tmdb_id: int,
    category: str | None,
    runtime_default_config: dict[str, Any],
) -> tuple[str, str]:
    raw = _logo_supplement_json(value, add_logo)
    if raw is None:
        return "", ""
    try:
        return await _logo_from_json(
            raw, tmdb_id, category, runtime_default_config
        )
    except Exception:
        logger.info("[yellow]Failed to process logo[/yellow]")
        return "", ""


def _resolved_original_language(
    manual_language: str | None,
    tmdb_language: str,
    requested_language: str | None,
) -> str | None:
    value = _first_nonempty(
        manual_language, tmdb_language, requested_language, default=None
    )
    return str(value) if value is not None else None


async def _tmdb_anime_fields(
    media_data: dict[str, Any],
    title: str,
    category: str | None,
    filename: str | None,
    path: str | None,
    mal_manual: int | None,
) -> tuple[int, str, bool, str]:
    anime_filename = filename if category == "MOVIE" else path
    mal_id, retrieved_aka, anime, demographic = await get_anime(
        media_data,
        Meta(
            {
                "title": title,
                "aka": "",
                "mal_id": 0,
                "filename": anime_filename,
            }
        ),
    )
    if mal_manual is not None and mal_manual != 0:
        mal_id = mal_manual
    return mal_id, retrieved_aka, anime, demographic


def _aka_too_similar(title: str, retrieved_aka: str) -> bool:
    candidate = retrieved_aka[5:].strip()
    if not candidate:
        return True
    difference = SequenceMatcher(
        None, title.lower(), candidate.lower()
    ).ratio()
    if difference >= 0.7:
        return True
    return candidate.lower() in title.lower()


def _clean_retrieved_aka(title: str, retrieved_aka: str, year: Any) -> str:
    if not retrieved_aka:
        return ""
    if _aka_too_similar(title, retrieved_aka):
        return ""
    year_text = f"({year})" if year else ""
    return (
        retrieved_aka.replace(year_text, "").strip()
        if year_text
        else retrieved_aka
    )


def _tmdb_other_result(
    core: dict[str, Any],
    common: dict[str, Any],
    *,
    imdb_id: int,
    tvdb_id: int,
    origin_country: list[Any],
    original_language: str | None,
    keywords: list[str],
    genres: list[str],
    genre_ids: str,
    creators: list[str],
    directors: list[str],
    cast: list[str],
    mal_id: int,
    anime: bool,
    demographic: str,
    retrieved_aka: str,
    logo_path: str,
    tmdb_logo: str,
    youtube: str,
    production_companies: Any,
    production_countries: Any,
    imdb_mismatch: bool,
    mismatched_imdb_id: int,
) -> dict[str, Any]:
    return {
        "tmdb_adult_media": common["adult_media"],
        "title": core["title"],
        "year": core["year"],
        "release_date": core["release_date"],
        "first_air_date": core["first_air_date"],
        "last_air_date": core["last_air_date"],
        "series_status": core["series_status"],
        "imdb_id": imdb_id,
        "tvdb_id": tvdb_id,
        "origin_country": origin_country,
        "original_language": original_language,
        "original_title": core["original_title"],
        "keywords": keywords,
        "genres": genres,
        "genre_ids": genre_ids,
        "tmdb_creators": creators,
        "tmdb_directors": directors,
        "tmdb_cast": cast,
        "mal_id": mal_id,
        "anime": anime,
        "demographic": demographic,
        "retrieved_aka": retrieved_aka,
        "artwork_url": common["artwork_url"],
        "tmdb_poster_path": common["poster_path"],
        "logo": logo_path,
        "tmdb_logo": tmdb_logo,
        "backdrop": common["backdrop"],
        "overview": common["overview"],
        "tmdb_type": core["tmdb_type"],
        "runtime": core["runtime"],
        "youtube": youtube,
        "certification": "",
        "production_companies": production_companies,
        "production_countries": production_countries,
        "networks": core["networks"],
        "imdb_mismatch": imdb_mismatch,
        "mismatched_imdb_id": mismatched_imdb_id,
    }


@dataclass(frozen=True)
class _TmdbOtherPrimary:
    media_data: dict[str, Any]
    core: dict[str, Any]
    common: dict[str, Any]
    tmdb_language: str
    add_logo: bool
    supplemental: dict[str, Any]


@dataclass(frozen=True)
class _TmdbOtherSupplemental:
    imdb_id: int
    tvdb_id: int
    imdb_mismatch: bool
    mismatched_imdb_id: int
    youtube: str
    keywords: list[str]
    directors: list[str]
    cast: list[str]
    logo_path: str
    tmdb_logo: str


def _tmdb_other_common_fields(
    media_data: dict[str, Any], poster: str | None
) -> dict[str, Any]:
    visual = _tmdb_visual_fields(media_data, poster)
    return {
        "adult_media": media_data.get("adult", False),
        "artwork_url": visual["artwork_url"],
        "poster_path": visual["tmdb_poster_path"],
        "backdrop": visual["backdrop"],
        "overview": media_data.get("overview", ""),
    }


async def _tmdb_other_primary(
    client: httpx.AsyncClient,
    cache: Any,
    category: str | None,
    tmdb_id: int,
    search_year: str | int | None,
    original_imdb_id: int,
    quickie_search: bool,
    unattended: bool,
    poster: str | None,
    runtime_defaults: dict[str, Any],
) -> _TmdbOtherPrimary | None:
    media_data = await _main_tmdb_media_data(client, cache, category, tmdb_id)
    if media_data is None:
        return None
    logger.debug(
        f"[cyan]TMDB Response: {json.dumps(media_data, indent=2)[:1200]}..."
    )
    core = _tmdb_core_fields(
        category,
        media_data,
        search_year,
        original_imdb_id,
        quickie_search,
        unattended,
    )
    add_logo = bool(runtime_defaults.get("add_logo", False))
    supplemental = await _supplemental_tmdb_results(
        client,
        _tmdb_main_url(category, tmdb_id),
        category,
        tmdb_id,
        add_logo,
    )
    return _TmdbOtherPrimary(
        media_data=media_data,
        core=core,
        common=_tmdb_other_common_fields(media_data, poster),
        tmdb_language=str(media_data.get("original_language", "")),
        add_logo=add_logo,
        supplemental=supplemental,
    )


async def _tmdb_other_supplemental(
    primary: _TmdbOtherPrimary,
    category: str | None,
    tmdb_id: int,
    original_imdb_id: int,
    current_tvdb_id: int,
    quickie_search: bool,
    unattended: bool,
    runtime_defaults: dict[str, Any],
) -> _TmdbOtherSupplemental:
    imdb_result, tvdb_result, mismatch, mismatched_id = (
        _processed_external_ids(
            primary.supplemental.get("external"),
            original_imdb_id,
            int(primary.core["imdb_id"]),
            current_tvdb_id,
            quickie_search,
            unattended,
        )
    )
    directors, cast = _credits_people(primary.supplemental.get("credits"))
    logo_path, tmdb_logo = await _logo_from_supplement(
        primary.supplemental.get("logo"),
        primary.add_logo,
        tmdb_id,
        category,
        runtime_defaults,
    )
    return _TmdbOtherSupplemental(
        imdb_id=imdb_result,
        tvdb_id=tvdb_result,
        imdb_mismatch=mismatch,
        mismatched_imdb_id=mismatched_id,
        youtube=_youtube_from_supplement(primary.supplemental.get("videos")),
        keywords=_keywords_from_supplement(
            primary.supplemental.get("keywords"), category
        ),
        directors=directors,
        cast=cast,
        logo_path=logo_path,
        tmdb_logo=tmdb_logo,
    )


def _origin_country_values(media_data: dict[str, Any]) -> list[Any]:
    value = media_data.get("origin_country")
    return (
        list(typing_cast(list[Any], value)) if isinstance(value, list) else []
    )


async def _tmdb_genre_values(
    media_data: dict[str, Any],
) -> tuple[list[str], str]:
    data = await get_genres(media_data)
    raw_names = data.get("genre_names", [])
    names = (
        [str(name) for name in typing_cast(list[Any], raw_names)]
        if isinstance(raw_names, list)
        else []
    )
    return names, str(data.get("genre_ids", ""))


async def _tmdb_other_anime_values(
    primary: _TmdbOtherPrimary,
    category: str | None,
    filename: str | None,
    path: str | None,
    mal_manual: int | None,
) -> tuple[int, str, bool, str]:
    mal_id, retrieved_aka, anime, demographic = await _tmdb_anime_fields(
        primary.media_data,
        str(primary.core["title"]),
        category,
        filename,
        path,
        mal_manual,
    )
    cleaned_aka = _clean_retrieved_aka(
        str(primary.core["title"]), retrieved_aka, primary.core["year"]
    )
    return mal_id, cleaned_aka, anime, demographic


async def _assemble_tmdb_other_result(
    primary: _TmdbOtherPrimary,
    supplemental: _TmdbOtherSupplemental,
    category: str | None,
    filename: str | None,
    path: str | None,
    mal_manual: int | None,
    manual_language: str | None,
    requested_language: str | None,
) -> dict[str, Any]:
    genres, genre_ids = await _tmdb_genre_values(primary.media_data)
    mal_id, retrieved_aka, anime, demographic = await _tmdb_other_anime_values(
        primary, category, filename, path, mal_manual
    )
    mismatch = bool(
        primary.core["imdb_mismatch"] or supplemental.imdb_mismatch
    )
    mismatched_id = int(
        supplemental.mismatched_imdb_id
        or primary.core["mismatched_imdb_id"]
        or 0
    )
    return _tmdb_other_result(
        primary.core,
        primary.common,
        imdb_id=supplemental.imdb_id,
        tvdb_id=supplemental.tvdb_id,
        origin_country=_origin_country_values(primary.media_data),
        original_language=_resolved_original_language(
            manual_language, primary.tmdb_language, requested_language
        ),
        keywords=supplemental.keywords,
        genres=genres,
        genre_ids=genre_ids,
        creators=_creator_names(primary.media_data),
        directors=supplemental.directors,
        cast=supplemental.cast,
        mal_id=mal_id,
        anime=anime,
        demographic=demographic,
        retrieved_aka=retrieved_aka,
        logo_path=supplemental.logo_path,
        tmdb_logo=supplemental.tmdb_logo,
        youtube=supplemental.youtube,
        production_companies=primary.media_data.get(
            "production_companies", []
        ),
        production_countries=primary.media_data.get(
            "production_countries", []
        ),
        imdb_mismatch=mismatch,
        mismatched_imdb_id=mismatched_id,
    )


async def tmdb_other_meta(
    tmdb_id: int | None,
    path: str | None = None,
    search_year: str | int | None = None,
    category: str | None = None,
    imdb_id: int | str | None = 0,
    manual_language: str | None = None,
    anime: bool = False,
    mal_manual: int | None = None,
    aka: str = "",
    original_language: str | None = None,
    poster: str | None = None,
    debug: bool = False,
    mode: str = "non_cli",
    tvdb_id: int | None = 0,
    quickie_search: bool = False,
    filename: str | None = None,
    base_dir: str = "",
    config: dict[str, Any] | None = None,
    unattended: bool = False,
) -> dict[str, Any]:
    """Fetch metadata from TMDb for a movie or TV show."""
    _ = anime
    _ = aka
    runtime_defaults = _runtime_tmdb_defaults(config)
    resolved_tmdb_id = await _resolved_tmdb_other_id(
        int(tmdb_id or 0),
        path,
        search_year,
        category,
        debug,
        mode,
        unattended,
    )
    if resolved_tmdb_id is None:
        return {}
    original_imdb_id = _external_imdb_id(imdb_id)
    cache = cache_for(base_dir, config)
    async with _tmdb_client() as client:
        primary = await _tmdb_other_primary(
            client,
            cache,
            category,
            resolved_tmdb_id,
            search_year,
            original_imdb_id,
            quickie_search,
            unattended,
            poster,
            runtime_defaults,
        )
        if primary is None:
            return {}
        supplemental = await _tmdb_other_supplemental(
            primary,
            category,
            resolved_tmdb_id,
            original_imdb_id,
            int(tvdb_id or 0),
            quickie_search,
            unattended,
            runtime_defaults,
        )
    return await _assemble_tmdb_other_result(
        primary,
        supplemental,
        category,
        filename,
        path,
        mal_manual,
        manual_language,
        original_language,
    )


def _keyword_entries(
    data: dict[str, Any], category: str
) -> list[dict[str, Any]]:
    key = "keywords" if category == "MOVIE" else "results"
    raw = data.get(key, [])
    if not isinstance(raw, list):
        return []
    return [
        typing_cast(dict[str, Any], item)
        for item in typing_cast(list[Any], raw)
        if isinstance(item, dict)
    ]


def _keyword_names(data: dict[str, Any], category: str) -> list[str]:
    return [
        str(entry.get("name", "")).replace(",", " ")
        for entry in _keyword_entries(data, category)
        if entry.get("name")
    ]


async def get_keywords(tmdb_id: int, category: str) -> list[str]:
    """Get keywords for a movie or TV show using httpx."""
    endpoint = "movie" if category == "MOVIE" else "tv"
    url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/keywords"
    async with _tmdb_client() as client:
        try:
            response = await client.get(url, params={})
            response.raise_for_status()
            data = typing_cast(dict[str, Any], response.json())
            return _keyword_names(data, category)
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[bold red]Failed to fetch keywords: {error.response.status_code}[/bold red]"
            )
            return []
        except Exception as error:
            logger.info(f"[yellow]Failed to get keywords: {error!s}")
            return []


def _named_genre_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    genre = typing_cast(dict[str, Any], value)
    return genre if genre.get("name") else None


def _valid_tmdb_genres(
    response_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    raw: Any = None if response_data is None else response_data.get("genres")
    if not isinstance(raw, list):
        return []
    genres: list[dict[str, Any]] = []
    for value in typing_cast(list[Any], raw):
        genre = _named_genre_entry(value)
        if genre is not None:
            genres.append(genre)
    return genres


def _genre_ids(genres: list[dict[str, Any]]) -> str:
    return ", ".join(
        str(genre["id"]) for genre in genres if genre.get("id") is not None
    )


async def get_genres(response_data: dict[str, Any] | None) -> dict[str, Any]:
    """Extract genres from TMDB response data."""
    genres = _valid_tmdb_genres(response_data)
    return {
        "genre_names": [
            str(genre["name"]).replace(",", " ") for genre in genres
        ],
        "genre_ids": _genre_ids(genres),
    }


def _credit_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key in ("cast", "crew"):
        raw = data.get(key, [])
        if isinstance(raw, list):
            entries.extend(
                typing_cast(dict[str, Any], item)
                for item in typing_cast(list[Any], raw)
                if isinstance(item, dict)
            )
    return entries


def _is_director_credit(entry: dict[str, Any]) -> bool:
    return (
        entry.get("known_for_department") == "Directing"
        or entry.get("job") == "Director"
    )


def _credit_name(entry: dict[str, Any]) -> str:
    return str(entry.get("original_name") or entry.get("name") or "")


def _director_names(data: dict[str, Any]) -> list[str]:
    return [
        _credit_name(entry)
        for entry in _credit_entries(data)
        if _is_director_credit(entry)
    ]


async def get_directors(tmdb_id: int, category: str) -> list[str]:
    """Get directors for a movie or TV show using httpx."""
    endpoint = "movie" if category == "MOVIE" else "tv"
    url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/credits"
    async with _tmdb_client() as client:
        try:
            response = await client.get(url, params={})
            response.raise_for_status()
            return _director_names(
                typing_cast(dict[str, Any], response.json())
            )
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[bold red]Failed to fetch credits: {error.response.status_code}[/bold red]"
            )
            return []
        except Exception as error:
            logger.info(f"[yellow]Failed to get directors: {error!s}")
            return []


def _origin_country_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(country).upper() for country in typing_cast(list[Any], value)]


def _production_country_code(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        typing_cast(dict[str, Any], value).get("iso_3166_1", "")
    ).upper()


def _production_country_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        code
        for code in (
            _production_country_code(item)
            for item in typing_cast(list[Any], value)
        )
        if code
    ]


def _response_countries(
    response: dict[str, Any],
) -> tuple[list[str], list[str]]:
    return (
        _origin_country_codes(response.get("origin_country", [])),
        _production_country_codes(response.get("production_countries", [])),
    )


def _response_has_animation(response: dict[str, Any]) -> bool:
    raw = response.get("genres", [])
    if not isinstance(raw, list):
        return False
    return any(
        typing_cast(dict[str, Any], genre).get("id") == 16
        or str(typing_cast(dict[str, Any], genre).get("name", "")).lower()
        == "animation"
        for genre in typing_cast(list[Any], raw)
        if isinstance(genre, dict)
    )


def _is_japanese_animation(response: dict[str, Any]) -> bool:
    if not _response_has_animation(response):
        return False
    language = str(response.get("original_language", "")).lower()
    origin, production = _response_countries(response)
    return language == "ja" or "JP" in origin or "JP" in production


async def _anilist_anime_metadata(meta: Meta) -> tuple[int, str, str]:
    (
        romaji,
        mal_id,
        _eng_title,
        _season_year,
        _episodes,
        demographic,
    ) = await get_romaji(meta.title, meta.mal_id, meta)
    alt_name = f"AKA {romaji}" if romaji and romaji != meta.title else meta.aka
    return mal_id, alt_name, demographic


async def get_anime(
    response: dict[str, Any], meta: Meta
) -> tuple[int, str, bool, str]:
    alt_name = "" if meta.aka == "" else meta.aka
    mal_id = 0
    demographic = ""
    anime = False
    if _is_japanese_animation(response):
        mal_id, alt_name, demographic = await _anilist_anime_metadata(meta)
        anime = True
    if meta.mal_id != 0:
        mal_id = meta.mal_id
        anime = True
    return mal_id, alt_name, anime, demographic


_ANILIST_TITLE_QUERY = """
    query ($search: String) {
        Page (page: 1) {
            pageInfo { total }
            media (search: $search, type: ANIME, sort: SEARCH_MATCH) {
                id
                idMal
                title { romaji english native }
                seasonYear
                episodes
                tags { name }
                externalLinks { id url site siteId }
            }
        }
    }
"""

_ANILIST_MAL_QUERY = """
    query ($search: Int) {
        Page (page: 1) {
            pageInfo { total }
            media (idMal: $search, type: ANIME, sort: SEARCH_MATCH) {
                id
                idMal
                title { romaji english native }
                seasonYear
                episodes
                tags { name }
            }
        }
    }
"""

_ANILIST_DEMOGRAPHICS = (
    "Shounen",
    "Seinen",
    "Shoujo",
    "Josei",
    "Kodomo",
    "Mina",
)


@dataclass(frozen=True)
class _AniListCacheResult:
    found: bool
    media: list[dict[str, Any]]
    demographic: str


def _clean_anilist_title(value: str) -> str:
    cleaned = value.replace("-", "").replace("The Movie", "")
    return " ".join(cleaned.split())


def _anilist_request(
    search_term: str, mal: int | None
) -> tuple[str, dict[str, str | int], str]:
    if mal in (None, 0):
        return (
            _ANILIST_TITLE_QUERY,
            {"search": _clean_anilist_title(search_term)},
            "title",
        )
    return _ANILIST_MAL_QUERY, {"search": int(mal)}, "mal"


def _anilist_cache_key(variables: dict[str, str | int], mode: str) -> str:
    return json.dumps(
        {"search": variables["search"], "mode": mode}, sort_keys=True
    )


def _cached_anilist_result(
    value: Any, default_demographic: str
) -> _AniListCacheResult:
    if is_cache_miss(value) or not isinstance(value, dict):
        return _AniListCacheResult(False, [], default_demographic)
    cached = typing_cast(dict[str, Any], value)
    raw_media = cached.get("media")
    if not isinstance(raw_media, list):
        return _AniListCacheResult(False, [], default_demographic)
    media = _dict_list(raw_media)
    demographic = str(cached.get("demographic", default_demographic))
    return _AniListCacheResult(True, media, demographic)


def _anilist_demographic(text: str, default: str) -> str:
    return next((tag for tag in _ANILIST_DEMOGRAPHICS if tag in text), default)


def _anilist_page_media(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = data.get("data", {})
    if not isinstance(payload, dict):
        return []
    page = typing_cast(dict[str, Any], payload).get("Page", {})
    if not isinstance(page, dict):
        return []
    return _dict_list(typing_cast(dict[str, Any], page).get("media", []))


async def _cache_anilist_result(
    cache: Any,
    cache_key: str,
    media: list[dict[str, Any]],
    demographic: str,
) -> None:
    try:
        await cache.set(
            "anilist",
            "media",
            cache_key,
            {"media": media, "demographic": demographic},
            negative=not bool(media),
        )
    except Exception as error:
        logger.debug(f"[yellow]AniList cache write failed: {error}[/yellow]")


async def _request_anilist_once(
    query: str,
    variables: dict[str, str | int],
    demographic: str,
) -> tuple[list[dict[str, Any]], str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": variables},
        )
    data = typing_cast(dict[str, Any], response.json())
    return _anilist_page_media(data), _anilist_demographic(
        response.text, demographic
    )


async def _fetch_anilist_with_retry(
    cache: Any,
    cache_key: str,
    query: str,
    variables: dict[str, str | int],
    demographic: str,
) -> tuple[list[dict[str, Any]], str]:
    for attempt in range(3):
        try:
            media, resolved_demographic = await _request_anilist_once(
                query, variables, demographic
            )
        except httpx.ReadTimeout, httpx.TimeoutException:
            if attempt < 2:
                logger.info(
                    f"[yellow]AniList request timed out, retrying ({attempt + 2}/3)...[/yellow]"
                )
                continue
            logger.error(
                "[red]Failed to get anime specific info from anilist. Continuing without it..."
            )
            return [], demographic
        except Exception:
            logger.error(
                "[red]Failed to get anime specific info from anilist. Continuing without it..."
            )
            return [], demographic
        await _cache_anilist_result(
            cache, cache_key, media, resolved_demographic
        )
        return media, resolved_demographic
    return [], demographic


async def _anilist_lookup_for_term(
    search_term: str,
    mal: int | None,
    meta: Meta,
    demographic: str,
) -> tuple[list[dict[str, Any]], str]:
    query, variables, mode = _anilist_request(search_term, mal)
    cache = cache_for(meta.base_dir, {"DEFAULT": default_config})
    cache_key = _anilist_cache_key(variables, mode)
    cached = _cached_anilist_result(
        await cache.get("anilist", "media", cache_key), demographic
    )
    if cached.found:
        return cached.media, cached.demographic
    return await _fetch_anilist_with_retry(
        cache, cache_key, query, variables, demographic
    )


async def _anilist_media(
    tmdb_name: str, mal: int | None, meta: Meta
) -> tuple[list[dict[str, Any]], str]:
    demographic = "Mina"
    for raw_term in (tmdb_name, meta.filename):
        if not raw_term:
            continue
        media, demographic = await _anilist_lookup_for_term(
            str(raw_term), mal, meta, demographic
        )
        if media:
            return media, demographic
    return [], demographic


def _season_number(value: Any) -> int | None:
    if not value:
        return None
    match = re.search(r"S?(\d+)", str(value), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _filename_anime_season(filename: str) -> int | None:
    if not filename:
        return None
    with contextlib.suppress(Exception):
        parsed = typing_cast(dict[str, Any], anitopy_parse_fn(filename) or {})
        value = parsed.get("anime_season")
        if value:
            return int(value)
    return None


def _expected_anime_season(meta: Meta) -> int | None:
    manual = _season_number(meta.manual_season)
    if manual is not None:
        return manual
    parsed = _filename_anime_season(meta.filename)
    return parsed if parsed is not None else _season_number(meta.season)


def _anilist_search_name(tmdb_name: str, meta: Meta) -> str:
    filename = meta.filename.lower()
    if "subsplease" in filename:
        return filename
    return re.sub(
        r"[^0-9a-zA-Z\[\\\]]+", "", tmdb_name.lower().replace(" ", "")
    )


def _anime_title_map(anime: dict[str, Any]) -> dict[str, Any]:
    raw = anime.get("title", {})
    return typing_cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _anilist_title_season(title_map: dict[str, Any]) -> int | None:
    for value in title_map.values():
        if not value:
            continue
        match = re.search(r"Season (\d+)", str(value), re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _clean_anilist_candidate_title(value: Any) -> str:
    return re.sub(
        "[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]+ (?=[A-Za-z ]+–)",  # noqa: RUF001
        "",
        str(value).lower().replace(" ", ""),
        flags=re.U,
    )


def _anime_best_similarity(anime: dict[str, Any], search_name: str) -> float:
    values = [
        SequenceMatcher(
            None, _clean_anilist_candidate_title(value), search_name
        ).ratio()
        for value in _anime_title_map(anime).values()
        if value is not None
    ]
    return max(values, default=0.0)


def _season_candidate_preferred(
    anime_season: int | None,
    expected_season: int | None,
    similarity: float,
    best_overall: float,
    best_season: float,
) -> bool:
    return bool(
        expected_season is not None
        and anime_season == expected_season
        and similarity > best_overall * 0.8
        and similarity > best_season
    )


def _select_anilist_media(
    media: list[dict[str, Any]], expected_season: int | None, search_name: str
) -> dict[str, Any]:
    best = media[0]
    best_similarity = -1.0
    season_best: dict[str, Any] | None = None
    season_similarity = 0.0
    for anime in media:
        similarity = _anime_best_similarity(anime, search_name)
        anime_season = _anilist_title_season(_anime_title_map(anime))
        if _season_candidate_preferred(
            anime_season,
            expected_season,
            similarity,
            best_similarity,
            season_similarity,
        ):
            season_best = anime
            season_similarity = similarity
        if similarity > best_similarity:
            best = anime
            best_similarity = similarity
    return season_best if season_best is not None else best


def _preferred_anilist_title(
    titles: dict[str, Any], primary: str, fallback: str
) -> str:
    value = titles.get(primary)
    if value:
        return str(value)
    fallback_value = titles.get(fallback)
    return str(fallback_value) if fallback_value else ""


def _optional_string(value: Any) -> str:
    return "" if value is None else str(value)


def _positive_int(value: Any) -> int:
    try:
        return int(value or 0)
    except TypeError, ValueError:
        return 0


def _resolved_anilist_mal_id(result: dict[str, Any], mal: int | None) -> int:
    return (
        int(mal)
        if mal not in (None, 0)
        else _positive_int(result.get("idMal"))
    )


def _anilist_result_tuple(
    result: dict[str, Any], mal: int | None, demographic: str
) -> tuple[str, int, str, str, int, str]:
    titles = _anime_title_map(result)
    return (
        _preferred_anilist_title(titles, "romaji", "english"),
        _resolved_anilist_mal_id(result, mal),
        _preferred_anilist_title(titles, "english", "romaji"),
        _optional_string(result.get("seasonYear", "")),
        _positive_int(result.get("episodes")),
        demographic,
    )


async def get_romaji(
    tmdb_name: str, mal: int | None, meta: Meta
) -> tuple[str, int, str, str, int, str]:
    media, demographic = await _anilist_media(tmdb_name, mal, meta)
    if not media:
        return "", int(mal or 0), "", "", 0, demographic
    result = _select_anilist_media(
        media,
        _expected_anime_season(meta),
        _anilist_search_name(tmdb_name, meta),
    )
    return _anilist_result_tuple(result, mal, demographic)


def _normalized_tmdb_category(category: str) -> str:
    value = str(category or "MOVIE").upper()
    return value if value in {"MOVIE", "TV"} else "MOVIE"


def _first_mediainfo_track(mediainfo: dict[str, Any]) -> dict[str, Any] | None:
    media = mediainfo.get("media")
    if not isinstance(media, dict):
        return None
    tracks = typing_cast(dict[str, Any], media).get("track")
    if not isinstance(tracks, list) or not tracks:
        return None
    first = typing_cast(list[Any], tracks)[0]
    return (
        typing_cast(dict[str, Any], first) if isinstance(first, dict) else None
    )


def _mediainfo_extra(mediainfo: dict[str, Any]) -> dict[str, Any]:
    first = _first_mediainfo_track(mediainfo)
    if first is None:
        return {}
    extra = first.get("extra")
    return (
        typing_cast(dict[str, Any], extra) if isinstance(extra, dict) else {}
    )


def _tmdb_from_extra(value: Any, category: str) -> tuple[str, int] | None:
    with contextlib.suppress(Exception):
        resolved_category, resolved_id = parse_tmdb_id(value, category)
        return resolved_category, int(resolved_id)
    return None


def _imdb_from_extra(value: Any) -> int | None:
    with contextlib.suppress(Exception):
        return extract_imdb_id(str(value))
    return None


def _tvdb_from_extra(value: Any) -> int | None:
    with contextlib.suppress(Exception):
        resolved = int(value)
        return resolved if resolved else None
    return None


def _apply_tmdb_extra(
    value: Any, category: str, tmdbid: int
) -> tuple[str, int]:
    if tmdbid:
        return category, tmdbid
    result = _tmdb_from_extra(value, category)
    return result if result is not None else (category, tmdbid)


def _apply_imdb_extra(value: Any, imdbid: int | None) -> int | None:
    return imdbid if imdbid else (_imdb_from_extra(value) or imdbid)


def _apply_tvdb_extra(value: Any, tvdbid: int | None) -> int | None:
    return tvdbid if tvdbid else (_tvdb_from_extra(value) or tvdbid)


def _apply_extra_identifier(
    key: str,
    value: Any,
    category: str,
    tmdbid: int,
    imdbid: int | None,
    tvdbid: int | None,
) -> tuple[str, int, int | None, int | None]:
    if key.startswith("tmdb"):
        category, tmdbid = _apply_tmdb_extra(value, category, tmdbid)
    elif key.startswith("imdb"):
        imdbid = _apply_imdb_extra(value, imdbid)
    elif key.startswith("tvdb"):
        tvdbid = _apply_tvdb_extra(value, tvdbid)
    return category, tmdbid, imdbid, tvdbid


async def get_tmdb_imdb_from_mediainfo(
    mediainfo: dict[str, Any],
    category: str,
    is_disc: bool,
    tmdbid: int,
    imdbid: int | None,
    tvdbid: int | None,
) -> tuple[str, int, int | None, int | None]:
    category = _normalized_tmdb_category(category)
    if is_disc:
        return category, tmdbid, imdbid, tvdbid
    for raw_key, value in _mediainfo_extra(mediainfo).items():
        category, tmdbid, imdbid, tvdbid = _apply_extra_identifier(
            str(raw_key).casefold(), value, category, tmdbid, imdbid, tvdbid
        )
    return category, tmdbid, imdbid, tvdbid


def extract_imdb_id(value: str) -> int | None:
    """Extract IMDb ID from various formats"""
    patterns = [
        r"/title/(tt\d+)",  # URL format
        r"^(tt\d+)$",  # Direct tt format
        r"^(\d+)$",  # Plain number
    ]

    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            imdb_id = match.group(1)
            if not imdb_id.startswith("tt"):
                imdb_id = f"tt{imdb_id}"
            return int(imdb_id.replace("tt", ""))

    return None


def _aware_datetime(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def _fetch_tmdb_json(
    client: httpx.AsyncClient, url: str, label: str
) -> dict[str, Any] | None:
    try:
        response = await client.get(url, params={})
    except Exception as error:
        logger.info(f"[bold red]Failed to fetch {label}: {error}[/bold red]")
        return None
    try:
        response.raise_for_status()
        return typing_cast(dict[str, Any], response.json())
    except Exception:
        logger.info(
            f"[bold red]Failed to fetch {label}: {response.status_code}[/bold red]"
        )
        return None


def _season_for_date(seasons: list[dict[str, Any]], date: datetime) -> int:
    season = 1
    for entry in seasons:
        raw_date = entry.get("air_date")
        if not raw_date:
            continue
        air_date = _aware_datetime(str(raw_date))
        if air_date <= date:
            season = int(entry.get("season_number", season))
    return season


def _episode_for_date(
    episodes: list[dict[str, Any]], date: datetime
) -> int | None:
    target = str(date.date())
    match = next(
        (
            entry
            for entry in episodes
            if str(entry.get("air_date", "")) == target
        ),
        None,
    )
    return int(match.get("episode_number", 1)) if match else None


async def daily_to_tmdb_season_episode(
    tmdbid: int, date: str | datetime
) -> tuple[int, int]:
    date = _aware_datetime(date)
    async with _tmdb_client() as client:
        tv_data = await _fetch_tmdb_json(
            client, f"{TMDB_BASE_URL}/tv/{tmdbid}", "TV data"
        )
        if tv_data is None:
            return 0, 0
        season = _season_for_date(_dict_list(tv_data.get("seasons", [])), date)
        season_data = await _fetch_tmdb_json(
            client,
            f"{TMDB_BASE_URL}/tv/{tmdbid}/season/{season}",
            "season data",
        )
        if season_data is None:
            return 0, 0
        episode = _episode_for_date(
            _dict_list(season_data.get("episodes", [])), date
        )
        if episode is None:
            logger.info(
                f"[yellow]Unable to map the date ([bold yellow]{date!s}[/bold yellow]) to a Season/Episode number"
            )
            episode = 1
    return season, episode


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        typing_cast(dict[str, Any], item)
        for item in typing_cast(list[Any], value)
        if isinstance(item, dict)
    ]


def _episode_crew(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": member.get("name", ""),
            "job": member.get("job", ""),
            "department": member.get("department", ""),
        }
        for member in _dict_list(data.get("crew", []))
    ]


def _episode_role_name(crew: list[dict[str, Any]], job: str) -> str:
    match = next((member for member in crew if member.get("job") == job), None)
    return str(match.get("name", "")) if match else ""


def _episode_guest_stars(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": guest.get("name", ""),
            "character": guest.get("character", ""),
            "profile_path": guest.get("profile_path", ""),
        }
        for guest in _dict_list(data.get("guest_stars", []))
    ]


def _episode_imdb_id(data: dict[str, Any]) -> str:
    external_ids = data.get("external_ids", {})
    if not isinstance(external_ids, dict):
        return ""
    return str(typing_cast(dict[str, Any], external_ids).get("imdb_id", ""))


def _episode_info(data: dict[str, Any]) -> dict[str, Any]:
    crew = _episode_crew(data)
    info: dict[str, Any] = {
        "name": data.get("name", ""),
        "overview": data.get("overview", ""),
        "air_date": data.get("air_date", ""),
        "still_path": data.get("still_path", ""),
        "vote_average": data.get("vote_average", 0),
        "episode_number": data.get("episode_number", 0),
        "season_number": data.get("season_number", 0),
        "runtime": data.get("runtime", 0),
        "crew": crew,
        "guest_stars": _episode_guest_stars(data),
        "director": _episode_role_name(crew, "Director"),
        "writer": _episode_role_name(crew, "Writer"),
        "imdb_id": _episode_imdb_id(data),
    }
    still_path = info.get("still_path")
    if still_path:
        info["still_url"] = f"https://image.tmdb.org/t/p/original{still_path}"
    return info


async def get_episode_details(
    tmdb_id: int,
    season_number: int,
    episode_number: int,
) -> dict[str, Any]:
    logger.debug(
        f"[cyan]Fetching episode details for TMDb ID: {tmdb_id}, Season: {season_number}, Episode: {episode_number}[/cyan]"
    )
    async with _tmdb_client() as client:
        try:
            response = await client.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}",
                params={"append_to_response": "images,credits,external_ids"},
            )
            response.raise_for_status()
            data = typing_cast(dict[str, Any], response.json())
            logger.debug(
                f"[cyan]Episode Data: {json.dumps(data, indent=2)[:600]}..."
            )
            return _episode_info(data)
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[bold red]Failed to fetch episode data: {error.response.status_code}[/bold red]"
            )
            return {}
        except Exception:
            logger.error(
                f"[red]Error fetching episode details for {tmdb_id}[/red]"
            )
            logger.info(
                f"[red]Season: {season_number}, Episode: {episode_number}[/red]"
            )
            return {}


def _season_episode_summary(episode: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "air_date",
        "episode_number",
        "episode_type",
        "id",
        "name",
        "overview",
        "runtime",
        "season_number",
        "still_path",
        "vote_average",
        "vote_count",
    )
    return {key: episode.get(key) for key in keys}


def _season_optional_section(
    data: dict[str, Any], parent: str, child: str
) -> Any:
    value = data.get(parent, {})
    if not isinstance(value, dict):
        return None
    return typing_cast(dict[str, Any], value).get(child)


def _season_info(data: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {
        key: data.get(key)
        for key in (
            "_id",
            "air_date",
            "name",
            "overview",
            "id",
            "poster_path",
            "season_number",
            "vote_average",
            "vote_count",
        )
    }
    info["episodes"] = [
        _season_episode_summary(episode)
        for episode in _dict_list(data.get("episodes", []))
    ]
    posters = _season_optional_section(data, "images", "posters")
    if posters is not None:
        info["images"] = {"posters": posters}
    cast = _season_optional_section(data, "credits", "cast")
    if cast is not None:
        info["credits"] = {"cast": cast}
    return info


async def get_season_details(
    tmdb_id: int,
    season_number: int,
) -> dict[str, Any]:
    logger.debug(
        f"[cyan]Fetching season details for TMDb ID: {tmdb_id}, Season: {season_number}[/cyan]"
    )
    async with _tmdb_client() as client:
        try:
            response = await client.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}",
                params={"append_to_response": "images,credits"},
            )
            response.raise_for_status()
            season_info = _season_info(
                typing_cast(dict[str, Any], response.json())
            )
            logger.debug(
                f"[cyan]Extracted season data: {json.dumps(season_info, indent=2)[:600]}...[/cyan]"
            )
            return season_info
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[bold red]Failed to fetch season data: {error.response.status_code}[/bold red]"
            )
            return {}
        except Exception:
            logger.error(
                f"[red]Error fetching season details for {tmdb_id}[/red]"
            )
            logger.info(f"[red]Season: {season_number}[/red]")
            return {}


def _string_logo_languages(value: str) -> list[str]:
    if "," in value:
        return [item.strip() for item in value.split(",")]
    return [value, "en"]


def _raw_logo_languages(value: list[str] | str | None) -> list[str]:
    if value is None:
        return [str(default_config.get("logo_language", "en")), "en"]
    if isinstance(value, str):
        return _string_logo_languages(value)
    return [str(item) for item in value]


def _normalized_logo_languages(value: list[str] | str | None) -> list[str]:
    raw = _raw_logo_languages(value)
    return list(dict.fromkeys(item for item in raw if item))


def _logo_entries(image_data: dict[str, Any]) -> list[dict[str, Any]]:
    return _dict_list(image_data.get("logos", []))


def _logo_url(entry: dict[str, Any] | None) -> str:
    if not entry or not entry.get("file_path"):
        return ""
    return f"https://image.tmdb.org/t/p/original{entry['file_path']}"


def _logo_for_language(
    logos: list[dict[str, Any]], language: str
) -> dict[str, Any] | None:
    return next(
        (
            logo
            for logo in logos
            if logo.get("iso_639_1") == language and logo.get("file_path")
        ),
        None,
    )


def _null_language_logo(logos: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            logo
            for logo in logos
            if logo.get("iso_639_1") in (None, "") and logo.get("file_path")
        ),
        None,
    )


async def _fetch_logo_image_data(
    tmdb_id: int,
    category: str,
    tmdb_api_key: str | None,
    tmdb_base_url: str | None,
) -> dict[str, Any]:
    explicit_params = {"api_key": tmdb_api_key} if tmdb_api_key else {}
    endpoint = "tv" if category == "TV" else "movie"
    base_url = tmdb_base_url or TMDB_BASE_URL
    async with _tmdb_client(params=explicit_params) as client:
        response = await client.get(f"{base_url}/{endpoint}/{tmdb_id}/images")
        try:
            response.raise_for_status()
            return typing_cast(dict[str, Any], response.json())
        except Exception:
            logger.info(
                f"[bold red]Failed to fetch image data: {response.status_code}[/bold red]"
            )
            return {}


async def _logo_image_data(
    tmdb_id: int,
    category: str,
    tmdb_api_key: str | None,
    tmdb_base_url: str | None,
    logo_json: dict[str, Any] | None,
) -> dict[str, Any]:
    if logo_json:
        logger.debug(
            "[cyan]Using provided logo_json data instead of making an HTTP request[/cyan]"
        )
        return logo_json
    return await _fetch_logo_image_data(
        tmdb_id, category, tmdb_api_key, tmdb_base_url
    )


def _select_logo_url(logos: list[dict[str, Any]], languages: list[str]) -> str:
    for language in languages:
        entry = _logo_for_language(logos, language)
        if entry is None:
            continue
        url = _logo_url(entry)
        logger.debug(
            f"[cyan]Found logo in language '{language}': {url}[/cyan]"
        )
        return url
    entry = _null_language_logo(logos)
    if entry is not None:
        url = _logo_url(entry)
        logger.debug(f"[cyan]Found logo with null language: {url}[/cyan]")
        return url
    logger.debug(
        "[yellow]No suitable logo found in preferred languages or null language[/yellow]"
    )
    return ""


async def get_logo(
    tmdb_id: int,
    category: str,
    logo_languages: list[str] | str | None = None,
    tmdb_api_key: str | None = None,
    tmdb_base_url: str | None = None,
    logo_json: dict[str, Any] | None = None,
) -> str:
    languages = _normalized_logo_languages(logo_languages)
    logger.debug(
        f"[cyan]Looking for logos in languages (in order): {languages}[/cyan]"
    )
    try:
        image_data = await _logo_image_data(
            tmdb_id, category, tmdb_api_key, tmdb_base_url, logo_json
        )
        if image_data:
            logger.debug(
                f"[cyan]Image Data: {json.dumps(image_data, indent=2)[:500]}..."
            )
        return _select_logo_url(_logo_entries(image_data), languages)
    except Exception as error:
        logger.error(f"[red]Error fetching logo: {error}[/red]")
        return ""


def _matching_translation(
    data: dict[str, Any], target_language: str
) -> dict[str, Any] | None:
    return next(
        (
            translation
            for translation in _dict_list(data.get("translations", []))
            if translation.get("iso_639_1") == target_language
        ),
        None,
    )


def _translation_title(data: dict[str, Any], target_language: str) -> str:
    translation = _matching_translation(data, target_language)
    if translation is None:
        return ""
    translated = translation.get("data", {})
    if not isinstance(translated, dict):
        return ""
    values = typing_cast(dict[str, Any], translated)
    return str(values.get("title") or values.get("name") or "")


async def get_tmdb_translations(
    tmdb_id: int,
    category: str,
    target_language: str = "en",
) -> str:
    """Get translations from TMDb API."""
    endpoint = "movie" if category == "MOVIE" else "tv"
    url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/translations"
    async with _tmdb_client() as client:
        try:
            response = await client.get(url, params={})
            response.raise_for_status()
            title = _translation_title(
                typing_cast(dict[str, Any], response.json()), target_language
            )
            if title:
                logger.debug(f"[cyan]Found TMDb translation: '{title}'[/cyan]")
                return title
            logger.debug(
                f"[yellow]No {target_language} translation found in TMDb[/yellow]"
            )
            return ""
        except Exception as error:
            logger.debug(
                f"[yellow]TMDb translation fetch failed: {error}[/yellow]"
            )
            return ""


def _tmdb_metadata_populated(meta: Meta) -> bool:
    if meta.edit:
        return False
    essential_fields = ("title", "year", "genres", "overview")
    return all(
        meta.get(field) is not None for field in essential_fields
    ) and bool(meta.get("title"))


def _manual_mal_id(meta: Meta) -> int | None:
    value = meta.mal_manual
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


async def _fetch_tmdb_metadata(
    meta: Meta, filename: str | None
) -> dict[str, Any]:
    return await tmdb_other_meta(
        tmdb_id=meta.tmdb_id,
        path=meta.path,
        search_year=meta.search_year,
        category=meta.category,
        imdb_id=meta.imdb_id,
        manual_language=meta.manual_language
        if isinstance(meta.manual_language, str)
        else None,
        anime=meta.anime,
        mal_manual=_manual_mal_id(meta),
        aka=meta.aka,
        original_language=meta.original_language,
        poster=meta.artwork_url,
        debug=meta.debug,
        mode=(meta.mode if meta.mode is not None else "cli"),
        tvdb_id=meta.tvdb_id,
        quickie_search=meta.quickie_search,
        filename=filename,
        base_dir=meta.base_dir,
        config=default_config,
        unattended=meta.unattended,
    )


def _valid_tmdb_metadata(metadata: dict[str, Any]) -> bool:
    return all(metadata.get(field) for field in ("title", "year"))


def _apply_tmdb_metadata(meta: Meta, metadata: dict[str, Any]) -> None:
    meta.update(metadata)
    if meta.retrieved_aka is not None:
        meta.aka = meta.retrieved_aka


async def _retry_delay(
    attempt: int, max_attempts: int, delay_seconds: int
) -> bool:
    if attempt >= max_attempts:
        return False
    logger.info(
        f"[yellow]Retrying TMDB metadata fetch in {delay_seconds} seconds... (Attempt {attempt + 1}/{max_attempts})[/yellow]"
    )
    await asyncio.sleep(delay_seconds)
    return True


async def _tmdb_metadata_attempt(
    meta: Meta, filename: str | None
) -> tuple[dict[str, Any] | None, Exception | None]:
    try:
        metadata = await _fetch_tmdb_metadata(meta, filename)
    except AmbiguousMetadataError:
        raise
    except Exception as error:
        return None, error
    if _valid_tmdb_metadata(metadata):
        return metadata, None
    return None, ValueError(
        f"Failed to retrieve essential metadata from TMDB ID: {meta.tmdb_id}"
    )


def _raise_tmdb_metadata_failure(meta: Meta, error: Exception) -> None:
    logger.info(
        f"[red]Catastrophic error getting TMDB data using ID {meta.tmdb_id}[/red]"
    )
    logger.info(
        f"[red]Check category is set correctly, UA was using {meta.category}[/red]"
    )
    raise RuntimeError(
        f"Unable to resolve TMDb metadata for ID {meta.tmdb_id}: {error!s}"
    ) from error


async def _resolve_tmdb_metadata(meta: Meta, filename: str | None) -> None:
    max_attempts = 2
    delay_seconds = 5
    for attempt in range(1, max_attempts + 1):
        metadata, error = await _tmdb_metadata_attempt(meta, filename)
        if metadata is not None:
            _apply_tmdb_metadata(meta, metadata)
            return
        if error is None:
            error = RuntimeError(
                f"TMDB metadata retrieval failed for ID {meta.tmdb_id}"
            )
        logger.debug(
            f"[bold red]TMDB metadata retrieval failed for ID {meta.tmdb_id}: {error!s}[/bold red]"
        )
        if await _retry_delay(attempt, max_attempts, delay_seconds):
            continue
        _raise_tmdb_metadata_failure(meta, error)


async def set_tmdb_metadata(meta: Meta, filename: str | None = None) -> None:
    if not _tmdb_metadata_populated(meta):
        await _resolve_tmdb_metadata(meta, filename)


def _localized_main_endpoint(meta: Meta) -> str:
    return f"/{meta.category.lower()}/{meta.tmdb}"


def _localized_season_endpoint(meta: Meta) -> str | None:
    if meta.season_int is None:
        return None
    return f"/tv/{meta.tmdb}/season/{meta.season_int}"


def _localized_episode_endpoint(meta: Meta) -> str | None:
    if meta.season_int is None or meta.episode_int is None:
        return None
    return (
        f"/tv/{meta.tmdb}/season/{meta.season_int}/episode/{meta.episode_int}"
    )


def _localized_endpoint(meta: Meta, data_type: str) -> str | None:
    if meta.tmdb is None:
        return None
    resolvers: dict[str, Callable[[Meta], str | None]] = {
        "main": _localized_main_endpoint,
        "season": _localized_season_endpoint,
        "episode": _localized_episode_endpoint,
    }
    resolver = resolvers.get(data_type)
    return resolver(meta) if resolver is not None else None


def _localized_params(
    language: str, append_to_response: str
) -> dict[str, str]:
    params = {"language": language}
    if append_to_response:
        params["append_to_response"] = append_to_response
    return params


def _localized_cache_key(
    meta: Meta, data_type: str, language: str, append_to_response: str
) -> str:
    return json.dumps(
        {
            "id": meta.tmdb,
            "category": meta.category,
            "type": data_type,
            "language": language,
            "append": append_to_response,
        },
        sort_keys=True,
    )


async def _fetch_localized_tmdb_data(
    url: str, params: dict[str, str]
) -> dict[str, Any]:
    try:
        async with _tmdb_client(timeout=10.0) as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                return (
                    typing_cast(dict[str, Any], data)
                    if isinstance(data, dict)
                    else {}
                )
            logger.info(
                f"[red]Request failed for {url}: Status code {response.status_code}[/red]"
            )
    except httpx.RequestError as error:
        logger.info(f"[red]Request failed for {url}: {error}[/red]")
    return {}


async def get_tmdb_localized_data(
    meta: Meta, data_type: str, language: str, append_to_response: str
) -> dict[str, Any]:
    endpoint = _localized_endpoint(meta, data_type)
    if endpoint is None:
        return {}
    logger.debug(
        "[green]Requesting localized data from TMDB.\n"
        f"Type: '{data_type}'.\nLanguage: '{language}'\n"
        f"Append to response: '{append_to_response}'\nEndpoint: '{endpoint}'[/green]\n"
    )
    cache = cache_for(meta.base_dir, {"DEFAULT": default_config})
    cache_key = _localized_cache_key(
        meta, data_type, language, append_to_response
    )
    cached_result = await cache.get("tmdb", "localized", cache_key)
    if not is_cache_miss(cached_result) and isinstance(cached_result, dict):
        return typing_cast(dict[str, Any], cached_result)
    url = f"{TMDB_BASE_URL}{endpoint}"
    data = await _fetch_localized_tmdb_data(
        url, _localized_params(language, append_to_response)
    )
    if data:
        await cache.set("tmdb", "localized", cache_key, data)
    return data
