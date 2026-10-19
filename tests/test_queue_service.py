from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain_models.errors import (
    NoWorkAvailableError,
    OperationAbortedError,
)
from src.domain_models.release import Meta
from src.services import queue_service
from src.services.queue_service import QueueManager


def _run(awaitable):
    return asyncio.run(awaitable)


def test_queue_helpers_dedupe_paths_and_names(tmp_path: Path) -> None:
    assert queue_service._dedupe_paths(["a", "b", "a", Path("b")]) == [
        "a",
        "b",
    ]
    assert (
        queue_service._queue_log_path(tmp_path, "My Queue", ".json")
        == tmp_path / "My_Queue.json"
    )
    for value in ("", ".", "..", "../escape", r"..\escape", "bad\0name"):
        with pytest.raises(ValueError, match="Invalid queue name"):
            queue_service._queue_log_path(tmp_path, value, ".json")


def test_trusted_existing_queue_log_modes() -> None:
    uid = os.geteuid()
    regular = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600, st_uid=uid, st_file_attributes=0
    )
    directory = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_file_attributes=0
    )
    foreign = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600, st_uid=uid + 1, st_file_attributes=0
    )
    reparse = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=uid,
        st_file_attributes=getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ),
    )
    assert queue_service._trusted_existing_queue_log(regular, windows=False)
    assert queue_service._trusted_existing_queue_log(foreign, windows=True)
    assert not queue_service._trusted_existing_queue_log(
        directory, windows=False
    )
    assert not queue_service._trusted_existing_queue_log(
        foreign, windows=False
    )
    assert not queue_service._trusted_existing_queue_log(reparse, windows=True)


def test_expand_multi_format_ebook_directories_for_strings_and_items(
    tmp_path: Path,
) -> None:
    release = tmp_path / "Books"
    release.mkdir()
    first = release / "First.epub"
    second = release / "Second.pdf"
    first.write_bytes(b"epub")
    second.write_bytes(b"pdf")
    (release / "notes.txt").write_text("notes", encoding="utf-8")

    expanded = queue_service._expand_multi_format_ebook_directories(
        [str(release), "plain"]
    )
    assert expanded == [str(first.resolve()), str(second.resolve()), "plain"]

    item = {
        "path": str(release),
        "args": [str(release), "-tk", "SITE"],
        "line": "original",
    }
    no_path = {"args": ["value"]}
    expanded_items = queue_service._expand_multi_format_ebook_directories(
        [item, no_path]
    )
    assert [entry.get("path") for entry in expanded_items] == [
        str(first.resolve()),
        str(second.resolve()),
        None,
    ]
    assert expanded_items[0]["args"] == [str(first.resolve()), "-tk", "SITE"]
    assert str(first.resolve()) in expanded_items[0]["line"]

    single = tmp_path / "Single"
    single.mkdir()
    (single / "Only.epub").write_bytes(b"epub")
    assert queue_service._expand_multi_format_ebook_directories(
        [str(single)]
    ) == [str(single)]

    audio = tmp_path / "Audiobook"
    audio.mkdir()
    (audio / "Book.epub").write_bytes(b"epub")
    (audio / "Book.pdf").write_bytes(b"pdf")
    (audio / "Book.mp3").write_bytes(b"audio")
    assert queue_service._expand_multi_format_ebook_directories(
        [str(audio)]
    ) == [str(audio)]
    mixed: list[Any] = ["plain", {"path": str(release)}]
    assert queue_service._expand_multi_format_ebook_directories(mixed) is mixed


def test_json_and_text_io_create_replace_and_reject_untrusted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "queue.json"
    _run(queue_service._write_json_file(path, {"items": [1]}, indent=2))
    assert json.loads(path.read_text(encoding="utf-8")) == {"items": [1]}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    _run(queue_service._write_json_file(path, ["changed"]))
    assert _run(queue_service._read_json_file(path)) == ["changed"]

    text = tmp_path / "lines.txt"
    text.write_text("one\ntwo\n", encoding="utf-8")
    assert _run(queue_service._read_text_lines(str(text))) == ["one", "two"]

    if os.name != "nt":
        target = tmp_path / "target"
        target.write_text("safe", encoding="utf-8")
        link = tmp_path / "link"
        link.symlink_to(target)
        with pytest.raises(PermissionError, match="untrusted queue log"):
            _run(queue_service._write_json_file(link, ["unsafe"]))
        assert target.read_text(encoding="utf-8") == "safe"


def test_process_site_upload_queue_all_file_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(
        QueueManager.process_site_upload_queue(Meta(), str(tmp_path))
    ) == ([], None)

    meta = Meta(site_upload="SITE")
    assert _run(
        QueueManager.process_site_upload_queue(meta, str(tmp_path))
    ) == ([], None)

    state = tmp_path / "tmp"
    results = state / "SITE_search_results.json"
    results.write_text("not-json", encoding="utf-8")
    assert _run(
        QueueManager.process_site_upload_queue(meta, str(tmp_path))
    ) == ([], None)

    results.write_text(
        json.dumps(
            [
                {"path": "/one", "imdb_id": 1},
                {"path": "/one", "imdb_id": 2},
                {"path": "/two", "imdb_id": None},
                {"path": "", "imdb_id": 3},
                {"path": "/three"},
            ]
        ),
        encoding="utf-8",
    )
    processed = state / "SITE_processed_paths.log"
    processed.write_text("bad", encoding="utf-8")
    queue, log = _run(
        QueueManager.process_site_upload_queue(meta, str(tmp_path))
    )
    assert queue == [{"path": "/one", "imdb_id": 1, "tracker": "SITE"}]
    assert log == str(processed)

    processed.write_text(json.dumps(["/one"]), encoding="utf-8")
    queue, _ = _run(
        QueueManager.process_site_upload_queue(meta, str(tmp_path))
    )
    assert queue == []

    class Raising(dict[str, Any]):
        def get(self, key: str, default: object = None) -> object:
            if key == "imdb_id":
                raise KeyError(key)
            return super().get(key, default)

    (state / "CUSTOM_search_results.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        queue_service,
        "_read_json_file",
        AsyncMock(return_value=[Raising(path="/raised")]),
    )
    queue, _ = _run(
        QueueManager.process_site_upload_queue(
            Meta(site_upload="CUSTOM"), str(tmp_path)
        )
    )
    assert queue == [{"path": "/raised", "imdb_id": 0, "tracker": "CUSTOM"}]


def test_process_item_save_and_load_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = Meta()
    assert (
        _run(
            QueueManager.process_site_upload_item(
                {"tracker": "SITE", "imdb_id": "tt1", "path": "/release"}, meta
            )
        )
        == "/release"
    )
    assert meta.trackers == ["SITE"] and meta.imdb_id == "tt1"

    log = tmp_path / "processed.json"
    _run(QueueManager.save_processed_path(str(log), "/one"))
    _run(QueueManager.save_processed_path(str(log), "/one"))
    _run(QueueManager.save_processed_path(str(log), "/two"))
    assert _run(QueueManager.load_processed_files(str(log))) == {
        "/one",
        "/two",
    }
    assert (
        _run(QueueManager.load_processed_files(str(tmp_path / "missing")))
        == set()
    )
    assert _run(QueueManager.get_log_file(str(tmp_path), "my queue")).endswith(
        "my_queue_processed_files.log"
    )

    log.write_text("bad", encoding="utf-8")
    _run(QueueManager.save_processed_path(str(log), "/fresh"))
    assert _run(QueueManager.load_processed_files(str(log))) == {"/fresh"}

    monkeypatch.setattr(
        queue_service,
        "_write_json_file",
        AsyncMock(side_effect=OSError("read only")),
    )
    _run(
        QueueManager.save_processed_path(
            str(tmp_path / "cannot-write"), "/ignored"
        )
    )


def test_gather_files_recursive_directory_file_bytes_errors_and_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    movie = root / "movie.mkv"
    ignored = root / "ignored.txt"
    movie.write_bytes(b"movie")
    ignored.write_text("ignored", encoding="utf-8")
    disc = root / "disc"
    (disc / "BDMV").mkdir(parents=True)
    empty = root / "empty"
    empty.mkdir()

    result = _run(
        QueueManager.gather_files_recursive(str(root).encode(), [".mkv"])
    )
    assert (
        str(movie) in result
        and str(disc) in result
        and str(ignored) not in result
        and str(empty) not in result
    )
    assert _run(QueueManager.gather_files_recursive(str(movie), [".mkv"])) == [
        str(movie)
    ]
    assert (
        _run(QueueManager.gather_files_recursive(str(ignored), [".mkv"])) == []
    )
    assert (
        _run(QueueManager.gather_files_recursive(str(tmp_path / "missing")))
        == []
    )

    import unicodedata

    with monkeypatch.context() as context:
        context.setattr(
            unicodedata,
            "normalize",
            lambda *_args: (_ for _ in ()).throw(ValueError("bad unicode")),
        )
        assert _run(
            QueueManager.gather_files_recursive(str(movie), [".mkv"])
        ) == [str(movie)]

    real_scandir = queue_service.os.scandir

    def fail_scandir(path: object):
        if not isinstance(path, int) and Path(path) == root:
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(queue_service.os, "scandir", fail_scandir)
    assert _run(QueueManager.gather_files_recursive(str(root))) == []


class _Entry:
    def __init__(
        self,
        path: Path,
        *,
        directory: bool = False,
        file: bool = False,
        fail: bool = False,
    ) -> None:
        self.path = str(path)
        self.name = path.name
        self._directory = directory
        self._file = file
        self._fail = fail

    def is_dir(self) -> bool:
        if self._fail:
            raise OSError("bad entry")
        return self._directory

    def is_file(self) -> bool:
        if self._fail:
            raise UnicodeError("bad entry")
        return self._file


def test_process_scandir_entry_direct_fallback_and_exclusion(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "movie.mkv").write_bytes(b"movie")
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"movie")
    ignored = tmp_path / "ignored.txt"
    ignored.write_text("ignored", encoding="utf-8")

    assert _run(
        QueueManager._process_scandir_entry(
            _Entry(folder, directory=True), str(tmp_path), (".mkv",), [".mkv"]
        )
    ) == [str(folder)]
    assert _run(
        QueueManager._process_scandir_entry(
            _Entry(movie, file=True), str(tmp_path), (".mkv",), [".mkv"]
        )
    ) == [str(movie)]
    assert (
        _run(
            QueueManager._process_scandir_entry(
                _Entry(ignored, file=True), str(tmp_path), (".mkv",), [".mkv"]
            )
        )
        == []
    )
    assert _run(
        QueueManager._process_scandir_entry(
            _Entry(movie, fail=True), str(tmp_path), (".mkv",), [".mkv"]
        )
    ) == [str(movie)]


def test_should_include_directory_disc_extensions_files_empty_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disc = tmp_path / "disc"
    (disc / "VIDEO_TS").mkdir(parents=True)
    assert _run(QueueManager.should_include_directory(disc, [".mkv"]))

    movie = tmp_path / "movie"
    movie.mkdir()
    (movie / "release.mkv").write_bytes(b"movie")
    assert _run(QueueManager.should_include_directory(movie, [".mkv"]))
    assert _run(QueueManager.should_include_directory(movie))

    empty = tmp_path / "empty"
    empty.mkdir()
    assert not _run(QueueManager.should_include_directory(empty, [".mkv"]))
    assert not _run(QueueManager.should_include_directory(empty))

    real_scandir = queue_service.os.scandir
    monkeypatch.setattr(
        queue_service.os,
        "scandir",
        lambda path: (
            (_ for _ in ()).throw(PermissionError("denied"))
            if Path(path) == movie
            else real_scandir(path)
        ),
    )
    assert not _run(QueueManager.should_include_directory(movie))


def test_split_glob_and_multi_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "First File.mkv"
    second = tmp_path / "Second.mkv"
    ignored = tmp_path / "Ignored.txt"
    first.write_bytes(b"movie")
    second.write_bytes(b"movie")
    ignored.write_text("ignored", encoding="utf-8")

    assert _run(QueueManager._resolve_split_path("")) == []
    assert _run(QueueManager._resolve_split_path(f"{first} {second}")) == [
        str(first),
        str(second),
    ]
    assert (
        _run(QueueManager._resolve_split_path(str(tmp_path / "missing path")))
        == []
    )

    displayed = AsyncMock()
    monkeypatch.setattr(QueueManager, "display_queue", displayed)
    globbed = _run(
        QueueManager.resolve_queue_with_glob_or_split(
            str(tmp_path / "*.mkv"), [str(tmp_path / "*.mkv")], [".mkv"]
        )
    )
    assert set(globbed) == {str(first), str(second)}
    multi = _run(
        QueueManager.resolve_queue_with_glob_or_split(
            str(first), [str(first), str(second), str(ignored)], [".mkv"]
        )
    )
    assert multi == [str(first), str(second)]
    monkeypatch.setattr(
        QueueManager,
        "_resolve_split_path",
        AsyncMock(return_value=[str(first), str(ignored)]),
    )
    split = _run(
        QueueManager.resolve_queue_with_glob_or_split(
            str(tmp_path / "missing" / "path"), [], [".mkv"]
        )
    )
    assert split == [str(first)]
    assert displayed.await_count == 3


def test_extract_safe_locations_and_display_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "scan.log"
    log.write_text(
        "Safe\nFile Location: /one\nFile Location: /two\nDanger\nFile Location: /ignored\nSafe\nnot a location\nRisky\n",
        encoding="utf-8",
    )
    assert _run(QueueManager.extract_safe_file_locations(str(log))) == [
        "/one",
        "/two",
    ]

    _run(
        QueueManager.display_queue(
            [{"path": "/one"}, {"line": "--flag"}, "/two"],
            str(tmp_path),
            "queue",
            save_to_log=True,
        )
    )
    saved = tmp_path / "tmp" / "queue_queue.log"
    assert json.loads(saved.read_text(encoding="utf-8")) == [
        "/one",
        "--flag",
        "/two",
    ]
    _run(QueueManager.display_queue(["plain"], save_to_log=False))

    monkeypatch.setattr(
        queue_service,
        "_write_json_file",
        AsyncMock(side_effect=OSError("read only")),
    )
    _run(
        QueueManager.display_queue(
            ["plain"], str(tmp_path), "broken", save_to_log=True
        )
    )


def test_handle_site_text_debug_unit3d_and_stdin_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_meta = Meta(site_upload="SITE")
    monkeypatch.setattr(
        QueueManager,
        "process_site_upload_queue",
        AsyncMock(return_value=([{"path": "/one"}], "/processed")),
    )
    assert _run(
        QueueManager.handle_queue("ignored", site_meta, [], str(tmp_path))
    ) == ([{"path": "/one"}], "/processed")
    assert (
        site_meta.queue == "SITE_upload"
        and site_meta.site_upload_queue is True
    )
    monkeypatch.setattr(
        QueueManager,
        "process_site_upload_queue",
        AsyncMock(return_value=([], None)),
    )
    assert _run(
        QueueManager.handle_queue(
            "ignored", Meta(site_upload="SITE"), [], str(tmp_path)
        )
    ) == ([], None)

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(
        "# comment\n\n'/one path' -tk SITE\n/one -debug\n/one -debug\n\"unterminated\n",
        encoding="utf-8",
    )
    meta = Meta(debug=True)
    queue, log = _run(
        QueueManager.handle_queue(
            str(manifest), meta, [str(manifest)], str(tmp_path)
        )
    )
    assert [item["path"] for item in queue] == ["/one path", "/one"]
    assert (
        meta.args_line_queue is True
        and meta.queue == "manifest"
        and log is not None
    )

    processed = Path(log)
    processed.write_text(json.dumps(["/one path", "/one"]), encoding="utf-8")
    with pytest.raises(NoWorkAvailableError, match="already been processed"):
        _run(
            QueueManager.handle_queue(
                str(manifest), Meta(), [str(manifest)], str(tmp_path)
            )
        )
    with pytest.raises(NoWorkAvailableError, match="not found"):
        _run(
            QueueManager.handle_queue(
                str(tmp_path / "missing.txt"), Meta(), [], str(tmp_path)
            )
        )

    stdin = tmp_path / "content.txt"
    stdin.write_text("content", encoding="utf-8")
    assert _run(
        QueueManager.handle_queue(
            str(stdin),
            Meta(paths_from_stdin=True),
            [str(stdin)],
            str(tmp_path),
        )
    )[0] == [str(stdin)]

    unit_log = tmp_path / "unit.txt"
    unit_log.write_text(
        "Safe\nFile Location: /safe\nDanger\n", encoding="utf-8"
    )
    unit_meta = Meta(unit3d=True)
    queue, _ = _run(
        QueueManager.handle_queue(
            str(unit_log), unit_meta, [str(unit_log)], str(tmp_path)
        )
    )
    assert queue == ["/safe"] and unit_meta.queue == "unit3d"
    empty_safe = tmp_path / "empty-safe.txt"
    empty_safe.write_text("Danger\n", encoding="utf-8")
    with pytest.raises(NoWorkAvailableError, match="No safe"):
        _run(
            QueueManager.handle_queue(
                str(empty_safe), Meta(unit3d=True), [], str(tmp_path)
            )
        )
    with pytest.raises(NoWorkAvailableError, match="not found"):
        _run(
            QueueManager.handle_queue(
                str(tmp_path / "missing-safe.txt"),
                Meta(unit3d=True),
                [],
                str(tmp_path),
            )
        )

    debug = tmp_path / "debug.log"
    debug.write_text(json.dumps(["/debug"]), encoding="utf-8")
    debug_meta = Meta(debug=True)
    queue, log = _run(
        QueueManager.handle_queue(str(debug), debug_meta, [], str(tmp_path))
    )
    assert (
        queue == ["/debug"]
        and log is not None
        and log.endswith("debugging_processed_files.log")
        and debug_meta.queue == "debugging"
    )
    with pytest.raises(NoWorkAvailableError, match="Debug queue"):
        _run(
            QueueManager.handle_queue(
                str(tmp_path / "missing.log"),
                Meta(debug=True),
                [],
                str(tmp_path),
            )
        )


def test_handle_text_manifest_write_and_unexpected_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("/one\n", encoding="utf-8")
    real_write = queue_service._write_json_file
    monkeypatch.setattr(
        queue_service,
        "_write_json_file",
        AsyncMock(side_effect=OSError("read only")),
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            str(manifest), Meta(), [str(manifest)], str(tmp_path)
        )
    )
    assert queue[0]["path"] == "/one"
    monkeypatch.setattr(queue_service, "_write_json_file", real_write)

    real_split = queue_service.shlex.split
    monkeypatch.setattr(
        queue_service.shlex,
        "split",
        lambda value, **_kwargs: (
            (_ for _ in ()).throw(RuntimeError("unexpected"))
            if value == "/bad"
            else real_split(value, posix=False)
        ),
    )
    manifest.write_text("/bad\n/good\n", encoding="utf-8")
    queue, _ = _run(
        QueueManager.handle_queue(
            str(manifest), Meta(), [str(manifest)], str(tmp_path)
        )
    )
    assert [item["path"] for item in queue] == ["/good"]

    monkeypatch.setattr(
        queue_service,
        "_write_json_file",
        AsyncMock(side_effect=OSError("unit write")),
    )
    unit_log = tmp_path / "unit.txt"
    unit_log.write_text("Safe\nFile Location: /safe\n", encoding="utf-8")
    with pytest.raises(OperationAbortedError, match="Failed to save"):
        _run(
            QueueManager.handle_queue(
                str(unit_log), Meta(unit3d=True), [], str(tmp_path)
            )
        )


def _queue_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "media"
    root.mkdir(exist_ok=True)
    first = root / "first.mkv"
    second = root / "second.mkv"
    third = root / "third.mkv"
    for path in (first, second, third):
        path.write_bytes(b"movie")
    return first, second, third


def _write_existing_queue(
    tmp_path: Path, name: str, values: list[str]
) -> Path:
    path = tmp_path / "tmp" / f"{name}_queue.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


@pytest.mark.parametrize("choice", ["u", "a", "e", "d", "", "invalid"])
def test_handle_existing_queue_with_changes_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str
) -> None:
    first, second, third = _queue_paths(tmp_path)
    _write_existing_queue(tmp_path, "named", [str(first), str(second)])
    answers = [choice]
    if choice == "a":
        answers.append("1,bad")
    monkeypatch.setattr(
        queue_service.cli_ui,
        "ask_string",
        lambda *_args, **_kwargs: answers.pop(0),
    )
    if choice == "e":
        monkeypatch.setattr(
            queue_service.click,
            "edit",
            lambda _value: json.dumps([str(third), str(third)]),
        )
    meta = Meta(queue="named", debug=True)
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent), meta, [str(first.parent)], str(tmp_path)
        )
    )
    assert queue
    if choice in {"u", "d"}:
        assert set(queue) == {str(first), str(second), str(third)}
    elif choice == "e":
        assert queue == [str(third)]
    else:
        assert str(first) in queue


def test_handle_existing_queue_choice_error_editor_invalid_none_and_unattended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = _queue_paths(tmp_path)
    _write_existing_queue(tmp_path, "named", [str(first), str(second)])

    monkeypatch.setattr(
        queue_service.cli_ui, "ask_string", lambda *_args, **_kwargs: "a"
    )
    monkeypatch.setattr(
        queue_service,
        "_write_json_file",
        AsyncMock(side_effect=OSError("write failed")),
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="named"),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert queue == [str(first), str(second)]

    real_write = queue_service._write_json_file
    monkeypatch.setattr(queue_service, "_write_json_file", real_write)
    monkeypatch.setattr(
        queue_service.cli_ui, "ask_string", lambda *_args, **_kwargs: "e"
    )
    monkeypatch.setattr(queue_service.click, "edit", lambda _value: "not-json")
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="named"),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert set(queue) == {str(first), str(second), str(third)}
    monkeypatch.setattr(queue_service.click, "edit", lambda _value: None)
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="named"),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert set(queue) == {str(first), str(second), str(third)}

    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="named", unattended=True),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert queue == [str(first), str(second)]


def test_handle_existing_queue_without_changes_all_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = _queue_paths(tmp_path)
    existing = [str(first), str(second), str(third)]
    _write_existing_queue(tmp_path, "named", existing)
    for choice, editor, expected in (
        ("e", json.dumps([str(first)]), [str(first)]),
        ("e", "not-json", existing),
        ("e", None, existing),
        ("d", None, existing),
        ("", None, existing),
    ):
        _write_existing_queue(tmp_path, "named", existing)
        monkeypatch.setattr(
            queue_service.cli_ui,
            "ask_string",
            lambda *_args, _choice=choice, **_kwargs: _choice,
        )
        monkeypatch.setattr(
            queue_service.click, "edit", lambda _value, _editor=editor: _editor
        )
        queue, _ = _run(
            QueueManager.handle_queue(
                str(first.parent),
                Meta(queue="named"),
                [str(first.parent)],
                str(tmp_path),
            )
        )
        if choice == "d":
            assert set(queue) == set(expected)
        else:
            assert queue == expected
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="named", unattended=True),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert queue == existing


def test_handle_new_named_queue_edits_and_processed_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, _ = _queue_paths(tmp_path)
    monkeypatch.setattr(
        queue_service.cli_ui, "ask_string", lambda *_args, **_kwargs: "e"
    )
    monkeypatch.setattr(
        queue_service.click,
        "edit",
        lambda _value: json.dumps([str(second), str(second)]),
    )
    meta = Meta(queue="new", debug=True)
    queue, log = _run(
        QueueManager.handle_queue(
            str(first.parent), meta, [str(first.parent)], str(tmp_path)
        )
    )
    assert queue == [str(second)] and log is not None

    queue_log = tmp_path / "tmp" / "new_queue.log"
    queue_log.unlink()
    monkeypatch.setattr(queue_service.click, "edit", lambda _value: "not-json")
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="new"),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert str(first) in queue and str(second) in queue

    queue_log.unlink()
    monkeypatch.setattr(queue_service.click, "edit", lambda _value: None)
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent),
            Meta(queue="new"),
            [str(first.parent)],
            str(tmp_path),
        )
    )
    assert queue

    processed_log = Path(_run(QueueManager.get_log_file(str(tmp_path), "new")))
    processed_log.write_text(
        json.dumps(
            [str(first), str(second), str(tmp_path / "media" / "third.mkv")]
        ),
        encoding="utf-8",
    )
    with pytest.raises(NoWorkAvailableError, match="already been processed"):
        _run(
            QueueManager.handle_queue(
                str(first.parent),
                Meta(queue="new"),
                [str(first.parent)],
                str(tmp_path),
            )
        )


def test_handle_generic_multi_existing_glob_split_and_no_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, _ = _queue_paths(tmp_path)
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first),
            Meta(),
            [str(first), str(second), str(first)],
            str(tmp_path),
        )
    )
    assert queue == [str(first), str(second)]
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first), Meta(), [str(first)], str(tmp_path)
        )
    )
    assert queue == [str(first)]
    queue, _ = _run(
        QueueManager.handle_queue(
            str(first.parent / "*.mkv"), Meta(), [], str(tmp_path)
        )
    )
    assert set(queue) == {
        str(first),
        str(second),
        str(first.parent / "third.mkv"),
    }
    with pytest.raises(NoWorkAvailableError, match="No valid"):
        _run(
            QueueManager.handle_queue(
                str(first.parent / "*.avi"), Meta(), [], str(tmp_path)
            )
        )

    missing_parent_path = str(tmp_path / "missing" / "one two")
    monkeypatch.setattr(
        QueueManager,
        "_resolve_split_path",
        AsyncMock(return_value=[str(first)]),
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            missing_parent_path, Meta(), [], str(tmp_path)
        )
    )
    assert queue == [str(first)]
    monkeypatch.setattr(
        QueueManager, "_resolve_split_path", AsyncMock(return_value=[])
    )
    with pytest.raises(NoWorkAvailableError, match="No valid"):
        _run(
            QueueManager.handle_queue(
                missing_parent_path, Meta(), [], str(tmp_path)
            )
        )


def test_module_level_queue_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meta = Meta()
    monkeypatch.setattr(
        QueueManager,
        "process_site_upload_queue",
        AsyncMock(return_value=([{"path": "/one"}], "/log")),
    )
    assert _run(
        queue_service.process_site_upload_queue(meta, str(tmp_path))
    ) == ([{"path": "/one"}], "/log")
    monkeypatch.setattr(
        QueueManager,
        "process_site_upload_item",
        AsyncMock(return_value="/item"),
    )
    assert _run(queue_service.process_site_upload_item({}, meta)) == "/item"
    save = AsyncMock()
    monkeypatch.setattr(QueueManager, "save_processed_path", save)
    _run(queue_service.save_processed_path("/log", "/item"))
    save.assert_awaited_once()
    monkeypatch.setattr(
        QueueManager, "get_log_file", AsyncMock(return_value="/log")
    )
    assert _run(queue_service.get_log_file(str(tmp_path), "queue")) == "/log"
    monkeypatch.setattr(
        QueueManager, "load_processed_files", AsyncMock(return_value={"/one"})
    )
    assert _run(queue_service.load_processed_files("/log")) == {"/one"}
    monkeypatch.setattr(
        QueueManager,
        "gather_files_recursive",
        AsyncMock(return_value=["/one"]),
    )
    assert _run(queue_service.gather_files_recursive(b"/root", [".mkv"])) == [
        "/one"
    ]
    monkeypatch.setattr(
        QueueManager, "should_include_directory", AsyncMock(return_value=True)
    )
    assert (
        _run(queue_service.should_include_directory("/root", [".mkv"])) is True
    )
    monkeypatch.setattr(
        QueueManager,
        "resolve_queue_with_glob_or_split",
        AsyncMock(return_value=["/one"]),
    )
    assert _run(
        queue_service.resolve_queue_with_glob_or_split("/root", [], [".mkv"])
    ) == ["/one"]
    monkeypatch.setattr(
        QueueManager,
        "extract_safe_file_locations",
        AsyncMock(return_value=["/one"]),
    )
    assert _run(queue_service.extract_safe_file_locations("/log")) == ["/one"]
    display = AsyncMock()
    monkeypatch.setattr(QueueManager, "display_queue", display)
    _run(queue_service.display_queue(["/one"], str(tmp_path), "queue", False))
    display.assert_awaited_once()
    monkeypatch.setattr(
        QueueManager,
        "handle_queue",
        AsyncMock(return_value=(["/one"], "/log")),
    )
    assert _run(
        queue_service.handle_queue("/one", meta, ["/one"], str(tmp_path))
    ) == (["/one"], "/log")


def test_manifest_empty_args_existing_and_new_queue_resolution_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("skip-me\n/good\n", encoding="utf-8")
    real_split = queue_service.shlex.split
    monkeypatch.setattr(
        queue_service.shlex,
        "split",
        lambda value, **_kwargs: (
            [] if value == "skip-me" else real_split(value, posix=False)
        ),
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            str(manifest), Meta(), [str(manifest)], str(tmp_path)
        )
    )
    assert [item["path"] for item in queue] == ["/good"]

    first, second, third = _queue_paths(tmp_path)
    missing = str(tmp_path / "missing-root" / "*.mkv")
    _write_existing_queue(
        tmp_path, "existing", [str(first), str(tmp_path / "removed.mkv")]
    )
    monkeypatch.setattr(
        QueueManager,
        "resolve_queue_with_glob_or_split",
        AsyncMock(return_value=[str(first), str(second)]),
    )
    monkeypatch.setattr(
        queue_service.cli_ui, "ask_string", lambda *_args, **_kwargs: ""
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            missing, Meta(queue="existing", debug=True), [], str(tmp_path)
        )
    )
    assert str(first) in queue

    monkeypatch.setattr(
        QueueManager,
        "resolve_queue_with_glob_or_split",
        AsyncMock(return_value=[str(third)]),
    )
    queue, _ = _run(
        QueueManager.handle_queue(
            missing, Meta(queue="new-missing"), [], str(tmp_path)
        )
    )
    assert queue == [str(third)]
