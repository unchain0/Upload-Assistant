from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from src.domain_models.release import Meta


def _year(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        year = int(str(value).strip())
    except TypeError, ValueError:
        return None
    return year if 1800 <= year <= 2200 else None


@dataclass(frozen=True, slots=True)
class ReleaseYearIdentity:
    """Semantic view of release-year sources with one explicit canonical value."""

    canonical: int | None
    metadata: int | None
    search: int | None
    manual: int | None
    imdb: int | None

    @classmethod
    def from_release(cls, meta: Meta) -> ReleaseYearIdentity:
        manual = _year(meta.manual_year)
        metadata = _year(meta.year)
        search = _year(meta.search_year)
        imdb = cls._imdb_year(meta.imdb_info)
        return cls(
            canonical=manual or metadata or search,
            metadata=metadata,
            search=search,
            manual=manual,
            imdb=imdb,
        )

    @staticmethod
    def _imdb_year(value: Any) -> int | None:
        if not isinstance(value, dict):
            return None
        mapping = cast(dict[str, Any], value)
        return _year(mapping.get("year"))

    @property
    def canonical_text(self) -> str:
        return "" if self.canonical is None else str(self.canonical)
