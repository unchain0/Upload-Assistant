"""Tests for deterministic AST-derived contract scenarios."""

from __future__ import annotations

from typing import Any

from src.domain_models.release import Meta
from tests.contract_scenarios import literal_branch_scenarios


def _aliased_release_rule(meta: Meta, config: dict[str, Any], choice: str = "") -> bool:
    category = str(meta.category or "").upper()
    defaults = config.get("DEFAULT", {})
    minimum = defaults.get("minimum", 0)
    normalized_choice = choice.strip().lower()
    return category == "MOVIE" and minimum >= 3 and normalized_choice in {"yes", "y"}


def test_literal_branch_scenarios_resolve_local_aliases_and_nested_mappings() -> None:
    scenarios = literal_branch_scenarios(_aliased_release_rule, Meta.__dataclass_fields__)

    assert any(meta.get("category") == "MOVIE" for meta, _params in scenarios)
    assert any(params.get("choice") in {"yes", "y"} for _meta, params in scenarios)
    assert any(
        isinstance(params.get("config"), dict) and isinstance(params["config"].get("DEFAULT"), dict) and params["config"]["DEFAULT"].get("minimum") in {2, 4}
        for _meta, params in scenarios
    )


def _typed_rule(value: object) -> bool:
    return isinstance(value, str) and value.startswith("prefix")


def test_literal_branch_scenarios_cover_type_and_prefix_checks() -> None:
    scenarios = literal_branch_scenarios(_typed_rule, ())

    values = [parameters.get("value") for _meta, parameters in scenarios]
    assert "example" in values
    assert "prefix" in values
    assert None in values
