# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import cli_ui
from rich.markup import escape

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import screenshot_requirement_error
from src.engines.upload_safety_policy import (
    book_metadata_cjk_fields,
    invalid_release_group_tag,
)
from src.integrations.filesystem.cleanup import cleanup_manager
from src.integrations.image_hosts.rehosting import (
    check_tracker_image_hosts,
    has_restricted_image_hosts,
    select_common_image_host,
)
from src.integrations.media.artwork import (
    audiobook_cover_missing_or_invalid,
    is_valid_cover_image,
)
from src.integrations.media.zentag import (
    prepare_zenith_audiobook,
    prepare_zenith_ebook,
    should_prepare_zenith_audiobook,
    should_prepare_zenith_ebook,
)
from src.integrations.observability.runtime_support import logger
from src.integrations.observability.terminal_link_formatting import (
    format_terminal_link,
)
from src.integrations.packaging.manual_package import ManualPackageManager
from src.integrations.security.redaction import Redaction
from src.integrations.torrent_clients.bandwidth import Wait
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.passthepopcorn import PassThePopcorn
from src.integrations.trackers.registry import TrackerSetup
from src.integrations.trackers.UNIT3D.znth import prepare_zenith_music_layout
from src.services.argument_override_service import ArgumentParserFactory
from src.services.duplicate_check_service import DupeChecker
from src.services.preparation_service import Prep

type StatusDict = dict[str, Any]

_TRACKER_CAPABILITIES: dict[str, dict[str, bool]] = {
    "AURA4K": {"mod_q": True, "draft": False},
    "AITHER": {"mod_q": True, "draft": False},
    "BEYONDHD": {"draft_live": True},
    "BLUTOPIA": {"mod_q": True, "draft": False},
    "LST": {"mod_q": True, "draft": True},
    "LATTEAM": {"mod_q": True, "draft": False},
    "LUMINARR": {"mod_q": True, "draft": False},
}


def _tracker_prepared_meta(shared_meta: Meta, tracker: str) -> Meta:
    prepared_by_tracker = shared_meta.get("tracker_prepared_meta", {})
    prepared: Any = None
    if isinstance(prepared_by_tracker, dict):
        prepared_mapping = cast(dict[str, Any], prepared_by_tracker)
        prepared = prepared_mapping.get(tracker)
    if isinstance(prepared, Meta):
        return prepared.copy()
    if isinstance(prepared, dict):
        return Meta(prepared)
    return shared_meta.copy()


def _sync_tracker_context(
    tracker_meta: Meta, shared_meta: Meta, tracker: str
) -> Meta:
    tracker_meta.trackers = [tracker]
    tracker_meta.tracker_status = shared_meta.tracker_status
    return tracker_meta


def _mark_zenith_skipped(shared_meta: Meta, message: str) -> None:
    status = shared_meta.tracker_status.setdefault("ZENITH", {})
    status.update(upload=False, skipped=True, status_message=message)


def _zenith_preparation_required(meta: Meta, config: dict[str, Any]) -> bool:
    audiobook_required = should_prepare_zenith_audiobook(meta, config)
    ebook_required = should_prepare_zenith_ebook(meta, config)
    return audiobook_required or ebook_required


async def _zenith_prepared_path(
    meta: Meta, config: dict[str, Any]
) -> str | None:
    prepared = await prepare_zenith_audiobook(meta, str(meta.base_dir), config)
    if prepared is not None:
        return prepared
    return await prepare_zenith_ebook(meta, str(meta.base_dir), config)


def _required_zenith_preparation_failed(
    required: bool, prepared_path: str | None
) -> bool:
    return required and not bool(prepared_path)


def _zenith_isolated_meta(meta: Meta, prepared_path: str) -> Meta:
    prepared_meta = meta.copy()
    prepared_meta.path = prepared_path
    prepared_meta.keep_folder = True
    prepared_meta.allow_spaces = True
    prepared_meta.uuid = f"{prepared_meta.uuid}-zenith"
    prepared_meta.update({"trusted_book_layout": True})
    return prepared_meta


async def _gather_zenith_meta(
    tracker_meta: Meta,
    shared_meta: Meta,
    config: dict[str, Any],
    prepared_path: str,
    argument_parser_factory: ArgumentParserFactory | None,
) -> Meta:
    prepared_meta = _zenith_isolated_meta(tracker_meta, prepared_path)
    prep = Prep(
        screens=prepared_meta.screens,
        img_host=prepared_meta.imghost,
        config=config,
        argument_parser_factory=argument_parser_factory,
    )
    gathered = await prep.gather_prep(meta=prepared_meta, mode="cli")
    _sync_tracker_context(gathered, shared_meta, "ZENITH")
    gathered.update({"zentag_prepared": True})
    return gathered


async def prepare_tracker_meta(
    shared_meta: Meta,
    tracker: str,
    config: dict[str, Any],
    argument_parser_factory: ArgumentParserFactory | None = None,
) -> Meta:
    tracker_meta = _sync_tracker_context(
        _tracker_prepared_meta(shared_meta, tracker), shared_meta, tracker
    )
    if tracker != "ZENITH":
        return tracker_meta

    preparation_required = _zenith_preparation_required(tracker_meta, config)
    prepared_book = await _zenith_prepared_path(tracker_meta, config)
    if _required_zenith_preparation_failed(
        preparation_required, prepared_book
    ):
        _mark_zenith_skipped(
            shared_meta,
            "Automatic zentag preparation failed; the original book will not be uploaded",
        )
        return tracker_meta
    if prepared_book:
        try:
            tracker_meta = await _gather_zenith_meta(
                tracker_meta,
                shared_meta,
                config,
                prepared_book,
                argument_parser_factory,
            )
        except Exception as error:
            logger.warning(
                f"[yellow]ZENITH: failed to prepare isolated zentag metadata; the original release will not be uploaded: {error}[/yellow]"
            )
            _mark_zenith_skipped(
                shared_meta,
                f"Prepared zentag metadata failed validation: {error}",
            )
    prepare_zenith_music_layout(tracker_meta)
    return tracker_meta


def _enabled_flag(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


async def _tracker_boolean_flag(
    tracker_class: Any, meta: Meta, name: str, enabled: bool
) -> str | None:
    if not enabled:
        return None
    value = await tracker_class.get_flag(meta, name)
    return "Yes" if _enabled_flag(value) else "No"


async def _tracker_draft_value(
    tracker_class: Any, meta: Meta, capabilities: Mapping[str, Any]
) -> str | None:
    if capabilities.get("draft_live"):
        live = await tracker_class.get_live(meta)
        return "Draft" if live == 0 else "Live"
    return await _tracker_boolean_flag(
        tracker_class, meta, "draft", bool(capabilities.get("draft"))
    )


async def check_mod_q_and_draft(
    tracker_class: Any,
    meta: Meta,
) -> tuple[str | None, str | None, dict[str, Any]]:
    tracker_caps = _TRACKER_CAPABILITIES.get(tracker_class.tracker, {})
    modq = await _tracker_boolean_flag(
        tracker_class, meta, "modq", bool(tracker_caps.get("mod_q"))
    )
    draft = await _tracker_draft_value(tracker_class, meta, tracker_caps)
    return modq, draft, tracker_caps


def _duration_color(duration: float) -> str:
    if duration < 5:
        return "#21ff00"
    if duration < 10:
        return "#9fd600"
    if duration < 15:
        return "#cfaa00"
    if duration < 20:
        return "#f17100"
    return "#ff0000"


def _upload_duration_part(
    meta: Meta, tracker: str, default_config: Mapping[str, Any]
) -> str | None:
    show_duration = bool(default_config.get("show_upload_duration", True))
    if not show_duration and not meta.upload_timer:
        return None
    duration = meta.get(f"{tracker}_upload_duration")
    if not duration or not isinstance(duration, (int, float)):
        return None
    color = _duration_color(float(duration))
    return f"[[{color}]{duration:.2f}s[/{color}]]"


def _tracker_result_link(status: Mapping[str, Any], tracker_class: Any) -> str:
    if "torrent_id" not in status:
        return ""
    torrent_url = str(getattr(tracker_class, "torrent_url", ""))
    return f"{torrent_url}{status['torrent_id']}"


def _tracker_link_part(
    meta: Meta, link_url: str, config: Mapping[str, Any]
) -> str | None:
    if not meta.print_tracker_links or not link_url:
        return None
    default_config = cast(dict[str, Any], config["DEFAULT"])
    link = format_terminal_link("link", link_url, default_config)
    return f"[[green]{link}[/green]]"


def _has_printable_status_message(status_message: str) -> bool:
    if not status_message:
        return False
    return "data error" not in status_message


def _should_print_status_message(
    meta: Meta, has_status_message: bool, link_url: str
) -> bool:
    if not has_status_message:
        return False
    if meta.print_tracker_messages:
        return True
    return bool(meta.print_tracker_links and not link_url)


def _tracker_status_part(
    meta: Meta, status: Mapping[str, Any], link_url: str
) -> str | None:
    status_message = str(status.get("status_message", ""))
    has_status = _has_printable_status_message(status_message)
    if not _should_print_status_message(meta, has_status, link_url):
        return None
    redacted = Redaction.redact_private_info(status_message)
    return escape(redacted)


def _success_tracker_message(
    tracker: str,
    tracker_class: Any,
    status: Mapping[str, Any],
    meta: Meta,
    default_config: Mapping[str, Any],
    config: Mapping[str, Any],
    tracker_label_width: int,
) -> str | None:
    link_url = _tracker_result_link(status, tracker_class)
    parts = [
        _upload_duration_part(meta, tracker, default_config),
        _tracker_link_part(meta, link_url, config),
        _tracker_status_part(meta, status, link_url),
    ]
    result_parts = [part for part in parts if part is not None]
    if not result_parts:
        return None
    tracker_label = f"{tracker.ljust(tracker_label_width)}:"
    return f"{tracker_label} {' '.join(result_parts)}"


def _data_error_message(
    tracker: str, status: Mapping[str, Any], tracker_label_width: int
) -> str | None:
    if "status_message" not in status:
        return None
    status_message = str(status["status_message"])
    if "data error" not in status_message:
        return None
    tracker_label = f"{tracker.ljust(tracker_label_width)}:"
    return f"[red]{tracker_label} {escape(status_message)}[/red]"


def _result_printing_enabled(meta: Meta) -> bool:
    return bool(meta.print_tracker_links or meta.print_tracker_messages)


def _log_success_tracker_result(
    tracker: str,
    tracker_class: Any,
    status: Mapping[str, Any],
    meta: Meta,
    default_config: Mapping[str, Any],
    config: Mapping[str, Any],
    tracker_label_width: int,
) -> None:
    message = _success_tracker_message(
        tracker,
        tracker_class,
        status,
        meta,
        default_config,
        config,
        tracker_label_width,
    )
    if message is not None:
        logger.info(message, extra={"highlighter": None})


def _log_failed_tracker_result(
    tracker: str, status: Mapping[str, Any], tracker_label_width: int
) -> None:
    message = _data_error_message(tracker, status, tracker_label_width)
    if message is not None:
        logger.info(message)


def _emit_tracker_result(
    tracker: str,
    tracker_class: Any,
    status: Mapping[str, Any],
    is_success: bool,
    meta: Meta,
    default_config: Mapping[str, Any],
    config: Mapping[str, Any],
    tracker_label_width: int,
) -> None:
    if is_success:
        _log_success_tracker_result(
            tracker,
            tracker_class,
            status,
            meta,
            default_config,
            config,
            tracker_label_width,
        )
        return
    _log_failed_tracker_result(tracker, status, tracker_label_width)


def _print_tracker_result(
    tracker: str,
    tracker_class: Any,
    status: Mapping[str, Any],
    is_success: bool,
    *,
    meta: Meta,
    default_config: Mapping[str, Any],
    config: Mapping[str, Any],
    tracker_label_width: int,
) -> None:
    try:
        if not _result_printing_enabled(meta):
            return
        _emit_tracker_result(
            tracker,
            tracker_class,
            status,
            is_success,
            meta,
            default_config,
            config,
            tracker_label_width,
        )
    except Exception as error:
        logger.error(f"[red]Error printing {tracker} result: {error}[/red]")


@dataclass(slots=True)
class _TrackerUploadContext:
    shared_meta: Meta
    config: dict[str, Any]
    client: Any
    api_trackers: frozenset[str]
    tracker_class_map: Mapping[str, Any]
    http_trackers: frozenset[str]
    other_api_trackers: frozenset[str]
    upload_target: str
    argument_parser_factory: ArgumentParserFactory | None
    enabled_trackers: list[str]
    default_config: dict[str, Any]
    disabled_trackers: dict[str, str]
    manual_packager: ManualPackageManager
    tracker_label_width: int


def _default_upload_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("DEFAULT", {})
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[Any, Any], value)
        return {str(key): item for key, item in mapping.items()}
    return {}


def _runtime_disabled_trackers(config: dict[str, Any]) -> dict[str, str]:
    runtime_value = config.get("_runtime")
    if not isinstance(runtime_value, dict):
        runtime_value = {}
        config["_runtime"] = runtime_value
    runtime = cast(dict[str, Any], runtime_value)
    disabled_value = runtime.get("disabled_trackers")
    if not isinstance(disabled_value, dict):
        disabled_value = {}
        runtime["disabled_trackers"] = disabled_value
    return cast(dict[str, str], disabled_value)


def _enabled_tracker_names(config: dict[str, Any], meta: Meta) -> list[str]:
    setup = TrackerSetup(config=config)
    setup_any = cast(Any, setup)
    enabled = cast(Sequence[str], setup_any.trackers_enabled(meta))
    return list(enabled)


def _tracker_label_width(trackers: Sequence[str]) -> int:
    labels = [
        len(str(tracker).replace(" ", "").upper().strip())
        for tracker in trackers
    ]
    return max(labels, default=0)


def _build_upload_context(
    meta: Meta,
    config: dict[str, Any],
    client: Any,
    api_trackers: Sequence[str],
    tracker_class_map: Mapping[str, Any],
    http_trackers: Sequence[str],
    other_api_trackers: Sequence[str],
    upload_target: str,
    argument_parser_factory: ArgumentParserFactory | None,
) -> _TrackerUploadContext:
    enabled = _enabled_tracker_names(config, meta)
    return _TrackerUploadContext(
        shared_meta=meta,
        config=config,
        client=client,
        api_trackers=frozenset(api_trackers),
        tracker_class_map=tracker_class_map,
        http_trackers=frozenset(http_trackers),
        other_api_trackers=frozenset(other_api_trackers),
        upload_target=upload_target,
        argument_parser_factory=argument_parser_factory,
        enabled_trackers=enabled,
        default_config=_default_upload_config(config),
        disabled_trackers=_runtime_disabled_trackers(config),
        manual_packager=ManualPackageManager(config),
        tracker_label_width=_tracker_label_width(enabled),
    )


def _tracker_upload_enabled(meta: Meta, tracker: str) -> bool:
    status = meta.tracker_status.get(tracker, {})
    if not isinstance(status, Mapping):
        return False
    return bool(cast(Mapping[str, Any], status).get("upload", False))


def _non_manual_trackers(trackers: Sequence[str]) -> list[str]:
    return [tracker for tracker in trackers if tracker != "MANUAL"]


def _upload_enabled_trackers(meta: Meta, trackers: Sequence[str]) -> list[str]:
    return [
        tracker
        for tracker in trackers
        if _tracker_upload_enabled(meta, tracker)
    ]


def _smart_host_targets(ctx: _TrackerUploadContext) -> list[str]:
    non_manual = _non_manual_trackers(ctx.enabled_trackers)
    if "MANUAL" in ctx.enabled_trackers:
        return non_manual
    return _upload_enabled_trackers(ctx.shared_meta, non_manual)


def _smart_host_selection_enabled(ctx: _TrackerUploadContext) -> bool:
    enabled = bool(ctx.default_config.get("smart_image_host_selection", True))
    return enabled and not ctx.shared_meta.imghost_from_cli


def _current_image_host(ctx: _TrackerUploadContext) -> str:
    fallback = ctx.default_config.get("img_host_1", "")
    value = ctx.shared_meta.imghost if ctx.shared_meta.imghost else fallback
    return str(value).strip().lower()


def _apply_selected_smart_host(
    ctx: _TrackerUploadContext, selected: str | None
) -> bool:
    if not selected:
        return False
    previous = _current_image_host(ctx)
    ctx.shared_meta.imghost = selected
    if selected != previous:
        logger.info(
            f"[green]Smart image-host selection changed the target host: {previous or 'unset'} -> {selected}[/green]"
        )
    return True


def _log_missing_shared_image_host(
    targets: Sequence[str], tracker_class_map: Mapping[str, Any]
) -> None:
    if not has_restricted_image_hosts(targets, tracker_class_map):
        return
    logger.info(
        "[yellow]No shared approved image host found; using per-tracker image-host selection.[/yellow]"
    )


def _apply_smart_image_host(ctx: _TrackerUploadContext) -> None:
    if not _smart_host_selection_enabled(ctx):
        return
    targets = _smart_host_targets(ctx)
    selected = select_common_image_host(
        ctx.default_config, targets, ctx.tracker_class_map
    )
    if _apply_selected_smart_host(ctx, selected):
        return
    _log_missing_shared_image_host(targets, ctx.tracker_class_map)


def _ctx_print_tracker_result(
    ctx: _TrackerUploadContext,
    tracker: str,
    tracker_class: Any,
    status: Mapping[str, Any],
    is_success: bool,
) -> None:
    _print_tracker_result(
        tracker,
        tracker_class,
        status,
        is_success,
        meta=ctx.shared_meta,
        default_config=ctx.default_config,
        config=ctx.config,
        tracker_label_width=ctx.tracker_label_width,
    )


def _preferred_setting(primary: Any, fallback: Any) -> Any:
    return primary if primary else fallback


def _bandwidth_control_enabled(
    meta: Meta, default_config: Mapping[str, Any]
) -> bool:
    value = _preferred_setting(
        meta.qbit_bandwidth_control,
        default_config.get("qbit_bandwidth_control", False),
    )
    return bool(value)


def _bandwidth_settings(
    meta: Meta, default_config: Mapping[str, Any]
) -> tuple[int, int]:
    threshold_raw = _preferred_setting(
        meta.qbit_bandwidth_threshold,
        default_config.get("qbit_bandwidth_threshold", 0),
    )
    time_raw = _preferred_setting(
        meta.qbit_bandwidth_time,
        default_config.get("qbit_bandwidth_time", 0),
    )
    try:
        return int(threshold_raw), int(time_raw)
    except (TypeError, ValueError) as error:
        logger.info(
            f"[red]Invalid bandwidth settings: {error}, skipping bandwidth wait.[/red]"
        )
        return 0, 0


def _positive_bandwidth_settings(settings: tuple[int, int]) -> bool:
    threshold, wait_time = settings
    return threshold > 0 and wait_time > 0


async def _waited_for_bandwidth(
    ctx: _TrackerUploadContext, meta: Meta, tracker_name: str
) -> bool:
    if not _bandwidth_control_enabled(meta, ctx.default_config):
        return False
    logger.info(f"\n[yellow]{tracker_name}: Checking bandwidth...[/yellow]")
    settings = _bandwidth_settings(meta, ctx.default_config)
    if not _positive_bandwidth_settings(settings):
        return False
    threshold, wait_time = settings
    return await Wait(ctx.config).wait_for_bandwidth(threshold, wait_time)


def _tracker_is_usenet(tracker_class: Any) -> bool:
    if tracker_class is None:
        return False
    return bool(getattr(tracker_class, "is_usenet", False))


async def _recheck_dupes(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker_name: str,
    tracker_class: Any,
) -> list[Any]:
    if tracker_name != "PASSTHEPOPCORN":
        return cast(list[Any], await tracker_class.search_existing(meta))
    ptp = PassThePopcorn(config=ctx.config)
    group_id = meta.ptp_groupid
    return cast(list[Any], await ptp.search_existing(group_id or "", meta))


def _record_dupe_recheck_error(
    meta: Meta, tracker_name: str, error: Exception
) -> None:
    logger.info(
        f"[bold red]{tracker_name}: Error redoing duplicate check after bandwidth wait: {error}[/bold red]"
    )
    status = meta.tracker_status.setdefault(tracker_name, {})
    status["status_message"] = (
        f"Skipped: Error redoing dupe check after bandwidth wait: {error}"
    )


async def _safe_recheck_dupes(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker_name: str,
    tracker_class: Any,
) -> list[Any] | None:
    try:
        return await _recheck_dupes(ctx, meta, tracker_name, tracker_class)
    except Exception as error:
        _record_dupe_recheck_error(meta, tracker_name, error)
        return None


def _dict_dupes_match(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> bool:
    return first.get("name") == second.get("name") and first.get(
        "size"
    ) == second.get("size")


def _dupes_match(first: Any, second: Any) -> bool:
    if isinstance(first, dict) and isinstance(second, dict):
        return _dict_dupes_match(
            cast(Mapping[str, Any], first), cast(Mapping[str, Any], second)
        )
    if isinstance(first, str) and isinstance(second, str):
        return first == second
    return False


def _initial_dupes(meta: Meta, tracker_name: str) -> list[Any]:
    raw = meta.initial_dupes
    if not isinstance(raw, dict):
        return []
    values = cast(dict[str, Any], raw).get(tracker_name, [])
    return cast(list[Any], values) if isinstance(values, list) else []


def _dupe_in_initial(dupe: Any, initial_dupes: list[Any]) -> bool:
    return any(_dupes_match(dupe, initial) for initial in initial_dupes)


def _new_dupes_only(
    meta: Meta, tracker_name: str, new_dupes: list[Any]
) -> list[Any]:
    initial = _initial_dupes(meta, tracker_name)
    return [dupe for dupe in new_dupes if not _dupe_in_initial(dupe, initial)]


async def _has_blocking_new_dupe(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker_name: str,
    new_dupes: list[Any],
) -> bool:
    real_new_dupes = _new_dupes_only(meta, tracker_name, new_dupes)
    if not real_new_dupes:
        return False
    filtered = cast(
        list[Any],
        await DupeChecker(ctx.config).filter_dupes(
            real_new_dupes, meta, tracker_name
        ),
    )
    if not filtered:
        return False
    from src.services.upload_decision_service import UploadHelper

    is_dupe, _ = await UploadHelper(ctx.config).dupe_check(
        filtered, meta, tracker_name
    )
    if is_dupe:
        logger.info(
            f"[red]New dupe found on {tracker_name} during wait! Automatically skipping upload.[/red]"
        )
    return bool(is_dupe)


async def _check_bandwidth_and_dupes(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker_name: str,
    tracker_class: Any,
) -> bool:
    if _tracker_is_usenet(tracker_class):
        return True
    if not await _waited_for_bandwidth(ctx, meta, tracker_name):
        return True
    logger.info(
        f"[yellow]{tracker_name}: Redoing dupe check after bandwidth wait...[/yellow]"
    )
    new_dupes = await _safe_recheck_dupes(
        ctx, meta, tracker_name, tracker_class
    )
    if new_dupes is None:
        return False
    return not await _has_blocking_new_dupe(ctx, meta, tracker_name, new_dupes)


def _status(meta: Meta, tracker: str) -> StatusDict:
    return meta.tracker_status.setdefault(tracker, {})


def _mark_bandwidth_skip(
    ctx: _TrackerUploadContext, meta: Meta, tracker: str, tracker_class: Any
) -> None:
    status = _status(meta, tracker_class.tracker)
    status.setdefault(
        "status_message", "Skipped due to new dupe found after bandwidth wait"
    )
    _ctx_print_tracker_result(ctx, tracker, tracker_class, status, False)


async def _tracker_images_ready(
    ctx: _TrackerUploadContext, meta: Meta, tracker: str, tracker_class: Any
) -> bool:
    if meta.debug:
        return True
    await check_tracker_image_hosts(meta, tracker_class)
    screenshot_error = screenshot_requirement_error(meta, ctx.config, tracker)
    if not screenshot_error:
        return True
    status = _status(meta, tracker)
    status.update(upload=False, skipped=True, status_message=screenshot_error)
    logger.info(
        f"[yellow]{tracker}: {escape(screenshot_error)} Skipping upload.[/yellow]"
    )
    return False


def _log_modq_value(
    tracker: str, capabilities: Mapping[str, Any], modq: str | None
) -> None:
    if capabilities.get("mod_q") and modq == "Yes":
        logger.info(f"{tracker} (modq: {modq})")


def _draft_capable(capabilities: Mapping[str, Any]) -> bool:
    return bool(capabilities.get("draft") or capabilities.get("draft_live"))


def _log_draft_value(
    tracker: str, capabilities: Mapping[str, Any], draft: str | None
) -> None:
    if _draft_capable(capabilities) and draft in {"Yes", "Draft"}:
        logger.info(f"{tracker} (draft: {draft})")


async def _log_api_tracker_flags(
    tracker: str, tracker_class: Any, meta: Meta
) -> None:
    modq, draft, capabilities = await check_mod_q_and_draft(
        tracker_class, meta
    )
    _log_modq_value(tracker, capabilities, modq)
    _log_draft_value(tracker, capabilities, draft)


async def _standard_upload_preflight(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    tracker_class: Any,
    *,
    api_tracker: bool,
) -> bool:
    if api_tracker:
        await _log_api_tracker_flags(tracker, tracker_class, meta)
    if not await _check_bandwidth_and_dupes(ctx, meta, tracker, tracker_class):
        _mark_bandwidth_skip(ctx, meta, tracker, tracker_class)
        return False
    return await _tracker_images_ready(ctx, meta, tracker, tracker_class)


_UPLOAD_ERROR = object()


async def _timed_tracker_upload(
    meta: Meta, shared_meta: Meta, tracker: str, tracker_class: Any
) -> object:
    try:
        started = time.time()
        uploaded = await tracker_class.upload(meta)
        duration = time.time() - started
        meta[f"{tracker}_upload_duration"] = duration
        shared_meta[f"{tracker}_upload_duration"] = duration
        return uploaded
    except Exception as error:
        logger.info(f"[red]Upload failed: {error}")
        logger.info(traceback.format_exc())
        return _UPLOAD_ERROR


def _normalized_upload_result(result: object, tracker_class: Any) -> bool:
    if result is not None:
        return bool(result)
    logger.warning(
        f"[yellow]Warning: {tracker_class.tracker} upload method returned None instead of boolean. Treating as failed upload.[/yellow]"
    )
    return False


def _status_is_dupe(status: Mapping[str, Any]) -> bool:
    return status.get("dupe") is True


def _successful_upload_result(
    uploaded: bool, status: Mapping[str, Any]
) -> bool:
    if not uploaded:
        return False
    return "data error" not in str(status.get("status_message", ""))


async def _finalize_successful_upload(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    tracker_class: Any,
    status: StatusDict,
) -> None:
    status["upload_success"] = True
    if not meta.debug and not _tracker_is_usenet(tracker_class):
        await ctx.client.add_to_client(meta, tracker_class.tracker)
    _ctx_print_tracker_result(ctx, tracker, tracker_class, status, True)


def _failure_detail(status: Mapping[str, Any]) -> str:
    value = status.get("status_message")
    return (
        str(value)
        if value
        else "No error details were returned by the tracker."
    )


def _record_failed_upload(
    ctx: _TrackerUploadContext,
    tracker: str,
    tracker_class: Any,
    status: StatusDict,
) -> None:
    status["upload_success"] = False
    _ctx_print_tracker_result(ctx, tracker, tracker_class, status, False)
    detail = _failure_detail(status)
    if "modqueue limit reached" in detail.lower():
        ctx.disabled_trackers[tracker] = detail
    logger.info(
        f"[red]{tracker} upload failed or returned data error: {detail}[/red]"
    )


async def _finalize_standard_upload(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    tracker_class: Any,
    uploaded: bool,
) -> None:
    status = _status(meta, tracker_class.tracker)
    if _status_is_dupe(status):
        status.pop("upload_success", None)
        logger.info(
            f"[yellow]{tracker}: release already exists on the tracker. Skipping duplicate upload.[/yellow]"
        )
        return
    if _successful_upload_result(uploaded, status):
        await _finalize_successful_upload(
            ctx, meta, tracker, tracker_class, status
        )
        return
    _record_failed_upload(ctx, tracker, tracker_class, status)


async def _run_standard_tracker_upload(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    tracker_class: Any,
    *,
    api_tracker: bool,
) -> None:
    if not _tracker_upload_enabled(meta, tracker):
        return
    ready = await _standard_upload_preflight(
        ctx, meta, tracker, tracker_class, api_tracker=api_tracker
    )
    if not ready:
        return
    result = await _timed_tracker_upload(
        meta, ctx.shared_meta, tracker, tracker_class
    )
    if result is _UPLOAD_ERROR:
        return
    await _finalize_standard_upload(
        ctx,
        meta,
        tracker,
        tracker_class,
        _normalized_upload_result(result, tracker_class),
    )


def _normalized_tracker_name(tracker: str) -> str:
    return tracker.replace(" ", "").upper().strip()


async def _prepared_single_tracker_meta(
    ctx: _TrackerUploadContext, tracker: str
) -> Meta:
    if ctx.argument_parser_factory is None:
        return await prepare_tracker_meta(ctx.shared_meta, tracker, ctx.config)
    return await prepare_tracker_meta(
        ctx.shared_meta, tracker, ctx.config, ctx.argument_parser_factory
    )


def _tracker_adapter(ctx: _TrackerUploadContext, tracker: str) -> Any:
    if tracker in {"MANUAL", "PASSTHEPOPCORN"}:
        return None
    return ctx.tracker_class_map[tracker](config=ctx.config)


def _zenith_prepared(meta: Meta, tracker: str) -> bool:
    return tracker == "ZENITH" and bool(meta.get("zentag_prepared", False))


async def _apply_zenith_upload_guard(
    meta: Meta, tracker: str, tracker_class: Any
) -> None:
    if not _zenith_prepared(meta, tracker):
        return
    if await tracker_class.get_additional_checks(meta):
        return
    status = _status(meta, tracker)
    status.update(
        upload=False,
        skipped=True,
        status_message="Prepared zentag audiobook failed Zenith validation",
    )


def _apply_cjk_upload_guard(meta: Meta, tracker: str) -> None:
    fields = book_metadata_cjk_fields(meta)
    if not fields or _zenith_prepared(meta, tracker):
        return
    field_names = ", ".join(fields)
    status = _status(meta, tracker)
    status.update(
        upload=False,
        skipped=True,
        status_message=f"BOOK metadata contains CJK characters in: {field_names}",
    )
    logger.info(
        f"[yellow]{tracker}: BOOK metadata still contains CJK characters in {field_names}; skipping upload.[/yellow]"
    )


def _strip_dupe_suffix(meta: Meta) -> None:
    if meta.name.endswith("DUPE?"):
        meta.name = meta.name.replace(" DUPE?", "")


def _skip_disabled_tracker(
    ctx: _TrackerUploadContext, meta: Meta, tracker: str
) -> bool:
    reason = ctx.disabled_trackers.get(tracker)
    if not reason:
        return False
    status = _status(meta, tracker)
    status.update(
        upload=False,
        skipped=True,
        status_message=f"Skipped for the remainder of this run: {reason}",
    )
    logger.info(
        f"[yellow]{tracker}: skipped for the remainder of this run: {escape(str(reason))}[/yellow]"
    )
    return True


def _tracker_requires_book_cover(tracker_class: Any) -> bool:
    return bool(getattr(tracker_class, "requires_book_cover", True))


def _skip_missing_book_cover(
    meta: Meta, tracker: str, tracker_class: Any
) -> bool:
    if meta.category != "BOOK":
        return False
    if not _tracker_requires_book_cover(tracker_class):
        return False
    if is_valid_cover_image(meta.artwork_path):
        return False
    status = _status(meta, tracker)
    status["upload"] = False
    status["status_message"] = (
        "Skipped: BOOK uploads require a valid cover image"
    )
    logger.info(
        f"[yellow]{tracker}: skipped because BOOK uploads require a valid cover image.[/yellow]"
    )
    return True


async def _manual_upload_requested(meta: Meta) -> bool:
    if meta.unattended:
        return True
    try:
        return bool(
            cli_ui.ask_yes_no("Get files for manual upload?", default=True)
        )
    except EOFError:
        logger.info("\n[red]Exiting on user request (Ctrl+C)[/red]")
        await cleanup_manager.cleanup()
        cleanup_manager.reset_terminal()
        raise OperationAbortedError(
            "Manual tracker upload confirmation was cancelled by the user."
        ) from None


async def _prepare_manual_tracker(
    ctx: _TrackerUploadContext, meta: Meta, manual_tracker: str
) -> None:
    tracker = _normalized_tracker_name(manual_tracker)
    tracker_class = ctx.tracker_class_map[tracker](config=ctx.config)
    try:
        await check_tracker_image_hosts(meta, tracker_class)
        if tracker in ctx.api_trackers:
            await DescriptionBuilder(
                tracker, ctx.config
            ).general_description_generator(
                meta,
                languages=False,
                mediainfo=False,
                nfo=False,
                signature=tracker,
            )
        else:
            await tracker_class.edit_desc(meta)
    except Exception as error:
        logger.info(
            f"[red]{tracker}: Error preparing manual upload files: {error}[/red]"
        )


async def _package_manual_upload(
    ctx: _TrackerUploadContext, meta: Meta
) -> None:
    url = await ctx.manual_packager.package(meta)
    if url is False:
        logger.info(
            f"[yellow]Unable to upload prep files, they can be found at `tmp/{meta.uuid}"
        )
        return
    logger.info(f"[green]{meta.name}")
    logger.info(f"[green]Files can be found at: [yellow]{url}[/yellow]")


async def _run_manual_upload(ctx: _TrackerUploadContext, meta: Meta) -> None:
    if not await _manual_upload_requested(meta):
        return
    for tracker in ctx.enabled_trackers:
        if tracker != "MANUAL":
            await _prepare_manual_tracker(ctx, meta, tracker)
    await _package_manual_upload(ctx, meta)


async def _timed_ptp_upload(
    meta: Meta,
    shared_meta: Meta,
    tracker: str,
    ptp: Any,
    ptp_url: str,
    ptp_data: Any,
) -> object:
    try:
        started = time.time()
        uploaded = await ptp.upload(meta, ptp_url, ptp_data)
        duration = time.time() - started
        meta[f"{tracker}_upload_duration"] = duration
        shared_meta[f"{tracker}_upload_duration"] = duration
        return uploaded
    except Exception as error:
        logger.info(f"[red]Upload failed: {error}")
        logger.info(traceback.format_exc())
        return _UPLOAD_ERROR


async def _finalize_ptp_upload(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    ptp: Any,
    uploaded: bool,
) -> None:
    status = _status(meta, ptp.tracker)
    if _successful_upload_result(uploaded, status):
        status["upload_success"] = True
        if not meta.debug:
            await ctx.client.add_to_client(meta, "PASSTHEPOPCORN")
        _ctx_print_tracker_result(ctx, tracker, ptp, status, True)
        return
    status["upload_success"] = False
    _ctx_print_tracker_result(ctx, tracker, ptp, status, False)
    logger.info(f"[red]{tracker} upload failed or returned data error.[/red]")


async def _run_ptp_upload(
    ctx: _TrackerUploadContext, meta: Meta, tracker: str
) -> None:
    if not _tracker_upload_enabled(meta, tracker):
        return
    try:
        ptp = PassThePopcorn(config=ctx.config)
        if not meta.debug:
            await check_tracker_image_hosts(meta, ptp)
        ptp_url, ptp_data = await ptp.fill_upload_form(meta.ptp_groupid, meta)
        result = await _timed_ptp_upload(
            meta, ctx.shared_meta, tracker, ptp, ptp_url, ptp_data
        )
        if result is _UPLOAD_ERROR:
            return
        await _finalize_ptp_upload(ctx, meta, tracker, ptp, bool(result))
    except Exception:
        logger.info(traceback.format_exc())


def _tracker_route(ctx: _TrackerUploadContext, tracker: str) -> str:
    standard = ctx.api_trackers | ctx.other_api_trackers | ctx.http_trackers
    if tracker in standard:
        return "standard"
    return {"MANUAL": "manual", "PASSTHEPOPCORN": "ptp"}.get(tracker, "none")


async def _run_tracker_route(
    ctx: _TrackerUploadContext,
    meta: Meta,
    tracker: str,
    tracker_class: Any,
) -> None:
    route = _tracker_route(ctx, tracker)
    if route == "standard":
        await _run_standard_tracker_upload(
            ctx,
            meta,
            tracker,
            tracker_class,
            api_tracker=tracker in ctx.api_trackers,
        )
        return
    if route == "manual":
        await _run_manual_upload(ctx, meta)
        return
    if route == "ptp":
        await _run_ptp_upload(ctx, meta, tracker)


async def _process_single_tracker(
    ctx: _TrackerUploadContext, tracker: str
) -> None:
    normalized = _normalized_tracker_name(tracker)
    meta = await _prepared_single_tracker_meta(ctx, normalized)
    tracker_class = _tracker_adapter(ctx, normalized)
    await _apply_zenith_upload_guard(meta, normalized, tracker_class)
    _apply_cjk_upload_guard(meta, normalized)
    _strip_dupe_suffix(meta)
    if _skip_disabled_tracker(ctx, meta, normalized):
        return
    if _skip_missing_book_cover(meta, normalized, tracker_class):
        return
    await _run_tracker_route(ctx, meta, normalized, tracker_class)


def _one_disc(meta: Meta) -> bool:
    discs = meta.discs if isinstance(meta.discs, list) else []
    return len(discs) <= 1


def _run_trackers_concurrently(ctx: _TrackerUploadContext) -> bool:
    multi_screens = int(ctx.default_config.get("multiScreens", 2))
    screen_mode_allows = (
        not ctx.shared_meta.tv_pack and _one_disc(ctx.shared_meta)
    ) or multi_screens == 0
    bandwidth_control = _bandwidth_control_enabled(
        ctx.shared_meta, ctx.default_config
    )
    return screen_mode_allows and not bandwidth_control


def _handle_tracker_task_result(tracker: str, result: object) -> None:
    if isinstance(result, OperationAbortedError):
        raise result
    if not isinstance(result, Exception):
        return
    logger.info(f"[red]{tracker} encountered an error: {result}[/red]")
    logger.debug(
        "".join(
            traceback.format_exception(
                type(result), result, result.__traceback__
            )
        )
    )


async def _run_concurrent_trackers(ctx: _TrackerUploadContext) -> None:
    tasks = [
        (tracker, asyncio.create_task(_process_single_tracker(ctx, tracker)))
        for tracker in ctx.enabled_trackers
    ]
    results = await asyncio.gather(
        *[task for _, task in tasks], return_exceptions=True
    )
    for (tracker, _), result in zip(tasks, results, strict=False):
        _handle_tracker_task_result(tracker, result)


async def _run_sequential_trackers(ctx: _TrackerUploadContext) -> None:
    for tracker in ctx.enabled_trackers:
        await _process_single_tracker(ctx, tracker)


async def _run_all_trackers(ctx: _TrackerUploadContext) -> None:
    if _run_trackers_concurrently(ctx):
        await _run_concurrent_trackers(ctx)
        return
    await _run_sequential_trackers(ctx)


async def process_trackers(
    meta: Meta,
    config: dict[str, Any],
    client: Any,
    api_trackers: Sequence[str],
    tracker_class_map: Mapping[str, Any],
    http_trackers: Sequence[str],
    other_api_trackers: Sequence[str],
    upload_target: str = "tracker",
    argument_parser_factory: ArgumentParserFactory | None = None,
) -> None:
    ctx = _build_upload_context(
        meta,
        config,
        client,
        api_trackers,
        tracker_class_map,
        http_trackers,
        other_api_trackers,
        upload_target,
        argument_parser_factory,
    )
    _apply_smart_image_host(ctx)
    await _run_all_trackers(ctx)
    logger.info(f"[green]All {upload_target} uploads processed.[/green]")


_legacy_process_trackers = process_trackers


def _global_upload_refusal_reason(meta: Meta) -> str | None:
    invalid_group = invalid_release_group_tag(meta)
    if invalid_group is not None:
        return f"Invalid release group {invalid_group!r} matches season/episode syntax"
    if audiobook_cover_missing_or_invalid(meta):
        return "Audiobook cover is missing or invalid"
    return None


async def _validated_process_trackers(
    meta: Meta,
    config: dict[str, Any],
    client: Any,
    api_trackers: Sequence[str],
    tracker_class_map: Mapping[str, Any],
    http_trackers: Sequence[str],
    other_api_trackers: Sequence[str],
    upload_target: str = "tracker",
    argument_parser_factory: ArgumentParserFactory | None = None,
) -> None:
    """Refuse tracker processing when a release-wide safety invariant fails."""
    reason = _global_upload_refusal_reason(meta)
    if reason is None:
        return await _legacy_process_trackers(
            meta,
            config,
            client,
            api_trackers,
            tracker_class_map,
            http_trackers,
            other_api_trackers,
            upload_target,
            argument_parser_factory,
        )
    for tracker in meta.trackers:
        meta.tracker_status.setdefault(str(tracker), {}).update(
            upload=False, skipped=True, status_message=f"Skipped: {reason}"
        )
    logger.error(
        f"[bold red]{reason}. Refusing {upload_target} upload; correct or clear the release group and retry.[/bold red]"
    )
    return None


process_trackers = _validated_process_trackers
