# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from collections.abc import Mapping
from typing import Any, cast

import httpx

from src.integrations.observability.runtime_support import logger

ShowInfo = dict[str, Any]
SonarrInstance = tuple[str, str]


def _empty_show_info() -> ShowInfo:
    return {
        "tvdb_id": None,
        "imdb_id": None,
        "tvmaze_id": None,
        "tmdb_id": None,
        "genres": [],
        "title": "",
        "year": None,
        "release_group": None,
    }


class SonarrManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.default_config = cast(dict[str, Any], config.get("DEFAULT", {}))

    def _has_api_keys(self) -> bool:
        return any(
            key.startswith("sonarr_api_key") for key in self.default_config
        )

    @staticmethod
    def _instance_suffix(instance_index: int) -> str:
        return "" if instance_index == 0 else f"_{instance_index}"

    @staticmethod
    def _instance_label(instance_index: int) -> str:
        return "default" if instance_index == 0 else str(instance_index)

    def _instance_config(self, instance_index: int) -> SonarrInstance | None:
        suffix = self._instance_suffix(instance_index)
        api_key = self.default_config.get(f"sonarr_api_key{suffix}")
        if not isinstance(api_key, str) or not api_key.strip():
            return None
        base_url = self.default_config.get(f"sonarr_url{suffix}")
        if not isinstance(base_url, str) or not base_url.strip():
            return None
        return api_key.strip(), base_url.strip().rstrip("/")

    @staticmethod
    def _query_url(
        base_url: str,
        tvdb_id: int | None,
        filename: str | None,
        title: str | None,
    ) -> str | None:
        if tvdb_id:
            return f"{base_url}/api/v3/series?tvdbId={tvdb_id}&includeSeasonImages=false"
        if filename and title:
            return f"{base_url}/api/v3/parse?title={title}&path={filename}"
        return None

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _has_identifiers(show_data: ShowInfo) -> bool:
        for key in ("tvdb_id", "imdb_id", "tmdb_id"):
            if show_data.get(key):
                return True
        return False

    async def _successful_response_data(
        self, response: httpx.Response, instance_label: str
    ) -> ShowInfo | None:
        if response.status_code != 200:
            logger.info(
                f"[yellow]Failed to fetch from Sonarr instance {instance_label}: {response.status_code} - {response.text}[/yellow]"
            )
            return None
        data = response.json()
        logger.debug(
            f"[blue]Sonarr Response Status:[/blue] {response.status_code}"
        )
        logger.debug(f"[blue]Sonarr Response Data:[/blue] {data}")
        return await self.extract_show_data(data)

    async def _fetch_instance(
        self, instance_index: int, url: str, api_key: str
    ) -> ShowInfo | None:
        label = self._instance_label(instance_index)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, headers=self._headers(api_key), timeout=10.0
                )
            return await self._successful_response_data(response, label)
        except httpx.TimeoutException:
            logger.info(
                f"[red]Timeout when fetching from Sonarr instance {label}[/red]"
            )
        except httpx.RequestError as exc:
            logger.error(
                f"[red]Error fetching from Sonarr instance {label}: {exc}[/red]"
            )
        except Exception as exc:
            logger.error(
                f"[red]Unexpected error with Sonarr instance {label}: {exc}[/red]"
            )
        return None

    async def _try_instance(
        self,
        instance_index: int,
        tvdb_id: int | None,
        filename: str | None,
        title: str | None,
    ) -> ShowInfo | None:
        instance = self._instance_config(instance_index)
        if instance is None:
            return None
        api_key, base_url = instance
        label = self._instance_label(instance_index)
        logger.debug(f"[blue]Trying Sonarr instance {label}[/blue]")
        url = self._query_url(base_url, tvdb_id, filename, title)
        if url is None:
            return None
        logger.debug(f"[green]TVDB ID {tvdb_id}[/green]")
        logger.debug(f"[blue]Sonarr URL:[/blue] {url}")
        show_data = await self._fetch_instance(instance_index, url, api_key)
        if show_data is None or not self._has_identifiers(show_data):
            return None
        logger.info(
            f"[green]Found valid show data from Sonarr instance {label}[/green]"
        )
        return show_data

    async def get_sonarr_data(
        self,
        tvdb_id: int | None = None,
        filename: str | None = None,
        title: str | None = None,
    ) -> ShowInfo | None:
        if not self._has_api_keys():
            logger.info("[red]No Sonarr API keys are configured.[/red]")
            return None
        for instance_index in range(4):
            show_data = await self._try_instance(
                instance_index, tvdb_id, filename, title
            )
            if show_data is not None:
                return show_data
        logger.info(
            "[yellow]No Sonarr instance returned valid show data.[/yellow]"
        )
        return None

    @staticmethod
    def _imdb_id(series: Mapping[str, Any]) -> int | None:
        value = series.get("imdbId")
        if not value:
            return None
        return int(str(value).replace("tt", ""))

    @staticmethod
    def _release_group(value: Any) -> Any | None:
        return value if value else None

    @classmethod
    def _parse_show_info(cls, sonarr_data: Mapping[str, Any]) -> ShowInfo:
        series = cast(Mapping[str, Any], sonarr_data["series"])
        parsed_info = cast(
            Mapping[str, Any], sonarr_data.get("parsedEpisodeInfo", {})
        )
        return {
            "tvdb_id": series.get("tvdbId", None),
            "imdb_id": cls._imdb_id(series),
            "tvmaze_id": series.get("tvMazeId", None),
            "tmdb_id": series.get("tmdbId", None),
            "genres": series.get("genres", []),
            "release_group": cls._release_group(
                parsed_info.get("releaseGroup")
            ),
            "year": series.get("year", None),
        }

    @classmethod
    def _series_show_info(cls, series: Mapping[str, Any]) -> ShowInfo:
        return {
            "tvdb_id": series.get("tvdbId", None),
            "imdb_id": cls._imdb_id(series),
            "tvmaze_id": series.get("tvMazeId", None),
            "tmdb_id": series.get("tmdbId", None),
            "genres": series.get("genres", []),
            "title": series.get("title", ""),
            "year": series.get("year", None),
            "release_group": cls._release_group(series.get("releaseGroup")),
        }

    @classmethod
    def _list_show_info(cls, sonarr_data: list[Any]) -> ShowInfo:
        if not sonarr_data:
            return _empty_show_info()
        series = cast(Mapping[str, Any], sonarr_data[0])
        return cls._series_show_info(series)

    async def extract_show_data(self, sonarr_data: Any) -> ShowInfo:
        if not sonarr_data:
            return _empty_show_info()
        if isinstance(sonarr_data, dict) and "series" in sonarr_data:
            return self._parse_show_info(cast(Mapping[str, Any], sonarr_data))
        if isinstance(sonarr_data, list):
            return self._list_show_info(cast(list[Any], sonarr_data))
        return _empty_show_info()
