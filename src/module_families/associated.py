"""Associated type witnesses and substitution across module dependencies.

Witnesses are immutable declarations checked before implementation execution;
they do not establish that arbitrary Python obeys the declared value types.
"""
from __future__ import annotations

import keyword
from copy import deepcopy

from .type_terms import fresh_scope, normalize_constructors, normalize_term, substitute


def _name(value):
    if not isinstance(value, str) or not value.isidentifier() or keyword.iskeyword(value):
        raise ValueError(f"associated type names must be identifiers: {value!r}")


def _walk(term):
    yield term
    for arg in term.get("args", []):
        yield from _walk(arg)


def validate_associated(card):
    """Validate a member's finite witness language, leaving ports unresolved."""
    spec = card.get("associated", {})
    if not isinstance(spec, dict) or set(spec) - {"types", "requires", "sharing", "constructors"}:
        raise ValueError("associated must contain types, requires, sharing, or constructors")
    constructors = normalize_constructors(spec.get("constructors", {}))
    ports = card.get("requires", {})
    if not isinstance(ports, dict):
        raise ValueError("requires must map dependency slots to interfaces")

    binder_kinds = {}

    def term(value):
        normalized = normalize_term(value, constructors)
        for item in _walk(normalized):
            if "fresh" in item:
                binder = tuple(item["fresh"])
                if binder in binder_kinds and binder_kinds[binder] != item["kind"]:
                    raise ValueError("fresh abstract binder has inconsistent kinds")
                binder_kinds[binder] = item["kind"]
            if "var" in item:
                raise ValueError("member associated witnesses cannot contain inference variables")
            if "from" in item:
                path = item["from"]
                if path.count(".") != 1:
                    raise ValueError("associated paths must be slot.Name")
                slot, name = path.split(".")
                _name(name)
                if slot not in ports:
                    raise ValueError(f"associated path outside required ports: {path}")
        return normalized

    types = spec.get("types", {})
    requirements = spec.get("requires", {})
    if not isinstance(types, dict) or not isinstance(requirements, dict):
        raise ValueError("associated types and requires must be tables")
    for name, value in types.items():
        _name(name)
        term(value)
    for slot, expected in requirements.items():
        if slot not in ports or not isinstance(expected, dict):
            raise ValueError(f"associated requirement outside required ports: {slot}")
        for name, value in expected.items():
            _name(name)
            term(value)
    sharing = spec.get("sharing", [])
    if not isinstance(sharing, (list, tuple)):
        raise ValueError("associated sharing must contain pairs of type terms")
    for pair in sharing:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("associated sharing must contain pairs of type terms")
        for value in pair:
            term(value)


def _inputs(card, children, constructors=None):
    registry = normalize_constructors(constructors or {})
    tables = {}
    for slot, child in children.items():
        if not isinstance(child, dict):
            raise ValueError(f"associated dependency {slot} must be a table")
        if "types" in child and isinstance(child["types"], dict):
            tables[slot] = child["types"]
            extra = child.get("constructors", {})
        else:
            tables[slot] = child
            extra = {}
        for name, declaration in normalize_constructors(extra).items():
            if name in registry and registry[name] != declaration:
                raise ValueError(f"associated constructor conflict: {name}")
            registry[name] = declaration
    for name, declaration in normalize_constructors(card.get("associated", {}).get("constructors", {})).items():
        if name in registry and registry[name] != declaration:
            raise ValueError(f"associated constructor conflict: {name}")
        registry[name] = declaration
    return tables, registry


def resolve_associated(card, children, *, constructors=None, scope=None):
    """Resolve closed output witnesses; reject unsatisfied dependency equations.

    Children map slots to associated metadata ``{types, constructors}``, or to
    plain witness tables when there are no constructor declarations to merge.
    """
    tables, registry = _inputs(card, children, constructors)
    checked_card = {"requires": dict(card.get("requires", {})), "associated": deepcopy(card.get("associated", {}))}
    checked_card["associated"]["constructors"] = registry
    validate_associated(checked_card)
    spec = card.get("associated", {})
    bindings = {}
    for slot, table in tables.items():
        for name, value in table.items():
            _name(name)
            normalized = normalize_term(value, registry)
            if any("from" in item or "var" in item for item in _walk(normalized)):
                raise ValueError(f"unresolved associated dependency: {slot}.{name}")
            bindings[f"{slot}.{name}"] = normalized

    def resolved(value):
        result = substitute(value, bindings, registry)
        if any("fresh" in item for item in _walk(result)):
            if scope is None:
                raise ValueError("generated abstract types require an instantiation scope")
            result = fresh_scope(result, scope)
        if any("from" in item or "var" in item for item in _walk(result)):
            raise ValueError(f"missing associated type binding: {value!r}")
        return result

    for slot, expected in spec.get("requires", {}).items():
        for name, value in expected.items():
            actual = bindings.get(f"{slot}.{name}")
            if actual is None or actual != resolved(value):
                raise ValueError(f"associated type mismatch: {slot}.{name}")
    for left, right in spec.get("sharing", []):
        if resolved(left) != resolved(right):
            raise ValueError(f"associated type sharing mismatch: {left!r} != {right!r}")
    return {name: resolved(value) for name, value in spec.get("types", {}).items()}


def resolve_metadata(card, children, *, scope=None):
    """Return closed witnesses together with their consistent constructor registry."""
    _, constructors = _inputs(card, children)
    return {"types": resolve_associated(card, children, constructors=constructors, scope=scope), "constructors": constructors}


def validate_against_interface(card, interface):
    """Check declared output witnesses against an available immutable interface.

    Open witnesses are checked by kind here; their equations are discharged
    after concrete dependency selection. Extra exports are not hidden silently.
    """
    expected = interface.get("associated", {})
    actual = card.get("associated", {})
    types = actual.get("types", {})
    kinds = expected.get("types", {})
    if set(types) != set(kinds):
        raise ValueError(f"associated exports differ from interface: expected {sorted(kinds)}, got {sorted(types)}")
    _, registry = _inputs(card, {}, expected.get("constructors", {}))
    for name, value in types.items():
        if normalize_term(value, registry)["kind"] != kinds[name]:
            raise ValueError(f"associated kind mismatch: {name}")
