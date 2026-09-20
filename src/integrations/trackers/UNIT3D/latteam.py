# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

from src.domain_models.release import Meta
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class LatTeam(UNIT3D):
    """
    Lat-Team is a SPANISH Private Torrent Tracker for MOVIES / TV
    """

    tracker = "LATTEAM"
    display_name = "LatTeam"
    base_url = "https://lat-team.com"
    banned_groups = ("EVO",)
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK")
    tracker_urls = ("https://lat-team.com",)
    allowed_bloated_audio_languages = ("es",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="LATTEAM")
        self.config: Config = config
        self.common = Common(config)

    async def get_category_id(
        self,
        meta: Meta,
        category: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._category_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = self._resolved_category(meta, category)
        category_id = mapping.get(resolved, "0")
        if resolved == "TV":
            category_id = self._tv_category_id(meta, category_id)
        return {"category_id": category_id}

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "EBOOK": "18",
            "AUDIOBOOK": "11",
            "MAGAZINE": "29",
            "COMIC": "30",
        }

    @classmethod
    def _resolved_category(cls, meta: Meta, requested: str | None) -> str:
        resolved = requested if requested else str(meta.category)
        if resolved != "BOOK":
            return resolved
        return cls._book_category(meta)

    @staticmethod
    def _book_category(meta: Meta) -> str:
        if meta.audiobook:
            return "AUDIOBOOK"
        if meta.comic or meta.manga:
            return "COMIC"
        if meta.magazine:
            return "MAGAZINE"
        return "EBOOK"

    @classmethod
    def _tv_category_id(cls, meta: Meta, fallback: str) -> str:
        if meta.anime:
            return "5"
        if cls._is_soap(meta):
            return "8"
        if cls._is_asian_drama(meta):
            return "20"
        return fallback

    @staticmethod
    def _soap_keywords() -> tuple[str, ...]:
        return ("telenovela", "novela", "soap", "culebrón", "culebron")

    @classmethod
    def _is_soap(cls, meta: Meta) -> bool:
        keywords = {str(value).lower() for value in meta.keywords}
        overview = str(meta.overview or "").lower()
        return any(
            word in keywords or word in overview
            for word in cls._soap_keywords()
        )

    @classmethod
    def _is_asian_drama(cls, meta: Meta) -> bool:
        genres = {str(value).lower() for value in meta.genres}
        if "drama" not in genres:
            return False
        countries = cls._origin_countries(meta.origin_country)
        return any(country in cls._asian_countries() for country in countries)

    @staticmethod
    def _origin_countries(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _asian_countries() -> set[str]:
        return {
            "AE",
            "AF",
            "AM",
            "AZ",
            "BD",
            "BH",
            "BN",
            "BT",
            "CN",
            "CY",
            "GE",
            "HK",
            "ID",
            "IL",
            "IN",
            "IQ",
            "IR",
            "JO",
            "JP",
            "KG",
            "KH",
            "KP",
            "KR",
            "KW",
            "KZ",
            "LA",
            "LB",
            "LK",
            "MM",
            "MN",
            "MO",
            "MV",
            "MY",
            "NP",
            "OM",
            "PH",
            "PK",
            "PS",
            "QA",
            "SA",
            "SG",
            "SY",
            "TH",
            "TJ",
            "TL",
            "TM",
            "TR",
            "TW",
            "UZ",
            "VN",
            "YE",
        }

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        mapping = self._type_mapping()
        mode = self._mapping_mode(
            mapping, reverse=reverse, mapping_only=mapping_only
        )
        if mode is not None:
            return mode
        resolved = self._resolved_type(type if type else meta.type)
        value = mapping.get(resolved, "0")
        if meta.category == "BOOK" and value == "0":
            value = "21"
        return {"type_id": value}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "DVDRIP": "3",
            "FLAC": "7",
            "ALAC": "8",
            "AC3": "9",
            "AAC": "10",
            "MP3": "11",
            "M4A": "18",
            "M4B": "17",
            "EPUB": "14",
            "PDF": "23",
            "CBZ": "25",
            "CBR": "25",
            "AZW3": "26",
            "MOBI": "26",
            "KFX": "26",
            "OTHER": "21",
        }

    @staticmethod
    def _resolved_type(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        resolved = value.upper().strip().lstrip(".")
        if resolved in {"CBZ", "CBR"}:
            return "CBZ"
        if resolved in {"AZW3", "MOBI", "KFX"}:
            return "AZW3"
        return resolved

    async def get_name(self, meta: Meta) -> dict[str, str]:
        name = (
            self._book_name(meta)
            if meta.category == "BOOK"
            else self._video_name(meta)
        )
        return {"name": re.sub(r"\s{2,}", " ", name).strip()}

    @classmethod
    def _book_name(cls, meta: Meta) -> str:
        author = str(meta.author or "").strip()
        title = str(meta.title or "").strip()
        fmt = str(meta.type or "").strip().upper()
        extra = cls._book_extra_info(meta)
        identity = f"{author} - {title}" if author else title
        return f"{identity}{cls._formatted_extra(extra)} {fmt}"

    @classmethod
    def _book_extra_info(cls, meta: Meta) -> list[str]:
        extra: list[str] = []
        cls._append_book_issue_info(extra, meta)
        cls._append_edition_info(extra, meta)
        cls._append_narration_info(extra, meta)
        return extra

    @classmethod
    def _append_book_issue_info(cls, extra: list[str], meta: Meta) -> None:
        cls._append_optional(extra, "Vol", cls._volume_value(meta))
        cls._append_optional(extra, "No", cls._issue_value(meta))

    @staticmethod
    def _volume_value(meta: Meta) -> str:
        value = meta.manual_season if meta.manual_season else meta.season
        return str(value or "").strip()

    @staticmethod
    def _issue_value(meta: Meta) -> str:
        value = meta.manual_episode if meta.manual_episode else meta.episode
        return str(value or "").strip()

    @staticmethod
    def _append_optional(extra: list[str], label: str, value: str) -> None:
        if value:
            extra.append(f"{label} {value}")

    @classmethod
    def _append_edition_info(cls, extra: list[str], meta: Meta) -> None:
        edition = str(meta.manual_edition or meta.edition or "").strip()
        if not edition:
            return
        extra.append(
            edition
            if cls._has_edition_marker(edition)
            else f"{edition} Edition"
        )

    @staticmethod
    def _has_edition_marker(value: str) -> bool:
        lowered = value.lower()
        return any(
            marker in lowered
            for marker in ("edición", "edicion", "edition", "ed.", "ed")
        )

    @classmethod
    def _append_narration_info(cls, extra: list[str], meta: Meta) -> None:
        if not meta.audiobook:
            return
        narration = cls._narration_label(str(meta.book_language or ""))
        if narration:
            extra.append(narration)

    @classmethod
    def _narration_label(cls, language: str) -> str:
        lowered = language.lower()
        if cls._contains_marker(lowered, ("spain", "castilian", "castellano")):
            return "Narración en Castellano"
        if cls._contains_marker(lowered, ("latin", "latino")):
            return "Narración en Latino"
        if cls._contains_marker(
            lowered, ("portuguese", "português", "portugues")
        ):
            return "Narración en Portugués"
        return f"Narración en {language.title()}" if lowered else ""

    @staticmethod
    def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
        return any(marker in value for marker in markers)

    @staticmethod
    def _formatted_extra(extra: list[str]) -> str:
        return (
            ""
            if not extra
            else " " + " ".join(f"({value})" for value in extra)
        )

    @classmethod
    def _video_name(cls, meta: Meta) -> str:
        aka = str(meta.aka or "")
        name = (
            str(meta.name or "")
            .replace("Dual-Audio", "")
            .replace("Dubbed", "")
            .replace(aka, "")
        )
        if meta.type == "DISC":
            return name
        name = cls._localized_title(name, meta, aka)
        return cls._apply_spanish_audio_tag(name, meta)

    @staticmethod
    def _localized_title(name: str, meta: Meta, aka: str) -> str:
        if meta.original_language != "es" or not aka:
            return name
        return name.replace(str(meta.title), aka.replace("AKA", "")).strip()

    @classmethod
    def _apply_spanish_audio_tag(cls, name: str, meta: Meta) -> str:
        has_latino, has_castilian = cls._spanish_audio_presence(meta)
        if has_castilian and not has_latino:
            return cls._insert_marker(name, meta.tag, "[CAST]")
        if not has_latino and not has_castilian:
            return cls._insert_marker(name, meta.tag, "[SUBS]")
        return name

    @classmethod
    def _spanish_audio_presence(cls, meta: Meta) -> tuple[bool, bool]:
        latino = False
        castilian = False
        for track in cls._audio_tracks(meta):
            kind = cls._spanish_audio_kind(track)
            latino = latino or kind == "latino"
            castilian = castilian or kind == "castilian"
        return latino, castilian

    @classmethod
    def _audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        values = cls._media_track_values(meta)
        return [track for track in values[2:] if track.get("@type") == "Audio"]

    @classmethod
    def _media_track_values(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in value
            if isinstance(track, dict)
        ]

    @classmethod
    def _spanish_audio_kind(cls, track: dict[str, Any]) -> str:
        language = str(track.get("Language", "")).lower()
        title = str(track.get("Title", "")).lower()
        if "commentary" in title:
            return ""
        if cls._is_latino_audio(language, title):
            return "latino"
        if cls._is_castilian_audio(language, title):
            return "castilian"
        return ""

    @staticmethod
    def _is_latino_audio(language: str, title: str) -> bool:
        codes = {
            "es-419",
            "es-mx",
            "es-ar",
            "es-cl",
            "es-ve",
            "es-bo",
            "es-co",
            "es-cr",
            "es-do",
            "es-ec",
            "es-sv",
            "es-gt",
            "es-hn",
            "es-ni",
            "es-pa",
            "es-py",
            "es-pe",
            "es-pr",
            "es-uy",
        }
        return language in codes or (
            language == "es"
            and any(marker in title for marker in ("latino", "latin america"))
        )

    @staticmethod
    def _is_castilian_audio(language: str, title: str) -> bool:
        return (language == "es" and "castellano" in title) or language in {
            "es",
            "es-es",
        }

    @staticmethod
    def _insert_marker(name: str, tag: str | None, marker: str) -> str:
        tag_value = str(tag or "")
        if not tag_value:
            return f"{name} {marker}"
        return name.replace(tag_value, f" {marker}{tag_value}")

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.category == "BOOK":
            return True
        spanish_languages = ["spanish", "spanish (latin america)"]
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=spanish_languages,
            check_audio=True,
            check_subtitle=True,
        )

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

        return data
