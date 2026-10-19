# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import contextlib
import html
import json
import re
from typing import Any, cast

import httpx

from src.domain_models.book_language import (
    is_valid_book_language,
    resolve_book_language,
)
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.media.book_extractors import validate_isbn_checksum
from src.integrations.observability.runtime_support import logger

mam_color = "[#eac117]MyAnonamouse[/#eac117]"

_PUBLISHER_FIELDS = (
    "publisher",
    "publisher_info",
    "publisher_name",
    "publishers",
    "pubname",
)
_PUBLICATION_FIELDS = (
    "year",
    "release_year",
    "publication_year",
    "published",
    "publish_date",
    "publication_date",
    "released",
)
_CATEGORY_MARKERS = ("comic", "manga", "magazine", "newspaper")


def _mam_isbn_candidates(cleaned: str) -> tuple[str, ...]:
    if len(cleaned) == 9 and cleaned.isdigit():
        return (f"0{cleaned}", cleaned)
    return (cleaned,)


def _normalize_mam_isbn(value: Any) -> str | None:
    cleaned = re.sub(r"[-\s]", "", str(value or "")).upper()
    for candidate in _mam_isbn_candidates(cleaned):
        isbn = validate_isbn_checksum(candidate)
        if isbn:
            return isbn
    return None


def _clean_mam_title(value: Any, author: str = "") -> str:
    title = html.unescape(str(value or "")).strip()
    title = re.sub(
        r"\.(?:epub|pdf|mobi|azw3?|fb2|cb[rz])$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    title = re.sub(
        r"\s*\((?:epub|pdf|mobi|azw3?|fb2|cb[rz])\)\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\s+-\s+(?:97[89]\d{10}|\d{9}[\dX])\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    if author:
        title = re.sub(
            rf"^{re.escape(author)}\s+-\s+", "", title, flags=re.IGNORECASE
        )
    return title.strip()


def _metadata_string_values(value: str) -> list[str]:
    text = value.strip()
    if text[:1] in "[{":
        with contextlib.suppress(json.JSONDecodeError):
            return _metadata_values(json.loads(text))
    text = html.unescape(text).strip()
    return [text] if text else []


def _preferred_metadata_value(value: dict[Any, Any]) -> Any:
    for key in ("name", "publisher", "title", "value"):
        if value.get(key):
            return value[key]
    return None


def _metadata_mapping_values(value: dict[Any, Any]) -> list[str]:
    preferred = _preferred_metadata_value(value)
    if preferred is not None:
        return _metadata_values(preferred)
    return _metadata_values(list(value.values()))


def _metadata_iterable_values(
    value: list[Any] | tuple[Any, ...] | set[Any],
) -> list[str]:
    values: list[str] = []
    for entry in value:
        values.extend(_metadata_values(entry))
    return values


def _metadata_scalar_values(value: Any) -> list[str]:
    text = html.unescape(str(value)).strip()
    if not text:
        return []
    return [text]


def _metadata_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return _metadata_string_values(value)
    if isinstance(value, dict):
        return _metadata_mapping_values(cast(dict[Any, Any], value))
    if isinstance(value, (list, tuple, set)):
        return _metadata_iterable_values(value)
    if value is None:
        return []
    return _metadata_scalar_values(value)


class MyAnonamouseManager:
    @staticmethod
    def _decoded_people(value: Any) -> dict[Any, Any]:
        if isinstance(value, dict):
            return cast(dict[Any, Any], value)
        if not isinstance(value, str):
            return {}
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return cast(dict[Any, Any], decoded)
        return {}

    @classmethod
    def _people_names(cls, value: Any) -> list[str]:
        people = cls._decoded_people(value)
        return [html.unescape(str(name)).strip() for name in people.values()]

    @classmethod
    def _add_people_field(
        cls,
        metadata: dict[str, Any],
        item: dict[str, Any],
        source_key: str,
        target_key: str,
        warning_label: str,
    ) -> None:
        value = item.get(source_key)
        if not value:
            return
        try:
            names = cls._people_names(value)
        except Exception as error:
            logger.debug(
                f"{mam_color}: [yellow]Warning: Could not parse MAM {warning_label}: {error}[/yellow]"
            )
            return
        if names:
            metadata[target_key] = ", ".join(names)

    @classmethod
    def _add_people_metadata(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        cls._add_people_field(
            metadata, item, "author_info", "author", "authors"
        )
        cls._add_people_field(
            metadata, item, "narrator_info", "narrator", "narrators"
        )

    @staticmethod
    def _add_description(
        metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        description = item.get("description")
        if description:
            metadata["overview"] = html.unescape(str(description)).strip()

    @staticmethod
    def _first_item_value(
        item: dict[str, Any], fields: tuple[str, ...]
    ) -> Any:
        for field in fields:
            if item.get(field):
                return item[field]
        return None

    @classmethod
    def _add_publisher(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        publisher_value = cls._first_item_value(item, _PUBLISHER_FIELDS)
        publishers = list(
            dict.fromkeys(
                name for name in _metadata_values(publisher_value) if name
            )
        )
        if publishers:
            metadata["publisher"] = ", ".join(publishers)

    @staticmethod
    def _add_title(metadata: dict[str, Any], item: dict[str, Any]) -> None:
        title = item.get("title") or item.get("name")
        if title:
            metadata["title"] = _clean_mam_title(
                title, str(metadata.get("author", ""))
            )

    @staticmethod
    def _add_isbn(metadata: dict[str, Any], item: dict[str, Any]) -> None:
        isbn = _normalize_mam_isbn(item.get("isbn"))
        if isbn:
            metadata["isbn"] = isbn

    @staticmethod
    def _asin_value(value: Any) -> str | None:
        if not value:
            return None
        asin_match = re.search(
            r"\bASIN\s*[:#]?\s*([A-Z0-9]{10})(?![A-Z0-9])",
            str(value),
            re.IGNORECASE,
        )
        cleaned = (
            (asin_match.group(1) if asin_match else str(value)).strip().upper()
        )
        if re.fullmatch(r"[A-Z0-9]{10}", cleaned):
            return cleaned
        return None

    @classmethod
    def _add_asin(cls, metadata: dict[str, Any], item: dict[str, Any]) -> None:
        asin = cls._asin_value(item.get("asin") or item.get("ASIN"))
        if asin:
            metadata["asin"] = asin

    @classmethod
    def _add_publication_year(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        value = cls._first_item_value(item, _PUBLICATION_FIELDS)
        match = re.search(r"\b(?:18|19|20)\d{2}\b", str(value or ""))
        if match:
            metadata["year"] = int(match.group(0))

    @staticmethod
    def _resolved_language(lang: Any) -> tuple[str, str] | None:
        try:
            full, iso3 = resolve_book_language(str(lang))
        except Exception as error:
            logger.debug(
                f"[yellow]Warning: Could not resolve language '{lang}': {error}[/yellow]"
            )
            return None
        if not is_valid_book_language(full, iso3):
            return None
        return full, iso3

    @classmethod
    def _add_language(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        lang = item.get("lang_code")
        if not lang:
            return
        resolved = cls._resolved_language(lang)
        if resolved is None:
            return
        full, iso3 = resolved
        metadata["book_language"] = full
        if iso3:
            metadata["book_language_iso"] = iso3

    @staticmethod
    def _cover_extension(poster_type: Any) -> str:
        lowered = str(poster_type).lower()
        if "png" in lowered:
            return "png"
        if "gif" in lowered:
            return "gif"
        return "jpeg"

    @classmethod
    def _add_cover(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        mam_id = item.get("id")
        poster_type = item.get("poster_type")
        if not mam_id or not poster_type:
            return
        extension = cls._cover_extension(poster_type)
        metadata["artwork_url"] = (
            f"https://cdn.myanonamouse.net/t/p/large/{mam_id}.{extension}"
        )

    @staticmethod
    def _category_text(item: dict[str, Any]) -> str:
        return " ".join(
            str(item.get(field) or "").lower()
            for field in ("catname", "tags", "categories")
        )

    @classmethod
    def _add_category_flags(
        cls, metadata: dict[str, Any], item: dict[str, Any]
    ) -> None:
        category_text = cls._category_text(item)
        for marker in _CATEGORY_MARKERS:
            if marker in category_text:
                metadata[marker] = True

    def _parse_torrent_info(self, item: dict[str, Any]) -> dict[str, Any]:
        logger.debug(f"{mam_color} raw item: {item}")
        metadata: dict[str, Any] = {}
        self._add_people_metadata(metadata, item)
        self._add_description(metadata, item)
        self._add_publisher(metadata, item)
        self._add_title(metadata, item)
        self._add_isbn(metadata, item)
        self._add_asin(metadata, item)
        self._add_publication_year(metadata, item)
        self._add_language(metadata, item)
        self._add_cover(metadata, item)
        self._add_category_flags(metadata, item)
        return metadata

    @staticmethod
    def _clean_torrent_id(torrent_id: str) -> str | None:
        clean_id = torrent_id.strip()
        if clean_id and clean_id.isdigit():
            return clean_id
        return None

    @staticmethod
    async def _cached_result(
        cache: Any, clean_id: str
    ) -> tuple[bool, dict[str, Any] | None]:
        cached_data = await cache.get("myanonamouse", "torrent", clean_id)
        if is_cache_miss(cached_data) or not isinstance(cached_data, dict):
            return False, None
        cached = cast(dict[str, Any], cached_data)
        if cached.get("not_found"):
            return True, None
        logger.info(f"{mam_color}: ID match found (cached): {clean_id}")
        return True, cached

    @staticmethod
    def _request_headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _request_payload(clean_id: str) -> dict[str, Any]:
        return {"tor": {"id": int(clean_id)}, "description": "", "isbn": ""}

    @staticmethod
    async def _cache_not_found(cache: Any, clean_id: str) -> None:
        await cache.set(
            "myanonamouse",
            "torrent",
            clean_id,
            {"not_found": True},
            negative=True,
        )

    @staticmethod
    def _first_result_item(data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        data_map = cast(dict[str, Any], data)
        entries_value = data_map.get("data")
        if not isinstance(entries_value, list):
            return None
        entries = cast(list[Any], entries_value)
        if not entries:
            return None
        first = entries[0]
        if not isinstance(first, dict):
            return None
        return cast(dict[str, Any], first)

    async def _successful_result(
        self, data: Any, cache: Any, clean_id: str
    ) -> dict[str, Any] | None:
        first = self._first_result_item(data)
        if first is None:
            logger.info(
                f"{mam_color}: [yellow]No items found for ID: {clean_id}[/yellow]"
            )
            await self._cache_not_found(cache, clean_id)
            return None
        metadata = self._parse_torrent_info(first)
        if not metadata:
            return None
        logger.info(f"{mam_color}: match found: {metadata.get('title')}")
        await cache.set("myanonamouse", "torrent", clean_id, metadata)
        return metadata

    async def _response_result(
        self, response: httpx.Response, cache: Any, clean_id: str
    ) -> dict[str, Any] | None:
        if response.status_code == 200:
            return await self._successful_result(
                response.json(), cache, clean_id
            )
        if response.status_code in (401, 403):
            logger.info(
                f"{mam_color}: [bold red]API: Unauthorized/Forbidden (Status {response.status_code}). Check your mam_api_key/mam_id and IP locked session cookie setting on the website.[/bold red]"
            )
            return None
        logger.info(
            f"{mam_color}: [red]API returned error status code {response.status_code} for ID: {clean_id}[/red]"
        )
        return None

    async def _fetch_result(
        self, clean_id: str, api_key: str, cache: Any
    ) -> dict[str, Any] | None:
        url = "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.post(
                    url,
                    json=self._request_payload(clean_id),
                    headers=self._request_headers(),
                    cookies={"mam_id": api_key},
                    timeout=15.0,
                )
            return await self._response_result(response, cache, clean_id)
        except Exception as error:
            logger.info(
                f"{mam_color}: [red]API: Network or query error for ID {clean_id}: {error}[/red]"
            )
            return None

    async def search_by_id(
        self, torrent_id: str, base_dir: str = "", api_key: str = ""
    ) -> dict[str, Any] | None:
        """Search MyAnonamouse API by torrent ID."""
        clean_id = self._clean_torrent_id(torrent_id)
        if clean_id is None:
            return None
        cache = cache_for(base_dir)
        cached, cached_data = await self._cached_result(cache, clean_id)
        if cached:
            return cached_data
        if not api_key:
            logger.debug(
                f"{mam_color}: [yellow]API key/session cookie not configured, skipping search[/yellow]"
            )
            return None
        logger.debug(f"{mam_color}: Searching API for ID: {clean_id}")
        return await self._fetch_result(clean_id, api_key, cache)


myanonamouse_manager = MyAnonamouseManager()
