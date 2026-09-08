"""Finite, import-free synthesis of module expressions from capability goals.

Providers and module constructors come from repository interface declarations.
Capabilities are author assertions, retained as residual obligations; synthesis
does not prove implementation behavior or search arbitrary Python programs.
"""

from __future__ import annotations

import copy
import hashlib
import tomllib
from pathlib import Path

from packaging.version import Version

from .assemblies import _check, _policy
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
    if set(document) - {"schema_version", "goal", "policy", "preferences"}:
        raise SynthesisError("unknown synthesis request fields")
    goal = document.get("goal")
    if not isinstance(goal, dict) or set(goal) - {"name", "requires", "capabilities"}:
        raise SynthesisError("goal declares name, requires and capabilities")
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
    used = set()

    def visit(node, depth, active):
        if (
            not isinstance(node, dict)
            or set(node) - {"use", "with"}
            or node.get("use") not in cards
        ):
            raise SynthesisError("invalid synthesized expression")
        if depth > max_depth:
            raise SynthesisError("synthesized expression exceeds its declared depth")
        alias = node["use"]
        used.add(alias)
        card = cards[alias]
        identity = _identity(card)
        next_active = active
        if _is_open({"requires": {}, **card}):
            if identity in active:
                raise SynthesisError(
                    "constructor artifact repeats on an expression path"
                )
            next_active = active | {identity}
        children = node.get("with", {})
        if not isinstance(children, dict):
            raise SynthesisError("synthesized arguments must be a mapping")
        for child in children.values():
            visit(child, depth + 1, next_active)

    visit(expression, 1, set())
    if used != set(cards):
        raise SynthesisError("synthesized candidates differ from the used expression")
    if cards[expression["use"]]["provides"] != document["goal"]["requires"]:
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
) -> dict:
    """Search bounded constructor applications without imports or downloads.

    Depth counts expression nodes on a path. One constructor artifact may
    occur at most once per path; siblings may reuse it and initialize separate
    instances at runtime. States count candidate expansions and constructed
    applications. Completeness concerns this finite grammar, not all programs.
    """
    if type(max_depth) is not int or not 1 <= max_depth <= 64:
        raise SynthesisError("max_depth must be an integer from 1 to 64")
    if any(
        type(value) is not int or value < 1
        for value in (max_states, max_solutions, max_candidates)
    ):
        raise SynthesisError("search budgets must be positive integers")
    document = read_goal(source)
    bounds = {
        "max_depth": max_depth,
        "max_states": max_states,
        "max_solutions": max_solutions,
        "max_candidates": max_candidates,
        "constructor_repetition": "forbidden-on-path",
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

    def expand(reference, depth, active):
        for card in candidates(reference):
            tick()
            alias, identity = _alias(card), _identity(card)
            if not _is_open({"requires": {}, **card}):
                yield {"use": alias}, {alias: card}
                continue
            if identity in active:
                structural_cutoffs.add("constructor-repetition")
                reject(
                    {"code": "recursive-constructor-cutoff", "member": list(identity)}
                )
                continue
            slots = sorted(card.get("requires", {}))
            if slots and depth >= max_depth:
                structural_cutoffs.add("max_depth")
                reject(
                    {"code": "depth-limit", "member": list(identity), "depth": depth}
                )
                continue
            next_active = active | {identity}

            def arguments(
                position,
                children,
                chosen,
                *,
                slots=slots,
                alias=alias,
                card=card,
                next_active=next_active,
            ):
                if position == len(slots):
                    tick()
                    yield {"use": alias, "with": children}, chosen
                    return
                slot = slots[position]
                for child, child_cards in expand(
                    card["requires"][slot], depth + 1, next_active
                ):
                    yield from arguments(
                        position + 1,
                        {**children, slot: child},
                        {**chosen, **child_cards},
                    )

            yield from arguments(0, {}, {alias: card})

    def declarations(cards):
        selected = set()
        for card in cards.values():
            for ref in [card["provides"], *card.get("requires", {}).values()]:
                key = (ref["id"], ref["version"])
                selected.add(key)
                if key not in interfaces:
                    interfaces[key] = validate_interface(repository.interface(*key))
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
        for expression, cards in expand(document["goal"]["requires"], 1, set()):
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
            if len(solutions) >= max_solutions:
                limited.add("max_solutions")
                break
            solutions.append(solution)
    except _BudgetExhausted:
        pass
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
