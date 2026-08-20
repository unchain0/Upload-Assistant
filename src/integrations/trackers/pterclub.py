# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import glob
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup
from unidecode import unidecode

from src.domain_models.processing import LoginError, UploadError
from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir, screenshots_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator, find_cookie_file

Config = dict[str, Any]


class PTerClub:
    """
    PTERCLUB (PT之友俱乐部) is a CHINESE Private Torrent Tracker for HD MUSIC VIDEOS / MOVIES / TV / ANIME
    """

    base_url = "https://pterclub.net"

    auth_type = "cookies"
    tracker = "PTERCLUB"
    display_name = "PTerClub"
    allows_bloated_audio = True
    source_flag = "PTER"
    ptgen_retry = 3
    signature: str | None = None
    banned_groups: tuple[str, ...] = ("",)
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.passkey = str(config["TRACKERS"][self.tracker].get("passkey", "")).strip()
        self.username = str(config["TRACKERS"][self.tracker].get("username", "")).strip()
        self.password = str(config["TRACKERS"][self.tracker].get("password", "")).strip()
        self.rehost_images = bool(config["TRACKERS"][self.tracker].get("img_rehost", False))
        self.ptgen_api = str(config["TRACKERS"][self.tracker].get("ptgen_api", "")).strip()
        self.cookie_validator = CookieValidator(config)
        self.common = Common(config=config)

    def _extract_auth_token(self, text: str, pattern: str) -> str:
        match = re.search(pattern, text)
        if match is None:
            raise LoginError("Unable to locate auth token for Pterimg.")
        return match.group(1)

    async def validate_credentials(self, meta: Meta) -> bool:
        vcookie = await self.validate_cookies(meta)
        if vcookie is not True:
            logger.error(f"{self.tracker}: [red]Failed to validate cookies. Please confirm that the site is up and your passkey is valid.")
            return False
        return True

    async def validate_cookies(self, meta: Meta) -> bool:
        cookiefile = Path(find_cookie_file(meta.base_dir, self.tracker, self.config))
        if not cookiefile.exists():
            logger.info(f"{self.tracker}: [bold red]Missing Cookie File. (data/cookies/PTERCLUB.txt)")
            return False
        cookies = await self.common.parse_cookie_file(str(cookiefile))
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url=self.base_url)
        return '<a href="#" data-url="logout.php" id="logout-confirm">' in response.text

    async def search_existing(self, meta: Meta) -> list[str] | bool:
        cookiefile = Path(find_cookie_file(meta.base_dir, self.tracker, self.config))
        if not cookiefile.exists():
            logger.info(f"{self.tracker}: [bold red]Missing Cookie File. (data/cookies/PTERCLUB.txt)")
            return False
        cookies = await self.common.parse_cookie_file(str(cookiefile))
        response = await self._search_response(meta, cookies)
        return self._search_release_names(response.text)

    async def _search_response(self, meta: Meta, cookies: dict[str, str]) -> httpx.Response:
        search_url = self._search_url(meta)
        async with httpx.AsyncClient(cookies=cookies, timeout=10.0, follow_redirects=True) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            return response

    def _search_url(self, meta: Meta) -> str:
        imdb = f"tt{meta.imdb}" if meta.imdb_id else ""
        source = self._medium_id(meta)
        return f"{self.base_url}/torrents.php?search={imdb}&incldead=0&search_mode=0&source{source}=1"

    @staticmethod
    def _search_release_names(html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("table.torrents > tr:has(table.torrentname)")
        return [name for row in rows if (name := PTerClub._row_release_name(row))]

    @staticmethod
    def _row_release_name(row: Any) -> str:
        link = row.select_one('a[href^="details.php?id="]')
        if link is None:
            return ""
        return str(link.attrs.get("title", ""))

    async def get_type_category_id(self, meta: Meta) -> str:
        special = self._special_category(meta)
        return special if special is not None else self._base_category(meta.category)

    @staticmethod
    def _base_category(category: str) -> str:
        return {"MOVIE": "401", "TV": "404"}.get(category, "EXIT")

    @classmethod
    def _special_category(cls, meta: Meta) -> str | None:
        text = cls._category_text(meta)
        if "animation" in text:
            return "403"
        if "documentary" in text:
            return "402"
        return None

    @staticmethod
    def _category_text(meta: Meta) -> str:
        values = [*meta.genres, *meta.keywords]
        return ", ".join(str(value) for value in values).lower()

    async def get_area_id(self, meta: Meta) -> int:

        area_id = 8
        area_map = {  # To do
            "中国大陆": 1,
            "中国香港": 2,
            "中国台湾": 3,
            "美国": 4,
            "日本": 6,
            "韩国": 5,
            "印度": 7,
            "法国": 4,
            "意大利": 4,
            "德国": 4,
            "西班牙": 4,
            "葡萄牙": 4,
            "英国": 4,
            "阿根廷": 8,
            "澳大利亚": 4,
            "比利时": 4,
            "巴西": 8,
            "加拿大": 4,
            "瑞士": 4,
            "智利": 8,
        }
        ptgen = meta.ptgen
        regions_value = ptgen.get("region", [])
        regions = cast(list[str], regions_value) if isinstance(regions_value, list) else []
        for area in area_map:
            if area in regions:
                return area_map[area]
        return area_id

    async def get_type_medium_id(self, meta: Meta) -> str:
        return self._medium_id(meta)

    @classmethod
    def _medium_id(cls, meta: Meta) -> str:
        disc = cls._disc_medium_id(meta)
        if disc is not None:
            return disc
        return cls._file_medium_id(meta.type)

    @staticmethod
    def _disc_medium_id(meta: Meta) -> str | None:
        if meta.is_disc in {"BDMV", "HD DVD"}:
            return "1" if meta.resolution == "2160p" else "2"
        if meta.is_disc == "DVD":
            return "7"
        return None

    @staticmethod
    def _file_medium_id(release_type: str) -> str:
        return {
            "HDTV": "4",
            "ENCODE": "6",
            "WEBRIP": "6",
            "REMUX": "3",
            "WEBDL": "5",
        }.get(release_type, "EXIT")

    async def edit_desc(self, meta: Meta) -> None:
        from src.domain_models.release_description import base_description
        from src.integrations.trackers.bbcode_formatting import BBCODE

        parts = await self._description_parts(meta, base_description(meta), BBCODE())
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write("".join(parts))

    async def _description_parts(self, meta: Meta, base: str, bbcode: Any) -> list[str]:
        parts = await self._description_preamble(meta)
        parts.extend(await self._technical_description(meta))
        parts.append(self._format_description(base, bbcode))
        parts.extend(await self._description_screenshots(meta))
        if self.signature is not None:
            parts.extend(("\n\n", self.signature))
        return parts

    async def _description_preamble(self, meta: Meta) -> list[str]:
        if not meta.imdb_id:
            return []
        ptgen = await self.common.ptgen(meta, self.ptgen_api, self.ptgen_retry)
        return [ptgen] if ptgen.strip() else []

    async def _technical_description(self, meta: Meta) -> list[str]:
        discs = self._disc_entries(meta)
        if discs:
            return [block for disc in discs if (block := self._disc_block(disc))]
        media_info = await self._read_temp_text(meta, "MEDIAINFO_CLEANPATH.txt")
        return [f"[hide=mediainfo]{media_info}[/hide]", "\n"]

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], meta.discs) if isinstance(meta.discs, list) else []

    @staticmethod
    def _disc_block(disc: dict[str, Any]) -> str:
        if disc.get("type") == "BDMV":
            return f"[hide=BDInfo]{disc.get('summary', '')}[/hide]\n\n"
        if disc.get("type") == "DVD":
            return f"{disc.get('name', '')}:\n[hide=mediainfo]{disc.get('vob_mi', '')}[/hide] [hide=mediainfo]{disc.get('ifo_mi', '')}[/hide]\n\n"
        return ""

    @staticmethod
    async def _read_temp_text(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    def _format_description(base: str, bbcode: Any) -> str:
        desc = bbcode.convert_code_to_quote(base)
        desc = bbcode.convert_spoiler_to_hide(desc)
        desc = bbcode.convert_comparison_to_centered(desc, 1000)
        return re.sub(r"(\[img=\d+)]", "[img]", desc, flags=re.IGNORECASE)

    async def _description_screenshots(self, meta: Meta) -> list[str]:
        images = await self._description_image_list(meta)
        links = [link for image in images[: self._screen_limit(meta.screens)] if (link := self._image_link(image))]
        return [] if not links else ["[center]", *links, "[/center]"]

    async def _description_image_list(self, meta: Meta) -> list[dict[str, str]]:
        if self.rehost_images:
            logger.info(f"{self.tracker}: [green]Rehosting Images...")
            return await self.pterimg_upload(meta)
        values = meta.image_list if isinstance(meta.image_list, list) else []
        return [cast(dict[str, str], item) for item in values if isinstance(item, dict)]

    @staticmethod
    def _screen_limit(value: Any) -> int:
        try:
            return max(0, int(value))
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _image_link(image: dict[str, str]) -> str:
        web_url = image.get("web_url")
        img_url = image.get("img_url")
        if not web_url or not img_url:
            return ""
        return f"[url={web_url}][img]{img_url}[/img][/url]"

    async def get_auth_token(self, meta: Meta) -> str:
        cookiefile = self._pterimg_cookie_file(meta)
        cookies = self._saved_cookie_values(cookiefile)
        saved = await self._saved_auth_token(cookies)
        if saved:
            return saved
        if not cookiefile.exists():
            logger.info(f"{self.tracker}: [yellow]Pterimg Cookies not found. Creating new session.")
        return await self._pterimg_login(cookiefile, cookies)

    def _pterimg_cookie_file(self, meta: Meta) -> Path:
        cookie_dir = Path(meta.base_dir) / "data" / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        return Path(find_cookie_file(meta.base_dir, "Pterimg", self.config))

    def _saved_cookie_values(self, cookiefile: Path) -> dict[str, str]:
        if not cookiefile.exists():
            return {}
        raw = self.cookie_validator._load_cookies_dict_secure(str(cookiefile))  # pyright: ignore[reportPrivateUsage]
        return {name: str(data.get("value", "")) for name, data in raw.items()}

    async def _saved_auth_token(self, cookies: dict[str, str]) -> str:
        if not cookies:
            return ""
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
            response = await client.get("https://s3.pterclub.com")
        if not await self.validate_login(response):
            return ""
        return self._extract_auth_token(response.text, r'auth_token.*?"(\w+)"')

    async def _pterimg_login(self, cookiefile: Path, cookies: dict[str, str]) -> str:
        data: dict[str, Any] = {"login-subject": self.username, "password": self.password, "keep-login": 1}
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
            response = await client.get("https://s3.pterclub.com")
            data["auth_token"] = self._extract_auth_token(response.text, r'auth_token.*?"(\w+)"')
            login_response = await client.post(url="https://s3.pterclub.com/login", data=data)
            if not login_response.is_success:
                raise LoginError("Failed to login to Pterimg.")
            auth_token = self._extract_auth_token(login_response.text, r'auth_token = *?"(\w+)"')
            self.cookie_validator._save_cookies_secure(client.cookies.jar, str(cookiefile))  # pyright: ignore[reportPrivateUsage]
            return auth_token

    async def validate_login(self, response: httpx.Response) -> bool:
        return response.text.find("""<a href="https://s3.pterclub.com/logout/?""") != -1

    async def pterimg_upload(self, meta: Meta) -> list[dict[str, str]]:
        images = self._screenshot_paths(meta)
        data: dict[str, Any] = {"type": "file", "action": "upload", "nsfw": 0, "auth_token": await self.get_auth_token(meta)}
        cookiefile = Path(find_cookie_file(meta.base_dir, "Pterimg", self.config))
        if not cookiefile.exists():
            return []
        cookies = self._saved_cookie_values(cookiefile)
        async with httpx.AsyncClient(cookies=cookies, timeout=60.0, follow_redirects=True) as client:
            return await self._upload_pterimg_images(client, images, data)

    @staticmethod
    def _screenshot_paths(meta: Meta) -> list[str]:
        pattern = f"{glob.escape(meta.filename)}-*.png"
        return [str(path) for path in screenshots_dir(meta.base_dir, meta.uuid).glob(pattern)]

    async def _upload_pterimg_images(self, client: httpx.AsyncClient, images: list[str], data: dict[str, Any]) -> list[dict[str, str]]:
        uploaded: list[dict[str, str]] = []
        for image_path in images:
            result = await self._upload_pterimg_image(client, image_path, data)
            if result is not None:
                uploaded.append(result)
        return uploaded

    async def _upload_pterimg_image(self, client: httpx.AsyncClient, image_path: str, data: dict[str, Any]) -> dict[str, str] | None:
        file_bytes = await self._read_image_bytes(image_path)
        response = await client.post(
            "https://s3.pterclub.com/json",
            data=data,
            files={"source": (Path(image_path).name, file_bytes)},
        )
        payload = self._pterimg_payload(response)
        message = self._pterimg_message(response, payload)
        if not response.is_success:
            if message in {"重复上传", "Duplicated upload"}:
                return None
            raise RuntimeError(f"HTTP {response.status_code}, reason: {message}")
        return self._pterimg_image_result(payload)

    @staticmethod
    async def _read_image_bytes(image_path: str) -> bytes:
        async with aiofiles.open(image_path, "rb") as handle:
            return await handle.read()

    @staticmethod
    def _pterimg_payload(response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except json.decoder.JSONDecodeError:
            return None
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else None

    @classmethod
    def _pterimg_message(cls, response: httpx.Response, payload: dict[str, Any] | None) -> str:
        payload_message = cls._pterimg_payload_message(payload)
        return payload_message if payload_message else cls._response_fallback_message(response)

    @staticmethod
    def _pterimg_payload_message(payload: dict[str, Any] | None) -> str:
        if payload is None:
            return ""
        error = payload.get("error", {})
        if not isinstance(error, dict):
            return ""
        message = error.get("message")
        return str(message) if message else ""

    @staticmethod
    def _response_fallback_message(response: httpx.Response) -> str:
        reason = (response.reason_phrase or "").strip()
        return reason if reason else (response.text or "").strip()

    @staticmethod
    def _pterimg_image_result(payload: dict[str, Any] | None) -> dict[str, str]:
        if payload is None:
            raise ValueError("Unexpected response payload while uploading to Pterimg.")
        image = payload.get("image")
        if not isinstance(image, dict):
            raise ValueError("Missing image data in Pterimg response.")
        image_url = image.get("url")
        if not isinstance(image_url, str):
            raise ValueError("Missing image url in Pterimg response.")
        return {"web_url": image_url, "img_url": image_url}

    async def get_name(self, meta: Meta) -> str:
        pter_name = meta.name

        remove_list = ["Dubbed", "Dual-Audio"]
        for each in remove_list:
            pter_name = pter_name.replace(each, "")

        pter_name = pter_name.replace(meta.aka, "")
        pter_name = pter_name.replace("PQ10", "HDR")

        if meta.type == "WEBDL" and meta.has_encode_settings is True:
            pter_name = pter_name.replace("H.264", "x264")

        return pter_name

    async def is_zhongzi(self, meta: Meta) -> str | None:
        if meta.is_disc == "BDMV":
            return "yes" if self._bdmv_has_chinese_subtitles(meta) else None
        return "yes" if self._mediainfo_has_chinese_subtitles(meta) else None

    @staticmethod
    def _bdmv_has_chinese_subtitles(meta: Meta) -> bool:
        bdinfo = meta.bdinfo if isinstance(meta.bdinfo, dict) else {}
        subtitles = bdinfo.get("subtitles", [])
        values = subtitles if isinstance(subtitles, list) else []
        return "Chinese" in values

    @classmethod
    def _mediainfo_has_chinese_subtitles(cls, meta: Meta) -> bool:
        return any(track.get("@type") == "Text" and track.get("Language") == "zh" for track in cls._media_tracks(meta))

    @classmethod
    def _media_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        media = cls._media_mapping(meta)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _media_mapping(meta: Meta) -> dict[str, Any]:
        if not isinstance(meta.mediainfo, dict):
            return {}
        media = meta.mediainfo.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]

    async def upload(self, meta: Meta) -> bool:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        await self._ensure_description(meta)
        data, files, torrent_path = await self._upload_parts(meta)
        if meta.debug:
            return await self._debug_upload(meta, data)
        return await self._submit_upload(meta, data, files, torrent_path)

    async def _ensure_description(self, meta: Meta) -> None:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        if not path.exists():
            await self.edit_desc(meta)

    async def _upload_parts(self, meta: Meta) -> tuple[dict[str, Any], dict[str, tuple[str, bytes, str]], Path]:
        description = await self._read_temp_text(meta, f"[{self.tracker}]DESCRIPTION.txt")
        torrent_path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        torrent_bytes = await self._read_binary(torrent_path)
        file_name = self._torrent_file_name(meta)
        files = {"file": (f"{file_name}.torrent", torrent_bytes, "application/x-bittorent")}
        data = await self._upload_data(meta, description)
        return data, files, torrent_path

    @staticmethod
    async def _read_binary(path: Path) -> bytes:
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    @staticmethod
    def _torrent_file_name(meta: Meta) -> str:
        filelist = meta.filelist if isinstance(meta.filelist, list) else []
        source = meta.video if len(filelist) == 1 else meta.path
        return unidecode(Path(str(source)).name.replace(" ", "."))

    async def _upload_data(self, meta: Meta, description: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": await self.get_name(meta),
            "small_descr": self._small_description(meta),
            "descr": description,
            "type": await self.get_type_category_id(meta),
            "source_sel": await self.get_type_medium_id(meta),
            "team_sel": await self.get_area_id(meta),
            "uplver": self._anonymous(meta),
            "zhongzi": await self.is_zhongzi(meta),
        }
        if meta.personalrelease:
            data["pr"] = "yes"
        return data

    @classmethod
    def _small_description(cls, meta: Meta) -> str:
        ptgen = meta.ptgen if isinstance(meta.ptgen, dict) else {}
        titles = cls._string_list(ptgen.get("trans_title", []))
        if not cls._has_translated_titles(titles):
            return str(meta.title)
        genre = cls._first_string(ptgen.get("genre", []))
        return cls._formatted_small_description(titles, genre)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [str(item) for item in values]

    @staticmethod
    def _has_translated_titles(titles: list[str]) -> bool:
        return bool(titles and titles != [""])

    @classmethod
    def _first_string(cls, value: Any) -> str:
        values = cls._string_list(value)
        return values[0] if values else ""

    @staticmethod
    def _formatted_small_description(titles: list[str], genre: str) -> str:
        value = " / ".join(titles) + " / | 类别:" + genre
        return value.replace("/ |", "|")

    def _anonymous(self, meta: Meta) -> str:
        configured = bool(self.config["TRACKERS"][self.tracker].get("anon", False))
        return "no" if meta.anon == 0 and not configured else "yes"

    async def _debug_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.debug(f"{self.base_url}/takeupload.php")
        logger.debug(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = "Debug mode enabled, not uploading."
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _submit_upload(
        self,
        meta: Meta,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
        torrent_path: Path,
    ) -> bool:
        cookiefile = Path(find_cookie_file(meta.base_dir, self.tracker, self.config))
        if not cookiefile.exists():
            return False
        cookies = await self.common.parse_cookie_file(str(cookiefile))
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
            response = await client.post(url=f"{self.base_url}/takeupload.php", data=data, files=files)
        return await self._handle_upload_response(meta, response, torrent_path)

    async def _handle_upload_response(self, meta: Meta, response: httpx.Response, torrent_path: Path) -> bool:
        if not str(response.url).startswith(f"{self.base_url}/details.php?id="):
            logger.info(meta.tracker_status.get(self.tracker, {}))
            logger.info(f"{self.tracker}: \n\n")
            raise UploadError(f"Upload to Pter Failed: result URL {response.url} ({response.status_code}) was not expected", "red")
        torrent_id = self._torrent_id(str(response.url))
        await self.download_new_torrent(torrent_id, str(torrent_path))
        status = meta.tracker_status[self.tracker]
        status["status_message"] = str(response.url).replace("&uploaded=1", "")
        status["torrent_id"] = torrent_id
        return True

    @staticmethod
    def _torrent_id(url: str) -> str:
        match = re.search(r"(?:^|[?&])id=(\d+)", urlparse(url).query)
        if match is None:
            raise UploadError("Upload succeeded but torrent id was not present in the redirect URL.", "red")
        return match.group(1)

    async def download_new_torrent(self, id: str, torrent_path: str) -> None:
        download_url = f"{self.base_url}/download.php?id={id}&passkey={self.passkey}"
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url=download_url)
        if r.status_code == 200:
            async with aiofiles.open(torrent_path, "wb") as tor:
                await tor.write(r.content)
        else:
            logger.info(f"{self.tracker}: [red]There was an issue downloading the new .torrent from pter")
            logger.info(r.text)
