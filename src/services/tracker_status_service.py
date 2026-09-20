# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Never, cast

import cli_ui

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.domain_models.tracker_upload_state import TrackerUploadState
from src.engines.tracker_upload_eligibility import (
    evaluate_tracker_upload_eligibility,
)
from src.engines.upload_safety_policy import (
    blocks_automatic_upload,
    book_metadata_cjk_fields,
    content_paths_with_spaces,
    invalid_release_group_tag,
)
from src.integrations.external_apis.imdb import imdb_manager
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.media.artwork import audiobook_cover_missing_or_invalid
from src.integrations.media.zentag import (
    should_prepare_zenith_audiobook,
    should_prepare_zenith_ebook,
)
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.trackers.AVISTAZ.routing import AvistaZNetworkRouter
from src.integrations.trackers.common import Common
from src.integrations.trackers.passthepopcorn import PassThePopcorn
from src.integrations.trackers.registry import TrackerSetup, tracker_class_map
from src.services.duplicate_check_service import DupeChecker
from src.services.game_preparation import missing_game_fields
from src.services.metadata_service import get_douban_id
from src.services.upload_decision_service import UploadHelper


def merge_tracker_status(
    processed: dict[str, dict[str, Any]],
    existing: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Preserve routing metadata while fresh processing stays authoritative."""
    merged = {tracker: dict(status) for tracker, status in existing.items()}
    for tracker, status in processed.items():
        merged.setdefault(tracker, {}).update(status)
    return merged


def missing_book_fields_for_tracker(
    meta: Meta, tracker_class: Any
) -> list[str]:
    from src.services.book_preparation import missing_book_fields

    missing = missing_book_fields(meta)
    required_fields = getattr(tracker_class, "required_book_fields", None)
    if required_fields is None:
        return missing
    required = {str(field) for field in required_fields}
    return [field for field in missing if field in required]


@dataclass(slots=True)
class _TrackerResult:
    name: str
    status: dict[str, Any]
    display_name: str | None
    tracker_class: Any


@dataclass(slots=True)
class _TrackerRuntime:
    setup: Any
    helper: Any
    dupe_checker: DupeChecker
    lock: asyncio.Lock


class TrackerStatusManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        trackers = config.get("TRACKERS", {})
        self.trackers_config = cast(
            Mapping[str, Mapping[str, Any]],
            trackers if isinstance(trackers, Mapping) else {},
        )

    async def process_all_trackers(self, meta: Meta) -> int:
        blocked = self._block_unsafe_release(meta)
        if blocked:
            return 0
        runtime = await self._prepare_runtime(meta)
        await self._ensure_required_imdb(meta)
        results = await self._process_trackers(meta, runtime)
        self._apply_completed_episode_rule(meta, results)
        successful = await self._select_uploads(meta, results, runtime.helper)
        self._log_debug_summary(meta, results, successful)
        meta.tracker_status = merge_tracker_status(
            {result.name: result.status for result in results},
            meta.tracker_status,
        )
        return successful

    def _block_unsafe_release(self, meta: Meta) -> bool:
        reason = self._unsafe_release_reason(meta)
        if reason is None:
            return False
        processed = {
            tracker: self._blocked_status(reason)
            for tracker in self._tracker_names(meta.trackers)
        }
        meta.tracker_status = merge_tracker_status(
            processed, meta.tracker_status
        )
        logger.info(
            f"[bold red]{reason} All tracker uploads were skipped.[/bold red]"
        )
        return True

    @staticmethod
    def _unsafe_release_reason(meta: Meta) -> str | None:
        invalid_group = invalid_release_group_tag(meta)
        if invalid_group:
            return f"Release group {invalid_group!r} matches season/episode syntax. Correct or clear the release group before uploading."
        if audiobook_cover_missing_or_invalid(meta):
            return "Audiobook cover is missing or invalid. Prepare a valid cover before uploading."
        return (
            TrackerStatusManager._unsafe_path_reason(meta)
            if blocks_automatic_upload(meta)
            else None
        )

    @staticmethod
    def _unsafe_path_reason(meta: Meta) -> str:
        suspicious = content_paths_with_spaces(meta)
        displayed = ", ".join(suspicious[:5])
        if len(suspicious) > 5:
            displayed = f"{displayed}, and {len(suspicious) - 5} more"
        return f"Content file/folder names contain spaces and may have been renamed: {displayed}. Use --allow-spaces to explicitly permit this upload."

    @staticmethod
    def _blocked_status(reason: str) -> dict[str, Any]:
        return {
            "banned": False,
            "skipped": True,
            "dupe": False,
            "upload": False,
            "other": False,
            "skip_reason": reason,
        }

    async def _prepare_runtime(self, meta: Meta) -> _TrackerRuntime:
        setup = TrackerSetup(config=self.config)
        setup.filter_unsupported_trackers(meta)
        await AvistaZNetworkRouter(self.config, tracker_class_map).apply(meta)
        self._ensure_status_entries(meta)
        await self._populate_douban_if_needed(meta)
        return _TrackerRuntime(
            setup=setup,
            helper=UploadHelper(self.config),
            dupe_checker=DupeChecker(self.config),
            lock=asyncio.Lock(),
        )

    @staticmethod
    def _ensure_status_entries(meta: Meta) -> None:
        for tracker in TrackerStatusManager._tracker_names(meta.trackers):
            meta.tracker_status.setdefault(tracker, {})
        meta.initial_dupes = (
            meta.initial_dupes if isinstance(meta.initial_dupes, dict) else {}
        )

    async def _populate_douban_if_needed(self, meta: Meta) -> None:
        douban_trackers = {
            "1PTBA",
            "LAJIDUI",
            "LEMONHD",
            "LONGPT",
            "MTEAM",
            "PTCAFE",
            "PTFANS",
            "PTGTK",
            "PTZONE",
            "RAILGUNPT",
            "XINGYUNGEPT",
        }
        if douban_trackers.intersection(self._tracker_names(meta.trackers)):
            meta.douban_id = await get_douban_id(meta)

    async def _ensure_required_imdb(self, meta: Meta) -> None:
        if not self._needs_imdb_prompt(meta):
            return
        while True:
            imdb_id = await self._prompt_imdb_id()
            if not imdb_id:
                meta.imdb_id = 0
                return
            if self._valid_imdb_id(imdb_id):
                await self._apply_manual_imdb(meta, imdb_id)
                return
            cli_ui.error("Invalid IMDB ID format. Expected format: tt1234567")

    @classmethod
    def _needs_imdb_prompt(cls, meta: Meta) -> bool:
        return (
            not bool(meta.unattended)
            and int(meta.imdb_id or 0) == 0
            and "PASSTHEPOPCORN" in cls._tracker_names(meta.trackers)
        )

    @staticmethod
    async def _prompt_imdb_id() -> str:
        value: Any = ""
        try:
            value = await prompt_in_thread(
                cli_ui.ask_string,
                "Unable to find IMDB id, please enter e.g.(tt1234567) or press Enter to skip uploading to trackers requiring it:",
            )
        except EOFError:
            await TrackerStatusManager._abort_tracker_selection()
        return str(value or "").strip().lower()

    @staticmethod
    def _valid_imdb_id(value: str) -> bool:
        return value.startswith("tt") and value[2:].isdigit()

    @staticmethod
    async def _apply_manual_imdb(meta: Meta, imdb_id: str) -> None:
        numeric = int(imdb_id[2:])
        meta.imdb_id = numeric
        meta.imdb = imdb_id[2:].zfill(7)
        meta.imdb_info = await imdb_manager.get_imdb_info_api(
            numeric, manual_language=meta.manual_language
        )

    @staticmethod
    async def _abort_tracker_selection() -> Never:
        logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise OperationAbortedError(
            "Tracker selection was cancelled by the user."
        )

    async def _process_trackers(
        self, meta: Meta, runtime: _TrackerRuntime
    ) -> list[_TrackerResult]:
        trackers = self._tracker_names(meta.trackers)
        searchable = [name for name in trackers if name in tracker_class_map]
        if searchable:
            logger.info(
                "[yellow]Searching for existing torrents on selected trackers..."
            )
        tasks = [
            self._process_single_tracker(name, meta, runtime)
            for name in trackers
        ]
        return list(await asyncio.gather(*tasks))

    async def _process_single_tracker(
        self, tracker_name: str, shared_meta: Meta, runtime: _TrackerRuntime
    ) -> _TrackerResult:
        local_meta = copy.deepcopy(shared_meta)
        self._remove_dupe_suffix(local_meta)
        status = self._initial_status()
        if tracker_name in {"MANUAL", "USENET"}:
            status["upload"] = True
            return _TrackerResult(tracker_name, status, None, None)
        tracker_class = self._tracker_instance(tracker_name)
        if tracker_class is None:
            status["upload"] = True
            return _TrackerResult(tracker_name, status, None, None)
        await self._prepare_tracker_meta(
            tracker_name, local_meta, tracker_class, status
        )
        if not status["skipped"] and not status["banned"]:
            await self._evaluate_tracker(
                tracker_name,
                local_meta,
                tracker_class,
                status,
                shared_meta,
                runtime,
            )
        self._ensure_skip_reason(tracker_name, local_meta, status)
        display_name = await self._display_name(
            local_meta, tracker_class, status
        )
        return _TrackerResult(
            tracker_name, status, display_name, tracker_class
        )

    @staticmethod
    def _remove_dupe_suffix(meta: Meta) -> None:
        if str(meta.name).endswith("DUPE?"):
            meta.name = str(meta.name).replace(" DUPE?", "")

    @staticmethod
    def _initial_status() -> dict[str, Any]:
        return {
            "banned": False,
            "skipped": False,
            "dupe": False,
            "upload": False,
            "other": False,
        }

    def _tracker_instance(self, tracker_name: str) -> Any | None:
        tracker_type = tracker_class_map.get(tracker_name)
        return (
            tracker_type(config=self.config)
            if tracker_type is not None
            else None
        )

    async def _prepare_tracker_meta(
        self,
        tracker_name: str,
        meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
    ) -> None:
        self._defer_zenith_validation(tracker_name, meta)
        self._apply_ptp_identity_requirement(tracker_name, meta, status)
        if status["skipped"]:
            return
        status["banned"] = bool(
            await TrackerSetup(config=self.config).check_banned_group(
                tracker_class.tracker, tracker_class.banned_groups, meta
            )
        )
        self._apply_preexisting_skip(tracker_name, meta, status)
        self._apply_missing_field_skip(
            tracker_name, meta, tracker_class, status
        )

    def _defer_zenith_validation(self, tracker_name: str, meta: Meta) -> None:
        if tracker_name != "ZENITH":
            return
        if should_prepare_zenith_audiobook(
            meta, self.config
        ) or should_prepare_zenith_ebook(meta, self.config):
            meta.defer_zentag_validation = True

    @staticmethod
    def _apply_ptp_identity_requirement(
        tracker_name: str, meta: Meta, status: dict[str, Any]
    ) -> None:
        if tracker_name != "PASSTHEPOPCORN" or int(meta.imdb_id or 0) != 0:
            return
        TrackerStatusManager._mark_skipped(
            status, "IMDb ID is required for PassThePopcorn duplicate checks."
        )

    @staticmethod
    def _apply_preexisting_skip(
        tracker_name: str, meta: Meta, status: dict[str, Any]
    ) -> None:
        tracker_status = meta.tracker_status.get(tracker_name, {})
        if not isinstance(tracker_status, Mapping):
            return
        tracker_status_map = cast(Mapping[str, Any], tracker_status)
        if not tracker_status_map.get("skip_upload"):
            return
        reason = str(
            tracker_status_map.get("skip_reason")
            or tracker_status_map.get("status_message")
            or (
                "A previous tracker eligibility check requested this upload be skipped."
            )
        )
        TrackerStatusManager._mark_skipped(status, reason)

    def _apply_missing_field_skip(
        self,
        tracker_name: str,
        meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
    ) -> None:
        reason = self._missing_field_reason(meta, tracker_class)
        if not reason:
            return
        logger.info(
            f"[yellow]{tracker_name}: Skipping upload because {reason}[/yellow]"
        )
        self._mark_skipped(status, reason)

    @classmethod
    def _missing_field_reason(cls, meta: Meta, tracker_class: Any) -> str:
        if not meta.unattended:
            return ""
        if meta.category == "BOOK":
            return cls._missing_book_field_reason(meta, tracker_class)
        if meta.category == "GAME":
            return cls._missing_game_field_reason(meta)
        return ""

    @staticmethod
    def _missing_book_field_reason(meta: Meta, tracker_class: Any) -> str:
        fields = missing_book_fields_for_tracker(meta, tracker_class)
        return (
            f"Required BOOK fields missing: {', '.join(fields)}"
            if fields
            else ""
        )

    @staticmethod
    def _missing_game_field_reason(meta: Meta) -> str:
        fields = missing_game_fields(meta)
        if not fields:
            return ""
        kind = "SOFTWARE" if meta.software else "GAME"
        return f"Required {kind} fields missing: {', '.join(fields)}"

    async def _evaluate_tracker(
        self,
        tracker_name: str,
        local_meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
    ) -> None:
        if await self._claim_blocks_upload(
            tracker_name, local_meta, status, runtime.setup
        ):
            return
        if not await self._additional_checks_allow(
            tracker_name, local_meta, tracker_class, status
        ):
            return
        if not self._cjk_metadata_allows(tracker_name, local_meta, status):
            return
        await self._store_prepared_meta(
            shared_meta, tracker_name, local_meta, runtime.lock
        )
        dupes = await self._search_tracker_dupes(
            tracker_name,
            local_meta,
            tracker_class,
            status,
            shared_meta,
            runtime,
        )
        await self._store_initial_dupes(
            shared_meta, tracker_name, dupes, runtime.lock
        )
        if status["skipped"]:
            return
        self._warn_amigos_anonymous(tracker_name, local_meta)
        await self._evaluate_dupes(
            tracker_name, local_meta, dupes, status, shared_meta, runtime
        )

    @staticmethod
    async def _claim_blocks_upload(
        tracker_name: str,
        meta: Meta,
        status: dict[str, Any],
        setup: Any,
    ) -> bool:
        claimed = bool(await setup.get_torrent_claims(meta, tracker_name))
        if claimed:
            TrackerStatusManager._mark_skipped(
                status, "An existing tracker claim blocks this upload."
            )
        return claimed

    async def _additional_checks_allow(
        self,
        tracker_name: str,
        meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
    ) -> bool:
        method = getattr(tracker_class, "get_additional_checks", None)
        if method is None:
            return True
        result = (
            await method(meta)
            if inspect.iscoroutinefunction(method)
            else method(meta)
        )
        if result:
            return True
        reason = self._tracker_skip_reason(
            tracker_name,
            meta,
            "Tracker-specific eligibility check rejected the release.",
        )
        self._mark_skipped(status, reason)
        meta.skipping = tracker_name
        return False

    def _cjk_metadata_allows(
        self, tracker_name: str, meta: Meta, status: dict[str, Any]
    ) -> bool:
        fields = book_metadata_cjk_fields(meta)
        if not fields or self._can_prepare_zenith(tracker_name, meta):
            return True
        reason = f"BOOK metadata contains CJK characters in: {', '.join(fields)}. Provide verified English metadata before uploading."
        logger.info(f"{tracker_name}: [bold red]{reason}[/bold red]")
        self._mark_skipped(status, reason)
        meta.skipping = tracker_name
        return False

    def _can_prepare_zenith(self, tracker_name: str, meta: Meta) -> bool:
        return tracker_name == "ZENITH" and (
            should_prepare_zenith_audiobook(meta, self.config)
            or should_prepare_zenith_ebook(meta, self.config)
        )

    @staticmethod
    async def _store_prepared_meta(
        meta: Meta, tracker_name: str, local_meta: Meta, lock: asyncio.Lock
    ) -> None:
        async with lock:
            prepared = meta.setdefault("tracker_prepared_meta", {})
            if not isinstance(prepared, dict):
                prepared = {}
                meta["tracker_prepared_meta"] = prepared
            prepared_map = cast(dict[str, Any], prepared)
            prepared_map.setdefault(tracker_name, local_meta.copy())

    async def _search_tracker_dupes(
        self,
        tracker_name: str,
        meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
    ) -> list[Any]:
        if tracker_name == "PASSTHEPOPCORN":
            return await self._search_ptp_dupes(
                tracker_name, meta, status, shared_meta, runtime
            )
        return await self._search_regular_dupes(
            tracker_name, meta, tracker_class, status, shared_meta, runtime
        )

    async def _search_regular_dupes(
        self,
        tracker_name: str,
        meta: Meta,
        tracker_class: Any,
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
    ) -> list[Any]:
        try:
            dupes = cast(list[Any], await tracker_class.search_existing(meta))
            await self._mark_dupe_checked(
                shared_meta, tracker_name, runtime.lock
            )
            tracker_status = meta.tracker_status.get(tracker_name, {})
            status["other"] = self._tracker_other_status(tracker_status)
            return dupes
        except Exception as error:
            return await self._handle_search_failure(
                tracker_name,
                meta,
                status,
                shared_meta,
                runtime,
                error,
                mark_checked=True,
            )

    @staticmethod
    def _tracker_other_status(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        mapping = cast(Mapping[str, Any], value)
        return bool(mapping.get("other", False))

    async def _search_ptp_dupes(
        self,
        tracker_name: str,
        meta: Meta,
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
    ) -> list[Any]:
        ptp: Any = PassThePopcorn(config=self.config)
        if not await self._ptp_additional_checks(
            ptp, tracker_name, meta, status
        ):
            return []
        try:
            group_id = await ptp.get_group_by_imdb(meta.imdb)
            async with runtime.lock:
                shared_meta.ptp_groupid = group_id
            return cast(
                list[Any],
                await ptp.search_existing(
                    group_id or "", cast(dict[str, Any], meta)
                ),
            )
        except Exception as error:
            return await self._handle_search_failure(
                tracker_name,
                meta,
                status,
                shared_meta,
                runtime,
                error,
                mark_checked=False,
            )

    async def _ptp_additional_checks(
        self, ptp: Any, tracker_name: str, meta: Meta, status: dict[str, Any]
    ) -> bool:
        method = getattr(ptp, "get_additional_checks", None)
        if method is None:
            return True
        result = (
            await method(meta)
            if inspect.iscoroutinefunction(method)
            else method(meta)
        )
        if result:
            return True
        reason = self._tracker_skip_reason(
            tracker_name,
            meta,
            "PassThePopcorn eligibility check rejected the release.",
        )
        self._mark_skipped(status, reason)
        meta.skipping = tracker_name
        return False

    async def _handle_search_failure(
        self,
        tracker_name: str,
        meta: Meta,
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
        error: Exception,
        *,
        mark_checked: bool,
    ) -> list[Any]:
        logger.info(
            f"[bold red]Error searching for duplicates on {tracker_name}: {error}[/bold red]"
        )
        if meta.unattended:
            reason = f"Duplicate search failed: {error}"
            self._mark_skipped(status, reason)
            meta.skipping = tracker_name
            return []
        proceed = await self._confirm_failed_dupe_search(
            runtime.helper, tracker_name
        )
        if proceed and mark_checked:
            await self._mark_dupe_checked(
                shared_meta, tracker_name, runtime.lock
            )
        if not proceed:
            self._mark_skipped(
                status, "Duplicate search failed and upload was not confirmed."
            )
            meta.skipping = tracker_name
        return []

    @staticmethod
    async def _confirm_failed_dupe_search(
        helper: Any, tracker_name: str
    ) -> bool:
        try:
            return bool(
                await helper.prompt_yes_no(
                    f"Duplicate check failed on {tracker_name}. Do you want to proceed with the upload anyway?",
                    default=False,
                )
            )
        except EOFError:
            await TrackerStatusManager._abort_tracker_selection()

    @staticmethod
    async def _mark_dupe_checked(
        meta: Meta, tracker_name: str, lock: asyncio.Lock
    ) -> None:
        async with lock:
            checked = meta.setdefault("dupe_checked_trackers", [])
            if tracker_name not in checked:
                checked.append(tracker_name)

    @staticmethod
    async def _store_initial_dupes(
        meta: Meta, tracker_name: str, dupes: list[Any], lock: asyncio.Lock
    ) -> None:
        async with lock:
            meta.initial_dupes[tracker_name] = copy.deepcopy(dupes)

    @staticmethod
    def _warn_amigos_anonymous(tracker_name: str, meta: Meta) -> None:
        if tracker_name != "AMIGOSSHARE" or not bool(meta.anon):
            return
        logger.info(
            "PORTUGAS: [yellow]Aviso: Você solicitou um upload anônimo, mas o AMIGOSSHARE não suporta essa opção.[/yellow][red] O envio não será anônimo.[/red]"
        )
        logger.warning(
            "EN: [yellow]Warning: You requested an anonymous upload, but AMIGOSSHARE does not support this option.[/yellow][red] The upload will not be anonymous.[/red]"
        )

    async def _evaluate_dupes(
        self,
        tracker_name: str,
        local_meta: Meta,
        dupes: list[Any],
        status: dict[str, Any],
        shared_meta: Meta,
        runtime: _TrackerRuntime,
    ) -> None:
        if local_meta.skipping:
            self._mark_skipped(
                status,
                self._tracker_skip_reason(
                    tracker_name,
                    local_meta,
                    "Tracker marked this release to be skipped.",
                ),
            )
            return
        filtered = cast(
            list[Any],
            await runtime.dupe_checker.filter_dupes(
                dupes, local_meta, tracker_name
            ),
        )
        is_dupe, local_meta = await runtime.helper.dupe_check(
            filtered, local_meta, tracker_name
        )
        status["dupe"] = bool(is_dupe)
        if is_dupe:
            status.setdefault("skip_reason", "A duplicate release was found.")
        await self._copy_dupe_side_effects(
            tracker_name, local_meta, shared_meta, runtime.lock
        )

    @staticmethod
    async def _copy_dupe_side_effects(
        tracker_name: str,
        local_meta: Meta,
        shared_meta: Meta,
        lock: asyncio.Lock,
    ) -> None:
        values = TrackerStatusManager._dupe_side_effect_values(
            tracker_name, local_meta
        )
        async with lock:
            for key, value in values.items():
                if value:
                    shared_meta[key] = copy.deepcopy(value)

    @staticmethod
    def _dupe_side_effect_values(
        tracker_name: str, meta: Meta
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            f"{tracker_name}_matched_episode_ids": meta.get(
                f"{tracker_name}_matched_episode_ids", []
            ),
            "trumpable_id": meta.get("trumpable_id"),
            f"{tracker_name}_cross_seed": meta.get(
                f"{tracker_name}_cross_seed"
            ),
        }
        if tracker_name in {"AITHER", "LST"}:
            values.update(
                were_trumping=meta.get("were_trumping", False),
                trump_reason=meta.get("trump_reason"),
                **{
                    f"{tracker_name}_trumpable_id": meta.get(
                        f"{tracker_name}_trumpable_id"
                    )
                },
            )
        return values

    @classmethod
    async def _display_name(
        cls, meta: Meta, tracker_class: Any, status: dict[str, Any]
    ) -> str | None:
        if cls._display_name_blocked(status):
            return None
        try:
            value = await tracker_class.get_name(meta)
        except Exception:
            return None
        return cls._display_name_value(value)

    @staticmethod
    def _display_name_blocked(status: Mapping[str, Any]) -> bool:
        return any(
            bool(status.get(key)) for key in ("banned", "skipped", "dupe")
        )

    @staticmethod
    def _display_name_value(value: Any) -> str | None:
        if isinstance(value, Mapping):
            mapping = cast(Mapping[str, Any], value)
            name = mapping.get("name")
            return str(name) if name is not None else None
        return value if isinstance(value, str) else None

    @classmethod
    def _ensure_skip_reason(
        cls, tracker_name: str, meta: Meta, status: dict[str, Any]
    ) -> None:
        if not status.get("skipped"):
            return
        reason = str(
            status.get("skip_reason")
            or cls._tracker_skip_reason(
                tracker_name,
                meta,
                "Tracker-specific condition skipped this upload.",
            )
        )
        status["skip_reason"] = reason
        logger.info(
            f"{tracker_name}: [yellow]{reason} Skipping upload.[/yellow]"
        )

    @staticmethod
    def _tracker_skip_reason(
        tracker_name: str, meta: Meta, fallback: str
    ) -> str:
        value = meta.tracker_status.get(tracker_name, {})
        if not isinstance(value, Mapping):
            return fallback
        mapping = cast(Mapping[str, Any], value)
        reason = mapping.get("skip_reason") or mapping.get("status_message")
        return str(reason) if reason else fallback

    @staticmethod
    def _mark_skipped(status: dict[str, Any], reason: str) -> None:
        status["skipped"] = True
        status["upload"] = False
        status["skip_reason"] = reason

    def _apply_completed_episode_rule(
        self, meta: Meta, results: Sequence[_TrackerResult]
    ) -> None:
        if not Common.is_completed_tv_episode(meta):
            return
        changed = False
        for result in results:
            changed = self._block_completed_episode_result(result) or changed
        if changed:
            logger.info(
                "[bold red]Individual episodes from completed TV series are not eligible for upload. Upload the complete season pack instead.[/bold red]"
            )

    @staticmethod
    def _block_completed_episode_result(result: _TrackerResult) -> bool:
        if result.status["dupe"]:
            return False
        TrackerStatusManager._mark_skipped(
            result.status,
            "Individual episode belongs to a completed TV series; upload the season pack instead.",
        )
        return True

    @staticmethod
    def _block_completed_episode_uploads(
        meta: Meta,
        results: Sequence[tuple[str, dict[str, Any], Any, Any]],
    ) -> bool:
        """Compatibility entry point for legacy tuple-shaped tracker results."""
        if not Common.is_completed_tv_episode(meta):
            return False
        changed = False
        for name, status, display_name, tracker_class in results:
            result = _TrackerResult(name, status, display_name, tracker_class)
            changed = (
                TrackerStatusManager._block_completed_episode_result(result)
                or changed
            )
        return changed

    async def _select_uploads(
        self, meta: Meta, results: list[_TrackerResult], helper: Any
    ) -> int:
        passed, dupes, skipped = self._classify_results(results)
        self._log_result_groups(dupes, skipped)
        if meta.unattended:
            return self._enable_unattended_uploads(passed)
        return await self._select_attended_uploads(meta, passed, helper)

    @classmethod
    def _classify_results(
        cls, results: Sequence[_TrackerResult]
    ) -> tuple[list[_TrackerResult], list[str], list[str]]:
        eligibility = cls._eligibility_by_tracker(results)
        passed = [
            result for result in results if eligibility[result.name].eligible
        ]
        dupes = cls._result_names_with_flag(results, "dupe")
        skipped = cls._result_names_with_flag(results, "skipped")
        return passed, dupes, skipped

    @classmethod
    def _eligibility_by_tracker(
        cls, results: Sequence[_TrackerResult]
    ) -> dict[str, Any]:
        states = [cls._state_from_result(result) for result in results]
        return {
            item.tracker: item
            for item in evaluate_tracker_upload_eligibility(states)
        }

    @staticmethod
    def _result_names_with_flag(
        results: Sequence[_TrackerResult], flag: str
    ) -> list[str]:
        return [
            result.name for result in results if bool(result.status.get(flag))
        ]

    @staticmethod
    def _state_from_result(result: _TrackerResult) -> TrackerUploadState:
        status = result.status
        banned = bool(status["banned"])
        skipped = bool(status["skipped"])
        dupe = bool(status["dupe"])
        return TrackerUploadState(
            tracker=result.name,
            upload_allowed=not any((banned, skipped, dupe)),
            skipped=skipped,
            banned=banned,
            dupe=dupe,
            reason=str(status.get("skip_reason", "")),
        )

    @staticmethod
    def _log_result_groups(dupes: list[str], skipped: list[str]) -> None:
        if skipped:
            logger.info(
                f"[red]Skipped due to specific tracker conditions: [bold yellow]{', '.join(skipped)}[/bold yellow]."
            )
        if dupes:
            logger.info(
                f"[red]Found potential dupes on: [bold yellow]{', '.join(dupes)}[/bold yellow].\n"
            )

    @staticmethod
    def _enable_unattended_uploads(passed: Sequence[_TrackerResult]) -> int:
        names: list[str] = []
        for result in passed:
            result.status["upload"] = True
            names.append(result.name)
        if names:
            logger.info(
                f"[bold]{', '.join(names)}[/bold]: [bold green]no potential dupes found.[/bold green]"
            )
        return len(names)

    async def _select_attended_uploads(
        self, meta: Meta, passed: list[_TrackerResult], helper: Any
    ) -> int:
        manual, prompt = self._partition_attended_results(passed)
        self._enable_result_uploads(manual)
        if meta.debug:
            self._enable_result_uploads(prompt)
            return len(passed)
        selected = await self._prompt_attended_uploads(prompt, helper)
        return len(manual) + selected

    @staticmethod
    def _partition_attended_results(
        passed: Sequence[_TrackerResult],
    ) -> tuple[list[_TrackerResult], list[_TrackerResult]]:
        manual_names = {"MANUAL", "USENET"}
        manual = [result for result in passed if result.name in manual_names]
        prompt = [
            result for result in passed if result.name not in manual_names
        ]
        return manual, prompt

    @staticmethod
    def _enable_result_uploads(results: Sequence[_TrackerResult]) -> None:
        for result in results:
            result.status["upload"] = True

    async def _prompt_attended_uploads(
        self, prompt: list[_TrackerResult], helper: Any
    ) -> int:
        if not prompt:
            return 0
        self._log_prompt_candidates(prompt)
        upload_all = await self._prompt_upload_decision(prompt, helper)
        if not upload_all:
            return 0
        self._enable_result_uploads(prompt)
        return len(prompt)

    async def _prompt_upload_decision(
        self, prompt: Sequence[_TrackerResult], helper: Any
    ) -> bool:
        try:
            question = "Upload?" if len(prompt) == 1 else "Upload to all?"
            return bool(await helper.prompt_yes_no(question, default=False))
        except EOFError:
            await self._abort_tracker_processing()

    @staticmethod
    def _log_prompt_candidates(prompt: Sequence[_TrackerResult]) -> None:
        names = [result.name for result in prompt]
        label = names[0] if len(names) == 1 else ", ".join(names)
        logger.info(
            f"[bold]{label}:[/bold] [green]no potential dupes found.[/green]"
        )

    @staticmethod
    async def _abort_tracker_processing() -> Never:
        logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise OperationAbortedError(
            "Tracker processing was cancelled by the user."
        )

    @staticmethod
    def _log_debug_summary(
        meta: Meta, results: Sequence[_TrackerResult], successful: int
    ) -> None:
        if not meta.debug:
            return
        logger.debug("\n[bold]Tracker Processing Summary:[/bold]")
        for result in results:
            TrackerStatusManager._log_debug_result(result)
        logger.debug(
            f"\n[bold]Trackers Passed all Checks:[/bold] {successful}"
        )
        logger.debug("", extra={"markup": False})
        logger.debug("[bold red]DEBUG MODE does not upload to sites")

    @classmethod
    def _log_debug_result(cls, result: _TrackerResult) -> None:
        status = result.status
        reason = str(status.get("skip_reason", ""))
        logger.debug(
            cls._debug_result_line(
                result.name,
                banned=bool(status["banned"]),
                skipped=bool(status["skipped"]),
                dupe=bool(status["dupe"]),
                upload=bool(status["upload"]),
                reason=reason,
            )
        )

    @classmethod
    def _debug_result_line(
        cls,
        tracker: str,
        *,
        banned: bool,
        skipped: bool,
        dupe: bool,
        upload: bool,
        reason: str,
    ) -> str:
        line = f"Tracker: {tracker} | Banned: {cls._yes_no(banned)} | Skipped: {cls._yes_no(skipped)} | Dupe: {cls._yes_no(dupe)} | [yellow]Upload:[/yellow] {cls._yes_no(upload)}"
        return cls._append_debug_reason(line, reason)

    @staticmethod
    def _yes_no(value: bool) -> str:
        return "Yes" if value else "No"

    @staticmethod
    def _append_debug_reason(line: str, reason: str) -> str:
        return f"{line} | Reason: {reason}" if reason else line

    @classmethod
    def _tracker_names(cls, value: Any) -> list[str]:
        values = cls._tracker_name_values(value)
        return [name for item in values if (name := cls._tracker_name(item))]

    @staticmethod
    def _tracker_name_values(value: Any) -> Sequence[Any]:
        if isinstance(value, str):
            return value.split(",")
        if isinstance(value, Sequence):
            return cast(Sequence[Any], value)
        return ()

    @staticmethod
    def _tracker_name(value: Any) -> str:
        return str(value).strip().upper()


async def process_all_trackers(meta: Meta, config: dict[str, Any]) -> int:
    return await TrackerStatusManager(config=config).process_all_trackers(meta)
