"""Authenticated, context-specific acceptance observations.

Ed25519 supports public verification; HMAC supports shared-secret trust domains.
Neither proves that Python code behaves correctly. Signing is an evaluator
operation: callers must only attest observations they accept.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path

from .registry import canonical_bytes
from .signing import PrivateEvaluatorKey, PublicEvaluatorKey


class EvidenceError(ValueError):
    """An observation, trust policy, or composition binding is invalid."""


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) - {"tasks", "evaluators"}:
        raise EvidenceError("evidence policy declares tasks and optional evaluators")
    tasks = policy.get("tasks")
    if not isinstance(tasks, list) or not tasks or any(not _hash(t) for t in tasks):
        raise EvidenceError("evidence.tasks must contain exact SHA256 task hashes")
    result = {"tasks": sorted(set(tasks))}
    if "evaluators" in policy:
        evaluators = policy["evaluators"]
        if (
            not isinstance(evaluators, list)
            or not evaluators
            or any(
                not isinstance(e, str) or not e.strip() or e != e.strip()
                for e in evaluators
            )
        ):
            raise EvidenceError("evidence.evaluators must contain evaluator IDs")
        result["evaluators"] = sorted(set(evaluators))
    return result


def context_fingerprint(assembly):
    """Bind the complete module expression, declarations, and artifact closures.

    Local binding aliases and project names do not affect this identity. Python
    interpreter/third-party wheel identities are retained separately on evidence;
    an unmaterialized synthesis result does not determine them.
    """
    bindings = assembly["bindings"]
    normalized = {}
    for alias, binding in bindings.items():
        normalized[alias] = {
            "member": binding["member"],
            "artifacts": sorted(binding["artifacts"], key=canonical_bytes),
            "external_requirements": sorted(binding["external_requirements"]),
        }

    def expression(node):
        if not isinstance(node, dict) or set(node) - {"use", "with"}:
            raise EvidenceError("invalid evidence expression")
        return {
            "binding": normalized[node["use"]],
            "with": {
                name: expression(child) for name, child in node.get("with", {}).items()
            },
        }

    return _digest(
        {
            "format": "module-families-evidence-context-1",
            "expression": expression(assembly["expression"]),
            "bindings": sorted(normalized.values(), key=canonical_bytes),
            "interfaces": sorted(assembly["interfaces"], key=canonical_bytes),
            "type_libraries": sorted(
                [normalized[alias] for alias in assembly.get("type_libraries", [])],
                key=canonical_bytes,
            ),
        }
    )


def _secret(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise EvidenceError("evaluator key must contain at least 32 bytes")
    return secret


def _signature(payload, secret):
    if isinstance(secret, PrivateEvaluatorKey):
        return secret.sign(canonical_bytes(payload)).hex()
    return hmac.new(
        _secret(secret), canonical_bytes(payload), hashlib.sha256
    ).hexdigest()


def _attestation_payload(record):
    if not isinstance(record, dict) or set(record) != {
        "payload",
        "signature",
        "sha256",
    }:
        raise EvidenceError("invalid evidence attestation")
    payload = record["payload"]
    fields = {
        "format",
        "evaluator",
        "task_sha256",
        "environment_sha256",
        "program_sha256",
        "evidence_sha256",
        "context_sha256",
        "environment_fingerprint",
        "status",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise EvidenceError("invalid attestation payload")
    if (
        payload["format"]
        not in {
            "module-families-evaluator-attestation-1",
            "module-families-ed25519-attestation-1",
        }
        or payload["status"] != "passed"
    ):
        raise EvidenceError("only passing evaluator attestations are accepted")
    if any(not _hash(payload[key]) for key in fields if key.endswith("sha256")):
        raise EvidenceError("invalid attestation digest")
    evaluator = payload["evaluator"]
    if (
        not isinstance(evaluator, str)
        or not evaluator.strip()
        or evaluator != evaluator.strip()
    ):
        raise EvidenceError("invalid evaluator identity")
    if not _hash(payload["environment_fingerprint"]):
        raise EvidenceError("invalid environment fingerprint")
    signature = record["signature"]
    size = 128 if payload["format"] == "module-families-ed25519-attestation-1" else 64
    if (
        not isinstance(signature, str)
        or re.fullmatch(r"[0-9a-f]{" + str(size) + "}", signature) is None
    ):
        raise EvidenceError("invalid evaluator signature")
    if record["sha256"] != _digest({"payload": payload, "signature": signature}):
        raise EvidenceError("attestation hash mismatch")
    return payload


def verify_attestation(record, trust_keys):
    payload = _attestation_payload(record)
    evaluator = payload["evaluator"]
    if evaluator not in trust_keys:
        raise EvidenceError(f"untrusted evaluator: {evaluator}")
    key = trust_keys[evaluator]
    if payload["format"] == "module-families-ed25519-attestation-1":
        if isinstance(key, PrivateEvaluatorKey):
            key = key.public_key()
        if not isinstance(key, PublicEvaluatorKey):
            raise EvidenceError("Ed25519 attestation requires an explicit public key")
        try:
            key.verify(bytes.fromhex(record["signature"]), canonical_bytes(payload))
        except ValueError as error:
            raise EvidenceError(str(error)) from error
    elif not isinstance(key, bytes) or not hmac.compare_digest(
        record["signature"], _signature(payload, key)
    ):
        raise EvidenceError("evaluator signature mismatch")
    return payload


def environment_fingerprint(lock):
    """Exact executable environment, independent of assembly receipt metadata."""
    return _digest(
        {
            "interpreter": lock["interpreter"],
            "wheels": sorted(lock["wheels"], key=canonical_bytes),
            "runtime": lock["runtime"],
        }
    )


def verify_receipt(assembly, policy, receipt, *, trust_keys=None):
    """Check immutable coverage; authenticate only against explicit local keys."""
    policy = validate_policy(policy)
    if not isinstance(receipt, dict) or set(receipt) != {
        "policy",
        "context_sha256",
        "observations",
    }:
        raise EvidenceError("invalid evidence receipt")
    context = context_fingerprint(assembly)
    if receipt["policy"] != policy or receipt["context_sha256"] != context:
        raise EvidenceError("evidence receipt policy or composition mismatch")
    if not isinstance(receipt["observations"], list) or not receipt["observations"]:
        raise EvidenceError("evidence receipt has no observations")
    covered = set()
    for record in receipt["observations"]:
        payload = (
            _attestation_payload(record)
            if trust_keys is None
            else verify_attestation(record, trust_keys)
        )
        if payload["context_sha256"] != context:
            raise EvidenceError("attestation belongs to another composition")
        if payload["task_sha256"] not in policy["tasks"]:
            raise EvidenceError("attestation covers an unrequested task")
        if "evaluators" in policy and payload["evaluator"] not in policy["evaluators"]:
            raise EvidenceError("evaluator is outside goal policy")
        covered.add(payload["task_sha256"])
    if covered != set(policy["tasks"]):
        raise EvidenceError("evidence receipt omits required tasks")
    return {"authenticated": trust_keys is not None, "tasks": sorted(covered)}


def verify_environment_evidence(assembly, fingerprint):
    """Require all requested tasks to have observations in this exact environment."""
    if "evidence" not in assembly:
        return
    receipt = assembly["evidence"]
    verify_receipt(assembly, receipt["policy"], receipt)
    covered = {
        record["payload"]["task_sha256"]
        for record in receipt["observations"]
        if record["payload"]["environment_fingerprint"] == fingerprint
    }
    if covered != set(receipt["policy"]["tasks"]):
        raise EvidenceError(
            "selected Python environment lacks matching trusted evaluation evidence"
        )


class EvidenceStore:
    """SQLite observations, keyed immutably and reauthenticated on every read."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS observations (digest TEXT PRIMARY KEY, context TEXT NOT NULL, task TEXT NOT NULL, record TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS observation_lookup ON observations(context, task)"
            )
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, 'immutable observation'); END"
            )
            db.execute(
                "CREATE TRIGGER IF NOT EXISTS observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT, 'immutable observation'); END"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def add(self, record, trust_keys):
        payload = verify_attestation(record, trust_keys)
        encoded = canonical_bytes(record).decode()
        with self._connect() as db:
            existing = db.execute(
                "SELECT record FROM observations WHERE digest=?", (record["sha256"],)
            ).fetchone()
            if existing is not None and existing[0] != encoded:
                raise EvidenceError("immutable observation collision")
            db.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?)",
                (
                    record["sha256"],
                    payload["context_sha256"],
                    payload["task_sha256"],
                    encoded,
                ),
            )
        return record["sha256"]

    def match(self, assembly, policy, trust_keys):
        policy = validate_policy(policy)
        context = context_fingerprint(assembly)
        accepted, missing, rejected = [], [], []
        for task in policy["tasks"]:
            matches = []
            with self._connect() as db:
                rows = db.execute(
                    "SELECT record FROM observations WHERE context=? AND task=? ORDER BY digest",
                    (context, task),
                ).fetchall()
            for row in rows:
                try:
                    record = json.loads(row[0])
                    payload = verify_attestation(record, trust_keys)
                    if (
                        payload["context_sha256"] != context
                        or payload["task_sha256"] != task
                    ):
                        raise EvidenceError(
                            "observation index differs from signed context"
                        )
                    if (
                        "evaluators" in policy
                        and payload["evaluator"] not in policy["evaluators"]
                    ):
                        raise EvidenceError("evaluator is outside goal policy")
                    matches.append(record)
                except (ValueError, KeyError, TypeError) as error:
                    rejected.append({"task_sha256": task, "reason": str(error)})
            if matches:
                accepted.extend(matches)
            else:
                missing.append(task)
        environments = None
        for task in policy["tasks"]:
            observed = {
                record["payload"]["environment_fingerprint"]
                for record in accepted
                if record["payload"]["task_sha256"] == task
            }
            environments = observed if environments is None else environments & observed
        if not missing and not environments:
            rejected.append(
                {"reason": "required tasks have no common tested Python environment"}
            )
        return {
            "accepted": not missing and bool(environments),
            "context_sha256": context,
            "observations": accepted,
            "missing_tasks": missing,
            "rejections": rejected,
        }


def ingest_evidence(
    task_path, environment_lock, evidence_path, store, evaluator_id, secret
):
    """Attest a verified report as a trusted evaluator and store it.

    Validation checks consistency, not execution authenticity. The holder of the
    evaluator key must control evaluation or otherwise trust the submitted report.
    Never expose this signing operation as an unauthenticated upload endpoint.
    """
    from .contributions import verify_evidence
    from .environments import verify_environment

    report = verify_evidence(task_path, environment_lock, evidence_path)
    lock, _ = verify_environment(environment_lock)
    if (
        lock["sha256"] != report["environment_sha256"]
        or lock["assembly"]["sha256"] != report["program_sha256"]
    ):
        raise EvidenceError("evaluation environment changed during attestation")
    payload = {
        "format": (
            "module-families-ed25519-attestation-1"
            if isinstance(secret, PrivateEvaluatorKey)
            else "module-families-evaluator-attestation-1"
        ),
        "evaluator": evaluator_id,
        "task_sha256": report["task_sha256"],
        "environment_sha256": report["environment_sha256"],
        "program_sha256": report["program_sha256"],
        "evidence_sha256": report["sha256"],
        "context_sha256": context_fingerprint(lock["assembly"]),
        "environment_fingerprint": environment_fingerprint(lock),
        "status": "passed",
    }
    record = {"payload": payload, "signature": _signature(payload, secret)}
    record["sha256"] = _digest(record)
    store.add(record, {evaluator_id: secret})
    return record
