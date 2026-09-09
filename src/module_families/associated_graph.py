"""Elaborate associated type witnesses and residual equations in open graphs."""

from __future__ import annotations

from .module_ir import constructor_identity
from .type_terms import (
    fresh_scope,
    normalize_constructors,
    normalize_term,
    substitute,
    unify,
)


def _merge(registries):
    result = {}
    for registry in registries:
        for name, declaration in normalize_constructors(registry).items():
            if name in result and result[name] != declaration:
                raise ValueError(f"conflicting type constructor: {name}")
            result[name] = declaration
    return result


def _terms(term):
    yield term
    for arg in term.get("args", []):
        yield from _terms(arg)


def graph_associated(doc, cards, order, interfaces):
    """Check kinded witnesses, substitute linked ports, export residual equations.

    Only public ports introduce flexible variables. A provider's declared
    nominal witnesses remain rigid even when their Python representations agree.
    """
    def spec(reference):
        return interfaces[(reference["id"], reference["version"])]

    own = doc.get("associated", {})
    constructors = _merge([
        *(value.get("associated", {}).get("constructors", {}) for value in interfaces.values()),
        *(card.get("associated", {}).get("constructors", {}) for card in cards.values()),
        own.get("constructors", {}),
    ])
    values, equations, origins = {}, [], []
    variable_kinds = {}

    def equation(left, right, origin):
        equations.append([left, right])
        origins.append(origin)

    def exported(reference, types, origin):
        kinds = spec(reference).get("associated", {}).get("types", {})
        if set(kinds) != set(types):
            raise ValueError(f"associated exports differ from signature at {origin}: expected {sorted(kinds)}, got {sorted(types)}")
        for name, kind in kinds.items():
            if types[name]["kind"] != kind:
                raise ValueError(f"associated type kind mismatch: {origin}.{name}")

    for alias, port in doc["ports"].items():
        values[alias] = {}
        for name, kind in spec(port["requires"]).get("associated", {}).get("types", {}).items():
            variable = "port:" + alias + "." + name
            variable_kinds[variable] = kind
            values[alias][name] = {"var": variable, "kind": kind}

    def lookup(path):
        owner, name = path.split(".")
        if name not in values.get(owner, {}):
            raise ValueError(f"missing associated type: {path}")
        return values[owner][name]

    def elaborate(term, bindings, scope=()):
        term = normalize_term(term, constructors)
        for piece in _terms(term):
            if "var" in piece:
                raise ValueError("graph witnesses use from paths, not free type variables")
            if "from" in piece and piece["from"] not in bindings:
                raise ValueError(f"unknown associated projection: {piece['from']}")
        return substitute(fresh_scope(term, scope, close=False), bindings, constructors)

    for alias in order:
        card = cards[alias]
        metadata = card.get("associated", {})
        bindings = {}
        for slot in card.get("requires", {}):
            provider = doc["links"][alias + "." + slot]
            supplied = values[provider]
            edge = alias + "." + slot
            if edge in doc.get("views", {}):
                renames = doc["views"][edge].get("associated", {})
                supplied = {name: supplied[renames.get(name, name)] for name in spec(card["requires"][slot]).get("associated", {}).get("types", {})}
                for name, term in doc["views"][edge].get("where", {}).items():
                    equation(supplied[name], normalize_term(term, constructors), "signature refinement: " + edge + "." + name)
            bindings.update({slot + "." + name: term for name, term in supplied.items()})
        scope = [alias, constructor_identity(card)]
        values[alias] = {name: elaborate(term, bindings, scope) for name, term in metadata.get("types", {}).items()}
        exported(card["provides"], values[alias], alias)
        for slot, constraints in metadata.get("requires", {}).items():
            for name, expected in constraints.items():
                path = slot + "." + name
                if path not in bindings:
                    raise ValueError(f"missing associated requirement: {alias}.{path}")
                equation(bindings[path], elaborate(expected, bindings, scope), alias + "." + path)
        for left, right in metadata.get("sharing", []):
            equation(elaborate(left, bindings, scope), elaborate(right, bindings, scope), alias + " sharing")
    graph_bindings = {alias + "." + name: value for alias, types in values.items() for name, value in types.items()}
    result = {name: elaborate(term, graph_bindings) for name, term in own.get("types", {}).items()}
    exported(doc["module"]["provides"], result, "result")
    for left, right in doc["constraints"].get("same_associated", []):
        equation(lookup(left), lookup(right), left + " = " + right)

    def value_type(interface, name, witnesses):
        descriptor = interface["typing"]["types"][name]
        if "id" in descriptor:
            term = {"nominal": descriptor["id"], "kind": descriptor["usage"]}
        else:
            term = substitute(descriptor["term"], witnesses, constructors)
        return term, descriptor["usage"], descriptor["representation"]

    target = spec(doc["module"]["provides"])
    if "typing" in target:
        for name, path in doc["exports"].items():
            if name not in target["callables"]:
                continue
            owner, export = path.split(".")
            source_ref = doc["ports"][owner]["requires"] if owner in doc["ports"] else cards[owner]["provides"]
            source = spec(source_ref)
            if "typing" not in source:
                raise ValueError(f"typed export requires a typed source contract: {name} <- {path}")
            actual = source["typing"]["operations"][export]
            expected = target["typing"]["operations"][name]
            if not set(actual["effects"]) <= set(expected["effects"]):
                raise ValueError(f"typed export effects exceed result contract: {name} <- {path}")
            if len(actual["parameters"]) != len(expected["parameters"]) or len(actual["returns"]) != len(expected["returns"]):
                raise ValueError(f"typed export contract mismatch: {name} <- {path}")
            pairs = []
            for (parameter, requirement), provided in zip(expected["parameters"].items(), actual["parameters"].values(), strict=True):
                if provided["mode"] != requirement["mode"]:
                    raise ValueError(f"typed export contract mismatch: {name}.{parameter} ownership mode")
                pairs.append((provided["type"], requirement["type"]))
            pairs.extend(zip(actual["returns"], expected["returns"], strict=True))
            for a, b in pairs:
                left, *left_properties = value_type(source, a, values[owner])
                right, *right_properties = value_type(target, b, result)
                if left_properties != right_properties:
                    raise ValueError(f"typed export contract mismatch: {name} <- {path}: usage or representation")
                equation(left, right, "typed export contract mismatch: " + name + " <- " + path)
    try:
        bindings = unify(equations, constructors)
    except ValueError as error:
        raise ValueError(f"associated type mismatch ({'; '.join(origins)}): {error}") from error

    def external(term):
        if "var" in term:
            variable = term["var"]
            if variable not in variable_kinds:
                raise ValueError(f"escaping abstract type variable: {variable}")
            return {"from": variable.removeprefix("port:"), "kind": term["kind"]}
        if "args" in term:
            return {**term, "args": [external(arg) for arg in term["args"]]}
        return term

    # Keep the solving obligations, not equations after both sides have been
    # rewritten to equality. Otherwise a published open graph loses constraints.
    residual = []
    for name, value in sorted(bindings.items()):
        left = {"var": name, "kind": variable_kinds[name]}
        residual.append([external(left), external(substitute(value, bindings, constructors))])
    published = {
        "types": {name: external(substitute(value, bindings, constructors)) for name, value in sorted(result.items())},
        "constructors": constructors,
        "sharing": residual,
    }
    return {"associated": published}
