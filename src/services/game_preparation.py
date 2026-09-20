# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import datetime
import html
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import httpx

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.integrations.cache.metadata_cache import cache_for, is_cache_miss
from src.integrations.external_apis.igdb import IGDBAPI
from src.integrations.observability.runtime_support import logger

_VERSION_PATTERN = r"\d+(?:[.\-]\d+)*(?:[a-z]\d*)?"


def normalize_version(version_str: str) -> str:
    version_str = version_str.strip()
    if not version_str:
        return ""
    if version_str.lower().startswith("v"):
        return "v" + version_str[1:]
    if version_str[0].isdigit():
        return "v" + version_str
    return version_str


def _is_scene_group_suffix(suffix: str) -> bool:
    if len(suffix) >= 15 or re.search(r"\s", suffix):
        return False
    return suffix.isupper() or suffix.isdigit()


def _strip_scene_group(title: str) -> str:
    if "-" not in title:
        return title
    prefix, suffix = title.rsplit("-", 1)
    return prefix if _is_scene_group_suffix(suffix) else title


def _clean_game_title_text(title: str) -> str:
    substitutions = (
        (r"(?i)\.(?:dmg|pkg|iso|rar|zip|7z)$", ""),
        (r"\[[^\]]+\]$", ""),
        (
            rf"(?i)(?<![A-Za-z0-9]){_VERSION_PATTERN}\s+(?:(?:incl(?:uded)?|with)\s+)?(?:keygen|crack(?:ed)?|serial)\b.*",
            "",
        ),
        (r"(?<![A-Za-z0-9])\d+(?:\.\d+){1,3}\s*$", ""),
        (r"[._-]+", " "),
        (r"(?i)\b(?:update|patch|build|version)\b.*", ""),
        (rf"(?i)\bv{_VERSION_PATTERN}\b.*", ""),
        (r"\b(?:19|20)\d{6}\b\s*$", ""),
        (r"(?i)\b[a-z]{2}(?:US|GB|CA|AU|BR|DE|ES|FR|IT|JP)\b\s*$", ""),
    )
    for pattern, replacement in substitutions:
        title = re.sub(pattern, replacement, title).strip(" ._-")
    return re.sub(r"\s+", " ", title).strip()


def clean_game_title(value: str) -> str:
    title = Path(str(value or "")).name
    title = re.sub(r"(?i)\.(?:dmg|pkg|iso|rar|zip|7z)$", "", title)
    title = re.sub(r"\[[^\]]+\]$", "", title).strip(" ._-")
    title = _strip_scene_group(title)
    title = _clean_game_title_text(title)
    return title.title() if title and title == title.lower() else title


def extract_release_group(value: str) -> str:
    release_name = re.sub(
        r"(?i)\.(?:dmg|pkg|iso|rar|zip|7z)$", "", Path(str(value or "")).name
    )
    bracket_match = re.search(r"\[([A-Za-z0-9]+)\]$", release_name)
    if bracket_match:
        return bracket_match.group(1)
    dash_match = re.search(r"-\s*([A-Za-z0-9]+)$", release_name)
    return dash_match.group(1) if dash_match else ""


def required_game_fields(meta: Meta) -> list[str]:
    fields = ["title", "platform"]
    if not meta.software:
        fields.insert(1, "year")
    return fields


def _missing_required_game_fields(meta: Meta) -> list[str]:
    return [
        field
        for field in required_game_fields(meta)
        if not str(getattr(meta, field, "") or "").strip()
    ]


def _software_field_values(meta: Meta) -> dict[str, Any]:
    return {
        "game_version": meta.game_version,
        "developer": meta.developer,
        "publisher": meta.publisher,
        "cover": meta.artwork_path or meta.artwork_url,
        "languages": meta.languages,
        "overview": meta.overview,
        "installation instructions": meta.software_notes,
    }


def missing_game_fields(meta: Meta) -> list[str]:
    missing = _missing_required_game_fields(meta)
    if not meta.software:
        return missing
    missing.extend(
        label
        for label, value in _software_field_values(meta).items()
        if not value
    )
    return missing


def _desktop_platform(meta: Meta) -> bool:
    return str(meta.platform or "").upper() in {"PC", "MAC", "LINUX"}


def _installer_paths(meta: Meta) -> list[str]:
    paths = [str(item) for item in meta.filelist]
    if meta.path:
        paths.append(str(meta.path))
    return paths


def _is_desktop_installer(meta: Meta) -> bool:
    if meta.console_game or not _desktop_platform(meta):
        return False
    return any(
        Path(path).suffix.lower() in {".dmg", ".exe", ".msi", ".pkg"}
        for path in _installer_paths(meta)
    )


def _software_notes_candidate(path: Path) -> bool:
    return path.suffix.lower() in {".md", ".nfo", ".txt"} and path.is_file()


async def _read_small_text_file(path: Path) -> str:
    try:
        if path.stat().st_size > 64 * 1024:
            return ""
        async with aiofiles.open(
            path, encoding="utf-8", errors="replace"
        ) as handle:
            return (await handle.read()).strip()
    except OSError:
        return ""


def _nfo_installation_instructions(notes: str) -> str:
    instructions: list[str] = []
    action_pattern = re.compile(
        r"\b(?:extract|burn|mount|run|setup|install|copy|crack|play|usage)\b",
        re.IGNORECASE,
    )
    for line in notes.splitlines():
        match = re.search(r"(\d+\.\s+.+)", line)
        if match is None:
            continue
        instruction = match.group(1).rstrip(" \t|│║�")
        if action_pattern.search(instruction):
            instructions.append(instruction)
    return "\n".join(instructions)


async def _software_notes_for_path(path: Path) -> str:
    if not _software_notes_candidate(path):
        return ""
    notes = await _read_small_text_file(path)
    if not notes:
        return ""
    if path.suffix.lower() != ".nfo":
        return notes
    return _nfo_installation_instructions(notes)


async def _read_software_notes(meta: Meta) -> str:
    for item in meta.filelist:
        notes = await _software_notes_for_path(Path(str(item)))
        if notes:
            return notes
    return ""


def extract_version_from_text(text: str) -> str | None:
    if not text:
        return None

    # 1. Match version/update/build prefixes:
    m = re.search(
        rf"(?i)(?<![a-zA-Z0-9])(?:update|version|ver|build)[.:=\-_\s]*[vV]?({_VERSION_PATTERN})(?![a-zA-Z0-9])",
        text,
    )
    if m:
        return normalize_version(m.group(1))

    m = re.search(
        rf"(?i)(?<![a-zA-Z0-9])[vV]({_VERSION_PATTERN})(?![a-zA-Z0-9])", text
    )
    if m:
        return normalize_version(m.group(1))

    # 2. Match isolated version numbers:
    for m in re.finditer(
        r"(?i)(?<![a-zA-Z0-9])(\d+(?:[.\-]\d+)+)(?![a-zA-Z0-9])", text
    ):
        candidate = m.group(1)
        return normalize_version(candidate)

    return None


def _read_nfo_text(nfo_path: str) -> str:
    path = Path(nfo_path)
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(encoding="latin-1")
        except Exception:
            return ""


def _prefixed_version_from_lines(lines: list[str]) -> str | None:
    pattern = re.compile(
        rf"(?i)(?<![a-zA-Z0-9])(?:update|version|ver|build)[.:=\-_\s]*[vV]?({_VERSION_PATTERN})(?![a-zA-Z0-9])"
    )
    for line in lines:
        match = pattern.search(line)
        if match:
            return normalize_version(match.group(1))
    return None


def _v_version_from_lines(lines: list[str]) -> str | None:
    pattern = re.compile(
        rf"(?i)(?<![a-zA-Z0-9])[vV]({_VERSION_PATTERN})(?![a-zA-Z0-9])"
    )
    for line in lines:
        match = pattern.search(line)
        if match:
            return normalize_version(match.group(0))
    return None


def _context_version_from_lines(lines: list[str]) -> str | None:
    pattern = re.compile(
        r"(?i)(?<![a-zA-Z0-9])(\d+(?:[.\-]\d+)+)(?![a-zA-Z0-9])"
    )
    context_words = ("version", "ver", "build", "v.", "v ")
    for line in lines:
        if not any(word in line.lower() for word in context_words):
            continue
        match = pattern.search(line)
        if match:
            return normalize_version(match.group(1))
    return None


def extract_version_from_nfo(nfo_path: str) -> str | None:
    lines = _read_nfo_text(nfo_path).splitlines()
    if not lines:
        return None
    return (
        _prefixed_version_from_lines(lines)
        or _v_version_from_lines(lines)
        or _context_version_from_lines(lines)
    )


def map_to_clean_code(p_name: str) -> str:
    p_name_norm = p_name.lower()
    nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
    reverse_map = {
        "playstation 5": "PS5",
        "playstation 4": "PS4",
        "playstation 3": "PS3",
        "playstation 2": "PS2",
        "playstation": "PS1",
        "xbox series": "XSX",
        "xbox series x|s": "XSX",
        "xbox series x/s": "XSX",
        "xbox one": "XONE",
        "xbox 360": "X360",
        "xbox": "XBOX",
        "switch": "SWITCH",
        f"{nin_term} switch": "SWITCH",
        "3ds": "3DS",
        f"{nin_term} 3ds": "3DS",
        "nds": "NDS",
        f"{nin_term} ds": "NDS",
        "wii u": "WIIU",
        "wiiu": "WIIU",
        "wii": "WII",
        "pc": "PC",
        "windows": "PC",
        "mac": "MAC",
        "linux": "LINUX",
    }
    for key, val in reverse_map.items():
        if key in p_name_norm or p_name_norm in key:
            return val
    return p_name.upper()


def _configured_7z_path(config: dict[str, Any] | None) -> str | None:
    if not isinstance(config, dict):
        return None
    defaults = config.get("DEFAULT", {})
    if not isinstance(defaults, dict):
        return None
    defaults_map = cast(dict[str, Any], defaults)
    value = str(defaults_map.get("7z_path", "")).strip()
    return value if value and Path(value).exists() else None


def _system_7z_path() -> str | None:
    return shutil.which("7z") or shutil.which("7z.exe")


async def _managed_7z_path(base_dir: str) -> str | None:
    try:
        from src.integrations.runtime_tools.seven_zip import (
            SevenZipBinaryManager,
        )

        binary_path = await SevenZipBinaryManager.ensure_7z_binary(base_dir)
    except Exception:
        return None
    if binary_path and Path(binary_path).exists():
        return binary_path
    return None


async def get_7z_path(
    base_dir: str, config: dict[str, Any] | None = None
) -> str | None:
    configured = _configured_7z_path(config)
    if configured:
        return configured
    system = _system_7z_path()
    if system:
        return system
    return await _managed_7z_path(base_dir)


def _archive_listing_path(line: str, archive_normalized: str) -> str | None:
    if not line.startswith("Path = ") or len(line) <= 7:
        return None
    path_value = line[7:].strip()
    if not path_value:
        return None
    if path_value.replace("\\", "/").lower() == archive_normalized:
        return None
    return path_value


def _archive_listing_paths(stdout: bytes, archive_path: str) -> list[str]:
    archive_normalized = archive_path.replace("\\", "/").lower()
    contents: list[str] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        path_value = _archive_listing_path(line, archive_normalized)
        if path_value is not None:
            contents.append(path_value)
    return contents


async def list_archive_contents_with_7z(
    archive_path: str, binary_path: str
) -> list[str]:
    try:
        command = [binary_path, "l", "-slt", archive_path]
        process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
    except Exception as error:
        logger.debug(
            f"[yellow]7-Zip: Failed to list archive contents for {archive_path}: {error}[/yellow]"
        )
        return []
    if process.returncode != 0:
        return []
    return _archive_listing_paths(stdout, archive_path)


_EARLY_PLATFORM_EXTENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SWITCH", (".nsp", ".xci", ".nca", ".szs", ".nsz", ".xcz")),
    ("3DS", (".3ds", ".cia", ".cci", ".3dsx")),
    ("NDS", (".nds", ".srl")),
    ("WIIU", (".wud", ".wux")),
    ("WII", (".wbfs", ".gcm")),
)
_PC_DLLS = frozenset(
    {"steam_api.dll", "steam_api64.dll", "unityplayer.dll", "galaxy64.dll"}
)
_PORTABLE_PS_EXTENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PSVITA", (".vpk",)),
    ("PSP", (".cso",)),
)
_XBOX_EXTENSION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("X360", (".xex",)),
    ("XBOX", (".xbe",)),
)
_ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".iso", ".tar", ".gz")
_PC_PATH_MARKERS = ("binaries/win64", "binaries/win32", "engine/binaries")


def _normalized_platform_paths(
    filelist: list[str],
) -> tuple[list[str], list[str]]:
    files = [value.replace("\\", "/").lower() for value in filelist]
    return files, [Path(value).name for value in files]


def _platform_by_extension(
    basenames: list[str],
    rules: tuple[tuple[str, tuple[str, ...]], ...],
) -> str | None:
    for platform, extensions in rules:
        if any(name.endswith(extensions) for name in basenames):
            return platform
    return None


def _archive_candidates(filelist: list[str]) -> list[str]:
    return [
        value
        for value in filelist
        if value.lower().endswith(_ARCHIVE_EXTENSIONS)
    ]


def _archive_supported_by_binary(archive: str, binary_path: str) -> bool:
    if "7zr" not in Path(binary_path).name.lower():
        return True
    return archive.lower().endswith(".7z")


async def _platform_paths_from_archive(
    archive: str, binary_path: str
) -> list[str]:
    if not _archive_supported_by_binary(archive, binary_path):
        return []
    logger.info(
        f"[cyan]7-Zip: Inspecting contents of archive/ISO: {Path(archive).name}...[/cyan]"
    )
    contents = await list_archive_contents_with_7z(archive, binary_path)
    return [value.replace("\\", "/").lower() for value in contents]


async def _archive_platform_paths(
    filelist: list[str],
    base_dir: str | None,
    config: dict[str, Any] | None,
) -> list[str]:
    if not base_dir:
        return []
    archives = _archive_candidates(filelist)
    if not archives:
        return []
    binary_path = await get_7z_path(base_dir, config)
    if not binary_path:
        return []
    contents: list[str] = []
    for archive in archives:
        contents.extend(
            await _platform_paths_from_archive(archive, binary_path)
        )
    return contents


def _wiiu_structure_platform(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    if any("/code/app.xml" in value for value in files_lower):
        return "WIIU"
    has_tmd = "title.tmd" in basenames_lower
    has_ticket = "title.tik" in basenames_lower
    return "WIIU" if has_tmd and has_ticket else None


def _ps3_structure_platform(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    if any("ps3_game" in value for value in files_lower):
        return "PS3"
    if any(name.endswith(".rap") for name in basenames_lower):
        return "PS3"
    return None


def _portable_playstation_platform(basenames_lower: list[str]) -> str | None:
    platform = _platform_by_extension(
        basenames_lower, _PORTABLE_PS_EXTENSION_RULES
    )
    if platform:
        return platform
    return "PSP" if "eboot.pbp" in basenames_lower else None


def _pkg_path_platform(files_lower: list[str]) -> str | None:
    all_paths = " ".join(files_lower)
    hints = (
        ("PS4", ("ps4", "playstation 4")),
        ("PS5", ("ps5", "playstation 5")),
        ("PS3", ("ps3", "playstation 3")),
    )
    for platform, markers in hints:
        if any(marker in all_paths for marker in markers):
            return platform
    return None


def _first_pkg_marker_platform(pkg_files: list[str]) -> str | None:
    markers = (("PS4", "cusa"), ("PS5", "ppsa"))
    for platform, marker in markers:
        if any(marker in pkg for pkg in pkg_files):
            return platform
    return None


def _pkg_product_platform(pkg_files: list[str]) -> str | None:
    marker_platform = _first_pkg_marker_platform(pkg_files)
    if marker_platform:
        return marker_platform
    pattern = re.compile(
        r"(?:^|[^a-z0-9])(?:np|bl|bc)[a-z]{2}\d{5}(?:[^a-z0-9]|$)"
    )
    return "PS3" if any(pattern.search(pkg) for pkg in pkg_files) else None


def _first_platform_choice(choices: tuple[str | None, ...]) -> str | None:
    for choice in choices:
        if choice:
            return choice
    return None


def _pkg_platform(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    pkg_files = [name for name in basenames_lower if name.endswith(".pkg")]
    if not pkg_files:
        return None
    return _first_platform_choice(
        (
            _pkg_product_platform(pkg_files),
            _pkg_path_platform(files_lower),
            "MAC",
        )
    )


def _playstation_platform(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    detectors = (
        lambda: _ps3_structure_platform(files_lower, basenames_lower),
        lambda: _portable_playstation_platform(basenames_lower),
        lambda: _pkg_platform(files_lower, basenames_lower),
    )
    for detector in detectors:
        platform = detector()
        if platform:
            return platform
    return None


def _xbox_platform(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    extension_platform = _platform_by_extension(
        basenames_lower, _XBOX_EXTENSION_RULES
    )
    if extension_platform == "X360":
        return extension_platform
    if any("$systemupdate" in value for value in files_lower):
        return "X360"
    return extension_platform


def _dreamcast_platform(basenames_lower: list[str]) -> str | None:
    return (
        "DREAMCAST"
        if any(name.endswith((".gdi", ".cdi")) for name in basenames_lower)
        else None
    )


def _contains_any_path_marker(
    files_lower: list[str], markers: tuple[str, ...]
) -> bool:
    return any(marker in value for value in files_lower for marker in markers)


def _contains_pc_dll(basenames_lower: list[str]) -> bool:
    return any(name in _PC_DLLS for name in basenames_lower)


def _contains_pc_installer(basenames_lower: list[str]) -> bool:
    return any(name.endswith((".exe", ".msi")) for name in basenames_lower)


def _contains_dmg(basenames_lower: list[str]) -> bool:
    return any(name.endswith(".dmg") for name in basenames_lower)


def _desktop_platform_from_files(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    evidence = (
        _contains_pc_dll(basenames_lower),
        _contains_any_path_marker(files_lower, _PC_PATH_MARKERS),
        _contains_pc_installer(basenames_lower),
    )
    if any(evidence):
        return "PC"
    return "MAC" if _contains_dmg(basenames_lower) else None


def _platform_keyword_mapping() -> dict[str, list[str]]:
    nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
    return {
        "PS5": ["ps5", "playstation 5", "playstation5"],
        "PS4": ["ps4", "playstation 4", "playstation4"],
        "PS3": ["ps3", "playstation 3", "playstation3"],
        "PS2": ["ps2", "playstation 2", "playstation2"],
        "PS1": ["ps1", "psx", "playstation 1", "playstation1"],
        "XSX": ["xsx", "xbox series", "xboxseries"],
        "XONE": ["xone", "xbox one", "xboxone"],
        "X360": ["x360", "xbox 360", "xbox360"],
        "XBOX": ["xbox"],
        "SWITCH": ["nsw", "switch", f"{nin_term} switch", f"{nin_term}switch"],
        "3DS": ["3ds", f"{nin_term} 3ds"],
        "NDS": ["nds", f"{nin_term} ds", f"{nin_term}ds"],
        "WIIU": ["wii u", "wiiu"],
        "WII": ["wii"],
        "PC": ["pc", "windows", "win"],
        "MAC": ["mac", "macos", "osx"],
        "LINUX": ["linux"],
        "PSP": ["psp"],
        "PSVITA": ["psvita", "ps vita", "vita"],
    }


def _normalized_platform_basename(path: str) -> str:
    basename = Path(path).name.lower()
    return re.sub(r"[.\-_\[\]]+", " ", basename)


def _keyword_platform(path_to_search: str) -> str | None:
    if not path_to_search:
        return None
    basename = _normalized_platform_basename(path_to_search)
    for platform_code, keywords in _platform_keyword_mapping().items():
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", basename)
            for keyword in keywords
        ):
            return platform_code
    return None


def _detected_platform_from_paths(
    files_lower: list[str], basenames_lower: list[str]
) -> str | None:
    early = _platform_by_extension(
        basenames_lower, _EARLY_PLATFORM_EXTENSION_RULES
    )
    if early:
        return early
    wiiu = _wiiu_structure_platform(files_lower, basenames_lower)
    if wiiu:
        return wiiu
    detectors = (
        lambda: _playstation_platform(files_lower, basenames_lower),
        lambda: _xbox_platform(files_lower, basenames_lower),
        lambda: _dreamcast_platform(basenames_lower),
        lambda: _desktop_platform_from_files(files_lower, basenames_lower),
    )
    for detector in detectors:
        platform = detector()
        if platform:
            return platform
    return None


def _extend_platform_paths(
    files_lower: list[str],
    basenames_lower: list[str],
    archive_paths: list[str],
) -> None:
    files_lower.extend(archive_paths)
    basenames_lower.extend(Path(value).name for value in archive_paths)


def _platform_search_path(
    path_to_check: str | None, filelist: list[str]
) -> str:
    if path_to_check:
        return path_to_check
    return filelist[0] if filelist else ""


async def detect_platform_from_files(
    filelist: list[str],
    path_to_check: str | None = None,
    base_dir: str | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    files_lower, basenames_lower = _normalized_platform_paths(filelist)
    archive_paths = await _archive_platform_paths(filelist, base_dir, config)
    _extend_platform_paths(files_lower, basenames_lower, archive_paths)
    platform = _detected_platform_from_paths(files_lower, basenames_lower)
    if platform:
        return platform
    return _keyword_platform(_platform_search_path(path_to_check, filelist))


def _game_directory_files(videoloc: str) -> list[str]:
    return sorted(
        str((Path(root) / filename).resolve())
        for root, _dirs, files in os.walk(videoloc)
        for filename in files
    )


def _largest_file(paths: list[str]) -> str:
    return max(paths, key=os.path.getsize)


def _candidate_files(
    filelist: list[str], suffixes: tuple[str, ...]
) -> list[str]:
    return [path for path in filelist if path.lower().endswith(suffixes)]


def _primary_game_file(filelist: list[str]) -> str:
    priority_groups = (
        _candidate_files(filelist, (".exe",)),
        _candidate_files(filelist, (".iso",)),
        _candidate_files(filelist, (".rar", ".zip", ".7z", ".tar", ".gz")),
    )
    for candidates in priority_groups:
        if candidates:
            return _largest_file(candidates)
    return _largest_file(filelist)


def _ordered_game_filelist(filelist: list[str], videopath: str) -> list[str]:
    if videopath not in filelist:
        return filelist
    return [videopath, *(item for item in filelist if item != videopath)]


def _resolved_game_files(videoloc: str) -> tuple[str, list[str]]:
    if not Path(videoloc).is_dir():
        return videoloc, [videoloc]
    filelist = _game_directory_files(videoloc)
    if not filelist:
        logger.info("[bold red]No game files found!")
        raise ItemProcessingError(
            "No game files were found in the selected path.", str(videoloc)
        )
    videopath = _primary_game_file(filelist)
    return videopath, _ordered_game_filelist(filelist, videopath)


def resolve_game_filelist(
    meta: Meta,
    videoloc: str,
) -> tuple[str, list[str], str, str]:
    """Scan *videoloc* and select the primary game file by stable priority."""
    videopath, filelist = _resolved_game_files(videoloc)
    meta.filelist = filelist
    meta.imdb_id = 0
    search_term = Path(filelist[0]).name if filelist else ""
    return videopath, filelist, search_term, "file"


# ---------------------------------------------------------------------------
# Metadata gathering
# ---------------------------------------------------------------------------


def _initialize_game_meta(meta: Meta) -> None:
    meta.category = "GAME"
    meta.search_year = ""
    meta.resolution = "Other"
    meta.hfr = False
    meta.sd = 0
    meta.valid_mi_settings = True


def _local_game_nfo_files(meta: Meta) -> list[Path]:
    return [
        Path(str(item))
        for item in meta.filelist
        if Path(str(item)).suffix.lower() == ".nfo"
        and Path(str(item)).is_file()
    ]


async def _apply_local_game_nfo(meta: Meta) -> None:
    nfo_files = _local_game_nfo_files(meta)
    if not nfo_files:
        return
    if not meta.scene_nfo_file:
        meta.scene_nfo_file = str(nfo_files[0])
    if not meta.software_notes:
        meta.software_notes = await _read_software_notes(meta)


def _game_title_source(meta: Meta, videopath: str) -> str:
    return str(meta.path or videopath or meta.filename or meta.title or "")


def _apply_fallback_game_identity(meta: Meta, title_source: str) -> None:
    fallback_title = clean_game_title(title_source)
    if fallback_title:
        meta.title = fallback_title
    if meta.tag:
        return
    release_group = extract_release_group(title_source)
    if release_group:
        meta.tag = f"-{release_group}"


def _game_cli_overrides(meta: Meta) -> dict[str, bool]:
    return {
        "title": False,
        "year": bool("manual_year" in meta and meta.manual_year),
        "platform": bool(meta.manual_platform),
    }


async def _apply_detected_game_platform(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None,
    manual_platform: bool,
) -> None:
    if manual_platform:
        return
    detected = await detect_platform_from_files(
        meta.filelist, meta.path or videopath, base_dir, config
    )
    if not detected:
        return
    meta.platform = detected
    logger.info(
        f"[green]Game platform auto-detected from files: {detected}[/green]"
    )


def _console_platform_words() -> tuple[str, ...]:
    nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
    return (
        "ps",
        "playstation",
        "xbox",
        "switch",
        "3ds",
        "nds",
        "wii",
        nin_term,
    )


def _platform_is_console(platform: Any) -> bool:
    if not platform:
        return False
    platform_lower = str(platform).lower()
    return any(word in platform_lower for word in _console_platform_words())


def _refresh_console_game(meta: Meta) -> None:
    meta.console_game = _platform_is_console(meta.platform)


def _game_search_directory(path_to_check: str | None) -> str:
    if not path_to_check:
        return ""
    path = Path(path_to_check)
    return str(path if path.is_dir() else path.parent)


def _searchable_game_directory(search_dir: str) -> Path | None:
    if not search_dir:
        return None
    path = Path(search_dir)
    return path if path.is_dir() else None


def _directory_nfo_files(search_dir: str) -> list[str]:
    directory = _searchable_game_directory(search_dir)
    if directory is None:
        return []
    try:
        entries = list(directory.iterdir())
    except Exception:
        return []
    return [
        str(path.resolve())
        for path in entries
        if path.name.lower().endswith(".nfo")
    ]


def _game_nfo_candidates(meta: Meta, path_to_check: str | None) -> list[str]:
    nfo_files = [
        str(item)
        for item in meta.filelist
        if str(item).lower().endswith(".nfo")
    ]
    for candidate in _directory_nfo_files(
        _game_search_directory(path_to_check)
    ):
        if candidate not in nfo_files:
            nfo_files.append(candidate)
    return nfo_files


def _version_from_game_directory(
    path_to_check: str | None, debug: bool
) -> str | None:
    search_dir = _game_search_directory(path_to_check)
    if not search_dir:
        return None
    version = extract_version_from_text(Path(search_dir).name)
    if version and debug:
        logger.info(
            f"[green]Game version extracted from directory name: {version}[/green]"
        )
    return version


def _version_from_nfo_candidates(nfo_files: list[str]) -> str | None:
    for nfo_path in nfo_files:
        version = extract_version_from_nfo(nfo_path)
        if not version:
            continue
        logger.info(
            f"[green]Game version extracted from NFO file ({Path(nfo_path).name}): {version}[/green]"
        )
        return version
    return None


def _detected_game_version(
    meta: Meta, path_to_check: str | None
) -> str | None:
    if meta.game_version:
        version = normalize_version(meta.game_version)
        logger.info(
            f"[green]Game version (manual override): {version}[/green]"
        )
        return version
    version = (
        extract_version_from_text(Path(path_to_check).name)
        if path_to_check
        else None
    )
    if version:
        return version
    version = _version_from_game_directory(path_to_check, meta.debug)
    if version:
        return version
    return _version_from_nfo_candidates(
        _game_nfo_candidates(meta, path_to_check)
    )


def _apply_game_version(meta: Meta, path_to_check: str | None) -> None:
    version = _detected_game_version(meta, path_to_check)
    if version:
        meta.game_version = version


def _igdb_credentials(
    config: dict[str, Any] | None,
) -> tuple[str, str]:
    defaults: dict[str, Any] = {}
    if isinstance(config, dict):
        value = config.get("DEFAULT", {})
        if isinstance(value, dict):
            defaults = cast(dict[str, Any], value)
    client_id = str(defaults.get("twitch_client_id", "")).strip()
    client_secret = str(defaults.get("twitch_client_secret", "")).strip()
    if not client_id:
        client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    if not client_secret:
        client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    return client_id, client_secret


_IGDB_TRAILING_TAGS = (
    r"(?:nsw|switch|nds|3ds|ps1|ps2|ps3|ps4|ps5|psp|psvita|vita|wii|wiiu|xbox|xbox360|x360|xone|xsx|pc|"
    r"multi\d*|eng|english|fre|french|ger|german|spa|spanish|ita|italian|jpn|japanese|por|portuguese|bra|brazilian|"
    r"proper|repack|readnfo|internal|dvd|rip|iso|god|jb|psn|eshop|rip|cracked|unlocked|crackfix)"
)


def _strip_igdb_trailing_tags(query: str) -> str:
    while True:
        new_query = re.sub(rf"(?i)\s+{_IGDB_TRAILING_TAGS}$", "", query)
        if new_query == query:
            return query
        query = new_query


def _igdb_title_query(title_source: str) -> str:
    query = clean_game_title(title_source)
    if not query:
        return ""
    query = query.replace(".", " ").replace("_", " ").replace("-", " ")
    query = re.sub(r"\s+", " ", query).strip()
    query = re.sub(r"(?i)\b(?:update|patch|build|version)\b.*", "", query)
    query = re.sub(r"(?i)\bv\d+.*", "", query)
    query = _strip_igdb_trailing_tags(query)
    return re.sub(r"\s+", " ", query).strip()


async def _read_game_nfo_content(nfo_path: str) -> str:
    try:
        async with aiofiles.open(nfo_path, encoding="utf-8") as handle:
            return await handle.read()
    except Exception:
        try:
            async with aiofiles.open(nfo_path, encoding="latin-1") as handle:
                return await handle.read()
        except Exception:
            raise


def _steam_id_from_nfo_content(content: str) -> str | None:
    match = re.search(r"store\.steampowered\.com/app/(\d+)", content)
    return match.group(1) if match else None


async def _detected_steam_id(
    meta: Meta, path_to_check: str | None
) -> str | None:
    for nfo_path in _game_nfo_candidates(meta, path_to_check):
        try:
            content = await _read_game_nfo_content(nfo_path)
        except Exception as error:
            logger.debug(
                f"[yellow]Debug: Error reading NFO {nfo_path}: {error}[/yellow]"
            )
            continue
        steam_id = _steam_id_from_nfo_content(content)
        if steam_id:
            logger.debug(
                f"[green]Auto-detected Steam ID {steam_id} from NFO file.[/green]"
            )
            return steam_id
    return None


async def _apply_detected_steam_manual(
    meta: Meta, path_to_check: str | None
) -> str | int | None:
    if meta.steam_manual or meta.igdb_manual:
        return meta.steam_manual
    detected = await _detected_steam_id(meta, path_to_check)
    if detected:
        meta.steam_manual = detected
        return detected
    return meta.steam_manual


async def _manual_igdb_selection(
    igdb: IGDBAPI, igdb_manual: Any, steam_manual: Any
) -> dict[str, Any] | None:
    if igdb_manual:
        logger.info(
            f"[cyan]Fetching IGDB metadata for ID: {igdb_manual}...[/cyan]"
        )
        selected = await igdb.fetch_game_by_id(igdb_manual)
        if not selected:
            logger.info(
                f"[yellow]IGDB: No game found with manual ID '{igdb_manual}'. Falling back to search.[/yellow]"
            )
        return selected
    if steam_manual:
        selected = await igdb.fetch_game_by_steam_id(steam_manual)
        if not selected:
            logger.info(
                f"[yellow]IGDB: No game found with Steam ID '{steam_manual}'. Falling back to search.[/yellow]"
            )
        return selected
    return None


def _igdb_result_platform_codes(result: dict[str, Any]) -> list[str]:
    raw_platforms = [
        platform.get("name")
        for platform in result.get("platforms", [])
        if platform.get("name")
    ]
    return [map_to_clean_code(platform) for platform in raw_platforms]


def _sort_igdb_results_for_platform(
    results: list[dict[str, Any]], platform: Any
) -> list[dict[str, Any]]:
    if not platform:
        return results
    target = str(platform).upper().strip()
    matching: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for result in results:
        destination = (
            matching
            if target in _igdb_result_platform_codes(result)
            else others
        )
        destination.append(result)
    return [*matching, *others]


def _igdb_choice_label(result: dict[str, Any]) -> str:
    release_date = result.get("first_release_date")
    year = ""
    if release_date:
        value = datetime.datetime.fromtimestamp(
            release_date, datetime.UTC
        ).year
        year = f" ({value})"
    platforms = [
        platform.get("name")
        for platform in result.get("platforms", [])
        if platform.get("name")
    ]
    platforms_str = f" [{', '.join(platforms)}]" if platforms else ""
    return f"{result.get('name')}{year}{platforms_str}"


def _igdb_skip_choice(choice: str) -> bool:
    return choice in {
        "Skip - Don't use any of these matches",
        "Skip - Don't select any match",
    }


def _automatic_igdb_result(
    results: list[dict[str, Any]], meta: Meta
) -> dict[str, Any] | None:
    if len(results) == 1 or meta.unattended:
        return results[0]
    return None


async def _prompt_igdb_result(
    results: list[dict[str, Any]], meta: Meta
) -> dict[str, Any] | None:
    automatic = _automatic_igdb_result(results, meta)
    if automatic is not None:
        return automatic
    choices = [_igdb_choice_label(result) for result in results]
    choices.append("Skip - Don't select any match")
    try:
        choice = cli_ui.ask_choice(
            "Select the correct game from IGDB:", choices=choices
        )
    except KeyboardInterrupt:
        logger.info(
            "[yellow]Selection cancelled. Skipping IGDB metadata.[/yellow]"
        )
        return None
    if _igdb_skip_choice(choice):
        return None
    return results[choices.index(choice)]


async def _handle_no_igdb_results(meta: Meta, title_query: str) -> None:
    meta.software = _is_desktop_installer(meta)
    logger.info(
        f"[yellow]IGDB: No games found matching '{title_query}'[/yellow]"
    )
    if not meta.software:
        return
    meta.software_notes = await _read_software_notes(meta)
    logger.info(
        "[green]Desktop software package detected; game-only metadata requirements will not be applied.[/green]"
    )


async def _resolve_igdb_game(
    meta: Meta,
    igdb: IGDBAPI,
    title_query: str,
    path_to_check: str | None,
) -> tuple[dict[str, Any] | None, bool]:
    steam_manual = await _apply_detected_steam_manual(meta, path_to_check)
    selected = await _manual_igdb_selection(
        igdb, meta.igdb_manual, steam_manual
    )
    if selected:
        return selected, True
    results = await igdb.search_game(title_query)
    if not results:
        await _handle_no_igdb_results(meta, title_query)
        return None, False
    sorted_results = _sort_igdb_results_for_platform(results, meta.platform)
    return await _prompt_igdb_result(sorted_results, meta), True


def _apply_igdb_identity(
    meta: Meta,
    selected_game: dict[str, Any],
    cli_overrides: dict[str, bool],
) -> None:
    name = selected_game.get("name")
    if name and not cli_overrides["title"]:
        meta.title = name
    release_date = selected_game.get("first_release_date")
    if not release_date or cli_overrides["year"]:
        return
    dt = datetime.datetime.fromtimestamp(release_date, datetime.UTC)
    meta.year = dt.year
    meta.search_year = dt.year
    meta.igdb_first_release_date = dt.strftime("%d/%m/%Y")


def _apply_igdb_rating(meta: Meta, selected_game: dict[str, Any]) -> None:
    rating = selected_game.get("rating")
    rating_count = selected_game.get("rating_count")
    if rating is not None:
        meta.igdb_rating = round(float(rating), 1)
    if rating_count is not None:
        meta.igdb_rating_count = int(rating_count)


def _apply_igdb_overview(meta: Meta, selected_game: dict[str, Any]) -> None:
    overview = (
        selected_game.get("summary") or selected_game.get("storyline") or ""
    )
    if overview:
        meta.overview = overview


def _normalized_igdb_image_url(value: Any, size: str) -> str:
    if not value:
        return ""
    url = str(value)
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("t_thumb", size)


def _apply_igdb_cover(meta: Meta, selected_game: dict[str, Any]) -> None:
    cover = selected_game.get("cover", {})
    if not isinstance(cover, dict):
        return
    cover_map = cast(dict[str, Any], cover)
    url = _normalized_igdb_image_url(cover_map.get("url"), "t_cover_big")
    if url:
        meta.artwork_url = url


def _apply_igdb_genres(meta: Meta, selected_game: dict[str, Any]) -> None:
    genres = [
        genre.get("name")
        for genre in selected_game.get("genres", [])
        if genre.get("name")
    ]
    if genres:
        meta.genres = genres
        meta.keywords = genres


def _igdb_platform_alias_mapping() -> dict[str, list[str]]:
    nin_term = bytes([110, 105, 110, 116, 101, 110, 100, 111]).decode()
    return {
        "playstation 5": ["ps5", "playstation 5", "playstation5"],
        "playstation 4": ["ps4", "playstation 4", "playstation4"],
        "playstation 3": ["ps3", "playstation 3", "playstation3"],
        "playstation 2": ["ps2", "playstation 2", "playstation2"],
        "playstation": ["ps1", "psx", "playstation 1", "playstation1"],
        "xbox series x|s": ["xsx", "xbox series", "xboxseries"],
        "xbox series x/s": ["xsx", "xbox series", "xboxseries"],
        "xbox one": ["xone", "xbox one", "xboxone"],
        "xbox 360": ["x360", "xbox 360", "xbox360"],
        "xbox": ["xbox"],
        f"{nin_term} switch": [
            "nsw",
            "switch",
            f"{nin_term} switch",
            f"{nin_term}switch",
        ],
        f"{nin_term} 3ds": ["3ds", f"{nin_term} 3ds"],
        f"{nin_term} ds": ["nds", f"{nin_term} ds"],
        "wii u": ["wii u", "wiiu"],
        "wii": ["wii"],
        "pc (microsoft windows)": [
            "pc",
            "windows",
            "win",
            "osx",
            "mac",
            "linux",
        ],
        "mac": ["mac", "macos", "osx"],
        "linux": ["linux"],
    }


def _igdb_platform_aliases(platform_name: str) -> list[str]:
    normalized = platform_name.lower()
    aliases: list[str] = []
    for map_key, map_aliases in _igdb_platform_alias_mapping().items():
        if map_key in normalized or normalized in map_key:
            aliases.extend(map_aliases)
    aliases.append(normalized)
    return list(dict.fromkeys(aliases))


def _normalized_platform_source(meta: Meta, videopath: str) -> str:
    basename = Path(str(meta.path or videopath or "")).name.lower()
    return re.sub(r"[.\-_\[\]]+", " ", basename)


def _detected_igdb_platform(
    raw_platforms: list[str], mapped_platforms: list[str], source: str
) -> str | None:
    for index, platform_name in enumerate(raw_platforms):
        aliases = _igdb_platform_aliases(platform_name)
        if any(
            re.search(rf"\b{re.escape(alias)}\b", source) for alias in aliases
        ):
            return mapped_platforms[index]
    return None


def _selected_game_platform_names(selected_game: dict[str, Any]) -> list[str]:
    names: list[str] = []
    raw_platforms = selected_game.get("platforms", [])
    if not isinstance(raw_platforms, list):
        return names
    for platform in cast(list[Any], raw_platforms):
        if not isinstance(platform, dict):
            continue
        name = cast(dict[str, Any], platform).get("name")
        if name:
            names.append(str(name))
    return names


def _retain_supported_platform(meta: Meta, mapped: list[str]) -> bool:
    current = str(meta.platform or "").upper()
    if not current or current not in mapped:
        return False
    meta.platform = current
    return True


def _apply_detected_igdb_platform(
    meta: Meta,
    raw_platforms: list[str],
    videopath: str,
) -> bool:
    detected = _detected_igdb_platform(
        raw_platforms,
        [map_to_clean_code(name) for name in raw_platforms],
        _normalized_platform_source(meta, videopath),
    )
    if not detected:
        return False
    meta.platform = detected
    logger.info(
        f"[green]Game platform auto-detected from folder/file name: {detected}[/green]"
    )
    return True


def _apply_single_igdb_platform(meta: Meta, mapped: list[str]) -> None:
    if len(mapped) != 1:
        return
    meta.platform = mapped[0]
    logger.debug(f"[green]Game platform set to: {mapped[0]}[/green]")


def _apply_igdb_platform(
    meta: Meta,
    selected_game: dict[str, Any],
    videopath: str,
    manual_platform: bool,
) -> None:
    if manual_platform:
        return
    raw_platforms = _selected_game_platform_names(selected_game)
    mapped = list(
        dict.fromkeys(map_to_clean_code(name) for name in raw_platforms)
    )
    if _retain_supported_platform(meta, mapped):
        return
    if _apply_detected_igdb_platform(meta, raw_platforms, videopath):
        return
    _apply_single_igdb_platform(meta, mapped)


def _company_role_entry(company_info: Any) -> tuple[str, bool, bool] | None:
    if not isinstance(company_info, dict):
        return None
    info = cast(dict[str, Any], company_info)
    company = info.get("company", {})
    if not isinstance(company, dict):
        return None
    company_map = cast(dict[str, Any], company)
    name = str(company_map.get("name") or "")
    if not name:
        return None
    return name, bool(info.get("developer")), bool(info.get("publisher"))


def _igdb_companies(
    selected_game: dict[str, Any],
) -> tuple[list[str], list[str]]:
    developers: list[str] = []
    publishers: list[str] = []
    for company_info in selected_game.get("involved_companies", []):
        entry = _company_role_entry(company_info)
        if entry is None:
            continue
        name, is_developer, is_publisher = entry
        if is_developer:
            developers.append(name)
        if is_publisher:
            publishers.append(name)
    return developers, publishers


def _apply_igdb_companies(meta: Meta, selected_game: dict[str, Any]) -> None:
    developers, publishers = _igdb_companies(selected_game)
    if developers:
        meta.developer = ", ".join(developers)
    if publishers:
        meta.publisher = ", ".join(publishers)


def _steam_url_from_websites(selected_game: dict[str, Any]) -> str | None:
    for website in selected_game.get("websites", []):
        if website.get("type") == 13:
            return website.get("url")
    return None


def _steam_url_from_external_games(
    selected_game: dict[str, Any],
) -> str | None:
    for external in selected_game.get("external_games", []):
        if external.get("external_game_source") != 1:
            continue
        url = external.get("url")
        if url:
            return url
        uid = external.get("uid")
        return f"https://store.steampowered.com/app/{uid}" if uid else None
    return None


def _steam_url(selected_game: dict[str, Any]) -> str | None:
    return _steam_url_from_websites(
        selected_game
    ) or _steam_url_from_external_games(selected_game)


def _nested_name(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(cast(dict[str, Any], value).get("name") or "")


def _language_support_entry(support: Any) -> tuple[str, str] | None:
    if not isinstance(support, dict):
        return None
    mapping = cast(dict[str, Any], support)
    language = _nested_name(mapping.get("language", {}))
    support_type = _nested_name(mapping.get("language_support_type", {}))
    if not language or not support_type:
        return None
    return language, support_type


def _record_language_support(
    languages: dict[str, list[str]], language: str, support_type: str
) -> None:
    bucket = languages.setdefault(language, [])
    if support_type not in bucket:
        bucket.append(support_type)


def _apply_igdb_languages(meta: Meta, selected_game: dict[str, Any]) -> None:
    languages: dict[str, list[str]] = {}
    for support in selected_game.get("language_supports", []):
        entry = _language_support_entry(support)
        if entry is None:
            continue
        _record_language_support(languages, *entry)
    if languages:
        meta.languages = languages


def _apply_available_platforms(
    meta: Meta, selected_game: dict[str, Any]
) -> None:
    meta.available_platforms = [
        platform.get("name")
        for platform in selected_game.get("platforms", [])
        if platform.get("name")
    ]


def _steam_app_id(steam_url: str | None) -> str | None:
    if not steam_url:
        return None
    match = re.search(r"/app/(\d+)", steam_url)
    return match.group(1) if match else None


def _uses_brazilian_steam_locale(meta: Meta) -> bool:
    target_trackers = {
        "AMIGOSSHARE",
        "BRASILTRACKER",
        "BJSHARE",
        "CAPYBARABR",
        "SAMARITANO",
    }
    return bool(
        {str(tracker).upper() for tracker in meta.trackers} & target_trackers
    )


def _steam_request_params(steam_id: str, localized: bool) -> dict[str, str]:
    params = {"appids": steam_id}
    if localized:
        params["l"] = "brazilian"
    return params


async def _steam_http_payload(params: dict[str, str]) -> tuple[int, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://store.steampowered.com/api/appdetails", params=params
        )
    return (
        response.status_code,
        response.json() if response.status_code == 200 else {},
    )


async def _resolved_steam_payload(
    cache: Any, cache_key: str, params: dict[str, str]
) -> Any:
    cached = await cache.get("steam", "appdetails", cache_key)
    if not is_cache_miss(cached):
        return cached
    status_code, payload = await _steam_http_payload(params)
    if status_code == 200 and isinstance(payload, dict):
        payload_map = cast(dict[str, Any], payload)
        await cache.set(
            "steam",
            "appdetails",
            cache_key,
            payload_map,
            negative=not bool(payload_map),
        )
        return payload_map
    if status_code == 404:
        payload = {}
        await cache.set(
            "steam", "appdetails", cache_key, payload, negative=True
        )
    return {}


def _steam_payload_entry(payload: Any, steam_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    payload_map = cast(dict[str, Any], payload)
    entry = payload_map.get(steam_id)
    return cast(dict[str, Any], entry) if isinstance(entry, dict) else None


def _steam_entry_data(entry: dict[str, Any] | None) -> dict[str, Any]:
    if entry is None or not entry.get("success"):
        return {}
    data = entry.get("data", {})
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _steam_app_data(payload: Any, steam_id: str) -> dict[str, Any]:
    return _steam_entry_data(_steam_payload_entry(payload, steam_id))


def _steam_localized_description(app_data: dict[str, Any]) -> str:
    description = (
        app_data.get("short_description")
        or app_data.get("about_the_game")
        or app_data.get("detailed_description")
        or ""
    )
    clean = re.sub(r"<[^>]+>", "", str(description)).strip()
    return html.unescape(clean)


def _apply_steam_localized_overview(
    meta: Meta, app_data: dict[str, Any], localized: bool
) -> None:
    if not localized:
        return
    description = _steam_localized_description(app_data)
    if description:
        meta.localized_overviews = {"brazilian": description}


def _steam_requirements_key(platform: Any) -> str | None:
    return {
        "PC": "pc_requirements",
        "MAC": "mac_requirements",
        "LINUX": "linux_requirements",
    }.get(str(platform or "").upper())


def _apply_steam_requirements(meta: Meta, app_data: dict[str, Any]) -> None:
    key = _steam_requirements_key(meta.platform)
    if key is None:
        return
    requirements = app_data.get(key)
    if not isinstance(requirements, dict):
        return
    requirements_map = cast(dict[str, Any], requirements)
    minimum = requirements_map.get("minimum", "")
    recommended = requirements_map.get("recommended", "")
    if minimum:
        meta.requirements_minimum = minimum
    if recommended:
        meta.requirements_recommended = recommended


async def _apply_steam_metadata(
    meta: Meta,
    steam_url: str | None,
    base_dir: str,
    config: dict[str, Any] | None,
) -> None:
    steam_id = _steam_app_id(steam_url)
    if not steam_id:
        return
    localized = _uses_brazilian_steam_locale(meta)
    params = _steam_request_params(steam_id, localized)
    try:
        cache = cache_for(base_dir, config)
        cache_key = json.dumps(params, sort_keys=True)
        payload = await _resolved_steam_payload(cache, cache_key, params)
        app_data = _steam_app_data(payload, steam_id)
        if not app_data:
            return
        _apply_steam_localized_overview(meta, app_data, localized)
        _apply_steam_requirements(meta, app_data)
    except Exception as error:
        logger.info(
            f"[yellow]Steam: Error fetching app details: {error}[/yellow]"
        )


def _igdb_screenshot_image(
    screenshot: dict[str, Any],
) -> dict[str, str] | None:
    url = screenshot.get("url")
    if not url:
        return None
    return {
        "img_url": _normalized_igdb_image_url(url, "t_screenshot_med"),
        "raw_url": _normalized_igdb_image_url(url, "t_1080p"),
        "web_url": _normalized_igdb_image_url(url, "t_1080p"),
    }


def _igdb_screenshot_images(
    selected_game: dict[str, Any],
) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for screenshot in selected_game.get("screenshots", []):
        if not isinstance(screenshot, dict):
            continue
        image = _igdb_screenshot_image(screenshot)
        if image:
            images.append(image)
    return images


async def _write_igdb_screenshot_data(
    meta: Meta, base_dir: str, image_list: list[dict[str, str]]
) -> None:
    tmp_dir = Path(base_dir) / "tmp" / meta.uuid
    tmp_dir.mkdir(parents=True, exist_ok=True)
    image_data_file = tmp_dir / "image_data.json"
    image_data: dict[str, Any] = {
        "image_list": image_list,
        "image_sizes": {},
        "tonemapped": False,
    }
    try:
        async with aiofiles.open(
            image_data_file, "w", encoding="utf-8"
        ) as image_file:
            await image_file.write(json.dumps(image_data, indent=4))
        logger.debug(
            f"[green]IGDB: Saved {len(image_list)} screenshots to image_data.json[/green]"
        )
    except Exception as error:
        logger.info(
            f"[yellow]IGDB: Failed to save screenshots to image_data.json: {error}[/yellow]"
        )


async def _apply_igdb_screenshots(
    meta: Meta, selected_game: dict[str, Any], base_dir: str
) -> None:
    images = _igdb_screenshot_images(selected_game)
    if not images:
        return
    meta.image_list = images
    await _write_igdb_screenshot_data(meta, base_dir, images)


async def _apply_selected_game_metadata(
    meta: Meta,
    selected_game: dict[str, Any],
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None,
    cli_overrides: dict[str, bool],
) -> None:
    _apply_igdb_identity(meta, selected_game, cli_overrides)
    _apply_igdb_rating(meta, selected_game)
    _apply_igdb_overview(meta, selected_game)
    _apply_igdb_cover(meta, selected_game)
    _apply_igdb_genres(meta, selected_game)
    _apply_igdb_platform(
        meta, selected_game, videopath, cli_overrides["platform"]
    )
    _apply_igdb_companies(meta, selected_game)
    steam_url = _steam_url(selected_game)
    if steam_url:
        meta.steam_url = steam_url
    _apply_igdb_languages(meta, selected_game)
    _apply_available_platforms(meta, selected_game)
    await _apply_steam_metadata(meta, steam_url, base_dir, config)
    await _apply_igdb_screenshots(meta, selected_game, base_dir)
    meta.igdb_id = selected_game.get("id", 0)
    _refresh_console_game(meta)


async def _prepare_local_game_state(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None,
) -> tuple[str, str, dict[str, bool]]:
    _initialize_game_meta(meta)
    await _apply_local_game_nfo(meta)
    title_source = _game_title_source(meta, videopath)
    _apply_fallback_game_identity(meta, title_source)
    cli_overrides = _game_cli_overrides(meta)
    await _apply_detected_game_platform(
        meta, videopath, base_dir, config, cli_overrides["platform"]
    )
    _refresh_console_game(meta)
    path_to_check = str(meta.path or videopath or "")
    _apply_game_version(meta, path_to_check)
    return title_source, path_to_check, cli_overrides


def _igdb_context(
    title_source: str,
    base_dir: str,
    config: dict[str, Any] | None,
) -> tuple[IGDBAPI, str] | None:
    client_id, client_secret = _igdb_credentials(config)
    if not client_id or not client_secret:
        logger.warning(
            "[bold red]Warning: Twitch Client ID or Secret is not configured. Game metadata search will be skipped.[/bold red]"
        )
        return None
    title_query = _igdb_title_query(title_source)
    if not title_query:
        logger.warning(
            "[bold red]Warning: Could not determine game title for metadata search.[/bold red]"
        )
        return None
    return IGDBAPI(client_id, client_secret, base_dir), title_query


async def _complete_igdb_game_metadata(
    meta: Meta,
    igdb: IGDBAPI,
    title_query: str,
    path_to_check: str,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None,
    cli_overrides: dict[str, bool],
) -> None:
    selected_game, search_had_results = await _resolve_igdb_game(
        meta, igdb, title_query, path_to_check
    )
    if selected_game is None:
        if search_had_results:
            logger.info("[yellow]Skipped IGDB metadata selection.[/yellow]")
        return
    await igdb.cache_game_details(selected_game)
    await _apply_selected_game_metadata(
        meta,
        selected_game,
        videopath,
        base_dir,
        config,
        cli_overrides,
    )
    logger.debug(
        f"[green]IGDB metadata successfully retrieved for game: {meta.title}[/green]"
    )


async def gather_game_prep(
    meta: Meta,
    videopath: str,
    base_dir: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Query IGDB/Steam metadata and populate game metadata in-place."""
    (
        title_source,
        path_to_check,
        cli_overrides,
    ) = await _prepare_local_game_state(meta, videopath, base_dir, config)
    context = _igdb_context(title_source, base_dir, config)
    if context is None:
        return
    igdb, title_query = context
    await _complete_igdb_game_metadata(
        meta,
        igdb,
        title_query,
        path_to_check,
        videopath,
        base_dir,
        config,
        cli_overrides,
    )
