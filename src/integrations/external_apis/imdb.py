# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, cast

import anitopy
import cli_ui
import guessit
import httpx

from src.domain_models.errors import OperationAbortedError
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)

anitopy_parse_fn: Any = cast(Any, anitopy).parse
guessit_module: Any = cast(Any, guessit)
GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]
IMDB_GRAPHQL_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://www.imdb.com/",
}


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return cast(dict[str, Any], guessit_module.guessit(value, options))


class ImdbManager:
    def safe_get(self, data: Any, path: list[str], default: Any = None) -> Any:
        for key in path:
            if isinstance(data, dict):
                data_dict = cast(Mapping[str, Any], data)
                data = data_dict.get(key, default)
            else:
                return default
        return data

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return cast(Mapping[str, Any], value)

    @staticmethod
    def _mapping_text(mapping: Mapping[str, Any], key: str) -> str:
        value = mapping.get(key)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _credit_pair(name: str, person_id: str) -> tuple[str, str] | None:
        if not name:
            return None
        if not person_id:
            return None
        return name, person_id

    @classmethod
    def _credit_person(cls, name_obj: Any) -> tuple[str, str] | None:
        name = cls._mapping(name_obj)
        if name is None:
            return None
        name_text = cls._mapping(name.get("nameText"))
        if name_text is None:
            return None
        person_id = cls._mapping_text(name, "id")
        person_name = cls._mapping_text(name_text, "text")
        return cls._credit_pair(person_name, person_id)

    @classmethod
    def _credit_people(cls, credits: Any) -> tuple[list[str], list[str]]:
        if not isinstance(credits, list):
            return [], []
        names: list[str] = []
        ids: list[str] = []
        for raw_credit in cast(list[Any], credits):
            credit = cls._mapping(raw_credit)
            if credit is None:
                continue
            person = cls._credit_person(credit.get("name"))
            if person is None:
                continue
            name, person_id = person
            names.append(name)
            ids.append(person_id)
        return names, ids

    @classmethod
    def _mapping_items(cls, value: Any) -> list[Mapping[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            mapping
            for item in cast(list[Any], value)
            if (mapping := cls._mapping(item)) is not None
        ]

    @classmethod
    def _credit_category_text(cls, group: Mapping[str, Any]) -> str:
        category = cls._mapping(group.get("category"))
        if category is None:
            return ""
        return str(category.get("text") or "")

    @classmethod
    def _matching_credit_group(
        cls, groups: list[Mapping[str, Any]], category_keyword: str
    ) -> Mapping[str, Any] | None:
        for group in groups:
            if category_keyword in cls._credit_category_text(group):
                return group
        return None

    @classmethod
    def _credits_for_category(
        cls, title_data: Mapping[str, Any], category_keyword: str
    ) -> tuple[list[str], list[str]]:
        groups = cls._mapping_items(title_data.get("principalCredits", []))
        group = cls._matching_credit_group(groups, category_keyword)
        if group is None:
            return [], []
        return cls._credit_people(group.get("credits", []))

    @staticmethod
    def _title_request_identity(
        imdb_id: int | str | None,
    ) -> tuple[str | None, dict[str, Any]]:
        if not imdb_id or imdb_id == 0:
            return None, {"type": None}
        try:
            if str(imdb_id).startswith("tt"):
                return str(imdb_id), {}
            return f"tt{imdb_id:07d}", {}
        except Exception as error:
            logger.error(f"[red]Error:[/red] {error}")
            return None, {}

    @staticmethod
    def _title_cache_key(imdb_id: str, manual_language: Any) -> str:
        return f"{imdb_id}|{manual_language!s}"

    @staticmethod
    async def _cached_title_info(
        cache: Any, cache_key: str
    ) -> tuple[bool, dict[str, Any]]:
        cached = await cache.get("imdb", "title", cache_key)
        if is_cache_miss(cached) or not isinstance(cached, dict):
            return False, {}
        return True, cast(dict[str, Any], cached)

    @staticmethod
    def _title_query(imdb_id_str: str) -> dict[str, str]:
        return {
            "query": f"""
            query GetTitleInfo {{
                title(id: "{imdb_id_str}") {{
                id
                titleText {{
                    text
                    isOriginalTitle
                    country {{
                        text
                    }}
                }}
                originalTitleText {{
                    text
                }}
                releaseYear {{
                    year
                    endYear
                }}
                titleType {{
                    id
                }}
                plot {{
                    plotText {{
                    plainText
                    }}
                }}
                ratingsSummary {{
                    aggregateRating
                    voteCount
                }}
                primaryImage {{
                    url
                }}
                runtime {{
                    displayableProperty {{
                    value {{
                        plainText
                    }}
                    }}
                    seconds
                }}
                titleGenres {{
                    genres {{
                    genre {{
                        text
                    }}
                    }}
                }}
                principalCredits {{
                    category {{
                    text
                    id
                    }}
                    credits {{
                    name {{
                        id
                        nameText {{
                        text
                        }}
                    }}
                    }}
                }}
                episodes {{
                    episodes(first: 500) {{
                        edges {{
                            node {{
                                id
                                series {{
                                    displayableEpisodeNumber {{
                                        displayableSeason {{
                                            season
                                        }}
                                        episodeNumber {{
                                            text
                                        }}
                                    }}
                                }}
                                titleText {{
                                    text
                                }}
                                releaseYear {{
                                    year
                                }}
                                releaseDate {{
                                    year
                                    month
                                    day
                                }}
                            }}
                        }}
                        pageInfo {{
                            hasNextPage
                            hasPreviousPage
                        }}
                        total
                    }}
                }}
                runtimes(first: 10) {{
                    edges {{
                        node {{
                            id
                            seconds
                            displayableProperty {{
                                value {{
                                    plainText
                                }}
                            }}
                            attributes {{
                                text
                            }}
                        }}
                    }}
                }}
                technicalSpecifications {{
                    aspectRatios {{
                        items {{
                            aspectRatio
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    cameras {{
                        items {{
                            camera
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    colorations {{
                        items {{
                            text
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    laboratories {{
                        items {{
                            laboratory
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    negativeFormats {{
                        items {{
                            negativeFormat
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    printedFormats {{
                        items {{
                            printedFormat
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    processes {{
                        items {{
                            process
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    soundMixes {{
                        items {{
                            text
                            attributes {{
                                text
                            }}
                        }}
                    }}
                    filmLengths {{
                        items {{
                            filmLength
                            countries {{
                                text
                            }}
                            numReels
                        }}
                    }}
                }}
                akas(first: 100) {{
                edges {{
                    node {{
                    text
                    country {{
                        text
                    }}
                    language {{
                        text
                    }}
                    attributes {{
                        text
                    }}
                    }}
                }}
                }}
                countriesOfOrigin {{
                    countries {{
                        text
                        }}
                    }}
                }}
            }}
            """
        }

    @staticmethod
    async def _fetch_title_payload(
        query: dict[str, str],
    ) -> dict[str, Any] | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.graphql.imdb.com/",
                    json=query,
                    headers=IMDB_GRAPHQL_HEADERS,
                    timeout=10,
                )
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
            except httpx.HTTPStatusError as error:
                logger.info(
                    f"[red]IMDb API error: {error.response.status_code}[/red]"
                )
            except httpx.RequestError as error:
                logger.info(f"[red]IMDb API Network error: {error}[/red]")
        return None

    async def _resolved_title_data(
        self, data: dict[str, Any], cache: Any, cache_key: str
    ) -> Mapping[str, Any] | None:
        title_data = self.safe_get(data, ["data", "title"], {})
        title_mapping = self._mapping(title_data)
        if title_mapping:
            return title_mapping
        if not data.get("errors"):
            await cache.set("imdb", "title", cache_key, {}, negative=True)
        return None

    @classmethod
    def _country_names(cls, title_data: Mapping[str, Any]) -> list[str]:
        countries_obj = cls._mapping(title_data.get("countriesOfOrigin"))
        if countries_obj is None:
            return []
        countries = cls._mapping_items(countries_obj.get("countries", []))
        return [
            name
            for country in countries
            if (name := cls._mapping_text(country, "text"))
        ]

    @classmethod
    def _country_values(cls, title_data: Mapping[str, Any]) -> tuple[str, str]:
        names = cls._country_names(title_data)
        if not names:
            return "", ""
        return names[0], ", ".join(names)

    @staticmethod
    def _aka_value(title: Any, original_title: Any) -> Any:
        if original_title and original_title != title:
            return original_title
        return title

    @staticmethod
    def _runtime_value(seconds: Any) -> str:
        if not seconds:
            return "60"
        return str(int(seconds) // 60)

    def _genres_value(self, title_data: Mapping[str, Any]) -> str:
        raw = self.safe_get(title_data, ["titleGenres", "genres"], [])
        genres = self._mapping_items(raw)
        values = [
            self.safe_get(genre, ["genre", "text"], "") for genre in genres
        ]
        return ", ".join(str(value) for value in values if value)

    def _base_title_info(
        self, title_data: Mapping[str, Any], imdb_id: str
    ) -> dict[str, Any]:
        title = self.safe_get(title_data, ["titleText", "text"])
        original_title = self.safe_get(
            title_data, ["originalTitleText", "text"], ""
        )
        country, country_list = self._country_values(title_data)
        runtime_seconds = self.safe_get(title_data, ["runtime", "seconds"], 0)
        return {
            "imdbID": imdb_id,
            "imdb_url": f"https://www.imdb.com/title/{imdb_id}",
            "title": title,
            "country": country,
            "country_list": country_list,
            "year": self.safe_get(title_data, ["releaseYear", "year"]),
            "end_year": self.safe_get(title_data, ["releaseYear", "endYear"]),
            "aka": self._aka_value(title, original_title),
            "type": self.safe_get(title_data, ["titleType", "id"], None),
            "runtime": self._runtime_value(runtime_seconds),
            "cover": self.safe_get(title_data, ["primaryImage", "url"]),
            "plot": self.safe_get(
                title_data,
                ["plot", "plotText", "plainText"],
                "No plot available",
            ),
            "genres": self._genres_value(title_data),
            "rating": self.safe_get(
                title_data, ["ratingsSummary", "aggregateRating"], "N/A"
            ),
            "votes": self.safe_get(
                title_data, ["ratingsSummary", "voteCount"], 0
            ),
        }

    def _apply_title_credits(
        self, info: dict[str, Any], title_data: Mapping[str, Any]
    ) -> None:
        for prefix, keyword in (
            ("directors", "Direct"),
            ("creators", "Creat"),
            ("writers", "Writ"),
            ("stars", "Star"),
        ):
            names, ids = self._credits_for_category(title_data, keyword)
            info[prefix] = names
            info[f"{prefix}_id"] = ids

    @classmethod
    def _attribute_texts(cls, value: Any) -> list[str]:
        texts: list[str] = []
        for attribute in cls._mapping_items(value):
            text = cls._mapping_text(attribute, "text")
            if text:
                texts.append(text)
        return texts

    def _edition_entry(
        self, edge: Mapping[str, Any]
    ) -> tuple[str, str, dict[str, Any]] | None:
        node = self._mapping(self.safe_get(edge, ["node"], {}))
        if node is None:
            return None
        seconds = self.safe_get(node, ["seconds"], 0)
        display = self.safe_get(
            node, ["displayableProperty", "value", "plainText"], ""
        )
        if not seconds or not display:
            return None
        minutes = int(seconds) // 60
        attributes = self._attribute_texts(
            self.safe_get(node, ["attributes"], [])
        )
        label = f"{display} ({minutes} min)"
        if attributes:
            label += f" [{', '.join(attributes)}]"
        details: dict[str, Any] = {
            "display_name": display,
            "seconds": seconds,
            "minutes": minutes,
            "attributes": attributes,
        }
        return str(minutes), label, details

    def _apply_editions(
        self, info: dict[str, Any], title_data: Mapping[str, Any]
    ) -> None:
        edges = self._mapping_items(
            self.safe_get(title_data, ["runtimes", "edges"], [])
        )
        if not edges:
            return
        labels: list[str] = []
        details: dict[str, Any] = {}
        for edge in edges:
            entry = self._edition_entry(edge)
            if entry is None:
                continue
            key, label, detail = entry
            labels.append(label)
            details[key] = detail
        info["edition_details"] = details
        info["edition_count"] = len(labels)
        info["editions"] = ", ".join(labels)

    def _aka_entry(self, edge: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "title": self.safe_get(edge, ["node", "text"]),
            "country": self.safe_get(edge, ["node", "country", "text"]),
            "language": self.safe_get(edge, ["node", "language", "text"]),
            "attributes": self.safe_get(edge, ["node", "attributes"], []),
        }

    def _aka_entries(
        self, title_data: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        edges = self._mapping_items(
            self.safe_get(title_data, ["akas", "edges"], [])
        )
        return [self._aka_entry(edge) for edge in edges]

    def _episode_entry(self, edge: Mapping[str, Any]) -> dict[str, Any]:
        node = self.safe_get(edge, ["node"], {})
        displayable = self.safe_get(
            node, ["series", "displayableEpisodeNumber"], {}
        )
        season_info = self.safe_get(displayable, ["displayableSeason"], {})
        episode_info = self.safe_get(displayable, ["episodeNumber"], {})
        return {
            "id": self.safe_get(node, ["id"], ""),
            "title": self.safe_get(
                node, ["titleText", "text"], "Unknown Title"
            ),
            "release_year": self.safe_get(
                node, ["releaseYear", "year"], "Unknown Year"
            ),
            "release_date": {
                "year": self.safe_get(node, ["releaseDate", "year"], None),
                "month": self.safe_get(node, ["releaseDate", "month"], None),
                "day": self.safe_get(node, ["releaseDate", "day"], None),
            },
            "season": self.safe_get(season_info, ["season"], "unknown"),
            "episode_number": self.safe_get(episode_info, ["text"], ""),
        }

    def _episode_entries(
        self, title_data: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        episodes_data = self.safe_get(
            title_data, ["episodes", "episodes"], None
        )
        if not episodes_data:
            return []
        edges = self._mapping_items(
            self.safe_get(episodes_data, ["edges"], [])
        )
        return [self._episode_entry(edge) for edge in edges]

    @staticmethod
    def _season_year_pair(
        episode: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        season_value = episode.get("season", "unknown")
        release_year = episode.get("release_year")
        try:
            season = (
                int(season_value)
                if season_value not in ("unknown", "", None)
                else None
            )
        except TypeError, ValueError:
            return None
        if season is None or not isinstance(release_year, int):
            return None
        return season, release_year

    @classmethod
    def _season_years(
        cls, episodes: list[dict[str, Any]]
    ) -> dict[int, set[int]]:
        seasons: dict[int, set[int]] = {}
        for episode in episodes:
            pair = cls._season_year_pair(episode)
            if pair is None:
                continue
            season, year = pair
            seasons.setdefault(season, set()).add(year)
        return seasons

    @staticmethod
    def _season_summary_entry(season: int, years: list[int]) -> dict[str, Any]:
        year_range = str(years[0])
        if len(years) > 1:
            year_range = f"{years[0]}-{years[-1]}"
        return {"season": season, "year": years[0], "year_range": year_range}

    @classmethod
    def _seasons_summary(
        cls, episodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seasons = cls._season_years(episodes)
        return [
            cls._season_summary_entry(season, sorted(seasons[season]))
            for season in sorted(seasons)
        ]

    def _sound_mixes(self, title_data: Mapping[str, Any]) -> list[str]:
        raw = self.safe_get(
            title_data, ["technicalSpecifications", "soundMixes", "items"], []
        )
        mixes = self._mapping_items(raw)
        return [
            self._mapping_text(mix, "text") for mix in mixes if "text" in mix
        ]

    @staticmethod
    def _release_years(episodes: list[dict[str, Any]]) -> list[int]:
        return [
            int(value)
            for episode in episodes
            if isinstance((value := episode.get("release_year")), int)
        ]

    @classmethod
    def _tv_year(
        cls, end_year: Any, episodes: list[dict[str, Any]]
    ) -> int | None:
        if end_year:
            return int(end_year)
        years = cls._release_years(episodes)
        if not years:
            return None
        current_year = datetime.now(UTC).year
        return min(years, key=lambda year: abs(year - current_year))

    def _build_title_info(
        self,
        title_data: Mapping[str, Any],
        imdb_id: str,
        manual_language: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        info = self._base_title_info(title_data, imdb_id)
        self._apply_title_credits(info, title_data)
        self._apply_editions(info, title_data)
        info["akas"] = self._aka_entries(title_data)
        if manual_language:
            info["original_language"] = manual_language
        episodes = self._episode_entries(title_data)
        info["episodes"] = episodes
        info["seasons_summary"] = self._seasons_summary(episodes)
        info["sound_mixes"] = self._sound_mixes(title_data)
        info["tv_year"] = self._tv_year(info.get("end_year"), episodes)
        return info

    async def _title_info_result(
        self,
        data: dict[str, Any],
        imdb_id: str,
        manual_language: str | dict[str, Any] | None,
        cache: Any,
        cache_key: str,
    ) -> dict[str, Any]:
        title_data = await self._resolved_title_data(data, cache, cache_key)
        if title_data is None:
            return {}
        info = self._build_title_info(title_data, imdb_id, manual_language)
        logger.debug(
            f"[yellow]IMDb Response: {json.dumps(info, indent=2)[:1000]}...[/yellow]"
        )
        await cache.set("imdb", "title", cache_key, info)
        return info

    async def get_imdb_info_api(
        self,
        imdb_id: int | str | None,
        manual_language: str | dict[str, Any] | None = None,
        base_dir: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        imdb_id_str, empty_info = self._title_request_identity(imdb_id)
        if imdb_id_str is None:
            return empty_info
        cache = cache_for(base_dir, config)
        cache_key = self._title_cache_key(imdb_id_str, manual_language)
        found, cached = await self._cached_title_info(cache, cache_key)
        if found:
            return cached
        data = await self._fetch_title_payload(self._title_query(imdb_id_str))
        if data is None:
            return empty_info
        return await self._title_info_result(
            data, imdb_id_str, manual_language, cache, cache_key
        )

    async def search_imdb(
        self,
        filename: str,
        search_year: str | int | None,
        quickie: bool = False,
        category: str | None = None,
        secondary_title: str | None = None,
        _path: str | None = None,
        untouched_filename: str | None = None,
        attempted: int | None = 0,
        duration: str | int | None = None,
        unattended: bool = False,
    ) -> int:
        search_results: list[dict[str, Any]] = []
        imdb_id_result = imdb_id = 0
        if attempted is None:
            attempted = 0
        logger.debug(
            f"[yellow]Searching IMDb for {filename} and year {search_year}...[/yellow]"
        )
        if attempted:
            await asyncio.sleep(1)  # Whoa baby, slow down

        async def run_imdb_search(
            filename: str,
            search_year: str | int | None,
            category: str | None = None,
            attempted: int | None = 0,
            duration: str | int | None = None,
            wide_search: bool = False,
        ) -> list[dict[str, Any]]:
            search_results: list[dict[str, Any]] = []
            if attempted:
                await asyncio.sleep(1)  # Whoa baby, slow down
            url = "https://api.graphql.imdb.com/"
            if category == "MOVIE":
                filename = (
                    filename.replace("and", "&")
                    .replace("And", "&")
                    .replace("AND", "&")
                    .strip()
                )

            constraints_parts = [
                f"titleTextConstraint: {{searchTerm: {json.dumps(filename)}}}"
            ]

            # Add release date constraint if search_year is provided
            if not wide_search and search_year:
                search_year_int = int(search_year)
                start_year = search_year_int - 1
                end_year = search_year_int + 1
                constraints_parts.append(
                    f'releaseDateConstraint: {{releaseDateRange: {{start: "{start_year}-01-01", end: "{end_year}-12-31"}}}}'
                )

            if not wide_search and duration and isinstance(duration, int):
                duration = str(duration)
                start_duration = int(duration) - 10
                end_duration = int(duration) + 10
                constraints_parts.append(
                    f"runtimeConstraint: {{runtimeRangeMinutes: {{min: {start_duration}, max: {end_duration}}}}}"
                )

            constraints_string = ", ".join(constraints_parts)

            query = {
                "query": f"""
                    {{
                        advancedTitleSearch(
                            first: 10,
                            constraints: {{{constraints_string}}}
                        ) {{
                            total
                            edges {{
                                node {{
                                    title {{
                                        id
                                        titleText {{
                                            text
                                        }}
                                        titleType {{
                                            text
                                        }}
                                        releaseYear {{
                                            year
                                        }}
                                        plot {{
                                            plotText {{
                                            plainText
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                """
            }

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        json=query,
                        headers=IMDB_GRAPHQL_HEADERS,
                        timeout=10,
                    )
                    response.raise_for_status()
                    data = response.json()
            except Exception as e:
                logger.info(f"[red]IMDb GraphQL API error: {e}[/red]")
                return []

            results = cast(
                list[dict[str, Any]],
                self.safe_get(
                    data, ["data", "advancedTitleSearch", "edges"], []
                ),
            )
            search_results = results

            logger.debug(f"[yellow]Found {len(results)} results...[/yellow]")
            logger.debug(
                f"quickie: {quickie}, category: {category}, search_year: {search_year}"
            )
            return search_results

        if not search_results:
            result = await run_imdb_search(
                filename,
                search_year,
                category,
                attempted,
                duration,
                wide_search=False,
            )
            if result and len(result) > 0:
                search_results = result

        if not search_results and secondary_title:
            logger.debug(
                f"[yellow]Trying IMDb with secondary title: {secondary_title}[/yellow]"
            )
            result = await run_imdb_search(
                secondary_title,
                search_year,
                category,
                attempted,
                duration,
                wide_search=True,
            )
            if result and len(result) > 0:
                search_results = result

        # remove 'the' from the beginning of the title if it exists
        if not search_results:
            try:
                words = filename.split()
                bad_words = ["the"]
                words_lower = [word.lower() for word in words]

                if words_lower and words_lower[0] in bad_words:
                    words.pop(0)
                    words_lower.pop(0)
                    title = " ".join(words)
                    logger.debug(
                        f"[bold yellow]Trying IMDb with the prefix removed: {title}[/bold yellow]"
                    )
                    result = await run_imdb_search(
                        title,
                        search_year,
                        category,
                        attempted + 1,
                        wide_search=False,
                    )
                    if result and len(result) > 0:
                        search_results = result
            except Exception as e:
                logger.info(
                    f"[bold red]Reduced name search error:[/bold red] {e}"
                )
                search_results = []

        # relax the constraints
        if not search_results:
            logger.debug(
                "[yellow]No results found, trying with a wider search...[/yellow]"
            )
            try:
                result = await run_imdb_search(
                    filename,
                    search_year,
                    category,
                    attempted + 1,
                    wide_search=True,
                )
                if result and len(result) > 0:
                    search_results = result
            except Exception as e:
                logger.error(f"[red]Error during wide search: {e}[/red]")

        # Try parsed title (anitopy + guessit)
        if not search_results:
            try:
                parsed = guessit_fn(
                    untouched_filename or "",
                    {"excludes": ["country", "language"]},
                )
                parsed_title_data = cast(
                    dict[str, Any],
                    anitopy_parse_fn(parsed.get("title", "")) or {},
                )
                parsed_title = str(parsed_title_data.get("anime_title", ""))
                logger.debug(
                    f"[bold yellow]Trying IMDB with parsed title: {parsed_title}[/bold yellow]"
                )
                result = await run_imdb_search(
                    parsed_title,
                    search_year,
                    category,
                    attempted + 1,
                    wide_search=True,
                )
                if result and len(result) > 0:
                    search_results = result
            except Exception:
                logger.info(
                    "[bold red]Guessit failed parsing title, trying another method[/bold red]"
                )

        # Try with less words in the title
        if not search_results:
            try:
                words = filename.split()
                extensions = ["mp4", "mkv", "avi", "webm", "mov", "wmv"]
                words_lower = [word.lower() for word in words]

                for ext in extensions:
                    if ext in words_lower:
                        ext_index = words_lower.index(ext)
                        words.pop(ext_index)
                        words_lower.pop(ext_index)
                        break

                if len(words) > 1:
                    reduced_title = " ".join(words[:-1])
                    logger.debug(
                        f"[bold yellow]Trying IMDB with reduced name: {reduced_title}[/bold yellow]"
                    )
                    result = await run_imdb_search(
                        reduced_title,
                        search_year,
                        category,
                        attempted + 1,
                        wide_search=True,
                    )
                    if result and len(result) > 0:
                        search_results = result
            except Exception as e:
                logger.info(
                    f"[bold red]Reduced name search error:[/bold red] {e}"
                )

        # Try with even fewer words
        if not search_results:
            try:
                words = filename.split()
                extensions = ["mp4", "mkv", "avi", "webm", "mov", "wmv"]
                words_lower = [word.lower() for word in words]

                for ext in extensions:
                    if ext in words_lower:
                        ext_index = words_lower.index(ext)
                        words.pop(ext_index)
                        words_lower.pop(ext_index)
                        break

                if len(words) > 2:
                    further_reduced_title = " ".join(words[:-2])
                    logger.debug(
                        f"[bold yellow]Trying IMDB with further reduced name: {further_reduced_title}[/bold yellow]"
                    )
                    result = await run_imdb_search(
                        further_reduced_title,
                        search_year,
                        category,
                        attempted + 1,
                        wide_search=True,
                    )
                    if result and len(result) > 0:
                        search_results = result
            except Exception as e:
                logger.info(
                    f"[bold red]Further reduced name search error:[/bold red] {e}"
                )

        if quickie:
            if search_results:
                first_result = search_results[0]
                logger.debug(
                    f"[cyan]Quickie search result: {first_result}[/cyan]"
                )
                node = self.safe_get(first_result, ["node"], {})
                title = self.safe_get(node, ["title"], {})
                type_info = self.safe_get(title, ["titleType"], {})
                year = self.safe_get(title, ["releaseYear", "year"], None)
                imdb_id = self.safe_get(title, ["id"], "")
                year_int = int(year) if year else None
                search_year_int = int(search_year) if search_year else None

                type_matches = False
                if type_info:
                    title_type = type_info.get("text", "").lower()
                    is_tv = bool(
                        category
                        and category.lower() == "tv"
                        and "tv series" in title_type
                    )
                    is_movie = bool(
                        category
                        and category.lower() == "movie"
                        and "tv series" not in title_type
                    )
                    type_matches = is_tv or is_movie

                if imdb_id and type_matches:
                    if year_int and search_year_int:
                        if year_int == search_year_int:
                            return int(imdb_id.replace("tt", "").strip())
                        logger.debug(
                            f"[yellow]Year mismatch: found {year_int}, expected {search_year_int}[/yellow]"
                        )
                        return 0
                    return int(imdb_id.replace("tt", "").strip())
                if not imdb_id:
                    logger.debug(
                        "[yellow]No IMDb ID found in quickie result[/yellow]"
                    )
                if not type_matches:
                    logger.debug(
                        f"[yellow]Type mismatch: found {type_info.get('text', '')}, expected {category}[/yellow]"
                    )
                imdb_id_result = 0

            return imdb_id_result if imdb_id_result else 0

        if len(search_results) == 1:
            imdb_id = self.safe_get(
                search_results[0], ["node", "title", "id"], ""
            )
            if imdb_id:
                return int(imdb_id.replace("tt", "").strip())
        elif len(search_results) > 1:
            # Calculate similarity for all results
            results_with_similarity: list[tuple[dict[str, Any], float]] = []
            filename_norm = filename.lower().strip()
            search_year_int = int(search_year) if search_year else 0

            for r in search_results:
                node = self.safe_get(r, ["node"], {})
                title = self.safe_get(node, ["title"], {})
                title_text = self.safe_get(title, ["titleText", "text"], "")
                result_year = self.safe_get(title, ["releaseYear", "year"], 0)

                similarity = SequenceMatcher(
                    None, filename_norm, title_text.lower().strip()
                ).ratio()

                # Only boost similarity if titles are very similar (>= 0.99) AND years match
                if (
                    similarity >= 0.99
                    and search_year_int > 0
                    and result_year > 0
                ):
                    if result_year == search_year_int:
                        similarity += 0.1  # Full boost for exact year match
                    elif result_year == search_year_int - 1:
                        similarity += 0.05  # Half boost for -1 year

                results_with_similarity.append((r, similarity))

            # Sort by similarity (highest first)
            results_with_similarity.sort(key=lambda x: x[1], reverse=True)

            # Filter results: if we have high similarity matches (>= 0.90), hide low similarity ones (< 0.75)
            best_similarity = results_with_similarity[0][1]
            if best_similarity >= 0.90:
                filtered_results_with_similarity: list[
                    tuple[dict[str, Any], float]
                ] = [
                    (result, sim)
                    for result, sim in results_with_similarity
                    if sim >= 0.75
                ]
                results_with_similarity = filtered_results_with_similarity

                logger.debug(
                    f"[yellow]Filtered out low similarity results (< 0.70) since best match has {best_similarity:.2f} similarity[/yellow]"
                )

            sorted_results: list[dict[str, Any]] = [
                r[0] for r in results_with_similarity
            ]

            # Check if the best match is significantly better than others
            best_similarity = results_with_similarity[0][1]
            similarity_threshold = 0.85

            if best_similarity >= similarity_threshold:
                second_best = (
                    results_with_similarity[1][1]
                    if len(results_with_similarity) > 1
                    else 0.0
                )

                if best_similarity - second_best >= 0.10:
                    logger.debug(
                        f"[green]Auto-selecting best match: {self.safe_get(sorted_results[0], ['node', 'title', 'titleText', 'text'], '')} (similarity: {best_similarity:.2f})[/green]"
                    )
                    imdb_id = self.safe_get(
                        sorted_results[0], ["node", "title", "id"], ""
                    )
                    if imdb_id:
                        return int(imdb_id.replace("tt", "").strip())

            if unattended:
                imdb_id = self.safe_get(
                    sorted_results[0], ["node", "title", "id"], ""
                )
                if imdb_id:
                    imdb_id_result = int(imdb_id.replace("tt", "").strip())
                    logger.debug(
                        f"[green]Unattended mode: auto-selected IMDb ID {imdb_id_result}[/green]"
                    )
                    return imdb_id_result

            # Show sorted results to user
            logger.info(
                "[bold yellow]Multiple IMDb results found. Please select the correct entry:[/bold yellow]"
            )

            for idx, candidate in enumerate(sorted_results):
                node = self.safe_get(candidate, ["node"], {})
                title = self.safe_get(node, ["title"], {})
                title_text = self.safe_get(title, ["titleText", "text"], "")
                year = self.safe_get(title, ["releaseYear", "year"], None)
                imdb_id = self.safe_get(title, ["id"], "")
                title_type = self.safe_get(title, ["titleType", "text"], "")
                plot = self.safe_get(
                    title, ["plot", "plotText", "plainText"], ""
                )
                similarity_score = results_with_similarity[idx][1]

                logger.info(
                    f"[cyan]{idx + 1}.[/cyan] [bold]{title_text}[/bold] ({year}) [yellow]ID:[/yellow] {imdb_id} [yellow]Type:[/yellow] {title_type} [dim](similarity: {similarity_score:.2f})[/dim]"
                )
                if plot:
                    logger.info(
                        f"[green]Plot:[/green] {plot[:200]}{'...' if len(plot) > 200 else ''}"
                    )
                logger.info("")

            if sorted_results:
                selection = None
                while True:
                    try:
                        selection = (
                            await prompt_in_thread(
                                cli_ui.ask_string,
                                "Enter the number of the correct entry, 0 for none, or manual IMDb ID (tt1234567): ",
                            )
                            or ""
                        )
                    except EOFError, KeyboardInterrupt:
                        logger.info(
                            "\n[red]Exiting on user request (Ctrl+C)[/red]"
                        )
                        await cleanup_manager.cleanup()
                        cleanup_manager.reset_terminal()
                        raise OperationAbortedError(
                            "IMDb selection was cancelled by the user."
                        ) from None
                    try:
                        # Check if it's a manual IMDb ID entry
                        if (
                            selection.lower().startswith("tt")
                            and len(selection) >= 3
                        ):
                            try:
                                manual_imdb_id = (
                                    selection.lower().replace("tt", "").strip()
                                )
                                if manual_imdb_id.isdigit():
                                    logger.info(
                                        f"[green]Using manual IMDb ID: {selection}[/green]"
                                    )
                                    return int(manual_imdb_id)
                                logger.info(
                                    "[bold red]Invalid IMDb ID format. Please try again.[/bold red]"
                                )
                                continue
                            except Exception as e:
                                logger.info(
                                    f"[bold red]Error parsing IMDb ID: {e}. Please try again.[/bold red]"
                                )
                                continue

                        # Handle numeric selection
                        selection_int = int(selection)
                        if 1 <= selection_int <= len(sorted_results):
                            selected = sorted_results[selection_int - 1]
                            imdb_id = self.safe_get(
                                selected, ["node", "title", "id"], ""
                            )
                            if imdb_id:
                                return int(imdb_id.replace("tt", "").strip())
                        elif selection_int == 0:
                            logger.info("[bold red]Skipping IMDb[/bold red]")
                            return 0
                        else:
                            logger.info(
                                "[bold red]Selection out of range. Please try again.[/bold red]"
                            )
                    except ValueError:
                        logger.info(
                            "[bold red]Invalid input. Please enter a number or IMDb ID (tt1234567).[/bold red]"
                        )

        else:
            if not unattended:
                try:
                    selection = (
                        await prompt_in_thread(
                            cli_ui.ask_string,
                            "No results found. Please enter a manual IMDb ID (tt1234567) or 0 to skip: ",
                        )
                        or ""
                    )
                except EOFError, KeyboardInterrupt:
                    logger.info(
                        "\n[red]Exiting on user request (Ctrl+C)[/red]"
                    )
                    await cleanup_manager.cleanup()
                    cleanup_manager.reset_terminal()
                    raise OperationAbortedError(
                        "IMDb selection was cancelled by the user."
                    ) from None
                if selection.lower().startswith("tt") and len(selection) >= 3:
                    try:
                        manual_imdb_id = (
                            selection.lower().replace("tt", "").strip()
                        )
                        if manual_imdb_id.isdigit():
                            logger.info(
                                f"[green]Using manual IMDb ID: {selection}[/green]"
                            )
                            return int(manual_imdb_id)
                        logger.info(
                            "[bold red]Invalid IMDb ID format. Please try again.[/bold red]"
                        )
                    except Exception as e:
                        logger.info(
                            f"[bold red]Error parsing IMDb ID: {e}. Please try again.[/bold red]"
                        )
            else:
                logger.info(
                    "[bold red]No IMDb results found in unattended mode. Skipping IMDb.[/bold red]"
                )

        return imdb_id_result if imdb_id_result else 0

    @staticmethod
    def _episode_imdb_id(imdb_id: int | str) -> str:
        value = str(imdb_id)
        if value.startswith("tt"):
            return value
        try:
            return f"tt{int(imdb_id):07d}"
        except Exception:
            return f"tt{value.zfill(7)}"

    @staticmethod
    def _episode_query(imdb_id: str) -> dict[str, str]:
        return {
            "query": f"""
                {{
                    title(id: "{imdb_id}") {{
                        id
                        titleText {{ text }}
                        series {{
                            displayableEpisodeNumber {{
                                displayableSeason {{ id season text }}
                                episodeNumber {{ id text }}
                            }}
                            nextEpisode {{ id titleText {{ text }} }}
                            previousEpisode {{ id titleText {{ text }} }}
                            series {{ id titleText {{ text }} }}
                        }}
                    }}
                }}
            """
        }

    @staticmethod
    async def _episode_api_data(query: dict[str, str]) -> Any | None:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.graphql.imdb.com/",
                    json=query,
                    headers=IMDB_GRAPHQL_HEADERS,
                    timeout=10,
                )
                response.raise_for_status()
                return response.json()
            except Exception as error:
                logger.debug(f"[red]IMDb API error: {error}[/red]")
                return None

    def _episode_neighbor(self, series_info: Any, key: str) -> dict[str, Any]:
        episode = self.safe_get(series_info, [key], {})
        return {
            "id": self.safe_get(episode, ["id"]),
            "title": self.safe_get(episode, ["titleText", "text"]),
        }

    def _episode_series_details(self, series_info: Any) -> dict[str, Any]:
        displayable = self.safe_get(
            series_info, ["displayableEpisodeNumber"], {}
        )
        season_info = self.safe_get(displayable, ["displayableSeason"], {})
        episode_info = self.safe_get(displayable, ["episodeNumber"], {})
        series_obj = self.safe_get(series_info, ["series"], {})
        return {
            "season_id": self.safe_get(season_info, ["id"]),
            "season": self.safe_get(season_info, ["season"]),
            "season_text": self.safe_get(season_info, ["text"]),
            "episode_id": self.safe_get(episode_info, ["id"]),
            "episode_text": self.safe_get(episode_info, ["text"]),
            "series_id": self.safe_get(series_obj, ["id"]),
            "series_title": self.safe_get(series_obj, ["titleText", "text"]),
        }

    def _episode_result(self, title_data: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.safe_get(title_data, ["id"]),
            "title": self.safe_get(title_data, ["titleText", "text"]),
            "series": {},
            "next_episode": {},
            "previous_episode": {},
        }
        series_info = self.safe_get(title_data, ["series"], {})
        if not series_info:
            return result
        result["series"] = self._episode_series_details(series_info)
        result["next_episode"] = self._episode_neighbor(
            series_info, "nextEpisode"
        )
        result["previous_episode"] = self._episode_neighbor(
            series_info, "previousEpisode"
        )
        return result

    async def get_imdb_from_episode(
        self, imdb_id: int | str
    ) -> dict[str, Any] | None:
        if not imdb_id or imdb_id == 0:
            return None
        normalized_id = self._episode_imdb_id(imdb_id)
        data = await self._episode_api_data(self._episode_query(normalized_id))
        if data is None:
            return None
        title_data = self.safe_get(data, ["data", "title"], {})
        if not title_data:
            return None
        return self._episode_result(title_data)


imdb_manager = ImdbManager()
