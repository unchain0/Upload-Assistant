# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
from collections.abc import Callable, Mapping
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
from rich.markup import escape

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.observability.terminal_link_formatting import (
    format_terminal_link,
)
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.registry import tracker_class_map
from src.services.disc_comparison_service import (
    compare_bdinfo,
    has_bdinfo_content,
)

DupeEntry = dict[str, Any]


def _dict_value(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _music_release_parts(
    meta: Meta,
) -> tuple[
    dict[str, Any],
    list[Any],
    dict[str, Any],
    list[Any],
    dict[str, Any],
]:
    release = _dict_value(meta.music_release)
    return (
        _dict_value(release.get("fields")),
        _list_value(release.get("tracks")),
        _dict_value(release.get("auxiliary")),
        _list_value(release.get("warnings")),
        _dict_value(release.get("conflicts")),
    )


def _music_field_value(
    fields: Mapping[str, Any], name: str, fallback: Any = ""
) -> Any:
    entry = fields.get(name, {})
    if not isinstance(entry, dict):
        return fallback
    value = cast(dict[str, Any], entry).get("value")
    return fallback if value in (None, "", [], {}) else value


def _music_field_source(fields: Mapping[str, Any], name: str) -> str:
    entry = fields.get(name, {})
    source_name = (
        cast(dict[str, Any], entry).get("source", "")
        if isinstance(entry, dict)
        else ""
    )
    labels = {
        "file_tag": "tags",
        "auxiliary": "auxiliary files",
        "directory": "folder name",
        "external": "external metadata",
        "user": "user input",
        "tracker": "tracker",
        "inferred": "inferred",
    }
    return labels.get(str(source_name), "")


def _music_display(
    fields: Mapping[str, Any], name: str, fallback: Any = ""
) -> str:
    item = _music_field_value(fields, name, fallback)
    if isinstance(item, list):
        item = " & ".join(str(part) for part in cast(list[Any], item))
    text = str(item).strip()
    provenance = _music_field_source(fields, name)
    suffix = f" [dim]({provenance})[/dim]" if text and provenance else ""
    return escape(text) + suffix


def _music_track_value(track: Any, key: str) -> Any | None:
    if not isinstance(track, dict):
        return None
    value = cast(dict[str, Any], track).get(key)
    return None if value in (None, "") else value


def _music_unique_track_values(tracks: list[Any], key: str) -> list[Any]:
    values: set[Any] = set()
    for track in tracks:
        value = _music_track_value(track, key)
        if value is not None:
            values.add(value)
    return sorted(values, key=str)


def _render_music_variants(
    values: list[Any], formatter: Callable[[Any], str]
) -> str:
    if not values:
        return ""
    rendered = [formatter(item) for item in values]
    if len(rendered) <= 2:
        return ", ".join(rendered)
    return f"{len(rendered)} variants"


def _music_technical_values(
    tracks: list[Any], key: str, formatter: Callable[[Any], str]
) -> str:
    return _render_music_variants(
        _music_unique_track_values(tracks, key), formatter
    )


def _music_channel_text(item: Any) -> str:
    channel_count = int(item)
    return {1: "Mono", 2: "Stereo"}.get(channel_count, f"{item} channels")


def _music_audio_summary(
    meta: Meta, fields: Mapping[str, Any], tracks: list[Any]
) -> tuple[str, str]:
    format_name = _music_display(fields, "format", meta.format)
    formats = _music_technical_values(tracks, "format", str)
    codecs = _music_technical_values(tracks, "codec", str)
    bit_depth = _music_technical_values(
        tracks, "bit_depth", lambda item: f"{item}-bit"
    )
    sample_rate = _music_technical_values(
        tracks, "sample_rate", lambda item: f"{int(item) / 1000:g} kHz"
    )
    channels = _music_technical_values(tracks, "channels", _music_channel_text)
    bitrate = _music_technical_values(
        tracks, "bitrate", lambda item: f"{round(int(item) / 1000)} kbps"
    )
    if formats.casefold() == codecs.casefold():
        codecs = ""
    technical = " / ".join(
        part
        for part in (
            formats or format_name,
            codecs,
            bit_depth,
            sample_rate,
            channels,
            bitrate,
        )
        if part
    )
    return format_name, technical


def _or_missing(value: Any, missing_warning: str) -> Any:
    return value if value else missing_warning


def _music_track_disc_text(
    fields: Mapping[str, Any], tracks: list[Any], missing_warning: str
) -> str:
    disc_count = _music_field_value(fields, "disc_count", 1)
    track_count = _music_field_value(fields, "track_count", len(tracks))
    return f"{_or_missing(track_count, missing_warning)} / {disc_count or 1}"


def _music_audio_text(
    meta: Meta,
    fields: Mapping[str, Any],
    tracks: list[Any],
    missing_warning: str,
) -> str:
    format_name, technical = _music_audio_summary(meta, fields, tracks)
    return str(_or_missing(technical or format_name, missing_warning))


def _music_base_confirmation_lines(
    meta: Meta,
    fields: Mapping[str, Any],
    tracks: list[Any],
    missing_warning: str,
) -> list[tuple[str, str]]:
    artist = _music_display(
        fields,
        "artists",
        _music_field_value(fields, "artist", meta.artist),
    )
    values = (
        ("Artist", artist),
        ("Album", _music_display(fields, "album", meta.title)),
        ("Original Year", _music_display(fields, "year", meta.year)),
        ("Release Type", _music_display(fields, "release_type")),
        ("Media", _music_display(fields, "media", meta.source)),
    )
    lines = [
        (label, str(_or_missing(value, missing_warning)))
        for label, value in values
    ]
    lines.append(
        (
            "Tracks / Discs",
            _music_track_disc_text(fields, tracks, missing_warning),
        )
    )
    lines.append(
        ("Audio", _music_audio_text(meta, fields, tracks, missing_warning))
    )
    return lines


def _join_nonempty(parts: tuple[str, ...]) -> str:
    return " / ".join(part for part in parts if part)


def _optional_detail_line(label: str, value: str) -> tuple[str, str] | None:
    return (label, value) if value else None


def _music_release_line(fields: Mapping[str, Any]) -> tuple[str, str] | None:
    parts = tuple(
        _music_display(fields, name)
        for name in (
            "release_year",
            "retail_date",
            "release_label",
            "release_catalogue_number",
        )
    )
    return _optional_detail_line("This Release", _join_nonempty(parts))


def _music_edition_line(fields: Mapping[str, Any]) -> tuple[str, str] | None:
    edition = _music_display(fields, "edition")
    if not edition:
        return None
    edition_year = _music_display(fields, "edition_year")
    return "Edition", _join_nonempty((edition, edition_year))


def _music_release_detail_lines(
    fields: Mapping[str, Any],
) -> list[tuple[str, str]]:
    candidates = (
        _optional_detail_line("Genre", _music_display(fields, "genres")),
        _music_release_line(fields),
        _music_edition_line(fields),
    )
    return [line for line in candidates if line is not None]


def _has_public_artwork_url(meta: Meta) -> bool:
    artwork_url = str(meta.artwork_url or "").strip()
    return bool(re.match(r"^https?://[^/]+", artwork_url, flags=re.IGNORECASE))


def _has_local_artwork(meta: Meta) -> bool:
    return Path(str(meta.artwork_path or "")).is_file()


def _music_artwork_status(meta: Meta) -> str:
    if _has_public_artwork_url(meta):
        return "public URL supplied"
    if not _has_local_artwork(meta):
        return "not found (optional for Orpheus)"
    suffix = "; host upload skipped in debug" if meta.debug else ""
    return f"local/embedded artwork available{suffix}"


def _music_auxiliary_summary(auxiliary: Mapping[str, Any]) -> str:
    sidecars: list[str] = []
    for label, key in (
        ("log", "logs"),
        ("cue", "cues"),
        ("NFO", "nfos"),
        ("playlist", "playlists"),
        ("SFV", "sfvs"),
        ("artwork", "artwork"),
        ("scan", "scans"),
    ):
        values = auxiliary.get(key, [])
        count = len(values) if isinstance(values, list) else 0
        if count:
            sidecars.append(f"{count} {label}{'' if count == 1 else 's'}")
    return ", ".join(sidecars)


def _music_conflict_line(
    conflicts: Mapping[str, Any],
) -> tuple[str, str] | None:
    if not conflicts:
        return None
    names = ", ".join(
        str(name).replace("_", " ") for name in sorted(conflicts)[:5]
    )
    extra = f" (+{len(conflicts) - 5})" if len(conflicts) > 5 else ""
    return "Metadata conflicts", f"[yellow]{escape(names)}{extra}[/yellow]"


def _music_warning_line(warnings: list[Any]) -> tuple[str, str] | None:
    if not warnings:
        return None
    preview = "; ".join(str(item) for item in warnings[:3])
    extra = f" (+{len(warnings) - 3} more)" if len(warnings) > 3 else ""
    return "Music validation", f"[yellow]{escape(preview)}{extra}[/yellow]"


def _append_music_optional_line(
    lines: list[tuple[str, str]], line: tuple[str, str] | None
) -> None:
    if line is not None:
        lines.append(line)


def _music_confirmation_lines(
    meta: Meta, missing_warning: str
) -> list[tuple[str, str]]:
    """Build a concise, tracker-neutral MUSIC review for confirmation."""
    fields, tracks, auxiliary, warnings, conflicts = _music_release_parts(meta)
    lines = _music_base_confirmation_lines(
        meta, fields, tracks, missing_warning
    )
    lines.extend(_music_release_detail_lines(fields))
    lines.append(("Artwork", _music_artwork_status(meta)))
    auxiliary_summary = _music_auxiliary_summary(auxiliary)
    if auxiliary_summary:
        lines.append(("Auxiliary", auxiliary_summary))
    _append_music_optional_line(lines, _music_conflict_line(conflicts))
    _append_music_optional_line(lines, _music_warning_line(warnings))
    return lines


_SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "kib": 1024,
    "mb": 1024**2,
    "mib": 1024**2,
    "gb": 1024**3,
    "gib": 1024**3,
    "tb": 1024**4,
    "tib": 1024**4,
}
_DECIMAL_COMMA_LARGE_UNITS = {"tb", "tib", "pb", "pib"}


def _trailing_size_unit(value: str) -> str:
    match = re.search(r"([a-zA-Z]+)$", value)
    return match.group(1).lower() if match else ""


def _ambiguous_comma_separator(value: str, digits_after: str) -> str:
    if len(digits_after) != 3:
        return "."
    if _trailing_size_unit(value) in _DECIMAL_COMMA_LARGE_UNITS:
        return "."
    return ""


def _normalize_ambiguous_size_comma(value: str) -> str:
    if "," not in value:
        return value
    if "." in value:
        return value.replace(",", "")
    match = re.search(r",(\d+)\s*[a-zA-Z]*$", value)
    if match is None:
        return value
    separator = _ambiguous_comma_separator(value, match.group(1))
    return value.replace(",", separator)


def _size_value_with_unit(value: str) -> int:
    match = re.match(r"^([\d.]+)\s*([a-zA-Z]+)$", value)
    if match is None:
        return int(float(value))
    value_str, unit = match.groups()
    numeric = float(value_str)
    multiplier = _SIZE_UNITS.get(unit.lower(), 1)
    return int(numeric * multiplier)


_SIZE_UNHANDLED = object()


def _simple_size_value(size_str: Any) -> int | object | None:
    if size_str is None:
        return None
    if isinstance(size_str, (int, float)):
        return int(size_str)
    value = str(size_str).strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    return _SIZE_UNHANDLED


def parse_size_to_bytes(size_str: Any) -> int | None:
    simple = _simple_size_value(size_str)
    if simple is not _SIZE_UNHANDLED:
        return cast(int | None, simple)
    value = str(size_str).strip()
    try:
        return _size_value_with_unit(_normalize_ambiguous_size_comma(value))
    except Exception:
        return None


def _hsl_sector(hue: float) -> int:
    if 0 <= hue < 300:
        return int(hue // 60)
    return 5


def _hsl_sector_rgb(
    sector: int, c: float, x: float
) -> tuple[float, float, float]:
    values = (
        (c, x, 0.0),
        (x, c, 0.0),
        (0.0, c, x),
        (0.0, x, c),
        (x, 0.0, c),
        (c, 0.0, x),
    )
    return values[sector]


def hsl_to_rgb(h: float, s: float, lx: float) -> tuple[int, int, int]:
    c = (1.0 - abs(2.0 * lx - 1.0)) * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = lx - c / 2.0
    r, g, b = _hsl_sector_rgb(_hsl_sector(h), c, x)
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


def get_color_for_diff(p: float) -> str:
    # Interpolate Hue from 120 (green) to 0 (red) for p in [0.0, 0.5]
    # saturation = 0.9, lightness = 0.6
    x = max(0.0, min(1.0, 1.0 - p / 0.5))
    h = 120.0 * (1.0 - x)
    r, g, b = hsl_to_rgb(h, 0.9, 0.6)
    return f"{r:02x}{g:02x}{b:02x}"


class UploadHelper:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.default_config = cast(
            Mapping[str, Any], config.get("DEFAULT", {})
        )
        if not isinstance(self.default_config, dict):
            raise ValueError("'DEFAULT' config section must be a dict")
        self.tracker_class_map = cast(Mapping[str, Any], tracker_class_map)
        self._prompt_lock = asyncio.Lock()

    async def prompt_yes_no(
        self, question: str, *, default: bool = False
    ) -> bool:
        """Ask one interactive question at a time without blocking the event loop."""
        async with self._prompt_lock:
            return await prompt_in_thread(
                cli_ui.ask_yes_no, question, default=default
            )

    def _dupe_size_diff(self, entry: Mapping[str, Any], meta: Meta) -> str:
        if not self.default_config.get("show_dupe_size_diff", True):
            return ""
        upload_size = meta.source_size
        dupe_size = parse_size_to_bytes(entry.get("size"))
        if not upload_size or not dupe_size:
            return ""
        diff_bytes = dupe_size - upload_size
        diff_mb = round(diff_bytes / (1024 * 1024))
        diff_pct = round((diff_bytes / upload_size) * 100)
        color_hex = get_color_for_diff(abs(diff_pct) / 100.0)
        return f" - [#{color_hex}][{diff_mb:+d} MB / {diff_pct:+d}%][/]"

    def _format_dupe(self, entry: DupeEntry | str, meta: Meta) -> str:
        if not isinstance(entry, dict):
            return entry
        name = str(entry.get("name", ""))
        size_diff = self._dupe_size_diff(entry, meta)
        link = entry.get("link")
        if isinstance(link, str) and link:
            name = format_terminal_link(name, link, self.default_config)
        return f"{name}{size_diff}"

    @staticmethod
    def _dupe_link(entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""
        link = cast(dict[str, Any], entry).get("link")
        return str(link) if link else ""

    def _format_dupes_list(self, entries: list[Any], meta: Meta) -> str:
        seen_links: set[str] = set()
        formatted: list[str] = []
        for entry in entries:
            link = self._dupe_link(entry)
            if link:
                if link in seen_links:
                    continue
                seen_links.add(link)
            formatted.append(
                self._format_dupe(cast(DupeEntry | str, entry), meta)
            )
        return "\n".join(formatted)

    @staticmethod
    def _terminal_safe_repack_value(value: object) -> str:
        without_osc = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", str(value))
        return "".join(
            character for character in without_osc if character.isprintable()
        )

    @staticmethod
    def _trusted_repack_id(value: object) -> int:
        raw_id = str(value)
        if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 20:
            return 0
        return int(raw_id)

    def _format_repack_result(
        self, entry: DupeEntry | str, tracker_class: Any
    ) -> str:
        if not isinstance(entry, dict):
            return escape(self._terminal_safe_repack_value(entry))
        name = self._terminal_safe_repack_value(entry.get("name", ""))
        torrent_id = self._trusted_repack_id(entry.get("id", ""))
        torrent_url = str(getattr(tracker_class, "torrent_url", ""))
        if torrent_id > 0 and torrent_url:
            return format_terminal_link(
                name, f"{torrent_url}{torrent_id}", self.default_config
            )
        return escape(name)

    @staticmethod
    def _replacement_identity(replaced_release: object) -> tuple[str, str]:
        if not isinstance(replaced_release, (Mapping, Meta)):
            return "", ""
        mapping = cast(Mapping[str, object] | Meta, replaced_release)
        raw_id = mapping.get("id")
        raw_link = mapping.get("link")
        return (
            str(raw_id) if raw_id is not None else "",
            str(raw_link) if raw_link else "",
        )

    @staticmethod
    def _entry_id_matches(
        entry: Mapping[str, Any] | Meta, replaced_id: str
    ) -> bool:
        if not replaced_id or entry.get("id") is None:
            return False
        return str(entry.get("id")) == replaced_id

    @staticmethod
    def _entry_link_matches(
        entry: Mapping[str, Any] | Meta,
        replaced_id: str,
        replaced_link: str,
    ) -> bool:
        if replaced_id or not replaced_link:
            return False
        return str(entry.get("link") or "") == replaced_link

    @staticmethod
    def _entry_identity_matches(
        entry: Mapping[str, Any] | Meta,
        replaced_release: object,
        replaced_id: str,
        replaced_link: str,
    ) -> bool:
        return (
            not replaced_id and not replaced_link and entry is replaced_release
        )

    @classmethod
    def _is_replaced_dupe(
        cls,
        entry: DupeEntry | str,
        replaced_release: object,
        replaced_id: str,
        replaced_link: str,
    ) -> bool:
        if not isinstance(entry, (Mapping, Meta)):
            return False
        entry_map = cast(Mapping[str, Any] | Meta, entry)
        return any(
            (
                cls._entry_id_matches(entry_map, replaced_id),
                cls._entry_link_matches(entry_map, replaced_id, replaced_link),
                cls._entry_identity_matches(
                    entry_map, replaced_release, replaced_id, replaced_link
                ),
            )
        )

    def _without_replaced_release(
        self, dupes: list[DupeEntry | str], replaced_release: object
    ) -> list[DupeEntry | str]:
        replaced_id, replaced_link = self._replacement_identity(
            replaced_release
        )
        return [
            entry
            for entry in dupes
            if not self._is_replaced_dupe(
                entry, replaced_release, replaced_id, replaced_link
            )
        ]

    def _repack_policy(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        tracker_class: Any,
    ) -> tuple[bool | None, list[DupeEntry | str]]:
        if not getattr(tracker_class, "prefers_repack", False):
            return None, dupes
        preferred = meta.get(f"{tracker_name}_preferred_repack")
        if preferred:
            logger.info(
                f"[bold red]{tracker_name}: a matching REPACK is already available. The non-REPACK release will be skipped.[/bold red]"
            )
            logger.info(
                f"[bold cyan]{self._format_repack_result(cast(DupeEntry | str, preferred), tracker_class)}[/bold cyan]"
            )
            return True, dupes
        replaced = meta.get(f"{tracker_name}_repack_replaces")
        if not replaced:
            return None, dupes
        logger.info(
            f"[bold green]{tracker_name}: this REPACK supersedes an existing release and may be uploaded.[/bold green]"
        )
        logger.info(
            "[yellow]After the upload succeeds, report the old release manually so staff can remove it:[/yellow] "
            f"{self._format_repack_result(cast(DupeEntry | str, replaced), tracker_class)}"
        )
        remaining = self._without_replaced_release(dupes, replaced)
        return (False if not remaining else None), remaining

    @staticmethod
    def _tracker_display_name(tracker_rename: Any) -> str | None:
        if isinstance(tracker_rename, dict) and "name" in tracker_rename:
            return str(cast(dict[str, Any], tracker_rename).get("name", ""))
        return tracker_rename if isinstance(tracker_rename, str) else None

    async def _renamed_release_name(
        self, tracker_class: Any, meta: Meta
    ) -> str | None:
        try:
            tracker_rename = await tracker_class.get_name(meta)
        except Exception:
            return None
        return self._tracker_display_name(tracker_rename)

    @staticmethod
    def _season_pack_blocks_episode(meta: Meta) -> bool:
        return meta.dupe is False and bool(meta.season_pack_exists)

    @staticmethod
    def _matched_episode_entries(
        meta: Meta, tracker_name: str
    ) -> list[DupeEntry]:
        value = meta.get(f"{tracker_name}_matched_episode_ids", [])
        return cast(list[DupeEntry], value) if isinstance(value, list) else []

    @staticmethod
    def _tagged_episode_match(
        matched_episodes: list[DupeEntry], user_tag: str
    ) -> DupeEntry | None:
        if not user_tag:
            return None
        for episode in matched_episodes:
            name = str(episode.get("name", "")).lower()
            if name.endswith(user_tag) or f"-{user_tag}" in name:
                return episode
        return None

    @classmethod
    def _selected_episode_match(
        cls, matched_episodes: list[DupeEntry], meta: Meta
    ) -> tuple[DupeEntry, bool]:
        user_tag = meta.tag.lstrip("-").lower() if meta.tag else ""
        matched = cls._tagged_episode_match(matched_episodes, user_tag)
        if matched is not None:
            return matched, True
        return matched_episodes[0], False

    @staticmethod
    def _trumpable_eligible(
        meta: Meta, matched_episodes: list[DupeEntry]
    ) -> bool:
        if meta.trumpable_id:
            return True
        return bool(meta.season_pack_contains_episode and matched_episodes)

    @staticmethod
    def _trumpable_dupes(dupes: list[DupeEntry | str]) -> list[DupeEntry]:
        return [
            cast(DupeEntry, entry)
            for entry in dupes
            if isinstance(entry, dict) and entry.get("trumpable")
        ]

    def _episode_trumpable_text(
        self, matched_episodes: list[DupeEntry], meta: Meta
    ) -> str:
        selected, tag_matched = self._selected_episode_match(
            matched_episodes, meta
        )
        logger.info(
            "[bold red]Trumpable found based on episode matching![/bold red]"
        )
        if meta.tag and not tag_matched:
            logger.info(
                f"[yellow]Note: No release found with matching tag '{meta.tag}'. Selected release may be from a different group.[/yellow]"
            )
        return self._format_dupe(selected, meta)

    def _trumpable_text(
        self, dupes: list[DupeEntry | str], meta: Meta, tracker_name: str
    ) -> str | None:
        matched_episodes = self._matched_episode_entries(meta, tracker_name)
        if not self._trumpable_eligible(meta, matched_episodes):
            return None
        trumpable = self._trumpable_dupes(dupes)
        if trumpable:
            logger.info("[bold red]Trumpable found![/bold red]")
            return self._format_dupes_list(trumpable, meta)
        if not matched_episodes:
            return None
        return self._episode_trumpable_text(matched_episodes, meta)

    async def _dupe_prompt(
        self, question: str, *, default: bool = False
    ) -> bool:
        try:
            return await self.prompt_yes_no(question, default=default)
        except EOFError:
            logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
            await cleanup_manager.cleanup()
            cleanup_manager.reset_terminal()
            raise OperationAbortedError(
                "Duplicate-check confirmation was cancelled by the user."
            ) from None

    @staticmethod
    def _set_trump_metadata(meta: Meta, tracker_name: str) -> None:
        meta.we_asked = True
        meta.were_trumping = True
        if not meta.get(f"{tracker_name}_trumpable_id"):
            meta[f"{tracker_name}_trumpable_id"] = meta.get(
                f"{tracker_name}_matched_id", None
            )
        meta.trump_reason = (
            "exact_match"
            if meta.filename_match and meta.file_count_match
            else "trumpable_release"
        )
        logger.debug(
            f"[bold green]Trump reason: {meta.trump_reason} on {tracker_name}[/bold green]"
        )

    @staticmethod
    def _should_filter_declined_pack_matches(
        meta: Meta, matched: list[DupeEntry]
    ) -> bool:
        return bool(
            meta.tv_pack and meta.season_pack_contains_episode and matched
        )

    @staticmethod
    def _matched_episode_ids(matched: list[DupeEntry]) -> set[Any]:
        return {entry.get("id") for entry in matched if entry.get("id")}

    @staticmethod
    def _remove_dupes_with_ids(
        dupes: list[DupeEntry | str], matched_ids: set[Any]
    ) -> list[DupeEntry | str]:
        return [
            entry
            for entry in dupes
            if not (isinstance(entry, dict) and entry.get("id") in matched_ids)
        ]

    def _filter_declined_pack_matches(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> list[DupeEntry | str]:
        matched = self._matched_episode_entries(meta, tracker_name)
        if not self._should_filter_declined_pack_matches(meta, matched):
            return dupes
        matched_ids = self._matched_episode_ids(matched)
        meta[f"{tracker_name}_matched_episode_ids"] = []
        return self._remove_dupes_with_ids(dupes, matched_ids)

    async def _handle_trumpable_prompt(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        trumpable_text: str | None,
    ) -> list[DupeEntry | str]:
        if not trumpable_text:
            return dupes
        logger.info(f"[bold cyan]{trumpable_text}[/bold cyan]")
        logger.info(
            "[yellow]Please check the trumpable entries above to see if you want to upload[/yellow]"
        )
        logger.info(
            "[yellow]You will have the option to report the trumpable torrent if you upload.[/yellow]"
        )
        if meta.dupe is not False:
            return dupes
        upload = await self._dupe_prompt(
            f"Are you trumping this release on {tracker_name}?", default=False
        )
        if upload:
            self._set_trump_metadata(meta, tracker_name)
            return dupes
        return self._filter_declined_pack_matches(dupes, meta, tracker_name)

    async def _exact_match_decision(
        self, meta: Meta, tracker_name: str
    ) -> bool:
        logger.info(
            f"[bold red]Exact match found! - {meta.filename_match}[/bold red]"
        )
        if tracker_name in {"AITHER", "LST"}:
            logger.info(
                f"[yellow]{tracker_name} supports automatic trumping of exact matches, if the file is allowed to be trumped.[/yellow]"
            )
            upload = await self._dupe_prompt(
                f"Are you trumping this exact match on {tracker_name}?",
                default=False,
            )
            if upload:
                self._set_trump_metadata(meta, tracker_name)
            return upload
        upload = await self._dupe_prompt(
            f"Upload to {tracker_name} anyway?", default=False
        )
        meta.we_asked = True
        return upload

    def _show_general_dupes(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> None:
        if meta.season_pack_exists:
            season_pack_name = meta.season_pack_name
            season_pack_link = meta.season_pack_link
            season_pack_text = (
                format_terminal_link(
                    season_pack_name, season_pack_link, self.default_config
                )
                if season_pack_link
                else season_pack_name
            )
            logger.info(
                f"[yellow]Note: A season pack exists on {tracker_name}[/yellow]"
            )
            logger.info(
                "[yellow]Ensure your upload is not part of that season pack, or is otherwise allowed.[/yellow]"
            )
            logger.info("")
            logger.info(f"[bold cyan]{season_pack_text}[/bold cyan]")
            return
        logger.info(
            f"[bold blue]{tracker_name}[/bold blue]: Check if these are actually dupes:"
        )
        logger.info("")
        logger.info(
            f"[bold cyan]{self._format_dupes_list(dupes, meta)}[/bold cyan]",
            extra={"highlighter": None},
        )

    async def _general_dupe_decision(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> bool:
        if not dupes:
            return True
        self._show_general_dupes(dupes, meta, tracker_name)
        if meta.dupe is not False:
            return True
        if meta.is_disc == "BDMV":
            await self.ask_bdinfo_comparison(meta, dupes, tracker_name)
        upload = await self._dupe_prompt(
            f"Upload to {tracker_name} anyway?", default=False
        )
        meta.we_asked = True
        return upload

    @staticmethod
    def _interactive_dupe_mode(meta: Meta) -> bool:
        return (
            not meta.unattended or meta.unattended_confirm
        ) and not meta.ask_dupe

    async def _interactive_dupe_decision(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> tuple[bool, list[DupeEntry | str]]:
        trumpable_text = self._trumpable_text(dupes, meta, tracker_name)
        dupes = await self._handle_trumpable_prompt(
            dupes, meta, tracker_name, trumpable_text
        )
        if meta.were_trumping:
            return True, dupes
        if meta.filename_match and meta.file_count_match:
            return await self._exact_match_decision(meta, tracker_name), dupes
        return await self._general_dupe_decision(
            dupes, meta, tracker_name
        ), dupes

    @staticmethod
    def _dupe_similarity(entry: DupeEntry | str, display_name: str) -> float:
        if not isinstance(entry, dict):
            return 0.0
        entry_name = str(entry.get("name", "")).lower()
        return SequenceMatcher(
            None, entry_name, display_name.lower().strip()
        ).ratio()

    @staticmethod
    def _save_cross_seed(meta: Meta, tracker_name: str, link: Any) -> None:
        if not link:
            return
        meta[f"{tracker_name}_cross_seed"] = link
        logger.debug(
            f"[bold red]Cross-seed link saved for {tracker_name}: {Redaction.redact_private_info(link)}.[/bold red]"
        )

    @staticmethod
    def _beyondhd_display_name(display_name: str, meta: Meta) -> str:
        result = display_name
        if meta.edition and meta.edition in result:
            result = result.replace(f"{meta.edition} ", "")
        if meta.region and meta.region in result:
            result = result.replace(f"{meta.region} ", "")
        return result

    def _similar_dupe_exists(
        self, dupes: list[DupeEntry | str], display_name: str
    ) -> bool:
        return any(
            self._dupe_similarity(entry, display_name) > 0.9
            for entry in dupes
            if isinstance(entry, dict)
        )

    def _apply_beyondhd_cross_seed(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        display_name: str,
        link: Any,
    ) -> None:
        logger.debug("[yellow]BEYONDHD cross seeding check[/yellow]")
        name = self._beyondhd_display_name(display_name, meta)
        if meta.size_match and link and self._similar_dupe_exists(dupes, name):
            self._save_cross_seed(meta, tracker_name, link)

    def _apply_exact_cross_seed(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        link: Any,
    ) -> None:
        logger.debug(
            f"[yellow]{tracker_name} filename and file count cross seeding check[/yellow]"
        )
        if link and any(isinstance(entry, dict) for entry in dupes):
            self._save_cross_seed(meta, tracker_name, link)

    def _apply_size_cross_seed(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        display_name: str,
        link: Any,
    ) -> None:
        if not meta.size_match:
            return
        logger.debug(
            f"[yellow]{tracker_name} size cross seeding check[/yellow]"
        )
        if link and self._similar_dupe_exists(dupes, display_name):
            self._save_cross_seed(meta, tracker_name, link)

    def _apply_cross_seed(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
        display_name: str,
    ) -> None:
        link = meta.get(f"{tracker_name}_matched_download")
        if tracker_name == "BEYONDHD":
            self._apply_beyondhd_cross_seed(
                dupes, meta, tracker_name, display_name, link
            )
            return
        if meta.filename_match and meta.file_count_match:
            self._apply_exact_cross_seed(dupes, meta, tracker_name, link)
            return
        self._apply_size_cross_seed(
            dupes, meta, tracker_name, display_name, link
        )

    @staticmethod
    def _mark_same_name_dupe(dupes: list[DupeEntry | str], meta: Meta) -> None:
        for entry in dupes:
            name = str(entry.get("name")) if isinstance(entry, dict) else entry
            if name == meta.name:
                meta.name = f"{meta.name} DUPE?"
                return

    async def _dupe_preflight(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> tuple[bool | None, list[DupeEntry | str], str | None]:
        if not dupes:
            logger.debug(
                f"[green]No dupes found at[/green] [yellow]{tracker_name}[/yellow]"
            )
            return False, [], None
        tracker_factory = cast(
            Callable[..., Any], self.tracker_class_map[tracker_name]
        )
        tracker_class = tracker_factory(config=self.config)
        repack_decision, dupes_list = self._repack_policy(
            list(dupes), meta, tracker_name, tracker_class
        )
        if repack_decision is not None:
            return repack_decision, dupes_list, None
        display_name = await self._renamed_release_name(tracker_class, meta)
        if self._season_pack_blocks_episode(meta):
            pack_name = meta.season_pack_name or "matching season pack"
            logger.info(
                f"[bold red]{tracker_name}: {pack_name} already contains this episode. Skipping individual episode upload.[/bold red]"
            )
            return True, dupes_list, display_name
        return None, dupes_list, display_name

    async def _dupe_upload_decision(
        self,
        dupes: list[DupeEntry | str],
        meta: Meta,
        tracker_name: str,
    ) -> tuple[bool, list[DupeEntry | str]]:
        if self._interactive_dupe_mode(meta):
            return await self._interactive_dupe_decision(
                dupes, meta, tracker_name
            )
        return meta.dupe is not False, dupes

    async def dupe_check(
        self, dupes: list[DupeEntry | str], meta: Meta, tracker_name: str
    ) -> tuple[bool, Meta]:
        meta.were_trumping = False
        preflight, dupes_list, display_name = await self._dupe_preflight(
            dupes, meta, tracker_name
        )
        if preflight is not None:
            return preflight, meta
        upload, dupes_list = await self._dupe_upload_decision(
            dupes_list, meta, tracker_name
        )
        resolved_name = display_name if display_name is not None else meta.name
        self._apply_cross_seed(dupes_list, meta, tracker_name, resolved_name)
        if not upload:
            return True, meta
        self._mark_same_name_dupe(dupes_list, meta)
        return False, meta

    @staticmethod
    def _bdinfo_comparison_possible(dupes: list[DupeEntry | str]) -> bool:
        return any(
            isinstance(entry, dict) and has_bdinfo_content(entry)
            for entry in dupes
        )

    @staticmethod
    def _bdinfo_comparison_messages(
        meta: Meta,
        dupes: list[DupeEntry | str],
        tracker_name: str,
    ) -> tuple[list[str], list[str]]:
        warnings: list[str] = []
        results: list[str] = []
        for entry in dupes:
            if not isinstance(entry, dict):
                continue
            warning_message, results_message = compare_bdinfo(
                meta, entry, tracker_name
            )
            if warning_message:
                warnings.append(warning_message)
            if results_message:
                results.append(results_message)
        return warnings, results

    @staticmethod
    def _log_bdinfo_comparison_messages(
        warnings: list[str], results: list[str]
    ) -> None:
        if warnings:
            logger.info("")
            logger.info("\n\n".join(warnings))
        if results:
            logger.info("")
            logger.info("\n".join(results))
            logger.info("")

    async def ask_bdinfo_comparison(
        self, meta: Meta, dupes: list[DupeEntry | str], tracker_name: str
    ) -> None:
        """Optionally compare local BDInfo against potential duplicates."""
        if not self._bdinfo_comparison_possible(dupes):
            return
        question = "[bold magenta]Found BDInfo content in potential duplicates.[/bold magenta] Perform a comparison?"
        if not await self.prompt_yes_no(question, default=True):
            return
        warnings, results = self._bdinfo_comparison_messages(
            meta, dupes, tracker_name
        )
        self._log_bdinfo_comparison_messages(warnings, results)

    @staticmethod
    def _debug_confirmation_lines(meta: Meta) -> list[str | tuple[str, str]]:
        if not meta.debug:
            return []
        return [
            "[bold red]DEBUG: True - Will not actually upload![/bold red]",
            f"Prep material saved to {Path(meta.base_dir) / 'tmp' / meta.uuid}",
        ]

    @staticmethod
    def _book_bool(value: bool) -> str:
        return "[green]True[/green]" if value else "[purple]False[/purple]"

    @staticmethod
    def _cover_status(meta: Meta, missing_warning: str) -> str:
        return (
            "Found"
            if meta.artwork_url or meta.artwork_path
            else missing_warning
        )

    @classmethod
    def _book_boolean_lines(cls, meta: Meta) -> list[tuple[str, str]]:
        return [
            ("Comic", cls._book_bool(meta.comic)),
            ("Manga", cls._book_bool(meta.manga)),
            ("Magazine", cls._book_bool(meta.magazine)),
            ("Newspaper", cls._book_bool(meta.newspaper)),
        ]

    @staticmethod
    def _book_value(value: Any, fallback: str) -> str:
        return str(value) if value else fallback

    @classmethod
    def _book_identity_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        return [
            ("Author", cls._book_value(meta.author, missing_warning)),
            ("Publisher", cls._book_value(meta.publisher, "")),
            ("Language", cls._book_value(meta.book_language, missing_warning)),
            ("ISBN", cls._book_value(meta.isbn, "")),
            ("ASIN", cls._book_value(meta.asin, "")),
        ]

    @staticmethod
    def _book_translator_line(meta: Meta) -> tuple[str, str] | None:
        return (
            ("Translator", meta.book_translator)
            if meta.book_translator
            else None
        )

    @staticmethod
    def _book_audiobook_lines(
        meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        if not meta.audiobook:
            return []
        return [
            ("Narrator", meta.narrator or missing_warning),
            ("Duration", meta.audiobook_duration_formatted or missing_warning),
        ]

    @classmethod
    def _book_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        lines = cls._book_identity_lines(meta, missing_warning)
        translator = cls._book_translator_line(meta)
        if translator is not None:
            lines.insert(1, translator)
        lines.extend(cls._book_boolean_lines(meta))
        lines.extend(cls._book_audiobook_lines(meta, missing_warning))
        lines.append(("Cover", cls._cover_status(meta, missing_warning)))
        return lines

    @staticmethod
    def _game_notes_source(meta: Meta) -> str:
        return str(
            meta.software_notes
            or meta.description_link
            or meta.description_file
            or ""
        )

    @staticmethod
    def _redacted_game_notes(meta: Meta, notes: str) -> str:
        if not notes or meta.software_notes:
            return notes
        if notes.startswith("http"):
            return notes[:16]
        return f"./{Path(notes).name}"

    @staticmethod
    def _missing_pc_notes(meta: Meta, notes: str) -> str:
        if meta.platform == "PC" and not notes:
            return "[yellow][italic]Installation instructions missing. Use -df or -dp to add them.[/italic][/yellow]"
        return notes

    @classmethod
    def _game_notes(cls, meta: Meta) -> str:
        notes = cls._redacted_game_notes(meta, cls._game_notes_source(meta))
        return cls._missing_pc_notes(meta, notes)

    @staticmethod
    def _game_language_supports_text(supports: Any) -> str:
        if isinstance(supports, list):
            return "/".join(str(item) for item in cast(list[Any], supports))
        return str(supports)

    @classmethod
    def _game_language_mapping_text(cls, value: dict[str, Any]) -> str:
        return ", ".join(
            f"{language} ({cls._game_language_supports_text(supports)})"
            for language, supports in value.items()
        )

    @staticmethod
    def _game_language_list_text(value: list[Any]) -> str:
        return ", ".join(str(item) for item in value)

    @classmethod
    def _game_languages_text(cls, value: Any) -> str:
        if isinstance(value, dict):
            return cls._game_language_mapping_text(cast(dict[str, Any], value))
        if isinstance(value, list):
            return cls._game_language_list_text(cast(list[Any], value))
        return str(value)

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            parsed = int(value)
        except TypeError, ValueError:
            return 0
        return parsed if parsed > 0 else 0

    @staticmethod
    def _game_subcategory_text(meta: Meta) -> str:
        label = {
            "full_game": "Full Game",
            "full_game_dlc": "Full Game + DLC",
            "dlc": "DLC",
            "update": "Update",
        }.get(meta.game_subcategory, "Unknown")
        return f"[italic]{meta.game_subcategory}[/italic] ({label})"

    @classmethod
    def _game_core_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        return [
            ("Subcategory", cls._game_subcategory_text(meta)),
            ("Version", meta.game_version or missing_warning),
            ("Developer", meta.developer or missing_warning),
            ("Publisher", meta.publisher or missing_warning),
            ("Platform", meta.platform or missing_warning),
            ("Cover", cls._cover_status(meta, missing_warning)),
        ]

    @classmethod
    def _game_optional_lines(cls, meta: Meta) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        notes = cls._game_notes(meta)
        if notes:
            lines.append(("Notes", notes))
        igdb_id = cls._positive_int(meta.igdb_id)
        if igdb_id:
            lines.append(("IGDB", str(igdb_id)))
        if meta.steam_url:
            lines.append(("Steam", str(meta.steam_url)))
        if meta.languages:
            lines.append(
                ("Languages", cls._game_languages_text(meta.languages))
            )
        return lines

    @classmethod
    def _game_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        lines = cls._game_core_lines(meta, missing_warning)
        optional = cls._game_optional_lines(meta)
        if optional and optional[0][0] == "Notes":
            lines.insert(2, optional.pop(0))
        lines.extend(optional)
        return lines

    @staticmethod
    def _tv_episode_confirmation_lines(meta: Meta) -> list[tuple[str, str]]:
        if meta.category != "TV" or meta.tv_pack:
            return []
        lines: list[tuple[str, str]] = []
        if meta.auto_episode_title:
            lines.append(("Episode Title", meta.auto_episode_title))
        if meta.overview_meta:
            lines.append(
                ("Episode overview:", meta.overview_meta[:60] + "....")
            )
        return lines

    @staticmethod
    def _demographic_confirmation_line(meta: Meta) -> tuple[str, str] | None:
        return (
            ("Demographic", meta.demographic)
            if meta.demographic != ""
            else None
        )

    @classmethod
    def _overview_confirmation_lines(cls, meta: Meta) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            ("Overview", f"{meta.overview[:60]}....")
        ]
        lines.extend(cls._tv_episode_confirmation_lines(meta))
        lines.append(("Genre", ", ".join(meta.genres)))
        demographic = cls._demographic_confirmation_line(meta)
        if demographic is not None:
            lines.append(demographic)
        return lines

    @staticmethod
    def _tmdb_display_url(meta: Meta) -> str | None:
        if not meta.tmdb_id:
            return None
        return f"https://www.themoviedb.org/{(meta.category or '').lower()}/{meta.tmdb_id}"

    @staticmethod
    def _imdb_display_url(meta: Meta) -> str | None:
        if not meta.imdb_id:
            return None
        return f"https://www.imdb.com/title/tt{meta.imdb}"

    @staticmethod
    def _tvdb_display_url(meta: Meta) -> str | None:
        return (
            f"https://www.thetvdb.com/?id={meta.tvdb_id}&tab=series"
            if meta.tvdb_id
            else None
        )

    @staticmethod
    def _tvmaze_display_url(meta: Meta) -> str | None:
        return (
            f"https://www.tvmaze.com/shows/{meta.tvmaze_id}"
            if meta.tvmaze_id
            else None
        )

    @staticmethod
    def _mal_display_url(meta: Meta) -> str | None:
        return (
            f"https://myanimelist.net/anime/{meta.mal_id}"
            if meta.mal_id
            else None
        )

    @staticmethod
    def _present_labeled_values(
        values: tuple[tuple[str, str | None], ...],
    ) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = []
        for label, value in values:
            if value:
                lines.append((label, value))
        return lines

    @classmethod
    def _id_confirmation_lines(cls, meta: Meta) -> list[tuple[str, str]]:
        return cls._present_labeled_values(
            (
                ("TMDB", cls._tmdb_display_url(meta)),
                ("IMDB", cls._imdb_display_url(meta)),
                ("TVDB", cls._tvdb_display_url(meta)),
                ("TVMaze", cls._tvmaze_display_url(meta)),
                ("MAL", cls._mal_display_url(meta)),
            )
        )

    @staticmethod
    def _normalized_group_tag(meta: Meta) -> str:
        tag = str(meta.tag or "")
        return tag[1:] if tag.startswith("-") else tag

    @staticmethod
    def _disc_release_lines(
        meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        if not meta.is_disc:
            return []
        return [
            ("Region", meta.region or missing_warning),
            ("Distributor", meta.distributor or missing_warning),
        ]

    @staticmethod
    def _freeleech_line(meta: Meta) -> tuple[str, str] | None:
        return (
            ("Freeleech", str(meta.freeleech)) if meta.freeleech != 0 else None
        )

    @classmethod
    def _release_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[tuple[str, str]]:
        lines: list[tuple[str, str]] = [
            ("Edition", meta.edition),
            ("Resolution", meta.resolution),
            ("Source", str(meta.source)),
            ("Type", meta.type or ""),
        ]
        if meta.category != "BOOK":
            lines.append(("Group Tag", cls._normalized_group_tag(meta)))
        lines.extend(cls._disc_release_lines(meta, missing_warning))
        freeleech = cls._freeleech_line(meta)
        if freeleech is not None:
            lines.append(freeleech)
        return lines

    @classmethod
    def _standard_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[str | tuple[str, str]]:
        lines: list[str | tuple[str, str]] = []
        lines.extend(cls._overview_confirmation_lines(meta))
        lines.extend(cls._id_confirmation_lines(meta))
        lines.extend(cls._release_confirmation_lines(meta, missing_warning))
        return lines

    @staticmethod
    def _base_confirmation_lines(meta: Meta) -> list[str | tuple[str, str]]:
        title = f"{meta.title} ({meta.year})" if meta.year else meta.title
        return ["", ("Title", title), ("Category", meta.category)]

    @classmethod
    def _category_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[str | tuple[str, str]]:
        if meta.category == "BOOK":
            return list(cls._book_confirmation_lines(meta, missing_warning))
        if meta.category == "GAME":
            return list(cls._game_confirmation_lines(meta, missing_warning))
        return []

    @classmethod
    def _media_confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> tuple[str | tuple[str, str], ...]:
        if meta.category == "MUSIC":
            return tuple(_music_confirmation_lines(meta, missing_warning))
        return tuple(cls._standard_confirmation_lines(meta, missing_warning))

    @staticmethod
    def _personal_release_line(meta: Meta) -> str | None:
        return (
            "[bold green]Personal Release![/bold green]"
            if meta.personalrelease
            else None
        )

    @classmethod
    def _confirmation_lines(
        cls, meta: Meta, missing_warning: str
    ) -> list[str | tuple[str, str]]:
        lines = cls._debug_confirmation_lines(meta)
        lines.extend(cls._base_confirmation_lines(meta))
        lines.extend(cls._category_confirmation_lines(meta, missing_warning))
        lines.extend(cls._media_confirmation_lines(meta, missing_warning))
        lines.append("")
        personal = cls._personal_release_line(meta)
        if personal is not None:
            lines.append(personal)
        return lines

    @staticmethod
    def _confirmation_max_label(lines: list[str | tuple[str, str]]) -> int:
        labels = [item[0] for item in lines if isinstance(item, tuple)]
        return max((len(label) for label in labels), default=0)

    @staticmethod
    def _format_confirmation_item(
        item: str | tuple[str, str], max_label_len: int
    ) -> str:
        if not isinstance(item, tuple):
            return item
        label, value = item
        padding = f"[white]{'.' * (max_label_len - len(label))}[/white]"
        return f"[bold cyan]{label}[/bold cyan]{padding} {value}"

    @classmethod
    def _formatted_confirmation_lines(
        cls, lines: list[str | tuple[str, str]]
    ) -> list[str]:
        max_label_len = cls._confirmation_max_label(lines)
        return [
            cls._format_confirmation_item(item, max_label_len)
            for item in lines
        ]

    @staticmethod
    def _log_confirmation_lines(lines: list[str | tuple[str, str]]) -> None:
        logger.info(
            "\n".join(UploadHelper._formatted_confirmation_lines(lines)),
            extra={"highlighter": None},
        )

    def _unattended_confirmation(self, meta: Meta) -> bool:
        if not meta.unattended or meta.unattended_confirm:
            return False
        if meta.debug:
            logger.info(
                "[bold yellow]Unattended mode is enabled, skipping confirmation.[/bold yellow]"
            )
        return True

    async def _confirm_keep_folder(self, meta: Meta) -> None:
        if meta.is_disc:
            meta.keep_folder = False
        if not meta.keep_folder or not meta.isdir:
            return
        confirmed = await self.prompt_yes_no(
            "You specified --keep-folder. Uploading in folders might not be allowed. Proceed?",
            default=False,
        )
        if confirmed:
            return
        logger.info("[bold red]Aborting...[/bold red]")
        raise OperationAbortedError(
            "Upload confirmation was cancelled by the user."
        ) from None

    @staticmethod
    def _tracker_display_name_from_result(result: Any) -> str | None:
        if isinstance(result, dict):
            mapping = cast(dict[str, Any], result)
            if len(mapping) == 1:
                return str(next(iter(mapping.values())))
            if "name" in mapping:
                return str(mapping.get("name", ""))
            return None
        return result if isinstance(result, str) else None

    async def _tracker_release_name(
        self, tracker_name: str, meta: Meta
    ) -> str | None:
        if tracker_name in {"MANUAL", "USENET"}:
            return None
        tracker_factory = self.tracker_class_map.get(tracker_name)
        if not tracker_factory:
            return None
        try:
            tracker_class = cast(Callable[..., Any], tracker_factory)(
                config=self.config
            )
            result = await tracker_class.get_name(meta)
            return self._tracker_display_name_from_result(result)
        except Exception as error:
            logger.error(f"Error: {error}")
            return None

    async def _tracker_release_names(self, meta: Meta) -> dict[str, str]:
        names: dict[str, str] = {}
        for tracker_name in meta.trackers:
            display_name = await self._tracker_release_name(tracker_name, meta)
            if display_name:
                names[tracker_name] = display_name
        return names

    @staticmethod
    def _log_tracker_release_names(
        meta: Meta, names: Mapping[str, str]
    ) -> None:
        logger.info(
            f"[bold]Base Name:[/bold] {meta.name}\n",
            extra={"highlighter": None},
        )
        if not names:
            return
        max_tracker_len = max(len(name) for name in names)
        for tracker_name, display_name in sorted(names.items()):
            prefix = f"{tracker_name}:".ljust(max_tracker_len + 1)
            logger.info(
                f"{prefix} {display_name}", extra={"highlighter": None}
            )
        logger.info("")

    @staticmethod
    def _ids_unchanged(meta: Meta) -> bool:
        return all(
            (
                meta.original_imdb == meta.imdb_id,
                meta.original_tmdb == meta.tmdb_id,
                meta.original_mal == meta.mal_id,
                meta.original_tvmaze == meta.tvmaze_id,
                meta.original_tvdb == meta.tvdb_id,
                meta.original_category == meta.category,
            )
        )

    @staticmethod
    def _audit_imdb_url(imdb_id: Any) -> str | None:
        return (
            f"https://www.imdb.com/title/tt{str(imdb_id).zfill(7)}"
            if imdb_id and str(imdb_id).isdigit()
            else None
        )

    @staticmethod
    def _audit_tmdb_url(tmdb_id: Any, category: Any) -> str | None:
        if not tmdb_id or not category:
            return None
        return f"https://www.themoviedb.org/{str(category).lower()}/{tmdb_id}"

    @staticmethod
    def _audit_tvdb_url(tvdb_id: Any) -> str | None:
        return (
            f"https://www.thetvdb.com/?id={tvdb_id}&tab=series"
            if tvdb_id
            else None
        )

    @staticmethod
    def _audit_tvmaze_url(tvmaze_id: Any) -> str | None:
        return (
            f"https://www.tvmaze.com/shows/{tvmaze_id}" if tvmaze_id else None
        )

    @staticmethod
    def _audit_mal_url(mal_id: Any) -> str | None:
        return f"https://myanimelist.net/anime/{mal_id}" if mal_id else None

    @staticmethod
    def _audit_value(value: Any) -> Any:
        return value if value is not None else "N/A"

    @classmethod
    def _audit_identifier_set(
        cls,
        imdb_id: Any,
        tmdb_id: Any,
        tvdb_id: Any,
        tvmaze_id: Any,
        mal_id: Any,
        category: Any,
    ) -> dict[str, Any]:
        return {
            "imdb_id": cls._audit_value(imdb_id),
            "imdb_url": cls._audit_imdb_url(imdb_id),
            "tmdb_id": cls._audit_value(tmdb_id),
            "tmdb_url": cls._audit_tmdb_url(tmdb_id, category),
            "tvdb_id": cls._audit_value(tvdb_id),
            "tvdb_url": cls._audit_tvdb_url(tvdb_id),
            "tvmaze_id": cls._audit_value(tvmaze_id),
            "tvmaze_url": cls._audit_tvmaze_url(tvmaze_id),
            "mal_id": cls._audit_value(mal_id),
            "mal_url": cls._audit_mal_url(mal_id),
            "category": cls._audit_value(category),
        }

    @classmethod
    def _db_check_entry(cls, meta: Meta) -> dict[str, Any]:
        return {
            "path": meta.path,
            "original": cls._audit_identifier_set(
                meta.original_imdb,
                meta.original_tmdb,
                meta.original_tvdb,
                meta.original_tvmaze,
                meta.original_mal,
                meta.original_category,
            ),
            "changed": cls._audit_identifier_set(
                meta.imdb_id,
                meta.tmdb_id,
                meta.tvdb_id,
                meta.tvmaze_id,
                meta.mal_id,
                meta.category,
            ),
            "tracker": meta.matched_tracker
            if meta.matched_tracker is not None
            else "N/A",
        }

    @staticmethod
    async def _existing_db_checks(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            async with aiofiles.open(path, encoding="utf-8") as handle:
                contents = await handle.read()
            if not contents:
                return []
            parsed = json.loads(contents)
            if isinstance(parsed, list):
                return cast(list[dict[str, Any]], parsed)
        except Exception:
            return []
        return []

    @classmethod
    async def _append_db_check(cls, meta: Meta) -> None:
        data_dir = Path(meta.base_dir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / "db_check.json"
        entries = await cls._existing_db_checks(path)
        entries.append(cls._db_check_entry(meta))
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(
                json.dumps(entries, indent=2, ensure_ascii=False)
            )

    async def get_confirmation(self, meta: Meta) -> bool:
        missing_warning = "[bold red]⚠️ Missing[/bold red]"
        self._log_confirmation_lines(
            self._confirmation_lines(meta, missing_warning)
        )
        if self._unattended_confirmation(meta):
            return True
        if bool(self.default_config.get("sfx_on_prompt", True)):
            logger.info("\a")
        await self._confirm_keep_folder(meta)
        release_names = await self._tracker_release_names(meta)
        self._log_tracker_release_names(meta, release_names)
        confirmed = await self.prompt_yes_no("Is this correct?")
        logger.info("")
        if not confirmed:
            return False
        if self._ids_unchanged(meta):
            logger.info(
                "[bold yellow]Database ID's are correct![/bold yellow]"
            )
            return True
        await self._append_db_check(meta)
        return True
