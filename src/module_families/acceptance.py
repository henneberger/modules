"""Publish the exact tested contribution through an explicit local evidence gate.

This is an evaluator workflow, not a repository-wide authentication policy.
Unsigned reports must come from an evaluator the caller trusts.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contributions import ContributionError, verify_evidence
from .environments import verify_environment
from .registry import canonical_bytes


def accept_contribution(task_path, environment_lock, evidence_path, index_path, repository):
    """Verify passing evidence and prevent publishing untested members alongside it."""
    evidence = verify_evidence(task_path, environment_lock, evidence_path)
    environment, _ = verify_environment(environment_lock)
    assembly = environment["assembly"]
    selected = assembly["bindings"][assembly["expression"]["use"]]
    index_path = Path(index_path)
    index = json.loads(index_path.read_text())
    if not isinstance(index, dict) or not isinstance(index.get("family"), dict):
        raise ContributionError("publication must be an index with a family object")
    if canonical_bytes(index.get("members")) != canonical_bytes([selected["member"]]):
        raise ContributionError("publication must contain exactly the tested root member")
    if index.get("family", {}).get("name") != selected["family"]:
        raise ContributionError("publication family differs from tested contribution")
    if index.get("publisher") != selected["member"].get("publisher"):
        raise ContributionError("publication publisher differs from tested contribution")
    expected = {record["distribution"]: record for record in selected["artifacts"]}
    artifacts = index.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) != len(expected):
        raise ContributionError("publication artifacts differ from tested root closure")
    if any(not isinstance(record, dict) or not isinstance(record.get("distribution"), str) for record in artifacts):
        raise ContributionError("publication artifact records must name distributions")
    actual = {record["distribution"]: record for record in artifacts}
    if canonical_bytes(actual) != canonical_bytes(expected):
        raise ContributionError("publication artifacts differ from tested root closure")
    # No unrelated contract publication may piggyback on evaluated code.
    known = {(spec["id"], spec["version"]): spec for spec in assembly["interfaces"]}
    interfaces = index.get("interfaces", [])
    if not isinstance(interfaces, list) or any(
        not isinstance(spec, dict) or not isinstance(spec.get("id"), str)
        or not isinstance(spec.get("version"), str) for spec in interfaces
    ):
        raise ContributionError("publication interfaces must have exact identities")
    for spec in interfaces:
        if canonical_bytes(spec) != canonical_bytes(known.get((spec["id"], spec["version"]))):
            raise ContributionError("publication includes an untested interface declaration")
    published = repository.publish(index_path)
    return {
        "format": "module-families-accepted-contribution-1",
        "evidence_sha256": evidence["sha256"],
        "environment_sha256": environment["sha256"],
        "program_sha256": assembly["sha256"],
        "member": selected["member"]["id"],
        "publication": published,
        "trust": "unsigned evidence from caller-trusted evaluator; not a repository admission policy",
    }
