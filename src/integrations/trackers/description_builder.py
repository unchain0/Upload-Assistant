# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import html
import json
import os
import re
import urllib.parse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from urllib.parse import ParseResult

import aiofiles
import httpx
import langcodes
from jinja2 import Template
from langcodes.tag_parser import LanguageTagError

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.filesystem.screenshot_manifest import (
    files as manifest_files,
)
from src.integrations.image_hosts.uploader import UploadScreensManager
from src.integrations.media.language_adapter import languages_manager
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.screenshot_capture import TakeScreensManager
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import PathAwareEncoder
from src.integrations.trackers.bbcode_formatting import BBCODE
from src.integrations.trackers.common import Common


def html_to_bbcode(text: str) -> str:
    """Convert HTML tags to BBCode format."""
    if not text:
        return text

    # Clean up <br> tags adjacent to list item tags to prevent empty lines
    text = re.sub(r"<br\s*/?>\s*</li>", "</li>", text, flags=re.IGNORECASE)
    text = re.sub(r"<li>\s*<br\s*/?>", "<li>", text, flags=re.IGNORECASE)

    # Define HTML to BBCode tag mappings
    html_bbcode_map = [
        (r"<b>(.*?)</b>", r"[b]\1[/b]"),
        (r"<i>(.*?)</i>", r"[i]\1[/i]"),
        (r"<u>(.*?)</u>", r"[u]\1[/u]"),
        (r"<s>(.*?)</s>", r"[s]\1[/s]"),
        (r"<em>(.*?)</em>", r"[i]\1[/i]"),
        (r"<strong>(.*?)</strong>", r"[b]\1[/b]"),
        (r"<strike>(.*?)</strike>", r"[s]\1[/s]"),
        (r"<del>(.*?)</del>", r"[s]\1[/s]"),
        (r"<br\s*/?>", r"\n"),
        (r"<br>", r"\n"),
        (r"<p>(.*?)</p>", r"\1\n"),
        (r"<li>(.*?)</li>", r"* \1\n"),
        (r"<li>", r"* "),
        (r"</li>", r"\n"),
        (r"<ul[^>]*>", r""),
        (r"</ul>", r""),
    ]

    converted_text = text
    for html_pattern, bbcode_replacement in html_bbcode_map:
        converted_text = re.sub(
            html_pattern,
            bbcode_replacement,
            converted_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # Strip any residual HTML tags
    return re.sub(r"<[^>]+>", "", converted_text)


def _clean_description_text(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _description_template_path(meta: Meta) -> Path:
    return (
        Path(meta.base_dir)
        / "data"
        / "templates"
        / f"{meta.description_template}.txt"
    )


async def _template_description(meta: Meta) -> str:
    if not meta.description_template:
        return ""
    try:
        async with aiofiles.open(
            _description_template_path(meta), encoding="utf-8"
        ) as handle:
            template = Template(await handle.read())
    except FileNotFoundError:
        logger.info(
            f"[ERROR] Template '{meta.description_template}' not found."
        )
        return ""
    rendered = template.render(meta)
    cleaned = _clean_description_text(rendered)
    if cleaned:
        meta.description_template_content = cleaned
    return cleaned


def _nfo_mode(meta: Meta) -> str:
    if "auto_nfo" in meta and meta.auto_nfo is True:
        return "scene"
    if "bhd_nfo" in meta and meta.bhd_nfo is True:
        return "bhd"
    return "normal"


def _nfo_files(specified_dir: Path, source_dir: Path, mode: str) -> list[str]:
    root = specified_dir if mode in {"scene", "bhd"} else source_dir
    logger.debug(f"specified_dir_path: {specified_dir}")
    logger.debug(f"sourcedir_path: {source_dir}")
    return sorted(str(path) for path in root.glob("*.nfo"))


async def _read_nfo_text(path: str) -> str:
    try:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            text = await handle.read()
        logger.debug("NFO content read with utf-8 encoding.")
        return text
    except UnicodeDecodeError:
        logger.debug("utf-8 decoding failed, trying latin1.")
        async with aiofiles.open(path, encoding="latin1") as handle:
            return await handle.read()


def _nfo_description_markup(mode: str, content: str) -> str:
    if mode == "scene":
        return f"[center][spoiler=Scene NFO:][code]{content}[/code][/spoiler][/center]"
    if mode == "bhd":
        return f"[center][spoiler=FraMeSToR NFO:][code]{content}[/code][/spoiler][/center]"
    return f"[code]{content}[/code]"


async def _apply_description_nfo(
    meta: Meta,
    description_lines: list[str],
    content_written: bool,
    specified_dir: Path,
    source_dir: Path,
) -> tuple[bool, bool]:
    if not meta.nfo:
        return content_written, False
    mode = _nfo_mode(meta)
    files = _nfo_files(specified_dir, source_dir, mode)
    if not files:
        logger.info("NFO was set but no nfo file was found")
        if not content_written:
            description_lines.append("")
        return content_written, True
    content = await _read_nfo_text(files[0])
    if not content_written:
        description_lines.append(_nfo_description_markup(mode, content))
        content_written = True
    meta.description_nfo_content = content.encode("utf-8", "ignore").decode(
        "utf-8"
    )
    return content_written, False


def _raw_description_url(description_link: str) -> str:
    parsed: ParseResult = urllib.parse.urlparse(
        description_link.replace("/raw/", "/")
    )
    split = os.path.split(parsed.path)
    path = (
        f"{split[0]}/raw/{split[1]}"
        if split[0] != "/"
        else f"/raw{parsed.path}"
    )
    return urllib.parse.urlunparse(parsed._replace(path=path))


async def _fetch_description_link(description_link: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(_raw_description_url(description_link))
    except Exception as error:
        logger.info(f"[ERROR] Failed to fetch description from link: {error}")
        raise
    return _clean_description_text(response.text)


def _accepted_description_link_text(cleaned: str) -> str:
    if cleaned and "Not Found" in cleaned:
        logger.error("Description link returned 'Not Found'")
        return ""
    return cleaned


async def _description_link_text(meta: Meta) -> str:
    description_link = str(meta.description_link or "")
    if not description_link:
        return ""
    cleaned = _accepted_description_link_text(
        await _fetch_description_link(description_link)
    )
    if cleaned:
        meta.description_link_content = cleaned
    return cleaned


async def _description_file_text(meta: Meta) -> str:
    description_file = str(meta.description_file or "")
    if not description_file or not Path(description_file).is_file():
        return ""
    async with aiofiles.open(description_file, encoding="utf-8") as handle:
        cleaned = _clean_description_text(await handle.read())
    if cleaned:
        meta.description_file_content = cleaned
    return cleaned


def _append_first_description_source(
    lines: list[str], text: str, content_written: bool
) -> bool:
    if not text:
        return content_written
    if not content_written:
        lines.append(text)
    return True


def _finalize_generated_description(meta: Meta, lines: list[str]) -> Meta:
    meta.description = "\n".join(lines).strip()
    meta.saved_description = bool(meta.description)
    if meta.description in ("None", "", " "):
        meta.description = ""
    return meta


async def gen_desc(
    meta: Meta,
    _takescreens_manager: TakeScreensManager,
    _uploadscreens_manager: UploadScreensManager,
) -> Meta:
    lines: list[str] = []
    template_text = await _template_description(meta)
    content_written = _append_first_description_source(
        lines, template_text, False
    )
    specified_dir = Path(meta.base_dir) / "tmp" / meta.uuid
    source_dir = Path(meta.path or "")
    content_written, nfo_missing = await _apply_description_nfo(
        meta, lines, content_written, specified_dir, source_dir
    )
    if nfo_missing:
        return _finalize_generated_description(meta, lines)
    link_text = await _description_link_text(meta)
    content_written = _append_first_description_source(
        lines, link_text, content_written
    )
    file_text = await _description_file_text(meta)
    content_written = _append_first_description_source(
        lines, file_text, content_written
    )
    if not content_written:
        existing = _clean_description_text(str(meta.description or ""))
        _append_first_description_source(lines, existing, False)
    return _finalize_generated_description(meta, lines)


@dataclass(frozen=True)
class _ScreenshotContext:
    screenheader: str | None
    pack_images_data: dict[str, Any]
    approved_image_hosts: tuple[str, ...]
    char_limit: int
    file_limit: int
    thumb_size: int
    process_limit: int
    screens_per_row: int
    multi_screens: int


@dataclass
class _MultiFileRenderState:
    parts: list[str] = field(default_factory=list)
    char_count: int = 0
    spoiler_open: bool = False


class DescriptionBuilder:
    def __init__(self, tracker: str, config: dict[str, Any]):
        self.config: dict[str, Any] = config
        self.common = Common(config)
        self.tracker: str = tracker
        self.takescreens_manager = TakeScreensManager(config)
        self.uploadscreens_manager = UploadScreensManager(config)

        trackers_config = self.config.get("TRACKERS")
        if not isinstance(trackers_config, dict):
            raise KeyError("Missing 'TRACKERS' section in config")
        trackers_config_map = cast(dict[str, Any], trackers_config)

        tracker_cfg = trackers_config_map.get(tracker)
        if tracker_cfg is None:
            available = list(trackers_config_map.keys())
            raise KeyError(
                f"Missing tracker config for '{tracker}'; available trackers: {available}"
            )

        self.tracker_config: dict[str, Any] = (
            cast(dict[str, Any], tracker_cfg)
            if isinstance(tracker_cfg, dict)
            else {}
        )
        self.parser = self.common.parser

    def _default_config(self) -> dict[str, Any]:
        raw = self.config.get("DEFAULT", {})
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}

    def _tracker_or_default_config_value(self, key: str, default: Any) -> Any:
        value = self.tracker_config.get(key)
        if value in (None, ""):
            return self._default_config().get(key, default)
        return value

    @staticmethod
    def _string_bool_config(value: str) -> bool | None:
        normalized = value.casefold().strip()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
        return None

    @classmethod
    def _coerce_bool_config(cls, value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            parsed = cls._string_bool_config(value)
            if parsed is not None:
                return parsed
        try:
            return bool(int(value))
        except ValueError, TypeError:
            return default

    def _get_bool_config(self, key: str, default: bool = False) -> bool:
        """Get a boolean config value with tracker-over-default precedence."""
        value = self._tracker_or_default_config_value(key, default)
        return self._coerce_bool_config(value, default)

    def _get_int_config(self, key: str, default: Any = 0) -> int:
        """Helper to get an integer config value safely. Falls back to DEFAULT or default if invalid/empty."""
        val = self.tracker_config.get(key)
        if val is None or val == "":
            val = self.config["DEFAULT"].get(key, default)

        try:
            return int(val)
        except ValueError, TypeError:
            try:
                return int(default)
            except ValueError, TypeError:
                return 0

    @staticmethod
    def _normalized_release_tag(meta: Meta | None) -> str:
        if meta is None or not meta.tag:
            return ""
        return str(meta.tag).strip().lstrip("-").casefold()

    @staticmethod
    def _normalized_configured_tag(value: Any) -> str:
        return str(value).strip().lstrip("-").casefold()

    @classmethod
    def _matching_tag_override_map(
        cls, source: dict[str, Any], tag: str
    ) -> dict[str, Any] | None:
        raw = source.get("tag_overrides", {})
        if not isinstance(raw, dict):
            return None
        for configured_tag, overrides in cast(dict[Any, Any], raw).items():
            if cls._normalized_configured_tag(configured_tag) != tag:
                continue
            return (
                cast(dict[str, Any], overrides)
                if isinstance(overrides, dict)
                else None
            )
        return None

    @classmethod
    def _tag_override_from_source(
        cls, source: dict[str, Any], key: str, tag: str
    ) -> str | None:
        overrides = cls._matching_tag_override_map(source, tag)
        if overrides is None:
            return None
        value = overrides.get(key)
        return str(value) if value is not None else None

    def _get_tag_override(self, key: str, meta: Meta | None) -> str | None:
        """Return a tag-specific string override, if configured."""
        tag = self._normalized_release_tag(meta)
        if not tag:
            return None
        for source in (self.tracker_config, self._default_config()):
            value = self._tag_override_from_source(source, key, tag)
            if value is not None:
                return value
        return None

    def _get_str_config(
        self, key: str, default: str = "", meta: Meta | None = None
    ) -> str:
        """Get a string config value, optionally overridden by the release group tag."""
        tag_override = self._get_tag_override(key, meta)
        if tag_override is not None:
            return tag_override
        if key in self.tracker_config:
            val = self.tracker_config[key]
            if val is not None:
                return str(val)
        val = self.config["DEFAULT"].get(key, default)
        return str(val) if val is not None else default

    async def get_custom_header(self, meta: Meta) -> str:
        """Returns a custom header if configured."""
        try:
            custom_description_header = self._get_str_config(
                "custom_description_header", "", meta
            )
            if custom_description_header:
                return custom_description_header
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Error setting custom description header: {e!s}[/yellow]"
            )

        return ""

    async def get_tonemapped_header(self, meta: Meta) -> str:
        try:
            tonemapped_description_header = self._get_str_config(
                "tonemapped_header", "", meta
            )
            if tonemapped_description_header and meta.tonemapped:
                return tonemapped_description_header
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Error setting tonemapped header: {e!s}[/yellow]"
            )
        return ""

    @staticmethod
    def _uses_tmdb_resized_logo(tracker: str) -> bool:
        return tracker in {
            "BJSHARE",
            "ANTHELION",
            "GREATPOSTERWALL",
            "BRASILTRACKER",
            "FUNFILE",
            "HDSPACE",
            "HDTORRENTS",
            "SPEEDAPP",
        }

    @staticmethod
    def _resized_tmdb_logo(meta: Meta) -> tuple[str, str] | None:
        logo_path = str(meta.tmdb_logo or "")
        if not logo_path:
            return None
        if logo_path.endswith(".svg"):
            logo_path = logo_path.removesuffix(".svg") + ".png"
        return f"https://image.tmdb.org/t/p/w300/{logo_path}", "300"

    def _configured_logo_section(self, meta: Meta) -> tuple[str, str]:
        if self._uses_tmdb_resized_logo(self.tracker):
            resized = self._resized_tmdb_logo(meta)
            if resized is not None:
                return resized
        logo = str(meta.logo or "")
        if not logo:
            return "", ""
        return logo, str(self._get_int_config("logo_size", 300))

    async def get_logo_section(self, meta: Meta) -> tuple[str, str]:
        """Returns the logo URL and size if applicable."""
        try:
            if not self._get_bool_config("add_logo", False):
                return "", ""
            return self._configured_logo_section(meta)
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting logo section: {error!s}[/yellow]"
            )
            return "", ""

    @staticmethod
    def _tmdb_episode_trackers() -> frozenset[str]:
        return frozenset(
            {
                "CAPYBARABR",
                "BJSHARE",
                "BRASILTRACKER",
                "LOCADORA",
                "SAMARITANO",
            }
        )

    @staticmethod
    def _mapping_value(value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _tmdb_episode_info(self, meta: Meta) -> tuple[str, str]:
        data = self._mapping_value(meta.episode_tmdb_data)
        return str(data.get("name", "") or ""), str(
            data.get("overview", "") or ""
        )

    @staticmethod
    def _generic_episode_name(value: str) -> bool:
        lowered = value.casefold()
        return lowered.startswith("episode") or "tba" in lowered

    @classmethod
    def _tvmaze_episode_title(cls, meta: Meta, data: dict[str, Any]) -> str:
        automatic = str(meta.auto_episode_title or "")
        if automatic:
            return automatic
        episode_name = str(data.get("episode_name", "") or "")
        return "" if cls._generic_episode_name(episode_name) else episode_name

    @staticmethod
    def _composed_tv_title(
        season_name: str,
        season_number: Any,
        episode_number: Any,
        episode_title: str,
    ) -> str:
        title = season_name
        if title and season_number:
            title += f" - {season_number}{episode_number}"
        if episode_title:
            title += f": {episode_title}" if title else episode_title
        return title

    @staticmethod
    def _tvmaze_season_name(meta: Meta, data: dict[str, Any]) -> str:
        return str(data.get("season_name") or meta.tvdb_season_name or "")

    @staticmethod
    def _tvmaze_overview(meta: Meta, data: dict[str, Any]) -> str:
        overview = str(data.get("overview") or meta.overview_meta or "")
        return html_to_bbcode(overview) if overview else ""

    def _tvmaze_episode_info(self, meta: Meta) -> tuple[str, str]:
        data = self._mapping_value(meta.tvmaze_episode_data)
        title = self._composed_tv_title(
            self._tvmaze_season_name(meta, data),
            meta.season,
            meta.episode,
            self._tvmaze_episode_title(meta, data),
        )
        return title, self._tvmaze_overview(meta, data)

    async def get_tv_info(self, meta: Meta) -> tuple[str, str]:
        try:
            enabled = self._get_bool_config("episode_overview", False)
            if not enabled or meta.category != "TV":
                return "", ""
            if self.tracker in self._tmdb_episode_trackers():
                return self._tmdb_episode_info(meta)
            return self._tvmaze_episode_info(meta)
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting TV info: {error!s}[/yellow]"
            )
            return "", ""

    @staticmethod
    def _mediainfo_section_allowed(meta: Meta) -> bool:
        return meta.is_disc != "BDMV" and meta.category not in {
            "GAME",
            "BOOK",
            "MUSIC",
            "PODCAST",
        }

    @staticmethod
    def _full_mediainfo_path(meta: Meta) -> Path:
        return (
            Path(meta.base_dir) / "tmp" / meta.uuid / "MEDIAINFO_CLEANPATH.txt"
        )

    @staticmethod
    def _short_mediainfo_paths(meta: Meta) -> tuple[Path, Path]:
        directory = Path(meta.base_dir) / "tmp" / meta.uuid
        return directory, directory / "MEDIAINFO_SHORT.txt"

    @staticmethod
    async def _read_text_file(path: Path) -> str:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    async def _cached_short_mediainfo(self, path: Path) -> str:
        if not path.exists() or path.stat().st_size <= 0:
            return ""
        with contextlib.suppress(Exception):
            return await self._read_text_file(path)
        return ""

    async def _write_short_mediainfo(
        self, directory: Path, path: Path, content: str
    ) -> None:
        with contextlib.suppress(Exception):
            await self.common.makedirs(str(directory))
            async with aiofiles.open(
                path, mode="w", encoding="utf-8"
            ) as handle:
                await handle.write(content)

    async def _full_mediainfo_content(self, meta: Meta) -> str:
        if not (self._get_bool_config("full_mediainfo", True) or meta.is_disc):
            return ""
        full_path = self._full_mediainfo_path(meta)
        if not await self.common.path_exists(str(full_path)):
            return ""
        return await self._read_text_file(full_path)

    async def _generated_short_mediainfo(self, meta: Meta) -> str:
        if not meta.mediainfo:
            return ""
        cache_dir, cache_path = self._short_mediainfo_paths(meta)
        video_file = str(meta.filelist[0]) if meta.filelist else ""
        content = self.format_short_mediainfo_json(meta.mediainfo, video_file)
        if content:
            await self._write_short_mediainfo(cache_dir, cache_path, content)
        return content

    async def get_mediainfo_section(self, meta: Meta) -> str:
        """Returns the mediainfo section, using a cache file if available."""
        if not self._mediainfo_section_allowed(meta):
            return ""
        full = await self._full_mediainfo_content(meta)
        if full:
            return full
        _cache_dir, cache_path = self._short_mediainfo_paths(meta)
        cached = await self._cached_short_mediainfo(cache_path)
        return (
            cached if cached else await self._generated_short_mediainfo(meta)
        )

    @staticmethod
    def _mediainfo_tracks(mediainfo: dict[str, Any]) -> list[dict[str, Any]]:
        media = mediainfo.get("media", {})
        if not isinstance(media, dict):
            return []
        raw_tracks = cast(dict[str, Any], media).get("track", [])
        if not isinstance(raw_tracks, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in cast(list[Any], raw_tracks)
            if isinstance(track, dict)
        ]

    @staticmethod
    def _mi_value(track: dict[str, Any], key: str) -> str:
        field = track.get(key, "")
        return field.strip() if isinstance(field, str) else ""

    @staticmethod
    def _format_mi_duration(seconds: str) -> str:
        try:
            milliseconds = int(
                (Decimal(seconds) * 1000).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
        except InvalidOperation, ValueError:
            return ""
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, remainder = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{whole_seconds:02}.{remainder:03}"

    @staticmethod
    def _format_mi_size(bytes_value: str) -> str:
        try:
            gibibytes = Decimal(bytes_value) / (1024**3)
        except InvalidOperation:
            return ""
        precision = ".1f" if gibibytes >= 10 else ".2f"
        return f"{gibibytes:{precision}} GiB"

    @staticmethod
    def _format_mi_bitrate(bits_per_second: str) -> str:
        try:
            bitrate = Decimal(bits_per_second)
        except InvalidOperation:
            return ""
        if bitrate >= 10_000_000:
            return f"{bitrate / 1_000_000:.1f} Mb/s"
        value = int((bitrate / 1000).to_integral_value(rounding=ROUND_HALF_UP))
        return f"{value:,}".replace(",", " ") + " kb/s"

    @staticmethod
    def _format_mi_sampling_rate(hertz: str) -> str:
        try:
            return f"{Decimal(hertz) / 1000:.1f} kHz"
        except InvalidOperation:
            return ""

    @staticmethod
    def _mi_language_name(language: str) -> str:
        if not language:
            return ""
        try:
            parsed = langcodes.Language.get(language)
            name = parsed.language_name("en")
            return f"{name} ({parsed.territory})" if parsed.territory else name
        except LanguageTagError:
            return language

    @classmethod
    def _general_mi_lines(
        cls, general: dict[str, Any], video_file: str
    ) -> list[str]:
        filename = Path(
            cls._mi_value(general, "CompleteName") or video_file
        ).stem
        return [
            filename,
            "",
            "---GENERAL----",
            f"Size...........: {cls._format_mi_size(cls._mi_value(general, 'FileSize'))}",
            f"Container......: {cls._mi_value(general, 'Format')}",
            f"Duration.......: {cls._format_mi_duration(cls._mi_value(general, 'Duration'))}",
            "",
        ]

    @classmethod
    def _video_codec(cls, video: dict[str, Any]) -> str:
        parts = [
            cls._mi_value(video, key)
            for key in (
                "Format",
                "Encoded_Library",
                "HDR_Format_String",
                "transfer_characteristics",
            )
        ]
        return ", ".join(part for part in parts if part)

    @classmethod
    def _video_mi_lines(cls, video: dict[str, Any]) -> list[str]:
        return [
            "---VIDEO----",
            f"Codec..........: {cls._video_codec(video)}",
            f"Resolution.....: {cls._mi_value(video, 'Width')}x{cls._mi_value(video, 'Height')}",
            f"Bit rate.......: {cls._format_mi_bitrate(cls._mi_value(video, 'BitRate'))}",
            f"Frame rate.....: {cls._mi_value(video, 'FrameRate')} fps",
            "",
        ]

    @classmethod
    def _audio_mi_lines(cls, audio: dict[str, Any]) -> list[str]:
        title = cls._mi_value(audio, "Title")
        channels = cls._mi_value(audio, "Channels")
        commercial = cls._mi_value(audio, "Format_Commercial_IfAny")
        language = cls._mi_language_name(cls._mi_value(audio, "Language"))
        return [
            "---AUDIO----",
            f"Format.........: {commercial or cls._mi_value(audio, 'Format')}",
            f"Channels.......: {channels} channel{'s' if channels != '1' else ''}",
            f"Sample rate....: {cls._format_mi_sampling_rate(cls._mi_value(audio, 'SamplingRate'))}",
            f"Bit rate.......: {cls._format_mi_bitrate(cls._mi_value(audio, 'BitRate'))}",
            f"Language.......: {language}{f' ({title})' if title else ''}",
            "",
        ]

    @classmethod
    def _text_mi_line(cls, text: dict[str, Any]) -> str:
        title = cls._mi_value(text, "Title")
        language = cls._mi_language_name(cls._mi_value(text, "Language"))
        return (
            f"Language.......: {language}{f' ({title})' if title else ''}, "
            f"{cls._mi_value(text, 'Format')}"
        )

    @classmethod
    def _tracks_of_type(
        cls, tracks: list[dict[str, Any]], track_type: str
    ) -> list[dict[str, Any]]:
        return [
            track
            for track in tracks
            if cls._mi_value(track, "@type") == track_type
        ]

    @classmethod
    def _append_short_mediainfo_tracks(
        cls, output: list[str], tracks: list[dict[str, Any]]
    ) -> None:
        for video in cls._tracks_of_type(tracks, "Video"):
            output.extend(cls._video_mi_lines(video))
        for audio in cls._tracks_of_type(tracks, "Audio"):
            output.extend(cls._audio_mi_lines(audio))
        text_tracks = cls._tracks_of_type(tracks, "Text")
        if text_tracks:
            output.append("---SUBTITLES---")
            output.extend(cls._text_mi_line(text) for text in text_tracks)

    @classmethod
    def format_short_mediainfo_json(
        cls, mediainfo: dict[str, Any] | None, video_file: str = ""
    ) -> str:
        """Render the short MediaInfo section from meta.mediainfo."""
        if not mediainfo:
            return ""
        tracks = cls._mediainfo_tracks(mediainfo)
        general_tracks = cls._tracks_of_type(tracks, "General")
        if not general_tracks:
            return ""
        output = cls._general_mi_lines(general_tracks[0], video_file)
        cls._append_short_mediainfo_tracks(output, tracks)
        return "\n".join(output).rstrip() + "\n"

    @staticmethod
    def _bdinfo_summary(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        summary = cast(dict[str, Any], value).get("summary")
        return str(summary) if summary else ""

    @classmethod
    def _bdinfo_summaries(cls, meta: Meta) -> list[str]:
        if meta.is_disc != "BDMV" or not meta.discs:
            return []
        return [
            summary
            for raw_disc in meta.discs
            if (summary := cls._bdinfo_summary(raw_disc))
        ]

    async def get_bdinfo_section(self, meta: Meta) -> str:
        """Returns the bdinfo section if applicable."""
        try:
            return "\n\n".join(self._bdinfo_summaries(meta))
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting bdinfo section: {error!s}[/yellow]"
            )
            return ""

    async def screenshot_header(self, meta: Meta) -> str:
        """Returns the screenshot header if applicable."""
        try:
            screenheader = self._get_str_config("screenshot_header", "", meta)
            if screenheader:
                return screenheader
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Error getting screenshot header: {e!s}[/yellow]"
            )

        return ""

    async def menu_screenshot_header(self, meta: Meta) -> str:
        """Returns the screenshot header for menus if applicable."""
        try:
            menu_images = get_tracker_image_collection(
                meta, self.tracker, "menu_images"
            )
            if meta.is_disc and menu_images:
                disc_menu_header = self._get_str_config(
                    "disc_menu_header", "", meta
                )
                if disc_menu_header:
                    return disc_menu_header
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Error getting menus screenshot header: {e!s}[/yellow]"
            )

        return ""

    async def get_user_description(self, meta: Meta) -> str:
        """Returns the user-provided description (file or link)."""
        try:
            file_content = str(meta.description_file_content or "").strip()
            link_content = str(meta.description_link_content or "").strip()
            return file_content or link_content
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting user description: {error!s}[/yellow]"
            )
            return ""

    async def get_custom_signature(self, meta: Meta) -> str:
        custom_signature: str = ""
        try:
            custom_signature = self._get_str_config(
                "custom_signature", "", meta
            )
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Error setting custom signature: {e!s}[/yellow]"
            )

        return custom_signature

    @staticmethod
    def _hosted_image_url(
        image: dict[str, Any], key: str, fallback: str = ""
    ) -> str:
        value = image.get(key)
        return str(value) if value else fallback

    @classmethod
    def _hosted_image_fields(cls, value: Any) -> tuple[str, str, str] | None:
        if not isinstance(value, dict):
            return None
        image = cast(dict[str, Any], value)
        web_url = cls._hosted_image_url(image, "web_url")
        raw_url = cls._hosted_image_url(image, "raw_url")
        if not web_url or not raw_url:
            return None
        return (
            web_url,
            raw_url,
            cls._hosted_image_url(image, "img_url", raw_url),
        )

    @staticmethod
    def _cover_file_path(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid / "covers.json"

    @staticmethod
    def _hosted_artwork_list(meta: Meta) -> list[Any]:
        hosted = meta.hosted_artwork
        return (
            cast(list[Any], hosted)
            if isinstance(hosted, list) and hosted
            else []
        )

    async def _cover_file_data(self, meta: Meta) -> list[Any]:
        cover_path = self._cover_file_path(meta)
        if not await self.common.path_exists(str(cover_path)):
            return []
        try:
            async with aiofiles.open(cover_path, encoding="utf-8") as handle:
                parsed = json.loads(await handle.read())
        except Exception:
            return []
        return cast(list[Any], parsed) if isinstance(parsed, list) else []

    async def _bluray_cover_data(self, meta: Meta) -> list[Any]:
        hosted = self._hosted_artwork_list(meta)
        return hosted if hosted else await self._cover_file_data(meta)

    @staticmethod
    def _bluray_release_url(meta: Meta, enabled: bool) -> str:
        if meta.is_disc not in {"BDMV", "DVD"} or not enabled:
            return ""
        return str(meta.release_url or "")

    def _format_bluray_cover(
        self, web_url: str, raw_url: str, cover_size: int
    ) -> str:
        if self.tracker == "TORRENTLEECH":
            return (
                f'<a href="{web_url}"><img src="{raw_url}" '
                f'style="max-width: {cover_size}px;"></a>  '
            )
        if self.tracker == "HDTORRENTS":
            return f"<a href='{raw_url}'><img src='{web_url}' height=137></a> "
        return f"[url={web_url}][img={cover_size}]{raw_url}[/img][/url]"

    async def _bluray_cover_images(self, meta: Meta, cover_size: int) -> str:
        enabled = self._get_bool_config("use_bluray_images", False)
        if meta.is_disc not in {"BDMV", "DVD"} or not enabled:
            return ""
        rendered: list[str] = []
        for raw_image in await self._bluray_cover_data(meta):
            fields = self._hosted_image_fields(raw_image)
            if fields is None:
                continue
            web_url, raw_url, _img_url = fields
            rendered.append(
                self._format_bluray_cover(web_url, raw_url, cover_size)
            )
        return "".join(rendered)

    async def get_bluray_section(self, meta: Meta) -> tuple[str, str]:
        try:
            cover_size = self._get_int_config("bluray_image_size", 250)
            release_url = self._bluray_release_url(
                meta, self._get_bool_config("add_bluray_link", False)
            )
            return release_url, await self._bluray_cover_images(
                meta, cover_size
            )
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting bluray section: {error!s}[/yellow]"
            )
            return "", ""

    def _spectrogram_enabled(self, meta: Meta) -> bool:
        configured = self._get_bool_config("add_audio_spectrogram", False)
        return bool(
            meta.audio_spectrogram
            or meta.audio_spectrogram_tracks
            or configured
        )

    async def _spectrogram_image_lines(self, meta: Meta) -> list[str]:
        images = get_tracker_image_collection(
            meta, self.tracker, "spectrograms_images"
        )
        screens_per_row = await self.get_screens_per_row()
        lines: list[str] = []
        for index, raw_image in enumerate(images):
            fields = self._hosted_image_fields(raw_image)
            if fields is None:
                continue
            web_url, raw_url, img_url = fields
            lines.append(self.format_screenshot(web_url, raw_url, img_url))
            self._append_screenshot_row_separator(
                lines, index, screens_per_row
            )
        return lines

    async def get_audio_spectrogram_section(self, meta: Meta) -> str:
        """Returns the audio spectrogram section if applicable."""
        try:
            if not self._spectrogram_enabled(meta):
                return ""
            images = await self._spectrogram_image_lines(meta)
            if not images:
                return ""
            header = self._get_str_config(
                "audio_spectrogram_header",
                "[center][b]Audio Spectrogram[/b][/center]",
                meta,
            )
            return "".join([header, "\n[center]", *images, "[/center]\n"])
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error getting audio spectrogram section: {error!s}[/yellow]"
            )
            return ""

    def _dynamic_hdr_enabled(self, meta: Meta) -> bool:
        return bool(
            meta.dynamic_hdr_plot
            or self._get_bool_config("add_dynamic_hdr_plot", False)
        )

    def _dynamic_hdr_image_lines(self, meta: Meta) -> list[str]:
        images = get_tracker_image_collection(
            meta, self.tracker, "dynamic_hdr_plot_images"
        )
        lines: list[str] = []
        for raw_image in images:
            fields = self._hosted_image_fields(raw_image)
            if fields is None:
                continue
            web_url, raw_url, img_url = fields
            lines.extend(
                (self.format_screenshot(web_url, raw_url, img_url), "\n")
            )
        return lines

    async def get_dynamic_hdr_plot_section(self, meta: Meta) -> str:
        """Return Dolby Vision/HDR10+ dynamic metadata plots, when enabled."""
        if not self._dynamic_hdr_enabled(meta):
            return ""
        images = self._dynamic_hdr_image_lines(meta)
        if not images:
            return ""
        header = self._get_str_config(
            "dynamic_hdr_plot_header",
            "[center][b]Dynamic HDR Metadata[/b][/center]",
            meta,
        )
        return "".join([header, "\n[center]", *images, "[/center]\n"])

    @staticmethod
    def _uses_pt_br_labels(tracker: str) -> bool:
        return tracker in {
            "AMIGOSSHARE",
            "BRASILTRACKER",
            "CAPYBARABR",
            "SAMARITANO",
            "BJSHARE",
        }

    @staticmethod
    def _description_header_tokens(header_size: int) -> tuple[str, str]:
        if not header_size:
            return "[h2]", "[/h2]"
        return f"[size={header_size}][b]", "[/b][/size]\n"

    def _book_layout(self, header_size: int, table: bool) -> tuple[int, bool]:
        if self.tracker in {
            "TORRENTLEECH",
            "IMMORTALSEED",
            "IPTORRENTS",
            "SPEEDAPP",
        }:
            return -1, False
        if self.tracker in {"BJSHARE", "BRASILTRACKER", "AMIGOSSHARE"}:
            resolved_size = header_size or 3
            return (
                resolved_size,
                False if self.tracker == "AMIGOSSHARE" else table,
            )
        return header_size, table

    @staticmethod
    def _localized_label(
        use_pt_br: bool, english: str, portuguese: str
    ) -> str:
        return portuguese if use_pt_br else english

    @classmethod
    def _book_labels(cls, use_pt_br: bool) -> dict[str, str]:
        def label(english: str, portuguese: str) -> str:
            return cls._localized_label(use_pt_br, english, portuguese)

        return {
            "asin": "ASIN",
            "author": label("Author", "Autor"),
            "avg_bitrate": label("Average Bitrate", "Bitrate Médio"),
            "translator": label("Translator", "Tradutor"),
            "duration": label("Duration", "Duração"),
            "edition": label("Edition", "Edição"),
            "isbn": "ISBN",
            "narrator": label("Narrator", "Narrador"),
            "overview": label("Overview", "Visão Geral"),
            "publisher": label("Publisher", "Editora"),
            "language": label("Language", "Idioma"),
            "page_count": label("Page Count", "Número de Páginas"),
            "series": label("Series", "Série"),
            "source": label("Source", "Fonte"),
            "technical": label("Technical Details", "Detalhes Técnicos"),
            "year": label("Release Year", "Ano de Lançamento"),
        }

    @staticmethod
    def _clean_description_overview(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", html_to_bbcode(text)).strip()

    @staticmethod
    def _append_book_field(
        fields: list[tuple[str, str]], label: str, value: Any
    ) -> None:
        if value not in (None, "", [], {}):
            fields.append((label, str(value)))

    @staticmethod
    def _book_series_value(meta: Meta) -> str:
        series = str(meta.book_series or "")
        if not series:
            return ""
        return (
            f"{series} #{meta.book_series_index}"
            if meta.book_series_index
            else series
        )

    def _append_audiobook_fields(
        self, fields: list[tuple[str, str]], meta: Meta, labels: dict[str, str]
    ) -> None:
        if not meta.audiobook:
            return
        self._append_book_field(
            fields, labels["duration"], meta.audiobook_duration_formatted
        )
        bitrate = meta.audiobook_bitrate
        if bitrate:
            fields.append((labels["avg_bitrate"], f"{bitrate} kbps"))

    def _book_fields(
        self, meta: Meta, labels: dict[str, str]
    ) -> list[tuple[str, str]]:
        fields: list[tuple[str, str]] = []
        for label, value in (
            (labels["author"], meta.author),
            (labels["translator"], meta.book_translator),
            (labels["narrator"], meta.narrator),
            (labels["publisher"], meta.publisher),
            (labels["language"], meta.book_language),
            (labels["series"], self._book_series_value(meta)),
            (
                labels["source"],
                str(meta.manual_source or meta.source or "").strip(),
            ),
            (labels["page_count"], meta.page_count),
            (labels["isbn"], meta.isbn),
            (labels["asin"], meta.asin),
            (labels["edition"], meta.edition),
            (labels["year"], meta.year),
        ):
            self._append_book_field(fields, label, value)
        self._append_audiobook_fields(fields, meta, labels)
        return fields

    @staticmethod
    def _styled_book_header(
        header_size: int, underline: bool
    ) -> tuple[str, str]:
        if underline:
            return "[b][u]", "[/u][/b]\n"
        if header_size == -1:
            return "[b]", "[/b]\n"
        return DescriptionBuilder._description_header_tokens(header_size)

    @staticmethod
    def _book_table(fields: list[tuple[str, str]]) -> str:
        lines = ["[table]"]
        lines.extend(
            f"[tr][td][b]{label}[/b][/td][td]{value}[/td][/tr]"
            for label, value in fields
        )
        lines.append("[/table]")
        return "\n".join(lines)

    @staticmethod
    def _book_metadata_spoiler(meta: Meta) -> str:
        if not meta.epubmeta_output:
            return ""
        return f"[spoiler=EPUB Metadata][code]{meta.epubmeta_output}[/code][/spoiler]"

    def _render_book_table_section(
        self,
        meta: Meta,
        fields: list[tuple[str, str]],
        overview: str,
        labels: dict[str, str],
        header_size: int,
        underline: bool,
    ) -> str:
        header, header_end = self._styled_book_header(header_size, underline)
        technical = (
            f"{header}{labels['technical']}{header_end}\n\n{self._book_table(fields)}"
            if fields
            else ""
        )
        overview_part = (
            f"{header}{labels['overview']}{header_end}\n{overview}"
            if overview
            else ""
        )
        parts = [technical, self._book_metadata_spoiler(meta), overview_part]
        return "\n\n".join(part for part in parts if part.strip())

    def _render_book_list_section(
        self,
        fields: list[tuple[str, str]],
        overview: str,
        labels: dict[str, str],
        header_size: int,
        underline: bool,
        bullet: str,
    ) -> str:
        header, header_end = self._styled_book_header(header_size, underline)
        prefix = f"{bullet} " if bullet else ""
        field_lines = [
            f"{prefix}[b]{label}:[/b] {value}" for label, value in fields
        ]
        parts: list[str] = []
        if field_lines:
            parts.append(
                f"{header}{labels['technical']}{header_end}"
                + "\n".join(field_lines)
            )
        if overview:
            parts.append(f"{header}{labels['overview']}{header_end}{overview}")
        return "\n\n".join(parts)

    def _build_book_desc_section(
        self,
        meta: Meta,
        header_size: int = 0,
        table: bool = True,
        underline: bool = False,
        bullet: str = "",
    ) -> str:
        """Build the BBCode table or list for BOOK-category uploads."""
        header_size, table = self._book_layout(header_size, table)
        labels = self._book_labels(self._uses_pt_br_labels(self.tracker))
        fields = self._book_fields(meta, labels)
        overview = self._clean_description_overview(meta.overview)
        if not fields and not overview:
            return ""
        if table:
            return self._render_book_table_section(
                meta, fields, overview, labels, header_size, underline
            )
        return self._render_book_list_section(
            fields, overview, labels, header_size, underline, bullet
        )

    def _game_header_size(self, header_size: int) -> int:
        if header_size:
            return header_size
        if self.tracker == "TORRENTLEECH":
            return 1
        if self.tracker in {"BJSHARE", "BRASILTRACKER"}:
            return 3
        return 0

    @classmethod
    def _game_labels(cls, use_pt_br: bool) -> dict[str, str]:
        def label(english: str, portuguese: str) -> str:
            return cls._localized_label(use_pt_br, english, portuguese)

        return {
            "technical": label("Technical Details", "Detalhes Técnicos"),
            "overview": label("Overview", "Visão Geral"),
            "platform": label("Platform", "Plataforma"),
            "version": label("Version", "Versão"),
            "genre": label("Genre", "Gênero"),
            "developer": label("Developer", "Desenvolvedor"),
            "publisher": label("Publisher", "Distribuidora"),
            "requirements": label(
                "System Requirements", "Requisitos do Sistema"
            ),
            "minimum": label("Minimum", "Mínimo"),
            "recommended": label("Recommended", "Recomendado"),
            "languages": label(
                "Officially Supported Languages",
                "Idiomas Oficialmente Suportados",
            ),
            "language": label("Language", "Idioma"),
            "support": label("Support", "Suporte"),
            "content_type": label("Content Type", "Tipo de Conteúdo"),
            "package": label("Package", "Pacote"),
            "release_group": label("Release Group", "Grupo de Lançamento"),
            "installation": label(
                "Installation and Usage Instructions",
                "Instruções de Instalação e Uso",
            ),
        }

    @staticmethod
    def _game_package_formats(meta: Meta) -> str:
        formats = sorted(
            {
                Path(str(item)).suffix.upper().lstrip(".")
                for item in meta.filelist
                if Path(str(item)).suffix.lower() in {".dmg", ".exe", ".pkg"}
            }
        )
        return ", ".join(formats)

    @staticmethod
    def _game_base_fields(
        meta: Meta, labels: dict[str, str]
    ) -> list[tuple[str, str]]:
        values = (
            (labels["platform"], meta.platform),
            (labels["version"], meta.game_version),
            (labels["genre"], ", ".join(meta.genres) if meta.genres else ""),
            (labels["developer"], meta.developer),
            (labels["publisher"], meta.publisher),
        )
        return [(label, str(value)) for label, value in values if value]

    def _game_software_fields(
        self, meta: Meta, labels: dict[str, str]
    ) -> list[tuple[str, str]]:
        if not meta.software:
            return []
        fields: list[tuple[str, str]] = [(labels["content_type"], "Software")]
        package_formats = self._game_package_formats(meta)
        if package_formats:
            fields.append((labels["package"], package_formats))
        if meta.tag:
            fields.append((labels["release_group"], str(meta.tag).lstrip("-")))
        return fields

    def _game_technical_fields(
        self, meta: Meta, labels: dict[str, str]
    ) -> list[tuple[str, str]]:
        fields = [
            *self._game_software_fields(meta, labels),
            *self._game_base_fields(meta, labels),
        ]
        if meta.steam_url:
            fields.append(("Steam", f"[url]{meta.steam_url}[/url]"))
        return fields

    @staticmethod
    def _render_detail_fields(
        header: str,
        header_end: str,
        heading: str,
        fields: list[tuple[str, str]],
        table: bool,
    ) -> str:
        if not fields:
            return ""
        lines = [f"{header}{heading}{header_end}"]
        if table:
            table_lines = ["[table]"]
            table_lines.extend(
                f"[tr][td][b]{label}[/b][/td][td]{value}[/td][/tr]"
                for label, value in fields
            )
            table_lines.append("[/table]")
            lines.append("\n".join(table_lines))
        else:
            lines.extend(f"[b]{label}[/b] {value}" for label, value in fields)
        return "\n".join(lines)

    @staticmethod
    def _localized_game_overview(meta: Meta, use_pt_br: bool) -> str:
        if not use_pt_br:
            return DescriptionBuilder._clean_description_overview(
                meta.overview
            )
        raw = meta.localized_overviews
        localized = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
        return DescriptionBuilder._clean_description_overview(
            localized.get("brazilian", "")
        )

    @classmethod
    def _game_overview_section(
        cls,
        meta: Meta,
        labels: dict[str, str],
        header: str,
        header_end: str,
        use_pt_br: bool,
    ) -> str:
        overview = cls._localized_game_overview(meta, use_pt_br)
        return (
            f"\n{header}{labels['overview']}{header_end}\n{overview}\n"
            if overview
            else ""
        )

    @classmethod
    def _game_software_notes_section(
        cls, meta: Meta, labels: dict[str, str], header: str, header_end: str
    ) -> str:
        if not meta.software or not meta.software_notes:
            return ""
        notes = cls._clean_description_overview(meta.software_notes)
        return (
            f"{header}{labels['installation']}{header_end}{notes}"
            if notes
            else ""
        )

    @staticmethod
    def _clean_game_requirement(value: Any, heading: str) -> str:
        text = str(value or "")
        if not text:
            return ""
        cleaned = html.unescape(html_to_bbcode(text))
        cleaned = re.sub(r"<[^>]+>", "", cleaned).strip()
        return re.sub(
            rf"^\[b\](?:{heading}):\[/b\]\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _game_requirements_table(
        labels: dict[str, str], minimum: str, recommended: str
    ) -> str:
        return "\n".join(
            [
                "[table]",
                f"[tr][td][b]{labels['minimum']}[/b][/td][td][b]{labels['recommended']}[/b][/td][/tr]",
                f"[tr][td]{minimum or '-'}[/td][td]{recommended or '-'}[/td][/tr]",
                "[/table]",
            ]
        )

    @staticmethod
    def _game_requirements_list(
        labels: dict[str, str], minimum: str, recommended: str
    ) -> str:
        rows: list[str] = []
        if minimum:
            rows.append(f"[b]{labels['minimum']}[/b] {minimum}")
        if recommended:
            rows.append(f"\n[b]{labels['recommended']}[/b] {recommended}")
        return "\n".join(rows)

    @classmethod
    def _game_requirements_section(
        cls,
        meta: Meta,
        labels: dict[str, str],
        header: str,
        header_end: str,
        table: bool,
    ) -> str:
        if not meta.requirements_minimum and not meta.requirements_recommended:
            return ""
        minimum = cls._clean_game_requirement(
            meta.requirements_minimum, "Minimum|Mínimo"
        )
        recommended = cls._clean_game_requirement(
            meta.requirements_recommended, "Recommended|Recomendado"
        )
        body = (
            cls._game_requirements_table(labels, minimum, recommended)
            if table
            else cls._game_requirements_list(labels, minimum, recommended)
        )
        return f"{header}{labels['requirements']}{header_end}\n{body}"

    @staticmethod
    def _game_support_values(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return list(map(str, cast(Iterable[Any], value)))
        return [str(value)] if value else []

    @classmethod
    def _game_language_entries(cls, meta: Meta) -> list[tuple[str, list[str]]]:
        raw = meta.languages
        if not isinstance(raw, dict):
            return []
        return [
            (str(language or ""), cls._game_support_values(support))
            for language, support in sorted(cast(dict[Any, Any], raw).items())
        ]

    @staticmethod
    def _game_languages_table(
        entries: list[tuple[str, list[str]]], labels: dict[str, str]
    ) -> str:
        rows = [
            f"[tr][td][b]{labels['language']}[/b][/td][td][b]{labels['support']}[/b][/td][/tr]"
        ]
        rows.extend(
            f"[tr][td]{language.strip() or '-'}[/td][td]{', '.join(support).strip() or '-'}[/td][/tr]"
            for language, support in entries
        )
        return "[table]\n" + "\n".join(rows) + "\n[/table]\n"

    @staticmethod
    def _game_languages_list(entries: list[tuple[str, list[str]]]) -> str:
        return (
            "\n".join(
                f"[b]{language}[/b]: {', '.join(support)}"
                for language, support in entries
            )
            + "\n"
        )

    @classmethod
    def _game_languages_section(
        cls,
        meta: Meta,
        labels: dict[str, str],
        header: str,
        header_end: str,
        table: bool,
    ) -> str:
        entries = cls._game_language_entries(meta)
        if not entries:
            return ""
        body = (
            cls._game_languages_table(entries, labels)
            if table
            else cls._game_languages_list(entries)
        )
        return f"{header}{labels['languages']}{header_end}\n{body}"

    def _build_game_desc_section(
        self, meta: Meta, header_size: int = 0, table: bool = True
    ) -> str:
        """Build the BBCode layout for GAME-category uploads."""
        if meta.category != "GAME":
            return ""
        header_size = self._game_header_size(header_size)
        header, header_end = self._description_header_tokens(header_size)
        use_pt_br = self._uses_pt_br_labels(self.tracker)
        labels = self._game_labels(use_pt_br)
        sections = [
            self._render_detail_fields(
                header,
                header_end,
                labels["technical"],
                self._game_technical_fields(meta, labels),
                table,
            ),
            self._game_overview_section(
                meta, labels, header, header_end, use_pt_br
            ),
            self._game_software_notes_section(
                meta, labels, header, header_end
            ),
            self._game_requirements_section(
                meta, labels, header, header_end, table
            ),
            self._game_languages_section(
                meta, labels, header, header_end, table
            ),
        ]
        return "\n".join(section for section in sections if section.strip())

    @staticmethod
    def _music_release_mapping(meta: Meta) -> dict[str, Any] | None:
        raw_release = meta.music_release
        if meta.category != "MUSIC" or not isinstance(raw_release, dict):
            return None
        return cast(dict[str, Any], raw_release)

    @staticmethod
    def _music_track_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in cast(list[Any], value)
            if isinstance(track, dict)
        ]

    @classmethod
    def _music_release_data(
        cls, meta: Meta
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]] | None:
        release = cls._music_release_mapping(meta)
        if release is None:
            return None
        fields = cls._mapping_value(release.get("fields"))
        tracks = cls._music_track_list(release.get("tracks"))
        external_ids = cls._mapping_value(release.get("external_ids"))
        return (
            (fields, tracks, external_ids)
            if fields or tracks or external_ids
            else None
        )

    def _music_table_enabled(self, table: bool) -> bool:
        if self.tracker in {
            "TORRENTLEECH",
            "IMMORTALSEED",
            "IPTORRENTS",
            "SPEEDAPP",
        }:
            return False
        return table

    def _music_header_size(self, header_size: int) -> int:
        if header_size:
            return header_size
        if self.tracker == "TORRENTLEECH":
            return 1
        if self.tracker in {"BJSHARE", "BRASILTRACKER", "SPEEDAPP"}:
            return 3
        return 0

    @staticmethod
    def _music_field_value(
        fields: dict[str, Any], name: str, fallback: Any = ""
    ) -> Any:
        raw = fields.get(name, {})
        if not isinstance(raw, dict):
            return fallback
        value = cast(dict[str, Any], raw).get("value")
        return fallback if value in (None, "", [], {}) else value

    @staticmethod
    def _music_display(value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(
                str(part)
                for part in cast(list[Any], value)
                if str(part).strip()
            )
        return str(value).strip() if value not in (None, "") else ""

    @classmethod
    def _formatted_music_technical_value(
        cls, item: Any, formatter: Callable[[Any], Any]
    ) -> str:
        if item in (None, ""):
            return ""
        try:
            hash(item)
            return cls._music_display(formatter(item))
        except TypeError, ValueError, OverflowError:
            return ""

    @classmethod
    def _music_technical_values(
        cls,
        tracks: list[dict[str, Any]],
        name: str,
        formatter: Callable[[Any], Any] = str,
    ) -> str:
        values: dict[Any, str] = {}
        for track in tracks:
            item = track.get(name)
            formatted = cls._formatted_music_technical_value(item, formatter)
            if formatted:
                values[item] = formatted
        return ", ".join(values[item] for item in sorted(values, key=str))

    @classmethod
    def _music_labels(cls, use_pt_br: bool) -> dict[str, str]:
        def label(english: str, portuguese: str) -> str:
            return cls._localized_label(use_pt_br, english, portuguese)

        return {
            "details": label("Music Details", "Detalhes da Música"),
            "artist": label("Artist", "Artista"),
            "album": label("Album", "Álbum"),
            "year": label(
                "Original Release Year", "Ano de Lançamento Original"
            ),
            "release_year": label("Release Year", "Ano desta Edição"),
            "edition": label("Edition", "Edição"),
            "edition_year": label("Edition Year", "Ano da Edição"),
            "type": label("Release Type", "Tipo de Lançamento"),
            "media": label("Media", "Mídia"),
            "label": label("Label", "Gravadora"),
            "catalogue": label("Catalogue Number", "Número de Catálogo"),
            "genres": label("Genres", "Gêneros"),
            "tracks": label("Tracks", "Faixas"),
            "discs": label("Discs", "Discos"),
            "format": label("Format", "Formato"),
            "codec": "Codec",
            "bit_depth": label("Bit Depth", "Profundidade de Bits"),
            "sample_rate": label("Sample Rate", "Taxa de Amostragem"),
            "channels": label("Channels", "Canais"),
            "bitrate": "Bitrate",
            "external_ids": label("External IDs", "IDs Externos"),
        }

    @staticmethod
    def _musicbrainz_link(kind: str, identifier: Any) -> str:
        value = str(identifier or "").strip()
        if not re.fullmatch(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
            value,
            re.IGNORECASE,
        ):
            return ""
        return f"[url=https://musicbrainz.org/{kind}/{value}]{value}[/url]"

    @staticmethod
    def _discogs_numeric_id(kind: str, identifier: Any) -> str:
        raw = str(identifier or "").strip()
        match = re.fullmatch(
            rf"(?:https?://(?:www\.)?discogs\.com/)?{kind}(?:/|:)(\d+)(?:-[^/?#]+)?/?(?:[?#].*)?",
            raw,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
        return raw if raw.isdecimal() else ""

    @classmethod
    def _discogs_link(cls, kind: str, identifier: Any) -> str:
        numeric = cls._discogs_numeric_id(kind, identifier)
        if not numeric:
            return ""
        return f"[url=https://www.discogs.com/{kind}/{numeric}]{numeric}[/url]"

    @classmethod
    def _music_external_links(cls, external_ids: dict[str, Any]) -> list[str]:
        links = [
            (
                "MusicBrainz Release",
                cls._musicbrainz_link(
                    "release", external_ids.get("musicbrainz_release")
                ),
            ),
            (
                "MusicBrainz Release Group",
                cls._musicbrainz_link(
                    "release-group",
                    external_ids.get("musicbrainz_release_group"),
                ),
            ),
            (
                "Discogs Release",
                cls._discogs_link(
                    "release", external_ids.get("discogs_release")
                ),
            ),
            (
                "Discogs Master",
                cls._discogs_link(
                    "master", external_ids.get("discogs_master")
                ),
            ),
        ]
        return [f"{label}: {link}" for label, link in links if link]

    @staticmethod
    def _format_music_bit_depth(value: Any) -> str:
        return f"{value}-bit"

    @staticmethod
    def _format_music_sample_rate(value: Any) -> str:
        return f"{int(value) / 1000:g} kHz"

    @staticmethod
    def _format_music_channels(value: Any) -> str:
        count = int(value)
        return {1: "Mono", 2: "Stereo"}.get(count, f"{value} channels")

    @staticmethod
    def _format_music_bitrate(value: Any) -> str:
        return f"{round(int(value) / 1000)} kbps"

    @classmethod
    def _music_fields(
        cls,
        meta: Meta,
        fields: dict[str, Any],
        tracks: list[dict[str, Any]],
        external_ids: dict[str, Any],
        labels: dict[str, str],
        table: bool,
    ) -> list[tuple[str, str]]:
        def value(name: str, fallback: Any = "") -> Any:
            return cls._music_field_value(fields, name, fallback)

        external_links = cls._music_external_links(external_ids)
        values = [
            (
                labels["artist"],
                cls._music_display(
                    value("artists", value("artist", meta.artist))
                ),
            ),
            (labels["album"], cls._music_display(value("album", meta.title))),
            (labels["year"], cls._music_display(value("year", meta.year))),
            (
                labels["release_year"],
                cls._music_display(value("release_year")),
            ),
            (labels["edition"], cls._music_display(value("edition"))),
            (
                labels["edition_year"],
                cls._music_display(value("edition_year")),
            ),
            (labels["type"], cls._music_display(value("release_type"))),
            (labels["media"], cls._music_display(value("media", meta.source))),
            (
                labels["label"],
                cls._music_display(value("release_label", value("label"))),
            ),
            (
                labels["catalogue"],
                cls._music_display(value("release_catalogue_number")),
            ),
            (labels["genres"], cls._music_display(value("genres"))),
            (
                labels["tracks"],
                cls._music_display(value("track_count", len(tracks))),
            ),
            (labels["discs"], cls._music_display(value("disc_count", 1))),
            (
                labels["format"],
                cls._music_display(
                    value(
                        "format", cls._music_technical_values(tracks, "format")
                    )
                ),
            ),
            (labels["codec"], cls._music_technical_values(tracks, "codec")),
            (
                labels["bit_depth"],
                cls._music_technical_values(
                    tracks, "bit_depth", cls._format_music_bit_depth
                ),
            ),
            (
                labels["sample_rate"],
                cls._music_technical_values(
                    tracks, "sample_rate", cls._format_music_sample_rate
                ),
            ),
            (
                labels["channels"],
                cls._music_technical_values(
                    tracks, "channels", cls._format_music_channels
                ),
            ),
            (
                labels["bitrate"],
                cls._music_technical_values(
                    tracks, "bitrate", cls._format_music_bitrate
                ),
            ),
            (
                labels["external_ids"],
                (
                    ", ".join(external_links)
                    if table
                    else "\n".join(external_links)
                ),
            ),
        ]
        return [
            (label, field_value)
            for label, field_value in values
            if field_value
        ]

    @staticmethod
    def _render_music_fields(
        fields: list[tuple[str, str]], table: bool
    ) -> str:
        if table:
            rows = ["[table]"]
            rows.extend(
                f"[tr][td][b]{label}[/b][/td][td]{value}[/td][/tr]"
                for label, value in fields
            )
            rows.append("[/table]")
            return "\n".join(rows)
        return "\n".join(f"[b]{label}:[/b] {value}" for label, value in fields)

    @staticmethod
    def _safe_music_int(value: Any, fallback: int) -> int:
        try:
            return int(value or fallback)
        except TypeError, ValueError:
            return fallback

    @classmethod
    def _music_disc_count(cls, tracks: list[dict[str, Any]]) -> int:
        return max(
            (
                cls._safe_music_int(track.get("disc_number"), 1)
                for track in tracks
            ),
            default=1,
        )

    @classmethod
    def _music_track_line(
        cls,
        track: dict[str, Any],
        index: int,
        disc_count: int,
        primary_artist: str,
    ) -> str:
        track_number = cls._safe_music_int(track.get("track_number"), index)
        disc_number = cls._safe_music_int(track.get("disc_number"), 1)
        number = (
            f"{disc_number}-{track_number:02d}"
            if disc_count > 1
            else f"{track_number:02d}"
        )
        title = cls._music_display(track.get("title"))
        if not title:
            return ""
        artist = cls._music_display(track.get("artist"))
        credit = (
            f"{artist} - "
            if artist and artist.casefold() != primary_artist.casefold()
            else ""
        )
        return f"{number}. {credit}{title}"

    @classmethod
    def _music_tracklist(
        cls,
        tracks: list[dict[str, Any]],
        primary_artist: str,
        use_pt_br: bool,
        header: str,
        header_end: str,
    ) -> str:
        disc_count = cls._music_disc_count(tracks)
        lines = [
            line
            for index, track in enumerate(tracks, start=1)
            if (
                line := cls._music_track_line(
                    track, index, disc_count, primary_artist
                )
            )
        ]
        if not lines:
            return ""
        heading = "Lista de Faixas" if use_pt_br else "Tracklist"
        return f"\n\n{header}{heading}{header_end}\n" + "\n".join(lines)

    def _build_music_desc_section(
        self, meta: Meta, header_size: int = 0, table: bool = True
    ) -> str:
        """Build a tracker-neutral BBCode summary for MUSIC-category uploads."""
        release_data = self._music_release_data(meta)
        if release_data is None:
            return ""
        fields, tracks, external_ids = release_data
        table = self._music_table_enabled(table)
        header_size = self._music_header_size(header_size)
        header, header_end = self._description_header_tokens(header_size)
        use_pt_br = self._uses_pt_br_labels(self.tracker)
        labels = self._music_labels(use_pt_br)
        music_fields = self._music_fields(
            meta, fields, tracks, external_ids, labels, table
        )
        body = self._render_music_fields(music_fields, table)
        primary_artist = self._music_display(
            self._music_field_value(fields, "artist")
        )
        tracklist = self._music_tracklist(
            tracks, primary_artist, use_pt_br, header, header_end
        )
        return f"{header}{labels['details']}{header_end}\n{body}{tracklist}"

    def _description_image_state(self, meta: Meta) -> tuple[list[Any], int]:
        tracker_images = get_tracker_image_collection(
            meta, self.tracker, "screenshots"
        )
        image_list = (
            cast(list[Any], tracker_images)
            if isinstance(tracker_images, list)
            else []
        )
        if image_list:
            images = image_list
            multi_screens = 0
        else:
            raw_images = meta.image_list
            images = (
                cast(list[Any], raw_images)
                if isinstance(raw_images, list)
                else []
            )
            multi_screens = self._get_int_config("multiScreens", 2)
        if meta.sorted_filelist:
            multi_screens = 0
        return images, multi_screens

    async def _custom_header_parts(
        self, meta: Meta, enabled: bool, desc_header: str
    ) -> list[str]:
        if not enabled:
            return []
        header = desc_header or await self.get_custom_header(meta)
        return [header + "\n"] if header else []

    @staticmethod
    def _description_language_values(
        value: list[str] | str | None,
    ) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [
                str(language) for language in value if str(language).strip()
            ]
        return []

    @classmethod
    def _language_output_line(
        cls,
        label: str,
        languages: list[str] | str | None,
        enabled: bool | None,
    ) -> str:
        values = cls._description_language_values(languages)
        if not bool(enabled) or not values:
            return ""
        return f"[code]{label}: {', '.join(values)}[/code]"

    @classmethod
    def _language_output_lines(cls, meta: Meta) -> list[str]:
        lines = (
            cls._language_output_line(
                "Audio Language/s",
                meta.audio_languages,
                meta.write_audio_languages,
            ),
            cls._language_output_line(
                "Subtitle Language/s",
                meta.subtitle_languages,
                meta.write_subtitle_languages,
            ),
            cls._language_output_line(
                "Hardcoded Subtitle Language/s",
                meta.subtitle_languages,
                meta.write_hc_languages,
            ),
        )
        return [line for line in lines if line]

    async def _language_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled:
            return []
        try:
            if not meta.language_checked:
                await languages_manager.process_desc_language(
                    meta, self.tracker
                )
            return self._language_output_lines(meta)
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error processing language: {error!s}[/yellow]"
            )
            return []

    async def _logo_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled:
            return []
        logo_url, logo_size = await self.get_logo_section(meta)
        if not logo_url or not logo_size:
            return []
        return [f"[center][img={logo_size}]{logo_url}[/img][/center]\n"]

    async def _bjshare_media_parts(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        if meta.is_disc == "DVD":
            parts.append(
                f"[hide=DVD MediaInfo][pre]{await self.get_mediainfo_section(meta)}[/pre][/hide]"
            )
        bd_info = await self.get_bdinfo_section(meta)
        if bd_info:
            parts.append(f"[hide=BDInfo][pre]{bd_info}[/pre][/hide]")
        return parts

    async def _digitalcore_media_parts(self, meta: Meta) -> list[str]:
        bd_info = await self.get_bdinfo_section(meta)
        return [bd_info] if bd_info else []

    async def _preformatted_media_parts(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        mediainfo = await self.get_mediainfo_section(meta)
        if mediainfo:
            parts.append(f"[pre]{mediainfo}[/pre]")
        bd_info = await self.get_bdinfo_section(meta)
        if bd_info:
            parts.append(f"[pre]{bd_info}[/pre]")
        return parts

    async def _plain_media_parts(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        mediainfo = await self.get_mediainfo_section(meta)
        if mediainfo:
            parts.append(mediainfo)
        bd_info = await self.get_bdinfo_section(meta)
        if bd_info:
            parts.append(bd_info)
        return parts

    async def _hdtorrents_media_parts(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        mediainfo = await self.get_mediainfo_section(meta)
        if mediainfo:
            parts.append(f"[left][font=consolas]{mediainfo}[/font][/left]")
        bd_info = await self.get_bdinfo_section(meta)
        if bd_info:
            parts.append(f"[left][font=consolas]{bd_info}[/font][/left]")
        return parts

    async def _mediainfo_description_parts(
        self, meta: Meta, enabled: bool
    ) -> list[str]:
        if not enabled:
            return []
        handlers = {
            "BJSHARE": self._bjshare_media_parts,
            "DIGITALCORE": self._digitalcore_media_parts,
            "FUNFILE": self._preformatted_media_parts,
            "HDSPACE": self._preformatted_media_parts,
            "IPTORRENTS": self._preformatted_media_parts,
            "IMMORTALSEED": self._preformatted_media_parts,
            "PTSKIT": self._plain_media_parts,
            "TORRENTLEECH": self._plain_media_parts,
            "HDTORRENTS": self._hdtorrents_media_parts,
        }
        handler = handlers.get(self.tracker)
        return await handler(meta) if handler is not None else []

    async def _bluray_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled:
            return []
        release_url, cover_images = await self.get_bluray_section(meta)
        parts: list[str] = []
        if release_url:
            parts.append(f"[center]{release_url}[/center]")
        if cover_images:
            parts.append(f"[center]{cover_images}[/center]\n")
        return parts

    async def _tv_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled:
            return []
        title, overview = await self.get_tv_info(meta)
        if not overview:
            return []
        parts = [f"[center]{title}[/center]\n"] if title else []
        parts.append(f"[center]{overview}[/center]\n")
        return parts

    def _book_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled or meta.category != "BOOK":
            return []
        section = self._build_book_desc_section(meta)
        return [section] if section else []

    def _game_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled or meta.category != "GAME":
            return []
        section = self._build_game_desc_section(meta)
        return [section] if section else []

    def _music_parts(self, meta: Meta, enabled: bool) -> list[str]:
        if not enabled or meta.category != "MUSIC":
            return []
        section = self._build_music_desc_section(meta)
        return [section] if section else []

    def _mteam_parts(self, meta: Meta) -> list[str]:
        value = str(meta.mteam_description or "")
        return [value] if self.tracker == "MTEAM" and value else []

    @staticmethod
    def _nexus_trackers() -> frozenset[str]:
        return frozenset(
            {
                "1PTBA",
                "LAJIDUI",
                "LEMONHD",
                "LONGPT",
                "PTCAFE",
                "PTFANS",
                "PTGTK",
                "PTZONE",
                "RAILGUNPT",
                "XINGYUNGEPT",
                "NEXUSPHP",
            }
        )

    def _nexus_parts(self, meta: Meta) -> list[str]:
        value = str(meta.nexusphp_description or "")
        return (
            [value] if self.tracker in self._nexus_trackers() and value else []
        )

    @staticmethod
    def _meta_description_text(meta: Meta) -> str:
        value = meta.description
        if value is None:
            return ""
        return value if isinstance(value, str) else str(value)

    def _is_aither_framestor(self, meta: Meta) -> bool:
        return (
            self.tracker == "AITHER"
            and "framestor" in meta
            and bool(meta.framestor)
        )

    @staticmethod
    def _aither_framestor_nfo(meta: Meta) -> str:
        content = str(meta.description_nfo_content or "")
        if not content:
            return ""
        rendered = f"[code]{content}[/code]"
        return rendered.replace(
            "https://i.imgur.com/e9o0zpQ.png",
            "https://beyondhd.co/images/2017/11/30/c5802892418ee2046efba17166f0cad9.png",
        )

    @staticmethod
    def _remove_embedded_nfo(description: str) -> str:
        return re.sub(
            r"\[center\]\[spoiler=.*? NFO:\]\[code\](.*?)\[/code\]\[/spoiler\]\[/center\]",
            "",
            description,
            flags=re.DOTALL,
        )

    def _api_description_part(
        self, meta: Meta, enabled: bool
    ) -> tuple[str, bool]:
        if not enabled:
            return "", False
        description = self._meta_description_text(meta)
        if not self._is_aither_framestor(meta):
            return description, False
        nfo = self._aither_framestor_nfo(meta)
        if nfo:
            return nfo, True
        return self._remove_embedded_nfo(description), False

    def _formatted_nfo_content(self, content: str) -> str:
        if self.tracker == "DIGITALCORE":
            return f"[nfo]{content}[/nfo]"
        if self.tracker == "TORRENTLEECH":
            return (
                "<div style='display: flex; justify-content: center;'><div "
                f"style='background-color: #000000; color: #ffffff;'>{content}</div></div>"
            )
        return f"[pre]{content}[/pre]"

    def _nfo_part(self, meta: Meta, enabled: bool) -> str:
        if not enabled:
            return ""
        content = meta.description_nfo_content
        if not isinstance(content, str) or not content:
            return ""
        return self._formatted_nfo_content(content)

    async def _user_description_part(
        self, meta: Meta, enabled: bool, description_enabled: bool
    ) -> str:
        if not enabled:
            return ""
        content = await self.get_user_description(meta)
        meta_description = self._meta_description_text(meta)
        if description_enabled and content.strip() == meta_description.strip():
            return ""
        return content

    async def _menu_part(self, meta: Meta, enabled: bool) -> str:
        return await self.menu_section(meta) if enabled else ""

    async def _tonemapped_part(self, meta: Meta, enabled: bool) -> str:
        return await self.get_tonemapped_header(meta) if enabled else ""

    async def _screenshots_part(
        self,
        meta: Meta,
        enabled: bool,
        approved_image_hosts: Sequence[str],
        images: list[Any],
        multi_screens: int,
    ) -> str:
        if not enabled:
            return ""
        return await self._handle_discs_and_screenshots(
            meta, approved_image_hosts, images, multi_screens
        )

    async def _spectrogram_part(self, meta: Meta, enabled: bool) -> str:
        return (
            await self.get_audio_spectrogram_section(meta) if enabled else ""
        )

    async def _hdr_plot_part(self, meta: Meta, enabled: bool) -> str:
        return await self.get_dynamic_hdr_plot_section(meta) if enabled else ""

    async def _custom_signature_part(self, meta: Meta, enabled: bool) -> str:
        return await self.get_custom_signature(meta) if enabled else ""

    @staticmethod
    def _ua_signature_part(meta: Meta, enabled: bool, signature: str) -> str:
        if not enabled:
            return ""
        if signature:
            return signature
        return (
            "[right][url=https://github.com/wastaken7/Upload-Assistant][size=4]"
            f"{meta.ua_signature}[/size][/url][/right]"
        )

    async def _save_debug_description(
        self, meta: Meta, description: str
    ) -> None:
        if not meta.debug:
            return
        path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}]DESCRIPTION.txt"
        )
        logger.debug(
            f"DEBUG: Saving final description to [yellow]{path}[/yellow]"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(description)

    async def general_description_generator(
        self,
        meta: Meta,
        audio_spectrogram: bool = True,
        bluray: bool = True,
        book: bool = True,
        custom_header: bool = True,
        custom_signature: bool = True,
        description: bool = True,
        game: bool = True,
        languages: bool = True,
        logo: bool = True,
        mediainfo: bool = True,
        menu_screenshots: bool = True,
        nfo: bool = True,
        screenshots: bool = True,
        tonemapped_header: bool = True,
        tv_info: bool = True,
        ua_signature: bool = True,
        user_description: bool = True,
        music: bool = True,
        dynamic_hdr_plot: bool = True,
        approved_image_hosts: Sequence[str] | None = None,
        signature: str = "",
        desc_header: str = "",
    ) -> str:
        images, multi_screens = self._description_image_state(meta)
        approved_hosts = approved_image_hosts or []
        parts: list[str] = []
        parts.extend(
            await self._custom_header_parts(meta, custom_header, desc_header)
        )
        parts.extend(await self._language_parts(meta, languages))
        parts.extend(await self._logo_parts(meta, logo))
        parts.extend(await self._mediainfo_description_parts(meta, mediainfo))
        parts.extend(await self._bluray_parts(meta, bluray))
        parts.extend(await self._tv_parts(meta, tv_info))
        parts.extend(self._book_parts(meta, book))
        parts.extend(self._game_parts(meta, game))
        parts.extend(self._music_parts(meta, music))
        parts.extend(self._mteam_parts(meta))
        parts.extend(self._nexus_parts(meta))
        api_description, suppress_images = self._api_description_part(
            meta, description
        )
        parts.append(api_description)
        parts.append(self._nfo_part(meta, nfo))
        parts.append(
            await self._user_description_part(
                meta, user_description, description
            )
        )
        parts.append(await self._menu_part(meta, menu_screenshots))
        parts.append(await self._tonemapped_part(meta, tonemapped_header))
        screenshot_images = [] if suppress_images else images
        parts.append(
            await self._screenshots_part(
                meta,
                screenshots,
                approved_hosts,
                screenshot_images,
                multi_screens,
            )
        )
        parts.append(await self._spectrogram_part(meta, audio_spectrogram))
        parts.append(await self._hdr_plot_part(meta, dynamic_hdr_plot))
        parts.append(await self._custom_signature_part(meta, custom_signature))
        parts.append(self._ua_signature_part(meta, ua_signature, signature))
        description_str = "\n".join(part for part in parts if part.strip())
        description_str = self.tracker_specific_formats(
            self.tracker, description_str
        )
        await self._save_debug_description(meta, description_str)
        return description_str

    @staticmethod
    def _pack_images_path(meta: Meta) -> Path:
        return (
            Path(meta.base_dir) / "tmp" / meta.uuid / "pack_image_links.json"
        )

    @staticmethod
    async def _load_pack_images_file(path: Path) -> dict[str, Any]:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            parsed = json.loads(await handle.read())
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}

    @staticmethod
    def _pack_image_keys(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = data.get("keys", {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): cast(dict[str, Any], value)
            for key, value in cast(dict[Any, Any], raw).items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _approved_image_host(hostname: str, approved_hosts: set[str]) -> bool:
        if not approved_hosts:
            return True
        return any(
            hostname == approved_host or hostname.endswith(f".{approved_host}")
            for approved_host in approved_hosts
        )

    @staticmethod
    def _pack_image_hostname(raw_url: str) -> str | None:
        try:
            parsed_url: ParseResult = urllib.parse.urlparse(raw_url)
            return parsed_url.netloc
        except Exception:
            logger.debug(f"[yellow]Could not parse URL: {raw_url}[/yellow]")
            return None

    @staticmethod
    def _string_image_mapping(value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        return {
            str(key): str(item)
            for key, item in cast(dict[Any, Any], value).items()
        }

    def _approved_pack_image(
        self,
        value: Any,
        approved_hosts: set[str],
        debug: bool,
    ) -> dict[str, str] | None:
        image = self._string_image_mapping(value)
        if image is None:
            return None
        hostname = self._pack_image_hostname(image.get("raw_url", ""))
        if hostname is None:
            return None
        if self._approved_image_host(hostname, approved_hosts):
            return image
        if debug:
            logger.info(
                f"[yellow]Filtering out image from non-approved host: {hostname}[/yellow]"
            )
        return None

    def _filtered_pack_key(
        self,
        key_data: dict[str, Any],
        approved_hosts: set[str],
        debug: bool,
    ) -> dict[str, Any] | None:
        raw_images = key_data.get("images", [])
        images = (
            cast(list[Any], raw_images) if isinstance(raw_images, list) else []
        )
        filtered = [
            image
            for raw_image in images
            if (
                image := self._approved_pack_image(
                    raw_image, approved_hosts, debug
                )
            )
            is not None
        ]
        if not filtered:
            return None
        updated = dict(key_data)
        updated["images"] = filtered
        updated["count"] = len(filtered)
        return updated

    def _filter_pack_image_keys(
        self,
        data: dict[str, Any],
        approved_hosts: set[str],
        debug: bool,
    ) -> dict[str, dict[str, Any]]:
        filtered: dict[str, dict[str, Any]] = {}
        for key_name, key_data in self._pack_image_keys(data).items():
            accepted = self._filtered_pack_key(key_data, approved_hosts, debug)
            if accepted is None:
                logger.debug(
                    f"[yellow]Removed key '{key_name}' - no approved image hosts[/yellow]"
                )
                continue
            filtered[key_name] = accepted
        return filtered

    @staticmethod
    def _pack_image_total(keys: dict[str, dict[str, Any]]) -> int:
        return sum(
            int(key_data.get("count", 0) or 0) for key_data in keys.values()
        )

    def _validated_pack_images(
        self,
        data: dict[str, Any],
        approved_hosts: set[str],
        debug: bool,
        path: Path,
    ) -> dict[str, Any]:
        keys = self._filter_pack_image_keys(data, approved_hosts, debug)
        total_count = self._pack_image_total(keys)
        if total_count < 3:
            logger.debug(
                "[yellow]Invalidating pack images - less than 3 approved images total[/yellow]"
            )
            return {}
        result: dict[str, Any] = dict(data)
        result["keys"] = keys
        result["total_count"] = total_count
        logger.debug(f"[green]Loaded previously uploaded images from {path}")
        logger.debug(
            f"[blue]Found {total_count} approved images across {len(keys)} keys[/blue]"
        )
        return result

    async def _check_saved_pack_image_links(
        self, meta: Meta, approved_image_hosts: Sequence[str]
    ) -> dict[str, Any]:
        path = self._pack_images_path(meta)
        if not await self.common.path_exists(str(path)):
            return {}
        try:
            data = await self._load_pack_images_file(path)
            return self._validated_pack_images(
                data, set(approved_image_hosts or []), bool(meta.debug), path
            )
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Could not load pack image data: {error!s}[/yellow]"
            )
            return {}

    async def _screenshot_context(
        self,
        meta: Meta,
        approved_image_hosts: Sequence[str],
        multi_screens: int,
    ) -> _ScreenshotContext:
        try:
            screenheader = await self.screenshot_header(meta)
        except Exception:
            screenheader = None
        return _ScreenshotContext(
            screenheader=screenheader,
            pack_images_data=await self._check_saved_pack_image_links(
                meta, approved_image_hosts
            ),
            approved_image_hosts=tuple(approved_image_hosts or ()),
            char_limit=self._get_int_config("charLimit", 14000),
            file_limit=self._get_int_config("fileLimit", 5),
            thumb_size=self._get_int_config("pack_thumb_size", 300),
            process_limit=self._get_int_config("processLimit", 10),
            screens_per_row=await self.get_screens_per_row(),
            multi_screens=multi_screens,
        )

    @staticmethod
    def _limited_screenshot_images(
        images: list[Any], limit: int | None
    ) -> list[Any]:
        return images if limit is None else images[:limit]

    def _render_screenshot_images(
        self,
        images: list[Any],
        context: _ScreenshotContext,
        *,
        limit: int | None = None,
        thumb_size: str | int = "",
        use_img_url: bool = True,
    ) -> str:
        parts: list[str] = []
        for index, raw_image in enumerate(
            self._limited_screenshot_images(images, limit)
        ):
            fields = self._hosted_image_fields(raw_image)
            if fields is None:
                continue
            web_url, raw_url, img_url = fields
            rendered = self.format_screenshot(
                web_url,
                raw_url,
                img_url if use_img_url else "",
                thumb_size,
            )
            parts.append(rendered)
            self._append_screenshot_row_separator(
                parts, index, context.screens_per_row
            )
        return "".join(parts)

    @staticmethod
    def _image_record_url(
        item: dict[str, Any], key: str, fallback: str = ""
    ) -> str:
        value = item.get(key)
        return str(value) if value else fallback

    @classmethod
    def _normalized_image_record(cls, value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict):
            return None
        item = cast(dict[str, Any], value)
        raw_url = cls._image_record_url(item, "raw_url")
        web_url = cls._image_record_url(item, "web_url")
        if not raw_url or not web_url:
            return None
        return {
            "img_url": cls._image_record_url(item, "img_url", raw_url),
            "raw_url": raw_url,
            "web_url": web_url,
        }

    @classmethod
    def _normalized_image_records(cls, values: Any) -> list[dict[str, str]]:
        if not isinstance(values, list):
            return []
        records = [
            cls._normalized_image_record(raw)
            for raw in cast(list[Any], values)
        ]
        return [record for record in records if record is not None]

    @staticmethod
    def _pack_key_images(
        pack_images_data: dict[str, Any], key: str
    ) -> list[dict[str, str]]:
        raw_keys = pack_images_data.get("keys", {})
        if not isinstance(raw_keys, dict):
            return []
        raw_entry = cast(dict[str, Any], raw_keys).get(key, {})
        if not isinstance(raw_entry, dict):
            return []
        return DescriptionBuilder._normalized_image_records(
            cast(dict[str, Any], raw_entry).get("images", [])
        )

    def _restore_pack_images(
        self, meta: Meta, key: str, context: _ScreenshotContext
    ) -> bool:
        images = self._pack_key_images(context.pack_images_data, key)
        if not images:
            return False
        logger.debug(
            f"[yellow]Using saved images from pack_image_links.json for {key}"
        )
        meta[key] = images
        return True

    @staticmethod
    def _manifest_names(meta: Meta, group: str) -> list[str]:
        return [
            file.name
            for file in manifest_files(meta.base_dir, meta.uuid, group)
        ]

    async def _upload_screenshot_group(
        self,
        meta: Meta,
        key: str,
        screens: list[str],
        context: _ScreenshotContext,
    ) -> list[dict[str, str]]:
        if not screens or meta.skip_imghost_upload:
            return []
        uploaded_raw, _ = await self.uploadscreens_manager.upload_screens(
            meta,
            context.multi_screens,
            1,
            0,
            context.multi_screens,
            screens,
            {key: meta[key]},
            allowed_hosts=context.approved_image_hosts,
        )
        uploaded = self._normalized_image_records(uploaded_raw)
        if uploaded:
            await self.common.save_image_links(meta, key, uploaded)
            meta[key].extend(uploaded)
        return uploaded

    @staticmethod
    async def _save_description_meta(meta: Meta) -> None:
        path = Path(meta.base_dir) / "tmp" / meta.uuid / "meta.json"
        async with aiofiles.open(path, "w") as handle:
            await handle.write(
                json.dumps(meta.to_dict(), indent=4, cls=PathAwareEncoder)
            )

    def _game_screenshot_section(
        self,
        meta: Meta,
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        limit = meta.screens if meta.screens is not None else 6
        parts = (
            [context.screenheader + "\n"]
            if context.screenheader is not None
            else []
        )
        parts.extend(
            (
                "[center]",
                self._render_screenshot_images(
                    images,
                    context,
                    limit=limit,
                    use_img_url=False,
                ),
                "[/center]",
            )
        )
        return "".join(parts)

    @staticmethod
    def _single_dvd_details(disc: dict[str, Any]) -> str:
        vob = str(disc.get("vob") or "")
        return (
            "[center]"
            f"[spoiler={Path(vob).name}][code]{disc.get('vob_mi', '')}[/code][/spoiler]\n\n"
            "[/center]"
        )

    @staticmethod
    def _bdinfo_keys(disc: dict[str, Any]) -> list[str]:
        return [key for key in disc if key.startswith("bdinfo")]

    @staticmethod
    def _playlist_details(
        disc: dict[str, Any], index: int, key: str
    ) -> tuple[str, str]:
        raw_bdinfo = disc.get(key, {})
        bdinfo = (
            cast(dict[str, Any], raw_bdinfo)
            if isinstance(raw_bdinfo, dict)
            else {}
        )
        edition = str(bdinfo.get("edition", "Unknown Edition"))
        summary = str(disc.get(f"summary_{index}", "No summary available"))
        return edition, summary

    async def _single_bdmv_playlist_section(
        self,
        meta: Meta,
        disc: dict[str, Any],
        index: int,
        key: str,
        context: _ScreenshotContext,
    ) -> str:
        image_key = f"new_images_playlist_{index}"
        edition, summary = self._playlist_details(disc, index, key)
        self._restore_pack_images(meta, image_key, context)
        parts = [
            "[center]\n\n",
            f"[spoiler={edition}][code]{summary}[/code][/spoiler]\n\n",
        ]
        existing = self._normalized_image_records(meta.get(image_key, []))
        if existing:
            logger.debug(
                "[yellow]Using original uploaded images for first disc"
            )
            parts.extend(
                (
                    "[center]",
                    self._render_screenshot_images(
                        existing,
                        context,
                        thumb_size=context.thumb_size,
                    ),
                    "[/center]\n\n",
                )
            )
            return "".join(parts)
        parts.append("[/center]\n\n")
        meta.retry_count += 1
        meta[image_key] = []
        screens = self._manifest_names(meta, f"PLAYLIST_{index}")
        if not screens:
            logger.warning(
                f"[yellow]Missing prepared screenshots for PLAYLIST_{index}; skipping its images in the description.[/yellow]"
            )
        uploaded = await self._upload_screenshot_group(
            meta, image_key, screens, context
        )
        if uploaded:
            parts.extend(
                (
                    "[center]",
                    self._render_screenshot_images(
                        uploaded,
                        context,
                        thumb_size=context.thumb_size,
                    ),
                    "[/center]\n\n",
                )
            )
        await self._save_description_meta(meta)
        return "".join(parts)

    async def _single_disc_section(
        self,
        meta: Meta,
        disc: dict[str, Any],
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        parts: list[str] = []
        if disc.get("type") == "DVD":
            parts.append(self._single_dvd_details(disc))
        if context.screenheader is not None:
            parts.append(context.screenheader + "\n")
        parts.extend(
            (
                "[center]",
                self._render_screenshot_images(
                    images, context, limit=meta.screens
                ),
                "[/center]",
            )
        )
        if disc.get("type") != "BDMV":
            return "".join(parts)
        keys = self._bdinfo_keys(disc)
        for index, key in enumerate(keys[1:], start=1):
            parts.append(
                await self._single_bdmv_playlist_section(
                    meta, disc, index, key, context
                )
            )
        return "".join(parts)

    @staticmethod
    def _first_multi_disc_details(disc: dict[str, Any]) -> str:
        if disc.get("type") == "BDMV":
            return f"{disc.get('name', 'BDINFO')}\n\n"
        if disc.get("type") != "DVD":
            return ""
        return (
            f"{disc.get('name', '')}:\n"
            f"[spoiler={Path(str(disc.get('vob', ''))).name}][code]{disc.get('vob_mi', '')}[/code][/spoiler]"
            f"[spoiler={Path(str(disc.get('ifo', ''))).name}][code]{disc.get('ifo_mi', '')}[/code][/spoiler]\n\n"
        )

    @staticmethod
    def _additional_disc_details(disc: dict[str, Any]) -> str:
        if disc.get("type") == "BDMV":
            return f"[spoiler={disc.get('name', 'BDINFO')}][code]{disc.get('summary', '')}[/code][/spoiler]\n\n"
        if disc.get("type") != "DVD":
            return ""
        return (
            f"{disc.get('name', '')}:\n"
            f"[spoiler={Path(str(disc.get('vob', ''))).name}][code]{disc.get('vob_mi', '')}[/code][/spoiler] "
            f"[spoiler={Path(str(disc.get('ifo', ''))).name}][code]{disc.get('ifo_mi', '')}[/code][/spoiler]\n\n"
        )

    def _first_multi_disc_section(
        self,
        meta: Meta,
        disc: dict[str, Any],
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        parts = ["[center]", self._first_multi_disc_details(disc)]
        logger.debug("[yellow]Using original uploaded images for first disc")
        if context.screenheader is not None:
            parts.extend(
                ("[/center]\n\n", context.screenheader + "\n", "[center]")
            )
        parts.extend(
            (
                self._render_screenshot_images(
                    images,
                    context,
                    limit=meta.screens,
                    thumb_size=context.thumb_size,
                ),
                "[/center]\n\n",
            )
        )
        return "".join(parts)

    async def _disc_capture_group(
        self, meta: Meta, disc: dict[str, Any], index: int
    ) -> list[str]:
        if disc.get("type") == "BDMV":
            return self._manifest_names(meta, f"FILE_{index}")
        if disc.get("type") != "DVD":
            return []
        group = await self.takescreens_manager.sanitize_filename(
            str(disc.get("name", ""))
        )
        return self._manifest_names(meta, group)

    @staticmethod
    def _log_disc_progress(
        index: int, total: int, disc: dict[str, Any]
    ) -> None:
        name = str(disc.get("name", f"Disc {index}"))
        display = name[:40] + ("..." if len(name) > 40 else "")
        logger.info(
            f"\rProcessing disc {index}/{total}: {display}",
            extra={"markup": False},
        )

    async def _additional_disc_section(
        self,
        meta: Meta,
        disc: dict[str, Any],
        index: int,
        context: _ScreenshotContext,
    ) -> str:
        key = f"new_images_disc_{index}"
        self._restore_pack_images(meta, key, context)
        details = (
            "[center]" + self._additional_disc_details(disc) + "[/center]\n\n"
        )
        existing = self._normalized_image_records(meta.get(key, []))
        if existing:
            logger.debug(f"[yellow]Found needed image URLs for {key}")
            return (
                details
                + "[center]"
                + self._render_screenshot_images(
                    existing, context, thumb_size=context.thumb_size
                )
                + "[/center]\n\n"
            )
        meta.retry_count += 1
        meta[key] = []
        screens = await self._disc_capture_group(meta, disc, index)
        if not screens:
            logger.warning(
                f"[yellow]Missing prepared screenshots for {key}; skipping its images in the description.[/yellow]"
            )
        uploaded = await self._upload_screenshot_group(
            meta, key, screens, context
        )
        await self._save_description_meta(meta)
        if not uploaded:
            return details
        return (
            details
            + "[center]"
            + self._render_screenshot_images(
                uploaded, context, thumb_size=context.thumb_size
            )
            + "[/center]\n\n"
        )

    async def _multi_disc_section(
        self,
        meta: Meta,
        discs: list[dict[str, Any]],
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        total = min(len(discs), context.process_limit)
        if context.multi_screens != 0:
            logger.info(
                "[cyan]Processing screenshots for packed content (multiScreens)[/cyan]"
            )
            logger.info(f"[cyan]{total} files (processLimit)[/cyan]")
        parts: list[str] = []
        for index, disc in enumerate(discs[: context.process_limit]):
            if index == 0:
                parts.append(
                    self._first_multi_disc_section(meta, disc, images, context)
                )
                continue
            if context.multi_screens == 0:
                continue
            self._log_disc_progress(index, total, disc)
            parts.append(
                await self._additional_disc_section(meta, disc, index, context)
            )
            logger.info("")
        return "".join(parts)

    @staticmethod
    def _comparison_group_source(meta: Meta) -> dict[Any, Any]:
        raw_groups = meta.comparison_groups
        if isinstance(raw_groups, dict):
            return cast(dict[Any, Any], raw_groups)
        if isinstance(raw_groups, list):
            return {
                str(index): value for index, value in enumerate(raw_groups)
            }
        return {}

    @classmethod
    def _comparison_groups(cls, meta: Meta) -> dict[str, dict[str, Any]]:
        return {
            str(key): cast(dict[str, Any], value)
            for key, value in cls._comparison_group_source(meta).items()
            if isinstance(value, dict)
        }

    @staticmethod
    def _comparison_url_lists(
        groups: dict[str, dict[str, Any]], indices: list[str]
    ) -> list[list[Any]]:
        lists: list[list[Any]] = []
        for index in indices:
            raw = groups[index].get("urls", [])
            lists.append(cast(list[Any], raw) if isinstance(raw, list) else [])
        return lists

    @staticmethod
    def _comparison_raw_url(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        return str(cast(dict[str, Any], value).get("raw_url") or "")

    @classmethod
    def _comparison_image_lines(cls, url_lists: list[list[Any]]) -> list[str]:
        count = min((len(urls) for urls in url_lists), default=0)
        lines: list[str] = []
        for image_index in range(count):
            for urls in url_lists:
                raw_url = cls._comparison_raw_url(urls[image_index])
                if raw_url:
                    lines.append(raw_url + "\n")
        return lines

    @classmethod
    def _comparison_section(cls, meta: Meta) -> str:
        if not meta.comparison or not meta.comparison_groups:
            return ""
        groups = cls._comparison_groups(meta)
        if not groups:
            return ""
        indices = sorted(groups, key=lambda value: int(value))
        names = [
            str(groups[index].get("name", f"Group {index}"))
            for index in indices
        ]
        url_lists = cls._comparison_url_lists(groups, indices)
        return "".join(
            [
                "[center]",
                f"[comparison={', '.join(names)}]\n",
                *cls._comparison_image_lines(url_lists),
                "[/comparison][/center]\n\n",
            ]
        )

    def _single_file_section(
        self,
        meta: Meta,
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        parts = [self._comparison_section(meta)]
        if context.screenheader is not None:
            parts.append(context.screenheader + "\n")
        parts.extend(
            (
                "[center]",
                self._render_screenshot_images(
                    images, context, limit=meta.screens
                ),
                "[/center]",
            )
        )
        return "".join(parts)

    @staticmethod
    def _log_file_progress(index: int, total: int, file_path: str) -> None:
        name = Path(file_path).name
        display = name[:40] + ("..." if len(name) > 40 else "")
        logger.info(
            f"\rProcessing file {index}/{total}: {display}",
            extra={"markup": False},
        )

    async def _capture_missing_file_screens(
        self,
        meta: Meta,
        file_path: str,
        index: int,
        context: _ScreenshotContext,
    ) -> list[str]:
        group = f"FILE_{index}"
        screens = self._manifest_names(meta, group)
        if screens:
            return screens
        if meta.debug:
            logger.info(
                f"[yellow]No existing screenshots for new_images_file_{index}; generating new ones."
            )
        try:
            await self.takescreens_manager.screenshots(
                file_path,
                group,
                meta.uuid,
                meta.base_dir,
                meta,
                context.multi_screens,
                True,
                capture_group=group,
            )
            await asyncio.sleep(0.1)
        except Exception as error:
            logger.info(
                f"Error during generic screenshot capture: {error}",
                extra={"markup": False},
            )
        return self._manifest_names(meta, group)

    async def _prepare_extra_file_images(
        self,
        meta: Meta,
        file_path: str,
        index: int,
        context: _ScreenshotContext,
    ) -> None:
        key = f"new_images_file_{index}"
        self._restore_pack_images(meta, key, context)
        if meta.get(key):
            return
        meta[key] = []
        screens = await self._capture_missing_file_screens(
            meta, file_path, index, context
        )
        uploaded = await self._upload_screenshot_group(
            meta, key, screens, context
        )
        if uploaded:
            await asyncio.sleep(0.1)

    @staticmethod
    def _log_multi_file_start(total: int, context: _ScreenshotContext) -> None:
        if context.multi_screens == 0 or total <= 1:
            return
        logger.info(
            "[cyan]Processing screenshots for packed content (multiScreens)[/cyan]"
        )
        logger.info(f"[cyan]{total} files (processLimit)[/cyan]")

    async def _prepare_multi_file_item(
        self,
        meta: Meta,
        file_path: str,
        index: int,
        total: int,
        context: _ScreenshotContext,
    ) -> None:
        if context.multi_screens == 0:
            return
        if total > 1:
            self._log_file_progress(index + 1, total, file_path)
        if index > 0:
            await self._prepare_extra_file_images(
                meta, file_path, index, context
            )
        await asyncio.sleep(0.05)

    async def _prepare_multi_file_images(
        self, meta: Meta, filelist: list[str], context: _ScreenshotContext
    ) -> int:
        total = min(len(filelist), context.process_limit)
        self._log_multi_file_start(total, context)
        for index, file_path in enumerate(filelist[: context.process_limit]):
            await self._prepare_multi_file_item(
                meta, file_path, index, total, context
            )
        await self._save_description_meta(meta)
        await asyncio.sleep(0.1)
        return total

    @staticmethod
    def _description_filename(file_path: str) -> str:
        return Path(file_path.strip()).stem.replace("[", "").replace("]", "")

    def _multi_file_mediainfo_heading(
        self, file_path: str, filename: str
    ) -> str:
        mi_dump = MediaInfo.parse(
            file_path,
            output="STRING",
            full=False,
            mediainfo_options={"inform_version": "1"},
        )
        formatted = self.parser.format_bbcode(
            self.parser.parse_mediainfo(mi_dump)
        )
        return f"[center][spoiler={filename}]{formatted}[/spoiler][/center]\n"

    @staticmethod
    def _first_multi_file_heading(
        filename: str, images: list[Any], context: _ScreenshotContext
    ) -> str:
        prefix = (
            context.screenheader + "\n"
            if images and context.screenheader is not None
            else ""
        )
        return prefix + f"[center]{filename}\n[/center]\n"

    def _multi_file_heading(
        self,
        file_path: str,
        index: int,
        images: list[Any],
        context: _ScreenshotContext,
        char_count: int,
    ) -> str:
        if context.multi_screens == 0:
            return ""
        filename = self._description_filename(file_path)
        if index > 0 and char_count < context.char_limit:
            return self._multi_file_mediainfo_heading(file_path, filename)
        return self._first_multi_file_heading(filename, images, context)

    def _first_multi_file_images_section(
        self, images: list[Any], context: _ScreenshotContext
    ) -> str:
        if not images:
            return ""
        header = (
            context.screenheader + "\n"
            if context.screenheader is not None
            else ""
        )
        return (
            header
            + "[center]"
            + self._render_screenshot_images(
                images, context, thumb_size=context.thumb_size
            )
            + "[/center]\n\n"
        )

    def _extra_multi_file_images_section(
        self, meta: Meta, index: int, context: _ScreenshotContext
    ) -> str:
        if context.multi_screens == 0:
            return ""
        key = f"new_images_file_{index}"
        existing = self._normalized_image_records(meta.get(key, []))
        if not existing:
            return ""
        return (
            "[center]"
            + self._render_screenshot_images(
                existing, context, thumb_size=context.thumb_size
            )
            + "[/center]\n\n"
        )

    def _multi_file_images_section(
        self,
        meta: Meta,
        index: int,
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        if index == 0:
            return self._first_multi_file_images_section(images, context)
        return self._extra_multi_file_images_section(meta, index, context)

    @staticmethod
    def _append_render_text(state: _MultiFileRenderState, text: str) -> None:
        state.parts.append(text)
        state.char_count += len(text)

    @staticmethod
    def _maybe_open_other_files_spoiler(
        state: _MultiFileRenderState,
        index: int,
        context: _ScreenshotContext,
    ) -> None:
        should_open = (
            context.multi_screens != 0
            and index >= context.file_limit
            and not state.spoiler_open
        )
        if not should_open:
            return
        opening = "[center][spoiler=Other files]\n"
        DescriptionBuilder._append_render_text(state, opening)
        state.spoiler_open = True

    def _render_multi_file_item(
        self,
        meta: Meta,
        file_path: str,
        index: int,
        images: list[Any],
        context: _ScreenshotContext,
        state: _MultiFileRenderState,
    ) -> None:
        self._maybe_open_other_files_spoiler(state, index, context)
        heading = self._multi_file_heading(
            file_path, index, images, context, state.char_count
        )
        self._append_render_text(state, heading)
        self._append_render_text(
            state,
            self._multi_file_images_section(meta, index, images, context),
        )

    @staticmethod
    def _finalize_multi_file_render(
        meta: Meta,
        total_files: int,
        state: _MultiFileRenderState,
    ) -> str:
        if state.spoiler_open:
            DescriptionBuilder._append_render_text(
                state, "[/spoiler][/center]\n"
            )
        if state.char_count >= 1 and meta.debug:
            logger.info(
                f"[yellow]Total characters written to description: {state.char_count}"
            )
        if total_files > 1:
            logger.info("")
        return "".join(state.parts)

    async def _render_multi_file_sections(
        self,
        meta: Meta,
        filelist: list[str],
        images: list[Any],
        context: _ScreenshotContext,
        total_files: int,
    ) -> str:
        if len(filelist) <= 1:
            return ""
        state = _MultiFileRenderState()
        for index, file_path in enumerate(filelist[: context.process_limit]):
            self._render_multi_file_item(
                meta, file_path, index, images, context, state
            )
        return self._finalize_multi_file_render(meta, total_files, state)

    @staticmethod
    def _disc_dicts(meta: Meta) -> list[dict[str, Any]]:
        raw = meta.discs
        return [
            cast(dict[str, Any], disc)
            for disc in raw
            if isinstance(disc, dict)
        ]

    async def _disc_screenshot_section(
        self,
        meta: Meta,
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        discs = self._disc_dicts(meta)
        if len(discs) == 1:
            return await self._single_disc_section(
                meta, discs[0], images, context
            )
        if len(discs) > 1:
            return await self._multi_disc_section(meta, discs, images, context)
        return ""

    async def _file_screenshot_section(
        self,
        meta: Meta,
        images: list[Any],
        context: _ScreenshotContext,
    ) -> str:
        filelist = [str(file_path) for file_path in meta.filelist]
        parts = (
            [self._single_file_section(meta, images, context)]
            if len(filelist) == 1
            else []
        )
        total_files = await self._prepare_multi_file_images(
            meta, filelist, context
        )
        parts.append(
            await self._render_multi_file_sections(
                meta, filelist, images, context, total_files
            )
        )
        return "".join(part for part in parts if part)

    async def _handle_discs_and_screenshots(
        self,
        meta: Meta,
        approved_image_hosts: Sequence[str],
        images: list[dict[str, str]],
        multi_screens: int,
    ) -> str:
        if not images:
            return ""
        context = await self._screenshot_context(
            meta, approved_image_hosts, multi_screens
        )
        image_values = cast(list[Any], images)
        if meta.category == "GAME":
            return self._game_screenshot_section(meta, image_values, context)
        disc_section = await self._disc_screenshot_section(
            meta, image_values, context
        )
        file_section = await self._file_screenshot_section(
            meta, image_values, context
        )
        return disc_section + file_section

    @staticmethod
    def _hawkeuno_screens_per_row(screens_per_row: int, width: int) -> int:
        while screens_per_row * width > 1100 and screens_per_row > 1:
            screens_per_row -= 1
        return screens_per_row

    async def get_screens_per_row(self) -> int:
        try:
            if self.tracker == "TORRENTLEECH":
                return 2
            screens_per_row = self._get_int_config("screens_per_row", 2)
            if self.tracker != "HAWKEUNO":
                return screens_per_row
            return self._hawkeuno_screens_per_row(
                screens_per_row, self._get_int_config("thumbnail_size", 350)
            )
        except Exception:
            return 2

    def _append_screenshot_row_separator(
        self, parts: list[str], image_index: int, screens_per_row: int
    ) -> str:
        if not screens_per_row or (image_index + 1) % screens_per_row != 0:
            return ""

        separator = "<br><br>" if self.tracker == "TORRENTLEECH" else "\n"
        parts.append(separator)
        return separator

    async def _menu_image_lines(
        self, meta: Meta, screens_per_row: int
    ) -> list[str]:
        raw_images = get_tracker_image_collection(
            meta, self.tracker, "menu_images"
        )
        images = (
            cast(list[Any], raw_images) if isinstance(raw_images, list) else []
        )
        lines: list[str] = []
        for index, raw_image in enumerate(images):
            fields = self._hosted_image_fields(raw_image)
            if fields is None:
                continue
            web_url, raw_url, img_url = fields
            lines.append(self.format_screenshot(web_url, raw_url, img_url))
            self._append_screenshot_row_separator(
                lines, index, screens_per_row
            )
        return lines

    async def _disc_menu_section(self, meta: Meta) -> str:
        if not meta.is_disc:
            return ""
        header = await self.menu_screenshot_header(meta)
        lines = await self._menu_image_lines(
            meta, await self.get_screens_per_row()
        )
        if not lines:
            return ""
        parts = [header + "\n"] if header else []
        parts.extend(("[center]", *lines, "[/center]\n\n"))
        return "".join(parts)

    async def menu_section(self, meta: Meta) -> str:
        try:
            return await self._disc_menu_section(meta)
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Error processing disc menu section: {error!s}[/yellow]"
            )
            return ""

    @staticmethod
    def _nexus_screenshot_trackers() -> frozenset[str]:
        return frozenset(
            {
                "1PTBA",
                "LAJIDUI",
                "LEMONHD",
                "LONGPT",
                "PTCAFE",
                "PTFANS",
                "PTGTK",
                "PTZONE",
                "RAILGUNPT",
                "XINGYUNGEPT",
                "NEXUSPHP",
            }
        )

    @staticmethod
    def _inline_image_screenshot(web_url: str, img_url: str) -> str:
        suffix = "\n" if "imgbox" not in web_url else " "
        return f"[url={web_url}][img]{img_url}[/img][/url]{suffix}"

    def _tracker_screenshot_markup(
        self,
        web_url: str,
        raw_url: str,
        img_url: str,
        thumb_size: str | int,
    ) -> str | None:
        if self.tracker in self._nexus_screenshot_trackers():
            return f"[img]{raw_url}[/img]"
        direct = {
            "HDTORRENTS": f"<a href='{raw_url}'><img src='{img_url}' height=137></a> ",
            "TORRENTLEECH": f'<a href="{web_url}"><img src="{img_url}" style="max-width: 350px;"></a>  ',
            "FUNFILE": f'<a href="{web_url}" target="_blank"><img src="{img_url}" width="{thumb_size}"></a> ',
            "GREATPOSTERWALL": f"[img]{raw_url}[/img] ",
        }
        if self.tracker in {"HDSPACE", "IPTORRENTS"}:
            return self._inline_image_screenshot(web_url, img_url)
        return direct.get(self.tracker)

    def format_screenshot(
        self,
        web_url: str,
        raw_url: str,
        img_url: str = "",
        thumb_size: str | int = "",
    ) -> str:
        resolved_img_url = img_url or raw_url
        resolved_thumb_size = thumb_size or self._get_int_config(
            "thumbnail_size", 350
        )
        tracker_markup = self._tracker_screenshot_markup(
            web_url, raw_url, resolved_img_url, resolved_thumb_size
        )
        if tracker_markup is not None:
            return tracker_markup
        return (
            f"[url={web_url}][img={resolved_thumb_size}]{raw_url}[/img][/url] "
        )

    @staticmethod
    def _format_brasiltracker_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = bbcode.remove_img_resize(description)
        return bbcode.remove_list(description)

    @staticmethod
    def _format_bjshare_description(description: str, bbcode: BBCODE) -> str:
        description = bbcode.convert_named_spoiler_to_named_hide(description)
        description = bbcode.convert_spoiler_to_hide(description)
        description = bbcode.remove_img_resize(description)
        description = bbcode.convert_to_align(description)
        description = bbcode.remove_list(description)
        return description.replace("[code]", "[pre]").replace(
            "[/code]", "[/pre]"
        )

    @staticmethod
    def _format_anthelion_description(description: str, bbcode: BBCODE) -> str:
        description = bbcode.convert_to_align(description)
        description = bbcode.remove_img_resize(description)
        description = bbcode.remove_sup(description)
        description = bbcode.remove_sub(description)
        description = bbcode.remove_list(description)
        description = (
            description.replace("•", "-")
            .replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
            .replace("\N{EN DASH}", "-")
        )
        return description.replace("[code]", "[pre]").replace(
            "[/code]", "[/pre]"
        )

    @staticmethod
    def _format_digitalcore_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[right]", "").replace(
            "[/right]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = bbcode.remove_sup(description)
        description = bbcode.remove_sub(description)
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = description.replace("[*] ", "• ").replace("[*]", "• ")
        description = bbcode.convert_named_spoiler_to_normal_spoiler(
            description
        )
        description = bbcode.remove_list(description)
        return description.strip()

    @staticmethod
    def _format_funfile_description(description: str, bbcode: BBCODE) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[right]", "").replace(
            "[/right]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = bbcode.remove_sub(description)
        description = bbcode.remove_sup(description)
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = description.replace("[hide]", "").replace("[/hide]", "")
        description = (
            description.replace("•", "-").replace("“", '"').replace("”", '"')
        )
        description = bbcode.convert_comparison_to_centered(description, 1000)
        description = bbcode.remove_spoiler(description)
        description = re.sub(
            "\\[url=(?P<href>[^\\]]+)\\]\\[img=(?P<width>\\d+)\\](?P<src>[^\\[]+)\\[/img\\]\\[/url\\]",
            '<a href="\\g<href>" target="_blank"><img src="\\g<src>" width="\\g<width>"></a>',
            description,
            flags=re.IGNORECASE,
        )
        description = re.sub(
            "\\[url=(?P<href>[^\\]]+)\\]\\[img\\](?P<src>[^\\[]+)\\[/img\\]\\[/url\\]",
            '<a href="\\g<href>" target="_blank"><img src="\\g<src>" width="220"></a>',
            description,
            flags=re.IGNORECASE,
        )
        return re.sub(
            "\\[img=(?P<width>\\d+)\\](?P<src>[^\\[]+)\\[/img\\]",
            '<img src="\\g<src>" width="\\g<width>">',
            description,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _format_greatposterwall_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = bbcode.remove_sup(description)
        description = bbcode.remove_sub(description)
        description = bbcode.convert_to_align(description)
        description = bbcode.remove_list(description)
        description = description.replace("[code]", "[pre]").replace(
            "[/code]", "[/pre]"
        )
        return re.sub(
            "\\[url=[^\\]]+\\]\\[img(?:=[^\\]]+)?\\]([^\\[]+)\\[/img\\]\\[/url\\]",
            "[img]\\1[/img]",
            description,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _format_hdspace_description(description: str, bbcode: BBCODE) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[right]", "").replace(
            "[/right]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = bbcode.remove_sub(description)
        description = bbcode.remove_sup(description)
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = bbcode.remove_hide(description)
        description = bbcode.remove_img_resize(description)
        description = bbcode.convert_comparison_to_centered(description, 1000)
        description = bbcode.remove_spoiler(description)
        description = bbcode.remove_color(description)

        def hds_image_formatter(match: re.Match[str]) -> str:
            web_url = match.group(1)
            raw_url = match.group(2)
            if "imgbox" not in web_url:
                return f"[url={web_url}][img]{raw_url}[/img][/url]\n"
            return f"[url={web_url}][img]{raw_url}[/img][/url]"

        pattern = "\\[url=([^\\]]+)\\]\\[img(?:=[^\\]]*)?\\]([^\\[]+)\\[/img\\]\\[/url\\]\\s*"
        return re.sub(pattern, hds_image_formatter, description)

    @staticmethod
    def _format_iptorrents_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[right]", "").replace(
            "[/right]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = bbcode.remove_sub(description)
        description = bbcode.remove_sup(description)
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = bbcode.remove_hide(description)
        description = bbcode.remove_img_resize(description)
        description = bbcode.convert_comparison_to_centered(description, 1000)
        return bbcode.remove_spoiler(description)

    @staticmethod
    def _format_hdtorrents_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = bbcode.remove_sub(description)
        description = bbcode.remove_sup(description)
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = bbcode.convert_spoiler_to_hide(description)
        description = bbcode.remove_img_resize(description)
        description = bbcode.convert_comparison_to_centered(description, 1000)
        description = bbcode.remove_spoiler(description)
        return bbcode.remove_list(description)

    @staticmethod
    def _format_ptskit_description(description: str, bbcode: BBCODE) -> str:
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[align=left]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[right]", "").replace(
            "[/right]", ""
        )
        description = description.replace("[align=right]", "").replace(
            "[/align]", ""
        )
        description = description.replace("[sup]", "").replace("[/sup]", "")
        description = description.replace("[sub]", "").replace("[/sub]", "")
        description = description.replace("[alert]", "").replace(
            "[/alert]", ""
        )
        description = description.replace("[note]", "").replace("[/note]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[h1]", "[u][b]").replace(
            "[/h1]", "[/b][/u]"
        )
        description = description.replace("[h2]", "[u][b]").replace(
            "[/h2]", "[/b][/u]"
        )
        description = description.replace("[h3]", "[u][b]").replace(
            "[/h3]", "[/b][/u]"
        )
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        description = description.replace("[hide]", "").replace("[/hide]", "")
        description = re.sub(
            "\\[center\\]\\[spoiler=.*? NFO:\\]\\[code\\](.*?)\\[/code\\]\\[/spoiler\\]\\[/center\\]",
            "",
            description,
            flags=re.DOTALL,
        )
        description = bbcode.convert_comparison_to_centered(description, 1000)
        description = bbcode.remove_spoiler(description)
        return re.sub("\\n{3,}", "\n\n", description)

    @staticmethod
    def _format_speedapp_description(description: str, bbcode: BBCODE) -> str:
        description = bbcode.remove_img_resize(description)
        description = bbcode.convert_named_spoiler_to_normal_spoiler(
            description
        )
        description = (
            description.replace("[note]", "Note: ")
            .replace("[/note]", "")
            .replace("[code]", "")
            .replace("[/code]", "")
            .replace("[*]", "• ")
        )
        description = bbcode.remove_spoiler(description)
        return bbcode.remove_list(description)

    @staticmethod
    def _format_torrentleech_description(
        description: str, bbcode: BBCODE
    ) -> str:
        description = description.replace("[center]", "<center>").replace(
            "[/center]", "</center>"
        )
        description = re.sub(
            "\\[\\*\\]", "\n[*]", description, flags=re.IGNORECASE
        )
        description = re.sub(
            "\\[c\\](.*?)\\[/c\\]",
            "[code]\\1[/code]",
            description,
            flags=re.IGNORECASE | re.DOTALL,
        )
        description = re.sub(
            "\\[hr\\]", "---", description, flags=re.IGNORECASE
        )
        description = re.sub(
            '\\[img=[\\d"x]+\\]', "[img]", description, flags=re.IGNORECASE
        )
        description = (
            description.replace("[*] ", "• ")
            .replace("[*]", "• ")
            .replace("[note]", "Note: ")
            .replace("[/note]", "")
            .replace("[code]", "")
            .replace("[/code]", "")
        )
        description = bbcode.remove_list(description)
        description = bbcode.convert_comparison_to_centered(description, 1000)
        description = bbcode.remove_spoiler(description)
        return re.sub("\\n{3,}", "\n\n", description)

    @staticmethod
    def _format_immortalseed_description(
        description: str, _bbcode: BBCODE
    ) -> str:
        description = html.unescape(description)
        description = re.sub(
            "<br\\s*/?\\s*>", "\n", description, flags=re.IGNORECASE
        )
        description = re.sub(
            "</(?:p|div|li|tr|h[1-6]|blockquote|pre)\\s*>",
            "\n",
            description,
            flags=re.IGNORECASE,
        )
        description = re.sub(
            "<!--.*?-->|<![^>]*>|</?[a-z][^>]*>",
            "",
            description,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return re.sub(
            "\\[/?[a-z][a-z0-9_-]*(?:=[^\\]]*|\\s+[^\\]]*)?\\]|\\[\\*\\]",
            "",
            description,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _format_unit3d_description(description: str, bbcode: BBCODE) -> str:
        description = bbcode.convert_hide_to_spoiler(description)
        description = description.replace("[user]", "").replace("[/user]", "")
        description = description.replace("[hr]", "").replace("[/hr]", "")
        description = description.replace("[ul]", "").replace("[/ul]", "")
        description = description.replace("[ol]", "").replace("[/ol]", "")
        return bbcode.convert_comparison_to_collapse(description, 1000)

    def tracker_specific_formats(self, tracker: str, description: str) -> str:
        bbcode = BBCODE()
        formatters: dict[str, Callable[[str, BBCODE], str]] = {
            "BRASILTRACKER": self._format_brasiltracker_description,
            "BJSHARE": self._format_bjshare_description,
            "ANTHELION": self._format_anthelion_description,
            "DIGITALCORE": self._format_digitalcore_description,
            "FUNFILE": self._format_funfile_description,
            "GREATPOSTERWALL": self._format_greatposterwall_description,
            "HDSPACE": self._format_hdspace_description,
            "IPTORRENTS": self._format_iptorrents_description,
            "HDTORRENTS": self._format_hdtorrents_description,
            "PTSKIT": self._format_ptskit_description,
            "SPEEDAPP": self._format_speedapp_description,
            "TORRENTLEECH": self._format_torrentleech_description,
            "IMMORTALSEED": self._format_immortalseed_description,
        }
        formatter = formatters.get(tracker)
        if formatter is not None:
            description = formatter(description, bbcode)
        from src.integrations.trackers.registry import (
            api_trackers as unit3d_trackers,
        )

        if tracker in unit3d_trackers:
            description = self._format_unit3d_description(description, bbcode)
        return bbcode.remove_extra_lines(description)
