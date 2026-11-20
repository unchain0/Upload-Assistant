# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import UNIT3D

_UPLOAD_FIELDS = frozenset(
    {
        "name",
        "category_id",
        "type_id",
        "resolution_id",
        "tmdb",
        "imdb",
        "tvdb",
        "mal",
        "igdb",
        "season_number",
        "episode_number",
        "distributor_id",
        "region_id",
        "description",
        "mediainfo",
        "bdinfo",
        "keywords",
        "anonymous",
        "personal_release",
    }
)
_KEEP_DOT = re.compile(
    r"(?<!\d)\d\.\d(?!\d)|(?<![A-Za-z0-9])H\.26[45](?!\d)", re.IGNORECASE
)


def _spaced_release_name(name: str) -> str:
    kept_dots = {
        match.start() + match.group().index(".")
        for match in _KEEP_DOT.finditer(name)
    }
    value = "".join(
        " " if char == "." and index not in kept_dots else char
        for index, char in enumerate(name)
    )
    return " ".join(value.split())


def _single_episode_filename(meta: Meta) -> str:
    filelist = cast(list[str], meta.filelist or [])
    is_single_episode = all(
        (
            not meta.scene_name,
            bool(meta.isdir),
            not meta.tv_pack,
            bool(meta.episode),
            len(filelist) == 1,
        )
    )
    return Path(filelist[0]).stem.strip() if is_single_episode else ""


class Nightbloom(UNIT3D):
    """Nightbloom (NIG) is a custom tracker with a UNIT3D-compatible API."""

    tracker = "NIGHTBLOOM"
    display_name = "Nightbloom"
    base_url = "https://nightbloom.cx"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("MOVIE", "TV")
    tracker_urls = ("nightbloom.cx",)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name=self.tracker)

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = {"MOVIE": "1", "TV": "2", "ANIME": "6"}
        if mapping_only:
            return mapping
        if reverse:
            return {value: key for key, value in mapping.items()}
        resolved = category or meta.category or ""
        if meta.anime and resolved in {"MOVIE", "TV"}:
            resolved = "ANIME"
        return {"category_id": mapping.get(resolved, "0")}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        original = (
            _single_episode_filename(meta)
            or str(meta.scene_name or meta.basename_no_ext or "").strip()
        )
        tag = str(meta.tag or "").strip()
        if original and tag and original.casefold().endswith(tag.casefold()):
            return {"name": _spaced_release_name(original)}
        return await super().get_name(meta)

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        data = await super().get_data(meta)
        return {
            key: value for key, value in data.items() if key in _UPLOAD_FIELDS
        }
