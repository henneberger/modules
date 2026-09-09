"""Turn observed missing providers into authorable, metadata-only work drafts.

A draft is not evidence that the goal is impossible or that implementing this
interface will solve it. Acceptance cases must be supplied by a human or agent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .interfaces import validate_interface
from .registry import canonical_bytes
from .synthesis import synthesize


class HandoffError(ValueError):
    """Contribution handoffs could not be safely written."""


def _hash(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _draft(task: dict, policy: dict) -> str:
    def quote(value):
        return json.dumps(value, ensure_ascii=False)

    reference = task["requires"]
    lines = [
        "# Draft: add meaningful [[cases]] before evaluating a contribution.",
        "# No behavioral acceptance cases or capability claims were inferred.",
        "schema_version = 1",
        "",
        "[task]",
        f"id = {quote(task['id'])}",
        f"summary = {quote(task['summary'])}",
        "requires = {id = "
        + quote(reference["id"])
        + ", version = "
        + quote(reference["version"])
        + "}",
        "capabilities = []",
    ]
    if policy.get("allowed_effects") is not None:
        lines.extend(
            [
                "",
                "[policy]",
                "allowed_effects = " + quote(policy["allowed_effects"]),
            ]
        )
    return "\n".join(lines) + "\n"


def plan_contributions(goal_source, repository, out, **synthesis_bounds) -> dict:
    """Write a resolution and drafts for interfaces with no published provider.

    Candidate lookup, interface lookup, and synthesis use metadata only. Search
    bounds retain their original meaning: a cutoff never becomes an absence
    claim. Even a complete search is not an unbounded impossibility proof.
    Existing nonempty output directories are refused to protect authored cases.
    """
    resolution = synthesize(goal_source, repository, **synthesis_bounds)
    destination = Path(out).resolve()
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise HandoffError("handoff output must be a new or empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    resolution_path = destination / "resolution.json"
    resolution_path.write_bytes(canonical_bytes(resolution) + b"\n")
    diagnostics = list(resolution["rejections"])
    tasks, missing = [], []
    # An unsatisfied alternative does not justify new work when a solution exists.
    if not resolution["solutions"] and "repository_changed" not in resolution["truncation"]:
        references = {
            (row["requires"]["id"], row["requires"]["version"])
            for row in resolution["candidate_counts"]
            if row["count"] == 0
        }
        for identifier, version in sorted(references):
            reference = {"id": identifier, "version": version}
            # Synthesis's empty valid domain can include invalid published cards.
            # Check the actual repository rather than interpreting that as absence.
            if repository.candidates(identifier, version, limit=1, offset=0):
                diagnostics.append(
                    {
                        "code": "providers-exist-but-none-selected",
                        "requires": reference,
                        "actionable_missing_provider": False,
                    }
                )
                continue
            try:
                spec = validate_interface(repository.interface(identifier, version))
                if {key: spec[key] for key in ("id", "version")} != reference:
                    raise ValueError("repository returned a different interface")
            except (ValueError, KeyError, TypeError) as error:
                diagnostics.append(
                    {
                        "code": "unavailable-interface-contract",
                        "requires": reference,
                        "error": str(error),
                        "actionable_missing_provider": False,
                    }
                )
                missing.append(
                    {"requires": reference, "status": "needs-interface-contract"}
                )
                continue
            origin = {
                "goal": resolution["request"]["goal"],
                "policy": resolution["request"].get("policy", {}),
            }
            identity = _hash(
                {"requires": reference, "interface": spec, "origin": origin}
            )
            task = {
                "id": "missing-" + identity[:24],
                "summary": f"Implement {identifier}@{version}; author acceptance cases",
                "requires": reference,
                "capabilities": [],
            }
            draft = _draft(task, origin["policy"])
            record = {
                "schema_version": 1,
                "format": "module-families-contribution-handoff",
                "status": "needs-acceptance-cases",
                "requires": reference,
                "interface": spec,
                "origin": origin,
                "task": task,
                "policy": origin["policy"],
                "draft_sha256": hashlib.sha256(draft.encode()).hexdigest(),
                "absence_scope": "No candidates returned by this repository at planning time",
            }
            record["sha256"] = _hash(record)
            metadata_path = destination / (task["id"] + ".json")
            draft_path = destination / (task["id"] + ".toml")
            metadata_path.write_bytes(canonical_bytes(record) + b"\n")
            draft_path.write_text(draft)
            tasks.append(
                {
                    "id": task["id"],
                    "requires": reference,
                    "status": record["status"],
                    "sha256": record["sha256"],
                    "metadata_path": str(metadata_path),
                    "draft_path": str(draft_path),
                }
            )
            missing.append({"requires": reference, "status": "needs-acceptance-cases"})
    status = (
        "resolved"
        if resolution["solutions"]
        else "incomplete"
        if not resolution["complete"]
        else "needs-contributions"
        if tasks
        else "unsatisfied"
    )
    plan = {
        "schema_version": 1,
        "format": "module-families-contribution-plan",
        "status": status,
        "synthesis_status": resolution["status"],
        "resolution_path": str(resolution_path),
        "tasks": tasks,
        "missing_obligations": missing,
        "diagnostics": diagnostics,
        "truncation": resolution["truncation"],
        "structural_cutoffs": resolution["structural_cutoffs"],
        "limitations": [
            "Drafts require authored acceptance cases before evaluation.",
            "Missing-provider observations do not prove the goal impossible or guarantee a contribution will solve it.",
            "Only interfaces reached by bounded synthesis are considered; this is not a complete decomposition of missing work.",
            "Repository observations are not an atomic snapshot across concurrent publications.",
        ],
    }
    plan["plan_path"] = str(destination / "plan.json")
    (destination / "plan.json").write_bytes(canonical_bytes(plan) + b"\n")
    return plan


__all__ = ["HandoffError", "plan_contributions"]
