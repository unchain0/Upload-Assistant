from __future__ import annotations

import asyncio
import http.cookiejar
import json
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from bs4.element import AttributeValueList

from src.domain_models.release import Meta
from src.integrations.trackers import cookie_auth
from src.integrations.trackers.cookie_auth import CookieAuthUploader, CookieValidator


def _config(tmp_path: Path, **tracker_values: object) -> dict[str, Any]:
    return {
        "DEFAULT": {},
        "TRACKERS": {
            "TEST": {"announce_url": "https://www.tracker.example/announce", **tracker_values},
            "ALPHARATIO": {
                "username": "user",
                "password": "".join(("pass", "word")),
                "announce_url": "https://alpharatio.cc/announce",
            },
        },
        "base_dir": str(tmp_path),
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "cookie",
        "path": str(media),
        "filename": media.name,
        "filelist": [str(media)],
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "infohash": "HASH",
        "debug": False,
        "tracker_status": {"TEST": {}, "ALPHARATIO": {}},
    }
    state.update(values)
    (tmp_path / "tmp" / str(state["uuid"])).mkdir(parents=True, exist_ok=True)
    return Meta(state)


class Response:
    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "",
        url: str = "https://tracker.example/page",
    ) -> None:
        self.status_code = status
        self.text = text
        self.url = httpx.URL(url)
        self.request = httpx.Request("GET", url)


class CookieContainer:
    def __init__(self, cookies: list[http.cookiejar.Cookie] | None = None) -> None:
        self.jar = list(cookies or [])

    def __iter__(self):
        return iter(cookie.name for cookie in self.jar)


class Client:
    queue: ClassVar[list[object]] = []
    instances: ClassVar[list[Client]] = []
    cookies_for_new: ClassVar[list[http.cookiejar.Cookie]] = []

    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.cookies = CookieContainer(type(self).cookies_for_new)
        type(self).instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def _next(self) -> Response:
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    async def get(self, *_args: object, **_kwargs: object) -> Response:
        return self._next()

    async def post(self, *_args: object, **_kwargs: object) -> Response:
        return self._next()

    @classmethod
    def reset(cls, *values: object, cookies: list[http.cookiejar.Cookie] | None = None) -> None:
        cls.queue = list(values)
        cls.instances = []
        cls.cookies_for_new = list(cookies or [])


def _cookie(name: str = "session", value: str = "value") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=".example.com",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


@pytest.fixture(autouse=True)
def reset_http(monkeypatch: pytest.MonkeyPatch) -> None:
    Client.reset()
    monkeypatch.setattr(cookie_auth.httpx, "AsyncClient", Client)


def test_attribute_and_upload_error_parsers() -> None:
    assert cookie_auth._attr_to_string("value") == "value"
    assert cookie_auth._attr_to_string(AttributeValueList(["one", "two"])) == "one two"
    assert cookie_auth._attr_to_string(None) == ""

    cases = [
        ('<div class="notification-border-e"><div class="notification-body"> Error: Modern failure </div></div>', "Error: Modern failure"),
        ('<div class="error">Specific old error</div>', "Specific old error"),
        ('<div><span class="error">Error</span> Detailed parent failure </div>', "Detailed parent failure"),
        ("<h2>Upload failed</h2><p>Sibling failure</p>", "Sibling failure"),
        ('<h1 class="dnu_header">Upload not allowed here</h1>', "Upload not allowed here"),
        ("<div>Error: Legacy failure <b>Back</b></div>", "Legacy failure"),
    ]
    for html, expected in cases:
        assert expected in cookie_auth.extract_upload_error(html)
    assert cookie_auth.extract_upload_error("<html>ok</html>") == ""


def test_tracker_domain_registry_config_fallback_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    assert cookie_auth.get_tracker_domain("FILELIST") == "filelist.io"
    config = {"TRACKERS": {"CUSTOM": {"announce_url": "https://www.Custom.Example/announce"}}}
    assert cookie_auth.get_tracker_domain("CUSTOM", config) == "custom.example"
    assert cookie_auth.get_tracker_domain("PASSTHEPOPCORN") == "passthepopcorn.me"
    assert cookie_auth.get_tracker_domain("UNKNOWN") == "unknown"

    import src.integrations.trackers.registry as registry

    class BrokenBase:
        @property
        def base_url(self) -> str:
            raise RuntimeError("broken")

    monkeypatch.setitem(registry.tracker_class_map, "BROKEN", BrokenBase())  # type: ignore[arg-type]
    assert cookie_auth.get_tracker_domain("BROKEN", {"TRACKERS": {"BROKEN": {}}}) == "broken"


def test_find_cookie_file_custom_filename_domain_multiple_and_fallback(tmp_path: Path) -> None:
    base = str(tmp_path)
    cookies = tmp_path / "data" / "cookies"
    config = _config(tmp_path)

    assert cookie_auth.find_cookie_file(base, "TEST", {"TRACKERS": {"TEST": {"cookie_file": str(tmp_path / "absolute.txt")}}}) == str((tmp_path / "absolute.txt").resolve())
    assert cookie_auth.find_cookie_file(base, "TEST", {"TRACKERS": {"TEST": {"cookie_file": "data/cookies/custom.txt"}}}) == str(
        (tmp_path / "data" / "cookies" / "custom.txt").resolve()
    )
    assert cookie_auth.find_cookie_file(base, "TEST", {"TRACKERS": {"TEST": {"cookie_file": "custom.txt"}}}) == str((cookies / "custom.txt").resolve())

    exact = cookies / "TEST.json"
    exact.write_text("{}", encoding="utf-8")
    assert cookie_auth.find_cookie_file(base, "TEST", config) == str(exact.resolve())
    exact.unlink()

    partial = cookies / "my_test_cookies.txt"
    partial.write_text("content", encoding="utf-8")
    assert cookie_auth.find_cookie_file(base, "TEST", config) == str(partial.resolve())
    partial.unlink()

    domain = cookies / "session.json"
    domain.write_text("tracker.example cookie", encoding="utf-8")
    assert cookie_auth.find_cookie_file(base, "TEST", config) == str(domain.resolve())
    domain.unlink()

    one = cookies / "test-one.txt"
    two = cookies / "test-two.txt"
    one.write_text("x", encoding="utf-8")
    two.write_text("y", encoding="utf-8")
    assert cookie_auth.find_cookie_file(base, "TEST", config) == str(one.resolve())
    one.unlink()
    two.unlink()

    assert cookie_auth.find_cookie_file(base, "FILELIST", config).endswith("FILELIST.json")
    assert cookie_auth.find_cookie_file(base, "PASSTHEPOPCORN", config).endswith("PASSTHEPOPCORN.json")
    assert cookie_auth.find_cookie_file(base, "Pterimg", config).endswith("Pterimg.json")
    assert cookie_auth.find_cookie_file(base, "OTHER", config).endswith("OTHER.txt")


def test_find_cookie_file_content_read_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = tmp_path / "data" / "cookies"
    cookies.mkdir(parents=True)
    candidate = cookies / "unrelated.txt"
    candidate.write_text("x", encoding="utf-8")
    original_open = Path.open

    def fail(path: Path, *args: object, **kwargs: object):
        if path == candidate:
            raise OSError("read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    assert cookie_auth.find_cookie_file(str(tmp_path), "TEST", _config(tmp_path)).endswith("TEST.txt")


class JarDouble:
    next_load_error: ClassVar[BaseException | None] = None
    saved = 0

    def __init__(self, filename: str = "") -> None:
        self.filename = filename
        self.load_calls = 0

    def load(self, **_kwargs: object) -> None:
        self.load_calls += 1
        error = type(self).next_load_error
        type(self).next_load_error = None
        if error:
            raise error

    def save(self, **_kwargs: object) -> None:
        type(self).saved += 1


def test_load_save_session_cookies_and_ar_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = CookieValidator(_config(tmp_path))
    monkeypatch.setattr(cookie_auth.http.cookiejar, "MozillaCookieJar", JarDouble)
    monkeypatch.setattr(cookie_auth, "find_cookie_file", lambda *_args, **_kwargs: str(tmp_path / "cookies.txt"))

    JarDouble.next_load_error = http.cookiejar.LoadError("bad")
    assert asyncio.run(validator.load_session_cookies(_meta(tmp_path), "TEST")) is None

    JarDouble.next_load_error = FileNotFoundError()
    assert asyncio.run(validator.load_session_cookies(_meta(tmp_path), "TEST")) is None

    validator.ar_login = AsyncMock(return_value=False)  # type: ignore[method-assign]
    JarDouble.next_load_error = FileNotFoundError()
    assert asyncio.run(validator.load_session_cookies(_meta(tmp_path), "ALPHARATIO")) is None

    validator.ar_login = AsyncMock(return_value=True)  # type: ignore[method-assign]
    JarDouble.next_load_error = FileNotFoundError()
    jar = asyncio.run(validator.load_session_cookies(_meta(tmp_path), "ALPHARATIO"))
    assert isinstance(jar, JarDouble)

    asyncio.run(validator.save_session_cookies("TEST", None))
    JarDouble.saved = 0
    asyncio.run(validator.save_session_cookies("TEST", JarDouble()))
    assert JarDouble.saved == 1

    class BadSave(JarDouble):
        def save(self, **_kwargs: object) -> None:
            raise OSError("save")

    asyncio.run(validator.save_session_cookies("TEST", BadSave()))


def test_auth_key_read_success_empty_and_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = CookieValidator(_config(tmp_path))
    cookie = tmp_path / "ar.txt"
    monkeypatch.setattr(cookie_auth, "find_cookie_file", lambda *_args, **_kwargs: str(cookie))
    auth = tmp_path / "ar_auth.txt"
    auth.write_text(" key \n", encoding="utf-8")
    assert asyncio.run(validator.get_ar_auth_key(_meta(tmp_path), "ALPHARATIO")) == "key"
    auth.write_text("   ", encoding="utf-8")
    assert asyncio.run(validator.get_ar_auth_key(_meta(tmp_path), "ALPHARATIO")) is None
    auth.unlink()
    assert asyncio.run(validator.get_ar_auth_key(_meta(tmp_path), "ALPHARATIO")) is None


def test_ar_login_missing_credentials_status_recovery_validation_and_exceptions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config["TRACKERS"]["ALPHARATIO"]["username"] = ""
    validator = CookieValidator(config)
    assert not asyncio.run(validator.ar_login(_meta(tmp_path), "ALPHARATIO", str(tmp_path / "ar.txt")))

    config = _config(tmp_path)
    validator = CookieValidator(config)
    Client.reset(Response(status=500))
    assert not asyncio.run(validator.ar_login(_meta(tmp_path), "ALPHARATIO", str(tmp_path / "ar.txt")))

    validator.common.save_html_file = AsyncMock(return_value="failure.html")  # type: ignore[method-assign]
    Client.reset(Response(text="Forgot your password login.php?act=recover"))
    assert not asyncio.run(validator.ar_login(_meta(tmp_path, debug=True), "ALPHARATIO", str(tmp_path / "ar.txt")))
    validator.common.save_html_file.assert_awaited_once()

    Client.reset(Response(text="ok"), Response(status=500, text="login.php?act=recover"))
    assert not asyncio.run(validator.ar_login(_meta(tmp_path), "ALPHARATIO", str(tmp_path / "ar.txt")))

    for error in (
        httpx.ConnectTimeout("timeout", request=httpx.Request("GET", "https://alpharatio.cc")),
        httpx.ConnectError("connect", request=httpx.Request("GET", "https://alpharatio.cc")),
        RuntimeError("other"),
    ):
        Client.reset(error)
        assert not asyncio.run(validator.ar_login(_meta(tmp_path), "ALPHARATIO", str(tmp_path / "ar.txt")))


def test_ar_login_success_cookie_and_auth_key(tmp_path: Path) -> None:
    validator = CookieValidator(_config(tmp_path))
    cookie = _cookie("session", "value")
    Client.reset(
        Response(text="login ok"),
        Response(text='<a href="logout.php?auth=SECRET&x=1">Logout</a>'),
        cookies=[cookie],
    )
    path = tmp_path / "cookies" / "ALPHARATIO.txt"
    assert asyncio.run(validator.ar_login(_meta(tmp_path), "ALPHARATIO", str(path)))
    assert path.is_file()
    assert (path.parent / "ALPHARATIO_auth.txt").read_text(encoding="utf-8") == "SECRET"


def test_cookie_validation_success_failure_indicators_token_and_exceptions(tmp_path: Path) -> None:
    validator = CookieValidator(_config(tmp_path))
    jar = JarDouble()
    validator.load_session_cookies = AsyncMock(return_value=jar)  # type: ignore[method-assign]
    validator.save_session_cookies = AsyncMock()  # type: ignore[method-assign]
    validator.handle_validation_failure = AsyncMock()  # type: ignore[method-assign]

    Client.reset(Response(text="welcome TOKEN=abc", status=200))
    assert asyncio.run(validator.cookie_validation(_meta(tmp_path), "TEST", "https://test", success_text="welcome", token_pattern="".join((r"TOKEN=", r"(\w+)"))))
    validator.save_session_cookies.assert_awaited_once()

    for kwargs, text, status in (
        ({"success_text": "welcome"}, "login", 200),
        ({"error_text": "login"}, "login", 200),
        ({"status_code": "201"}, "ok", 200),
        ({"token_pattern": r"TOKEN=(\w+)"}, "no token", 200),
    ):
        validator.handle_validation_failure.reset_mock()
        Client.reset(Response(text=text, status=status))
        assert not asyncio.run(validator.cookie_validation(_meta(tmp_path), "TEST", "https://test", **kwargs))
        validator.handle_validation_failure.assert_awaited_once()

    validator.load_session_cookies = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert not asyncio.run(validator.cookie_validation(_meta(tmp_path), "TEST", "https://test"))

    validator.load_session_cookies = AsyncMock(return_value=jar)  # type: ignore[method-assign]
    errors: list[BaseException] = [
        httpx.ConnectTimeout("x", request=httpx.Request("GET", "https://x")),
        httpx.ReadTimeout("x", request=httpx.Request("GET", "https://x")),
        httpx.ConnectError("x", request=httpx.Request("GET", "https://x")),
        httpx.ProxyError("x", request=httpx.Request("GET", "https://x")),
        httpx.DecodingError("x", request=httpx.Request("GET", "https://x")),
        httpx.TooManyRedirects("x", request=httpx.Request("GET", "https://x")),
        httpx.RequestError("x", request=httpx.Request("GET", "https://x")),
        RuntimeError("x"),
    ]
    for error in errors:
        Client.reset(error)
        assert not asyncio.run(validator.cookie_validation(_meta(tmp_path), "TEST", "https://test"))

    request = httpx.Request("GET", "https://x")
    response = httpx.Response(500, request=request)
    Client.reset(httpx.HTTPStatusError("bad", request=request, response=response))
    assert not asyncio.run(validator.cookie_validation(_meta(tmp_path), "TEST", "https://test"))


def test_validation_failure_token_and_secure_cookie_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = CookieValidator(_config(tmp_path))
    validator.common.save_html_file = AsyncMock(return_value="failure.html")  # type: ignore[method-assign]
    asyncio.run(validator.handle_validation_failure(_meta(tmp_path), "TEST", "html"))
    validator.common.save_html_file.assert_awaited_once()
    assert asyncio.run(validator.find_html_token("TEST", r"TOKEN=(\w+)", "TOKEN=abc")) == "abc"
    assert asyncio.run(validator.find_html_token("TEST", r"TOKEN=(\w+)", "none")) is None

    path = tmp_path / "secure.json"
    validator._save_cookies_secure([_cookie()], str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["session"]["value"] == "value"
    assert validator._load_cookies_dict_secure(str(path))["session"]["value"] == "value"

    class SessionCookies:
        def __init__(self) -> None:
            self.values: list[dict[str, object]] = []

        def set(self, **kwargs: object) -> None:
            self.values.append(dict(kwargs))

    session = SimpleNamespace(cookies=SessionCookies())
    validator._load_cookies_secure(session, str(path), "TEST")
    assert session.cookies.values[0]["domain"] == ".example.com"

    path.write_text(json.dumps({"session": {"value": "x", "domain": None}}), encoding="utf-8")
    session = SimpleNamespace(cookies=SessionCookies())
    validator._load_cookies_secure(session, str(path), "TEST")
    assert session.cookies.values[0]["domain"] == ""

    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        validator._load_cookies_dict_secure(str(path))
    with pytest.raises(json.JSONDecodeError):
        validator._load_cookies_secure(SimpleNamespace(cookies=SessionCookies()), str(path), "TEST")
    with pytest.raises(OSError):
        validator._load_cookies_dict_secure(str(tmp_path / "missing.json"))

    original_open = Path.open

    def fail_write(target: Path, *args: object, **kwargs: object):
        if target == path and args and "w" in str(args[0]):
            raise OSError("write")
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(OSError):
        validator._save_cookies_secure([_cookie()], str(path))


def test_secure_cookie_encoding_error(tmp_path: Path) -> None:
    validator = CookieValidator(_config(tmp_path))

    class BadCookie:
        name = "bad"
        value = object()
        domain = ""
        path = "/"
        secure = False
        expires = None

    with pytest.raises(TypeError):
        validator._save_cookies_secure([BadCookie()], str(tmp_path / "bad.json"))


class UploaderCommon:
    def __init__(self) -> None:
        self.create_torrent_for_upload = AsyncMock()
        self.create_torrent_ready_to_seed = AsyncMock(return_value="READYHASH")
        self.save_html_file = AsyncMock(return_value="failed.html")


def _uploader(tmp_path: Path) -> tuple[CookieAuthUploader, UploaderCommon, Meta]:
    uploader = CookieAuthUploader(_config(tmp_path))
    common_double = UploaderCommon()
    uploader.common = common_double  # type: ignore[assignment]
    meta = _meta(tmp_path)
    torrent = tmp_path / "tmp" / meta.uuid / "[TEST].torrent"
    torrent.write_bytes(b"torrent-bytes")
    return uploader, common_double, meta


def test_uploader_rejects_missing_or_ambiguous_success_criteria(tmp_path: Path) -> None:
    uploader, _common, meta = _uploader(tmp_path)
    assert not asyncio.run(
        uploader.handle_upload(
            meta,
            "TEST",
            "SRC",
            "https://tracker/torrent/",
            {},
            "file",
            {},
            "https://tracker/upload",
        )
    )
    assert "at least one" in meta.tracker_status["TEST"]["status_message"]

    assert not asyncio.run(
        uploader.handle_upload(
            meta,
            "TEST",
            "SRC",
            "https://tracker/torrent/",
            {},
            "file",
            {},
            "https://tracker/upload",
            success_text="ok",
            error_text="bad",
        )
    )
    assert "Only one" in meta.tracker_status["TEST"]["status_message"]


def test_uploader_debug_load_file_and_additional_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploader, common_double, meta = _uploader(tmp_path)
    meta.debug = True
    debug = Mock()
    monkeypatch.setattr(uploader, "upload_debug", debug)
    assert asyncio.run(
        uploader.handle_upload(
            meta,
            "TEST",
            "SRC",
            "https://tracker/torrent/",
            {"token": "secret"},
            "torrent",
            {},
            "https://tracker/upload",
            success_text="ok",
            additional_files={"nfo": ("release.nfo", b"nfo", "text/plain")},
        )
    )
    debug.assert_called_once()
    common_double.create_torrent_for_upload.assert_awaited()
    assert meta.tracker_status["TEST"]["status_message"] == "Debug mode enabled, not uploading"

    meta.debug = False
    files = asyncio.run(uploader.load_torrent_file(meta, "TEST", "torrent", "Explicit.Name", "SRC", "https://announce"))
    assert files["torrent"] == ("Explicit.Name.torrent", b"torrent-bytes", "application/x-bittorrent")
    files = asyncio.run(uploader.load_torrent_file(meta, "TEST", "torrent", "", "SRC", "https://announce"))
    assert files["torrent"][0] == "TEST.HASH.placeholder.torrent"


def test_uploader_success_text_list_status_and_error_absence(tmp_path: Path) -> None:
    variants = [
        ({"success_text": "uploaded"}, Response(text="uploaded successfully", url="https://tracker.example/details?id=1")),
        ({"success_list": ["done", "created"]}, Response(text="created torrent", url="https://tracker.example/details?id=2")),
        ({"success_status_code": "200, 201"}, Response(status=201, text="ok", url="https://tracker.example/details?id=3")),
        ({"error_text": "fatal"}, Response(text="everything fine", url="https://tracker.example/details?id=4")),
    ]
    for index, (criteria, response) in enumerate(variants):
        uploader, common_double, meta = _uploader(tmp_path)
        meta.uuid = f"success-{index}"
        target = tmp_path / "tmp" / meta.uuid
        target.mkdir(parents=True)
        (target / "[TEST].torrent").write_bytes(b"torrent")
        Client.reset(response)
        uploader.handle_successful_upload = AsyncMock(return_value=True)  # type: ignore[method-assign]
        assert asyncio.run(
            uploader.handle_upload(
                meta,
                "TEST",
                "SRC",
                "https://tracker/torrent/",
                {},
                "torrent",
                {},
                "https://tracker/upload",
                additional_files={"nfo": ("x.nfo", b"nfo", "text/plain")},
                **criteria,
            )
        )
        uploader.handle_successful_upload.assert_awaited_once()
        common_double.create_torrent_ready_to_seed.assert_not_awaited()


def test_uploader_failed_criteria_calls_failure_handler(tmp_path: Path) -> None:
    variants = [
        ({"success_text": "uploaded"}, Response(text="nope", status=200)),
        ({"success_list": ["done"]}, Response(text="nope", status=200)),
        ({"success_status_code": "201"}, Response(text="nope", status=200)),
        ({"error_text": "fatal"}, Response(text="fatal happened", status=200)),
    ]
    for index, (criteria, response) in enumerate(variants):
        uploader, _common, meta = _uploader(tmp_path)
        meta.uuid = f"failure-{index}"
        target = tmp_path / "tmp" / meta.uuid
        target.mkdir(parents=True)
        (target / "[TEST].torrent").write_bytes(b"torrent")
        Client.reset(response)
        uploader.handle_failed_upload = AsyncMock(return_value=False)  # type: ignore[method-assign]
        assert not asyncio.run(
            uploader.handle_upload(
                meta,
                "TEST",
                "SRC",
                "https://tracker/torrent/",
                {},
                "torrent",
                {},
                "https://tracker/upload",
                **criteria,
            )
        )
        uploader.handle_failed_upload.assert_awaited_once()


def test_uploader_http_exception_matrix_sets_status_and_builds_seed_torrent(tmp_path: Path) -> None:
    request = httpx.Request("POST", "https://tracker.example/upload")
    response = httpx.Response(500, request=request)
    errors: list[tuple[BaseException, str]] = [
        (httpx.ConnectTimeout("timeout", request=request), "Connection timed out"),
        (httpx.ReadTimeout("timeout", request=request), "Read timed out"),
        (httpx.ConnectError("connect", request=request), "Failed to connect"),
        (httpx.ProxyError("proxy", request=request), "Proxy connection failed"),
        (httpx.DecodingError("decode", request=request), "Response decoding failed"),
        (httpx.TooManyRedirects("redirect", request=request), "Too many redirects"),
        (httpx.HTTPStatusError("status", request=request, response=response), "HTTP error 500"),
        (httpx.RequestError("request", request=request), "Request error"),
        (RuntimeError("unexpected"), "Unexpected upload error"),
    ]
    for index, (error, expected) in enumerate(errors):
        uploader, common_double, meta = _uploader(tmp_path)
        meta.uuid = f"exception-{index}"
        target = tmp_path / "tmp" / meta.uuid
        target.mkdir(parents=True)
        (target / "[TEST].torrent").write_bytes(b"torrent")
        Client.reset(error)
        assert not asyncio.run(
            uploader.handle_upload(
                meta,
                "TEST",
                "SRC",
                "https://tracker/torrent/",
                {},
                "torrent",
                {},
                "https://tracker/upload",
                success_text="ok",
            )
        )
        assert expected in meta.tracker_status["TEST"]["status_message"]
        common_double.create_torrent_ready_to_seed.assert_awaited_once()


def test_upload_debug_redaction_non_dict_and_error(tmp_path: Path) -> None:
    uploader, _common, _meta_value = _uploader(tmp_path)
    uploader.upload_debug(
        "TEST",
        {
            "password": "secret",
            "passkey_value": "secret",
            "auth": "secret",
            "csrf": "secret",
            "token": "secret",
            "title": "safe",
        },
    )
    uploader.upload_debug("TEST", "raw-form")

    class BrokenDict(dict[str, object]):
        def items(self):
            raise RuntimeError("items failed")

    with pytest.raises(RuntimeError, match="items failed"):
        uploader.upload_debug("TEST", BrokenDict())


def test_handle_successful_upload_url_text_hash_and_no_id(tmp_path: Path) -> None:
    uploader, common_double, meta = _uploader(tmp_path)
    response = Response(text="body id=55", url="https://tracker.example/details?id=44")
    assert asyncio.run(
        uploader.handle_successful_upload(
            meta,
            "TEST",
            response,  # type: ignore[arg-type]
            r"id=(\d+)",
            False,
            "SRC",
            "https://announce",
            "https://tracker/torrent/",
        )
    )
    assert meta.tracker_status["TEST"]["torrent_id"] == "44"
    assert meta.tracker_status["TEST"]["status_message"] == "Torrent uploaded successfully."
    common_double.create_torrent_ready_to_seed.assert_awaited_once()

    meta = _meta(tmp_path, uuid="text-id")
    uploader.common = common_double  # type: ignore[assignment]
    response = Response(text="torrent=66", url="https://tracker.example/no-id")
    asyncio.run(
        uploader.handle_successful_upload(
            meta,
            "TEST",
            response,  # type: ignore[arg-type]
            r"torrent=(\d+)",
            False,
            "SRC",
            "https://announce",
            "https://tracker/torrent/",
        )
    )
    assert meta.tracker_status["TEST"]["torrent_id"] == "66"

    meta = _meta(tmp_path, uuid="hash-id")
    common_double.create_torrent_ready_to_seed = AsyncMock(return_value="HASHID")
    uploader.common = common_double  # type: ignore[assignment]
    asyncio.run(
        uploader.handle_successful_upload(
            meta,
            "TEST",
            Response(text="", url="https://tracker.example/no-id"),  # type: ignore[arg-type]
            "",
            True,
            "SRC",
            "https://announce",
            "https://tracker/torrent/",
        )
    )
    assert meta.tracker_status["TEST"]["torrent_id"] == "HASHID"


def test_handle_failed_upload_all_message_modes_and_tracker_error(tmp_path: Path) -> None:
    variants = [
        ({"success_text": "success", "success_list": None, "error_text": "", "success_status_code": ""}, "Could not find the success text"),
        ({"success_text": "", "success_list": ["one", "two"], "error_text": "", "success_status_code": ""}, "Could not find any"),
        ({"success_text": "", "success_list": None, "error_text": "bad", "success_status_code": ""}, "Found the error text"),
        ({"success_text": "", "success_list": None, "error_text": "", "success_status_code": "201"}, "Expected status code"),
        ({"success_text": "", "success_list": None, "error_text": "", "success_status_code": ""}, "Unknown upload error"),
    ]
    for index, (kwargs, expected) in enumerate(variants):
        uploader, common_double, meta = _uploader(tmp_path)
        meta.uuid = f"failed-message-{index}"
        response = Response(
            status=400,
            text='<div class="notification-border-e"><div class="notification-body">Tracker rejected release</div></div>',
        )
        assert not asyncio.run(
            uploader.handle_failed_upload(
                meta,
                "TEST",
                kwargs["success_status_code"],
                kwargs["success_text"],
                kwargs["error_text"],
                response,  # type: ignore[arg-type]
                kwargs["success_list"],
            )
        )
        message = meta.tracker_status["TEST"]["status_message"]
        assert expected in message and "Tracker rejected release" in message and "failed.html" in message
        common_double.save_html_file.assert_awaited_once()


def test_final_cookie_helper_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Legacy error extraction skips unrelated text nodes and non-prefixed parents.
    assert cookie_auth.extract_upload_error("<div>plain text</div>") == ""
    assert cookie_auth.extract_upload_error("<div>prefix <span>Upload failed!</span></div>") == ""

    import src.integrations.trackers.registry as registry

    class WwwTracker:
        base_url = "https://www.Example.COM/path"

    monkeypatch.setitem(registry.tracker_class_map, "WWWTEST", WwwTracker)
    assert cookie_auth.get_tracker_domain("WWWTEST") == "example.com"

    # Malformed IPv6 makes urllib.parse reject the configured announce URL and
    # falls through to the tracker-name fallback.
    assert (
        cookie_auth.get_tracker_domain(
            "CUSTOMBROKEN",
            {"TRACKERS": {"CUSTOMBROKEN": {"announce_url": "https://[::1"}}},
        )
        == "custombroken"
    )

    monkeypatch.delitem(registry.tracker_class_map, "PASSTHEPOPCORN", raising=False)
    assert cookie_auth.get_tracker_domain("PASSTHEPOPCORN") == "passthepopcorn.me"

    cookies_dir = tmp_path / "data" / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    (cookies_dir / "subdir").mkdir()
    assert cookie_auth.find_cookie_file(str(tmp_path), "NOCOOKIE", _config(tmp_path)).endswith("NOCOOKIE.txt")


def test_final_cookie_validator_read_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = CookieValidator(_config(tmp_path))
    monkeypatch.setattr(cookie_auth, "find_cookie_file", lambda *_args, **_kwargs: str(tmp_path / "cookies.txt"))

    # Normal successful first load returns the jar.
    monkeypatch.setattr(cookie_auth.http.cookiejar, "MozillaCookieJar", JarDouble)
    JarDouble.next_load_error = None
    assert isinstance(asyncio.run(validator.load_session_cookies(_meta(tmp_path), "TEST")), JarDouble)

    # Successful ALPHARATIO login followed by an unreadable freshly-created jar.
    class TwoStageJar(JarDouble):
        loads: ClassVar[int] = 0

        def load(self, **_kwargs: object) -> None:
            type(self).loads += 1
            if type(self).loads == 1:
                raise FileNotFoundError
            raise OSError("new jar unreadable")

    TwoStageJar.loads = 0
    monkeypatch.setattr(cookie_auth.http.cookiejar, "MozillaCookieJar", TwoStageJar)
    validator.ar_login = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(validator.load_session_cookies(_meta(tmp_path), "ALPHARATIO")) is None

    # Auth-key file can exist but fail during asynchronous open/read.
    cookie_file = tmp_path / "ar.txt"
    auth_file = tmp_path / "ar_auth.txt"
    auth_file.write_text("key", encoding="utf-8")
    monkeypatch.setattr(cookie_auth, "find_cookie_file", lambda *_args, **_kwargs: str(cookie_file))
    original_aio_open = cookie_auth.aiofiles.open

    def fail_auth_open(path: object, *args: object, **kwargs: object):
        if Path(str(path)) == auth_file:
            raise OSError("auth read failed")
        return original_aio_open(path, *args, **kwargs)

    monkeypatch.setattr(cookie_auth.aiofiles, "open", fail_auth_open)
    assert asyncio.run(validator.get_ar_auth_key(_meta(tmp_path), "ALPHARATIO")) is None
    monkeypatch.setattr(cookie_auth.aiofiles, "open", original_aio_open)

    # Token assignment path uses an actual registered tracker class.
    monkeypatch.setattr(cookie_auth.httpx, "AsyncClient", Client)
    validator.load_session_cookies = AsyncMock(return_value=JarDouble())  # type: ignore[method-assign]
    validator.save_session_cookies = AsyncMock()  # type: ignore[method-assign]
    Client.reset(Response(text="TOKEN=abc"))
    import src.integrations.trackers.registry as registry

    assert asyncio.run(
        validator.cookie_validation(
            _meta(tmp_path),
            "FILELIST",
            "https://filelist.io",
            token_pattern="".join((r"TOKEN=", r"(\w+)")),
        )
    )
    assert registry.tracker_class_map["FILELIST"].secret_token == "abc"

    # Secure session-cookie loader surfaces filesystem errors deterministically.
    with pytest.raises(OSError):
        validator._load_cookies_secure(SimpleNamespace(cookies=SimpleNamespace(set=lambda **_kwargs: None)), str(tmp_path / "missing.json"), "TEST")
