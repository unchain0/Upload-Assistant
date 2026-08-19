import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from src.integrations.filesystem.paths import _default_data_dir


def test_user_data_override_holds_runtime_config(tmp_path: Path) -> None:
    """``data.config`` must resolve from UA_DATA_DIR, never the checkout."""
    state_dir = tmp_path / "state"
    environment = os.environ | {"PYTHONPATH": str(Path.cwd()), "UA_DATA_DIR": str(state_dir)}

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from data.config import config; from src.integrations.filesystem.paths import CONFIG_PATH, DATA_DIR, STATE_DIR; "
            "assert CONFIG_PATH.parent == DATA_DIR; assert CONFIG_PATH.exists(); "
            "assert str(STATE_DIR / 'data' / 'config.py') == str(CONFIG_PATH); assert isinstance(config, dict)",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (state_dir / "data" / "config.py").is_file()


def test_state_layout_has_one_data_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    config_path = state_dir / "data" / "config.py"
    assert config_path.parent == state_dir / "data"
    assert not (state_dir / "data" / "data" / "config.py").exists()


def test_default_data_dir_unix_default(monkeypatch) -> None:
    fake_home = Path("/home/user")
    monkeypatch.delenv("UA_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    monkeypatch.setattr("pathlib.Path.exists", lambda _self: False)

    with patch("src.integrations.filesystem.paths.os.name", "posix"):
        assert _default_data_dir().as_posix() == "/home/user/.local/share/Upload-Assistant"


def test_default_data_dir_unix_xdg_data_home(monkeypatch) -> None:
    fake_xdg = Path("/custom_xdg")
    monkeypatch.delenv("UA_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "/custom_xdg")
    monkeypatch.setattr("pathlib.Path.expanduser", lambda _self: fake_xdg)
    monkeypatch.setattr("pathlib.Path.exists", lambda _self: False)

    with patch("src.integrations.filesystem.paths.os.name", "posix"):
        assert _default_data_dir() == fake_xdg / "Upload-Assistant"


def test_default_data_dir_unix_legacy_fallback(monkeypatch) -> None:
    fake_home = Path("/home/user")
    legacy_dir = "/home/user/.local/share/upload-assistant"

    monkeypatch.delenv("UA_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

    def fake_exists(self: Path) -> bool:
        return self.as_posix() == legacy_dir

    monkeypatch.setattr("pathlib.Path.exists", fake_exists)

    with patch("src.integrations.filesystem.paths.os.name", "posix"):
        assert _default_data_dir().as_posix() == legacy_dir


def test_runtime_path_service_override_windows_and_legacy(monkeypatch, tmp_path: Path) -> None:
    from src.services import runtime_paths_service

    override = tmp_path / "override"
    monkeypatch.setenv("UA_DATA_DIR", str(override))
    assert runtime_paths_service._state_dir() == override

    monkeypatch.delenv("UA_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    with (
        patch.object(runtime_paths_service.os, "name", "nt"),
        patch.object(runtime_paths_service, "Path", side_effect=lambda value: PurePosixPath(value)) as path_factory,
    ):
        path_factory.home.return_value = PurePosixPath(str(tmp_path / "home"))
        assert runtime_paths_service._state_dir() == tmp_path / "local" / "Upload-Assistant"

    monkeypatch.delenv("LOCALAPPDATA")
    with (
        patch.object(runtime_paths_service.os, "name", "nt"),
        patch.object(runtime_paths_service, "Path", side_effect=lambda value: PurePosixPath(value)) as path_factory,
    ):
        path_factory.home.return_value = PurePosixPath(str(tmp_path / "home"))
        assert runtime_paths_service._state_dir() == tmp_path / "home" / "AppData" / "Local" / "Upload-Assistant"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    legacy = tmp_path / "xdg" / "upload-assistant"
    legacy.mkdir(parents=True)
    with patch.object(runtime_paths_service.os, "name", "posix"):
        assert runtime_paths_service._state_dir() == legacy

    primary = tmp_path / "xdg" / "Upload-Assistant"
    primary.mkdir()
    with patch.object(runtime_paths_service.os, "name", "posix"):
        assert runtime_paths_service._state_dir() == primary


def test_runtime_paths_service_resolves_all_runtime_locations(monkeypatch, tmp_path: Path) -> None:
    from src.services import runtime_paths_service

    state = tmp_path / "state"
    monkeypatch.setenv("UA_DATA_DIR", str(state))
    paths = runtime_paths_service.resolve_runtime_paths()

    assert paths.code_dir == Path(runtime_paths_service.__file__).resolve().parents[2]
    assert paths.state_dir == state
    assert paths.data_dir == state / "data"
    assert paths.tmp_dir == state / "tmp"
    assert paths.config_path == state / "data" / "config.py"
    assert paths.legacy_config_path == paths.code_dir / "data" / "config.py"
    assert paths.example_config_path == paths.code_dir / "data" / "example_config.py"
