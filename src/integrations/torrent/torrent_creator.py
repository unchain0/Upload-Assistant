# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import fnmatch
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cli_ui
import torf
from rich.progress import BarColumn, TaskProgressColumn, TextColumn
from torf import Torrent

from src.domain_models.release import Meta
from src.integrations.filesystem.paths import CODE_DIR
from src.integrations.observability.console import progress_display
from src.integrations.observability.runtime_support import (
    console,
    is_cli_progress_suppressed,
    logger,
)
from src.integrations.runtime_tools.configured_binaries import (
    configured_binary,
)
from src.integrations.runtime_tools.mkbrr import MkbrrBinaryManager

PIECE_SIZE_MIN = 32 * 1024  # 32 KiB
PIECE_SIZE_MAX = 134_217_728  # 128 MiB
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".vtt", ".ssa", ".ass", ".idx")
_PIECE_SIZE_THRESHOLDS = (
    (60, 32 * 1024),
    (120, 64 * 1024),
    (240, 128 * 1024),
    (480, 256 * 1024),
    (960, 512 * 1024),
    (1920, 1024 * 1024),
    (3840, 2 * 1024 * 1024),
    (7680, 4 * 1024 * 1024),
    (15360, 8 * 1024 * 1024),
    (46080, 16 * 1024 * 1024),
    (92160, 32 * 1024 * 1024),
    (138240, 64 * 1024 * 1024),
)


@dataclass(frozen=True)
class _TorrentCreationPlan:
    path: str | os.PathLike[str]
    creation_filelist: list[str]
    include: list[str]
    exclude: list[str]
    is_subs: bool


def calculate_piece_size(
    total_size: int,
    min_size: int,
    max_size: int,
    meta: Meta,
    piece_size: int | None = None,
) -> int:
    return TorrentCreator.calculate_piece_size(
        total_size=total_size,
        min_size=min_size,
        max_size=max_size,
        meta=meta,
        piece_size=piece_size,
    )


class CustomTorrent(torf.Torrent):
    _progress_id: str
    _progress_label: str

    def __init__(self, meta: Meta, *args: Any, **kwargs: Any) -> None:
        self._meta = meta

        # Extract and store the precalculated piece size
        self._precalculated_piece_size: int | None = kwargs.pop(
            "piece_size", None
        )
        super().__init__(*args, **kwargs)

        # Set piece size directly
        if self._precalculated_piece_size is not None:
            self._piece_size = self._precalculated_piece_size
            self.metainfo["info"]["piece length"] = (
                self._precalculated_piece_size
            )

    @property
    def piece_size_min(self) -> int:
        return PIECE_SIZE_MIN

    @piece_size_min.setter
    def piece_size_min(self, piece_size_min: int | None) -> None:
        _ = piece_size_min
        return

    @property
    def piece_size_max(self) -> int:
        return PIECE_SIZE_MAX

    @piece_size_max.setter
    def piece_size_max(self, piece_size_max: int | None) -> None:
        _ = piece_size_max
        return

    @property
    def piece_size(self) -> int:
        return self._piece_size

    @piece_size.setter
    def piece_size(self, value: int | None) -> None:
        if self._precalculated_piece_size is not None:
            value = self._precalculated_piece_size
        if value is None:
            return

        self._piece_size = value
        self.metainfo["info"]["piece length"] = value

    def validate_piece_size(self, _meta: Meta | None = None) -> None:
        if self._precalculated_piece_size is not None:
            self._piece_size = self._precalculated_piece_size
            self.metainfo["info"]["piece length"] = (
                self._precalculated_piece_size
            )
            return


class TorrentCreator:
    # Limit concurrent torrent creation to avoid heavy parallel hashing
    _create_torrent_semaphore = asyncio.Semaphore(1)
    _create_torrent_inflight = 0
    _torf_start_time = time.time()

    @staticmethod
    def _piece_size_limit(piece_size: int | None) -> int:
        if not piece_size:
            return PIECE_SIZE_MAX
        try:
            return min(piece_size * 1024 * 1024, PIECE_SIZE_MAX)
        except TypeError, ValueError:
            return PIECE_SIZE_MAX

    @staticmethod
    def _piece_size_for_content(total_size_mib: float) -> int:
        for threshold_mib, size in _PIECE_SIZE_THRESHOLDS:
            if total_size_mib <= threshold_mib:
                return size
        return PIECE_SIZE_MAX

    @staticmethod
    def _tracker_piece_size_cap(meta: Meta, piece_size: int) -> int:
        capped_trackers = {"HDBITS", "PASSTHEPOPCORN"}
        if capped_trackers.intersection(meta.trackers):
            return min(piece_size, 16 * 1024 * 1024)
        return piece_size

    @classmethod
    def calculate_piece_size(
        cls,
        total_size: int,
        min_size: int,
        max_size: int,
        meta: Meta,
        piece_size: int | None = None,
    ) -> int:
        max_size = min(max_size, cls._piece_size_limit(piece_size))
        logger.debug(f"Content size: {total_size / (1024 * 1024):.2f} MiB")
        logger.debug(f"Max size: {max_size}")
        selected = cls._piece_size_for_content(total_size / (1024 * 1024))
        selected = cls._tracker_piece_size_cap(meta, selected)
        selected = max(min_size, min(selected, max_size))
        num_pieces = math.ceil(total_size / selected)
        logger.debug(f"Selected piece size: {selected / 1024:.2f} KiB")
        logger.debug(f"Number of pieces: {num_pieces}")
        return selected

    @staticmethod
    def _mkbrr_manual_patterns(allow_subs: bool) -> list[str]:
        patterns = ["*.nfo", "*.jpg", "*.png", "*.txt", "*.xml"]
        if not allow_subs:
            patterns[3:3] = ["*.srt", "*.sub", "*.vtt", "*.ssa", "*.ass"]
        return patterns

    @staticmethod
    def _excluded_filename(
        dirpath: str,
        filename: str,
        keep_set: set[str],
        manual_patterns: list[str],
    ) -> str | None:
        full_path = str((Path(dirpath) / filename).resolve())
        if full_path in keep_set:
            return None
        if any(
            fnmatch.fnmatch(filename, pattern) for pattern in manual_patterns
        ):
            return None
        return filename

    @classmethod
    def build_mkbrr_exclude_string(
        cls,
        root_folder: str,
        filelist: Sequence[str],
        allow_subs: bool = False,
    ) -> str:
        manual_patterns = cls._mkbrr_manual_patterns(allow_subs)
        keep_set = {str(Path(filename).resolve()) for filename in filelist}
        exclude_files = {
            excluded
            for dirpath, _, filenames in os.walk(root_folder)
            for filename in filenames
            if (
                excluded := cls._excluded_filename(
                    dirpath, filename, keep_set, manual_patterns
                )
            )
            is not None
        }
        return ",".join(sorted(exclude_files) + manual_patterns)

    @staticmethod
    def _creation_filelist(meta: Meta, is_subs: bool) -> list[str]:
        files = list(meta.filelist)
        if is_subs and meta.subtitle_files:
            files.extend(meta.subtitle_files)
        return files

    @staticmethod
    def _media_default_filters(meta: Meta) -> tuple[list[str], list[str]]:
        if meta.is_disc:
            return [], []
        return ["*.mkv", "*.mp4", "*.ts"], ["*.*", "*sample.mkv", "!sample*.*"]

    @staticmethod
    def _folder_file_includes(
        path: str | os.PathLike[str], creation_filelist: list[str]
    ) -> list[str]:
        folder_name = Path(str(path)).name
        return [
            f"{folder_name}/{Path(filename).name}"
            for filename in creation_filelist
        ]

    @staticmethod
    def _keep_nfo_filters(
        meta: Meta, output_filename: str
    ) -> tuple[list[str], list[str]] | None:
        if not meta.keep_nfo or "BASE" in output_filename:
            return None
        logger.info(
            "--keep-nfo was specified. Including NFO files in torrent."
        )
        meta.mkbrr = False
        return ["*.mkv", "*.mp4", "*.ts", "*.nfo"], ["*.*", "*sample.mkv"]

    @classmethod
    def _keep_folder_filters(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
        creation_filelist: list[str],
    ) -> tuple[list[str], list[str]]:
        logger.info(
            "--keep-folder was specified. Using complete folder for torrent creation."
        )
        nfo_filters = cls._keep_nfo_filters(meta, output_filename)
        if nfo_filters is not None:
            return nfo_filters
        if meta.tv_pack:
            return [], []
        return cls._folder_file_includes(path, creation_filelist), [
            "*",
            "*/**",
        ]

    @staticmethod
    def _is_primary_video_filename(filename: str) -> bool:
        lower = filename.lower()
        return not lower.endswith("sample.mkv") or "!sample" in lower

    @classmethod
    def _directory_video_files(cls, path: str | os.PathLike[str]) -> list[str]:
        root = Path(os.fspath(path))
        candidates = [
            file.name
            for extension in ("*.mkv", "*.mp4", "*.ts")
            for file in root.glob(extension)
        ]
        return [
            str((root / filename).resolve())
            for filename in candidates
            if cls._is_primary_video_filename(filename)
        ]

    @classmethod
    def _directory_content_filters(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        creation_filelist: list[str],
        is_subs: bool,
    ) -> tuple[str | os.PathLike[str], list[str], list[str]]:
        if meta.tv_pack:
            return (
                path,
                cls._folder_file_includes(path, creation_filelist),
                ["*", "*/**"],
            )
        files = cls._directory_video_files(path)
        resolved_path = (
            meta.filelist[0] if len(files) == 1 and not is_subs else path
        )
        include, exclude = cls._media_default_filters(meta)
        return resolved_path, include, exclude

    @classmethod
    def _directory_filters(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
        creation_filelist: list[str],
        is_subs: bool,
    ) -> tuple[str | os.PathLike[str], list[str], list[str]]:
        nfo_filters = cls._keep_nfo_filters(meta, output_filename)
        if nfo_filters is not None and not meta.is_disc:
            include, exclude = nfo_filters
            return path, include, exclude
        if meta.is_disc:
            return path, [], []
        return cls._directory_content_filters(
            meta, path, creation_filelist, is_subs
        )

    @staticmethod
    def _subtitle_root_filters(
        path: str | os.PathLike[str], creation_filelist: list[str]
    ) -> tuple[list[str], list[str]]:
        root = Path(path).resolve()
        folder_name = root.name
        include: list[str] = []
        for selected_file in creation_filelist:
            try:
                relative_file = Path(selected_file).resolve().relative_to(root)
            except ValueError:
                continue
            include.append(f"{folder_name}/{relative_file.as_posix()}")
        return include, ["*", "*/**"]

    @staticmethod
    def _non_media_path(
        meta: Meta, path: str | os.PathLike[str]
    ) -> str | os.PathLike[str]:
        if meta.isdir and len(meta.filelist) == 1 and not meta.keep_folder:
            return meta.filelist[0]
        return path

    @classmethod
    def _media_creation_plan(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
        creation_filelist: list[str],
        is_subs: bool,
    ) -> _TorrentCreationPlan:
        if meta.keep_folder:
            include, exclude = cls._keep_folder_filters(
                meta, path, output_filename, creation_filelist
            )
            return _TorrentCreationPlan(
                path, creation_filelist, include, exclude, is_subs
            )
        if meta.isdir:
            path, include, exclude = cls._directory_filters(
                meta, path, output_filename, creation_filelist, is_subs
            )
            return _TorrentCreationPlan(
                path, creation_filelist, include, exclude, is_subs
            )
        if is_subs:
            include, exclude = cls._subtitle_root_filters(
                path, creation_filelist
            )
            return _TorrentCreationPlan(
                path, creation_filelist, include, exclude, is_subs
            )
        include, exclude = cls._media_default_filters(meta)
        return _TorrentCreationPlan(
            path, creation_filelist, include, exclude, is_subs
        )

    @classmethod
    def _creation_plan(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
    ) -> _TorrentCreationPlan:
        is_subs = "BASE_SUBS" in output_filename
        creation_filelist = cls._creation_filelist(meta, is_subs)
        if is_subs and Path(path).is_file():
            path = Path(path).parent
        if meta.category not in ("MOVIE", "TV"):
            path = cls._non_media_path(meta, path)
            return _TorrentCreationPlan(
                path, creation_filelist, [], [], is_subs
            )
        return cls._media_creation_plan(
            meta, path, output_filename, creation_filelist, is_subs
        )

    @staticmethod
    def _torrent_output_path(meta: Meta, output_filename: str) -> Path:
        return (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"{output_filename}.torrent"
        )

    @staticmethod
    def _mkbrr_piece_power(piece_size: int) -> int | None:
        try:
            max_size_bytes = piece_size * 1024 * 1024
            return min(27, max(16, math.floor(math.log2(max_size_bytes))))
        except ValueError, TypeError:
            return None

    @classmethod
    def _append_requested_piece_args(
        cls, command: list[str], piece_size: int
    ) -> None:
        power = cls._mkbrr_piece_power(piece_size)
        if power is None:
            logger.warning(
                "[yellow]Warning: Invalid max_piece_size value, using default piece length"
            )
            return
        command.extend(["-l", str(power)])
        logger.info(
            f"[yellow]Setting mkbrr piece length to 2^{power} ({(2**power) / (1024 * 1024):.2f} MiB)"
        )

    @staticmethod
    def _needs_default_mkbrr_max(meta: Meta, tracker_url: str | None) -> bool:
        capped = {"HDBITS", "PASSTHEPOPCORN"}.intersection(meta.trackers)
        return not tracker_url and not capped

    @classmethod
    def _append_mkbrr_piece_args(
        cls,
        command: list[str],
        meta: Meta,
        piece_size: int,
        tracker_url: str | None,
    ) -> None:
        if piece_size and not tracker_url:
            cls._append_requested_piece_args(command, piece_size)
            return
        if cls._needs_default_mkbrr_max(meta, tracker_url):
            command.extend(["-m", "27"])

    @staticmethod
    def _append_mkbrr_tracker_arg(
        command: list[str], tracker_url: str | None
    ) -> None:
        if tracker_url:
            command.extend(["-t", tracker_url])

    @staticmethod
    def _append_mkbrr_worker_arg(command: list[str], meta: Meta) -> None:
        if meta.mkbrr_threads != "0":
            command.extend(["--workers", str(meta.mkbrr_threads)])

    @classmethod
    def _append_mkbrr_exclude_arg(
        cls, command: list[str], meta: Meta, plan: _TorrentCreationPlan
    ) -> None:
        if meta.is_disc or meta.category not in ("MOVIE", "TV"):
            return
        exclude_str = cls.build_mkbrr_exclude_string(
            str(plan.path), plan.creation_filelist, allow_subs=plan.is_subs
        )
        command.extend(["--exclude", exclude_str])

    @classmethod
    def _mkbrr_command(
        cls,
        meta: Meta,
        plan: _TorrentCreationPlan,
        mkbrr_binary: str,
        output_path: Path,
        tracker_url: str | None,
        piece_size: int,
    ) -> list[str]:
        command = [mkbrr_binary, "create", os.fspath(plan.path)]
        cls._append_mkbrr_tracker_arg(command, tracker_url)
        if meta.randomized >= 1:
            command.append("-e")
        cls._append_mkbrr_piece_args(command, meta, piece_size, tracker_url)
        cls._append_mkbrr_worker_arg(command, meta)
        cls._append_mkbrr_exclude_arg(command, meta, plan)
        command.extend(["-o", str(output_path)])
        return command

    @staticmethod
    def _mkbrr_hash_progress(line: str) -> tuple[str, int] | None:
        match = re.search(
            r"Hashing pieces.*?\[(\d+(?:\.\d+)? (?:G|M)(?:B|iB)/s)\]\s+(\d+)%",
            line,
        )
        if match is None:
            return None
        return match.group(1), int(match.group(2))

    @staticmethod
    def _mkbrr_eta(line: str, pieces_done: int, started: float) -> str:
        eta_match = re.search(r"\[(\d+)s:(\d+)s\]", line)
        if eta_match:
            return time.strftime("%M:%S", time.gmtime(int(eta_match.group(2))))
        if pieces_done <= 0:
            return "--:--"
        elapsed = time.time() - started
        estimated_total = elapsed / (pieces_done / 100)
        remaining = int(max(0.0, estimated_total - elapsed))
        return time.strftime("%M:%S", time.gmtime(remaining))

    @classmethod
    def _update_mkbrr_progress(
        cls, line: str, progress: Any, task: Any, started: float
    ) -> None:
        parsed = cls._mkbrr_hash_progress(line)
        if parsed is None:
            return
        speed, pieces_done = parsed
        eta = cls._mkbrr_eta(line, pieces_done, started)
        progress.update(
            task,
            description=f"mkbrr hashing... {speed} | ETA: {eta}",
            completed=pieces_done,
        )

    @classmethod
    def _consume_mkbrr_output(
        cls,
        stdout: Any,
        progress: Any,
        task: Any,
        started: float,
        debug: bool,
    ) -> None:
        for raw_line in stdout:
            line = raw_line.strip()
            cls._update_mkbrr_progress(line, progress, task, started)
            if debug and "Wrote" in line and ".torrent" in line:
                logger.info(f"[bold cyan]{line}")

    @staticmethod
    def _finish_mkbrr_progress(
        result: int, output_path: Path, progress: Any, task: Any
    ) -> None:
        if result == 0 and output_path.exists():
            progress.update(task, completed=100)

    @classmethod
    def _run_mkbrr_process(
        cls, command: list[str], output_path: Path, debug: bool
    ) -> int:
        process = subprocess.Popen(  # noqa: S603  # nosemgrep: dangerous-subprocess-use-audit
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if process.stdout is None:
            return process.wait()
        with progress_display(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=False,
            disable=False,
        ) as progress:
            task = progress.add_task("mkbrr hashing...", total=100)
            cls._consume_mkbrr_output(
                process.stdout, progress, task, time.time(), debug
            )
            result = process.wait()
            cls._finish_mkbrr_progress(result, output_path, progress, task)
            return result

    @classmethod
    def _validated_mkbrr_binary(
        cls, meta: Meta, plan: _TorrentCreationPlan
    ) -> str:
        if not Path(plan.path).exists():
            raise ValueError(f"Path does not exist: {plan.path}")
        binary = cls.get_mkbrr_path(meta)
        if not Path(binary).exists():
            raise FileNotFoundError(f"mkbrr binary not found: {binary}")
        if not sys.platform.startswith("win"):
            with contextlib.suppress(Exception):
                Path(binary).chmod(0o700)
        return binary

    @staticmethod
    def _validate_mkbrr_result(result: int, output_path: Path) -> None:
        if result != 0:
            raise RuntimeError(f"mkbrr exited with status code {result}")
        if not output_path.exists():
            raise FileNotFoundError(
                f"Expected torrent file {output_path} was not created"
            )

    @classmethod
    async def _execute_mkbrr(
        cls,
        meta: Meta,
        plan: _TorrentCreationPlan,
        output_filename: str,
        tracker_url: str | None,
        piece_size: int,
    ) -> str:
        binary = cls._validated_mkbrr_binary(meta, plan)
        output_path = cls._torrent_output_path(meta, output_filename)
        command = cls._mkbrr_command(
            meta, plan, binary, output_path, tracker_url, piece_size
        )
        logger.debug(f"[cyan]mkbrr cmd: {command}")
        result = await asyncio.to_thread(
            cls._run_mkbrr_process, command, output_path, meta.debug
        )
        cls._validate_mkbrr_result(result, output_path)
        return str(output_path)

    @classmethod
    async def _try_mkbrr(
        cls,
        meta: Meta,
        plan: _TorrentCreationPlan,
        output_filename: str,
        tracker_url: str | None,
        piece_size: int,
    ) -> str | None:
        if not meta.mkbrr:
            return None
        try:
            return await cls._execute_mkbrr(
                meta, plan, output_filename, tracker_url, piece_size
            )
        except Exception as error:
            logger.info(f"[bold red]Error using mkbrr: {error!s}")
            logger.info("[yellow]Falling back to CustomTorrent method")
            meta.mkbrr = False
            return None

    @staticmethod
    def _directory_size(root: Path) -> int:
        total = 0
        for directory, _dirs, filenames in os.walk(root):
            for filename in filenames:
                file = Path(directory) / filename
                if file.is_file():
                    total += file.stat().st_size
        return total

    @classmethod
    def _path_size(cls, path: str | os.PathLike[str]) -> int:
        root = Path(path)
        if root.is_file():
            return root.stat().st_size
        return cls._directory_size(root) if root.is_dir() else 0

    @staticmethod
    def _selected_subtitle_paths(
        root: Path, subtitle_files: list[str]
    ) -> list[str]:
        selected: list[str] = []
        for subtitle_file in subtitle_files:
            try:
                selected.append(
                    Path(subtitle_file).resolve().relative_to(root).as_posix()
                )
            except ValueError:
                logger.warning(
                    f"[yellow]Selected subtitle is outside torrent root and will be skipped: {subtitle_file}"
                )
        return selected

    @classmethod
    def _custom_include(
        cls, meta: Meta, plan: _TorrentCreationPlan
    ) -> list[str]:
        include = list(plan.include)
        if (
            not plan.is_subs
            or meta.is_disc
            or meta.category not in ("TV", "MOVIE")
        ):
            return include
        selected = cls._selected_subtitle_paths(
            Path(plan.path).resolve(), list(meta.subtitle_files)
        )
        return list(dict.fromkeys([*include, *selected]))

    @classmethod
    def _custom_torrent(
        cls,
        meta: Meta,
        plan: _TorrentCreationPlan,
        piece_size: int,
    ) -> CustomTorrent:
        return CustomTorrent(
            meta=meta,
            path=plan.path,
            trackers=["https://fake.tracker"],
            source="UA",
            private=True,
            exclude_globs=plan.exclude,
            include_globs=cls._custom_include(meta, plan),
            creation_date=datetime.now(UTC),
            comment=f"{meta.ua_name} (fork)",
            created_by=f"{meta.ua_name} (fork)",
            piece_size=piece_size,
        )

    @classmethod
    async def _generate_custom_torrent(
        cls,
        torrent: CustomTorrent,
        meta: Meta,
        plan: _TorrentCreationPlan,
        output_filename: str,
    ) -> None:
        output_path = cls._torrent_output_path(meta, output_filename)

        def generate() -> None:
            torrent.generate(callback=cls.torf_cb, interval=5)
            torrent.write(output_path, overwrite=True)
            torrent.verify_filesize(plan.path)

        await asyncio.to_thread(generate)

    @staticmethod
    def _log_custom_torrent_created(
        meta: Meta, output_filename: str, started: float
    ) -> None:
        elapsed = time.time() - started
        formatted = time.strftime("%H:%M:%S", time.gmtime(elapsed))
        output_path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"{output_filename}.torrent"
        )
        size_kib = output_path.stat().st_size / 1024
        logger.debug("")
        logger.debug(f"[bold green]torrent created in {formatted}")
        logger.debug(f"[green]Torrent file size: {size_kib:.2f} KB")

    @classmethod
    async def _create_custom_torrent(
        cls,
        meta: Meta,
        plan: _TorrentCreationPlan,
        output_filename: str,
        requested_piece_size: int,
    ) -> CustomTorrent:
        started = time.time()
        initial_size = await asyncio.to_thread(cls._path_size, plan.path)
        selected_piece_size = cls.calculate_piece_size(
            initial_size,
            PIECE_SIZE_MIN,
            PIECE_SIZE_MAX,
            meta,
            piece_size=requested_piece_size,
        )
        torrent = cls._custom_torrent(meta, plan, selected_piece_size)
        await cls._generate_custom_torrent(
            torrent, meta, plan, output_filename
        )
        cls._log_custom_torrent_created(meta, output_filename, started)
        return torrent

    @classmethod
    async def _create_torrent_locked(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
        tracker_url: str | None,
        piece_size: int,
    ) -> str | Torrent:
        requested_piece_size = piece_size or meta.max_piece_size
        plan = cls._creation_plan(meta, path, output_filename)
        mkbrr_result = await cls._try_mkbrr(
            meta,
            plan,
            output_filename,
            tracker_url,
            requested_piece_size,
        )
        if mkbrr_result is not None:
            return mkbrr_result
        return await cls._create_custom_torrent(
            meta, plan, output_filename, requested_piece_size
        )

    @classmethod
    def _torrent_wait_started(cls) -> float | None:
        if not cls._create_torrent_semaphore.locked():
            return None
        logger.debug("[yellow]Waiting for create_torrent slot...[/yellow]")
        return time.time()

    @classmethod
    def _log_torrent_start(
        cls, meta: Meta, wait_started: float | None
    ) -> None:
        if not meta.debug:
            return
        waited = ""
        if wait_started is not None:
            waited = f" (waited {time.time() - wait_started:.2f}s)"
        logger.debug(
            f"[cyan]create_torrent start | in-flight={cls._create_torrent_inflight}{waited}[/cyan]"
        )

    @classmethod
    async def create_torrent(
        cls,
        meta: Meta,
        path: str | os.PathLike[str],
        output_filename: str,
        tracker_url: str | None = None,
        piece_size: int = 0,
    ) -> str | Torrent:
        wait_started = cls._torrent_wait_started()
        async with cls._create_torrent_semaphore:
            cls._create_torrent_inflight += 1
            try:
                cls._log_torrent_start(meta, wait_started)
                return await cls._create_torrent_locked(
                    meta,
                    path,
                    output_filename,
                    tracker_url or None,
                    piece_size,
                )
            finally:
                cls._create_torrent_inflight -= 1
                logger.debug(
                    f"[cyan]create_torrent end | in-flight={cls._create_torrent_inflight}[/cyan]"
                )

    @staticmethod
    def _torf_percentage(pieces_done: int, pieces_total: int) -> float:
        return (pieces_done / pieces_total) * 100 if pieces_total > 0 else 0.0

    @staticmethod
    def _torf_eta(elapsed: float, pieces_done: int, pieces_total: int) -> str:
        if pieces_done <= 0 or pieces_total <= 0:
            return "--:--"
        estimated_total = elapsed / (pieces_done / pieces_total)
        remaining = max(0.0, estimated_total - elapsed)
        return time.strftime("%M:%S", time.gmtime(remaining))

    @staticmethod
    def _torf_speed(torrent: Torrent, elapsed: float, pieces_done: int) -> str:
        if elapsed <= 0 or pieces_done <= 0:
            return "-- MB/s"
        piece_size_mib = (torrent.piece_size or 0) / (1024 * 1024)
        speed = (pieces_done * piece_size_mib) / elapsed
        return f"{speed:.2f} MB/s"

    @staticmethod
    def torf_cb(
        torrent: Torrent, _filepath: str, pieces_done: int, pieces_total: int
    ) -> None:
        if pieces_done == 0:
            TorrentCreator._torf_start_time = time.time()
        elapsed = time.time() - TorrentCreator._torf_start_time
        percentage = TorrentCreator._torf_percentage(pieces_done, pieces_total)
        eta = TorrentCreator._torf_eta(elapsed, pieces_done, pieces_total)
        speed = TorrentCreator._torf_speed(torrent, elapsed, pieces_done)
        if is_cli_progress_suppressed():
            return
        cli_ui.info_progress(
            f"Hashing... {speed} | ETA: {eta}", int(percentage), 100
        )

    @staticmethod
    def create_random_torrents(
        base_dir: str, uuid: str, num: int | str, path: str
    ) -> None:
        manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", Path(path).name)
        base_torrent = Torrent.read(
            f"{base_dir}{'/' + 'tmp' + '/'}{uuid}/BASE.torrent"
        )
        for i in range(1, int(num) + 1):
            new_torrent = base_torrent
            new_torrent.metainfo["info"]["entropy"] = random.randint(1, 999999)  # type: ignore  # nosec B311  # noqa: S311
            Torrent.copy(new_torrent).write(
                f"{base_dir}{'/' + 'tmp' + '/'}{uuid}/[RAND-{i}]{manual_name}.torrent",
                overwrite=True,
            )

    @staticmethod
    def _base_info_keys(info: Any) -> set[str]:
        keys = {"name", "piece length", "pieces", "private", "source"}
        if "files" in info:
            keys.add("files")
        elif "length" in info:
            keys.add("length")
        return keys

    @staticmethod
    def _prune_mapping(mapping: Any, allowed: set[str]) -> None:
        for key in list(mapping):
            if key not in allowed:
                mapping.pop(key, None)

    @classmethod
    def _sanitize_base_torrent(cls, base_torrent: Torrent) -> None:
        base_torrent.trackers = ["https://fake.tracker"]
        base_torrent.comment = "Upload-Assistant (fork)"
        base_torrent.created_by = "Upload-Assistant (fork)"
        info = base_torrent.metainfo["info"]
        cls._prune_mapping(info, cls._base_info_keys(info))
        cls._prune_mapping(
            base_torrent.metainfo,
            {
                "announce",
                "comment",
                "creation date",
                "created by",
                "encoding",
                "info",
                "imdb",
                "tmdb",
                "tvdb",
                "tvmaze",
                "mal",
                "douban",
                "igdb",
                "asin",
                "isbn",
            },
        )
        base_torrent.source = "L4G"
        base_torrent.private = True

    @staticmethod
    def _base_torrent_output_path(
        base_torrent: Torrent, base_dir: str, uuid: str
    ) -> Path:
        has_subs = any(
            Path(str(filename)).suffix.lower() in SUBTITLE_EXTENSIONS
            for filename in base_torrent.files
        )
        name = "BASE_SUBS.torrent" if has_subs else "BASE.torrent"
        return Path(base_dir) / "tmp" / uuid / name

    @classmethod
    async def create_base_from_existing_torrent(
        cls, torrentpath: str, base_dir: str, uuid: str
    ) -> str | None:
        if not Path(torrentpath).exists():
            return None
        base_torrent = Torrent.read(torrentpath)
        cls._sanitize_base_torrent(base_torrent)
        output_path = cls._base_torrent_output_path(
            base_torrent, base_dir, uuid
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Torrent.copy(base_torrent).write(output_path, overwrite=True)
        return str(output_path)

    @staticmethod
    def _windows_mkbrr_path(base_dir: Path, arch: str) -> Path:
        if arch not in {"x86_64", "amd64", "arm64", "aarch64"}:
            raise Exception("Unsupported Windows architecture")
        return base_dir / "windows" / "x86_64" / "mkbrr.exe"

    @staticmethod
    def _macos_mkbrr_path(base_dir: Path, arch: str) -> Path:
        architecture = "arm64" if "arm" in arch else "x86_64"
        return base_dir / "macos" / architecture / "mkbrr"

    @staticmethod
    def _linux_mkbrr_architecture(arch: str) -> str:
        aliases = (
            ("x86_64", "amd64"),
            ("aarch64", "arm64"),
            ("arm64", "arm64"),
            ("armv6", "armv6"),
        )
        for marker, target in aliases:
            if marker in arch:
                return target
        if "arm" in arch:
            return "arm"
        raise Exception("Unsupported Linux architecture")

    @classmethod
    def _platform_mkbrr_path(
        cls, base_dir: Path, system: str, arch: str
    ) -> Path:
        if system == "windows":
            return cls._windows_mkbrr_path(base_dir, arch)
        if system == "darwin":
            return cls._macos_mkbrr_path(base_dir, arch)
        if system == "linux":
            return (
                base_dir
                / "linux"
                / cls._linux_mkbrr_architecture(arch)
                / "mkbrr"
            )
        raise Exception("Unsupported OS")

    @staticmethod
    def _existing_mkbrr_path(meta: Meta) -> str | None:
        configured = configured_binary("mkbrr_path")
        if configured:
            return configured
        return MkbrrBinaryManager.find_existing_binary(
            CODE_DIR
        ) or MkbrrBinaryManager.find_existing_binary(meta.base_dir)

    @classmethod
    def get_mkbrr_path(cls, meta: Meta) -> str:
        """Determine the correct mkbrr binary based on OS and architecture."""
        existing = cls._existing_mkbrr_path(meta)
        if existing:
            return existing
        base_dir = Path(meta.base_dir) / "bin" / "mkbrr"
        binary_path = cls._platform_mkbrr_path(
            base_dir, platform.system().lower(), platform.machine().lower()
        )
        if not binary_path.exists():
            raise FileNotFoundError(f"mkbrr binary not found: {binary_path}")
        return str(binary_path)


def build_mkbrr_exclude_string(
    root_folder: str, filelist: Sequence[str], allow_subs: bool = False
) -> str:
    return TorrentCreator.build_mkbrr_exclude_string(
        root_folder, filelist, allow_subs
    )


async def create_torrent(
    meta: Meta,
    path: str | os.PathLike[str],
    output_filename: str,
    tracker_url: str | None = None,
    piece_size: int = 0,
) -> str | Torrent:
    return await TorrentCreator.create_torrent(
        meta=meta,
        path=path,
        output_filename=output_filename,
        tracker_url=tracker_url,
        piece_size=piece_size,
    )


def torf_cb(
    torrent: Torrent, filepath: str, pieces_done: int, pieces_total: int
) -> None:
    TorrentCreator.torf_cb(torrent, filepath, pieces_done, pieces_total)


def create_random_torrents(
    base_dir: str, uuid: str, num: int | str, path: str
) -> None:
    TorrentCreator.create_random_torrents(base_dir, uuid, num, path)


async def create_base_from_existing_torrent(
    torrentpath: str, base_dir: str, uuid: str
) -> str | None:
    return await TorrentCreator.create_base_from_existing_torrent(
        torrentpath, base_dir, uuid
    )


def get_mkbrr_path(meta: Meta) -> str:
    return TorrentCreator.get_mkbrr_path(meta)
