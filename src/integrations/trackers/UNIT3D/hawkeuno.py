# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

import aiofiles
import httpx
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.image_hosts.rehosting import ImageHostPolicy, RehostImagesManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.UNIT3D import UNIT3D


class HawkeUno(UNIT3D):
    """
    hawke-uno (HUNO) is a Private Torrent Tracker for HD MOVIES / TV
    """

    tracker = "HAWKEUNO"
    display_name = "HawkeUno"
    allows_bloated_audio = True
    source_flag = "HUNO"
    base_url = "https://hawke.uno"
    banned_groups = (
        "4K4U",
        "Bearfish",
        "BiTOR",
        "BONE",
        "D3FiL3R",
        "d3g",
        "DTR",
        "ELiTE",
        "EVO",
        "eztv",
        "EzzRips",
        "FGT",
        "HashMiner",
        "HETeam",
        "HEVCBay",
        "HiQVE",
        "HR-DR",
        "iFT",
        "ION265",
        "iVy",
        "JATT",
        "Joy",
        "LAMA",
        "m3th",
        "MeGusta",
        "MRN",
        "Musafirboy",
        "OEPlus",
        "Pahe.in",
        "PHOCiS",
        "PSA",
        "RARBG",
        "RMTeam",
        "ShieldBearer",
        "SiQ",
        "TBD",
        "Telly",
        "TSP",
        "VXT",
        "WKS",
        "YAWNiX",
        "YIFY",
        "YTS",
    )
    approved_image_hosts = (
        "imgbox",
        "imgbb",
        "pixhost",
        "bam",
        "onlyimage",
        "ptscreens",
        "passtheimage",
        "hawke.pics",
    )
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "pixhost.to": "pixhost",
            "imgbox.com": "imgbox",
            "imagebam.com": "bam",
            "hawke.pics": "hawke.pics",
            "onlyimage.org": "onlyimage",
            "ptscreens.com": "ptscreens",
            "passtheimage.me": "passtheimage",
        },
        approved_image_hosts,
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/api/requests/filter"
    tracker_urls = ("https://hawke.uno",)
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, "HAWKEUNO")
        self.config = config
        self.common = Common(config)
        self.rehost_images_manager = RehostImagesManager(config)
        self.announce_url = str(self.config.get("TRACKERS", {}).get(self.tracker, {}).get("announce_url", "")).strip()

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.type == "WEBRIP":
            logger.info(f"{self.tracker}: [bold red]WEB-RIP is not allowed, skipping upload.[/bold red]")
            return False
        if not await self._language_policy_passes(meta):
            return False
        if not meta.valid_mi_settings:
            logger.info(f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping upload.[/bold red]")
            return False
        return self._codec_quality_policy_passes(meta)

    async def _language_policy_passes(self, meta: Meta) -> bool:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        if meta.audio_languages:
            return True
        logger.info(f"{self.tracker}: [bold red]No audio languages found, skipping upload.[/bold red]")
        return False

    def _codec_quality_policy_passes(self, meta: Meta) -> bool:
        if not self._needs_hevc_quality_check(meta):
            return True
        return all(self._video_quality_policy_passes(meta, track) for track in self._video_tracks(meta))

    @staticmethod
    def _needs_hevc_quality_check(meta: Meta) -> bool:
        if meta.is_disc or meta.type not in {"ENCODE", "DVDRIP", "HDTV"}:
            return False
        return "x265" in str(meta.video_encode) or "HEVC" in str(meta.video_codec)

    @classmethod
    def _video_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [track for track in cls._media_tracks(meta) if track.get("@type") == "Video"]

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        tracks = media.get("track", [])
        if not isinstance(tracks, list):
            return []
        return [track for track in tracks if isinstance(track, dict)]

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return media if isinstance(media, dict) else {}

    def _video_quality_policy_passes(self, meta: Meta, track: dict[str, Any]) -> bool:
        settings = track.get("Encoded_Library_Settings", {})
        if not settings:
            return True
        crf = self._crf_value(settings)
        if crf is not None:
            return self._crf_policy_passes(meta, crf)
        return self._bitrate_policy_passes(meta, track.get("BitRate"))

    @staticmethod
    def _crf_value(settings: Any) -> float | None:
        match = re.search(r"crf[ =:]+([\d.]+)", str(settings), re.IGNORECASE)
        return float(match.group(1)) if match else None

    def _crf_policy_passes(self, meta: Meta, crf: float) -> bool:
        logger.debug(f"{self.tracker}: Found CRF value: {crf}")
        if crf <= 22:
            return True
        self._log_attended(meta, f"CRF value too high: {crf} for HawkeUno")
        return False

    def _bitrate_policy_passes(self, meta: Meta, value: Any) -> bool:
        logger.debug(f"{self.tracker}: No CRF value found in encoding settings.")
        if "Animation" in self._genre_values(meta):
            return True
        bitrate = self._bitrate_kbps(value)
        if bitrate is None or bitrate >= 3000:
            return True
        self._log_attended(meta, f"Video bitrate too low: {bitrate:.0f} kbps for HawkeUno")
        return False

    @staticmethod
    def _genre_values(meta: Meta) -> list[str]:
        value = meta.genre
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)] if value else []

    @staticmethod
    def _bitrate_kbps(value: Any) -> float | None:
        try:
            return int(value) / 1000 if value else None
        except TypeError, ValueError:
            return None

    def _log_attended(self, meta: Meta, message: str) -> None:
        if not meta.unattended:
            logger.info(f"{self.tracker}: {message}")

    async def get_description(self, meta: Meta) -> None:
        desc = await DescriptionBuilder(self.tracker, self.config).general_description_generator(
            meta,
            mediainfo=False,
            nfo=False,
            approved_image_hosts=self.approved_image_hosts,
            signature=f"[right][url=https://github.com/wastaken7/Upload-Assistant][size=8]{meta.ua_signature}[/size][/url][/right]",
        )
        async with aiofiles.open(f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt", "w", encoding="utf-8") as f:
            await f.write(desc)

    async def get_internal(self, meta: Meta) -> int:
        if not meta.tag:
            return 0
        enabled = self.tracker_config.get("internal", False) is True
        groups = self.tracker_config.get("internal_groups", [])
        return int(enabled and meta.tag[1:] in groups)

    async def get_resolution_id(self, meta: Meta, resolution: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = {
            "Other": "10",
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "540p": "11",
            "540i": "11",
            "480p": "8",
            "480i": "9",
        }
        return self._mapping_response(mapping, resolution, meta.resolution, reverse=reverse, mapping_only=mapping_only, default="10", key="resolution_id")

    async def get_type_id(self, meta: Meta, type: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        mapping = {"DISC": "1", "REMUX": "2", "WEBDL": "3", "WEBRIP": "15", "HDTV": "15", "ENCODE": "15", "DVDRIP": "15"}
        return self._mapping_response(mapping, type, meta.type, reverse=reverse, mapping_only=mapping_only, default="0", key="type_id")

    @classmethod
    def _mapping_response(
        cls,
        mapping: dict[str, str],
        requested: str,
        fallback: str | None,
        *,
        reverse: bool,
        mapping_only: bool,
        default: str,
        key: str,
    ) -> dict[str, str]:
        mode = cls._mapping_mode(mapping, reverse=reverse, mapping_only=mapping_only)
        if mode is not None:
            return mode
        return {key: mapping.get(cls._selected_value(requested, fallback), default)}

    @staticmethod
    def _mapping_mode(mapping: dict[str, str], *, reverse: bool, mapping_only: bool) -> dict[str, str] | None:
        if mapping_only:
            return mapping
        return {value: name for name, value in mapping.items()} if reverse else None

    @staticmethod
    def _selected_value(requested: str, fallback: str | None) -> str:
        return requested if requested else (fallback or "")

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        await self.get_description(meta)
        data = await self._base_upload_data(meta)
        self._apply_release_metadata(data, meta)
        self._apply_disc_metadata(data, meta)
        self._apply_tv_metadata(data, meta)
        return data

    async def _base_upload_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "category_id": 1 if meta.category == "MOVIE" else 2,
            "type_id": (await self.get_type_id(meta))["type_id"],
            "tmdb": meta.tmdb,
            "anonymous": int(bool(meta.anon) or self.tracker_config.get("anon", False)),
            "imdb": meta.imdb_id,
            "edition": meta.edition,
        }
        if await self.get_internal(meta):
            data["internal"] = 1
        return data

    @staticmethod
    def _apply_release_metadata(data: dict[str, Any], meta: Meta) -> None:
        if meta.repack:
            data["release_tag"] = meta.repack

    @staticmethod
    def _apply_disc_metadata(data: dict[str, Any], meta: Meta) -> None:
        if not meta.is_disc:
            return
        if meta.region:
            data["region"] = meta.region
        if meta.distributor:
            data["distributor"] = meta.distributor

    @staticmethod
    def _apply_tv_metadata(data: dict[str, Any], meta: Meta) -> None:
        if meta.category != "TV":
            return
        optional = {
            "season_number": meta.season_int,
            "episode_number": meta.episode_int,
            "tvdb": meta.tvdb_id,
            "mal": meta.mal_id,
        }
        data.update({key: value for key, value in optional.items() if value})
        data["season_pack"] = meta.tv_pack

    async def get_files(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        files: dict[str, tuple[str, bytes, str]] = {}
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag, announce_url=self.announce_url)
        root = release_temp_dir(meta.base_dir, meta.uuid)
        torrent_path = root / f"[{self.tracker}].torrent"
        async with aiofiles.open(torrent_path, "rb") as f:
            files["torrent"] = (f"{meta.clean_name}.torrent", await f.read(), "application/x-bittorrent")

        desc_path = root / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(desc_path, "rb") as f:
            files["description"] = ("description.txt", await f.read(), "text/plain")

        if meta.is_disc == "BDMV":
            bdinfo_path = root / "BD_SUMMARY_00.txt"
            async with aiofiles.open(bdinfo_path, "rb") as f:
                files["bdinfo"] = ("bdinfo.txt", await f.read(), "text/plain")
        else:
            mediainfo_path = root / "MEDIAINFO_CLEANPATH.txt"
            async with aiofiles.open(mediainfo_path, "rb") as f:
                files["mediainfo"] = ("mediainfo.txt", await f.read(), "text/plain")

        return files

    async def upload(self, meta: Meta) -> bool:
        data = await self.get_data(meta)
        status = meta.tracker_status.setdefault(self.tracker, {})
        if meta.debug:
            return await self._debug_upload(meta, data, status)
        try:
            response = await self._submit_upload(meta, data)
            return self._handle_upload_response(status, response)
        except httpx.HTTPStatusError as error:
            message = f"HTTP {error.response.status_code} - {error.response.text}"
            return self._record_upload_error(status, message, "Upload error")
        except (httpx.RequestError, ValueError, KeyError) as error:
            return self._record_upload_error(status, str(error), "Upload connection/parsing error")
        except Exception as error:
            status["status_message"] = f"data error: {error}"
            logger.info(f"{self.tracker}: [bold red]Upload unexpected error: {escape(str(error))}[/bold red]")
            raise

    async def _debug_upload(self, meta: Meta, data: dict[str, Any], status: dict[str, Any]) -> bool:
        logger.debug(f"{self.tracker}: [cyan]Request Data:")
        logger.debug(Redaction.redact_private_info(data))
        status["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _submit_upload(self, meta: Meta, data: dict[str, Any]) -> httpx.Response:
        files = await self.get_files(meta)
        api_token = str(self.tracker_config.get("api_key", ""))
        url = f"{self.upload_url}?api_token={api_token}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.post(url=url, data=data, files=files)
            response.raise_for_status()
            return response

    def _handle_upload_response(self, status: dict[str, Any], response: httpx.Response) -> bool:
        payload = response.json()
        if payload.get("success") is not True:
            message = str(payload.get("message", "Unknown error"))
            status["status_message"] = f"data error: API error: {message}"
            logger.info(f"{self.tracker}: [yellow]Upload to {self.tracker} failed: {message}[/yellow]")
            return False
        response_data = payload.get("data", {})
        status["status_message"] = self._success_status_message(payload, response_data)
        return True

    @staticmethod
    def _success_status_message(payload: dict[str, Any], response_data: Any) -> str:
        data = response_data if isinstance(response_data, dict) else {}
        moderation = data.get("moderation_status", "")
        warnings = data.get("warnings", [])
        name_issues = data.get("name_issues", [])
        return f"{payload.get('message')}\nModeration Status: {moderation}\nWarnings: {warnings}\nName Issues: {name_issues}"

    def _record_upload_error(self, status: dict[str, Any], message: str, label: str) -> bool:
        status["status_message"] = f"data error: {message}"
        logger.info(f"{self.tracker}: [bold red]{label}: {escape(message)}[/bold red]")
        return False
