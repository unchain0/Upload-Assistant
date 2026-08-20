# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.image_hosts.rehosting import (
    ImageHostPolicy,
    RehostImagesManager,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class DigitalCore:
    """
    DIGITALCORE (DC) is a Private Torrent Tracker for 0DAY / GENERAL
    """

    auth_type = "other_api"
    tracker = "DIGITALCORE"
    display_name = "DigitalCore"
    base_url = "https://digitalcore.club"
    api_base_url = f"{base_url}/api/v1/torrents"
    banned_groups = ("",)
    # PTScreens is allowed by DigitalCore's CSP img-src directive.
    approved_image_hosts = (
        "imgbox",
        "imgbb",
        "bhd",
        "imgur",
        "postimg",
        "sharex",
        "ptscreens",
    )
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "imgbox.com": "imgbox",
            "beyondhd.co": "bhd",
            "imgur.com": "imgur",
            "postimg.cc": "postimg",
            "digitalcore.club": "sharex",
            "img.digitalcore.club": "sharex",
            "ptscreens.com": "ptscreens",
        },
        approved_image_hosts,
    )
    torrent_url = f"{base_url}/torrent/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("tracker.digitalcore.club", "trackerprxy.digitalcore.club")
    allows_bloated_audio = True

    def __init__(self, config: Config):
        self.config = config
        self.common = Common(config)
        self.rehost_images_manager = RehostImagesManager(config)
        self.api_key = (
            self.config["TRACKERS"][self.tracker].get("api_key") or ""
        )
        self.session = httpx.AsyncClient(
            headers={"X-API-KEY": self.api_key}, timeout=30.0
        )

    async def mediainfo(self, meta: Meta) -> str:
        mediainfo = ""
        if meta.category in ("TV", "MOVIE", "MUSIC") or meta.audiobook:
            if meta.is_disc == "BDMV":
                mediainfo = await self.common.get_bdmv_mediainfo(
                    meta, remove=["File size", "Overall bit rate"]
                )
            else:
                mi_path = (
                    f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/MEDIAINFO_"
                    + "CLEANPATH.txt"
                )
                async with aiofiles.open(mi_path, encoding="utf-8") as f:
                    mediainfo = await f.read()

        return mediainfo

    async def generate_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        return await builder.general_description_generator(
            meta,
            approved_image_hosts=self.approved_image_hosts,
            bluray=False,
            custom_signature=False,
            logo=False,
            signature=f"[center][url=https://github.com/wastaken7/Upload-Assistant]{meta.ua_signature}[/url][/center]",
        )

    def get_category_id(self, meta: Meta) -> int | None:
        disc_category = self._disc_category_id(meta)
        if disc_category is not None:
            return disc_category
        special_category = self._special_category_id(meta)
        if special_category is not None:
            return special_category
        return self._video_category_id(meta)

    @staticmethod
    def _disc_category_id(meta: Meta) -> int | None:
        if meta.is_disc == "BDMV":
            return DigitalCore._bdmv_category_id(
                meta.category, meta.resolution
            )
        if meta.is_disc == "DVD":
            return {"MOVIE": 1, "TV": 11}.get(str(meta.category))
        return None

    @staticmethod
    def _bdmv_category_id(
        category: str | None, resolution: str | None
    ) -> int | None:
        if category == "TV":
            return 14
        if category != "MOVIE":
            return None
        return {"1080p": 3, "2160p": 38}.get(str(resolution))

    @classmethod
    def _special_category_id(cls, meta: Meta) -> int | None:
        tv_pack = cls._tv_pack_category_id(meta)
        if tv_pack is not None:
            return tv_pack
        resolvers: dict[str, Callable[[Meta], int | None]] = {
            "BOOK": cls._book_category_id,
            "GAME": cls._game_meta_category_id,
            "MUSIC": cls._music_meta_category_id,
        }
        resolver = resolvers.get(str(meta.category))
        return None if resolver is None else resolver(meta)

    @staticmethod
    def _book_category_id(meta: Meta) -> int:
        return 44 if meta.audiobook else 28

    @classmethod
    def _game_meta_category_id(cls, meta: Meta) -> int:
        return cls._game_category_id(meta.platform)

    @classmethod
    def _music_meta_category_id(cls, meta: Meta) -> int | None:
        return cls._music_category_id(meta.format)

    @staticmethod
    def _tv_pack_category_id(meta: Meta) -> int | None:
        return 12 if meta.category == "TV" and meta.tv_pack == 1 else None

    @staticmethod
    def _game_category_id(platform: str | None) -> int:
        return {"PC": 25, "MAC": 27}.get(str(platform), 26)

    @staticmethod
    def _music_category_id(format_value: str | None) -> int | None:
        return {"FLAC": 23, "MP3": 22}.get(str(format_value or "").upper())

    @staticmethod
    def _video_category_id(meta: Meta) -> int | None:
        category = str(meta.category)
        if meta.sd == 1:
            return {"MOVIE": 2, "TV": 10}.get(category)
        category_map = {
            "MOVIE": {"2160p": 4, "1080p": 6, "1080i": 6, "720p": 5},
            "TV": {"2160p": 13, "1080p": 9, "1080i": 9, "720p": 8},
        }
        resolution_map = category_map.get(category)
        return (
            None
            if resolution_map is None
            else resolution_map.get(str(meta.resolution))
        )

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        response = await self.session.get(
            self.api_base_url,
            params=self._search_params(meta),
            headers=self.session.headers,
            timeout=15,
        )
        response.raise_for_status()
        return self._matching_search_results(
            response, self.get_category_id(meta), meta
        )

    @staticmethod
    def _search_params(meta: Meta) -> dict[str, str]:
        imdb_id = (
            meta.imdb_info.get("imdbID")
            if isinstance(meta.imdb_info, dict)
            else None
        )
        return (
            {"searchText": str(imdb_id)}
            if imdb_id
            else {"search": str(meta.title)}
        )

    @classmethod
    def _matching_search_results(
        cls,
        response: httpx.Response,
        category_id: int | None,
        meta: Meta | None = None,
    ) -> list[dict[str, Any]]:
        payload = cls._search_payload(response)
        return [
            entry
            for item in payload
            if (entry := cls._dupe_entry(item, category_id, meta)) is not None
        ]

    @staticmethod
    def _search_payload(response: httpx.Response) -> list[Any]:
        if not response.text or response.text == "[]":
            return []
        payload: Any = response.json()
        return cast(list[Any], payload) if isinstance(payload, list) else []

    @classmethod
    def _dupe_entry(
        cls,
        item: Any,
        category_id: int | None,
        meta: Meta | None = None,
    ) -> dict[str, Any] | None:
        entry = cls._category_entry(item, category_id)
        if entry is None:
            return None
        name = str(entry.get("name") or "")
        semantics = cls._candidate_semantics(name)
        if not cls._candidate_allowed(meta, semantics):
            return None
        return cls._dupe_payload(entry, name, semantics)

    @staticmethod
    def _category_entry(
        item: Any, category_id: int | None
    ) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        entry = cast(dict[str, Any], item)
        return entry if entry.get("category") == category_id else None

    @classmethod
    def _candidate_allowed(
        cls, meta: Meta | None, semantics: dict[str, str]
    ) -> bool:
        return meta is None or cls._candidate_matches_release(meta, semantics)

    @classmethod
    def _dupe_payload(
        cls,
        entry: dict[str, Any],
        name: str,
        semantics: dict[str, str],
    ) -> dict[str, Any]:
        torrent_id = entry.get("id")
        return {
            "id": torrent_id,
            "download": f"{cls.api_base_url}/download/{torrent_id}",
            "file_count": entry.get("numfiles", ""),
            "name": name,
            "size": entry.get("size"),
            "type": semantics["type"],
            "res": semantics["resolution"],
            "link": f"{cls.torrent_url}{torrent_id}/" if torrent_id else None,
        }

    @classmethod
    def _candidate_matches_release(
        cls,
        meta: Meta,
        semantics: dict[str, str],
    ) -> bool:
        if meta.category not in {"MOVIE", "TV"}:
            return True
        target = cls._target_semantics(meta)
        keys = ("resolution", "type", "codec")
        compatible = all(
            cls._semantic_value_matches(target[key], semantics[key])
            for key in keys
        )
        return compatible and cls._has_required_candidate_semantics(semantics)

    @staticmethod
    def _semantic_value_matches(target: str, candidate: str) -> bool:
        return not target or not candidate or target == candidate

    @staticmethod
    def _has_required_candidate_semantics(semantics: dict[str, str]) -> bool:
        return bool(semantics["resolution"] and semantics["type"])

    @classmethod
    def _target_semantics(cls, meta: Meta) -> dict[str, str]:
        source = cls._first_text(meta.type, meta.source)
        codec = cls._first_text(meta.video_encode, meta.video_codec)
        return {
            "resolution": str(meta.resolution or "").lower(),
            "type": cls._normalized_source_type(source),
            "codec": cls._normalized_codec(codec),
        }

    @staticmethod
    def _first_text(*values: Any) -> str:
        return next((str(value) for value in values if value), "")

    @classmethod
    def _candidate_semantics(cls, name: str) -> dict[str, str]:
        lowered = name.lower()
        resolution_match = re.search(
            r"(?<!\d)(480p|576p|720p|1080[pi]|1440p|2160p)(?!\d)", lowered
        )
        return {
            "resolution": resolution_match.group(1)
            if resolution_match
            else "",
            "type": cls._source_type_from_name(lowered),
            "codec": cls._codec_from_name(lowered),
        }

    @classmethod
    def _source_type_from_name(cls, value: str) -> str:
        compact = re.sub(r"[ ._-]+", "", value).upper()
        return next(
            (
                kind
                for pattern, kind in cls._source_patterns()
                if re.search(pattern, compact)
            ),
            "",
        )

    @staticmethod
    def _source_patterns() -> tuple[tuple[str, str], ...]:
        return (
            (r"WEBRIP", "WEBRIP"),
            (r"WEBDL", "WEBDL"),
            (r"REMUX", "REMUX"),
            (r"HDTV", "HDTV"),
            (r"BLURAY|BDRIP|BRRIP|ENCODE", "BLURAY"),
        )

    @classmethod
    def _normalized_source_type(cls, value: str) -> str:
        compact = re.sub(r"[ ._-]+", "", value).upper()
        if compact == "WEB":
            return "WEBDL"
        detected = cls._source_type_from_name(compact)
        return detected if detected else compact

    @staticmethod
    def _codec_from_name(value: str) -> str:
        if re.search(r"(?:x|h[ .]?)265|hevc", value, re.IGNORECASE):
            return "H265"
        if re.search(r"(?:x|h[ .]?)264|avc", value, re.IGNORECASE):
            return "H264"
        return ""

    @classmethod
    def _normalized_codec(cls, value: str) -> str:
        return cls._codec_from_name(value)

    async def get_name(self, meta: Meta) -> str:
        scene_name = meta.scene_name or ""
        if self._use_metadata_name():
            return self._metadata_release_name(meta, scene_name)
        return f"{scene_name} [NORAR]" if scene_name else meta.basename_no_ext

    def _use_metadata_name(self) -> bool:
        return bool(
            self.config["TRACKERS"][self.tracker].get(
                "use_metadata_name", False
            )
        )

    @classmethod
    def _metadata_release_name(cls, meta: Meta, scene_name: str) -> str:
        base_name = scene_name if scene_name else (meta.clean_name or "")
        sanitized = cls._sanitize_release_name(base_name)
        return f"{sanitized} [NORAR]" if scene_name else sanitized

    @staticmethod
    def _sanitize_release_name(name: str) -> str:
        normalized = (
            name.replace("DD+", "DDP")
            .replace("DTS:", "DTS-")
            .replace("HDR10+", "HDR10P")
        )
        normalized = unicodedata.normalize("NFD", normalized)
        normalized = "".join(
            char
            for char in normalized
            if char.isascii() and (char.isalnum() or char in (" ", ".", "-"))
        )
        return normalized.replace("!", "")

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._codec_policy_passes(meta):
            return False
        if not self._source_policy_passes(meta):
            return False
        if str(meta.category).upper() not in {"MOVIE", "TV"}:
            return True
        if not self._file_policy_passes(meta):
            return False
        return self._screenshot_policy_passes(meta)

    def _codec_policy_passes(self, meta: Meta) -> bool:
        values = (
            str(meta.video_codec or "").lower(),
            str(meta.video_encode or "").lower(),
        )
        if not self._contains_forbidden_codec(values):
            return True
        logger.info(f"{self.tracker}: DivX/XviD uploads are not allowed.")
        return False

    @staticmethod
    def _contains_forbidden_codec(values: tuple[str, str]) -> bool:
        return any(
            forbidden in value
            for forbidden in ("divx", "xvid")
            for value in values
        )

    def _source_policy_passes(self, meta: Meta) -> bool:
        source = str(meta.source or "").upper()
        media_type = str(meta.type or "").upper()
        if not self._has_forbidden_source(meta, source, media_type):
            return True
        logger.info(f"{self.tracker}: CAM/TS uploads are not allowed.")
        return False

    @classmethod
    def _has_forbidden_source(
        cls, meta: Meta, source: str, media_type: str
    ) -> bool:
        if cls._direct_forbidden_source(source, media_type):
            return True
        return cls._contains_forbidden_source_marker(
            cls._release_context(meta, source, media_type)
        )

    @staticmethod
    def _direct_forbidden_source(source: str, media_type: str) -> bool:
        return source in {"CAM", "TS"} or media_type in {"CAM", "TS"}

    @staticmethod
    def _release_context(meta: Meta, source: str, media_type: str) -> str:
        values = (
            meta.name,
            meta.scene_name,
            meta.tag,
            meta.title,
            source,
            media_type,
        )
        return " ".join(str(value) for value in values if value)

    def _file_policy_passes(self, meta: Meta) -> bool:
        for item in self._filelist(meta):
            if self._is_rar_file(str(item)):
                logger.info(
                    f"{self.tracker}: RAR files are not allowed: {item}"
                )
                return False
        return True

    @staticmethod
    def _filelist(meta: Meta) -> list[Any]:
        value = meta.filelist
        return list(value) if isinstance(value, (list, tuple, set)) else []

    def _screenshot_policy_passes(self, meta: Meta) -> bool:
        return all(
            self._screenshot_is_allowed(image)
            for image in self._image_list(meta)
        )

    @staticmethod
    def _image_list(meta: Meta) -> list[Any]:
        value = meta.image_list
        return list(value) if isinstance(value, (list, tuple)) else []

    def _screenshot_is_allowed(self, image: Any) -> bool:
        url = self._screenshot_url(image)
        if not url or Path(url).suffix.lower() != ".webp":
            return True
        logger.info(
            f"{self.tracker}: Screenshots for DIGITALCORE must be JPG/PNG/GIF."
            " WEBP is not allowed."
        )
        return False

    @staticmethod
    def _screenshot_url(image: Any) -> str:
        if isinstance(image, str):
            return image.strip()
        if not isinstance(image, Mapping):
            return ""
        mapping = cast(Mapping[str, Any], image)
        return next(
            (
                value
                for key in ("raw_url", "img_url", "web_url")
                if (value := str(mapping.get(key, "")).strip())
            ),
            "",
        )

    @staticmethod
    def _contains_forbidden_source_marker(value: str) -> bool:
        return bool(
            re.search(
                r"(?<![A-Za-z0-9])(?:CAM|TS)(?![A-Za-z0-9])",
                value,
                re.IGNORECASE,
            )
        )

    async def get_firstpic(self, meta: Meta) -> str:
        if meta.category in ("BOOK", "MUSIC"):
            covers = meta.hosted_artwork
            if isinstance(covers, list) and len(covers) > 0:
                raw_url = covers[0].get("raw_url")
                if raw_url:
                    return str(raw_url)
        return ""

    @staticmethod
    def _is_rar_file(path_value: str) -> bool:
        lowered = path_value.lower()
        if lowered.endswith(".rar"):
            return True
        return bool(re.search(r"\.r\d{2,}$", lowered))

    async def fetch_data(self, meta: Meta) -> dict[str, Any]:
        anon = (
            "1"
            if meta.anon
            or self.config["TRACKERS"][self.tracker].get("anon", False)
            else "0"
        )

        return {
            "category": self.get_category_id(meta),
            "imdbId": meta.imdb_tt,
            "nfo": await self.generate_description(meta),
            "mediainfo": await self.mediainfo(meta),
            "reqid": "0",
            "section": "new",
            "frileech": "1",
            "anonymousUpload": anon,
            "p2p": "0",
            "unrar": "1",
            "firstpic": await self.get_firstpic(meta),
            "language": meta.book_language,
        }

    async def upload(self, meta: Meta) -> bool:
        data = await self.fetch_data(meta)
        torrent_title = await self.get_name(meta)
        status = meta.tracker_status.setdefault(self.tracker, {})
        if meta.debug:
            return await self._debug_upload(meta, data, status)
        return await self._upload_release(meta, data, torrent_title, status)

    async def _upload_release(
        self,
        meta: Meta,
        data: dict[str, Any],
        torrent_title: str,
        status: dict[str, Any],
    ) -> bool:
        try:
            response = await self._submit_upload(meta, data, torrent_title)
            return await self._handle_upload_response(meta, status, response)
        except httpx.HTTPStatusError as error:
            status["status_message"] = (
                f"data error: HTTP {error.response.status_code} -"
                f" {error.response.text}"
            )
            return False
        except httpx.TimeoutException:
            status["status_message"] = (
                "data error: Request timed out after"
                f" {self.session.timeout.write} seconds"
            )
            return False
        except httpx.RequestError as error:
            status["status_message"] = self._request_error_message(error)
            return False
        except Exception as error:
            status["status_message"] = (
                "data error: It may have uploaded, go check. Error:"
                f" {error}.\nResponse: No response received"
            )
            return False

    async def _debug_upload(
        self, meta: Meta, data: dict[str, Any], status: dict[str, Any]
    ) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        status["status_message"] = "Debug mode enabled, not uploading"
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _submit_upload(
        self, meta: Meta, data: dict[str, Any], torrent_title: str
    ) -> httpx.Response:
        await self.common.create_torrent_for_upload(
            meta, self.tracker, "DigitalCore.club"
        )
        files = {
            "file": (
                f"{torrent_title}.torrent",
                await self._torrent_bytes(meta),
                "application/x-bittorrent",
            )
        }
        response = await self.session.post(
            f"{self.api_base_url}/upload",
            data=data,
            files=files,
            headers=dict(self.session.headers),
            timeout=90,
        )
        response.raise_for_status()
        return response

    async def _torrent_bytes(self, meta: Meta) -> bytes:
        torrent_path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            return await torrent_file.read()

    async def _handle_upload_response(
        self, meta: Meta, status: dict[str, Any], response: httpx.Response
    ) -> bool:
        response_data = self._response_data(response)
        if response.status_code != 200 or not response_data.get("id"):
            status["status_message"] = (
                "data error:"
                f" {response_data.get('message', 'Unknown API error.')}"
            )
            return False
        torrent_id = str(response_data["id"])
        status["torrent_id"] = f"{torrent_id}/"
        status["status_message"] = response_data.get("message")
        await self.common.download_tracker_torrent(
            meta,
            self.tracker,
            headers=dict(self.session.headers),
            downurl=f"{self.api_base_url}/download/{torrent_id}",
        )
        return True

    @staticmethod
    def _response_data(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _request_error_message(error: httpx.RequestError) -> str:
        response_text = getattr(
            getattr(error, "response", None), "text", "No response received"
        )
        return (
            f"data error: Unable to upload. Error: {error}.\nResponse:"
            f" {response_text}"
        )
