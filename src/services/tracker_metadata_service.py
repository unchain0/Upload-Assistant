# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlparse

import cli_ui
import click
import httpx
import requests

from src.domain_models.release import Meta
from src.engines.tracker_description_policy import description_fingerprint
from src.integrations.cache.metadata_cache import (
    is_cache_miss,
    tracker_metadata_cache_for,
)
from src.integrations.external_apis.btn import BtnIdManager
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.trackers.registry import tracker_class_map
from src.services.tracker_metadata_parser import TrackerMetaManager


class TrackerDataManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        trackers_cfg = cast(
            Mapping[str, Mapping[str, Any]], config.get("TRACKERS", {})
        )
        if not isinstance(trackers_cfg, dict):
            raise ValueError("'TRACKERS' config section must be a dict")
        default_cfg = cast(Mapping[str, Any], config.get("DEFAULT", {}))
        if not isinstance(default_cfg, dict):
            raise ValueError("'DEFAULT' config section must be a dict")
        self.trackers_config = trackers_cfg
        self.default_config = default_cfg
        self.tracker_meta_manager = TrackerMetaManager(config)

    def get_tracker_config(self, tracker_name: str) -> Mapping[str, Any]:
        return self.trackers_config.get(tracker_name, MappingProxyType({}))

    @staticmethod
    def _explicit_tracker_id(meta: Meta, tracker_name: str) -> str:
        value = meta.get_tracker_id(tracker_name)
        return "" if value is None else str(value).strip()

    @staticmethod
    def _tracker_cache_key(
        tracker_id: str, meta: Meta, skip_tracker_descriptions: bool
    ) -> str:
        return json.dumps(
            {
                "id": tracker_id,
                "is_disc": bool(meta.is_disc),
                "keep_images": bool(meta.keep_images),
                "skip_descriptions": skip_tracker_descriptions,
            },
            sort_keys=True,
        )

    @staticmethod
    def _metadata_patch(
        before: Mapping[str, Any], after: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {
            key: value
            for key, value in after.items()
            if before.get(key) != value
        }

    @staticmethod
    def _apply_cached_tracker_result(
        meta: Meta,
        cached: Any,
        tracker_name: str,
        tracker_id: str,
    ) -> tuple[Meta, bool] | None:
        if is_cache_miss(cached) or not isinstance(cached, dict):
            return None
        cached_mapping = cast(Mapping[str, Any], cached)
        metadata = cached_mapping.get("metadata")
        if isinstance(metadata, dict):
            meta.update(cast(dict[str, Any], metadata))
        match = bool(cached_mapping.get("match", False))
        logger.debug(
            f"[cyan]{tracker_name}: using cached metadata for torrent ID {tracker_id}.[/cyan]"
        )
        return meta, match

    async def _tracker_cache_result(
        self,
        meta: Meta,
        tracker_name: str,
        tracker_id: str,
        cache_key: str,
        *,
        use_cache: bool,
    ) -> tuple[Meta, bool] | None:
        if not use_cache:
            return None
        cache = tracker_metadata_cache_for(meta.base_dir, self.config)
        cached = await cache.get(tracker_name.lower(), "torrent", cache_key)
        return self._apply_cached_tracker_result(
            meta, cached, tracker_name, tracker_id
        )

    async def _store_tracker_cache_result(
        self,
        meta: Meta,
        tracker_name: str,
        cache_key: str,
        match: bool,
        metadata_patch: dict[str, Any],
        *,
        use_cache: bool,
    ) -> None:
        if not use_cache:
            return
        cache = tracker_metadata_cache_for(meta.base_dir, self.config)
        await cache.set(
            tracker_name.lower(),
            "torrent",
            cache_key,
            {"match": match, "metadata": metadata_patch},
            negative=not match,
        )

    async def _fetch_explicit_tracker_metadata(
        self,
        tracker_name: str,
        tracker_instance: Any,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
        tracker_id: str,
    ) -> tuple[Meta, bool, dict[str, Any]]:
        before = meta.to_dict()
        (
            updated_meta,
            match,
        ) = await self.tracker_meta_manager.update_metadata_from_tracker(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
            torrent_id=tracker_id,
        )
        patch = self._metadata_patch(before, updated_meta.to_dict())
        return updated_meta, match, patch

    async def update_metadata_from_explicit_tracker(
        self,
        tracker_name: str,
        tracker_instance: Any,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
        *,
        use_cache: bool = True,
    ) -> tuple[Meta, bool]:
        """Reuse a cached tracker response only when the user supplied a torrent ID."""
        tracker_id = self._explicit_tracker_id(meta, tracker_name)
        if not tracker_id:
            return (
                await self.tracker_meta_manager.update_metadata_from_tracker(
                    tracker_name,
                    tracker_instance,
                    meta,
                    search_term,
                    search_file_folder,
                    skip_tracker_descriptions,
                )
            )
        cache_key = self._tracker_cache_key(
            tracker_id, meta, skip_tracker_descriptions
        )
        cached_result = await self._tracker_cache_result(
            meta,
            tracker_name,
            tracker_id,
            cache_key,
            use_cache=use_cache,
        )
        if cached_result is not None:
            return cached_result
        (
            updated_meta,
            match,
            patch,
        ) = await self._fetch_explicit_tracker_metadata(
            tracker_name,
            tracker_instance,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
            tracker_id,
        )
        await self._store_tracker_cache_result(
            updated_meta,
            tracker_name,
            cache_key,
            match,
            patch,
            use_cache=use_cache,
        )
        return updated_meta, match

    def _search_enabled(self, tracker_name: str) -> bool:
        tracker_config = self.get_tracker_config(tracker_name)
        use_search = tracker_config.get("use_for_search")
        if use_search is None:
            use_search = tracker_config.get("useAPI", "false")
        return str(use_search).lower() == "true"

    @staticmethod
    def _identifier_changed(
        original: Meta, candidate: Meta, field: str
    ) -> bool:
        value = candidate.get(field)
        if not value:
            return False
        return value != original.get(field)

    @classmethod
    def _identifier_score(cls, original: Meta, candidate: Meta) -> int:
        score = 0
        for field in ("tmdb_id", "imdb_id", "tvdb_id", "mal_id"):
            if cls._identifier_changed(original, candidate, field):
                score += 20
        return score

    @staticmethod
    def _provenance_score(candidate: Meta) -> int:
        provenance = candidate.description_provenance
        if not provenance:
            return 0
        return int(provenance.get("score", 0)) + 10

    @staticmethod
    def _description_score(original: Meta, candidate: Meta) -> int:
        if not candidate.description:
            return 0
        return 10 if candidate.description != original.description else 0

    @classmethod
    def _candidate_score(cls, original: Meta, candidate: Meta) -> int:
        return (
            cls._identifier_score(original, candidate)
            + cls._provenance_score(candidate)
            + cls._description_score(original, candidate)
            + min(len(candidate.image_list), 10)
        )

    @staticmethod
    def _new_tracker_candidate(meta: Meta, tracker_name: str) -> Meta:
        candidate = meta.copy()
        candidate.uuid = (
            f"{meta.uuid}-candidate-{tracker_name.lower()}-{uuid.uuid4().hex}"
        )
        candidate.unattended = True
        candidate.unattended_confirm = False
        candidate.persist_description = False
        return candidate

    @classmethod
    def _candidate_result(
        cls, tracker_name: str, original: Meta, candidate: Meta
    ) -> tuple[str, Meta, int]:
        return (
            tracker_name,
            candidate,
            cls._candidate_score(original, candidate),
        )

    @staticmethod
    def _btn_api_value(default_config: Mapping[str, Any]) -> str | None:
        value = default_config.get("btn_api")
        if not isinstance(value, str):
            return None
        return value if len(value) > 25 else None

    @staticmethod
    def _prefer_identifier(value: Any, fallback: Any) -> Any:
        return value if value else fallback

    async def _collect_btn_candidate(
        self, original: Meta, candidate: Meta
    ) -> tuple[str, Meta, int] | None:
        btn_api = self._btn_api_value(self.default_config)
        if btn_api is None:
            return None
        btn_id = self._explicit_tracker_id(candidate, "BTN")
        imdb, tvdb = await BtnIdManager.get_btn_torrents(btn_api, btn_id)
        if not imdb and not tvdb:
            return None
        candidate.imdb_id = self._prefer_identifier(imdb, candidate.imdb_id)
        candidate.tvdb_id = self._prefer_identifier(tvdb, candidate.tvdb_id)
        return self._candidate_result("BTN", original, candidate)

    async def _collect_anthelion_candidate(
        self, original: Meta, candidate: Meta
    ) -> tuple[str, Meta, int] | None:
        data = await tracker_class_map["ANTHELION"](
            config=self.config
        ).get_data_from_files(candidate)
        if not data:
            return None
        for values in data:
            candidate.update(values)
        return self._candidate_result("ANTHELION", original, candidate)

    async def _collect_generic_candidate(
        self,
        tracker_name: str,
        original: Meta,
        candidate: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> tuple[str, Meta, int] | None:
        factory = tracker_class_map.get(tracker_name)
        if factory is None:
            return None
        candidate, match = await self.update_metadata_from_explicit_tracker(
            tracker_name,
            factory(config=self.config),
            candidate,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
            use_cache=True,
        )
        if not match:
            return None
        return self._candidate_result(tracker_name, original, candidate)

    async def _candidate_by_tracker(
        self,
        tracker_name: str,
        original: Meta,
        candidate: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> tuple[str, Meta, int] | None:
        if tracker_name == "BTN":
            return await self._collect_btn_candidate(original, candidate)
        if tracker_name == "ANTHELION":
            return await self._collect_anthelion_candidate(original, candidate)
        return await self._collect_generic_candidate(
            tracker_name,
            original,
            candidate,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
        )

    @staticmethod
    def _log_candidate_error(tracker_name: str, error: Exception) -> None:
        if isinstance(
            error, (httpx.ConnectError, requests.exceptions.ConnectionError)
        ):
            logger.info(
                f"{tracker_name} tracker request failed due to connection error: {error}",
                extra={"markup": False},
            )
            return
        logger.info(
            f"{tracker_name} tracker metadata candidate failed: {error}",
            extra={"markup": False},
        )

    async def _collect_explicit_tracker_candidate(
        self,
        tracker_name: str,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> tuple[str, Meta, int] | None:
        """Fetch one candidate without allowing it to mutate the live release."""
        candidate = self._new_tracker_candidate(meta, tracker_name)
        candidate_dir = Path(meta.base_dir) / "tmp" / candidate.uuid
        await asyncio.to_thread(
            candidate_dir.mkdir, mode=0o700, parents=True, exist_ok=True
        )
        try:
            return await self._candidate_by_tracker(
                tracker_name,
                meta,
                candidate,
                search_term,
                search_file_folder,
                skip_tracker_descriptions,
            )
        except Exception as error:
            self._log_candidate_error(tracker_name, error)
            return None
        finally:
            await asyncio.to_thread(shutil.rmtree, candidate_dir, True)

    @staticmethod
    def _ranked_candidates(
        candidates: list[tuple[str, Meta, int]],
    ) -> list[tuple[str, Meta, int]]:
        return sorted(candidates, key=lambda item: (-item[2], item[0]))

    @staticmethod
    def _should_prompt_candidate(
        meta: Meta, ranked: list[tuple[str, Meta, int]]
    ) -> bool:
        return len(ranked) > 1 and not meta.unattended

    @staticmethod
    def _log_candidate_choices(ranked: list[tuple[str, Meta, int]]) -> None:
        logger.info("[cyan]Tracker metadata candidates:[/cyan]")
        for index, (tracker_name, candidate, score) in enumerate(
            ranked, start=1
        ):
            display_name = (
                candidate.name if candidate.name else candidate.filename
            )
            logger.info(
                f"  {index}. {tracker_name}: score {score}, {display_name}"
            )

    @staticmethod
    def _candidate_choice(
        choice: Any, ranked: list[tuple[str, Meta, int]]
    ) -> tuple[str, Meta] | None:
        text = "" if choice is None else str(choice).strip()
        if not text:
            return None
        if not text.isdigit():
            logger.warning(
                "[yellow]Invalid candidate selection; using the best score.[/yellow]"
            )
            return None
        selected = int(text) - 1
        if not 0 <= selected < len(ranked):
            return None
        return ranked[selected][0], ranked[selected][1]

    async def _choose_explicit_tracker_candidate(
        self,
        meta: Meta,
        candidates: list[tuple[str, Meta, int]],
    ) -> tuple[str, Meta] | None:
        if not candidates:
            return None
        ranked = self._ranked_candidates(candidates)
        if not self._should_prompt_candidate(meta, ranked):
            return ranked[0][0], ranked[0][1]
        self._log_candidate_choices(ranked)
        choice = await prompt_in_thread(
            cli_ui.ask_string,
            f"Choose a tracker candidate [1-{len(ranked)}] (Enter for best): ",
        )
        selected = self._candidate_choice(choice, ranked)
        if selected is not None:
            return selected
        return ranked[0][0], ranked[0][1]

    async def _apply_explicit_tracker_candidate(
        self, meta: Meta, tracker_name: str, candidate: Meta
    ) -> None:
        """Apply the selected isolated result without leaking worker-only state."""
        excluded = {
            "uuid",
            "unattended",
            "unattended_confirm",
            "base_dir",
            "persist_description",
        }
        for key, value in candidate.to_dict().items():
            if key not in excluded and meta.get(key) != value:
                meta[key] = value
        meta.matched_tracker = tracker_name

    @staticmethod
    def _can_review_candidate_description(meta: Meta, candidate: Meta) -> bool:
        if meta.unattended:
            return False
        return bool(candidate.description)

    @staticmethod
    async def _edit_candidate_description(
        candidate: Meta, tracker_name: str
    ) -> None:
        edited = await asyncio.to_thread(click.edit, candidate.description)
        if edited is None:
            return
        candidate.description = str(edited).strip()
        candidate.saved_description = bool(candidate.description)
        candidate.description_fingerprint = description_fingerprint(
            candidate, tracker_name
        )
        candidate.description_provenance = {
            **candidate.description_provenance,
            "edited": True,
        }

    @staticmethod
    def _discard_candidate_description(candidate: Meta) -> None:
        candidate.description = ""
        candidate.saved_description = False
        candidate.description_provenance = {
            **candidate.description_provenance,
            "discarded": True,
        }

    @classmethod
    async def _apply_description_review_choice(
        cls, choice: str, candidate: Meta, tracker_name: str
    ) -> None:
        if choice == "e":
            await cls._edit_candidate_description(candidate, tracker_name)
            return
        if choice == "d":
            cls._discard_candidate_description(candidate)

    async def _review_explicit_tracker_description(
        self, meta: Meta, tracker_name: str, candidate: Meta
    ) -> None:
        """Allow an interactive run to edit the selected tracker description."""
        if not self._can_review_candidate_description(meta, candidate):
            return
        logger.info(
            f"[cyan]Selected description from {tracker_name}:[/cyan]\n{candidate.description[:1000]}",
            extra={"markup": False},
        )
        choice = await prompt_in_thread(
            cli_ui.ask_string,
            "\nEnter 'e' to edit, 'd' to discard the description, or press Enter to keep it: ",
        )
        normalized = "" if choice is None else str(choice).strip().lower()
        await self._apply_description_review_choice(
            normalized, candidate, tracker_name
        )

    async def get_tracker_timestamps(
        self, base_dir: str | None = None
    ) -> dict[str, float]:
        """Get tracker timestamps from the log file"""
        timestamp_file = (
            Path(f"{base_dir}") / "data" / "banned" / "tracker_timestamps.json"
        )
        try:
            if Path(timestamp_file).exists():
                timestamps_text = await asyncio.to_thread(
                    Path(timestamp_file).read_text
                )
                return cast(dict[str, float], json.loads(timestamps_text))
            return {}
        except Exception as e:
            logger.warning(
                f"[yellow]Warning: Could not load tracker timestamps: {e}[/yellow]"
            )
            return {}

    async def save_tracker_timestamp(
        self, tracker_name: str, base_dir: str | None = None
    ) -> None:
        """Save timestamp for when tracker was processed"""
        timestamp_file = (
            Path(f"{base_dir}") / "data" / "banned" / "tracker_timestamps.json"
        )
        try:
            Path(f"{base_dir}/data/banned").mkdir(parents=True, exist_ok=True)

            timestamps = await self.get_tracker_timestamps(base_dir)
            timestamps[tracker_name] = time.time()

            timestamps_text = json.dumps(timestamps, indent=2)
            await asyncio.to_thread(
                Path(timestamp_file).write_text, timestamps_text
            )

            logger.debug(
                f"[yellow]Saved timestamp for {tracker_name} - will be available again in 60 seconds[/yellow]"
            )

        except Exception as e:
            logger.error(f"[red]Error saving tracker timestamp: {e}[/red]")

    async def get_available_trackers(
        self,
        specific_trackers: list[str],
        base_dir: str | None = None,
        debug: bool = False,
    ) -> tuple[list[str], list[tuple[str, float]]]:
        """Get trackers that are available (60+ seconds since last processed)"""
        _ = debug
        timestamps = await self.get_tracker_timestamps(base_dir)
        current_time = time.time()
        available: list[str] = []
        waiting: list[tuple[str, float]] = []

        for tracker in specific_trackers:
            cooldown_seconds = 60 if tracker == "PASSTHEPOPCORN" else 15
            last_processed = timestamps.get(tracker, 0)
            time_since_last = current_time - last_processed

            if time_since_last >= cooldown_seconds:
                available.append(tracker)
            else:
                wait_time = cooldown_seconds - time_since_last
                waiting.append((tracker, wait_time))

        return available, waiting

    def _enabled_specific_trackers(self, meta: Meta) -> list[str]:
        enabled: list[str] = []
        for tracker in sorted(meta.tracker_ids):
            if self._search_enabled(tracker):
                enabled.append(tracker)
                continue
            logger.debug(
                f"[yellow]Tracker {tracker} is not enabled for metadata search, skipping[/yellow]"
            )
        return enabled

    @staticmethod
    def _apply_specific_tracker_constraints(
        meta: Meta, trackers: list[str]
    ) -> None:
        if meta.is_disc and "ANTHELION" in trackers:
            trackers.remove("ANTHELION")
        if meta.category == "MOVIE" and "BTN" in trackers:
            trackers.remove("BTN")

    @staticmethod
    def _normalized_meta_trackers(meta: Meta) -> list[str]:
        raw = meta.trackers
        if isinstance(raw, str):
            return [item.strip().upper() for item in raw.split(",")]
        if isinstance(raw, list):
            return [str(item).upper() for item in cast(list[Any], raw)]
        return []

    @staticmethod
    def _remove_site_check_trackers(
        meta: Meta, specific_trackers: list[str], meta_trackers: list[str]
    ) -> None:
        if not meta.site_check:
            return
        for tracker in list(specific_trackers):
            if tracker not in meta_trackers:
                continue
            specific_trackers.remove(tracker)
            meta_trackers.remove(tracker)

    @classmethod
    def _prepare_specific_trackers(
        cls, meta: Meta, trackers: list[str]
    ) -> list[str]:
        cls._apply_specific_tracker_constraints(meta, trackers)
        meta_trackers = cls._normalized_meta_trackers(meta)
        cls._remove_site_check_trackers(meta, trackers, meta_trackers)
        meta.trackers = meta_trackers
        return trackers

    @staticmethod
    def _cooldown_wait_time(waiting: list[tuple[str, float]]) -> float:
        return max(wait for _tracker, wait in waiting)

    @staticmethod
    def _cooldown_waiting_names(waiting: list[tuple[str, float]]) -> str:
        return ", ".join(
            f"{tracker} ({wait:.1f}s)" for tracker, wait in waiting
        )

    async def _available_specific_trackers(
        self, trackers: list[str], meta: Meta
    ) -> list[str]:
        available, waiting = await self.get_available_trackers(
            trackers, meta.base_dir, debug=meta.debug
        )
        if available or not waiting:
            return available
        logger.info(
            "[yellow]Waiting for tracker metadata candidate cooldowns: "
            f"{self._cooldown_waiting_names(waiting)}[/yellow]"
        )
        await asyncio.sleep(self._cooldown_wait_time(waiting))
        available, waiting = await self.get_available_trackers(
            trackers, meta.base_dir, debug=meta.debug
        )
        if waiting:
            logger.warning(
                "[yellow]Some tracker metadata candidates remain in cooldown and will not be queried.[/yellow]"
            )
        return available

    def _tracker_search_semaphore(self) -> asyncio.Semaphore:
        value = self.default_config.get("tracker_search_concurrency", 4)
        try:
            return asyncio.Semaphore(max(1, int(value)))
        except TypeError, ValueError:
            return asyncio.Semaphore(4)

    async def _collect_candidate_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        tracker_name: str,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> tuple[str, Meta, int] | None:
        async with semaphore:
            return await self._collect_explicit_tracker_candidate(
                tracker_name,
                meta,
                search_term,
                search_file_folder,
                skip_tracker_descriptions,
            )

    async def _collect_available_candidates(
        self,
        available_trackers: list[str],
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> list[tuple[str, Meta, int]]:
        semaphore = self._tracker_search_semaphore()
        tasks = [
            self._collect_candidate_with_semaphore(
                semaphore,
                tracker_name,
                meta,
                search_term,
                search_file_folder,
                skip_tracker_descriptions,
            )
            for tracker_name in available_trackers
        ]
        results = await asyncio.gather(*tasks)
        for tracker_name in available_trackers:
            await self.save_tracker_timestamp(
                tracker_name, base_dir=meta.base_dir
            )
        return [result for result in results if result is not None]

    async def _apply_selected_tracker_candidate(
        self, meta: Meta, candidates: list[tuple[str, Meta, int]]
    ) -> bool:
        selected = await self._choose_explicit_tracker_candidate(
            meta, candidates
        )
        if selected is None:
            return False
        tracker_name, candidate = selected
        await self._review_explicit_tracker_description(
            meta, tracker_name, candidate
        )
        await self._apply_explicit_tracker_candidate(
            meta, tracker_name, candidate
        )
        logger.debug(
            f"[green]Selected tracker metadata candidate: {tracker_name}[/green]"
        )
        return True

    @staticmethod
    def _log_specific_search_result(meta: Meta, found_match: bool) -> None:
        if found_match:
            tracker = (
                meta.matched_tracker
                if meta.matched_tracker is not None
                else "Unknown"
            )
            logger.debug(
                f"[green]Successfully found match using tracker: {tracker}[/green]"
            )
            return
        logger.debug(
            "[yellow]No matches found on any available specific trackers.[/yellow]"
        )

    async def _search_specific_tracker_data(
        self,
        meta: Meta,
        trackers: list[str],
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> Meta:
        trackers = self._prepare_specific_trackers(meta, trackers)
        available = await self._available_specific_trackers(trackers, meta)
        candidates = await self._collect_available_candidates(
            available,
            meta,
            search_term,
            search_file_folder,
            skip_tracker_descriptions,
        )
        found_match = await self._apply_selected_tracker_candidate(
            meta, candidates
        )
        self._log_specific_search_result(meta, found_match)
        return meta

    @staticmethod
    def _filename_tracker_order(meta: Meta, cat: str | None) -> list[str]:
        from src.integrations.trackers.registry import api_trackers

        other_api = sorted(api_trackers - {"BEYONDHD"})
        order = ["PASSTHEPOPCORN", "HDBITS", "BEYONDHD", *other_api]
        if cat != "TV" and meta.category != "TV":
            return order
        logger.debug(
            "[yellow]Detected TV content, skipping PASSTHEPOPCORN tracker check"
        )
        return [tracker for tracker in order if tracker != "PASSTHEPOPCORN"]

    async def _process_filename_tracker(
        self,
        tracker_name: str,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        skip_tracker_descriptions: bool,
    ) -> tuple[Meta, bool]:
        factory = tracker_class_map.get(tracker_name)
        if factory is None:
            logger.info(
                f"[red]Tracker class for {tracker_name} not found.[/red]"
            )
            return meta, False
        try:
            (
                updated_meta,
                match,
            ) = await self.update_metadata_from_explicit_tracker(
                tracker_name,
                factory(config=self.config),
                meta,
                search_term,
                search_file_folder,
                skip_tracker_descriptions,
            )
        except httpx.ConnectError:
            logger.info(
                f"{tracker_name} tracker request failed due to SSL/Connection error.",
                extra={"markup": False},
            )
            return meta, False
        except requests.exceptions.ConnectionError as error:
            logger.info(
                f"{tracker_name} tracker request failed due to connection error: {error}",
                extra={"markup": False},
            )
            return meta, False
        if match:
            logger.debug(
                f"[green]Match found on tracker: {tracker_name}[/green]"
            )
            meta.matched_tracker = tracker_name
        return updated_meta, match

    @staticmethod
    def _mark_filename_search_result(meta: Meta, found_match: bool) -> None:
        if found_match:
            return
        meta.no_tracker_match = True
        logger.debug("[yellow]No matches found on any trackers.[/yellow]")

    async def _search_filename_trackers(
        self,
        meta: Meta,
        search_term: str,
        search_file_folder: str,
        cat: str | None,
        skip_tracker_descriptions: bool,
    ) -> Meta:
        found_match = False
        for tracker_name in self._filename_tracker_order(meta, cat):
            if found_match:
                break
            if not self._search_enabled(tracker_name):
                continue
            meta, found_match = await self._process_filename_tracker(
                tracker_name,
                meta,
                search_term,
                search_file_folder,
                skip_tracker_descriptions,
            )
        self._mark_filename_search_result(meta, found_match)
        return meta

    def _tracker_comment_only(self) -> bool:
        return bool(self.default_config.get("tracker_comment_only", True))

    async def get_tracker_data(
        self,
        _video: Any,
        meta: Meta,
        search_term: str | None = None,
        search_file_folder: str | None = None,
        cat: str | None = None,
        skip_tracker_descriptions: bool = False,
    ) -> Meta:
        if not search_term:
            logger.warning(
                "[yellow]Warning: No valid search term available, skipping tracker updates.[/yellow]"
            )
            return meta
        search_file_folder_value = search_file_folder or ""
        specific_trackers = self._enabled_specific_trackers(meta)
        logger.debug(
            f"[blue]Specific trackers to check: {specific_trackers}[/blue]"
        )
        if specific_trackers:
            return await self._search_specific_tracker_data(
                meta,
                specific_trackers,
                search_term,
                search_file_folder_value,
                skip_tracker_descriptions,
            )
        if self._tracker_comment_only():
            logger.debug(
                "[cyan]Skipping filename-based tracker metadata searches because DEFAULT.tracker_comment_only is enabled.[/cyan]"
            )
            return meta
        return await self._search_filename_trackers(
            meta,
            search_term,
            search_file_folder_value,
            cat,
            skip_tracker_descriptions,
        )

    @staticmethod
    def _unit3d_tracker_order(api_trackers: set[str]) -> list[str]:
        prioritized = ["BLUTOPIA", "AITHER", "ULCX", "LST", "ONLYENCODES"]
        return prioritized + sorted(
            api_trackers - set(prioritized) - {"BEYONDHD"}
        )

    @staticmethod
    def _hostname_from_url(url: Any) -> str:
        if not isinstance(url, str):
            return ""
        if not url:
            return ""
        hostname = urlparse(url).hostname
        return hostname.lower() if hostname else ""

    def _tracker_base_url(self, tracker_name: str) -> Any:
        factory = tracker_class_map.get(tracker_name)
        if factory is None:
            return ""
        try:
            tracker_instance = factory(self.config)
        except Exception:
            return ""
        return getattr(tracker_instance, "base_url", "")

    def _tracker_base_hostname(self, tracker_name: str) -> str:
        return self._hostname_from_url(self._tracker_base_url(tracker_name))

    def _tracker_announce_hostname(self, tracker_name: str) -> str:
        tracker_config = self.get_tracker_config(tracker_name)
        return self._hostname_from_url(tracker_config.get("announce_url", ""))

    def _tracker_hostname(self, tracker_name: str) -> str:
        hostname = self._tracker_base_hostname(tracker_name)
        if hostname:
            return hostname
        return self._tracker_announce_hostname(tracker_name)

    def _unit3d_tracker_hosts(self, api_trackers: set[str]) -> dict[str, str]:
        hosts: dict[str, str] = {}
        for tracker_name in api_trackers:
            hostname = self._tracker_hostname(tracker_name)
            if hostname:
                hosts[tracker_name] = hostname
        for tracker_name, hostname in {
            "BLUTOPIA": "blutopia.cc",
            "AITHER": "aither.cc",
            "LST": "lst.gg",
            "ONLYENCODES": "onlyencodes.cc",
            "ULCX": "upload.cx",
        }.items():
            hosts.setdefault(tracker_name, hostname)
        return hosts

    @staticmethod
    def _candidate_url_matches_host(
        candidate_url: str, expected_host: str
    ) -> bool:
        parsed = urlparse(candidate_url)
        if parsed.scheme not in ("http", "https"):
            return False
        return parsed.hostname == expected_host

    @classmethod
    def _comment_has_tracker_url(
        cls, comment: str, expected_host: str
    ) -> bool:
        if not expected_host:
            return False
        if expected_host not in comment:
            return False
        return any(
            cls._candidate_url_matches_host(candidate_url, expected_host)
            for candidate_url in re.findall(r"https?://[^\s\"'<>]+", comment)
        )

    @staticmethod
    def _trailing_tracker_id(comment: str) -> str | None:
        match = re.search(r"/(\d+)$", comment)
        return match.group(1) if match else None

    def _tracker_id_from_comment(
        self,
        comment_data: Mapping[str, Any],
        tracker_name: str,
        api_trackers: set[str],
    ) -> str | None:
        comment = str(comment_data.get("comment", ""))
        expected_host = self._unit3d_tracker_hosts(api_trackers).get(
            tracker_name, ""
        )
        if not self._comment_has_tracker_url(comment, expected_host):
            return None
        return self._trailing_tracker_id(comment)

    def _tracker_id_from_comments(
        self, meta: Meta, tracker_name: str, api_trackers: set[str]
    ) -> str | None:
        comments = (
            meta.torrent_comments
            if isinstance(meta.torrent_comments, list)
            else []
        )
        for raw_comment in comments:
            if not isinstance(raw_comment, Mapping):
                continue
            tracker_id = self._tracker_id_from_comment(
                cast(Mapping[str, Any], raw_comment),
                tracker_name,
                api_trackers,
            )
            if tracker_id is not None:
                meta[tracker_name.lower()] = tracker_id
                return tracker_id
        return None

    @staticmethod
    def _missing_region_distributor(meta: Meta) -> list[str]:
        missing: list[str] = []
        if not meta.region:
            missing.append("region")
        if not meta.distributor:
            missing.append("distributor")
        return missing

    @staticmethod
    def _region_distributor_patch(
        meta: Meta, previous_region: Any, previous_distributor: Any
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if meta.region != previous_region:
            patch["region"] = meta.region
        if meta.distributor != previous_distributor:
            patch["distributor"] = meta.distributor
        return patch

    @staticmethod
    def _apply_cached_region_distributor(
        meta: Meta,
        cached: Any,
        tracker_name: str,
        tracker_id: str,
    ) -> bool:
        if is_cache_miss(cached) or not isinstance(cached, dict):
            return False
        cached_mapping = cast(Mapping[str, Any], cached)
        metadata = cached_mapping.get("metadata")
        if isinstance(metadata, dict):
            meta.update(cast(dict[str, Any], metadata))
        logger.debug(
            f"[cyan]{tracker_name}: using cached region/distributor data for torrent ID {tracker_id}.[/cyan]"
        )
        return True

    async def _load_region_distributor(
        self,
        common: Any,
        meta: Meta,
        tracker_name: str,
        tracker_id: str,
    ) -> tuple[Any, Any]:
        tracker_instance = tracker_class_map[tracker_name](config=self.config)
        previous_region = meta.region
        previous_distributor = meta.distributor
        cache = tracker_metadata_cache_for(meta.base_dir, self.config)
        cache_key = json.dumps({"id": tracker_id}, sort_keys=True)
        cached = await cache.get(
            tracker_name.lower(), "region_distributor", cache_key
        )
        if self._apply_cached_region_distributor(
            meta, cached, tracker_name, tracker_id
        ):
            return previous_region, previous_distributor
        await common.unit3d_region_distributor(
            meta,
            tracker_name,
            tracker_instance.torrent_url,
            tracker_id,
        )
        patch = self._region_distributor_patch(
            meta, previous_region, previous_distributor
        )
        await cache.set(
            tracker_name.lower(),
            "region_distributor",
            cache_key,
            {"metadata": patch},
            negative=not bool(patch),
        )
        return previous_region, previous_distributor

    @staticmethod
    def _should_log_new_value(value: Any, previous: Any, debug: bool) -> bool:
        if not debug:
            return False
        if not value:
            return False
        return not bool(previous)

    @classmethod
    def _log_new_region(
        cls, meta: Meta, tracker_name: str, previous_region: Any
    ) -> None:
        if not cls._should_log_new_value(
            meta.region, previous_region, meta.debug
        ):
            return
        logger.info(
            f"[green]Found region '{meta.region}' from {tracker_name}[/green]"
        )

    @classmethod
    def _log_new_distributor(
        cls, meta: Meta, tracker_name: str, previous_distributor: Any
    ) -> None:
        if not cls._should_log_new_value(
            meta.distributor, previous_distributor, meta.debug
        ):
            return
        logger.info(
            f"[green]Found distributor '{meta.distributor}' from {tracker_name}[/green]"
        )

    @classmethod
    def _log_new_region_distributor(
        cls,
        meta: Meta,
        tracker_name: str,
        previous_region: Any,
        previous_distributor: Any,
    ) -> None:
        cls._log_new_region(meta, tracker_name, previous_region)
        cls._log_new_distributor(meta, tracker_name, previous_distributor)

    async def _process_unit3d_tracker_id(
        self,
        common: Any,
        meta: Meta,
        tracker_name: str,
        tracker_id: str,
    ) -> None:
        missing_info = self._missing_region_distributor(meta)
        logger.debug(
            f"[cyan]Using {tracker_name} ID {tracker_id} to get {'/'.join(missing_info)} info[/cyan]"
        )
        (
            previous_region,
            previous_distributor,
        ) = await self._load_region_distributor(
            common, meta, tracker_name, tracker_id
        )
        self._log_new_region_distributor(
            meta, tracker_name, previous_region, previous_distributor
        )

    @staticmethod
    def _unit3d_metadata_complete(meta: Meta) -> bool:
        return bool(meta.region and meta.distributor)

    async def ping_unit3d(self, meta: Meta) -> None:
        from src.integrations.trackers.common import Common
        from src.integrations.trackers.registry import api_trackers

        if not meta.torrent_comments:
            return
        common = Common(self.config)
        for tracker_name in self._unit3d_tracker_order(set(api_trackers)):
            if self._unit3d_metadata_complete(meta):
                logger.debug(
                    f"[green]Both region ({meta.region}) and distributor ({meta.distributor}) found - no need to check more trackers[/green]"
                )
                break
            tracker_id = self._tracker_id_from_comments(
                meta, tracker_name, set(api_trackers)
            )
            if tracker_id is None:
                continue
            await self._process_unit3d_tracker_id(
                common, meta, tracker_name, tracker_id
            )
