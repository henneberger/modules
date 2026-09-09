"""Finite, import-free checking and enumeration of module expressions.

Compatibility here concerns declared metadata. Runtime interfaces, type object
identity and behavioral claims remain explicit obligations, not inferred proofs.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import re
from typing import Any

from .associated import resolve_metadata, validate_associated
from .indices import resolve_indices, validate_indices
from .instance_terms import resolve_instances, validate_instances
from .module_ir import (
    ModuleIRError,
    compile_unit,
    constructor_identity,
    normalize_expression,
    unit_type_scope,
)


class PlanningError(ValueError):
    """Malformed planning input or an implicit ambiguous selection."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningError(f"{label} must be a nonempty string")
    return value


def _reference(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"id", "version"}:
        raise PlanningError(f"{label} must contain exactly id and version")
    return {key: _text(value[key], f"{label}.{key}") for key in ("id", "version")}


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise PlanningError(f"{label} must be a collection of strings")
    return sorted({_text(item, label) for item in value})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _is_open(card: dict) -> bool:
    return (
        bool(card["requires"])
        or card.get("kind") == "functor"
        or card.get("contract_target")
        in {
            "named-module-factory",
            "module-returned-by-named-module-factory",
        }
    )


def _cards(candidates: Any) -> dict[str, dict]:
    if not isinstance(candidates, dict):
        raise PlanningError("candidates must map aliases to immutable member cards")
    result = {}
    for alias, candidate in candidates.items():
        _text(alias, "candidate alias")
        if not isinstance(candidate, dict):
            raise PlanningError(f"candidate {alias!r} must be an object")
        card = copy.deepcopy(candidate)
        for field in ("family", "id", "version"):
            _text(card.get(field), f"{alias}.{field}")
        digest = card.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise PlanningError(f"{alias}.sha256 must be a lowercase SHA-256 identity")
        card["provides"] = _reference(card.get("provides"), f"{alias}.provides")
        requirements = card.get("requires", {})
        if not isinstance(requirements, dict):
            raise PlanningError(
                f"{alias}.requires must map slots to exact contract references"
            )
        card["requires"] = {
            _text(slot, "requirement slot"): _reference(ref, f"{alias}.requires.{slot}")
            for slot, ref in requirements.items()
        }
        card["effects"] = _strings(card.get("effects", ["unknown"]), f"{alias}.effects")
        sharing = card.get("sharing", [])
        if not isinstance(sharing, list):
            raise PlanningError(
                f"{alias}.sharing must be a list of slot.Type equalities"
            )
        for pair in sharing:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise PlanningError(
                    f"{alias}.sharing entries must contain two slot.Type paths"
                )
            for path in pair:
                if not isinstance(path, str) or path.count(".") != 1:
                    raise PlanningError(f"{alias}: invalid shared type path {path!r}")
                slot, name = path.split(".")
                if slot not in requirements or not name.isidentifier():
                    raise PlanningError(
                        f"{alias}: shared type path {path!r} is outside declared slots"
                    )
        type_exports = card.get("type_exports", {})
        if not isinstance(type_exports, dict):
            raise PlanningError(
                f"{alias}.type_exports must map type names to manifest identities"
            )
        for name, identity in type_exports.items():
            if not isinstance(name, str) or not name.isidentifier():
                raise PlanningError(f"{alias}: invalid type export {name!r}")
            _text(identity, f"{alias}.type_exports.{name}")
        card["type_exports"] = type_exports
        validate_instances(card)
        validate_indices(card)
        validate_associated(card)
        result[alias] = card
    try:
        _canonical(result)
    except (TypeError, ValueError) as error:
        raise PlanningError("candidate metadata must be finite JSON data") from error
    return result


def _expression(
    expression: Any, candidates: dict, holes: dict, path: str = "$"
) -> None:
    if not isinstance(expression, dict):
        raise PlanningError(f"{path}: expression must be an object")
    if "hole" in expression:
        if set(expression) != {"hole", "requires"}:
            raise PlanningError(
                f"{path}: hole expressions contain only hole and requires"
            )
        name = _text(expression["hole"], f"{path}.hole")
        ref = _reference(expression["requires"], f"{path}.requires")
        if name in holes and holes[name] != ref:
            raise PlanningError(
                f"hole {name!r} is repeated with different contract requirements"
            )
        holes[name] = ref
        return
    if "use" not in expression or set(expression) - {"use", "with"}:
        raise PlanningError(f"{path}: expression must contain use and optional with")
    alias = _text(expression["use"], f"{path}.use")
    if alias not in candidates:
        raise PlanningError(f"{path}: unknown candidate alias {alias!r}")
    if "with" in expression:
        bindings = expression["with"]
        if not isinstance(bindings, dict):
            raise PlanningError(f"{path}.with must map slots to expressions")
        for slot, child in bindings.items():
            _text(slot, "binding slot")
            _expression(child, candidates, holes, f"{path}.with.{slot}")


class _Rejected(Exception):
    def __init__(self, code: str, path: str, alias: str, **details):
        self.record = {"code": code, "path": path, "alias": alias, **details}


def _check(
    expression: dict,
    candidates: dict,
    bindings: dict,
    allowed: set[str] | None,
    path: str = "$",
    resolved: dict | None = None,
    type_scope=None,
) -> dict:
    if "ref" in expression:
        if resolved is None or expression["ref"] not in resolved:
            raise PlanningError("unresolved module reference")
        return resolved[expression["ref"]]
    if "hole" in expression:
        alias = bindings[expression["hole"]]
        return _check({"use": alias}, candidates, bindings, allowed, path, resolved, type_scope)
    alias = expression["use"]
    card = candidates[alias]
    open_ = _is_open(card)
    arguments = expression.get("with", {})
    if open_ and "with" not in expression:
        raise _Rejected("unfilled-constructor", path, alias, requires=card["requires"])
    if not open_ and "with" in expression:
        raise _Rejected("closed-provider-application", path, alias)
    missing = sorted(set(card["requires"]) - set(arguments))
    extra = sorted(set(arguments) - set(card["requires"]))
    if missing or extra:
        raise _Rejected("slot-mismatch", path, alias, missing=missing, extra=extra)
    children = {}
    residuals = []
    selected = {alias}
    effects = set(card["effects"])
    if open_:
        effects.discard("dependency-effects")
    concrete = {"use": alias}
    if open_:
        concrete["with"] = {}
    for slot in sorted(arguments):
        child = _check(
            arguments[slot], candidates, bindings, allowed, f"{path}.with.{slot}", resolved, type_scope
        )
        expected = card["requires"][slot]
        if child["provides"] != expected:
            raise _Rejected(
                "contract-mismatch",
                f"{path}.with.{slot}",
                alias,
                required=expected,
                actual=child["provides"],
            )
        children[slot] = child
        concrete["with"][slot] = (dict(arguments[slot]) if "ref" in arguments[slot] else child["expression"])
        if resolved is None:
            residuals.extend(child["residual_obligations"])
        selected.update(child["selected"])
        effects.update(child["effects"])
    unresolved_effects = effects & {"unknown", "dependency-effects"}
    if allowed is not None:
        if unresolved_effects:
            raise _Rejected(
                "unknown-effects",
                path,
                alias,
                effects=sorted(effects),
                allowed=sorted(allowed),
            )
        if not effects.issubset(allowed):
            raise _Rejected(
                "effect-policy",
                path,
                alias,
                effects=sorted(effects),
                allowed=sorted(allowed),
            )
    if unresolved_effects:
        residuals.append(
            {
                "kind": "effects",
                "path": path,
                "alias": alias,
                "unresolved": sorted(unresolved_effects),
            }
        )
    try:
        indices = resolve_indices(card, {slot: child["indices"] for slot, child in children.items()})
    except ValueError as error:
        raise _Rejected("semantic-index-mismatch", path, alias, reason=str(error)) from error
    try:
        associated = resolve_metadata(card, {slot: child["associated"] for slot, child in children.items()}, scope=[*(type_scope or []), path, constructor_identity(card)])
    except ValueError as error:
        raise _Rejected("associated-type-mismatch", path, alias, reason=str(error)) from error
    try:
        instances = resolve_instances(card, children, scope=path)
    except ValueError as error:
        raise _Rejected("instance-sharing-mismatch", path, alias, reason=str(error)) from error
    for left, right in card.get("sharing", []):
        left_slot, left_type = left.split(".")
        right_slot, right_type = right.split(".")
        left_id = children[left_slot]["type_exports"].get(left_type)
        right_id = children[right_slot]["type_exports"].get(right_type)
        if left_id is not None and right_id is not None:
            if left_id != right_id:
                raise _Rejected(
                    "type-sharing-mismatch",
                    path,
                    alias,
                    sharing=[left, right],
                    identities=[left_id, right_id],
                )
        else:
            residuals.append(
                {
                    "kind": "runtime-sharing",
                    "path": path,
                    "alias": alias,
                    "sharing": [left, right],
                    "known_identities": [left_id, right_id],
                }
            )
    for field in ("behavior", "assumes", "guarantees", "assumptions", "obligations"):
        if field in card:
            residuals.append(
                {
                    "kind": "behavioral",
                    "path": path,
                    "alias": alias,
                    "field": field,
                    "claim": copy.deepcopy(card[field]),
                }
            )
    residuals.append(
        {
            "kind": "runtime-interface",
            "path": path,
            "alias": alias,
            "provides": card["provides"],
        }
    )
    return {
        "expression": concrete,
        "instance_id": path,
        "instances": instances,
        "provides": card["provides"],
        "effects": sorted(effects),
        "type_exports": card["type_exports"],
        "indices": indices,
        "associated": associated,
        "residual_obligations": residuals,
        "selected": selected,
    }


def plan(
    expression: dict,
    candidates: dict,
    allowed_effects=None,
    max_solutions: int = 16,
    max_states: int = 10000,
) -> dict:
    """Enumerate finite admissible bindings without importing candidate code.

    Holes range only over supplied closed providers, including explicitly
    declared zero-argument module factories. Constructors requiring named
    modules must already appear as explicit applications in the expression.
    ``max_states`` counts complete hole-binding assignments checked.
    """
    for value, name in ((max_solutions, "max_solutions"), (max_states, "max_states")):
        if type(value) is not int or value < 1:
            raise PlanningError(f"{name} must be a positive integer")
    cards = _cards(candidates)
    allowed = (
        None
        if allowed_effects is None
        else set(_strings(allowed_effects, "allowed_effects"))
    )
    holes: dict[str, dict] = {}
    try:
        graph = normalize_expression(expression)
    except ModuleIRError as error:
        raise PlanningError(str(error)) from error
    for node in graph.nodes.values():
        shallow = {key: value for key, value in node.items() if key != "with"}
        _expression(shallow, cards, holes)
    rejections: dict[str, dict] = {}
    rejection_count = 0

    def reject(record: dict) -> None:
        nonlocal rejection_count
        key = _canonical(record)
        if key not in rejections:
            rejection_count += 1
            # Bound diagnostic output independently of the search budget.
            if len(rejections) < 256:
                rejections[key] = record

    domains = {}
    for hole, requirement in sorted(holes.items()):
        domains[hole] = []
        for alias, card in sorted(cards.items()):
            if _is_open(card):
                reject({"code": "open-provider", "hole": hole, "alias": alias})
            elif card["provides"] != requirement:
                reject(
                    {
                        "code": "contract-mismatch",
                        "hole": hole,
                        "alias": alias,
                        "required": requirement,
                        "actual": card["provides"],
                    }
                )
            else:
                domains[hole].append(alias)
        if not domains[hole]:
            reject(
                {"code": "no-closed-provider", "hole": hole, "requires": requirement}
            )
    names = sorted(domains)
    total = math.prod(len(domains[name]) for name in names)
    visited = 0
    solutions = []
    for alternatives in itertools.product(*(domains[name] for name in names)):
        if visited >= max_states or len(solutions) >= max_solutions:
            break
        visited += 1
        bindings = dict(zip(names, alternatives, strict=True))
        try:
            resolved = {}
            concrete_nodes = {name: ({"use": bindings[node["hole"]]} if "hole" in node else node) for name, node in graph.nodes.items()}
            identities = {node["use"]: constructor_identity(cards[node["use"]]) for node in concrete_nodes.values()}
            type_scope = unit_type_scope({}, concrete_nodes, identities)
            for node_id in graph.order:
                resolved[node_id] = _check(graph.nodes[node_id], cards, bindings, allowed, node_id, resolved, type_scope)
            checked = resolved[graph.root]
            checked["expression"] = graph.expression(bindings)
            checked["residual_obligations"] = [item for node in resolved.values() for item in node["residual_obligations"]]
        except _Rejected as rejected:
            reject({**rejected.record, "bindings": bindings})
            continue
        residuals = {_canonical(item): item for item in checked["residual_obligations"]}
        solutions.append(
            {
                "expression": checked["expression"],
                "unit": compile_unit(
                    ports={}, nodes={name: ({"use": bindings[node["hole"]]} if "hole" in node else node) for name, node in graph.nodes.items()},
                    exports={"module": {"ref": graph.root}}, signature=checked["provides"], identities=identities,
                ),
                "bindings": bindings,
                "provides": copy.deepcopy(checked["provides"]),
                "effects": checked["effects"],
                "type_exports": copy.deepcopy(checked["type_exports"]),
                "indices": copy.deepcopy(checked["indices"]),
                "associated": copy.deepcopy(checked["associated"]),
                "residual_obligations": [residuals[key] for key in sorted(residuals)],
                "selected": [
                    {
                        "alias": alias,
                        **{
                            field: cards[alias][field]
                            for field in ("family", "id", "version", "sha256")
                        },
                    }
                    for alias in sorted(checked["selected"])
                ],
            }
        )
    complete = visited == total
    status = (
        "incomplete"
        if not complete
        else "unsatisfied"
        if not solutions
        else "unique"
        if len(solutions) == 1
        else "ambiguous"
    )
    return {
        "status": status,
        "solutions": solutions,
        "rejections": list(rejections.values()),
        "complete_for_candidates": complete,
        "visited_states": visited,
        "candidate_assignments": total,
        "rejections_omitted": max(0, rejection_count - len(rejections)),
        "limits": {"max_solutions": max_solutions, "max_states": max_states},
    }


def select(result: dict, index: int | None = None) -> dict:
    """Choose an explicitly indexed solution, or the sole complete solution."""
    if not isinstance(result, dict) or not isinstance(result.get("solutions"), list):
        raise PlanningError("selection requires a planner result")
    solutions = result["solutions"]
    if index is None:
        if (
            result.get("status") != "unique"
            or result.get("complete_for_candidates") is not True
            or len(solutions) != 1
        ):
            raise PlanningError(
                "selection requires an explicit index unless the complete plan is unique"
            )
        index = 0
    if type(index) is not int or not 0 <= index < len(solutions):
        raise PlanningError("solution index is outside the discovered solutions")
    return copy.deepcopy(solutions[index])
