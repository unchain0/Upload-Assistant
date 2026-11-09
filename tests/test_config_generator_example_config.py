import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_example_config", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _set_example_path(module: Any, tmp_path: Path, content: str) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    example_path = data_dir / "example_config.py"
    example_path.write_text(content, encoding="utf-8")
    module.CODE_DIR = tmp_path
    return example_path


def test_read_example_config_preserves_simple_and_fully_qualified_comments(
    config_generator_module: Any, tmp_path: Path
) -> None:
    _set_example_path(
        config_generator_module,
        tmp_path,
        """from typing import Any

config: dict[str, Any] = {
    # Default section
    \"DEFAULT\": {
        # TMDB key
        \"tmdb_api\": \"\",
        \"nested\": {
            # Nested value
            \"value\": 1,
        },
    },
    # Tracker section
    \"TRACKERS\": {},
}
""",
    )

    config, comments = config_generator_module.read_example_config()

    assert config == {
        "DEFAULT": {"tmdb_api": "", "nested": {"value": 1}},
        "TRACKERS": {},
    }
    assert comments["DEFAULT"] == ["# Default section"]
    assert comments["tmdb_api"] == ["# TMDB key"]
    assert comments["DEFAULT.tmdb_api"] == ["# TMDB key"]
    assert comments["value"] == ["# Nested value"]
    assert comments["DEFAULT.nested.value"] == ["# Nested value"]
    assert comments["TRACKERS"] == ["# Tracker section"]


def test_read_example_config_returns_empty_result_when_template_missing(
    config_generator_module: Any, tmp_path: Path
) -> None:
    config_generator_module.CODE_DIR = tmp_path

    assert config_generator_module.read_example_config() == (None, {})


def test_read_example_config_keeps_comments_when_config_parse_fails(
    config_generator_module: Any, tmp_path: Path
) -> None:
    _set_example_path(
        config_generator_module,
        tmp_path,
        """# ignored header
# Section comment
\"DEFAULT\": {
    # Value comment
    \"value\": 1,
}
""",
    )

    config, comments = config_generator_module.read_example_config()

    assert config is None
    assert comments["DEFAULT"] == ["# ignored header", "# Section comment"]
    assert comments["value"] == ["# Value comment"]
    assert comments["DEFAULT.value"] == ["# Value comment"]


def test_parse_example_config_content_rejects_non_dict_literal(
    config_generator_module: Any,
) -> None:
    config = config_generator_module._parse_example_config_content(
        "config = {1, 2}"
    )

    assert config is None
