"""Compatibility layer backed by the official MediaInfo CLI."""

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bin.get_mediainfo import MediaInfoBinaryManager
from src.app_paths import CODE_DIR, STATE_DIR
from src.binaries import configured_binary

_REPORT_BY_LINE = re.compile(r"(?<![^\r\n])[ \t]*ReportBy[ \t]*:[^\r\n]*(?:\r\n?|\n)?", re.IGNORECASE)


def strip_report_by_line(report: str) -> str:
    """Remove MediaInfo's optional ReportBy version line from a text report."""
    return _REPORT_BY_LINE.sub("", report)


def resolve_mediainfo_binary(
    config: Mapping[str, Any] | None = None,
    *,
    code_dir: Path | None = None,
    state_dir: Path | None = None,
) -> str | None:
    if configured := configured_binary("mediainfo_path", config):
        return configured
    for root in (code_dir or CODE_DIR, state_dir or STATE_DIR):
        if managed := MediaInfoBinaryManager.find_managed_binary(root):
            return managed
    return shutil.which("mediainfo")


async def ensure_mediainfo_binary(config: Mapping[str, Any] | None = None, *, state_dir: Path | None = None) -> str:
    runtime_state = state_dir or STATE_DIR
    if existing := resolve_mediainfo_binary(config, state_dir=runtime_state):
        return existing
    return await MediaInfoBinaryManager.ensure_mediainfo_binary(runtime_state)


def _binary() -> str:
    binary = resolve_mediainfo_binary()
    if binary is None:
        raise RuntimeError("MediaInfo CLI is not installed; run Upload Assistant so it can download bin/MI first")
    return binary


def run_mediainfo(path: str | Path, *, output: str | None = None, full: bool = True, inform: str | None = None) -> str:
    command = [_binary()]
    if output != "JSON":
        command.append("--inform_version=1")
    if full:
        command.append("--Full")
    if inform:
        command.append(f"--Inform={inform}")
    elif output and output != "STRING":
        command.append(f"--Output={output}")
    command.append(str(path))
    try:
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MediaInfo timed out after 15 minutes") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"MediaInfo failed with exit code {result.returncode}\n"
            f"Command: {command!r}\n"
            f"stdout:\n{result.stdout.strip() or '(empty)'}\n"
            f"stderr:\n{result.stderr.strip() or '(empty)'}"
        )
    return result.stdout


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).replace("@", "").strip("_").lower()


class MediaInfoTrack:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def track_type(self) -> str | None:
        value = self._data.get("@type")
        return str(value) if value is not None else None

    def to_data(self) -> dict[str, Any]:
        return {_snake_case(key): value for key, value in self._data.items()}

    def __getattr__(self, name: str) -> Any:
        for key, value in self._data.items():
            if _snake_case(key) == name:
                if name == "duration" and value is not None:
                    try:
                        return float(value) * 1000
                    except TypeError, ValueError:
                        return value
                return value
        return None


class MediaInfoResult:
    def __init__(self, report: dict[str, Any]) -> None:
        tracks = report.get("media", {}).get("track", [])
        self.tracks = [MediaInfoTrack(track) for track in tracks if isinstance(track, dict)]


class MediaInfo:
    """Subset of the previous Python binding API used by Upload Assistant."""

    @staticmethod
    def parse(
        filename: str | Path,
        *,
        output: str | None = None,
        full: bool = True,
        mediainfo_options: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> MediaInfoResult | str:
        inform = (mediainfo_options or {}).get("inform")
        if output is not None or inform:
            return run_mediainfo(filename, output=output, full=full, inform=inform)
        return MediaInfoResult(json.loads(run_mediainfo(filename, output="JSON", full=full)))
