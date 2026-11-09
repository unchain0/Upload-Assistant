from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import tracker_status_service
from src.services.tracker_status_service import TrackerStatusManager


def _meta(tmp_path: Path, **values: object) -> Meta:
    media = tmp_path / "Release.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(media),
        "filelist": [str(media)],
        "name": "Release 2026 1080p WEB-DL-GROUP",
        "uuid": "Release.2026.1080p.WEB-DL-GROUP",
        "category": "MOVIE",
        "trackers": [],
        "tracker_status": {},
        "tracker_ids": {},
        "unattended": True,
        "unattended_confirm": False,
        "debug": False,
        "allow_spaces": True,
        "imdb_id": 1234567,
        "imdb": "1234567",
        "imdb_info": {},
        "manual_language": "",
        "is_disc": "",
        "anon": False,
        "region": "",
        "distributor": "",
        "tag": "-GROUP",
        "title": "Release",
        "year": 2026,
        "season": "S01",
        "episode": "E01",
        "season_int": 1,
        "episode_int": 1,
        "tv_pack": False,
        "tvdb_series_status": "Continuing",
        "skipping": None,
        "initial_dupes": {},
        "dupe_checked_trackers": [],
        "tracker_prepared_meta": {},
    }
    state.update(values)
    return Meta(state)


class _Setup:
    def __init__(self, *, banned: bool = False, claimed: bool = False) -> None:
        self.banned = banned
        self.claimed = claimed
        self.filtered = 0

    def filter_unsupported_trackers(self, _meta: Meta) -> None:
        self.filtered += 1

    async def check_banned_group(
        self, *_args: object, **_kwargs: object
    ) -> bool:
        return self.banned

    async def get_torrent_claims(
        self, *_args: object, **_kwargs: object
    ) -> bool:
        return self.claimed


class _Router:
    applied = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def apply(self, _meta: Meta) -> None:
        type(self).applied += 1


class _Helper:
    def __init__(
        self, *, answers: list[object] | None = None, is_dupe: bool = False
    ) -> None:
        self.answers = list(answers or [])
        self.is_dupe = is_dupe
        self.prompts: list[str] = []

    async def prompt_yes_no(
        self, prompt: str, *, default: bool = False
    ) -> bool:
        self.prompts.append(prompt)
        if self.answers:
            value = self.answers.pop(0)
            if isinstance(value, BaseException):
                raise value
            return bool(value)
        return default

    async def dupe_check(
        self, _dupes: list[Any], meta: Meta, _tracker: str
    ) -> tuple[bool, Meta]:
        return self.is_dupe, meta


class _DupeChecker:
    result: ClassVar[list[Any] | None] = None

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    async def filter_dupes(
        self, dupes: list[Any], _meta: Meta, _tracker: str
    ) -> list[Any]:
        return list(self.result if self.result is not None else dupes)


class _Tracker:
    tracker = "TEST"
    banned_groups: ClassVar[list[str]] = []
    required_book_fields = None
    search_result: ClassVar[object] = []
    rename: ClassVar[object] = "Tracker Name"

    def __init__(self, **_kwargs: object) -> None:
        self.tracker = type(self).tracker
        self.banned_groups = type(self).banned_groups

    async def search_existing(self, _meta: Meta) -> list[Any]:
        value = type(self).search_result
        if isinstance(value, BaseException):
            raise value
        return list(value) if isinstance(value, list) else []

    async def get_name(self, _meta: Meta) -> object:
        value = type(self).rename
        if isinstance(value, BaseException):
            raise value
        return value


class _PTP:
    additional = True
    search_result: ClassVar[object] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def get_additional_checks(self, _meta: Meta) -> bool:
        return type(self).additional

    async def get_group_by_imdb(self, _imdb: str) -> str:
        return "group"

    async def search_existing(
        self, _group: str, _meta: dict[str, Any]
    ) -> list[Any]:
        value = type(self).search_result
        if isinstance(value, BaseException):
            raise value
        return list(value) if isinstance(value, list) else []


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    setup: _Setup | None = None,
    helper: _Helper | None = None,
) -> tuple[_Setup, _Helper]:
    setup = setup or _Setup()
    helper = helper or _Helper()
    _Router.applied = 0
    monkeypatch.setattr(
        tracker_status_service, "TrackerSetup", lambda **_kwargs: setup
    )
    monkeypatch.setattr(
        tracker_status_service, "AvistaZNetworkRouter", _Router
    )
    monkeypatch.setattr(
        tracker_status_service, "UploadHelper", lambda _config: helper
    )
    monkeypatch.setattr(tracker_status_service, "DupeChecker", _DupeChecker)
    monkeypatch.setattr(
        tracker_status_service, "blocks_automatic_upload", lambda _meta: False
    )
    return setup, helper


def test_merge_status_and_required_book_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {
        "AITHER": {"route": "a", "upload": False},
        "BHD": {"route": "b"},
    }
    processed = {"AITHER": {"upload": True, "dupe": False}}
    assert tracker_status_service.merge_tracker_status(
        processed, existing
    ) == {
        "AITHER": {"route": "a", "upload": True, "dupe": False},
        "BHD": {"route": "b"},
    }

    monkeypatch.setattr(
        "src.services.book_preparation.missing_book_fields",
        lambda _meta: ["title", "author", "isbn_or_asin"],
    )

    class All:
        required_book_fields = None

    class Some:
        required_book_fields: ClassVar[list[str]] = ["title", "isbn_or_asin"]

    assert tracker_status_service.missing_book_fields_for_tracker(
        Meta(), All
    ) == ["title", "author", "isbn_or_asin"]
    assert tracker_status_service.missing_book_fields_for_tracker(
        Meta(), Some
    ) == ["title", "isbn_or_asin"]


def test_spaces_block_all_trackers_and_preserve_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        tracker_status_service, "blocks_automatic_upload", lambda _meta: True
    )
    monkeypatch.setattr(
        tracker_status_service,
        "content_paths_with_spaces",
        lambda _meta: [f"path {index}" for index in range(7)],
    )
    meta = _meta(
        tmp_path,
        trackers="AITHER",
        tracker_status={"AITHER": {"route": "preferred"}},
    )
    assert (
        asyncio.run(TrackerStatusManager({}).process_all_trackers(meta)) == 0
    )
    assert meta.tracker_status["AITHER"]["route"] == "preferred"
    assert meta.tracker_status["AITHER"]["skipped"] is True
    assert "and 2 more" in meta.tracker_status["AITHER"]["skip_reason"]


def test_manual_usenet_unknown_unattended_and_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup, _helper = _patch_runtime(monkeypatch)
    meta = _meta(
        tmp_path,
        trackers=["MANUAL", "USENET", "UNKNOWN"],
        unattended=True,
        debug=True,
    )
    result = asyncio.run(
        TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
    )
    assert result == 3
    assert all(
        meta.tracker_status[name]["upload"]
        for name in ("MANUAL", "USENET", "UNKNOWN")
    )
    assert setup.filtered == 1 and _Router.applied == 1

    wrapped = _meta(tmp_path, trackers=["MANUAL"])
    assert (
        asyncio.run(
            tracker_status_service.process_all_trackers(
                wrapped, {"TRACKERS": {}}
            )
        )
        == 1
    )


def test_douban_and_imdb_prompt_blank_invalid_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch, helper=_Helper(answers=[True]))
    monkeypatch.setattr(
        tracker_status_service, "get_douban_id", AsyncMock(return_value=999)
    )
    monkeypatch.setattr(
        tracker_status_service.imdb_manager,
        "get_imdb_info_api",
        AsyncMock(return_value={"title": "IMDb"}),
    )
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "PASSTHEPOPCORN", _Tracker
    )
    monkeypatch.setattr(tracker_status_service, "PassThePopcorn", _PTP)

    prompts = iter(("bad", "tt1234567"))
    monkeypatch.setattr(
        tracker_status_service,
        "prompt_in_thread",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(prompts)),
    )
    meta = _meta(
        tmp_path,
        trackers=["PASSTHEPOPCORN", "RAILGUNPT"],
        imdb_id=0,
        imdb="",
        unattended=False,
    )
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "RAILGUNPT", _Tracker
    )
    result = asyncio.run(
        TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
    )
    assert result == 2 and meta.imdb_id == 1234567 and meta.douban_id == 999
    assert meta.imdb_info == {"title": "IMDb"}

    monkeypatch.setattr(
        tracker_status_service, "prompt_in_thread", AsyncMock(return_value="")
    )
    blank = _meta(
        tmp_path,
        trackers=["PASSTHEPOPCORN"],
        imdb_id=0,
        imdb="",
        unattended=False,
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(blank)
        )
        == 0
    )
    assert blank.imdb_id == 0


def test_imdb_prompt_eof_cleans_and_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch)
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "PASSTHEPOPCORN", _Tracker
    )
    monkeypatch.setattr(
        tracker_status_service,
        "prompt_in_thread",
        AsyncMock(side_effect=EOFError),
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "cleanup", cleanup
    )
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "reset_terminal", lambda: None
    )
    meta = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=0, unattended=False
    )
    with pytest.raises(OperationAbortedError, match="Tracker selection"):
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
        )
    cleanup.assert_awaited_once()


def test_banned_claimed_skip_upload_book_and_game_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "TEST", _Tracker
    )

    for setup, values, expected_reason in (
        (_Setup(banned=True), {}, None),
        (_Setup(claimed=True), {}, None),
        (_Setup(), {"tracker_status": {"TEST": {"skip_upload": True}}}, None),
    ):
        _patch_runtime(monkeypatch, setup=setup)
        meta = _meta(tmp_path, trackers=["TEST"], **values)
        assert (
            asyncio.run(
                TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                    meta
                )
            )
            == 0
        )
        assert meta.tracker_status["TEST"]["banned"] is setup.banned
        assert meta.tracker_status["TEST"]["skipped"] is (not setup.banned)
        assert expected_reason is None

    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        tracker_status_service,
        "missing_book_fields_for_tracker",
        lambda *_args: ["narrator"],
    )
    book = _meta(tmp_path, trackers=["TEST"], category="BOOK", unattended=True)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(book)
        )
        == 0
    )
    assert "narrator" in book.tracker_status["TEST"]["skip_reason"]

    monkeypatch.setattr(
        tracker_status_service,
        "missing_game_fields",
        lambda _meta: ["platform"],
    )
    game = _meta(
        tmp_path,
        trackers=["TEST"],
        category="GAME",
        unattended=True,
        software=False,
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(game)
        )
        == 0
    )
    assert "GAME fields" in game.tracker_status["TEST"]["skip_reason"]
    software = _meta(
        tmp_path,
        trackers=["TEST"],
        category="GAME",
        unattended=True,
        software=True,
    )
    asyncio.run(
        TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(software)
    )
    assert "SOFTWARE fields" in software.tracker_status["TEST"]["skip_reason"]


def test_additional_checks_sync_async_cjk_and_zenith_defer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch)

    class AsyncReject(_Tracker):
        @staticmethod
        async def get_additional_checks(_meta: Meta) -> bool:
            return False

    class SyncReject(_Tracker):
        @staticmethod
        def get_additional_checks(_meta: Meta) -> bool:
            return False

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "ASYNC", AsyncReject
    )
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "SYNC", SyncReject
    )
    meta = _meta(tmp_path, trackers=["ASYNC", "SYNC"])
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
        )
        == 0
    )

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "TEST", _Tracker
    )
    monkeypatch.setattr(
        tracker_status_service,
        "missing_book_fields_for_tracker",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        tracker_status_service,
        "book_metadata_cjk_fields",
        lambda _meta: ["title"],
    )
    cjk = _meta(tmp_path, trackers=["TEST"], category="BOOK")
    asyncio.run(
        TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(cjk)
    )
    assert "CJK" in cjk.tracker_status["TEST"]["skip_reason"]

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "ZENITH", _Tracker
    )
    monkeypatch.setattr(
        tracker_status_service,
        "should_prepare_zenith_audiobook",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        tracker_status_service,
        "should_prepare_zenith_ebook",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        tracker_status_service,
        "book_metadata_cjk_fields",
        lambda _meta: ["title"],
    )
    zenith = _meta(tmp_path, trackers=["ZENITH"], category="BOOK")
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(zenith)
        )
        == 1
    )
    assert (
        zenith.tracker_prepared_meta["ZENITH"].get("defer_zentag_validation")
        is True
    )


def test_regular_search_success_other_dupe_state_and_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch, helper=_Helper(is_dupe=True))

    class RichTracker(_Tracker):
        tracker = "AITHER"
        search_result: ClassVar[list[dict[str, int]]] = [{"id": 1}]
        rename: ClassVar[dict[str, str]] = {"name": "Renamed"}

        async def search_existing(self, meta: Meta) -> list[Any]:
            meta.tracker_status["AITHER"]["other"] = True
            meta.AITHER_matched_episode_ids = [{"id": 1}]
            meta.trumpable_id = 99
            meta.AITHER_cross_seed = "hash"
            meta.were_trumping = True
            meta.trump_reason = "better"
            meta.AITHER_trumpable_id = 88
            return list(type(self).search_result)

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "AITHER", RichTracker
    )
    meta = _meta(tmp_path, trackers=["AITHER"], unattended=True)
    result = asyncio.run(
        TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
    )
    assert result == 0 and meta.tracker_status["AITHER"]["dupe"] is True
    assert meta.initial_dupes["AITHER"] == [{"id": 1}]
    assert meta.AITHER_matched_episode_ids == [{"id": 1}]
    assert meta.trumpable_id == 99 and meta.AITHER_cross_seed == "hash"
    assert (
        meta.were_trumping
        and meta.trump_reason == "better"
        and meta.AITHER_trumpable_id == 88
    )
    assert "AITHER" in meta.dupe_checked_trackers

    _patch_runtime(monkeypatch)

    class StringName(_Tracker):
        rename = "String Rename"

    class BrokenName(_Tracker):
        rename = RuntimeError("rename failed")

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "STRING", StringName
    )
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "BROKEN", BrokenName
    )
    attended = _meta(
        tmp_path, trackers=["STRING", "BROKEN"], unattended=False, debug=True
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                attended
            )
        )
        == 2
    )


def test_search_errors_unattended_and_attended_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing(_Tracker):
        search_result = RuntimeError("search failed")

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "FAIL", Failing
    )
    _patch_runtime(monkeypatch)
    unattended = _meta(tmp_path, trackers=["FAIL"], unattended=True)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                unattended
            )
        )
        == 0
    )
    assert unattended.tracker_status["FAIL"]["skipped"]

    for answer, expected in ((True, 1), (False, 0)):
        _patch_runtime(monkeypatch, helper=_Helper(answers=[answer, True]))
        attended = _meta(tmp_path, trackers=["FAIL"], unattended=False)
        assert (
            asyncio.run(
                TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                    attended
                )
            )
            == expected
        )

    cleanup = AsyncMock()
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "cleanup", cleanup
    )
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "reset_terminal", lambda: None
    )
    _patch_runtime(monkeypatch, helper=_Helper(answers=[EOFError()]))
    with pytest.raises(OperationAbortedError, match="Tracker selection"):
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                _meta(tmp_path, trackers=["FAIL"], unattended=False)
            )
        )
    cleanup.assert_awaited_once()


def test_ptp_additional_search_success_failure_and_prompt_eof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "PASSTHEPOPCORN", _Tracker
    )
    monkeypatch.setattr(tracker_status_service, "PassThePopcorn", _PTP)
    _PTP.additional = False
    _patch_runtime(monkeypatch)
    skipped = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=True
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                skipped
            )
        )
        == 0
    )

    _PTP.additional = True
    _PTP.search_result = [{"id": 1}]
    _patch_runtime(monkeypatch)
    success = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=True
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                success
            )
        )
        == 1
    )
    assert success.ptp_groupid == "group"

    _PTP.search_result = RuntimeError("ptp failed")
    _patch_runtime(monkeypatch)
    failed = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=True
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(failed)
        )
        == 0
    )

    _patch_runtime(monkeypatch, helper=_Helper(answers=[True, True]))
    attended = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=False
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                attended
            )
        )
        == 1
    )

    cleanup = AsyncMock()
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "cleanup", cleanup
    )
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "reset_terminal", lambda: None
    )
    _patch_runtime(monkeypatch, helper=_Helper(answers=[EOFError()]))
    with pytest.raises(OperationAbortedError, match="Tracker selection"):
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                _meta(
                    tmp_path,
                    trackers=["PASSTHEPOPCORN"],
                    imdb_id=123,
                    unattended=False,
                )
            )
        )


def test_attended_upload_prompt_yes_no_eof_manual_and_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "ONE", _Tracker
    )
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "TWO", _Tracker
    )

    for answer, expected in ((True, 2), (False, 0)):
        _patch_runtime(monkeypatch, helper=_Helper(answers=[answer]))
        meta = _meta(tmp_path, trackers=["ONE", "TWO"], unattended=False)
        assert (
            asyncio.run(
                TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                    meta
                )
            )
            == expected
        )

    _patch_runtime(monkeypatch, helper=_Helper(answers=[False]))
    manual = _meta(tmp_path, trackers=["MANUAL", "ONE"], unattended=False)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(manual)
        )
        == 1
    )
    assert manual.tracker_status["MANUAL"]["upload"]

    cleanup = AsyncMock()
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "cleanup", cleanup
    )
    monkeypatch.setattr(
        tracker_status_service.cleanup_manager, "reset_terminal", lambda: None
    )
    _patch_runtime(monkeypatch, helper=_Helper(answers=[EOFError()]))
    with pytest.raises(OperationAbortedError, match="Tracker processing"):
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                _meta(tmp_path, trackers=["ONE"], unattended=False)
            )
        )

    _patch_runtime(monkeypatch)
    debug = _meta(tmp_path, trackers=["ONE"], unattended=False, debug=True)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(debug)
        )
        == 1
    )


def test_completed_episode_blocks_non_dupes_and_amigos_anonymous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "AMIGOSSHARE", _Tracker
    )
    _patch_runtime(monkeypatch)
    monkeypatch.setattr(
        tracker_status_service.Common,
        "is_completed_tv_episode",
        lambda _meta: True,
    )
    meta = _meta(
        tmp_path,
        trackers=["AMIGOSSHARE"],
        category="TV",
        tvdb_series_status="Ended",
        anon=True,
        unattended=True,
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
        )
        == 0
    )
    assert meta.tracker_status["AMIGOSSHARE"]["skipped"]


def test_remaining_nested_tracker_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch)
    manual = _meta(tmp_path, trackers=["MANUAL"], name="Release DUPE?")
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(manual)
        )
        == 1
    )

    class SyncPTP:
        def __init__(self, **_kwargs: object) -> None:
            pass

        @staticmethod
        def get_additional_checks(_meta: Meta) -> bool:
            return True

        async def get_group_by_imdb(self, _imdb: str) -> str:
            return "group"

        async def search_existing(
            self, _group: str, _meta: dict[str, Any]
        ) -> list[Any]:
            return []

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "PASSTHEPOPCORN", _Tracker
    )
    monkeypatch.setattr(tracker_status_service, "PassThePopcorn", SyncPTP)
    ptp = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=True
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(ptp)
        )
        == 1
    )

    class FailingPTP(SyncPTP):
        async def search_existing(
            self, _group: str, _meta: dict[str, Any]
        ) -> list[Any]:
            raise RuntimeError("ptp failed")

    monkeypatch.setattr(tracker_status_service, "PassThePopcorn", FailingPTP)
    _patch_runtime(monkeypatch, helper=_Helper(answers=[False]))
    ptp = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=False
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(ptp)
        )
        == 0
    )
    assert ptp.tracker_status["PASSTHEPOPCORN"]["skipped"]

    class DictName(_Tracker):
        rename: ClassVar[dict[str, str]] = {"name": "Dictionary Rename"}

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "DICT", DictName
    )
    _patch_runtime(monkeypatch)
    dictionary_name = _meta(tmp_path, trackers=["DICT"], unattended=True)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                dictionary_name
            )
        )
        == 1
    )
    assert dictionary_name.initial_dupes["DICT"] == []


def test_remaining_name_ptp_sync_decline_initial_dupes_and_dict_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runtime(monkeypatch)

    class DictName(_Tracker):
        rename: ClassVar[dict[str, str]] = {"name": "Renamed Dict"}

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "DICT", DictName
    )
    meta = _meta(
        tmp_path, trackers=["DICT"], name="Release DUPE?", unattended=True
    )
    meta.pop("initial_dupes", None)
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(meta)
        )
        == 1
    )
    assert meta.initial_dupes["DICT"] == []

    class SyncPTP(_PTP):
        def get_additional_checks(self, _meta: Meta) -> bool:  # type: ignore[override]
            return True

    monkeypatch.setitem(
        tracker_status_service.tracker_class_map, "PASSTHEPOPCORN", _Tracker
    )
    monkeypatch.setattr(tracker_status_service, "PassThePopcorn", SyncPTP)
    SyncPTP.search_result = RuntimeError("ptp failed")
    _patch_runtime(monkeypatch, helper=_Helper(answers=[False]))
    declined = _meta(
        tmp_path, trackers=["PASSTHEPOPCORN"], imdb_id=123, unattended=False
    )
    assert (
        asyncio.run(
            TrackerStatusManager({"TRACKERS": {}}).process_all_trackers(
                declined
            )
        )
        == 0
    )
    assert declined.tracker_status["PASSTHEPOPCORN"]["skipped"] is True


def test_tracker_status_defensive_mapping_and_missing_game_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = TrackerStatusManager({"TRACKERS": {}})
    meta = _meta(tmp_path, tracker_status={"TEST": "invalid"})
    status = manager._initial_status()
    manager._apply_preexisting_skip("TEST", meta, status)
    assert not status["skipped"]
    assert manager._tracker_other_status("invalid") is False
    assert manager._tracker_skip_reason("TEST", meta, "fallback") == "fallback"

    monkeypatch.setattr(
        tracker_status_service, "missing_game_fields", lambda _meta: []
    )
    assert (
        manager._missing_game_field_reason(_meta(tmp_path, category="GAME"))
        == ""
    )
    assert manager._tracker_name_values(42) == ()


@pytest.mark.asyncio
async def test_tracker_status_additional_checks_accept_and_ptp_missing_method(
    tmp_path: Path,
) -> None:
    manager = TrackerStatusManager({"TRACKERS": {}})
    meta = _meta(tmp_path)
    status = manager._initial_status()

    class Accepts:
        @staticmethod
        async def get_additional_checks(_meta: Meta) -> bool:
            return True

    assert await manager._additional_checks_allow(
        "TEST", meta, Accepts(), status
    )
    assert await manager._ptp_additional_checks(
        object(), "PASSTHEPOPCORN", meta, status
    )


@pytest.mark.asyncio
async def test_tracker_status_store_prepared_meta_repairs_invalid_container(
    tmp_path: Path,
) -> None:
    meta = _meta(tmp_path, tracker_prepared_meta="invalid")
    local = _meta(tmp_path)
    await TrackerStatusManager._store_prepared_meta(
        meta, "TEST", local, asyncio.Lock()
    )
    prepared = meta.get("tracker_prepared_meta")
    assert isinstance(prepared, dict)
    assert isinstance(prepared["TEST"], Meta)


@pytest.mark.asyncio
async def test_tracker_status_dupe_evaluation_honors_tracker_skip(
    tmp_path: Path,
) -> None:
    manager = TrackerStatusManager({"TRACKERS": {}})
    local = _meta(
        tmp_path,
        skipping="TEST",
        tracker_status={"TEST": {"skip_reason": "tracker policy"}},
    )
    shared = _meta(tmp_path)
    status = manager._initial_status()
    runtime = tracker_status_service._TrackerRuntime(
        setup=_Setup(),
        helper=_Helper(),
        dupe_checker=_DupeChecker({}),
        lock=asyncio.Lock(),
    )
    await manager._evaluate_dupes("TEST", local, [], status, shared, runtime)
    assert status["skipped"] is True
    assert status["skip_reason"] == "tracker policy"


def test_tracker_status_completed_episode_preserves_dupe_result() -> None:
    status = {
        "banned": False,
        "skipped": False,
        "dupe": True,
        "upload": False,
        "other": False,
    }
    result = tracker_status_service._TrackerResult(
        "TEST", status, None, _Tracker
    )
    assert (
        TrackerStatusManager._block_completed_episode_result(result) is False
    )
    assert status["skipped"] is False
