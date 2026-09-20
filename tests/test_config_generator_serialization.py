import importlib.util
import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def config_generator_module():
    path = Path(__file__).parents[1] / "config-generator.py"
    spec = importlib.util.spec_from_file_location(
        "config_generator_serialization", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_format_config_value_normalizes_nested_boolean_strings(
    config_generator_module: Any,
) -> None:
    value = {
        1: "true",
        "nested": {"disabled": "FALSE", "unchanged": "value"},
        "items": ["true", "false", "keep"],
    }

    assert config_generator_module._format_config_value(value) == {
        "1": True,
        "nested": {"disabled": False, "unchanged": "value"},
        "items": [True, False, "keep"],
    }


def test_write_config_dict_orders_trackers_and_preserves_comments(
    config_generator_module: Any,
) -> None:
    stream = StringIO()
    config = {
        "TRACKERS": {
            "ZETA": {"api_key": "z"},
            "MANUAL": {"api_key": "m"},
            "default_trackers": "ALPHA,ZETA",
            "ALPHA": {"api_key": "a"},
        }
    }
    comments = {"TRACKERS.ALPHA.api_key": ["# Alpha API key"]}

    config_generator_module._write_config_dict(stream, config, comments)

    output = stream.getvalue()
    default_pos = output.index('"default_trackers"')
    alpha_pos = output.index('"ALPHA"')
    zeta_pos = output.index('"ZETA"')
    manual_pos = output.index('"MANUAL"')
    assert default_pos < alpha_pos < zeta_pos < manual_pos
    assert '# Alpha API key\n            "api_key": "a",' in output
