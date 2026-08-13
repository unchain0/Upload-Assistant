# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, ClassVar, cast

from src.console import logger
from src.meta import Meta
from src.trackers.NEXUSPHP import NEXUSPHP

Config = dict[str, Any]


class RailgunPT(NEXUSPHP):
    """
    RAILGUNPT is a CHINESE Private Torrent Tracker for MOVIES / TV / MUSIC / GAME.
    """

    banned_groups = ()
    display_name = "RailgunPT"
    base_url = "https://bilibili.download"
    source_flag = "[bilibili.download] RailgunPT"
    torrent_url = f"{base_url}/details.php?id="
    supported_categories = ("TV", "MOVIE", "MUSIC", "GAME")
    tracker_urls = ("https://bilibili.download",)
    allows_bloated_audio = True
    _ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".rar", ".r00", ".r01", ".r02", ".zip", ".7z"})
    _ATTACHMENT_ARCHIVE_MARKERS: tuple[str, ...] = ("sub", "subtitle", "font", "scan", "cover", "patch", "crack")
    _AUDIO_EXTENSIONS: frozenset[str] = frozenset({".aac", ".ac3", ".ape", ".dts", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"})
    _LOSSY_AUDIO_EXTENSIONS: frozenset[str] = frozenset({".aac", ".ac3", ".dts", ".m4a", ".mp3", ".ogg", ".opus", ".wma"})
    _GAME_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".bin", ".chd", ".cso", ".img", ".iso", ".mdf", ".nrg", ".wbfs"})
    _MUSIC_FORMAT_BY_EXTENSION: ClassVar[dict[str, str]] = {
        ".aac": "aac",
        ".ac3": "ac3",
        ".ape": "ape",
        ".dts": "dts",
        ".flac": "flac",
        ".m4a": "aac",
        ".mp3": "mp3",
        ".ogg": "ogg vorbis",
        ".opus": "opus",
        ".wav": "wav",
        ".wma": "wma",
    }
    _BANNED_EXTENSIONS: frozenset[str] = frozenset({".rm", ".rmvb", ".flv", ".torrent", ".url"})
    _VIDEO_EXTENSIONS: frozenset[str] = frozenset({".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpg", ".mpeg", ".rm", ".rmvb", ".ts", ".vob", ".webm"})
    _LOW_QUALITY_MARKERS: tuple[str, ...] = ("cam", "hdcam", "tc", "telesync", "ts", "scr", "dvdscr", "r5", "r5 line", "halfcd")
    _SOURCE_TOKENS: tuple[str, ...] = ("blu-ray", "bluray", "hddvd", "hd dvd", "hdtv", "uhdtv", "dvd", "web-dl", "webdl", "remux", "dsr", "tv")
    _VIDEO_CODEC_TOKENS: tuple[str, ...] = ("avc", "h.264", "h264", "hevc", "h.265", "h265", "mpeg-2", "mpeg2", "vc-1", "vc1", "x264", "x265", "xvid")
    _PACK_SOURCE_TOKENS: tuple[str, ...] = ("bluray", "hddvd", "hdtv", "uhdtv", "dvd", "webdl", "webrip", "remux", "dsr", "tv")
    _PACK_CODEC_TOKENS: tuple[str, ...] = ("x264", "x265", "h264", "h265", "hevc", "avc", "mpeg2", "vc1", "xvid")
    _DISC_TYPES: frozenset[str] = frozenset({"bdmv", "dvd", "hddvd_ts", "video_ts"})
    _GAME_PROHIBITED_MARKERS: tuple[str, ...] = (
        "portable",
        "highly compressed",
        "compressed",
        "repack",
        "re packed",
        "repacked",
        "cracked",
        "keygen",
        "unofficial",
        "third party mod",
    )

    def __init__(self, config: Config) -> None:
        super().__init__(config, "RAILGUNPT")

    async def load_localized_data(self, meta: Meta) -> None:
        if str(meta.category or "").upper() in {"GAME", "MUSIC"}:
            self.tmdb_data = {}
            return
        await super().load_localized_data(meta)

    async def get_technical_info(self, meta: Meta) -> str:
        if str(meta.category or "").upper() in {"GAME", "MUSIC"}:
            return ""
        return await super().get_technical_info(meta)

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        if str(meta.category or "").upper() not in {"GAME", "MUSIC"}:
            return await super().search_existing(meta)

        original_season, original_episode, original_tv_pack = meta.season, meta.episode, meta.tv_pack
        meta.season, meta.episode, meta.tv_pack = "", "", False
        try:
            return await super().search_existing(meta)
        finally:
            meta.season, meta.episode, meta.tv_pack = original_season, original_episode, original_tv_pack

    @staticmethod
    def _normalized_token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @staticmethod
    def _metadata_values(value: Any) -> list[Any]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return []

    @staticmethod
    def _music_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        raw_value = cast(dict[object, Any], value)
        return {str(key): item for key, item in raw_value.items()}

    @classmethod
    def _music_tracks(cls, release: dict[str, Any]) -> list[dict[str, Any]]:
        tracks = release.get("tracks")
        if not isinstance(tracks, list):
            return []
        raw_tracks = cast(list[Any], tracks)
        return [cls._music_dict(track) for track in raw_tracks if isinstance(track, dict)]

    @classmethod
    def _music_field(cls, release: dict[str, Any], name: str, default: Any = "") -> Any:
        field = cls._music_dict(cls._music_dict(release.get("fields")).get(name))
        return field.get("value", default)

    @classmethod
    def _canonical_music_format(cls, value: Any) -> str:
        token = cls._normalized_token(value)
        return {
            "aac": "aac",
            "ac3": "ac3",
            "ape": "ape",
            "dts": "dts",
            "flac": "flac",
            "m4a": "aac",
            "mp3": "mp3",
            "ogg": "ogg vorbis",
            "oggvorbis": "ogg vorbis",
            "opus": "opus",
            "vorbis": "ogg vorbis",
            "wav": "wav",
            "wma": "wma",
        }.get(token, token)

    @classmethod
    def _music_track_formats(cls, release: dict[str, Any], paths: list[Path]) -> set[str]:
        formats = {cls._canonical_music_format(track.get("format") or track.get("codec") or "") for track in cls._music_tracks(release)}
        formats.discard("")
        payload_formats = {cls._MUSIC_FORMAT_BY_EXTENSION[path.suffix.casefold()] for path in paths if path.suffix.casefold() in cls._AUDIO_EXTENSIONS}
        return payload_formats | formats

    @staticmethod
    def _title_contains_token(title: str, token: Any) -> bool:
        parts = re.findall(r"[a-z0-9]+", str(token or "").casefold())
        if not parts:
            return False
        pattern = r"[\s._-]*".join(re.escape(part) for part in parts)
        return bool(re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", title.casefold()))

    @staticmethod
    def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        tokens = normalized.split()
        for marker in markers:
            marker_tokens = re.sub(r"[^a-z0-9]+", " ", marker.casefold()).strip().split()
            if not marker_tokens:
                continue
            marker_text = " ".join(marker_tokens)
            if re.search(rf"(?<![a-z0-9]){re.escape(marker_text)}(?![a-z0-9])", normalized):
                return True
            compact_marker = "".join(marker_tokens)
            if compact_marker in tokens:
                return True
        return False

    @classmethod
    def _archive_is_allowed_attachment(cls, path: Path) -> bool:
        return cls._is_attachment_file(path)

    @classmethod
    def _is_archive_file(cls, path: Path) -> bool:
        return path.suffix.casefold() in cls._ARCHIVE_EXTENSIONS or cls._is_multipart_archive(path)

    @staticmethod
    def _is_multipart_archive(path: Path) -> bool:
        return bool(re.search(r"(?:\.r\d{2,}|(?:\.rar|\.zip|\.7z)\.\d{3,}|\.part\d+\.(?:rar|zip|7z))$", path.name.casefold()))

    @classmethod
    def _is_attachment_file(cls, path: Path) -> bool:
        stem = re.sub(r"[._-]+", " ", path.stem.casefold())
        return any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}s?(?![a-z0-9])", stem) for marker in cls._ATTACHMENT_ARCHIVE_MARKERS)

    @classmethod
    def _pack_tokens(cls, paths: list[Path], tokens: tuple[str, ...]) -> set[str]:
        found: set[str] = set()
        normalized_tokens = {cls._normalized_token(token): token for token in tokens}
        for path in paths:
            normalized_name = cls._normalized_token(path.stem)
            matches = sorted((canonical for token, canonical in normalized_tokens.items() if token in normalized_name), key=lambda token: len(cls._normalized_token(token)), reverse=True)
            if matches:
                found.add(matches[0])
        return found

    @staticmethod
    def _resolution_height(value: Any) -> int | None:
        match = re.search(r"(\d{3,4})", str(value or ""))
        return int(match.group(1)) if match else None

    def _valid_sd_release(self, meta: Meta, height: int) -> bool:
        if height < 480:
            return False
        genres = [str(value).casefold().strip() for value in self._metadata_values(meta.genres)]
        keywords = [str(value).casefold().strip() for value in self._metadata_values(meta.keywords)]
        source = self._normalized_token(meta.source)
        if str(meta.category or "").upper() == "TV" and source in {"tv", "dsr"} and any(value in {"sport", "sports"} for value in genres + keywords):
            return True
        disc_type = str(meta.is_disc or "").casefold()
        release_type = self._normalized_token(meta.type)
        if "dvd" in disc_type or release_type in {"dvd", "dvdrip", "cndvdrip"} or source in {"dvd", "dvdrip", "cndvdrip"}:
            return True
        return release_type == "encode" and source in {"bluray", "uhdbluray", "hddvd", "hdtv", "uhdtv"}

    def _size_exception_applies(self, meta: Meta, category: str) -> bool:
        if category == "GAME" and meta.software:
            return True
        if category != "MUSIC" or not isinstance(meta.music_release, dict):
            return False
        release_type = str(self._music_field(meta.music_release, "release_type", "")).casefold().strip()
        return release_type in {"single", "single album", "single track"}

    @staticmethod
    def _channel_count(value: Any) -> float | None:
        match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?:\s*(?:channels?|ch))?", str(value or "").casefold())
        return float(match.group(1)) if match else None

    @classmethod
    def _music_channel_counts(cls, release: dict[str, Any]) -> list[float | None]:
        return [cls._channel_count(track.get("channels")) for track in cls._music_tracks(release)]

    @classmethod
    def _music_payload_root(cls, paths: list[Path]) -> Path | None:
        audio_paths = [path for path in paths if path.suffix.casefold() in cls._AUDIO_EXTENSIONS]
        if not audio_paths or not all(path.is_absolute() for path in audio_paths):
            return None
        try:
            resolved_audio = [path.resolve(strict=True) for path in audio_paths]
            if not all(path.is_file() for path in resolved_audio):
                return None
            payload_root = resolved_audio[0].parent
            for audio_path in resolved_audio[1:]:
                while payload_root != audio_path.parent and payload_root not in audio_path.parent.parents:
                    payload_root = payload_root.parent
            while re.fullmatch(r"(?:cd|disc|disk)[ ._-]?\d+", payload_root.name, re.IGNORECASE) and payload_root.parent != payload_root:
                payload_root = payload_root.parent
            if payload_root.parent == payload_root:
                return None
            for audio_path in resolved_audio:
                audio_path.relative_to(payload_root)
        except (OSError, RuntimeError, ValueError):
            return None
        if not payload_root.is_dir():
            return None
        return payload_root

    @staticmethod
    def _cue_references_audio(cue_path: Path, payload_root: Path, audio_paths: list[Path]) -> bool:
        try:
            content = cue_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            return False
        references = re.findall(r"^\s*FILE\s+(?:\"([^\"]+)\"|(\S+))", content, re.IGNORECASE | re.MULTILINE)
        if not references:
            return False
        try:
            resolved_audio = {path.resolve(strict=True) for path in audio_paths if path.resolve(strict=True).is_file()}
        except (OSError, RuntimeError):
            return False
        resolved_references: list[Path] = []
        for quoted, bare in references:
            reference = (quoted or bare).replace("\\", "/")
            reference_path = Path(reference)
            if reference_path.is_absolute() or ".." in reference_path.parts:
                return False
            try:
                candidate = (cue_path.parent / reference_path).resolve(strict=True)
                candidate.relative_to(payload_root)
            except (OSError, RuntimeError, ValueError):
                return False
            if candidate not in resolved_audio:
                return False
            resolved_references.append(candidate)
        return bool(resolved_references)

    @classmethod
    def _music_cue_is_present(cls, release: dict[str, Any], paths: list[Path]) -> bool:
        cue_paths = [path for path in paths if path.suffix.casefold() == ".cue"]
        audio_paths = [path for path in paths if path.suffix.casefold() in cls._AUDIO_EXTENSIONS]
        payload_root = cls._music_payload_root(paths)
        for cue_path in cue_paths:
            if payload_root is not None:
                if cue_path.is_absolute() or ".." not in cue_path.parts:
                    try:
                        resolved_cue = cue_path.resolve() if cue_path.is_absolute() else (payload_root / cue_path).resolve()
                        resolved_cue.relative_to(payload_root)
                    except (OSError, RuntimeError, ValueError):
                        continue
                    if resolved_cue.is_file() and cls._cue_references_audio(resolved_cue, payload_root, audio_paths):
                        return True
                continue

        cues_value = cls._music_dict(release.get("auxiliary")).get("cues")
        if not isinstance(cues_value, list) or payload_root is None:
            return False
        raw_cues = cast(list[Any], cues_value)
        for cue in raw_cues:
            cue_path = Path(str(cue))
            if cue_path.is_absolute() or ".." in cue_path.parts or cue_path.suffix.casefold() != ".cue":
                continue
            try:
                resolved_cue = (payload_root / cue_path).resolve()
                resolved_cue.relative_to(payload_root)
            except (OSError, RuntimeError, ValueError):
                continue
            if resolved_cue.is_file() and cls._cue_references_audio(resolved_cue, payload_root, audio_paths):
                return True
        return False

    def _validate_audio_rules(self, meta: Meta, paths: list[Path]) -> bool:
        audio_paths = [path for path in paths if path.suffix.casefold() in self._AUDIO_EXTENSIONS]
        if not audio_paths:
            return True

        lossy_paths = [path for path in audio_paths if path.suffix.casefold() in self._LOSSY_AUDIO_EXTENSIONS]
        channel_counts = [self._channel_count(meta.channels)]
        release = meta.music_release if isinstance(meta.music_release, dict) else {}
        if str(meta.category or "").upper() == "MUSIC":
            channel_counts.extend(self._music_channel_counts(release))
        channel_counts = [channel for channel in channel_counts if channel is not None]
        if lossy_paths and (not channel_counts or any(channel < 5.1 for channel in channel_counts)):
            logger.info(f"{self.tracker}: [bold red]Lossy audio files must meet the 5.1-channel minimum.[/bold red]")
            return False

        has_cue = self._music_cue_is_present(release, paths)
        if len(audio_paths) > 1 and not has_cue:
            logger.info(f"{self.tracker}: [bold red]Multi-track audio uploads must include a cue sheet.[/bold red]")
            return False
        return True

    def _validate_music_rules(self, meta: Meta, paths: list[Path]) -> bool:
        audio_paths = [path for path in paths if path.suffix.casefold() in self._AUDIO_EXTENSIONS]
        if not audio_paths:
            logger.info(f"{self.tracker}: [bold red]Music uploads must contain supported audio files.[/bold red]")
            return False
        if not self._validate_audio_rules(meta, paths):
            return False

        release = meta.music_release if isinstance(meta.music_release, dict) else {}
        if len(self._music_track_formats(release, audio_paths)) > 1:
            logger.info(f"{self.tracker}: [bold red]Packed audio releases must use one encoding format.[/bold red]")
            return False
        tracks = self._music_tracks(release)
        albums = {str(track.get("album", "")).casefold().strip() for track in tracks if str(track.get("album", "")).strip()}
        if len(albums) > 1 and len(albums) < 5:
            logger.info(f"{self.tracker}: [bold red]Music packs must contain at least five albums.[/bold red]")
            return False
        return True

    def _validate_game_rules(self, meta: Meta, paths: list[Path]) -> bool:
        if meta.software:
            return True
        image_paths = [path for path in paths if path.suffix.casefold() in self._GAME_IMAGE_EXTENSIONS]
        if not image_paths:
            logger.info(f"{self.tracker}: [bold red]PC game uploads must contain an original disc image.[/bold red]")
            return False
        context = " ".join([str(meta.name or ""), *(path.name for path in paths)]).casefold()
        if any(self._contains_marker(context, (marker,)) for marker in self._GAME_PROHIBITED_MARKERS):
            logger.info(f"{self.tracker}: [bold red]Portable, highly compressed, repacked, or modified game releases are not allowed.[/bold red]")
            return False
        return True

    def _validate_pack_consistency(self, paths: list[Path]) -> bool:
        signatures: list[tuple[str, str, str]] = []
        for path in paths:
            resolution = re.search(r"\b(480[pi]|576[pi]|720p|1080[pi]|2160p)\b", path.name, re.IGNORECASE)
            sources = self._pack_tokens([path], self._PACK_SOURCE_TOKENS)
            codecs = self._pack_tokens([path], self._PACK_CODEC_TOKENS)
            if not resolution or len(sources) != 1 or len(codecs) != 1:
                return False
            signatures.append((resolution.group(1).casefold(), next(iter(sources)), next(iter(codecs))))
        return len(set(signatures)) == 1

    def _title_has_required_video_tokens(self, meta: Meta, title: str) -> bool:
        resolution = str(meta.resolution or "").strip()
        if resolution and not self._title_contains_token(title, resolution):
            return False
        if not any(self._title_contains_token(title, token) for token in self._SOURCE_TOKENS):
            return False
        if not any(self._title_contains_token(title, token) for token in self._VIDEO_CODEC_TOKENS):
            return False
        if str(meta.category or "").upper() == "MOVIE" and meta.year and str(meta.year) not in title:
            return False
        if str(meta.category or "").upper() == "TV":
            tv_pattern = r"\bS\d{1,3}(?:E\d{1,4})?\b" if meta.tv_pack else r"(?:\bS\d{1,3}E\d{1,4}\b|\b\d{4}[.-]\d{2}[.-]\d{2}\b)"
            if not re.search(tv_pattern, title, re.IGNORECASE):
                return False
        return True

    async def get_additional_checks(self, meta: Meta) -> bool:
        category = str(meta.category or "").upper()
        if category not in self.supported_categories:
            logger.info(f"{self.tracker}: [bold red]This upload category is not supported by RailgunPT.[/bold red]")
            return False

        if bool(meta.adult_media or meta.tmdb_adult_media or meta.nsfw):
            logger.info(f"{self.tracker}: [bold red]Pornographic or sensitive adult content is not allowed.[/bold red]")
            return False

        genre_values = [str(value).casefold().strip() for value in self._metadata_values(meta.genres)]
        keyword_values = [str(value).casefold().strip() for value in self._metadata_values(meta.keywords)]
        if {"politics", "political", "political propaganda"}.intersection(genre_values + keyword_values):
            logger.info(f"{self.tracker}: [bold red]Politically sensitive content is not allowed.[/bold red]")
            return False

        try:
            source_size = int(meta.source_size)
        except (TypeError, ValueError, OverflowError):
            source_size = 0
        if source_size < 100 * 1024 * 1024 and not self._size_exception_applies(meta, category):
            logger.info(f"{self.tracker}: [bold red]Torrents must be at least 100 MiB unless a RailgunPT exception applies.[/bold red]")
            return False

        raw_filelist = [] if meta.filelist is None else meta.filelist
        if not isinstance(raw_filelist, (list, tuple, set)):
            logger.info(f"{self.tracker}: [bold red]File list metadata is invalid.[/bold red]")
            return False
        paths = [Path(str(item)) for item in raw_filelist if str(item).strip()]
        video_paths = [path for path in paths if path.suffix.casefold() in self._VIDEO_EXTENSIONS]

        for path in paths:
            suffix = path.suffix.casefold()
            if suffix in self._BANNED_EXTENSIONS:
                logger.info(f"{self.tracker}: [bold red]Unsupported or spam file found: {path.name}.[/bold red]")
                return False
            if self._is_archive_file(path) and not self._archive_is_allowed_attachment(path):
                logger.info(f"{self.tracker}: [bold red]Archived files are not allowed: {path.name}.[/bold red]")
                return False
            if self._is_multipart_archive(path):
                logger.info(f"{self.tracker}: [bold red]Multipart archives are not allowed: {path.name}.[/bold red]")
                return False
            lowered_name = path.name.casefold()
            if "downloaded from" in lowered_name or "torrent downloaded" in lowered_name:
                logger.info(f"{self.tracker}: [bold red]Advertising or tracker-reference files are not allowed.[/bold red]")
                return False

        attachments = [path for path in paths if self._is_attachment_file(path)]
        if attachments and any(self._is_archive_file(path) for path in attachments) and any(not self._is_archive_file(path) for path in attachments):
            logger.info(f"{self.tracker}: [bold red]Subtitle, crack, patch, font, and scan attachments must be consistently archived or unarchived.[/bold red]")
            return False
        if not self._validate_audio_rules(meta, paths):
            return False

        if category == "MUSIC":
            return self._validate_music_rules(meta, paths)
        if category == "GAME":
            return self._validate_game_rules(meta, paths)

        if video_paths and all("sample" in path.stem.casefold() for path in video_paths):
            logger.info(f"{self.tracker}: [bold red]An individual sample cannot be uploaded as the main torrent.[/bold red]")
            return False
        main_video_paths = [path for path in video_paths if "sample" not in path.stem.casefold()]
        if not meta.is_disc and not main_video_paths:
            logger.info(f"{self.tracker}: [bold red]A non-disc upload must contain at least one recognized video file.[/bold red]")
            return False

        release_context = " ".join(str(value or "") for value in (meta.name, meta.type, meta.source, meta.uuid))
        if self._contains_marker(release_context, self._LOW_QUALITY_MARKERS):
            logger.info(f"{self.tracker}: [bold red]CAM/TC/TS/SCR/R5 and similar low-quality sources are not allowed.[/bold red]")
            return False
        codec_values = (str(meta.video_codec or ""), str(meta.video_encode or ""))
        if any("realvideo" in value.casefold() or self._normalized_token(value) in {"rv", "rv10", "rv20", "rv30", "rv40"} for value in codec_values):
            logger.info(f"{self.tracker}: [bold red]RealVideo encodes are not allowed.[/bold red]")
            return False

        disc_type = str(meta.is_disc or "").casefold()
        height = self._resolution_height(meta.resolution)
        if not disc_type:
            if height is None:
                logger.info(f"{self.tracker}: [bold red]A supported video resolution is required.[/bold red]")
                return False
            if height < 720 and not self._valid_sd_release(meta, height):
                logger.info(f"{self.tracker}: [bold red]SD uploads must be at least 480p and sourced from HD media or DVD.[/bold red]")
                return False
            if height < 720 and "upscale" in release_context.casefold():
                logger.info(f"{self.tracker}: [bold red]Upscaled SD-mastered content is not allowed.[/bold red]")
                return False

        release_name = str(meta.name or "").strip()
        if not release_name or not self._title_has_required_video_tokens(meta, release_name):
            logger.info(f"{self.tracker}: [bold red]Title must include the required year/season, resolution, source, and video codec information.[/bold red]")
            return False

        if category == "MOVIE" and len(main_video_paths) > 1 and disc_type not in self._DISC_TYPES:
            pack_markers = ("boxset", "box set", "collection", "trilogy")
            if not any(marker in release_name.casefold() for marker in pack_markers):
                logger.info(f"{self.tracker}: [bold red]Movie packs must be identifiable official box-set collections.[/bold red]")
                return False
            if not self._validate_pack_consistency(main_video_paths):
                logger.info(f"{self.tracker}: [bold red]Packed videos must use the same source type, resolution, and video codec.[/bold red]")
                return False

        if meta.tv_pack and len(main_video_paths) > 1 and not self._validate_pack_consistency(main_video_paths):
            logger.info(f"{self.tracker}: [bold red]Packed videos must use the same source type, resolution, and video codec.[/bold red]")
            return False

        return True

    def get_category(self, meta: Meta) -> int:
        animations = 405
        documentaries = 404
        movies = 401
        tv_series = 402
        tv_shows = 403

        category = str(meta.category or "").upper()
        genres = ", ".join(str(value) for value in self._metadata_values(meta.genres)).lower()
        keywords = ", ".join(str(value) for value in self._metadata_values(meta.keywords)).lower()

        if category == "MUSIC":
            return 408
        if category == "GAME":
            return 410 if meta.software else 412
        is_sports = category == "TV" and any(re.search(rf"(^|,\s*){re.escape(keyword)}(\s*,|$)", genres + ", " + keywords, re.IGNORECASE) for keyword in ("sport", "sports"))
        if is_sports:
            return 407
        if "documentary" in genres or "documentary" in keywords:
            return documentaries
        if meta.anime or "animation" in genres or "animation" in keywords:
            return animations

        if category == "MOVIE":
            return movies
        if category == "TV":
            game_show_keywords = [
                "award show",
                "competition",
                "game show",
                "music show",
                "performance",
                "reality television",
                "reality tv",
                "reality",
                "stand-up",
                "talk show",
                "tv show",
                "variety",
            ]
            if any(re.search(rf"(^|,\s*){re.escape(keyword)}(\s*,|$)", genres, re.IGNORECASE) for keyword in game_show_keywords):
                return tv_shows
            return tv_series

        return movies

    def get_type(self, meta: Meta) -> int:
        blu_ray = 1
        dvd = 6
        encode = 7
        hdtv = 5
        remux = 3
        uhd = 2
        web_dl = 4

        if str(meta.category or "").upper() == "MUSIC":
            return 8

        is_disc = str(meta.is_disc or "").lower()
        mtype = str(meta.type).lower()
        resolution = str(meta.resolution or "").lower()

        if is_disc == "bdmv":
            if resolution == "2160p":
                return uhd
            return blu_ray
        if "dvd" in is_disc:
            return dvd

        if mtype == "remux":
            return remux
        if mtype in ("webdl", "webrip"):
            return web_dl
        if mtype == "hdtv":
            return hdtv
        if mtype == "encode":
            return encode

        return encode

    def get_codec(self, meta: Meta) -> int:
        h264 = 1
        h265 = 2
        mpeg2 = 4
        other = 6
        vc1 = 3
        xvid = 5

        codec = str(meta.video_codec or "").lower()

        if "h265" in codec or "x265" in codec or "hevc" in codec:
            return h265
        if "h264" in codec or "x264" in codec or "avc" in codec:
            return h264
        if "mpeg2" in codec or "mpeg-2" in codec:
            return mpeg2
        if "vc1" in codec or "vc-1" in codec:
            return vc1
        if "xvid" in codec:
            return xvid

        return other

    def get_resolution(self, meta: Meta) -> int:
        resolution = str(meta.resolution or "").lower()

        if resolution == "1080p" or resolution == "1080i":
            return 2
        if resolution == "720p":
            return 3
        if meta.sd:
            return 4
        if resolution == "2160p":
            return 1

        return 5

    def get_audio_codec(self, meta: Meta) -> int:
        audio_values = [str(meta.audio or ""), str(meta.format or "")]
        if isinstance(meta.music_release, dict):
            tracks = self._music_tracks(self._music_dict(meta.music_release))
            if tracks:
                audio_values.extend([str(tracks[0].get("format") or ""), str(tracks[0].get("codec") or "")])
        audio_codec = " ".join(audio_values).lower()

        if "true" in audio_codec or "atmos" in audio_codec:
            return 1
        if "dts" in audio_codec:
            return 2
        if "dd" in audio_codec:
            return 3
        if "lpcm" in audio_codec:
            return 4
        if "flac" in audio_codec:
            return 5
        if "mp3" in audio_codec:
            return 6
        if "aac" in audio_codec:
            return 7
        if "ape" in audio_codec:
            return 8
        if "wav" in audio_codec:
            return 10

        return 9

    def get_checkboxes(self, meta: Meta) -> list[str]:
        chinese_audio = 5
        chinese_subtitle = 6
        hdr = 7
        reposting_prohibited = 1

        audio_tracks = meta.audio_languages or []
        mhdr = str(meta.hdr or "")
        subtitle_tracks = meta.subtitle_languages or []

        checkboxes: list[str] = []

        if meta.exclusive:
            checkboxes.append(str(reposting_prohibited))

        if "Chinese" in audio_tracks or "Mandarin" in audio_tracks:
            checkboxes.append(str(chinese_audio))

        if "Chinese" in subtitle_tracks or "Mandarin" in subtitle_tracks:
            checkboxes.append(str(chinese_subtitle))

        if "HDR" in mhdr.upper():
            checkboxes.append(str(hdr))

        return checkboxes

    def get_anonymous(self, meta: Meta) -> bool:
        return not (meta.anon == 0 and not self.config["TRACKERS"][self.tracker].get("anon", False))
