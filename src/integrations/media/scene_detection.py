# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx
from bs4 import BeautifulSoup
from bs4.element import AttributeValueList

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger


class SceneManager:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.default_config = cast(
            Mapping[str, Any], config.get("DEFAULT", {})
        )
        if not isinstance(self.default_config, dict):
            raise ValueError("'DEFAULT' config section must be a dict")

    def _attr_to_string(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, AttributeValueList):
            return " ".join(value)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _file_base(video: str, meta: Meta) -> tuple[str, bool]:
        base = Path(video).name
        match = re.match(r"^(.+)\.[a-zA-Z0-9]{3,4}$", base)
        if match is None:
            return base, False
        if meta.is_disc and not meta.keep_folder:
            return base, False
        base = match.group(1)
        return base, base.islower()

    @staticmethod
    def _game_base(meta: Meta, base: str, is_lower: bool) -> tuple[str, bool]:
        if meta.category != "GAME" or not meta.isdir:
            return base, is_lower
        folder_name = Path(str(meta.path)).name
        if not folder_name:
            return base, is_lower
        return folder_name, folder_name.islower()

    @classmethod
    def _scene_base(cls, video: str, meta: Meta) -> tuple[str, bool]:
        base, is_lower = cls._file_base(video, meta)
        return cls._game_base(meta, base, is_lower)

    @staticmethod
    def _cache_dirs(meta: Meta) -> tuple[Path, Path]:
        cache_dir = Path(meta.base_dir) / "tmp" / meta.uuid / "srrdb"
        search_dir = cache_dir / "search"
        details_dir = cache_dir / "details"
        search_dir.mkdir(parents=True, exist_ok=True)
        details_dir.mkdir(parents=True, exist_ok=True)
        return search_dir, details_dir

    @staticmethod
    async def _read_json_cache(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return cast(dict[str, Any], json.loads(text))
        except Exception:
            return None

    @staticmethod
    async def _write_json_cache(path: Path, value: dict[str, Any]) -> None:
        text = json.dumps(value)
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")

    async def _search_srrdb(
        self,
        client: httpx.AsyncClient,
        base: str,
        search_cache_dir: Path,
    ) -> dict[str, Any] | None:
        quoted_base = urllib.parse.quote(base)
        cache_file = search_cache_dir / f"{quoted_base}.json"
        cached = await self._read_json_cache(cache_file)
        if cached is not None:
            logger.debug(f"[cyan]SRRDB: Using cached search for {base}")
            return cached
        url = f"https://api.srrdb.com/v1/search/r:{quoted_base}"
        logger.debug(f"Using SRRDB url: {url}")
        try:
            response = await client.get(url, timeout=30.0)
            if response.status_code != 200:
                return None
            data = cast(dict[str, Any], response.json())
            await self._write_json_cache(cache_file, data)
            return data
        except Exception as exc:
            logger.info(f"[yellow]SRRDB: Search request failed: {exc}")
            return None

    @staticmethod
    def _has_results(data: dict[str, Any] | None) -> bool:
        if not data:
            return False
        return int(data.get("resultsCount", 0)) > 0

    @staticmethod
    def _first_result(data: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], data["results"][0])

    @staticmethod
    def _mark_missing_tag(meta: Meta, is_all_lowercase: bool) -> None:
        if is_all_lowercase and not meta.tag:
            meta.we_need_tag = True

    @staticmethod
    def _matched_imdb(
        first_result: dict[str, Any], meta: Meta, current_imdb: int | None
    ) -> int | None:
        imdb_raw = first_result.get("imdbId")
        if not imdb_raw:
            return current_imdb
        imdb_str = str(imdb_raw)
        if imdb_str.isdigit() and not meta.imdb_manual:
            return int(imdb_str)
        return None

    @classmethod
    def _apply_primary_match(
        cls,
        meta: Meta,
        first_result: dict[str, Any],
        is_all_lowercase: bool,
        imdb: int | None,
    ) -> tuple[str, int | None]:
        release = str(first_result["release"])
        meta.scene_name = release
        cls._mark_missing_tag(meta, is_all_lowercase)
        return f"{release}.mkv", cls._matched_imdb(first_result, meta, imdb)

    @staticmethod
    def _safe_release_name(release: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(release).name).strip(
            "._"
        )
        return sanitized or "scene_release"

    async def _release_details(
        self,
        client: httpx.AsyncClient,
        release: str,
        safe_release: str,
        details_cache_dir: Path,
    ) -> dict[str, Any] | None:
        cache_file = details_cache_dir / f"{safe_release}.json"
        cached = await self._read_json_cache(cache_file)
        if cached is not None:
            return cached
        url = f"https://api.srrdb.com/v1/details/{release}"
        response = await client.get(url, timeout=30.0)
        if response.status_code != 200:
            return None
        details = cast(dict[str, Any], response.json())
        await self._write_json_cache(cache_file, details)
        return details

    @staticmethod
    def _safe_nfo_stem(name: str, fallback: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._")
        return stem or fallback

    @classmethod
    def _details_nfo_name(
        cls, details: dict[str, Any] | None, fallback: str
    ) -> str:
        if not details:
            return fallback
        resolved = fallback
        try:
            for file_info in details.get("files", []):
                name = file_info["name"]
                if name.endswith(".nfo"):
                    resolved = cls._safe_nfo_stem(name, resolved)
        except KeyError, ValueError:
            return resolved
        return resolved

    @staticmethod
    async def _nfo_file_result(
        client: httpx.AsyncClient, url: str, path: Path
    ) -> tuple[bool, bool]:
        if path.exists():
            return True, False
        response = await client.get(url, timeout=30.0)
        if response.status_code != 200:
            return False, False
        await asyncio.to_thread(path.write_bytes, response.content)
        return True, True

    @staticmethod
    def _set_nfo_flags(meta: Meta) -> None:
        meta.nfo = True
        meta.auto_nfo = True

    @classmethod
    def _apply_primary_nfo_result(
        cls,
        meta: Meta,
        path: Path,
        success: bool,
        downloaded: bool,
    ) -> None:
        meta.scene_nfo_file = str(path)
        if not success:
            logger.info("[yellow]NFO file not available for download.")
            return
        cls._set_nfo_flags(meta)
        if downloaded:
            logger.debug(f"[green]NFO downloaded to {path}")

    async def _download_primary_nfo(
        self,
        client: httpx.AsyncClient,
        meta: Meta,
        first_result: dict[str, Any],
        details_cache_dir: Path,
    ) -> None:
        if meta.nfo:
            return
        if first_result.get("hasNFO") != "yes":
            return
        try:
            release = str(first_result["release"])
            safe_release = self._safe_release_name(release)
            details = await self._release_details(
                client, release, safe_release, details_cache_dir
            )
            nfo_name = self._details_nfo_name(details, safe_release.lower())
            url = (
                f"https://www.srrdb.com/download/file/{release}/{nfo_name}.nfo"
            )
            save_dir = Path(meta.base_dir) / "tmp" / meta.uuid
            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / f"{nfo_name}.nfo"
            success, downloaded = await self._nfo_file_result(
                client, url, path
            )
            self._apply_primary_nfo_result(meta, path, success, downloaded)
        except Exception as exc:
            logger.info(f"[yellow]Failed to download NFO file: {exc}")

    @staticmethod
    def _log_primary_no_match(
        meta: Meta, response_json: dict[str, Any] | None
    ) -> None:
        if meta.debug and response_json:
            logger.info("[yellow]SRRDB: No match found")

    async def _primary_search(
        self,
        client: httpx.AsyncClient,
        video: str,
        meta: Meta,
        imdb: int | None,
        base: str,
        is_all_lowercase: bool,
        search_cache_dir: Path,
        details_cache_dir: Path,
    ) -> tuple[str, bool, int | None]:
        response_json = await self._search_srrdb(
            client, base, search_cache_dir
        )
        if not self._has_results(response_json):
            self._log_primary_no_match(meta, response_json)
            return video, False, imdb
        response_data = cast(dict[str, Any], response_json)
        first_result = self._first_result(response_data)
        video, imdb = self._apply_primary_match(
            meta, first_result, is_all_lowercase, imdb
        )
        await self._download_primary_nfo(
            client, meta, first_result, details_cache_dir
        )
        return video, True, imdb

    @staticmethod
    def _lower_terms(meta: Meta) -> tuple[str, str] | None:
        filename = meta.filename
        tag = meta.tag
        name = (
            filename.replace(" ", ".") if isinstance(filename, str) else None
        )
        group = tag.replace("-", "") if isinstance(tag, str) else None
        if not name or not group:
            return None
        return name, group

    @staticmethod
    def _lower_result_matches(
        first_result: dict[str, Any], meta: Meta
    ) -> bool:
        imdb_raw = first_result.get("imdbId")
        if not imdb_raw:
            return False
        return (
            str(imdb_raw) == str(meta.imdb_id).zfill(7) and meta.imdb_id != 0
        )

    @classmethod
    def _apply_lower_nfo_result(
        cls,
        meta: Meta,
        path: Path,
        success: bool,
        downloaded: bool,
    ) -> None:
        if not success:
            return
        cls._set_nfo_flags(meta)
        if downloaded:
            logger.info(f"[green]NFO downloaded to {path}")

    async def _download_lower_nfo(
        self,
        client: httpx.AsyncClient,
        meta: Meta,
        first_result: dict[str, Any],
        quoted_base: str,
    ) -> None:
        if meta.nfo:
            return
        if first_result.get("hasNFO") != "yes":
            return
        try:
            release = str(first_result["release"])
            release_lower = release.lower()
            url = f"https://www.srrdb.com/download/file/{release}/{quoted_base}.nfo"
            save_dir = Path(meta.base_dir) / "tmp" / meta.uuid
            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / f"{release_lower}.nfo"
            success, downloaded = await self._nfo_file_result(
                client, url, path
            )
            self._apply_lower_nfo_result(meta, path, success, downloaded)
        except Exception as exc:
            logger.info(f"[yellow]Failed to download NFO file: {exc}")

    async def _lower_search(
        self,
        client: httpx.AsyncClient,
        video: str,
        meta: Meta,
        quoted_base: str,
        imdb: int | None,
    ) -> tuple[str, bool, int | None]:
        terms = self._lower_terms(meta)
        if terms is None:
            logger.debug(
                "[yellow]SRRDB: Missing name or tag for lower/tag search"
            )
            return video, False, imdb
        name, tag = terms
        url = f"https://api.srrdb.com/v1/search/start:{name}/group:{tag}"
        logger.debug(f"Using SRRDB url: {url}")
        try:
            response = await client.get(url, timeout=10.0)
            response_json = cast(dict[str, Any], response.json())
            if not self._has_results(response_json):
                logger.debug(
                    "[yellow]SRRDB: No match found with lower/tag search"
                )
                return video, False, imdb
            first_result = self._first_result(response_json)
            if not self._lower_result_matches(first_result, meta):
                return video, False, imdb
            meta.scene = True
            release_name = str(first_result["release"])
            await self._download_lower_nfo(
                client, meta, first_result, quoted_base
            )
            return release_name, True, imdb
        except Exception as exc:
            logger.info(f"[yellow]SRRDB search failed: {exc}")
            return video, False, imdb

    async def _search_scene(
        self,
        client: httpx.AsyncClient,
        video: str,
        meta: Meta,
        imdb: int | None,
        lower: bool,
        base: str,
        is_all_lowercase: bool,
        search_cache_dir: Path,
        details_cache_dir: Path,
    ) -> tuple[str, bool, int | None]:
        if not meta.scene and not lower:
            return await self._primary_search(
                client,
                video,
                meta,
                imdb,
                base,
                is_all_lowercase,
                search_cache_dir,
                details_cache_dir,
            )
        if lower:
            return await self._lower_search(
                client, video, meta, urllib.parse.quote(base), imdb
            )
        return video, False, imdb

    async def _predb_fallback(
        self, meta: Meta, video: str, scene: bool
    ) -> bool:
        if scene:
            return True
        if not bool(self.default_config.get("check_predb", False)):
            return False
        logger.debug("[yellow]SRRDB: No scene match found, checking predb")
        return await self.predb_check(meta, video)

    @staticmethod
    def _debug_start(meta: Meta) -> float:
        if meta.debug:
            return time.time()
        return 0.0

    @staticmethod
    def _log_debug_duration(meta: Meta, started_at: float) -> None:
        if not meta.debug:
            return
        elapsed = time.time() - started_at
        logger.debug(f"Scene data processed in {elapsed:.2f} seconds")

    async def is_scene(
        self,
        video: str,
        meta: Meta,
        imdb: int | None = None,
        lower: bool = False,
    ) -> tuple[str, bool, int | None]:
        started_at = self._debug_start(meta)
        base, is_all_lowercase = self._scene_base(video, meta)
        search_cache_dir, details_cache_dir = self._cache_dirs(meta)
        async with httpx.AsyncClient() as client:
            video, scene, imdb = await self._search_scene(
                client,
                video,
                meta,
                imdb,
                lower,
                base,
                is_all_lowercase,
                search_cache_dir,
                details_cache_dir,
            )
        scene = await self._predb_fallback(meta, video, scene)
        self._log_debug_duration(meta, started_at)
        return video, scene, imdb

    async def predb_check(self, meta: Meta, video: str) -> bool:
        url = f"https://predb.pw/search.php?search={urllib.parse.quote(Path(video).name)}"
        logger.debug(f"Using predb url: {url}")
        try:
            response = await self._predb_response(url)
            if response.status_code != 200:
                logger.info(
                    f"[red]Predb: Error {response.status_code} while checking"
                )
                return False
            return self._match_predb_response(meta, video, response.text)
        except httpx.RequestError as e:
            logger.info(f"[red]Predb: Request failed: {e}")
            return False
        except Exception as e:
            logger.info(f"[yellow]Predb error: {e}")
            return False

    @staticmethod
    async def _predb_response(url: str) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            return await client.get(url, timeout=10.0)

    def _match_predb_response(
        self, meta: Meta, video: str, html_text: str
    ) -> bool:
        soup = BeautifulSoup(html_text, "lxml")
        video_base = Path(video).name.lower()
        for row in soup.select("table.zebra-striped tbody tr"):
            release = self._predb_row_release(row)
            if release is None:
                continue
            release_name, group = release
            logger.debug(
                f"[yellow]Predb: Checking {release_name.lower()} against {video_base}"
            )
            if release_name.lower() == video_base:
                self._apply_predb_match(meta, release_name, group)
                return True
        logger.info("[yellow]Predb: No match found")
        return False

    def _predb_row_release(self, row: Any) -> tuple[str, str] | None:
        tds = row.find_all("td")
        if len(tds) < 3:
            return None
        release_a = tds[2].find("a", title=True)
        if release_a is None:
            return None
        release_attr = self._attr_to_string(release_a.get("title")).strip()
        if not release_attr:
            return None
        return release_attr, self._predb_group(tds)

    def _predb_group(self, tds: list[Any]) -> str:
        if len(tds) < 4:
            return ""
        group_a = tds[3].find("a")
        return (
            ""
            if group_a is None
            else self._attr_to_string(group_a.get_text()).strip()
        )

    @staticmethod
    def _apply_predb_match(meta: Meta, release_name: str, group: str) -> None:
        meta.scene_name = release_name
        meta.tag = SceneManager._predb_group_tag(group)
        logger.info("[green]Predb: Match found")

    @staticmethod
    def _predb_group_tag(group: str) -> str:
        if not group:
            return ""
        return group if group.startswith("-") else f"-{group}"
