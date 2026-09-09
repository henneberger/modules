"""Evaluate concrete, checked module expressions over already loaded exports."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .associated import resolve_metadata
from .contracts import Functor, ModuleView, Requirement, Signature
from .indices import resolve_indices
from .module_ir import execute_unit, normalize_expression
from .planning import plan, select


class CompositionError(ValueError):
    """A concrete expression lacks an explicit, supported runtime interpretation."""


@dataclass(frozen=True)
class _Prepared:
    signature: Signature
    requirement: Requirement
    identity: str
    factory: Functor | None
    raw_exports: Mapping[str, Any] | None



def link_expression(
    expression: dict,
    exports: Mapping[str, Any],
    candidates: dict[str, dict],
    signatures: Mapping[tuple[str, str], Signature],
    *,
    types: Mapping[str, type] | None = None,
) -> ModuleView:
    graph = normalize_expression(expression)
    if any("hole" in node for node in graph.nodes.values()):
        raise CompositionError("select and fill every module hole before execution")
    if not isinstance(exports, Mapping):
        raise CompositionError(
            "exports must map verified candidate aliases to loaded values"
        )
    if not isinstance(signatures, Mapping):
        raise CompositionError(
            "signatures must map (id, version) pairs to runtime Signatures"
        )
    if types is not None and not isinstance(types, Mapping):
        raise CompositionError(
            "types must map shared export names to Python type objects"
        )
    fixed_types = dict(types or {})
    for name, value in fixed_types.items():
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or not isinstance(value, type)
        ):
            raise CompositionError(
                "types must map export identifiers to Python type objects"
            )

    # Snapshot ordinary caller-owned metadata and alias maps before executing any
    # selected code. The planner validates the cards as finite JSON declarations.
    cards = deepcopy(candidates)
    selected = select(plan(expression, cards))
    loaded = dict(exports)
    runtime_signatures = dict(signatures)

    def lookup(reference: dict[str, str]) -> Signature:
        key = (reference["id"], reference["version"])
        signature = runtime_signatures.get(key)
        if not isinstance(signature, Signature):
            raise CompositionError(f"missing runtime Signature for {key[0]}@{key[1]}")
        if signature.reference() != reference:
            raise CompositionError(
                f"runtime Signature stored under {key!r} declares another contract"
            )
        return signature

    def requirement(signature: Signature) -> Requirement:
        return Requirement(
            signature,
            types={
                name: fixed_types[name]
                for name in signature.types
                if name in fixed_types
            },
        )

    prepared = {}
    for selection in selected["selected"]:
        alias = selection["alias"]
        if alias not in loaded:
            raise CompositionError(
                f"selected candidate {alias!r} has no verified loaded export"
            )
        card = cards[alias]
        signature = lookup(card["provides"])
        result_requirement = requirement(signature)
        identity = f"{card['family']}/{card['id']}@{card['version']}#{card['sha256']}"
        requirements = card.get("requires", {})
        open_factory = (
            bool(requirements)
            or card.get("kind") == "functor"
            or card.get("contract_target")
            in {
                "named-module-factory",
                "module-returned-by-named-module-factory",
            }
        )
        closed_factory = card.get("kind") == "module-factory" or card.get(
            "contract_target"
        ) in {
            "zero-argument-module-factory",
            "module-returned-by-zero-argument-factory",
        }
        factory = None
        raw_exports = None
        if open_factory or closed_factory:
            parameters = {
                slot: requirement(lookup(reference))
                for slot, reference in requirements.items()
            }
            factory = Functor(
                identity,
                parameters,
                signature,
                loaded[alias],
                sharing=card.get("sharing", ()),
                associated=card.get("associated", {}),
                instance_sharing=card.get("instance_sharing", ()),
                instance_exports=card.get("instance_exports", {}),
            )
        elif card.get("kind") == "module":
            if not isinstance(loaded[alias], Mapping):
                raise CompositionError(f"{alias!r} must export a mapping for its grouped module")
            raw_exports = loaded[alias]
        elif len(signature.types) == 1 and not signature.callables:
            raw_exports = {signature.types[0]: loaded[alias]}
        elif len(signature.callables) == 1 and not signature.types:
            raw_exports = {next(iter(signature.callables)): loaded[alias]}
        else:
            raise CompositionError(
                f"{alias!r} must explicitly declare a module-factory: its signature requires "
                "multiple exports or both callable and type exports"
            )
        if raw_exports is not None:
            # Check even late-discovered raw providers before any factory runs.
            result_requirement.check(
                signature.seal(raw_exports, identity=identity), alias
            )
        prepared[alias] = _Prepared(
            signature, result_requirement, identity, factory, raw_exports
        )

    def evaluate(alias, arguments) -> ModuleView:
        implementation = prepared[alias]
        if implementation.factory is not None:
            module = implementation.factory(**arguments)
        else:
            module = implementation.signature.seal(
                implementation.raw_exports, identity=implementation.identity
            )
        implementation.requirement.check(module, alias)
        indices = resolve_indices(cards[alias], {slot: child.metadata()["indices"] for slot, child in arguments.items()})
        associated = resolve_metadata(cards[alias], {slot: child.metadata()["associated"] for slot, child in arguments.items()})
        return module.signature.seal(module, identity=module.identity, indices=indices, associated=associated)

    def invoker(alias):
        def invoke(**arguments):
            return evaluate(alias, arguments)
        return invoke

    invokers = {alias: invoker(alias) for alias in prepared}
    _, projected = execute_unit(selected["unit"], invokers, {})
    return projected["module"]


__all__ = ["CompositionError", "link_expression"]
