# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

import httpx

from src.domain_models.book_language import (
    is_valid_book_language,
    resolve_book_language,
)
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.observability.runtime_support import logger

google_color_str = "[#4285f4]G[/#4285f4][#ea4335]o[/#ea4335][#fbbc05]o[/#fbbc05][#4285f4]g[/#4285f4][#34a853]l[/#34a853][#ea4335]e[/#ea4335] [#4285f4]Books[/#4285f4]"

Metadata = dict[str, Any]


class GoogleBooksManager:
    @staticmethod
    def _canonical_isbn(value: str) -> str:
        clean = re.sub(r"[^0-9X]", "", value.upper())
        if len(clean) != 10:
            return clean
        base = f"978{clean[:9]}"
        check = (
            10
            - sum(
                int(digit) * (1 if index % 2 == 0 else 3)
                for index, digit in enumerate(base)
            )
            % 10
        ) % 10
        return f"{base}{check}"

    @classmethod
    def _identifier_values(cls, item: dict[str, Any]) -> set[str]:
        identifiers = item.get("volumeInfo", {}).get("industryIdentifiers", [])
        return {
            cls._canonical_isbn(str(identifier.get("identifier", "")))
            for identifier in identifiers
        }

    @classmethod
    def _matching_volume(
        cls, data: dict[str, Any], isbn: str
    ) -> dict[str, Any] | None:
        if data.get("totalItems", 0) <= 0 or "items" not in data:
            return None
        clean_isbn = cls._canonical_isbn(isbn)
        for item in data["items"]:
            if clean_isbn in cls._identifier_values(item):
                return item
        return None

    @staticmethod
    def _add_cover(metadata: Metadata, volume: dict[str, Any]) -> None:
        volume_id = volume.get("id")
        volume_info = volume.get("volumeInfo", {})
        image_link = volume_info.get("imageLinks", {}).get("thumbnail", "")
        if volume_id and image_link:
            metadata["artwork_url"] = image_link

    @staticmethod
    def _add_title(metadata: Metadata, volume_info: dict[str, Any]) -> None:
        title = volume_info.get("title")
        if not title:
            return
        subtitle = volume_info.get("subtitle")
        metadata["title"] = f"{title}: {subtitle}" if subtitle else title

    @staticmethod
    def _add_author_publisher(
        metadata: Metadata, volume_info: dict[str, Any]
    ) -> None:
        authors = volume_info.get("authors")
        if authors:
            metadata["author"] = ", ".join(authors)
        publisher = volume_info.get("publisher")
        if publisher:
            metadata["publisher"] = publisher

    @staticmethod
    def _add_description(
        metadata: Metadata, volume_info: dict[str, Any]
    ) -> None:
        description = volume_info.get("description")
        if description:
            metadata["overview"] = re.sub(r"<[^>]+>", "", description).strip()

    @staticmethod
    def _add_year(metadata: Metadata, volume_info: dict[str, Any]) -> None:
        published_date = volume_info.get("publishedDate")
        if not published_date:
            return
        year_match = re.search(r"\b\d{4}\b", published_date)
        if year_match is None:
            return
        year_str = year_match.group(0)
        metadata["year"] = year_str
        metadata["search_year"] = int(year_str)

    @staticmethod
    def _add_language(metadata: Metadata, volume_info: dict[str, Any]) -> None:
        language = volume_info.get("language")
        if not language:
            return
        try:
            full, iso3 = resolve_book_language(language)
        except Exception as exc:
            logger.debug(
                f"[yellow]Warning: Could not resolve language '{language}': {exc}[/yellow]"
            )
            return
        if not is_valid_book_language(full, iso3):
            return
        metadata["book_language"] = full
        if iso3:
            metadata["book_language_iso"] = iso3

    @staticmethod
    def _has_category_marker(categories: list[str], marker: str) -> bool:
        return any(marker in category for category in categories)

    @classmethod
    def _category_flags(cls, categories: list[str]) -> dict[str, bool]:
        return {
            "comic": cls._has_category_marker(categories, "comic"),
            "manga": cls._has_category_marker(categories, "manga"),
            "magazine": cls._has_category_marker(categories, "magazine"),
            "newspaper": cls._has_category_marker(categories, "newspaper"),
        }

    @staticmethod
    def _normalized_categories(categories: list[Any]) -> list[str]:
        return [str(category) for category in categories if category]

    @classmethod
    def _apply_category_flags(
        cls, metadata: Metadata, categories: list[str]
    ) -> None:
        lowered = [category.lower() for category in categories]
        for key, enabled in cls._category_flags(lowered).items():
            if enabled:
                metadata[key] = True

    @classmethod
    def _add_categories(
        cls, metadata: Metadata, volume_info: dict[str, Any]
    ) -> None:
        categories = volume_info.get("categories")
        if not categories:
            return
        normalized = cls._normalized_categories(categories)
        metadata["keywords"] = metadata["genres"] = normalized
        cls._apply_category_flags(metadata, normalized)

    def _parse_volume_info(
        self, data: dict[str, Any], isbn: str
    ) -> dict[str, Any] | None:
        """Parse a matching Google Books volume into normalized metadata."""
        volume = self._matching_volume(data, isbn)
        if volume is None:
            return None
        volume_info = volume.get("volumeInfo", {})
        metadata: Metadata = {}
        self._add_cover(metadata, volume)
        self._add_title(metadata, volume_info)
        self._add_author_publisher(metadata, volume_info)
        self._add_description(metadata, volume_info)
        self._add_year(metadata, volume_info)
        self._add_language(metadata, volume_info)
        self._add_categories(metadata, volume_info)
        metadata["isbn"] = isbn
        return metadata

    @staticmethod
    def _clean_isbn(isbn: str) -> str:
        return re.sub(r"[-\s]", "", isbn)

    @staticmethod
    def _search_url(clean_isbn: str, api_key: str) -> str:
        url = (
            f"https://www.googleapis.com/books/v1/volumes?q=isbn:{clean_isbn}"
        )
        if api_key:
            return f"{url}&key={api_key}"
        return url

    @staticmethod
    async def _cached_result(
        cache: Any, clean_isbn: str
    ) -> tuple[bool, Metadata | None]:
        cached_data = await cache.get("google_books", "isbn_exact", clean_isbn)
        if is_cache_miss(cached_data) or not isinstance(cached_data, dict):
            return False, None
        cached_mapping = cast(Metadata, cached_data)
        if cached_mapping.get("not_found"):
            logger.info(
                f"{google_color_str}: ISBN match not found (cached): {clean_isbn}"
            )
            return True, None
        logger.info(
            f"{google_color_str}: ISBN match found (cached): {clean_isbn}"
        )
        return True, cached_mapping

    @staticmethod
    async def _cache_not_found(cache: Any, clean_isbn: str) -> None:
        await cache.set(
            "google_books",
            "isbn_exact",
            clean_isbn,
            {"not_found": True},
            negative=True,
        )

    async def _metadata_from_success(
        self,
        data: dict[str, Any],
        isbn: str,
        clean_isbn: str,
        cache: Any,
    ) -> Metadata | None:
        if data.get("totalItems", 0) <= 0 or "items" not in data:
            logger.info(
                f"{google_color_str}: No items found for ISBN: {clean_isbn}"
            )
            await self._cache_not_found(cache, clean_isbn)
            return None
        metadata = self._parse_volume_info(data, isbn)
        if metadata is None:
            logger.info(
                f"{google_color_str}: ISBN match not found: {clean_isbn}"
            )
            await self._cache_not_found(cache, clean_isbn)
            return None
        await cache.set("google_books", "isbn_exact", clean_isbn, metadata)
        logger.info(f"{google_color_str}: ISBN match found: {clean_isbn}")
        return metadata

    async def _metadata_from_response(
        self,
        response: httpx.Response,
        isbn: str,
        clean_isbn: str,
        cache: Any,
    ) -> Metadata | None:
        if response.status_code == 200:
            return await self._metadata_from_success(
                response.json(), isbn, clean_isbn, cache
            )
        if response.status_code == 429:
            logger.info(
                f"{google_color_str}: Rate limited (Status 429) for ISBN: {clean_isbn}"
            )
            return None
        logger.info(
            f"{google_color_str}: API returned error status code {response.status_code} for ISBN: {clean_isbn}"
        )
        if response.status_code == 404:
            await self._cache_not_found(cache, clean_isbn)
        return None

    async def _fetch_metadata(
        self,
        isbn: str,
        clean_isbn: str,
        api_key: str,
        cache: Any,
    ) -> Metadata | None:
        url = self._search_url(clean_isbn, api_key)
        logger.debug(
            f"[cyan]{google_color_str}: Searching API for ISBN: {clean_isbn}[/cyan]"
        )
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
            return await self._metadata_from_response(
                response, isbn, clean_isbn, cache
            )
        except Exception as exc:
            logger.info(
                f"{google_color_str}: Network or query error for ISBN {clean_isbn}: {exc}"
            )
            return None

    async def search_by_isbn(
        self, isbn: str, base_dir: str = "", api_key: str = ""
    ) -> dict[str, Any] | None:
        """Search Google Books API by ISBN with exact-match caching."""
        clean_isbn = self._clean_isbn(isbn)
        if not clean_isbn:
            return None
        cache = cache_for(base_dir)
        cached, cached_data = await self._cached_result(cache, clean_isbn)
        if cached:
            return cached_data
        return await self._fetch_metadata(isbn, clean_isbn, api_key, cache)


google_books_manager = GoogleBooksManager()
