import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module(monkeypatch: pytest.MonkeyPatch):
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_load_existing", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "migrate_old_config", lambda config: config)
    return module


def _configure_paths(module: Any, tmp_path: Path) -> tuple[Path, Path, Path]:
    current = tmp_path / "config.py"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config1 = data_dir / "config1.py"
    legacy = tmp_path / "legacy.py"
    module.CONFIG_PATH = current
    module.DATA_DIR = data_dir
    module.LEGACY_CONFIG_PATH = legacy
    return current, config1, legacy


def test_load_existing_config_prefers_current_config(
    config_generator_module: Any, tmp_path: Path
) -> None:
    current, config1, _legacy = _configure_paths(
        config_generator_module, tmp_path
    )
    current.write_text('config = {"DEFAULT": {"source": "current"}}')
    config1.write_text('config = {"DEFAULT": {"source": "fallback"}}')

    config, destination = config_generator_module.load_existing_config()

    assert config == {"DEFAULT": {"source": "current"}}
    assert destination == current


def test_load_existing_config_skips_invalid_config(
    config_generator_module: Any, tmp_path: Path
) -> None:
    current, config1, _legacy = _configure_paths(
        config_generator_module, tmp_path
    )
    current.write_text("config = []")
    config1.write_text('config = {"DEFAULT": {"source": "fallback"}}')

    config, destination = config_generator_module.load_existing_config()

    assert config == {"DEFAULT": {"source": "fallback"}}
    assert destination == config1


def test_load_existing_config_redirects_legacy_destination(
    config_generator_module: Any, tmp_path: Path
) -> None:
    current, _config1, legacy = _configure_paths(
        config_generator_module, tmp_path
    )
    legacy.write_text('config = {"DEFAULT": {"source": "legacy"}}')

    config, destination = config_generator_module.load_existing_config()

    assert config == {"DEFAULT": {"source": "legacy"}}
    assert destination == current
