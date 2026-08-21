# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

import httpx

from src.integrations.observability.runtime_support import logger

MovieInfo = dict[str, Any]


class RadarrManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        default = config.get("DEFAULT", {})
        self.default_config = (
            cast(dict[str, Any], default) if isinstance(default, dict) else {}
        )

    async def get_radarr_data(
        self, tmdb_id: int | None = None, filename: str | None = None
    ) -> MovieInfo | None:
        instances = self._configured_instances()
        if not instances:
            logger.info("[red]No Radarr API keys are configured.[/red]")
            return None
        for index, api_key, base_url in instances:
            query_url = self._query_url(base_url, tmdb_id, filename)
            if query_url is None:
                continue
            movie = await self._fetch_instance(
                index, api_key, query_url, filename
            )
            if self._valid_movie_info(movie):
                logger.info(
                    f"[green]Found valid movie data from Radarr instance {self._instance_label(index)}[/green]"
                )
                return movie
        logger.info(
            "[yellow]No Radarr instance returned valid movie data.[/yellow]"
        )
        return None

    def _configured_instances(self) -> list[tuple[int, str, str]]:
        instances: list[tuple[int, str, str]] = []
        for index in range(4):
            suffix = "" if index == 0 else f"_{index}"
            api_key = self._config_string(f"radarr_api_key{suffix}")
            base_url = self._config_string(f"radarr_url{suffix}").rstrip("/")
            if api_key and base_url:
                instances.append((index, api_key, base_url))
        return instances

    def _config_string(self, key: str) -> str:
        value = self.default_config.get(key)
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _query_url(
        base_url: str, tmdb_id: int | None, filename: str | None
    ) -> str | None:
        if tmdb_id:
            return f"{base_url}/api/v3/movie?tmdbId={tmdb_id}&excludeLocalCovers=true"
        if filename:
            return f"{base_url}/api/v3/movie/lookup?term={filename}"
        return None

    async def _fetch_instance(
        self, index: int, api_key: str, url: str, filename: str | None
    ) -> MovieInfo | None:
        logger.debug(
            f"[blue]Trying Radarr instance {self._instance_label(index)}[/blue]"
        )
        logger.debug(f"[blue]Radarr URL:[/blue] {url}")
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url,
                    headers={
                        "X-Api-Key": api_key,
                        "Content-Type": "application/json",
                    },
                    timeout=10.0,
                )
            return await self._movie_from_response(response, index, filename)
        except httpx.TimeoutException:
            logger.info(
                f"[red]Timeout when fetching from Radarr instance {self._instance_label(index)}[/red]"
            )
        except httpx.RequestError as error:
            logger.error(
                f"[red]Error fetching from Radarr instance {self._instance_label(index)}: {error}[/red]"
            )
        except Exception as error:
            logger.error(
                f"[red]Unexpected error with Radarr instance {self._instance_label(index)}: {error}[/red]"
            )
        return None

    async def _movie_from_response(
        self, response: Any, index: int, filename: str | None
    ) -> MovieInfo | None:
        if response.status_code != 200:
            logger.info(
                f"[yellow]Failed to fetch from Radarr instance {self._instance_label(index)}: {response.status_code} - {response.text}[/yellow]"
            )
            return None
        data = response.json()
        logger.debug(
            f"[blue]Radarr Response Status:[/blue] {response.status_code}"
        )
        logger.debug(f"[blue]Radarr Response Data:[/blue] {data}")
        return await self.extract_movie_data(data, filename)

    @staticmethod
    def _instance_label(index: int) -> str:
        return "default" if index == 0 else str(index)

    @staticmethod
    def _valid_movie_info(movie: MovieInfo | None) -> bool:
        return bool(movie and (movie.get("imdb_id") or movie.get("tmdb_id")))

    async def extract_movie_data(
        self, radarr_data: Any, filename: str | None = None
    ) -> MovieInfo | None:
        items = self._movie_items(radarr_data)
        if not items:
            return self._empty_movie_info()
        movie = self._select_movie(items, filename)
        return None if movie is None else self._movie_info(movie)

    @staticmethod
    def _movie_items(value: Any) -> list[Mapping[str, Any]]:
        if not isinstance(value, list):
            return []
        values = cast(list[Any], value)
        return [
            cast(Mapping[str, Any], item)
            for item in values
            if isinstance(item, Mapping)
        ]

    @staticmethod
    def _empty_movie_info() -> MovieInfo:
        return {
            "imdb_id": None,
            "tmdb_id": None,
            "year": None,
            "secondary_year": None,
            "genres": [],
            "release_group": None,
        }

    @classmethod
    def _select_movie(
        cls, items: list[Mapping[str, Any]], filename: str | None
    ) -> Mapping[str, Any] | None:
        if not filename:
            return items[0]
        exact = cls._exact_movie(items, filename)
        return (
            exact
            if exact is not None
            else cls._best_scored_movie(items, filename)
        )

    @classmethod
    def _exact_movie(
        cls, items: list[Mapping[str, Any]], filename: str
    ) -> Mapping[str, Any] | None:
        return next(
            (item for item in items if cls._exact_file_match(item, filename)),
            None,
        )

    @classmethod
    def _best_scored_movie(
        cls, items: list[Mapping[str, Any]], filename: str
    ) -> Mapping[str, Any] | None:
        scored = [
            (cls._movie_match_score(item, filename), item) for item in items
        ]
        best_score, best = max(scored, key=lambda pair: pair[0])
        if best_score < 6:
            return None
        logger.debug(
            f"[green]Accepted strong Radarr lookup match with score {best_score}: {best.get('title', '')}[/green]"
        )
        return best

    @staticmethod
    def _exact_file_match(movie: Mapping[str, Any], filename: str) -> bool:
        movie_file = movie.get("movieFile", {})
        if not isinstance(movie_file, Mapping):
            return False
        file_map = cast(Mapping[str, Any], movie_file)
        original: Any = file_map.get("originalFilePath")
        if not isinstance(original, str) or not original:
            return False
        return (
            original == filename or Path(original).name == Path(filename).name
        )

    @classmethod
    def _movie_match_score(
        cls, movie: Mapping[str, Any], filename: str
    ) -> int:
        source_title, source_year = cls._filename_identity(filename)
        return (
            cls._title_score(source_title, movie)
            + cls._year_score(source_year, movie)
            + cls._identifier_score(movie)
        )

    @classmethod
    def _title_score(cls, source_title: str, movie: Mapping[str, Any]) -> int:
        return (
            4 if cls._best_title_similarity(source_title, movie) >= 0.75 else 0
        )

    @classmethod
    def _year_score(
        cls, source_year: int | None, movie: Mapping[str, Any]
    ) -> int:
        return (
            3
            if source_year is not None
            and source_year in cls._candidate_years(movie)
            else 0
        )

    @staticmethod
    def _identifier_score(movie: Mapping[str, Any]) -> int:
        return 1 if movie.get("tmdbId") or movie.get("imdbId") else 0

    @classmethod
    def _filename_identity(cls, filename: str) -> tuple[str, int | None]:
        stem = Path(filename).stem
        year_match = re.search(r"(?<!\d)(18|19|20)\d{2}(?!\d)", stem)
        title_part = (
            stem[: year_match.start()] if year_match is not None else stem
        )
        title = cls._normalized_title(title_part)
        year = int(year_match.group(0)) if year_match is not None else None
        return title, year

    @classmethod
    def _best_title_similarity(
        cls, source_title: str, movie: Mapping[str, Any]
    ) -> float:
        if not source_title:
            return 0.0
        candidates = cls._candidate_titles(movie)
        return max(
            (
                SequenceMatcher(
                    None, source_title, cls._normalized_title(title)
                ).ratio()
                for title in candidates
            ),
            default=0.0,
        )

    @classmethod
    def _candidate_titles(cls, movie: Mapping[str, Any]) -> list[str]:
        title: Any = movie.get("title")
        original_title: Any = movie.get("originalTitle")
        values: list[Any] = [
            title,
            original_title,
            *cls._alternate_titles(movie),
        ]
        return [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]

    @staticmethod
    def _alternate_titles(movie: Mapping[str, Any]) -> list[Any]:
        alternate = movie.get("alternateTitles", [])
        if not isinstance(alternate, list):
            return []
        values = cast(list[Any], alternate)
        titles: list[Any] = []
        for item in values:
            if isinstance(item, Mapping):
                mapping = cast(Mapping[str, Any], item)
                titles.append(mapping.get("title"))
        return titles

    @staticmethod
    def _normalized_title(value: str) -> str:
        return "".join(
            character for character in value.casefold() if character.isalnum()
        )

    @staticmethod
    def _candidate_years(movie: Mapping[str, Any]) -> set[int]:
        years: set[int] = set()
        for key in ("year", "secondaryYear"):
            value: Any = movie.get(key)
            try:
                if value is not None:
                    years.add(int(value))
            except TypeError, ValueError:
                continue
        return years

    @classmethod
    def _movie_info(cls, movie: Mapping[str, Any]) -> MovieInfo:
        movie_file = movie.get("movieFile", {})
        release_group = cls._release_group(movie_file)
        return {
            "imdb_id": cls._imdb_numeric_id(movie.get("imdbId")),
            "tmdb_id": movie.get("tmdbId"),
            "year": movie.get("year"),
            "secondary_year": movie.get("secondaryYear"),
            "genres": movie.get("genres", []),
            "release_group": release_group or None,
        }

    @staticmethod
    def _release_group(value: Any) -> str | None:
        if not isinstance(value, Mapping):
            return None
        file_map = cast(Mapping[str, object], value)
        release_group = file_map.get("releaseGroup")
        return (
            release_group
            if isinstance(release_group, str) and release_group
            else None
        )

    @staticmethod
    def _imdb_numeric_id(value: Any) -> int | None:
        text = str(value or "").replace("tt", "")
        return int(text) if text.isdigit() else None
