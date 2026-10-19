# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D


class TorrentHR(UNIT3D):
    """TorrentHR (THR) is a Croatian UNIT3D tracker for movies and TV."""

    tracker = "TORRENTHR"
    display_name = "TorrentHR"
    base_url = "https://www.torrenthr.org"
    banned_groups: tuple[str, ...] = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("torrenthr.org",)
    allows_bloated_audio = True

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name=self.tracker)
        self.common = Common(config)

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE_SD": "4",
            "MOVIE_DVD": "14",
            "MOVIE_HD": "17",
            "ANIMATION": "18",
            "TV_SD": "7",
            "TV_HD": "34",
            "ANIME": "31",
            "MOVIE_BD": "40",
            "DOCUMENTARY": "12",
        }

    @staticmethod
    def _resolved_category(meta: Meta, category: str | None) -> str:
        if category is not None and category != "":
            return category
        return meta.category

    @staticmethod
    def _themed_category_key(meta: Meta) -> str | None:
        genres = f"{meta.combined_genres} {meta.keywords}".lower()
        if "documentary" in genres:
            return "DOCUMENTARY"
        if meta.anime:
            return "ANIME"
        if "animation" in genres or "cartoon" in genres:
            return "ANIMATION"
        return None

    @staticmethod
    def _movie_category_key(meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return "MOVIE_BD"
        if meta.is_disc in {"DVD", "HDDVD"}:
            return "MOVIE_DVD"
        return "MOVIE_SD" if meta.sd else "MOVIE_HD"

    @staticmethod
    def _base_category_key(meta: Meta, category: str) -> str | None:
        if category == "MOVIE":
            return TorrentHR._movie_category_key(meta)
        if category == "TV":
            return "TV_SD" if meta.sd else "TV_HD"
        return None

    @classmethod
    def _category_key(cls, meta: Meta, category: str) -> str | None:
        themed = cls._themed_category_key(meta)
        return (
            themed
            if themed is not None
            else cls._base_category_key(meta, category)
        )

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        category_id = self._category_mapping()
        if mapping_only:
            return category_id
        if reverse:
            return {value: key for key, value in category_id.items()}
        resolved = self._resolved_category(meta, category)
        resolved_key = self._category_key(meta, resolved)
        if resolved_key is None:
            return {"category_id": "0"}
        return {"category_id": category_id[resolved_key]}
