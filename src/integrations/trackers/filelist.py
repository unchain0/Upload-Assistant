# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import asyncio
import glob
import json
import re
from pathlib import Path
from typing import Any, cast

import aiofiles
import cli_ui
import httpx
from bs4 import BeautifulSoup
from rich.markup import escape
from unidecode import unidecode

from src.domain_models.processing import LoginError, UploadError
from src.domain_models.release import Meta
from src.domain_models.release_description import base_description
from src.integrations.filesystem.temp_paths import screenshots_dir
from src.integrations.observability.runtime_support import (
    logger,
    prompt_in_thread,
)
from src.integrations.security.redaction import Redaction
from src.integrations.trackers.common import Common
from src.integrations.trackers.cookie_auth import CookieValidator


class FileList:
    """
    FL Private Torrent Tracker
    """

    auth_type = "cookies"
    tracker = "FILELIST"
    display_name = "FileList"
    allows_bloated_audio = True
    source_flag = "FL"
    signature: str | None = None
    banned_groups = ("",)
    base_url = "https://filelist.io"
    supported_categories = ("TV", "MOVIE")
    tracker_urls = ("reactor.filelist", "reactor.thefl.org")

    def __init__(self, config: dict[str, Any]) -> None:
        self.config: dict[str, Any] = config
        tracker_cfg = config["TRACKERS"][self.tracker]
        self.username: str = str(tracker_cfg.get("username", "")).strip()
        self.password: str = str(tracker_cfg.get("password", "")).strip()
        fltools_raw = tracker_cfg.get("fltools", {})
        self.fltools: dict[str, Any] = (
            cast(dict[str, Any], fltools_raw)
            if isinstance(fltools_raw, dict)
            else {}
        )
        uploader_name_raw = tracker_cfg.get("uploader_name")
        self.uploader_name: str | None = (
            str(uploader_name_raw) if uploader_name_raw else None
        )

        self.cookie_validator = CookieValidator(config)

    @staticmethod
    def _movie_uses_bluray_category(meta: Meta) -> bool:
        return meta.is_disc == "BDMV" or meta.type == "REMUX"

    @staticmethod
    def _movie_bluray_category_id(meta: Meta) -> int:
        return 26 if meta.resolution == "2160p" else 20

    @classmethod
    def _movie_base_category_id(cls, meta: Meta) -> int:
        if cls._movie_uses_bluray_category(meta):
            return cls._movie_bluray_category_id(meta)
        if meta.resolution == "2160p":
            return 6
        return 1 if meta.sd == 1 else 4

    @staticmethod
    def _movie_ro_category_applies(meta: Meta, has_ro_sub: bool) -> bool:
        return bool(has_ro_sub and meta.sd == 0 and meta.resolution != "2160p")

    @classmethod
    def _movie_category_id(cls, meta: Meta, has_ro_sub: bool) -> int:
        if cls._movie_ro_category_applies(meta, has_ro_sub):
            return 19
        return cls._movie_base_category_id(meta)

    @staticmethod
    def _tv_category_id(meta: Meta) -> int:
        if meta.resolution == "2160p":
            return 27
        return 23 if meta.sd == 1 else 21

    @staticmethod
    def _disc_category_override(meta: Meta, has_ro_sub: bool) -> int | None:
        if meta.is_disc != "DVD":
            return None
        return 3 if has_ro_sub else 2

    @classmethod
    def _base_category_id(cls, meta: Meta, has_ro_sub: bool) -> int:
        if meta.category == "MOVIE":
            return cls._movie_category_id(meta, has_ro_sub)
        if meta.category == "TV":
            return cls._tv_category_id(meta)
        return 4

    async def get_category_id(self, meta: Meta) -> int:
        _has_ro_audio, has_ro_sub = await self.get_ro_tracks(meta)
        category_id = self._base_category_id(meta, has_ro_sub)
        disc_override = self._disc_category_override(meta, has_ro_sub)
        if disc_override is not None:
            category_id = disc_override
        return 24 if meta.anime is True else category_id

    @staticmethod
    def _name_hdr_and_audio(meta: Meta, name: str) -> str:
        if "DV" in meta.hdr:
            name = name.replace(" DV ", " DoVi ")
        if meta.type in ("WEBDL", "WEBRIP", "ENCODE"):
            name = name.replace(meta.audio, meta.audio.replace(" ", "", 1))
        return name.replace(meta.aka, "")

    @staticmethod
    def _name_imdb_aka(
        meta: Meta, name: str, imdb_info: dict[str, Any]
    ) -> str:
        imdb_aka = str(imdb_info.get("aka", ""))
        return name.replace(meta.title, imdb_aka) if imdb_aka else name

    @staticmethod
    def _name_imdb_year(
        meta: Meta, name: str, imdb_info: dict[str, Any]
    ) -> str:
        meta_year = str(meta.year).strip() if meta.year is not None else ""
        imdb_year = str(imdb_info.get("year", meta_year))
        if meta_year and meta_year != imdb_year:
            return name.replace(meta_year, imdb_year)
        return name

    @classmethod
    def _name_imdb_overrides(cls, meta: Meta, name: str) -> str:
        if not isinstance(meta.imdb_info, dict):
            return name
        imdb_info = meta.imdb_info
        name = cls._name_imdb_aka(meta, name, imdb_info)
        return cls._name_imdb_year(meta, name, imdb_info)

    @staticmethod
    def _name_audio_corrections(meta: Meta, name: str) -> str:
        if "DD+" in meta.audio and "DDP" in meta.basename_no_ext:
            name = name.replace("DD+", "DDP")
        if "Atmos" in meta.audio and "Atmos" not in meta.basename_no_ext:
            name = name.replace("Atmos", "")
        return name

    @staticmethod
    def _name_release_normalization(name: str) -> str:
        name = (
            name.replace("BluRay REMUX", "Remux")
            .replace("BluRay Remux", "Remux")
            .replace("Bluray Remux", "Remux")
        )
        name = name.replace("PQ10", "HDR").replace("HDR10+", "HDR")
        name = (
            name.replace("DoVi HDR HEVC", "HEVC DoVi HDR")
            .replace("HDR HEVC", "HEVC HDR")
            .replace("DoVi HEVC", "HEVC DoVi")
        )
        name = (
            name.replace("DTS7.1", "DTS")
            .replace("DTS5.1", "DTS")
            .replace("DTS2.0", "DTS")
            .replace("DTS1.0", "DTS")
        )
        return name.replace("Dubbed", "").replace("Dual-Audio", "")

    @staticmethod
    def _finalize_name(name: str) -> str:
        name = " ".join(name.split())
        name = re.sub(r"[^0-9a-zA-ZÀ-ÿ. &+'\-\[\]]+", "", name)
        return name.replace(" ", ".").replace("..", ".")

    async def get_name(self, meta: Meta) -> str:
        name = self._name_hdr_and_audio(meta, meta.name)
        name = self._name_imdb_overrides(meta, name)
        name = self._name_audio_corrections(meta, name)
        return self._finalize_name(self._name_release_normalization(name))

    def _is_true(self, value: Any) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes"}

    @staticmethod
    def _netscape_cookie_line(line: str) -> tuple[str, str] | None:
        if not line.strip() or line.startswith(("# ", "#")):
            return None
        fields = re.split(r"\s+", line.strip())
        if len(fields) < 7:
            return None
        return fields[5], fields[6]

    def _load_netscape_cookies(self, path: Path) -> dict[str, str]:
        cookies: dict[str, str] = {}
        try:
            with path.open(
                "r", encoding="utf-8", errors="ignore"
            ) as file_handle:
                for line in file_handle:
                    entry = self._netscape_cookie_line(line)
                    if entry is not None:
                        cookies[entry[0]] = entry[1]
        except Exception as error:
            logger.error(
                f"{self.tracker}: [red]Error parsing {self.tracker} Netscape "
                f"cookie file: {escape(str(error))}[/red]"
            )
        return cookies

    @staticmethod
    def _looks_like_json_cookie_file(path: Path) -> bool:
        try:
            prefix = path.read_bytes()[:4096].lstrip()
        except OSError:
            return False
        return prefix.startswith(b"{")

    def _load_pickle_cookies(self, path: Path) -> dict[str, str]:
        if self._looks_like_json_cookie_file(path):
            return self._load_json_cookies(path)
        logger.warning(
            f"{self.tracker}: [yellow]Refusing legacy pickle cookie file "
            f"{path.name}; pickle deserialization can execute arbitrary code. "
            "Re-authenticate or export cookies as JSON/Netscape text.[/yellow]"
        )
        return {}

    def _load_secure_cookies(self, path: Path) -> dict[str, str]:
        raw_cookies = self.cookie_validator._load_cookies_dict_secure(
            str(path)
        )  # pyright: ignore[reportPrivateUsage]
        return {
            name: str(data.get("value", ""))
            for name, data in raw_cookies.items()
        }

    @staticmethod
    def _nested_raw_cookie_values(data: dict[Any, Any]) -> dict[str, str]:
        return {
            str(name): str(cast(dict[str, Any], item).get("value", ""))
            for name, item in data.items()
        }

    @staticmethod
    def _flat_raw_cookie_values(data: dict[Any, Any]) -> dict[str, str]:
        return {str(name): str(value) for name, value in data.items()}

    @classmethod
    def _raw_json_cookie_values(cls, data: dict[Any, Any]) -> dict[str, str]:
        if not data:
            return {}
        first_value = next(iter(data.values()))
        if isinstance(first_value, dict) and "value" in first_value:
            return cls._nested_raw_cookie_values(data)
        return cls._flat_raw_cookie_values(data)

    def _load_raw_json_cookies(self, path: Path) -> dict[str, str]:
        try:
            with path.open("r", encoding="utf-8") as file_handle:
                data = json.load(file_handle)
            return (
                self._raw_json_cookie_values(cast(dict[Any, Any], data))
                if isinstance(data, dict)
                else {}
            )
        except Exception as error:
            logger.error(
                f"{self.tracker}: [yellow]Warning: Error parsing cookie file: "
                f"{error}[/yellow]"
            )
            return {}

    def _load_json_cookies(self, path: Path) -> dict[str, str]:
        try:
            return self._load_secure_cookies(path)
        except Exception:
            return self._load_raw_json_cookies(path)

    def _load_cookie_dict(self, cookiefile: str) -> dict[str, str]:
        path = Path(cookiefile)
        if not path.exists():
            return {}
        suffix = path.suffix.lower()
        if suffix == ".txt":
            return self._load_netscape_cookies(path)
        if suffix in {".pkl", ".pickle"}:
            return self._load_pickle_cookies(path)
        return self._load_json_cookies(path)

    async def _confirmed_upload_name(
        self, meta: Meta, filelist_name: str
    ) -> str | None:
        cli_ui.info(f"Filelist name: {filelist_name}")
        if meta.unattended is not False:
            return filelist_name
        confirmed = await prompt_in_thread(
            cli_ui.ask_yes_no, "Correct?", default=False
        )
        if confirmed is True:
            return filelist_name
        manual_name = await prompt_in_thread(
            cli_ui.ask_string, "Please enter a proper name", default=""
        )
        if manual_name != "":
            return str(manual_name)
        logger.info(f"{self.tracker}: No proper name given")
        logger.info(f"{self.tracker}: Aborting...")
        return None

    @staticmethod
    def _torrent_file_name(meta: Meta, filelist_name: str) -> str:
        if meta.anime is True and meta.tag == "-SubsPlease":
            return str(filelist_name)
        return meta.basename_no_ext

    @staticmethod
    def _upload_tmp_dir(meta: Meta) -> Path:
        return Path(meta.base_dir) / "tmp" / meta.uuid

    @classmethod
    def _upload_artifact_paths(cls, meta: Meta) -> tuple[Path, Path, Path]:
        tmp_dir = cls._upload_tmp_dir(meta)
        description = tmp_dir / "[FILELIST]DESCRIPTION.txt"
        torrent = tmp_dir / "[FILELIST].torrent"
        media_info = (
            tmp_dir / "BD_SUMMARY_00.txt"
            if meta.bdinfo
            else tmp_dir / "MEDIAINFO_CLEANPATH.txt"
        )
        return description, torrent, media_info

    @classmethod
    async def _upload_artifacts(
        cls, meta: Meta, torrent_file_name: str
    ) -> tuple[dict[str, tuple[str, bytes, str]], str, str, str]:
        description_path, torrent_path, media_info_path = (
            cls._upload_artifact_paths(meta)
        )
        async with aiofiles.open(
            description_path, newline="", encoding="utf-8"
        ) as description_file:
            description = await description_file.read()
        async with aiofiles.open(
            media_info_path, encoding="utf-8"
        ) as media_file:
            media_info = await media_file.read()
        async with aiofiles.open(torrent_path, "rb") as torrent_file:
            torrent_bytes = await torrent_file.read()
        torrent_name = unidecode(torrent_file_name)
        files = {
            "file": (
                f"{torrent_name}.torrent",
                torrent_bytes,
                "application/x-bittorent",
            )
        }
        return files, description, media_info, str(torrent_path)

    @staticmethod
    def _has_imdb_id(meta: Meta) -> bool:
        value = str(meta.imdb_id if meta.imdb_id is not None else "0")
        return value.isdigit() and int(value) != 0

    @classmethod
    def _add_imdb_upload_data(cls, meta: Meta, data: dict[str, Any]) -> None:
        if not cls._has_imdb_id(meta):
            return
        data["imdbid"] = meta.imdb
        imdb_info = meta.imdb_info if isinstance(meta.imdb_info, dict) else {}
        data["description"] = imdb_info.get("genres", "")

    def _uploader_name_allowed(self) -> bool:
        anon = self.config["TRACKERS"][self.tracker].get("anon", "False")
        return self.uploader_name not in ("", None) and not self._is_true(anon)

    @staticmethod
    def _disc_freeleech_required(meta: Meta) -> bool:
        return meta.is_disc == "BDMV" or meta.type == "REMUX"

    @staticmethod
    def _numeric_flag_enabled(value: object) -> bool:
        normalized = value if value is not None else "0"
        return int(cast(Any, normalized)) != 0

    @classmethod
    def _freeleech_required(cls, meta: Meta) -> bool:
        if cls._disc_freeleech_required(meta):
            return True
        if cls._numeric_flag_enabled(meta.tv_pack):
            return True
        return cls._numeric_flag_enabled(meta.freeleech)

    def _apply_upload_flags(
        self, meta: Meta, data: dict[str, Any], has_ro_audio: bool
    ) -> None:
        if self._uploader_name_allowed():
            data["epenis"] = self.uploader_name
        if has_ro_audio:
            data["materialro"] = "on"
        if self._freeleech_required(meta):
            data["freeleech"] = "on"

    def _upload_data(
        self,
        meta: Meta,
        filelist_name: str,
        category_id: int,
        description: str,
        media_info: str,
        has_ro_audio: bool,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": filelist_name,
            "type": category_id,
            "descr": description.strip(),
            "nfo": media_info,
        }
        self._add_imdb_upload_data(meta, data)
        self._apply_upload_flags(meta, data, has_ro_audio)
        return data

    async def _debug_upload(
        self,
        common: Common,
        meta: Meta,
        url: str,
        data: dict[str, Any],
    ) -> bool:
        logger.debug(url)
        logger.debug(Redaction.redact_private_info(data))
        meta.tracker_status[self.tracker]["status_message"] = (
            "Debug mode enabled, not uploading."
        )
        debug_tracker = f"{self.tracker}_DEBUG"
        await common.create_torrent_for_upload(
            meta,
            debug_tracker,
            debug_tracker,
            announce_url="https://fake.tracker",
        )
        return True

    def _cookie_values(self, meta: Meta) -> dict[str, str]:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookiefile = find_cookie_file(meta.base_dir, self.tracker, self.config)
        return self._load_cookie_dict(cookiefile)

    async def _post_upload(
        self,
        url: str,
        data: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]],
        cookies: dict[str, str],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            cookies=cookies, timeout=60.0, follow_redirects=True
        ) as client:
            return await client.post(url=url, data=data, files=files)

    def _upload_success_match(
        self, response_url: object
    ) -> re.Match[str] | None:
        host = self.base_url.replace("https://", "")
        return re.match(
            rf".*?{re.escape(host)}/details\.php\?id=(\d+)&uploaded=(\d+)",
            str(response_url),
        )

    async def _complete_upload(
        self,
        meta: Meta,
        response: httpx.Response,
        cookies: dict[str, str],
        torrent_path: str,
        data: dict[str, Any],
    ) -> bool:
        match = self._upload_success_match(response.url)
        if match is not None:
            meta.tracker_status[self.tracker]["status_message"] = match.group(
                0
            )
            await self.download_new_torrent(
                cookies, match.group(1), torrent_path
            )
            return True
        logger.info(data)
        logger.info(f"{self.tracker}: \n\n")
        logger.info(response.text)
        raise UploadError(
            f"Upload to FILELIST Failed: result URL {response.url} "
            f"({response.status_code}) was not expected",
            "red",
        )

    async def upload(self, meta: Meta) -> bool:
        common = Common(config=self.config)
        await common.create_torrent_for_upload(
            meta, self.tracker, self.source_flag
        )
        await self.edit_desc(meta)
        filelist_name = await self.get_name(meta)
        category_id = await self.get_category_id(meta)
        has_ro_audio, _has_ro_sub = await self.get_ro_tracks(meta)
        confirmed_name = await self._confirmed_upload_name(meta, filelist_name)
        if confirmed_name is None:
            return False
        torrent_name = self._torrent_file_name(meta, confirmed_name)
        (
            files,
            description,
            media_info,
            torrent_path,
        ) = await self._upload_artifacts(meta, torrent_name)
        data = self._upload_data(
            meta,
            confirmed_name,
            category_id,
            description,
            media_info,
            has_ro_audio,
        )
        url = f"{self.base_url}/takeupload.php"
        if meta.debug:
            return await self._debug_upload(common, meta, url, data)
        cookies = self._cookie_values(meta)
        response = await self._post_upload(url, data, files, cookies)
        return await self._complete_upload(
            meta, response, cookies, torrent_path, data
        )

    @classmethod
    async def _search_params(
        cls, meta: Meta, category_id: int
    ) -> dict[str, Any]:
        if cls._has_imdb_id(meta):
            return {"search": meta.imdb, "cat": category_id, "searchin": "3"}
        return {"search": meta.title, "cat": category_id, "searchin": "0"}

    @staticmethod
    def _dupe_title(anchor: Any) -> str | None:
        href = anchor.get("href")
        title = anchor.get("title")
        if not isinstance(href, str) or not href.startswith("details.php?id="):
            return None
        if "&" in href or not isinstance(title, str):
            return None
        return title

    @classmethod
    def _parse_dupe_titles(cls, html_text: str) -> list[str]:
        soup = BeautifulSoup(html_text, "html.parser")
        titles: list[str] = []
        for anchor in soup.find_all("a", href=True):
            title = cls._dupe_title(anchor)
            if title is not None:
                titles.append(title)
        return titles

    async def search_existing(self, meta: Meta) -> list[str]:
        cookies = self._cookie_values(meta)
        category_id = await self.get_category_id(meta)
        params = await self._search_params(meta, category_id)
        async with httpx.AsyncClient(cookies=cookies, timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/browse.php", params=params
            )
            response.raise_for_status()
            dupes = self._parse_dupe_titles(response.text)
            await asyncio.sleep(0.5)
        return dupes

    @staticmethod
    def _can_relogin(meta: Meta) -> bool:
        return bool(not meta.unattended or meta.unattended_confirm)

    async def _relogin(self, meta: Meta, cookiefile: str) -> bool:
        recreate = await prompt_in_thread(
            cli_ui.ask_yes_no, "Log in again and create new session?"
        )
        if recreate is not True:
            return False
        path = Path(cookiefile)
        if path.exists():
            path.unlink()
        await self.login(cookiefile)
        return await self.validate_cookies(meta, cookiefile)

    async def validate_credentials(self, meta: Meta) -> bool:
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookiefile = find_cookie_file(meta.base_dir, self.tracker, self.config)
        if not Path(cookiefile).exists():
            await self.login(cookiefile)
        if await self.validate_cookies(meta, cookiefile):
            return True
        logger.error(
            f"{self.tracker}: [red]Failed to validate cookies. Please confirm "
            "that the site is up and your passkey is valid."
        )
        if self._can_relogin(meta):
            return await self._relogin(meta, cookiefile)
        return False

    async def validate_cookies(self, meta: Meta, _cookiefile: str) -> bool:
        url = f"{self.base_url}/index.php"
        from src.integrations.trackers.cookie_auth import find_cookie_file

        cookiefile = find_cookie_file(meta.base_dir, self.tracker, self.config)
        cookies = self._load_cookie_dict(cookiefile)
        if cookies:
            async with httpx.AsyncClient(
                cookies=cookies, timeout=30.0
            ) as client:
                resp = await client.get(url=url)
            logger.debug(resp.url)
            return resp.text.find("Logout") != -1
        return False

    async def login(self, cookiefile: str) -> None:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True
        ) as client:
            r = await client.get(f"{self.base_url}/login.php")
            await asyncio.sleep(0.5)
            soup = BeautifulSoup(r.text, "html.parser")
            validator_input = soup.find("input", {"name": "validator"})
            if validator_input is None:
                raise LoginError(
                    "Unable to locate validator input on FILELIST login page."
                )
            validator_value = validator_input.get("value")
            if not isinstance(validator_value, str):
                raise LoginError(
                    "Validator input missing value attribute on FILELIST login page."
                )
            validator = validator_value
            data = {
                "validator": validator,
                "username": self.username,
                "password": self.password,
                "unlock": "1",
            }
            await client.post(f"{self.base_url}/takelogin.php", data=data)
            index = f"{self.base_url}/index.php"
            response = await client.get(index)
            if response.text.find("Logout") != -1:
                logger.info(
                    f"{self.tracker}: [green]Successfully logged into {self.tracker}"
                )
                self.cookie_validator._save_cookies_secure(
                    client.cookies.jar, cookiefile
                )  # pyright: ignore[reportPrivateUsage]
            else:
                logger.info(
                    f"{self.tracker}: [bold red]Something went wrong while trying to log into {self.tracker}"
                )
                logger.info(response.url)
        return

    async def download_new_torrent(
        self, cookies: dict[str, str], id: str, torrent_path: str
    ) -> None:
        download_url = f"{self.base_url}/download.php?id={id}"
        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            r = await client.get(url=download_url)
        if r.status_code == 200:
            async with aiofiles.open(torrent_path, "wb") as tor:
                await tor.write(r.content)
        else:
            logger.info(
                f"{self.tracker}: [red]There was an issue downloading the new .torrent from {self.tracker}"
            )
            logger.info(r.text)
        return

    @staticmethod
    def _description_output_path(meta: Meta, tracker: str) -> Path:
        return (
            Path(meta.base_dir)
            / "tmp"
            / meta.uuid
            / f"[{tracker}]DESCRIPTION.txt"
        )

    @staticmethod
    def _formatted_base_description(meta: Meta) -> str:
        from src.integrations.trackers.bbcode_formatting import BBCODE

        bbcode = BBCODE()
        desc = base_description(meta)
        desc = bbcode.remove_spoiler(desc)
        desc = bbcode.convert_code_to_quote(desc)
        desc = bbcode.convert_comparison_to_centered(desc, 900)
        desc = desc.replace("[img]", "[img]").replace("[/img]", "[/img]")
        return re.sub(r"(\[img=\d+)]", "[img]", desc, flags=re.IGNORECASE)

    @staticmethod
    def _description_api_url() -> str:
        return "https://up.img4k.net/api/description"

    async def _screenshot_upload_files(
        self, meta: Meta
    ) -> list[tuple[str, tuple[str, bytes, str]]]:
        screen_dir = screenshots_dir(meta.base_dir, meta.uuid)
        names = [
            path.name
            for path in screen_dir.glob(f"{glob.escape(meta.filename)}-*.png")
        ]
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for name in names:
            async with aiofiles.open(screen_dir / name, "rb") as image_file:
                image_bytes = await image_file.read()
            files.append(
                ("images", (Path(name).name, image_bytes, "image/png"))
            )
        return files

    def _description_api_auth(self) -> tuple[Any, Any]:
        return self.fltools["user"], self.fltools["pass"]

    async def _post_description_api(
        self,
        *,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._description_api_url(),
                data=data,
                files=files,
                auth=self._description_api_auth(),
            )
        return response.text.replace("\r\n", "\n")

    @classmethod
    def _mediainfo_description_path(cls, meta: Meta) -> Path:
        return cls._upload_tmp_dir(meta) / "MEDIAINFO_CLEANPATH.txt"

    async def _web_description_data(self, meta: Meta) -> dict[str, Any]:
        async with aiofiles.open(
            self._mediainfo_description_path(meta), encoding="utf-8"
        ) as media_file:
            data: dict[str, Any] = {"mediainfo": await media_file.read()}
        if meta.imdb_id:
            data["imdbURL"] = f"tt{meta.imdb_id}"
        return data

    async def _web_description(self, meta: Meta) -> str:
        data = await self._web_description_data(meta)
        files = await self._screenshot_upload_files(meta)
        return await self._post_description_api(data=data, files=files)

    @classmethod
    def _bd_summary_path(cls, meta: Meta) -> Path:
        return cls._upload_tmp_dir(meta) / "BD_SUMMARY_EXT.txt"

    @staticmethod
    def _format_bd_summary(summary: str, base_desc: str) -> str:
        summary = summary.replace(
            "[/pre][/quote]", f"[/pre][/quote]\n\n{base_desc}\n", 1
        )
        summary = (
            summary.replace(
                "DISC INFO:",
                "[pre][quote=BD_Info][b][color=#FF0000]DISC INFO:[/color][/b]",
            )
            .replace(
                "PLAYLIST REPORT:",
                "[b][color=#FF0000]PLAYLIST REPORT:[/color][/b]",
            )
            .replace("VIDEO:", "[b][color=#FF0000]VIDEO:[/color][/b]")
            .replace("AUDIO:", "[b][color=#FF0000]AUDIO:[/color][/b]")
            .replace("SUBTITLES:", "[b][color=#FF0000]SUBTITLES:[/color][/b]")
        )
        return f"{summary}[/pre][/quote]\n"

    async def _bluray_description(self, meta: Meta, base_desc: str) -> str:
        async with aiofiles.open(
            self._bd_summary_path(meta), encoding="utf-8"
        ) as bd_file:
            summary = await bd_file.read()
        if not summary.strip():
            return summary
        formatted = self._format_bd_summary(summary, base_desc)
        files = await self._screenshot_upload_files(meta)
        screenshots = await self._post_description_api(files=files)
        return f"{formatted}{screenshots}"

    async def _generated_description(self, meta: Meta, base_desc: str) -> str:
        if meta.is_disc == "BDMV":
            return await self._bluray_description(meta, base_desc)
        return await self._web_description(meta)

    async def edit_desc(self, meta: Meta) -> None:
        base_desc = self._formatted_base_description(meta)
        final_desc = await self._generated_description(meta, base_desc)
        async with aiofiles.open(
            self._description_output_path(meta, self.tracker),
            "w",
            newline="",
            encoding="utf-8",
        ) as description_file:
            await description_file.write(final_desc)
            if self.signature is not None:
                await description_file.write(self.signature)

    @staticmethod
    def _raw_mediainfo_tracks(meta: Meta) -> list[Any]:
        if not isinstance(meta.mediainfo, dict):
            return []
        media = meta.mediainfo.get("media")
        if not isinstance(media, dict):
            return []
        tracks = media.get("track")
        return cast(list[Any], tracks) if isinstance(tracks, list) else []

    @classmethod
    def _mediainfo_tracks(cls, meta: Meta) -> list[dict[str, Any]]:
        return [
            cast(dict[str, Any], track)
            for track in cls._raw_mediainfo_tracks(meta)
            if isinstance(track, dict)
        ]

    @staticmethod
    def _ro_mediainfo_tracks(
        tracks: list[dict[str, Any]],
    ) -> tuple[bool, bool]:
        has_audio = any(
            track.get("@type") == "Audio" and track.get("Audio") == "ro"
            for track in tracks
        )
        has_subtitle = any(
            track.get("@type") == "Text" and track.get("Language") == "ro"
            for track in tracks
        )
        return has_audio, has_subtitle

    @staticmethod
    def _bdinfo_dict(meta: Meta) -> dict[str, Any]:
        return meta.bdinfo if isinstance(meta.bdinfo, dict) else {}

    @classmethod
    def _ro_bd_subtitle(cls, meta: Meta) -> bool:
        subtitles = cls._bdinfo_dict(meta).get("subtitles")
        return bool(isinstance(subtitles, list) and "Romanian" in subtitles)

    @classmethod
    def _ro_bd_audio(cls, meta: Meta) -> bool:
        audio_tracks = cls._bdinfo_dict(meta).get("audio")
        if not isinstance(audio_tracks, list):
            return False
        for audio_track in audio_tracks:
            if not isinstance(audio_track, dict):
                continue
            if audio_track.get("language") == "Romanian":
                return True
        return False

    @classmethod
    def _ro_bd_tracks(cls, meta: Meta) -> tuple[bool, bool]:
        return cls._ro_bd_audio(meta), cls._ro_bd_subtitle(meta)

    async def get_ro_tracks(self, meta: Meta) -> tuple[bool, bool]:
        if meta.is_disc == "BDMV":
            return self._ro_bd_tracks(meta)
        return self._ro_mediainfo_tracks(self._mediainfo_tracks(meta))
