# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import hashlib
import io
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import librosa
import librosa.display
import matplotlib
from matplotlib import font_manager, ft2font

matplotlib.use("Agg")

import cli_ui
import matplotlib.pyplot as plt
import numpy as np

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import spectrograms_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)

DURATION_LIMIT = 600
SAMPLE_RATE = 48000
WIDTH_INCH = 16
HEIGHT_INCH = 9
DPI_VALUE = 240
CACHE_VERSION = 3
_PLOT_FONT_CACHE: tuple[str, bool, str | None] | None = None
AUDIOBOOK_EXTENSIONS = {
    ".aac",
    ".aax",
    ".flac",
    ".m4a",
    ".m4b",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
SPECTROGRAM_N_FFT = 2048
MAX_TIME_BINS = 1024
_PREFERRED_PLOT_FONTS: tuple[str, ...] = (
    "Noto Sans CJK SC",
    "Noto Sans CJK TC",
    "Noto Sans SC",
    "Noto Sans TC",
    "PingFang SC",
    "PingFang TC",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Noto Sans",
    "DejaVu Sans",
)
_CJK_SYSTEM_FONT_HINTS: tuple[str, ...] = (
    "notosanscjk",
    "notoserifcjk",
    "notosansmonocjk",
    "noto sans cjk",
    "wenquanyi",
    "wqy",
    "simhei",
    "pingfang",
)
_CJK_FONT_NAME_HINTS: tuple[str, ...] = (
    "noto sans cjk",
    "noto sans sc",
    "noto sans tc",
    "noto sans mono cjk",
    "wenquanyi",
    "wqy",
    "simhei",
    "pingfang",
)
_CJK_FONT_PATH_ENV_VARS: tuple[str, ...] = (
    "UA_AUDIO_SPECTROGRAM_FONT_PATH",
    "AUDIO_SPECTROGRAM_FONT_PATH",
)
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2CEAF),
    (0x2F00, 0x2FDF),
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7A3),
)


def _env_font_path() -> str | None:
    for name in _CJK_FONT_PATH_ENV_VARS:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _is_cjk_character(character: str) -> bool:
    if not character:
        return False
    value = ord(character)
    return any(start <= value <= end for start, end in _CJK_RANGES)


def prompt_audio_stream_positions() -> str:
    """Ask for stream positions through the asynchronous CLI prompt API."""
    return (
        cli_ui.ask_string(
            "Select audio stream positions (e.g. 0,1 or all)",
            default="all",
        )
        or "all"
    )


def _run_ffprobe(command: list[str], file_path: str | Path) -> str:
    try:
        result = subprocess.run(  # noqa: S603  # nosemgrep: dangerous-subprocess-use-audit
            command, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Could not run ffprobe: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or "unknown ffprobe error"
        raise RuntimeError(
            f"ffprobe could not inspect '{file_path}': {detail}"
        )
    return result.stdout


def _decode_ffprobe_streams(stdout: str, file_path: str | Path) -> list[Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"ffprobe returned invalid JSON for '{file_path}'"
        ) from error
    if not isinstance(payload, dict):
        return []
    streams = cast(dict[str, Any], payload).get("streams", [])
    return cast(list[Any], streams) if isinstance(streams, list) else []


def _stream_dicts(streams: list[Any]) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], stream)
        for stream in streams
        if isinstance(stream, dict)
    ]


def get_audio_streams(file_path: str | Path) -> list[dict[str, Any]]:
    """Return the audio streams reported by ffprobe, or raise a useful error."""
    command = [
        configured_binary("ffprobe_path") or "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index:stream_tags=language,title",
        "-select_streams",
        "a",
        "-of",
        "json",
        str(file_path),
    ]
    stdout = _run_ffprobe(command, file_path)
    return _stream_dicts(_decode_ffprobe_streams(stdout, file_path))


def _selection_tokens(choice: str) -> list[str]:
    return [item.strip().lower() for item in choice.split(",") if item.strip()]


def _selected_stream_position(item: str, stream_count: int) -> int | None:
    if not item.isdigit():
        logger.warning(
            f"Invalid audio stream selection: {item}. Use zero-based positions or 'all'."
        )
        return None
    position = int(item)
    if not 0 <= position < stream_count:
        logger.warning(
            f"Invalid audio stream position: {position}. Available positions: 0-{stream_count - 1}."
        )
        return None
    return position


def select_audio_streams(
    streams: list[dict[str, Any]], choice: str
) -> list[dict[str, Any]]:
    """Select streams by their displayed, zero-based position; ``all`` selects all."""
    tokens = _selection_tokens(choice)
    if "all" in tokens:
        return streams
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in tokens:
        position = _selected_stream_position(item, len(streams))
        if position is None or position in seen:
            continue
        selected.append(streams[position])
        seen.add(position)
    return selected


def _positive_config_int(
    config: dict[str, Any], key: str, default: int
) -> int:
    value = config.get("DEFAULT", {}).get(key, default)
    try:
        value_as_int = int(value)
    except TypeError, ValueError:
        logger.warning(
            f"[yellow]Invalid {key!r} value {value!r}; using {default}.[/yellow]"
        )
        return default
    if value_as_int <= 0:
        logger.warning(
            f"[yellow]{key!r} must be positive; using {default}.[/yellow]"
        )
        return default
    return value_as_int


def _existing_spectrogram_sources(filelist: list[Any]) -> list[Path]:
    return [
        Path(file_path) for file_path in filelist if Path(file_path).is_file()
    ]


def _category_spectrogram_sources(
    category: str, sources: list[Path]
) -> list[Path]:
    if category == "BOOK":
        return [
            source
            for source in sources
            if source.suffix.lower() in AUDIOBOOK_EXTENSIONS
        ]
    if category == "MUSIC":
        return sources
    return sources[:1]


def get_spectrogram_sources(
    category: str,
    filelist: list[Any],
    disc_final_path: Path | None,
    max_source_files: int,
) -> list[Path]:
    """Return source files for a release, preserving all music/audiobook chapters."""
    if disc_final_path:
        return [disc_final_path]
    sources = _category_spectrogram_sources(
        category, _existing_spectrogram_sources(filelist)
    )
    return sources[:max_source_files]


def get_stft_parameters(sample_count: int) -> tuple[int, int]:
    """Bound the matrix plotted by Matplotlib while retaining useful frequency detail."""
    n_fft = min(
        SPECTROGRAM_N_FFT,
        max(32, 2 ** int(np.floor(np.log2(max(sample_count, 1))))),
    )
    hop_length = max(n_fft // 4, int(np.ceil(sample_count / MAX_TIME_BINS)))
    return n_fft, hop_length


def _cache_fingerprint(
    audio_sources: list[Path],
    duration: int,
    sample_rate: int,
    stream_indexes: list[tuple[Path, int]],
) -> str:
    data: dict[str, object] = {
        "cache_version": CACHE_VERSION,
        "sources": [
            {
                "path": str(source.resolve()),
                "size": source.stat().st_size,
                "mtime_ns": source.stat().st_mtime_ns,
            }
            for source in audio_sources
        ],
        "duration": duration,
        "sample_rate": sample_rate,
        "stream_indexes": [
            {"path": str(source.resolve()), "index": index}
            for source, index in stream_indexes
        ],
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


def _decoded_image_cache(content: str) -> dict[str, object]:
    if not content.strip():
        return {}
    loaded = json.loads(content)
    return cast(dict[str, object], loaded) if isinstance(loaded, dict) else {}


def _matching_cached_images(
    cache: dict[str, object], fingerprint: str
) -> list[Any]:
    images = cache.get("spectrograms_images")
    if cache.get("fingerprint") != fingerprint or not isinstance(images, list):
        return []
    return cast(list[Any], images)


def _load_cached_images(cache_path: Path, fingerprint: str) -> list[Any]:
    if not cache_path.exists():
        return []
    try:
        content = cache_path.read_text(encoding="utf-8")
        return _matching_cached_images(
            _decoded_image_cache(content), fingerprint
        )
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(
            f"[yellow]Could not load spectrogram image cache: {error!s}[/yellow]"
        )
        return []


def _font_name_for_file(font_path: str) -> str:
    try:
        return font_manager.FontProperties(fname=font_path).get_name()
    except RuntimeError, OSError:
        return Path(font_path).stem


def _register_font(font_path: str) -> None:
    with contextlib.suppress(Exception):
        font_manager.fontManager.addfont(font_path)  # pyright: ignore[reportUnknownMemberType]


def _font_path_supports_cjk(
    font_path: str, font_name: str | None = None
) -> bool:
    lower_font_path = font_path.lower()
    if any(hint in lower_font_path for hint in _CJK_SYSTEM_FONT_HINTS):
        return True
    if font_name:
        return any(hint in font_name.lower() for hint in _CJK_FONT_NAME_HINTS)
    return False


def _font_is_loadable(font_path: str) -> bool:
    try:
        ft2font.FT2Font(font_path)
        return True
    except Exception:
        return False


def _override_plot_font() -> tuple[str, bool, str] | None:
    override_path = _env_font_path()
    if not override_path:
        return None
    override_font_path = Path(override_path).expanduser()
    if not override_font_path.is_file():
        return None
    font_path = str(override_font_path.resolve())
    if not _font_is_loadable(font_path):
        logger.warning(
            f"[yellow]Configured spectrogram font '{override_font_path}' is not loadable; falling back to auto-detected font.[/yellow]"
        )
        return None
    _register_font(font_path)
    return (
        _font_name_for_file(font_path),
        _font_path_supports_cjk(font_path),
        font_path,
    )


def _preferred_font_candidate(font_name: str) -> tuple[str, bool, str] | None:
    try:
        font_path = str(
            cast(Any, font_manager).findfont(
                font_name, fallback_to_default=False
            )
        )
    except RuntimeError, ValueError:
        return None
    if not font_path or not _font_is_loadable(font_path):
        return None
    resolved_name = _font_name_for_file(font_path)
    _register_font(font_path)
    return (
        resolved_name,
        _font_path_supports_cjk(font_path, resolved_name),
        font_path,
    )


def _preferred_plot_fonts() -> tuple[
    tuple[str, bool, str] | None, tuple[str, bool, str] | None
]:
    fallback: tuple[str, bool, str] | None = None
    for font_name in _PREFERRED_PLOT_FONTS:
        candidate = _preferred_font_candidate(font_name)
        if candidate is None:
            continue
        if candidate[1]:
            return candidate, fallback
        if fallback is None:
            fallback = candidate
    return None, fallback


def _system_cjk_font() -> tuple[str, bool, str] | None:
    for font_path in dict.fromkeys(font_manager.findSystemFonts()):  # pyright: ignore[reportUnknownMemberType]
        if not _font_is_loadable(font_path):
            continue
        resolved_name = _font_name_for_file(font_path)
        if not _font_path_supports_cjk(font_path, resolved_name):
            continue
        _register_font(font_path)
        return resolved_name, True, font_path
    return None


def _discover_plot_font() -> tuple[str, bool, str | None]:
    override = _override_plot_font()
    if override is not None:
        return override
    preferred, fallback = _preferred_plot_fonts()
    if preferred is not None:
        return preferred
    system_font = _system_cjk_font()
    if system_font is not None:
        return system_font
    return fallback if fallback is not None else ("DejaVu Sans", False, None)


def _resolve_plot_font() -> tuple[str, bool, str | None]:
    global _PLOT_FONT_CACHE
    if _PLOT_FONT_CACHE is None:
        _PLOT_FONT_CACHE = _discover_plot_font()
    return _PLOT_FONT_CACHE


def _build_plot_font_properties(
    font_path: str | None,
) -> tuple[font_manager.FontProperties | None, bool]:
    if not font_path:
        return None, False
    try:
        return font_manager.FontProperties(fname=font_path), True
    except Exception as error:
        logger.warning(
            f"[yellow]Could not load spectrogram font from '{font_path}': {error}[/yellow]"
        )
    return None, False


def _sanitize_plot_text(text: str, supports_unicode: bool) -> str:
    if supports_unicode:
        return text
    return "".join(
        "?" if _is_cjk_character(character) else character
        for character in text
    )


def _ffmpeg_spectrogram_command(
    stream_index: int,
    file_path: str | Path,
    duration: int,
    sample_rate: int,
) -> list[str]:
    return [
        configured_binary("ffmpeg_path") or "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(file_path),
        "-map",
        f"0:{stream_index}",
        "-t",
        str(duration),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        "pipe:1",
    ]


def _decode_spectrogram_audio(
    stream_index: int,
    file_path: str | Path,
    duration: int,
    sample_rate: int,
) -> bytes:
    command = _ffmpeg_spectrogram_command(
        stream_index, file_path, duration, sample_rate
    )
    try:
        result = subprocess.run(  # noqa: S603  # nosemgrep: dangerous-subprocess-use-audit
            command, capture_output=True, check=False, timeout=duration + 120
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f"Could not decode audio stream {stream_index}: {error}"
        ) from error
    if result.returncode or not result.stdout:
        detail = (
            result.stderr.decode(errors="replace").strip()
            or "no audio was produced"
        )
        raise RuntimeError(
            f"FFmpeg could not decode audio stream {stream_index}: {detail}"
        )
    return result.stdout


def _load_spectrogram_samples(
    decoded_audio: bytes, stream_index: int
) -> tuple[Any, int]:
    try:
        samples, actual_sample_rate = librosa.load(
            io.BytesIO(decoded_audio), sr=None, mono=True
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not read decoded audio for stream {stream_index}: {error}"
        ) from error
    if samples.size == 0:
        raise RuntimeError(
            f"Audio stream {stream_index} contains no decodable samples."
        )
    return samples, int(actual_sample_rate)


def _resolved_plot_font_state(
    font_properties: font_manager.FontProperties | None,
    supports_unicode: bool | None,
) -> tuple[font_manager.FontProperties | None, bool]:
    if supports_unicode is not None and font_properties is not None:
        return font_properties, supports_unicode
    _font_name, resolved_supports_unicode, plot_font_path = (
        _resolve_plot_font()
    )
    if font_properties is not None:
        return font_properties, resolved_supports_unicode
    properties, property_supports_unicode = _build_plot_font_properties(
        plot_font_path
    )
    return properties, resolved_supports_unicode and property_supports_unicode


def _spectrogram_display_label(
    stream_index: int, stream_label: str, source_name: str
) -> str:
    if stream_label and stream_label != f"Stream_{stream_index}":
        return stream_label
    return source_name


def _render_spectrogram(
    db_spectrogram: Any,
    actual_sample_rate: int,
    hop_length: int,
    stream_index: int,
    stream_label: str,
    stream_lang: str,
    duration: int,
    source_position: int,
    source_name: str,
    output_dir: Path,
    font_properties: font_manager.FontProperties | None,
    supports_unicode: bool,
) -> Path:
    with matplotlib.rc_context({"font.family": ["sans-serif"]}):  # pyright: ignore[reportUnknownMemberType]
        figure, axis = plt.subplots(
            figsize=(WIDTH_INCH, HEIGHT_INCH), dpi=DPI_VALUE
        )  # pyright: ignore[reportUnknownMemberType]
        image = librosa.display.specshow(
            db_spectrogram,
            sr=actual_sample_rate,
            hop_length=hop_length,
            x_axis="time",
            y_axis="hz",
            cmap="inferno",
            ax=axis,
            rasterized=True,
        )
        figure.colorbar(image, ax=axis, format="%+2.0f dB")  # pyright: ignore[reportUnknownMemberType]
        display_label = _spectrogram_display_label(
            stream_index, stream_label, source_name
        )
        axis.set_title(  # pyright: ignore[reportUnknownMemberType]
            _sanitize_plot_text(display_label, supports_unicode),
            fontsize=18,
            fontweight="bold",
            pad=22,
            fontproperties=font_properties,
        )
        axis.text(  # pyright: ignore[reportUnknownMemberType]
            0.5,
            1.01,
            _sanitize_plot_text(
                f"File: {source_name}  •  Stream {stream_index}  •  {stream_lang}  •  First {duration}s  •  mono mix @ {actual_sample_rate / 1000:g} kHz",
                supports_unicode,
            ),
            transform=axis.transAxes,
            ha="center",
            va="bottom",
            fontsize=10,
            fontproperties=font_properties,
        )
        axis.set_xlabel("Time (s)")  # pyright: ignore[reportUnknownMemberType]
        axis.set_ylabel("Frequency (Hz)")  # pyright: ignore[reportUnknownMemberType]
        output_name = output_dir / (
            f"spectrogram_source_{source_position:02d}_stream_{stream_index}.png"
        )
        figure.tight_layout()
        figure.savefig(output_name, dpi=DPI_VALUE, bbox_inches="tight")  # pyright: ignore[reportUnknownMemberType]
        plt.close(figure)
    return output_name


def generate_spectrogram(
    stream_index: int,
    stream_label: str,
    stream_lang: str,
    file_path: str | Path,
    output_dir: Path,
    duration: int,
    sample_rate: int,
    source_position: int,
    source_name: str,
    font_properties: font_manager.FontProperties | None = None,
    supports_unicode: bool | None = None,
) -> Path:
    """Decode one stream and generate a frequency/time image suitable for review."""
    decoded_audio = _decode_spectrogram_audio(
        stream_index, file_path, duration, sample_rate
    )
    samples, actual_sample_rate = _load_spectrogram_samples(
        decoded_audio, stream_index
    )
    n_fft, hop_length = get_stft_parameters(samples.size)
    stft = np.abs(librosa.stft(samples, n_fft=n_fft, hop_length=hop_length))
    db_spectrogram = librosa.amplitude_to_db(stft, ref=np.max)  # pyright: ignore[reportUnknownMemberType]
    font_properties, supports_unicode = _resolved_plot_font_state(
        font_properties, supports_unicode
    )
    return _render_spectrogram(
        db_spectrogram,
        actual_sample_rate,
        hop_length,
        stream_index,
        stream_label,
        stream_lang,
        duration,
        source_position,
        source_name,
        output_dir,
        font_properties,
        supports_unicode,
    )


type SourceStreams = list[tuple[int, Path, list[dict[str, Any]]]]
type SpectrogramJob = tuple[int, Path, dict[str, Any]]


@dataclass(frozen=True)
class _SpectrogramPlan:
    audio_sources: list[Path]
    selected_jobs: list[SpectrogramJob]
    output_dir: Path
    cache_path: Path


def _first_bdinfo_file(data: dict[str, Any]) -> str:
    files = data.get("files", [])
    if not isinstance(files, list) or not files:
        return ""
    first = cast(list[Any], files)[0]
    if not isinstance(first, dict):
        return ""
    return str(cast(dict[str, Any], first).get("file", ""))


def _disc_spectrogram_path(bdinfo: Any) -> Path | None:
    if not isinstance(bdinfo, dict):
        return None
    data = cast(dict[str, Any], bdinfo)
    disc_path = str(data.get("path", ""))
    disc_file = _first_bdinfo_file(data)
    if not disc_path or not disc_file:
        return None
    path = Path(disc_path) / "STREAM" / disc_file
    logger.debug(f"disc_final_path: {path}")
    return path


def _limited_audio_sources(
    meta: Meta, config: dict[str, Any], disc_final_path: Path | None
) -> list[Path]:
    max_source_files = _positive_config_int(
        config, "audio_spectrogram_max_files", 12
    )
    all_sources = get_spectrogram_sources(
        meta.category,
        meta.filelist,
        disc_final_path,
        max(len(meta.filelist), 1),
    )
    if len(all_sources) > max_source_files:
        logger.info(
            f"[yellow]Limiting audio spectrogram generation to the first {max_source_files} of {len(all_sources)} {meta.category.lower()} audio files.[/yellow]"
        )
    return all_sources[:max_source_files]


def _bdinfo_audio_entries(bdinfo: Any) -> list[dict[str, Any]]:
    if not isinstance(bdinfo, dict):
        return []
    entries = cast(dict[str, Any], bdinfo).get("audio", [])
    if not isinstance(entries, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], entries)
        if isinstance(item, dict)
    ]


def _apply_bdinfo_audio_tags(
    streams: list[dict[str, Any]], bdinfo: Any
) -> None:
    entries = _bdinfo_audio_entries(bdinfo)
    for position, stream in enumerate(streams):
        if position >= len(entries):
            continue
        tags = stream.setdefault("tags", {})
        entry = entries[position]
        if not tags.get("language") or tags.get("language") == "und":
            tags["language"] = entry.get("language", "und")
        tags.setdefault("title", entry.get("codec", "No Title"))


async def _probe_audio_source(audio_path: Path) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(get_audio_streams, audio_path)
    except RuntimeError as error:
        logger.error(f"[red]{error}[/red]")
        return []


async def _source_streams(
    audio_sources: list[Path], bdinfo: Any, disc_final_path: Path | None
) -> SourceStreams:
    source_streams: SourceStreams = []
    for source_position, audio_path in enumerate(audio_sources, start=1):
        streams = await _probe_audio_source(audio_path)
        if bdinfo and audio_path == disc_final_path:
            _apply_bdinfo_audio_tags(streams, bdinfo)
        if streams:
            source_streams.append((source_position, audio_path, streams))
    return source_streams


def _automatic_stream_choice(
    meta: Meta, config: dict[str, Any], source_count: int
) -> str | None:
    if meta.audio_spectrogram_tracks is not None:
        return str(meta.audio_spectrogram_tracks)
    if not meta.unattended and source_count <= 1:
        return None
    return (
        "all"
        if config.get("DEFAULT", {}).get(
            "process_all_audio_spectrogram", False
        )
        else "0"
    )


def _prompt_stream_choice(source_streams: SourceStreams) -> str:
    _, first_audio_path, first_streams = source_streams[0]
    logger.info(
        f"Available audio streams for {first_audio_path.name} (use zero-based positions):"
    )
    for position, stream in enumerate(first_streams):
        tags = stream.get("tags", {})
        logger.info(
            f"[{position}] FFmpeg stream {stream.get('index')} | Lang: {tags.get('language', 'und')} | Title: {tags.get('title', 'No Title')}"
        )
    return prompt_audio_stream_positions()


def _stream_choice(
    meta: Meta, config: dict[str, Any], source_streams: SourceStreams
) -> str:
    automatic = _automatic_stream_choice(meta, config, len(source_streams))
    return (
        automatic
        if automatic is not None
        else _prompt_stream_choice(source_streams)
    )


def _selected_spectrogram_jobs(
    source_streams: SourceStreams, choice: str
) -> list[SpectrogramJob]:
    jobs: list[SpectrogramJob] = []
    for source_position, audio_path, streams in source_streams:
        selected = select_audio_streams(streams, choice)
        if not selected:
            logger.warning(
                f"[yellow]No valid streams selected for {audio_path.name}; skipping it.[/yellow]"
            )
            continue
        jobs.extend(
            (source_position, audio_path, stream) for stream in selected
        )
    return jobs


async def _prepare_spectrogram_plan(
    meta: Meta, config: dict[str, Any]
) -> _SpectrogramPlan | None:
    output_dir = spectrograms_dir(meta.base_dir, meta.uuid)
    cache_path = (
        Path(meta.base_dir)
        / "tmp"
        / meta.uuid
        / "audio_spectrograms_images.json"
    )
    disc_final_path = _disc_spectrogram_path(meta.bdinfo)
    audio_sources = _limited_audio_sources(meta, config, disc_final_path)
    if not audio_sources:
        logger.info(
            "[red]Could not find a valid audio or video file to process spectrograms from.[/red]"
        )
        return None
    source_streams = await _source_streams(
        audio_sources, meta.bdinfo, disc_final_path
    )
    if not source_streams:
        logger.warning("No audio streams found.")
        return None
    jobs = _selected_spectrogram_jobs(
        source_streams, _stream_choice(meta, config, source_streams)
    )
    if not jobs:
        logger.warning(
            "[yellow]No valid audio streams were selected.[/yellow]"
        )
        return None
    return _SpectrogramPlan(audio_sources, jobs, output_dir, cache_path)


def _spectrogram_render_settings(
    config: dict[str, Any],
) -> tuple[int, int, font_manager.FontProperties | None, bool]:
    duration = _positive_config_int(
        config, "audio_spectrogram_duration", DURATION_LIMIT
    )
    sample_rate = _positive_config_int(
        config, "audio_spectrogram_sample_rate", SAMPLE_RATE
    )
    _, supports_unicode, plot_font_path = _resolve_plot_font()
    properties, property_supports_unicode = _build_plot_font_properties(
        plot_font_path
    )
    return (
        duration,
        sample_rate,
        properties,
        supports_unicode and property_supports_unicode,
    )


def _plan_fingerprint(
    plan: _SpectrogramPlan, duration: int, sample_rate: int
) -> str:
    stream_indexes = [
        (audio_path, int(stream["index"]))
        for _, audio_path, stream in plan.selected_jobs
    ]
    return _cache_fingerprint(
        plan.audio_sources, duration, sample_rate, stream_indexes
    )


async def _use_cached_spectrograms(
    meta: Meta, cache_path: Path, fingerprint: str
) -> bool:
    cached_images = await asyncio.to_thread(
        _load_cached_images, cache_path, fingerprint
    )
    if not cached_images:
        return False
    meta.spectrograms_images = cached_images
    logger.debug(
        f"[cyan]Loaded {len(cached_images)} matching cached spectrograms.[/cyan]"
    )
    return True


async def _generate_spectrogram_files(
    plan: _SpectrogramPlan,
    duration: int,
    sample_rate: int,
    font_properties: font_manager.FontProperties | None,
    supports_unicode: bool,
) -> list[str]:
    generated_files: list[str] = []
    for source_position, audio_path, stream in plan.selected_jobs:
        tags = stream.get("tags", {})
        label = tags.get("title", f"Stream_{stream['index']}")
        language = tags.get("language", "und")
        try:
            file_path = await asyncio.to_thread(
                generate_spectrogram,
                int(stream["index"]),
                label,
                language,
                audio_path,
                plan.output_dir,
                duration,
                sample_rate,
                source_position,
                audio_path.stem,
                font_properties,
                supports_unicode,
            )
        except RuntimeError as error:
            logger.error(f"[red]{error}[/red]")
            continue
        generated_files.append(str(file_path))
    return generated_files


async def _save_spectrogram_cache(
    cache_path: Path, fingerprint: str, images: list[Any]
) -> None:
    cache: dict[str, object] = {
        "cache_version": CACHE_VERSION,
        "fingerprint": fingerprint,
        "spectrograms_images": images,
    }
    await asyncio.to_thread(
        cache_path.write_text,
        json.dumps(cache, indent=4),
        encoding="utf-8",
    )
    logger.debug(
        f"[cyan]Saved {len(images)} spectrograms to audio_spectrograms_images.json[/cyan]"
    )


async def _upload_spectrograms(
    meta: Meta,
    uploadscreens_manager: Any,
    generated_files: list[str],
    cache_path: Path,
    fingerprint: str,
) -> None:
    if not generated_files or uploadscreens_manager is None:
        return
    logger.info("[yellow]Uploading Audio Spectrograms...[/yellow]")
    try:
        spec_images, _ = await uploadscreens_manager.upload_screens(
            meta,
            len(generated_files),
            1,
            0,
            len(generated_files),
            generated_files,
            {},
        )
        if spec_images:
            meta.spectrograms_images = spec_images
            await _save_spectrogram_cache(cache_path, fingerprint, spec_images)
    except Exception as error:
        logger.error(f"[red]Error uploading audio spectrograms: {error}[/red]")


async def _render_spectrogram_plan(
    meta: Meta,
    config: dict[str, Any],
    uploadscreens_manager: Any,
    plan: _SpectrogramPlan,
) -> list[str]:
    duration, sample_rate, font_properties, supports_unicode = (
        _spectrogram_render_settings(config)
    )
    fingerprint = _plan_fingerprint(plan, duration, sample_rate)
    if await _use_cached_spectrograms(meta, plan.cache_path, fingerprint):
        return []
    generated_files = await _generate_spectrogram_files(
        plan, duration, sample_rate, font_properties, supports_unicode
    )
    await _upload_spectrograms(
        meta,
        uploadscreens_manager,
        generated_files,
        plan.cache_path,
        fingerprint,
    )
    return generated_files


async def process_audio_spectrograms(
    meta: Meta, config: dict[str, Any], uploadscreens_manager: Any = None
) -> list[str]:
    if meta.spectrograms_images:
        return []
    logger.info("[yellow]Generating Audio Spectrograms...[/yellow]")
    plan = await _prepare_spectrogram_plan(meta, config)
    if plan is None:
        return []
    return await _render_spectrogram_plan(
        meta, config, uploadscreens_manager, plan
    )
