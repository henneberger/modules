"""Small runtime checks used by fixed, build-generated module wiring.

This module does not select providers, inspect repositories, or compile source.
"""

from __future__ import annotations

import hashlib

from .contracts import Functor, Requirement
from .indices import resolve_indices
from .interfaces import signature_from_spec
from .registry import canonical_bytes


class ModuleLinkError(ValueError):
    """Prepared Python modules violate their declared linking obligations."""


def prepare_graph(spec, raw, ports):
    """Runtime preflight for generated wiring; performs no search or imports."""
    signatures = {
        (s["id"], s["version"]): signature_from_spec(s) for s in spec["interfaces"]
    }

    def signature(ref):
        return signatures[(ref["id"], ref["version"])]

    doc = spec["document"]
    if set(ports) != set(doc["ports"]):
        raise ModuleLinkError("unfilled or extra open module ports")
    for name, module in ports.items():
        Requirement(signature(doc["ports"][name]["requires"])).check(module, name)
    resolve_indices(
        spec, {name: module.metadata()["indices"] for name, module in ports.items()}
    )
    prepared = {}

    def factory_invoker(functor, card):
        def invoke(**arguments):
            indices = resolve_indices(
                card,
                {
                    slot: module.metadata()["indices"]
                    for slot, module in arguments.items()
                },
            )
            module = functor(**arguments)
            return module.signature.seal(
                module, identity=module.identity, indices=indices
            )

        return invoke

    def raw_invoker(module):
        def invoke(**arguments):
            if arguments:
                raise ModuleLinkError("raw module cannot receive ports")
            return module

        return invoke

    for alias, card in spec["cards"].items():
        result = signature(card["provides"])
        factory = (
            bool(card.get("requires"))
            or card.get("kind") in {"functor", "module-factory"}
            or card.get("contract_target")
            in {
                "named-module-factory",
                "module-returned-by-named-module-factory",
                "zero-argument-module-factory",
                "module-returned-by-zero-argument-factory",
            }
        )
        if factory:
            functor = Functor(
                alias,
                {
                    name: Requirement(signature(ref))
                    for name, ref in card.get("requires", {}).items()
                },
                result,
                raw[alias],
                sharing=card.get("sharing", ()),
            )

            invoke = factory_invoker(functor, card)
        else:
            if card.get("kind") == "module":
                exports = raw[alias]
            elif len(result.callables) + len(result.types) == 1:
                exports = {next(iter((*result.callables, *result.types))): raw[alias]}
            else:
                raise ModuleLinkError(
                    f"{alias} needs a grouped module or module factory"
                )
            module = result.seal(
                exports,
                identity=f"artifact:{card['sha256']}",
                indices=resolve_indices(card, {}),
            )

            invoke = raw_invoker(module)
        prepared[alias] = invoke
    return prepared


def finish_graph(spec, nodes):
    """Check final nominal sharing and project the explicitly selected exports."""
    doc = spec["document"]
    for left, right in doc["constraints"].get("same_type", []):
        a, x = left.split(".")
        b, y = right.split(".")
        if nodes[a][x] is not nodes[b][y]:
            raise ModuleLinkError(f"nominal type mismatch: {left} != {right}")
    for left, right in doc["constraints"].get("same_index", []):
        a, x = left.split(".")
        b, y = right.split(".")
        if nodes[a].metadata()["indices"].get(x) != nodes[b].metadata()["indices"].get(
            y
        ):
            raise ModuleLinkError(f"semantic index mismatch: {left} != {right}")
    ref = doc["module"]["provides"]
    result = next(
        signature_from_spec(s)
        for s in spec["interfaces"]
        if (s["id"], s["version"]) == (ref["id"], ref["version"])
    )
    return result.seal(
        {
            name: nodes[path.split(".")[0]][path.split(".")[1]]
            for name, path in doc["exports"].items()
        },
        identity="graph:" + hashlib.sha256(canonical_bytes(spec)).hexdigest(),
        indices=resolve_indices(
            spec, {name: nodes[name].metadata()["indices"] for name in doc["ports"]}
        ),
    )
