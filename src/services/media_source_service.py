# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import importlib
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain_models.processing import WeirdSystemError
from src.domain_models.release import Meta
from src.services.runtime_support import logger

GuessitFn = Callable[[str, dict[str, Any] | None], dict[str, Any]]
_guessit_module = importlib.import_module("guessit")
_guessit_fn: GuessitFn = _guessit_module.guessit


def guessit_fn(
    value: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    return _guessit_fn(value, options)


def _mediainfo_path(base_dir: str, folder_id: str) -> Path:
    return Path(base_dir) / "tmp" / folder_id / "MediaInfo.json"


async def _load_mediainfo(
    meta: Meta, base_dir: str, folder_id: str
) -> dict[str, Any]:
    if meta.is_disc == "BDMV":
        return {}
    try:
        text = await asyncio.to_thread(
            _mediainfo_path(base_dir, folder_id).read_text,
            encoding="utf-8",
        )
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        logger.debug("No mediainfo.json")
        return {}


def _guess_source(video: str, path: str) -> Any:
    try:
        return guessit_fn(video).get("source", "BluRay")
    except Exception:
        try:
            return guessit_fn(path).get("source", "BluRay")
        except Exception:
            return "BluRay"


def _initial_source(meta: Meta, video: str, path: str) -> Any:
    return (
        meta.manual_source
        if meta.manual_source
        else _guess_source(video, path)
    )


def _normalize_bluray_source(source: Any, type_name: str, is_disc: str) -> Any:
    bluray = source in ("Blu-ray", "Ultra HD Blu-ray", "BluRay", "BR")
    if not bluray and is_disc != "BDMV":
        return source
    if type_name == "DISC":
        return "Blu-ray"
    if type_name in ("ENCODE", "REMUX"):
        return "BluRay"
    return source


def _mediainfo_dvd_system(mi: dict[str, Any]) -> str:
    try:
        system = ""
        for track in mi["media"]["track"]:
            if track["@type"] == "Video":
                system = str(track.get("Standard", ""))
        if system not in ("PAL", "NTSC"):
            raise WeirdSystemError
        return system
    except Exception:
        return ""


def _guess_dvd_system(video: str) -> str:
    try:
        other = guessit_fn(video).get("other", [])
    except Exception:
        return ""
    if not isinstance(other, list):
        return ""
    if "PAL" in other:
        return "PAL"
    return "NTSC" if "NTSC" in other else ""


def _framerate_dvd_system(mi: dict[str, Any]) -> str:
    try:
        framerate = str(mi["media"]["track"][1].get("FrameRate", ""))
    except Exception:
        return ""
    if "25" in framerate or "50" in framerate:
        return "PAL"
    return "NTSC" if framerate else ""


def _dvd_system(mi: dict[str, Any], video: str) -> str:
    system = _mediainfo_dvd_system(mi)
    if system:
        return system
    system = _guess_dvd_system(video)
    return system if system else _framerate_dvd_system(mi)


def _normalize_dvd_source(
    source: Any,
    type_name: str,
    is_disc: str,
    mi: dict[str, Any],
    video: str,
) -> Any:
    if is_disc != "DVD" and source not in ("DVD", "dvd"):
        return source
    system = _dvd_system(mi, video)
    return f"{system} DVD".strip() if type_name == "REMUX" else system


def _normalize_web_type(source: Any, type_name: str) -> str:
    if source in ("Web", "WEB") and type_name == "ENCODE":
        return "WEBRIP"
    return type_name


def _normalize_hddvd_source(source: Any, type_name: str, is_disc: str) -> Any:
    if source not in ("HD-DVD", "HD DVD", "HDDVD"):
        return source
    if type_name in ("ENCODE", "REMUX"):
        return "HDDVD"
    return "HD DVD" if is_disc == "HDDVD" else source


def _final_source(source: Any, type_name: str) -> Any:
    if type_name in ("WEBDL", "WEBRIP"):
        return "Web"
    return "UHDTV" if source == "Ultra HDTV" else source


def _resolve_source(
    source: Any,
    type_name: str,
    is_disc: str,
    mi: dict[str, Any],
    video: str,
) -> tuple[Any, str]:
    source = _normalize_bluray_source(source, type_name, is_disc)
    source = _normalize_dvd_source(source, type_name, is_disc, mi, video)
    type_name = _normalize_web_type(source, type_name)
    source = _normalize_hddvd_source(source, type_name, is_disc)
    return _final_source(source, type_name), type_name


async def get_source(
    type: str,
    video: str,
    path: str,
    is_disc: str,
    meta: Meta,
    folder_id: str,
    base_dir: str,
) -> tuple[str, str]:
    mi = await _load_mediainfo(meta, base_dir, folder_id)
    try:
        source = _initial_source(meta, video, path)
        source, type = _resolve_source(source, type, is_disc, mi, video)
    except Exception:
        logger.info(traceback.format_exc())
        source = "BluRay"
    return str(source), type
