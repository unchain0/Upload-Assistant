# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

import httpx

from src.integrations.cache.metadata_cache import (
    MetadataCache,
    cache_for,
    is_cache_miss,
)
from src.integrations.observability.runtime_support import logger

openlibrary_color_str = "[#e1d8c1]OpenLibrary[/#e1d8c1]"
Metadata = dict[str, Any]


class OpenLibraryManager:
    @staticmethod
    async def _cached_author_name(
        cache: MetadataCache, author_id: str
    ) -> tuple[bool, str]:
        cached_data = await cache.get("openlibrary", "author", author_id)
        if is_cache_miss(cached_data) or not isinstance(cached_data, dict):
            return False, ""
        cached_mapping = cast(Metadata, cached_data)
        return True, str(cached_mapping.get("name", ""))

    @staticmethod
    async def _cache_author_name(
        cache: MetadataCache, author_id: str, name: str
    ) -> None:
        if name:
            await cache.set("openlibrary", "author", author_id, {"name": name})
            return
        await cache.set("openlibrary", "author", author_id, {}, negative=True)

    @classmethod
    async def _author_response_name(
        cls, response: httpx.Response, cache: MetadataCache, author_id: str
    ) -> str:
        if response.status_code == 404:
            await cls._cache_author_name(cache, author_id, "")
            return ""
        if response.status_code != 200:
            return ""
        data = cast(Metadata, response.json())
        name = str(data.get("name") or data.get("personal_name") or "")
        await cls._cache_author_name(cache, author_id, name)
        return name

    async def get_author_name(
        self, author_key: str, client: httpx.AsyncClient, cache: MetadataCache
    ) -> str:
        """Fetch an author name from a key such as /authors/OL26320A."""
        author_id = author_key.split("/")[-1]
        cached, name = await self._cached_author_name(cache, author_id)
        if cached:
            return name
        url = f"https://openlibrary.org/authors/{author_id}.json"
        try:
            response = await client.get(url, timeout=10.0)
            return await self._author_response_name(response, cache, author_id)
        except Exception as exc:
            logger.debug(
                f"[yellow]Warning: Error fetching author name for {author_id}: {exc}[/yellow]"
            )
            return ""

    @staticmethod
    async def _cached_metadata(
        cache: MetadataCache,
        kind: str,
        key: str,
        label: str,
    ) -> tuple[bool, Metadata | None]:
        cached_data = await cache.get("openlibrary", kind, key)
        if is_cache_miss(cached_data) or not isinstance(cached_data, dict):
            return False, None
        cached_mapping = cast(Metadata, cached_data)
        if cached_mapping.get("not_found"):
            logger.info(
                f"{openlibrary_color_str}: {label} match not found (cached): {key}"
            )
            return True, None
        logger.info(
            f"{openlibrary_color_str}: {label} match found (cached): {key}"
        )
        return True, cached_mapping

    @staticmethod
    async def _cache_not_found(
        cache: MetadataCache, kind: str, key: str
    ) -> None:
        await cache.set(
            "openlibrary", kind, key, {"not_found": True}, negative=True
        )

    @staticmethod
    def _description_text(description: Any) -> str:
        if not description:
            return ""
        if isinstance(description, dict):
            description_map = cast(Metadata, description)
            value = str(description_map.get("value", ""))
        else:
            value = str(description)
        return re.sub(r"<[^>]+>", "", value).strip()

    @staticmethod
    def _cover_url(covers: Any) -> str:
        if not isinstance(covers, list) or not covers:
            return ""
        typed_covers = cast(list[Any], covers)
        first = typed_covers[0]
        if not isinstance(first, int) or first <= 0:
            return ""
        return f"https://covers.openlibrary.org/b/id/{first}-L.jpg"

    @staticmethod
    def _author_key(author_entry: Any) -> str:
        if not isinstance(author_entry, dict):
            return ""
        entry = cast(Metadata, author_entry)
        author = entry.get("author")
        if not isinstance(author, dict):
            return ""
        author_map = cast(Metadata, author)
        return str(author_map.get("key", ""))

    async def _work_author_names(
        self,
        authors: Any,
        client: httpx.AsyncClient,
        cache: MetadataCache,
    ) -> list[str]:
        if not isinstance(authors, list):
            return []
        names: list[str] = []
        for author_entry in cast(list[Any], authors):
            author_key = self._author_key(author_entry)
            if not author_key:
                continue
            author_name = await self.get_author_name(author_key, client, cache)
            if author_name:
                names.append(author_name)
        return names

    @staticmethod
    def _subject_list(subjects: Any) -> list[str]:
        if not isinstance(subjects, list):
            return []
        return [
            str(subject)
            for subject in cast(list[Any], subjects)[:10]
            if subject
        ]

    @classmethod
    def _add_work_description(cls, metadata: Metadata, data: Metadata) -> None:
        description = cls._description_text(data.get("description"))
        if description:
            metadata["overview"] = description

    @classmethod
    def _add_work_cover(cls, metadata: Metadata, data: Metadata) -> None:
        cover_url = cls._cover_url(data.get("covers"))
        if cover_url:
            metadata["artwork_url"] = cover_url

    async def _add_work_authors(
        self,
        metadata: Metadata,
        data: Metadata,
        client: httpx.AsyncClient,
        cache: MetadataCache,
    ) -> None:
        author_names = await self._work_author_names(
            data.get("authors"), client, cache
        )
        if author_names:
            metadata["author"] = ", ".join(author_names)

    @classmethod
    def _add_work_subjects(cls, metadata: Metadata, data: Metadata) -> None:
        subjects = cls._subject_list(data.get("subjects"))
        if not subjects:
            return
        metadata["keywords"] = list(subjects)
        metadata["genres"] = list(subjects)

    async def _work_metadata(
        self,
        data: Metadata,
        work_id: str,
        client: httpx.AsyncClient,
        cache: MetadataCache,
    ) -> Metadata | None:
        title = data.get("title")
        if not title:
            return None
        subtitle = data.get("subtitle")
        metadata: Metadata = {
            "title": f"{title}: {subtitle}" if subtitle else title,
            "openlibrary": work_id,
        }
        self._add_work_description(metadata, data)
        self._add_work_cover(metadata, data)
        await self._add_work_authors(metadata, data, client, cache)
        self._add_work_subjects(metadata, data)
        return metadata

    async def _work_response_metadata(
        self,
        response: httpx.Response,
        work_id: str,
        client: httpx.AsyncClient,
        cache: MetadataCache,
    ) -> Metadata | None:
        if response.status_code != 200:
            logger.info(
                f"{openlibrary_color_str}: API returned error status code {response.status_code} for Work ID: {work_id}"
            )
            if response.status_code == 404:
                await self._cache_not_found(cache, "work", work_id)
            return None
        data = cast(Metadata, response.json())
        metadata = await self._work_metadata(data, work_id, client, cache)
        if metadata is not None:
            await cache.set("openlibrary", "work", work_id, metadata)
            return metadata
        logger.info(
            f"{openlibrary_color_str}: No metadata found for Work ID: {work_id}"
        )
        await self._cache_not_found(cache, "work", work_id)
        return None

    async def search_by_work_id(
        self, work_id: str, base_dir: str = ""
    ) -> dict[str, Any] | None:
        """Search OpenLibrary by Work ID (e.g. OL45883W)."""
        work_id = work_id.strip()
        if not work_id:
            return None
        cache = cache_for(base_dir)
        cached, cached_data = await self._cached_metadata(
            cache, "work", work_id, "Work"
        )
        if cached:
            return cached_data
        url = f"https://openlibrary.org/works/{work_id}.json"
        logger.debug(
            f"[cyan]{openlibrary_color_str}: Searching API for Work ID: {work_id}[/cyan]"
        )
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
                return await self._work_response_metadata(
                    response, work_id, client, cache
                )
        except Exception as exc:
            logger.info(
                f"{openlibrary_color_str}: Network or query error for Work ID {work_id}: {exc}"
            )
            return None

    @staticmethod
    def _clean_isbn(isbn: str) -> str:
        return re.sub(r"[-\s]", "", isbn)

    @staticmethod
    def _work_key(details: Metadata) -> str:
        works = details.get("works", [])
        if not isinstance(works, list) or not works:
            return ""
        first = cast(list[Any], works)[0]
        if not isinstance(first, dict):
            return ""
        first_map = cast(Metadata, first)
        return str(first_map.get("key", ""))

    @staticmethod
    def _publisher_text(publishers: Any) -> str:
        if not isinstance(publishers, list) or not publishers:
            return ""
        return ", ".join(str(value) for value in cast(list[Any], publishers))

    def _merge_work_details(
        self, metadata: Metadata, details: Metadata, clean_isbn: str
    ) -> Metadata:
        publisher = self._publisher_text(details.get("publishers"))
        if publisher and not metadata.get("publisher"):
            metadata["publisher"] = publisher
        self._add_year(metadata, details.get("publish_date"))
        metadata["isbn"] = clean_isbn
        return metadata

    async def _isbn_metadata_from_book(
        self,
        book_data: Metadata,
        clean_isbn: str,
        base_dir: str,
        cache: MetadataCache,
    ) -> Metadata | None:
        raw_details = book_data.get("details", {})
        details = (
            cast(Metadata, raw_details)
            if isinstance(raw_details, dict)
            else {}
        )
        work_key = self._work_key(details)
        if work_key:
            metadata = await self.search_by_work_id(
                work_key.split("/")[-1], base_dir
            )
            if metadata:
                merged = self._merge_work_details(
                    metadata, details, clean_isbn
                )
                await cache.set("openlibrary", "isbn", clean_isbn, merged)
                return merged
        metadata = self._metadata_from_book_details(
            book_data, details, clean_isbn
        )
        if metadata:
            await cache.set("openlibrary", "isbn", clean_isbn, metadata)
            return metadata
        await self._cache_not_found(cache, "isbn", clean_isbn)
        return None

    async def _isbn_response_metadata(
        self,
        response: httpx.Response,
        bibkey: str,
        clean_isbn: str,
        base_dir: str,
        cache: MetadataCache,
    ) -> Metadata | None:
        if response.status_code != 200:
            logger.info(
                f"{openlibrary_color_str}: API returned error status code {response.status_code} for ISBN: {clean_isbn}"
            )
            if response.status_code == 404:
                await self._cache_not_found(cache, "isbn", clean_isbn)
            return None
        response_data = cast(Metadata, response.json())
        raw_book_data = response_data.get(bibkey)
        if isinstance(raw_book_data, dict):
            return await self._isbn_metadata_from_book(
                cast(Metadata, raw_book_data), clean_isbn, base_dir, cache
            )
        logger.info(
            f"{openlibrary_color_str}: No items found for ISBN: {clean_isbn}"
        )
        await self._cache_not_found(cache, "isbn", clean_isbn)
        return None

    async def search_by_isbn(
        self, isbn: str, base_dir: str = ""
    ) -> dict[str, Any] | None:
        """Search OpenLibrary by ISBN."""
        clean_isbn = self._clean_isbn(isbn)
        if not clean_isbn:
            return None
        cache = cache_for(base_dir)
        cached, cached_data = await self._cached_metadata(
            cache, "isbn", clean_isbn, "ISBN"
        )
        if cached:
            return cached_data
        bibkey = f"ISBN:{clean_isbn}"
        url = f"https://openlibrary.org/api/books?bibkeys={bibkey}&jscmd=details&format=json"
        logger.debug(
            f"[cyan]{openlibrary_color_str}: Searching API for ISBN: {clean_isbn}[/cyan]"
        )
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
            return await self._isbn_response_metadata(
                response, bibkey, clean_isbn, base_dir, cache
            )
        except Exception as exc:
            logger.info(
                f"{openlibrary_color_str}: Network or query error for ISBN {clean_isbn}: {exc}"
            )
            return None

    @staticmethod
    def _add_year(metadata: Metadata, publish_date: Any) -> None:
        if not publish_date or metadata.get("year"):
            return
        year_match = re.search(r"\b\d{4}\b", str(publish_date))
        if year_match is None:
            return
        year = year_match.group(0)
        metadata["year"] = year
        metadata["search_year"] = int(year)

    @staticmethod
    def _book_title(details: Metadata) -> str:
        title = details.get("title")
        if not title:
            return ""
        subtitle = details.get("subtitle")
        return f"{title}: {subtitle}" if subtitle else str(title)

    @staticmethod
    def _book_authors(details: Metadata) -> list[str]:
        raw_authors = details.get("authors", [])
        if not isinstance(raw_authors, list):
            return []
        names: list[str] = []
        for author in cast(list[Any], raw_authors):
            if not isinstance(author, dict):
                continue
            name = cast(Metadata, author).get("name")
            if name:
                names.append(str(name))
        return names

    def _metadata_from_book_details(
        self, book_data: dict[str, Any], details: dict[str, Any], isbn: str
    ) -> dict[str, Any]:
        title = self._book_title(details)
        if not title:
            return {}
        metadata: Metadata = {"title": title, "isbn": isbn}
        authors = self._book_authors(details)
        if authors:
            metadata["author"] = ", ".join(authors)
        publisher = self._publisher_text(details.get("publishers"))
        if publisher:
            metadata["publisher"] = publisher
        self._add_year(metadata, details.get("publish_date"))
        thumbnail_url = book_data.get("thumbnail_url")
        if thumbnail_url:
            metadata["artwork_url"] = str(thumbnail_url).replace(
                "-S.jpg", "-L.jpg"
            )
        return metadata


openlibrary_manager = OpenLibraryManager()
