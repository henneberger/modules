"""Build, publish, synthesize, and execute a real module graph in a private venv."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.module_build import build_module
from module_families.registry import Registry
from module_families.synthesis import synthesize

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = {
    "dogs": "dogs are loyal pets and enjoy walks",
    "cats": "cats are independent pets and enjoy naps",
    "python": "python modules compose reusable software",
    "storage": "sqlite stores documents in a database",
}
QUERIES = ["dogs pets walks", "python reusable modules"]


def run(work: Path, wheelhouse: Path | None = None):
    work = work.resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    repository = Registry(work / "repository")
    original = ROOT / "projects/mari-kit/src/mark_kit/retrieval/fusion.py"
    source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    for name in ("interfaces", "mari", "adapters"):
        out = work / "build" / name
        build_family(ROOT / "families/knowledge" / (name + ".toml"), out)
        repository.publish(out / "index.json")
    builds = []
    for name in ("retrieval", "knowledge"):
        out = work / "build" / name
        builds.append(
            build_module(ROOT / "examples/modules" / (name + ".toml"), repository, out)
        )
        repository.publish(out / "index.json")
    resolution = synthesize(ROOT / "examples/goals/knowledge.toml", repository)
    assert resolution["status"] == "unique"
    assembly = lock_assembly(resolution, repository)
    (work / "program.lock.json").write_text(json.dumps(assembly, indent=2) + "\n")
    environment = lock_environment(
        assembly,
        repository,
        work / "environment",
        find_links=[str(wheelhouse.resolve()), str(ROOT / ".mf/upstream-wheels")]
        if wheelhouse
        else [],
        no_index=wheelhouse is not None,
    )
    sync_environment(environment["lock"], work / "venv")
    replay = run_environment(
        environment["lock"], work / "venv", export="run", args=[DOCUMENTS, QUERIES, 2]
    )
    assert [hits[0]["id"] for hits in replay["result"]] == ["dogs", "python"]
    assert all(len(hits) == 2 for hits in replay["result"])
    assert hashlib.sha256(original.read_bytes()).hexdigest() == source_hash
    report = {
        "builds": builds,
        "synthesis_status": resolution["status"],
        "program_sha256": assembly["sha256"],
        "mari_source_sha256": source_hash,
        "mari_source_unchanged": True,
        "environment": environment,
        "replay": replay,
        "limits": [
            "HashingVectorizer produces lexical feature vectors, not neural semantic embeddings",
            "The harness is ThreadPoolExecutor, not an LLM reasoning agent",
            "SQLite is an in-memory, explicitly closable candidate store",
            "The example wires providers; it does not implement a universal model API",
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
    print(
        json.dumps(
            {
                "report": str(args.work_dir / "report.json"),
                "result": report["replay"]["result"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
