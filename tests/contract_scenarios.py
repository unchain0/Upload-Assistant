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


def _target(
    node: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]] | None = None,
) -> tuple[str, str] | None:
    aliases = aliases or {}
    node = _unwrap(node)
    if isinstance(node, ast.Name):
        if node.id in aliases:
            return aliases[node.id]
        if node.id in parameter_names:
            return ("parameter", node.id)
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            target = _target(
                value, meta_names, parameter_names, domain_fields, aliases
            )
            if target is not None:
                return target
    if isinstance(node, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in meta_names
            and node.attr in domain_fields
        ):
            return ("meta", node.attr)
        parent = _target(
            node.value, meta_names, parameter_names, domain_fields, aliases
        )
        if parent is not None:
            return (parent[0], f"{parent[1]}.{node.attr}")
    if isinstance(node, ast.Subscript):
        parent = _target(
            node.value, meta_names, parameter_names, domain_fields, aliases
        )
        key = _literal(node.slice)
        if parent is not None and isinstance(key, str):
            if (
                parent[0] == "meta"
                and parent[1] == ""
                and key not in domain_fields
            ):
                return None
            return (parent[0], f"{parent[1]}.{key}" if parent[1] else key)
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"str", "int", "float", "bool"}
            and node.args
        ):
            return _target(
                node.args[0],
                meta_names,
                parameter_names,
                domain_fields,
                aliases,
            )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            parent = _target(
                node.func.value,
                meta_names,
                parameter_names,
                domain_fields,
                aliases,
            )
            key = _literal(node.args[0])
            if parent is not None and isinstance(key, str):
                if (
                    parent[0] == "meta"
                    and parent[1] == ""
                    and key not in domain_fields
                ):
                    return None
                return (parent[0], f"{parent[1]}.{key}" if parent[1] else key)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            parent = _target(
                node.args[0],
                meta_names,
                parameter_names,
                domain_fields,
                aliases,
            )
            key = _literal(node.args[1])
            if parent is not None and isinstance(key, str):
                if (
                    parent[0] == "meta"
                    and parent[1] == ""
                    and key not in domain_fields
                ):
                    return None
                return (parent[0], f"{parent[1]}.{key}" if parent[1] else key)
    return None


def _aliases(
    tree: ast.AST,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        target_name: str | None = None
        value: ast.AST | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target_name = node.targets[0].id
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target_name = node.target.id
            value = node.value
        if target_name is None or value is None:
            continue
        resolved = _target(
            value, meta_names, parameter_names, domain_fields, aliases
        )
        if resolved is not None:
            aliases[target_name] = resolved
    return aliases


def _alternative(value: object) -> object:
    if value is None:
        return "example"
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1 if value != 0 else 1
    if isinstance(value, float):
        return value + 1.0 if value != 0 else 1.0
    if isinstance(value, str):
        return "__other__" if value != "__other__" else "example"
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


def _simple_truth(
    node: ast.AST,
    *,
    truth: bool,
    meta_names: set[str],
    parameter_names: set[str],
    domain_fields: set[str],
    aliases: dict[str, tuple[str, str]],
) -> list[list[Assignment]]:
    target = _target(node, meta_names, parameter_names, domain_fields, aliases)
    if target is not None:
        return [[Assignment(*target, truth)]]

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _condition_options(
            node.operand,
            truth=not truth,
            meta_names=meta_names,
            parameter_names=parameter_names,
            domain_fields=domain_fields,
            aliases=aliases,
        )

    if isinstance(node, ast.BoolOp):
        children = [
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
        if (truth and isinstance(node.op, ast.And)) or (
            not truth and isinstance(node.op, ast.Or)
        ):
            options: list[list[Assignment]] = []
            for combination in itertools.product(
                *[child or [[]] for child in children]
            ):
                merged = _merge(combination)
                if merged is not None:
                    options.append(merged)
            return options
        # True OR: any child true. False AND: any child false.
        return [option for child in children for option in child]

    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
    ):
        left, right = node.left, node.comparators[0]
        op = node.ops[0]
        target_info = _target(
            left, meta_names, parameter_names, domain_fields, aliases
        )
        literal_node = right
        reverse = False
        if target_info is None:
            target_info = _target(
                right, meta_names, parameter_names, domain_fields, aliases
            )
            literal_node = left
            reverse = True
        if target_info is not None:
            values = _literal_values(literal_node)
            literal = _literal(literal_node)
            if isinstance(op, ast.In | ast.NotIn):
                if not values:
                    return []
                wants_member = truth if isinstance(op, ast.In) else not truth
                chosen = values[0] if wants_member else _alternative(values)
                return [[Assignment(*target_info, chosen)]]
            if literal is not None or isinstance(literal_node, ast.Constant):
                equals = isinstance(op, ast.Eq | ast.Is)
                differs = isinstance(op, ast.NotEq | ast.IsNot)
                if equals or differs:
                    wants_equal = truth if equals else not truth
                    return [
                        [
                            Assignment(
                                *target_info,
                                literal
                                if wants_equal
                                else _alternative(literal),
                            )
                        ]
                    ]
                if isinstance(literal, int | float) and isinstance(
                    op, ast.Lt | ast.LtE | ast.Gt | ast.GtE
                ):
                    # Pick values on both sides of the boundary. Reverse comparison
                    # swaps which side makes the target expression true.
                    delta = 1 if isinstance(literal, int) else 1.0
                    if isinstance(op, ast.Lt | ast.LtE):
                        candidate = (
                            literal - delta
                            if truth ^ reverse
                            else literal + delta
                        )
                    else:
                        candidate = (
                            literal + delta
                            if truth ^ reverse
                            else literal - delta
                        )
                    return [[Assignment(*target_info, candidate)]]

    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) >= 2
        ):
            target_info = _target(
                node.args[0],
                meta_names,
                parameter_names,
                domain_fields,
                aliases,
            )
            representative = _representative_type(node.args[1])
            if target_info is not None and representative is not None:
                return [
                    [
                        Assignment(
                            *target_info, representative if truth else None
                        )
                    ]
                ]
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"startswith", "endswith"}
            and node.args
        ):
            target_info = _target(
                node.func.value,
                meta_names,
                parameter_names,
                domain_fields,
                aliases,
            )
            values = _literal_values(node.args[0])
            if target_info is not None and values:
                value = values[0]
                if not isinstance(value, str):
                    return []
                candidate = value if truth else "__other__"
                return [[Assignment(*target_info, candidate)]]

    return []


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


def literal_branch_scenarios(
    function: Callable[..., object],
    domain_fields: Iterable[str],
    *,
    limit: int = 512,
) -> list[Scenario]:
    """Return deterministic scenarios that make source-level branches true/false."""

    target = getattr(function, "__func__", function)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(target)))
        signature = inspect.signature(function)
    except OSError, TypeError, SyntaxError, ValueError:
        return []
    parameter_names = set(signature.parameters)
    meta_names = {
        name
        for name in parameter_names
        if "meta" in name.casefold() or name in {"release", "item"}
    }
    fields = set(domain_fields)
    aliases = _aliases(tree, meta_names, parameter_names, fields)
    scenarios: list[Scenario] = []
    seen: set[str] = set()

    def add(assignments: Iterable[Assignment]) -> None:
        if len(scenarios) >= limit:
            return
        scenario = _as_scenario(assignments)
        key = repr(scenario)
        if key not in seen and any(scenario):
            seen.add(key)
            scenarios.append(scenario)

    for node in ast.walk(tree):
        if isinstance(node, ast.If | ast.IfExp | ast.While | ast.Assert):
            test = node.test
            for truth in (True, False):
                for assignments in _condition_options(
                    test,
                    truth=truth,
                    meta_names=meta_names,
                    parameter_names=parameter_names,
                    domain_fields=fields,
                    aliases=aliases,
                ):
                    add(assignments)
        elif isinstance(node, ast.Return) and node.value is not None:
            for truth in (True, False):
                for assignments in _condition_options(
                    node.value,
                    truth=truth,
                    meta_names=meta_names,
                    parameter_names=parameter_names,
                    domain_fields=fields,
                    aliases=aliases,
                ):
                    add(assignments)
        elif isinstance(node, ast.Compare | ast.Call):
            for truth in (True, False):
                for assignments in _condition_options(
                    node,
                    truth=truth,
                    meta_names=meta_names,
                    parameter_names=parameter_names,
                    domain_fields=fields,
                    aliases=aliases,
                ):
                    add(assignments)
        elif isinstance(node, ast.Match):
            target_info = _target(
                node.subject, meta_names, parameter_names, fields, aliases
            )
            if target_info is not None:
                for case in node.cases:
                    if isinstance(case.pattern, ast.MatchValue):
                        value = _literal(case.pattern.value)
                        if value is not None:
                            add([Assignment(*target_info, value)])
                    elif isinstance(case.pattern, ast.MatchSingleton):
                        add([Assignment(*target_info, case.pattern.value)])

    # Pairwise and bounded triple combinations exercise conjunctions split
    # across nested branches while keeping every contract run deterministic.
    base = list(scenarios)

    def add_merged(parts: tuple[Scenario, ...]) -> None:
        merged_meta: dict[str, object] = {}
        merged_params: dict[str, object] = {}
        for meta_values, parameter_values in parts:
            if any(
                key in merged_meta and merged_meta[key] != value
                for key, value in meta_values.items()
            ):
                return
            if any(
                key in merged_params and merged_params[key] != value
                for key, value in parameter_values.items()
            ):
                return
            merged_meta.update(meta_values)
            merged_params.update(parameter_values)
        add(
            [
                *(
                    Assignment("meta", key, value)
                    for key, value in merged_meta.items()
                ),
                *(
                    Assignment("parameter", key, value)
                    for key, value in merged_params.items()
                ),
            ]
        )

    for first_index, first in enumerate(base[:64]):
        for second in base[first_index + 1 : 64]:
            add_merged((first, second))
            if len(scenarios) >= limit:
                return scenarios

    for first_index, first in enumerate(base[:24]):
        for second_index, second in enumerate(
            base[first_index + 1 : 24], start=first_index + 1
        ):
            for third in base[second_index + 1 : 24]:
                add_merged((first, second, third))
                if len(scenarios) >= limit:
                    return scenarios

    # Finally combine one representative assignment for as many distinct
    # targets as possible. This reaches common deeply nested happy paths.
    representatives: list[Scenario] = []
    used_targets: set[tuple[str, str]] = set()
    for scenario in base:
        targets = {
            *(("meta", key) for key in scenario[0]),
            *(("parameter", key) for key in scenario[1]),
        }
        if targets and not targets & used_targets:
            representatives.append(scenario)
            used_targets.update(targets)
    if representatives:
        add_merged(tuple(representatives))
    return scenarios
