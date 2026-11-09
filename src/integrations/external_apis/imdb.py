# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _SearchContext:
    filename: str
    search_year: str | int | None
    quickie: bool
    category: str | None
    secondary_title: str | None
    untouched_filename: str | None
    attempted: int
    duration: str | int | None
    unattended: bool


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

    @staticmethod
    def _search_attempted(attempted: int | None) -> int:
        return 0 if attempted is None else attempted

    @staticmethod
    async def _search_delay(attempted: int) -> None:
        if attempted:
            await asyncio.sleep(1)

    @staticmethod
    def _movie_search_title(filename: str, category: str | None) -> str:
        if category != "MOVIE":
            return filename
        return (
            filename.replace("and", "&")
            .replace("And", "&")
            .replace("AND", "&")
            .strip()
        )

    @staticmethod
    def _release_date_constraint(
        search_year: str | int | None, wide_search: bool
    ) -> str | None:
        if wide_search or not search_year:
            return None
        year = int(search_year)
        return (
            "releaseDateConstraint: {releaseDateRange: "
            f'{{start: "{year - 1}-01-01", end: "{year + 1}-12-31"}}}}'
        )

    @staticmethod
    def _runtime_constraint(
        duration: str | int | None, wide_search: bool
    ) -> str | None:
        if wide_search or not isinstance(duration, int):
            return None
        return (
            "runtimeConstraint: {runtimeRangeMinutes: "
            f"{{min: {duration - 10}, max: {duration + 10}}}}}"
        )

    @classmethod
    def _search_constraints(
        cls,
        filename: str,
        search_year: str | int | None,
        duration: str | int | None,
        wide_search: bool,
    ) -> str:
        parts = [
            f"titleTextConstraint: {{searchTerm: {json.dumps(filename)}}}"
        ]
        parts.extend(
            constraint
            for constraint in (
                cls._release_date_constraint(search_year, wide_search),
                cls._runtime_constraint(duration, wide_search),
            )
            if constraint is not None
        )
        return ", ".join(parts)

    @staticmethod
    def _advanced_search_query(constraints: str) -> dict[str, str]:
        return {
            "query": f"""
                {{
                    advancedTitleSearch(
                        first: 10,
                        constraints: {{{constraints}}}
                    ) {{
                        total
                        edges {{
                            node {{
                                title {{
                                    id
                                    titleText {{ text }}
                                    titleType {{ text }}
                                    releaseYear {{ year }}
                                    plot {{ plotText {{ plainText }} }}
                                }}
                            }}
                        }}
                    }}
                }}
            """
        }

    async def _fetch_search_results(
        self, query: dict[str, str]
    ) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.graphql.imdb.com/",
                    json=query,
                    headers=IMDB_GRAPHQL_HEADERS,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as error:
            logger.info(f"[red]IMDb GraphQL API error: {error}[/red]")
            return []
        raw = self.safe_get(data, ["data", "advancedTitleSearch", "edges"], [])
        if not isinstance(raw, list):
            return []
        return [
            cast(dict[str, Any], item)
            for item in cast(list[Any], raw)
            if isinstance(item, dict)
        ]

    async def _run_imdb_search(
        self,
        filename: str,
        search_year: str | int | None,
        category: str | None,
        attempted: int,
        duration: str | int | None = None,
        *,
        wide_search: bool,
        quickie: bool,
    ) -> list[dict[str, Any]]:
        await self._search_delay(attempted)
        title = self._movie_search_title(filename, category)
        constraints = self._search_constraints(
            title, search_year, duration, wide_search
        )
        results = await self._fetch_search_results(
            self._advanced_search_query(constraints)
        )
        logger.debug(f"[yellow]Found {len(results)} results...[/yellow]")
        logger.debug(
            f"quickie: {quickie}, category: {category}, search_year: {search_year}"
        )
        return results

    async def _attempt_primary(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        return await self._run_imdb_search(
            context.filename,
            context.search_year,
            context.category,
            context.attempted,
            context.duration,
            wide_search=False,
            quickie=context.quickie,
        )

    async def _attempt_secondary(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        if not context.secondary_title:
            return []
        logger.debug(
            f"[yellow]Trying IMDb with secondary title: {context.secondary_title}[/yellow]"
        )
        return await self._run_imdb_search(
            context.secondary_title,
            context.search_year,
            context.category,
            context.attempted,
            context.duration,
            wide_search=True,
            quickie=context.quickie,
        )

    @staticmethod
    def _without_leading_the(filename: str) -> str | None:
        words = filename.split()
        if not words:
            return None
        if words[0].lower() != "the":
            return None
        return " ".join(words[1:])

    async def _attempt_prefix(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        try:
            title = self._without_leading_the(context.filename)
            if title is None:
                return []
            logger.debug(
                f"[bold yellow]Trying IMDb with the prefix removed: {title}[/bold yellow]"
            )
            return await self._run_imdb_search(
                title,
                context.search_year,
                context.category,
                context.attempted + 1,
                wide_search=False,
                quickie=context.quickie,
            )
        except Exception as error:
            logger.info(
                f"[bold red]Reduced name search error:[/bold red] {error}"
            )
            return []

    async def _attempt_wide(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        logger.debug(
            "[yellow]No results found, trying with a wider search...[/yellow]"
        )
        try:
            return await self._run_imdb_search(
                context.filename,
                context.search_year,
                context.category,
                context.attempted + 1,
                wide_search=True,
                quickie=context.quickie,
            )
        except Exception as error:
            logger.error(f"[red]Error during wide search: {error}[/red]")
            return []

    @staticmethod
    def _parsed_search_title(untouched_filename: str | None) -> str:
        parsed = guessit_fn(
            untouched_filename or "", {"excludes": ["country", "language"]}
        )
        anime_raw: Any = anitopy_parse_fn(parsed.get("title", "")) or {}
        if not isinstance(anime_raw, dict):
            return ""
        anime = cast(dict[str, Any], anime_raw)
        return str(anime.get("anime_title", ""))

    async def _attempt_parsed(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        try:
            title = self._parsed_search_title(context.untouched_filename)
            logger.debug(
                f"[bold yellow]Trying IMDB with parsed title: {title}[/bold yellow]"
            )
            return await self._run_imdb_search(
                title,
                context.search_year,
                context.category,
                context.attempted + 1,
                wide_search=True,
                quickie=context.quickie,
            )
        except Exception:
            logger.info(
                "[bold red]Guessit failed parsing title, trying another method[/bold red]"
            )
            return []

    @staticmethod
    def _words_without_extension(filename: str) -> list[str]:
        words = filename.split()
        words_lower = [word.lower() for word in words]
        for extension in ("mp4", "mkv", "avi", "webm", "mov", "wmv"):
            if extension in words_lower:
                index = words_lower.index(extension)
                words.pop(index)
                break
        return words

    @classmethod
    def _reduced_search_title(cls, filename: str, count: int) -> str | None:
        words = cls._words_without_extension(filename)
        if len(words) <= count:
            return None
        return " ".join(words[:-count])

    async def _attempt_reduced(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        try:
            title = self._reduced_search_title(context.filename, 1)
            if title is None:
                return []
            logger.debug(
                f"[bold yellow]Trying IMDB with reduced name: {title}[/bold yellow]"
            )
            return await self._run_imdb_search(
                title,
                context.search_year,
                context.category,
                context.attempted + 1,
                wide_search=True,
                quickie=context.quickie,
            )
        except Exception as error:
            logger.info(
                f"[bold red]Reduced name search error:[/bold red] {error}"
            )
            return []

    async def _attempt_further_reduced(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        try:
            title = self._reduced_search_title(context.filename, 2)
            if title is None:
                return []
            logger.debug(
                f"[bold yellow]Trying IMDB with further reduced name: {title}[/bold yellow]"
            )
            return await self._run_imdb_search(
                title,
                context.search_year,
                context.category,
                context.attempted + 1,
                wide_search=True,
                quickie=context.quickie,
            )
        except Exception as error:
            logger.info(
                f"[bold red]Further reduced name search error:[/bold red] {error}"
            )
            return []

    def _search_attempts(
        self,
    ) -> tuple[
        Callable[[_SearchContext], Awaitable[list[dict[str, Any]]]], ...
    ]:
        return (
            self._attempt_primary,
            self._attempt_secondary,
            self._attempt_prefix,
            self._attempt_wide,
            self._attempt_parsed,
            self._attempt_reduced,
            self._attempt_further_reduced,
        )

    async def _collect_search_results(
        self, context: _SearchContext
    ) -> list[dict[str, Any]]:
        for attempt in self._search_attempts():
            results = await attempt(context)
            if results:
                return results
        return []

    @staticmethod
    def _normalized_category(category: str | None) -> str:
        return "" if category is None else category.lower()

    @classmethod
    def _quickie_type_matches(
        cls, title_type: str, category: str | None
    ) -> bool:
        normalized = cls._normalized_category(category)
        if normalized == "tv":
            return "tv series" in title_type
        if normalized == "movie":
            return "tv series" not in title_type
        return False

    @staticmethod
    def _numeric_imdb_id(value: Any) -> int | None:
        if not value:
            return None
        return int(str(value).replace("tt", "").strip())

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if not value:
            return None
        return int(value)

    @classmethod
    def _quickie_year_result(
        cls, imdb_id: Any, year: Any, search_year: str | int | None
    ) -> int:
        result_id = cls._numeric_imdb_id(imdb_id)
        if result_id is None:
            return 0
        year_int = cls._optional_int(year)
        search_year_int = cls._optional_int(search_year)
        if year_int is None or search_year_int is None:
            return result_id
        if year_int == search_year_int:
            return result_id
        logger.debug(
            f"[yellow]Year mismatch: found {year_int}, expected {search_year_int}[/yellow]"
        )
        return 0

    def _quickie_result(
        self,
        results: list[dict[str, Any]],
        search_year: str | int | None,
        category: str | None,
    ) -> int:
        if not results:
            return 0
        first = results[0]
        logger.debug(f"[cyan]Quickie search result: {first}[/cyan]")
        title = self.safe_get(first, ["node", "title"], {})
        type_info = self.safe_get(title, ["titleType"], {})
        title_type = str(self.safe_get(type_info, ["text"], "")).lower()
        imdb_id = self.safe_get(title, ["id"], "")
        if not imdb_id:
            logger.debug("[yellow]No IMDb ID found in quickie result[/yellow]")
            return 0
        if not self._quickie_type_matches(title_type, category):
            logger.debug(
                f"[yellow]Type mismatch: found {self.safe_get(type_info, ['text'], '')}, expected {category}[/yellow]"
            )
            return 0
        year = self.safe_get(title, ["releaseYear", "year"], None)
        return self._quickie_year_result(imdb_id, year, search_year)

    def _result_title(self, result: dict[str, Any]) -> Any:
        return self.safe_get(result, ["node", "title"], {})

    def _result_similarity(
        self, result: dict[str, Any], filename_norm: str, search_year: int
    ) -> float:
        title = self._result_title(result)
        title_text = str(self.safe_get(title, ["titleText", "text"], ""))
        result_year = int(
            self.safe_get(title, ["releaseYear", "year"], 0) or 0
        )
        similarity = SequenceMatcher(
            None, filename_norm, title_text.lower().strip()
        ).ratio()
        if similarity < 0.99:
            return similarity
        return similarity + self._year_similarity_boost(
            result_year, search_year
        )

    @staticmethod
    def _year_similarity_boost(result_year: int, search_year: int) -> float:
        if result_year <= 0 or search_year <= 0:
            return 0.0
        if result_year == search_year:
            return 0.1
        if result_year == search_year - 1:
            return 0.05
        return 0.0

    def _ranked_results(
        self,
        results: list[dict[str, Any]],
        filename: str,
        search_year: str | int | None,
    ) -> list[tuple[dict[str, Any], float]]:
        filename_norm = filename.lower().strip()
        search_year_int = int(search_year) if search_year else 0
        ranked = [
            (
                result,
                self._result_similarity(
                    result, filename_norm, search_year_int
                ),
            )
            for result in results
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return self._filter_ranked_results(ranked)

    @staticmethod
    def _filter_ranked_results(
        ranked: list[tuple[dict[str, Any], float]],
    ) -> list[tuple[dict[str, Any], float]]:
        if not ranked or ranked[0][1] < 0.90:
            return ranked
        best = ranked[0][1]
        filtered = [item for item in ranked if item[1] >= 0.75]
        logger.debug(
            f"[yellow]Filtered out low similarity results (< 0.70) since best match has {best:.2f} similarity[/yellow]"
        )
        return filtered

    @staticmethod
    def _has_clear_best_match(
        ranked: list[tuple[dict[str, Any], float]],
    ) -> bool:
        if not ranked:
            return False
        if ranked[0][1] < 0.85:
            return False
        second_best = ranked[1][1] if len(ranked) > 1 else 0.0
        return ranked[0][1] - second_best >= 0.10

    def _clear_best_match(
        self, ranked: list[tuple[dict[str, Any], float]]
    ) -> int | None:
        if not self._has_clear_best_match(ranked):
            return None
        title = self._result_title(ranked[0][0])
        imdb_id = self.safe_get(title, ["id"], "")
        result_id = self._numeric_imdb_id(imdb_id)
        if result_id is None:
            return None
        name = self.safe_get(title, ["titleText", "text"], "")
        logger.debug(
            f"[green]Auto-selecting best match: {name} (similarity: {ranked[0][1]:.2f})[/green]"
        )
        return result_id

    def _first_ranked_id(
        self, ranked: list[tuple[dict[str, Any], float]]
    ) -> int | None:
        if not ranked:
            return None
        title = self._result_title(ranked[0][0])
        return self._numeric_imdb_id(self.safe_get(title, ["id"], ""))

    def _log_ranked_results(
        self, ranked: list[tuple[dict[str, Any], float]]
    ) -> None:
        logger.info(
            "[bold yellow]Multiple IMDb results found. Please select the correct entry:[/bold yellow]"
        )
        for index, (candidate, similarity) in enumerate(ranked, 1):
            title = self._result_title(candidate)
            title_text = self.safe_get(title, ["titleText", "text"], "")
            year = self.safe_get(title, ["releaseYear", "year"], None)
            imdb_id = self.safe_get(title, ["id"], "")
            title_type = self.safe_get(title, ["titleType", "text"], "")
            plot = str(
                self.safe_get(title, ["plot", "plotText", "plainText"], "")
            )
            logger.info(
                f"[cyan]{index}.[/cyan] [bold]{title_text}[/bold] ({year}) [yellow]ID:[/yellow] {imdb_id} [yellow]Type:[/yellow] {title_type} [dim](similarity: {similarity:.2f})[/dim]"
            )
            if plot:
                suffix = "..." if len(plot) > 200 else ""
                logger.info(f"[green]Plot:[/green] {plot[:200]}{suffix}")
            logger.info("")

    @staticmethod
    async def _ask_imdb_selection(prompt: str) -> str:
        try:
            return await prompt_in_thread(cli_ui.ask_string, prompt) or ""
        except EOFError, KeyboardInterrupt:
            logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
            await cleanup_manager.cleanup()
            cleanup_manager.reset_terminal()
            raise OperationAbortedError(
                "IMDb selection was cancelled by the user."
            ) from None

    @staticmethod
    def _manual_imdb_selection(selection: str) -> tuple[bool, int | None]:
        try:
            if not selection.lower().startswith("tt") or len(selection) < 3:
                return False, None
            manual = selection.lower().replace("tt", "").strip()
            if manual.isdigit():
                logger.info(
                    f"[green]Using manual IMDb ID: {selection}[/green]"
                )
                return True, int(manual)
            logger.info(
                "[bold red]Invalid IMDb ID format. Please try again.[/bold red]"
            )
            return True, None
        except Exception as error:
            logger.info(
                f"[bold red]Error parsing IMDb ID: {error}. Please try again.[/bold red]"
            )
            return True, None

    def _numeric_search_selection(
        self,
        selection: str,
        ranked: list[tuple[dict[str, Any], float]],
    ) -> int | None:
        try:
            selected_index = int(selection)
        except ValueError:
            logger.info(
                "[bold red]Invalid input. Please enter a number or IMDb ID (tt1234567).[/bold red]"
            )
            return None
        if selected_index == 0:
            logger.info("[bold red]Skipping IMDb[/bold red]")
            return 0
        if not 1 <= selected_index <= len(ranked):
            logger.info(
                "[bold red]Selection out of range. Please try again.[/bold red]"
            )
            return None
        title = self._result_title(ranked[selected_index - 1][0])
        return self._numeric_imdb_id(self.safe_get(title, ["id"], ""))

    async def _prompt_ranked_result(
        self, ranked: list[tuple[dict[str, Any], float]]
    ) -> int:
        prompt = (
            "Enter the number of the correct entry, 0 for none, or manual IMDb ID "
            "(tt1234567): "
        )
        while True:
            selection = await self._ask_imdb_selection(prompt)
            is_manual, manual_id = self._manual_imdb_selection(selection)
            if is_manual:
                if manual_id is not None:
                    return manual_id
                continue
            selected_id = self._numeric_search_selection(selection, ranked)
            if selected_id is not None:
                return selected_id

    async def _multiple_search_result(
        self,
        results: list[dict[str, Any]],
        filename: str,
        search_year: str | int | None,
        unattended: bool,
    ) -> int:
        ranked = self._ranked_results(results, filename, search_year)
        clear_best = self._clear_best_match(ranked)
        if clear_best is not None:
            return clear_best
        if unattended:
            result_id = self._first_ranked_id(ranked)
            if result_id is None:
                return 0
            logger.debug(
                f"[green]Unattended mode: auto-selected IMDb ID {result_id}[/green]"
            )
            return result_id
        self._log_ranked_results(ranked)
        return await self._prompt_ranked_result(ranked)

    async def _no_search_result(self, unattended: bool) -> int:
        if unattended:
            logger.info(
                "[bold red]No IMDb results found in unattended mode. Skipping IMDb.[/bold red]"
            )
            return 0
        selection = await self._ask_imdb_selection(
            "No results found. Please enter a manual IMDb ID (tt1234567) or 0 to skip: "
        )
        is_manual, manual_id = self._manual_imdb_selection(selection)
        if not is_manual or manual_id is None:
            return 0
        return manual_id

    async def _resolve_search_results(
        self,
        results: list[dict[str, Any]],
        context: _SearchContext,
    ) -> int:
        if context.quickie:
            return self._quickie_result(
                results, context.search_year, context.category
            )
        if len(results) == 1:
            title = self._result_title(results[0])
            return self._numeric_imdb_id(self.safe_get(title, ["id"], "")) or 0
        if len(results) > 1:
            return await self._multiple_search_result(
                results,
                context.filename,
                context.search_year,
                context.unattended,
            )
        return await self._no_search_result(context.unattended)

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
        normalized_attempted = self._search_attempted(attempted)
        logger.debug(
            f"[yellow]Searching IMDb for {filename} and year {search_year}...[/yellow]"
        )
        await self._search_delay(normalized_attempted)
        context = _SearchContext(
            filename=filename,
            search_year=search_year,
            quickie=quickie,
            category=category,
            secondary_title=secondary_title,
            untouched_filename=untouched_filename,
            attempted=normalized_attempted,
            duration=duration,
            unattended=unattended,
        )
        results = await self._collect_search_results(context)
        return await self._resolve_search_results(results, context)

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
