# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import time
import traceback
from collections.abc import Mapping, Sequence
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
    tracker_setup = TrackerSetup(config=config)
    tracker_setup_any = cast(Any, tracker_setup)
    enabled_trackers = list(
        cast(Sequence[str], tracker_setup_any.trackers_enabled(meta))
    )
    default_config_value = config.get("DEFAULT", {})
    default_config = (
        cast(Mapping[str, Any], default_config_value)
        if isinstance(default_config_value, Mapping)
        else {}
    )
    runtime_state_value = config.setdefault("_runtime", {})
    if isinstance(runtime_state_value, dict):
        runtime_state = cast(dict[str, Any], runtime_state_value)
    else:
        runtime_state = {}
        config["_runtime"] = runtime_state
    disabled_trackers_value = runtime_state.setdefault("disabled_trackers", {})
    if isinstance(disabled_trackers_value, dict):
        disabled_trackers = cast(dict[str, str], disabled_trackers_value)
    else:
        disabled_trackers = {}
        runtime_state["disabled_trackers"] = disabled_trackers
    if (
        default_config.get("smart_image_host_selection", True)
        and not meta.imghost_from_cli
    ):
        manual_targets = "MANUAL" in enabled_trackers
        target_trackers = [
            tracker
            for tracker in enabled_trackers
            if tracker != "MANUAL"
            and (
                manual_targets
                or bool(
                    cast(
                        Mapping[str, Any], meta.tracker_status.get(tracker, {})
                    ).get("upload", False)
                )
            )
        ]
        selected_host = select_common_image_host(
            default_config, target_trackers, tracker_class_map
        )
        if selected_host:
            current_host = (
                str(meta.imghost or default_config.get("img_host_1", ""))
                .strip()
                .lower()
            )
            meta.imghost = selected_host
            if selected_host != current_host:
                logger.info(
                    f"[green]Smart image-host selection changed the target host: {current_host or 'unset'} -> {selected_host}[/green]"
                )
        elif has_restricted_image_hosts(target_trackers, tracker_class_map):
            logger.info(
                "[yellow]No shared approved image host found; using per-tracker image-host selection.[/yellow]"
            )
    manual_packager = ManualPackageManager(config)
    tracker_label_width = max(
        (
            len(str(tracker).replace(" ", "").upper().strip())
            for tracker in enabled_trackers
        ),
        default=0,
    )

    def print_tracker_result(
        tracker: str,
        tracker_class: Any,
        status: Mapping[str, Any],
        is_success: bool,
    ) -> None:
        """Print tracker upload result immediately after upload completes."""
        try:
            # Check config settings for what to print
            print_links = meta.print_tracker_links
            print_messages = meta.print_tracker_messages

            # If neither option is enabled, don't print anything
            if not print_links and not print_messages:
                return

            tracker_label = f"{tracker.ljust(tracker_label_width)}:"
            message: str | None = None
            if is_success:
                result_parts = []
                status_message = str(status.get("status_message", ""))
                has_status_message = (
                    bool(status_message) and "data error" not in status_message
                )
                link_url = ""

                if "torrent_id" in status:
                    torrent_url = str(
                        getattr(tracker_class, "torrent_url", "")
                    )
                    link_url = f"{torrent_url}{status['torrent_id']}"

                if (
                    default_config.get("show_upload_duration", True)
                    or meta.upload_timer
                ):
                    duration = meta.get(f"{tracker}_upload_duration")
                    if duration and isinstance(duration, (int, float)):
                        color = (
                            "#21ff00"
                            if duration < 5
                            else "#9fd600"
                            if duration < 10
                            else "#cfaa00"
                            if duration < 15
                            else "#f17100"
                            if duration < 20
                            else "#ff0000"
                        )
                        result_parts.append(
                            f"[[{color}]{duration:.2f}s[/{color}]]"
                        )

                if print_links and link_url:
                    result_parts.append(
                        f"[[green]{format_terminal_link('link', link_url, config['DEFAULT'])}[/green]]"
                    )

                if has_status_message and (
                    print_messages or (print_links and not link_url)
                ):
                    result_parts.append(
                        escape(Redaction.redact_private_info(status_message))
                    )

                message = (
                    f"{tracker_label} {' '.join(result_parts)}"
                    if result_parts
                    else None
                )
            else:
                if "status_message" in status and "data error" in str(
                    status["status_message"]
                ):
                    logger.info(
                        f"[red]{tracker_label} {escape(str(status['status_message']))}[/red]"
                    )
                    return

            if message is not None:
                logger.info(message, extra={"highlighter": None})
        except Exception as e:
            logger.error(f"[red]Error printing {tracker} result: {e}[/red]")

    async def process_single_tracker(
        tracker: str, shared_meta: Meta = meta
    ) -> None:
        tracker = tracker.replace(" ", "").upper().strip()
        if argument_parser_factory is None:
            meta = await prepare_tracker_meta(shared_meta, tracker, config)
        else:
            meta = await prepare_tracker_meta(
                shared_meta, tracker, config, argument_parser_factory
            )

        tracker_class: Any = None
        if tracker not in {"MANUAL", "PASSTHEPOPCORN"}:
            tracker_class = tracker_class_map[tracker](config=config)

        if (
            tracker == "ZENITH"
            and meta.get("zentag_prepared", False)
            and not await tracker_class.get_additional_checks(meta)
        ):
            status = meta.tracker_status.setdefault(tracker, {})
            status.update(
                upload=False,
                skipped=True,
                status_message="Prepared zentag audiobook failed Zenith validation",
            )

        cjk_fields = book_metadata_cjk_fields(meta)
        if cjk_fields and not (
            tracker == "ZENITH" and meta.get("zentag_prepared", False)
        ):
            fields = ", ".join(cjk_fields)
            status = meta.tracker_status.setdefault(tracker, {})
            status.update(
                upload=False,
                skipped=True,
                status_message=f"BOOK metadata contains CJK characters in: {fields}",
            )
            logger.info(
                f"[yellow]{tracker}: BOOK metadata still contains CJK characters in {fields}; skipping upload.[/yellow]"
            )
        if meta.name.endswith("DUPE?"):
            meta.name = meta.name.replace(" DUPE?", "")

        disabled_reason = disabled_trackers.get(tracker)
        if disabled_reason:
            status = meta.tracker_status.setdefault(tracker, {})
            status.update(
                upload=False,
                skipped=True,
                status_message=f"Skipped for the remainder of this run: {disabled_reason}",
            )
            logger.info(
                f"[yellow]{tracker}: skipped for the remainder of this run: {escape(str(disabled_reason))}[/yellow]"
            )
            return

        requires_book_cover = bool(
            getattr(tracker_class, "requires_book_cover", True)
        )
        if (
            meta.category == "BOOK"
            and requires_book_cover
            and not is_valid_cover_image(meta.artwork_path)
        ):
            status = meta.tracker_status.setdefault(tracker, {})
            status["upload"] = False
            status["status_message"] = (
                "Skipped: BOOK uploads require a valid cover image"
            )
            logger.info(
                f"[yellow]{tracker}: skipped because BOOK uploads require a valid cover image.[/yellow]"
            )
            return

        async def check_bandwidth_and_dupes(
            tracker_name: str, t_class: Any
        ) -> bool:
            if t_class and getattr(t_class, "is_usenet", False):
                return True
            qbit_bw_control = (
                meta.qbit_bandwidth_control
                or default_config.get("qbit_bandwidth_control", False)
            )
            if qbit_bw_control:
                logger.info(
                    f"\n[yellow]{tracker_name}: Checking bandwidth...[/yellow]"
                )
                waiter = Wait(config)
                bw_thresh = (
                    meta.qbit_bandwidth_threshold
                    or default_config.get("qbit_bandwidth_threshold", 0)
                )
                bw_time = meta.qbit_bandwidth_time or default_config.get(
                    "qbit_bandwidth_time", 0
                )
                try:
                    bw_thresh = int(bw_thresh)
                    bw_time = int(bw_time)
                except (ValueError, TypeError) as e:
                    logger.info(
                        f"[red]Invalid bandwidth settings: {e}, skipping bandwidth wait.[/red]"
                    )
                    bw_thresh = 0
                    bw_time = 0

                if bw_thresh > 0 and bw_time > 0:
                    waited = await waiter.wait_for_bandwidth(
                        bw_thresh, bw_time
                    )
                    if waited:
                        logger.info(
                            f"[yellow]{tracker_name}: Redoing dupe check after bandwidth wait...[/yellow]"
                        )
                        try:
                            if tracker_name not in {"PASSTHEPOPCORN"}:
                                new_dupes = cast(
                                    list[Any],
                                    await t_class.search_existing(meta),
                                )
                            else:
                                ptp = PassThePopcorn(config=config)
                                group_id = meta.ptp_groupid
                                new_dupes = cast(
                                    list[Any],
                                    await ptp.search_existing(
                                        group_id or "", meta
                                    ),
                                )
                        except Exception as e:
                            logger.info(
                                f"[bold red]{tracker_name}: Error redoing duplicate check after bandwidth wait: {e}[/bold red]"
                            )
                            status = meta.tracker_status.setdefault(
                                tracker_name, {}
                            )
                            status["status_message"] = (
                                f"Skipped: Error redoing dupe check after bandwidth wait: {e}"
                            )
                            return False

                        initial_dupes = meta.initial_dupes.get(
                            tracker_name, []
                        )

                        def is_in_initial(dupe: Any) -> bool:
                            for initial_dupe in initial_dupes:
                                if isinstance(dupe, dict) and isinstance(
                                    initial_dupe, dict
                                ):
                                    if dupe.get("name") == initial_dupe.get(
                                        "name"
                                    ) and dupe.get("size") == initial_dupe.get(
                                        "size"
                                    ):
                                        return True
                                elif (
                                    isinstance(dupe, str)
                                    and isinstance(initial_dupe, str)
                                    and dupe == initial_dupe
                                ):
                                    return True
                            return False

                        real_new_dupes = [
                            d for d in new_dupes if not is_in_initial(d)
                        ]

                        if real_new_dupes:
                            dupe_checker = DupeChecker(config)
                            real_new_dupes = cast(
                                list[Any],
                                await dupe_checker.filter_dupes(
                                    real_new_dupes, meta, tracker_name
                                ),
                            )
                            if real_new_dupes:
                                from src.services.upload_decision_service import (
                                    UploadHelper,
                                )

                                is_dupe, _ = await UploadHelper(
                                    config
                                ).dupe_check(
                                    real_new_dupes, meta, tracker_name
                                )
                                if is_dupe:
                                    logger.info(
                                        f"[red]New dupe found on {tracker_name} during wait! Automatically skipping upload.[/red]"
                                    )
                                    return False
            return True

        if tracker in api_trackers:
            tracker_status = meta.tracker_status
            upload_status = cast(
                Mapping[str, Any], tracker_status.get(tracker, {})
            ).get("upload", False)
            if upload_status:
                modq, draft, tracker_caps = await check_mod_q_and_draft(
                    tracker_class, meta
                )
                if tracker_caps.get("mod_q") and modq == "Yes":
                    logger.info(f"{tracker} (modq: {modq})")
                if (
                    tracker_caps.get("draft") or tracker_caps.get("draft_live")
                ) and draft in ["Yes", "Draft"]:
                    logger.info(f"{tracker} (draft: {draft})")
                is_uploaded = False
                try:
                    if not await check_bandwidth_and_dupes(
                        tracker, tracker_class
                    ):
                        status = meta.tracker_status.setdefault(
                            tracker_class.tracker, {}
                        )
                        status.setdefault(
                            "status_message",
                            "Skipped due to new dupe found after bandwidth wait",
                        )
                        print_tracker_result(
                            tracker, tracker_class, status, False
                        )
                        return
                    if not meta.debug:
                        await check_tracker_image_hosts(meta, tracker_class)
                        screenshot_error = screenshot_requirement_error(
                            meta, config, tracker
                        )
                        if screenshot_error:
                            status = meta.tracker_status.setdefault(
                                tracker, {}
                            )
                            status.update(
                                upload=False,
                                skipped=True,
                                status_message=screenshot_error,
                            )
                            logger.info(
                                f"[yellow]{tracker}: {escape(screenshot_error)} Skipping upload.[/yellow]"
                            )
                            return
                    upload_start_time = time.time()
                    is_uploaded = await tracker_class.upload(meta)
                    upload_duration = time.time() - upload_start_time
                    meta[f"{tracker}_upload_duration"] = upload_duration
                    shared_meta[f"{tracker}_upload_duration"] = upload_duration
                except Exception as e:
                    logger.info(f"[red]Upload failed: {e}")
                    logger.info(traceback.format_exc())
                    return

                if is_uploaded is None:
                    logger.warning(
                        f"[yellow]Warning: {tracker_class.tracker} upload method returned None instead of boolean. Treating as failed upload.[/yellow]"
                    )
                    is_uploaded = False

                status = meta.tracker_status.setdefault(
                    tracker_class.tracker, {}
                )
                if status.get("dupe") is True:
                    status.pop("upload_success", None)
                    logger.info(
                        f"[yellow]{tracker}: release already exists on the tracker. Skipping duplicate upload.[/yellow]"
                    )
                elif is_uploaded and "data error" not in str(
                    status.get("status_message", "")
                ):
                    status["upload_success"] = True
                    if not meta.debug and not getattr(
                        tracker_class, "is_usenet", False
                    ):
                        await client.add_to_client(meta, tracker_class.tracker)
                    print_tracker_result(tracker, tracker_class, status, True)
                else:
                    status["upload_success"] = False
                    print_tracker_result(tracker, tracker_class, status, False)
                    failure_detail = str(
                        status.get("status_message")
                        or "No error details were returned by the tracker."
                    )
                    if "modqueue limit reached" in failure_detail.lower():
                        disabled_trackers[tracker] = failure_detail
                    logger.info(
                        f"[red]{tracker} upload failed or returned data error: {failure_detail}[/red]"
                    )

        elif tracker in other_api_trackers or tracker in http_trackers:
            tracker_status = meta.tracker_status
            upload_status = cast(
                Mapping[str, Any], tracker_status.get(tracker, {})
            ).get("upload", False)
            if upload_status:
                is_uploaded = False
                try:
                    if not await check_bandwidth_and_dupes(
                        tracker, tracker_class
                    ):
                        status = meta.tracker_status.setdefault(
                            tracker_class.tracker, {}
                        )
                        status.setdefault(
                            "status_message",
                            "Skipped due to new dupe found after bandwidth wait",
                        )
                        print_tracker_result(
                            tracker, tracker_class, status, False
                        )
                        return
                    if not meta.debug:
                        await check_tracker_image_hosts(meta, tracker_class)
                        screenshot_error = screenshot_requirement_error(
                            meta, config, tracker
                        )
                        if screenshot_error:
                            status = meta.tracker_status.setdefault(
                                tracker, {}
                            )
                            status.update(
                                upload=False,
                                skipped=True,
                                status_message=screenshot_error,
                            )
                            logger.info(
                                f"[yellow]{tracker}: {escape(screenshot_error)} Skipping upload.[/yellow]"
                            )
                            return
                    upload_start_time = time.time()
                    is_uploaded = await tracker_class.upload(meta)
                    upload_duration = time.time() - upload_start_time
                    meta[f"{tracker}_upload_duration"] = upload_duration
                    shared_meta[f"{tracker}_upload_duration"] = upload_duration
                except Exception as e:
                    logger.info(f"[red]Upload failed: {e}")
                    logger.info(traceback.format_exc())
                    return

                # Detect and handle None return value from upload method
                if is_uploaded is None:
                    logger.warning(
                        f"[yellow]Warning: {tracker_class.tracker} upload method returned None instead of boolean. Treating as failed upload.[/yellow]"
                    )
                    is_uploaded = False

                status = meta.tracker_status.setdefault(
                    tracker_class.tracker, {}
                )
                if status.get("dupe") is True:
                    status.pop("upload_success", None)
                    logger.info(
                        f"[yellow]{tracker}: release already exists on the tracker. Skipping duplicate upload.[/yellow]"
                    )
                elif is_uploaded and "data error" not in str(
                    status.get("status_message", "")
                ):
                    status["upload_success"] = True
                    if not meta.debug and not getattr(
                        tracker_class, "is_usenet", False
                    ):
                        await client.add_to_client(meta, tracker_class.tracker)
                    print_tracker_result(tracker, tracker_class, status, True)
                else:
                    status["upload_success"] = False
                    print_tracker_result(tracker, tracker_class, status, False)
                    failure_detail = str(
                        status.get("status_message")
                        or "No error details were returned by the tracker."
                    )
                    if "modqueue limit reached" in failure_detail.lower():
                        disabled_trackers[tracker] = failure_detail
                    logger.info(
                        f"[red]{tracker} upload failed or returned data error: {failure_detail}[/red]"
                    )

        elif tracker == "MANUAL":
            if meta.unattended:
                do_manual = True
            else:
                try:
                    do_manual = cli_ui.ask_yes_no(
                        "Get files for manual upload?", default=True
                    )
                except EOFError:
                    logger.info(
                        "\n[red]Exiting on user request (Ctrl+C)[/red]"
                    )
                    await cleanup_manager.cleanup()
                    cleanup_manager.reset_terminal()
                    raise OperationAbortedError(
                        "Manual tracker upload confirmation was cancelled by the user."
                    ) from None
            if do_manual:
                for manual_tracker in enabled_trackers:
                    if manual_tracker != "MANUAL":
                        manual_tracker = (
                            manual_tracker.replace(" ", "").upper().strip()
                        )
                        tracker_class = tracker_class_map[manual_tracker](
                            config=config
                        )
                        try:
                            await check_tracker_image_hosts(
                                meta, tracker_class
                            )
                            if manual_tracker in api_trackers:
                                await DescriptionBuilder(
                                    manual_tracker, config
                                ).general_description_generator(
                                    meta,
                                    languages=False,
                                    mediainfo=False,
                                    nfo=False,
                                    signature=manual_tracker,
                                )
                            else:
                                await tracker_class.edit_desc(meta)
                        except Exception as e:
                            logger.info(
                                f"[red]{manual_tracker}: Error preparing manual upload files: {e}[/red]"
                            )
                url = await manual_packager.package(meta)
                if url is False:
                    logger.info(
                        f"[yellow]Unable to upload prep files, they can be found at `tmp/{meta.uuid}"
                    )
                else:
                    logger.info(f"[green]{meta.name}")
                    logger.info(
                        f"[green]Files can be found at: [yellow]{url}[/yellow]"
                    )

        elif tracker == "PASSTHEPOPCORN":
            tracker_status = meta.tracker_status
            upload_status = cast(
                Mapping[str, Any], tracker_status.get(tracker, {})
            ).get("upload", False)
            if upload_status:
                try:
                    ptp = PassThePopcorn(config=config)
                    group_id = meta.ptp_groupid
                    if not meta.debug:
                        await check_tracker_image_hosts(meta, ptp)
                    ptp_url, ptp_data = await ptp.fill_upload_form(
                        group_id, meta
                    )
                    is_uploaded = False
                    try:
                        upload_start_time = time.time()
                        is_uploaded = await ptp.upload(meta, ptp_url, ptp_data)
                        upload_duration = time.time() - upload_start_time
                        meta[f"{tracker}_upload_duration"] = upload_duration
                        shared_meta[f"{tracker}_upload_duration"] = (
                            upload_duration
                        )
                    except Exception as e:
                        logger.info(f"[red]Upload failed: {e}")
                        logger.info(traceback.format_exc())
                        return
                    status = meta.tracker_status.setdefault(ptp.tracker, {})
                    if is_uploaded and "data error" not in str(
                        status.get("status_message", "")
                    ):
                        status["upload_success"] = True
                        if not meta.debug:
                            await client.add_to_client(meta, "PASSTHEPOPCORN")
                        print_tracker_result(tracker, ptp, status, True)
                    else:
                        status["upload_success"] = False
                        print_tracker_result(tracker, ptp, status, False)
                        logger.info(
                            f"[red]{tracker} upload failed or returned data error.[/red]"
                        )
                except Exception:
                    logger.info(traceback.format_exc())
                    return

    multi_screens = int(default_config.get("multiScreens", 2))
    discs = meta.discs or []
    one_disc = True
    if discs and len(discs) == 1:
        one_disc = True
    elif discs and len(discs) > 1:
        one_disc = False

    bandwidth_control = meta.qbit_bandwidth_control or default_config.get(
        "qbit_bandwidth_control", False
    )

    if (
        (not meta.tv_pack and one_disc) or multi_screens == 0
    ) and not bandwidth_control:
        # Run all tracker tasks concurrently with individual error handling
        tasks: list[tuple[str, asyncio.Task[None]]] = []
        for tracker in enabled_trackers:
            task = asyncio.create_task(process_single_tracker(tracker))
            tasks.append((tracker, task))

        # Wait for all tasks to complete, but don't let one tracker's failure stop others
        results = await asyncio.gather(
            *[task for _, task in tasks], return_exceptions=True
        )

        # Log any exceptions that occurred
        for (tracker, _), result in zip(tasks, results, strict=False):
            if isinstance(result, OperationAbortedError):
                raise result
            if isinstance(result, Exception):
                logger.info(
                    f"[red]{tracker} encountered an error: {result}[/red]"
                )
                logger.debug(
                    "".join(
                        traceback.format_exception(
                            type(result), result, result.__traceback__
                        )
                    )
                )
    else:
        # Process each tracker sequentially
        for tracker in enabled_trackers:
            await process_single_tracker(tracker)

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
