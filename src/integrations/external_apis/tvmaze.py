# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
from typing import Any, cast

import cli_ui
import httpx

from src.domain_models.release import Meta
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.observability.runtime_support import logger

_CACHE_MISS = object()


class TvmazeManager:
    @staticmethod
    def _has_external_id_value(value: int | str | None) -> bool:
        if not isinstance(value, int | str):
            return False
        return value not in ("", "0")

    @staticmethod
    def _external_id_raw(value: int | str, *, imdb: bool) -> int | str:
        if imdb and isinstance(value, str) and value.startswith("tt"):
            return value[2:]
        return value

    @classmethod
    def _normalized_external_id(
        cls,
        value: int | str | None,
        label: str,
        *,
        imdb: bool = False,
    ) -> int:
        if not cls._has_external_id_value(value):
            return 0
        raw = cls._external_id_raw(cast(int | str, value), imdb=imdb)
        try:
            return int(raw)
        except TypeError, ValueError:
            logger.error(
                f"[red]Error: {label} is not a valid integer. Received: {value}[/red]"
            )
            return 0

    @staticmethod
    def _search_return_value(
        tvmaze_id: int,
        imdb_id: int,
        tvdb_id: int,
        return_full_tuple: bool,
    ) -> int | tuple[int, int, int]:
        if return_full_tuple:
            return tvmaze_id, imdb_id, tvdb_id
        return tvmaze_id

    @classmethod
    def _manual_tvmaze_value(
        cls,
        tvmaze_manual: int | str,
        imdb_id: int,
        tvdb_id: int,
        return_full_tuple: bool,
    ) -> int | tuple[int, int, int]:
        try:
            tvmaze_id = int(tvmaze_manual)
        except TypeError, ValueError:
            logger.error(
                f"[red]Error: tvmaze_manual is not a valid integer. Received: {tvmaze_manual}[/red]"
            )
            tvmaze_id = 0
        return cls._search_return_value(
            tvmaze_id, imdb_id, tvdb_id, return_full_tuple
        )

    @staticmethod
    def _search_response_items(
        response: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> list[Any]:
        if isinstance(response, dict):
            return [response]
        if isinstance(response, list):
            return cast(list[Any], response)
        return []

    @staticmethod
    def _dressed_search_candidate(raw_item: Any) -> dict[str, Any] | None:
        if not isinstance(raw_item, dict):
            return None
        item = cast(dict[str, Any], raw_item)
        wrapped = item.get("show")
        candidate = (
            cast(dict[str, Any], wrapped)
            if isinstance(wrapped, dict)
            else item
        )
        if not isinstance(candidate.get("id"), int):
            return None
        return candidate

    @classmethod
    def _dressed_search_candidates(
        cls,
        response: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        return [
            candidate
            for raw_item in cls._search_response_items(response)
            if (candidate := cls._dressed_search_candidate(raw_item))
            is not None
        ]

    async def _fetch_search_candidates(
        self,
        url: str,
        params: dict[str, Any],
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        response = await self._make_tvmaze_request(
            url, params, base_dir, config
        )
        return self._dressed_search_candidates(response)

    async def _search_by_tvdb(
        self,
        tvdb_id: int,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not tvdb_id:
            return []
        return await self._fetch_search_candidates(
            "https://api.tvmaze.com/lookup/shows",
            {"thetvdb": tvdb_id},
            base_dir,
            config,
        )

    async def _search_by_imdb(
        self,
        imdb_id: int,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not imdb_id:
            return []
        return await self._fetch_search_candidates(
            "https://api.tvmaze.com/lookup/shows",
            {"imdb": f"tt{imdb_id:07d}"},
            base_dir,
            config,
        )

    async def _search_by_title(
        self,
        title: str,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        return await self._fetch_search_candidates(
            "https://api.tvmaze.com/search/shows",
            {"q": title},
            base_dir,
            config,
        )

    @staticmethod
    def _short_search_title(filename: str) -> str | None:
        first_two_words = " ".join(filename.split()[:2])
        if not first_two_words or first_two_words == filename:
            return None
        return first_two_words

    async def _search_candidates(
        self,
        filename: str,
        imdb_id: int,
        tvdb_id: int,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        results = await self._search_by_tvdb(tvdb_id, base_dir, config)
        if not results:
            results = await self._search_by_imdb(imdb_id, base_dir, config)
        if not results:
            results = await self._search_by_title(filename, base_dir, config)
        short_title = self._short_search_title(filename)
        if not results and short_title is not None:
            results = await self._search_by_title(
                short_title, base_dir, config
            )
        return results

    @staticmethod
    def _unique_search_results(
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen: set[int] = set()
        unique: list[dict[str, Any]] = []
        for show in results:
            show_id = int(show["id"])
            if show_id in seen:
                continue
            seen.add(show_id)
            unique.append(show)
        return unique

    @staticmethod
    def _log_search_results(results: list[dict[str, Any]]) -> None:
        logger.info("[bold]Search results:[/bold]")
        for index, show in enumerate(results, start=1):
            logger.info(
                f"[bold red]{index}[/bold red]. [green]{show.get('name', 'Unknown')} (TVmaze ID:[/green] [bold red]{show['id']}[/bold red])"
            )
            logger.info(
                f"[yellow]   Premiered: {show.get('premiered', 'Unknown')}[/yellow]"
            )
            logger.info(
                f"   Externals: {json.dumps(show.get('externals', {}), indent=2)}"
            )

    @staticmethod
    def _selection_action(
        choice_raw: Any, count: int
    ) -> tuple[str, int | None]:
        try:
            choice = int((choice_raw or "").strip())
        except AttributeError, TypeError, ValueError:
            logger.info("Invalid input. Please enter a number.")
            return "retry", None
        if choice == 0:
            return "skip", None
        if 1 <= choice <= count:
            return "select", choice - 1
        logger.info(
            f"Invalid choice. Please choose a number between 1 and {count}, or 0 to skip."
        )
        return "retry", None

    @staticmethod
    def _selected_tvdb_id(show: dict[str, Any], current_tvdb_id: int) -> int:
        externals_raw = show.get("externals")
        if not isinstance(externals_raw, dict):
            return current_tvdb_id
        externals = cast(dict[str, Any], externals_raw)
        value = externals.get("thetvdb")
        if value in (None, ""):
            return current_tvdb_id
        try:
            return int(value)
        except TypeError, ValueError:
            return current_tvdb_id

    @classmethod
    def _manual_selected_show(
        cls,
        show: dict[str, Any],
        tvdb_id: int,
    ) -> tuple[int, int]:
        tvmaze_id = int(show["id"])
        updated_tvdb = cls._selected_tvdb_id(show, tvdb_id)
        if updated_tvdb != tvdb_id:
            logger.info(f"[green]Updated TVDb ID to: {updated_tvdb}[/green]")
        logger.info(
            f"Selected show: {show.get('name')} (TVmaze ID: {tvmaze_id})"
        )
        return tvmaze_id, updated_tvdb

    @classmethod
    def _manual_show_selection(
        cls,
        results: list[dict[str, Any]],
        tvdb_id: int,
    ) -> tuple[int, int]:
        cls._log_search_results(results)
        while True:
            choice_raw = cli_ui.ask_string(
                f"Enter the number of the correct show (1-{len(results)}) or 0 to skip: "
            )
            action, index = cls._selection_action(choice_raw, len(results))
            if action == "skip":
                logger.info("Skipping selection.")
                return 0, tvdb_id
            if action == "select" and index is not None:
                return cls._manual_selected_show(results[index], tvdb_id)

    @staticmethod
    def _automatic_show_selection(show: dict[str, Any]) -> int:
        tvmaze_id = int(show["id"])
        logger.debug(
            f"[cyan]Automatically selected show: {show.get('name')} (TVmaze ID: {tvmaze_id})[/cyan]"
        )
        return tvmaze_id

    @staticmethod
    def _show_by_id(
        results: list[dict[str, Any]], tvmaze_id: int
    ) -> dict[str, Any] | None:
        for show in results:
            if int(show.get("id", 0)) == tvmaze_id:
                return show
        return None

    @staticmethod
    def _external_ids(show: dict[str, Any]) -> tuple[Any, Any]:
        externals_raw = show.get("externals")
        if not isinstance(externals_raw, dict):
            return None, None
        externals = cast(dict[str, Any], externals_raw)
        return externals.get("imdb"), externals.get("thetvdb")

    @classmethod
    def _adopt_selected_externals(
        cls,
        show: dict[str, Any] | None,
        imdb_id: int,
        tvdb_id: int,
    ) -> tuple[int, int]:
        if show is None or tvdb_id or imdb_id:
            return imdb_id, tvdb_id
        selected_imdb, selected_tvdb = cls._external_ids(show)
        tvdb_id = cls._normalized_external_id(selected_tvdb, "tvdb_id")
        imdb_id = cls._normalized_external_id(
            selected_imdb, "imdb_id", imdb=True
        )
        return imdb_id, tvdb_id

    @staticmethod
    def _log_search_return(
        tvmaze_id: int,
        imdb_id: int,
        tvdb_id: int,
        return_full_tuple: bool,
    ) -> None:
        if return_full_tuple:
            logger.debug(
                f"[cyan]Returning TVmaze ID: {tvmaze_id} (type: {type(tvmaze_id).__name__}), IMDb ID: {imdb_id} (type: {type(imdb_id).__name__}), TVDB ID: {tvdb_id} (type: {type(tvdb_id).__name__})[/cyan]"
            )
            return
        logger.debug(
            f"[cyan]Returning TVmaze ID: {tvmaze_id} (type: {type(tvmaze_id).__name__})[/cyan]"
        )

    async def search_tvmaze(
        self,
        filename: str,
        year: str,
        imdb_id: int | str | None,
        tvdb_id: int | str | None,
        manual_date: str | None = None,
        tvmaze_manual: int | str | None = None,
        return_full_tuple: bool = False,
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> int | tuple[int, int, int]:
        """Search TVMaze using external IDs and title fallbacks."""
        logger.debug(
            f"[cyan]Searching TVMaze for TVDB {tvdb_id} or IMDB {imdb_id} or {filename} ({year}) and returning {return_full_tuple}.[/cyan]"
        )
        normalized_tvdb = self._normalized_external_id(tvdb_id, "tvdb_id")
        normalized_imdb = self._normalized_external_id(
            imdb_id, "imdb_id", imdb=True
        )
        if tvmaze_manual:
            return self._manual_tvmaze_value(
                tvmaze_manual,
                normalized_imdb,
                normalized_tvdb,
                return_full_tuple,
            )
        results = self._unique_search_results(
            await self._search_candidates(
                filename,
                normalized_imdb,
                normalized_tvdb,
                base_dir,
                config,
            )
        )
        if not results:
            logger.debug("[yellow]No TVMaze results found.[/yellow]")
            return self._search_return_value(
                0, normalized_imdb, normalized_tvdb, return_full_tuple
            )
        if manual_date is not None or len(results) > 1:
            tvmaze_id, normalized_tvdb = self._manual_show_selection(
                results, normalized_tvdb
            )
        else:
            tvmaze_id = self._automatic_show_selection(results[0])
        selected = self._show_by_id(results, tvmaze_id)
        normalized_imdb, normalized_tvdb = self._adopt_selected_externals(
            selected, normalized_imdb, normalized_tvdb
        )
        self._log_search_return(
            tvmaze_id, normalized_imdb, normalized_tvdb, return_full_tuple
        )
        return self._search_return_value(
            tvmaze_id, normalized_imdb, normalized_tvdb, return_full_tuple
        )

    @staticmethod
    def _cached_response(cached: Any) -> object:
        if is_cache_miss(cached):
            return _CACHE_MISS
        if isinstance(cached, dict | list):
            return cast(dict[str, Any] | list[dict[str, Any]], cached)
        return _CACHE_MISS

    @staticmethod
    def _dressed_response_list(data: list[Any]) -> list[dict[str, Any]]:
        return [
            cast(dict[str, Any], item)
            for item in data
            if isinstance(item, dict)
        ]

    @staticmethod
    async def _request_json(url: str, params: dict[str, Any]) -> Any:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, params=params, timeout=10)
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[ERROR] TVmaze API error: {error.response.status_code}",
                extra={"markup": False},
            )
            return None
        except httpx.RequestError as error:
            logger.info(
                f"[ERROR] Network error while accessing TVmaze: {error}",
                extra={"markup": False},
            )
            return None
        if response.status_code != 200:
            return None
        return response.json()

    @classmethod
    async def _cache_response_data(
        cls, cache: Any, cache_key: str, data: Any
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        if isinstance(data, dict):
            result = cast(dict[str, Any], data)
            await cache.set("tvmaze", "response", cache_key, result)
            return result
        if not isinstance(data, list):
            return None
        result = cls._dressed_response_list(cast(list[Any], data))
        await cache.set(
            "tvmaze",
            "response",
            cache_key,
            result,
            negative=not bool(result),
        )
        return result

    async def _make_tvmaze_request(
        self,
        url: str,
        params: dict[str, Any],
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Fetch one cached TVMaze request and dress its response."""
        cache_key = json.dumps(
            {"url": url, "params": params}, sort_keys=True, default=str
        )
        cache = cache_for(base_dir, config)
        cached = self._cached_response(
            await cache.get("tvmaze", "response", cache_key)
        )
        if cached is not _CACHE_MISS:
            return cast(dict[str, Any] | list[dict[str, Any]], cached)
        data = await self._request_json(url, params)
        return await self._cache_response_data(cache, cache_key, data)

    @staticmethod
    def _cached_show(cached: Any) -> object:
        if is_cache_miss(cached) or not isinstance(cached, dict):
            return _CACHE_MISS
        mapping = cast(dict[str, Any], cached)
        return {} if mapping.get("not_found") else mapping

    async def get_show_details(
        self,
        tvmaze_id: int,
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_key = str(tvmaze_id)
        cache = cache_for(base_dir, config)
        cached = self._cached_show(
            await cache.get("tvmaze", "show", cache_key)
        )
        if cached is not _CACHE_MISS:
            return cast(dict[str, Any], cached)
        data = await self._make_tvmaze_request(
            f"https://api.tvmaze.com/shows/{tvmaze_id}", {}, base_dir, config
        )
        if isinstance(data, dict) and data:
            await cache.set("tvmaze", "show", cache_key, data)
            return data
        await cache.set(
            "tvmaze", "show", cache_key, {"not_found": True}, negative=True
        )
        return {}

    @staticmethod
    def _cached_episode_date(cached: Any) -> object:
        if is_cache_miss(cached) or not isinstance(cached, dict):
            return _CACHE_MISS
        mapping = cast(dict[str, Any], cached)
        return {} if mapping.get("not_found") else mapping

    @staticmethod
    def _first_episode_by_date(
        response: dict[str, Any] | list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(response, list) or not response:
            return None
        first = cast(list[Any], response)[0]
        if not isinstance(first, dict):
            return None
        return cast(dict[str, Any], first)

    @staticmethod
    async def _cache_episode_date_not_found(
        cache: Any, cache_key: str
    ) -> None:
        await cache.set(
            "tvmaze",
            "episode-date",
            cache_key,
            {"not_found": True},
            negative=True,
        )

    @staticmethod
    def _mapping_or_empty(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return cast(dict[str, Any], value)

    @classmethod
    def _episode_show_link(cls, episode: dict[str, Any]) -> dict[str, Any]:
        links = cls._mapping_or_empty(episode.get("_links"))
        return cls._mapping_or_empty(links.get("show"))

    @staticmethod
    def _show_link_url(show_link: dict[str, Any], tvmaze_id: int) -> str:
        value = show_link.get("href")
        if isinstance(value, str) and value:
            return value
        return f"https://api.tvmaze.com/shows/{tvmaze_id}"

    async def _episode_show_data(
        self,
        episode: dict[str, Any],
        tvmaze_id: int,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        show_link = self._episode_show_link(episode)
        show_url = self._show_link_url(show_link, tvmaze_id)
        response = await self._make_tvmaze_request(
            show_url, {}, base_dir, config
        )
        show_data = (
            cast(dict[str, Any], response)
            if isinstance(response, dict)
            else {}
        )
        return show_link, show_data

    @staticmethod
    def _clean_html(value: object) -> str:
        return str(value or "").replace("<p>", "").replace("</p>", "").strip()

    @classmethod
    def _image_url(cls, value: Any) -> str:
        image = cls._mapping_or_empty(value)
        original = image.get("original")
        if original:
            return str(original)
        medium = image.get("medium")
        return "" if medium is None else str(medium)

    @staticmethod
    def _show_name(
        show_data: dict[str, Any], show_link: dict[str, Any]
    ) -> str:
        value = show_data.get("name")
        if value:
            return str(value)
        fallback = show_link.get("name")
        return "" if fallback is None else str(fallback)

    @staticmethod
    def _text_or_empty(value: Any) -> str:
        return "" if not value else str(value)

    @staticmethod
    def _int_or_zero(value: Any) -> int:
        return 0 if not value else int(value)

    @classmethod
    def _episode_date_result(
        cls,
        episode: dict[str, Any],
        show_link: dict[str, Any],
        show_data: dict[str, Any],
    ) -> dict[str, Any]:
        externals = cls._mapping_or_empty(show_data.get("externals"))
        result: dict[str, Any] = {
            "episode_name": cls._text_or_empty(episode.get("name")),
            "season": cls._int_or_zero(episode.get("season")),
            "episode": cls._int_or_zero(episode.get("number")),
            "airdate": cls._text_or_empty(episode.get("airdate")),
            "runtime": cls._int_or_zero(episode.get("runtime")),
            "episode_image": cls._image_url(episode.get("image")),
            "show_name": cls._show_name(show_data, show_link),
            "show_overview": cls._clean_html(show_data.get("summary")),
            "show_image": cls._image_url(show_data.get("image")),
            "tvdb_id": cls._int_or_zero(externals.get("thetvdb")),
            "imdb_id": cls._text_or_empty(externals.get("imdb")),
        }
        overview = cls._clean_html(episode.get("summary"))
        if overview:
            result["episode_overview"] = overview
        return result

    async def _fetch_episode_date_result(
        self,
        tvmaze_id: int,
        airdate: str,
        base_dir: str,
        config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        response = await self._make_tvmaze_request(
            f"https://api.tvmaze.com/shows/{tvmaze_id}/episodesbydate",
            {"date": airdate},
            base_dir,
            config,
        )
        episode = self._first_episode_by_date(response)
        if episode is None:
            return None
        show_link, show_data = await self._episode_show_data(
            episode, tvmaze_id, base_dir, config
        )
        return self._episode_date_result(episode, show_link, show_data)

    async def get_episode_by_date(
        self,
        tvmaze_id: int,
        airdate: str,
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_key = f"{tvmaze_id}:{airdate}"
        cache = cache_for(base_dir, config)
        cached = self._cached_episode_date(
            await cache.get("tvmaze", "episode-date", cache_key)
        )
        if cached is not _CACHE_MISS:
            return cast(dict[str, Any], cached)
        try:
            result = await self._fetch_episode_date_result(
                tvmaze_id, airdate, base_dir, config
            )
        except Exception as error:
            logger.info(f"[red]TVMaze date lookup failed: {error}[/red]")
            return {}
        if result is None:
            await self._cache_episode_date_not_found(cache, cache_key)
            return {}
        await cache.set("tvmaze", "episode-date", cache_key, result)
        return result

    @classmethod
    def _episode_number_show_link(cls, data: dict[str, Any]) -> dict[str, Any]:
        return cls._episode_show_link(data)

    @classmethod
    async def _episode_number_show_data(
        cls, client: httpx.AsyncClient, data: dict[str, Any]
    ) -> dict[str, Any]:
        show_link = cls._episode_number_show_link(data)
        show_url = show_link.get("href")
        if not isinstance(show_url, str) or not show_url:
            return {}
        show_response = await client.get(show_url, timeout=10.0)
        if show_response.status_code == 200:
            payload = show_response.json()
            return (
                cast(dict[str, Any], payload)
                if isinstance(payload, dict)
                else {}
            )
        return {"name": show_link.get("name", "")}

    @classmethod
    def _optional_image_values(cls, value: Any) -> tuple[Any, Any]:
        image = cls._mapping_or_empty(value)
        if not image:
            return None, None
        return image.get("original"), image.get("medium")

    @classmethod
    def _episode_number_series_name(
        cls, data: dict[str, Any], show_data: dict[str, Any]
    ) -> Any:
        show_link = cls._episode_number_show_link(data)
        return show_data.get("name", show_link.get("name", ""))

    @classmethod
    def _episode_number_result(
        cls,
        data: dict[str, Any],
        show_data: dict[str, Any],
        season: int,
        episode: int,
    ) -> dict[str, Any]:
        episode_image, episode_medium = cls._optional_image_values(
            data.get("image")
        )
        series_image, series_medium = cls._optional_image_values(
            show_data.get("image")
        )
        summary = data.get("summary", "")
        overview = cls._clean_html(summary) if summary else summary
        return {
            "episode_name": data.get("name", ""),
            "overview": overview,
            "season_number": data.get("season", season),
            "episode_number": data.get("number", episode),
            "air_date": data.get("airdate", ""),
            "runtime": data.get("runtime", 0),
            "series_name": cls._episode_number_series_name(data, show_data),
            "series_overview": cls._clean_html(show_data.get("summary", "")),
            "image": episode_image,
            "image_medium": episode_medium,
            "series_image": series_image,
            "series_image_medium": series_medium,
        }

    async def _fetch_episode_number_result(
        self, tvmaze_id: int, season: int, episode: int
    ) -> dict[str, Any] | None:
        url = f"https://api.tvmaze.com/shows/{tvmaze_id}/episodebynumber"
        params = {"season": season, "number": episode}
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload:
                logger.info(
                    f"[yellow]No episode data found for S{season:02d}E{episode:02d}[/yellow]"
                )
                return None
            data = cast(dict[str, Any], payload)
            show_data = await self._episode_number_show_data(client, data)
            return self._episode_number_result(
                data, show_data, season, episode
            )

    @staticmethod
    def _tvdb_episode_raw_entries(data: Any) -> list[Any]:
        if isinstance(data, list):
            return cast(list[Any], data)
        if not isinstance(data, dict):
            return []
        episodes = cast(dict[str, Any], data).get("episodes", [])
        return cast(list[Any], episodes) if isinstance(episodes, list) else []

    @classmethod
    def _tvdb_episode_entries(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            cast(dict[str, Any], item)
            for item in cls._tvdb_episode_raw_entries(meta.tvdb_episode_data)
            if isinstance(item, dict)
        ]

    @classmethod
    def _tvdb_episode_airdate(cls, meta: Meta) -> str | None:
        episode_id = meta.tvdb_episode_id
        for entry in cls._tvdb_episode_entries(meta):
            if entry.get("id") != episode_id:
                continue
            airdate = entry.get("aired")
            if isinstance(airdate, str):
                logger.debug(
                    f"[cyan]Found airdate from TVDB episode data: {airdate}[/cyan]"
                )
                return airdate
        if meta.debug:
            logger.info(
                f"[yellow]Could not find airdate for TVDB episode ID {episode_id}[/yellow]"
            )
        return None

    @classmethod
    def _episode_fallback_airdate(cls, meta: Meta) -> str | None:
        if meta.manual_date:
            value = (
                meta.manual_date if isinstance(meta.manual_date, str) else None
            )
            logger.debug(f"[cyan]Using manual_date: {value}[/cyan]")
            return value
        if meta.tvdb_episode_id and meta.tvdb_episode_data:
            return cls._tvdb_episode_airdate(meta)
        return None

    async def _fallback_episode_number_by_date(
        self, tvmaze_id: int, meta: Meta
    ) -> dict[str, Any] | None:
        airdate = self._episode_fallback_airdate(meta)
        if not airdate:
            logger.debug(
                "[yellow]No airdate available for fallback lookup[/yellow]"
            )
            return None
        logger.debug(
            f"[cyan]Attempting TVMaze lookup by date: {airdate}[/cyan]"
        )
        return await self.get_tvmaze_episode_data_by_date(tvmaze_id, airdate)

    async def _episode_http_error_fallback(
        self, error: httpx.HTTPStatusError, tvmaze_id: int, meta: Meta | None
    ) -> dict[str, Any] | None:
        if error.response.status_code != 404 or meta is None:
            return None
        logger.info(
            "[yellow]Episode not found using season/episode, trying date-based lookup...[/yellow]"
        )
        return await self._fallback_episode_number_by_date(tvmaze_id, meta)

    async def get_tvmaze_episode_data(
        self,
        tvmaze_id: int,
        season: int,
        episode: int,
        meta: Meta | None = None,
    ) -> dict[str, Any] | None:
        try:
            return await self._fetch_episode_number_result(
                tvmaze_id, season, episode
            )
        except httpx.HTTPStatusError as error:
            return await self._episode_http_error_fallback(
                error, tvmaze_id, meta
            )
        except httpx.RequestError as error:
            logger.info(f"[red]TVMaze Request error occurred: {error}[/red]")
            return None
        except Exception as error:
            logger.info(
                f"[red]TVMaze Error fetching TVMaze episode data: {error}[/red]"
            )
            return None

    async def _fetch_episode_data_by_date(
        self, tvmaze_id: int, airdate: str
    ) -> dict[str, Any] | None:
        url = f"https://api.tvmaze.com/shows/{tvmaze_id}/episodesbydate"
        params = {"date": airdate}
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            payload = response.json()
            data = self._first_episode_by_date(
                cast(list[dict[str, Any]], payload)
                if isinstance(payload, list)
                else None
            )
            if data is None:
                logger.info(
                    f"[yellow]No episode data found for date {airdate}[/yellow]"
                )
                return None
            show_data = await self._episode_number_show_data(client, data)
            return self._episode_number_result(data, show_data, 0, 0)

    async def get_tvmaze_episode_data_by_date(
        self, tvmaze_id: int, airdate: str
    ) -> dict[str, Any] | None:
        try:
            return await self._fetch_episode_data_by_date(tvmaze_id, airdate)
        except httpx.HTTPStatusError as error:
            logger.info(
                f"[red]TVMaze HTTP error occurred in episodesbydate: {error.response.status_code} - {error.response.text}[/red]"
            )
            return None
        except httpx.RequestError as error:
            logger.info(
                f"[red]TVMaze Request error occurred in episodesbydate: {error}[/red]"
            )
            return None
        except Exception as error:
            logger.info(
                f"[red]TVMaze Error fetching TVMaze episode data by date: {error}[/red]"
            )
            return None


tvmaze_manager = TvmazeManager()
