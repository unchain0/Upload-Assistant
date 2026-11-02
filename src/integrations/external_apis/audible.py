# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import difflib
import re
from typing import Any, ClassVar

import httpx

from src.integrations.observability.runtime_support import logger

AUDIBLE_SEARCH_URL = "https://api.audible.com/1.0/catalog/products"
_TITLE_THRESHOLD = 0.9


def _normalized(value: object) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").casefold())
    return " ".join(text.split())


class AudibleManager:
    """Resolve an ASIN from a title and author, refusing ambiguous matches."""

    _cache: ClassVar[dict[tuple[str, str], str]] = {}

    @classmethod
    def _title_similarity(cls, candidate: object, title: str) -> float:
        return difflib.SequenceMatcher(
            None, _normalized(candidate), _normalized(title)
        ).ratio()

    @classmethod
    def _author_matches(cls, values: list[Any], author: str) -> bool:
        wanted = set(_normalized(author).split())
        if not wanted:
            return False
        for value in values:
            name = value.get("name") if isinstance(value, dict) else value
            if wanted & set(_normalized(name).split()):
                return True
        return False

    @classmethod
    def _candidate_asin(
        cls, products: list[Any], title: str, author: str
    ) -> str:
        matches: list[tuple[str, str]] = []
        for product in products:
            if not isinstance(product, dict):
                continue
            if cls._title_similarity(product.get("title"), title) < _TITLE_THRESHOLD:
                continue
            if not cls._author_matches(product.get("authors", []), author):
                continue
            asin = str(product.get("asin", "")).strip()
            if asin:
                matches.append((asin, _normalized(product.get("title"))))
        if not matches:
            return ""
        titles = {normalized for _asin, normalized in matches}
        if len(titles) == 1:
            # Same title and author, different marketplaces/editions: the
            # first result is the US edition on the default marketplace.
            return matches[0][0]
        logger.info(
            f"{len(matches)} Audible results match but their titles differ; "
            "refusing to guess an ASIN"
        )
        return ""

    async def asin_by_title_author(
        self,
        title: str,
        author: str,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        if not str(title or "").strip() or not str(author or "").strip():
            return ""
        key = (_normalized(title), _normalized(author))
        if key in self._cache:
            return self._cache[key]
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await http.get(
                AUDIBLE_SEARCH_URL,
                params={
                    "title": title,
                    "author": author,
                    "num_results": 10,
                    "response_groups": "contributors,product_desc",
                },
            )
        except Exception as error:
            logger.warning(f"[yellow]Audible lookup failed: {error}[/yellow]")
            return ""
        finally:
            if owns_client:
                await http.aclose()
        if response.status_code != 200:
            logger.warning(
                f"[yellow]Audible search returned HTTP {response.status_code}; "
                "no ASIN resolved[/yellow]"
            )
            return ""
        payload = response.json()
        products = payload.get("products") if isinstance(payload, dict) else None
        asin = self._candidate_asin(
            products if isinstance(products, list) else [], title, author
        )
        if not asin:
            logger.info("No confident ASIN found on Audible")
        self._cache[key] = asin
        return asin
