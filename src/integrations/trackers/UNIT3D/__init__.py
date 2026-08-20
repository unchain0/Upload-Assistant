# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import platform
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.media.artwork import is_valid_image_bytes
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder

type QueryValue = str | int | float | bool | None
type ParamsList = list[tuple[str, QueryValue]]


class _UploadRejectedError(RuntimeError):
    """Internal control-flow signal for a definitive tracker upload rejection."""


class UNIT3D:
    auth_type = "unit3d_api"
    supported_categories: tuple[str, ...] = ("TV", "MOVIE")
    tracker: str = ""
    banned_groups: tuple[str, ...] = ()
    base_url: str = ""
    pending_url: str = ""
    search_url: str = ""
    upload_url: str = ""
    download_url_hosts: tuple[str, ...] = ()
    max_torrent_download_size: int | None = None
    max_json_response_size: int | None = None
    follow_upload_redirects = True
    follow_search_redirects = True
    expose_remote_error_details = True

    def __init__(self, config: dict[str, Any], tracker_name: str):
        self.config = config
        self.tracker = tracker_name
        self.common = Common(config)
        self.tracker_config: dict[str, Any] = self.config["TRACKERS"].get(self.tracker, {})

        # Normalize announce_url: must be a non-empty string after stripping
        raw_announce = self.tracker_config.get("announce_url")
        self.announce_url = raw_announce.strip() if isinstance(raw_announce, str) else ""

        # Normalize api_key: must be a non-empty string after stripping
        raw_api_key = self.tracker_config.get("api_key")
        self.api_key = raw_api_key.strip() if isinstance(raw_api_key, str) else ""

    async def get_additional_checks(self, meta: Meta) -> bool:
        _meta = meta
        return True

    def _remote_error(self, value: object) -> str:
        if not self.expose_remote_error_details:
            return "[tracker response omitted]"
        return str(Redaction.redact_private_info(value))

    async def get_search_urls(self, meta: Meta, request_params: ParamsList) -> list[tuple[str, ParamsList, bool]]:
        _ = meta
        urls: list[tuple[str, ParamsList, bool]] = [(self.search_url, request_params, False)]
        if getattr(self, "pending_url", None):
            urls.append((self.pending_url, request_params, True))
        return urls

    def get_search_name(self, meta: Meta) -> str:
        return meta.title or meta.name

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        meta.setdefault("tracker_status", {})
        meta.tracker_status.setdefault(self.tracker, {})
        params = await self._search_request_params(meta)
        urls_to_check = await self.get_search_urls(meta, params)
        headers = self._search_headers()
        dupes: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=self.follow_search_redirects) as client:
            for url, request_params, check_pending in urls_to_check:
                response = await self._search_response(client, url, request_params, headers, check_pending)
                self._append_search_results(meta, response, check_pending, dupes)
        return dupes

    async def _search_request_params(self, meta: Meta) -> ParamsList:
        params = await self._search_params_dict(meta)
        return list(params.items())

    async def _search_params_dict(self, meta: Meta) -> dict[str, str]:
        if meta.category in {"MOVIE", "TV"}:
            return await self._video_search_params(meta)
        return await self._non_video_search_params(meta)

    async def _video_search_params(self, meta: Meta) -> dict[str, str]:
        search_name = str(meta.title or meta.name)
        params = {"name": self._video_search_name(meta, search_name), "perPage": "100"}
        if meta.tmdb is not None:
            params["tmdbId"] = str(meta.tmdb)
        else:
            params["categories[]"] = (await self.get_category_id(meta))["category_id"]
        return params

    @staticmethod
    def _video_search_name(meta: Meta, search_name: str) -> str:
        if meta.category != "TV":
            return search_name
        return f"{search_name} {meta.season}".strip()

    async def _non_video_search_params(self, meta: Meta) -> dict[str, str]:
        search_name = self._book_search_name(meta, self.get_search_name(meta))
        return {
            "name": search_name,
            "categories[]": (await self.get_category_id(meta))["category_id"],
            "perPage": "100",
        }

    @staticmethod
    def _book_search_name(meta: Meta, search_name: str) -> str:
        if meta.category != "BOOK" or ":" not in search_name:
            return search_name
        main_title = search_name.split(":", 1)[0].strip()
        return main_title if len(main_title.split()) >= 2 else search_name

    def _search_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.api_key}", "accept": "application/json"}

    async def _search_response(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: ParamsList,
        headers: dict[str, str],
        check_pending: bool,
    ) -> httpx.Response:
        logger.debug(f"{self.tracker}: Searching URL: {url} with params: {params} (pending={check_pending})")
        response = await self._request_search_response(client, url, params, headers)
        if self._search_redirect_rejected(response):
            raise ValueError("Tracker search redirect rejected")
        response.raise_for_status()
        return response

    async def _request_search_response(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: ParamsList,
        headers: dict[str, str],
    ) -> httpx.Response:
        if self.max_json_response_size is None:
            return await client.get(url=url, headers=headers, params=params)
        async with client.stream("GET", url, headers=headers, params=params) as streamed_response:
            return await self._bounded_response(streamed_response, self.max_json_response_size)

    def _search_redirect_rejected(self, response: httpx.Response) -> bool:
        return 300 <= response.status_code < 400 and not self.follow_search_redirects

    def _append_search_results(self, meta: Meta, response: httpx.Response, check_pending: bool, dupes: list[dict[str, Any]]) -> None:
        if response.status_code != 200:
            logger.info(f"{self.tracker}: [bold red]Failed to search torrents. HTTP Status: {response.status_code}")
            return
        for each in self._response_entries(response):
            result = self._search_result(meta, each, check_pending)
            if result is not None:
                dupes.append(result)

    @staticmethod
    def _response_entries(response: httpx.Response) -> list[dict[str, Any]]:
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        data = payload.get("data", [])
        if not isinstance(data, list):
            return []
        return [cast(dict[str, Any], item) for item in data if isinstance(item, dict)]

    def _search_result(self, meta: Meta, each: dict[str, Any], check_pending: bool) -> dict[str, Any] | None:
        if check_pending and not self._pending_matches(meta, each):
            return None
        attributes = self._result_attributes(each, check_pending)
        result = self._base_search_result(each, attributes, check_pending)
        if meta.is_disc:
            self._apply_disc_search_fields(result, attributes)
        else:
            result["files"] = self._file_names(attributes)
        return result

    @staticmethod
    def _pending_matches(meta: Meta, each: dict[str, Any]) -> bool:
        entry_tmdb = str(each.get("tmdb_id") or "")
        meta_tmdb = str(meta.tmdb) if meta.tmdb is not None else ""
        return entry_tmdb == meta_tmdb

    @staticmethod
    def _result_attributes(each: dict[str, Any], check_pending: bool) -> dict[str, Any]:
        value = each if check_pending else each.get("attributes", {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _base_search_result(self, each: dict[str, Any], attributes: dict[str, Any], check_pending: bool) -> dict[str, Any]:
        return {
            "name": attributes.get("name", ""),
            "size": attributes.get("size", 0),
            "files": [],
            "file_count": self._file_count(attributes),
            "trumpable": attributes.get("trumpable", False),
            "link": f"{self.base_url}/torrents/pending" if check_pending else attributes.get("details_link"),
            "download": attributes.get("download_link"),
            "id": each.get("id"),
            "type": attributes.get("type"),
            "res": attributes.get("resolution"),
            "internal": attributes.get("internal", False),
        }

    @staticmethod
    def _file_count(attributes: dict[str, Any]) -> int:
        files = attributes.get("files", [])
        return len(files) if isinstance(files, list) else 0

    @staticmethod
    def _file_names(attributes: dict[str, Any]) -> list[str]:
        files = attributes.get("files", [])
        if not isinstance(files, list):
            return []
        return [str(file["name"]) for file in files if isinstance(file, dict) and "name" in file]

    @staticmethod
    def _apply_disc_search_fields(result: dict[str, Any], attributes: dict[str, Any]) -> None:
        result["bd_info"] = attributes.get("bd_info", "")
        result["description"] = attributes.get("description", "")

    async def get_name(self, meta: Meta) -> dict[str, str]:
        return {"name": meta.name}

    async def get_description(self, meta: Meta) -> Any:
        return {
            "description": await DescriptionBuilder(self.tracker, self.config).general_description_generator(
                meta,
                mediainfo=False,
                nfo=False,
            )
        }

    async def get_mediainfo(self, meta: Meta) -> dict[str, str]:
        if meta.bdinfo or (meta.category in ["GAME", "BOOK"] and not meta.audiobook):
            mediainfo = ""
        else:
            async with aiofiles.open(f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/MEDIAINFO_CLEANPATH.txt", encoding="utf-8") as f:
                mediainfo = await f.read()
        return {"mediainfo": mediainfo}

    async def get_bdinfo(self, meta: Meta) -> dict[str, str]:
        if meta.bdinfo:
            async with aiofiles.open(f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/BD_SUMMARY_00.txt", encoding="utf-8") as f:
                bdinfo = await f.read()
        else:
            bdinfo = ""
        return {"bdinfo": bdinfo}

    async def get_category_id(self, meta: Meta, category: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        category_id = {
            "MOVIE": "1",
            "TV": "2",
        }
        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}
        if category:
            return {"category_id": category_id.get(category, "0")}
        meta_category = meta.category
        resolved_id = category_id.get(meta_category, "0")
        return {"category_id": resolved_id}

    async def get_type_id(self, meta: Meta, type: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = self._type_mapping()
        mode = self._mapping_mode(mapping, reverse=reverse, mapping_only=mapping_only)
        if mode is not None:
            return mode
        selected = type if type else str(meta.type or "")
        return {"type_id": mapping.get(selected, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {"DISC": "1", "REMUX": "2", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "ENCODE": "3", "DVDRIP": "3"}

    @staticmethod
    def _mapping_mode(mapping: dict[str, str], *, reverse: bool, mapping_only: bool) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        return {value: key for key, value in mapping.items()} if reverse else None

    async def get_resolution_id(self, meta: Meta, resolution: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }
        if mapping_only:
            return resolution_id
        if reverse:
            return {v: k for k, v in resolution_id.items()}
        if resolution:
            return {"resolution_id": resolution_id.get(resolution, "10")}
        meta_resolution = meta.resolution
        resolved_id = resolution_id.get(meta_resolution, "10")
        return {"resolution_id": resolved_id}

    async def get_anonymous(self, meta: Meta) -> dict[str, str]:
        anonymous = "0" if meta.anon == 0 and not self.tracker_config.get("anon", False) else "1"
        return {"anonymous": anonymous}

    async def get_additional_data(self, meta: Meta) -> dict[str, str]:
        # Used to add additional data if needed
        """
        data = {
            'mod_queue_opt_in': await self.get_flag(meta, 'modq'),
            'draft': await self.get_flag(meta, 'draft'),
        }
        """
        _meta = meta
        data: dict[str, str] = {}

        return data

    async def get_flag(self, meta: Meta, flag_name: str) -> str:
        config_flag = self.tracker_config.get(flag_name)
        if meta.get(flag_name, False):
            return "1"
        if config_flag is not None:
            return "1" if config_flag else "0"
        return "0"

    async def get_distributor_id(self, meta: Meta) -> dict[str, str]:
        distributor_id = await self.common.unit3d_distributor_ids(meta.distributor)
        if distributor_id:
            return {"distributor_id": distributor_id}

        return {}

    async def get_region_id(self, meta: Meta) -> dict[str, str]:
        region_id = await self.common.unit3d_region_ids(meta.region)
        if region_id:
            return {"region_id": region_id}

        return {}

    async def get_tmdb(self, meta: Meta) -> dict[str, str]:
        return {"tmdb": str(meta.tmdb) if meta.tmdb is not None else "0"}

    async def get_imdb(self, meta: Meta) -> dict[str, str]:
        imdb = meta.imdb_id if meta.category in ("TV", "MOVIE") else 0
        return {"imdb": str(imdb or 0)}

    async def get_tvdb(self, meta: Meta) -> dict[str, str]:
        tvdb = meta.tvdb_id if meta.category == "TV" else 0
        return {"tvdb": f"{tvdb}"}

    async def get_mal(self, meta: Meta) -> dict[str, str]:
        return {"mal": f"{meta.mal_id}"}

    async def get_igdb(self, meta: Meta) -> dict[str, str]:
        igdb = meta.igdb_id if meta.category == "GAME" else 0
        return {"igdb": f"{igdb}"}

    async def get_stream(self, meta: Meta) -> dict[str, str]:
        return {"stream": f"{meta.stream}"}

    async def get_sd(self, meta: Meta) -> dict[str, str]:
        return {"sd": f"{meta.sd}"}

    async def get_keywords(self, meta: Meta) -> dict[str, str]:
        """Build the UNIT3D keyword payload without exceeding VARCHAR(255)."""
        values: list[str] = []
        current_len = 0
        for raw_keyword in meta.keywords:
            keyword = str(raw_keyword).strip()
            if not keyword:
                continue
            next_len = self._keyword_payload_length(current_len, values, keyword)
            if next_len > 255:
                self._append_first_truncated_keyword(values, keyword)
                break
            values.append(keyword)
            current_len = next_len
        return {"keywords": ", ".join(values)}

    @staticmethod
    def _keyword_payload_length(current_len: int, values: list[str], keyword: str) -> int:
        separator = 2 if values else 0
        return current_len + separator + len(keyword)

    @staticmethod
    def _append_first_truncated_keyword(values: list[str], keyword: str) -> None:
        if not values and len(keyword) > 255:
            values.append(keyword[:255])

    async def get_personal_release(self, meta: Meta) -> dict[str, str]:
        personal_release = "1" if meta.personalrelease else "0"
        return {"personal_release": personal_release}

    async def get_internal(self, meta: Meta) -> Any:
        internal = "0"
        if self.tracker_config.get("internal", False) is True and meta.tag and (meta.tag[1:] in self.tracker_config.get("internal_groups", [])):
            internal = "1"

        return {"internal": internal}

    async def get_season_number(self, meta: Meta) -> dict[str, str]:
        data = {}
        if meta.category == "TV":
            data = {"season_number": f"{(meta.season_int if meta.season_int is not None else '0')}"}

        return data

    async def get_episode_number(self, meta: Meta) -> dict[str, str]:
        data = {}
        if meta.category == "TV":
            data = {"episode_number": f"{(meta.episode_int if meta.episode_int is not None else '0')}"}

        return data

    async def get_featured(self, meta: Meta) -> dict[str, str]:
        _meta = meta
        return {"featured": "0"}

    async def get_free(self, meta: Meta) -> dict[str, str]:
        free = "0"
        if meta.freeleech != 0:
            free = f"{(meta.freeleech if meta.freeleech is not None else '0')}"

        return {"free": free}

    async def get_doubleup(self, meta: Meta) -> dict[str, str]:
        _meta = meta
        return {"doubleup": "0"}

    async def get_sticky(self, meta: Meta) -> dict[str, str]:
        _meta = meta
        return {"sticky": "0"}

    async def get_data(self, meta: Meta) -> dict[str, str]:
        results = await asyncio.gather(
            self.get_name(meta),
            self.get_description(meta),
            self.get_mediainfo(meta),
            self.get_bdinfo(meta),
            self.get_category_id(meta),
            self.get_type_id(meta),
            self.get_resolution_id(meta),
            self.get_tmdb(meta),
            self.get_imdb(meta),
            self.get_tvdb(meta),
            self.get_mal(meta),
            self.get_igdb(meta),
            self.get_anonymous(meta),
            self.get_stream(meta),
            self.get_sd(meta),
            self.get_keywords(meta),
            self.get_personal_release(meta),
            self.get_internal(meta),
            self.get_season_number(meta),
            self.get_episode_number(meta),
            self.get_featured(meta),
            self.get_free(meta),
            self.get_doubleup(meta),
            self.get_sticky(meta),
            self.get_additional_data(meta),
            self.get_region_id(meta),
            self.get_distributor_id(meta),
        )

        merged: dict[str, str] = {}
        for r in results:
            merged.update(r)

        # Handle exclusive flag centrally for all UNIT3D trackers
        # Priority: meta.exclusive > tracker config > default (not set)
        exclusive_flag = None
        if meta.exclusive or self.tracker_config.get("exclusive", False):
            exclusive_flag = "1"
        if exclusive_flag:
            merged["exclusive"] = exclusive_flag

        return merged

    async def get_image_file(self, image_path: str | Path, max_size: int | None = None) -> tuple[str, bytes, str] | None:
        """Read an image unchanged and return it with a content type verified from its signature."""
        path = Path(image_path)
        image_bytes = await self._read_image_bytes(path, max_size)
        if image_bytes is None:
            return None
        if not is_valid_image_bytes(image_bytes):
            logger.info(f"{self.tracker}: [yellow]Invalid or unsupported image: {path}[/yellow]")
            return None
        image_type = self._image_type(image_bytes)
        if image_type is None:
            logger.info(f"{self.tracker}: [yellow]Unsupported image format: {path}[/yellow]")
            return None
        extension, media_type = image_type
        return f"{path.stem}{extension}", image_bytes, media_type

    async def _read_image_bytes(self, path: Path, max_size: int | None) -> bytes | None:
        try:
            if not self._image_path_allowed(path, max_size):
                return None
            async with aiofiles.open(path, "rb") as handle:
                return await handle.read()
        except OSError as error:
            logger.info(f"{self.tracker}: [yellow]Failed to read image {path}: {error}[/yellow]")
            return None

    @staticmethod
    def _image_path_allowed(path: Path, max_size: int | None) -> bool:
        if not path.is_file():
            return False
        return max_size is None or path.stat().st_size <= max_size

    @classmethod
    def _image_type(cls, image_bytes: bytes) -> tuple[str, str] | None:
        simple = cls._simple_image_type(image_bytes)
        if simple is not None:
            return simple
        return (".webp", "image/webp") if cls._is_webp(image_bytes) else None

    @staticmethod
    def _simple_image_type(image_bytes: bytes) -> tuple[str, str] | None:
        if image_bytes.startswith(b"\xff\xd8\xff"):
            return ".jpg", "image/jpeg"
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png", "image/png"
        if image_bytes.startswith((b"GIF87a", b"GIF89a")):
            return ".gif", "image/gif"
        return None

    @staticmethod
    def _is_webp(image_bytes: bytes) -> bool:
        return image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP"

    async def get_additional_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        files: dict[str, tuple[str, bytes, str]] = {}
        nfo_path = self._first_nfo_path(meta)
        if nfo_path is not None:
            files["nfo"] = await self._nfo_file(nfo_path)
        if meta.category not in {"MOVIE", "TV", "GAME"}:
            await self._append_artwork_files(meta, files)
        return files

    def _first_nfo_path(self, meta: Meta) -> Path | None:
        game_nfo = self._game_nfo_path(meta)
        if game_nfo is not None:
            return game_nfo
        temp_nfo = self._temporary_nfo_path(meta)
        if temp_nfo is not None:
            return temp_nfo
        return self._kept_nfo_path(meta)

    def _game_nfo_path(self, meta: Meta) -> Path | None:
        if meta.category != "GAME":
            return None
        source_dir = self._game_source_dir(meta.path)
        candidates = [meta.scene_nfo_file, *self._filelist_values(meta.filelist)]
        return self._first_resolved_nfo(candidates, source_dir)

    @staticmethod
    def _game_source_dir(value: Any) -> Path:
        source_path = Path(str(value or ""))
        return source_path if source_path.is_dir() else source_path.parent

    @classmethod
    def _first_resolved_nfo(cls, candidates: list[Any], source_dir: Path) -> Path | None:
        return next((path for candidate in candidates if (path := cls._resolved_nfo_candidate(candidate, source_dir)) is not None), None)

    @staticmethod
    def _filelist_values(value: Any) -> list[Any]:
        return list(value) if isinstance(value, (list, tuple, set)) else []

    @classmethod
    def _resolved_nfo_candidate(cls, candidate: Any, source_dir: Path) -> Path | None:
        path = Path(str(candidate or ""))
        if path.suffix.lower() != ".nfo":
            return None
        resolved = cls._resolved_candidate_path(path, source_dir)
        return resolved if resolved.is_file() else None

    @staticmethod
    def _resolved_candidate_path(path: Path, source_dir: Path) -> Path:
        if path.is_file() or path.is_absolute():
            return path
        return source_dir / path

    @staticmethod
    def _temporary_nfo_path(meta: Meta) -> Path | None:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        return next(root.glob("*.nfo"), None)

    @staticmethod
    def _kept_nfo_path(meta: Meta) -> Path | None:
        if not meta.keep_nfo or not (meta.keep_folder or meta.isdir):
            return None
        search_dir = Path(str(meta.path)).parent
        return next(search_dir.glob("*.nfo"), None)

    @staticmethod
    async def _nfo_file(path: Path) -> tuple[str, bytes, str]:
        async with aiofiles.open(path, "rb") as handle:
            return path.name, await handle.read(), "text/plain"

    async def _append_artwork_files(self, meta: Meta, files: dict[str, tuple[str, bytes, str]]) -> None:
        await self._append_image_file(files, "torrent-cover", meta.artwork_path)
        await self._append_image_file(files, "torrent-banner", meta.artwork_banner_path)

    async def _append_image_file(self, files: dict[str, tuple[str, bytes, str]], key: str, image_path: Any) -> None:
        if not image_path:
            return
        image_file = await self.get_image_file(str(image_path))
        if image_file is not None:
            files[key] = image_file

    async def get_upload_torrent_filename(self, meta: Meta) -> str:
        return await self.common.get_torrent_filename(meta, self.tracker_config)

    async def upload(self, meta: Meta) -> bool:
        data, files, headers = await self._upload_request_parts(meta)
        if meta.debug:
            return await self._debug_upload(meta, data)
        uploaded = await self._post_with_retries(meta, data, files, headers)
        if uploaded is None:
            return False
        return await self._download_uploaded_torrent(meta, headers, uploaded)

    async def _upload_request_parts(
        self,
        meta: Meta,
    ) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]], dict[str, str]]:
        data = await self.get_data(meta)
        files = await self._upload_files(meta)
        return data, files, self._upload_headers(meta)

    async def _upload_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        torrent_filename = await self.get_upload_torrent_filename(meta)
        torrent_path = release_temp_dir(meta.base_dir, meta.uuid) / f"{torrent_filename}.torrent"
        async with aiofiles.open(torrent_path, "rb") as handle:
            torrent_bytes = await handle.read()
        files = {"torrent": ("torrent.torrent", torrent_bytes, "application/x-bittorrent")}
        files.update(await self.get_additional_files(meta))
        return files

    def _upload_headers(self, meta: Meta) -> dict[str, str]:
        return {
            "User-Agent": f"{meta.ua_name} {meta.current_version} ({platform.system()} {platform.release()})",
            "authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
        }

    async def _debug_upload(self, meta: Meta, data: dict[str, str]) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = f"Debug mode enabled, not uploading: {self.tracker}."
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _post_with_retries(
        self,
        meta: Meta,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        headers: dict[str, str],
    ) -> str | None:
        timeout = 40.0
        response_data: dict[str, Any] = {}
        for attempt in range(2):
            try:
                response_data = await self._post_upload_attempt(meta, data, files, headers, timeout)
                return str(response_data.get("data") or "")
            except Exception as error:
                retry, timeout = await self._upload_error_decision(meta, error, attempt, timeout, response_data)
                if not retry:
                    return None
        return None

    async def _post_upload_attempt(
        self,
        meta: Meta,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        headers: dict[str, str],
        request_timeout: float,
    ) -> dict[str, Any]:
        response = await self._upload_response(data, files, headers, request_timeout)
        if self._upload_redirect_rejected(response):
            meta.tracker_status[self.tracker]["status_message"] = "data error: Upload redirect rejected"
            raise _UploadRejectedError("Upload redirect rejected")
        response.raise_for_status()
        response_data = self._json_object(response)
        self._ensure_api_success(meta, response_data)
        await self._record_upload_response(meta, response_data)
        return response_data

    async def _upload_response(
        self,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        headers: dict[str, str],
        request_timeout: float,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=request_timeout, follow_redirects=self.follow_upload_redirects) as client:
            if self.max_json_response_size is None:
                return await client.post(url=self.upload_url, files=files, data=data, headers=headers)
            async with client.stream("POST", self.upload_url, files=files, data=data, headers=headers) as streamed_response:
                return await self._bounded_response(streamed_response, self.max_json_response_size)

    def _upload_redirect_rejected(self, response: httpx.Response) -> bool:
        return 300 <= response.status_code < 400 and not self.follow_upload_redirects

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        raw = cast(object, response.json())
        if not isinstance(raw, dict):
            raise ValueError("Tracker response must be a JSON object")
        return cast(dict[str, Any], raw)

    def _ensure_api_success(self, meta: Meta, response_data: dict[str, Any]) -> None:
        if response_data.get("success"):
            return
        error_msg = self._remote_error(response_data.get("message", "Unknown error"))
        meta.tracker_status[self.tracker]["status_message"] = f"API error: {error_msg}"
        logger.info(f"{self.tracker}: [yellow]Upload to {self.tracker} failed: {error_msg}[/yellow]")
        raise _UploadRejectedError(f"Tracker API rejected upload: {error_msg}")

    async def _record_upload_response(self, meta: Meta, response_data: dict[str, Any]) -> None:
        processed = await self.process_response_data(response_data)
        meta.tracker_status[self.tracker]["status_message"] = processed if self.expose_remote_error_details else "Upload successful"
        meta.tracker_status[self.tracker]["torrent_id"] = await self.get_torrent_id(response_data)

    async def _upload_error_decision(
        self,
        meta: Meta,
        error: Exception,
        attempt: int,
        request_timeout: float,
        response_data: dict[str, Any],
    ) -> tuple[bool, float]:
        if isinstance(error, _UploadRejectedError):
            return False, request_timeout
        if isinstance(error, httpx.HTTPStatusError):
            return await self._http_error_decision(meta, error, attempt, request_timeout)
        if isinstance(error, httpx.TimeoutException):
            return await self._timeout_error_decision(meta, attempt, request_timeout)
        if isinstance(error, httpx.RequestError):
            return await self._request_error_decision(meta, error, attempt, request_timeout, response_data)
        self._value_error_status(meta, error)
        return False, request_timeout

    async def _http_error_decision(
        self,
        meta: Meta,
        error: httpx.HTTPStatusError,
        attempt: int,
        request_timeout: float,
    ) -> tuple[bool, float]:
        status_code = error.response.status_code
        if status_code in {403, 302}:
            self._auth_error_status(meta, error)
            return False, request_timeout
        if status_code in {401, 404, 422}:
            return self._client_http_error_decision(meta, error, attempt, request_timeout)
        return await self._retryable_http_error_decision(meta, error, attempt, request_timeout)

    def _auth_error_status(self, meta: Meta, error: httpx.HTTPStatusError) -> None:
        code = error.response.status_code
        if code == 403:
            message = f"data error: Forbidden (403). This may indicate that you do not have upload permission. {self._remote_error(error.response.text)}"
        else:
            message = f"data error: Redirect (302). This may indicate a problem with authentication. {self._remote_error(error.response.text)}"
        meta.tracker_status[self.tracker]["status_message"] = message

    def _client_http_error_decision(
        self,
        meta: Meta,
        error: httpx.HTTPStatusError,
        attempt: int,
        timeout: float,
    ) -> tuple[bool, float]:
        if self._is_duplicate_name_error(error.response.text):
            self._duplicate_upload_status(meta)
            return False, timeout
        meta.tracker_status[self.tracker]["status_message"] = f"data error: HTTP {error.response.status_code} - {self._remote_error(error.response.text)}"
        return attempt == 0, timeout

    def _duplicate_upload_status(self, meta: Meta) -> None:
        status = meta.tracker_status[self.tracker]
        status["dupe"] = True
        status["upload"] = False
        status["status_message"] = "Duplicate detected during upload: the release name or info hash already exists on the tracker."

    async def _retryable_http_error_decision(
        self,
        meta: Meta,
        error: httpx.HTTPStatusError,
        attempt: int,
        request_timeout: float,
    ) -> tuple[bool, float]:
        if attempt == 0:
            await self._retry_delay(f"HTTP {error.response.status_code} error", attempt, request_timeout)
            return True, request_timeout
        self._final_http_error_status(meta, error)
        return False, request_timeout

    def _final_http_error_status(self, meta: Meta, error: httpx.HTTPStatusError) -> None:
        code = error.response.status_code
        if code == 520:
            message = "data error: Error (520). This is probably a cloudflare issue on the tracker side."
        else:
            message = f"data error: HTTP {code} - {self._remote_error(error.response.text)}"
        meta.tracker_status[self.tracker]["status_message"] = message

    async def _timeout_error_decision(self, meta: Meta, attempt: int, request_timeout: float) -> tuple[bool, float]:
        if attempt == 0:
            next_timeout = request_timeout * 1.5
            await self._retry_delay("Request timed out", attempt, next_timeout)
            return True, next_timeout
        meta.tracker_status[self.tracker]["status_message"] = "data error: Request timed out after multiple attempts"
        return False, request_timeout

    async def _request_error_decision(
        self,
        meta: Meta,
        error: httpx.RequestError,
        attempt: int,
        request_timeout: float,
        response_data: dict[str, Any],
    ) -> tuple[bool, float]:
        if attempt == 0:
            await self._retry_delay("Request error", attempt, request_timeout)
            return True, request_timeout
        meta.tracker_status[self.tracker]["status_message"] = f"data error: Unable to upload. Error: {error}.\nResponse: {self._remote_error(response_data)}"
        return False, request_timeout

    async def _retry_delay(self, label: str, attempt: int, request_timeout: float) -> None:
        retry_delay = 5
        logger.info(f"{self.tracker}: [yellow]{label}, retrying in {retry_delay} seconds with {request_timeout}s timeout... (attempt {attempt + 1}/2)[/yellow]")
        await asyncio.sleep(retry_delay)

    def _value_error_status(self, meta: Meta, error: Exception) -> None:
        meta.tracker_status[self.tracker]["status_message"] = f"data error: Invalid JSON response from {self.tracker}. Error: {error}"

    async def _download_uploaded_torrent(self, meta: Meta, headers: dict[str, str], download_url: str) -> bool:
        if not download_url:
            meta.tracker_status[self.tracker]["status_message"] = "data error: Upload succeeded but the API returned no torrent download URL"
            return False
        downloaded = await self.common.download_tracker_torrent(
            meta,
            self.tracker,
            headers=headers,
            downurl=download_url,
            allowed_hosts=self.download_url_hosts,
            max_size=self.max_torrent_download_size,
        )
        if self.download_url_hosts and downloaded is None:
            meta.tracker_status[self.tracker]["status_message"] = "data error: Upload succeeded but the torrent download was rejected or failed"
            return False
        return True

    @staticmethod
    def _is_duplicate_name_error(response_text: str) -> bool:
        normalized = str(response_text or "").casefold()
        if re.search(r"\bsame[\s_-]+info[\s_-]*hash\b", normalized):
            return True
        return '"name"' in normalized and any(
            phrase in normalized for phrase in ("already been taken", "already exists", "já se encontra registado", "ja se encontra registado")
        )

    @staticmethod
    async def _bounded_response(response: httpx.Response, max_size: int) -> httpx.Response:
        content_length = response.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > max_size:
            raise ValueError("Tracker JSON response exceeds the configured size limit")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > max_size:
                raise ValueError("Tracker JSON response exceeds the configured size limit")
        return httpx.Response(response.status_code, headers=response.headers, content=bytes(body), request=response.request)

    async def get_torrent_id(self, response_data: dict[str, Any]) -> str:
        """Matches /12345.abcde and returns 12345"""
        torrent_id = ""
        try:
            match = re.search(r"/(\d+)\.", response_data["data"])
            if match:
                torrent_id = match.group(1)
        except IndexError, KeyError:
            logger.info(f"{self.tracker}: Could not parse torrent_id from response data.")
        return torrent_id

    async def process_response_data(self, response_data: dict[str, Any]) -> str:
        """Returns the success message from the response data as a string."""
        if response_data.get("success") is True:
            return str(response_data.get("message", "Upload successful"))

        # For non-success responses, format as string
        error_msg = response_data.get("message", "")
        if error_msg:
            return f"API response: {error_msg}"
        return f"API response: {response_data}"
