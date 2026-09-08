"""Compose existing knowledge implementations with associated type witnesses."""
from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from copy import deepcopy
from pathlib import Path

from knowledge_system import DOCUMENTS, QUERIES

from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.manifest import write_manifest
from module_families.module_build import build_module, resolve_module
from module_families.registry import Registry
from module_families.synthesis import synthesize

ROOT = Path(__file__).resolve().parents[1]
CONSTRUCTORS = {"knowledge.VectorBatch": {"parameters": ["identity"], "result": "shared"}}
SPACE = {"nominal": "sklearn-1.8.0.hashing.n1024.unsigned.l2.default-tokenizer", "kind": "identity"}
DOCUMENT_ID = {"nominal": "associated-knowledge.documents.primary", "kind": "shared"}


def path(name, kind="identity"):
    return {"from": name, "kind": kind}


def rename(value):
    if isinstance(value, dict):
        return {key: rename(item) for key, item in value.items()}
    if isinstance(value, list):
        return [rename(item) for item in value]
    if isinstance(value, str):
        if value == "knowledge":
            return "associated-knowledge"
        if value.startswith("knowledge."):
            return "associated." + value
    return value


def read(relative):
    source = ROOT / relative
    doc = rename(tomllib.loads(source.read_text()))
    if "source" in doc:
        doc["source"]["root"] = str((source.parent / doc["source"]["root"]).resolve())
        doc["source"]["license_files"] = [str((source.parent / item).resolve()) for item in doc["source"].get("license_files", [])]
    return doc


def declarations():
    interfaces = read("families/knowledge/interfaces.toml")
    kinds = {
        "embedding": {"Space": "identity"}, "documents": {"DocumentId": "shared"},
        "retrieval": {"Space": "identity", "DocumentId": "shared"},
        "batch": {"DocumentId": "shared"}, "system": {"DocumentId": "shared"},
    }
    for spec in interfaces["interfaces"]:
        suffix = spec["id"].rsplit(".", 1)[1]
        spec["associated"] = {"types": kinds.get(suffix, {}), "constructors": CONSTRUCTORS}
        if suffix == "embedding":
            spec["typing"] = {
                "types": {
                    "Texts": {"id": "knowledge.text-batch", "usage": "shared", "representation": "opaque"},
                    "Vectors": {"term": {"apply": "knowledge.VectorBatch", "args": [{"var": "Space", "kind": "identity"}], "kind": "shared"}, "usage": "shared", "representation": "opaque"},
                },
                "operations": {"embed": {"parameters": {"texts": {"type": "Texts", "mode": "share"}}, "returns": ["Vectors"], "effects": ["memory"]}},
            }
    adapters = read("families/knowledge/adapters.toml")
    witnesses = {
        "hashing": {"Space": SPACE}, "cached_embedding": {"Space": path("base.Space")},
        "sqlite_documents": {"DocumentId": DOCUMENT_ID},
        "retrieval": {"Space": path("embedding.Space"), "DocumentId": path("data.DocumentId", "shared")},
        "threaded_queries": {"DocumentId": path("retrieval.DocumentId", "shared")},
    }
    for member in adapters["members"]:
        member["associated"] = {"types": witnesses[member["id"]], "constructors": CONSTRUCTORS}
        if member["id"] == "threaded_queries":
            member["associated"]["sharing"] = [[path("retrieval.DocumentId", "shared"), path("data.DocumentId", "shared")]]
    # These deliberately contradictory declarations exercise diagnostics. They
    # reuse the same Python adapters and are not alternate embedding algorithms.
    for original, identifier, name, witness in (
        ("hashing", "contradictory_hashing", "Space", {"nominal": "fixture.contradictory-space", "kind": "identity"}),
        ("sqlite_documents", "other_documents", "DocumentId", {"nominal": "fixture.other-document-namespace", "kind": "shared"}),
    ):
        member = deepcopy(next(item for item in adapters["members"] if item["id"] == original))
        member["id"] = identifier
        member["summary"] = "Contradictory witness fixture using unchanged " + original + " implementation"
        member["associated"]["types"][name] = witness
        adapters["members"].append(member)
    return {"interfaces": interfaces, "mari": read("families/knowledge/mari.toml"), "adapters": adapters}


def run(work: Path, wheelhouse: Path | None = None):
    work = work.resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    repository = Registry(work / "repository")
    original = ROOT / "projects/mari-kit/src/mark_kit/retrieval/fusion.py"
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    for name, document in declarations().items():
        manifest = write_manifest(document, work / "manifests" / (name + ".toml"))
        out = work / "build" / name
        build_family(manifest, out)
        repository.publish(out / "index.json")
    opened = read("examples/modules/retrieval.toml")
    opened["associated"] = {"types": {"Space": path("embedding.Space"), "DocumentId": path("data.DocumentId", "shared")}, "constructors": CONSTRUCTORS}
    closed = read("examples/modules/knowledge.toml")
    closed["associated"] = {"types": {"DocumentId": path("data.DocumentId", "shared")}, "constructors": CONSTRUCTORS}
    closed["constraints"]["same_associated"] = [["embedding.Space", "retrieval.Space"]]
    builds = []
    for name, document in (("retrieval", opened), ("knowledge", closed)):
        manifest = write_manifest(document, work / "manifests" / (name + ".toml"))
        out = work / "build" / name
        builds.append(build_module(manifest, repository, out))
        repository.publish(out / "index.json")

    # A typed projection must substitute the provider's actual Space into its
    # VectorBatch result. Same call shape cannot justify a different witness.
    projected = {
        "schema_version": 1, "family": deepcopy(closed["family"]), "publisher": {"name": "checks"},
        "module": {"id": "embedding", "version": "1.0.0", "provides": {"id": "associated.knowledge.embedding", "version": "1"}},
        "nodes": {"embedding": {"select": {"family": "associated-knowledge", "member": "adapters.hashing"}}},
        "exports": {"embed": "embedding.embed"},
        "associated": {"types": {"Space": SPACE}, "constructors": CONSTRUCTORS},
    }
    assert resolve_module(projected, repository)["status"] == "unique"
    projected["nodes"]["embedding"]["select"]["member"] = "adapters.contradictory_hashing"
    write_manifest(projected, work / "manifests/rejected-vector-space.toml")
    vector_rejection = resolve_module(projected, repository)
    assert vector_rejection["status"] == "unsatisfied"
    assert "typed export contract mismatch" in str(vector_rejection)
    miswired = deepcopy(closed)
    del miswired["constraints"]["same_instance"]
    miswired["nodes"]["other_data"] = {"select": {"family": "associated-knowledge", "member": "adapters.other_documents"}}
    miswired["links"]["harness.data"] = "other_data"
    write_manifest(miswired, work / "manifests/rejected-document-identity.toml")
    document_rejection = resolve_module(miswired, repository)
    assert document_rejection["status"] == "unsatisfied"
    assert "harness sharing" in str(document_rejection)

    goal = read("examples/goals/knowledge.toml")
    write_manifest(goal, work / "manifests/goal.toml")
    resolution = synthesize(goal, repository)
    assert resolution["status"] == "unique"
    assembly = lock_assembly(resolution, repository)
    (work / "program.lock.json").write_text(json.dumps(assembly, indent=2) + "\n")
    environment = lock_environment(assembly, repository, work / "environment",
                                   find_links=[str(wheelhouse.resolve()), str(ROOT / ".mf/upstream-wheels")] if wheelhouse else [],
                                   no_index=wheelhouse is not None)
    sync_environment(environment["lock"], work / "venv")
    replay = run_environment(environment["lock"], work / "venv", export="run", args=[DOCUMENTS, QUERIES, 2])
    assert [hits[0]["id"] for hits in replay["result"]] == ["dogs", "python"]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    report = {
        "builds": builds, "synthesis_status": resolution["status"], "program_sha256": assembly["sha256"],
        "mari_source_sha256": source_hash, "mari_source_unchanged": True, "environment": environment, "replay": replay,
        "rejections": {"vector_space": vector_rejection["rejections"], "document_identity": document_rejection["rejections"]},
        "limits": [
            "Associated witnesses are publisher declarations; Python numerical behavior is trusted.",
            "Negative fixtures reuse existing Python with deliberately conflicting identity metadata.",
            "Shared document identity does not establish shared store instance; same_instance remains necessary.",
            "HashingVectorizer is lexical; ThreadPoolExecutor is not an LLM harness.",
            "This exercises first-order associated type substitution, not generative types or generic .mfl.",
        ],
    }
    (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    report = run(args.work_dir, args.wheelhouse)
    print(json.dumps({"report": str(args.work_dir / "report.json"), "result": report["replay"]["result"], "rejections": report["rejections"]}, indent=2))


if __name__ == "__main__":
    main()
