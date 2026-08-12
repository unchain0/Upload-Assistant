# ruff: noqa: S101

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.temp_paths import artwork_dir, ensure_temp_root, menu_screenshots_dir, release_temp_dir, screenshots_dir, spectrograms_dir


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode checks do not apply on Windows")
def test_temp_root_rejects_writable_directory_owned_by_another_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()

    monkeypatch.setattr(Path, "chmod", lambda _path, _mode: (_ for _ in ()).throw(PermissionError("not owner")))
    monkeypatch.setattr(Path, "stat", lambda _path: SimpleNamespace(st_uid=12345, st_mode=stat.S_IFDIR | 0o1777))

    with pytest.raises(PermissionError, match="not owner"):
        ensure_temp_root(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and mode checks do not apply on Windows")
def test_temp_root_accepts_root_owned_sticky_shared_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()

    monkeypatch.setattr(Path, "chmod", lambda _path, _mode: (_ for _ in ()).throw(PermissionError("not owner")))
    monkeypatch.setattr(Path, "stat", lambda _path: SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o1777))

    assert ensure_temp_root(tmp_path) == temp_root


def test_typed_image_directories_are_isolated_per_release(tmp_path: Path) -> None:
    directories = {
        screenshots_dir(tmp_path, "release"),
        artwork_dir(tmp_path, "release"),
        menu_screenshots_dir(tmp_path, "release"),
        spectrograms_dir(tmp_path, "release"),
    }
    other_directories = {
        screenshots_dir(tmp_path, "other-release"),
        artwork_dir(tmp_path, "other-release"),
        menu_screenshots_dir(tmp_path, "other-release"),
        spectrograms_dir(tmp_path, "other-release"),
    }

    assert len(directories) == 4
    assert {path.parent.name for path in directories} == {"release"}
    assert {path.parent.name for path in other_directories} == {"other-release"}
    assert directories.isdisjoint(other_directories)
    assert {path.name for path in directories} == {"screenshots", "artwork", "menu_screenshots", "spectrograms"}
    assert all(path.is_dir() for path in directories)


@pytest.mark.skipif(os.name == "nt", reason="POSIX ownership checks do not apply on Windows")
def test_release_temp_dir_rejects_foreign_owned_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    release = tmp_path / "tmp" / "release"
    release.mkdir(parents=True)
    original_stat = Path.stat

    def foreign_stat(path: Path):  # type: ignore[no-untyped-def]
        attributes = original_stat(path)
        if path == release:
            return SimpleNamespace(st_uid=os.geteuid() + 1, st_mode=attributes.st_mode)
        return attributes

    monkeypatch.setattr(Path, "stat", foreign_stat)

    with pytest.raises(PermissionError, match="owned by another user"):
        release_temp_dir(tmp_path, "release")
