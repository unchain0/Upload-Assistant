# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class MTeam:
    """
    MTEAM Private Torrent Tracker
    """

    auth_type = "other_api"
    tracker = "MTEAM"
    display_name = "MTeam"
    allows_bloated_audio = True
    base_url = "https://kp.m-team.cc"
    api_base_url = "https://api.m-team.cc/api"
    banned_groups = ("FGT",)
    requests_url = f"{api_base_url}/seek/search"
    tracker_urls = ("tracker.m-team.cc", "tra1.m-team.cc", "tracker.m-team.io", "tra1.m-team.io", "tra99.manfuz.co")
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config):
        self.config = config
        self.common = Common(config)
        self.tmdb_manager = TmdbManager(config)
        raw_url = str(self.config["TRACKERS"][self.tracker].get("base_url", "kp.m-team.cc")).strip()
        parsed_raw = urlparse(raw_url)
        clean_netloc = parsed_raw.netloc if parsed_raw.netloc else parsed_raw.path
        self.base_url = urlunparse(("https", clean_netloc, "", "", "", ""))
        self.torrent_url = f"{self.base_url}/detail/"
        self.api_key = self.config["TRACKERS"][self.tracker].get("api_key")
        self.session = httpx.AsyncClient(
            headers={
                "x-api-key": self.api_key,
                "Accept": "*/*",
            },
            timeout=30.0,
        )

    async def get_requests(self, meta: Meta) -> list[dict[str, str]]:
        try:
            response = await self.session.post(self.requests_url, json=self._request_payload(meta), timeout=15)
            response.raise_for_status()
            requests = self._request_entries(response.json(), self.get_category_id(meta))
            self._log_request_entries(requests)
            return requests
        except Exception as error:
            logger.info(f"{self.tracker}: [bold red]Error searching for requests with title {meta.title}: {error}[/bold red]")
            return []

    @staticmethod
    def _request_payload(meta: Meta) -> dict[str, int | bool | str]:
        return {"pageNumber": 1, "pageSize": 10, "keyword": meta.title, "take": False}

    @classmethod
    def _request_entries(cls, payload: Any, category: int) -> list[dict[str, str]]:
        items = cls._nested_data_list(payload)
        return [entry for item in items if (entry := cls._request_entry(item, category)) is not None]

    @staticmethod
    def _nested_data_list(payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        outer = payload.get("data", {})
        if not isinstance(outer, dict):
            return []
        items = outer.get("data", [])
        return items if isinstance(items, list) else []

    @classmethod
    def _request_entry(cls, item: Any, category: int) -> dict[str, str] | None:
        if not isinstance(item, dict) or item.get("category") != category:
            return None
        return {
            "Name": str(item.get("title", "N/A")),
            "Reward": str(item.get("rewardCurrent", "0")),
            "Link": f"{cls.base_url}/seekDetail?id={item.get('id')}",
        }

    def _log_request_entries(self, requests: list[dict[str, str]]) -> None:
        if not requests:
            return
        lines = [f"\n{self.tracker}: [bold yellow]Your upload may fulfill the following request(s), check it out:[/bold yellow]\n"]
        for request in requests:
            lines.extend(self._request_log_lines(request))
        logger.info("\n".join(lines))

    @staticmethod
    def _request_log_lines(request: dict[str, str]) -> list[str]:
        return [
            f"[bold green]Name:[/bold green] {request['Name']}",
            f"[bold green]Reward:[/bold green] {request['Reward']}",
            f"[bold green]Link:[/bold green] {request['Link']}",
            "",
        ]

    async def mediainfo(self, meta: Meta) -> str:
        path = self._mediainfo_path(meta)
        if path is None:
            return ""
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @classmethod
    def _mediainfo_path(cls, meta: Meta) -> Path | None:
        root = release_temp_dir(meta.base_dir, meta.uuid)
        if meta.is_disc == "BDMV":
            return cls._bdmv_mediainfo_path(root)
        return root / "MEDIAINFO_CLEANPATH.txt"

    @staticmethod
    def _bdmv_mediainfo_path(root: Path) -> Path | None:
        return next((path for path in root.iterdir() if path.name.endswith("_FULL.txt")), None)

    def bbcode_to_markdown(self, text: str) -> str:
        specific_img_pattern = r"\[url=[^\]]*\]\[img(?:=[^\]]*)?\](.*?)\[/img\]\[/url\]"
        text = re.sub(specific_img_pattern, r"![](\1)", text, flags=re.IGNORECASE)

        patterns = [
            (r"\[b\](.*?)\[/b\]", r"**\1**"),
            (r"\[i\](.*?)\[/i\]", r"*\1*"),
            (r"\[u\](.*?)\[/u\]", r"<u>\1</u>"),
            (r"\[s\](.*?)\[/s\]", r"~~\1~~"),
            (r"\[img(?:=[^\]]*)?\](.*?)\[/img\]", r"![](\1)"),
            (r"\[url=(.*?)\](.*?)\[/url\]", r"[\2](\1)"),
            (r"\[url\](.*?)\[/url\]", r"<\1>"),
        ]

        for pattern, replacement in patterns:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE | re.DOTALL)

        return text

    async def get_douban_info(self, meta: Meta) -> dict[str, Any]:
        info: dict[str, Any] = {}
        douban_id = meta.douban_id
        if not douban_id:
            return info

        api_url = f"{self.api_base_url}/media/douban/infoV2"

        params: dict[str, bool | int] = {
            "code": douban_id,
            "refresh": False,
        }

        headers: dict[str, str] = {
            "x-api-key": self.api_key,
            "Accept": "*/*",
        }

        try:
            response = await self.session.post(api_url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.info(f"{self.tracker}: Error fetching Douban info: {e}")
            return info

    async def mteam_standard_desc(self, meta: Meta) -> str:
        db_info = await self.get_douban_info(meta)
        douban = self._douban_payload(db_info)
        if douban is not None:
            return self._douban_description(douban)
        logger.info(f"{self.tracker}: Douban information is unavailable, using an alternative English version for the description.")
        return self._fallback_description(meta)

    @staticmethod
    def _douban_payload(db_info: Any) -> dict[str, Any] | None:
        if not isinstance(db_info, dict) or db_info.get("code") != "0":
            return None
        data = db_info.get("data")
        return cast(dict[str, Any], data) if isinstance(data, dict) else None

    @classmethod
    def _douban_description(cls, data: dict[str, Any]) -> str:
        title = str(data.get("title", ""))
        aka = cls._string_list(data.get("aka"))
        translated_names = " / ".join(([title] if title else []) + aka)
        rating = data.get("score", "0")
        rating_count = cls._mapping_value(data.get("rating"), "count", "0")
        subject_id = data.get("subjectId", "")
        lines = [
            f"![]({data.get('coverUrl', '')})",
            "",
            f"**◎译　　名** {translated_names}",
            f"**◎片　　名** {title}",
            f"**◎年　　代** {data.get('year', 'N/A')}",
            f"**◎产　　地** {cls._joined_values(data.get('countries'))}",
            f"**◎类　　别** {cls._joined_values(data.get('genres'))}",
            f"**◎语　　言** {cls._joined_values(data.get('languages'))}",
            f"**◎上映日期** {cls._joined_values(data.get('pubdate'))}",
            f"**◎豆瓣评分** {rating}/10 from {rating_count} users",
            f"**◎豆瓣链接** https://www.douban.com/subject/{subject_id}/",
            f"**◎片　　长** {cls._joined_values(data.get('durations'))}",
            f"**◎导　　演** {cls._people_names(data.get('directors'))}",
            f"**◎主　　演** {cls._people_names(data.get('actors'))}",
            "",
            "**◎简　　介**",
            "",
            f"　　{data.get('intro', '')}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _mapping_value(value: Any, key: str, default: Any) -> Any:
        return value.get(key, default) if isinstance(value, dict) else default

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @classmethod
    def _joined_values(cls, value: Any) -> str:
        return " / ".join(cls._string_list(value))

    @classmethod
    def _people_names(cls, value: Any) -> str:
        people = value if isinstance(value, list) else []
        return " / ".join(str(person.get("name", "")) for person in people if isinstance(person, dict))

    @classmethod
    def _fallback_description(cls, meta: Meta) -> str:
        imdb = cls._imdb_mapping(meta)
        values = cls._fallback_description_values(meta, imdb)
        return "\n".join(cls._fallback_description_lines(values))

    @staticmethod
    def _imdb_mapping(meta: Meta) -> dict[str, Any]:
        return meta.imdb_info if isinstance(meta.imdb_info, dict) else {}

    @classmethod
    def _fallback_description_values(cls, meta: Meta, imdb: dict[str, Any]) -> dict[str, str]:
        return {
            "poster": cls._fallback_poster(meta, imdb),
            "title": cls._display_value(meta.title),
            "year": cls._display_value(meta.year),
            "rating": str(imdb.get("rating", "N/A")),
            "writers": " / ".join(cls._string_list(imdb.get("writers"))),
            "actors": " / ".join(cls._cast_values(meta)),
            "plot": str(imdb.get("plot", meta.overview)),
        }

    @staticmethod
    def _display_value(value: Any) -> str:
        return "N/A" if value is None else str(value)

    @staticmethod
    def _cast_values(meta: Meta) -> list[str]:
        values = meta.cast if meta.cast else meta.tmdb_cast
        return [str(item) for item in values] if isinstance(values, list) else []

    @staticmethod
    def _fallback_description_lines(values: dict[str, str]) -> list[str]:
        return [
            f"![]({values['poster']})",
            "",
            f"**Title**: {values['title']}",
            f"**Year**: {values['year']}",
            f"**IMDb Rating**: {values['rating']}/10",
            f"**Creators**: {values['writers']}",
            f"**Actors**: {values['actors']}",
            "",
            "### Introduction",
            "",
            f"  {values['plot']}",
        ]

    @staticmethod
    def _fallback_poster(meta: Meta, imdb: dict[str, Any]) -> str:
        path = str(meta.tmdb_poster_path or "").strip()
        if path:
            return f"https://image.tmdb.org/t/p/w200{path}"
        return str(imdb.get("cover") or "")

    async def generate_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        meta.mteam_description = await self.mteam_standard_desc(meta)

        description = await builder.general_description_generator(
            meta,
            mediainfo=False,
            nfo=False,
            tv_info=False,
            signature=f"[{meta.ua_signature}](https://github.com/wastaken7/Upload-Assistant)",
        )

        from src.integrations.trackers.bbcode_formatting import BBCODE

        bbcode = BBCODE()
        description = description.strip()
        description = description.replace("[*] ", "• ").replace("[*]", "• ")
        description = self.bbcode_to_markdown(description)
        description = description.replace("[center]", "").replace("[/center]", "")
        description = bbcode.remove_extra_lines(description)

        async with aiofiles.open(f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{self.tracker}]DESCRIPTION.txt", "w", encoding="utf-8") as description_file:
            await description_file.write(description)

        return description

    def get_category_id(self, meta: Meta) -> int:
        if meta.anime:
            return 405
        disc = self._disc_category(meta)
        if disc is not None:
            return disc
        if meta.type == "REMUX" and meta.category == "MOVIE":
            return 439
        return self._resolution_category(meta)

    @staticmethod
    def _disc_category(meta: Meta) -> int | None:
        if meta.is_disc == "BDMV":
            return 438 if meta.category == "TV" else 421
        if meta.is_disc == "DVD":
            return 435 if meta.category == "TV" else 420
        return None

    @staticmethod
    def _resolution_category(meta: Meta) -> int:
        if meta.sd:
            return 403 if meta.category == "TV" else 401
        return 402 if meta.category == "TV" else 419

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not meta.imdb_tt:
            logger.info(f"{self.tracker}: [bold yellow]IMDb ID not found in metadata, skipping upload.[/bold yellow]")
            return False
        if not await self._upscale_policy_passes(meta):
            return False
        if not self._screenshot_policy_passes(meta):
            return False
        return await self._lgbt_policy_passes(meta)

    async def _upscale_policy_passes(self, meta: Meta) -> bool:
        if not self._is_unmarked_upscale(meta):
            return True
        logger.info(f"{self.tracker}: Uploading upscaled files created by converting low-bitrate videos to high-bitrate versions might be prohibited.")
        return await self._confirm_policy(meta)

    @staticmethod
    def _is_unmarked_upscale(meta: Meta) -> bool:
        return "upscale" in str(meta.uuid).casefold() and "upscale" not in str(meta.title).casefold()

    def _screenshot_policy_passes(self, meta: Meta) -> bool:
        if self._screen_count(meta.screens) >= 3:
            return True
        logger.info(f"{self.tracker}: [bold yellow]At least 3 screenshots are required for video uploads. Skipping upload.[/bold yellow]")
        return False

    @staticmethod
    def _screen_count(value: Any) -> int:
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return 0

    async def _lgbt_policy_passes(self, meta: Meta) -> bool:
        if not self._contains_lgbt_content(meta):
            return True
        logger.info(
            f"{self.tracker}: [bold yellow]LGBT content detected. Please ensure the cover photo does not contain depictions of genitalia per tracker rules.[/bold yellow]"
        )
        return await self._confirm_policy(meta)

    @classmethod
    def _contains_lgbt_content(cls, meta: Meta) -> bool:
        keywords = cls._string_list(meta.keywords)
        genres = [item.strip() for item in f"{', '.join(keywords)} {meta.combined_genres}".split(",") if item.strip()]
        lgbt = {"lgbt", "queer", "lgbtq", "lgbtqia", "transgender", "trans", "gay", "lesbian", "bisexual", "pansexual", "non-binary", "homoerotic"}
        return any(item in lgbt for item in genres)

    async def _confirm_policy(self, meta: Meta) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        return await self.common.prompt_user_for_confirmation(f"{self.tracker}: Do you want to continue with the upload? (y/n): ", meta)

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        if not meta.imdb_tt:
            logger.info(f"{self.tracker}: [bold yellow]Cannot perform search on {self.tracker}: IMDb ID not found in metadata.[/bold yellow]")
            return []
        response = await self.session.post(
            f"{self.api_base_url}/torrent/search",
            json=self._search_payload(meta),
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        self._raise_api_error(payload)
        return await self._search_entries(meta, payload)

    def _search_payload(self, meta: Meta) -> dict[str, str | list[str | int]]:
        return {
            "mode": "normal",
            "imdb": meta.imdb_tt,
            "categories": [self.get_category_id(meta)],
            "standards": [self.get_standard(meta)],
        }

    @staticmethod
    def _raise_api_error(payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("code") == "0":
            return
        message = payload.get("message") if isinstance(payload, dict) else None
        raise RuntimeError(f"MTEAM API Error: {message}")

    async def _search_entries(self, meta: Meta, payload: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for torrent in self._nested_data_list(payload):
            entry = await self._search_entry(meta, torrent)
            if entry is not None:
                entries.append(entry)
        return entries

    async def _search_entry(self, meta: Meta, torrent: Any) -> dict[str, Any] | None:
        if not isinstance(torrent, dict) or not torrent.get("id"):
            return None
        torrent_id = int(torrent["id"])
        entry: dict[str, Any] = {
            "name": torrent.get("name"),
            "size": int(torrent.get("size", 0)),
            "link": f"{self.base_url}/detail/{torrent_id}",
            "file_count": torrent.get("file_count", 0),
            "download": f"{self.api_base_url}/torrent/genDlToken?id={torrent_id}",
            "id": torrent_id,
        }
        if meta.is_disc == "BDMV":
            bdinfo = await self.get_dupe_bdinfo(torrent_id)
            if bdinfo:
                entry["bd_info"] = bdinfo
        return entry

    async def get_dupe_bdinfo(self, torrent_id: int) -> str:
        api_url = f"{self.api_base_url}/torrent/detail?id={torrent_id}"

        try:
            response = await self.session.post(api_url, timeout=15)
            response.raise_for_status()

            response_data = response.json()
            bdinfo = response_data.get("data", {}).get("mediainfo")
            if not bdinfo:
                bdinfo = response_data.get("data", {}).get("descr")
            return bdinfo

        except Exception as e:
            logger.info(f"{self.tracker}: Error fetching BDinfo: {e}")
            return ""

    def get_standard(self, meta: Meta) -> int:
        mapping = {"1080p": 1, "1080i": 2, "720p": 3, "2160p": 6, "4320p": 7}
        resolution = str(meta.resolution).casefold()
        if resolution in mapping:
            return mapping[resolution]
        if meta.sd:
            return 5
        logger.info(f"{self.tracker}: Unknown or unsupported resolution '{resolution}', defaulting to 1080p.")
        return 1

    def get_videocodec(self, meta: Meta) -> int:
        mapping = {
            "h264": 1,
            "x264": 1,
            "avc": 1,
            "h.264": 1,
            "h265": 16,
            "h.265": 16,
            "hevc": 16,
            "x265": 16,
            "vc1": 2,
            "vc-1": 2,
            "mpeg2": 4,
            "mpeg-2": 4,
            "xvid": 3,
            "av1": 19,
            "vp8": 21,
            "vp9": 21,
        }
        codec = str(meta.video_codec).casefold()
        if codec in mapping:
            return mapping[codec]
        logger.info(f"{self.tracker}: Unknown or unsupported video codec '{codec}', defaulting to x264.")
        return 1

    def get_audiocodec(self, meta: Meta) -> int:
        codec = str(meta.audio).casefold()
        result = self._known_audio_codec(codec)
        if result is not None:
            return result
        logger.info(f"{self.tracker}: Unknown or unsupported audio codec '{codec}', defaulting to AC3.")
        return 8

    @staticmethod
    def _known_audio_codec(codec: str) -> int | None:
        mappings = (
            (("atmos", "dd+"), 13),
            (("aac",), 6),
            (("dd+",), 12),
            (("dd ",), 8),
            (("dts-hd",), 11),
            (("dts",), 3),
            (("truehd",), 9),
        )
        return next((value for tokens, value in mappings if all(token in codec for token in tokens)), None)

    async def fetch_data(self, meta: Meta) -> dict[str, Any]:
        """
        https://test2.m-team.cc/api/swagger-ui/index.html#/種子/createOredit
        """
        return {
            # "torrent": 0,
            # "offer": 0,
            "name": await self.get_name(meta),
            "smallDescr": self.common.get_small_description(meta),
            "descr": await self.generate_description(meta),
            "category": self.get_category_id(meta),
            # "source": 0,
            # "medium": 0,
            "standard": self.get_standard(meta),
            "videoCodec": self.get_videocodec(meta),
            "audioCodec": self.get_audiocodec(meta),
            # "team": 0,
            # "processing": 0,
            # "countries": "",
            "imdb": meta.imdb_info.get("imdbID", ""),
            "douban": meta.douban_id,
            # "dmmCode": "",
            # "cids": "",
            # "aids": "",
            "anonymous": bool(meta.anon or self.config["TRACKERS"][self.tracker].get("anon", False)),
            # "labels": 0,
            # "tags": "",
            # "file": "",
            # "nfo": "",
            "mediainfo": await self.mediainfo(meta),
            "mediaInfoAnalysisResult": True,
            # "labelsNew": ""
        }

    async def upload(self, meta: Meta) -> bool:
        data = await self.fetch_data(meta)
        status = meta.tracker_status.setdefault(self.tracker, {})
        if meta.debug:
            return await self._debug_upload(meta, data, status)
        return await self._upload_release(meta, data, status)

    async def _debug_upload(self, meta: Meta, data: dict[str, Any], status: dict[str, Any]) -> bool:
        logger.info(f"{self.tracker}: [cyan]{self.tracker} Request Data:")
        logger.info(Redaction.redact_private_info(data))
        status["status_message"] = "Debug mode enabled, not uploading"
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _upload_release(self, meta: Meta, data: dict[str, Any], status: dict[str, Any]) -> bool:
        try:
            response = await self._submit_upload(meta, data)
            return await self._handle_upload_response(meta, status, response)
        except httpx.HTTPStatusError as error:
            status["status_message"] = f"data error: HTTP {error.response.status_code} - {error.response.text}"
            return False
        except httpx.TimeoutException:
            status["status_message"] = f"data error: Request timed out after {self.session.timeout.write} seconds"
            return False
        except httpx.RequestError as error:
            status["status_message"] = self._request_error_message(error)
            return False
        except Exception as error:
            status["status_message"] = f"data error: It may have uploaded, go check. Error: {error}.\nResponse: No response received"
            return False

    async def _submit_upload(self, meta: Meta, data: dict[str, Any]) -> httpx.Response:
        await self.common.create_torrent_for_upload(meta, self.tracker, "[kp.m-team.cc] M-Team - TP")
        files = {"file": ("upload.torrent", await self._torrent_bytes(meta), "application/x-bittorrent")}
        response = await self.session.post(
            f"{self.api_base_url}/torrent/createOredit",
            data=data,
            files=files,
            headers=dict(self.session.headers),
            timeout=90,
        )
        response.raise_for_status()
        return response

    async def _torrent_bytes(self, meta: Meta) -> bytes:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    async def _handle_upload_response(self, meta: Meta, status: dict[str, Any], response: httpx.Response) -> bool:
        payload = response.json()
        data = cast(dict[str, Any], payload) if isinstance(payload, dict) else {}
        if data.get("message") != "SUCCESS":
            status["status_message"] = f"data error: {data.get('message', 'Unknown API error.')}"
            return False
        torrent_id = self._upload_torrent_id(data)
        if torrent_id is None:
            status["status_message"] = "data error: Unknown API error."
            return False
        status["torrent_id"] = str(torrent_id)
        status["status_message"] = data.get("message")
        return await self._download_uploaded_torrent(meta, status, torrent_id)

    @staticmethod
    def _upload_torrent_id(payload: dict[str, Any]) -> int | None:
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return int(data["id"])

    async def _download_uploaded_torrent(self, meta: Meta, status: dict[str, Any], torrent_id: int) -> bool:
        response = await self.session.post(f"{self.api_base_url}/torrent/genDlToken?id={torrent_id}")
        payload = response.json()
        download_url = payload.get("data") if isinstance(payload, dict) else None
        if not download_url:
            logger.info(f"{self.tracker}: Failed to get download URL from API response.")
            status["status_message"] = "Failed to get download URL from API response"
            return False
        await self.common.download_tracker_torrent(meta, self.tracker, headers=dict(self.session.headers), downurl=str(download_url))
        return True

    @staticmethod
    def _request_error_message(error: httpx.RequestError) -> str:
        response_text = getattr(getattr(error, "response", None), "text", "No response received")
        return f"data error: Unable to upload. Error: {error}.\nResponse: {response_text}"

    async def get_name(self, meta: Meta) -> str:
        name = str(meta.name)
        for transform in self._name_transforms():
            name = transform(name)
        return re.sub(r"\.(mkv|mp4|avi|ts)$", "", name, flags=re.IGNORECASE)

    @staticmethod
    def _name_transforms() -> tuple[Any, ...]:
        return (
            MTeam._normalize_bluray,
            MTeam._normalize_webdl,
            MTeam._normalize_dolby_vision,
            MTeam._normalize_hdr,
            MTeam._normalize_dolby_audio,
            MTeam._normalize_dtsx,
            MTeam._normalize_truehd,
            MTeam._normalize_hfr,
        )

    @staticmethod
    def _normalize_bluray(name: str) -> str:
        return re.sub(r"\bblu[-_]?ray\b", "BluRay", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_webdl(name: str) -> str:
        return re.sub(r"\bweb[-_]?dl\b", "WEB-DL", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_dolby_vision(name: str) -> str:
        return re.sub(r"\bdovi\b", "DV", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_hdr(name: str) -> str:
        def replacement(match: re.Match[str]) -> str:
            return f"{match.group(1).upper()}{match.group(2) or ''}{match.group(3) or ''}"

        return re.sub(r"\b(hdr|hlg)(10)?(\+)?\b", replacement, name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_dolby_audio(name: str) -> str:
        name = re.sub(r"\b(eac[-_]?3|dd\+)(?![a-zA-Z0-9])", "DDP", name, flags=re.IGNORECASE)
        return re.sub(r"\bac[-_]?3(?![a-zA-Z0-9])", "DD", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_dtsx(name: str) -> str:
        return re.sub(r"\bdts[-_\s]?x\b", "DTS:X", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_truehd(name: str) -> str:
        return re.sub(r"\btrue[-_]?hd\b", "TrueHD", name, flags=re.IGNORECASE)

    @staticmethod
    def _normalize_hfr(name: str) -> str:
        name = re.sub(r"\b(50|60|120)fps\b", "HFR", name, flags=re.IGNORECASE)
        return re.sub(r"\bHFR\b([-.\s_]+HFR)+", "HFR", name, flags=re.IGNORECASE)
