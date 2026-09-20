# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import http.cookiejar
import json
import re
import stat
import traceback
from pathlib import Path
from typing import Any, cast

import aiofiles
import httpx
from bs4 import BeautifulSoup
from bs4.element import AttributeValueList

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger
from src.integrations.trackers.common import Common


def _attr_to_string(value: str | AttributeValueList | None) -> str:
    """Convert BeautifulSoup attribute values to a plain string."""
    if isinstance(value, str):
        return value
    if isinstance(value, AttributeValueList):
        return " ".join(value)
    return ""


def _clean_upload_error_text(value: str) -> str:
    return " ".join(value.split()).strip(" :-")


def _notification_upload_error(soup: BeautifulSoup) -> str:
    for element in soup.select(".notification-border-e .notification-body"):
        message = _clean_upload_error_text(element.get_text(" ", strip=True))
        if message:
            return message
    return ""


def _error_parent_message(element: Any, element_message: str) -> str:
    for parent in [element, *element.parents]:
        message = _clean_upload_error_text(parent.get_text(" ", strip=True))
        if not len(message) > len(element_message) or len(message) >= 500:
            continue
        message = re.sub(
            r"^(?:erro|error)\b", "", message, flags=re.IGNORECASE
        )
        cleaned = _clean_upload_error_text(message)
        if cleaned:
            return cleaned
    return ""


def _class_upload_error(soup: BeautifulSoup) -> str:
    for element in soup.select("[class*='error']"):
        element_message = _clean_upload_error_text(
            element.get_text(" ", strip=True)
        )
        if element_message and element_message.lower() not in {
            "error",
            "erro",
        }:
            return element_message
        parent_message = _error_parent_message(element, element_message)
        if parent_message:
            return parent_message
    return ""


def _heading_upload_error(soup: BeautifulSoup) -> str:
    for heading in soup.select("h1, h2, h3, h4"):
        heading_text = heading.get_text(" ", strip=True)
        if not re.search(r"error|failed", heading_text, re.IGNORECASE):
            continue
        sibling = heading.find_next_sibling(["p", "div"])
        if sibling is None:
            continue
        message = _clean_upload_error_text(sibling.get_text(" ", strip=True))
        if message:
            return message
    return ""


def _blocked_upload_notice(soup: BeautifulSoup) -> str:
    selector = "h1.dnu_header, h2.dnu_header, h3.dnu_header, #dnu_header"
    for heading in soup.select(selector):
        message = _clean_upload_error_text(heading.get_text(" ", strip=True))
        if re.search(
            r"proib|permitid|not allowed|forbidden", message, re.IGNORECASE
        ):
            return message
    return ""


def _legacy_parent_error(text_parent: Any) -> str:
    for parent in [text_parent, *text_parent.parents]:
        message = _clean_upload_error_text(parent.get_text(" ", strip=True))
        if len(message) > 500:
            continue
        if not re.match(
            r"^(?:error\s*:|upload failed!)", message, re.IGNORECASE
        ):
            continue
        message = re.sub(
            r"^(?:error\s*:|upload failed!?)\s*",
            "",
            message,
            flags=re.IGNORECASE,
        )
        message = re.sub(r"\s+Back$", "", message, flags=re.IGNORECASE)
        cleaned = _clean_upload_error_text(message)
        if cleaned:
            return cleaned
    return ""


def _legacy_upload_error(soup: BeautifulSoup) -> str:
    selector = "td, div, p, h1, h2, h3, h4, b, span, font"
    for text_parent in soup.select(selector):
        raw_message = _clean_upload_error_text(
            text_parent.get_text(" ", strip=True)
        )
        if not re.match(
            r"^(?:error\s*:|upload failed!)", raw_message, re.IGNORECASE
        ):
            continue
        message = _legacy_parent_error(text_parent)
        if message:
            return message
    return ""


def extract_upload_error(html: str) -> str:
    """Extract the useful error message from common tracker upload pages."""
    soup = BeautifulSoup(html, "html.parser")
    extractors = (
        _notification_upload_error,
        _class_upload_error,
        _heading_upload_error,
        _blocked_upload_notice,
        _legacy_upload_error,
    )
    for extractor in extractors:
        message = extractor(soup)
        if message:
            return message
    return ""


_TRACKER_FALLBACK_DOMAINS = {
    "amigosshare": "amigos-share.club",
    "avistaz": "avistaz.to",
    "bjshare": "bj-share.info",
    "brasiltracker": "brasiltracker.org",
    "cinemaz": "cinemaz.to",
    "greatposterwall": "greatposterwall.com",
    "hdbits": "hdbits.org",
    "hdspace": "hd-space.org",
    "hdtorrents": "hd-torrents.org",
    "iptorrents": "iptorrents.com",
    "immortalseed": "immortalseed.me",
    "lajidui": "lajidui.top",
    "longpt": "longpt.org",
    "privatehd": "privatehd.to",
    "ptcafe": "ptcafe.club",
    "ptfans": "ptfans.cc",
    "ptgtk": "gtkpw.xyz",
    "ptskit": "ptskit.org",
    "railgunpt": "bilibili.download",
    "torrentleech": "torrentleech.org",
    "filelist": "filelist.io",
    "passthepopcorn": "passthepopcorn.me",
    "pterclub": "pterclub.com",
}


def _normalized_domain(url: str) -> str:
    from urllib.parse import urlparse

    netloc = urlparse(url).netloc.lower().lstrip(".")
    return netloc[4:] if netloc.startswith("www.") else netloc


def _registry_tracker_domain(tracker: str) -> str:
    try:
        from src.integrations.trackers.registry import tracker_class_map

        tracker_class = tracker_class_map.get(tracker.upper())
        base_url = getattr(tracker_class, "base_url", "")
        if not isinstance(base_url, str):
            return ""
        return _normalized_domain(base_url)
    except Exception as error:
        logger.error(
            f"[yellow]Warning: Error getting tracker domain: {error}[/yellow]"
        )
        return ""


def _tracker_config(
    config: dict[str, Any] | None, tracker: str
) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    trackers = config.get("TRACKERS", {})
    if not isinstance(trackers, dict):
        return {}
    value = cast(dict[str, Any], trackers).get(tracker, {})
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _configured_tracker_domain(
    config: dict[str, Any] | None, tracker: str
) -> str:
    announce_url = str(
        _tracker_config(config, tracker).get("announce_url", "")
    )
    if not announce_url:
        return ""
    try:
        return _normalized_domain(announce_url)
    except Exception as error:
        logger.error(
            f"[yellow]Warning: Error getting tracker domain: {error}[/yellow]"
        )
        return ""


def get_tracker_domain(
    tracker: str, config: dict[str, Any] | None = None
) -> str:
    """Extract or map a tracker name to its primary domain name."""
    domain = _registry_tracker_domain(tracker)
    if domain:
        return domain
    domain = _configured_tracker_domain(config, tracker)
    if domain:
        return domain
    tracker_lower = tracker.lower()
    return _TRACKER_FALLBACK_DOMAINS.get(tracker_lower, tracker_lower)


def _custom_cookie_file(config: dict[str, Any] | None, tracker: str) -> str:
    tracker_config = _tracker_config(config, tracker)
    cookie_file = tracker_config.get("cookie_file", "")
    cookies_value = tracker_config.get("cookies", "")
    for value in (cookie_file, cookies_value):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolved_custom_cookie_path(
    base_dir: str, cookies_dir: Path, custom_cookie_file: str
) -> Path:
    custom_path = Path(custom_cookie_file)
    if custom_path.is_absolute():
        return custom_path.resolve()
    if len(custom_path.parts) > 1 and custom_path.parts[0] == "data":
        return (Path(base_dir) / custom_cookie_file).resolve()
    return (cookies_dir / custom_cookie_file).resolve()


def _cookie_directory_files(cookies_dir: Path) -> list[Path]:
    return sorted(
        (path for path in cookies_dir.glob("*") if path.is_file()),
        key=lambda path: path.name,
    )


def _exact_cookie_matches(files: list[Path], tracker_lower: str) -> list[Path]:
    return [path for path in files if path.stem.lower() == tracker_lower]


def _partial_cookie_matches(
    files: list[Path], tracker_lower: str
) -> list[Path]:
    return [path for path in files if tracker_lower in path.name.lower()]


def _filename_cookie_matches(files: list[Path], tracker: str) -> list[Path]:
    tracker_lower = tracker.lower()
    exact = _exact_cookie_matches(files, tracker_lower)
    return exact if exact else _partial_cookie_matches(files, tracker_lower)


def _cookie_file_contains_domain(file_path: Path, domain: str) -> bool:
    if file_path.suffix.lower() not in {".txt", ".json"}:
        return False
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            return domain in handle.read(10240).lower()
    except Exception as error:
        logger.error(
            f"[yellow]Warning: Error reading cookie file: {error}[/yellow]"
        )
        return False


def _domain_cookie_matches(
    files: list[Path], tracker: str, config: dict[str, Any] | None
) -> list[Path]:
    domain = get_tracker_domain(tracker, config)
    if not domain:
        return []
    return [
        path for path in files if _cookie_file_contains_domain(path, domain)
    ]


def _warn_multiple_cookie_files(tracker: str, files: list[Path]) -> None:
    if len(files) <= 1:
        return
    names = ", ".join(path.name for path in files)
    logger.warning(
        f"[yellow]{tracker}: Found multiple cookie files ({names}). Using the first one by default: {files[0].name}[/yellow]"
    )


def _default_cookie_path(cookies_dir: Path, tracker: str) -> Path:
    json_names = {
        "FILELIST": "FILELIST.json",
        "PASSTHEPOPCORN": "PASSTHEPOPCORN.json",
        "Pterimg": "Pterimg.json",
    }
    filename = json_names.get(tracker, f"{tracker}.txt")
    return (cookies_dir / filename).resolve()


def find_cookie_file(
    base_dir: str, tracker: str, config: dict[str, Any] | None = None
) -> str:
    """Find the best cookie file for a tracker without executing its contents."""
    cookies_dir = Path(base_dir) / "data" / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    custom_cookie_file = _custom_cookie_file(config, tracker)
    if custom_cookie_file:
        return str(
            _resolved_custom_cookie_path(
                base_dir, cookies_dir, custom_cookie_file
            )
        )
    files = _cookie_directory_files(cookies_dir)
    matching_files = _filename_cookie_matches(files, tracker)
    if not matching_files:
        matching_files = _domain_cookie_matches(files, tracker, config)
    if matching_files:
        _warn_multiple_cookie_files(tracker, matching_files)
        return str(matching_files[0].resolve())
    return str(_default_cookie_path(cookies_dir, tracker))


class CookieValidator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _load_cookie_jar_status(
        cookie_jar: http.cookiejar.MozillaCookieJar, tracker: str
    ) -> str:
        try:
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
            return "loaded"
        except http.cookiejar.LoadError as error:
            logger.info(f"{tracker}: Failed to load the cookie file: {error}")
            logger.info(
                f"{tracker}: Please ensure the cookie file is in the correct format (Netscape)."
            )
            return "invalid"
        except FileNotFoundError:
            return "missing"

    @staticmethod
    def _log_missing_cookie_file(tracker: str, cookie_file: str) -> None:
        logger.info(
            f"{tracker}: [red]Cookie file not found.[/red]\n"
            f"{tracker}: You must first log in through your usual browser and export the cookies to: [yellow]{cookie_file}[/yellow]\n"
            f'{tracker}: Cookies can be exported using browser extensions like "cookies.txt" (Firefox) or "Get cookies.txt LOCALLY" (Chrome).'
        )

    async def _recover_alpharatio_cookie(
        self,
        meta: Meta,
        tracker: str,
        cookie_file: str,
        cookie_jar: http.cookiejar.MozillaCookieJar,
    ) -> http.cookiejar.MozillaCookieJar | None:
        logger.info(
            f"{tracker}: [yellow]Cookie file not found. Attempting automatic login...[/yellow]"
        )
        if not await self.ar_login(meta, tracker, cookie_file):
            logger.info(f"{tracker}: Automatic login failed.")
            return None
        try:
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
            return cookie_jar
        except Exception as error:
            logger.info(
                f"{tracker}: Failed to load cookies after login: {error}"
            )
            return None

    async def load_session_cookies(
        self, meta: Meta, tracker: str
    ) -> http.cookiejar.MozillaCookieJar | None:
        cookie_file = find_cookie_file(meta.base_dir, tracker, self.config)
        cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
        status = self._load_cookie_jar_status(cookie_jar, tracker)
        if status == "loaded":
            return cookie_jar
        if status == "invalid":
            return None
        if tracker == "ALPHARATIO":
            return await self._recover_alpharatio_cookie(
                meta, tracker, cookie_file, cookie_jar
            )
        self._log_missing_cookie_file(tracker, cookie_file)
        return None

    async def save_session_cookies(
        self, tracker: str, cookie_jar: http.cookiejar.MozillaCookieJar | None
    ) -> None:
        """Save updated cookies after a successful validation."""
        if not cookie_jar:
            logger.info(
                f"{tracker}: Cookie jar not initialized, cannot save cookies."
            )
            return

        try:
            cookie_jar.save(ignore_discard=True, ignore_expires=True)
        except Exception as e:
            logger.info(f"{tracker}: Failed to update the cookie file: {e}")

    async def get_ar_auth_key(self, meta: Meta, tracker: str) -> str | None:
        """Retrieve the saved auth key for ALPHARATIO tracker."""
        cookie_file = find_cookie_file(meta.base_dir, tracker, self.config)
        auth_file = cookie_file.replace(".txt", "_auth.txt")

        if Path(auth_file).exists():
            try:
                async with aiofiles.open(auth_file, encoding="utf-8") as f:
                    auth_key = await f.read()
                    auth_key = auth_key.strip()
                    if auth_key:
                        return auth_key
            except Exception as e:
                logger.info(f"{tracker}: Error reading auth key: {e}")

        return None

    def _ar_credentials(self, tracker: str) -> tuple[str, str]:
        tracker_config = _tracker_config(self.config, tracker)
        username = str(tracker_config.get("username", "")).strip()
        password = str(tracker_config.get("password", "")).strip()
        return username, password

    @staticmethod
    def _user_agent_headers(meta: Meta) -> dict[str, str]:
        version = (
            meta.current_version
            if meta.current_version is not None
            else "github.com/wastaken7/Upload-Assistant"
        )
        return {"User-Agent": f"{meta.ua_name} {version}"}

    @staticmethod
    def _ar_login_response_failed(response: httpx.Response) -> bool:
        return any(
            marker in response.text
            for marker in ("login.php?act=recover", "Forgot your password")
        )

    async def _save_ar_login_failure(
        self, meta: Meta, tracker: str, html: str
    ) -> None:
        logger.info(
            f"{tracker}: [red]Login failed. Please check your username and password.[/red]"
        )
        if not meta.debug:
            return
        failure_path = await self.common.save_html_file(
            meta, tracker, html, "Failed_Login"
        )
        logger.debug(
            f"{tracker}: Login response saved to [yellow]{failure_path}[/yellow] for debugging."
        )

    @staticmethod
    def _ar_session_valid(response: httpx.Response) -> bool:
        return (
            response.status_code == 200
            and "login.php?act=recover" not in response.text
        )

    @staticmethod
    def _ar_auth_key(html: str, tracker: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        logout_link = cast(Any, soup).find("a", href=True, string="Logout")
        if logout_link is None:
            return None
        href = _attr_to_string(logout_link.get("href"))
        match = re.search(r"auth=([^&]+)", href)
        if match is None:
            return None
        logger.info(
            f"{tracker}: [green]Auth key extracted successfully[/green]"
        )
        return match.group(1)

    @staticmethod
    def _httpx_cookie_by_name(cookies: Any, name: str) -> Any | None:
        for cookie in cookies.jar:
            if cookie.name == name:
                return cookie
        return None

    @staticmethod
    def _mozilla_cookie(cookie: Any) -> http.cookiejar.Cookie:
        rest = getattr(cookie, "_rest", {})
        rest_map = cast(dict[str, Any], rest) if isinstance(rest, dict) else {}
        domain = cookie.domain or ".alpharatio.cc"
        return http.cookiejar.Cookie(
            version=0,
            name=cookie.name,
            value=cookie.value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=cookie.path if cookie.path else "/",
            path_specified=True,
            secure=bool(rest_map.get("secure")) if rest_map else True,
            expires=None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )

    @classmethod
    def _save_ar_cookies(
        cls, client: Any, cookie_file: str, tracker: str
    ) -> None:
        Path(cookie_file).parent.mkdir(parents=True, exist_ok=True)
        cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
        for cookie_name in client.cookies:
            cookie = cls._httpx_cookie_by_name(client.cookies, cookie_name)
            if cookie is not None:
                cookie_jar.set_cookie(cls._mozilla_cookie(cookie))
        cookie_jar.save(ignore_discard=True, ignore_expires=True)
        logger.info(
            f"{tracker}: [green]Cookies saved to {cookie_file}[/green]"
        )

    @staticmethod
    async def _save_ar_auth_key(
        cookie_file: str, auth_key: str | None, tracker: str
    ) -> None:
        if not auth_key:
            return
        auth_file = cookie_file.replace(".txt", "_auth.txt")
        async with aiofiles.open(auth_file, "w", encoding="utf-8") as handle:
            await handle.write(auth_key)
        logger.info(f"{tracker}: [green]Auth key saved to {auth_file}[/green]")

    @staticmethod
    def _log_ar_login_exception(tracker: str, error: Exception) -> None:
        if isinstance(error, httpx.TimeoutException):
            logger.info(
                f"{tracker}: Connection timed out. The site may be down or unreachable."
            )
            return
        if isinstance(error, httpx.ConnectError):
            logger.info(
                f"{tracker}: Failed to connect. The site may be down or your connection is blocked."
            )
            return
        logger.info(f"{tracker}: Login error: {error}")
        logger.debug(traceback.format_exc())

    async def _perform_ar_login(
        self,
        meta: Meta,
        tracker: str,
        cookie_file: str,
        username: str,
        password: str,
    ) -> bool:
        base_url = "https://alpharatio.cc"
        login_data = {
            "username": username,
            "password": password,
            "keeplogged": "1",
            "login": "Login",
        }
        async with httpx.AsyncClient(
            headers=self._user_agent_headers(meta),
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                f"{base_url}/login.php", data=login_data
            )
            if response.status_code != 200:
                logger.info(
                    f"{tracker}: Login failed with status code {response.status_code}"
                )
                return False
            if self._ar_login_response_failed(response):
                await self._save_ar_login_failure(meta, tracker, response.text)
                return False
            test_response = await client.get(f"{base_url}/torrents.php")
            if not self._ar_session_valid(test_response):
                logger.info(f"{tracker}: [red]Login validation failed.[/red]")
                return False
            logger.info(f"{tracker}: [green]Login successful![/green]")
            auth_key = self._ar_auth_key(test_response.text, tracker)
            self._save_ar_cookies(client, cookie_file, tracker)
            await self._save_ar_auth_key(cookie_file, auth_key, tracker)
            return True

    async def ar_login(
        self, meta: Meta, tracker: str, cookie_file: str
    ) -> bool:
        """Perform automatic login to ALPHARATIO and save cookies in Netscape format."""
        username, password = self._ar_credentials(tracker)
        if not username or not password:
            logger.info(
                f"{tracker}: Username or password not configured in config."
            )
            return False
        try:
            return await self._perform_ar_login(
                meta, tracker, cookie_file, username, password
            )
        except Exception as error:
            self._log_ar_login_exception(tracker, error)
            return False

    @staticmethod
    def _basic_validation_failed(
        response: httpx.Response,
        status_code: str,
        error_text: str,
        success_text: str,
    ) -> bool:
        checks = (
            bool(success_text and success_text not in response.text),
            bool(error_text and error_text in response.text),
            bool(status_code and response.status_code != int(status_code)),
        )
        return any(checks)

    @staticmethod
    def _validation_token(
        text: str, token_pattern: str
    ) -> tuple[bool, str | None]:
        if not token_pattern:
            return True, None
        match = re.search(token_pattern, text)
        if match is None:
            return False, None
        return True, str(match.group(1))

    @classmethod
    def _validation_result(
        cls,
        response: httpx.Response,
        status_code: str,
        error_text: str,
        success_text: str,
        token_pattern: str,
    ) -> tuple[bool, str | None]:
        if cls._basic_validation_failed(
            response, status_code, error_text, success_text
        ):
            return False, None
        return cls._validation_token(response.text, token_pattern)

    @staticmethod
    def _set_tracker_secret_token(tracker: str, token: str | None) -> None:
        if token is None:
            return
        from src.integrations.trackers.registry import tracker_class_map

        tracker_class = tracker_class_map.get(tracker.upper())
        if tracker_class is not None:
            tracker_class.secret_token = token

    @staticmethod
    def _simple_validation_exception_message(error: Exception) -> str | None:
        simple_messages: tuple[tuple[type[Exception], str], ...] = (
            (
                httpx.ConnectTimeout,
                "Connection timeout. Server took too long to respond.",
            ),
            (
                httpx.ReadTimeout,
                "Read timeout. Data transfer stopped prematurely.",
            ),
            (
                httpx.ConnectError,
                "Connection failed. Check URL, port, and network status.",
            ),
            (httpx.ProxyError, "Proxy error. Failed to connect via proxy."),
            (
                httpx.DecodingError,
                "Decoding failed. Response content is not valid (e.g., unexpected encoding).",
            ),
            (
                httpx.TooManyRedirects,
                "Too many redirects. Request exceeded the maximum redirect limit.",
            ),
        )
        for error_type, message in simple_messages:
            if isinstance(error, error_type):
                return message
        return None

    @classmethod
    def _validation_exception_message(
        cls, tracker: str, error: Exception
    ) -> str:
        simple = cls._simple_validation_exception_message(error)
        if simple is not None:
            return f"{tracker}: {simple}"
        if isinstance(error, httpx.HTTPStatusError):
            reason = error.response.reason_phrase or "Unknown Reason"
            return (
                f"{tracker}: HTTP status error {error.response.status_code}: "
                f"{reason} for {error.request.url}"
            )
        if isinstance(error, httpx.RequestError):
            return f"{tracker}: General request error: {error}"
        return f"{tracker}: Unexpected validation error: {error}"

    async def cookie_validation(
        self,
        meta: Meta,
        tracker: str,
        test_url: str = "",
        status_code: str = "",
        error_text: str = "",
        success_text: str = "",
        token_pattern: str = "",
    ) -> bool:
        """Validate tracker cookies and optionally capture the tracker token."""
        cookie_jar = await self.load_session_cookies(meta, tracker)
        if not cookie_jar:
            return False
        try:
            async with httpx.AsyncClient(
                headers=self._user_agent_headers(meta),
                timeout=20.0,
                cookies=cookie_jar,
            ) as session:
                response = await session.get(test_url)
                valid, token = self._validation_result(
                    response,
                    status_code,
                    error_text,
                    success_text,
                    token_pattern,
                )
                if not valid:
                    await self.handle_validation_failure(
                        meta, tracker, response.text
                    )
                    return False
                self._set_tracker_secret_token(tracker, token)
                await self.save_session_cookies(tracker, cookie_jar)
                return True
        except Exception as error:
            logger.info(self._validation_exception_message(tracker, error))
            return False

    async def handle_validation_failure(
        self, meta: Meta, tracker: str, text: str
    ) -> None:
        logger.info(
            f"{tracker}: Validation failed. The cookie appears to be expired or invalid.\n{tracker}: Please log in through your usual browser and export the cookies again."
        )
        failure_path = await self.common.save_html_file(
            meta, tracker, text, "Failed_Login"
        )
        logger.info(
            f"The web page has been saved to [yellow]{failure_path}[/yellow] for analysis.\n"
            "[red]Do not share this file publicly[/red], as it may contain confidential information such as passkeys, IP address, e-mail, etc.\n"
            "You can open this file in a web browser to see what went wrong.\n"
        )

        return

    async def find_html_token(
        self, tracker: str, token_pattern: str, response: str
    ) -> str | None:
        """Find the auth token in a web page using a regular expression pattern."""
        auth_match = re.search(token_pattern, response)
        if not auth_match:
            logger.info(
                f"{tracker}: The required token could not be found in the page's HTML. Pattern used: {token_pattern}\n"
                f"{tracker}: This can happen if the site HTML has changed or if the login failed silently."
            )
            return None
        return str(auth_match.group(1))

    def _save_cookies_secure(
        self, session_cookies: Any, cookiefile: str
    ) -> None:
        """Securely save session cookies using JSON instead of pickle"""
        try:
            # Convert RequestsCookieJar to dictionary for JSON serialization
            cookie_dict = {}
            for cookie in session_cookies:
                cookie_dict[cookie.name] = {
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }

            with Path(cookiefile).open("w", encoding="utf-8") as f:
                json.dump(cookie_dict, f, indent=2)

            # Set restrictive permissions (0o600) to protect cookie secrets
            Path(cookiefile).chmod(stat.S_IRUSR | stat.S_IWUSR)

        except OSError as e:
            logger.error(f"[red]Error with cookie file operations: {e}[/red]")
            raise
        except (TypeError, ValueError) as e:
            logger.error(f"[red]Error encoding cookies to JSON: {e}[/red]")
            raise

    def _load_cookies_secure(
        self, session: Any, cookiefile: str, _tracker: str
    ) -> None:
        """Securely load session cookies from JSON instead of pickle"""

        # Load cookies from JSON file only. Legacy pickle migration is intentionally not automatic
        # to avoid executing untrusted pickle payloads during normal startup.
        try:
            with Path(cookiefile).open(encoding="utf-8") as f:
                cookie_dict = json.load(f)

            # Convert dictionary back to session cookies
            for name, cookie_data in cookie_dict.items():
                # Prevent None domain values
                domain = cookie_data.get("domain")
                if domain is None:
                    domain = ""  # Use empty string instead of None

                session.cookies.set(
                    name=name,
                    value=cookie_data["value"],
                    domain=domain,
                    path=cookie_data.get("path", "/"),
                    secure=cookie_data.get("secure", False),
                )

        except OSError as e:
            logger.error(f"[red]Error reading cookie file: {e}[/red]")
            raise
        except json.JSONDecodeError as e:
            logger.error(
                f"[red]Error decoding JSON from cookie file: {e}[/red]"
            )
            raise

    def _load_cookies_dict_secure(self, cookiefile: str) -> dict[str, Any]:
        """Securely load cookies as dictionary from JSON instead of pickle"""
        try:
            with Path(cookiefile).open(encoding="utf-8") as f:
                cookie_dict = json.load(f)
            return cast(dict[str, Any], cookie_dict)
        except OSError as e:
            logger.error(f"[red]Error reading cookie file: {e}[/red]")
            raise
        except json.JSONDecodeError as e:
            logger.error(
                f"[red]Error decoding JSON from cookie file: {e}[/red]"
            )
            raise


class CookieAuthUploader:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.common = Common(config)

    @staticmethod
    def _success_criteria_error(
        success_status_code: str,
        error_text: str,
        success_text: str,
        success_list: list[str] | None,
    ) -> str:
        values: tuple[str | list[str] | None, ...] = (
            success_status_code,
            error_text,
            success_text,
            success_list,
        )
        count = sum(bool(value) for value in values)
        if count == 1:
            return ""
        if count == 0:
            return "You must provide at least one of: success_status_code, error_text, success_text, or success_list."
        return "Only one of success_status_code, error_text, success_text, or success_list should be provided."

    @staticmethod
    def _valid_upload_status_codes(value: str) -> set[int]:
        return {
            int(code.strip())
            for code in value.split(",")
            if code.strip().isdigit()
        }

    @staticmethod
    def _success_list_matches(
        text: str, success_list: list[str] | None
    ) -> bool:
        return bool(
            success_list and any(item in text for item in success_list)
        )

    @classmethod
    def _upload_response_succeeded(
        cls,
        response: httpx.Response,
        success_status_code: str,
        error_text: str,
        success_text: str,
        success_list: list[str] | None,
    ) -> bool:
        if success_text:
            return success_text in response.text
        if success_list:
            return cls._success_list_matches(response.text, success_list)
        if success_status_code:
            return response.status_code in cls._valid_upload_status_codes(
                success_status_code
            )
        return bool(error_text and error_text not in response.text)

    @staticmethod
    def _upload_exception_message(error: Exception) -> str:
        simple_messages: tuple[tuple[type[Exception], str], ...] = (
            (httpx.ConnectTimeout, "Connection timed out"),
            (httpx.ReadTimeout, "Read timed out"),
            (httpx.ConnectError, "Failed to connect to the server"),
            (httpx.ProxyError, "Proxy connection failed"),
            (httpx.DecodingError, "Response decoding failed"),
            (httpx.TooManyRedirects, "Too many redirects"),
        )
        for error_type, message in simple_messages:
            if isinstance(error, error_type):
                return message
        if isinstance(error, httpx.HTTPStatusError):
            return f"HTTP error {error.response.status_code}: {error}"
        if isinstance(error, httpx.RequestError):
            return f"Request error: {error}"
        return f"Unexpected upload error: {error}"

    @staticmethod
    async def _post_upload_request(
        meta: Meta,
        upload_cookies: Any,
        upload_url: str,
        data: dict[str, Any],
        files: dict[str, Any],
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            headers=CookieValidator._user_agent_headers(meta),
            timeout=30.0,
            cookies=upload_cookies,
            follow_redirects=True,
        ) as session:
            return await session.post(upload_url, data=data, files=files)

    async def _debug_upload(
        self, meta: Meta, tracker: str, data: dict[str, Any]
    ) -> bool:
        if not meta.debug:
            return False
        self.upload_debug(tracker, data)
        meta.tracker_status[tracker]["status_message"] = (
            "Debug mode enabled, not uploading"
        )
        await self.common.create_torrent_for_upload(
            meta,
            f"{tracker}_DEBUG",
            f"{tracker}_DEBUG",
            announce_url="https://fake.tracker",
        )
        return True

    async def _prepared_upload_files(
        self,
        meta: Meta,
        tracker: str,
        torrent_field_name: str,
        torrent_name: str,
        source_flag: str,
        default_announce: str,
        additional_files: dict[str, Any] | None,
    ) -> dict[str, Any]:
        files: dict[str, Any] = await self.load_torrent_file(
            meta,
            tracker,
            torrent_field_name,
            torrent_name,
            source_flag,
            default_announce,
        )
        if additional_files:
            files.update(additional_files)
        return files

    async def _dispatch_upload_response(
        self,
        meta: Meta,
        tracker: str,
        response: httpx.Response,
        success_status_code: str,
        success_text: str,
        error_text: str,
        success_list: list[str] | None,
        id_pattern: str,
        hash_is_id: bool,
        source_flag: str,
        user_announce_url: str,
        torrent_url: str,
    ) -> bool:
        if self._upload_response_succeeded(
            response,
            success_status_code,
            error_text,
            success_text,
            success_list,
        ):
            return await self.handle_successful_upload(
                meta,
                tracker,
                response,
                id_pattern,
                hash_is_id,
                source_flag,
                user_announce_url,
                torrent_url,
            )
        return await self.handle_failed_upload(
            meta,
            tracker,
            success_status_code,
            success_text,
            error_text,
            response,
            success_list,
        )

    async def _submit_upload(
        self,
        meta: Meta,
        tracker: str,
        upload_cookies: Any,
        upload_url: str,
        data: dict[str, Any],
        files: dict[str, Any],
        success_status_code: str,
        success_text: str,
        error_text: str,
        success_list: list[str] | None,
        id_pattern: str,
        hash_is_id: bool,
        source_flag: str,
        user_announce_url: str,
        torrent_url: str,
    ) -> bool:
        try:
            response = await self._post_upload_request(
                meta, upload_cookies, upload_url, data, files
            )
        except Exception as error:
            meta.tracker_status[tracker]["status_message"] = (
                self._upload_exception_message(error)
            )
            await self.common.create_torrent_ready_to_seed(
                meta, tracker, source_flag, user_announce_url, torrent_url
            )
            return False
        return await self._dispatch_upload_response(
            meta,
            tracker,
            response,
            success_status_code,
            success_text,
            error_text,
            success_list,
            id_pattern,
            hash_is_id,
            source_flag,
            user_announce_url,
            torrent_url,
        )

    async def handle_upload(
        self,
        meta: Meta,
        tracker: str,
        source_flag: str,
        torrent_url: str,
        data: dict[str, Any],
        torrent_field_name: str,
        upload_cookies: Any,
        upload_url: str,
        default_announce: str = "",
        torrent_name: str = "",
        id_pattern: str = "",
        success_status_code: str = "",
        error_text: str = "",
        success_text: str = "",
        success_list: list[str] | None = None,
        additional_files: dict[str, Any] | None = None,
        hash_is_id: bool = False,
    ) -> bool:
        """
        Upload a torrent to a tracker using cookies for authentication.
        Return True if the upload is successful, False otherwise.

        1.  Create the [tracker].torrent file and set the source flag.
            Uses default_announce if provided as some trackers require it.

        2.  Load the torrent file into memory.
        3.  Post the torrent file and form data to the provided upload URL using the provided cookies.
        4.  Check the response for success indicators.
        5.  Handle success or failure accordingly.

        A successful upload will create a torrent entry with the announce URL and torrent ID (if applicable).
        A failed upload will save the response HTML for analysis and also create a torrent entry with the announce URL,
        as the upload may have partially succeeded.
        """
        criteria_error = self._success_criteria_error(
            success_status_code,
            error_text,
            success_text,
            success_list,
        )
        if criteria_error:
            meta.tracker_status[tracker]["status_message"] = criteria_error
            return False

        user_announce_url = str(
            _tracker_config(self.config, tracker).get("announce_url", "")
        )
        files = await self._prepared_upload_files(
            meta,
            tracker,
            torrent_field_name,
            torrent_name,
            source_flag,
            default_announce,
            additional_files,
        )
        if await self._debug_upload(meta, tracker, data):
            return True
        return await self._submit_upload(
            meta,
            tracker,
            upload_cookies,
            upload_url,
            data,
            files,
            success_status_code,
            success_text,
            error_text,
            success_list,
            id_pattern,
            hash_is_id,
            source_flag,
            user_announce_url,
            torrent_url,
        )

    @staticmethod
    def _is_sensitive_form_key(key: str) -> bool:
        sensitive_keywords = ("password", "passkey", "auth", "csrf", "token")
        return any(keyword in key.lower() for keyword in sensitive_keywords)

    @classmethod
    def _redacted_form_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            key: "[REDACTED]" if cls._is_sensitive_form_key(key) else value
            for key, value in data.items()
        }

    def upload_debug(self, tracker: str, data: Any) -> None:
        try:
            shown = (
                self._redacted_form_data(cast(dict[str, Any], data))
                if isinstance(data, dict)
                else data
            )
            logger.info(f"{tracker}: Form Data: {shown}")
        except Exception as error:
            logger.info(f"Error displaying form data: {error}")
            raise

    async def load_torrent_file(
        self,
        meta: Meta,
        tracker: str,
        torrent_field_name: str,
        torrent_name: str,
        source_flag: str,
        default_announce: str,
    ) -> dict[str, tuple[str, bytes, str]]:
        """Load the torrent file into memory."""
        await self.common.create_torrent_for_upload(
            meta, tracker, source_flag, announce_url=default_announce
        )
        torrent_path = f"{meta.base_dir}{'/' + 'tmp' + '/'}{meta.uuid}/[{tracker}].torrent"
        async with aiofiles.open(torrent_path, "rb") as f:
            file_bytes = await f.read()

        name = (
            torrent_name
            if torrent_name
            else f"{tracker}.{meta.infohash}.placeholder"
        )

        return {
            torrent_field_name: (
                f"{name}.torrent",
                file_bytes,
                "application/x-bittorrent",
            )
        }

    @staticmethod
    def _response_torrent_id(response: httpx.Response, id_pattern: str) -> str:
        if not id_pattern:
            return ""
        for source in (str(response.url), response.text):
            match = re.search(id_pattern, source)
            if match:
                return match.group(1)
        return ""

    async def handle_successful_upload(
        self,
        meta: Meta,
        tracker: str,
        response: httpx.Response,
        id_pattern: str,
        hash_is_id: bool,
        source_flag: str,
        user_announce_url: str,
        torrent_url: str,
    ) -> bool:
        torrent_id = self._response_torrent_id(response, id_pattern)
        if torrent_id:
            meta.tracker_status[tracker]["torrent_id"] = torrent_id
        torrent_hash = await self.common.create_torrent_ready_to_seed(
            meta,
            tracker,
            source_flag,
            user_announce_url,
            torrent_url + torrent_id,
            hash_is_id=hash_is_id,
        )
        if hash_is_id and torrent_hash is not None:
            meta.tracker_status[tracker]["torrent_id"] = torrent_hash
        meta.tracker_status[tracker]["status_message"] = (
            "Torrent uploaded successfully."
        )
        return True

    @staticmethod
    def _failure_criterion_message(
        success_status_code: str,
        success_text: str,
        error_text: str,
        success_list: list[str] | None,
        response: httpx.Response,
    ) -> str:
        if success_text:
            return f"Could not find the success text '{success_text}' in the response."
        if success_list:
            return f"Could not find any of the success strings in {success_list} in the response."
        if error_text:
            return f"Found the error text '{error_text}' in the response."
        if success_status_code:
            return (
                f"Expected status code '{success_status_code}', "
                f"got '{response.status_code}'."
            )
        return "Unknown upload error."

    async def handle_failed_upload(
        self,
        meta: Meta,
        tracker: str,
        success_status_code: str,
        success_text: str,
        error_text: str,
        response: httpx.Response,
        success_list: list[str] | None = None,
    ) -> bool:
        message = [
            "data error: The upload appears to have failed. It may have uploaded, go check.",
            self._failure_criterion_message(
                success_status_code,
                success_text,
                error_text,
                success_list,
                response,
            ),
        ]
        error_message = extract_upload_error(response.text)
        if error_message:
            message.append(f"Tracker error: {error_message}")
        failure_path = await self.common.save_html_file(
            meta, tracker, response.text, "Failed_Upload"
        )
        message.append(
            f"The web page has been saved to [yellow]{failure_path}[/yellow] for analysis.\n"
            "[red]Do not share this file publicly[/red], as it may contain confidential information such as passkeys, IP address, e-mail, etc.\n"
            "You can open this file in a web browser to see what went wrong.\n"
        )
        meta.tracker_status[tracker]["status_message"] = "\n".join(message)
        return False
