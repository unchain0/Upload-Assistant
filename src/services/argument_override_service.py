# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from src.domain_models.release import Meta
from src.services.runtime_support import logger

UserArgsEntry = dict[str, Any]

_ID_MAPPINGS = {
    "tmdb": ("tmdb_id", "tmdb", "tmdb_manual"),
    "tvmaze": ("tvmaze_id", "tvmaze", "tvmaze_manual"),
    "imdb": ("imdb_id", "imdb", "imdb_manual"),
    "tvdb": ("tvdb_id", "tvdb", "tvdb_manual"),
}


def _numeric_current_id(value: Any) -> Any:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return value


def _integer_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _category_for_tmdb_prefix(prefix: str, category: str | None) -> str | None:
    return {"tv": "TV", "movie": "MOVIE"}.get(prefix, category)


def _parsed_tmdb_id(
    tmdb_id: Any | None, category: str | None
) -> tuple[str | None, int]:
    if tmdb_id is None:
        return category, 0
    text = str(tmdb_id).strip().lower()
    if not text:
        return category, 0
    parts = text.split("/")
    if len(parts) >= 2:
        category = _category_for_tmdb_prefix(parts[0], category)
        return category, _integer_or_zero(parts[1])
    return category, _integer_or_zero(text)


def _entry_args(entry: UserArgsEntry) -> list[str]:
    return cast(list[str], entry.get("args", []))


def _tmdb_entry_match(
    entry: UserArgsEntry,
    meta: Meta,
    current_tmdb_id: Any,
) -> tuple[int, list[str]] | None:
    entry_tmdb_id = entry.get("tmdb_id")
    if not entry_tmdb_id:
        return None
    category, normalized_id = _parsed_tmdb_id(entry_tmdb_id, None)
    if category and category != meta.category:
        logger.debug(
            "Skipping user entry because override category "
            f"{category} does not match UA category {meta.category}:"
        )
        return None
    if normalized_id != current_tmdb_id:
        return None
    return normalized_id, _entry_args(entry)


def _first_tmdb_match(
    entries: list[UserArgsEntry], meta: Meta, current_tmdb_id: Any
) -> tuple[str, Any, list[str]] | None:
    for entry in entries:
        match = _tmdb_entry_match(entry, meta, current_tmdb_id)
        if match is not None:
            normalized_id, args = match
            return "TMDb", normalized_id, args
    return None


def _tvdb_entry_matches(entry: UserArgsEntry, current_tvdb_id: Any) -> bool:
    return bool(
        "tvdb_id" in entry
        and str(entry["tvdb_id"]) == str(current_tvdb_id)
        and current_tvdb_id != 0
    )


def _normalized_imdb_entry(entry: UserArgsEntry) -> Any:
    value = entry.get("imdb_id")
    if isinstance(value, str) and value.startswith("tt"):
        return value[2:]
    return value


def _imdb_entry_matches(entry: UserArgsEntry, current_imdb_id: Any) -> bool:
    if "imdb_id" not in entry or current_imdb_id == 0:
        return False
    return str(_normalized_imdb_entry(entry)) == str(current_imdb_id)


def _other_entry_match(
    entry: UserArgsEntry,
    current_tvdb_id: Any,
    current_imdb_id: Any,
) -> tuple[str, Any, list[str]] | None:
    if _tvdb_entry_matches(entry, current_tvdb_id):
        return "TVDb", current_tvdb_id, _entry_args(entry)
    if _imdb_entry_matches(entry, current_imdb_id):
        return "IMDb", current_imdb_id, _entry_args(entry)
    return None


def _first_other_match(
    entries: list[UserArgsEntry],
    current_tvdb_id: Any,
    current_imdb_id: Any,
) -> tuple[str, Any, list[str]] | None:
    for entry in entries:
        match = _other_entry_match(entry, current_tvdb_id, current_imdb_id)
        if match is not None:
            return match
    return None


def _tracked_arguments(args: list[str]) -> tuple[set[str], dict[str, str]]:
    keys: set[str] = set()
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            keys.add(key)
            next_index = index + 1
            if next_index < len(args) and not args[next_index].startswith(
                "--"
            ):
                values[key] = args[next_index]
                index += 1
        index += 1
    return keys, values


def _normalized_id_override(key: str, value: str) -> Any:
    try:
        if value.isdigit():
            return int(value)
        if key == "imdb" and value.startswith("tt"):
            return int(value[2:])
    except ValueError:
        return value
    return value


def _apply_identifier_override(
    meta: Meta,
    key: str,
    value: Any,
    modified_keys: list[str],
) -> None:
    for related_key in _ID_MAPPINGS[key]:
        meta[related_key] = value
        modified_keys.append(related_key)
        logger.debug(
            f"[Debug] Override: {related_key} changed from "
            f"{meta.get(related_key)} to {value}"
        )


def _apply_regular_override(
    meta: Meta,
    updated_meta: Meta,
    key: str,
    modified_keys: list[str],
) -> None:
    if key == "path" or key not in updated_meta or key not in meta:
        return
    new_value = updated_meta[key]
    old_value = meta[key]
    if new_value == old_value:
        return
    meta[key] = new_value
    modified_keys.append(key)
    logger.debug(
        f"[Debug] Override: {key} changed from {old_value} to {new_value}"
    )


def _apply_tracked_overrides(
    meta: Meta,
    updated_meta: Meta,
    keys: set[str],
    values: dict[str, str],
) -> list[str]:
    modified_keys: list[str] = []
    for key in keys:
        if key in _ID_MAPPINGS:
            if key in values:
                value = _normalized_id_override(key, values[key])
                _apply_identifier_override(meta, key, value, modified_keys)
            continue
        _apply_regular_override(meta, updated_meta, key, modified_keys)
    return modified_keys


class ArgumentParserPort(Protocol):
    def parse(
        self, argv: list[str], meta: Meta
    ) -> tuple[Meta, object, list[str]]: ...


ArgumentParserFactory = Callable[[dict[str, Any]], ArgumentParserPort]


class ApplyOverrides:
    def __init__(
        self,
        config: dict[str, Any],
        argument_parser_factory: ArgumentParserFactory | None = None,
    ) -> None:
        self.config = config
        self._argument_parser_factory = argument_parser_factory

    async def get_source_override(
        self, meta: Meta, other_id: bool = False
    ) -> Meta:
        try:
            path = (
                Path(meta.base_dir) / "data" / "templates" / "user-args.json"
            )
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            logger.info("[green]Found user-args.json")
            user_args = cast(dict[str, Any], json.loads(text))
            current_tmdb_id = _numeric_current_id(meta.tmdb_id)
            current_imdb_id = _numeric_current_id(meta.imdb_id)
            current_tvdb_id = _numeric_current_id(meta.tvdb_id)
            if other_id:
                match = _first_other_match(
                    cast(list[UserArgsEntry], user_args.get("other_ids", [])),
                    current_tvdb_id,
                    current_imdb_id,
                )
            else:
                match = _first_tmdb_match(
                    cast(list[UserArgsEntry], user_args.get("entries", [])),
                    meta,
                    current_tmdb_id,
                )
            if match is not None:
                label, identifier, args = match
                logger.info(
                    f"[green]Found matching override for {label} ID: {identifier}"
                )
                logger.info(f"[yellow]Applying arguments: {' '.join(args)}")
                meta = await self.apply_args_to_meta(meta, args)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            logger.error(f"[red]Error loading user-args.json: {error}")
        return meta

    async def parse_tmdb_id(
        self, tmdb_id: Any | None, category: str | None = None
    ) -> tuple[str | None, int]:
        return _parsed_tmdb_id(tmdb_id, category)

    async def apply_args_to_meta(self, meta: Meta, args: list[str]) -> Meta:
        try:
            keys, values = _tracked_arguments(args)
            logger.debug(
                f"[Debug] Tracking changes for keys: {', '.join(keys)}"
            )
            if self._argument_parser_factory is None:
                logger.warning(
                    "[yellow]Skipping user-args override because no argument "
                    "parser port was supplied.[/yellow]"
                )
                return meta
            processor = self._argument_parser_factory(self.config)
            updated_meta, _, _ = processor.parse(
                ["upload.py", *args], meta.copy()
            )
            updated_meta["path"] = meta.path
            modified_keys = _apply_tracked_overrides(
                meta, updated_meta, keys, values
            )
            if meta.debug and modified_keys:
                logger.info(
                    f"[Debug] Applied overrides for: "
                    f"{', '.join(modified_keys)}"
                )
        except Exception as error:
            logger.error(f"[red]Error processing arguments: {error}")
            logger.debug(traceback.format_exc())
        return meta
