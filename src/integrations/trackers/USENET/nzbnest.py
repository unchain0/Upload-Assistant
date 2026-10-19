# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import json
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


def _digit_id(value: object) -> bool:
    return bool(value) and str(value).isdigit()


def _search_category(meta: Meta) -> str:
    if getattr(meta, "is_sports", False):
        return "SPORTS"
    return str(meta.category or "").upper()


class NzbNest:
    """NzbNest Usenet indexer."""

    base_url = "https://nzbnest.com"
    search_url = f"{base_url}/api"
    upload_url = f"{base_url}/v1/upload"
    torrent_url = f"{base_url}/account/releases/"

    auth_type = "other_api"
    tracker = "NZBNEST"
    display_name = "NzbNest"
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    supported_categories = ("TV", "MOVIE", "GAME", "BOOK", "MUSIC")
    is_usenet = True
    exact_match_only = False

    def __init__(self, config: Config) -> None:
        self.config = config
        self.common = Common(config)
        self.tracker_cfg = config.get("TRACKERS", {}).get(self.tracker, {})
        self.api_key = str(self.tracker_cfg.get("api_key", "")).strip()
        self.daily_api_hit_limit = get_daily_api_hit_limit(self.tracker_cfg)

    async def get_name(self, meta: Meta) -> str:
        return meta.scene_name or meta.basename_no_ext

    def get_search_query(self, meta: Meta) -> str:
        return build_newznab_search_query(meta)

    def _parse_dupes_from_response(
        self, response_text: str
    ) -> list[dict[str, Any]]:
        return parse_newznab_dupes(response_text, self.torrent_url)

    async def search_existing(self, meta: Meta) -> list[Any]:
        cached = await self._cached_upload_match(meta)
        if cached:
            return cached
        if self.daily_api_hit_limit <= 0:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search via API is disabled because daily_api_hit_limit is 0.[/yellow]"
            )
            return []
        return await self._search_api(meta)

    async def _search_api(self, meta: Meta) -> list[Any]:
        allowed, used_hits = await reserve_daily_api_hit(
            meta.base_dir, self.tracker, self.daily_api_hit_limit
        )
        if not allowed:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search skipped because the 24-hour API hit limit ({self.daily_api_hit_limit}) has been reached.[/yellow]"
            )
            return []
        response = await self._search_response(self._search_params(meta))
        logger.debug(
            f"{self.tracker}: Duplicate search used API hit {used_hits}/{self.daily_api_hit_limit} in the last 24 hours."
        )
        response.raise_for_status()
        if not response.text.strip():
            return []
        return self._parse_dupes_from_response(response.text)

    async def _cached_upload_match(self, meta: Meta) -> list[str]:
        release_name = await self.get_name(meta)
        cache_file = (
            Path(meta.base_dir) / "tmp" / meta.uuid / f"{self.tracker}_upload_ok"
        )
        if not release_name or not cache_file.exists():
            return []
        logger.info(
            f"{self.tracker}: [yellow]Found local upload cache.[/yellow]"
        )
        return [release_name]

    def _search_params(self, meta: Meta) -> dict[str, str]:
        category = _search_category(meta)
        params = self._category_params(meta, category)
        if category != "XXX":
            params["cat"] = get_newznab_search_category_id(meta)
        return params

    def _category_params(self, meta: Meta, category: str) -> dict[str, str]:
        if category == "TV":
            return self._tv_search_params(meta)
        if category == "MOVIE":
            return self._movie_search_params(meta)
        return {"t": "search", "q": self.get_search_query(meta)}

    def _tv_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"t": "tvsearch"}
        params.update(self._tv_search_identity(meta))
        if meta.season_int > 0:
            params["season"] = str(meta.season_int)
        if meta.episode_int > 0:
            params["ep"] = str(meta.episode_int)
        return params

    def _tv_search_identity(self, meta: Meta) -> dict[str, str]:
        if _digit_id(meta.tvdb_id):
            return {"tvdbid": str(meta.tvdb_id)}
        if _digit_id(meta.tmdb_id):
            return {"tmdbid": str(meta.tmdb_id)}
        if meta.imdb_tt:
            return {"imdbid": meta.imdb_tt}
        return {"q": self.get_search_query(meta)}

    def _movie_search_params(self, meta: Meta) -> dict[str, str]:
        if meta.imdb_tt:
            return {"t": "movie", "imdbid": meta.imdb_tt}
        if _digit_id(meta.tmdb_id):
            return {"t": "movie", "tmdbid": str(meta.tmdb_id)}
        return {"t": "movie", "q": self.get_search_query(meta)}

    async def _search_response(self, params: dict[str, str]) -> httpx.Response:
        request_params = {
            "apikey": self.api_key,
            "limit": "100",
            "extended": "1",
            **params,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(self.search_url, params=request_params)

    async def upload(self, meta: Meta) -> bool:
        status = meta.tracker_status.setdefault(self.tracker, {})
        if not await self._valid_upload_source(meta):
            status["status_message"] = (
                "data error: NZB file missing or password missing in header"
            )
            return False
        if meta.debug:
            status["status_message"] = "Debug mode enabled, skipping upload."
            return True
        return await self._upload_with_error_handling(meta, status)

    async def _valid_upload_source(self, meta: Meta) -> bool:
        return bool(meta.nzb_path) and await self.common.check_nzb_file(
            self.tracker, meta
        )

    async def _upload_with_error_handling(
        self, meta: Meta, status: dict[str, Any]
    ) -> bool:
        try:
            response = await self._submit(meta)
            return await self._process_upload_response(meta, response, status)
        except httpx.TimeoutException:
            status["status_message"] = (
                "data error: Request timed out after 60 seconds"
            )
        except httpx.RequestError as error:
            status["status_message"] = (
                f"data error: Unable to upload. Error: {error}"
            )
        except Exception as error:
            status["status_message"] = (
                f"data error: Unexpected error. Error: {error}"
            )
        return False

    async def _process_upload_response(
        self, meta: Meta, response: httpx.Response, status: dict[str, Any]
    ) -> bool:
        if response.status_code not in (200, 201):
            status["status_message"] = (
                f"data error: HTTP {response.status_code} - {response.text}"
            )
            return False
        guid = _response_guid(response)
        if not guid:
            status["status_message"] = (
                "data error: NzbNest did not return a release guid."
            )
            return False
        await self._record_success(meta, status, guid)
        return True

    async def _submit(self, meta: Meta) -> httpx.Response:
        files = await self._submission_files(meta)
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            return await client.post(
                self.upload_url, params={"apikey": self.api_key}, files=files
            )

    async def _submission_files(
        self, meta: Meta
    ) -> dict[str, tuple[str, bytes, str]]:
        nzb_path = Path(str(meta.nzb_path))
        async with aiofiles.open(nzb_path, "rb") as handle:
            nzb_content = await handle.read()
        files = {
            "file": (nzb_path.name, nzb_content, "application/x-nzb")
        }
        nfo_file = await self._get_nfo_file(meta, nzb_path)
        if nfo_file is not None:
            files["nfo"] = nfo_file
        return files

    async def _get_nfo_file(
        self, meta: Meta, nzb_path: Path
    ) -> tuple[str, bytes, str] | None:
        candidate = next(
            (
                path
                for path in self._nfo_candidates(meta)
                if path.exists() and path.is_file()
            ),
            None,
        )
        if candidate is None:
            return None
        async with aiofiles.open(candidate, "rb") as handle:
            return f"{nzb_path.stem}.nfo", await handle.read(), "text/plain"

    @staticmethod
    def _nfo_candidates(meta: Meta) -> list[Path]:
        nfo_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        preferred = _preferred_nfo(meta, nfo_dir)
        return [preferred, *nfo_dir.glob("*.nfo")]

    async def _record_success(
        self, meta: Meta, status: dict[str, Any], guid: str
    ) -> None:
        status["status_message"] = "Upload successful"
        status["torrent_id"] = guid
        cache_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            cache_dir / f"{self.tracker}_upload_ok", "w", encoding="utf-8"
        ) as handle:
            await handle.write("ok")


def _preferred_nfo(meta: Meta, nfo_dir: Path) -> Path:
    if meta.scene:
        return nfo_dir / "ignored.nfo"
    if meta.is_disc == "BDMV":
        return nfo_dir / "BD_SUMMARY_00.txt"
    return nfo_dir / "MEDIAINFO_CLEANPATH.txt"


def _response_guid(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    guid = payload.get("guid")
    return str(guid) if guid else ""
