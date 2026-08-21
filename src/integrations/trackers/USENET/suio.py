# Upload Assistant © 2026 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import hashlib
import io
import re
import unicodedata
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

import aiofiles
import httpx
from PIL import Image

from src.domain_models.release import Meta
from src.integrations.filesystem.temp_paths import artwork_dir
from src.integrations.observability.runtime_support import logger
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.USENET.search_helpers import (
    build_newznab_search_query,
    get_daily_api_hit_limit,
    get_newznab_search_category_id,
    parse_newznab_dupes,
    reserve_daily_api_hit,
)

Config = dict[str, Any]


class _ParsedUploadResponse(TypedDict):
    comment: str
    font_error: str
    error: bool


class Suio:
    """
    SUIO Private Torrent Tracker
    """

    auth_type = "other_api"
    tracker = "SUIO"
    display_name = "Suio"
    allows_bloated_audio = True
    banned_groups: tuple[str, ...] = ()
    upload_url: str | None = None
    torrent_url: str | None = None
    search_url: str | None = None
    base_url = "https://suio.cc"
    supported_categories = ("MOVIE", "TV", "GAME", "BOOK", "XXX")
    is_usenet = True

    def __init__(self, config: Config) -> None:
        self.config = config
        self.common = Common(config)
        self.tracker_cfg = self._tracker_config(config)
        self.api_key = str(self.tracker_cfg.get("api_key", "")).strip()
        self.daily_api_hit_limit = get_daily_api_hit_limit(self.tracker_cfg)
        self.upload_url, self.torrent_url, self.search_url = self._configured_urls(self.tracker_cfg)

    @classmethod
    def _tracker_config(cls, config: Config) -> dict[str, Any]:
        trackers = config.get("TRACKERS", {})
        if not isinstance(trackers, dict):
            return {}
        value = trackers.get(cls.tracker, {})
        return value if isinstance(value, dict) else {}

    @classmethod
    def _configured_urls(cls, tracker_cfg: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        base_url = str(tracker_cfg.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            return None, None, None
        try:
            return cls._validated_urls(base_url)
        except Exception:
            return None, None, None

    @classmethod
    def _validated_urls(cls, base_url: str) -> tuple[str | None, str | None, str | None]:
        url_to_parse = base_url if base_url.startswith(("http://", "https://")) else "https://" + base_url
        parsed = urlparse(url_to_parse)
        hostname = parsed.netloc.lower().split(":")[0]
        if not cls._allowed_domain(hostname):
            logger.info(f"{cls.tracker}: [red]base_url from config.py does not match the expected domain. Skipping...[/red]")
            return None, None, None
        scheme = parsed.scheme or "https"
        api_hostname = (parsed.netloc or parsed.path).split("@")[-1]
        return f"{base_url}/api-upload", f"{base_url}/details.php?id=", f"{scheme}://api.{api_hostname}/api"

    @staticmethod
    def _allowed_domain(hostname: str) -> bool:
        parts = hostname.split(".")
        main_domain = ".".join(parts[-2:]) if len(parts) >= 2 else hostname
        domain_hash = hashlib.sha256(main_domain.encode("utf-8")).hexdigest()
        return domain_hash == "a0fcf409be81cbcec4e212cb69331960e5d709449c0e9cad40e36369d8da8f3c"

    def get_search_query(self, meta: Meta) -> str:
        return build_newznab_search_query(meta)

    async def get_search_name(self, meta: Meta) -> str:
        return await self.get_name(meta)

    def _parse_dupes_from_response(self, response_text: str) -> list[dict[str, Any]]:
        return parse_newznab_dupes(response_text, self.torrent_url, use_guid_attr_as_id=True)

    async def search_existing(self, meta: Meta) -> list[Any]:
        cached = await self._cached_search_result(meta)
        if cached is not None:
            return cached
        if not self._search_enabled():
            return []
        queries = await self._search_queries(meta)
        return await self._execute_search_queries(meta, queries)

    async def _cached_search_result(self, meta: Meta) -> list[str] | None:
        release_name = await self.get_name(meta)
        cache_file = Path(meta.base_dir) / "tmp" / meta.uuid / f"{self.tracker}_upload_ok"
        if release_name and cache_file.exists():
            logger.info(f"{self.tracker}: [yellow]Found local upload cache.[/yellow]")
            return [release_name]
        return None

    def _search_enabled(self) -> bool:
        if not self.search_url:
            return False
        if self.daily_api_hit_limit > 0:
            return True
        logger.info(f"{self.tracker}: [yellow]Duplicate search via API is disabled because daily_api_hit_limit is 0.[/yellow]")
        return False

    async def _search_queries(self, meta: Meta) -> list[dict[str, str]]:
        queries: list[dict[str, str]] = []
        exact = await self.get_search_name(meta)
        if exact:
            queries.append({"t": "search", "q": exact, "pw": "0"})
        queries.append(self._category_search_params(meta))
        return queries

    def _category_search_params(self, meta: Meta) -> dict[str, str]:
        params = {"cat": get_newznab_search_category_id(meta)}
        category = meta.category.upper()
        if category == "TV":
            params.update(self._tv_search_params(meta))
        elif category == "MOVIE":
            params.update(self._movie_search_params(meta))
        else:
            params.update({"t": "search", "q": self.get_search_query(meta)})
        return params

    def _tv_search_params(self, meta: Meta) -> dict[str, str]:
        params: dict[str, str] = {"t": "tvsearch", **self._tv_identity_params(meta)}
        params.update(self._tv_episode_params(meta))
        return params

    def _tv_identity_params(self, meta: Meta) -> dict[str, str]:
        tvdb = str(meta.tvdb_id or "")
        if tvdb.isdigit() and int(tvdb) > 0:
            return {"tvdbid": tvdb}
        return {"q": self.get_search_query(meta)}

    @staticmethod
    def _tv_episode_params(meta: Meta) -> dict[str, str]:
        params: dict[str, str] = {}
        if meta.season_int > 0:
            params["season"] = str(meta.season_int)
        if meta.episode_int > 0:
            params["ep"] = str(meta.episode_int)
        return params

    def _movie_search_params(self, meta: Meta) -> dict[str, str]:
        if meta.imdb_tt:
            return {"t": "movie", "imdbid": meta.imdb_tt}
        return {"t": "movie", "q": self.get_search_query(meta)}

    async def _execute_search_queries(self, meta: Meta, queries: list[dict[str, str]]) -> list[dict[str, Any]]:
        dupes: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        async with httpx.AsyncClient(timeout=10.0) as client:
            for query in queries:
                if not await self._execute_search_query(meta, client, query, dupes, seen_keys):
                    break
        return dupes

    async def _execute_search_query(
        self,
        meta: Meta,
        client: httpx.AsyncClient,
        query: dict[str, str],
        dupes: list[dict[str, Any]],
        seen_keys: set[str],
    ) -> bool:
        allowed, used_hits = await reserve_daily_api_hit(meta.base_dir, self.tracker, self.daily_api_hit_limit)
        if not allowed:
            logger.info(f"{self.tracker}: [yellow]Duplicate search stopped because the 24-hour API hit limit ({self.daily_api_hit_limit}) has been reached.[/yellow]")
            return False
        search_url = self.search_url
        if search_url is None:
            return False
        response = await client.get(search_url, params=self._request_params(query))
        logger.debug(f"{self.tracker}: Duplicate search used API hit {used_hits}/{self.daily_api_hit_limit} in the last 24 hours.")
        response.raise_for_status()
        self._append_search_dupes(response.text, dupes, seen_keys)
        return True

    def _request_params(self, query: dict[str, str]) -> dict[str, str]:
        return {"apikey": self.api_key, "limit": "100", "extended": "1", "pw": "2", **query}

    def _append_search_dupes(self, response_text: str, dupes: list[dict[str, Any]], seen_keys: set[str]) -> None:
        if not response_text.strip():
            return
        for dupe in self._parse_dupes_from_response(response_text):
            self._append_search_dupe(dupe, dupes, seen_keys)

    @staticmethod
    def _append_search_dupe(dupe: dict[str, Any], dupes: list[dict[str, Any]], seen_keys: set[str]) -> None:
        key = str(dupe.get("link") or dupe.get("name") or "")
        if key in seen_keys:
            return
        seen_keys.add(key)
        dupes.append(dupe)

    async def get_additional_checks(self, _meta: Meta) -> bool:
        tracker_cfg = self.config.get("TRACKERS", {}).get(self.tracker, {})
        username = tracker_cfg.get("username", "").strip()
        if not (username and self.upload_url and self.torrent_url):
            logger.info(f"{self.tracker}: [red]Skipping due to missing Username or base_url.[/red]")
            return False
        return True

    def get_category_id(self, meta: Meta) -> str:
        category = meta.category.upper()
        resolver = {
            "MOVIE": self._movie_category_id,
            "TV": self._tv_category_id,
            "XXX": self._xxx_category_id,
            "GAME": self._game_category_id,
            "MUSIC": self._music_category_id,
            "BOOK": self._book_category_id,
        }.get(category)
        return "video" if resolver is None else resolver(meta)

    @classmethod
    def _resolution_class(cls, resolution: str) -> str:
        value = resolution.lower()
        exact = cls._resolution_groups().get(value)
        if exact is not None:
            return exact
        return "sd" if cls._sd_resolution(value) else "other"

    @staticmethod
    def _resolution_groups() -> dict[str, str]:
        return {
            "2160p": "uhd",
            "4320p": "uhd",
            "8640p": "uhd",
            "1080p": "hd",
            "1080i": "hd",
            "720p": "hd",
            "1440p": "hd",
        }

    @staticmethod
    def _sd_resolution(value: str) -> bool:
        return any(marker in value for marker in ("sd", "480p", "576p"))

    @classmethod
    def _movie_category_id(cls, meta: Meta) -> str:
        by_resolution = {"uhd": "31", "hd": "16", "sd": "15"}.get(cls._resolution_class(meta.resolution))
        if by_resolution:
            return by_resolution
        if meta.is_disc == "BDMV":
            return "35"
        return "17" if "DVD" in str(meta.source).upper() else "movie"

    @classmethod
    def _tv_category_id(cls, meta: Meta) -> str:
        return {"uhd": "30", "hd": "20", "sd": "19"}.get(cls._resolution_class(meta.resolution), "tv")

    @classmethod
    def _xxx_category_id(cls, meta: Meta) -> str:
        return {"uhd": "33", "hd": "27"}.get(cls._resolution_class(meta.resolution), "xxx")

    @staticmethod
    def _game_category_id(meta: Meta) -> str:
        platform = meta.platform.upper()
        if "PC" in platform or "WINDOWS" in platform:
            return "12"
        if "MAC" in platform:
            return "13"
        return "14"

    @staticmethod
    def _music_category_id(meta: Meta) -> str:
        fmt = meta.format.upper()
        if "FLAC" in fmt or "LOSSLESS" in fmt:
            return "22"
        return "7" if "MP3" in fmt else "3"

    @staticmethod
    def _book_category_id(meta: Meta) -> str:
        return "29" if meta.audiobook else "9"

    def _map_single_language_to_id(self, lang: str) -> str:
        normalized = lang.lower().strip()
        if not normalized:
            logger.info(f"{self.tracker}: No audio languages found, setting to Auto ([red]0[/red])")
            return "0"
        language_id = self._matched_language_id(normalized)
        if language_id is not None:
            return language_id
        logger.info(f"{self.tracker}: Could not find language {normalized} ID, setting to Other ([red]10[/red])")
        return "10"

    @classmethod
    def _matched_language_id(cls, value: str) -> str | None:
        for language_id, aliases in cls._language_aliases().items():
            if cls._matches_language_alias(value, aliases):
                return language_id
        return None

    @classmethod
    def _matches_language_alias(cls, value: str, aliases: tuple[str, ...]) -> bool:
        return any(cls._matches_one_language_alias(value, alias) for alias in aliases)

    @staticmethod
    def _matches_one_language_alias(value: str, alias: str) -> bool:
        if len(alias) == 2:
            return value == alias
        return alias in value

    @staticmethod
    def _language_aliases() -> dict[str, tuple[str, ...]]:
        return {
            "11": ("english", "eng", "en"),
            "1": ("danish", "dan", "da"),
            "2": ("dutch", "dut", "nld", "nl"),
            "3": ("finnish", "fin", "fi"),
            "4": ("french", "fre", "fra", "fr"),
            "5": ("german", "ger", "deu", "de"),
            "6": ("norwegian", "nor", "no"),
            "7": ("spanish", "spa", "esp", "es"),
            "8": ("swedish", "swe", "sv"),
            "12": ("hebrew", "heb", "he"),
            "13": ("portuguese", "por", "pt"),
            "9": ("multi",),
        }

    def _is_same_language(self, lang_str: str, orig_code: str | None) -> bool:
        if not orig_code:
            return False
        value = lang_str.lower().strip()
        code = orig_code.lower().strip()
        if value == code:
            return True
        if self._langcodes_match(value, code):
            return True
        return self._fallback_language_match(value, code)

    @staticmethod
    def _langcodes_match(value: str, code: str) -> bool:
        try:
            import langcodes

            name = langcodes.Language.get(code).display_name().lower()
            return name in value or value in name
        except Exception:
            return False

    @classmethod
    def _fallback_language_match(cls, value: str, code: str) -> bool:
        aliases = cls._language_code_aliases().get(code, ())
        return any(alias in value or value in alias for alias in aliases)

    @staticmethod
    def _language_code_aliases() -> dict[str, tuple[str, ...]]:
        return {
            "en": ("english", "eng"),
            "pt": ("portuguese", "português", "por"),
            "es": ("spanish", "español", "spa", "esp"),
            "fr": ("french", "français", "fre", "fra"),
            "de": ("german", "deutsch", "ger", "deu"),
            "it": ("italian", "italiano", "ita"),
            "da": ("danish", "dansk", "dan"),
            "nl": ("dutch", "nederlands", "dut", "nld"),
            "fi": ("finnish", "suomi", "fin"),
            "no": ("norwegian", "norsk", "nor"),
            "sv": ("swedish", "svenska", "swe"),
            "he": ("hebrew", "עברית", "heb"),
        }

    def get_language_id(self, meta: Meta) -> str:
        if not self._resolve_language_enabled():
            return "0"
        languages = self._audio_languages(meta)
        if len(languages) == 1:
            return self._map_single_language_to_id(languages[0])
        if len(languages) == 2:
            return self._dual_language_id(languages, meta.original_language)
        if len(languages) >= 3:
            return "9"
        logger.info(f"{self.tracker}: No audio languages found, setting to Auto ([red]0[/red])")
        return "0"

    def _resolve_language_enabled(self) -> bool:
        return bool(self.tracker_cfg.get("resolve_language", True))

    @classmethod
    def _audio_languages(cls, meta: Meta) -> list[str]:
        value = cls._audio_language_value(meta)
        values = cls._language_values(value)
        return [str(language) for language in values if language]

    @staticmethod
    def _audio_language_value(meta: Meta) -> Any:
        if meta.audio_languages:
            return meta.audio_languages
        return meta.book_language_iso if meta.book_language_iso else []

    @staticmethod
    def _language_values(value: Any) -> list[Any]:
        if isinstance(value, str):
            return [value]
        return list(value)

    def _dual_language_id(self, languages: list[str], original_language: str | None) -> str:
        selected = languages[1] if self._is_same_language(languages[0], original_language) else languages[0]
        return self._map_single_language_to_id(selected)

    async def _prepare_files(self, meta: Meta) -> dict[str, Any] | None:
        nzb_file = await self._nzb_file(meta)
        if nzb_file is None:
            return None
        files: dict[str, Any] = {"nzb": nzb_file}
        nfo_file = await self._nfo_file(meta)
        if nfo_file is not None:
            files["nfo"] = nfo_file
        cover_file = await self._cover_file(meta)
        if cover_file is not None:
            files["cover"] = cover_file
        return files

    async def _nzb_file(self, meta: Meta) -> tuple[str, bytes, str] | None:
        nzb_path = meta.nzb_path
        if not nzb_path or not await self.common.check_nzb_file(self.tracker, meta):
            return None
        content = await self._read_bytes(Path(nzb_path))
        return Path(nzb_path).name, content, "application/x-nzb"

    async def _nfo_file(self, meta: Meta) -> tuple[str, bytes, str] | None:
        candidate = self._nfo_candidate(meta)
        if candidate is None:
            return None
        filename, path = candidate
        return filename, await self._read_bytes(path), "application/octet-stream"

    def _nfo_candidate(self, meta: Meta) -> tuple[str, Path] | None:
        root = Path(meta.base_dir) / "tmp" / meta.uuid
        if meta.scene:
            return self._first_nfo(root)
        preferred = self._preferred_nfo(meta, root)
        return preferred if preferred is not None else self._first_nfo(root)

    @staticmethod
    def _first_nfo(root: Path) -> tuple[str, Path] | None:
        path = next(root.glob("*.nfo"), None)
        return (path.name, path) if path is not None and path.exists() else None

    @staticmethod
    def _preferred_nfo(meta: Meta, root: Path) -> tuple[str, Path] | None:
        if meta.is_disc == "BDMV":
            path = root / "BD_SUMMARY_00.txt"
            return ("BDInfo.nfo", path) if path.exists() else None
        path = root / "MEDIAINFO_CLEANPATH.txt"
        return ("MediaInfo.nfo", path) if path.exists() else None

    async def _cover_file(self, meta: Meta) -> tuple[str, bytes, str] | None:
        if meta.category in {"TV", "MOVIE"}:
            return None
        path = self._cover_path(meta)
        if path is None:
            return None
        content, filename = await self._jpeg_cover(path)
        return filename, content, "image/jpeg"

    @staticmethod
    def _cover_path(meta: Meta) -> Path | None:
        root = artwork_dir(meta.base_dir, meta.uuid)
        jpg = root / "POSTER.jpg"
        png = root / "POSTER.png"
        if jpg.exists():
            return jpg
        return png if png.exists() else None

    async def _jpeg_cover(self, path: Path) -> tuple[bytes, str]:
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            return await self._read_bytes(path), path.name
        content = await asyncio.to_thread(self._convert_to_jpg, path)
        return content, path.stem + ".jpg"

    @staticmethod
    def _convert_to_jpg(path: str | Path) -> bytes:
        with Image.open(path) as image:
            rgb = Suio._rgb_image(image)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=95)
            return buffer.getvalue()

    @staticmethod
    def _rgb_image(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"}:
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            return background
        return image if image.mode == "RGB" else image.convert("RGB")

    @staticmethod
    async def _read_bytes(path: Path) -> bytes:
        async with aiofiles.open(path, "rb") as handle:
            return await handle.read()

    async def get_name(self, meta: Meta) -> str:
        name = meta.scene_name or meta.basename_no_ext or ""
        normalized = unicodedata.normalize("NFKD", name)
        return "".join(char for char in normalized if not unicodedata.combining(char))

    async def _prepare_data(self, meta: Meta) -> dict[str, Any]:
        return {
            "rlsname": await self.get_name(meta),
            "catid": self.get_category_id(meta),
            "upload": "Post NZB",
            "language": self.get_language_id(meta),
            "tag": "0",
        }

    async def upload(self, meta: Meta) -> bool | None:
        status = meta.tracker_status.setdefault(self.tracker, {})
        if not self.upload_url:
            logger.info(f"{self.tracker}: [red]base_url missing. Cannot upload.[/red]")
            status["status_message"] = "data error: base_url missing"
            return False
        files = await self._prepare_files(meta)
        if not files:
            status["status_message"] = "data error: NZB file missing or password missing in header"
            return False
        data = await self._prepare_data(meta)
        username = str(self.tracker_cfg.get("username", "")).strip()
        if meta.debug:
            return self._debug_upload(status, username, data, files)
        return await self._submit_upload(meta, status, username, data, files)

    def _debug_upload(self, status: dict[str, Any], username: str, data: dict[str, Any], files: dict[str, Any]) -> bool:
        logger.debug(f"{self.tracker}: [cyan]Upload (DEBUG MODE):[/cyan]")
        logger.debug(f"{self.tracker}: User: {username}")
        logger.debug(f"{self.tracker}: Fields:")
        logger.debug(Redaction.redact_private_info(data))
        logger.debug(f"{self.tracker}: Files:")
        logger.debug({key: value[0] for key, value in files.items()})
        status["status_message"] = "Debug mode enabled, skipping upload."
        return True

    async def _submit_upload(
        self,
        meta: Meta,
        status: dict[str, Any],
        username: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> bool:
        try:
            response = await self._upload_response(meta, username, data, files)
            return await self._handle_upload_response(meta, status, username, data, response)
        except httpx.TimeoutException:
            status["status_message"] = "data error: Request timed out after 60 seconds"
            return False
        except httpx.RequestError as error:
            status["status_message"] = f"data error: Unable to upload. Error: {error}"
            return False
        except Exception as error:
            status["status_message"] = f"data error: Unexpected error. Error: {error}"
            return False

    async def _upload_response(self, meta: Meta, username: str, data: dict[str, Any], files: dict[str, Any]) -> httpx.Response:
        params = {"user": username, "api": self.api_key}
        headers = {"User-Agent": self._user_agent(meta)}
        upload_url = self.upload_url
        if upload_url is None:
            raise ValueError("SUIO upload URL is not configured")
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(upload_url, files=files, data=data, params=params, headers=headers, follow_redirects=True)

    @staticmethod
    def _user_agent(meta: Meta) -> str:
        version = meta.current_version if meta.current_version is not None else "github.com/Audionut/Upload-Assistant"
        return f"{meta.ua_name} {version}"

    async def _handle_upload_response(
        self,
        meta: Meta,
        status: dict[str, Any],
        username: str,
        data: dict[str, Any],
        response: httpx.Response,
    ) -> bool:
        parsed = self._parsed_upload_response(response)
        if self._response_failed(response, parsed):
            status["status_message"] = "data error: " + self._redacted_message(meta, data, username, self._failure_message(response, parsed))
            return False
        status["status_message"] = self._redacted_message(meta, data, username, parsed["comment"] or "Upload successful")
        await self._write_upload_cache(meta)
        torrent_id = self._response_id(response, parsed["comment"])
        if torrent_id:
            status["torrent_id"] = torrent_id
        return True

    @staticmethod
    def _parsed_upload_response(response: httpx.Response) -> _ParsedUploadResponse:
        comment = Suio._response_comment(response.text)
        font_error = Suio._font_error(response.text)
        final_url = str(response.url)
        error = "inf=err" in final_url or "/404" in final_url or bool(font_error) or Suio._comment_is_error(comment)
        return {"comment": comment, "font_error": font_error, "error": error}

    @staticmethod
    def _response_comment(text: str) -> str:
        match = re.search(r"<!--\s*<response>(.*?)</response>\s*-->", text, re.IGNORECASE | re.DOTALL)
        return re.sub(r"\s+", " ", match.group(1).strip()) if match else ""

    @staticmethod
    def _font_error(text: str) -> str:
        match = re.search(r'<font[^>]*color=["\']?red["\']?[^>]*>(.*?)</font>', text, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        value = re.sub(r"<[^>]+>", " ", match.group(1))
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _comment_is_error(comment: str) -> bool:
        lowered = comment.lower()
        return any(marker in lowered for marker in ("invalid", "error", "did not select", "fail"))

    @staticmethod
    def _response_failed(response: httpx.Response, parsed: _ParsedUploadResponse) -> bool:
        return response.status_code not in {200, 201} or bool(parsed["error"])

    @staticmethod
    def _failure_message(response: httpx.Response, parsed: _ParsedUploadResponse) -> str:
        font_error = str(parsed["font_error"])
        if font_error:
            return font_error
        comment = str(parsed["comment"])
        if comment:
            return comment
        return f"HTTP {response.status_code}" if response.status_code not in {200, 201} else "Unknown upload failure"

    @staticmethod
    def _redacted_message(meta: Meta, data: dict[str, Any], username: str, message: str) -> str:
        result = message
        values = (Path(str(meta.nzb_path or "")).name, str(data.get("rlsname", "")), username)
        for value in values:
            if value:
                result = re.sub(re.escape(value), "[redacted]", result, flags=re.IGNORECASE)
        return result

    @staticmethod
    async def _write_upload_cache(meta: Meta) -> None:
        cache_dir = Path(meta.base_dir) / "tmp" / meta.uuid
        cache_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(cache_dir / "SUIO_upload_ok", "w", encoding="utf-8") as handle:
            await handle.write("ok")

    @staticmethod
    def _response_id(response: httpx.Response, comment: str) -> str:
        candidates = (comment, response.text, str(response.url))
        patterns = (r"ID:\s*([a-zA-Z0-9]+)", r"(?:details\.php\?id=|details/|id=)([a-zA-Z0-9]+)")
        for candidate in candidates:
            for pattern in patterns:
                match = re.search(pattern, candidate, re.IGNORECASE)
                if match:
                    return str(match.group(1))
        return ""
