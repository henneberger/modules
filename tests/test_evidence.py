from __future__ import annotations

import copy
import json
import sqlite3
from unittest.mock import Mock

import pytest
from test_assemblies import provider, publish
from test_assemblies import repo as repo
from test_contributions import SOURCE, SPEC
from test_synthesis import RETRY_SOURCE, goal, retry_members

from module_families import contributions as c
from module_families import environments
from module_families import evidence as e
from module_families.assemblies import AssemblyError, lock_assembly, verify_assembly
from module_families.synthesis import read_goal, synthesize

KEY = b"test-evaluator-secret-key-32-bytes!"
TRUST = {"ci": KEY}
TASK = "1" * 64


def attestation(
    assembly, *, task=TASK, evaluator="ci", secret=KEY, environment="2" * 64
):
    payload = {
        "format": "module-families-evaluator-attestation-1",
        "evaluator": evaluator,
        "task_sha256": task,
        "environment_sha256": "3" * 64,
        "program_sha256": "4" * 64,
        "evidence_sha256": "5" * 64,
        "context_sha256": e.context_fingerprint(assembly),
        "environment_fingerprint": environment,
        "status": "passed",
    }
    record = {"payload": payload, "signature": e._signature(payload, secret)}
    record["sha256"] = e._digest(record)
    return record


@pytest.fixture
def assembly():
    return {
        "expression": {"use": "a", "with": {"child": {"use": "b"}}},
        "bindings": {
            alias: {
                "member": {"id": alias, "sha256": digest * 64},
                "artifacts": [{"distribution": alias, "sha256": digest * 64}],
                "external_requirements": [],
            }
            for alias, digest in [("a", "a"), ("b", "b")]
        },
        "interfaces": [{"id": "example", "version": "1"}],
        "type_libraries": [],
    }


def test_context_ignores_local_aliases_and_order_not_composition(assembly):
    changed = copy.deepcopy(assembly)
    changed["bindings"]["renamed"] = changed["bindings"].pop("a")
    changed["expression"]["use"] = "renamed"
    assert e.context_fingerprint(assembly) == e.context_fingerprint(changed)
    changed["expression"]["with"]["another"] = changed["expression"]["with"].pop(
        "child"
    )
    assert e.context_fingerprint(assembly) != e.context_fingerprint(changed)


@pytest.mark.parametrize(
    "change", ["artifact", "member", "interface", "external", "child"]
)
def test_context_binds_whole_program(assembly, change):
    altered = copy.deepcopy(assembly)
    if change == "artifact":
        altered["bindings"]["b"]["artifacts"][0]["sha256"] = "c" * 64
    elif change == "member":
        altered["bindings"]["a"]["member"]["associated"] = {"Document": "other"}
    elif change == "interface":
        altered["interfaces"][0]["version"] = "2"
    elif change == "external":
        altered["bindings"]["b"]["external_requirements"] = ["dependency==2"]
    else:
        altered["expression"]["with"]["child"]["use"] = "a"
    assert e.context_fingerprint(altered) != e.context_fingerprint(assembly)


def test_signature_requires_explicit_trust_and_secret(assembly):
    record = attestation(assembly)
    assert e.verify_attestation(record, TRUST)["evaluator"] == "ci"
    with pytest.raises(e.EvidenceError, match="untrusted"):
        e.verify_attestation(record, {})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(record, {"ci": b"x" * 32})
    with pytest.raises(e.EvidenceError, match="32 bytes"):
        e.verify_attestation(record, {"ci": b"short"})


@pytest.mark.parametrize(
    "field",
    [
        "task_sha256",
        "context_sha256",
        "environment_fingerprint",
        "evidence_sha256",
        "program_sha256",
    ],
)
def test_rehashing_modified_claim_does_not_forge_signature(assembly, field):
    record = attestation(assembly)
    record["payload"][field] = "9" * 64
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(record, TRUST)


def test_failed_signed_observation_cannot_be_accepted(assembly):
    record = attestation(assembly)
    record["payload"]["status"] = "failed"
    record["signature"] = e._signature(record["payload"], KEY)
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="passing"):
        e.verify_attestation(record, TRUST)


def test_store_immutable_authenticated_and_policy_specific(assembly, tmp_path):
    store = e.EvidenceStore(tmp_path / "evidence.sqlite")
    record = attestation(assembly)
    assert store.add(record, TRUST) == store.add(record, TRUST)
    match = store.match(assembly, {"tasks": [TASK]}, TRUST)
    assert match["accepted"] and match["observations"] == [record]
    assert not store.match(assembly, {"tasks": [TASK, "f" * 64]}, TRUST)["accepted"]
    assert not store.match(assembly, {"tasks": [TASK], "evaluators": ["other"]}, TRUST)[
        "accepted"
    ]
    revoked = store.match(assembly, {"tasks": [TASK]}, {})
    assert not revoked["accepted"] and "untrusted" in revoked["rejections"][0]["reason"]
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM observations")
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert KEY not in store.path.read_bytes()


def test_store_revalidates_untrusted_database_rows(assembly, tmp_path):
    store = e.EvidenceStore(tmp_path / "e.sqlite")
    record = attestation(assembly)
    record["signature"] = "0" * 64
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO observations VALUES(?,?,?,?)",
            (
                record["sha256"],
                e.context_fingerprint(assembly),
                TASK,
                json.dumps(record),
            ),
        )
    match = store.match(assembly, {"tasks": [TASK]}, TRUST)
    assert not match["accepted"] and "signature" in match["rejections"][0]["reason"]


def test_environment_is_exact_and_independent_of_receipt(assembly):
    environment = {
        "assembly": assembly,
        "interpreter": {"python": "3.14"},
        "wheels": [{"sha256": "a" * 64}],
        "runtime": {"sha256": "b" * 64},
    }
    fingerprint = e.environment_fingerprint(environment)
    assembly["evidence"] = {
        "policy": {"tasks": [TASK]},
        "context_sha256": e.context_fingerprint(assembly),
        "observations": [attestation(assembly, environment=fingerprint)],
    }
    assert e.environment_fingerprint(environment) == fingerprint
    e.verify_environment_evidence(assembly, fingerprint)
    environment["wheels"][0]["sha256"] = "f" * 64
    with pytest.raises(e.EvidenceError, match="environment"):
        e.verify_environment_evidence(assembly, e.environment_fingerprint(environment))


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"tasks": []},
        {"tasks": ["not-hash"]},
        {"tasks": [TASK], "unknown": 1},
        {"tasks": [TASK], "evaluators": []},
    ],
)
def test_invalid_policy(policy):
    with pytest.raises(ValueError):
        read_goal(goal(evidence=policy))


def test_synthesis_selects_tested_whole_constructor_context(tmp_path, repo):
    for family in ["alpha", "beta"]:
        publish(
            tmp_path,
            repo,
            family,
            [
                provider(
                    package=family, capabilities=["client"], effects=["local-state"]
                )
            ],
            "def run(value):\n    return value\n",
        )
    publish(tmp_path, repo, "retry", retry_members(), RETRY_SOURCE)
    request = goal("retry", policy={"allowed_effects": ["local-state"]})
    original = synthesize(request, repo)
    assert original["status"] == "ambiguous"
    assembly = lock_assembly(original, repo, choice=0)
    store = e.EvidenceStore(tmp_path / "e.sqlite")
    store.add(attestation(assembly), TRUST)
    request["evidence"] = {"tasks": [TASK], "evaluators": ["ci"]}
    selected = synthesize(request, repo, evidence_store=store, trust_keys=TRUST)
    assert selected["status"] == "unique"
    assert (
        selected["solutions"][0]["expression"] == original["solutions"][0]["expression"]
    )
    assert any(r["code"] == "evidence-unsatisfied" for r in selected["rejections"])
    assert synthesize(request, repo, evidence_store=store, trust_keys=TRUST) == selected
    with pytest.raises(AssemblyError, match="explicit evaluator"):
        lock_assembly(selected, repo)
    locked = lock_assembly(selected, repo, trust_keys=TRUST)
    assert verify_assembly(locked, trust_keys=TRUST)["evidence"]["authenticated"]
    assert verify_assembly(locked)["evidence"]["authenticated"] is False
    with pytest.raises(AssemblyError, match="untrusted"):
        verify_assembly(locked, trust_keys={})
    tampered = copy.deepcopy(locked)
    tampered.pop("evidence")
    tampered["sha256"] = e._digest({k: v for k, v in tampered.items() if k != "sha256"})
    with pytest.raises(AssemblyError, match="policy and receipt"):
        verify_assembly(tampered)


def test_synthesis_fails_closed_without_trust(tmp_path, repo):
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    request = goal(evidence={"tasks": [TASK]})
    result = synthesize(request, repo)
    assert result["status"] == "unsatisfied"
    assert result["rejections"][-1]["code"] == "missing-evidence-trust"


def test_ingest_verifies_task_cases_environment_before_signing(tmp_path, monkeypatch):
    source = tmp_path / "contribution.toml"
    source.write_text(SOURCE)
    bundle = c.prepare_contribution(
        source, Mock(interface=Mock(return_value=SPEC)), tmp_path
    )
    task_path = tmp_path / "task.json"
    card = {
        "family": "kb",
        "id": "search",
        "version": "1",
        "sha256": "a" * 64,
        "provides": {"id": "kb.search", "version": "1"},
        "requires": {},
        "effects": ["storage"],
        "capabilities": ["citations"],
    }
    lock = {
        "sha256": "b" * 64,
        "assembly": {
            "sha256": "c" * 64,
            "expression": {"use": "search"},
            "bindings": {
                "search": {"member": card, "artifacts": [], "external_requirements": []}
            },
            "effects": ["storage"],
            "interfaces": [SPEC],
        },
        "interpreter": {},
        "wheels": [],
        "runtime": {},
    }
    verifier = Mock(return_value=(lock, None))
    monkeypatch.setattr(c, "verify_environment", verifier)
    monkeypatch.setattr(environments, "verify_environment", verifier)
    report = {
        "format": "module-families-contribution-evidence-1",
        "task_sha256": bundle["sha256"],
        "environment_sha256": lock["sha256"],
        "program_sha256": lock["assembly"]["sha256"],
        "timeout_seconds": 10,
        "trust": "unsigned-local-observation",
        "status": "passed",
        "cases": [
            {
                "id": "find-document",
                "status": "passed",
                "actual": [{"document": "policy-1", "page": 2}],
            }
        ],
    }
    report["sha256"] = e._digest(report)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report))
    store = e.EvidenceStore(tmp_path / "e.sqlite")
    record = e.ingest_evidence(task_path, tmp_path / "env.json", path, store, "ci", KEY)
    assert e.verify_attestation(record, TRUST)["evidence_sha256"] == report["sha256"]
    report["cases"][0]["actual"] = []
    report["sha256"] = e._digest({k: v for k, v in report.items() if k != "sha256"})
    path.write_text(json.dumps(report))
    with pytest.raises(c.ContributionError, match="unaccepted case"):
        e.ingest_evidence(task_path, tmp_path / "env.json", path, store, "ci", KEY)


def test_required_tasks_need_a_common_tested_environment(assembly, tmp_path):
    store = e.EvidenceStore(tmp_path / "e.sqlite")
    store.add(attestation(assembly), TRUST)
    store.add(attestation(assembly, task="f" * 64, environment="9" * 64), TRUST)
    policy = {"tasks": [TASK, "f" * 64]}
    match = store.match(assembly, policy, TRUST)
    assert not match["accepted"] and not match["missing_tasks"]
    assert "common tested" in match["rejections"][0]["reason"]
    store.add(attestation(assembly, task="f" * 64), TRUST)
    assert store.match(assembly, policy, TRUST)["accepted"]


def test_stale_root_and_child_artifacts_do_not_reuse_observations(assembly, tmp_path):
    store = e.EvidenceStore(tmp_path / "e.sqlite")
    store.add(attestation(assembly), TRUST)
    for alias in ["a", "b"]:
        altered = copy.deepcopy(assembly)
        altered["bindings"][alias]["member"]["sha256"] = "f" * 64
        assert not store.match(altered, {"tasks": [TASK]}, TRUST)["accepted"]


def test_receipt_cannot_change_tasks_or_child_context(assembly):
    receipt = {
        "policy": {"tasks": [TASK]},
        "context_sha256": e.context_fingerprint(assembly),
        "observations": [attestation(assembly)],
    }
    with pytest.raises(e.EvidenceError, match="policy"):
        e.verify_receipt(assembly, {"tasks": ["f" * 64]}, receipt, trust_keys=TRUST)
    altered = copy.deepcopy(assembly)
    altered["bindings"]["b"]["member"]["sha256"] = "f" * 64
    with pytest.raises(e.EvidenceError, match="composition"):
        e.verify_receipt(altered, receipt["policy"], receipt, trust_keys=TRUST)


def test_signature_and_receipt_unknown_fields_are_rejected(assembly):
    record = attestation(assembly)
    record["unexpected"] = "value"
    with pytest.raises(e.EvidenceError, match="attestation"):
        e.verify_attestation(record, TRUST)
    record = attestation(assembly)
    record["payload"]["unexpected"] = "value"
    with pytest.raises(e.EvidenceError, match="payload"):
        e.verify_attestation(record, TRUST)


def test_exact_candidate_root_still_allows_same_interface_children(tmp_path, repo):
    publish(
        tmp_path, repo, "alpha", [provider()], "def run(value):\n    return value\n"
    )
    publish(tmp_path, repo, "retry", retry_members(), RETRY_SOURCE)
    root = repo.inspect("retry", "tests.run")
    request = goal()
    request["goal"]["root"] = {
        k: root[k] for k in ("family", "id", "version", "sha256")
    }
    result = synthesize(request, repo)
    assert result["status"] == "unique"
    expression = result["solutions"][0]["expression"]
    cards = result["solutions"][0]["candidates"]
    assert cards[expression["use"]]["family"] == "retry"
    assert cards[expression["with"]["client"]["use"]]["family"] == "alpha"
    locked = lock_assembly(result, repo)
    assert locked["synthesis"]["request"]["goal"]["root"] == request["goal"]["root"]
    changed = copy.deepcopy(result)
    changed["request"]["goal"]["root"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="root"):
        lock_assembly(changed, repo)


@pytest.mark.parametrize(
    "root",
    [
        {},
        {"family": "f", "id": "m", "version": "1", "sha256": "short"},
        {"family": "f", "id": "m", "version": "invalid!", "sha256": TASK},
    ],
)
def test_invalid_exact_candidate_root(root):
    request = goal()
    request["goal"]["root"] = root
    with pytest.raises(ValueError, match="root"):
        read_goal(request)
