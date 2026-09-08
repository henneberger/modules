"""Evaluate concrete, checked module expressions over already loaded exports."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .contracts import Functor, ModuleView, Requirement, Signature
from .indices import resolve_indices
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


def _reject_holes(expression: Any, active: set[int] | None = None) -> None:
    if not isinstance(expression, dict):
        return  # The planner supplies the structural diagnostic.
    if "hole" in expression:
        raise CompositionError(
            "link_expression requires a concrete tree; select and fill every hole first"
        )
    active = set() if active is None else active
    if id(expression) in active:
        raise CompositionError(
            "recursive expression objects are unsupported; supply a finite JSON tree"
        )
    active.add(id(expression))
    arguments = expression.get("with")
    if isinstance(arguments, dict):
        for child in arguments.values():
            _reject_holes(child, active)
    active.remove(id(expression))


def link_expression(
    expression: dict,
    exports: Mapping[str, Any],
    candidates: dict[str, dict],
    signatures: Mapping[tuple[str, str], Signature],
    *,
    types: Mapping[str, type] | None = None,
) -> ModuleView:
    """Interpret ``{use: alias, with: {slot: expression}}`` after metadata preflight.

    `exports` contains values already loaded through a caller-verified import
    bundle. Candidate artifact references must correspond to those verified
    locks: this interpreter neither imports code nor verifies that association.
    `signatures` maps exact (contract ID, version) pairs to runtime signatures.
    `types` optionally fixes nominal identities from a shared type library.

    All selected representations, exports, signature references, and factory
    call shapes are checked before invoking a factory. Concrete metadata is
    checked by plan() and select(); holes are rejected even if uniquely solvable.
    Runtime sharing and result conformance are checked as applications evaluate.

    Each factory occurrence creates a fresh instance; there is no let-binding,
    result memoization, or recursive expression support. Raw class/function
    values remain the supplied objects. Behavioral obligations are not proved,
    and failures do not roll back Python effects or previously invoked factories.
    """
    _reject_holes(expression)
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
    concrete = selected["expression"]
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

    def evaluate(node: dict) -> ModuleView:
        alias = node["use"]
        implementation = prepared[alias]
        arguments = {}
        if implementation.factory is not None:
            arguments = {
                slot: evaluate(child)
                for slot, child in sorted(node.get("with", {}).items())
            }
            module = implementation.factory(**arguments)
        else:
            module = implementation.signature.seal(
                implementation.raw_exports, identity=implementation.identity
            )
        implementation.requirement.check(module, alias)
        indices = resolve_indices(cards[alias], {slot: child.metadata()["indices"] for slot, child in arguments.items()})
        return module.signature.seal(module, identity=module.identity, indices=indices)

    return evaluate(concrete)


__all__ = ["CompositionError", "link_expression"]
