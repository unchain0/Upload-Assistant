# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, cast

import aiofiles

from src.domain_models.processing import NoAudioMediaError
from src.domain_models.release import Meta
from src.integrations.media.media_info import MediaInfo
from src.integrations.observability.runtime_support import logger
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)

_DECLARED_RESOLUTION_DIMENSIONS = {
    "480p": (854, 480),
    "480i": (854, 480),
    "576p": (1024, 576),
    "576i": (1024, 576),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1080i": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
    "4320p": (7680, 4320),
    "8640p": (15360, 8640),
}


def _actual_dimensions(res: str, width: str | int) -> tuple[int, int] | None:
    try:
        return int(width), int(res.rsplit("x", 1)[1][:-1])
    except IndexError, TypeError, ValueError:
        return None


def _dimensions_fit_nominal(
    nominal: tuple[int, int], actual: tuple[int, int]
) -> bool:
    nominal_width, nominal_height = nominal
    actual_width, actual_height = actual
    return (
        nominal_height * 0.70 <= actual_height <= nominal_height * 1.05
        and actual_width <= nominal_width * 1.25
    )


def _declared_resolution_is_compatible(
    declared: Any, res: str, width: str | int, scan: str
) -> bool:
    declared_text = str(declared or "").lower()
    nominal = _DECLARED_RESOLUTION_DIMENSIONS.get(declared_text)
    actual = _actual_dimensions(res, width)
    if nominal is None or actual is None:
        return False
    if not declared_text.endswith(scan):
        return False
    return _dimensions_fit_nominal(nominal, actual)


def _resolved_file_path(file_path: str) -> Path:
    try:
        return Path(file_path).resolve()
    except (OSError, ValueError) as error:
        raise ValueError(f"Invalid file path: {error}") from error


def _validate_resolved_path(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"File does not exist: {path}")
    if path.is_file() or path.is_dir():
        return
    raise ValueError(f"Path is neither a file nor directory: {path}")


def validate_file_path(file_path: str) -> str:
    if not file_path:
        raise ValueError("File path cannot be empty")
    path = _resolved_file_path(file_path)
    _validate_resolved_path(path)
    return str(path)


def _windows_dvd_mediainfo(binary_root: Path) -> dict[str, Any] | None:
    cli_path = binary_root / "windows" / "dvd" / "MediaInfo.exe"
    if cli_path.exists():
        logger.debug(f"[blue]Windows MediaInfo CLI: {cli_path} (found)[/blue]")
        return {"cli": cli_path, "lib": None, "lib_dir": None}
    logger.debug(
        f"[yellow]Windows MediaInfo CLI: {cli_path} (not found)[/yellow]"
    )
    return None


def _prepend_ld_library_path(lib_dir: Path) -> None:
    lib_dir_str = str(lib_dir)
    current = os.environ.get("LD_LIBRARY_PATH", "")
    current_paths = current.split(os.pathsep) if current else []
    if lib_dir_str in current_paths:
        return
    os.environ["LD_LIBRARY_PATH"] = (
        f"{lib_dir_str}{os.pathsep}{current}" if current else lib_dir_str
    )
    logger.debug(
        f"[blue]Updated LD_LIBRARY_PATH to include: {lib_dir_str}[/blue]"
    )


def _availability_label(available: bool) -> str:
    return "found" if available else "not found"


def _linux_dvd_mediainfo(binary_root: Path) -> dict[str, Any]:
    lib_dir = binary_root / "linux" / "dvd"
    mediainfo_lib = lib_dir / "libmediainfo.so.0"
    mediainfo_cli = lib_dir / "mediainfo"
    cli_available = mediainfo_cli.exists()
    lib_available = mediainfo_lib.exists()
    logger.debug(
        f"[blue]MediaInfo CLI binary: {mediainfo_cli} ({_availability_label(cli_available)})[/blue]"
    )
    logger.debug(
        f"[blue]MediaInfo library: {mediainfo_lib} ({_availability_label(lib_available)})[/blue]"
    )
    if lib_available:
        _prepend_ld_library_path(lib_dir)
    return {
        "cli": mediainfo_cli if cli_available else None,
        "lib": mediainfo_lib if lib_available else None,
        "lib_dir": lib_dir,
    }


def find_dvd_mediainfo(base_dir: str | Path) -> dict[str, Any] | None:
    """Return the MediaInfo components installed for DVD processing."""
    configured = configured_binary("dvd_mediainfo_path")
    if configured:
        return {"cli": Path(configured), "lib": None, "lib_dir": None}
    system = platform.system().lower()
    binary_root = Path(base_dir) / "bin" / "MI"
    if system == "windows":
        return _windows_dvd_mediainfo(binary_root)
    if system == "linux":
        return _linux_dvd_mediainfo(binary_root)
    return None


_RESOLUTION_MAP = {
    "3840x2160p": "2160p",
    "2160p": "2160p",
    "2560x1440p": "1440p",
    "1440p": "1440p",
    "1920x1080p": "1080p",
    "1080p": "1080p",
    "1920x1080i": "1080i",
    "1080i": "1080i",
    "1280x720p": "720p",
    "720p": "720p",
    "1280x540p": "720p",
    "1280x576p": "720p",
    "1024x576p": "576p",
    "576p": "576p",
    "1024x576i": "576i",
    "576i": "576i",
    "960x540p": "540p",
    "540p": "540p",
    "960x540i": "540i",
    "540i": "540i",
    "854x480p": "480p",
    "480p": "480p",
    "854x480i": "480i",
    "480i": "480i",
    "720x576p": "576p",
    "720x576i": "576i",
    "720x480p": "480p",
    "720x480i": "480i",
    "15360x8640p": "8640p",
    "8640p": "8640p",
    "7680x4320p": "4320p",
    "4320p": "4320p",
    "OTHER": "OTHER",
}
_WIDTH_RESOLUTION_MAP = {
    "3840p": "2160p",
    "2560p": "1550p",
    "1920p": "1080p",
    "1920i": "1080i",
    "1280p": "720p",
    "1024p": "576p",
    "1024i": "576i",
    "960p": "540p",
    "960i": "540i",
    "854p": "480p",
    "854i": "480i",
    "720p": "576p",
    "720i": "576i",
    "15360p": "4320p",
    "OTHERp": "OTHER",
}


def _guess_screen_size(guess: Any) -> str:
    if not isinstance(guess, dict):
        return ""
    return str(cast(dict[str, Any], guess).get("screen_size", ""))


def _fallback_resolution(guess: Any, width: str | int, scan: str) -> str:
    declared = _guess_screen_size(guess)
    if declared in _RESOLUTION_MAP:
        return declared
    return _WIDTH_RESOLUTION_MAP.get(f"{width}{scan}", "OTHER")


async def mi_resolution(
    res: str,
    guess: dict[str, Any] | None,
    width: str | int,
    scan: str,
) -> str:
    declared_resolution = _guess_screen_size(guess)
    if _declared_resolution_is_compatible(
        declared_resolution, res, width, scan
    ):
        return str(declared_resolution).lower()
    resolution = _RESOLUTION_MAP.get(res)
    if resolution is None:
        resolution = _fallback_resolution(guess, width, scan)
    return resolution if resolution in _RESOLUTION_MAP else "OTHER"


_GENERAL_TRACK_KEYS = (
    "@type",
    "UniqueID",
    "VideoCount",
    "AudioCount",
    "TextCount",
    "MenuCount",
    "FileExtension",
    "Format",
    "Format_Version",
    "FileSize",
    "Duration",
    "OverallBitRate",
    "FrameRate",
    "FrameCount",
    "StreamSize",
    "IsStreamable",
    "File_Created_Date",
    "File_Created_Date_Local",
    "File_Modified_Date",
    "File_Modified_Date_Local",
    "Encoded_Application",
    "Encoded_Library",
    "extra",
)
_GENERAL_OPTIONAL_TAGS = (
    "Album",
    "Album_Performer",
    "Track_name",
    "Performer",
    "Composer",
    "Publisher",
    "Genre",
    "Recorded_Date",
    "ISBN",
    "Comment",
    "Description",
    "album",
    "album_performer",
    "track_name",
    "performer",
    "composer",
    "publisher",
    "genre",
    "recorded_date",
    "isbn",
    "comment",
    "description",
)
_VIDEO_TRACK_KEYS = (
    "@type",
    "StreamOrder",
    "ID",
    "UniqueID",
    "Format",
    "Format_Profile",
    "Format_Version",
    "Format_Level",
    "Format_Tier",
    "HDR_Format",
    "HDR_Format_Version",
    "HDR_Format_String",
    "HDR_Format_Profile",
    "HDR_Format_Level",
    "HDR_Format_Settings",
    "HDR_Format_Compression",
    "HDR_Format_Compatibility",
    "CodecID",
    "CodecID_Hint",
    "Duration",
    "BitRate",
    "Width",
    "Height",
    "Stored_Height",
    "Sampled_Width",
    "Sampled_Height",
    "PixelAspectRatio",
    "DisplayAspectRatio",
    "FrameRate_Mode",
    "FrameRate",
    "FrameRate_Original",
    "FrameRate_Num",
    "FrameRate_Den",
    "FrameCount",
    "Standard",
    "ColorSpace",
    "ChromaSubsampling",
    "ChromaSubsampling_Position",
    "BitDepth",
    "ScanType",
    "ScanOrder",
    "Delay",
    "Delay_Source",
    "StreamSize",
    "Language",
    "Default",
    "Forced",
    "colour_description_present",
    "colour_description_present_Source",
    "colour_range",
    "colour_range_Source",
    "colour_primaries",
    "colour_primaries_Source",
    "transfer_characteristics",
    "transfer_characteristics_Source",
    "transfer_characteristics_Original",
    "matrix_coefficients",
    "matrix_coefficients_Source",
    "MasteringDisplay_ColorPrimaries",
    "MasteringDisplay_ColorPrimaries_Source",
    "MasteringDisplay_Luminance",
    "MasteringDisplay_Luminance_Source",
    "MaxCLL",
    "MaxCLL_Source",
    "MaxFALL",
    "MaxFALL_Source",
    "Encoded_Library_Settings",
    "Encoded_Library",
    "Encoded_Library_Name",
)
_AUDIO_TRACK_KEYS = (
    "@type",
    "StreamOrder",
    "ID",
    "UniqueID",
    "Format",
    "Format_Version",
    "Format_Profile",
    "Format_Settings",
    "Format_Commercial_IfAny",
    "Format_Settings_Endianness",
    "Format_AdditionalFeatures",
    "CodecID",
    "Duration",
    "BitRate_Mode",
    "BitRate",
    "Channels",
    "ChannelPositions",
    "ChannelLayout",
    "Channels_Original",
    "ChannelLayout_Original",
    "SamplesPerFrame",
    "SamplingRate",
    "SamplingCount",
    "FrameRate",
    "FrameCount",
    "Compression_Mode",
    "Delay",
    "Delay_Source",
    "Video_Delay",
    "StreamSize",
    "Title",
    "Language",
    "ServiceKind",
    "Default",
    "Forced",
    "extra",
)
_TEXT_TRACK_KEYS = (
    "@type",
    "@typeorder",
    "StreamOrder",
    "ID",
    "UniqueID",
    "Format",
    "CodecID",
    "Duration",
    "BitRate",
    "FrameRate",
    "FrameCount",
    "ElementCount",
    "StreamSize",
    "Title",
    "Language",
    "Default",
    "Forced",
)
_TRACK_KEYS: dict[str, tuple[str, ...]] = {
    "General": _GENERAL_TRACK_KEYS,
    "Video": _VIDEO_TRACK_KEYS,
    "Audio": _AUDIO_TRACK_KEYS,
    "Text": _TEXT_TRACK_KEYS,
    "Menu": ("@type", "extra"),
}


def _append_general_tags(
    track: dict[str, Any], projected: dict[str, Any]
) -> None:
    for tag in _GENERAL_OPTIONAL_TAGS:
        if tag in track:
            projected[tag] = track[tag]


def _project_track(track: dict[str, Any]) -> dict[str, Any] | None:
    track_type = str(track.get("@type", ""))
    keys = _TRACK_KEYS.get(track_type)
    if keys is None:
        return None
    projected = {key: track.get(key, {}) for key in keys}
    if track_type == "General":
        _append_general_tags(track, projected)
    return projected


def _media_tracks(media: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tracks = media.get("track", [])
    if not isinstance(raw_tracks, list):
        return []
    return [
        cast(dict[str, Any], item)
        for item in cast(list[Any], raw_tracks)
        if isinstance(item, dict)
    ]


def _filter_mediainfo(data: dict[str, Any]) -> dict[str, Any]:
    media = data.get("media")
    if not isinstance(media, dict):
        return {
            "creatingLibrary": data.get("creatingLibrary"),
            "media": {"@ref": "", "track": []},
        }
    media_dict = cast(dict[str, Any], media)
    projected_tracks = [
        projected
        for track in _media_tracks(media_dict)
        if (projected := _project_track(track)) is not None
    ]
    return {
        "creatingLibrary": data.get("creatingLibrary"),
        "media": {
            "@ref": media_dict.get("@ref", ""),
            "track": projected_tracks,
        },
    }


async def _parse_mediainfo(video: str, **kwargs: Any) -> Any:
    return await asyncio.to_thread(MediaInfo.parse, video, **kwargs)


def _dvd_mediainfo_cli(base_dir: str, is_dvd: bool) -> str | None:
    if not is_dvd:
        return None
    logger.debug("[bold yellow]DVD detected, using specialized MediaInfo...")
    current_platform = platform.system().lower()
    if current_platform not in {"linux", "windows"}:
        logger.debug(
            f"[yellow]DVD processing on {current_platform} not supported with specialized MediaInfo[/yellow]"
        )
        return None
    config = find_dvd_mediainfo(base_dir)
    if not config:
        logger.debug(
            "[yellow]No specialized MediaInfo components found, using system MediaInfo[/yellow]"
        )
        return None
    cli = config.get("cli")
    if not cli:
        logger.debug("[yellow]DVD MediaInfo CLI not available[/yellow]")
        return None
    return str(cli)


async def _run_specialized_mediainfo(
    video: str, mediainfo_cmd: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    safe_video_path = validate_file_path(video)
    safe_mediainfo_cmd = validate_file_path(mediainfo_cmd)
    command = [safe_mediainfo_cmd, *arguments, safe_video_path]
    result = cast(
        subprocess.CompletedProcess[str],
        await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=30,
        ),
    )
    if result.returncode == 0 and result.stdout:
        return result
    raise subprocess.CalledProcessError(
        result.returncode, command, result.stdout, result.stderr
    )


async def _standard_mediainfo_text(video: str) -> str:
    return str(await _parse_mediainfo(video, output="STRING", full=False))


async def _standard_mediainfo_json(video: str) -> dict[str, Any]:
    payload = await _parse_mediainfo(video, output="JSON")
    return cast(dict[str, Any], json.loads(str(payload)))


async def _mediainfo_text(video: str, mediainfo_cmd: str | None) -> str:
    if mediainfo_cmd is None:
        return await _standard_mediainfo_text(video)
    try:
        return (await _run_specialized_mediainfo(video, mediainfo_cmd)).stdout
    except subprocess.TimeoutExpired:
        logger.info(
            "[bold red]Specialized MediaInfo timed out (30s) - falling back to standard MediaInfo[/bold red]"
        )
    except ValueError as error:
        logger.info(f"[bold red]Path validation error: {error}[/bold red]")
    except Exception as error:
        logger.info(
            f"[bold red]Error getting text from specialized MediaInfo: {error}"
        )
    logger.info("[bold yellow]Falling back to standard MediaInfo for text...")
    return await _standard_mediainfo_text(video)


async def _mediainfo_json(
    video: str, mediainfo_cmd: str | None
) -> dict[str, Any]:
    if mediainfo_cmd is None:
        return await _standard_mediainfo_json(video)
    try:
        result = await _run_specialized_mediainfo(
            video, mediainfo_cmd, "--Output=JSON"
        )
        return cast(dict[str, Any], json.loads(result.stdout))
    except Exception as error:
        logger.info(
            f"[bold red]Error getting JSON from specialized MediaInfo: {error}[/bold red]"
        )
        logger.info(
            "[bold yellow]Falling back to standard MediaInfo for JSON...[/bold yellow]"
        )
        return await _standard_mediainfo_json(video)


def _media_info_output_dir(base_dir: str, folder_id: str) -> Path:
    return Path(base_dir) / "tmp" / folder_id


async def _write_mediainfo_text_reports(
    video: str, base_dir: str, folder_id: str, media_info: str
) -> None:
    output_dir = _media_info_output_dir(base_dir, folder_id)
    clean_text = media_info.replace(video, Path(video).name)
    for filename in ("MEDIAINFO.txt", "MEDIAINFO_CLEANPATH.txt"):
        async with aiofiles.open(
            output_dir / filename,
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            await handle.write(clean_text)
    logger.debug("[bold green]MediaInfo Exported.")


async def _write_mediainfo_json_report(
    base_dir: str, folder_id: str, filtered_info: dict[str, Any]
) -> dict[str, Any]:
    output_path = (
        _media_info_output_dir(base_dir, folder_id) / "MediaInfo.json"
    )
    async with aiofiles.open(output_path, "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(filtered_info, indent=4))
    logger.debug(f"[green]JSON file written to: {output_path}[/green]")
    async with aiofiles.open(output_path, encoding="utf-8") as handle:
        return cast(dict[str, Any], json.loads(await handle.read()))


async def export_info(
    video: str,
    isdir: bool,
    folder_id: str,
    base_dir: str,
    is_dvd: bool = False,
) -> dict[str, Any]:
    mediainfo_cmd = _dvd_mediainfo_cli(base_dir, is_dvd)
    logger.debug("[bold yellow]Exporting MediaInfo...")
    if not isdir:
        os.chdir(Path(video).parent)
    media_info = await _mediainfo_text(video, mediainfo_cmd)
    await _write_mediainfo_text_reports(video, base_dir, folder_id, media_info)
    media_info_dict = await _mediainfo_json(video, mediainfo_cmd)
    filtered_info = _filter_mediainfo(media_info_dict)
    return await _write_mediainfo_json_report(
        base_dir, folder_id, filtered_info
    )


def _meta_mediainfo_tracks(meta: Meta) -> list[dict[str, Any]]:
    media = meta.mediainfo.get("media", {})
    if not isinstance(media, dict):
        return []
    tracks = cast(dict[str, Any], media).get("track", [])
    if not isinstance(tracks, list):
        return []
    return [
        cast(dict[str, Any], track)
        for track in cast(list[Any], tracks)
        if isinstance(track, dict)
    ]


def _present_field(track: dict[str, Any], field: str) -> str:
    value = track.get(field)
    if value in (None, {}, ""):
        return ""
    return str(value).strip()


def _track_value(
    tracks: list[dict[str, Any]], track_type: str, field: str
) -> str:
    for track in tracks:
        if track.get("@type") != track_type:
            continue
        value = _present_field(track, field)
        if value:
            return value
    return ""


def _validate_audio_present(meta: Meta, tracks: list[dict[str, Any]]) -> None:
    if any(track.get("@type") == "Audio" for track in tracks):
        return
    raise NoAudioMediaError(f"{meta.ua_name} does not support no audio media.")


def _log_mediainfo_tracks(tracks: list[dict[str, Any]]) -> None:
    names = [str(track.get("@type", "Unknown")) for track in tracks]
    logger.debug(f"[cyan]MediaInfo tracks: {', '.join(names)}[/cyan]")


def _is_mkv_release(meta: Meta) -> bool:
    return any(str(path).lower().endswith(".mkv") for path in meta.filelist)


def _validation_spec(settings: bool) -> tuple[str, str, str, str]:
    if settings:
        return (
            "Video",
            "Encoded_Library_Settings",
            "encoding settings",
            "no encoding settings",
        )
    return "General", "UniqueID", "Unique ID", "no unique ID"


def validate_mediainfo(meta: Meta, settings: bool = False) -> bool:
    if not _is_mkv_release(meta):
        logger.debug(
            f"[yellow]Skipping {meta.path} (not an .mkv file)[/yellow]"
        )
        return True
    logger.debug("[cyan]Validating MediaInfo")
    tracks = _meta_mediainfo_tracks(meta)
    if tracks:
        _log_mediainfo_tracks(tracks)
        _validate_audio_present(meta, tracks)
    track_type, field, success_label, failure_label = _validation_spec(
        settings
    )
    value = _track_value(tracks, track_type, field)
    if not value:
        logger.debug(
            f"[yellow]Mediainfo failed validation ({failure_label})[/yellow]"
        )
        return False
    logger.debug(f"[green]Found {success_label}: {value}[/green]")
    return True


def _general_mediainfo_track(meta: Meta) -> dict[str, Any] | None:
    for track in _meta_mediainfo_tracks(meta):
        if track.get("@type") == "General":
            return track
    return None


async def get_conformance_error(meta: Meta) -> bool:
    if meta.is_disc == "BDMV":
        return False
    general_track = _general_mediainfo_track(meta)
    if general_track is None:
        return False
    extra = general_track.get("extra", {})
    has_errors = isinstance(extra, dict) and bool(
        cast(dict[str, Any], extra).get("ConformanceErrors", {})
    )
    if not has_errors:
        logger.debug(
            "[green]No Conformance errors found in MediaInfo General track[/green]"
        )
    return has_errors
