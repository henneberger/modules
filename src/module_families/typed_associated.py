"""Separate symbolic checking and concrete descriptor substitution for .mfl.

Only declared equations are solved. Operation calls cannot add equations.
Runtime substitution inspects sealed metadata, never providers or source code.
"""

from copy import deepcopy

from .associated import (
    resolve_metadata,
    validate_against_interface,
    validate_associated,
)
from .type_terms import (
    normalize_constructors,
    normalize_term,
    substitute,
    term_id,
    unify,
)


def identity(term):
    return term.get("nominal") or term_id(term)


def _primitive(value):
    if value["id"] in {"python." + p for p in ("str", "int", "float", "bool", "bytes")}:
        if (
            value["usage"] != "shared"
            or value["representation"] != value["id"].split(".")[1]
        ):
            raise ValueError(
                "canonical primitive identity requires its shared representation"
            )


def prepare(doc, specs):
    lookup = {(s["id"], s["version"]): s for s in specs}
    ports = {
        name: lookup[(p["requires"]["id"], p["requires"]["version"])]
        for name, p in doc["ports"].items()
    }
    ref = doc["module"]["provides"]
    result = lookup[(ref["id"], ref["version"])]
    registry = {}
    for spec in [*ports.values(), result, {"associated": doc.get("associated", {})}]:
        for name, declaration in normalize_constructors(
            spec.get("associated", {}).get("constructors", {})
        ).items():
            if name in registry and registry[name] != declaration:
                raise ValueError(f"associated constructor conflict: {name}")
            registry[name] = declaration
    from .module_syntax import associated_paths

    associated = associated_paths(doc.get("associated", {}), ports)
    associated["constructors"] = registry
    card = {"requires": doc["ports"], "associated": associated}
    validate_associated(card)
    validate_against_interface(card, result)
    bindings = {
        f"{port}.{name}": {"var": f"{port}.{name}", "kind": kind}
        for port, spec in ports.items()
        for name, kind in spec.get("associated", {}).get("types", {}).items()
    }

    def scope(term):
        def visit(node):
            node = dict(node)
            if "from" in node:
                path = node["from"]
                if path not in bindings or bindings[path]["kind"] != node["kind"]:
                    raise ValueError(
                        f"missing or incompatible associated port declaration: {path}"
                    )
                return bindings[path]
            if "args" in node:
                node["args"] = [visit(arg) for arg in node["args"]]
            return node

        return visit(normalize_term(term, registry))

    equations = [[scope(a), scope(b)] for a, b in associated.get("sharing", [])]
    for port, requirements in associated.get("requires", {}).items():
        for name, term in requirements.items():
            equations.append(
                [scope({"from": f"{port}.{name}", "kind": term["kind"]}), scope(term)]
            )
    solved = unify(equations, registry)
    terms = {}

    def specialize(spec, local):
        spec = deepcopy(spec)
        spec.pop("associated", None)
        if "typing" not in spec:
            raise ValueError(f"interface {spec['id']} has no typed contract")
        for value in spec["typing"]["types"].values():
            term = value.pop("term", None)
            if term is None:
                term = {"nominal": value["id"], "kind": value["usage"]}
            term = substitute(substitute(term, local, registry), solved, registry)
            value["id"] = identity(term)
            _primitive(value)
            terms[value["id"]] = term
        return spec

    scoped_ports = {
        port: specialize(
            spec,
            {
                name: bindings[f"{port}.{name}"]
                for name in spec.get("associated", {}).get("types", {})
            },
        )
        for port, spec in ports.items()
    }
    scoped_result = specialize(
        result,
        {name: scope(term) for name, term in associated.get("types", {}).items()},
    )
    assumptions = {
        "associated": associated,
        "ports": ports,
        "result": result,
        "terms": terms,
        "substitution": solved,
        "declarations": [
            value
            for spec in [*scoped_ports.values(), scoped_result]
            for value in spec["typing"]["types"].values()
        ],
    }
    return scoped_ports, scoped_result, assumptions


def specialize_runtime(assumptions, ports, type_scope=None):
    """Discharge declared equations before any operation or ownership transfer."""
    from .type_terms import fresh_scope
    if set(ports) != set(assumptions["ports"]):
        raise ValueError("checked program ports differ from certificate")
    metadata = {}
    for name, module in ports.items():
        metadata[name] = module.metadata()["associated"]
        validate_against_interface(
            {"associated": metadata[name]}, assumptions["ports"][name]
        )
    card = {"requires": assumptions["ports"], "associated": assumptions["associated"]}
    resolved = resolve_metadata(card, metadata, scope=type_scope)
    validate_against_interface({"associated": resolved}, assumptions["result"])
    registry = resolved["constructors"]
    bindings = {
        f"{port}.{name}": term
        for port, spec in metadata.items()
        for name, term in spec["types"].items()
    }
    mapping = {
        key: identity(fresh_scope(substitute(term, bindings, registry), type_scope or []))
        for key, term in assumptions["terms"].items()
    }
    seen = {}
    for value in descriptors(assumptions["declarations"], mapping):
        old = seen.setdefault(value["id"], value)
        if old != value:
            raise ValueError(f"inconsistent concrete type declaration: {value['id']}")
    return mapping


def descriptors(values, identities):
    """Specialize static call descriptors, preserving modes and representations."""
    result = [
        {**value, "id": identities.get(value["id"], value["id"])} for value in values
    ]
    for value in result:
        _primitive(value)
    return result
