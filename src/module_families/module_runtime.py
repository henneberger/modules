"""Small runtime checks used by fixed, build-generated module wiring.

This module does not select providers, inspect repositories, or compile source.
"""

from __future__ import annotations

import hashlib

from .associated import resolve_metadata, validate_against_interface
from .contracts import Functor, Requirement
from .indices import resolve_indices
from .instance_terms import instance_at, resolve_instances
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

    declarations = {(s["id"], s["version"]): s for s in spec["interfaces"]}
    doc = spec["document"]
    if set(ports) != set(doc["ports"]):
        raise ModuleLinkError("unfilled or extra open module ports")
    for name, module in ports.items():
        Requirement(signature(doc["ports"][name]["requires"])).check(module, name)
        ref = doc["ports"][name]["requires"]
        validate_against_interface({"associated": module.metadata()["associated"]}, declarations[(ref["id"], ref["version"])])
    resolve_indices(
        spec, {name: module.metadata()["indices"] for name, module in ports.items()}
    )
    resolve_instances({"requires": doc["ports"], "instance_sharing": spec.get("instance_sharing", []), "instance_exports": spec.get("instance_exports", {})}, {name: module.metadata() for name, module in ports.items()})
    witnesses = {name: module.metadata()["associated"] for name, module in ports.items()}
    resolve_metadata({"requires": doc["ports"], "associated": spec.get("associated", {})}, witnesses)
    for alias in spec["order"]:
        card = spec["cards"][alias]
        witnesses[alias] = resolve_metadata(card, {
            slot: witnesses[doc["links"][alias + "." + slot]] for slot in card.get("requires", {})
        })
        ref = card["provides"]
        validate_against_interface({"associated": witnesses[alias]}, declarations[(ref["id"], ref["version"])])
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
                module, identity=module.identity, indices=indices, associated=module.metadata()["associated"]
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
                associated=card.get("associated", {}),
                instance_sharing=card.get("instance_sharing", ()),
                instance_exports=card.get("instance_exports", {}),
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
                associated=resolve_metadata(card, {}),
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
        instances={name: instance_at(path, {key: module.metadata() for key, module in nodes.items()}) for name, path in doc.get("instance_exports", {}).items()},
        associated=resolve_metadata(
            {"requires": doc["ports"], "associated": spec.get("associated", {})},
            {name: nodes[name].metadata()["associated"] for name in doc["ports"]},
        ),
        indices=resolve_indices(
            spec, {name: nodes[name].metadata()["indices"] for name in doc["ports"]}
        ),
    )
