# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import platform
import re
from typing import Any, cast

import aiofiles
import httpx

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class TorrentLeech:
    """
    TORRENTLEECH (TL) is a Private Torrent Tracker for 0DAY / GENERAL. not here _ not scene
    """

    auth_type = "other_api"
    tracker = "TORRENTLEECH"
    display_name = "TorrentLeech"
    source_flag = "TorrentLeech.org"
    base_url = "https://www.torrentleech.org"
    banned_groups = ()
    http_upload_url = f"{base_url}/torrents/upload/"
    api_upload_url = f"{base_url}/torrents/upload/apiupload"
    torrent_url = f"{base_url}/torrent/"
    supported_categories = ("TV", "MOVIE", "BOOK", "GAME", "MUSIC")
    tracker_urls = ("tracker.tleechreload", "tracker.torrentleech")
    allows_bloated_audio = True

    def __init__(self, config: Config) -> None:
        self.config: Config = config
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.session = httpx.AsyncClient(timeout=60.0)
        self.tracker_config: dict[str, Any] = self.config["TRACKERS"][self.tracker]
        self.api_upload: bool = bool(self.tracker_config.get("api_upload", False))
        self.passkey: str = str(self.tracker_config.get("passkey", ""))
        self.announce_list = [f"https://tracker.torrentleech.org/a/{self.passkey}/announce", f"https://tracker.tleechreload.org/a/{self.passkey}/announce"]
        self.session.headers.update({"User-Agent": f"Upload Assistant ({platform.system()} {platform.release()})"})

    async def get_additional_checks(self, meta: Meta) -> bool:
        return await self.common.check_and_confirm_adult_media_upload(meta, self.tracker)

    async def login(self, meta: Meta, force: bool = False) -> bool:
        if self.api_upload and not force:
            return True
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if cookie_jar is None:
            return False
        self.session.cookies = cast(Any, cookie_jar)
        return await self._validate_cookie_session(force)

    async def _validate_cookie_session(self, force: bool) -> bool:
        try:
            response = await self._login_probe(force)
        except httpx.RequestError as error:
            logger.info(f"{self.tracker}: [bold red]Error while validating credentials for '{self.tracker}': {error}[/bold red]")
            return False
        if self._login_probe_success(response, force):
            logger.debug(f"{self.tracker}: [bold green]Logged in to '{self.tracker}' with cookies.[/bold green]")
            return True
        logger.info(f"{self.tracker}: [bold red]Login to '{self.tracker}' with cookies failed. Please check your cookies.[/bold red]")
        return False

    async def _login_probe(self, force: bool) -> httpx.Response:
        url = f"{self.base_url}/torrents/browse/index" if force else self.http_upload_url
        return await self.session.get(url, timeout=10)

    @staticmethod
    def _login_probe_success(response: httpx.Response, force: bool) -> bool:
        expected_status = 301 if force else 200
        expected_path = "torrents/browse" if force else "torrents/upload"
        return response.status_code == expected_status and expected_path in str(response.url)

    async def generate_description(self, meta: Meta) -> str:
        builder = DescriptionBuilder(self.tracker, self.config)
        process_screenshot = not self.tracker_config.get("img_rehost", True) or self.tracker_config.get("api_upload", True)
        return await builder.general_description_generator(
            meta,
            audio_spectrogram=process_screenshot,
            menu_screenshots=process_screenshot,
            screenshots=process_screenshot,
            signature=f"""<div style="text-align: right; font-size: 11px;"><a href="https://github.com/wastaken7/Upload-Assistant">{meta.ua_signature}</a></div>""",
        )

    def get_category(self, meta: Meta) -> int:
        if meta.anime:
            return 34
        resolver = {
            "MOVIE": self._movie_category,
            "TV": self._tv_category,
            "BOOK": self._book_category,
            "GAME": self._game_category,
            "MUSIC": lambda _meta: 31,
        }.get(str(meta.category))
        return 0 if resolver is None else resolver(meta)

    @classmethod
    def _movie_category(cls, meta: Meta) -> int:
        language_or_genre = cls._movie_language_genre_category(meta)
        if language_or_genre is not None:
            return language_or_genre
        source = cls._movie_source_category(meta)
        return 0 if source is None else source

    @staticmethod
    def _movie_language_genre_category(meta: Meta) -> int | None:
        if str(meta.original_language) != "en":
            return 36
        if "Documentary" in str(meta.genres):
            return 29
        if meta.resolution == "2160p":
            return 47
        return None

    @classmethod
    def _movie_source_category(cls, meta: Meta) -> int | None:
        disc = cls._movie_disc_category(meta)
        if disc is not None:
            return disc
        encode = cls._movie_encode_category(meta)
        if encode is not None:
            return encode
        return cls._movie_web_hdtv_category(meta)

    @classmethod
    def _movie_disc_category(cls, meta: Meta) -> int | None:
        if cls._is_bluray_disc(meta):
            return 13
        if cls._is_dvd_disc(meta):
            return 12
        return None

    @staticmethod
    def _is_bluray_disc(meta: Meta) -> bool:
        return meta.is_disc in {"BDMV", "HDDVD"} or (str(meta.type) == "REMUX" and str(meta.source) in {"BluRay", "HDDVD"})

    @staticmethod
    def _is_dvd_disc(meta: Meta) -> bool:
        return meta.is_disc == "DVD" or (str(meta.type) == "REMUX" and "DVD" in str(meta.source))

    @classmethod
    def _movie_encode_category(cls, meta: Meta) -> int | None:
        release_type = str(meta.type)
        if release_type == "DVDRIP":
            return 11
        if release_type != "ENCODE":
            return None
        return cls._encoded_source_category(str(meta.source))

    @staticmethod
    def _encoded_source_category(source: str) -> int | None:
        if source in {"BluRay", "HDDVD"}:
            return 14
        return 11 if "DVD" in source else None

    @staticmethod
    def _movie_web_hdtv_category(meta: Meta) -> int | None:
        if "WEB" in str(meta.type):
            return 37
        if str(meta.type) == "HDTV":
            return 43
        return None

    @staticmethod
    def _tv_category(meta: Meta) -> int:
        if str(meta.original_language) != "en":
            return 44
        if meta.tv_pack:
            return 27
        return 26 if meta.sd else 32

    @staticmethod
    def _book_category(meta: Meta) -> int:
        return 46 if meta.comic or meta.manga else 45

    @staticmethod
    def _game_category(meta: Meta) -> int:
        mapping = {
            "x360": 19,
            "xone": 40,
            "xbox": 18,
            "pc": 17,
            "ps5": 49,
            "ps4": 39,
            "ps3": 21,
            "ps2": 20,
            "psp": 22,
            "wii": 28,
            "nds": 30,
            "switch": 48,
            "mac": 42,
        }
        return mapping.get(str(meta.platform).lower(), 17)

    def get_screens(self, meta: Meta) -> list[str]:
        images = cast(list[dict[str, Any]], meta.menu_images) + meta.image_list + meta.spectrograms_images + meta.dynamic_hdr_plot_images
        return [image["raw_url"] for image in images if image.get("raw_url")]

    async def get_name(self, meta: Meta) -> str:
        tl_name = meta.name.replace(meta.aka, "")
        return re.sub(r"\s{2,}", " ", tl_name)

    async def search_existing(self, meta: Meta) -> list[dict[str, Any]]:
        if not await self.login(meta, force=True):
            meta.skipping = self.tracker
            logger.debug(f"{self.tracker}: [bold red]Skipping upload to '{self.tracker}' as login failed.[/bold red]")
            return []
        forbidden = self._forbidden_search_keywords(meta)
        urls = self._search_urls(meta, self.get_category(meta))
        results: list[dict[str, Any]] = []
        for url in urls:
            results.extend(await self._search_url(url, forbidden))
        return results

    @staticmethod
    def _forbidden_search_keywords(meta: Meta) -> list[str]:
        keywords: list[str] = []
        if str(meta.is_disc or "").strip().lower() == "bdmv":
            keywords.extend(("remux", "x264", "x265"))
        if str(meta.type or "").strip().lower() == "webdl":
            keywords.extend(("webrip", "bluray", "blu-ray"))
        return keywords

    @classmethod
    def _search_urls(cls, meta: Meta, category_id: int) -> list[str]:
        query = cls._search_query(meta)
        if meta.category == "TV":
            return cls._tv_search_urls(meta, category_id, query)
        if meta.category == "MOVIE":
            return [cls._browse_url(category_id, f"{query} {meta.year or ''} {meta.resolution}".strip())]
        if meta.category in {"BOOK", "GAME", "MUSIC"}:
            return [cls._browse_url(category_id, query)]
        return []

    @staticmethod
    def _search_query(meta: Meta) -> str:
        return str(meta.title or "")

    @classmethod
    def _tv_search_urls(cls, meta: Meta, category_id: int, query: str) -> list[str]:
        season = str(meta.season)
        if meta.tv_pack:
            return [cls._browse_url(category_id, f"{query} {season} {meta.resolution}".strip())]
        season_episode = f"{season}{meta.episode}" if season or meta.episode else ""
        episode = cls._browse_url(category_id, f"{query} {season_episode} {meta.resolution}".strip())
        pack_category = 44 if category_id == 44 else 27
        pack = cls._browse_url(pack_category, f"{query} {season} {meta.resolution}".strip())
        return [episode, pack]

    @classmethod
    def _browse_url(cls, category_id: int, query: str) -> str:
        return f"{cls.base_url}/torrents/browse/list/categories/{category_id}/query/{query}"

    async def _search_url(self, url: str, forbidden_keywords: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        response = await self.session.get(url, timeout=20)
        response.raise_for_status()

        data = cast(dict[str, Any], response.json())
        torrents = cast(list[dict[str, Any]], data.get("torrentList", []))

        for torrent in torrents:
            name = str(torrent.get("name", ""))
            link = f"{self.torrent_url}{torrent.get('fid')}"
            size = torrent.get("size")
            if not any(keyword in name.lower() for keyword in forbidden_keywords):
                results.append({"name": name, "size": size, "link": link})

        return results

    async def upload(self, meta: Meta) -> bool | None:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)

        if self.api_upload:
            return await self.upload_api(meta)
        return await self.cookie_upload(meta)

    async def upload_api(self, meta: Meta) -> bool:
        files = await self._api_torrent_file(meta)
        data = await self._api_upload_data(meta)
        if meta.debug:
            return await self._debug_api_upload(meta, data)
        response = await self.session.post(url=self.api_upload_url, files=files, data=data)
        return await self._handle_api_upload_response(meta, response)

    async def _api_torrent_file(self, meta: Meta) -> dict[str, tuple[Any, Any, str]]:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        async with aiofiles.open(path, "rb") as handle:
            payload = await handle.read()
        return {"torrent": (f"{await self.get_name(meta)}.torrent", payload, "application/x-bittorrent")}

    async def _api_upload_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "announcekey": self.passkey,
            "category": self.get_category(meta),
            "description": await self.generate_description(meta),
            "name": await self.get_name(meta),
            "nonscene": "on" if not meta.scene else "off",
        }
        self._apply_api_external_ids(data, meta)
        if self._anonymous(meta):
            data["is_anonymous_upload"] = "on"
        return data

    @classmethod
    def _apply_api_external_ids(cls, data: dict[str, Any], meta: Meta) -> None:
        if cls._apply_anime_id(data, meta):
            return
        cls._apply_movie_id(data, meta)
        cls._apply_tv_id(data, meta)

    @staticmethod
    def _apply_anime_id(data: dict[str, Any], meta: Meta) -> bool:
        if not meta.anime or meta.mal_id == 0:
            return False
        data["animeid"] = f"https://anilist.co/anime/{meta.mal_id}"
        return True

    @staticmethod
    def _apply_movie_id(data: dict[str, Any], meta: Meta) -> None:
        if meta.category != "MOVIE":
            return
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        data["imdb"] = imdb.get("imdbID", "")

    @staticmethod
    def _apply_tv_id(data: dict[str, Any], meta: Meta) -> None:
        if meta.category == "TV":
            data.update({"tvmazeid": meta.tvmaze_id, "tvmazetype": meta.tv_pack})

    def _anonymous(self, meta: Meta) -> bool:
        return not (meta.anon == 0 and not self.tracker_config.get("anon", False))

    async def _debug_api_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.info(f"{self.tracker}: Request Data:")
        logger.info(Redaction.redact_private_info(data))
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _handle_api_upload_response(self, meta: Meta, response: httpx.Response) -> bool:
        if not response.text.isnumeric():
            meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "data error: " + response.text
            return False
        torrent_id = response.text
        status = meta.tracker_status.setdefault(self.tracker, {})
        status["status_message"] = "Torrent uploaded successfully."
        status["torrent_id"] = torrent_id
        await self.common.create_torrent_ready_to_seed(meta, self.tracker, self.source_flag, self.announce_list, self.torrent_url + torrent_id)
        return True

    async def get_cookie_upload_data(self, meta: Meta) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": await self.get_name(meta),
            "category": self.get_category(meta),
            "nonscene": "on" if not meta.scene else "off",
            "imdbURL": self._imdb_url(meta),
            "tvMazeURL": self._tvmaze_url(meta),
            "igdbURL": "",
            "torrentNFO": "0",
            "torrentDesc": "1",
            "nfotextbox": "",
            "torrentComment": "0",
            "uploaderComments": "",
            "is_anonymous_upload": "on" if self._anonymous(meta) else "off",
            "screenshots[]": self.get_screens(meta) if self.tracker_config.get("img_rehost", True) else "",
        }
        return data

    @staticmethod
    def _imdb_url(meta: Meta) -> str:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return str(imdb.get("imdb_url", ""))

    @staticmethod
    def _tvmaze_url(meta: Meta) -> str:
        if meta.category != "TV" or not meta.tvmaze_id:
            return ""
        return f"https://www.tvmaze.com/shows/{meta.tvmaze_id}"

    async def cookie_upload(self, meta: Meta) -> bool | None:
        description = await self.generate_description(meta)
        if not await self.login(meta):
            meta.tracker_status.setdefault(self.tracker, {})["status_message"] = "data error: Login with cookies failed."
            return None
        data = await self.get_cookie_upload_data(meta)
        if meta.debug:
            return await self._debug_cookie_upload(meta, data)
        return await self._submit_cookie_upload(meta, data, description)

    async def _debug_cookie_upload(self, meta: Meta, data: dict[str, Any]) -> bool:
        logger.debug(f"{self.tracker}: [cyan]Request Data:")
        logger.debug(Redaction.redact_private_info(data))
        await self.common.create_torrent_for_upload(meta, f"{self.tracker}_DEBUG", f"{self.tracker}_DEBUG", announce_url="https://fake.tracker")
        return True

    async def _submit_cookie_upload(self, meta: Meta, data: dict[str, Any], description: str) -> bool | None:
        try:
            response = await self._cookie_upload_response(meta, data, description)
        except httpx.RequestError as error:
            meta.tracker_status[self.tracker]["status_message"] = f"data error - {error!s}"
            return None
        return await self._handle_cookie_upload_response(meta, response)

    async def _cookie_upload_response(self, meta: Meta, data: dict[str, Any], description: str) -> httpx.Response:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}].torrent"
        async with aiofiles.open(path, "rb") as handle:
            torrent_bytes = await handle.read()
        files: dict[str, tuple[str, bytes | str, str]] = {
            "torrent": ("torrent.torrent", torrent_bytes, "application/x-bittorrent"),
            "nfo": ("description.txt", description, "text/plain"),
        }
        return await self.session.post(url=self.http_upload_url, files=files, data=data)

    async def _handle_cookie_upload_response(self, meta: Meta, response: httpx.Response) -> bool:
        if response.status_code == 302 and "location" in response.headers:
            return await self._record_cookie_upload(meta, response.headers["location"])
        meta.tracker_status[self.tracker]["status_message"] = "data error - Upload failed: No success redirect found."
        failure_path = await self.common.save_html_file(meta, self.tracker, response.text, "Failed_Upload")
        logger.info(f"{self.tracker}: Failed upload. The HTML response saved to {failure_path}")
        return False

    async def _record_cookie_upload(self, meta: Meta, location: str) -> bool:
        torrent_id = location.replace("/successfulupload?torrentID=", "")
        torrent_url = f"{self.base_url}/torrent/{torrent_id}"
        status = meta.tracker_status[self.tracker]
        status["status_message"] = "Torrent uploaded successfully."
        status["torrent_id"] = torrent_id
        await self.common.create_torrent_ready_to_seed(meta, self.tracker, self.source_flag, self.announce_list, torrent_url)
        return True
