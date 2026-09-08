"""An actual coding agent composes existing Mari/sklearn/SQLite modules in TOML."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.contributions import prepare_contribution
from module_families.coordination import CoordinationQueue
from module_families.evidence import EvidenceStore
from module_families.module_build import build_module
from module_families.registry import Registry
from module_families.synthesis import synthesize
from module_families.workers import evaluate_submission, run_worker_once

ROOT = Path(__file__).resolve().parents[1]


def run(work, wheelhouse):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    base, staging = Registry(work / "accepted"), Registry(work / "staging")
    for name in ("interfaces", "mari", "adapters"):
        build_family(ROOT / "families/knowledge" / (name + ".toml"), work / "base" / name)
        base.publish(work / "base" / name / "index.json")
    retrieval = build_module(ROOT / "examples/modules/retrieval.toml", base, work / "base/retrieval")
    base.publish(retrieval["index"])
    payload = prepare_contribution(ROOT / "examples/contributions/knowledge.toml", base, work / "task")
    candidates, interfaces = [], []
    for identifier in ("embedding", "documents", "retrieval", "batch", "system"):
        reference = "knowledge." + identifier
        interfaces.append(base.interface(reference, "1"))
        candidates.extend(base.candidates(reference, "1", limit=20))
    guide = work / "composition-guide.txt"
    guide.write_text('''Create a graph TOML, with no new Python implementation. Compose the supplied
accepted providers to satisfy the task. The build system handles Python wiring.
Use the existing open retrieval subsystem if it satisfies your needs. Include
caching if required by capabilities. Ingestion and retrieval must share the same
data instance; independent stores would lose ingested documents.
Graph grammar:
schema_version=1
[family]
name="knowledge"
version="1.0.0"
description="Composable knowledge retrieval and execution modules"
[publisher]
name="agent-integrator"
[module]
id="system"
version="1.0.0"
provides={id="knowledge.system",version="1"}
summary="Describe your composition"
capabilities=["copy required task capabilities"]
[nodes.NAME.select]
family="knowledge"
member="publisher.member_id from supplied cards"
[links]
"node.requirement_slot"="other_node"
[constraints]
same_instance=[["node_a.data","node_b.data"]]
[exports]
run="node.export_name"
close="node.export_name"
[policy]
allowed_effects=["copy task effect policy"]
Optional [indices] Space="embedding_node.Space" exports semantic metadata.
Each node's named requirements must be linked. Export every callable from the
required result interface. Candidate card id already includes publisher prefix.
Return {"kind":"graph","manifest":"system.toml"} after writing it.
Accepted candidate cards and interfaces follow (not a whole source checkout):
''' + json.dumps({"candidates": candidates, "interfaces": interfaces}, indent=2))
    queue = CoordinationQueue(work / "queue.sqlite")
    queue.enqueue(payload, task_id="knowledge-system", max_attempts=3)
    store, secret = EvidenceStore(work / "evidence.sqlite"), secrets.token_bytes(32)
    attempts = []
    for _ in range(3):
        authored = run_worker_once(
            queue, base, staging, worker="codex-integrator", work_root=work / "workers",
            driver=[sys.executable, str(ROOT / "scripts/codex_contribution_driver.py"),
                    "{task}", "{proposal}", "--guide", str(guide), "--feedback", "{feedback}"],
            timeout=600,
        )
        accepted = evaluate_submission(
            queue, "knowledge-system", base, staging, work_root=work / "evaluations",
            evidence_store=store, evaluator_id="knowledge-ci", secret=secret,
            find_links=[str(Path(wheelhouse).resolve())] if wheelhouse else [], no_index=bool(wheelhouse),
        )
        attempts.append({"worker": authored["status"], "evaluation": accepted["status"]})
        if accepted["status"] == "accepted":
            break
    if accepted["status"] != "accepted":
        raise RuntimeError(f"agent composition failed after {len(attempts)} attempts; inspect {work}")
    goal = {"schema_version": 1, "goal": {
        "name": "agent-composed-knowledge", "requires": payload["document"]["task"]["requires"],
        "capabilities": payload["document"]["task"]["capabilities"],
    }, "policy": payload["document"]["policy"],
        "evidence": {"tasks": [payload["sha256"]], "evaluators": ["knowledge-ci"]}}
    keys = {"knowledge-ci": secret}
    resolved = synthesize(goal, base, evidence_store=store, trust_keys=keys)
    locked = lock_assembly(resolved, base, trust_keys=keys)
    (work / "program.lock.json").write_text(json.dumps(locked, indent=2))
    report = {"actual_agent_invocations": len(attempts), "attempts": attempts,
              "synthesis": resolved["status"], "assembly_sha256": locked["sha256"],
              "attestation": accepted["attestation"], "candidate_cards_shared": len(candidates),
              "guide_bytes": guide.stat().st_size, "new_python_implementation": False}
    (work / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--wheelhouse")
    args = parser.parse_args()
    run(args.work_dir, args.wheelhouse)
