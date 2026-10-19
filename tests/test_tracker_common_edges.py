from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import bencodepy
import httpx
import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.integrations.trackers import common
from src.integrations.trackers.common import Common


def _config(**defaults: object) -> dict[str, Any]:
    return {
        "DEFAULT": dict(defaults),
        "TRACKERS": {
            "TEST": {
                "announce_url": " https://tracker.example/announce ",
                "allow_ext_subtitles": False,
            }
        },
    }


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Example.mkv"
    media.write_bytes(b"video")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "common",
        "path": str(media),
        "filename": media.name,
        "filelist": [str(media)],
        "subtitle_files": [],
        "category": "MOVIE",
        "unattended": True,
        "unattended_confirm": False,
        "imdb_id": 1234567,
        "tmdb_id": 123,
        "tvdb_id": 456,
        "tvmaze_id": 789,
        "mal_id": 10,
        "douban_id": "douban",
        "igdb": 11,
        "asin": "B000000000",
        "isbn": "9780000000000",
        "ua_name": "Upload Assistant",
        "current_version": "1.0",
        "entropy": None,
        "debug": False,
        "tracker_status": {},
        "audio_languages": ["English"],
        "subtitle_languages": ["English"],
        "manual_language": "",
        "original_language": "English",
        "is_disc": "",
    }
    state.update(values)
    target = tmp_path / "tmp" / str(state["uuid"])
    target.mkdir(parents=True, exist_ok=True)
    return Meta(state)


class FakeTorrentValue:
    def __init__(self, metainfo: dict[str, Any] | None = None) -> None:
        self.metainfo = metainfo or {
            "announce": "old",
            "comment": "private",
            "created by": "mkbrr 1.0",
            "junk": "remove",
            "info": {"name": "Example.mkv", "private": True},
        }
        self.writes: list[tuple[str, bool]] = []

    def write(self, path: str, *, overwrite: bool = False) -> None:
        self.writes.append((path, overwrite))
        Path(path).write_bytes(b"written")


class FakeTorrentAPI:
    current = FakeTorrentValue()
    copied: ClassVar[list[FakeTorrentValue]] = []

    @classmethod
    def read(cls, _path: str) -> FakeTorrentValue:
        return cls.current

    @classmethod
    def copy(cls, torrent: FakeTorrentValue) -> FakeTorrentValue:
        cls.copied.append(torrent)
        return torrent


@pytest.fixture(autouse=True)
def reset_fakes() -> None:
    FakeTorrentAPI.current = FakeTorrentValue()
    FakeTorrentAPI.copied = []


def test_language_helper_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    lookup = manager._build_language_alias_lookup()
    assert "english" in lookup and "en" in lookup["english"]
    assert manager._coerce_language_values("English") == ["English"]
    assert manager._coerce_language_values(["English", 1, "French"]) == [
        "English",
        "French",
    ]
    assert manager._coerce_language_values(None) == []
    assert manager._expand_language_candidates("", lookup) == set()
    expanded = manager._expand_language_candidates(
        "Portuguese, Brazil", lookup
    )
    assert "portuguese" in expanded
    assert manager._expand_language_list(["English", "French"], lookup) >= {
        "english",
        "french",
    }

    class BrokenLanguage:
        @classmethod
        def get(cls, _value: str) -> object:
            raise ValueError("invalid")

    monkeypatch.setattr(common.langcodes, "Language", BrokenLanguage)
    assert manager._expand_language_candidates("xx", lookup) == {"xx"}
    assert manager._format_language_for_display("xx") == "xx"
    assert manager._format_language_for_display("") == ""

    utf16 = tmp_path / "sub.srt"
    utf16.write_bytes("olá mundo".encode("utf-16"))
    assert "olá" in manager._read_subtitle_text(utf16)
    assert manager._read_subtitle_text(tmp_path / "missing.srt") == ""


def test_portuguese_video_description_attended_and_unattended(
    tmp_path: Path,
) -> None:
    manager = Common(_config())
    subtitle = tmp_path / "Movie.pt-BR.srt"
    subtitle.write_text("texto", encoding="utf-8")
    assert asyncio.run(
        manager.has_portuguese_external_subtitle(
            _meta(tmp_path, subtitle_files=[str(subtitle)])
        )
    )

    words = tmp_path / "Movie.srt"
    words.write_text("Você não sabe onde estamos agora", encoding="utf-8")
    assert asyncio.run(
        manager.has_portuguese_external_subtitle(
            _meta(tmp_path, subtitle_files=[str(words)])
        )
    )

    manager.has_portuguese_external_subtitle = AsyncMock(return_value=False)  # type: ignore[method-assign]
    manager.check_language_requirements = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        manager.check_portuguese_video_requirements(
            _meta(tmp_path, unattended=True, unattended_confirm=False), "TEST"
        )
    )
    assert asyncio.run(
        manager.check_portuguese_video_requirements(
            _meta(tmp_path, unattended=True, unattended_confirm=True), "TEST"
        )
    )

    manager.prompt_user_for_confirmation = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_portuguese_video_requirements(
            _meta(tmp_path, unattended=False), "TEST"
        )
    )

    assert not asyncio.run(
        manager.check_portuguese_description_requirements(
            "English description", "TEST", _meta(tmp_path)
        )
    )
    assert asyncio.run(
        manager.check_portuguese_description_requirements(
            "Descrição válida para você", "TEST", _meta(tmp_path)
        )
    )
    assert asyncio.run(
        manager.check_portuguese_description_requirements(
            "English",
            "TEST",
            _meta(tmp_path, unattended=True, unattended_confirm=True),
        )
    )
    manager.prompt_user_for_confirmation = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert not asyncio.run(
        manager.check_portuguese_description_requirements(
            "English", "TEST", _meta(tmp_path, unattended=False)
        )
    )


def test_portuguese_description_prefers_localized_ptbr_overview(
    tmp_path: Path,
) -> None:
    manager = Common(_config())
    meta = _meta(
        tmp_path,
        unattended=True,
        unattended_confirm=False,
        tmdb_localized_data={
            "pt-BR": {
                "main": {
                    "overview": (
                        "Você não sabe onde estamos agora. Isso ficou muito bem e vamos continuar."
                    )
                }
            }
        },
    )

    assert asyncio.run(
        manager.check_portuguese_description_requirements(
            "This is an English fallback description.", "AMIGOSSHARE", meta
        )
    )


def test_portuguese_description_candidates_fall_back_without_localized_data(
    tmp_path: Path,
) -> None:
    manager = Common(_config())
    meta = _meta(tmp_path, tmdb_localized_data={})

    assert manager._portuguese_description_candidates("fallback", meta) == (
        "fallback",
    )


def test_async_filesystem_and_torrent_filename(tmp_path: Path) -> None:
    manager = Common(_config())
    directory = tmp_path / "nested"
    asyncio.run(manager.makedirs(str(directory)))
    assert directory.is_dir()
    file = directory / "file.txt"
    file.write_text("x", encoding="utf-8")
    assert asyncio.run(manager.path_exists(str(file)))
    asyncio.run(manager.remove_file(str(file)))
    assert not file.exists()

    meta = _meta(tmp_path)
    assert asyncio.run(manager.get_torrent_filename(meta, {})) == "BASE"
    subs = tmp_path / "tmp" / meta.uuid / "BASE_SUBS.torrent"
    subs.write_bytes(b"torrent")
    assert (
        asyncio.run(
            manager.get_torrent_filename(meta, {"allow_ext_subtitles": True})
        )
        == "BASE_SUBS"
    )


def test_create_torrent_for_upload_private_public_entropy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "Torrent", FakeTorrentAPI)
    manager = Common(_config())
    meta = _meta(tmp_path, entropy=32)
    base = tmp_path / "tmp" / meta.uuid / "BASE.torrent"
    base.write_bytes(b"base")
    monkeypatch.setattr(common.secrets, "randbelow", lambda limit: limit - 1)
    asyncio.run(manager.create_torrent_for_upload(meta, "TEST", "SRC"))
    value = FakeTorrentAPI.current.metainfo
    assert "junk" not in value
    assert value["announce"] == "https://tracker.example/announce"
    assert (
        value["info"]["private"] is True and value["info"]["source"] == "SRC"
    )
    assert value["info"]["entropy"] == 2**32 - 1
    assert value["comment"] == ""
    assert "using Upload Assistant 1.0" in value["created by"]
    assert (tmp_path / "tmp" / meta.uuid / "[TEST].torrent").is_file()

    FakeTorrentAPI.current = FakeTorrentValue(
        {
            "info": {"private": True},
            "announce": "old",
            "announce-list": [["old"]],
        }
    )
    meta.entropy = 64
    asyncio.run(
        manager.create_torrent_for_upload(
            meta,
            "TEST",
            "PUBLIC",
            torrent_filename="BASE",
            is_public=True,
            public_trackers=["https://one", "https://two"],
        )
    )
    value = FakeTorrentAPI.current.metainfo
    assert "private" not in value["info"]
    assert value["announce"] == "https://one"
    assert value["announce-list"] == [["https://one"], ["https://two"]]

    FakeTorrentAPI.current = FakeTorrentValue(
        {
            "info": {"private": True},
            "announce": "old",
            "announce-list": [["old"]],
        }
    )
    meta.entropy = "bad"
    asyncio.run(
        manager.create_torrent_for_upload(
            meta, "TEST", "PUBLIC", is_public=True, public_trackers=[]
        )
    )
    value = FakeTorrentAPI.current.metainfo
    assert "announce" not in value and "announce-list" not in value


def test_create_torrent_for_upload_announce_override_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "Torrent", FakeTorrentAPI)
    config = _config()
    config["TRACKERS"]["TEST"]["announce_url"] = ""
    manager = Common(config)
    meta = _meta(tmp_path)
    base = tmp_path / "tmp" / meta.uuid / "BASE.torrent"
    base.write_bytes(b"base")
    asyncio.run(
        manager.create_torrent_for_upload(
            meta, "TEST", "SRC", announce_url="https://override"
        )
    )
    assert FakeTorrentAPI.current.metainfo["announce"] == "https://override"
    FakeTorrentAPI.current = FakeTorrentValue()
    asyncio.run(manager.create_torrent_for_upload(meta, "TEST", "SRC"))
    assert (
        FakeTorrentAPI.current.metainfo["announce"] == "https://fake.tracker"
    )

    base.unlink()
    FakeTorrentAPI.copied.clear()
    asyncio.run(manager.create_torrent_for_upload(meta, "TEST", "SRC"))
    assert FakeTorrentAPI.copied == []


def test_ready_to_seed_and_torrent_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(common, "Torrent", FakeTorrentAPI)
    manager = Common(_config())
    meta = _meta(tmp_path)
    tracker_path = tmp_path / "tmp" / meta.uuid / "[TEST].torrent"
    tracker_path.write_bytes(b"torrent")

    assert (
        asyncio.run(
            manager.create_torrent_ready_to_seed(meta, "TEST", "SRC", [])
        )
        is None
    )
    result = asyncio.run(
        manager.create_torrent_ready_to_seed(
            meta,
            "TEST",
            "SRC",
            ["https://one", "https://two"],
            "id=",
            hash_is_id=True,
        )
    )
    assert result and len(result) == 40
    assert FakeTorrentAPI.current.metainfo["announce"] == "https://one"
    assert FakeTorrentAPI.current.metainfo["announce-list"] == [
        ["https://one", "https://two"]
    ]
    assert FakeTorrentAPI.current.metainfo["comment"].startswith("id=")

    FakeTorrentAPI.current = FakeTorrentValue({"info": {"name": "x"}})
    assert (
        asyncio.run(
            manager.create_torrent_ready_to_seed(
                meta, "TEST", "SRC", "https://tracker", "comment"
            )
        )
        is None
    )
    assert FakeTorrentAPI.current.metainfo["comment"] == "comment"

    tracker_path.unlink()
    assert (
        asyncio.run(
            manager.create_torrent_ready_to_seed(
                meta, "TEST", "SRC", "https://tracker"
            )
        )
        is None
    )

    info = {b"name": b"Example"}
    torrent_data = {b"info": info}
    tracker_path.write_bytes(bencodepy.encode(torrent_data))
    monkeypatch.setattr(common, "Torrent", __import__("torf").Torrent)
    expected = hashlib.sha1(
        bencodepy.encode(info), usedforsecurity=False
    ).hexdigest()
    assert asyncio.run(manager.get_torrent_hash(meta, "TEST")) == expected

    tracker_path.write_bytes(bencodepy.encode([b"bad"]))
    assert asyncio.run(manager.get_torrent_hash(meta, "TEST")) == ""
    tracker_path.write_bytes(bencodepy.encode({b"info": b"bad"}))
    assert asyncio.run(manager.get_torrent_hash(meta, "TEST")) == ""


def test_save_image_links_schema_accumulation_and_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    meta = _meta(tmp_path)
    assert asyncio.run(manager.save_image_links(meta, "disc", None)) is None

    result = asyncio.run(
        manager.save_image_links(
            meta,
            "disc",
            [{"raw_url": "raw1", "web_url": "web1", "img_url": "img1"}],
        )
    )
    assert result
    path = Path(result)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert (
        payload["keys"]["disc"]["count"] == 1 and payload["total_count"] == 1
    )

    asyncio.run(
        manager.save_image_links(
            meta, "disc", [{"raw_url": "raw2", "web_url": "web2"}]
        )
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert (
        payload["keys"]["disc"]["count"] == 2 and payload["total_count"] == 2
    )

    path.write_text("not-json", encoding="utf-8")
    assert asyncio.run(manager.save_image_links(meta, "new", []))
    path.write_text(json.dumps([]), encoding="utf-8")
    assert asyncio.run(manager.save_image_links(meta, "new", []))
    path.write_text(
        json.dumps({"keys": [], "total_count": "bad"}), encoding="utf-8"
    )
    assert asyncio.run(manager.save_image_links(meta, "new", []))
    path.write_text(
        json.dumps(
            {
                "keys": {"new": {"count": "bad", "images": "bad"}},
                "total_count": 0,
            }
        ),
        encoding="utf-8",
    )
    assert asyncio.run(
        manager.save_image_links(
            meta, "new", [{"raw_url": "r", "web_url": "w"}]
        )
    )

    original_open = common.aiofiles.open

    def fail_write(path_value: object, mode: str = "r", **kwargs: object):
        if "w" in mode:
            raise OSError("read only")
        return original_open(path_value, mode=mode, **kwargs)

    monkeypatch.setattr(common.aiofiles, "open", fail_write)
    assert asyncio.run(manager.save_image_links(meta, "error", [])) is None


class JsonResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class JsonClient:
    queue: ClassVar[list[object]] = []
    calls: ClassVar[list[tuple[str, dict[str, object]]]] = []

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

    async def get(
        self, url: str | None = None, **kwargs: object
    ) -> JsonResponse:
        type(self).calls.append((str(url), dict(kwargs)))
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, JsonResponse)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.calls = []


def test_prompt_selection_and_confirmation_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    assert asyncio.run(
        manager.prompt_user_for_id_selection(
            _meta(tmp_path), tmdb=1, imdb=12, tvdb=3, mal=4, filename="x"
        )
    )

    answers = iter(("n", "yes", "", "n"))

    async def prompt(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    monkeypatch.setattr(common, "prompt_in_thread", prompt)
    attended = _meta(tmp_path, unattended=False)
    assert not asyncio.run(
        manager.prompt_user_for_id_selection(
            attended, tmdb=1, tracker_name=None
        )
    )
    assert asyncio.run(
        manager.prompt_user_for_id_selection(
            attended, imdb=12, tracker_name="TEST"
        )
    )
    assert asyncio.run(
        manager.prompt_user_for_confirmation("Continue", attended)
    )
    assert not asyncio.run(
        manager.prompt_user_for_confirmation("Continue", attended)
    )
    assert asyncio.run(
        manager.prompt_user_for_confirmation(
            "Continue",
            _meta(tmp_path, unattended=True, unattended_confirm=True),
        )
    )


def test_prompt_selection_eof_translates_to_domain_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())

    async def eof(*_args: object, **_kwargs: object) -> str:
        raise EOFError

    monkeypatch.setattr(common, "prompt_in_thread", eof)
    with pytest.raises(OperationAbortedError, match="cancelled"):
        asyncio.run(
            manager.prompt_user_for_id_selection(
                _meta(tmp_path, unattended=False)
            )
        )


def test_region_distributor_mapping_and_http_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common({"TRACKERS": {"TEST": {"api_key": " key "}}})
    assert asyncio.run(manager.unit3d_region_ids("AUS")) == "14"
    assert (
        asyncio.run(manager.unit3d_region_ids(reverse=True, region_id=14))
        == "AUS"
    )
    assert (
        asyncio.run(manager.unit3d_region_ids(reverse=True, region_id=9999))
        == ""
    )
    assert asyncio.run(manager.unit3d_distributor_ids("WARNER"))
    assert (
        asyncio.run(
            manager.unit3d_distributor_ids(reverse=True, distributor_id=99999)
        )
        == ""
    )

    meta = _meta(tmp_path, is_disc="BDMV", region="", distributor="")
    manager.unit3d_region_ids = AsyncMock(return_value="USA")  # type: ignore[method-assign]
    manager.unit3d_distributor_ids = AsyncMock(return_value="Warner")  # type: ignore[method-assign]
    asyncio.run(
        manager._apply_region_distributor(
            meta, {"region_id": 1, "distributor_id": 2}
        )
    )
    assert meta.region == "USA" and meta.distributor == "Warner"

    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)
    JsonClient.reset(
        JsonResponse(
            {"data": [{"attributes": {"region_id": 1, "distributor_id": 2}}]}
        )
    )
    meta = _meta(tmp_path, uuid="region-list", region="", distributor="")
    asyncio.run(
        manager.unit3d_region_distributor(
            meta, "TEST", "https://tracker/", "1"
        )
    )
    assert meta.region == "USA" and meta.distributor == "Warner"

    JsonClient.reset(JsonResponse({"attributes": {"region_id": 1}}))
    meta = _meta(
        tmp_path, uuid="region-direct", region="", distributor="Already"
    )
    asyncio.run(
        manager.unit3d_region_distributor(meta, "TEST", "https://tracker/")
    )
    assert meta.region == "USA"

    for payload in ({"data": "404"}, ValueError("bad json")):
        JsonClient.reset(JsonResponse(payload))
        asyncio.run(
            manager.unit3d_region_distributor(
                _meta(tmp_path, uuid=f"region-{len(JsonClient.calls)}"),
                "TEST",
                "https://tracker/",
            )
        )

    request = httpx.RequestError(
        "offline", request=httpx.Request("GET", "https://tracker")
    )
    JsonClient.reset(request)
    asyncio.run(
        manager.unit3d_region_distributor(
            _meta(tmp_path, uuid="region-error"), "TEST", "https://tracker/"
        )
    )


def test_unit3d_torrent_info_list_direct_prompt_description_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"TRACKERS": {"TEST": {"api_key": "key"}}}
    manager = Common(config)
    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)
    manager.unit3d_region_ids = AsyncMock(return_value="USA")  # type: ignore[method-assign]
    manager.unit3d_distributor_ids = AsyncMock(return_value="Warner")  # type: ignore[method-assign]
    manager.prompt_user_for_id_selection = AsyncMock(return_value=True)  # type: ignore[method-assign]

    class FakeBBCode:
        def clean_unit3d_description(
            self, description: str, _url: str
        ) -> tuple[str, list[dict[str, str]]]:
            return f"clean:{description}", [
                {"raw_url": "raw", "web_url": "web"}
            ]

    monkeypatch.setattr(common, "BBCODE", FakeBBCode)
    attrs = {
        "category": "MOVIE",
        "description": "desc",
        "tmdb_id": "123",
        "tvdb_id": "456",
        "mal_id": "7",
        "imdb_id": "7654321",
        "info_hash": "HASH",
        "region_id": 1,
        "distributor_id": 2,
    }
    JsonClient.reset(JsonResponse({"data": [{"attributes": attrs}]}))
    meta = _meta(
        tmp_path,
        unattended=True,
        is_disc="BDMV",
        region="",
        distributor="",
        keep_images=True,
    )
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            meta,
            file_name="Example.mkv",
        )
    )
    assert result[:7] == (123, 7654321, 456, 7, "clean:desc", "MOVIE", "HASH")
    assert result[7] and meta.region == "USA" and meta.distributor == "Warner"
    assert meta.tracker_description_raw["TEST"] == "desc"

    direct_attrs = {
        **attrs,
        "files": [{"name": "one.mkv"}, {"name": "two.mkv"}],
    }
    JsonClient.reset(JsonResponse({"attributes": direct_attrs}))
    meta = _meta(
        tmp_path, uuid="direct-info", unattended=True, keep_images=False
    )
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            meta,
            id=5,
            skip_tracker_descriptions=True,
        )
    )
    assert (
        result[4] == ""
        and result[7] == []
        and result[8] == ["one.mkv", "two.mkv"]
    )

    assert asyncio.run(
        manager.unit3d_torrent_info(
            "TEST", "https://torrent/", "https://search", meta
        )
    ) == (None, None, None, None, None, None, None, [], None)

    manager.prompt_user_for_id_selection = AsyncMock(return_value=False)  # type: ignore[method-assign]
    JsonClient.reset(JsonResponse({"data": [{"attributes": attrs}]}))
    assert asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            _meta(tmp_path, uuid="skip"),
            file_name="x",
        )
    ) == (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        [],
        None,
    )

    for response in (
        JsonResponse({"data": "404"}),
        JsonResponse(ValueError("bad json")),
        httpx.RequestError(
            "offline", request=httpx.Request("GET", "https://tracker")
        ),
    ):
        JsonClient.reset(response)
        result = asyncio.run(
            manager.unit3d_torrent_info(
                "TEST",
                "https://torrent/",
                "https://search",
                _meta(tmp_path, uuid="error-info"),
                id=1,
            )
        )
        assert result == (None, None, None, None, None, None, None, [], None)

    JsonClient.reset(JsonResponse({"attributes": {"tmdb_id": "not-int"}}))
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            _meta(tmp_path, uuid="invalid-info"),
            id=1,
        )
    )
    assert result == (None, None, None, None, None, None, None, [], None)


def test_unit3d_torrent_info_attended_edit_discard_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common({"TRACKERS": {"TEST": {"api_key": "key"}}})
    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)

    class FakeBBCode:
        def clean_unit3d_description(
            self, description: str, _url: str
        ) -> tuple[str, list[dict[str, str]]]:
            return description, [{"raw_url": "raw", "web_url": "web"}]

    monkeypatch.setattr(common, "BBCODE", FakeBBCode)
    attrs = {"description": "description", "tmdb_id": 1}

    monkeypatch.setattr(
        common.cli_ui, "ask_string", lambda *_args, **_kwargs: "e"
    )
    monkeypatch.setattr(common.click, "edit", lambda _value: " edited ")
    JsonClient.reset(JsonResponse({"attributes": attrs}))
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            _meta(tmp_path, unattended=False, keep_images=True),
            id=1,
        )
    )
    assert result[4] == "edited" and result[7]

    monkeypatch.setattr(
        common.cli_ui, "ask_string", lambda *_args, **_kwargs: "d"
    )
    JsonClient.reset(JsonResponse({"attributes": attrs}))
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            _meta(tmp_path, uuid="discard", unattended=False),
            id=1,
        )
    )
    assert result[4] is None and result[7] == []

    monkeypatch.setattr(
        common.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    JsonClient.reset(JsonResponse({"attributes": attrs}))
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST",
            "https://torrent/",
            "https://search",
            _meta(tmp_path, uuid="keep", unattended=False, keep_images=True),
            id=1,
        )
    )
    assert result[4] == "description"


def test_parse_cookie_file(tmp_path: Path) -> None:
    cookie = tmp_path / "cookies.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n# comment\n.example.com\tTRUE\t/\tFALSE\t0\tsession\tvalue\nshort line\n",
        encoding="utf-8",
    )
    assert asyncio.run(Common(_config()).parse_cookie_file(str(cookie))) == {
        "session": "value"
    }


def test_ptgen_success_retries_prompts_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)
    meta = _meta(
        tmp_path,
        imdb_id=1234567,
        imdb_info={"cover": "cover.jpg"},
        artwork_url="fallback.jpg",
    )
    JsonClient.reset(
        JsonResponse({"error": "retry"}),
        JsonResponse({"data": [{"link": "https://douban/1"}]}),
        JsonResponse({"format": "[img]old[/img]Details"}),
    )
    result = asyncio.run(
        manager.ptgen(meta, ptgen_site="https://ptgen", ptgen_retry=2)
    )
    assert result == "[img]cover.jpg[/img]Details"
    assert meta.ptgen["format"]

    async def answer(*_args: object, **_kwargs: object) -> str:
        return "https://douban/manual"

    monkeypatch.setattr(common, "prompt_in_thread", answer)
    meta = _meta(
        tmp_path,
        uuid="ptgen-manual",
        imdb_id=None,
        unattended=False,
        imdb_info={},
        artwork_url="art.jpg",
    )
    JsonClient.reset(JsonResponse({"format": "Manual"}))
    assert "Manual" in asyncio.run(manager.ptgen(meta))

    meta = _meta(
        tmp_path,
        uuid="ptgen-no-data",
        imdb_id=123,
        unattended=True,
        unattended_confirm=False,
        imdb_info={},
        artwork_url="art.jpg",
    )
    JsonClient.reset(
        JsonResponse({"data": []}), JsonResponse({"format": "Fallback"})
    )
    assert "Fallback" in asyncio.run(manager.ptgen(meta, ptgen_retry=0))

    meta = _meta(
        tmp_path,
        uuid="ptgen-fail",
        imdb_id=None,
        unattended=True,
        unattended_confirm=False,
    )
    JsonClient.reset(
        JsonResponse({"error": "bad"}), JsonResponse({"error": "bad"})
    )
    assert asyncio.run(manager.ptgen(meta, ptgen_retry=1)) == ""

    JsonClient.reset(RuntimeError("unexpected"))
    assert (
        asyncio.run(
            manager.ptgen(
                _meta(tmp_path, uuid="ptgen-error", imdb_id=1), ptgen_retry=0
            )
        )
        == ""
    )


def test_mediainfo_parser_and_bbcode_all_sections() -> None:
    parser = Common.MediaInfoParser()
    text = """
General
File name : Example.mkv
Format : Matroska
Ignored : no
Video #1
Format : AVC
Width : 1920
Audio #1
Format : AAC
Language : English
Channels : 2
Text #1
Title : Forced
Title : Duplicate
Language : French
Language : Duplicate
Menu
00:00:00 : Chapter
"""
    parsed = parser.parse_mediainfo(text)
    assert parsed["general"]["file_name"] == "Example.mkv"
    assert parsed["video"][0]["width"] == "1920"
    assert parsed["audio"][0]["language"] == "English"
    assert parsed["text"][0] == {"title": "Forced", "language": "French"}
    rendered = parser.format_bbcode(parsed)
    assert "[b]General[/b]" in rendered and "[b]Video[/b]" in rendered
    assert "1. English / AAC / 2" in rendered and "French" in rendered
    assert parser.format_bbcode({}) == "\n\n"


def test_get_bdmv_mediainfo_generation_filter_fallback_and_non_disc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    assert (
        asyncio.run(manager.get_bdmv_mediainfo(_meta(tmp_path, is_disc="")))
        == ""
    )

    playlist = tmp_path / "playlist.mpls"
    playlist.write_bytes(b"playlist")
    largest = tmp_path / "largest.m2ts"
    largest.write_bytes(b"video")
    meta = _meta(
        tmp_path,
        is_disc="BDMV",
        discs=[
            {
                "playlists": [
                    {
                        "path": str(playlist),
                        "items": [
                            {"file": str(largest), "size": 100},
                            {"file": "small", "size": 1},
                        ],
                    }
                ]
            }
        ],
    )
    mi_path = tmp_path / "tmp" / meta.uuid / "MEDIAINFO_CLEANPATH.txt"

    async def export(path: str, *_args: object, **_kwargs: object) -> None:
        text = (
            "Remove: hidden\nKeep: "
            + ("x" * 80 if path == str(playlist) else "short")
            + "\n"
        )
        mi_path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(common, "export_info", export)
    result = asyncio.run(
        manager.get_bdmv_mediainfo(meta, remove=["Remove:"], char_limit=30)
    )
    assert "short" in result and "Remove:" not in result

    meta = _meta(
        tmp_path,
        uuid="bd-no-items",
        is_disc="BDMV",
        discs=[{"playlists": [{"path": str(playlist), "items": []}]}],
    )
    mi_path = tmp_path / "tmp" / meta.uuid / "MEDIAINFO_CLEANPATH.txt"
    mi_path.write_text("x" * 50, encoding="utf-8")
    assert (
        len(asyncio.run(manager.get_bdmv_mediainfo(meta, char_limit=10))) == 50
    )


def test_language_requirements_book_video_original_and_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    assert asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, category="MUSIC"),
            "TEST",
            ["English"],
            check_audio=True,
        )
    )
    assert asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, category="BOOK", book_language="English"),
            "TEST",
            ["English"],
            check_audio=True,
        )
    )
    assert asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, category="BOOK", book_language=""),
            "TEST",
            ["English"],
            check_audio=True,
        )
    )

    manager.prompt_user_for_confirmation = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_language_requirements(
            _meta(
                tmp_path,
                category="BOOK",
                book_language="French",
                unattended=False,
            ),
            "TEST",
            ["English"],
            check_audio=True,
        )
    )
    assert not asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, category="BOOK", book_language="French"),
            "TEST",
            ["English"],
            check_audio=True,
            prompt_on_failure=False,
        )
    )

    process = AsyncMock()
    monkeypatch.setattr(
        common.languages_manager, "process_desc_language", process
    )
    meta = _meta(
        tmp_path,
        language_checked=False,
        audio_languages=["English"],
        subtitle_languages=["French"],
    )
    assert asyncio.run(
        manager.check_language_requirements(
            meta, "TEST", ["English"], check_audio=True
        )
    )
    process.assert_awaited_once()

    assert asyncio.run(
        manager.check_language_requirements(
            _meta(
                tmp_path,
                language_checked=True,
                audio_languages=["English"],
                subtitle_languages=["English"],
            ),
            "TEST",
            ["English"],
            check_audio=True,
            check_subtitle=True,
            require_both=True,
        )
    )
    assert not asyncio.run(
        manager.check_language_requirements(
            _meta(
                tmp_path,
                language_checked=True,
                audio_languages=["French"],
                subtitle_languages=[],
            ),
            "TEST",
            ["English"],
            check_audio=True,
            check_subtitle=True,
            require_both=True,
            prompt_on_failure=False,
        )
    )

    original = _meta(
        tmp_path,
        language_checked=True,
        original_language="French",
        audio_languages=["French"],
        subtitle_languages=["English"],
    )
    assert asyncio.run(
        manager.check_language_requirements(
            original,
            "TEST",
            ["English"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
        )
    )
    original.subtitle_languages = []
    assert not asyncio.run(
        manager.check_language_requirements(
            original,
            "TEST",
            ["English"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
            prompt_on_failure=False,
        )
    )

    required = _meta(
        tmp_path,
        language_checked=True,
        original_language="French",
        audio_languages=["English"],
        debug=True,
    )
    assert not asyncio.run(
        manager.check_language_requirements(
            required,
            "TEST",
            ["English"],
            check_audio=True,
            original_language=True,
            original_required=True,
            prompt_on_failure=False,
        )
    )
    manager.prompt_user_for_confirmation = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_language_requirements(
            required,
            "TEST",
            ["English"],
            check_audio=True,
            original_language=True,
            original_required=True,
            prompt_on_failure=True,
        )
    )

    assert asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, language_checked=True),
            "TEST",
            [],
            check_audio=False,
            check_subtitle=False,
        )
    )

    monkeypatch.setattr(
        manager,
        "_build_language_alias_lookup",
        lambda: (_ for _ in ()).throw(RuntimeError("bad language")),
    )
    assert not asyncio.run(
        manager.check_language_requirements(
            _meta(tmp_path, language_checked=True),
            "TEST",
            ["English"],
            check_audio=True,
        )
    )


def test_misc_common_outputs_nzb_and_bdinfo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(
        {
            **_config(),
            "USENET": {"archive_password": "secret", "skip_archive": False},
        }
    )
    meta = _meta(
        tmp_path,
        resolution="1080p",
        audio="AAC 2.0",
        video_bitrate=5000,
        audio_bitrate=192,
    )
    path = asyncio.run(
        manager.save_html_file(meta, "TEST", "<b>html</b>", "-page")
    )
    assert Path(path).read_text(encoding="utf-8") == "<b>html</b>"
    assert (
        manager.get_small_description(meta)
        == "1080p @ 5000 kbps - AAC 2.0 @ 192 kbps"
    )
    meta.video_bitrate = None
    meta.audio_bitrate = None
    assert "0 kbps" in manager.get_small_description(meta)

    assert (
        manager.portuguese_title_capitalization(
            "o senhor dos anéis: a sociedade do anel"
        )
        == "O Senhor dos Anéis: A Sociedade do Anel"
    )
    assert manager.portuguese_title_capitalization("---") == "---"
    assert manager.has_bdinfo("DISC INFO:\nDisc Title: X")
    assert not manager.has_bdinfo("")
    assert not manager.has_bdinfo(123)  # type: ignore[arg-type]

    assert not asyncio.run(
        manager.check_nzb_file("TEST", _meta(tmp_path, nzb_path=""))
    )
    nzb = tmp_path / "release.nzb"
    nzb.write_text("nzb", encoding="utf-8")
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(common, "verify_nzb_has_password", verify)
    assert not asyncio.run(
        manager.check_nzb_file(
            "TEST",
            _meta(
                tmp_path,
                uuid="nzb",
                nzb_path=str(nzb),
                archive_password="".join(("sec", "ret")),
            ),
        )
    )
    verify.return_value = True
    assert asyncio.run(
        manager.check_nzb_file(
            "TEST",
            _meta(
                tmp_path,
                uuid="nzb-ok",
                nzb_path=str(nzb),
                archive_password="".join(("sec", "ret")),
            ),
        )
    )

    manager.config["USENET"]["skip_archive"] = True
    verify.reset_mock()
    assert asyncio.run(
        manager.check_nzb_file(
            "TEST", _meta(tmp_path, uuid="nzb-skip", nzb_path=str(nzb))
        )
    )
    verify.assert_not_awaited()


class StreamResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        redirect: bool = False,
    ) -> None:
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks or []
        self.is_redirect = redirect
        self.request = httpx.Request("GET", "https://tracker.example/download")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError(
                "failed", request=self.request, response=response
            )

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


class StreamClient:
    queue: ClassVar[list[object]] = []
    instances: ClassVar[list[StreamClient]] = []

    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.kwargs = kwargs
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

    def stream(self, *_args: object, **_kwargs: object) -> StreamResponse:
        value = type(self).queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, StreamResponse)
        return value

    @classmethod
    def reset(cls, *values: object) -> None:
        cls.queue = list(values)
        cls.instances = []


def test_download_tracker_torrent_success_redirect_hash_cross_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    meta = _meta(tmp_path)
    monkeypatch.setattr(common.httpx, "AsyncClient", StreamClient)

    StreamClient.reset(
        StreamResponse(
            chunks=[b"abc", b"def"], headers={"content-length": "6"}
        )
    )
    result = asyncio.run(
        manager.download_tracker_torrent(
            meta, "TEST", downurl="https://tracker.example/file"
        )
    )
    assert result and Path(result).read_bytes() == b"abcdef"

    StreamClient.reset(
        StreamResponse(redirect=True, headers={"location": "/next"}),
        StreamResponse(chunks=[b"redirected"]),
    )
    result = asyncio.run(
        manager.download_tracker_torrent(
            _meta(tmp_path, uuid="redirect"),
            "TEST",
            downurl="https://tracker.example/start",
            allowed_hosts=("tracker.example",),
        )
    )
    assert result and Path(result).read_bytes() == b"redirected"

    manager.get_torrent_hash = AsyncMock(return_value="HASH")  # type: ignore[method-assign]
    StreamClient.reset(StreamResponse(chunks=[b"torrent"]))
    assert (
        asyncio.run(
            manager.download_tracker_torrent(
                _meta(tmp_path, uuid="hash"),
                "TEST",
                downurl="https://tracker.example/file",
                hash_is_id=True,
            )
        )
        == "HASH"
    )

    StreamClient.reset(StreamResponse(chunks=[b"torrent"]))
    assert (
        asyncio.run(
            manager.download_tracker_torrent(
                _meta(tmp_path, uuid="cross"),
                "TEST",
                downurl="https://tracker.example/file",
                cross=True,
            )
        )
        is None
    )

    for uuid, response, max_size in (
        (
            "header-too-large",
            StreamResponse(chunks=[b"x"], headers={"content-length": "20"}),
            5,
        ),
        ("stream-too-large", StreamResponse(chunks=[b"123", b"456"]), 5),
    ):
        target = _meta(tmp_path, uuid=uuid)
        StreamClient.reset(response)
        assert (
            asyncio.run(
                manager.download_tracker_torrent(
                    target,
                    "TEST",
                    downurl="https://tracker.example/file",
                    allowed_hosts=("tracker.example",),
                    max_size=max_size,
                )
            )
            is None
        )
        assert not (tmp_path / "tmp" / uuid / "[TEST].torrent").exists()


def test_download_tracker_torrent_security_redirect_errors_cookie_and_no_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    monkeypatch.setattr(common.httpx, "AsyncClient", StreamClient)
    assert (
        asyncio.run(manager.download_tracker_torrent(_meta(tmp_path), "TEST"))
        is None
    )

    invalid_urls = (
        "http://tracker.example/file",
        "https://evil.example/file",
        "https://user:pass@tracker.example/file",
        "https://tracker.example:444/file",
    )
    for index, url in enumerate(invalid_urls):
        StreamClient.reset(StreamResponse(chunks=[b"never"]))
        assert (
            asyncio.run(
                manager.download_tracker_torrent(
                    _meta(tmp_path, uuid=f"invalid-url-{index}"),
                    "TEST",
                    downurl=url,
                    allowed_hosts=("tracker.example",),
                )
            )
            is None
        )

    StreamClient.reset(StreamResponse(redirect=True, headers={}))
    assert (
        asyncio.run(
            manager.download_tracker_torrent(
                _meta(tmp_path, uuid="missing-location"),
                "TEST",
                downurl="https://tracker.example/start",
                allowed_hosts=("tracker.example",),
            )
        )
        is None
    )

    StreamClient.reset(
        *[
            StreamResponse(redirect=True, headers={"location": "/next"})
            for _ in range(6)
        ]
    )
    assert (
        asyncio.run(
            manager.download_tracker_torrent(
                _meta(tmp_path, uuid="redirect-loop"),
                "TEST",
                downurl="https://tracker.example/start",
                allowed_hosts=("tracker.example",),
            )
        )
        is None
    )

    class CookieDouble:
        def __init__(self, _config: dict[str, Any]) -> None:
            pass

        async def load_session_cookies(
            self, _meta: Meta, _tracker: str
        ) -> dict[str, str]:
            return {"session": "cookie"}

    import src.integrations.trackers.cookie_auth as cookie_auth

    monkeypatch.setattr(cookie_auth, "CookieValidator", CookieDouble)
    StreamClient.reset(StreamResponse(chunks=[b"cookie"]))
    assert asyncio.run(
        manager.download_tracker_torrent(
            _meta(tmp_path, uuid="cookie"),
            "TEST",
            downurl="https://tracker.example/file",
            use_cookie_auth=True,
        )
    )
    assert StreamClient.instances[-1].kwargs["cookies"] == {
        "session": "cookie"
    }

    class BrokenCookie(CookieDouble):
        async def load_session_cookies(
            self, _meta: Meta, _tracker: str
        ) -> dict[str, str]:
            raise RuntimeError("cookie failed")

    monkeypatch.setattr(cookie_auth, "CookieValidator", BrokenCookie)
    StreamClient.reset(StreamResponse(chunks=[b"without-cookie"]))
    assert asyncio.run(
        manager.download_tracker_torrent(
            _meta(tmp_path, uuid="cookie-error"),
            "TEST",
            downurl="https://tracker.example/file",
            use_cookie_auth=True,
        )
    )
    assert StreamClient.instances[-1].kwargs["cookies"] is None


def test_remaining_language_helper_and_portuguese_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    lookup = manager._build_language_alias_lookup()
    expanded = manager._expand_language_candidates("en", lookup)
    assert "english" in expanded and "en" in expanded
    assert manager._format_language_for_display("en") == "english"
    assert not manager.is_portuguese_description("")

    forced = tmp_path / "Movie.pt-BR.forced.srt"
    forced.write_text("text", encoding="utf-8")
    assert asyncio.run(
        manager.has_portuguese_external_subtitle(
            _meta(tmp_path, subtitle_files=[str(forced)])
        )
    )

    manager.has_portuguese_external_subtitle = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_portuguese_video_requirements(_meta(tmp_path), "TEST")
    )
    manager.has_portuguese_external_subtitle = AsyncMock(return_value=False)  # type: ignore[method-assign]
    manager.check_language_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_portuguese_video_requirements(
            _meta(tmp_path, uuid="lang-true"), "TEST"
        )
    )

    original_read = Path.read_text

    def always_unicode_error(
        _path: Path, *_args: object, **_kwargs: object
    ) -> str:
        raise UnicodeError("decode")

    monkeypatch.setattr(Path, "read_text", always_unicode_error)
    assert manager._read_subtitle_text(forced) == ""
    monkeypatch.setattr(Path, "read_text", original_read)


def test_region_distributor_string_ids_and_invalid_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common({"TRACKERS": {"TEST": {"api_key": "key"}}})
    assert (
        asyncio.run(manager.unit3d_region_ids(reverse=True, region_id="14"))
        == "AUS"
    )  # type: ignore[arg-type]
    assert (
        asyncio.run(manager.unit3d_region_ids(reverse=True, region_id="bad"))
        == ""
    )  # type: ignore[arg-type]
    distributor_id = int(asyncio.run(manager.unit3d_distributor_ids("WARNER")))
    assert asyncio.run(
        manager.unit3d_distributor_ids(
            reverse=True, distributor_id=str(distributor_id)
        )
    ).startswith("WARNER")  # type: ignore[arg-type]
    assert (
        asyncio.run(
            manager.unit3d_distributor_ids(reverse=True, distributor_id="bad")
        )
        == ""
    )  # type: ignore[arg-type]

    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)
    JsonClient.reset(JsonResponse([]))
    asyncio.run(
        manager.unit3d_region_distributor(
            _meta(tmp_path), "TEST", "https://tracker/"
        )
    )


def test_unit3d_direct_disc_region_single_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common({"TRACKERS": {"TEST": {"api_key": "key"}}})
    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)
    manager.unit3d_region_ids = AsyncMock(return_value="USA")  # type: ignore[method-assign]
    manager.unit3d_distributor_ids = AsyncMock(return_value="Warner")  # type: ignore[method-assign]
    JsonClient.reset(
        JsonResponse(
            {
                "attributes": {
                    "tmdb_id": 1,
                    "region_id": 1,
                    "distributor_id": 2,
                    "files": [{"name": "only.mkv"}],
                }
            }
        )
    )
    meta = _meta(tmp_path, is_disc="DVD", region="", distributor="")
    result = asyncio.run(
        manager.unit3d_torrent_info(
            "TEST", "https://torrent/", "https://search", meta, id=1
        )
    )
    assert (
        result[8] == "only.mkv"
        and meta.region == "USA"
        and meta.distributor == "Warner"
    )


def test_ptgen_attended_imdb_failure_and_second_stage_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = Common(_config())
    monkeypatch.setattr(common.httpx, "AsyncClient", JsonClient)

    async def answer(*_args: object, **_kwargs: object) -> str:
        return "https://douban/manual"

    monkeypatch.setattr(common, "prompt_in_thread", answer)
    meta = _meta(
        tmp_path,
        imdb_id=1,
        unattended=False,
        imdb_info={},
        artwork_url="cover",
    )
    JsonClient.reset(
        JsonResponse({"data": []}),
        JsonResponse({"error": "retry"}),
        JsonResponse({"format": "Recovered"}),
    )
    assert "Recovered" in asyncio.run(manager.ptgen(meta, ptgen_retry=1))

    meta = _meta(
        tmp_path,
        uuid="ptgen-json-error",
        imdb_id=1,
        unattended=True,
        unattended_confirm=False,
    )
    JsonClient.reset(
        JsonResponse(ValueError("bad json")),
        JsonResponse({"data": []}),
        JsonResponse({"format": "Okay"}),
    )
    assert "Okay" in asyncio.run(manager.ptgen(meta, ptgen_retry=1))


def test_mediainfo_parser_final_section_and_bdmv_missing_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = Common.MediaInfoParser()
    assert parser.parse_mediainfo("Audio\nFormat : AAC")["audio"] == [
        {"format": "AAC"}
    ]
    assert parser.parse_mediainfo("General\nFormat : Matroska")["general"] == {
        "format": "Matroska"
    }

    manager = Common(_config())
    playlist = tmp_path / "playlist.mpls"
    playlist.write_bytes(b"playlist")
    meta = _meta(
        tmp_path,
        uuid="bd-missing-export",
        is_disc="BDMV",
        discs=[{"playlists": [{"path": str(playlist), "items": []}]}],
    )
    monkeypatch.setattr(common, "export_info", AsyncMock(return_value=None))
    assert asyncio.run(manager.get_bdmv_mediainfo(meta)) == ""


def test_remaining_language_requirement_and_title_branches(
    tmp_path: Path,
) -> None:
    manager = Common(_config())
    original_list = _meta(
        tmp_path,
        language_checked=True,
        original_language=["French"],
        audio_languages=["French"],
        subtitle_languages=["English"],
    )
    assert asyncio.run(
        manager.check_language_requirements(
            original_list,
            "TEST",
            ["English"],
            check_audio=True,
            check_subtitle=True,
            original_language=True,
        )
    )

    original_non_string = _meta(
        tmp_path,
        uuid="orig-non-string",
        language_checked=True,
        original_language=[1],
        audio_languages=["English"],
    )
    assert asyncio.run(
        manager.check_language_requirements(
            original_non_string,
            "TEST",
            ["English"],
            check_audio=True,
            original_language=True,
        )
    )

    failure = _meta(
        tmp_path,
        uuid="or-failure",
        language_checked=True,
        audio_languages=["French"],
        subtitle_languages=[],
    )
    manager.prompt_user_for_confirmation = AsyncMock(return_value=True)  # type: ignore[method-assign]
    assert asyncio.run(
        manager.check_language_requirements(
            failure,
            "TEST",
            ["English"],
            check_audio=True,
            prompt_on_failure=True,
        )
    )

    assert "!!!" in manager.portuguese_title_capitalization("!!! palavra")
