# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import platform
from typing import Any, ClassVar, cast

import aiofiles
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.external_apis.tmdb import TmdbManager
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import (
    CookieAuthUploader,
    CookieValidator,
)
from src.integrations.trackers.description_builder import DescriptionBuilder

Config = dict[str, Any]


class NEXUSPHP:
    auth_type = "cookies"
    supported_categories: tuple[str, ...] = ("TV", "MOVIE")
    tracker: str = ""
    source_flag: str = ""
    banned_groups: tuple[str, ...] = ()
    base_url: str = ""
    search_url: str = ""
    torrent_url: str = ""
    upload_url: str = ""
    tmdb_localization_requirements: ClassVar = {
        "zh-cn": {
            "main": "credits",
        }
    }

    def __init__(self, config: dict[str, Any], tracker_name: str):
        self.common = Common(config)
        self.config = config
        self.cookie_auth_uploader = CookieAuthUploader(config)
        self.cookie_validator = CookieValidator(config)
        self.tmdb_manager = TmdbManager(config)
        self.tracker = tracker_name
        self.tracker_config: dict[str, Any] = self.config["TRACKERS"].get(
            self.tracker, {}
        )

        # Normalize announce_url: must be a non-empty string after stripping
        raw_announce = self.tracker_config.get("announce_url")
        self.announce_url = (
            raw_announce.strip() if isinstance(raw_announce, str) else ""
        )

        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": f"Upload-Assistant ({platform.system()} {platform.release()})"
            },
            timeout=60.0,
        )

    async def load_localized_data(self, meta: Meta) -> None:
        data = meta.tmdb_localized_data
        zh_cn_data = data.get("zh-cn")
        if not zh_cn_data or not zh_cn_data.get("main"):
            raise RuntimeError(
                f"{self.tracker}: Missing TMDB localized data (zh-cn)."
            )

        self.tmdb_data = zh_cn_data.get("main") or {}
        return

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        if not self._search_is_configured(meta):
            return []
        await self._load_search_cookies(meta)
        response = await self._search_response(meta)
        if await self._search_requires_login(meta, response):
            return []
        response.raise_for_status()
        return await self._parse_search_results(meta, response.text)

    def _search_is_configured(self, meta: Meta) -> bool:
        if self.announce_url:
            return True
        logger.info(
            f"{self.tracker}: [red]Announce URL is not set for {self.tracker}[/red]",
            extra={"markup": True},
        )
        meta.skipping = self.tracker
        return False

    async def _load_search_cookies(self, meta: Meta) -> None:
        cookies = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookies:
            self.session.cookies.update(cookies)

    async def _search_response(self, meta: Meta) -> httpx.Response:
        return await self.session.get(
            f"{self.base_url}/torrents.php", params=self._search_params(meta)
        )

    def _search_params(self, meta: Meta) -> dict[str, str]:
        return {
            f"cat{self.get_category(meta)}": "1",
            f"medium{self.get_type(meta)}": "1",
            f"standard{self.get_resolution(meta)}": "1",
            "incldead": "0",
            "search": self._search_name(meta),
        }

    @classmethod
    def _search_name(cls, meta: Meta) -> str:
        if meta.category == "MOVIE":
            return cls._movie_search_name(meta)
        return cls._tv_search_name(meta)

    @staticmethod
    def _movie_search_name(meta: Meta) -> str:
        year = "" if meta.year is None else str(meta.year)
        return f"{meta.title} {year}".strip()

    @staticmethod
    def _tv_search_name(meta: Meta) -> str:
        if meta.tv_pack:
            return f"{meta.title} {meta.season}".strip()
        season_episode = (
            f"{meta.season}{meta.episode}"
            if meta.season or meta.episode
            else ""
        )
        return f"{meta.title} {season_episode}".strip()

    async def _search_requires_login(
        self, meta: Meta, response: httpx.Response
    ) -> bool:
        if (
            "login.php" not in str(response.url)
            and "login.php" not in response.text
        ):
            return False
        await self.cookie_validator.handle_validation_failure(
            meta, self.tracker, response.text
        )
        meta.skipping = self.tracker
        return True

    async def _parse_search_results(
        self, meta: Meta, html: str
    ) -> list[dict[str, str]]:
        rows = self._search_rows(html)
        results: list[dict[str, str]] = []
        for row in rows:
            entry = await self._search_row(meta, row)
            if entry is not None:
                results.append(entry)
        return results

    @staticmethod
    def _search_rows(html: str) -> list[Any]:
        table = BeautifulSoup(html, "html.parser").find(
            "table", class_="torrents"
        )
        if table is None:
            return []
        return table.find_all("tr", recursive=False)[1:]

    async def _search_row(self, meta: Meta, row: Any) -> dict[str, str] | None:
        link = self._torrent_name_link(row)
        if link is None:
            return None
        torrent_id = self._torrent_id_from_link(link)
        if not torrent_id:
            return None
        entry = {
            "name": self._torrent_name(link),
            "link": f"{self.base_url}/details.php?id={torrent_id}",
        }
        if meta.is_disc == "BDMV":
            bdinfo = await self.get_dupe_bdinfo(torrent_id)
            if bdinfo:
                entry["bd_info"] = bdinfo
        return entry

    @staticmethod
    def _torrent_name_link(row: Any) -> Any | None:
        name_table = row.find("table", class_="torrentname")
        if name_table is None:
            return None
        return name_table.find(
            "a", href=lambda value: bool(value and "details.php?id=" in value)
        )

    @staticmethod
    def _torrent_id_from_link(link: Any) -> str:
        href = link.get("href")
        if isinstance(href, list):
            href = href[0] if href else ""
        if not isinstance(href, str) or "id=" not in href:
            return ""
        return href.split("id=", 1)[1].split("&", 1)[0]

    @staticmethod
    def _torrent_name(link: Any) -> str:
        value = link.get("title")
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
        return str(value or link.get_text(strip=True))

    async def get_dupe_bdinfo(self, torrent_id: str) -> str:
        try:
            bdinfo_url = f"{self.base_url}/details.php?id={torrent_id}"
            response = await self.session.get(bdinfo_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            bdinfo_div = soup.find("div", class_="nexus-media-info-raw")
            if bdinfo_div:
                pre_tag = bdinfo_div.find("pre")
                if pre_tag:
                    return pre_tag.get_text(strip=True)

            return ""

        except Exception as e:
            logger.info(
                f"{self.tracker}: Error getting BDInfo for torrent {torrent_id}: {e}",
                extra={"markup": False},
            )
            return ""

    async def validate_credentials(self, meta: Meta) -> bool:
        cookies = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        if cookies:
            self.session.cookies.update(cookies)
            return True
        return False

    async def standard_desc(self, meta: Meta) -> str:
        data = getattr(self, "tmdb_data", {})
        if not data:
            return ""
        lines: list[str] = []
        self._append_poster(lines, data)
        name = self._localized_title(meta, data)
        self._append_identity(lines, meta, data, name)
        self._append_ratings(lines, meta)
        self._append_runtime(lines, meta, data)
        self._append_people(lines, data)
        self._append_overview(lines, data)
        return "\n".join(lines)

    @staticmethod
    def _append_poster(lines: list[str], data: dict[str, Any]) -> None:
        poster_path = data.get("poster_path")
        if poster_path:
            lines.extend(
                (
                    f"[img]https://image.tmdb.org/t/p/w500{poster_path}[/img]",
                    "",
                )
            )

    @classmethod
    def _localized_title(cls, meta: Meta, data: dict[str, Any]) -> str:
        name = str(data.get("name", ""))
        if meta.category != "TV" or not meta.season:
            return name
        return cls._season_title(name, meta.season, data.get("seasons", []))

    @classmethod
    def _season_title(cls, name: str, season: Any, raw_seasons: Any) -> str:
        season_info = cls._season_info(raw_seasons, season)
        season_name = str(season_info.get("name", ""))
        default_name = f"第 {season} 季"
        addition = (
            default_name
            if not season_name or season_name == default_name
            else season_name
        )
        return name if addition in name else f"{name} {addition}".strip()

    @staticmethod
    def _season_info(raw_seasons: Any, season: Any) -> dict[str, Any]:
        seasons = raw_seasons if isinstance(raw_seasons, list) else []
        return next(
            (
                cast(dict[str, Any], item)
                for item in seasons
                if isinstance(item, dict)
                and item.get("season_number") == season
            ),
            {},
        )

    @classmethod
    def _append_identity(
        cls, lines: list[str], meta: Meta, data: dict[str, Any], name: str
    ) -> None:
        lines.append(f"◎片　　名　{name}")
        cls._append_original_name(lines, data, name)
        release_date = cls._release_date(data)
        cls._append_year_country_genre_language(
            lines, meta, data, release_date
        )

    @staticmethod
    def _append_original_name(
        lines: list[str], data: dict[str, Any], name: str
    ) -> None:
        original_name = str(data.get("original_name", ""))
        if original_name and original_name != name:
            lines.append(f"◎译　　名　{original_name}")

    @staticmethod
    def _release_date(data: dict[str, Any]) -> str:
        return str(
            data.get("first_air_date") or data.get("release_date") or ""
        )

    @classmethod
    def _append_year_country_genre_language(
        cls,
        lines: list[str],
        meta: Meta,
        data: dict[str, Any],
        release_date: str,
    ) -> None:
        cls._append_year(lines, meta, release_date)
        countries = cls._named_values(data.get("production_countries"))
        cls._append_localized_lists(lines, countries, data)
        cls._append_release_date(lines, release_date, countries)

    @staticmethod
    def _append_year(lines: list[str], meta: Meta, release_date: str) -> None:
        year = release_date[:4] if release_date else meta.year
        if year:
            lines.append(f"◎年　　代　{year}")

    @classmethod
    def _append_localized_lists(
        cls, lines: list[str], countries: list[str], data: dict[str, Any]
    ) -> None:
        cls._append_joined(lines, "◎产　　地　", countries)
        cls._append_joined(
            lines, "◎类　　别　", cls._named_values(data.get("genres"))
        )
        cls._append_joined(
            lines,
            "◎语　　言　",
            cls._named_values(data.get("spoken_languages")),
        )

    @staticmethod
    def _append_release_date(
        lines: list[str], release_date: str, countries: list[str]
    ) -> None:
        if not release_date:
            return
        country = countries[0] if countries else ""
        suffix = f"({country})" if country else ""
        lines.append(f"◎上映日期　{release_date}{suffix}")

    @staticmethod
    def _named_values(value: Any) -> list[str]:
        values = value if isinstance(value, list) else []
        return [
            str(item.get("name"))
            for item in values
            if isinstance(item, dict) and item.get("name")
        ]

    @staticmethod
    def _append_joined(
        lines: list[str], label: str, values: list[str]
    ) -> None:
        if values:
            lines.append(f"{label}{' / '.join(values)}")

    @classmethod
    def _append_ratings(cls, lines: list[str], meta: Meta) -> None:
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        cls._append_rating(
            lines, "◎IMDb评分  ", imdb.get("rating"), imdb.get("votes")
        )
        imdb_url = imdb.get("imdb_url")
        if imdb_url:
            lines.append(f"◎IMDb链接  {imdb_url}/")
        cls._append_rating(
            lines, "◎豆瓣评分　", meta.douban_rating, meta.douban_votes
        )
        if meta.douban_id:
            lines.append(
                f"◎豆瓣链接　https://movie.douban.com/subject/{meta.douban_id}/"
            )

    @staticmethod
    def _append_rating(
        lines: list[str], label: str, rating: Any, votes: Any
    ) -> None:
        if not rating:
            return
        votes_text = f" ({votes} 人评价)" if votes else ""
        lines.append(f"{label}{rating}/10{votes_text}")

    @classmethod
    def _append_runtime(
        cls, lines: list[str], meta: Meta, data: dict[str, Any]
    ) -> None:
        if meta.category == "TV":
            cls._append_tv_runtime(lines, meta, data)
            return
        runtime = data.get("runtime") or meta.runtime
        if runtime:
            lines.append(f"◎片　　长　{runtime}分钟")

    @classmethod
    def _append_tv_runtime(
        cls, lines: list[str], meta: Meta, data: dict[str, Any]
    ) -> None:
        if meta.season:
            season_info = cls._season_info(
                data.get("seasons", []), meta.season
            )
            episode_count = season_info.get("episode_count")
            if episode_count:
                lines.append(f"◎集　　数　{episode_count}")
        lines.append(f"◎季　　数　{meta.season}")
        runtime = cls._tv_runtime(data)
        if runtime:
            lines.append(f"◎片　　长　{runtime}分钟")

    @staticmethod
    def _tv_runtime(data: dict[str, Any]) -> Any:
        runtime = data.get("episode_run_time", [])
        values = runtime if isinstance(runtime, list) else []
        if values and values[0]:
            return values[0]
        last_episode = data.get("last_episode_to_air")
        return (
            last_episode.get("runtime")
            if isinstance(last_episode, dict)
            else None
        )

    @classmethod
    def _append_people(cls, lines: list[str], data: dict[str, Any]) -> None:
        credits = data.get("credits", {})
        credits = credits if isinstance(credits, dict) else {}
        crew = (
            credits.get("crew", [])
            if isinstance(credits.get("crew", []), list)
            else []
        )
        directors = cls._crew_names(crew, {"Director"})
        writers = list(
            dict.fromkeys(
                cls._crew_names(crew, {"Writer", "Screenplay", "Author"})
            )
        )
        cls._append_joined(lines, "◎导　　演　", directors)
        cls._append_joined(lines, "◎编　　剧　", writers)
        cls._append_cast(lines, credits.get("cast", []))

    @staticmethod
    def _crew_names(crew: list[Any], jobs: set[str]) -> list[str]:
        return [
            NEXUSPHP._person_name(item)
            for item in crew
            if isinstance(item, dict) and item.get("job") in jobs
        ]

    @staticmethod
    def _person_name(person: dict[str, Any]) -> str:
        return f"{person.get('name')} {person.get('original_name')}"

    @classmethod
    def _append_cast(cls, lines: list[str], raw_cast: Any) -> None:
        for index, actor in enumerate(cls._cast_people(raw_cast)):
            lines.append(f"{cls._cast_prefix(index)}{cls._actor_name(actor)}")

    @staticmethod
    def _cast_people(raw_cast: Any) -> list[dict[str, Any]]:
        values = raw_cast if isinstance(raw_cast, list) else []
        return [
            cast(dict[str, Any], item)
            for item in values[:25]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _cast_prefix(index: int) -> str:
        return "◎主　　演　" if index == 0 else "　　　　　　"

    @classmethod
    def _actor_name(cls, actor: dict[str, Any]) -> str:
        name = cls._person_name(actor)
        character = actor.get("character")
        return f"{name} (饰 {character})" if character else name

    @staticmethod
    def _append_overview(lines: list[str], data: dict[str, Any]) -> None:
        overview = data.get("overview")
        if overview:
            lines.extend(("", "◎简　　介", "", f"　　{overview}"))

    async def get_description(self, meta: Meta) -> dict[str, str]:
        builder = DescriptionBuilder(self.tracker, self.config)
        meta.nexusphp_description = await self.standard_desc(meta)

        description = await builder.general_description_generator(
            meta,
            logo=False,
            mediainfo=False,
            nfo=False,
            signature=f"[right][url=https://github.com/wastaken7/Upload-Assistant][size=1]{meta.ua_signature}[/size][/url][/right]",
        )
        return {"descr": description}

    def get_category(self, meta: Meta) -> int:
        meta = meta
        raise NotImplementedError

    def get_type(self, meta: Meta) -> int:
        meta = meta
        raise NotImplementedError

    def get_codec(self, meta: Meta) -> int:
        meta = meta
        raise NotImplementedError

    def get_resolution(self, meta: Meta) -> int:
        meta = meta
        raise NotImplementedError

    def get_group_tag(self, meta: Meta) -> int:
        meta = meta
        return 0

    def get_checkboxes(self, meta: Meta) -> list[str]:
        meta = meta
        return []

    def get_audio_codec(self, meta: Meta) -> int:
        meta = meta
        return 0

    def get_douban_url(self, meta: Meta) -> str:
        if meta.douban_id:
            return f"https://movie.douban.com/subject/{meta.douban_id}/"
        return ""

    def get_imdb_url(self, meta: Meta) -> str:
        if meta.imdb_id:
            return f"{meta.imdb_info.get('imdb_url', '')}"
        return ""

    def get_region(self, meta: Meta) -> int:
        meta = meta
        return 0

    def get_container(self, meta: Meta) -> int:
        meta = meta
        return 0

    async def get_technical_info(self, meta: Meta) -> dict[str, str]:
        filename = (
            "BD_SUMMARY_00.txt"
            if meta.is_disc == "BDMV"
            else "MEDIAINFO_CLEANPATH.txt"
        )
        path = release_temp_dir(meta.base_dir, meta.uuid) / filename
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return {"technical_info": await handle.read()}

    async def get_name(self, meta: Meta) -> dict[str, str]:
        return {"name": meta.name}

    async def get_category_data(self, meta: Meta) -> dict[str, int]:
        return {"type": self.get_category(meta)}

    async def get_type_data(self, meta: Meta) -> dict[str, int]:
        return {"medium_sel[4]": self.get_type(meta)}

    async def get_codec_data(self, meta: Meta) -> dict[str, int]:
        return {"codec_sel[4]": self.get_codec(meta)}

    async def get_resolution_data(self, meta: Meta) -> dict[str, int]:
        return {"standard_sel[4]": self.get_resolution(meta)}

    async def get_group_tag_data(self, meta: Meta) -> dict[str, int]:
        group_tag = self.get_group_tag(meta)
        return {"team_sel[4]": group_tag} if group_tag else {}

    async def get_checkboxes_data(self, meta: Meta) -> dict[str, list[str]]:
        checkboxes = self.get_checkboxes(meta)
        return {"tags[4][]": checkboxes} if checkboxes else {}

    async def get_anonymous_data(self, meta: Meta) -> dict[str, str]:
        anonymous = not (
            meta.anon == 0 and not self.tracker_config.get("anon", False)
        )
        return {"uplver": "yes"} if anonymous else {}

    async def get_imdb_data(self, meta: Meta) -> dict[str, str]:
        imdb_url = self.get_imdb_url(meta)
        return {"url": imdb_url} if imdb_url else {}

    async def get_douban_data(self, meta: Meta) -> dict[str, str]:
        douban_url = self.get_douban_url(meta)
        return {"pt_gen": douban_url} if douban_url else {}

    async def get_audio_codec_data(self, meta: Meta) -> dict[str, int]:
        audio = self.get_audio_codec(meta)
        return {"audiocodec_sel[4]": audio} if audio else {}

    async def get_region_data(self, meta: Meta) -> dict[str, int]:
        region = self.get_region(meta)
        return {"source_sel[4]": region} if region else {}

    async def get_container_data(self, meta: Meta) -> dict[str, int]:
        container = self.get_container(meta)
        return {"processing_sel[4]": container} if container else {}

    async def get_data(self, meta: Meta) -> dict[str, Any]:
        await self.load_localized_data(meta)
        results = await asyncio.gather(
            self.get_name(meta),
            self.get_description(meta),
            self.get_technical_info(meta),
            self.get_category_data(meta),
            self.get_type_data(meta),
            self.get_codec_data(meta),
            self.get_resolution_data(meta),
            self.get_group_tag_data(meta),
            self.get_checkboxes_data(meta),
            self.get_anonymous_data(meta),
            self.get_imdb_data(meta),
            self.get_douban_data(meta),
            self.get_audio_codec_data(meta),
            self.get_region_data(meta),
            self.get_container_data(meta),
        )

        data: dict[str, Any] = {
            "color": 0,
            "font": 0,
            "size": 0,
            "small_descr": self.common.get_small_description(meta),
        }
        for result in results:
            data.update(result)
        return data

    async def upload(self, meta: Meta) -> bool:
        cookies = await self.cookie_validator.load_session_cookies(
            meta, self.tracker
        )
        self.session.cookies.clear()
        if cookies is not None:
            self.session.cookies.update(cookies)
        data = await self.get_data(meta)

        return await self.cookie_auth_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            id_pattern=r"download\.php\?id=(\d+)",
            data=data,
            torrent_field_name="file",
            upload_cookies=self.session.cookies,
            upload_url=f"{self.base_url}/takeupload.php",
            success_text="download.php?id=",
        )
