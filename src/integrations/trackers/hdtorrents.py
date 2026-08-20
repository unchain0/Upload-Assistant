# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class HDTorrents:
    """
    HD-Torrents (HDT) is a Private Torrent Tracker for HD MOVIES / TV / MUSIC / 3X
    """

    auth_type = "cookies"
    tracker = "HDTORRENTS"
    display_name = "HDTorrents"
    allows_bloated_audio = True
    source_flag = "hd-torrents.org"
    auth_token: str | None = None
    banned_groups = ()
    base_url = "https://hd-torrents.org"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("https://hdts-announce.ru",)
    secret_token: str = ""

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)

        tracker_config = self.config.get("TRACKERS", {}).get(self.tracker, {})
        tracker_config_dict = cast(dict[str, Any], tracker_config) if isinstance(tracker_config, dict) else {}
        url_from_config = str(tracker_config_dict.get("url", "")).strip()
        parsed_url = urlparse(url_from_config)
        self.config_url = parsed_url.netloc or parsed_url.path.strip("/")
        self.base_url = f"https://{self.config_url}" if self.config_url else type(self).base_url

        self.torrent_url = f"{self.base_url}/details.php?id="
        self.announce_url = str(tracker_config_dict.get("announce_url", ""))
        self.session = httpx.AsyncClient(
            # HD-Torrents is very strict about User-Agent, so we use a common browser UA to avoid being blocked
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=60.0,
        )

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if not cookie_jar:
            return False
        configured_host = self._configured_host()
        if configured_host not in self._cookie_hosts(cookie_jar):
            logger.error(f"{self.tracker}: Cookie domain does not match the configured base URL ({configured_host}). Please export cookies from {self.base_url}.")
            return False
        self.session.cookies = cookie_jar
        return True

    def _configured_host(self) -> str:
        return (urlparse(self.base_url).hostname or self.config_url).lower().lstrip(".")

    @staticmethod
    def _cookie_hosts(cookie_jar: Any) -> set[str]:
        return {str(cookie.domain).lower().lstrip(".") for cookie in cookie_jar if getattr(cookie, "domain", None)}

    async def get_category_id(self, meta: Meta) -> int:
        category = str(meta.category)
        if category == "MOVIE":
            return self._movie_category(meta)
        if category == "TV":
            return self._tv_category(meta)
        return 0

    @classmethod
    def _movie_category(cls, meta: Meta) -> int:
        disc = cls._disc_category(meta, uhd=70, hd=1)
        if disc:
            return disc
        if meta.type == "REMUX":
            return 71 if cls._is_uhd_remux(meta) else 2
        return cls._resolution_category(meta, {"2160p": 64, "1080p": 5, "1080i": 5, "720p": 3})

    @classmethod
    def _tv_category(cls, meta: Meta) -> int:
        disc = cls._disc_category(meta, uhd=72, hd=59)
        if disc:
            return disc
        if meta.type == "REMUX":
            return 73 if cls._is_uhd_remux(meta) else 60
        return cls._resolution_category(meta, {"2160p": 65, "1080p": 30, "1080i": 30, "720p": 38})

    @staticmethod
    def _disc_category(meta: Meta, *, uhd: int, hd: int) -> int:
        if meta.is_disc != "BDMV" and meta.type != "DISC":
            return 0
        if meta.resolution == "2160p":
            return uhd
        return hd if meta.resolution in {"1080p", "1080i"} else 0

    @staticmethod
    def _is_uhd_remux(meta: Meta) -> bool:
        return meta.uhd == "UHD" and meta.resolution == "2160p"

    @staticmethod
    def _resolution_category(meta: Meta, mapping: dict[str, int]) -> int:
        return mapping.get(str(meta.resolution), 0)

    async def get_name(self, meta: Meta) -> str:
        hdt_name = meta.name
        audio = meta.audio
        hdr = meta.hdr
        if meta.type in ("WEBDL", "WEBRIP", "ENCODE"):
            hdt_name = hdt_name.replace(audio, audio.replace(" ", "", 1))
        if "DV" in hdr:
            hdt_name = hdt_name.replace(" DV ", " DoVi ")
        if "BluRay REMUX" in hdt_name:
            hdt_name = hdt_name.replace("BluRay REMUX", "Blu-ray Remux")

        hdt_name = " ".join(hdt_name.split())
        hdt_name = re.sub(r"[^0-9a-zA-ZÀ-ÿ. &+'\-\[\]]+", "", hdt_name)
        return hdt_name.replace(":", "").replace("..", " ").replace("  ", " ")

    async def edit_desc(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        return await builder.general_description_generator(
            meta,
            book=False,
            game=False,
            nfo=False,
            signature=f"[right][url=https://github.com/wastaken7/Upload-Assistant][size=1]{meta.ua_signature}[/size][/url][/right]",
        )

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.resolution not in ["2160p", "1080p", "1080i", "720p"]:
            logger.info(f"{self.tracker}: The resolution must be at least 720p, skipping the upload...")
            return False
        return True

    async def search_existing(self, meta: Meta) -> list[dict[str, str | None]]:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if cookie_jar:
            self.session.cookies = cookie_jar
        response = await self._search_response(meta)
        if await self._handle_login_redirect(meta, response):
            return []
        if not self._update_secret_token(meta, response.text):
            return []
        return self._search_results(response.text)

    async def _search_response(self, meta: Meta) -> httpx.Response:
        params = await self._search_params(meta)
        response = await self.session.get(f"{self.base_url}/torrents.php?", params=params, follow_redirects=True)
        response.raise_for_status()
        return response

    async def _search_params(self, meta: Meta) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "csrfToken": self.secret_token,
            "category[]": await self.get_category_id(meta),
        }
        params.update(self._search_identity(meta))
        return params

    @staticmethod
    def _search_identity(meta: Meta) -> dict[str, str | int]:
        if meta.imdb_id or 0 != 0:
            return {"search": meta.imdb_tt, "active": "0", "options": "2"}
        return {"search": meta.title, "options": "3"}

    async def _handle_login_redirect(self, meta: Meta, response: httpx.Response) -> bool:
        if "login.php" not in str(response.url) and "login.php" not in response.text:
            return False
        await self.cookie_validator.handle_validation_failure(meta, self.tracker, response.text)
        meta.skipping = self.tracker
        return True

    def _update_secret_token(self, meta: Meta, html: str) -> bool:
        token_match = re.search(r'name="csrfToken" value="([^"]+)"', html)
        if token_match:
            type(self).secret_token = token_match.group(1)
            return True
        logger.info(f"{self.tracker}: [bold red]Failed to find auth token on page.[/bold red]")
        meta.skipping = self.tracker
        return False

    def _search_results(self, html: str) -> list[dict[str, str | None]]:
        soup = BeautifulSoup(html, "html.parser")
        return [result for row in soup.find_all("tr") if (result := self._row_result(row)) is not None]

    def _row_result(self, row: Any) -> dict[str, str | None] | None:
        if row.find(string="Filename", attrs={"class": "mainblockcontent"}) is not None:
            return None
        name_tag = row.find("a", attrs={"href": re.compile(r"details\.php\?id=")})
        if name_tag is None:
            return None
        href = str(name_tag.get("href", ""))
        return {
            "name": name_tag.text.strip(),
            "size": self._row_size(row),
            "link": f"{self.base_url}/{href}" if href else None,
        }

    @staticmethod
    def _row_size(row: Any) -> str | None:
        for cell in row.find_all("td", class_="mainblockcontent"):
            text = cell.text.strip()
            if "GiB" in text or "MiB" in text:
                return text
        return None

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "filename": await self.get_name(meta),
            "category": await self.get_category_id(meta),
            "info": await self.edit_desc(meta),
            "csrfToken": self.secret_token,
        }
        self._apply_visual_flags(data, meta)
        self._apply_imdb_info(data, meta)
        data["season"] = self._bool_string(self._is_season_pack(meta))
        data["anonymous"] = self._bool_string(self._is_anonymous(meta))
        return data

    @staticmethod
    def _apply_visual_flags(data: dict[str, Any], meta: Meta) -> None:
        if "3D" in meta.three_d:
            data["3d"] = "true"
        if "HDR" in meta.hdr:
            data["HDR10"] = "true"
        if "HDR10+" in meta.hdr:
            data["HDR10Plus"] = "true"
        if "DV" in meta.hdr:
            data["DolbyVision"] = "true"

    @staticmethod
    def _apply_imdb_info(data: dict[str, Any], meta: Meta) -> None:
        if meta.imdb_id or 0 != 0:
            data["infosite"] = f"{meta.imdb_info.get('imdb_url', '')}/"

    @staticmethod
    def _is_season_pack(meta: Meta) -> bool:
        return int((meta.tv_pack if meta.tv_pack is not None else "0") or 0) != 0

    def _is_anonymous(self, meta: Meta) -> bool:
        tracker_config = self.config["TRACKERS"][self.tracker]
        return int(meta.anon or 0) != 0 or bool(tracker_config.get("anon", False))

    @staticmethod
    def _bool_string(value: bool) -> str:
        return "true" if value else "false"

    async def get_nfo(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        nfo_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        nfo_files = list(nfo_dir.glob("*.nfo"))

        if nfo_files:
            nfo_path = nfo_files[0]
            async with aiofiles.open(nfo_path, "rb") as nfo_file:
                nfo_bytes = await nfo_file.read()
            return {"nfos": (nfo_path.name, nfo_bytes, "application/octet-stream")}
        return {}

    async def upload(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if cookie_jar:
            self.session.cookies = cookie_jar

        data = await self.get_data(meta)
        files = await self.get_nfo(meta)

        return await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            data=data,
            torrent_field_name="torrent",
            upload_cookies=self.session.cookies,
            upload_url=f"{self.base_url}/upload.php",
            hash_is_id=True,
            success_text="Upload successful!",
            default_announce="https://hdts-announce.ru/announce.php",
            additional_files=files,
        )
