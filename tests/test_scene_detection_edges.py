from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx
import pytest
from bs4 import BeautifulSoup
from bs4.element import AttributeValueList

from src.domain_models.release import Meta
from src.integrations.media import scene_detection
from src.integrations.media.scene_detection import SceneManager


class Response:
    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        text: str = "",
        content: bytes = b"nfo",
    ) -> None:
        self.payload = payload
        self.status_code = status
        self.text = text
        self.content = content

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class Client:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[str]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, url: str, **_kwargs: object) -> Response:
        type(self).calls.append(url)
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, Response)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


@pytest.fixture(autouse=True)
def client(monkeypatch: pytest.MonkeyPatch) -> None:
    Client.reset()
    monkeypatch.setattr(scene_detection.httpx, "AsyncClient", Client)


def _meta(tmp_path: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "scene",
        "category": "MOVIE",
        "path": str(tmp_path / "Movie.Release-GROUP.mkv"),
        "filename": "Movie.Release-GROUP.mkv",
        "isdir": False,
        "is_disc": "",
        "keep_folder": False,
        "scene": False,
        "debug": False,
        "tag": "",
        "imdb_id": 1234567,
        "imdb_manual": None,
        "nfo": False,
        "auto_nfo": False,
        "we_need_tag": False,
    }
    state.update(values)
    return Meta(state)


def _scene_result(**values: object) -> dict[str, object]:
    result: dict[str, object] = {
        "release": "Movie.Release-GROUP",
        "hasNFO": "no",
        "imdbId": "1234567",
    }
    result.update(values)
    return {"resultsCount": 1, "results": [result]}


def test_constructor_and_attr_conversion() -> None:
    with pytest.raises(ValueError, match="DEFAULT"):
        SceneManager({"DEFAULT": []})  # type: ignore[dict-item]
    manager = SceneManager({"DEFAULT": {}})
    assert manager._attr_to_string("value") == "value"
    assert manager._attr_to_string(None) == ""
    assert manager._attr_to_string(123) == "123"
    values = AttributeValueList(["one", "two"])
    assert manager._attr_to_string(values) == "one two"


def test_scene_search_cached_match_lowercase_and_imdb(tmp_path: Path) -> None:
    meta = _meta(tmp_path, filename="movie.release-group.mkv")
    cache = tmp_path / "tmp" / "scene" / "srrdb" / "search"
    cache.mkdir(parents=True)
    (cache / "movie.release-group.json").write_text(
        json.dumps(_scene_result()), encoding="utf-8"
    )

    video, scene, imdb = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )

    assert scene and video == "Movie.Release-GROUP.mkv" and imdb == 1234567
    assert meta.scene_name == "Movie.Release-GROUP"
    assert meta.we_need_tag is True
    assert Client.calls == []


def test_scene_search_cache_invalid_then_remote_no_match(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path, debug=True)
    cache = tmp_path / "tmp" / "scene" / "srrdb" / "search"
    cache.mkdir(parents=True)
    (cache / "Movie.Release-GROUP.json").write_text(
        "bad-json", encoding="utf-8"
    )
    Client.reset(Response({"resultsCount": 0, "results": []}))

    video, scene, imdb = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )

    assert not scene and video == meta.filename and imdb is None
    assert len(Client.calls) == 1


def test_scene_search_request_error_and_predb_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = httpx.RequestError(
        "offline", request=httpx.Request("GET", "https://srrdb.com")
    )
    Client.reset(request)
    manager = SceneManager({"DEFAULT": {"check_predb": True}})
    monkeypatch.setattr(
        manager, "predb_check", lambda *_args: asyncio.sleep(0, result=True)
    )
    meta = _meta(tmp_path)
    _, scene, _ = asyncio.run(manager.is_scene(meta.filename, meta))
    assert scene


def test_scene_nfo_details_remote_and_download(tmp_path: Path) -> None:
    meta = _meta(tmp_path)
    details = {
        "files": [
            {"name": "README.txt"},
            {"name": "Folder/Release.nfo"},
        ]
    }
    Client.reset(
        Response(_scene_result(hasNFO="yes")),
        Response(details),
        Response(content=b"NFO DATA"),
    )

    _, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )

    assert scene and meta.nfo and meta.auto_nfo
    nfo = Path(meta.scene_nfo_file)
    assert nfo.read_bytes() == b"NFO DATA"
    assert nfo.name == "Release.nfo"


def test_scene_nfo_cached_details_and_existing_local_file(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    details_dir = tmp_path / "tmp" / "scene" / "srrdb" / "details"
    details_dir.mkdir(parents=True)
    (details_dir / "Movie.Release-GROUP.json").write_text(
        json.dumps({"files": [{"name": "cached.nfo"}]}),
        encoding="utf-8",
    )
    nfo = tmp_path / "tmp" / "scene" / "cached.nfo"
    nfo.write_bytes(b"existing")
    Client.reset(Response(_scene_result(hasNFO="yes")))

    asyncio.run(SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta))

    assert meta.nfo and meta.auto_nfo and Path(meta.scene_nfo_file) == nfo
    assert len(Client.calls) == 1


def test_scene_nfo_detail_errors_and_failed_download_are_nonfatal(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path)
    Client.reset(
        Response(_scene_result(hasNFO="yes", release="Bad / Release")),
        Response({"files": [{"bad": True}]}),
        Response(status=404),
    )
    _, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )
    assert scene and not meta.nfo

    meta = _meta(tmp_path, uuid="error")
    Client.reset(
        Response(_scene_result(hasNFO="yes")), RuntimeError("details failed")
    )
    _, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )
    assert scene and not meta.nfo


def test_scene_imdb_manual_prevents_remote_override(tmp_path: Path) -> None:
    meta = _meta(tmp_path, imdb_manual=7654321)
    Client.reset(Response(_scene_result(imdbId="1234567")))
    _, scene, imdb = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(
            meta.filename, meta, imdb=7654321
        )
    )
    assert scene and imdb is None


def test_game_folder_name_is_used_for_search(tmp_path: Path) -> None:
    folder = tmp_path / "Game.Release-GROUP"
    folder.mkdir()
    meta = _meta(
        tmp_path,
        category="GAME",
        path=str(folder),
        filename="setup.exe",
        isdir=True,
    )
    Client.reset(Response(_scene_result(release="Game.Release-GROUP")))
    video, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )
    assert scene and video == "Game.Release-GROUP.mkv"
    assert "Game.Release-GROUP" in Client.calls[0]


def test_lower_search_match_download_and_existing_nfo(tmp_path: Path) -> None:
    meta = _meta(
        tmp_path, filename="Movie Release.mkv", tag="-GROUP", imdb_id=1234567
    )
    result = _scene_result(hasNFO="yes", imdbId="1234567")
    Client.reset(Response(result), Response(content=b"NFO"))
    video, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta, lower=True)
    )
    assert scene and meta.scene and video == "Movie.Release-GROUP"
    assert meta.nfo and meta.auto_nfo

    meta = _meta(
        tmp_path,
        uuid="existing",
        filename="Movie Release.mkv",
        tag="-GROUP",
        imdb_id=1234567,
    )
    result = _scene_result(hasNFO="yes", imdbId="1234567")
    release_nfo = tmp_path / "tmp" / "existing" / "movie.release-group.nfo"
    release_nfo.parent.mkdir(parents=True)
    release_nfo.write_bytes(b"existing")
    Client.reset(Response(result))
    asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta, lower=True)
    )
    assert meta.nfo and meta.auto_nfo


def test_lower_search_missing_mismatch_no_results_and_errors(
    tmp_path: Path,
) -> None:
    manager = SceneManager({"DEFAULT": {}})
    missing = _meta(tmp_path, filename=None, tag=None)  # type: ignore[arg-type]
    _, scene, _ = asyncio.run(
        manager.is_scene("video.mkv", missing, lower=True)
    )
    assert not scene and Client.calls == []

    mismatch = _meta(tmp_path, tag="-GROUP", imdb_id=1234567)
    Client.reset(Response(_scene_result(imdbId="7654321")))
    _, scene, _ = asyncio.run(
        manager.is_scene(mismatch.filename, mismatch, lower=True)
    )
    assert not scene

    no_results = _meta(tmp_path, uuid="none", tag="-GROUP")
    Client.reset(Response({"resultsCount": 0, "results": []}))
    _, scene, _ = asyncio.run(
        manager.is_scene(no_results.filename, no_results, lower=True)
    )
    assert not scene

    error = _meta(tmp_path, uuid="err", tag="-GROUP")
    Client.reset(RuntimeError("search failed"))
    _, scene, _ = asyncio.run(
        manager.is_scene(error.filename, error, lower=True)
    )
    assert not scene


def test_predb_success_group_no_group_and_all_failures(tmp_path: Path) -> None:
    html = """
    <table class='zebra-striped'><tbody>
      <tr><td>1</td><td>2</td><td><a title='Movie.Release-GROUP'>release</a></td><td><a>GROUP</a></td></tr>
    </tbody></table>
    """
    meta = _meta(tmp_path)
    Client.reset(Response(status=200, text=html))
    assert asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(meta, "Movie.Release-GROUP")
    )
    assert meta.scene_name == "Movie.Release-GROUP" and meta.tag == "-GROUP"

    no_match_html = "<table class='zebra-striped'><tbody><tr><td>1</td><td>2</td><td><a title='Other'>x</a></td></tr></tbody></table>"
    Client.reset(Response(status=200, text=no_match_html))
    assert not asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(
            _meta(tmp_path), "Movie.Release-GROUP"
        )
    )

    Client.reset(Response(status=500, text=""))
    assert not asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(
            _meta(tmp_path), "Movie.Release-GROUP"
        )
    )

    Client.reset(
        httpx.RequestError(
            "offline", request=httpx.Request("GET", "https://predb.pw")
        )
    )
    assert not asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(
            _meta(tmp_path), "Movie.Release-GROUP"
        )
    )

    Client.reset(RuntimeError("broken"))
    assert not asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(
            _meta(tmp_path), "Movie.Release-GROUP"
        )
    )


def test_predb_empty_title_and_attribute_list(tmp_path: Path) -> None:
    html = """
    <table class='zebra-striped'><tbody>
      <tr><td>1</td><td>2</td><td><a title=''>empty</a></td></tr>
      <tr><td>1</td><td>2</td><td><a title='Movie.Release-GROUP'>release</a></td><td><a><span>GROUP</span></a></td></tr>
    </tbody></table>
    """
    Client.reset(Response(status=200, text=html))
    meta = _meta(tmp_path)
    assert asyncio.run(
        SceneManager({"DEFAULT": {}}).predb_check(meta, "Movie.Release-GROUP")
    )
    assert meta.tag == "-GROUP"


def test_invalid_details_cache_and_lower_nfo_exception(tmp_path: Path) -> None:
    meta = _meta(tmp_path, uuid="invalid-details")
    details_dir = tmp_path / "tmp" / "invalid-details" / "srrdb" / "details"
    details_dir.mkdir(parents=True)
    (details_dir / "Movie.Release-GROUP.json").write_text(
        "bad-json", encoding="utf-8"
    )
    Client.reset(
        Response(_scene_result(hasNFO="yes")),
        Response({"files": []}),
        Response(status=404),
    )
    _, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(meta.filename, meta)
    )
    assert scene and not meta.nfo

    lower = _meta(tmp_path, uuid="lower-error", tag="-GROUP", imdb_id=1234567)
    Client.reset(
        Response(_scene_result(hasNFO="yes", imdbId="1234567")),
        RuntimeError("nfo failed"),
    )
    _, scene, _ = asyncio.run(
        SceneManager({"DEFAULT": {}}).is_scene(
            lower.filename, lower, lower=True
        )
    )
    assert scene and not lower.nfo


def test_predb_row_and_group_helpers_cover_invalid_shapes() -> None:
    manager = SceneManager({"DEFAULT": {}})
    short_soup = BeautifulSoup("<table><tr><td>one</td></tr></table>", "lxml")
    short_row = short_soup.find("tr")
    assert short_row is not None
    assert manager._predb_row_release(short_row) is None

    no_link_soup = BeautifulSoup(
        "<table><tr><td>1</td><td>2</td><td>release</td></tr></table>", "lxml"
    )
    no_link_row = no_link_soup.find("tr")
    assert no_link_row is not None
    assert manager._predb_row_release(no_link_row) is None
    assert manager._predb_group_tag("") == ""
