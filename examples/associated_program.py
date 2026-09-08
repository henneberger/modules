"""Separately check a generic knowledge search program, then run SQLite FTS5."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.manifest import write_manifest
from module_families.registry import Registry
from module_families.synthesis import synthesize
from module_families.typed_program import TypeCheckError, build_program, check_program

SOURCE = Path(__file__).resolve().parent / "associated_program"


def run(work, wheelhouse=None):
    work = Path(work).resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    repository = Registry(work / "repository")
    build_family(SOURCE / "interfaces.toml", work / "interfaces")
    repository.publish(work / "interfaces/index.json")
    assert not repository.candidates("kb.generic.query", "1")
    assert not repository.candidates("kb.generic.index", "1")
    checked = check_program(SOURCE / "search.toml", repository)
    built = build_program(SOURCE / "search.toml", repository, work / "program")
    repository.publish(built["index"])

    # Sharing is an explicit work contract, never inferred from an unchecked call.
    missing = work / "missing-assumption"
    missing.mkdir()
    document = tomllib.loads((SOURCE / "search.toml").read_text())
    document["associated"].pop("sharing")
    write_manifest(document, missing / "search.toml")
    (missing / "search.mfl").write_text((SOURCE / "search.mfl").read_text())
    try:
        check_program(missing / "search.toml", repository)
    except TypeCheckError as error:
        missing_equality = str(error)
    else:
        raise AssertionError("generic call silently inferred a missing equality")

    build_family(SOURCE / "providers.toml", work / "providers")
    repository.publish(work / "providers/index.json")
    resolution = synthesize(
        {
            "schema_version": 1,
            "goal": {
                "name": "generic-knowledge-search",
                "requires": {"id": "kb.generic.search", "version": "1"},
                "capabilities": ["citation-search"],
            },
            "policy": {"allowed_effects": ["sqlite"]},
        },
        repository,
    )
    assert resolution["status"] == "unique", resolution
    assert resolution["rejections"], "contradictory query syntax was not rejected"
    assembly = lock_assembly(resolution, repository)
    (work / "assembly.lock.json").write_text(json.dumps(assembly, indent=2) + "\n")
    environment = lock_environment(
        assembly,
        repository,
        work / "environment",
        find_links=[str(Path(wheelhouse).resolve())] if wheelhouse else [],
        no_index=wheelhouse is not None,
    )
    sync_environment(environment["lock"], work / "venv")
    replay = run_environment(
        environment["lock"], work / "venv", export="search", args=["data retention"]
    )
    assert len(replay["result"]) == 1
    assert replay["result"][0]["document_id"] == "retention-2026"
    assert replay["result"][0]["page"] == 3
    report = {
        "separate_compilation_without_providers": True,
        "checked_sha256": checked["sha256"],
        "associated_assumptions": checked["associated_assumptions"],
        "missing_equality_rejected": missing_equality,
        "link_rejections": resolution["rejections"],
        "environment": environment,
        "replay": replay,
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
                "result": report["replay"]["result"],
                "missing_equality_rejected": report["missing_equality_rejected"],
                "report": str(args.work_dir / "report.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
