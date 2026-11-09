# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import traceback
from collections import OrderedDict, defaultdict
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import cli_ui
import defusedxml.ElementTree as ElementTree
from langcodes import Language
from rich.progress import BarColumn, TaskProgressColumn, TextColumn

from src.domain_models.release import Meta
from src.integrations.media.media_info import MediaInfo
from src.integrations.media.media_info_export import find_dvd_mediainfo
from src.integrations.observability.console import progress_display
from src.integrations.observability.runtime_support import (
    console,
    logger,
    prompt_in_thread,
)
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)
from src.integrations.runtime_tools.playlist import MplsParser

PlaylistItem = dict[str, Any]
PlaylistInfo = dict[str, Any]


class DiscParse:
    PROCESS_CLEANUP_TIMEOUT = 5

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.mediainfo_config: dict[str, Any] | None = None

    @staticmethod
    def _process_group_options() -> dict[str, Any]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    async def _terminate_windows_process_tree(
        process: asyncio.subprocess.Process, pid: int
    ) -> None:
        tree_killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(
                tree_killer.wait(), timeout=DiscParse.PROCESS_CLEANUP_TIMEOUT
            )
        except TimeoutError:
            with suppress(ProcessLookupError):
                tree_killer.kill()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()

    @staticmethod
    def _terminate_posix_process(
        process: asyncio.subprocess.Process, pid: int | None
    ) -> None:
        if pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            return
        with suppress(ProcessLookupError):
            process.kill()

    @classmethod
    async def _terminate_process_tree(
        cls, process: asyncio.subprocess.Process
    ) -> None:
        if process.returncode is not None:
            return
        pid = getattr(process, "pid", None)
        if os.name == "nt" and pid is not None:
            await cls._terminate_windows_process_tree(process, pid)
            return
        cls._terminate_posix_process(process, pid)

    async def _run_specialized_mediainfo(
        self, binary: str, *arguments: str, env: dict[str, str] | None = None
    ) -> tuple[bytes, bytes, int | None]:
        # binary is resolved from validated local config/bundled MediaInfo; argv is exec-form.
        process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
            binary,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **self._process_group_options(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )
        except TimeoutError:
            await self._terminate_process_tree(process)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    process.communicate(), timeout=self.PROCESS_CLEANUP_TIMEOUT
                )
            raise RuntimeError(
                "Specialized MediaInfo timed out after 30 seconds"
            ) from None
        except BaseException:
            await self._terminate_process_tree(process)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    process.communicate(), timeout=self.PROCESS_CLEANUP_TIMEOUT
                )
            raise
        return stdout, stderr, process.returncode

    @staticmethod
    def _playlist_sizes(items: list[PlaylistItem]) -> tuple[int, int]:
        sizes = [int(item["size"]) for item in items]
        if not sizes:
            return 0, 0
        return max(sizes), sum(sizes)

    @staticmethod
    def _playlist_concentration(
        items: list[PlaylistItem], total_play_items: int
    ) -> float:
        if total_play_items <= 0:
            return 0.0
        unique_files = len({str(item["file"]) for item in items})
        return unique_files / total_play_items

    @classmethod
    def _playlist_metrics(
        cls, playlist: PlaylistInfo
    ) -> tuple[int, int, float, float]:
        items = cast(list[PlaylistItem], playlist.get("items", []))
        largest_file, total_size = cls._playlist_sizes(items)
        duration = float(playlist.get("duration", 0.0) or 0.0)
        total_play_items = int(
            playlist.get("total_play_items", len(items)) or 0
        )
        concentration = cls._playlist_concentration(items, total_play_items)
        return largest_file, total_size, duration, concentration

    @staticmethod
    def _normalized_metric(
        value: float, maximum: float, weight: float
    ) -> float:
        return min(value / maximum, 1.0) * weight

    def _calculate_playlist_score(self, playlist: PlaylistInfo) -> float:
        """Calculate weighted score for playlist selection."""
        if not playlist.get("items"):
            return 0.0
        largest_file, total_size, duration, concentration = (
            self._playlist_metrics(playlist)
        )
        return sum(
            (
                self._normalized_metric(largest_file, 100.0 * 1024**3, 40.0),
                self._normalized_metric(total_size, 150.0 * 1024**3, 30.0),
                self._normalized_metric(duration, 14400.0, 20.0),
                concentration * 10.0,
            )
        )

    @staticmethod
    def _windows_dvd_mediainfo(
        base_dir: str | None,
    ) -> tuple[str, dict[str, str]] | None:
        if base_dir is None or platform.system().lower() != "windows":
            return None
        path = (
            Path(base_dir) / "bin" / "MI" / "windows" / "dvd" / "MediaInfo.exe"
        )
        return (str(path), os.environ.copy()) if path.is_file() else None

    @staticmethod
    def _linux_dvd_paths(base_dir: str) -> tuple[Path, Path, Path]:
        dvd_dir = Path(base_dir) / "bin" / "MI" / "linux" / "dvd"
        return dvd_dir, dvd_dir / "mediainfo", dvd_dir / "libmediainfo.so.0"

    @staticmethod
    def _linux_dvd_env(dvd_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        current = os.environ.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            f"{dvd_dir}{os.pathsep}{current}" if current else str(dvd_dir)
        )
        return env

    @classmethod
    def _linux_dvd_mediainfo(
        cls, base_dir: str | None
    ) -> tuple[str, dict[str, str]] | None:
        if base_dir is None or platform.system().lower() != "linux":
            return None
        dvd_dir, dvd_cli, dvd_lib = cls._linux_dvd_paths(base_dir)
        if not all((dvd_cli.is_file(), dvd_lib.is_file())):
            return None
        return str(dvd_cli), cls._linux_dvd_env(dvd_dir)

    def _discovered_dvd_mediainfo(
        self, base_dir: str | None
    ) -> tuple[str, dict[str, str]] | None:
        if self.mediainfo_config is None:
            if base_dir is None:
                return None
            self.mediainfo_config = find_dvd_mediainfo(base_dir)
        if not self.mediainfo_config:
            return None
        cli = self.mediainfo_config.get("cli")
        return (str(cli), os.environ.copy()) if cli else None

    def setup_mediainfo_for_dvd(
        self, base_dir: str | None
    ) -> tuple[str, dict[str, str]] | None:
        """Setup MediaInfo binary for DVD processing."""
        configured = configured_binary("dvd_mediainfo_path", self.config)
        if configured:
            return configured, os.environ.copy()
        for candidate in (
            self._windows_dvd_mediainfo(base_dir),
            self._linux_dvd_mediainfo(base_dir),
        ):
            if candidate is not None:
                return candidate
        return self._discovered_dvd_mediainfo(base_dir)

    @staticmethod
    def _bdinfo_progress_pattern() -> re.Pattern[str]:
        return re.compile(
            r"Stream scan:\s+(?P<percent>\d+(?:\.\d+)?)%\s+\((?P<done>[^,]+)\s*/\s*(?P<total>[^,]+),\s*files\s*"
            r"(?P<files_done>\d+)/(?P<files_total>\d+),\s*read\s*(?P<speed>[^,]+),\s*ETA\s*(?P<eta>[^)]+)\)"
        )

    @staticmethod
    def _bdinfo_progress_value(
        pattern: re.Pattern[str], text: str
    ) -> tuple[float, str] | None:
        match = pattern.search(text)
        if match is None:
            return None
        detail = (
            f"{match['done'].strip()} / {match['total'].strip()} | "
            f"{match['speed'].strip()} | ETA {match['eta'].strip()}"
        )
        return float(match["percent"]), detail

    @classmethod
    def _update_bdinfo_progress(
        cls, progress: Any, task: Any, pattern: re.Pattern[str], text: str
    ) -> None:
        value = cls._bdinfo_progress_value(pattern, text)
        if value is None:
            return
        percent, detail = value
        progress.update(
            task,
            completed=percent,
            description=f"Scanning Blu-ray... {detail}",
        )

    @classmethod
    async def _consume_bdinfo_progress(
        cls, stderr: asyncio.StreamReader, progress: Any, task: Any
    ) -> None:
        pattern = cls._bdinfo_progress_pattern()
        buffer = ""
        while chunk := await stderr.read(1024):
            buffer += chunk.decode("utf-8", errors="replace")
            updates = re.split(r"[\r\n]+", buffer)
            buffer = updates.pop()
            for update in updates:
                cls._update_bdinfo_progress(progress, task, pattern, update)
        if buffer:
            cls._update_bdinfo_progress(progress, task, pattern, buffer)

    async def _run_bdinfo_with_progress(
        self, command: list[str], _progress_id: str
    ) -> int:
        """Run go-bdinfo and forward its stream-scan progress to CLI observers."""
        process = await asyncio.create_subprocess_exec(  # nosemgrep: dangerous-asyncio-create-exec-audit
            *[*command, "--progress"],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            **self._process_group_options(),
        )
        try:
            if process.stderr is None:
                raise RuntimeError("Unable to read go-bdinfo progress output")
            with progress_display(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                transient=False,
            ) as progress:
                task = progress.add_task("Scanning Blu-ray...", total=100)
                await self._consume_bdinfo_progress(
                    process.stderr, progress, task
                )
                returncode = await process.wait()
                if returncode == 0:
                    progress.update(
                        task,
                        completed=100,
                        description="Scanning Blu-ray complete",
                    )
            return returncode
        finally:
            if process.returncode is None:
                await self._terminate_process_tree(process)
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        process.wait(), timeout=self.PROCESS_CLEANUP_TIMEOUT
                    )

    """
    Get and parse bdinfo
    """

    @staticmethod
    def _bdinfo_save_dir(base_dir: str, folder_id: str) -> Path:
        path = Path(base_dir) / "tmp" / folder_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _bdinfo_cached_summary(save_dir: Path, disc_index: int) -> Path | None:
        candidate = save_dir / f"BD_SUMMARY_{disc_index:02d}.txt"
        return candidate if candidate.exists() else None

    @staticmethod
    def _record_missing_certificate(
        meta: Meta, disc: dict[str, Any], path: str
    ) -> None:
        if disc.get("type") != "BDMV":
            return
        if (Path(path).parent / "CERTIFICATE").exists():
            return
        meta.setdefault("discs_missing_certificate", []).append(
            disc.get("path")
        )

    @staticmethod
    def _load_mpls(mpls_path: str) -> Any:
        with Path(mpls_path).open("rb") as mpls_file:
            parser = MplsParser(mpls_file)
            header = parser.load_movie_playlist()
            mpls_file.seek(header.playlist_start_address, os.SEEK_SET)
            return parser.load_playlist()

    @staticmethod
    def _play_item_duration(item: Any) -> float | None:
        intime = getattr(item, "intime", None)
        outtime = getattr(item, "outtime", None)
        if intime is None or outtime is None:
            return None
        return (outtime - intime) / 45000.0

    @staticmethod
    def _play_item_clip_name(item: Any) -> str:
        value = getattr(item, "clip_information_filename", None)
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _record_playlist_stream(
        stream_path: Path,
        file_counts: defaultdict[str, int],
        file_sizes: dict[str, int],
    ) -> None:
        key = str(stream_path)
        file_counts[key] += 1
        file_sizes[key] = stream_path.stat().st_size

    @classmethod
    def _playlist_play_item(
        cls,
        item: Any,
        stream_directory: Path,
        file_counts: defaultdict[str, int],
        file_sizes: dict[str, int],
        meta: Meta,
    ) -> tuple[float, int]:
        duration = cls._play_item_duration(item)
        if duration is None:
            return 0.0, 0
        clip_name = cls._play_item_clip_name(item)
        if not clip_name:
            return duration, 0
        stream_path = stream_directory / f"{clip_name}.m2ts"
        if not stream_path.exists():
            if meta.debug:
                logger.info(f"[yellow]    Missing m2ts file: {clip_name}.m2ts")
            return duration, 0
        cls._record_playlist_stream(stream_path, file_counts, file_sizes)
        return duration, 1

    @staticmethod
    def _debug_playlist_added(
        file_name: str,
        duration: float,
        file_counts: defaultdict[str, int],
        file_sizes: dict[str, int],
        total_size: int,
    ) -> None:
        duplicates = [file for file, count in file_counts.items() if count > 1]
        if duplicates:
            logger.debug(
                f"[green]  ✓ Added {file_name}: {duration:.1f}s, {len(file_sizes)} unique files ({len(duplicates)} files repeated), {total_size // (1024 * 1024)} MB total"
            )
            return
        logger.debug(
            f"[green]  ✓ Added {file_name}: {duration:.1f}s, {len(file_counts)} unique files, {total_size // (1024 * 1024)} MB total"
        )

    @classmethod
    def _built_bdinfo_playlist(
        cls,
        file_name: str,
        mpls_path: str,
        play_items: Sequence[Any],
        stream_directory: Path,
        meta: Meta,
    ) -> PlaylistInfo | None:
        logger.debug(
            f"[cyan]  Found {len(play_items)} play items in {file_name}"
        )
        duration = 0.0
        total_play_items = 0
        file_counts: defaultdict[str, int] = defaultdict(int)
        file_sizes: dict[str, int] = {}
        for item in play_items:
            item_duration, added = cls._playlist_play_item(
                item, stream_directory, file_counts, file_sizes, meta
            )
            duration += item_duration
            total_play_items += added
        if not file_sizes:
            logger.debug(f"[yellow]  No m2ts files found for {file_name}")
            return None
        items: list[PlaylistItem] = [
            {"file": file, "size": file_sizes[file]} for file in file_counts
        ]
        if meta.debug:
            cls._debug_playlist_added(
                file_name,
                duration,
                file_counts,
                file_sizes,
                sum(file_sizes.values()),
            )
        return {
            "file": file_name,
            "duration": duration,
            "path": mpls_path,
            "items": items,
            "total_play_items": total_play_items,
        }

    @classmethod
    async def _bdinfo_playlist_info(
        cls,
        file_name: str,
        playlists_path: Path,
        stream_directory: Path,
        meta: Meta,
    ) -> PlaylistInfo | None:
        if not file_name.endswith(".mpls"):
            return None
        mpls_path = str(playlists_path / file_name)
        logger.debug(f"[cyan]Processing playlist: {file_name}")
        try:
            playlist_data = await asyncio.to_thread(cls._load_mpls, mpls_path)
        except Exception as error:
            logger.info(
                f"[bold red]Error parsing playlist {mpls_path}: {error}"
            )
            return None
        play_items = getattr(playlist_data, "play_items", None)
        if not play_items:
            logger.debug(f"[yellow]  No play_items found in {file_name}")
            return None
        return cls._built_bdinfo_playlist(
            file_name,
            mpls_path,
            cast(Sequence[Any], play_items),
            stream_directory,
            meta,
        )

    @classmethod
    async def _append_valid_bdinfo_playlist(
        cls,
        valid: list[PlaylistInfo],
        file_name: str,
        playlists_path: Path,
        stream_directory: Path,
        meta: Meta,
    ) -> None:
        playlist = await cls._bdinfo_playlist_info(
            file_name, playlists_path, stream_directory, meta
        )
        if playlist is not None:
            valid.append(playlist)

    @classmethod
    async def _valid_bdinfo_playlists(
        cls, path: str, meta: Meta
    ) -> list[PlaylistInfo]:
        playlists_path = Path(path) / "PLAYLIST"
        if not playlists_path.exists():
            logger.info(
                f"[bold red]PLAYLIST directory not found for disc {path}"
            )
            return []
        logger.debug(f"[cyan]Parsing playlists from: {playlists_path}")
        stream_directory = Path(path) / "STREAM"
        valid: list[PlaylistInfo] = []
        for file_name in (value.name for value in playlists_path.iterdir()):
            await cls._append_valid_bdinfo_playlist(
                valid, file_name, playlists_path, stream_directory, meta
            )
        if not valid:
            logger.info(f"[bold red]No playlists found for disc {path}")
        return valid

    def _scored_bdinfo_playlists(
        self, valid_playlists: list[PlaylistInfo]
    ) -> list[tuple[PlaylistInfo, float]]:
        scored = [
            (playlist, self._calculate_playlist_score(playlist))
            for playlist in valid_playlists
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def _log_bdinfo_playlist_choices(
        self, playlists: list[PlaylistInfo]
    ) -> None:
        logger.info("[bold green]Available top playlists (by score):")
        for index, playlist in enumerate(playlists):
            duration = float(playlist["duration"])
            duration_str = (
                f"{int(duration // 3600)}h {int((duration % 3600) // 60)}m "
                f"{int(duration % 60)}s"
            )
            items = cast(list[PlaylistItem], playlist["items"])
            items_str = ", ".join(
                f"{Path(str(item['file'])).name} ({int(item['size']) // (1024 * 1024)} MB)"
                for item in items
            )
            score = self._calculate_playlist_score(playlist)
            logger.info(
                f"[{index}] {playlist['file']} - {duration_str} - score {score:.2f} - {items_str}"
            )

    @staticmethod
    def _bdinfo_selected_indices(value: str, size: int) -> list[int]:
        try:
            indices = [int(item) for item in value.split(",")]
        except ValueError:
            return []
        return [index for index in indices if 0 <= index < size]

    @staticmethod
    def _bdinfo_selection_result(
        value: str, top_playlists: list[PlaylistInfo]
    ) -> list[PlaylistInfo] | None:
        if value == "all":
            return top_playlists
        if value == "":
            return [top_playlists[0]]
        indices = DiscParse._bdinfo_selected_indices(value, len(top_playlists))
        if not indices:
            return None
        return [top_playlists[index] for index in indices]

    async def _interactive_bdinfo_playlists(
        self, top_playlists: list[PlaylistInfo]
    ) -> list[PlaylistInfo]:
        if len(top_playlists) == 1:
            logger.info(
                "[yellow]Only one playlist found. Automatically selecting."
            )
            return top_playlists
        while True:
            self._log_bdinfo_playlist_choices(top_playlists)
            logger.info(
                "[bold yellow]Enter playlist numbers separated by commas, 'ALL' to select all, or press Enter to select the top-scoring playlist:"
            )
            raw = await prompt_in_thread(
                cli_ui.ask_string, "Select playlists: "
            )
            selected = self._bdinfo_selection_result(
                (raw or "").strip().lower(), top_playlists
            )
            if selected is not None:
                return selected
            logger.info("[bold red]No valid selections. Please try again.")

    async def _selected_bdinfo_playlists(
        self,
        valid_playlists: list[PlaylistInfo],
        meta: Meta,
        use_largest: bool,
    ) -> list[PlaylistInfo]:
        scored = self._scored_bdinfo_playlists(valid_playlists)
        top = [playlist for playlist, _score in scored[:5]]
        if use_largest or (meta.unattended and not meta.unattended_confirm):
            best, score = scored[0]
            logger.info(
                f"[yellow]Auto-selecting best playlist using weighted scoring: {best['file']} ({score:.2f})"
            )
            return [best]
        return await self._interactive_bdinfo_playlists(top)

    @staticmethod
    def _linux_bdinfo_folder(machine: str) -> str:
        if machine in ("x86_64", "amd64"):
            return "linux/amd64"
        if machine in ("arm64", "aarch64"):
            return "linux/arm64"
        return "linux/arm"

    @classmethod
    def _bdinfo_relative_binary(cls, system: str, machine: str) -> str | None:
        if system == "linux":
            return f"bin/bdinfo/{cls._linux_bdinfo_folder(machine)}/bdinfo"
        if system == "darwin":
            folder = "macos/arm64" if machine == "arm64" else "macos/x86_64"
            return f"bin/bdinfo/{folder}/bdinfo"
        if system == "windows":
            return "bin/bdinfo/windows/x86_64/bdinfo.exe"
        return None

    @classmethod
    def _bundled_bdinfo_path(cls, base_dir: str) -> str | None:
        relative = cls._bdinfo_relative_binary(
            platform.system().lower(), platform.machine().lower()
        )
        if relative is None:
            return None
        path = Path(base_dir) / relative
        return str(path) if path.exists() else None

    def _bdinfo_binary(self, base_dir: str) -> str | None:
        configured = configured_binary("bdinfo_path", self.config)
        if configured:
            return configured
        bundled = self._bundled_bdinfo_path(base_dir)
        if bundled:
            return bundled
        return "bdinfo" if shutil.which("bdinfo") else None

    def _bdinfo_scan_command(
        self,
        base_dir: str,
        path: str,
        playlist: PlaylistInfo,
        report_path: Path,
    ) -> list[str] | None:
        binary = self._bdinfo_binary(base_dir)
        if binary is None:
            return None
        return [
            binary,
            path,
            "--playlist",
            str(playlist["file"]),
            "--reportfilename",
            str(report_path),
        ]

    async def _run_bdinfo_scan(
        self, command: list[str], playlist: PlaylistInfo, folder_id: str
    ) -> bool:
        try:
            returncode = await self._run_bdinfo_with_progress(
                command, f"bdinfo-scan-{folder_id}"
            )
        except Exception as error:
            logger.info(
                f"[bold red]Error scanning playlist {playlist['file']}: {error}"
            )
            return False
        if returncode == 0:
            return True
        logger.info(
            f"[bold red]BDInfo failed with return code {returncode}[/bold red]"
        )
        return False

    async def _bdinfo_report_path(
        self,
        base_dir: str,
        path: str,
        playlist: PlaylistInfo,
        report_path: Path,
        folder_id: str,
    ) -> Path | None:
        if report_path.exists():
            return report_path
        command = self._bdinfo_scan_command(
            base_dir, path, playlist, report_path
        )
        if command is None:
            logger.info(
                f"[bold red]go-bdinfo not found. Place it under {base_dir}/bin/bdinfo/ or install a system bdinfo binary[/bold red]"
            )
            return None
        if not await self._run_bdinfo_scan(command, playlist, folder_id):
            return None
        if report_path.is_file():
            return report_path
        logger.info(
            f"[bold red]go-bdinfo did not create report {report_path}[/bold red]"
        )
        return None

    @staticmethod
    def _parsed_bdinfo_report(text: str) -> tuple[str, str, str]:
        quick_parts = text.split("QUICK SUMMARY:", 2)
        files_parts = (
            quick_parts[0]
            .split("FILES:", 2)[1]
            .split("CHAPTERS:", 2)[0]
            .split("-------------")
        )
        summary = (
            quick_parts[1].rstrip(" \n").split("********************", 1)[0]
        )
        code_parts = text.split("[code]", 3)
        extended = code_parts[2].rstrip(" \n").split("FILES:", 1)[0]
        return (
            re.sub(r" +", " ", summary.strip()),
            re.sub(r" +", " ", extended.strip()),
            files_parts[1],
        )

    @staticmethod
    def _bdinfo_summary_paths(
        save_dir: Path, disc_index: int, playlist_index: int
    ) -> tuple[Path, Path]:
        suffix = "" if playlist_index == 0 else f"_{playlist_index}"
        return (
            save_dir / f"BD_SUMMARY_{disc_index:02d}{suffix}.txt",
            save_dir / f"BD_SUMMARY_EXT_{disc_index:02d}{suffix}.txt",
        )

    @classmethod
    async def _write_bdinfo_summaries(
        cls,
        save_dir: Path,
        disc_index: int,
        playlist_index: int,
        summary: str,
        extended: str,
    ) -> None:
        summary_path, extended_path = cls._bdinfo_summary_paths(
            save_dir, disc_index, playlist_index
        )
        await asyncio.to_thread(
            summary_path.write_text,
            summary,
            encoding="utf-8",
            errors="replace",
        )
        await asyncio.to_thread(
            extended_path.write_text,
            extended,
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _interactive_bdinfo_mode(meta: Meta) -> bool:
        return not meta.unattended or bool(meta.unattended_confirm)

    @classmethod
    async def _maybe_set_playlist_edition(
        cls,
        meta: Meta,
        selected: list[PlaylistInfo],
        index: int,
        playlist: PlaylistInfo,
        bdinfo: dict[str, Any],
    ) -> None:
        if len(selected) <= 1:
            return
        current_label = bdinfo.get("label", f"Playlist {index}")
        logger.info(
            f"[bold yellow]Current label for playlist {playlist['file']}: {current_label}"
        )
        if not cls._interactive_bdinfo_mode(meta):
            return
        logger.info(
            "[bold green]You can create a custom Edition for this playlist."
        )
        raw = await prompt_in_thread(
            cli_ui.ask_string,
            f"Enter a new Edition title for playlist {playlist['file']} (or press Enter to keep the current label): ",
        )
        edition = (raw or "").strip()
        if not edition:
            return
        bdinfo["edition"] = edition
        selected[index]["edition"] = edition
        logger.info(f"[bold green]Edition updated to: {edition}")

    @staticmethod
    def _simplified_valid_playlists(
        valid_playlists: list[PlaylistInfo],
    ) -> list[dict[str, Any]]:
        duration_map: dict[int, dict[str, Any]] = {}
        for playlist in valid_playlists:
            rounded = round(float(playlist["duration"]))
            if rounded not in duration_map:
                duration_map[rounded] = {
                    "file": playlist["file"],
                    "duration": playlist["duration"],
                }
        result = list(duration_map.values())
        result.sort(key=lambda item: float(item["duration"]), reverse=True)
        return result

    @classmethod
    def _store_bdinfo_playlist(
        cls,
        disc: dict[str, Any],
        index: int,
        summary: str,
        bdinfo: dict[str, Any],
        selected: list[PlaylistInfo],
        valid: list[PlaylistInfo],
        meta: Meta,
    ) -> None:
        if index != 0:
            disc[f"summary_{index}"] = summary
            disc[f"bdinfo_{index}"] = bdinfo
            return
        disc["summary"] = summary
        disc["bdinfo"] = bdinfo
        disc["playlists"] = selected
        if valid and meta.unattended and not meta.unattended_confirm:
            simplified = cls._simplified_valid_playlists(valid)
            disc["all_valid_playlists"] = simplified
            logger.debug(
                f"[cyan]Stored {len(simplified)} unique playlists by duration (from {len(valid)} total)"
            )

    async def _process_bdinfo_playlist(
        self,
        meta: Meta,
        disc: dict[str, Any],
        disc_index: int,
        playlist_index: int,
        playlist: PlaylistInfo,
        selected: list[PlaylistInfo],
        valid: list[PlaylistInfo],
        path: str,
        save_dir: Path,
        base_dir: str,
        folder_id: str,
    ) -> None:
        duration = float(playlist["duration"])
        logger.info(
            f"[bold green]Scanning playlist {playlist['file']} with duration "
            f"{int(duration // 3600)} hours {int((duration % 3600) // 60)} minutes "
            f"{int(duration % 60)} seconds"
        )
        playlist_number = str(playlist["file"]).replace(".mpls", "")
        report = save_dir / f"Disc{disc_index + 1}_{playlist_number}_FULL.txt"
        report = await self._bdinfo_report_path(
            base_dir, path, playlist, report, folder_id
        )
        if report is None or not report.is_file():
            logger.info(
                f"[bold red]No valid BDInfo file found for playlist {playlist_number}."
            )
            return
        try:
            text = await asyncio.to_thread(
                report.read_text, encoding="utf-8", errors="replace"
            )
            summary, extended, files = self._parsed_bdinfo_report(text)
            await self._write_bdinfo_summaries(
                save_dir, disc_index, playlist_index, summary, extended
            )
            bdinfo = self.parse_bdinfo(summary, files, path)
            await self._maybe_set_playlist_edition(
                meta, selected, playlist_index, playlist, bdinfo
            )
            self._store_bdinfo_playlist(
                disc,
                playlist_index,
                summary,
                bdinfo,
                selected,
                valid,
                meta,
            )
        except Exception:
            logger.info(traceback.format_exc())

    async def _process_bdinfo_disc(
        self,
        meta: Meta,
        disc: dict[str, Any],
        disc_index: int,
        save_dir: Path,
        base_dir: str,
        folder_id: str,
        use_largest: bool,
    ) -> None:
        path_value = disc.get("path")
        if not isinstance(path_value, str) or not path_value:
            return
        path = str(Path(path_value).resolve())
        self._record_missing_certificate(meta, disc, path)
        valid = await self._valid_bdinfo_playlists(path, meta)
        if not valid:
            return
        selected = await self._selected_bdinfo_playlists(
            valid, meta, use_largest
        )
        for index, playlist in enumerate(selected):
            await self._process_bdinfo_playlist(
                meta,
                disc,
                disc_index,
                index,
                playlist,
                selected,
                valid,
                path,
                save_dir,
                base_dir,
                folder_id,
            )

    @staticmethod
    def _primary_bdinfo(discs: list[dict[str, Any]]) -> dict[str, Any]:
        if not discs:
            return {}
        value = discs[0].get("bdinfo", {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    async def get_bdinfo(
        self,
        meta: Meta,
        discs: list[dict[str, Any]],
        folder_id: str,
        base_dir: str,
        meta_discs: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        use_largest = bool(
            int(self.config["DEFAULT"].get("use_largest_playlist", False))
        )
        save_dir = self._bdinfo_save_dir(base_dir, folder_id)
        for index, disc in enumerate(list(discs)):
            if (
                meta_discs
                and self._bdinfo_cached_summary(save_dir, index) is not None
            ):
                discs = meta_discs
                continue
            await self._process_bdinfo_disc(
                meta,
                disc,
                index,
                save_dir,
                base_dir,
                folder_id,
                use_largest,
            )
        return discs, self._primary_bdinfo(discs)

    @staticmethod
    def _bdinfo_file_parts(line: str) -> list[str] | None:
        parts = line.split()
        if len(parts) < 5:
            return None
        if parts[1].startswith("(") and ")" in parts[1]:
            return [f"{parts[0]} {parts[1]}", *parts[2:]]
        return parts

    @classmethod
    def _parse_bdinfo_file_line(cls, raw_line: str) -> dict[str, str] | None:
        line = raw_line.strip()
        if not line:
            return None
        try:
            parts = cls._bdinfo_file_parts(line)
            if parts is None:
                return None
            return {"file": parts[0], "length": parts[2]}
        except Exception as error:
            logger.info(
                f"Failed to process bdinfo line: {line} -> {error}",
                extra={"markup": False},
            )
            return None

    @classmethod
    def parse_bdinfo_files(cls, files: str) -> list[dict[str, str]]:
        """Parse the FILES section of the BDInfo input."""
        parsed: list[dict[str, str]] = []
        for raw_line in files.splitlines():
            item = cls._parse_bdinfo_file_line(raw_line)
            if item is not None:
                parsed.append(item)
        return parsed

    @staticmethod
    def _normalized_bdinfo_line(raw_line: str) -> str:
        value = raw_line.strip()
        if value.startswith("*"):
            value = raw_line.replace("*", "").strip()
        return value.lower()

    @staticmethod
    def _pad_parts(parts: list[str], minimum: int) -> list[str]:
        return [*parts, *("" for _ in range(max(0, minimum - len(parts))))]

    @classmethod
    def _parse_bdinfo_video_line(cls, raw_line: str) -> dict[str, Any]:
        parts = cls._pad_parts(raw_line.split(":", 1)[1].split("/", 12), 9)
        offset = 1 if "Eye" in parts[2].strip() else 0
        three_dim = parts[2].strip() if offset else ""
        return {
            "codec": parts[0].strip(),
            "bitrate": parts[1].strip(),
            "res": parts[offset + 2].strip(),
            "fps": parts[offset + 3].strip(),
            "aspect_ratio": parts[offset + 4].strip(),
            "profile": parts[offset + 5].strip(),
            "bit_depth": parts[offset + 6].strip(),
            "hdr_dv": parts[offset + 7].strip(),
            "color": parts[offset + 8].strip(),
            "3d": three_dim,
        }

    @classmethod
    def _parse_bdinfo_audio_line(cls, raw_line: str) -> dict[str, Any]:
        value = raw_line.split("(", 1)[0].strip()
        parts = cls._pad_parts(value.split(":", 1)[1].split("/"), 7)
        atmos = parts[2].strip() if "Atmos" in parts[2].strip() else ""
        offset = 1 if atmos else 0
        return {
            "language": parts[0].strip(),
            "codec": parts[1].strip(),
            "channels": parts[offset + 2].strip(),
            "sample_rate": parts[offset + 3].strip(),
            "bitrate": parts[offset + 4].strip(),
            "bit_depth": parts[offset + 5].strip(),
            "atmos_why_you_be_like_this": atmos,
        }

    @staticmethod
    def _apply_bdinfo_primary_scalars(
        bdinfo: dict[str, Any], line: str, raw_line: str
    ) -> bool:
        if line.startswith("playlist:"):
            bdinfo["playlist"] = (
                raw_line.split(":", 1)[1].split(".", 1)[0].strip()
            )
            return True
        if line.startswith("disc size:"):
            raw_size = (
                raw_line.split(":", 1)[1].split("bytes", 1)[0].replace(",", "")
            )
            bdinfo["size"] = float(raw_size) / float(1 << 30)
            return True
        return False

    @staticmethod
    def _apply_bdinfo_text_scalars(
        bdinfo: dict[str, Any], line: str, raw_line: str
    ) -> bool:
        if line.startswith("length:"):
            bdinfo["length"] = (
                raw_line.split(":", 1)[1].split(".", 1)[0].strip()
            )
            return True
        if line.startswith("disc title:"):
            bdinfo["title"] = raw_line.split(":", 1)[1]
            return True
        if line.startswith("disc label:"):
            bdinfo["label"] = raw_line.split(":", 1)[1]
            return True
        return False

    @classmethod
    def _apply_bdinfo_scalar(
        cls, bdinfo: dict[str, Any], line: str, raw_line: str
    ) -> bool:
        if cls._apply_bdinfo_primary_scalars(bdinfo, line, raw_line):
            return True
        return cls._apply_bdinfo_text_scalars(bdinfo, line, raw_line)

    @classmethod
    def _apply_bdinfo_track(
        cls, bdinfo: dict[str, Any], line: str, raw_line: str
    ) -> None:
        if line.startswith("video:"):
            cast(list[dict[str, Any]], bdinfo["video"]).append(
                cls._parse_bdinfo_video_line(raw_line)
            )
            return
        if line.startswith("audio:"):
            cast(list[dict[str, Any]], bdinfo["audio"]).append(
                cls._parse_bdinfo_audio_line(raw_line)
            )
            return
        if line.startswith("subtitle:"):
            subtitle = raw_line.split(":", 1)[1].split("/", 1)[0].strip()
            cast(list[str], bdinfo["subtitles"]).append(subtitle)

    @classmethod
    def parse_bdinfo(
        cls, bdinfo_input: str, files: str, path: str
    ) -> dict[str, Any]:
        bdinfo: dict[str, Any] = {
            "video": [],
            "audio": [],
            "subtitles": [],
            "path": path,
        }
        for raw_line in bdinfo_input.splitlines():
            line = cls._normalized_bdinfo_line(raw_line)
            if cls._apply_bdinfo_scalar(bdinfo, line, raw_line):
                continue
            cls._apply_bdinfo_track(bdinfo, line, raw_line)
        bdinfo["files"] = cls.parse_bdinfo_files(files)
        return bdinfo

    """
    Parse VIDEO_TS and get mediainfos
    """

    @staticmethod
    def _dvd_vob_sets() -> OrderedDict[str, list[str]]:
        files = sorted(p.name for p in Path.cwd().glob("VTS_*.VOB"))
        grouped: OrderedDict[str, list[str]] = OrderedDict()
        for file_name in files:
            trimmed = file_name[4:]
            grouped.setdefault(trimmed[:2], []).append(trimmed)
        return grouped

    @staticmethod
    def _standard_dvd_json(ifo_file: str) -> str:
        return MediaInfo.parse(ifo_file, output="JSON")

    async def _specialized_dvd_json(
        self, ifo_file: str, binary: str, env: dict[str, str] | None
    ) -> str | None:
        try:
            stdout, stderr, returncode = await self._run_specialized_mediainfo(
                binary, "--Output=JSON", ifo_file, env=env
            )
        except Exception as error:
            logger.info(
                f"[yellow]Error with DVD MediaInfo binary for JSON: {error!s}"
            )
            return None
        if returncode == 0 and stdout:
            return stdout.decode()
        logger.info(
            f"[yellow]Specialized MediaInfo failed for {ifo_file}, falling back to standard[/yellow]"
        )
        if stderr:
            logger.info(f"[red]MediaInfo stderr: {stderr.decode()}[/red]")
        return None

    async def _dvd_json_mediainfo(
        self,
        ifo_file: str,
        binary: str | None,
        env: dict[str, str] | None,
    ) -> str:
        if binary is None:
            return self._standard_dvd_json(ifo_file)
        specialized = await self._specialized_dvd_json(ifo_file, binary, env)
        return (
            specialized
            if specialized is not None
            else self._standard_dvd_json(ifo_file)
        )

    @staticmethod
    def _json_mapping(value: Any) -> dict[str, Any]:
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    @classmethod
    def _dvd_duration_from_json(cls, value: str) -> str:
        data = cls._json_mapping(json.loads(value))
        media = cls._json_mapping(data.get("media", {}))
        tracks = media.get("track", [])
        if not isinstance(tracks, list) or len(tracks) <= 1:
            logger.warning("Warning: Expected track[1] is missing.")
            return "Unknown"
        track = cls._json_mapping(cast(list[Any], tracks)[1])
        if not track:
            logger.warning("Warning: Expected track[1] is missing.")
            return "Unknown"
        return str(track.get("Duration", "Unknown"))

    async def _dvd_vob_set_duration(
        self,
        vob_set: list[str],
        binary: str | None,
        env: dict[str, str] | None,
    ) -> float | None:
        try:
            ifo_file = f"VTS_{vob_set[0][:2]}_0.IFO"
            value = self._dvd_duration_from_json(
                await self._dvd_json_mediainfo(ifo_file, binary, env)
            )
        except Exception as error:
            logger.info(f"Error processing VOB set: {error}")
            value = "Unknown"
        if value == "Unknown" or not value.replace(".", "", 1).isdigit():
            logger.info(f"Skipping VOB set due to invalid duration: {value}")
            return None
        return float(value)

    async def _dvd_main_set(
        self,
        grouped: OrderedDict[str, list[str]],
        binary: str | None,
        env: dict[str, str] | None,
    ) -> list[str]:
        main_set: list[str] = []
        main_duration = 0.0
        for vob_set in grouped.values():
            duration = await self._dvd_vob_set_duration(vob_set, binary, env)
            if duration is None:
                continue
            if not main_set or duration > main_duration * 1.10:
                main_set = vob_set
                main_duration = duration
        return main_set

    @staticmethod
    def _standard_dvd_string(filename: str) -> str:
        return MediaInfo.parse(filename, output="STRING", full=False).replace(
            "\r\n", "\n"
        )

    async def _specialized_dvd_string(
        self,
        filename: str,
        binary: str,
        env: dict[str, str] | None,
        label: str,
    ) -> str | None:
        try:
            stdout, stderr, returncode = await self._run_specialized_mediainfo(
                binary, filename, env=env
            )
        except Exception as error:
            logger.info(
                f"[yellow]Error with DVD MediaInfo binary for {label}: {error!s}"
            )
            return None
        if returncode == 0 and stdout:
            return stdout.decode().replace("\r\n", "\n")
        logger.info(
            f"[yellow]Specialized MediaInfo failed for {label}, falling back[/yellow]"
        )
        if stderr:
            logger.info(f"[red]MediaInfo stderr: {stderr.decode()}[/red]")
        return None

    async def _dvd_string_mediainfo(
        self,
        filename: str,
        binary: str | None,
        env: dict[str, str] | None,
        label: str,
    ) -> str:
        if binary is None:
            return self._standard_dvd_string(filename)
        specialized = await self._specialized_dvd_string(
            filename, binary, env, label
        )
        return (
            specialized
            if specialized is not None
            else self._standard_dvd_string(filename)
        )

    async def _apply_dvd_media_info(
        self,
        each: dict[str, Any],
        path: str,
        main_set: list[str],
        binary: str | None,
        env: dict[str, str] | None,
    ) -> None:
        number = main_set[0][:2]
        vob = f"{path}/VTS_{number}_1.VOB"
        ifo = f"{path}/VTS_{number}_0.IFO"
        each["vob"] = vob
        each["ifo"] = ifo
        vob_output = await self._dvd_string_mediainfo(
            Path(vob).name, binary, env, "VOB"
        )
        ifo_output = await self._dvd_string_mediainfo(
            Path(ifo).name, binary, env, "IFO"
        )
        each["vob_mi"] = vob_output
        each["vob_mi_full"] = vob_output
        each["ifo_mi"] = ifo_output
        each["ifo_mi_full"] = ifo_output

    @staticmethod
    def _apply_dvd_size(each: dict[str, Any]) -> None:
        size = sum(
            item.stat().st_size for item in Path().iterdir() if item.is_file()
        ) / float(1 << 30)
        each["disc_size"] = round(size, 2)
        each["size"] = "DVD5" if size <= 4.37 else "DVD9"

    async def _process_dvd_disc(
        self,
        each: dict[str, Any],
        binary: str | None,
        env: dict[str, str] | None,
    ) -> None:
        path = each.get("path")
        if not isinstance(path, str) or not path:
            return
        os.chdir(path)
        main_set = await self._dvd_main_set(self._dvd_vob_sets(), binary, env)
        each["main_set"] = main_set
        if not main_set:
            logger.info(
                f"[yellow]No valid DVD title set found for {path}; skipping disc.[/yellow]"
            )
            return
        try:
            await self._apply_dvd_media_info(each, path, main_set, binary, env)
        except Exception as error:
            logger.info(
                f"[yellow]Error using DVD MediaInfo binary, falling back to standard: {error}"
            )
            number = main_set[0][:2]
            vob_name = f"VTS_{number}_1.VOB"
            ifo_name = f"VTS_{number}_0.IFO"
            vob_output = MediaInfo.parse(
                vob_name, output="STRING", full=False
            ).replace("\r\n", "\n")
            ifo_output = MediaInfo.parse(
                ifo_name, output="STRING", full=False
            ).replace("\r\n", "\n")
            each["vob_mi"] = each["vob_mi_full"] = vob_output
            each["ifo_mi"] = each["ifo_mi_full"] = ifo_output
        self._apply_dvd_size(each)

    async def get_dvdinfo(
        self, discs: list[dict[str, Any]], base_dir: str | None = None
    ) -> list[dict[str, Any]]:
        config = self.setup_mediainfo_for_dvd(base_dir)
        binary, env = config if config else (None, None)
        for each in discs:
            await self._process_dvd_disc(each, binary, env)
        return discs

    @staticmethod
    def _hddvd_evo_files(path: str, playlist: dict[str, Any]) -> list[str]:
        clips = cast(list[dict[str, Any]], playlist.get("primaryClips", []))
        return [
            str(
                Path(
                    f"{path}/{Path(str(clip.get('src', '')).replace('.MAP', '.EVO')).name}"
                ).resolve()
            )
            for clip in clips
        ]

    @staticmethod
    def _existing_total_size(paths: list[str]) -> int:
        return sum(
            Path(value).stat().st_size
            for value in paths
            if Path(value).exists()
        )

    @classmethod
    def _valid_hddvd_playlists(
        cls, path: str, playlists: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for playlist in playlists:
            evo_files = cls._hddvd_evo_files(path, playlist)
            total_size = cls._existing_total_size(evo_files)
            if total_size <= 0:
                continue
            playlist["totalSize"] = total_size
            playlist["evoFiles"] = evo_files
            valid.append(playlist)
        return valid

    @staticmethod
    def _largest_hddvd_playlist(
        valid_playlists: list[dict[str, Any]], unattended: bool
    ) -> list[dict[str, Any]]:
        message = (
            "[yellow]Unattended mode: Auto-selecting the largest playlist."
            if unattended
            else "[yellow]Auto-selecting the largest playlist based on size."
        )
        logger.info(message)
        return [
            max(valid_playlists, key=lambda playlist: playlist["totalSize"])
        ]

    @staticmethod
    def _hddvd_playlist_line(index: int, playlist: dict[str, Any]) -> str:
        duration = playlist.get("titleDuration", "Unknown")
        title_number = playlist.get("titleNumber", "")
        total_size = float(playlist.get("totalSize", 0) or 0)
        info: list[str] = []
        playlist_id = playlist.get("id", "")
        description = playlist.get("description", "")
        if playlist_id:
            info.append(f"[yellow]ID:[/yellow] {playlist_id}")
        if description:
            info.append(f"[yellow]Description:[/yellow] {description}")
        info.append(
            f"[yellow]Size:[/yellow] {total_size / (1024 * 1024):.2f} MB"
        )
        suffix = f" ({', '.join(info)})"
        return (
            f"{index}: Duration: {duration} Playlist: {title_number}{suffix}"
        )

    @classmethod
    def _log_hddvd_playlists(
        cls, valid_playlists: list[dict[str, Any]]
    ) -> None:
        logger.info("[cyan]Available playlists:")
        for index, playlist in enumerate(valid_playlists, start=1):
            logger.info(cls._hddvd_playlist_line(index, playlist))

    @staticmethod
    def _parsed_hddvd_indices(value: str) -> list[int] | None:
        try:
            return [int(item) - 1 for item in value.split(",")]
        except ValueError:
            return None

    @classmethod
    def _hddvd_selection_indices(
        cls, value: str, size: int
    ) -> list[int] | None:
        indices = cls._parsed_hddvd_indices(value)
        if indices is None:
            return None
        if any(index < 0 or index >= size for index in indices):
            return None
        return indices

    @classmethod
    async def _interactive_hddvd_playlists(
        cls, valid_playlists: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        while True:
            cls._log_hddvd_playlists(valid_playlists)
            raw = await prompt_in_thread(
                cli_ui.ask_string,
                "Enter the number of the playlist you want to select: ",
            )
            indices = cls._hddvd_selection_indices(
                (raw or "").strip(), len(valid_playlists)
            )
            if indices is None:
                logger.info("[red]Invalid input. Please try again.")
                continue
            return [valid_playlists[index] for index in indices]

    @classmethod
    async def _select_hddvd_playlists(
        cls,
        valid_playlists: list[dict[str, Any]],
        meta: Meta,
        use_largest: bool,
    ) -> list[dict[str, Any]]:
        if use_largest:
            return cls._largest_hddvd_playlist(valid_playlists, False)
        if meta.unattended and not meta.unattended_confirm:
            return cls._largest_hddvd_playlist(valid_playlists, True)
        return await cls._interactive_hddvd_playlists(valid_playlists)

    @classmethod
    def _validated_hddvd_selection(
        cls, selected_playlists: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        for playlist in selected_playlists:
            evo_files = cast(list[str], playlist.get("evoFiles", []))
            if not evo_files or any(
                not Path(value).exists() for value in evo_files
            ):
                raise ValueError(
                    f"Expected EVO files for playlist {playlist.get('id', '')} do not exist."
                )
            playlist["totalSize"] = cls._existing_total_size(evo_files)
            playlist["evoFiles"] = evo_files
        return selected_playlists

    @staticmethod
    def _selected_evo_path(evo_files: list[str]) -> str:
        existing = [value for value in evo_files if Path(value).exists()]
        if not existing:
            raise FileNotFoundError("No selected .EVO files are accessible.")
        if len(existing) >= 2:
            return existing[1]
        return max(existing, key=os.path.getsize)

    def _base_hddvd_mediainfo(
        self, selected_playlist: dict[str, Any]
    ) -> tuple[str, str]:
        evo_files = cast(list[str], selected_playlist.get("evoFiles", []))
        selected_evo_path = self._selected_evo_path(evo_files)
        total_size = float(selected_playlist.get("totalSize", 0) or 0)
        output = MediaInfo.parse(
            selected_evo_path, output="STRING", full=False
        )
        output = re.sub(
            r"File size\s+:\s+[^\r\n]+",
            f"File size                                : {total_size / (1024**3):.2f} GiB",
            output,
        )
        output = re.sub(
            r"Duration\s+:\s+[^\r\n]+",
            f"Duration                                 : {self.format_duration(str(selected_playlist.get('titleDuration', '')))}",
            output,
        )
        return selected_evo_path, output

    @staticmethod
    def _language_entry(language: str) -> str:
        return f"\nLanguage                                 : {language}"

    @staticmethod
    def _matching_block_index(
        blocks: list[str], pattern: re.Pattern[str]
    ) -> int | None:
        for index, block in enumerate(blocks):
            if pattern.match(block):
                return index
        return None

    @staticmethod
    def _block_has_language(block: str, language: str) -> bool:
        if not language:
            return True
        return bool(re.search(rf"Language\s+:\s+{re.escape(language)}", block))

    @classmethod
    def _language_injected_block(
        cls, block: str, language: str, anchor: str
    ) -> str:
        anchor_index = block.find(anchor)
        if anchor_index == -1:
            return block
        insertion = block.find("\n", anchor_index)
        if insertion == -1:
            insertion = len(block)
        return (
            block[:insertion]
            + cls._language_entry(language)
            + block[insertion:]
        )

    @classmethod
    def _inject_block_language(
        cls,
        blocks: list[str],
        block_pattern: str,
        track_number: int,
        language: str,
        anchor: str,
    ) -> bool:
        pattern = re.compile(block_pattern.format(track=track_number))
        index = cls._matching_block_index(blocks, pattern)
        if index is None:
            return False
        block = blocks[index]
        if not cls._block_has_language(block, language):
            blocks[index] = cls._language_injected_block(
                block, language, anchor
            )
        return True

    @classmethod
    def _inject_audio_languages(
        cls, blocks: list[str], selected_playlist: dict[str, Any]
    ) -> None:
        tracks = selected_playlist.get("audioTracks", [])
        if not isinstance(tracks, list):
            return
        for raw_track in cast(list[Any], tracks):
            if not isinstance(raw_track, dict):
                continue
            track = cast(dict[str, Any], raw_track)
            number = int(track.get("track", "1"))
            language = str(track.get("language", ""))
            langcode = str(track.get("langcode", ""))
            logger.info(f"[Debug] Processing Audio Track: {number}")
            logger.info(f"        Language: {language}")
            logger.info(f"        Langcode: {langcode}")
            found = cls._inject_block_language(
                blocks,
                r"^\s*Audio #\s*{track}\b.*",
                number,
                language,
                "Compression mode",
            )
            if not found:
                logger.info(
                    f"[Debug] No matching MediaInfo block found for Audio Track {number}."
                )

    @classmethod
    def _inject_subtitle_languages(
        cls, blocks: list[str], selected_playlist: dict[str, Any]
    ) -> None:
        tracks = selected_playlist.get("subtitleTracks", [])
        if not isinstance(tracks, list):
            return
        for raw_track in cast(list[Any], tracks):
            if not isinstance(raw_track, dict):
                continue
            track = cast(dict[str, Any], raw_track)
            number = int(track.get("track", "1"))
            language = str(track.get("language", ""))
            langcode = str(track.get("langcode", ""))
            logger.info(f"[Debug] Processing Subtitle Track: {number}")
            logger.info(f"        Language: {language}")
            logger.info(f"        Langcode: {langcode}")
            found = cls._inject_block_language(
                blocks,
                r"^\s*Text #\s*{track}\b",
                number,
                language,
                "Format",
            )
            if not found:
                logger.info(
                    f"[Debug] No matching MediaInfo block found for Subtitle Track {number}."
                )

    def _apply_hddvd_mediainfo(
        self, each: dict[str, Any], selected_playlist: dict[str, Any]
    ) -> None:
        evo_path, output = self._base_hddvd_mediainfo(selected_playlist)
        blocks = output.replace("\r\n", "\n").split("\n\n")
        self._inject_audio_languages(blocks, selected_playlist)
        self._inject_subtitle_languages(blocks, selected_playlist)
        each["evo_mi"] = "\n\n".join(blocks)
        each["largest_evo"] = evo_path

    @classmethod
    def _hddvd_playlist_file(cls, path: str) -> str:
        playlist_path = Path(path) / "ADV_OBJ"
        files = [str(value) for value in playlist_path.glob("*.xpl")]
        logger.debug(f"Found {files} in {playlist_path}")
        if not files:
            raise FileNotFoundError(f"No .xpl files found in {playlist_path}")
        return files[0]

    async def _process_hddvd_playlist(
        self, each: dict[str, Any], meta: Meta, use_largest: bool
    ) -> None:
        path = str(each["path"])
        playlists = self.parse_hddvd_playlist(self._hddvd_playlist_file(path))
        valid = self._valid_hddvd_playlists(path, playlists)
        if not valid:
            raise ValueError(
                "No valid playlists found with accessible .EVO files."
            )
        selected = await self._select_hddvd_playlists(valid, meta, use_largest)
        selected = self._validated_hddvd_selection(selected)
        selected_playlist = selected[0]
        self._apply_hddvd_mediainfo(each, selected_playlist)
        meta.HDDVD_PLAYLIST = selected_playlist

    @staticmethod
    def _fallback_hddvd_evo(each: dict[str, Any], path: str) -> None:
        files = [value.name for value in Path.cwd().glob("*.EVO")]
        if not files:
            logger.info("No EVO files found in the directory.")
            return
        largest = max(files, key=lambda value: Path(value).stat().st_size)
        each["evo_mi"] = MediaInfo.parse(
            Path(largest).name, output="STRING", full=False
        )
        each["largest_evo"] = str(Path(f"{path}/{largest}").resolve())

    async def _process_hddvd_disc(
        self, each: dict[str, Any], meta: Meta, use_largest: bool
    ) -> None:
        path = each.get("path")
        if not isinstance(path, str) or not path:
            return
        os.chdir(path)
        try:
            await self._process_hddvd_playlist(each, meta, use_largest)
        except (
            FileNotFoundError,
            ValueError,
            ElementTree.ParseError,
        ) as error:
            logger.info(
                f"Playlist processing failed: {error}. Falling back to largest EVO file detection."
            )
            self._fallback_hddvd_evo(each, path)

    async def get_hddvd_info(
        self, discs: list[dict[str, Any]], meta: Meta
    ) -> list[dict[str, Any]]:
        use_largest = bool(
            int(self.config["DEFAULT"].get("use_largest_playlist", False))
        )
        for each in discs:
            await self._process_hddvd_disc(each, meta, use_largest)
        return discs

    def format_duration(self, timecode: str) -> str:
        parts = timecode.split(":")
        if len(parts) != 4:
            return "Unknown duration"

        try:
            hours, minutes, _seconds, _ = map(int, parts)
        except ValueError:
            return "Unknown duration"
        duration = ""
        if hours > 0:
            duration += f"{hours} h "
        if minutes > 0:
            duration += f"{minutes} min"
        return duration.strip()

    @staticmethod
    def _hddvd_namespace() -> dict[str, str]:
        return {"ns": "http://www.dvdforum.org/2005/HDDVDVideo/Playlist"}

    @staticmethod
    def _hddvd_title_data(title: Any, duration: str) -> dict[str, Any]:
        return {
            "titleNumber": title.get("titleNumber"),
            "id": title.get("id"),
            "description": title.get("description"),
            "titleDuration": duration,
            "displayName": title.get("displayName"),
            "onEnd": title.get("onEnd"),
            "alternativeSDDisplayMode": title.get("alternativeSDDisplayMode"),
            "primaryClips": [],
            "chapters": [],
            "audioTracks": [],
            "subtitleTracks": [],
            "applicationSegments": [],
        }

    @staticmethod
    def _hddvd_clip_audio(
        clip: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "track": audio.get("track"),
                "streamNumber": audio.get("streamNumber"),
                "mediaAttr": audio.get("mediaAttr"),
                "description": audio.get("description"),
            }
            for audio in clip.findall(".//ns:Audio", namespaces=namespace)
        ]

    @staticmethod
    def _hddvd_clip_subtitles(
        clip: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "track": subtitle.get("track"),
                "streamNumber": subtitle.get("streamNumber"),
                "mediaAttr": subtitle.get("mediaAttr"),
                "description": subtitle.get("description"),
            }
            for subtitle in clip.findall(
                ".//ns:Subtitle", namespaces=namespace
            )
        ]

    @classmethod
    def _hddvd_primary_clips(
        cls, title: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "src": clip.get("src"),
                "titleTimeBegin": clip.get("titleTimeBegin"),
                "titleTimeEnd": clip.get("titleTimeEnd"),
                "seamless": clip.get("seamless"),
                "audioTracks": cls._hddvd_clip_audio(clip, namespace),
                "subtitleTracks": cls._hddvd_clip_subtitles(clip, namespace),
            }
            for clip in title.findall(
                ".//ns:PrimaryAudioVideoClip", namespaces=namespace
            )
        ]

    @staticmethod
    def _hddvd_chapters(
        title: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "displayName": chapter.get("displayName"),
                "titleTimeBegin": chapter.get("titleTimeBegin"),
            }
            for chapter in title.findall(
                ".//ns:ChapterList/ns:Chapter", namespaces=namespace
            )
        ]

    @staticmethod
    def _hddvd_language(value: str) -> tuple[str, str]:
        short = value.split(":", 1)[0] if ":" in value else value
        return short, Language.get(short).display_name()

    @classmethod
    def _hddvd_audio_tracks(
        cls, title: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for audio in title.findall(
            ".//ns:TrackNavigationList/ns:AudioTrack", namespaces=namespace
        ):
            short, language = cls._hddvd_language(audio.get("langcode", ""))
            tracks.append(
                {
                    "track": audio.get("track"),
                    "langcode": short,
                    "language": language,
                    "description": audio.get("description"),
                    "selectable": audio.get("selectable"),
                }
            )
        return tracks

    @classmethod
    def _hddvd_subtitle_tracks(
        cls, title: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for subtitle in title.findall(
            ".//ns:TrackNavigationList/ns:SubtitleTrack", namespaces=namespace
        ):
            short, language = cls._hddvd_language(subtitle.get("langcode", ""))
            tracks.append(
                {
                    "track": subtitle.get("track"),
                    "langcode": short,
                    "language": language,
                    "selectable": subtitle.get("selectable"),
                }
            )
        return tracks

    @staticmethod
    def _hddvd_resources(
        segment: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "src": resource.get("src"),
                "size": resource.get("size"),
                "priority": resource.get("priority"),
                "multiplexed": resource.get("multiplexed"),
            }
            for resource in segment.findall(
                ".//ns:ApplicationResource", namespaces=namespace
            )
        ]

    @classmethod
    def _hddvd_application_segments(
        cls, title: Any, namespace: dict[str, str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "src": segment.get("src"),
                "titleTimeBegin": segment.get("titleTimeBegin"),
                "titleTimeEnd": segment.get("titleTimeEnd"),
                "sync": segment.get("sync"),
                "zOrder": segment.get("zOrder"),
                "resources": cls._hddvd_resources(segment, namespace),
            }
            for segment in title.findall(
                ".//ns:ApplicationSegment", namespaces=namespace
            )
        ]

    @classmethod
    def _parsed_hddvd_title(
        cls, title: Any, namespace: dict[str, str]
    ) -> dict[str, Any] | None:
        duration = title.get("titleDuration", "00:00:00:00")
        if cls.timecode_to_seconds(duration) <= 600:
            return None
        data = cls._hddvd_title_data(title, duration)
        data["primaryClips"] = cls._hddvd_primary_clips(title, namespace)
        data["chapters"] = cls._hddvd_chapters(title, namespace)
        data["audioTracks"] = cls._hddvd_audio_tracks(title, namespace)
        data["subtitleTracks"] = cls._hddvd_subtitle_tracks(title, namespace)
        data["applicationSegments"] = cls._hddvd_application_segments(
            title, namespace
        )
        return data

    @classmethod
    def parse_hddvd_playlist(cls, file_path: str) -> list[dict[str, Any]]:
        titles: list[dict[str, Any]] = []
        try:
            root = ElementTree.parse(file_path).getroot()
            if root is None:
                return titles
            namespace = cls._hddvd_namespace()
            for title in root.findall(".//ns:Title", namespaces=namespace):
                parsed = cls._parsed_hddvd_title(title, namespace)
                if parsed is not None:
                    titles.append(parsed)
        except ElementTree.ParseError as error:
            logger.info(
                f"Error parsing XPL file: {error}", extra={"markup": False}
            )
        return titles

    @staticmethod
    def timecode_to_seconds(timecode: str) -> int:
        parts = timecode.split(":")
        if len(parts) != 4:
            return 0
        try:
            hours, minutes, seconds, _frames = map(int, parts)
        except ValueError:
            return 0
        return hours * 3600 + minutes * 60 + seconds
