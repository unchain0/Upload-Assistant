import asyncio
import contextlib
import gettext
import json
import mimetypes
import platform
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urljoin

import aiofiles
import cli_ui
import httpx
import langcodes
import pycountry
from bs4 import BeautifulSoup

from src.domain_models.genre_mapping import ENG_TO_PTBR_GENRE_MAP
from src.domain_models.release import Meta
from src.integrations.media.language_adapter import languages_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.observability.terminal_link_formatting import (
    format_terminal_link,
)
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator

Config = dict[str, Any]

FORUM_COUNTRY_GROUPS: tuple[tuple[int, frozenset[str]], ...] = (
    (
        461,
        frozenset(
            [
                "DZ",
                "AO",
                "BJ",
                "BW",
                "BF",
                "BI",
                "CM",
                "CV",
                "CF",
                "TD",
                "KM",
                "CD",
                "CG",
                "CI",
                "DJ",
                "EG",
                "GQ",
                "ER",
                "ET",
                "GA",
                "GM",
                "GH",
                "GN",
                "GW",
                "KE",
                "LS",
                "LR",
                "LY",
                "MG",
                "MW",
                "ML",
                "MR",
                "MU",
                "MA",
                "MZ",
                "NA",
                "NE",
                "NG",
                "RW",
                "ST",
                "SN",
                "SC",
                "SL",
                "SO",
                "ZA",
                "SS",
                "SD",
                "SZ",
                "TZ",
                "TG",
                "TN",
                "UG",
                "ZM",
                "ZW",
            ]
        ),
    ),
    (
        24,
        frozenset(
            [
                "AF",
                "AM",
                "AZ",
                "BD",
                "BT",
                "BN",
                "KH",
                "CN",
                "GE",
                "IN",
                "ID",
                "JP",
                "KZ",
                "KG",
                "LA",
                "MY",
                "MV",
                "MN",
                "MM",
                "NP",
                "KP",
                "KR",
                "PK",
                "PH",
                "SG",
                "LK",
                "TW",
                "TJ",
                "TH",
                "TL",
                "TM",
                "UZ",
                "VN",
            ]
        ),
    ),
    (
        25,
        frozenset(
            [
                "AL",
                "XC",
                "AD",
                "AT",
                "BY",
                "BE",
                "BA",
                "BG",
                "HR",
                "SU",
                "CY",
                "CZ",
                "DK",
                "EE",
                "FI",
                "FR",
                "DE",
                "GR",
                "HU",
                "IS",
                "IE",
                "IT",
                "XK",
                "LV",
                "LI",
                "LT",
                "LU",
                "MT",
                "MD",
                "MC",
                "ME",
                "MK",
                "NL",
                "NO",
                "PL",
                "PT",
                "RO",
                "RU",
                "SM",
                "RS",
                "SK",
                "SI",
                "ES",
                "SE",
                "CH",
                "UA",
                "GB",
                "VA",
            ]
        ),
    ),
    (
        29,
        frozenset(
            [
                "AR",
                "BO",
                "CL",
                "CO",
                "CR",
                "CU",
                "DO",
                "EC",
                "SV",
                "GT",
                "HN",
                "MX",
                "NI",
                "PA",
                "PY",
                "PE",
                "UY",
                "VE",
            ]
        ),
    ),
    (27, frozenset({"BR"})),
    (26, frozenset({"US", "CA"})),
    (
        31,
        frozenset(
            [
                "AU",
                "FJ",
                "KI",
                "MH",
                "FM",
                "NR",
                "NZ",
                "PW",
                "PG",
                "WS",
                "SB",
                "TO",
                "TV",
                "VU",
            ]
        ),
    ),
    (
        30,
        frozenset(
            [
                "BH",
                "IR",
                "IQ",
                "IL",
                "JO",
                "KW",
                "LB",
                "OM",
                "QA",
                "SA",
                "SY",
                "AE",
                "YE",
            ]
        ),
    ),
)

FORUM_OPTIONS: dict[str, tuple[int, str]] = {
    "1": (461, "África"),
    "2": (24, "Asiático"),
    "3": (77, "Curtas"),
    "4": (28, "Documentários"),
    "5": (25, "Europeu"),
    "6": (29, "Latino Americano"),
    "7": (27, "Nacional (Brasil)"),
    "8": (26, "Norte-Americano"),
    "9": (31, "Oceania"),
    "10": (30, "Oriente Médio"),
}


class MakingOff:
    """
    Making Off is a BRAZILIAN Private Torrent Tracker for MOVIES / TV / GENERAL
    """

    auth_type = "cookies"
    tracker = "MAKINGOFF"
    display_name = "MakingOff"
    source_flag = ""
    base_url = "https://www.makingoff.org"
    banned_groups: tuple[str, ...] = ("aXXo", "CM8", "YIFY", "STUTTERSHIT")
    index_url = "https://www.makingoff.org/"
    torrent_url = ""
    supported_categories = ("MOVIE",)
    max_search_pages = 25
    allows_bloated_audio = True
    tmdb_localization_requirements: ClassVar = {
        "pt-BR": {
            "main": "credits,translations",
        },
        "en-US": {
            "main": "credits,translations",
        },
    }

    # HMediaInfo constants
    VIDEO_CODEC_MAP: ClassVar[list[tuple[list[str], str]]] = [
        (["avc", "h.264", "h264"], "H.264"),
        (["hevc", "h.265", "h265"], "H.265 (HEVC)"),
        (["av1"], "AV1"),
        (["vp9"], "VP9"),
        (["xvid"], "XviD"),
        (["divx"], "DivX"),
        (["mpeg-4"], "MPEG-4"),
        (["mpeg"], "MPEG-2"),
    ]

    AUDIO_CODEC_MAP: ClassVar[list[tuple[list[str], str]]] = [
        (["aac"], "AAC"),
        (["e-ac-3", "eac3"], "E-AC-3 (Dolby Digital Plus)"),
        (["ac-3", "ac3"], "AC-3 (Dolby Digital)"),
        (["truehd"], "Dolby TrueHD"),
        (["dts"], "DTS"),
        (["mp3", "mpeg audio"], "MP3"),
        (["flac"], "FLAC"),
        (["opus"], "Opus"),
    ]

    def __init__(self, config: Config):
        self.config = config
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)

        # Cache for the resolved PT-BR display title, keyed by meta.uuid.
        self._display_title_cache: dict[str, str] = {}
        self._csrf_token: str = ""

        tracker_config = dict(
            dict(config.get("TRACKERS", {})).get("MAKINGOFF", {})
        )
        public_trackers_raw = tracker_config.get("trackers", [])
        if isinstance(public_trackers_raw, str):
            self._public_trackers: list[str] = [
                t.strip()
                for t in public_trackers_raw.splitlines()
                if t.strip()
            ]
        else:
            self._public_trackers = list(public_trackers_raw)

        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
                ),
                "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate",
                "Sec-Ch-Ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=60.0,
            follow_redirects=True,
        )

    def _normalize_codec(
        self, fmt: str, mapping: list[tuple[list[str], str]]
    ) -> str:
        f = fmt.lower()
        for keys, label in mapping:
            if any(k in f for k in keys):
                return label
        return fmt

    def _mediainfo_video_codec(
        self, meta: Meta, video_track: dict[str, Any]
    ) -> str:
        """Return the normalised video codec label."""
        fmt = video_track.get("Format", "").strip()
        if not fmt:
            fmt = (meta.video_encode or meta.video_codec or "").strip()
        return self._normalize_codec(fmt, self.VIDEO_CODEC_MAP) if fmt else ""

    def _mediainfo_audio_codec(
        self, meta: Meta, audio_track: dict[str, Any]
    ) -> str:
        """Return the normalised audio codec label."""
        fmt = audio_track.get("Format", "").strip()
        if not fmt:
            fmt = (meta.audio or "").strip()
        return self._normalize_codec(fmt, self.AUDIO_CODEC_MAP) if fmt else ""

    @staticmethod
    def _container_alias(fmt: str) -> str | None:
        aliases = (
            ("matroska", "MKV"),
            ("avi", "AVI"),
            ("mp4", "MP4"),
            ("mpeg-4", "MP4"),
        )
        for token, label in aliases:
            if token in fmt:
                return label
        return None

    def _mediainfo_container(
        self, general_track: dict[str, Any], fallback: str = ""
    ) -> str:
        """Return the container format, preferring mediainfo General track."""
        raw_format = str(general_track.get("Format", "") or "")
        if not raw_format:
            return fallback
        return self._container_alias(raw_format.lower()) or raw_format

    def _mediainfo_filesize(self, meta: Meta) -> str:
        """Return a human-readable file size (GB or MB)."""
        try:
            gb = meta.source_size / 1024**3
            return (
                f"{gb:.2f} GB"
                if gb >= 1
                else f"{meta.source_size / 1024**2:.0f} MB"
            )
        except TypeError, ValueError:
            return "N/A"

    def _mediainfo_duration(
        self, general_track: dict[str, Any], video_track: dict[str, Any]
    ) -> str:
        """Return duration in minutes from mediainfo General track."""
        raw = (
            general_track.get("Duration") or video_track.get("Duration") or ""
        )
        try:
            return str(int(float(raw)) // 60)
        except TypeError, ValueError:
            return ""

    def _aspect_ratio(self, width: Any, height: Any) -> str:
        """Return an aspect ratio category from video dimensions matching MakingOff options."""
        try:
            r = int(width) / int(height)
            if r < 1.45:
                return "Tela Cheia (4x3)"
            if r < 1.85:
                return "Widescreen (16x9)"
            return "Scope (2.35:1)"
        except TypeError, ValueError, ZeroDivisionError:
            return "Widescreen (16x9)"

    def _html_encode(self, text: str) -> str:
        """Return the text unchanged (XenForo supports native UTF-8)."""
        return text

    @staticmethod
    def _screen_pair(left: str, right: str) -> str:
        return (
            f"[screenLeft][screenIma]{left}[/screenIma][/screenLeft]"
            f"[screenRight][screenIma]{right}[/screenIma][/screenRight]"
        )

    def _screen_rows(self, image_urls: list[str]) -> str:
        """Pair screenshot URLs into two-column BBCode rows matching MakingOff."""
        urls = image_urls[:8]
        pair_count = max(2, min(4, (len(urls) + 1) // 2))
        padded = urls + [""] * (pair_count * 2 - len(urls))
        pairs = [
            self._screen_pair(padded[index], padded[index + 1])
            for index in range(0, pair_count * 2, 2)
        ]
        body = "[/tr][tr]".join(pairs)
        return f"{body}[closeTab][/closeTab][/tr]"

    @staticmethod
    def _ffmpeg_arch(machine: str) -> str | None:
        normalized = machine.lower()
        if normalized in {"x86_64", "amd64"}:
            return "amd"
        if normalized in {"aarch64", "arm64"}:
            return "arm"
        return None

    @classmethod
    def _linux_ffmpeg_candidate(cls, base_dir: str) -> Path | None:
        arch = cls._ffmpeg_arch(platform.machine())
        if arch is None:
            return None
        return Path(base_dir) / "bin" / "ffmpeg" / arch / "ffmpeg"

    @staticmethod
    def _windows_ffmpeg_candidate(base_dir: str) -> Path:
        return Path(base_dir) / "bin" / "ffmpeg.exe"

    @classmethod
    def _bundled_ffmpeg_candidate(cls, base_dir: str) -> Path | None:
        system = platform.system()
        if system == "Linux":
            return cls._linux_ffmpeg_candidate(base_dir)
        if system == "Windows":
            return cls._windows_ffmpeg_candidate(base_dir)
        return None

    def _get_ffmpeg_path(self, meta: Meta) -> str:
        configured = configured_binary("ffmpeg_path", self.config)
        if configured:
            return configured
        base_dir = getattr(meta, "base_dir", "") or str(
            Path(__file__).parent.parent.parent
        )
        candidate = self._bundled_ffmpeg_candidate(str(base_dir))
        if candidate is not None and candidate.exists():
            return str(candidate)
        return "ffmpeg"

    @staticmethod
    def _subtitle_word_sets() -> tuple[set[str], set[str]]:
        portuguese = {
            "que",
            "não",
            "uma",
            "com",
            "mais",
            "para",
            "está",
            "estou",
            "você",
            "como",
            "mas",
            "bem",
            "ele",
            "ela",
            "vocês",
            "estavam",
            "fazer",
        }
        english = {
            "the",
            "and",
            "you",
            "that",
            "was",
            "for",
            "are",
            "with",
            "have",
            "this",
            "what",
            "they",
            "here",
            "know",
        }
        return portuguese, english

    def _read_subtitle_sample(self, file_path: str) -> str:
        for encoding in ("utf-8", "latin-1", "cp1252", "utf-16"):
            try:
                content = (
                    Path(file_path)
                    .read_text(encoding=encoding, errors="ignore")[:4096]
                    .lower()
                )
            except Exception as error:
                logger.debug(
                    f"{self.tracker}: Failed to read file {file_path} with "
                    f"encoding {encoding}: {error}"
                )
                continue
            if content:
                return content
        return ""

    @classmethod
    def _subtitle_language_counts(cls, content: str) -> tuple[int, int]:
        portuguese, english = cls._subtitle_word_sets()
        words = re.findall(r"\b\w+\b", content)
        pt_count = sum(word in portuguese for word in words)
        en_count = sum(word in english for word in words)
        return pt_count, en_count

    def _is_subtitle_in_portuguese(self, file_path: str) -> bool:
        content = self._read_subtitle_sample(file_path)
        if not content:
            return False
        pt_count, en_count = self._subtitle_language_counts(content)
        return pt_count > en_count

    @staticmethod
    def _has_portuguese_language(languages: Any) -> bool:
        """Return whether a MediaInfo/UA language value denotes Portuguese."""
        values = (
            [languages] if isinstance(languages, str) else (languages or [])
        )
        for value in values:
            normalized = str(value).strip().lower()
            if normalized in {
                "pt",
                "por",
                "portuguese",
                "português",
                "pt-br",
                "pt_br",
                "pt-pt",
                "pt_pt",
            }:
                return True
        return False

    @staticmethod
    def _portuguese_subtitle_markers() -> tuple[str, ...]:
        return (
            ".pt",
            ".por",
            "portuguese",
            "português",
            "ptbr",
            "pt_br",
            "pt-pt",
            "ptpt",
        )

    @classmethod
    def _subtitle_name_is_portuguese(cls, name: str) -> bool:
        lowered = name.lower()
        return any(
            marker in lowered for marker in cls._portuguese_subtitle_markers()
        )

    def _sidecar_is_portuguese(self, sub_file: str) -> bool:
        path = Path(sub_file)
        if not path.exists():
            return False
        if self._subtitle_name_is_portuguese(path.name):
            return True
        return self._is_subtitle_in_portuguese(str(path))

    def _track_is_portuguese(self, track: dict[str, Any]) -> bool:
        if track.get("@type") != "Text":
            return False
        if self._has_portuguese_language(track.get("Language", "")):
            return True
        return self._subtitle_name_is_portuguese(str(track.get("Title", "")))

    def _embedded_portuguese_subtitle(self, meta: Meta) -> bool:
        tracks = cast(
            list[dict[str, Any]],
            getattr(meta, "mediainfo", {}).get("media", {}).get("track", []),
        )
        return any(self._track_is_portuguese(track) for track in tracks)

    def _has_portuguese_subtitle(self, meta: Meta) -> bool:
        """Detect a Portuguese subtitle track, sidecar subtitle, or hard sub."""
        if self._has_portuguese_language(
            getattr(meta, "subtitle_languages", [])
        ):
            return True
        sidecars = getattr(meta, "subtitle_files", [])
        if any(self._sidecar_is_portuguese(str(path)) for path in sidecars):
            return True
        if self._embedded_portuguese_subtitle(meta):
            return True
        return bool(getattr(meta, "hardcoded_subs", False))

    @staticmethod
    def _dimension_value(value: object) -> int:
        if not isinstance(value, (str, int, float)):
            return 0
        try:
            return int(value)
        except TypeError, ValueError:
            return 0

    @classmethod
    def _dimensions_are_hidef(cls, width: object, height: object) -> bool:
        return (
            cls._dimension_value(width) > 1024
            or cls._dimension_value(height) > 576
        )

    @classmethod
    def _is_hidef(cls, meta: Meta) -> bool:
        """Apply MakingOff's definition of HD, falling back to UA resolution."""
        if cls._dimensions_are_hidef(meta.video_width, meta.video_height):
            return True
        return str(getattr(meta, "resolution", "")).lower() in {
            "720p",
            "1080i",
            "1080p",
            "1440p",
            "2160p",
            "4320p",
        }

    @staticmethod
    def _release_tokens(meta: Meta) -> str:
        """Return release-identifying text used for deterministic quality checks."""
        fields = (
            getattr(meta, "name", ""),
            getattr(meta, "basename_no_ext", ""),
            getattr(meta, "source", ""),
            getattr(meta, "type", ""),
        )
        filenames = (str(item) for item in getattr(meta, "filelist", []) or [])
        return " ".join(
            str(value) for value in (*fields, *filenames) if value
        ).lower()

    def _external_subtitle_match(self, path: Path) -> tuple[bool, bool]:
        if not path.exists():
            return False, False
        marker_match = self._subtitle_name_is_portuguese(path.name)
        content_match = self._is_subtitle_in_portuguese(str(path))
        return marker_match, content_match

    def _external_portuguese_subtitles(self, meta: Meta) -> list[str]:
        matches: list[str] = []
        for sub_file in getattr(meta, "subtitle_files", []):
            path = Path(str(sub_file))
            marker_match, content_match = self._external_subtitle_match(path)
            if not marker_match and not content_match:
                continue
            matches.append(str(path))
            reason = "" if marker_match else " (content-matched)"
            logger.info(
                f"{self.tracker}: [green]Found external Portuguese "
                f"subtitle{reason}:[/green] {path.name}"
            )
        return matches

    @staticmethod
    def _embedded_subtitle_video(meta: Meta) -> str | None:
        if meta.is_disc or not meta.filelist:
            return None
        video_file = str(meta.filelist[0])
        valid_extension = video_file.lower().endswith((".mkv", ".mp4", ".m4v"))
        if not valid_extension or not Path(video_file).is_file():
            return None
        return video_file

    @staticmethod
    def _embedded_text_tracks(meta: Meta) -> list[dict[str, Any]]:
        tracks = cast(
            list[dict[str, Any]],
            meta.mediainfo.get("media", {}).get("track", []),
        )
        return [track for track in tracks if track.get("@type") == "Text"]

    def _embedded_track_is_portuguese(self, track: dict[str, Any]) -> bool:
        language = str(track.get("Language", ""))
        if self._has_portuguese_language(language):
            return True
        return self._subtitle_name_is_portuguese(str(track.get("Title", "")))

    @staticmethod
    def _subtitle_extension(track: dict[str, Any]) -> str:
        fmt = str(track.get("Format", "")).upper()
        aliases = (
            ("ASS", ".ass"),
            ("SSA", ".ass"),
            ("VTT", ".vtt"),
            ("PGS", ".sup"),
            ("SUP", ".sup"),
        )
        for token, extension in aliases:
            if token in fmt:
                return extension
        return ".srt"

    @staticmethod
    def _subtitle_title_slug(track: dict[str, Any]) -> str:
        title = track.get("Title")
        if not title:
            return ""
        clean = re.sub(r"[^a-zA-Z0-9_-]", "_", str(title))
        return f"-{clean}"

    @classmethod
    def _subtitle_output_path(
        cls, meta: Meta, track: dict[str, Any], index: int
    ) -> tuple[str, str]:
        temp_dir = Path(str(meta.base_dir)) / "tmp" / str(meta.uuid)
        temp_dir.mkdir(parents=True, exist_ok=True)
        release_name = meta.basename_no_ext or meta.name or meta.uuid
        filename = str(release_name).replace(" ", ".")
        output_name = (
            f"{filename}.pt-{index}{cls._subtitle_title_slug(track)}"
            f"{cls._subtitle_extension(track)}"
        )
        return output_name, str(temp_dir / output_name)

    @staticmethod
    def _ffmpeg_command(
        ffmpeg_path: str, video_file: str, index: int, output_path: str
    ) -> list[str]:
        return [
            ffmpeg_path,
            "-y",
            "-i",
            video_file,
            "-map",
            f"0:s:{index}",
            output_path,
        ]

    @staticmethod
    def _extracted_subtitle_valid(process: Any, output_path: str) -> bool:
        path = Path(output_path)
        return bool(
            process.returncode == 0
            and path.exists()
            and path.stat().st_size > 0
        )

    async def _extract_embedded_subtitle(
        self,
        meta: Meta,
        video_file: str,
        track: dict[str, Any],
        index: int,
    ) -> str | None:
        output_name, output_path = self._subtitle_output_path(
            meta, track, index
        )
        command = self._ffmpeg_command(
            self._get_ffmpeg_path(meta), video_file, index, output_path
        )
        logger.info(
            f"{self.tracker}: Extracting embedded Portuguese subtitle "
            f"(stream {index}) to {output_name}..."
        )
        if meta.debug:
            logger.debug(
                f"{self.tracker}: Skipping ffmpeg extraction in debug mode. "
                f"Command: {' '.join(command)}"
            )
            return None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
        except (OSError, ValueError) as error:
            logger.error(
                f"{self.tracker}: [red]Error running ffmpeg to extract "
                f"subtitle: {error}[/red]"
            )
            return None
        if self._extracted_subtitle_valid(process, output_path):
            logger.info(
                f"{self.tracker}: [green]Successfully extracted embedded "
                "Portuguese subtitle.[/green]"
            )
            return output_path
        logger.warning(
            f"{self.tracker}: [yellow]Failed to extract subtitle stream {index}. "
            f"ffmpeg exit code: {process.returncode}[/yellow]"
        )
        if stderr:
            logger.debug(
                f"{self.tracker}: ffmpeg stderr: "
                f"{stderr.decode('utf-8', errors='ignore')}"
            )
        return None

    async def _embedded_portuguese_subtitles(self, meta: Meta) -> list[str]:
        video_file = self._embedded_subtitle_video(meta)
        if video_file is None:
            return []
        results: list[str] = []
        for index, track in enumerate(self._embedded_text_tracks(meta)):
            if not self._embedded_track_is_portuguese(track):
                continue
            output = await self._extract_embedded_subtitle(
                meta, video_file, track, index
            )
            if output is not None:
                results.append(output)
        return results

    async def _get_portuguese_subtitles(self, meta: Meta) -> list[str]:
        """Find external and embedded Portuguese subtitles for the release."""
        subtitles = self._external_portuguese_subtitles(meta)
        subtitles.extend(await self._embedded_portuguese_subtitles(meta))
        return sorted(set(subtitles))

    @staticmethod
    def _release_label(release: str) -> str:
        value = release or "Release não informado"
        return f"[release]{value}[/release][/tr]"

    def _bbcode_intro(
        self,
        *,
        title_br: str,
        title_orig: str,
        release: str,
        poster_url: str,
        overview: str,
        image_urls: list[str],
    ) -> str:
        return (
            "[tablePrinc][tr][titMasc]Título do Filme[/titMasc][/tr]"
            f"[tr][titTrad]{title_br}[/titTrad][titOri]{title_orig}[/titOri]"
            f"{self._release_label(release)}"
            "[tr][posterMasc]Poster[/posterMasc][sinopseMasc]Sinopse[/sinopseMasc][/tr]"
            f"[tr][poster][posterIma]{poster_url}[/posterIma][/poster]"
            f"[sinopse]{overview}[/sinopse][tableScreen]Screenshots[/tableScreen]"
            f"{self._screen_rows(image_urls)}[/tablePrinc]"
        )

    @staticmethod
    def _optional_info_line(label: str, value: str, suffix: str = "") -> str:
        return f"[b]{label}: [/b]{value}{suffix}\n" if value else ""

    @classmethod
    def _movie_info_block(
        cls,
        *,
        genres: str,
        directors: str,
        duration: str,
        year: str,
        countries: str,
        audio: str,
        imdb_url: str,
        homepage_url: str,
    ) -> str:
        lines = [
            f"[info][b]Gênero: [/b]{genres}\n",
            f"[b]Diretor: [/b]{directors}\n",
            cls._optional_info_line("Duração", duration, " minutos"),
            f"[b]Ano de Lançamento: [/b]{year}\n",
            f"[b]País de Origem: [/b]{countries}\n",
            f"[b]Idioma do Áudio: [/b]{audio}\n",
        ]
        if imdb_url:
            lines.append(f"[b]IMDB: [/b][url={imdb_url}]{imdb_url}[/url]\n")
        if homepage_url:
            lines.append(
                f"[b]Site Oficial: [/b][url={homepage_url}]"
                f"{homepage_url}[/url]\n"
            )
        lines.append("[/info]")
        return "".join(lines)

    @staticmethod
    def _valid_resolution_text(resolution: str) -> bool:
        return bool(
            resolution and "x0" not in resolution and "0x" not in resolution
        )

    @staticmethod
    def _bitrate_line(label: str, value: str) -> str:
        if not value or value == "None":
            return ""
        return f"[b]{label}: [/b]{value} Kbps\n"

    @classmethod
    def _resolution_line(cls, resolution: str) -> str:
        if not cls._valid_resolution_text(resolution):
            return ""
        return f"[b]Resolução: [/b]{resolution}\n"

    @classmethod
    def _release_info_block(
        cls,
        *,
        quality: str,
        container: str,
        video_codec: str,
        video_brate: str,
        audio_codec: str,
        audio_brate: str,
        res_str: str,
        aspect: str,
        fps_str: str,
        filesize: str,
        subs: str,
    ) -> str:
        lines = [
            f"[info][b]Qualidade de Vídeo: [/b]{quality}\n",
            cls._optional_info_line("Container", container),
            cls._optional_info_line("Vídeo Codec", video_codec),
            cls._bitrate_line("Vídeo Bitrate", video_brate),
            cls._optional_info_line("Áudio Codec", audio_codec),
            cls._bitrate_line("Áudio Bitrate", audio_brate),
            cls._resolution_line(res_str),
            cls._optional_info_line("Formato de Tela", aspect),
            cls._optional_info_line("Frame Rate", fps_str),
            f"[b]Tamanho: [/b]{filesize}\n",
            f"[b]Legendas: [/b]{subs}[/info]",
        ]
        return "".join(lines)

    @staticmethod
    def _extra_info_rows(awards: str, trivia: str, critic: str) -> str:
        sections = (
            ("Premiações", awards),
            ("Curiosidades", trivia),
            ("Crítica", critic),
        )
        return "".join(
            f"[/tr][tr][infoExtraMasc]{label}[/infoExtraMasc][/tr]"
            f"[tr][infoExtra]{value}[/infoExtra]"
            for label, value in sections
            if value
        )

    def _build_bbcode(
        self,
        *,
        title_br: str,
        title_orig: str,
        release: str,
        poster_url: str,
        overview: str,
        image_urls: list[str],
        cast_text: str,
        genres: str,
        directors: str,
        duration: str,
        year: str,
        countries: str,
        audio: str,
        subs: str,
        imdb_url: str,
        homepage_url: str,
        quality: str,
        container: str,
        video_codec: str,
        video_brate: str,
        audio_codec: str,
        audio_brate: str,
        res_str: str,
        aspect: str,
        fps_str: str,
        filesize: str,
        awards: str = "",
        trivia: str = "",
        critic: str = "",
    ) -> str:
        """Render the complete MakingOff BBCode post body."""
        intro = self._bbcode_intro(
            title_br=title_br,
            title_orig=title_orig,
            release=release,
            poster_url=poster_url,
            overview=overview,
            image_urls=image_urls,
        )
        columns = (
            "[tablePrinc][tr][posterMasc]Elenco[/posterMasc]"
            "[infoMasc]Informações sobre o filme[/infoMasc]"
            "[infoMasc]Informações sobre o release[/infoMasc][/tr]"
            f"[tr][elenco]{cast_text}[/elenco]"
        )
        movie_info = self._movie_info_block(
            genres=genres,
            directors=directors,
            duration=duration,
            year=year,
            countries=countries,
            audio=audio,
            imdb_url=imdb_url,
            homepage_url=homepage_url,
        )
        release_info = self._release_info_block(
            quality=quality,
            container=container,
            video_codec=video_codec,
            video_brate=video_brate,
            audio_codec=audio_codec,
            audio_brate=audio_brate,
            res_str=res_str,
            aspect=aspect,
            fps_str=fps_str,
            filesize=filesize,
            subs=subs,
        )
        extras = self._extra_info_rows(awards, trivia, critic)
        footer = (
            "[/tr][tr][rodape]Coopere, deixe semeando ao menos duas vezes o "
            "tamanho do arquivo que baixar.[/rodape][/tr][/tablePrinc]"
        )
        return self._html_encode(
            f"{intro}{columns}{movie_info}{release_info}{extras}{footer}"
        )

    def _get_lang_name(self, lang_string: str) -> str:
        with contextlib.suppress(Exception):
            lang = langcodes.find(lang_string)
            if lang and lang.is_valid():
                return lang.display_name("pt").capitalize()
        return lang_string.capitalize()

    @staticmethod
    def _country_translations() -> tuple[Any | None, Any | None]:
        try:
            normal = gettext.translation(
                "iso3166-1", pycountry.LOCALES_DIR, languages=["pt_BR"]
            )
            historic = gettext.translation(
                "iso3166-3", pycountry.LOCALES_DIR, languages=["pt_BR"]
            )
            return normal, historic
        except OSError:
            return None, None

    @staticmethod
    def _production_country_codes(meta: Meta) -> list[str]:
        codes: list[str] = []
        for country in meta.production_countries:
            code = country.get("iso_3166_1")
            if code:
                codes.append(str(code))
        return codes

    @classmethod
    def _country_codes(cls, meta: Meta) -> list[str]:
        production = cls._production_country_codes(meta)
        if production:
            return production
        return [str(code) for code in meta.origin_country if code]

    @staticmethod
    def _active_country_name(code: str, normal: Any | None) -> str | None:
        country = pycountry.countries.get(alpha_2=code)
        if country is None:
            return None
        return normal.gettext(country.name) if normal else str(country.name)

    @staticmethod
    def _historic_country_name(code: str, historic: Any | None) -> str | None:
        country = pycountry.historic_countries.get(alpha_2=code)
        if country is None:
            return None
        return (
            historic.gettext(country.name) if historic else str(country.name)
        )

    @classmethod
    def _localized_country_name(
        cls, code: str, normal: Any | None, historic: Any | None
    ) -> str:
        custom = {"XC": "Checoslováquia"}
        upper = code.upper()
        if upper in custom:
            return custom[upper]
        active = cls._active_country_name(upper, normal)
        if active is not None:
            return active
        old = cls._historic_country_name(upper, historic)
        return old or code

    def _localizer_countries(self, meta: Meta) -> str:
        """Convert the first production country code to PT-BR name."""
        codes = self._country_codes(meta)
        if not codes or not codes[0]:
            return "Desconhecido"
        normal, historic = self._country_translations()
        return self._localized_country_name(codes[0], normal, historic)

    @staticmethod
    def _genre_values_from_list(values: list[object]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    @staticmethod
    def _genre_values_from_string(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @classmethod
    def _genre_list(cls, raw: object) -> list[str]:
        if isinstance(raw, list):
            return cls._genre_values_from_list(cast(list[object], raw))
        if isinstance(raw, str):
            return cls._genre_values_from_string(raw)
        return []

    @staticmethod
    def _localized_genre(genre: str) -> str:
        translated = ENG_TO_PTBR_GENRE_MAP.get(genre.lower(), genre)
        return translated.title() if translated != genre else genre

    def _localizer_genres(self, meta: Meta) -> str:
        """Convert genre names to PT-BR."""
        genres = self._genre_list(meta.genres or meta.combined_genres or "")
        if not genres:
            return "Desconhecido"
        return ", ".join(self._localized_genre(genre) for genre in genres)

    def _localizer_audio_language(self, meta: Meta) -> str:
        """
        Determine audio language(s) in PT-BR.

        Resolution order: meta audio_languages, mediainfo audio tracks,
        then meta original_language as last resort.
        """
        return (
            "Desconhecido"
            if not meta.audio_languages
            else ", ".join(
                self._get_lang_name(lang.strip())
                for lang in meta.audio_languages
            )
        )

    def _localizer_video_quality(self, meta: Meta) -> str:
        """Convert release type to a localised video quality label matching MakingOff options."""
        type_raw = (meta.type or "").upper()

        video_quality_ptbr: dict[str, str] = {
            "WEBDL": "Web DL",
            "WEBRIP": "Web DL",
            "BLURAY": "BDRip",
            "REMUX": "BR Remux",
            "ENCODE": "BDRip",
            "DISC": "Blu-Ray Full",
            "DVDRIP": "DVD Rip",
            "HDTV": "HDTV Rip",
            "TVRIP": "TV Rip",
            "VHSRIP": "VHS Rip",
            "CAM": "Outro",
        }

        return video_quality_ptbr.get(type_raw, "Outro")

    # -- IPB client methods

    @staticmethod
    def _tag_attribute(tag: object, attribute: str) -> str:
        if not hasattr(tag, "get"):
            return ""
        value = tag.get(attribute)  # type: ignore[union-attr]
        return str(value).strip() if value else ""

    def _get_csrf_token(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        html_token = self._tag_attribute(soup.find("html"), "data-csrf")
        if html_token:
            return html_token
        input_token = self._tag_attribute(
            soup.find("input", {"name": "_xfToken"}), "value"
        )
        if input_token:
            return input_token
        match = re.search(r'csrf:\s*["\']([^"\']+)["\']', html)
        return match.group(1).strip() if match else ""

    async def _session_home_html(self) -> str | None:
        try:
            response = await self.session.get(f"{self.base_url}/")
            if response.status_code != 403:
                response.raise_for_status()
            return response.text
        except httpx.HTTPError as error:
            response = getattr(error, "response", None)
            if response is not None:
                return cast(httpx.Response, response).text
            logger.error(f"{self.tracker}: Error validating session: {error}")
            return None

    @staticmethod
    def _page_logged_in(html: str) -> bool:
        tag = BeautifulSoup(html, "html.parser").find("html")
        return bool(tag and tag.get("data-logged-in") == "true")

    async def refresh_session(self) -> bool:
        html = await self._session_home_html()
        if html is None:
            return False
        if not self._page_logged_in(html):
            logger.warning(
                f"{self.tracker}: The session is unauthenticated. Check the cookie file."
            )
            return False
        self._csrf_token = self._get_csrf_token(html)
        await self.cookie_validator.save_session_cookies(
            self.tracker, cast(Any, self.session.cookies.jar)
        )
        return True

    async def _new_post_page(self, forum_id: int) -> str | None:
        url = f"{self.base_url}/forums/{forum_id}/post-thread"
        try:
            response = await self.session.get(url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as error:
            logger.error(
                f"{self.tracker}: Failed loading topic new page: {error}"
            )
            return None

    @staticmethod
    def _input_value(soup: BeautifulSoup, name: str) -> str:
        tag = soup.find("input", {"name": name})
        return str(tag.get("value", "")).strip() if tag else ""

    async def get_new_post_tokens(self, forum_id: int) -> tuple[str, str, str]:
        """Retrieve CSRF and attachment tokens required to create a topic."""
        html = await self._new_post_page(forum_id)
        if html is None:
            return "", "", ""
        if not self._page_logged_in(html):
            logger.warning(
                f"{self.tracker}: Unauthenticated session detected on this page."
            )
            return "", "", ""
        soup = BeautifulSoup(html, "html.parser")
        csrf_token = self._get_csrf_token(html)
        attachment_hash = self._input_value(soup, "attachment_hash")
        combined = self._input_value(soup, "attachment_hash_combined")
        if not csrf_token:
            logger.warning(
                f"{self.tracker}: It wasn't possible to extract xfToken. "
                "Check if the session is valid."
            )
        return csrf_token, attachment_hash, combined

    @staticmethod
    def _extract_post_height(text: str) -> int:
        """Extract a release height from MakingOff's current or legacy post layout."""
        match = re.search(
            r"Resolu[^\s:]*[:\s]+(\d{3,4})\s*[xX\u00d7]\s*(\d{3,4})", text
        )
        if match:
            return int(match.group(2))

        # Older generator posts did not render a ``Resolução`` field.  Their
        # release name still carries the usual 480p/720p/etc. marker.
        match = re.search(
            r"(?<!\d)(2160|1440|1080|720|576|540|480|432|360|240)[pi]\b",
            text,
            re.IGNORECASE,
        )
        return int(match.group(1)) if match else 0

    async def get_post_resolution(self, topic_url: str) -> int:
        """
        Fetches the topic resolution

        Returns:
            int: its resolution.
        """
        topic_url = re.sub(
            r"^https?://(www\.)?makingoff\.org",
            "https://makingoff.org",
            topic_url,
        )

        try:
            resp = await self.session.get(topic_url, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError:
            return 0

        soup = BeautifulSoup(resp.content, "html.parser")

        first_post = soup.find(class_="bbWrapper") or soup.find(
            "div", attrs={"itemprop": "commentText"}
        )
        if first_post:
            text = first_post.get_text(" ", strip=True)
            return self._extract_post_height(text)

        return 0

    def _parse_attachment_combined(self, value: str) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            parsed = json.loads(value)
        except Exception as error:
            logger.debug(
                f"{self.tracker}: Failed to parse attachment_hash_combined: {error}"
            )
            return None
        return (
            cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
        )

    def _attachment_context(
        self, attachment_hash_combined: str, forum_id: int
    ) -> tuple[str, dict[str, Any]]:
        combined = self._parse_attachment_combined(attachment_hash_combined)
        if combined is None:
            return "post", {"node_id": forum_id}
        attachment_type = str(combined.get("type", "post"))
        raw_context = combined.get("context", {})
        context = (
            cast(dict[str, Any], raw_context)
            if isinstance(raw_context, dict)
            else {}
        )
        return attachment_type, context

    @staticmethod
    def _attachment_payload(
        csrf_token: str,
        attachment_hash: str,
        attachment_type: str,
        context: dict[str, Any],
    ) -> dict[str, str]:
        payload = {
            "_xfToken": csrf_token,
            "_xfResponseType": "json",
            "hash": attachment_hash,
            "type": attachment_type,
        }
        for key, value in context.items():
            payload[f"context[{key}]"] = str(value)
        return payload

    @staticmethod
    def _attachment_mime_type(file_path: str, filename: str) -> str:
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            return mime_type
        return (
            "application/x-bittorrent"
            if filename.endswith(".torrent")
            else "application/octet-stream"
        )

    @staticmethod
    async def _attachment_bytes(file_path: str) -> bytes:
        async with aiofiles.open(file_path, "rb") as file_handle:
            return await file_handle.read()

    async def _post_attachment(
        self,
        url: str,
        payload: dict[str, str],
        filename: str,
        data: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        response = await self.session.post(
            url,
            data=payload,
            files={"upload": (filename, data, mime_type)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    @staticmethod
    def _attachment_error_message(data: dict[str, Any]) -> str:
        errors = data.get("errors", {})
        error_html = data.get("errorHtml", {})
        content = (
            error_html.get("content", "")
            if isinstance(error_html, dict)
            else ""
        )
        return str(content or errors)

    async def _upload_attachment_response(
        self,
        file_path: str,
        payload: dict[str, str],
        filename: str,
    ) -> dict[str, Any] | None:
        try:
            data = await self._attachment_bytes(file_path)
            mime_type = self._attachment_mime_type(file_path, filename)
            return await self._post_attachment(
                f"{self.base_url}/attachments/upload",
                payload,
                filename,
                data,
                mime_type,
            )
        except FileNotFoundError:
            logger.error(
                f"{self.tracker}: [bold red]File not found[/bold red]: {file_path}"
            )
            return None
        except httpx.HTTPError as error:
            logger.error(
                f"{self.tracker}: [bold red]Failed uploading attachment:[/bold red] {error}"
            )
            response = getattr(error, "response", None)
            if response is not None:
                logger.debug(
                    f"{self.tracker}: Response: {cast(httpx.Response, response).text}"
                )
            return None
        except ValueError as error:
            logger.error(
                f"{self.tracker}: [bold red]Failed to process upload response:[/bold red] {error}"
            )
            return None

    @staticmethod
    def _attachment_succeeded(response_data: dict[str, Any]) -> bool:
        return bool(
            response_data.get("status") == "ok"
            or "attachment" in response_data
        )

    async def upload_attachment(
        self,
        file_path: str,
        csrf_token: str,
        attachment_hash: str,
        attachment_hash_combined: str,
        forum_id: int,
    ) -> bool:
        """Upload a file as a XenForo forum attachment."""
        attachment_type, context = self._attachment_context(
            attachment_hash_combined, forum_id
        )
        payload = self._attachment_payload(
            csrf_token, attachment_hash, attachment_type, context
        )
        filename = Path(file_path).name
        response_data = await self._upload_attachment_response(
            file_path, payload, filename
        )
        if response_data is None:
            return False
        if self._attachment_succeeded(response_data):
            logger.info(
                f"{self.tracker}: [green]Attachment sent successfully: {filename}[/green]"
            )
            return True
        logger.error(
            f"{self.tracker}: [bold red]Unwanted response while uploading attachment "
            f"{filename}:[/bold red]\n{self._attachment_error_message(response_data)}"
        )
        return False

    @staticmethod
    def _search_payload(
        phrase: str,
        csrf_token: str,
        forum_id: int | None,
        title_only: bool,
    ) -> dict[str, str]:
        payload = {
            "keywords": phrase,
            "_xfToken": csrf_token,
            "_xfResponseType": "json",
        }
        if title_only:
            payload["c[title_only]"] = "1"
        if forum_id is not None:
            payload["c[nodes][0]"] = str(forum_id)
            payload["c[child_nodes]"] = "1"
        return payload

    async def _search_redirect(self, payload: dict[str, str]) -> str | None:
        try:
            response = await self.session.post(
                f"{self.base_url}/search/search",
                data=payload,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
        except httpx.HTTPError as error:
            logger.error(
                f"{self.tracker}: [bold red]Error on the search POST:[/bold red] {error}"
            )
            return None
        except ValueError as error:
            logger.error(
                f"{self.tracker}: [bold red]Unwanted response while searching POST:[/bold red] {error}"
            )
            return None
        redirect = data.get("redirect")
        if redirect:
            return str(redirect)
        errors = data.get("errors", {})
        if errors:
            logger.debug(f"{self.tracker}: Search errors: {errors}")
        return None

    def _absolute_forum_url(self, value: str) -> str:
        return urljoin(f"{self.base_url}/", value)

    @staticmethod
    def _href_text(value: object) -> str:
        if isinstance(value, list):
            return " ".join(str(item) for item in value).strip()
        return str(value or "").strip()

    def _search_item_result(self, item: Any) -> tuple[str, str] | None:
        anchor = item.find("a")
        if anchor is None:
            return None
        title = anchor.get_text(" ", strip=True)
        href = self._href_text(anchor.get("href", ""))
        if not href:
            return None
        return title, self._absolute_forum_url(href)

    @staticmethod
    def _store_search_result(
        results: dict[str, str], title: str, url: str
    ) -> None:
        if title not in results:
            results[title] = url
            return
        topic_id = url.rstrip("/").split(".")[-1]
        results[f"{title} ({topic_id})"] = url

    def _parse_search_page(self, html: str, results: dict[str, str]) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for item in soup.find_all(class_="contentRow-title"):
            parsed = self._search_item_result(item)
            if parsed is not None:
                self._store_search_result(results, *parsed)
        next_page = soup.select_one("a.pageNav-jump--next[href]")
        return self._href_text(next_page["href"]) if next_page else ""

    async def _search_page_html(self, page_url: str) -> str | None:
        try:
            response = await self.session.get(page_url)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as error:
            logger.error(
                f"{self.tracker}: [bold red]Error fetching search results page:[/bold red] {error}"
            )
            return None

    @staticmethod
    def _search_page_allowed(page_url: str, visited: set[str]) -> bool:
        return bool(page_url and page_url not in visited)

    async def _next_search_page(
        self, page_url: str, results: dict[str, str]
    ) -> str | None:
        html = await self._search_page_html(page_url)
        if html is None:
            return None
        next_page = self._parse_search_page(html, results)
        return self._absolute_forum_url(next_page) if next_page else ""

    def _warn_search_page_limit(
        self, page_url: str, visited: set[str]
    ) -> None:
        if not page_url or len(visited) != self.max_search_pages:
            return
        logger.warning(
            f"{self.tracker}: [yellow]Stopped duplicate search after "
            f"{self.max_search_pages} result pages.[/yellow]"
        )

    async def _collect_search_pages(self, start_url: str) -> dict[str, str]:
        results: dict[str, str] = {}
        visited: set[str] = set()
        page_url = self._absolute_forum_url(start_url)
        for _ in range(self.max_search_pages):
            if not self._search_page_allowed(page_url, visited):
                break
            visited.add(page_url)
            next_page = await self._next_search_page(page_url, results)
            if next_page is None:
                break
            page_url = next_page
        self._warn_search_page_limit(page_url, visited)
        return results

    async def search_candidate(
        self,
        phrase: str,
        forum_id: int | None = None,
        title_only: bool = True,
    ) -> dict[str, str] | None:
        """Search XenForo for a candidate release title."""
        if not self._csrf_token:
            await self.refresh_session()
        if not self._csrf_token:
            logger.error(
                f"{self.tracker}: Cannot search, no CSRF token available."
            )
            return None
        payload = self._search_payload(
            phrase, self._csrf_token, forum_id, title_only
        )
        redirect = await self._search_redirect(payload)
        if redirect is None:
            return None
        results = await self._collect_search_pages(redirect)
        return results or None

    @staticmethod
    def _card_matches_imdb(card: Any, imdb_pattern: re.Pattern[str]) -> bool:
        return any(
            imdb_pattern.search(str(anchor.get("href", "")))
            for anchor in card.select("a[href]")
        )

    @staticmethod
    def _card_topic_anchor(card: Any) -> Any | None:
        primary = card.select_one(".card-title a[href*='/topicos/']")
        return primary or card.select_one("a[href*='/topicos/']")

    @staticmethod
    def _card_year(card: Any) -> str:
        year_anchor = card.select_one("a[href^='?ano=']")
        return year_anchor.get_text(" ", strip=True) if year_anchor else ""

    @classmethod
    def _card_topic_result(cls, card: Any) -> tuple[str, str, str] | None:
        anchor = cls._card_topic_anchor(card)
        if anchor is None:
            return None
        title = anchor.get_text(" ", strip=True)
        href = str(anchor.get("href", "")).strip()
        if not title or not href:
            return None
        return title, href, cls._card_year(card)

    @staticmethod
    def _index_display_title(title: str, year: str) -> str:
        return f"{title} ({year})" if year.isdigit() else title

    @classmethod
    def _parse_index_results(cls, html: str, imdb_tt: str) -> dict[str, str]:
        """Extract only exact IMDb matches from the MakingOff catalogue cards."""
        soup = BeautifulSoup(html, "html.parser")
        pattern = re.compile(
            rf"/title/{re.escape(imdb_tt)}(?:[/?#]|$)", re.IGNORECASE
        )
        results: dict[str, str] = {}
        for card in soup.select(".filme-card"):
            if not cls._card_matches_imdb(card, pattern):
                continue
            parsed = cls._card_topic_result(card)
            if parsed is None:
                continue
            title, href, year = parsed
            display_title = cls._index_display_title(title, year)
            results[display_title] = urljoin(f"{cls.base_url}/", href)
        return results

    async def search_index_by_imdb(
        self, imdb_tt: str
    ) -> dict[str, str] | None:
        """Search the catalogue, which supports exact IMDb identifiers."""
        try:
            resp = await self.session.get(
                f"{self.base_url}/indice/", params={"q": imdb_tt}
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(
                f"{self.tracker}: [bold red]Error searching the catalogue by IMDb ID:[/bold red] {e}"
            )
            return None

        return self._parse_index_results(resp.text, imdb_tt) or None

    def get_topic_fields(
        self,
        forum_id: int,
        csrf_token: str,
        attachment_hash: str,
        attachment_hash_combined: str,
        topic_title: str,
        post_body: str,
    ) -> dict[str, str]:
        """
        Build the dictionary of form fields for creating a new XenForo topic.
        """
        return {
            "_xfToken": csrf_token,
            "prefix_id": "0",
            "title": topic_title,
            "discussion_type": "discussion",
            "message": post_body,
            "attachment_hash": attachment_hash,
            "attachment_hash_combined": attachment_hash_combined,
            "_xfSet[watch_thread]": "1",
            "_xfResponseType": "json",
            "_xfWithData": "1",
            "_xfRequestUri": f"/forums/{forum_id}/post-thread",
        }

    async def _post_topic(
        self, forum_id: int, fields: dict[str, str]
    ) -> dict[str, Any] | None:
        try:
            response = await self.session.post(
                f"{self.base_url}/forums/{forum_id}/post-thread",
                data=fields,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())
        except httpx.HTTPError as error:
            logger.error(f"{self.tracker}: Failed creating topic: {error}")
            response = getattr(error, "response", None)
            if response is not None:
                logger.debug(
                    f"{self.tracker}: Response: {cast(httpx.Response, response).text}"
                )
            return None
        except ValueError as error:
            logger.error(f"{self.tracker}: Failed to parse response: {error}")
            return None

    def _topic_redirect(self, data: dict[str, Any]) -> str:
        if data.get("status") != "ok" or "redirect" not in data:
            return ""
        return self._absolute_forum_url(str(data["redirect"]))

    @staticmethod
    def _response_error_message(data: dict[str, Any]) -> str:
        errors = data.get("errors", {})
        error_html = data.get("errorHtml", {})
        content = (
            error_html.get("content", "")
            if isinstance(error_html, dict)
            else ""
        )
        return str(content or errors)

    async def create_topic(
        self,
        forum_id: int,
        csrf_token: str,
        attachment_hash: str,
        attachment_hash_combined: str,
        topic_title: str,
        post_body: str,
    ) -> str:
        """Create a new forum topic and return its URL."""
        fields = self.get_topic_fields(
            forum_id=forum_id,
            csrf_token=csrf_token,
            attachment_hash=attachment_hash,
            attachment_hash_combined=attachment_hash_combined,
            topic_title=topic_title,
            post_body=post_body,
        )
        data = await self._post_topic(forum_id, fields)
        if data is None:
            return ""
        redirect = self._topic_redirect(data)
        if redirect:
            return redirect
        logger.error(
            f"{self.tracker}: [bold red]Failed creating topic:[/bold red]\n"
            f"{self._response_error_message(data)}"
        )
        return ""

    async def validate_credentials(self, meta: Meta) -> bool:
        """
        Validate tracker credentials and configure the authenticated session.

        Loads session cookies using CookieValidator.

        Args:
            meta: Release metadata.

        Returns:
            bool: True if the credentials are valid.
        """
        cookie_jar = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if not cookie_jar:
            return False

        self.session.cookies = cast(Any, cookie_jar)

        if not await self.refresh_session():
            logger.error(
                f"{self.tracker}: [bold red]Session couldn't be validated.[/bold red] Cookies may be expired."
            )
            return False

        return True

    @staticmethod
    def _is_brazilian_origin(meta: Meta) -> bool:
        return bool(meta.origin_country and "BR" in meta.origin_country)

    @staticmethod
    def _unique_titles(values: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    @classmethod
    def _duplicate_candidates(cls, meta: Meta, title_ptbr: str) -> list[str]:
        if cls._is_brazilian_origin(meta):
            return [title_ptbr]
        return cls._unique_titles(
            (title_ptbr, meta.title, meta.original_title)
        )

    @staticmethod
    def _search_result_conflicts(
        results: dict[str, str], title: str, url: str
    ) -> bool:
        return title in results and results[title] != url

    @staticmethod
    def _search_result_alias(title: str, topic_id: str, suffix: int) -> str:
        tail = topic_id if suffix == 1 else f"{topic_id}-{suffix}"
        return f"{title} ({tail})"

    @classmethod
    def _merge_search_result(
        cls, results: dict[str, str], title: str, url: str
    ) -> None:
        if not cls._search_result_conflicts(results, title, url):
            results[title] = url
            return
        topic_id = url.rstrip("/").split(".")[-1]
        suffix = 1
        result_title = title
        while cls._search_result_conflicts(results, result_title, url):
            result_title = cls._search_result_alias(title, topic_id, suffix)
            suffix += 1
        results[result_title] = url

    @classmethod
    def _merge_search_results(
        cls, results: dict[str, str], found: dict[str, str]
    ) -> None:
        for title, url in found.items():
            cls._merge_search_result(results, title, url)

    async def _search_exact_imdb(
        self,
        meta: Meta,
        results: dict[str, str],
        exact_urls: set[str],
    ) -> None:
        if not meta.imdb_tt:
            return
        logger.info(
            f"{self.tracker}: [yellow]Searching catalogue by IMDB ID:[/yellow] "
            f"{meta.imdb_tt}"
        )
        found = await self.search_index_by_imdb(meta.imdb_tt)
        if found:
            self._merge_search_results(results, found)
            exact_urls.update(found.values())

    async def _search_titles(
        self,
        candidates: list[str],
        forum_id: int,
        results: dict[str, str],
    ) -> None:
        for candidate in candidates:
            phrase = candidate.strip()
            logger.info(
                f"{self.tracker}: [yellow]Searching for title:[/yellow] {phrase}"
            )
            found = await self.search_candidate(
                phrase, forum_id=forum_id, title_only=True
            )
            if found:
                self._merge_search_results(results, found)

    @staticmethod
    def _unique_search_results(
        results: dict[str, str],
    ) -> list[tuple[str, str]]:
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for title, url in results.items():
            if url in seen:
                continue
            seen.add(url)
            unique.append((title, url))
        return unique

    @staticmethod
    def _resolution_height(value: str) -> int:
        try:
            return int(value.replace("p", "").replace("i", ""))
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _year_is_compatible(
        title: str, upload_year: str, exact_imdb: bool
    ) -> bool:
        if not upload_year or exact_imdb:
            return True
        year = int(upload_year)
        return any(
            f"({candidate})" in title
            for candidate in (year - 1, year, year + 1)
        )

    @staticmethod
    def _sd_blocked_by_hidef(
        uploading_hidef: bool, existing_hidef: bool
    ) -> bool:
        return not uploading_hidef and existing_hidef

    @staticmethod
    def _hidef_upgrade_allowed(
        uploading_hidef: bool, existing_hidef: bool
    ) -> bool:
        return uploading_hidef and not existing_hidef

    @classmethod
    def _duplicate_action(
        cls,
        uploading_hidef: bool,
        existing_hidef: bool,
        existing_height: int,
        upload_height: int,
    ) -> str:
        if cls._sd_blocked_by_hidef(uploading_hidef, existing_hidef):
            return "hidef_exists"
        if cls._hidef_upgrade_allowed(uploading_hidef, existing_hidef):
            return "allow_upgrade"
        return (
            "equivalent_or_better"
            if existing_height >= upload_height
            else "allow_upgrade"
        )

    @staticmethod
    def _duplicate_entry(
        title: str, url: str, resolution: int, include_size: bool
    ) -> dict[str, str]:
        return {
            "name": f"[url={url}]{title}[/url]",
            "size": str(resolution) if include_size else "",
            "link": url,
        }

    def _log_duplicate_action(
        self,
        action: str,
        title: str,
        url: str,
        default_config: dict[str, Any],
    ) -> None:
        label = (
            "A Hidef release exists"
            if action == "hidef_exists"
            else "A better or equivalent Hidef release exists"
        )
        logger.warning(
            f"{self.tracker}: [bold red]Aborting: {label}:[/bold red] "
            f"{format_terminal_link(title, url, default_config)}"
        )

    async def _existing_duplicate(
        self,
        meta: Meta,
        title: str,
        url: str,
        uploading_hidef: bool,
        upload_year: str,
        exact_imdb_urls: set[str],
        default_config: dict[str, Any],
    ) -> dict[str, str] | None:
        resolution = await self.get_post_resolution(url)
        existing_hidef = (
            title.strip().startswith("[Hidef]") or resolution > 576
        )
        exact_imdb = url in exact_imdb_urls
        if not self._year_is_compatible(title, upload_year, exact_imdb):
            logger.info(
                f"{self.tracker}: [yellow]Skipping: different year in existing "
                f"release:[/yellow] {format_terminal_link(title, url, default_config)}"
            )
            return None
        action = self._duplicate_action(
            uploading_hidef,
            existing_hidef,
            resolution,
            self._resolution_height(meta.resolution),
        )
        if action == "allow_upgrade":
            return None
        self._log_duplicate_action(action, title, url, default_config)
        if not meta.debug:
            meta.skipping = self.tracker
        return self._duplicate_entry(
            title,
            url,
            resolution,
            include_size=action == "equivalent_or_better",
        )

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        """Search for existing releases on the forum before uploading."""
        if not await self.validate_credentials(meta):
            return []
        title_ptbr = await self._resolve_display_title(meta)
        candidates = self._duplicate_candidates(meta, title_ptbr)
        forum_id = await self.get_forum_id(meta)
        results: dict[str, str] = {}
        exact_imdb_urls: set[str] = set()
        await self._search_exact_imdb(meta, results, exact_imdb_urls)
        await self._search_titles(candidates, forum_id, results)
        if not results:
            return []
        default_config = cast(dict[str, Any], self.config.get("DEFAULT", {}))
        duplicates: list[dict[str, str]] = []
        for title, url in self._unique_search_results(results):
            duplicate = await self._existing_duplicate(
                meta,
                title,
                url,
                self._is_hidef(meta),
                str(meta.year),
                exact_imdb_urls,
                default_config,
            )
            if duplicate is not None:
                duplicates.append(duplicate)
        return duplicates

    @staticmethod
    def _genres_text(meta: Meta) -> str:
        genres = meta.genres or meta.combined_genres or ""
        if isinstance(genres, list):
            return ", ".join(str(value) for value in genres)
        return str(genres)

    @classmethod
    def _is_documentary(cls, meta: Meta) -> bool:
        genres = cls._genres_text(meta).lower()
        return "documentary" in genres or "documentário" in genres

    @staticmethod
    def _origin_countries(meta: Meta) -> list[str]:
        if meta.origin_country:
            return [str(code) for code in meta.origin_country]
        return [
            str(country.get("iso_3166_1", ""))
            for country in meta.production_countries
            if country.get("iso_3166_1")
        ]

    @staticmethod
    def _forum_for_country(code: str) -> int | None:
        for forum_id, countries in FORUM_COUNTRY_GROUPS:
            if code in countries:
                return forum_id
        return None

    def _unattended_forum_default(
        self, meta: Meta, origin_countries: list[str]
    ) -> int | None:
        if not meta.unattended or meta.unattended_confirm:
            return None
        logger.info(
            f"{self.tracker}: [yellow]Unattended mode: Unmapped origin "
            f"country ({origin_countries}), using North-American (26) as "
            "default.[/yellow]"
        )
        return 26

    def _log_forum_options(self) -> None:
        for key, (forum_id, name) in FORUM_OPTIONS.items():
            logger.info(f"{self.tracker}:   {key}) {name} (ID: {forum_id})")

    @staticmethod
    def _selected_forum_id(choice: str) -> int | None:
        option = FORUM_OPTIONS.get(choice)
        return option[0] if option is not None else None

    async def _manual_forum_id(
        self, meta: Meta, origin_countries: list[str]
    ) -> int:
        logger.info(
            f"{self.tracker}: [bold yellow]Unmapped origin country "
            f"[/bold yellow]({origin_countries}). [bold yellow]Select the "
            "subforum manually:[/bold yellow]"
        )
        unattended_default = self._unattended_forum_default(
            meta, origin_countries
        )
        if unattended_default is not None:
            return unattended_default
        self._log_forum_options()
        choice = (
            await prompt_in_thread(cli_ui.ask_string, "Escolha: ") or ""
        ).strip()
        selected = self._selected_forum_id(choice)
        if selected is not None:
            return selected
        logger.warning(
            f"{self.tracker}: [yellow]Invalid option, using North-American "
            "(26) as default.[/yellow]"
        )
        return 26

    async def get_forum_id(self, meta: Meta) -> int:
        """Determine the target forum ID from genre, runtime, and origin."""
        if self._is_documentary(meta):
            return 28
        if 0 < meta.runtime < 40:
            return 77
        origin_countries = self._origin_countries(meta)
        for code in origin_countries:
            forum_id = self._forum_for_country(code)
            if forum_id is not None:
                return forum_id
        return await self._manual_forum_id(meta, origin_countries)

    # -- title resolution

    def _is_brazilian(self, meta: Meta) -> bool:
        """
        Detect whether the release is a Brazilian production.

        Checks origin_country and production_countries first; falls back to
        original_language == 'pt' for older/regional titles.

        Args:
            meta: Release metadata.

        Returns:
            bool: True if the release is considered Brazilian.
        """
        origin_countries: list[str] = meta.origin_country
        prod_codes = [
            c.get("iso_3166_1", "")
            for c in meta.production_countries
            if c.get("iso_3166_1")
        ]
        if "BR" in origin_countries or "BR" in prod_codes:
            return True
        return str(meta.original_language).lower() == "pt"

    def _find_translation_title(
        self,
        ptbr_main_or_en_main: dict[str, Any],
        iso_639_1: str,
        iso_3166_1: str | None = None,
    ) -> str:
        translations = ptbr_main_or_en_main.get("translations", {}).get(
            "translations", []
        )
        primary: dict[str, Any] | None = next(
            (
                t
                for t in translations
                if t.get("iso_639_1") == iso_639_1
                and (iso_3166_1 is None or t.get("iso_3166_1") == iso_3166_1)
            ),
            None,
        )
        if not primary and iso_3166_1:
            primary = next(
                (t for t in translations if t.get("iso_639_1") == iso_639_1),
                None,
            )
        return (primary or {}).get("data", {}).get("title", "") or ""

    async def _resolve_display_title(self, meta: Meta) -> str:
        """
        Resolve the display title, preferring PT-BR.

        For Brazilian films, tries PT-BR first then falls back to
        original_title. For foreign films, tries PT-BR then English
        when the native and original titles are identical.

        The resolved title is cached on the tracker instance (keyed by
        ``meta.uuid``) so that repeated calls within the same upload do
        not trigger extra TMDB requests.

        Args:
            meta: Release metadata.

        Returns:
            str: Resolved display title.
        """
        cache_key: str = meta.uuid
        if cache_key and cache_key in self._display_title_cache:
            return self._display_title_cache[cache_key]

        title_native = meta.title
        title_orig = meta.original_title

        ptbr_main = meta.tmdb_localized_data.get("pt-BR", {}).get("main", {})
        en_main = meta.tmdb_localized_data.get("en-US", {}).get("main", {})

        if self._is_brazilian(meta):
            if ptbr_main:
                ptbr = self._find_translation_title(ptbr_main, "pt", "BR")
                if ptbr:
                    title_native = ptbr
                elif title_orig:
                    title_native = title_orig
        else:
            if ptbr_main:
                ptbr = self._find_translation_title(ptbr_main, "pt", "BR")
                if ptbr and ptbr.lower() != title_orig.lower():
                    title_native = ptbr
                elif title_native.lower() == title_orig.lower() and en_main:
                    en = self._find_translation_title(en_main, "en", "US")
                    if en and en.lower() != title_orig.lower():
                        title_native = en

        if cache_key:
            self._display_title_cache[cache_key] = title_native
        return title_native

    async def get_name(self, meta: Meta) -> str:
        """
        Generate the forum topic title.

        Format for Brazilian films:  [Hidef] PT-BR Title (Year)
        Format for foreign films:    [Hidef] PT-BR Title / Original Title (Year)

        Args:
            meta (dict[str, Any]): Release metadata.

        Returns:
            str: Formatted topic title.
        """
        prefix = "[Hidef] " if self._is_hidef(meta) else ""

        title_ptbr = await self._resolve_display_title(meta)
        year: str = str(meta.year) if meta.year else ""

        if self._is_brazilian(meta):
            title_part = title_ptbr
        else:
            title_orig = meta.original_title
            title_part = (
                f"{title_ptbr} / {title_orig}"
                if title_orig and title_orig.lower() != title_ptbr.lower()
                else title_ptbr
            )

        return (
            f"{prefix}{title_part} ({year})"
            if year
            else f"{prefix}{title_part}"
        )

    # -- description generation

    def _extract_image_urls(self, meta: Meta) -> list[str]:
        """
        Extract screenshot URLs from meta image_list.

        Handles both plain URL strings and dict entries produced by
        various image host modules.

        Args:
            meta (dict[str, Any]): Release metadata.

        Returns:
            list[str]: Resolved image URLs.
        """
        urls: list[str] = []
        image_list = (
            cast(list[dict[str, Any]], meta.menu_images)
            + meta.image_list
            + meta.spectrograms_images
            + meta.dynamic_hdr_plot_images
        )
        for img in image_list:
            if isinstance(img, str):
                urls.append(img)
            elif isinstance(img, dict):
                url = (
                    img.get("raw_url")
                    or img.get("img_url")
                    or img.get("url")
                    or img.get("web_url")
                    or ""
                )
                if url:
                    urls.append(url)
        return urls

    async def _subtitles_ptbr(self, meta: Meta) -> str:
        """
        Prompt the user to select a subtitle type.

        Returns:
            str: Selected subtitle type label.
        """
        if not meta.language_checked:
            await languages_manager.process_desc_language(
                meta, tracker=self.tracker
            )
        portuguese_languages = {"portuguese", "português", "pt"}

        meta_subtitle_languages = (
            meta.subtitle_languages if meta.subtitle_languages else []
        )
        found_languages = {lang.lower() for lang in meta_subtitle_languages}

        # Check if we have external Portuguese subtitles or embedded ones.
        # If we have external Portuguese subtitle files, they will be uploaded as attachments ("Anexas").
        has_external_pt_sub = False
        for sub_file in getattr(meta, "subtitle_files", []):
            if not Path(sub_file).exists():
                continue
            name_lower = Path(sub_file).name.lower()
            if any(
                term in name_lower
                for term in (
                    ".pt",
                    ".pt-br",
                    ".por",
                    "portuguese",
                    "ptbr",
                    "pt_br",
                )
            ) or self._is_subtitle_in_portuguese(sub_file):
                has_external_pt_sub = True
                break

        if has_external_pt_sub:
            return "Anexas"

        if any(lang in portuguese_languages for lang in found_languages):
            return "Embutidas"

        # Fallback to asking
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: Subtitles not determined, defaulting to 'Sem Legenda'.[/yellow]"
            )
            return "Sem Legenda"

        options = {
            "1": "No torrent",
            "2": "Anexas",
            "3": "Embutidas",
            "4": "Fixas",
            "5": "Sem Legenda",
        }
        logger.info(f"{self.tracker}: [yellow]Any subtitles?[/yellow]")
        for k, v in options.items():
            logger.info(f"{self.tracker}:   {k}) {v}")
        selection = (
            await prompt_in_thread(cli_ui.ask_string, "Choose: ") or ""
        ).strip()
        return options.get(selection, "Sem Legenda")

    async def generate_description(self, meta: Meta) -> str:
        """
        Generate the BBCode description for the forum post.

        Args:
            meta (dict[str, Any]): Release metadata.

        Returns:
            str: Formatted BBCode description.
        """
        title_br = await self._resolve_display_title(meta)
        title_orig = (
            title_br
            if self._is_brazilian(meta)
            else meta.original_title or title_br
        )

        release_name = meta.basename_no_ext or meta.name or meta.uuid
        release = release_name.replace(" ", ".")

        # Prefer TMDB PT-BR overview already cached by the UA; fall back to
        # translation details from the pre-fetched translations list.
        ptbr_main = dict(meta.tmdb_localized_data.get("pt-BR", {})).get(
            "main", {}
        )
        en_main = dict(meta.tmdb_localized_data.get("en-US", {})).get(
            "main", {}
        )

        poster_raw = ptbr_main.get("poster_path") or meta.tmdb_poster_path
        poster_url = (
            poster_raw
            if poster_raw.startswith("http")
            else f"https://image.tmdb.org/t/p/original{poster_raw}"
            if poster_raw
            else ""
        )

        pt_overview = ""
        if ptbr_main:
            translations = ptbr_main.get("translations", {}).get(
                "translations", []
            )
            for iso_3166_1 in ("BR", None):
                match = next(
                    (
                        t
                        for t in translations
                        if t.get("iso_639_1") == "pt"
                        and (
                            iso_3166_1 is None
                            or t.get("iso_3166_1") == iso_3166_1
                        )
                    ),
                    None,
                )
                if match:
                    pt_overview = match.get("data", {}).get("overview", "")
                    if pt_overview:
                        break

        overview = ptbr_main.get("overview") or pt_overview or meta.overview

        # Romanize cast names by pulling from en-US main data, slice to 10 and join with comma, matching the JS generator
        cast_list: list[dict[str, Any]] = (
            cast(
                list[dict[str, Any]],
                en_main.get("credits", {}).get("cast", [])[:10],
            )
            if en_main
            else []
        )
        cast_names: list[str] = [
            cast(str, member.get("name"))
            for member in cast_list
            if member.get("name")
        ]
        cast_text = ", ".join(cast_names)

        # Romanize director name
        tmdb_dirs: list[str] = (
            [
                cast(str, member.get("name"))
                for member in cast(
                    list[dict[str, Any]],
                    en_main.get("credits", {}).get("crew", []),
                )
                if member.get("job") == "Director" and member.get("name")
            ]
            if en_main
            else []
        )
        imdb_dirs: list[str] = [
            name
            for name in cast(
                list[Any], meta.imdb_info.get("directors", []) or []
            )
            if isinstance(name, str)
        ]
        directors = ", ".join(tmdb_dirs if tmdb_dirs else imdb_dirs)

        imdb_url = ""
        if meta.imdb_id or meta.imdb_info.get("imdb_url"):
            imdb_url = (
                meta.imdb_info.get("imdb_url")
                or f"https://www.imdb.com/title/tt{str(meta.imdb_id).zfill(7)}/"
            )

        homepage_url = (
            ptbr_main.get("homepage") or en_main.get("homepage") or ""
        )

        # Extract tracks from meta.mediainfo
        tracks: list[dict[str, Any]] = cast(
            list[dict[str, Any]],
            meta.mediainfo.get("media", {}).get("track", []),
        )
        video_track: dict[str, Any] = next(
            (track for track in tracks if track.get("@type") == "Video"), {}
        )
        audio_track: dict[str, Any] = next(
            (track for track in tracks if track.get("@type") == "Audio"), {}
        )
        general_track: dict[str, Any] = next(
            (track for track in tracks if track.get("@type") == "General"), {}
        )

        width, height = meta.video_width or 0, meta.video_height or 0

        # Optional fields from meta
        awards = (
            getattr(meta, "awards", "")
            or getattr(meta, "premiacoes", "")
            or ""
        )
        trivia = (
            getattr(meta, "trivia", "")
            or getattr(meta, "curiosidades", "")
            or ""
        )
        critic = (
            getattr(meta, "critic", "") or getattr(meta, "critica", "") or ""
        )

        return self._build_bbcode(
            title_br=title_br,
            title_orig=title_orig,
            release=release,
            poster_url=poster_url,
            overview=overview,
            image_urls=self._extract_image_urls(meta),
            cast_text=cast_text,
            genres=self._localizer_genres(meta),
            directors=directors,
            duration=str(
                meta.runtime
                or self._mediainfo_duration(general_track, video_track)
                or ""
            ),
            year=str(getattr(meta, "year", "") or ""),
            countries=self._localizer_countries(meta),
            audio=self._localizer_audio_language(meta),
            subs=await self._subtitles_ptbr(meta),
            imdb_url=imdb_url,
            homepage_url=homepage_url,
            quality=self._localizer_video_quality(meta),
            container=self._mediainfo_container(
                general_track,
                fallback=(getattr(meta, "container", "") or "").upper(),
            ),
            video_codec=self._mediainfo_video_codec(meta, video_track),
            video_brate=str(meta.video_bitrate),
            audio_codec=self._mediainfo_audio_codec(meta, audio_track),
            audio_brate=str(meta.audio_bitrate),
            res_str=f"{width}x{height}",
            aspect=self._aspect_ratio(width, height),
            fps_str=f"{meta.frame_rate:.3f} FPS"
            if meta.frame_rate
            else "23.976 FPS",
            filesize=self._mediainfo_filesize(meta),
            awards=awards,
            trivia=trivia,
            critic=critic,
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        """
        Validate tracker-specific requirements before uploading.

        Args:
            meta (dict[str, Any]): Release metadata.

        Returns:
            bool: True if the release meets all requirements.
        """
        if str(getattr(meta, "category", "")).upper() != "MOVIE":
            logger.warning(
                f"{self.tracker}: [bold red]Only films may be uploaded to this forum.[/bold red]"
            )
            return False

        if bool(
            getattr(meta, "adult_media", False)
            or getattr(meta, "tmdb_adult_media", False)
        ):
            logger.warning(
                f"{self.tracker}: [bold red]Adult releases are not allowed on this forum.[/bold red]"
            )
            return False

        if meta.is_disc and meta.is_disc != "DVD":
            logger.warning(
                f"{self.tracker}: [bold red]Only complete DVD structures are allowed; Blu-ray/HDDVD structures must be remuxed to MKV.[/bold red]"
            )
            return False

        if not meta.is_disc and meta.container.upper() not in ("MKV", "AVI"):
            logger.warning(
                f"{self.tracker}: [bold red]Only MKV/AVI containers are allowed on this forum.[/bold red]"
            )
            return False

        video = f"{getattr(meta, 'video_codec', '')} {getattr(meta, 'video_encode', '')}".upper()
        if any(codec in video for codec in ("HEVC", "H.265", "H265", "X265")):
            logger.warning(
                f"{self.tracker}: [bold red]HEVC/H.265 video is not allowed on this forum.[/bold red]"
            )
            return False

        if not meta.is_disc and self._is_hidef(meta):
            if not any(
                codec in video for codec in ("H264", "H.264", "AVC", "X264")
            ):
                logger.warning(
                    f"{self.tracker}: [bold red]High-definition releases must use H.264/AVC video.[/bold red]"
                )
                return False

            try:
                bitrate = int(meta.video_bitrate or 0)
                height = int(meta.video_height or 0)
            except TypeError, ValueError:
                bitrate, height = 0, 0
            minimum = (
                5000
                if height >= 1080
                or str(meta.resolution)
                in {"1080i", "1080p", "1440p", "2160p", "4320p"}
                else 2200
            )
            if bitrate and bitrate < minimum:
                logger.warning(
                    f"{self.tracker}: [yellow]HD bitrate is {bitrate} kbps; the forum normally requires at least {minimum} kbps. "
                    "TV/internet captures require a manual quality review.[/yellow]"
                )

        release = self._release_tokens(meta)
        prohibited_release = re.search(
            r"(?:^|[. _-])(cam|telesync|ts|telecine|tc|r5|dvdscr(?:eener)?|hdrip|vodrip|axxo|cm8|yify|yts|stuttershit)(?:$|[. _-])",
            release,
            re.IGNORECASE,
        )
        if prohibited_release:
            logger.warning(
                f"{self.tracker}: [bold red]Prohibited/low-quality release marker found: {prohibited_release.group(1)}.[/bold red]"
            )
            return False

        prohibited_files = {
            ".zip",
            ".rar",
            ".7z",
            ".exe",
            ".msi",
            ".bat",
            ".cmd",
            ".com",
            ".scr",
            ".ps1",
            ".sh",
        }
        bad_file = next(
            (
                Path(str(item)).name
                for item in getattr(meta, "filelist", []) or []
                if Path(str(item)).suffix.lower() in prohibited_files
            ),
            "",
        )
        if bad_file:
            logger.warning(
                f"{self.tracker}: [bold red]Torrent contains prohibited archive/executable file: {bad_file}.[/bold red]"
            )
            return False

        if not self._has_portuguese_subtitle(meta):
            logger.warning(
                f"{self.tracker}: [bold red]A Portuguese subtitle is required for this forum.[/bold red]"
            )
            return False

        return True

    async def upload(self, meta: Meta) -> bool:
        """
        Upload a release by creating a forum topic with the torrent as attachment.

        Args:
            meta (dict[str, Any]): Release metadata.

        Returns:
            bool: True if the upload succeeded.
        """
        forum_id = await self.get_forum_id(meta)
        logger.info(
            f"{self.tracker}: [green]Selected subforum:[/green] {forum_id} "
        )
        # Extract before creating the torrent so a non-hardcoded embedded
        # Portuguese subtitle can be included in the torrent as well as
        # attached separately to the forum post.
        sub_files = await self._get_portuguese_subtitles(meta)
        if (
            not sub_files
            and not getattr(meta, "hardcoded_subs", False)
            and not meta.debug
        ):
            logger.warning(
                f"{self.tracker}: [bold red]Unable to provide a separate Portuguese subtitle file.[/bold red]"
            )
            meta["tracker_status"][self.tracker]["status_message"] = (
                "Upload blocked: no separate Portuguese subtitle file."
            )
            return False

        if sub_files:
            existing = list(getattr(meta, "subtitle_files", []) or [])
            meta.subtitle_files = list(dict.fromkeys([*existing, *sub_files]))

        await self.common.create_torrent_for_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            is_public=True,
            public_trackers=self._public_trackers,
        )
        torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}].torrent"

        # Creates a copy of the torrent with the media filename,
        # this one should be attached to the topic.
        release_name = meta.basename_no_ext or meta.name or meta.uuid
        release_filename = release_name.replace(" ", ".")
        named_torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/{release_filename}.torrent"
        shutil.copy2(torrent_path, named_torrent_path)

        # Zip subtitles to comply with MakingOff allowed formats (.torrent, .rar, .zip)
        if sub_files:
            temp_dir = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}"
            zip_path = str(Path(temp_dir) / f"{release_filename}.legendas.zip")
            try:
                with zipfile.ZipFile(
                    zip_path, "w", zipfile.ZIP_DEFLATED
                ) as zipf:
                    for sub_file in sub_files:
                        zipf.write(sub_file, arcname=Path(sub_file).name)
                logger.info(
                    f"{self.tracker}: [green]Zipped {len(sub_files)} subtitles to {Path(zip_path).name}[/green]"
                )
                sub_files = [zip_path]
            except (OSError, zipfile.BadZipFile) as e:
                logger.error(
                    f"{self.tracker}: [red]Failed to create zip file for subtitles: {e}[/red]"
                )
                if not meta.debug:
                    meta["tracker_status"][self.tracker]["status_message"] = (
                        "data error: Failed to package Portuguese subtitles."
                    )
                    return False
                sub_files = []

        if meta.debug:
            topic_title = await self.get_name(meta)
            post_body = await self.generate_description(meta)

            fields = self.get_topic_fields(
                forum_id=forum_id,
                csrf_token="DEBUG_CSRF",  # noqa: S106
                attachment_hash="DEBUG_HASH",
                attachment_hash_combined="DEBUG_COMBINED",
                topic_title=topic_title,
                post_body=post_body,
            )

            logger.info(f"{self.tracker}: [cyan]Request Data:[/cyan]")
            logger.info(Redaction.redact_private_info(fields))

            if sub_files:
                logger.info(
                    f"{self.tracker}: [cyan]Debug Subtitles to upload:[/cyan] {sub_files}"
                )

            txt_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt"
            async with aiofiles.open(txt_path, "w", encoding="utf-8") as f:
                await f.write(f"TITULO: {topic_title}\n\n")
                await f.write(post_body)
            logger.info(
                f"{self.tracker}: [yellow]BBCode saved.[/yellow] {txt_path}"
            )
            meta["tracker_status"][self.tracker]["status_message"] = (
                "Debug mode enabled, not uploading (simulated successfully)"
            )
            return True

        # The UA instantiates a fresh tracker object for the upload step,
        # so credentials must be loaded again here.
        if not await self.validate_credentials(meta):
            meta["tracker_status"][self.tracker]["status_message"] = (
                "data error: Failed to validate credentials before upload."
            )
            return False

        (
            csrf_token,
            attachment_hash,
            attachment_hash_combined,
        ) = await self.get_new_post_tokens(forum_id)
        if not csrf_token or not attachment_hash:
            meta["tracker_status"][self.tracker]["status_message"] = (
                "data error: Failed to retrieve XenForo tokens."
            )
            return False

        if not await self.upload_attachment(
            named_torrent_path,
            csrf_token,
            attachment_hash,
            attachment_hash_combined,
            forum_id,
        ):
            meta["tracker_status"][self.tracker]["status_message"] = (
                "data error: Failed to upload .torrent attachment."
            )
            return False

        # Upload Portuguese subtitles if any
        for sub_file in sub_files:
            logger.info(
                f"{self.tracker}: [yellow]Uploading Portuguese subtitle as attachment:[/yellow] {Path(sub_file).name}"
            )
            if not await self.upload_attachment(
                sub_file,
                csrf_token,
                attachment_hash,
                attachment_hash_combined,
                forum_id,
            ):
                meta["tracker_status"][self.tracker]["status_message"] = (
                    "data error: Failed to upload Portuguese subtitle attachment."
                )
                return False

        topic_title = await self.get_name(meta)
        post_body = await self.generate_description(meta)

        topic_url = await self.create_topic(
            forum_id=forum_id,
            csrf_token=csrf_token,
            attachment_hash=attachment_hash,
            attachment_hash_combined=attachment_hash_combined,
            topic_title=topic_title,
            post_body=post_body,
        )

        if topic_url:
            meta["tracker_status"][self.tracker]["status_message"] = (
                "Upload successful"
            )
            meta["tracker_status"][self.tracker]["torrent_id"] = topic_url
            return True

        meta["tracker_status"][self.tracker]["status_message"] = (
            "data error: Failed creating the forum topic."
        )
        return False
