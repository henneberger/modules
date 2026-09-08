"""Portable contribution tasks and unsigned, artifact-bound local evaluations.

Cases establish observations, not behavioral proofs. Execution uses a complete
locked Python environment, which is isolation for dependencies, not a sandbox.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tomllib
from pathlib import Path

from .environments import run_environment, verify_environment
from .interfaces import (
    signature_from_spec,
    validate_interface,
    validate_interface_reference,
)
from .planning import plan
from .registry import canonical_bytes


class ContributionError(ValueError):
    """Invalid contribution specification, incompatible selection, or evidence."""


def _hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _shape(value, required, optional, label):
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required - optional
    ):
        raise ContributionError(f"{label} has missing or unknown fields")
    return value


def _text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContributionError(f"{label} must be a nonempty trimmed string")
    return value


def _strings(value, label):
    if not isinstance(value, list):
        raise ContributionError(f"{label} must be a list")
    result = [_text(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ContributionError(f"{label} must be distinct")
    return sorted(result)


def _json(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json(item) for key, item in value.items()}
    raise ContributionError("case values must be finite JSON values")


def _document(document):
    _shape(document, {"schema_version", "task", "cases"}, {"policy"}, "contribution")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ContributionError("unsupported contribution schema_version")
    task = _shape(
        document["task"], {"id", "summary", "requires"}, {"capabilities"}, "task"
    )
    task = {
        "id": _text(task["id"], "task.id"),
        "summary": _text(task["summary"], "task.summary"),
        "requires": validate_interface_reference(task["requires"]),
        "capabilities": _strings(task.get("capabilities", []), "task.capabilities"),
    }
    policy = _shape(document.get("policy", {}), set(), {"allowed_effects"}, "policy")
    policy = {
        "allowed_effects": _strings(
            policy.get("allowed_effects", []), "policy.allowed_effects"
        )
    }
    cases = document["cases"]
    if not isinstance(cases, list) or not cases:
        raise ContributionError("contribution requires at least one case")
    result = []
    for case in cases:
        _shape(case, {"id", "export", "args", "expected"}, set(), "case")
        if not isinstance(case["args"], list):
            raise ContributionError("case.args must be a list")
        result.append(
            {
                "id": _text(case["id"], "case.id"),
                "export": _text(case["export"], "case.export"),
                "args": _json(case["args"]),
                "expected": _json(case["expected"]),
            }
        )
    if len({case["id"] for case in result}) != len(result):
        raise ContributionError("case IDs must be distinct")
    return {"schema_version": 1, "task": task, "policy": policy, "cases": result}


def read_contribution(path: str | Path) -> dict:
    """Read a strict TOML contribution contract without importing providers."""
    path = Path(path)
    if path.suffix != ".toml":
        raise ContributionError("contribution source must be a .toml file")
    return _document(tomllib.loads(path.read_text()))


def _interface(document, interface):
    interface = validate_interface(interface)
    reference = {key: interface[key] for key in ("id", "version")}
    if reference != document["task"]["requires"]:
        raise ContributionError("task interface identity mismatch")
    signature = signature_from_spec(interface)
    for case in document["cases"]:
        callable_spec = signature.callables.get(case["export"])
        if callable_spec is None:
            raise ContributionError(f"unknown case export: {case['export']}")
        try:
            callable_spec.signature.bind(*case["args"])
        except TypeError as error:
            raise ContributionError(
                f"case {case['id']} does not bind: {error}"
            ) from error
    return interface


def prepare_contribution(path, repository, out) -> dict:
    """Seal exact interface metadata and cases; no candidate discovery or fetch."""
    document = read_contribution(path)
    ref = document["task"]["requires"]
    interface = _interface(document, repository.interface(ref["id"], ref["version"]))
    bundle = {
        "format": "module-families-contribution-task-1",
        "document": document,
        "interface": interface,
    }
    bundle["sha256"] = _hash(bundle)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    destination = out / "task.json"
    if destination.exists() and destination.read_bytes() != canonical_bytes(bundle):
        raise ContributionError(
            "task destination already contains a different contract"
        )
    destination.write_bytes(canonical_bytes(bundle))
    return bundle


def _task(path):
    task = json.loads(Path(path).read_text())
    _shape(task, {"format", "document", "interface", "sha256"}, set(), "task bundle")
    if task["format"] != "module-families-contribution-task-1" or task[
        "sha256"
    ] != _hash({k: v for k, v in task.items() if k != "sha256"}):
        raise ContributionError("task bundle hash or format mismatch")
    document = _document(task["document"])
    interface = _interface(document, task["interface"])
    if canonical_bytes(document) != canonical_bytes(
        task["document"]
    ) or canonical_bytes(interface) != canonical_bytes(task["interface"]):
        raise ContributionError("task bundle is not normalized")
    return task


def _selection(task, lock):
    assembly = lock["assembly"]
    cards = {
        alias: binding["member"] for alias, binding in assembly["bindings"].items()
    }
    checked = plan(
        assembly["expression"],
        cards,
        allowed_effects=task["document"]["policy"]["allowed_effects"],
    )
    if checked["status"] != "unique":
        raise ContributionError(
            "selected program violates contribution effect policy or composition"
        )
    selected = checked["solutions"][0]
    if selected["provides"] != task["document"]["task"]["requires"]:
        raise ContributionError("selected program provides a different task interface")
    if task["interface"] not in assembly["interfaces"]:
        raise ContributionError(
            "selected program differs from the sealed task interface"
        )
    if set(assembly["effects"]) - set(task["document"]["policy"]["allowed_effects"]):
        raise ContributionError("selected program exceeds contribution effects")
    capabilities = {
        capability
        for item in selected["selected"]
        for capability in cards[item["alias"]].get("capabilities", [])
    }
    missing = set(task["document"]["task"]["capabilities"]) - capabilities
    if missing:
        raise ContributionError(
            f"selected program lacks declared capabilities: {sorted(missing)}"
        )


def evaluate_contribution(task_path, environment_lock, target, *, timeout=30) -> dict:
    """Run each case in a fresh locked worker process and return local evidence."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ContributionError("timeout must be a finite positive number")
    task = _task(task_path)
    executable = Path(target).resolve() / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    lock, _ = verify_environment(environment_lock, python=str(executable))
    _selection(task, lock)
    results = []
    for case in task["document"]["cases"]:
        result = {"id": case["id"]}
        try:
            replay = run_environment(
                environment_lock,
                target,
                export=case["export"],
                args=case["args"],
                timeout=timeout,
            )
            if (
                replay.get("assembly") != lock["assembly"]["sha256"]
                or replay.get("export") != case["export"]
            ):
                raise ContributionError("worker result provenance mismatch")
            result["actual"] = _json(replay["result"])
            result["status"] = (
                "passed"
                if canonical_bytes(result["actual"])
                == canonical_bytes(case["expected"])
                else "failed"
            )
        except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
            result = {
                "id": case["id"],
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
        results.append(result)
    report = {
        "format": "module-families-contribution-evidence-1",
        "task_sha256": task["sha256"],
        "environment_sha256": lock["sha256"],
        "program_sha256": lock["assembly"]["sha256"],
        "timeout_seconds": timeout,
        "trust": "unsigned-local-observation",
        "cases": results,
        "status": "passed"
        if all(result["status"] == "passed" for result in results)
        else "failed",
    }
    report["sha256"] = _hash(report)
    return report


def verify_evidence(task_path, environment_lock, evidence_path) -> dict:
    """Verify hashes, selection and acceptance without executing provider code.

    An unsigned report can be fabricated by its author; verification establishes
    internal consistency and artifact binding, not an authenticated execution.
    """
    task = _task(task_path)
    lock, _ = verify_environment(environment_lock)
    _selection(task, lock)
    report = json.loads(Path(evidence_path).read_text())
    _shape(
        report,
        {
            "format",
            "task_sha256",
            "environment_sha256",
            "program_sha256",
            "timeout_seconds",
            "trust",
            "cases",
            "status",
            "sha256",
        },
        set(),
        "evidence",
    )
    if report["sha256"] != _hash({k: v for k, v in report.items() if k != "sha256"}):
        raise ContributionError("evidence hash mismatch")
    expected = {
        "format": "module-families-contribution-evidence-1",
        "task_sha256": task["sha256"],
        "environment_sha256": lock["sha256"],
        "program_sha256": lock["assembly"]["sha256"],
        "trust": "unsigned-local-observation",
        "status": "passed",
    }
    if any(report[key] != value for key, value in expected.items()):
        raise ContributionError("evidence provenance or acceptance mismatch")
    timeout = report["timeout_seconds"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ContributionError("invalid evidence timeout")
    if not isinstance(report["cases"], list) or len(report["cases"]) != len(
        task["document"]["cases"]
    ):
        raise ContributionError("evidence case count mismatch")
    for case, result in zip(task["document"]["cases"], report["cases"], strict=True):
        _shape(result, {"id", "status", "actual"}, set(), "accepted case evidence")
        if (
            result["id"] != case["id"]
            or result["status"] != "passed"
            or canonical_bytes(_json(result["actual"]))
            != canonical_bytes(case["expected"])
        ):
            raise ContributionError("evidence contains an unaccepted case")
    return report
