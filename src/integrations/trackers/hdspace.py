# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import aiofiles
import httpx
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class HDSpace:
    """
    HD-Space (HDS) is a Private Torrent Tracker for HD MOVIES / TV
    """

    auth_type = "cookies"
    tracker = "HDSPACE"
    display_name = "HDSpace"
    allows_bloated_audio = True
    source_flag = "HD-Space"
    banned_groups = ("",)
    base_url = "https://hd-space.org"
    torrent_url = f"{base_url}/index.php?page=torrent-details&id="
    requests_url = f"{base_url}/index.php?page=viewrequests"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("hd-space.pw",)

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.session = httpx.AsyncClient(headers={"User-Agent": f"Upload-Assistant/2.3 ({platform.system()} {platform.release()})"}, timeout=30)

    async def validate_credentials(self, meta: Meta) -> bool:
        return await self._load_cookies(meta)

    async def _load_cookies(self, meta: Meta) -> bool:
        cookies = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        self.session.cookies.clear()
        if cookies is None:
            return False
        self.session.cookies.update(cookies)
        return True

    async def generate_description(self, meta: Meta) -> str:
        try:
            builder = DescriptionBuilder(self.tracker, self.config)
            description = await builder.general_description_generator(
                meta,
                bluray=False,
                book=False,
                custom_signature=False,
                game=False,
                nfo=False,
                signature=f"[center][url=https://github.com/wastaken7/Upload-Assistant][size=2]{meta.ua_signature}[/size][/url][/center]",
            )
        except Exception as e:
            logger.info(f"{self.tracker}: Error generating description: {e}")
            description = ""

        return description

    async def get_additional_checks(self, meta: Meta) -> bool:
        if not self._resolution_policy_passes(meta):
            return False
        if not self._codec_policy_passes(meta):
            return False
        filelist = self._payload_files(meta)
        if not filelist:
            logger.info(f"{self.tracker}: No payload files found, skipping upload.")
            return False
        if not self._archive_policy_passes(filelist):
            return False
        return self._screenshot_policy_passes(meta)

    def _resolution_policy_passes(self, meta: Meta) -> bool:
        if meta.resolution in {"2160p", "1080p", "1080i", "720p"}:
            return True
        logger.info(f"{self.tracker}: The resolution must be at least 720p, skipping the upload...")
        return False

    def _codec_policy_passes(self, meta: Meta) -> bool:
        values = (str(meta.video_codec or "").casefold(), str(meta.video_encode or "").casefold())
        if not self._contains_forbidden_codec(values):
            return True
        logger.info(f"{self.tracker}: xvid/divx codecs are not allowed, skipping the upload...")
        return False

    @staticmethod
    def _contains_forbidden_codec(values: tuple[str, str]) -> bool:
        return any(forbidden in value for forbidden in ("xvid", "divx") for value in values)

    @staticmethod
    def _payload_files(meta: Meta) -> list[Any]:
        value = meta.filelist
        return list(value) if isinstance(value, (list, tuple, set)) else []

    def _archive_policy_passes(self, filelist: list[Any]) -> bool:
        archive = next((Path(str(item)).name for item in filelist if self._is_rar_file(str(item))), "")
        if not archive:
            return True
        logger.info(f"{self.tracker}: RAR files are not allowed: {archive}. Skipping upload.")
        return False

    def _screenshot_policy_passes(self, meta: Meta) -> bool:
        screenshots = self._collect_movie_tv_screenshots(meta)
        if meta.category in {"MOVIE", "TV"} and len(screenshots) < 3:
            logger.info(f"{self.tracker}: HD-Space requires at least 3 valid screenshots for Movie/TV uploads.")
            return False
        return all(self._screenshot_url_policy_passes(url) for url in screenshots)

    def _screenshot_url_policy_passes(self, image_url: str) -> bool:
        if not image_url.lower().startswith(("http://", "https://")):
            logger.info(f"{self.tracker}: Screenshot links must use direct HTTP(S) URLs. Found invalid entry: {image_url}.")
            return False
        if self._is_png_screenshot(image_url):
            return True
        logger.info(f"{self.tracker}: HD-Space requires .png screenshots only, skipping upload.")
        return False

    async def search_existing(self, meta: Meta) -> list[dict[str, str | None]]:
        await self._load_cookies(meta)
        imdb_id = str(meta.imdb)
        if imdb_id == "0":
            logger.info(f"{self.tracker}: IMDb ID not found, cannot search for duplicates on {self.tracker}.")
            return []
        return await self._search_pages(meta, imdb_id)

    async def _search_pages(self, meta: Meta, imdb_id: str) -> list[dict[str, str | None]]:
        dupes: list[dict[str, str | None]] = []
        current_page = 0
        while current_page <= 10:
            response = await self._search_page_response(imdb_id, current_page)
            if await self._search_login_failure(meta, response):
                return []
            parsed = self._parse_search_page(response.text, current_page)
            if parsed is None:
                break
            entries, has_next = parsed
            dupes.extend(entries)
            if not has_next:
                break
            current_page += 1
        return dupes

    async def _search_page_response(self, imdb_id: str, current_page: int) -> httpx.Response:
        response = await self.session.get(
            f"{self.base_url}/index.php",
            params={"page": "torrents", "search": imdb_id, "active": "0", "options": "2", "pages": str(current_page)},
        )
        response.raise_for_status()
        return response

    async def _search_login_failure(self, meta: Meta, response: httpx.Response) -> bool:
        if not self._is_login_response(response):
            return False
        await self.cookie_validator.handle_validation_failure(meta, self.tracker, response.text)
        meta.skipping = self.tracker
        return True

    @staticmethod
    def _is_login_response(response: httpx.Response) -> bool:
        text = response.text
        return "Recover password" in text or "page=login" in str(response.url) or "page=login" in text

    def _parse_search_page(self, html: str, current_page: int) -> tuple[list[dict[str, str | None]], bool] | None:
        parts = html.split("Show/Hide Categories", 1)
        if len(parts) < 2:
            logger.info(f"{self.tracker}: [bold yellow]Unexpected page structure on page {current_page}, stopping search[/bold yellow]")
            return None
        soup = BeautifulSoup(parts[1], "html.parser")
        rows = soup.select("tr:has(td.lista)")
        if not rows:
            return [], False
        entries = [entry for row in rows if (entry := self._search_row(row)) is not None]
        return entries, self._has_next_page(soup, current_page)

    @classmethod
    def _search_row(cls, row: Any) -> dict[str, str | None] | None:
        name_tag = row.select_one('a[href*="page=torrent-details"]')
        if not name_tag:
            return None
        name = cls._search_name(name_tag)
        torrent_link = cls._search_link(name_tag)
        if not name or not torrent_link:
            return None
        return {"name": name, "size": cls._search_size(row), "link": torrent_link}

    @staticmethod
    def _search_name(name_tag: Any) -> str:
        name = name_tag.get_text(strip=True)
        if name:
            return name
        return str(name_tag.get("title", "")) if name_tag.has_attr("title") else ""

    @classmethod
    def _search_link(cls, name_tag: Any) -> str:
        href = str(name_tag.get("href", "")).lstrip("/")
        return f"{cls.base_url.rstrip('/')}/{href}" if href else ""

    @staticmethod
    def _search_size(row: Any) -> str | None:
        cells = row.find_all("td", class_="lista")
        return next((text for cell in cells if (text := cell.get_text(strip=True)) and re.search(r"([0-9.]+)\s+(GB|MB|KB|B)", text, re.I)), None)

    @staticmethod
    def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
        next_link = cast(Any, soup).find("a", href=re.compile(r"pages="), string=re.compile(r"Next|>>", re.I))
        if next_link:
            return True
        return soup.find("a", href=re.compile(rf"pages={current_page + 1}")) is not None

    async def get_category_id(self, meta: Meta) -> int:
        if meta.is_disc == "BDMV":
            return 15
        if meta.type == "REMUX":
            return 40
        group = self._category_group(meta)
        return self._category_mapping(group).get(str(meta.resolution), 38)

    @classmethod
    def _category_group(cls, meta: Meta) -> str:
        terms = cls._metadata_terms(meta)
        if "documentary" in terms:
            return "DOCUMENTARY"
        if meta.anime:
            return "ANIME"
        return str(meta.category) if meta.category in {"MOVIE", "TV"} else "OTHER"

    @staticmethod
    def _category_mapping(group: str) -> dict[str, int]:
        mappings = {
            "MOVIE": {"2160p": 46, "1080p": 19, "1080i": 19, "720p": 18},
            "TV": {"2160p": 45, "1080p": 22, "1080i": 22, "720p": 21},
            "DOCUMENTARY": {"2160p": 47, "1080p": 25, "1080i": 25, "720p": 24},
            "ANIME": {"2160p": 48, "1080p": 28, "1080i": 28, "720p": 27},
        }
        return mappings.get(group, {})

    @staticmethod
    def _metadata_terms(meta: Meta) -> set[str]:
        genres = [str(value).casefold() for value in meta.genres] if isinstance(meta.genres, list) else []
        keywords = [str(value).casefold() for value in meta.keywords] if isinstance(meta.keywords, list) else []
        return set(genres + keywords)

    async def get_requests(self, meta: Meta) -> list[dict[str, str | None]] | bool:
        if not self._request_search_enabled(meta):
            return False
        try:
            await self._load_cookies(meta)
            response = await self._request_response(meta.title)
            results = self._request_entries(response.text)
            self._log_request_entries(results)
            return results
        except Exception as error:
            logger.info(f"{self.tracker}: An error occurred while fetching requests: {error}", extra={"markup": False})
            return []

    def _request_search_enabled(self, meta: Meta) -> bool:
        return bool(self.config["DEFAULT"].get("search_requests", False) or meta.search_requests)

    async def _request_response(self, query: str) -> httpx.Response:
        response = await self.session.get(
            f"{self.base_url}/index.php?",
            params={"page": "viewrequests", "search": query, "filter": "true"},
            cookies=self.session.cookies,
        )
        response.raise_for_status()
        return response

    @classmethod
    def _request_entries(cls, html: str) -> list[dict[str, str | None]]:
        soup = BeautifulSoup(html, "html.parser")
        return [entry for row in soup.select('form[action="index.php?page=takedelreq"] table.lista tr') if (entry := cls._request_row(row)) is not None]

    @staticmethod
    def _request_row(row: Any) -> dict[str, str | None] | None:
        if row.find("td", class_="header"):
            return None
        name_element = row.select_one("td.lista a b")
        if not name_element:
            return None
        link_element = name_element.find_parent("a")
        raw_link = link_element.get("href") if link_element else None
        return {"Name": name_element.text.strip(), "Link": str(raw_link) if raw_link else None}

    def _log_request_entries(self, results: list[dict[str, str | None]]) -> None:
        if not results:
            return
        lines = [f"\n{self.tracker}: [bold yellow]Your upload may fulfill the following request(s), check it out:[/bold yellow]\n"]
        for result in results:
            lines.extend(self._request_log_lines(result))
        logger.info("\n".join(lines))

    def _request_log_lines(self, result: dict[str, str | None]) -> list[str]:
        return [
            f"[bold green]Name:[/bold green] {result['Name']}",
            f"[bold green]Link:[/bold green] {self.base_url}/{result['Link']}",
            "",
        ]

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "category": await self.get_category_id(meta),
            "filename": await self.get_name(meta),
            "genre": str(meta.genres),
            "imdb": str(meta.imdb),
            "info": await self.generate_description(meta),
            "nuk_rea": "",
            "nuk": "false",
            "req": "false",
            "submit": "Send",
            "t3d": "true" if "3D" in meta.three_d else "false",
            "user_id": "",
            "youtube_video": str(meta.youtube),
        }

        data["anonymous"] = "true" if self._is_anonymous(meta) else "false"
        return data

    def _is_anonymous(self, meta: Meta) -> bool:
        configured = bool(self.config["TRACKERS"][self.tracker].get("anon", False))
        return int(meta.anon or 0) != 0 or configured

    async def get_nfo(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        nfo_dir = release_temp_dir(meta.base_dir, meta.uuid)
        nfo_files = list(nfo_dir.glob("*.nfo"))

        if nfo_files:
            nfo_path = nfo_files[0]
            async with aiofiles.open(nfo_path, "rb") as nfo_file:
                nfo_bytes = await nfo_file.read()
            return {"nfo": (nfo_path.name, nfo_bytes, "application/octet-stream")}
        return {}

    async def upload(self, meta: Meta) -> bool:
        await self._load_cookies(meta)
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
            upload_url="https://hd-space.org/index.php?page=upload",
            hash_is_id=True,
            success_text="download.php?id=",
            additional_files=files,
        )

    async def get_name(self, meta: Meta) -> str:
        return meta.name

    @staticmethod
    def _is_rar_file(path_value: str) -> bool:
        lowered = path_value.lower()
        if lowered.endswith(".rar"):
            return True
        return bool(re.search(r"\.r\d{2,}$", lowered))

    @staticmethod
    def _is_png_screenshot(url_or_path: str) -> bool:
        candidate = Path(urlparse(url_or_path).path).suffix.lower()
        return candidate == ".png" and candidate != ""

    @staticmethod
    def _is_allowed_screenshot_width(width: int) -> bool:
        return width in (1280, 1920, 3840)

    @staticmethod
    def _read_screenshot_width(image_path: str) -> int | None:
        try:
            with Image.open(image_path) as image:
                width, _ = image.size
                return width
        except FileNotFoundError, OSError, UnidentifiedImageError, TypeError:
            return None

    @classmethod
    def _collect_movie_tv_screenshots(cls, meta: Meta) -> list[str]:
        images = cls._image_items(meta.image_list)
        screenshots: list[str] = []
        dimension_failures: list[str] = []
        for image in images:
            result = cls._processed_screenshot(image)
            if result is None:
                continue
            image_url, failed_local = result
            if failed_local:
                dimension_failures.append(failed_local)
            else:
                screenshots.append(image_url)
        cls._log_dimension_failures(dimension_failures)
        return screenshots

    @staticmethod
    def _image_items(value: Any) -> list[Any]:
        return list(value) if isinstance(value, (list, tuple)) else []

    @classmethod
    def _processed_screenshot(cls, image: Any) -> tuple[str, str] | None:
        image_url, local_file = cls._screenshot_entry(image)
        if not cls._valid_screenshot_url(image_url):
            return None
        failed_local = local_file if local_file and cls._invalid_local_screenshot(local_file) else ""
        return image_url, failed_local

    @staticmethod
    def _screenshot_entry(image: Any) -> tuple[str, str]:
        if isinstance(image, str):
            return image.strip(), ""
        if not isinstance(image, Mapping):
            return "", ""
        raw_url = str(image.get("raw_url", "")).strip()
        img_url = str(image.get("img_url", "")).strip()
        web_url = str(image.get("web_url", "")).strip()
        local_file = str(image.get("local_file_path", "")).strip()
        return raw_url or img_url or web_url, local_file

    @classmethod
    def _valid_screenshot_url(cls, image_url: str) -> bool:
        return bool(image_url) and cls._is_png_screenshot(image_url)

    @classmethod
    def _invalid_local_screenshot(cls, local_file: str) -> bool:
        width = cls._read_screenshot_width(local_file)
        return width is not None and not cls._is_allowed_screenshot_width(width)

    @classmethod
    def _log_dimension_failures(cls, failures: list[str]) -> None:
        if not failures:
            return
        logger.info(f"{cls.tracker}: found screenshot(s) with invalid dimensions (must be 1280x*, 1920x*, or 3840x*). Offending: {', '.join(failures)}")
