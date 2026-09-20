# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
import re
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.USENET.search_helpers import (
    build_newznab_search_query,
    get_daily_api_hit_limit,
    get_newznab_search_category_id,
    parse_newznab_dupes,
    reserve_daily_api_hit,
)

Config = dict[str, Any]


class DrunkenSlug:
    """
    DS Private Torrent Tracker
    """

    base_url = "https://drunkenslug.com"

    auth_type = "other_api"
    tracker = "DRUNKENSLUG"
    display_name = "DrunkenSlug"
    allows_bloated_audio = True
    banned_groups = ()
    search_url = f"{base_url}/api"
    torrent_url = f"{base_url}/search/"
    supported_categories = ("TV", "MOVIE", "GAME", "BOOK")
    is_usenet = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self.common = Common(config)
        self.tracker_cfg = self.config.get("TRACKERS", {}).get(
            self.tracker, {}
        )
        self.api_key = str(self.tracker_cfg.get("api_key", "")).strip()
        self.daily_api_hit_limit = get_daily_api_hit_limit(self.tracker_cfg)

    async def search_existing(self, meta: Meta) -> list[Any]:
        cached = await self._cached_upload_name(meta)
        if cached:
            return [cached]
        if self.daily_api_hit_limit <= 0:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search via API is disabled because daily_api_hit_limit is 0.[/yellow]"
            )
            return []
        params = self._search_params(meta)
        return await self._api_search(meta, params)

    async def _cached_upload_name(self, meta: Meta) -> str:
        release_name = await self.get_name(meta)
        cache_file = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"{self.tracker}_upload_ok"
        )
        if release_name and cache_file.exists():
            logger.info(
                f"{self.tracker}: [yellow]Found local upload cache.[/yellow]"
            )
            return release_name
        return ""

    def _search_params(self, meta: Meta) -> dict[str, str]:
        category = str(meta.category or "").upper()
        if category == "TV":
            return self._tv_search_params(meta)
        if category == "MOVIE":
            return self._movie_search_params(meta)
        return {
            "cat": get_newznab_search_category_id(meta),
            "t": "search",
            "q": self.get_search_query(meta),
        }

    def _tv_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"cat": get_newznab_search_category_id(meta), "t": "tvsearch"}
        params.update(self._tv_identifier_params(meta))
        if meta.season_int > 0:
            params["season"] = str(meta.season_int)
        if meta.episode_int > 0:
            params["ep"] = str(meta.episode_int)
        return params

    def _tv_identifier_params(self, meta: Meta) -> dict[str, str]:
        if self._positive_numeric(meta.tvdb_id):
            return {"tvdbid": str(meta.tvdb_id)}
        if self._positive_numeric(meta.tmdb_id):
            return {"tmdbid": str(meta.tmdb_id)}
        if self._positive_numeric(meta.imdb_id):
            return {"imdbid": str(meta.imdb_id)}
        return {"q": self.get_search_query(meta)}

    def _movie_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"cat": get_newznab_search_category_id(meta), "t": "movie"}
        if self._positive_numeric(meta.imdb_id):
            params["imdbid"] = str(meta.imdb_id)
        elif self._positive_numeric(meta.tmdb_id):
            params["tmdbid"] = str(meta.tmdb_id)
        else:
            params["q"] = self.get_search_query(meta)
        return params

    @staticmethod
    def _positive_numeric(value: Any) -> bool:
        text = str(value or "")
        return text.isdigit() and int(text) > 0

    async def _api_search(
        self, meta: Meta, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        allowed, used_hits = await reserve_daily_api_hit(
            meta.base_dir, self.tracker, self.daily_api_hit_limit
        )
        if not allowed:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search skipped because the 24-hour API hit limit ({self.daily_api_hit_limit}) has been reached.[/yellow]"
            )
            return []
        response = await self._search_response(params)
        logger.debug(
            f"{self.tracker}: Duplicate search used API hit {used_hits}/{self.daily_api_hit_limit} in the last 24 hours."
        )
        response.raise_for_status()
        if not response.text.strip():
            return []
        return self._dedupe_results(
            self._parse_dupes_from_response(response.text)
        )

    async def _search_response(self, params: dict[str, str]) -> httpx.Response:
        request_params = {
            "apikey": self.api_key,
            "limit": "100",
            "extended": "1",
            **params,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(self.search_url, params=request_params)

    @staticmethod
    def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in results:
            key = str(result.get("link") or result.get("name") or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    async def get_name(self, meta: Meta) -> str:
        return meta.scene_name or meta.basename_no_ext

    def get_search_query(self, meta: Meta) -> str:
        return build_newznab_search_query(meta)

    def _parse_dupes_from_response(
        self, response_text: str
    ) -> list[dict[str, Any]]:
        return parse_newznab_dupes(response_text)

    async def upload(self, meta: Meta) -> bool:
        status = meta.tracker_status.setdefault(self.tracker, {})
        if not await self._nzb_is_uploadable(meta):
            status["status_message"] = (
                "data error: NZB file missing or password missing in header"
            )
            return False
        nzb_name = f"{await self.get_name(meta)}.nzb"
        nzb_content = await self._read_nzb(meta.nzb_path)
        if meta.debug:
            status["status_message"] = "Debug mode enabled, skipping upload."
            return True
        return await self._upload_nzb(meta, status, nzb_name, nzb_content)

    async def _nzb_is_uploadable(self, meta: Meta) -> bool:
        return bool(meta.nzb_path) and await self.common.check_nzb_file(
            self.tracker, meta
        )

    @staticmethod
    async def _read_nzb(nzb_path: str | Path) -> bytes:
        async with aiofiles.open(nzb_path, "rb") as handle:
            return await handle.read()

    async def _upload_nzb(
        self,
        meta: Meta,
        status: dict[str, Any],
        nzb_name: str,
        nzb_content: bytes,
    ) -> bool:
        try:
            response = await self._post_nzb(nzb_name, nzb_content)
            return await self._handle_upload_response(
                meta, status, nzb_name, response
            )
        except httpx.TimeoutException:
            status["status_message"] = (
                "data error: Request timed out after 60 seconds"
            )
            return False
        except httpx.RequestError as error:
            status["status_message"] = (
                f"data error: Unable to upload. Error: {error}"
            )
            return False
        except Exception as error:
            status["status_message"] = (
                f"data error: Unexpected error. Error: {error}"
            )
            return False

    async def _post_nzb(
        self, nzb_name: str, nzb_content: bytes
    ) -> httpx.Response:
        files = {"files[]": (nzb_name, nzb_content, "application/x-nzb")}
        headers = {"X-API-Key": self.api_key}
        async with httpx.AsyncClient() as client:
            return await client.post(
                "https://nzbs.drunkenslug.com/upload.php",
                headers=headers,
                files=files,
            )

    async def _handle_upload_response(
        self,
        meta: Meta,
        status: dict[str, Any],
        nzb_name: str,
        response: httpx.Response,
    ) -> bool:
        if response.status_code not in {200, 201}:
            status["status_message"] = (
                f"data error: HTTP {response.status_code} - {response.text}"
            )
            return False
        data = self._json_payload(response, status)
        if data is None:
            return False
        result = self._first_result(data)
        if result is None:
            status["status_message"] = (
                "data error: No results returned from tracker."
            )
            return False
        status["status_message"] = self._clean_upload_result(result, nzb_name)
        status["torrent_id"] = nzb_name.removesuffix(".nzb")
        await self._write_upload_cache(meta)
        return True

    @staticmethod
    def _json_payload(
        response: httpx.Response, status: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            status["status_message"] = (
                "data error: Could not decode JSON response."
            )
            return None
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _first_result(data: dict[str, Any]) -> str | None:
        results = data.get("results", [])
        if not isinstance(results, list) or not results:
            return None
        return str(results[0])

    @staticmethod
    def _clean_upload_result(result: str, nzb_name: str) -> str:
        cleaned = result.replace(f"{nzb_name}: ", "[redacted]: ")
        return re.sub(
            r"(\buploaded by\s+)\S+",
            r"\1[redacted]",
            cleaned,
            flags=re.IGNORECASE,
        )

    async def _write_upload_cache(self, meta: Meta) -> None:
        cache_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            cache_dir / f"{self.tracker}_upload_ok", "w", encoding="utf-8"
        ) as handle:
            await handle.write("ok")
