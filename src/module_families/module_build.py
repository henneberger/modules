"""Compile acyclic open module graphs to independently publishable Python wheels.

The generated factory contains fixed calls and imports. Selection happens here,
never in the deployed program. A published open graph is an ordinary constructor
and can itself be selected as a node or closed by goal-directed synthesis.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import itertools
import json
import keyword
import tomllib
from pathlib import Path

from . import __version__
from .assemblies import _domain, read_assembly
from .associated_graph import graph_associated
from .indices import validate_indices
from .interfaces import (
    signature_from_spec,
    validate_interface,
    validate_interface_reference,
)
from .planning import _cards
from .registry import _validate_index, canonical_bytes
from .wheels import build_wheel, normalize_distribution

FORMAT = "module-families-port-graph-1"


class ModuleBuildError(ValueError):
    """A module graph is invalid, ambiguous, or not safely linkable."""


def _name(value):
    if (
        not isinstance(value, str)
        or not value.isidentifier()
        or keyword.iskeyword(value)
    ):
        raise ModuleBuildError(f"expected a Python identifier: {value!r}")
    return value


def _shape(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ModuleBuildError(f"invalid {label}; allowed fields: {sorted(allowed)}")
    return value


def _path(value, names):
    if not isinstance(value, str) or value.count(".") != 1:
        raise ModuleBuildError(f"expected node.export or port.export: {value!r}")
    owner, export = value.split(".")
    if owner not in names:
        raise ModuleBuildError(f"unknown module: {owner}")
    _name(export)
    return owner, export


def read_module(source):
    if isinstance(source, dict):
        doc = copy.deepcopy(source)
    else:
        path = Path(source)
        if path.suffix != ".toml":
            raise ModuleBuildError("module build authoring must be TOML")
        doc = tomllib.loads(path.read_text())
    canonical_bytes(doc)
    _shape(
        doc,
        {
            "schema_version",
            "family",
            "publisher",
            "module",
            "ports",
            "nodes",
            "links",
            "views",
            "exports",
            "indices",
            "associated",
            "instance_exports",
            "constraints",
            "policy",
        },
        "module document",
    )
    if type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise ModuleBuildError("module schema_version must be 1")
    _shape(doc.get("family"), {"name", "version", "description", "context"}, "family")
    _shape(doc.get("publisher"), {"name"}, "publisher")
    header = _shape(
        doc.get("module"),
        {"id", "version", "provides", "summary", "capabilities"},
        "module header",
    )
    _name(header.get("id"))
    validate_interface_reference(header.get("provides"))
    for owner, fields in (
        (doc["family"], ("name", "version", "description")),
        (doc["publisher"], ("name",)),
        (header, ("version",)),
    ):
        for field in fields:
            if not isinstance(owner.get(field), str) or not owner[field].strip():
                raise ModuleBuildError(f"missing nonempty {field}")
    for key in (
        "ports",
        "nodes",
        "links",
        "exports",
        "views",
        "indices",
        "associated",
        "instance_exports",
        "constraints",
        "policy",
    ):
        doc.setdefault(key, {})
        if not isinstance(doc[key], dict):
            raise ModuleBuildError(f"{key} must be a table")
    for name, path in doc["instance_exports"].items():
        _name(name)
        if not isinstance(path, str) or len(path.split(".")) not in {1, 2}:
            raise ModuleBuildError("instance exports require node or port paths")
        for part in path.split("."):
            _name(part)
    _shape(doc["associated"], {"types", "constructors"}, "associated graph types")
    if not isinstance(doc["associated"].get("types", {}), dict):
        raise ModuleBuildError("associated.types must be a table")
    if not doc["nodes"] or not doc["exports"]:
        raise ModuleBuildError("a module needs nodes and explicit exports")
    names = set(doc["ports"]) | set(doc["nodes"])
    if set(doc["ports"]) & set(doc["nodes"]):
        raise ModuleBuildError("ports and nodes must have distinct names")
    for name in names:
        _name(name)
        if name.startswith("_"):
            raise ModuleBuildError(
                "module node and port names must not start with an underscore"
            )
    for port in doc["ports"].values():
        _shape(port, {"requires"}, "port")
        validate_interface_reference(port.get("requires"))
    for name, node in doc["nodes"].items():
        _shape(node, {"select"}, "node")
        # Reuse the exact repository selector grammar and version validation.
        read_assembly(
            {
                "schema_version": 1,
                "assembly": {"name": name},
                "bindings": {name: node.get("select")},
                "expression": {"use": name},
            }
        )
    if set(doc["views"]) - set(doc["links"]):
        raise ModuleBuildError("signature views must name declared dependency links")
    for target, provider in doc["links"].items():
        _path(target, doc["nodes"])
        if not isinstance(provider, str) or provider not in names:
            raise ModuleBuildError(f"unknown link provider: {provider!r}")
    for key in ("exports", "indices"):
        for export, path in doc[key].items():
            _name(export)
            _path(path, names)
    _shape(
        doc["constraints"], {"same_type", "same_index", "same_instance", "same_associated"}, "constraints"
    )
    for kind, pairs in doc["constraints"].items():
        if not isinstance(pairs, list):
            raise ModuleBuildError(f"{kind} requires pairs")
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ModuleBuildError(f"{kind} requires pairs")
            for path in pair:
                _path(path, doc["nodes"] if kind == "same_instance" else names)
    _shape(doc["policy"], {"allowed_effects"}, "policy")
    for values in (
        header.get("capabilities", []),
        doc["policy"].get("allowed_effects", []),
    ):
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v for v in values
        ):
            raise ModuleBuildError(
                "capabilities and effects must be lists of nonempty strings"
            )
    return doc


def _symbolic_indices(doc, cards, order):
    """Propagate concrete identities and open-port equalities without imports."""
    values, equations, required = {}, [], set()

    def term(path):
        owner, name = path.split(".")
        if owner in doc["ports"]:
            value = (owner, name)
            required.add(value)
            return value
        if name not in values.get(owner, {}):
            raise ModuleBuildError(f"missing semantic index: {path}")
        return values[owner][name]

    for alias in order:
        card = cards[alias]
        validate_indices(card)

        def child(path, alias=alias):
            slot, name = path.split(".")
            return term(doc["links"][f"{alias}.{slot}"] + "." + name)

        for slot, expected in card.get("index_requires", {}).items():
            for name, value in expected.items():
                equations.append((child(f"{slot}.{name}"), value))
        for left, right in card.get("index_sharing", []):
            equations.append((child(left), child(right)))
        values[alias] = {
            name: child(value["from"]) if isinstance(value, dict) else value
            for name, value in card.get("index_exports", {}).items()
        }
    for left, right in doc["constraints"].get("same_index", []):
        equations.append((term(left), term(right)))
    exported = {name: term(path) for name, path in doc["indices"].items()}
    parent = {}

    def find(value):
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    for left, right in equations:
        a, b = find(left), find(right)
        if isinstance(a, str) and isinstance(b, str) and a != b:
            raise ModuleBuildError(f"semantic index mismatch: {a!r} != {b!r}")
        if isinstance(a, str):
            parent[b] = a
        else:
            parent[a] = b
    expected, sharing = {}, []
    groups = {}
    for value in sorted(required):
        root = find(value)
        path = ".".join(value)
        if isinstance(root, str):
            expected.setdefault(value[0], {})[value[1]] = root
        else:
            groups.setdefault(root, []).append(path)
    for paths in groups.values():
        # A self equality also requires that an exported open identity exists.
        sharing.extend([[paths[0], path] for path in paths])
    exports = {}
    for name, value in exported.items():
        root = find(value)
        exports[name] = root if isinstance(root, str) else {"from": ".".join(root)}
    return {
        "index_requires": expected,
        "index_sharing": sharing,
        "index_exports": exports,
    }


def _check_graph(doc, cards, repository):
    _cards(cards)
    references = {alias: card["provides"] for alias, card in cards.items()}
    references.update({alias: port["requires"] for alias, port in doc["ports"].items()})
    specs = {}
    for ref in [
        doc["module"]["provides"],
        *references.values(),
        *(ref for card in cards.values() for ref in card.get("requires", {}).values()),
    ]:
        key = (ref["id"], ref["version"])
        specs[key] = validate_interface(repository.interface(*key))
    signatures = {
        alias: specs[(ref["id"], ref["version"])] for alias, ref in references.items()
    }
    expected_links = {
        f"{alias}.{slot}"
        for alias, card in cards.items()
        for slot in card.get("requires", {})
    }
    if set(doc["links"]) != expected_links:
        raise ModuleBuildError(
            f"unfilled or extra ports: missing={sorted(expected_links - set(doc['links']))}, extra={sorted(set(doc['links']) - expected_links)}"
        )
    for target, provider in doc["links"].items():
        alias, slot = target.split(".")
        expected = cards[alias]["requires"][slot]
        if target in doc["views"]:
            from .refinement import refine_signature

            refine_signature(signatures[provider], specs[(expected["id"], expected["version"])], doc["views"][target])
        elif expected != references[provider]:
            raise ModuleBuildError(f"contract mismatch: {target} <- {provider}; declare a signature view")
    from .module_ir import ModuleIRError, lower_graph

    try:
        unit = lower_graph(doc, cards)
    except ModuleIRError as error:
        raise ModuleBuildError(str(error)) from error
    order = unit["order"]
    from .instance_terms import graph_instances

    try:
        instances = graph_instances(doc, cards, order)
    except ValueError as error:
        raise ModuleBuildError(str(error)) from error
    sharing = []
    for left, right in doc["constraints"].get("same_type", []):
        identities = []
        for path in (left, right):
            owner, name = path.split(".")
            if name not in signatures[owner]["types"]:
                raise ModuleBuildError(f"not a declared type: {path}")
            identities.append(cards.get(owner, {}).get("type_exports", {}).get(name))
        if all(identities) and identities[0] != identities[1]:
            raise ModuleBuildError(f"nominal type mismatch: {left}, {right}")
        if all(path.split(".")[0] in doc["ports"] for path in (left, right)):
            sharing.append([left, right])
    ref = doc["module"]["provides"]
    result = specs[(ref["id"], ref["version"])]
    if set(doc["exports"]) != set(result["callables"]) | set(result["types"]):
        raise ModuleBuildError(
            "exports must exactly implement the public signature; rename or project explicitly"
        )

    for name, path in doc["exports"].items():
        owner, export = path.split(".")
        if name in result["types"]:
            if export not in signatures[owner]["types"]:
                raise ModuleBuildError(f"missing type export: {path}")
        else:
            from .contracts import _check_call_acceptance

            source_call = signature_from_spec(signatures[owner]).callables.get(export)
            target_call = signature_from_spec(result).callables[name]
            if source_call is None or source_call.asynchronous != target_call.asynchronous:
                raise ModuleBuildError(f"export operation kind mismatch: {name} <- {path}")
            _check_call_acceptance(target_call.signature, source_call.signature, name)
    from .instance_terms import validate_instance_interface

    for alias, card in cards.items():
        validate_instance_interface(card, signatures[alias], {
            slot: specs[(ref["id"], ref["version"])] for slot, ref in card.get("requires", {}).items()
        })
    validate_instance_interface({"requires": doc["ports"], **instances}, result, {
        name: signatures[name] for name in doc["ports"]
    })
    from .mixins import validate_mixin_interfaces

    for card in cards.values():
        validate_mixin_interfaces(card, specs)
    associated = graph_associated(doc, cards, order, specs)
    # Every selected node/port must contribute to the public dependency graph.
    used = {path.split(".")[0] for path in doc["exports"].values()}
    used.update(path.split(".")[0] for path in doc["indices"].values())
    used.update(path.split(".")[0] for path in doc["instance_exports"].values())
    def associated_dependencies(term):
        if "from" in term:
            used.add(term["from"].split(".")[0])
        for argument in term.get("args", []):
            associated_dependencies(argument)
    for term in doc["associated"].get("types", {}).values():
        associated_dependencies(term)
    for pair in doc["constraints"].get("same_associated", []):
        used.update(path.split(".")[0] for path in pair)
    for alias in reversed(order):
        if alias in used:
            used.update(
                doc["links"][f"{alias}.{slot}"]
                for slot in cards[alias].get("requires", {})
            )
    if used != set(references):
        raise ModuleBuildError(
            f"unused nodes or ports: {sorted(set(references) - used)}"
        )
    effects = set()
    for card in cards.values():
        declared = set(card.get("effects", ["unknown"]))
        if card.get("requires") or card.get("kind") == "functor":
            declared.discard("dependency-effects")
        effects.update(declared)
    allowed = doc["policy"].get("allowed_effects")
    if allowed is not None and (
        effects & {"unknown", "dependency-effects"} or not effects <= set(allowed)
    ):
        raise ModuleBuildError(
            f"effect policy rejects selected implementation effects: {sorted(effects)}"
        )
    if doc["ports"]:
        effects.add("dependency-effects")
    return {
        "order": order,
        "unit": unit,
        "interfaces": [specs[key] for key in sorted(specs)],
        "effects": sorted(effects),
        "sharing": sharing,
        **_symbolic_indices(doc, cards, order),
        **associated,
        **instances,
    }


def resolve_module(
    source, repository, *, max_candidates=100, max_states=10000, max_solutions=16
):
    """Resolve a finite named graph, retaining open ports and explicit ambiguity."""
    if any(
        type(n) is not int or n < 1 for n in (max_candidates, max_states, max_solutions)
    ):
        raise ModuleBuildError("search bounds must be positive integers")
    doc = read_module(source)
    domains, limited = {}, False
    for alias, node in sorted(doc["nodes"].items()):
        domains[alias], truncated = _domain(repository, node["select"], max_candidates)
        limited |= truncated
    solutions, rejections, visited = [], [], 0
    for values in itertools.product(*domains.values()):
        if visited >= max_states or len(solutions) >= max_solutions:
            limited = True
            break
        visited += 1
        cards = dict(zip(domains, values, strict=True))
        try:
            checked = _check_graph(doc, cards, repository)
            solutions.append({"cards": cards, **checked})
        except (ValueError, KeyError, TypeError) as error:
            if len(rejections) < 100:
                rejections.append(str(error))
    for alias, domain in domains.items():
        if not domain:
            rejections.append(f"no candidates: {alias}")
    status = (
        "incomplete"
        if limited
        else "unsatisfied"
        if not solutions
        else "unique"
        if len(solutions) == 1
        else "ambiguous"
    )
    return {
        "format": FORMAT,
        "request": doc,
        "status": status,
        "complete": not limited,
        "visited_states": visited,
        "candidate_counts": {k: len(v) for k, v in domains.items()},
        "bounds": {
            "max_candidates": max_candidates,
            "max_states": max_states,
            "max_solutions": max_solutions,
        },
        "solutions": solutions,
        "rejections": rejections,
    }


def _generated_source(spec, import_module):
    lines = [
        f'"""Generated fixed module wiring: {import_module}."""',
        "from module_families.module_runtime import prepare_graph as _prepare, finish_graph as _finish",
        "from module_families.module_ir import execute_unit as _execute, scoped_factory as _scoped_factory",
    ]
    for i, (_alias, card) in enumerate(sorted(spec["cards"].items())):
        # Both paths originate in validated repository cards, not source text.
        lines.append(f"import {card['import_module']} as _artifact_{i}")
    lines.extend(
        [
            f"_SPEC = {spec!r}",
            "",
            "@_scoped_factory",
            "def create(_type_scope"
            + (
                ", *, " + ", ".join(sorted(spec["document"]["ports"]))
                if spec["document"]["ports"]
                else ""
            )
            + "):",
        ]
    )
    lines.append(
        "    _ports = {"
        + ", ".join(f"{name!r}: {name}" for name in sorted(spec["document"]["ports"]))
        + "}"
    )
    lines.append(
        "    _raw = {"
        + ", ".join(
            f"{alias!r}: _artifact_{i}.{card['export']}"
            for i, (alias, card) in enumerate(sorted(spec["cards"].items()))
        )
        + "}"
    )
    lines.extend([
        "    _prepared = _prepare(_SPEC, _raw, _ports, _type_scope)",
        "    _nodes, _exports = _execute(_SPEC['unit'], _prepared, _ports, type_scope=_type_scope)",
        "    return _finish(_SPEC, _nodes, _type_scope)", "",
        "", "__all__ = ['create']", "",
    ])
    source = "\n".join(lines)
    compile(source, "<generated module>", "exec")
    return source


def _contract_stub(spec):
    """Emit call-shape protocols; value types remain explicitly Any."""
    doc = spec["document"]
    interfaces = {
        (s["id"], s["version"]): signature_from_spec(s) for s in spec["interfaces"]
    }
    lines = ["from typing import Any, Protocol", ""]
    entries = [
        (f"_Port{i}", port["requires"]) for i, port in enumerate(doc["ports"].values())
    ]
    entries.append(("_Result", doc["module"]["provides"]))
    for name, ref in entries:
        signature = interfaces[(ref["id"], ref["version"])]
        lines.append(f"class {name}(Protocol):")
        for export in signature.types:
            lines.append(f"    {export}: type[Any]")
        for export, call in signature.callables.items():
            receiver = "_self"
            while receiver in call.signature.parameters:
                receiver += "_"
            params = [inspect.Parameter(receiver, inspect.Parameter.POSITIONAL_ONLY)]
            for param in call.signature.parameters.values():
                params.append(
                    param.replace(
                        annotation="Any",
                        default=inspect.Parameter.empty
                        if param.default is inspect.Parameter.empty
                        else Ellipsis,
                    )
                )
            shape = (
                str(inspect.Signature(params))
                .replace("'Any'", "Any")
                .replace("Ellipsis", "...")
            )
            lines.append(
                f"    {'async ' if call.asynchronous else ''}def {export}{shape} -> Any: ..."
            )
        if not signature.types and not signature.callables:
            lines.append("    ...")
        lines.append("")
    params = ", ".join(f"{name}: _Port{i}" for i, name in enumerate(doc["ports"]))
    lines.append(f"def create({'*, ' + params if params else ''}) -> _Result: ...")
    text = "\n".join(lines) + "\n"
    compile(text, "<module contract stub>", "exec")
    return text


def build_module(
    source,
    repository,
    out,
    *,
    choice=None,
    max_candidates=100,
    max_states=10000,
    max_solutions=16,
):
    """Resolve and emit an open or closed graph as a standard repository member."""
    resolution = resolve_module(
        source,
        repository,
        max_candidates=max_candidates,
        max_states=max_states,
        max_solutions=max_solutions,
    )
    if choice is None:
        if resolution["status"] != "unique":
            raise ModuleBuildError(
                f"module resolution is {resolution['status']}; inspect resolve-module and choose a solution: {resolution['rejections'][:3]}"
            )
        choice = 0
    if type(choice) is not int or not 0 <= choice < len(resolution["solutions"]):
        raise ModuleBuildError("choice must identify a returned module solution")
    selected = resolution["solutions"][choice]
    doc = resolution["request"]
    cards, artifacts, locks = selected["cards"], {}, {}
    for alias, card in cards.items():
        lock = repository.lock(card["family"], card["id"], version=card["version"])
        if lock["member"] != card:
            raise ModuleBuildError(f"repository member changed during build: {alias}")
        repository._verify_lock(lock)
        locks[alias] = lock
        for artifact in lock["artifacts"]:
            name = artifact["distribution"]
            if name in artifacts and artifacts[name] != artifact:
                raise ModuleBuildError(f"incompatible artifact versions: {name}")
            artifacts[name] = artifact
    spec = {
        "format": FORMAT,
        "document": doc,
        "cards": cards,
        **{key: value for key, value in selected.items() if key != "cards"},
    }
    # Authored table order must not change bytes under the same graph identity.
    spec = json.loads(canonical_bytes(spec))
    toolchain = hashlib.sha256(
        b"".join(
            (Path(__file__).parent / name).read_bytes()
            for name in (
                "module_build.py",
                "module_runtime.py",
                "module_ir.py",
                "instance_terms.py",
                "refinement.py",
                "mixins.py",
                "indices.py",
                "contracts.py",
                "interfaces.py",
                "associated_graph.py",
                "associated.py",
                "type_terms.py",
            )
        )
    ).hexdigest()
    digest = hashlib.sha256(
        canonical_bytes({"spec": spec, "runtime": __version__, "toolchain": toolchain})
    ).hexdigest()
    import_module = f"mf_members.m_{digest}"
    generated = _generated_source(spec, import_module)
    stub = _contract_stub(spec)
    header = doc["module"]
    publisher = doc["publisher"]["name"]
    identifier = f"{publisher}.{header['id']}"
    distribution = normalize_distribution(f"mf-{doc['family']['name']}-{identifier}")
    if distribution in artifacts:
        raise ModuleBuildError(
            "output distribution conflicts with a selected dependency"
        )
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts.values():
        content = repository._blob(artifact["sha256"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise ModuleBuildError("dependency wheel hash mismatch")
        target = out / artifact["filename"]
        if target.is_symlink():
            raise ModuleBuildError("wheel output must not be a symlink")
        target.write_bytes(content)
    dependencies = sorted({card["distribution"] for card in cards.values()})
    artifact = build_wheel(
        out,
        distribution=distribution,
        version=header["version"],
        files={
            import_module.replace(".", "/") + ".py": generated,
            import_module.replace(".", "/") + ".pyi": stub,
        },
        requires_dist=[f"{name}=={artifacts[name]['version']}" for name in dependencies]
        + [f"module-families=={__version__}"],
        description=header.get("summary", "Compiled open module graph"),
        provenance=canonical_bytes(
            {
                "compiler": FORMAT,
                "graph": spec,
                "selection_locks": {
                    alias: lock["sha256"] for alias, lock in locks.items()
                },
            }
        ),
    )
    artifact["dependencies"] = dependencies
    member = {
        "id": identifier,
        "local_id": header["id"],
        "publisher": publisher,
        "family": doc["family"]["name"],
        "family_version": doc["family"]["version"],
        "version": header["version"],
        "summary": header.get("summary", "Compiled open module graph"),
        "kind": "functor" if doc["ports"] else "module-factory",
        "provides": header["provides"],
        "requires": {name: port["requires"] for name, port in doc["ports"].items()},
        "capabilities": header.get("capabilities", []),
        "effects": selected["effects"],
        "sharing": selected["sharing"],
        "distribution": distribution,
        "wheel": artifact["filename"],
        "sha256": artifact["sha256"],
        "import_module": import_module,
        "export": "create",
        "module_graph": {
            "format": FORMAT,
            "sha256": digest,
            "nodes": len(cards),
            "initialization": selected["order"],
        },
        **{
            key: selected[key]
            for key in ("index_exports", "index_requires", "index_sharing", "associated", "instance_exports", "instance_sharing")
        },
    }
    index = _validate_index(
        {
            "schema_version": 1,
            "publisher": publisher,
            "family": {"context": {}, **doc["family"]},
            "members": [member],
            "artifacts": sorted(
                [*artifacts.values(), artifact], key=lambda a: a["distribution"]
            ),
            "build": {
                "compiler": FORMAT,
                "graph_digest": digest,
                "toolchain_sha256": toolchain,
            },
        }
    )
    (out / "index.json").write_bytes(canonical_bytes(index))
    (out / "module.lock.json").write_bytes(
        canonical_bytes(
            {
                "format": FORMAT,
                "document": doc,
                "bindings": locks,
                "interfaces": selected["interfaces"],
                "graph_digest": digest,
                "toolchain_sha256": toolchain,
                "wheel_sha256": artifact["sha256"],
            }
        )
    )
    (out / "generated.py").write_text(generated)
    (out / "contract.pyi").write_text(stub)
    # A worker can use the exact relevant signatures without installing providers.
    public_refs = {
        (ref["id"], ref["version"])
        for ref in [header["provides"], *member["requires"].values()]
    }
    (out / "work-contract.json").write_bytes(
        canonical_bytes(
            {
                "provides": header["provides"],
                "requires": member["requires"],
                "interfaces": [
                    s
                    for s in selected["interfaces"]
                    if (s["id"], s["version"]) in public_refs
                ],
                "indices": {
                    key: member[key]
                    for key in ("index_exports", "index_requires", "index_sharing")
                },
                "associated": member["associated"],
                "instance_exports": member["instance_exports"],
                "instance_sharing": member["instance_sharing"],
                "obligations": [
                    "Python behavior and declared semantic identities require independent conformance evidence"
                ],
            }
        )
    )
    return {
        "index": str(out / "index.json"),
        "member": identifier,
        "open_ports": sorted(doc["ports"]),
        "graph_sha256": digest,
        "wheel_sha256": artifact["sha256"],
        "artifacts": len(index["artifacts"]),
    }
