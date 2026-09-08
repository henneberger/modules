"""Check a resource program before providers exist, then synthesize and run it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from module_families.assemblies import lock_assembly
from module_families.compiler import build_family
from module_families.environments import (
    lock_environment,
    run_environment,
    sync_environment,
)
from module_families.registry import Registry
from module_families.synthesis import synthesize
from module_families.typed_program import TypeCheckError, build_program, check_program

ROOT = Path(__file__).resolve().parents[1]


def run(work, wheelhouse=None):
    work = Path(work).resolve()
    if work.exists() and any(work.iterdir()):
        raise ValueError("choose an empty work directory")
    work.mkdir(parents=True, exist_ok=True)
    repository = Registry(work / "repository")
    for name in ("interfaces", "workflows"):
        out = work / "build" / name
        build_family(ROOT / "families/transactions" / (name + ".toml"), out)
        repository.publish(out / "index.json")
    assert not repository.candidates("transactions.store", "1")
    authored = ROOT / "examples/typed/transaction.toml"
    checked = check_program(authored, repository)
    built = build_program(authored, repository, work / "build/program")
    repository.publish(Path(built["index"]))
    rejected = {}
    for name in ("rejected-use-after-move", "rejected-leak"):
        folder = work / name
        folder.mkdir()
        (folder / "transaction.mfl").write_text(
            (ROOT / "examples/typed" / (name + ".mfl")).read_text()
        )
        (folder / "transaction.toml").write_text(authored.read_text())
        try:
            check_program(folder / "transaction.toml", repository)
        except TypeCheckError as error:
            rejected[name] = str(error)
        else:
            raise AssertionError(f"invalid program unexpectedly checked: {name}")
    providers = work / "build/providers"
    build_family(ROOT / "families/transactions/providers.toml", providers)
    repository.publish(providers / "index.json")
    goal = {
        "schema_version": 1,
        "goal": {
            "name": "typed-transaction",
            "requires": {"id": "transactions.workflow", "version": "1"},
            "capabilities": ["transactional-key-value-workflow"],
        },
        "policy": {"allowed_effects": ["sqlite"]},
    }
    selected = synthesize(goal, repository)
    assert selected["status"] == "unique"
    assembly = lock_assembly(selected, repository)
    (work / "program.lock.json").write_text(json.dumps(assembly, indent=2) + "\n")
    environment = lock_environment(
        assembly,
        repository,
        work / "environment",
        find_links=[str(Path(wheelhouse).resolve())] if wheelhouse else [],
        no_index=wheelhouse is not None,
    )
    sync_environment(environment["lock"], work / "venv")
    replay = run_environment(
        environment["lock"],
        work / "venv",
        export="run",
        args=["subject", "module systems"],
    )
    assert replay["result"] == ["module systems", "committed"]
    report = {
        "separate_compilation_without_providers": True,
        "checked": {
            "sha256": checked["sha256"],
            "effects": checked["effects"],
            "guarantees": checked["guarantees"],
        },
        "build": built,
        "rejections": rejected,
        "program_sha256": assembly["sha256"],
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
    result = run(args.work_dir, args.wheelhouse)
    print(
        json.dumps(
            {
                "result": result["replay"]["result"],
                "rejections": result["rejections"],
                "report": str(args.work_dir / "report.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
