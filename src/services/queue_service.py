# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import contextlib
import json
import os
import re
import shlex
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import cli_ui
import click
from rich.markup import escape

from src.domain_models.errors import (
    NoWorkAvailableError,
    OperationAbortedError,
)
from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import ensure_temp_root
from src.integrations.observability.runtime_support import logger
from src.services.book_preparation import AUDIOBOOK_EXTENSIONS, BOOK_EXTENSIONS

type QueueItem = dict[str, Any]
type QueueList = list[str] | list[QueueItem]


def _dedupe_paths(paths: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in paths:
        current = str(value)
        if current in seen:
            continue
        seen.add(current)
        deduped.append(current)
    return deduped


def _invalid_queue_name(value: str) -> bool:
    if not value or value in {".", ".."}:
        return True
    if "\0" in value:
        return True
    return any(separator in value for separator in ("/", "\\"))


def _queue_log_path(tmp_dir: str | Path, queue_name: str, suffix: str) -> Path:
    normalized = queue_name.replace(" ", "_")
    if _invalid_queue_name(normalized):
        raise ValueError(f"Invalid queue name: {queue_name!r}")
    return Path(tmp_dir) / f"{normalized}{suffix}"


def _trusted_existing_queue_log(
    attributes: os.stat_result, *, windows: bool
) -> bool:
    is_reparse_point = bool(
        getattr(attributes, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    return (
        stat.S_ISREG(attributes.st_mode)
        and not is_reparse_point
        and (windows or attributes.st_uid == os.geteuid())
    )


def _directory_files(directory: str) -> list[Path]:
    return sorted(
        file_path.resolve()
        for file_path in Path(directory).rglob("*")
        if file_path.is_file()
    )


def _ebook_files(files: list[Path]) -> list[Path]:
    ebook_extensions = BOOK_EXTENSIONS - {".txt", ".html", ".htm"}
    return [
        file_path
        for file_path in files
        if file_path.suffix.casefold() in ebook_extensions
    ]


def _contains_audiobook(files: list[Path]) -> bool:
    return any(
        file_path.suffix.casefold() in AUDIOBOOK_EXTENSIONS
        for file_path in files
    )


def _ebook_files_in_directory(directory: str) -> tuple[list[Path], bool]:
    files = _directory_files(directory)
    return _ebook_files(files), _contains_audiobook(files)


def _split_multi_format_ebook_path(directory: str) -> list[str]:
    if not Path(directory).is_dir():
        return [directory]
    ebook_files, has_audiobook = _ebook_files_in_directory(directory)
    if len(ebook_files) <= 1 or has_audiobook:
        return [directory]
    logger.info(
        f"[cyan]Splitting {escape(Path(directory).name)} into {len(ebook_files)} separate ebook uploads, one per file.[/cyan]"
    )
    return [str(file_path) for file_path in ebook_files]


def _expanded_string_queue(queue: list[str]) -> list[str]:
    expanded: list[str] = []
    for item in queue:
        expanded.extend(_split_multi_format_ebook_path(item))
    return expanded


def _updated_split_queue_item(
    item: QueueItem, split_item_path: str
) -> QueueItem:
    expanded_item = dict(item)
    expanded_item["path"] = split_item_path
    args = expanded_item.get("args")
    if isinstance(args, list) and args:
        typed_args = cast(list[str], args)
        expanded_item["args"] = [split_item_path, *typed_args[1:]]
        if isinstance(expanded_item.get("line"), str):
            expanded_item["line"] = shlex.join(expanded_item["args"])
    return expanded_item


def _expanded_item_queue(queue: list[QueueItem]) -> list[QueueItem]:
    expanded_items: list[QueueItem] = []
    for item in queue:
        item_path = item.get("path")
        if not isinstance(item_path, str):
            expanded_items.append(item)
            continue
        split_paths = _split_multi_format_ebook_path(item_path)
        if len(split_paths) == 1:
            expanded_items.append(item)
            continue
        expanded_items.extend(
            _updated_split_queue_item(item, split_path)
            for split_path in split_paths
        )
    return expanded_items


def _expand_multi_format_ebook_directories(queue: QueueList) -> QueueList:
    if all(isinstance(item, str) for item in queue):
        return _expanded_string_queue(cast(list[str], queue))
    if all(isinstance(item, dict) for item in queue):
        return _expanded_item_queue(cast(list[QueueItem], queue))
    return queue


async def _read_json_file(path: str | Path) -> Any:
    content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    return json.loads(content)


async def _write_json_file(
    path: str | Path, data: Any, indent: int = 4
) -> None:
    content = json.dumps(data, indent=indent)

    def write_securely() -> None:
        destination = path if isinstance(path, Path) else Path(path)
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError:
            attributes = destination.lstat()
            if not _trusted_existing_queue_log(
                attributes, windows=os.name == "nt"
            ):
                raise PermissionError(
                    f"Refusing to replace untrusted queue log: {destination}"
                ) from None
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            )
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)

    await asyncio.to_thread(write_securely)


async def _read_text_lines(path: str) -> list[str]:
    content = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
    return content.splitlines()


class QueueManager:
    @staticmethod
    async def _load_site_search_results(path: Path) -> list[QueueItem] | None:
        if not path.exists():
            logger.info(f"[red]Search results file not found: {path}[/red]")
            return None
        try:
            value = await _read_json_file(path)
        except (json.JSONDecodeError, OSError) as error:
            logger.error(
                f"[red]Error loading search results file: {error}[/red]"
            )
            return None
        return cast(list[QueueItem], value)

    @staticmethod
    async def _load_processed_site_paths(path: Path) -> set[str]:
        if not path.exists():
            return set()
        try:
            return set(cast(list[str], await _read_json_file(path)))
        except (json.JSONDecodeError, OSError) as error:
            logger.warning(
                f"[yellow]Warning: Could not load processed files log: {error}[/yellow]"
            )
            return set()

    @staticmethod
    def _site_item_imdb_id(item: QueueItem) -> Any:
        try:
            return item.get("imdb_id")
        except KeyError:
            return 0

    @staticmethod
    def _site_item_is_queueable(
        path: Any,
        imdb_id: Any,
        processed_paths: set[str],
        seen_paths: set[str],
    ) -> bool:
        if not path or imdb_id is None:
            return False
        return path not in processed_paths and path not in seen_paths

    @classmethod
    def _site_queue_item(
        cls,
        item: QueueItem,
        tracker: str,
        processed_paths: set[str],
        seen_paths: set[str],
    ) -> QueueItem | None:
        path = item.get("path")
        imdb_id = cls._site_item_imdb_id(item)
        if not cls._site_item_is_queueable(
            path, imdb_id, processed_paths, seen_paths
        ):
            return None
        path_text = str(path)
        seen_paths.add(path_text)
        return {"path": path_text, "imdb_id": imdb_id, "tracker": tracker}

    @classmethod
    def _site_upload_queue(
        cls,
        search_results: list[QueueItem],
        tracker: str,
        processed_paths: set[str],
    ) -> list[QueueItem]:
        seen_paths: set[str] = set()
        queue: list[QueueItem] = []
        for item in search_results:
            queue_item = cls._site_queue_item(
                item, tracker, processed_paths, seen_paths
            )
            if queue_item is not None:
                queue.append(queue_item)
        return queue

    @staticmethod
    def _display_site_upload_queue(
        queue: list[QueueItem], tracker: str
    ) -> None:
        if not queue:
            return
        paths_only = [item["path"] for item in queue]
        md_text = "\n - ".join(escape(path) for path in paths_only)
        logger.info(
            "\n[bold green]Queuing these files for site upload:[/bold green]"
        )
        logger.info(f"- {md_text.rstrip()}\n\n")
        logger.info(f"[yellow]Tracker: {tracker}[/yellow]")
        logger.info("\n\n")

    @classmethod
    async def process_site_upload_queue(
        cls, meta: Meta, base_dir: str
    ) -> tuple[list[QueueItem], str | None]:
        site_upload = meta.site_upload
        if not site_upload:
            return [], None
        tmp_dir = ensure_temp_root(base_dir)
        search_results_file = _queue_log_path(
            tmp_dir, site_upload, "_search_results.json"
        )
        search_results = await cls._load_site_search_results(
            search_results_file
        )
        if search_results is None:
            return [], None
        processed_files_log = _queue_log_path(
            tmp_dir, site_upload, "_processed_paths.log"
        )
        processed_paths = await cls._load_processed_site_paths(
            processed_files_log
        )
        queue = cls._site_upload_queue(
            search_results, site_upload, processed_paths
        )
        logger.info(
            f"[cyan]Found {len(queue)} unprocessed items for {site_upload} upload[/cyan]"
        )
        cls._display_site_upload_queue(queue, site_upload)
        return queue, str(processed_files_log)

    @staticmethod
    async def process_site_upload_item(
        queue_item: Mapping[str, Any], meta: Meta
    ) -> str:
        # Set the tracker argument (-tk XXX)
        tracker = cast(str, queue_item["tracker"])
        meta.trackers = [tracker]

        # Set the IMDb ID
        imdb = queue_item.get("imdb_id", 0)
        meta.imdb_id = imdb

        # Return the path for processing
        return cast(str, queue_item["path"])

    @staticmethod
    async def save_processed_path(processed_files_log: str, path: str) -> None:
        processed_paths: set[str] = set()

        # Load existing processed paths
        if Path(processed_files_log).exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                processed_paths = set(
                    cast(list[str], await _read_json_file(processed_files_log))
                )

        # Add the new path
        processed_paths.add(path)

        # Save back to file
        try:
            Path(processed_files_log).parent.mkdir(parents=True, exist_ok=True)
            await _write_json_file(
                processed_files_log, list(processed_paths), indent=4
            )
        except OSError as e:
            logger.error(f"[red]Error saving processed path: {e}[/red]")

    @staticmethod
    async def get_log_file(base_dir: str, queue_name: str) -> str:
        """
        Returns the path to the log file for the given base directory and queue name.
        """
        return str(
            _queue_log_path(
                ensure_temp_root(base_dir), queue_name, "_processed_files.log"
            )
        )

    @staticmethod
    async def load_processed_files(log_file: str) -> set[str]:
        """
        Loads the list of processed files from the log file.
        """
        if Path(log_file).exists():
            return set(cast(list[str], await _read_json_file(log_file)))
        return set()

    @staticmethod
    def _normalized_queue_path(path: str | bytes) -> str:
        path_str = (
            path.decode("utf-8", errors="replace")
            if isinstance(path, bytes)
            else path
        )
        try:
            import unicodedata

            path_str = unicodedata.normalize("NFC", path_str)
        except Exception as error:
            logger.warning(
                f"[yellow]Warning: Path normalization failed for {path_str}: {error}[/yellow]"
            )
        return os.path.normpath(path_str)

    @staticmethod
    def _allowed_extensions_tuple(
        allowed_extensions: Sequence[str] | None,
    ) -> tuple[str, ...] | None:
        return tuple(allowed_extensions) if allowed_extensions else None

    @staticmethod
    def _matches_allowed_extensions(
        path: str | Path, allowed_extensions: tuple[str, ...] | None
    ) -> bool:
        return allowed_extensions is None or str(path).lower().endswith(
            allowed_extensions
        )

    @classmethod
    async def _gather_directory_entries(
        cls,
        normalized_path: str,
        allowed_extensions_tuple: tuple[str, ...] | None,
        allowed_extensions: Sequence[str] | None,
    ) -> list[str]:
        queue: list[str] = []
        try:
            for entry in os.scandir(normalized_path):
                queue.extend(
                    await cls._process_scandir_entry(
                        entry,
                        normalized_path,
                        allowed_extensions_tuple,
                        allowed_extensions,
                    )
                )
        except (OSError, PermissionError) as error:
            logger.error(
                f"[red]Error scanning directory {normalized_path}: {error}[/red]"
            )
        return queue

    @classmethod
    async def gather_files_recursive(
        cls,
        path: str | bytes,
        allowed_extensions: Sequence[str] | None = None,
    ) -> list[str]:
        """Gather files and first-level subfolders accepted by queue policy."""
        normalized_path = cls._normalized_queue_path(path)
        allowed_tuple = cls._allowed_extensions_tuple(allowed_extensions)
        if Path(normalized_path).is_dir():
            return await cls._gather_directory_entries(
                normalized_path, allowed_tuple, allowed_extensions
            )
        if Path(normalized_path).is_file():
            return (
                [normalized_path]
                if cls._matches_allowed_extensions(
                    normalized_path, allowed_tuple
                )
                else []
            )
        logger.info(f"[red]Invalid path: {normalized_path}[/red]")
        return []

    @classmethod
    async def _queueable_path(
        cls,
        path: str | Path,
        allowed_extensions_tuple: tuple[str, ...] | None,
        allowed_extensions: Sequence[str] | None,
    ) -> bool:
        candidate = Path(path)
        if candidate.is_dir():
            return await cls.should_include_directory(
                candidate, allowed_extensions
            )
        return candidate.is_file() and cls._matches_allowed_extensions(
            candidate, allowed_extensions_tuple
        )

    @classmethod
    async def _scandir_fallback_path(
        cls,
        entry: os.DirEntry[str],
        normalized_path: str,
        allowed_extensions_tuple: tuple[str, ...] | None,
        allowed_extensions: Sequence[str] | None,
    ) -> str | None:
        try:
            alt_path = Path(normalized_path) / entry.name
            if not alt_path.exists():
                return None
            if await cls._queueable_path(
                alt_path, allowed_extensions_tuple, allowed_extensions
            ):
                return str(alt_path)
        except Exception:
            return None
        return None

    @classmethod
    async def _process_scandir_entry(
        cls,
        entry: os.DirEntry[str],
        normalized_path: str,
        allowed_extensions_tuple: tuple[str, ...] | None,
        allowed_extensions: Sequence[str] | None,
    ) -> list[str]:
        try:
            entry_path = os.path.normpath(entry.path)
            if await cls._queueable_path(
                entry_path, allowed_extensions_tuple, allowed_extensions
            ):
                return [entry_path]
            return []
        except (OSError, UnicodeDecodeError, UnicodeError) as error:
            logger.warning(
                f"[yellow]Warning: Skipping entry due to encoding issue: {error}[/yellow]"
            )
            fallback = await cls._scandir_fallback_path(
                entry,
                normalized_path,
                allowed_extensions_tuple,
                allowed_extensions,
            )
            return [fallback] if fallback is not None else []

    @staticmethod
    def _is_disc_structure_entry(entry: os.DirEntry[str]) -> bool:
        return entry.is_dir() and entry.name.upper() in {"VIDEO_TS", "BDMV"}

    @classmethod
    def _directory_entry_is_allowed_file(
        cls,
        entry: os.DirEntry[str],
        allowed_extensions: tuple[str, ...] | None,
    ) -> bool:
        if not entry.is_file():
            return False
        return cls._matches_allowed_extensions(entry.name, allowed_extensions)

    @classmethod
    async def should_include_directory(
        cls,
        dir_path: str | Path,
        allowed_extensions: Sequence[str] | None = None,
    ) -> bool:
        """Return whether a directory contains queueable media or disc structure."""
        normalized_path = os.path.normpath(dir_path)
        allowed_tuple = cls._allowed_extensions_tuple(allowed_extensions)
        try:
            entries = list(os.scandir(normalized_path))
        except (OSError, PermissionError, UnicodeError) as error:
            logger.warning(
                f"[yellow]Warning: Could not scan directory {normalized_path}: {error}[/yellow]"
            )
            return False
        if any(cls._is_disc_structure_entry(entry) for entry in entries):
            return True
        return any(
            cls._directory_entry_is_allowed_file(entry, allowed_tuple)
            for entry in entries
        )

    @staticmethod
    def _split_path_boundary(current: str, next_part: str) -> bool:
        return (
            Path(current).exists()
            and not Path(f"{current} {next_part}").exists()
        )

    @classmethod
    async def _resolve_split_path(cls, path: str) -> list[str]:
        parts = path.split()
        if not parts:
            return []
        queue: list[str] = []
        current = parts[0]
        for next_part in parts[1:]:
            if cls._split_path_boundary(current, next_part):
                queue.append(current)
                current = next_part
            else:
                current = f"{current} {next_part}"
        if Path(current).exists():
            queue.append(current)
        else:
            logger.info(
                f"[red]Path: [bold red]{current}[/bold red] does not exist"
            )
        return queue

    @classmethod
    def _queue_candidate_allowed(
        cls, candidate: str | Path, allowed_extensions: tuple[str, ...] | None
    ) -> bool:
        path = Path(candidate)
        if path.is_dir():
            return True
        return path.is_file() and cls._matches_allowed_extensions(
            path, allowed_extensions
        )

    @classmethod
    def _filtered_queue_candidates(
        cls,
        candidates: Sequence[str | Path],
        allowed_extensions: tuple[str, ...] | None,
    ) -> list[str]:
        return [
            str(candidate)
            for candidate in candidates
            if cls._queue_candidate_allowed(candidate, allowed_extensions)
        ]

    @classmethod
    def _glob_queue_candidates(
        cls, path: str, allowed_extensions: tuple[str, ...] | None
    ) -> list[str]:
        parent_dir = Path(path).parent
        pattern = Path(path).name.replace("[", "[[]")
        return cls._filtered_queue_candidates(
            list(parent_dir.glob(pattern)), allowed_extensions
        )

    @classmethod
    async def resolve_queue_with_glob_or_split(
        cls,
        path: str,
        paths: Sequence[str],
        allowed_extensions: Sequence[str] | None = None,
    ) -> list[str]:
        """Resolve explicit paths, glob patterns, or split shell-like path text."""
        allowed_tuple = cls._allowed_extensions_tuple(allowed_extensions)
        parent_exists = Path(path).parent.exists()
        if parent_exists and len(paths) <= 1:
            queue = cls._glob_queue_candidates(path, allowed_tuple)
        elif parent_exists:
            queue = cls._filtered_queue_candidates(paths, allowed_tuple)
        else:
            split_paths = await cls._resolve_split_path(path)
            queue = cls._filtered_queue_candidates(split_paths, allowed_tuple)
        if queue:
            await cls.display_queue(queue, save_to_log=False)
        return queue

    @staticmethod
    def _safe_section_state(line: str, current: bool) -> bool:
        lowered = line.lower()
        if lowered == "safe":
            return True
        if lowered in {"danger", "risky"}:
            return False
        return current

    @staticmethod
    def _safe_file_location(line: str, safe_section: bool) -> str | None:
        if not safe_section or not line.startswith("File Location:"):
            return None
        match = re.search(r"File Location:\s*(.+)", line)
        return match.group(1).strip() if match else None

    @classmethod
    async def extract_safe_file_locations(cls, log_file: str) -> list[str]:
        """Extract file locations that appear inside a safe scanner section."""
        safe_section = False
        locations: list[str] = []
        for raw_line in await _read_text_lines(log_file):
            line = raw_line.strip()
            safe_section = cls._safe_section_state(line, safe_section)
            location = cls._safe_file_location(line, safe_section)
            if location is not None:
                locations.append(location)
        return locations

    @staticmethod
    def _queue_display_values(queue: Sequence[Any]) -> list[str]:
        values: list[str] = []
        for item in queue:
            if isinstance(item, dict):
                mapping = cast(dict[str, Any], item)
                values.append(
                    str(mapping.get("line") or mapping.get("path", ""))
                )
            else:
                values.append(str(item))
        return values

    @staticmethod
    def _log_queue_values(values: list[str]) -> None:
        md_text = "\n - ".join(escape(path) for path in values)
        logger.info("\n[bold green]Queuing these files:[/bold green]")
        logger.info(f"- {md_text.rstrip()}\n\n")
        logger.info("\n\n")

    @staticmethod
    async def _save_displayed_queue(
        values: list[str], base_dir: str, queue_name: str
    ) -> None:
        tmp_dir = ensure_temp_root(base_dir)
        log_file = _queue_log_path(tmp_dir, queue_name, "_queue.log")
        try:
            await _write_json_file(log_file, values, indent=4)
            logger.info(
                f"[bold green]Queue successfully saved to log file: {log_file}"
            )
        except Exception as error:
            logger.info(f"[bold red]Failed to save queue to log file: {error}")

    @classmethod
    async def display_queue(
        cls,
        queue: Sequence[Any],
        base_dir: str | None = None,
        queue_name: str | None = None,
        save_to_log: bool = True,
    ) -> None:
        """Display queued items and optionally persist the display representation."""
        values = cls._queue_display_values(queue)
        cls._log_queue_values(values)
        if save_to_log and base_dir and queue_name:
            await cls._save_displayed_queue(values, base_dir, queue_name)

    @staticmethod
    def _text_queue_mode(meta: Meta) -> str:
        if meta.unit3d:
            return "unit3d"
        if meta.paths_from_stdin:
            return "generic"
        return "manifest"

    @staticmethod
    def _named_or_generic_mode(meta: Meta) -> str:
        return "named" if meta.queue else "generic"

    @classmethod
    def _log_queue_mode(cls, meta: Meta) -> str:
        if meta.debug:
            return "debug"
        return cls._named_or_generic_mode(meta)

    @classmethod
    def _queue_mode(cls, path: str, meta: Meta) -> str:
        suffix = Path(path).suffix.lower()
        if suffix == ".txt":
            return cls._text_queue_mode(meta)
        if suffix == ".log":
            return cls._log_queue_mode(meta)
        return cls._named_or_generic_mode(meta)

    @classmethod
    async def _handle_site_upload_mode(
        cls, meta: Meta, base_dir: str
    ) -> tuple[QueueList, str | None]:
        logger.info(
            f"[bold yellow]Processing site upload queue for tracker: {meta.site_upload}[/bold yellow]"
        )
        site_queue, processed_log = await cls.process_site_upload_queue(
            meta, base_dir
        )
        if site_queue:
            meta.queue = f"{meta.site_upload}_upload"
            meta.site_upload_queue = True
            return site_queue, processed_log
        logger.info(
            f"[yellow]No unprocessed items found for {meta.site_upload} upload[/yellow]"
        )
        return [], None

    @staticmethod
    def _clean_manifest_arg(arg: str) -> str:
        quoted = len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in {'"', "'"}
        return arg[1:-1] if quoted else arg

    @classmethod
    def _parsed_manifest_args(cls, stripped: str) -> list[str] | None:
        try:
            args_list = shlex.split(stripped, posix=False)
        except ValueError as error:
            logger.error(
                f"[red]Error parsing line (shlex) in queue file: {stripped}. Error: {error}[/red]"
            )
            return None
        except Exception as error:
            logger.error(
                f"[red]Unexpected error processing line in queue file: {stripped}. Error: {error}[/red]"
            )
            return None
        return [cls._clean_manifest_arg(arg) for arg in args_list]

    @staticmethod
    def _manifest_item_is_processed(
        stripped: str,
        item_path: str,
        processed_files: set[str],
        seen_paths: set[str],
    ) -> bool:
        if stripped in processed_files or item_path in processed_files:
            return True
        return item_path in seen_paths

    @classmethod
    def _manifest_queue_item(
        cls,
        line: str,
        processed_files: set[str],
        seen_paths: set[str],
    ) -> QueueItem | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        cleaned_args = cls._parsed_manifest_args(stripped)
        if not cleaned_args:
            return None
        item_path = cleaned_args[0]
        if cls._manifest_item_is_processed(
            stripped, item_path, processed_files, seen_paths
        ):
            return None
        seen_paths.add(item_path)
        return {"path": item_path, "args": cleaned_args, "line": stripped}

    @classmethod
    async def _manifest_items(
        cls, path: str, processed_files: set[str]
    ) -> list[QueueItem]:
        seen_paths: set[str] = set()
        queue: list[QueueItem] = []
        for line in await _read_text_lines(path):
            item = cls._manifest_queue_item(line, processed_files, seen_paths)
            if item is not None:
                queue.append(item)
        return queue

    @staticmethod
    async def _save_manifest_queue_log(
        base_dir: str, queue_name: str, queue: list[QueueItem]
    ) -> None:
        queue_log = _queue_log_path(
            ensure_temp_root(base_dir), queue_name, "_queue.log"
        )
        try:
            await _write_json_file(
                queue_log, [item["line"] for item in queue], indent=4
            )
        except OSError as error:
            logger.info(
                f"[bold red]Failed to save the queue log file: {error}[/bold red]"
            )

    @classmethod
    async def _handle_manifest_mode(
        cls, path: str, meta: Meta, base_dir: str
    ) -> tuple[QueueList, str | None]:
        logger.info(
            f"[bold yellow]Detected a text file for queue input: {path}[/bold yellow]"
        )
        if not Path(path).exists():
            message = f"Queue input file was not found: {path}"
            logger.info(f"[bold red]{message}[/bold red]")
            raise NoWorkAvailableError(message)
        queue_name = Path(path).stem
        meta.queue = queue_name
        meta.args_line_queue = True
        processed_log = await cls.get_log_file(base_dir, queue_name)
        processed_files = await cls.load_processed_files(processed_log)
        queue = await cls._manifest_items(path, processed_files)
        if not queue:
            message = f"All items in the {queue_name} queue have already been processed."
            logger.info(f"[bold yellow]{message}[/bold yellow]")
            raise NoWorkAvailableError(message)
        expanded = cast(
            list[QueueItem], _expand_multi_format_ebook_directories(queue)
        )
        await cls._save_manifest_queue_log(base_dir, queue_name, expanded)
        if meta.debug:
            await cls.display_queue(
                expanded, base_dir, queue_name, save_to_log=False
            )
        return expanded, processed_log

    @staticmethod
    async def _handle_unit3d_mode(
        path: str, meta: Meta, log_file: Path
    ) -> list[str]:
        logger.info(
            f"[bold yellow]Detected a text file for queue input: {path}[/bold yellow]"
        )
        if not Path(path).exists():
            message = f"Queue input file was not found: {path}"
            logger.info(f"[bold red]{message}[/bold red]")
            raise NoWorkAvailableError(message)
        safe_locations = await QueueManager.extract_safe_file_locations(path)
        if not safe_locations:
            message = (
                "No safe file locations were found in the queue input file."
            )
            logger.info(f"[bold red]{message}[/bold red]")
            raise NoWorkAvailableError(message)
        logger.info(
            f"[cyan]Extracted {len(safe_locations)} safe file locations from the text file.[/cyan]"
        )
        meta.queue = "unit3d"
        try:
            await _write_json_file(log_file, safe_locations, indent=4)
            logger.info(
                f"[bold green]Queue log file saved successfully: {log_file}[/bold green]"
            )
        except OSError as error:
            message = f"Failed to save the queue log file: {error}"
            logger.info(f"[bold red]{message}[/bold red]")
            raise OperationAbortedError(message) from error
        return safe_locations

    @staticmethod
    async def _handle_debug_mode(
        path: str, meta: Meta
    ) -> tuple[list[str], str]:
        logger.info(
            f"[bold yellow]Processing debugging queue:[/bold yellow] [bold green]{path}[/bold green]"
        )
        if not Path(path).exists():
            message = f"Debug queue log file was not found: {path}"
            logger.info(f"[bold red]{message}[/bold red]")
            raise NoWorkAvailableError(message)
        queue = cast(list[str], await _read_json_file(path))
        meta.queue = "debugging"
        return queue, path

    @classmethod
    async def _current_queue_files(
        cls,
        path: str,
        paths: Sequence[str],
        allowed_extensions: Sequence[str],
    ) -> list[str]:
        if Path(path).exists():
            values = await cls.gather_files_recursive(
                path, allowed_extensions=allowed_extensions
            )
        else:
            values = await cls.resolve_queue_with_glob_or_split(
                path, paths, allowed_extensions=allowed_extensions
            )
        return _dedupe_paths(values)

    @staticmethod
    def _queue_interactive(meta: Meta) -> bool:
        return not meta.unattended or meta.unattended_confirm

    @staticmethod
    def _log_new_queue_files(new_files: set[str]) -> None:
        if not new_files:
            return
        logger.info(f"[green]New files found ({len(new_files)}):[/green]")
        for file in sorted(new_files):
            logger.info(f"  + {file}")

    @staticmethod
    def _log_removed_queue_files(removed_files: set[str]) -> None:
        if not removed_files:
            return
        logger.info(f"[red]Removed files ({len(removed_files)}):[/red]")
        for file in sorted(removed_files):
            logger.info(f"  - {file}")

    @classmethod
    def _log_queue_changes(
        cls, new_files: set[str], removed_files: set[str], debug: bool
    ) -> None:
        logger.info("[bold yellow]Queue changes detected:[/bold yellow]")
        if not debug:
            return
        cls._log_new_queue_files(new_files)
        cls._log_removed_queue_files(removed_files)

    @staticmethod
    async def _write_named_queue(
        log_file: Path, queue: list[str], message: str
    ) -> None:
        await _write_json_file(log_file, queue, indent=4)
        logger.info(message)

    @staticmethod
    def _edited_queue(
        source: list[str], fallback: list[str], failure_label: str
    ) -> tuple[list[str], bool]:
        edited_content = cast(
            str | None, click.edit(cast(Any, json.dumps(source, indent=4)))
        )
        if not edited_content:
            logger.info(
                f"[bold red]No changes were made. Using the {failure_label}."
            )
            return list(fallback), False
        try:
            queue = _dedupe_paths(json.loads(edited_content.strip()))
        except json.JSONDecodeError as error:
            logger.info(
                f"[bold red]Failed to parse the edited content: {error}. Using the {failure_label}."
            )
            return list(fallback), False
        logger.info(
            "[bold green]Successfully updated the queue from the editor."
        )
        return queue, True

    @staticmethod
    def _selected_queue_indices(selected: str) -> set[int]:
        indices: set[int] = set()
        for value in selected.split(","):
            if value.strip().isdigit():
                indices.add(int(value))
        return indices

    @staticmethod
    def _queue_files_at_indices(
        ordered: list[str], indices: set[int]
    ) -> list[str]:
        return [
            file for index, file in enumerate(ordered, 1) if index in indices
        ]

    @classmethod
    def _selected_new_files(cls, new_files: set[str]) -> list[str]:
        logger.info(
            "[yellow]Select which new files to add (comma-separated numbers):[/yellow]"
        )
        ordered = sorted(new_files)
        for index, file in enumerate(ordered, 1):
            logger.info(f"  {index}. {file}")
        selected_raw = cli_ui.ask_string("Enter numbers (e.g., 1,3,5): ")
        selected = (selected_raw or "").strip()
        return cls._queue_files_at_indices(
            ordered, cls._selected_queue_indices(selected)
        )

    @classmethod
    async def _update_changed_queue(
        cls, current_files: list[str], log_file: Path
    ) -> list[str]:
        logger.info(
            f"[bold green]Queue updated with current files ({len(current_files)} items)."
        )
        await cls._write_named_queue(
            log_file,
            current_files,
            f"[bold green]Queue log file updated: {log_file}[/bold green]",
        )
        return current_files

    @classmethod
    async def _add_changed_queue_files(
        cls,
        existing_queue: list[str],
        new_files: set[str],
        log_file: Path,
    ) -> list[str]:
        try:
            selected_files = cls._selected_new_files(new_files)
            queue = _dedupe_paths([*existing_queue, *selected_files])
            logger.info(
                f"[bold green]Queue updated with selected new files ({len(queue)} items)."
            )
            await cls._write_named_queue(
                log_file,
                queue,
                f"[bold green]Queue log file updated: {log_file}[/bold green]",
            )
            return queue
        except Exception as error:
            logger.info(
                f"[bold red]Failed to update queue with selected files: {error}. Using the existing queue."
            )
            return list(existing_queue)

    @classmethod
    async def _edit_changed_queue(
        cls, current_files: list[str], log_file: Path
    ) -> list[str]:
        queue, changed = cls._edited_queue(
            current_files, current_files, "current files"
        )
        if changed:
            await _write_json_file(log_file, queue, indent=4)
        return queue

    @classmethod
    async def _discard_changed_queue(
        cls, current_files: list[str], log_file: Path
    ) -> list[str]:
        logger.info(
            "[bold yellow]Discarding the existing queue log. Creating a new queue."
        )
        await cls._write_named_queue(
            log_file,
            current_files,
            f"[bold green]New queue log file created: {log_file}[/bold green]",
        )
        return current_files

    @classmethod
    async def _existing_changed_choice(
        cls,
        choice: str,
        existing_queue: list[str],
        current_files: list[str],
        new_files: set[str],
        log_file: Path,
    ) -> list[str]:
        if choice == "u":
            return await cls._update_changed_queue(current_files, log_file)
        if choice == "a":
            return await cls._add_changed_queue_files(
                existing_queue, new_files, log_file
            )
        if choice == "e":
            return await cls._edit_changed_queue(current_files, log_file)
        if choice == "d":
            return await cls._discard_changed_queue(current_files, log_file)
        logger.info("[bold green]Keeping the existing queue as is.")
        return list(existing_queue)

    @classmethod
    async def _existing_unchanged_choice(
        cls,
        choice: str,
        existing_queue: list[str],
        current_files: list[str],
        log_file: Path,
    ) -> list[str]:
        if choice == "e":
            queue, changed = cls._edited_queue(
                existing_queue, existing_queue, "original queue"
            )
            if changed:
                await _write_json_file(log_file, queue, indent=4)
            return queue
        if choice == "d":
            logger.info(
                "[bold yellow]Discarding the existing queue log. Creating a new queue."
            )
            await cls._write_named_queue(
                log_file,
                current_files,
                f"[bold green]New queue log file created: {log_file}[/bold green]",
            )
            return current_files
        logger.info("[bold green]Keeping the existing queue as is.")
        return list(existing_queue)

    @staticmethod
    def _queue_changed(new_files: set[str], removed_files: set[str]) -> bool:
        return bool(new_files or removed_files)

    @staticmethod
    def _log_existing_queue_status(
        log_file: Path, existing_queue: list[str], queued: list[str]
    ) -> None:
        logger.info(
            f"[bold yellow]Found an existing queue log file:[/bold yellow] [green]{log_file}[/green]"
        )
        logger.info(
            f"[cyan]The queue log contains {len(existing_queue)} total items and {len(queued)} unprocessed items.[/cyan]"
        )

    @classmethod
    async def _existing_queue_with_changes(
        cls,
        meta: Meta,
        existing_queue: list[str],
        current_files: list[str],
        new_files: set[str],
        removed_files: set[str],
        log_file: Path,
    ) -> list[str]:
        cls._log_queue_changes(new_files, removed_files, meta.debug)
        if not cls._queue_interactive(meta):
            logger.info(
                "[bold yellow]New or removed files detected, but unattended mode is active. Using existing queue."
            )
            return existing_queue
        logger.info(
            "[yellow]Do you want to update the queue log, edit, discard, or keep the existing queue?[/yellow]"
        )
        choice = (
            (
                cli_ui.ask_string(
                    "Enter 'u' to update, 'a' to add specific new files, 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
                )
                or ""
            )
            .strip()
            .lower()
        )
        return await cls._existing_changed_choice(
            choice, existing_queue, current_files, new_files, log_file
        )

    @classmethod
    async def _existing_queue_without_changes(
        cls,
        meta: Meta,
        existing_queue: list[str],
        current_files: list[str],
        log_file: Path,
    ) -> list[str]:
        logger.info("[green]No changes detected in the queue.[/green]")
        if not cls._queue_interactive(meta):
            logger.info("[bold green]Keeping the existing queue as is.")
            return existing_queue
        logger.info(
            "[yellow]Do you want to edit, discard, or keep the existing queue?[/yellow]"
        )
        choice = (
            (
                cli_ui.ask_string(
                    "Enter 'e' to edit, 'd' to discard, or press Enter to keep it as is: "
                )
                or ""
            )
            .strip()
            .lower()
        )
        return await cls._existing_unchanged_choice(
            choice, existing_queue, current_files, log_file
        )

    @classmethod
    async def _existing_named_queue(
        cls,
        path: str,
        paths: Sequence[str],
        meta: Meta,
        base_dir: str,
        log_file: Path,
        allowed_extensions: Sequence[str],
    ) -> list[str]:
        existing_queue = _dedupe_paths(
            cast(list[str], await _read_json_file(log_file))
        )
        current_files = await cls._current_queue_files(
            path, paths, allowed_extensions
        )
        existing_set = set(existing_queue)
        current_set = set(current_files)
        new_files = current_set - existing_set
        removed_files = existing_set - current_set
        processed_log = await cls.get_log_file(base_dir, cast(str, meta.queue))
        processed_files = await cls.load_processed_files(processed_log)
        queued = [
            file for file in existing_queue if file not in processed_files
        ]
        cls._log_existing_queue_status(log_file, existing_queue, queued)
        if cls._queue_changed(new_files, removed_files):
            return await cls._existing_queue_with_changes(
                meta,
                existing_queue,
                current_files,
                new_files,
                removed_files,
                log_file,
            )
        return await cls._existing_queue_without_changes(
            meta, existing_queue, current_files, log_file
        )

    @classmethod
    async def _new_named_queue(
        cls,
        path: str,
        paths: Sequence[str],
        log_file: Path,
        allowed_extensions: Sequence[str],
    ) -> list[str]:
        queue = await cls._current_queue_files(path, paths, allowed_extensions)
        logger.info(
            f"[cyan]A new queue log file will be created:[/cyan] [green]{log_file}[/green]"
        )
        logger.info(
            f"[cyan]The new queue will contain {len(queue)} items.[/cyan]"
        )
        logger.info(
            "[cyan]Do you want to edit the initial queue before saving?[/cyan]"
        )
        choice = (
            (
                cli_ui.ask_string(
                    "Enter 'e' to edit, or press Enter to save as is: "
                )
                or ""
            )
            .strip()
            .lower()
        )
        if choice == "e":
            queue, _changed = cls._edited_queue(queue, queue, "original queue")
        await _write_json_file(log_file, queue, indent=4)
        logger.info(
            f"[bold green]Queue log file created: {log_file}[/bold green]"
        )
        return queue

    @classmethod
    async def _handle_named_mode(
        cls,
        path: str,
        paths: Sequence[str],
        meta: Meta,
        base_dir: str,
        log_file: Path,
        allowed_extensions: Sequence[str],
    ) -> list[str]:
        if log_file.exists():
            return await cls._existing_named_queue(
                path,
                paths,
                meta,
                base_dir,
                log_file,
                allowed_extensions,
            )
        return await cls._new_named_queue(
            path, paths, log_file, allowed_extensions
        )

    @classmethod
    async def _handle_generic_mode(
        cls, path: str, paths: Sequence[str]
    ) -> list[str]:
        if len(paths) > 1:
            queue = _dedupe_paths(paths)
            await cls.display_queue(queue, save_to_log=False)
            return queue
        if Path(path).exists():
            return [path]
        queue = await cls.resolve_queue_with_glob_or_split(
            path, paths, allowed_extensions=None
        )
        if not queue and Path(path).parent.exists():
            logger.info(
                f"[red]Path: [bold red]{path}[/bold red] does not exist"
            )
        return queue

    @classmethod
    async def _run_queue_mode(
        cls,
        mode: str,
        path: str,
        paths: Sequence[str],
        meta: Meta,
        base_dir: str,
        log_file: Path,
        allowed_extensions: Sequence[str],
    ) -> tuple[QueueList, Path]:
        if mode == "unit3d":
            return await cls._handle_unit3d_mode(
                path, meta, log_file
            ), log_file
        if mode == "debug":
            queue, debug_log = await cls._handle_debug_mode(path, meta)
            return queue, Path(debug_log)
        if mode == "named":
            queue = await cls._handle_named_mode(
                path,
                paths,
                meta,
                base_dir,
                log_file,
                allowed_extensions,
            )
            return queue, log_file
        return await cls._handle_generic_mode(path, paths), log_file

    @staticmethod
    def _require_nonempty_queue(queue: QueueList, path: str) -> QueueList:
        if queue:
            return queue
        message = f"No valid files or directories were found for path: {path}"
        logger.info(f"[red]{message}[/red]")
        raise NoWorkAvailableError(message)

    @classmethod
    async def _processed_queue_values(
        cls,
        queue: QueueList,
        base_dir: str,
        queue_name: str,
    ) -> tuple[list[str], str]:
        processed_log = await cls.get_log_file(base_dir, queue_name)
        processed_files = await cls.load_processed_files(processed_log)
        values = _dedupe_paths(cast(Sequence[str], queue))
        filtered = [item for item in values if item not in processed_files]
        return filtered, processed_log

    @staticmethod
    def _require_unprocessed_queue(
        queue: list[str], queue_name: str
    ) -> list[str]:
        if queue:
            return queue
        message = (
            f"All files in the {queue_name} queue have already been processed."
        )
        logger.info(f"[bold yellow]{message}[/bold yellow]")
        raise NoWorkAvailableError(message)

    @classmethod
    async def _finalize_queue(
        cls,
        queue: QueueList,
        path: str,
        meta: Meta,
        base_dir: str,
        log_file: Path,
    ) -> tuple[QueueList, str | None]:
        expanded = cls._require_nonempty_queue(
            _expand_multi_format_ebook_directories(queue), path
        )
        if not meta.queue:
            return expanded, str(log_file)
        queue_name = meta.queue
        filtered, processed_log = await cls._processed_queue_values(
            expanded, base_dir, queue_name
        )
        filtered = cls._require_unprocessed_queue(filtered, queue_name)
        if meta.debug:
            await cls.display_queue(
                filtered, base_dir, queue_name, save_to_log=False
            )
        return filtered, processed_log

    @classmethod
    async def handle_queue(
        cls,
        path: str,
        meta: Meta,
        paths: Sequence[str],
        base_dir: str,
    ) -> tuple[QueueList, str | None]:
        if meta.site_upload:
            return await cls._handle_site_upload_mode(meta, base_dir)
        allowed_extensions = [".mkv", ".mp4", ".ts", ".avi"]
        log_file = _queue_log_path(
            ensure_temp_root(base_dir), meta.queue or "default", "_queue.log"
        )
        mode = cls._queue_mode(path, meta)
        if mode == "manifest":
            return await cls._handle_manifest_mode(path, meta, base_dir)
        queue, log_file = await cls._run_queue_mode(
            mode,
            path,
            paths,
            meta,
            base_dir,
            log_file,
            allowed_extensions,
        )
        return await cls._finalize_queue(queue, path, meta, base_dir, log_file)


async def process_site_upload_queue(
    meta: Meta, base_dir: str
) -> tuple[list[QueueItem], str | None]:
    return await QueueManager.process_site_upload_queue(meta, base_dir)


async def process_site_upload_item(
    queue_item: Mapping[str, Any], meta: Meta
) -> str:
    return await QueueManager.process_site_upload_item(queue_item, meta)


async def save_processed_path(processed_files_log: str, path: str) -> None:
    await QueueManager.save_processed_path(processed_files_log, path)


async def get_log_file(base_dir: str, queue_name: str) -> str:
    return await QueueManager.get_log_file(base_dir, queue_name)


async def load_processed_files(log_file: str) -> set[str]:
    return await QueueManager.load_processed_files(log_file)


async def gather_files_recursive(
    path: str | bytes,
    allowed_extensions: Sequence[str] | None = None,
) -> list[str]:
    return await QueueManager.gather_files_recursive(
        path, allowed_extensions=allowed_extensions
    )


async def should_include_directory(
    dir_path: str,
    allowed_extensions: Sequence[str] | None = None,
) -> bool:
    return await QueueManager.should_include_directory(
        dir_path, allowed_extensions=allowed_extensions
    )


async def resolve_queue_with_glob_or_split(
    path: str,
    paths: Sequence[str],
    allowed_extensions: Sequence[str] | None = None,
) -> list[str]:
    return await QueueManager.resolve_queue_with_glob_or_split(
        path, paths, allowed_extensions=allowed_extensions
    )


async def extract_safe_file_locations(log_file: str) -> list[str]:
    return await QueueManager.extract_safe_file_locations(log_file)


async def display_queue(
    queue: Sequence[Any],
    base_dir: str | None = None,
    queue_name: str | None = None,
    save_to_log: bool = True,
) -> None:
    await QueueManager.display_queue(
        queue,
        base_dir=base_dir,
        queue_name=queue_name,
        save_to_log=save_to_log,
    )


async def handle_queue(
    path: str,
    meta: Meta,
    paths: Sequence[str],
    base_dir: str,
) -> tuple[QueueList, str | None]:
    return await QueueManager.handle_queue(path, meta, paths, base_dir)
