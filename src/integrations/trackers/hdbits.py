# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlparse

import aiofiles
import httpx
from rich.markup import escape
from unidecode import unidecode

from src.domain_models.processing import UploadError
from src.domain_models.release import Meta
from src.domain_models.release_description import base_description
from src.integrations.filesystem.temp_paths import (
    release_temp_dir,
    screenshots_dir,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.trackers.common import Common

Config = dict[str, Any]


class HDBits:
    """
    HDB Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "HDBITS"
    display_name = "HDBits"
    allows_bloated_audio = True
    source_flag = "HDBits"
    signature: str | None = None
    banned_groups: tuple[str, ...] = ("",)
    base_url = "https://hdbits.org"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://tracker.hdbits.org",)

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        trackers = config.get("TRACKERS", {})
        tracker_config = (
            trackers.get("HDBITS", {}) if isinstance(trackers, dict) else {}
        )
        tracker_config_dict = (
            cast(dict[str, Any], tracker_config)
            if isinstance(tracker_config, dict)
            else {}
        )
        self.username = str(tracker_config_dict.get("username", "")).strip()
        self.passkey = str(tracker_config_dict.get("passkey", "")).strip()
        self.rehost_images = bool(tracker_config_dict.get("img_rehost", True))
        self.common = Common(config=config)

    async def get_type_category_id(self, meta: Meta) -> int:
        concert = self._concert_category(meta)
        if concert is not None:
            return concert
        if self._is_documentary(meta):
            return 3
        return {"MOVIE": 1, "TV": 2}.get(meta.category, 0)

    @staticmethod
    def _is_documentary(meta: Meta) -> bool:
        genres = {str(value).lower() for value in meta.genres}
        keywords = {str(value).lower() for value in meta.keywords}
        return "documentary" in genres or "documentary" in keywords

    @staticmethod
    def _concert_category(meta: Meta) -> int | None:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        imdb_type = str(imdb.get("type", "")).lower()
        genres = str(imdb.get("genres", "")).lower()
        return (
            4
            if "concert" in imdb_type
            or ("video" in imdb_type and "music" in genres)
            else None
        )

    async def get_type_codec_id(self, meta: Meta) -> int:
        codecmap = {
            "AVC": 1,
            "H.264": 1,
            "HEVC": 5,
            "H.265": 5,
            "MPEG-2": 2,
            "VC-1": 3,
            "XviD": 4,
            "VP9": 6,
        }
        searchcodec = meta.video_codec or meta.video_encode or ""
        return codecmap.get(searchcodec, 0)

    async def get_type_medium_id(self, meta: Meta) -> int:
        if meta.is_disc in {"BDMV", "HD DVD"}:
            return 1
        if meta.type == "HDTV":
            return 3 if meta.has_encode_settings is True else 4
        return {"ENCODE": 3, "WEBRIP": 3, "REMUX": 5, "WEBDL": 6}.get(
            str(meta.type or ""), 0
        )

    async def get_res_id(self, resolution: str) -> str:
        return {
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
        }.get(resolution, "10")

    async def get_tags(self, meta: Meta) -> list[int]:
        tags: list[int] = []
        self._append_mapped_tag(tags, meta.service, self._service_tags())
        self._append_mapped_tag(
            tags, meta.distributor, self._distributor_tags()
        )
        self._append_edition_tags(tags, meta.edition)
        self._append_audio_tags(tags, meta)
        self._append_hdr_tags(tags, str(meta.hdr or ""))
        return tags

    @staticmethod
    def _service_tags() -> dict[str, int]:
        return {
            "AMZN": 28,
            "NF": 29,
            "HULU": 34,
            "DSNP": 33,
            "HMAX": 30,
            "ATVP": 27,
            "iT": 38,
            "iP": 56,
            "STAN": 32,
            "PCOK": 31,
            "CR": 72,
            "PMTP": 69,
            "MA": 77,
            "SHO": 76,
            "BCORE": 66,
            "CORE": 66,
            "CRKL": 73,
            "FUNI": 74,
            "HLMK": 71,
            "HTSR": 79,
            "CRAV": 80,
            "MAX": 88,
        }

    @staticmethod
    def _distributor_tags() -> dict[str, int]:
        return {
            "WARNER ARCHIVE": 68,
            "WARNER ARCHIVE COLLECTION": 68,
            "WAC": 68,
            "CRITERION": 18,
            "CRITERION COLLECTION": 18,
            "CC": 18,
            "MASTERS OF CINEMA": 19,
            "MOC": 19,
            "KINO LORBER": 55,
            "KINO": 55,
            "BFI VIDEO": 63,
            "BFI": 63,
            "BRITISH FILM INSTITUTE": 63,
            "STUDIO CANAL": 65,
            "ARROW": 64,
        }

    @staticmethod
    def _append_mapped_tag(
        tags: list[int], value: Any, mapping: dict[str, int]
    ) -> None:
        tag = mapping.get(str(value or ""))
        if tag is not None:
            tags.append(tag)

    @staticmethod
    def _append_edition_tags(tags: list[int], edition: str) -> None:
        if "IMAX" in edition:
            tags.append(14)
        if "OPEN MATTE" in edition.upper():
            tags.append(58)

    def _append_audio_tags(self, tags: list[int], meta: Meta) -> None:
        audio = str(meta.audio or "")
        if "DTS:X" in audio:
            tags.append(7)
        if "Atmos" in audio:
            tags.append(5)
        if meta.silent is True:
            logger.info(
                f"{self.tracker}: [yellow]zxx audio track found, suggesting you tag as silent"
            )

    @staticmethod
    def _append_hdr_tags(tags: list[int], hdr: str) -> None:
        if "HDR" in hdr:
            tags.append(25 if "HDR10+" in hdr else 9)
        if "DV" in hdr:
            tags.append(6)
        if "HLG" in hdr:
            tags.append(10)

    async def get_name(self, meta: Meta) -> str:
        name = str(meta.name or "").replace("H.265", "HEVC")
        name = self._strip_service(name, meta)
        name = self._normalize_hdr_name(name, meta)
        name = self._normalize_audio_name(name, meta)
        name = self._apply_imdb_name(name, meta)
        return self._finalize_hdb_name(name)

    @staticmethod
    def _strip_service(name: str, meta: Meta) -> str:
        if meta.source or meta.service:
            return name.replace(f"{meta.service} ", "", 1)
        return name

    @staticmethod
    def _normalize_hdr_name(name: str, meta: Meta) -> str:
        result = (
            name.replace(" DV ", " DoVi ") if "DV" in str(meta.hdr) else name
        )
        if "HDR" in str(meta.hdr) and "HDR10+" not in str(meta.hdr):
            result = result.replace("HDR", "HDR10")
        return result

    @staticmethod
    def _normalize_audio_name(name: str, meta: Meta) -> str:
        audio = str(meta.audio or "")
        if not audio:
            return name
        replacement = audio.replace(" Atmos", "")
        if meta.type in {"WEBDL", "WEBRIP", "ENCODE"}:
            replacement = replacement.replace(" ", "", 1)
        return name.replace(audio, replacement)

    @classmethod
    def _apply_imdb_name(cls, name: str, meta: Meta) -> str:
        result = name.replace(str(meta.aka or ""), "")
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        if not imdb:
            return result
        result = result.replace(
            str(meta.title), str(imdb.get("aka", meta.title))
        )
        return cls._replace_imdb_year(result, meta, imdb)

    @staticmethod
    def _replace_imdb_year(name: str, meta: Meta, imdb: dict[str, Any]) -> str:
        meta_year = "" if meta.year is None else str(meta.year)
        imdb_year = str(imdb.get("year", meta_year))
        if not meta_year or meta_year == imdb_year:
            return name
        return name.replace(meta_year, imdb_year)

    @staticmethod
    def _finalize_hdb_name(name: str) -> str:
        result = (
            name.replace("PQ10", "HDR")
            .replace("Dubbed", "")
            .replace("Dual-Audio", "")
        )
        result = (
            result.replace("REMUX", "Remux")
            .replace("BluRay Remux", "Remux")
            .replace("UHD Remux", "Remux")
        )
        result = result.replace("DTS-HD HRA", "DTS-HD HR")
        result = " ".join(result.split())
        result = re.sub(r"[^0-9a-zA-ZÀ-ÿ. :&+'\-\[\]]+", "", result)
        return result.replace(" .", ".").replace("..", ".")

    async def upload(self, meta: Meta) -> bool | None:
        await self.edit_desc(meta)
        identifiers = await self._upload_identifiers(meta)
        if not self._valid_upload_identifiers(identifiers):
            logger.info(
                f"{self.tracker}: [bold red]Something didn't map correctly, or this content is not allowed on {self.tracker}"
            )
            return None
        if self._dual_audio_forbidden(meta):
            logger.info(
                f"{self.tracker}: [bold red]Dual-Audio Encodes are not allowed for non-anime and non-disc content"
            )
            return None
        await self._prepare_upload_torrent(meta)
        data, files, torrent_path = await self._upload_request_parts(
            meta, identifiers
        )
        if meta.debug:
            return await self._debug_upload(meta, data)
        response = await self._post_upload(meta, data, files)
        return await self._handle_upload_response(meta, response, torrent_path)

    async def _upload_identifiers(self, meta: Meta) -> dict[str, Any]:
        return {
            "name": await self.get_name(meta),
            "category": await self.get_type_category_id(meta),
            "codec": await self.get_type_codec_id(meta),
            "medium": await self.get_type_medium_id(meta),
            "tags": await self.get_tags(meta),
        }

    @staticmethod
    def _valid_upload_identifiers(values: dict[str, Any]) -> bool:
        return all(
            values.get(key) not in {0, "0", None}
            for key in ("category", "codec", "medium")
        )

    @staticmethod
    def _dual_audio_forbidden(meta: Meta) -> bool:
        return (
            "Dual-Audio" in str(meta.audio)
            and not meta.anime
            and bool(meta.is_disc)
        )

    async def _prepare_upload_torrent(self, meta: Meta) -> None:
        piece_mb = meta.base_torrent_piece_mb or 0
        if piece_mb <= 16 or meta.nohash:
            await self.common.create_torrent_for_upload(
                meta, self.tracker, self.source_flag
            )
            return
        logger.info(
            f"{self.tracker}: [red]Piece size is OVER 16M and does not work on {self.tracker}. Generating a new .torrent"
        )
        await self._rehash_upload_torrent(meta)

    async def _rehash_upload_torrent(self, meta: Meta) -> None:
        tracker_url = str(
            self._tracker_config().get("announce_url", "https://fake.tracker")
        ).strip()
        cooldown = self._rehash_cooldown()
        if cooldown > 0:
            await asyncio.sleep(cooldown)
        torrent_name = f"[{self.tracker}]"
        await TorrentCreator.create_torrent(
            meta,
            str(meta.path),
            torrent_name,
            tracker_url=tracker_url,
            piece_size=16,
        )
        await self.common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag, torrent_filename=torrent_name
        )

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _rehash_cooldown(self) -> int:
        default = self.config.get("DEFAULT", {})
        value = (
            default.get("rehash_cooldown", 0)
            if isinstance(default, dict)
            else 0
        )
        try:
            return max(0, int(value or 0))
        except TypeError, ValueError:
            return 0

    async def _upload_request_parts(
        self, meta: Meta, identifiers: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, tuple[str, bytes, str]], Path]:
        description = await self._read_temp_text(
            meta, f"[{self.tracker}]DESCRIPTION.txt"
        )
        torrent_path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"[{self.tracker}].torrent"
        )
        torrent_bytes = await self._read_bytes(torrent_path)
        files = {
            "file": (
                f"{self._torrent_filename(meta)}.torrent",
                torrent_bytes,
                "application/x-bittorrent",
            )
        }
        data = await self._upload_data(meta, identifiers, description)
        return data, files, torrent_path

    @staticmethod
    async def _read_temp_text(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    async def _read_bytes(path: Path) -> bytes:
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    @staticmethod
    def _torrent_filename(meta: Meta) -> str:
        filelist = meta.filelist if isinstance(meta.filelist, list) else []
        source = meta.video if len(filelist) == 1 else meta.path
        return unidecode(Path(str(source)).name.replace(" ", "."))

    async def _upload_data(
        self, meta: Meta, identifiers: dict[str, Any], description: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": identifiers["name"],
            "category": identifiers["category"],
            "codec": identifiers["codec"],
            "medium": identifiers["medium"],
            "origin": self._origin_value(meta),
            "descr": description.rstrip(),
            "techinfo": await self._technical_info(meta),
            "tags[]": identifiers["tags"],
            "imdb": self._imdb_value(meta),
        }
        self._append_external_ids(data, meta)
        return data

    def _origin_value(self, meta: Meta) -> int:
        tag = str(meta.tag or "").lstrip("-")
        internal = bool(self._tracker_config().get("internal", False))
        groups = self._tracker_config().get("internal_groups", [])
        return int(internal and isinstance(groups, list) and tag in groups)

    async def _technical_info(self, meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return ""
        return await self._read_temp_text(meta, "MEDIAINFO_CLEANPATH.txt")

    @staticmethod
    def _imdb_value(meta: Meta) -> str | int:
        if not meta.imdb_id:
            return 0
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        url = str(imdb.get("imdb_url", ""))
        return f"{url}/" if url else 0

    @staticmethod
    def _append_external_ids(data: dict[str, Any], meta: Meta) -> None:
        if meta.tvdb_id:
            data["tvdb"] = meta.tvdb_id
        if meta.category == "TV":
            data["tvdb_season"] = meta.season_int
            data["tvdb_episode"] = meta.episode_int

    async def _debug_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.debug(f"{self.base_url}/upload/upload")
        logger.debug(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _post_upload(
        self,
        meta: Meta,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
    ) -> httpx.Response:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookiefile = find_cookie_file(meta.base_dir, self.tracker, self.config)
        cookies = await self.common.parse_cookie_file(cookiefile)
        async with httpx.AsyncClient(
            cookies=cookies, timeout=30.0, follow_redirects=True
        ) as client:
            return await client.post(
                url=f"{self.base_url}/upload/upload", data=data, files=files
            )

    async def _handle_upload_response(
        self, meta: Meta, response: httpx.Response, torrent_path: Path
    ) -> bool:
        match = re.match(
            rf".*?{re.escape(self.base_url.replace('https://', ''))}/details\.php\?id=(\d+)&uploaded=(\d+)",
            str(response.url),
        )
        if match is None:
            logger.info(response.text)
            raise UploadError(
                f"Upload to HDBITS Failed: result URL {response.url} ({response.status_code}) was not expected",
                "red",
            )
        meta.tracker_status[self.tracker]["status_message"] = match.group(0)
        torrent_id = self._torrent_id_from_url(str(response.url))
        if torrent_id:
            meta.tracker_status[self.tracker]["torrent_id"] = torrent_id
            await self.download_new_torrent(torrent_id, str(torrent_path))
        return True

    @staticmethod
    def _torrent_id_from_url(url: str) -> str:
        match = re.search(r"(?:^|&)id=(\d+)", urlparse(url).query)
        return match.group(1) if match else ""

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/torrents"
        data = await self._search_payload(meta)
        terms = self._search_terms(meta)
        if not terms:
            return await self._search_api(url, data)
        dupes: list[dict[str, Any]] = []
        for term in terms:
            logger.info(
                f"{self.tracker}: [yellow]Searching {self.tracker} for: {escape(str(term))}"
            )
            query = {**data, "search": term}
            dupes.extend(await self._search_api(url, query))
        return dupes

    async def _search_payload(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "username": self.username,
            "passkey": self.passkey,
            "category": await self.get_type_category_id(meta),
            "codec": await self.get_type_codec_id(meta),
            "medium": await self.get_type_medium_id(meta),
        }
        if meta.imdb_id:
            data["imdb"] = {"id": meta.imdb}
        if meta.tvdb_id:
            data["tvdb"] = {"id": meta.tvdb_id}
        return data

    def _search_terms(self, meta: Meta) -> list[str]:
        if not self._needs_fallback_search(meta):
            return []
        logger.info(
            f"{self.tracker}: [yellow]No IMDb or TVDB ID found, trying other options..."
        )
        logger.info(
            f"{self.tracker}: [yellow]Double check that the upload does not already exist..."
        )
        return self._fallback_search_candidates(meta)

    def _fallback_search_candidates(self, meta: Meta) -> list[str]:
        candidates = (
            str(meta.filename or ""),
            self._clean_aka(meta.aka),
            str(meta.basename_no_ext or ""),
        )
        return list(dict.fromkeys(value for value in candidates if value))

    @staticmethod
    def _needs_fallback_search(meta: Meta) -> bool:
        if meta.category == "MOVIE":
            return not bool(meta.imdb_id)
        if meta.category == "TV":
            return not bool(meta.tvdb_id or meta.imdb_id)
        return False

    @staticmethod
    def _clean_aka(value: Any) -> str:
        return str(value or "").replace("AKA ", "").strip()

    async def _search_api(
        self, url: str, data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=data)
        if response.status_code != 200:
            logger.info(
                f"{self.tracker}: [bold red]HTTP request failed. Status: {response.status_code}"
            )
            return []
        return self._search_results(response.json())

    def _search_results(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        results = payload.get("data", [])
        values = results if isinstance(results, list) else []
        return [
            self._search_result(item)
            for item in values
            if isinstance(item, dict)
        ]

    def _search_result(self, item: dict[str, Any]) -> dict[str, Any]:
        filename = str(item.get("filename", ""))
        torrent_id = item.get("id")
        return {
            "name": item.get("name", ""),
            "size": item.get("size", 0),
            "files": filename[:-8]
            if filename.endswith(".torrent")
            else filename,
            "filecount": item.get("numfiles", 0),
            "link": f"{self.base_url}/details.php?id={torrent_id}",
            "download": f"{self.base_url}/download.php/{quote(filename)}?id={torrent_id}&passkey={self.passkey}",
        }

    async def validate_credentials(self, meta: Meta) -> bool:
        vcookie = await self.validate_cookies(meta)
        if vcookie is not True:
            logger.error(
                f"{self.tracker}: [red]Failed to validate cookies. Please confirm that the site is up and your passkey is valid."
            )
            return False
        return True

    async def validate_cookies(self, meta: Meta) -> bool:
        common = Common(config=self.config)
        url = self.base_url
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookiefile = find_cookie_file(meta.base_dir, self.tracker, self.config)
        if Path(cookiefile).exists():
            cookies = await common.parse_cookie_file(cookiefile)
            async with httpx.AsyncClient(
                cookies=cookies, timeout=30.0
            ) as client:
                resp = await client.get(url=url)
            return resp.text.find("""<a href="/logout.php">Logout</a>""") != -1
        logger.info(
            f"{self.tracker}: [bold red]Missing Cookie File. (data/cookies/HDBITS.txt)"
        )
        return False

    async def download_new_torrent(self, id: str, torrent_path: str) -> None:
        filename = await self._torrent_filename_from_api(id)
        response = await self._download_torrent_response(id, filename)
        self._validate_torrent_download(response, filename, id)
        async with aiofiles.open(torrent_path, "wb") as handle:
            await handle.write(response.content)

    async def _torrent_filename_from_api(self, torrent_id: str) -> str:
        api_url = f"{self.base_url}/api/torrents"
        data = {
            "username": self.username,
            "passkey": self.passkey,
            "id": torrent_id,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url=api_url, json=data)
        response.raise_for_status()
        payload = self._json_object(response, api_url, data)
        return self._torrent_filename_from_payload(payload, api_url, data)

    @staticmethod
    def _json_object(
        response: httpx.Response, api_url: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Failed to parse JSON response from {api_url}. Response content: {response.text}. Data: {data}. Error: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise ValueError(
                f"Invalid JSON response from {api_url}: expected object. Response: {payload}. Data: {data}"
            )
        return cast(dict[str, Any], payload)

    @staticmethod
    def _torrent_filename_from_payload(
        payload: dict[str, Any], api_url: str, data: dict[str, Any]
    ) -> str:
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise ValueError(
                f"Invalid JSON response from {api_url}: 'data' key missing, not a list, or empty. Response: {payload}. Data: {data}"
            )
        first = rows[0]
        if not isinstance(first, dict) or not first.get("filename"):
            raise ValueError(
                f"Failed to access filename in response from {api_url}. Response: {payload}. Data: {data}"
            )
        return str(first["filename"])

    async def _download_torrent_response(
        self, torrent_id: str, filename: str
    ) -> httpx.Response:
        url = f"{self.base_url}/download.php/{quote(filename)}"
        params = {"passkey": self.passkey, "id": torrent_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url=url, params=params)
        response.raise_for_status()
        return response

    @staticmethod
    def _validate_torrent_download(
        response: httpx.Response, filename: str, torrent_id: str
    ) -> None:
        content_type = response.headers.get("content-type", "").lower()
        if (
            "bittorrent" not in content_type
            and "octet-stream" not in content_type
        ):
            raise ValueError(
                f"Unexpected content-type for torrent download: {content_type}. File: {filename}. ID: {torrent_id}"
            )
        if not response.content.startswith(b"d"):
            raise ValueError(
                f"Downloaded content does not appear to be a valid torrent file (does not start with 'd'). File: {filename}. ID: {torrent_id}"
            )

    async def edit_desc(self, meta: Meta) -> None:
        parts = await self._description_parts(meta)
        path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write("".join(parts))

    async def _description_parts(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        web_note = self._web_source_note(meta)
        if web_note:
            parts.append(web_note)
        parts.extend(self._disc_description_parts(meta))
        parts.append(self._formatted_base_description(meta))
        image_block = await self._image_description_block(meta)
        if image_block:
            parts.append(image_block)
        if self.signature is not None:
            parts.append(self.signature)
        return parts

    @staticmethod
    def _web_source_note(meta: Meta) -> str:
        if (
            meta.type != "WEBDL"
            or not meta.service_longname
            or not meta.description
        ):
            return ""
        return f"[center][quote]This release is sourced from {meta.service_longname}[/quote][/center]"

    @classmethod
    def _disc_description_parts(cls, meta: Meta) -> list[str]:
        discs = cls._disc_entries(meta)
        if not discs:
            return []
        parts = [cls._first_disc_description(discs[0])]
        parts.extend(
            cls._additional_disc_description(disc) for disc in discs[1:]
        )
        return [part for part in parts if part]

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        values = meta.discs if isinstance(meta.discs, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    @staticmethod
    def _first_disc_description(disc: dict[str, Any]) -> str:
        if disc.get("type") == "DVD":
            return f"[quote=VOB MediaInfo]{disc.get('vob_mi', '')}[/quote]\n\n"
        if disc.get("type") == "BDMV":
            return f"[quote]{str(disc.get('summary', '')).strip()}[/quote]\n\n"
        return ""

    @staticmethod
    def _additional_disc_description(disc: dict[str, Any]) -> str:
        if disc.get("type") == "BDMV":
            return f"[quote={disc.get('name', 'BDINFO')}]{disc.get('summary', '')}[/quote]\n\n"
        if disc.get("type") == "DVD":
            return (
                f"{disc.get('name', '')}:\n"
                f"[quote={Path(str(disc.get('vob', ''))).name}]{disc.get('vob_mi', '')}[/quote] "
                f"[quote={Path(str(disc.get('ifo', ''))).name}]{disc.get('ifo_mi', '')}[/quote]\n\n"
            )
        return ""

    @staticmethod
    def _formatted_base_description(meta: Meta) -> str:
        desc = base_description(meta)
        replacements = (
            ("[code]", "[font=monospace]"),
            ("[/code]", "[/font]"),
            ("[user]", ""),
            ("[/user]", ""),
            ("[left]", ""),
            ("[/left]", ""),
            ("[align=left]", ""),
            ("[/align]", ""),
            ("[right]", ""),
            ("[/right]", ""),
            ("[align=right]", ""),
            ("[sup]", ""),
            ("[/sup]", ""),
            ("[sub]", ""),
            ("[/sub]", ""),
            ("[alert]", ""),
            ("[/alert]", ""),
            ("[note]", ""),
            ("[/note]", ""),
            ("[hr]", ""),
            ("[/hr]", ""),
            ("[h1]", "[u][b]"),
            ("[/h1]", "[/b][/u]"),
            ("[h2]", "[u][b]"),
            ("[/h2]", "[/b][/u]"),
            ("[h3]", "[u][b]"),
            ("[/h3]", "[/b][/u]"),
            ("[ul]", ""),
            ("[/ul]", ""),
            ("[ol]", ""),
            ("[/ol]", ""),
            ("[*]", "* "),
        )
        for source, target in replacements:
            desc = desc.replace(source, target)
        bbcode = BBCODE()
        desc = bbcode.convert_spoiler_to_hide(desc)
        desc = bbcode.convert_comparison_to_centered(desc, 1000)
        desc = re.sub(r"(\[img=\d+)]", "[img]", desc, flags=re.IGNORECASE)
        return re.sub(r"\[/size\]|\[size=\d+\]", "", desc, flags=re.IGNORECASE)

    async def _image_description_block(self, meta: Meta) -> str:
        if self.rehost_images:
            logger.info(f"{self.tracker}: [green]Rehosting Images...")
            uploaded = await self.hdbimg_upload(meta)
            if uploaded is None:
                return ""
            return self._rehosted_image_block(meta, uploaded)
        return self._manual_image_block(meta)

    def _rehosted_image_block(self, meta: Meta, bbcode: str) -> str:
        if not meta.comparison:
            return f"[center]{bbcode}[/center]"
        header = self._comparison_header(meta)
        return f"[center][b]Screenshot comparison[/b]\n\n{header}\n\n{bbcode}[/center]"

    @staticmethod
    def _comparison_header(meta: Meta) -> str:
        groups = (
            meta.comparison_groups
            if isinstance(meta.comparison_groups, dict)
            else {}
        )
        if not groups:
            return ""
        names: list[str] = []
        for index in sorted(groups, key=lambda value: int(str(value))):
            group = groups.get(index, {})
            group_mapping = (
                cast(dict[str, Any], group) if isinstance(group, dict) else {}
            )
            names.append(str(group_mapping.get("name", f"Group {index}")))
        return " vs ".join(names)

    @classmethod
    def _manual_image_block(cls, meta: Meta) -> str:
        links = cls._manual_image_links(meta)
        return f"[center]{''.join(links)}[/center]" if links else ""

    @classmethod
    def _manual_image_links(cls, meta: Meta) -> list[str]:
        images = cls._mapping_image_list(meta.image_list)
        limit = cls._screen_limit(meta.screens)
        return [
            link
            for image in images[:limit]
            if (link := cls._manual_image_link(image))
        ]

    @staticmethod
    def _mapping_image_list(value: Any) -> list[dict[str, Any]]:
        values = value if isinstance(value, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    @staticmethod
    def _screen_limit(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _manual_image_link(image: dict[str, Any]) -> str:
        img_url = str(image.get("img_url", ""))
        web_url = str(image.get("web_url", ""))
        return (
            f"[url={web_url}][img]{img_url}[/img][/url]"
            if img_url and web_url
            else ""
        )

    async def hdbimg_upload(self, meta: Meta) -> str | None:
        plan = await self._image_upload_plan(meta)
        if plan is None:
            return None
        image_files, thumb_size, group_indices = plan
        upload_files = await self._planned_upload_files(meta, image_files)
        if not upload_files:
            logger.info(f"{self.tracker}: [red]No files to upload")
            return None
        data = self._image_upload_data(meta, thumb_size)
        return await self._safe_image_upload(
            meta, upload_files, group_indices, data
        )

    async def _planned_upload_files(
        self, meta: Meta, image_files: list[str]
    ) -> dict[str, tuple[str, bytes, str]]:
        upload_count = self._image_upload_count(meta, image_files)
        return await self._load_image_upload_files(image_files[:upload_count])

    async def _safe_image_upload(
        self,
        meta: Meta,
        upload_files: dict[str, tuple[str, bytes, str]],
        group_indices: list[str],
        data: dict[str, Any],
    ) -> str | None:
        try:
            return await self._execute_image_upload(
                meta, upload_files, group_indices, data
            )
        except httpx.RequestError as error:
            logger.info(f"{self.tracker}: [red]HTTP Request failed: {error}")
            return None

    async def _execute_image_upload(
        self,
        meta: Meta,
        upload_files: dict[str, tuple[str, bytes, str]],
        group_indices: list[str],
        data: dict[str, Any],
    ) -> str | None:
        if not meta.comparison:
            return await self._upload_image_batch(upload_files, data)
        bbcode = await self._upload_comparison_images(
            upload_files, group_indices, data
        )
        return (
            None
            if bbcode is None
            else self._format_comparison_bbcode(
                bbcode, len(group_indices) or 3
            )
        )

    async def _image_upload_plan(
        self, meta: Meta
    ) -> tuple[list[str], str, list[str]] | None:
        if meta.comparison:
            return self._comparison_image_plan(meta)
        files = await self._screenshot_image_files(meta)
        if not files:
            logger.info(f"{self.tracker}: [red]No images found for upload")
            return None
        return files, "w300", []

    def _comparison_image_plan(
        self, meta: Meta
    ) -> tuple[list[str], str, list[str]] | None:
        comparison_path = Path(str(meta.comparison or ""))
        if not comparison_path.is_dir():
            logger.info(
                f"{self.tracker}: [red]Comparison path not found: {meta.comparison}"
            )
            return None
        groups = self._comparison_group_images(meta, comparison_path)
        indices = sorted(groups, key=lambda value: int(str(value)))
        files = self._interleaved_group_images(groups, indices)
        if not files:
            logger.info(f"{self.tracker}: [red]No images found for upload")
            return None
        return files, self._comparison_thumb_size(len(indices)), indices

    def _comparison_group_images(
        self, meta: Meta, comparison_path: Path
    ) -> dict[str, list[str]]:
        groups = meta.comparison_groups
        if isinstance(groups, dict):
            return self._declared_comparison_groups(
                cast(dict[str, Any], groups), comparison_path
            )
        return self._discovered_comparison_groups(comparison_path)

    @classmethod
    def _declared_comparison_groups(
        cls, groups: dict[str, Any], comparison_path: Path
    ) -> dict[str, list[str]]:
        return {
            str(group_index): cls._declared_group_files(value, comparison_path)
            for group_index, value in groups.items()
        }

    @classmethod
    def _declared_group_files(
        cls, value: Any, comparison_path: Path
    ) -> list[str]:
        mapping = (
            cast(dict[str, Any], value) if isinstance(value, dict) else {}
        )
        names = cls._comparison_file_names(mapping.get("files", []))
        paths = [
            comparison_path / name
            for name in sorted(names, key=cls._comparison_file_order)
        ]
        return [str(path) for path in paths if path.exists()]

    @staticmethod
    def _comparison_file_names(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [str(item) for item in values]

    @staticmethod
    def _comparison_file_order(filename: str) -> int:
        match = re.match(r"(\d+)-", filename)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _discovered_comparison_groups(
        comparison_path: Path,
    ) -> dict[str, list[str]]:
        pattern = re.compile(r"(\d+)-(\d+)-(.+)\.png", re.IGNORECASE)
        groups: dict[str, list[tuple[int, str]]] = {}
        for path in comparison_path.iterdir():
            match = pattern.match(path.name)
            if match is None:
                continue
            first, second, _ = match.groups()
            groups.setdefault(second, []).append((int(first), str(path)))
        return {
            group: [path for _, path in sorted(entries)]
            for group, entries in groups.items()
        }

    @staticmethod
    def _interleaved_group_images(
        groups: dict[str, list[str]], indices: list[str]
    ) -> list[str]:
        count = max((len(groups[index]) for index in indices), default=0)
        return [
            groups[index][image]
            for image in range(count)
            for index in indices
            if image < len(groups[index])
        ]

    @staticmethod
    def _comparison_thumb_size(group_count: int) -> str:
        if group_count < 3:
            return "w350"
        return {3: "w300", 4: "w200", 5: "w150"}.get(group_count, "w100")

    async def _screenshot_image_files(self, meta: Meta) -> list[str]:
        root = screenshots_dir(meta.base_dir, meta.uuid)
        return await asyncio.to_thread(self._visible_screenshot_files, root)

    @classmethod
    def _visible_screenshot_files(cls, root: Path) -> list[str]:
        files = cls._glob_paths(root, ("*.png", ".[!.]*.png"))
        unwanted = cls._unwanted_screenshot_paths(root)
        return sorted(str(path) for path in files - unwanted)

    @staticmethod
    def _glob_paths(root: Path, patterns: tuple[str, ...]) -> set[Path]:
        result: set[Path] = set()
        for pattern in patterns:
            result.update(root.glob(pattern))
        return result

    @classmethod
    def _unwanted_screenshot_paths(cls, root: Path) -> set[Path]:
        patterns = tuple(
            value
            for base in ("FILE*", "PLAYLIST*", "POSTER*")
            for value in (base, f".{base}")
        )
        return cls._glob_paths(root, patterns)

    @staticmethod
    def _image_upload_count(meta: Meta, files: list[str]) -> int:
        if meta.comparison:
            return len(files)
        maximum = 3 if meta.category == "TV" and meta.tv_pack == 0 else 6
        return min(len(files), maximum)

    async def _load_image_upload_files(
        self, image_files: list[str]
    ) -> dict[str, tuple[str, bytes, str]]:
        upload: dict[str, tuple[str, bytes, str]] = {}
        for index, file_path in enumerate(image_files):
            loaded = await self._load_image_upload_file(file_path)
            if loaded is not None:
                upload[f"images_files[{index}]"] = loaded
        return upload

    async def _load_image_upload_file(
        self, file_path: str
    ) -> tuple[str, bytes, str] | None:
        try:
            path = Path(file_path)
            async with aiofiles.open(path, "rb") as handle:
                content = await handle.read()
            return path.name, content, "image/png"
        except (OSError, ValueError) as error:
            logger.error(
                f"{self.tracker}: [red]Failed to open {escape(file_path)}: {escape(str(error))}"
            )
            return None

    def _image_upload_data(
        self, meta: Meta, thumb_size: str
    ) -> dict[str, Any]:
        return {
            "username": self.username,
            "passkey": self.passkey,
            "galleryoption": "1",
            "galleryname": meta.name,
            "thumbsize": thumb_size,
        }

    async def _upload_image_batch(
        self, files: dict[str, tuple[str, bytes, str]], data: dict[str, Any]
    ) -> str | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://img.hdbits.org/upload_api.php", data=data, files=files
            )
        if response.status_code == 200:
            logger.info(f"{self.tracker}: [green]Upload successful!")
            return response.text
        logger.info(
            f"{self.tracker}: [red]Upload failed with status code {response.status_code}"
        )
        return None

    async def _upload_comparison_images(
        self,
        files: dict[str, tuple[str, bytes, str]],
        group_indices: list[str],
        data: dict[str, Any],
    ) -> str | None:
        group_count = len(group_indices) or 3
        chunks = self._comparison_chunks(files, group_count)
        responses: list[str] = []
        for index, chunk in enumerate(chunks):
            response = await self._upload_comparison_chunk(
                index, len(chunks), chunk, data
            )
            if response is None:
                return None
            responses.append(response)
        return "".join(responses)

    @staticmethod
    def _comparison_chunks(
        files: dict[str, tuple[str, bytes, str]],
        group_count: int,
    ) -> list[dict[str, tuple[str, bytes, str]]]:
        rows = HDBits._comparison_rows(list(files.values()), group_count)
        return HDBits._chunk_comparison_rows(rows, 100 * 1024 * 1024)

    @staticmethod
    def _comparison_rows(
        entries: list[tuple[str, bytes, str]],
        group_count: int,
    ) -> list[list[tuple[str, bytes, str]]]:
        return [
            entries[index : index + group_count]
            for index in range(0, len(entries), group_count)
        ]

    @staticmethod
    def _chunk_comparison_rows(
        rows: list[list[tuple[str, bytes, str]]],
        max_size: int,
    ) -> list[dict[str, tuple[str, bytes, str]]]:
        chunks: list[dict[str, tuple[str, bytes, str]]] = []
        current: dict[str, tuple[str, bytes, str]] = {}
        current_size = 0
        for row in rows:
            row_size = HDBits._comparison_row_size(row)
            if HDBits._comparison_chunk_full(
                current, current_size, row_size, max_size
            ):
                chunks.append(current)
                current = {}
                current_size = 0
            current_size += HDBits._append_comparison_row(current, row)
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _comparison_row_size(row: list[tuple[str, bytes, str]]) -> int:
        return sum(len(item[1]) for item in row)

    @staticmethod
    def _comparison_chunk_full(
        current: dict[str, tuple[str, bytes, str]],
        current_size: int,
        row_size: int,
        max_size: int,
    ) -> bool:
        return bool(current) and current_size + row_size > max_size

    @staticmethod
    def _append_comparison_row(
        current: dict[str, tuple[str, bytes, str]],
        row: list[tuple[str, bytes, str]],
    ) -> int:
        for item in row:
            current[f"images_files[{len(current)}]"] = item
        return sum(len(item[1]) for item in row)

    async def _upload_comparison_chunk(
        self,
        index: int,
        total: int,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, Any],
    ) -> str | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://img.hdbits.org/upload_api.php", data=data, files=files
            )
        if response.status_code == 200:
            logger.info(
                f"{self.tracker}: [green]Chunk {index + 1}/{total} upload successful!"
            )
            return response.text
        logger.info(
            f"{self.tracker}: [red]Chunk {index + 1}/{total} upload failed with status code {response.status_code}"
        )
        return None

    @staticmethod
    def _format_comparison_bbcode(bbcode: str, group_count: int) -> str:
        matches = re.findall(r"\[url=.*?\]\[img\].*?\[/img\]\[/url\]", bbcode)
        rows = [
            " ".join(matches[index : index + group_count])
            for index in range(0, len(matches), group_count)
        ]
        return "\n".join(rows)

    async def get_info_from_torrent_id(
        self, hdb_id: int
    ) -> tuple[int | None, int | None, str | None, str | None, str | None]:
        try:
            payload = await self._torrent_info_payload(hdb_id)
            return self._torrent_info_tuple(payload)
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [red]Request error: {escape(str(error))}[/red]"
            )
            return None, None, None, None, None
        except (ValueError, KeyError, IndexError, TypeError) as error:
            logger.error(
                f"{self.tracker}: [red]Failed to parse HDBITS response. Error: {escape(str(error))}[/red]"
            )
            return None, None, None, None, None

    async def _torrent_info_payload(self, hdb_id: int) -> dict[str, Any]:
        url = f"{self.base_url}/api/torrents"
        data = {
            "username": self.username,
            "passkey": self.passkey,
            "id": hdb_id,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("HDBits torrent response must be a JSON object")
        return cast(dict[str, Any], payload)

    def _torrent_info_tuple(
        self, payload: dict[str, Any]
    ) -> tuple[int | None, int | None, str | None, str | None, str | None]:
        if payload.get("status") != 0 or not payload.get("data"):
            logger.info(
                f"{self.tracker}: [red]API returned error status {payload.get('status', 'unknown')}: {payload.get('message', 'No error message provided')}[/red]"
            )
            return None, None, None, None, None
        first = self._first_mapping(payload.get("data"))
        return (
            self._external_numeric_id(first.get("imdb")),
            self._external_numeric_id(first.get("tvdb")),
            self._optional_string(first.get("name")),
            self._optional_string(first.get("hash")),
            self._optional_string(first.get("descr")),
        )

    @staticmethod
    def _first_mapping(value: Any) -> dict[str, Any]:
        if (
            not isinstance(value, list)
            or not value
            or not isinstance(value[0], dict)
        ):
            raise ValueError("HDBits response contains no torrent object")
        return cast(dict[str, Any], value[0])

    @staticmethod
    def _external_numeric_id(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        return int(value.get("id") or 0)

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return None if value is None else str(value)

    async def search_filename(
        self, search_term: str, search_file_folder: str, meta: Meta
    ) -> tuple[
        int | None, int | None, str | None, str | None, str | None, int | None
    ]:
        data = await self._filename_search_payload(
            search_term, search_file_folder, meta
        )
        if data is None:
            return self._empty_filename_result()
        try:
            payload = await self._filename_search_response(data)
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [red]Request error: {escape(str(error))}[/red]"
            )
            return self._empty_filename_result()
        result = self._filename_search_result(payload)
        if result[-1] is None:
            logger.info(
                f"{self.tracker}: [yellow]Could not find a matching release on {self.tracker}[/yellow]"
            )
        return result

    async def _filename_search_payload(
        self, search_term: str, search_file_folder: str, meta: Meta
    ) -> dict[str, Any] | None:
        base = {
            "username": self.username,
            "passkey": self.passkey,
            "limit": 100,
        }
        if search_file_folder == "folder" and meta.is_disc:
            title = await self._disc_search_title(meta)
            if not title:
                return None
            logger.info(
                f"{self.tracker}: [green]Searching {self.tracker} for title: [bold yellow]{escape(title)}[/bold yellow]"
            )
            return {**base, "search": title}
        filename = Path(search_term).name
        logger.info(
            f"{self.tracker}: [green]Searching {self.tracker} for file: [bold yellow]{escape(filename)}[/bold yellow]"
        )
        return {**base, "file_in_torrent": filename}

    async def _disc_search_title(self, meta: Meta) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / "BD_SUMMARY_00.txt"
        try:
            async with aiofiles.open(path, encoding="utf-8") as handle:
                lines = await handle.readlines()
        except FileNotFoundError:
            logger.error(
                f"{self.tracker}: [red]Error: File not found at {path}[/red]"
            )
            return ""
        title = self._disc_title_from_lines(lines)
        return title or str(meta.basename_no_ext or "")

    @staticmethod
    def _disc_title_from_lines(lines: list[str]) -> str:
        for line in lines:
            if "Disc Title:" in line:
                return line.split("Disc Title:", 1)[1].strip()
        return ""

    async def _filename_search_response(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/torrents"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=data)
        if not response.is_success:
            logger.error(
                f"{self.tracker}: [red]Failed to get info from {self.tracker}. Status code: {response.status_code}, Reason: {escape(str(response.reason_phrase))}[/red]"
            )
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("HDBits search response must be a JSON object")
        return cast(dict[str, Any], payload)

    def _filename_search_result(
        self, payload: dict[str, Any]
    ) -> tuple[
        int | None, int | None, str | None, str | None, str | None, int | None
    ]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            logger.error(
                f"{self.tracker}: [red]Error: 'data' key not found or empty in {self.tracker} API response. Full response: {escape(str(payload))}[/red]"
            )
            return self._empty_filename_result()
        if not rows:
            logger.info(
                f"{self.tracker}: [yellow]No data found in the {self.tracker} API response[/yellow]"
            )
            return self._empty_filename_result()
        first = self._first_mapping(rows)
        result = self._filename_result_tuple(first)
        logger.info(
            f"{self.tracker}: [bold green]Matched release with {self.tracker} ID: [yellow]{self.base_url}/details.php?id={result[-1]}[/yellow][/bold green]"
        )
        return result

    @classmethod
    def _filename_result_tuple(
        cls, entry: dict[str, Any]
    ) -> tuple[int, int, str | None, str | None, str | None, int | None]:
        torrent_id = entry.get("id")
        return (
            cls._external_numeric_id(entry.get("imdb")),
            cls._external_numeric_id(entry.get("tvdb")),
            cls._optional_string(entry.get("name")),
            cls._optional_string(entry.get("hash")),
            cls._optional_string(entry.get("descr")),
            int(torrent_id) if torrent_id is not None else None,
        )

    @staticmethod
    def _empty_filename_result() -> tuple[None, None, None, None, None, None]:
        return None, None, None, None, None, None
