# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any, cast

import cloudscraper

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.trackers.UNIT3D import UNIT3D


class Emuwarez(UNIT3D):
    """
    eMuwarez is a SPANISH Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    allows_bloated_audio = True
    base_url = "https://emuwarez.com"
    banned_groups = ()
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="EMUWAREZ")
        self.tmdb_manager = TmdbManager(config)

    async def get_name(self, meta: Meta) -> dict[str, str]:
        title = await self._get_title(meta)
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        parts = self._name_parts(meta, title, await self._build_audio_string(meta))
        base_name = re.sub(r"\s{2,}", " ", " ".join(parts)).strip()
        subs_tag = " SUBS" if self._has_spanish_subs(meta) else ""
        return {"name": f"{base_name}{subs_tag}-{self._release_group(meta.tag)}"}

    def _name_parts(self, meta: Meta, title: str, audio: str) -> list[str]:
        values = (
            title,
            self._season_name(meta),
            self._year_name(meta),
            self._map_resolution(meta.resolution),
            self._map_format(meta),
            self._map_codec(meta),
            audio,
        )
        return [value for value in values if value]

    @staticmethod
    def _season_name(meta: Meta) -> str:
        if meta.category != "TV" or not meta.season_int:
            return ""
        return f"S{meta.season_int:02d}"

    @staticmethod
    def _year_name(meta: Meta) -> str:
        return "" if meta.year is None else str(meta.year)

    @staticmethod
    def _release_group(value: Any) -> str:
        tag = str(value or "").strip().lstrip("-")
        invalid = {"", "nogrp", "nogroup", "unknown", "unk", "hd.ma.5.1", "untouched"}
        return "EMUWAREZ" if tag.casefold() in invalid else tag

    async def _get_title(self, meta: Meta) -> str:
        spanish_title = self._imdb_spanish_title(meta)
        if not spanish_title:
            spanish_title = await self._tmdb_spanish_title(meta)
        if self._use_spanish_title(spanish_title):
            return str(spanish_title)
        return meta.title

    def _imdb_spanish_title(self, meta: Meta) -> str:
        imdb_info = cast(dict[str, Any], meta.imdb_info) if isinstance(meta.imdb_info, dict) else {}
        akas = imdb_info.get("akas", [])
        if not isinstance(akas, list):
            return ""
        country_match = self._first_aka_title(akas, self._country_spanish_title)
        return country_match or self._first_aka_title(akas, self._language_spanish_title)

    @staticmethod
    def _first_aka_title(akas: list[Any], selector: Any) -> str:
        return next((title for aka in akas if (title := selector(aka))), "")

    @staticmethod
    def _country_spanish_title(aka: Any) -> str:
        if not isinstance(aka, dict) or aka.get("country") not in {"Spain", "ES"}:
            return ""
        return str(aka.get("title") or "")

    @staticmethod
    def _language_spanish_title(aka: Any) -> str:
        if not isinstance(aka, dict) or aka.get("language") not in {"Spain", "Spanish", "ES"}:
            return ""
        return str(aka.get("title") or "")

    async def _tmdb_spanish_title(self, meta: Meta) -> str:
        tmdb_id = self._numeric_tmdb_id(meta.tmdb)
        if not tmdb_id:
            return ""
        result = await self.tmdb_manager.get_tmdb_translations(tmdb_id=tmdb_id, category=meta.category, target_language="es")
        return result if isinstance(result, str) else ""

    @staticmethod
    def _numeric_tmdb_id(value: Any) -> int:
        text = str(value) if isinstance(value, (int, str)) else ""
        return int(text) if text.isdigit() else 0

    def _use_spanish_title(self, title: str) -> bool:
        return bool(title) and bool(self.config["TRACKERS"][self.tracker].get("use_spanish_title", False))

    def _map_resolution(self, resolution: str) -> str:
        """Map resolution to Emuwarez nomenclature"""
        resolution_map = {
            "4320p": "4320p FUHD",
            "2160p": "2160p UHD",
            "1080p": "1080p",
            "720p": "720p",
            "576p": "576p SD",
            "540p": "540p SD",
            "480p": "480p SD",
        }
        return resolution_map.get(resolution, resolution)

    def _map_format(self, meta: Meta) -> str:
        format_map = {"BDMV": "FBD", "DVD": "FDVD", "REMUX": "BDRemux"}
        disc_or_type = self._disc_or_type_format(meta, format_map)
        if disc_or_type:
            return disc_or_type
        return self._source_format(str(meta.source))

    @staticmethod
    def _disc_or_type_format(meta: Meta, mapping: dict[str, str]) -> str:
        is_disc = meta.is_disc if isinstance(meta.is_disc, str) else ""
        if is_disc in mapping:
            return mapping[is_disc]
        return mapping.get(str(meta.type), "")

    @classmethod
    def _source_format(cls, source: str) -> str:
        if cls._is_bluray_source(source):
            return "BluRay"
        web = cls._web_source_format(source)
        if web:
            return web
        if "HDTV" in source:
            return "HDTV"
        return "SD" if "DVD" in source else ""

    @staticmethod
    def _is_bluray_source(source: str) -> bool:
        return "BluRay" in source or "Blu-ray" in source

    @staticmethod
    def _web_source_format(source: str) -> str:
        if "WEB" not in source:
            return ""
        return "WEB-DL" if "WEB-DL" in source else "WEBRIP"

    def _map_codec(self, meta: Meta) -> str:
        """Map video codec to Emuwarez nomenclature with HDR/DV prefix"""
        codec_map = {
            "H.264": "AVC",
            "H.265": "HEVC",
            "HEVC": "HEVC",
            "AVC": "AVC",
            "x264": "x264",
            "x265": "x265",
            "AV1": "AV1",
            "VP9": "VP9",
            "VP8": "VP8",
            "VC-1": "VC-1",
            "MPEG-4": "MPEG",
        }

        hdr_prefix = ""
        if meta.hdr:
            hdr = meta.hdr
            if "DV" in hdr:
                hdr_prefix = "DV "
            if "HDR" in hdr:
                hdr_prefix += "HDR "

        video_codec = meta.video_codec
        video_encode = meta.video_encode
        codec = codec_map.get(video_codec) or codec_map.get(video_encode, video_codec)

        return f"{hdr_prefix}{codec}".strip()

    async def _get_original_language(self, meta: Meta) -> str | None:
        language = str(meta.original_language or "").strip()
        if not language:
            language = self._imdb_original_language(meta)
        return self._map_language(language) if language else None

    @classmethod
    def _imdb_original_language(cls, meta: Meta) -> str:
        imdb_info = cast(dict[str, Any], meta.imdb_info) if isinstance(meta.imdb_info, dict) else {}
        value = imdb_info.get("language")
        return cls._language_text(cls._first_language_value(value))

    @staticmethod
    def _first_language_value(value: Any) -> Any:
        if isinstance(value, list):
            return value[0] if value else ""
        return value

    @staticmethod
    def _language_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("text", "")).strip()
        return str(value or "").strip()

    async def _build_audio_string(self, meta: Meta) -> str:
        audio_tracks = self._get_audio_tracks(meta)
        audio_langs = self._extract_audio_languages(audio_tracks, meta)
        if not audio_tracks or not audio_langs:
            return ""
        special = await self._special_audio_string(meta, audio_tracks, audio_langs)
        return special or self._listed_audio_string(audio_tracks, audio_langs)

    async def _special_audio_string(self, meta: Meta, tracks: list[dict[str, Any]], languages: list[str]) -> str:
        dual = self._dual_audio_string(tracks)
        if dual:
            return dual
        multi = self._multi_audio_string(tracks)
        if multi:
            return multi
        return await self._single_original_audio_string(meta, tracks, languages)

    def _dual_audio_string(self, tracks: list[dict[str, Any]]) -> str:
        if len(tracks) != 2:
            return ""
        codecs = [self._map_audio_codec(track) for track in tracks]
        if codecs[0] != codecs[1]:
            return ""
        return f"DUAL {codecs[0]} {self._get_audio_channels(tracks[0])}"

    def _multi_audio_string(self, tracks: list[dict[str, Any]]) -> str:
        if len(tracks) < 4:
            return ""
        codecs = [self._map_audio_codec(track) for track in tracks]
        if not self._all_equal(codecs):
            return ""
        return f"MULTI {codecs[0]} {self._get_audio_channels(tracks[0])}"

    @staticmethod
    def _all_equal(values: list[str]) -> bool:
        return bool(values) and all(value == values[0] for value in values)

    async def _single_original_audio_string(self, meta: Meta, tracks: list[dict[str, Any]], languages: list[str]) -> str:
        if len(tracks) != 1:
            return ""
        original = await self._get_original_language(meta)
        if not self._single_original_matches(original, languages):
            return ""
        codec = self._map_audio_codec(tracks[0])
        channels = self._get_audio_channels(tracks[0])
        label = "VOSE" if self._has_spanish_subs(meta) else "V.O."
        return f"{label} {original} {codec} {channels}"

    @staticmethod
    def _single_original_matches(original: str | None, languages: list[str]) -> bool:
        if not original or len(languages) != 1:
            return False
        if "ESP" in languages or "LAT" in languages:
            return False
        return languages[0] == original

    def _listed_audio_string(self, tracks: list[dict[str, Any]], languages: list[str]) -> str:
        parts = [self._audio_part(track, languages[index]) for index, track in enumerate(tracks) if index < len(languages)]
        return " ".join(parts)

    def _audio_part(self, track: dict[str, Any], language: str) -> str:
        return f"{language} {self._map_audio_codec(track)} {self._get_audio_channels(track)}"

    @classmethod
    def _get_audio_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [track for track in cls._media_tracks(meta) if track.get("@type") == "Audio"]

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        tracks = media.get("track", [])
        if not isinstance(tracks, list):
            return []
        return [cast(dict[str, Any], track) for track in tracks if isinstance(track, dict)]

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    def _extract_audio_languages(self, audio_tracks: list[dict[str, Any]], meta: Meta) -> list[str]:
        track_languages = self._mapped_unique_languages(track.get("Language", "") for track in audio_tracks)
        if track_languages:
            return track_languages
        return self._mapped_unique_languages(self._fallback_audio_languages(meta))

    def _mapped_unique_languages(self, values: Any) -> list[str]:
        mapped: list[str] = []
        for value in values:
            code = self._map_language(str(value))
            if code and code not in mapped:
                mapped.append(code)
        return mapped

    @staticmethod
    def _fallback_audio_languages(meta: Meta) -> list[Any]:
        value = meta.audio_languages
        return value if isinstance(value, list) else []

    def _map_language(self, lang: str) -> str:
        """Map language codes and names to Emuwarez nomenclature"""
        if not lang:
            return ""

        lang_map = {
            "spa": "ESP",
            "es": "ESP",
            "spanish": "ESP",
            "español": "ESP",
            "castellano": "ESP",
            "es-es": "ESP",
            "eng": "ING",
            "en": "ING",
            "english": "ING",
            "en-us": "ING",
            "en-gb": "ING",
            "lat": "LAT",
            "latino": "LAT",
            "latin american spanish": "LAT",
            "es-mx": "LAT",
            "es-419": "LAT",
            "fre": "FRA",
            "fra": "FRA",
            "fr": "FRA",
            "french": "FRA",
            "français": "FRA",
            "ger": "ALE",
            "deu": "ALE",
            "de": "ALE",
            "german": "ALE",
            "deutsch": "ALE",
            "jpn": "JAP",
            "ja": "JAP",
            "japanese": "JAP",
            "日本語": "JAP",
            "kor": "COR",
            "ko": "COR",
            "korean": "COR",
            "한국어": "COR",
            "ita": "ITA",
            "it": "ITA",
            "italian": "ITA",
            "italiano": "ITA",
            "por": "POR",
            "pt": "POR",
            "portuguese": "POR",
            "português": "POR",
            "pt-br": "POR",
            "pt-pt": "POR",
            "chi": "CHI",
            "zho": "CHI",
            "zh": "CHI",
            "chinese": "CHI",
            "mandarin": "CHI",
            "中文": "CHI",
            "zh-cn": "CHI",
            "rus": "RUS",
            "ru": "RUS",
            "russian": "RUS",
            "русский": "RUS",
            "ara": "ARA",
            "ar": "ARA",
            "arabic": "ARA",
            "hin": "HIN",
            "hi": "HIN",
            "hindi": "HIN",
            "tha": "THA",
            "th": "THA",
            "thai": "THA",
            "vie": "VIE",
            "vi": "VIE",
            "vietnamese": "VIE",
        }

        lang_lower = lang.lower().strip()
        mapped = lang_map.get(lang_lower)

        if mapped:
            return mapped

        return lang.upper()[:3] if len(lang) >= 3 else lang.upper()

    def _map_audio_codec(self, audio_track: dict[str, Any]) -> str:
        """Map audio codec to Emuwarez nomenclature"""
        codec = str(audio_track.get("Format", "")).upper()

        if "atmos" in str(audio_track.get("Format_AdditionalFeatures", "")).lower():
            return "Atmos"

        codec_map = {
            "AAC LC": "AAC LC",
            "AAC": "AAC",
            "AC-3": "DD",
            "AC3": "DD",
            "E-AC-3": "DD+",
            "EAC3": "DD+",
            "DTS": "DTS",
            "DTS-HD MA": "DTS-HD MA",
            "DTS-HD HRA": "DTS-HD HRA",
            "TRUEHD": "TrueHD",
            "MLP FBA": "MLP",
            "PCM": "PCM",
            "FLAC": "FLAC",
            "OPUS": "OPUS",
            "MP3": "MP3",
        }

        return codec_map.get(codec, codec)

    def _get_audio_channels(self, audio_track: dict[str, Any]) -> str:
        """Get audio channel configuration"""
        channels = audio_track.get("Channels", "")
        channel_map = {
            "1": "Mono",
            "2": "2.0",
            "3": "3.0",
            "4": "3.1",
            "5": "5.0",
            "6": "5.1",
            "8": "7.1",
        }
        return channel_map.get(str(channels), "5.1")

    def _has_spanish_subs(self, meta: Meta) -> bool:
        return any(self._is_spanish_subtitle(track) for track in self._media_tracks(meta))

    @staticmethod
    def _is_spanish_subtitle(track: dict[str, Any]) -> bool:
        if track.get("@type") != "Text":
            return False
        language = str(track.get("Language", "")).casefold()
        title = str(track.get("Title", "")).casefold()
        if language in {"es", "spa", "spanish", "es-es", "español"}:
            return True
        return any(term in title for term in ("spanish", "español", "castellano"))

    async def get_cat_id(self, category_name: str) -> str:
        """Categories: Movies(1), Series(2), Documentales(4), Musica(5), Juegos(6), Software(7)"""
        category_map = {"MOVIE": "1", "TV": "2", "FANRES": "1"}
        return category_map.get(category_name, "1")

    async def get_type_id(self, meta: Meta, type: Any = None, reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        _ = (type, reverse, mapping_only)
        """Types: Full Disc(1), Remux(2), Encode(3), WEB-DL(4), WEBRIP(5), HDTV(6), SD(7)"""
        type_map = {"DISC": "1", "REMUX": "2", "ENCODE": "3", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "SD": "7"}
        meta_type = meta.type
        type_id = type_map.get(str(meta_type), "3")
        return {"type_id": type_id}

    async def get_res_id(self, resolution: str) -> str:
        """Resolutions: 4320p(1), 2160p(2), 1080p(3), 1080i(4), 720p(5), 576p(6), 540p(7), 480p(8), Otras(10)"""
        resolution_map = {"4320p": "1", "2160p": "2", "1080p": "3", "1080i": "4", "720p": "5", "576p": "6", "540p": "7", "480p": "8", "SD": "10", "OTHER": "10"}
        return resolution_map.get(resolution, "10")

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        meta.setdefault("tracker_status", {})
        meta.tracker_status.setdefault(self.tracker, {})
        params = await self._search_params(meta)
        response = self._scraper_response(params)
        response.raise_for_status()
        return self._search_results(response.json(), bool(meta.is_disc))

    async def _search_params(self, meta: Meta) -> list[tuple[str, str]]:
        res_id = await self.get_res_id(meta.resolution)
        params: list[tuple[str, str]] = [
            ("categories[]", await self.get_cat_id(str(meta.category))),
            ("name", self._search_name(meta)),
            ("perPage", "100"),
        ]
        if meta.tmdb is not None:
            params.append(("tmdbId", str(meta.tmdb)))
        params.extend(self._resolution_params(res_id))
        params.append(("types[]", (await self.get_type_id(meta))["type_id"]))
        return params

    @staticmethod
    def _search_name(meta: Meta) -> str:
        return str(meta.season) if meta.category == "TV" and meta.season else ""

    @staticmethod
    def _resolution_params(res_id: str) -> list[tuple[str, str]]:
        if res_id in {"3", "4"}:
            return [("resolutions[]", "3"), ("resolutions[]", "4")]
        return [("resolutions[]", res_id)]

    def _scraper_response(self, params: list[tuple[str, str]]) -> Any:
        api_key = str(self.config["TRACKERS"][self.tracker].get("api_key", "")).strip()
        scraper = cast(Any, cloudscraper).create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False, "desktop": True},
            delay=10,
        )
        scraper.get(self.base_url, timeout=15.0)
        return scraper.get(url=self.search_url, params=params, headers=self._search_headers(api_key), timeout=15.0)

    def _search_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.base_url,
            "Origin": self.base_url,
        }

    @classmethod
    def _search_results(cls, payload: Any, is_disc: bool) -> list[dict[str, Any]]:
        items = cls._search_items(payload)
        return [entry for item in items if (entry := cls._search_result(item, is_disc)) is not None]

    @staticmethod
    def _search_items(payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("data")
        return items if isinstance(items, list) else []

    @classmethod
    def _search_result(cls, torrent: Any, is_disc: bool) -> dict[str, Any] | None:
        attributes = cls._torrent_attributes(torrent)
        if not attributes or "name" not in attributes:
            return None
        result = {
            "name": attributes["name"],
            "size": attributes.get("size"),
            "trumpable": attributes.get("trumpable", False),
            "link": attributes.get("details_link"),
        }
        if not is_disc:
            files = cls._torrent_files(attributes)
            result["files"] = cls._file_names(files)
            result["file_count"] = len(files)
        return result

    @staticmethod
    def _torrent_attributes(torrent: Any) -> dict[str, Any]:
        if not isinstance(torrent, dict):
            return {}
        attributes = torrent.get("attributes")
        return cast(dict[str, Any], attributes) if isinstance(attributes, dict) else {}

    @staticmethod
    def _torrent_files(attributes: dict[str, Any]) -> list[Any]:
        files = attributes.get("files", [])
        return files if isinstance(files, list) else []

    @staticmethod
    def _file_names(files: list[Any]) -> list[str]:
        return [str(file["name"]) for file in files if isinstance(file, dict) and isinstance(file.get("name"), str)]

    async def get_upload_data(self, meta: Meta) -> dict[str, Any]:
        """Get upload data with Emuwarez-specific options"""
        upload_data = await super().get_data(meta)

        if meta.anon:
            upload_data["anonymous"] = "1"
        if meta.stream:
            upload_data["stream"] = "1"
        if meta.resolution in ["576p", "540p", "480p"]:
            upload_data["sd"] = "1"
        if meta.personalrelease:
            upload_data["personal_release"] = "1"

        return upload_data
