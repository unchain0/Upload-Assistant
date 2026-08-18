"""Shared parsing helpers for external metadata identifiers."""

import urllib.parse


def parse_tmdb_id(id_str: str, category: str | None) -> tuple[str, int]:
    """Normalize a TMDb ID or TMDb URL into category and numeric ID."""
    normalized_category = category or ""
    parsed_id = id_str.lower().strip()
    if parsed_id.startswith("http"):
        parsed = urllib.parse.urlparse(parsed_id)
        path = parsed.path.strip("/")
        if "/" in path:
            parts = path.split("/")
            if len(parts) >= 2:
                type_part = parts[-2]
                id_part = parts[-1]
                if type_part == "tv":
                    normalized_category = "TV"
                elif type_part == "movie":
                    normalized_category = "MOVIE"
                parsed_id = id_part

    if parsed_id.startswith("tv"):
        parsed_id = parsed_id.split("/", 1)[1]
        normalized_category = "TV"
    elif parsed_id.startswith("movie"):
        parsed_id = parsed_id.split("/", 1)[1]
        normalized_category = "MOVIE"

    return normalized_category, int(parsed_id) if parsed_id.isdigit() else 0
