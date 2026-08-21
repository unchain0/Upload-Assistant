"""Shared parsing helpers for external metadata identifiers."""

import urllib.parse

_TMDB_HOSTS = {"themoviedb.org", "www.themoviedb.org"}


def parse_tmdb_id(id_str: str, category: str | None) -> tuple[str, int]:
    """Normalize a TMDb ID or TMDb URL into category and numeric ID."""
    normalized_category = category or ""
    parsed_id = id_str.lower().strip()
    if parsed_id.startswith(("http://", "https://")):
        normalized_category, parsed_id = _tmdb_url_identity(parsed_id, normalized_category)
    normalized_category, parsed_id = _tmdb_prefixed_identity(parsed_id, normalized_category)
    return normalized_category, _numeric_tmdb_id(parsed_id)


def _tmdb_url_identity(value: str, category: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(value)
    if not _valid_tmdb_url(parsed):
        return category, ""
    parts = [part for part in parsed.path.split("/") if part]
    return _identity_from_parts(parts, category)


def _valid_tmdb_url(parsed: urllib.parse.ParseResult) -> bool:
    hostname = (parsed.hostname or "").rstrip(".").lower()
    return parsed.scheme in {"http", "https"} and hostname in _TMDB_HOSTS


def _identity_from_parts(parts: list[str], category: str) -> tuple[str, str]:
    for index, type_part in enumerate(parts[:-1]):
        identity = _typed_tmdb_identity(type_part, parts[index + 1])
        if identity is not None:
            return identity
    return category, ""


def _typed_tmdb_identity(type_part: str, id_part: str) -> tuple[str, str] | None:
    if type_part not in {"tv", "movie"} or not id_part.isdigit():
        return None
    category = "TV" if type_part == "tv" else "MOVIE"
    return category, id_part


def _tmdb_prefixed_identity(value: str, category: str) -> tuple[str, str]:
    if not value.startswith(("tv/", "movie/")):
        return category, value
    type_part, _, remainder = value.partition("/")
    identity = remainder.split("/", 1)[0]
    return ("TV" if type_part == "tv" else "MOVIE"), identity


def _numeric_tmdb_id(value: str) -> int:
    return int(value) if value.isdigit() else 0
