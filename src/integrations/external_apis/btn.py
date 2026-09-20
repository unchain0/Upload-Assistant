# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import uuid
from collections.abc import Mapping
from typing import Any, cast

import httpx

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.bbcode_formatting import BBCODE

JsonDict = dict[str, Any]


class BtnIdManager:
    @staticmethod
    async def generate_guid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _btn_request_data(
        btn_api: str, btn_id: str, request_id: str
    ) -> JsonDict:
        return {
            "jsonrpc": "2.0",
            "id": request_id[:8],
            "method": "getTorrentsSearch",
            "params": [btn_api, {"id": btn_id}, 50],
        }

    @staticmethod
    async def _btn_response_data(post_data: JsonDict) -> JsonDict | None:
        url = "https://api.broadcasthe.net/"
        headers = {"Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=post_data, timeout=10
                )
                response.raise_for_status()
                try:
                    return cast(JsonDict, response.json())
                except ValueError as exc:
                    logger.info(
                        f"[ERROR] Failed to parse BTN response as JSON: {exc}",
                        extra={"markup": False},
                    )
                    logger.info(
                        f"Response content: {response.text[:200]}...",
                        extra={"markup": False},
                    )
                    return None
        except Exception as exc:
            logger.info(
                f"[ERROR] Failed to fetch BTN data: {exc}",
                extra={"markup": False},
            )
            return None

    @staticmethod
    def _log_btn_api_error(data: JsonDict) -> bool:
        error = data.get("error")
        if not isinstance(error, dict):
            return False
        error_map = cast(JsonDict, error)
        code = error_map.get("code", "unknown")
        message = str(error_map.get("message", "Unknown BTN API error"))
        if "unauthorized ip" in message.lower():
            logger.info(
                f"[red]BTN API error: Unauthorized IP address (code {code}).[/red]"
            )
            logger.info(
                "[yellow]Your current public IP isn't whitelisted for your BTN API key.[/yellow]"
            )
        else:
            logger.info(f"[red]BTN API error (code {code}): {message}[/red]")
        logger.debug(data)
        return True

    @staticmethod
    def _first_btn_torrent(data: JsonDict) -> Mapping[str, Any] | None:
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        result_map = cast(JsonDict, result)
        torrents = result_map.get("torrents")
        if not isinstance(torrents, dict):
            return None
        torrent_map = cast(dict[str, Any], torrents)
        first_torrent = next(iter(torrent_map.values()), None)
        if isinstance(first_torrent, dict):
            return cast(Mapping[str, Any], first_torrent)
        return None

    @staticmethod
    def _numeric_pair(first: Any, second: Any) -> tuple[int, int]:
        if not first and not second:
            return 0, 0
        return int(first or 0), int(second or 0)

    @staticmethod
    def _btn_ids(data: JsonDict) -> tuple[int, int]:
        first_torrent = BtnIdManager._first_btn_torrent(data)
        if first_torrent is None:
            return 0, 0
        return BtnIdManager._numeric_pair(
            first_torrent.get("ImdbID"), first_torrent.get("TvdbID")
        )

    @staticmethod
    async def get_btn_torrents(btn_api: str, btn_id: str) -> tuple[int, int]:
        logger.debug("Fetching BTN data...", extra={"markup": False})
        request_id = await BtnIdManager.generate_guid()
        data = await BtnIdManager._btn_response_data(
            BtnIdManager._btn_request_data(btn_api, btn_id, request_id)
        )
        if not data:
            logger.info(
                "[ERROR] BTN API response is empty or invalid.",
                extra={"markup": False},
            )
            return 0, 0
        if BtnIdManager._log_btn_api_error(data):
            return 0, 0
        logger.debug(
            f"[green]BTN data fetched successfully for BTN ID {data.get('id')}[/green]"
        )
        ids = BtnIdManager._btn_ids(data)
        if ids != (0, 0):
            return ids
        logger.debug("[red]No IMDb or TVDb ID found.")
        return 0, 0

    @staticmethod
    def _bhd_post_data(
        bhd_rss_key: str,
        info_hash: str | None,
        filename: str | None,
        foldername: str | None,
        torrent_id: int | None,
    ) -> JsonDict:
        data: JsonDict
        if torrent_id is not None:
            data = {"action": "details", "torrent_id": torrent_id}
        else:
            data = {"action": "search", "rsskey": bhd_rss_key}
        if info_hash:
            data["info_hash"] = info_hash
        if filename:
            data["file_name"] = filename
        if foldername:
            data["folder_name"] = foldername
        return data

    @staticmethod
    async def _bhd_response_data(
        url: str, post_data: JsonDict
    ) -> JsonDict | None:
        headers = {"Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, headers=headers, json=post_data, timeout=10
                )
                response.raise_for_status()
                try:
                    return cast(JsonDict, response.json())
                except ValueError as exc:
                    logger.info(
                        f"[ERROR] Failed to parse BEYONDHD response as JSON: {exc}",
                        extra={"markup": False},
                    )
                    logger.info(
                        f"Response content: {response.text[:200]}...",
                        extra={"markup": False},
                    )
                    return None
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.info(
                f"[ERROR] Failed to fetch BEYONDHD data: {exc}",
                extra={"markup": False},
            )
            return None

    @staticmethod
    def _bhd_api_failed(data: JsonDict) -> bool:
        if data.get("status_code") != 0 and data.get("success") is not False:
            return False
        error_message = data.get(
            "status_message", "Unknown BEYONDHD API error"
        )
        logger.info(
            f"[ERROR] BEYONDHD API error: {error_message}",
            extra={"markup": False},
        )
        return True

    @staticmethod
    def _first_bhd_result(data: JsonDict) -> JsonDict | None:
        results = data.get("results")
        if isinstance(results, list) and results:
            result_list = cast(list[Any], results)
            first = result_list[0]
            if isinstance(first, dict):
                return cast(JsonDict, first)
        result = data.get("result")
        if isinstance(result, dict):
            return cast(JsonDict, result)
        return None

    @staticmethod
    async def _bhd_description_request(url: str, torrent_id: Any) -> str:
        headers = {"Content-Type": "application/json"}
        desc_post_data: JsonDict = {
            "action": "description",
            "torrent_id": torrent_id,
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=desc_post_data,
                    timeout=10,
                )
                response.raise_for_status()
                data = cast(JsonDict, response.json())
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.info(
                f"[ERROR] Failed to fetch description: {exc}",
                extra={"markup": False},
            )
            return ""
        if data.get("status_code") == 1 and data.get("success") is True:
            logger.info(
                "Successfully retrieved full description",
                extra={"markup": False},
            )
            return str(data.get("result", ""))
        error_message = data.get(
            "status_message", "Unknown BEYONDHD API error"
        )
        logger.info(
            f"[ERROR] Failed to fetch description: {error_message}",
            extra={"markup": False},
        )
        return ""

    @staticmethod
    async def _bhd_description(
        url: str, result: JsonDict, torrent_id: Any
    ) -> str:
        description_value = result.get("description")
        if description_value in {1, "1"}:
            return await BtnIdManager._bhd_description_request(url, torrent_id)
        if description_value is None:
            return ""
        return str(description_value)

    @staticmethod
    def _bhd_imdb_id(result: JsonDict) -> int:
        raw = result.get("imdb_id", "")
        if not raw:
            return 0
        return int(str(raw).replace("tt", "") or 0)

    @staticmethod
    async def _bhd_tmdb_id(result: JsonDict, meta: Meta) -> int:
        raw_tmdb_id = result.get("tmdb_id", "")
        if not raw_tmdb_id or raw_tmdb_id == "0":
            return 0
        category, tmdb_id = await BtnIdManager.parse_tmdb_id(
            str(raw_tmdb_id), meta.category
        )
        if category is not None:
            meta.category = category
        return tmdb_id

    @staticmethod
    def _apply_bhd_group_flags(name: str, meta: Meta) -> None:
        if "framestor" in name:
            meta.framestor = True
        elif "flux" in name:
            meta.flux = True

    @staticmethod
    def _apply_bhd_description(
        description: str,
        meta: Meta,
        skip_tracker_descriptions: bool,
    ) -> None:
        bbcode = BBCODE()
        cleaned, imagelist = bbcode.clean_bhd_description(description, meta)
        if not skip_tracker_descriptions:
            meta.description = cleaned
            meta.image_list = imagelist
        elif meta.keep_images:
            meta.description = ""
            meta.image_list = imagelist

    @staticmethod
    def _log_bhd_ids(imdb: int, tmdb: int, meta: Meta) -> None:
        if imdb or tmdb:
            logger.info(f"[green]Found BEYONDHD IDs: IMDb={imdb}, TMDb={tmdb}")
        elif meta.debug:
            logger.info(
                f"[yellow]BEYONDHD search returned no valid IDs (IMDb={imdb}, TMDb={tmdb})[/yellow]"
            )

    @staticmethod
    def _validated_bhd_result(data: JsonDict | None) -> JsonDict | None:
        if data is None:
            return None
        if BtnIdManager._bhd_api_failed(data):
            return None
        result = BtnIdManager._first_bhd_result(data)
        if result is not None:
            return result
        logger.info(
            "No valid results found in BEYONDHD API response.",
            extra={"markup": False},
        )
        return None

    @staticmethod
    async def _apply_bhd_result(
        url: str,
        result: JsonDict,
        torrent_id: int | None,
        meta: Meta,
        skip_tracker_descriptions: bool,
    ) -> tuple[int, int]:
        resolved_torrent_id = torrent_id or result.get("id", 0)
        description = await BtnIdManager._bhd_description(
            url, result, resolved_torrent_id
        )
        imdb = BtnIdManager._bhd_imdb_id(result)
        tmdb = await BtnIdManager._bhd_tmdb_id(result, meta)
        if skip_tracker_descriptions and not meta.keep_images:
            return imdb, tmdb
        BtnIdManager._apply_bhd_group_flags(
            str(result.get("name", "")).lower(), meta
        )
        BtnIdManager._apply_bhd_description(
            description, meta, skip_tracker_descriptions
        )
        BtnIdManager._log_bhd_ids(imdb, tmdb, meta)
        return imdb, tmdb

    @staticmethod
    async def get_bhd_torrents(
        bhd_api: str,
        bhd_rss_key: str,
        meta: Meta,
        skip_tracker_descriptions: bool = False,
        info_hash: str | None = None,
        filename: str | None = None,
        foldername: str | None = None,
        torrent_id: int | None = None,
    ) -> tuple[int, int]:
        logger.debug("Fetching BEYONDHD data...", extra={"markup": False})
        url = f"https://beyond-hd.me/api/torrents/{bhd_api}"
        post_data = BtnIdManager._bhd_post_data(
            bhd_rss_key, info_hash, filename, foldername, torrent_id
        )
        data = await BtnIdManager._bhd_response_data(url, post_data)
        result = BtnIdManager._validated_bhd_result(data)
        if result is None:
            return 0, 0
        return await BtnIdManager._apply_bhd_result(
            url,
            result,
            torrent_id,
            meta,
            skip_tracker_descriptions,
        )

    @staticmethod
    async def parse_tmdb_id(
        tmdb_id: str, category: str | None
    ) -> tuple[str | None, int]:
        """Parses TMDb ID, ensures correct formatting, and assigns category."""
        tmdb_id_str = tmdb_id.strip().lower()
        if tmdb_id_str.startswith("tv/"):
            tmdb_id_str = tmdb_id_str.split("/")[1].split("-")[0]
            category = "TV"
        elif tmdb_id_str.startswith("movie/"):
            tmdb_id_str = tmdb_id_str.split("/")[1].split("-")[0]
            category = "MOVIE"
        parsed_id = int(tmdb_id_str) if tmdb_id_str.isdigit() else 0
        return category, parsed_id


async def generate_guid() -> str:
    return await BtnIdManager.generate_guid()


async def get_btn_torrents(btn_api: str, btn_id: str) -> tuple[int, int]:
    return await BtnIdManager.get_btn_torrents(btn_api, btn_id)


async def get_bhd_torrents(
    bhd_api: str,
    bhd_rss_key: str,
    meta: Meta,
    skip_tracker_descriptions: bool = False,
    info_hash: str | None = None,
    filename: str | None = None,
    foldername: str | None = None,
    torrent_id: int | None = None,
) -> tuple[int, int]:
    return await BtnIdManager.get_bhd_torrents(
        bhd_api,
        bhd_rss_key,
        meta,
        skip_tracker_descriptions=skip_tracker_descriptions,
        info_hash=info_hash,
        filename=filename,
        foldername=foldername,
        torrent_id=torrent_id,
    )


async def parse_tmdb_id(
    tmdb_id: str, category: str | None = None
) -> tuple[str | None, int]:
    return await BtnIdManager.parse_tmdb_id(tmdb_id, category)
