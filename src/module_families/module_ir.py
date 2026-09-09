from __future__ import annotations

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


def compile_unit(*, ports, nodes, exports, signature, equations=None, bodies=None):
    ports, nodes, exports = deepcopy(ports), deepcopy(nodes), deepcopy(exports)
    bodies = deepcopy(bodies or {})
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
        equations={
            "associated": document.get("associated", {}),
            "constraints": document.get("constraints", {}),
            "instance_exports": document.get("instance_exports", {}),
        },
    )


def execute_unit(unit, implementations, ports):
    checked = compile_unit(
        ports=unit["ports"], nodes=unit["nodes"], exports=unit["exports"],
        signature=unit["signature"], equations=unit["equations"], bodies=unit["bodies"],
    )
    if checked != unit:
        raise ModuleIRError("compiled module unit is not canonical")
    if set(ports) != set(unit["ports"]):
        raise ModuleIRError("module unit dependencies differ from its public ports")
    required = {node.get("use", node.get("body")) for node in unit["nodes"].values()}
    if not required <= implementations.keys():
        raise ModuleIRError("compiled module unit has missing implementations")
    values = dict(ports)
    for name in unit["order"]:
        node = unit["nodes"][name]
        arguments = {slot: values[target["ref"]] for slot, target in node.get("with", {}).items()}
        values[name] = implementations[node.get("use", node.get("body"))](**arguments)
    exports = {}
    for name, target in unit["exports"].items():
        value = values[target["ref"]]
        exports[name] = value[target["export"]] if "export" in target else value
    return values, exports
