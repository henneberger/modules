"""Declared semantic identities; these are checked assertions, not proofs."""

from __future__ import annotations


def resolve_indices(card, children):
    """Resolve exported indices and check constraints on supplied modules."""

    def lookup(path):
        if not isinstance(path, str) or path.count(".") != 1:
            raise ValueError("index paths must be slot.Name")
        slot, name = path.split(".")
        value = children.get(slot, {}).get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing semantic index: {path}")
        return value

    for slot, expected in card.get("index_requires", {}).items():
        for name, value in expected.items():
            if lookup(f"{slot}.{name}") != value:
                raise ValueError(f"semantic index mismatch: {slot}.{name}")
    for left, right in card.get("index_sharing", []):
        if lookup(left) != lookup(right):
            raise ValueError(f"semantic index mismatch: {left} != {right}")
    result = {}
    for name, value in card.get("index_exports", {}).items():
        if isinstance(value, dict) and set(value) == {"from"}:
            value = lookup(value["from"])
        if not isinstance(value, str) or not value:
            raise ValueError(f"invalid semantic index: {name}")
        result[name] = value
    return result


def validate_indices(card):
    """Validate the finite index language without requiring actual providers."""
    requirements = card.get("requires", {})
    if not isinstance(requirements, dict):
        raise ValueError("requires must map named slots to contracts")
    children = {slot: {} for slot in requirements}

    def path(value):
        if not isinstance(value, str) or value.count(".") != 1:
            raise ValueError("index paths must be slot.Name")
        slot, name = value.split(".")
        if slot not in children or not name.isidentifier():
            raise ValueError(f"index path outside required ports: {value}")
        children[slot][name] = "validation"

    for field in ("index_exports", "index_requires"):
        if not isinstance(card.get(field, {}), dict):
            raise ValueError(f"{field} must be a table")
    for name, value in card.get("index_exports", {}).items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("semantic index names must be identifiers")
        if isinstance(value, dict) and set(value) == {"from"}:
            path(value["from"])
        elif not isinstance(value, str) or not value:
            raise ValueError(f"invalid semantic index: {name}")
    for slot, expected in card.get("index_requires", {}).items():
        if slot not in children:
            raise ValueError(f"index requirement outside required ports: {slot}")
        if not isinstance(expected, dict):
            raise ValueError("index_requires must map ports to index tables")
        for name, value in expected.items():
            path(f"{slot}.{name}")
            if not isinstance(value, str) or not value:
                raise ValueError(
                    "required semantic identities must be nonempty strings"
                )
    sharing = card.get("index_sharing", [])
    if not isinstance(sharing, list):
        raise ValueError("index_sharing must contain pairs of port indices")
    for pair in sharing:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("index_sharing must contain pairs of port indices")
        for value in pair:
            path(value)
