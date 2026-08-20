# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import re
from typing import Any

import aiofiles
import bencodepy
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class IPTorrents:
    """
    IPT Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "IPTORRENTS"
    display_name = "IPTorrents"
    allows_bloated_audio = True
    source_flag = "IPTorrents"
    base_url = "https://iptorrents.com"
    banned_groups = (
        "1337x",
        "3DM",
        "3dtorrents",
        "ali213",
        "AreaFiles",
        "BD25",
        "BlackBox",
        "BLuBits",
        "bluhd.org",
        "BTN",
        "BTNet",
        "Catalyst RG",
        "CBUT",
        "CHDBits",
        "CHDTV.Net",
        "CINEMANIA",
        "CorePack",
        "CorePacks",
        "CPG",
        "DADDY",
        "DDR",
        "Digital Desi Releasers",
        "DLBR",
        "DLLHits",
        "DRIG",
        "DVDSEED",
        "EncodeKing",
        "FGT",
        "filelist.ro",
        "flashtorrents",
        "Ganool",
        "h33t",
        "HD4FUN",
        "HDAccess",
        "HDChina",
        "HDGeek",
        "HDME",
        "HDRoad",
        "HDStar",
        "HDTime",
        "HDTurk",
        "HDWing",
        "HorribleSubs",
        "hqsource.org",
        "IWStream",
        "Kingdom-KVCD",
        "MeGaHeRTZ",
        "MkvCage",
        "MVGroup.org",
        "MYEGY",
        "nosTEAM",
        "OntohinBD",
        "os4world",
        "Pimp4003",
        "Projekt-Revolution",
        "ps3gameroom",
        "PTP",
        "RARBG/RBG",
        "RLS",
        "RLSM",
        "SFS-RG",
        "SFS",
        "Shaanig",
        "SHOWSCEN",
        "SilverTorrents",
        "SiRiUs sHaRe",
        "SpaceHD",
        "The Wolfs Den",
        "TPTB",
        "TTG",
        "UNKNOWN",
        "X360ISO",
        "YIFY",
        "zombiRG",
    )
    torrent_url = "https://iptorrents.com/torrent.php?id="
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("ssl.empirehost.me", "routing.bgp.technology", "127.0.0.1.stackoverflow.tech")

    def __init__(self, config: Config):
        self.config = config
        self.cookie_validator = CookieValidator(config)
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.session = httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"}, timeout=30)

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if not cookie_jar:
            return False
        self.session.cookies = cookie_jar
        return True

    async def generate_description(self, meta: Meta):
        builder = DescriptionBuilder(self.tracker, self.config)
        return await builder.general_description_generator(
            meta,
            logo=False,
            nfo=False,
            signature=f"[center][url=https://github.com/wastaken7/Upload-Assistant]{meta.ua_signature}[/center][/url][/right]",
        )

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        search = self._search_spec(meta)
        if search is None:
            return []
        category_id, query = search
        await self._load_search_cookies(meta)
        response = await self.session.get(self._search_url(category_id, query), follow_redirects=True)
        if await self._search_requires_login(meta, response):
            return []
        response.raise_for_status()
        forbidden = self._forbidden_search_keywords(meta)
        return self._parse_search_results(response.text, forbidden)

    def _search_spec(self, meta: Meta) -> tuple[int, str] | None:
        direct = self._direct_search_spec(meta)
        if direct is not None:
            return direct
        return self._mapped_search_spec(meta)

    @staticmethod
    def _direct_search_spec(meta: Meta) -> tuple[int, str] | None:
        category = str(meta.category or "")
        if category == "MOVIE":
            return 72, str(meta.title)
        if category == "TV":
            return 73, f"{meta.title} {meta.season}".strip()
        return None

    def _mapped_search_spec(self, meta: Meta) -> tuple[int, str] | None:
        category_id = self.get_category_id(meta)
        query = self._non_video_search_query(meta)
        if not category_id or not query:
            return None
        return category_id, query

    @staticmethod
    def _non_video_search_query(meta: Meta) -> str:
        if meta.category in {"BOOK", "GAME"}:
            return str(meta.title or "")
        if meta.category == "MUSIC":
            return f"{meta.artist} {meta.title}".strip()
        return ""

    @classmethod
    def _search_url(cls, category_id: int, query: str) -> str:
        return f"{cls.base_url}/t?{category_id}=&q={query}"

    async def _load_search_cookies(self, meta: Meta) -> None:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if cookie_jar:
            self.session.cookies = cookie_jar

    async def _search_requires_login(self, meta: Meta, response: httpx.Response) -> bool:
        if "login" not in str(response.url).casefold() and "login.php" not in response.text.casefold():
            return False
        await self.cookie_validator.handle_validation_failure(meta, self.tracker, response.text)
        meta.skipping = self.tracker
        return True

    @classmethod
    def _forbidden_search_keywords(cls, meta: Meta) -> list[str]:
        keywords: list[str] = []
        cls._append_disc_forbidden_keywords(keywords, meta)
        cls._append_type_forbidden_keywords(keywords, meta)
        return keywords

    @staticmethod
    def _append_disc_forbidden_keywords(keywords: list[str], meta: Meta) -> None:
        if str(meta.is_disc or "").strip().casefold() != "bdmv":
            return
        keywords.extend(["remux", "x264", "x265", "x 264", "x 265", "webrip", "av1", "h 264", "h 265", "h264", "h265", " web "])
        if "1080" in str(meta.resolution):
            keywords.append("hevc")

    @staticmethod
    def _append_type_forbidden_keywords(keywords: list[str], meta: Meta) -> None:
        if str(meta.type or "").strip().casefold() == "webdl":
            keywords.extend(["webrip", "bluray", "blu-ray"])

    @classmethod
    def _parse_search_results(cls, html: str, forbidden_keywords: list[str]) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", id="torrents")
        if table is None:
            return []
        body = table.find("tbody")
        if body is None:
            return []
        return [entry for row in body.find_all("tr") if (entry := cls._search_row(row, forbidden_keywords)) is not None]

    @classmethod
    def _search_row(cls, row: Any, forbidden_keywords: list[str]) -> dict[str, str] | None:
        cells = row.find_all("td")
        if len(cells) <= 5:
            return None
        link_tag = cells[1].find("a", class_="hv")
        if link_tag is None:
            return None
        name = " ".join(link_tag.get_text(" ", strip=True).split())
        if cls._has_forbidden_keyword(name, forbidden_keywords):
            return None
        torrent_path = str(link_tag.get("href") or "")
        return {
            "name": name,
            "size": cls._size_text(cells[5].get_text(" ", strip=True)),
            "link": f"{cls.base_url}{torrent_path}",
        }

    @staticmethod
    def _has_forbidden_keyword(name: str, forbidden_keywords: list[str]) -> bool:
        lowered = name.casefold()
        return any(keyword in lowered for keyword in forbidden_keywords)

    @staticmethod
    def _size_text(value: str) -> str:
        match = re.search(r"\d+(?:[.,]\d+)?\s*(?:KB|MB|GB|TB)", value, re.IGNORECASE)
        return match.group(0) if match else ""

    def get_category_id(self, meta: Meta) -> int:
        generic = self._genre_category(meta)
        if generic is not None:
            return generic
        resolver = {
            "MOVIE": self._movie_category,
            "TV": self._tv_category,
            "GAME": self._game_category,
            "BOOK": self._book_category,
            "MUSIC": self._music_category,
        }.get(str(meta.category))
        return 0 if resolver is None else resolver(meta)

    @staticmethod
    def _genre_category(meta: Meta) -> int | None:
        genres = str(meta.genres or "").casefold()
        if "documentary" in genres:
            return 26
        if "sport" in genres:
            return 55
        return None

    @classmethod
    def _movie_category(cls, meta: Meta) -> int:
        disc = cls._movie_disc_category(meta)
        if disc is not None:
            return disc
        special = cls._movie_special_category(meta)
        if special is not None:
            return special
        return cls._movie_language_category(meta)

    @staticmethod
    def _movie_disc_category(meta: Meta) -> int | None:
        return {"BDMV": 89, "DVD": 6}.get(str(meta.is_disc))

    @classmethod
    def _movie_special_category(cls, meta: Meta) -> int | None:
        primary = cls._movie_primary_special(meta)
        if primary is not None:
            return primary
        return cls._movie_secondary_special(meta)

    @classmethod
    def _movie_primary_special(cls, meta: Meta) -> int | None:
        visual = cls._movie_visual_special(meta)
        if visual is not None:
            return visual
        return cls._movie_codec_special(meta)

    @staticmethod
    def _movie_visual_special(meta: Meta) -> int | None:
        if meta.resolution == "2160p":
            return 101
        return 87 if "3D" in str(meta.three_d) else None

    @staticmethod
    def _movie_codec_special(meta: Meta) -> int | None:
        if str(meta.video_codec or "").casefold() == "x265":
            return 100
        return 20 if meta.type in {"WEBDL", "WEBRIP"} else None

    @classmethod
    def _movie_secondary_special(cls, meta: Meta) -> int | None:
        source = str(meta.source or "").casefold()
        bluray = cls._bluray_movie_category(meta, source)
        if bluray is not None:
            return bluray
        release = cls._movie_release_type_category(meta)
        if release is not None:
            return release
        return cls._movie_resolution_source_category(meta, source)

    @staticmethod
    def _bluray_movie_category(meta: Meta, source: str) -> int | None:
        return 48 if source == "bluray" and meta.resolution in {"1080p", "720p"} else None

    @staticmethod
    def _movie_release_type_category(meta: Meta) -> int | None:
        return {"BDRIP": 90, "XVID": 7}.get(str(meta.type))

    @staticmethod
    def _movie_resolution_source_category(meta: Meta, source: str) -> int | None:
        if meta.resolution == "480p":
            return 77
        return 96 if source.upper() in {"CAM", "TS", "TC"} else None

    @classmethod
    def _movie_language_category(cls, meta: Meta) -> int:
        if cls._is_family_movie(meta):
            return 54
        return 38 if cls._is_non_english(meta) else 48

    @staticmethod
    def _is_family_movie(meta: Meta) -> bool:
        genres = str(meta.genres or "").casefold()
        return "kids" in genres or "family" in genres

    @staticmethod
    def _is_non_english(meta: Meta) -> bool:
        language = str(meta.original_language or "")
        return bool(language) and language != "en"

    @classmethod
    def _tv_category(cls, meta: Meta) -> int:
        pack = cls._tv_pack_category(meta)
        if pack is not None:
            return pack
        if meta.original_language and meta.original_language != "en":
            return 82
        disc = {"BDMV": 23, "DVD": 24}.get(str(meta.is_disc))
        if disc is not None:
            return disc
        return cls._tv_release_category(meta)

    @staticmethod
    def _tv_pack_category(meta: Meta) -> int | None:
        if not meta.tv_pack:
            return None
        return 83 if meta.original_language and meta.original_language != "en" else 65

    @staticmethod
    def _tv_release_category(meta: Meta) -> int:
        checks = (
            (str(meta.video_codec or "").casefold() == "x265", 99),
            (meta.type in {"WEBDL", "WEBRIP"}, 22),
            (meta.type == "DVDRIP", 25),
            (meta.resolution == "480p", 78),
            (meta.type == "XVID", 4),
        )
        return next((value for matched, value in checks if matched), 5)

    @staticmethod
    def _game_category(meta: Meta) -> int:
        platform = str(meta.platform or "").upper()
        groups = {
            47: {"NDS", "3DS", "SWITCH"},
            50: {"WII", "WIIU"},
            71: {"PS1", "PS2", "PS3", "PS4", "PS5", "PSP", "PSVITA"},
            44: {"XBOX", "X360", "XONE", "XSX"},
        }
        return next((category for category, platforms in groups.items() if platform in platforms), 43)

    @classmethod
    def _book_category(cls, meta: Meta) -> int:
        special = cls._book_special_category(meta)
        if special is not None:
            return special
        return 102 if cls._book_is_non_english(meta) else 35

    @classmethod
    def _book_special_category(cls, meta: Meta) -> int | None:
        if meta.audiobook:
            return 64
        illustrated = cls._book_illustrated_category(meta)
        if illustrated is not None:
            return illustrated
        return cls._book_periodical_category(meta)

    @staticmethod
    def _book_illustrated_category(meta: Meta) -> int | None:
        return 94 if meta.comic or meta.manga else None

    @staticmethod
    def _book_periodical_category(meta: Meta) -> int | None:
        return 92 if meta.magazine or meta.newspaper else None

    @staticmethod
    def _book_is_non_english(meta: Meta) -> bool:
        return str(meta.book_language_iso or "").casefold() != "eng"

    @staticmethod
    def _music_category(meta: Meta) -> int:
        return 80 if str(meta.format or "").upper() == "FLAC" else 3

    async def get_name(self, meta: Meta) -> str:
        name = str(meta.scene_name if meta.scene_name else meta.clean_name)
        name = self._normalized_group_name(name)
        name = name.replace("'", "").replace('"', "")
        if meta.scene and "[NO RAR]" not in name.upper():
            name += " [NO RAR]"
        return re.sub(r"\s{2,}", " ", name)

    @staticmethod
    def _normalized_group_name(name: str) -> str:
        replacements = {
            "3DAccess": "3DA",
            "AreaFiles": "AF",
            "BeyondHD": "BHD",
            "Blackcat": "Blackcat",
            "Blu-Bits": "BluHD",
            "Bluebird": "BB",
            "BlueEvolution": "BluEvo",
            "Chdbits": "CHD",
            "CtrlHD": "CtrlHD",
            "HDAccess": "HDA",
            "HDChina": "HDC",
            "HDClub": "HDCL",
            "HDGeek": "HDG",
            "HDRoad": "HDR",
            "HDStar": "HDS",
            "HDWing": "HDW",
            "ExtraTorrent": "ETRG",
            "IWStream": "IWS",
            "Kingdom-KVCD": "KVCD",
            "MVGroup": "MVG",
            "Projekt-Revolution": "Projekt",
            "PublicHD": "PHD",
            "SpaceHD": "SHD",
            "ThumperDC": "TDC",
            "TrollHD": "TrollHD",
            "TheWolfsDen": "TWD",
        }
        result = name
        for key, value in replacements.items():
            result = result.replace(key, value)
        return result

    async def get_is_freeleech(self, meta: Meta) -> bool:
        torrent_path = release_temp_dir(meta.base_dir, meta.uuid) / "BASE.torrent"
        if not torrent_path.exists():
            return False
        try:
            async with aiofiles.open(torrent_path, "rb") as handle:
                torrent_data = await handle.read()
            return self._torrent_size_bytes(torrent_data) / (1024**3) >= 8
        except Exception as error:
            logger.info(f"{self.tracker}: [bold red]Error reading torrent file for size check on {self.tracker}: {error}[/bold red]")
            return False

    @staticmethod
    def _torrent_size_bytes(torrent_data: bytes) -> int:
        metainfo = bencodepy.decode(torrent_data)
        info = metainfo.get(b"info", {})
        files = info.get(b"files")
        if isinstance(files, list):
            return sum(int(file_info.get(b"length", 0)) for file_info in files if isinstance(file_info, dict))
        return int(info.get(b"length", 0))

    async def get_data(self, meta: Meta) -> dict[str, str | int]:
        data: dict[str, str | int] = {
            "name": meta.name,
            "descr": await self.generate_description(meta),
            "type": self.get_category_id(meta),
        }

        if await self.get_is_freeleech(meta):
            data["freeleech"] = "on"

        if self._anonymous_upload(meta):
            data["anonymous"] = "on"

        return data

    def _anonymous_upload(self, meta: Meta) -> bool:
        tracker_anon = bool(self.config["TRACKERS"][self.tracker].get("anon", False))
        return meta.anon != 0 or tracker_anon

    async def upload(self, meta: Meta) -> bool:
        cookies = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        self.session.cookies.clear()
        if cookies is not None:
            self.session.cookies.update(cookies)

        data = await self.get_data(meta)

        upload = await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            data=data,
            torrent_field_name="file",
            torrent_name=await self.get_name(meta),
            upload_cookies=self.session.cookies,
            upload_url=f"{self.base_url}/takeupload.php",
            error_text="Upload failed!",
            id_pattern=r"download\.php/(\d+)/",
        )

        if upload and self._should_force_data(meta):
            await self.edit_post_upload(meta)

        return upload

    def _should_force_data(self, meta: Meta) -> bool:
        return bool(self.config["TRACKERS"][self.tracker].get("force_data", False)) and not meta.debug

    async def edit_post_upload(self, meta: Meta):
        torrent_id = meta.tracker_status[self.tracker]["torrent_id"]
        data: dict[str, str | int] = {
            "name": meta.name,
            "descr": await self.generate_description(meta),
            "type": self.get_category_id(meta),
            "imdb_id": str(meta.tmdb_id),
            "id": torrent_id,
        }

        edit_url = f"https://iptorrents.com/t/{torrent_id}/edit"

        response = await self.session.post(edit_url, data=data)
        if response.status_code != 302:
            meta.tracker_status[self.tracker]["status_message"] += " Failed to edit torrent."
