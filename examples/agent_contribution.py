"""A real coding agent adapts textwrap to a knowledge-ingestion contract."""
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
from module_families.manifest import write_manifest
from module_families.registry import Registry
from module_families.synthesis import synthesize
from module_families.workers import evaluate_submission, run_worker_once

ROOT = Path(__file__).resolve().parents[1]


def run(work, wheelhouse):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    base, staging = Registry(work / "accepted"), Registry(work / "staging")
    reference = {"id": "knowledge.chunker", "version": "1"}
    manifest = write_manifest({
        "schema_version": 1, "name": "knowledge-contracts", "version": "1.0.0",
        "description": "Knowledge ingestion contracts", "publisher": "contracts",
        "context": {}, "members": [], "interfaces": [{
            **reference, "types": [], "callables": {"run": {
                "parameters": [{"name": name, "kind": "POSITIONAL_OR_KEYWORD", "required": True}
                               for name in ("documents", "width")], "asynchronous": False,
            }},
        }],
    }, work / "interfaces.toml")
    build_family(manifest, work / "interfaces")
    base.publish(work / "interfaces/index.json")
    task = work / "task.toml"
    task.write_text('''schema_version=1
[task]
id="source-preserving-chunker"
summary="Adapt Python textwrap.wrap to chunk document text at the given positive integer width. Input documents are a list of objects with id and text. Return a flat list in input list order, then chunk order; each item has document_id, ordinal (zero-based per document), and text. Preserve source IDs. Use textwrap.wrap defaults, including long-word splitting and whitespace normalization. Empty documents yield no chunks."
requires={id="knowledge.chunker",version="1"}
capabilities=["source-preserving-chunking"]
[policy]
allowed_effects=[]
[[cases]]
id="source-identities"
export="run"
args=[[{id="manual",text="alpha beta gamma"},{id="faq",text="one two"}],10]
expected=[{document_id="manual",ordinal=0,text="alpha beta"},{document_id="manual",ordinal=1,text="gamma"},{document_id="faq",ordinal=0,text="one two"}]
[[cases]]
id="empty"
export="run"
args=[[{id="empty",text=""}],10]
expected=[]
[[cases]]
id="long-word"
export="run"
args=[[{id="guide",text="abcdefgh"}],3]
expected=[{document_id="guide",ordinal=0,text="abc"},{document_id="guide",ordinal=1,text="def"},{document_id="guide",ordinal=2,text="gh"}]
''')
    payload = prepare_contribution(task, base, work / "task")
    queue = CoordinationQueue(work / "queue.sqlite")
    queue.enqueue(payload, task_id="chunker")
    result = run_worker_once(
        queue, base, staging, worker="codex", work_root=work / "workers",
        driver=[sys.executable, str(ROOT / "scripts/codex_contribution_driver.py"), "{task}", "{proposal}"],
        timeout=600,
    )
    (work / "worker.json").write_text(json.dumps(result, indent=2))
    store = EvidenceStore(work / "evidence.sqlite")
    secret = secrets.token_bytes(32)
    accepted = evaluate_submission(
        queue, "chunker", base, staging, work_root=work / "evaluations",
        evidence_store=store, evaluator_id="local-evaluator", secret=secret,
        find_links=[str(Path(wheelhouse).resolve())] if wheelhouse else [],
        no_index=bool(wheelhouse),
    )
    if accepted["status"] != "accepted":
        raise RuntimeError(f"contribution was {accepted['status']}; inspect {work}")
    goal = {"schema_version": 1, "goal": {
        "name": "knowledge-ingestion", "requires": reference,
        "capabilities": ["source-preserving-chunking"],
    }, "policy": {"allowed_effects": []},
        "evidence": {"tasks": [payload["sha256"]], "evaluators": ["local-evaluator"]}}
    keys = {"local-evaluator": secret}
    resolved = synthesize(goal, base, evidence_store=store, trust_keys=keys)
    lock = lock_assembly(resolved, base, trust_keys=keys)
    (work / "program.lock.json").write_text(json.dumps(lock, indent=2))
    report = {"actual_coding_agents": 1, "worker_status": result["status"],
              "admission_status": accepted["status"], "synthesis_status": resolved["status"],
              "task_sha256": payload["sha256"], "assembly_sha256": lock["sha256"],
              "attestation": accepted["attestation"], "work": str(work)}
    (work / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--wheelhouse")
    args = parser.parse_args()
    run(args.work_dir, args.wheelhouse)
