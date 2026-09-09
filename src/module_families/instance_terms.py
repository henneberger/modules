from __future__ import annotations


def validate_instances(card):
    requirements = card.get("requires", {})

    def path(value):
        if not isinstance(value, str):
            raise ValueError("instance paths must be strings")
        parts = value.split(".")
        if len(parts) not in {1, 2} or any(not part.isidentifier() for part in parts):
            raise ValueError(f"invalid instance path: {value}")
        if parts[0] not in requirements:
            raise ValueError(f"instance path is outside declared dependencies: {value}")

    sharing = card.get("instance_sharing", [])
    if not isinstance(sharing, (list, tuple)):
        raise ValueError("instance_sharing must contain pairs of dependency paths")
    for pair in sharing:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("instance_sharing requires pairs of dependency paths")
        for value in pair:
            path(value)
    exports = card.get("instance_exports", {})
    if not isinstance(exports, dict):
        raise ValueError("instance_exports must map role names to dependency paths")
    for name, value in exports.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"invalid instance role: {name}")
        path(value)


def instance_at(path, dependencies):
    owner, separator, role = path.partition(".")
    if owner not in dependencies:
        raise ValueError(f"missing instance dependency: {owner}")
    module = dependencies[owner]
    if not separator:
        return module["instance_id"]
    if role not in module.get("instances", {}):
        raise ValueError(f"missing instance role: {path}")
    return module["instances"][role]


def resolve_instances(card, dependencies):
    validate_instances(card)
    for left, right in card.get("instance_sharing", []):
        a, b = instance_at(left, dependencies), instance_at(right, dependencies)
        if a != b:
            raise ValueError(f"instance sharing conflict: {left} != {right}")
    return {name: instance_at(path, dependencies) for name, path in card.get("instance_exports", {}).items()}


def graph_instances(doc, cards, order):
    parents = {}
    residual = []
    values = {name: {"instance_id": "port:" + name, "instances": {}} for name in doc["ports"]}

    def canonical(value):
        while value in parents:
            value = parents[value]
        return value

    def equal(left, right):
        left, right = canonical(left), canonical(right)
        if left == right:
            return
        if not left.startswith("port:") or not right.startswith("port:"):
            raise ValueError(f"distinct module instances cannot be unified: {left}, {right}")
        a, b = sorted((left, right))
        parents[b] = a
        residual.append([a.removeprefix("port:"), b.removeprefix("port:")])

    def lookup(path):
        owner, separator, role = path.partition(".")
        if owner not in values:
            raise ValueError(f"unknown instance owner: {owner}")
        if separator and owner in doc["ports"]:
            values[owner]["instances"].setdefault(role, "port:" + path)
        return canonical(instance_at(path, values))

    for alias in order:
        card = cards[alias]
        validate_instances(card)

        def dependency(path, alias=alias):
            slot, separator, role = path.partition(".")
            target = doc["links"][alias + "." + slot]
            return target + ("." + role if separator else "")

        for left, right in card.get("instance_sharing", []):
            equal(lookup(dependency(left)), lookup(dependency(right)))
        values[alias] = {
            "instance_id": "node:" + alias,
            "instances": {name: lookup(dependency(path)) for name, path in card.get("instance_exports", {}).items()},
        }
    for left, right in doc["constraints"].get("same_instance", []):
        if left not in doc["links"] or right not in doc["links"]:
            raise ValueError(f"instance sharing requires declared dependency slots: {left}, {right}")
        equal(lookup(doc["links"][left]), lookup(doc["links"][right]))
    exports = {}
    for name, path in doc.get("instance_exports", {}).items():
        value = lookup(path)
        if not value.startswith("port:"):
            raise ValueError(f"instance export {name} must preserve a public dependency identity")
        exports[name] = value.removeprefix("port:")
    return {"instance_sharing": residual, "instance_exports": exports}


def validate_instance_interface(card, interface, dependencies=None):
    validate_instances(card)
    expected = set(interface.get("instances", []))
    actual = set(card.get("instance_exports", {}))
    if actual != expected:
        raise ValueError(f"public instance roles differ from signature: expected {sorted(expected)}, got {sorted(actual)}")
    if dependencies is None:
        return
    paths = list(card.get("instance_exports", {}).values())
    paths.extend(path for pair in card.get("instance_sharing", []) for path in pair)
    for path in paths:
        slot, separator, role = path.partition(".")
        if separator and role not in dependencies[slot].get("instances", []):
            raise ValueError(f"instance role is not exposed by dependency signature: {path}")
