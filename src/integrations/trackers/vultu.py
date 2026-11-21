# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import re
from pathlib import Path
from typing import Any, TypeGuard, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.domain_models.tracker_image_policy import get_tracker_image_collection
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers import payload_preview
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder
from src.integrations.trackers.music_validation import lame_preset
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR

Config = dict[str, Any]


class Vultu:
    """
    Vultu is a Brazilian Private Torrent Tracker for MOVIES / TV / ANIME / GAMES / MUSIC
    """

    base_url = "https://vultu.org"
    api_url = f"{base_url}/api/v1"

    auth_type = "other_api"
    tracker = "VULTU"
    display_name = "vultu"
    source_flag = "vultu"
    banned_groups: tuple[str, ...] = ()
    banned_url = f"{api_url}/banned-groups"
    banned_groups_response_key = "groups"
    upload_url = f"{api_url}/torrents"
    torrent_url = f"{base_url}/torrents/"
    requests_url = f"{base_url}/pedidos"
    tracker_urls = ("vultu.org",)
    supported_categories = ("MOVIE", "TV", "GAME", "MUSIC")
    allows_bloated_audio = True

    min_screenshots = 3
    max_screenshots = 6
    max_name_length = 255
    max_nfo_size = 64 * 1024
    # BBCode notes are converted to Markdown by the site; the raw text may be this long.
    max_bbcode_notes_length = 131072
    max_duplicate_pages = 5

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.common = Common(config)
        self.tracker_config: dict[str, Any] = cast(
            dict[str, Any], config.get("TRACKERS", {}).get(self.tracker, {})
        )
        self.api_key = str(self.tracker_config.get("api_key", "")).strip()

    def _headers(
        self, meta: Meta, accept: str = "application/json"
    ) -> dict[str, str]:
        return {
            "User-Agent": f"{meta.ua_name} {meta.current_version} ({platform.system()} {platform.release()})",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
        }

    @staticmethod
    def _append_retry_after(response: httpx.Response, message: str) -> str:
        retry_after = response.headers.get("Retry-After")
        if response.status_code == 429 and retry_after:
            return f"{message} Retry after {retry_after}s."
        return message

    @staticmethod
    def _problem_message(
        response: httpx.Response, problem: dict[str, Any]
    ) -> str:
        detail = str(
            problem.get("detail")
            or problem.get("title")
            or response.reason_phrase
        )
        code = str(problem.get("code") or "")
        message = f"HTTP {response.status_code}: {detail}{f' ({code})' if code else ''}"
        return Vultu._append_retry_after(response, message)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Format Vultu's RFC 9457 problem responses for the user."""
        try:
            problem = cast(dict[str, Any], response.json())
        except ValueError:
            return f"HTTP {response.status_code}: {response.text[:500]}"
        return Vultu._problem_message(response, problem)

    @staticmethod
    def _tmdb_kind(meta: Meta) -> str:
        return "tv" if str(meta.category).upper() == "TV" else "movie"

    @staticmethod
    def _music_release(meta: Meta) -> dict[str, Any]:
        return (
            cast(dict[str, Any], meta.music_release)
            if isinstance(meta.music_release, dict)
            else {}
        )

    @staticmethod
    def _musicbrainz_work(external_ids: dict[str, Any]) -> str:
        pattern = re.compile(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.IGNORECASE
        )
        for key, kind in (
            ("musicbrainz_release_group", "release-group"),
            ("musicbrainz_release", "release"),
        ):
            value = str(external_ids.get(key) or "").strip()
            if pattern.fullmatch(value):
                return f"musicbrainz:{kind}:{value.lower()}"
        return ""

    @staticmethod
    def _discogs_master(meta: Meta, external_ids: dict[str, Any]) -> str:
        master = str(
            external_ids.get("discogs_master")
            or meta.music_discogs_master_id
            or ""
        ).strip()
        return f"discogs:master:{master}" if master.isdecimal() else ""

    @staticmethod
    def _discogs_release(meta: Meta, external_ids: dict[str, Any]) -> str:
        release = str(
            external_ids.get("discogs_release")
            or meta.music_discogs_release_id
            or meta.music_discogs_id
            or ""
        ).strip()
        return f"discogs:release:{release}" if release.isdecimal() else ""

    @staticmethod
    def _discogs_work(meta: Meta, external_ids: dict[str, Any]) -> str:
        if not meta.music_discogs_enabled:
            return ""
        return Vultu._discogs_master(
            meta, external_ids
        ) or Vultu._discogs_release(meta, external_ids)

    @classmethod
    def _music_work(cls, meta: Meta) -> str:
        """Return the album as MusicBrainz release group, MusicBrainz release, Discogs master or Discogs release."""
        raw_ids = cls._music_release(meta).get("external_ids")
        external_ids = (
            cast(dict[str, Any], raw_ids) if isinstance(raw_ids, dict) else {}
        )
        return cls._musicbrainz_work(external_ids) or cls._discogs_work(
            meta, external_ids
        )

    async def _lookup_imdb_work(self, meta: Meta, kind: str) -> str:
        if not meta.imdb_tt:
            return ""
        response = await self._find_work_by_imdb(meta, kind)
        self._raise_for_work_lookup_failure(response)
        return self._matching_work(response, kind)

    async def _find_work_by_imdb(
        self, meta: Meta, kind: str
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            headers=self._headers(meta), timeout=30.0
        ) as client:
            return await client.get(
                f"{self.api_url}/works",
                params={"imdb": meta.imdb_tt, "kind": kind},
            )

    def _raise_for_work_lookup_failure(self, response: httpx.Response) -> None:
        if response.status_code != 200:
            raise RuntimeError(
                f"{self.tracker}: work lookup failed: {self._error_message(response)}"
            )

    @staticmethod
    def _matching_work(response: httpx.Response, kind: str) -> str:
        results = cast(
            list[dict[str, Any]],
            cast(dict[str, Any], response.json()).get("results") or [],
        )
        return next(
            (
                str(result["work"])
                for result in results
                if result.get("kind") == kind and result.get("work")
            ),
            "",
        )

    @staticmethod
    def _work_from_ids(meta: Meta, category: str) -> str:
        if category == "GAME":
            return Vultu._igdb_work(meta)
        if category == "MUSIC":
            return Vultu._music_work(meta)
        return Vultu._tmdb_work(meta)

    @staticmethod
    def _igdb_work(meta: Meta) -> str:
        return f"igdb:{meta.igdb_id}" if meta.igdb_id else ""

    @staticmethod
    def _tmdb_work(meta: Meta) -> str:
        tmdb_id = meta.tmdb or meta.tmdb_id
        return f"tmdb:{Vultu._tmdb_kind(meta)}:{tmdb_id}" if tmdb_id else ""

    async def get_work(self, meta: Meta) -> str:
        """Return Vultu's work identifier (TMDB, IGDB, MusicBrainz, or Discogs)."""
        category = str(meta.category).upper()
        work = self._work_from_ids(meta, category)
        if category in ("GAME", "MUSIC") or work:
            return work
        return await self._lookup_imdb_work(meta, self._tmdb_kind(meta))

    @staticmethod
    def _season_number(meta: Meta) -> int | None:
        """Return the single season of a TV release, or None for multi-season packs."""
        if str(meta.category).upper() != "TV":
            return None
        match = re.fullmatch(
            r"S(\d+)", str(meta.season or "").strip(), re.IGNORECASE
        )
        return int(match.group(1)) if match else None

    @classmethod
    def _music_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        raw_tracks = cls._music_release(meta).get("tracks")
        if not isinstance(raw_tracks, list):
            return []
        return [
            cast(dict[str, Any], track)
            for track in cast(list[Any], raw_tracks)
            if isinstance(track, dict)
        ]

    @staticmethod
    def _common_music_encoding(tracks: list[dict[str, Any]]) -> str:
        presets = {
            lame_preset(str(track.get("encoder_settings") or ""))
            for track in tracks
        }
        preset = next(iter(presets)) if len(presets) == 1 else ""
        return preset.split()[0] if preset else ""

    @staticmethod
    def _constant_bitrate_encoding(tracks: list[dict[str, Any]]) -> str:
        if not Vultu._constant_bitrate_tracks(tracks):
            return ""
        average = Vultu._average_track_bitrate(tracks)
        return str(average) if average else ""

    @staticmethod
    def _constant_bitrate_tracks(tracks: list[dict[str, Any]]) -> bool:
        return all(
            track.get("bitrate_mode") in {"CBR", "ABR"} for track in tracks
        )

    @staticmethod
    def _average_track_bitrate(tracks: list[dict[str, Any]]) -> int:
        bitrate = sum(int(track.get("bitrate") or 0) for track in tracks)
        return round(bitrate / len(tracks) / 1000)

    @classmethod
    def _music_encoding(cls, meta: Meta) -> str:
        """Return the lossy encoding (V0, 320...) that Vultu's music names carry after the format."""
        tracks = cls._music_tracks(meta)
        if not tracks:
            return ""
        return cls._common_music_encoding(
            tracks
        ) or cls._constant_bitrate_encoding(tracks)

    @staticmethod
    def _keep_music_name(base: str, encoding: str) -> bool:
        words = base.split()
        # Lossless names end with the sample rate ("44.1 kHz").
        return (
            not words
            or words[-1].upper() in {"FLAC", "ALAC", "KHZ"}
            or not encoding
        )

    def _music_name(self, meta: Meta) -> str:
        """Artist - Album Year Media Format Encoding-GROUP; lossless keeps the bit depth and rate already in the name."""
        name = str(meta.name or "").strip()
        base, tag = self._music_name_parts(name, str(meta.tag or "").strip())
        encoding = self._music_encoding(meta)
        return self._format_music_name(name, base, tag, encoding)

    @staticmethod
    def _music_name_parts(name: str, tag: str) -> tuple[str, str]:
        if not tag or not name.endswith(tag):
            tag = ""
        return name[: len(name) - len(tag)].rstrip(), tag

    def _format_music_name(
        self, name: str, base: str, tag: str, encoding: str
    ) -> str:
        if self._keep_music_name(base, encoding):
            return name
        return f"{base} {encoding}{tag}"

    async def get_name(self, meta: Meta) -> str:
        if str(meta.category).upper() == "MUSIC":
            return (meta.manual_name or self._music_name(meta)).strip()
        # Vultu follows the same naming as CapybaraBR.
        cbr = CapybaraBR(self.config)
        cbr.tracker = self.tracker
        return (await cbr.get_name(meta))["name"].strip()

    def _screenshot_urls(self, meta: Meta) -> list[str]:
        # In Music the screenshots field holds the track spectrograms.
        images = get_tracker_image_collection(
            meta,
            self.tracker,
            "spectrograms_images"
            if str(meta.category).upper() == "MUSIC"
            else "screenshots",
        )
        urls = [
            raw_url
            for image in images
            if self._valid_screenshot_url(raw_url := image.get("raw_url"))
        ]
        return list(dict.fromkeys(urls))[: self.max_screenshots]

    @staticmethod
    def _valid_screenshot_url(raw_url: object) -> TypeGuard[str]:
        return isinstance(raw_url, str) and raw_url.startswith("https://")

    async def get_mediainfo(self, meta: Meta) -> str:
        # Vultu discards MediaInfo for games and music.
        if str(meta.category).upper() in ("GAME", "MUSIC"):
            return ""
        tmp_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        if meta.is_disc == "BDMV":
            # Vultu accepts BDInfo in the same field as MediaInfo.
            bd_summary_path = tmp_dir / "BD_SUMMARY_00.txt"
            if not bd_summary_path.is_file():
                return await self.common.get_bdmv_mediainfo(meta)
            async with aiofiles.open(
                bd_summary_path, encoding="utf-8"
            ) as bd_file:
                return await bd_file.read()
        mi_path = tmp_dir / "MEDIAINFO_CLEANPATH.txt"
        if not mi_path.is_file():
            return ""
        async with aiofiles.open(mi_path, encoding="utf-8") as mi_file:
            return await mi_file.read()

    async def get_notes(self, meta: Meta) -> str:
        """Build the release notes in BBCode; Vultu converts them to its Markdown (notes_format=bbcode).

        The site drops images and videos from the notes, and MediaInfo, NFO, screenshots and music
        spectrograms have their own fields, so those sections are left out.
        """
        builder = DescriptionBuilder(self.tracker, self.config)
        notes = await builder.general_description_generator(
            meta,
            audio_spectrogram=False,
            logo=False,
            mediainfo=False,
            menu_screenshots=False,
            nfo=False,
            screenshots=False,
            tonemapped_header=False,
        )
        notes = notes.strip()
        if len(notes) > self.max_bbcode_notes_length:
            logger.warning(
                f"{self.tracker}: [yellow]Release notes are longer than {self.max_bbcode_notes_length} characters and will not be sent.[/yellow]"
            )
            return ""
        return notes

    async def get_nfo(self, meta: Meta) -> tuple[str, bytes, str] | None:
        nfo_files = self._nfo_files(meta)
        if not nfo_files:
            return None
        nfo_path = nfo_files[0]
        if self._nfo_is_oversized(nfo_path):
            logger.warning(
                f"{self.tracker}: [yellow]NFO is larger than 64 KB and will not be sent.[/yellow]"
            )
            return None
        async with aiofiles.open(nfo_path, "rb") as nfo_file:
            return (nfo_path.name, await nfo_file.read(), "text/plain")

    @staticmethod
    def _nfo_is_oversized(nfo_path: Path) -> bool:
        return nfo_path.stat().st_size > Vultu.max_nfo_size

    @staticmethod
    def _nfo_files(meta: Meta) -> list[Path]:
        nfo_files = sorted(
            (Path(meta.base_dir) / "tmp" / meta.uuid).glob("*.nfo")
        )
        if nfo_files or not Vultu._can_use_source_nfo(meta):
            return nfo_files
        return sorted(Path(str(meta.path)).parent.glob("*.nfo"))

    @staticmethod
    def _can_use_source_nfo(meta: Meta) -> bool:
        return bool(
            meta.keep_nfo and (meta.keep_folder or meta.isdir) and meta.path
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        """Enforce Vultu's upload rules that can be checked before uploading."""
        if not self._category_checks_pass(meta):
            return False
        return await self._name_length_passes(meta)

    def _category_checks_pass(self, meta: Meta) -> bool:
        category = str(meta.category).upper()
        checks = {
            "GAME": self._game_checks_pass,
            "MUSIC": self._music_checks_pass,
            "MOVIE": self._video_checks_pass,
            "TV": self._video_checks_pass,
        }
        check = checks.get(category)
        return check(meta) if check else True

    def _game_checks_pass(self, meta: Meta) -> bool:
        if meta.igdb_id:
            return True
        logger.warning(
            f"{self.tracker}: [red]Games must be linked to an IGDB entry.[/red]"
        )
        return False

    def _music_checks_pass(self, meta: Meta) -> bool:
        if self._music_work(meta):
            return True
        logger.warning(
            f"{self.tracker}: [red]Music must be linked to a MusicBrainz or Discogs album.[/red]"
        )
        return False

    def _video_checks_pass(self, meta: Meta) -> bool:
        if not self._has_video_id(meta):
            logger.warning(
                f"{self.tracker}: [red]Movies and TV must be linked to a TMDB (or IMDb) entry.[/red]"
            )
            return False
        if int(meta.screens or 0) >= self.min_screenshots:
            return True
        logger.warning(
            f"{self.tracker}: [red]Movies, TV and anime require at least {self.min_screenshots} screenshots (configured {meta.screens}).[/red]"
        )
        return False

    @staticmethod
    def _has_video_id(meta: Meta) -> bool:
        return bool(meta.tmdb or meta.tmdb_id or meta.imdb_tt)

    async def _name_length_passes(self, meta: Meta) -> bool:
        if len(await self.get_name(meta)) <= self.max_name_length:
            return True
        logger.warning(
            f"{self.tracker}: [red]Release name is longer than {self.max_name_length} characters.[/red]"
        )
        return False

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        """Search Vultu's duplicates endpoint, including releases pending moderation."""
        dupes: list[dict[str, Any]] = []
        params = await self._duplicate_params(meta)
        async with httpx.AsyncClient(
            headers=self._headers(meta), timeout=30.0
        ) as client:
            for _ in range(self.max_duplicate_pages):
                data = await self._duplicate_page(client, params)
                dupes.extend(self._duplicate_rows(data))
                next_page = data.get("next")
                if next_page is None:
                    break
                params["before"] = next_page
        return dupes

    async def _duplicate_params(self, meta: Meta) -> dict[str, Any]:
        work = await self.get_work(meta)
        params: dict[str, Any] = (
            {"work": work} if work else {"name": meta.title}
        )
        season = self._season_number(meta)
        if work and season is not None:
            params["season"] = season
        return params

    async def _duplicate_page(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> dict[str, Any]:
        response = await client.get(
            f"{self.api_url}/duplicates", params=params
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"{self.tracker}: duplicate search failed: {self._error_message(response)}"
            )
        return cast(dict[str, Any], response.json())

    def _duplicate_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        releases = cast(list[dict[str, Any]], data.get("releases") or [])
        return [
            row
            for release in releases
            if (row := self._duplicate_row(release)) is not None
        ]

    @staticmethod
    def _duplicate_row(release: dict[str, Any]) -> dict[str, Any] | None:
        name = release.get("name")
        if not name:
            return None
        files = [
            str(file.get("path"))
            for file in cast(list[dict[str, Any]], release.get("files") or [])
            if file.get("path")
        ]
        return {
            "name": str(name),
            "size": release.get("size"),
            "files": files,
            "file_count": len(files),
            "link": release.get("url"),
            "id": release.get("id"),
        }

    def _request_search_enabled(self, meta: Meta) -> bool:
        return bool(
            self.config["DEFAULT"].get("search_requests", False)
            or meta.search_requests
        )

    async def _open_requests(self, meta: Meta) -> list[dict[str, Any]]:
        work = await self.get_work(meta)
        if not work:
            return []
        async with httpx.AsyncClient(
            headers=self._headers(meta), timeout=30.0
        ) as client:
            response = await client.get(
                f"{self.api_url}/requests",
                params={"status": "open", "work": work},
            )
        if response.status_code != 200:
            raise RuntimeError(self._error_message(response))
        data = cast(dict[str, Any], response.json())
        return cast(list[dict[str, Any]], data.get("requests") or [])

    def _request_rows(
        self, meta: Meta, items: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        season = self._season_number(meta)
        return [
            self._request_row(item)
            for item in items
            if self._request_matches_season(item, season)
        ]

    @staticmethod
    def _request_matches_season(
        item: dict[str, Any], season: int | None
    ) -> bool:
        return (
            season is None
            or item.get("season") is None
            or item.get("season") == season
        )

    def _request_row(self, item: dict[str, Any]) -> dict[str, str]:
        return {
            "Name": str(item.get("title") or ""),
            "Reward": str(item.get("bounty") or ""),
            "Link": str(
                item.get("url") or f"{self.requests_url}/{item.get('id')}"
            ),
        }

    def _log_requests(self, results: list[dict[str, str]]) -> None:
        if not results:
            return
        lines = [
            f"\n{self.tracker}: [bold yellow]Seu upload pode atender o(s) seguinte(s) pedido(s), confira:[/bold yellow]\n"
        ]
        lines.extend(
            f"[bold green]Nome:[/bold green] {result['Name']}\n[bold green]Recompensa:[/bold green] {result['Reward']}\n[bold green]Link:[/bold green] {result['Link']}\n"
            for result in results
        )
        logger.info("\n".join(lines))

    async def get_requests(self, meta: Meta) -> list[dict[str, str]]:
        """Show the open requests for this work; filling them is left to the user on the site."""
        if not self._request_search_enabled(meta):
            return []
        try:
            results = self._request_rows(meta, await self._open_requests(meta))
        except (httpx.HTTPError, RuntimeError) as error:
            logger.info(
                f"{self.tracker}: Unable to search requests: {error}",
                extra={"markup": False},
            )
            return []
        self._log_requests(results)
        return results

    async def get_data(
        self, meta: Meta, work: str, preview: bool = False
    ) -> dict[str, Any]:
        """Build the upload form; a ``preview`` leaves out the screenshots, MediaInfo and release notes."""
        data = await self._base_data(meta, work)
        if preview:
            return data
        self._add_screenshots(meta, data)
        await self._add_media_fields(meta, data)
        return data

    async def _base_data(self, meta: Meta, work: str) -> dict[str, Any]:
        anon = bool(meta.anon) or bool(self.tracker_config.get("anon", False))
        return {
            "work": work,
            "name": await self.get_name(meta),
            "anime": "true"
            if meta.anime and work.startswith("tmdb:")
            else "false",
            "anonymous": "true" if anon else "false",
        }

    def _add_screenshots(self, meta: Meta, data: dict[str, Any]) -> None:
        screenshots = self._screenshot_urls(meta)
        if not screenshots:
            return
        data["screenshots"] = screenshots
        if str(meta.category).upper() != "MUSIC":
            # Shown on the site only when the MediaInfo says the video is HDR.
            data["tonemapped"] = "true" if meta.tonemapped else "false"

    async def _add_media_fields(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        if mediainfo := await self.get_mediainfo(meta):
            data["mediainfo"] = mediainfo
        if notes := await self.get_notes(meta):
            data["notes"] = notes
            data["notes_format"] = "bbcode"

    async def get_payload_preview(
        self, meta: Meta
    ) -> payload_preview.TrackerPreview:
        """The upload form as it is known before screenshots, for the Web UI to show and edit."""
        deferred = {
            "screenshots": "Uploaded after this question",
            "mediainfo": "Read from the MediaInfo report at upload time",
            "notes": payload_preview.DESCRIPTION_NOTE,
        }
        # Without a TMDB or IGDB ID the work is looked up on Vultu, which only happens at upload time.
        if (
            str(meta.category).upper() in ("GAME", "MUSIC")
            or meta.tmdb
            or meta.tmdb_id
        ):
            work = await self.get_work(meta)
        else:
            work = ""
            deferred = {"work": payload_preview.FROM_TRACKER_NOTE, **deferred}
        specs = {
            "work": payload_preview.text("Work"),
            "name": payload_preview.text("Name"),
            "anime": payload_preview.flag("Anime", on="true", off="false"),
            "anonymous": payload_preview.flag(
                "Anonymous", on="true", off="false"
            ),
        }
        data = await self.get_data(meta, work, preview=True)
        return payload_preview.describe(
            self.tracker, data, specs, deferred=deferred
        )

    async def upload(self, meta: Meta) -> bool:
        tracker_status = meta.tracker_status.setdefault(self.tracker, {})
        work = await self.get_work(meta)
        if not self._work_is_available(work, tracker_status):
            return False
        data = payload_preview.apply_payload_edits(
            meta, self.tracker, await self.get_data(meta, work)
        )
        if not self._screenshots_are_sufficient(meta, data, tracker_status):
            return False
        if meta.debug:
            return await self._debug_upload(meta, data, tracker_status)
        return await self._upload_release(meta, data, tracker_status)

    def _work_is_available(
        self, work: str, tracker_status: dict[str, Any]
    ) -> bool:
        if work:
            return True
        tracker_status["status_message"] = (
            "data error: Could not determine the TMDB/IGDB/MusicBrainz/Discogs work required by Vultu."
        )
        return False

    def _screenshots_are_sufficient(
        self, meta: Meta, data: dict[str, Any], tracker_status: dict[str, Any]
    ) -> bool:
        if not self._requires_more_screenshots(meta, data):
            return True
        tracker_status["status_message"] = (
            f"data error: Vultu requires at least {self.min_screenshots} https screenshot URLs, found {len(data.get('screenshots', []))}."
        )
        return False

    def _requires_more_screenshots(
        self, meta: Meta, data: dict[str, Any]
    ) -> bool:
        category = str(meta.category).upper()
        return (
            category in ("MOVIE", "TV")
            and len(data.get("screenshots", [])) < self.min_screenshots
        )

    async def _debug_upload(
        self, meta: Meta, data: dict[str, Any], tracker_status: dict[str, Any]
    ) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(self._debug_data(data)))
        tracker_status["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    @staticmethod
    def _debug_data(data: dict[str, Any]) -> dict[str, Any]:
        return {
            **data,
            "mediainfo": "...[DEBUG MODE]" if data.get("mediainfo") else "",
        }

    async def _upload_release(
        self, meta: Meta, data: dict[str, Any], tracker_status: dict[str, Any]
    ) -> bool:
        files = await self._upload_files(meta, data)
        response = await self._send_upload(meta, data, files, tracker_status)
        if response is None:
            return False
        result = self._upload_result(response, tracker_status)
        if result is None:
            return False
        return await self._download_registered_torrent(
            meta, result, tracker_status
        )

    async def _upload_files(
        self, meta: Meta, data: dict[str, Any]
    ) -> dict[str, tuple[str, bytes, str]]:
        # The announce is replaced by the passkey one when the registered torrent is downloaded below.
        await self.common.create_torrent_for_upload(
            meta,
            self.tracker,
            self.source_flag,
            announce_url=f"{self.base_url}/announce",
        )
        torrent_path = (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            files = {
                "torrent": (
                    f"{data['name']}.torrent",
                    await torrent_file.read(),
                    "application/x-bittorrent",
                )
            }
        if nfo := await self.get_nfo(meta):
            files["nfo"] = nfo
        return files

    async def _send_upload(
        self,
        meta: Meta,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
        tracker_status: dict[str, Any],
    ) -> httpx.Response | None:
        try:
            async with httpx.AsyncClient(
                headers=self._headers(meta), timeout=120.0
            ) as client:
                return await client.post(
                    self.upload_url, data=data, files=files
                )
        except httpx.TimeoutException:
            tracker_status["status_message"] = (
                "data error: Request timed out; check the site before retrying (a repeated upload is safe)."
            )
        except httpx.RequestError as error:
            tracker_status["status_message"] = (
                f"data error: Unable to upload: {error!r}"
            )
        return None

    def _upload_result(
        self, response: httpx.Response, tracker_status: dict[str, Any]
    ) -> dict[str, Any] | None:
        if response.status_code not in (200, 201):
            tracker_status["status_message"] = (
                f"data error: {self._error_message(response)}"
            )
            return None
        try:
            return cast(dict[str, Any], response.json())
        except ValueError:
            tracker_status["status_message"] = (
                f"data error: Unexpected response, check the site manually: {response.text[:500]}"
            )
            return None

    async def _download_registered_torrent(
        self,
        meta: Meta,
        result: dict[str, Any],
        tracker_status: dict[str, Any],
    ) -> bool:
        urls = self._registered_torrent_urls(result, tracker_status)
        if urls is None:
            return False
        details_url, download_url = urls
        return await self._download_torrent(
            meta, download_url, details_url, tracker_status
        )

    def _registered_torrent_urls(
        self, result: dict[str, Any], tracker_status: dict[str, Any]
    ) -> tuple[str, str] | None:
        torrent_id = str(result.get("id") or "")
        if not torrent_id:
            tracker_status["status_message"] = (
                f"data error: No torrent id in response, check the site manually: {result}"
            )
            return None
        self._record_upload_success(result, torrent_id, tracker_status)
        return self._torrent_urls(result, torrent_id)

    def _record_upload_success(
        self,
        result: dict[str, Any],
        torrent_id: str,
        tracker_status: dict[str, Any],
    ) -> None:
        tracker_status["torrent_id"] = torrent_id
        tracker_status["status_message"] = (
            "Uploaded; pending moderation."
            if result.get("pending")
            else "Uploaded."
        )

    def _torrent_urls(
        self, result: dict[str, Any], torrent_id: str
    ) -> tuple[str, str]:
        details_url = str(
            result.get("url") or f"{self.torrent_url}{torrent_id}"
        )
        download_url = str(
            result.get("downloadUrl")
            or f"{self.api_url}/torrents/{torrent_id}/download"
        )
        return details_url, download_url

    async def _download_torrent(
        self,
        meta: Meta,
        download_url: str,
        details_url: str,
        tracker_status: dict[str, Any],
    ) -> bool:
        downloaded = await self.common.download_tracker_torrent(
            meta,
            self.tracker,
            headers=self._headers(meta, accept="*/*"),
            downurl=download_url,
        )
        if downloaded:
            return True
        tracker_status["status_message"] = (
            f"data error: Uploaded, but the registered .torrent could not be downloaded. Download it from {details_url}"
        )
        return False
