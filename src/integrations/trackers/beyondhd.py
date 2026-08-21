# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import httpx
from rich.markup import escape

from src.domain_models.release import Meta
from src.domain_models.release_description import base_description
from src.domain_models.tracker_image_policy import ImageCollection, get_tracker_image_collection
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.image_hosts.rehosting import ImageHostPolicy, RehostImagesManager
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common


class BEYONDHD:
    """
    BHD Private Torrent Tracker
    """

    auth_type = "unit3d_api"
    tracker = "BEYONDHD"
    display_name = "BeyondHD"
    reject_english_original_bloat = True
    source_flag = "BHD"
    banned_groups = (
        "4K4U",
        "AOC",
        "BiTOR",
        "C4K",
        "CRUCiBLE",
        "d3g",
        "EASports",
        "FGT",
        "Flights",
        "iFT",
        "iVy",
        "MeGusta",
        "MezRips",
        "nikt0",
        "OFT",
        "ProRes",
        "QxR",
        "RARBG",
        "ReaLHD",
        "SasukeducK",
        "Sicario",
        "SyncUP",
        "TEKNO3D",
        "Telly",
        "TGS",
        "tigole",
        "TOMMY",
        "WKS",
        "x0r",
        "YIFY",
    )
    approved_image_hosts = ("imgbox", "imgbb", "pixhost", "bhd", "bam")
    image_host_policy = ImageHostPolicy(
        {
            "ibb.co": "imgbb",
            "pixhost.to": "pixhost",
            "imgbox.com": "imgbox",
            "beyondhd.co": "bhd",
            "imagebam.com": "bam",
        },
        approved_image_hosts,
    )
    base_url = "https://beyond-hd.me"
    upload_url = f"{base_url}/api/upload/"
    torrent_url = f"{base_url}/details/"
    tracker_urls = (base_url, "tracker.beyond-hd.me")
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.rehost_images_manager = RehostImagesManager(config)
        self.common = Common(config=config)
        trackers_cfg = cast(dict[str, Any], self.config.get("TRACKERS", {}))
        self.tracker_config = cast(dict[str, Any], trackers_cfg.get("BEYONDHD", {}))
        api_key = str(self.tracker_config.get("api_key", "")).strip()
        self.requests_url = f"{self.base_url}/api/requests/{api_key}"

    async def upload(self, meta: Meta) -> bool:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        await self.edit_desc(meta)
        data, files = await self._upload_request_parts(meta)
        if meta.debug:
            return await self._debug_upload(meta, data)
        details_link = await self._post_upload(meta, data, files)
        if details_link is None:
            return False
        return await self._seed_uploaded_torrent(meta, details_link)

    async def _upload_request_parts(self, meta: Meta) -> tuple[dict[str, Any], dict[str, Any]]:
        tags = await self.get_tags(meta)
        custom, edition = await self.get_edition(meta, tags)
        data: dict[str, Any] = {
            "name": await self.get_name(meta),
            "category_id": await self.get_cat_id(meta.category),
            "type": await self.get_type(meta),
            "source": await self.get_source(str(meta.source)),
            "imdb_id": meta.imdb,
            "tmdb_id": meta.tmdb,
            "description": await self._description_text(meta),
            "anon": self._anonymous(meta),
            "sd": meta.sd,
            "live": await self.get_live(meta),
        }
        self._apply_upload_options(data, meta, tags, custom, edition)
        return data, await self._upload_files(meta)

    async def _description_text(self, meta: Meta) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    async def _upload_files(self, meta: Meta) -> dict[str, Any]:
        media_info = await self._media_info_text(meta)
        torrent_path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        async with aiofiles.open(torrent_path, "rb") as handle:
            torrent_bytes = await handle.read()
        return {"mediainfo": media_info, "file": ("torrent.torrent", torrent_bytes, "application/x-bittorrent")}

    async def _media_info_text(self, meta: Meta) -> str:
        filename = "BD_SUMMARY_00.txt" if meta.is_disc == "BDMV" else "MEDIAINFO.txt"
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    def _anonymous(self, meta: Meta) -> int:
        configured = bool(self.tracker_config.get("anon", False))
        return 0 if meta.anon == 0 and not configured else 1

    def _apply_upload_options(self, data: dict[str, Any], meta: Meta, tags: list[str], custom: bool, edition: str) -> None:
        self._apply_internal(data, meta)
        self._apply_pack_special_region(data, meta)
        self._apply_edition(data, custom, edition)
        if tags:
            data["tags"] = ",".join(tags)

    def _apply_internal(self, data: dict[str, Any], meta: Meta) -> None:
        if not meta.tag or not bool(self.tracker_config.get("internal", False)):
            return
        groups = self.tracker_config.get("internal_groups", [])
        if isinstance(groups, list) and meta.tag[1:] in groups:
            data["internal"] = 1

    @staticmethod
    def _apply_pack_special_region(data: dict[str, Any], meta: Meta) -> None:
        if meta.tv_pack == 1:
            data["pack"] = 1
        if meta.season == "S00":
            data["special"] = 1
        if meta.region in BEYONDHD._allowed_regions():
            data["region"] = meta.region

    @staticmethod
    def _allowed_regions() -> set[str]:
        return {"AUS", "CAN", "CEE", "CHN", "ESP", "EUR", "FRA", "GBR", "GER", "HKG", "ITA", "JPN", "KOR", "NOR", "NLD", "RUS", "TWN", "USA"}

    @staticmethod
    def _apply_edition(data: dict[str, Any], custom: bool, edition: str) -> None:
        if custom:
            data["custom_edition"] = edition
        elif edition:
            data["edition"] = edition

    @staticmethod
    def _user_agent(meta: Meta) -> str:
        version = meta.current_version if meta.current_version is not None else "github.com/wastaken7/Upload-Assistant"
        return f"{meta.ua_name} {version} ({platform.system()} {platform.release()})"

    async def _debug_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _post_upload(self, meta: Meta, data: dict[str, Any], files: dict[str, Any]) -> str | None:
        try:
            response = await self._submit_request(data, files, {"User-Agent": self._user_agent(meta)})
            return await self._handle_upload_response(meta, data, files, response)
        except Exception as error:
            meta.tracker_status[self.tracker]["status_message"] = f"data error: {error}"
            return None

    async def _submit_request(self, data: dict[str, Any], files: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        url = self.upload_url + str(self.tracker_config.get("api_key", "")).strip()
        async with httpx.AsyncClient(timeout=60) as client:
            return await client.post(url=url, files=files, data=data, headers=headers)

    async def _handle_upload_response(self, meta: Meta, data: dict[str, Any], files: dict[str, Any], response: httpx.Response) -> str | None:
        payload = self._json_object(response)
        payload = await self._maybe_retry_invalid_imdb(meta, data, files, payload)
        self._log_invalid_name(payload, data.get("name"))
        return self._details_link_from_payload(meta, payload)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

    async def _maybe_retry_invalid_imdb(self, meta: Meta, data: dict[str, Any], files: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if int(payload.get("status_code", 1)) != 0:
            return payload
        message = str(payload.get("status_message", ""))
        logger.info(f"{self.tracker}: [red]{escape(message)}")
        if not message.startswith("Invalid imdb_id"):
            return payload
        logger.info(f"{self.tracker}: [yellow]RETRYING UPLOAD")
        data["imdb_id"] = 1
        response = await self._submit_request(data, files, {"User-Agent": self._user_agent(meta)})
        return self._json_object(response)

    def _log_invalid_name(self, payload: dict[str, Any], name: Any) -> None:
        message = str(payload.get("status_message", ""))
        if int(payload.get("status_code", 1)) == 0 and message.startswith("Invalid name value"):
            logger.info(f"{self.tracker}: [bold yellow]Submitted Name: {escape(str(name or ''))}")

    def _details_link_from_payload(self, meta: Meta, payload: dict[str, Any]) -> str | None:
        if "status_message" not in payload:
            meta.tracker_status[self.tracker]["status_message"] = "data error: No status_message in response."
            return None
        message = str(payload["status_message"])
        match = re.search(rf"{re.escape(self.base_url)}/torrent/download/.*\.(\d+)\.", message)
        if match is None:
            meta.tracker_status[self.tracker]["status_message"] = "No valid details link found in status_message."
            return ""
        torrent_id = match.group(1)
        meta.tracker_status[self.tracker]["torrent_id"] = torrent_id
        meta.tracker_status[self.tracker]["status_message"] = payload
        return f"{self.base_url}/details/{torrent_id}"

    async def _seed_uploaded_torrent(self, meta: Meta, details_link: str) -> bool:
        if details_link == "":
            return True
        try:
            await self.common.create_torrent_ready_to_seed(
                meta,
                self.tracker,
                self.source_flag,
                cast(str | list[str], self.tracker_config.get("announce_url")),
                details_link,
            )
            return True
        except Exception as error:
            logger.info(f"{self.tracker}: Error while editing the torrent file: {error}")
            return False

    async def get_cat_id(self, category_name: str) -> str:
        return {
            "MOVIE": "1",
            "TV": "2",
        }.get(category_name, "1")

    async def get_source(self, source: str) -> str | None:
        sources = {
            "Blu-ray": "Blu-ray",
            "BluRay": "Blu-ray",
            "HDDVD": "HD-DVD",
            "HD DVD": "HD-DVD",
            "WEB": "WEB",
            "Web": "WEB",
            "HDTV": "HDTV",
            "UHDTV": "HDTV",
            "NTSC": "DVD",
            "NTSC DVD": "DVD",
            "PAL": "DVD",
            "PAL DVD": "DVD",
        }

        return sources.get(source)

    async def get_type(self, meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return self._bdmv_type(meta)
        if meta.is_disc == "DVD":
            return self._dvd_type(meta)
        if meta.type == "REMUX":
            return self._remux_type(meta)
        acceptable = {"2160p", "1080p", "1080i", "720p", "576p", "576i", "540p", "480p", "Other"}
        return str(meta.resolution) if meta.resolution in acceptable else "Other"

    @classmethod
    def _bdmv_type(cls, meta: Meta) -> str:
        bdinfo = meta.bdinfo if isinstance(meta.bdinfo, dict) else {}
        bd_size = cls._bd_size(cls._safe_number(bdinfo.get("size"), 100))
        type_id = cls._bd_type_label("UHD" if meta.uhd == "UHD" else None, bd_size)
        return type_id if type_id in cls._allowed_bd_types() else "Other"

    @staticmethod
    def _bd_size(size: float) -> int:
        for candidate in (25, 50, 66, 100):
            if size < candidate:
                return candidate
        return 100

    @staticmethod
    def _bd_type_label(uhd: str | None, bd_size: int) -> str:
        if uhd == "UHD" and bd_size != 25:
            return f"UHD {bd_size}"
        return f"BD {bd_size}"

    @staticmethod
    def _allowed_bd_types() -> set[str]:
        return {"UHD 100", "UHD 66", "UHD 50", "BD 50", "BD 25"}

    @staticmethod
    def _safe_number(value: Any, default: float) -> float:
        try:
            return float(value)
        except TypeError, ValueError:
            return default

    @staticmethod
    def _dvd_type(meta: Meta) -> str:
        size = str(meta.dvd_size or "")
        if "DVD5" in size:
            return "DVD 5"
        if "DVD9" in size:
            return "DVD 9"
        return "Other"

    @staticmethod
    def _remux_type(meta: Meta) -> str:
        if meta.uhd == "UHD":
            return "UHD Remux"
        if meta.source == "BluRay":
            return "BD Remux"
        if meta.source in {"PAL DVD", "NTSC DVD"}:
            return "DVD Remux"
        return "Other"

    async def edit_desc(self, meta: Meta) -> None:
        description = await self._description_content(meta)
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(description)

    async def _description_content(self, meta: Meta) -> str:
        base = self._rehosted_base_description(meta, base_description(meta))
        parts = [*self._disc_description_blocks(meta), base.replace("[img]", "[img width=300]")]
        comparison = self._comparison_block(meta)
        if comparison:
            parts.append(comparison)
        tonemapped = self._tonemapped_header(meta)
        if tonemapped:
            parts.append(tonemapped)
        screenshot_block = self._screenshot_block(meta)
        if screenshot_block:
            parts.append(screenshot_block)
        parts.append(self._signature_block(meta))
        return "".join(parts)

    def _rehosted_base_description(self, meta: Meta, base: str) -> str:
        result = base
        collections: tuple[ImageCollection, ...] = ("menu_images", "spectrograms_images", "dynamic_hdr_plot_images")
        for collection_name in collections:
            result = self._replace_rehosted_collection(meta, result, collection_name)
        return result

    def _replace_rehosted_collection(self, meta: Meta, text: str, collection_name: ImageCollection) -> str:
        original = getattr(meta, collection_name, [])
        rehosted = get_tracker_image_collection(meta, self.tracker, collection_name)
        if not isinstance(original, list) or not isinstance(rehosted, list):
            return text
        result = text
        for source, target in zip(original, rehosted, strict=False):
            result = self._replace_rehosted_url(result, source, target)
        return result

    @classmethod
    def _replace_rehosted_url(cls, text: str, source: Any, target: Any) -> str:
        source_url = cls._raw_url(source)
        target_url = cls._raw_url(target)
        if not cls._should_replace_url(source_url, target_url):
            return text
        return text.replace(source_url, target_url)

    @staticmethod
    def _raw_url(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        raw = value.get("raw_url")
        return raw if isinstance(raw, str) else ""

    @staticmethod
    def _should_replace_url(source_url: str, target_url: str) -> bool:
        return bool(source_url and target_url and source_url != target_url)

    @classmethod
    def _disc_description_blocks(cls, meta: Meta) -> list[str]:
        discs = cls._disc_entries(meta)
        if not discs:
            return []
        first = cls._first_disc_block(discs[0])
        additional = [cls._additional_disc_block(disc) for disc in discs[1:]]
        return [part for part in [first, *additional] if part]

    @staticmethod
    def _first_disc_block(disc: dict[str, Any]) -> str:
        if disc.get("type") != "DVD":
            return ""
        return f"[spoiler=VOB MediaInfo][code]{disc.get('vob_mi', '')}[/code][/spoiler]\n"

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], meta.discs) if isinstance(meta.discs, list) else []

    @staticmethod
    def _additional_disc_block(disc: dict[str, Any]) -> str:
        disc_type = disc.get("type")
        if disc_type == "BDMV":
            return f"[spoiler={disc.get('name', 'BDINFO')}][code]{disc.get('summary', '')}[/code][/spoiler]\n"
        if disc_type == "DVD":
            return (
                f"{disc.get('name', '')}:\n"
                f"[spoiler={Path(str(disc.get('vob', ''))).name}][code]{disc.get('vob_mi', '')}[/code][/spoiler] "
                f"[spoiler={Path(str(disc.get('ifo', ''))).name}][code]{disc.get('ifo_mi', '')}[/code][/spoiler]\n"
            )
        if disc_type == "HDDVD":
            return f"{disc.get('name', '')}:\n[spoiler={Path(str(disc.get('largest_evo', ''))).name}][code]{disc.get('evo_mi', '')}[/code][/spoiler]\n"
        return ""

    @classmethod
    def _comparison_block(cls, meta: Meta) -> str:
        groups = cls._comparison_groups(meta)
        if not groups:
            return ""
        indices = cls._comparison_indices(groups)
        names = cls._comparison_names(groups, indices)
        urls = cls._comparison_urls(groups, indices)
        return f"[center][comparison={', '.join(names)}]\n{''.join(f'{url}\n' for url in urls)}[/comparison][/center]\n\n"

    @staticmethod
    def _comparison_groups(meta: Meta) -> dict[str, Any]:
        if not meta.comparison or not isinstance(meta.comparison_groups, dict):
            return {}
        return cast(dict[str, Any], meta.comparison_groups)

    @staticmethod
    def _comparison_indices(groups: dict[str, Any]) -> list[str]:
        return sorted(groups, key=lambda value: int(str(value)))

    @staticmethod
    def _comparison_names(groups: dict[str, Any], indices: list[str]) -> list[str]:
        return [str(cast(dict[str, Any], groups[index]).get("name", f"Group {index}")) for index in indices]

    @classmethod
    def _comparison_urls(cls, groups: dict[str, Any], indices: list[str]) -> list[str]:
        image_count = cls._comparison_image_count(groups, indices)
        urls: list[str] = []
        for image_index in range(image_count):
            urls.extend(cls._comparison_row_urls(groups, indices, image_index))
        return urls

    @classmethod
    def _comparison_image_count(cls, groups: dict[str, Any], indices: list[str]) -> int:
        counts = [len(cls._group_urls(groups.get(index))) for index in indices]
        return min(counts) if counts else 0

    @classmethod
    def _comparison_row_urls(cls, groups: dict[str, Any], indices: list[str], image_index: int) -> list[str]:
        return [url for index in indices if (url := cls._group_raw_url(groups.get(index), image_index))]

    @staticmethod
    def _group_urls(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, dict):
            return []
        urls = value.get("urls", [])
        if not isinstance(urls, list):
            return []
        return [cast(dict[str, Any], item) for item in urls if isinstance(item, dict)]

    @classmethod
    def _group_raw_url(cls, value: Any, image_index: int) -> str:
        urls = cls._group_urls(value)
        if image_index >= len(urls):
            return ""
        return str(urls[image_index].get("raw_url") or "")

    def _tonemapped_header(self, meta: Meta) -> str:
        if not meta.tonemapped:
            return ""
        default_config = self.config.get("DEFAULT", {})
        if not isinstance(default_config, dict):
            return ""
        value = default_config.get("tonemapped_header")
        return f"{value}\n\n" if value else ""

    def _screenshot_block(self, meta: Meta) -> str:
        links = self._screenshot_links(meta)
        if not links:
            return ""
        return f"[align=center]{self._paired_screenshot_rows(links)}[/align]"

    def _screenshot_links(self, meta: Meta) -> list[str]:
        images = get_tracker_image_collection(meta, self.tracker, "screenshots")
        if not isinstance(images, list):
            return []
        selected = images[: self._screen_limit(meta.screens)]
        return [link for image in selected if (link := self._screenshot_link(image))]

    @staticmethod
    def _paired_screenshot_rows(links: list[str]) -> str:
        rows = [" ".join(links[index : index + 2]) for index in range(0, len(links), 2)]
        return "\n\n".join(rows)

    @staticmethod
    def _screen_limit(value: Any) -> int:
        try:
            return max(0, int(value))
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _screenshot_link(image: Any) -> str:
        if not isinstance(image, dict):
            return ""
        web_url = image.get("web_url")
        img_url = image.get("img_url")
        if not web_url or not img_url:
            return ""
        return f"[url={web_url}][img width=350]{img_url}[/img][/url]"

    @staticmethod
    def _signature_block(meta: Meta) -> str:
        return f"\n[align=right][url=https://github.com/wastaken7/Upload-Assistant][size=10]{meta.ua_signature}[/size][/url][/align]"

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self._internal_release_policy(meta):
            return False
        if not self._mediainfo_policy(meta):
            return False
        if not self._container_policy(meta):
            return False
        if not await self._group_policy(meta):
            return False
        return await self.common.check_and_confirm_adult_media_upload(meta, self.tracker)

    async def _internal_release_policy(self, meta: Meta) -> bool:
        name = (await self.get_name(meta)).lower()
        if not self._contains_internal_marker(name):
            return True
        logger.info(f"{self.tracker}: [bold red]This is an internal {self.tracker} release, skipping upload[/bold red]")
        return self._optional_policy_override(meta)

    @staticmethod
    def _contains_internal_marker(name: str) -> bool:
        markers = (
            "-framestor",
            "-bhdstudio",
            "-bmf",
            "-decibel",
            "-d-zone",
            "-hifi",
            "-ncmt",
            "-tdd",
            "-flux",
            "-crfw",
            "-sonny",
            "-zr-",
            "-mkvultra",
            "-rpg",
            "-w4nk3r",
            "-irobot",
            "-beyondhd",
        )
        return any(marker in name for marker in markers)

    @staticmethod
    def _optional_policy_override(meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        return bool(cli_ui.ask_yes_no("Do you want to upload anyway?", default=False))

    def _mediainfo_policy(self, meta: Meta) -> bool:
        if meta.valid_mi_settings:
            return True
        logger.info(f"{self.tracker}: [bold red]No encoding settings in mediainfo, skipping {self.tracker} upload.[/bold red]")
        return False

    def _container_policy(self, meta: Meta) -> bool:
        if meta.type not in {"REMUX", "ENCODE", "WEBDL", "WEBRIP"} or meta.container in {"mkv", "mp4"}:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Container '{escape(str(meta.container))}' is not allowed for {escape(str(meta.type))}. Only MKV and MP4 are permitted. Skipping upload.[/bold red]"
        )
        return False

    async def _group_policy(self, meta: Meta) -> bool:
        if meta.type == "WEBDL" or not meta.tag or "EVO" not in meta.tag:
            return True
        logger.info(f"{self.tracker}: [bold red]Group {escape(str(meta.tag))} is only allowed for raw type content at {self.tracker}[/bold red]")
        return self._optional_policy_override(meta)

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        params, rss_key = await self._search_params(meta)
        url = f"{self.base_url}/api/torrents/{str(self.tracker_config.get('api_key', '')).strip()}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, params=params)
            response.raise_for_status()
        payload = self._search_payload(response)
        return [self._search_result(item, rss_key) for item in self._search_items(payload)]

    async def _search_params(self, meta: Meta) -> tuple[dict[str, Any], bool]:
        category = "Movies" if meta.category == "MOVIE" else "TV"
        type_id = None if meta.is_disc == "DVD" else await self.get_type(meta)
        params: dict[str, Any] = {"action": "search", "types": type_id, "categories": category}
        self._apply_search_identity(params, meta)
        self._apply_search_scope(params, meta)
        rss_key = bool(self.tracker_config.get("bhd_rss_key"))
        if rss_key:
            params["rsskey"] = str(self.tracker_config.get("bhd_rss_key", "")).strip()
        return params, rss_key

    @staticmethod
    def _apply_search_identity(params: dict[str, Any], meta: Meta) -> None:
        if meta.tmdb:
            kind = "movie" if meta.category == "MOVIE" else "tv"
            params["tmdb_id"] = f"{kind}/{meta.tmdb}"

    @staticmethod
    def _apply_search_scope(params: dict[str, Any], meta: Meta) -> None:
        if meta.sd == 1:
            params["categories"] = None
            params["types"] = None
        if meta.category == "TV":
            params["search"] = str(meta.season)

    def _search_payload(self, response: httpx.Response) -> dict[str, Any]:
        payload = self._json_object(response)
        if payload.get("status_code") != 1:
            raise RuntimeError(f"BEYONDHD API Error: {payload.get('message', 'Unknown Error')}")
        return payload

    @staticmethod
    def _search_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        value = payload.get("results", [])
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]

    @classmethod
    def _search_result(cls, item: dict[str, Any], rss_key: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": item.get("name", ""),
            "link": item.get("url", ""),
            "size": item.get("size", 0),
            "flags": cls._search_flags(item),
        }
        if rss_key:
            result["download"] = item.get("download_url")
        return result

    @staticmethod
    def _search_flags(item: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        if item.get("dv") == 1:
            flags.append("DV")
        if item.get("hdr10") == 1 or item.get("hdr10+") == 1:
            flags.append("HDR")
        return flags

    def _is_true(self, value: Any) -> bool:
        """
        Converts a value to a boolean. Returns True for "true", "1", "yes" (case-insensitive), and False otherwise.
        """
        return str(value).strip().lower() in {"true", "1", "yes"}

    async def get_live(self, meta: Meta) -> int:
        draft_value = self.config["TRACKERS"][self.tracker].get("draft_default", False)
        draft_bool = draft_value if isinstance(draft_value, bool) else self._is_true(str(draft_value).strip())

        return 0 if draft_bool or meta.draft else 1

    async def get_edition(self, meta: Meta, tags: list[str]) -> tuple[bool, str]:
        edition = str(meta.edition or "")
        if "Hybrid" in tags:
            edition = edition.replace("Hybrid", "").strip()
        if not edition:
            return False, ""
        known = self._known_edition(edition)
        return (False, known) if known else (True, edition)

    @staticmethod
    def _known_edition(edition: str) -> str:
        normalized = edition.lower()
        editions = ("collector", "director", "cirector", "extended", "limited", "special", "theatrical", "uncut", "unrated")
        return next((value for value in editions if value in normalized), "")

    async def get_tags(self, meta: Meta) -> list[str]:
        return [tag for tag, matched in self._tag_rules(meta) if matched]

    @staticmethod
    def _tag_rules(meta: Meta) -> tuple[tuple[str, bool], ...]:
        hdr = str(meta.hdr or "")
        edition = str(meta.edition or "")
        audio = str(meta.audio or "")
        return (
            ("WEBRip", meta.type == "WEBRIP"),
            ("WEBDL", meta.type == "WEBDL"),
            ("3D", meta.three_d == "3D"),
            ("DualAudio", "Dual-Audio" in audio),
            ("EnglishDub", "Dubbed" in audio),
            ("OpenMatte", "Open Matte" in edition),
            ("Scene", bool(meta.scene)),
            ("Personal", bool(meta.personalrelease)),
            ("Hybrid", "hybrid" in edition.lower()),
            ("Commentary", bool(meta.has_commentary)),
            ("DV", "DV" in hdr),
            ("HDR10+", "HDR10+" in hdr),
            ("HDR10", "HDR" in hdr and "HDR10+" not in hdr),
            ("HLG", "HLG" in hdr),
        )

    async def get_name(self, meta: Meta) -> str:
        name = meta.name or ""
        if meta.source in ("PAL DVD", "NTSC DVD", "DVD", "NTSC", "PAL"):
            audio = meta.audio
            audio = " ".join(audio.split())
            name = name.replace(audio, f"{meta.video_codec} {audio}")
        return name.replace("DD+", "DDP")
