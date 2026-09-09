from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass


class ModuleIRError(ValueError):
    pass


def dependency_order(dependencies):
    pending = {name: set(values) for name, values in dependencies.items()}
    unknown = set().union(*pending.values()) - set(pending) if pending else set()
    if unknown:
        raise ModuleIRError(f"unknown module bindings: {sorted(unknown)}")
    order = []
    while pending:
        ready = sorted(name for name, values in pending.items() if not values)
        if not ready:
            raise ModuleIRError(f"module initialization cycle: {sorted(pending)}")
        order.extend(ready)
        for name in ready:
            del pending[name]
        for values in pending.values():
            values.difference_update(ready)
    return tuple(order)


@dataclass(frozen=True)
class ModuleGraph:
    nodes: dict
    root: str
    order: tuple[str, ...]
    source: dict

    def expression(self, bindings):
        def fill(node):
            if "hole" in node:
                return {"use": bindings[node["hole"]]}
            if "ref" in node:
                return dict(node)
            if "let" in node:
                return {
                    "let": {name: fill(value) for name, value in sorted(node["let"].items())},
                    "in": fill(node["in"]),
                }
            result = {"use": node["use"]}
            if "with" in node:
                result["with"] = {name: fill(value) for name, value in sorted(node["with"].items())}
            return result

        return fill(self.source)

    def used(self):
        return {node["use"] for node in self.nodes.values() if "use" in node}


def normalize_expression(expression, *, max_depth=64):
    nodes = {}
    names = {}
    active = set()
    resolving = set()
    definitions = {}

    def name(value, label):
        if not isinstance(value, str) or not value or not value.isidentifier():
            raise ModuleIRError(f"{label} must be an identifier")
        return value

    if isinstance(expression, dict) and "let" in expression:
        if set(expression) != {"let", "in"} or not isinstance(expression["let"], dict):
            raise ModuleIRError("module bindings require let and in")
        definitions = expression["let"]
        for key in definitions:
            name(key, "module binding")
        root_expression = expression["in"]
    else:
        root_expression = expression

    def binding(key, depth):
        name(key, "module reference")
        if key not in definitions:
            raise ModuleIRError(f"unknown module binding: {key}")
        if key in resolving:
            raise ModuleIRError(f"module initialization cycle through {key}")
        if key not in names:
            resolving.add(key)
            names[key] = visit(definitions[key], f"$.let.{key}", depth)
            resolving.remove(key)
        return names[key]

    def visit(node, path, depth):
        if depth > max_depth:
            raise ModuleIRError(f"module expression exceeds depth {max_depth}")
        if not isinstance(node, dict):
            raise ModuleIRError(f"{path}: module expression must be an object")
        if id(node) in active:
            raise ModuleIRError(f"recursive expression object at {path}")
        active.add(id(node))
        try:
            if "ref" in node:
                if set(node) != {"ref"}:
                    raise ModuleIRError(f"{path}: references contain only ref")
                return binding(node["ref"], depth)
            if "hole" in node:
                if set(node) != {"hole", "requires"}:
                    raise ModuleIRError(f"{path}: holes require hole and requires")
                name(node["hole"], "module hole")
                nodes[path] = deepcopy(node)
                return path
            if "use" not in node or set(node) - {"use", "with"}:
                raise ModuleIRError(f"{path}: expected use with optional with, or ref")
            name(node["use"], "module implementation alias")
            arguments = node.get("with", {})
            if not isinstance(arguments, dict):
                raise ModuleIRError(f"{path}: with must map ports to expressions")
            normalized = {"use": node["use"]}
            if "with" in node:
                normalized["with"] = {}
                for slot, value in sorted(arguments.items()):
                    name(slot, "module port")
                    normalized["with"][slot] = {"ref": visit(value, f"{path}.with.{slot}", depth + 1)}
            nodes[path] = normalized
            return path
        finally:
            active.remove(id(node))

    root = visit(root_expression, "$", 0)
    unused = set(definitions) - set(names)
    if unused:
        raise ModuleIRError(f"unused module bindings: {sorted(unused)}")
    dependencies = {key: {child["ref"] for child in node.get("with", {}).values()} for key, node in nodes.items()}
    order = dependency_order(dependencies)
    depths = {}
    for key in order:
        depths[key] = 1 + max((depths[child] for child in dependencies[key]), default=0)
        if depths[key] > max_depth:
            raise ModuleIRError(f"module expression exceeds depth {max_depth}")
    return ModuleGraph(nodes, root, order, deepcopy(expression))


def satisfy_instance_sharing(expression, cards):
    graph = normalize_expression(expression)
    if not any(cards[node["use"]].get("instance_sharing") for node in graph.nodes.values()):
        return graph.expression({})
    parents = {}

    def representative(key):
        while key in parents:
            key = parents[key]
        return key

    def dependency(key, path):
        key = representative(key)
        slot, separator, role = path.partition(".")
        node = graph.nodes[key]
        if slot not in node.get("with", {}):
            raise ModuleIRError(f"missing instance dependency: {slot}")
        child = representative(node["with"][slot]["ref"])
        if not separator:
            return child, None
        provider = cards[graph.nodes[child]["use"]]
        exported = provider.get("instance_exports", {}).get(role)
        if exported is None:
            raise ModuleIRError(f"missing instance role: {path}")
        if isinstance(exported, dict):
            return child, exported["local"]
        return dependency(child, exported)

    def merge(left, right):
        left, right = representative(left), representative(right)
        if left == right:
            return
        a, b = graph.nodes[left], graph.nodes[right]
        if a["use"] != b["use"] or set(a.get("with", {})) != set(b.get("with", {})):
            raise ModuleIRError("required shared instance has incompatible constructor selections")
        first, second = sorted((left, right))
        parents[second] = first
        for slot in sorted(a.get("with", {})):
            merge(a["with"][slot]["ref"], b["with"][slot]["ref"])

    for key in graph.order:
        for left, right in cards[graph.nodes[key]["use"]].get("instance_sharing", []):
            a, role_a = dependency(key, left)
            b, role_b = dependency(key, right)
            if role_a != role_b:
                raise ModuleIRError("required instance roles have distinct local identities")
            merge(a, b)
    root = representative(graph.root)
    reachable = set()

    def visit(key):
        key = representative(key)
        if key in reachable:
            return
        reachable.add(key)
        for child in graph.nodes[key].get("with", {}).values():
            visit(child["ref"])

    visit(root)
    names = {key: f"m{index}" for index, key in enumerate(sorted(reachable))}
    definitions = {}
    for key in sorted(reachable):
        node = graph.nodes[key]
        result = {"use": node["use"]}
        if "with" in node:
            result["with"] = {slot: {"ref": names[representative(child["ref"])]} for slot, child in sorted(node["with"].items())}
        definitions[names[key]] = result
    result = {"let": definitions, "in": {"ref": names[root]}}
    normalize_expression(result)
    return result


def compile_unit(*, ports, nodes, exports, signature, equations=None, bodies=None, identities=None):
    ports, nodes, exports = deepcopy(ports), deepcopy(nodes), deepcopy(exports)
    bodies = deepcopy(bodies or {})
    identities = dict(identities or {})
    if any(not isinstance(name, str) or not isinstance(value, str) or not value for name, value in identities.items()):
        raise ModuleIRError("module implementation identities must be nonempty strings")
    if set(ports) & set(nodes):
        raise ModuleIRError("module ports and bindings must have distinct names")
    dependencies = {}
    for name, node in nodes.items():
        if not isinstance(name, str) or not name:
            raise ModuleIRError("module bindings require nonempty names")
        if not isinstance(node, dict) or len(set(node) & {"use", "body"}) != 1:
            raise ModuleIRError(f"module binding must select an implementation or checked body: {name}")
        if set(node) - {"use", "body", "with"}:
            raise ModuleIRError(f"unknown module binding fields: {name}")
        if "body" in node and node["body"] not in bodies:
            raise ModuleIRError(f"missing checked body: {name}")
        arguments = node.get("with", {})
        if not isinstance(arguments, dict):
            raise ModuleIRError(f"module binding arguments must be a mapping: {name}")
        targets = set()
        for slot, target in arguments.items():
            if not isinstance(slot, str) or not slot.isidentifier():
                raise ModuleIRError(f"invalid module dependency slot: {slot}")
            if not isinstance(target, dict) or set(target) != {"ref"}:
                raise ModuleIRError(f"module dependency must reference a binding: {name}.{slot}")
            if not isinstance(target["ref"], str) or target["ref"] not in nodes.keys() | ports.keys():
                raise ModuleIRError(f"unknown dependency binding: {name}.{slot}")
            if target["ref"] in nodes:
                targets.add(target["ref"])
        dependencies[name] = targets
    for name, target in exports.items():
        if not isinstance(target, dict) or set(target) - {"ref", "export"} or "ref" not in target:
            raise ModuleIRError(f"invalid module export projection: {name}")
        if target["ref"] not in nodes.keys() | ports.keys():
            raise ModuleIRError(f"unknown module export binding: {name}")
        if "export" in target and (not isinstance(target["export"], str) or not target["export"].isidentifier()):
            raise ModuleIRError(f"invalid projected operation: {name}")
    return {
        "format": "module-unit-1",
        "ports": ports,
        "nodes": nodes,
        "exports": exports,
        "signature": deepcopy(signature),
        "equations": deepcopy(equations or {}),
        "bodies": bodies,
        "identities": identities,
        "order": list(dependency_order(dependencies)),
    }


def lower_graph(document, cards):
    return compile_unit(
        ports=document["ports"],
        nodes={name: {"use": name, "with": {
            slot: {"ref": document["links"][name + "." + slot]} for slot in card.get("requires", {})
        }} for name, card in cards.items()},
        exports={name: {"ref": path.split(".")[0], "export": path.split(".")[1]} for name, path in document["exports"].items()},
        signature=document["module"]["provides"],
        identities={alias: constructor_identity(card) for alias, card in cards.items()},
        equations={
            "associated": document.get("associated", {}),
            "constraints": document.get("constraints", {}),
            "views": document.get("views", {}),
            "instance_exports": document.get("instance_exports", {}),
        },
    )


def execute_unit(unit, implementations, ports, *, type_scope=None):
    checked = compile_unit(
        ports=unit["ports"], nodes=unit["nodes"], exports=unit["exports"],
        signature=unit["signature"], equations=unit["equations"], bodies=unit["bodies"], identities=unit["identities"],
    )
    if checked != unit:
        raise ModuleIRError("compiled module unit is not canonical")
    if set(ports) != set(unit["ports"]):
        raise ModuleIRError("module unit dependencies differ from its public ports")
    required = {node.get("use", node.get("body")) for node in unit["nodes"].values()}
    if not required <= implementations.keys():
        raise ModuleIRError("compiled module unit has missing implementations")
    scope = list(type_scope) if type_scope is not None else unit_scope(unit)
    values = dict(ports)
    for name in unit["order"]:
        node = unit["nodes"][name]
        arguments = {slot: values[target["ref"]] for slot, target in node.get("with", {}).items()}
        implementation = implementations[node.get("use", node.get("body"))]
        instantiate = getattr(implementation, "__mf_instantiate__", None)
        child_scope = scope if "body" in node else [*scope, name]
        values[name] = instantiate(arguments, child_scope) if instantiate is not None else implementation(**arguments)
    exports = {}
    for name, target in unit["exports"].items():
        value = values[target["ref"]]
        exports[name] = value[target["export"]] if "export" in target else value
    return values, exports


def unit_type_scope(ports, nodes, identities=None, bodies=None):
    encoded = json.dumps({"ports": ports, "nodes": nodes, "identities": identities or {}, "bodies": bodies or {}}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    return ["unit:" + hashlib.sha256(encoded).hexdigest()]


def constructor_identity(card):
    return f"{card['family']}/{card['id']}@{card['version']}#{card['sha256']}"


def scoped_factory(factory):
    import inspect
    from functools import wraps

    signature = inspect.signature(factory)
    parameters = list(signature.parameters.values())
    if not parameters or parameters[0].kind not in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}:
        raise ModuleIRError("a scoped factory must accept its type scope first")
    public = signature.replace(parameters=parameters[1:])

    @wraps(factory)
    def invoke(**bindings):
        public.bind(**bindings)
        return factory(None, **bindings)

    def instantiate(bindings, scope):
        public.bind(**bindings)
        return factory(scope, **bindings)

    invoke.__signature__ = public
    invoke.__mf_instantiate__ = instantiate
    return invoke


def unit_scope(unit):
    return unit_type_scope(unit["ports"], unit["nodes"], unit["identities"], unit["bodies"])
