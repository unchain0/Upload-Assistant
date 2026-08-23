"""AST-derived branch scenarios used by deterministic contract tests.

The helpers convert literal branch predicates into concrete domain/argument
updates.  They do not execute or transform production source; each generated
scenario invokes the real callable through the test's normal boundary fakes.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import textwrap
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Assignment:
    kind: str
    name: str
    value: object


Scenario = tuple[dict[str, object], dict[str, object]]

_TRANSFORM_CALLS = {"casefold", "lower", "upper", "strip", "lstrip", "rstrip"}


def _literal(node: ast.AST) -> object | None:
    try:
        value = ast.literal_eval(node)
    except ValueError, TypeError, SyntaxError:
        return None
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    if isinstance(value, tuple | list | set | frozenset):
        return value
    return None


def _literal_values(node: ast.AST) -> list[object]:
    value = _literal(node)
    if isinstance(value, tuple | list | set | frozenset):
        return list(value)
    return (
        []
        if value is None
        and not (isinstance(node, ast.Constant) and node.value is None)
        else [value]
    )


def _unwrap(node: ast.AST) -> ast.AST:
    current = node
    while (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and current.func.attr in _TRANSFORM_CALLS
        and not current.args
    ):
        current = current.func.value
    return current


def _target_name(
    node: ast.Name,
    parameter_names: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if node.id in aliases:
        return aliases[node.id]
    if node.id in parameter_names:
        return ("parameter", node.id)
    return None


def _target_boolop(
    node: ast.BoolOp,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    for value in node.values:
        resolved = _target(
            value, meta_names, parameter_names, domain_fields, aliases
        )
        if resolved is not None:
            return resolved
    return None


def _target_attribute(
    node: ast.Attribute,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if (
        isinstance(node.value, ast.Name)
        and node.value.id in meta_names
        and node.attr in domain_fields
    ):
        return ("meta", node.attr)
    parent = _target(
        node.value, meta_names, parameter_names, domain_fields, aliases
    )
    if parent is None:
        return None
    return (parent[0], f"{parent[1]}.{node.attr}")


def _invalid_meta_nested_key(
    parent: tuple[str, str], key: str, domain_fields: set[str]
) -> bool:
    if parent[0] != "meta":
        return False
    if parent[1] != "":
        return False
    return key not in domain_fields


def _nested_target(
    parent: tuple[str, str] | None,
    key: object,
    domain_fields: set[str],
) -> tuple[str, str] | None:
    if parent is None:
        return None
    if not isinstance(key, str):
        return None
    if _invalid_meta_nested_key(parent, key, domain_fields):
        return None
    name = f"{parent[1]}.{key}" if parent[1] else key
    return parent[0], name


def _target_subscript(
    node: ast.Subscript,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    parent = _target(
        node.value, meta_names, parameter_names, domain_fields, aliases
    )
    return _nested_target(parent, _literal(node.slice), domain_fields)


def _target_cast_call(
    node: ast.Call,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if not isinstance(node.func, ast.Name):
        return None
    if node.func.id not in {"str", "int", "float", "bool"} or not node.args:
        return None
    return _target(
        node.args[0], meta_names, parameter_names, domain_fields, aliases
    )


def _target_get_call(
    node: ast.Call,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get" or not node.args:
        return None
    parent = _target(
        node.func.value, meta_names, parameter_names, domain_fields, aliases
    )
    return _nested_target(parent, _literal(node.args[0]), domain_fields)


def _target_getattr_call(
    node: ast.Call,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "getattr" or len(node.args) < 2:
        return None
    parent = _target(
        node.args[0], meta_names, parameter_names, domain_fields, aliases
    )
    return _nested_target(parent, _literal(node.args[1]), domain_fields)


def _target_call(
    node: ast.Call,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    for resolver in (
        _target_cast_call,
        _target_get_call,
        _target_getattr_call,
    ):
        resolved = resolver(
            node, meta_names, parameter_names, domain_fields, aliases
        )
        if resolved is not None:
            return resolved
    return None


def _target_non_call(
    node: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[str, str] | None:
    if isinstance(node, ast.Name):
        return _target_name(node, parameter_names, aliases)
    if isinstance(node, ast.BoolOp):
        return _target_boolop(
            node, meta_names, parameter_names, domain_fields, aliases
        )
    if isinstance(node, ast.Attribute):
        return _target_attribute(
            node, meta_names, parameter_names, domain_fields, aliases
        )
    if isinstance(node, ast.Subscript):
        return _target_subscript(
            node, meta_names, parameter_names, domain_fields, aliases
        )
    return None


def _target(
    node: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, str] | None:
    resolved_aliases = aliases or {}
    node = _unwrap(node)
    resolved = _target_non_call(
        node, meta_names, parameter_names, domain_fields, resolved_aliases
    )
    if resolved is not None:
        return resolved
    if not isinstance(node, ast.Call):
        return None
    return _target_call(
        node, meta_names, parameter_names, domain_fields, resolved_aliases
    )


def _plain_alias_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if not isinstance(node, ast.Assign):
        return None
    if len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        return None
    return target.id, node.value


def _annotated_alias_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if not isinstance(node, ast.AnnAssign):
        return None
    if not isinstance(node.target, ast.Name):
        return None
    if node.value is None:
        return None
    return node.target.id, node.value


def _alias_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    plain = _plain_alias_assignment(node)
    if plain is not None:
        return plain
    return _annotated_alias_assignment(node)


def _aliases(
    tree: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        assignment = _alias_assignment(node)
        if assignment is None:
            continue
        target_name, value = assignment
        resolved = _target(
            value, meta_names, parameter_names, domain_fields, aliases
        )
        if resolved is not None:
            aliases[target_name] = resolved
    return aliases


def _alternative_none(_value: object) -> object:
    return "example"


def _alternative_bool(value: object) -> object:
    return not bool(value)


def _alternative_int(value: object) -> object:
    number = int(value)
    return number + 1 if number != 0 else 1


def _alternative_float(value: object) -> object:
    number = float(value)
    return number + 1.0 if number != 0 else 1.0


def _alternative_str(value: object) -> object:
    text = str(value)
    return "__other__" if text != "__other__" else "example"


_ALTERNATIVE_SCALARS: dict[type[object], Callable[[object], object]] = {
    type(None): _alternative_none,
    bool: _alternative_bool,
    int: _alternative_int,
    float: _alternative_float,
    str: _alternative_str,
}


def _alternative(value: object) -> object:
    scalar = _ALTERNATIVE_SCALARS.get(type(value))
    if scalar is not None:
        return scalar(value)
    if isinstance(value, tuple | list | set | frozenset):
        return "__other__"
    return None


def _representative_type(node: ast.AST) -> object | None:
    if isinstance(node, ast.Name):
        values: dict[str, object] = {
            "str": "example",
            "int": 1,
            "float": 1.0,
            "bool": True,
            "dict": {},
            "list": [],
            "tuple": (),
            "set": set(),
            "Path": Path("example"),
        }
        return values.get(node.id)
    return None


def _merge(groups: Iterable[Iterable[Assignment]]) -> list[Assignment] | None:
    merged: dict[tuple[str, str], Assignment] = {}
    for group in groups:
        for item in group:
            key = (item.kind, item.name)
            previous = merged.get(key)
            if previous is not None and previous.value != item.value:
                return None
            merged[key] = item
    return list(merged.values())


def _direct_truth_assignment(
    node: ast.AST,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]] | None:
    target = _target(node, meta_names, parameter_names, domain_fields, aliases)
    if target is None:
        return None
    return [[Assignment(*target, truth)]]


def _not_truth_options(
    node: ast.UnaryOp,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    if not isinstance(node.op, ast.Not):
        return []
    return _condition_options(
        node.operand,
        truth=not truth,
        meta_names=meta_names,
        parameter_names=parameter_names,
        domain_fields=domain_fields,
        aliases=aliases,
    )


def _bool_children(
    node: ast.BoolOp,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[list[Assignment]]]:
    return [
        _condition_options(
            value,
            truth=truth,
            meta_names=meta_names,
            parameter_names=parameter_names,
            domain_fields=domain_fields,
            aliases=aliases,
        )
        for value in node.values
    ]


def _bool_requires_merge(node: ast.BoolOp, truth: bool) -> bool:
    if truth and isinstance(node.op, ast.And):
        return True
    return not truth and isinstance(node.op, ast.Or)


def _merged_bool_options(
    children: list[list[list[Assignment]]],
) -> list[list[Assignment]]:
    options: list[list[Assignment]] = []
    combinations = itertools.product(*[child or [[]] for child in children])
    for combination in combinations:
        merged = _merge(combination)
        if merged is not None:
            options.append(merged)
    return options


def _flattened_bool_options(
    children: list[list[list[Assignment]]],
) -> list[list[Assignment]]:
    return [option for child in children for option in child]


def _bool_truth_options(
    node: ast.BoolOp,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    children = _bool_children(
        node, truth, meta_names, parameter_names, domain_fields, aliases
    )
    if _bool_requires_merge(node, truth):
        return _merged_bool_options(children)
    return _flattened_bool_options(children)


def _comparison_parts(
    node: ast.Compare,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> tuple[tuple[str, str], ast.AST, ast.cmpop, bool] | None:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    left = node.left
    right = node.comparators[0]
    op = node.ops[0]
    target = _target(left, meta_names, parameter_names, domain_fields, aliases)
    if target is not None:
        return target, right, op, False
    target = _target(
        right, meta_names, parameter_names, domain_fields, aliases
    )
    if target is None:
        return None
    return target, left, op, True


def _membership_assignment(
    target: tuple[str, str],
    literal_node: ast.AST,
    op: ast.cmpop,
    truth: bool,
) -> list[list[Assignment]] | None:
    if not isinstance(op, ast.In | ast.NotIn):
        return None
    values = _literal_values(literal_node)
    if not values:
        return []
    wants_member = truth if isinstance(op, ast.In) else not truth
    chosen = values[0] if wants_member else _alternative(values)
    return [[Assignment(*target, chosen)]]


def _equality_relation(op: ast.cmpop) -> bool | None:
    if isinstance(op, ast.Eq | ast.Is):
        return True
    if isinstance(op, ast.NotEq | ast.IsNot):
        return False
    return None


def _literal_or_constant(node: ast.AST) -> tuple[bool, object | None]:
    literal = _literal(node)
    if literal is not None:
        return True, literal
    return isinstance(node, ast.Constant), literal


def _equality_assignment(
    target: tuple[str, str],
    literal_node: ast.AST,
    op: ast.cmpop,
    truth: bool,
) -> list[list[Assignment]] | None:
    supported, literal = _literal_or_constant(literal_node)
    if not supported:
        return None
    equals = _equality_relation(op)
    if equals is None:
        return None
    wants_equal = truth if equals else not truth
    value = literal if wants_equal else _alternative(literal)
    return [[Assignment(*target, value)]]


def _ordering_candidate(
    literal: int | float,
    op: ast.cmpop,
    truth: bool,
    reverse: bool,
) -> int | float:
    delta: int | float = 1 if isinstance(literal, int) else 1.0
    true_side = truth ^ reverse
    if isinstance(op, ast.Lt | ast.LtE):
        return literal - delta if true_side else literal + delta
    return literal + delta if true_side else literal - delta


def _ordering_assignment(
    target: tuple[str, str],
    literal_node: ast.AST,
    op: ast.cmpop,
    truth: bool,
    reverse: bool,
) -> list[list[Assignment]] | None:
    literal = _literal(literal_node)
    if not isinstance(literal, int | float):
        return None
    if not isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE):
        return None
    candidate = _ordering_candidate(literal, op, truth, reverse)
    return [[Assignment(*target, candidate)]]


def _compare_truth_options(
    node: ast.Compare,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    parts = _comparison_parts(
        node, meta_names, parameter_names, domain_fields, aliases
    )
    if parts is None:
        return []
    target, literal_node, op, reverse = parts
    for resolver in (_membership_assignment, _equality_assignment):
        resolved = resolver(target, literal_node, op, truth)
        if resolved is not None:
            return resolved
    ordering = _ordering_assignment(target, literal_node, op, truth, reverse)
    return [] if ordering is None else ordering


def _is_isinstance_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id != "isinstance":
        return False
    return len(node.args) >= 2


def _assignment_from_representative(
    target: tuple[str, str] | None,
    representative: object | None,
    truth: bool,
) -> list[list[Assignment]]:
    if target is None:
        return []
    if representative is None:
        return []
    value = representative if truth else None
    return [[Assignment(*target, value)]]


def _isinstance_truth_options(
    node: ast.Call,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]] | None:
    if not _is_isinstance_call(node):
        return None
    target = _target(
        node.args[0], meta_names, parameter_names, domain_fields, aliases
    )
    representative = _representative_type(node.args[1])
    return _assignment_from_representative(target, representative, truth)


def _is_prefix_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in {"startswith", "endswith"}:
        return False
    return bool(node.args)


def _prefix_assignment(
    target: tuple[str, str] | None,
    values: list[object],
    truth: bool,
) -> list[list[Assignment]]:
    if target is None or not values:
        return []
    value = values[0]
    if not isinstance(value, str):
        return []
    candidate = value if truth else "__other__"
    return [[Assignment(*target, candidate)]]


def _prefix_truth_options(
    node: ast.Call,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]] | None:
    if not _is_prefix_call(node):
        return None
    target = _target(
        node.func.value, meta_names, parameter_names, domain_fields, aliases
    )
    values = _literal_values(node.args[0])
    return _prefix_assignment(target, values, truth)


def _call_truth_options(
    node: ast.Call,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    for resolver in (_isinstance_truth_options, _prefix_truth_options):
        resolved = resolver(
            node, truth, meta_names, parameter_names, domain_fields, aliases
        )
        if resolved is not None:
            return resolved
    return []


def _non_target_truth_options(
    node: ast.AST,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    if isinstance(node, ast.UnaryOp):
        return _not_truth_options(
            node, truth, meta_names, parameter_names, domain_fields, aliases
        )
    if isinstance(node, ast.BoolOp):
        return _bool_truth_options(
            node, truth, meta_names, parameter_names, domain_fields, aliases
        )
    if isinstance(node, ast.Compare):
        return _compare_truth_options(
            node, truth, meta_names, parameter_names, domain_fields, aliases
        )
    if isinstance(node, ast.Call):
        return _call_truth_options(
            node, truth, meta_names, parameter_names, domain_fields, aliases
        )
    return []


def _simple_truth(
    node: ast.AST,
    *,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    direct = _direct_truth_assignment(
        node, truth, meta_names, parameter_names, domain_fields, aliases
    )
    if direct is not None:
        return direct
    return _non_target_truth_options(
        node, truth, meta_names, parameter_names, domain_fields, aliases
    )


def _condition_options(
    node: ast.AST,
    *,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    return _simple_truth(
        node,
        truth=truth,
        meta_names=meta_names,
        parameter_names=parameter_names,
        domain_fields=domain_fields,
        aliases=aliases,
    )


def _set_nested(
    target: dict[str, object], dotted_name: str, value: object
) -> None:
    parts = dotted_name.split(".")
    current = target
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = value


def _as_scenario(assignments: Iterable[Assignment]) -> Scenario:
    meta: dict[str, object] = {}
    parameters: dict[str, object] = {}
    for assignment in assignments:
        _set_nested(
            meta if assignment.kind == "meta" else parameters,
            assignment.name,
            assignment.value,
        )
    return meta, parameters


@dataclass(slots=True)
class _ScenarioCollector:
    limit: int
    scenarios: list[Scenario]
    seen: set[str]

    @classmethod
    def create(cls, limit: int) -> _ScenarioCollector:
        return cls(limit=limit, scenarios=[], seen=set())

    @property
    def full(self) -> bool:
        return len(self.scenarios) >= self.limit

    def add(self, assignments: Iterable[Assignment]) -> None:
        if self.full:
            return
        scenario = _as_scenario(assignments)
        if not any(scenario):
            return
        key = repr(scenario)
        if key in self.seen:
            return
        self.seen.add(key)
        self.scenarios.append(scenario)


def _source_tree_and_signature(
    function: Callable[..., object],
) -> tuple[ast.AST, inspect.Signature] | None:
    target = getattr(function, "__func__", function)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
        signature = inspect.signature(function)
    except OSError, TypeError, SyntaxError, ValueError:
        return None
    return tree, signature


def _meta_parameter_names(parameter_names: set[str]) -> set[str]:
    return {
        name
        for name in parameter_names
        if "meta" in name.casefold() or name in {"release", "item"}
    }


def _node_condition(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.If | ast.IfExp | ast.While | ast.Assert):
        return node.test
    if isinstance(node, ast.Return):
        return node.value
    if isinstance(node, ast.Compare | ast.Call):
        return node
    return None


def _add_condition_scenarios(
    collector: _ScenarioCollector,
    condition: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> None:
    for truth in (True, False):
        options = _condition_options(
            condition,
            truth=truth,
            meta_names=meta_names,
            parameter_names=parameter_names,
            domain_fields=fields,
            aliases=aliases,
        )
        for assignments in options:
            collector.add(assignments)


def _match_case_assignment(
    case: ast.match_case,
    target: tuple[str, str],
) -> Assignment | None:
    if isinstance(case.pattern, ast.MatchValue):
        value = _literal(case.pattern.value)
        if value is not None:
            return Assignment(*target, value)
        return None
    if isinstance(case.pattern, ast.MatchSingleton):
        return Assignment(*target, case.pattern.value)
    return None


def _add_match_scenarios(
    collector: _ScenarioCollector,
    node: ast.Match,
    meta_names: set[str],
    parameter_names: set[str],
    fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> None:
    target = _target(
        node.subject, meta_names, parameter_names, fields, aliases
    )
    if target is None:
        return
    for case in node.cases:
        assignment = _match_case_assignment(case, target)
        if assignment is not None:
            collector.add([assignment])


def _collect_base_scenarios(
    collector: _ScenarioCollector,
    tree: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> None:
    for node in ast.walk(tree):
        condition = _node_condition(node)
        if condition is not None:
            _add_condition_scenarios(
                collector,
                condition,
                meta_names,
                parameter_names,
                fields,
                aliases,
            )
            continue
        if isinstance(node, ast.Match):
            _add_match_scenarios(
                collector,
                node,
                meta_names,
                parameter_names,
                fields,
                aliases,
            )


def _mapping_conflicts(
    merged: dict[str, object], incoming: dict[str, object]
) -> bool:
    for key, value in incoming.items():
        if key in merged and merged[key] != value:
            return True
    return False


def _merged_scenario_parts(
    parts: tuple[Scenario, ...],
) -> tuple[dict[str, object], dict[str, object]] | None:
    merged_meta: dict[str, object] = {}
    merged_params: dict[str, object] = {}
    for meta_values, parameter_values in parts:
        if _mapping_conflicts(merged_meta, meta_values):
            return None
        if _mapping_conflicts(merged_params, parameter_values):
            return None
        merged_meta.update(meta_values)
        merged_params.update(parameter_values)
    return merged_meta, merged_params


def _merged_assignments(
    parts: tuple[Scenario, ...],
) -> list[Assignment] | None:
    merged = _merged_scenario_parts(parts)
    if merged is None:
        return None
    merged_meta, merged_params = merged
    assignments = [
        Assignment("meta", key, value) for key, value in merged_meta.items()
    ]
    assignments.extend(
        Assignment("parameter", key, value)
        for key, value in merged_params.items()
    )
    return assignments


def _add_merged_scenarios(
    collector: _ScenarioCollector, parts: tuple[Scenario, ...]
) -> None:
    assignments = _merged_assignments(parts)
    if assignments is not None:
        collector.add(assignments)


def _add_pairwise_combinations(
    collector: _ScenarioCollector, base: list[Scenario]
) -> bool:
    for first_index, first in enumerate(base[:64]):
        for second in base[first_index + 1 : 64]:
            _add_merged_scenarios(collector, (first, second))
            if collector.full:
                return True
    return False


def _add_triple_combinations(
    collector: _ScenarioCollector, base: list[Scenario]
) -> bool:
    for first_index, first in enumerate(base[:24]):
        seconds = base[first_index + 1 : 24]
        for second_index, second in enumerate(seconds, start=first_index + 1):
            for third in base[second_index + 1 : 24]:
                _add_merged_scenarios(collector, (first, second, third))
                if collector.full:
                    return True
    return False


def _scenario_targets(scenario: Scenario) -> set[tuple[str, str]]:
    meta_values, parameter_values = scenario
    targets = {("meta", key) for key in meta_values}
    targets.update(("parameter", key) for key in parameter_values)
    return targets


def _representative_scenarios(base: list[Scenario]) -> list[Scenario]:
    representatives: list[Scenario] = []
    used_targets: set[tuple[str, str]] = set()
    for scenario in base:
        targets = _scenario_targets(scenario)
        if not targets:
            continue
        if targets & used_targets:
            continue
        representatives.append(scenario)
        used_targets.update(targets)
    return representatives


def literal_branch_scenarios(
    function: Callable[..., object],
    domain_fields: Iterable[str],
    *,
    limit: int = 512,
) -> list[Scenario]:
    """Return deterministic scenarios that make source-level branches true/false."""
    source = _source_tree_and_signature(function)
    if source is None:
        return []
    tree, signature = source
    parameter_names = set(signature.parameters)
    meta_names = _meta_parameter_names(parameter_names)
    fields = set(domain_fields)
    aliases = _aliases(tree, meta_names, parameter_names, fields)
    collector = _ScenarioCollector.create(limit)
    _collect_base_scenarios(
        collector, tree, meta_names, parameter_names, fields, aliases
    )
    base = list(collector.scenarios)
    if _add_pairwise_combinations(collector, base):
        return collector.scenarios
    if _add_triple_combinations(collector, base):
        return collector.scenarios
    representatives = _representative_scenarios(base)
    _add_merged_scenarios(collector, tuple(representatives))
    return collector.scenarios
