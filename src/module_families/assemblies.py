"""Repository-resolved TOML module expressions and immutable assembly locks."""

from __future__ import annotations

import copy
import hashlib
import itertools
import keyword
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from .composition import link_expression
from .contracts import ModuleView
from .interfaces import (
    signature_from_spec,
    validate_interface,
    validate_interface_reference,
)
from .planning import plan
from .registry import canonical_bytes
from .runtime import ImportBundle, atomic_import


class AssemblyError(ValueError):
    """An assembly is invalid, unresolved, ambiguous, or inconsistent with its lock."""


def _digest(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _alias(name: str) -> None:
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        raise AssemblyError(f"binding alias must be a Python identifier: {name!r}")


def _libraries(libraries: Any) -> list[str]:
    if not isinstance(libraries, list):
        raise AssemblyError("assembly.type_libraries must be distinct binding aliases")
    for alias in libraries:
        _alias(alias)
    if len(set(libraries)) != len(libraries):
        raise AssemblyError("assembly.type_libraries must be distinct binding aliases")
    return libraries


def _policy(policy: Any) -> dict:
    if not isinstance(policy, dict) or set(policy) - {"allowed_effects"}:
        raise AssemblyError("policy supports allowed_effects")
    effects = policy.get("allowed_effects")
    if effects is not None and (
        not isinstance(effects, list)
        or any(not isinstance(value, str) or not value.strip() for value in effects)
    ):
        raise AssemblyError("policy.allowed_effects must be a list of nonempty strings")
    return policy


def _used(expression: Any, depth: int = 0) -> set[str]:
    if depth > 64:
        raise AssemblyError("module expression exceeds depth 64")
    if not isinstance(expression, dict) or set(expression) - {"use", "with"}:
        raise AssemblyError(
            "assembly expressions contain use and optional with bindings"
        )
    alias = expression.get("use")
    _alias(alias)
    arguments = expression.get("with", {})
    if not isinstance(arguments, dict):
        raise AssemblyError("expression.with must be a table of named module arguments")
    result = {alias}
    for name, child in arguments.items():
        _alias(name)
        result.update(_used(child, depth + 1))
    return result


def read_assembly(source: str | Path | dict) -> dict:
    if isinstance(source, dict):
        document = copy.deepcopy(source)
    else:
        path = Path(source)
        if path.suffix.lower() != ".toml":
            raise AssemblyError(
                "authored assemblies must be TOML; generated resolutions and locks use JSON"
            )
        document = tomllib.loads(path.read_text())
    try:
        canonical_bytes(document)
    except (ValueError, TypeError) as error:
        raise AssemblyError(
            "assembly must contain finite JSON-compatible data"
        ) from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise AssemblyError("assembly schema_version must be 1")
    if set(document) - {
        "schema_version",
        "assembly",
        "bindings",
        "expression",
        "policy",
    }:
        raise AssemblyError("unknown assembly fields")
    header = document.get("assembly", {})
    if not isinstance(header, dict) or set(header) - {"name", "type_libraries"}:
        raise AssemblyError("assembly header contains name and optional type_libraries")
    if not isinstance(header.get("name"), str) or not header["name"].strip():
        raise AssemblyError("assembly.name is required")
    libraries = _libraries(header.get("type_libraries", []))
    bindings = document.get("bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise AssemblyError("bindings must contain repository member selectors")
    used = _used(document.get("expression")) | set(libraries)
    if used != set(bindings):
        raise AssemblyError(
            f"bindings differ from expression/type libraries: missing={sorted(used - set(bindings))}, unused={sorted(set(bindings) - used)}"
        )
    for alias, selector in bindings.items():
        _alias(alias)
        if not isinstance(selector, dict) or set(selector) - {
            "family",
            "member",
            "version",
            "requires",
            "selection",
        }:
            raise AssemblyError(f"{alias}: invalid repository selector")
        if "member" in selector and not selector.get("family"):
            raise AssemblyError(f"{alias}: member selection requires a family")
        if "member" not in selector and "requires" not in selector:
            raise AssemblyError(
                f"{alias}: select a member or an exact required interface"
            )
        if selector.get("selection", "unique") not in {"unique", "latest"}:
            raise AssemblyError(f"{alias}: selection must be unique or latest")
        for field in ("family", "member", "version"):
            if field in selector and (
                not isinstance(selector[field], str)
                or (field != "version" and not selector[field].strip())
            ):
                raise AssemblyError(f"{alias}.{field} must be a string")
        try:
            SpecifierSet(selector.get("version", ""))
        except InvalidSpecifier as error:
            raise AssemblyError(
                f"{alias}: invalid PEP 440 version selector: {error}"
            ) from error
        if "requires" in selector:
            try:
                validate_interface_reference(selector["requires"])
            except ValueError as error:
                raise AssemblyError(
                    f"{alias}: requires must be an exact id/version reference: {error}"
                ) from error
    _policy(document.get("policy", {}))
    return document


def _matches_selector(card: dict, selector: dict) -> bool:
    return (
        ("family" not in selector or card["family"] == selector["family"])
        and ("member" not in selector or card["id"] == selector["member"])
        and ("requires" not in selector or card.get("provides") == selector["requires"])
        and SpecifierSet(selector.get("version", "")).contains(
            Version(card["version"]), prereleases=True
        )
    )


def _domain(repository, selector: dict, limit: int) -> tuple[list[dict], bool]:
    if "member" in selector:
        candidates = repository.versions(selector["family"], selector["member"])
        truncated = False
    else:
        ref = selector["requires"]
        candidates = []
        while len(candidates) <= limit:
            requested = min(100, limit + 1 - len(candidates))
            page = repository.candidates(
                ref["id"],
                ref["version"],
                family=selector.get("family"),
                version_spec=selector.get("version"),
                limit=requested,
                offset=len(candidates),
            )
            candidates.extend(page)
            if len(page) < requested:
                break
        truncated = len(candidates) > limit
        candidates = candidates[:limit]
    candidates = [card for card in candidates if _matches_selector(card, selector)]
    candidates.sort(
        key=lambda c: (c["family"], c["id"], Version(c["version"]), c["sha256"]),
        reverse=True,
    )
    if selector.get("selection") == "latest":
        latest = {}
        for card in candidates:
            latest.setdefault((card["family"], card["id"]), card)
        candidates = list(latest.values())
    truncated |= len(candidates) > limit
    return candidates[:limit], truncated


def _check(expression, cards, libraries, effects):
    result = plan(expression, cards, allowed_effects=effects)
    if result["status"] != "unique":
        return result
    chosen = result["solutions"][0]
    combined = set(chosen["effects"])
    selected = {item["alias"] for item in chosen["selected"]}
    fixed = {}
    for alias in libraries:
        library = plan({"use": alias}, cards, allowed_effects=effects)
        if library["status"] != "unique":
            return library
        combined.update(library["solutions"][0]["effects"])
        chosen["residual_obligations"].extend(
            library["solutions"][0]["residual_obligations"]
        )
        selected.add(alias)
        for name, identity in cards[alias].get("type_exports", {}).items():
            if name in fixed and fixed[name] != identity:
                return {
                    "status": "unsatisfied",
                    "rejections": [
                        {
                            "code": "type-library-mismatch",
                            "alias": alias,
                            "type": name,
                            "expected": fixed[name],
                            "actual": identity,
                        }
                    ],
                }
            fixed[name] = identity
    for alias in sorted(selected):
        capabilities = cards[alias].get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            not isinstance(value, str) or not value.strip() for value in capabilities
        ):
            raise AssemblyError(
                f"{alias}: capabilities must be a list of nonempty labels"
            )
        if capabilities:
            chosen["residual_obligations"].append(
                {
                    "kind": "capability",
                    "alias": alias,
                    "claims": sorted(set(capabilities)),
                    "verified": False,
                }
            )
        for name, identity in cards[alias].get("type_exports", {}).items():
            if name in fixed and fixed[name] != identity:
                return {
                    "status": "unsatisfied",
                    "rejections": [
                        {
                            "code": "type-library-mismatch",
                            "alias": alias,
                            "type": name,
                            "expected": fixed[name],
                            "actual": identity,
                        }
                    ],
                }
    chosen["effects"] = sorted(combined)
    residuals = {canonical_bytes(item): item for item in chosen["residual_obligations"]}
    chosen["residual_obligations"] = [residuals[key] for key in sorted(residuals)]
    return result


def resolve_assembly(
    source,
    repository,
    *,
    max_solutions: int = 16,
    max_states: int = 10000,
    max_candidates: int = 1000,
) -> dict:
    """Resolve each named repository selector, preserving ambiguous choices."""
    if any(
        type(n) is not int or n < 1 for n in (max_solutions, max_states, max_candidates)
    ):
        raise AssemblyError("resolver budgets must be positive integers")
    document = read_assembly(source)
    aliases = sorted(document["bindings"])
    domains, truncated = {}, False
    for alias in aliases:
        domains[alias], limited = _domain(
            repository, document["bindings"][alias], max_candidates
        )
        truncated |= limited
    total = 1
    for values in domains.values():
        total *= len(values)
    solutions, rejections, visited = [], [], 0
    for alias in aliases:
        if not domains[alias]:
            rejections.append(
                {
                    "code": "no-candidates",
                    "alias": alias,
                    "selector": document["bindings"][alias],
                }
            )
    for combination in itertools.product(*(domains[alias] for alias in aliases)):
        if visited >= max_states or len(solutions) >= max_solutions:
            truncated = True
            break
        visited += 1
        cards = dict(zip(aliases, combination, strict=True))
        try:
            checked = _check(
                document["expression"],
                cards,
                document["assembly"].get("type_libraries", []),
                document.get("policy", {}).get("allowed_effects"),
            )
            if checked["status"] != "unique":
                if len(rejections) < 100:
                    rejections.append(
                        {
                            "members": {
                                a: f"{c['family']}/{c['id']}@{c['version']}"
                                for a, c in cards.items()
                            },
                            "reasons": checked["rejections"],
                        }
                    )
                continue
            interfaces = {}
            for card in cards.values():
                for ref in [card["provides"], *card.get("requires", {}).values()]:
                    key = (ref["id"], ref["version"])
                    interfaces[key] = validate_interface(repository.interface(*key))
            for alias in document["assembly"].get("type_libraries", []):
                ref = cards[alias]["provides"]
                if not interfaces[(ref["id"], ref["version"])]["types"]:
                    raise AssemblyError(
                        f"type library {alias!r} declares no type exports"
                    )
            selection = checked["solutions"][0]
            solutions.append(
                {
                    "expression": selection["expression"],
                    "candidates": cards,
                    "effects": selection["effects"],
                    "residual_obligations": selection["residual_obligations"],
                    "interfaces": [interfaces[k] for k in sorted(interfaces)],
                }
            )
        except (ValueError, KeyError) as error:
            if len(rejections) < 100:
                rejections.append({"error": str(error)})
    complete = not truncated and visited == total
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
        "format": "module-families-resolution",
        "request": document,
        "status": status,
        "complete": complete,
        "candidate_counts": {a: len(domains[a]) for a in aliases},
        "assignments": total,
        "visited_states": visited,
        "solutions": solutions,
        "rejections": rejections,
    }


def lock_assembly(resolution: dict, repository, *, choice: int | None = None) -> dict:
    if not isinstance(resolution, dict) or resolution.get("format") not in {
        "module-families-resolution",
        "module-families-synthesis",
    }:
        raise AssemblyError("expected a repository resolution")
    solutions = resolution.get("solutions", [])
    if choice is None:
        if resolution.get("status") != "unique" or not resolution.get("complete"):
            raise AssemblyError(
                "resolution requires an explicit choice; it is ambiguous, incomplete, or unsatisfied"
            )
        choice = 0
    if type(choice) is not int or not 0 <= choice < len(solutions):
        raise AssemblyError("choice must identify a returned solution")
    selected = solutions[choice]
    synthesis = None
    if resolution["format"] == "module-families-synthesis":
        from .synthesis import read_goal, validate_selection

        goal = read_goal(resolution["request"])
        capabilities = validate_selection(
            goal,
            selected["expression"],
            selected["candidates"],
            max_depth=resolution["bounds"]["max_depth"],
        )
        if capabilities != selected["capabilities"]:
            raise AssemblyError(
                "resolved capabilities differ from selected declarations"
            )
        request = read_assembly(
            {
                "schema_version": 1,
                "assembly": {"name": goal["goal"]["name"]},
                "bindings": {
                    alias: {
                        "family": card["family"],
                        "member": card["id"],
                        "version": "==" + card["version"],
                    }
                    for alias, card in selected["candidates"].items()
                },
                "expression": selected["expression"],
                "policy": goal.get("policy", {}),
            }
        )
        synthesis = {
            "request": goal,
            "bounds": copy.deepcopy(resolution["bounds"]),
            "complete_for_bounds": resolution["complete_for_bounds"],
            "capabilities": capabilities,
        }
    else:
        request = read_assembly(resolution["request"])
    if selected.get("expression") != request["expression"]:
        raise AssemblyError("resolved expression differs from the assembly request")
    if set(selected["candidates"]) != set(request["bindings"]):
        raise AssemblyError("resolved candidates differ from the requested bindings")
    bindings = {}
    for alias, card in selected["candidates"].items():
        if not _matches_selector(card, request["bindings"][alias]):
            raise AssemblyError(
                f"resolved member does not satisfy its selector: {alias}"
            )
        lock = repository.lock(card["family"], card["id"], version=card["version"])
        if canonical_bytes(lock["member"]) != canonical_bytes(card):
            raise AssemblyError(f"repository member changed after resolution: {alias}")
        bindings[alias] = lock
    lock = {
        "schema_version": 1,
        "format": "module-families-assembly-lock",
        "name": request["assembly"]["name"],
        "expression": selected["expression"],
        "bindings": bindings,
        "interfaces": selected["interfaces"],
        "type_libraries": request["assembly"].get("type_libraries", []),
        "policy": request.get("policy", {}),
        "effects": selected["effects"],
        "residual_obligations": selected["residual_obligations"],
        "environment": {
            "locked": False,
            "requirements": sorted(
                {
                    r
                    for binding in bindings.values()
                    for r in binding["external_requirements"]
                }
            ),
        },
    }
    if synthesis is not None:
        lock["synthesis"] = synthesis
    lock["sha256"] = _digest(lock)
    verify_assembly(lock, repository)
    return lock


def verify_assembly(lock: dict, repository=None) -> dict:
    """Check lock integrity, declarations and, when supplied, repository identity.

    Without a repository, hashes establish internal consistency only; they do
    not authenticate the source of the member cards or interface declarations.
    """
    try:
        return _verify_assembly(lock, repository)
    except AssemblyError:
        raise
    except (ValueError, TypeError, KeyError) as error:
        raise AssemblyError(f"invalid assembly lock: {error}") from error


def _verify_assembly(lock: dict, repository=None) -> dict:
    if (
        not isinstance(lock, dict)
        or lock.get("format") != "module-families-assembly-lock"
        or lock.get("schema_version") != 1
    ):
        raise AssemblyError("expected a version-1 assembly lock")
    if _digest({k: v for k, v in lock.items() if k != "sha256"}) != lock.get("sha256"):
        raise AssemblyError("assembly lock hash mismatch")
    if set(lock) - {"synthesis"} != {
        "schema_version",
        "format",
        "name",
        "expression",
        "bindings",
        "interfaces",
        "type_libraries",
        "policy",
        "effects",
        "residual_obligations",
        "environment",
        "sha256",
    }:
        raise AssemblyError("assembly lock has missing or unknown fields")
    if not isinstance(lock["name"], str) or not lock["name"].strip():
        raise AssemblyError("assembly lock name must be a nonempty string")
    libraries = _libraries(lock["type_libraries"])
    policy = _policy(lock["policy"])
    if not isinstance(lock["interfaces"], list):
        raise AssemblyError("locked interfaces must be a list")
    environment = lock["environment"]
    if (
        not isinstance(environment, dict)
        or set(environment) != {"locked", "requirements"}
        or environment["locked"] is not False
    ):
        raise AssemblyError(
            "assembly environment remains unresolved; use a separate environment lock"
        )
    if not isinstance(lock.get("bindings"), dict) or not lock["bindings"]:
        raise AssemblyError("assembly lock has no bindings")
    used = _used(lock["expression"]) | set(libraries)
    if used != set(lock["bindings"]):
        raise AssemblyError("locked binding set differs from the module expression")
    signatures = {}
    for spec in lock["interfaces"]:
        normalized = validate_interface(spec)
        key = (normalized["id"], normalized["version"])
        if key in signatures:
            raise AssemblyError("duplicate locked interface")
        if (
            repository is not None
            and validate_interface(repository.interface(*key)) != normalized
        ):
            raise AssemblyError(f"repository interface differs from lock: {key}")
        signatures[key] = signature_from_spec(normalized)
    cards = {}
    references = set()
    for alias, binding in lock["bindings"].items():
        _alias(alias)
        if repository is not None:
            repository._verify_lock(binding)
        elif _digest(
            {k: v for k, v in binding.items() if k != "sha256"}
        ) != binding.get("sha256"):
            raise AssemblyError(f"module lock hash mismatch: {alias}")
        cards[alias] = binding["member"]
        for ref in [
            cards[alias]["provides"],
            *cards[alias].get("requires", {}).values(),
        ]:
            key = (ref["id"], ref["version"])
            references.add(key)
            if key not in signatures:
                raise AssemblyError(f"locked interface is missing: {ref}")
    if set(signatures) != references:
        raise AssemblyError("locked interfaces differ from the selected contracts")
    for alias in libraries:
        ref = cards[alias]["provides"]
        if not signatures[(ref["id"], ref["version"])].types:
            raise AssemblyError(f"type library {alias!r} declares no type exports")
    checked = _check(
        lock["expression"],
        cards,
        libraries,
        policy.get("allowed_effects"),
    )
    if checked["status"] != "unique":
        raise AssemblyError(
            "locked module expression no longer satisfies its declared interfaces"
        )
    if checked["solutions"][0]["effects"] != lock["effects"]:
        raise AssemblyError("locked effects differ from the module expression")
    if checked["solutions"][0]["residual_obligations"] != lock["residual_obligations"]:
        raise AssemblyError(
            "locked residual obligations differ from the module expression"
        )
    if "synthesis" in lock:
        from .synthesis import read_goal, validate_selection

        synthesis = lock["synthesis"]
        if not isinstance(synthesis, dict) or set(synthesis) != {
            "request",
            "bounds",
            "complete_for_bounds",
            "capabilities",
        }:
            raise AssemblyError("invalid synthesis lock provenance")
        request = read_goal(synthesis["request"])
        bounds = synthesis["bounds"]
        if (
            not isinstance(bounds, dict)
            or set(bounds)
            != {
                "max_depth",
                "max_states",
                "max_solutions",
                "max_candidates",
                "constructor_repetition",
            }
            or bounds["constructor_repetition"] != "forbidden-on-path"
            or any(
                type(bounds[key]) is not int or bounds[key] < 1
                for key in (
                    "max_depth",
                    "max_states",
                    "max_solutions",
                    "max_candidates",
                )
            )
        ):
            raise AssemblyError("invalid synthesis search bounds")
        if type(synthesis["complete_for_bounds"]) is not bool:
            raise AssemblyError("synthesis completeness must be a boolean")
        if (
            request.get("policy", {}) != policy
            or request["goal"]["name"] != lock["name"]
            or libraries
        ):
            raise AssemblyError("locked synthesis request differs from its assembly")
        capabilities = validate_selection(
            request, lock["expression"], cards, max_depth=bounds["max_depth"]
        )
        if capabilities != synthesis["capabilities"]:
            raise AssemblyError(
                "locked synthesis capabilities differ from selected declarations"
            )
    expected = sorted(
        {
            r
            for binding in lock["bindings"].values()
            for r in binding["external_requirements"]
        }
    )
    if expected != lock["environment"]["requirements"]:
        raise AssemblyError("locked external requirements are incomplete")
    return {"cards": cards, "signatures": signatures}


@dataclass(frozen=True)
class AssemblyInstance:
    module: ModuleView
    bundle: ImportBundle
    identity: str

    def __getattr__(self, name):
        return getattr(self.module, name)


def instantiate(lock: dict, repository, target: str | Path) -> AssemblyInstance:
    checked = verify_assembly(lock, repository)

    def link(exports):
        fixed = {}
        for alias in lock.get("type_libraries", []):
            library = link_expression(
                {"use": alias}, exports, checked["cards"], checked["signatures"]
            )
            for name in library.signature.types:
                if name in fixed and fixed[name] is not library[name]:
                    raise AssemblyError(f"conflicting shared type libraries for {name}")
                fixed[name] = library[name]
        module = link_expression(
            lock["expression"],
            exports,
            checked["cards"],
            checked["signatures"],
            types=fixed,
        )
        return module.signature.seal(module, identity=f"assembly:{lock['sha256']}", indices=module.metadata()["indices"])

    bundle = atomic_import(repository, lock["bindings"], target, link=link)
    return AssemblyInstance(bundle.value, bundle, lock["sha256"])
