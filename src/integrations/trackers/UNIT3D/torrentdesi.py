from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.UNIT3D import UNIT3D


class DesiTorrents(UNIT3D):
    """
    DesiTorrents is an INDIAN Private Torrent Tracker for MOVIES / TV
    """

    tracker = "DESITORRENTS"
    display_name = "DesiTorrents"
    allows_bloated_audio = True
    base_url = "https://torrent.desi"
    banned_groups = (
        "DusIcTv",
        "PDHM",
        "Ranvijay",
        "BWT",
        "DDH",
        "Telly",
        "YTS",
        "RARBG",
        "BonsaiHD",
        "GalaxyRG",
        "-=!DrSTAR!=-",
    )
    id_url = f"{base_url}/api/v1/torrents/"
    upload_url = f"{base_url}/api/v1/torrents/upload"
    search_url = f"{base_url}/api/v1/torrents/filter"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config, tracker_name="DESITORRENTS")

        # Banned Groups

    async def get_category_id(
        self,
        meta: Meta,
        category: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """
        Returns the category ID for the release.
        """

        # DesiTorrents Category Map: Movie=1, TV=2, Music=3, Game=4
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "3", "GAME": "4"}

        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}
        if category:
            return {"category_id": category_id.get(category, "0")}
        meta_category = meta.category
        resolved_id = category_id.get(meta_category, "0")
        return {"category_id": resolved_id}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "3",
            "REMUX": "5",
            "ENCODE": "12",
            "WEBDL": "11",
            "WEBRIP": "12",
            "HDTV": "13",
            "DVD": "8",
        }

    @staticmethod
    def _disc_type_id(meta: Meta) -> str:
        return "4" if meta.disctype == "BD25" else "3"

    @staticmethod
    def _uhd_type_id(meta_type: str, is_uhd: bool) -> str | None:
        type_ids = {
            "REMUX": ("2", "5"),
            "ENCODE": ("1", "12"),
        }.get(meta_type)
        if type_ids is None:
            return None
        return type_ids[0] if is_uhd else type_ids[1]

    @classmethod
    def _dynamic_type_id(cls, meta: Meta) -> str:
        meta_type = str(meta.type or "")
        if meta_type == "DISC":
            return cls._disc_type_id(meta)
        uhd_type = cls._uhd_type_id(meta_type, bool(meta.uhd))
        if uhd_type is not None:
            return uhd_type
        return {
            "WEBDL": "11",
            "WEBRIP": "12",
            "DVD": "8",
            "HDTV": "13",
        }.get(meta_type, "0")

    async def get_type_id(
        self,
        meta: Meta,
        type: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """Returns the type ID (source) for the release."""
        type_id_map = self._type_mapping()
        if mapping_only:
            return type_id_map
        if reverse:
            return {value: key for key, value in type_id_map.items()}
        if type:
            return {"type_id": type_id_map.get(type, "0")}
        return {"type_id": self._dynamic_type_id(meta)}

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str = "",
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        """
        Returns the resolution ID for the release.
        """

        # DesiTorrents Specific Resolutions
        resolution_id = {
            "4320p": "9",  # 8k
            "2160p": "8",
            "1080p": "11",
            "1080i": "7",
            "720p": "6",
            "720i": "5",
            "576p": "4",
            "576i": "3",
            "540p": "12",
            "480p": "2",
            "480i": "1",
        }

        if mapping_only:
            return resolution_id
        if reverse:
            return {v: k for k, v in resolution_id.items()}
        if resolution:
            return {"resolution_id": resolution_id.get(resolution, "10")}
        meta_resolution = meta.resolution
        resolved_id = resolution_id.get(meta_resolution, "10")  # 10 is 'Other'
        return {"resolution_id": resolved_id}
