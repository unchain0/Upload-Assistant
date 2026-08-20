# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import cli_ui
import httpx
from bs4 import BeautifulSoup
from unidecode import unidecode

from src.domain_models.processing import UploadError
from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger, prompt_in_thread
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator

Config = dict[str, Any]


class ToTheGlory:
    """
    TTG Private Torrent Tracker
    """

    base_url = "https://totheglory.im"

    auth_type = "cookies"
    tracker = "TOTHEGLORY"
    display_name = "ToTheGlory"
    allows_bloated_audio = True
    source_flag = "TTG"
    signature = None
    banned_groups = ("",)
    supported_categories = ("TV", "MOVIE")

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.username = str(config["TRACKERS"][self.tracker].get("username", "")).strip()
        self.password = str(config["TRACKERS"][self.tracker].get("password", "")).strip()
        self.passid = str(config["TRACKERS"][self.tracker].get("login_question", "0")).strip()
        self.passan = str(config["TRACKERS"][self.tracker].get("login_answer", "")).strip()
        self.uid = str(config["TRACKERS"][self.tracker].get("user_id", "")).strip()
        self.passkey = str(config["TRACKERS"][self.tracker].get("announce_url", "")).strip().split("/")[-1]
        self.cookie_validator = CookieValidator(config)

    async def get_name(self, meta: Meta) -> str:
        ttg_name = meta.name

        remove_list = ["Dubbed", "Dual-Audio"]
        for each in remove_list:
            ttg_name = ttg_name.replace(each, "")
        ttg_name = ttg_name.replace("PQ10", "HDR")
        return ttg_name.replace(".", "{@}")

    async def get_type_id(self, meta: Meta) -> int:
        base_type = self._base_type_id(meta)
        documentary = self._documentary_type_id(meta)
        if documentary is not None:
            base_type = documentary
        if self._is_animation(meta):
            base_type = 58
        if meta.resolution == "2160p":
            return 109 if meta.is_disc == "BDMV" else 108
        return base_type

    @classmethod
    def _base_type_id(cls, meta: Meta) -> int:
        if meta.category == "MOVIE":
            return cls._movie_type_id(meta)
        if meta.category == "TV":
            return cls._tv_type_id(meta)
        return 0

    @staticmethod
    def _movie_type_id(meta: Meta) -> int:
        if meta.is_disc == "BDMV":
            return 54
        if str(meta.resolution).startswith("1080"):
            return 53
        if str(meta.resolution).startswith("720"):
            return 52
        return 0

    @classmethod
    def _tv_type_id(cls, meta: Meta) -> int:
        language = str(meta.original_language).upper()
        if meta.tv_pack == 1:
            return cls._tv_pack_type_id(language)
        return cls._tv_episode_type_id(meta.resolution, language)

    @staticmethod
    def _tv_pack_type_id(language: str) -> int:
        mapping = {"KR": 99, "KO": 99, "JA": 88, "JP": 88, "ZH": 90, "CN": 90, "CMN": 90}
        return mapping.get(language, 87)

    @classmethod
    def _tv_episode_type_id(cls, resolution: str, language: str) -> int:
        language_type = cls._tv_language_type(language)
        if language_type is not None:
            return language_type
        return cls._tv_resolution_type(resolution, cls._is_chinese(language))

    @staticmethod
    def _tv_language_type(language: str) -> int | None:
        return {"KR": 75, "KO": 75, "JA": 73, "JP": 73}.get(language)

    @staticmethod
    def _is_chinese(language: str) -> bool:
        return language in {"ZH", "CN", "CMN"}

    @staticmethod
    def _tv_resolution_type(resolution: str, chinese: bool) -> int:
        if str(resolution).startswith("1080"):
            return 75 if chinese else 70
        if str(resolution).startswith("720"):
            return 76 if chinese else 69
        return 0

    @classmethod
    def _documentary_type_id(cls, meta: Meta) -> int | None:
        if not cls._is_documentary(meta):
            return None
        if meta.is_disc == "BDMV":
            return 64
        if str(meta.resolution).startswith("1080"):
            return 63
        if str(meta.resolution).startswith("720"):
            return 62
        return 0

    @staticmethod
    def _normalized_metadata_text(value: Any) -> str:
        return str(value).lower().replace(" ", "").replace("-", "")

    @classmethod
    def _is_documentary(cls, meta: Meta) -> bool:
        return "documentary" in cls._normalized_metadata_text(meta.genres) or "documentary" in cls._normalized_metadata_text(meta.keywords)

    @classmethod
    def _is_animation(cls, meta: Meta) -> bool:
        if meta.sd != 0:
            return False
        return "animation" in cls._normalized_metadata_text(meta.genres) or "animation" in cls._normalized_metadata_text(meta.keywords)

    async def upload(self, meta: Meta) -> bool | None:
        common = Common(config=self.config)
        await common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        await self.edit_desc(meta)
        data, files, torrent_path = await self._upload_parts(meta)
        if meta.debug:
            return await self._debug_upload(meta, common, data)
        response = await self._post_upload(meta, data, files)
        return await self._handle_upload_response(meta, response, torrent_path)

    async def _upload_parts(self, meta: Meta) -> tuple[dict[str, Any], dict[str, tuple[str, bytes] | tuple[str, bytes, str]], str]:
        ttg_name = await self.get_name(meta)
        description = await self._read_text(meta, f"[{self.tracker}]DESCRIPTION.txt")
        torrent_path = str(release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent")
        torrent_bytes = await self._read_bytes(Path(torrent_path))
        media_info = await self._media_info_text(meta)
        files: dict[str, tuple[str, bytes] | tuple[str, bytes, str]] = {
            "file": (f"{self._torrent_file_name(meta)}.torrent", torrent_bytes, "application/x-bittorrent"),
            "nfo": ("torrent.nfo", media_info.encode("utf-8")),
        }
        return self._upload_data(meta, ttg_name, description), files, torrent_path

    async def _media_info_text(self, meta: Meta) -> str:
        filename = "BD_SUMMARY_00.txt" if meta.bdinfo else "MEDIAINFO.txt"
        return await self._read_text(meta, filename)

    @staticmethod
    async def _read_bytes(path: Path) -> bytes:
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    @staticmethod
    async def _read_text(meta: Meta, filename: str) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @staticmethod
    def _torrent_file_name(meta: Meta) -> str:
        filelist = meta.filelist if isinstance(meta.filelist, list) else []
        source = meta.video if len(filelist) == 1 else meta.path
        return unidecode(Path(str(source)).name.replace(" ", "."))

    def _upload_data(self, meta: Meta, name: str, description: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "MAX_FILE_SIZE": "4000000",
            "team": "",
            "hr": "no",
            "name": name,
            "type": 0,
            "descr": description.rstrip(),
            "anonymity": self._anonymous_value(meta),
            "nodistr": "no",
        }
        if meta.imdb_id:
            data["imdb_c"] = f"tt{meta.imdb}"
        return data

    def _anonymous_value(self, meta: Meta) -> str:
        configured = bool(self.config["TRACKERS"][self.tracker].get("anon", False))
        return "no" if meta.anon == 0 and not configured else "yes"

    async def _debug_upload(self, meta: Meta, common: Common, data: dict[str, Any]) -> bool:
        data["type"] = await self.get_type_id(meta)
        logger.debug(f"{self.base_url}/takeupload.php")
        logger.debug(Redaction.redact_private_info(data))
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "Debug mode enabled, not uploading."
        await common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _post_upload(self, meta: Meta, data: dict[str, Any], files: dict[str, Any]) -> httpx.Response:
        data["type"] = await self.get_type_id(meta)
        cookiefile = Path(meta.base_dir) / "data" / "cookies" / f"{self.tracker}.json"
        raw_cookies = self.cookie_validator._load_cookies_dict_secure(str(cookiefile.resolve()))  # type: ignore[reportPrivateUsage]
        cookies = {name: str(value.get("value", "")) for name, value in raw_cookies.items()}
        async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=60.0) as client:
            return await client.post(url=f"{self.base_url}/takeupload.php", data=data, files=files)

    async def _handle_upload_response(self, meta: Meta, response: httpx.Response, torrent_path: str) -> bool:
        if not str(response.url).startswith(f"{self.base_url}/details.php?id="):
            logger.info(f"{self.tracker}: \n\n")
            raise UploadError(f"Upload to {self.tracker} Failed: result URL {response.url} ({response.status_code}) was not expected", "red")
        torrent_id = self._torrent_id_from_url(str(response.url))
        if torrent_id is None:
            raise UploadError(f"Upload to {self.tracker} succeeded but torrent id missing from URL {response.url}", "red")
        meta.tracker_status.setdefault(self.tracker, {})["status_message"] = str(response.url)
        await self.download_new_torrent(torrent_id, torrent_path)
        return True

    @staticmethod
    def _torrent_id_from_url(url: str) -> str | None:
        match = re.search(r"(?:^|[?&])id=(\d+)", urlparse(url).query)
        return match.group(1) if match else None

    async def search_existing(self, meta: Meta) -> list[str]:
        cookiefile = self._cookie_path(meta, ".json")
        if not cookiefile.exists():
            logger.info(f"{self.tracker}: [bold red]Cookie file not found: {self.tracker}.json")
            return []
        cookies = self._cookie_values(cookiefile)
        search_url = self._search_url(meta)
        async with httpx.AsyncClient(cookies=cookies, timeout=10.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
        await asyncio.sleep(0.5)
        return self._search_release_names(response.text)

    @staticmethod
    def _cookie_path(meta: Meta, suffix: str) -> Path:
        return (Path(meta.base_dir) / "data" / "cookies" / f"TOTHEGLORY{suffix}").resolve()

    def _cookie_values(self, cookiefile: Path) -> dict[str, str]:
        raw = self.cookie_validator._load_cookies_dict_secure(str(cookiefile))  # type: ignore[reportPrivateUsage]
        return {name: str(value.get("value", "")) for name, value in raw.items()}

    def _search_url(self, meta: Meta) -> str:
        imdb = f"imdb{meta.imdb}" if meta.imdb_id else ""
        return f"{self.base_url}/browse.php?search_field= {imdb} {self._search_resolution(meta)}"

    @staticmethod
    def _search_resolution(meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return f"{meta.resolution} Blu-ray"
        if meta.is_disc == "DVD":
            return "DVD"
        return str(meta.resolution)

    @classmethod
    def _search_release_names(cls, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        return [name for link in soup.find_all("a", href=True) if (name := cls._release_name_from_link(link))]

    @staticmethod
    def _release_name_from_link(link: Any) -> str:
        href = link.get("href")
        if not isinstance(href, str) or not href.startswith("/t/"):
            return ""
        match = re.search(r"(<b>)(<font.*>)?(.*)<br", str(link))
        return match.group(3) if match else ""

    async def validate_credentials(self, meta: Meta) -> bool:
        cookiefile = self._cookie_path(meta, ".pkl")
        if not cookiefile.exists():
            await self.login(str(cookiefile), meta)
        if await self.validate_cookies(meta, str(cookiefile)):
            return True
        logger.error(f"{self.tracker}: [red]Failed to validate cookies. Please confirm that the site is up and your passkey is valid.")
        return await self._maybe_recreate_session(meta, cookiefile)

    async def _maybe_recreate_session(self, meta: Meta, cookiefile: Path) -> bool:
        if meta.unattended and not meta.unattended_confirm:
            return False
        recreate = cli_ui.ask_yes_no("Log in again and create new session?")
        if recreate is not True:
            return False
        if cookiefile.exists():
            cookiefile.unlink()
        await self.login(str(cookiefile), meta)
        return await self.validate_cookies(meta, str(cookiefile))

    async def validate_cookies(self, meta: Meta, cookiefile: str) -> bool:  # noqa: ARG002
        path = Path(cookiefile)
        if not path.exists():
            return False
        cookies = self._cookie_values(path)
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url=self.base_url)
        logger.debug(f"{self.tracker}: [cyan]Cookies:")
        logger.debug(response.url)
        return '<a href="/logout.php">Logout</a>' in response.text

    async def login(self, cookiefile: str, meta: Meta | None = None) -> None:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.post(f"{self.base_url}/takelogin.php", data=self._login_data())
            await asyncio.sleep(0.5)
            response = await self._maybe_complete_two_factor(client, response, meta)
            await self._save_login_result(client, response, cookiefile)

    def _login_data(self) -> dict[str, Any]:
        return {"username": self.username, "password": self.password, "passid": self.passid, "passan": self.passan}

    async def _maybe_complete_two_factor(self, client: httpx.AsyncClient, response: httpx.Response, meta: Meta | None) -> httpx.Response:
        if not str(response.url).endswith("2fa.php"):
            return response
        auth_token = self._required_authenticity_token(response.text)
        if self._skip_two_factor(meta):
            logger.error(f"{self.tracker}: [red]Unattended mode: 2FA required. Skipping login.[/red]")
            return response
        return await self._submit_two_factor(client, auth_token)

    def _required_authenticity_token(self, html: str) -> str:
        token = self._authenticity_token(html)
        if not token:
            raise UploadError(f"Missing authenticity token during {self.tracker} login", "red")
        return token

    @staticmethod
    def _skip_two_factor(meta: Meta | None) -> bool:
        return meta is not None and meta.unattended and not meta.unattended_confirm

    async def _submit_two_factor(self, client: httpx.AsyncClient, auth_token: str) -> httpx.Response:
        data = await self._two_factor_data(auth_token)
        response = await client.post(f"{self.base_url}/take2fa.php", data=data)
        await asyncio.sleep(0.5)
        return response

    @staticmethod
    def _authenticity_token(html: str) -> str:
        field = BeautifulSoup(html, "html.parser").find("input", {"name": "authenticity_token"})
        if field is None:
            return ""
        value = field.get("value")
        return str(value) if value is not None else ""

    async def _two_factor_data(self, auth_token: str) -> dict[str, Any]:
        otp = await prompt_in_thread(cli_ui.ask_string, f"{self.tracker} 2FA Code:", default="") or ""
        return {"otp": otp, "authenticity_token": auth_token, "uid": self.uid}

    async def _save_login_result(self, client: httpx.AsyncClient, response: httpx.Response, cookiefile: str) -> None:
        if str(response.url).endswith("my.php"):
            logger.info(f"{self.tracker}: [green]Successfully logged into {self.tracker}")
            self.cookie_validator._save_cookies_secure(client.cookies.jar, cookiefile)  # type: ignore[reportPrivateUsage]
            return
        logger.info(f"{self.tracker}: [bold red]Something went wrong")
        await asyncio.sleep(1)
        logger.info(response.text)
        logger.info(response.url)

    async def edit_desc(self, meta: Meta) -> None:
        from src.domain_models.release_description import base_description
        from src.integrations.trackers.bbcode_formatting import BBCODE

        parts = await self._description_preamble(meta)
        parts.extend(await self._technical_description(meta))
        parts.append(self._formatted_base_description(base_description(meta), BBCODE()))
        parts.extend(self._screenshot_description(meta))
        parts.extend(self._signature_description())
        await self._write_description(meta, "".join(parts))

    async def _description_preamble(self, meta: Meta) -> list[str]:
        parts: list[str] = []
        if meta.imdb_id:
            ptgen = await Common(config=self.config).ptgen(meta)
            if ptgen.strip():
                parts.append(ptgen)
        web_note = self._web_source_note(meta)
        if web_note:
            parts.append(web_note)
        return parts

    @staticmethod
    def _web_source_note(meta: Meta) -> str:
        if meta.type != "WEBDL" or not meta.service_longname or meta.description:
            return ""
        return (
            f"[center][b][color=#ff00ff][size=3]{meta.service_longname}的无损REMUX片源，没有转码/"  # noqa: RUF001
            f"This release is sourced from {meta.service_longname} and is not transcoded, just remuxed from the direct "
            f"{meta.service_longname} stream[/size][/color][/b][/center]"
        )

    async def _technical_description(self, meta: Meta) -> list[str]:
        discs = self._disc_entries(meta)
        if discs:
            return [text for disc in discs if (text := self._disc_description(disc))]
        media_info = await self._read_text(meta, "MEDIAINFO_CLEANPATH.txt")
        return [f"[quote=MediaInfo]{media_info}[/quote]", "\n"]

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], meta.discs) if isinstance(meta.discs, list) else []

    @staticmethod
    def _disc_description(disc: dict[str, Any]) -> str:
        disc_type = disc.get("type")
        if disc_type == "BDMV":
            return f"[quote={disc.get('name', 'BDINFO')}]{disc.get('summary', '')}[/quote]\n\n"
        if disc_type == "DVD":
            return (
                f"{disc.get('name', '')}:\n"
                f"[quote={Path(str(disc.get('vob', ''))).name}]{disc.get('vob_mi', '')}[/quote] "
                f"[quote={Path(str(disc.get('ifo', ''))).name}]{disc.get('ifo_mi', '')}[/quote]\n\n"
            )
        return ""

    @staticmethod
    def _formatted_base_description(base: str, bbcode: Any) -> str:
        value = bbcode.convert_code_to_quote(base)
        value = bbcode.convert_spoiler_to_hide(value)
        value = bbcode.convert_comparison_to_centered(value, 1000)
        return re.sub(r"(\[img=\d+)]", "[img]", value, flags=re.IGNORECASE)

    @classmethod
    def _screenshot_description(cls, meta: Meta) -> list[str]:
        links = cls._screenshot_links(meta)
        if not links:
            return []
        return ["[center]", *links, "[/center]"]

    @classmethod
    def _screenshot_links(cls, meta: Meta) -> list[str]:
        images = meta.image_list if isinstance(meta.image_list, list) else []
        limit = cls._screen_limit(meta.screens, len(images))
        return [link for image in images[:limit] if (link := cls._screenshot_link(image))]

    @staticmethod
    def _screen_limit(value: Any, available: int) -> int:
        try:
            return min(int(value or 0), available)
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _screenshot_link(image: Any) -> str:
        if not isinstance(image, dict):
            return ""
        web_url = image.get("web_url")
        img_url = image.get("img_url")
        if not web_url or not img_url:
            return ""
        return f"[url={web_url}][img]{img_url}[/img][/url]"

    def _signature_description(self) -> list[str]:
        return [] if self.signature is None else ["\n\n", self.signature]

    async def _write_description(self, meta: Meta, description: str) -> None:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(path, "w", encoding="utf-8") as handle:
            await handle.write(description)

    async def download_new_torrent(self, id: str, torrent_path: str) -> None:
        download_url = f"{self.base_url}/dl/{id}/{self.passkey}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url=download_url)
        if r.status_code == 200:
            async with aiofiles.open(torrent_path, "wb") as tor:
                await tor.write(r.content)
        else:
            logger.info(f"{self.tracker}: [red]There was an issue downloading the new .torrent from {self.tracker}")
            logger.info(r.text)
