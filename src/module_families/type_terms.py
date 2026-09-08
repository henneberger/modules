"""Bounded first-order associated types; no Python implementation is inspected.

Variables are scoped names supplied by the caller. Nominal identities and constructor
names are rigid: unification never equates implementations by structural resemblance.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

KINDS = frozenset({"identity", "shared", "affine", "linear"})
MAX_DEPTH = 32
MAX_NODES = 1000


class TypeTermError(ValueError):
    """A malformed or inconsistent associated type expression."""


def _name(value, context):
    if not isinstance(value, str) or not value.strip():
        raise TypeTermError(f"{context} must be a nonempty string")
    return value


def _kind(value, context):
    if not isinstance(value, str) or value not in KINDS:
        raise TypeTermError(f"{context}: unknown kind {value!r}")
    return value


def normalize_constructors(constructors=None):
    """Validate a fixed-arity constructor registry and return a defensive copy."""
    if constructors is None:
        constructors = {}
    if not isinstance(constructors, Mapping):
        raise TypeTermError("constructors must be a mapping")
    result = {}
    for name, spec in sorted(constructors.items(), key=lambda item: str(item[0])):
        _name(name, "constructor name")
        if not isinstance(spec, Mapping) or set(spec) != {"parameters", "result"}:
            raise TypeTermError(f"constructor {name}: expected parameters and result")
        if not isinstance(spec["parameters"], list):
            raise TypeTermError(f"constructor {name}: parameters must be a list")
        result[name] = {
            "parameters": [_kind(k, f"constructor {name} parameter") for k in spec["parameters"]],
            "result": _kind(spec["result"], f"constructor {name} result"),
        }
    return result


def _normalize(term, constructors, *, check_constructors=True):
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise TypeTermError(f"type term exceeds depth {MAX_DEPTH} or size {MAX_NODES}")
        if not isinstance(node, Mapping):
            raise TypeTermError("type term must be a mapping")
        tags = [key for key in ("nominal", "var", "from", "apply") if key in node]
        if len(tags) != 1:
            raise TypeTermError("type term requires exactly one of nominal, var, from, apply")
        tag = tags[0]
        expected = {tag, "kind", "args"} if tag == "apply" else {tag, "kind"}
        if set(node) != expected:
            raise TypeTermError(f"{tag} type term requires exactly {sorted(expected)}")
        name = _name(node[tag], f"{tag} identity")
        kind = _kind(node["kind"], f"{tag} {name}")
        if tag == "from" and ("." not in name or any(not part for part in name.split("."))):
            raise TypeTermError("from must be a dotted slot.Name path")
        result = {tag: name, "kind": kind}
        if tag == "apply":
            if not isinstance(node["args"], list):
                raise TypeTermError(f"constructor {name}: args must be a list")
            args = [visit(arg, depth + 1) for arg in node["args"]]
            if check_constructors:
                if name not in constructors:
                    raise TypeTermError(f"unknown constructor {name}")
                spec = constructors[name]
                if len(args) != len(spec["parameters"]):
                    raise TypeTermError(f"constructor {name}: expected arity {len(spec['parameters'])}, got {len(args)}")
                if kind != spec["result"]:
                    raise TypeTermError(f"constructor {name}: result kind must be {spec['result']}")
                for position, (arg, expected_kind) in enumerate(zip(args, spec["parameters"], strict=True)):
                    if arg["kind"] != expected_kind:
                        raise TypeTermError(f"constructor {name}: argument {position} kind must be {expected_kind}")
            result["args"] = args
        return result

    return visit(term, 0)


def normalize_term(term, constructors=None):
    """Validate syntax, kinds, arities and bounds, returning a defensive copy."""
    return _normalize(term, normalize_constructors(constructors))


def collect_variables(term):
    """Return scoped variable names; unresolved paths are rigid, not variables."""
    term = _normalize(term, {}, check_constructors=False)
    result = set()

    def visit(node):
        if "var" in node:
            result.add(node["var"])
        for arg in node.get("args", []):
            visit(arg)

    visit(term)
    return result


def term_id(term):
    """Canonical identity for a well-shaped term, including symbolic terms.

    Constructor declarations are checked by normalize_term before integration;
    this hashing helper does not have or infer a constructor registry.
    """
    normalized = _normalize(term, {}, check_constructors=False)
    data = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "type:" + hashlib.sha256(data.encode()).hexdigest()


def substitute(term, bindings, constructors=None):
    """Recursively replace exact var/from names, rejecting cycles and kind changes."""
    registry = normalize_constructors(constructors)
    source = _normalize(term, registry)
    if not isinstance(bindings, Mapping):
        raise TypeTermError("bindings must be a mapping")
    normalized = {_name(k, "binding name"): _normalize(v, registry) for k, v in bindings.items()}
    visits = 0

    def visit(node, active, depth):
        nonlocal visits
        visits += 1
        if depth > MAX_DEPTH or visits > MAX_NODES:
            raise TypeTermError("substitution exceeds type term bounds")
        key = node.get("var", node.get("from"))
        if key is not None and key in normalized:
            if key in active:
                raise TypeTermError(f"cyclic substitution (occurs check): {key}")
            replacement = normalized[key]
            if replacement["kind"] != node["kind"]:
                raise TypeTermError(f"substitution {key}: kind mismatch {node['kind']} != {replacement['kind']}")
            return visit(replacement, active | {key}, depth + 1)
        if "apply" in node:
            return {"apply": node["apply"], "kind": node["kind"],
                    "args": [visit(arg, active, depth + 1) for arg in node["args"]]}
        return dict(node)

    return _normalize(visit(source, set(), 0), registry)


def unify(equations, constructors=None):
    """Solve first-order equations, returning an idempotent variable substitution.

    Unresolved ``from`` paths remain rigid. Graph elaboration must resolve them
    or turn them into scoped variables before calling this function.
    """
    registry = normalize_constructors(constructors)
    if not isinstance(equations, (list, tuple)):
        raise TypeTermError("equations must be a sequence of term pairs")
    pending = []
    variable_kinds = {}
    for number, equation in enumerate(equations, 1):
        if not isinstance(equation, (list, tuple)) or len(equation) != 2:
            raise TypeTermError(f"equation {number}: expected two terms")
        try:
            pair = [_normalize(term, registry) for term in equation]
            def inspect(node):
                if "var" in node:
                    name = node["var"]
                    if name in variable_kinds and variable_kinds[name] != node["kind"]:
                        raise TypeTermError(f"variable {name} has inconsistent kinds")
                    variable_kinds[name] = node["kind"]
                for arg in node.get("args", []):
                    inspect(arg)
            for term in pair:
                inspect(term)
        except TypeTermError as exc:
            raise TypeTermError(f"equation {number}: {exc}") from exc
        pending.append((pair[0], pair[1], number))
    bindings = {}
    while pending:
        left, right, number = pending.pop(0)
        try:
            left = substitute(left, bindings, registry)
            right = substitute(right, bindings, registry)
            if left == right:
                continue
            if left["kind"] != right["kind"]:
                raise TypeTermError(f"kind mismatch {left['kind']} != {right['kind']}")
            if "var" in left or "var" in right:
                if "var" not in left or ("var" in right and left["var"] < right["var"]):
                    left, right = right, left
                name = left["var"]
                if name in collect_variables(right):
                    raise TypeTermError(f"occurs check failed for {name}")
                bindings[name] = right
            elif "apply" in left and "apply" in right and left["apply"] == right["apply"]:
                pending[0:0] = [(a, b, number) for a, b in zip(left["args"], right["args"], strict=True)]
            else:
                raise TypeTermError(f"rigid type mismatch: {left!r} != {right!r}")
        except TypeTermError as exc:
            raise TypeTermError(f"equation {number}: {exc}") from exc
    return {name: substitute(value, bindings, registry) for name, value in sorted(bindings.items())}
