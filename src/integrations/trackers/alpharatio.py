# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import httpx
from bs4 import BeautifulSoup

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import release_temp_dir
from src.integrations.media.media_info import MediaInfo
from src.integrations.observability.runtime_support import logger, prompt_in_thread
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator


class AlphaRatio:
    """
    AR Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "ALPHARATIO"
    display_name = "AlphaRatio"
    allows_bloated_audio = True
    source_flag = "AlphaRatio"
    base_url = "https://alpharatio.cc"
    banned_groups = ()
    login_url = f"{base_url}/login.php"
    upload_url = f"{base_url}/upload.php"
    search_url = f"{base_url}/torrents.php"
    test_url = f"{base_url}/torrents.php"
    torrent_url = f"{base_url}/torrents.php?id="
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("tracker.alpharatio",)

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config)
        self.cookie_validator = CookieValidator(config)
        self.cookie_uploader = CookieAuthUploader(config)
        trackers_cfg = cast(dict[str, Any], self.config.get("TRACKERS", {}))
        ar_cfg = cast(dict[str, Any], trackers_cfg.get("ALPHARATIO", {}))
        self.username = str(ar_cfg.get("username", "")).strip()
        self.password = str(ar_cfg.get("password", "")).strip()

    async def get_type(self, meta: Meta) -> str:
        if self._is_bluray_disc_or_remux(meta):
            return "14"
        if meta.anime:
            return self._anime_type(meta)
        if meta.category == "TV":
            return self._tv_type(meta)
        if meta.category == "MOVIE":
            return self._movie_type(meta)
        return "7"

    @staticmethod
    def _is_bluray_disc_or_remux(meta: Meta) -> bool:
        return meta.type in {"DISC", "REMUX"} and meta.source == "Blu-ray"

    @staticmethod
    def _anime_type(meta: Meta) -> str:
        if meta.sd:
            return "15"
        resolutions = {"8640p", "4320p", "2160p", "1440p", "1080p", "1080i", "720p"}
        return "16" if meta.resolution in resolutions else "15"

    @classmethod
    def _tv_type(cls, meta: Meta) -> str:
        if meta.tv_pack:
            return cls._tv_pack_type(meta)
        return cls._tv_episode_type(meta)

    @staticmethod
    def _tv_pack_type(meta: Meta) -> str:
        if meta.sd:
            return "4"
        if meta.resolution in {"8640p", "4320p", "2160p"}:
            return "6"
        return "5" if meta.resolution in {"1440p", "1080p", "1080i", "720p"} else "4"

    @staticmethod
    def _tv_episode_type(meta: Meta) -> str:
        if meta.sd:
            return "0"
        if meta.resolution in {"8640p", "4320p", "2160p"}:
            return "2"
        return "1" if meta.resolution in {"1440p", "1080p", "1080i", "720p"} else "0"

    @staticmethod
    def _movie_type(meta: Meta) -> str:
        if meta.sd:
            return "7"
        if meta.adult_media:
            return "13"
        if meta.resolution in {"8640p", "4320p", "2160p"}:
            return "9"
        return "8" if meta.resolution in {"1440p", "1080p", "1080i", "720p"} else "7"

    async def validate_credentials(self, meta: Meta) -> bool:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        return cookie_jar is not None

    def get_links(self, movie: Meta, subheading: str, heading_end: str) -> str:
        heading = f"\n{subheading}Links{heading_end}\n"
        images = self.config.get("IMAGES")
        if isinstance(images, dict):
            links = self._image_metadata_links(movie, cast(dict[str, Any], images))
            return heading + "".join(links)
        return heading + "".join(self._plain_metadata_links(movie))

    @classmethod
    def _image_metadata_links(cls, movie: Meta, images: dict[str, Any]) -> list[str]:
        values = cls._metadata_urls(movie)
        result: list[str] = []
        for key, url in values:
            image = str(images.get(f"{key}_75", ""))
            if url and image:
                prefix = "" if not result else " "
                result.append(f"{prefix}[url={url}][img]{image}[/img][/url]")
        return result

    @classmethod
    def _plain_metadata_links(cls, movie: Meta) -> list[str]:
        urls = [url for _key, url in cls._metadata_urls(movie) if url]
        if not urls:
            return []
        return [urls[0], *[f"\n{url}" for url in urls[1:]]]

    @classmethod
    def _metadata_urls(cls, movie: Meta) -> list[tuple[str, str]]:
        return [
            ("imdb", cls._imdb_url(movie)),
            ("tmdb", cls._tmdb_url(movie)),
            ("tvdb", cls._tvdb_url(movie)),
            ("tvmaze", cls._tvmaze_url(movie)),
            ("mal", cls._mal_url(movie)),
        ]

    @staticmethod
    def _imdb_url(movie: Meta) -> str:
        if movie.imdb_id in {None, 0} or not isinstance(movie.imdb_info, dict):
            return ""
        return str(movie.imdb_info.get("imdb_url", ""))

    @staticmethod
    def _tmdb_url(movie: Meta) -> str:
        return f"https://www.themoviedb.org/{str(movie.category).lower()}/{movie.tmdb}" if movie.tmdb else ""

    @staticmethod
    def _tvdb_url(movie: Meta) -> str:
        return f"https://www.thetvdb.com/?id={movie.tvdb_id}&tab=series" if movie.tvdb_id not in {None, 0} else ""

    @staticmethod
    def _tvmaze_url(movie: Meta) -> str:
        return f"https://www.tvmaze.com/shows/{movie.tvmaze_id}" if movie.tvmaze_id not in {None, 0} else ""

    @staticmethod
    def _mal_url(movie: Meta) -> str:
        return f"https://myanimelist.net/anime/{movie.mal_id}" if movie.mal_id not in {None, 0} else ""

    async def edit_desc(self, meta: Meta) -> None:
        heading = "[color=green][size=6]"
        subheading = "[color=red][size=4]"
        heading_end = "[/size][/color]"
        description = self._description_header(meta, heading, subheading, heading_end)
        discs = self._disc_entries(meta)
        if discs:
            description += self._disc_description(meta, discs)
        else:
            description += await self._file_description(meta, subheading, heading_end)
        await self._write_description(meta, description)

    def _description_header(self, meta: Meta, heading: str, subheading: str, heading_end: str) -> str:
        media_heading = "BDINFO" if meta.is_disc == "BDMV" else "MEDIAINFO"
        return f"{heading}{meta.name}{heading_end}\n{self.get_links(meta, subheading, heading_end)}\n\n{subheading}{media_heading}{heading_end}\n"

    @staticmethod
    def _disc_entries(meta: Meta) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], meta.discs) if isinstance(meta.discs, list) else []

    @classmethod
    def _disc_description(cls, meta: Meta, discs: list[dict[str, Any]]) -> str:
        if len(discs) >= 2:
            return "".join(cls._additional_disc_block(each) for each in discs[1:])
        return cls._single_disc_block(meta, discs[0])

    @staticmethod
    def _additional_disc_block(disc: dict[str, Any]) -> str:
        if disc.get("type") == "BDMV":
            return f"[hide={disc.get('name', 'BDINFO')}][code]{disc.get('summary', '')}[/code][/hide]\n\n"
        if disc.get("type") == "DVD":
            return (
                f"{disc.get('name', '')}:\n"
                f"[hide={Path(str(disc.get('vob', ''))).name}][code]{disc.get('vob_mi', '')}[/code][/hide] "
                f"[hide={Path(str(disc.get('ifo', ''))).name}][code]{disc.get('ifo_mi', '')}[/code][/hide]\n\n"
            )
        return ""

    @staticmethod
    def _single_disc_block(meta: Meta, disc: dict[str, Any]) -> str:
        if disc.get("type") == "DVD":
            return f"[hide][code]{disc.get('vob_mi', '')}[/code][/hide]\n\n"
        if meta.is_disc == "BDMV":
            return f"[hide][code]{disc.get('summary', '')}[/code][/hide]\n\n"
        return ""

    async def _file_description(self, meta: Meta, subheading: str, heading_end: str) -> str:
        description = await self._mediainfo_description(meta)
        description += self._narrative_description(meta, subheading, heading_end)
        return description

    async def _mediainfo_description(self, meta: Meta) -> str:
        video = self._video_path(meta)
        template = Path(meta.base_dir) / "data" / "templates" / "summary-mediainfo.csv"
        if template.exists():
            return await self._templated_mediainfo(meta, video, template)
        logger.info(f"{self.tracker}: [bold red]Couldn't find the MediaInfo template")
        logger.info(f"{self.tracker}: [green]Using normal MediaInfo for the description.")
        cleaned = await self._clean_mediainfo_text(meta)
        return f"[code]\n{cleaned}\n[/code]\n\n"

    @staticmethod
    def _video_path(meta: Meta) -> str:
        files = cast(list[str], meta.filelist) if isinstance(meta.filelist, list) else []
        return files[0] if files else str(meta.path or "")

    async def _templated_mediainfo(self, meta: Meta, video: str, template: Path) -> str:
        media_info = await self.parse_mediainfo_async(video, str(template.resolve()))
        full_mediainfo = await self._clean_mediainfo_text(meta)
        return f"[code]\n{media_info}\n[/code]\n[hide=FULL MEDIAINFO][code]{full_mediainfo}[/code][/hide]\n"

    @staticmethod
    async def _clean_mediainfo_text(meta: Meta) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / "MEDIAINFO_CLEANPATH.txt"
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    def _narrative_description(self, meta: Meta, subheading: str, heading_end: str) -> str:
        sections = [f"\n\n{subheading}PLOT{heading_end}\n{meta.overview}"]
        if meta.genres:
            sections.append(f"\n\n{subheading}Genres{heading_end}\n{meta.genres}")
        screenshots = self._screenshot_block(meta, subheading, heading_end)
        if screenshots:
            sections.append(screenshots)
        if "youtube" in meta:
            sections.append(f"\n\n{subheading}Youtube{heading_end}\n{meta.youtube}")
        notes = self._base_notes(meta)
        if len(notes) > 2:
            sections.append(f"\n\n{subheading}Notes{heading_end}\n{notes}")
        return "".join(sections)

    @classmethod
    def _screenshot_block(cls, meta: Meta, subheading: str, heading_end: str) -> str:
        links = cls._screenshot_links(meta.image_list)
        if not links:
            return ""
        return f"\n\n{subheading}Screenshots{heading_end}\n[align=center]{''.join(links)}[/align]"

    @classmethod
    def _screenshot_links(cls, value: Any) -> list[str]:
        images = value if isinstance(value, list) else []
        return [link for image in images if (link := cls._screenshot_link(image))]

    @staticmethod
    def _screenshot_link(image: Any) -> str:
        if not isinstance(image, dict):
            return ""
        raw_url = image.get("raw_url")
        img_url = image.get("img_url")
        if not raw_url or not img_url:
            return ""
        return f"[url={raw_url}][img]{img_url}[/img][/url]"

    @staticmethod
    def _base_notes(meta: Meta) -> str:
        from src.domain_models.release_description import base_description

        base = base_description(meta)
        base = re.sub(r"\[center\]\[spoiler=Scene NFO:\].*?\[/center\]", "", base, flags=re.DOTALL)
        return re.sub(r"\[center\]\[spoiler=FraMeSToR NFO:\].*?\[/center\]", "", base, flags=re.DOTALL)

    async def _write_description(self, meta: Meta, description: str) -> None:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf8") as handle:
            await handle.write(description)

    async def get_language_tag(self, meta: Meta) -> str:
        if meta.is_disc == "BDMV":
            return self._bdmv_language_tag(meta)
        return await self._mediainfo_language_tag(meta)

    @classmethod
    def _bdmv_language_tag(cls, meta: Meta) -> str:
        tracks = cls._bdmv_audio_tracks(meta)
        if cls._has_english_bdmv_audio(tracks):
            return ""
        return cls._first_bdmv_language(tracks)

    @staticmethod
    def _bdmv_audio_tracks(meta: Meta) -> list[dict[str, Any]]:
        bdinfo = meta.bdinfo if isinstance(meta.bdinfo, dict) else {}
        audio = bdinfo.get("audio", [])
        values = audio if isinstance(audio, list) else []
        return [cast(dict[str, Any], track) for track in values if isinstance(track, dict)]

    @staticmethod
    def _has_english_bdmv_audio(tracks: list[dict[str, Any]]) -> bool:
        return any(track.get("language") == "English" for track in tracks)

    @staticmethod
    def _first_bdmv_language(tracks: list[dict[str, Any]]) -> str:
        if not tracks:
            return ""
        return str(tracks[0].get("language", "")).upper()

    async def _mediainfo_language_tag(self, meta: Meta) -> str:
        try:
            payload = json.loads(await self._mediainfo_json_text(meta))
            return self._language_tag_from_payload(payload)
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.error(f"{self.tracker}: [red]Error: {error}")
            return ""

    @staticmethod
    async def _mediainfo_json_text(meta: Meta) -> str:
        path = release_temp_dir(meta.base_dir, meta.uuid) / "MediaInfo.json"
        async with aiofiles.open(path, encoding="utf-8") as handle:
            return await handle.read()

    @classmethod
    def _language_tag_from_payload(cls, payload: Any) -> str:
        tracks = cls._audio_tracks(payload)
        if any(cls._track_is_english(track) for track in tracks):
            return ""
        return cls._first_audio_language(tracks)

    @classmethod
    def _audio_tracks(cls, payload: Any) -> list[dict[str, Any]]:
        tracks = cls._media_track_values(payload)
        return [track for track in tracks if track.get("@type") == "Audio"]

    @classmethod
    def _media_track_values(cls, payload: Any) -> list[dict[str, Any]]:
        media = cls._media_mapping(payload)
        return cls._mapping_tracks(media.get("track", []))

    @staticmethod
    def _media_mapping(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        media = payload.get("media", {})
        return cast(dict[str, Any], media) if isinstance(media, dict) else {}

    @staticmethod
    def _mapping_tracks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [cast(dict[str, Any], track) for track in value if isinstance(track, dict)]

    @staticmethod
    def _track_is_english(track: dict[str, Any]) -> bool:
        return str(track.get("Language", "None")).startswith("en")

    @staticmethod
    def _first_audio_language(tracks: list[dict[str, Any]]) -> str:
        if not tracks:
            return ""
        return str(tracks[0].get("Language_String", "")).upper()

    async def get_basename(self, meta: Meta) -> str:
        filelist = cast(list[str], meta.filelist or [])
        path = filelist[0] if filelist else str(meta.path or "")
        return Path(path).name

    async def search_existing(self, meta: Meta) -> list[dict[str, str]]:
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if not cookie_jar:
            logger.info(f"{self.tracker}: Cannot search without valid cookies.")
            return []
        query = self._search_query(meta)
        if not query:
            logger.info(f"{self.tracker}: [red]Title is missing.")
            return []
        response = await self._search_response(meta, query, cast(httpx.Cookies, cookie_jar))
        if await self._search_requires_login(meta, response):
            return []
        response.raise_for_status()
        return self._search_results(response.json())

    @staticmethod
    def _search_query(meta: Meta) -> str:
        title = str(meta.title or "").strip()
        if not title:
            return ""
        year = "" if meta.year is None else str(meta.year).strip()
        return f"{title} {year}".strip()

    async def _search_response(self, meta: Meta, query: str, cookie_jar: httpx.Cookies) -> httpx.Response:
        encoded = urllib.parse.quote(query)
        url = f"{self.base_url}/ajax.php?action=browse&searchstr={encoded}"
        logger.debug(f"{self.tracker}: [blue]{url}")
        async with httpx.AsyncClient(headers=self._user_agent_header(meta), timeout=30.0, cookies=cookie_jar) as client:
            return await client.get(url)

    @staticmethod
    def _user_agent_header(meta: Meta) -> dict[str, str]:
        version = meta.current_version if meta.current_version is not None else "github.com/wastaken7/Upload-Assistant"
        return {"User-Agent": f"{meta.ua_name} {version}"}

    async def _search_requires_login(self, meta: Meta, response: httpx.Response) -> bool:
        if "login.php" not in str(response.url) and "login.php" not in response.text:
            return False
        await self.cookie_validator.handle_validation_failure(meta, self.tracker, response.text)
        meta.skipping = self.tracker
        return True

    def _search_results(self, payload: Any) -> list[dict[str, str]]:
        data = self._successful_search_payload(payload)
        results = data.get("results", [])
        values = results if isinstance(results, list) else []
        return [entry for item in values if (entry := self._search_result(item)) is not None]

    def _successful_search_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RuntimeError(f"{self.tracker}: API returned invalid response")
        if payload.get("status") != "success":
            raise RuntimeError(f"{self.tracker}: API returned unsuccessful status: {payload.get('error', 'unknown error')}")
        response = payload.get("response", {})
        return cast(dict[str, Any], response) if isinstance(response, dict) else {}

    @classmethod
    def _search_result(cls, item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict) or "groupName" not in item:
            return None
        result = cast(dict[str, Any], item)
        torrent_id = result.get("torrentId")
        group_id = result.get("groupId")
        return {
            "name": str(result.get("groupName", "")),
            "size": str(result.get("size", "")),
            "files": str(result.get("groupName", "")),
            "file_count": str(result.get("fileCount", "")),
            "link": f"{cls.search_url}?id={group_id}&torrentid={torrent_id}",
            "download": f"{cls.base_url}/torrents.php?action=download&id={torrent_id}",
        }

    async def get_auth_key(self, meta: Meta) -> str | None:
        saved = await self.cookie_validator.get_ar_auth_key(meta, self.tracker)
        if saved:
            return saved
        logger.info(f"{self.tracker}: [yellow]Auth key not found. This may happen if you're using manually exported cookies.[/yellow]")
        logger.info(f"{self.tracker}: [yellow]Attempting to extract auth key from torrents page...[/yellow]")
        cookie_jar = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if not cookie_jar:
            return None
        return await self._fetch_auth_key(meta, cast(httpx.Cookies, cookie_jar))

    async def _fetch_auth_key(self, meta: Meta, cookie_jar: httpx.Cookies) -> str | None:
        try:
            response = await self._auth_page(meta, cookie_jar)
            auth_key = self._extract_auth_key(response.text)
            if not auth_key:
                return None
            await self._save_auth_key(meta, auth_key)
            return auth_key
        except (httpx.HTTPError, OSError, ValueError) as error:
            logger.error(f"{self.tracker}: [red]Error extracting auth key: {error}")
            return None

    async def _auth_page(self, meta: Meta, cookie_jar: httpx.Cookies) -> httpx.Response:
        async with httpx.AsyncClient(headers=self._user_agent_header(meta), timeout=30.0, cookies=cookie_jar) as client:
            response = await client.get(self.test_url)
            response.raise_for_status()
            return response

    @staticmethod
    def _extract_auth_key(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        logout_link = cast(Any, soup).find("a", href=True, string="Logout")
        if logout_link is None:
            return None
        href_value = logout_link.get("href")
        if not isinstance(href_value, str):
            return None
        match = re.search(r"auth=([^&]+)", href_value)
        return match.group(1) if match else None

    async def _save_auth_key(self, meta: Meta, auth_key: str) -> None:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookie_file = find_cookie_file(meta.base_dir, self.tracker, self.config)
        auth_file = cookie_file.replace(".txt", "_auth.txt")
        try:
            async with aiofiles.open(auth_file, "w", encoding="utf-8") as handle:
                await handle.write(auth_key)
            logger.info(f"{self.tracker}: [green]Auth key saved for future use[/green]")
        except OSError:
            return

    async def upload(self, meta: Meta) -> bool:
        await self.common.create_torrent_for_upload(meta, self.tracker, self.source_flag)
        await self.edit_desc(meta)
        description = await self._upload_description(meta)
        if description is None:
            return False
        cover = await self._upload_cover(meta)
        if cover is None:
            return False
        auth_key = await self.get_auth_key(meta)
        if not auth_key:
            meta.tracker_status[self.tracker]["status_message"] = "data error: Failed to extract auth key"
            return False
        cookies = await self.cookie_validator.load_session_cookies(meta, self.tracker)
        if not cookies:
            meta.tracker_status[self.tracker]["status_message"] = "data error: Failed to load cookies for upload"
            return False
        data = await self._upload_data(meta, description, cover, auth_key)
        return await self._submit_upload(meta, data, cast(httpx.Cookies, cookies))

    async def _upload_description(self, meta: Meta) -> str | None:
        path = release_temp_dir(meta.base_dir, meta.uuid) / f"[{self.tracker}]DESCRIPTION.txt"
        try:
            async with aiofiles.open(path, encoding="utf-8") as handle:
                return await handle.read()
        except FileNotFoundError:
            meta.tracker_status[self.tracker]["status_message"] = f"data error: Description file not found at {path}"
            return None

    async def _upload_cover(self, meta: Meta) -> str | None:
        cover = self._cover_from_meta(meta)
        if cover:
            return cover
        if meta.unattended and not meta.unattended_confirm:
            logger.info(f"{self.tracker}: [yellow]Unattended mode: No cover image found. Skipping {self.tracker} upload.[/yellow]")
            meta.skipping = self.tracker
            return None
        return await self._prompt_cover()

    @staticmethod
    def _cover_from_meta(meta: Meta) -> str:
        if meta.artwork_url:
            return str(meta.artwork_url)
        imdb = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        return str(imdb.get("cover") or "")

    async def _prompt_cover(self) -> str:
        while True:
            cover = await prompt_in_thread(cli_ui.ask_string, "No Cover was found. Please input a link to a cover:", default="") or ""
            if self._valid_cover_url(cover):
                return cover
            logger.info(f"{self.tracker}: [red]Invalid image link. Please enter a link that ends with .jpg, .png, or .gif.")

    @staticmethod
    def _valid_cover_url(value: str) -> bool:
        return re.fullmatch(r"https?://.*\.(?:jpg|png|gif)", value, flags=re.IGNORECASE) is not None

    async def _upload_data(self, meta: Meta, description: str, cover: str, auth_key: str) -> dict[str, Any]:
        return {
            "submit": "true",
            "auth": auth_key,
            "type": await self.get_type(meta),
            "title": await self.get_name(meta),
            "tags": self._upload_tags(meta),
            "image": cover,
            "desc": description,
        }

    @classmethod
    def _upload_tags(cls, meta: Meta) -> str:
        parts: list[str] = []
        if meta.imdb_id != 0:
            parts.append(f"tt{meta.imdb}")
        genres = cls._genre_tags(meta.genres)
        if genres:
            parts.append(genres)
        return ", ".join(parts) + (", " if parts else "")

    @classmethod
    def _genre_tags(cls, value: Any) -> str:
        tags = cls._genre_values(value)
        return re.sub(r"\.{2,}", ".", ", ".join(tags))

    @staticmethod
    def _genre_values(value: Any) -> list[str]:
        if isinstance(value, list):
            return [text for item in value if (text := str(item).strip())]
        return AlphaRatio._split_genre_text(str(value or ""))

    @staticmethod
    def _split_genre_text(value: str) -> list[str]:
        parts: list[str] = []
        for item in value.split(","):
            parts.extend(text for subitem in item.split("&") if (text := subitem.strip()))
        return parts

    async def _submit_upload(self, meta: Meta, data: dict[str, Any], cookies: httpx.Cookies) -> bool:
        return await self.cookie_uploader.handle_upload(
            meta=meta,
            tracker=self.tracker,
            data=data,
            upload_cookies=cookies,
            upload_url=self.upload_url,
            torrent_field_name="file_input",
            source_flag=self.source_flag,
            torrent_url=self.torrent_url,
            id_pattern=r"torrents\.php\?id=(\d+)",
            success_status_code="200",
        )

    async def parse_mediainfo_async(self, video_path: str, template_path: str) -> str:
        """Parse MediaInfo asynchronously using thread executor"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: MediaInfo.parse(video_path, output="STRING", full=False, mediainfo_options={"inform": f"file://{template_path}"}))

    async def get_name(self, meta: Meta) -> str:
        name = str(meta.scene_name or "") if meta.scene else self._normalized_release_name(str(meta.uuid or ""))
        return self._ensure_group_name(name, meta.tag)

    @staticmethod
    def _normalized_release_name(value: str) -> str:
        path = Path(value)
        name = path.stem if path.suffix.lower() in {".mkv", ".mp4", ".avi", ".ts"} else value
        for char in "':()[]{}":
            name = name.replace(char, "." if char in "()[]{}" else "")
        name = name.replace(" ", ".")
        return re.sub(r"\.{2,}", ".", name)

    @classmethod
    def _ensure_group_name(cls, name: str, tag: str | None) -> str:
        if cls._valid_group_tag(tag):
            return name
        return f"{cls._strip_invalid_group_tags(name)}-NoGRP"

    @staticmethod
    def _valid_group_tag(tag: str | None) -> bool:
        if not tag:
            return False
        lowered = str(tag).lower()
        return not any(invalid in lowered for invalid in ("nogrp", "nogroup", "unknown", "-unk-"))

    @staticmethod
    def _strip_invalid_group_tags(name: str) -> str:
        result = name
        for invalid in ("nogrp", "nogroup", "unknown", "-unk-"):
            result = re.sub(f"-{re.escape(invalid)}", "", result, flags=re.IGNORECASE)
        return result
