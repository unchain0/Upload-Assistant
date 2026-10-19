# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

from src.domain_models.release import Meta
from src.integrations.external_apis.music_sources import DiscogsEnricher
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.UNIT3D import UNIT3D

Config = dict[str, Any]


class LST(UNIT3D):
    """
    LST is an ENGLISH Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "LST"
    display_name = "LST"
    allows_bloated_audio = True
    base_url = "https://lst.gg"
    banned_groups = ()
    banned_url = f"{base_url}/api/bannedReleaseGroups"
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    trumping_url = f"{base_url}/api/reports/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "MUSIC", "XXX")
    tracker_urls = ("https://lst.gg",)

    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name="LST")
        self.config: Config = config
        self.common = Common(config)

    @staticmethod
    def _needs_video_checks(meta: Meta) -> bool:
        return meta.category in {"MOVIE", "TV"}

    def _mediainfo_settings_allowed(self, meta: Meta) -> bool:
        if meta.valid_mi_settings:
            return True
        logger.info(
            f"{self.tracker}: [bold red]No encoding settings in mediainfo, "
            f"skipping {self.tracker} upload.[/bold red]"
        )
        return False

    async def _language_requirements_allowed(self, meta: Meta) -> bool:
        if meta.is_disc in {"BDMV", "DVD"}:
            return True
        return await self.common.check_language_requirements(
            meta,
            self.tracker,
            languages_to_check=["english"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._needs_video_checks(meta):
            return True
        if not self._mediainfo_settings_allowed(meta):
            return False
        return await self._language_requirements_allowed(meta)

    @staticmethod
    def _category_mapping() -> dict[str, str]:
        return {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3",
            "BOOK": "9",
            "XXX": "8",
        }

    @staticmethod
    def _selected_category(meta: Meta, category: str | None) -> str:
        if category is not None and category != "":
            return category
        return meta.category

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
        selected = self._selected_category(meta, category)
        return {"category_id": category_id.get(selected, "0")}

    @staticmethod
    def _type_mapping() -> dict[str, str]:
        return {
            "DISC": "1",
            "REMUX": "2",
            "ENCODE": "3",
            "DVDRIP": "3",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "SDTV": "16",
            "FLAC": "7",
            "ALAC": "8",
            "AC3": "9",
            "AAC": "10",
            "MP3": "11",
            "MAC": "12",
            "WINDOWS": "13",
            "LINUX": "14",
            "OTHER": "15",
        }

    @staticmethod
    def _selected_type(meta: Meta, explicit_type: str | None) -> Any:
        if explicit_type is not None and explicit_type != "":
            return explicit_type
        if meta.category == "MUSIC" and not meta.type:
            return meta.format
        return meta.type

    @staticmethod
    def _normalized_type(value: Any) -> str:
        return str(value or "").upper().strip().lstrip(".")

    @staticmethod
    def _selected_type_id(
        meta: Meta, mapping: dict[str, str], selected: str
    ) -> str:
        if meta.category == "BOOK" and selected not in mapping:
            return "15"
        return mapping.get(selected, "0")

    async def get_type_id(
        self,
        meta: Meta,
        type: str | None = None,
        reverse: bool = False,
        mapping_only: bool = False,
    ) -> dict[str, str]:
        type_id = self._type_mapping()
        if mapping_only:
            return type_id
        if reverse:
            return {value: key for key, value in type_id.items()}
        selected = self._normalized_type(self._selected_type(meta, type))
        return {"type_id": self._selected_type_id(meta, type_id, selected)}

    async def _base_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
            "draft_queue_opt_in": await self.get_flag(meta, "draft"),
        }

    async def _add_edition_data(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        edition_id = await self.get_edition(meta)
        if edition_id is not None:
            data["edition_id"] = edition_id

    @staticmethod
    def _openlibrary_id(meta: Meta) -> str:
        value = meta.openlibrary or meta.openlibrary_id
        return str(value or meta.openlibrary_book_id or "")

    @classmethod
    def _book_additional_data(cls, meta: Meta) -> dict[str, Any]:
        return {
            "book_exists_on_openlibrary": "1",
            "openlibrary_book_id": cls._openlibrary_id(meta),
            "openlibrary_isbn": meta.isbn or "",
            "extra_openlibrary_ids": meta.extra_openlibrary_ids or "",
        }

    @staticmethod
    def _music_release(meta: Meta) -> dict[str, Any]:
        return (
            meta.music_release if isinstance(meta.music_release, dict) else {}
        )

    @classmethod
    def _discogs_references(cls, meta: Meta) -> tuple[Any, Any]:
        release = cls._music_release(meta)
        raw_ids = release.get("external_ids", {})
        external_ids = raw_ids if isinstance(raw_ids, dict) else {}
        release_reference = (
            external_ids.get("discogs_release")
            or meta.music_discogs_release_id
            or meta.music_discogs_id
        )
        master_reference = (
            external_ids.get("discogs_master") or meta.music_discogs_master_id
        )
        return release_reference, master_reference

    @staticmethod
    def _discogs_identifier(
        reference: Any, kind: str
    ) -> tuple[str, str] | None:
        return DiscogsEnricher.parse_reference(str(reference or ""), kind)

    @staticmethod
    def _discogs_id_value(
        identifier: tuple[str, str] | None, kind: str
    ) -> str:
        if identifier is None or identifier[0] != kind:
            return ""
        return identifier[1]

    @classmethod
    def _discogs_additional_data(cls, meta: Meta) -> dict[str, Any]:
        release_reference, master_reference = cls._discogs_references(meta)
        release_id = cls._discogs_identifier(release_reference, "release")
        master_id = cls._discogs_identifier(master_reference, "master")
        data: dict[str, Any] = {
            "discogs": cls._discogs_id_value(release_id, "release"),
            "discogs_master_id": cls._discogs_id_value(master_id, "master"),
            "extra_discogs_master_ids": "",
            "extra_discogs_ids": "",
        }
        if release_id is not None or master_id is not None:
            data["release_exists_on_discogs"] = "1"
        return data

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        data = await self._base_additional_data(meta)
        await self._add_edition_data(meta, data)
        if meta.category == "BOOK":
            data.update(self._book_additional_data(meta))
        if meta.category == "MUSIC" and meta.music_discogs_enabled:
            data.update(self._discogs_additional_data(meta))
        return data

    async def get_edition(self, meta: Meta) -> int | None:
        edition_mapping = {
            "Alternative Cut": 12,
            "Collector's Edition": 1,
            "Director's Cut": 2,
            "Extended Cut": 3,
            "Extended Uncut": 4,
            "Extended Unrated": 5,
            "Limited Edition": 6,
            "Special Edition": 7,
            "Theatrical Cut": 8,
            "Uncut": 9,
            "Unrated": 10,
            "X Cut": 11,
            "Other": 0,  # Default value for "Other"
        }
        edition = meta.edition
        if edition in edition_mapping:
            return edition_mapping[edition]
        return None

    @classmethod
    def _special_name(cls, meta: Meta) -> str | None:
        if meta.category == "MUSIC":
            return cls._music_name(meta)
        if meta.category == "BOOK":
            return cls._book_name(meta)
        return None

    @staticmethod
    def _movie_dvdrip_name(meta: Meta, name: str) -> str:
        value = name.replace(
            f"{meta.source}{meta.video_encode}", str(meta.resolution), 1
        )
        return value.replace(
            str(meta.audio), f"{meta.audio}{meta.video_encode}", 1
        )

    @staticmethod
    def _tv_dvdrip_name(meta: Meta, name: str) -> str:
        value = name.replace(str(meta.source), str(meta.resolution), 1)
        return value.replace(
            str(meta.video_codec), f"{meta.audio} {meta.video_codec}", 1
        )

    @classmethod
    def _dvdrip_name(cls, meta: Meta, name: str) -> str:
        if meta.type != "DVDRIP":
            return name
        if meta.category == "MOVIE":
            return cls._movie_dvdrip_name(meta, name)
        return cls._tv_dvdrip_name(meta, name)

    async def get_name(self, meta: Meta) -> dict[str, str]:
        special = self._special_name(meta)
        if special is not None:
            return {"name": self._append_trump(special, meta)}
        name = self._dvdrip_name(meta, str(meta.name))
        return {"name": self._append_trump(name, meta)}

    @staticmethod
    def _join_title_parts(parts: list[str]) -> str:
        values = [part.strip() for part in parts if str(part or "").strip()]
        return " ".join(" ".join(values).split())

    @staticmethod
    def _normalized_tag(tag: str | None) -> str:
        return str(tag or "").strip().lstrip("-").strip()

    @classmethod
    def _with_tag(cls, parts: list[str], tag: str | None) -> str:
        """Join a LST title and append the release-group tag once."""
        name = cls._join_title_parts(parts)
        normalized_tag = cls._normalized_tag(tag)
        return f"{name}-{normalized_tag}" if normalized_tag else name

    @staticmethod
    def _append_trump(name: str, meta: Meta) -> str:
        return (
            f"{name} - TRUMP" if meta.trump_reason == "exact_match" else name
        )

    @staticmethod
    def _release_field(
        release: dict[str, Any], name: str, default: Any = ""
    ) -> Any:
        """Read a JSON-serialized MusicRelease field without its provenance."""
        fields = release.get("fields", {})
        value = fields.get(name, {}) if isinstance(fields, dict) else {}
        return (
            value.get("value", default) if isinstance(value, dict) else default
        )

    @staticmethod
    def _codec(value: Any) -> str:
        codec = str(value or "").upper().strip()
        aliases = {
            "OGG VORBIS": "VORBIS",
            "OGG": "VORBIS",
            "MPEG AUDIO": "MP3",
            "MPEG-4 AAC": "AAC",
            "M4A": "AAC",
            "M4B": "M4B",
            "MOBI": "KINDLE",
            "AZW": "KINDLE",
            "AZW3": "KINDLE",
            "CBR": "CBA",
            "CBZ": "CBA",
        }
        return aliases.get(codec, codec)

    @staticmethod
    def _source(value: Any) -> str:
        source = str(value or "").strip().casefold()
        aliases = {
            "cd": "CD",
            "hdcd": "HDCD",
            "dts-cd": "DTS-CD",
            "dts cd": "DTS-CD",
            "8-track": "8-Track",
            "8 track": "8-Track",
            "vinyl": "Vinyl",
            "web": "WEB",
            "cassette": "Cassette",
        }
        return aliases.get(source, str(value or "").strip())

    @staticmethod
    def _first_music_track(release: dict[str, Any]) -> dict[str, Any]:
        tracks = release.get("tracks", [])
        if not isinstance(tracks, list) or not tracks:
            return {}
        first = tracks[0]
        return first if isinstance(first, dict) else {}

    @staticmethod
    def _sample_rate_label(rate: Any) -> str:
        match = re.search(r"\d+(?:[.,]\d+)?", str(rate or ""))
        if match is None:
            return ""
        value = float(match.group().replace(",", "."))
        if value >= 1000:
            value /= 1000
        return f"{value:g} kHz"

    @classmethod
    def _lossless_music_details(
        cls,
        release: dict[str, Any],
        track: dict[str, Any],
    ) -> list[str]:
        depth = track.get("bit_depth") or cls._release_field(
            release, "nfo_bit_depth"
        )
        rate = track.get("sample_rate") or cls._release_field(
            release, "nfo_sample_rate"
        )
        details: list[str] = []
        if depth:
            details.append(f"{depth}-bit")
        rate_label = cls._sample_rate_label(rate)
        if rate_label:
            details.append(rate_label)
        return details

    @classmethod
    def _music_name(cls, meta: Meta) -> str:
        """Format music using LST's Discogs-based naming convention."""
        release = cls._music_release(meta)
        artist = cls._release_field(release, "artist", meta.artist)
        title = cls._release_field(release, "album", meta.title)
        year = cls._release_field(
            release,
            "release_year",
            cls._release_field(release, "year", meta.year),
        )
        source = cls._source(cls._release_field(release, "media", meta.source))
        track = cls._first_music_track(release)
        codec = cls._codec(
            track.get("codec")
            or track.get("format")
            or meta.format
            or meta.type
        )
        parts = [str(artist), "-", str(title), str(year), source, codec]
        if codec in {"FLAC", "ALAC"}:
            parts.extend(cls._lossless_music_details(release, track))
        return cls._with_tag(parts, meta.tag)

    @staticmethod
    def _book_identity(meta: Meta) -> tuple[str, str, str]:
        return (
            str(meta.author or meta.publisher or ""),
            str(meta.title or ""),
            str(meta.year or ""),
        )

    @staticmethod
    def _book_media_tracks(meta: Meta) -> list[Any]:
        media = meta.mediainfo.get("media", {})
        if not isinstance(media, dict):
            return []
        tracks = media.get("track", [])
        return tracks if isinstance(tracks, list) else []

    @classmethod
    def _first_audio_track(cls, meta: Meta) -> dict[str, Any]:
        for track in cls._book_media_tracks(meta):
            if not isinstance(track, dict):
                continue
            if track.get("@type") == "Audio":
                return track
        return {}

    @staticmethod
    def _bit_depth_label(depth: Any) -> str:
        match = re.search(r"\d+", str(depth or ""))
        return f"{match.group()}-bit" if match is not None else ""

    @classmethod
    def _audiobook_lossless_details(cls, meta: Meta) -> list[str]:
        audio = cls._first_audio_track(meta)
        depth = audio.get("BitDepth") or audio.get("BitDepth_String")
        rate = audio.get("SamplingRate") or audio.get("SamplingRate_String")
        return [
            value
            for value in (
                cls._bit_depth_label(depth),
                cls._sample_rate_label(rate),
            )
            if value
        ]

    @classmethod
    def _audiobook_name(cls, meta: Meta) -> str:
        author, title, year = cls._book_identity(meta)
        codec = cls._codec(meta.type)
        source = cls._source(meta.source)
        parts = [author, "-", title, year, source, codec]
        if codec in {"FLAC", "ALAC"}:
            parts.extend(cls._audiobook_lossless_details(meta))
        return cls._with_tag(parts, meta.tag)

    @classmethod
    def _ebook_scan_type(cls, meta: Meta) -> str:
        if meta.ocr:
            return "OCR"
        return "SCAN" if cls._source(meta.source).upper() == "SCAN" else ""

    @classmethod
    def _ebook_name(cls, meta: Meta) -> str:
        author, title, year = cls._book_identity(meta)
        edition = str(meta.manual_edition or meta.edition or "")
        format_name = cls._codec(meta.type)
        isbn = re.sub(r"[^0-9Xx]", "", str(meta.isbn or ""))
        parts = [
            author,
            "-",
            title,
            edition,
            year,
            format_name,
            cls._ebook_scan_type(meta),
            isbn,
        ]
        return cls._with_tag(parts, meta.tag)

    @classmethod
    def _book_name(cls, meta: Meta) -> str:
        """Format LST audiobooks and eBooks according to their category rules."""
        return (
            cls._audiobook_name(meta)
            if meta.audiobook
            else cls._ebook_name(meta)
        )
