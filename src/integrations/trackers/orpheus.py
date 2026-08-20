from __future__ import annotations

import json
import platform
import re
from pathlib import Path
from typing import Any, ClassVar, cast
from urllib.parse import urlparse

import httpx
from rich.markup import escape

from src.domain_models.music import MusicRelease
from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.music_validation import OrpheusMusicValidator, ValidationLevel


class Orpheus:
    """Orpheus is a Private Torrent Tracker for MUSIC"""

    tracker = "ORPHEUS"
    display_name = "Orpheus"
    auth_type = "other_api"
    supported_categories = ("MUSIC",)
    source_flag = "OPS"
    base_url = "https://orpheus.network"
    release_types: ClassVar[dict[str, int]] = {
        "Album": 1,
        "Soundtrack": 3,
        "EP": 5,
        "Anthology": 6,
        "Compilation": 7,
        "Sampler": 8,
        "Single": 9,
        "Demo": 10,
        "Live album": 11,
        "Split": 12,
        "Remix": 13,
        "Bootleg": 14,
        "Interview": 15,
        "Mixtape": 16,
        "DJ Mix": 17,
        "Concert recording": 18,
        "Concert Recording": 18,
        "Unknown": 21,
    }
    banned_groups = ()
    blocked_music_artists: ClassVar[dict[str, str]] = {
        "vap0rwave": "Vap0rwave",
        "pauldvr": "Paul_DVR",
        "firmensprecher": "Firmensprecher",
        "stretches": "stretches",
        "phyllomedusa": "Phyllomedusa",
    }
    blocked_music_releases: ClassVar[tuple[tuple[str, str], ...]] = (
        ("Bruce Springsteen", "Odds and Sods"),
        ("Dr. Dre", "Detox"),
        ("Green Day", "Cigarettes and Valentines"),
        ("Jean-Michel Jarre", "Music for Supermarkets"),
        ("Michael Jackson", "Super Mix"),
        ("Pink Floyd", "Tree Full of Secrets"),
        ("The Beatles", "Carnival of Light"),
        ("The Upholsterers", "Your Furniture Was Always Dead… I Was Just Afraid To Tell You"),
        ("Various Artists", "The Ultimate 500 CD Jazz Collection"),
        ("Wu-Tang Clan", "Once Upon a Time in Shaolin"),
    )
    blocked_music_labels: ClassVar[tuple[str, ...]] = (
        "Sandero Classic Sound",
        "Sip It & Trip It Records",
    )
    comment_hosts = ("orpheus.network", "home.opsfet.ch")
    tracker_urls = ("home.opsfet.ch",)

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        settings = config.get("TRACKERS", {}).get(self.tracker, {})
        self.api_key = str(settings.get("api_key", "")).strip()
        self.announce_url = str(settings.get("announce_url", "")).strip()
        self.requests_url = f"{self.base_url}/requests.php"
        self.torrent_url = f"{self.base_url}/torrents.php?torrentid="
        self.common = Common(config)

    def _headers(self, meta: Meta) -> dict[str, str]:
        product = str(meta.ua_name or "Upload Assistant").strip() or "Upload Assistant"
        version = str(meta.current_version or "").strip()
        user_agent = f"{product}{f' {version}' if version else ''} ({platform.system()} {platform.release()})"
        return {"Authorization": f"token {self.api_key}", "User-Agent": user_agent}

    @staticmethod
    def _release(meta: Meta) -> MusicRelease:
        if not isinstance(meta.music_release, dict):
            raise ValueError("MUSIC analysis is missing; run preparation before using Orpheus.")
        return MusicRelease.from_dict(meta.music_release)

    @staticmethod
    def _normalise_artist_name(value: Any) -> str:
        """Compare artist names independently of case, spaces and underscores."""
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @classmethod
    def _blocked_artists(cls, release: MusicRelease) -> list[str]:
        candidates = cls._artist_credit_candidates(cls._artists(release))
        return cls._matched_blocked_artists(candidates)

    @staticmethod
    def _artist_credit_candidates(values: list[str]) -> list[str]:
        candidates: list[str] = []
        for value in values:
            parts = re.split(r"\s*(?:,|&|;|\bfeat(?:uring)?\.?\b|\bwith\b)\s*", value, flags=re.I)
            candidates.extend(part.strip() for part in parts if part.strip())
        return candidates

    @classmethod
    def _matched_blocked_artists(cls, candidates: list[str]) -> list[str]:
        blocked: list[str] = []
        for candidate in candidates:
            name = cls.blocked_music_artists.get(cls._normalise_artist_name(candidate))
            if name and name not in blocked:
                blocked.append(name)
        return blocked

    @classmethod
    def _blocked_releases(cls, release: MusicRelease) -> list[str]:
        artists = {cls._normalise_artist_name(value) for value in cls._artists(release)}
        title = cls._normalise_artist_name(release.get("album", ""))
        matches: list[str] = []
        for artist, album in cls.blocked_music_releases:
            if cls._normalise_artist_name(artist) in artists and cls._normalise_artist_name(album) == title:
                matches.append(f"{artist} - {album}")
        return matches

    @classmethod
    def _blocked_labels(cls, release: MusicRelease) -> list[str]:
        labels = cls._release_label_keys(release)
        return [label for label in cls.blocked_music_labels if cls._normalise_artist_name(label) in labels]

    @classmethod
    def _release_label_keys(cls, release: MusicRelease) -> set[str]:
        values = (release.get("release_label", ""), release.get("label", ""))
        return {cls._normalise_artist_name(value) for value in values if str(value or "").strip()}

    async def get_additional_checks(self, meta: Meta) -> bool:
        release = self._release(meta)
        reasons = self._blacklist_reasons(release)
        if not reasons:
            return True
        self._record_blacklist_failure(meta, reasons)
        return False

    @classmethod
    def _blacklist_reasons(cls, release: MusicRelease) -> list[str]:
        return [
            *(f"artist {artist}" for artist in cls._blocked_artists(release)),
            *(f"blacklisted release {name}" for name in cls._blocked_releases(release)),
            *(f"blacklisted label {label}" for label in cls._blocked_labels(release)),
        ]

    def _record_blacklist_failure(self, meta: Meta, reasons: list[str]) -> None:
        message = f"Upload blocked: Orpheus blacklist matched {', '.join(reasons)}."
        status = meta.tracker_status.setdefault(self.tracker, {})
        status["status_message"] = message
        status["blocked_reasons"] = reasons
        logger.error(f"{self.tracker}: [red]{message}[/red]")

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        release = self._release(meta)
        artist = str(release.get("artist", ""))
        album = str(release.get("album", ""))
        if not artist or not album or not self.api_key:
            return []
        payload = await self._browse_payload(meta, artist, album)
        return self._browse_results(payload)

    async def _browse_payload(self, meta: Meta, artist: str, album: str) -> dict[str, Any] | None:
        params = {"action": "browse", "artistname": artist, "groupname": album}
        try:
            return await self._gazelle_json(meta, params, request_timeout=8.0)
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(f"{self.tracker}: [yellow]read-only duplicate search failed: {error}[/yellow]")
            return None

    async def _gazelle_json(self, meta: Meta, params: dict[str, str], *, request_timeout: float) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(request_timeout), headers=self._headers(meta)) as client:
            response = await client.get(f"{self.base_url}/ajax.php", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Orpheus API response must be a JSON object")
        return cast(dict[str, Any], payload)

    @classmethod
    def _browse_results(cls, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        groups = cls._response_results(payload)
        return [result for group in groups if (result := cls._browse_result(group)) is not None]

    @classmethod
    def _response_results(cls, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        response = cls._successful_response_mapping(payload)
        return cls._mapping_list(response.get("results", []))

    @staticmethod
    def _successful_response_mapping(payload: dict[str, Any] | None) -> dict[str, Any]:
        if not payload or payload.get("status") != "success":
            return {}
        response = payload.get("response")
        return cast(dict[str, Any], response) if isinstance(response, dict) else {}

    @staticmethod
    def _mapping_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]

    @classmethod
    def _browse_result(cls, group: dict[str, Any]) -> dict[str, Any] | None:
        editions = group.get("torrents")
        if not isinstance(editions, list):
            return None
        group_id = group.get("groupId")
        return {
            "name": f"{group.get('artist', '')} - {group.get('groupName', '')}".strip(" -"),
            "size": group.get("maxSize"),
            "id": group_id,
            "link": f"{cls.base_url}/torrents.php?id={group_id}" if group_id else None,
            "flags": cls._edition_encodings(editions),
            "type": group.get("releaseType"),
        }

    @staticmethod
    def _edition_encodings(editions: list[Any]) -> list[str]:
        return [
            f"{item.get('media', '')} {item.get('format', '')} {item.get('encoding', '')}".strip()
            for raw in editions
            if isinstance(raw, dict)
            for item in [cast(dict[str, Any], raw)]
        ]

    async def get_torrent(self, torrent_id: int | str, meta: Meta) -> dict[str, Any] | None:
        identifier = str(torrent_id).strip()
        if not identifier.isdigit() or not self.api_key:
            return None
        payload = await self._torrent_payload(meta, identifier)
        return self._torrent_response(payload)

    async def _torrent_payload(self, meta: Meta, identifier: str) -> dict[str, Any] | None:
        try:
            return await self._gazelle_json(meta, {"action": "torrent", "id": identifier}, request_timeout=15.0)
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(f"{self.tracker}: [yellow]read-only torrent lookup failed for {identifier}: {error}[/yellow]")
            return None

    @staticmethod
    def _torrent_response(payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not payload or payload.get("status") != "success":
            return None
        response = payload.get("response")
        return cast(dict[str, Any], response) if isinstance(response, dict) else None

    async def get_requests(self, meta: Meta) -> list[dict[str, Any]]:
        if meta.category != "MUSIC" or not self.api_key:
            return []
        release = self._release(meta)
        album = str(release.get("album", "")).strip()
        if not album:
            return []
        payload = await self._requests_payload(meta, album)
        matches = self._request_matches(release, payload)
        self._log_request_matches(matches)
        meta.tracker_status.setdefault(self.tracker, {})["request_matches"] = matches
        return matches

    async def _requests_payload(self, meta: Meta, album: str) -> dict[str, Any] | None:
        params = {"action": "requests", "search": album, "show_filled": "false", "filter_cat[]": "1"}
        try:
            return await self._gazelle_json(meta, params, request_timeout=10.0)
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(f"{self.tracker}: [yellow]read-only request search failed: {error}[/yellow]")
            return None

    @classmethod
    def _request_matches(cls, release: MusicRelease, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        records = cls._response_results(payload)
        return [match for record in records if (match := cls._request_match(release, record)) is not None]

    @classmethod
    def _request_match(cls, release: MusicRelease, record: dict[str, Any]) -> dict[str, Any] | None:
        if record.get("isFilled"):
            return None
        match_type = cls._request_match_type(release, record)
        if match_type is None:
            return None
        request_id = record.get("requestId")
        if not isinstance(request_id, int | str):
            return None
        return cls._request_match_payload(record, request_id, match_type)

    @classmethod
    def _request_match_payload(cls, record: dict[str, Any], request_id: int | str, match_type: str) -> dict[str, Any]:
        artists = cls._request_artists(record)
        title = str(record.get("title", "")).strip()
        return {
            "id": str(request_id),
            "name": f"{' & '.join(artists)} - {title}".strip(" -"),
            "bounty": record.get("bounty", 0),
            "description": str(record.get("description", "")),
            "match_type": match_type,
            "requirements": cls._request_requirements(record),
            "url": f"{cls.base_url}/requests.php?action=view&id={request_id}",
            "year": record.get("year", ""),
        }

    @staticmethod
    def _request_requirements(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "release_type": record.get("releaseType", ""),
            "bitrate": record.get("bitrateList", ""),
            "format": record.get("formatList", ""),
            "media": record.get("mediaList", ""),
            "log_cue": record.get("logCue", ""),
        }

    def _log_request_matches(self, matches: list[dict[str, Any]]) -> None:
        if not matches:
            return
        logger.info(f"{self.tracker}: [bold yellow]matching open music request(s) found; review requirements before filling:[/bold yellow]")
        for match in matches:
            self._log_request_match(match)

    def _log_request_match(self, match: dict[str, Any]) -> None:
        logger.info(f"{self.tracker}: [bold green]{match['match_type'].title()} match:[/bold green] {escape(str(match['name']))} — bounty: {escape(str(match['bounty']))}")
        logger.info(f"{self.tracker}: [cyan]{match['url']}[/cyan]")
        logger.info(f"{self.tracker}: [yellow]Requested technical fields: {match['requirements']}[/yellow]")

    @staticmethod
    def _normalise_request_text(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @classmethod
    def _request_artists(cls, record: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for role in cls._artist_roles(record.get("artists")):
            names.extend(cls._role_artist_names(role))
        return list(dict.fromkeys(names))

    @staticmethod
    def _artist_roles(value: Any) -> list[list[Any]]:
        roles = value if isinstance(value, list) else []
        return [cast(list[Any], role) for role in roles if isinstance(role, list)]

    @staticmethod
    def _role_artist_names(role: list[Any]) -> list[str]:
        return [name for item in role if isinstance(item, dict) if (name := str(cast(dict[str, Any], item).get("name", "")).strip())]

    @classmethod
    def _request_match_type(cls, release: MusicRelease, record: dict[str, Any]) -> str | None:
        if not cls._request_title_matches(release, record):
            return None
        artist_match = cls._request_artist_matches(release, record)
        year_match = cls._request_year_matches(release, record)
        return "exact" if artist_match and year_match else "partial"

    @classmethod
    def _request_title_matches(cls, release: MusicRelease, record: dict[str, Any]) -> bool:
        return cls._normalise_request_text(record.get("title")) == cls._normalise_request_text(release.get("album"))

    @classmethod
    def _request_artist_matches(cls, release: MusicRelease, record: dict[str, Any]) -> bool:
        release_artists = {cls._normalise_request_text(item) for item in cls._artists(release)}
        request_artists = {cls._normalise_request_text(item) for item in cls._request_artists(record)}
        return bool(request_artists and release_artists.intersection(request_artists))

    @staticmethod
    def _request_year_matches(release: MusicRelease, record: dict[str, Any]) -> bool:
        request_year = str(record.get("year", "")).strip()
        release_year = str(release.get("year", "")).strip()
        return bool(request_year and release_year and request_year == release_year)

    async def get_name(self, meta: Meta) -> str:
        """For the terminal display only, not for upload."""
        release = self._release(meta)
        return f"{release.get('artist', '')} - {release.get('album', '')} [{release.get('year', '')!s}]".strip(" -")

    async def upload(self, meta: Meta) -> bool:
        release = self._release(meta)
        if not await self._preflight_passes(meta, release):
            return False
        if meta.debug:
            return self._record_debug_upload(meta, release)
        payload = await self._prepared_upload_payload(meta, release)
        return False if payload is None else self._record_upload_response(meta, payload)

    async def _preflight_passes(self, meta: Meta, release: MusicRelease) -> bool:
        if not await self.get_additional_checks(meta):
            return False
        return self._validation_passes(meta, release)

    async def _prepared_upload_payload(self, meta: Meta, release: MusicRelease) -> dict[str, Any] | None:
        if not self._credentials_present(meta):
            return None
        torrent_path = await self._prepare_upload_torrent(meta)
        if torrent_path is None:
            return None
        return await self._post_release(meta, release, torrent_path)

    def _validation_passes(self, meta: Meta, release: MusicRelease) -> bool:
        issues = OrpheusMusicValidator().validate(release)
        errors = [issue.message for issue in issues if issue.level == ValidationLevel.ERROR]
        if not errors:
            return True
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "Validation failed: " + " | ".join(errors)
        return False

    def _record_debug_upload(self, meta: Meta, release: MusicRelease) -> bool:
        data = self.build_upload_payload(meta, release)
        debug_payload: dict[str, Any] = {
            **data,
            "file_input": "<not-created in debug mode>",
            "logfiles[]": list(release.auxiliary.logs),
        }
        status = meta.tracker_status.setdefault(self.tracker, {})
        status["status_message"] = "Debug mode: upload skipped; payload prepared locally. Artwork is optional on Orpheus."
        status["debug_payload_fields"] = sorted(data)
        status["debug_payload"] = debug_payload
        logger.info(f"{self.tracker}: [yellow]debug mode enabled; POST upload skipped. Prepared payload:[/yellow]")
        logger.info(json.dumps(debug_payload, ensure_ascii=False, indent=2), extra={"markup": False})
        return True

    def _credentials_present(self, meta: Meta) -> bool:
        if self.api_key and self.announce_url:
            return True
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "Missing Orpheus API key or announce URL."
        return False

    async def _prepare_upload_torrent(self, meta: Meta) -> Path | None:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag, announce_url=self.announce_url)
        torrent_path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        if torrent_path.is_file():
            return torrent_path
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "Tracker torrent was not created."
        return None

    async def _post_release(self, meta: Meta, release: MusicRelease, torrent_path: Path) -> dict[str, Any] | None:
        data = self.build_upload_payload(meta, release)
        try:
            return await self._upload_json(meta, release, torrent_path, data)
        except (httpx.HTTPError, ValueError, OSError) as error:
            meta.tracker_status.setdefault(self.tracker, {})["status_message"] = f"Upload request failed: {error}"
            return None

    async def _upload_json(
        self,
        meta: Meta,
        release: MusicRelease,
        torrent_path: Path,
        data: dict[str, str | list[str] | list[int] | int],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), headers=self._headers(meta)) as client:
            with torrent_path.open("rb") as torrent_file:
                files = self._upload_files(release, torrent_path, torrent_file)
                try:
                    response = await client.post(f"{self.base_url}/ajax.php?action=upload", data=data, files=files)
                finally:
                    self._close_log_handles(files)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Orpheus upload response must be a JSON object")
        return cast(dict[str, Any], payload)

    @staticmethod
    def _upload_files(release: MusicRelease, torrent_path: Path, torrent_file: Any) -> list[tuple[str, tuple[str, Any, str]]]:
        files: list[tuple[str, tuple[str, Any, str]]] = [("file_input", (torrent_path.name, torrent_file, "application/x-bittorrent"))]
        for log in release.auxiliary.logs:
            log_path = release.path / log
            if log_path.is_file():
                files.append(("logfiles[]", (log_path.name, log_path.open("rb"), "text/plain")))
        return files

    @staticmethod
    def _close_log_handles(files: list[tuple[str, tuple[str, Any, str]]]) -> None:
        for _, (_, handle, _) in files[1:]:
            handle.close()

    def _record_upload_response(self, meta: Meta, payload: Any) -> bool:
        """Store torrent IDs, warnings and semantic upload status."""
        status = meta.tracker_status.setdefault(self.tracker, {})
        response_payload = self._upload_response_mapping(payload, status)
        if response_payload is None:
            return False
        if response_payload.get("status") != "success":
            self._record_rejected_upload(status, response_payload)
            return False
        result = self._upload_result_mapping(status, response_payload)
        if result is None:
            return True
        self._record_result_ids(status, result)
        self._record_result_warnings(status, result)
        return True

    @staticmethod
    def _upload_response_mapping(payload: Any, status: dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            return cast(dict[str, Any], payload)
        status["status_message"] = "Orpheus returned a malformed upload response."
        return None

    @staticmethod
    def _record_rejected_upload(status: dict[str, Any], payload: dict[str, Any]) -> None:
        error = str(payload.get("error", "")).strip()
        status["status_message"] = f"Orpheus rejected the upload request: {error}" if error else "Orpheus rejected the upload request."

    @staticmethod
    def _upload_result_mapping(status: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        result = payload.get("response")
        if isinstance(result, dict):
            return cast(dict[str, Any], result)
        status["status_message"] = "Orpheus accepted the request but returned no upload result. Verify it on the tracker."
        return None

    @staticmethod
    def _record_result_ids(status: dict[str, Any], result: dict[str, Any]) -> None:
        for source, target in (("torrentId", "torrent_id"), ("groupId", "group_id")):
            value = result.get(source)
            if value is not None:
                status[target] = value
        if "newgroup" in result:
            status["new_group"] = bool(result["newgroup"])

    def _record_result_warnings(self, status: dict[str, Any], result: dict[str, Any]) -> None:
        warnings = self._warning_list(result.get("warnings"))
        if not warnings:
            status["status_message"] = "Upload accepted by Orpheus."
            return
        status["warnings"] = warnings
        status["status_message"] = f"Upload accepted by Orpheus. Warnings: {' | '.join(warnings)}"
        logger.warning(f"{self.tracker}: [yellow]upload accepted with warning(s): {' | '.join(warnings)}[/yellow]")

    @staticmethod
    def _warning_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [text for item in values if item is not None if (text := str(item).strip())]

    def build_upload_payload(self, meta: Meta, release: MusicRelease) -> dict[str, str | list[str] | list[int] | int]:
        media = self._required_media(release)
        format_name = self._first_format(release)
        bitrate, other_bitrate, vbr = self._encoding(release, format_name)
        artists = self._artists(release)
        remaster = self._remaster_fields(release)
        payload = self._base_upload_payload(meta, release, media, format_name, bitrate, vbr, artists, remaster)
        if other_bitrate:
            payload["other_bitrate"] = other_bitrate
        return self._non_empty_payload(payload)

    @staticmethod
    def _required_media(release: MusicRelease) -> str:
        media = str(release.get("media", ""))
        if not media:
            raise ValueError("Orpheus media/source must be provided; the analyzer will not guess it.")
        return media

    @staticmethod
    def _first_format(release: MusicRelease) -> str:
        return next(iter(release.formats))

    def _remaster_fields(self, release: MusicRelease) -> dict[str, str | int]:
        edition_year = self._edition_year_for_upload(release)
        is_remaster = bool(edition_year or release.get("edition"))
        return {
            "remaster_catalogue_number": str(release.get("edition_catalogue_number", "")) if is_remaster else "",
            "remaster_record_label": str(release.get("edition_label", "")) if is_remaster else "",
            "remaster_title": str(release.get("edition", "")),
            "remaster_year": edition_year,
            "remaster": int(is_remaster),
        }

    def _base_upload_payload(
        self,
        meta: Meta,
        release: MusicRelease,
        media: str,
        format_name: str,
        bitrate: str,
        vbr: bool,
        artists: list[str],
        remaster: dict[str, str | int],
    ) -> dict[str, str | list[str] | list[int] | int]:
        return {
            "album_desc": self._album_description(release),
            "artists[]": artists,
            "bitrate": bitrate,
            "catalogue_number": str(release.get("release_catalogue_number", release.get("catalogue_number", ""))),
            "format": format_name,
            "image": self._cover_url(meta),
            "importance[]": [1] * len(artists),
            "media": media,
            "record_label": str(release.get("release_label", release.get("label", ""))),
            "release_desc": self._release_description(release),
            "releasetype": self.release_types.get(str(release.get("release_type", "Unknown")), 21),
            **remaster,
            "scene": int(bool(meta.scene)),
            "submit": 1,
            "tags": self._genre_tags(release),
            "title": str(release.get("album")),
            "type": 0,
            "vbr": int(vbr),
            "year": str(release.get("year", "")),
        }

    @staticmethod
    def _genre_tags(release: MusicRelease) -> str:
        return ",".join(str(value).replace(" ", ".").lower() for value in release.get("genres", []))

    @staticmethod
    def _non_empty_payload(payload: dict[str, str | list[str] | list[int] | int]) -> dict[str, str | list[str] | list[int] | int]:
        return {key: value for key, value in payload.items() if value not in ("", None, [])}

    @staticmethod
    def _edition_year_for_upload(release: MusicRelease) -> str:
        """Resolve Orpheus's mandatory edition year without mutating metadata."""
        candidates = (release.get("edition_year", ""), release.get("release_year", ""), release.get("retail_date", ""), release.get("year", ""))
        for candidate in candidates:
            match = re.search(r"\b(\d{4})\b", str(candidate or ""))
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _artists(cls, release: MusicRelease) -> list[str]:
        artists = cls._artist_list(release.get("artists"))
        if artists:
            return artists
        artist = str(release.get("artist", "")).strip()
        return [artist] if artist else []

    @staticmethod
    def _artist_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [text for item in values if item is not None if (text := str(item).strip())]

    @staticmethod
    def _form_data(payload: dict[str, str | int | list[str | int]]) -> list[tuple[str, str | int]]:
        """Encode repeated Gazelle fields as repeated multipart form keys."""
        form: list[tuple[str, str | int]] = []
        for key, value in payload.items():
            if isinstance(value, list):
                form.extend((key, item) for item in value)
            else:
                form.append((key, value))
        return form

    @classmethod
    def _cover_url(cls, meta: Meta) -> str:
        """Return an allowed optional HTTP(S) artwork URL."""
        value = str(meta.artwork_url or "").strip()
        parsed = urlparse(value)
        return value if cls._allowed_cover_location(parsed.scheme, parsed.hostname or "") else ""

    @classmethod
    def _allowed_cover_location(cls, scheme: str, hostname: str) -> bool:
        if scheme not in {"http", "https"} or not hostname:
            return False
        return not cls._banned_cover_host(hostname.casefold())

    @staticmethod
    def _banned_cover_host(hostname: str) -> bool:
        banned_hosts = ("discogs.com", "fbcdn.net", "photobucket.com")
        return any(hostname == banned or hostname.endswith(f".{banned}") for banned in banned_hosts)

    @classmethod
    def _encoding(cls, release: MusicRelease, format_name: str) -> tuple[str, str, bool]:
        if format_name == "FLAC":
            return cls._flac_encoding(release)
        average = cls._average_bitrate(release)
        mode = cls._bitrate_mode(release)
        if cls._standard_mp3(format_name, mode, average):
            return str(average), "", False
        return "Other", str(average or "VBR"), mode == "VBR"

    @staticmethod
    def _flac_encoding(release: MusicRelease) -> tuple[str, str, bool]:
        high_resolution = any((track.bit_depth or 0) > 16 for track in release.tracks)
        return ("24bit Lossless" if high_resolution else "Lossless", "", False)

    @staticmethod
    def _average_bitrate(release: MusicRelease) -> int:
        tracks = release.tracks
        return round(sum(track.bitrate or 0 for track in tracks) / max(len(tracks), 1) / 1000)

    @staticmethod
    def _bitrate_mode(release: MusicRelease) -> str | None:
        return next((track.bitrate_mode for track in release.tracks if track.bitrate_mode), None)

    @staticmethod
    def _standard_mp3(format_name: str, mode: str | None, average: int) -> bool:
        return format_name == "MP3" and mode == "CBR" and average in {192, 256, 320}

    @classmethod
    def _album_description(cls, release: MusicRelease) -> str:
        lines = [f"[b]Tracklist[/b] ({release.disc_count} disc(s))\n"]
        lines.extend(cls._track_description_lines(release))
        lines.append(f"\nTotal length: {cls._format_duration(cls._total_duration(release))}")
        return "\n".join(lines)

    @classmethod
    def _track_description_lines(cls, release: MusicRelease) -> list[str]:
        return [cls._track_description_line(release, track) for track in release.tracks]

    @classmethod
    def _track_description_line(cls, release: MusicRelease, track: Any) -> str:
        prefix = f"{track.disc_number}." if release.disc_count > 1 else ""
        number = str(track.track_number) if track.track_number else "--"
        duration = float(track.duration or 0)
        title = track.title or Path(track.relative_path).stem
        return f"{prefix}{number}. {title} ({cls._format_duration(duration)})"

    @staticmethod
    def _total_duration(release: MusicRelease) -> float:
        return sum(float(track.duration or 0) for track in release.tracks)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total_seconds = max(0, round(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    @classmethod
    def _release_description(cls, release: MusicRelease) -> str:
        parts = cls._technical_audio_parts(release)
        parts.extend(cls._release_metadata_parts(release))
        parts.extend(cls._auxiliary_parts(release))
        return "\n".join(parts)

    @classmethod
    def _technical_audio_parts(cls, release: MusicRelease) -> list[str]:
        variants = cls._audio_variants(release)
        return [f"Technical audio: {', '.join(variants)}."] if variants else []

    @classmethod
    def _audio_variants(cls, release: MusicRelease) -> list[str]:
        return sorted({cls._audio_variant(track) for track in release.tracks})

    @staticmethod
    def _audio_variant(track: Any) -> str:
        return f"{track.bit_depth or '?'}-bit / {(track.sample_rate or 0) / 1000:g} kHz / {track.channels or '?'}ch"

    @staticmethod
    def _release_metadata_parts(release: MusicRelease) -> list[str]:
        parts: list[str] = []
        retail_date = release.get("retail_date")
        store_url = release.get("store_url")
        if retail_date:
            parts.append(f"Retail date: {retail_date}.")
        if store_url:
            parts.append(f"[url={store_url}]Store listing[/url]")
        return parts

    @staticmethod
    def _auxiliary_parts(release: MusicRelease) -> list[str]:
        parts: list[str] = []
        if release.auxiliary.cues:
            parts.append("Cue sheet included.")
        if release.auxiliary.logs:
            parts.append("Rip log included.")
        return parts
