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


class NZBGeek:
    """NZBGeek Usenet indexer."""

    base_url = "https://api.nzbgeek.info"
    torrent_url = "https://nzbgeek.info/geekseek.php?browseincludewords="
    submit_url = f"{base_url}/submit"
    search_url = f"{base_url}/api"

    auth_type = "other_api"
    tracker = "NZBGEEK"
    display_name = "NZBGeek"
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    supported_categories = ("TV", "MOVIE", "GAME", "BOOK", "MUSIC")
    is_usenet = True

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
        return parse_newznab_dupes(response_text)

    async def search_existing(self, meta: Meta) -> list[Any]:
        cached = await self._cached_upload_match(meta)
        if cached:
            return cached
        if self.daily_api_hit_limit <= 0:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search via API is disabled because daily_api_hit_limit is 0.[/yellow]"
            )
            return []
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
        return (
            self._parse_dupes_from_response(response.text)
            if response.text.strip()
            else []
        )

    async def _cached_upload_match(self, meta: Meta) -> list[str]:
        release_name = await self.get_name(meta)
        cache_file = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"{self.tracker}_upload_ok"
        )
        if not release_name or not cache_file.exists():
            return []
        logger.info(
            f"{self.tracker}: [yellow]Found local upload cache.[/yellow]"
        )
        return [release_name]

    def _search_params(self, meta: Meta) -> dict[str, str]:
        params: dict[str, str] = {"cat": get_newznab_search_category_id(meta)}
        category = meta.category.upper()
        if category == "TV":
            params.update(self._tv_search_params(meta))
        elif category == "MOVIE":
            params.update(self._movie_search_params(meta))
        else:
            params.update(t="search", q=self.get_search_query(meta))
        return params

    def _tv_search_params(self, meta: Meta) -> dict[str, str]:
        params: dict[str, str] = {"t": "tvsearch"}
        params.update(self._tv_search_identity(meta))
        if meta.season_int > 0:
            params["season"] = str(meta.season_int)
        if meta.episode_int > 0:
            params["ep"] = str(meta.episode_int)
        return params

    def _tv_search_identity(self, meta: Meta) -> dict[str, str]:
        if meta.tvdb_id and str(meta.tvdb_id).isdigit():
            return {"tvdbid": str(meta.tvdb_id)}
        return {"q": self.get_search_query(meta)}

    def _movie_search_params(self, meta: Meta) -> dict[str, str]:
        if meta.imdb_tt:
            return {"t": "movie", "imdbid": meta.imdb_tt}
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

    def get_category_id(self, meta: Meta) -> str:
        handlers = {
            "MOVIE": self._movie_category,
            "TV": self._tv_category,
            "XXX": self._adult_category,
            "GAME": self._game_category,
            "MUSIC": self._music_category,
            "BOOK": self._book_category,
        }
        handler = handlers.get(meta.category.upper())
        return handler(meta) if handler is not None else "8010"

    @classmethod
    def _movie_category(cls, meta: Meta) -> str:
        return cls._quality_category(meta, uhd="2045", hd="2040", sd="2030")

    @classmethod
    def _tv_category(cls, meta: Meta) -> str:
        if meta.anime:
            return "5070"
        return cls._quality_category(meta, uhd="5045", hd="5040", sd="5030")

    @classmethod
    def _adult_category(cls, meta: Meta) -> str:
        return "6040" if cls._is_hd_or_uhd(meta) else "6070"

    @staticmethod
    def _game_category(meta: Meta) -> str:
        platform = meta.platform.upper()
        if "SWITCH" in platform or "NSW" in platform:
            return "1035"
        if any(
            token in platform for token in ("PLAYSTATION", "PS", "XBOX", "WII")
        ):
            return "1000"
        return "4050"

    @staticmethod
    def _music_category(meta: Meta) -> str:
        fmt = meta.format.upper()
        if "AUDIOBOOK" in fmt or meta.audiobook:
            return "3030"
        if "FLAC" in fmt or "LOSSLESS" in fmt:
            return "3040"
        return "3010"

    @staticmethod
    def _book_category(meta: Meta) -> str:
        return "7030" if "COMIC" in str(meta.type or "").upper() else "7020"

    @classmethod
    def _quality_category(
        cls, meta: Meta, *, uhd: str, hd: str, sd: str
    ) -> str:
        quality = cls._quality_band(meta.resolution)
        return {"uhd": uhd, "hd": hd}.get(quality, sd)

    @staticmethod
    def _quality_band(resolution: str) -> str:
        value = resolution.lower()
        if value in {"2160p", "4320p", "8640p"}:
            return "uhd"
        if value in {"720p", "1080i", "1080p", "1440p"}:
            return "hd"
        return "sd"

    @classmethod
    def _is_hd_or_uhd(cls, meta: Meta) -> bool:
        return cls._quality_band(meta.resolution) in {"hd", "uhd"}

    async def _get_nfo_file(self, meta: Meta) -> tuple[str, bytes, str] | None:
        candidates = self._nfo_candidates(meta)
        candidate = next(
            (
                (path, filename)
                for path, filename in candidates
                if path.exists() and path.is_file()
            ),
            None,
        )
        if candidate is None:
            return None
        path, filename = candidate
        async with aiofiles.open(path, "rb") as handle:
            return filename, await handle.read(), "application/octet-stream"

    @staticmethod
    def _nfo_candidates(meta: Meta) -> list[tuple[Path, str]]:
        nfo_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        existing_nfos = [(path, path.name) for path in nfo_dir.glob("*.nfo")]
        if meta.scene:
            return [*existing_nfos, *existing_nfos]
        preferred = (
            (nfo_dir / "BD_SUMMARY_00.txt", "BDInfo.nfo")
            if meta.is_disc == "BDMV"
            else (nfo_dir / "MEDIAINFO_CLEANPATH.txt", "MediaInfo.nfo")
        )
        return [preferred, *existing_nfos]

    @staticmethod
    def _successful_response(response_text: str, includes_nfo: bool) -> bool:
        try:
            attributes = (
                json.loads(response_text)
                .get("response", {})
                .get("@attributes", {})
            )
        except json.JSONDecodeError:
            return False
        if attributes.get("API") != "OK" or attributes.get("REGISTER") != "OK":
            return False
        return not includes_nfo or attributes.get("NFO") == "OK"

    async def upload(self, meta: Meta) -> bool:
        status = meta.tracker_status.setdefault(self.tracker, {})
        if not await self._valid_upload_source(meta):
            status["status_message"] = (
                "data error: NZB file missing or password missing in header"
            )
            return False
        files, nfo_file = await self._submission_files(meta)
        if meta.debug:
            status["status_message"] = "Debug mode enabled, skipping upload."
            return True
        return await self._upload_with_error_handling(
            meta, files, nfo_file, status
        )

    async def _upload_with_error_handling(
        self,
        meta: Meta,
        files: dict[str, tuple[str, bytes, str]],
        nfo_file: tuple[str, bytes, str] | None,
        status: dict[str, Any],
    ) -> bool:
        try:
            response = await self._submit(meta, files)
            return await self._process_upload_response(
                meta, response, nfo_file, status
            )
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
        self,
        meta: Meta,
        response: httpx.Response,
        nfo_file: tuple[str, bytes, str] | None,
        status: dict[str, Any],
    ) -> bool:
        if not self._response_status_ok(response, status):
            return False
        if not self._successful_response(
            response.text, includes_nfo=nfo_file is not None
        ):
            status["status_message"] = (
                "data error: NZBGeek did not confirm the NZB submission."
            )
            return False
        await self._record_success(meta, status)
        return True

    async def _valid_upload_source(self, meta: Meta) -> bool:
        return bool(meta.nzb_path) and await self.common.check_nzb_file(
            self.tracker, meta
        )

    async def _submission_files(
        self, meta: Meta
    ) -> tuple[
        dict[str, tuple[str, bytes, str]], tuple[str, bytes, str] | None
    ]:
        nzb_path = str(meta.nzb_path)
        async with aiofiles.open(nzb_path, "rb") as handle:
            nzb_content = await handle.read()
        files: dict[str, tuple[str, bytes, str]] = {
            "nzb": (Path(nzb_path).name, nzb_content, "application/x-nzb")
        }
        nfo_file = await self._get_nfo_file(meta)
        if nfo_file is not None:
            files["nfo"] = nfo_file
        return files, nfo_file

    async def _submit(
        self, meta: Meta, files: dict[str, tuple[str, bytes, str]]
    ) -> httpx.Response:
        params = {"apikey": self.api_key, "cat": self.get_category_id(meta)}
        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            return await client.post(
                self.submit_url, params=params, files=files
            )

    @staticmethod
    def _response_status_ok(
        response: httpx.Response, status: dict[str, Any]
    ) -> bool:
        if response.status_code in {200, 201}:
            return True
        status["status_message"] = (
            f"data error: HTTP {response.status_code} - {response.text}"
        )
        return False

    async def _record_success(
        self, meta: Meta, status: dict[str, Any]
    ) -> None:
        status["status_message"] = "Upload successful"
        status["torrent_id"] = meta.basename_no_ext
        cache_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(
            cache_dir / f"{self.tracker}_upload_ok", "w", encoding="utf-8"
        ) as handle:
            await handle.write("ok")
