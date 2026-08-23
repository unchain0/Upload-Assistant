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

    async def get_episode_by_date(
        self,
        tvmaze_id: int,
        airdate: str,
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_key = f"{tvmaze_id}:{airdate}"
        cache = cache_for(base_dir, config)
        cached = await cache.get("tvmaze", "episode-date", cache_key)
        if not is_cache_miss(cached) and isinstance(cached, dict):
            return (
                {} if cached.get("not_found") else cast(dict[str, Any], cached)
            )

        try:
            response = await self._make_tvmaze_request(
                f"https://api.tvmaze.com/shows/{tvmaze_id}/episodesbydate",
                {"date": airdate},
                base_dir,
                config,
            )
            if (
                not isinstance(response, list)
                or not response
                or not isinstance(response[0], dict)
            ):
                await cache.set(
                    "tvmaze",
                    "episode-date",
                    cache_key,
                    {"not_found": True},
                    negative=True,
                )
                return {}
            episode = response[0]
            links = (
                episode.get("_links")
                if isinstance(episode.get("_links"), dict)
                else {}
            )
            show_link = (
                links.get("show")
                if isinstance(links, dict)
                and isinstance(links.get("show"), dict)
                else {}
            )
            show_url = (
                show_link.get("href") if isinstance(show_link, dict) else None
            )
            if not isinstance(show_url, str) or not show_url:
                show_url = f"https://api.tvmaze.com/shows/{tvmaze_id}"
            show_data: dict[str, Any] = {}
            show_response = await self._make_tvmaze_request(
                show_url, {}, base_dir, config
            )
            if isinstance(show_response, dict):
                show_data = show_response

            def clean_html(value: object) -> str:
                return (
                    str(value or "")
                    .replace("<p>", "")
                    .replace("</p>", "")
                    .strip()
                )

            episode_image_value = episode.get("image")
            show_image_value = show_data.get("image")
            externals_value = show_data.get("externals")
            episode_image_data: dict[str, Any] = (
                episode_image_value
                if isinstance(episode_image_value, dict)
                else {}
            )
            show_image_data: dict[str, Any] = (
                show_image_value if isinstance(show_image_value, dict) else {}
            )
            externals: dict[str, Any] = (
                externals_value if isinstance(externals_value, dict) else {}
            )
            result: dict[str, Any] = {
                "episode_name": str(episode.get("name") or ""),
                "season": int(episode.get("season") or 0),
                "episode": int(episode.get("number") or 0),
                "airdate": str(episode.get("airdate") or ""),
                "runtime": int(episode.get("runtime") or 0),
                "episode_image": str(
                    episode_image_data.get("original")
                    or episode_image_data.get("medium")
                    or ""
                ),
                "show_name": str(
                    show_data.get("name")
                    or (
                        show_link.get("name")
                        if isinstance(show_link, dict)
                        else ""
                    )
                    or ""
                ),
                "show_overview": clean_html(show_data.get("summary")),
                "show_image": str(
                    show_image_data.get("original")
                    or show_image_data.get("medium")
                    or ""
                ),
                "tvdb_id": int(externals.get("thetvdb") or 0),
                "imdb_id": str(externals.get("imdb") or ""),
            }
            overview = clean_html(episode.get("summary"))
            if overview:
                result["episode_overview"] = overview
            await cache.set("tvmaze", "episode-date", cache_key, result)
            return result
        except Exception as error:
            logger.info(f"[red]TVMaze date lookup failed: {error}[/red]")
            return {}

    async def get_tvmaze_episode_data(
        self,
        tvmaze_id: int,
        season: int,
        episode: int,
        meta: Meta | None = None,
    ) -> dict[str, Any] | None:
        url = f"https://api.tvmaze.com/shows/{tvmaze_id}/episodebynumber"
        params = {"season": season, "number": episode}

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                if data:
                    # Get show data for additional information
                    show_data: dict[str, Any] = {}
                    if (
                        "show" in data.get("_links", {})
                        and "href" in data["_links"]["show"]
                    ):
                        show_url = data["_links"]["show"]["href"]
                        show_name = data["_links"]["show"].get("name", "")

                        show_response = await client.get(
                            show_url, timeout=10.0
                        )
                        show_data = (
                            show_response.json()
                            if show_response.status_code == 200
                            else {"name": show_name}
                        )

                    # Clean HTML tags from summary
                    summary = data.get("summary", "")
                    if summary:
                        summary = (
                            summary.replace("<p>", "")
                            .replace("</p>", "")
                            .strip()
                        )

                    # Format the response in a consistent structure
                    return {
                        "episode_name": data.get("name", ""),
                        "overview": summary,
                        "season_number": data.get("season", season),
                        "episode_number": data.get("number", episode),
                        "air_date": data.get("airdate", ""),
                        "runtime": data.get("runtime", 0),
                        "series_name": show_data.get(
                            "name",
                            data.get("_links", {})
                            .get("show", {})
                            .get("name", ""),
                        ),
                        "series_overview": show_data.get("summary", "")
                        .replace("<p>", "")
                        .replace("</p>", "")
                        .strip(),
                        "image": data.get("image", {}).get("original", None)
                        if data.get("image")
                        else None,
                        "image_medium": data.get("image", {}).get(
                            "medium", None
                        )
                        if data.get("image")
                        else None,
                        "series_image": show_data.get("image", {}).get(
                            "original", None
                        )
                        if show_data.get("image")
                        else None,
                        "series_image_medium": show_data.get("image", {}).get(
                            "medium", None
                        )
                        if show_data.get("image")
                        else None,
                    }

                logger.info(
                    f"[yellow]No episode data found for S{season:02d}E{episode:02d}[/yellow]"
                )
                return None

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and meta is not None:
                logger.info(
                    "[yellow]Episode not found using season/episode, trying date-based lookup...[/yellow]"
                )

                # Try to get airdate from meta data
                airdate = None

                # First priority: manual_date
                if meta and meta.manual_date:
                    manual_date = meta.manual_date
                    if isinstance(manual_date, str):
                        airdate = manual_date
                    logger.debug(f"[cyan]Using manual_date: {airdate}[/cyan]")

                # Second priority: find airdate from tvdb_episode_data using tvdb_episode_id
                elif meta and meta.tvdb_episode_id and meta.tvdb_episode_data:
                    tvdb_episode_id = meta.tvdb_episode_id
                    tvdb_data = meta.tvdb_episode_data

                    episodes: list[dict[str, Any]] = []
                    if isinstance(tvdb_data, dict):
                        tvdb_data_dict = tvdb_data
                        tvdb_episodes_raw = tvdb_data_dict.get("episodes", [])
                        if isinstance(tvdb_episodes_raw, list):
                            episodes = list(
                                cast(list[dict[str, Any]], tvdb_episodes_raw)
                            )
                    elif isinstance(tvdb_data, list):
                        episodes = list(cast(list[dict[str, Any]], tvdb_data))

                    for ep in episodes:
                        if ep.get("id") == tvdb_episode_id:
                            ep_airdate = ep.get("aired")
                            if isinstance(ep_airdate, str):
                                airdate = ep_airdate
                                logger.debug(
                                    f"[cyan]Found airdate from TVDB episode data: {airdate}[/cyan]"
                                )
                                break

                    if not airdate and meta.debug:
                        logger.info(
                            f"[yellow]Could not find airdate for TVDB episode ID {tvdb_episode_id}[/yellow]"
                        )

                # Try date-based lookup if we have an airdate
                if isinstance(airdate, str) and airdate:
                    logger.debug(
                        f"[cyan]Attempting TVMaze lookup by date: {airdate}[/cyan]"
                    )
                    return await self.get_tvmaze_episode_data_by_date(
                        tvmaze_id, airdate
                    )
                logger.debug(
                    "[yellow]No airdate available for fallback lookup[/yellow]"
                )
                return None
            return None
        except httpx.RequestError as e:
            logger.info(f"[red]TVMaze Request error occurred: {e}[/red]")
            return None
        except Exception as e:
            logger.info(
                f"[red]TVMaze Error fetching TVMaze episode data: {e}[/red]"
            )
            return None

    async def get_tvmaze_episode_data_by_date(
        self, tvmaze_id: int, airdate: str
    ) -> dict[str, Any] | None:
        url = f"https://api.tvmaze.com/shows/{tvmaze_id}/episodesbydate"
        params = {"date": airdate}

        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                if data and len(data) > 0:
                    # Take the first episode from the date (in case multiple episodes aired on same date)
                    episode_data = data[0]

                    # Get show data for additional information
                    show_data: dict[str, Any] = {}
                    if (
                        "show" in episode_data.get("_links", {})
                        and "href" in episode_data["_links"]["show"]
                    ):
                        show_url = episode_data["_links"]["show"]["href"]
                        show_name = episode_data["_links"]["show"].get(
                            "name", ""
                        )

                        show_response = await client.get(
                            show_url, timeout=10.0
                        )
                        show_data = (
                            show_response.json()
                            if show_response.status_code == 200
                            else {"name": show_name}
                        )

                    # Clean HTML tags from summary
                    summary = episode_data.get("summary", "")
                    if summary:
                        summary = (
                            summary.replace("<p>", "")
                            .replace("</p>", "")
                            .strip()
                        )

                    # Format the response in a consistent structure
                    return {
                        "episode_name": episode_data.get("name", ""),
                        "overview": summary,
                        "season_number": episode_data.get("season", 0),
                        "episode_number": episode_data.get("number", 0),
                        "air_date": episode_data.get("airdate", ""),
                        "runtime": episode_data.get("runtime", 0),
                        "series_name": show_data.get(
                            "name",
                            episode_data.get("_links", {})
                            .get("show", {})
                            .get("name", ""),
                        ),
                        "series_overview": show_data.get("summary", "")
                        .replace("<p>", "")
                        .replace("</p>", "")
                        .strip(),
                        "image": episode_data.get("image", {}).get(
                            "original", None
                        )
                        if episode_data.get("image")
                        else None,
                        "image_medium": episode_data.get("image", {}).get(
                            "medium", None
                        )
                        if episode_data.get("image")
                        else None,
                        "series_image": show_data.get("image", {}).get(
                            "original", None
                        )
                        if show_data.get("image")
                        else None,
                        "series_image_medium": show_data.get("image", {}).get(
                            "medium", None
                        )
                        if show_data.get("image")
                        else None,
                    }

                logger.info(
                    f"[yellow]No episode data found for date {airdate}[/yellow]"
                )
                return None

        except httpx.HTTPStatusError as e:
            logger.info(
                f"[red]TVMaze HTTP error occurred in episodesbydate: {e.response.status_code} - {e.response.text}[/red]"
            )
            return None
        except httpx.RequestError as e:
            logger.info(
                f"[red]TVMaze Request error occurred in episodesbydate: {e}[/red]"
            )
            return None
        except Exception as e:
            logger.info(
                f"[red]TVMaze Error fetching TVMaze episode data by date: {e}[/red]"
            )
            return None


tvmaze_manager = TvmazeManager()
