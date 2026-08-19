from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import OperationAbortedError
from src.domain_models.release import Meta
from src.services import tracker_upload_service


def _meta(tmp_path: Path, trackers: list[str], **values: object) -> Meta:
    media = tmp_path / "Release.mkv"
    media.write_bytes(b"media")
    state: dict[str, object] = {
        "base_dir": str(tmp_path),
        "path": str(media),
        "filelist": [str(media)],
        "uuid": "release",
        "name": "Release 2026 1080p WEB-DL-GROUP",
        "category": "MOVIE",
        "trackers": trackers,
        "tracker_status": {tracker: {"upload": True} for tracker in trackers},
        "tracker_prepared_meta": {},
        "imghost": "imgbb",
        "imghost_from_cli": False,
        "image_list": [{"raw_url": f"https://img.invalid/{index}.png"} for index in range(4)],
        "tracker_image_collections": {},
        "artwork_path": "",
        "print_tracker_links": True,
        "print_tracker_messages": True,
        "upload_timer": False,
        "debug": False,
        "unattended": True,
        "qbit_bandwidth_control": False,
        "qbit_bandwidth_threshold": 0,
        "qbit_bandwidth_time": 0,
        "initial_dupes": {},
        "ptp_groupid": "group",
        "discs": [],
        "tv_pack": False,
        "screens": 4,
        "keep_folder": False,
        "allow_spaces": True,
        "zentag_prepared": False,
    }
    state.update(values)
    return Meta(state)


def _config(**defaults: object) -> dict[str, Any]:
    return {
        "DEFAULT": {
            "multiScreens": 2,
            "smart_image_host_selection": False,
            "qbit_bandwidth_control": False,
            "qbit_bandwidth_threshold": 0,
            "qbit_bandwidth_time": 0,
            "show_upload_duration": True,
            "img_host_1": "imgbb",
            "min_successful_image_uploads": 1,
            **defaults,
        }
    }


class _Setup:
    enabled: ClassVar[list[str]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def trackers_enabled(self, _meta: Meta) -> list[str]:
        return list(type(self).enabled)


class _Client:
    def __init__(self) -> None:
        self.added: list[str] = []

    async def add_to_client(self, _meta: Meta, tracker: str) -> None:
        self.added.append(tracker)


class _Tracker:
    tracker = "TEST"
    torrent_url = "https://tracker.invalid/torrents/"
    requires_book_cover = False
    is_usenet = False
    upload_result: ClassVar[object] = True
    search_result: ClassVar[object] = []
    flags: ClassVar[dict[str, object]] = {}
    live = 1
    additional = True
    edit_calls = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def upload(self, _meta: Meta) -> object:
        value = type(self).upload_result
        if isinstance(value, BaseException):
            raise value
        return value

    async def search_existing(self, _meta: Meta) -> list[Any]:
        value = type(self).search_result
        if isinstance(value, BaseException):
            raise value
        return list(value) if isinstance(value, list) else []

    async def get_flag(self, _meta: Meta, name: str) -> object:
        return type(self).flags.get(name, False)

    async def get_live(self, _meta: Meta) -> int:
        return type(self).live

    async def get_additional_checks(self, _meta: Meta) -> bool:
        return type(self).additional

    async def edit_desc(self, _meta: Meta) -> None:
        type(self).edit_calls += 1


class _PTP:
    tracker = "PASSTHEPOPCORN"
    torrent_url = "https://ptp.invalid/torrents.php?id="
    upload_result: ClassVar[object] = True

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def fill_upload_form(self, _group: str, _meta: Meta) -> tuple[str, dict[str, str]]:
        return "https://ptp.invalid/upload", {"title": "Release"}

    async def upload(self, *_args: object, **_kwargs: object) -> object:
        value = type(self).upload_result
        if isinstance(value, BaseException):
            raise value
        return value

    async def search_existing(self, *_args: object, **_kwargs: object) -> list[Any]:
        return []


class _Package:
    result: ClassVar[object] = "https://files.invalid/package"

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    async def package(self, _meta: Meta) -> object:
        return type(self).result


class _Wait:
    result = False

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    async def wait_for_bandwidth(self, _threshold: int, _seconds: int) -> bool:
        return type(self).result


class _DupeChecker:
    result: ClassVar[list[Any] | None] = None

    def __init__(self, _config: dict[str, Any]) -> None:
        pass

    async def filter_dupes(self, dupes: list[Any], _meta: Meta, _tracker: str) -> list[Any]:
        return list(type(self).result if type(self).result is not None else dupes)


def _patch_basics(monkeypatch: pytest.MonkeyPatch, enabled: list[str]) -> None:
    _Setup.enabled = list(enabled)
    _Tracker.upload_result = True
    _Tracker.search_result = []
    _Tracker.flags = {}
    _Tracker.live = 1
    _Tracker.additional = True
    _Tracker.edit_calls = 0
    _PTP.upload_result = True
    _PTP.search_result = []
    _Package.result = "https://files.invalid/package"
    _Wait.result = False
    _DupeChecker.result = None
    monkeypatch.setattr(tracker_upload_service, "TrackerSetup", _Setup)
    monkeypatch.setattr(tracker_upload_service, "ManualPackageManager", _Package)
    monkeypatch.setattr(tracker_upload_service, "Wait", _Wait)
    monkeypatch.setattr(tracker_upload_service, "DupeChecker", _DupeChecker)
    monkeypatch.setattr(tracker_upload_service, "check_tracker_image_hosts", AsyncMock())
    monkeypatch.setattr(tracker_upload_service, "screenshot_requirement_error", lambda *_args: None)
    monkeypatch.setattr(tracker_upload_service, "book_metadata_cjk_fields", lambda _meta: [])
    monkeypatch.setattr(tracker_upload_service, "is_valid_cover_image", lambda _path: True)
    monkeypatch.setattr(tracker_upload_service, "select_common_image_host", lambda *_args: None)
    monkeypatch.setattr(tracker_upload_service, "has_restricted_image_hosts", lambda *_args: False)
    monkeypatch.setattr(tracker_upload_service, "PassThePopcorn", _PTP)


def _run(
    meta: Meta,
    config: dict[str, Any],
    tracker_map: dict[str, Any],
    *,
    api: tuple[str, ...] = (),
    http: tuple[str, ...] = (),
    other: tuple[str, ...] = (),
    client: _Client | None = None,
) -> _Client:
    client = client or _Client()
    asyncio.run(
        tracker_upload_service.process_trackers(
            meta,
            config,
            client,
            api,
            tracker_map,
            http,
            other,
        )
    )
    return client


def test_prepare_tracker_meta_failure_and_nonzenith_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = _meta(tmp_path, ["TEST"], tracker_prepared_meta={"TEST": {"title": "Prepared"}})
    result = asyncio.run(tracker_upload_service.prepare_tracker_meta(shared, "TEST", _config()))
    assert result.title == "Prepared" and result.trackers == ["TEST"]

    prepared = tmp_path / "zentag"
    prepared.mkdir()
    monkeypatch.setattr(tracker_upload_service, "should_prepare_zenith_audiobook", lambda *_args: True)
    monkeypatch.setattr(tracker_upload_service, "should_prepare_zenith_ebook", lambda *_args: False)
    monkeypatch.setattr(tracker_upload_service, "prepare_zenith_audiobook", AsyncMock(return_value=str(prepared)))
    monkeypatch.setattr(tracker_upload_service, "prepare_zenith_ebook", AsyncMock(return_value=None))

    class BrokenPrep:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def gather_prep(self, **_kwargs: object) -> Meta:
            raise RuntimeError("validation failed")

    monkeypatch.setattr(tracker_upload_service, "Prep", BrokenPrep)
    monkeypatch.setattr(tracker_upload_service, "prepare_zenith_music_layout", lambda _meta: None)
    shared = _meta(tmp_path, ["ZENITH"])
    result = asyncio.run(tracker_upload_service.prepare_tracker_meta(shared, "ZENITH", _config()))
    assert result.path != str(prepared)
    assert shared.tracker_status["ZENITH"]["skipped"] is True
    assert "validation failed" in shared.tracker_status["ZENITH"]["status_message"]


def test_modq_and_draft_capabilities() -> None:
    async def exercise() -> None:
        class BHD(_Tracker):
            tracker = "BEYONDHD"
            live = 0

        class Aither(_Tracker):
            tracker = "AITHER"
            flags: ClassVar[dict[str, object]] = {"modq": "yes"}

        class LST(_Tracker):
            tracker = "LST"
            flags: ClassVar[dict[str, object]] = {"modq": "0", "draft": "true"}

        assert await tracker_upload_service.check_mod_q_and_draft(BHD(), Meta()) == (None, "Draft", {"draft_live": True})
        assert await tracker_upload_service.check_mod_q_and_draft(Aither(), Meta()) == ("Yes", None, {"mod_q": True, "draft": False})
        assert await tracker_upload_service.check_mod_q_and_draft(LST(), Meta()) == ("No", "Yes", {"mod_q": True, "draft": True})
        assert await tracker_upload_service.check_mod_q_and_draft(_Tracker(), Meta()) == (None, None, {})

    asyncio.run(exercise())


def test_smart_host_selection_and_invalid_runtime_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])
    monkeypatch.setattr(tracker_upload_service, "select_common_image_host", lambda *_args: "imgbox")
    meta = _meta(tmp_path, ["TEST"], tracker_status={"TEST": {"upload": True}}, imghost="imgbb")
    config: dict[str, Any] = {
        "DEFAULT": {"smart_image_host_selection": True, "multiScreens": 2, "img_host_1": "imgbb"},
        "_runtime": "invalid",
    }
    _run(meta, config, {"TEST": _Tracker}, api=("TEST",))
    assert meta.imghost == "imgbox"
    assert isinstance(config["_runtime"], dict)

    _patch_basics(monkeypatch, ["TEST"])
    monkeypatch.setattr(tracker_upload_service, "has_restricted_image_hosts", lambda *_args: True)
    config = {"DEFAULT": [], "_runtime": {"disabled_trackers": "invalid"}}
    _run(_meta(tmp_path, ["TEST"], imghost_from_cli=True), config, {"TEST": _Tracker}, api=("TEST",))  # type: ignore[arg-type]
    assert config["_runtime"]["disabled_trackers"] == {}


def test_api_success_prints_link_message_duration_colors_and_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])

    class Success(_Tracker):
        tracker = "TEST"

        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["TEST"].update(torrent_id=123, status_message="Uploaded with private-token")
            return True

    logged: list[str] = []
    monkeypatch.setattr(tracker_upload_service.logger, "info", lambda message, **_kwargs: logged.append(str(message)))
    monkeypatch.setattr(tracker_upload_service.Redaction, "redact_private_info", lambda value: value.replace("private-token", "[REDACTED]"))
    monkeypatch.setattr(tracker_upload_service, "format_terminal_link", lambda text, url, _config: f"{text}:{url}")
    times = iter((1.0, 7.0))
    monkeypatch.setattr(tracker_upload_service, "time", SimpleNamespace(time=lambda: next(times)))
    meta = _meta(tmp_path, ["TEST"], print_tracker_links=True, print_tracker_messages=True)
    client = _run(meta, _config(), {"TEST": Success}, api=("TEST",))
    assert meta.tracker_status["TEST"]["upload_success"] is True
    assert client.added == ["TEST"]
    assert any("link:https://tracker.invalid/torrents/123" in message for message in logged)
    assert any("[REDACTED]" in message for message in logged)
    assert any("6.00s" in message for message in logged), logged


def test_print_result_none_data_error_and_print_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])

    class NoneUpload(_Tracker):
        tracker = "TEST"
        upload_result = None

    meta = _meta(tmp_path, ["TEST"], print_tracker_links=False, print_tracker_messages=False)
    _run(meta, _config(), {"TEST": NoneUpload}, api=("TEST",))
    assert meta.tracker_status["TEST"]["upload_success"] is False

    class DataError(_Tracker):
        tracker = "TEST"

        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["TEST"]["status_message"] = "data error: bad payload"
            return False

    meta = _meta(tmp_path, ["TEST"], print_tracker_links=True, print_tracker_messages=True)
    _run(meta, _config(), {"TEST": DataError}, api=("TEST",))
    assert meta.tracker_status["TEST"]["upload_success"] is False

    monkeypatch.setattr(tracker_upload_service, "format_terminal_link", lambda *_args: (_ for _ in ()).throw(RuntimeError("print failed")))

    class Link(_Tracker):
        tracker = "TEST"

        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["TEST"]["torrent_id"] = 1
            return True

    _run(_meta(tmp_path, ["TEST"]), _config(), {"TEST": Link}, api=("TEST",))


def test_disabled_cover_cjk_zentag_and_dupe_status_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])
    disabled_config = _config()
    disabled_config["_runtime"] = {"disabled_trackers": {"TEST": "modqueue limit"}}
    disabled = _meta(tmp_path, ["TEST"])
    _run(disabled, disabled_config, {"TEST": _Tracker}, api=("TEST",))
    assert disabled.tracker_status["TEST"]["skipped"]

    class Cover(_Tracker):
        tracker = "TEST"
        requires_book_cover = True

    monkeypatch.setattr(tracker_upload_service, "is_valid_cover_image", lambda _path: False)
    book = _meta(tmp_path, ["TEST"], category="BOOK")
    _run(book, _config(), {"TEST": Cover}, api=("TEST",))
    assert "cover image" in book.tracker_status["TEST"]["status_message"]

    monkeypatch.setattr(tracker_upload_service, "is_valid_cover_image", lambda _path: True)
    monkeypatch.setattr(tracker_upload_service, "book_metadata_cjk_fields", lambda _meta: ["title"])
    cjk = _meta(tmp_path, ["TEST"], category="BOOK")
    _run(cjk, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert "CJK" in cjk.tracker_status["TEST"]["status_message"]

    class Zenith(_Tracker):
        tracker = "ZENITH"
        additional = False

    _patch_basics(monkeypatch, ["ZENITH"])
    monkeypatch.setattr(tracker_upload_service, "book_metadata_cjk_fields", lambda _meta: ["title"])
    prepared = _meta(tmp_path, ["ZENITH"], category="BOOK", zentag_prepared=True)
    _run(prepared, _config(), {"ZENITH": Zenith}, api=("ZENITH",))
    assert "failed Zenith validation" in prepared.tracker_status["ZENITH"]["status_message"]

    _patch_basics(monkeypatch, ["TEST"])
    dupe = _meta(tmp_path, ["TEST"], tracker_status={"TEST": {"upload": True, "dupe": True, "upload_success": True}})
    _run(dupe, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert "upload_success" not in dupe.tracker_status["TEST"]


def test_bandwidth_invalid_no_wait_and_new_dupe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])
    invalid = _meta(
        tmp_path,
        ["TEST"],
        qbit_bandwidth_control=True,
        qbit_bandwidth_threshold="bad",
        qbit_bandwidth_time="bad",
    )
    _run(invalid, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert invalid.tracker_status["TEST"]["upload_success"] is True

    class Search(_Tracker):
        tracker = "TEST"
        search_result: ClassVar[list[Any]] = [
            {"name": "initial", "size": 1},
            {"name": "new", "size": 2},
            "same",
            "new-string",
        ]

    _Wait.result = True
    _DupeChecker.result = [{"name": "new", "size": 2}]

    class DupeHelper:
        def __init__(self, _config: dict[str, Any]) -> None:
            pass

        async def dupe_check(self, _dupes: list[Any], meta: Meta, _tracker: str) -> tuple[bool, Meta]:
            return True, meta

    monkeypatch.setattr("src.services.upload_decision_service.UploadHelper", DupeHelper)
    meta = _meta(
        tmp_path,
        ["TEST"],
        qbit_bandwidth_control=True,
        qbit_bandwidth_threshold=1,
        qbit_bandwidth_time=1,
        initial_dupes={"TEST": [{"name": "initial", "size": 1}, "same"]},
    )
    _run(meta, _config(), {"TEST": Search}, api=("TEST",))
    assert "new dupe" in meta.tracker_status["TEST"]["status_message"]
    assert not meta.tracker_status["TEST"].get("upload_success")


def test_bandwidth_recheck_errors_and_ptp_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])

    class BrokenSearch(_Tracker):
        tracker = "TEST"
        search_result = RuntimeError("recheck failed")

    _Wait.result = True
    meta = _meta(tmp_path, ["TEST"], qbit_bandwidth_control=True, qbit_bandwidth_threshold=1, qbit_bandwidth_time=1)
    _run(meta, _config(), {"TEST": BrokenSearch}, api=("TEST",))
    assert "Error redoing dupe" in meta.tracker_status["TEST"]["status_message"]

    _patch_basics(monkeypatch, ["PASSTHEPOPCORN"])
    _Wait.result = True
    _PTP.search_result = []
    ptp = _meta(tmp_path, ["PASSTHEPOPCORN"], qbit_bandwidth_control=True, qbit_bandwidth_threshold=1, qbit_bandwidth_time=1)
    _run(ptp, _config(), {}, other=("PASSTHEPOPCORN",))
    assert not ptp.tracker_status["PASSTHEPOPCORN"].get("upload_success")


def test_rehost_screenshot_upload_exception_failure_and_modqueue_disable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])
    monkeypatch.setattr(tracker_upload_service, "screenshot_requirement_error", lambda *_args: "Need screenshots")
    screenshots = _meta(tmp_path, ["TEST"])
    _run(screenshots, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert screenshots.tracker_status["TEST"]["skipped"]

    _patch_basics(monkeypatch, ["TEST"])
    monkeypatch.setattr(tracker_upload_service, "check_tracker_image_hosts", AsyncMock(side_effect=RuntimeError("rehost failed")))
    failed = _meta(tmp_path, ["TEST"])
    _run(failed, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert not failed.tracker_status["TEST"].get("upload_success")

    class Modqueue(_Tracker):
        tracker = "TEST"

        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["TEST"]["status_message"] = "Modqueue limit reached"
            return False

    _patch_basics(monkeypatch, ["TEST"])
    config = _config()
    modqueue = _meta(tmp_path, ["TEST"])
    _run(modqueue, config, {"TEST": Modqueue}, api=("TEST",))
    assert config["_runtime"]["disabled_trackers"]["TEST"] == "Modqueue limit reached"


def test_other_http_success_none_failure_and_usenet_client_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["HTTP"])

    class Http(_Tracker):
        tracker = "HTTP"

    meta = _meta(tmp_path, ["HTTP"])
    client = _run(meta, _config(), {"HTTP": Http}, http=("HTTP",))
    assert meta.tracker_status["HTTP"]["upload_success"] and client.added == ["HTTP"]

    Http.upload_result = None
    meta = _meta(tmp_path, ["HTTP"])
    _run(meta, _config(), {"HTTP": Http}, other=("HTTP",))
    assert meta.tracker_status["HTTP"]["upload_success"] is False

    class Usenet(Http):
        tracker = "HTTP"
        is_usenet = True
        upload_result = True

    meta = _meta(tmp_path, ["HTTP"])
    client = _run(meta, _config(), {"HTTP": Usenet}, other=("HTTP",))
    assert client.added == []


def test_manual_package_api_http_errors_false_url_and_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["MANUAL", "API", "HTTP"])

    class Api(_Tracker):
        tracker = "API"

    class Http(_Tracker):
        tracker = "HTTP"

    monkeypatch.setattr(tracker_upload_service, "DescriptionBuilder", lambda *_args, **_kwargs: type("Builder", (), {"general_description_generator": AsyncMock()})())
    _Package.result = False
    manual = _meta(tmp_path, ["MANUAL"], unattended=True)
    _run(manual, _config(), {"API": Api, "HTTP": Http}, api=("API",), http=("HTTP",))
    assert Api.edit_calls == 0 and Http.edit_calls == 1

    _patch_basics(monkeypatch, ["MANUAL"])
    _Package.result = "https://files.invalid/package"
    monkeypatch.setattr(tracker_upload_service.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: False)
    _run(_meta(tmp_path, ["MANUAL"], unattended=False), _config(), {}, api=())

    cleanup = AsyncMock()
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "reset_terminal", lambda: None)
    _patch_basics(monkeypatch, ["MANUAL"])
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "reset_terminal", lambda: None)
    monkeypatch.setattr(tracker_upload_service.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(OperationAbortedError, match="Manual tracker"):
        _run(_meta(tmp_path, ["MANUAL"], unattended=False, discs=[{}, {}]), _config(), {})


def test_ptp_success_failure_exception_and_debug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["PASSTHEPOPCORN"])
    success = _meta(tmp_path, ["PASSTHEPOPCORN"])
    client = _run(success, _config(), {})
    assert success.tracker_status["PASSTHEPOPCORN"]["upload_success"] and client.added == ["PASSTHEPOPCORN"]

    _PTP.upload_result = False
    failure = _meta(tmp_path, ["PASSTHEPOPCORN"])
    _run(failure, _config(), {})
    assert failure.tracker_status["PASSTHEPOPCORN"]["upload_success"] is False

    _PTP.upload_result = RuntimeError("upload failed")
    error = _meta(tmp_path, ["PASSTHEPOPCORN"])
    _run(error, _config(), {})
    assert not error.tracker_status["PASSTHEPOPCORN"].get("upload_success")

    _PTP.upload_result = True
    debug = _meta(tmp_path, ["PASSTHEPOPCORN"], debug=True)
    client = _run(debug, _config(), {})
    assert client.added == []


def test_concurrent_exception_and_sequential_multi_disc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["MISSING"])
    concurrent = _meta(tmp_path, ["MISSING"])
    _run(concurrent, _config(), {}, api=("MISSING",))

    _patch_basics(monkeypatch, ["TEST"])
    sequential = _meta(tmp_path, ["TEST"], discs=[{}, {}])
    _run(sequential, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert sequential.tracker_status["TEST"]["upload_success"]

    bandwidth = _meta(tmp_path, ["TEST"], qbit_bandwidth_control=True)
    _run(bandwidth, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert bandwidth.tracker_status["TEST"]["upload_success"]


def test_remaining_smart_host_argument_factory_dupe_name_and_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["TEST"])
    monkeypatch.setattr(tracker_upload_service, "has_restricted_image_hosts", lambda *_args: True)
    restricted = _meta(tmp_path, ["TEST"], imghost_from_cli=False)
    _run(restricted, _config(smart_image_host_selection=True), {"TEST": _Tracker}, api=("TEST",))

    prepared = _meta(tmp_path, ["TEST"], name="Release DUPE?")
    real_prepare_tracker_meta = tracker_upload_service.prepare_tracker_meta
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(tracker_upload_service, "prepare_tracker_meta", prepare)
    asyncio.run(
        tracker_upload_service.process_trackers(
            prepared,
            _config(),
            _Client(),
            ["TEST"],
            {"TEST": _Tracker},
            [],
            [],
            argument_parser_factory=lambda _config: object(),
        )
    )
    assert prepared.name == "Release"
    assert len(prepare.await_args.args) == 4
    monkeypatch.setattr(tracker_upload_service, "prepare_tracker_meta", real_prepare_tracker_meta)

    class LST(_Tracker):
        tracker = "LST"
        flags: ClassVar[dict[str, object]] = {"modq": "yes", "draft": "true"}

    _patch_basics(monkeypatch, ["LST"])
    flagged = _meta(tmp_path, ["LST"])
    _run(flagged, _config(), {"LST": LST}, api=("LST",))
    assert flagged.tracker_status["LST"]["upload_success"] is True


def test_remaining_http_bandwidth_screenshot_dupe_and_modqueue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Http(_Tracker):
        tracker = "HTTP"
        search_result: ClassVar[object] = [{"name": "new", "size": 2}]

    _patch_basics(monkeypatch, ["HTTP"])
    _Wait.result = True
    _DupeChecker.result = [{"name": "new", "size": 2}]

    class DupeHelper:
        def __init__(self, _config: dict[str, Any]) -> None:
            pass

        async def dupe_check(self, _dupes: list[Any], meta: Meta, _tracker: str) -> tuple[bool, Meta]:
            return True, meta

    monkeypatch.setattr("src.services.upload_decision_service.UploadHelper", DupeHelper)
    bandwidth = _meta(
        tmp_path,
        ["HTTP"],
        qbit_bandwidth_control=True,
        qbit_bandwidth_threshold=1,
        qbit_bandwidth_time=1,
        initial_dupes={"HTTP": []},
    )
    _run(bandwidth, _config(), {"HTTP": Http}, http=("HTTP",))
    assert "new dupe" in bandwidth.tracker_status["HTTP"]["status_message"]

    _patch_basics(monkeypatch, ["HTTP"])
    monkeypatch.setattr(tracker_upload_service, "screenshot_requirement_error", lambda *_args: "Need screenshots")
    screenshots = _meta(tmp_path, ["HTTP"])
    _run(screenshots, _config(), {"HTTP": Http}, http=("HTTP",))
    assert screenshots.tracker_status["HTTP"]["skipped"] is True

    _patch_basics(monkeypatch, ["HTTP"])
    dupe = _meta(tmp_path, ["HTTP"], tracker_status={"HTTP": {"upload": True, "dupe": True, "upload_success": True}})
    _run(dupe, _config(), {"HTTP": Http}, http=("HTTP",))
    assert "upload_success" not in dupe.tracker_status["HTTP"]

    class Modqueue(Http):
        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["HTTP"]["status_message"] = "Modqueue limit reached"
            return False

    _patch_basics(monkeypatch, ["HTTP"])
    config = _config()
    failed = _meta(tmp_path, ["HTTP"])
    _run(failed, config, {"HTTP": Modqueue}, http=("HTTP",))
    assert config["_runtime"]["disabled_trackers"]["HTTP"] == "Modqueue limit reached"


def test_remaining_manual_error_url_ptp_outer_error_and_one_disc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Api(_Tracker):
        tracker = "API"

    _patch_basics(monkeypatch, ["MANUAL", "API"])
    monkeypatch.setattr(tracker_upload_service, "check_tracker_image_hosts", AsyncMock(side_effect=RuntimeError("manual failed")))
    _Package.result = "https://files.invalid/final"
    _run(_meta(tmp_path, ["MANUAL"], unattended=True), _config(), {"API": Api}, api=("API",))

    _patch_basics(monkeypatch, ["PASSTHEPOPCORN"])

    class BrokenPTP:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError("PTP construction failed")

    monkeypatch.setattr(tracker_upload_service, "PassThePopcorn", BrokenPTP)
    _run(_meta(tmp_path, ["PASSTHEPOPCORN"]), _config(), {})

    _patch_basics(monkeypatch, ["TEST"])
    one_disc = _meta(tmp_path, ["TEST"], discs=[{}])
    _run(one_disc, _config(), {"TEST": _Tracker}, api=("TEST",))
    assert one_disc.tracker_status["TEST"]["upload_success"] is True


def test_remaining_host_parser_name_flags_and_one_disc_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["LST"])
    monkeypatch.setattr(tracker_upload_service, "has_restricted_image_hosts", lambda *_args: True)

    class LST(_Tracker):
        tracker = "LST"
        flags: ClassVar[dict[str, object]] = {"modq": "yes", "draft": "yes"}

    meta = _meta(tmp_path, ["LST"], name="Release DUPE?", discs=[{}])
    asyncio.run(
        tracker_upload_service.process_trackers(
            meta,
            _config(smart_image_host_selection=True),
            _Client(),
            ("LST",),
            {"LST": LST},
            (),
            (),
            argument_parser_factory=lambda _config: object(),
        )
    )
    assert meta.tracker_status["LST"]["upload_success"] is True


def test_other_tracker_bandwidth_screenshot_dupe_and_modqueue_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Http(_Tracker):
        tracker = "HTTP"

    _patch_basics(monkeypatch, ["HTTP"])
    _Wait.result = True
    _DupeChecker.result = [{"name": "new", "size": 2}]

    class DupeHelper:
        def __init__(self, _config: dict[str, Any]) -> None:
            pass

        async def dupe_check(self, _dupes: list[Any], meta: Meta, _tracker: str) -> tuple[bool, Meta]:
            return True, meta

    monkeypatch.setattr("src.services.upload_decision_service.UploadHelper", DupeHelper)
    Http.search_result = [{"name": "new", "size": 2}]
    bandwidth = _meta(
        tmp_path,
        ["HTTP"],
        qbit_bandwidth_control=True,
        qbit_bandwidth_threshold=1,
        qbit_bandwidth_time=1,
        initial_dupes={"HTTP": []},
    )
    _run(bandwidth, _config(), {"HTTP": Http}, other=("HTTP",))
    assert "new dupe" in bandwidth.tracker_status["HTTP"]["status_message"]

    _patch_basics(monkeypatch, ["HTTP"])
    monkeypatch.setattr(tracker_upload_service, "screenshot_requirement_error", lambda *_args: "Missing screenshots")
    screenshot = _meta(tmp_path, ["HTTP"])
    _run(screenshot, _config(), {"HTTP": Http}, http=("HTTP",))
    assert screenshot.tracker_status["HTTP"]["skipped"] is True

    _patch_basics(monkeypatch, ["HTTP"])
    dupe = _meta(tmp_path, ["HTTP"], tracker_status={"HTTP": {"upload": True, "dupe": True, "upload_success": True}})
    _run(dupe, _config(), {"HTTP": Http}, other=("HTTP",))
    assert "upload_success" not in dupe.tracker_status["HTTP"]

    class Modqueue(Http):
        async def upload(self, meta: Meta) -> bool:
            meta.tracker_status["HTTP"]["status_message"] = "Modqueue limit reached"
            return False

    _patch_basics(monkeypatch, ["HTTP"])
    config = _config()
    failed = _meta(tmp_path, ["HTTP"])
    _run(failed, config, {"HTTP": Modqueue}, other=("HTTP",))
    assert config["_runtime"]["disabled_trackers"]["HTTP"] == "Modqueue limit reached"


def test_manual_success_preparation_error_and_concurrent_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["MANUAL", "BAD"])

    class Bad(_Tracker):
        tracker = "BAD"

    calls = 0

    async def rehost(_meta: Meta, tracker: object) -> None:
        nonlocal calls
        calls += 1
        if getattr(tracker, "tracker", "") == "BAD":
            raise RuntimeError("manual prep failed")

    monkeypatch.setattr(tracker_upload_service, "check_tracker_image_hosts", rehost)
    _Package.result = "https://files.invalid/package"
    manual = _meta(tmp_path, ["MANUAL"], unattended=True)
    _run(manual, _config(), {"BAD": Bad})
    assert calls == 1

    _patch_basics(monkeypatch, ["MANUAL"])
    cleanup = AsyncMock()
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "cleanup", cleanup)
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "reset_terminal", lambda: None)
    monkeypatch.setattr(tracker_upload_service.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(OperationAbortedError, match="Manual tracker"):
        _run(_meta(tmp_path, ["MANUAL"], unattended=False), _config(), {})
    cleanup.assert_awaited_once()


def test_ptp_outer_exception_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["PASSTHEPOPCORN"])

    class BrokenPTP:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("constructor failed")

    monkeypatch.setattr(tracker_upload_service, "PassThePopcorn", BrokenPTP)
    meta = _meta(tmp_path, ["PASSTHEPOPCORN"])
    _run(meta, _config(), {})
    assert not meta.tracker_status["PASSTHEPOPCORN"].get("upload_success")


def test_concurrent_manual_abort_is_propagated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_basics(monkeypatch, ["MANUAL"])
    monkeypatch.setattr(tracker_upload_service.cli_ui, "ask_yes_no", lambda *_args, **_kwargs: (_ for _ in ()).throw(EOFError()))
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "cleanup", AsyncMock())
    monkeypatch.setattr(tracker_upload_service.cleanup_manager, "reset_terminal", lambda: None)
    with pytest.raises(OperationAbortedError, match="Manual tracker"):
        _run(_meta(tmp_path, ["MANUAL"], unattended=False), _config(), {})
