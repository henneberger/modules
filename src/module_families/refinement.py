from __future__ import annotations

from .contracts import _check_call_acceptance
from .interfaces import signature_from_spec, validate_interface
from .type_terms import normalize_term, substitute


def refine_signature(actual, expected, view=None):
    actual, expected = validate_interface(actual), validate_interface(expected)
    view = dict(view or {})
    if set(view) - {"exports", "associated", "instances", "where"}:
        raise ValueError("signature views accept exports, associated types, and instance roles")
    maps = {}
    for category, target, source in (
        ("exports", set(expected["callables"]) | set(expected["types"]), set(actual["callables"]) | set(actual["types"])),
        ("associated", set(expected.get("associated", {}).get("types", {})), set(actual.get("associated", {}).get("types", {}))),
        ("instances", set(expected.get("instances", [])), set(actual.get("instances", []))),
    ):
        renames = view.get(category, {})
        if not isinstance(renames, dict) or set(renames) - target:
            raise ValueError(f"invalid {category} projection")
        maps[category] = {name: renames.get(name, name) for name in target}
        if any(not isinstance(name, str) or name not in source for name in maps[category].values()):
            raise ValueError(f"signature does not supply required {category}")
    source_shape, target_shape = signature_from_spec(actual), signature_from_spec(expected)
    for name in expected["types"]:
        if maps["exports"][name] not in actual["types"]:
            raise ValueError(f"type projection is not a type: {name}")
    registry = {}
    for spec in (actual, expected):
        for name, declaration in spec.get("associated", {}).get("constructors", {}).items():
            if name in registry and registry[name] != declaration:
                raise ValueError(f"conflicting type constructor in signature refinement: {name}")
            registry[name] = declaration
    bindings = {}
    for name, source in maps["associated"].items():
        kind = expected["associated"]["types"][name]
        if kind != actual["associated"]["types"][source]:
            raise ValueError(f"associated kind differs in signature refinement: {name}")
        bindings[name] = {"var": "source:" + source, "kind": kind}

    actual_bindings = {name: {"var": "source:" + name, "kind": kind} for name, kind in actual.get("associated", {}).get("types", {}).items()}
    refinements = view.get("where", {})
    if not isinstance(refinements, dict) or set(refinements) - set(maps["associated"]):
        raise ValueError("type refinements must name public associated types")
    def closed(term):
        return not (set(term) & {"var", "from", "fresh"}) and all(closed(arg) for arg in term.get("args", []))
    maps["where"] = {}
    for name, value in refinements.items():
        term = normalize_term(value, registry)
        if not closed(term) or term["kind"] != expected["associated"]["types"][name]:
            raise ValueError(f"type refinement requires a closed term of the declared kind: {name}")
        maps["where"][name] = term
    fixed = {}
    for name, term in maps["where"].items():
        source = maps["associated"][name]
        if source in fixed and fixed[source] != term:
            raise ValueError(f"conflicting refinements for associated type: {source}")
        fixed[source] = term
    actual_bindings.update(fixed)
    for name, source in maps["associated"].items():
        if source in fixed:
            bindings[name] = fixed[source]

    def descriptor(spec, name, substitutions):
        value = spec["typing"]["types"][name]
        term = value.get("term", {"nominal": value.get("id"), "kind": value["usage"]})
        return substitute(term, substitutions, registry), value["usage"], value["representation"]

    for name, target in target_shape.callables.items():
        source_name = maps["exports"][name]
        source = source_shape.callables.get(source_name)
        if source is None or source.asynchronous != target.asynchronous:
            raise ValueError(f"operation kind differs in signature refinement: {name}")
        _check_call_acceptance(target.signature, source.signature, name)
        if "typing" not in expected:
            continue
        if "typing" not in actual:
            raise ValueError(f"typed signature refinement requires typed implementation: {name}")
        a, b = actual["typing"]["operations"][source_name], expected["typing"]["operations"][name]
        if not set(a["effects"]) <= set(b["effects"]):
            raise ValueError(f"refinement increases effects: {name}")
        ap, bp = list(a["parameters"].values()), list(b["parameters"].values())
        if len(ap) != len(bp) or len(a["returns"]) != len(b["returns"]):
            raise ValueError(f"typed operation arity differs: {name}")
        pairs = []
        for left, right in zip(ap, bp, strict=True):
            if left["mode"] != right["mode"]:
                raise ValueError(f"refinement changes ownership mode: {name}")
            pairs.append((left["type"], right["type"]))
        pairs.extend(zip(a["returns"], b["returns"], strict=True))
        for left, right in pairs:
            if descriptor(actual, left, actual_bindings) != descriptor(expected, right, bindings):
                raise ValueError(f"refinement changes a value type: {name}")
    return maps


def project_module(module, actual, expected, view=None):
    maps = refine_signature(actual, expected, view)
    if module.provides != {key: actual[key] for key in ("id", "version")}:
        raise ValueError("module does not implement the source of its signature view")
    metadata = module.metadata()
    from .associated import validate_against_interface

    validate_against_interface({"associated": metadata["associated"]}, actual)
    for name, term in maps["where"].items():
        if metadata["associated"]["types"][maps["associated"][name]] != term:
            raise ValueError(f"module does not satisfy associated type refinement: {name}")
    result = signature_from_spec(expected).seal(
        {name: module[source] for name, source in maps["exports"].items()},
        identity=module.identity,
        associated={"types": {name: metadata["associated"]["types"][source] for name, source in maps["associated"].items()}, "constructors": metadata["associated"]["constructors"]},
        instances={name: metadata["instances"][source] for name, source in maps["instances"].items()},
        indices=metadata["indices"],
    )
    object.__setattr__(result, "_instance_id", module.instance_id)
    return result
