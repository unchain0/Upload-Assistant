# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import contextlib
import json
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
import langcodes
from defusedxml import ElementTree

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.USENET.search_helpers import (
    build_newznab_search_query,
    parse_newznab_dupes,
)

Config = dict[str, Any]


class Curupira:
    """
    CRP Private Torrent Tracker
    """

    base_url = "https://curupira.cc"

    auth_type = "other_api"
    tracker = "CURUPIRA"
    display_name = "Curupira"
    banned_groups = (
        "4K4U",
        "afm72",
        "Alcaide_Kira",
        "AROMA",
        "ASM",
        "Bandi",
        "BiTOR",
        "BLUDV",
        "Bluespots",
        "BOLS",
        "CaNNIBal",
        "Comando",
        "d3g",
        "DepraveD",
        "EMBER",
        "Emmid",
        "FGT",
        "FreetheFish",
        "Garshasp",
        "Ghost",
        "Grym",
        "HDS",
        "Hi10",
        "HiQVE",
        "Hiro360",
        "ImE",
        "ION10",
        "iVy",
        "Judas",
        "LAMA",
        "Langbard",
        "Lapumia",
        "LION",
        "MeGusta",
        "Memoriadatv",
        "MONOLITH",
        "MRCS",
        "NaNi",
        "Natty",
        "nikt0",
        "OEPlus",
        "OFT",
        "OsC",
        "Panda",
        "PANDEMONiUM",
        "PHOCiS",
        "PiRaTeS",
        "PYC",
        "r00t",
        "Ralphy",
        "RARBG",
        "RetroPeeps",
        "RZeroX",
        "S74Ll10n",
        "SAMPA",
        "Sicario",
        "SiCFoI",
        "Silence",
        "SkipTT",
        "SM737",
        "SPDVD",
        "STUTTERSHIT",
        "SWTYBLZ",
        "t3nzin",
        "TAoE",
        "TEKNO3D",
        "Telly",
        "TGx",
        "Tigole",
        "TSP",
        "TSPxL",
        "TWA",
        "UnKn0wn",
        "VXT",
        "Vyndros",
        "W32",
        "Will1869",
        "x0r",
        "YIFY",
        "YTS.MX",
        "YTS",
    )
    upload_url = f"{base_url}/v1/releases"
    torrent_url = f"{base_url}/releases/"
    supported_categories = ("TV", "MOVIE", "GAME", "BOOK")
    is_usenet = True
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self.common = Common(config)
        self.api_key = (
            self.config.get("TRACKERS", {})
            .get(self.tracker, {})
            .get("api_key", "")
            .strip()
        )

    async def get_search_name(self, meta: Meta) -> str:
        return await self.get_name(meta)

    def get_search_query(self, meta: Meta) -> str:
        return build_newznab_search_query(meta)

    def _parse_dupes_from_response(
        self, response_text: str
    ) -> list[dict[str, Any]]:
        return parse_newznab_dupes(response_text)

    async def search_existing(self, meta: Meta) -> list[Any]:
        cached = await self._cached_upload_name(meta)
        if cached:
            return [cached]
        params_list = await self._search_param_list(meta)
        return await self._search_queries(params_list)

    async def _cached_upload_name(self, meta: Meta) -> str:
        release_name = await self.get_name(meta)
        cache_file = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"{self.tracker}_upload_ok"
        )
        if release_name and cache_file.exists():
            logger.info(
                f"{self.tracker}: [yellow]Found local upload cache.[/yellow]"
            )
            return release_name
        return ""

    async def _search_param_list(self, meta: Meta) -> list[dict[str, str]]:
        params_list: list[dict[str, str]] = []
        exact_name = await self.get_search_name(meta)
        if exact_name:
            params_list.append({"t": "search", "q": exact_name})
        params_list.append(self._structured_search_params(meta))
        return params_list

    def _structured_search_params(self, meta: Meta) -> dict[str, str]:
        category = str(meta.category or "").upper()
        if category == "TV":
            params = self._tv_search_params(meta)
        elif category == "MOVIE":
            params = self._movie_search_params(meta)
        else:
            params = {
                "t": "search",
                "cat": self.get_category_id(meta),
                "q": self.get_search_query(meta),
            }
        if not params.get("q"):
            params["q"] = self.get_search_query(meta)
        return params

    def _tv_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"t": "tvsearch"}
        params.update(self._tv_identifier(meta))
        if meta.season_int > 0:
            params["season"] = str(meta.season_int)
        if meta.episode_int > 0:
            params["ep"] = str(meta.episode_int)
        return params

    def _tv_identifier(self, meta: Meta) -> dict[str, str]:
        if self._positive_numeric(meta.tvdb_id):
            return {"tvdbid": str(meta.tvdb_id)}
        if self._positive_numeric(meta.tmdb_id):
            return {"tmdbid": str(meta.tmdb_id)}
        if self._positive_numeric(meta.imdb_id):
            return {"imdbid": f"tt{meta.imdb}"}
        return {"q": self.get_search_query(meta)}

    def _movie_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"t": "movie"}
        if self._positive_numeric(meta.imdb_id):
            params["imdbid"] = f"tt{meta.imdb}"
        elif self._positive_numeric(meta.tmdb_id):
            params["tmdbid"] = str(meta.tmdb_id)
        else:
            params["q"] = self.get_search_query(meta)
        return params

    @staticmethod
    def _positive_numeric(value: Any) -> bool:
        text = str(value or "")
        return text.isdigit() and int(text) > 0

    async def _search_queries(
        self, params_list: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        dupes: list[dict[str, Any]] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=10.0) as client:
            for query_params in params_list:
                await self._append_query_results(
                    client, query_params, dupes, seen
                )
        return dupes

    async def _append_query_results(
        self,
        client: httpx.AsyncClient,
        query_params: dict[str, str],
        dupes: list[dict[str, Any]],
        seen: set[str],
    ) -> None:
        response = await self._safe_search_response(client, query_params)
        if response is None:
            return
        self._extend_unique_dupes(response.text, dupes, seen)

    async def _safe_search_response(
        self, client: httpx.AsyncClient, query_params: dict[str, str]
    ) -> httpx.Response | None:
        try:
            response = await client.get(
                f"{self.base_url}/api",
                params={
                    "apikey": str(self.api_key),
                    "limit": "100",
                    **query_params,
                },
            )
        except httpx.TimeoutException:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search timed out.[/yellow]"
            )
            return None
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [yellow]Duplicate search request failed: {error}[/yellow]"
            )
            return None
        if self._search_response_usable(response):
            return response
        logger.info(
            f"{self.tracker}: [yellow]Duplicate search failed with HTTP {response.status_code}.[/yellow]"
        )
        return None

    def _extend_unique_dupes(
        self, response_text: str, dupes: list[dict[str, Any]], seen: set[str]
    ) -> None:
        try:
            parsed = self._parse_dupes_from_response(response_text)
        except ElementTree.ParseError:
            logger.info(
                f"{self.tracker}: [yellow]Failed to parse duplicate search response.[/yellow]"
            )
            return
        for dupe in parsed:
            self._append_unique_dupe(dupe, dupes, seen)

    @staticmethod
    def _search_response_usable(response: httpx.Response) -> bool:
        return response.status_code == 200 and bool(response.text.strip())

    @staticmethod
    def _append_unique_dupe(
        dupe: dict[str, Any], dupes: list[dict[str, Any]], seen: set[str]
    ) -> None:
        key = str(dupe.get("link") or dupe.get("name") or "")
        if key in seen:
            return
        seen.add(key)
        dupes.append(dupe)

    async def get_additional_checks(self, _meta: Meta) -> bool:
        return True

    def get_category_id(self, meta: Meta) -> str:
        if meta.anime:
            return "5070"
        category = str(meta.category or "").upper()
        resolver = {
            "MOVIE": self._movie_category_id,
            "TV": self._tv_category_id,
            "BOOK": self._book_category_id,
            "GAME": lambda _meta: "4050",
            "MUSIC": lambda _meta: "3000",
        }.get(category)
        return "2000" if resolver is None else resolver(meta)

    @staticmethod
    def _movie_category_id(meta: Meta) -> str:
        return Curupira._resolution_category(
            meta.resolution, uhd="2045", hd="2040", sd="2030"
        )

    @staticmethod
    def _tv_category_id(meta: Meta) -> str:
        return Curupira._resolution_category(
            meta.resolution, uhd="5045", hd="5040", sd="5030"
        )

    @staticmethod
    def _book_category_id(meta: Meta) -> str:
        return "3030" if meta.audiobook else "7020"

    @staticmethod
    def _resolution_category(
        resolution: str | None, *, uhd: str, hd: str, sd: str
    ) -> str:
        value = str(resolution or "").casefold()
        if value in {"2160p", "4320p", "8640p"}:
            return uhd
        return hd if value in {"1080p", "1080i", "720p", "1440p"} else sd

    async def get_name(self, meta: Meta) -> str:
        return meta.scene_name or meta.basename_no_ext

    def get_iso_639_1(self, lang_name: str) -> str | None:
        with contextlib.suppress(Exception):
            lang = langcodes.find(lang_name)
            if lang and lang.is_valid():
                return lang.language
        return None

    def get_source(self, meta: Meta) -> str | None:
        source = str(meta.source or "")
        if not source:
            return None
        return self._known_source(meta, source)

    def _known_source(self, meta: Meta, source: str) -> str:
        upper = source.upper()
        disc = self._bluray_source(meta, upper)
        if disc is not None:
            return disc
        web = self._web_source(meta, upper)
        if web is not None:
            return web
        return self._simple_source(upper, source)

    @staticmethod
    def _simple_source(upper: str, fallback: str) -> str:
        if "HDTV" in upper:
            return "HDTV"
        return "DVD" if "DVD" in upper else fallback

    @staticmethod
    def _bluray_source(meta: Meta, upper: str) -> str | None:
        if "BLU" not in upper:
            return None
        return "Full Disc" if meta.is_disc else "BluRay"

    @staticmethod
    def _web_source(meta: Meta, upper: str) -> str | None:
        if "WEB" not in upper:
            return None
        return "WEBRip" if meta.type == "WEBRIP" else "WEB-DL"

    async def _prepare_files(self, meta: Meta) -> dict[str, Any] | None:
        if not meta.nzb_path or not await self.common.check_nzb_file(
            self.tracker, meta
        ):
            return None
        files = {"nzb_file": await self._nzb_file_tuple(meta.nzb_path)}
        nfo = await self._optional_nfo(meta)
        if nfo is not None:
            files["nfo_file"] = nfo
        return files

    @staticmethod
    async def _nzb_file_tuple(path: str | Path) -> tuple[str, bytes, str]:
        source = Path(path)
        async with aiofiles.open(source, "rb") as handle:
            return source.name, await handle.read(), "application/x-nzb"

    @staticmethod
    async def _optional_nfo(meta: Meta) -> tuple[str, bytes, str] | None:
        nfo_dir = release_temp_dir(meta.base_dir, meta.uuid)
        nfo_path = next(iter(nfo_dir.glob("*.nfo")), None)
        if nfo_path is None or not nfo_path.exists():
            return None
        async with aiofiles.open(nfo_path, "rb") as handle:
            return (
                nfo_path.name,
                await handle.read(),
                "application/octet-stream",
            )

    async def get_media_info(self, meta: Meta) -> str:
        path = self._media_info_path(meta)
        if not path.exists():
            logger.info(
                f"[bold red]Arquivo de info não encontrado: {path}[/bold red]"
            )
            return ""
        try:
            async with aiofiles.open(path, encoding="utf-8") as handle:
                return await handle.read()
        except Exception as error:
            logger.info(
                f"{self.tracker}: [bold red]Erro ao ler o arquivo de info em {path}: {error}[/bold red]"
            )
            return ""

    @staticmethod
    def _media_info_path(meta: Meta) -> Path:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        return root / (
            "BD_SUMMARY_00.txt"
            if meta.is_disc == "BDMV"
            else "MEDIAINFO_CLEANPATH.txt"
        )

    def get_cover(self, meta: Meta) -> str:
        hosted = self._first_https_artwork(meta.hosted_artwork)
        if hosted:
            return hosted
        artwork_url = meta.artwork_url
        return (
            artwork_url
            if isinstance(artwork_url, str)
            and artwork_url.startswith("https://")
            else ""
        )

    @classmethod
    def _first_https_artwork(cls, value: Any) -> str:
        entries = value if isinstance(value, list) else []
        return next(
            (
                url
                for entry in entries
                if (url := cls._https_artwork_url(entry))
            ),
            "",
        )

    @staticmethod
    def _https_artwork_url(entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""
        raw_url = entry.get("raw_url")
        return (
            raw_url
            if isinstance(raw_url, str) and raw_url.startswith("https://")
            else ""
        )

    async def get_screens(self, meta: Meta) -> list[str]:
        menu = self._image_dicts(
            get_tracker_image_collection(meta, self.tracker, "menu_images")
        )
        screenshots = self._image_dicts(
            get_tracker_image_collection(meta, self.tracker, "screenshots")
        )
        spectrograms = self._image_dicts(
            get_tracker_image_collection(
                meta, self.tracker, "spectrograms_images"
            )
        )
        dynamic_hdr = self._valid_raw_images(
            get_tracker_image_collection(
                meta, self.tracker, "dynamic_hdr_plot_images"
            )
        )
        reserved = max(0, 6 - len(dynamic_hdr))
        combined = (menu + screenshots + spectrograms)[:reserved] + dynamic_hdr
        return self._raw_urls(combined)[:6]

    @staticmethod
    def _image_dicts(value: Any) -> list[dict[str, Any]]:
        return (
            [
                cast(dict[str, Any], item)
                for item in value
                if isinstance(item, dict)
            ]
            if isinstance(value, list)
            else []
        )

    @classmethod
    def _valid_raw_images(cls, value: Any) -> list[dict[str, Any]]:
        return [
            image for image in cls._image_dicts(value) if cls._raw_url(image)
        ]

    @staticmethod
    def _raw_url(image: dict[str, Any]) -> str:
        value = image.get("raw_url")
        return value if isinstance(value, str) and value else ""

    @classmethod
    def _raw_urls(cls, images: list[dict[str, Any]]) -> list[str]:
        return [url for image in images if (url := cls._raw_url(image))]

    async def _prepare_data(self, meta: Meta) -> dict[str, Any]:
        screenshots = await self.get_screens(meta)
        data: dict[str, Any] = {
            "name": await self.get_name(meta),
            "category_id": self.get_category_id(meta),
        }
        await self._apply_optional_media_data(data, meta)
        self._apply_disc_languages(data, meta)
        self._apply_external_ids(data, meta)
        self._apply_upload_flags(data, meta, screenshots)
        return data

    async def _apply_optional_media_data(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        self._apply_cover_data(data, meta)
        await self._apply_mediainfo_data(data, meta)
        self._apply_quality_data(data, meta)
        self._apply_source_data(data, meta)

    def _apply_cover_data(self, data: dict[str, Any], meta: Meta) -> None:
        if meta.category in {"MOVIE", "TV"}:
            return
        cover = self.get_cover(meta)
        if cover:
            data["custom_cover_url"] = cover

    async def _apply_mediainfo_data(
        self, data: dict[str, Any], meta: Meta
    ) -> None:
        if meta.category in {"MOVIE", "TV"} or meta.audiobook:
            data["mediainfo_text"] = await self.get_media_info(meta)

    @staticmethod
    def _apply_quality_data(data: dict[str, Any], meta: Meta) -> None:
        quality = str(meta.resolution or "")
        if quality and quality.upper() != "OTHER":
            data["quality"] = quality

    def _apply_source_data(self, data: dict[str, Any], meta: Meta) -> None:
        source = self.get_source(meta)
        if source:
            data["source"] = source

    def _apply_disc_languages(self, data: dict[str, Any], meta: Meta) -> None:
        if not meta.is_disc:
            return
        audio = self._iso_language_codes(meta.audio_languages)
        subtitles = self._iso_language_codes(
            self._language_values(meta.subtitle_languages)
        )
        if audio:
            data["audio_langs"] = json.dumps(audio)
        if subtitles:
            data["subs_langs"] = json.dumps(subtitles)

    @staticmethod
    def _language_values(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return [value] if isinstance(value, str) and value else []

    def _iso_language_codes(self, values: Any) -> list[str]:
        return [
            code
            for value in self._language_values(values)
            if (code := self.get_iso_639_1(value))
        ]

    def _apply_external_ids(self, data: dict[str, Any], meta: Meta) -> None:
        if self._positive_numeric(meta.tmdb_id):
            data["tmdb_id"] = str(meta.tmdb_id)
            category = str(meta.category or "").casefold()
            if category in {"movie", "tv"}:
                data["tmdb_type"] = category
        if self._positive_numeric(meta.mal_id):
            data["mal_id"] = str(meta.mal_id)

    def _apply_upload_flags(
        self, data: dict[str, Any], meta: Meta, screenshots: list[str]
    ) -> None:
        if self._anonymous(meta):
            data["anonymous"] = "true"
        if screenshots:
            data["screenshot_urls"] = json.dumps(screenshots[:6])

    def _anonymous(self, meta: Meta) -> bool:
        configured = bool(
            self.config.get("TRACKERS", {})
            .get(self.tracker, {})
            .get("anon", False)
        )
        return meta.anon != 0 or configured

    async def upload(self, meta: Meta) -> bool | None:
        status = meta.tracker_status.setdefault(self.tracker, {})
        if not await self.common.check_nzb_file(self.tracker, meta):
            status["status_message"] = (
                "data error: NZB file missing or password missing in header"
            )
            return False
        files = await self._prepare_files(meta)
        if not files:
            logger.error(
                f"{self.tracker}: [red]Error: NZB file not found for {self.tracker}.[/red]"
            )
            status["status_message"] = "data error: NZB file not found"
            return False
        data = await self._prepare_data(meta)
        if meta.debug:
            self._log_debug_upload(meta, data, files)
            status["status_message"] = "Debug mode enabled, skipping upload."
            return True
        return await self._upload_release(meta, status, data, files)

    def _log_debug_upload(
        self, meta: Meta, data: dict[str, Any], files: dict[str, Any]
    ) -> None:
        logger.debug(f"{self.tracker}: [cyan]Upload (DEBUG MODE):[/cyan]")
        logger.debug(f"{self.tracker}: URL: {self.upload_url}")
        logger.debug(
            f"{self.tracker}: Category ID: {self.get_category_id(meta)}"
        )
        logger.debug(f"{self.tracker}: Fields:")
        logger.debug(Redaction.redact_private_info(data))
        logger.debug(f"{self.tracker}: Files:")
        logger.debug({key: value[0] for key, value in files.items()})

    async def _upload_release(
        self,
        meta: Meta,
        status: dict[str, Any],
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> bool:
        try:
            response = await self._post_upload(meta, data, files)
            return await self._handle_upload_response(meta, status, response)
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

    async def _post_upload(
        self, meta: Meta, data: dict[str, Any], files: dict[str, Any]
    ) -> httpx.Response:
        version = (
            meta.current_version
            if meta.current_version is not None
            else "github.com/wastaken7/Upload-Assistant"
        )
        headers = {"User-Agent": f"Upload-Assistant {version}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(
                self.upload_url,
                files=files,
                data=data,
                params={"apikey": self.api_key},
                headers=headers,
            )

    async def _handle_upload_response(
        self, meta: Meta, status: dict[str, Any], response: httpx.Response
    ) -> bool:
        if response.status_code not in {200, 201}:
            status["status_message"] = (
                f"data error: HTTP {response.status_code} - {response.text}"
            )
            return False
        payload = response.json()
        status["status_message"] = "Upload successful"
        await self._write_upload_cache(meta)
        release_id = (
            payload.get("public_id") if isinstance(payload, dict) else None
        )
        if release_id:
            status["torrent_id"] = str(release_id)
        return True

    async def _write_upload_cache(self, meta: Meta) -> None:
        cache_dir = release_temp_dir(meta.base_dir, meta.uuid)
        async with aiofiles.open(
            cache_dir / f"{self.tracker}_upload_ok", "w", encoding="utf-8"
        ) as handle:
            await handle.write("ok")
