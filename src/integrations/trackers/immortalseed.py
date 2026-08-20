# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
from typing import Any

import aiofiles
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class ImmortalSeed:
    """
    IS Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "IMMORTALSEED"
    display_name = "ImmortalSeed"
    allows_bloated_audio = True
    source_flag = "https://immortalseed.me"
    banned_groups = ("",)
    base_url = "https://immortalseed.me"
    torrent_url = "https://immortalseed.me/details.php?hash="
    supported_categories = ("TV", "MOVIE", "BOOK", "MUSIC", "GAME")
    tracker_urls = ("https://immortalseed.me",)

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.session = httpx.AsyncClient(headers={"User-Agent": f"Upload-Assistant/2.3 ({platform.system()} {platform.release()})"}, timeout=30)

    async def validate_credentials(self, meta: Meta) -> bool:
        return await self._load_cookies(meta)

    async def generate_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        return await builder.general_description_generator(
            meta,
            logo=False,
            nfo=False,
            signature=f"\n{meta.ua_signature} (https://github.com/wastaken7/Upload-Assistant)",
        )

    async def search_existing(self, meta: Meta) -> list[dict[str, str | None]]:
        await self._load_cookies(meta)
        search = self._search_definition(meta)
        if search is None:
            return []
        response = await self.session.get(self._search_url(*search))
        if await self._search_requires_login(meta, response):
            return []
        response.raise_for_status()
        return self._search_entries(response.text)

    async def _load_cookies(self, meta: Meta) -> bool:
        cookies = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        self.session.cookies.clear()
        if cookies is None:
            return False
        self.session.cookies.update(cookies)
        return True

    @staticmethod
    def _search_definition(meta: Meta) -> tuple[str, str] | None:
        category = str(meta.category)
        if category == "MOVIE":
            return "t_genre", str(meta.imdb_tt)
        if category == "TV":
            return "t_name", f"{meta.title} {meta.season}"
        if category in {"BOOK", "GAME"}:
            return "t_name", str(meta.title)
        if category == "MUSIC":
            return "t_name", f"{meta.artist} {meta.title}"
        return None

    @classmethod
    def _search_url(cls, search_type: str, search_query: str) -> str:
        return f"{cls.base_url}/browse.php?do=search&keywords={search_query}&search_type={search_type}"

    async def _search_requires_login(self, meta: Meta, response: httpx.Response) -> bool:
        if not self._is_login_response(response):
            return False
        await self.cookie_validator.handle_validation_failure(meta, self.tracker, response.text)
        meta.skipping = self.tracker
        return True

    @staticmethod
    def _is_login_response(response: httpx.Response) -> bool:
        text = response.text
        return "Forget your password" in text or "login.php" in str(response.url) or "login.php" in text

    @classmethod
    def _search_entries(cls, html: str) -> list[dict[str, str | None]]:
        soup = BeautifulSoup(html, "html.parser")
        torrent_table = soup.find("table", id="sortabletable")
        if not torrent_table:
            return []
        return [entry for row in torrent_table.select("tbody > tr")[1:] if (entry := cls._search_entry(row)) is not None]

    @staticmethod
    def _search_entry(row: Any) -> dict[str, str | None] | None:
        name_tag = row.select_one('a[href*="details.php?id="]')
        if not name_tag:
            return None
        href_value = name_tag.get("href")
        size_tag = row.select_one("td:nth-of-type(5)")
        return {
            "name": name_tag.get_text(strip=True),
            "size": size_tag.get_text(strip=True) if size_tag else None,
            "link": href_value if isinstance(href_value, str) else "",
        }

    def get_category_id(self, meta: Meta) -> int:
        resolver = {
            "MOVIE": self._movie_category_id,
            "TV": self._tv_category_id,
            "BOOK": self._book_category_id,
            "MUSIC": self._music_category_id,
            "GAME": self._game_category_id,
        }.get(str(meta.category))
        return 0 if resolver is None else resolver(meta)

    @classmethod
    def _movie_category_id(cls, meta: Meta) -> int:
        special = cls._movie_special_category(meta)
        if special is not None:
            return special
        return cls._movie_resolution_category(meta)

    @classmethod
    def _movie_special_category(cls, meta: Meta) -> int | None:
        if cls._is_documentary(meta):
            return 53 if bool(meta.sd) else 54
        return 32 if meta.anime else None

    @classmethod
    def _movie_resolution_category(cls, meta: Meta) -> int:
        tier = cls._movie_resolution_tier(meta)
        return {
            ("uhd", False): 59,
            ("uhd", True): 60,
            ("hd", False): 16,
            ("hd", True): 18,
            ("sd", False): 14,
            ("sd", True): 34,
        }[(tier, cls._is_non_english(meta))]

    @staticmethod
    def _movie_resolution_tier(meta: Meta) -> str:
        if meta.resolution == "2160p":
            return "uhd"
        return "sd" if meta.sd else "hd"

    @classmethod
    def _tv_category_id(cls, meta: Meta) -> int:
        special = cls._tv_special_category(meta)
        if special is not None:
            return special
        return cls._tv_resolution_category(meta)

    @classmethod
    def _tv_special_category(cls, meta: Meta) -> int | None:
        documentary = cls._documentary_category(meta)
        if documentary is not None:
            return documentary
        if meta.anime:
            return 32
        if cls._is_children_cartoon(meta):
            return 31
        return cls._tv_pack_category_id(meta) if meta.tv_pack else None

    @classmethod
    def _documentary_category(cls, meta: Meta) -> int | None:
        if not cls._is_documentary(meta):
            return None
        return 53 if bool(meta.sd) else 54

    @staticmethod
    def _tv_resolution_category(meta: Meta) -> int:
        if meta.resolution == "2160p":
            return 64
        if meta.resolution in {"1080p", "1080i", "720p"}:
            return 8
        if meta.sd:
            return 9 if "xvid" in str(meta.video_encode).casefold() else 48
        return 47

    @classmethod
    def _book_category_id(cls, meta: Meta) -> int:
        if meta.audiobook:
            return 35
        if meta.comic or meta.manga:
            return 41
        if meta.magazine:
            return 46
        return 22

    @staticmethod
    def _music_category_id(meta: Meta) -> int:
        return {"FLAC": 37, "MP3": 36}.get(str(meta.format).upper(), 39)

    @classmethod
    def _game_category_id(cls, meta: Meta) -> int:
        platform = str(meta.platform).upper()
        if platform in {"NDS", "3DS", "SWITCH", "WII", "WIIU"}:
            return 61
        if platform in {"PS1", "PS2", "PS3", "PS4", "PS5", "PSP", "PSVITA"}:
            return 28
        if platform in {"XBOX", "X360", "XONE", "XSX"}:
            return 29
        return 26

    @staticmethod
    def _tv_pack_category_id(meta: Meta) -> int:
        if meta.resolution == "2160p":
            return 63
        return 6 if meta.sd else 4

    @classmethod
    def _is_documentary(cls, meta: Meta) -> bool:
        return "documentary" in cls._metadata_terms(meta)

    @classmethod
    def _is_children_cartoon(cls, meta: Meta) -> bool:
        terms = cls._metadata_terms(meta)
        return bool(terms & {"children", "cartoons", "cartoon"})

    @staticmethod
    def _metadata_terms(meta: Meta) -> set[str]:
        genres = [str(value).casefold() for value in meta.genres] if isinstance(meta.genres, list) else []
        keywords = [str(value).casefold() for value in meta.keywords] if isinstance(meta.keywords, list) else []
        return set(genres + keywords)

    @staticmethod
    def _is_non_english(meta: Meta) -> bool:
        return str(meta.original_language) != "en"

    async def get_nfo(self, meta: Meta) -> dict[str, tuple[str, bytes, str]]:
        nfo_dir = release_temp_dir(meta.base_dir, meta.uuid)
        nfo_files = list(nfo_dir.glob("*.nfo"))

        if nfo_files:
            nfo_path = nfo_files[0]
            async with aiofiles.open(nfo_path, "rb") as nfo_file:
                nfo_bytes = await nfo_file.read()
            return {"nfofile": (nfo_path.name, nfo_bytes, "application/octet-stream")}
        nfo_content = await self.generate_description(meta)
        nfo_bytes = nfo_content.encode("utf-8")
        nfo_filename = f"{(meta.scene_name or meta.basename_no_ext)}.nfo"
        return {"nfofile": (nfo_filename, nfo_bytes, "application/octet-stream")}

    async def get_name(self, meta: Meta) -> str:
        if meta.scene_name:
            return meta.scene_name

        return meta.basename_no_ext

    async def get_cover(self, meta: Meta) -> str:
        hosted = self._hosted_cover(meta.hosted_artwork)
        if hosted:
            return hosted
        return self._https_url(meta.artwork_url)

    @classmethod
    def _hosted_cover(cls, covers: Any) -> str:
        if not isinstance(covers, list):
            return ""
        return next((url for entry in covers if (url := cls._cover_entry_url(entry))), "")

    @classmethod
    def _cover_entry_url(cls, entry: Any) -> str:
        if not isinstance(entry, dict):
            return ""
        return cls._https_url(entry.get("raw_url"))

    @staticmethod
    def _https_url(value: Any) -> str:
        return value if isinstance(value, str) and value.startswith("https://") else ""

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        message = self._description_message(meta)
        data: dict[str, Any] = {
            "UseNFOasDescr": "no",
            "message": message,
            "category": self.get_category_id(meta),
            "subject": await self.get_name(meta),
            "nothingtopost": "1",
            "t_image_url": await self.get_cover(meta),
            "submit": "Upload Torrent",
            "anonymous": "yes" if self._is_anonymous(meta) else "no",
        }
        if meta.category == "MOVIE":
            data["t_link"] = self._imdb_url(meta)
        return data

    @staticmethod
    def _description_message(meta: Meta) -> str:
        if meta.category in {"BOOK", "MUSIC"}:
            return str(meta.overview)
        return f"{meta.overview}\n\n[youtube]{meta.youtube}[/youtube]"

    def _is_anonymous(self, meta: Meta) -> bool:
        configured = bool(self.config["TRACKERS"][self.tracker].get("anon", False))
        return int(meta.anon or 0) != 0 or configured

    @staticmethod
    def _imdb_url(meta: Meta) -> str:
        return str(meta.imdb_info.get("imdb_url", "")) if isinstance(meta.imdb_info, dict) else ""

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
            hash_is_id=True,
            torrent_field_name="torrentfile",
            torrent_name=f"{(meta.clean_name if meta.clean_name is not None else 'placeholder')}",
            upload_cookies=self.session.cookies,
            upload_url="https://immortalseed.me/upload.php",
            additional_files=files,
            success_list=["Download Torrent (SSL)", "Thank you for uploading"],
        )
