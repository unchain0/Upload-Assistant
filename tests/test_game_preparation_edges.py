from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import TracebackType
from typing import Any, ClassVar, Self
from unittest.mock import AsyncMock

import pytest

from src.domain_models.processing import ItemProcessingError
from src.domain_models.release import Meta
from src.services import game_preparation as game


def test_version_title_group_and_required_field_edges() -> None:
    assert game.normalize_version("  ") == ""
    assert game.normalize_version("V1.2") == "v1.2"
    assert game.normalize_version("1.2") == "v1.2"
    assert game.normalize_version("alpha") == "alpha"

    assert game.clean_game_title("title_name") == "Title Name"
    assert game.clean_game_title("Game-RELEASE") == "Game"
    assert game.clean_game_title("Game-Long Group") == "Game Long Group"
    assert game.clean_game_title("Game 1.2 incl keygen extra") == "Game"
    assert game.clean_game_title("Game.Update.3") == "Game"
    assert game.clean_game_title("Game.v1.2.EXTRA") == "Game"
    assert game.clean_game_title("Game.20260101") == "Game"
    assert game.clean_game_title("Game.enUS") == "Game"
    assert game.clean_game_title("") == ""

    assert game.extract_release_group("Game[GROUP].iso") == "GROUP"
    assert game.extract_release_group("Game-GROUP.iso") == "GROUP"
    assert game.extract_release_group("Game.iso") == ""

    normal = Meta(title="", year=0, platform="")
    assert game.required_game_fields(normal) == ["title", "year", "platform"]
    assert game.missing_game_fields(normal) == ["title", "year", "platform"]
    software = Meta(software=True, title="Tool", platform="PC")
    assert game.required_game_fields(software) == ["title", "platform"]
    assert game.missing_game_fields(software) == [
        "game_version",
        "developer",
        "publisher",
        "cover",
        "languages",
        "overview",
        "installation instructions",
    ]


def test_desktop_installer_detection() -> None:
    assert not game._is_desktop_installer(Meta(console_game=True, platform="PC", filelist=["setup.exe"]))
    assert not game._is_desktop_installer(Meta(platform="SWITCH", filelist=["setup.exe"]))
    assert game._is_desktop_installer(Meta(platform="PC", filelist=["setup.exe"]))
    assert game._is_desktop_installer(Meta(platform="MAC", path="setup.dmg"))
    assert not game._is_desktop_installer(Meta(platform="LINUX", filelist=["README.txt"]))


def test_read_software_notes_text_nfo_limits_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ignored = tmp_path / "image.png"
    ignored.write_bytes(b"x")
    large = tmp_path / "large.txt"
    large.write_bytes(b"x" * (64 * 1024 + 1))
    empty = tmp_path / "empty.md"
    empty.write_text("  ", encoding="utf-8")
    text = tmp_path / "install.txt"
    text.write_text("Install this tool", encoding="utf-8")
    meta = Meta(filelist=[str(ignored), str(large), str(empty), str(text)])
    assert asyncio.run(game._read_software_notes(meta)) == "Install this tool"

    nfo = tmp_path / "scene.nfo"
    nfo.write_text(
        "1. Decorative line\n│ 2. Extract archive │\n3. Run setup.exe\t|\n4. Copy crack ║\n5. Nothing useful",
        encoding="utf-8",
    )
    assert asyncio.run(game._read_software_notes(Meta(filelist=[str(nfo)]))) == ("2. Extract archive\n3. Run setup.exe\n4. Copy crack")

    original_stat = Path.stat

    def bad_stat(path: Path, *args: object, **kwargs: object):
        if path == text:
            raise OSError("denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", bad_stat)
    assert asyncio.run(game._read_software_notes(Meta(filelist=[str(text)]))) == ""


def test_version_extraction_text_and_nfo(tmp_path: Path) -> None:
    assert game.extract_version_from_text("") is None
    assert game.extract_version_from_text("Update: v2.3") == "v2.3"
    assert game.extract_version_from_text("Game v4.5") == "v4.5"
    assert game.extract_version_from_text("Game 2020 1080") is None
    assert game.extract_version_from_text("Game 3.4") == "v3.4"
    assert game.extract_version_from_text("Game") is None

    strong = tmp_path / "strong.nfo"
    strong.write_text("Build 5.6", encoding="utf-8")
    assert game.extract_version_from_nfo(str(strong)) == "v5.6"
    prefixed = tmp_path / "prefixed.nfo"
    prefixed.write_text("Game v7.8", encoding="utf-8")
    assert game.extract_version_from_nfo(str(prefixed)) == "v7.8"
    contextual = tmp_path / "context.nfo"
    contextual.write_text("release version text 9.10", encoding="utf-8")
    assert game.extract_version_from_nfo(str(contextual)) == "v9.10"
    filtered = tmp_path / "filtered.nfo"
    filtered.write_text("release 2020\nresolution 1080", encoding="utf-8")
    assert game.extract_version_from_nfo(str(filtered)) is None
    assert game.extract_version_from_nfo(str(tmp_path / "missing.nfo")) is None

    latin = tmp_path / "latin.nfo"
    latin.write_bytes("Versão Build 11.2".encode("latin-1"))
    assert game.extract_version_from_nfo(str(latin)) == "v11.2"


def test_platform_mapping_and_7z_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert game.map_to_clean_code("PlayStation 5") == "PS5"
    assert game.map_to_clean_code("Xbox Series X|S") == "XSX"
    assert game.map_to_clean_code("Unknown Console") == "UNKNOWN CONSOLE"

    configured = tmp_path / "7z"
    configured.write_bytes(b"binary")
    assert asyncio.run(game.get_7z_path(str(tmp_path), {"DEFAULT": {"7z_path": str(configured)}})) == str(configured)

    monkeypatch.setattr(game.shutil, "which", lambda name: "/usr/bin/7z" if name == "7z" else None)
    assert asyncio.run(game.get_7z_path(str(tmp_path), {})) == "/usr/bin/7z"

    monkeypatch.setattr(game.shutil, "which", lambda _name: None)
    managed = tmp_path / "managed7z"
    managed.write_bytes(b"binary")
    from src.integrations.runtime_tools import seven_zip

    monkeypatch.setattr(seven_zip.SevenZipBinaryManager, "ensure_7z_binary", AsyncMock(return_value=str(managed)))
    assert asyncio.run(game.get_7z_path(str(tmp_path), {})) == str(managed)
    monkeypatch.setattr(seven_zip.SevenZipBinaryManager, "ensure_7z_binary", AsyncMock(side_effect=RuntimeError("failed")))
    assert asyncio.run(game.get_7z_path(str(tmp_path), {})) is None


class _Stream:
    def __init__(self, value: bytes) -> None:
        self.value = value


class _Process:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self.stdout_value = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout_value, b""


def test_archive_listing_success_failure_and_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    output = b"Path = game.zip\nPath = setup.exe\nPath = folder/file.dll\nPath = \n"
    monkeypatch.setattr(game.asyncio, "create_subprocess_exec", AsyncMock(return_value=_Process(output)))
    assert asyncio.run(game.list_archive_contents_with_7z("game.zip", "7z")) == ["setup.exe", "folder/file.dll"]
    monkeypatch.setattr(game.asyncio, "create_subprocess_exec", AsyncMock(return_value=_Process(output, 1)))
    assert asyncio.run(game.list_archive_contents_with_7z("game.zip", "7z")) == []
    monkeypatch.setattr(game.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("missing")))
    assert asyncio.run(game.list_archive_contents_with_7z("game.zip", "7z")) == []


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["game.nsp"], "SWITCH"),
        (["game.3ds"], "3DS"),
        (["game.nds"], "NDS"),
        (["game.wud"], "WIIU"),
        (["root/code/app.xml"], "WIIU"),
        (["title.tmd", "title.tik"], "WIIU"),
        (["game.wbfs"], "WII"),
        (["PS3_GAME/PARAM.SFO"], "PS3"),
        (["license.rap"], "PS3"),
        (["game.vpk"], "PSVITA"),
        (["game.cso"], "PSP"),
        (["EBOOT.PBP"], "PSP"),
        (["Game-CUSA00001.pkg"], "PS4"),
        (["Game-PPSA00001.pkg"], "PS5"),
        (["folder/PS4/Game.pkg"], "PS4"),
        (["folder/PS5/Game.pkg"], "PS5"),
        (["folder/PS3/Game.pkg"], "PS3"),
        (["NPUB12345.pkg"], "PS3"),
        (["generic.pkg"], "MAC"),
        (["default.xex"], "X360"),
        (["folder/$SystemUpdate/file"], "X360"),
        (["default.xbe"], "XBOX"),
        (["game.gdi"], "DREAMCAST"),
        (["steam_api64.dll"], "PC"),
        (["Game/Binaries/Win64/game.bin"], "PC"),
        (["setup.exe"], "PC"),
        (["installer.dmg"], "MAC"),
    ],
)
def test_detect_platform_extensions(files: list[str], expected: str) -> None:
    assert asyncio.run(game.detect_platform_from_files(files)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Game.PS5.Release", "PS5"),
        ("Game.PlayStation.4", "PS4"),
        ("Game.PS3", "PS3"),
        ("Game.PS2", "PS2"),
        ("Game.PSX", "PS1"),
        ("Game.XSX", "XSX"),
        ("Game.Xbox.One", "XONE"),
        ("Game.Xbox.360", "X360"),
        ("Game.Xbox", "XBOX"),
        ("Game.NSW", "SWITCH"),
        ("Game.3DS", "3DS"),
        ("Game.NDS", "NDS"),
        ("Game.Wii.U", "WIIU"),
        ("Game.Wii", "WII"),
        ("Game.Windows", "PC"),
        ("Game.MacOS", "MAC"),
        ("Game.Linux", "LINUX"),
        ("Game.PSP", "PSP"),
        ("Game.PSVita", "PSVITA"),
    ],
)
def test_detect_platform_keywords(name: str, expected: str) -> None:
    assert asyncio.run(game.detect_platform_from_files([], name)) == expected


def test_detect_platform_archive_content_and_7zr_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    archive = tmp_path / "game.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(game, "get_7z_path", AsyncMock(return_value="/usr/bin/7z"))
    monkeypatch.setattr(game, "list_archive_contents_with_7z", AsyncMock(return_value=["steam_api.dll"]))
    assert asyncio.run(game.detect_platform_from_files([str(archive)], base_dir=str(tmp_path))) == "PC"

    monkeypatch.setattr(game, "get_7z_path", AsyncMock(return_value="/usr/bin/7zr"))
    inspect = AsyncMock(return_value=["steam_api.dll"])
    monkeypatch.setattr(game, "list_archive_contents_with_7z", inspect)
    assert asyncio.run(game.detect_platform_from_files([str(archive)], base_dir=str(tmp_path))) is None
    inspect.assert_not_awaited()

    seven = tmp_path / "game.7z"
    seven.write_bytes(b"archive")
    assert asyncio.run(game.detect_platform_from_files([str(seven)], base_dir=str(tmp_path))) == "PC"


def test_resolve_game_filelist_priorities_empty_and_single_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ItemProcessingError):
        game.resolve_game_filelist(Meta(), str(empty))

    for label, files, expected in (
        ("exe", {"small.exe": 1, "large.exe": 3, "game.iso": 5}, "large.exe"),
        ("iso", {"small.iso": 1, "large.iso": 3}, "large.iso"),
        ("archive", {"small.zip": 1, "large.rar": 3}, "large.rar"),
        ("fallback", {"small.bin": 1, "large.bin": 3}, "large.bin"),
    ):
        root = tmp_path / label
        root.mkdir()
        for name, size in files.items():
            (root / name).write_bytes(b"x" * size)
        meta = Meta(imdb_id=123)
        selected, filelist, search, kind = game.resolve_game_filelist(meta, str(root))
        assert Path(selected).name == expected
        assert filelist[0] == selected
        assert search == expected and kind == "file" and meta.imdb_id == 0

    single = tmp_path / "single.iso"
    single.write_bytes(b"iso")
    selected, filelist, search, kind = game.resolve_game_filelist(Meta(), str(single))
    assert selected == str(single) and filelist == [str(single)] and search == "single.iso" and kind == "file"


class _IGDB:
    search_results: ClassVar[list[dict[str, Any]]] = []
    id_result: ClassVar[dict[str, Any] | None] = None
    steam_result: ClassVar[dict[str, Any] | None] = None
    searches: ClassVar[list[str]] = []
    cached: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    @classmethod
    def reset(cls) -> None:
        cls.search_results = []
        cls.id_result = None
        cls.steam_result = None
        cls.searches = []
        cls.cached = []

    async def search_game(self, query: str) -> list[dict[str, Any]]:
        self.searches.append(query)
        return list(self.search_results)

    async def fetch_game_by_id(self, _identifier: object) -> dict[str, Any] | None:
        return self.id_result

    async def fetch_game_by_steam_id(self, _identifier: object) -> dict[str, Any] | None:
        return self.steam_result

    async def cache_game_details(self, selected: dict[str, Any]) -> None:
        self.cached.append(selected)


class _Cache:
    def __init__(self, value: object) -> None:
        self.value = value
        self.sets: list[tuple[object, ...]] = []

    async def get(self, *_args: object) -> object:
        return self.value

    async def set(self, *args: object, **_kwargs: object) -> None:
        self.sets.append(args)


class _Response:
    status_code = 200
    payload: ClassVar[object] = {}

    def json(self) -> object:
        return self.payload


class _Client:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, _exc_type: type[BaseException] | None, _exc: BaseException | None, _tb: TracebackType | None) -> None:
        return None

    async def get(self, *_args: object, **_kwargs: object) -> _Response:
        return _Response()


@pytest.fixture(autouse=True)
def _reset_igdb(monkeypatch: pytest.MonkeyPatch) -> None:
    _IGDB.reset()
    monkeypatch.setattr(game, "IGDBAPI", _IGDB)


def _game_meta(tmp_path: Path, name: str = "Example.Game.v1.2-GROUP.iso", **values: object) -> Meta:
    path = tmp_path / name
    path.write_bytes(b"game")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "game",
        "path": str(path),
        "filename": path.name,
        "filelist": [str(path)],
        "trackers": [],
        "unattended": True,
    }
    state.update(values)
    return Meta(state)


def _selected_game() -> dict[str, Any]:
    return {
        "id": 42,
        "name": "Selected Game",
        "first_release_date": 1_704_067_200,
        "rating": 88.84,
        "rating_count": 123,
        "summary": "Summary",
        "storyline": "Story",
        "cover": {"url": "//images.igdb.com/t_thumb/cover.jpg"},
        "genres": [{"name": "Action"}, {}],
        "platforms": [{"name": "PC (Microsoft Windows)"}, {"name": "Linux"}],
        "involved_companies": [
            {"company": {"name": "Developer"}, "developer": True},
            {"company": {"name": "Publisher"}, "publisher": True},
        ],
        "websites": [{"type": 13, "url": "https://store.steampowered.com/app/1234/Game"}],
        "external_games": [],
        "language_supports": [
            {"language": {"name": "English"}, "language_support_type": {"name": "Audio"}},
            {"language": {"name": "English"}, "language_support_type": {"name": "Subtitles"}},
            {"language": {}, "language_support_type": {}},
        ],
        "screenshots": [{"url": "//images.igdb.com/t_thumb/screen.jpg"}, {}],
    }


def test_gather_game_prep_credentials_title_and_no_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    no_credentials = _game_meta(tmp_path)
    asyncio.run(game.gather_game_prep(no_credentials, no_credentials.path, str(tmp_path), {"DEFAULT": {}}))
    assert no_credentials.category == "GAME" and no_credentials.game_version == "v1.2"

    monkeypatch.setenv("TWITCH_CLIENT_ID", "client")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "secret")
    software = _game_meta(tmp_path, "Tool.v2.0-GROUP.exe", filelist=[])
    software.filelist = [software.path]
    asyncio.run(game.gather_game_prep(software, software.path, str(tmp_path), {"DEFAULT": {}}))
    assert software.software is True

    empty = _game_meta(tmp_path, "---.iso")
    empty.path = ""
    empty.filename = ""
    empty.title = ""
    asyncio.run(game.gather_game_prep(empty, "", str(tmp_path), {"DEFAULT": {}}))


def test_gather_game_prep_manual_ids_nfo_steam_and_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    nfo = tmp_path / "scene.nfo"
    nfo.write_text("Build 3.4\nhttps://store.steampowered.com/app/1234/Game", encoding="utf-8")

    _IGDB.id_result = None
    _IGDB.search_results = [_selected_game()]
    manual = _game_meta(tmp_path, igdb_manual=99, filelist=[str(nfo)], platform="PC", manual_platform="PC", game_version="5.6")
    asyncio.run(game.gather_game_prep(manual, manual.path, str(tmp_path), config))
    assert manual.game_version == "v5.6" and manual.title == "Selected Game"

    _IGDB.reset()
    _IGDB.steam_result = _selected_game()
    steam = _game_meta(tmp_path, filelist=[str(nfo)], debug=True)
    asyncio.run(game.gather_game_prep(steam, steam.path, str(tmp_path), config))
    assert steam.steam_manual == "1234" and steam.igdb_id == 42

    _IGDB.reset()
    _IGDB.search_results = [_selected_game(), {**_selected_game(), "id": 43, "name": "Other"}]
    choices = iter(("Skip - Don't select any match",))
    monkeypatch.setattr(game.cli_ui, "ask_choice", lambda *_args, **_kwargs: next(choices))
    interactive = _game_meta(tmp_path, unattended=False)
    asyncio.run(game.gather_game_prep(interactive, interactive.path, str(tmp_path), config))
    assert interactive.igdb_id == 0

    monkeypatch.setattr(game.cli_ui, "ask_choice", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    cancelled = _game_meta(tmp_path, unattended=False)
    asyncio.run(game.gather_game_prep(cancelled, cancelled.path, str(tmp_path), config))


def test_gather_game_prep_full_metadata_steam_cache_and_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    selected = _selected_game()
    _IGDB.search_results = [selected]
    cache = _Cache(object())
    monkeypatch.setattr(game, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(game, "is_cache_miss", lambda value: value is cache.value)
    _Response.payload = {
        "1234": {
            "success": True,
            "data": {
                "short_description": "<b>Descrição &amp; texto</b>",
                "pc_requirements": {"minimum": "Minimum", "recommended": "Recommended"},
            },
        }
    }
    monkeypatch.setattr(game.httpx, "AsyncClient", _Client)
    meta = _game_meta(tmp_path, trackers=["AMIGOSSHARE"], platform="PC")

    asyncio.run(game.gather_game_prep(meta, meta.path, str(tmp_path), config))

    assert meta.title == "Selected Game"
    assert meta.year == 2024 and meta.search_year == 2024
    assert meta.igdb_first_release_date == "01/01/2024"
    assert meta.igdb_rating == 88.8 and meta.igdb_rating_count == 123
    assert meta.overview == "Summary"
    assert meta.artwork_url.startswith("https:") and "t_cover_big" in meta.artwork_url
    assert meta.genres == ["Action"] and meta.keywords == ["Action"]
    assert meta.platform == "PC"
    assert meta.developer == "Developer" and meta.publisher == "Publisher"
    assert meta.steam_url.endswith("/1234/Game")
    assert meta.languages == {"English": ["Audio", "Subtitles"]}
    assert meta.available_platforms == ["PC (Microsoft Windows)", "Linux"]
    assert meta.localized_overviews == {"brazilian": "Descrição & texto"}
    assert meta.requirements_minimum == "Minimum" and meta.requirements_recommended == "Recommended"
    assert meta.image_list[0]["raw_url"].startswith("https:") and "t_1080p" in meta.image_list[0]["raw_url"]
    assert json.loads((tmp_path / "tmp" / "game" / "image_data.json").read_text())["image_list"] == meta.image_list
    assert meta.igdb_id == 42 and meta.console_game is False
    assert cache.sets


def test_gather_game_prep_external_game_platform_and_steam_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    selected = _selected_game()
    selected["websites"] = []
    selected["external_games"] = [{"external_game_source": 1, "uid": "999"}]
    selected["platforms"] = [{"name": "PlayStation 5"}, {"name": "PC (Microsoft Windows)"}]
    selected["summary"] = ""
    selected["storyline"] = "Storyline"
    selected["cover"] = {"url": "https://image/t_thumb/cover"}
    _IGDB.search_results = [selected]

    cache = _Cache({})
    monkeypatch.setattr(game, "cache_for", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(game, "is_cache_miss", lambda _value: False)
    meta = _game_meta(tmp_path, "Selected.Game.PS5.iso")
    asyncio.run(game.gather_game_prep(meta, meta.path, str(tmp_path), config))
    assert meta.platform == "PS5" and meta.console_game is True
    assert meta.steam_url.endswith("/999") and meta.overview == "Storyline"

    _IGDB.reset()
    _IGDB.search_results = [{**selected, "platforms": [{"name": "Linux"}], "websites": [], "external_games": []}]
    no_platform = _game_meta(tmp_path, "Unknown.Game.bin")
    asyncio.run(game.gather_game_prep(no_platform, no_platform.path, str(tmp_path), config))
    assert no_platform.platform == "LINUX"

    _IGDB.reset()
    _IGDB.search_results = [{**selected, "platforms": [], "websites": [], "external_games": []}]
    missing_platform = _game_meta(tmp_path, "Unknown.Game.bin")
    asyncio.run(game.gather_game_prep(missing_platform, missing_platform.path, str(tmp_path), config))
    assert missing_platform.console_game is False

    _IGDB.reset()
    _IGDB.search_results = [selected]
    monkeypatch.setattr(game, "cache_for", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache failed")))
    failed = _game_meta(tmp_path)
    asyncio.run(game.gather_game_prep(failed, failed.path, str(tmp_path), config))


def test_gather_game_prep_steam_http_statuses_and_image_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    selected = _selected_game()
    _IGDB.search_results = [selected]
    monkeypatch.setattr(game.httpx, "AsyncClient", _Client)

    for status, payload in ((404, {}), (500, {}), (200, [])):
        _Response.status_code = status
        _Response.payload = payload
        cache = _Cache(object())
        monkeypatch.setattr(game, "cache_for", lambda *_args, _cache=cache, **_kwargs: _cache)
        monkeypatch.setattr(game, "is_cache_miss", lambda value, _cache=cache: value is _cache.value)
        meta = _game_meta(tmp_path, f"Game-{status}.iso", uuid=f"game-{status}")
        asyncio.run(game.gather_game_prep(meta, meta.path, str(tmp_path), config))

    _Response.status_code = 200
    _Response.payload = {}
    _IGDB.reset()
    _IGDB.search_results = [selected]
    original_open = game.aiofiles.open

    def failing_open(path: object, *args: object, **kwargs: object):
        if str(path).endswith("image_data.json"):
            raise OSError("read only")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(game.aiofiles, "open", failing_open)
    meta = _game_meta(tmp_path, "Image.Write.Error.iso", uuid="game-error")
    asyncio.run(game.gather_game_prep(meta, meta.path, str(tmp_path), config))


def test_gather_game_prep_directory_and_discovered_nfo_version(tmp_path: Path) -> None:
    config = {"DEFAULT": {}}
    release = tmp_path / "Game.v12.3"
    release.mkdir()
    media = release / "game.bin"
    media.write_bytes(b"game")
    meta = Meta(path=str(media), filelist=[str(media)], debug=True)
    asyncio.run(game.gather_game_prep(meta, str(media), str(tmp_path), config))
    assert meta.game_version == "v12.3"

    release = tmp_path / "GameNoVersion"
    release.mkdir()
    media = release / "game.bin"
    media.write_bytes(b"game")
    nfo = release / "release.nfo"
    nfo.write_text("Build 14.5", encoding="utf-8")
    meta = Meta(path=str(media), filelist=[str(media)])
    asyncio.run(game.gather_game_prep(meta, str(media), str(tmp_path), config))
    assert meta.game_version == "v14.5"


def test_gather_game_prep_latin_nfo_error_and_platform_sorting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    release = tmp_path / "LatinGame"
    release.mkdir()
    media = release / "game.bin"
    media.write_bytes(b"game")
    latin = release / "latin.nfo"
    latin.write_bytes("Steam https://store.steampowered.com/app/777/Gáme".encode("latin-1"))
    _IGDB.steam_result = _selected_game()
    meta = Meta(path=str(media), filelist=[str(latin)], platform="PC", manual_platform="PC")
    asyncio.run(game.gather_game_prep(meta, str(media), str(tmp_path), config))
    assert meta.steam_manual == "777"

    _IGDB.reset()
    matching = {**_selected_game(), "id": 1, "name": "Matching", "platforms": [{"name": "PC (Microsoft Windows)"}]}
    other = {**_selected_game(), "id": 2, "name": "Other", "platforms": [{"name": "Linux"}]}
    _IGDB.search_results = [other, matching]
    sorted_meta = _game_meta(tmp_path, "Sorted.Game.bin", platform="PC", manual_platform="PC")
    asyncio.run(game.gather_game_prep(sorted_meta, sorted_meta.path, str(tmp_path), config))
    assert sorted_meta.title == "Matching"

    _IGDB.reset()
    _IGDB.search_results = [matching, other]
    monkeypatch.setattr(game.cli_ui, "ask_choice", lambda _message, choices, **_kwargs: choices[1])
    chosen = _game_meta(tmp_path, "Chosen.Game.bin", unattended=False, platform="PC", manual_platform="PC")
    asyncio.run(game.gather_game_prep(chosen, chosen.path, str(tmp_path), config))
    assert chosen.title == "Other"


def test_gather_game_prep_nfo_outer_error_and_platform_alias_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"DEFAULT": {"twitch_client_id": "client", "twitch_client_secret": "secret"}}
    nfo = tmp_path / "broken.nfo"
    nfo.write_text("nothing", encoding="utf-8")
    _IGDB.search_results = [_selected_game()]

    original_open = game.aiofiles.open

    def fail_nfo(path: object, *args: object, **kwargs: object):
        if Path(str(path)) == nfo:
            raise OSError("denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(game.aiofiles, "open", fail_nfo)
    meta = _game_meta(tmp_path, "Broken.NFO.Game.bin", filelist=[str(nfo)])
    asyncio.run(game.gather_game_prep(meta, meta.path, str(tmp_path), config))
    assert meta.title == "Selected Game"

    _IGDB.reset()
    selected = _selected_game()
    selected["platforms"] = [{"name": "PlayStation 5"}, {"name": "Linux"}]
    selected["websites"] = []
    selected["external_games"] = []
    _IGDB.search_results = [selected]
    monkeypatch.setattr(game, "detect_platform_from_files", AsyncMock(return_value=None))
    alias = _game_meta(tmp_path, "Alias.Game.PS5.bin", platform="")
    asyncio.run(game.gather_game_prep(alias, alias.path, str(tmp_path), config))
    assert alias.platform == "PS5"
