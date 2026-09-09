"""Finite, import-free synthesis of module expressions from capability goals.

Providers and module constructors come from repository interface declarations.
Capabilities are author assertions, retained as residual obligations; synthesis
does not prove implementation behavior or search arbitrary Python programs.
"""

from __future__ import annotations

import copy
import hashlib
import re
import tomllib
from pathlib import Path

from packaging.version import Version

from .assemblies import _check, _policy
from .associated import validate_against_interface
from .interfaces import validate_interface, validate_interface_reference
from .planning import _cards, _is_open
from .registry import canonical_bytes


class SynthesisError(ValueError):
    """A goal or claimed synthesized selection is malformed."""


def read_goal(source: str | Path | dict) -> dict:
    if isinstance(source, dict):
        document = copy.deepcopy(source)
    else:
        path = Path(source)
        if path.suffix.lower() != ".toml":
            raise SynthesisError(
                "authored goals must be TOML; generated plans and locks use JSON"
            )
        document = tomllib.loads(path.read_text())
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise SynthesisError("goal schema_version must be 1")
    if set(document) - {"schema_version", "goal", "policy", "preferences", "evidence"}:
        raise SynthesisError("unknown synthesis request fields")
    goal = document.get("goal")
    if not isinstance(goal, dict) or set(goal) - {
        "name",
        "requires",
        "capabilities",
        "root",
    }:
        raise SynthesisError(
            "goal declares name, requires, capabilities and optional exact root"
        )
    if not isinstance(goal.get("name"), str) or not goal["name"].strip():
        raise SynthesisError("goal.name must be a nonempty string")
    try:
        goal["requires"] = validate_interface_reference(goal.get("requires"))
        _policy(document.get("policy", {}))
        canonical_bytes(document)
    except (ValueError, TypeError) as error:
        raise SynthesisError(str(error)) from error
    capabilities = goal.get("capabilities", [])
    if not isinstance(capabilities, list) or any(
        not isinstance(value, str) or not value.strip() for value in capabilities
    ):
        raise SynthesisError("goal.capabilities must be a list of nonempty labels")
    goal["capabilities"] = sorted(set(capabilities))
    if "evidence" in document:
        from .evidence import validate_policy

        document["evidence"] = validate_policy(document["evidence"])
    if "root" in goal:
        root = goal["root"]
        if (
            not isinstance(root, dict)
            or set(root) != {"family", "id", "version", "sha256"}
            or any(
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                for value in root.values()
            )
            or re.fullmatch(r"[0-9a-f]{64}", root["sha256"]) is None
        ):
            raise SynthesisError(
                "goal.root must identify exact family, id, version and SHA256"
            )
        try:
            Version(root["version"])
        except ValueError as error:
            raise SynthesisError("goal.root.version must be a valid version") from error
    preferences = document.get("preferences", {})
    if (
        not isinstance(preferences, dict)
        or set(preferences) - {"selection"}
        or preferences.get("selection", "all") not in {"all", "min_artifacts"}
    ):
        raise SynthesisError("preferences.selection must be all or min_artifacts")
    return document


def _identity(card: dict) -> tuple[str, str, str, str]:
    return tuple(card[key] for key in ("family", "id", "version", "sha256"))


def _alias(card: dict) -> str:
    return "m_" + hashlib.sha256(canonical_bytes(_identity(card))).hexdigest()


def _capabilities(cards: dict) -> list[str]:
    values = set()
    for alias, card in cards.items():
        capabilities = card.get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            not isinstance(value, str) or not value.strip() for value in capabilities
        ):
            raise SynthesisError(
                f"{alias}: capabilities must be a list of nonempty labels"
            )
        values.update(capabilities)
    return sorted(values)


def validate_selection(
    request: dict, expression: dict, cards: dict, *, max_depth: int
) -> list[str]:
    """Validate goal obligations and the finite expression grammar for a lock."""
    document = read_goal(request)
    if type(max_depth) is not int or not 1 <= max_depth <= 64:
        raise SynthesisError("max_depth must be an integer from 1 to 64")
    from .module_ir import ModuleIRError, normalize_expression

    try:
        graph = normalize_expression(expression, max_depth=max_depth)
    except ModuleIRError as error:
        raise SynthesisError(str(error)) from error
    if any("use" not in node or node["use"] not in cards for node in graph.nodes.values()):
        raise SynthesisError("invalid synthesized expression")
    used = graph.used()
    root_alias = graph.nodes[graph.root]["use"]
    if "root" in document["goal"] and _identity(cards[root_alias]) != _identity(
        document["goal"]["root"]
    ):
        raise SynthesisError("selected root differs from the required exact artifact")
    if used != set(cards):
        raise SynthesisError("synthesized candidates differ from the used expression")
    if cards[root_alias]["provides"] != document["goal"]["requires"]:
        raise SynthesisError(
            "synthesized result does not provide the requested interface"
        )
    capabilities = _capabilities(cards)
    missing = set(document["goal"]["capabilities"]) - set(capabilities)
    if missing:
        raise SynthesisError(
            f"synthesized expression lacks capabilities: {sorted(missing)}"
        )
    checked = _check(
        expression, cards, [], document.get("policy", {}).get("allowed_effects")
    )
    if checked["status"] != "unique":
        raise SynthesisError(
            "synthesized expression violates declared interfaces, effects or sharing"
        )
    return capabilities


class _BudgetExhausted(Exception):
    pass


def synthesize(
    source,
    repository,
    *,
    max_depth: int = 5,
    max_states: int = 10000,
    max_solutions: int = 16,
    max_candidates: int = 100,
    evidence_store=None,
    trust_keys=None,
) -> dict:
    if type(max_depth) is not int or not 1 <= max_depth <= 64:
        raise SynthesisError("max_depth must be an integer from 1 to 64")
    if any(
        type(value) is not int or value < 1
        for value in (max_states, max_solutions, max_candidates)
    ):
        raise SynthesisError("search budgets must be positive integers")
    document = read_goal(source)
    revision = canonical_bytes(repository.revision())
    bounds = {
        "max_depth": max_depth,
        "max_states": max_states,
        "max_solutions": max_solutions,
        "max_candidates": max_candidates,
        "constructor_repetition": "bounded-by-depth",
    }
    domains, interfaces, costs = {}, {}, {}
    rejections, seen_rejections = [], set()
    limited, structural_cutoffs = set(), set()
    states = 0

    def reject(record):
        encoded = canonical_bytes(record)
        if encoded not in seen_rejections and len(rejections) < 100:
            seen_rejections.add(encoded)
            rejections.append(record)

    def tick():
        nonlocal states
        if states >= max_states:
            limited.add("max_states")
            raise _BudgetExhausted
        states += 1

    def candidates(reference):
        key = (reference["id"], reference["version"])
        if key in domains:
            return domains[key]
        rows = []
        while len(rows) <= max_candidates:
            requested = min(100, max_candidates + 1 - len(rows))
            page = repository.candidates(*key, limit=requested, offset=len(rows))
            rows.extend(page)
            if len(page) < requested:
                break
        if len(rows) > max_candidates:
            limited.add("max_candidates")
            reject(
                {
                    "code": "candidate-limit",
                    "requires": reference,
                    "limit": max_candidates,
                }
            )
        values = []
        for card in rows[:max_candidates]:
            try:
                # This checks immutable metadata without interpreting its code.
                _cards({_alias(card): card})
                _capabilities({_alias(card): card})
                if card["provides"] != reference:
                    raise SynthesisError(
                        "repository returned a different provided interface"
                    )
            except (ValueError, KeyError, TypeError) as error:
                reject({"code": "invalid-candidate", "error": str(error)})
                continue
            values.append(card)
        values.sort(
            key=lambda card: (
                card["family"],
                card["id"],
                Version(card["version"]),
                card["sha256"],
            )
        )
        domains[key] = values
        if not values:
            reject({"code": "no-provider", "requires": reference})
        return values

    def expand(reference, depth):
        pinned = None
        if "root" in document["goal"]:
            selected = document["goal"]["root"]
            try:
                pinned = repository.lock(selected["family"], selected["id"], version=selected["version"])["member"]
                _cards({_alias(pinned): pinned})
                if _identity(pinned) != _identity(selected) or pinned["provides"] != reference:
                    raise SynthesisError("pinned root identity or interface mismatch")
            except (ValueError, KeyError, TypeError) as error:
                reject({"code": "invalid-pinned-root", "error": str(error)})
                return

        def closed(name, nodes, cards, visiting=None):
            visiting = set() if visiting is None else visiting
            if name in visiting:
                return False
            node = nodes[name]
            if set(node.get("with", {})) != set(cards[node["use"]].get("requires", {})):
                return False
            return all(closed(child["ref"], nodes, cards, visiting | {name}) for child in node.get("with", {}).values())

        def expression(nodes, root):
            needed = set()
            def visit(name):
                if name in needed:
                    return
                needed.add(name)
                for child in nodes[name].get("with", {}).values():
                    visit(child["ref"])
            visit(root)
            return {"let": {name: nodes[name] for name in sorted(needed)}, "in": {"ref": root}}

        validated = set()

        def valid(nodes, cards):
            for name in nodes:
                if not closed(name, nodes, cards):
                    continue
                graph = expression(nodes, name)
                identity = canonical_bytes({"expression": graph, "artifacts": {node["use"]: cards[node["use"]]["sha256"] for node in graph["let"].values()}})
                if identity in validated:
                    continue
                result = _check(graph, cards, [], document.get("policy", {}).get("allowed_effects"))
                if result["status"] != "unique":
                    reject({"code": "partial-graph-conflict", "binding": name, "reasons": result.get("rejections", [])})
                    return False
                validated.add(identity)
            return True

        def branches(nodes, cards, pending, root):
            parent, slot, required, level, ancestors = pending[0]
            remaining = pending[1:]
            if parent is not None:
                for name, node in sorted(nodes.items()):
                    if name in ancestors or cards[node["use"]]["provides"] != required or not closed(name, nodes, cards):
                        continue
                    tick()
                    updated = copy.deepcopy(nodes)
                    updated[parent]["with"][slot] = {"ref": name}
                    if valid(updated, cards):
                        yield updated, cards, remaining, root
            choices = [pinned] if parent is None and pinned is not None else candidates(required)
            for card in choices:
                tick()
                requirements = card.get("requires", {})
                if requirements and level >= max_depth:
                    structural_cutoffs.add("max_depth")
                    reject({"code": "depth-limit", "member": list(_identity(card)), "depth": level})
                    continue
                alias = _alias(card)
                chosen = {**cards, alias: card}
                name = f"n{len(nodes)}"
                updated = copy.deepcopy(nodes)
                updated[name] = {"use": alias}
                if _is_open({"requires": {}, **card}):
                    updated[name]["with"] = {}
                if parent is not None:
                    updated[parent]["with"][slot] = {"ref": name}
                children = [(name, port, requirement, level + 1, ancestors | {name}) for port, requirement in sorted(requirements.items())]
                if valid(updated, chosen):
                    yield updated, chosen, children + remaining, root or name

        stack = [iter([({}, {}, [(None, None, reference, depth, set())], None)])]
        while stack:
            try:
                nodes, cards, pending, root = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if not pending:
                yield expression(nodes, root), cards
            else:
                stack.append(branches(nodes, cards, pending, root))

    def declarations(cards):
        selected = set()
        for card in cards.values():
            for ref in [card["provides"], *card.get("requires", {}).values()]:
                key = (ref["id"], ref["version"])
                selected.add(key)
                if key not in interfaces:
                    interfaces[key] = validate_interface(repository.interface(*key))
        for card in cards.values():
            ref = card["provides"]
            validate_against_interface(card, interfaces[(ref["id"], ref["version"])])
            from .instance_terms import validate_instance_interface

            validate_instance_interface(card, interfaces[(ref["id"], ref["version"])], {
                slot: interfaces[(requirement["id"], requirement["version"])] for slot, requirement in card.get("requires", {}).items()
            })
        from .mixins import validate_mixin_interfaces

        for card in cards.values():
            validate_mixin_interfaces(card, interfaces)
        return [interfaces[key] for key in sorted(selected)]

    def artifact_count(cards):
        artifacts = set()
        for card in cards.values():
            identity = _identity(card)
            if identity not in costs:
                lock = repository.lock(
                    card["family"], card["id"], version=card["version"]
                )
                costs[identity] = {
                    (item["distribution"], item["version"], item["sha256"])
                    for item in lock["artifacts"]
                }
            artifacts.update(costs[identity])
        return len(artifacts)

    solutions, seen_solutions = [], set()
    try:
        for expression, cards in expand(document["goal"]["requires"], 1):
            encoded = canonical_bytes(expression)
            if encoded in seen_solutions:
                continue
            seen_solutions.add(encoded)
            try:
                capabilities = validate_selection(
                    document, expression, cards, max_depth=max_depth
                )
                checked = _check(
                    expression,
                    cards,
                    [],
                    document.get("policy", {}).get("allowed_effects"),
                )
                specs = declarations(cards)
                selected = checked["solutions"][0]
                solution = {
                    "expression": selected["expression"],
                    "candidates": cards,
                    "effects": selected["effects"],
                    "capabilities": capabilities,
                    "residual_obligations": selected["residual_obligations"],
                    "interfaces": specs,
                }
                if document.get("preferences", {}).get("selection") == "min_artifacts":
                    solution["artifact_count"] = artifact_count(cards)
            except (ValueError, KeyError, TypeError) as error:
                reject(
                    {
                        "code": "goal-or-contract-mismatch",
                        "members": [list(_identity(card)) for card in cards.values()],
                        "error": str(error),
                    }
                )
                continue
            if "evidence" in document:
                if evidence_store is None or trust_keys is None:
                    reject(
                        {
                            "code": "missing-evidence-trust",
                            "error": "evidence policy requires a store and explicit evaluator trust keys",
                        }
                    )
                    continue
                try:
                    bindings = {}
                    for alias, card in cards.items():
                        binding = repository.lock(
                            card["family"], card["id"], version=card["version"]
                        )
                        if canonical_bytes(binding["member"]) != canonical_bytes(card):
                            raise SynthesisError(
                                "repository member changed during evidence selection"
                            )
                        bindings[alias] = binding
                    assembly = {
                        "expression": solution["expression"],
                        "bindings": bindings,
                        "interfaces": specs,
                        "type_libraries": [],
                    }
                    match = evidence_store.match(
                        assembly, document["evidence"], trust_keys
                    )
                    if not match["accepted"]:
                        reject(
                            {
                                "code": "evidence-unsatisfied",
                                "context_sha256": match["context_sha256"],
                                "missing_tasks": match["missing_tasks"],
                                "details": match["rejections"],
                            }
                        )
                        continue
                    solution["evidence"] = {
                        "policy": document["evidence"],
                        "context_sha256": match["context_sha256"],
                        "observations": match["observations"],
                    }
                except (ValueError, KeyError, TypeError) as error:
                    reject({"code": "invalid-evidence", "error": str(error)})
                    continue
            if len(solutions) >= max_solutions:
                limited.add("max_solutions")
                break
            solutions.append(solution)
    except _BudgetExhausted:
        pass
    revision_unchanged = canonical_bytes(repository.revision()) == revision
    if not revision_unchanged:
        limited.add("repository_changed")
        reject({
            "code": "repository-changed-during-synthesis",
            "error": "Publication changed the candidate domain; rerun synthesis before selecting a program.",
        })
    complete_for_bounds = not limited
    if (
        document.get("preferences", {}).get("selection") == "min_artifacts"
        and solutions
    ):
        least = min(solution["artifact_count"] for solution in solutions)
        solutions = [
            solution for solution in solutions if solution["artifact_count"] == least
        ]
    # A failed bounded search with a recursive/depth cutoff supplies no proof
    # that the requested behavior cannot be assembled at a greater depth.
    complete = complete_for_bounds and not (not solutions and structural_cutoffs)
    status = (
        "incomplete"
        if not complete
        else "unsatisfied"
        if not solutions
        else "unique"
        if len(solutions) == 1
        else "ambiguous"
    )
    return {
        "schema_version": 1,
        "format": "module-families-synthesis",
        "request": document,
        "status": status,
        "complete": complete,
        "complete_for_bounds": complete_for_bounds,
        "repository_revision": {"sha256": hashlib.sha256(revision).hexdigest(), "unchanged": revision_unchanged},
        "bounds": bounds,
        "visited_states": states,
        "candidate_counts": [
            {"requires": {"id": key[0], "version": key[1]}, "count": len(values)}
            for key, values in sorted(domains.items())
        ],
        "solutions": solutions,
        "rejections": rejections,
        "truncation": sorted(limited),
        "structural_cutoffs": sorted(structural_cutoffs),
        "limitations": [
            "Capabilities and behavioral declarations remain author claims.",
            "Search is finite: depth is bounded and a constructor artifact cannot repeat on one path.",
            "Complete results are complete only for the declared finite grammar and enumerated candidate domain; they do not establish unbounded synthesis completeness.",
        ],
    }


__all__ = ["SynthesisError", "read_goal", "synthesize", "validate_selection"]
