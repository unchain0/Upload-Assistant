"""Direct tests for upstream CLI behaviors that were previously only named."""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from src.domain_models.release import Meta
from src.domain_models.tracker_catalog import KNOWN_TRACKERS
from src.domain_models.video_title_builder import (
    BLU_NAME_RULES,
    build_name_components,
    build_video_name,
    with_imdb_title,
)
from src.engines.region_mapping import get_region
from src.integrations.external_apis.bluray import map_country_to_region_code
from src.integrations.image_hosts.uploader import (
    SAMARITANO_SHAREX_URL,
    THRIMG_UPLOAD_URL,
    _upload_samaritano_host,
    _upload_thrimg_host,
)
from src.integrations.media.scene_detection import (
    SceneFileMismatchError,
    SceneManager,
)
from src.integrations.media.screenshot_capture import (
    _VideoCapturePlan,
    _capture_task_arguments,
    _missing_screenshot_indices,
    _png_is_complete,
)
from src.integrations.trackers.UNIT3D.capybarabr import CapybaraBR
from src.integrations.trackers.UNIT3D.homiehelpdesk import HomieHelpDesk
from src.integrations.trackers.UNIT3D.onlyencodes import OnlyEncodes
from src.integrations.trackers.beyondhd import BEYONDHD
from src.integrations.trackers.registry import tracker_class_map
from src.services.config_sync import sync_user_config


def test_component_title_uses_imdb_fields_and_drops_edition_words() -> None:
    meta = Meta(
        category="MOVIE",
        title="Local",
        year=2020,
        type="ENCODE",
        source="BluRay",
        resolution="1080p",
        audio="DTS",
        video_encode="x265",
        uhd="UHD",
        edition="Remastered Director's Cut",
        tag="-GROUP",
        imdb_info={"title": "Canonical", "aka": "Alternate", "year": "2021"},
    )
    components = with_imdb_title(meta, build_name_components(meta))
    name = build_video_name(meta, BLU_NAME_RULES, components)

    assert name.startswith("Canonical AKA Alternate 2021")
    assert "Director's Cut" in name
    assert "Remastered" not in name
    assert "UHD" in name
    assert name.endswith("-GROUP")
    assert "Local" not in name


def test_onlyencodes_name_comes_from_components() -> None:
    tracker = OnlyEncodes({"DEFAULT": {}, "TRACKERS": {"ONLYENCODES": {}}})
    meta = Meta(
        category="MOVIE",
        title="Movie",
        year=2024,
        type="WEBDL",
        source="WEB",
        resolution="1080p",
        audio="AAC 2.0",
        video_encode="H264",
        service="AMZN",
        tag="-GROUP",
        audio_languages=["French"],
        imdb_info={},
    )

    name = asyncio.run(tracker.get_name(meta))["name"]

    assert name.startswith("Movie 2024 FRENCH")
    assert "1080p" in name
    assert "WEB-DL" in name
    assert "AMZN" in name
    assert name.endswith("-GROUP")


def test_incomplete_png_is_recaptured_at_its_own_index(tmp_path: Path) -> None:
    good = tmp_path / "Show-1.png"
    bad = tmp_path / "Show-0.png"
    Image.new("RGB", (2, 2), "red").save(good)
    bad.write_bytes(b"\x89PNG\r\nnot-a-png")

    assert _png_is_complete(good) is True
    assert _png_is_complete(bad) is False
    assert _missing_screenshot_indices(tmp_path, "Show", 2, False) == [0]


def test_capture_tasks_use_missing_indices_not_a_prefix(tmp_path: Path) -> None:
    meta = Meta(category="MOVIE", retake=False)
    plan = _VideoCapturePlan(
        path="video.mkv",
        folder_id="folder",
        base_dir=str(tmp_path),
        meta=meta,
        img_host=None,
        start_time=0.0,
        group="main",
        requested_screens=2,
        registered_screens=[],
        manual_frames=[],
        force_screenshots=False,
        cleanup_after_capture=False,
        length=10.0,
        width=16.0,
        height=9.0,
        w_sar=1.0,
        h_sar=1.0,
        frame_rate=24.0,
        loglevel="quiet",
        screenshot_dir=tmp_path,
        sanitized_filename="Show",
        ss_times=["1.5", "8.0"],
        num_capture=1,
        existing_images_count=1,
        capture_indices=[0],
    )

    tasks = _capture_task_arguments(plan, False)

    assert len(tasks) == 1
    assert tasks[0][0] == 0
    assert tasks[0][2] == 1.5
    assert tasks[0][3].endswith("Show-0.png")


def test_hhd_sends_audiobook_and_ebook_fields() -> None:
    tracker = HomieHelpDesk({"DEFAULT": {}, "TRACKERS": {"HOMIEHELPDESK": {}}})
    audiobook = Meta(
        category="BOOK",
        audiobook=True,
        title="Story",
        asin="B00TEST123",
        author="Ada",
        narrator="Neil",
    )
    audiobook.audible_url = "https://www.audible.co.uk/pd/Story"
    ebook = Meta(category="BOOK", isbn="978-0-306-40615-7")

    audio = asyncio.run(tracker.get_additional_data(audiobook))
    book = asyncio.run(tracker.get_additional_data(ebook))

    assert audio["audiobook_asin"] == "B00TEST123"
    assert audio["audiobook_region"] == "uk"
    assert audio["audiobook_title"] == "Story"
    assert audio["audiobook_authors"] == "Ada"
    assert audio["audiobook_narrators"] == "Neil"
    assert book == {"isbn": "9780306406157"}


def test_samaritano_posts_to_the_sharex_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    calls: list[str] = []

    class Response:
        status_code = 200
        text = '{"link": "https://img.samaritano.cc/shot.png"}'

        def json(self) -> dict[str, str]:
            return {"link": "https://img.samaritano.cc/shot.png"}

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> Response:
            calls.append(url)
            return Response()

    monkeypatch.setattr(
        "src.integrations.image_hosts.uploader.httpx.AsyncClient", Client
    )
    result = asyncio.run(
        _upload_samaritano_host(
            str(image),
            {"TRACKERS": {"SAMARITANO": {"image_host_api_key": "token"}}},
            Meta(),
            5.0,
        )
    )

    assert calls == [SAMARITANO_SHAREX_URL]
    assert result["img_url"] == "https://img.samaritano.cc/shot.png"


def test_thrimg_posts_to_torrenthr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"png")
    calls: list[str] = []

    class Response:
        status_code = 200
        text = ""

        def json(self) -> dict[str, dict[str, str]]:
            return {"image": {"url": "https://slike.torrenthr.org/shot.png"}}

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: object) -> Response:
            calls.append(url)
            return Response()

    monkeypatch.setattr(
        "src.integrations.image_hosts.uploader.httpx.AsyncClient", Client
    )
    result = asyncio.run(
        _upload_thrimg_host(
            str(image), {"DEFAULT": {"thrimg_api": "token"}}, Meta(), 5.0
        )
    )

    assert calls == [THRIMG_UPLOAD_URL]
    assert result["img_url"] == "https://slike.torrenthr.org/shot.png"


def test_beyondhd_and_onlyencodes_approve_the_new_hosts() -> None:
    assert "passtheimage" in BEYONDHD.approved_image_hosts
    assert (
        BEYONDHD.image_host_policy.url_host_mapping["passtheima.ge"]
        == "passtheimage"
    )
    assert "lostimg" in OnlyEncodes.approved_image_hosts
    assert (
        OnlyEncodes.image_host_policy.url_host_mapping["lostimg.cc"]
        == "lostimg"
    )


def test_bluray_region_codes_cover_the_added_countries() -> None:
    assert map_country_to_region_code("Bulgaria") == "BGR"
    assert map_country_to_region_code("Finland") == "FIN"
    assert map_country_to_region_code("Taiwan") == "TWN"
    assert asyncio.run(get_region({"label": "Movie BGR 1080p"})) == "BGR"
    assert asyncio.run(get_region({"label": "Movie SGP 1080p"})) == "SGP"


def test_capybara_manual_multi_overrides_a_single_language() -> None:
    meta = Meta(
        category="GAME",
        manual_multi=True,
        languages=["ENGLISH"],
        language="ENGLISH",
    )

    assert CapybaraBR._game_language_tag(meta) == "[MULTI]"


def _scene_meta(tmp_path: Path, video: Path, **values: object) -> Meta:
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "uuid": "scene-match",
        "category": "MOVIE",
        "path": str(video),
        "filename": video.name,
        "nfo": True,
        "unattended": True,
        "unattended_confirm": False,
        "scene": False,
    }
    state.update(values)
    return Meta(state)


@dataclass(frozen=True)
class _ArchiveCase:
    name: str
    filename: str
    kind: str
    release: str
    details: dict[str, object] | None
    local_size: int
    difference: str
    raises: bool
    decoy_size: int = 0


_ARCHIVE_CASES = [
    _ArchiveCase(
        "size-mismatch",
        "Show.mkv",
        "file",
        "Show",
        {"archived-files": [{"name": "Show.mkv", "size": "9"}]},
        4,
        "Show.mkv size differs from archived 9 bytes",
        True,
        9,
    ),
    _ArchiveCase(
        "matching-size",
        "Show.mkv",
        "file",
        "Show",
        {"archived-files": [{"name": "Show.mkv", "size": "9"}]},
        9,
        "",
        False,
    ),
    _ArchiveCase(
        "bdmv-directory",
        "Show",
        "dir",
        "Show",
        {
            "archived-files": [
                {"name": "Show.mkv", "size": "4"},
                {"name": "Show.nfo", "size": "1"},
            ]
        },
        0,
        "",
        False,
    ),
    _ArchiveCase(
        "duplicate-basename",
        "Show.mkv",
        "file",
        "Show",
        {
            "archived-files": [
                {"name": "Show.mkv", "size": "4"},
                {"name": "Show.mkv", "size": "4"},
            ]
        },
        4,
        "",
        False,
    ),
    _ArchiveCase(
        "not-listed",
        "Show.mkv",
        "file",
        "Release",
        {"archived-files": [{"name": "Other.mkv", "size": "4"}]},
        4,
        "Show.mkv is not listed in Release",
        True,
    ),
    _ArchiveCase(
        "empty-archive",
        "Show.mkv",
        "file",
        "Show",
        {"archived-files": []},
        4,
        "",
        False,
    ),
    _ArchiveCase(
        "no-details",
        "Show.mkv",
        "file",
        "Show",
        None,
        4,
        "",
        False,
    ),
    _ArchiveCase(
        "rar-size-from-files",
        "Show.rar",
        "file",
        "Show",
        {
            "files": [{"name": "Show.rar", "size": "9"}],
            "archived-files": [{"name": "Show.rar", "size": "4"}],
        },
        4,
        "Show.rar size differs from archived 9 bytes",
        True,
    ),
    _ArchiveCase(
        "case-rename",
        "Show.mkv",
        "file",
        "Show",
        {"archived-files": [{"name": "show.mkv", "size": "4"}]},
        4,
        "Show.mkv was archived as show.mkv",
        True,
    ),
    _ArchiveCase(
        "missing-size",
        "Show.mkv",
        "file",
        "Show",
        {"archived-files": [{"name": "Show.mkv"}]},
        4,
        "",
        False,
    ),
]


def _materialize_archive_case(tmp_path: Path, case: _ArchiveCase) -> Path:
    path = tmp_path / case.filename
    if case.kind == "dir":
        path.mkdir()
        return path
    path.write_bytes(b"x" * case.local_size)
    return path


def _scene_filelist(tmp_path: Path, case: _ArchiveCase) -> list[str]:
    if case.decoy_size <= 0:
        return []
    decoy = tmp_path / "other" / case.filename
    decoy.parent.mkdir()
    decoy.write_bytes(b"y" * case.decoy_size)
    return [str(decoy)]


def _run_unattended_scene(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    case: _ArchiveCase,
) -> None:
    meta = _scene_meta(
        tmp_path, path, filelist=_scene_filelist(tmp_path, case)
    )
    manager = SceneManager({"DEFAULT": {}})

    async def search_srrdb(
        *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        return {
            "resultsCount": 1,
            "results": [{"release": case.release, "hasNFO": "no"}],
        }

    async def release_details(*_args: object, **_kwargs: object) -> object:
        return case.details

    monkeypatch.setattr(manager, "_search_srrdb", search_srrdb)
    monkeypatch.setattr(manager, "_release_details", release_details)
    if case.raises:
        with pytest.raises(
            SceneFileMismatchError, match=re.escape(case.difference)
        ):
            asyncio.run(manager.is_scene(str(path), meta))
        return
    renamed, scene, _imdb = asyncio.run(manager.is_scene(str(path), meta))
    assert scene is True
    assert renamed == f"{case.release}.mkv"


@pytest.mark.parametrize("case", _ARCHIVE_CASES, ids=lambda case: case.name)
def test_srrdb_archived_file_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ArchiveCase
) -> None:
    path = _materialize_archive_case(tmp_path, case)
    difference = SceneManager.archived_file_difference(
        path, case.release, case.details
    )

    assert difference == case.difference
    _run_unattended_scene(tmp_path, monkeypatch, path, case)


def test_config_sync_adds_missing_keys_without_replacing_values(tmp_path: Path) -> None:
    example = tmp_path / "example.py"
    user = tmp_path / "config.py"
    example.write_text(
        'config = {"DEFAULT": {"screens": 6, "tone_map": False}, "TRACKERS": {}}\n',
        encoding="utf-8",
    )
    user.write_text(
        'config = {"DEFAULT": {"screens": 2}}\n',
        encoding="utf-8",
    )

    result = sync_user_config(user, example)
    synced = user.read_text(encoding="utf-8")

    assert result.updated is True
    assert "DEFAULT.tone_map" in result.added
    assert "'screens': 2" in synced
    assert "tone_map" in synced


def test_btn_series_matching_stays_out_of_the_cli_catalog() -> None:
    assert "BTN" not in KNOWN_TRACKERS
    assert "BTN" not in tracker_class_map
