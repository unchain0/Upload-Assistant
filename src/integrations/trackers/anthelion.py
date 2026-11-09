# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import platform
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import httpx
from rich.markup import escape

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.media.media_info import strip_report_by_line
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.security.redaction import Redaction
from src.integrations.torrent.torrent_creator import TorrentCreator
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class Anthelion:
    """
    Anthelion (ANT) is a Private Torrent Tracker for MOVIES
    """

    auth_type = "other_api"
    tracker = "ANTHELION"
    display_name = "Anthelion"
    source_flag = "ANT"
    allowed_bloated_audio_languages = ("en",)
    reject_english_original_bloat = True
    banned_groups = (
        "3LTON",
        "4yEo",
        "ADE",
        "AFG",
        "AniHLS",
        "AnimeRG",
        "AniURL",
        "AROMA",
        "aXXo",
        "Brrip",
        "CHD",
        "CM8",
        "CrEwSaDe",
        "d3g",
        "DDR",
        "DeadFish",
        "DNL",
        "ELiTE",
        "eSc",
        "EVO",
        "FaNGDiNG0",
        "FGT",
        "FRDS",
        "FUM",
        "HAiKU",
        "HD2DVD",
        "HDS",
        "HDTime",
        "Hi10",
        "ION10",
        "iPlanet",
        "JIVE",
        "KiNGDOM",
        "Leffe",
        "LiGaS",
        "LOAD",
        "MeGusta",
        "mHD",
        "MkvCage",
        "mSD",
        "NhaNc3",
        "nHD",
        "NOIVTC",
        "nSD",
        "Oj",
        "Ozlem",
        "PiRaTeS",
        "PRoDJi",
        "RAPiDCOWS",
        "RARBG",
        "RDN",
        "REsuRRecTioN",
        "RetroPeeps",
        "RMTeam",
        "SANTi",
        "SicFoI",
        "SM737",
        "SPASM",
        "SPDVD",
        "STUTTERSHIT",
        "TBS",
        "Telly",
        "TM",
        "UPiNSMOKE",
        "URANiME",
        "WAF",
        "xRed",
        "XS",
        "YIFY",
        "YTS",
        "Zeus",
        "ZKBL",
        "ZmN",
        "ZMNT",
    )
    base_url = "https://anthelion.me"
    api_url = f"{base_url}/api.php"
    supported_categories = ("MOVIE",)
    tracker_urls = ("tracker.anthelion.me",)

    def __init__(self, config: Config):
        self.config = config
        self.common = Common(config)
        self.tracker_config = self.config["TRACKERS"].get(self.tracker, {})
        self.api_key: str = str(self.tracker_config.get("api_key", "")).strip()

    async def get_flags(self, meta: Meta) -> list[str]:
        flags = self._edition_flags(meta)
        flags.extend(self._audio_flags(meta))
        flags.extend(self._feature_flags(meta))
        return flags

    @staticmethod
    def _edition_flags(meta: Meta) -> list[str]:
        edition = str(meta.edition or "").replace("'", "")
        values = (
            "Directors",
            "Extended",
            "Uncut",
            "Unrated",
            "4KRemaster",
            "IMAX",
        )
        return [flag for flag in values if flag in edition]

    @staticmethod
    def _audio_flags(meta: Meta) -> list[str]:
        audio = str(meta.audio or "")
        return [
            flag.replace("-", "")
            for flag in ("Dual-Audio", "Atmos")
            if flag in audio
        ]

    @classmethod
    def _feature_flags(cls, meta: Meta) -> list[str]:
        return [
            flag for flag, matched in cls._feature_flag_checks(meta) if matched
        ]

    @staticmethod
    def _feature_flag_checks(meta: Meta) -> tuple[tuple[str, bool], ...]:
        return (
            ("Commentary", Anthelion._has_commentary(meta)),
            ("3D", Anthelion._is_three_d(meta)),
            ("HDR10", Anthelion._has_hdr(meta)),
            ("DV", Anthelion._has_dolby_vision(meta)),
            ("Criterion", Anthelion._is_criterion(meta)),
            ("Remux", Anthelion._is_remux(meta)),
        )

    @staticmethod
    def _has_commentary(meta: Meta) -> bool:
        return bool(meta.has_commentary or meta.manual_commentary)

    @staticmethod
    def _is_three_d(meta: Meta) -> bool:
        return meta.three_d == "3D"

    @staticmethod
    def _has_hdr(meta: Meta) -> bool:
        return "HDR" in str(meta.hdr or "")

    @staticmethod
    def _has_dolby_vision(meta: Meta) -> bool:
        return "DV" in str(meta.hdr or "")

    @staticmethod
    def _is_criterion(meta: Meta) -> bool:
        return "Criterion" in str(meta.distributor or meta.edition or "")

    @staticmethod
    def _is_remux(meta: Meta) -> bool:
        return "REMUX" in str(meta.type or "")

    async def get_release_group(self, meta: Meta) -> str:
        if meta.tag:
            tag = meta.tag

            return tag[1:]  # Remove leading character

        return ""

    async def get_tags(self, meta: Meta) -> list[str] | str:
        meta.ant_user_tags = False
        tags = self._metadata_tags(meta)
        if tags:
            return tags
        imdb_tags = self._imdb_tags(meta)
        if imdb_tags:
            await self._notify_manual_tagging(meta, imdb_tags)
            return ""
        return await self._prompt_missing_tags(meta)

    @classmethod
    def _metadata_tags(cls, meta: Meta) -> list[str]:
        return cls._normalized_tags(meta.genres)

    @classmethod
    def _imdb_tags(cls, meta: Meta) -> list[str]:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        tags = cls._normalized_tags(imdb.get("genres", []))
        allowed = cls._allowed_tags()
        return [tag for tag in tags if tag in allowed]

    @staticmethod
    def _allowed_tags() -> set[str]:
        return {
            "action",
            "adventure",
            "animation",
            "comedy",
            "crime",
            "documentary",
            "drama",
            "family",
            "fantasy",
            "history",
            "horror",
            "music",
            "mystery",
            "romance",
            "sci.fi",
            "thriller",
            "war",
            "western",
        }

    @staticmethod
    def _normalized_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            return []
        return [
            str(item).replace(" ", ".").lower()
            for item in values
            if str(item).strip()
        ]

    async def _notify_manual_tagging(
        self, meta: Meta, tags: list[str]
    ) -> None:
        logger.info(
            f"{self.tracker}: [green]Using IMDb genres for tagging: {', '.join(tags)}"
        )
        logger.info(
            f"{self.tracker}: [yellow]api will accept this upload, but no tag will be added.\nYou must manually add at least one tag from the approved list when uploaded."
        )
        await asyncio.sleep(3)
        meta.ant_user_tags = True

    async def _prompt_missing_tags(self, meta: Meta) -> list[str] | str:
        if meta.unattended and not meta.unattended_confirm:
            logger.info(
                f"{self.tracker}: [yellow]Unattended mode: No genres found for tagging. Skipping {self.tracker} upload.[/yellow]"
            )
            meta.skipping = self.tracker
            return ""
        logger.info(
            f"{self.tracker}: [yellow]No genres found for tagging. Tag required."
        )
        logger.info(
            f"{self.tracker}: [yellow]Only use a tag in the approved list found in the site search box."
        )
        logger.info(
            f"{self.tracker}: [yellow]api will accept this upload, but no tag will be added.\nYou must manually add at least one tag from the approved list when uploaded."
        )
        await asyncio.sleep(3)
        user_tag = await prompt_in_thread(
            cli_ui.ask_string,
            "Please enter at least one tag (genre) to use for the upload",
            default="",
        )
        if not user_tag:
            return []
        meta.ant_user_tags = True
        return [str(user_tag).replace(" ", ".").lower()]

    async def get_type(self, meta: Meta) -> int:
        detected = self._imdb_type(meta)
        if detected is None:
            detected = self._tmdb_type(meta)
        if detected is not None:
            return detected
        return await self._prompt_type(meta)

    @classmethod
    def _imdb_type(cls, meta: Meta) -> int | None:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        raw_type = imdb.get("type")
        if raw_type is None:
            return None
        imdb_type = str(raw_type).lower()
        if imdb_type in {"movie", "tv movie", "tvmovie"}:
            return cls._runtime_type(imdb.get("runtime", "60"))
        return {"short": 1, "tv mini series": 2, "comedy": 3}.get(imdb_type)

    @classmethod
    def _tmdb_type(cls, meta: Meta) -> int | None:
        keywords = {str(value).lower() for value in meta.keywords}
        keyword_type = cls._keyword_type(keywords)
        if keyword_type is not None:
            return keyword_type
        return cls._declared_tmdb_type(meta)

    @classmethod
    def _declared_tmdb_type(cls, meta: Meta) -> int | None:
        tmdb_type = str(meta.tmdb_type or "movie").lower()
        if tmdb_type == "miniseries":
            return 2
        if tmdb_type != "movie":
            return None
        runtime = meta.runtime if meta.runtime is not None else 60
        return cls._runtime_type(runtime)

    @staticmethod
    def _keyword_type(keywords: set[str]) -> int | None:
        if keywords.intersection({"short", "short film"}):
            return 1
        if "stand-up comedy" in keywords:
            return 3
        if "miniseries" in keywords:
            return 2
        return None

    @staticmethod
    def _runtime_type(value: Any) -> int:
        try:
            runtime = int(value)
        except TypeError, ValueError:
            runtime = 60
        return 0 if runtime >= 45 or runtime == 0 else 1

    async def _prompt_type(self, meta: Meta) -> int:
        if meta.unattended:
            logger.debug(
                f"{self.tracker}: [bold red]type could not be determined automatically in unattended mode."
            )
            return 0
        choices = ["Feature Film", "Short Film", "Miniseries", "Other"]
        choice = await prompt_in_thread(
            cli_ui.ask_choice,
            "Select the proper type for ANTHELION",
            choices=choices,
        )
        return {
            "Feature Film": 0,
            "Short Film": 1,
            "Miniseries": 2,
            "Other": 3,
        }.get(choice, 0)

    async def upload(self, meta: Meta) -> bool:
        torrent_filename = await self._prepare_torrent(meta)
        await self.common.create_torrent_for_upload(
            meta,
            self.tracker,
            self.source_flag,
            torrent_filename=torrent_filename,
        )
        audioformat = await self.get_audio(meta)
        if not audioformat:
            logger.info(
                f"{self.tracker}: [bold red]upload aborted due to unsupported audio format."
            )
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: upload aborted: unsupported audio format"
            )
            return False
        files = await self._torrent_upload_file(meta)
        data = await self._upload_data(meta, audioformat)
        if getattr(meta, "skipping", None) == self.tracker:
            return False
        await self._apply_release_group(meta, data)
        await self._apply_screenshot_policy(meta, data)
        if meta.debug:
            return await self._debug_upload(meta, data)
        return await self._submit_upload(meta, files, data)

    async def _prepare_torrent(self, meta: Meta) -> str:
        base_path = release_temp_dir(meta.base_dir, meta.uuid) / "BASE.torrent"
        if base_path.stat().st_size / 1024 <= 250:
            return "BASE"
        logger.info(
            f"{self.tracker}: [yellow]Existing .torrent exceeds 250 KiB and will be regenerated to fit constraints."
        )
        meta.max_piece_size = 128
        tracker_url = self._torrent_tracker_url(meta)
        await TorrentCreator.create_torrent(
            meta,
            str(Path(str(meta.path))),
            self.tracker,
            tracker_url=tracker_url,
        )
        return self.tracker

    def _torrent_tracker_url(self, meta: Meta) -> str:
        if not meta.mkbrr:
            return ""
        return str(
            self.tracker_config.get("announce_url", "https://fake.tracker")
        ).strip()

    async def _torrent_upload_file(
        self, meta: Meta
    ) -> dict[str, tuple[str, bytes, str]]:
        torrent_path = (
            release_temp_dir(meta.base_dir, meta.uuid)
            / f"[{self.tracker}].torrent"
        )
        async with aiofiles.open(torrent_path, "rb") as handle:
            torrent_bytes = await handle.read()
        return {
            "file_input": (
                "torrent.torrent",
                torrent_bytes,
                "application/x-bittorrent",
            )
        }

    async def _upload_data(
        self, meta: Meta, audioformat: str
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": await self.get_type(meta),
            "audioformat": audioformat,
            "api_key": str(self.tracker_config.get("api_key", "")).strip(),
            "action": "upload",
            "tmdbid": meta.tmdb,
            "flags[]": await self.get_flags(meta),
            "release_desc": await self.edit_desc(meta),
        }
        await self._apply_media_payload(meta, data)
        self._apply_scene_flag(meta, data)
        await self._apply_tags(meta, data)
        return data

    async def _apply_media_payload(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        if meta.is_disc == "BDMV":
            data["bdinfo"] = await self._read_text_payload(
                meta, "BD_SUMMARY_00.txt"
            )
            data["container_type"] = "m2ts"
            return
        mediainfo = await self._read_text_payload(
            meta, "MEDIAINFO_CLEANPATH.txt"
        )
        data["mediainfo"] = strip_report_by_line(mediainfo)

    @staticmethod
    async def _read_text_payload(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    def _apply_scene_flag(meta: Meta, data: dict[str, Any]) -> None:
        if meta.scene:
            data["censored"] = 1

    async def _apply_tags(self, meta: Meta, data: dict[str, Any]) -> None:
        tags = await self.get_tags(meta)
        if tags != "":
            data["tags"] = ",".join(tags)

    async def _apply_release_group(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        release_group = await self.get_release_group(meta)
        if release_group and release_group not in self.banned_groups:
            data["releasegroup"] = release_group
            return
        data["noreleasegroup"] = 1

    async def _apply_screenshot_policy(
        self, meta: Meta, data: dict[str, Any]
    ) -> None:
        screenshots = self._screenshot_text(meta)
        if not meta.adult_media:
            data["screenshots"] = screenshots
            self._apply_manual_tag_reason(meta, data)
            return
        await self._apply_adult_screenshots(meta, data, screenshots)

    @staticmethod
    def _screenshot_text(meta: Meta) -> str:
        images = meta.image_list if isinstance(meta.image_list, list) else []
        urls = [
            str(image.get("raw_url"))
            for image in images[:4]
            if isinstance(image, dict) and image.get("raw_url")
        ]
        return "\n".join(urls)

    @staticmethod
    def _apply_manual_tag_reason(meta: Meta, data: dict[str, Any]) -> None:
        if meta.ant_user_tags:
            data["flagchangereason"] = "User prompted to add tags manually"

    async def _apply_adult_screenshots(
        self, meta: Meta, data: dict[str, Any], screenshots: str
    ) -> None:
        if meta.unattended and not meta.unattended_confirm:
            data["screenshots"] = ""
            return
        logger.info(
            f"{self.tracker}: [bold red]Adult content detected[/bold red]"
        )
        safe = await prompt_in_thread(
            cli_ui.ask_yes_no, "Are the screenshots safe?", default=False
        )
        if not safe:
            data["screenshots"] = ""
            return
        data["screenshots"] = screenshots
        data["flagchangereason"] = self._adult_flag_reason(meta)

    @staticmethod
    def _adult_flag_reason(meta: Meta) -> str:
        suffix = ". User to add tags manually." if meta.ant_user_tags else ""
        return f"Adult with screens uploaded with {meta.ua_name}{suffix}"

    @staticmethod
    def _user_agent(meta: Meta) -> str:
        version = (
            meta.current_version
            if meta.current_version is not None
            else "github.com/wastaken7/Upload-Assistant"
        )
        return f"{meta.ua_name} {version} ({platform.system()} {platform.release()})"

    async def _debug_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        if "mediainfo" in data:
            path = (
                release_temp_dir(meta.base_dir, meta.uuid)
                / f"{self.tracker}_MEDIAINFO.txt"
            )
            async with aiofiles.open(
                path, "w", newline="", encoding="utf-8"
            ) as handle:
                await handle.write(str(data["mediainfo"]))
            logger.info(
                f"{self.tracker}: [green]Final MediaInfo payload written to {path}[/green]"
            )
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _submit_upload(
        self,
        meta: Meta,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, Any],
    ) -> bool:
        try:
            response = await self._post_upload(
                files, data, {"User-Agent": self._user_agent(meta)}
            )
            return self._handle_upload_response(meta, response)
        except httpx.TimeoutException:
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: ANTHELION request timed out while uploading."
            )
            return False
        except httpx.RequestError as error:
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error: An error occurred while making the request: {error}"
            )
            return False
        except Exception as error:
            self._log_unexpected_upload_error(meta, error)
            return False

    async def _post_upload(
        self,
        files: dict[str, tuple[str, bytes, str]],
        data: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=40) as client:
            return await client.post(
                url=self.api_url, files=files, data=data, headers=headers
            )

    def _handle_upload_response(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        try:
            response_data = self._json_object(response)
        except json.JSONDecodeError:
            meta.tracker_status[self.tracker]["status_message"] = (
                "data error: ANTHELION json decode error, the API is probably down"
            )
            return False
        if response.status_code not in {200, 201}:
            payload = {
                "error": f"ANTHELION returned status code: {response.status_code}",
                "response_content": response.text,
            }
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error - {payload}"
            )
            return False
        if not self._response_is_success(response_data):
            meta.tracker_status[self.tracker]["status_message"] = (
                f"data error: {response_data}"
            )
            return False
        meta.tracker_status[self.tracker]["status_message"] = response_data
        return True

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return (
            cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        )

    @staticmethod
    def _response_is_success(payload: dict[str, Any]) -> bool:
        return (
            "success" in payload
            or str(payload.get("status", "")).lower() == "success"
        )

    def _log_unexpected_upload_error(
        self, meta: Meta, error: Exception
    ) -> None:
        import traceback

        error_type = type(error).__name__
        error_msg = str(error) if str(error) else "No error message"
        logger.info(
            f"{self.tracker}: [bold red]upload exception ({error_type}): {escape(error_msg)}[/bold red]"
        )
        logger.info(
            f"{self.tracker}: [red]Traceback:\n{escape(traceback.format_exc())}[/red]"
        )
        meta.tracker_status[self.tracker]["status_message"] = (
            "data error: double check if it uploaded"
        )

    async def get_audio(self, meta: Meta) -> str:
        """
        Possible values:
        DD+, DD, DTS-HD MA, DTS, TrueHD, FLAC, PCM, OPUS, AAC, MP3, MP2
        """
        audio = meta.audio
        if not audio:
            return "NoAudio"

        audio_map = {
            "DD+": "EAC3",
            "DD": "AC3",
            "DTS-HD MA": "DTSMA",
            "DTS": "DTS",
            "TRUEHD": "TrueHD",
            "FLAC": "FLAC",
            "PCM": "PCM",
            "OPUS": "Opus",
            "AAC": "AAC",
            "MP3": "MP3",
            "MP2": "MP2",
        }
        for key, value in audio_map.items():
            if key in audio.upper():
                return value
        logger.info(
            f"{self.tracker}: Unexpected audio format: {audio}. The format must be one of the following: DD+, DD, DTS-HD MA, DTS, TRUEHD, FLAC, PCM, OPUS, AAC, MP3, MP2"
        )
        logger.info(
            f"{self.tracker}: Audio will be set to 'Other'. [bold red]Correct manually if necessary.[/bold red]"
        )
        return "Other"

    async def edit_desc(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        user_desc = await builder.get_user_description(meta)
        has_user_desc = bool(user_desc.strip())

        return await builder.general_description_generator(
            meta,
            bluray=False,
            book=False,
            custom_header=has_user_desc,
            custom_signature=False,
            description=False,
            game=False,
            logo=has_user_desc,
            mediainfo=False,
            nfo=False,
            screenshots=False,
            tv_info=False,
            ua_signature=False,
            user_description=has_user_desc,
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.valid_mi is False:
            if not meta.unattended:
                logger.info(
                    f"{self.tracker}: [bold red]No unique ID in mediainfo, skipping {self.tracker} upload."
                )
            return False

        return True

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        params = self._search_params(meta)
        headers = {
            "X-API-Key": self.api_key,
            "User-Agent": self._user_agent(meta),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                url=self.api_url, params=params, headers=headers
            )
            response.raise_for_status()
        return self._search_results(meta, response.json())

    @staticmethod
    def _search_params(meta: Meta) -> dict[str, str]:
        params = {"t": "search", "o": "json"}
        if meta.tmdb:
            params["tmdbid"] = str(meta.tmdb)
        elif meta.imdb_id:
            params["imdbid"] = str(meta.imdb)
        return params

    def _search_results(
        self, meta: Meta, payload: Any
    ) -> list[dict[str, Any]]:
        items = self._search_items(payload)
        target_resolution = str(meta.resolution or "").lower()
        results: list[dict[str, Any]] = []
        for item in items:
            entry = self._search_entry(item, target_resolution)
            if entry is not None:
                results.append(entry)
                logger.debug(
                    f"{self.tracker}: [green]Found potential dupe: {escape(str(entry['name']))} ({entry['size']} bytes)"
                )
        return results

    def _search_items(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw_items = payload.get("item", [])
        if not isinstance(raw_items, list):
            logger.warning(
                f"{self.tracker}: Unexpected search response: 'item' is not a list."
            )
            return []
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                items.append(cast(dict[str, Any], item))
            else:
                logger.warning(
                    f"{self.tracker}: Skipping malformed search result."
                )
        return items

    @classmethod
    def _search_entry(
        cls, item: dict[str, Any], target_resolution: str
    ) -> dict[str, Any] | None:
        if not cls._resolution_matches(item, target_resolution):
            return None
        files = cls._valid_files(item.get("files", []))
        return cls._search_entry_payload(item, files)

    @staticmethod
    def _resolution_matches(
        item: dict[str, Any], target_resolution: str
    ) -> bool:
        if not target_resolution:
            return True
        return str(item.get("resolution") or "").lower() == target_resolution

    @classmethod
    def _search_entry_payload(
        cls, item: dict[str, Any], files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "name": cls._largest_file_name(files) or item.get("fileName", ""),
            "files": [str(file.get("name", "")) for file in files],
            "size": cls._safe_int(item.get("size", 0)),
            "link": item.get("guid", ""),
            "flags": item.get("flags", []),
            "file_count": item.get("fileCount", 0),
            "download": str(item.get("link", "")).replace("&amp;", "&"),
        }

    @staticmethod
    def _valid_files(value: Any) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else []
        return [
            cast(dict[str, Any], item)
            for item in items
            if isinstance(item, dict)
        ]

    @classmethod
    def _largest_file_name(cls, files: list[dict[str, Any]]) -> str:
        if not files:
            return ""
        largest = max(
            files, key=lambda item: cls._safe_int(item.get("size", 0))
        )
        return str(largest.get("name", ""))

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except TypeError, ValueError:
            return 0

    async def get_data_from_files(self, meta: Meta) -> list[dict[str, Any]]:
        filename = self._file_search_name(meta)
        if filename is None:
            return []
        api_key = self._configured_api_key()
        if api_key is None:
            return []
        return await self._safe_file_search(filename, api_key)

    async def _safe_file_search(
        self, filename: str, api_key: str
    ) -> list[dict[str, Any]]:
        try:
            response = await self._file_search_response(filename, api_key)
        except httpx.TimeoutException:
            logger.info(
                f"{self.tracker}: [bold red]Request timed out after 5 seconds"
            )
            return []
        except httpx.RequestError as error:
            logger.info(
                f"{self.tracker}: [bold red]Unable to search for existing torrents: {escape(str(error))}"
            )
            return []
        except Exception as error:
            logger.error(
                f"{self.tracker}: [bold red]Unexpected error: {escape(str(error))}"
            )
            return []
        return self._file_search_response_ids(filename, response)

    def _file_search_response_ids(
        self, filename: str, response: httpx.Response
    ) -> list[dict[str, Any]]:
        if response.status_code == 200:
            return self._file_search_ids(filename, response)
        logger.info(
            f"{self.tracker}: [bold red]Failed to search torrents. HTTP Status: {response.status_code}"
        )
        return []

    def _file_search_name(self, meta: Meta) -> str | None:
        if meta.is_disc:
            return None
        files = meta.filelist if isinstance(meta.filelist, list) else []
        if not files:
            logger.debug(
                f"{self.tracker}: [yellow]No files in filelist, skipping file-based search."
            )
            return None
        return Path(str(files[0])).name

    def _configured_api_key(self) -> str | None:
        value = self.tracker_config.get("api_key")
        if not isinstance(value, str) or not value.strip():
            logger.debug(
                f"{self.tracker}: [yellow]API key not configured, skipping file-based search."
            )
            return None
        return value.strip()

    async def _file_search_response(
        self, filename: str, api_key: str
    ) -> httpx.Response:
        headers = {
            "X-API-Key": api_key,
            "User-Agent": f"Upload Assistant/2.4 ({platform.system()} {platform.release()})",
        }
        params = {"t": "search", "filename": filename, "o": "json"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.get(
                url=self.api_url, params=params, headers=headers
            )

    def _file_search_ids(
        self, filename: str, response: httpx.Response
    ) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            logger.info(
                f"{self.tracker}: [bold yellow]Error parsing JSON response from {self.tracker}"
            )
            return []
        matched = self._matched_file_item(filename, payload)
        return [] if matched is None else self._external_ids(matched)

    def _matched_file_item(
        self, filename: str, payload: Any
    ) -> dict[str, Any] | None:
        items = self._file_items(payload)
        direct = self._single_file_item(items)
        if direct is not None:
            return direct
        return self._matched_multiple_file_item(filename, items)

    def _matched_multiple_file_item(
        self, filename: str, items: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not items:
            return None
        matched = next(
            (
                item
                for item in items
                if self._item_matches_filename(item, filename)
            ),
            None,
        )
        if matched is None:
            logger.debug(
                f"{self.tracker}: [yellow]Could not match filename, returning empty list"
            )
        return matched

    @staticmethod
    def _single_file_item(
        items: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        return items[0] if len(items) == 1 else None

    @staticmethod
    def _file_items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        raw_items = payload.get("item", [])
        values = raw_items if isinstance(raw_items, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values
            if isinstance(item, dict)
        ]

    @classmethod
    def _item_matches_filename(
        cls, item: dict[str, Any], filename: str
    ) -> bool:
        return any(
            cls._filename_matches(filename, str(file.get("name", "")))
            for file in cls._valid_files(item.get("files", []))
        )

    @staticmethod
    def _filename_matches(filename: str, candidate: str) -> bool:
        if filename.lower() == candidate.lower():
            return True
        return Path(filename).stem.lower() == Path(candidate).stem.lower()

    @classmethod
    def _external_ids(cls, item: dict[str, Any]) -> list[dict[str, Any]]:
        ids: list[dict[str, Any]] = []
        imdb = cls._imdb_numeric_id(item.get("imdb"))
        if imdb is not None:
            ids.append({"imdb_id": imdb})
        tmdb = cls._tmdb_numeric_id(item.get("tmdb"))
        if tmdb is not None:
            ids.append({"tmdb_id": tmdb})
        return ids

    @staticmethod
    def _imdb_numeric_id(value: Any) -> int | None:
        text = str(value or "")
        if not text.startswith("tt") or not text[2:].isdigit():
            return None
        return int(text[2:])

    @staticmethod
    def _tmdb_numeric_id(value: Any) -> int | None:
        text = str(value or "")
        if not text.isdigit() or int(text) == 0:
            return None
        return int(text)

    async def get_name(self, meta: Meta) -> str:
        return meta.title
