from __future__ import annotations

import asyncio
import tarfile
import zipfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import mutagen

from src.domain_models.release import Meta
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.media_info_export import export_info

AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".ac3",
        ".aiff",
        ".alac",
        ".ape",
        ".dts",
        ".flac",
        ".m4a",
        ".m4b",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
        ".wv",
    }
)
VIDEO_EXTENSIONS = frozenset(
    {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".ts", ".webm"}
)
ARCHIVE_EXTENSIONS = frozenset(
    {
        ".7z",
        ".bz2",
        ".cbr",
        ".cbz",
        ".gz",
        ".rar",
        ".tar",
        ".tbz",
        ".tbz2",
        ".tgz",
        ".txz",
        ".xz",
        ".zip",
        ".zst",
    }
)


class _MediaTrack(Protocol):
    track_type: str | None
    internet_media_type: str | None


class _MediaInfoResult(Protocol):
    tracks: list[_MediaTrack]


class _AudioInfo(Protocol):
    bitrate: int | None


class _AudioFile(Protocol):
    info: _AudioInfo | None


mutagen_file = cast(Callable[[str], _AudioFile | None], vars(mutagen)["File"])
mutagen_error = cast(type[Exception], vars(mutagen)["MutagenError"])


def _has_symlink_component(path: Path) -> bool:
    absolute = path.expanduser().absolute()
    return any(
        component.is_symlink()
        for component in (*reversed(absolute.parents), absolute)
    )


def _reject_symlink(path: Path) -> None:
    if _has_symlink_component(path):
        raise ValueError("Podcast uploads cannot contain symbolic links")


def _directory_source_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Podcast uploads cannot contain symbolic links")
        if path.is_file():
            candidates.append(path)
    return candidates


def _source_files(root: Path) -> list[Path]:
    _reject_symlink(root)
    if root.is_file():
        return [root]
    return _directory_source_files(root)


def _has_archive_header(header: bytes) -> bool:
    return (
        header.startswith(
            (
                b"PK\x03\x04",
                b"PK\x05\x06",
                b"PK\x07\x08",
                b"Rar!\x1a\x07",
                b"7z\xbc\xaf\x27\x1c",
                b"\x1f\x8b",
                b"\x1f\x9d",
                b"BZh",
                b"\xfd7zXZ\x00",
                b"\x28\xb5\x2f\xfd",
                b"\x04\x22\x4d\x18",
                b"MSCF",
                b"LZIP",
                b"xar!",
                b"!<arch>\n",
            )
        )
        or header[257:262] == b"ustar"
    )


def _has_archive_signature(path: Path) -> bool:
    with path.open("rb") as source:
        header = source.read(512)
    if _has_archive_header(header) or zipfile.is_zipfile(path):
        return True
    try:
        return tarfile.is_tarfile(path)
    except OSError:
        return False


def _has_track_type(tracks: list[_MediaTrack], track_type: str) -> bool:
    return any(track.track_type == track_type for track in tracks)


def _general_content_type(tracks: list[_MediaTrack]) -> str:
    for track in tracks:
        if track.track_type == "General":
            return str(track.internet_media_type or "").casefold()
    return ""


def _kind_from_tracks(tracks: list[_MediaTrack]) -> str | None:
    if _has_track_type(tracks, "Video"):
        return "video"
    general_content_type = _general_content_type(tracks)
    if general_content_type.startswith("video/"):
        return "video"
    if _has_track_type(tracks, "Audio"):
        return "audio"
    if general_content_type.startswith("audio/"):
        return "audio"
    return None


def _detected_media_kind(path: Path) -> str | None:
    try:
        media_info = cast(_MediaInfoResult, MediaInfo.parse(str(path)))
    except OSError, RuntimeError, ValueError:
        return None
    return _kind_from_tracks(media_info.tracks)


def _is_archive(path: Path) -> bool:
    return (
        path.suffix.casefold() in ARCHIVE_EXTENSIONS
        or _has_archive_signature(path)
    )


def _declared_media_kind(path: Path) -> str | None:
    suffix = path.suffix.casefold()
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _validated_media_kind(path: Path) -> str | None:
    declared_kind = _declared_media_kind(path)
    if declared_kind is None:
        return None
    detected_kind = _detected_media_kind(path)
    if detected_kind is None:
        raise ValueError(
            f"Podcast media content could not be identified: {path.name}"
        )
    if detected_kind != declared_kind:
        raise ValueError(
            f"Podcast media extension does not match its actual content: {path.name}"
        )
    return declared_kind


def _append_media_file(
    audio: list[Path], video: list[Path], path: Path, declared_kind: str | None
) -> None:
    if declared_kind == "audio":
        audio.append(path.resolve())
    elif declared_kind == "video":
        video.append(path.resolve())


def _media_files(candidates: list[Path]) -> tuple[list[Path], list[Path]]:
    if any(_is_archive(path) for path in candidates):
        raise ValueError(
            "Podcast uploads cannot contain compressed archive files"
        )
    audio: list[Path] = []
    video: list[Path] = []
    for path in candidates:
        _append_media_file(audio, video, path, _validated_media_kind(path))
    audio.sort(key=str)
    video.sort(key=str)
    return audio, video


def _dominant_extension(files: list[Path]) -> str:
    counts = Counter(path.suffix.lstrip(".").upper() for path in files)
    return counts.most_common(1)[0][0] if counts else ""


def _raw_bitrate(audio: _AudioFile | None) -> int:
    if audio is None or audio.info is None:
        return 0
    return int(audio.info.bitrate or 0)


def _positive_bitrate_kbps(bitrate: int) -> int:
    if bitrate <= 0:
        return 0
    return round(bitrate / 1000)


def _bitrate_kbps(path: Path) -> int:
    try:
        bitrate = _raw_bitrate(mutagen_file(str(path)))
    except OSError, TypeError, ValueError, mutagen_error:
        return 0
    return _positive_bitrate_kbps(bitrate)


def _dominant_bitrate(bitrates: list[int]) -> int | None:
    if not bitrates:
        return None
    counts = Counter(bitrates)
    bitrate, count = counts.most_common(1)[0]
    if count / len(bitrates) < 0.7:
        return None
    return bitrate


def _audio_bitrate(files: list[Path]) -> int | None:
    bitrates = [
        bitrate for path in files if (bitrate := _bitrate_kbps(path)) > 0
    ]
    return _dominant_bitrate(bitrates)


def _fallback_title(root: Path) -> str:
    return root.stem if root.is_file() else root.name


def _podcast_year(meta: Meta) -> str:
    return str(meta.manual_year or meta.year or "").strip()


def _technical_title_detail(
    files: list[Path], audio_bitrate: int | None
) -> str:
    media_format = _dominant_extension(files)
    if not media_format:
        return ""
    if audio_bitrate:
        return f"{media_format} - {audio_bitrate}kbps"
    return media_format


def _title_details(
    meta: Meta, files: list[Path], audio_bitrate: int | None
) -> list[str]:
    details: list[str] = []
    year = _podcast_year(meta)
    if year:
        details.append(year)
    technical = _technical_title_detail(files, audio_bitrate)
    if technical:
        details.append(technical)
    return details


def _generated_title(
    meta: Meta, root: Path, files: list[Path], audio_bitrate: int | None
) -> str:
    title = str(meta.title or _fallback_title(root)).strip()
    details = _title_details(meta, files, audio_bitrate)
    return f"{title} [{'/'.join(details)}]" if details else title


def _validated_podcast_root(meta: Meta) -> Path:
    root = Path(str(meta.path or ""))
    _reject_symlink(root)
    if not root.exists():
        raise ValueError(f"Podcast path does not exist: {root}")
    return root


def _selected_media_files(
    audio_files: list[Path], video_files: list[Path]
) -> list[Path]:
    if audio_files and video_files:
        raise ValueError(
            "Podcast torrents cannot contain mixed audio and video media"
        )
    media_files = audio_files or video_files
    if not media_files:
        raise ValueError(
            "Podcast upload contains no supported audio or video files"
        )
    return media_files


def _torrent_files(source_files: list[Path]) -> list[Path]:
    return sorted((path.resolve() for path in source_files), key=str)


def _reset_external_ids(meta: Meta) -> None:
    meta.tmdb_id = 0
    meta.imdb_id = 0
    meta.tvdb_id = 0
    meta.mal_id = 0
    meta.igdb_id = 0
    meta.tmdb = 0
    meta.imdb = "0"
    meta.tvdb = 0
    meta.mal = 0


def _apply_podcast_media_state(
    meta: Meta,
    root: Path,
    torrent_files: list[Path],
    audio_files: list[Path],
    media_files: list[Path],
) -> None:
    meta.category = "PODCAST"
    meta.filelist = [str(path) for path in torrent_files]
    meta.isdir = root.is_dir()
    meta.keep_folder = root.is_dir()
    _reset_external_ids(meta)
    meta.type = "AUDIO" if audio_files else "VIDEO"
    meta.container = _dominant_extension(media_files).casefold()
    meta.resolution = ""
    meta.sd = 0
    meta.valid_mi = True
    meta.valid_mi_settings = True
    meta.source = "WEB"


def _validated_artwork_path(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    _reject_symlink(path)
    return str(path.resolve())


def _apply_podcast_artwork(meta: Meta) -> None:
    cover = _validated_artwork_path(meta.podcast_cover)
    if cover:
        meta.artwork_path = cover
    banner = _validated_artwork_path(meta.podcast_banner)
    if banner:
        meta.artwork_banner_path = banner


async def _podcast_audio_bitrate(audio_files: list[Path]) -> int | None:
    if not audio_files:
        return None
    return await asyncio.to_thread(_audio_bitrate, audio_files)


async def _apply_podcast_mediainfo(
    meta: Meta, media_files: list[Path]
) -> None:
    primary = max(media_files, key=lambda path: path.stat().st_size)
    meta.mediainfo = await export_info(
        str(primary), meta.isdir, meta.uuid, meta.base_dir, is_dvd=False
    )


def _apply_podcast_title(
    meta: Meta, root: Path, media_files: list[Path]
) -> None:
    final_title = str(
        meta.podcast_title
        or _generated_title(meta, root, media_files, meta.audio_bitrate)
    ).strip()
    meta.title = meta.title or _fallback_title(root)
    meta.name_notag = final_title
    meta.name = final_title
    meta.clean_name = final_title
    meta.search_year = ""


async def gather_podcast_prep(meta: Meta) -> None:
    root = _validated_podcast_root(meta)
    source_files = await asyncio.to_thread(_source_files, root)
    audio_files, video_files = await asyncio.to_thread(
        _media_files, source_files
    )
    media_files = _selected_media_files(audio_files, video_files)
    _apply_podcast_media_state(
        meta, root, _torrent_files(source_files), audio_files, media_files
    )
    meta.audio_bitrate = await _podcast_audio_bitrate(audio_files)
    _apply_podcast_artwork(meta)
    await _apply_podcast_mediainfo(meta, media_files)
    _apply_podcast_title(meta, root, media_files)
