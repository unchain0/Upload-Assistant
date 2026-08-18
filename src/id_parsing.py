"""Shared parsing helpers for external metadata identifiers."""

import urllib.parse

_TMDB_HOSTS = {"themoviedb.org", "www.themoviedb.org"}


def parse_tmdb_id(id_str: str, category: str | None) -> tuple[str, int]:
    """Normalize a TMDb ID or TMDb URL into category and numeric ID."""
    normalized_category = category or ""
    parsed_id = id_str.lower().strip()
    if parsed_id.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(parsed_id)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"http", "https"} or hostname not in _TMDB_HOSTS:
            return normalized_category, 0
        parts = [part for part in parsed.path.split("/") if part]
        parsed_id = ""
        for index, type_part in enumerate(parts[:-1]):
            if type_part not in {"tv", "movie"}:
                continue
            id_part = parts[index + 1]
            if not id_part.isdigit():
                continue
            normalized_category = "TV" if type_part == "tv" else "MOVIE"
            parsed_id = id_part
            break

    if parsed_id.startswith(("tv/", "movie/")):
        type_part, _, remainder = parsed_id.partition("/")
        parsed_id = remainder.split("/", 1)[0]
        normalized_category = "TV" if type_part == "tv" else "MOVIE"

    return normalized_category, int(parsed_id) if parsed_id.isdigit() else 0
