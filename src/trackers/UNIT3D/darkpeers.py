# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx

from src.book_extractors import validate_isbn_checksum
from src.console import logger
from src.get_desc import DescriptionBuilder
from src.languages import languages_manager
from src.meta import Meta
from src.music.models import MusicRelease
from src.music.validation import MusicValidator, ValidationLevel
from src.tmdb import TmdbManager
from src.trackers.UNIT3D import UNIT3D
from src.type_utils import to_int


class DarkPeers(UNIT3D):
    """
    Darkpeers is a Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    tracker = "DARKPEERS"
    display_name = "DarkPeers"
    allows_bloated_audio = True
    prefers_repack = True
    reject_episode_if_season_pack_exists = True
    _AUDIO_TRACK_PATTERN = re.compile(
        r"^(?:\d{1,3}(?:-\d{1,2})?\.\s+.+|\d{1,3}(?:-\d{1,2})?\s+-\s+.+|\d{1,3}(?:-\d{1,2})?-(?!-).+|.+-\d{1,3}(?:-\d{1,2})?-(?!-).+)$"
    )
    _AUDIO_CODEC_PATTERN = re.compile(r"\s+(?:DTS Headphone:X|DTS-HD MA|DTS-HD HRA|DTS-ES|DTS:X|TrueHD|DD\+ EX|DD EX|DD\+|DD|LPCM|FLAC|ALAC|AAC|Opus|MP3|MP2|Vorbis)(?=\s|$)")
    _DUB_ELEMENT_PATTERN = re.compile(
        r"\s+(?:(?:[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,2}\s+)?(?:MULTi|Dubbed)|Dual-Audio)"
        r"(?=\s+(?:DTS Headphone:X|DTS-HD MA|DTS-HD HRA|DTS-ES|DTS:X|TrueHD|DD\+ EX|DD EX|DD\+|DD|LPCM|FLAC|ALAC|AAC|Opus|MP3|MP2|Vorbis)(?:\s|$)|-[A-Za-z0-9]+$)"
    )
    base_url = "https://darkpeers.org"
    banned_groups = (
        "ARCADE",
        "aXXo",
        "BANDOLEROS",
        "BONE",
        "BRrip",
        "CM8",
        "CrEwSaDe",
        "CTFOH",
        "dAV1nci",
        "DNL",
        "eranger2",
        "FaNGDiNG0",
        "FGT",
        "FiSTER",
        "flower",
        "GalaxyTV",
        "Goki",
        "H4XO",
        "HD2DVD",
        "HDTime",
        "HorribleSubs",
        "iHYTECH",
        "ION10",
        "iPlanet",
        "KiNGDOM",
        "LAMA",
        "MeGusta",
        "mHD",
        "mSD",
        "NaNi",
        "NhaNc3",
        "nHD",
        "nikt0",
        "nSD",
        "OFT",
        "PiTBULL",
        "PRODJi",
        "PSA",
        "RARBG",
        "Rifftrax",
        "ROCKETRACCOON",
        "SANTi",
        "SARTRE",
        "SasukeducK",
        "SEEDSTER",
        "ShAaNiG",
        "Sicario",
        "STUTTERSHIT",
        "Subsplease",
        "SyncUp",
        "TAoE",
        "TGALAXY",
        "TGx",
        "TORRENTGALAXY",
        "ToVaR",
        "Trix",
        "TSP",
        "TSPxL",
        "ViSION",
        "VXT",
        "WAF",
        "WKS",
        "X0r",
        "YIFY",
        "YTS",
    )
    id_url = f"{base_url}/api/torrents/"
    upload_url = f"{base_url}/api/torrents/upload"
    requests_url = f"{base_url}/api/requests/filter"
    search_url = f"{base_url}/api/torrents/filter"
    torrent_url = f"{base_url}/torrents/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("https://darkpeers.org",)

    def __init__(self, config: dict[str, Any]):
        super().__init__(config, tracker_name="DARKPEERS")
        self.config = config
        self.tmdb_manager = TmdbManager(config)

    async def get_description(self, meta: Meta) -> dict[str, str]:
        audio_spectrogram = str(meta.category or "").strip().upper() == "MUSIC"
        description = await DescriptionBuilder(self.tracker, self.config).unit3d_edit_desc(meta, audio_spectrogram=audio_spectrogram)
        return {"description": description}

    async def get_additional_checks(self, meta: Meta) -> bool:
        group = str(meta.tag or "").lstrip("-").strip().upper()
        release_type = str(meta.type or "").strip().upper()
        category = str(meta.category or "").strip().upper()
        filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid. Skipping upload.[/bold red]")
            return False

        if category in {"MOVIE", "TV"}:
            if self._is_local_path_name(str(meta.name or "")):
                logger.info(f"{self.tracker}: [bold red]Generated upload title contains a local file path. Skipping upload.[/bold red]")
                return False
            if not await self.validate_video_languages(meta):
                return False
            if not await self.validate_video_resolution(meta):
                return False
            has_payload = bool([value for value in (meta.filelist or []) if str(value).strip()])
            if has_payload or category == "MOVIE":
                if not await self.validate_video_quality(meta):
                    return False
                if not meta.is_disc:
                    if not self.validate_video_files(meta):
                        return False
                    if not self.validate_video_content(meta):
                        return False
            if not self.validate_video_screenshots(meta):
                return False
            if (
                meta.keep_folder
                and (category == "MOVIE" or not self._is_single_tv_season(meta))
                and not await self._confirm_or_skip("does not allow an individual video file in an unnecessary folder.", meta)
            ):
                return False
        if category == "TV" and not self.validate_tv_scope(meta):
            return False

        if category == "BOOK" and not await self.validate_book(meta):
            return False
        if category == "MUSIC" and not self.validate_music(meta):
            return False

        if category == "GAME" and not await self.validate_game(meta):
            return False

        if group == "EVO" and release_type != "WEBDL":
            logger.info(f"{self.tracker}: [bold red]only allows EVO releases when they are WEB-DLs. Skipping upload.")
            return False

        if group == "HDT" and release_type != "REMUX":
            logger.info(f"{self.tracker}: [bold red]only allows HDT releases when they are Remuxes. Skipping upload.")
            return False

        if category in {"MOVIE", "TV"} and meta.hardcoded_subs:
            logger.info(f"{self.tracker}: [bold red]does not allow Movies or TV releases with hardcoded subtitles. Skipping upload.")
            return False

        return True

    _NORDIC_LANGUAGES: ClassVar[set[str]] = {
        "danish",
        "finnish",
        "icelandic",
        "norwegian bokmal",
        "norwegian nynorsk",
        "norwegian",
        "swedish",
    }
    _LANGUAGE_ALIASES: ClassVar[dict[str, str]] = {
        "da": "danish",
        "dan": "danish",
        "de": "german",
        "deu": "german",
        "en": "english",
        "eng": "english",
        "es": "spanish",
        "fi": "finnish",
        "fin": "finnish",
        "fr": "french",
        "fra": "french",
        "fre": "french",
        "ger": "german",
        "ice": "icelandic",
        "is": "icelandic",
        "isl": "icelandic",
        "ja": "japanese",
        "jpn": "japanese",
        "no": "norwegian",
        "nor": "norwegian",
        "por": "portuguese",
        "pt": "portuguese",
        "spa": "spanish",
        "sv": "swedish",
        "swe": "swedish",
    }
    _BOOK_FORMATS: ClassVar[set[str]] = {
        "AZW3",
        "CBR",
        "CBZ",
        "CHM",
        "DJVU",
        "DOC",
        "DOCX",
        "EPUB",
        "FB2",
        "HTML",
        "KFX",
        "LIT",
        "MOBI",
        "PDB",
        "PDF",
        "RTF",
        "TXT",
    }
    _AUDIOBOOK_FORMATS: ClassVar[set[str]] = {
        "AAC",
        "ALAC",
        "FLAC",
        "M4B",
        "MP3",
        "OPUS",
        "PCM",
        "VORBIS",
    }
    _VIDEO_EXTENSIONS: ClassVar[set[str]] = {
        ".3gp",
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
    _WEB_DL_MIN_VIDEO_BITRATE_KBPS: ClassVar[dict[str, int]] = {
        "4320p": 35_000,
        "2160p": 18_000,
        "1080p": 2_500,
        "1080i": 2_500,
        "720p": 1_800,
    }
    _WEB_DL_MIN_AUDIO_BITRATE_KBPS: ClassVar[dict[str, int]] = {
        "default": 128,
        "4320p": 192,
        "2160p": 192,
        "1080p": 128,
        "720p": 96,
    }

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _config_min_bitrate(self, key: str) -> dict[str, int]:
        raw = self.tracker_config.get(key, {})
        if not isinstance(raw, dict):
            return {}
        values: dict[str, int] = {}
        for key_name, raw_value in raw.items():
            parsed = self._to_int(raw_value)
            if parsed is None or parsed < 0 or not isinstance(key_name, str):
                continue
            values[key_name.lower()] = parsed
        return values

    def _min_webl_bitrate(self, bitrate_type: str, resolution: str) -> int | None:
        resolution_key = str(resolution or "").lower()
        base = self._WEB_DL_MIN_VIDEO_BITRATE_KBPS if bitrate_type == "video" else self._WEB_DL_MIN_AUDIO_BITRATE_KBPS
        default_value = base.get("default")
        overrides = self._config_min_bitrate(f"webl_min_{bitrate_type}_kbps")

        for source in (overrides, base):
            if resolution_key in source:
                return source[resolution_key]
            if bitrate_type == "audio" and "default" in source:
                default_value = source["default"]

        return default_value

    async def validate_video_quality(self, meta: Meta) -> bool:
        if str(meta.type or "").upper() != "WEBDL":
            return True

        resolution = str(meta.resolution or "").lower()
        min_video_kbps = self._min_webl_bitrate("video", resolution)
        min_audio_kbps = self._min_webl_bitrate("audio", resolution)

        if min_video_kbps is None and min_audio_kbps is None:
            return True

        if min_video_kbps is not None:
            video_bitrate = self._to_int(meta.video_bitrate)
            if video_bitrate is None:
                logger.info(f"{self.tracker}: [bold red]Could not determine video bitrate for this WEBDL upload.")
                return False
            if video_bitrate < min_video_kbps:
                logger.info(
                    f"{self.tracker}: [bold red]Video bitrate too low for DARKPEERS WEBDL ({video_bitrate} < {min_video_kbps} kbps). Skipping upload."
                )
                return False

        audio_bitrate = self._to_int(meta.audio_bitrate)
        if min_audio_kbps is not None and audio_bitrate is not None and audio_bitrate < min_audio_kbps:
            logger.info(
                f"{self.tracker}: [bold red]Audio bitrate too low for DARKPEERS WEBDL ({audio_bitrate} < {min_audio_kbps} kbps). Skipping upload."
            )
            return False

        return True

    @classmethod
    def _normalise_language(cls, value: Any) -> str:
        language = re.sub(r"\s+", " ", str(value or "").strip().casefold())
        language = re.sub(r"\s*\([^)]*\)", "", language).strip()
        language = language.split("-", maxsplit=1)[0]
        language = language.replace("_", "-").split("-", maxsplit=1)[0].strip()
        return cls._LANGUAGE_ALIASES.get(language, language)

    @classmethod
    def _languages(cls, value: list[str] | str | None) -> set[str]:
        values = [value] if isinstance(value, str) else (value or [])
        return {norm for item in values if (norm := cls._normalise_language(item))}

    @classmethod
    def _accepted_languages(cls) -> set[str]:
        return {"english", *cls._NORDIC_LANGUAGES}

    async def validate_video_languages(self, meta: Meta) -> bool:
        """Apply DP's audio/original-audio-and-subtitles rule, not the generic OR helper."""
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        audio = self._languages(meta.audio_languages)
        subtitles = self._languages(meta.subtitle_languages)
        original = self._normalise_language(meta.original_language)
        accepted = self._accepted_languages()
        valid = bool(audio & accepted) or (bool(original) and original in audio and bool(subtitles & accepted))
        if not valid:
            logger.info(f"{self.tracker}: [bold red]requires English/Nordic audio, or original audio with English/Nordic subtitles. Skipping upload.")
        return valid

    async def validate_video_resolution(self, meta: Meta) -> bool:
        resolution = str(meta.resolution or "")
        allowed = {"480i", "480p", "576i", "576p", "720p", "1080i", "1080p", "2160p", "4320p"}
        if resolution in allowed:
            return True
        if resolution == "360p":
            return await self._confirm_or_skip("only permits 360p when no official higher-resolution release exists.", meta)
        logger.info(f"{self.tracker}: [bold red]does not support {resolution or 'an unknown'} video resolution. Skipping upload.")
        return False

    def validate_video_files(self, meta: Meta) -> bool:
        filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid. Skipping upload.[/bold red]")
            return False
        archive = next((Path(str(item)).name for item in filelist if Path(str(item)).suffix.lower() in {".rar", ".zip", ".7z"}), "")
        if archive:
            logger.info(f"{self.tracker}: [bold red]does not permit archives in Movie/TV uploads: {archive}. Skipping upload.")
            return False
        group = str(meta.tag or "").lstrip("-").strip().casefold()
        renamed = next(
            (
                Path(str(item)).name
                for item in filelist
                if group
                and Path(str(item)).suffix.casefold() in self._VIDEO_EXTENSIONS
                and any(char.isspace() for char in Path(str(item)).stem)
                and Path(str(item)).stem.casefold().endswith(f"-{group}")
            ),
            "",
        )
        if renamed:
            logger.info(
                f"{self.tracker}: [bold red]Tagged release file appears to have been renamed with spaces: {renamed}. "
                "Restore the original filename before uploading.[/bold red]"
            )
            return False
        return True

    @staticmethod
    def _is_local_path_name(value: str) -> bool:
        return bool(value) and (value.startswith(("/", "\\")) or Path(value).is_absolute() or bool(re.match(r"^[A-Za-z]:[\\/]", value)))

    def validate_video_content(self, meta: Meta) -> bool:
        paths = [Path(str(item)) for item in (meta.filelist or []) if str(item).strip()]
        if not paths:
            logger.info(f"{self.tracker}: [bold red]Movie/TV uploads require a payload of files. Skipping upload.")
            return False

        valid_video_files: list[str] = []
        invalid_files: list[str] = []
        for path in paths:
            suffix = path.suffix.lower()
            if not suffix:
                invalid_files.append(path.name or str(path))
            elif suffix in self._VIDEO_EXTENSIONS:
                valid_video_files.append(path.name)
            else:
                invalid_files.append(path.name)

        if not valid_video_files:
            logger.info(f"{self.tracker}: [bold red]Movie/TV uploads did not include a recognized video file extension. Skipping upload.")
            return False
        if invalid_files:
            logger.info(
                f"{self.tracker}: [bold red]Movie/TV uploads should include video files only. Remove non-video files: {', '.join(invalid_files)}. Skipping upload."
            )
            return False
        return True

    def validate_video_screenshots(self, meta: Meta) -> bool:
        screenshot_count = to_int(meta.screens, 0)
        if screenshot_count < 3:
            logger.info(f"{self.tracker}: [bold red]requires at least 3 screenshots for Movie/TV uploads. Skipping upload.")
            return False
        if screenshot_count > 5:
            logger.info(f"{self.tracker}: [bold red]supports at most 5 screenshots for Movie/TV uploads. Skipping upload.")
            return False
        return True

    def validate_tv_scope(self, meta: Meta) -> bool:
        name = " ".join((str(meta.name or ""), Path(str(meta.path or "")).name)).casefold()
        if re.search(r"\b(?:complete[ ._-]*series|all[ ._-]*seasons?|seasons?[ ._-]*\d+[ ._-]*(?:-|to)[ ._-]*\d+|s\d{1,2}[ ._-]*-[ ._-]*s?\d{1,2})\b", name):
            logger.info(f"{self.tracker}: [bold red]only individual seasons or episodes are allowed. Skipping multi-season/complete-series upload.")
            return False
        seasons = {match.casefold() for item in meta.filelist or [] for match in re.findall(r"\bS(\d{1,2})(?:E\d{1,3})?\b", Path(str(item)).name, re.IGNORECASE)}
        if len(seasons) > 1:
            logger.info(f"{self.tracker}: [bold red]torrent contains files from multiple seasons. Skipping upload.")
            return False
        return True

    def _is_single_tv_season(self, meta: Meta) -> bool:
        if meta.episode:
            return False
        seasons = {match for item in meta.filelist or [] for match in re.findall(r"\bS(\d{1,2})(?:E\d{1,3})?\b", Path(str(item)).name, re.IGNORECASE)}
        return len(seasons) == 1 or bool(meta.season)

    async def validate_book(self, meta: Meta) -> bool:
        author = str(meta.author or meta.book_author or "").strip()
        if not author:
            return await self._missing_required("author", meta)
        if not str(meta.title or meta.book_title or "").strip():
            return await self._missing_required("title", meta)
        if not meta.year:
            return await self._missing_required("release year", meta)
        format_name = self._book_format(meta)
        allowed = self._AUDIOBOOK_FORMATS if meta.audiobook else self._BOOK_FORMATS
        if format_name not in allowed:
            logger.info(f"{self.tracker}: [bold red]does not support {format_name or 'an unspecified'} book format. Skipping upload.")
            return False
        collection = self._is_book_collection(meta)
        if meta.audiobook:
            validated_isbn = validate_isbn_checksum(str(meta.isbn or meta.book_isbn or ""))
            if not validated_isbn and not collection:
                logger.info(f"{self.tracker}: [bold red]Audiobooks require a valid ISBN-10 or ISBN-13. Re-run with --isbn. Skipping upload.[/bold red]")
                return False
            meta.isbn = validated_isbn or ""
        elif not collection:
            validated_isbn = validate_isbn_checksum(str(meta.isbn or meta.book_isbn or ""))
            if not validated_isbn:
                logger.info(f"{self.tracker}: [bold red]Individual eBooks require a valid ISBN-10 or ISBN-13 in the upload title. Re-run with --isbn. Skipping upload.[/bold red]")
                return False
            meta.isbn = validated_isbn
        identifier = self._book_identifier(meta)
        if not identifier and not collection:
            return await self._missing_required("a valid ISBN", meta)
        publisher = str(meta.publisher or meta.book_publisher or "").strip()
        if not publisher:
            return await self._missing_required("publisher", meta)
        if meta.audiobook and not str(meta.narrator or "").strip():
            return await self._missing_required("audiobook narrator", meta)
        if meta.audiobook and not meta.audiobook_duration:
            return await self._missing_required("audiobook runtime", meta)
        if meta.audiobook and format_name in {"MP3", "AAC", "OPUS", "VORBIS"}:
            if not meta.audiobook_bitrate:
                return await self._missing_required("lossy audiobook bitrate", meta)
            if int(meta.audiobook_bitrate) < 64:
                logger.info(f"{self.tracker}: [bold red]Speech-only audiobooks require a bitrate of at least 64 kbps. Skipping upload.[/bold red]")
                return False
        if meta.audiobook:
            details = (
                f"Narrator: {meta.narrator}; Runtime: {meta.audiobook_duration_formatted or meta.audiobook_duration}; "
                f"Publisher: {publisher}; Year: {meta.year}; ISBN: {identifier}"
            )
            if meta.unattended:
                logger.info(
                    f"{self.tracker}: [bold red]Audiobook edition metadata cannot be verified safely in unattended mode. "
                    f"Run attended and verify that narrator/runtime match publisher, year, and ISBN. {details}[/bold red]"
                )
                return False
            logger.info(f"{self.tracker}: [yellow]Verify that all audiobook edition fields describe the same recording. {details}[/yellow]")
            if not await self.common.prompt_user_for_confirmation("Do these audiobook edition details match the files?", meta):
                return False
        if not meta.audiobook and format_name == "PDF" and not bool(meta.get("page_count", None) or meta.get("book_page_count", None)):
            return await self._missing_required("PDF page count", meta)
        if not meta.audiobook:
            source = str(meta.manual_source or meta.source or "").strip().upper()
            if source not in {"RETAIL", "SCAN", "OTHER"}:
                logger.info(
                    f"{self.tracker}: [bold red]eBook provenance must be explicit. Re-run with --source RETAIL for an untouched digital retail file, "
                    "--source SCAN (and --ocr when applicable), or --source OTHER for a verified non-retail born-digital file. "
                    "Generic WEB metadata is not proof of a retail release. Skipping upload.[/bold red]"
                )
                return False
            if meta.ocr and source != "SCAN":
                logger.info(f"{self.tracker}: [bold red]OCR cannot be combined with Retail. Use --source SCAN --ocr. Skipping upload.[/bold red]")
                return False
        return self._validate_book_file_layout(meta, format_name)

    @staticmethod
    def _is_book_collection(meta: Meta) -> bool:
        files = [Path(str(item)) for item in meta.filelist or []]
        book_files = [item for item in files if item.suffix.upper().lstrip(".") in DarkPeers._BOOK_FORMATS | DarkPeers._AUDIOBOOK_FORMATS]
        label = " ".join((str(meta.title or ""), str(meta.name or ""), Path(str(meta.path or "")).name))
        marked = bool(re.search(r"\b(?:collection|complete|books?\s+\d+\s*-\s*\d+|series\s+pack)\b", label, re.IGNORECASE))
        return marked and len(book_files) >= 5

    def _validate_book_file_layout(self, meta: Meta, format_name: str) -> bool:
        source_path = Path(str(meta.path or ""))
        if meta.audiobook and source_path.exists() and source_path.is_file():
            logger.info(f"{self.tracker}: [bold red]Audiobooks must be uploaded inside a single descriptive folder. Skipping upload.[/bold red]")
            return False

        files = [Path(str(item)) for item in meta.filelist or []]
        if meta.audiobook:
            audio_files = [item for item in files if item.suffix.upper().lstrip(".") in self._AUDIOBOOK_FORMATS]
            if format_name == "M4B" and len(audio_files) == 1:
                expected = self._normalized_book_filename(f"{meta.author} {meta.title} {meta.year}")
                actual = self._normalized_book_filename(audio_files[0].stem)
                if actual != expected:
                    logger.info(
                        f"{self.tracker}: [bold red]single-file M4B must be named "
                        f"'Author - Title - Year.m4b': {audio_files[0].name}. Skipping upload.[/bold red]"
                    )
                    return False
            if len(audio_files) > 1:
                invalid = next((item.name for item in audio_files if not re.match(r"^(?:\d{1,3}|chapter\s*\d+|(?:disc|part)\s*\d+)", item.stem, re.IGNORECASE)), "")
                if invalid:
                    logger.info(f"{self.tracker}: [bold red]Audiobook files must use logical chapter, disc, or part numbering: {invalid}. Skipping upload.[/bold red]")
                    return False
            return True

        if source_path.exists():
            author_tokens = set(re.findall(r"[a-z0-9]+", str(meta.author or meta.book_author or "").casefold()))
            title_tokens = set(re.findall(r"[a-z0-9]+", str(meta.title or meta.book_title or "").casefold()))
            ebook_files = [item for item in files if item.suffix.upper().lstrip(".") in self._BOOK_FORMATS]
            invalid = next(
                (
                    item.name
                    for item in ebook_files
                    if not author_tokens.issubset(set(re.findall(r"[a-z0-9]+", item.stem.casefold())))
                    or not title_tokens.issubset(set(re.findall(r"[a-z0-9]+", item.stem.casefold())))
                ),
                "",
            )
            if invalid:
                logger.info(f"{self.tracker}: [bold red]eBook filename must include the author and title: {invalid}. Skipping upload.[/bold red]")
                return False
        return True

    @staticmethod
    def _normalized_book_filename(value: str) -> str:
        return " ".join(re.findall(r"[\w]+", value.casefold()))

    @staticmethod
    def _book_format(meta: Meta) -> str:
        """Resolve container aliases from MediaInfo when the codec is available."""
        format_name = str(meta.type or meta.format or "").upper().strip()
        if format_name == "HTM":
            return "HTML"
        if not meta.audiobook or format_name not in {"M4A", "OGG", "WAV"}:
            return format_name
        media = (meta.mediainfo if isinstance(meta.mediainfo, dict) else {}).get("media")
        raw_tracks = (media if isinstance(media, dict) else {}).get("track")
        tracks = raw_tracks if isinstance(raw_tracks, list) else []
        audio_text = " ".join(
            str(track.get("Format") or track.get("format") or track.get("CodecID") or track.get("codec") or "")
            for track_value in tracks
            if (track := track_value if isinstance(track_value, dict) else {}) and str(track.get("@type") or track.get("type") or "").casefold() == "audio"
        ).casefold()
        if format_name == "M4A":
            return "ALAC" if "alac" in audio_text else "AAC" if "aac" in audio_text else format_name
        if format_name == "OGG":
            return "OPUS" if "opus" in audio_text else "VORBIS" if "vorbis" in audio_text else format_name
        return "PCM" if "pcm" in audio_text else format_name

    @staticmethod
    def _book_identifier(meta: Meta) -> str:
        isbn = str(meta.isbn or meta.book_isbn or "").strip()
        if isbn:
            cleaned = re.sub(r"[^0-9Xx]", "", isbn)
            return cleaned[:-1] + cleaned[-1:].upper() if cleaned else ""
        asin = str(meta.asin or meta.book_asin or "").strip()
        return re.sub(r"[^0-9A-Za-z]", "", asin).upper()

    def validate_music(self, meta: Meta) -> bool:
        release_data = meta.music_release if isinstance(meta.music_release, dict) else {}
        release = MusicRelease.from_dict(release_data) if release_data else MusicRelease(root=str(meta.path or ""))
        errors = [issue.message for issue in MusicValidator().validate(release) if issue.level == ValidationLevel.ERROR]
        if errors:
            logger.info(f"{self.tracker}: [bold red]{' '.join(errors)} Skipping upload.")
            return False
        audio_suffixes = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".alac"}
        paths = [track.relative_path for track in release.tracks] or [str(item) for item in meta.filelist or [] if Path(str(item)).suffix.lower() in audio_suffixes]
        album = str(release.get("album", meta.title or "")).strip()
        root = Path(str(meta.path or ""))
        if root.exists() and not root.is_dir():
            logger.info(f"{self.tracker}: [bold red]Music uploads must be contained in a descriptive folder. Skipping upload.[/bold red]")
            return False
        if root.is_dir() and album and re.sub(r"\W+", " ", album.casefold()).strip() not in re.sub(r"\W+", " ", root.name.casefold()).strip():
            logger.info(f"{self.tracker}: [bold red]Music folder name must include the album title. Skipping upload.[/bold red]")
            return False
        release_type = str(release.get("release_type", "")).casefold()
        unnumbered_single = len(paths) == 1 and release_type == "single"
        for path in paths:
            relative = str(path).replace("\\", "/")
            filename = Path(relative).name
            full_relative = "/".join(part for part in (root.name if root.is_dir() else "", relative) if part)
            if len(full_relative) > 180 or filename.startswith(" ") or any(part.startswith(" ") for part in relative.split("/")):
                logger.info(f"{self.tracker}: [bold red]invalid music path: {relative}. Skipping upload.")
                return False
            if any(char in filename for char in ':?<>|*"'):
                logger.info(f"{self.tracker}: [bold red]music filename contains prohibited filesystem characters: {filename}. Skipping upload.")
                return False
            if re.search(r"\b\w+\.\w+\.\d{1,2}\.\w+", filename) or (not unnumbered_single and not self._AUDIO_TRACK_PATTERN.match(Path(filename).stem)):
                logger.info(f"{self.tracker}: [bold red]music filename must include a track number and title: {filename}. Skipping upload.")
                return False
        return True

    async def validate_game(self, meta: Meta) -> bool:
        files = [Path(str(item)) for item in meta.filelist or []]
        rar_files = [item for item in files if item.suffix.lower() == ".rar" or re.search(r"\.r\d{2}$", item.name, re.IGNORECASE)]
        prohibited = next((item.name for item in files if item.suffix.lower() in {".iso", ".zip", ".7z"}), "")
        if prohibited or not rar_files or not meta.scene or not str(meta.scene_nfo_file or "").strip() or str(meta.repack or "").strip():
            logger.info(f"{self.tracker}: [bold red]Games/Apps must be an original RAR'd scene release with its NFO, not a repack or ISO. Skipping upload.")
            return False
        instructions = " ".join(str(value or "") for value in (meta.description, meta.description_file_content, meta.description_link_content, meta.description_nfo_content))
        if not re.search(r"\b(?:install(?:ation)?|setup|usage|instructions?)\b", instructions, re.IGNORECASE):
            return await self._missing_required("installation and usage instructions", meta)
        return True

    async def _missing_required(self, field: str, meta: Meta) -> bool:
        _ = meta
        logger.info(f"{self.tracker}: [bold red]missing required {field}. Skipping upload.[/bold red]")
        return False

    async def _confirm_or_skip(self, message: str, meta: Meta) -> bool:
        logger.info(f"{self.tracker}: [bold red]{message}[/bold red]")
        if meta.unattended:
            return bool(meta.unattended_confirm)
        return await self.common.prompt_user_for_confirmation("Do you want to upload anyway?", meta)

    async def get_additional_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "mod_queue_opt_in": await self.get_flag(meta, "modq"),
        }

    async def get_audio(self, meta: Meta) -> str:
        if not meta.language_checked:
            await languages_manager.process_desc_language(meta, tracker=self.tracker)
        if meta.is_disc:
            return "SKIPPED"

        audio = self._languages(meta.audio_languages)
        original = self._normalise_language(meta.original_language)
        accepted = self._accepted_languages()
        if not audio:
            return "SKIPPED"
        if len(audio) == 1 and original in audio:
            return "SKIPPED"
        if audio == {"english"} and original and original != "english":
            return "Dubbed"
        if len(audio) == 1:
            only = next(iter(audio))
            if original and only != original and only in self._NORDIC_LANGUAGES:
                return f"{only.title()} Dubbed"
            return "SKIPPED"
        if original and original in audio:
            if "english" in audio and len(audio) == 2:
                if original == "english":
                    other = next(iter(audio - {"english"}), "")
                    return f"{other.title()} MULTi" if other else "SKIPPED"
                return "Dual-Audio"
            if len(audio) >= 3:
                return "MULTi"
            other = next(iter(audio - {original}), "")
            return f"{other.title()} MULTi" if other else "SKIPPED"
        if "english" in audio:
            other = audio - {"english"}
            if len(other) == 1:
                return f"{next(iter(other)).title()} MULTi"
            return "MULTi"
        # A Nordic original plus one non-original track follows the same
        # Language MULTi convention; other ambiguous combinations retain the
        # original release name instead of inventing a label.
        other = audio - accepted
        return f"{next(iter(other)).title()} MULTi" if len(other) == 1 and len(audio) == 2 else "SKIPPED"

    async def get_name(self, meta: Meta) -> dict[str, str]:
        if meta.category == "MUSIC":
            name = self._music_name(meta)
            return {"name": self._ensure_group_tag(name, meta.tag, preserve_if_scene=meta.scene)}

        if meta.category == "BOOK":
            name = self._book_name(meta)
            return {"name": self._ensure_group_tag(name, meta.tag, preserve_if_scene=meta.scene)}

        scene_name = str(meta.scene_name or "")
        has_scene_name = scene_name and not self._is_local_path_name(scene_name)
        if has_scene_name:
            if meta.scene:
                return {"name": scene_name}

            normalized_scene_name = self._normalize_scene_name(scene_name)
            return {"name": self._ensure_group_tag(normalized_scene_name, meta.tag)}

        dp_name = str(meta.name or "")
        if str(meta.type or "").strip():
            dp_name = await self._video_name(meta)
        elif meta.category == "TV":
            dp_name = await self._tv_name(meta, dp_name)

        if meta.category in {"TV", "MOVIE"} and not meta.scene:
            year = str(meta.manual_year) if meta.manual_year not in (None, 0) else str(meta.year or "").strip()
            dp_name = self._normalize_aka_year_order(dp_name, meta.title, meta.aka, year)
        audio = await self.get_audio(meta)
        dp_name = self._apply_dub_element(dp_name, audio)

        return {"name": self._ensure_group_tag(dp_name, meta.tag, preserve_if_scene=bool(has_scene_name and meta.scene))}

    @staticmethod
    def _normalize_scene_name(scene_name: str) -> str:
        parts = [part for part in scene_name.replace("_", " ").split(".") if part.strip()]
        return " ".join(part.strip() for part in parts)

    @staticmethod
    def _ensure_group_tag(name: str, tag: str | None, preserve_if_scene: bool = False) -> str:
        if preserve_if_scene:
            return name
        cleaned = str(tag or "").strip()
        if DarkPeers._has_group_in_name(name):
            return name
        if not cleaned:
            return name
        return f"{name}{cleaned}" if cleaned.startswith("-") else f"{name}-{cleaned}"

    @staticmethod
    def _has_group_in_name(name: str) -> bool:
        match = re.search(r"-([A-Za-z][A-Za-z0-9+_-]{1,})$", str(name).strip())
        if not match:
            return False
        token = match.group(1)
        token_lower = token.lower()
        if token_lower in {"h264", "h265", "x264", "x265", "hevc", "avc", "ac3", "eac3", "dd", "dts", "opus", "aac", "mp3", "flac"}:
            return False
        if re.fullmatch(r"[xhx][.-]?\d{3,4}", token_lower):
            return False
        if re.fullmatch(r"\d+p", token_lower):
            return False
        return True

    @staticmethod
    def _normalize_aka_year_order(name: str, title: str, aka: str, year: str) -> str:
        if not name or not title or not aka or not year:
            return name

        title_value = " ".join(str(title).split())
        aka_value = " ".join(str(aka).split())
        if not title_value or not aka_value:
            return name

        year_value = str(year).strip()
        if not year_value:
            return name

        normalized_name = " ".join(name.split())

        if normalized_name.casefold() == name.casefold():
            name = normalized_name

        expected = f"{title_value} {year_value} {aka_value}"
        if re.search(re.escape(expected), name, flags=re.IGNORECASE):
            return re.sub(rf"(?i)\b{re.escape(title_value)}\s+{re.escape(year_value)}\s+{re.escape(aka_value)}\b", f"{title_value} {aka_value} {year_value}", name, count=1)

        return name

    async def _video_name(self, meta: Meta) -> str:
        release_type = str(meta.type or "").upper()
        title = str(meta.title or "").strip()
        aka = "" if meta.no_aka else str(meta.aka or "").strip()
        year = str(meta.manual_year or meta.year or "").strip()
        if meta.category == "TV" and year and not await self._tv_title_needs_year(meta):
            year = ""
        if meta.no_year:
            year = ""

        if meta.manual_date:
            year = ""
            season_episode = str(meta.manual_date)
        else:
            season_episode = "" if meta.no_season else f"{meta.season or ''}{meta.episode or ''}"
        edition = str(meta.manual_edition or meta.edition or "").strip()
        hybrid = "Hybrid" if meta.webdv or re.search(r"\bHybrid\b", edition, re.IGNORECASE) else ""
        edition = re.sub(r"\bHybrid\b", "", edition, flags=re.IGNORECASE).strip()

        ratio = ""
        for pattern, canonical in ((r"\bOpen Matte\b", "Open Matte"), (r"\bIMAX\b", "IMAX"), (r"\bMAR\b", "MAR")):
            if match := re.search(pattern, edition, re.IGNORECASE):
                ratio = canonical
                edition = f"{edition[: match.start()]} {edition[match.end() :]}".strip()
                break

        cut = ""
        cut_patterns = (
            r"Director(?:'|\u2019)s Cut",
            r"Super Duper Cut",
            r"Special Edition",
            r"Extended",
            r"Unrated",
            r"Uncut",
        )
        for pattern in cut_patterns:
            if match := re.search(rf"\b{pattern}\b", edition, re.IGNORECASE):
                cut = match.group(0)
                edition = f"{edition[: match.start()]} {edition[match.end() :]}".strip()
                break
        edition = " ".join(edition.split())

        resolution = "" if str(meta.resolution or "").upper() == "OTHER" else str(meta.resolution or "").strip()
        source = self._video_source(meta, release_type)
        type_name = {"REMUX": "REMUX", "WEBDL": "WEB-DL", "WEBRIP": "WEBRip"}.get(release_type, "")
        full_disc_or_remux = release_type in {"DISC", "REMUX"}
        dvd_sourced = "DVD" in source.upper()
        if dvd_sourced:
            resolution = ""

        context = " ".join((str(meta.name or ""), str(meta.basename_no_ext or ""), Path(str(meta.path or "")).name))
        ds4k = "DS4K" if not full_disc_or_remux and release_type in {"WEBDL", "WEBRIP", "HDTV"} and re.search(r"\bDS4K\b", context, re.IGNORECASE) else ""
        hi10p = "Hi10P" if re.search(r"\bHi10P\b", context, re.IGNORECASE) else ""
        video_codec = str(meta.video_codec if full_disc_or_remux else meta.video_encode or meta.video_codec or "").strip()
        region = str(meta.region or "").strip() if full_disc_or_remux else ""
        three_d = str(meta.three_d or "").strip()
        repack = str(meta.repack or "").strip()
        audio = str(meta.audio or "").strip()
        hdr = str(meta.hdr or "").strip()
        tag = str(meta.tag or "").strip()

        prefix = [title, aka, year, season_episode, cut, ratio, hybrid, repack, resolution]
        if full_disc_or_remux:
            parts = [*prefix, edition, region, three_d, source, type_name, hi10p, hdr, video_codec, audio]
        else:
            parts = [*prefix, ds4k, edition, three_d, source, type_name, audio, hi10p, hdr, video_codec]
        name = " ".join(part for part in parts if part)
        return f"{' '.join(name.split())}{tag}"

    @staticmethod
    def _video_source(meta: Meta, release_type: str) -> str:
        source = str(meta.source or "").strip()
        if release_type in {"WEBDL", "WEBRIP"}:
            return str(meta.service or "").strip() or ("" if source.upper() == "WEB" else source)
        if release_type == "DVDRIP":
            standard = "NTSC" if "NTSC" in source.upper() else "PAL" if "PAL" in source.upper() else ""
            return " ".join(part for part in (standard, "DVDRip") if part)
        if release_type == "DISC":
            if source in {"BluRay", "Blu-ray"}:
                source = "Blu-ray"
                if str(meta.uhd or "").upper() == "UHD":
                    source = "UHD Blu-ray"
            if source in {"PAL DVD", "NTSC DVD"} and str(meta.dvd_size or "").strip():
                source = f"{source}{str(meta.dvd_size).replace('DVD', '')}"
        elif release_type == "REMUX" and source in {"Blu-ray", "BluRay"}:
            source = "UHD BluRay" if str(meta.uhd or "").upper() == "UHD" else "BluRay"
        return source

    @classmethod
    def _apply_dub_element(cls, name: str, audio: str) -> str:
        if audio == "SKIPPED":
            return " ".join(name.split())
        existing = cls._DUB_ELEMENT_PATTERN.search(name)
        replacement = f" {audio}" if audio else ""
        if existing:
            normalized = f"{name[: existing.start()]}{replacement}{name[existing.end() :]}"
            return " ".join(normalized.split())
        if not replacement:
            return " ".join(name.split())
        codec_match = cls._AUDIO_CODEC_PATTERN.search(name)
        if not codec_match:
            return " ".join(name.split())
        normalized = f"{name[: codec_match.start()]}{replacement}{name[codec_match.start() :]}"
        return " ".join(normalized.split())

    async def _tv_name(self, meta: Meta, name: str) -> str:
        title = str(meta.title or "").strip()
        year = str(meta.year or "").strip()
        if year and not await self._tv_title_needs_year(meta):
            name = re.sub(rf"^({re.escape(title)})\s+{re.escape(year)}(?=\s|$)", r"\1", name, count=1, flags=re.IGNORECASE)
        return " ".join(name.split())

    async def _tv_title_needs_year(self, meta: Meta) -> bool:
        title = str(meta.title or "").strip()
        api_key = str(self.config.get("DEFAULT", {}).get("tmdb_api", "")).strip()
        if not title:
            return False
        if not api_key:
            return True
        try:
            logger.info(f"{self.tracker}: Checking if TMDb has multiple shows with the title '{title}'...")
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.themoviedb.org/3/search/tv",
                    params={"api_key": api_key, "query": title, "language": "en-US", "include_adult": "true"},
                )
                response.raise_for_status()
                payload_raw: Any = response.json()
        except httpx.HTTPError, ValueError, TypeError:
            return True

        title_key = " ".join(title.casefold().split())
        current_id = str(meta.tmdb_id or "")
        payload = cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}
        results_raw: Any = payload.get("results", [])
        results = cast(list[Any], results_raw) if isinstance(results_raw, list) else []
        matching_ids: set[str] = set()
        for result_raw in results:
            if not isinstance(result_raw, dict):
                continue
            result = cast(dict[str, Any], result_raw)
            result_id = str(result.get("id", ""))
            names = (result.get("name"), result.get("original_name"))
            if any(" ".join(str(candidate or "").casefold().split()) == title_key for candidate in names):
                matching_ids.add(result_id)
        matching_ids.discard("")
        return bool(matching_ids - {current_id}) if current_id else len(matching_ids) > 1

    @classmethod
    def _release_field(cls, release: dict[str, Any], name: str, default: Any = "") -> Any:
        """Read a value from the serialized music release model."""
        fields = release.get("fields") if isinstance(release, dict) else {}
        value = fields.get(name) if isinstance(fields, dict) else {}
        return value.get("value", default) if isinstance(value, dict) else default

    @classmethod
    def _music_name(cls, meta: Meta) -> str:
        """Format music as ``Artist - Album (Year) - Format`` for DarkPeers."""
        release = meta.music_release if isinstance(meta.music_release, dict) else {}
        artist = str(cls._release_field(release, "artist", meta.artist)).strip()
        album = str(cls._release_field(release, "album", meta.title)).strip()
        year = str(cls._release_field(release, "release_year", cls._release_field(release, "year", meta.year or ""))).strip()
        media = str(cls._release_field(release, "media", meta.source)).strip()
        raw_tracks = release.get("tracks")
        tracks = raw_tracks if isinstance(raw_tracks, list) else []
        first_track = tracks[0] if tracks and isinstance(tracks[0], dict) else {}
        codec = str(first_track.get("codec") or first_track.get("format") or meta.format or meta.type).upper().strip()

        format_parts = [media, codec]
        if codec in {"FLAC", "ALAC", "PCM"}:
            depth = first_track.get("bit_depth") or cls._release_field(release, "nfo_bit_depth")
            rate = first_track.get("sample_rate") or cls._release_field(release, "nfo_sample_rate")
            if depth is not None and rate is not None:
                with suppress(TypeError, ValueError):
                    format_parts.append(f"{int(depth)}-{int(rate) / 1000:g}")
        elif codec in {"MP3", "AAC", "OPUS", "VORBIS"}:
            bitrate = first_track.get("bitrate") or meta.audio_bitrate
            if bitrate is not None:
                with suppress(TypeError, ValueError):
                    b = int(bitrate)
                    bitrate_kbps = b // 1000 if b >= 1000 else b
                    format_parts.append(str(bitrate_kbps))
            bitrate_mode = str(first_track.get("bitrate_mode") or "").upper().strip()
            if bitrate_mode:
                format_parts.append(bitrate_mode)

        format_name = " ".join(part for part in format_parts if part)
        release_type = str(cls._release_field(release, "release_type", "")).strip().casefold()
        if release_type == "single":
            format_name = f"{format_name} Single".strip()
        title = " - ".join(part for part in (artist, album) if part)
        if year:
            title = f"{title} ({year})" if title else f"({year})"
        return f"{title} - {format_name}" if format_name else title

    @staticmethod
    def _book_name(meta: Meta) -> str:
        """Format eBooks and audiobooks according to DarkPeers' book rules."""
        # Publisher is a description field, never a substitute for the author.
        author = str(meta.author or meta.book_author or "").strip()
        title = str(meta.title or "").strip()
        if author:
            title = re.sub(rf"^{re.escape(author)}\s*[-:]+\s*", "", title, flags=re.IGNORECASE).strip()
        year = str(meta.year or "").strip()
        edition = str(meta.manual_edition or meta.edition or "").strip()
        format_name = DarkPeers._book_format(meta)
        identifier = DarkPeers._book_identifier(meta)

        parts = [part for part in (author, "-" if author and title else "", title, year) if part]
        if not meta.audiobook and edition and not re.search(r"\b(?:1st|first)\b", edition, re.IGNORECASE):
            parts.append(edition)
        if format_name:
            parts.append(format_name)

        if meta.audiobook:
            manual_source = str(meta.manual_source or "").strip().upper()
            source_name = {"CD": "CD", "OVERDRIVE": "Overdrive", "HOOPLA": "Hoopla", "WEB": "Web", "OTHER": "Other"}.get(manual_source, "")
            if source_name:
                parts.insert(-1, source_name)
            if format_name in {"MP3", "AAC", "OPUS", "VORBIS"} and meta.audiobook_bitrate:
                parts.append(str(meta.audiobook_bitrate))
            if identifier:
                parts.append(identifier)
            if manual_source == "RETAIL":
                parts.append("Retail")
            base_name = " ".join(parts)
            return base_name

        if identifier:
            parts.append(identifier)
        source = str(meta.manual_source or meta.source or "").upper().strip()
        if source == "RETAIL":
            parts.append("Retail")
        if source == "SCAN":
            parts.append("Scan")
        if meta.ocr:
            parts.append("OCR")
        return " ".join(parts)

    async def get_category_id(self, meta: Meta, category: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "BOOK": "8",
            "GAME": "4",
            "MUSIC": "3",
        }
        if mapping_only:
            return category_id
        if reverse:
            return {v: k for k, v in category_id.items()}
        return {"category_id": category_id.get(category or meta.category, "0")}

    async def get_type_id(self, meta: Meta, type: str = "", reverse: bool = False, mapping_only: bool = False) -> dict[str, str]:
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "DVDRIP": "3",
            "AUDIOBOOK": "15",
            "COMIC": "17",
            "EBOOK": "18",
            "PC": "9",
            "LINUX": "14",
            "MAC": "11",
            "CONSOLE": "10",
            "FLAC": "8",
            "MP3": "7",
        }
        if mapping_only:
            return type_id
        if reverse:
            return {v: k for k, v in type_id.items()}

        meta_type = "" if not meta.type else meta.type.upper()

        # Book
        if meta.category == "BOOK":
            if type:
                t_upper = type.upper()
                if t_upper in ("CBR", "CBZ"):
                    t_upper = "COMIC"
                elif t_upper in (
                    "EPUB",
                    "PDF",
                    "MOBI",
                    "AZW",
                    "AZW3",
                    "KFX",
                    "FB2",
                    "HTML",
                    "HTM",
                    "CHM",
                    "DJVU",
                    "DOC",
                    "DOCX",
                    "LIT",
                    "PDB",
                    "TXT",
                    "RTF",
                ):
                    t_upper = "EBOOK"
                elif t_upper in ("MP3", "M4B", "FLAC", "AAC", "M4A", "OGG", "WAV", "OPUS", "ALAC", "VORBIS", "PCM"):
                    t_upper = "AUDIOBOOK"
                return {"type_id": type_id.get(t_upper, type_id.get(type, "0"))}
            if meta.audiobook:
                meta_type = "AUDIOBOOK"
            elif meta.comic or meta_type in ("CBR", "CBZ"):
                meta_type = "COMIC"
            else:
                meta_type = "EBOOK"

        if meta.category == "GAME":
            meta_type = "CONSOLE" if meta.console_game else meta.platform.upper()

        if meta.category == "MUSIC":
            meta_type = meta.format.upper()

        resolved_id = type_id.get(meta_type, "0")
        return {"type_id": resolved_id}
