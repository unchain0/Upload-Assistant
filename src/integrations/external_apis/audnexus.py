# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

from typing import Any

import httpx

from src.integrations.observability.runtime_support import logger

AUDNEXUS_BASE = "https://api.audnex.us/books"


class AudnexusManager:
    """Resolve audiobook metadata (narrator) from Audnexus by ASIN."""

    @staticmethod
    def _narrators(payload: dict[str, Any]) -> str:
        narrators = payload.get("narrators")
        if not isinstance(narrators, list):
            return ""
        names = [
            str(item.get("name", "")).strip()
            for item in narrators
            if isinstance(item, dict)
        ]
        return ", ".join(name for name in names if name)

    async def metadata_by_asin(
        self, asin: str, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any]:
        identifier = str(asin or "").strip()
        if not identifier:
            return {}
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=10.0)
        try:
            response = await http.get(
                f"{AUDNEXUS_BASE}/{identifier}", params={"region": "us"}
            )
        except Exception as error:
            logger.warning(
                f"[yellow]Audnexus lookup failed for {identifier}: {error}[/yellow]"
            )
            return {}
        finally:
            if owns_client:
                await http.aclose()
        if response.status_code != 200:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        year = ""
        release_date = str(payload.get("releaseDate") or "")
        if release_date[:4].isdigit():
            year = release_date[:4]
        return {
            "narrator": self._narrators(payload),
            "language": str(payload.get("language") or "").strip(),
            "publisher": str(payload.get("publisherName") or "").strip(),
            "year": year,
        }

    async def narrator_by_asin(
        self, asin: str, client: httpx.AsyncClient | None = None
    ) -> str:
        metadata = await self.metadata_by_asin(asin, client=client)
        return str(metadata.get("narrator") or "")
