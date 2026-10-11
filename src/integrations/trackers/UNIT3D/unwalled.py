import re
from pathlib import Path
from typing import Any, cast

import httpx

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.UNIT3D import UNIT3D
from src.integrations.trackers.UNIT3D.unwalled_validation import (
    UnwalledValidationMixin,
)

type OptionCatalog = dict[str, dict[str, str]]
PODCAST_TITLE_PATTERN = re.compile(
    r"^.+\s\[(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?/[A-Z0-9][A-Z0-9.+-]*(?: - (\d+)kbps)?\]$"
)


class Unwalled(UnwalledValidationMixin, UNIT3D):
    tracker = "UNWALLED"
    display_name = "Unwalled"
    base_url = "https://unwalled.cc"
    source_flag = "Unwalled"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("PODCAST",)
    tracker_urls = ("https://unwalled.cc",)
    download_url_hosts = ("unwalled.cc",)
    max_torrent_download_size = 1024 * 1024
    max_json_response_size = 2 * 1024 * 1024
    follow_upload_redirects = False
    follow_search_redirects = False
    expose_remote_error_details = False
    banned_groups: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name=self.tracker)
        self.option_catalog: OptionCatalog = {"categories": {}, "types": {}}
        self.option_discovery_complete = False

    @staticmethod
    def _normalize_option(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _catalog_entries(payload: dict[str, Any]) -> list[object]:
        raw_entries = payload.get("data", [])
        if not isinstance(raw_entries, list):
            return []
        return cast(list[object], raw_entries)

    @staticmethod
    def _catalog_attributes(raw_entry: object) -> dict[str, object] | None:
        if not isinstance(raw_entry, dict):
            return None
        entry = cast(dict[str, object], raw_entry)
        raw_attributes = entry.get("attributes", {})
        if not isinstance(raw_attributes, dict):
            return None
        return cast(dict[str, object], raw_attributes)

    @classmethod
    def _catalog_option(
        cls,
        attributes: dict[str, object],
        name_key: str,
        id_key: str,
    ) -> tuple[str, str] | None:
        name = attributes.get(name_key)
        option_id = attributes.get(id_key)
        if not isinstance(name, str) or not name.strip():
            return None
        option_id_text = str(option_id or "")
        if not option_id_text.isdigit():
            return None
        return cls._normalize_option(name), option_id_text

    @classmethod
    def _add_catalog_options(
        cls, catalog: OptionCatalog, attributes: dict[str, object]
    ) -> None:
        for plural, name_key, id_key in (
            ("categories", "category", "category_id"),
            ("types", "type", "type_id"),
        ):
            option = cls._catalog_option(attributes, name_key, id_key)
            if option is not None:
                name, option_id = option
                catalog[plural][name] = option_id

    @classmethod
    def catalog_from_response(cls, payload: dict[str, Any]) -> OptionCatalog:
        catalog: OptionCatalog = {"categories": {}, "types": {}}
        for raw_entry in cls._catalog_entries(payload):
            attributes = cls._catalog_attributes(raw_entry)
            if attributes is not None:
                cls._add_catalog_options(catalog, attributes)
        return catalog

    @staticmethod
    def _discovery_headers(api_key: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {api_key}",
            "accept": "application/json",
        }

    async def _discovery_page(
        self,
        client: httpx.AsyncClient,
        page: int,
        headers: dict[str, str],
        max_size: int,
    ) -> dict[str, Any] | None:
        async with client.stream(
            "GET",
            self.search_url,
            headers=headers,
            params={"name": "", "perPage": "100", "page": str(page)},
        ) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("Unwalled option discovery redirect rejected")
            response.raise_for_status()
            bounded_response = await self._bounded_response(response, max_size)
        raw_payload = bounded_response.json()
        if not isinstance(raw_payload, dict):
            return None
        return cast(dict[str, Any], raw_payload)

    def _merge_discovered_options(self, payload: dict[str, Any]) -> None:
        discovered = self.catalog_from_response(payload)
        self.option_catalog["categories"].update(discovered["categories"])
        self.option_catalog["types"].update(discovered["types"])

    def _finish_discovery_page(self, payload: dict[str, Any]) -> bool:
        entries = payload.get("data")
        if not isinstance(entries, list):
            return True
        if len(entries) >= 100:
            return False
        self.option_discovery_complete = True
        return True

    async def _discover_pages(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        max_size: int,
    ) -> None:
        for page in range(1, 101):
            payload = await self._discovery_page(
                client, page, headers, max_size
            )
            if payload is None:
                return
            self._merge_discovered_options(payload)
            if self._finish_discovery_page(payload):
                return

    async def discover_options(self) -> OptionCatalog:
        if self.option_discovery_complete:
            return self.option_catalog
        headers = self._discovery_headers(self.api_key)
        max_size = self.max_json_response_size or 2 * 1024 * 1024
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=self.follow_search_redirects
            ) as client:
                await self._discover_pages(client, headers, max_size)
        except (httpx.HTTPError, ValueError) as error:
            logger.info(
                f"{self.tracker}: [yellow]Unable to discover category/type IDs: {error}[/yellow]"
            )
        return self.option_catalog

    async def _resolve_option(self, value: str, plural: str) -> str:
        requested = value.strip()
        numeric = self._positive_numeric_option(requested)
        if numeric:
            return numeric
        singular = self._option_singular(plural)
        if not requested:
            raise ValueError(
                f"Set --unwalled-{singular} or TRACKERS.UNWALLED.{singular}"
            )
        catalog = await self.discover_options()
        option_id = catalog[plural].get(self._normalize_option(requested))
        if option_id:
            return option_id
        available = ", ".join(sorted(catalog[plural])) or "none discovered"
        raise ValueError(
            f"Unknown Unwalled {singular} {requested!r}; available: {available}. A numeric ID is also accepted"
        )

    @staticmethod
    def _positive_numeric_option(value: str) -> str:
        return value if value.isdigit() and int(value) > 0 else ""

    @staticmethod
    def _option_singular(plural: str) -> str:
        return {"categories": "category", "types": "type"}.get(
            plural, plural.removesuffix("s")
        )

    async def _option_mapping(
        self, plural: str, mapping_only: bool, reverse: bool
    ) -> dict[str, str] | None:
        if not mapping_only and not reverse:
            return None
        catalog = await self.discover_options()
        mapping = catalog[plural]
        if mapping_only:
            return mapping
        return {option_id: name for name, option_id in mapping.items()}

    @staticmethod
    def _requested_option(
        explicit: str, meta_value: str, configured_value: object
    ) -> str:
        return explicit or meta_value or str(configured_value or "")

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = await self._option_mapping(
            "categories", mapping_only, reverse
        )
        if mapping is not None:
            return mapping
        requested = self._requested_option(
            category,
            meta.unwalled_category,
            self.tracker_config.get("category", ""),
        )
        return {
            "category_id": await self._resolve_option(requested, "categories")
        }

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = await self._option_mapping("types", mapping_only, reverse)
        if mapping is not None:
            return mapping
        requested = self._requested_option(
            type,
            meta.unwalled_type,
            self.tracker_config.get("type", ""),
        )
        return {"type_id": await self._resolve_option(requested, "types")}

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        if (
            meta.category == "PODCAST"
            and not mapping_only
            and not reverse
            and not resolution
        ):
            return {}
        return await super().get_resolution_id(
            meta, resolution, reverse, mapping_only
        )

    def get_search_name(self, meta: Meta) -> str:
        return re.sub(r"\s+", " ", meta.podcast_title or meta.name).strip()

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = re.sub(r"\s+", " ", meta.podcast_title or meta.name).strip()
        return {"name": name}

    @staticmethod
    def _valid_podcast_title(title: str, audio: bool) -> bool:
        match = PODCAST_TITLE_PATTERN.fullmatch(title.strip())
        return match is not None and (not audio or match.group(1) is not None)

    def _podcast_identity_checks(self, meta: Meta) -> bool:
        if meta.category != "PODCAST":
            return False
        title = str(meta.podcast_title or meta.name).strip()
        if not title:
            logger.info(
                f"{self.tracker}: [bold red]A podcast torrent title is required.[/bold red]"
            )
            return False
        if self._valid_podcast_title(title, meta.type == "AUDIO"):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Podcast title must include year/date, format, and audio bitrate when applicable.[/bold red]"
        )
        return False

    def _podcast_announce_check(self, meta: Meta) -> bool:
        if meta.debug or self._valid_announce_url(self.announce_url):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Configure a valid personal Unwalled announce URL.[/bold red]"
        )
        return False

    def _podcast_metadata_checks(self, meta: Meta) -> bool:
        if not self._podcast_identity_checks(meta):
            return False
        return self._podcast_announce_check(meta)

    def _podcast_file_checks(self, meta: Meta) -> bool:
        if not meta.filelist or not self._valid_torrent_paths(meta):
            logger.info(
                f"{self.tracker}: [bold red]The torrent contains a filename rejected by Unwalled.[/bold red]"
            )
            return False
        return self._valid_artwork(meta)

    async def _option_checks(self, meta: Meta) -> bool:
        try:
            await self.get_category_id(meta)
            await self.get_type_id(meta)
        except ValueError as error:
            logger.info(f"{self.tracker}: [bold red]{error}[/bold red]")
            return False
        return True

    @staticmethod
    def _artwork_size(meta: Meta) -> int:
        paths = (
            Path(meta.artwork_path),
            Path(str(meta.artwork_banner_path or "")),
        )
        return sum(path.stat().st_size for path in paths if path.is_file())

    def _artwork_budget_check(self, meta: Meta) -> bool:
        if self._artwork_size(meta) < 1024 * 1024:
            return True
        logger.info(
            f"{self.tracker}: [bold red]Cover and banner leave no room for a torrent under the 1 MiB limit.[/bold red]"
        )
        return False

    def _base_torrent_check(self, meta: Meta) -> bool:
        base_torrent = Path(meta.base_dir) / "tmp" / meta.uuid / "BASE.torrent"
        if not base_torrent.is_file() or self._torrent_is_v1(base_torrent):
            return True
        logger.info(
            f"{self.tracker}: [bold red]Unwalled requires a V1 torrent.[/bold red]"
        )
        return False

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._podcast_metadata_checks(meta):
            return False
        if not self._podcast_file_checks(meta):
            return False
        if not await self._option_checks(meta):
            return False
        if not self._artwork_budget_check(meta):
            return False
        return self._base_torrent_check(meta)

    async def get_upload_torrent_filename(self, meta: Meta) -> str:
        announce_url = (
            "https://fake.tracker" if meta.debug else self.announce_url
        )
        if not meta.debug and not self._valid_announce_url(announce_url):
            raise ValueError(
                "A valid personal Unwalled announce URL is required"
            )
        await self.common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag, announce_url=announce_url
        )
        torrent_filename = f"[{self.tracker}]"
        torrent_path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"{torrent_filename}.torrent"
        )
        if not self._valid_upload_bundle(meta, torrent_path):
            raise ValueError("Unwalled upload bundle validation failed")
        return torrent_filename
