# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import contextlib
import json
import re
import time
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx

from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.observability.runtime_support import logger

_OAUTH_URL = "https://id.twitch.tv/oauth2/token"
_GAMES_URL = "https://api.igdb.com/v4/games"
_GAME_FIELDS = (
    "name, summary, storyline, first_release_date, rating, rating_count, "
    "cover.url, screenshots.url, genres.name, platforms.name, "
    "involved_companies.company.name, involved_companies.developer, "
    "involved_companies.publisher, websites.url, websites.type, "
    "external_games.url, external_games.external_game_source, "
    "external_games.uid, language_supports.language.name, "
    "language_supports.language_support_type.name"
)


async def _cached_token(token_file: str | Path) -> tuple[bool, Any]:
    if not token_file or not Path(token_file).exists():
        return False, None
    with contextlib.suppress(Exception):
        async with aiofiles.open(token_file, encoding="utf-8") as handle:
            cached = json.loads(await handle.read())
        if cached.get("expires_at", 0) > time.time() + 300:
            return True, cached.get("access_token")
    return False, None


async def _save_token(
    token_file: str | Path,
    access_token: Any,
    expires_at: Any,
) -> None:
    if not token_file:
        return
    path = Path(token_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": access_token, "expires_at": expires_at}
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(payload))


def _oauth_params(api: IGDBAPI) -> dict[str, str]:
    return {
        "client_id": api.client_id,
        "client_secret": api.client_secret,
        "grant_type": "client_credentials",
    }


async def _request_access_token(api: IGDBAPI) -> Any:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(_OAUTH_URL, params=_oauth_params(api))
        if response.status_code != 200:
            logger.info(
                "[red]IGDB: Failed to authenticate with Twitch API. "
                f"Status: {response.status_code}[/red]"
            )
            return None
        data = response.json()
        token = data.get("access_token")
        expires_at = time.time() + data.get("expires_in", 3600)
        await _save_token(api.token_file, token, expires_at)
        return token
    except Exception as error:
        logger.info(f"[red]IGDB: Twitch OAuth error: {error}[/red]")
        return None


def _api_headers(api: IGDBAPI, token: str) -> dict[str, str]:
    return {
        "Client-ID": api.client_id,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "text/plain",
    }


async def _post_games(api: IGDBAPI, token: str, query: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(
            _GAMES_URL,
            headers=_api_headers(api, token),
            content=query,
        )


async def _cached_list(cache: Any, resource: str, key: str) -> Any:
    data = await cache.get("igdb", resource, key)
    if not is_cache_miss(data) and isinstance(data, list):
        return data
    return None


async def _cached_dict(cache: Any, resource: str, key: str) -> Any:
    data = await cache.get("igdb", resource, key)
    if not is_cache_miss(data) and isinstance(data, dict):
        return data
    return None


def _search_query(title: str) -> str:
    return f'search "{title}"; fields {_GAME_FIELDS}; limit 5;'


def _id_query(igdb_id: str) -> str:
    return f"where id = {igdb_id}; fields {_GAME_FIELDS};"


def _steam_query(steam_id: str) -> str:
    return (
        "where external_games.external_game_source = 1 & "
        f'external_games.uid = "{steam_id}"; fields {_GAME_FIELDS};'
    )


async def _search_response(
    cache: Any,
    key: str,
    response: httpx.Response,
) -> Any:
    if response.status_code != 200:
        logger.info(
            "[red]IGDB: API request failed. "
            f"Status: {response.status_code}, Body: {response.text}[/red]"
        )
        return None
    data = response.json()
    if data is not None:
        await cache.set(
            "igdb",
            "search",
            key,
            data,
            negative=not bool(data),
        )
    return data


async def _remote_search(
    api: IGDBAPI,
    cache: Any,
    key: str,
    title: str,
    token: str,
) -> Any:
    try:
        response = await _post_games(api, token, _search_query(title))
        return await _search_response(cache, key, response)
    except Exception as error:
        logger.info(f"[red]IGDB: Search error: {error}[/red]")
        return None


def _first_game(data: Any) -> tuple[bool, Any]:
    if not isinstance(data, list) or not data:
        return False, None
    return True, data[0]


async def _single_response(
    cache: Any,
    resource: str,
    key: str,
    response: httpx.Response,
    not_found_message: str,
) -> Any:
    if response.status_code != 200:
        logger.info(
            "[red]IGDB: API request failed. "
            f"Status: {response.status_code}, Body: {response.text}[/red]"
        )
        return None
    found, game_data = _first_game(response.json())
    if not found:
        logger.info(not_found_message)
        return None
    if game_data is not None:
        await cache.set("igdb", resource, key, game_data)
    return game_data


async def _remote_single_game(
    api: IGDBAPI,
    cache: Any,
    resource: str,
    key: str,
    query: str,
    token: str,
    not_found_message: str,
    error_label: str,
) -> Any:
    try:
        response = await _post_games(api, token, query)
        return await _single_response(
            cache,
            resource,
            key,
            response,
            not_found_message,
        )
    except Exception as error:
        logger.info(f"[red]IGDB: {error_label}: {error}[/red]")
        return None


def _numeric_id(value: str, label: str) -> str | None:
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    logger.info(f"[red]IGDB: Invalid {label} '{value}'[/red]")
    return None


async def _fetch_single_game(
    api: IGDBAPI,
    resource: str,
    key: str,
    query: str,
    cache_message: str,
    not_found_message: str,
    error_label: str,
) -> Any:
    cache = cache_for(api.base_dir)
    cached_data = await _cached_dict(cache, resource, key)
    if cached_data is not None:
        logger.info(cache_message)
        return cached_data
    token = await api.get_access_token()
    if not token:
        return None
    return await _remote_single_game(
        api,
        cache,
        resource,
        key,
        query,
        token,
        not_found_message,
        error_label,
    )


class IGDBAPI:
    def __init__(self, client_id: str, client_secret: str, base_dir: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_dir = base_dir
        self.token_file = (
            Path(base_dir) / "tmp" / "igdb_cache" / "igdb_token.json"
            if base_dir
            else ""
        )
        self.access_token = None

    async def get_access_token(self) -> str | None:
        cached, token = await _cached_token(self.token_file)
        if cached:
            self.access_token = token
            return cast(str | None, self.access_token)
        self.access_token = await _request_access_token(self)
        return cast(str | None, self.access_token)

    async def search_game(self, title: str) -> list[dict[str, Any]] | None:
        clean_title = re.sub(r"[^a-zA-Z0-9_\-]", "_", title).lower()
        cache = cache_for(self.base_dir)
        cached_data = await _cached_list(cache, "search", clean_title)
        if cached_data is not None:
            logger.info(
                f"[cyan]IGDB: Using cached search results for '{title}'[/cyan]"
            )
            return cast(list[dict[str, Any]], cached_data)
        token = await self.get_access_token()
        if not token:
            return None
        return cast(
            list[dict[str, Any]] | None,
            await _remote_search(self, cache, clean_title, title, token),
        )

    async def fetch_game_by_id(self, igdb_id: str) -> dict[str, Any] | None:
        igdb_id_str = _numeric_id(igdb_id, "ID")
        if igdb_id_str is None:
            return None
        return cast(
            dict[str, Any] | None,
            await _fetch_single_game(
                self,
                "game",
                igdb_id_str,
                _id_query(igdb_id_str),
                "[cyan]IGDB: Using cached game details for ID "
                f"'{igdb_id_str}'[/cyan]",
                f"[red]IGDB: No game found with ID {igdb_id_str}[/red]",
                "Fetch error",
            ),
        )

    async def fetch_game_by_steam_id(
        self, steam_id: str
    ) -> dict[str, Any] | None:
        steam_id_str = _numeric_id(steam_id, "Steam ID")
        if steam_id_str is None:
            return None
        return cast(
            dict[str, Any] | None,
            await _fetch_single_game(
                self,
                "steam",
                steam_id_str,
                _steam_query(steam_id_str),
                "[cyan]IGDB: Using cached game details for Steam ID: "
                f"{steam_id_str}[/cyan]",
                f"[red]IGDB: No game found with Steam ID {steam_id_str}[/red]",
                "Steam Fetch error",
            ),
        )

    async def cache_game_details(self, game_data: dict[str, Any]) -> None:
        if not self.base_dir or not game_data or "id" not in game_data:
            return
        igdb_id = str(game_data["id"])
        await cache_for(self.base_dir).set("igdb", "game", igdb_id, game_data)
