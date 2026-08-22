# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class Utopia(UNIT3D):
    """
    UTOPIA is a UKRAINIAN Private Tracker for HD MOVIES and TV
    """

    tracker = "UTOPIA"
    display_name = "Utopia"
    base_url = "https://utp.to"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")
    allowed_bloated_audio_languages = ("uk", "en")

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="UTOPIA")
        self.config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (category, reverse, mapping_only)
        category_name = meta.category
        category_id = {
            "MOVIE": "1",
            "TV": "2",
        }.get(category_name, "1")  # Default to MOVIE
        return {"category_id": category_id}

    async def get_resolution_id(
        self,
        meta: Meta,
        resolution: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (resolution, reverse, mapping_only)
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4",
        }.get(meta.resolution, "11")  # Default to Other (11)
        return {"resolution_id": resolution_id}

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        _ = (type, reverse, mapping_only)
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
        }.get(str(meta.type).upper(), "3")  # Default to ENCODE
        return {"type_id": type_id}

    @staticmethod
    def _transformed_image(image: dict[str, Any]) -> dict[str, Any]:
        medium = image.get("img_url", "")
        return {
            "web_url": image.get("raw_url", ""),
            "raw_url": medium,
            "img_url": medium,
        }

    @classmethod
    def _transformed_images(
        cls, images: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [cls._transformed_image(image) for image in images]

    @staticmethod
    def _packed_image_keys(meta: Meta) -> list[str]:
        return [key for key in meta.to_dict() if key.startswith("new_images_")]

    @classmethod
    def _swap_packed_images(cls, meta: Meta) -> dict[str, Any]:
        originals: dict[str, Any] = {}
        for key in cls._packed_image_keys(meta):
            originals[key] = meta[key]
            meta[key] = cls._transformed_images(meta[key])
        return originals

    @staticmethod
    def _restore_packed_images(meta: Meta, originals: dict[str, Any]) -> None:
        for key, value in originals.items():
            meta[key] = value

    async def get_description(self, meta: Meta) -> dict[str, str]:
        """Use full image links with medium images for utppm compatibility."""
        from src.integrations.trackers.description_builder import (
            DescriptionBuilder,
        )

        original_image_list = meta.image_list
        original_new_images = self._swap_packed_images(meta)
        meta.image_list = self._transformed_images(original_image_list)
        try:
            description = await DescriptionBuilder(
                self.tracker, self.config
            ).general_description_generator(meta, mediainfo=False, nfo=False)
        finally:
            meta.image_list = original_image_list
            self._restore_packed_images(meta, original_new_images)
        return {"description": description}

    _lossless_audio_indicators = (
        "Atmos",
        "TrueHD",
        "DTS-HD MA",
        "DTS:X",
        "LPCM",
        "FLAC",
        "PCM",
    )

    @classmethod
    def _name_audio(cls, meta: Meta) -> str:
        if not any(
            indicator in meta.audio
            for indicator in cls._lossless_audio_indicators
        ):
            return ""
        audio = meta.audio.replace("Dual-Audio", "").replace("Dubbed", "")
        return " ".join(audio.split())

    @staticmethod
    def _bluray_name_components(
        meta: Meta, release_type: str
    ) -> tuple[str, str, str]:
        type_tag = "BDRemux" if release_type == "REMUX" else "BDRip"
        vcodec = (
            meta.video_codec if release_type == "REMUX" else meta.video_encode
        )
        return "", type_tag, vcodec

    @staticmethod
    def _web_name_components(
        meta: Meta, release_type: str
    ) -> tuple[str, str, str]:
        type_tag = "WEB-DL" if release_type == "WEBDL" else "WEBRip"
        return str(meta.service), type_tag, meta.video_encode

    @classmethod
    def _source_type_video(cls, meta: Meta) -> tuple[str, str, str]:
        release_type = str(meta.type).upper()
        if release_type in {"REMUX", "ENCODE"}:
            return cls._bluray_name_components(meta, release_type)
        if release_type in {"WEBDL", "WEBRIP"}:
            return cls._web_name_components(meta, release_type)
        if release_type == "HDTV":
            return str(meta.source), "", meta.video_encode
        return str(meta.source), "", meta.video_codec

    @classmethod
    def _movie_name(
        cls, meta: Meta, source: str, type_tag: str, vcodec: str, audio: str
    ) -> str:
        year = str(meta.year) if meta.year is not None else ""
        hybrid = "Hybrid" if meta.webdv else ""
        parts = (
            meta.title,
            meta.aka.strip(),
            year,
            hybrid,
            meta.repack,
            meta.edition,
            meta.region or "",
            meta.three_d,
            meta.uhd,
            source,
            type_tag,
            meta.resolution,
            meta.hdr,
            vcodec,
            audio,
        )
        return " ".join(str(part) for part in parts)

    @classmethod
    def _tv_name(
        cls, meta: Meta, source: str, type_tag: str, vcodec: str, audio: str
    ) -> str:
        year = str(meta.year) if meta.year is not None else ""
        hybrid = "Hybrid" if meta.webdv else ""
        parts = (
            meta.title,
            meta.aka.strip(),
            f"{meta.season}{meta.episode}",
            year,
            hybrid,
            meta.edition,
            meta.repack,
            meta.region or "",
            meta.three_d,
            meta.uhd,
            source,
            type_tag,
            meta.resolution,
            meta.hdr,
            vcodec,
            audio,
        )
        return " ".join(str(part) for part in parts)

    @classmethod
    def _category_name(
        cls, meta: Meta, source: str, type_tag: str, vcodec: str, audio: str
    ) -> str:
        if str(meta.category) == "MOVIE":
            return cls._movie_name(meta, source, type_tag, vcodec, audio)
        if str(meta.category) == "TV":
            return cls._tv_name(meta, source, type_tag, vcodec, audio)
        return meta.name

    @staticmethod
    def _final_name(name: str, tag: str | None) -> str:
        cleaned = " ".join(name.split())
        return f"{cleaned}{tag}" if tag else cleaned

    async def get_name(self, meta: Meta) -> dict[str, str]:
        """Build a UTOPIA-compliant torrent name from normalized components."""
        source, type_tag, vcodec = self._source_type_video(meta)
        audio = self._name_audio(meta)
        name = self._category_name(meta, source, type_tag, vcodec, audio)
        return {"name": self._final_name(name, meta.tag)}
