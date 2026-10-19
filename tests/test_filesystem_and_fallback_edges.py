"""Complete edge coverage for runtime paths, manifests, and image fallback."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.integrations.filesystem.paths as paths
import src.integrations.filesystem.screenshot_manifest as manifest
import src.integrations.filesystem.temp_paths as temp_paths
import src.integrations.runtime_tools.runtime_tool_paths as tool_paths
from src.integrations.image_hosts.fallback import (
    configured_image_hosts,
    image_host_fallback_plan,
)


def test_runtime_data_directory_platform_and_migration_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "override"
    monkeypatch.setenv("UA_DATA_DIR", f"  {override}  ")
    assert paths._default_data_dir() == override

    monkeypatch.delenv("UA_DATA_DIR")
    real_os = paths.os
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(
        paths, "os", SimpleNamespace(name="nt", environ=real_os.environ)
    )
    assert paths._default_data_dir() == tmp_path / "local" / "Upload-Assistant"

    monkeypatch.setattr(paths, "os", real_os)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    legacy = tmp_path / "xdg" / "upload-assistant"
    legacy.mkdir(parents=True)
    assert paths._default_data_dir() == legacy
    (tmp_path / "xdg" / "Upload-Assistant").mkdir()
    assert paths._default_data_dir() == tmp_path / "xdg" / "Upload-Assistant"

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "DATA_DIR", state / "data")
    assert paths.ensure_data_dir() == state
    assert state.is_dir() and (state / "data").is_dir()


def test_temp_path_security_permissions_and_typed_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_mode = stat.S_IFDIR | stat.S_ISVTX | stat.S_IWOTH | stat.S_IXOTH
    trusted_root = SimpleNamespace(
        stat=lambda: SimpleNamespace(st_uid=0, st_mode=trusted_mode)
    )
    assert temp_paths._is_trusted_shared_root(trusted_root)  # type: ignore[arg-type]

    root = temp_paths.ensure_temp_root(tmp_path)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert temp_paths._safe_release_id("") == "release-pending"
    for unsafe in (".", "..", "a/b", r"a\b", "bad\x00id", "C:drive"):
        with pytest.raises(ValueError):
            temp_paths._safe_release_id(unsafe)

    release = temp_paths.release_temp_dir(tmp_path, "release")
    assert release.is_dir() and stat.S_IMODE(release.stat().st_mode) == 0o700
    with monkeypatch.context() as context:
        context.setattr(
            temp_paths.os, "geteuid", lambda: release.stat().st_uid + 1
        )
        with pytest.raises(PermissionError, match="owned by another user"):
            temp_paths.release_temp_dir(tmp_path, "foreign-owner")
    assert (
        temp_paths.music_release_snapshot_path(tmp_path, "release").name
        == "music_release.json"
    )
    monkeypatch.setattr(temp_paths, "STATE_DIR", tmp_path / "fallback-state")
    assert "music-release-pending" in str(
        temp_paths.music_release_snapshot_path(None, "")
    )

    assert (
        temp_paths.screenshots_dir(tmp_path, "release").name == "screenshots"
    )
    assert temp_paths.artwork_dir(tmp_path, "release").name == "artwork"
    assert (
        temp_paths.menu_screenshots_dir(tmp_path, "release").name
        == "menu_screenshots"
    )
    assert (
        temp_paths.spectrograms_dir(tmp_path, "release").name == "spectrograms"
    )
    assert (
        temp_paths.dynamic_hdr_plots_dir(tmp_path, "release").name
        == "dynamic_hdr_plots"
    )

    linked_base = tmp_path / "linked-base"
    linked_base.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (linked_base / "tmp").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symbolic link"):
        temp_paths.ensure_temp_root(linked_base)

    release_link = tmp_path / "release-link-base"
    (release_link / "tmp").mkdir(parents=True)
    (release_link / "tmp" / "release").symlink_to(
        target, target_is_directory=True
    )
    with pytest.raises(RuntimeError, match="symbolic link"):
        temp_paths.release_temp_dir(release_link, "release")

    class ChmodFailurePath(type(root)):
        pass

    monkeypatch.setattr(
        temp_paths.Path,
        "chmod",
        lambda _self, _mode: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(
        temp_paths, "_is_trusted_shared_root", lambda _path: True
    )
    assert temp_paths.ensure_temp_root(tmp_path / "trusted").is_dir()
    monkeypatch.setattr(
        temp_paths, "_is_trusted_shared_root", lambda _path: False
    )
    with pytest.raises(PermissionError):
        temp_paths.ensure_temp_root(tmp_path / "untrusted")


def test_private_runtime_tool_paths_and_trusted_executables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    assert tool_paths._is_private_writable_directory(private)
    private.chmod(0o777)
    assert not tool_paths._is_private_writable_directory(private)
    missing = tmp_path / "missing"
    assert not tool_paths._is_private_writable_directory(missing)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    assert not tool_paths._is_private_writable_directory(link)

    with monkeypatch.context() as context:
        context.setattr(
            tool_paths,
            "os",
            SimpleNamespace(
                name="nt",
                access=lambda _path, _mode: True,
                W_OK=os.W_OK,
                X_OK=os.X_OK,
            ),
        )
        assert tool_paths._is_private_writable_directory(private)

    tool_paths._private_root = None
    first = tool_paths._private_tool_root()
    assert first == tool_paths._private_tool_root()
    assert stat.S_IMODE(first.stat().st_mode) == 0o700

    install = tool_paths.tool_install_dir(tmp_path, "ffmpeg", "current")
    assert install == tmp_path / "bin" / "ffmpeg" / "current"

    monkeypatch.setattr(
        tool_paths, "_is_private_writable_directory", lambda _path: False
    )
    fallback = tool_paths.tool_install_dir(tmp_path, "mkv", "current")
    assert fallback == tool_paths._private_tool_root() / "mkv" / "current"

    executable = tmp_path / "executable"
    executable.write_text("tool", encoding="utf-8")
    executable.chmod(0o700)
    executable.parent.chmod(0o700)
    assert tool_paths.trusted_executable(executable)
    executable.chmod(0o600)
    assert not tool_paths.trusted_executable(executable)
    executable.chmod(0o720)
    assert not tool_paths.trusted_executable(executable)
    executable.chmod(0o700)
    executable.parent.chmod(0o720)
    assert not tool_paths.trusted_executable(executable)

    with monkeypatch.context() as context:
        context.setattr(
            tool_paths,
            "os",
            SimpleNamespace(
                name="nt", access=lambda _path, _mode: True, X_OK=os.X_OK
            ),
        )
        assert tool_paths.trusted_executable(executable)


def test_image_fallback_plan_is_sorted_deduplicated_filtered_and_noncyclic() -> (
    None
):
    config = {
        "img_host_10": " Zippy ",
        "img_host_2": "imgbox",
        "img_host_1": "IMGBB",
        "img_host_3": "imgbox",
        "img_host_x": "ignored",
        "img_host_4": "",
        3: "ignored",
    }
    assert configured_image_hosts(config) == ("imgbb", "imgbox", "zippy")
    assert image_host_fallback_plan(config, preferred_host="Zippy") == (
        "zippy",
        "imgbb",
        "imgbox",
    )
    assert image_host_fallback_plan(
        config,
        preferred_host="new-host",
        allowed_hosts=["imgbox", "new-host", ""],
        unavailable_hosts=["IMGBB", "new-host", ""],
    ) == ("imgbox",)
    assert (
        image_host_fallback_plan(
            config,
            preferred_host=None,
            unavailable_hosts=configured_image_hosts(config),
        )
        == ()
    )


def test_screenshot_manifest_complete_lifecycle_and_malformed_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_id = "release"
    directory = temp_paths.screenshots_dir(tmp_path, release_id)
    first = directory / "capture.PNG"
    first.write_bytes(b"one")
    no_suffix = directory / "capture"
    no_suffix.write_bytes(b"two")
    missing = directory / "missing.png"

    generated = iter(
        [
            SimpleNamespace(hex="same"),
            SimpleNamespace(hex="same"),
            SimpleNamespace(hex="second"),
            SimpleNamespace(hex="third"),
        ]
    )
    monkeypatch.setattr(manifest.uuid, "uuid4", lambda: next(generated))
    collision = directory / "same.png"
    collision.write_bytes(b"existing")
    registered = manifest.register(
        tmp_path, release_id, [missing, first, no_suffix], "main"
    )
    assert [path.name for path in registered] == ["second.png", "third.png"]
    assert collision.read_bytes() == b"existing"

    listed = manifest.files(tmp_path, release_id)
    assert [path.name for path in listed] == ["second.png", "third.png"]
    assert manifest.files(tmp_path, release_id, "other") == []
    assert manifest.group_for(tmp_path, release_id, listed[0]) == "main"
    assert (
        manifest.group_for(tmp_path, release_id, Path("unknown.png")) == "main"
    )

    manifest.forget_file(tmp_path, release_id, listed[0])
    assert listed[0] not in manifest.files(tmp_path, release_id)
    manifest.clear_group(tmp_path, release_id, "main")
    assert manifest.files(tmp_path, release_id) == []

    manifest_path = tmp_path / "tmp" / release_id / "screenshot_manifest.json"
    manifest_path.write_text("not-json", encoding="utf-8")
    assert manifest._load(tmp_path, release_id) == {}
    manifest_path.write_text("[]", encoding="utf-8")
    assert manifest._load(tmp_path, release_id) == {}

    manifest_path.write_text(json.dumps({"screenshots": []}), encoding="utf-8")
    assert manifest.files(tmp_path, release_id) == []
    assert manifest.group_for(tmp_path, release_id, Path("none.png")) == "main"
    manifest.clear_group(tmp_path, release_id, "main")
    manifest.forget_file(tmp_path, release_id, Path("none.png"))

    manifest_path.write_text(
        json.dumps(
            {
                "screenshots": {
                    "bad": "not-a-dict",
                    "missing": {"group": "other"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert manifest.files(tmp_path, release_id, "main") == []
    manifest.clear_group(tmp_path, release_id, "main")
    assert manifest.group_for(tmp_path, release_id, Path("none.png")) == "main"

    manifest_path.write_text(json.dumps({"screenshots": []}), encoding="utf-8")
    source = directory / "new.jpg"
    source.write_bytes(b"new")
    monkeypatch.setattr(
        manifest.uuid, "uuid4", lambda: SimpleNamespace(hex="published")
    )
    assert (
        manifest.register(tmp_path, release_id, [source], "disc")[0].name
        == "published.jpg"
    )
    assert (
        manifest.group_for(tmp_path, release_id, Path("published.jpg"))
        == "disc"
    )
