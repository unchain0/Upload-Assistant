# ruff: noqa: S101
import asyncio
import threading

import pytest

from src.dupe_checking import DupeChecker
from src.meta import Meta
from src.trackers.UNIT3D import UNIT3D
from src.uphelper import DupeEntry, UploadHelper


@pytest.mark.asyncio
async def test_prompt_yes_no_serializes_concurrent_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def ask_yes_no(question: str, default: bool = False) -> bool:
        if question == "first":
            first_started.set()
            assert release_first.wait(timeout=1)
        else:
            second_started.set()
        return default

    monkeypatch.setattr("src.uphelper.cli_ui.ask_yes_no", ask_yes_no)
    helper = UploadHelper({"DEFAULT": {}})

    first = asyncio.create_task(helper.prompt_yes_no("first"))
    second = asyncio.create_task(helper.prompt_yes_no("second", default=True))

    assert await asyncio.to_thread(first_started.wait, 1)
    await asyncio.sleep(0)
    assert not second_started.is_set()

    release_first.set()
    assert await asyncio.gather(first, second) == [False, True]
    assert second_started.is_set()


@pytest.mark.asyncio
async def test_bdinfo_comparison_prompt_uses_rich_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    question: str | None = None

    async def prompt_yes_no(value: str, *, default: bool = False) -> bool:
        nonlocal question
        del default
        question = value
        return False

    monkeypatch.setattr("src.uphelper.has_bdinfo_content", lambda _entry: True)
    helper = UploadHelper({"DEFAULT": {}})
    monkeypatch.setattr(helper, "prompt_yes_no", prompt_yes_no)

    await helper.ask_bdinfo_comparison({}, [{}], "AITHER")

    assert question == "[bold magenta]Found BDInfo content in potential duplicates.[/bold magenta] Perform a comparison?"
    assert "\033" not in question


@pytest.mark.asyncio
async def test_dupe_check_rejects_episode_when_existing_season_pack_is_found() -> None:
    class SeasonPackTracker:
        async def get_name(self, meta: Meta) -> dict[str, str]:
            return {"name": meta.name}

    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {"DARKPEERS": lambda **_kwargs: SeasonPackTracker()}
    meta = Meta(category="TV", name="Yowayowa Sensei S01E01", season_pack_exists=True, season_pack_name="Yowayowa Sensei S01 1080p WEB-DL")
    dupes: list[DupeEntry | str] = [meta.season_pack_name]

    is_dupe, result_meta = await helper.dupe_check(dupes, meta, "DARKPEERS")

    assert is_dupe is True
    assert result_meta is meta


@pytest.mark.asyncio
async def test_dupe_check_honors_skip_dupe_check_for_existing_season_pack() -> None:
    class SeasonPackTracker:
        async def get_name(self, meta: Meta) -> dict[str, str]:
            return {"name": meta.name}

    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {"DARKPEERS": lambda **_kwargs: SeasonPackTracker()}
    meta = Meta(category="TV", name="Yowayowa Sensei S01E01", dupe=True, season_pack_exists=True, season_pack_name="Yowayowa Sensei S01 1080p WEB-DL")

    is_dupe, result_meta = await helper.dupe_check([meta.season_pack_name], meta, "DARKPEERS")

    assert is_dupe is False
    assert result_meta is meta


@pytest.mark.asyncio
async def test_dupe_check_rejects_existing_season_pack_for_every_tracker() -> None:
    class SeasonPackTracker:
        async def get_name(self, meta: Meta) -> dict[str, str]:
            return {"name": meta.name}

    helper = UploadHelper({"DEFAULT": {}})
    helper.tracker_class_map = {"OTHER": lambda **_kwargs: SeasonPackTracker()}
    meta = Meta(category="TV", name="Show S01E01", season_pack_exists=True, season_pack_name="Show S01")

    dupes: list[DupeEntry | str] = [meta.season_pack_name]
    is_dupe, _ = await helper.dupe_check(dupes, meta, "OTHER")

    assert is_dupe is True


@pytest.mark.asyncio
async def test_dupe_filter_detects_season_pack_before_quality_filters() -> None:
    meta = Meta(
        category="TV",
        name="Treasure & Dirt S01E06 1080p HDTV x264-DARKFLiX",
        uuid="Treasure.And.Dirt.S01E06.1080p.HDTV.H264-DARKFLiX",
        season="S01",
        episode="E06",
        resolution="1080p",
        source="HDTV",
        type="HDTV",
        filelist=["Treasure.And.Dirt.S01E06.mkv"],
    )
    season_pack: DupeEntry = {
        "name": "Treasure & Dirt S01 720p WEB-DL x265-GROUP",
        "size": 1,
        "files": [f"Treasure.And.Dirt.S01E{episode:02}.mkv" for episode in range(1, 7)],
        "file_count": 6,
        "trumpable": False,
        "link": "https://example.com/torrents/123",
        "download": None,
        "flags": [],
        "id": 123,
        "type": "WEB-DL",
        "res": "720p",
        "internal": 0,
        "bd_info": None,
        "description": None,
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([season_pack], meta, "OTHER")

    assert result == [season_pack]
    assert meta.season_pack_exists is True
    assert meta.season_pack_id == 123


@pytest.mark.asyncio
async def test_dupe_filter_does_not_block_episode_missing_from_partial_pack() -> None:
    meta = Meta(
        category="TV",
        name="Treasure & Dirt S01E06 1080p HDTV x264-DARKFLiX",
        uuid="Treasure.And.Dirt.S01E06.1080p.HDTV.H264-DARKFLiX",
        season="S01",
        episode="E06",
        resolution="1080p",
        source="HDTV",
        type="HDTV",
        filelist=["Treasure.And.Dirt.S01E06.mkv"],
    )
    partial_pack = {
        "name": "Treasure & Dirt S01 1080p HDTV x264-GROUP",
        "files": [f"Treasure.And.Dirt.S01E{episode:02}.mkv" for episode in range(1, 6)],
        "id": 124,
    }

    assert await DupeChecker({"DEFAULT": {}}).filter_dupes([partial_pack], meta, "OTHER") == []
    assert meta.season_pack_exists is False


@pytest.mark.asyncio
async def test_dupe_filter_does_not_mark_season_pack_without_file_evidence() -> None:
    meta = Meta(
        category="TV",
        name="Treasure & Dirt S01E06 1080p HDTV x264-DARKFLiX",
        uuid="Treasure.And.Dirt.S01E06.1080p.HDTV.H264-DARKFLiX",
        season="S01",
        episode="E06",
        resolution="1080p",
        source="HDTV",
        type="HDTV",
    )
    season_pack = {"name": "Treasure & Dirt S01 1080p HDTV x264-GROUP", "files": [], "id": 125}

    await DupeChecker({"DEFAULT": {}}).filter_dupes([season_pack], meta, "OTHER")

    assert meta.season_pack_exists is False


@pytest.mark.asyncio
async def test_unit3d_episode_search_includes_all_season_pack_qualities(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_params: list[tuple[str, object]] = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, list[object]]:
            return {"data": []}

    class Client:
        async def __aenter__(self) -> "Client":  # noqa: UP037
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, *, url: str, headers: dict[str, str], params: list[tuple[str, object]]) -> Response:
            del url, headers
            captured_params.extend(params)
            return Response()

    monkeypatch.setattr("src.trackers.UNIT3D.httpx.AsyncClient", lambda **_kwargs: Client())
    tracker = UNIT3D({"TRACKERS": {"TEST": {}}}, "TEST")
    tracker.search_url = "https://example.com/api/torrents/filter"
    meta = Meta(category="TV", tmdb=325785, season="S01", episode="E03", resolution="1080p", type="HDTV", tv_pack=False)

    assert await tracker.search_existing(meta) == []
    assert ("tmdbId", "325785") in captured_params
    assert ("name", "S01") in captured_params
    assert not any(key in {"resolutions[]", "types[]"} for key, _value in captured_params)

    captured_params.clear()
    movie = Meta(category="MOVIE", tmdb=27073, resolution="480p", type="ENCODE")
    assert await tracker.search_existing(movie) == []
    assert ("tmdbId", "27073") in captured_params
    assert not any(key in {"resolutions[]", "types[]"} for key, _value in captured_params)

    captured_params.clear()
    book = Meta(category="BOOK", title="Atomic Habits: Tiny Changes, Remarkable Results")
    assert await tracker.search_existing(book) == []
    assert ("name", "Atomic Habits") in captured_params

    assert UNIT3D._is_duplicate_name_error('{"data":{"name":["The name has already been taken."]}}') is True
    assert UNIT3D._is_duplicate_name_error('{"data":{"name":["The name field already exists."]}}') is True
    assert UNIT3D._is_duplicate_name_error('{"data":{"name":["O valor indicado para o campo name já se encontra registado."]}}') is True


@pytest.mark.asyncio
async def test_dupe_filter_keeps_exact_name_before_quality_filters() -> None:
    name = "Full Contact AKA Hap do Ko Fei 1992 480p BluRay Dual-Audio AAC 1.0 x264-gazer"
    meta = Meta(category="MOVIE", name=name, uuid=name, resolution="480p", source="BluRay", type="ENCODE")
    candidate = {"name": name, "size": 1, "type": "REMUX", "res": "1080p"}

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([candidate], meta, "DARKPEERS")

    assert len(result) == 1
    assert result[0]["name"] == name


@pytest.mark.asyncio
async def test_dupe_filter_resets_season_pack_state_between_trackers() -> None:
    meta = Meta(
        category="TV",
        season_pack_exists=True,
        season_pack_id=123,
        season_pack_link="https://example.com/123",
        season_pack_name="Previous Tracker Pack",
    )

    await DupeChecker({"DEFAULT": {}}).filter_dupes([], meta, "OTHER")

    assert meta.season_pack_exists is False
    assert meta.season_pack_id is None
    assert meta.season_pack_link is None
    assert meta.season_pack_name == ""


@pytest.mark.asyncio
async def test_book_dupe_filter_ignores_author_segment_before_main_title() -> None:
    meta = Meta(
        category="BOOK",
        author="James Clear",
        title="Atomic Habits: Tiny Changes, Remarkable Results-Penguin Publishing Group (2018)",
        type="PDF",
        filelist=["Atomic Habits.pdf"],
    )
    candidate = {
        "name": "James Clear - Atomic Habits: An Easy & Proven Way to Build Good Habits & Break Bad Ones 2018 ENG PDF",
        "type": "PDF",
        "files": ["Atomic Habits.pdf"],
        "file_count": 1,
        "size": 13_883_392,
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([candidate], meta, "YUSCENE")

    assert len(result) == 1
    assert result[0]["name"].startswith("James Clear - Atomic Habits:")


@pytest.mark.asyncio
async def test_book_dupe_filter_accepts_exact_payload_with_different_title_metadata() -> None:
    meta = Meta(
        category="BOOK",
        author="James Clear",
        title="Atomic Habits",
        type="PDF",
        filelist=["Atomic Habits.pdf"],
        source_size=13_883_392,
    )
    candidate = {
        "name": "Completely Different Catalog Title 2018 ENG PDF",
        "type": "PDF",
        "files": ["Atomic Habits.pdf"],
        "file_count": 1,
        "size": 13_883_392,
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([candidate], meta, "YUSCENE")

    assert len(result) == 1


@pytest.mark.asyncio
async def test_book_dupe_filter_does_not_match_different_book_by_same_author() -> None:
    meta = Meta(category="BOOK", author="James Clear", title="Atomic Habits", type="PDF", filelist=["Atomic Habits.pdf"])
    candidate = {
        "name": "James Clear - The Clear Habit Journal 2023 ENG PDF",
        "type": "PDF",
        "files": ["The Clear Habit Journal.pdf"],
        "file_count": 1,
        "size": 10_000_000,
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([candidate], meta, "YUSCENE")

    assert result == []


@pytest.mark.asyncio
async def test_book_dupe_filter_does_not_match_workbook_derivative() -> None:
    meta = Meta(category="BOOK", author="James Clear", title="Atomic Habits", type="PDF", filelist=["Atomic Habits.pdf"])
    candidate = {
        "name": "James Clear - WORKBOOK For Atomic Habits 2021 ENG PDF",
        "type": "PDF",
        "files": ["Workbook for Atomic Habits.pdf"],
        "file_count": 1,
        "size": 11_000_000,
    }

    result = await DupeChecker({"DEFAULT": {}}).filter_dupes([candidate], meta, "YUSCENE")

    assert result == []
