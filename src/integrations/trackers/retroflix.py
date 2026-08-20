# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import base64
import datetime
import json
import re
from typing import Any, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder


class RetroFlix:
    """
    RTF Private Torrent Tracker
    """

    base_url = "https://retroflix.club"

    auth_type = "other_api"
    tracker = "RETROFLIX"
    display_name = "RetroFlix"
    allows_bloated_audio = True
    source_flag = "sunshine"
    banned_groups: tuple[str, ...] = ()
    upload_url = f"{base_url}/api/upload"
    search_url = f"{base_url}/api/torrent"
    torrent_url = f"{base_url}/browse/t/"
    forum_link = f"{base_url}/forums.php?action=viewtopic&topicid=3619"
    tracker_urls = ("peer.retroflix",)
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config=config)

    async def upload(self, meta: Meta) -> bool:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        await DescriptionBuilder(self.tracker, self.config).general_description_generator(
            meta,
            mediainfo=False,
            nfo=False,
            signature=self.forum_link,
        )
        json_data = await self._upload_payload(meta)
        if meta.debug:
            return await self._debug_upload(meta, json_data)
        return await self._upload_release(meta, json_data)

    async def _upload_payload(self, meta: Meta) -> dict[str, Any]:
        media_info = await self._media_info_payload(meta)
        data: dict[str, Any] = {
            "name": await self.get_name(meta),
            "description": "",
            "mediaInfo": media_info,
            "nfo": "",
            "url": self._imdb_url(meta),
            "descr": "",
            "poster": meta.artwork_url,
            "type": "401" if meta.category == "MOVIE" else "402",
            "screenshots": self._screenshot_urls(meta),
            "isAnonymous": self._tracker_config().get("anon", False),
        }
        data["file"] = await self._torrent_payload(meta)
        return data

    async def _media_info_payload(self, meta: Meta) -> str:
        if meta.bdinfo:
            return await self._text_file(meta, "BD_SUMMARY_00.txt")
        mediainfo = await self._text_file(meta, "MEDIAINFO.txt")
        return re.sub(r"(\d+)\s+(\d+)", r"\1,\2", mediainfo)

    @staticmethod
    def _screenshot_urls(meta: Meta) -> list[str]:
        images = meta.image_list if isinstance(meta.image_list, list) else []
        return [str(image["raw_url"]) for image in images if isinstance(image, dict) and image.get("raw_url") is not None]

    @staticmethod
    def _imdb_url(meta: Meta) -> str:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        value = str(imdb.get("imdb_url") or "")
        return f"{value}/" if value else ""

    async def _torrent_payload(self, meta: Meta) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        async with aiofiles.open(path, "rb") as handle:
            return base64.b64encode(await handle.read()).decode("utf-8")

    @staticmethod
    async def _text_file(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    def _tracker_config(self) -> dict[str, Any]:
        trackers = self.config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(self.tracker, {})
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}

    def _upload_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": str(self._tracker_config().get("api_key", "")).strip(),
        }

    async def _debug_upload(self, meta: Meta, json_data: dict[str, Any]) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        debug_data = json_data.copy()
        if debug_data.get("file"):
            debug_data["file"] = f"{str(debug_data['file'])[:10]}..."
        logger.info(Redaction.redact_private_info(debug_data))
        meta.tracker_status[self.tracker]["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(
            meta,
            f"{self.tracker}_DEBUG",
            f"{self.tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _upload_release(self, meta: Meta, json_data: dict[str, Any]) -> bool:
        try:
            response = await self._post_upload(json_data)
            return await self._handle_upload_response(meta, response)
        except httpx.TimeoutException:
            meta.tracker_status[self.tracker]["status_message"] = "data error: RETROFLIX request timed out while uploading."
            return False
        except httpx.RequestError as error:
            meta.tracker_status[self.tracker]["status_message"] = f"data error: An error occurred while making the request: {error}"
            return False
        except Exception as error:
            meta.tracker_status[self.tracker]["status_message"] = f"data error - Unexpected error: {error}"
            return False

    async def _post_upload(self, json_data: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=40.0) as client:
            return await client.post(url=self.upload_url, json=json_data, headers=self._upload_headers())

    async def _handle_upload_response(self, meta: Meta, response: httpx.Response) -> bool:
        if response.status_code == 201:
            return await self._handle_created_upload(meta, response)
        message = self._error_status_message(response)
        meta.tracker_status[self.tracker]["status_message"] = message
        if response.status_code not in {400, 403, 409, 413, 422}:
            logger.info(f"{self.tracker}: [bold red]Unexpected response: {message.removeprefix('Unexpected response: ')}")
        return False

    async def _handle_created_upload(self, meta: Meta, response: httpx.Response) -> bool:
        payload = self._json_object(response)
        if payload.get("error", False):
            error_msg = payload.get("message", "Unknown error occurred")
            meta.tracker_status[self.tracker]["status_message"] = f"Upload error: {error_msg}"
            return False
        torrent_id = self._torrent_id(payload)
        if torrent_id is None:
            meta.tracker_status[self.tracker]["status_message"] = f"Error parsing response: {response.text}: missing key torrent.id"
            return False
        meta.tracker_status[self.tracker]["status_message"] = payload
        meta.tracker_status[self.tracker]["torrent_id"] = torrent_id
        await self.common.create_torrent_ready_to_seed(
            meta,
            self.tracker,
            self.source_flag,
            self._tracker_config().get("announce_url"),
            f"{self.base_url}/browse/t/{torrent_id}",
        )
        return True

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _torrent_id(payload: dict[str, Any]) -> Any | None:
        torrent = payload.get("torrent")
        return torrent.get("id") if isinstance(torrent, dict) else None

    def _error_status_message(self, response: httpx.Response) -> str:
        defaults = {
            400: ("Bad request", "Bad request or torrent file"),
            403: ("Permission denied", "You are not allowed to upload"),
            409: ("Duplicate", "Torrent already exists"),
            413: ("File size error", "Torrent file is too big or has too many files"),
            422: ("Upload rejected", "Upload rejected based on rules"),
        }
        if response.status_code in defaults:
            prefix, fallback = defaults[response.status_code]
            return f"{prefix}: {self._response_message(response, fallback)}"
        return f"Unexpected response: {self._response_message(response, f'HTTP {response.status_code}: {response.text[:200]}')}"

    @classmethod
    def _response_message(cls, response: httpx.Response, fallback: str) -> str:
        try:
            payload = cls._json_object(response)
        except ValueError, json.JSONDecodeError:
            return fallback
        return str(payload.get("message", fallback))

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not await self.common.check_and_confirm_adult_media_upload(meta, self.tracker):
            return False
        latest_year, latest_date = self._latest_release_age(meta)
        if meta.category == "MOVIE":
            return self._movie_age_policy(meta, latest_year)
        if meta.category == "TV":
            return self._tv_age_policy(meta, latest_year, latest_date)
        return self._year_policy(meta, latest_year)

    @classmethod
    def _latest_release_age(cls, meta: Meta) -> tuple[int | None, datetime.date | None]:
        years = cls._candidate_years(meta)
        latest_date = cls._latest_episode_date(meta, years)
        if latest_date is not None:
            years.append(latest_date.year)
        fallback = cls._numeric_year(meta.year)
        return (max(years) if years else fallback), latest_date

    @classmethod
    def _candidate_years(cls, meta: Meta) -> list[int]:
        years: list[int] = []
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        cls._append_numeric_year(years, imdb.get("end_year"))
        cls._append_numeric_year(years, meta.tvdb_episode_year)
        return years

    @staticmethod
    def _numeric_year(value: Any) -> int | None:
        text = str(value or "")
        return int(text) if text.isdigit() else None

    @classmethod
    def _append_numeric_year(cls, years: list[int], value: Any) -> None:
        year = cls._numeric_year(value)
        if year is not None:
            years.append(year)

    @classmethod
    def _latest_episode_date(cls, meta: Meta, years: list[int]) -> datetime.date | None:
        episodes = cls._episode_values(meta)
        dates: list[datetime.date] = []
        for episode in episodes:
            cls._collect_episode_age(episode, dates, years)
        return max(dates) if dates else None

    @staticmethod
    def _episode_values(meta: Meta) -> list[dict[str, Any]]:
        data = meta.tvdb_episode_data if isinstance(meta.tvdb_episode_data, dict) else {}
        episodes = data.get("episodes", [])
        values = episodes if isinstance(episodes, list) else []
        return [cast(dict[str, Any], item) for item in values if isinstance(item, dict)]

    @classmethod
    def _collect_episode_age(cls, episode: dict[str, Any], dates: list[datetime.date], years: list[int]) -> None:
        aired = str(episode.get("aired", ""))
        if not aired:
            return
        parsed = cls._parse_date(aired)
        if parsed is not None:
            dates.append(parsed)
            return
        cls._append_numeric_year(years, aired.split("-", 1)[0])

    @staticmethod
    def _parse_date(value: Any) -> datetime.date | None:
        try:
            return datetime.datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=datetime.UTC).date()
        except ValueError, TypeError:
            return None

    def _movie_age_policy(self, meta: Meta, latest_year: int | None) -> bool:
        if meta.release_date:
            release_date = self._parse_date(meta.release_date)
            if release_date is not None:
                return self._date_policy(meta, release_date)
            fallback_year = self._numeric_year(str(meta.release_date).split("-", 1)[0])
            return self._year_policy(meta, fallback_year)
        return self._year_policy(meta, latest_year)

    def _tv_age_policy(self, meta: Meta, latest_year: int | None, latest_date: datetime.date | None) -> bool:
        if latest_date is not None:
            return self._date_policy(meta, latest_date)
        return self._year_policy(meta, latest_year)

    def _date_policy(self, meta: Meta, release_date: datetime.date) -> bool:
        if release_date <= self._ten_year_cutoff():
            return True
        self._log_age_rejection(meta)
        return False

    def _year_policy(self, meta: Meta, year: int | None) -> bool:
        if year is None or datetime.datetime.now(datetime.UTC).date().year - year >= 10:
            return True
        self._log_age_rejection(meta)
        return False

    @staticmethod
    def _ten_year_cutoff() -> datetime.date:
        today = datetime.datetime.now(datetime.UTC).date()
        return today - datetime.timedelta(days=365 * 10 + 3)

    def _log_age_rejection(self, meta: Meta) -> None:
        if not meta.unattended:
            logger.info(f"{self.tracker}: [red]Content must be older than 10 Years to upload at RETROFLIX")

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self.search_url, params=self._search_params(meta), headers=self._search_headers())
            response.raise_for_status()
        payload = response.json()
        values = payload if isinstance(payload, list) else []
        return [self._search_entry(item) for item in values if isinstance(item, dict)]

    def _search_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "Authorization": str(self._tracker_config().get("api_key", "")).strip(),
        }

    @staticmethod
    def _search_params(meta: Meta) -> dict[str, str]:
        params = {"includingDead": "1"}
        imdb_id = str(meta.imdb_id or "")
        if imdb_id and imdb_id != "0":
            params["imdbId"] = imdb_id if imdb_id.startswith("tt") else f"tt{imdb_id}"
        else:
            params["search"] = str(meta.title).replace(":", "").replace("'", "").replace(",", "")
        return params

    @classmethod
    def _search_entry(cls, entry: dict[str, Any]) -> dict[str, Any]:
        name = str(entry.get("name", ""))
        return {
            "name": name,
            "size": entry.get("size", 0),
            "files": name,
            "link": str(entry.get("url", "")),
            "download": cls._download_url(entry),
        }

    @classmethod
    def _download_url(cls, entry: dict[str, Any]) -> str:
        torrent_id = entry.get("id") or cls._torrent_id_from_url(entry.get("url"))
        if torrent_id:
            return f"{cls.base_url}/api/torrent/{torrent_id}/download"
        return str(entry.get("url", ""))

    @staticmethod
    def _torrent_id_from_url(value: Any) -> str:
        match = re.search(r"/browse/t/(\d+)", str(value or ""))
        return match.group(1) if match else ""

    async def api_test(self, meta: Meta) -> bool | None:
        """Test if the stored API key is valid.

        RETROFLIX API keys expire weekly, so this method validates the current key
        and generates a new one if needed.

        Args:
            meta: Metadata dictionary containing base directory path.

        Returns:
            True if API key is valid, None if key generation was attempted.
        """
        headers: dict[str, Any] = {
            "accept": "application/json",
            "Authorization": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/test", headers=headers)

                if response.status_code != 200:
                    logger.info(f"{self.tracker}: [bold red]Your API key is incorrect SO generating a new one")
                    await self.generate_new_api(meta)
                    return None
                return True
        except httpx.RequestError as e:
            logger.info(f"{self.tracker}: [bold red]Error testing API: {e!s}")
            await self.generate_new_api(meta)
            return None
        except Exception as e:
            logger.error(f"{self.tracker}: [bold red]Unexpected error testing API: {e!s}")
            await self.generate_new_api(meta)
            return None

    async def generate_new_api(self, meta: Meta) -> bool | None:
        try:
            response = await self._login_response()
            if response.status_code != 201:
                logger.info(f"{self.tracker}: [bold red]Error getting new API key: {response.status_code}, please check username and password in the config.")
                return None
            token = self._response_token(response)
            if not token:
                logger.info(f"{self.tracker}: [bold red]API response does not contain a token.")
                return None
            return await self._save_new_api(meta, token)
        except httpx.RequestError as error:
            logger.info(f"{self.tracker}: [bold red]An error occurred while requesting the API: {error!s}")
            return None
        except Exception as error:
            logger.info(f"{self.tracker}: [bold red]An unexpected error occurred: {error!s}")
            return None

    async def _login_response(self) -> httpx.Response:
        payload = {
            "username": self._tracker_config().get("username", ""),
            "password": self._tracker_config().get("password", ""),
        }
        async with httpx.AsyncClient() as client:
            return await client.post(f"{self.base_url}/api/login", headers={"accept": "application/json"}, json=payload)

    @classmethod
    def _response_token(cls, response: httpx.Response) -> str:
        payload = cls._json_object(response)
        return str(payload.get("token") or "")

    async def _save_new_api(self, meta: Meta, token: str) -> bool | None:
        self._tracker_config()["api_key"] = token
        path = self._config_path(meta)
        try:
            config_data = await self._read_config(path)
            updated = self._updated_config(config_data, token)
            if updated is None:
                logger.info(f"{self.tracker}: [bold red]Failed to update RETROFLIX api_key in config file.")
                return None
            await self._write_config(path, updated)
            logger.info(f"{self.tracker}: [bold green]API Key successfully saved to {path}")
            return True
        except (OSError, ValueError) as error:
            logger.info(f"{self.tracker}: [bold red]Failed to update config file: {error!s}")
            return None

    @staticmethod
    def _config_path(meta: Meta) -> str:
        base_dir = meta.base_dir if meta.base_dir is not None else "."
        return f"{base_dir}/data/config.py"

    @staticmethod
    async def _read_config(path: str) -> str:
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    def _updated_config(config_data: str, token: str) -> str | None:
        pattern = r"""(['"]RETROFLIX['"]\s*:\s*{.*?['"]api_key['"]\s*:\s*)(['"])[^'"]*(['"])"""
        updated, replacements = re.subn(pattern, rf"\1\2{token}\3", config_data, count=1, flags=re.DOTALL)
        return updated if replacements else None

    @staticmethod
    async def _write_config(path: str, content: str) -> None:
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(content)

    async def get_name(self, meta: Meta) -> str:
        return meta.name
